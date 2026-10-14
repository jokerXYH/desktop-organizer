# -*- coding: utf-8 -*-
"""快捷启动：生成桌面 / 开始菜单的启动快捷方式（纯逻辑层，无 GUI 依赖）。

实现选择：调用系统 PowerShell 的 WScript.Shell COM 组件写 .lnk，而不是新增
pywin32 依赖——源码运行与打包成 exe 后都能用，依赖更少（requirements.txt 不变）。

三种启动形态共用一份逻辑：
    源码运行    -> "pythonw.exe" "…\\main.py"   （pythonw 无控制台黑窗）
    打包成 exe  -> exe 自身                       （见 config.app_dir 的说明）
"""
import os
import subprocess
import sys

import config as cfg
import organizer as org

SHORTCUT_NAME = "桌面分类助手.lnk"
APP_DESCRIPTION = "桌面分类助手"
# 快捷方式自带的组合键：即使程序已在托盘常驻，按它也会唤起控制面板
# （单实例机制会把"再次启动"转成"显示面板"，不会重复开进程）
SHORTCUT_HOTKEY = "Ctrl+Alt+D"


def desktop_dir() -> str:
    """桌面目录（复用 organizer 的注册表读取，兼容桌面被重定向到其他盘）。"""
    return org.get_desktop_path()


def start_menu_dir() -> str:
    """开始菜单「程序」目录：%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs。"""
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def launch_target() -> tuple:
    """返回 (可执行文件, 参数)：打包后指向 exe 自身，源码运行则用 pythonw 跑入口脚本。"""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    exe = sys.executable
    # 与开机自启同一套处理：python.exe -> pythonw.exe，避免弹控制台黑窗
    if os.path.basename(exe).lower() == "python.exe":
        pythonw = os.path.splitext(exe)[0] + "w.exe"
        if os.path.exists(pythonw):
            exe = pythonw
    entry = os.path.join(cfg.app_dir(), "main.py")
    return exe, f'"{entry}"'


def icon_path() -> str:
    """应用图标路径；图标缺失时返回空串（快捷方式退回目标程序自带图标）。"""
    path = os.path.join(cfg.app_dir(), "assets", "app_icon.ico")
    return path if os.path.exists(path) else ""


def _ps_literal(text: str) -> str:
    """转成 PowerShell 单引号字面量：内部单引号翻倍，避免中文/空格路径被拆断。"""
    return "'" + str(text).replace("'", "''") + "'"


def build_ps_command(link_path: str, target: str, arguments: str = "",
                     workdir: str = "", icon: str = "",
                     description: str = "", hotkey: str = "") -> str:
    """构造创建 .lnk 的 PowerShell 脚本（纯函数，便于测试）。"""
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        f"$lnk = $shell.CreateShortcut({_ps_literal(link_path)})",
        f"$lnk.TargetPath = {_ps_literal(target)}",
        f"$lnk.Arguments = {_ps_literal(arguments)}",
        f"$lnk.WorkingDirectory = {_ps_literal(workdir)}",
        f"$lnk.Description = {_ps_literal(description)}",
        # 图标/热键即使为空也显式赋值：CreateShortcut 读的是已存在的 .lnk，
        # 不赋值就会把旧值原样留下（实测重写开始菜单那份时旧热键被保留）
        f"$lnk.IconLocation = {_ps_literal(icon)}",
        f"$lnk.Hotkey = {_ps_literal(hotkey)}",
        "$lnk.Save()",
    ]
    return "; ".join(lines)


def create_shortcut(link_path: str, target: str, arguments: str = "",
                    workdir: str = "", icon: str = "", description: str = "",
                    hotkey: str = "") -> str:
    """创建一个 .lnk 并返回其路径；失败抛 RuntimeError（由调用方统一提示）。"""
    script = build_ps_command(link_path, target, arguments, workdir, icon,
                              description, hotkey)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"无法调用 PowerShell 创建快捷方式：{exc}") from exc
    if result.returncode != 0 or not os.path.exists(link_path):
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "未知错误"
        raise RuntimeError(f"创建快捷方式失败（{reason}）")
    return link_path


def create_launch_shortcuts(folders=None) -> list:
    """按目录批量创建启动快捷方式，返回创建成功的路径列表。

    :param folders: 目标目录列表；默认「桌面 + 开始菜单」
    逐个目录独立处理：开始菜单无写权限等情况不影响桌面那一份；全失败才报错。
    """
    target, arguments = launch_target()
    icon, workdir = icon_path(), cfg.app_dir()
    targets = (desktop_dir(), start_menu_dir()) if folders is None else folders
    created = []
    for index, folder in enumerate(targets):
        if not folder or not os.path.isdir(folder):
            continue
        link = os.path.join(folder, SHORTCUT_NAME)
        # 组合键只挂在第一份（默认即桌面那份）：两个 .lnk 抢同一个热键时
        # 系统只会认其中一个，挂两份没有意义还可能互相顶掉
        hotkey = SHORTCUT_HOTKEY if index == 0 else ""
        try:
            created.append(create_shortcut(
                link, target, arguments, workdir=workdir, icon=icon,
                description=APP_DESCRIPTION, hotkey=hotkey))
        except RuntimeError:
            continue                      # 该位置失败：继续下一个，结果由返回值反映
    if not created:
        raise RuntimeError("快捷方式创建失败：桌面与开始菜单都无法写入。")
    return created
