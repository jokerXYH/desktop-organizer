# -*- coding: utf-8 -*-
"""控制面板窗口：一键整理、撤销、恢复、设置项、搜索与各类开关。

面板不直接持有业务对象，通过 AppContext 回调与主程序交互，
保证逻辑层可独立测试。
v2：新增开机自启、游戏模式、预览方式切换、收纳库搜索与暗色美化。
"""
import os

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

import autostart
import config as cfg
import organizer as org
from widgets import COLOR_ACCENT, COLOR_TEXT, COLOR_TEXT_DIM, open_in_shell

PANEL_WIDTH = 480    # 面板宽度
PANEL_HEIGHT = 760   # 面板高度
SEARCH_DEBOUNCE_MS = 300          # 搜索输入防抖
SEARCH_RESULT_LIMIT = 100         # 搜索结果上限（与 organizer.search_storage 一致）

# 面板玻璃拟态 QSS：深色底 + 浅白描边 + 半透明控件层叠（与桌面格子同一套语言）
PANEL_QSS = """
QWidget{background:#17181b;color:#eceef1;font-family:"Microsoft YaHei UI";font-size:12px;}
QLabel{background:transparent;}
QLabel#sectionLabel{color:#9ba1ab;font-size:11px;}
QPushButton{background:rgba(255,255,255,14);border:1px solid rgba(255,255,255,30);
            border-radius:9px;padding:7px 14px;color:#eceef1;}
QPushButton:hover{background:rgba(255,255,255,26);border-color:rgba(255,255,255,150);}
QPushButton:pressed{background:rgba(255,255,255,8);}
QPushButton#accentBtn{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                     stop:0 #ccd4dd, stop:1 #a6b0bd);
                     border:none;color:#14171b;font-weight:bold;border-radius:9px;}
QPushButton#accentBtn:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                     stop:0 #dbe2e9, stop:1 #b6c0cc);}
QPushButton#accentBtn:pressed{background:#a6b0bd;}
QLineEdit{background:rgba(255,255,255,10);border:1px solid rgba(255,255,255,28);
          border-radius:9px;padding:6px 10px;color:#eceef1;
          selection-background-color:rgba(255,255,255,70);}
QLineEdit:focus{border-color:rgba(255,255,255,150);background:rgba(255,255,255,16);}
QComboBox{background:rgba(255,255,255,10);border:1px solid rgba(255,255,255,28);
          border-radius:9px;padding:5px 10px;color:#eceef1;}
QComboBox:hover{border-color:rgba(255,255,255,150);}
QComboBox::drop-down{border:none;width:22px;}
QComboBox QAbstractItemView{background:#1d2129;color:#eceef1;outline:0;
          border:1px solid rgba(255,255,255,30);border-radius:8px;padding:4px;
          selection-background-color:rgba(255,255,255,70);}
QListWidget{background:rgba(0,0,0,70);border:1px solid rgba(255,255,255,26);
            border-radius:10px;outline:0;padding:4px;}
QListWidget::item{padding:6px;border-radius:7px;}
QListWidget::item:hover{background:rgba(255,255,255,16);}
QListWidget::item:selected{background:rgba(255,255,255,60);color:#ffffff;}
QScrollBar:vertical{background:transparent;width:8px;margin:2px;}
QScrollBar::handle:vertical{background:rgba(255,255,255,50);border-radius:4px;
                            min-height:28px;}
QScrollBar::handle:vertical:hover{background:#93a1b3;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
"""


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
            # 面板底色本身很暗，透明度太低会"看不见勾选框"——提高描边/填充对比度
            painter.setPen(QPen(QColor(255, 255, 255, 110 if hovered else 78), 1))
            painter.setBrush(QColor(255, 255, 255, 40 if hovered else 24))
            painter.drawRoundedRect(rect, 5, 5)

        painter.setPen(QColor(COLOR_TEXT))
        painter.setFont(self.font())
        painter.drawText(QRect(box + 8, 0, self.width() - box - 8, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, self.text())


class ControlPanel(QWidget):
    """主控制窗口，所有整理操作的入口。"""

    def __init__(self, ctx):
        """初始化面板。

        :param ctx: AppContext，需提供 on_organize/on_undo/on_widgets_rebuild/
                    set_autostart/create_shortcuts/set_game_mode/set_preview 回调与
                    refresh_status(text) 方法
        """
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"桌面分类助手 v{cfg.APP_VERSION} - 控制面板")
        # 高度按屏幕可用空间钳制：1366×768 等小屏笔记本上不会被任务栏截断
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(PANEL_WIDTH, min(PANEL_HEIGHT, screen.height() - 80))
        self.setStyleSheet(PANEL_QSS)
        self._restore_geometry()          # 恢复上次关闭时的面板位置与大小

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ---- 第一行：整理操作按钮 ----
        action_row = QHBoxLayout()
        self.organize_btn = QPushButton("一键整理桌面")
        self.organize_btn.setObjectName("accentBtn")
        self.organize_btn.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self.organize_btn.clicked.connect(self._do_organize)
        self.undo_btn = QPushButton("撤销上次整理")
        self.undo_btn.clicked.connect(self._do_undo)
        self.restore_btn = QPushButton("恢复全部")
        self.restore_btn.clicked.connect(self._do_restore)
        for btn in (self.organize_btn, self.undo_btn, self.restore_btn):
            action_row.addWidget(btn)
        layout.addLayout(action_row)

        # ---- 第二行：收纳目录设置 ----
        cfg_row = QHBoxLayout()
        root_label = QLabel("收纳目录:")
        self.root_edit = QLineEdit()
        self.root_edit.setText(self.ctx.config.get("storageRoot", ""))
        self.root_edit.editingFinished.connect(self._save_root)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_root)
        cfg_row.addWidget(root_label)
        cfg_row.addWidget(self.root_edit)
        cfg_row.addWidget(browse_btn)
        layout.addLayout(cfg_row)

        # ---- 快捷启动：一键在桌面与开始菜单生成启动快捷方式 ----
        launch_row = QHBoxLayout()
        launch_label = QLabel("快捷启动:")
        launch_label.setObjectName("sectionLabel")
        self.shortcut_btn = QPushButton("创建快捷方式（桌面 + 开始菜单）")
        self.shortcut_btn.clicked.connect(self._create_shortcuts)
        launch_row.addWidget(launch_label)
        launch_row.addWidget(self.shortcut_btn)
        layout.addLayout(launch_row)

        # ---- 第三行：功能开关（自启 / 游戏模式 / 收录选项 / 预览方式） ----
        option_grid = QGridLayout()
        self.shortcut_check = GlassCheckBox("整理时收走快捷方式")
        self.shortcut_check.setChecked(
            bool(self.ctx.config.get("includeShortcuts", False)))
        self.shortcut_check.toggled.connect(self._save_shortcut_option)
        self.folder_check = GlassCheckBox("整理时收走文件夹")
        self.folder_check.setChecked(
            bool(self.ctx.config.get("includeFolders", True)))
        self.folder_check.toggled.connect(self._save_folder_option)
        self.autostart_check = GlassCheckBox("开机自启")
        self.autostart_check.setChecked(autostart.get_autostart_enabled())
        self.autostart_check.toggled.connect(self._toggle_autostart)
        self.game_check = GlassCheckBox("游戏模式（隐藏格子并暂停监听）")
        self.game_check.setChecked(
            bool(self.ctx.config.get("gameMode", False)))
        self.game_check.toggled.connect(
            lambda checked: self.ctx.set_game_mode(checked))
        preview_label = QLabel("预览方式（全部格子）:")
        self.preview_combo = QComboBox()
        self.preview_combo.addItems(["图标视图", "紧凑列表"])
        self.preview_combo.setCurrentIndex(
            0 if self.ctx.config.get("previewIcons", True) else 1)
        self.preview_combo.currentIndexChanged.connect(self._change_preview)
        option_grid.addWidget(self.autostart_check, 0, 0)
        option_grid.addWidget(self.game_check, 0, 1)
        option_grid.addWidget(self.shortcut_check, 1, 0)
        option_grid.addWidget(self.folder_check, 1, 1)
        option_grid.addWidget(preview_label, 2, 0)
        option_grid.addWidget(self.preview_combo, 2, 1)
        self.confirm_check = GlassCheckBox("整理前先预览确认")
        self.confirm_check.setChecked(
            bool(self.ctx.config.get("confirmOrganize", True)))
        self.confirm_check.toggled.connect(self._save_confirm_option)
        option_grid.addWidget(self.confirm_check, 3, 0)
        self.auto_check = GlassCheckBox("自动整理新桌面文件")
        self.auto_check.setChecked(
            bool(self.ctx.config.get("autoOrganize", False)))
        self.auto_check.toggled.connect(
            lambda checked: self.ctx.set_auto_organize(checked))
        self.auto_delay_combo = QComboBox()
        self.auto_delay_combo.addItems(["静置 30 秒", "静置 5 分钟", "静置 30 分钟"])
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
        option_grid.addWidget(self.auto_check, 4, 0)
        option_grid.addWidget(self.auto_delay_combo, 4, 1)
        self.search_grid_check = GlassCheckBox("桌面搜索格子")
        self.search_grid_check.setChecked(
            bool(self.ctx.config.get("searchGrid", False)))
        self.search_grid_check.toggled.connect(
            lambda checked: self.ctx.set_search_grid(checked))
        option_grid.addWidget(self.search_grid_check, 5, 0)
        layout.addLayout(option_grid)

        # ---- 第四行：分类格子开关（勾选即显示；支持自定义格子） ----
        cat_row = QHBoxLayout()
        cat_label = QLabel("显示的格子（分类）:")
        cat_label.setObjectName("sectionLabel")
        self.add_grid_btn = QPushButton("＋ 新建格子")
        self.add_grid_btn.clicked.connect(self._create_grid)
        self.map_folder_btn = QPushButton("映射文件夹…")
        self.map_folder_btn.clicked.connect(self.ctx.map_folder_interactive)
        cat_row.addWidget(cat_label)
        cat_row.addStretch()
        cat_row.addWidget(self.add_grid_btn)
        cat_row.addWidget(self.map_folder_btn)
        layout.addLayout(cat_row)
        self._cat_grid = QGridLayout()
        layout.addLayout(self._cat_grid)
        self._rebuild_category_checks()

        # ---- 第五行：收纳库搜索 ----
        search_label = QLabel("搜索收纳库:")
        search_label.setObjectName("sectionLabel")
        layout.addWidget(search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入文件名关键词，如：报告")
        layout.addWidget(self.search_edit)
        self.search_list = QListWidget()
        self.search_list.itemDoubleClicked.connect(self._open_search_item)
        layout.addWidget(self.search_list, 1)   # 拉伸占据剩余空间
        locate_row = QHBoxLayout()
        locate_btn = QPushButton("打开所在位置")
        locate_btn.clicked.connect(self._locate_search_item)
        locate_row.addStretch()
        locate_row.addWidget(locate_btn)
        layout.addLayout(locate_row)

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

        # ---- 状态栏 ----
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(
            f"color:{COLOR_TEXT_DIM};background:transparent;padding:2px;")
        layout.addWidget(self.status_label)

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
        """恢复上次关闭时的面板位置与大小；越界或首次运行时居中显示。

        恢复的尺寸同样按屏幕钳制（换到小屏显示器后不会超界）。
        """
        screen = QApplication.primaryScreen().availableGeometry()
        saved = cfg.parse_geometry(self.ctx.config.get("panelGeometry", ""))
        if saved and self.ctx._position_visible(*saved):
            width = max(320, min(int(saved[2]), screen.width() - 40))
            height = max(400, min(int(saved[3]), screen.height() - 80))
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
