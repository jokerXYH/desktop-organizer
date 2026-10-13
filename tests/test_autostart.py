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
