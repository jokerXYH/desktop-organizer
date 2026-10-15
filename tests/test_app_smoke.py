# -*- coding: utf-8 -*-
"""AppContext 集成冒烟测试：重建格子、拖入/拖回、游戏模式守卫、隐藏状态与位置越界回退。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config as cfg  # noqa: E402
import organizer as org  # noqa: E402
import shell_menu  # noqa: E402
from main import AppContext  # noqa: E402
from widgets import COLLAPSED_HEIGHT, WIDGET_GAP  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """构造指向临时收纳库的 AppContext（完全不触碰真实配置与历史）。"""
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "history.json"))
    cfg.save_config(dict(cfg.DEFAULT_CONFIG,
                         storageRoot=str(tmp_path / "收纳库")))
    # 面板构造会读取桌面右键菜单的注册状态：测试固定为未注册（不碰真实注册表）
    monkeypatch.setattr(shell_menu, "is_registered", lambda: False)
    context = AppContext()
    context.rebuild_widgets()
    yield context
    # 清空后台搜索/更新任务并等线程收尾，避免残留后台线程向已销毁控件发信号
    context.search_service.pool.clear()
    context.search_service.pool.waitForDone(1000)
    context.panel.update_service.pool.clear()
    context.panel.update_service.pool.waitForDone(1000)
    for widget in context.widgets:
        widget.close()
    context.panel.close()


def test_rebuild_creates_category_widgets(ctx):
    """重建后每个分类对应一个格子，收纳子目录自动创建。"""
    expected = len(ctx.config["categories"]) + \
        (1 if ctx.config.get("searchGrid", False) else 0)
    assert len(ctx.widgets) == expected
    for name in ctx.config["categories"]:
        assert os.path.isdir(os.path.join(ctx.config["storageRoot"], name))


def test_handle_drop_moves_file_and_records_history(ctx):
    """拖入文件应移入目标格子文件夹并写入历史；拖回原格子被静默忽略。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    src = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "拖入测试.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("x")

    ctx.handle_drop([src], doc)

    moved = os.path.join(ctx.config["storageRoot"], "文档", "拖入测试.txt")
    assert os.path.exists(moved)
    assert len(cfg.load_history()) == 1

    ctx.handle_drop([moved], doc)          # 拖回自身：静默忽略，不计失败
    assert len(cfg.load_history()) == 1


def test_game_mode_stops_watcher_and_pending_refresh(ctx):
    """游戏模式期间：监听全部移除，文件事件不再触发防抖刷新。"""
    assert ctx.watcher.directories()       # 正常模式下有监听

    ctx.set_game_mode(True, notify=False)

    assert ctx.watcher.directories() == []
    ctx._on_storage_changed("ignored")
    assert not ctx._refresh_timer.isActive()

    ctx.set_game_mode(False)
    assert ctx.watcher.directories()       # 退出后监听恢复


def test_widgets_hidden_state_survives_rebuild(ctx):
    """F7 隐藏状态在重建格子（改设置）后仍然保持。"""
    ctx.toggle_widgets()                   # 隐藏
    assert not any(w.isVisible() for w in ctx.widgets)

    ctx.rebuild_widgets()

    assert not any(w.isVisible() for w in ctx.widgets)
    ctx.toggle_widgets()
    assert any(w.isVisible() for w in ctx.widgets)


def test_out_of_screen_saved_position_falls_back(ctx):
    """记忆位置越界（换屏/改分辨率）时应回退默认布局并写回配置。"""
    positions = ctx.config.setdefault("widgetPositions", {})
    positions["文档"] = "99999,99999"
    ctx.rebuild_widgets()

    doc = next(w for w in ctx.widgets if w.title == "文档")
    assert doc.x() < 10000
    assert cfg.load_config()["widgetPositions"].get("文档") != "99999,99999"


def test_grid_create_rename_remove(ctx):
    """自定义格子全流程：新建（含文件夹）→ 重命名（含历史改写）→ 删除（文件夹保留）。"""
    root = ctx.config["storageRoot"]
    ctx.create_grid("资料夹")
    assert "资料夹" in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "资料夹"))
    assert any(w.title == "资料夹" for w in ctx.widgets)

    cfg.append_records([{
        "src": "D:/x.pdf",
        "dst": os.path.join(root, "资料夹", "x.pdf"),
        "batch": "b1",
    }])
    error = ctx.rename_grid("资料夹", "学习资料")
    assert error == ""
    assert "学习资料" in ctx.config["categories"]
    assert "资料夹" not in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "学习资料"))
    assert not os.path.isdir(os.path.join(root, "资料夹"))
    assert any(w.title == "学习资料" for w in ctx.widgets)
    assert cfg.load_history()[0]["dst"] == \
        os.path.join(root, "学习资料", "x.pdf")          # 历史已改写，仍可撤销

    ctx.remove_grid("学习资料")
    assert "学习资料" not in ctx.config["categories"]
    assert os.path.isdir(os.path.join(root, "学习资料"))  # 文件夹与文件保留
    assert not any(w.title == "学习资料" for w in ctx.widgets)


def test_rename_grid_rejects_bad_name(ctx):
    """重命名校验：重名或非法字符应返回错误提示而不是抛异常。"""
    ctx.create_grid("资料夹")
    assert "已存在" in ctx.rename_grid("资料夹", "文档")     # 与内置分类重名
    assert "不能包含" in ctx.rename_grid("资料夹", "a:b")
    assert "资料夹" in ctx.config["categories"]             # 失败后配置不变


def test_rename_builtin_category_changes_display_name_only(ctx):
    """内置分类改名：只改显示名，分类键与收纳库文件夹保持不变。"""
    root = ctx.config["storageRoot"]
    assert ctx.rename_grid("文档", "资料") == ""

    assert "文档" in ctx.config["categories"]               # 分类键不变（自动整理依赖它）
    assert os.path.isdir(os.path.join(root, "文档"))         # 文件夹不变
    assert ctx.config["gridTitles"]["文档"] == "资料"        # 仅显示名改变

    doc = next(w for w in ctx.widgets if w.title == "资料")
    assert doc.grid_key == "文档"                           # 记忆键仍是内置键
    assert "已存在" in ctx.rename_grid("资料", "图片")       # 与其它内置分类显示名重名被拒

    ctx.rebuild_widgets()                                   # 重启后显示名保持
    assert any(w.title == "资料" for w in ctx.widgets)


def test_panel_geometry_persist(ctx):
    """面板几何记忆：保存后写入配置，可被 parse 还原。"""
    ctx.panel.move(100, 120)
    ctx.panel.resize(520, 680)
    ctx.panel.save_geometry()

    saved = cfg.parse_geometry(cfg.load_config()["panelGeometry"])
    assert saved == (100, 120, 520, 680)


def test_grid_zoom_and_size_persist(ctx):
    """图标缩放与窗口尺寸各自持久化，重建格子后都能恢复。"""
    from widgets import scale_geometry

    doc = next(w for w in ctx.widgets if w.title == "文档")
    doc._zoom(1)                                  # 图标 100% -> 115%（窗口尺寸不变）
    assert doc.width() == 270
    assert ctx.config["widgetScales"]["文档"] == 1.15

    doc._apply_window_size(420, 300)              # 拖拽调整窗口尺寸
    doc._emit_size()
    assert ctx.config["widgetSizes"]["文档"] == "420,300"

    ctx.rebuild_widgets()                         # 模拟重启后恢复
    doc2 = next(w for w in ctx.widgets if w.title == "文档")
    assert (doc2.width(), doc2.height()) == (420, 300)
    assert doc2.scale == 1.15
    assert doc2.list_widget.gridSize().height() == scale_geometry(1.15)["cell_h"]

    doc2._zoom(-1)                                # Ctrl+滚轮缩小回 100% 并持久化
    doc2._apply_window_size(270, 360)             # 拖拽边缘缩回默认尺寸
    doc2._emit_size()
    assert doc2.scale == 1.0
    assert (doc2.width(), doc2.height()) == (270, 360)
    assert ctx.config["widgetScales"]["文档"] == 1.0
    assert ctx.config["widgetSizes"]["文档"] == "270,360"


def test_preview_per_grid(ctx):
    """预览方式按单格子保存：切换一个格子不影响其它格子。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    pics = next(w for w in ctx.widgets if w.title == "图片")

    doc._switch_preview(False)

    assert doc.preview_icons is False
    assert ctx.config["widgetPreviews"]["文档"] is False
    assert pics.preview_icons is True                    # 其它格子不受影响

    ctx.rebuild_widgets()
    doc2 = next(w for w in ctx.widgets if w.title == "文档")
    pics2 = next(w for w in ctx.widgets if w.title == "图片")
    assert doc2.preview_icons is False
    assert pics2.preview_icons is True


def test_map_folder_grid(ctx, tmp_path):
    """路径映射：格子指向外部文件夹；重命名只改名称；删除不动外部文件。"""
    external = tmp_path / "外部资料"
    external.mkdir()
    (external / "a.txt").write_text("a", encoding="utf-8")

    ctx.create_grid("外部资料", folder=str(external))

    assert ctx.config["gridPaths"]["外部资料"] == str(external)
    widget = next(w for w in ctx.widgets if w.title == "外部资料")
    assert widget.folder == str(external)
    assert str(external) in ctx.watcher.directories()    # 外部文件夹纳入监听

    error = ctx.rename_grid("外部资料", "资料映射")
    assert error == ""
    assert external.is_dir()                             # 映射的外部文件夹不改名
    assert ctx.config["gridPaths"]["资料映射"] == str(external)

    ctx.remove_grid("资料映射")
    assert "资料映射" not in ctx.config["categories"]
    assert "资料映射" not in ctx.config["gridPaths"]
    assert external.is_dir() and (external / "a.txt").exists()


def test_auto_organize_new_files_only(ctx, tmp_path, monkeypatch):
    """自动整理：只收纳开启之后新出现的桌面文件，旧文件不动。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    (desktop / "旧文件.pdf").write_text("old", encoding="utf-8")
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))

    ctx.set_auto_organize(True)                  # 基线=当前桌面（旧文件不整理）
    (desktop / "新文件.pdf").write_text("new", encoding="utf-8")

    ctx._run_auto_organize()                     # 静置到期：进入稳定性确认
    ctx._confirm_and_collect()                   # 确认无变化：真正收纳

    root = ctx.config["storageRoot"]
    assert os.path.exists(os.path.join(root, "文档", "新文件.pdf"))
    assert (desktop / "旧文件.pdf").exists()      # 旧文件不被触碰
    assert len(cfg.load_history()) == 1

    ctx.set_auto_organize(False)
    assert ctx._auto_watcher.directories() == []


# ---------- v2.7.1 自动整理时序修复回归 ----------

def test_auto_organize_waits_for_stability(ctx, tmp_path, monkeypatch):
    """还在写入的文件不被搬走：确认窗口内大小变化 → 继续等；稳定后只收一次。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)
    target = desktop / "下载中.pdf"
    target.write_text("部分内容", encoding="utf-8")

    ctx._run_auto_organize()                     # 进入稳定性确认
    target.write_text("部分内容，还在继续写入更长的内容", encoding="utf-8")
    ctx._confirm_and_collect()                   # 确认窗口内变化 → 不收

    root = ctx.config["storageRoot"]
    assert not os.path.exists(os.path.join(root, "文档", "下载中.pdf"))
    assert target.exists()
    assert len(cfg.load_history()) == 0

    ctx._run_auto_organize()                     # 第二轮：已稳定
    ctx._confirm_and_collect()
    assert os.path.exists(os.path.join(root, "文档", "下载中.pdf"))
    assert not target.exists()
    assert len(cfg.load_history()) == 1


def test_auto_organize_skips_in_progress_names(ctx, tmp_path, monkeypatch):
    """下载中的临时名（.crdownload）不收也不进基线；改成最终名后正常归档。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)

    part = desktop / "报告.pdf.crdownload"
    part.write_text("half", encoding="utf-8")
    ctx._run_auto_organize()
    ctx._confirm_and_collect()

    root = ctx.config["storageRoot"]
    assert part.exists()                          # 临时名原样留在桌面
    assert os.listdir(os.path.join(root, "其他")) == []   # 没被误收进"其他"
    assert "报告.pdf.crdownload" not in ctx._auto_seen    # 不进基线

    os.replace(part, desktop / "报告.pdf")        # 模拟浏览器下载完成改名
    ctx._run_auto_organize()
    ctx._confirm_and_collect()

    assert os.path.exists(os.path.join(root, "文档", "报告.pdf"))
    assert len(cfg.load_history()) == 1


def test_auto_organize_locked_file_retries(ctx, tmp_path, monkeypatch):
    """被占用的文件不被基线吞掉：失败进入有界重试，占用释放后自动补收。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)
    locked = desktop / "锁定.pdf"
    locked.write_text("x", encoding="utf-8")

    real_move = org.shutil.move
    failing = {"now": True}

    def flaky_move(src, dst):
        if failing["now"] and os.path.basename(src) == "锁定.pdf":
            raise PermissionError("文件被占用")
        return real_move(src, dst)

    monkeypatch.setattr(org.shutil, "move", flaky_move)

    ctx._run_auto_organize()
    ctx._confirm_and_collect()                   # 第一次尝试：被占用
    assert locked.exists()
    assert ctx._auto_attempts.get("锁定.pdf") == 1
    assert "锁定.pdf" in ctx._auto_stable         # 留在待确认队列等重试
    assert ctx._auto_retry_timer.isActive()

    failing["now"] = False                       # 模拟占用释放
    ctx._confirm_and_collect()                   # 重试到期（与确认共用回调）
    root = ctx.config["storageRoot"]
    assert os.path.exists(os.path.join(root, "文档", "锁定.pdf"))
    assert ctx._auto_attempts == {}
    assert len(cfg.load_history()) == 1


def test_auto_organize_gives_up_after_max_attempts(ctx, tmp_path, monkeypatch):
    """持续被占用的条目重试 AUTO_MAX_ATTEMPTS 次后放弃并提示，不无限循环。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)
    locked = desktop / "钉子户.pdf"
    locked.write_text("x", encoding="utf-8")

    def always_fail(src, dst):
        raise PermissionError("文件被占用")

    monkeypatch.setattr(org.shutil, "move", always_fail)
    from main import AUTO_MAX_ATTEMPTS

    ctx._run_auto_organize()
    for _ in range(AUTO_MAX_ATTEMPTS):
        ctx._confirm_and_collect()

    assert locked.exists()                        # 始终留在桌面，从不丢失
    assert ctx._auto_attempts == {}               # 计数已清理
    assert "钉子户.pdf" not in ctx._auto_stable   # 不再待确认
    assert "钉子户.pdf" in ctx._auto_seen         # 入基线：不再反复打扰
    assert len(cfg.load_history()) == 0
    assert "放弃" in ctx.panel.status_label.text()


def test_auto_organize_duplicate_guard(ctx, tmp_path, monkeypatch):
    """刚收纳过的同名条目短时间再现（程序反复释放）不重复入库。"""
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)

    folder = desktop / "资料"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")
    ctx._run_auto_organize()
    ctx._confirm_and_collect()
    root = ctx.config["storageRoot"]
    assert os.path.exists(os.path.join(root, "文件夹", "资料", "a.txt"))

    folder.mkdir()                                # 模拟释放方重建同名文件夹
    (folder / "b.txt").write_text("b", encoding="utf-8")
    ctx._run_auto_organize()
    ctx._confirm_and_collect()

    assert (folder / "b.txt").exists()            # 新副本留在桌面，不重复入库
    assert not os.path.exists(os.path.join(root, "文件夹", "资料", "b.txt"))
    assert "资料" in ctx._auto_seen               # 入基线：不再反复提示
    assert "重复" in ctx.panel.status_label.text()


def test_auto_organize_guard_restored_from_history(ctx, tmp_path, monkeypatch):
    """同名保护跨重启：开启自动整理时从撤销历史恢复保护名单。

    释放源反复释放 + 应用重启（保护名单原存内存会丢失）的场景：
    guard 窗口内有该名字的移动记录 → 再现时跳过；窗口外的记录不设防。
    """
    from datetime import datetime, timedelta

    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    root = ctx.config["storageRoot"]
    now = datetime.now()
    # 模拟"上一个进程"留下的历史：5 分钟前收纳过 "资料"，2 小时前收纳过 "旧释放"
    # （注意 dst 是被移动的条目本身——文件夹收纳时 dst 即文件夹路径）
    cfg.append_records([
        {"src": str(desktop / "资料"),
         "dst": os.path.join(root, "文件夹", "资料"),
         "batch": "b1",
         "time": (now - timedelta(minutes=5)).isoformat(timespec="seconds")},
        {"src": str(desktop / "旧释放"),
         "dst": os.path.join(root, "文件夹", "旧释放"),
         "batch": "b2",
         "time": (now - timedelta(hours=2)).isoformat(timespec="seconds")},
    ])

    ctx.set_auto_organize(True)                   # 启动恢复：从历史加载保护名单
    recent = desktop / "资料"                     # 释放方再次释放同名文件夹
    recent.mkdir()
    (recent / "b.txt").write_text("b", encoding="utf-8")
    stale = desktop / "旧释放"                     # 超过窗口的同名再现：正常收纳
    stale.mkdir()
    (stale / "c.txt").write_text("c", encoding="utf-8")

    ctx._run_auto_organize()
    assert "重复" in ctx.panel.status_label.text()     # 保护提示已发出
    ctx._confirm_and_collect()

    assert (recent / "b.txt").exists()            # 窗口内：被保护跳过
    assert not os.path.exists(os.path.join(root, "文件夹", "资料", "b.txt"))
    assert os.path.exists(os.path.join(root, "文件夹", "旧释放", "c.txt"))


def test_search_grid(ctx):
    """搜索格子：默认存在、支持拼音搜索（后台提交+结果回填）、删除后消失、可恢复。"""
    search = next(w for w in ctx.widgets if w.title == "搜索")
    root = ctx.config["storageRoot"]
    with open(os.path.join(root, "文档", "季度报告.docx"), "w",
              encoding="utf-8") as handle:
        handle.write("x")

    search.search_edit.setText("jidu")             # 拼音（季度）
    search._run_search()                           # 提交后台请求：结果回来前保持收起
    assert search.list_widget.count() == 0         # 无结果时列表收起（只剩搜索框）

    ctx.search_service.results_ready.emit(
        search._search_seq, "jidu", ctx.search_service.run_now("jidu", 200))

    assert search.list_widget.count() == 1
    assert search.list_widget.item(0).data(Qt.UserRole).endswith("季度报告.docx")
    assert "1 项" in search.count_label.text()

    ctx.set_search_grid(False)
    assert not any(w.title == "搜索" for w in ctx.widgets)
    assert ctx.config["searchGrid"] is False

    ctx.set_search_grid(True)
    assert any(w.title == "搜索" for w in ctx.widgets)


def test_search_grid_ignores_stale_results(ctx):
    """后台搜索过期结果（序号已过期或关键词已变化）不得污染列表。"""
    search = next(w for w in ctx.widgets if w.title == "搜索")
    root = ctx.config["storageRoot"]
    with open(os.path.join(root, "文档", "季度报告.docx"), "w",
              encoding="utf-8") as handle:
        handle.write("x")

    search.search_edit.setText("jidu")
    search._run_search()
    first_seq = search._search_seq

    # 用户改了关键词（防抖计时被手动停掉），此时第一个请求的结果才回来：
    # 关键词与当前不一致 → 必须丢弃
    search.search_edit.setText("完全不同的词")
    search._search_timer.stop()
    ctx.search_service.results_ready.emit(
        first_seq, "jidu", ctx.search_service.run_now("jidu", 200))
    assert search.list_widget.count() == 0

    # 用当前关键词重新提交：序号已更新，回填正常生效（无命中=空列表）
    search._run_search()
    assert search._search_seq > first_seq
    ctx.search_service.results_ready.emit(
        search._search_seq, "完全不同的词",
        ctx.search_service.run_now("完全不同的词", 200))
    assert search.list_widget.count() == 0
    assert search.count_label.text() == "0 项"


def test_panel_search_async_and_stale_guard(ctx):
    """面板搜索：提交为后台请求，过期结果不回填，最新结果正常填充与提示。"""
    panel = ctx.panel
    root = ctx.config["storageRoot"]
    with open(os.path.join(root, "文档", "季度报表.xlsx"), "w",
              encoding="utf-8") as handle:
        handle.write("x")

    panel.search_edit.setText("报表")
    panel._run_search()
    assert panel._search_pending is True
    first_seq = panel._search_seq
    assert "搜索中" in panel.status_label.text()

    # 过期结果（关键词已变）到达：不回填
    panel.search_edit.setText("别的词")
    panel._search_timer.stop()
    ctx.search_service.results_ready.emit(
        first_seq, "报表", ctx.search_service.run_now("报表", 100))
    assert panel.search_list.count() == 0

    panel.search_edit.setText("报表")
    panel._search_timer.stop()
    panel._run_search()
    ctx.search_service.results_ready.emit(
        panel._search_seq, "报表", ctx.search_service.run_now("报表", 100))
    assert panel.search_list.count() == 1
    assert "搜索到 1 个结果" in panel.status_label.text()


def test_targeted_refresh_only_affected_grids(ctx, monkeypatch):
    """文件事件只刷新事件源目录对应的格子，防抖窗口内自动合并，不清空后重刷。"""
    root = ctx.config["storageRoot"]
    doc = next(w for w in ctx.widgets if w.title == "文档")
    pics = next(w for w in ctx.widgets if w.title == "图片")
    hits = {"文档": 0, "图片": 0}
    monkeypatch.setattr(doc, "refresh",
                        lambda: hits.__setitem__("文档", hits["文档"] + 1))
    monkeypatch.setattr(pics, "refresh",
                        lambda: hits.__setitem__("图片", hits["图片"] + 1))

    ctx._on_storage_changed(os.path.join(root, "文档"))
    ctx._on_storage_changed(os.path.join(root, "图片"))   # 防抖窗口内合并
    assert ctx._refresh_timer.isActive()
    ctx._refresh_timer.stop()
    ctx._refresh_pending()

    assert hits == {"文档": 1, "图片": 1}
    ctx._refresh_pending()                                # 队列已清空，不重刷
    assert hits == {"文档": 1, "图片": 1}


def test_cut_paste_between_grids(ctx):
    """右键剪切 → 目标格子粘贴：文件移动、写入历史、剪切板清空。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    pics = next(w for w in ctx.widgets if w.title == "图片")
    src = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "cut_me.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("x")

    ctx.handle_cut_files([src], doc)
    assert ctx.clipboard_count() == 1
    assert os.path.exists(src)                    # 剪切不立即移动

    ctx.handle_paste_files(pics)
    assert not os.path.exists(src)
    moved = os.path.join(ctx.config["storageRoot"], "图片", "cut_me.txt")
    assert os.path.exists(moved)
    assert ctx.clipboard_count() == 0
    assert len(cfg.load_history()) == 1


# ---------- v2.6.1 优化回归：批量写历史 / 粘贴保留失败项 / 根目录守卫 ----------

def test_handle_drop_writes_history_once(ctx, monkeypatch):
    """拖入多个文件只做一次历史落盘，而不是每个文件重写一遍历史文件。"""
    calls = []
    monkeypatch.setattr(cfg, "append_records",
                        lambda records: calls.append(records))
    doc = next(w for w in ctx.widgets if w.title == "文档")
    base = os.path.dirname(ctx.config["storageRoot"])
    srcs = []
    for i in range(3):
        src = os.path.join(base, f"批量{i}.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("x")
        srcs.append(src)

    ctx.handle_drop(srcs, doc)

    assert len(calls) == 1                        # 一次拖入 = 一次写入
    assert len(calls[0]) == 3
    assert os.path.isdir(os.path.join(ctx.config["storageRoot"], "文档"))


def test_paste_failure_keeps_items_in_clipboard(ctx, monkeypatch):
    """粘贴时被占用而移动失败的条目应保留在剪切板，成功项正常移动入库。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    base = os.path.dirname(ctx.config["storageRoot"])
    ok = os.path.join(base, "好文件.txt")
    bad = os.path.join(base, "坏文件.txt")
    for path, text in ((ok, "好"), (bad, "坏")):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    real_move_into = org.move_into

    def flaky(src, folder):
        if os.path.basename(src) == "坏文件.txt":
            raise OSError("文件被占用")
        return real_move_into(src, folder)

    monkeypatch.setattr(org, "move_into", flaky)
    ctx.file_clipboard = [ok, bad]

    ctx.handle_paste_files(doc)

    assert os.path.exists(
        os.path.join(ctx.config["storageRoot"], "文档", "好文件.txt"))
    assert not os.path.exists(
        os.path.join(ctx.config["storageRoot"], "文档", "坏文件.txt"))
    assert ctx.file_clipboard == [bad]            # 失败项保留，可重新粘贴
    assert len(cfg.load_history()) == 1           # 只有成功项写历史


def test_save_root_ignores_no_change(ctx, monkeypatch):
    """收纳目录未改动时失焦不应写配置或全量重建格子（editingFinished 守卫）。"""
    rebuild_calls = []
    monkeypatch.setattr(ctx, "on_widgets_rebuild",
                        lambda: rebuild_calls.append(1))

    ctx.panel._save_root()                        # 文本与配置一致 → 无动作
    assert rebuild_calls == []

    new_root = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "新库")
    ctx.panel.root_edit.setText(new_root)
    ctx.panel._save_root()

    assert rebuild_calls == [1]                   # 真正改动才重建
    assert cfg.load_config()["storageRoot"] == new_root


# ---------- v2.7.0 界面优化回归 ----------

def test_panel_height_clamped_to_screen(ctx):
    """面板高度不超过屏幕可用高度-80（1366×768 等小屏不被任务栏截断）。"""
    screen = QApplication.primaryScreen().availableGeometry()
    assert ctx.panel.height() <= screen.height() - 80


def test_auto_organize_tray_panel_sync(ctx):
    """自动整理开关：ctx 状态变化同步面板勾选与托盘动作，无信号递归。"""
    from PySide6.QtGui import QAction

    ctx.act_auto_organize = QAction(None, checkable=True)
    ctx.act_auto_organize.setChecked(False)

    ctx.set_auto_organize(True)
    assert ctx.panel.auto_check.isChecked() is True
    assert ctx.act_auto_organize.isChecked() is True
    assert ctx.config["autoOrganize"] is True

    ctx.set_auto_organize(False)
    assert ctx.panel.auto_check.isChecked() is False
    assert ctx.act_auto_organize.isChecked() is False


def test_search_results_category_tag(ctx):
    """搜索结果固定单列显示：行尾带分类标签，且不会被"预览方式"排成网格。"""
    from PySide6.QtWidgets import QListView

    search = next(w for w in ctx.widgets if w.title == "搜索")
    path = os.path.join(ctx.config["storageRoot"], "文档", "报告.docx")

    search.search_edit.setText("报告")
    search._search_timer.stop()
    search._run_search()
    ctx.search_service.results_ready.emit(
        search._search_seq, "报告", [("文档", path)])
    assert search.list_widget.count() == 1
    assert search.list_widget.item(0).text() == "报告.docx（文档）"

    # 搜索条忽略"图标视图"：切换后仍是单列列表
    search.set_preview(True)
    assert search.list_widget.viewMode() == QListView.ListMode


def test_empty_grid_guide_hint(ctx):
    """空格子显示两行引导：主提示 + 右键操作提示。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    doc.refresh()
    assert doc.list_widget.count() == 2
    assert "拖入" in doc.list_widget.item(0).text()
    assert "右键" in doc.list_widget.item(1).text()


def test_dialog_dark_theme_rules():
    """全局 QSS 覆盖弹窗（QMessageBox/QInputDialog），不再出现亮色系统弹窗。"""
    from main import APP_QSS

    assert "QMessageBox" in APP_QSS
    assert "QInputDialog" in APP_QSS
    assert "QDialog" in APP_QSS


def test_resize_hot_zone_widened(ctx):
    """尺寸拖拽热区加宽到 8px：小尺寸格子上更容易命中边缘。"""
    from PySide6.QtCore import QPoint

    doc = next(w for w in ctx.widgets if w.title == "文档")
    assert doc._edge_at(QPoint(7, 180)) == "left"    # 7px 处仍是热区（原 6px 不在）
    assert doc._edge_at(QPoint(135, 180)) is None    # 中部空白仍是拖动窗口区


# ---------- v2.7.2 自动整理与搜索线程修复回归 ----------

def test_auto_organize_skipped_by_switch_not_marked_collected(ctx, tmp_path, monkeypatch):
    """因收录开关关闭而跳过的条目不能被当成"已收纳"：打开开关后仍能收。

    旧实现把 set(stable)-set(failed) 全当成功，文件夹被误入已收纳名单与
    基线，用户之后打开"收录文件夹"开关时它已不在"新条目"里，永久不再收纳。
    """
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.config["includeFolders"] = False          # 文件夹收录开关关闭
    ctx.set_auto_organize(True)

    folder = desktop / "资料"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")

    ctx._run_auto_organize()
    ctx._confirm_and_collect()

    root = ctx.config["storageRoot"]
    assert folder.is_dir()                                   # 有意留在桌面
    assert not os.path.exists(os.path.join(root, "文件夹", "资料"))
    assert "资料" not in ctx._auto_collected                 # 未误记为已收纳
    assert "资料" not in ctx._auto_seen                      # 也未入基线
    assert "资料" not in ctx._auto_stable                    # 已撤出待确认

    ctx.config["includeFolders"] = True           # 用户随后打开开关
    ctx._run_auto_organize()
    ctx._confirm_and_collect()
    assert os.path.exists(os.path.join(root, "文件夹", "资料", "a.txt"))


def test_auto_organize_detects_deep_folder_writes(ctx, tmp_path, monkeypatch):
    """子目录里继续落文件也应判为"还在变化"：文件夹稳定性需递归计数。

    只数顶层条目时，往已存在的子目录里拷贝文件既不改变顶层条目数、
    也不改变顶层目录 mtime（目录 mtime 只随本层增删改名更新），
    半拷贝的文件夹会被误判为已稳定而整体搬走 → 释放方重建产生副本。
    """
    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    ctx.set_auto_organize(True)

    folder = desktop / "半拷贝"
    sub = folder / "子目录"
    sub.mkdir(parents=True)
    (sub / "a.bin").write_text("a", encoding="utf-8")

    ctx._run_auto_organize()                      # 进入确认（快照含 a.bin）
    (sub / "b.bin").write_text("b", encoding="utf-8")   # 顶层条目数与 mtime 均不变

    ctx._confirm_and_collect()
    root = ctx.config["storageRoot"]
    assert not os.path.exists(os.path.join(root, "文件夹", "半拷贝"))  # 未搬走
    assert folder.is_dir()
    assert "半拷贝" in ctx._auto_stable            # 仍在待确认，继续观察


def test_auto_organize_guard_uses_src_name_on_collision(ctx, tmp_path, monkeypatch):
    """同名保护名单按 src（桌面原名）重建：dst 带" (2)"时取名字会错位。

    收纳库已有"资料"时再次收纳的同名条目 dst 会变成"资料 (2)"，
    用 dst 的 basename 建名单就与桌面上真实再现的"资料"对不上，保护失效。
    """
    from datetime import datetime, timedelta

    desktop = tmp_path / "fake_desktop"
    desktop.mkdir()
    monkeypatch.setattr(org, "get_desktop_path", lambda: str(desktop))
    root = ctx.config["storageRoot"]
    cfg.append_records([{
        "src": str(desktop / "资料"),
        "dst": os.path.join(root, "文件夹", "资料 (2)"),   # 冲突改名后的目标
        "batch": "b1",
        "time": (datetime.now() - timedelta(minutes=5)).isoformat(
            timespec="seconds"),
    }])

    ctx.set_auto_organize(True)                   # 启动恢复保护名单
    assert "资料" in ctx._auto_collected          # 用 src 取到的名字

    recent = desktop / "资料"
    recent.mkdir()
    (recent / "b.txt").write_text("b", encoding="utf-8")
    ctx._run_auto_organize()
    assert "重复" in ctx.panel.status_label.text()     # 同名保护生效


def test_grid_dirs_provider_returns_snapshot(ctx):
    """路径映射提供者返回快照拷贝：后台搜索线程迭代不会踩到主线程的增删。"""
    external = os.path.join(os.path.dirname(ctx.config["storageRoot"]), "外部")
    os.makedirs(external, exist_ok=True)
    ctx.create_grid("映射A", folder=external)

    snapshot = ctx._snapshot_grid_dirs()
    assert ("映射A", external) in snapshot
    assert isinstance(snapshot, list)

    ctx.remove_grid("映射A")
    # 旧快照是独立拷贝：不随之后的增删变化（后台线程可安心迭代）
    assert ("映射A", external) in snapshot
    assert all(name != "映射A" for name, _path in ctx._snapshot_grid_dirs())


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感仅 Windows 文件系统")
def test_storage_change_path_case_insensitive(ctx, monkeypatch):
    """事件源路径与格子记录的路径大小写不同也必须命中（用同一归一化尺子）。"""
    root = ctx.config["storageRoot"]
    doc = next(w for w in ctx.widgets if w.title == "文档")
    hits = []
    monkeypatch.setattr(doc, "refresh", lambda: hits.append(1))

    ctx._on_storage_changed(os.path.join(root, "文档").upper())
    ctx._refresh_timer.stop()
    ctx._refresh_pending()
    assert hits == [1]


def test_organize_reports_oserror_instead_of_crashing(ctx, monkeypatch):
    """一键整理遇到 OSError（如建分类目录失败）应弹提示，而非异常逃出槽函数。"""
    from PySide6.QtWidgets import QMessageBox

    ctx.config["confirmOrganize"] = False         # 跳过预览，直接走整理分支

    def boom(*_args, **_kwargs):
        raise OSError("磁盘只读")

    monkeypatch.setattr(org, "organize_desktop", boom)
    messages = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda parent, title, text:
                                     messages.append(text)))

    ctx.panel._do_organize()                      # 不应抛异常

    assert messages and "磁盘只读" in messages[0]


# ---------- v2.9.1 外观与对齐 ----------

# 与真机 settings.json 同形的 8 分类 4 列 × 2 行凌乱现场
USER_CATEGORIES = ["文档", "图片", "压缩包", "安装包", "代码", "快捷方式",
                   "文件夹", "其他"]
USER_COORDS = {
    "文档": (1608, 21, 310, 414),
    "图片": (642, 448, 330, 540),
    "压缩包": (1302, 18, 304, 429),
    "安装包": (1302, 448, 304, 538),
    "代码": (979, 18, 303, 432),
    "快捷方式": (979, 448, 303, 537),
    "其他": (642, 18, 330, 432),
    "文件夹": (1623, 448, 312, 536),
}


def _arrange_user_like_layout(ctx):
    """把测试上下文布置成"真机同形的凌乱现场"（供整齐排列用例使用）。"""
    ctx.config["categories"] = list(USER_CATEGORIES)
    ctx.set_search_grid(False)          # 聚焦 8 个分类格子的栅格（顺带重建）
    for widget in ctx.widgets:
        if widget.title in USER_COORDS:
            widget.setGeometry(*USER_COORDS[widget.title])


def test_tidy_layout_produces_even_grid(ctx):
    """整齐排列：凌乱布局归一为统一 16px 间距的规整栅格，并写回配置。"""
    _arrange_user_like_layout(ctx)

    ctx.tidy_layout()

    # 列：按 x 分组后列内左缘一致、列间统一 16px
    columns = {}
    for widget in ctx.widgets:
        columns.setdefault(widget.x(), []).append(widget)
    xs = sorted(columns)
    assert len(xs) == 4
    for left, right in zip(xs, xs[1:]):
        col_right = max(w.x() + w.width() for w in columns[left])
        assert right - col_right == WIDGET_GAP

    # 行：上缘一致、行间距 16px；轻微尺寸差异被统一（文档 414→432 等）
    doc = next(w for w in ctx.widgets if w.title == "文档")
    folder = next(w for w in ctx.widgets if w.title == "文件夹")
    assert (doc.width(), doc.height()) == (312, 432)
    assert (folder.width(), folder.height()) == (312, 540)
    assert folder.y() - (doc.y() + doc.height()) == WIDGET_GAP

    # 结果已持久化（位置 + 统一后的尺寸）
    saved = cfg.load_config()
    assert saved["widgetPositions"]["文档"] == f"{doc.x()},{doc.y()}"
    assert saved["widgetSizes"]["文档"] == "312,432"


def test_tidy_layout_second_run_is_noop(ctx):
    """再点一次「整齐排列」不应有任何变化（幂等）。"""
    _arrange_user_like_layout(ctx)
    ctx.tidy_layout()
    before = [(w.x(), w.y(), w.width(), w.height()) for w in ctx.widgets]
    ctx.tidy_layout()
    after = [(w.x(), w.y(), w.width(), w.height()) for w in ctx.widgets]
    assert before == after


def test_collapse_state_persists_across_rebuild(ctx):
    """收起状态写入配置并在重建后保留；展开恢复原高度。"""
    doc = next(w for w in ctx.widgets if w.title == "文档")
    doc.set_collapsed(True)
    assert doc.height() == COLLAPSED_HEIGHT
    assert cfg.load_config()["widgetCollapsed"]["文档"] is True

    ctx.rebuild_widgets()
    doc2 = next(w for w in ctx.widgets if w.title == "文档")
    assert doc2.height() == COLLAPSED_HEIGHT

    doc2.set_collapsed(False)
    assert doc2.height() > COLLAPSED_HEIGHT
    assert cfg.load_config()["widgetCollapsed"]["文档"] is False


def test_new_widget_default_placement_no_overlap(ctx):
    """无位置记忆的格子按右侧列排队落位，不与任何已有格子重叠。"""
    ctx.config["widgetPositions"].pop("文档", None)
    cfg.save_config(ctx.config)

    ctx.rebuild_widgets()

    doc = next(w for w in ctx.widgets if w.title == "文档")
    for other in ctx.widgets:
        if other is doc:
            continue
        assert not doc.geometry().intersects(other.geometry())


def test_rebuild_positions_are_stable(ctx):
    """同一配置重复重建：所有格子位置尺寸保持一致（默认布局可收敛）。

    v2.11.0 起：默认布局在极端小屏上可能生成越界位置，第一次重建会被
    钳回屏幕内并写回配置——从第二次重建开始必须完全稳定，且全部在屏内。
    """
    from PySide6.QtCore import QRect

    ctx.rebuild_widgets()                      # 第一次：越界位置收进屏内并写回
    before = {w.grid_key: (w.x(), w.y(), w.width(), w.height())
              for w in ctx.widgets}
    ctx.rebuild_widgets()
    after = {w.grid_key: (w.x(), w.y(), w.width(), w.height())
             for w in ctx.widgets}
    assert before == after
    screen = QApplication.primaryScreen().availableGeometry()
    for widget in ctx.widgets:                 # 全部格子必须可见地落在屏幕内
        rect = QRect(widget.x(), widget.y(),
                     widget.width(), widget.height())
        assert screen.intersected(rect) == rect


# ---------- v2.9.2 桌面右键菜单与关于页 ----------

def test_panel_has_shell_menu_and_about(ctx):
    """面板包含「桌面右键菜单」开关与「关于」按钮。"""
    assert "桌面右键菜单" in ctx.panel.shell_menu_check.text()
    assert "关于" in ctx.panel.about_btn.text()


def test_panel_shell_menu_toggle(ctx, monkeypatch):
    """桌面右键菜单开关：开→注册、关→移除、记录写入配置。"""
    calls = []
    monkeypatch.setattr(shell_menu, "register",
                        lambda script: calls.append("register"))
    monkeypatch.setattr(shell_menu, "unregister",
                        lambda: calls.append("unregister"))

    ctx.panel.shell_menu_check.setChecked(True)
    assert calls[-1] == "register"
    assert cfg.load_config()["shellMenu"] is True

    ctx.panel.shell_menu_check.setChecked(False)
    assert calls[-1] == "unregister"
    assert cfg.load_config()["shellMenu"] is False


def test_shell_menu_toggle_rolls_back_on_error(ctx, monkeypatch):
    """注册表写入失败：勾选回滚 + 弹窗提示，不抛异常。"""
    from PySide6.QtWidgets import QMessageBox

    def boom(_script):
        raise OSError("注册表被拒绝")

    monkeypatch.setattr(shell_menu, "register", boom)
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args)))

    ctx.panel.shell_menu_check.setChecked(True)

    assert not ctx.panel.shell_menu_check.isChecked()   # 勾选回滚
    assert warnings                                     # 有弹窗提示


# ---------- v2.10.0 横向面板与检查更新 ----------

def test_panel_is_horizontal_layout(ctx):
    """控制面板为横向布局：默认宽度大于高度，且按屏钳制。"""
    from control_panel import PANEL_HEIGHT, PANEL_WIDTH

    assert PANEL_WIDTH > PANEL_HEIGHT
    assert ctx.panel.width() >= ctx.panel.height()


def test_panel_ignores_legacy_vertical_geometry(ctx):
    """旧版竖版几何记忆（宽 480）不再适配：忽略并用新横向默认尺寸居中显示。"""
    from control_panel import ControlPanel, PANEL_WIDTH

    ctx.config["panelGeometry"] = "50,50,480,730"
    panel = ControlPanel(ctx)
    try:
        screen = QApplication.primaryScreen().availableGeometry()
        expected = min(PANEL_WIDTH, screen.width() - 40)
        assert panel.width() == expected
    finally:
        panel.close()


def test_panel_has_update_section(ctx):
    """面板包含版本与更新区：当前版本 / 检查更新按钮 / 更新源输入框。"""
    assert ctx.panel.update_btn.text() == "检查更新"
    assert cfg.APP_VERSION in ctx.panel.version_label.text()
    assert hasattr(ctx.panel, "update_url_edit")


def test_check_update_requires_url(ctx, monkeypatch):
    """未配置更新源时点击「检查更新」：提示先填写更新源，不发任何请求。"""
    from PySide6.QtWidgets import QMessageBox

    ctx.panel.update_url_edit.setText("")
    hints = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: hints.append(args)))
    called = []
    monkeypatch.setattr(ctx.panel.update_service, "check",
                        lambda url: called.append(url))

    ctx.panel._check_update()

    assert hints and "更新源" in hints[0][2]
    assert called == []


def test_save_update_url(ctx):
    """更新源地址保存进配置。"""
    ctx.panel.update_url_edit.setText("https://example.com/update.json")
    ctx.panel._save_update_url()
    assert cfg.load_config()["updateUrl"] == "https://example.com/update.json"


def test_panel_has_exit_entry(ctx):
    """面板底栏提供「退出助手」与常用入口（用户反馈找不到退出）。"""
    assert ctx.panel.exit_btn.text() == "退出助手"
    assert ctx.panel.open_storage_btn.text() == "打开收纳库"
    assert ctx.panel.open_config_btn.text() == "打开配置目录"
    assert "关于" in ctx.panel.about_btn.text()


def test_reclamp_brings_widgets_back_into_screen(ctx):
    """换屏/分辨率变化后：reclamp 把越界格子收回屏内并把修正写回配置。"""
    from PySide6.QtCore import QRect

    doc = next(w for w in ctx.widgets if w.title == "文档")
    screen = QApplication.primaryScreen().availableGeometry()
    doc.setGeometry(281, screen.height() + 2000, 330, 400)   # 整块在屏幕下方

    moved = ctx.reclamp_widgets()

    assert moved >= 1
    rect = QRect(doc.x(), doc.y(), doc.width(), doc.height())
    assert screen.intersected(rect) == rect                    # 完完整整在屏内
    assert cfg.load_config()["widgetPositions"]["文档"] == \
        f"{doc.x()},{doc.y()}"                                 # 修正后的位置已写回


def test_offscreen_saved_position_clamped_on_restart(ctx):
    """启动恢复：屏幕外的记忆位置应被钳回屏内，而不是"藏在屏幕外面"。"""
    from PySide6.QtCore import QRect

    positions = ctx.config.setdefault("widgetPositions", {})
    positions["文档"] = "281,99999"
    cfg.save_config(ctx.config)

    ctx.rebuild_widgets()

    doc = next(w for w in ctx.widgets if w.title == "文档")
    screen = QApplication.primaryScreen().availableGeometry()
    rect = QRect(doc.x(), doc.y(), doc.width(), doc.height())
    assert screen.intersected(rect) == rect
    assert cfg.load_config()["widgetPositions"].get("文档") != "281,99999"
