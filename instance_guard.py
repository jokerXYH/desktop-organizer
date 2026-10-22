# -*- coding: utf-8 -*-
"""进程级单实例锁。

登录时 Windows 可能同时拉起注册表自启项和用户点击的快捷方式；只靠
QLocalServer 有竞态窗口（第一个实例尚未 listen 时，第二个也会继续）。
QLockFile 在创建任何窗口之前先做原子抢占，保证只有一个进程构建格子。
"""
import os

from PySide6.QtCore import QLockFile


def acquire(lock_path: str):
    """尝试获取锁；成功返回 QLockFile（调用方需持有到进程退出），失败返回 None。

    tryLock 等待 1 秒而不是立即失败：进程崩溃后 QLockFile 需要一点时间判定
    陈旧锁；登录时两个启动项同时到达也在这里串行化，后到者能稳定地走
    "唤醒已有实例"分支。
    """
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(10000)   # 崩溃残留锁最多阻塞 10 秒
    return lock if lock.tryLock(1000) else None
