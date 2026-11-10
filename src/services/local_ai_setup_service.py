"""
src/services/local_ai_setup_service.py
Dịch vụ quản trị và cài đặt AI cục bộ (Local AI Setup Service) cho ChunDVC / ResolveFlow:
- Cung cấp khả năng cài đặt Ollama độc lập cho người dùng (user-local, không yêu cầu sudo/systemd)
- Quản lý mô hình nhận diện giọng nói Whisper (tải trước và cache theo tiêu chuẩn HuggingFace)
- Quản lý vòng đời tiến trình Ollama daemon cục bộ (127.0.0.1:11434) với định danh sở hữu xác thực dương tính (positive verification)
- Chống PID-reuse và bảo vệ không bao giờ gửi SIGKILL hoặc force-kill sang tiến trình người dùng
- Kiểm tra độ sẵn sàng trung thực khi khởi động (chỉ báo thành công khi loopback API phản hồi)
- Tải và xác thực mô hình thị giác AI (Vision Model, mặc định qwen3-vl:2b-instruct)
- Phòng vệ giải nén tệp lưu trữ (Path Traversal, Absolute Path, Symlinks/Hardlinks rejection)
- Hỗ trợ tiêm Adapter cho HTTP, Process, FileSystem phục vụ kiểm thử đơn vị độc lập 100%
"""

import os
import sys
import json
import uuid
import time
import shutil
import hashlib
import tarfile
import logging
import platform
import subprocess
import signal
import tempfile
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Callable, Any
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QSettings

from src.utils.subprocess_utils import clean_media_subprocess_env, no_window_kwargs

logger = logging.getLogger(__name__)

# Phiên bản phát hành Ollama chính thức được ghim cố định
OLLAMA_RELEASE_VERSION = "v0.40.16"

# Bảng mã băm SHA256 chính thức từ nhà sản xuất (sha256sum.txt)
OLLAMA_LINUX_AMD64_HASH = "87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d"
OLLAMA_LINUX_ARM64_HASH = "e45b8e6dcb86cb9f3057396351655079c9a4879b4ced2678c9350d375dcbc224"
OLLAMA_WINDOWS_AMD64_HASH = "84db4f756ddc9e091142a85cdc0f7ae5db68bc9b3e075f114388b326f8ed912d"

# Mô hình thị giác khuyên dùng chính thức (dung lượng ~1.9 GB)
DEFAULT_VISION_MODEL = "qwen3-vl:2b-instruct"

# Cấu hình cổng và máy chủ cục bộ mặc định
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"


class OllamaInstallStatus:
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    STOPPED = "stopped"
    RUNNING = "running"
    UNSUPPORTED = "unsupported"


class VisionModelStatus:
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"      # "Đã tải" (chưa xác thực)
    VERIFYING = "verifying"        # "Đang kiểm tra..."
    READY = "ready"                # "Sẵn sàng" (đã xác thực thành công)
    FAILED = "failed"              # "Lỗi"


class WhisperModelStatus:
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"      # "Sẵn sàng" (đã có trong cache)


@dataclass
class OllamaStatusResult:
    install_status: str
    is_running: bool
    is_owned: bool
    pid: Optional[int]
    version: str
    endpoint: str
    details: str


@dataclass
class VisionModelStatusResult:
    model: str
    status: str
    size_mb: float
    digest: str
    is_verified: bool
    details: str


# ==============================================================================
# HELPER FUNCTIONS FOR PROCESS AND TAR SECURITY
# ==============================================================================

def _tar_member_path(name: str, dest_path: Path) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or "\\" in name or ":" in name:
        raise ValueError(f"Tệp lưu trữ chứa đường dẫn tuyệt đối không an toàn: '{name}'")
    target = dest_path.joinpath(*relative.parts)
    if ".." in relative.parts or not target.resolve().is_relative_to(dest_path):
        raise ValueError(f"Phát hiện tấn công leo thang thư mục (Path Traversal): '{name}'")
    return target


def validate_single_tar_member(member: tarfile.TarInfo, dest_path: Path):
    """
    Xác thực nghiêm ngặt một member trong tệp tar:
    - Từ chối đường dẫn tuyệt đối (/... hoặc C:\\...)
    - Từ chối leo thang thư mục (Path Traversal: ../ hoặc .. trong các phần đường dẫn)
    - Tránh lỗi startswith đơn thuần (dùng Path.resolve().relative_to và os.path.commonpath)
    - Từ chối mọi liên kết mềm (symlink), liên kết cứng (hardlink), FIFO, socket, device node.
    - Chỉ chấp nhận tệp thông thường (isfile) và thư mục (isdir).
    """
    name = member.name
    _tar_member_path(name, dest_path)
    if member.issym() or member.islnk():
        raise ValueError(f"Tệp lưu trữ chứa liên kết (link) không an toàn bị từ chối: '{name}'")
    if not (member.isfile() or member.isdir()):
        raise ValueError(f"Tệp lưu trữ chứa loại tệp đặc biệt không hỗ trợ: '{name}'")

def validate_tar_members(tar: tarfile.TarFile, dest_dir: str) -> List[tarfile.TarInfo]:
    """
    Xác thực nghiêm ngặt toàn bộ danh sách member trong tệp tar trước khi giải nén.
    """
    dest_path = Path(dest_dir).resolve()
    valid_members: List[tarfile.TarInfo] = []

    for member in tar.getmembers():
        validate_single_tar_member(member, dest_path)
        valid_members.append(member)

    return valid_members


def _extract_ollama_tar(
    archive: tarfile.TarFile,
    destination: Path,
    cancel_cb: Optional[Callable[[], bool]],
) -> bool:
    """Extract files before creating the package's sibling library links."""
    library_links: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for member in archive:
        if cancel_cb and cancel_cb():
            return False
        target = _tar_member_path(member.name, destination)
        if target in seen:
            raise ValueError(f"Tệp lưu trữ chứa đường dẫn trùng lặp: '{member.name}'")
        seen.add(target)
        if member.issym():
            relative = target.relative_to(destination)
            link = PurePosixPath(member.linkname)
            if (
                relative.parts[:1] != ("lib",)
                or link.is_absolute()
                or len(link.parts) != 1
                or link.name == ".."
                or "\\" in member.linkname
                or ":" in member.linkname
            ):
                raise ValueError(f"Liên kết thư viện không an toàn: '{member.name}'")
            library_links.append((target, link.name))
        else:
            validate_single_tar_member(member, destination)
            archive.extract(member, path=destination, filter="data")

    for target, link_name in library_links:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link_name)
    for target, _ in library_links:
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(destination / "lib") or not resolved.is_file():
            raise ValueError(f"Liên kết thư viện không trỏ đến tệp hợp lệ: '{target.name}'")
    return True


def _read_posix_proc_identity(pid: int) -> Optional[Dict[str, Any]]:
    """Đọc thông tin danh tính tiến trình từ /proc/{pid} trên POSIX để chống PID-reuse."""
    if sys.platform.startswith("win"):
        return None
    proc_dir = f"/proc/{pid}"
    if not os.path.isdir(proc_dir):
        return None

    try:
        uid = os.stat(proc_dir).st_uid
    except Exception:
        return None

    exe = ""
    try:
        exe = os.path.realpath(os.readlink(f"{proc_dir}/exe"))
    except Exception:
        pass

    cmd: List[str] = []
    try:
        with open(f"{proc_dir}/cmdline", "rb") as f:
            raw = f.read()
            cmd = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
    except Exception:
        pass

    start_time = None
    try:
        with open(f"{proc_dir}/stat", "r") as f:
            content = f.read()
        rparen = content.rfind(")")
        if rparen != -1:
            fields = content[rparen + 2:].split()
            if len(fields) >= 20:
                start_time = int(fields[19])
    except Exception:
        pass

    return {
        "pid": pid,
        "uid": uid,
        "executable": exe,
        "cmd": cmd,
        "start_time": start_time,
    }


# ==============================================================================
# ADAPTER INTERFACES FOR TESTING
# ==============================================================================

class IHttpClient(ABC):
    @abstractmethod
    def download_file(
        self,
        url: str,
        target_path: str,
        expected_sha256: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> bool:
        pass

    @abstractmethod
    def get_json(self, url: str, timeout: float = 5.0) -> Dict:
        pass

    @abstractmethod
    def post_json(self, url: str, payload: Dict, timeout: float = 10.0) -> Dict:
        pass

    @abstractmethod
    def post_json_streaming(
        self,
        url: str,
        payload: Dict,
        chunk_cb: Callable[[Dict], None],
        cancel_cb: Optional[Callable[[], bool]] = None,
        timeout: float = 60.0,
    ) -> bool:
        pass


class IProcessRunner(ABC):
    @abstractmethod
    def start_process(self, cmd: List[str], env: Dict[str, str], cwd: str) -> Tuple[int, Dict[str, Any]]:
        """Khởi chạy tiến trình và trả về (pid, metadata_nhận_diện)."""
        pass

    @abstractmethod
    def is_process_running(self, pid: int) -> bool:
        pass

    @abstractmethod
    def get_process_identity(self, pid: int) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def verify_process_identity(self, pid: int, expected_meta: Dict[str, Any]) -> bool:
        """Xác thực dương tính danh tính tiến trình (chống PID-reuse)."""
        pass

    @abstractmethod
    def terminate_graceful(self, pid: int, timeout: float = 5.0, expected_meta: Optional[Dict[str, Any]] = None) -> bool:
        """Dừng tiến trình nhẹ nhàng bằng verified handle (tuyệt đối KHÔNG force-kill/SIGKILL)."""
        pass

    @abstractmethod
    def verify_installed_binary(self, bin_path: str) -> bool:
        """Xác thực tệp binary đã cài đặt là Ollama hợp lệ."""
        pass

    @abstractmethod
    def start_detached_installer(self, installer_path: str) -> bool:
        """Mở bộ cài đặt Windows chính thức độc lập."""
        pass


class IFileSystemAdapter(ABC):
    @abstractmethod
    def get_free_disk_space(self, path: str) -> int:
        pass

    @abstractmethod
    def extract_archive(
        self,
        archive_path: str,
        dest_dir: str,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> bool:
        pass


# ==============================================================================
# DEFAULT PRODUCTION ADAPTERS
# ==============================================================================

class DefaultHttpClient(IHttpClient):
    """Adapter HTTP dùng thư viện chuẩn Python (urllib)."""

    def download_file(
        self,
        url: str,
        target_path: str,
        expected_sha256: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> bool:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        hasher = hashlib.sha256() if expected_sha256 else None

        req = urllib.request.Request(url, headers={"User-Agent": "ChunDVC-LocalAI/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 512

            part_path = target_path + ".part"
            with open(part_path, "wb") as f_out:
                while True:
                    if cancel_cb and cancel_cb():
                        if os.path.exists(part_path):
                            os.remove(part_path)
                        return False
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    if hasher:
                        hasher.update(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total_size)

            if expected_sha256 and hasher:
                actual_sha = hasher.hexdigest().lower()
                if actual_sha != expected_sha256.lower():
                    if os.path.exists(part_path):
                        os.remove(part_path)
                    raise ValueError(f"Checksum mismatch: mong đợi {expected_sha256}, thực tế {actual_sha}")

            if os.path.exists(target_path):
                os.remove(target_path)
            os.rename(part_path, target_path)
            return True

    def get_json(self, url: str, timeout: float = 5.0) -> Dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def post_json(self, url: str, payload: Dict, timeout: float = 10.0) -> Dict:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)

    def post_json_streaming(
        self,
        url: str,
        payload: Dict,
        chunk_cb: Callable[[Dict], None],
        cancel_cb: Optional[Callable[[], bool]] = None,
        timeout: float = 60.0,
    ) -> bool:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                if cancel_cb and cancel_cb():
                    return False
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue
                obj = json.loads(line_str)
                if not isinstance(obj, dict):
                    raise ValueError("Phản hồi tải mô hình Ollama không hợp lệ.")
                chunk_cb(obj)
        return True


class DefaultProcessRunner(IProcessRunner):
    """Adapter Process Runner thực thi tiến trình an toàn, kiểm tra danh tính và dừng nhẹ nhàng."""

    def __init__(
        self,
        pidfd_open: Optional[Callable[[int, int], int]] = None,
        pidfd_send_signal: Optional[Callable[[int, int], None]] = None,
        close_fd: Optional[Callable[[int], None]] = None,
        win_open_process: Optional[Callable[[int, bool, int], Any]] = None,
        win_close_handle: Optional[Callable[[Any], None]] = None,
        get_identity: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
        is_running: Optional[Callable[[int], bool]] = None,
    ):
        self._pidfd_open = pidfd_open if pidfd_open is not None else getattr(os, "pidfd_open", None)
        self._pidfd_send_signal = pidfd_send_signal if pidfd_send_signal is not None else getattr(signal, "pidfd_send_signal", None)
        self._close_fd = close_fd if close_fd is not None else getattr(os, "close", None)
        self._win_open_process = win_open_process
        self._win_close_handle = win_close_handle
        self._get_identity_override = get_identity
        self._is_running_override = is_running

    def start_process(self, cmd: List[str], env: Dict[str, str], cwd: str) -> Tuple[int, Dict[str, Any]]:
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs.update(no_window_kwargs())
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs
        )
        pid = proc.pid
        time.sleep(0.05)

        identity = self.get_process_identity(pid) or {}
        meta = {
            "pid": pid,
            "executable": os.path.realpath(cmd[0]),
            "cmd": list(cmd),
            "uid": identity.get("uid", os.getuid() if hasattr(os, "getuid") else None),
            "start_time": identity.get("start_time"),
        }
        return pid, meta

    def is_process_running(self, pid: int) -> bool:
        if self._is_running_override is not None:
            return self._is_running_override(pid)
        if pid <= 0:
            return False
        if sys.platform.startswith("win"):
            try:
                import ctypes
                PROCESS_QUERY_INFORMATION = 0x0400
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if handle == 0:
                    return False
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                # Kiểm tra trạng thái zombie trên POSIX/Linux
                if os.path.isdir(f"/proc/{pid}"):
                    try:
                        with open(f"/proc/{pid}/status", "r") as f:
                            for line in f:
                                if line.startswith("State:"):
                                    if "Z" in line or "X" in line:
                                        return False
                                    break
                    except Exception:
                        pass
                return True
            except (OSError, ProcessLookupError):
                return False

    def get_process_identity(self, pid: int) -> Optional[Dict[str, Any]]:
        if self._get_identity_override is not None:
            return self._get_identity_override(pid)
        if not self.is_process_running(pid):
            return None
        try:
            import psutil
            p = psutil.Process(pid)
            return {
                "pid": pid,
                "uid": p.uids().real if hasattr(p, "uids") else None,
                "executable": os.path.realpath(p.exe()) if p.exe() else "",
                "cmd": p.cmdline(),
                "start_time": int(p.create_time()),
            }
        except Exception:
            pass

        return _read_posix_proc_identity(pid)

    def verify_process_identity(self, pid: int, expected_meta: Dict[str, Any]) -> bool:
        if not self.is_process_running(pid):
            return False
        if not expected_meta or not isinstance(expected_meta, dict):
            logger.warning("Metadata rỗng hoặc sai kiểu: từ chối xác thực PID %s", pid)
            return False

        # Xác thực dương tính (positive verification): bắt buộc phải có đủ trường định danh
        exp_exe = os.path.realpath(str(expected_meta.get("executable", "")).strip())
        exp_cmd = expected_meta.get("cmd", [])
        if not exp_exe or not exp_cmd or not isinstance(exp_cmd, list):
            logger.warning("Metadata thiếu executable hoặc cmd: từ chối xác thực PID %s (chống PID-only file)", pid)
            return False

        current = self.get_process_identity(pid)
        if not current:
            return False

        cur_exe = os.path.realpath(str(current.get("executable", "")).strip())
        cur_cmd = current.get("cmd", [])
        if not cur_exe or not cur_cmd or not isinstance(cur_cmd, list):
            logger.warning("Tiến trình hiện tại PID %s thiếu executable hoặc cmd", pid)
            return False

        # 1. Kiểm tra Executable chính xác tuyệt đối (BỎ hoàn toàn cơ chế basename fallback)
        if exp_exe != cur_exe:
            logger.warning("Executable PID %s không khớp chính xác: mong đợi '%s' != thực tế '%s'", pid, exp_exe, cur_exe)
            return False

        # 2. Kiểm tra Command: Bắt buộc có đối số 'serve' chính xác (không chấp nhận substring như 'observe')
        has_exact_serve = any(str(arg).strip().lower() == "serve" for arg in cur_cmd)
        if not has_exact_serve:
            logger.warning("Lệnh thực thi PID %s không chứa đối số chính xác 'serve': %s", pid, cur_cmd)
            return False

        # 3. Bắt buộc có các trường định danh và trên POSIX kiểm tra UID + Start Time chống PID-reuse
        if not sys.platform.startswith("win"):
            exp_uid = expected_meta.get("uid")
            cur_uid = current.get("uid")
            if exp_uid is None or cur_uid is None:
                logger.warning("Thiếu trường bắt buộc UID trên POSIX: từ chối xác thực PID %s", pid)
                return False
            if hasattr(os, "getuid"):
                if cur_uid != os.getuid() or exp_uid != os.getuid():
                    logger.warning("UID PID %s không khớp người dùng hiện tại (%s): exp=%s, cur=%s",
                                   pid, os.getuid(), exp_uid, cur_uid)
                    return False
            elif exp_uid != cur_uid:
                return False

            exp_st = expected_meta.get("start_time")
            cur_st = current.get("start_time")
            if exp_st is None or cur_st is None:
                logger.warning("Thiếu trường bắt buộc start_time trên POSIX: từ chối xác thực PID %s để chống PID-reuse", pid)
                return False
            if exp_st != cur_st:
                logger.warning("Start time PID %s không khớp (phát hiện PID-reuse): exp=%s != cur=%s",
                               pid, exp_st, cur_st)
                return False

        return True

    def terminate_graceful(self, pid: int, timeout: float = 5.0, expected_meta: Optional[Dict[str, Any]] = None) -> bool:
        """
        Dừng tiến trình nhẹ nhàng bằng handle đã xác thực (verified process handle).
        - Trên Linux: Sử dụng pidfd_open + revalidate danh tính + pidfd_send_signal(SIGTERM).
          Nếu pidfd không khả dụng hoặc danh tính không khớp: fail closed (trả về False, 0 signals gửi đi).
        - Trên Windows: Fail closed nếu không có cơ chế verified process handle an toàn.
          Tuyệt đối KHÔNG gọi taskkill chung chung trên numeric PID tùy ý.
        - Tuyệt đối KHÔNG gửi SIGKILL hay taskkill /F.
        """
        if not self.is_process_running(pid):
            return True

        if sys.platform.startswith("win"):
            # Trên Windows: Phải có cơ chế verified process handle an toàn; nếu không có, fail closed
            if self._win_open_process is None:
                logger.warning("Không có cơ chế verified process handle an toàn trên Windows. Từ chối gửi tín hiệu (fail closed).")
                return False
            try:
                # Verified handle workflow trên Windows
                h_proc = self._win_open_process(0x0001 | 0x1000, False, pid)  # PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
                if not h_proc:
                    logger.warning("Không thể mở process handle trên Windows cho PID %s (fail closed).", pid)
                    return False
                try:
                    if expected_meta and not self.verify_process_identity(pid, expected_meta):
                        logger.warning("Danh tính tiến trình Windows PID %s không khớp sau khi mở handle. Fail closed.", pid)
                        return False
                finally:
                    if self._win_close_handle:
                        self._win_close_handle(h_proc)
            except Exception as exc:
                logger.warning("Lỗi xử lý verified handle trên Windows cho PID %s: %s", pid, exc)
                return False
            return False

        # Trên Linux / POSIX: Đóng cửa sổ PID reuse bằng pidfd + identity revalidation
        if not self._pidfd_open or not self._pidfd_send_signal or not self._close_fd:
            logger.warning("Hệ thống thiếu hỗ trợ pidfd_open / pidfd_send_signal an toàn. Từ chối gửi tín hiệu (fail closed).")
            return False

        # 1. Mở pidfd để giữ handle bất biến trỏ tới đúng tiến trình đó
        try:
            pidfd = self._pidfd_open(pid, 0)
        except Exception as exc:
            logger.warning("Không thể mở pidfd cho PID %s (tiến trình đã thoát hoặc không có quyền): %s", pid, exc)
            return False

        try:
            # 2. Trong khi đang nắm giữ pidfd, xác thực lại danh tính tiến trình (revalidate identity)
            if expected_meta:
                if not self.verify_process_identity(pid, expected_meta):
                    logger.warning("Danh tính PID %s không khớp sau khi có pidfd. Từ chối gửi tín hiệu (fail closed).", pid)
                    return False
            else:
                ident = self.get_process_identity(pid)
                if not ident:
                    logger.warning("Không thể đọc danh tính PID %s sau khi có pidfd. Từ chối gửi tín hiệu.", pid)
                    return False

            # 3. Gửi SIGTERM tới handle pidfd đã xác thực (tuyệt đối KHÔNG dùng os.kill(pid, ...) và KHÔNG gửi SIGKILL)
            sigterm_val = getattr(signal, "SIGTERM", 15)
            self._pidfd_send_signal(pidfd, sigterm_val)
        finally:
            try:
                self._close_fd(pidfd)
            except Exception:
                pass

        # 4. Chờ tiến trình kết thúc nhẹ nhàng trong timeout
        start_t = time.time()
        while time.time() - start_t < timeout:
            if not self.is_process_running(pid):
                return True
            time.sleep(0.1)

        logger.warning("Tiến trình PID %s không dừng sau %s giây gửi SIGTERM qua verified handle.", pid, timeout)
        return False

    def verify_installed_binary(self, bin_path: str) -> bool:
        """Xác thực tệp binary đã cài đặt là Ollama hợp lệ."""
        if not os.path.isfile(bin_path):
            return False
        if not sys.platform.startswith("win") and not os.access(bin_path, os.X_OK):
            return False
        try:
            res = subprocess.run(
                [bin_path, "--version"],
                env=clean_media_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=3.0,
                **no_window_kwargs() if sys.platform.startswith("win") else {}
            )
            return res.returncode == 0 and ("ollama" in res.stdout.lower() or "version" in res.stdout.lower())
        except Exception:
            return False

    def start_detached_installer(self, installer_path: str) -> bool:
        """Mở bộ cài đặt Windows độc lập."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(installer_path)  # type: ignore
                return True
            else:
                subprocess.Popen([installer_path], start_new_session=True)
                return True
        except Exception as exc:
            logger.error("Không thể mở bộ cài đặt: %s", exc)
            return False


class DefaultFileSystemAdapter(IFileSystemAdapter):
    """Adapter hệ thống tệp và đĩa có xác thực bảo mật trước khi giải nén."""

    def get_free_disk_space(self, path: str) -> int:
        target = path
        while target and not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        usage = shutil.disk_usage(target if os.path.exists(target) else "/")
        return usage.free

    def extract_archive(
        self,
        archive_path: str,
        dest_dir: str,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> bool:
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = Path(dest_dir).resolve()
        if archive_path.endswith(".tar.zst"):
            zstd_cmd = shutil.which("zstd")
            if not zstd_cmd:
                raise RuntimeError("Không tìm thấy lệnh 'zstd' trên hệ thống để giải nén tệp lưu trữ.")
            with subprocess.Popen(
                [zstd_cmd, "-dc", archive_path],
                stdout=subprocess.PIPE,
                env=clean_media_subprocess_env(),
            ) as proc:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                    extracted = _extract_ollama_tar(tar, dest_path, cancel_cb)
                if not extracted:
                    proc.terminate()
                    return False
                while proc.stdout.read(64 * 1024):
                    pass
                if proc.wait() != 0:
                    raise ValueError("Không thể giải nén toàn bộ gói Ollama.")
                return True
        elif archive_path.endswith(".tar") or archive_path.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:*") as tar:
                return _extract_ollama_tar(tar, dest_path, cancel_cb)
        return False


# ==============================================================================
# MAIN LOCAL AI SETUP SERVICE
# ==============================================================================

class LocalAISetupService:
    """
    Dịch vụ quản trị và cài đặt AI Cục Bộ (Whisper, Ollama daemon, Vision model).
    """

    def __init__(
        self,
        storage_root: Optional[str] = None,
        settings: Optional[QSettings] = None,
        http_client: Optional[IHttpClient] = None,
        process_runner: Optional[IProcessRunner] = None,
        fs_adapter: Optional[IFileSystemAdapter] = None,
        platform_info: Optional[Tuple[str, str]] = None,
    ):
        self.settings = settings if settings is not None else QSettings("ChunDVC", "ResolveFlow")
        self.http = http_client if http_client is not None else DefaultHttpClient()
        self.proc = process_runner if process_runner is not None else DefaultProcessRunner()
        self.fs = fs_adapter if fs_adapter is not None else DefaultFileSystemAdapter()

        # Nền tảng thực thi (os_name, arch)
        if platform_info:
            self.os_name, self.arch = platform_info
        else:
            sys_name = "win32" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")
            machine = platform.machine().lower()
            if machine in ("x86_64", "amd64"):
                norm_arch = "x86_64" if sys_name == "linux" else "AMD64"
            elif machine in ("aarch64", "arm64"):
                norm_arch = "aarch64"
            else:
                norm_arch = machine
            self.os_name, self.arch = sys_name, norm_arch

        # Thư mục gốc lưu trữ AI
        if storage_root:
            self._storage_root = os.path.abspath(storage_root)
        else:
            saved_root = str(self.settings.value("ai/storage_root", "") or "").strip()
            if saved_root and os.path.exists(saved_root):
                self._storage_root = os.path.abspath(saved_root)
            else:
                self._storage_root = self.get_default_storage_root()

        # Endpoint Ollama
        self.endpoint = str(self.settings.value("ai/vision_endpoint", DEFAULT_OLLAMA_ENDPOINT) or DEFAULT_OLLAMA_ENDPOINT)

        # Manifest tải xuống chính thức
        self.manifest = {
            ("linux", "x86_64"): {
                "filename": "ollama-linux-amd64.tar.zst",
                "sha256": OLLAMA_LINUX_AMD64_HASH,
                "url": f"https://github.com/ollama/ollama/releases/download/{OLLAMA_RELEASE_VERSION}/ollama-linux-amd64.tar.zst",
                "required_free_space": 5 * 1024 * 1024 * 1024,
                "installer_type": "archive",
            },
            ("linux", "aarch64"): {
                "filename": "ollama-linux-arm64.tar.zst",
                "sha256": OLLAMA_LINUX_ARM64_HASH,
                "url": f"https://github.com/ollama/ollama/releases/download/{OLLAMA_RELEASE_VERSION}/ollama-linux-arm64.tar.zst",
                "required_free_space": 5 * 1024 * 1024 * 1024,
                "installer_type": "archive",
            },
            ("win32", "AMD64"): {
                "filename": "OllamaSetup.exe",
                "sha256": OLLAMA_WINDOWS_AMD64_HASH,
                "url": f"https://github.com/ollama/ollama/releases/download/{OLLAMA_RELEASE_VERSION}/OllamaSetup.exe",
                "required_free_space": 4 * 1024 * 1024 * 1024,
                "installer_type": "official_installer_exe",
            },
            ("win32", "x86_64"): {
                "filename": "OllamaSetup.exe",
                "sha256": OLLAMA_WINDOWS_AMD64_HASH,
                "url": f"https://github.com/ollama/ollama/releases/download/{OLLAMA_RELEASE_VERSION}/OllamaSetup.exe",
                "required_free_space": 4 * 1024 * 1024 * 1024,
                "installer_type": "official_installer_exe",
            },
        }

    @staticmethod
    def get_default_storage_root() -> str:
        """Đường dẫn thư mục lưu trữ AI mặc định theo nền tảng người dùng."""
        if sys.platform.startswith("win"):
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local"))
            return os.path.join(base, "ChunDVC-AI")
        elif sys.platform == "darwin":
            return os.path.expanduser("~/Library/Application Support/ChunDVC-AI")
        else:
            base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            return os.path.join(base, "chundvc-ai")

    def get_storage_root(self) -> str:
        return self._storage_root

    def set_storage_root(self, path: str):
        """Thay đổi thư mục lưu trữ AI, giữ nguyên toàn bộ tệp cũ của người dùng và lưu vào QSettings."""
        norm_path = os.path.abspath(path.strip())
        self._storage_root = norm_path
        self.settings.setValue("ai/storage_root", norm_path)
        self.settings.sync()

    def check_storage_space(self, required_bytes: int = 5 * 1024 * 1024 * 1024) -> Tuple[bool, float, str]:
        """Kiểm tra dung lượng ổ đĩa khả dụng tại thư mục lưu trữ."""
        try:
            free_bytes = self.fs.get_free_disk_space(self._storage_root)
            free_gb = free_bytes / (1024 ** 3)
            req_gb = required_bytes / (1024 ** 3)
            if free_bytes >= required_bytes:
                return True, free_gb, f"Dung lượng trống khả dụng: {free_gb:.1f} GB (Đủ điều kiện)"
            else:
                return False, free_gb, f"Dung lượng trống không đủ: chỉ còn {free_gb:.1f} GB (cần ít nhất {req_gb:.1f} GB)."
        except Exception as exc:
            return False, 0.0, f"Không thể kiểm tra dung lượng ổ đĩa: {exc}"

    def get_daemon_meta_path(self) -> str:
        """Đường dẫn tệp metadata sở hữu daemon được ghi nguyên tử (atomic)."""
        return os.path.join(self._storage_root, "ollama_daemon.json")

    def get_pid_file_path(self) -> str:
        """Tương thích ngược tệp PID."""
        return os.path.join(self._storage_root, "ollama.pid")

    def _save_daemon_metadata(self, meta: Dict[str, Any]):
        """Ghi metadata sở hữu nguyên tử qua tệp tạm + atomic rename."""
        meta_path = self.get_daemon_meta_path()
        tmp_path = meta_path + f".tmp_{uuid.uuid4().hex}"
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp_path, meta_path)

        # Ghi tương thích ollama.pid
        pid_file = self.get_pid_file_path()
        pid_tmp = pid_file + f".tmp_{uuid.uuid4().hex}"
        with open(pid_tmp, "w", encoding="utf-8") as f:
            f.write(str(meta.get("pid", "")))
        os.replace(pid_tmp, pid_file)

    def _read_daemon_metadata(self) -> Optional[Dict[str, Any]]:
        """Đọc metadata sở hữu của daemon từ tệp json nguyên tử."""
        meta_path = self.get_daemon_meta_path()
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "pid" in data:
                        return data
            except Exception:
                pass
        return None

    def _remove_daemon_metadata(self):
        """Xóa metadata khi daemon đã dừng an toàn."""
        for p in [self.get_daemon_meta_path(), self.get_pid_file_path()]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def get_binary_path(self, check_system: bool = False) -> Optional[str]:
        """Tìm đường dẫn binary ollama ưu tiên trong storage_root, sau đó đến PATH nếu check_system=True."""
        candidates = [
            os.path.join(self._storage_root, "bin", "ollama"),
            os.path.join(self._storage_root, "bin", "ollama.exe"),
            os.path.join(self._storage_root, "ollama"),
            os.path.join(self._storage_root, "ollama.exe"),
        ]
        # Trên Windows: hỗ trợ kiểm tra thêm thư mục cài đặt tiêu chuẩn của OllamaSetup.exe nếu check_system=True
        if check_system and (sys.platform.startswith("win") or self.os_name == "win32"):
            local_app = os.environ.get("LOCALAPPDATA", "")
            if local_app:
                candidates.append(os.path.join(local_app, "Programs", "Ollama", "ollama.exe"))

        for c in candidates:
            if os.path.isfile(c):
                # Tuyệt đối không gọi os.chmod ở đây: tránh tác dụng phụ khi chỉ truy vấn đường dẫn
                return os.path.abspath(c)

        if check_system:
            sys_bin = shutil.which("ollama")
            if sys_bin:
                return sys_bin
        return None

    def check_ollama_status(self, probe_network: bool = True, check_system: bool = False) -> OllamaStatusResult:
        """
        Kiểm tra trạng thái Ollama:
        - Binary tồn tại?
        - Tiến trình đang chạy và được xác thực danh tính sở hữu?
        - API có phản hồi?
        """
        bin_path = self.get_binary_path(check_system=check_system)
        meta = self._read_daemon_metadata()
        owned_pid = None
        is_verified_owned = False

        if meta and "pid" in meta:
            p = meta["pid"]
            if self.proc.is_process_running(p):
                if self.proc.verify_process_identity(p, meta):
                    owned_pid = p
                    is_verified_owned = True

        is_responsive = False
        if probe_network:
            try:
                self.http.get_json(f"{self.endpoint}/api/tags", timeout=1.5)
                is_responsive = True
            except Exception:
                is_responsive = False

        if not bin_path and not is_responsive:
            key = (self.os_name, self.arch)
            if key not in self.manifest:
                return OllamaStatusResult(
                    install_status=OllamaInstallStatus.UNSUPPORTED,
                    is_running=False,
                    is_owned=False,
                    pid=None,
                    version="",
                    endpoint=self.endpoint,
                    details=f"Nền tảng {self.os_name} ({self.arch}) chưa hỗ trợ cài đặt tự động.",
                )
            return OllamaStatusResult(
                install_status=OllamaInstallStatus.NOT_INSTALLED,
                is_running=False,
                is_owned=False,
                pid=None,
                version="",
                endpoint=self.endpoint,
                details="Chưa cài đặt Ollama trên máy.",
            )

        if is_responsive:
            details = "Đang chạy (Do ứng dụng quản lý)" if is_verified_owned else "Đang chạy (Dịch vụ hệ thống hoặc người dùng)"
            return OllamaStatusResult(
                install_status=OllamaInstallStatus.RUNNING,
                is_running=True,
                is_owned=is_verified_owned,
                pid=owned_pid,
                version=OLLAMA_RELEASE_VERSION,
                endpoint=self.endpoint,
                details=details,
            )

        return OllamaStatusResult(
            install_status=OllamaInstallStatus.STOPPED,
            is_running=False,
            is_owned=False,
            pid=None,
            version=OLLAMA_RELEASE_VERSION,
            endpoint=self.endpoint,
            details=f"Đã cài đặt tại {bin_path} (Máy chủ chưa bật)",
        )

    def install_ollama(
        self,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, str]:
        """Tải và cài đặt Ollama binary độc lập, an toàn tệp người dùng và hỗ trợ riêng Windows."""
        key = (self.os_name, self.arch)
        if key not in self.manifest:
            return False, f"Nền tảng {self.os_name} ({self.arch}) chưa hỗ trợ cài đặt tự động. Vui lòng cài đặt thủ công."

        spec = self.manifest[key]
        req_space = spec.get("required_free_space", 5 * 1024 * 1024 * 1024)

        # 1. Kiểm tra dung lượng đĩa
        has_space, free_gb, space_msg = self.check_storage_space(req_space)
        if not has_space:
            return False, f"Không đủ dung lượng đĩa để cài đặt Ollama: {space_msg}"

        downloads = os.path.join(self._storage_root, "downloads")
        os.makedirs(downloads, exist_ok=True)
        dl_dir = tempfile.mkdtemp(prefix="ollama-", dir=downloads)
        archive_path = os.path.join(dl_dir, spec["filename"])

        # 2. Tải tệp lưu trữ kèm xác thực mã băm SHA256 trước khi đụng tới hệ thống
        if progress_cb:
            progress_cb(0.0, f"Đang tải {spec['filename']}...")

        def _on_dl_prog(dl_bytes, total_bytes):
            if progress_cb and total_bytes > 0:
                pct = (dl_bytes / total_bytes) * 70.0
                mb_dl = dl_bytes / (1024 * 1024)
                mb_tot = total_bytes / (1024 * 1024)
                progress_cb(pct, f"Đang tải: {mb_dl:.1f}/{mb_tot:.1f} MB ({pct:.0f}%)")

        try:
            dl_ok = self.http.download_file(
                url=spec["url"],
                target_path=archive_path,
                expected_sha256=spec["sha256"],
                progress_cb=_on_dl_prog,
                cancel_cb=cancel_cb,
            )
            if not dl_ok:
                return False, "Quá trình tải đã bị hủy bởi người dùng."
        except Exception as exc:
            return False, f"Lỗi tải gói cài đặt Ollama: {exc}"

        # 3. Xử lý riêng cho Windows: Không bao giờ coi .exe là tar archive, chuyển giao trình cài đặt chính thức trung thực
        if self.os_name == "win32":
            if progress_cb:
                progress_cb(100.0, "Đang mở trình cài đặt chính thức Ollama...")
            opened = self.proc.start_detached_installer(archive_path)
            if opened:
                return True, (
                    f"Đã mở bộ cài đặt chính thức Ollama ({os.path.basename(archive_path)}). "
                    "Vui lòng hoàn tất quá trình cài đặt theo hướng dẫn trên Windows rồi quay lại bấm 'Khởi động'."
                )
            return False, f"Không thể tự động mở bộ cài đặt {archive_path}. Vui lòng chạy tệp thủ công."

        # 4. Linux: Kiểm tra thư mục đích trước khi giải nén
        bin_target = os.path.join(self._storage_root, "bin")
        lib_target = os.path.join(self._storage_root, "lib")
        if os.path.exists(bin_target) and not os.path.isdir(bin_target):
            return False, f"Đường dẫn đích {bin_target} bị chiếm bởi tệp không phải thư mục."
        if os.path.exists(lib_target) and not os.path.isdir(lib_target):
            return False, f"Đường dẫn đích {lib_target} bị chiếm bởi tệp không phải thư mục."

        # 5. Linux: Trích xuất giải nén an toàn qua staging duy nhất (private unique staging)
        if progress_cb:
            progress_cb(75.0, "Đang tiền xác thực và giải nén tệp thực thi...")

        staging_dir = os.path.join(self._storage_root, "tmp", f"staging_{uuid.uuid4().hex}")
        os.makedirs(staging_dir, mode=0o700, exist_ok=True)

        try:
            extract_ok = self.fs.extract_archive(
                archive_path=archive_path,
                dest_dir=staging_dir,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
            if not extract_ok:
                shutil.rmtree(staging_dir, ignore_errors=True)
                return False, "Giải nén thất bại hoặc bị hủy."
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            return False, f"Lỗi tiền xác thực an toàn gói Ollama: {exc}"

        # 6. Preflight kiểm tra xung đột: Tuyệt đối không xóa đè tệp người dùng, tiếp nhận tích cực bản khớp hợp lệ
        stg_bin = os.path.join(staging_dir, "bin")
        stg_lib = os.path.join(staging_dir, "lib")

        for stg_subdir, target_subdir in [(stg_bin, bin_target), (stg_lib, lib_target)]:
            if os.path.exists(stg_subdir):
                for root, dirs, files in os.walk(stg_subdir):
                    rel = os.path.relpath(root, stg_subdir)
                    tdir = target_subdir if rel == "." else os.path.join(target_subdir, rel)
                    if rel != "." and os.path.exists(tdir) and not os.path.isdir(tdir):
                        shutil.rmtree(staging_dir, ignore_errors=True)
                        return False, f"Thư mục đích chứa tệp xung đột: '{tdir}'"
                    for fname in files:
                        dest_file = os.path.join(tdir, fname)
                        if os.path.exists(dest_file):
                            if fname in ("ollama", "ollama.exe") and self.proc.verify_installed_binary(dest_file):
                                shutil.rmtree(staging_dir, ignore_errors=True)
                                return True, f"Đã tiếp nhận bản cài đặt Ollama hợp lệ có sẵn tại {dest_file}."
                            shutil.rmtree(staging_dir, ignore_errors=True)
                            return False, (
                                f"Thư mục đích chứa tệp xung đột đã tồn tại: '{dest_file}'. "
                                "Từ chối ghi đè để bảo vệ tệp của người dùng."
                            )

        # 7. Xuất bản (Publish) an toàn vì đã xác nhận 100% không có xung đột
        os.makedirs(bin_target, exist_ok=True)
        os.makedirs(lib_target, exist_ok=True)

        if os.path.exists(stg_bin):
            for item in os.listdir(stg_bin):
                src_item = os.path.join(stg_bin, item)
                dst_item = os.path.join(bin_target, item)
                shutil.move(src_item, dst_item)
                if not sys.platform.startswith("win"):
                    try:
                        os.chmod(dst_item, 0o755)
                    except Exception:
                        pass

        if os.path.exists(stg_lib):
            for item in os.listdir(stg_lib):
                src_item = os.path.join(stg_lib, item)
                dst_item = os.path.join(lib_target, item)
                shutil.move(src_item, dst_item)

        # Dọn dẹp staging an toàn
        shutil.rmtree(staging_dir, ignore_errors=True)

        if progress_cb:
            progress_cb(100.0, "Cài đặt Ollama thành công.")
        return True, "Cài đặt hoàn tất."

    def safe_extract_tar(self, archive_path: str, dest_dir: str):
        """Trích xuất tar an toàn, tiền xác thực toàn bộ member qua validate_tar_members."""
        os.makedirs(dest_dir, exist_ok=True)
        with tarfile.open(archive_path, "r:*") as tar:
            valid_members = validate_tar_members(tar, dest_dir)
            tar.extractall(dest_dir, members=valid_members)

    def start_ollama_daemon(self, timeout: float = 10.0) -> Tuple[bool, str]:
        """
        Khởi động máy chủ Ollama độc lập trong nền.
        Độ sẵn sàng trung thực: chỉ báo thành công khi loopback API phản hồi trong timeout
        VÀ tiến trình vừa khởi chạy vẫn đang chạy và xác thực được danh tính.
        Nếu không phản hồi hoặc tiến trình chết, dọn dẹp chỉ tiến trình vừa tạo và báo lỗi.
        """
        bin_path = self.get_binary_path()
        if not bin_path:
            return False, "Không tìm thấy tệp thực thi ollama. Vui lòng cài đặt trước."

        # Kiểm tra xem daemon do mình sở hữu đã chạy và phản hồi chưa
        meta = self._read_daemon_metadata()
        if meta and isinstance(meta, dict) and "pid" in meta:
            p = meta["pid"]
            if self.proc.is_process_running(p) and self.proc.verify_process_identity(p, meta):
                try:
                    self.http.get_json(f"{self.endpoint}/api/tags", timeout=1.0)
                    return True, "Máy chủ Ollama đã đang hoạt động."
                except Exception:
                    pass

        # Thiết lập môi trường sạch sẽ không ô nhiễm thư viện Resolve/PyInstaller
        env = clean_media_subprocess_env()
        models_dir = os.path.join(self._storage_root, "models")
        os.makedirs(models_dir, exist_ok=True)

        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["OLLAMA_MODELS"] = models_dir
        env["OLLAMA_NO_CLOUD"] = "1"
        env["OLLAMA_CONTEXT_LENGTH"] = "4096"
        env["OLLAMA_NUM_PARALLEL"] = "1"
        env["OLLAMA_MAX_LOADED_MODELS"] = "1"
        env["OLLAMA_KEEP_ALIVE"] = "2m"

        cmd = [bin_path, "serve"]
        try:
            pid, process_meta = self.proc.start_process(cmd, env=env, cwd=self._storage_root)
            self._save_daemon_metadata(process_meta)
        except Exception as exc:
            return False, f"Không thể khởi chạy tiến trình Ollama: {exc}"

        # Đợi máy chủ phản hồi trên cổng loopback trong khoảng thời gian timeout
        start_t = time.time()
        api_ready = False
        while time.time() - start_t < timeout:
            if not self.proc.is_process_running(pid):
                # Tiến trình vừa tạo đã thoát hoặc sập sớm
                break
            try:
                self.http.get_json(f"{self.endpoint}/api/tags", timeout=0.8)
                api_ready = True
                break
            except Exception:
                time.sleep(0.4)

        # Độ sẵn sàng trung thực: API loopback phải sẵn sàng VÀ tiến trình của chính mình phải đang chạy & khớp danh tính
        if api_ready and self.proc.is_process_running(pid) and self.proc.verify_process_identity(pid, process_meta):
            return True, "Khởi động máy chủ Ollama thành công."

        # KHÔNG BÁO THÀNH CÔNG GIẢ TẠO: Dọn dẹp chỉ tiến trình vừa tạo nếu xác thực được danh tính
        logger.warning("Máy chủ Ollama (PID %s) không sẵn sàng hoặc không phản hồi tại %s sau %ss.", pid, self.endpoint, timeout)
        if self.proc.is_process_running(pid):
            if self.proc.verify_process_identity(pid, process_meta):
                self.proc.terminate_graceful(pid, timeout=3.0, expected_meta=process_meta)
                if not self.proc.is_process_running(pid):
                    self._remove_daemon_metadata()
            # Nếu không xác thực hoặc dừng không kịp, giữ nguyên metadata để bảo vệ
        else:
            # Tiến trình đã tự thoát
            self._remove_daemon_metadata()

        return False, f"Máy chủ Ollama đã khởi chạy (PID {pid}) nhưng không phản hồi tại {self.endpoint} sau {timeout:.0f} giây."

    def stop_ollama_daemon(self, timeout: float = 5.0) -> Tuple[bool, str]:
        """
        Dừng Ollama daemon an toàn xác thực dương tính (positive verification).
        - Tuyệt đối không gửi SIGKILL hay taskkill /F.
        - Không bao giờ gửi tín hiệu tới tiến trình chưa qua xác thực danh tính.
        - Nếu dừng nhẹ nhàng không kịp thời hạn, giữ nguyên metadata và báo lỗi trung thực.
        """
        meta = self._read_daemon_metadata()
        if not meta or not isinstance(meta, dict) or "pid" not in meta:
            return False, "Không tìm thấy thông tin quản lý daemon của ứng dụng. Không dừng tiến trình hệ thống/ngoài."

        pid = meta["pid"]
        if not self.proc.is_process_running(pid):
            self._remove_daemon_metadata()
            return True, "Tiến trình đã dừng trước đó."

        # XÁC THỰC DƯƠNG TÍNH DANH TÍNH TIẾN TRÌNH (UID, Executable, Command, Start-time)
        if not self.proc.verify_process_identity(pid, meta):
            return False, (
                f"Từ chối dừng tiến trình PID {pid}: danh tính không khớp với phiên khởi chạy "
                "(phát hiện nguy cơ PID-reuse hoặc tiến trình khác)."
            )

        # Dừng nhẹ nhàng graceful
        stopped = self.proc.terminate_graceful(pid, timeout=timeout, expected_meta=meta)
        if stopped:
            self._remove_daemon_metadata()
            return True, "Đã dừng máy chủ Ollama."

        # Giữ nguyên metadata nếu không dừng được, KHÔNG force-kill
        return False, f"Tiến trình PID {pid} không dừng lại trong thời gian cho phép ({timeout}s). Giữ nguyên metadata sở hữu để đảm bảo an toàn."

    def list_installed_models(self) -> List[Dict]:
        """Lấy danh sách các mô hình Ollama đã cài trên máy."""
        try:
            data = self.http.get_json(f"{self.endpoint}/api/tags", timeout=2.0)
            return data.get("models", [])
        except Exception as exc:
            logger.debug("Không thể lấy danh sách mô hình từ Ollama: %s", exc)
            return []

    def check_vision_model_status(self, model: str = DEFAULT_VISION_MODEL) -> VisionModelStatusResult:
        """Kiểm tra trạng thái mô hình thị giác: Chưa tải, Đã tải hay Sẵn sàng (đã xác thực)."""
        installed = self.list_installed_models()
        wanted = model if ":" in model else f"{model}:latest"
        found = None
        for m in installed:
            name = m.get("name", "")
            if name == model or name == wanted:
                found = m
                break

        if not found:
            return VisionModelStatusResult(
                model=model,
                status=VisionModelStatus.NOT_DOWNLOADED,
                size_mb=0.0,
                digest="",
                is_verified=False,
                details="Chưa tải về máy.",
            )

        size_mb = float(found.get("size", 0)) / (1024 * 1024)
        digest = found.get("digest", "")

        verified_digest = str(self.settings.value(f"ai/verified_models/{model}", "") or "")
        is_verified = (len(digest) > 0 and digest == verified_digest)

        status = VisionModelStatus.READY if is_verified else VisionModelStatus.DOWNLOADED
        details = "Đã xác thực và sẵn sàng sử dụng" if is_verified else "Đã tải (Cần kiểm tra xác thực trước khi chạy)"

        return VisionModelStatusResult(
            model=model,
            status=status,
            size_mb=size_mb,
            digest=digest,
            is_verified=is_verified,
            details=details,
        )

    def pull_vision_model(
        self,
        model: str = DEFAULT_VISION_MODEL,
        progress_cb: Optional[Callable[[Optional[float], str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, str]:
        """Tải mô hình thị giác qua Ollama streaming API với tiến trình thời gian thực."""
        payload = {"model": model, "stream": True}
        url = f"{self.endpoint}/api/pull"
        completed_successfully = False

        def _chunk_handler(chunk: Dict):
            nonlocal completed_successfully
            if "error" in chunk:
                raise ValueError(str(chunk["error"]))

            status_txt = chunk.get("status", "")
            if status_txt == "success":
                completed_successfully = True
            completed = chunk.get("completed")
            total = chunk.get("total")

            if completed is not None and total is not None and total > 0:
                pct = (completed / total) * 100.0
                mb_c = completed / (1024 * 1024)
                mb_t = total / (1024 * 1024)
                msg = f"{status_txt}: {mb_c:.1f}/{mb_t:.1f} MB ({pct:.0f}%)"
                if progress_cb:
                    progress_cb(pct, msg)
            else:
                if progress_cb:
                    progress_cb(None, status_txt)

        try:
            ok = self.http.post_json_streaming(
                url=url,
                payload=payload,
                chunk_cb=_chunk_handler,
                cancel_cb=cancel_cb,
                timeout=300.0,
            )
            if not ok:
                return False, "Quá trình tải mô hình đã bị hủy."
            if not completed_successfully:
                return False, "Kết nối Ollama kết thúc trước khi tải mô hình hoàn tất."
            return True, f"Tải mô hình '{model}' thành công."
        except Exception as exc:
            return False, f"Lỗi tải mô hình thị giác: {exc}"

    def _find_model_digest(self, tags_data: Dict[str, Any], model: str) -> Optional[str]:
        """Tìm digest của model trong danh sách phản hồi /api/tags theo tên chuẩn hóa."""
        if not tags_data or not isinstance(tags_data, dict):
            return None
        models_list = tags_data.get("models", [])
        if not isinstance(models_list, list):
            return None

        wanted = model if ":" in model else f"{model}:latest"
        base_name = model.split(":")[0]

        for m in models_list:
            if not isinstance(m, dict):
                continue
            name = m.get("name", "")
            if name == model or name == wanted:
                digest = m.get("digest", "")
                if digest:
                    return str(digest).strip()
            if name.split(":")[0] == base_name and (name == model or name == wanted):
                digest = m.get("digest", "")
                if digest:
                    return str(digest).strip()
        return None

    def verify_vision_model(self, model: str = DEFAULT_VISION_MODEL) -> Tuple[bool, str]:
        """
        Xác thực khả năng thị giác và chạy thử nghiệm (smoke test) mô hình cục bộ:
        - Lấy digest từ /api/tags trước và sau khi thử nghiệm (Ollama ShowResponse không có top-level digest).
        - Kiểm tra capability 'vision' từ /api/show.
        - Chạy smoke test thực tế với ảnh 1x1 JPEG base64 và thời gian timeout 120s (cold load).
        - Yêu cầu phản hồi thành công thực sự (done=True, response không rỗng, không có lỗi).
        - Từ chối cấp quyền sẵn sàng nếu digest bị thiếu hoặc thay đổi.
        """
        # 1. Lấy digest từ /api/tags TRƯỚC khi thử nghiệm
        try:
            tags_before = self.http.get_json(f"{self.endpoint}/api/tags", timeout=5.0)
        except Exception as exc:
            return False, f"Không thể lấy danh sách mô hình từ Ollama (/api/tags): {exc}"

        digest_before = self._find_model_digest(tags_before, model)
        if not digest_before:
            return False, f"Mô hình '{model}' chưa được cài đặt hoặc thiếu digest trong /api/tags."

        # 2. Kiểm tra capability 'vision' từ /api/show (Ollama ShowResponse chính thức v0.40.16)
        try:
            info = self.http.post_json(f"{self.endpoint}/api/show", {"model": model}, timeout=10.0)
        except Exception as exc:
            return False, f"Không thể lấy thông tin mô hình từ Ollama (/api/show): {exc}"

        capabilities = info.get("capabilities", [])
        if "vision" not in capabilities:
            return False, f"Mô hình '{model}' không hỗ trợ khả năng phân tích hình ảnh (vision)."

        # 3. Chạy smoke test thực tế với ảnh 1x1 JPEG base64 hợp lệ và timeout 120s
        tiny_1x1_jpeg_b64 = (
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
            "wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
        )

        smoke_payload = {
            "model": model,
            "prompt": "describe",
            "images": [tiny_1x1_jpeg_b64],
            "stream": False,
            "keep_alive": "2m",
            "options": {"num_predict": 10},
        }

        try:
            gen_resp = self.http.post_json(f"{self.endpoint}/api/generate", smoke_payload, timeout=120.0)
        except Exception as exc:
            return False, f"Lỗi khi gửi yêu cầu thử nghiệm tới mô hình: {exc}"

        # Kiểm tra phản hồi thành công thực sự (genuine successful response)
        if not gen_resp or not isinstance(gen_resp, dict):
            return False, "Smoke test mô hình thất bại: phản hồi không hợp lệ."
        if "error" in gen_resp:
            err = gen_resp.get("error", "Lỗi không xác định")
            return False, f"Smoke test mô hình thất bại: {err}"
        if not gen_resp.get("done", False):
            return False, "Smoke test mô hình thất bại: suy luận chưa hoàn thành (done=False)."
        resp_text = str(gen_resp.get("response", "")).strip()
        if not resp_text:
            return False, "Smoke test mô hình thất bại: phản hồi trả về rỗng."

        # 4. Lấy digest từ /api/tags SAU khi thử nghiệm để kiểm tra tính toàn vẹn
        try:
            tags_after = self.http.get_json(f"{self.endpoint}/api/tags", timeout=5.0)
        except Exception as exc:
            return False, f"Không thể lấy lại danh sách mô hình từ Ollama (/api/tags) sau thử nghiệm: {exc}"

        digest_after = self._find_model_digest(tags_after, model)
        if not digest_after:
            return False, f"Mô hình '{model}' không còn tồn tại trong /api/tags sau thử nghiệm."

        if digest_after != digest_before:
            return False, (
                f"Digest của mô hình '{model}' bị thay đổi trong quá trình thử nghiệm "
                f"(trước: {digest_before}, sau: {digest_after})."
            )

        # 5. Lưu trạng thái sẵn sàng cho đúng digest đã xác thực
        self.settings.setValue(f"ai/verified_models/{model}", digest_after)
        self.settings.sync()

        return True, f"Mô hình '{model}' đã qua kiểm tra và sẵn sàng hoạt động!"

    def check_whisper_status(self, model_size: str = "small") -> Tuple[WhisperModelStatus, str]:
        """Kiểm tra mô hình Whisper đã có sẵn trong bộ nhớ đệm hay chưa."""
        try:
            from src.core.transcriber import is_model_on_disk
            on_disk = is_model_on_disk(model_size)
            if on_disk:
                return WhisperModelStatus.DOWNLOADED, "Đã có sẵn trên máy (HuggingFace Hub cache)"
            return WhisperModelStatus.NOT_DOWNLOADED, "Chưa tải về máy"
        except Exception as exc:
            return WhisperModelStatus.NOT_DOWNLOADED, f"Không xác định: {exc}"
