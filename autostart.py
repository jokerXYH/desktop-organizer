# -*- coding: utf-8 -*-
"""开机自启：通过 HKCU 注册表 Run 键实现查询与开关。

命令行统一使用 pythonw.exe（无控制台黑窗），路径全部加引号，
避免含空格路径导致解析失败。
"""
import os
import sys
import winreg

# 当前用户的开机启动注册表路径与本应用的键值名
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MyDeskOrganizer"


def build_autostart_command(exe_path: str, script_path: str) -> str:
    """构造自启命令行字符串（纯函数，便于测试）。

    :param exe_path: Python 解释器路径
    :param script_path: 入口脚本绝对路径
    :return: 如 '"C:\\Python\\pythonw.exe" "D:\\Tools\\main.py"'
    """
    exe = exe_path
    # 自启需要无窗口运行：若当前是 python.exe 且同目录存在 pythonw.exe 则替换
    if os.path.basename(exe_path).lower() == "python.exe":
        pythonw = os.path.splitext(exe_path)[0] + "w.exe"
        if os.path.exists(pythonw):
            exe = pythonw
    return f'"{exe}" "{script_path}"'


def get_autostart_enabled() -> bool:
    """查询注册表中是否已存在本应用的开机自启项。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH)
        winreg.QueryValueEx(key, VALUE_NAME)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def set_autostart(script_path: str, enable: bool) -> None:
    """写入或删除 HKCU Run 自启项。

    :param script_path: 入口脚本绝对路径（写启用项时使用）
    :param enable: True=开启自启，False=关闭自启
    """
    if enable:
        command = build_autostart_command(sys.executable, script_path)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH,
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)
    else:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH,
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, VALUE_NAME)
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass  # 本来就没有自启项，视为成功
