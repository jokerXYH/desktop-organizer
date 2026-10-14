# -*- coding: utf-8 -*-
"""回归测试：桌面文件夹与收纳库已有同名文件夹时，应"合并去重"而不是生成副本。

背景（真机反馈）：桌面的 jifang 与收纳库中的 jifang 同时存在，用户看到
"莫名其妙两份"。根因是旧逻辑在目标重名时用 unique_path 生成 "jifang (2)"，
而桌面那份又因只读等原因没被收走。本文件锁定合并去重后的行为：
  - 不生成 "名字 (2)"；
  - 内容完全相同的重复项不重复保留（桌面那份去重）；
  - 同名但内容不同则两份都留（绝不静默丢数据）；
  - 有条目搬不动时留下现场并上报，不留下难以察觉的半截状态。
"""
import os

import organizer as org  # noqa: E402

# 固定时间戳：让"最顶层文件"与"收纳库里的同名文件"被判为同一份内容
FIXED_MTIME = 1_700_000_000


def _write(path, text, mtime=FIXED_MTIME):
    """写文件并统一修改时间，便于构造"内容完全相同"的场景。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _fake_recycle(monkeypatch):
    """把"送回收站"替换为直接删除，返回是否成功——避免测试真的操作回收站。"""
    def _recycle(path):
        os.remove(path)
        return True
    monkeypatch.setattr(org, "move_to_recycle_bin", _recycle)


def test_merge_absorbs_perfect_duplicates(tmp_path, monkeypatch):
    """同名且内容一致：桌面那份去重，源目录消失，目标里不出现 "a (2).txt"。"""
    _fake_recycle(monkeypatch)
    src = tmp_path / "桌面" / "资料"
    dst = tmp_path / "收纳库" / "资料"
    _write(src / "a.txt", "same")
    _write(dst / "a.txt", "same")

    pairs, leftovers = org.merge_directory(str(src), str(dst))

    assert pairs == [] and leftovers == []
    assert not src.exists()                                  # 源目录已清空并删除
    assert (dst / "a.txt").read_text(encoding="utf-8") == "same"
    assert not (dst / "a (2).txt").exists()                   # 没有留下重复副本


def test_merge_keeps_both_for_different_content(tmp_path):
    """同名但内容不同：在目标里改名保留，两份都不丢。"""
    src = tmp_path / "桌面" / "资料"
    dst = tmp_path / "收纳库" / "资料"
    _write(src / "b.txt", "桌面上的新版本内容")
    _write(dst / "b.txt", "收纳库里的旧内容", mtime=FIXED_MTIME - 100000)

    pairs, leftovers = org.merge_directory(str(src), str(dst))

    assert leftovers == []
    assert len(pairs) == 1
    assert (dst / "b.txt").read_text(encoding="utf-8") == "收纳库里的旧内容"
    assert (dst / "b (2).txt").read_text(encoding="utf-8") == "桌面上的新版本内容"
    assert not src.exists()


def test_merge_recurses_into_nested_same_name_dirs(tmp_path, monkeypatch):
    """同名子目录递归合并：新内容搬进去，重复内容去重。"""
    _fake_recycle(monkeypatch)
    src = tmp_path / "桌面" / "资料"
    dst = tmp_path / "收纳库" / "资料"
    _write(src / "子" / "重复.txt", "same")
    _write(dst / "子" / "重复.txt", "same")
    _write(src / "子" / "新增.txt", "new")

    pairs, leftovers = org.merge_directory(str(src), str(dst))

    assert leftovers == []
    assert not (src / "子" / "重复.txt").exists()              # 重复项已去重
    assert (dst / "子" / "新增.txt").read_text(encoding="utf-8") == "new"
    assert any(p[1].endswith("新增.txt") for p in pairs)       # 新增项计入移动列表
    assert not src.exists()


def test_organize_merges_instead_of_creating_copy(tmp_path, monkeypatch):
    """一键整理：桌面文件夹与收纳库同名时合并，既收走桌面那份也不生成 (2)。"""
    _fake_recycle(monkeypatch)
    desktop = tmp_path / "桌面"
    storage = tmp_path / "收纳库"
    _write(desktop / "资料" / "a.txt", "same")
    _write(storage / "文件夹" / "资料" / "a.txt", "same")
    _write(desktop / "资料" / "仅桌面.txt", "new")

    records, failed, skipped = org.organize_desktop(
        str(desktop), str(storage), include_folders=True, return_failed=True)

    assert failed == [] and skipped == []
    assert not (desktop / "资料").exists()                     # 桌面那份已收走
    assert not (storage / "文件夹" / "资料 (2)").exists()       # 没有生成同名副本
    assert (storage / "文件夹" / "资料" / "a.txt").exists()
    assert (storage / "文件夹" / "资料" / "仅桌面.txt").exists()
    assert [r.dst for r in records] == [
        str(storage / "文件夹" / "资料" / "仅桌面.txt")]        # 仅新增项需要撤销


def test_merge_reports_leftover_when_item_cannot_move(tmp_path, monkeypatch):
    """有条目搬不动（被占用等）：留下现场并按失败上报，源目录不删除。"""
    monkeypatch.setattr(org, "move_entry", lambda src, dst: False)
    src = tmp_path / "桌面" / "资料"
    dst = tmp_path / "收纳库" / "资料"
    _write(src / "a.txt", "x")
    dst.mkdir(parents=True, exist_ok=True)

    pairs, leftovers = org.merge_directory(str(src), str(dst))

    assert pairs == []
    assert leftovers == [str(src / "a.txt")]                    # 明确上报未处理项
    assert (src / "a.txt").exists()                             # 现场保留，可重试
    assert not (dst / "a.txt").exists()


def test_merge_survives_unavailable_recycle_bin(tmp_path, monkeypatch):
    """回收站不可用时退化为改名保留：宁可有重复，也绝不静默丢数据。"""
    monkeypatch.setattr(org, "move_to_recycle_bin", lambda path: False)
    src = tmp_path / "桌面" / "资料"
    dst = tmp_path / "收纳库" / "资料"
    _write(src / "a.txt", "same")
    _write(dst / "a.txt", "same")

    pairs, leftovers = org.merge_directory(str(src), str(dst))

    assert leftovers == []
    assert (dst / "a.txt").exists() and (dst / "a (2).txt").exists()
    assert not src.exists()
