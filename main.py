# -*- coding: utf-8 -*-
"""桌面分类助手 - 程序入口。

组装桌面格子、控制面板、系统托盘与 F7 全局热键，
并提供"一键整理 -> 历史记录 -> 撤销恢复"的完整链路。
v2：格子位置记忆、QFileSystemWatcher 事件驱动刷新（无轮询）、
游戏模式、开机自启（注册表 Run 键）。
"""
import ctypes
import os
import shutil
import sys
import uuid
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import (QAbstractNativeEventFilter, QFileSystemWatcher,
                            QPoint, QRect, QTimer)
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog,
                               QMenu, QMessageBox, QSystemTrayIcon)

import autostart
import config as cfg
import organizer as org
from control_panel import ControlPanel
from search_service import SearchService
from widgets import (BUILTIN_GRID_NAMES, SEARCH_GRID_TITLE, FileWidget,
                     GuideOverlay, SearchWidget)

# Windows 全局热键常量
WM_HOTKEY = 0x0312        # 热键消息编号
MOD_NOREPEAT = 0x4000     # 不重复触发标志
VK_F7, VK_F8, VK_F9 = 0x76, 0x77, 0x78                     # F7/F8/F9 虚拟键码
HOTKEY_F7, HOTKEY_F8, HOTKEY_F9 = 0xB00B, 0xB00C, 0xB00D   # 热键注册 ID

# 本入口脚本绝对路径（写注册表自启项用）
SCRIPT_PATH = os.path.abspath(__file__)

# 文件系统事件到达后的防抖刷新间隔（毫秒），合并突发改动避免频繁刷列表
REFRESH_DEBOUNCE_MS = 400

# 单实例互斥：本地套接字服务名（Windows 上对应命名管道）
SINGLE_INSTANCE_KEY = "MyDeskOrganizer.SingleInstance"

# 全局暗色 QSS：右键菜单、工具提示与弹窗对话框跟随应用主题
APP_QSS = """
QMenu{background:#26282e;color:#e8eaed;border:1px solid #3a3d46;
      border-radius:8px;padding:4px;}
QMenu::item{padding:5px 18px;border-radius:5px;}
QMenu::item:selected{background:rgba(76,194,255,70);}
QMenu::separator{height:1px;background:#3a3d46;margin:4px 6px;}
QToolTip{background:#26282e;color:#e8eaed;border:1px solid #3a3d46;
         border-radius:6px;padding:4px;}
QDialog,QMessageBox,QInputDialog{background:#1e2025;color:#e8eaed;}
QDialog QLabel,QMessageBox QLabel,QInputDialog QLabel{
      color:#e8eaed;background:transparent;}
QDialog QPushButton,QMessageBox QPushButton{background:#2a2d34;
      border:1px solid #3a3d46;border-radius:8px;padding:5px 16px;color:#e8eaed;}
QDialog QPushButton:hover,QMessageBox QPushButton:hover{
      background:#33373f;border-color:#4cc2ff;}
QDialog QPushButton:pressed{background:#23262c;}
QDialog QLineEdit,QInputDialog QLineEdit{background:#26282e;
      border:1px solid #3a3d46;border-radius:8px;padding:5px 8px;color:#e8eaed;
      selection-background-color:rgba(76,194,255,90);}
QDialog QLineEdit:focus{border-color:#4cc2ff;}
"""


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    """全局热键过滤器：在 Windows 消息循环中拦截 WM_HOTKEY 并触发对应回调。"""

    def __init__(self, callbacks: dict):
        super().__init__()
        self.callbacks = dict(callbacks)

    def nativeEventFilter(self, event_type, message):
        """Qt 原生事件钩子：仅处理 Windows 消息类型。"""
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                callback = self.callbacks.get(msg.wParam)
                if callback is not None:
                    callback()
        return False, 0


def notify_running_instance() -> bool:
    """检测是否已有实例在运行；有则请求它显示控制面板并返回 True。"""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def make_tray_icon() -> QIcon:
    """程序托盘图标：程序化绘制一个圆角方块，避免依赖外部资源文件。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(76, 194, 255))          # 主题蓝
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)   # 圆角底
    painter.setPen(QColor(255, 255, 255))
    painter.setFont(QFont("Microsoft YaHei UI", 26, QFont.Bold))
    painter.drawText(pixmap.rect(), 132, "分")       # 132 = 水平垂直居中
    painter.end()
    return QIcon(pixmap)


class AppContext:
    """应用上下文：持有配置、格子列表、文件监听器与所有跨组件回调。"""

    def __init__(self):
        self.config = cfg.load_config()
        self.widgets: list = []
        # 搜索后台服务：面板与搜索格子共享，全库遍历/拼音在后台线程执行
        # （必须在 ControlPanel 之前创建：面板构造时会连接其信号）
        self.search_service = SearchService(
            lambda: self.config.get("storageRoot", ""),
            lambda: list(self.config.get("gridPaths", {}).items()))
        self.panel = ControlPanel(self)
        self.tray = None
        self.act_game_mode = None    # 托盘"游戏模式"菜单项（供状态同步）
        self.act_auto_organize = None  # 托盘"自动整理"菜单项（供状态同步）
        self._game_mode = False
        self._widgets_hidden = False  # F7 手动隐藏状态（跨重建保留）
        self.file_clipboard: list = []  # 内部剪切板：右键"剪切"的文件路径（供跨格子粘贴）

        # 事件驱动刷新：QFileSystemWatcher 监听收纳库，改动到达后防抖刷新。
        # 无任何轮询定时器，游戏时几乎零 CPU 开销。
        self.watcher = QFileSystemWatcher()
        self.watcher.directoryChanged.connect(self._on_storage_changed)
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_pending)
        self._pending_dirs: set = set()   # 防抖窗口内累计的"发生变化的目录"

        # 自动整理：监听桌面，新文件静置一段时间（dwell）后收入收纳库；事件驱动无轮询
        self._auto_watcher = QFileSystemWatcher()
        self._auto_watcher.directoryChanged.connect(self._on_desktop_changed)
        self._auto_timer = QTimer()
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._run_auto_organize)
        self._auto_seen: set = set()   # 开启自动整理时的桌面基线（只整理之后新出现的）
        self._guide_overlay = None     # 拖动对齐指示线覆盖层（懒加载）

    # ---------- 格子管理 ----------

    def ensure_storage_dirs(self):
        """确保收纳根目录及各分类子目录存在（路径映射的格子除外）。"""
        root = self.config["storageRoot"]
        mapped = self.config.get("gridPaths", {})
        for name in self.config.get("categories", []):
            if name in mapped:
                continue
            os.makedirs(os.path.join(root, name), exist_ok=True)

    def rebuild_widgets(self):
        """按当前配置重建全部桌面格子；有记忆位置则恢复，否则平铺摆放。"""
        for w in self.widgets:
            w.close()
        self.widgets.clear()
        self.ensure_storage_dirs()

        root = self.config["storageRoot"]
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - 300          # 靠屏幕右侧竖向排列
        y = screen.top() + 40
        positions = self.config.get("widgetPositions", {})
        sizes = self.config.get("widgetSizes", {})
        scales = self.config.get("widgetScales", {})
        previews = self.config.get("widgetPreviews", {})
        mapped = self.config.get("gridPaths", {})
        locked = self.config.get("lockedWidgets", [])
        need_save_default = False         # 是否有格子的位置需要写回配置
        for name in self.config.get("categories", []):
            folder = mapped.get(name) or os.path.join(root, name)
            try:
                metrics = float(scales.get(name, 1.0))
            except (TypeError, ValueError):
                metrics = 1.0
            preview = bool(previews.get(
                name, self.config.get("previewIcons", True)))
            widget = FileWidget(
                name, folder, self.handle_drop,
                on_position_changed=self.handle_position_changed,
                on_preview_changed=self.handle_preview_changed,
                preview_icons=preview,
                locked=name in locked,
                on_lock_changed=self.handle_lock_changed,
                snap_provider=self.widget_rects,
                on_rename_grid=self.handle_rename_grid_request,
                on_delete_grid=self.handle_delete_grid_request,
                on_new_grid=self.handle_new_grid_request,
                on_map_folder=self.handle_map_folder_request,
                size=cfg.parse_position(sizes.get(name, "")),
                metrics_scale=metrics,
                on_zoom_changed=self.handle_zoom_changed,
                on_size_changed=self.handle_size_changed,
                on_cut_files=self.handle_cut_files,
                on_paste_files=self.handle_paste_files,
                clipboard_count=self.clipboard_count,
                guide_show=self.show_guides,
                guide_hide=self.hide_guides)
            saved = cfg.parse_position(positions.get(name, ""))
            if saved and self._position_visible(saved[0], saved[1],
                                                widget.width(), widget.height()):
                widget.move(QPoint(*saved))       # 恢复上次拖动后的位置
            else:
                if saved:
                    need_save_default = True      # 记忆位置已越界，回退默认布局
                # 超出屏幕高度时换一列
                if y + widget.height() > screen.bottom():
                    y = screen.top() + 40
                    x -= widget.width() + 20
                widget.move(QPoint(x, y))
                need_save_default = True
            widget.show()
            self.widgets.append(widget)
            y += widget.height() + 16

        # 桌面搜索格子：常驻桌面、可删除；位置/尺寸等记忆键名固定为“搜索”
        if self.config.get("searchGrid", False):
            name = SEARCH_GRID_TITLE
            try:
                search_metrics = float(scales.get(name, 1.0))
            except (TypeError, ValueError):
                search_metrics = 1.0
            search_widget = SearchWidget(
                self.search_all, name, root, None,
                search_service=self.search_service,
                on_position_changed=self.handle_position_changed,
                on_preview_changed=self.handle_preview_changed,
                preview_icons=bool(previews.get(
                    name, self.config.get("previewIcons", True))),
                locked=name in locked,
                on_lock_changed=self.handle_lock_changed,
                snap_provider=self.widget_rects,
                on_delete_grid=self.handle_delete_grid_request,
                size=cfg.parse_position(sizes.get(name, "")),
                metrics_scale=search_metrics,
                on_zoom_changed=self.handle_zoom_changed,
                on_size_changed=self.handle_size_changed,
                guide_show=self.show_guides,
                guide_hide=self.hide_guides)
            saved = cfg.parse_position(positions.get(name, ""))
            if saved and self._position_visible(saved[0], saved[1],
                                                search_widget.width(),
                                                search_widget.height()):
                search_widget.move(QPoint(*saved))
            else:
                search_widget.move(QPoint(screen.left() + 40,
                                          screen.top() + 40))
                need_save_default = True
            search_widget.show()
            self.widgets.append(search_widget)

        if need_save_default:
            self.save_all_positions()
        self._apply_visibility()
        self._sync_watcher_paths()

    def refresh_all(self):
        """刷新所有格子的文件列表。"""
        for w in self.widgets:
            w.refresh()

    def _refresh_pending(self):
        """防抖到期：只刷新本次文件事件真正波及的格子。

        QFileSystemWatcher 的信号携带事件源目录，防抖窗口内把目录聚合在
        _pending_dirs 里，用"目录 → 对应格子"映射做定向刷新；
        搜索格子收纳的是全库视图，只要有任何变化就重跑一次现有关键词
        （关键词为空时代价可忽略）。此前的"任一变化 → 全量刷新"会让
        路径映射的大目录格子被无关事件反复扫盘，这里一并修正。
        """
        dirs = set(self._pending_dirs)
        self._pending_dirs.clear()
        if not dirs:
            return
        for w in self.widgets:
            if w.title == SEARCH_GRID_TITLE:
                continue
            folder_norm = os.path.normcase(os.path.abspath(str(w.folder or "")))
            if folder_norm in dirs:
                w.refresh()
        search = next((w for w in self.widgets
                       if w.title == SEARCH_GRID_TITLE), None)
        if search is not None:
            search.refresh()

    def toggle_widgets(self):
        """F7 / 托盘菜单：显示或隐藏全部格子；游戏模式中按 F7 视为退出游戏模式。"""
        if self._game_mode:
            self._widgets_hidden = False
            self.set_game_mode(False)
            return
        self._widgets_hidden = not self._widgets_hidden
        self._apply_visibility()
        if not self._widgets_hidden:
            self.refresh_all()            # 重新显示时补一次刷新

    def _apply_visibility(self):
        """按"游戏模式 / 手动隐藏"状态统一设置全部格子的可见性。"""
        visible = not self._game_mode and not self._widgets_hidden
        for w in self.widgets:
            w.setVisible(visible)

    def activate(self):
        """被新的启动实例唤起：退出游戏模式/取消隐藏并显示控制面板。"""
        if self._game_mode:
            self.set_game_mode(False)
        if self._widgets_hidden:
            self._widgets_hidden = False
            self._apply_visibility()
            self.refresh_all()
        self.show_panel()

    # ---------- 位置记忆 ----------

    def handle_position_changed(self, widget: FileWidget):
        """格子拖动停止（防抖后）：把新位置写入配置并持久化。"""
        positions = self.config.setdefault("widgetPositions", {})
        positions[widget.title] = cfg.format_position(widget.x(), widget.y())
        cfg.save_config(self.config)

    def save_all_positions(self):
        """退出或首次布局时保存全部格子位置。"""
        positions = self.config.setdefault("widgetPositions", {})
        for w in self.widgets:
            positions[w.title] = cfg.format_position(w.x(), w.y())
        self.config["panelGeometry"] = cfg.format_geometry(
            self.panel.x(), self.panel.y(),
            self.panel.width(), self.panel.height())
        cfg.save_config(self.config)

    def _position_visible(self, x: int, y: int, width: int, height: int) -> bool:
        """判断记忆位置是否仍有足够面积落在某个屏幕内（防止换屏后格子不可见）。"""
        rect = QRect(int(x), int(y), int(width), int(height))
        for screen in QApplication.screens():
            inter = screen.availableGeometry().intersected(rect)
            if inter.width() >= 80 and inter.height() >= 80:
                return True
        return False

    def widget_rects(self, current: FileWidget):
        """其它可见格子的矩形列表，供拖动窗口时吸附对齐。"""
        return [QRect(w.x(), w.y(), w.width(), w.height())
                for w in self.widgets if w is not current and w.isVisible()]

    def show_guides(self, lines):
        """显示拖动对齐指示线（懒加载覆盖层）。"""
        if self._guide_overlay is None:
            self._guide_overlay = GuideOverlay()
        self._guide_overlay.show_lines(lines)

    def hide_guides(self):
        """隐藏拖动对齐指示线。"""
        if self._guide_overlay is not None:
            self._guide_overlay.hide_lines()

    def handle_lock_changed(self, widget: FileWidget, locked: bool):
        """格子切换位置锁定后写入配置持久化。"""
        locked_list = self.config.setdefault("lockedWidgets", [])
        if locked and widget.title not in locked_list:
            locked_list.append(widget.title)
        elif not locked and widget.title in locked_list:
            locked_list.remove(widget.title)
        cfg.save_config(self.config)

    # ---------- 尺寸缩放 ----------

    def handle_zoom_changed(self, widget: FileWidget, scale: float):
        """图标/列宽缩放变化（Ctrl+滚轮/右键菜单）后写入配置持久化。"""
        scales = self.config.setdefault("widgetScales", {})
        scales[widget.title] = round(float(scale), 4)
        cfg.save_config(self.config)
        self.refresh_status(
            f"「{widget.title}」图标大小已调整为 {int(round(scale * 100))}%")

    def handle_size_changed(self, widget: FileWidget):
        """窗口宽高变化（拖拽调整/恢复默认）后写入配置持久化。"""
        sizes = self.config.setdefault("widgetSizes", {})
        sizes[widget.title] = cfg.format_position(widget.width(), widget.height())
        cfg.save_config(self.config)
        self.refresh_status(
            f"「{widget.title}」窗口大小已调整为 {widget.width()}×{widget.height()}")

    # ---------- 跨格子剪切 / 粘贴 ----------

    def handle_cut_files(self, paths: list, widget: FileWidget):
        """右键"剪切"：记住待移动的条目，随后在目标格子右键"粘贴"完成移动。"""
        paths = [p for p in paths if p and os.path.exists(p)]
        self.file_clipboard = paths
        if paths:
            self.refresh_status(
                f"已剪切 {len(paths)} 项，右键目标格子选择“粘贴”完成移动")
        else:
            self.refresh_status("没有可剪切的条目")

    def handle_paste_files(self, widget: FileWidget):
        """右键"粘贴"：把剪切板里的条目移动到本格子（复用拖入的移动与历史记录）。

        被占用导致移动失败的条目保留在剪切板，可直接换个格子或稍后重新粘贴。
        """
        paths, self.file_clipboard = self.file_clipboard, []
        if not paths:
            return
        failed = self.handle_drop(paths, widget) or []
        remaining = [p for p in failed if os.path.exists(p)]
        if remaining:
            self.file_clipboard = remaining
            names = "、".join(os.path.basename(p) for p in remaining[:3])
            suffix = f" 等 {len(remaining)} 项" if len(remaining) > 3 else ""
            self.refresh_status(
                f"粘贴失败 {len(remaining)} 项（{names}{suffix}），"
                "已保留在剪切板，可换个格子或稍后重试")

    def clipboard_count(self) -> int:
        """当前剪切板条目数（供右键菜单显示与可用状态）。"""
        return len(self.file_clipboard)

    # ---------- 桌面搜索格子 ----------

    def search_all(self, keyword: str):
        """桌面搜索格子用：搜索收纳库与所有路径映射的文件夹。"""
        mapped = [(name, path)
                  for name, path in self.config.get("gridPaths", {}).items()
                  if os.path.isdir(path)]
        return org.search_storage(self.config["storageRoot"], keyword,
                                  limit=200, extra_dirs=mapped)

    def set_search_grid(self, enabled: bool):
        """显示/隐藏桌面搜索格子（右键可删除，随时可从面板重新开启）。"""
        self.config["searchGrid"] = bool(enabled)
        cfg.save_config(self.config)
        self.rebuild_widgets()
        self.panel.set_search_grid_checked(enabled)
        if enabled:
            self.refresh_status("桌面搜索格子已开启")
        else:
            self.refresh_status("搜索格子已移除（可在控制面板重新开启）")

    # ---------- 自定义格子（新建/重命名/删除） ----------

    def create_grid(self, name: str, folder: str = None):
        """新建格子：加入分类列表并重建；folder 给定时为路径映射（不建新文件夹）。"""
        self.config.setdefault("categories", []).append(name)
        if folder is not None:
            self.config.setdefault("gridPaths", {})[name] = folder
        cfg.save_config(self.config)
        self.panel.refresh_category_checks()
        self.rebuild_widgets()
        if folder is not None:
            self.refresh_status(f"已映射文件夹为格子「{name}」（{folder}）")
        else:
            self.refresh_status(f"已新建格子「{name}」")

    def create_grid_interactive(self):
        """弹出输入框新建格子（控制面板按钮与格子右键菜单共用入口）。"""
        name, ok = QInputDialog.getText(
            self.panel, "新建格子",
            "格子名称（会在收纳库中创建同名文件夹）：")
        if not ok:
            return
        error = cfg.validate_grid_name(name, self.config.get("categories", []))
        if error:
            QMessageBox.warning(self.panel, "无法新建", error)
            return
        self.create_grid(name.strip())

    def handle_new_grid_request(self, _widget: FileWidget = None):
        """格子右键"新建格子…"入口。"""
        self.create_grid_interactive()

    def map_folder_interactive(self):
        """映射任意现有文件夹为格子（文件保持原位置，不搬动）。"""
        folder = QFileDialog.getExistingDirectory(self.panel, "选择要映射的文件夹")
        if not folder:
            return
        root = self.config["storageRoot"]
        if org.is_within(folder, root):
            QMessageBox.warning(
                self.panel, "无法映射",
                "请选择收纳库以外的文件夹。\n"
                "收纳库内部的文件夹已按分类展示，无需映射。")
            return
        default_name = os.path.basename(folder.rstrip("\\/")) or "映射格子"
        name, ok = QInputDialog.getText(
            self.panel, "映射文件夹", "格子名称：", text=default_name)
        if not ok:
            return
        error = cfg.validate_grid_name(name, self.config.get("categories", []))
        if error:
            QMessageBox.warning(self.panel, "无法映射", error)
            return
        self.create_grid(name.strip(), folder=folder)

    def handle_map_folder_request(self, _widget: FileWidget = None):
        """格子右键"映射文件夹…"入口。"""
        self.map_folder_interactive()

    def rename_grid(self, old_name: str, new_name: str) -> str:
        """重命名自定义格子：同步文件夹（仅收纳库内）、各类记忆与撤销历史。

        成功返回空字符串，失败返回错误提示（不抛异常，便于 UI 直接展示）。
        路径映射的格子只改显示名称，映射的文件夹不做改动。
        """
        existing = [n for n in self.config.get("categories", []) if n != old_name]
        error = cfg.validate_grid_name(new_name, existing)
        if error:
            return error
        mapping = self.config.get("gridPaths", {})
        mapped = old_name in mapping
        root = self.config["storageRoot"]
        old_dir = os.path.join(root, old_name)
        new_dir = os.path.join(root, new_name)
        if not mapped:
            if os.path.isdir(new_dir):
                return f"收纳库中已存在同名文件夹「{new_name}」"
            if os.path.isdir(old_dir):
                try:
                    os.rename(old_dir, new_dir)
                except OSError as exc:
                    return f"重命名文件夹失败：{exc}"
        categories = self.config.get("categories", [])
        if old_name in categories:
            categories[categories.index(old_name)] = new_name
        if mapped:
            mapping[new_name] = mapping.pop(old_name)
        positions = self.config.get("widgetPositions", {})
        if old_name in positions:
            positions[new_name] = positions.pop(old_name)
        sizes = self.config.get("widgetSizes", {})
        if old_name in sizes:
            sizes[new_name] = sizes.pop(old_name)
        scales = self.config.get("widgetScales", {})
        if old_name in scales:
            scales[new_name] = scales.pop(old_name)
        previews = self.config.get("widgetPreviews", {})
        if old_name in previews:
            previews[new_name] = previews.pop(old_name)
        locked = self.config.get("lockedWidgets", [])
        if old_name in locked:
            locked[locked.index(old_name)] = new_name
        cfg.save_config(self.config)
        if not mapped:
            cfg.retarget_history_prefix(old_dir, new_dir)  # 保证历史记录仍可撤销
        self.panel.refresh_category_checks()
        self.rebuild_widgets()
        self.refresh_status(f"格子「{old_name}」已重命名为「{new_name}」")
        return ""

    def remove_grid(self, name: str):
        """从桌面删除格子（收纳库里的文件夹与文件保留，不会丢失）。"""
        categories = self.config.get("categories", [])
        if name in categories:
            categories.remove(name)
        self.config.get("widgetPositions", {}).pop(name, None)
        self.config.get("widgetSizes", {}).pop(name, None)
        self.config.get("widgetScales", {}).pop(name, None)
        self.config.get("widgetPreviews", {}).pop(name, None)
        self.config.get("gridPaths", {}).pop(name, None)
        locked = self.config.get("lockedWidgets", [])
        if name in locked:
            locked.remove(name)
        cfg.save_config(self.config)
        self.panel.refresh_category_checks()
        self.rebuild_widgets()
        self.refresh_status(f"已删除格子「{name}」，文件保留在收纳库中")

    def handle_rename_grid_request(self, widget: FileWidget):
        """格子右键"重命名格子…"入口：弹输入框并执行重命名。"""
        if widget.title in BUILTIN_GRID_NAMES:
            QMessageBox.information(
                self.panel, "重命名格子",
                f"「{widget.title}」是内置分类（参与自动整理），不能重命名。\n"
                "可以在控制面板取消勾选来隐藏它。")
            return
        new_name, ok = QInputDialog.getText(
            self.panel, "重命名格子", "新名称：", text=widget.title)
        if not ok:
            return
        new_name = new_name.strip()
        if new_name == widget.title:
            return
        error = self.rename_grid(widget.title, new_name)
        if error:
            QMessageBox.warning(self.panel, "无法重命名", error)

    def handle_delete_grid_request(self, widget: FileWidget):
        """格子右键"删除此格子"入口：确认后仅移除格子，不动收纳库文件。"""
        name = widget.title
        if name == SEARCH_GRID_TITLE:
            answer = QMessageBox.question(
                self.panel, "删除搜索格子",
                "移除桌面搜索格子？\n可随时在控制面板勾选“桌面搜索格子”恢复。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self.set_search_grid(False)
            return
        hint = "（内置分类，之后可在控制面板重新勾选恢复）" \
            if name in BUILTIN_GRID_NAMES else ""
        answer = QMessageBox.question(
            self.panel, "删除格子",
            f"从桌面删除「{name}」格子{hint}？\n"
            "收纳库中的文件夹和文件都会保留，不会丢失。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.remove_grid(name)

    # ---------- 预览方式 ----------

    def handle_preview_changed(self, widget: FileWidget, icons: bool):
        """单个格子切换预览方式：只作用于该格子并持久化。"""
        self.config.setdefault("widgetPreviews", {})[widget.title] = bool(icons)
        cfg.save_config(self.config)
        mode = "图标视图" if icons else "紧凑列表"
        self.refresh_status(f"「{widget.title}」预览方式已切换为{mode}")

    def set_preview_all(self, icons: bool):
        """面板下拉框：所有格子统一预览方式，并作为新格子的默认值。"""
        self.config["previewIcons"] = icons
        self.config.setdefault("widgetPreviews", {}).clear()
        cfg.save_config(self.config)
        for w in self.widgets:
            if w.preview_icons != icons:
                w.set_preview(icons)
        self.refresh_status("预览方式已应用到全部格子")

    # ---------- 开机自启 ----------

    def set_autostart(self, enabled: bool):
        """写入/删除注册表自启项并记录到配置。"""
        autostart.set_autostart(SCRIPT_PATH, enabled)
        self.config["autostart"] = enabled
        cfg.save_config(self.config)

    # ---------- 游戏模式 ----------

    def set_game_mode(self, enabled: bool, notify: bool = True):
        """游戏模式：隐藏全部格子与面板、暂停文件监听，几乎零资源占用。

        关闭时恢复显示、重新挂载监听并立即刷新一次；
        notify=False 用于"启动时恢复上次状态"，避免重复弹托盘提示。
        """
        self._game_mode = enabled
        self.config["gameMode"] = enabled
        cfg.save_config(self.config)
        self._apply_visibility()
        if enabled:
            self._refresh_timer.stop()                            # 停止待执行的刷新
            self.watcher.removePaths(self.watcher.directories())  # 暂停监听
            self._auto_timer.stop()                               # 自动整理同样暂停
            if notify and self.tray is not None:
                self.tray.showMessage(
                    "游戏模式已开启",
                    "格子已隐藏、文件监听已暂停。按 F7 或托盘菜单可退出。",
                    QSystemTrayIcon.Information, 2000)
        else:
            self._sync_watcher_paths()
            self.refresh_all()
        self._sync_auto_watcher()
        self.panel.set_game_mode_checked(enabled)
        if self.act_game_mode is not None:
            self.act_game_mode.setChecked(enabled)

    def toggle_game_mode(self):
        """F9 / 托盘菜单：在游戏模式开/关之间切换。"""
        self.set_game_mode(not self._game_mode)

    # ---------- 文件监听（事件驱动，无轮询） ----------

    def _sync_watcher_paths(self):
        """让监听路径与"收纳根目录+各分类子目录"保持一致（增删差量更新）。"""
        if self._game_mode:
            return                       # 游戏模式下保持零监听，退出时再统一挂载
        root = self.config["storageRoot"]
        mapped = self.config.get("gridPaths", {})
        wanted = [root]
        for name in self.config.get("categories", []):
            path = mapped.get(name) or os.path.join(root, name)
            if os.path.isdir(path) and path not in wanted:
                wanted.append(path)
        current = self.watcher.directories()
        added = [p for p in wanted if p not in current]
        removed = [p for p in current if p not in wanted]
        if added:
            self.watcher.addPaths(added)
        if removed:
            self.watcher.removePaths(removed)

    def _on_storage_changed(self, path: str):
        """收纳库目录有改动（整理/拖入/外部增删）：防抖后定向刷新受影响格子。"""
        if self._game_mode or self._widgets_hidden:
            return                       # 游戏模式/隐藏期间不做无谓刷新
        self._pending_dirs.add(os.path.normcase(str(path)))
        self._refresh_timer.start(REFRESH_DEBOUNCE_MS)
        # 防抖期间可能新建了分类目录，刷新时顺带补挂监听
        QTimer.singleShot(REFRESH_DEBOUNCE_MS + 50, self._sync_watcher_paths)

    # ---------- 自动整理新桌面文件（事件驱动 + 静置等待，默认关闭） ----------

    def set_auto_organize(self, enabled: bool):
        """开关自动整理：开启后只整理"开启之后新出现"的桌面文件，旧文件不动。"""
        self.config["autoOrganize"] = bool(enabled)
        cfg.save_config(self.config)
        if enabled:
            self._auto_seen = set(self._list_desktop())   # 以当前桌面为基线
            self._sync_auto_watcher()
            delay = int(self.config.get("autoOrganizeDelay", 300))
            self.refresh_status(f"自动整理已开启（新文件静置 {delay} 秒后收纳）")
        else:
            self._auto_timer.stop()
            self._sync_auto_watcher()
            self.refresh_status("自动整理已关闭")
        self.panel.set_auto_organize_checked(enabled)     # 面板与托盘同步，不触发回调
        if self.act_auto_organize is not None:
            self.act_auto_organize.setChecked(enabled)

    def set_auto_organize_delay(self, seconds: int):
        """设置自动整理前的静置等待时间（秒）。"""
        self.config["autoOrganizeDelay"] = int(seconds)
        cfg.save_config(self.config)
        self.refresh_status(f"自动整理静置时间已设为 {int(seconds)} 秒")

    def _list_desktop(self):
        """列出桌面顶层条目名（失败返回空列表）。"""
        try:
            return os.listdir(org.get_desktop_path())
        except OSError:
            return []

    def _sync_auto_watcher(self):
        """让桌面监听与"开关 + 游戏模式"保持一致。"""
        active = bool(self.config.get("autoOrganize", False)) and \
            not self._game_mode
        desktop = org.get_desktop_path()
        current = self._auto_watcher.directories()
        if active and os.path.isdir(desktop):
            if desktop not in current:
                self._auto_watcher.addPaths([desktop])
                self._auto_seen = set(self._list_desktop())  # 开始监听时以当前桌面为基线
        elif current:
            self._auto_watcher.removePaths(current)

    def _on_desktop_changed(self, _path: str):
        """桌面有变化：等新文件静置一段时间（dwell）再整理，避免搬动下载中的文件。"""
        if self._game_mode or not self.config.get("autoOrganize", False):
            return
        delay = max(5, int(self.config.get("autoOrganizeDelay", 300)))
        self._auto_timer.start(delay * 1000)

    def _run_auto_organize(self):
        """静置计时结束：把开启自动整理之后新出现的桌面条目收纳归位。"""
        if self._game_mode or not self.config.get("autoOrganize", False):
            return
        current = set(self._list_desktop())
        new_names = current - self._auto_seen
        self._auto_seen = current
        if not new_names:
            return
        records = org.organize_desktop(
            org.get_desktop_path(), self.config["storageRoot"],
            include_shortcuts=bool(self.config.get("includeShortcuts", False)),
            include_folders=bool(self.config.get("includeFolders", True)),
            only_names=new_names)
        if records:
            cfg.append_records([r.to_dict() for r in records])
            self.refresh_all()
            self.refresh_status(f"自动整理：已收纳 {len(records)} 个新文件/文件夹")
            self._auto_seen = set(self._list_desktop())

    # ---------- 业务回调（供控制面板调用） ----------

    def on_organize(self, count: int):
        """一键整理完成后的刷新与状态提示。"""
        self.refresh_all()
        self.panel.status_label.setText(f"已整理 {count} 个文件/文件夹")

    def on_undo(self, restored: int, skipped: int):
        """撤销/恢复完成后的刷新与状态提示。"""
        self.refresh_all()
        msg = f"已恢复 {restored} 个文件到原位置"
        if skipped:
            msg += f"（{skipped} 个跳过：源位置或目标已变化）"
        self.panel.status_label.setText(msg)

    def on_widgets_rebuild(self):
        """配置变化后重建格子并提示。"""
        self.rebuild_widgets()
        self.panel.status_label.setText("设置已保存，格子已重建")

    def refresh_status(self, text: str):
        """仅更新面板状态栏文字。"""
        self.panel.status_label.setText(text)

    # ---------- 拖拽收纳 ----------

    def handle_drop(self, paths: list, widget: FileWidget) -> list:
        """把拖入格子的文件/文件夹移动到该格子对应的分类文件夹并记录历史。

        :returns: 移动失败的路径列表（全部成功时为空）。供"粘贴"保留失败项用。
        历史记录每批只落盘一次（拖入 50 个文件 = 1 次写入，而不是 50 次）。
        """
        batch = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        records, failed = [], []
        for src in paths:
            if org.is_within(src, widget.folder):
                continue                  # 从本格子拖回自身：静默忽略，不计失败
            try:
                dst = org.move_into(src, widget.folder)
            except (OSError, shutil.Error, ValueError):
                failed.append(src)
                continue
            records.append(org.MoveRecord(
                src=src, dst=dst, batch=batch, time=now,
            ).to_dict())
        if records:
            cfg.append_records(records)   # 整批一次写入，避免逐文件重写历史
            widget.refresh()
        if failed:
            names = "、".join(os.path.basename(p) for p in failed[:3])
            extra = f" 等 {len(failed)} 项" if len(failed) > 3 else ""
            self.refresh_status(
                f"收纳到「{widget.title}」：成功 {len(records)} 项，"
                f"失败 {len(failed)} 项（{names}{extra}，可能被占用）")
        elif records:
            self.refresh_status(f"已收纳 {len(records)} 项到「{widget.title}」")
        return failed

    def show_panel(self):
        """显示并激活控制面板。"""
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()


def main():
    """程序入口：初始化 Qt 应用、注册热键、构建托盘与格子。"""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关闭面板不退出，常驻托盘
    app.setApplicationName("桌面分类助手")
    app.setStyleSheet(APP_QSS)

    # 单实例：已有实例在运行时，唤起它的控制面板后本进程直接退出
    if notify_running_instance():
        return

    ctx = AppContext()

    # 单实例服务：接收后续启动实例的"唤起"请求
    server = QLocalServer(app)
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)  # 清理上次异常退出可能残留的占位
    server.listen(SINGLE_INSTANCE_KEY)

    def _on_new_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(conn.readAll)
            conn.disconnected.connect(conn.deleteLater)
        ctx.activate()

    server.newConnection.connect(_on_new_connection)

    # 注册全局热键：F7 显隐格子 / F8 控制面板 / F9 游戏模式（失败时托盘提示）
    hotkey_filter = None
    hotkeys_ok = {}
    hotkeys_failed = []
    if sys.platform == "win32":
        for hk_id, vk, label, callback in (
                (HOTKEY_F7, VK_F7, "F7 显示/隐藏格子", ctx.toggle_widgets),
                (HOTKEY_F8, VK_F8, "F8 控制面板", ctx.show_panel),
                (HOTKEY_F9, VK_F9, "F9 游戏模式", ctx.toggle_game_mode)):
            if ctypes.windll.user32.RegisterHotKey(None, hk_id, MOD_NOREPEAT, vk):
                hotkeys_ok[hk_id] = callback
            else:
                hotkeys_failed.append(label)
        if hotkeys_ok:
            hotkey_filter = GlobalHotkeyFilter(hotkeys_ok)
            app.installNativeEventFilter(hotkey_filter)

    # 系统托盘
    tray = QSystemTrayIcon(make_tray_icon())
    menu = QMenu()
    act_panel = QAction("控制面板 (F8)", menu)
    act_panel.triggered.connect(ctx.show_panel)
    act_toggle = QAction("显示/隐藏格子 (F7)", menu)
    act_toggle.triggered.connect(ctx.toggle_widgets)
    act_game = QAction("游戏模式 (F9)", menu, checkable=True)
    act_game.triggered.connect(
        lambda checked: ctx.set_game_mode(checked))
    act_auto = QAction("自动整理新桌面文件", menu, checkable=True)
    act_auto.setChecked(ctx.config.get("autoOrganize", False))
    act_auto.triggered.connect(
        lambda checked: ctx.set_auto_organize(checked))
    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    for act in (act_panel, act_toggle, act_game, act_auto):
        menu.addAction(act)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip(f"桌面分类助手 v{cfg.APP_VERSION}")
    # 左键单击 / 双击托盘图标 = 打开控制面板（符合大多数托盘应用的习惯）
    tray.activated.connect(
        lambda reason: ctx.show_panel()
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
        else None)
    tray.show()
    ctx.tray = tray
    ctx.act_game_mode = act_game
    ctx.act_auto_organize = act_auto

    if hotkeys_failed:
        tray.showMessage("部分热键不可用",
                         "、".join(hotkeys_failed) + " 可能被其它程序占用，可从托盘菜单操作。",
                         QSystemTrayIcon.Warning, 3000)

    # 构建格子（自动恢复上次位置）；若上次处于游戏模式则保持隐藏
    ctx.rebuild_widgets()
    if ctx.config.get("gameMode", False):
        ctx.set_game_mode(True, notify=False)   # 启动恢复不重复弹提示
    else:
        ctx.show_panel()
    if ctx.config.get("autoOrganize", False) and not ctx._game_mode:
        ctx.set_auto_organize(True)             # 恢复自动整理（重新建立桌面基线）

    app.aboutToQuit.connect(ctx.save_all_positions)
    exit_code = app.exec()
    for hk_id in hotkeys_ok:
        ctypes.windll.user32.UnregisterHotKey(None, hk_id)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
