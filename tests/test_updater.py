# -*- coding: utf-8 -*-
"""updater 模块单元测试：版本比较 / 清单校验 / 自替换 / 下载（file:// 离线）。"""
import hashlib
import os

import pytest

import updater


def test_normalize_version_parses_forms():
    """版本号归一：容忍 v 前缀 / 两段式 / 后缀；非法返回空元组。"""
    assert updater.normalize_version("2.9.3") == (2, 9, 3)
    assert updater.normalize_version("v2.9.3") == (2, 9, 3)
    assert updater.normalize_version("2.9") == (2, 9, 0)
    assert updater.normalize_version("2.9.3-beta") == (2, 9, 3)
    assert updater.normalize_version("abc") == ()
    assert updater.normalize_version("") == ()


def test_is_newer_compares_versions():
    """大小比较（含跨段进位）；任一非法视为不更新。"""
    assert updater.is_newer("2.9.3", "2.9.2")
    assert updater.is_newer("2.10.0", "2.9.9")
    assert updater.is_newer("3.0.0", "2.99.99")
    assert not updater.is_newer("2.9.2", "2.9.2")
    assert not updater.is_newer("2.9.1", "2.9.2")
    assert not updater.is_newer("bad", "2.9.2")


def test_validate_manifest_accepts_full_and_minimal():
    """清单校验：全字段与最小字段都能通过；sha256 归一小写、page 缺省回退 url。"""
    full = updater.validate_manifest({
        "version": "2.10.0", "url": "https://x/a.exe",
        "sha256": "ABC", "notes": "说明", "page": "https://x/page"})
    assert full["version"] == "2.10.0"
    assert full["url"] == "https://x/a.exe"
    assert full["sha256"] == "abc"
    assert full["notes"] == "说明"
    assert full["page"] == "https://x/page"

    minimal = updater.validate_manifest({"version": "2.10.0", "url": "u"})
    assert minimal["sha256"] == "" and minimal["notes"] == ""
    assert minimal["page"] == "u"


def test_validate_manifest_rejects_invalid():
    """缺 version / 缺 url / 非对象 / 非法版本号：一律 ValueError。"""
    with pytest.raises(ValueError):
        updater.validate_manifest({"url": "u"})
    with pytest.raises(ValueError):
        updater.validate_manifest({"version": "2.10.0"})
    with pytest.raises(ValueError):
        updater.validate_manifest("not a dict")
    with pytest.raises(ValueError):
        updater.validate_manifest({"version": "abc", "url": "u"})


def test_sha256_file(tmp_path):
    """文件摘要与 hashlib 一致（下载完整性校验的基准）。"""
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    assert updater.sha256_file(str(path)) == \
        hashlib.sha256(b"hello").hexdigest()


def test_apply_update_swaps_and_backs_up(tmp_path):
    """自替换：旧文件改名 .old、新文件落到原名（普通文件即可仿真）。"""
    current = tmp_path / "app.exe"
    new = tmp_path / "download" / "app_new.exe"
    new.parent.mkdir()
    current.write_bytes(b"OLD")
    new.write_bytes(b"NEW")

    result = updater.apply_update(str(current), str(new))

    assert result == str(current)
    assert current.read_bytes() == b"NEW"
    assert (tmp_path / "app.exe.old").read_bytes() == b"OLD"


def test_apply_update_restores_on_failure(tmp_path):
    """复制失败（新文件缺失）时把 .old 改回原名，绝不把程序弄丢。"""
    current = tmp_path / "app.exe"
    current.write_bytes(b"OLD")

    with pytest.raises(OSError):
        updater.apply_update(str(current), str(tmp_path / "missing.exe"))

    assert current.read_bytes() == b"OLD"
    assert not (tmp_path / "app.exe.old").exists()


@pytest.mark.skipif(os.name != "nt", reason="进程等待仅 Windows 可用")
def test_wait_for_process_exit():
    """当前进程短超时内不会退出（False）；不存在的 PID 视为已退出（True）。"""
    assert updater.wait_for_process_exit(os.getpid(), timeout_s=0.1) is False
    assert updater.wait_for_process_exit(99999999, timeout_s=0.1) is True


def test_download_file_url_with_progress(tmp_path):
    """file:// 离线下载：内容一致，进度回调最终到达 100。"""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dst = tmp_path / "dst.bin"

    percents = []
    updater.download(url=src.as_uri(), dest=str(dst),
                     progress_cb=percents.append)

    assert dst.read_bytes() == b"x" * 4096
    assert percents and percents[-1] == 100
