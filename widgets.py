# -*- coding: utf-8 -*-
"""桌面格子窗口：无边框、半透明、置底，支持文件/文件夹拖入与窗口拖动。

每个格子对应收纳目录下的一个分类文件夹：
- 双击条目用系统默认程序打开；拖入外部文件自动移入本格子的文件夹
- 格子里选中的文件/文件夹（可多选）可拖出到资源管理器、桌面或其它格子
- 预览方式可在"图标视图 / 紧凑列表"间切换（右键菜单），图标模式显示
  Windows 真实文件图标（QFileIconProvider，系统级缓存，开销极低）
- 图片类文件（jpg/png/gif 等栅格图）显示内容缩略图：解码在后台线程执行，
  缩放后的 QImage 先缓存再由 GUI 线程转成图标，避免大图/多图卡住界面
- 窗口拖动结束后通过防抖回调上报位置，实现"格子位置记忆"
"""
import os
import subprocess
import threading
import weakref

from PySide6.QtCore import (QFileInfo, QMimeData, QObject, QPoint, QRect, QRectF,
                            QRunnable, QSize, QThreadPool, Qt, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QAction, QActionGroup, QBrush, QColor, QFont,
                           QIcon, QImage, QImageReader, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication,
                               QFileIconProvider, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QListView, QMenu, QMessageBox, QVBoxLayout,
                               QWidget)

from organizer import (CATEGORY_RULES, FOLDER_CATEGORY, move_to_recycle_bin,
                       rename_entry)

# 格子外观常量
WIDGET_WIDTH = 270        # 格子宽度（像素）
WIDGET_HEIGHT = 360       # 格子高度（像素）
BG_ALPHA = 208            # 玻璃主背景不透明度（比纯暗色更透，透出壁纸形成毛玻璃观感）
CORNER_RADIUS = 12        # 圆角半径（收敛一些，避免大圆角带来的"气泡感"）
TITLE_BAR_HEIGHT = 36     # 标题栏高度
COLLAPSED_HEIGHT = TITLE_BAR_HEIGHT + 8   # 收起形态总高度：只剩标题行 + 上下边距
ICON_SIZE = 38            # 图标模式下文件图标的边长（像素）
GRID_CELL_W = 76          # 图标模式网格单元宽（保证每行 3 列排满）
GRID_CELL_H = 84          # 图标模式网格单元高
COMPACT_ICON_PX = 24      # 紧凑列表左侧图标/缩略图的边长（比原 16px 更能看出图片内容）

# 桌面搜索格子：扁平搜索条——平时只有"搜索框高度"，有结果时才向下展开显示结果
SEARCH_BAR_WIDTH = 300    # 搜索条宽度（仍是"一个搜索框"的尺度）
SEARCH_BAR_HEIGHT = 46    # 搜索条高度（仅容纳输入框，不再占满 270×360）
SEARCH_RESULT_ROW_H = 28  # 结果行高（用于估算展开高度）
SEARCH_RESULT_MAX_ROWS = 9  # 展开时最多显示的结果行数（再多就滚动）

# 内置分类（由扩展名/文件夹规则决定，参与自动整理）：
# 可以改"显示名"，但分类键（文件夹名与 classify() 的返回值）必须保持稳定
BUILTIN_GRID_NAMES = set(CATEGORY_RULES) | {FOLDER_CATEGORY}

# 桌面搜索格子的保留名称（独立于分类，位置/尺寸记忆也用它作键）
SEARCH_GRID_TITLE = "搜索"

# 性能与行为常量
MAX_LIST_ITEMS = 200            # 列表最多渲染的条目数，防止大目录拖慢游戏
POSITION_SAVE_DELAY_MS = 600    # 拖动停止后延迟保存位置的防抖时间
SNAP_DISTANCE = 10              # 拖动窗口时与其它格子边缘对齐的吸附距离（像素）

# 格子条目的拖出效果：只声明"移动"，避免跨盘（D: 收纳库 -> C: 桌面）被系统
# 按"复制"处理而把源文件留在收纳库里（详见 FileListWidget.startDrag 的说明）
DRAG_ACTION = Qt.MoveAction

# 自由调整窗口尺寸（拖拽右/下边缘或右下角，内容自动重排）
RESIZE_EDGE = 8                 # 边缘/角落拖拽热区宽度（像素；6px 在小格子上难点中）
MIN_WIDGET_W, MIN_WIDGET_H = 170, 150      # 窗口最小宽高
MAX_WIDGET_W, MAX_WIDGET_H = 1200, 1200    # 窗口最大宽高
SIZE_SAVE_DELAY_MS = 600        # 尺寸保存防抖（与位置保存同节奏）

# 图标/列宽缩放（Ctrl+滚轮）：按档位缩放单元格，窗口尺寸不变
SCALE_LEVELS = (0.75, 0.85, 1.0, 1.15, 1.3, 1.5)
SCALE_MIN, SCALE_MAX = SCALE_LEVELS[0], SCALE_LEVELS[-1]

# 统一间距系统：格子的间距与贴边外边距一律同一数值，外观才"对得齐"。
# 一键「整齐排列」（plan_tidy_layout）与默认落位（plan_default_placement）共用。
WIDGET_GAP = 16        # 格子之间的统一间距（水平与垂直一致）
SCREEN_MARGIN = 16     # 贴屏幕边缘的统一外边距
WIDTH_GROW_CAP = 32    # 排列时单列宽度统一的最大生长量（差异更大则不强行对齐）
HEIGHT_GROW_CAP = 48   # 排列时同行高度统一的最大生长量（同上）

# 图片缩略图：可直接像素解码的栅格图（svg/psd/ai/ico 交给系统图标，不做解码）
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
THUMB_MAX_SRC_BYTES = 40 * 1024 * 1024   # 超大图不缩略（PNG 需全量解码，避免卡顿）
THUMB_CACHE_MAX = 500                    # 缩略图缓存上限，超出后淘汰最早的一半

# 玻璃拟态主题色：中性暗色底 + 单层浅描边（"深底 + 细边"，去掉霓虹蓝与发光）
COLOR_BG = QColor(31, 31, 35, BG_ALPHA)             # 玻璃主背景（渐变亮端，在上方）
COLOR_BG_BOTTOM = QColor(22, 22, 25, BG_ALPHA)      # 玻璃主背景（渐变暗端，在下方）
COLOR_GLOW = QColor(255, 255, 255, 16)              # 顶部高光渐变的起始色（弱化，只留一点玻璃反光）
COLOR_BORDER = QColor(255, 255, 255, 30)            # 外描边：中性浅灰细线
COLOR_INNER_HIGHLIGHT = QColor(255, 255, 255, 12)   # 内描边：更淡的一圈，做出玻璃"厚度"而不刺眼
COLOR_SEPARATOR = QColor(255, 255, 255, 22)         # 标题分隔线（左右渐隐，比实线更精致）
COLOR_TEXT = "#eceef1"                              # 主文字
COLOR_TEXT_DIM = "#9ba1ab"                          # 次级文字（条目计数、状态提示）
COLOR_TEXT_FAINT = "#6f747d"                        # 弱提示文字（空状态引导）
COLOR_ACCENT = "#93a1b3"                            # 强调色（低饱和钢灰，替代原霓虹蓝）

# 格子列表的 QSS：透明背景 + 玻璃圆角悬停/选中 + 半透明细滚动条
LIST_QSS = (
    "QListWidget{background:transparent;border:none;color:%s;outline:0;padding:2px;}"
    "QListWidget::item{border-radius:8px;padding:2px;}"
    "QListWidget::item:hover{background:rgba(255,255,255,16);}"
    "QListWidget::item:selected{background:rgba(255,255,255,30);color:#ffffff;"
    "border:1px solid rgba(255,255,255,88);}"
    "QScrollBar:vertical{background:transparent;width:8px;margin:2px 1px;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,54);"
    "border-radius:4px;min-height:28px;}"
    "QScrollBar::handle:vertical:hover{background:%s;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
) % (COLOR_TEXT, COLOR_ACCENT)

# 紧凑列表专用样式：行距更松弛（用户反馈"别那么紧凑"），左侧配 24px 小图标/缩略图
COMPACT_LIST_QSS = LIST_QSS + "QListWidget::item{padding:6px 5px;border-radius:7px;}"

# 搜索格子输入框样式：原先未设样式会退回系统浅色默认外观，与玻璃格子格格不入
SEARCH_EDIT_QSS = (
    "QLineEdit{background:rgba(255,255,255,14);border:1px solid rgba(255,255,255,34);"
    "border-radius:8px;padding:5px 8px;color:%s;"
    "selection-background-color:rgba(255,255,255,70);}"
    "QLineEdit:focus{border-color:rgba(255,255,255,150);background:rgba(255,255,255,22);}"
) % COLOR_TEXT

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

    if len(_FILE_ICON_CACHE) > 2000:                # 防呆：超限时批量淘汰最早的一半
        for key in list(_FILE_ICON_CACHE)[:1000]:   # （原为整体清空：下一轮刷新
            _FILE_ICON_CACHE.pop(key, None)         #   会出现全量重算的卡顿尖峰）
    _FILE_ICON_CACHE[key] = icon
    return icon


def sys_darwin() -> bool:
    """判断是否 macOS（拆出来便于测试桩替换）。"""
    import platform
    return platform.system() == "Darwin"


# ---------- 图片缩略图（后台解码 + 全局缓存） ----------

def is_thumbnailable(path: str) -> bool:
    """是否值得生成缩略图：扩展名支持像素解码，且源文件体积在阈值内。"""
    if os.path.splitext(path)[1].lower() not in THUMB_EXTS:
        return False
    try:
        return os.path.getsize(path) <= THUMB_MAX_SRC_BYTES
    except OSError:
        return False


def decode_thumbnail(path: str, size: int) -> QImage:
    """解码出边长不超过 size 的缩略图；失败返回空 QImage。

    刻意只返回 QImage 而不返回 QIcon/QPixmap：QPixmap 只能在 GUI 线程创建，
    本函数运行在后台线程（见 ThumbnailTask.run）。用 setScaledSize 让解码器
    按比例出小图——JPEG 可直接按比例解码，代价远低于解码全尺寸再缩放。
    """
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)          # 尊重 EXIF 方向，避免手机照片倒过来
        source = reader.size()
        if source.isValid() and not source.isEmpty():
            reader.setScaledSize(
                source.scaled(QSize(size, size), Qt.KeepAspectRatio))
        image = reader.read()
    except Exception:                          # 解码器异常不影响格子刷新
        return QImage()
    if image.isNull():
        return image
    if image.width() > size or image.height() > size:
        image = image.scaled(size, size, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
    return image


class ThumbnailService(QObject):
    """缩略图后台服务：全局单例，把解码丢到 QThreadPool，完成后回传 QImage。

    为什么放后台：一个装满大图的目录在 UI 线程解码会把界面卡住数百毫秒到
    数秒（刷新、整理、拖拽结束都会触发刷新）。缓存键带上 mtime，文件被改写
    后自动失效重解码；正在解码的键记入 _pending，避免重复提交同一张图。
    """

    ready = Signal(str, int, QImage)           # (路径, 边长, 解码结果；失败为空图)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)         # 2 线程足够，避免抢占搜索等其它任务
        self._lock = threading.Lock()          # 缓存/在途集合会被后台线程写入
        self._cache = {}                       # {(路径, mtime, 边长): QImage}
        self._pending = set()                  # 正在解码的键

    def get(self, path: str, size: int):
        """取缩略图：命中缓存返回 QImage；未命中则提交后台任务并返回 None。

        调用方（GUI 线程）拿到 None 时先显示系统图标占位，等 ready 信号回填。
        """
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, mtime, int(size))
        with self._lock:
            cached = self._cache.get(key)
            submit = cached is None and key not in self._pending
            if submit:
                self._pending.add(key)
        if cached is not None:
            return cached
        if submit:
            self.pool.start(ThumbnailTask(self, key))
        return None

    def _store(self, key, image: QImage):
        """后台解码结束（后台线程调用）：写入缓存并广播结果。"""
        with self._lock:
            self._pending.discard(key)
            if not image.isNull():
                self._cache[key] = image
                if len(self._cache) > THUMB_CACHE_MAX:
                    # 键按插入顺序排列，新键在末尾：只淘汰最早的一半，不会误删刚存入的
                    for stale in list(self._cache)[:THUMB_CACHE_MAX // 2]:
                        self._cache.pop(stale, None)
        self.ready.emit(key[0], key[2], image)


class ThumbnailTask(QRunnable):
    """一次缩略图解码任务：后台产出 QImage，经服务信号回到 GUI 线程。"""

    def __init__(self, service: ThumbnailService, key: tuple):
        super().__init__()
        self.service = service
        self.key = key

    def run(self):
        image = decode_thumbnail(self.key[0], self.key[2])
        self.service._store(self.key, image)


_THUMB_SERVICE = None


def _dispatch_thumb(widget_ref, path: str, size: int, image: QImage):
    """把后台缩略图结果转发给仍存活的格子（weakref 已失效则静默丢弃）。"""
    widget = widget_ref()
    if widget is not None:
        widget._on_thumb_ready(path, size, image)


def get_thumbnail_service() -> ThumbnailService:
    """返回全局共享的缩略图服务（懒加载单例）。"""
    global _THUMB_SERVICE
    if _THUMB_SERVICE is None:
        _THUMB_SERVICE = ThumbnailService()
    return _THUMB_SERVICE


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


def plan_tidy_layout(rects, screen, gap: int = WIDGET_GAP,
                     margin: int = SCREEN_MARGIN, fixed=None):
    """把一组格子矩形整理成"行列栅格"，返回与输入等长（顺序不变）的新 QRect 列表。

    这是「整齐排列」的纯逻辑核心，只做几何计算、不依赖窗口对象，便于单测：
    1) 按 x 区间重叠归列（同一列的格子左右缘对齐）；
    2) 列内宽度、行内高度在容差内"只生长"地统一，让边缘完全对齐——
       差异过大的（如收起的窄条、超长列表）不强行拉伸；
    3) 行顶对齐、列/行间距统一为 gap；
    4) 整块超出屏幕时平移回统一外边距以内（优先保证右缘不越界）。
    fixed 为与 rects 等长的布尔列表：True 的矩形（收起态格子）锁定尺寸、
    不参与统一，仅参与对位。
    """
    count = len(rects)
    if count == 0:
        return []
    fixed_flags = list(fixed) if fixed else [False] * count
    work = [QRect(r) for r in rects]

    # 1) 按 x 区间重叠归列（从左到右扫描）
    order = sorted(range(count), key=lambda i: (work[i].x(), work[i].y()))
    columns = []
    column_right = None
    for index in order:
        if columns and work[index].x() < column_right:
            columns[-1].append(index)
            column_right = max(column_right,
                               work[index].x() + work[index].width())
        else:
            columns.append([index])
            column_right = work[index].x() + work[index].width()
    for column in columns:
        column.sort(key=lambda i: (work[i].y(), work[i].x()))

    # 2) 列内宽度统一（容差内只生长，绝不缩小）
    column_widths = []
    for column in columns:
        target = max(work[i].width() for i in column)
        for index in column:
            if (not fixed_flags[index]
                    and target - work[index].width() <= WIDTH_GROW_CAP):
                work[index].setWidth(target)
        column_widths.append(max(work[i].width() for i in column))

    # 3) 行内高度统一（同样只生长）
    row_count = max(len(column) for column in columns)
    for row in range(row_count):
        ids = [column[row] for column in columns if len(column) > row]
        target = max(work[i].height() for i in ids)
        for index in ids:
            if (not fixed_flags[index]
                    and target - work[index].height() <= HEIGHT_GROW_CAP):
                work[index].setHeight(target)

    # 4) 列 x 布局：从左到右、统一间距
    cursor = min(r.x() for r in work)
    for column, width in zip(columns, column_widths):
        for index in column:
            work[index].moveLeft(cursor)
        cursor += width + gap

    # 5) 行 y 布局：各列首行顶部对齐，列内自上而下堆叠
    top = min(work[column[0]].y() for column in columns)
    for column in columns:
        row_top = top
        for index in column:
            work[index].moveTop(row_top)
            row_top += work[index].height() + gap

    # 6) 越界归位：先保证右/下缘不越界，再保证左/上缘不越界
    dx = 0
    right_limit = screen.x() + screen.width() - margin
    right_edge = max(r.x() + r.width() for r in work)
    if right_edge > right_limit:
        dx = right_limit - right_edge
    left_edge = min(r.x() for r in work) + dx
    if left_edge < screen.x() + margin:
        dx += (screen.x() + margin) - left_edge
    dy = 0
    bottom_limit = screen.y() + screen.height() - margin
    bottom_edge = max(r.y() + r.height() for r in work)
    if bottom_edge > bottom_limit:
        dy = bottom_limit - bottom_edge
    top_edge = min(r.y() for r in work) + dy
    if top_edge < screen.y() + margin:
        dy += (screen.y() + margin) - top_edge
    if dx or dy:
        for rect in work:
            rect.translate(dx, dy)
    return work


def plan_default_placement(rects, size, screen, gap: int = WIDGET_GAP,
                           margin: int = SCREEN_MARGIN):
    """为"没有位置记忆"的新格子挑选落点（默认布局，纯逻辑可单测）。

    优先级：① 贴到右侧列的下缘（右缘对齐统一外边距）；② 右侧列放不下时，
    在整块左侧开新列、顶部与整块对齐；③ 两侧都放不下时贴左缘自上而下扫描空位。
    """
    width, height = int(size[0]), int(size[1])
    left_limit = screen.x() + margin
    right_limit = screen.x() + screen.width() - margin
    bottom_limit = screen.y() + screen.height() - margin
    top = screen.y() + margin

    x = max(left_limit, right_limit - width)
    zone = [r for r in rects if r.x() < x + width and r.x() + r.width() > x]
    if zone:
        y = max(r.y() + r.height() for r in zone) + gap
    else:
        y = top
    if y + height <= bottom_limit:
        return QPoint(x, y)

    if rects:                                  # 整块左侧开新列
        new_x = min(r.x() for r in rects) - gap - width
        new_y = min(r.y() for r in rects)
        if new_x >= left_limit:
            return QPoint(new_x, new_y)

    scan_x, scan_y = left_limit, top             # 兜底：自上而下扫描空位
    for _ in range(50):
        blocked = [r for r in rects
                   if r.x() < scan_x + width and r.x() + r.width() > scan_x
                   and r.y() < scan_y + height and r.y() + r.height() > scan_y]
        if not blocked:
            return QPoint(scan_x, scan_y)
        scan_y = max(r.y() + r.height() for r in blocked) + gap
    return QPoint(scan_x, scan_y)


class FileListWidget(QListWidget):
    """文件列表控件：支持多选，并把选中的真实文件作为拖动源拖出到资源管理器/桌面/其它格子。

    - 拖动数据使用标准 file URL（Windows 上即 CF_HDROP）。
    - 拖动结果与 Delete 键都通过回调交给外层 FileWidget 处理，控件自身不碰文件系统。
    - drag_finished_cb 在拖动结束时回调，用于刷新列表（文件被移出后立即更新）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_finished_cb = None
        self.delete_cb = None                  # Delete 键删除回调：(paths) -> None
        self.rename_cb = None                  # F2 键重命名回调：(paths) -> None
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

    def keyPressEvent(self, event):
        """Delete 键：把选中条目交给删除回调；F2 键：交给重命名回调。

        只有带真实路径（UserRole）的业务条目才参与操作；空状态下的提示条目
        没有路径，会自然被过滤掉。
        """
        if event.key() == Qt.Key_Delete and callable(self.delete_cb):
            paths = [it.data(Qt.UserRole) for it in self.selectedItems()
                     if it.data(Qt.UserRole)]
            if paths:
                self.delete_cb(paths)
                event.accept()
                return
        if event.key() == Qt.Key_F2 and callable(self.rename_cb):
            paths = [it.data(Qt.UserRole) for it in self.selectedItems()
                     if it.data(Qt.UserRole)]
            if paths:
                self.rename_cb(paths)
                event.accept()
                return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        """执行拖动；结束后回调刷新，让"拖出"的结果立即可见。

        为什么忽略基类传来的 supportedActions、只声明"移动"：
        Qt 把允许的拖放效果交给系统决定，资源管理器对跨盘拖放会按"复制"处理
        （同盘才移动）。收纳库在 D:、桌面在 C:，于是"拖出格子"实际是复制，
        源文件仍留在收纳库里 —— 格子上看起来"拖出来了却没少"，桌面上却多了
        一份副本。只允许 MoveAction 后，跨盘拖出也会真正移走源文件。
        """
        if not any(it.data(Qt.UserRole) for it in self.selectedItems()):
            return
        super().startDrag(DRAG_ACTION)
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
        """绘制对齐线（中性钢灰、1px，替代原先的霓虹蓝 2px）。"""
        if not self._lines:
            return
        painter = QPainter(self)
        pen = QPen(QColor(COLOR_ACCENT), 1)
        painter.setPen(pen)
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
                 preview_icons: bool = True,
                 snap_provider=None,
                 on_rename_grid=None, on_delete_grid=None,
                 on_new_grid=None, on_map_folder=None,
                 size=None, metrics_scale=None,
                 on_zoom_changed=None, on_size_changed=None,
                 on_cut_files=None, on_paste_files=None,
                 clipboard_count=None, guide_show=None, guide_hide=None,
                 collapsed: bool = False, on_collapse_changed=None,
                 on_tidy_layout=None, grid_key=None, parent=None):
        """初始化格子。

        :param title: 格子标题（显示名，可被用户重命名）
        :param grid_key: 配置键（位置/尺寸/缩放/预览记忆的键）；内置分类改名后
            title 变了但键必须保持原样，默认与 title 相同
        :param folder: 本格子对应的收纳文件夹
        :param on_drop: 拖入文件回调，签名 (list[str], FileWidget)
        :param on_position_changed: 窗口位置变化(防抖后)回调，签名 (FileWidget)
        :param on_preview_changed: 预览方式切换回调，签名 (bool)（图标视图=True）
        :param preview_icons: 初始预览方式（True=图标视图）
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
        :param collapsed: 是否以收起形态启动（恢复上次的收起状态）
        :param on_collapse_changed: 收起/展开回调，签名 (FileWidget, bool)
        :param on_tidy_layout: 右键「整齐排列全部格子」回调（AppContext 统一整理）
        """
        super().__init__(parent)
        self.title = title             # 显示名（用户可见，可重命名）
        self.grid_key = grid_key or title   # 配置键：位置/尺寸/缩放记忆与历史记录用
        self.folder = folder
        self.on_drop = on_drop
        self.on_position_changed = on_position_changed
        self.on_preview_changed = on_preview_changed
        self.preview_icons = preview_icons
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
        self.on_collapse_changed = on_collapse_changed
        self.on_tidy_layout = on_tidy_layout
        self._collapsed = False        # 收起状态：只剩标题栏高度
        self._expanded_height = None   # 收起前的高度（展开时还原）
        self.scale = 1.0               # 图标/列宽缩放比（1.0 = 基准 38px 图标）
        if metrics_scale is not None:
            try:
                self.scale = max(SCALE_MIN, min(SCALE_MAX,
                                                float(metrics_scale)))
            except (TypeError, ValueError):
                self.scale = 1.0
        self._init_size = self._clamp_size(size)   # 窗口尺寸记忆 (宽, 高)
        self._resizable = True         # 是否允许拖边缘调整大小（扁平搜索条关闭）
        self._bar_mode = False         # 是否"只有内容区、无标题栏"的扁平形态
        self._drag_pos = None          # 窗口拖动的起始鼠标偏移
        self._resize_edge = None       # 正在拖拽调整的边缘：right / bottom / corner
        self._resize_from = None       # 调整开始时的 (鼠标全局坐标, 宽, 高)
        self._position_ready = False   # 首次 show 之前不保存位置（避免覆盖记忆值）
        self._drag_active = False      # 是否有文件正悬停拖拽在本格子上（高亮提示）
        self._item_total = 0           # 最近一次刷新的条目总数（供标题旁计数显示）
        self._is_empty = False         # 最近一次刷新是否为空（空状态走引导布局）
        self._guide_active = False     # 列表当前是否已处于引导布局（避免重复切换）
        self._thumb_items = {}         # 待回填缩略图的条目 {(路径, 边长): QListWidgetItem}

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
        self.count_label.setStyleSheet(
            f"color:{COLOR_TEXT_DIM};background:transparent;")
        self.open_btn = QLabel("打开")
        self.open_btn.setFont(QFont("Microsoft YaHei UI", 9))
        # "打开"按钮：胶囊形文字按钮，悬停时叠一层浅底，提示可点击
        self.open_btn.setStyleSheet(
            f"QLabel{{color:{COLOR_ACCENT};background:transparent;"
            "padding:2px 7px;border-radius:7px;}"
            "QLabel:hover{background:rgba(255,255,255,44);}")
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
        self.list_widget.delete_cb = self._delete_paths     # Delete 键删除选中条目
        self.list_widget.rename_cb = self._rename_paths     # F2 键重命名选中条目
        self.list_widget.zoom_cb = self._zoom              # Ctrl+滚轮缩放
        self.list_widget.setFont(QFont("Microsoft YaHei UI", 9))
        self.list_widget.setStyleSheet(LIST_QSS)
        self.list_widget.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_menu)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.list_widget)

        # 缩略图后台解码完成：回填对应条目的图标（信号在 GUI 线程触发）
        # 用 weakref 转发：避免"全局服务 → 绑定方法 → 格子"的强引用把窗口钉住不释放
        widget_ref = weakref.ref(self)
        self._thumb_slot = (
            lambda path, size, image: _dispatch_thumb(widget_ref, path, size, image))
        get_thumbnail_service().ready.connect(self._thumb_slot)

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
        if collapsed:
            self.set_collapsed(True, notify=False)  # 恢复上次的收起形态

    # ---------- 外观绘制 ----------

    def paintEvent(self, event):
        """绘制玻璃拟态背景：渐变毛玻璃基底 + 顶部高光 + 双层描边 + 渐隐标题分隔线。

        绘制顺序（后画的压在前画的上面）决定了玻璃质感：
        1) 竖向渐变基底：上方偏亮、下方偏暗，模拟光线自上方透入玻璃；
        2) 顶部高光：标题栏高度内由浅白渐隐到全透明，形成玻璃表面反射；
        3) 外描边 + 内高光描边：深底配亮边，是"玻璃边缘"的关键；
        4) 标题分隔线：左右两端渐隐，比一条实线更精致。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 内缩半像素：让 1px 描边完整落在像素内，避免被窗口边缘裁掉一半显得发虚
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
            CORNER_RADIUS, CORNER_RADIUS)

        # 1) 毛玻璃基底：竖向渐变
        base = QLinearGradient(0, 0, 0, self.height())
        base.setColorAt(0.0, COLOR_BG)
        base.setColorAt(1.0, COLOR_BG_BOTTOM)
        painter.fillPath(path, QBrush(base))

        # 2) 顶部高光：只在标题栏附近渐隐，避免整块发灰
        glow = QLinearGradient(0, 0, 0, TITLE_BAR_HEIGHT + 26)
        glow.setColorAt(0.0, COLOR_GLOW)
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(glow))

        if self._drag_active:            # 有文件拖拽悬停：仅用中性描边强调，不用霓虹色
            painter.fillPath(path, QColor(255, 255, 255, 18))
            painter.setPen(QPen(QColor(COLOR_ACCENT), 1.5))
        else:
            painter.setPen(QPen(COLOR_BORDER, 1))
        painter.drawPath(path)

        # 3) 内高光描边：再描一圈更淡更窄的圆角线，做出玻璃的"厚度"
        inner = QPainterPath()
        inner.addRoundedRect(
            QRectF(1.5, 1.5, self.width() - 3, self.height() - 3),
            CORNER_RADIUS - 1, CORNER_RADIUS - 1)
        painter.setPen(QPen(COLOR_INNER_HIGHLIGHT, 1))
        painter.drawPath(inner)

        # 4) 标题分隔线：中间实、两端渐隐
        # 扁平形态（搜索条）没有标题栏、收起形态列表不可见：都直接跳过
        if not self._bar_mode and not self._collapsed:
            separator = QLinearGradient(12, 0, self.width() - 12, 0)
            separator.setColorAt(0.0, QColor(255, 255, 255, 0))
            separator.setColorAt(0.5, COLOR_SEPARATOR)
            separator.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(QPen(QBrush(separator), 1))
            painter.drawLine(12, TITLE_BAR_HEIGHT,
                             self.width() - 12, TITLE_BAR_HEIGHT)

    # ---------- 窗口拖动与位置记忆 ----------

    def mousePressEvent(self, event):
        """按下：优先响应右下边缘的尺寸调整，其次空白处拖动窗口。"""
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge is not None:
                self._resize_edge = edge
                self._resize_from = (event.globalPosition().toPoint(),
                                     self.x(), self.y(),
                                     self.width(), self.height())
                event.accept()
                return
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

    def mouseDoubleClickEvent(self, event):
        """双击标题栏：收起/展开格子（Fences / 桌面整理类产品的通用交互）。

        列表区域的双击属于"打开文件"，不在此处理。
        """
        if (event.button() == Qt.LeftButton and not self._bar_mode
                and event.position().y() <= TITLE_BAR_HEIGHT):
            self.set_collapsed(not self._collapsed)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """标题区（含收起形态）右键：弹出格子菜单；列表区仍由列表自身处理。"""
        if not self._bar_mode and (self._collapsed
                                   or event.pos().y() <= TITLE_BAR_HEIGHT):
            self._build_menu(QPoint(-1, -1)).exec(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    # ---------- 收起 / 展开 ----------

    def set_collapsed(self, collapsed: bool, notify: bool = True):
        """收起/展开：收起后只剩标题栏高度，展开还原收起前的高度。

        收起**不写尺寸记忆**：44px 的临时高度若被持久化，
        下次启动展开时就找不到原来的高度了。
        """
        if self._bar_mode:
            return                        # 扁平搜索条没有标题栏，不支持收起
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self._expanded_height = self.height()
            self.list_widget.hide()
            self.layout().activate()   # 先让布局排除已隐藏的列表，最小高度约束随之收紧
            self.resize(self.width(), COLLAPSED_HEIGHT)
        else:
            self.list_widget.show()
            self.layout().activate()
            height = self._expanded_height or self._init_size[1]
            self.resize(self.width(),
                        max(MIN_WIDGET_H, min(MAX_WIDGET_H, int(height))))
        self.update()                     # 重绘（收起时不画标题分隔线）
        if notify and self.on_collapse_changed is not None:
            self.on_collapse_changed(self, collapsed)

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
        """返回上下左右边缘/角落热区名称；不在热区内时返回 None。"""
        if not self._resizable or self._collapsed:
            return None                    # 扁平搜索条 / 收起形态：不允许拖边缘改尺寸
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
            # 吸附只改"被拖动的那条边"，对边必须钉住：
            # 拖右缘时 left 不动、只改 width；拖下缘时 top 不动、只改 height。
            # （若反过来改 left/top，用户会看到对面的边跟着一起动，很反常。）
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
                        width = max(MIN_WIDGET_W, edge_x - left)
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
                        height = max(MIN_WIDGET_H, edge_y - top)
                        guide_left = min(left, rect.x())
                        guide_right = max(left + width, rect.x() + rect.width())
                        guides.append(QRect(guide_left, edge_y,
                                            guide_right - guide_left, 1))
                        break
        return (left, top, width, height), guides

    def _emit_size(self):
        """尺寸防抖到期：把当前窗口宽高上报给回调（持久化）。

        收起形态的高度是临时的，不上报，避免覆盖展开时的尺寸记忆。
        """
        if self._collapsed:
            return
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
        self._guide_active = False         # 网格/紧凑布局覆盖引导布局
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
            self.list_widget.setIconSize(QSize(COMPACT_ICON_PX, COMPACT_ICON_PX))
        if self._is_empty:                 # 空状态：网格/紧凑行都会挤坏引导文字
            self._apply_guide_layout()     # 改走单列宽行（保留 gridSize 供缩放档位使用）

    def _apply_guide_layout(self):
        """空状态引导布局：换成单列宽行，避免引导文字被图标网格挤成竖排。

        图标视图的单元格仅 76px 宽，长句会被强行折成 3~4 行竖排，观感破相；
        改为列表模式后文字占满整行宽度，可正常换行显示。
        只调整视图模式与换行策略，**刻意不动 gridSize/iconSize**：
        网格尺寸是缩放档位的持久化依据，缩放切换与测试断言都依赖它保持不变。
        """
        if self._guide_active:                            # 已在引导布局：无需重复设置
            return
        self._guide_active = True
        self.list_widget.setViewMode(QListView.ListMode)
        self.list_widget.setWordWrap(True)               # 单列宽行：长提示可正常换行
        self.list_widget.setUniformItemSizes(False)      # 引导条目高度按内容自适应

    # ---------- 尺寸缩放（Ctrl+滚轮） ----------

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

    # ---------- 文件列表 ----------

    def refresh(self):
        """重新读取收纳文件夹内容并刷新列表（文件夹在前、文件在后）。"""
        self.list_widget.clear()
        self._thumb_items.clear()          # 列表已重建：旧的待回填条目全部作废
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
        was_empty = self._is_empty                 # 记录刷新前状态：由空转非空需恢复布局
        self._is_empty = (total == 0)

        if total == 0:                        # 空状态引导：主提示 + 可发现性提示
            main_tip = QListWidgetItem("拖入文件或文件夹即可收纳")
            main_tip.setFlags(Qt.NoItemFlags)
            main_tip.setTextAlignment(Qt.AlignCenter)
            main_tip.setForeground(QBrush(QColor("#8c96aa")))
            self.list_widget.addItem(main_tip)
            hint = QListWidgetItem("右键空白处：新建 / 映射 / 预览方式")
            hint.setFlags(Qt.NoItemFlags)
            hint.setTextAlignment(Qt.AlignCenter)
            hint.setForeground(QBrush(QColor(COLOR_TEXT_FAINT)))
            self.list_widget.addItem(hint)
            self._apply_guide_layout()        # 切单列宽行：引导文字不再被网格挤成竖排
            return

        if was_empty:                         # 由空转非空：撤销引导布局，回到预览方式
            self._apply_view_mode()
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
            self._set_item_icon(item, full)   # 图片走缩略图，其余走系统图标
            self.list_widget.addItem(item)
        if total > MAX_LIST_ITEMS:            # 超出上限时显示提示占位条目
            tip = QListWidgetItem(f"… 共 {total} 项，点右上角\"打开\"查看全部")
            tip.setFlags(Qt.NoItemFlags)      # 不可选中/双击
            self.list_widget.addItem(tip)

    def _set_item_icon(self, item: QListWidgetItem, path: str):
        """给条目设置图标：图片显示内容缩略图，其余显示 Windows 系统图标。

        缩略图解码在后台线程：首次遇到某张图时先放系统图标占位，解码完成后
        经 _on_thumb_ready 回填（命中缓存则立即显示）；非图片直接走 get_file_icon。
        """
        size = self._icon_px if self.preview_icons else COMPACT_ICON_PX
        if is_thumbnailable(path):
            image = get_thumbnail_service().get(path, size)
            if image is not None:
                item.setIcon(QIcon(QPixmap.fromImage(image)))
                return
            self._thumb_items[(path, size)] = item    # 等待后台解码回填
        icon = get_file_icon(path)        # 白页修复 + Shell 兜底 + 缓存
        if not icon.isNull():
            item.setIcon(icon)

    def _on_thumb_ready(self, path: str, size: int, image: QImage):
        """缩略图后台解码完成：回填对应条目的图标（列表已重建则忽略）。"""
        item = self._thumb_items.get((path, size))
        if item is None or image.isNull():
            return
        item.setIcon(QIcon(QPixmap.fromImage(image)))

    def closeEvent(self, event):
        """关闭窗口时断开缩略图服务连接，避免全局服务无限累积失效回调。"""
        try:
            get_thumbnail_service().ready.disconnect(self._thumb_slot)
        except (RuntimeError, TypeError):
            pass                               # 未连接或已断开：静默忽略
        super().closeEvent(event)

    def _update_count_label(self, total=None):
        """更新标题旁的数量标签。"""
        if total is not None:
            self._item_total = total
        if self._item_total:
            self.count_label.setText(f"{self._item_total} 项")
        else:
            self.count_label.setText("")

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
        """右键菜单：条目操作 / 粘贴 / 外观 / 格子管理 / 刷新 五个分区。

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
        # 右键菜单沿用全局玻璃配色：深底 + 浅白边 + 强调色选中块
        menu.setStyleSheet(
            ("QMenu{background:#1c1f26;color:%s;border:1px solid rgba(255,255,255,38);"
             "border-radius:10px;padding:6px;}"
             "QMenu::item{padding:6px 20px;border-radius:7px;}"
             "QMenu::item:selected{background:rgba(255,255,255,60);color:#ffffff;}"
             "QMenu::separator{height:1px;background:rgba(255,255,255,26);"
             "margin:5px 8px;}"
             ) % COLOR_TEXT
        )
        if item is not None:
            path = item.data(Qt.UserRole)
            menu.addAction("打开", lambda: open_in_shell(path))
            menu.addAction("打开所在位置",
                           lambda: open_in_shell(os.path.dirname(path)))
            paths = [it.data(Qt.UserRole) for it in self.list_widget.selectedItems()
                     if it.flags() & Qt.ItemIsEnabled and it.data(Qt.UserRole)]
            suffix = f"（{len(paths)} 项）" if len(paths) > 1 else ""
            # 重命名：与资源管理器一致，一次只处理一项（多项时置灰）
            act_rename_item = menu.addAction(
                "重命名（F2）", lambda: self._rename_paths(paths))
            act_rename_item.setEnabled(len(paths) == 1)
            if len(paths) != 1:
                act_rename_item.setToolTip("一次只能重命名一项")
            menu.addSeparator()
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
        if not self._bar_mode:              # 扁平搜索条固定单列显示，无需预览方式
            self._show_preview_menu(menu)
        menu.addAction("整齐排列全部格子",
                       lambda: self.on_tidy_layout()
                       if self.on_tidy_layout else None)
        if not self._bar_mode:
            menu.addAction("展开格子" if self._collapsed else "收起格子",
                           lambda: self.set_collapsed(not self._collapsed))
        menu.addSeparator()
        act_rename = menu.addAction("重命名格子…")
        if self.grid_key == SEARCH_GRID_TITLE:
            act_rename.setEnabled(False)
            act_rename.setToolTip("搜索格子不可重命名")
        else:
            # 内置分类同样可改名：只改显示名，分类键与文件夹保持不变
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

    def _rename_paths(self, paths):
        """就地重命名选中的文件/文件夹（与资源管理器一致：一次只能一项）。

        名称非法、目标已存在、被占用等情况弹窗提示；**绝不覆盖**已有文件。
        """
        paths = [p for p in paths if p]
        if len(paths) != 1:
            QMessageBox.information(self, "重命名", "一次只能重命名一项。")
            return
        path = paths[0]
        old_name = os.path.basename(path)
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return                       # 未改动：不碰文件系统
        try:
            rename_entry(path, new_name)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))
            return
        self.refresh()                   # 名称已变：重建列表显示新名字

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
    """桌面搜索条：平时只有"一个搜索框"的高度，输入关键词后向下展开结果。

    - 输入防抖 300ms；支持拼音搜索（organizer.search_storage 内置）；
    - 配 search_service 时搜索在后台线程执行，结果经序号+关键词双重校验回填；
      未配置 service 时退化为前台同步搜索（保持旧行为，便于测试兜底）；
    - 关键词为空或没有结果时自动收起，重新变回"只有一个搜索框"的扁平形态；
    - 结果固定用单列列表显示（行高可预测，便于据此计算展开高度）；
    - 只保留拖动与位置记忆（键名固定为"搜索"），不允许拖边缘改尺寸。
    """

    def __init__(self, on_search, *args, **kwargs):
        self._on_search = on_search        # 同步搜索入口（降级/测试用）
        self._service = kwargs.pop("search_service", None)
        self._search_ready = False
        self._search_seq = 0               # 最近一次提交给后台的请求序号
        self._result_rows = 0              # 当前展开显示的结果行数（决定窗口高度）
        super().__init__(*args, **kwargs)
        self._bar_mode = True              # 扁平形态：不画标题分隔线
        self._resizable = False            # 扁平搜索条：不允许拖边缘调整尺寸
        # 扁平化外观：隐藏标题栏、计数与"打开"按钮，只留输入框
        self.title_label.hide()
        self.count_label.hide()
        self.open_btn.hide()
        self.layout().setContentsMargins(8, 6, 8, 6)
        self.setAcceptDrops(False)         # 搜索条不接受拖入
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索收纳库（支持拼音）")
        self.search_edit.setStyleSheet(SEARCH_EDIT_QSS)
        self.layout().insertWidget(1, self.search_edit)   # 输入框放在标题栏与列表之间
        self.layout().setStretch(1, 0)     # 输入框只占自身高度，不参与纵向拉伸
        self.layout().setStretch(2, 1)     # 结果列表吃掉展开出来的空间
        if self._service is not None:
            self._service.results_ready.connect(self._on_results)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._run_search)
        self.search_edit.textChanged.connect(
            lambda _text: self._search_timer.start(300))
        self._search_ready = True
        self._apply_bar_height()           # 初始态：无结果，只有搜索框高度
        self.refresh()

    def _apply_window_size(self, width: int, height: int):
        """扁平搜索条：高度由"搜索框 + 结果行数"决定，不走通用尺寸记忆与钳制。"""
        self.resize(SEARCH_BAR_WIDTH, SEARCH_BAR_HEIGHT)

    def set_preview(self, icons: bool):
        """搜索条固定用单列列表展示结果：忽略"图标视图"，避免结果被排成网格。"""
        super().set_preview(False)

    def _bar_base_height(self) -> int:
        """纯搜索条的高度 = 上下边距 + 输入框的自然高度。"""
        margins = self.layout().contentsMargins()
        return (margins.top() + margins.bottom()
                + self.search_edit.sizeHint().height())

    def _apply_bar_height(self):
        """按结果行数决定窗口高度：无结果=只有搜索框，有结果=向下展开显示结果。"""
        rows = min(self._result_rows, SEARCH_RESULT_MAX_ROWS)
        if rows <= 0:                      # 无结果：收起列表，只剩输入框
            self.list_widget.hide()
            height = self._bar_base_height()
        else:
            self.list_widget.show()
            row_h = self.list_widget.sizeHintForRow(0)
            if row_h <= 0:                 # 列表尚未布局：用估算行高兜底
                row_h = SEARCH_RESULT_ROW_H
            margins = self.layout().contentsMargins()
            height = (self._bar_base_height() + self.layout().spacing()
                      + rows * row_h + margins.bottom())
        if self.height() != height:
            self.resize(self.width(), height)

    def _set_result_rows(self, rows: int):
        """更新结果行数并据此调整窗口高度（0 = 收起为纯搜索条）。"""
        self._result_rows = max(0, int(rows))
        self._apply_bar_height()

    def refresh(self):
        """刷新 = 用当前关键词重跑搜索（存储变动/重建格子时自动保持最新）。"""
        if not getattr(self, "_search_ready", False):
            self.list_widget.clear()
            return
        self._run_search()

    def _run_search(self):
        """执行搜索并填充结果（有后台服务时改为提交请求，结果回来再展开）。"""
        keyword = self.search_edit.text().strip()
        self.list_widget.clear()
        self._thumb_items.clear()
        self.count_label.setText("")
        if not keyword:                      # 关键词为空：收起结果区（只剩搜索框）
            self._set_result_rows(0)
            return
        if self._service is not None:
            self._search_seq = self._service.start(keyword, MAX_LIST_ITEMS)
            self._set_result_rows(0)         # 搜索中：先收起，结果回来再展开
        else:
            self._fill_results(self._on_search(keyword) or [])

    def _on_results(self, seq: int, keyword: str, results: list):
        """后台搜索完成回填；序号或关键词不匹配（已过期）则丢弃。"""
        if seq != self._search_seq or keyword != self.search_edit.text().strip():
            return
        self._fill_results(results or [])

    def _fill_results(self, results: list):
        """把搜索结果填充到列表：固定单列、一行一条，并按结果条数展开窗口。"""
        results = results or []
        self.count_label.setText(f"{len(results)} 项")
        for category, path in results[:MAX_LIST_ITEMS]:
            # 单列列表行宽裕，行尾带分类标签方便区分同名文件
            item = QListWidgetItem(f"{os.path.basename(path)}（{category}）")
            item.setData(Qt.UserRole, path)
            item.setToolTip(f"[{category}] {path}")
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            icon = get_file_icon(path)
            if not icon.isNull():
                item.setIcon(icon)
            self.list_widget.addItem(item)
        self._set_result_rows(min(len(results), MAX_LIST_ITEMS))
