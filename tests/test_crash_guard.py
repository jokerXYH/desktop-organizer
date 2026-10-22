# -*- coding: utf-8 -*-
"""crash_guard 单元测试：异常落盘、线程钩子、日志轮转。"""
import sys
import threading

import crash_guard


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def test_install_logs_exception_and_message(tmp_path):
    """安装后普通日志与异常 traceback 都应写入同一文件。"""
    path = tmp_path / "logs" / "app.log"
    crash_guard.uninstall()
    assert crash_guard.install(str(path)) == str(path)
    try:
        crash_guard.log_message("hello-banner")
        try:
            raise ValueError("boom-detail")
        except ValueError:
            crash_guard.log_exception("测试上下文")
        text = _read(path)
    finally:
        crash_guard.uninstall()

    assert "hello-banner" in text
    assert "测试上下文" in text
    assert "ValueError: boom-detail" in text
    assert "Traceback" in text


def test_thread_hook_records_worker_exception(tmp_path):
    """后台搜索/缩略图线程的异常也要落盘，而不是只出现在控制台。"""
    path = tmp_path / "app.log"
    crash_guard.uninstall()
    crash_guard.install(str(path))
    try:
        try:
            raise RuntimeError("worker-boom")
        except RuntimeError:
            args = type("Args", (), {
                "thread": type("T", (), {"name": "search-worker"})(),
                "exc_type": sys.exc_info()[0],
                "exc_value": sys.exc_info()[1],
                "exc_traceback": sys.exc_info()[2],
            })()
            threading.excepthook(args)
        text = _read(path)
    finally:
        crash_guard.uninstall()
    assert "search-worker" in text
    assert "worker-boom" in text


def test_log_rotates_when_too_large(tmp_path, monkeypatch):
    """日志超过上限时轮转为 .1，避免长期运行无限膨胀。"""
    path = tmp_path / "app.log"
    monkeypatch.setattr(crash_guard, "MAX_LOG_BYTES", 32)
    path.write_text("x" * 64, encoding="utf-8")
    crash_guard.uninstall()
    crash_guard.install(str(path))
    try:
        crash_guard.log_message("after-rotate")
    finally:
        crash_guard.uninstall()

    assert (tmp_path / "app.log.1").exists()
    assert "after-rotate" in _read(path)


def test_install_is_idempotent(tmp_path):
    """重复安装不应反复替换钩子或重复打开日志句柄。"""
    path = tmp_path / "app.log"
    crash_guard.uninstall()
    first = crash_guard.install(str(path))
    second = crash_guard.install(str(path))
    crash_guard.uninstall()
    assert first == second
