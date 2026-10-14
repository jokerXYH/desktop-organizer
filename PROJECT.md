# 桌面分类助手（v2.6）— AI 交接文档

> 本文档写给后续接手的 AI / 开发者：v2 源码已按本文档重建完成并经过 v2.1 迭代
> （见第 13 节），后续以代码 + 本文档为行为基准。收纳库数据、配置文件、注册表自启项均正常。

## 1. 项目是什么

Windows 桌面文件整理工具（Python + PySide6，独立自研）：
当前版本 v2.6.0，版本历史见仓库根的 CHANGELOG.md。
- 桌面右侧悬浮一排**分类格子窗口**（无边框、半透明、置底、不抢任务栏）
- 一键整理：把桌面顶层文件/文件夹按扩展名分类移入收纳库
- 拖拽：把任意文件/文件夹拖进格子即移入对应分类文件夹
- 撤销：所有移动记录批次化，可"撤销上次"或"恢复全部"
- 附加：真实图标预览、位置记忆、开机自启、游戏模式、收纳库搜索、F7 全局热键、系统托盘

**性能红线**：不允许轮询定时器；刷新必须事件驱动（QFileSystemWatcher + 防抖）；游戏模式下必须零资源占用（隐藏全部窗口 + 暂停监听）。

## 2. 运行环境（实测可用）

| 项 | 值 |
|---|---|
| Python | 3.9+（GUI 启动建议使用同目录的 pythonw.exe，无控制台窗口） |
| 依赖 | PySide6 6.10.3、pytest 8.4.2（requirements.txt 尚存） |
| 用户真实桌面 | 通过注册表 User Shell Folders 解析（可能被重定向到其它盘，必须用 `get_desktop_path()` 读取） |
| 收纳库 | 项目目录下的 `收纳库` 文件夹（分类子目录由程序自动创建） |
| 配置目录 | `%APPDATA%\MyDeskOrganizer\`（settings.json + history.json 仍在） |
| 项目目录 | 本仓库根目录 |
| ⚠ 注册表自启项 | HKCU Run 键 `MyDeskOrganizer` 指向 main.py；如不再使用，请删除该键 |

## 3. 模块与职责（共 6 个源文件）

```
项目根目录\
├── main.py            程序入口：AppContext / 托盘 / F7 热键 / 文件监听 / 位置与自启编排
├── config.py          配置与历史 JSON 持久化（纯逻辑，无 GUI 依赖）
├── organizer.py       分类/整理/撤销/拖入移动/收纳库搜索（纯逻辑，无 GUI 依赖）
├── widgets.py         FileWidget 桌面格子窗口（图标、预览切换、位置记忆、暗色绘制）
├── control_panel.py   ControlPanel 控制面板（按钮、开关、搜索、暗色 QSS）
├── autostart.py       HKCU Run 键读写（纯 winreg，便于测试）
├── requirements.txt   PySide6 + pytest
└── tests\             conftest.py + 5 个测试文件，共 80 用例（含 GUI/集成冒烟）
```

## 4. organizer.py（核心逻辑层）

### 4.1 分类规则（常量 `CATEGORY_RULES`，键顺序即格子顺序）

| 分类 | 扩展名（小写带点） |
|---|---|
| 文档 | .pdf .doc .docx .xls .xlsx .ppt .pptx .txt .md .csv .epub .caj .rtf |
| 图片 | .jpg .jpeg .png .gif .bmp .webp .svg .ico .psd .ai .tif .tiff |
| 视频 | .mp4 .mkv .avi .mov .wmv .flv .webm .m4v |
| 音频 | .mp3 .wav .flac .aac .ogg .m4a .wma |
| 压缩包 | .zip .rar .7z .tar .gz .bz2 .iso .cab |
| 安装包 | .exe .msi .apk .bat .cmd .ps1 .jar |
| 代码 | .py .js .ts .html .css .c .cpp .h .java .go .rs .sql .json .xml .yml .yaml |
| 快捷方式 | .lnk .url |
| 其他 | set()（兜底：未知/无扩展名） |

另有常量：
- `FOLDER_CATEGORY = "文件夹"`（目录不按扩展名分类，统一进此类）
- `IGNORED_NAMES = {"desktop.ini", "dumpstack.log.tmp", "swapfile.sys"}`；`.` 开头的隐藏文件也跳过

### 4.2 数据结构

```python
@dataclass
class MoveRecord:
    src: str; dst: str; time: str; batch: str   # ISO 秒级时间；batch = uuid4().hex[:12]
    # to_dict() / from_dict()（容忍缺字段）
```

### 4.3 函数签名与行为

```python
get_desktop_path() -> str
    # winreg 读 HKCU\...\User Shell Folders\Desktop，expandvars 后返回；OSError 时退回 ~/Desktop
is_within(path, root) -> bool
    # abspath 后判断 path==root 或 startswith(root+os.sep)，防"把收纳库移进自己"
classify(filename) -> str            # 只看扩展名；未命中→"其他"
unique_path(path) -> str             # 冲突时 name.ext -> "name (2).ext" 递增
organize_desktop(desktop_path, storage_root, include_shortcuts=False, include_folders=True)
    -> List[MoveRecord]              # 同一 batch；跳过 IGNORED/隐藏/收纳库内部路径(is_within 保护)；
                                     # 目录→"文件夹"，文件→classify；单文件失败 continue 不中断；
                                     # desktop 不可访问抛 RuntimeError
undo_moves(records) -> List[MoveRecord]
    # reversed 逆序恢复；dst 不存在或 src 为空跳过；src 已占用则 unique_path 改名恢复
move_into(src, folder) -> str        # 文件/文件夹皆可；不存在抛 FileNotFoundError；
                                     # is_within(src, folder) 抛 ValueError；重名自动加后缀
search_storage(root, keyword, limit=100) -> List[(分类名, 完整路径)]
    # sorted(os.listdir(root)) 逐分类 os.walk 递归；kw 小写包含匹配；空 kw 或 limit<=0 返回 []
```

## 5. config.py

路径常量：`CONFIG_DIR = %APPDATA%\MyDeskOrganizer`，`CONFIG_PATH=settings.json`，`HISTORY_PATH=history.json`，`HISTORY_MAX_RECORDS=2000`。

`DEFAULT_CONFIG`（全量键，v2）：

```python
{
    "storageRoot": 项目目录下的"收纳库"路径（默认按 config.py 所在目录自动计算）,
    "includeShortcuts": False,   # 整理是否收走 .lnk/.url
    "includeFolders": True,      # 整理是否收走文件夹
    "autostart": False,          # 记录值；勾选框实际以注册表为准
    "previewIcons": True,        # True=图标视图 False=紧凑列表（全局）
    "gameMode": False,           # 游戏模式状态记忆
    "widgetPositions": {},       # {分类名: "x,y"} 位置记忆
    "categories": ["文档","图片","视频","音频","压缩包","安装包","代码","文件夹","其他"],
}
```

函数：
- `load_config()`：deepcopy 默认 → merge 文件。**v1 兼容**：文件数据里没有 `V2_MARKER_KEY="widgetPositions"` 视为旧版 → `ensure_folder_category()` 给 categories 末尾补 `"文件夹"`；v2 配置不做强制补（尊重用户取消勾选）。损坏文件静默退默认。
- `save_config()`：makedirs + json.dump(ensure_ascii=False, indent=2)。
- 历史：`load_history() / save_history() / append_records()`（超出 2000 丢最旧）、`pop_last_batch()`（按最后一条的 batch 取出并删除，供"撤销上次"）。
- 位置工具：`format_position(x,y) -> "x,y"`；`parse_position(text) -> (x,y) | None`（任何非法输入返回 None，不抛异常）。

history.json 结构：`[{"src","dst","time","batch"}]`。

## 6. autostart.py

```python
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME   = "MyDeskOrganizer"
build_autostart_command(exe_path, script_path) -> '"exe" "script"'
    # exe 是 python.exe 且同目录存在 pythonw.exe 时替换为 pythonw（纯函数可测）
get_autostart_enabled() -> bool      # QueryValueEx 探测
set_autostart(script_path, enable)   # KEY_SET_VALUE 写 REG_SZ / DeleteValue；删除时 FileNotFoundError 视为成功
```

## 7. widgets.py（FileWidget）

### 7.1 常量

```python
WIDGET_WIDTH=270  WIDGET_HEIGHT=360  BG_ALPHA=232  CORNER_RADIUS=14  TITLE_BAR_HEIGHT=36
ICON_SIZE=32  GRID_CELL_W=78  GRID_CELL_H=74
MAX_LIST_ITEMS=200          # 大目录防卡：超出只渲染 200 条 + 一条"… 共 N 项"提示（Qt.NoItemFlags）
POSITION_SAVE_DELAY_MS=600  # 位置保存防抖
COLOR_BG=QColor(28,30,36,232)  COLOR_BORDER=#404450  COLOR_TEXT="#e8eaed"  COLOR_ACCENT="#4cc2ff"
```

### 7.2 窗口与构造

```python
FileWidget(title, folder, on_drop,
           on_position_changed=None,   # (FileWidget) 拖动防抖后回调
           on_preview_changed=None,    # (bool) 该格内切换预览后回调（全局生效）
           preview_icons=True, parent=None)
```

- 窗口 flags：`Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint | Qt.Tool` + `WA_TranslucentBackground` + `setAcceptDrops(True)`
- `paintEvent`：QPainterPath 圆角矩形填充 COLOR_BG + 1px 边框 + 标题分隔线
- 标题栏：分类名 + 条目数（"N 项"）+ stretch + "打开"QLabel（点击开资源管理器，蓝色手型光标）
- 列表 QListWidget：`ScrollPerPixel`、右键 `CustomContextMenu`、双击打开；QSS 见 `LIST_QSS`（透明底+悬停/选中高亮）
- `self.title` 必须保存（位置记忆以分类名为键）；条目完整路径存 `Qt.UserRole`（tooltip 同步显示）

### 7.3 关键行为

- **图标**：全局单例 `get_icon_provider()`（QFileIconProvider），仅图标模式取 `provider.icon(QFileInfo(path))`；紧凑模式不取图标（省资源）。列表按"文件夹在前、文件在后"排序（均 `key=str.lower`）。
- **预览切换 `set_preview(icons)`**：
  - 图标视图：`IconMode` + IconSize 32 + GridSize 78x74 + WordWrap + `Adjust` + `LayoutMode Batched`(batchSize 50)
  - 紧凑列表：`ListMode` + `setGridSize(QSize())` 复位 + 不取图标
- **位置记忆**：`showEvent` 置 `_position_ready=True`（首次 show 前不保存，避免覆盖记忆值）；`mouseMoveEvent` 拖动时 `_pos_timer.start(600)`；到期 `_emit_position()` 回调；`hideEvent` 若定时器活跃则立即停表并回调一次。
- **右键菜单**（暗色 QSS 内联）：条目上 → 打开/打开所在位置；任意位置 → 打开文件夹 / "预览方式"子菜单（QActionGroup 互斥：图标视图/紧凑列表）/ 刷新。
- **拖放**：`dragEnterEvent` 有 urls 即 accept；`dropEvent` 收集 `toLocalFile()` 且 `os.path.exists` 的路径 → `on_drop(paths, self)`。
- `open_in_shell(path)`：nt 用 `os.startfile`，macOS `open`，其余 `xdg-open`（`sys_darwin()` 拆出便于打桩）。

## 8. control_panel.py（ControlPanel）

- 尺寸 480x660，`PANEL_QSS` 暗色（#1e2025 底、#4cc2ff 强调、圆角 8、一键整理按钮 objectName=`accentBtn`）。
- 自上而下：
  1. 按钮行：一键整理桌面(accent) / 撤销上次整理 / 恢复全部
  2. 收纳目录：QLineEdit(editingFinished 保存并 `on_widgets_rebuild`) + 浏览…
  3. 开关区（QGridLayout）：开机自启 / 游戏模式（隐藏格子并暂停监听）/ 整理时收走快捷方式 / 整理时收走文件夹 / 预览方式 QComboBox（图标视图/紧凑列表）
  4. 分类格子勾选区：`[n for n in CATEGORY_RULES if n!="其他"] + [FOLDER_CATEGORY, "其他"]`，勾选即保存并重建
  5. 搜索区：QLineEdit(placeholder"输入文件名关键词") + 结果 QListWidget + "打开所在位置"按钮
  6. 状态栏 status_label（ctx.refresh_status 更新）
- **搜索**：textChanged → 300ms 单发 QTimer 防抖 → `org.search_storage(root, kw, 100)` → 结果项文字=文件名、UserRole=完整路径、tooltip=`[分类] 路径`；双击打开；状态栏"搜索到 N 个结果（已达上限…）"。
- 自启勾选初始值读 `autostart.get_autostart_enabled()`（注册表为真源）；切换调 `ctx.set_autostart`，OSError 时回滚勾选 + QMessageBox。
- 对外同步方法（内部 blockSignals，防循环触发）：`set_game_mode_checked(checked)`、`set_preview_index(icons)`。
- 回调依赖 ctx：`on_organize(count)` / `on_undo(restored, skipped)` / `on_widgets_rebuild()` / `set_autostart(bool)` / `set_game_mode(bool)` / `set_preview(bool)` / `refresh_status(text)`。

## 9. main.py（AppContext 与入口）

### 9.1 常量

```python
WM_HOTKEY=0x0312  VK_F7=0x76  MOD_NOREPEAT=0x4000  HOTKEY_ID=0xB00B
SCRIPT_PATH = os.path.abspath(__file__)      # 写自启项用
REFRESH_DEBOUNCE_MS = 400                    # 文件事件防抖
APP_QSS  # 全局暗色 QMenu / QToolTip（"分"字圆角托盘图标 make_tray_icon() 程序化绘制）
```

`GlobalHotkeyFilter(QAbstractNativeEventFilter)`：`event_type==b"windows_generic_MSG"` 时解析 `wintypes.MSG`，`WM_HOTKEY && wParam==HOTKEY_ID` 触发回调。`RegisterHotKey(None, id, MOD_NOREPEAT, VK_F7)` 仅 win32 注册。

### 9.2 AppContext 职责

- 持有 config / widgets / panel / tray / act_game_mode / `QFileSystemWatcher` + 400ms 单发 QTimer（timeout→refresh_all）。**没有任何轮询定时器**。
- `rebuild_widgets()`：关旧格子 → ensure_storage_dirs → 逐分类建 FileWidget：
  - 有记忆位置（`parse_position` 成功）→ `move()` 恢复；
  - 无 → 屏幕右侧竖排（`screen.right()-300` 起，超高换列）且标记 `need_save_default` → 收尾 `save_all_positions()` 持久化首 layout；
  - 最后 `_sync_watcher_paths()`。
- `_sync_watcher_paths()`：监听集 = 收纳根目录 + 各已存在分类子目录；与 `watcher.directories()` 差量 add/remove。
- `_on_storage_changed(_path)`：`_refresh_timer.start(400)` + `QTimer.singleShot(450, _sync_watcher_paths)`（新建目录补挂监听）。
- `handle_position_changed(widget)`：`config["widgetPositions"][widget.title] = format_position(...)` → 立即 save_config。
- `handle_preview_changed(icons)`：写 config → 其他格子 `set_preview` → `panel.set_preview_index` 同步；`set_preview` 供面板调用（幂等短路）。
- `set_autostart(enabled)`：`autostart.set_autostart(SCRIPT_PATH, enabled)` + config 记录。
- `set_game_mode(enabled)`：写 config；全部格子 `setVisible(not enabled)`；开→`removePaths(watcher.directories())` + 托盘气泡提示；关→`_sync_watcher_paths()` + `refresh_all()`；同步面板勾选与托盘菜单项（blockSignals）。
- `toggle_widgets()`（F7/托盘）：游戏模式中按 F7 = 退出游戏模式；否则显隐切换。
- `handle_drop(paths, widget)`：batch=uuid4().hex[:12]，逐个 `org.move_into`（捕获 OSError/shutil.Error/ValueError 计失败），成功记录 MoveRecord 入 history 并 `widget.refresh()`；状态栏报成功/失败数。
- `main()`：`setQuitOnLastWindowClosed(False)` → 建 ctx → 注册热键 → 托盘菜单（控制面板 / 显示隐藏格子(F7) / 游戏模式(checkable) / 退出）→ `rebuild_widgets()` → 若 config gameMode=True 则保持游戏模式（面板不弹），否则 `show_panel()` → `app.aboutToQuit.connect(save_all_positions)` → exec 后 UnregisterHotKey。

## 10. 测试基线（80 用例，必须全绿）

```bash
python -m pytest tests/ -q    # 期望 80 passed
```

- `tests/conftest.py`：仅把项目根插入 sys.path。
- `test_organizer.py`：classify 全分类/大小写/未知扩展；unique_path 冲突；organize（分类归位、**默认收走文件夹→"文件夹"分类且内部结构保持**、include_folders=False 留桌面、文件夹同名加 "(2)"、快捷方式默认保留、系统/隐藏文件跳过、同名加后缀、batch 唯一、非法桌面抛 RuntimeError）；undo（正常恢复/dst 缺失跳过/src 冲突改名）；move_into（文件/文件夹/缺源抛错/**自身与内部路径抛 ValueError**）；search_storage（关键词命中两分类/大小写不敏感/`limit=0` 返回空/空关键词与根目录缺失不报错）。
- `test_config.py`：默认值含 v2 五键；旧配置自动补"文件夹"；v2 配置不强制补；位置序列化互逆与非法输入；配置读写/损坏兜底/历史追加/整批弹出/2000 上限。
- `test_autostart.py`：命令引号；python.exe→pythonw.exe 替换；已是 pythonw 或 pythonw 缺失时不替换。（不碰真实注册表）

已知教训：`search_storage` 的 limit 校验必须放在 append 之前（v2 曾因先 append 后判 `>=limit` 导致 limit=0 返回 1 条）。

## 11. 重建步骤建议

1. `requirements.txt` 保留：`PySide6>=6.6`、`pytest>=8`（已装，无需重装）。
2. 按 organizer.py → config.py → autostart.py → widgets.py → control_panel.py → main.py 顺序重建（纯逻辑层先行的原因：GUI 依赖其 API）。
3. 补齐 tests 三件套 → pytest 全绿。
4. 实机验收：
   - 用 pythonw 启动 `main.py`，桌面右侧出现暗色格子且**文件带真实图标**；
   - 拖动格子→重启→位置复原；
   - 面板搜索输入关键词→结果列表填充；
   - 托盘勾选"游戏模式"→格子与面板消失、托盘气泡提示；
   - 勾选"开机自启"→`reg query HKCU\...\Run /v MyDeskOrganizer` 应返回 `"<pythonw 路径>" "<main.py 路径>"`。
5. 杀旧进程：重装/重启前 `Get-Process pythonw | Stop-Process -Force`（本机曾有残留进程导致窗口重复）。

## 12. 数据状态说明

- 收纳库为项目目录下的普通文件夹，内容与用户文件**均保存在本机，不随仓库发布**。
- `%APPDATA%\MyDeskOrganizer\settings.json` 与 `history.json` 保存配置与可撤销历史。
- 整理后桌面主要剩余快捷方式与用户自行保留的文件。

## 13. v2.1 修复与桌面体验增强（2026-10-14）

### 修复
- 游戏模式竞态：`_on_storage_changed` / `_sync_watcher_paths` 在游戏模式下直接返回，
  开启游戏模式时停掉待执行的刷新定时器——监听不会再被防抖回调偷偷恢复。
- 单实例保护（QLocalServer/QLocalSocket）：重复启动只唤起已有实例的控制面板并自行退出。
- F7 注册失败时托盘气泡提示；启动恢复游戏模式不再重复弹提示（`set_game_mode(..., notify=False)`）。
- F7 隐藏状态跨重建保留（`_widgets_hidden` + `_apply_visibility`）。
- 位置恢复做屏幕可见性校验（`_position_visible`），越界（换分辨率/拔显示器）回退默认布局。
- `pop_last_batch` 兼容末条无 batch 的历史（单条弹出，"撤销上次"始终可用）。

### 桌面体验
- 格子支持"拖出"：列表多选（ExtendedSelection），选中文件以标准 file URL 拖到
  资源管理器/桌面/其它格子；同盘移动、跨盘复制走系统规则（Shift/Ctrl 可强制）；
  拖动结束自动刷新（`FileListWidget`，`mimeData`/`startDrag` 重写）。
- 拖入高亮：文件悬停格子时边框高亮（`_drag_active`）；拖入结果状态栏实时反馈；
  拖回原格子静默忽略，不计失败。
- 空格子显示"拖入文件或文件夹即可收纳"引导条。
- 右键菜单新增：复制路径、删除到回收站（确认后 `organizer.move_to_recycle_bin`）、
  锁定位置（持久化到 `lockedWidgets`）。
- 窗口拖动靠近其它格子时左/上边缘自动对齐（`SNAP_DISTANCE=10`）。
- 托盘左键单击显示控制面板；"恢复全部"增加确认对话框。

### 新增配置键
- `lockedWidgets: []` —— 锁定了位置的分类名列表。

### 新增测试
- `tests/test_widgets_smoke.py`：offscreen 下验证刷新/空状态/拖出 MIME/锁定标签。
- `tests/test_app_smoke.py`：AppContext 集成——拖入/拖回、游戏模式守卫、
  隐藏状态跨重建、位置越界回退。
- 回收站删除与空 batch 撤销的回归用例。

## 14. v2.2 自定义格子与视觉优化（2026-10-14）

### 自定义格子（新增能力）
- 新建：控制面板分类区"＋ 新建格子"按钮，输入名称即在收纳库创建同名文件夹并生成格子。
- 重命名：格子右键"重命名格子…"（仅自定义格子可用；内置分类参与自动整理规则，
  禁用并提示）。重命名会同步：文件夹改名、widgetPositions / lockedWidgets 键、
  以及历史记录中该目录下的 dst 路径（`cfg.retarget_history_prefix`），保证仍可撤销。
- 删除：格子右键"删除此格子"，仅移除格子，收纳库文件夹与文件全部保留；
  内置分类删除后可在控制面板重新勾选恢复。
- 名称校验 `cfg.validate_grid_name`：非空、不含 `\/:*?"<>|`、不以句点结尾、不重名。
- 控制面板分类勾选区改为动态重建（`_rebuild_category_checks`），自定义格子自动出现。

### 整理前预览确认（新增）
- `organizer.preview_organize`：与整理共用扫描逻辑（`_iter_candidates`），不产生任何改动。
- 面板开关"整理前先预览确认"（配置键 `confirmOrganize`，默认开）：整理前弹摘要
  （各项分类计数 + 文件夹数量提醒），确认后才真正移动。

### 面板几何记忆（新增）
- 配置键 `panelGeometry`（"x,y,w,h"）：关闭面板/退出程序时保存，启动时恢复；
  越界或首次运行则居中显示。工具函数 `cfg.format_geometry / parse_geometry`。

### 图标视图视觉优化
- 网格单元 78x84 → 76x84：修复每行被挤成 2 列、右侧大片留白的问题，现在每行排满 3 列。
- 图标 32px → 38px；条目文字显式水平居中（图标模式下图标与文字对齐更整齐）。
- 列表滚动条改为暗色细滚动条（8px、圆角、悬停变主题蓝），替换难看的系统默认亮色滚动条。
- 图标模式启用 `setUniformItemSizes(True)`，排布更整齐、绘制更省。

### 启动注意事项
- 若以 `Start-Process -WindowStyle Hidden` 启动本应用，Windows 会把隐藏状态应用到
  面板的首次显示上，导致控制面板一直不可见；正常双击 / pythonw main.py / 注册表自启
  均不受影响。开发调试时请正常启动。

### 新增测试（合计 58）
- 名称校验 / 几何序列化 / 历史路径改写（`test_config.py`）。
- 整理预览不移动文件、遵守收录开关（`test_organizer.py`）。
- 格子新建→重命名→删除全流程、非法重命名拒绝、面板几何记忆（`test_app_smoke.py`）。

## 15. v2.3 右键菜单修复 / 尺寸缩放 / 剪切粘贴（2026-10-14）

### 重要修复：右键菜单从不弹出（静默异常）
- `_show_menu` 中 `menu.setStyleSheet((QSS...) % COLOR_TEXT)` 写成了
  `menu.setStyleSheet(QSS...) % COLOR_TEXT`——`%` 作用于 `setStyleSheet()` 的返回值 None，
  抛 `TypeError` 被 Qt 槽机制静默吞掉，**菜单永远不会显示**（stderr 可见 traceback）。
- 修复：`%` 移入括号内，先格式化字符串再传入；并新增
  `test_context_menu_items` 用 `_build_menu`（新拆出的纯构建方法）做自动回归。

### 右键菜单重构（五个分区结构）
- 五个分区：条目操作（打开/打开所在位置/复制路径/剪切/删除到回收站）→
  粘贴 → 外观与尺寸（打开文件夹/预览方式▸/格子尺寸▸/锁定位置）→
  格子管理（重命名格子…/新建格子…/删除此格子）→ 刷新。
- 右键即可新建/重命名/删除格子（内置分类的重命名项禁用并带提示 tooltip；
  新建格子弹窗与面板"＋ 新建格子"共用 `AppContext.create_grid_interactive`）。
- 剪切/粘贴：`AppContext.file_clipboard` 内部剪切板，粘贴复用 `handle_drop`
  移动并写历史（可撤销）；菜单中显示条目数，"无内容"时禁用。

### 格子尺寸缩放（Ctrl+滚轮 / 菜单档位）
- 档位 `SCALE_LEVELS=(0.75, 0.85, 1.0, 1.15, 1.3, 1.5)`，几何按比例缩放
  （窗口 270x360 基准、图标 38px、单元 76x84、字号联动），`widgets.scale_geometry` 纯函数。
- 交互：Ctrl+滚轮上/下（列表与标题区均生效）、右键"格子尺寸"子菜单
  （当前百分比 / 放大 / 缩小 / 恢复默认）。
- 持久化：配置键 `widgetSizes`（{分类名: "宽,高"}，复用位置序列化格式），
  重命名格子时随位置一起迁移；实机验证（合成输入）：100%→115%→100% 精确生效。

### 新增测试（合计 63）
- `scale_geometry` 基准值/钳制、`_zoom` 档位步进与回退（test_widgets_smoke.py）。
- 右键菜单五个分区关键条目、内置/自定义格子的重命名可用性（test_widgets_smoke.py）。
- 缩放持久化与重建恢复、剪切→粘贴跨格子移动（test_app_smoke.py）。

## 16. v2.4 自由缩放 / 单格预览 / 路径映射 / 自动整理 / 拼音搜索 / 多热键（2026-10-14）

### 格子尺寸：手动拖拽调整（修正 v2.3 的"整体缩放"理解偏差）
- 拖拽格子**右边缘 / 下边缘 / 右下角**可自由调整窗口宽高（热区 6px，最小 170x150，
  最大 1200x1200），内容自动重排（变宽=更多列），图标不跟着缩放；锁定位置同时锁住调整。
- Ctrl+滚轮与右键"尺寸调整"改为只缩放**图标/列宽档位**（0.75~1.5，窗口尺寸不变）；
  子菜单含"窗口恢复默认大小"。
- 配置分离：`widgetSizes`（窗口宽高，600ms 防抖）、`widgetScales`（图标缩放比）。

### 预览方式：按单格子独立
- 格子右键"预览方式"只切换该格（存 `widgetPreviews`）；面板下拉框=批量应用到全部格子
  （同时作为新格子的默认值）。
- **图标兜底**：QFileIconProvider 对个别 .lnk/.exe 返回空图标（实测"天翼云盘.lnk"），
  新增 `shell_fallback_icon`（SHGetFileInfoW + GDI 转 QImage，含无 alpha 老图标处理）兜底。

### 路径映射（把任意现有文件夹变成格子，不搬文件）
- 面板"映射文件夹…"按钮 / 格子右键"映射文件夹…"；禁止映射收纳库内部。
- 映射格子纳入文件监听（`_sync_watcher_paths`）与搜索（`search_storage(extra_dirs=...)`）
  与重建目录逻辑（`ensure_storage_dirs` 跳过）；重命名只改显示名、不碰外部文件夹；
  删除只移除格子、不动外部文件。

### 自动整理新桌面文件（默认关闭）
- 配置 `autoOrganize` / `autoOrganizeDelay`（面板可选 30 秒 / 5 分钟 / 30 分钟）。
- 事件驱动：桌面纳入 QFileSystemWatcher → 变化后静置防抖（dwell）→ 仅收纳"开启之后
  新出现"的条目（`organize_desktop(only_names=...)`），旧文件绝不触碰；
  游戏模式完全暂停；启动与退出游戏模式时重新建立基线；整理结果写入历史（可撤销）。

### 拼音搜索
- 新增依赖 pypinyin；搜索支持中文全拼与首字母（"baogao"/"bg" → 报告），
  与子串匹配并存；pypinyin 缺失时自动退化为纯子串匹配。

### 快捷键扩展（全局热键）
- F7 显示/隐藏格子、F8 控制面板、F9 游戏模式；注册失败逐个托盘提示，退出时全部注销。

### 新增测试（合计 71）
- organizer：extra_dirs 搜索、拼音搜索（importorskip）、only_names 过滤。
- widgets：图标缩放不影响窗口尺寸、自由缩放与持久化回调、边缘热区判定（锁定禁用）。
- app：图标/窗口尺寸持久化与重建恢复、单格预览互不影响、路径映射全流程、
  自动整理只处理新文件且不碰旧文件。

## 17. v2.5 图标修复 / 紧凑列表 / 对齐指示线（2026-10-14）

### 快捷方式图标"发白"修复（重要）
- 现象：本机上 QFileIconProvider 对**所有** .lnk/.url 返回"白页"坏图标（彩色度采样=0.00），
  .pdf/.docx 等正常；Steam 游戏快捷方式（.url）全部显示白色页面。
- 修复（`widgets.get_file_icon`，带 (路径, mtime) 缓存）：
  1. `_icon_stats` 采样检测"低彩色 + 高白色"的疑似坏图标；
  2. `.url` 优先直读其 `IconFile=`（Steam 游戏图标实测 23 个里 22 个可用）；
  3. 否则走 Shell 兜底（SHGetFileInfoW），彩色度更高才替换；
  4. 两种预览模式共用同一图标管线。
- 实测："快捷方式"格子 48 项中 47 项恢复真实彩色图标（修复前为 0）。

### 紧凑列表优化
- 行距更松弛（`COMPACT_LIST_QSS` 追加 item padding），不再"太紧凑"；
- 左侧新增 16px 微小图标（图标与文字同排显示）。

### 拖动对齐指示线（智能参考线）
- 吸附规则扩展为：左缘 / 右缘 / 上缘 / 下缘对齐（阈值 SNAP_DISTANCE=10px）；
- 达到吸附时由 `GuideOverlay`（置顶、鼠标穿透、不抢焦点）绘制主题蓝 2px 参考线，
  松开鼠标即隐藏；通过完整事件管线（QApplication.sendEvent）自动化验证。

### 新增测试（合计 77）
- `_icon_stats` 白页识别、`_url_icon_file` 解析（含缺失）、`get_file_icon` 非空。
- 拖动完整管线：吸附对齐 + 指示线显示/隐藏。
- 紧凑列表：16px 图标 + 行距样式。

## 18. v2.6 八方向缩放 / Steam 图标根治 / 搜索格子 / 重复副本清理（2026-10-14）

### Steam（.url）图标发白的真正根因与修复
- v2.5 的白页检测阈值是"彩色度 < 0.02"，但**真实 Windows 平台上 .url 白页图标的
  彩色度是 0.05**，恰好漏判——修复分支从未对 .url 生效（此前离屏诊断环境又恰好把
  所有 .lnk 渲染成白页，误导了判断，offscreen 的 QFileIconProvider 行为与真实平台不同）。
- 修复：`.url` 不再依赖阈值——只要文件里写了 `IconFile=` 就**无条件优先直读**该图标
  （与资源管理器同款机制），并立即 `pixmap()` 渲染验证防懒加载假成功；
  其余文件保留"低彩色+高白色"检测 + Shell 兜底（阈值放宽至 0.12/0.30）。
- 实测：22 个 .url 中 20 个恢复真实游戏图标（其余 2 个为图标文件缺失/本身偏白）。

### 格子缩放：八方向拖拽 + 缩放参考线 + 记忆
- `_edge_at`/`_resize_geometry` 重写：上/下/左/右四条边 + 四个角共 8 个热区，
  左/上边缘拖动会同时改变位置（几何与位置一起防抖保存）。
- 拖动边缘时同样触发吸附与**智能参考线**：移动中的边缘与其它格子的左/右缘、上/下缘
  相差 ≤10px 时吸附并显示指示线。
- 鼠标指针按方向切换（横向/纵向/两种对角线）。

### 桌面搜索格子（SearchWidget）
- 新控件：常驻桌面、可随时删除（右键"删除此格子"或面板开关），输入防抖 300ms，
  支持拼音；结果双击打开、右键可复制路径/剪切（到目标格子粘贴即移动）。
- 位置/尺寸/图标缩放/锁定等记忆复用 FileWidget 机制，键名固定为"搜索"；
  默认位置屏幕左上区域，默认开启（配置键 `searchGrid`，面板"桌面搜索格子"开关）。

### 数据清理记录（用户授权执行）
- `收纳库\文件夹` 中 14 份内容完全一致的 jifang 重复副本（各 77MB，共约 1.08GB）
  经逐份内容校验后放入回收站；主 `jifang`（713MB）保留。回收站可恢复。
- 成因：同一课程资料文件夹（jifang）反复出现在桌面并被反复收录；
  若后续再出现同类情况，需排查向桌面释放该文件夹的程序。

### 新增测试（合计 80）
- 八方向热区判定（含锁定）、`_resize_geometry` 全edge/钳制/吸附与指示线。
- SearchWidget 基本行为（提示/结果/计数）、搜索格子集成（拼音命中/删除/恢复）。

## 19. v2.6.1 Bug 修复与健壮性（2026-10-14）

### 修复
- **收纳目录守卫**：`_save_root` 绑定在 `editingFinished`（回车或失焦都触发，
  实测 Qt 6.12 文本未改动也触发）。用户每次焦点经过输入框都会全量重建格子。
  现在先做 `normcase(normpath(...))` 对比，值未变化直接 return，不写配置不重建。
- **配置/历史原子写入**（config.py）：新增 `_atomic_write(path, data)`——
  先 `json.dumps` 序列化，再写 `.tmp` 临时文件，最后 `os.replace` 原子替换；
  失败路径会清理 tmp。彻底消除"崩溃瞬间损坏 JSON → 静默丢位置/丢撤销历史"。
- **拖入批量落盘**（main.py `handle_drop`）：原来循环内每个文件调一次
  `append_records`（每文件 load+save 全量历史，2000 条记录），现在整批
  收集为 `records` 后单次写入。拖入返回值改为**失败路径列表**（供粘贴用）。
- **粘贴失败保留**（`handle_paste_files`）：先整体换出剪切板 → 执行移动 →
  失败且源仍存在的路径回填剪切板并提示"可换个格子或稍后重试"。
  在同格"拖回被静默忽略"的条目仍不从剪切板保留（语义为用户误操作）。

### 行为注意
- `handle_drop` 现返回 `List[str]`（失败路径），原调用方（dropEvent、粘贴）
  忽略返回值，不影响现有逻辑。

### 新增测试（合计 85）
- `test_save_config_atomic_keeps_old_on_write_error` /
  `test_save_history_atomic`（写中断→旧文件完整、无 .tmp 残留）。
- `test_handle_drop_writes_history_once`（3 文件一次落盘）。
- `test_paste_failure_keeps_items_in_clipboard`（失败项回填剪切板）。
- `test_save_root_ignores_no_change`（未改不重建；真正改动才写配置+重建）。
