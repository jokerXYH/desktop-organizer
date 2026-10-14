# -*- coding: utf-8 -*-
"""回归测试：只读条目跨盘移动导致的"副本越积越多"与"文件收纳不进去"。

背景（真机复现）：桌面在 C:、收纳库在 D: 时，跨盘移动 = 复制 + 删源；
Windows 下源为只读时"删源"会 PermissionError，结果是目标已生成副本、
源却还在原地。旧实现只把该条目标记为"失败"，那份副本永远留在收纳库里，
用户每点一次"一键整理桌面"就再叠一份同名副本。本文件锁定修复后的行为。
"""
import os
import shutil

import pytest  # noqa: E402

import organizer as org  # noqa: E402

# Windows 只读属性：0o444 会让 os.access(path, os.W_OK) 返回 False
READONLY_MODE = 0o444


def _make_readonly(path):
    """把文件设为只读（模拟桌面上的只读安装包/只读 PDF）。"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x")
    os.chmod(path, READONLY_MODE)
    return path


def _fake_cross_drive_move(state, fail_times=1):
    """伪造 Windows 跨盘移动：先把内容复制到目标，再抛出"删源失败"。

    这正是真机上 read-only + C:→D: 的行为——目标副本已落盘，源还在。
    fail_times 表示前几次调用都这样失败（默认 1 次，之后放行给真实 move）。
    """
    real_move = shutil.move

    def fake_move(src, dst):
        if state["remaining"] > 0:
            state["remaining"] -= 1
            shutil.copy2(src, dst)          # 半成品副本先落盘
            raise PermissionError(13, "拒绝访问。: " + str(src))
        return real_move(src, dst)

    return fake_move


def test_move_entry_reclaims_readonly_source(tmp_path, monkeypatch):
    """只读源：清理半成品 → 解除只读重试 → 真正收纳，且副本保持原只读属性。"""
    src = _make_readonly(tmp_path / "ro.txt")
    dst = tmp_path / "dest" / "ro.txt"
    dst.parent.mkdir()
    state = {"remaining": 1}
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    assert org.move_entry(str(src), str(dst)) is True

    assert not src.exists()                   # 源被真正收纳走（不再是"还在桌面"）
    assert dst.exists() and dst.read_text(encoding="utf-8") == "x"
    assert not os.access(dst, os.W_OK)        # 收纳后仍保留只读：不改变用户原意
    assert not list(dst.parent.glob("ro (*).txt"))   # 没有留下 (2) 之类的半成品


def test_move_entry_leaves_no_partial_when_retry_fails(tmp_path, monkeypatch):
    """重试仍失败（被占用等）：目标不留任何半成品，源与属性保持不变。"""
    src = _make_readonly(tmp_path / "lock.txt")
    dst = tmp_path / "dest" / "lock.txt"
    dst.parent.mkdir()
    state = {"remaining": 99}                 # 每次都"复制成功、删源失败"
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    assert org.move_entry(str(src), str(dst)) is False

    assert src.exists() and not os.access(src, os.W_OK)   # 源属性已还原
    assert not dst.exists()                               # 关键：不留副本


def test_repeated_organize_does_not_accumulate_copies(tmp_path, monkeypatch):
    """反复整理同一条只读文件：收纳库不再逐次叠加副本（本次反馈的核心）。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _make_readonly(desktop / "安装包.exe")
    root = tmp_path / "storage"
    state = {"remaining": 99}
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    for _ in range(3):                        # 模拟用户连点三次"一键整理桌面"
        records, failed, _skipped = org.organize_desktop(
            str(desktop), str(root), return_failed=True)
        assert records == [] and failed == ["安装包.exe"]

    assert (desktop / "安装包.exe").exists()          # 文件仍在桌面
    assert os.listdir(root / "安装包") == []          # 且收纳库没有堆出副本


def test_organize_moves_readonly_file_into_storage(tmp_path, monkeypatch):
    """只读文件应被真正收纳（旧行为：留一份副本在收纳库、原件留在桌面）。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _make_readonly(desktop / "setup.exe")
    root = tmp_path / "storage"
    state = {"remaining": 1}
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    records, failed, _skipped = org.organize_desktop(
        str(desktop), str(root), return_failed=True)

    assert failed == []
    assert len(records) == 1
    assert not (desktop / "setup.exe").exists()
    moved = root / "安装包" / "setup.exe"
    assert moved.exists() and not os.access(moved, os.W_OK)


def test_disabled_category_stays_on_desktop(tmp_path):
    """分类没有对应格子：条目留在桌面并计入 skipped，不产生"看不见"的目录。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "影片.mp4").write_text("v", encoding="utf-8")
    (desktop / "报告.pdf").write_text("d", encoding="utf-8")
    root = tmp_path / "storage"

    records, failed, skipped = org.organize_desktop(
        str(desktop), str(root), return_failed=True,
        enabled_categories={"文档"})

    assert skipped == ["影片.mp4"]
    assert (desktop / "影片.mp4").exists()               # 留在桌面，不漏文件
    assert not (root / "视频").exists()                  # 没有格子就不建对应目录
    assert {os.path.basename(r.src) for r in records} == {"报告.pdf"}


def test_preview_respects_enabled_categories(tmp_path):
    """预览与实际整理共用同一限制：不会列出实际不会被移动的条目。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "影片.mp4").write_text("v", encoding="utf-8")
    (desktop / "报告.pdf").write_text("d", encoding="utf-8")

    preview = org.preview_organize(
        str(desktop), str(tmp_path / "storage"), enabled_categories={"文档"})

    assert preview == [("文档", "报告.pdf")]


def test_move_into_readonly_source_no_copy_left(tmp_path, monkeypatch):
    """拖入只读文件：同样不能留下"桌面原件 + 格子内副本"的组合。"""
    src = _make_readonly(tmp_path / "拖入.exe")
    folder = tmp_path / "storage" / "安装包"
    folder.mkdir(parents=True)
    state = {"remaining": 1}
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    dst = org.move_into(str(src), str(folder))

    assert not src.exists() and os.path.exists(dst)
    assert dst.endswith("拖入.exe")
    assert not os.access(dst, os.W_OK)        # 只读属性保留


def test_move_into_failure_raises_and_leaves_nothing(tmp_path, monkeypatch):
    """拖入彻底失败时抛异常，且目标目录不留半成品副本。"""
    src = tmp_path / "占用.txt"
    src.write_text("x", encoding="utf-8")
    folder = tmp_path / "storage" / "文档"
    folder.mkdir(parents=True)
    state = {"remaining": 99}
    monkeypatch.setattr(org.shutil, "move", _fake_cross_drive_move(state))

    with pytest.raises(OSError):
        org.move_into(str(src), str(folder))

    assert src.exists()
    assert os.listdir(folder) == []
