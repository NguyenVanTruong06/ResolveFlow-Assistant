"""
tests/services/test_local_ai_setup_service.py
Kiểm thử dịch vụ cấu hình và cài đặt Local AI (Whisper, Ollama daemon, vision model):
- Kiểm tra dung lượng đĩa và đường dẫn lưu trữ tùy biến
- Nhận diện nền tảng (Linux x86_64, Windows, unsupported)
- Xác thực checksum SHA256 và từ chối tệp tải hỏng/sai mã hash
- Phòng chống tấn công Path Traversal khi giải nén tệp lưu trữ
- Nhận diện và tiếp nhận thư mục cài đặt hiện có mà không xóa tệp người dùng
- Quản lý vòng đời Ollama daemon (start, stop, kiểm tra PID sở hữu)
- Tải mô hình thị giác qua loopback streaming API với tiến trình thực
- Xác thực mô hình thị giác (chỉ cấp trạng thái 'Sẵn sàng' khi kiểm tra thành công)
"""

import os
import sys
import io
import json
import tarfile
import hashlib
import tempfile
import pytest
from unittest.mock import MagicMock
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable, Any

from PySide6.QtCore import QSettings

from src.services.local_ai_setup_service import (
    LocalAISetupService,
    OllamaInstallStatus,
    VisionModelStatus,
    OLLAMA_LINUX_AMD64_HASH,
    DEFAULT_VISION_MODEL,
    IHttpClient,
    IProcessRunner,
    IFileSystemAdapter,
    DefaultFileSystemAdapter,
    DefaultHttpClient,
    validate_tar_members,
)


class FakeHttpClient(IHttpClient):
    """Giả lập HTTP client phục vụ kiểm thử đơn vị độc lập không qua mạng."""
    def __init__(self):
        self.download_data: bytes = b"dummy content"
        self.download_fail: bool = False
        self.tags_response: Dict = {"models": []}
        # Phản hồi show thực tế: KHÔNG có top-level digest
        self.show_response: Dict = {"capabilities": ["vision"], "details": {"format": "gguf"}}
        self.generate_response: Dict = {"response": "Image shows a clean setup", "done": True}
        self.pull_events: List[Dict] = []
        self.download_calls = []
        self.pull_calls = []
        self.generate_calls = []

    def download_file(
        self,
        url: str,
        target_path: str,
        expected_sha256: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> bool:
        self.download_calls.append(url)
        if self.download_fail:
            raise ConnectionError("Không thể kết nối đến máy chủ tải")
        if cancel_cb and cancel_cb():
            return False

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(self.download_data)

        if expected_sha256:
            hasher = hashlib.sha256()
            hasher.update(self.download_data)
            actual_sha = hasher.hexdigest()
            if actual_sha.lower() != expected_sha256.lower():
                raise ValueError(f"Checksum mismatch: expected {expected_sha256}, got {actual_sha}")

        if progress_cb:
            progress_cb(len(self.download_data), len(self.download_data))
        return True

    def get_json(self, url: str, timeout: float = 5.0) -> Dict:
        if "/api/tags" in url:
            return self.tags_response
        raise ConnectionError(f"404 Not Found: {url}")

    def post_json(self, url: str, payload: Dict, timeout: float = 10.0) -> Dict:
        if "/api/show" in url:
            return self.show_response
        elif "/api/generate" in url:
            self.generate_calls.append((url, payload, timeout))
            return self.generate_response
        return {}

    def post_json_streaming(
        self,
        url: str,
        payload: Dict,
        chunk_cb: Callable[[Dict], None],
        cancel_cb: Optional[Callable[[], bool]] = None,
        timeout: float = 60.0,
    ) -> bool:
        self.pull_calls.append((url, payload))
        if self.download_fail:
            raise ConnectionError("Lỗi kết nối khi tải model")
        for ev in self.pull_events:
            if cancel_cb and cancel_cb():
                return False
            chunk_cb(ev)
        return True


class FakeProcessRunner(IProcessRunner):
    """Giả lập Process Runner cho các lệnh subprocess của daemon với xác thực danh tính."""
    def __init__(self):
        self.running_pids: set = set()
        self.started_commands: List = []
        self.terminated_pids: List[int] = []
        self.detached_installers: List[str] = []
        self.identities: Dict[int, Dict[str, Any]] = {}
        self.next_pid = 9901
        self.graceful_fail = False

    def start_process(self, cmd: List[str], env: Dict[str, str], cwd: str) -> Tuple[int, Dict[str, Any]]:
        pid = self.next_pid
        self.next_pid += 1
        self.running_pids.add(pid)
        self.started_commands.append((cmd, env, cwd, pid))
        meta = {
            "pid": pid,
            "uid": 1000,
            "executable": cmd[0],
            "cmd": cmd,
            "start_time": 1234567,
        }
        self.identities[pid] = meta
        return pid, meta

    def is_process_running(self, pid: int) -> bool:
        return pid in self.running_pids

    def get_process_identity(self, pid: int) -> Optional[Dict[str, Any]]:
        if pid in self.running_pids:
            return self.identities.get(pid, {
                "pid": pid,
                "uid": 1000,
                "executable": "/fake/ollama",
                "cmd": ["ollama", "serve"],
                "start_time": 1234567,
            })
        return None

    def verify_process_identity(self, pid: int, expected_meta: Dict[str, Any]) -> bool:
        if pid not in self.running_pids:
            return False
        if not expected_meta or not isinstance(expected_meta, dict):
            return False
        exp_exe = os.path.realpath(str(expected_meta.get("executable", "")).strip())
        exp_cmd = expected_meta.get("cmd", [])
        if not exp_exe or not exp_cmd:
            return False
        current = self.get_process_identity(pid)
        if not current:
            return False
        cur_exe = os.path.realpath(str(current.get("executable", "")).strip())
        if exp_exe != cur_exe:
            return False
        cur_cmd = current.get("cmd", [])
        if not any(str(arg).strip().lower() == "serve" for arg in cur_cmd):
            return False
        for key in ("uid", "start_time"):
            if key not in expected_meta or key not in current or expected_meta[key] != current[key]:
                return False
        return True

    def terminate_graceful(self, pid: int, timeout: float = 5.0, expected_meta: Optional[Dict[str, Any]] = None) -> bool:
        if self.graceful_fail:
            return False
        if pid in self.running_pids:
            self.running_pids.remove(pid)
            self.terminated_pids.append(pid)
            return True
        return False

    def verify_installed_binary(self, bin_path: str) -> bool:
        if not os.path.isfile(bin_path):
            return False
        try:
            with open(bin_path, "r", errors="ignore") as f:
                content = f.read()
            return "ollama" in content or "exit 0" in content or "binary" in content
        except Exception:
            return False

    def start_detached_installer(self, installer_path: str) -> bool:
        self.detached_installers.append(installer_path)
        return True


class FakeFileSystemAdapter(IFileSystemAdapter):
    """Giả lập Adapter đĩa và hệ thống tệp."""
    def __init__(self, free_bytes: int = 20 * 1024 * 1024 * 1024):
        self.free_bytes = free_bytes
        self.extracted_archives = []

    def get_free_disk_space(self, path: str) -> int:
        return self.free_bytes

    def extract_archive(self, archive_path: str, dest_dir: str, progress_cb=None, cancel_cb=None) -> bool:
        self.extracted_archives.append((archive_path, dest_dir))
        # Tạo sẵn binary giả lập trong thư mục bin sau giải nén
        bin_dir = os.path.join(dest_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        ollama_bin = os.path.join(bin_dir, "ollama")
        with open(ollama_bin, "w") as f:
            f.write("#!/bin/sh\necho ollama\n")
        os.chmod(ollama_bin, 0o755)
        return True


@pytest.fixture
def isolated_service(tmp_path):
    """Fixture cung cấp LocalAISetupService độc lập với fake adapters và settings riêng."""
    settings = QSettings(str(tmp_path / "test_settings.ini"), QSettings.Format.IniFormat)
    storage_root = str(tmp_path / "ai_root")

    http = FakeHttpClient()
    proc = FakeProcessRunner()
    fs = FakeFileSystemAdapter()

    service = LocalAISetupService(
        storage_root=storage_root,
        settings=settings,
        http_client=http,
        process_runner=proc,
        fs_adapter=fs,
        platform_info=("linux", "x86_64"),
    )
    return service, http, proc, fs, tmp_path


def test_service_initial_state_no_auto_network(isolated_service):
    """Mở service/dialog không được tự ý thực hiện cuộc gọi mạng hoặc tải dữ liệu."""
    service, http, proc, fs, tmp_path = isolated_service

    assert len(http.download_calls) == 0
    assert len(http.pull_calls) == 0
    assert len(proc.started_commands) == 0

    status = service.check_ollama_status(probe_network=False)
    assert status.install_status == OllamaInstallStatus.NOT_INSTALLED
    assert status.is_running is False
    assert status.is_owned is False


def test_service_storage_space_checks(isolated_service):
    """Kiểm tra dung lượng đĩa: thành công khi đủ, cảnh báo giải thích rõ khi thiếu."""
    service, http, proc, fs, tmp_path = isolated_service

    # Đủ dung lượng (> 10 GB)
    fs.free_bytes = 15 * 1024 * 1024 * 1024
    ok, free_gb, msg = service.check_storage_space(required_bytes=5 * 1024 * 1024 * 1024)
    assert ok is True
    assert free_gb >= 15.0

    # Thiếu dung lượng (< 2 GB)
    fs.free_bytes = 1 * 1024 * 1024 * 1024
    ok, free_gb, msg = service.check_storage_space(required_bytes=5 * 1024 * 1024 * 1024)
    assert ok is False
    assert "Dung lượng trống không đủ" in msg or "cần ít nhất" in msg


def test_service_adopts_existing_root_without_destroying_files(isolated_service):
    """Tiếp nhận thư mục cài đặt hiện có mà không xóa tệp hoặc mô hình của người dùng."""
    service, http, proc, fs, tmp_path = isolated_service

    existing_root = tmp_path / "existing_ai"
    bin_dir = existing_root / "bin"
    models_dir = existing_root / "models"
    bin_dir.mkdir(parents=True)
    models_dir.mkdir(parents=True)

    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    user_file = models_dir / "important_model.bin"
    user_file.write_text("precious user data")

    service.set_storage_root(str(existing_root))
    assert service.get_storage_root() == str(existing_root)

    # Đảm bảo tệp người dùng còn nguyên vẹn
    assert user_file.exists()
    assert user_file.read_text() == "precious user data"

    # Kiểm tra trạng thái nhận diện ngay là INSTALLED
    status = service.check_ollama_status(probe_network=False)
    assert status.install_status in (OllamaInstallStatus.INSTALLED, OllamaInstallStatus.STOPPED)


def test_service_install_ollama_checksum_verification(isolated_service):
    """Xác thực mã băm SHA256 trước khi giải nén; từ chối khi mã hash không khớp."""
    service, http, proc, fs, tmp_path = isolated_service

    # Chuẩn bị dữ liệu tải có mã băm hợp lệ
    dummy_archive = b"VALID_OLLAMA_TAR_ZST_PAYLOAD"
    hasher = hashlib.sha256()
    hasher.update(dummy_archive)
    valid_hash = hasher.hexdigest()

    http.download_data = dummy_archive

    # Patch manifest hash tạm thời để kiểm thử mã hash khớp
    service.manifest[("linux", "x86_64")]["sha256"] = valid_hash

    ok, msg = service.install_ollama()
    assert ok is True
    assert len(fs.extracted_archives) == 1

    # Trường hợp mã hash không khớp
    service.manifest[("linux", "x86_64")]["sha256"] = "wrong_hash_00000000000000000000000000000000"
    ok, msg = service.install_ollama()
    assert ok is False
    assert "Checksum mismatch" in msg or "mã băm" in msg.lower()


def test_service_path_traversal_protection(isolated_service):
    """Kiểm tra bảo vệ an toàn: từ chối tệp giải nén chứa member nhảy ra ngoài đích (../)."""
    service, http, proc, fs, tmp_path = isolated_service

    # Tạo tệp tar có chứa path traversal "../evil.sh"
    archive_path = tmp_path / "malicious.tar"
    with tarfile.open(archive_path, "w") as tar:
        ti = tarfile.TarInfo(name="../evil.sh")
        content = b"echo hacked"
        ti.size = len(content)
        tar.addfile(ti, io.BytesIO(content))

    dest = tmp_path / "safe_dest"
    dest.mkdir()

    # Hàm trích xuất an toàn tích hợp trong service phải ném lỗi hoặc từ chối giải nén
    with pytest.raises((ValueError, PermissionError)):
        service.safe_extract_tar(str(archive_path), str(dest))


def test_service_daemon_start_and_ownership(isolated_service):
    """Khởi động daemon: ghi PID, chỉ cho phép dừng daemon do ứng dụng sở hữu."""
    service, http, proc, fs, tmp_path = isolated_service

    # Cài đặt sẵn fake binary
    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("binary")
    fake_bin.chmod(0o755)

    # Giả lập khi start thì /api/tags phản hồi OK
    http.tags_response = {"models": []}

    ok, msg = service.start_ollama_daemon()
    assert ok is True

    status = service.check_ollama_status(probe_network=True)
    assert status.is_running is True
    assert status.is_owned is True
    assert status.pid is not None

    # Dừng daemon do mình sở hữu: thành công
    stop_ok, stop_msg = service.stop_ollama_daemon()
    assert stop_ok is True
    assert status.pid in proc.terminated_pids

    # Trường hợp daemon của hệ thống (không có PID file do mình quản lý)
    pid_file = Path(service.get_storage_root()) / "ollama.pid"
    if pid_file.exists():
        pid_file.unlink()

    # Daemon vẫn đang chạy trên mạng nhưng không do app quản lý
    proc.running_pids.add(1111)  # external pid
    status_ext = service.check_ollama_status(probe_network=True)
    assert status_ext.is_running is True
    assert status_ext.is_owned is False

    # Dừng daemon ngoài -> từ chối kill tiến trình của người dùng/hệ thống
    stop_ext_ok, stop_ext_msg = service.stop_ollama_daemon()
    assert stop_ext_ok is False
    assert "ngoài" in stop_ext_msg or "hệ thống" in stop_ext_msg


def test_service_pull_vision_model_streaming_progress(isolated_service):
    """Tải model thị giác streaming: cập nhật % tiến trình thực và kết thúc thành công."""
    service, http, proc, fs, tmp_path = isolated_service

    http.pull_events = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 500, "total": 1000},
        {"status": "downloading", "completed": 1000, "total": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    ]

    progress_reports = []

    def on_prog(pct, msg):
        progress_reports.append((pct, msg))

    ok, msg = service.pull_vision_model(DEFAULT_VISION_MODEL, progress_cb=on_prog)
    assert ok is True
    assert len(progress_reports) >= 3
    # Báo cáo tiến trình chứa giá trị 50% và 100%
    percentages = [p[0] for p in progress_reports if p[0] is not None]
    assert 50.0 in percentages or 100.0 in percentages


def test_service_verify_vision_model_readiness(isolated_service):
    """Xác thực model thị giác: 'Đã tải' sau khi pull, chỉ 'Sẵn sàng' khi qua bước verify."""
    service, http, proc, fs, tmp_path = isolated_service

    # Model chưa tải
    http.tags_response = {"models": []}
    st1 = service.check_vision_model_status(DEFAULT_VISION_MODEL)
    assert st1.status == VisionModelStatus.NOT_DOWNLOADED

    # Model đã có trong /api/tags nhưng chưa verify
    http.tags_response = {"models": [{"name": DEFAULT_VISION_MODEL, "size": 1900000000, "digest": "sha256:abc"}]}
    st2 = service.check_vision_model_status(DEFAULT_VISION_MODEL)
    # Chưa xác thực -> chỉ được phép là DOWNLOADED ("Đã tải"), tuyệt đối KHÔNG phải READY ("Sẵn sàng")
    assert st2.status == VisionModelStatus.DOWNLOADED

    # Thực hiện verify thành công (show response thực tế không có digest, có capability vision và smoke test OK)
    http.show_response = {"capabilities": ["vision"]}
    http.generate_response = {"response": "A crisp video thumbnail", "done": True}

    v_ok, v_msg = service.verify_vision_model(DEFAULT_VISION_MODEL)
    assert v_ok is True
    # Kiểm tra smoke test đã gửi ảnh qua images payload với timeout 120s
    assert len(http.generate_calls) == 1
    call_url, call_payload, call_timeout = http.generate_calls[0]
    assert call_timeout == 120.0
    assert call_payload.get("prompt") == "describe"
    assert "images" in call_payload and len(call_payload["images"]) == 1
    assert call_payload["images"][0].startswith("/9j/")

    # Sau khi verify thành công -> trạng thái trở thành READY ("Sẵn sàng")
    st3 = service.check_vision_model_status(DEFAULT_VISION_MODEL)
    assert st3.status == VisionModelStatus.READY


def test_service_verify_fails_when_no_vision_capability(isolated_service):
    """Từ chối cấp trạng thái 'Sẵn sàng' nếu model không hỗ trợ xử lý hình ảnh."""
    service, http, proc, fs, tmp_path = isolated_service

    http.tags_response = {"models": [{"name": "text-only:latest", "digest": "sha256:txt"}]}
    # Show response thực tế thiếu 'vision' capability và không chứa digest
    http.show_response = {"capabilities": ["text"]}

    v_ok, v_msg = service.verify_vision_model("text-only:latest")
    assert v_ok is False
    assert "không hỗ trợ" in v_msg.lower() or "vision" in v_msg.lower()

    st = service.check_vision_model_status("text-only:latest")
    assert st.status != VisionModelStatus.READY


def test_daemon_stop_refuses_unverified_identity(isolated_service):
    """Từ chối dừng tiến trình khi metadata nhận diện không khớp (chống PID-reuse)."""
    service, http, proc, fs, tmp_path = isolated_service

    # Cài fake binary và start daemon
    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("binary")
    fake_bin.chmod(0o755)

    http.tags_response = {"models": []}
    ok, _ = service.start_ollama_daemon()
    assert ok is True

    status = service.check_ollama_status(probe_network=True)
    pid = status.pid
    assert pid is not None

    # Thay đổi thông tin nhận diện trong metadata để giả lập PID-reuse (tiến trình khác sở hữu PID này)
    meta_path = Path(service.get_daemon_meta_path())
    meta = json.loads(meta_path.read_text())
    meta["executable"] = "/usr/bin/some_other_app"
    meta_path.write_text(json.dumps(meta))

    # Yêu cầu dừng phải bị từ chối
    stop_ok, stop_msg = service.stop_ollama_daemon()
    assert stop_ok is False
    assert "danh tính không khớp" in stop_msg or "PID-reuse" in stop_msg
    # Tiến trình không được phép bị dừng
    assert pid not in proc.terminated_pids
    assert proc.is_process_running(pid) is True


def test_daemon_stop_graceful_failure_never_sends_sigkill(isolated_service):
    """Khi dừng nhẹ nhàng graceful thất bại/quá hạn, tuyệt đối KHÔNG force-kill và giữ metadata."""
    service, http, proc, fs, tmp_path = isolated_service

    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("binary")
    fake_bin.chmod(0o755)

    http.tags_response = {"models": []}
    ok, _ = service.start_ollama_daemon()
    assert ok is True

    status = service.check_ollama_status(probe_network=True)
    pid = status.pid
    assert pid is not None

    # Giả lập terminate_graceful thất bại (ví dụ tiến trình bị đơ)
    proc.graceful_fail = True

    stop_ok, stop_msg = service.stop_ollama_daemon(timeout=1.0)
    assert stop_ok is False
    assert "không dừng lại" in stop_msg or "an toàn" in stop_msg

    # Đảm bảo không force-kill và metadata vẫn còn
    assert pid not in proc.terminated_pids
    assert Path(service.get_daemon_meta_path()).exists()


def test_daemon_start_unreachable_fails_truthfully_and_cleans_up(isolated_service):
    """Khi daemon start nhưng API loopback không phản hồi, trả về False và dọn dẹp tiến trình."""
    service, http, proc, fs, tmp_path = isolated_service

    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("binary")
    fake_bin.chmod(0o755)

    # API tags luôn throw lỗi ConnectionError (không phản hồi)
    http.get_json = MagicMock(side_effect=ConnectionError("Port closed"))

    ok, msg = service.start_ollama_daemon(timeout=0.6)
    assert ok is False
    assert "không phản hồi" in msg

    # Tiến trình vừa tạo phải được terminate dọn dẹp
    assert len(proc.terminated_pids) == 1
    # Metadata phải được xóa bỏ
    assert not Path(service.get_daemon_meta_path()).exists()


def test_tar_prevalidation_rejects_symlinks_hardlinks_and_traversal(tmp_path):
    """Hàm validate_tar_members tiền xác thực từ chối nghiêm ngặt symlink, hardlink, path traversal."""
    dest_dir = tmp_path / "target_dir"
    dest_dir.mkdir()

    # 1. Archive chứa symlink
    sym_tar = tmp_path / "symlink.tar"
    with tarfile.open(sym_tar, "w") as tar:
        ti = tarfile.TarInfo(name="symlink_link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tar.addfile(ti)

    with tarfile.open(sym_tar, "r") as tar:
        with pytest.raises(ValueError, match="liên kết"):
            validate_tar_members(tar, str(dest_dir))

    # 2. Archive chứa hardlink
    hard_tar = tmp_path / "hardlink.tar"
    with tarfile.open(hard_tar, "w") as tar:
        ti = tarfile.TarInfo(name="hardlink_link")
        ti.type = tarfile.LNKTYPE
        ti.linkname = "some_file"
        tar.addfile(ti)

    with tarfile.open(hard_tar, "r") as tar:
        with pytest.raises(ValueError, match="liên kết"):
            validate_tar_members(tar, str(dest_dir))

    # 3. Archive chứa đường dẫn tuyệt đối
    abs_tar = tmp_path / "abs.tar"
    with tarfile.open(abs_tar, "w") as tar:
        ti = tarfile.TarInfo(name="/etc/passwd")
        tar.addfile(ti)

    with tarfile.open(abs_tar, "r") as tar:
        with pytest.raises(ValueError, match="tuyệt đối"):
            validate_tar_members(tar, str(dest_dir))

    # 4. Archive chứa traversal ../
    trav_tar = tmp_path / "traversal.tar"
    with tarfile.open(trav_tar, "w") as tar:
        ti = tarfile.TarInfo(name="../escape.txt")
        tar.addfile(ti)

    with tarfile.open(trav_tar, "r") as tar:
        with pytest.raises(ValueError, match="leo thang thư mục"):
            validate_tar_members(tar, str(dest_dir))

    # 5. Archive an toàn chỉ chứa tệp và thư mục hợp lệ
    valid_tar = tmp_path / "valid.tar"
    with tarfile.open(valid_tar, "w") as tar:
        d = tarfile.TarInfo(name="bin")
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        f = tarfile.TarInfo(name="bin/ollama")
        f.type = tarfile.REGTYPE
        content = b"echo hello"
        f.size = len(content)
        tar.addfile(f, io.BytesIO(content))

    with tarfile.open(valid_tar, "r") as tar:
        valid_members = validate_tar_members(tar, str(dest_dir))
        assert len(valid_members) == 2


def test_windows_installer_handoff_no_tar(tmp_path):
    """Trên Windows, install_ollama tải OllamaSetup.exe và chuyển giao detached installer, không giải nén tar."""
    settings = QSettings(str(tmp_path / "win_settings.ini"), QSettings.Format.IniFormat)
    storage_root = str(tmp_path / "win_root")

    http = FakeHttpClient()
    proc = FakeProcessRunner()
    fs = FakeFileSystemAdapter()

    # Dữ liệu installer exe giả lập
    dummy_exe = b"MZ_PE_WINDOWS_INSTALLER_PAYLOAD"
    hasher = hashlib.sha256()
    hasher.update(dummy_exe)
    http.download_data = dummy_exe

    service = LocalAISetupService(
        storage_root=storage_root,
        settings=settings,
        http_client=http,
        process_runner=proc,
        fs_adapter=fs,
        platform_info=("win32", "x86_64"),
    )
    # Khớp hash trong manifest của Windows
    service.manifest[("win32", "x86_64")]["sha256"] = hasher.hexdigest()

    ok, msg = service.install_ollama()
    assert ok is True
    assert "bộ cài đặt chính thức" in msg or "OllamaSetup.exe" in msg
    # Không bao giờ gọi extract_archive cho tệp exe
    assert len(fs.extracted_archives) == 0
    # Đã gọi start_detached_installer
    assert len(proc.detached_installers) == 1
    assert "OllamaSetup.exe" in proc.detached_installers[0]


def test_install_destination_conflict_refusal(isolated_service):
    """Từ chối cài đặt nếu thư mục bin đích bị chiếm dụng bởi tệp không phải thư mục."""
    service, http, proc, fs, tmp_path = isolated_service

    # Tạo tệp bin thay vì thư mục
    bin_conflict = Path(service.get_storage_root()) / "bin"
    bin_conflict.parent.mkdir(parents=True, exist_ok=True)
    bin_conflict.write_text("i am a file blocking bin directory")

    dummy_archive = b"VALID_OLLAMA_PAYLOAD"
    hasher = hashlib.sha256()
    hasher.update(dummy_archive)
    http.download_data = dummy_archive
    service.manifest[("linux", "x86_64")]["sha256"] = hasher.hexdigest()

    ok, msg = service.install_ollama()
    assert ok is False
    assert "xung đột" in msg or "không phải thư mục" in msg


def test_daemon_stop_refuses_pid_only_file_or_missing_metadata(isolated_service):
    """Từ chối dừng tiến trình khi metadata chỉ có PID hoặc thiếu thuộc tính bắt buộc."""
    service, http, proc, fs, tmp_path = isolated_service

    # Tạo tệp metadata chỉ có PID (PID-only metadata)
    meta_path = Path(service.get_daemon_meta_path())
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({"pid": 9999}))
    proc.running_pids.add(9999)

    stop_ok, stop_msg = service.stop_ollama_daemon()
    assert stop_ok is False
    assert "danh tính không khớp" in stop_msg or "PID-reuse" in stop_msg
    # Tiến trình tuyệt đối không bị dừng
    assert 9999 not in proc.terminated_pids


def test_daemon_stop_refuses_basename_fallback_when_path_differs(isolated_service):
    """Không cho phép basename fallback: từ chối dừng nếu đường dẫn tuyệt đối executable khác nhau."""
    service, http, proc, fs, tmp_path = isolated_service

    pid = 9988
    proc.running_pids.add(pid)
    # Metadata mong đợi executable ở /custom/bin/ollama
    meta = {
        "pid": pid,
        "uid": 1000,
        "executable": "/custom/bin/ollama",
        "cmd": ["/custom/bin/ollama", "serve"],
        "start_time": 12345,
    }
    Path(service.get_daemon_meta_path()).parent.mkdir(parents=True, exist_ok=True)
    Path(service.get_daemon_meta_path()).write_text(json.dumps(meta))

    # Tiến trình thực tế chạy từ /usr/bin/ollama (cùng basename 'ollama')
    proc.identities[pid] = {
        "pid": pid,
        "uid": 1000,
        "executable": "/usr/bin/ollama",
        "cmd": ["/usr/bin/ollama", "serve"],
        "start_time": 12345,
    }

    stop_ok, stop_msg = service.stop_ollama_daemon()
    assert stop_ok is False
    assert "danh tính không khớp" in stop_msg
    assert pid not in proc.terminated_pids


def test_daemon_stop_refuses_mismatched_start_time_for_pid_reuse(isolated_service):
    """Từ chối dừng tiến trình khi start_time không khớp (phát hiện PID-reuse)."""
    service, http, proc, fs, tmp_path = isolated_service

    pid = 9977
    proc.running_pids.add(pid)
    meta = {
        "pid": pid,
        "uid": 1000,
        "executable": "/fake/ollama",
        "cmd": ["ollama", "serve"],
        "start_time": 100,
    }
    Path(service.get_daemon_meta_path()).parent.mkdir(parents=True, exist_ok=True)
    Path(service.get_daemon_meta_path()).write_text(json.dumps(meta))

    # Tiến trình thực tế có start_time mới hơn
    proc.identities[pid] = {
        "pid": pid,
        "uid": 1000,
        "executable": "/fake/ollama",
        "cmd": ["ollama", "serve"],
        "start_time": 200,
    }

    stop_ok, stop_msg = service.stop_ollama_daemon()
    assert stop_ok is False
    assert "PID-reuse" in stop_msg or "danh tính không khớp" in stop_msg
    assert pid not in proc.terminated_pids


def test_daemon_start_dies_early_truthful_failure_even_if_port_open(isolated_service):
    """Nếu tiến trình vừa tạo sập sớm, trả về False trung thực ngay cả khi port 11434 đang có daemon ngoài."""
    service, http, proc, fs, tmp_path = isolated_service

    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "ollama"
    fake_bin.write_text("binary")
    fake_bin.chmod(0o755)

    # Port loopback phản hồi (ví dụ daemon hệ thống ngoài)
    http.tags_response = {"models": []}

    # Giả lập tiến trình vừa khởi chạy sập ngay (chết sớm sau start_process)
    original_start = proc.start_process

    def dying_start(cmd, env, cwd):
        pid, meta = original_start(cmd, env, cwd)
        # Tiến trình lập tức chết
        proc.running_pids.remove(pid)
        return pid, meta

    proc.start_process = dying_start

    ok, msg = service.start_ollama_daemon(timeout=1.0)
    assert ok is False
    assert "không phản hồi" in msg or "không sẵn sàng" in msg.lower()
    # Không để lại metadata sở hữu cho tiến trình đã chết
    assert not Path(service.get_daemon_meta_path()).exists()


def test_install_ollama_adopts_matching_binary_without_overwriting(isolated_service):
    """Khi thư mục đích đã có sẵn binary Ollama hợp lệ, tiếp nhận tích cực thay vì xóa tệp."""
    service, http, proc, fs, tmp_path = isolated_service

    # Tạo sẵn binary hợp lệ trong thư mục đích
    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    existing_ollama = bin_dir / "ollama"
    existing_ollama.write_text("#!/bin/sh\necho ollama\nexit 0\n")
    existing_ollama.chmod(0o755)

    dummy_archive = b"VALID_OLLAMA_PAYLOAD"
    hasher = hashlib.sha256()
    hasher.update(dummy_archive)
    http.download_data = dummy_archive
    service.manifest[("linux", "x86_64")]["sha256"] = hasher.hexdigest()

    ok, msg = service.install_ollama()
    assert ok is True
    assert "tiếp nhận" in msg.lower() or "hợp lệ" in msg.lower()
    # Tệp ban đầu vẫn còn nguyên vẹn
    assert existing_ollama.exists()
    assert "exit 0" in existing_ollama.read_text()


def test_get_binary_path_has_no_chmod_side_effects(isolated_service):
    """Gọi get_binary_path là thao tác truy vấn thuần túy, tuyệt đối không tự ý chmod tệp."""
    service, http, proc, fs, tmp_path = isolated_service

    bin_dir = Path(service.get_storage_root()) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    test_bin = bin_dir / "ollama"
    test_bin.write_text("binary")
    test_bin.chmod(0o644)  # Không có quyền thực thi

    p = service.get_binary_path()
    assert p == str(test_bin.resolve())
    # Quyền tệp vẫn phải giữ nguyên 0o644, không bị ép thành 0o755
    current_mode = test_bin.stat().st_mode & 0o777
    assert current_mode == 0o644


def test_default_process_runner_rejects_substring_serve_argument():
    """DefaultProcessRunner phải yêu cầu token 'serve' chính xác, từ chối substring (observe, server, v.v.)."""
    from src.services.local_ai_setup_service import DefaultProcessRunner

    runner = DefaultProcessRunner(
        is_running=lambda pid: True,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "observe"],  # Chứa 'serve' dạng substring nhưng không phải lệnh 'serve'
            "start_time": 5555,
        }
    )

    meta = {
        "pid": 1234,
        "uid": os.getuid() if hasattr(os, "getuid") else 1000,
        "executable": "/bin/ollama",
        "cmd": ["/bin/ollama", "observe"],
        "start_time": 5555,
    }
    # Phải từ chối vì không có token chính xác 'serve'
    assert runner.verify_process_identity(1234, meta) is False

    # Chấp nhận khi có token chính xác 'serve'
    runner_valid = DefaultProcessRunner(
        is_running=lambda pid: True,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "serve"],
            "start_time": 5555,
        }
    )
    meta_valid = dict(meta, cmd=["/bin/ollama", "serve"])
    assert runner_valid.verify_process_identity(1234, meta_valid) is True


def test_default_process_runner_mandatory_identity_fields():
    """DefaultProcessRunner từ chối xác thực nếu thiếu bất kỳ trường định danh bắt buộc nào."""
    from src.services.local_ai_setup_service import DefaultProcessRunner

    runner = DefaultProcessRunner(
        is_running=lambda pid: True,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "serve"],
            "start_time": 5555,
        }
    )

    base_meta = {
        "pid": 1234,
        "uid": os.getuid() if hasattr(os, "getuid") else 1000,
        "executable": "/bin/ollama",
        "cmd": ["/bin/ollama", "serve"],
        "start_time": 5555,
    }

    # Thiếu start_time
    m1 = dict(base_meta)
    del m1["start_time"]
    assert runner.verify_process_identity(1234, m1) is False

    # Thiếu uid
    m2 = dict(base_meta)
    del m2["uid"]
    assert runner.verify_process_identity(1234, m2) is False

    # Thiếu executable
    m3 = dict(base_meta, executable="")
    assert runner.verify_process_identity(1234, m3) is False

    # Thiếu cmd
    m4 = dict(base_meta, cmd=[])
    assert runner.verify_process_identity(1234, m4) is False


def test_default_process_runner_wrong_start_identity_sends_zero_signals():
    """Khi start_time không khớp (nguy cơ PID reuse), runner mở pidfd nhưng gửi 0 tín hiệu và fail closed."""
    from src.services.local_ai_setup_service import DefaultProcessRunner

    signals_sent = []
    closed_fds = []

    def mock_pidfd_open(pid, flags):
        return 99

    def mock_pidfd_send_signal(fd, sig):
        signals_sent.append((fd, sig))

    def mock_close(fd):
        closed_fds.append(fd)

    runner = DefaultProcessRunner(
        pidfd_open=mock_pidfd_open,
        pidfd_send_signal=mock_pidfd_send_signal,
        close_fd=mock_close,
        is_running=lambda pid: True,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "serve"],
            "start_time": 200,  # Khác với expected start_time 100
        }
    )

    expected_meta = {
        "pid": 5678,
        "uid": os.getuid() if hasattr(os, "getuid") else 1000,
        "executable": "/bin/ollama",
        "cmd": ["/bin/ollama", "serve"],
        "start_time": 100,
    }

    ok = runner.terminate_graceful(5678, timeout=0.5, expected_meta=expected_meta)
    assert ok is False
    # Tuyệt đối ZERO tín hiệu được gửi đi
    assert len(signals_sent) == 0
    # Handle pidfd đã được giải phóng đóng lại an toàn
    assert 99 in closed_fds


def test_default_process_runner_no_pidfd_fails_closed_zero_signals():
    """Nếu không có pidfd (hệ điều hành không hỗ trợ hoặc lỗi mở), runner gửi 0 tín hiệu và fail closed."""
    from src.services.local_ai_setup_service import DefaultProcessRunner

    signals_sent = []

    runner = DefaultProcessRunner(
        pidfd_open=None,  # Không có pidfd_open
        pidfd_send_signal=lambda fd, sig: signals_sent.append((fd, sig)),
        close_fd=lambda fd: None,
        is_running=lambda pid: True,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "serve"],
            "start_time": 100,
        }
    )

    expected_meta = {
        "pid": 5678,
        "uid": os.getuid() if hasattr(os, "getuid") else 1000,
        "executable": "/bin/ollama",
        "cmd": ["/bin/ollama", "serve"],
        "start_time": 100,
    }

    ok = runner.terminate_graceful(5678, timeout=0.5, expected_meta=expected_meta)
    assert ok is False
    assert len(signals_sent) == 0


def test_default_process_runner_stable_handle_signal_success():
    """Khi xác thực danh tính thành công sau khi mở pidfd, tín hiệu gửi tới chính handle fd ổn định."""
    from src.services.local_ai_setup_service import DefaultProcessRunner
    import signal

    signals_sent = []
    closed_fds = []
    running_state = [True]

    def mock_is_running(pid):
        # Sau khi nhận signal, tiến trình dừng
        if len(signals_sent) > 0:
            return False
        return running_state[0]

    runner = DefaultProcessRunner(
        pidfd_open=lambda pid, flags: 77,
        pidfd_send_signal=lambda fd, sig: signals_sent.append((fd, sig)),
        close_fd=lambda fd: closed_fds.append(fd),
        is_running=mock_is_running,
        get_identity=lambda pid: {
            "pid": pid,
            "uid": os.getuid() if hasattr(os, "getuid") else 1000,
            "executable": "/bin/ollama",
            "cmd": ["/bin/ollama", "serve"],
            "start_time": 1234,
        }
    )

    expected_meta = {
        "pid": 4321,
        "uid": os.getuid() if hasattr(os, "getuid") else 1000,
        "executable": "/bin/ollama",
        "cmd": ["/bin/ollama", "serve"],
        "start_time": 1234,
    }

    ok = runner.terminate_graceful(4321, timeout=1.0, expected_meta=expected_meta)
    assert ok is True
    # Tín hiệu SIGTERM được gửi tới fd 77 (handle ổn định)
    assert len(signals_sent) == 1
    fd_used, sig_used = signals_sent[0]
    assert fd_used == 77
    assert sig_used == signal.SIGTERM
    assert 77 in closed_fds


def test_default_process_runner_windows_no_handle_fails_closed(monkeypatch):
    """Trên Windows, nếu cơ chế verified handle không khả dụng, runner fail closed và không chạy taskkill."""
    from src.services.local_ai_setup_service import DefaultProcessRunner

    monkeypatch.setattr(sys, "platform", "win32")

    runner = DefaultProcessRunner(
        win_open_process=None,  # Không có verified handle adapter
        is_running=lambda pid: True,
    )

    ok = runner.terminate_graceful(9999, timeout=0.5)
    assert ok is False


def test_verify_vision_model_fails_on_digest_mismatch_or_change(isolated_service):
    """Từ chối cấp READY nếu digest trong /api/tags bị thay đổi trong quá trình verify."""
    service, http, proc, fs, tmp_path = isolated_service

    # Lần 1 (trước smoke test): digest 'sha256:first'
    # Lần 2 (sau smoke test): digest 'sha256:second' (bị đổi)
    call_count = [0]
    def dynamic_get_json(url, timeout=5.0):
        if "/api/tags" in url:
            call_count[0] += 1
            cur_digest = "sha256:first" if call_count[0] == 1 else "sha256:second"
            return {"models": [{"name": DEFAULT_VISION_MODEL, "size": 1900000000, "digest": cur_digest}]}
        raise ConnectionError(url)

    http.get_json = dynamic_get_json
    http.show_response = {"capabilities": ["vision"]}
    http.generate_response = {"response": "A clean shot", "done": True}

    ok, msg = service.verify_vision_model(DEFAULT_VISION_MODEL)
    assert ok is False
    assert "thay đổi" in msg.lower() or "digest" in msg.lower()

    st = service.check_vision_model_status(DEFAULT_VISION_MODEL)
    assert st.status != VisionModelStatus.READY


def test_verify_vision_model_fails_on_empty_or_incomplete_response(isolated_service):
    """Từ chối cấp READY nếu phản hồi /api/generate có done=False hoặc nội dung văn bản rỗng."""
    service, http, proc, fs, tmp_path = isolated_service

    http.tags_response = {"models": [{"name": DEFAULT_VISION_MODEL, "size": 1900000000, "digest": "sha256:static"}]}
    http.show_response = {"capabilities": ["vision"]}

    # Trường hợp 1: done = False
    http.generate_response = {"response": "partial", "done": False}
    ok1, msg1 = service.verify_vision_model(DEFAULT_VISION_MODEL)
    assert ok1 is False
    assert "done=False" in msg1 or "chưa hoàn thành" in msg1

    # Trường hợp 2: response rỗng
    http.generate_response = {"response": "   ", "done": True}
    ok2, msg2 = service.verify_vision_model(DEFAULT_VISION_MODEL)
    assert ok2 is False
    assert "rỗng" in msg2.lower()

    # Trường hợp 3: có trường 'error'
    http.generate_response = {"error": "out of memory", "done": True}
    ok3, msg3 = service.verify_vision_model(DEFAULT_VISION_MODEL)
    assert ok3 is False
    assert "out of memory" in msg3




@pytest.mark.skipif(sys.platform == "win32", reason="Linux Ollama archive layout")
def test_install_archive_preserves_internal_library_links(tmp_path):
    archive = tmp_path / "ollama.tar"
    destination = tmp_path / "staging"
    with tarfile.open(archive, "w") as package:
        for name, target in (
            ("lib/ollama/libggml.so", "./libggml.so.0"),
            ("lib/ollama/libggml.so.0", "libggml.so.0.1"),
        ):
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            package.addfile(member)
        library = tarfile.TarInfo("lib/ollama/libggml.so.0.1")
        library.size = 3
        package.addfile(library, io.BytesIO(b"lib"))

    assert DefaultFileSystemAdapter().extract_archive(str(archive), str(destination))
    link = destination / "lib/ollama/libggml.so"
    assert link.is_symlink()
    assert link.read_bytes() == b"lib"


def test_install_archive_rejects_escaping_library_link(tmp_path):
    archive = tmp_path / "ollama.tar"
    destination = tmp_path / "staging"
    with tarfile.open(archive, "w") as package:
        member = tarfile.TarInfo("lib/ollama/libggml.so")
        member.type = tarfile.SYMTYPE
        member.linkname = "../../../outside"
        package.addfile(member)

    with pytest.raises(ValueError):
        DefaultFileSystemAdapter().extract_archive(str(archive), str(destination))
    assert not (destination / "lib/ollama/libggml.so").is_symlink()


@pytest.mark.parametrize("response", [
    b'{"status":"pulling manifest"}\n{"error":"model not found"}\n',
    b'{"status":"downloading","completed":1,"total":2}\n',
])
def test_pull_does_not_report_success_when_ollama_fails_or_disconnects(
    isolated_service, monkeypatch, response
):
    service, *_ = isolated_service
    service.http = DefaultHttpClient()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(response))

    ok, _ = service.pull_vision_model()

    assert not ok
