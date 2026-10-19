# -*- coding: utf-8 -*-
"""显示器感知的布局记忆：屏幕身份、相对坐标映射与重叠消解。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402

import layout_store  # noqa: E402


class FakeScreen:
    """QScreen 的最小替身：布局逻辑只依赖名称与可用区域。"""

    def __init__(self, name: str, rect: QRect):
        self._name = name
        self._rect = QRect(rect)

    def name(self):
        return self._name

    def geometry(self):
        return QRect(self._rect)

    def availableGeometry(self):
        return QRect(self._rect)


def test_screen_identity_is_stable_and_non_empty():
    screen = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))

    first = layout_store.screen_identity(screen)
    second = layout_store.screen_identity(screen)

    assert first == second
    assert "DISPLAY1" in first


def test_record_roundtrip_keeps_absolute_rect_and_screen():
    screen = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    rect = QRect(1182, 35, 330, 485)

    record = layout_store.make_layout_record(rect, screen)

    assert layout_store.record_rect(record) == rect
    assert layout_store.record_screen_name(record) == layout_store.screen_identity(screen)
    assert record["screenRect"] == "0,0,1920,1040"


def test_restore_exact_when_same_screen_and_geometry():
    screen = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    rect = QRect(1182, 35, 330, 485)
    record = layout_store.make_layout_record(rect, screen)

    result = layout_store.restore_layout(record, [screen])

    assert result is not None
    assert result.desired == rect
    assert result.rect == rect
    assert result.provisional is False
    assert result.changed is False


def test_missing_secondary_screen_is_provisional_and_keeps_saved_coordinates():
    """副屏未枚举时不能把 -1745 的格子改成主屏坐标并写回配置。"""
    primary = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    secondary = FakeScreen(r"\\.\DISPLAY2", QRect(-1920, 0, 1920, 1040))
    desired = QRect(-1745, 55, 330, 485)
    record = layout_store.make_layout_record(desired, secondary)

    result = layout_store.restore_layout(record, [primary])

    assert result is not None
    assert result.desired == desired
    assert result.rect is None
    assert result.provisional is True
    assert result.changed is False


def test_resolution_change_maps_position_relative_to_the_same_screen():
    old = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    new = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 2560, 1440))
    record = layout_store.make_layout_record(QRect(1182, 35, 330, 485), old)

    result = layout_store.restore_layout(record, [new])

    assert result is not None
    assert result.provisional is False
    assert result.changed is True
    assert result.rect == QRect(1576, 48, 330, 485)


def test_taskbar_height_change_keeps_absolute_position():
    """任务栏/可用区域小幅变化不应让所有格子按比例漂移。"""
    old = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    new = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1080))
    saved = QRect(1182, 35, 330, 485)
    record = layout_store.make_layout_record(saved, old)

    result = layout_store.restore_layout(record, [new])

    assert result is not None
    assert result.rect == saved
    assert result.provisional is False


def test_absurd_legacy_offscreen_position_falls_back_to_screen():
    primary = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    record = {"x": 99999, "y": 99999, "w": 330, "h": 485}

    result = layout_store.restore_layout(record, [primary])

    assert result is not None
    assert result.provisional is False
    assert result.changed is True
    assert result.rect == QRect(1590, 555, 330, 485)


def test_plausible_legacy_offscreen_position_is_kept_provisional():
    """旧配置没有屏幕身份，但 -1745 属于合理副屏范围，不能当成坏数据。"""
    primary = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1040))
    record = {"x": -1745, "y": 55, "w": 330, "h": 485}

    result = layout_store.restore_layout(record, [primary])

    assert result is not None
    assert result.provisional is True
    assert result.desired == QRect(-1745, 55, 330, 485)


def test_resolve_overlaps_separates_duplicate_rects():
    screen = FakeScreen(r"\\.\DISPLAY1", QRect(0, 0, 1000, 800))
    rects = [QRect(0, 0, 200, 200)] * 3

    result = layout_store.resolve_overlaps(rects, [screen], gap=10)

    assert len(result) == 3
    for index, rect in enumerate(result):
        for other in result[index + 1:]:
            assert not rect.intersects(other)
