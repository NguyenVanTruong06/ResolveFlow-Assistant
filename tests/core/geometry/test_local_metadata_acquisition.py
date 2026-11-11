"""tests/core/geometry/test_local_metadata_acquisition.py
Bộ kiểm thử đơn vị toàn diện, hữu hạn và độc lập cho module thu thập siêu dữ liệu tiêu đề tệp video cục bộ
src/core/geometry/local_metadata_acquisition.py (G06-06C2b-FINAL-THEN-06C2c1-I).

Quy tắc:
1. 100% mocked / in-memory subprocess, stream pipes, và clock. Tuyệt đối không gọi binary ngoài thực tế,
   không decode media, không kết nối mạng.
2. Không import Qt, không thay đổi PATH/cwd toàn cục, không lưu trữ cache hay ghi đè tệp ngoài tmp_path.
3. Bao phủ toàn bộ ma trận lỗi: missing binary/source, symlink/FIFO/directory/URI, timeout, cancellation ở cả 4 giai đoạn,
   stdout/stderr overflow, nonzero exit, malformed/duplicate/nonfinite/non-UTF8 JSON, TOCTOU mutation, cleanup chaining,
   và DTO constructor validation với hostile objects / huge ints.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional
import unittest.mock as mock

import pytest

from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.local_metadata_acquisition import (
    MAX_PROBE_STDERR_BYTES,
    MAX_PROBE_STDOUT_BYTES,
    LocalHeaderAcquisitionResult,
    SourceFreshnessStat,
    _BoundedPipeReader,
    _cleanup_child_and_readers,
    _parse_format_duration,
    acquire_local_video_header,
)
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader


# ==============================================================================
# Test Fixtures & Fakes
# ==============================================================================

class HostileReprObject:
    """Đối tượng ném ngoại lệ nếu bị gọi __repr__, __str__, hoặc __eq__."""
    def __init__(self, name: str = "hostile") -> None:
        self.name = name

    def __repr__(self) -> str:
        raise RuntimeError("HOSTILE_REPR_INVOKED")

    def __str__(self) -> str:
        raise RuntimeError("HOSTILE_STR_INVOKED")

    def __eq__(self, other: Any) -> bool:
        raise RuntimeError("HOSTILE_EQ_INVOKED")


class FakeStream(io.BytesIO):
    """Giả lập luồng I/O bytes hỗ trợ callback theo dõi số lần đóng."""
    def __init__(self, data: bytes = b"", *, on_read: Optional[Callable[[int], bytes]] = None) -> None:
        super().__init__(data)
        self._on_read = on_read
        self.closed_count = 0

    def read(self, n: int = -1) -> bytes:
        if self._on_read is not None:
            return self._on_read(n)
        return super().read(n)

    def close(self) -> None:
        self.closed_count += 1
        super().close()


class FakePopen:
    """Giả lập tiến trình con subprocess.Popen với kiểm soát dứt điểm trạng thái poll, terminate, kill, wait."""
    def __init__(
        self,
        stdout_data: bytes = b"",
        stderr_data: bytes = b"",
        returncode: int = 0,
        *,
        stdout_stream: Optional[io.IOBase] = None,
        stderr_stream: Optional[io.IOBase] = None,
        poll_sequence: Optional[list[Optional[int]]] = None,
        on_terminate: Optional[Callable[[], None]] = None,
        on_kill: Optional[Callable[[], None]] = None,
    ) -> None:
        self.stdout = stdout_stream or io.BytesIO(stdout_data)
        self.stderr = stderr_stream or io.BytesIO(stderr_data)
        self.returncode = returncode
        self._poll_seq = list(poll_sequence) if poll_sequence is not None else [0]
        self.terminate_called = 0
        self.kill_called = 0
        self.wait_called = 0
        self.stdin = None
        self._on_terminate = on_terminate
        self._on_kill = on_kill

    def poll(self) -> Optional[int]:
        if self._poll_seq:
            res = self._poll_seq.pop(0)
            if res is not None:
                self.returncode = res
            return res
        return self.returncode

    def terminate(self) -> None:
        self.terminate_called += 1
        self.returncode = -15
        self._poll_seq = []
        if self._on_terminate:
            self._on_terminate()

    def kill(self) -> None:
        self.kill_called += 1
        self.returncode = -9
        self._poll_seq = []
        if self._on_kill:
            self._on_kill()

    def wait(self, timeout: Optional[float] = None) -> int:
        self.wait_called += 1
        return self.returncode


@pytest.fixture
def sample_video_file(tmp_path: Path) -> Path:
    """Tạo một regular video file thực tế trong tmp_path."""
    video = tmp_path / "valid_clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom")
    return video


def make_valid_ffprobe_payload(
    *,
    r_frame_rate: str = "30000/1001",
    avg_frame_rate: str = "30000/1001",
    nb_frames: Optional[str] = "300",
    duration: Optional[Any] = "10.01",
    width: int = 1920,
    height: int = 1080,
    codec_name: str = "h264",
    extra_streams: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Tạo payload dict hợp lệ chuẩn ffprobe JSON."""
    video_stream: dict[str, Any] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": codec_name,
        "width": width,
        "height": height,
        "r_frame_rate": r_frame_rate,
        "avg_frame_rate": avg_frame_rate,
        "disposition": {"attached_pic": 0},
    }
    if nb_frames is not None:
        video_stream["nb_frames"] = nb_frames

    streams = [video_stream]
    if extra_streams:
        streams.extend(extra_streams)

    payload: dict[str, Any] = {"streams": streams}
    if duration is not None:
        payload["format"] = {"duration": duration}
    return payload


# ==============================================================================
# Group 1: Valid Execution & Header Parsing
# ==============================================================================

def test_header_probe_cleans_packaged_libraries_without_changing_parent(sample_video_file, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/resolveflow-assistant/_internal:/opt/resolve/libs")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/resolve/libs:/usr/lib")
    parent_env = dict(os.environ)
    calls = []

    def spawn(cmd, **kwargs):
        calls.append(kwargs)
        return FakePopen(stdout_data=json.dumps(make_valid_ffprobe_payload()).encode("utf-8"))

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", spawn)
    acquire_local_video_header(sample_video_file)

    assert calls[0]["env"]["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in calls[0]["env"]
    assert calls[0]["env"]["PATH"] == parent_env["PATH"]
    assert dict(os.environ) == parent_env


def test_acquire_header_nominal_ntsc_success(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra đường chạy danh định: video NTSC 30000/1001, format duration 10.01, DTO bất biến, không suy diễn CFR."""
    payload = make_valid_ffprobe_payload(
        r_frame_rate="30000/1001",
        avg_frame_rate="30000/1001",
        nb_frames="300",
        duration="10.01",
    )
    stdout_bytes = json.dumps(payload).encode("utf-8")

    popen_called = []

    def mock_popen(cmd: list[str], **kwargs: Any) -> FakePopen:
        popen_called.append((cmd, kwargs))
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    result = acquire_local_video_header(sample_video_file)

    assert len(popen_called) == 1
    cmd, kwargs = popen_called[0]
    assert cmd[0] == "/usr/bin/ffprobe"
    assert "-protocol_whitelist" in cmd
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == os.path.abspath(str(sample_video_file))

    # Kiểm tra kết quả LocalHeaderAcquisitionResult
    assert isinstance(result, LocalHeaderAcquisitionResult)
    assert result.file_path == os.path.abspath(str(sample_video_file))
    assert result.format_duration_sec == Fraction(1001, 100)
    assert result.video_header.r_frame_rate == RationalFPS(30000, 1001)
    assert result.video_header.avg_frame_rate == RationalFPS(30000, 1001)
    assert result.video_header.reported_nb_frames == 300
    assert result.video_header.codec_name == "h264"

    # Kiểm tra freshness stat
    st = os.stat(sample_video_file)
    assert result.freshness.file_size_bytes == st.st_size
    assert result.freshness.device_id == st.st_dev
    assert result.freshness.inode_number == st.st_ino

    # Kiểm tra tính bất biến (frozen dataclasses)
    with pytest.raises(FrozenInstanceError):
        result.file_path = "other"  # type: ignore
    with pytest.raises(FrozenInstanceError):
        result.freshness.file_size_bytes = 0  # type: ignore

    # Kiểm tra cấm các cờ suy diễn CFR / duration / FPS
    assert not hasattr(result, "is_cfr")
    assert not hasattr(result, "is_fps_known")
    assert not hasattr(result, "is_duration_known")
    assert not hasattr(result, "verified")


def test_acquire_header_duration_none_controls(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra các trường hợp format.duration thiếu hoặc là N/A hoặc là 0 -> trả về None."""
    cases = [
        {},
        {"format": {}},
        {"format": {"duration": None}},
        {"format": {"duration": "N/A"}},
        {"format": {"duration": 0}},
        {"format": {"duration": 0.0}},
        {"format": {"duration": "0"}},
        {"format": {"duration": "0.0"}},
    ]

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    for fmt_case in cases:
        payload = make_valid_ffprobe_payload()
        if "format" in fmt_case:
            payload["format"] = fmt_case["format"]
        else:
            payload.pop("format", None)

        stdout_bytes = json.dumps(payload).encode("utf-8")
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen(stdout_data=stdout_bytes))

        res = acquire_local_video_header(sample_video_file)
        assert res.format_duration_sec is None


def test_acquire_header_selected_stream_index(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra chọn chính xác luồng video qua selected_stream_index."""
    stream0 = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "hevc",
        "width": 3840,
        "height": 2160,
        "r_frame_rate": "60/1",
        "avg_frame_rate": "60/1",
        "disposition": {"attached_pic": 0},
    }
    payload = make_valid_ffprobe_payload(extra_streams=[stream0])
    # Đổi stream0 có index=1 để phân biệt
    payload["streams"][1]["index"] = 1
    stdout_bytes = json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen(stdout_data=stdout_bytes))

    res = acquire_local_video_header(sample_video_file, selected_stream_index=1)
    assert res.video_header.stream_index == 1
    assert res.video_header.codec_name == "hevc"


def test_acquire_header_special_file_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra đường dẫn chứa khoảng trắng, tiếng Việt có dấu, và leading dash."""
    special_names = [
        "thước phim quay 2026.mp4",
        "-leading_dash_video.mp4",
        "thư mục phim/video nét căng.mp4",
    ]
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    popen_cmds = []

    def mock_popen(cmd: list[str], **kwargs: Any) -> FakePopen:
        popen_cmds.append(cmd)
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    for name in special_names:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"content")

        res = acquire_local_video_header(p)
        assert res.file_path == os.path.abspath(str(p))
        assert popen_cmds[-1][-1] == os.path.abspath(str(p))


# ==============================================================================
# Group 2: Input & Pre-execution Argument Validation (Zero Popen Calls)
# ==============================================================================

def test_argument_validation_file_path_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra từ chối các file_path sai kiểu, rỗng, khoảng trắng, chứa NUL mà không gọi Popen."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    invalid_paths = [
        123,
        None,
        [],
        {},
        "",
        "   ",
        "\t\n",
        "video\x00file.mp4",
    ]
    for p in invalid_paths:
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
            acquire_local_video_header(p)  # type: ignore

    assert len(popen_called) == 0


def test_argument_validation_network_uri_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra từ chối mọi URI giao thức mạng (http, rtsp, file://) mà không gọi Popen."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    network_uris = [
        "http://example.com/video.mp4",
        "https://domain.vn/stream.m3u8",
        "rtsp://192.168.1.10:554/live",
        "ftp://storage.local/raw.mov",
        "file:///media/video.mp4",
    ]
    for uri in network_uris:
        with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_PATH"):
            acquire_local_video_header(uri)

    assert len(popen_called) == 0


def test_source_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp nguồn không tồn tại ban đầu -> FileNotFoundError(ERR_SOURCE_FILE_NOT_FOUND)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    missing = tmp_path / "nonexistent_file.mp4"
    with pytest.raises(FileNotFoundError, match="ERR_SOURCE_FILE_NOT_FOUND"):
        acquire_local_video_header(missing)

    assert len(popen_called) == 0


def test_source_file_directory_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp nguồn là thư mục -> ValueError(ERR_INVALID_SOURCE_PATH)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    d = tmp_path / "sub_dir"
    d.mkdir()
    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_PATH"):
        acquire_local_video_header(d)

    assert len(popen_called) == 0


def test_source_file_symlink_rejected(tmp_path: Path, sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp nguồn là symlink (trỏ tệp thật hoặc dangling) -> ValueError(ERR_INVALID_SOURCE_PATH)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    # 1. Symlink trỏ tệp thật
    valid_symlink = tmp_path / "link_to_real.mp4"
    valid_symlink.symlink_to(sample_video_file)

    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_PATH"):
        acquire_local_video_header(valid_symlink)

    # 2. Dangling symlink (trỏ tệp không tồn tại)
    dangling_symlink = tmp_path / "broken_link.mp4"
    dangling_symlink.symlink_to(tmp_path / "does_not_exist.mp4")

    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_PATH"):
        acquire_local_video_header(dangling_symlink)

    assert len(popen_called) == 0


def test_source_file_fifo_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp nguồn là FIFO pipe -> ValueError(ERR_INVALID_SOURCE_PATH)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    fifo_path = tmp_path / "test_fifo.pipe"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, OSError):
        pytest.skip("Hệ thống không hỗ trợ os.mkfifo")

    with pytest.raises(ValueError, match="ERR_INVALID_SOURCE_PATH"):
        acquire_local_video_header(fifo_path)

    assert len(popen_called) == 0


def test_argument_validation_timeout_sec(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra xác thực timeout_sec: bool, <=0, >60, NaN, Inf, 10**400 -> ValueError(ERR_INVALID_PROBE_ARGUMENT)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    invalid_timeouts = [
        True,
        False,
        0,
        0.0,
        -1.0,
        -0.001,
        60.0001,
        60.1,
        100.0,
        10**400,
        float("nan"),
        float("inf"),
        float("-inf"),
        "5.0",
        None,
    ]
    for t in invalid_timeouts:
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
            acquire_local_video_header(sample_video_file, timeout_sec=t)  # type: ignore

    assert len(popen_called) == 0


def test_argument_validation_selected_stream_index(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra xác thực selected_stream_index: bool, âm, str, float -> ValueError(ERR_INVALID_STREAM_SELECTION)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    invalid_indexes = [
        True,
        False,
        -1,
        -10,
        "0",
        1.5,
    ]
    for idx in invalid_indexes:
        with pytest.raises(ValueError, match="ERR_INVALID_STREAM_SELECTION"):
            acquire_local_video_header(sample_video_file, selected_stream_index=idx)  # type: ignore

    assert len(popen_called) == 0


def test_argument_validation_cancelled_fn(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra cancelled_fn không phải callable -> ValueError(ERR_INVALID_PROBE_ARGUMENT)."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        acquire_local_video_header(sample_video_file, cancelled_fn="not_callable")  # type: ignore

    assert len(popen_called) == 0


def test_binary_locator_missing_or_invalid(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra định vị binary ffprobe: không có trong PATH hoặc ffprobe_path không tồn tại."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))

    # 1. shutil.which trả về None và ffprobe_path=None
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="ERR_FFPROBE_NOT_FOUND"):
        acquire_local_video_header(sample_video_file)

    # 2. ffprobe_path chỉ định tệp không tồn tại
    with pytest.raises(FileNotFoundError, match="ERR_FFPROBE_NOT_FOUND"):
        acquire_local_video_header(sample_video_file, ffprobe_path="/opt/bin/no_such_ffprobe")

    # 3. ffprobe_path là chuỗi rỗng / NUL
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        acquire_local_video_header(sample_video_file, ffprobe_path="")

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        acquire_local_video_header(sample_video_file, ffprobe_path="ffprobe\x00")

    assert len(popen_called) == 0


# ==============================================================================
# Group 3: Subprocess Flags & Platform Semantics
# ==============================================================================

def test_subprocess_argv_and_kwargs_linux(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra argv và kwargs khi chạy trên Linux (POSIX): không có creationflags của Windows."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    captured_kwargs = {}

    def mock_popen(cmd: list[str], **kwargs: Any) -> FakePopen:
        captured_kwargs.update(kwargs)
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    acquire_local_video_header(sample_video_file)

    assert captured_kwargs["stdin"] == subprocess.DEVNULL
    assert captured_kwargs["shell"] is False
    assert captured_kwargs["stdout"] == subprocess.PIPE
    assert captured_kwargs["stderr"] == subprocess.PIPE
    assert "creationflags" not in captured_kwargs


def test_subprocess_kwargs_windows(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra cờ creationflags CREATE_NO_WINDOW trên Windows."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    captured_kwargs = {}

    def mock_popen(cmd: list[str], **kwargs: Any) -> FakePopen:
        captured_kwargs.update(kwargs)
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda _: "C:\\ffmpeg\\ffprobe.exe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    acquire_local_video_header(sample_video_file)

    assert "creationflags" in captured_kwargs
    assert captured_kwargs["creationflags"] == 0x08000000


def test_no_environment_or_path_mutation(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra hàm không làm thay đổi biến môi trường PATH hoặc cwd."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    path_before = os.environ.get("PATH")
    cwd_before = os.getcwd()

    acquire_local_video_header(sample_video_file)

    assert os.environ.get("PATH") == path_before
    assert os.getcwd() == cwd_before


# ==============================================================================
# Group 4: Streaming Pipe Readers & Buffer Overflows
# ==============================================================================

def test_stdout_cap_equality_allowed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra lượng stdout đúng bằng MAX_PROBE_STDOUT_BYTES (512KB) được chấp thuận."""
    base_payload = make_valid_ffprobe_payload()
    base_bytes = json.dumps(base_payload).encode("utf-8")
    assert len(base_bytes) < MAX_PROBE_STDOUT_BYTES

    # Độn thêm khoảng trắng cuối JSON để đạt đúng 512KB
    padding = b" " * (MAX_PROBE_STDOUT_BYTES - len(base_bytes))
    exact_bytes = base_bytes + padding
    assert len(exact_bytes) == MAX_PROBE_STDOUT_BYTES

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=exact_bytes))

    result = acquire_local_video_header(sample_video_file)
    assert result.video_header.r_frame_rate == RationalFPS(30000, 1001)


def test_stdout_cap_plus_one_overflow_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra stdout vượt quá MAX_PROBE_STDOUT_BYTES đúng 1 byte -> ValueError(ERR_PROBE_METADATA_LIMIT)."""
    overflow_bytes = b"A" * (MAX_PROBE_STDOUT_BYTES + 1)
    fake_proc = FakePopen(stdout_data=overflow_bytes, poll_sequence=[None, 0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_PROBE_METADATA_LIMIT"):
        acquire_local_video_header(sample_video_file)

    assert fake_proc.terminate_called > 0 or fake_proc.kill_called > 0


def test_stderr_cap_equality_allowed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra stderr đúng bằng MAX_PROBE_STDERR_BYTES (64KB) được chấp thuận khi tiến trình thoát 0."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")
    exact_stderr = b"E" * MAX_PROBE_STDERR_BYTES

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: FakePopen(stdout_data=stdout_bytes, stderr_data=exact_stderr)
    )

    result = acquire_local_video_header(sample_video_file)
    assert result.video_header.reported_nb_frames == 300


def test_stderr_cap_plus_one_overflow_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra stderr vượt quá MAX_PROBE_STDERR_BYTES đúng 1 byte -> ValueError(ERR_PROBE_METADATA_LIMIT)."""
    overflow_stderr = b"E" * (MAX_PROBE_STDERR_BYTES + 1)
    fake_proc = FakePopen(stderr_data=overflow_stderr, poll_sequence=[None, 0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_PROBE_METADATA_LIMIT"):
        acquire_local_video_header(sample_video_file)

    assert fake_proc.terminate_called > 0 or fake_proc.kill_called > 0


def test_reader_io_exception_handled(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra ngoại lệ I/O từ stream trong reader -> RuntimeError(ERR_PROBE_EXECUTION_FAILED)."""
    def broken_read(_: int) -> bytes:
        raise OSError("BROKEN_PIPE_SIMULATION")

    stream = FakeStream(on_read=broken_read)
    fake_proc = FakePopen(stdout_stream=stream, poll_sequence=[None, 0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED"):
        acquire_local_video_header(sample_video_file)


# ==============================================================================
# Group 5: Process Lifecycle, Timeout, Returncodes & Cleanup Chaining
# ==============================================================================

def test_child_spawn_failure(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra Popen ném ngoại lệ khi khởi chạy -> RuntimeError(ERR_PROBE_EXECUTION_FAILED)."""
    def mock_broken_popen(*a: Any, **kw: Any) -> None:
        raise OSError("CANNOT_SPAWN_PROCESS")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_broken_popen)

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED"):
        acquire_local_video_header(sample_video_file)


def test_child_nonzero_exit_code(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tiến trình kết thúc với mã lỗi != 0 -> RuntimeError(ERR_PROBE_EXECUTION_FAILED)."""
    fake_proc = FakePopen(returncode=1, poll_sequence=[1])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*mã lỗi 1"):
        acquire_local_video_header(sample_video_file)


def test_probe_timeout_expired(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra quá hạn timeout_sec -> TimeoutError(ERR_PROBE_TIMEOUT), tiến trình bị kill."""
    # Giả lập đồng hồ đơn điệu nhảy vượt deadline sau lần kiểm tra thứ 2
    times = [100.0, 100.1, 110.0, 110.1, 110.2]
    def mock_monotonic() -> float:
        if times:
            return times.pop(0)
        return 200.0

    fake_proc = FakePopen(poll_sequence=[None, None, None])

    monkeypatch.setattr("time.monotonic", mock_monotonic)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(TimeoutError, match="ERR_PROBE_TIMEOUT"):
        acquire_local_video_header(sample_video_file, timeout_sec=5.0)

    assert fake_proc.terminate_called > 0 or fake_proc.kill_called > 0


def test_forced_cleanup_failure_chains_original(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra khi xảy ra lỗi chính và cleanup cũng thất bại -> RuntimeError(ERR_PROBE_CLEANUP_FAILED) được chain từ lỗi chính."""
    fake_proc = FakePopen(returncode=1, poll_sequence=[1])

    def mock_broken_cleanup(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ERR_PROBE_CLEANUP_FAILED: cannot join reader")

    monkeypatch.setattr("src.core.geometry.local_metadata_acquisition._cleanup_child_and_readers", mock_broken_cleanup)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(RuntimeError, match="ERR_PROBE_CLEANUP_FAILED") as exc_info:
        acquire_local_video_header(sample_video_file)

    # Khẳng định exception chaining
    assert exc_info.value.__cause__ is not None
    assert "ERR_PROBE_EXECUTION_FAILED" in str(exc_info.value.__cause__)


# ==============================================================================
# Group 6: Cancellation at All 4 Stages & Callback Protection
# ==============================================================================

def test_cancellation_stage1_before_spawn(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra hủy ở giai đoạn 1 (trước khi spawn): ném InterruptedError, 0 lần gọi Popen."""
    popen_called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: popen_called.append(1))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        acquire_local_video_header(sample_video_file, cancelled_fn=lambda: True)

    assert len(popen_called) == 0


def test_cancellation_stage2_during_supervision(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra hủy ở giai đoạn 2 (trong vòng lặp giám sát): tiến trình bị dọn dẹp sạch sẽ."""
    cancels = [False, True]
    def cancel_fn() -> bool:
        if cancels:
            return cancels.pop(0)
        return True

    fake_proc = FakePopen(poll_sequence=[None, None])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        acquire_local_video_header(sample_video_file, cancelled_fn=cancel_fn)

    assert fake_proc.terminate_called > 0 or fake_proc.kill_called > 0


def test_cancellation_stage3_after_read(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra hủy ở giai đoạn 3 (ngay sau khi đọc xong pipes trước khi phân tích payload)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    # Giả lập cancel_fn trả về False cho đến khi pipes đọc xong (lần thứ 3)
    call_count = 0
    def cancel_fn() -> bool:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            return True
        return False

    fake_proc = FakePopen(stdout_data=stdout_bytes, poll_sequence=[0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        acquire_local_video_header(sample_video_file, cancelled_fn=cancel_fn)


def test_cancellation_stage4_before_return(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra hủy ở giai đoạn 4 (ngay trước khi công bố kết quả LocalHeaderAcquisitionResult)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    # cancel_fn chỉ kích hoạt ở lần gọi cuối cùng
    call_count = 0
    def cancel_fn() -> bool:
        nonlocal call_count
        call_count += 1
        if call_count >= 4:
            return True
        return False

    fake_proc = FakePopen(stdout_data=stdout_bytes, poll_sequence=[0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        acquire_local_video_header(sample_video_file, cancelled_fn=cancel_fn)


def test_cancellation_callback_raises_exception(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra cancelled_fn ném ngoại lệ -> RuntimeError(ERR_PROBE_EXECUTION_FAILED) và dọn dẹp."""
    def broken_cancelled_fn() -> bool:
        raise ArithmeticError("CALLBACK_CRASHED")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*cancelled_fn ném ngoại lệ"):
        acquire_local_video_header(sample_video_file, cancelled_fn=broken_cancelled_fn)


# ==============================================================================
# Group 7: Strict JSON Parsing & Payload Invariants
# ==============================================================================

def test_stdout_empty_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra stdout rỗng hoặc chỉ toàn khoảng trắng -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    empty_outputs = [b"", b"   \n\t", b"\n"]
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    for out in empty_outputs:
        fake_proc = FakePopen(stdout_data=out)
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
            acquire_local_video_header(sample_video_file)


def test_stdout_invalid_utf8_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra stdout chứa byte không phải UTF-8 -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    invalid_utf8 = b"\x80\xff\xfe\x00\x01\x02"
    fake_proc = FakePopen(stdout_data=invalid_utf8)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*UTF-8"):
        acquire_local_video_header(sample_video_file)


def test_stdout_malformed_json_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra JSON bị cụt hoặc sai cú pháp -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    malformed_json = b'{"streams": [{"codec_type": "video"'
    fake_proc = FakePopen(stdout_data=malformed_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
        acquire_local_video_header(sample_video_file)


def test_stdout_json_duplicate_keys_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra JSON chứa khóa trùng lặp trong đối tượng -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    duplicate_json = b'{"format": {}, "format": {}}'
    fake_proc = FakePopen(stdout_data=duplicate_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*khóa trùng lặp"):
        acquire_local_video_header(sample_video_file)


def test_stdout_json_nan_constant_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra JSON chứa hằng số NaN -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    nan_json = b'{"streams": [{"width": NaN}]}'
    fake_proc = FakePopen(stdout_data=nan_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*hằng số không chuẩn"):
        acquire_local_video_header(sample_video_file)


def test_stdout_json_infinity_constant_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra JSON chứa hằng số Infinity -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    inf_json = b'{"streams": [{"width": Infinity}]}'
    fake_proc = FakePopen(stdout_data=inf_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*hằng số không chuẩn"):
        acquire_local_video_header(sample_video_file)


def test_container_no_video_stream(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra container chỉ có luồng âm thanh hoặc ảnh bìa -> ValueError(ERR_NO_VIDEO_STREAM)."""
    # 1. Chỉ có audio
    audio_only = {"streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac"}]}
    stdout_bytes = json.dumps(audio_only).encode("utf-8")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        acquire_local_video_header(sample_video_file)

    # 2. Luồng video là cover / attached_pic
    cover_only = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
            }
        ]
    }
    stdout_cover = json.dumps(cover_only).encode("utf-8")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_cover))

    with pytest.raises(ValueError, match="ERR_NO_VIDEO_STREAM"):
        acquire_local_video_header(sample_video_file)


# ==============================================================================
# Group 8: TOCTOU Source Mutation Protection
# ==============================================================================

def test_source_mutation_deleted_during_probe(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị xóa trong lúc probe -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    def on_terminate() -> None:
        if sample_video_file.exists():
            sample_video_file.unlink()

    fake_proc = FakePopen(stdout_data=stdout_bytes)
    # Xóa file ngay sau khi Popen khởi tạo xong
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    def mock_popen(*a: Any, **k: Any) -> FakePopen:
        sample_video_file.unlink()
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*xóa"):
        acquire_local_video_header(sample_video_file)


def test_source_mutation_replaced_with_symlink(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị thay thế bằng symlink trong lúc probe -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    other_file = sample_video_file.parent / "other_target.mp4"
    other_file.write_bytes(b"target")

    def mock_popen(*a: Any, **k: Any) -> FakePopen:
        sample_video_file.unlink()
        sample_video_file.symlink_to(other_file)
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*symlink"):
        acquire_local_video_header(sample_video_file)


def test_source_mutation_size_changed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị thay đổi kích thước trong lúc probe -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    def mock_popen(*a: Any, **k: Any) -> FakePopen:
        # Ghi thêm dữ liệu để thay đổi size
        with open(sample_video_file, "ab") as f:
            f.write(b"EXTRA_APPENDED_BYTES")
        return FakePopen(stdout_data=stdout_bytes)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*sửa đổi"):
        acquire_local_video_header(sample_video_file)


def test_source_mutation_ctime_only_changed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị thay đổi ctime (chỉ ctime_ns khác biệt) -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    orig_lstat = os.lstat
    call_count = 0

    def mock_lstat(p: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal call_count
        call_count += 1
        st = orig_lstat(p, *args, **kwargs)
        if call_count > 1:
            # Lần gọi thứ 2 sau probe: sửa ctime_ns
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    int(st.st_atime),
                    int(st.st_mtime),
                    int(st.st_ctime) + 10,
                ),
                {
                    "st_atime_ns": st.st_atime_ns,
                    "st_mtime_ns": st.st_mtime_ns,
                    "st_ctime_ns": st.st_ctime_ns + 10_000_000_000,
                },
            )
        return st

    monkeypatch.setattr("os.lstat", mock_lstat)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*sửa đổi hoặc tráo đổi"):
        acquire_local_video_header(sample_video_file)


# ==============================================================================
# Group 9: DTO Constructors, Format Duration & Hostile Protection
# ==============================================================================

def test_source_freshness_stat_validation_and_hostility() -> None:
    """Kiểm tra SourceFreshnessStat: int >= 0 cho size/dev/ino, signed int cho mtime/ctime, hostile protection."""
    # 1. Hợp lệ danh định
    stat1 = SourceFreshnessStat(
        file_size_bytes=1024,
        mtime_ns=-500000000,  # Thời gian trước 1970 được phép âm
        ctime_ns=1700000000000,
        device_id=1,
        inode_number=100,
    )
    assert stat1.file_size_bytes == 1024
    assert stat1.mtime_ns == -500000000

    # 2. Từ chối bool
    for fld in ("file_size_bytes", "mtime_ns", "ctime_ns", "device_id", "inode_number"):
        kwargs: dict[str, Any] = {
            "file_size_bytes": 100,
            "mtime_ns": 100,
            "ctime_ns": 100,
            "device_id": 1,
            "inode_number": 1,
        }
        kwargs[fld] = True
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
            SourceFreshnessStat(**kwargs)

    # 3. Từ chối âm cho file_size_bytes, device_id, inode_number
    for fld in ("file_size_bytes", "device_id", "inode_number"):
        kwargs = {
            "file_size_bytes": 100,
            "mtime_ns": 100,
            "ctime_ns": 100,
            "device_id": 1,
            "inode_number": 1,
        }
        kwargs[fld] = -1
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
            SourceFreshnessStat(**kwargs)

    # 4. Hostile object với __repr__/__str__ ném lỗi
    hostile = HostileReprObject()
    kwargs = {
        "file_size_bytes": hostile,
        "mtime_ns": 100,
        "ctime_ns": 100,
        "device_id": 1,
        "inode_number": 1,
    }
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        SourceFreshnessStat(**kwargs)  # type: ignore

    # 5. Native huge int (10**5000)
    huge_int = 10**5000
    stat_huge = SourceFreshnessStat(
        file_size_bytes=huge_int,
        mtime_ns=0,
        ctime_ns=0,
        device_id=0,
        inode_number=0,
    )
    assert stat_huge.file_size_bytes == huge_int


def test_local_header_acquisition_result_validation() -> None:
    """Kiểm tra LocalHeaderAcquisitionResult: kiểm tra độc lập tại constructor không I/O."""
    valid_stat = SourceFreshnessStat(100, 100, 100, 1, 1)
    valid_hdr = VideoStreamHeader(
        stream_index=0,
        codec_name="h264",
        r_frame_rate=RationalFPS(30, 1),
        avg_frame_rate=RationalFPS(30, 1),
        reported_nb_frames=100,
        time_base=Fraction(1, 30),
    )

    # 1. Danh định thành công
    res = LocalHeaderAcquisitionResult(
        file_path="/media/video.mp4",
        video_header=valid_hdr,
        format_duration_sec=Fraction(10, 1),
        freshness=valid_stat,
    )
    assert res.format_duration_sec == Fraction(10, 1)

    # 2. format_duration_sec là None thành công
    res_none = LocalHeaderAcquisitionResult(
        file_path="/media/video.mp4",
        video_header=valid_hdr,
        format_duration_sec=None,
        freshness=valid_stat,
    )
    assert res_none.format_duration_sec is None

    # 3. file_path sai kiểu hoặc rỗng hoặc NUL
    for p in (123, None, "", "test\x00video.mp4"):
        with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
            LocalHeaderAcquisitionResult(
                file_path=p,  # type: ignore
                video_header=valid_hdr,
                format_duration_sec=None,
                freshness=valid_stat,
            )

    # 4. video_header không phải VideoStreamHeader
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        LocalHeaderAcquisitionResult(
            file_path="/media/video.mp4",
            video_header={"not": "header"},  # type: ignore
            format_duration_sec=None,
            freshness=valid_stat,
        )

    # 5. freshness không phải SourceFreshnessStat
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT"):
        LocalHeaderAcquisitionResult(
            file_path="/media/video.mp4",
            video_header=valid_hdr,
            format_duration_sec=None,
            freshness={"not": "stat"},  # type: ignore
        )

    # 6. format_duration_sec <= 0 hoặc sai kiểu
    for bad_dur in (Fraction(0, 1), Fraction(-10, 1), 10.5, "10.5", True):
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            LocalHeaderAcquisitionResult(
                file_path="/media/video.mp4",
                video_header=valid_hdr,
                format_duration_sec=bad_dur,  # type: ignore
                freshness=valid_stat,
            )


def test_parse_format_duration_exhaustive() -> None:
    """Kiểm tra chi tiết hàm phân tích format.duration theo hợp đồng lexical ASCII decimal nghiêm ngặt."""
    # 1. Hợp lệ trả về Fraction
    assert _parse_format_duration({"duration": 10}) == Fraction(10, 1)
    assert _parse_format_duration({"duration": 12.5}) == Fraction(25, 2)
    assert _parse_format_duration({"duration": "12.5"}) == Fraction(25, 2)
    assert _parse_format_duration({"duration": "29.97"}) == Fraction(2997, 100)

    # 2. Thiếu / None duration / 0 / N/A -> None
    assert _parse_format_duration({}) is None
    assert _parse_format_duration({"duration": None}) is None
    assert _parse_format_duration({"duration": "N/A"}) is None
    assert _parse_format_duration({"duration": 0}) is None
    assert _parse_format_duration({"duration": 0.0}) is None
    assert _parse_format_duration({"duration": "0"}) is None
    assert _parse_format_duration({"duration": "0.0"}) is None

    # 3. Format là None hoặc sai kiểu container -> ERR_INVALID_PROBE_PAYLOAD
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*format không được là null"):
        _parse_format_duration(None)
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*format phải là Mapping"):
        _parse_format_duration("not_a_mapping")

    # 4. Giá trị không hợp lệ -> ERR_INVALID_VIDEO_METADATA
    invalid_durations = [
        True,
        False,
        -1,
        -1.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        "",
        "   ",
        "invalid_text",
        [10],
        "1/2",           # Non-decimal fraction string rejected
        "30000/1001",    # Non-decimal fraction string rejected (test correction to binding spec)
        "1e10000",       # Scientific exponent rejected
        "+2",            # Signed number rejected
        "-0",            # Signed zero rejected
        " 2 ",           # Whitespace padding rejected
        "٢.٥",           # Unicode Arabic-Indic digits rejected
        "2_0",           # Underscores rejected
        "0/1",           # Zero fraction string rejected
    ]
    for d in invalid_durations:
        with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA"):
            _parse_format_duration({"duration": d})

    # Vượt quá giới hạn text 1024 bytes -> ERR_PROBE_METADATA_LIMIT
    with pytest.raises(ValueError, match="ERR_PROBE_METADATA_LIMIT"):
        _parse_format_duration({"duration": "A" * 2000})

    # 5. Native huge int duration
    huge_dur = 10**400
    res_frac = _parse_format_duration({"duration": huge_dur})
    assert res_frac == Fraction(huge_dur, 1)


def test_stdout_json_deeply_nested_recursion_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra JSON lồng nhau quá sâu (RecursionError) -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    deep_json = (b'{"a":' * 2500) + b'1' + (b'}' * 2500)
    fake_proc = FakePopen(stdout_data=deep_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
        acquire_local_video_header(sample_video_file)


def test_stdout_json_huge_int_string_limit_rejected(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra số nguyên vượt giới hạn phân tích cú pháp chuỗi integer (>4300 chữ số) -> ValueError(ERR_INVALID_PROBE_PAYLOAD)."""
    huge_int_json = b'{"streams": [{"width": ' + (b'9' * 5000) + b'}]}'
    fake_proc = FakePopen(stdout_data=huge_int_json)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: fake_proc)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD"):
        acquire_local_video_header(sample_video_file)


def test_source_mutation_device_id_changed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị thay đổi device_id trong lúc probe -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    orig_lstat = os.lstat
    call_count = 0

    def mock_lstat(p: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal call_count
        call_count += 1
        st = orig_lstat(p, *args, **kwargs)
        if call_count > 1:
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev + 99,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    int(st.st_atime),
                    int(st.st_mtime),
                    int(st.st_ctime),
                ),
                {
                    "st_atime_ns": st.st_atime_ns,
                    "st_mtime_ns": st.st_mtime_ns,
                    "st_ctime_ns": st.st_ctime_ns,
                },
            )
        return st

    monkeypatch.setattr("os.lstat", mock_lstat)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*sửa đổi hoặc tráo đổi"):
        acquire_local_video_header(sample_video_file)


def test_source_mutation_inode_number_changed(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra tệp bị thay đổi inode_number trong lúc probe -> ValueError(ERR_SOURCE_MUTATED)."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    orig_lstat = os.lstat
    call_count = 0

    def mock_lstat(p: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal call_count
        call_count += 1
        st = orig_lstat(p, *args, **kwargs)
        if call_count > 1:
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino + 9999,
                    st.st_dev,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    int(st.st_atime),
                    int(st.st_mtime),
                    int(st.st_ctime),
                ),
                {
                    "st_atime_ns": st.st_atime_ns,
                    "st_mtime_ns": st.st_mtime_ns,
                    "st_ctime_ns": st.st_ctime_ns,
                },
            )
        return st

    monkeypatch.setattr("os.lstat", mock_lstat)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    with pytest.raises(ValueError, match="ERR_SOURCE_MUTATED.*sửa đổi hoặc tráo đổi"):
        acquire_local_video_header(sample_video_file)


# ==============================================================================
# Group 10: G06-06C2c1-R1 Lifecycle Ownership, Boundary & Strict Stat Tests
# ==============================================================================

def test_reader_start_failure_ownership(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra khởi chạy reader thất bại -> tiến trình con được thu hồi dứt điểm và đóng pipe."""
    proc = FakePopen(stdout_data=b"{}", poll_sequence=[None, None, 0])

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)

    # Giả lập lỗi khi start reader
    def mock_start(self: Any) -> None:
        raise RuntimeError("cannot start reader")

    monkeypatch.setattr(_BoundedPipeReader, "start", mock_start)

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*cannot start reader"):
        acquire_local_video_header(sample_video_file)

    # Xác nhận tiến trình con đã bị terminate/reap và pipe đã đóng
    assert proc.terminate_called >= 1 or proc.wait_called >= 1
    assert proc.stdout.closed
    assert proc.stderr.closed
    assert len([t for t in threading.enumerate() if "ffprobe" in t.name and t.is_alive()]) == 0


def test_late_pipe_error_after_poll_before_join(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra lỗi đọc pipe muộn phát sinh ngay sau khi poll() trả về 0 -> bắt buộc từ chối thành công."""
    payload = make_valid_ffprobe_payload()
    valid_json_bytes = json.dumps(payload).encode("utf-8")

    ev_first_read_done = threading.Event()
    ev_child_polled = threading.Event()

    class FakeLatePipeStream(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self._call_count = 0

        def read(self, n: int = -1) -> bytes:
            self._call_count += 1
            if self._call_count == 1:
                chunk = super().read(n)
                ev_first_read_done.set()
                return chunk
            # Lần đọc thứ 2: chờ cho tới khi poll() đã được supervisor gọi và trả về 0
            ev_child_polled.wait(timeout=2.0)
            raise OSError("Simulated late pipe read failure")

    stdout_stream = FakeLatePipeStream(valid_json_bytes)

    class FakeLatePollPopen(FakePopen):
        def poll(self) -> Optional[int]:
            # Chỉ trả về 0 khi reader đã hoàn thành chunk đầu tiên
            if ev_first_read_done.is_set():
                self.returncode = 0
                ev_child_polled.set()
                return 0
            return None

    proc = FakeLatePollPopen(stdout_stream=stdout_stream)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*Simulated late pipe read failure"):
        acquire_local_video_header(sample_video_file)

    assert len([t for t in threading.enumerate() if "ffprobe" in t.name and t.is_alive()]) == 0


def test_cleanup_child_and_readers_exhaustive() -> None:
    """Kiểm tra toàn diện các nhánh thu hồi của _cleanup_child_and_readers."""
    # 1. Terminate timeout -> kill -> wait thành công
    proc1 = FakePopen(poll_sequence=[None])
    wait_calls = 0

    def mock_wait(timeout: Optional[float] = None) -> int:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=0.5)
        return 0

    proc1.wait = mock_wait  # type: ignore[assignment]
    _cleanup_child_and_readers(proc1, None, None)
    assert proc1.terminate_called == 1
    assert proc1.kill_called == 1

    # 2. Đóng pipe lỗi -> ném ERR_PROBE_CLEANUP_FAILED
    class BadPipeStream(io.BytesIO):
        def close(self) -> None:
            raise OSError("disk IO error on close")

    proc2 = FakePopen(stdout_stream=BadPipeStream())
    with pytest.raises(RuntimeError, match="ERR_PROBE_CLEANUP_FAILED.*disk IO error"):
        _cleanup_child_and_readers(proc2, None, None)

    # 3. Reader chưa start (ident is None) -> không gọi join và không ném RuntimeError
    unstarted_reader = _BoundedPipeReader(io.BytesIO(b""), 1024, "unstarted")
    assert getattr(unstarted_reader, "ident", None) is None
    proc3 = FakePopen()
    _cleanup_child_and_readers(proc3, unstarted_reader, None)

    # 4. Reader join timeout (vẫn alive) -> ném ERR_PROBE_CLEANUP_FAILED
    class StuckReader:
        def __init__(self) -> None:
            self.ident = 12345
            self.name = "stuck_reader"

        def join(self, timeout: Optional[float] = None) -> None:
            pass

        def is_alive(self) -> bool:
            return True

    proc4 = FakePopen()
    with pytest.raises(RuntimeError, match="ERR_PROBE_CLEANUP_FAILED.*stuck_reader không thể kết thúc"):
        _cleanup_child_and_readers(proc4, StuckReader(), None)  # type: ignore[arg-type]

    # 5. Tiến trình đã kết thúc trước đó (poll() != None) -> không gọi terminate hay kill
    proc5 = FakePopen(poll_sequence=[0])
    _cleanup_child_and_readers(proc5, None, None)
    assert proc5.terminate_called == 0
    assert proc5.kill_called == 0


def test_cancellation_callback_strict_bool_returns(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra cancelled_fn bắt buộc trả về strict bool (False tiếp tục, True hủy), từ chối truthiness."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    # 1. Các kiểu trả về sai -> ERR_PROBE_EXECUTION_FAILED
    class RaisingBool:
        def __bool__(self) -> bool:
            raise AssertionError("Truthiness __bool__ không được phép bị triệu gọi!")

    wrong_returns = [
        None,
        0,
        1,
        "cancel",
        [True],
        RaisingBool(),
    ]

    for wrong_val in wrong_returns:
        with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*strict bool"):
            acquire_local_video_header(sample_video_file, cancelled_fn=lambda val=wrong_val: val)  # type: ignore[return-value]

    # 2. Sai kiểu trả về trong vòng lặp giám sát (sau spawn) -> cleanup và ERR_PROBE_EXECUTION_FAILED
    call_idx = 0
    proc = FakePopen(stdout_data=stdout_bytes, poll_sequence=[None, None, 0])
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)

    def callback_wrong_after_spawn() -> Any:
        nonlocal call_idx
        call_idx += 1
        if call_idx > 1:
            return "cancel"  # Sai kiểu sau spawn
        return False

    with pytest.raises(RuntimeError, match="ERR_PROBE_EXECUTION_FAILED.*strict bool"):
        acquire_local_video_header(sample_video_file, cancelled_fn=callback_wrong_after_spawn)

    assert proc.stdout.closed


def test_payload_boundary_format_controls(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra các trường hợp biên của payload format tại acquire_local_video_header."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")

    # 1. format vắng mặt -> format_duration_sec là None
    payload_no_fmt = make_valid_ffprobe_payload()
    payload_no_fmt.pop("format", None)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=json.dumps(payload_no_fmt).encode("utf-8")))
    res_no_fmt = acquire_local_video_header(sample_video_file)
    assert res_no_fmt.format_duration_sec is None

    # 2. format tường minh là null -> ERR_INVALID_PROBE_PAYLOAD
    payload_null_fmt = make_valid_ffprobe_payload()
    payload_null_fmt["format"] = None
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=json.dumps(payload_null_fmt).encode("utf-8")))
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*format không được là null"):
        acquire_local_video_header(sample_video_file)

    # 3. format không phải Mapping -> ERR_INVALID_PROBE_PAYLOAD
    payload_bad_fmt = make_valid_ffprobe_payload()
    payload_bad_fmt["format"] = "not_a_mapping"
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=json.dumps(payload_bad_fmt).encode("utf-8")))
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_PAYLOAD.*format phải là Mapping"):
        acquire_local_video_header(sample_video_file)

    # 4. format là Mapping rỗng hoặc duration là null -> format_duration_sec là None
    for dur_val in [{}, {"duration": None}]:
        payload_dur_none = make_valid_ffprobe_payload()
        payload_dur_none["format"] = dur_val
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=json.dumps(payload_dur_none).encode("utf-8")))
        res = acquire_local_video_header(sample_video_file)
        assert res.format_duration_sec is None


def test_result_ownership_and_revalidation(sample_video_file: Path) -> None:
    """Kiểm tra LocalHeaderAcquisitionResult sở hữu bản sao độc lập (không alias) và xác thực nghiêm ngặt."""
    header = VideoStreamHeader(
        stream_index=0,
        codec_name="h264",
        r_frame_rate=RationalFPS(30000, 1001),
        avg_frame_rate=RationalFPS(30000, 1001),
        reported_nb_frames=300,
        time_base=Fraction(1, 90000),
    )
    stat_val = SourceFreshnessStat(
        file_size_bytes=1024,
        mtime_ns=1700000000000,
        ctime_ns=1700000000000,
        device_id=1,
        inode_number=100,
    )

    abs_path = str(sample_video_file.resolve())

    # 1. Alias isolation: sửa đối tượng gốc không làm thay đổi result
    res = LocalHeaderAcquisitionResult(
        file_path=abs_path,
        video_header=header,
        format_duration_sec=Fraction(10, 1),
        freshness=stat_val,
    )

    object.__setattr__(header, "stream_index", 999)
    assert res.video_header.stream_index == 0

    object.__setattr__(stat_val, "mtime_ns", 999999999)
    assert res.freshness.mtime_ns == 1700000000000

    # 2. Revalidate: header bị lỗi bên trong bị từ chối có kiểm soát
    corrupted_header = VideoStreamHeader(
        stream_index=0,
        codec_name="h264",
        r_frame_rate=None,
        avg_frame_rate=None,
        reported_nb_frames=None,
        time_base=None,
    )
    object.__setattr__(corrupted_header, "stream_index", -5)

    with pytest.raises(ValueError, match="ERR_INVALID_VIDEO_METADATA.*stream_index"):
        LocalHeaderAcquisitionResult(
            file_path=abs_path,
            video_header=corrupted_header,
            format_duration_sec=None,
            freshness=stat_val,
        )

    # 3. Revalidate: freshness bị lỗi bên trong bị từ chối có kiểm soát
    corrupted_stat = SourceFreshnessStat(
        file_size_bytes=100,
        mtime_ns=100,
        ctime_ns=100,
        device_id=1,
        inode_number=1,
    )
    object.__setattr__(corrupted_stat, "file_size_bytes", -10)

    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT.*file_size_bytes"):
        LocalHeaderAcquisitionResult(
            file_path=abs_path,
            video_header=res.video_header,
            format_duration_sec=None,
            freshness=corrupted_stat,
        )

    # 4. file_path phải là đường dẫn tuyệt đối theo OS
    with pytest.raises(ValueError, match="ERR_INVALID_PROBE_ARGUMENT.*tuyệt đối"):
        LocalHeaderAcquisitionResult(
            file_path="relative/path/clip.mp4",
            video_header=res.video_header,
            format_duration_sec=None,
            freshness=stat_val,
        )


def test_source_freshness_ns_platform_evidence(sample_video_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra chụp stat bắt buộc trường nanosecond thực tế, loại trừ mọi fallback sang float."""
    payload = make_valid_ffprobe_payload()
    stdout_bytes = json.dumps(payload).encode("utf-8")

    orig_lstat = os.lstat

    # 1. Hệ thống / mock thiếu trường st_mtime_ns -> ERR_SOURCE_STAT_FAILED trước khi spawn Popen
    popen_called = False

    def mock_popen(*a: Any, **k: Any) -> FakePopen:
        nonlocal popen_called
        popen_called = True
        return FakePopen(stdout_data=stdout_bytes)

    def mock_lstat_missing_ns(p: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
        st = orig_lstat(p, *args, **kwargs)
        # 10-tuple thuần túy không có dict nanosecond
        return os.stat_result((
            st.st_mode,
            st.st_ino,
            st.st_dev,
            st.st_nlink,
            st.st_uid,
            st.st_gid,
            st.st_size,
            int(st.st_atime),
            int(st.st_mtime),
            int(st.st_ctime),
        ))

    monkeypatch.setattr("os.lstat", mock_lstat_missing_ns)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    with pytest.raises(OSError, match="ERR_SOURCE_STAT_FAILED"):
        acquire_local_video_header(sample_video_file)

    # Xác nhận lỗi xảy ra trước khi spawn Popen
    assert popen_called is False

    # 2. Trường nanosecond mang kiểu bool hoặc None -> ERR_SOURCE_STAT_FAILED
    class MockStatResultBadType:
        st_mode = stat.S_IFREG | 0o644
        st_ino = 100
        st_dev = 1
        st_size = 1024
        st_mtime_ns = True  # bool thay vì int
        st_ctime_ns = 1700000000000

    monkeypatch.setattr("os.lstat", lambda *a, **k: MockStatResultBadType())
    with pytest.raises(OSError, match="ERR_SOURCE_STAT_FAILED.*st_mtime_ns"):
        acquire_local_video_header(sample_video_file)

    # 3. Lỗi stat sau khi probe hoàn thành -> ERR_SOURCE_STAT_FAILED
    call_count = 0

    def mock_lstat_after_error(p: str | Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise PermissionError("EACCES: permission denied")
        return orig_lstat(p, *args, **kwargs)

    monkeypatch.setattr("os.lstat", mock_lstat_after_error)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakePopen(stdout_data=stdout_bytes))

    with pytest.raises(OSError, match="ERR_SOURCE_STAT_FAILED.*không thể đọc lại"):
        acquire_local_video_header(sample_video_file)


