# -*- coding: utf-8 -*-
"""屏幕边界钳制纯逻辑测试：主子程序把格子收回屏幕内依赖这套计算。

真实案例（用户 settings.json）：屏幕可用高度 1040，但"图片/压缩包/安装包"
等格子存着 y=1360~3012 的位置——整块在屏幕下方，桌面上根本看不见。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402

from widgets import clamp_rect_to_screens, nearest_screen_rect  # noqa: E402

MAIN = QRect(0, 0, 1920, 1040)      # 主屏
RIGHT = QRect(1920, 0, 1920, 1040)  # 右侧副屏


def test_inside_is_untouched():
    """完全在屏内：原样返回（热路径每次调用也不产生副作用）。"""
    rect = QRect(100, 100, 330, 500)
    assert clamp_rect_to_screens(rect, [MAIN, RIGHT]) == rect


def test_partial_overflow_is_pulled_back():
    """右缘拖出屏幕：平移回屏内，尺寸不变。"""
    rect = QRect(1800, 100, 330, 500)          # 右边越界 210px
    assert clamp_rect_to_screens(rect, [MAIN]) == QRect(1590, 100, 330, 500)


def test_fully_below_snaps_to_nearest_screen():
    """整块在屏幕下方（真实越界案例）：收进最近的屏幕，可见可拖。"""
    rect = QRect(281, 3012, 330, 536)
    clamped = clamp_rect_to_screens(rect, [MAIN, RIGHT])
    assert clamped == QRect(281, 504, 330, 536)   # 贴底可见，x 保持不动


def test_off_top_on_right_screen_goes_to_right():
    """位于副屏正上方：应选择关系最近的副屏，而不是主屏。"""
    rect = QRect(2400, -400, 300, 300)
    clamped = clamp_rect_to_screens(rect, [MAIN, RIGHT])
    assert clamped == QRect(2400, 0, 300, 300)


def test_oversize_shrinks_and_pins_top_left():
    """窗口比屏幕还大：先收小到屏幕尺寸，贴屏幕左上。"""
    clamped = clamp_rect_to_screens(QRect(-50, -50, 4000, 3000), [MAIN])
    assert clamped == QRect(0, 0, 1920, 1040)


def test_negative_coordinates_clamped():
    """左上角拖出屏幕（y 为负）：收回到屏幕内。"""
    clamped = clamp_rect_to_screens(QRect(-200, -300, 330, 500), [MAIN])
    assert clamped == QRect(0, 0, 330, 500)


def test_no_screens_is_noop():
    """极端环境（无屏幕信息）：不做任何修改，保证程序不崩。"""
    rect = QRect(10, 10, 100, 100)
    assert clamp_rect_to_screens(rect, []) == rect
    assert nearest_screen_rect(rect, []) is None


def test_nearest_prefers_larger_overlap():
    """横跨两块屏：优先收进交叠面积更大的那块。"""
    rect = QRect(1700, 100, 500, 300)              # 200px 在主屏、300px 在副屏
    assert nearest_screen_rect(rect, [MAIN, RIGHT]) == RIGHT
