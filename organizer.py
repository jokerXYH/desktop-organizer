# -*- coding: utf-8 -*-
"""核心文件整理逻辑：分类规则、桌面扫描、移动与撤销、收纳库搜索。

本模块为纯逻辑层，不依赖任何 GUI 框架，便于单元测试与后续复用。
v2：新增文件夹支持——桌面顶层文件夹作为一个整体移入"文件夹"分类；
新增 search_storage 供控制面板搜索收纳库。
"""
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Tuple

# 文件夹专用分类名：目录不按扩展名分类，统一归入此类
FOLDER_CATEGORY = "文件夹"

# 分类规则常量：分类名 -> 扩展名集合（小写、带点号）
CATEGORY_RULES: Dict[str, set] = {
    "文档": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".txt", ".md", ".csv", ".epub", ".caj", ".rtf"},
    "图片": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
            ".ico", ".psd", ".ai", ".tif", ".tiff"},
    "视频": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "音频": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".iso", ".cab"},
    "安装包": {".exe", ".msi", ".apk", ".bat", ".cmd", ".ps1", ".jar"},
    "代码": {".py", ".js", ".ts", ".html", ".css", ".c", ".cpp", ".h",
            ".java", ".go", ".rs", ".sql", ".json", ".xml", ".yml", ".yaml"},
    "快捷方式": {".lnk", ".url"},
    "其他": set(),  # 兜底分类：未匹配任何扩展名的文件
}

# 整理时默认忽略的文件（避免搬走系统关键文件）
IGNORED_NAMES = {"desktop.ini", "dumpstack.log.tmp", "swapfile.sys"}


@dataclass
class MoveRecord:
    """一次文件移动的记录，用于撤销恢复（src 原位置 -> dst 整理后位置）。"""

    src: str          # 原始路径，撤销时恢复到此处
    dst: str          # 整理后的路径
    time: str = ""    # 移动时间（ISO 格式字符串）
    batch: str = ""   # 批次 ID：同一次整理/拖拽产生的记录共享同一 ID

    def to_dict(self) -> dict:
        """序列化为 JSON 可存储的字典。"""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "MoveRecord":
        """从字典反序列化，容忍缺失字段。"""
        return MoveRecord(
            src=data.get("src", ""),
            dst=data.get("dst", ""),
            time=data.get("time", ""),
            batch=data.get("batch", ""),
        )


def get_desktop_path() -> str:
    """读取当前用户的真实桌面路径（兼容被重定向到其他盘的情况）。

    优先读注册表 User Shell Folders，失败时退回默认路径。
    """
    import winreg  # 仅 Windows 需要，放函数内方便测试环境跳过

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        raw, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        return os.path.expandvars(os.path.expanduser(raw))
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def is_within(path: str, root: str) -> bool:
    """判断 path 是否等于 root 或位于 root 内部（防止把收纳库搬进自己）。"""
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(root)
    return path_abs == root_abs or path_abs.startswith(root_abs + os.sep)


def classify(filename: str) -> str:
    """按扩展名返回文件所属分类名；无扩展名或未知扩展名归入"其他"。"""
    ext = os.path.splitext(filename)[1].lower()
    for category, exts in CATEGORY_RULES.items():
        if ext in exts:
            return category
    return "其他"


def unique_path(path: str) -> str:
    """路径冲突时生成不重复路径：name.ext -> name (2).ext -> name (3).ext ..."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 2
    while os.path.exists(f"{base} ({counter}){ext}"):
        counter += 1
    return f"{base} ({counter}){ext}"


def _iter_candidates(desktop_path: str, storage_root: str,
                     include_shortcuts: bool, include_folders: bool,
                     only_names=None):
    """枚举桌面上应被整理的条目（供整理与预览共用）。

    逐个 yield (名称, 完整路径, 分类)；桌面不可访问时抛 RuntimeError。
    only_names 不为 None 时仅处理名称在该集合中的条目（自动整理用）。
    """
    try:
        entries = os.listdir(desktop_path)
    except OSError as exc:
        raise RuntimeError(f"无法访问桌面目录: {exc}") from exc
    for name in entries:
        if only_names is not None and name not in only_names:
            continue
        src = os.path.join(desktop_path, name)
        # 跳过隐藏文件、系统文件，以及收纳库自身/内部路径（自我保护）
        if name.lower() in IGNORED_NAMES or name.startswith("."):
            continue
        if is_within(src, storage_root):
            continue
        if os.path.isdir(src):
            if not include_folders:
                continue          # 文件夹收录开关关闭时留在桌面
            category = FOLDER_CATEGORY
        elif os.path.isfile(src):
            category = classify(name)
            if category == "快捷方式" and not include_shortcuts:
                continue
        else:
            continue              # 跳过失效链接等特殊条目
        yield name, src, category


def organize_desktop(desktop_path: str, storage_root: str,
                     include_shortcuts: bool = False,
                     include_folders: bool = True,
                     only_names=None) -> List[MoveRecord]:
    """一键整理：把桌面顶层文件/文件夹按分类移动到收纳根目录的对应子文件夹。

    :param desktop_path: 桌面目录
    :param storage_root: 收纳根目录（自动创建分类子目录）
    :param include_shortcuts: 是否把快捷方式也收走（默认留在桌面）
    :param include_folders: 是否把桌面顶层文件夹整体收进"文件夹"分类
    :param only_names: 仅整理名称集合中的条目（自动整理新文件用），None=全部
    :return: 本次成功移动的 MoveRecord 列表（同一 batch）
    """
    batch = uuid.uuid4().hex[:12]
    records: List[MoveRecord] = []
    for name, src, category in _iter_candidates(
            desktop_path, storage_root, include_shortcuts, include_folders,
            only_names):
        dest_dir = os.path.join(storage_root, category)
        os.makedirs(dest_dir, exist_ok=True)
        dst = unique_path(os.path.join(dest_dir, name))
        try:
            shutil.move(src, dst)
        except (OSError, shutil.Error):
            continue  # 单个文件失败（被占用等）不影响其余文件
        records.append(MoveRecord(
            src=src, dst=dst, batch=batch,
            time=datetime.now().isoformat(timespec="seconds"),
        ))
    return records


def preview_organize(desktop_path: str, storage_root: str,
                     include_shortcuts: bool = False,
                     include_folders: bool = True) -> List[Tuple[str, str]]:
    """整理前预览：返回将被移动的 (分类名, 名称) 列表，不产生任何实际改动。"""
    return [(category, name) for name, _src, category in _iter_candidates(
        desktop_path, storage_root, include_shortcuts, include_folders)]


def undo_moves(records: List[MoveRecord]) -> List[MoveRecord]:
    """撤销一批移动：把文件从 dst 移回 src，返回实际恢复成功的记录。

    按记录逆序恢复；目标或源异常时跳过该条，不中断整体流程。
    """
    restored: List[MoveRecord] = []
    for rec in reversed(records):
        if not rec.dst or not os.path.exists(rec.dst):
            continue  # 目标已不存在（被用户手动处理过），跳过
        src = rec.src
        if not src:
            continue
        if os.path.exists(src):
            src = unique_path(src)  # 原位置已有同名文件则改名恢复
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(rec.dst, src)
        except (OSError, shutil.Error):
            continue
        restored.append(rec)
    return restored


def move_into(src: str, folder: str) -> str:
    """把单个外部文件或文件夹移入指定格子文件夹，返回目标路径（供拖拽调用）。

    目标重名时自动加后缀；源不存在或试图移入自身内部时抛异常由调用方处理。
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f"源路径不存在: {src}")
    if is_within(src, folder):
        raise ValueError("不能把收纳库内部的路径移进它自己")
    dst = unique_path(os.path.join(folder, os.path.basename(src)))
    shutil.move(src, dst)
    return dst


def search_storage(root: str, keyword: str,
                   limit: int = 100, extra_dirs=None) -> List[Tuple[str, str]]:
    """在收纳根目录下递归搜索文件名包含关键词的文件。

    :param root: 收纳根目录
    :param keyword: 搜索关键词（大小写不敏感、支持拼音与首字母；空关键词返回空结果）
    :param limit: 结果上限，防止大收纳库卡界面
    :param extra_dirs: 额外的 (分类名, 目录) 列表（路径映射的格子），一并搜索
    :return: (分类名, 完整路径) 列表
    """
    results: List[Tuple[str, str]] = []
    kw = keyword.strip().lower()
    if not kw or limit <= 0:
        return results

    def scan(category: str, base_dir: str) -> bool:
        """扫描一个目录；返回 True 表示已达结果上限、需要整体结束。"""
        for cur, _dirs, files in os.walk(base_dir):
            for name in files:
                if kw not in name.lower() and not _pinyin_matches(name, kw):
                    continue
                if len(results) >= limit:      # 上限校验必须在 append 之前
                    return True
                results.append((category, os.path.join(cur, name)))
        return False

    try:
        categories = sorted(os.listdir(root))
    except OSError:
        categories = []
    for cat in categories:
        cat_dir = os.path.join(root, cat)
        if not os.path.isdir(cat_dir):
            continue
        if scan(cat, cat_dir):
            return results
    for cat, base_dir in (extra_dirs or []):
        if os.path.isdir(base_dir) and scan(cat, base_dir):
            return results
    return results


def _pinyin_matches(name: str, keyword: str) -> bool:
    """拼音匹配：文件名全拼或首字母包含关键词时命中（pypinyin 缺失时返回 False）。"""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return False
    parts = lazy_pinyin(name)
    if keyword in "".join(parts).lower():
        return True
    initials = "".join(part[0] for part in parts if part).lower()
    return keyword in initials


def move_to_recycle_bin(path: str) -> bool:
    """把文件/文件夹删除到 Windows 回收站（可在资源管理器中恢复），成功返回 True。

    仅 Windows 可用；路径不存在或删除失败时返回 False（不弹系统错误框），
    由调用方决定如何提示用户。
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"   # 双空字符结尾的路径列表
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted
