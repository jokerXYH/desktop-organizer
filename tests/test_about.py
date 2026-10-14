# -*- coding: utf-8 -*-
"""关于页（详情介绍 / 更新日志 / 免责声明）纯函数测试。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config as cfg
from control_panel import build_about_html, latest_changelog_sections


def test_latest_changelog_sections_limits_versions():
    """只取最近 N 个版本段落，且丢掉文件头部说明。"""
    text = ("# 更新日志\n\n本项目采用语义化版本号。\n\n"
            "## [3.0] 甲\nA\n\n## [2.0] 乙\nB\n\n## [1.0] 丙\nC")
    out = latest_changelog_sections(text, count=2)
    assert "3.0" in out and "2.0" in out
    assert "1.0" not in out
    assert "本项目采用" not in out


def test_latest_changelog_sections_empty():
    assert latest_changelog_sections("") == ""


def test_about_html_contains_sections_and_version():
    """关于页含三大节与当前版本号，并展示收纳库路径。"""
    html = build_about_html("## [9.9.9] - 2099-01-01\n演示更新条目",
                            r"F:\某处\收纳库")
    assert "详情介绍" in html
    assert "更新日志" in html
    assert "免责声明" in html
    assert cfg.APP_VERSION in html
    assert "9.9.9" in html
    assert r"F:\某处\收纳库" in html


def test_about_html_mentions_local_privacy():
    """关于页明确声明本地运行 / 不上传（隐私红线）。"""
    html = build_about_html("")
    assert "不联网" in html or "不上传" in html


def test_about_html_fallback_without_changelog():
    """更新日志缺失时给出降级说明而不是空白。"""
    html = build_about_html("")
    assert "CHANGELOG" in html
