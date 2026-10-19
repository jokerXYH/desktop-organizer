# -*- coding: utf-8 -*-
"""配置与移动历史的 JSON 持久化。

配置文件与历史文件均保存在 %APPDATA%/MyDeskOrganizer 下，
读写失败时静默退回默认值，保证程序总能启动。
"""
import copy
import json
import os
import sys
from typing import Any, Dict, List

# 应用版本号（语义化版本：主版本.次版本.修订号；与 README / CHANGELOG 同步更新）
APP_VERSION = "2.11.0"

# 配置目录与文件路径常量
CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "MyDeskOrganizer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
HISTORY_PATH = os.path.join(CONFIG_DIR, "history.json")

# 历史记录最大保留条数，防止文件无限膨胀
HISTORY_MAX_RECORDS = 2000

# 文件夹分类名（与 organizer.FOLDER_CATEGORY 一致，避免 GUI 模块依赖）
FOLDER_CATEGORY = "文件夹"


def app_dir() -> str:
    """程序所在目录：打包成 exe 后为 exe 所在目录，否则为源码根目录。

    为什么不能直接用 __file__：PyInstaller onefile 模式下 __file__ 指向临时
    解包目录（退出即删）。若拿它当默认收纳库根目录，用户整理的资料会连目录
    一起消失；同理自启项也会指向一个不存在的脚本。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# 默认收纳根目录：程序目录下的"收纳库"（可跨机器使用；面板中可随时修改）
_DEFAULT_STORAGE_ROOT = os.path.join(app_dir(), "收纳库")

# 默认配置
DEFAULT_CONFIG: Dict[str, Any] = {
    "storageRoot": _DEFAULT_STORAGE_ROOT,          # 收纳根目录
    "includeShortcuts": False,                     # 一键整理是否收走快捷方式
    "includeFolders": True,                        # 一键整理是否收走桌面文件夹
    "autostart": False,                            # 开机自启（注册表 Run 键）
    "shellMenu": False,                            # 桌面右键菜单记录值；实际以注册表为准
    "previewIcons": True,                          # 格子预览：True=图标视图 False=紧凑列表
    "gameMode": False,                             # 游戏模式：隐藏格子并暂停监听
    "widgetPositions": {},                         # 格子位置记忆：{分类名: "x,y"}
    "widgetLayouts": {},                           # 显示器感知布局：{分类键: {x,y,w,h,screen,screenRect}}
    "widgetSizes": {},                             # 格子窗口尺寸：{分类名: "宽,高"}（拖拽调整）
    "widgetScales": {},                            # 图标/列宽缩放：{分类名: 缩放比}
    "widgetPreviews": {},                          # 单格子预览方式：{分类名: True=图标视图}
    "widgetCollapsed": {},                         # 收起状态：{分类键: True=已收起}
    "gridPaths": {},                               # 路径映射格子：{分类名: 映射的文件夹}
    "gridTitles": {},                              # 显示名记忆：{分类键: 显示名}
                                                   # 内置分类改名只改这里，分类键不变
    "autoOrganize": False,                         # 自动整理新出现的桌面文件
    "autoOrganizeDelay": 300,                      # 自动整理前的静置等待（秒）
    "searchGrid": True,                            # 桌面搜索格子（常驻，可删除）
    "updateUrl": "https://github.com/jokerXYH/desktop-organizer/"
                 "releases/latest/download/update.json",  # 新版本清单地址（留空=不检查）
    "confirmOrganize": True,                       # 一键整理前先预览确认
    "trayTipShown": False,                         # 托盘一次性提示是否已展示（提示退出入口）
    "panelGeometry": "",                           # 控制面板位置记忆："x,y,w,h"
    "categories": ["文档", "图片", "视频", "音频", "压缩包", "安装包",
                   "代码", "文件夹", "其他"],
}

# 用于识别 v1 旧配置的键：旧配置文件里没有它，加载时补"文件夹"分类
V2_MARKER_KEY = "widgetPositions"


def load_config() -> Dict[str, Any]:
    """读取配置；文件不存在或损坏时返回默认配置的拷贝。

    兼容 v1 旧配置：文件中没有 V2_MARKER_KEY 时视为旧版，
    自动在 categories 末尾补上"文件夹"分类。
    """
    merged = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged.update(data)
            if V2_MARKER_KEY not in data and isinstance(merged["categories"], list):
                merged["categories"] = ensure_folder_category(merged["categories"])
    except (OSError, ValueError):
        pass
    return merged


def ensure_folder_category(categories: list) -> list:
    """确保分类列表包含"文件夹"分类（追加到末尾），返回新列表。"""
    cats = list(categories)
    if FOLDER_CATEGORY not in cats:
        cats.append(FOLDER_CATEGORY)
    return cats


def format_position(x: int, y: int) -> str:
    """把窗口坐标序列化为 "x,y" 字符串，便于 JSON 存储。"""
    return f"{int(x)},{int(y)}"


def parse_position(text: str) -> tuple:
    """把 "x,y" 字符串解析为 (x, y)；格式非法时返回 None。"""
    try:
        x_str, y_str = str(text).split(",")
        return int(x_str), int(y_str)
    except (ValueError, AttributeError):
        return None


def validate_grid_name(name: str, existing_names) -> str:
    """校验新建/重命名格子的名称；合法返回空字符串，否则返回错误提示。"""
    name = str(name).strip()
    if not name:
        return "名称不能为空"
    for ch in '\\/:*?"<>|':
        if ch in name:
            return '名称不能包含 \\ / : * ? " < > | 字符'
    if name.endswith("."):
        return "名称不能以句点结尾"
    if name in list(existing_names):
        return f"已存在同名格子「{name}」"
    return ""


def format_geometry(x: int, y: int, width: int, height: int) -> str:
    """把窗口几何序列化为 "x,y,w,h" 字符串。"""
    return f"{int(x)},{int(y)},{int(width)},{int(height)}"


def parse_geometry(text: str) -> tuple:
    """把 "x,y,w,h" 解析为 (x, y, w, h)；格式非法时返回 None。"""
    try:
        parts = [int(part) for part in str(text).split(",")]
    except (ValueError, AttributeError):
        return None
    return tuple(parts) if len(parts) == 4 else None


def retarget_history_prefix(old_prefix: str, new_prefix: str) -> int:
    """把历史记录中位于 old_prefix 下的 dst 路径改到 new_prefix 下。

    重命名格子（分类文件夹）后调用，保证"撤销/恢复"仍然能找到文件。返回更新条数。
    """
    old_pre = os.path.abspath(old_prefix) + os.sep
    new_pre = os.path.abspath(new_prefix) + os.sep
    history = load_history()
    changed = 0
    for record in history:
        dst = record.get("dst", "")
        if dst and os.path.abspath(dst).startswith(old_pre):
            record["dst"] = new_pre + os.path.abspath(dst)[len(old_pre):]
            changed += 1
    if changed:
        save_history(history)
    return changed


def _atomic_write(path: str, data: str) -> None:
    """原子写文件：先写临时文件再 os.replace 替换。

    直接 truncate 原文件再写的写法在崩溃/断电瞬间会产生损坏的半截 JSON，
    导致下次启动静默丢失格子位置与撤销历史。os.replace 在同分区上是原子的，
    任何时刻磁盘上的正式文件要么是完整旧版、要么是完整新版。
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):          # 写入/替换失败残留的临时文件
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_config(config: Dict[str, Any]) -> None:
    """保存配置到磁盘，目录不存在则自动创建（原子写入，防损坏）。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = json.dumps(config, ensure_ascii=False, indent=2)
    _atomic_write(CONFIG_PATH, data)


def load_history() -> List[dict]:
    """读取全部移动历史记录；文件损坏时返回空列表。"""
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(records: List[dict]) -> None:
    """整体覆盖保存历史记录（原子写入，防损坏）。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = json.dumps(records, ensure_ascii=False, indent=2)
    _atomic_write(HISTORY_PATH, data)


def append_records(new_records: List[dict]) -> None:
    """把新移动记录追加进历史文件，超出上限时丢弃最旧的记录。"""
    history = load_history()
    history.extend(new_records)
    save_history(history[-HISTORY_MAX_RECORDS:])


def pop_last_batch() -> List[dict]:
    """取出最近一次批次（batch）的全部记录并从历史中删除，用于"撤销上次"。

    兼容旧数据：最后一条记录没有 batch 时只弹出这一条，保证"撤销上次"始终可用。
    """
    history = load_history()
    if not history:
        return []
    last_batch = history[-1].get("batch", "")
    if last_batch:
        batch_records = [r for r in history if r.get("batch") == last_batch]
        remaining = [r for r in history if r.get("batch") != last_batch]
    else:
        batch_records = [history[-1]]
        remaining = history[:-1]
    save_history(remaining)
    return batch_records
