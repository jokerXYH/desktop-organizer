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
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import (QAbstractNativeEventFilter,
                            QFileSystemWatcher, QRect, Qt, QTimer)
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QMenu,
                               QMessageBox, QSystemTrayIcon)

import autostart
import config as cfg
import crash_guard
import instance_guard
import layout_store
import organizer as org
import shell_menu
import shortcut
import theme
import updater
import win_compat
from control_panel import ControlPanel
from search_service import SearchService
from widgets import (BUILTIN_GRID_NAMES, SEARCH_GRID_TITLE, WIDGET_GAP,
                     FileWidget, GuideOverlay, SearchWidget,
                     clamp_rect_to_screens, current_screen_rects,
                     plan_default_placement, plan_tidy_layout)

# Windows 全局热键常量
WM_HOTKEY = 0x0312        # 热键消息编号
MOD_NOREPEAT = 0x4000     # 不重复触发标志
VK_F7, VK_F8, VK_F9 = 0x76, 0x77, 0x78                     # F7/F8/F9 虚拟键码
HOTKEY_F7, HOTKEY_F8, HOTKEY_F9 = 0xB00B, 0xB00C, 0xB00D   # 热键注册 ID

# 本入口脚本绝对路径（写注册表自启项用）；打包成 exe 后由 exe 自身启动，
# 此时 __file__ 是临时解包目录（退出即删），写入自启项会指向不存在的脚本
SCRIPT_PATH = "" if getattr(sys, "frozen", False) else os.path.abspath(__file__)

# 文件系统事件到达后的防抖刷新间隔（毫秒），合并突发改动避免频繁刷列表
REFRESH_DEBOUNCE_MS = 400

# 自动整理的稳定性确认与失败重试（v2.7.1：修复"收走还在写入的文件"与
# "移动失败后被基线吞掉、永久遗漏"两类问题）
AUTO_CONFIRM_MS = 2000         # 静置到期后还需稳定不变的确认窗口（毫秒）
AUTO_MAX_CONFIRM_ROUNDS = 45   # 条目持续变化的观察轮数上限（约 90 秒后放弃）
AUTO_RETRY_MS = 30000          # 移动失败（被占用）后的重试间隔（毫秒）
AUTO_MAX_ATTEMPTS = 5          # 同一条目的最大自动重试次数
AUTO_DUP_GUARD_S = 900         # 同名条目重复出现的保护窗口（秒，15 分钟）
AUTO_DIR_SCAN_CAP = 500        # 文件夹稳定性快照的递归条目数上限（防超大目录拖慢确认轮）

# 单实例互斥：本地套接字服务名（Windows 上对应命名管道）
SINGLE_INSTANCE_KEY = "MyDeskOrganizer.SingleInstance"

# 全局菜单/弹窗跟随瓷白主题；格子与面板有各自的专用 QSS。
APP_QSS = theme.DIALOG_QSS


def _norm_dir(path) -> str:
    """目录路径归一化键：绝对化 + 大小写归一（Windows 路径不敏感）。

    监听回调拿到的路径与格子记录的用户路径可能大小写/尾分隔符不同，
    两侧必须用同一把"尺子"归一，否则比对不上导致漏刷新（见 _refresh_pending）。
    """
    return os.path.normcase(os.path.abspath(str(path or "")))


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


def notify_running_instance(message: bytes = b"show") -> bool:
    """检测是否已有实例在运行；有则发送请求并返回 True。

    消息含义：b"show" = 显示控制面板（默认）；b"organize" = 执行一键整理
    （桌面右键菜单进入时使用，见 cli_action）。

    登录瞬间两个启动项同时拉起时，第一个实例可能还没完成 listen。这里做
    几次短重试，避免第二个进程"既没抢到锁、又没唤起旧实例"后静默退出。
    """
    for attempt in range(5):
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(300):
            socket.write(message)
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
        socket.abort()
        if attempt < 4:
            time.sleep(0.15)
    return False


def cli_action(argv) -> str:
    """从命令行参数解析本次启动意图："organize"（一键整理）或 "show"（默认唤起面板）。"""
    return "organize" if "--organize" in list(argv) else "show"


def update_wait_pid(argv):
    """解析 --updated-from <pid>：自更新接力时新实例需要等待的旧进程 PID。"""
    args = list(argv)
    if "--updated-from" not in args:
        return None
    try:
        return int(args[args.index("--updated-from") + 1])
    except (IndexError, ValueError):
        return None


def make_tray_icon() -> QIcon:
    """程序托盘图标：墨色圆角方块 + 深青描边，避免依赖外部资源文件。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(theme.PALETTE["rail"]))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(4, 4, 56, 56, 16, 16)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(theme.PALETTE["jade"]), 2))
    painter.drawRoundedRect(7, 7, 50, 50, 13, 13)
    painter.setPen(QColor(theme.PALETTE["rail_text"]))
    painter.setFont(QFont(theme.FONT_FAMILY, 26, QFont.DemiBold))
    painter.drawText(pixmap.rect(), 132, "分")
    painter.end()
    return QIcon(pixmap)


class AppContext:
    """应用上下文：持有配置、格子列表、文件监听器与所有跨组件回调。"""

    def __init__(self):
        self.config = cfg.load_config()
        self.widgets: list = []
        # 路径映射快照锁：搜索在后台线程读 gridPaths，主线程可能同时在增删映射，
        # 直接迭代同一个 dict 会抛 "dictionary changed size during iteration"，
        # 被后台线程的宽 except 吞成"空结果"。用锁保护这一次浅拷贝。
        self._grid_lock = threading.Lock()
        # 搜索后台服务：面板与搜索格子共享，全库遍历/拼音在后台线程执行
        # （必须在 ControlPanel 之前创建：面板构造时会连接其信号）
        self.search_service = SearchService(
            lambda: self.config.get("storageRoot", ""),
            self._snapshot_grid_dirs)
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
        # 稳定性确认与重试状态（v2.7.1 两段式自动整理）
        self._auto_stable: dict = {}   # 待确认条目 {名称: (文件大小或目录条目数, mtime)}
        self._auto_rounds: dict = {}   # 条目的连续确认轮数（防"永远在变化"死循环）
        self._auto_attempts: dict = {}  # 移动失败条目的已尝试次数 {名称: 次数}
        self._auto_collected: dict = {}  # 近期已自动收纳的条目 {名称: 时间戳}（同名重复保护）
        self._auto_confirm_timer = QTimer()
        self._auto_confirm_timer.setSingleShot(True)
        self._auto_confirm_timer.timeout.connect(self._confirm_and_collect)
        self._auto_retry_timer = QTimer()
        self._auto_retry_timer.setSingleShot(True)
        self._auto_retry_timer.timeout.connect(self._confirm_and_collect)
        self._guide_overlay = None     # 拖动对齐指示线覆盖层（懒加载）

        # 显示器增删 / 分辨率变化的防抖计时器：变化后把全部格子收回屏幕内，
        # 防止拔掉副屏、改分辨率之后格子"留在屏幕外面"找不回来。
        self._screen_timer = QTimer()
        self._screen_timer.setSingleShot(True)
        self._screen_timer.timeout.connect(self.reclamp_widgets)
        # 登录后显示器枚举可能比 Explorer 晚 1–3 秒；再补一次延迟恢复，
        # 让副屏上的格子在系统稳定后自动回位。
        self._screen_settle_timer = QTimer()
        self._screen_settle_timer.setSingleShot(True)
        self._screen_settle_timer.timeout.connect(self._delayed_layout_restore)
        self._watch_screen_changes()

    # ---------- 格子管理 ----------

    def _snapshot_grid_dirs(self) -> list:
        """线程安全地返回路径映射快照 [(分类名, 目录), ...]。

        供搜索后台线程调用：在锁内做一次浅拷贝，避免与主线程的增删映射
        并发迭代同一个 dict（否则抛 RuntimeError 被吞成"空搜索"）。
        """
        with self._grid_lock:
            return list(self.config.get("gridPaths", {}).items())

    def ensure_storage_dirs(self):
        """确保收纳根目录及各分类子目录存在（路径映射的格子除外）。

        单个目录创建失败（U 盘拔出、盘符不可用、权限不足）只跳过该目录，
        不能让整次重建/启动失败。
        """
        root = self.config["storageRoot"]
        mapped = self.config.get("gridPaths", {})
        for name in self.config.get("categories", []):
            if name in mapped:
                continue
            try:
                os.makedirs(os.path.join(root, name), exist_ok=True)
            except OSError as exc:
                crash_guard.log_message(
                    f"创建收纳目录失败 {name}: {exc}", "WARN")

    def rebuild_widgets(self):
        """按当前配置重建全部桌面格子。

        布局恢复优先使用显示器感知的 widgetLayouts：副屏暂未就绪时先临时
        放在当前可见屏幕，但保留目标坐标，绝不把临时位置写回配置。
        """
        for w in self.widgets:
            w.close()
        self.widgets.clear()
        self.ensure_storage_dirs()

        root = self.config["storageRoot"]
        primary = QApplication.primaryScreen()
        screen = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 800)
        screens = QApplication.screens()
        positions = self.config.setdefault("widgetPositions", {})
        layouts = self.config.setdefault("widgetLayouts", {})
        sizes = self.config.setdefault("widgetSizes", {})
        scales = self.config.get("widgetScales", {})
        previews = self.config.get("widgetPreviews", {})
        mapped = self.config.get("gridPaths", {})
        titles = self.config.get("gridTitles", {})   # 显示名记忆：{分类键: 显示名}
        collapsed_map = self.config.get("widgetCollapsed", {})
        need_save_default = False         # 是否有格子的位置需要写回配置
        placed_rects: list = []           # 已落位格子的矩形：新格子据此避开重叠
        pending: list = []                # 没有有效记忆位置/副屏未就绪的格子
        restored: list = []               # 已按真实屏幕恢复的格子：检查重叠

        def apply_resolution(widget, resolution):
            """把恢复方案应用到窗口；返回是否需要保存布局。"""
            widget._layout_desired_rect = QRect(resolution.desired)
            widget._layout_provisional = bool(resolution.provisional)
            if resolution.rect is None:
                pending.append(widget)
                return False
            target = QRect(resolution.rect.x(), resolution.rect.y(),
                           widget.width(), widget.height())
            target = clamp_rect_to_screens(target, current_screen_rects())
            widget.setGeometry(target)
            placed_rects.append(target)
            if not resolution.provisional:
                restored.append(widget)
            return bool(resolution.changed) and not resolution.provisional

        for name in self.config.get("categories", []):
            folder = mapped.get(name) or os.path.join(root, name)
            display = titles.get(name, name)   # 内置分类改名后：键不变，只换显示名
            try:
                metrics = float(scales.get(name, 1.0))
            except (TypeError, ValueError):
                metrics = 1.0
            preview = bool(previews.get(
                name, self.config.get("previewIcons", True)))
            widget = FileWidget(
                display, folder, self.handle_drop,
                on_position_changed=self.handle_position_changed,
                on_preview_changed=self.handle_preview_changed,
                preview_icons=preview,
                snap_provider=self.widget_rects,
                on_rename_grid=self.handle_rename_grid_request,
                on_delete_grid=self.handle_delete_grid_request,
                on_new_grid=self.handle_new_grid_request,
                on_map_folder=self.handle_map_folder_request,
                size=self._stored_widget_size(name, sizes, layouts),
                metrics_scale=metrics,
                on_zoom_changed=self.handle_zoom_changed,
                on_size_changed=self.handle_size_changed,
                on_cut_files=self.handle_cut_files,
                on_paste_files=self.handle_paste_files,
                clipboard_count=self.clipboard_count,
                guide_show=self.show_guides,
                guide_hide=self.hide_guides,
                collapsed=bool(collapsed_map.get(name, False)),
                on_collapse_changed=self.handle_collapse_changed,
                on_tidy_layout=self.tidy_layout,
                on_organize_desktop=self.organize_now,
                grid_key=name)
            resolution = self._resolve_saved_geometry(widget, name, positions)
            if resolution is None:
                pending.append(widget)
                need_save_default = True
            else:
                need_save_default = apply_resolution(
                    widget, resolution) or need_save_default
            if name not in layouts:
                need_save_default = True     # 旧配置首次启动：立即补写屏幕感知记录
            self.widgets.append(widget)

        # 桌面搜索条：扁平形态（只有搜索框高度）、常驻桌面、可删除；位置记忆键名固定为"搜索"
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
                snap_provider=self.widget_rects,
                on_delete_grid=self.handle_delete_grid_request,
                metrics_scale=search_metrics,
                on_zoom_changed=self.handle_zoom_changed,
                guide_show=self.show_guides,
                guide_hide=self.hide_guides,
                on_tidy_layout=self.tidy_layout,
                on_organize_desktop=self.organize_now)
            resolution = self._resolve_saved_geometry(
                search_widget, name, positions)
            if resolution is None:
                pending.append(search_widget)
                need_save_default = True
            else:
                need_save_default = apply_resolution(
                    search_widget, resolution) or need_save_default
            if name not in layouts:
                need_save_default = True
            self.widgets.append(search_widget)

        # 修复历史数据里的明显重叠：只处理超过较小窗口 40% 的重叠。
        if len(restored) > 1:
            rects = [QRect(w.x(), w.y(), w.width(), w.height())
                     for w in restored]
            resolved = layout_store.resolve_overlaps(
                rects, screens, gap=WIDGET_GAP)
            for widget, rect in zip(restored, resolved):
                if rect == widget.geometry():
                    continue
                widget.setGeometry(rect)
                need_save_default = True

        # 无记忆位置或副屏未就绪的格子：统一在主屏排队落位，避开已恢复的格子。
        for widget in pending:
            anchored = [rect for rect in placed_rects
                        if screen.intersects(rect)]
            point = plan_default_placement(
                anchored, (widget.width(), widget.height()), screen)
            widget.move(point)
            placed_rects.append(QRect(point.x(), point.y(),
                                      widget.width(), widget.height()))
            if not widget._layout_provisional:
                need_save_default = True

        for widget in self.widgets:
            widget.show()

        if need_save_default:
            self.save_all_positions()
        self._apply_visibility()
        self._sync_watcher_paths()

    def _stored_widget_size(self, key, sizes, layouts):
        """窗口尺寸优先用 widgetSizes；只有新布局记录时回退到其中的 w/h。"""
        parsed = cfg.parse_position(sizes.get(key, ""))
        if parsed:
            return parsed
        record = layouts.get(key)
        if isinstance(record, dict):
            try:
                return int(record["w"]), int(record["h"])
            except (KeyError, TypeError, ValueError):
                return None
        return None

    def _resolve_saved_geometry(self, widget, key, positions):
        """读取新布局记录；旧配置则用 widgetPositions 构造兼容记录。"""
        record = self.config.get("widgetLayouts", {}).get(key)
        if layout_store.record_rect(record) is None:
            saved = cfg.parse_position(positions.get(key, ""))
            if not saved:
                return None
            record = {
                "x": saved[0],
                "y": saved[1],
                "w": widget.width(),
                "h": widget.height(),
            }
        return layout_store.restore_layout(record, QApplication.screens())

    @staticmethod
    def _widget_screen(widget):
        """窗口中心所在屏幕；极端情况下回退主屏。"""
        return (QApplication.screenAt(widget.geometry().center())
                or QApplication.primaryScreen())

    def _save_widget_layout(self, widget, positions=None) -> bool:
        """保存格子当前的显示器感知布局；临时布局不写回。"""
        if getattr(widget, "_layout_provisional", False):
            return False
        screen = self._widget_screen(widget)
        layouts = self.config.setdefault("widgetLayouts", {})
        layouts[widget.grid_key] = layout_store.make_layout_record(
            widget.geometry(), screen)
        widget._layout_desired_rect = QRect(widget.geometry())
        if positions is not None:
            positions[widget.grid_key] = cfg.format_position(
                widget.x(), widget.y())
        return True

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
            folder_norm = _norm_dir(w.folder)
            if folder_norm in dirs:
                w.refresh()
        search = next((w for w in self.widgets
                       if w.title == SEARCH_GRID_TITLE), None)
        if search is not None:
            search.refresh()

    # ---------- 外观对齐（整齐排列 / 收起） ----------

    def tidy_layout(self):
        """整齐排列：把可见格子整理为统一 16px 间距、边缘对齐的规整栅格。

        只调整几何（位置 + 容差内统一的尺寸），不触碰文件；同屏格子按屏分组、
        各自整理；已收起的格子只参与对位并锁定尺寸（展开高度记忆不被覆盖）。
        """
        groups = {}
        for widget in self.widgets:
            if not widget.isVisible():
                continue
            screen = QApplication.screenAt(widget.geometry().center()) \
                or QApplication.primaryScreen()
            groups.setdefault(screen, []).append(widget)
        if not groups:
            self.refresh_status("没有可见的格子可整理")
            return

        changed = 0
        for screen, screen_widgets in groups.items():
            rects = [QRect(w.x(), w.y(), w.width(), w.height())
                     for w in screen_widgets]
            fixed = [bool(getattr(w, "_collapsed", False))
                     for w in screen_widgets]
            plan = plan_tidy_layout(
                rects, screen.availableGeometry(), fixed=fixed)
            for widget, rect in zip(screen_widgets, plan):
                if widget.geometry() != rect:
                    widget.setGeometry(rect)
                    changed += 1

        positions = self.config.setdefault("widgetPositions", {})
        sizes = self.config.setdefault("widgetSizes", {})
        for widget in self.widgets:
            if not widget.isVisible():
                continue
            widget._layout_provisional = False
            self._save_widget_layout(widget, positions)
            if not getattr(widget, "_collapsed", False):
                sizes[widget.grid_key] = cfg.format_position(
                    widget.width(), widget.height())
        cfg.save_config(self.config)
        if changed:
            self.refresh_status(
                f"已整齐排列 {changed} 个格子（间距统一 {WIDGET_GAP}px）")
        else:
            self.refresh_status("格子已经排列整齐，无需调整")

    def handle_collapse_changed(self, widget, collapsed: bool):
        """收起/展开状态变化：写入配置（跨重建保留）。"""
        self.config.setdefault(
            "widgetCollapsed", {})[widget.grid_key] = bool(collapsed)
        cfg.save_config(self.config)
        self.refresh_status(
            f"「{widget.title}」{'已收起' if collapsed else '已展开'}")

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
        widget._layout_provisional = False
        positions = self.config.setdefault("widgetPositions", {})
        self._save_widget_layout(widget, positions)
        cfg.save_config(self.config)

    def save_all_positions(self):
        """退出或首次布局时保存全部格子位置。

        副屏暂时缺失的格子处于 provisional 状态：保留原目标坐标，跳过写入，
        避免一次开机把副屏位置永久覆盖成主屏位置。
        """
        positions = self.config.setdefault("widgetPositions", {})
        for w in self.widgets:
            self._save_widget_layout(w, positions)
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

    # ---------- 屏幕变化守卫 ----------

    def _watch_screen_changes(self):
        """监听显示器增删/分辨率变化：任何变化都把格子收回屏幕并写回配置。"""
        app = QApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)
        for screen in app.screens():
            self._hook_screen(screen)
        app.screenAdded.connect(self._hook_screen)

    def _hook_screen(self, screen):
        """订阅单个屏幕的几何变化（分辨率、任务栏、DPI 变动都会触发）。"""
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.availableGeometryChanged.connect(self._on_screens_changed)

    def _on_screens_changed(self, *_args):
        """屏幕变化信号可能成串到达：防抖后统一处理，避免连续重排。"""
        self._screen_timer.start(400)
        self._screen_settle_timer.start(2500)

    def _delayed_layout_restore(self):
        """登录后显示器枚举较慢时补一次恢复，副屏出现后把格子送回原位。"""
        restored = self._restore_provisional_widgets()
        self.reclamp_widgets()
        if restored:
            self.refresh_status(f"副屏已就绪，{restored} 个格子已回到保存位置")

    def _restore_provisional_widgets(self, screens=None) -> int:
        """副屏出现后回放 provisional 格子；返回真正回到保存位置的格子数。"""
        screens = list(screens or QApplication.screens())
        layouts = self.config.setdefault("widgetLayouts", {})
        positions = self.config.setdefault("widgetPositions", {})
        restored = 0
        for widget in self.widgets:
            if not getattr(widget, "_layout_provisional", False):
                continue
            resolution = layout_store.restore_layout(
                layouts.get(widget.grid_key), screens)
            if (resolution is None or resolution.provisional
                    or resolution.rect is None):
                continue
            target = QRect(resolution.rect.x(), resolution.rect.y(),
                           widget.width(), widget.height())
            target = clamp_rect_to_screens(
                target, [screen.availableGeometry() for screen in screens])
            widget.setGeometry(target)
            widget._layout_provisional = False
            widget._layout_desired_rect = QRect(resolution.desired)
            self._save_widget_layout(widget, positions)
            restored += 1
        if restored:
            cfg.save_config(self.config)
        return restored

    def reclamp_widgets(self) -> int:
        """把所有格子收进屏幕可见区域（屏幕变化自动触发；托盘菜单也能手动触发）。

        返回被移动的格子数量；修正后的位置会写回配置。
        """
        screens = list(QApplication.screens())
        self._restore_provisional_widgets(screens)
        screen_rects = [screen.availableGeometry() for screen in screens]
        positions = self.config.setdefault("widgetPositions", {})
        layouts = self.config.setdefault("widgetLayouts", {})
        moved = 0
        for widget in self.widgets:
            if getattr(widget, "_layout_provisional", False):
                continue
            rect = QRect(widget.x(), widget.y(),
                         widget.width(), widget.height())
            resolution = layout_store.restore_layout(
                layouts.get(widget.grid_key), screens)
            if resolution is not None and resolution.provisional:
                # 目标屏暂时缺失：保留当前位置与目标坐标，等屏幕回来再回放。
                widget._layout_provisional = True
                widget._layout_desired_rect = QRect(resolution.desired)
                continue
            if resolution is not None and resolution.rect is not None:
                target = QRect(resolution.rect.x(), resolution.rect.y(),
                               widget.width(), widget.height())
                target = clamp_rect_to_screens(target, screen_rects)
            else:
                target = clamp_rect_to_screens(rect, screen_rects)
            if target == rect:
                continue
            widget.setGeometry(target)
            self._save_widget_layout(widget, positions)
            moved += 1
        if moved:
            cfg.save_config(self.config)
            self.refresh_status(f"已把 {moved} 个格子移回屏幕内")
        return moved

    def request_quit(self):
        """控制面板「退出助手」：保存几何后退出（位置由 aboutToQuit 统一保存）。"""
        self.panel.save_geometry()
        QApplication.instance().quit()

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

    # ---------- 尺寸缩放 ----------

    def handle_zoom_changed(self, widget: FileWidget, scale: float):
        """图标/列宽缩放变化（Ctrl+滚轮）后写入配置持久化。"""
        scales = self.config.setdefault("widgetScales", {})
        scales[widget.grid_key] = round(float(scale), 4)
        cfg.save_config(self.config)
        self.refresh_status(
            f"「{widget.title}」图标大小已调整为 {int(round(scale * 100))}%")

    def handle_size_changed(self, widget: FileWidget):
        """窗口宽高变化（拖拽边缘调整）后写入配置持久化。"""
        sizes = self.config.setdefault("widgetSizes", {})
        sizes[widget.grid_key] = cfg.format_position(widget.width(),
                                                     widget.height())
        positions = self.config.setdefault("widgetPositions", {})
        self._save_widget_layout(widget, positions)
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
            with self._grid_lock:      # 与搜索后台线程的快照读取互斥
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
        error = cfg.validate_grid_name(
            name, self.config.get("categories", []) + [SEARCH_GRID_TITLE])
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
        error = cfg.validate_grid_name(
            name, self.config.get("categories", []) + [SEARCH_GRID_TITLE])
        if error:
            QMessageBox.warning(self.panel, "无法映射", error)
            return
        self.create_grid(name.strip(), folder=folder)

    def handle_map_folder_request(self, _widget: FileWidget = None):
        """格子右键"映射文件夹…"入口。"""
        self.map_folder_interactive()

    def rename_grid(self, old_name: str, new_name: str) -> str:
        """重命名格子：内置分类只改显示名，自定义格子改名并同步文件夹与历史。

        成功返回空字符串，失败返回错误提示（不抛异常，便于 UI 直接展示）。

        为什么内置分类不能改文件夹/分类键：`classify()` 按扩展名只会返回
        「文档/图片/…/其他」这些固定名字，自动整理据此定位目标文件夹。若把
        「文档」的键改成别的名字，自动整理就会找不到格子而停止收纳文档。
        因此内置分类的键与文件夹保持不变，只在 gridTitles 里改显示名。
        """
        titles = self.config.setdefault("gridTitles", {})
        # 用户可见名字是"显示名"，重名判断必须按显示名来（键可能与显示名不同）；
        # "搜索"是搜索条的保留名，不允许被占用
        existing = [titles.get(n, n) for n in self.config.get("categories", [])
                    if n != old_name] + [SEARCH_GRID_TITLE]
        error = cfg.validate_grid_name(new_name, existing)
        if error:
            return error
        root = self.config["storageRoot"]
        if old_name in BUILTIN_GRID_NAMES:
            titles[old_name] = new_name       # 只改显示名：键/文件夹/记忆/历史全不动
            cfg.save_config(self.config)
            self.rebuild_widgets()
            self.refresh_status(f"格子「{old_name}」已重命名为「{new_name}」")
            return ""
        mapping = self.config.get("gridPaths", {})
        mapped = old_name in mapping
        old_dir = os.path.join(root, old_name)
        new_dir = os.path.join(root, new_name)
        if not mapped:                        # 非映射的自定义格子：文件夹跟着改名
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
            with self._grid_lock:      # 与搜索后台线程的快照读取互斥
                mapping[new_name] = mapping.pop(old_name)
        positions = self.config.get("widgetPositions", {})
        if old_name in positions:
            positions[new_name] = positions.pop(old_name)
        layouts = self.config.get("widgetLayouts", {})
        if old_name in layouts:
            layouts[new_name] = layouts.pop(old_name)
        sizes = self.config.get("widgetSizes", {})
        if old_name in sizes:
            sizes[new_name] = sizes.pop(old_name)
        scales = self.config.get("widgetScales", {})
        if old_name in scales:
            scales[new_name] = scales.pop(old_name)
        previews = self.config.get("widgetPreviews", {})
        if old_name in previews:
            previews[new_name] = previews.pop(old_name)
        titles.pop(old_name, None)            # 自定义格子的键即显示名，无需另存
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
        self.config.get("widgetLayouts", {}).pop(name, None)
        self.config.get("widgetSizes", {}).pop(name, None)
        self.config.get("widgetScales", {}).pop(name, None)
        self.config.get("widgetPreviews", {}).pop(name, None)
        with self._grid_lock:          # 与搜索后台线程的快照读取互斥
            self.config.get("gridPaths", {}).pop(name, None)
        cfg.save_config(self.config)
        self.panel.refresh_category_checks()
        self.rebuild_widgets()
        self.refresh_status(f"已删除格子「{name}」，文件保留在收纳库中")

    def handle_rename_grid_request(self, widget: FileWidget):
        """格子右键"重命名格子…"入口：弹输入框并执行重命名（内置分类也可改名）。"""
        current = widget.title                 # 当前显示名（可能已被改过）
        new_name, ok = QInputDialog.getText(
            self.panel, "重命名格子", "新名称：", text=current)
        if not ok:
            return
        new_name = new_name.strip()
        if new_name == current:
            return
        error = self.rename_grid(widget.grid_key, new_name)
        if error:
            QMessageBox.warning(self.panel, "无法重命名", error)

    def handle_delete_grid_request(self, widget: FileWidget):
        """格子右键"删除此格子"入口：确认后仅移除格子，不动收纳库文件。"""
        name = widget.grid_key                 # 配置键：删除要按键删，不能用显示名
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
            f"从桌面删除「{widget.title}」格子{hint}？\n"
            "收纳库中的文件夹和文件都会保留，不会丢失。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.remove_grid(name)

    # ---------- 预览方式 ----------

    def handle_preview_changed(self, widget: FileWidget, icons: bool):
        """单个格子切换预览方式：只作用于该格子并持久化。"""
        self.config.setdefault("widgetPreviews", {})[widget.grid_key] = bool(icons)
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
        """写入/删除注册表自启项并记录到配置。

        注册表是自启的唯一真源；配置写入失败只记日志，不能反过来让
        已经写好的注册表项被误报为失败（否则 UI 会回滚勾选、状态不一致）。
        """
        autostart.set_autostart(
            SCRIPT_PATH, enabled, extra_args=[autostart.AUTOSTART_ARG])
        self.config["autostart"] = enabled
        try:
            cfg.save_config(self.config)
        except OSError as exc:
            crash_guard.log_message(f"保存自启配置失败：{exc}", "WARN")
        crash_guard.log_message(f"开机自启已{'开启' if enabled else '关闭'}")

    def set_shell_menu(self, enabled: bool):
        """添加/移除桌面右键菜单项（HKCU 注册表；失败抛 OSError 由面板提示）。"""
        if enabled:
            shell_menu.register(SCRIPT_PATH)
        else:
            shell_menu.unregister()
        self.config["shellMenu"] = bool(enabled)
        cfg.save_config(self.config)
        self.refresh_status("已添加桌面右键菜单：一键整理桌面"
                            if enabled else "已移除桌面右键菜单")

    # ---------- 快捷启动 ----------

    def create_shortcuts(self) -> list:
        """在桌面与开始菜单生成启动快捷方式，返回创建成功的 .lnk 路径列表。"""
        return shortcut.create_launch_shortcuts()

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
            self._auto_confirm_timer.stop()   # 待确认条目保留在内存，退出后继续
            self._auto_retry_timer.stop()
            if notify and self.tray is not None:
                self.tray.showMessage(
                    "游戏模式已开启",
                    "格子已隐藏、文件监听已暂停。按 F7 或托盘菜单可退出。",
                    QSystemTrayIcon.Information, 2000)
        else:
            self._sync_watcher_paths()
            self.refresh_all()
            if self._auto_stable:             # 有待确认条目：恢复稳定性确认
                self._auto_confirm_timer.start(AUTO_CONFIRM_MS)
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
        self._pending_dirs.add(_norm_dir(path))     # 与 _refresh_pending 用同一归一化
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
            self._auto_stable.clear()
            self._auto_rounds.clear()
            self._auto_attempts.clear()
            self._load_auto_collected()   # 从历史恢复同名保护名单（跨重启）
            self._sync_auto_watcher()
            delay = int(self.config.get("autoOrganizeDelay", 300))
            self.refresh_status(f"自动整理已开启（新文件静置 {delay} 秒后收纳）")
        else:
            self._auto_timer.stop()
            self._auto_confirm_timer.stop()
            self._auto_retry_timer.stop()
            self._auto_stable.clear()
            self._auto_rounds.clear()
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
                self._auto_seen -= set(self._auto_stable)    # 待确认条目仍算"新"，不吞掉
                self._load_auto_collected()  # 重挂监听时刷新同名保护名单（含历史恢复）
        elif current:
            self._auto_watcher.removePaths(current)

    def _on_desktop_changed(self, _path: str):
        """桌面有变化：等新文件静置一段时间（dwell）再整理，避免搬动下载中的文件。"""
        if self._game_mode or not self.config.get("autoOrganize", False):
            return
        delay = max(5, int(self.config.get("autoOrganizeDelay", 300)))
        self._auto_timer.start(delay * 1000)

    @staticmethod
    def _count_tree(root: str, cap: int) -> int:
        """统计目录树内条目总数；一旦达到 cap 立即返回（表示"未扫完/仍在增长"）。

        只数不计内容，os.walk 逐层 yield，命中上限即提前结束，
        因此超大文件夹的代价也被压在 cap 级别。
        """
        total = 0
        for _cur, dirs, files in os.walk(root):
            total += len(dirs) + len(files)
            if total >= cap:
                return cap
        return total

    def _stat_entry(self, name: str):
        """取桌面条目的稳定性快照；不存在返回 None。

        文件=(大小, mtime_ns)，文件夹=(递归条目数, mtime_ns)。
        两点关键设计：
        1) 文件夹必须**递归**计数。拷贝/解压程序往子目录里逐层落文件时，
           顶层条目数与顶层目录 mtime 都不变，只统计顶层会把"半拷贝"的
           文件夹误判为已稳定而整体搬走，释放方随后按原路径重建 →
           收纳库堆出副本（v2.7.0 副本根因）。
        2) 用 mtime_ns 而非 int(st_mtime)：秒级截断会把同一秒内的
           最后一次写入抹平，同样制造"假稳定"。
        """
        try:
            path = os.path.join(org.get_desktop_path(), name)
            stat = os.stat(path)
        except OSError:
            return None
        if os.path.isdir(path):
            return (self._count_tree(path, AUTO_DIR_SCAN_CAP), stat.st_mtime_ns)
        return (stat.st_size, stat.st_mtime_ns)

    def _forget_pending(self, name: str):
        """把条目移出自动整理的待确认/观察表（收纳成功、放弃或跳过时统一清理）。"""
        self._auto_stable.pop(name, None)
        self._auto_rounds.pop(name, None)

    def _load_auto_collected(self):
        """从撤销历史恢复"近期已自动收纳"名单，让同名保护跨重启生效。

        释放源（网盘同步等）反复释放同名条目时，应用重启会丢掉内存里的
        保护名单，导致再次照单全收。历史记录里每条移动都带时间戳，
        天然是持久化凭据：guard 窗口内收纳过的名字一律跳过。

        用记录的 src（原桌面名）而不是 dst 取名字：收纳库已有同名条目时
        dst 会被改成"名 (2)"，与桌面上真实再现的名字对不上，保护就失效了。
        """
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(seconds=AUTO_DUP_GUARD_S)
        collected = {}
        for rec in cfg.load_history():
            try:
                moved_at = datetime.fromisoformat(rec.get("time", ""))
            except ValueError:
                continue
            if moved_at < cutoff:
                continue
            src = rec.get("src", "")
            if src:
                name = os.path.basename(src)
                ts = moved_at.timestamp()
                if collected.get(name, 0) < ts:
                    collected[name] = ts
        for name, ts in self._auto_collected.items():   # 与内存记录合并取新
            if collected.get(name, 0) < ts:
                collected[name] = ts
        self._auto_collected = collected

    def _run_auto_organize(self):
        """静置计时结束：新条目先进入稳定性确认，不再立即搬运。

        旧实现在此直接 shutil.move，撞上"还在写入/解压"的条目时：搬走后
        写入方按路径重建目录继续写 → 桌面反复重现同名文件夹 → 收纳库堆出
        "(2)(3)"副本；而移动失败（被占用）的条目又被基线吞掉，永远不会再
        重试（表现为"PDF 遗漏分类"）。因此改为两段式：
        静置到期 → 快照 → 确认窗口内无变化才真正收纳（见 _confirm_and_collect）。
        """
        if self._game_mode or not self.config.get("autoOrganize", False):
            return
        current = set(self._list_desktop())
        new_names = current - self._auto_seen
        pending = set(self._auto_stable) | new_names

        # 还在写入中的临时名（浏览器 .crdownload 等）：不收也不进基线，
        # 等写入方改成最终文件名后自然会作为新条目正常收纳
        pending = {n for n in pending if not org.is_in_progress_name(n)}

        # 同名重复保护：刚收纳过的名字短时间再现，多半是某个程序在反复释放
        # （v2.6 曾出现同一文件夹被反复收录出 14 份副本）；跳过并提示手动处理
        now = time.time()
        for name in [n for n, t in self._auto_collected.items()
                     if now - t >= AUTO_DUP_GUARD_S]:
            self._auto_collected.pop(name, None)        # 顺带清理过期记录
        guarded = {n for n in pending
                   if now - self._auto_collected.get(n, 0) < AUTO_DUP_GUARD_S}
        if guarded:
            pending -= guarded
            names = "、".join(sorted(guarded)[:3])
            suffix = f" 等 {len(guarded)} 项" if len(guarded) > 3 else ""
            self.refresh_status(
                f"「{names}」{suffix}在 {AUTO_DUP_GUARD_S // 60} 分钟内重复出现在桌面，"
                "已跳过自动收纳（可能是某个程序在反复释放，请手动处理）")

        # 基线更新：当前桌面上既不在待确认、也不是写入中临时名的都算"旧"
        in_progress_now = {n for n in current if org.is_in_progress_name(n)}
        self._auto_seen |= (current - pending - in_progress_now)

        # 进入稳定性确认窗口
        snaps = {name: self._stat_entry(name) for name in pending}
        self._auto_stable = {n: s for n, s in snaps.items() if s is not None}
        if self._auto_stable:
            self._auto_confirm_timer.start(AUTO_CONFIRM_MS)

    def _confirm_and_collect(self):
        """稳定性确认到期（或失败重试到期）：只收纳确认窗口内未变化的条目。

        仍在变化的条目刷新快照继续观察（观察轮数有上限，防"永远在变化"）；
        移动失败（被占用）的条目进入有界重试：最多 AUTO_MAX_ATTEMPTS 次，
        超过则基线化并提示手动整理，不再无限循环。
        """
        if self._game_mode or not self.config.get("autoOrganize", False):
            return
        if not self._auto_stable:
            return
        stable, churning = [], []
        for name, snap in list(self._auto_stable.items()):
            current = self._stat_entry(name)
            if current is None:
                self._forget_pending(name)          # 消失了（改名/手动处理）
            elif current == snap:
                stable.append(name)
            else:
                churning.append(name)               # 还在变化，继续观察

        for name in churning:
            rounds = self._auto_rounds.get(name, 0) + 1
            if rounds >= AUTO_MAX_CONFIRM_ROUNDS:
                self._forget_pending(name)          # 观察上限已到：放弃该条目
                self._auto_seen.add(name)
                self.refresh_status(
                    f"「{name}」持续变化超过 {AUTO_MAX_CONFIRM_ROUNDS} 轮观察，"
                    "已跳过自动收纳，请手动处理")
            else:
                self._auto_rounds[name] = rounds
                self._auto_stable[name] = self._stat_entry(name)
        if churning and self._auto_stable:
            self._auto_confirm_timer.start(AUTO_CONFIRM_MS)  # 继续下一轮观察

        if not stable:
            self._auto_seen |= set(self._list_desktop()) - set(self._auto_stable)
            return

        records, failed, skipped = org.organize_desktop(
            org.get_desktop_path(), self.config["storageRoot"],
            include_shortcuts=bool(self.config.get("includeShortcuts", False)),
            include_folders=bool(self.config.get("includeFolders", True)),
            only_names=set(stable), skip_in_progress=True, return_failed=True,
            enabled_categories=set(self.config.get("categories", [])))

        # skipped = 因收录开关关闭而有意留在桌面的条目：撤出待确认，但**不**记入
        # 已收纳名单、也**不**入基线 —— 否则它会被当成"已处理"而在用户之后
        # 打开开关时永久不再收纳（旧实现把它们混进 set(stable)-set(failed)）。
        for name in skipped:
            self._forget_pending(name)

        # 只有真正搬运成功的条目才记入同名保护；用记录里的 src 原名，
        # 不要用"确认时还在待确认"的名字（可能已在缝隙里消失）。
        moved = {os.path.basename(rec.src) for rec in records}
        now = time.time()
        for name in moved:
            self._forget_pending(name)
            self._auto_attempts.pop(name, None)
            self._auto_collected[name] = now       # 记录时间戳，供同名重复保护
        for name in set(stable) - moved - set(failed) - set(skipped):
            self._forget_pending(name)             # 搬运前一刻消失，停止跟踪

        if failed:
            retry, give_up = [], []
            for name in failed:
                attempts = self._auto_attempts.get(name, 0) + 1
                if attempts >= AUTO_MAX_ATTEMPTS:
                    give_up.append(name)
                    self._auto_attempts.pop(name, None)
                    self._forget_pending(name)   # 移出待确认，停止重试
                    self._auto_seen.add(name)      # 放弃重试：入基线，不再打扰
                else:
                    self._auto_attempts[name] = attempts
                    fresh = self._stat_entry(name)
                    if fresh is not None:
                        self._auto_stable[name] = fresh
                        retry.append(name)
                    else:
                        self._auto_seen.add(name)  # 重试间隙里消失了
            if retry:
                self._auto_retry_timer.start(AUTO_RETRY_MS)
            if give_up:
                names = "、".join(give_up[:3])
                suffix = f" 等 {len(give_up)} 项" if len(give_up) > 3 else ""
                self.refresh_status(
                    f"自动整理已放弃 {len(give_up)} 项（{names}{suffix}，"
                    "多次移动失败可能被占用），请手动整理")

        if records:
            cfg.append_records([r.to_dict() for r in records])
            self.refresh_all()
            self.refresh_status(f"自动整理：已收纳 {len(records)} 个新文件/文件夹")
        # 基线 = 当前桌面 \ 待确认 \ 被开关跳过的（后两类仍是"新"，
        # 待确认等下一轮、被跳过的等开关打开后有机会再收）
        self._auto_seen |= (set(self._list_desktop())
                            - set(self._auto_stable) - set(skipped))

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
        """显示并激活控制面板（最小化时先还原，F8/托盘唤起才可靠）。"""
        self.panel.showNormal()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def organize_now(self):
        """一键整理桌面（桌面右键菜单 / 托盘 / 格子右键共用面板同一流程）。

        先显示控制面板（预览确认弹窗以面板为父窗口，保证可见与居中）；
        整理流程本身完全复用 control_panel._do_organize：
        尊重「整理前先预览确认」开关、写历史、可撤销。
        """
        self.show_panel()
        QTimer.singleShot(60, self.panel._do_organize)


def main():
    """程序入口：初始化 Qt 应用、注册热键、构建托盘与格子。"""
    # 第一步就装崩溃日志：后面任何一步（配置、图形驱动、自启）出问题都能留痕。
    crash_guard.install()
    # 任务栏/通知归属 ID 必须在创建 QApplication 与任何窗口前设置。
    win_compat.set_app_user_model_id()

    # 自更新接力：新版本 exe 先等旧进程退出、清理 *.old，随后正常启动
    # （apply_update 会把旧 exe 改名为 *.old 再放新文件，见 updater.py）
    wait_pid = update_wait_pid(sys.argv)
    if wait_pid is not None:
        updater.wait_for_process_exit(wait_pid)
        if getattr(sys, "frozen", False):
            updater.cleanup_old_exe(sys.executable)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关闭面板不退出，常驻托盘
    app.setApplicationName("桌面分类助手")
    app.setStyleSheet(APP_QSS)

    action = cli_action(sys.argv)

    # 先抢进程锁，再创建任何窗口：登录时两个启动项同时拉起也不会各建一套格子。
    instance_lock = instance_guard.acquire(
        os.path.join(cfg.CONFIG_DIR, "app.lock"))
    if instance_lock is None:
        notified = notify_running_instance(
            b"organize" if action == "organize" else b"show")
        crash_guard.log_message(
            f"已有实例运行，唤醒{'成功' if notified else '失败（旧实例可能仍在启动）'}",
            "WARN" if not notified else "INFO")
        return

    # 已开启自启时自动修正过期路径，并补上 --autostart 参数。
    try:
        if autostart.sync_autostart(SCRIPT_PATH):
            crash_guard.log_message("已修正过期的开机自启路径")
    except Exception as exc:
        crash_guard.log_message(f"自启路径同步失败：{exc}", "WARN")

    is_autostart = autostart.AUTOSTART_ARG in sys.argv
    try:
        ctx = AppContext()
    except Exception:
        crash_guard.log_exception("AppContext 初始化失败")
        QMessageBox.critical(
            None, "桌面分类助手启动失败",
            "初始化时发生错误，已写入日志：\n"
            f"{crash_guard.log_path()}\n\n"
            "请把该日志发给开发者，或删除配置目录后重试。")
        return

    # 单实例服务：接收后续启动实例的"唤起"请求
    server = QLocalServer(app)
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)  # 清理上次异常退出可能残留的占位
    server.listen(SINGLE_INSTANCE_KEY)

    def _on_new_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        state = {"handled": False}

        def _dispatch(data: bytes):
            state["handled"] = True
            if b"organize" in data:
                ctx.organize_now()     # 桌面右键菜单：一键整理桌面
            else:
                ctx.activate()         # 常规唤起：显示控制面板

        def _deliver(*_args):
            if state["handled"]:
                return
            data = bytes(conn.readAll())
            if data:
                _dispatch(data)

        def _fallback():
            # 数据迟迟未到（异常客户端/系统抖动）：按"唤起面板"兜底，确保一定有响应
            if state["handled"]:
                return
            try:
                data = bytes(conn.readAll())
            except RuntimeError:           # 连接对象已销毁
                data = b""
            _dispatch(data)

        conn.readyRead.connect(_deliver)
        conn.disconnected.connect(_deliver)    # 断开兜底：缓冲区可能仍有数据
        conn.disconnected.connect(conn.deleteLater)
        QTimer.singleShot(300, _fallback)

    server.newConnection.connect(_on_new_connection)

    # 桌面右键菜单已注册但指向的程序不存在（换过目录/形态）：自动改写到当前路径
    try:
        shell_menu.refresh_if_stale(SCRIPT_PATH)
    except OSError:
        pass

    # 注册全局热键：F7 显隐格子 / F8 控制面板 / F9 游戏模式（失败时托盘提示）
    hotkey_filter = None
    hotkeys_ok = {}
    hotkeys_failed = []
    if sys.platform == "win32":
        for hk_id, vk, label, callback in (
                (HOTKEY_F7, VK_F7, "F7 显示/隐藏格子", ctx.toggle_widgets),
                (HOTKEY_F8, VK_F8, "F8 控制面板", ctx.show_panel),
                (HOTKEY_F9, VK_F9, "F9 游戏模式", ctx.toggle_game_mode)):
            try:
                registered = bool(ctypes.windll.user32.RegisterHotKey(
                    None, hk_id, MOD_NOREPEAT, vk))
            except Exception:
                registered = False
            if registered:
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
    act_organize = QAction("一键整理桌面", menu)
    act_organize.triggered.connect(ctx.organize_now)
    act_toggle = QAction("显示/隐藏格子 (F7)", menu)
    act_toggle.triggered.connect(ctx.toggle_widgets)
    act_tidy = QAction("整齐排列格子", menu)
    act_tidy.triggered.connect(ctx.tidy_layout)
    act_rescue = QAction("把所有格子移回屏幕内", menu)
    act_rescue.triggered.connect(ctx.reclamp_widgets)
    act_game = QAction("游戏模式 (F9)", menu, checkable=True)
    act_game.triggered.connect(
        lambda checked: ctx.set_game_mode(checked))
    act_auto = QAction("自动整理新桌面文件", menu, checkable=True)
    act_auto.setChecked(ctx.config.get("autoOrganize", False))
    act_auto.triggered.connect(
        lambda checked: ctx.set_auto_organize(checked))
    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    for act in (act_panel, act_organize, act_toggle, act_tidy, act_rescue,
                act_game, act_auto):
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
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()
        # 首次运行弹一次托盘提示：用户反馈"不知道程序在运行、找不到退出"，
        # 明确告诉出口在哪（之后不再打扰，配置里记住）
        if not ctx.config.get("trayTipShown", False):
            tray.showMessage(
                "桌面分类助手已在系统托盘运行",
                "左键图标 = 控制面板；右键菜单里有「退出」。\n关闭面板不会退出程序，按 F8 可随时重新打开。",
                QSystemTrayIcon.Information, 6000)
            ctx.config["trayTipShown"] = True
            cfg.save_config(ctx.config)
        ctx.tray = tray
    else:
        # 托盘不可用（罕见环境）：关闭面板=退出，保证用户始终有明确的出口
        app.setQuitOnLastWindowClosed(True)
        ctx.tray = None
        QMessageBox.warning(
            None, "系统托盘不可用",
            "当前系统没有可用的通知区域（系统托盘），程序无法常驻运行。\n\n"
            "此时关闭控制面板将直接退出程序；请通过桌面快捷方式再次启动。")
    ctx.act_game_mode = act_game
    ctx.act_auto_organize = act_auto

    if hotkeys_failed and ctx.tray is not None:
        tray.showMessage("部分热键不可用",
                         "、".join(hotkeys_failed) + " 可能被其它程序占用，可从托盘菜单操作。",
                         QSystemTrayIcon.Warning, 3000)

    def _start_widgets():
        """构建格子并恢复状态；自启模式下不主动弹控制面板。"""
        try:
            ctx.rebuild_widgets()
        except Exception:
            crash_guard.log_exception("构建桌面格子失败")
            if ctx.tray is not None:
                ctx.tray.showMessage(
                    "桌面格子加载失败",
                    f"详情已写入日志：{crash_guard.log_path()}",
                    QSystemTrayIcon.Warning, 6000)
            return
        if ctx.config.get("gameMode", False):
            ctx.set_game_mode(True, notify=False)   # 启动恢复不重复弹提示
        elif not is_autostart:
            ctx.show_panel()
        if ctx.config.get("autoOrganize", False) and not ctx._game_mode:
            ctx.set_auto_organize(True)             # 重新建立桌面基线
        if action == "organize":                    # 桌面右键菜单启动：进入后即整理
            QTimer.singleShot(120, ctx.organize_now)

    # Win10 登录后显示器枚举可能晚于 Run 项：自启时稍等桌面就绪，
    # 避免副屏尚未出现就把格子临时落在主屏。2 秒是 Explorer 任务栏/托盘
    # 基本就绪的保守值；屏幕就绪后 _screen_settle_timer 还会再校准一次。
    if is_autostart:
        QTimer.singleShot(2000, _start_widgets)
    else:
        _start_widgets()

    def _safe_save_positions():
        """退出/关机落盘：配置目录不可写时只记日志，不让退出流程崩溃。"""
        try:
            ctx.save_all_positions()
        except Exception:
            crash_guard.log_exception("保存格子布局失败")

    app.aboutToQuit.connect(_safe_save_positions)
    # Windows 注销/关机时先保存一次布局（不是所有关机都会走 aboutToQuit）。
    commit_signal = getattr(app, "commitDataRequest", None)
    if commit_signal is not None:
        commit_signal.connect(lambda _manager: _safe_save_positions())
    exit_code = app.exec()
    instance_lock.unlock()
    for hk_id in hotkeys_ok:
        try:
            ctypes.windll.user32.UnregisterHotKey(None, hk_id)
        except Exception:
            pass
    crash_guard.log_message("正常退出")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
