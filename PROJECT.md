# 桌面分类助手（v2.6）— AI 交接文档

> 本文档写给后续接手的 AI / 开发者：v2 源码已按本文档重建完成并经过 v2.1 迭代
> （见第 13 节），后续以代码 + 本文档为行为基准。收纳库数据、配置文件、注册表自启项均正常。

## 1. 项目是什么

Windows 桌面文件整理工具（Python + PySide6，独立自研）：
当前版本 v2.10.0，版本历史见仓库根的 CHANGELOG.md。
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

## 3. 模块与职责（共 8 个源文件）

```
项目根目录\
├── main.py            程序入口：AppContext / 托盘 / F7-F9 热键 / 文件监听 / 位置与自启编排
├── config.py          配置与历史 JSON 持久化 + app_dir()（纯逻辑，无 GUI 依赖）
├── organizer.py       分类/整理/撤销/拖入移动/收纳库搜索（纯逻辑，无 GUI 依赖）
├── shortcut.py        桌面/开始菜单快捷方式生成（纯逻辑，无 GUI 依赖）
├── widgets.py         FileWidget 桌面格子窗口（图标、预览切换、位置记忆、暗色绘制）
├── control_panel.py   ControlPanel 控制面板（按钮、开关、搜索、暗色 QSS）
├── autostart.py       HKCU Run 键读写（纯 winreg，便于测试）
├── search_service.py  收纳库搜索后台线程服务（面板与搜索格子共享）
├── assets\            app_icon.ico（快捷方式与打包 exe 共用）
├── tools\             make_readme_screenshots.py / make_app_icon.py / build_exe.py
├── requirements.txt   PySide6 + pytest
└── tests\             conftest.py + 8 个测试文件，共 139 用例（含 GUI/集成冒烟）
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
COMPACT_ICON_PX=24          # 紧凑列表左侧图标/缩略图边长
THUMB_EXTS / THUMB_MAX_SRC_BYTES=40MB / THUMB_CACHE_MAX=500  # 缩略图准入与缓存上限
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

- **图标**：全局单例 `get_icon_provider()`（QFileIconProvider）；紧凑模式不取系统图标。**图片条目优先显示内容缩略图**：`ThumbnailService` 单例把解码放到后台线程池，按 `(路径, mtime, 边长)` 缓存，完成后经 `ready` 信号回填条目；非图片/超 40MB 的图走系统图标兜底。列表按"文件夹在前、文件在后"排序（均 `key=str.lower`）。
- **预览切换 `set_preview(icons)`**：
  - 图标视图：`IconMode` + IconSize 38 + GridSize 76x84 + WordWrap + `Adjust` + `LayoutMode Batched`(batchSize 50)
  - 紧凑列表：`ListMode` + `setGridSize(QSize())` 复位 + IconSize 24（左侧小缩略图）
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

## 10. 测试基线（143 用例，必须全绿）

```bash
python -m pytest tests/ -q    # 期望 143 passed
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

## 20. v2.7.0 性能与界面优化（2026-10-14）

### 后台搜索（新模块 search_service.py）
- `SearchService(QObject)`：`start(keyword, limit)` 提交后台任务（QThreadPool，
  maxThreadCount=1 串行），`results_ready(int seq, str keyword, list)` 信号回主线程；
  `_root_provider/_dirs_provider` 用无参函数现取，改配置即生效。
- 接收方守卫：**序号+关键词双校验**——只比序号会在"双接收方共享服务"时误判；
  面板 `ControlPanel._on_search_results` 与 `SearchWidget._on_results` 各自维护
  自己最近一次提交的 seq；`_search_pending` 标志防止清空后的老请求回填。
- `run_now()` 前台同步入口供测试与降级；service 缺省可缺席（None 分支保持旧行为）。
- AppContext.__init__ 中 **search_service 必须先于 ControlPanel 创建**
  （面板构造即连接其信号）。
- 实测端到端链路（offscreen 真实事件循环）：提交→线程扫描→信号排队→回填 OK。

### 事件定向刷新
- `_on_storage_changed(path)` 把 normcase 目录收入 `_pending_dirs`；
  `_refresh_timer.timeout → _refresh_pending()`：日志按"目录→格子"映射
  只刷新对应 widget；搜索格子只要有任何变化就整体重跑（现有关键词）。
- `refresh_all()` 保留给全量场景：重新显示、退出游戏模式、整理/撤销回调。
- 已知边界：root 目录事件不会直接命中任何格子目录（分类子目录才映射），
  由"搜索格子统一重跑"兜底。

### 界面
- 面板高度 `min(760, 屏高-80)`；`_restore_geometry` 对记忆宽高同样钳制。
- APP_QSS 增补 QDialog/QMessageBox/QInputDialog 规则（按钮/输入框暗色）。
- 搜索结果：紧凑模式行尾"（分类）"标签；图标模式仍只显示名称。
- 空格子 2 条引导（主提示 + "右键空白处：新建/映射/锁定格子"）。
- `RESIZE_EDGE` 6 → 8（拖拽热区）；拖入悬浮时背景叠加 rgba(76,194,255,26)。
- 托盘新增"自动整理新桌面文件"勾选项；`ctx.act_auto_organize` 同步面板勾选。

### 其它
- 图标缓存超限改为批量淘汰最早一半（FIFO）。
- BUILTIN_GRID_NAMES 统一从 widgets 导入。
- test_app_smoke 的 ctx 夹具 teardown 增加 `pool.clear() + waitForDone(1000)`，
  消除后台线程偶发向已销毁控件发信号的崩溃。

### 启动注意
- PySide6 现为 6.12.0（重建 v2.6 时记录的是 6.10.3，升级不影响行为）。

### 新增测试（合计 94）

## 21. v2.7.1 自动整理时序修复（2026-10-14，实测复现修复）

### 现象（用户实测 v2.7.0）
- 收纳库\文件夹 出现 fish.jokerx.top / jifang / markdown 各 7 份、
  安装包 YIQI K3.exe 3 份、文档某答案 PDF 8 份——同一释放源反复释放，
  自动整理反复收集。
- 部分 PDF 留在桌面未被收纳（"遗漏分类"）。

### 根因
1. `_run_auto_organize` 静置到期**立即 shutil.move**：撞上还在写入的条目，
   搬走后释放方按路径重建 → 同名条目反复再现 → 反复收集成副本。
2. `organize_desktop` 对移动失败（被占用）静默 continue，`_run_auto_organize`
   随后把该名称写进 `_auto_seen` 基线 → **永远不再重试**（遗漏的来源）。
3. 浏览器下载临时名（.crdownload）被当"其他"收走，改名后产生第二份。

### 修复后的状态机（AppContext）
- `_auto_stable: {名称: 快照}`——待确认条目；快照=文件(大小, mtime) /
  文件夹(子条目数, mtime)。`_auto_rounds` 连续观察轮数（上限 45 轮放弃）。
- `_run_auto_organize`（静置到期）：
  过滤 IN_PROGRESS 临时名 → 同名重复保护（`_auto_collected` 15 分钟窗口，
  命中则跳过+提示+入基线）→ 剩余条目快照进 `_auto_stable` → 启动
  `_auto_confirm_timer(2000ms)`。基线=当前桌面\待确认\临时名。
- `_confirm_and_collect`（确认/重试到期，共用回调）：
  逐条比对快照——变化者刷新快照继续观察；消失者移出；稳定者调
  `organize_desktop(only_names, skip_in_progress=True, return_failed=True)`。
  成功 → 记 `_auto_collected` 时间戳（供同名保护）；失败 → `_auto_attempts`
  计数 +1，重试定时器 30s，达到 `AUTO_MAX_ATTEMPTS=5` 放弃（入基线+提示）。
- 游戏模式：进入时停 `_auto_confirm_timer/_auto_retry_timer`（待确认保留）；
  退出时如有待确认则重启确认。`_sync_auto_watcher` 重建基线时
  `-= set(self._auto_stable)`，不会吞掉待确认条目。

### organizer.py 接口变化
- 新增 `IN_PROGRESS_SUFFIXES/PREFIXES` 与 `is_in_progress_name()`。
- `_iter_candidates(..., skip_in_progress=False)`；`organize_desktop(...,
  skip_in_progress=False, return_failed=False)`——return_failed=True 返回
  `(records, failed_names)`；默认 False 完全兼容旧调用与旧测试。

### 维护提醒
- 桌面上有程序在反复释放 jifang / markdown / fish.jokerx.top 等条目
  （实测各 7 份副本），需要定位该释放源（疑似网盘同步/课程资料工具）；
  15 分钟同名保护只能止损，不能替代释放源治理。
- 深层缺口：文件夹内部深层文件的持续写入（顶层条目数与目录 mtime 不变）
  不会被稳定性快照发现，仍依赖同名保护兜底。

### 新增测试（合计 99）
- test_auto_organize_waits_for_stability / skips_in_progress_names /
  locked_file_retries / gives_up_after_max_attempts / duplicate_guard。

补充（同日）：`_load_auto_collected()` 从撤销历史恢复同名保护名单
（窗口内有该名字移动记录 → 再现时跳过），`set_auto_organize(True)` 与
`_sync_auto_watcher` 重挂监听时调用——应用重启后保护不丢。
新增 test_auto_organize_guard_restored_from_history（合计 100）。

## 22. v2.7.2 健壮性修复与界面视觉精修（2026-10-14）

### 修复的缺陷（含第 21 节遗留的"深层缺口"）
1. **深层缺口已关闭**（原第 21 节列为"仍依赖同名保护兜底"）：文件夹稳定性
   快照改为**递归**条目数 + `mtime_ns`。此前只数顶层条目、且 `int(st_mtime)`
   秒级截断，往已存在的子目录里落文件时快照不变 → 半拷贝文件夹被整体搬走 →
   释放方重建副本。递归计数有上限 `AUTO_DIR_SCAN_CAP=500`，超大目录提前结束。
2. **同名保护名单改用 `src` 取名**：`dst` 因冲突改名（"名 (2)"）会与桌面
   真实名字错位。涉及 `_load_auto_collected`。
3. **`organize_desktop` 新增 `skipped` 返回值**（`return_failed=True` 时返回
   三元组 `(records, failed, skipped)`）：开关关闭而有意留下的条目不再被
   自动整理误记为"已收纳"；自动整理对 skipped 撤出待确认但不记入
   `_auto_collected`、不入基线（`_confirm_and_collect` 与末尾基线两处）。
   调用方 `control_panel._do_organize` 同步改为三元组解包。
4. **路径映射并发安全**：`AppContext._grid_lock` + `_snapshot_grid_dirs()`；
   搜索服务 `dirs_provider` 改为该方法。`create_grid/rename_grid/remove_grid`
   的 gridPaths 改动都在锁内。
5. **`control_panel._do_organize` 捕获 `(RuntimeError, OSError)`**。
6. **`organizer.is_within` 比较前 `os.path.normcase`**（Windows 大小写不敏感）。
7. **`main._norm_dir()`** 统一 `_on_storage_changed` 与 `_refresh_pending`
   的路径归一化，修正大小写/尾分隔符不一致导致的漏刷。
8. `AppContext._forget_pending(name)`：抽出"从 `_auto_stable`/`_auto_rounds`
   移除"的重复片段（原 5 处重复 pop 对）。

### 界面视觉精修（玻璃拟态）
统一"深底 + 浅白描边 + 半透明层叠"的玻璃语言，仅改配色/圆角/间距/字体层级
与悬停选中态，**布局结构、控件层级、信号连接、几何计算与业务逻辑零变更**。

| 位置 | 改动要点 |
| --- | --- |
| `widgets.FileWidget.paintEvent` | 分层绘制：竖向渐变基底（上亮下暗）→ 顶部高光渐隐 → 浅白外描边 → 更淡内高光描边（玻璃"厚度"）→ 标题分隔线两端渐隐 |
| `widgets` 外观常量 | `CORNER_RADIUS` 14→16、`BG_ALPHA` 232→208（更透，透出壁纸成毛玻璃）；新增 `COLOR_BG_BOTTOM`/`COLOR_GLOW`/`COLOR_INNER_HIGHLIGHT`/`COLOR_SEPARATOR`/`COLOR_TEXT_DIM`/`COLOR_TEXT_FAINT` |
| `widgets.LIST_QSS` / `COMPACT_LIST_QSS` | 悬停 16、选中强调色 52 加半透明强调描边；滚动条滑块半透明浅白、悬停转主题色；紧凑列表行距再放宽 |
| `widgets.SEARCH_EDIT_QSS`（新增） | 搜索格子输入框原先无样式会退回系统浅色默认外观，现统一为玻璃输入框（含聚焦描边） |
| `widgets` 标题栏 | "打开"改为胶囊形文字按钮 + 悬停浅蓝底；计数标签改用 `COLOR_TEXT_DIM` |
| `control_panel.PANEL_QSS` | 底色 #15181f；按钮/输入框/下拉框/列表改半透明浅底 + 浅白描边 + 9~10px 圆角；主按钮改上下渐变 + 深色文字 |
| `control_panel.GlassCheckBox`（新增） | 自绘指示器：选中=主题蓝圆角块 + 深色对勾，未选中=半透明玻璃方块（悬停提亮）；仅覆盖绘制，`isChecked/toggled` 不变 |
| `main.APP_QSS` | 右键菜单/工具提示/对话框同步玻璃配色（10px 圆角 + 浅白描边 + 强调色选中块） |
| `main.make_tray_icon` | 渐变圆角块 + 内高光描边 |

### 实机验证（真机复核）

方法：真机（显示缩放 2.0，物理 2880×1800）上用 `PrintWindow`（`SetProcessDPIAware`
后）直接抓取"置底"格子的窗口内容——置底窗常被最大化窗口遮挡，`PrintWindow`
可绕开遮挡取证且不打扰其它窗口；面板另用 `WA_DontShowOnScreen` + `grab()`
离屏取证。

发现并修复：

| 问题 | 处理 |
| --- | --- |
| 空格子引导文字被图标网格（单元格仅 76px）挤成 3~4 行竖排，观感破相（既有问题） | 新增 `FileWidget._apply_guide_layout()`：空状态切 `ListMode` + `wordWrap` + 非等高条目，文字占满整行；**不动 `gridSize/iconSize`**，缩放档位记忆与既有断言不受影响 |
| 空↔非空切换、缩放/预览切换时布局可能错乱 | 新增 `_is_empty` 状态位（refresh 时更新）与 `_guide_active`；仅在"由空转非空"时恢复预览布局，避免每次刷新重设样式表 |
| 面板未勾选框（`GlassCheckBox` 未选中态描边/填充 45/14）在深底上几乎不可见 | 对比度提高到 78/24（悬停 110/40） |
| 搜索格子空提示同样被网格挤窄 | `SearchWidget._run_search()` 空提示走引导布局；有结果时恢复预览方式布局 |

实机结论：玻璃效果（圆角、双层描边、壁纸透出、标题栏与渐隐分隔线）成立；
全部窗口矩形在屏内（8 个格子默认 270×360 逻辑、面板底边未越界）；
`panelGeometry` 恢复与越界钳制正常。

### 只读文件导致的副本堆积（真机反馈修复）

真机复现：桌面在 C:、收纳库在 D:，跨盘移动 = 复制 + 删源；Windows 下源为只读
时"删源"抛 `PermissionError` → **目标已生成副本、源仍留在原地**。旧实现只标记
"失败"就继续，副本永远留在收纳库，用户每点一次「一键整理桌面」就多叠一份同名
副本（实测 `jifang` 12 份、只读 exe 9 份，累计约 10.7 GB），且这些文件始终收纳
不进格子。

| 改动 | 要点 |
| --- | --- |
| `organizer.move_entry`（新增） | 统一移动入口，顺序"先正常试、失败才修"：① 直接 `shutil.move`（快路径，不额外扫描/改属性）；② 失败先清掉本次生成的半成品目标（防副本堆积）；③ 源含只读位则解除只读重试一次；④ 成功后把副本还原为只读，重试仍失败则还原源属性并计入失败 |
| `organizer.organize_desktop` / `undo_moves` / `move_into` | 全部改走 `move_entry`，覆盖一键整理、拖入、剪切粘贴与撤销（撤销只读条目同样不再留副本） |
| `organizer._iter_candidates` / 预览 / 整理 | 新增 `enabled_categories`：只收纳"当前显示为格子"的分类，未启用分类（如未勾选「视频」）留在桌面并在状态栏提示，避免"搬进无格子目录后凭空消失" |
| `control_panel._do_organize` / `main._auto_organize` | 传入 `set(config["categories"])`，并把 `skipped` 的结果提示出来（不再静默） |

数据清理（经用户确认）：按"同目录同基名"判定重复，保留每组无编号原件，删除 43 项
编号副本、回收约 **11.1 GB**；同时把「整理时收走文件夹」改为关闭，桌面三个含只读
文件的大文件夹（`jifang` / `markdown` / `fish.jokerx.top`）留在桌面。

### 格子操作体验与同名文件夹合并（真机反馈）

真机反馈三点：① 桌面上与收纳库里各有一个 `jifang`，为什么两份；② 格子里的条目
希望按 Delete 能直接删掉；③ 从格子里拖出去后，格子里应该就没有了。

| 改动 | 要点 |
| --- | --- |
| `widgets.FileListWidget.keyPressEvent`（新增） | Delete 键把选中条目交给 `delete_cb`；格子默认接到右键同款"删除到回收站"入口（仍有确认框，可在资源管理器恢复）。空状态提示条目没有真实路径，不参与删除 |
| `widgets.DRAG_ACTION` / `FileListWidget.startDrag` | 拖出只声明 `MoveAction`。资源管理器对跨盘（D: 收纳库 → C: 桌面）拖放默认按"复制"处理，源文件会留在收纳库里——格子上"拖出来了却没少"、桌面却多一份副本；现在跨盘拖出也真正移走源文件，`drag_finished_cb` 随后刷新列表 |
| `organizer.merge_directory`（新增） / `organize_desktop` | 目标已有同名文件夹时改为逐项合并，不再生成 `jifang (2)`：同名且内容一致（大小一致 + 修改时间差 ≤ 2 秒，沿用 robocopy 口径）视为重复，桌面那份送回收站（回收站不可用则退化为改名保留，绝不静默丢数据）；同名但内容不同用 `unique_path` 改名，两份都留；子目录递归合并；有条目搬不动时保留现场并按"整理失败"上报。合并产生的逐项移动各自记入 MoveRecord，撤销可逐条还原 |

桌面那份残缺 `jifang`（19 个子目录全空、37 个顶层文件在收纳库中均有且尺寸/时间
完全一致、无任何独有文件）已送回收站；收纳库里完整的（1564 个文件）保留。

### 测试
- 由 100 → **127**。新增用例见 CHANGELOG 2.7.2。
- 回归命令：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q`。
- UI 改动后复跑：**109 passed**；并用 `tools/make_readme_screenshots.py`
  重生成 `docs/images/{hero,menu,compact}.png`（该脚本桩上下文补了缺失的
  `search_service` 属性）。
- 实机验证修复（空状态引导布局 + 勾选框对比度）后复跑：**109 passed**。
- 只读文件跨盘移动修复后复跑：**117 passed**（新增 `tests/test_organizer_readonly.py`
  8 例，覆盖只读源重试成功、重试失败不留半成品、重复整理不叠副本、
  未启用分类留在桌面、预览尊重启用分类、撤销/拖入只读源不留副本）。
- 格子 Delete 键 / 拖出即移走 / 同名文件夹合并去重复跑：**127 passed**
  （新增 `tests/test_organizer_merge.py` 6 例与 `test_widgets_smoke.py` 4 例）。

### 未改动
- 业务逻辑、界面**布局与交互**保持不变；`_auto_confirm_timer` 与
  `_auto_retry_timer` 共用 `_confirm_and_collect` 的双定时器结构保留
  （回调幂等，风险低，避免为小概率问题改动核心状态机）。
- 手动整理 / 拖拽 / 撤销 / 路径映射的路径与键名均未变动。
- 未新增任何轮询定时器（性能红线）。

## 23. v2.8.0 快捷启动与 exe 打包（2026-10-14）

用户需求：做一个方便的快捷启动本工具的方式。确认方案为「桌面 + 开始菜单快捷
方式」与「打包成独立 exe」两项。

### 新增文件

| 文件 | 职责 |
| --- | --- |
| `shortcut.py`（新增，纯逻辑层） | `create_launch_shortcuts()` 在桌面与开始菜单各生成一份快捷方式；`launch_target()` 处理双形态启动目标；`build_ps_command()` 构造 PowerShell 脚本 |
| `tools/make_app_icon.py` | 生成 `assets/app_icon.ico`（16/32/48/64/128/256 六个尺寸） |
| `tools/build_exe.py` | PyInstaller 打包（`--onefile --windowed --collect-data pypinyin --exclude-module tkinter --icon`），产物 `dist/桌面分类助手.exe`（实测 45.5 MB） |
| `control_panel.py` | 新增「快捷启动」行 + `_create_shortcuts()`；`main.AppContext.create_shortcuts()` 转发到 shortcut |

实现选择：用系统 PowerShell 的 `WScript.Shell` COM 写 `.lnk`，而不是引入 pywin32
——`requirements.txt` 不变，源码运行与 exe 两种形态都能调用。

为什么自己拼 ICO：Qt 的 ICO 写入在本机会直接把进程打崩（0xC0000409），
而 PyInstaller 的 `--icon` 与快捷方式的 `IconLocation` 都只认 .ico；
于是把若干尺寸的 PNG 按 ICO 容器格式手工打包（Vista+ 支持 PNG 条目）。

### 打包适配（易踩的坑）

| 改动 | 要点 |
| --- | --- |
| `config.app_dir()`（新增） | 打包后返回 **exe 所在目录**。onefile 模式下 `__file__` 是临时解包目录（退出即删），拿它当默认收纳库根目录会让用户资料随目录一起消失 |
| `main.SCRIPT_PATH` | 打包后取空串；`autostart.build_autostart_command(exe, "")` 只写 exe 自身，避免注册表自启项指向开机时已不存在的临时脚本 |
| `shortcut.launch_target()` | 同样双形态：源码运行用 `pythonw.exe "…\main.py"`（无控制台黑窗），打包后直接指向 exe |

### 真机验证（本机执行）

- 桌面与开始菜单两份 `.lnk` 均已生成并回读校验：`TargetPath=pythonw.exe`、
  `WorkingDirectory=D:\小设计`、`IconLocation=…\assets\app_icon.ico,0`；
  桌面那份 `Hotkey=Alt+Ctrl+D`，开始菜单那份为空（组合键只挂第一份——
  两份 .lnk 抢同一个热键时系统只认其中一个）。
- **踩坑**：`CreateShortcut` 读的是**已存在的 .lnk**，不赋值就保留旧值
  （重写开始菜单那份时旧热键一直残留）。修复：图标/热键即使为空也显式赋空值。
- `dist\桌面分类助手.exe` 启动冒烟：进程正常退出（exit 0，检测到已有实例后
  转交"唤起面板"请求），说明 PySide6 等依赖已正确打包。
- 配置沿用 `%APPDATA%\MyDeskOrganizer\settings.json`（`storageRoot=D:\小设计\收纳库`），
  exe 与源码运行共用同一收纳库，不会分裂成两份数据。

### 测试
- 139 passed（新增 `tests/test_shortcut.py` 9 例、`test_config.py` 2 例、
  `test_autostart.py` 1 例）。
- 回归命令：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q`。

## 24. v2.8.1 交互精简与图片缩略图（2026-10-14）

用户需求三项：排查"桌面还有东西没进格子"、精简右键菜单、图片可在格子里预览。

### 排查结论：桌面条目未过滤进格子（仅分析，按用户选择不改公共桌面）

按用户"分析一下别急着动手"，逐条定位如下（用户选择"暂不处理公共桌面"）：

1. **只扫用户桌面**：`get_desktop_path()` 读注册表得 `C:\Users\35954\Desktop`
   （当时仅剩 desktop.ini），**公共桌面** `C:\Users\Public\Desktop` 的 13 个
   系统快捷方式永不纳入扫描 —— 这正是用户"看到桌面上还有东西"的直接原因。
2. **自动整理未开**：`autoOrganize: false`，一键整理之外不会自动收纳。
3. **基线机制**：开启监听/重启/退出游戏模式时把当时桌面全量设入 `_auto_seen`，
   于是"开启前就存在的条目"永远不在自动整理范围（只能靠一键整理）。
4. **分类未勾选**：当时 categories 缺"视频/音频"，其文件按设计留在桌面并提示。
5. 次要：写入中临时名（.crdownload/.part/~$）、隐藏点文件、同名 15 分钟保护。

### 右键菜单精简（去掉「尺寸调整」与「锁定位置」）

- 菜单分区由「条目操作 / 粘贴 / 外观与尺寸 / 格子管理 / 刷新」变为
  「条目操作 / 粘贴 / 外观 / 格子管理 / 刷新」：移除「尺寸调整」子菜单与
  「锁定位置」复选项。
- 连带清理位置锁定的全部实现：`FileWidget` 的 `locked`/`_set_locked`、
  `_show_zoom_menu`、`_reset_scale`、`_reset_window_size`；`main.handle_lock_changed`
  与重命名/删除格子里的 `lockedWidgets` 维护；`config.DEFAULT_CONFIG` 的
  `lockedWidgets` 键。窗口尺寸改由**拖拽边缘**、图标大小改由 **Ctrl+滚轮** 调整。

### 图片内容缩略图（后台解码）

- 新增 `ThumbnailService`（QObject 单例，`ready = Signal(str,int,QImage)`）：
  `get()` 命中 `(路径,mtime,边长)` 缓存即回 QImage，否则提交 `ThumbnailTask`
  （QRunnable）到 `QThreadPool`（2 线程）后台解码，完成后 `_store()` 写缓存并 emit。
- 解码只产出 **QImage**（QPixmap/QIcon 只能在 GUI 线程建）；`QImageReader`
  设 `setScaledSize` + `setAutoTransform`（省解码量、尊重 EXIF 方向）。
- `FileWidget.refresh()` 逐条 `_set_item_icon()`：图片走缩略图（未就绪先放系统
  图标占位，`_on_thumb_ready` 回填），非图片走系统图标；紧凑列表 IconSize 24。

### 关键坑：信号连接方式会导致进程崩溃

- **现象**：`ready.connect(self._on_thumb_ready)` 后，测试进程随机
  `Fatal Python error: Aborted`（栈溢出式 GC 崩溃），崩溃点还会漂移。
- **根因**：PySide6 连接绑定方法时，全局服务**强引用**每个格子，窗口
  `close()` 后仍无法释放，垃圾回收期触发 Qt 崩溃。
- **修复**：改为经 `weakref` 转发（`_dispatch_thumb`），并在 `closeEvent`
  断开连接 —— 既不钉住窗口，也不让服务无限累积失效回调。

### 拖拽缩放修复与"去霓虹"视觉收敛

- **拖边缘吸附只改被拖边**：旧实现拖下缘时把 `top = edge_y - height`、拖右缘时把
  `left = edge_x - width`，于是拖下边会带动上边（用户反馈"我拉下面为什么上面也动"）。
  现改为拖下缘只改 `height`、拖右缘只改 `width`，对边钉住，吸附后再做最小尺寸钳制。
- **保留玻璃、去掉霓虹**：`COLOR_ACCENT` 由 `#4cc2ff` 改为钢灰 `#93a1b3`；背景由
  偏蓝 `(30,34,44)` 改中性炭灰 `(31,31,35)`；顶部高光 30→16；圆角 16→12；选中态、
  对齐线、菜单、面板强调按钮全部去蓝。

### 测试
- 143 passed（缩略图准入/解码边界/缓存命中 3 例；缩略图+菜单断言更新；拖边缘吸附
  回归 1 例，验证下缘/右缘吸附时对边保持不动）。
- 回归命令：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q`。

## 25. v2.9.0 条目重命名 / 内置分类改名 / 搜索条扁平化（2026-10-14）

用户需求两项：**重命名功能必须实现**（文件条目 + 格子名两者都要）、搜索格子改为
**扁扁的只有搜索框大小**的形态。经 AskUserQuestion 确认：搜索格子"改成扁平搜索条"。

### 关键设计：分类键（grid_key）与显示名（title）分离

内置分类的**分类键必须保持稳定**——`classify()` 按扩展名只会返回
`文档/图片/…/其他` 这些固定名，自动整理据此定位目标文件夹；`enabled_categories`
（`set(config["categories"])`）也按下拉固定键匹配。若把「文档」的键改成别的名字，
自动整理会找不到格子而**停止收纳文档**，控制面板还会出现重复勾选框。

因此内置分类改名**只改显示名**，键 / 文件夹 / 记忆 / 历史全部不动：

- 新增配置 `gridTitles = {分类键: 显示名}`（`config.DEFAULT_CONFIG`）。
- `FileWidget` 新增 `grid_key` 参数：`title` 是用户可见的显示名（可改名），
  `grid_key` 是配置键（位置 / 尺寸 / 缩放 / 预览记忆与删除操作用它）。
  `main.rebuild_widgets` 构造格子时 `display = titles.get(name, name)`，
  并传 `grid_key=name`；所有持久化读写（`handle_position_changed` /
  `save_all_positions` / `handle_zoom_changed` / `handle_size_changed` /
  `handle_preview_changed`）统一改用 `grid_key`。
- `main.rename_grid`：`old_name in BUILTIN_GRID_NAMES` → 只 `titles[old_name] = new_name`，
  其余分支（自定义 / 映射格子）沿用原逻辑（改键、改文件夹、迁移记忆与历史）。
- 重名判断改按**显示名**比较（键可能与显示名不同）；`SEARCH_GRID_TITLE`（"搜索"）
  是保留名，create/rename 的校验名单均追加它，避免占用后误命中搜索条判断。

### 条目重命名（文件 / 文件夹）

- 纯逻辑层 `organizer.rename_entry` / `validate_entry_name`（不依赖 PySide6）：
  校验非法字符（与资源管理器一致 `\ / : * ? " < > |`）、空名、句点结尾、
  `.`/`..` 保留名；目标已存在**绝不覆盖**（抛 `ValueError`）；源缺失抛
  `FileNotFoundError`；被占用 / 无权限抛 `OSError`。改名只动目录项，**只读文件
  也能成功**（不写文件内容）。
- UI 层：`FileListWidget` 右键菜单新增「重命名（F2）」（多项置灰），
  `keyPressEvent` 拦截 F2；`FileWidget._rename_paths` 走 `QInputDialog` 取名，
  成功后 `refresh()` 重建列表。
- **校验顺序**：一开始先查源文件存在再校验名称，导致"非法名称 + 不存在的源"
  抛 `FileNotFoundError` 而非 `ValueError`；改为**先校验用户输入**（边界优先），
  并把 `.`/`..` 判在句点规则之前（否则 `..` 被误判为"以句点结尾"）。

### 搜索格子 → 扁平搜索条（SearchWidget）

- **静态形态**：`300×46`，只有"一个搜索框"的高度；隐藏标题栏 / 计数 / 打开按钮，
  输入框插入布局中；不画标题分隔线（`_bar_mode`），不允许拖边缘改尺寸
  （`_resizable=False`）。
- **动态展开**：`_apply_bar_height()` 按结果行数算高度——无结果收起只剩输入框，
  有结果向下展开（最多 `SEARCH_RESULT_MAX_ROWS=9` 行，再多滚动）；结果回来前
  先收起。`_run_search` 空关键词直接 `_set_result_rows(0)`。
- **固定单列**：`set_preview` 重写为恒 `super().set_preview(False)`，
  结果一行一条、行尾带分类标签 `名称（分类）`，行高可预测便于算展开高度；
  「预览方式」对搜索条无效（菜单里也不再出现该项）。
- 只保留**拖动与位置记忆**（键名固定为 `SEARCH_GRID_TITLE`）。

### 截图工具修复：幽灵方块

`tools/make_readme_screenshots.py` 的 `draw_widget` 用 `grab()` 位图的
**设备像素宽高**画阴影矩形，而 `drawPixmap` 按**逻辑尺寸**绘制；位图带
`devicePixelRatio=2`，于是每个格子都拖出一个 2 倍大的黑色"幽灵盒子"。
改为 `width/ratio`、`height/ratio` 换算回逻辑尺寸后，截图恢复干净。

### 测试
- 154 passed（较 v2.8.1 的 143 增 11 例）：`rename_entry` 文件/文件夹改名、
  目标已存在不覆盖、源缺失、6 组非法名称 + 1 组合法名称、只读文件可改名；
  内置分类改名只改显示名（`categories`/文件夹不变、`grid_key` 稳定）；
  搜索条空关键词收起（`list_widget.isHidden()`）、有结果变高、固定单列；
  右键菜单含「重命名（F2）」、内置分类「重命名格子」由禁用改为可用。
- 回归命令：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q`。

## 26. v2.9.1 外观对齐：整齐排列 / 统一间距 / 收起格子（2026-10-14）

真机反馈：桌面格子"不对齐"很强迫症。对真机 settings.json 复核：列内左右错位
15px、列间距 7/20/2px、行间距 13/1/-2/-2px（后两组几乎贴住甚至重叠）、
右缘溢出屏幕 15px（文件夹右缘 1935 > 屏宽 1920）。对标互联网同类产品后落实：

### 对标结论（调研）
- Stardock Fences（Windows 付费标杆）：主打交互是**双击标题栏 roll-up 收起**；
  栅栏自动归整 + 规则自动分类。
- 小智桌面：一键整理主打**「栅格式规整布局」**；分区可**收起为小图标**、可合并拆分。
- 腾讯桌面整理 / iTop Easy Desktop：收起格子、多页面、个性化盒子；华为/荣耀桌面
  「自动对齐（删除后自动补齐空位）」。
- 共性：**规整栅格 + 一键对齐 + 收起** 是这类产品的三大基础体验，全部零新依赖可实现。

### 实现（零新依赖）
- `widgets.plan_tidy_layout(rects, screen, gap, margin, fixed)`（纯函数）：
  x 区间重叠归列 → 列内宽度 / 行内高度容差内"只生长"统一（`WIDTH_GROW_CAP=32`、
  `HEIGHT_GROW_CAP=48`，超差保持原样）→ 行顶对齐 → 间距统一 `WIDGET_GAP=16` →
  整块越界归位（先保右/下缘、再保左/上缘）。**幂等**：整理结果再整理不变。
- `widgets.plan_default_placement(rects, size, screen)`（纯函数）：新格子落位——
  ① 贴右侧列下缘 → ② 整块左侧开新列（顶部对齐）→ ③ 贴左缘扫描空位。
  `AppContext.rebuild_widgets` 重写：无记忆位置的格子收集为 pending 统一排队
  （修复旧的"y 累计忽略已恢复格子实际位置"的错位源头）。
- `AppContext.tidy_layout()`：按屏分组整理可见格子、批量写回位置/尺寸
  （收起态跳过尺寸）；入口 = 托盘「整齐排列格子」+ 右键「整齐排列全部格子」。
- 统一间距常量：`WIDGET_GAP=16`、`SCREEN_MARGIN=16`（水平/垂直/贴边同值）；
  默认布局弃用旧的 300/40/20 多套参数。
- 收起：`FileWidget.set_collapsed()`（`COLLAPSED_HEIGHT=44`），双击标题栏或右键切换；
  展开还原收起前高度；跨重启保留（配置键 `widgetCollapsed`）；收起态禁拖边缘、
  不写尺寸记忆、省略标题分隔线；标题区右键也能唤出菜单。

### 坑与验证
- Qt 布局最小高度约束是惰性的：`list_widget.hide()` 后必须 `layout().activate()`
  再 resize，否则被旧最小高度（实测 102px）顶住收不下去。
- 列表区右键事件会传播到格子窗口：窗口级 `contextMenuEvent` 只响应标题区/收起态，
  无"双菜单"（事件探针亲测）。
- 真机数据只读仿真：`plan_tidy_layout` 将现有凌乱布局归一为
  列 x=607/953/1272/1592、行 y=18/466、右缘 1904（=1920-16），
  15px 溢出与全部间距错位修复（逐格数值见测试断言）。
- README 截图已重新生成（右键菜单含新条目）。

### 测试
- 154 → **172**：`tests/test_layout.py`（新增 10 例，纯函数）+ widgets/app 冒烟 8 例。
- 回归：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q` → **172 passed**。

## 27. v2.9.2 桌面右键「一键整理」与关于页（2026-10-14）

用户需求：把一键整理做到桌面右键菜单；控制面板补充「详情介绍 / 更新 / 免责声明」。

### 桌面右键菜单（`shell_menu.py` 新增，HKCU 注册表）
- 位置：`HKCU\Software\Classes\DesktopBackground\Shell\MyDeskOrganizer`
  （MUIVerb + Icon + command 子键）——不需要管理员权限，不是 COM shell 扩展，
  删除即卸载，行为透明。
- 命令：与自启同一套规则（复用 `autostart.build_autostart_command` 再追加
  ` --organize`）：源码 = `"pythonw.exe" "main.py" --organize`；
  打包 = `"exe" --organize`。
- 流程：点击后 `main()` 用 `cli_action` 解析出 organize → 单实例通道发送
  `b"organize"` → 常驻实例 `_on_new_connection` 读取消息 → `ctx.organize_now()`
  （show_panel + 面板同一 `_do_organize`：尊重预览确认、写历史、可撤销）。
  未运行时正常启动程序，启动完成后 `QTimer.singleShot(120, ctx.organize_now)`。
- 自愈：`refresh_if_stale` —— 注册的命令指向的文件不存在（目录搬迁/换形态）时，
  下次启动自动改写为当前路径；命令仍有效则不动。
- 系统适配：Win10 直接进桌面右键一级菜单；Win11 位于「显示更多选项」二级
  （`platform_menu_hint()` 给出说明，用作面板开关 tooltip）；非 Windows 提示不可用。
- 面板开关「桌面右键菜单（一键整理）」：勾选=register、取消=unregister，
  OSError 回滚 + 弹窗（同自启开关模式）；实际状态以注册表为准。

### 应用内入口
- 托盘菜单「一键整理桌面」；格子/搜索条右键「一键整理桌面」；
  与面板按钮共用 `AppContext.organize_now()`。

### 关于页（控制面板）
- `AboutDialog` + `build_about_html` / `latest_changelog_sections`（纯函数可测）：
  详情介绍（功能/快捷键/收纳库与配置位置/隐私）、更新日志（程序目录
  CHANGELOG.md 最近 6 个版本，缺失时降级）、免责声明（撤销/回收站、备份提醒、
  按"原样"提供、隐私）；附「打开收纳库 / 打开配置目录」按钮；无任何网络请求。
  `APP_QSS` 增补 QTextBrowser 暗色规则。

### 测试
- 172 → **188**：`test_shell_menu.py` 8 例、`test_about.py` 5 例、app 冒烟 3 例；
  app 夹具 patch `shell_menu.is_registered`（所有测试不碰真实注册表）。
- 回归：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q` → **188 passed**。
- 真机：注册菜单项（reg query 复核）→ 重启程序（加载新代码）→ 桌面右键可见
  「一键整理桌面（桌面分类助手）」。

## 28. v2.10.0 横向控制面板与一键更新（2026-10-14）

用户需求：做真正的「更新」——发布新版本后点击即可升级；控制面板改横向布局。

### 横向三栏改版（control_panel.py）
- PANEL_WIDTH/HEIGHT 480×760 → **1000×620**；__init__ 重排为三栏：
  左（整理桌面：一键整理/撤销/恢复/收纳目录/快捷启动）、
  中（显示的格子（分类）+ 新建/映射 + 功能开关网格）、
  右（搜索收纳库 + 版本与更新 + 关于）。
- 所有控件属性名与信号连接保持原样（既有测试零改动通过）。
- `_restore_geometry`：宽度 < 800 的旧几何（竖版）直接忽略、居中显示新默认；
  新几何恢复按屏钳制（宽 ≥600 / 高 ≥420 保底）。
- 截图工具：hero 布局重排 + `draw_widget(..., scale=)` 支持等比可视化大控件。

### 一键更新（updater.py 新增）
- 更新源 = 返回新版本清单 JSON 的地址（配置键 `updateUrl`，面板可编辑）：
  `{version, url, sha256?, page?, notes?}`（version/url 必填，其余可选）。
- 面板「版本与更新」区：当前版本 + 检查更新按钮 + 更新源输入框。
  流程：检查（后台线程拉清单）→ 已最新提示 / 有新版弹确认（含更新说明）→
  下载（进度回填状态栏、可选 SHA-256 校验）→ 自动更新。
- exe 自更新：`apply_update` = 运行中的 exe 改名 `*.old` → `copyfile` 新文件到
  原名（失败自动还原）→ `Popen([exe, "--updated-from", pid])` → 旧进程 quit。
  新实例启动最前等待旧 PID 退出（`wait_for_process_exit`：WaitForSingleObject，
  无轮询）并清理 `*.old`——无需外部 bat/脚本，也避开 bat 中文路径编码问题。
- 源码形态：检测到新版本仅 `webbrowser.open(page)` 打开下载页，不动源码。
- 隐私边界：平时不联网；唯一联网行为 = 用户点击检查更新时访问自填更新源
  （关于页两处文案已同步说明）。

### 测试
- 188 → **203**：`test_updater.py` 9 例（版本比较/清单校验/自替换成功与失败还原/
  进程等待/file:// 离线下载与进度）、app 冒烟 5 例（横向布局/旧几何忽略/更新区/
  未配置不发请求/更新源保存）、`--updated-from` 解析 1 例。
- 回归：`$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/ -q` → **203 passed**。
- 截图已重新生成（hero 展示横向面板 + 版本与更新区）。

### 发布规范（每版照做，v2.10.0 起）
1. 升版本：`config.APP_VERSION` 与 CHANGELOG / README / PROJECT 同步；
2. 打包：`python tools/build_exe.py` → `dist/桌面分类助手_v<版本>.exe`（文件名自带版本号）；
3. 提交推送后，在 GitHub 发 Release（tag `v<版本>`），把该 exe 作为附件上传；
4. 更新仓库根 `update.json`（version / url / sha256 / notes）——应用内「检查更新」
   默认指向 `raw.githubusercontent.com/jokerXYH/desktop-organizer/main/update.json`；
5. 冒烟：旧版 exe 打开 → 检查更新 → 确认后应自动下载、替换并重启（源码形态只打开下载页）。
