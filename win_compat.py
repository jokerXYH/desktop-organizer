# -*- coding: utf-8 -*-
"""Windows 10/11 兼容辅助：只调用有文档、失败可回退的系统接口。

设计原则：所有系统调用都包在 try/except 里，失败只返回 False。
宁可少一个视觉效果，也不让一个 DWM/Shell 调用把主程序带崩。
"""
import ctypes
import sys

APP_USER_MODEL_ID = "jokerXYH.DesktopOrganizer"


def windows_build() -> int:
    """返回 Windows 内部版本号（如 Win10 19045、Win11 22631/26200）；非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def is_windows_11() -> bool:
    """Windows 11 的版本号从 22000 起。"""
    return windows_build() >= 22000


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """设置任务栏/通知归属 ID，避免托盘图标与快捷方式被拆成两个应用。

    必须在创建 QApplication/任何窗口之前调用；失败不影响运行。
    """
    if sys.platform != "win32" or not app_id:
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(app_id))
        return True
    except Exception:
        return False
