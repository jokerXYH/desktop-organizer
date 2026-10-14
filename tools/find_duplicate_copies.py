# -*- coding: utf-8 -*-
"""扫描收纳库中"同名 (N)"副本并逐份校验内容一致性。

只报告、不删除；配合 --recycle 参数才把"与主副本内容完全一致"的副本
放入回收站（organizer.move_to_recycle_bin，可在资源管理器恢复）。

用法：
    python tools/find_duplicate_copies.py [收纳库路径] [--recycle]
不带 --recycle 时仅输出报告。
"""
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_COPY_SUFFIX = re.compile(r"^(.*?) \((\d+)\)(\.[^.]*)?$")


def _strip_copy_suffix(name):
    """把 "name (2)" / "name (2).ext" 归并为基准名；普通名字原样返回。"""
    m = _COPY_SUFFIX.match(name)
    if not m:
        return name
    return (m.group(1) + (m.group(3) or ""))


def _file_md5(path, chunk=1024 * 1024):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def _folder_signature(path):
    """文件夹内容签名：{相对路径: (大小, md5)}；不可读文件标记为 None。"""
    sig = {}
    for cur, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, path)
            try:
                sig[rel] = (os.path.getsize(full), _file_md5(full))
            except OSError:
                sig[rel] = None
    return sig


def scan(root):
    """扫描收纳库，返回 [(分类, 基准名, [副本路径...])]（含主副本，按序号排序）。"""
    groups = {}
    for category in sorted(os.listdir(root)):
        cat_dir = os.path.join(root, category)
        if not os.path.isdir(cat_dir):
            continue
        for name in sorted(os.listdir(cat_dir)):
            base = _strip_copy_suffix(name)
            if base == name:
                continue                    # 不是 "name (N)" 形式
            key = (category, base)
            groups.setdefault(key, []).append(os.path.join(cat_dir, name))
    # 把主副本（无后缀原名）也纳入同组
    merged = {}
    for (category, base), copies in groups.items():
        main_path = os.path.join(root, category, base)
        all_paths = ([main_path] if os.path.exists(main_path) else []) + copies
        merged[(category, base)] = all_paths
    return sorted(merged.items())


def verify_identical(paths):
    """按内容签名聚类同组副本；返回 (每簇代表路径, 待删路径, 全部签名)。"""
    sigs = []
    for p in paths:
        if os.path.isdir(p):
            sigs.append((p, ("dir", _folder_signature(p))))
        else:
            sigs.append((p, ("file", os.path.getsize(p), _file_md5(p))))
    clusters = {}                      # 签名 -> 该簇全部路径（保序）
    for p, sig in sigs:
        clusters.setdefault(repr(sig[1:]), []).append(p)
    keep, drop = [], []
    for members in clusters.values():
        keep.append(members[0])        # 每簇保留第一份（主副本优先，已在最前）
        drop.extend(members[1:])
    return keep, drop, sigs


def _tree_size(path):
    if os.path.isdir(path):
        return sum(os.path.getsize(os.path.join(c, f))
                   for c, _d, fs in os.walk(path) for f in fs)
    return os.path.getsize(path)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "收纳库")
    recycle = "--recycle" in sys.argv[2:]
    if not os.path.isdir(root):
        print(f"收纳库不存在: {root}")
        return 1

    import organizer as org

    groups = scan(root)
    if not groups:
        print("未发现 \"name (N)\" 形式的副本。")
        return 0

    duplicate_count = 0
    waste_bytes = 0
    for (category, base), paths in groups:
        keep, drop, sigs = verify_identical(paths)
        clusters = len({repr(s[1:]) for s in sigs})
        print(f"\n[{category}] {base} — {len(paths)} 份，"
              f"按内容分为 {clusters} 簇（保留 {len(keep)} 份）")
        for p in paths:
            mark = "保留" if p in keep else "冗余"
            print(f"    {mark}  {_tree_size(p):>13,} B  {os.path.basename(p)}")
        duplicate_count += len(drop)
        waste_bytes += sum(_tree_size(p) for p in drop)
        if recycle:
            for p in drop:
                if org.move_to_recycle_bin(p):
                    print(f"    -> 已放入回收站: {os.path.basename(p)}")
                else:
                    print(f"    !! 回收站失败，保留: {p}")
    print(f"\n合计：{duplicate_count} 份冗余副本，约 {waste_bytes / 1024 / 1024:.1f} MB")
    if not recycle:
        print("（仅报告模式；加 --recycle 参数才会把同簇冗余副本放入回收站）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
