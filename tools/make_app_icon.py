# -*- coding: utf-8 -*-
"""生成应用图标 assets/app_icon.ico（供桌面快捷方式与打包 exe 共用）。

为什么自己拼 ICO：Qt 的 ICO 写入在本机会直接把进程打崩（0xC0000409），
而 PyInstaller 的 --icon 与 Windows 快捷方式的 IconLocation 都只认 .ico。
这里用最朴素的写法——把若干尺寸的 PNG 按 ICO 容器格式包起来即可，
Vista 之后的 Windows 支持 PNG 压缩的图标条目，无需第三方图像库。

用法：
    python tools/make_app_icon.py
输出：
    assets/app_icon.ico
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

ICON_SIZES = (16, 32, 48, 64, 128, 256)   # Windows 各 DPI 场景会用到的尺寸
BASE_SIZE = 64                            # 绘制基准：其余尺寸按比例缩放
TEXT_MIN_SIZE = 32                        # 小于此尺寸不画"分"字（会糊成一团）


def render_icon_png(size: int) -> bytes:
    """按指定边长绘制图标（玻璃感圆角块 + "分"字）并编码为 PNG 字节。"""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    scale = size / BASE_SIZE
    margin = 4 * scale
    span = size - 2 * margin

    gradient = QLinearGradient(0, margin, 0, size - margin)
    gradient.setColorAt(0.0, QColor(108, 214, 255))       # 上亮下深，有体积感
    gradient.setColorAt(1.0, QColor(48, 150, 214))
    painter.setBrush(gradient)
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(QRectF(margin, margin, span, span), 16 * scale, 16 * scale)
    painter.setBrush(QColor(0, 0, 0, 0))
    painter.setPen(QColor(255, 255, 255, 90))             # 内高光描边：玻璃反光
    painter.drawRoundedRect(QRectF(margin + scale, margin + scale,
                                   span - 2 * scale, span - 2 * scale),
                            15 * scale, 15 * scale)

    if size >= TEXT_MIN_SIZE:
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Microsoft YaHei UI", int(26 * scale), QFont.Bold))
        painter.drawText(image.rect(), 132, "分")          # 132 = 水平垂直居中
    painter.end()

    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def build_ico(entries) -> bytes:
    """把 [(边长, PNG字节), ...] 按 ICO 容器格式打包。

    ICO 结构 = 文件头(6B) + 每张图一条目录项(16B) + 各图原始数据；
    边长 >= 256 时目录项用 0 表示（ICO 的边长字段只有 1 字节）。
    """
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, payload = b"", b""
    for size, png in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + directory + payload


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无显示环境也能绘制
    QApplication.instance() or QApplication(sys.argv)        # 文字绘制需要应用实例
    entries = [(size, render_icon_png(size)) for size in ICON_SIZES]
    out_dir = os.path.join(ROOT, "assets")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "app_icon.ico")
    with open(out_path, "wb") as handle:
        handle.write(build_ico(entries))
    print(f"已生成 {out_path}（{len(ICON_SIZES)} 个尺寸）")


if __name__ == "__main__":
    main()
