# -*- coding: utf-8 -*-
"""pytest 公共配置：把项目根目录加入 sys.path，保证测试能导入主模块。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
