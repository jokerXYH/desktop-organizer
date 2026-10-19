# -*- coding: utf-8 -*-
"""autostart 模块单元测试：自启命令行构造（不触碰真实注册表）。"""
import os

import autostart


def test_build_command_quotes_paths(tmp_path):
    """路径必须加引号，防止含空格路径解析失败。"""
    cmd = autostart.build_autostart_command(
        str(tmp_path / "pythonw.exe"), str(tmp_path / "main.py"))
    assert cmd == f'"{tmp_path / "pythonw.exe"}" "{tmp_path / "main.py"}"'


def test_build_command_replaces_python_with_pythonw(tmp_path):
    """python.exe 且同目录存在 pythonw.exe 时应替换为无窗口版本。"""
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")

    cmd = autostart.build_autostart_command(
        str(tmp_path / "python.exe"), r"E:\x\main.py")

    assert cmd.startswith(f'"{tmp_path / "pythonw.exe"}"')


def test_build_command_keeps_pythonw_and_missing_pythonw(tmp_path):
    """已是 pythonw 直接使用；pythonw 不存在时保留原解释器。"""
    (tmp_path / "python.exe").write_text("", encoding="utf-8")  # 无 pythonw

    # pythonw 本身不被替换
    cmd = autostart.build_autostart_command(
        str(tmp_path / "pythonw.exe"), r"E:\x\main.py")
    assert str(tmp_path / "pythonw.exe") in cmd

    # pythonw 缺失时保持 python.exe
    cmd2 = autostart.build_autostart_command(
        str(tmp_path / "python.exe"), r"E:\x\main.py")
    assert str(tmp_path / "python.exe") in cmd2


def test_build_command_frozen_exe_without_script():
    """打包成 exe 后自启项只写 exe 自身，不带脚本参数。

    否则会指向 __file__（onefile 的临时解包目录），下次开机自启必然失败。
    """
    cmd = autostart.build_autostart_command(r"D:\App\桌面分类助手.exe")
    assert cmd == r'"D:\App\桌面分类助手.exe"'


def test_build_command_adds_autostart_flag(tmp_path):
    """Win10 自启项带 --autostart：主程序据此延迟恢复布局且不弹面板。"""
    cmd = autostart.build_autostart_command(
        str(tmp_path / "pythonw.exe"), str(tmp_path / "main.py"),
        extra_args=[autostart.AUTOSTART_ARG])
    assert cmd.endswith(
        f'"{tmp_path / "main.py"}" {autostart.AUTOSTART_ARG}')


def test_command_matches_ignores_case_and_spacing():
    """旧注册表命令可能大小写/空白不同，应视为同一条命令。"""
    expected = r'"D:\App\桌面分类助手.exe" --autostart'
    assert autostart.command_matches(
        '  "d:\\app\\桌面分类助手.exe"   --autostart  ', expected)


def test_command_matches_detects_stale_path():
    expected = r'"F:\新目录\main.py" --autostart'
    assert not autostart.command_matches(
        r'"E:\旧目录\main.py" --autostart', expected)
