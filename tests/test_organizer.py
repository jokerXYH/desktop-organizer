# -*- coding: utf-8 -*-
"""organizer 模块单元测试：分类、整理、冲突处理、撤销恢复。"""
import os

import pytest

import organizer as org


# ---------- classify 分类 ----------

def test_classify_known_extensions():
    """常见扩展名应归入正确分类。"""
    assert org.classify("报告.pdf") == "文档"
    assert org.classify("photo.jpg") == "图片"
    assert org.classify("movie.mp4") == "视频"
    assert org.classify("song.mp3") == "音频"
    assert org.classify("data.zip") == "压缩包"
    assert org.classify("setup.exe") == "安装包"
    assert org.classify("main.py") == "代码"
    assert org.classify("启动.lnk") == "快捷方式"


def test_classify_case_insensitive():
    """大写扩展名应等价匹配。"""
    assert org.classify("A.PDF") == "文档"
    assert org.classify("IMG.JPG") == "图片"


def test_classify_unknown_and_no_extension():
    """未知扩展名与无扩展名都归入"其他"。"""
    assert org.classify("file.xyz") == "其他"
    assert org.classify("noext") == "其他"


# ---------- unique_path 冲突处理 ----------

def test_unique_path_no_conflict(tmp_path):
    """路径不存在时原样返回。"""
    target = tmp_path / "a.txt"
    assert org.unique_path(str(target)) == str(target)


def test_unique_path_conflict_increments(tmp_path):
    """冲突时依次生成 name (2).ext、name (3).ext。"""
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (2).txt").write_text("2")
    result = org.unique_path(str(tmp_path / "a.txt"))
    assert result == str(tmp_path / "a (3).txt")


# ---------- organize_desktop 一键整理 ----------

def _touch(path, content="x"):
    """辅助：创建文件及其父目录。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_organize_moves_files_by_category(tmp_path):
    """桌面文件应按分类移入收纳目录对应子文件夹。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "报告.pdf"))
    _touch(str(desktop / "图.png"))
    _touch(str(desktop / "unknown.xyz"))

    records = org.organize_desktop(str(desktop), str(storage))

    assert len(records) == 3
    assert os.path.exists(str(storage / "文档" / "报告.pdf"))
    assert os.path.exists(str(storage / "图片" / "图.png"))
    assert os.path.exists(str(storage / "其他" / "unknown.xyz"))
    assert not os.path.exists(str(desktop / "报告.pdf"))


# ---------- v2 文件夹支持 ----------

def test_organize_moves_folders_by_default(tmp_path):
    """默认应把桌面顶层文件夹整体移入"文件夹"分类。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "项目资料" / "内部文件.txt"))

    records = org.organize_desktop(str(desktop), str(storage))

    assert len(records) == 1
    assert os.path.isdir(str(storage / org.FOLDER_CATEGORY / "项目资料"))
    # 文件夹内的文件应随文件夹整体移动且保持结构
    assert os.path.isfile(str(storage / org.FOLDER_CATEGORY / "项目资料" / "内部文件.txt"))


def test_organize_keeps_folders_when_disabled(tmp_path):
    """include_folders=False 时文件夹应留在桌面。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    os.mkdir(str(desktop / "我的文件夹"))

    records = org.organize_desktop(str(desktop), str(storage),
                                   include_folders=False)

    assert records == []
    assert os.path.isdir(str(desktop / "我的文件夹"))


def test_organize_folder_collision_renames(tmp_path):
    """同名文件夹两次整理应自动加后缀而不是覆盖。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    os.mkdir(str(desktop / "资料"))
    org.organize_desktop(str(desktop), str(storage))
    os.mkdir(str(desktop / "资料"))

    org.organize_desktop(str(desktop), str(storage))

    assert os.path.isdir(str(storage / org.FOLDER_CATEGORY / "资料"))
    assert os.path.isdir(str(storage / org.FOLDER_CATEGORY / "资料 (2)"))


def test_organize_keeps_shortcuts_by_default(tmp_path):
    """默认不收走快捷方式，开关打开后才收。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "微信.lnk"))

    records = org.organize_desktop(str(desktop), str(storage))
    assert records == []
    assert os.path.exists(str(desktop / "微信.lnk"))

    records = org.organize_desktop(str(desktop), str(storage), include_shortcuts=True)
    assert len(records) == 1
    assert os.path.exists(str(storage / "快捷方式" / "微信.lnk"))


def test_organize_ignores_system_files(tmp_path):
    """desktop.ini 等系统文件与隐藏文件不应被移动。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "desktop.ini"))
    _touch(str(desktop / ".hidden"))

    records = org.organize_desktop(str(desktop), str(storage))
    assert records == []
    assert os.path.exists(str(desktop / "desktop.ini"))


def test_organize_collision_renames(tmp_path):
    """同名文件两次整理应自动加后缀而不是覆盖。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "a.pdf"), "first")
    org.organize_desktop(str(desktop), str(storage))
    _touch(str(desktop / "a.pdf"), "second")
    org.organize_desktop(str(desktop), str(storage))

    assert os.path.exists(str(storage / "文档" / "a.pdf"))
    assert os.path.exists(str(storage / "文档" / "a (2).pdf"))


def test_organize_records_share_batch(tmp_path):
    """同一次整理产生的记录应共享同一批次 ID。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _touch(str(desktop / "a.pdf"))
    _touch(str(desktop / "b.png"))
    records = org.organize_desktop(str(desktop), str(tmp_path / "storage"))
    assert len({r.batch for r in records}) == 1


def test_organize_invalid_desktop_raises(tmp_path):
    """桌面路径不存在时应抛出 RuntimeError。"""
    with pytest.raises(RuntimeError):
        org.organize_desktop(str(tmp_path / "not_exist"), str(tmp_path / "s"))


# ---------- undo_moves 撤销恢复 ----------

def test_undo_restores_files(tmp_path):
    """整理后撤销应把文件移回桌面原位置。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "报告.pdf"))
    records = org.organize_desktop(str(desktop), str(storage))

    restored = org.undo_moves(records)

    assert len(restored) == 1
    assert os.path.exists(str(desktop / "报告.pdf"))
    assert not os.path.exists(str(storage / "文档" / "报告.pdf"))


def test_undo_skips_missing_destination(tmp_path):
    """目标文件已被删除时撤销应跳过而不是报错。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "a.pdf"))
    records = org.organize_desktop(str(desktop), str(storage))
    os.remove(str(storage / "文档" / "a.pdf"))  # 模拟用户手动删除

    restored = org.undo_moves(records)
    assert restored == []


def test_undo_collision_renames(tmp_path):
    """原位置已有同名文件时恢复应加后缀保留双方。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "a.pdf"), "moved")
    records = org.organize_desktop(str(desktop), str(storage))
    _touch(str(desktop / "a.pdf"), "new")  # 桌面又出现了同名新文件

    org.undo_moves(records)

    with open(str(desktop / "a.pdf"), encoding="utf-8") as f:
        assert f.read() == "new"      # 原文件保留
    with open(str(desktop / "a (2).pdf"), encoding="utf-8") as f:
        assert f.read() == "moved"    # 恢复的文件改名


# ---------- move_into 拖拽收纳 ----------

def test_move_into(tmp_path):
    """move_into 应把文件移入指定文件夹并返回目标路径。"""
    src = tmp_path / "a.txt"
    src.write_text("data", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()

    dst = org.move_into(str(src), str(folder))

    assert os.path.exists(dst)
    assert not src.exists()


def test_move_into_missing_src_raises(tmp_path):
    """源文件不存在时应抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        org.move_into(str(tmp_path / "ghost.txt"), str(tmp_path))


def test_move_into_folder_directory(tmp_path):
    """move_into 应支持把整个文件夹移入格子文件夹。"""
    src = tmp_path / "相册"
    (src / "a.jpg").parent.mkdir(parents=True)
    (src / "a.jpg").write_text("img", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()

    dst = org.move_into(str(src), str(folder))

    assert os.path.isdir(dst)
    assert os.path.isfile(os.path.join(dst, "a.jpg"))
    assert not src.exists()


def test_move_into_self_guard(tmp_path):
    """把目标文件夹自身或其内部路径移入自己时应抛 ValueError。"""
    folder = tmp_path / "folder"
    folder.mkdir()
    inner = folder / "inner.txt"
    inner.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        org.move_into(str(folder), str(folder))
    with pytest.raises(ValueError):
        org.move_into(str(inner), str(folder))


# ---------- search_storage 收纳库搜索 ----------

def test_search_storage_finds_by_keyword(tmp_path):
    """应递归搜索各分类子目录并返回 (分类, 路径)。"""
    storage = tmp_path / "storage"
    _touch(str(storage / "文档" / "年度报告2026.pdf"))
    _touch(str(storage / "图片" / "报告配图.png"))
    _touch(str(storage / "文档" / "清单.txt"))

    results = org.search_storage(str(storage), "报告")

    assert len(results) == 2
    assert all(cat in ("文档", "图片") for cat, _path in results)
    assert any("年度报告2026.pdf" in path for _cat, path in results)


def test_search_storage_case_insensitive_and_limit(tmp_path):
    """搜索应大小写不敏感，且受 limit 上限约束。"""
    storage = tmp_path / "storage"
    _touch(str(storage / "文档" / "README.md"))

    assert org.search_storage(str(storage), "readme") != []
    assert org.search_storage(str(storage), "readme", limit=0) == []


def test_search_storage_empty_keyword_and_missing_root(tmp_path):
    """空关键词与不存在的根目录都应返回空结果且不报错。"""
    assert org.search_storage(str(tmp_path / "storage"), "x") == []
    assert org.search_storage(str(tmp_path), "") == []


def test_search_storage_extra_dirs(tmp_path):
    """extra_dirs（路径映射的格子）应一并被搜索到。"""
    storage = tmp_path / "storage"
    _touch(str(storage / "文档" / "a.txt"))
    external = tmp_path / "外部资料"
    _touch(str(external / "报告.pdf"))

    results = org.search_storage(
        str(storage), "报告", extra_dirs=[("外部资料", str(external))])

    assert len(results) == 1
    assert results[0][0] == "外部资料"
    assert "报告.pdf" in results[0][1]


def test_search_storage_pinyin(tmp_path):
    """拼音搜索：全拼与首字母都能命中中文文件名（需 pypinyin）。"""
    pytest.importorskip("pypinyin")
    storage = tmp_path / "storage"
    _touch(str(storage / "文档" / "年度报告2026.pdf"))
    _touch(str(storage / "文档" / "无关文件.txt"))

    assert [p for _c, p in org.search_storage(str(storage), "baogao")]
    assert [p for _c, p in org.search_storage(str(storage), "ndbg")]
    assert org.search_storage(str(storage), "zzzz") == []


def test_organize_only_names_filter(tmp_path):
    """only_names 只整理指定名称的条目（自动整理新文件用）。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "新文件.pdf"))
    _touch(str(desktop / "旧文件.pdf"))

    records = org.organize_desktop(
        str(desktop), str(storage), only_names={"新文件.pdf"})

    assert len(records) == 1
    assert os.path.exists(str(storage / "文档" / "新文件.pdf"))
    assert (desktop / "旧文件.pdf").exists()      # 未在集合内的不动


# ---------- preview_organize 整理前预览 ----------

def test_preview_organize_does_not_move(tmp_path):
    """预览应返回 (分类, 名称) 列表，但不移动任何文件、不创建收纳目录。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "报告.pdf"))
    _touch(str(desktop / "照片.jpg"))
    (desktop / "相册").mkdir()
    _touch(str(desktop / "相册" / "内.jpg"))

    preview = org.preview_organize(str(desktop), str(storage))

    assert sorted(preview) == sorted([
        ("文档", "报告.pdf"), ("图片", "照片.jpg"), ("文件夹", "相册")])
    assert (desktop / "报告.pdf").exists()      # 文件没被动
    assert not storage.exists()                 # 没有创建任何收纳目录


def test_preview_organize_respects_options(tmp_path):
    """预览同样遵守"不收快捷方式/不收文件夹"开关。"""
    desktop = tmp_path / "desktop"
    storage = tmp_path / "storage"
    desktop.mkdir()
    _touch(str(desktop / "启动.lnk"))
    (desktop / "相册").mkdir()

    preview = org.preview_organize(
        str(desktop), str(storage),
        include_shortcuts=False, include_folders=False)

    assert preview == []


# ---------- move_to_recycle_bin 回收站删除 ----------

@pytest.mark.skipif(os.name != "nt", reason="回收站删除仅支持 Windows")
def test_move_to_recycle_bin(tmp_path):
    """应把文件删除到回收站：文件从原位置消失且返回 True。"""
    target = tmp_path / "待删除的临时文件.txt"
    target.write_text("temp", encoding="utf-8")

    assert org.move_to_recycle_bin(str(target)) is True
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="回收站删除仅支持 Windows")
def test_move_to_recycle_bin_missing_file(tmp_path):
    """删除不存在的路径应返回 False 而不是崩溃或弹窗。"""
    assert org.move_to_recycle_bin(str(tmp_path / "ghost.txt")) is False
