# -*- coding: utf-8 -*-
"""显示器感知的窗口布局记忆。

旧版只保存绝对坐标：开机时副屏还没被 Windows 枚举，坐标会被当成
"屏幕外"钳回主屏并覆盖配置，副屏稍后出现也回不到原位。这里把
屏幕身份、屏幕可用区域和绝对坐标一起保存，并提供三种恢复结果：

- 同一屏幕且分辨率未变：原样恢复；
- 同一屏幕但分辨率/任务栏变化：按相对位置映射；
- 屏幕暂时缺失：保留目标坐标，调用方临时安置，不覆盖配置。
"""
from dataclasses import dataclass
from typing import Iterable, Optional

from PySide6.QtCore import QRect

# 旧配置没有屏幕身份时，用这个距离区分"副屏坐标"与"明显损坏的坐标"。
MAX_PLAUSIBLE_OFFSCREEN = 5000


@dataclass
class LayoutResolution:
    """一次布局恢复结果。rect=None 表示目标屏未就绪，由调用方临时安置。"""

    desired: QRect
    rect: Optional[QRect]
    provisional: bool
    changed: bool
    screen: object = None


def screen_identity(screen) -> str:
    """返回稳定的屏幕身份；Windows 上 QScreen.name() 即 DISPLAY1/DISPLAY2。"""
    name = str(getattr(screen, "name", lambda: "")() or "screen")
    model = str(getattr(screen, "model", lambda: "")() or "")
    serial = str(getattr(screen, "serialNumber", lambda: "")() or "")
    return f"{name}|{model}|{serial}"


def _screen_rect(screen) -> QRect:
    return QRect(screen.availableGeometry())


def _format_rect(rect: QRect) -> str:
    return f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}"


def _parse_rect(text) -> Optional[QRect]:
    try:
        parts = [int(part) for part in str(text).split(",")]
    except (TypeError, ValueError):
        return None
    if len(parts) != 4:
        return None
    return QRect(parts[0], parts[1], parts[2], parts[3])


def make_layout_record(rect: QRect, screen) -> dict:
    """把窗口矩形与所在屏幕序列化为可持久化记录。"""
    rect = QRect(rect)
    return {
        "x": rect.x(),
        "y": rect.y(),
        "w": rect.width(),
        "h": rect.height(),
        "screen": screen_identity(screen) if screen is not None else "",
        "screenRect": _format_rect(_screen_rect(screen))
        if screen is not None else "",
    }


def record_rect(record) -> Optional[QRect]:
    if not isinstance(record, dict):
        return None
    try:
        return QRect(int(record["x"]), int(record["y"]),
                     int(record["w"]), int(record["h"]))
    except (KeyError, TypeError, ValueError):
        return None


def record_screen_name(record) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("screen", "") or "")


def find_screen_by_identity(identity: str, screens: Iterable):
    for screen in screens:
        if screen_identity(screen) == identity:
            return screen
    return None


def _contains_fully(screen_rect: QRect, rect: QRect) -> bool:
    return screen_rect.intersected(rect) == rect


def _nearest_screen(rect: QRect, screens: list):
    best = None
    best_key = None
    for screen in screens:
        area = screen.availableGeometry()
        inter = area.intersected(rect)
        overlap = max(0, inter.width()) * max(0, inter.height())
        dx = max(area.x() - rect.x(),
                 rect.x() - (area.x() + area.width()), 0)
        dy = max(area.y() - rect.y(),
                 rect.y() - (area.y() + area.height()), 0)
        key = (overlap, -(dx * dx + dy * dy))
        if best_key is None or key > best_key:
            best, best_key = screen, key
    return best


def _clamp_to_screen(rect: QRect, screen) -> QRect:
    area = screen.availableGeometry()
    width = min(rect.width(), area.width())
    height = min(rect.height(), area.height())
    x = min(max(rect.x(), area.x()), area.x() + area.width() - width)
    y = min(max(rect.y(), area.y()), area.y() + area.height() - height)
    return QRect(x, y, width, height)


def _distance_to_virtual_desktop(rect: QRect, screens: list) -> int:
    if not screens:
        return 0
    virtual = QRect(screens[0].availableGeometry())
    for screen in screens[1:]:
        virtual = virtual.united(screen.availableGeometry())
    dx = max(virtual.x() - rect.x(),
             rect.x() - (virtual.x() + virtual.width()), 0)
    dy = max(virtual.y() - rect.y(),
             rect.y() - (virtual.y() + virtual.height()), 0)
    return dx + dy


def _map_to_new_geometry(saved: QRect, old_area: QRect,
                         new_area: QRect) -> QRect:
    """保留窗口在屏幕内的相对位置，适配分辨率/任务栏变化。"""
    rel_x = (saved.x() - old_area.x()) / max(1, old_area.width())
    rel_y = (saved.y() - old_area.y()) / max(1, old_area.height())
    width = min(saved.width(), new_area.width())
    height = min(saved.height(), new_area.height())
    x = new_area.x() + round(rel_x * new_area.width())
    y = new_area.y() + round(rel_y * new_area.height())
    x = min(max(x, new_area.x()), new_area.x() + new_area.width() - width)
    y = min(max(y, new_area.y()), new_area.y() + new_area.height() - height)
    return QRect(x, y, width, height)


def _geometry_changed_significantly(old_area: QRect, new_area: QRect) -> bool:
    """区分"任务栏显隐/可用区域微调"与真正的分辨率/缩放变化。

    前者保留绝对坐标，避免每次登录都让整桌格子轻微漂移；后者才按相对
    位置缩放。
    """
    return (abs(old_area.x() - new_area.x()) > 64
            or abs(old_area.y() - new_area.y()) > 64
            or abs(old_area.width() - new_area.width()) > 64
            or abs(old_area.height() - new_area.height()) > 96)


def restore_layout(record, screens) -> Optional[LayoutResolution]:
    """根据布局记录与当前屏幕给出恢复方案。"""
    saved = record_rect(record)
    screens = list(screens or [])
    if saved is None or not screens:
        return None
    identity = record_screen_name(record)

    if identity:
        screen = find_screen_by_identity(identity, screens)
        if screen is not None:
            new_area = screen.availableGeometry()
            old_area = _parse_rect(record.get("screenRect", ""))
            if old_area is None or old_area == new_area:
                return LayoutResolution(saved, QRect(saved), False, False, screen)
            if not _geometry_changed_significantly(old_area, new_area):
                clamped = _clamp_to_screen(saved, screen)
                return LayoutResolution(
                    saved, clamped, False, clamped != saved, screen)
            mapped = _map_to_new_geometry(saved, old_area, new_area)
            return LayoutResolution(saved, mapped, False, True, screen)

        containing = next(
            (item for item in screens
             if _contains_fully(item.availableGeometry(), saved)), None)
        if containing is not None:
            return LayoutResolution(saved, QRect(saved), False, False, containing)
        nearest = _nearest_screen(saved, screens)
        if nearest is not None and nearest.availableGeometry().intersects(saved):
            return LayoutResolution(saved, _clamp_to_screen(saved, nearest),
                                    True, False, nearest)
        return LayoutResolution(saved, None, True, False, None)

    # 旧配置：能完整落在当前屏幕就原样恢复。
    containing = next(
        (item for item in screens
         if _contains_fully(item.availableGeometry(), saved)), None)
    if containing is not None:
        return LayoutResolution(saved, QRect(saved), False, False, containing)

    # 明显损坏的坐标（99999）回退；合理的副屏坐标先临时安置。
    if _distance_to_virtual_desktop(saved, screens) > MAX_PLAUSIBLE_OFFSCREEN:
        nearest = _nearest_screen(saved, screens)
        return LayoutResolution(saved, _clamp_to_screen(saved, nearest),
                                False, True, nearest)

    nearest = _nearest_screen(saved, screens)
    if nearest is not None and nearest.availableGeometry().intersects(saved):
        return LayoutResolution(saved, _clamp_to_screen(saved, nearest),
                                True, False, nearest)
    return LayoutResolution(saved, None, True, False, None)


def _overlap_ratio(first: QRect, second: QRect) -> float:
    inter = first.intersected(second)
    if inter.isEmpty():
        return 0.0
    overlap = inter.width() * inter.height()
    smaller = min(first.width() * first.height(),
                  second.width() * second.height())
    return overlap / max(1, smaller)


def resolve_overlaps(rects, screens, gap: int = 16):
    """消解明显重叠（超过较小窗口 40%），返回与输入等长的新矩形列表。

    只用于启动恢复/整齐排列等"期望规整"的场景；轻微叠放属于用户的自由布局，
    不会被强行拆开。
    """
    screens = list(screens or [])
    result = [QRect(rect) for rect in rects]
    if not result or not screens:
        return result

    for index in range(1, len(result)):
        rect = result[index]
        for _ in range(40):
            blocked = [other for other in result[:index]
                       if _overlap_ratio(rect, other) > 0.4]
            if not blocked:
                break
            screen = _nearest_screen(rect, screens)
            area = screen.availableGeometry()
            next_y = max(other.y() + other.height() + gap
                         for other in blocked)
            candidate = QRect(rect.x(), next_y, rect.width(), rect.height())
            if candidate.y() + candidate.height() > area.y() + area.height():
                next_x = max(other.x() + other.width() + gap
                             for other in blocked)
                candidate = QRect(next_x, area.y(), rect.width(), rect.height())
            clamped = _clamp_to_screen(candidate, screen)
            if clamped == rect:
                break
            rect = clamped
        result[index] = rect
    return result
