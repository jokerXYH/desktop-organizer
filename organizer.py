# -*- coding: utf-8 -*-
"""核心文件整理逻辑：分类规则、桌面扫描、移动与撤销、收纳库搜索。

本模块为纯逻辑层，不依赖任何 GUI 框架，便于单元测试与后续复用。
v2：新增文件夹支持——桌面顶层文件夹作为一个整体移入"文件夹"分类；
新增 search_storage 供控制面板搜索收纳库。
"""
import os
import shutil
import stat
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

# "还在写入中"的临时名（自动整理跳过；手动整理不受影响）：
# 浏览器下载中 .crdownload/.part、Office 打开文档时的 ~$ 锁文件等。
# 直接收走这类文件会撞上写入方随后按路径重建/改名，制造副本与遗漏。
IN_PROGRESS_SUFFIXES = (".crdownload", ".part", ".partial", ".download")
IN_PROGRESS_PREFIXES = ("~$",)


def is_in_progress_name(name: str) -> bool:
    """判断是否"还在写入中"的临时文件名（浏览器下载中 / Office 锁文件等）。"""
    low = name.lower()
    if low.endswith(IN_PROGRESS_SUFFIXES):
        return True
    return name.startswith(IN_PROGRESS_PREFIXES)


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
    """判断 path 是否等于 root 或位于 root 内部（防止把收纳库搬进自己）。

    Windows 文件系统大小写不敏感：若只比小写敏感的字符串，
    "D:\\WaiBu" 与 "d:\\waibu" 会被判为不同路径，自我保护随之失效
    （收纳库自身的文件可能被再次"收纳"）。比较前统一 normcase。
    """
    path_abs = os.path.normcase(os.path.abspath(path))
    root_abs = os.path.normcase(os.path.abspath(root))
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


# Windows 文件名非法字符（与资源管理器一致），同时挡掉子目录分隔符
INVALID_NAME_CHARS = '\\/:*?"<>|'


def validate_entry_name(new_name: str) -> str:
    """校验文件/文件夹的新名称；合法返回空字符串，否则返回错误提示。"""
    name = str(new_name).strip()
    if not name:
        return "名称不能为空"
    # 路径保留名优先判断："."/".." 会被句点规则误判为"以句点结尾"
    if name in (".", ".."):
        return "名称无效"
    for ch in INVALID_NAME_CHARS:
        if ch in name:
            return '名称不能包含 \\ / : * ? " < > | 字符'
    if name.endswith("."):
        return "名称不能以句点结尾"
    return ""


def rename_entry(path: str, new_name: str) -> str:
    """就地重命名文件/文件夹（只改名称，不移动位置），成功返回新的完整路径。

    失败用异常表达，交由 UI 提示：
    - 名称非法或目标已存在（不允许覆盖）→ ValueError
    - 文件被占用 / 无权限 → OSError
    源文件带有只读位不影响改名（改名只动目录项，不写文件内容）。
    """
    # 先校验用户输入（边界优先），无谓的文件系统查询放在其后
    new_name = str(new_name or "").strip()
    error = validate_entry_name(new_name)
    if error:
        raise ValueError(error)
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    parent = os.path.dirname(os.path.abspath(path))
    target = os.path.join(parent, new_name)
    if os.path.exists(target):
        raise ValueError(f"「{new_name}」已存在")
    os.rename(path, target)          # 源只读也能成功：改名不触碰文件内容
    return target


def _rmtree_onerror(func, path, _exc_info):
    """rmtree 回调：只读/权限导致删除失败时，先解除只读位再重试一次。"""
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _remove_partial(path: str) -> None:
    """删除"移动失败时已落在目标位置"的半成品，避免重复副本越积越多。

    为什么需要它：跨盘移动 = 复制 + 删源，删源失败时目标副本已经写盘。
    旧实现只把该条目标为"失败"就继续下一个，那份副本永远留在收纳库里；
    用户每点一次"一键整理桌面"就再叠一份同名副本（这正是本次反馈的
    "副本越来越多"）。目标副本通常带只读位，删除前需先解除。
    只删我们自己刚创建的路径（调用方经 unique_path 保证原本不存在），安全。
    """
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, onerror=_rmtree_onerror)
        elif os.path.exists(path):
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
            os.remove(path)
    except OSError:
        pass          # 清理失败不影响主流程，仍按"移动失败"上报给用户


def _has_readonly(path: str) -> bool:
    """条目自身（目录则为其后代）是否含只读位——只读是"删源失败"的根因。"""
    try:
        if os.path.isfile(path):
            return not os.access(path, os.W_OK)
        if os.path.isdir(path):
            for base, _dirs, files in os.walk(path):
                for name in files:
                    if not os.access(os.path.join(base, name), os.W_OK):
                        return True     # 找到第一个只读即可返回，避免全量扫描
    except OSError:
        return False
    return False


def _chmod_readonly(path: str, readonly: bool) -> None:
    """递归设置/解除只读位（用于只读条目的重试与收纳后属性还原）。"""
    if os.path.isfile(path):
        mode = os.stat(path).st_mode
        os.chmod(path, (mode & ~stat.S_IWRITE) if readonly
                 else (mode | stat.S_IWRITE))
        return
    for base, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            mode = os.stat(full).st_mode
            os.chmod(full, (mode & ~stat.S_IWRITE) if readonly
                     else (mode | stat.S_IWRITE))


def move_entry(src: str, dst: str) -> bool:
    """把 src 移动到 dst（调用方须保证 dst 原本不存在），返回是否成功。

    自己包一层的原因：跨盘移动在 Windows 下对只读条目会"复制成功、删源失败"，
    结果既没收纳进格子，又在目标留一份副本。处理顺序刻意做成"先正常试、
    失败才修"，绝大多数文件走第一条分支，不做任何额外扫描或改属性：

    1) 直接 shutil.move（快路径）；
    2) 失败：清掉本次生成的半成品目标，防止下次整理再叠一份副本；
    3) 源含只读位时解除只读后重试一次，让只读文件也能真正收纳；
    4) 成功后把副本还原为只读——保持用户原有的文件属性不被改变；
    5) 重试仍失败则还原源属性并返回 False，由调用方计入失败清单提示用户。
    """
    try:
        shutil.move(src, dst)
        return True
    except (OSError, shutil.Error):
        _remove_partial(dst)              # 先清半成品，避免留下重复副本
    if not _has_readonly(src):
        return False
    try:
        _chmod_readonly(src, readonly=False)
        shutil.move(src, dst)
    except (OSError, shutil.Error):
        _remove_partial(dst)
        try:
            _chmod_readonly(src, readonly=True)   # 重试失败：还原源属性
        except OSError:
            pass
        return False
    _chmod_readonly(dst, readonly=True)   # 收纳到位后还原原来的只读属性
    return True


# 合并去重时判定"同一份内容"的修改时间容差（秒）：沿用 robocopy 的口径
_SAME_CONTENT_TOLERANCE_S = 2


def _same_content(path_a: str, path_b: str) -> bool:
    """两个文件能否视为"同一份内容"：大小一致 + 修改时间相差 2 秒内。

    沿用 robocopy 的判定口径，不逐字节比对——收纳库里常有 GB 级大文件，
    全量哈希会让整理卡住。判定为同一份时桌面那份会被送去回收站（可人工
    找回），所以口径取"保守"而非"激进"：大小或时间对不上就保留两份。
    """
    try:
        stat_a, stat_b = os.stat(path_a), os.stat(path_b)
    except OSError:
        return False
    return (stat_a.st_size == stat_b.st_size
            and abs(stat_a.st_mtime - stat_b.st_mtime) <= _SAME_CONTENT_TOLERANCE_S)


def _remove_empty_dir(path: str) -> None:
    """删除已清空的目录（只读目录先解除只读位），失败时静默留在原地。"""
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        os.rmdir(path)
    except OSError:
        pass


def merge_directory(src: str, dst: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """把 src 目录合并进"已存在的同名目录"dst，返回 (逐项移动列表, 未处理源路径)。

    为什么要合并而不是生成 "名字 (2)"：桌面文件夹经常与收纳库里已有的同名
    文件夹是同一批内容（历史残留、反复整理）。继续用 unique_path 就会像以前
    那样在收纳库里叠出 "jifang (2)" 这类同名副本，用户看到的就是"莫名两份"。
    合并规则（不覆盖、不丢数据）：
      - dst 无同名项       → 正常移过去，记入移动列表（可被撤销恢复）；
      - 同名且都是目录     → 递归合并；
      - 同名文件且内容相同 → 视为重复：桌面那份送回收站（回收站不可用时改为
                             在 dst 内改名保留，绝不静默丢数据）；
      - 同名但内容不同     → 在 dst 内用 unique_path 改名保留，两份都留。
    源目录只有在被完全清空后才删除；仍有条目留下时把它们返回，交由调用方按
    "整理失败"提示用户（可下次重试），不会留下难以察觉的半截状态。
    """
    pairs: List[Tuple[str, str]] = []
    leftovers: List[str] = []
    try:
        names = os.listdir(src)
    except OSError:
        return pairs, [src]            # 源目录都读不到：整体算未处理，交给调用方报告

    for name in names:
        src_child = os.path.join(src, name)
        dst_child = os.path.join(dst, name)
        if os.path.isdir(src_child) and os.path.isdir(dst_child):
            sub_pairs, sub_left = merge_directory(src_child, dst_child)
            pairs.extend(sub_pairs)
            leftovers.extend(sub_left)
            continue
        # 完全相同的重复项：去重（不进移动列表——它已不是"移动"而是"丢弃重复"）
        if (os.path.isfile(src_child) and os.path.isfile(dst_child)
                and _same_content(src_child, dst_child)
                and move_to_recycle_bin(src_child)):
            continue
        # 无重名 → 原样落位；有重名（内容不同，或回收站不可用）→ 改名保留
        target = dst_child if not os.path.exists(dst_child) else unique_path(dst_child)
        if not move_entry(src_child, target):
            leftovers.append(src_child)          # 被占用等：留下现场，下次可重试
            continue
        pairs.append((src_child, target))

    if not leftovers:
        _remove_empty_dir(src)                   # 已彻底清空才删除源目录
    return pairs, leftovers


def _iter_candidates(desktop_path: str, storage_root: str,
                     include_shortcuts: bool, include_folders: bool,
                     only_names=None, skip_in_progress: bool = False,
                     skip_sink=None, enabled_categories=None):
    """枚举桌面上应被整理的条目（供整理与预览共用）。

    逐个 yield (名称, 完整路径, 分类)；桌面不可访问时抛 RuntimeError。
    only_names 不为 None 时仅处理名称在该集合中的条目（自动整理用）。
    skip_in_progress 为 True 时跳过"还在写入中"的临时名（自动整理用，
    手动整理/预览保持所见即所得，不受影响）。
    skip_sink 为 list 时，"有意留在桌面"的条目名会追加进去：既包括因开关
    关闭而留下的，也包括分类没有对应格子而留下的（见 enabled_categories）。
    自动整理需要把它们与被成功收纳/移动失败的条目区分开，否则会被误记为
    "已收纳"而长期不再收纳（见 organize_desktop 的 skipped 返回值）。
    enabled_categories 为集合时，仅收纳其中列出的分类；未列出（没有对应
    格子）的分类条目留在桌面——否则会被搬进收纳库却没有任何格子显示，
    用户视角就是"文件莫名消失了"。
    """
    try:
        entries = os.listdir(desktop_path)
    except OSError as exc:
        raise RuntimeError(f"无法访问桌面目录: {exc}") from exc
    for name in entries:
        if only_names is not None and name not in only_names:
            continue
        if skip_in_progress and is_in_progress_name(name):
            continue
        src = os.path.join(desktop_path, name)
        # 跳过隐藏文件、系统文件，以及收纳库自身/内部路径（自我保护）
        if name.lower() in IGNORED_NAMES or name.startswith("."):
            continue
        if is_within(src, storage_root):
            continue
        if os.path.isdir(src):
            if not include_folders:
                if skip_sink is not None:
                    skip_sink.append(name)   # 文件夹开关关闭：有意留在桌面
                continue
            category = FOLDER_CATEGORY
        elif os.path.isfile(src):
            category = classify(name)
            if category == "快捷方式" and not include_shortcuts:
                if skip_sink is not None:
                    skip_sink.append(name)   # 快捷方式开关关闭：有意留在桌面
                continue
        else:
            continue              # 跳过失效链接等特殊条目
        if enabled_categories is not None and category not in enabled_categories:
            if skip_sink is not None:
                skip_sink.append(name)   # 该分类没有对应格子：留在桌面，不漏文件
            continue
        yield name, src, category


def organize_desktop(desktop_path: str, storage_root: str,
                     include_shortcuts: bool = False,
                     include_folders: bool = True,
                     only_names=None, skip_in_progress: bool = False,
                     return_failed: bool = False, enabled_categories=None):
    """一键整理：把桌面顶层文件/文件夹按分类移动到收纳根目录的对应子文件夹。

    :param desktop_path: 桌面目录
    :param storage_root: 收纳根目录（自动创建分类子目录）
    :param include_shortcuts: 是否把快捷方式也收走（默认留在桌面）
    :param include_folders: 是否把桌面顶层文件夹整体收进"文件夹"分类
    :param only_names: 仅整理名称集合中的条目（自动整理新文件用），None=全部
    :param skip_in_progress: 跳过"还在写入中"的临时名（自动整理用）
    :param return_failed: 为 True 时返回 (成功记录, 失败名称列表, 跳过名称列表)，
        供自动整理做有界重试（默认 False 保持旧返回值，兼容手动整理与测试）。
        失败=移动时异常（被占用等）；跳过=有意留在桌面（收录开关关闭，或该
        分类没有对应格子），两者都必须与"成功收纳"区分，否则自动整理会误记。
    :param enabled_categories: 收纳结果会被放进的全部分类集合（通常是当前
        显示为格子的分类）。None 表示不限（兼容旧调用）。
    :return: 本次成功移动的 MoveRecord 列表（同一 batch）；return_failed 时为元组
    """
    batch = uuid.uuid4().hex[:12]
    records: List[MoveRecord] = []
    failed_names: List[str] = []
    skipped_names: List[str] = []
    for name, src, category in _iter_candidates(
            desktop_path, storage_root, include_shortcuts, include_folders,
            only_names, skip_in_progress, skipped_names, enabled_categories):
        dest_dir = os.path.join(storage_root, category)
        os.makedirs(dest_dir, exist_ok=True)
        existing = os.path.join(dest_dir, name)
        if os.path.isdir(src) and os.path.isdir(existing):
            # 目标已有同名文件夹：合并进去（并在其中按内容去重），而不是生成
            # "名字 (2)"——那正是用户看到的"莫名多出一份同名文件夹"。
            pairs, leftovers = merge_directory(src, existing)
            stamp = datetime.now().isoformat(timespec="seconds")
            records.extend(MoveRecord(src=child_src, dst=child_dst,
                                      batch=batch, time=stamp)
                           for child_src, child_dst in pairs)
            if leftovers:
                failed_names.append(name)  # 部分条目没搬走（被占用等）：提示可重试
            continue
        dst = unique_path(existing)
        if not move_entry(src, dst):
            failed_names.append(name)  # 单个文件失败（被占用等）不影响其余文件
            continue
        records.append(MoveRecord(
            src=src, dst=dst, batch=batch,
            time=datetime.now().isoformat(timespec="seconds"),
        ))
    if return_failed:
        return records, failed_names, skipped_names
    return records


def preview_organize(desktop_path: str, storage_root: str,
                     include_shortcuts: bool = False,
                     include_folders: bool = True,
                     enabled_categories=None) -> List[Tuple[str, str]]:
    """整理前预览：返回将被移动的 (分类名, 名称) 列表，不产生任何实际改动。

    预览与实际整理共用同一枚举逻辑与 enabled_categories 限制，
    否则预览会列出实际不会被移动的条目（如未启用格子的视频类文件）。
    """
    return [(category, name) for name, _src, category in _iter_candidates(
        desktop_path, storage_root, include_shortcuts, include_folders,
        enabled_categories=enabled_categories)]


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
        os.makedirs(os.path.dirname(src), exist_ok=True)
        # 用 move_entry 而非裸 shutil.move：收纳进来的只读文件若直接跨盘搬回，
        # 会"复制成功、删源失败"，在收纳库里留下副本（撤销不完全）。
        if not move_entry(rec.dst, src):
            continue
        restored.append(rec)
    return restored


def move_into(src: str, folder: str) -> str:
    """把单个外部文件或文件夹移入指定格子文件夹，返回目标路径（供拖拽调用）。

    目标重名时自动加后缀；源不存在或试图移入自身内部时抛异常由调用方处理。
    移动经 move_entry 完成：只读源跨盘移动失败时会被正确处理（见其文档），
    否则拖入只读文件会"复制成功、删源失败"，桌面上留下原件而格子多一份副本。
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f"源路径不存在: {src}")
    if is_within(src, folder):
        raise ValueError("不能把收纳库内部的路径移进它自己")
    dst = unique_path(os.path.join(folder, os.path.basename(src)))
    if not move_entry(src, dst):
        raise OSError(f"移动失败（可能被占用或只读）: {src}")
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
