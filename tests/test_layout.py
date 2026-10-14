# -*- coding: utf-8 -*-
"""外观布局纯函数测试：整齐排列（plan_tidy_layout）与新格子落点（plan_default_placement）。

坐标取自一次真实桌面布局的"凌乱现场"（1920×1040 可用区）：
列内左右错位 15px、列间距 7/20/2px、行间距 13/1/-2/-2px、右缘溢出屏幕 15px。
整理函数必须把它归一为「统一 16px 间距 + 列边对齐 + 行顶对齐 + 不越界」的规整栅格。
"""
from PySide6.QtCore import QPoint, QRect

from widgets import (SCREEN_MARGIN, WIDGET_GAP, plan_default_placement,
                     plan_tidy_layout)

SCREEN = QRect(0, 0, 1920, 1040)


def _r(x, y, w, h):
    return QRect(x, y, w, h)


# 与 settings.json 真机数据同形的 8 个格子（4 列 × 2 行）
REAL_CASE = [
    _r(1608, 21, 310, 414),    # 文档
    _r(642, 448, 330, 540),    # 图片
    _r(1302, 18, 304, 429),    # 压缩包
    _r(1302, 448, 304, 538),   # 安装包
    _r(979, 18, 303, 432),     # 代码
    _r(979, 448, 303, 537),    # 快捷方式
    _r(642, 18, 330, 432),     # 其他
    _r(1623, 448, 312, 536),   # 文件夹
]


# ---------- plan_tidy_layout ----------

def test_tidy_real_case_produces_even_grid():
    """真实凌乱案例应归一为：4 列左缘对齐、列/行间距统一 16px、右缘不越界。"""
    result = plan_tidy_layout(REAL_CASE, SCREEN)

    # 保持输入顺序：第 0 个（文档）与第 7 个（文件夹）的最终位置
    assert result[0] == QRect(1592, 18, 312, 432)
    assert result[6] == QRect(607, 18, 330, 432)      # 其他
    assert result[1] == QRect(607, 466, 330, 540)     # 图片
    assert result[7] == QRect(1592, 466, 312, 540)    # 文件夹

    # 列：按 x 分组后列内左缘完全一致、列间统一 16px
    columns = {}
    for rect in result:
        columns.setdefault(rect.x(), []).append(rect)
    xs = sorted(columns)
    assert xs == [607, 953, 1272, 1592]
    for left, right in zip(xs, xs[1:]):
        col_right = max(r.x() + r.width() for r in columns[left])
        assert right - col_right == WIDGET_GAP

    # 行：同排上缘一致，行间距 = 行高 + 16px
    tops = sorted({r.y() for r in result})
    assert tops == [18, 466]
    assert tops[1] - tops[0] == 432 + WIDGET_GAP

    # 右缘贴到统一外边距、不越界
    assert max(r.x() + r.width() for r in result) == \
        SCREEN.x() + SCREEN.width() - SCREEN_MARGIN

    # 任意两格不重叠
    for i, rect_a in enumerate(result):
        for rect_b in result[i + 1:]:
            assert not rect_a.intersects(rect_b)


def test_tidy_layout_is_idempotent():
    """整理结果再整理一次必须完全不变（幂等）。"""
    once = plan_tidy_layout(REAL_CASE, SCREEN)
    twice = plan_tidy_layout(once, SCREEN)
    assert twice == once


def test_tidy_keeps_large_height_differences():
    """高度差异过大（收起条、超长列表）不强行等高，只对齐顶部与间距。"""
    rects = [_r(0, 0, 200, 200), _r(220, 0, 200, 600)]
    result = plan_tidy_layout(rects, SCREEN)

    assert [r.height() for r in result] == [200, 600]
    assert result[0].x() == SCREEN_MARGIN                  # 左缘归到统一外边距
    assert result[1].x() == SCREEN_MARGIN + 200 + WIDGET_GAP
    assert result[0].y() == result[1].y() == SCREEN_MARGIN  # 行顶对齐到统一外边距


def test_tidy_respects_fixed_size_rects():
    """固定尺寸（如已收起的格子）不参与尺寸统一。"""
    rects = [_r(0, 0, 200, 200), _r(220, 0, 200, 300)]
    result = plan_tidy_layout(rects, SCREEN, fixed=[False, True])

    assert [r.height() for r in result] == [200, 300]


def test_tidy_clamps_left_edge_to_margin():
    """整块贴左缘时归到统一外边距。"""
    result = plan_tidy_layout([_r(2, 100, 200, 200)], SCREEN)
    assert result[0] == QRect(SCREEN_MARGIN, 100, 200, 200)


def test_tidy_empty_returns_empty():
    """无格子时返回空列表，不抛异常。"""
    assert plan_tidy_layout([], SCREEN) == []


# ---------- plan_default_placement ----------

def test_default_placement_top_right_when_empty():
    """桌面没有格子时：贴右上角、留统一外边距。"""
    pos = plan_default_placement([], (304, 429), SCREEN)
    assert pos == QPoint(SCREEN.x() + SCREEN.width() - SCREEN_MARGIN - 304,
                         SCREEN.y() + SCREEN_MARGIN)


def test_default_placement_below_right_column():
    """已有右侧列时：贴到该列下缘、右缘对齐、留统一间距。"""
    rects = [_r(1600, 16, 304, 300)]
    pos = plan_default_placement(rects, (304, 200), SCREEN)
    assert pos == QPoint(1600, 16 + 300 + WIDGET_GAP)


def test_default_placement_opens_left_column_on_overflow():
    """右侧列已到底：在整块左侧开新列，顶部与整块对齐。"""
    rects = [_r(1608, 21, 310, 414), _r(1623, 448, 312, 536),
             _r(642, 18, 330, 432), _r(642, 448, 330, 540)]
    pos = plan_default_placement(rects, (304, 429), SCREEN)
    assert pos == QPoint(642 - WIDGET_GAP - 304, 18)


def test_default_placement_scan_fallback():
    """右侧列与整块左侧都放不下：贴左缘自上而下扫描空位。"""
    rects = [_r(16, 16, 304, 1000), _r(1600, 16, 304, 1000)]
    pos = plan_default_placement(rects, (304, 200), SCREEN)
    assert pos == QPoint(16, 16 + 1000 + WIDGET_GAP)
