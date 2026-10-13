# -*- coding: utf-8 -*-
"""AppContext 集成冒烟测试：重建格子、拖入/拖回、游戏模式守卫、隐藏状态与位置越界回退。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config as cfg  # noqa: E402
import organizer as org  # noqa: E402
from main import AppContext  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """构造指向临时收纳库的 AppContext（完全不触碰真实配置与历史）。"""
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "history.json"))
    cfg.save_config(dict(cfg.DEFAULT_CONFIG,
                         storageRoot=str(tmp_path / "收纳库")))
    context = AppContext()
    context.rebuild_widgets()
    yield context
    for widget in context.widgets:
        widget.close()
    context.panel.close()


def test_rebuild_creates_category_widgets(ctx):
    """重建后每个分类对应一个格子，收纳子目录自动创建。"""
    expected = len(ctx.config["categories"]) + \
        (1 if ctx.config.get("searchGrid", False) else 0)
    assert len(ctx.widgets) == expected
    for name in ctx.config["categories"]:
        assert os.path.isdir(os.path.join(ctx.config["storageRoot"], name))


def test_handle_drop_moves_file_and_records_history(ctx):
    """拖入文件应移入目标格子文件夹并写入历史；拖回原格子被静默忽略。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    src = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "拖入测试.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("x")

    ctx.handle_drop([src], doc)

    moved = os.path.join(ctx.config["storageRoot"], "文档", "拖入测试.txt")
    assert os.path.exists(moved)
    assert len(cfg.load_history()) == 1

    ctx.handle_drop([moved], doc)          # 拖回自身：静默忽略，不计失败
    assert len(cfg.load_history()) == 1


def test_game_mode_stops_watcher_and_pending_refresh(ctx):
    """游戏模式期间：监听全部移除，文件事件不再触发防抖刷新。"""
    assert ctx.watcher.directories()       # 正常模式下有监听

    ctx.set_game_mode(True, notify=False)

    assert ctx.watcher.directories() == []
    ctx._on_storage_changed("ignored")
    assert not ctx._refresh_timer.isActive()

    ctx.set_game_mode(False)
    assert ctx.watcher.directories()       # 退出后监听恢复


def test_widgets_hidden_state_survives_rebuild(ctx):
    """F7 隐藏状态在重建格子（改设置）后仍然保持。"""
    ctx.toggle_widgets()                   # 隐藏
    assert not any(w.isVisible() for w in ctx.widgets)

    ctx.rebuild_widgets()

    assert not any(w.isVisible() for w in ctx.widgets)
    ctx.toggle_widgets()
    assert any(w.isVisible() for w in ctx.widgets)


def test_out_of_screen_saved_position_falls_back(ctx):
    """记忆位置越界（换屏/改分辨率）时应回退默认布局并写回配置。"""
    positions = ctx.config.setdefault("widgetPositions", {})
    positions["文档"] = "99999,99999"
    ctx.rebuild_widgets()

    doc = next(w for w in ctx.widgets if w.title == "文档")
    assert doc.x() < 10000
    assert cfg.load_config()["widgetPositions"].get("文档") != "99999,99999"


def test_grid_create_rename_remove(ctx):
    """自定义格子全流程：新建（含文件夹）→ 重命名（含历史改写）→ 删除（文件夹保留）。"""
    root = ctx.config["storageRoot"]
    ctx.create_grid("资料夹")
    assert "资料夹" in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "资料夹"))
    assert any(w.title == "资料夹" for w in ctx.widgets)

    cfg.append_records([{
        "src": "D:/x.pdf",
        "dst": os.path.join(root, "资料夹", "x.pdf"),
        "batch": "b1",
    }])
    error = ctx.rename_grid("资料夹", "学习资料")
    assert error == ""
    assert "学习资料" in ctx.config["categories"]
    assert "资料夹" not in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "学习资料"))
    assert not os.path.isdir(os.path.join(root, "资料夹"))
    assert any(w.title == "学习资料" for w in ctx.widgets)
    assert cfg.load_history()[0]["dst"] == \
        os.path.join(root, "学习资料", "x.pdf")          # 历史已改写，仍可撤销

    ctx.remove_grid("学习资料")
    assert "学习资料" not in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "学习资料"))  # 文件夹与文件保留
    assert not any(w.title == "学习资料" for w in ctx.widgets)


def test_rename_grid_rejects_bad_name(ctx):
    """重命名校验：重名或非法字符应返回错误提示而不是抛异常。"""
    ctx.create_grid("资料夹")
    assert "已存在" in ctx.rename_grid("资料夹", "文档")     # 与内置分类重名
    assert "不能包含" in ctx.rename_grid("资料夹", "a:b")
    assert "资料夹" in ctx.config["categories"]             # 失败后配置不变


def test_panel_geometry_persist(ctx):
    """面板几何记忆：保存后写入配置，可被 parse 还原。"""
    ctx.panel.move(100, 120)
    ctx.panel.resize(520, 680)
    ctx.panel.save_geometry()

    saved = cfg.parse_geometry(cfg.load_config()["panelGeometry"])
    assert saved == (100, 120, 520, 680)


def test_grid_zoom_and_size_persist(ctx):
    """图标缩放与窗口尺寸各自持久化，重建格子后都能恢复。"""
    from widgets import scale_geometry

    doc = next(w for w in ctx.widgets if w.title == "文档")
    doc._zoom(1)                                  # 图标 100% -> 115%（窗口尺寸不变）
    assert doc.width() == 270
    assert ctx.config["widgetScales"]["文档"] == 1.15

    doc._apply_window_size(420, 300)              # 拖拽调整窗口尺寸
    doc._emit_size()
    assert ctx.config["widgetSizes"]["文档"] == "420,300"

    ctx.rebuild_widgets()                         # 模拟重启后恢复
    doc2 = next(w for w in ctx.widgets if w.title == "文档")
    assert (doc2.width(), doc2.height()) == (420, 300)
    assert doc2.scale == 1.15
    assert doc2.list_widget.gridSize().height() == scale_geometry(1.15)["cell_h"]

    doc2._reset_scale()
    doc2._reset_window_size()
    assert doc2.scale == 1.0
    assert (doc2.width(), doc2.height()) == (270, 360)
    assert ctx.config["widgetScales"]["文档"] == 1.0
    assert ctx.config["widgetSizes"]["文档"] == "270,360"


def test_preview_per_grid(ctx):
    """预览方式按单格子保存：切换一个格子不影响其它格子。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    pics = next(w for w in ctx.widgets if w.title == "图片")

    doc._switch_preview(False)

    assert doc.preview_icons is False
    assert ctx.config["widgetPreviews"]["文档"] is False
    assert pics.preview_icons is True                    # 其它格子不受影响

    ctx.rebuild_widgets()
    doc2 = next(w for w in ctx.widgets if w.title == "文档")
    pics2 = next(w for w in ctx.widgets if w.title == "图片")
    assert doc2.preview_icons is False
    assert pics2.preview_icons is True


def test_map_folder_grid(ctx, tmp_path):
    """路径映射：格子指向外部文件夹；重命名只改名称；删除不动外部文件。"""
    external = tmp_path / "外部资料"
    external.mkdir()
    (external / "a.txt").write_text("a", encoding="utf-8")

    ctx.create_grid("外部资料", folder=str(external))

    assert ctx.config["gridPaths"]["外部资料"] == str(external)
    widget = next(w for w in ctx.widgets if w.title == "外部资料")
    assert widget.folder == str(external)
    assert str(external) in ctx.watcher.directories()    # 外部文件夹纳入监听

    error = ctx.rename_grid("外部资料", "资料映射")
    assert error == ""
    assert external.is_dir()                             # 映射的外部文件夹不改名
    assert ctx.config["gridPaths"]["资料映射"] == str(external)

    ctx.remove_grid("资料映射")
    assert "资料映射" not in ctx.config["categories"]
    assert "资料映射" not in ctx.config["gridPaths"]
    assert external.is_dir() and (external / "a.txt").exists()


def test_auto_organize_new_files_only(ctx, tmp_path, monkeypatch):
    """自动整理：只收纳开启之后新出现的桌面文件，旧文件不动。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    (desktop / "旧文件.pdf").write_text("old", encoding="utf-8")
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))

    ctx.set_auto_organize(True)                  # 基线=当前桌面（旧文件不整理）
    (desktop / "新文件.pdf").write_text("new", encoding="utf-8")

    ctx._run_auto_organize()

    root = ctx.config["storageRoot"]
    assert os.path.exists(os.path.join(root, "文档", "新文件.pdf"))
    assert (desktop / "旧文件.pdf").exists()      # 旧文件不被触碰
    assert len(cfg.load_history()) == 1

    ctx.set_auto_organize(False)
    assert ctx._auto_watcher.directories() == []


def test_search_grid(ctx):
    """搜索格子：默认存在、支持拼音搜索、删除后消失、可在面板恢复。"""
    search = next(w for w in ctx.widgets if w.title == "搜索")
    root = ctx.config["storageRoot"]
    with open(os.path.join(root, "文档", "季度报告.docx"), "w",
              encoding="utf-8") as handle:
        handle.write("x")

    search.search_edit.setText("jidu")             # 拼音（季度）
    search._run_search()
    assert search.list_widget.count() == 1
    assert search.list_widget.item(0).data(Qt.UserRole).endswith("季度报告.docx")
    assert "1 项" in search.count_label.text()

    ctx.set_search_grid(False)
    assert not any(w.title == "搜索" for w in ctx.widgets)
    assert ctx.config["searchGrid"] is False

    ctx.set_search_grid(True)
    assert any(w.title == "搜索" for w in ctx.widgets)


def test_cut_paste_between_grids(ctx):
    """右键剪切 → 目标格子粘贴：文件移动、写入历史、剪切板清空。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    pics = next(w for w in ctx.widgets if w.title == "图片")
    src = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "cut_me.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("x")

    ctx.handle_cut_files([src], doc)
    assert ctx.clipboard_count() == 1
    assert os.path.exists(src)                    # 剪切不立即移动

    ctx.handle_paste_files(pics)
    assert not os.path.exists(src)
    moved = os.path.join(ctx.config["storageRoot"], "图片", "cut_me.txt")
    assert os.path.exists(moved)
    assert ctx.clipboard_count() == 0
    assert len(cfg.load_history()) == 1
