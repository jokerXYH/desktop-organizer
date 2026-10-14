# -*- coding: utf-8 -*-
"""生成 README 用的功能截图（合成的演示数据，不含任何个人文件）。

原理：构造真实的 FileWidget / SearchWidget / ControlPanel，并设置
Qt.WA_DontShowOnScreen 让窗口"不可见地"完成布局与渲染，再 grab() 取图，
最后用 QPainter 合成到自绘的渐变背景上。窗口不会出现在屏幕上。

用法（Windows + PySide6 环境）：
    python tools/make_readme_screenshots.py
输出：
    docs/images/hero.png     总览（格子 + 搜索格子 + 控制面板）
    docs/images/menu.png     右键菜单
    docs/images/compact.png  图标视图 / 紧凑列表 对比
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication

import autostart
import config as cfg
import organizer as org
import shell_menu
from control_panel import ControlPanel
from widgets import FileWidget, SearchWidget

# 演示数据：通用文件名，与真实用户数据无关
DEMO_ROOT = os.path.join(tempfile.gettempdir(), "organizer_demo_storage")
DEMO_FILES = {
    "文档": ["2026年度报告.pdf", "项目计划书.pdf", "会议纪要.docx",
             "数据报表.xlsx", "写作文档.md"],
    "图片": ["风景照片.png", "设计草图.jpg", "图标素材.svg"],
    "压缩包": ["素材备份.zip", "资料打包.7z"],
    "音频": ["轻音乐.mp3"],
    "文件夹": ["课程资料/讲义.pdf", "项目A/需求说明.docx"],
}


def build_demo_storage():
    """重建演示用的收纳库。"""
    if os.path.isdir(DEMO_ROOT):
        shutil.rmtree(DEMO_ROOT)
    for category, entries in DEMO_FILES.items():
        category_dir = os.path.join(DEMO_ROOT, category)
        os.makedirs(category_dir, exist_ok=True)
        for entry in entries:
            path = os.path.join(category_dir, *entry.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("demo")
    return DEMO_ROOT


class _StubContext:
    """ControlPanel 所需的最小上下文（截图专用，不连接真实应用）。"""

    def __init__(self, storage_root):
        # storageRoot 使用展示用的整洁路径（截图里不出现本机临时目录）
        self.config = dict(cfg.DEFAULT_CONFIG,
                           storageRoot=r"D:\DesktopOrganizer\收纳库",
                           categories=["文档", "图片", "压缩包", "音频",
                                       "文件夹", "其他"])
        # 面板构造时会读取该属性并决定是否连接搜索信号；截图用桩置空即可
        self.search_service = None

    def map_folder_interactive(self):
        pass

    def set_autostart(self, enabled):
        pass

    def set_game_mode(self, enabled):
        pass

    def set_preview_all(self, icons):
        pass

    def on_widgets_rebuild(self):
        pass

    def create_grid_interactive(self):
        pass

    def refresh_status(self, text):
        pass

    def set_search_grid(self, enabled):
        pass

    def _position_visible(self, *args):
        return True


def prepare(widget):
    """让窗口在"不显示"的状态下完成布局与渲染。"""
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()
    return widget


def grab(widget):
    """抓取控件渲染结果。"""
    QApplication.processEvents()
    return widget.grab()


def make_background(width, height):
    """自绘渐变背景（不涉及真实桌面/壁纸）。"""
    image = QImage(width, height, QImage.Format_ARGB32)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor("#243450"))
    gradient.setColorAt(1.0, QColor("#0e1726"))
    painter.fillRect(image.rect(), gradient)
    for cx, cy, color in ((width * 0.18, height * 0.12, QColor(76, 194, 255, 60)),
                          (width * 0.88, height * 0.85, QColor(122, 92, 255, 48))):
        glow = QRadialGradient(cx, cy, width * 0.5)
        glow.setColorAt(0.0, color)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(image.rect(), glow)
    painter.end()
    return image


def draw_widget(painter, widget_pixmap, x, y):
    """把控件画到画布上（带柔和投影）。

    grab() 得到的位图带 devicePixelRatio=2：drawPixmap 按**逻辑尺寸**绘制，
    所以阴影矩形也必须换算回逻辑尺寸，否则会画出 2 倍大的"幽灵盒子"。
    """
    ratio = widget_pixmap.devicePixelRatio() or 1.0
    width = widget_pixmap.width() / ratio
    height = widget_pixmap.height() / ratio
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.drawRoundedRect(x + 8, y + 12, width, height, 16, 16)
    painter.drawPixmap(x, y, widget_pixmap)


def main():
    storage = build_demo_storage()
    autostart.get_autostart_enabled = lambda: False   # 截图固定为未勾选
    shell_menu.is_registered = lambda: False          # 截图固定为未注册
    app = QApplication(sys.argv)
    out_dir = os.path.join(ROOT, "docs", "images")
    os.makedirs(out_dir, exist_ok=True)

    # ---- 各控件 ----
    doc_widget = prepare(FileWidget(
        "文档", os.path.join(storage, "文档"), None))
    doc_widget.refresh()
    pic_widget = prepare(FileWidget(
        "图片", os.path.join(storage, "图片"), None))
    pic_widget.refresh()
    zip_widget = prepare(FileWidget(
        "压缩包", os.path.join(storage, "压缩包"), None))
    zip_widget.refresh()
    folder_compact = prepare(FileWidget(
        "文件夹", os.path.join(storage, "文件夹"), None))
    folder_compact.set_preview(False)
    folder_compact.refresh()

    search_widget = prepare(SearchWidget(
        lambda keyword: org.search_storage(storage, keyword, limit=50),
        "搜索", storage, None))
    search_widget.search_edit.setText("报告")
    search_widget._run_search()

    panel = prepare(ControlPanel(_StubContext(storage)))

    # ---- hero.png：总览 ----
    canvas = make_background(1920, 1080)
    painter = QPainter(canvas)
    draw_widget(painter, grab(doc_widget), 110, 130)
    draw_widget(painter, grab(pic_widget), 400, 130)
    draw_widget(painter, grab(zip_widget), 690, 130)
    draw_widget(painter, grab(search_widget), 980, 130)
    draw_widget(painter, grab(folder_compact), 110, 550)
    draw_widget(painter, grab(panel), 1400, 170)
    painter.setPen(QColor("#e8eaed"))
    painter.setFont(QFont("Microsoft YaHei UI", 17, QFont.DemiBold))
    painter.drawText(110, 1020, "桌面分类助手 · 悬浮分类格子 / 一键整理 / 拼音搜索")
    painter.end()
    canvas.save(os.path.join(out_dir, "hero.png"))

    # ---- menu.png：右键菜单 ----
    item_pos = doc_widget.list_widget.visualItemRect(
        doc_widget.list_widget.item(0)).center()
    menu = doc_widget._build_menu(item_pos)
    prepare(menu)
    canvas = make_background(1280, 900)
    painter = QPainter(canvas)
    draw_widget(painter, grab(doc_widget), 130, 280)
    draw_widget(painter, grab(menu), 560, 140)
    painter.setPen(QColor("#e8eaed"))
    painter.setFont(QFont("Microsoft YaHei UI", 15, QFont.DemiBold))
    painter.drawText(130, 240,
                     "右键菜单：打开 / 复制路径 / 剪切粘贴 / 预览方式 / 新建·映射·删除格子")
    painter.end()
    canvas.save(os.path.join(out_dir, "menu.png"))

    # ---- compact.png：两种视图对比 ----
    icon_view = prepare(FileWidget("文档", os.path.join(storage, "文档"), None))
    icon_view.refresh()
    canvas = make_background(1100, 640)
    painter = QPainter(canvas)
    painter.setPen(QColor("#e8eaed"))
    painter.setFont(QFont("Microsoft YaHei UI", 13, QFont.DemiBold))
    painter.drawText(130, 120, "图标视图")
    painter.drawText(530, 120, "紧凑列表（小图标 + 宽松行距）")
    draw_widget(painter, grab(icon_view), 130, 160)
    draw_widget(painter, grab(folder_compact), 530, 160)
    painter.end()
    canvas.save(os.path.join(out_dir, "compact.png"))

    print("saved to", out_dir)
    for name in ("hero.png", "menu.png", "compact.png"):
        path = os.path.join(out_dir, name)
        print(name, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
