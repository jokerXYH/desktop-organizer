# -*- coding: utf-8 -*-
"""控制面板窗口：一键整理、撤销、恢复、设置项、搜索与各类开关。

面板不直接持有业务对象，通过 AppContext 回调与主程序交互，
保证逻辑层可独立测试。
v2：新增开机自启、游戏模式、预览方式切换、收纳库搜索与暗色美化。
"""
import html
import os
import subprocess
import sys
import tempfile
import webbrowser

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

import autostart
import config as cfg
import organizer as org
import shell_menu
import theme
import updater
from widgets import COLOR_ACCENT, COLOR_TEXT, open_in_shell

PANEL_WIDTH = 1080   # 面板宽度（左侧导航 + 右侧内容）
PANEL_HEIGHT = 720   # 面板高度
SEARCH_DEBOUNCE_MS = 300          # 搜索输入防抖
SEARCH_RESULT_LIMIT = 100         # 搜索结果上限（与 organizer.search_storage 一致）

PANEL_QSS = theme.PANEL_QSS


class GlassCheckBox(QCheckBox):
    """玻璃拟态勾选框：自绘圆角指示器（选中=钢灰填充 + 深色对勾）。

    为什么不用 QSS 直接改 ::indicator：QSS 只能替换指示器的底色/边框，
    画不出对勾（对勾需要外部图片资源）；为免引入图片依赖，这里用一个
    极轻量的自绘指示器替代，仅覆盖绘制，不影响 isChecked/toggled 等逻辑。
    """

    BOX = 16        # 指示器边长（像素）

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WA_Hover, True)   # 开启悬停事件以支持 hover 反馈
        self.setMinimumHeight(30)

    def paintEvent(self, event):
        """自绘：左侧圆角指示器 + 右侧文字（按勾选/悬停状态切换配色）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self.BOX
        rect = QRectF(0, (self.height() - box) / 2, box, box)
        hovered = self.underMouse()

        if self.isChecked():                    # 选中：钢灰圆角块 + 深色对勾
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLOR_ACCENT))
            painter.drawRoundedRect(rect, 5, 5)
            pen = QPen(QColor("#14171b"), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            left, top = rect.x(), rect.y()
            painter.drawPolyline([
                QPointF(left + box * 0.26, top + box * 0.53),
                QPointF(left + box * 0.43, top + box * 0.69),
                QPointF(left + box * 0.75, top + box * 0.32),
            ])
        else:                                   # 未选中：半透明玻璃小方块，悬停时提亮
            painter.setPen(QPen(
                QColor(31, 35, 39, 120 if hovered else 76), 1))
            painter.setBrush(QColor(31, 35, 39, 12))
            painter.drawRoundedRect(rect, 5, 5)

        painter.setPen(QColor(COLOR_TEXT))
        painter.setFont(self.font())
        painter.drawText(QRect(box + 8, 0, self.width() - box - 8, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text())


# ---------- 关于页（详情介绍 / 更新日志 / 免责声明） ----------

def latest_changelog_sections(text: str, count: int = 6) -> str:
    """从 CHANGELOG 全文截取最近 count 个版本段落（文件按"新版本在上"维护）。"""
    if not text.strip():
        return ""
    parts = text.split("\n## ")
    sections = [part.strip() for part in parts[1:count + 1] if part.strip()]
    if not sections:
        return text.strip()
    return "\n\n".join("## " + section for section in sections)


def read_changelog_text(count: int = 6) -> str:
    """读取程序目录下的 CHANGELOG.md 并截取最近若干版本；读取失败返回空串。"""
    try:
        path = os.path.join(cfg.app_dir(), "CHANGELOG.md")
        with open(path, "r", encoding="utf-8") as handle:
            return latest_changelog_sections(handle.read(), count)
    except OSError:
        return ""


def build_about_html(changelog_text: str, storage_root: str = "") -> str:
    """构造「关于」页 HTML：详情介绍 / 更新日志 / 免责声明（纯函数，便于测试）。"""
    storage = html.escape(storage_root) if storage_root else "（未设置）"
    config_dir = html.escape(str(cfg.CONFIG_DIR))
    if changelog_text.strip():
        log_html = f"<pre>{html.escape(changelog_text.strip())}</pre>"
    else:
        log_html = (
            "<p>未找到更新日志文件（CHANGELOG.md）。<br>"
            f"当前版本 v{html.escape(cfg.APP_VERSION)}；完整更新日志见项目目录或"
            "发行包中的 CHANGELOG.md。</p>")
    return f"""<html><body>
<h2>桌面分类助手 v{html.escape(cfg.APP_VERSION)}</h2>
<p>Windows 桌面文件整理工具 · 完全本地运行</p>

<h3>详情介绍</h3>
<ul>
<li>桌面悬浮分类格子：把桌面文件一键归入收纳库分类（分类可自定义、可映射任意文件夹）</li>
<li>一键整理桌面：整理前预览确认，所有移动可撤销；桌面右键 / 托盘菜单均可触发</li>
<li>收纳库搜索（拼音全拼与首字母）、拖动收纳与取出、格子整齐排列与收起</li>
<li>全局快捷键：F7 显示/隐藏格子、F8 控制面板、F9 游戏模式</li>
<li>版本更新：控制面板「检查更新」可检测新版本并一键升级（exe 形态自动替换并重启）</li>
<li>收纳库位置：{storage}</li>
<li>配置位置：{config_dir}</li>
<li>隐私：所有文件与配置都保存在本机；平时不联网、不上传、不收集任何数据（仅在你点击「检查更新」时访问你填写的更新源）</li>
</ul>

<h3>更新日志</h3>
{log_html}

<h3>免责声明</h3>
<p>本工具会移动、重命名您的文件：所有整理操作都会记录历史并可在面板中撤销或恢复，
删除操作均送往回收站。仍请在使用前自行备份重要数据，并确认整理结果。</p>
<p>本软件按“原样”提供，不附带任何明示或暗示的担保；因使用本软件造成的任何数据
丢失或损失，作者不承担责任。</p>
<p>隐私说明：本软件完全在本地运行，不会上传、收集或分享任何文件与使用数据；唯一
可能的联网行为是你主动点击「检查更新」时访问你自己填写的更新源。</p>
</body></html>"""


class AboutDialog(QDialog):
    """「关于」对话框：详情介绍 / 更新日志 / 免责声明（纯本地展示，无网络请求）。"""

    def __init__(self, parent=None, storage_root: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"关于 · 桌面分类助手 v{cfg.APP_VERSION}")
        self.resize(560, 620)
        layout = QVBoxLayout(self)

        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(False)
        browser.setHtml(build_about_html(read_changelog_text(), storage_root))
        layout.addWidget(browser, 1)

        row = QHBoxLayout()
        open_storage = QPushButton("打开收纳库")
        open_storage.clicked.connect(
            lambda: open_in_shell(storage_root) if storage_root else None)
        open_config = QPushButton("打开配置目录")
        open_config.clicked.connect(lambda: open_in_shell(cfg.CONFIG_DIR))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(open_storage)
        row.addWidget(open_config)
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)


class ControlPanel(QWidget):
    """主控制窗口，所有整理操作的入口。"""

    PAGE_SPECS = (
        ("整理", "桌面整理", "把散落在桌面的文件归入对应的收纳格子。"),
        ("格子", "格子", "决定桌面上显示哪些收纳格，以及它们的呈现方式。"),
        ("设置", "设置", "启动、整理规则与 Windows 集成。"),
        ("关于", "关于", "版本、配置与完整更新日志。"),
    )

    def __init__(self, ctx):
        """初始化面板。

        :param ctx: AppContext，需提供 on_organize/on_undo/on_widgets_rebuild/
                    set_autostart/create_shortcuts/set_game_mode/set_preview 回调与
                    refresh_status(text) 方法
        """
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"桌面分类助手 v{cfg.APP_VERSION} - 控制面板")
        # 宽高都按屏幕可用空间钳制（小屏笔记本不会被截断）
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(PANEL_WIDTH, screen.width() - 40),
                    min(PANEL_HEIGHT, screen.height() - 80))
        self.setStyleSheet(PANEL_QSS)
        self._restore_geometry()          # 恢复上次关闭时的面板位置与大小

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_rail())

        page = QWidget()
        page.setObjectName("page")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(30, 26, 30, 18)
        page_layout.setSpacing(18)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(3)
        self.page_title_label = QLabel()
        self.page_title_label.setObjectName("pageTitle")
        self.page_subtitle_label = QLabel()
        self.page_subtitle_label.setObjectName("pageSubtitle")
        title_block.addWidget(self.page_title_label)
        title_block.addWidget(self.page_subtitle_label)
        header.addLayout(title_block)
        header.addStretch()
        header_hint = QLabel("F8 随时打开")
        header_hint.setObjectName("sectionHint")
        header.addWidget(header_hint)
        page_layout.addLayout(header)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_grids_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_about_page())
        page_layout.addWidget(self.pages, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        footer.addWidget(self.status_label, 1)
        self.open_storage_btn = QPushButton("打开收纳库")
        self.open_storage_btn.setToolTip("在资源管理器中打开收纳库目录")
        self.open_storage_btn.clicked.connect(self._open_storage)
        footer.addWidget(self.open_storage_btn)
        self.open_config_btn = QPushButton("打开配置目录")
        self.open_config_btn.setToolTip(
            f"配置与整理历史都在这里：{cfg.CONFIG_DIR}")
        self.open_config_btn.clicked.connect(
            lambda: open_in_shell(cfg.CONFIG_DIR))
        footer.addWidget(self.open_config_btn)
        self.about_btn = QPushButton("关于")
        self.about_btn.setToolTip("详情介绍 / 更新日志 / 免责声明")
        self.about_btn.clicked.connect(self._show_about)
        footer.addWidget(self.about_btn)
        self.exit_btn = QPushButton("退出助手")
        self.exit_btn.setObjectName("dangerBtn")
        self.exit_btn.setToolTip(
            "停止格子与文件监听并退出程序（收纳库、历史与设置都会保留）")
        self.exit_btn.clicked.connect(self._request_quit)
        footer.addWidget(self.exit_btn)
        page_layout.addLayout(footer)
        root_layout.addWidget(page, 1)

        # 搜索防抖：停止输入 300ms 后才提交请求
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._run_search)
        self.search_edit.textChanged.connect(
            lambda _t: self._search_timer.start(SEARCH_DEBOUNCE_MS))

        # 搜索在后台线程执行（search_storage 全库遍历 + 拼音较慢）：
        # 提交时记下服务端序号，结果回填时校验序号与关键词，过期结果丢弃
        self._search_seq = 0
        self._search_pending = False
        if self.ctx.search_service is not None:
            self.ctx.search_service.results_ready.connect(
                self._on_search_results)

        # 检查更新：网络请求只在用户点击后发起（后台线程，同搜索服务模式）
        self.update_service = updater.UpdateService(self)
        self.update_service.checked.connect(self._on_update_checked)
        self.update_service.downloaded.connect(self._on_update_downloaded)
        self.update_service.progress.connect(self._on_update_progress)
        self._pending_manifest = None

        self._switch_page(0)

    def _build_rail(self) -> QWidget:
        """左侧导航：深色窄栏承载品牌与四个页面入口。"""
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(208)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(22, 24, 18, 18)
        layout.setSpacing(6)

        brand = QLabel("桌面分类")
        brand.setObjectName("brand")
        brand_sub = QLabel("桌面文件收纳")
        brand_sub.setObjectName("brandSub")
        layout.addWidget(brand)
        layout.addWidget(brand_sub)
        layout.addSpacing(24)

        self.nav_buttons = []
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (label, _title, _subtitle) in enumerate(self.PAGE_SPECS):
            button = QPushButton(label)
            button.setObjectName("navBtn")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, page_index=index:
                self._switch_page(page_index))
            group.addButton(button, index)
            layout.addWidget(button)
            self.nav_buttons.append(button)

        layout.addStretch()
        version = QLabel(f"v{cfg.APP_VERSION}")
        version.setObjectName("railVersion")
        layout.addWidget(version)
        hint = QLabel("关闭窗口不会退出")
        hint.setObjectName("railVersion")
        layout.addWidget(hint)
        return rail

    def _switch_page(self, index: int):
        """切换左侧导航页并同步标题。"""
        index = max(0, min(len(self.PAGE_SPECS) - 1, int(index)))
        self.pages.setCurrentIndex(index)
        _label, title, subtitle = self.PAGE_SPECS[index]
        self.page_title_label.setText(title)
        self.page_subtitle_label.setText(subtitle)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    @staticmethod
    def _scroll_page(content: QWidget) -> QScrollArea:
        """给页面加透明滚动容器，小屏/高 DPI 下内容不被截断。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _make_card(title: str = "", hint: str = ""):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(11)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            layout.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("sectionHint")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        return card, layout

    def _build_overview_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        hero, hero_layout = self._make_card(
            "整理桌面", "整理前可先预览确认；所有移动都能撤销。")
        self.organize_btn = QPushButton("一键整理桌面")
        self.organize_btn.setObjectName("primaryBtn")
        self.organize_btn.setFont(
            QFont(theme.FONT_FAMILY, 10, QFont.DemiBold))
        self.organize_btn.clicked.connect(self._do_organize)
        hero_layout.addWidget(self.organize_btn)
        action_row = QHBoxLayout()
        self.undo_btn = QPushButton("撤销上次整理")
        self.undo_btn.clicked.connect(self._do_undo)
        self.restore_btn = QPushButton("恢复全部")
        self.restore_btn.clicked.connect(self._do_restore)
        action_row.addWidget(self.undo_btn)
        action_row.addWidget(self.restore_btn)
        action_row.addStretch()
        hero_layout.addLayout(action_row)

        storage, storage_layout = self._make_card(
            "收纳目录", "所有分类文件夹都保存在这里。")
        cfg_row = QHBoxLayout()
        self.root_edit = QLineEdit()
        self.root_edit.setText(self.ctx.config.get("storageRoot", ""))
        self.root_edit.setCursorPosition(0)
        self.root_edit.editingFinished.connect(self._save_root)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_root)
        cfg_row.addWidget(self.root_edit, 1)
        cfg_row.addWidget(browse_btn)
        storage_layout.addLayout(cfg_row)

        search_card, search_layout = self._make_card(
            "搜索收纳库", "支持文件名、拼音全拼与首字母。")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入文件名关键词，如：报告")
        search_layout.addWidget(self.search_edit)
        self.search_list = QListWidget()
        self.search_list.setMinimumHeight(100)
        self.search_list.itemDoubleClicked.connect(self._open_search_item)
        search_layout.addWidget(self.search_list, 1)
        locate_row = QHBoxLayout()
        locate_btn = QPushButton("打开所在位置")
        locate_btn.clicked.connect(self._locate_search_item)
        locate_row.addStretch()
        locate_row.addWidget(locate_btn)
        search_layout.addLayout(locate_row)

        left_column = QVBoxLayout()
        left_column.setSpacing(16)
        left_column.addWidget(hero)
        left_column.addWidget(storage)
        left_column.addStretch()
        right_column = QVBoxLayout()
        right_column.setSpacing(16)
        right_column.addWidget(search_card, 1)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left_column, 1)
        columns.addLayout(right_column, 1)
        layout.addLayout(columns, 1)
        return self._scroll_page(content)

    def _build_grids_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        category_card, category_layout = self._make_card(
            "显示的格子", "勾选需要常驻桌面的分类；自定义分类会出现在这里。")
        cat_row = QHBoxLayout()
        self.add_grid_btn = QPushButton("＋ 新建格子")
        self.add_grid_btn.clicked.connect(self._create_grid)
        self.map_folder_btn = QPushButton("映射文件夹…")
        self.map_folder_btn.clicked.connect(self.ctx.map_folder_interactive)
        cat_row.addWidget(self.add_grid_btn)
        cat_row.addWidget(self.map_folder_btn)
        cat_row.addStretch()
        category_layout.addLayout(cat_row)
        self._cat_grid = QGridLayout()
        self._cat_grid.setHorizontalSpacing(10)
        self._cat_grid.setVerticalSpacing(4)
        category_layout.addLayout(self._cat_grid)
        self._rebuild_category_checks()
        layout.addWidget(category_card)

        appearance, appearance_layout = self._make_card(
            "呈现方式", "这些设置会立即应用到桌面格子。")
        preview_row = QHBoxLayout()
        preview_label = QLabel("默认预览")
        self.preview_combo = QComboBox()
        self.preview_combo.addItems(["图标视图", "紧凑列表"])
        self.preview_combo.setCurrentIndex(
            0 if self.ctx.config.get("previewIcons", True) else 1)
        self.preview_combo.currentIndexChanged.connect(self._change_preview)
        preview_row.addWidget(preview_label)
        preview_row.addWidget(self.preview_combo, 1)
        appearance_layout.addLayout(preview_row)
        self.search_grid_check = GlassCheckBox("显示桌面搜索条")
        self.search_grid_check.setChecked(
            bool(self.ctx.config.get("searchGrid", False)))
        self.search_grid_check.toggled.connect(
            lambda checked: self.ctx.set_search_grid(checked))
        appearance_layout.addWidget(self.search_grid_check)
        tidy_btn = QPushButton("整齐排列全部格子")
        tidy_btn.clicked.connect(
            lambda: getattr(self.ctx, "tidy_layout", lambda: None)())
        appearance_layout.addWidget(tidy_btn)
        layout.addWidget(appearance)
        layout.addStretch()
        return self._scroll_page(content)

    def _build_settings_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        startup, startup_layout = self._make_card(
            "启动与桌面", "Win10 登录后自动运行，并保持格子位置。")
        self.autostart_check = GlassCheckBox("开机自启（Win10 登录后运行）")
        self.autostart_check.setChecked(autostart.get_autostart_enabled())
        self.autostart_check.toggled.connect(self._toggle_autostart)
        startup_layout.addWidget(self.autostart_check)
        self.game_check = GlassCheckBox("游戏模式（隐藏格子并暂停监听）")
        self.game_check.setChecked(
            bool(self.ctx.config.get("gameMode", False)))
        self.game_check.toggled.connect(
            lambda checked: self.ctx.set_game_mode(checked))
        startup_layout.addWidget(self.game_check)
        self.shell_menu_check = GlassCheckBox("桌面右键菜单（一键整理）")
        self.shell_menu_check.setChecked(shell_menu.is_registered())
        self.shell_menu_check.setToolTip(shell_menu.platform_menu_hint())
        self.shell_menu_check.toggled.connect(self._toggle_shell_menu)
        startup_layout.addWidget(self.shell_menu_check)
        startup_layout.addStretch()

        organize, organize_layout = self._make_card(
            "整理规则", "决定哪些桌面内容会被收走，以及自动整理的时机。")
        self.shortcut_check = GlassCheckBox("整理时收走快捷方式")
        self.shortcut_check.setChecked(
            bool(self.ctx.config.get("includeShortcuts", False)))
        self.shortcut_check.toggled.connect(self._save_shortcut_option)
        organize_layout.addWidget(self.shortcut_check)
        self.folder_check = GlassCheckBox("整理时收走文件夹")
        self.folder_check.setChecked(
            bool(self.ctx.config.get("includeFolders", True)))
        self.folder_check.toggled.connect(self._save_folder_option)
        organize_layout.addWidget(self.folder_check)
        self.confirm_check = GlassCheckBox("整理前先预览确认")
        self.confirm_check.setChecked(
            bool(self.ctx.config.get("confirmOrganize", True)))
        self.confirm_check.toggled.connect(self._save_confirm_option)
        organize_layout.addWidget(self.confirm_check)
        self.auto_check = GlassCheckBox("自动整理新桌面文件")
        self.auto_check.setChecked(
            bool(self.ctx.config.get("autoOrganize", False)))
        self.auto_check.toggled.connect(
            lambda checked: self.ctx.set_auto_organize(checked))
        organize_layout.addWidget(self.auto_check)
        self.auto_delay_combo = QComboBox()
        self.auto_delay_combo.addItems(
            ["静置 30 秒", "静置 5 分钟", "静置 30 分钟"])
        self._auto_delay_values = [30, 300, 1800]
        try:
            current_delay = int(self.ctx.config.get("autoOrganizeDelay", 300))
        except (TypeError, ValueError):
            current_delay = 300
        self.auto_delay_combo.setCurrentIndex(
            self._auto_delay_values.index(current_delay)
            if current_delay in self._auto_delay_values else 1)
        self.auto_delay_combo.currentIndexChanged.connect(
            lambda index: self.ctx.set_auto_organize_delay(
                self._auto_delay_values[index]))
        organize_layout.addWidget(self.auto_delay_combo)
        organize_layout.addStretch()

        launch, launch_layout = self._make_card(
            "快捷启动", "创建桌面与开始菜单快捷方式，随时唤起控制面板。")
        self.shortcut_btn = QPushButton("创建快捷方式（桌面 + 开始菜单）")
        self.shortcut_btn.clicked.connect(self._create_shortcuts)
        launch_layout.addWidget(self.shortcut_btn)
        launch_layout.addStretch()

        update_card, update_layout = self._make_card(
            "版本与更新", "只有点击「检查更新」时才会访问更新源。")
        update_row = QHBoxLayout()
        self.version_label = QLabel(f"当前版本 v{cfg.APP_VERSION}")
        self.version_label.setObjectName("sectionHint")
        update_row.addWidget(self.version_label)
        update_row.addStretch()
        self.update_btn = QPushButton("检查更新")
        self.update_btn.clicked.connect(self._check_update)
        update_row.addWidget(self.update_btn)
        update_layout.addLayout(update_row)
        self.update_url_edit = QLineEdit()
        self.update_url_edit.setPlaceholderText(
            "新版本清单 JSON 地址（留空=不可检查）")
        self.update_url_edit.setText(self.ctx.config.get("updateUrl", ""))
        self.update_url_edit.setCursorPosition(0)
        self.update_url_edit.editingFinished.connect(self._save_update_url)
        update_layout.addWidget(self.update_url_edit)
        update_layout.addStretch()

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(startup, 1)
        columns.addWidget(organize, 1)
        layout.addLayout(columns)
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(launch, 1)
        bottom.addWidget(update_card, 1)
        layout.addLayout(bottom)
        layout.addStretch()
        return self._scroll_page(content)

    def _build_about_page(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        card, card_layout = self._make_card(
            "桌面分类助手",
            "Windows 桌面文件整理工具 · 完全本地运行 · 不上传任何文件")
        summary = QLabel(
            "桌面悬浮分类格子、一键整理、拖拽收纳、拼音搜索与可撤销历史。\n"
            "所有文件与配置都保存在本机；版本号与完整更新日志可在下方查看。")
        summary.setObjectName("sectionHint")
        summary.setWordWrap(True)
        card_layout.addWidget(summary)
        detail_btn = QPushButton("查看完整介绍与更新日志")
        detail_btn.clicked.connect(self._show_about)
        card_layout.addWidget(detail_btn)
        layout.addWidget(card)

        paths, paths_layout = self._make_card("本机位置")
        paths_layout.addWidget(QLabel(
            f"收纳库：{self.ctx.config.get('storageRoot', '')}"))
        paths_layout.addWidget(QLabel(f"配置：{cfg.CONFIG_DIR}"))
        layout.addWidget(paths)
        layout.addStretch()
        return self._scroll_page(content)

    # ---------- 操作回调 ----------

    def _do_organize(self):
        """一键整理桌面文件/文件夹（可配置先预览确认）并刷新格子。"""
        root = self.root_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "提示", "请先设置收纳目录")
            return
        desktop = org.get_desktop_path()
        include_shortcuts = self.shortcut_check.isChecked()
        include_folders = self.folder_check.isChecked()
        # 仅收纳"当前显示为格子"的分类：分类没有对应格子时把文件搬进收纳库
        # 会让用户找不到（旧行为是把视频类文件收进无格子的目录后凭空消失）
        enabled = set(self.ctx.config.get("categories", []))   # 搜索格子不在其中
        if self.ctx.config.get("confirmOrganize", True):
            try:
                preview = org.preview_organize(
                    desktop, root, include_shortcuts, include_folders,
                    enabled_categories=enabled)
            except RuntimeError as exc:
                QMessageBox.critical(self, "整理失败", str(exc))
                return
            if not preview:
                self.ctx.refresh_status("桌面没有需要整理的文件")
                return
            if not self._confirm_preview(preview):
                self.ctx.refresh_status("已取消整理")
                return
        try:
            records, failed, skipped = org.organize_desktop(
                desktop, root,
                include_shortcuts=include_shortcuts,
                include_folders=include_folders, return_failed=True,
                enabled_categories=enabled)
        except (RuntimeError, OSError) as exc:
            # OSError：organizer 里 os.makedirs 建分类目录失败（磁盘只读/权限等），
            # 旧实现只捕 RuntimeError，会让异常逃出槽函数，用户看不到任何提示。
            QMessageBox.critical(self, "整理失败", str(exc))
            return
        cfg.append_records([r.to_dict() for r in records])
        self.ctx.on_organize(len(records))
        if skipped:                         # 分类未启用格子：有意留在桌面，必须告知
            self.ctx.refresh_status(
                f"{len(skipped)} 项留在桌面：对应分类未显示为格子"
                "（可在「显示的格子」中勾选后再整理）")
            return
        if failed:                          # 失败（被占用等）不再静默，明确告知
            names = "、".join(failed[:3])
            suffix = f" 等 {len(failed)} 项" if len(failed) > 3 else ""
            self.ctx.refresh_status(
                f"已整理 {len(records)} 项，{len(failed)} 项失败"
                f"（{names}{suffix}，可能被占用）")

    def _confirm_preview(self, preview) -> bool:
        """把整理预览结果整理成"分类：n 项"摘要，请用户确认后再执行。"""
        counts = {}
        for category, _name in preview:
            counts[category] = counts.get(category, 0) + 1
        order = list(org.CATEGORY_RULES.keys()) + [org.FOLDER_CATEGORY]
        lines = [f"{cat}：{counts[cat]} 项" for cat in order if cat in counts]
        message = f"本次将整理 {len(preview)} 项：\n\n" + "\n".join(lines)
        folder_count = counts.get(org.FOLDER_CATEGORY, 0)
        if folder_count:
            message += f"\n\n注意：其中包含 {folder_count} 个文件夹，会整体移入收纳库。"
        message += "\n\n整理后可随时点击“撤销上次整理”恢复。确定开始吗？"
        answer = QMessageBox.question(
            self, "整理前预览", message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _do_undo(self):
        """撤销最近一次批次整理。"""
        batch = cfg.pop_last_batch()
        if not batch:
            self.ctx.refresh_status("没有可撤销的整理记录")
            return
        records = [org.MoveRecord.from_dict(r) for r in batch]
        restored = org.undo_moves(records)
        self.ctx.on_undo(len(restored), len(records) - len(restored))

    def _do_restore(self):
        """恢复历史记录中的全部文件到原位置（清空历史，需确认）。"""
        history = cfg.load_history()
        if not history:
            self.ctx.refresh_status("历史记录为空，无需恢复")
            return
        answer = QMessageBox.question(
            self, "恢复全部",
            f"将尝试把 {len(history)} 条记录中的文件全部移回各自原来的位置，\n"
            "并清空撤销历史（此操作不能再撤销）。确定要继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        records = [org.MoveRecord.from_dict(r) for r in history]
        restored = org.undo_moves(records)
        cfg.save_history([])
        self.ctx.on_undo(len(restored), len(records) - len(restored))

    # ---------- 设置项 ----------

    def _browse_root(self):
        """弹出目录选择对话框设置收纳根目录。"""
        current = self.root_edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "选择收纳目录", current)
        if chosen:
            self.root_edit.setText(chosen)
            self._save_root()

    def _save_root(self):
        """保存收纳根目录并重建格子；值未变化时直接跳过。

        editingFinished 在回车或"失焦"时都会触发，即使文本从未改动；
        不做守卫的话，用户每点一下别的控件都会全量重建一次格子。
        """
        old_root = os.path.normcase(os.path.normpath(
            str(self.ctx.config.get("storageRoot", ""))))
        text = self.root_edit.text().strip()
        if os.path.normcase(os.path.normpath(text)) == old_root:
            return                       # 未修改：不写配置、不重建格子
        self.ctx.config["storageRoot"] = text
        cfg.save_config(self.ctx.config)
        self.ctx.on_widgets_rebuild()

    def _save_categories(self):
        """保存分类开关并重建格子。"""
        selected = [n for n, c in self.category_checks.items() if c.isChecked()]
        self.ctx.config["categories"] = selected
        cfg.save_config(self.ctx.config)
        self.ctx.on_widgets_rebuild()

    def _save_shortcut_option(self):
        """保存"是否收走快捷方式"选项。"""
        self.ctx.config["includeShortcuts"] = self.shortcut_check.isChecked()
        cfg.save_config(self.ctx.config)

    def _save_folder_option(self):
        """保存"是否收走文件夹"选项。"""
        self.ctx.config["includeFolders"] = self.folder_check.isChecked()
        cfg.save_config(self.ctx.config)

    def _save_confirm_option(self):
        """保存"整理前先预览确认"选项。"""
        self.ctx.config["confirmOrganize"] = self.confirm_check.isChecked()
        cfg.save_config(self.ctx.config)

    # ---------- 分类格子管理 ----------

    def _rebuild_category_checks(self):
        """按当前配置重建分类勾选列表（内置分类在前，自定义格子随后）。"""
        while self._cat_grid.count():
            item = self._cat_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.category_checks = {}
        categories = self.ctx.config.get("categories", [])
        names = [n for n in org.CATEGORY_RULES if n != "其他"] + \
                [cfg.FOLDER_CATEGORY]
        for name in categories:
            if name not in names and name != "其他":
                names.append(name)          # 自定义格子
        names.append("其他")
        for i, name in enumerate(names):
            check = GlassCheckBox(name)
            check.setChecked(name in categories)
            check.toggled.connect(self._save_categories)
            self.category_checks[name] = check
            self._cat_grid.addWidget(check, i // 4, i % 4)

    def refresh_category_checks(self):
        """供 AppContext 在新建/重命名/删除格子后刷新勾选区。"""
        self._rebuild_category_checks()

    def _create_grid(self):
        """新建自定义格子（弹窗与校验逻辑与格子右键菜单共用）。"""
        self.ctx.create_grid_interactive()

    def _toggle_autostart(self, checked: bool):
        """开关开机自启；注册表操作失败时回滚勾选并提示。"""
        try:
            self.ctx.set_autostart(checked)
        except OSError as exc:
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not checked)
            self.autostart_check.blockSignals(False)
            QMessageBox.warning(self, "开机自启设置失败", str(exc))

    def _toggle_shell_menu(self, checked: bool):
        """开关桌面右键菜单项；注册表操作失败时回滚勾选并提示。"""
        try:
            self.ctx.set_shell_menu(checked)
        except OSError as exc:
            self.shell_menu_check.blockSignals(True)
            self.shell_menu_check.setChecked(not checked)
            self.shell_menu_check.blockSignals(False)
            QMessageBox.warning(self, "桌面右键菜单设置失败", str(exc))

    def _show_about(self):
        """打开「关于」对话框（详情介绍 / 更新日志 / 免责声明）。"""
        AboutDialog(self, self.ctx.config.get("storageRoot", "")).exec()

    def _open_storage(self):
        """打开收纳库目录（不存在时只提示，不弹系统错误框）。"""
        root = self.ctx.config.get("storageRoot", "")
        if root and os.path.isdir(root):
            open_in_shell(root)
        else:
            self.ctx.refresh_status("收纳目录不存在，请检查上方「收纳目录」设置")

    def _request_quit(self):
        """面板「退出助手」：确认后退出程序（AppContext 统一收尾并保存状态）。"""
        answer = QMessageBox.question(
            self, "退出桌面分类助手",
            "确定要退出吗？\n\n"
            "· 退出后桌面格子与文件监听都会停止；\n"
            "· 收纳库文件、整理记录与设置都会保留，下次启动自动恢复；\n\n"
            "（只想收起窗口的话：直接关掉本窗口即可，程序会继续在托盘运行；"
            "想开机自动运行可在中栏勾选「开机自启」。）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.save_geometry()
        quit_fn = getattr(self.ctx, "request_quit", None)
        if callable(quit_fn):
            quit_fn()
        else:
            QApplication.instance().quit()

    # ---------- 版本更新 ----------

    def _save_update_url(self):
        """保存更新源地址（值未变化时不写配置）。"""
        text = self.update_url_edit.text().strip()
        if text == str(self.ctx.config.get("updateUrl", "")):
            return
        self.ctx.config["updateUrl"] = text
        cfg.save_config(self.ctx.config)
        self.ctx.refresh_status("更新源已保存" if text else "已清空更新源")

    def _check_update(self):
        """点击「检查更新」：后台拉取新版本清单（唯一的主动联网入口）。"""
        url = self.update_url_edit.text().strip()
        if not url:
            QMessageBox.information(
                self, "检查更新",
                "请先在下方填写更新源地址（新版本清单 JSON 的地址）。")
            return
        self.update_btn.setEnabled(False)
        self.ctx.refresh_status("正在检查更新…")
        self.update_service.check(url)

    def _on_update_checked(self, ok: bool, payload):
        """清单返回：无新版本提示最新；有新版本弹确认（源码模式打开下载页）。"""
        self.update_btn.setEnabled(True)
        if not ok:
            self.ctx.refresh_status("检查更新失败（网络或地址问题）")
            QMessageBox.warning(self, "检查更新失败", str(payload))
            return
        manifest = payload
        if not updater.is_newer(manifest["version"], cfg.APP_VERSION):
            self.ctx.refresh_status(f"已是最新版本 v{cfg.APP_VERSION}")
            QMessageBox.information(
                self, "检查更新", f"已是最新版本（v{cfg.APP_VERSION}）。")
            return
        notes = manifest["notes"] or "（作者未填写更新说明）"
        answer = QMessageBox.question(
            self, "发现新版本",
            f"发现新版本 v{manifest['version']}（当前 v{cfg.APP_VERSION}）。"
            f"\n\n更新说明：\n{notes}\n\n现在更新吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        if not getattr(sys, "frozen", False):
            # 源码模式：打开下载页，由用户手动更新（不覆盖正在使用的源码）
            webbrowser.open(manifest["page"])
            self.ctx.refresh_status("已打开下载页（源码模式请手动更新）")
            return
        self._pending_manifest = manifest
        self.update_btn.setEnabled(False)
        self.ctx.refresh_status(f"正在下载 v{manifest['version']}… 0%")
        dest = os.path.join(
            tempfile.gettempdir(),
            f"桌面分类助手_v{manifest['version']}.exe")
        self.update_service.download(
            manifest["url"], dest, manifest["sha256"])

    def _on_update_progress(self, percent: int):
        """下载进度回填状态栏。"""
        self.ctx.refresh_status(f"正在下载新版本… {percent}%")

    def _on_update_downloaded(self, ok: bool, payload: str):
        """下载完成：替换程序文件并重启到新版本（失败时保持原样并提示）。"""
        if not ok:
            self.update_btn.setEnabled(True)
            self.ctx.refresh_status("更新下载失败")
            QMessageBox.warning(self, "更新失败", payload)
            return
        try:
            exe = updater.apply_update(sys.executable, payload)
        except OSError as exc:
            self.update_btn.setEnabled(True)
            self.ctx.refresh_status("更新失败（程序文件被占用？）")
            QMessageBox.critical(
                self, "更新失败",
                f"替换程序文件失败：{exc}\n可稍后重试或手动更新。")
            return
        version = (self._pending_manifest or {}).get("version", "")
        self.ctx.refresh_status(f"更新完成，正在重启到 v{version}…")
        # 启动新版本并退出当前进程；新实例会先等旧进程退出（--updated-from）
        subprocess.Popen([exe, "--updated-from", str(os.getpid())])
        QApplication.instance().quit()

    def _create_shortcuts(self):
        """生成桌面与开始菜单快捷方式并汇报结果（含组合键提示）。"""
        try:
            created = self.ctx.create_shortcuts()
        except (RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "创建快捷方式失败", str(exc))
            return
        self.ctx.refresh_status(
            f"已创建 {len(created)} 个快捷方式（可直接双击启动）")
        QMessageBox.information(
            self, "创建完成",
            "已创建以下快捷方式：\n" + "\n".join(created) +
            "\n\n提示：双击即可启动；若已常驻托盘，"
            "按 Ctrl+Alt+D 可随时唤起控制面板。")

    def _change_preview(self, index: int):
        """批量切换全部格子的预览方式（单个格子用右键菜单切换）。"""
        self.ctx.set_preview_all(index == 0)

    def set_game_mode_checked(self, checked: bool):
        """由外部（托盘菜单/F7）同步游戏模式勾选状态，不触发回调。"""
        self.game_check.blockSignals(True)
        self.game_check.setChecked(checked)
        self.game_check.blockSignals(False)

    def set_search_grid_checked(self, checked: bool):
        """由外部（格子右键删除等）同步搜索格子开关，不触发回调。"""
        self.search_grid_check.blockSignals(True)
        self.search_grid_check.setChecked(checked)
        self.search_grid_check.blockSignals(False)

    def set_auto_organize_checked(self, checked: bool):
        """由外部（托盘菜单）同步自动整理勾选状态，不触发回调。"""
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(checked)
        self.auto_check.blockSignals(False)

    # ---------- 收纳库搜索 ----------

    def _run_search(self):
        """提交收纳库搜索请求（后台线程执行，UI 线程不再全库遍历）。"""
        keyword = self.search_edit.text()
        self.search_list.clear()
        self._search_pending = False
        if not keyword.strip():
            self.ctx.refresh_status("输入关键词搜索收纳库")
            return
        service = self.ctx.search_service
        if service is not None:
            self._search_pending = True
            self._search_seq = service.start(keyword, SEARCH_RESULT_LIMIT)
            self.ctx.refresh_status("搜索中…")
        else:
            self._apply_search_results(keyword,
                                       self._search_now(keyword, SEARCH_RESULT_LIMIT))

    def _search_now(self, keyword: str, limit: int) -> list:
        """同步搜索（service 未配置时的降级路径，与后台逻辑一致）。"""
        root = self.ctx.config.get("storageRoot", "")
        mapped = [(name, path)
                  for name, path in self.ctx.config.get("gridPaths", {}).items()
                  if os.path.isdir(path)]
        return org.search_storage(root, keyword, limit=limit, extra_dirs=mapped)

    def _apply_search_results(self, keyword: str, results: list):
        """把搜索结果填充到面板列表并更新状态栏。"""
        if self._search_pending:
            self._search_pending = False
        for cat, path in results:
            item = QListWidgetItem(f"{os.path.basename(path)}（{cat}）")
            item.setData(Qt.UserRole, path)
            self.search_list.addItem(item)
        note = "（已达上限，可细化关键词）" if len(results) >= SEARCH_RESULT_LIMIT else ""
        self.ctx.refresh_status(f"搜索到 {len(results)} 个结果{note}")

    def _on_search_results(self, seq: int, keyword: str, results: list):
        """后台搜索回填结果；序号不匹配或关键词已变（过期结果）则丢弃。"""
        if seq != self._search_seq or not self._search_pending:
            return
        if keyword != self.search_edit.text():
            return
        self._apply_search_results(keyword, results or [])

    def _open_search_item(self, item):
        """双击搜索结果：用系统默认程序打开。"""
        path = item.data(Qt.UserRole)
        if path:
            open_in_shell(path)

    def _locate_search_item(self):
        """在资源管理器中打开当前选中结果所在的文件夹。"""
        item = self.search_list.currentItem()
        if item is None:
            self.ctx.refresh_status("请先在搜索结果中选中一项")
            return
        path = item.data(Qt.UserRole)
        if path:
            open_in_shell(os.path.dirname(path))

    # ---------- 窗口几何记忆 ----------

    def _restore_geometry(self):
        """恢复上次关闭时的面板位置与大小；旧版竖版尺寸或越界时用横向默认布局。

        横向改版后旧几何（宽 480）不再适配：宽度 < 800 的历史记忆直接忽略、
        居中显示新默认尺寸；恢复的尺寸同样按屏幕钳制（换到小屏显示器不会超界）。
        """
        screen = QApplication.primaryScreen().availableGeometry()
        saved = cfg.parse_geometry(self.ctx.config.get("panelGeometry", ""))
        if saved and saved[2] >= 800 and self.ctx._position_visible(*saved):
            width = max(600, min(int(saved[2]), screen.width() - 40))
            height = max(420, min(int(saved[3]), screen.height() - 80))
            self.resize(width, height)
            self.move(saved[0], saved[1])
            return
        self.move(screen.center() - self.rect().center())

    def save_geometry(self):
        """把面板当前位置与大小写入配置（关闭/退出时调用）。"""
        self.ctx.config["panelGeometry"] = cfg.format_geometry(
            self.x(), self.y(), self.width(), self.height())
        cfg.save_config(self.ctx.config)

    def closeEvent(self, event):
        """关闭面板（隐藏）前保存几何，下次打开保持原位。"""
        self.save_geometry()
        super().closeEvent(event)
