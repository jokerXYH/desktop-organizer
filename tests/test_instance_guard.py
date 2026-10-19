# -*- coding: utf-8 -*-
"""单实例锁：登录时两个进程同时启动，只允许第一个继续创建格子。"""
from pathlib import Path

import instance_guard


def test_second_instance_lock_is_rejected(tmp_path: Path):
    lock_path = str(tmp_path / "instance.lock")
    first = instance_guard.acquire(lock_path)
    assert first is not None
    try:
        assert instance_guard.acquire(lock_path) is None
    finally:
        first.unlock()


def test_lock_can_be_reacquired_after_release(tmp_path: Path):
    lock_path = str(tmp_path / "instance.lock")
    first = instance_guard.acquire(lock_path)
    assert first is not None
    first.unlock()

    second = instance_guard.acquire(lock_path)
    assert second is not None
    second.unlock()
