# -*- coding: utf-8 -*-
"""shortcut 模块单元测试：快捷方式脚本构造、启动目标选择、批量创建与容错。

真机操作（调用 PowerShell 写 .lnk）由 monkeypatch 拦截，测试不触碰真实桌面。
"""
import sys

import pytest

import shortcut


# ---------- PowerShell 脚本构造 ----------

def test_build_ps_command_quotes_and_escapes():
    """路径用单引号包裹；内含单引号时翻倍转义（PowerShell 字面量规则）。"""
    script = shortcut.build_ps_command(
        r"C:\Users\O'Brien\Desktop\桌面分类助手.lnk", r"C:\Py\pythonw.exe",
        arguments=r'"D:\x\main.py"', workdir=r"D:\x",
        description="桌面分类助手", hotkey="Ctrl+Alt+D")

    assert "'C:\\Users\\O''Brien\\Desktop\\桌面分类助手.lnk'" in script
    assert "$lnk.TargetPath = 'C:\\Py\\pythonw.exe'" in script
    assert "$lnk.Hotkey = 'Ctrl+Alt+D'" in script
    assert script.endswith("$lnk.Save()")


def test_build_ps_command_clears_optional_fields_when_empty():
    """图标/热键为空时也显式赋空值。

    CreateShortcut 读的是"已存在的 .lnk"：不赋值就会把旧值原样保留，
    导致重写开始菜单那份时旧热键一直残留（真机实测踩过）。
    """
    script = shortcut.build_ps_command("/tmp/a.lnk", "/tmp/app.exe")

    assert "$lnk.IconLocation = ''" in script
    assert "$lnk.Hotkey = ''" in script


# ---------- 启动目标选择 ----------

def test_launch_target_prefers_pythonw(monkeypatch):
    """源码运行：优先用 pythonw.exe（无控制台黑窗）跑入口脚本。"""
    monkeypatch.setattr(shortcut.sys, "executable", r"C:\Py\python.exe")
    monkeypatch.setattr(shortcut.os.path, "exists", lambda _p: True)

    target, arguments = shortcut.launch_target()

    assert target == r"C:\Py\pythonw.exe"
    assert "main.py" in arguments and arguments.startswith('"')


def test_launch_target_frozen_points_to_exe(monkeypatch):
    """打包成 exe：直接指向 exe 自身，不带脚本参数。"""
    monkeypatch.setattr(shortcut.sys, "frozen", True, raising=False)
    monkeypatch.setattr(shortcut.sys, "executable", r"D:\App\桌面分类助手.exe")

    target, arguments = shortcut.launch_target()

    assert target == r"D:\App\桌面分类助手.exe"
    assert arguments == ""


# ---------- 批量创建与容错 ----------

def test_create_launch_shortcuts_creates_every_existing_folder(tmp_path, monkeypatch):
    """桌面与开始菜单都存在时应各创建一份，返回两条路径。"""
    desktop, start_menu = tmp_path / "desktop", tmp_path / "programs"
    desktop.mkdir()
    start_menu.mkdir()
    created_links = []

    def fake_create(link_path, *_args, **_kwargs):
        created_links.append(link_path)
        return link_path

    monkeypatch.setattr(shortcut, "create_shortcut", fake_create)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: str(desktop))
    monkeypatch.setattr(shortcut, "start_menu_dir", lambda: str(start_menu))

    result = shortcut.create_launch_shortcuts()

    assert result == [str(desktop / shortcut.SHORTCUT_NAME),
                      str(start_menu / shortcut.SHORTCUT_NAME)]
    assert created_links == result


def test_create_launch_shortcuts_skips_missing_folder(tmp_path, monkeypatch):
    """某个位置不存在（如开始菜单目录缺失）时只创建另一份，不报错。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    monkeypatch.setattr(shortcut, "create_shortcut",
                        lambda link_path, *_a, **_k: link_path)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: str(desktop))
    monkeypatch.setattr(shortcut, "start_menu_dir",
                        lambda: str(tmp_path / "not_exist"))

    result = shortcut.create_launch_shortcuts()

    assert result == [str(desktop / shortcut.SHORTCUT_NAME)]


def test_create_launch_shortcuts_raises_when_all_fail(tmp_path, monkeypatch):
    """两个位置都写入失败时应抛 RuntimeError，供面板提示用户。"""
    desktop = tmp_path / "desktop"
    desktop.mkdir()

    def fail(*_args, **_kwargs):
        raise RuntimeError("权限不足")

    monkeypatch.setattr(shortcut, "create_shortcut", fail)
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: str(desktop))
    monkeypatch.setattr(shortcut, "start_menu_dir", lambda: str(desktop))

    with pytest.raises(RuntimeError):
        shortcut.create_launch_shortcuts()


def test_icon_path_empty_when_missing(tmp_path, monkeypatch):
    """图标不存在时返回空串，让快捷方式退回目标程序自带图标。"""
    monkeypatch.setattr(shortcut.cfg, "app_dir", lambda: str(tmp_path))
    assert shortcut.icon_path() == ""


def test_create_shortcut_raises_on_powershell_failure(tmp_path, monkeypatch):
    """PowerShell 返回非零时必须抛 RuntimeError，交由面板提示用户。"""
    class _FailedResult:
        returncode, stderr = 1, "拒绝访问。"

    monkeypatch.setattr(shortcut.subprocess, "run",
                        lambda *_a, **_k: _FailedResult())

    with pytest.raises(RuntimeError):
        shortcut.create_shortcut(str(tmp_path / "x.lnk"), "some.exe")
