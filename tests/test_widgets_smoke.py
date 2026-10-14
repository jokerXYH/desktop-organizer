# -*- coding: utf-8 -*-
"""GUI 冒烟测试：offscreen 平台下构造格子窗口，验证刷新、空状态、拖出数据与预览切换。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QListView  # noqa: E402

from widgets import (COMPACT_ICON_PX, FileWidget, MIN_WIDGET_H,  # noqa: E402
                     MIN_WIDGET_W, SCALE_MAX, SCALE_MIN, SearchWidget,
                     _icon_stats, _url_icon_file, decode_thumbnail,
                     get_file_icon, get_thumbnail_service, is_thumbnailable,
                     scale_geometry)

_APP = QApplication.instance() or QApplication(sys.argv)


def _make_widget(tmp_path, title="文档", **kwargs):
    """辅助：构造一个指向临时目录的格子。"""
    folder = tmp_path / title
    folder.mkdir(parents=True, exist_ok=True)
    return FileWidget(title, str(folder), on_drop=None, **kwargs)


def test_empty_folder_shows_guide(tmp_path):
    """空格子应显示"拖入"主提示与右键操作提示，且提示条目不可选中。"""
    widget = _make_widget(tmp_path)
    widget.refresh()

    assert widget.list_widget.count() == 2
    tip = widget.list_widget.item(0)
    hint = widget.list_widget.item(1)
    assert "拖入" in tip.text()
    assert "右键" in hint.text()
    assert not (tip.flags() & Qt.ItemIsEnabled)
    assert not (hint.flags() & Qt.ItemIsEnabled)
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


def test_preview_switch_keeps_count_label(tmp_path):
    """预览切换应改变视图模式，数量标签保持纯计数（不再有锁定后缀）。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    widget.refresh()

    assert widget.count_label.text() == "1 项"

    widget.set_preview(False)
    assert widget.list_widget.viewMode() == QListView.ListMode
    widget.set_preview(True)
    assert widget.list_widget.viewMode() == QListView.IconMode
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

    widget._zoom(-1)                              # 85%
    widget._zoom(-1)                              # 落到最低档位 75%
    assert widget.scale == SCALE_MIN
    widget._zoom(-1)                              # 已到底：再缩小保持不变
    assert widget.scale == SCALE_MIN
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
    """八方向拖拽热区判定（四边+四角），格子内部区域不触发调整。"""
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


def test_resize_snap_keeps_opposite_edge(tmp_path):
    """吸附只改"被拖的那条边"：拖下缘 top 不动、拖右缘 left 不动。

    回归点：旧实现把下缘吸附写成 top = edge_y - height、右缘写成
    left = edge_x - width，于是用户拖下边时上边也会跟着动，观感很反常。
    """
    from PySide6.QtCore import QPoint, QRect
    widget = _make_widget(tmp_path)
    start = (100, 100, 270, 360)

    # 拖下缘：bottom 靠近 peer 上缘(300) → 只改 height，top 必须保持 100
    peer_below = QRect(600, 300, 270, 360)
    geom, guides = widget._resize_geometry(
        "bottom", start, QPoint(0, -156), [peer_below])
    assert geom[1] == 100                 # 对边（上缘）没有被带动
    assert geom[3] == 200                 # 下缘吸附到 300 → 300 - 100
    assert len(guides) == 1

    # 拖右缘：right 靠近 peer 左缘(400) → 只改 width，left 必须保持 100
    peer_right = QRect(400, 600, 270, 360)
    geom, guides = widget._resize_geometry(
        "right", start, QPoint(34, 0), [peer_right])
    assert geom[0] == 100                 # 对边（左缘）没有被带动
    assert geom[2] == 300                 # 右缘吸附到 400 → 400 - 100
    assert len(guides) == 1
    widget.close()


def test_search_widget_basic(tmp_path):
    """搜索条：平时只有搜索框的高度；输入关键词后向下展开显示结果。"""
    results = [("文档", str(tmp_path / "报告.docx"))]
    widget = SearchWidget(
        lambda kw: results if "bg" in kw.lower() else [],
        "搜索", str(tmp_path), None)

    widget.refresh()
    # 空关键词：不显示任何结果条目，结果区收起 → 窗口只剩"一个搜索框"的高度
    assert widget.list_widget.count() == 0
    assert widget.list_widget.isHidden()
    bar_height = widget.height()

    widget.search_edit.setText("bg")
    widget._run_search()
    assert widget.list_widget.count() == 1
    assert widget.list_widget.item(0).data(Qt.UserRole).endswith("报告.docx")
    assert "1 项" in widget.count_label.text()
    assert widget.height() > bar_height           # 有结果：窗口向下展开
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
    assert any("重命名（F2）" in t for t in texts)   # 条目重命名入口
    assert any("删除到回收站" in t for t in texts)
    assert any("粘贴" in t for t in texts)
    assert "预览方式" in texts                    # 子菜单
    assert "尺寸调整" not in texts                # 已移除：尺寸请用拖拽边缘/Ctrl+滚轮
    assert "锁定位置" not in texts                # 已移除：不再提供位置锁定
    assert any("重命名格子" in t for t in texts)
    assert any("新建格子" in t for t in texts)
    assert any("映射文件夹" in t for t in texts)
    assert any("删除此格子" in t for t in texts)
    assert "刷新" in texts

    # 内置分类改名只改显示名，同样可用（搜索条才禁止重命名）
    rename_action = next(a for a in menu.actions() if "重命名格子" in a.text())
    assert rename_action.isEnabled()
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


# ---------- 图片缩略图 ----------

def test_is_thumbnailable_filters(tmp_path):
    """缩略图准入：图片扩展名通过，文本/缺失文件不通过。"""
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x" * 10)
    assert is_thumbnailable(str(img))

    txt = tmp_path / "a.txt"
    txt.write_text("x", encoding="utf-8")
    assert not is_thumbnailable(str(txt))
    assert not is_thumbnailable(str(tmp_path / "missing.png"))


def test_decode_thumbnail_bounds_size(tmp_path):
    """解码缩略图：结果非空，且长边不超过请求尺寸。"""
    from PySide6.QtGui import QColor, QImage

    src = tmp_path / "big.png"
    big = QImage(200, 100, QImage.Format_RGB32)
    big.fill(QColor(120, 40, 200))
    assert big.save(str(src), "PNG")

    thumb = decode_thumbnail(str(src), 40)
    assert not thumb.isNull()
    assert max(thumb.width(), thumb.height()) <= 40


def test_set_item_icon_prefers_cached_thumbnail(tmp_path):
    """命中缩略图缓存时，图片条目直接用缩略图，而不是系统图标。"""
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QListWidgetItem

    widget = _make_widget(tmp_path)
    img_path = tmp_path / "文档" / "photo.png"
    thumb = QImage(30, 12, QImage.Format_ARGB32)
    thumb.fill(QColor(20, 140, 220))
    assert thumb.save(str(img_path), "PNG")

    # 预置缓存：绕开后台线程时序，稳定验证"缓存命中即用缩略图"的分支
    size = widget._icon_px
    key = (str(img_path), os.path.getmtime(str(img_path)), size)
    get_thumbnail_service()._cache[key] = thumb

    item = QListWidgetItem("photo.png")
    widget._set_item_icon(item, str(img_path))

    pix = item.icon().pixmap(QSize(64, 64))
    assert pix.size() == QSize(30, 12)     # 尺寸与预置缩略图一致：证明未走系统图标
    widget.close()


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
    """紧凑列表：左侧小缩略图（24px）、行距更松弛。"""
    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    widget.set_preview(False)

    assert widget.list_widget.iconSize() == QSize(COMPACT_ICON_PX, COMPACT_ICON_PX)
    assert "padding" in widget.list_widget.styleSheet()
    item = widget.list_widget.item(0)
    assert item is not None and not item.icon().isNull()
    widget.close()


# ---------- Delete 键删除 / 拖出即移走 ----------

def test_delete_key_sends_selected_paths(tmp_path):
    """Delete 键：把选中条目的真实路径交给删除回调（外层再执行删除到回收站）。"""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    widget = _make_widget(tmp_path)
    (tmp_path / "文档" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "文档" / "b.txt").write_text("b", encoding="utf-8")
    widget.refresh()

    received = []
    widget.list_widget.delete_cb = received.append
    widget.list_widget.setCurrentItem(widget.list_widget.item(0))
    QApplication.sendEvent(
        widget.list_widget,
        QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))

    assert received == [[str(tmp_path / "文档" / "a.txt")]]
    widget.close()


def test_delete_key_ignored_without_selection(tmp_path):
    """空状态（只有提示条目）时按 Delete：没有真实路径，不触发删除回调。"""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    widget = _make_widget(tmp_path)
    widget.refresh()                                  # 只有"拖入"与"右键"两条提示

    received = []
    widget.list_widget.delete_cb = received.append
    widget.list_widget.setCurrentItem(widget.list_widget.item(0))
    QApplication.sendEvent(
        widget.list_widget,
        QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))

    assert received == []
    widget.close()


def test_delete_callback_wired_to_delete_to_recycle(tmp_path):
    """格子默认把 Delete 键接到与右键"删除到回收站"同一个入口。"""
    widget = _make_widget(tmp_path)
    assert widget.list_widget.delete_cb == widget._delete_paths
    widget.close()


def test_drag_out_declares_move_only():
    """拖出只声明"移动"，跨盘（D: 收纳库 -> C: 桌面）才会真正移走源文件。

    若沿用系统默认的"复制|移动"，资源管理器对跨盘拖放按复制处理，
    源文件会留在收纳库里，格子上看起来"拖出来了却没少"。
    """
    from widgets import DRAG_ACTION
    assert DRAG_ACTION == Qt.MoveAction
