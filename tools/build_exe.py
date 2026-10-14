# -*- coding: utf-8 -*-
"""把项目打包成独立 exe（PyInstaller），双击即用、目标机器无需安装 Python。

用法：
    python tools/build_exe.py             # 无控制台黑窗（推荐，与快捷方式一致）
    python tools/build_exe.py --console   # 保留控制台，便于排查启动崩溃
输出：
    dist/桌面分类助手_v<版本号>.exe（版本号取自 config.APP_VERSION）

要点：
    --onefile    单文件；收纳库默认生成在 exe 同级的"收纳库"目录
                 （见 config.app_dir：不能用 __file__，那是临时解包目录）；
    pypinyin     词库是数据文件，必须 --collect-data 一起打包，否则拼音搜索失效；
    tkinter      本项目从不使用，排除掉避免被误打包。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as cfg  # noqa: E402  版本号与源码保持同步

APP_NAME = f"桌面分类助手_v{cfg.APP_VERSION}"   # 产物文件名自带版本号


def build_command(console: bool = False) -> list:
    """构造 PyInstaller 命令行（纯函数，便于测试与打印核对）。"""
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", APP_NAME,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--collect-data", "pypinyin",       # 拼音词库是数据文件，需一并打包
        "--exclude-module", "tkinter",      # 本项目从未使用，排除以减小体积
        "--console" if console else "--windowed",
    ]
    icon = os.path.join(ROOT, "assets", "app_icon.ico")
    if os.path.exists(icon):
        command += ["--icon", icon]         # 图标缺失也能打包，只是没有自定义图标
    command.append(os.path.join(ROOT, "main.py"))
    return command


def main() -> int:
    """执行打包，返回进程退出码。"""
    try:
        import PyInstaller  # noqa: F401  仅探测是否已安装
    except ImportError:
        print("未安装 PyInstaller，请先执行：pip install pyinstaller")
        return 1
    command = build_command(console="--console" in sys.argv)
    print("执行： " + " ".join(f'"{part}"' if " " in part else part
                              for part in command))
    code = subprocess.call(command, cwd=ROOT)
    if code == 0:
        print(f"\n打包完成：{os.path.join(ROOT, 'dist', APP_NAME + '.exe')}")
    return code


if __name__ == "__main__":
    sys.exit(main())
