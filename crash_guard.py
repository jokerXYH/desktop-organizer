# -*- coding: utf-8 -*-
"""崩溃与异常日志：让无控制台的 exe 也能留下可排查的现场。

为什么需要它：
- 打包成 `--windowed` 后没有控制台，Python 的 traceback 默认写到 stderr，
  用户什么都看不到，程序"突然就没了"；
- Qt 槽函数里的异常由 PySide6 转给 `sys.excepthook`，同样会被吞掉；
- 搜索/缩略图线程的异常走 `threading.excepthook`；
- 极少数 Qt/驱动层原生崩溃（访问违例）只有 `faulthandler` 能留下线程栈。

本模块只做"记录 + 轮转"，不弹窗、不改变程序行为，日志失败也绝不阻塞启动。
"""
import datetime
import faulthandler
import os
import platform
import sys
import threading
import traceback

import config as cfg

MAX_LOG_BYTES = 2 * 1024 * 1024      # 单个日志上限 2 MB，超出轮转为 .1

_lock = threading.Lock()
_log_path = ""
_log_handle = None                   # faulthandler 持有的二进制文件句柄
_installed = False
_original_excepthook = None
_original_thread_hook = None


def log_path() -> str:
    """当前日志文件路径（未安装时返回默认位置）。"""
    return _log_path or os.path.join(cfg.CONFIG_DIR, "logs", "app.log")


def _rotate_if_needed(path: str) -> None:
    """日志超过上限时轮转为 .1（只保留一份历史，避免无限增长）。"""
    try:
        if os.path.getsize(path) >= MAX_LOG_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def _append(text: str) -> None:
    """线程安全地追加一行日志；任何失败都静默忽略。"""
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8", errors="replace") as handle:
                handle.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def log_message(message: str, level: str = "INFO") -> None:
    """记录一条普通日志（启动信息、自启修复、更新结果等）。"""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    _append(f"[{stamp}] [{level}] {message}")


def log_exception(context: str, exc_type=None, exc=None, tb=None) -> None:
    """记录异常与完整 traceback；不重新抛出，供 excepthook 与兜底代码共用。"""
    if exc_type is None or exc is None or tb is None:
        exc_type, exc, tb = sys.exc_info()
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    detail = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip()
    _append(f"[{stamp}] [ERROR] {context}\n{detail}")


def startup_context() -> str:
    """启动环境摘要：排查 Win10/Win11、打包形态与 Python 版本差异时用。"""
    bits = [
        f"version={cfg.APP_VERSION}",
        f"python={platform.python_version()}",
        f"frozen={bool(getattr(sys, 'frozen', False))}",
        f"exe={sys.executable}",
        f"platform={platform.platform()}",
    ]
    if sys.platform == "win32":
        try:
            bits.append(f"win_build={sys.getwindowsversion().build}")
        except Exception:
            pass
    return " ".join(bits)


def install(path: str = "") -> str:
    """安装全局异常钩子与 faulthandler；重复调用只生效一次，返回日志路径。"""
    global _log_path, _log_handle, _installed
    global _original_excepthook, _original_thread_hook
    if _installed:
        return log_path()

    _log_path = path or os.path.join(cfg.CONFIG_DIR, "logs", "app.log")
    try:
        os.makedirs(os.path.dirname(_log_path), exist_ok=True)
        _rotate_if_needed(_log_path)
        _log_handle = open(_log_path, "ab", buffering=0)
        # 原生崩溃（访问违例等）也会把各线程栈写进同一个日志
        faulthandler.enable(_log_handle, all_threads=True)
    except OSError:
        _log_handle = None                 # 日志目录不可写：继续运行，只是无日志

    _original_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _original_excepthook(exc_type, exc, tb)
            return
        log_exception("未捕获的异常（主线程/Qt 槽函数）", exc_type, exc, tb)
        try:
            _original_excepthook(exc_type, exc, tb)   # 开发时仍保留 stderr 输出
        except Exception:
            pass

    _original_thread_hook = getattr(threading, "excepthook", None)
    sys.excepthook = _excepthook

    def _thread_hook(args):
        log_exception(
            f"未捕获的线程异常（{getattr(args.thread, 'name', '?')}）",
            args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook
    _installed = True
    log_message(f"启动 {startup_context()}")
    return _log_path


def uninstall() -> None:
    """还原钩子并关闭日志（测试/重启诊断用）。"""
    global _installed, _log_handle
    if not _installed:
        return
    try:
        faulthandler.disable()
    except Exception:
        pass
    if _log_handle is not None:
        try:
            _log_handle.close()
        except OSError:
            pass
    _log_handle = None
    if _original_excepthook is not None:
        sys.excepthook = _original_excepthook
    if _original_thread_hook is not None:
        threading.excepthook = _original_thread_hook
    _installed = False
