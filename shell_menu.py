# -*- coding: utf-8 -*-
"""桌面右键菜单集成：Windows 桌面空白处右键「一键整理桌面」。

为什么用 HKCU 注册表而不是 COM 扩展：
- `DesktopBackground\\Shell` 的静态菜单项不需要管理员权限、不需要注册 DLL/COM；
  卸载 = 删键，行为透明、不惊动安全软件。
- 点击只是启动一条命令，由单实例机制把「整理」请求转交给常驻实例执行
  （命令末尾追加 --organize，见 main.cli_action 与 main._on_new_connection）。

系统适配：
- Windows 10：菜单项直接显示在桌面右键一级菜单；
- Windows 11：注册表类菜单项位于「显示更多选项」二级菜单（系统限制，除非做
  MSIX/COM 的现代菜单项，成本不成比例）。`platform_menu_hint()` 给出位置说明。
- 非 Windows：`is_supported()` 为 False，面板开关会给出提示。
- 双形态：源码运行指向 pythonw + main.py，打包后指向 exe 自身（与自启/快捷方式同规则）。
"""
import os
import sys
import winreg

import autostart
import config as cfg

# 注册表位置与名称（HKCU，无需管理员权限）
MENU_KEY_PATH = r"Software\Classes\DesktopBackground\Shell\MyDeskOrganizer"
MENU_LABEL = "一键整理桌面（桌面分类助手）"
MENU_ARG = "--organize"


def build_shell_command(exe_path: str, script_path: str = "") -> str:
    """构造菜单项命令行：与自启命令同一套规则（pythonw 替换 / 双形态），
    末尾追加 --organize 表示「启动后立即执行一键整理」。纯函数便于测试。"""
    return f"{autostart.build_autostart_command(exe_path, script_path)} {MENU_ARG}"


def _icon_path() -> str:
    """菜单项图标（应用图标）；缺失时返回空串（系统使用默认图标）。"""
    path = os.path.join(cfg.app_dir(), "assets", "app_icon.ico")
    return path if os.path.exists(path) else ""


def is_supported() -> bool:
    """桌面右键菜单仅 Windows 可用。"""
    return os.name == "nt"


def platform_menu_hint() -> str:
    """当前系统下菜单项的位置说明（展示在面板工具提示里）。"""
    try:
        build = sys.getwindowsversion().build
    except AttributeError:
        return ""
    if build >= 22000:
        return "Windows 11：位于桌面右键的「显示更多选项」二级菜单"
    return "位于桌面空白处的右键菜单"


def is_registered() -> bool:
    """查询菜单项是否存在（只读 HKCU）。"""
    if not is_supported():
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MENU_KEY_PATH)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def register(script_path: str) -> None:
    """写入菜单项：名称 + 图标 + command 子键（失败抛 OSError 由面板提示）。"""
    if not is_supported():
        raise OSError("桌面右键菜单仅支持 Windows")
    command = build_shell_command(sys.executable, script_path)
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, MENU_KEY_PATH)
    try:
        winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, MENU_LABEL)
        icon = _icon_path()
        if icon:
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon)
    finally:
        winreg.CloseKey(key)
    cmd_key = winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, MENU_KEY_PATH + r"\command")
    try:
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(cmd_key)


def unregister() -> None:
    """删除菜单项（键本来就不存在时视为成功）。"""
    for path in (MENU_KEY_PATH + r"\command", MENU_KEY_PATH):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass


def _quoted_tokens(command: str) -> list:
    """提取命令行中带引号的部分（构建器总是给路径加引号）。"""
    parts = str(command).split('"')
    return [parts[i] for i in range(1, len(parts), 2) if parts[i].strip()]


def command_targets_exist(command: str) -> bool:
    """命令指向的可执行文件/脚本是否都还存在（用于判断菜单项是否已失效）。"""
    tokens = _quoted_tokens(command)
    if not tokens:
        return False
    return all(os.path.exists(token) for token in tokens)


def refresh_if_stale(script_path: str) -> bool:
    """菜单项已注册但指向的文件不存在（目录被移动过）时，自动改写到当前路径。

    返回是否发生了改写；未注册、命令仍有效或读取失败时都不动作。
    """
    if not is_registered():
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, MENU_KEY_PATH + r"\command")
        stored, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
    except OSError:
        return False
    if stored == build_shell_command(sys.executable, script_path):
        return False
    if command_targets_exist(stored):
        return False
    register(script_path)
    return True
