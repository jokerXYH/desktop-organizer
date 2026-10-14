# -*- coding: utf-8 -*-
"""版本更新：检查 / 下载 / exe 自替换（「点击更新」的完整链路）。

更新源：一个返回「新版本清单」JSON 的地址（面板里可配置，见 config.updateUrl）：
    {
      "version": "2.10.1",                    # 必填：新版本号
      "url": "https://…/桌面分类助手.exe",     # 必填：新版本包直链
      "sha256": "…",                          # 可选：完整性校验（不匹配不发更）
      "page": "https://…/releases",           # 可选：源码模式的下载页（缺省用 url）
      "notes": "更新说明文本"                  # 可选
    }

隐私：只有用户点击「检查更新 / 立即更新」才会发起网络请求（urllib），
其余时间不主动联网、不上传任何数据。

exe 更新流程（源码模式只打开下载页，不动正在使用的源码）：
1) 下载新 exe 到临时目录（带进度）；
2) apply_update：运行中的 exe 先改名为 *.old（Windows 允许重命名运行中的文件），
   再把新文件复制到原路径（复制失败自动还原，绝不把程序弄丢）；
3) 以 `--updated-from <旧PID>` 启动新 exe，旧进程退出；
4) 新实例启动时先等待旧进程退出、清理 *.old，随后正常启动——无需外部脚本。
"""
import hashlib
import os
import shutil
import urllib.request

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


# ---------- 纯逻辑（无 GUI / 无网络，便于单测） ----------

def normalize_version(text: str) -> tuple:
    """把版本号字符串归一为 (major, minor, patch) 元组；非法返回空元组。

    容忍 "v" 前缀、两段式（"2.9" → (2,9,0)）与后缀（"2.9.3-beta"）。
    """
    raw = str(text or "").strip().lstrip("vV")
    if not raw:
        return ()
    head = raw.split("-", 1)[0].strip()
    parts = head.split(".")
    if len(parts) > 3:
        return ()
    numbers = []
    for part in parts:
        if not part.isdigit():
            return ()
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def is_newer(remote: str, current: str) -> bool:
    """远程版本是否比当前版本新（任一非法则视为不更新）。"""
    remote_v = normalize_version(remote)
    current_v = normalize_version(current)
    if not remote_v or not current_v:
        return False
    return remote_v > current_v


def validate_manifest(data) -> dict:
    """校验更新清单并归一化字段；缺失/非法必填项时抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("更新清单格式不正确（应为 JSON 对象）")
    version = str(data.get("version", "")).strip()
    url = str(data.get("url", "")).strip()
    if not version:
        raise ValueError("更新清单缺少 version 字段")
    if not normalize_version(version):
        raise ValueError(f"更新清单 version 字段不合法：{version}")
    if not url:
        raise ValueError("更新清单缺少 url 字段")
    return {
        "version": version,
        "url": url,
        "sha256": str(data.get("sha256", "")).strip().lower(),
        "notes": str(data.get("notes", "")).strip(),
        "page": str(data.get("page", "")).strip() or url,
    }


def sha256_file(path: str) -> str:
    """计算文件 SHA-256（下载完整性校验用）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: str, progress_cb=None, timeout: int = 30) -> None:
    """下载 url 到 dest；progress_cb(percent) 报告 0~100 进度。

    响应没有 Content-Length 时只在完成时报一次 100。
    """
    with urllib.request.urlopen(url, timeout=timeout) as response, \
            open(dest, "wb") as handle:
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        got = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            got += len(chunk)
            if progress_cb is not None and total:
                progress_cb(min(100, got * 100 // total))
    if progress_cb is not None:
        progress_cb(100)


def apply_update(current_exe: str, new_exe: str) -> str:
    """把新 exe 替换到当前程序路径（运行中先改名为 .old），返回新程序路径。

    运行中的 exe 在 Windows 上允许改名但不允许覆盖，所以顺序是：
    改名旧文件 → 复制新文件（旧文件留在 *.old，下次启动清理）。
    复制失败时把 .old 改回原名并抛 OSError，绝不把程序弄丢。
    """
    backup = current_exe + ".old"
    if os.path.exists(backup):
        try:
            os.remove(backup)
        except OSError:
            pass
    os.rename(current_exe, backup)
    try:
        shutil.copyfile(new_exe, current_exe)
    except OSError:
        os.rename(backup, current_exe)
        raise
    return current_exe


def cleanup_old_exe(exe_path: str) -> None:
    """清理上次自替换留下的 *.old（尽力而为，失败静默）。"""
    try:
        os.remove(exe_path + ".old")
    except OSError:
        pass


def wait_for_process_exit(pid: int, timeout_s: float = 25.0) -> bool:
    """等待指定进程退出；返回是否在超时前退出（进程不存在视为已退出）。

    用 WaitForSingleObject 真等内核对象，避免轮询（性能红线：不新增轮询定时器）。
    """
    if os.name != "nt":
        return True
    import ctypes

    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(
            handle, int(timeout_s * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


# ---------- 后台服务（检查 / 下载；网络请求只在用户点击后发起） ----------

class UpdateService(QObject):
    """后台执行「检查更新」与「下载新版本」（QThreadPool 串行，信号回主线程）。"""

    checked = Signal(bool, object)     # (成功?, 清单 dict 或错误文本)
    downloaded = Signal(bool, str)     # (成功?, 本地路径 或 错误文本)
    progress = Signal(int)             # 下载进度 0~100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    def check(self, url: str) -> None:
        """后台拉取并校验更新清单。"""
        self.pool.start(_CheckTask(self, url))

    def download(self, url: str, dest: str, sha256: str = "") -> None:
        """后台下载新版本到 dest（可选 SHA-256 校验）。"""
        self.pool.start(_DownloadTask(self, url, dest, sha256))


class _CheckTask(QRunnable):
    """一次「检查更新」任务：拉取 JSON → 校验 → 信号回主线程。"""

    def __init__(self, service: UpdateService, url: str):
        super().__init__()
        self.service = service
        self.url = url

    def run(self):
        import json

        try:
            with urllib.request.urlopen(self.url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            manifest = validate_manifest(payload)
            self.service.checked.emit(True, manifest)
        except Exception as exc:            # 网络/解析异常统一转成文本
            self.service.checked.emit(False, str(exc))


class _DownloadTask(QRunnable):
    """一次「下载新版本」任务：落盘 + 可选校验 → 信号回主线程。"""

    def __init__(self, service: UpdateService, url: str, dest: str,
                 sha256: str):
        super().__init__()
        self.service = service
        self.url = url
        self.dest = dest
        self.sha256 = sha256

    def run(self):
        try:
            download(self.url, self.dest,
                     progress_cb=self.service.progress.emit)
            if self.sha256 and sha256_file(self.dest) != self.sha256:
                raise ValueError("下载文件校验不通过（SHA-256 不匹配）")
            self.service.downloaded.emit(True, self.dest)
        except Exception as exc:
            self.service.downloaded.emit(False, str(exc))
