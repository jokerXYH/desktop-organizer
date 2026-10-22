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
AUTOSTART_ARG = "--autostart"


def build_autostart_command(exe_path: str, script_path: str = "",
                            extra_args=None) -> str:
    """构造自启命令行字符串（纯函数，便于测试）。

    :param exe_path: Python 解释器路径；打包成 exe 后传 exe 自身路径
    :param script_path: 入口脚本绝对路径；打包成 exe 后为空（exe 自己就是入口）
    :return: 如 '"C:\\Python\\pythonw.exe" "D:\\Tools\\main.py"'，或 '"D:\\App\\桌面分类助手.exe"'
    """
    exe = exe_path
    # 自启需要无窗口运行：若当前是 python.exe 且同目录存在 pythonw.exe 则替换
    if os.path.basename(exe_path).lower() == "python.exe":
        pythonw = os.path.splitext(exe_path)[0] + "w.exe"
        if os.path.exists(pythonw):
            exe = pythonw
    parts = [f'"{exe}"']
    if script_path:
        parts.append(f'"{script_path}"')
    parts.extend(str(arg) for arg in (extra_args or []))
    return " ".join(parts)


def _normalize_command(text: str) -> str:
    return " ".join(str(text or "").strip().split()).casefold()


def command_matches(command: str, expected: str) -> bool:
    """比较两条 Run 命令；忽略大小写与多余空白。"""
    return _normalize_command(command) == _normalize_command(expected)


def get_autostart_command() -> str:
    """读取当前用户 Run 项的命令；不存在或读取失败返回空串。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH,
                            0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value or "")
    except OSError:
        return ""


def get_autostart_enabled() -> bool:
    """查询注册表中是否已存在本应用的开机自启项。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def _open_run_key(access: int):
    """打开（必要时创建）HKCU Run 键；用 CreateKeyEx 兼容极少数键被删的环境。"""
    return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)


def set_autostart(script_path: str, enable: bool,
                  extra_args=None) -> None:
    """写入或删除 HKCU Run 自启项，并在写入后回读校验。

    :param script_path: 入口脚本绝对路径（写启用项时使用；打包版传空串）
    :param enable: True=开启自启，False=关闭自启
    :raises OSError: 注册表被策略/权限拒绝，或写入后回读不一致
    """
    if enable:
        command = build_autostart_command(
            sys.executable, script_path, extra_args or [AUTOSTART_ARG])
        with _open_run_key(winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        # 回读校验：注册表被安全软件/组策略拦截时，SetValueEx 可能"成功"但值没落地
        if not command_matches(get_autostart_command(), command):
            raise OSError("自启项写入后回读不一致（可能被安全软件拦截）")
    else:
        try:
            with _open_run_key(winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass  # 本来就没有自启项，视为成功


def sync_autostart(script_path: str) -> bool:
    """已开启自启时，把过期路径/旧命令修正为当前程序。

    返回 True 表示注册表被更新。程序换目录、源码版换成 exe 版、
    或旧版本没有 --autostart 参数时，登录后会静默失效，这里自动修复。
    """
    if not get_autostart_enabled():
        return False
    expected = build_autostart_command(
        sys.executable, script_path, [AUTOSTART_ARG])
    current = get_autostart_command()
    if command_matches(current, expected):
        return False
    with _open_run_key(winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, expected)
    return True
