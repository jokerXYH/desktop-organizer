# -*- coding: utf-8 -*-
"""win_compat 单元测试：所有系统调用都必须"失败可回退"。"""
import sys

import win_compat


def test_windows_build_and_version_flags_are_consistent():
    build = win_compat.windows_build()
    if sys.platform != "win32":
        assert build == 0
        assert not win_compat.is_windows_11()
    else:
        assert build > 0
        assert win_compat.is_windows_11() == (build >= 22000)


def test_app_user_model_id_never_raises():
    assert isinstance(win_compat.set_app_user_model_id(), bool)
    assert win_compat.set_app_user_model_id("") is False
