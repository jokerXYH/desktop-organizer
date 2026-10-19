# 桌面分类助手视觉与布局稳定性重构计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把桌面格子与控制面板重构为简约的瓷白纸感界面，并保证多显示器、重启和 Win10 自启场景下位置稳定、不重叠。

**Architecture:** 新增 `layout_store.py` 负责显示器身份、相对坐标映射与冲突消解；`AppContext` 只负责把恢复结果应用到窗口，并在副屏暂未就绪时保留原始坐标。新增 `theme.py` 统一格子与面板的设计 token，控制面板改为左侧导航 + 右侧分页内容。

**Tech Stack:** Python 3.9、PySide6 6.10、JSON 配置、Windows HKCU Run 注册表、pytest。

**Spec:** 用户已确认的方向：瓷白纸感 + 墨色文字 + 深青强调色；格子保持轻量收纳盒质感；面板改为左侧窄导航 + 右侧内容区。

## Global Constraints

- 不引入新的 UI 框架或第三方依赖。
- 保留现有收纳、撤销、搜索、更新、热键和托盘行为。
- 本地 `收纳库/`、`build/`、`dist/` 不得删除或覆盖。
- 位置恢复在副屏未就绪时不得覆盖已保存坐标。
- 所有新行为先写失败测试，再实现。

## Review Focus

- 副屏在开机后延迟出现：格子临时落在主屏，但不得改写原副屏坐标。
- 显示器分辨率变化：保存的布局按屏幕相对位置映射，不能堆到同一角落。
- 同一布局键重复或历史数据重叠：启动时消解明显重叠。
- Win10 Run 项路径过期：每次启动自动修正到当前脚本或 exe。
- 大目录刷新：列表构建期间暂停重绘，扫描使用单次目录遍历。

---

### Task 1: 显示器感知的布局存储

**Files:**
- Create: `layout_store.py`
- Test: `tests/test_layout_store.py`

**Interfaces:**
- Consumes: `PySide6.QtCore.QRect`
- Produces:
  - `screen_identity(screen) -> str`
  - `make_layout_record(rect, screen) -> dict`
  - `restore_layout(record, screens) -> LayoutResolution | None`
  - `resolve_overlaps(rects, screens, gap=16) -> list[QRect]`
  - `LayoutResolution(desired, rect, provisional, changed, screen)`

- [ ] **Step 1: 写失败测试**：精确恢复、副屏缺失返回 provisional、分辨率变化按相对坐标映射、离谱旧坐标回退、重叠消解。
- [ ] **Step 2: 运行 `pytest tests/test_layout_store.py -q`**，确认因模块不存在而失败。
- [ ] **Step 3: 实现 `layout_store.py`**。
- [ ] **Step 4: 运行测试至通过**。

### Task 2: AppContext 接入布局存储

**Files:**
- Modify: `config.py`
- Modify: `main.py`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `restore_layout`、`make_layout_record`、`resolve_overlaps`
- Produces:
  - `config["widgetLayouts"]: dict`
  - `AppContext._restore_widget_geometry(widget, key, positions, sizes)`
  - `AppContext._save_widget_layout(widget)`
  - `widget._layout_provisional: bool`

- [ ] **Step 1: 写失败测试**：副屏缺失时 `widgetLayouts` 原坐标不变、`widgetPositions` 不被改写；屏幕恢复后重新落位；明显重叠被消解。
- [ ] **Step 2: 运行相关测试确认失败**。
- [ ] **Step 3: 接入恢复、保存、屏幕变化回放和关机保存**。
- [ ] **Step 4: 运行相关测试至通过**。

### Task 3: Win10 自启与单实例

**Files:**
- Modify: `autostart.py`
- Modify: `main.py`
- Test: `tests/test_autostart.py`
- Create: `tests/test_instance_guard.py`

**Interfaces:**
- Consumes: `SCRIPT_PATH`、`sys.executable`
- Produces:
  - `autostart.AUTOSTART_ARG = "--autostart"`
  - `autostart.build_autostart_command(exe, script="", extra_args=None)`
  - `autostart.sync_autostart(script_path) -> bool`
  - `instance_guard.acquire(path) -> QLockFile | None`

- [ ] **Step 1: 写失败测试**：自启命令包含 `--autostart`、旧命令可识别为过期、第二个实例锁失败。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现自启同步与 QLockFile 单实例。**
- [ ] **Step 4: 运行测试至通过，并只读检查当前注册表命令。**

### Task 4: 性能优化

**Files:**
- Modify: `widgets.py`
- Test: `tests/test_widgets_smoke.py`

**Interfaces:**
- Consumes: `FileWidget.refresh`、`FileWidget.paintEvent`
- Produces:
  - `FileWidget._background_pixmap()`
  - `FileWidget._invalidate_background()`
  - 单次 `os.scandir` 刷新
  - 拖动期间复用屏幕矩形快照

- [ ] **Step 1: 写失败测试**：背景缓存只生成一次；刷新后列表条目排序与数量不变。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现缓存、scandir 和拖动屏幕快照。**
- [ ] **Step 4: 运行相关测试至通过。**

### Task 5: 瓷白纸感视觉系统

**Files:**
- Create: `theme.py`
- Modify: `widgets.py`
- Modify: `main.py`
- Test: `tests/test_widgets_smoke.py`

**Interfaces:**
- Consumes: PySide6 QSS、QPainter
- Produces: 统一的 `PALETTE`、`GRID_QSS`、`PANEL_QSS`、`DIALOG_QSS`

- [ ] **Step 1: 写结构测试**：格子背景缓存、列表 QSS 使用深青强调色、菜单 QSS 不再使用旧暗色。
- [ ] **Step 2: 实现 theme 与格子重绘。**
- [ ] **Step 3: 用截图工具生成 hero 截图并目视检查。**

### Task 6: 控制面板重构

**Files:**
- Modify: `control_panel.py`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `theme.PANEL_QSS`、现有所有控件属性
- Produces:
  - 左侧导航 `self.nav_buttons`
  - 分页容器 `self.pages`
  - `ControlPanel._switch_page(index)`

- [ ] **Step 1: 写结构测试**：四个导航页存在、关键控件仍可访问、窗口仍为横向。
- [ ] **Step 2: 用左侧导航 + 分页重构布局。**
- [ ] **Step 3: 运行冒烟测试与截图工具。**

### Task 7: 全量验证

- [ ] **Step 1: 运行 `pytest -q`。**
- [ ] **Step 2: 运行 `python -m compileall -q .`（排除 build/dist）。**
- [ ] **Step 3: 重新生成 README 截图并目视检查。**
- [ ] **Step 4: 重启本机程序，验证显示器恢复、托盘与自启项。**
