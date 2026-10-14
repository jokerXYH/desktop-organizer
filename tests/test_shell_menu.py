# -*- coding: utf-8 -*-
"""shell_menu 模块单元测试：桌面右键菜单的命令行构造与解析（不触碰真实注册表）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shell_menu


def test_build_command_quotes_and_appends_organize(tmp_path):
    """路径加引号；python.exe → pythonw（同目录存在时）；末尾追加 --organize。"""
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")

    cmd = shell_menu.build_shell_command(
        str(tmp_path / "python.exe"), str(tmp_path / "main.py"))

    assert cmd == (f'"{tmp_path / "pythonw.exe"}" '
                   f'"{tmp_path / "main.py"}" {shell_menu.MENU_ARG}')


def test_build_command_keeps_python_and_quotes_spaced_path(tmp_path):
    """pythonw 不存在时保留原解释器；含空格/中文的脚本路径仍带引号。"""
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    script = tmp_path / "小 设计" / "main.py"

    cmd = shell_menu.build_shell_command(str(tmp_path / "python.exe"), str(script))

    assert str(tmp_path / "python.exe") in cmd
    assert f'"{script}"' in cmd
    assert cmd.endswith(shell_menu.MENU_ARG)


def test_build_command_frozen_exe():
    """打包形态：只写 exe 自身 + --organize（无脚本参数）。"""
    cmd = shell_menu.build_shell_command(r"D:\App\桌面分类助手.exe")
    assert cmd == rf'"D:\App\桌面分类助手.exe" {shell_menu.MENU_ARG}'


def test_quoted_tokens_extracts_paths():
    """从注册表命令行中提取带引号的路径（用于判断菜单项是否失效）。"""
    command = '"D:\\py\\pythonw.exe" "F:\\小设计\\main.py" --organize'
    assert shell_menu._quoted_tokens(command) == [
        "D:\\py\\pythonw.exe", "F:\\小设计\\main.py"]
    assert shell_menu._quoted_tokens('"D:\\x.exe" --organize') == ["D:\\x.exe"]
    assert shell_menu._quoted_tokens("nothing here") == []


def test_command_targets_exist(tmp_path):
    """命令指向的文件都在 → True；缺任一 → False；无引号路径 → False。"""
    exe = tmp_path / "pythonw.exe"
    script = tmp_path / "main.py"
    exe.write_text("", encoding="utf-8")
    script.write_text("", encoding="utf-8")

    assert shell_menu.command_targets_exist(f'"{exe}" "{script}" --organize')
    assert not shell_menu.command_targets_exist(
        f'"{exe}" "{tmp_path / "gone.py"}" --organize')
    assert not shell_menu.command_targets_exist("pythonw main.py")


def test_platform_menu_hint_mentions_location():
    """位置提示非空且说明在桌面右键（Win10 一级 / Win11 显示更多选项）。"""
    hint = shell_menu.platform_menu_hint()
    assert "桌面" in hint and "右键" in hint


def test_menu_metadata_constants():
    """菜单项常量：触发参数 --organize、注册表位于 DesktopBackground。"""
    assert shell_menu.MENU_ARG == "--organize"
    assert "DesktopBackground" in shell_menu.MENU_KEY_PATH
    assert "桌面分类助手" in shell_menu.MENU_LABEL


def test_cli_action_parses_organize_flag():
    """命令行 --organize 触发一键整理模式；默认是唤起面板。"""
    import main

    assert main.cli_action(["main.py"]) == "show"
    assert main.cli_action(["main.py", "--organize"]) == "organize"
