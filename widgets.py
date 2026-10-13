# -*- coding: utf-8 -*-
"""桌面格子窗口：无边框、半透明、置底，支持文件/文件夹拖入与窗口拖动。

每个格子对应收纳目录下的一个分类文件夹：
- 双击条目用系统默认程序打开；拖入外部文件自动移入本格子的文件夹
- 格子里选中的文件/文件夹（可多选）可拖出到资源管理器、桌面或其它格子
- 预览方式可在"图标视图 / 紧凑列表"间切换（右键菜单），图标模式显示
  Windows 真实文件图标（QFileIconProvider，系统级缓存，开销极低）
- 窗口拖动结束后通过防抖回调上报位置，实现"格子位置记忆"
"""
import os
import subprocess

from PySide6.QtCore import (QFileInfo, QMimeData, QPoint, QRect, QSize, Qt,
                            QTimer, QUrl)
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont,
                           QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication,
                               QFileIconProvider, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QListView, QMenu, QMessageBox, QVBoxLayout,
                               QWidget)

from organizer import CATEGORY_RULES, FOLDER_CATEGORY, move_to_recycle_bin

# 格子外观常量
WIDGET_WIDTH = 270        # 格子宽度（像素）
WIDGET_HEIGHT = 360       # 格子高度（像素）
BG_ALPHA = 232            # 背景不透明度（0-255）
CORNER_RADIUS = 14        # 圆角半径
TITLE_BAR_HEIGHT = 36     # 标题栏高度
ICON_SIZE = 38            # 图标模式下文件图标的边长（像素）
GRID_CELL_W = 76          # 图标模式网格单元宽（保证每行 3 列排满）
GRID_CELL_H = 84          # 图标模式网格单元高

# 内置分类（由扩展名/文件夹规则决定，参与自动整理）：不可重命名
BUILTIN_GRID_NAMES = set(CATEGORY_RULES) | {FOLDER_CATEGORY}

# 桌面搜索格子的保留名称（独立于分类，位置/尺寸记忆也用它作键）
SEARCH_GRID_TITLE = "搜索"

# 性能与行为常量
MAX_LIST_ITEMS = 200            # 列表最多渲染的条目数，防止大目录拖慢游戏
POSITION_SAVE_DELAY_MS = 600    # 拖动停止后延迟保存位置的防抖时间
SNAP_DISTANCE = 10              # 拖动窗口时与其它格子边缘对齐的吸附距离（像素）

# 自由调整窗口尺寸（拖拽右/下边缘或右下角，内容自动重排）
RESIZE_EDGE = 6                 # 边缘/角落拖拽热区宽度（像素）
MIN_WIDGET_W, MIN_WIDGET_H = 170, 150      # 窗口最小宽高
MAX_WIDGET_W, MAX_WIDGET_H = 1200, 1200    # 窗口最大宽高
SIZE_SAVE_DELAY_MS = 600        # 尺寸保存防抖（与位置保存同节奏）

# 图标/列宽缩放（Ctrl+滚轮 / 右键"尺寸调整"）：按档位缩放单元格，窗口尺寸不变
SCALE_LEVELS = (0.75, 0.85, 1.0, 1.15, 1.3, 1.5)
SCALE_MIN, SCALE_MAX = SCALE_LEVELS[0], SCALE_LEVELS[-1]

# 暗色主题色（Fluent 风格）
COLOR_BG = QColor(28, 30, 36, BG_ALPHA)        # 格子背景
COLOR_BORDER = QColor(64, 68, 80)              # 边框
COLOR_TEXT = "#e8eaed"                         # 主文字
COLOR_ACCENT = "#4cc2ff"                       # 强调色（Fluent 蓝）

# 格子列表的 QSS：暗色透明背景 + 圆角悬停高亮 + 暗色细滚动条
LIST_QSS = (
    "QListWidget{background:transparent;border:none;color:%s;outline:0;}"
    "QListWidget::item{border-radius:6px;}"
    "QListWidget::item:hover{background:rgba(255,255,255,18);}"
    "QListWidget::item:selected{background:rgba(76,194,255,60);color:#ffffff;}"
    "QScrollBar:vertical{background:transparent;width:8px;margin:2px 1px;}"
    "QScrollBar::handle:vertical{background:#3a3d46;border-radius:4px;min-height:28px;}"
    "QScrollBar::handle:vertical:hover{background:#4cc2ff;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
) % COLOR_TEXT

# 紧凑列表专用样式：行距更松弛（用户反馈"别那么紧凑"），左侧配 16px 小图标
COMPACT_LIST_QSS = LIST_QSS + "QListWidget::item{padding:5px 4px;}"

# 共享图标提供器：全局仅创建一次，Windows 资源管理器图标缓存复用
_ICON_PROVIDER = None

# 文件图标缓存：{(路径, mtime): QIcon}，避免每次刷新重复做白页检测与 Shell 兜底
_FILE_ICON_CACHE = {}


def get_icon_provider() -> QFileIconProvider:
    """返回全局共享的 QFileIconProvider（懒加载单例）。"""
    global _ICON_PROVIDER
    if _ICON_PROVIDER is None:
        _ICON_PROVIDER = QFileIconProvider()
    return _ICON_PROVIDER


def shell_fallback_icon(path: str):
    """Shell API 图标兜底：个别 .lnk/.exe 的 QFileIconProvider 图标为空时改用它。

    通过 SHGetFileInfoW 取 HICON，再经 GDI 拷成 QImage（32bpp）。失败返回 None。
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", ctypes.c_wchar * 260),
                    ("szTypeName", ctypes.c_wchar * 80)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                    ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP),
                    ("hbmColor", wintypes.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                    ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                    ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    shell32.SHGetFileInfoW.restype = ctypes.c_size_t
    user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.POINTER(BITMAPINFO), wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    info = SHFILEINFOW()
    res = shell32.SHGetFileInfoW(path, 0, ctypes.byref(info),
                                 ctypes.sizeof(info), 0x100)   # SHGFI_ICON
    if not res or not info.hIcon:
        return None
    hicon = info.hIcon
    icon_info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(icon_info)):
        user32.DestroyIcon(hicon)
        return None
    try:
        bmp = BITMAP()
        gdi32.GetObjectW(icon_info.hbmColor, ctypes.sizeof(BITMAP),
                         ctypes.byref(bmp))
        width, height = bmp.bmWidth, bmp.bmHeight
        if width <= 0 or height <= 0:
            return None
        hdc = user32.GetDC(None)
        memdc = gdi32.CreateCompatibleDC(hdc)
        try:
            bitmap_info = BITMAPINFO()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bitmap_info.bmiHeader.biWidth = width
            bitmap_info.bmiHeader.biHeight = -height   # 自顶向下
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            buffer = ctypes.create_string_buffer(width * height * 4)
            gdi32.SelectObject(memdc, icon_info.hbmColor)
            gdi32.GetDIBits(memdc, icon_info.hbmColor, 0, height, buffer,
                            ctypes.byref(bitmap_info), 0)
            image = QImage(bytes(buffer), width, height,
                           QImage.Format_ARGB32).copy()
            alpha = bytes(image.constBits())[3::4]
            if alpha and max(alpha) == 0:      # 老式无透明通道图标：按不透明处理
                image = image.convertToFormat(QImage.Format_RGB32)
            return QIcon(QPixmap.fromImage(image))
        finally:
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(None, hdc)
    except Exception:
        return None
    finally:
        if icon_info.hbmColor:
            gdi32.DeleteObject(icon_info.hbmColor)
        if icon_info.hbmMask:
            gdi32.DeleteObject(icon_info.hbmMask)
        user32.DestroyIcon(hicon)


def open_in_shell(path: str) -> None:
    """用系统默认方式打开文件或文件夹（跨平台封装）。"""
    if os.name == "nt":
        os.startfile(path)
    elif sys_darwin():
        subprocess.call(["open", path])
    else:
        subprocess.call(["xdg-open", path])


def _icon_stats(icon, size: int = 38):
    """采样统计图标：返回 (白色占比, 彩色占比)，用于识别"白页"坏图标。"""
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        return 1.0, 0.0
    image = pixmap.toImage()
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return 1.0, 0.0
    whites = colors = samples = 0
    for y in range(0, height, max(1, height // 8)):
        for x in range(0, width, max(1, width // 8)):
            color = image.pixelColor(x, y)
            samples += 1
            if color.alpha() <= 40:
                continue
            r, g, b = color.red(), color.green(), color.blue()
            if r > 225 and g > 225 and b > 225:
                whites += 1
            if max(r, g, b) - min(r, g, b) > 40:
                colors += 1
    return whites / samples, colors / samples


def _url_icon_file(path: str):
    """解析 .url 快捷方式里的 IconFile= 图标路径；不存在时返回 None。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.lower().startswith("iconfile="):
                    icon_path = line.split("=", 1)[1].strip()
                    if not os.path.isabs(icon_path):
                        icon_path = os.path.join(os.path.dirname(path), icon_path)
                    return icon_path if os.path.exists(icon_path) else None
    except OSError:
        pass
    return None


def get_file_icon(path: str):
    """获取文件图标（带缓存）。

    修复个别环境下 QFileIconProvider 对 .lnk/.url 返回"白页"坏图标的问题：
    采样检测到坏图标时——.url 优先直读 IconFile（Steam 游戏图标），
    否则改走 Shell 兜底（SHGetFileInfoW），取到更"彩色"的图标才替换。
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    key = (path, mtime)
    cached = _FILE_ICON_CACHE.get(key)
    if cached is not None:
        return cached

    icon = get_icon_provider().icon(QFileInfo(path))
    if icon.isNull() or not icon.availableSizes():
        white, colors = 1.0, 0.0
    else:
        white, colors = _icon_stats(icon)

    fixed = None
    if path.lower().endswith(".url"):
        # .url（Steam 游戏快捷方式等）：IconFile= 里记录的就是权威图标，优先直读
        icon_file = _url_icon_file(path)
        if icon_file:
            candidate = QIcon(icon_file)
            if not candidate.pixmap(32, 32).isNull():   # 立即渲染验证，避免懒加载假成功
                fixed = candidate
    elif colors < 0.12 and white > 0.30:                # 疑似"白页"坏图标
        fallback = shell_fallback_icon(path)
        if fallback is not None:
            _fb_white, fb_colors = _icon_stats(fallback)
            if fb_colors > colors:
                fixed = fallback                        # Shell 兜底更彩色才采用
    if fixed is not None:
        icon = fixed

    if len(_FILE_ICON_CACHE) > 2000:                # 防呆：缓存过大时清空
        _FILE_ICON_CACHE.clear()
    _FILE_ICON_CACHE[key] = icon
    return icon


def sys_darwin() -> bool:
    """判断是否 macOS（拆出来便于测试桩替换）。"""
    import platform
    return platform.system() == "Darwin"


def scale_geometry(scale: float) -> dict:
    """按缩放比计算格子的几何参数（窗口/图标/网格单元/字号）。

    基准（scale=1.0）：窗口 270x360、图标 38px、网格单元 76x84、标题 11pt、条目 9pt；
    超出档位范围自动钳制。数值关系与排版在桌面实机上验证过（每行 3 列排满）。
    """
    s = max(SCALE_MIN, min(SCALE_MAX, float(scale)))
    icon = max(24, min(56, round(ICON_SIZE * s)))
    return {
        "scale": s,
        "width": round(WIDGET_WIDTH * s),
        "height": round(WIDGET_HEIGHT * s),
        "icon": icon,
        "cell_w": max(58, round(GRID_CELL_W * s)),
        "cell_h": icon + 46,                          # 图标 + 两行文字与内边距
        "title_pt": max(9, min(15, round(11 * s))),
        "small_pt": max(7, min(10, round(8 * s))),
        "item_pt": max(8, min(12, round(9 * s))),
        "open_pt": max(8, min(12, round(9 * s))),
    }


class FileListWidget(QListWidget):
    """文件列表控件：支持多选，并把选中的真实文件作为拖动源拖出到资源管理器/桌面/其它格子。

    - 拖动数据使用标准 file URL（Windows 上即 CF_HDROP），复制还是移动
      由系统默认规则决定（同盘移动、跨盘复制，可用 Shift/Ctrl 强制）。
    - drag_finished_cb 在拖动结束时回调，用于刷新列表（文件被移出后立即更新）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_finished_cb = None
        self.zoom_cb = None                    # Ctrl+滚轮缩放回调：(direction) -> None
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)   # 只作拖动源，接收由外层格子处理
        self.setDefaultDropAction(Qt.MoveAction)

    def wheelEvent(self, event):
        """Ctrl+滚轮缩放整个格子；普通滚轮保持列表滚动。"""
        if event.modifiers() & Qt.ControlModifier and callable(self.zoom_cb):
            self.zoom_cb(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def mimeData(self, items):
        """拖动时携带的真实文件路径列表。"""
        mime = QMimeData()
        urls = []
        for item in items:
            path = item.data(Qt.UserRole)
            if path and os.path.exists(path):
                urls.append(QUrl.fromLocalFile(path))
        mime.setUrls(urls)
        return mime

    def startDrag(self, supported_actions):
        """执行拖动；结束后回调刷新，让"拖出"的结果立即可见。"""
        if not any(it.data(Qt.UserRole) for it in self.selectedItems()):
            return
        super().startDrag(supported_actions)
        if callable(self.drag_finished_cb):
            self.drag_finished_cb()


class GuideOverlay(QWidget):
    """拖动格子时的对齐指示线覆盖层：置顶、鼠标穿透、不抢焦点。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._lines = []          # 覆盖层局部坐标的对齐线（细长 QRect）

    def show_lines(self, lines):
        """显示对齐线；lines 为全局坐标的细长 QRect 列表。"""
        screens = QApplication.screens()
        if not screens:
            return
        area = screens[0].geometry()
        for screen in screens[1:]:
            area = area.united(screen.geometry())
        self.setGeometry(area)
        origin = area.topLeft()
        self._lines = [QRect(line.x() - origin.x(), line.y() - origin.y(),
                             max(1, line.width()), max(1, line.height()))
                       for line in lines]
        self.show()
        self.raise_()
        self.update()

    def hide_lines(self):
        """隐藏全部对齐线。"""
        self._lines = []
        self.hide()

    def paintEvent(self, event):
        """绘制对齐线（主题蓝、2px）。"""
        if not self._lines:
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor(76, 194, 255, 220), 2))
        for line in self._lines:
            if line.height() > line.width():
                painter.drawLine(line.left(), line.top(),
                                 line.left(), line.bottom())
            else:
                painter.drawLine(line.left(), line.top(),
                                 line.right(), line.top())


class FileWidget(QWidget):
    """显示一个分类文件夹内容的桌面格子窗口。"""

    def __init__(self, title: str, folder: str, on_drop,
                 on_position_changed=None, on_preview_changed=None,
                 preview_icons: bool = True, locked: bool = False,
                 on_lock_changed=None, snap_provider=None,
                 on_rename_grid=None, on_delete_grid=None,
                 on_new_grid=None, on_map_folder=None,
                 size=None, metrics_scale=None,
                 on_zoom_changed=None, on_size_changed=None,
                 on_cut_files=None, on_paste_files=None,
                 clipboard_count=None, guide_show=None, guide_hide=None,
                 parent=None):
        """初始化格子。

        :param title: 格子标题（分类名）
        :param folder: 本格子对应的收纳文件夹
        :param on_drop: 拖入文件回调，签名 (list[str], FileWidget)
        :param on_position_changed: 窗口位置变化(防抖后)回调，签名 (FileWidget)
        :param on_preview_changed: 预览方式切换回调，签名 (bool)（图标视图=True）
        :param preview_icons: 初始预览方式（True=图标视图）
        :param locked: 是否锁定位置（锁定后不能拖动窗口，右键菜单可切换）
        :param on_lock_changed: 锁定状态变化回调，签名 (FileWidget, bool)
        :param snap_provider: 拖动吸附提供者，签名 (FileWidget) -> [QRect]（其它格子矩形）
        :param on_rename_grid: 右键"重命名格子…"回调，签名 (FileWidget)
        :param on_delete_grid: 右键"删除此格子"回调，签名 (FileWidget)
        :param on_new_grid: 右键"新建格子…"回调，签名 (FileWidget)
        :param on_map_folder: 右键"映射文件夹…"回调，签名 (FileWidget)
        :param size: 窗口尺寸记忆 (宽, 高)（拖拽调整过）；None 表示默认 270x360
        :param metrics_scale: 图标/列宽缩放比记忆；None 表示 100%
        :param on_zoom_changed: 图标缩放变化回调，签名 (FileWidget, float 缩放比)
        :param on_size_changed: 窗口尺寸变化回调（拖拽/恢复默认后），签名 (FileWidget)
        :param on_cut_files: 右键"剪切"回调，签名 (list[str], FileWidget)
        :param on_paste_files: 右键"粘贴"回调，签名 (FileWidget)
        :param clipboard_count: 返回当前剪切板条目数的回调（用于菜单文案与可用状态）
        :param guide_show: 显示对齐指示线回调，签名 (lines: list[QRect]，全局坐标)
        :param guide_hide: 隐藏对齐指示线回调
        """
        super().__init__(parent)
        self.title = title
        self.folder = folder
        self.on_drop = on_drop
        self.on_position_changed = on_position_changed
        self.on_preview_changed = on_preview_changed
        self.preview_icons = preview_icons
        self.locked = locked
        self.on_lock_changed = on_lock_changed
        self.snap_provider = snap_provider
        self.on_rename_grid = on_rename_grid
        self.on_delete_grid = on_delete_grid
        self.on_new_grid = on_new_grid
        self.on_map_folder = on_map_folder
        self.on_zoom_changed = on_zoom_changed
        self.on_size_changed = on_size_changed
        self.on_cut_files = on_cut_files
        self.on_paste_files = on_paste_files
        self.clipboard_count = clipboard_count
        self.guide_show = guide_show
        self.guide_hide = guide_hide
        self.scale = 1.0               # 图标/列宽缩放比（1.0 = 基准 38px 图标）
        if metrics_scale is not None:
            try:
                self.scale = max(SCALE_MIN, min(SCALE_MAX,
                                                float(metrics_scale)))
            except (TypeError, ValueError):
                self.scale = 1.0
        self._init_size = self._clamp_size(size)   # 窗口尺寸记忆 (宽, 高)
        self._drag_pos = None          # 窗口拖动的起始鼠标偏移
        self._resize_edge = None       # 正在拖拽调整的边缘：right / bottom / corner
        self._resize_from = None       # 调整开始时的 (鼠标全局坐标, 宽, 高)
        self._position_ready = False   # 首次 show 之前不保存位置（避免覆盖记忆值）
        self._drag_active = False      # 是否有文件正悬停拖拽在本格子上（高亮提示）
        self._item_total = 0           # 最近一次刷新的条目总数（供锁定标签显示）

        # 无边框 + 置底 + 不显示任务栏按钮 + 半透明背景
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)    # 悬停边缘时切换鼠标指针
        self.resize(WIDGET_WIDTH, WIDGET_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(4)

        # 标题栏：分类名（含条目数）+ 打开文件夹按钮
        title_row = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        self.title_label.setStyleSheet(f"color:{COLOR_TEXT};background:transparent;")
        self.count_label = QLabel("")
        self.count_label.setFont(QFont("Microsoft YaHei UI", 8))
        self.count_label.setStyleSheet("color:#8a90a0;background:transparent;")
        self.open_btn = QLabel("打开")
        self.open_btn.setFont(QFont("Microsoft YaHei UI", 9))
        self.open_btn.setStyleSheet(f"color:{COLOR_ACCENT};background:transparent;")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.mousePressEvent = self._open_folder  # 点击打开收纳文件夹
        title_row.addWidget(self.title_label)
        title_row.addWidget(self.count_label)
        title_row.addStretch()
        title_row.addWidget(self.open_btn)
        layout.addLayout(title_row)

        # 文件列表：图标视图（默认）或紧凑列表
        self.list_widget = FileListWidget(self)
        self.list_widget.drag_finished_cb = self.refresh   # 拖出结束后立即刷新列表
        self.list_widget.zoom_cb = self._zoom              # Ctrl+滚轮缩放
        self.list_widget.setFont(QFont("Microsoft YaHei UI", 9))
        self.list_widget.setStyleSheet(LIST_QSS)
        self.list_widget.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_menu)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.list_widget)

        # 位置保存防抖定时器：拖动停止 POSITION_SAVE_DELAY_MS 后才上报
        self._pos_timer = QTimer(self)
        self._pos_timer.setSingleShot(True)
        self._pos_timer.timeout.connect(self._emit_position)

        # 尺寸保存防抖定时器：拖拽调整窗口宽高停止后上报
        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.timeout.connect(self._emit_size)

        self._apply_window_size(*self._init_size)  # 应用窗口尺寸记忆
        self._apply_scale(self.scale)              # 应用图标/列宽缩放记忆
        self.set_preview(preview_icons)

    # ---------- 外观绘制 ----------

    def paintEvent(self, event):
        """绘制半透明圆角背景、细边框与标题分隔线。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(),
                            CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(path, COLOR_BG)
        if self._drag_active:                       # 有文件拖拽悬停：强调色高亮边框
            painter.setPen(QPen(QColor(COLOR_ACCENT), 2))
        else:
            painter.setPen(QPen(COLOR_BORDER, 1))
        painter.drawPath(path)
        painter.setPen(QColor(58, 62, 74))
        painter.drawLine(12, TITLE_BAR_HEIGHT, self.width() - 12, TITLE_BAR_HEIGHT)

    # ---------- 窗口拖动与位置记忆 ----------

    def mousePressEvent(self, event):
        """按下：优先响应右下边缘的尺寸调整，其次空白处拖动窗口（锁定均不响应）。"""
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge is not None:
                self._resize_edge = edge
                self._resize_from = (event.globalPosition().toPoint(),
                                     self.x(), self.y(),
                                     self.width(), self.height())
                event.accept()
                return
            if not self.locked:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """移动：调整尺寸 / 拖动窗口 / 悬停边缘时切换鼠标指针。"""
        if self._resize_edge is not None and event.buttons() & Qt.LeftButton:
            start, x0, y0, width0, height0 = self._resize_from
            delta = event.globalPosition().toPoint() - start
            peers = self.snap_provider(self) if self.snap_provider else []
            geometry, guides = self._resize_geometry(
                self._resize_edge, (x0, y0, width0, height0), delta, peers)
            if guides:
                self._show_guides(guides)
            else:
                self._hide_guides()
            self.setGeometry(*geometry)
            if self._position_ready:
                self._pos_timer.start(POSITION_SAVE_DELAY_MS)   # 左/上边缘会改位置
            self._size_timer.start(SIZE_SAVE_DELAY_MS)
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            pos = event.globalPosition().toPoint() - self._drag_pos
            if self.snap_provider is not None:
                pos, guides = self._apply_snap(
                    pos, self.snap_provider(self) or [])
                if guides:
                    self._show_guides(guides)
                else:
                    self._hide_guides()
            self.move(pos)
            if self._position_ready:
                self._pos_timer.start(POSITION_SAVE_DELAY_MS)
        elif not (event.buttons() & Qt.LeftButton):
            edge = self._edge_at(event.position().toPoint())
            cursors = {"bottom-right": Qt.SizeFDiagCursor,
                       "top-left": Qt.SizeFDiagCursor,
                       "top-right": Qt.SizeBDiagCursor,
                       "bottom-left": Qt.SizeBDiagCursor,
                       "right": Qt.SizeHorCursor,
                       "left": Qt.SizeHorCursor,
                       "bottom": Qt.SizeVerCursor,
                       "top": Qt.SizeVerCursor}
            if edge is not None:
                self.setCursor(cursors[edge])
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """释放鼠标：结束尺寸调整或窗口拖动（尺寸由防抖定时器持久化）。"""
        self._resize_edge = None
        self._resize_from = None
        self._drag_pos = None
        self._hide_guides()
        super().mouseReleaseEvent(event)

    def _apply_snap(self, pos, peers):
        """把拖动中的目标位置吸附到其它格子边缘；返回 (新位置, 对齐指示线)。

        对齐规则：左缘对齐、右缘对齐、上缘对齐、下缘对齐（阈值 SNAP_DISTANCE）；
        指示线为全局坐标的细长 QRect，交给 GuideOverlay 绘制。
        """
        guides = []
        width, height = self.width(), self.height()
        for rect in peers:
            if abs(pos.x() - rect.x()) <= SNAP_DISTANCE:            # 左缘对齐
                pos.setX(rect.x())
                top = min(pos.y(), rect.y())
                bottom = max(pos.y() + height, rect.y() + rect.height())
                guides.append(QRect(rect.x(), top, 1, bottom - top))
            elif abs(pos.x() + width - rect.x() - rect.width()) <= SNAP_DISTANCE:
                pos.setX(rect.x() + rect.width() - width)           # 右缘对齐
                top = min(pos.y(), rect.y())
                bottom = max(pos.y() + height, rect.y() + rect.height())
                guides.append(QRect(pos.x() + width, top, 1, bottom - top))
            if abs(pos.y() - rect.y()) <= SNAP_DISTANCE:            # 上缘对齐
                pos.setY(rect.y())
                left = min(pos.x(), rect.x())
                right = max(pos.x() + width, rect.x() + rect.width())
                guides.append(QRect(left, rect.y(), right - left, 1))
            elif abs(pos.y() + height - rect.y() - rect.height()) <= SNAP_DISTANCE:
                pos.setY(rect.y() + rect.height() - height)         # 下缘对齐
                left = min(pos.x(), rect.x())
                right = max(pos.x() + width, rect.x() + rect.width())
                guides.append(QRect(left, pos.y() + height, right - left, 1))
        return pos, guides

    def _show_guides(self, lines):
        """显示对齐指示线（通过外部覆盖层）。"""
        if self.guide_show is not None:
            self.guide_show(lines)

    def _hide_guides(self):
        """隐藏对齐指示线。"""
        if self.guide_hide is not None:
            self.guide_hide()

    def _clamp_size(self, size):
        """把尺寸记忆解析并钳制到允许范围；无效时返回默认尺寸。"""
        try:
            width = max(MIN_WIDGET_W, min(MAX_WIDGET_W, int(size[0])))
            height = max(MIN_WIDGET_H, min(MAX_WIDGET_H, int(size[1])))
            return width, height
        except (TypeError, ValueError, IndexError):
            return WIDGET_WIDTH, WIDGET_HEIGHT

    def _apply_window_size(self, width: int, height: int):
        """应用窗口宽高（右/下边缘拖拽调整；内容自动重排、不缩放图标）。"""
        width = max(MIN_WIDGET_W, min(MAX_WIDGET_W, int(width)))
        height = max(MIN_WIDGET_H, min(MAX_WIDGET_H, int(height)))
        self.resize(width, height)

    def _edge_at(self, pos):
        """返回上下左右边缘/角落热区名称；不在热区内或已锁定时返回 None。"""
        if self.locked:
            return None
        at_left = pos.x() < RESIZE_EDGE
        at_right = pos.x() >= self.width() - RESIZE_EDGE
        at_top = pos.y() < RESIZE_EDGE
        at_bottom = pos.y() >= self.height() - RESIZE_EDGE
        horizontal = "left" if at_left else ("right" if at_right else "")
        vertical = "top" if at_top else ("bottom" if at_bottom else "")
        if horizontal and vertical:
            return f"{vertical}-{horizontal}"    # 例如 bottom-right / top-left
        return horizontal or vertical or None

    def _resize_geometry(self, edge, start, delta, peers):
        """按拖拽的边缘与位移计算新几何（含四边吸附）；返回 (x, y, w, h, 指示线)。

        支持八方向：left/right/top/bottom 及四个角；左/上边缘拖动会同时移动位置。
        拖动中的边缘与其它格子对应边缘（左/右缘、上/下缘）相差 ≤ SNAP_DISTANCE
        时吸附并生成对齐指示线。
        """
        x0, y0, width0, height0 = start
        left, top = x0, y0
        width, height = width0, height0
        if "right" in edge:
            width = width0 + delta.x()
        elif "left" in edge:
            width = width0 - delta.x()
        if "bottom" in edge:
            height = height0 + delta.y()
        elif "top" in edge:
            height = height0 - delta.y()
        width = max(MIN_WIDGET_W, min(MAX_WIDGET_W, width))
        height = max(MIN_WIDGET_H, min(MAX_WIDGET_H, height))
        if "left" in edge:
            left = x0 + (width0 - width)
        if "top" in edge:
            top = y0 + (height0 - height)

        guides = []
        for rect in peers or []:
            x_edges = (rect.x(), rect.x() + rect.width())
            y_edges = (rect.y(), rect.y() + rect.height())
            if "left" in edge:
                for edge_x in x_edges:
                    if abs(left - edge_x) <= SNAP_DISTANCE:
                        left = edge_x
                        guide_top = min(top, rect.y())
                        guide_bottom = max(top + height, rect.y() + rect.height())
                        guides.append(QRect(edge_x, guide_top, 1,
                                            guide_bottom - guide_top))
                        break
            if "right" in edge:
                for edge_x in x_edges:
                    if abs(left + width - edge_x) <= SNAP_DISTANCE:
                        left = edge_x - width
                        guide_top = min(top, rect.y())
                        guide_bottom = max(top + height, rect.y() + rect.height())
                        guides.append(QRect(edge_x, guide_top, 1,
                                            guide_bottom - guide_top))
                        break
            if "top" in edge:
                for edge_y in y_edges:
                    if abs(top - edge_y) <= SNAP_DISTANCE:
                        top = edge_y
                        guide_left = min(left, rect.x())
                        guide_right = max(left + width, rect.x() + rect.width())
                        guides.append(QRect(guide_left, edge_y,
                                            guide_right - guide_left, 1))
                        break
            if "bottom" in edge:
                for edge_y in y_edges:
                    if abs(top + height - edge_y) <= SNAP_DISTANCE:
                        top = edge_y - height
                        guide_left = min(left, rect.x())
                        guide_right = max(left + width, rect.x() + rect.width())
                        guides.append(QRect(guide_left, edge_y,
                                            guide_right - guide_left, 1))
                        break
        return (left, top, width, height), guides

    def _emit_size(self):
        """尺寸防抖到期：把当前窗口宽高上报给回调（持久化）。"""
        if self.on_size_changed is not None:
            self.on_size_changed(self)

    def showEvent(self, event):
        """首次显示后位置即视为有效，之后的移动才需要记忆。"""
        self._position_ready = True
        super().showEvent(event)

    def hideEvent(self, event):
        """隐藏前若还有未保存的位置/尺寸则立即保存。"""
        self._hide_guides()
        if self._pos_timer.isActive():
            self._pos_timer.stop()
            self._emit_position()
        if self._size_timer.isActive():
            self._size_timer.stop()
            self._emit_size()
        super().hideEvent(event)

    def _emit_position(self):
        """防抖到期：把当前窗口位置上报给回调（保存进配置）。"""
        if self._position_ready and self.on_position_changed is not None:
            self.on_position_changed(self)

    # ---------- 预览方式 ----------

    def set_preview(self, icons: bool):
        """切换预览方式并重建列表：True=图标视图，False=紧凑列表。"""
        self.preview_icons = icons
        self._apply_view_mode()
        self.refresh()

    def _apply_view_mode(self):
        """按当前预览方式与缩放档位设置列表视图参数。"""
        if self.preview_icons:
            self.list_widget.setStyleSheet(LIST_QSS)
            self.list_widget.setViewMode(QListView.IconMode)
            self.list_widget.setIconSize(QSize(self._icon_px, self._icon_px))
            self.list_widget.setGridSize(QSize(self._cell_w, self._cell_h))
            self.list_widget.setWordWrap(True)
            self.list_widget.setResizeMode(QListView.Adjust)
            self.list_widget.setLayoutMode(QListView.Batched)  # 分批绘制更省资源
            self.list_widget.setBatchSize(50)
            self.list_widget.setUniformItemSizes(True)         # 单元格等高等宽，布局更整齐
        else:
            self.list_widget.setStyleSheet(COMPACT_LIST_QSS)   # 行距更松弛
            self.list_widget.setViewMode(QListView.ListMode)
            self.list_widget.setGridSize(QSize())              # 恢复默认行高
            self.list_widget.setWordWrap(False)
            self.list_widget.setUniformItemSizes(False)
            self.list_widget.setIconSize(QSize(16, 16))        # 左侧微小图标

    # ---------- 尺寸缩放（Ctrl+滚轮 / 右键"格子尺寸"） ----------

    def _apply_scale(self, scale: float):
        """按缩放比应用图标/列宽与字号（窗口尺寸不变，内容自动重排；不刷新列表）。"""
        geo = scale_geometry(scale)
        self.scale = geo["scale"]
        self._icon_px = geo["icon"]
        self._cell_w = geo["cell_w"]
        self._cell_h = geo["cell_h"]
        self.title_label.setFont(
            QFont("Microsoft YaHei UI", geo["title_pt"], QFont.Bold))
        self.count_label.setFont(QFont("Microsoft YaHei UI", geo["small_pt"]))
        self.open_btn.setFont(QFont("Microsoft YaHei UI", geo["open_pt"]))
        self.list_widget.setFont(QFont("Microsoft YaHei UI", geo["item_pt"]))
        self._apply_view_mode()

    def _zoom(self, direction: int):
        """按档位放大/缩小：direction=+1 加大、-1 减小；到顶/到底时无动作。"""
        index = min(range(len(SCALE_LEVELS)),
                    key=lambda i: abs(SCALE_LEVELS[i] - self.scale))
        new_index = max(0, min(len(SCALE_LEVELS) - 1, index + direction))
        if new_index == index:
            return
        self._apply_scale(SCALE_LEVELS[new_index])
        self.refresh()
        if self.on_zoom_changed is not None:
            self.on_zoom_changed(self, self.scale)

    def _reset_scale(self):
        """恢复默认尺寸（100%）。"""
        if self.scale == 1.0:
            return
        self._apply_scale(1.0)
        self.refresh()
        if self.on_zoom_changed is not None:
            self.on_zoom_changed(self, 1.0)

    def _reset_window_size(self):
        """窗口恢复默认大小（右键"尺寸调整"）。"""
        if (self.width(), self.height()) == (WIDGET_WIDTH, WIDGET_HEIGHT):
            return
        self._apply_window_size(WIDGET_WIDTH, WIDGET_HEIGHT)
        if self.on_size_changed is not None:
            self.on_size_changed(self)

    def wheelEvent(self, event):
        """Ctrl+滚轮缩放格子（标题栏等空白区域同样生效）。"""
        if event.modifiers() & Qt.ControlModifier:
            self._zoom(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def _show_preview_menu(self, menu: QMenu):
        """向右键菜单追加"预览方式"子菜单（单选互斥）。"""
        sub = menu.addMenu("预览方式")
        group = QActionGroup(sub)
        act_icons = QAction("图标视图", sub, checkable=True)
        act_text = QAction("紧凑列表", sub, checkable=True)
        act_icons.setChecked(self.preview_icons)
        act_text.setChecked(not self.preview_icons)
        group.addAction(act_icons)
        group.addAction(act_text)
        act_icons.triggered.connect(lambda: self._switch_preview(True))
        act_text.triggered.connect(lambda: self._switch_preview(False))
        sub.addAction(act_icons)
        sub.addAction(act_text)

    def _switch_preview(self, icons: bool):
        """切换本格子预览方式并通知外部持久化（只影响本格子）。"""
        if icons == self.preview_icons:
            return
        self.set_preview(icons)
        if self.on_preview_changed is not None:
            self.on_preview_changed(self, icons)

    def _show_zoom_menu(self, menu: QMenu):
        """向右键菜单追加"尺寸调整"子菜单（图标缩放 + 窗口恢复默认）。"""
        sub = menu.addMenu("尺寸调整")
        info = sub.addAction(f"当前图标 {round(self.scale * 100)}%")
        info.setEnabled(False)
        sub.addSeparator()
        sub.addAction("图标放大（Ctrl+滚轮上）", lambda: self._zoom(1))
        sub.addAction("图标缩小（Ctrl+滚轮下）", lambda: self._zoom(-1))
        sub.addAction("图标恢复默认大小", self._reset_scale)
        sub.addSeparator()
        sub.addAction("窗口恢复默认大小", self._reset_window_size)

    # ---------- 位置锁定 ----------

    def _set_locked(self, locked: bool):
        """切换位置锁定状态并通知外部持久化（右键菜单调用）。"""
        if locked == self.locked:
            return
        self.locked = locked
        self._update_count_label()
        if self.on_lock_changed is not None:
            self.on_lock_changed(self, locked)

    # ---------- 文件列表 ----------

    def refresh(self):
        """重新读取收纳文件夹内容并刷新列表（文件夹在前、文件在后）。"""
        self.list_widget.clear()
        try:
            names = sorted(os.listdir(self.folder), key=str.lower)
        except OSError:
            names = []
        dirs, files = [], []
        for name in names:
            full = os.path.join(self.folder, name)
            (dirs if os.path.isdir(full) else files).append((name, full))
        total = len(dirs) + len(files)
        self._update_count_label(total)

        if total == 0:                        # 空状态引导，告诉用户格子怎么用
            tip = QListWidgetItem("拖入文件或文件夹即可收纳")
            tip.setFlags(Qt.NoItemFlags)
            tip.setTextAlignment(Qt.AlignCenter)
            tip.setForeground(QBrush(QColor("#6a7080")))
            self.list_widget.addItem(tip)
            return

        entries = dirs + files
        shown = entries[:MAX_LIST_ITEMS]
        for name, full in shown:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, full)   # 完整路径存 UserRole，打开时取用
            item.setToolTip(full)
            if self.preview_icons:
                item.setTextAlignment(Qt.AlignHCenter)        # 图标与文字在单元格内水平居中
            else:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            icon = get_file_icon(full)        # 白页修复 + Shell 兜底 + 缓存
            if not icon.isNull():
                item.setIcon(icon)
            self.list_widget.addItem(item)
        if total > MAX_LIST_ITEMS:            # 超出上限时显示提示占位条目
            tip = QListWidgetItem(f"… 共 {total} 项，点右上角\"打开\"查看全部")
            tip.setFlags(Qt.NoItemFlags)      # 不可选中/双击
            self.list_widget.addItem(tip)

    def _update_count_label(self, total=None):
        """更新标题旁的数量标签（含位置锁定状态）。"""
        if total is not None:
            self._item_total = total
        suffix = "（已锁定）" if self.locked else ""
        if self._item_total:
            self.count_label.setText(f"{self._item_total} 项{suffix}")
        else:
            self.count_label.setText(suffix)

    def _open_item(self, item):
        """双击列表项：用系统默认程序打开文件或文件夹。"""
        path = item.data(Qt.UserRole)
        if path:
            open_in_shell(path)

    def _open_folder(self, _event):
        """点击"打开"：在资源管理器中打开本格子的收纳文件夹。"""
        os.makedirs(self.folder, exist_ok=True)
        open_in_shell(self.folder)

    def _show_menu(self, pos):
        """右键菜单：条目操作 / 粘贴 / 外观与尺寸 / 格子管理 / 刷新 五个分区。

        按使用频率分组：常用文件操作在前，格子管理在后，分隔线区分区块。
        """
        self._build_menu(pos).exec(self.list_widget.mapToGlobal(pos))

    def _build_menu(self, pos) -> QMenu:
        """构建右键菜单（拆分出来便于单元测试，不进入事件循环）。"""
        item = self.list_widget.itemAt(pos)
        if item is not None and not (item.flags() & Qt.ItemIsEnabled):
            item = None                       # 空状态/上限提示条目：不参与条目菜单
        if item is not None and not item.isSelected():
            self.list_widget.clearSelection()
            item.setSelected(True)            # 右键未选中条目时先选中它（支持多选批量操作）
        menu = QMenu(self)
        menu.setStyleSheet(
            ("QMenu{background:#26282e;color:%s;border:1px solid #3a3d46;"
             "border-radius:8px;padding:4px;}"
             "QMenu::item{padding:5px 18px;border-radius:5px;}"
             "QMenu::item:selected{background:rgba(76,194,255,70);}"
             "QMenu::separator{height:1px;background:#3a3d46;margin:4px 6px;}"
             ) % COLOR_TEXT
        )
        if item is not None:
            path = item.data(Qt.UserRole)
            menu.addAction("打开", lambda: open_in_shell(path))
            menu.addAction("打开所在位置",
                           lambda: open_in_shell(os.path.dirname(path)))
            menu.addSeparator()
            paths = [it.data(Qt.UserRole) for it in self.list_widget.selectedItems()
                     if it.flags() & Qt.ItemIsEnabled and it.data(Qt.UserRole)]
            suffix = f"（{len(paths)} 项）" if len(paths) > 1 else ""
            menu.addAction(f"复制路径{suffix}", lambda: self._copy_paths(paths))
            menu.addAction(f"剪切{suffix}", lambda: self._cut_paths(paths))
            menu.addAction(f"删除到回收站{suffix}",
                           lambda: self._delete_paths(paths))
            menu.addSeparator()
        clip_count = self.clipboard_count() if self.clipboard_count else 0
        act_paste = menu.addAction(
            f"粘贴（{clip_count} 项）" if clip_count else "粘贴（无内容）",
            lambda: self.on_paste_files(self) if self.on_paste_files else None)
        act_paste.setEnabled(clip_count > 0)
        if clip_count == 0:
            act_paste.setToolTip("先在有内容的格子上右键选择“剪切”")
        menu.addSeparator()
        menu.addAction("打开文件夹", self._open_folder)
        self._show_preview_menu(menu)
        self._show_zoom_menu(menu)
        act_lock = menu.addAction("锁定位置")
        act_lock.setCheckable(True)
        act_lock.setChecked(self.locked)
        act_lock.toggled.connect(self._set_locked)
        menu.addSeparator()
        act_rename = menu.addAction("重命名格子…")
        if self.title in BUILTIN_GRID_NAMES or self.title == SEARCH_GRID_TITLE:
            act_rename.setEnabled(False)
            act_rename.setToolTip("内置分类/搜索格子不可重命名")
        else:
            act_rename.triggered.connect(
                lambda: self.on_rename_grid(self) if self.on_rename_grid else None)
        menu.addAction("新建格子…",
                       lambda: self.on_new_grid(self) if self.on_new_grid else None)
        menu.addAction("映射文件夹…",
                       lambda: self.on_map_folder(self) if self.on_map_folder else None)
        act_delete = menu.addAction("删除此格子")
        act_delete.triggered.connect(
            lambda: self.on_delete_grid(self) if self.on_delete_grid else None)
        menu.addSeparator()
        menu.addAction("刷新", self.refresh)
        return menu

    def _copy_paths(self, paths):
        """把条目的完整路径复制到剪贴板。"""
        QApplication.clipboard().setText("\n".join(p for p in paths if p))

    def _cut_paths(self, paths):
        """把选中条目交给外部剪切（用于跨格子粘贴移动）。"""
        if self.on_cut_files is not None:
            self.on_cut_files([p for p in paths if p], self)

    def _delete_paths(self, paths):
        """把选中条目删除到回收站（确认后执行，可在资源管理器恢复）。"""
        paths = [p for p in paths if p]
        if not paths:
            return
        names = "、".join(os.path.basename(p) for p in paths[:3])
        if len(paths) > 3:
            names += f" 等 {len(paths)} 项"
        answer = QMessageBox.question(
            self, "删除到回收站",
            f"确定把 {names} 删除到回收站吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        for path in paths:
            move_to_recycle_bin(path)
        self.refresh()

    # ---------- 文件/文件夹拖入 ----------

    def dragEnterEvent(self, event):
        """有文件拖入时接受并高亮边框（从本格子拖出的除外）。"""
        if event.source() is self.list_widget:
            event.ignore()
            return
        if event.mimeData().hasUrls():
            self._drag_active = True
            self.update()
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        """拖拽在格子内移动时持续接受，确保 dropEvent 正常触发。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        """拖拽离开格子：取消高亮。"""
        self._drag_active = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        """拖放完成：收集本地路径交给回调处理。"""
        self._drag_active = False
        self.update()
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and os.path.exists(local):
                paths.append(local)
        if paths:
            event.acceptProposedAction()
            if self.on_drop is not None:
                self.on_drop(paths, self)


class SearchWidget(FileWidget):
    """桌面搜索格子：常驻桌面、可随时删除，输入关键词搜索收纳库与路径映射文件夹。

    - 输入防抖 300ms；支持拼音搜索（organizer.search_storage 内置）；
    - 结果条目双击打开，右键可打开所在位置/复制路径/剪切（配合目标格子粘贴移动）；
    - 位置/尺寸/图标缩放/锁定等记忆完全复用 FileWidget 的机制（键名固定为"搜索"）。
    """

    def __init__(self, on_search, *args, **kwargs):
        self._on_search = on_search
        self._search_ready = False
        super().__init__(*args, **kwargs)
        self.title_label.setText(SEARCH_GRID_TITLE)
        self.count_label.setText("")
        self.setAcceptDrops(False)                     # 搜索格子不接受拖入
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("输入关键词搜索（支持拼音）")
        self.layout().insertWidget(1, self.search_edit)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._run_search)
        self.search_edit.textChanged.connect(
            lambda _text: self._search_timer.start(300))
        self._search_ready = True
        self.refresh()

    def refresh(self):
        """刷新 = 用当前关键词重跑搜索（存储变动/重建格子时自动保持最新）。"""
        if not getattr(self, "_search_ready", False):
            self.list_widget.clear()
            return
        self._run_search()

    def _run_search(self):
        """执行搜索并填充结果列表。"""
        keyword = self.search_edit.text().strip()
        self.list_widget.clear()
        if not keyword:
            self.count_label.setText("")
            tip = QListWidgetItem("输入关键词搜索收纳库（支持拼音）")
            tip.setFlags(Qt.NoItemFlags)
            tip.setTextAlignment(Qt.AlignCenter)
            tip.setForeground(QBrush(QColor("#6a7080")))
            self.list_widget.addItem(tip)
            return
        results = self._on_search(keyword) or []
        self.count_label.setText(f"{len(results)} 项")
        for category, path in results[:MAX_LIST_ITEMS]:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(f"[{category}] {path}")
            if self.preview_icons:
                item.setTextAlignment(Qt.AlignHCenter)
            else:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            icon = get_file_icon(path)
            if not icon.isNull():
                item.setIcon(icon)
            self.list_widget.addItem(item)
