# -*- coding: utf-8 -*-
"""收纳库搜索后台服务：避免全库遍历 + 拼音计算阻塞 UI 线程。

实测基准（5000 文件、40% 中文名）：一次"未命中"搜索在 UI 线程约 114ms，
面板搜索与桌面搜索格子每次按键都会触发，真实大库下必然卡顿。
本模块把 os.walk / pypinyin 计算挪到 QThreadPool 后台线程，
结果经 results_ready 信号送回主线程；接收方用**提交序号 + 当前关键词**
双重校验丢弃过期结果（没有关联的关键词校验、只比序号时，
两个接收方共服务会误判）。

测试可调 run_now() 在前台同步执行同一套扫描逻辑，行为完全等价。
"""
import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from organizer import search_storage


class SearchService(QObject):
    """后台搜索服务（全文库一个实例即可，控制面板与搜索格子共用）。"""

    results_ready = Signal(int, str, list)   # (请求序号, 关键词, 结果列表)

    def __init__(self, root_provider, dirs_provider, parent=None):
        """初始化。

        :param root_provider: 无参函数，返回收纳根目录（每次搜索现取，改配置即生效）
        :param dirs_provider: 无参函数，返回路径映射格子的 (分类名, 目录) 可迭代
        """
        super().__init__(parent)
        self._root_provider = root_provider
        self._dirs_provider = dirs_provider
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)     # 串行执行：天然只让最新请求生效
        self._seq = 0                      # 请求序号，单调递增

    def start(self, keyword: str, limit: int = 100) -> int:
        """提交一次后台搜索，返回本次请求的序号（供接收方做过期校验）。"""
        self._seq += 1
        task = SearchTask(self, self._seq, str(keyword), int(limit))
        self.pool.start(task)
        return self._seq

    def _collect_dirs(self) -> list:
        """现取收纳根目录与映射目录（过滤已不存在的外部路径）。"""
        mapped = []
        try:
            for name, path in self._dirs_provider() or []:
                if os.path.isdir(path):
                    mapped.append((name, path))
        except (TypeError, ValueError):
            pass
        return mapped

    def _run_keyword(self, keyword: str, limit: int) -> list:
        """同步执行一次搜索（后台线程与 run_now 共用；纯 Python，无 Qt 调用）。"""
        return search_storage(self._root_provider(), keyword,
                              limit=limit, extra_dirs=self._collect_dirs())

    def run_now(self, keyword: str, limit: int = 100) -> list:
        """前台同步执行（测试与降级用），不占用后台线程，不发信号。"""
        return self._run_keyword(keyword, limit)


class SearchTask(QRunnable):
    """一次后台搜索任务：跑纯逻辑扫描，完成后经信号回主线程。"""

    def __init__(self, service: SearchService, seq: int,
                 keyword: str, limit: int):
        super().__init__()
        self.service = service
        self.seq = seq
        self.keyword = keyword
        self.limit = limit

    def run(self):
        try:
            results = self.service._run_keyword(self.keyword, self.limit)
        except Exception:                  # 后台线程异常不冒泡，静默按空结果处理
            results = []
        self.service.results_ready.emit(self.seq, self.keyword, results)
