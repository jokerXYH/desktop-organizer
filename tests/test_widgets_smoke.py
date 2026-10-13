# -*- coding: utf-8 -*-
"""GUI 冒烟测试：offscreen 平台下构造格子窗口，验证刷新、空状态、拖出数据与锁定标签。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QListView  # noqa: E402

from widgets import (FileWidget, MIN_WIDGET_H, MIN_WIDGET_W,  # noqa: E402
                     SCALE_MAX, SCALE_MIN, SearchWidget, _icon_stats,
                     _url_icon_file, get_file_icon, scale_geometry)

_APP = QApplication.instance() or QApplication(sys.argv)


def _make_widget(tmp_path, title="文档", **kwargs):
    """辅助：构造一个指向临时目录的格子。"""
    folder = tmp_path / title
    folder.mkdir(parents=True, exist_ok=True)
    return FileWidget(title, str(folder), on_drop=None, **kwargs)


def test_empty_folder_shows_guide(tmp_path):
    """空格子应显示"拖入"引导提示，且提示条目不可选中。"""
    widget = _make_widget(tmp_path)
    widget.refresh()

    assert widget.list_widget.count() == 1
    tip = widget.list_widget.item(0)
    assert "拖入" in tip.text()
    assert not (tip.flags() & Qt.ItemIsEnabled)
    widget.close()


def test_refresh_lists_files_with_paths(tmp_path):
    """刷新后条目文本为文件名，UserRole 存完整路径。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "笔记.txt").write_text("x", encoding="utf-8")
    widget.refresh()

    assert widget.list_widget.count() == 1
    item = widget.list_widget.item(0)
    assert item.text() == "笔记.txt"
    assert item.data(Qt.UserRole) == str(tmp_path / "文档" / "笔记.txt")
    widget.close()


def test_drag_out_mime_contains_file_urls(tmp_path):
    """拖出时 MIME 数据应包含选中文件的真实路径，可供资源管理器/其它格子接收。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "文档" / "b.txt").write_text("b", encoding="utf-8")
    widget.refresh()
    items = [widget.list_widget.item(i)
             for i in range(widget.list_widget.count())]

    mime = widget.list_widget.mimeData(items)

    urls = mime.urls()
    assert len(urls) == 2
    assert {os.path.basename(u.toLocalFile()) for u in urls} == {"a.txt", "b.txt"}
    assert widget.list_widget.dragEnabled()
    widget.close()


def test_preview_switch_and_lock_label(tmp_path):
    """预览切换应改变视图模式；锁定后数量标签带提示并回调持久化。"""
    changes = []
    widget = _make_widget(tmp_path, locked=True,
                          on_lock_changed=lambda w, locked: changes.append(locked))
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    widget.refresh()

    assert "已锁定" in widget.count_label.text()

    widget.set_preview(False)
    assert widget.list_widget.viewMode() == QListView.ListMode
    widget.set_preview(True)
    assert widget.list_widget.viewMode() == QListView.IconMode

    widget._set_locked(False)
    assert changes == [False]
    assert "已锁定" not in widget.count_label.text()
    widget.close()


def test_scale_geometry_base_and_clamp():
    """缩放几何：基准值正确，超范围输入钳制到档位边界。"""
    base = scale_geometry(1.0)
    assert base["width"] == 270 and base["height"] == 360
    assert base["icon"] == 38 and base["cell_w"] == 76 and base["cell_h"] == 84
    assert scale_geometry(10.0) == scale_geometry(SCALE_MAX)
    assert scale_geometry(0.1) == scale_geometry(SCALE_MIN)


def test_widget_metrics_zoom_steps(tmp_path):
    """Ctrl+滚轮缩放只调整图标/列宽档位，窗口尺寸保持不变。"""
    widget = _make_widget(tmp_path)
    base = scale_geometry(1.0)
    assert (widget.width(), widget.height()) == (270, 360)
    assert widget.list_widget.gridSize().width() == base["cell_w"]

    widget._zoom(1)
    bigger = scale_geometry(1.15)
    assert (widget.width(), widget.height()) == (270, 360)   # 窗口尺寸不受图标缩放影响
    assert widget.list_widget.gridSize().height() == bigger["cell_h"]

    widget._zoom(-1)                              # 缩小回 100%
    assert widget.list_widget.gridSize().height() == base["cell_h"]

    widget._reset_scale()
    assert widget.list_widget.gridSize().height() == base["cell_h"]
    widget.close()


def test_free_resize_and_persist_callback(tmp_path):
    """拖拽调整窗口宽高：钳制到最小尺寸，防抖回调上报（供持久化）。"""
    changes = []
    widget = _make_widget(
        tmp_path, on_size_changed=lambda w: changes.append((w.width(), w.height())))

    widget._apply_window_size(420, 300)
    widget._emit_size()
    assert changes[-1] == (420, 300)

    widget._apply_window_size(50, 50)             # 低于最小值：钳制
    assert (widget.width(), widget.height()) == (MIN_WIDGET_W, MIN_WIDGET_H)
    widget.close()


def test_resize_edge_hit_testing(tmp_path):
    """八方向拖拽热区判定（四边+四角）；锁定时不响应调整。"""
    from PySide6.QtCore import QPoint
    widget = _make_widget(tmp_path)
    w, h = widget.width(), widget.height()
    assert widget._edge_at(QPoint(w // 2, h // 2)) is None
    assert widget._edge_at(QPoint(2, h // 2)) == "left"
    assert widget._edge_at(QPoint(w - 2, h // 2)) == "right"
    assert widget._edge_at(QPoint(w // 2, 2)) == "top"
    assert widget._edge_at(QPoint(w // 2, h - 2)) == "bottom"
    assert widget._edge_at(QPoint(2, 2)) == "top-left"
    assert widget._edge_at(QPoint(w - 2, 2)) == "top-right"
    assert widget._edge_at(QPoint(2, h - 2)) == "bottom-left"
    assert widget._edge_at(QPoint(w - 2, h - 2)) == "bottom-right"
    widget._set_locked(True)
    assert widget._edge_at(QPoint(w - 2, h - 2)) is None
    widget.close()


def test_resize_geometry_all_edges(tmp_path):
    """八方向缩放几何：四边/四角、最小钳制、边缘吸附与指示线。"""
    from PySide6.QtCore import QPoint, QRect
    widget = _make_widget(tmp_path)
    start = (100, 100, 270, 360)

    geom, guides = widget._resize_geometry("right", start, QPoint(40, 0), [])
    assert geom == (100, 100, 310, 360) and guides == []

    geom, _ = widget._resize_geometry("left", start, QPoint(-40, 0), [])
    assert geom == (60, 100, 310, 360)            # 左移且变宽

    geom, _ = widget._resize_geometry("top", start, QPoint(0, -30), [])
    assert geom == (100, 70, 270, 390)            # 上移且变高

    geom, _ = widget._resize_geometry("bottom-right", start, QPoint(30, 20), [])
    assert geom == (100, 100, 300, 380)

    geom, _ = widget._resize_geometry("left", start, QPoint(500, 0), [])
    assert geom[2] == MIN_WIDGET_W                # 最小宽度钳制
    assert geom[0] == 100 + 270 - MIN_WIDGET_W

    peer = QRect(500, 50, 270, 360)
    geom, guides = widget._resize_geometry("right", start, QPoint(126, 0), [peer])
    assert geom[0] + geom[2] == 500               # 右缘吸附到 peer 左缘
    assert len(guides) == 1

    geom, guides = widget._resize_geometry("top", start, QPoint(0, -60), [peer])
    assert geom[1] == 50                          # 上缘吸附到 peer 上缘
    assert len(guides) == 1
    widget.close()


def test_search_widget_basic(tmp_path):
    """搜索格子：输入关键词后展示结果、空关键词显示提示。"""
    results = [("文档", str(tmp_path / "报告.docx"))]
    widget = SearchWidget(
        lambda kw: results if "bg" in kw.lower() else [],
        "搜索", str(tmp_path), None)

    widget.refresh()
    assert widget.list_widget.count() == 1        # 空关键词：提示条目
    assert "输入关键词" in widget.list_widget.item(0).text()

    widget.search_edit.setText("bg")
    widget._run_search()
    assert widget.list_widget.count() == 1
    assert widget.list_widget.item(0).data(Qt.UserRole).endswith("报告.docx")
    assert "1 项" in widget.count_label.text()
    widget.close()


def test_context_menu_items(tmp_path):
    """右键菜单包含五个分区的关键条目。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    widget.refresh()
    item = widget.list_widget.item(0)
    pos = widget.list_widget.visualItemRect(item).center()

    menu = widget._build_menu(pos)
    texts = [a.text() for a in menu.actions() if a.text()]

    assert "打开" in texts
    assert any("复制路径" in t for t in texts)
    assert any("剪切" in t for t in texts)
    assert any("删除到回收站" in t for t in texts)
    assert any("粘贴" in t for t in texts)
    assert "预览方式" in texts                    # 子菜单
    assert "尺寸调整" in texts                    # 子菜单
    assert "锁定位置" in texts
    assert any("重命名格子" in t for t in texts)
    assert any("新建格子" in t for t in texts)
    assert any("映射文件夹" in t for t in texts)
    assert any("删除此格子" in t for t in texts)
    assert "刷新" in texts

    # 内置分类：重命名不可用；自定义格子：重命名可用
    rename_action = next(a for a in menu.actions() if "重命名格子" in a.text())
    assert not rename_action.isEnabled()
    menu.deleteLater()

    custom = _make_widget(tmp_path, title="自定义格")
    (tmp_path / "自定义格" / "b.txt").write_text("b", encoding="utf-8")
    custom.refresh()
    menu2 = custom._build_menu(
        custom.list_widget.visualItemRect(custom.list_widget.item(0)).center())
    rename2 = next(a for a in menu2.actions() if "重命名格子" in a.text())
    assert rename2.isEnabled()
    menu2.deleteLater()

    custom.close()
    widget.close()


# ---------- 图标管线（白页修复） ----------

def test_icon_stats_detects_white_page():
    """白色页面图标应判为"低彩色"，彩色图标正常识别。"""
    from PySide6.QtGui import QColor, QIcon, QPixmap

    white = QPixmap(32, 32)
    white.fill(QColor(255, 255, 255))
    w_white, c_white = _icon_stats(QIcon(white))
    assert w_white > 0.9 and c_white < 0.02

    red = QPixmap(32, 32)
    red.fill(QColor(200, 30, 40))
    w_red, c_red = _icon_stats(QIcon(red))
    assert c_red > 0.9 and w_red < 0.1


def test_url_icon_file_parsing(tmp_path):
    """解析 .url 的 IconFile：存在返回路径，缺失返回 None。"""
    from PySide6.QtGui import QColor, QPixmap

    ico = tmp_path / "game.ico"
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(10, 200, 10))
    if not pixmap.save(str(ico), "ICO"):
        pytest.skip("当前环境不支持写出 ICO 文件")

    url = tmp_path / "游戏.url"
    url.write_text(
        "[InternetShortcut]\r\nURL=steam://rungameid/1\r\nIconFile=%s\r\n" % ico,
        encoding="utf-8")
    assert _url_icon_file(str(url)) == str(ico)

    missing = tmp_path / "缺失.url"
    missing.write_text("IconFile=%s\r\n" % (tmp_path / "none.ico"),
                       encoding="utf-8")
    assert _url_icon_file(str(missing)) is None


def test_get_file_icon_returns_nonnull(tmp_path):
    """普通文件应通过管线取到非空图标（含缓存路径）。"""
    sample = tmp_path / "sample.txt"
    sample.write_text("x", encoding="utf-8")
    icon = get_file_icon(str(sample))
    assert icon is not None and not icon.isNull()


# ---------- 拖动吸附与对齐指示线 ----------

def test_apply_snap_aligns_and_returns_guides(tmp_path):
    """吸附逻辑：左缘/上缘/右缘对齐并生成指示线；远离时不吸附。"""
    from PySide6.QtCore import QPoint, QRect

    widget = _make_widget(tmp_path)
    peer = QRect(200, 300, 270, 360)

    snapped, guides = widget._apply_snap(QPoint(205, 305), [peer])
    assert (snapped.x(), snapped.y()) == (200, 300)
    assert len(guides) == 2                      # 垂直 + 水平指示线

    far, no_guides = widget._apply_snap(QPoint(800, 800), [peer])
    assert (far.x(), far.y()) == (800, 800)
    assert no_guides == []

    wide_peer = QRect(200, 300, 300, 400)
    right_aligned, guides2 = widget._apply_snap(QPoint(234, 600), [wide_peer])
    assert right_aligned.x() == 230              # 右缘对齐（200+300-270）
    assert len(guides2) == 1
    widget.close()


def test_drag_pipeline_snaps_and_shows_guides(tmp_path):
    """完整拖动管线（合成事件）：窗口移动、吸附对齐、触发/隐藏对齐指示线。"""
    from PySide6.QtCore import QEvent, QPointF, QRect
    from PySide6.QtGui import QMouseEvent

    guides = []
    widget = _make_widget(
        tmp_path,
        guide_show=guides.append,
        guide_hide=lambda: guides.clear())
    widget.show()
    peer = QRect(500, 100, 270, 360)
    widget.snap_provider = lambda w: [peer]
    start = widget.pos()

    def send(kind, gx, gy, button, buttons):
        event = QMouseEvent(kind, QPointF(20, 20), QPointF(gx, gy),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(widget, event)

    send(QEvent.MouseButtonPress, start.x() + 20, start.y() + 20,
         Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, 520, 120, Qt.NoButton, Qt.LeftButton)

    assert (widget.x(), widget.y()) == (500, 100)   # 吸附到 peer 左缘/上缘
    assert len(guides) == 1 and len(guides[0]) == 2  # 垂直 + 水平指示线

    send(QEvent.MouseButtonRelease, 520, 120, Qt.LeftButton, Qt.NoButton)
    assert guides == []                              # 释放后隐藏
    widget.close()


# ---------- 紧凑列表：小图标 + 行距 ----------

def test_compact_mode_small_icons_and_roomier_rows(tmp_path):
    """紧凑列表：左侧 16px 小图标、行距更松弛。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    widget.set_preview(False)

    assert widget.list_widget.iconSize() == QSize(16, 16)
    assert "padding" in widget.list_widget.styleSheet()
    item = widget.list_widget.item(0)
    assert item is not None and not item.icon().isNull()
    widget.close()
