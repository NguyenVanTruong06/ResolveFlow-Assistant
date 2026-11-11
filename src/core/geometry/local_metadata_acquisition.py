"""src/core/geometry/local_metadata_acquisition.py
Thu thập siêu dữ liệu tiêu đề tệp video cục bộ có giới hạn tài nguyên nghiêm ngặt (G06-06C2b-FINAL-THEN-06C2c1-I).

Quy tắc kiến trúc:
1. Pure standard library (dataclasses, fractions, json, os, pathlib, shutil, stat, subprocess, sys, threading, time, typing)
   + các hợp đồng và parser đã nghiệm thu từ source_frame_evidence_contracts và source_frame_evidence_parser.
2. Không import Qt (PySide6), model, Resolve API, ClipManifest, autocut, hay cache_manager.
3. Không biến đổi môi trường toàn cục (không sửa PATH, cwd, os.environ, hay gọi static_ffmpeg.add_paths).
4. Phân loại bằng chứng: HEADER_REPORTED (siêu dữ liệu do container ffprobe báo cáo; không phải bằng chứng CFR hay proof nội dung).
5. Giao thức subprocess bị chặn trần: local regular file only, shell=False, stdin=DEVNULL, streaming pipe reader giới hạn 512KB/64KB,
   deadline đồng hồ đơn điệu, thu hồi tiến trình và join reader sạch sẽ, bảo vệ TOCTOU hai đầu stat.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, IO, Optional, Union

from src.utils.subprocess_utils import clean_media_subprocess_env
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader
from src.core.geometry.source_frame_evidence_parser import (
    _check_bounded_text,
    parse_ffprobe_video_header,
)

MAX_PROBE_STDOUT_BYTES: int = 512 * 1024  # 512 KB
MAX_PROBE_STDERR_BYTES: int = 64 * 1024   # 64 KB
READ_CHUNK_BYTES: int = 4096              # 4 KB


@dataclass(frozen=True)
class SourceFreshnessStat:
    """Ảnh chụp trạng thái hệ thống tệp của nguồn video trước và sau khi probe."""
    file_size_bytes: int
    mtime_ns: int
    ctime_ns: int
    device_id: int
    inode_number: int

    def __post_init__(self) -> None:
        for field_name in ("file_size_bytes", "device_id", "inode_number"):
            val = getattr(self, field_name)
            if type(val) is not int or type(val) is bool or val < 0:
                raise ValueError(
                    f"ERR_INVALID_PROBE_ARGUMENT: {field_name} phải là int >= 0 (không phải bool), "
                    f"nhận: {type(val).__name__}"
                )
        for field_name in ("mtime_ns", "ctime_ns"):
            val = getattr(self, field_name)
            if type(val) is not int or type(val) is bool:
                raise ValueError(
                    f"ERR_INVALID_PROBE_ARGUMENT: {field_name} phải là int (không phải bool), "
                    f"nhận: {type(val).__name__}"
                )


_DURATION_DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$", re.ASCII)


@dataclass(frozen=True)
class LocalHeaderAcquisitionResult:
    """Kết quả thu thập siêu dữ liệu tiêu đề cục bộ do container báo cáo (HEADER_REPORTED).

    Quy định:
    - Đúng 4 trường chuẩn tắc: file_path, video_header, format_duration_sec, freshness.
    - Không chứa cờ is_cfr, verified, is_fps_known, is_duration_known.
    - format_duration_sec bắt buộc là Fraction > 0 hoặc None.
    - Validation toàn diện tại constructor mà không thực hiện I/O.
    """
    file_path: str
    video_header: VideoStreamHeader
    format_duration_sec: Optional[Fraction]
    freshness: SourceFreshnessStat

    def __post_init__(self) -> None:
        if type(self.file_path) is not str or not self.file_path or "\x00" in self.file_path:
            raise ValueError(
                f"ERR_INVALID_PROBE_ARGUMENT: file_path phải là str không rỗng và không chứa NUL, "
                f"nhận: {type(self.file_path).__name__}"
            )
        if not os.path.isabs(self.file_path):
            raise ValueError(
                f"ERR_INVALID_PROBE_ARGUMENT: file_path phải là đường dẫn tuyệt đối, nhận: {self.file_path!r}"
            )

        if not isinstance(self.video_header, VideoStreamHeader):
            raise ValueError(
                f"ERR_INVALID_PROBE_ARGUMENT: video_header phải là instance của VideoStreamHeader, "
                f"nhận: {type(self.video_header).__name__ if self.video_header is not None else 'None'}"
            )

        # Tái tạo và xác thực bản sao độc lập (owned copy) của VideoStreamHeader để cách ly alias
        try:
            owned_header = VideoStreamHeader(
                stream_index=self.video_header.stream_index,
                codec_name=self.video_header.codec_name,
                r_frame_rate=self.video_header.r_frame_rate,
                avg_frame_rate=self.video_header.avg_frame_rate,
                reported_nb_frames=self.video_header.reported_nb_frames,
                time_base=self.video_header.time_base,
            )
        except (ValueError, TypeError, AttributeError) as err:
            msg = str(err)
            if not msg.startswith("ERR_"):
                raise ValueError(f"ERR_INVALID_VIDEO_METADATA: video_header không hợp lệ: {err}") from err
            raise
        object.__setattr__(self, "video_header", owned_header)

        if not isinstance(self.freshness, SourceFreshnessStat):
            raise ValueError(
                f"ERR_INVALID_PROBE_ARGUMENT: freshness phải là instance của SourceFreshnessStat, "
                f"nhận: {type(self.freshness).__name__ if self.freshness is not None else 'None'}"
            )

        # Tái tạo và xác thực bản sao độc lập (owned copy) của SourceFreshnessStat để cách ly alias
        try:
            owned_freshness = SourceFreshnessStat(
                file_size_bytes=self.freshness.file_size_bytes,
                mtime_ns=self.freshness.mtime_ns,
                ctime_ns=self.freshness.ctime_ns,
                device_id=self.freshness.device_id,
                inode_number=self.freshness.inode_number,
            )
        except (ValueError, TypeError, AttributeError) as err:
            msg = str(err)
            if not msg.startswith("ERR_"):
                raise ValueError(f"ERR_INVALID_PROBE_ARGUMENT: freshness không hợp lệ: {err}") from err
            raise
        object.__setattr__(self, "freshness", owned_freshness)

        if self.format_duration_sec is not None:
            if not isinstance(self.format_duration_sec, Fraction):
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: format_duration_sec phải là Fraction hoặc None, "
                    f"nhận: {type(self.format_duration_sec).__name__}"
                )
            num = getattr(self.format_duration_sec, "numerator", None)
            den = getattr(self.format_duration_sec, "denominator", None)
            if type(num) is not int or type(num) is bool:
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: format_duration_sec numerator phải là int, "
                    f"nhận: {type(num).__name__ if num is not None else 'None'}"
                )
            if type(den) is not int or type(den) is bool:
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: format_duration_sec denominator phải là int, "
                    f"nhận: {type(den).__name__ if den is not None else 'None'}"
                )
            if num <= 0 or den <= 0:
                raise ValueError(
                    "ERR_INVALID_VIDEO_METADATA: format_duration_sec phải là Fraction dương (> 0)"
                )
            object.__setattr__(self, "format_duration_sec", Fraction(num, den))


def _parse_format_duration(format_data: Any) -> Optional[Fraction]:
    """Phân tích thời lượng container từ trường format của ffprobe."""
    if format_data is None:
        raise ValueError("ERR_INVALID_PROBE_PAYLOAD: format không được là null (None)")
    if not isinstance(format_data, Mapping):
        raise ValueError(
            f"ERR_INVALID_PROBE_PAYLOAD: format phải là Mapping, "
            f"nhận: {type(format_data).__name__}"
        )
    if "duration" not in format_data:
        return None

    raw_dur = format_data["duration"]
    if raw_dur is None:
        return None

    if type(raw_dur) is bool:
        raise ValueError("ERR_INVALID_VIDEO_METADATA: format.duration không được là bool")

    if type(raw_dur) is int:
        if raw_dur < 0:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: format.duration không được âm")
        if raw_dur == 0:
            return None
        return Fraction(raw_dur, 1)

    if type(raw_dur) is float:
        if math.isnan(raw_dur) or math.isinf(raw_dur):
            raise ValueError("ERR_INVALID_VIDEO_METADATA: format.duration không được là NaN hoặc Inf")
        if raw_dur < 0:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: format.duration không được âm")
        if raw_dur == 0.0:
            return None
        return Fraction(str(raw_dur))

    if type(raw_dur) is str:
        _check_bounded_text(raw_dur, "format.duration")
        if raw_dur == "N/A":
            return None
        if not _DURATION_DECIMAL_RE.fullmatch(raw_dur):
            raise ValueError(
                f"ERR_INVALID_VIDEO_METADATA: format.duration chuỗi phải là định dạng số thập phân ASCII không dấu hợp lệ, "
                f"nhận: {raw_dur!r}"
            )
        frac = Fraction(raw_dur)
        if frac == 0:
            return None
        return frac

    raise ValueError(
        f"ERR_INVALID_VIDEO_METADATA: format.duration phải là int, float, str hoặc None, "
        f"nhận: {type(raw_dur).__name__}"
    )


class _BoundedPipeReader(threading.Thread):
    """Luồng đọc streaming có giới hạn chặn trần nghiêm ngặt cho pipe stdout/stderr."""
    def __init__(self, stream: Optional[IO[bytes]], max_bytes: int, name: str) -> None:
        super().__init__(name=name, daemon=False)
        self.stream = stream
        self.max_bytes = max_bytes
        self.chunks: list[bytes] = []
        self.total_bytes = 0
        self.overflow = False
        self.exception: Optional[Exception] = None
        self.clean_eof = False
        self.completed = False

    def run(self) -> None:
        if self.stream is None:
            self.clean_eof = True
            self.completed = True
            return
        try:
            while True:
                chunk = self.stream.read(READ_CHUNK_BYTES)
                if not chunk:
                    self.clean_eof = True
                    break
                if self.total_bytes + len(chunk) > self.max_bytes:
                    self.overflow = True
                    break
                self.chunks.append(chunk)
                self.total_bytes += len(chunk)
        except Exception as exc:
            self.exception = exc
        finally:
            self.completed = True

    def get_data(self) -> bytes:
        return b"".join(self.chunks)


def _check_cancellation(cancelled_fn: Optional[Callable[[], bool]]) -> None:
    """Kiểm tra điều kiện hủy từ caller và fail-closed nếu callback ném ngoại lệ hoặc trả về sai kiểu."""
    if cancelled_fn is not None:
        try:
            res = cancelled_fn()
        except Exception as err:
            raise RuntimeError("ERR_PROBE_EXECUTION_FAILED: cancelled_fn ném ngoại lệ") from err
        if type(res) is not bool:
            raise RuntimeError(
                f"ERR_PROBE_EXECUTION_FAILED: cancelled_fn phải trả về strict bool (True/False), "
                f"nhận: {type(res).__name__}"
            )
        if res is True:
            raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ probe bị hủy bởi caller")


def _cleanup_child_and_readers(
    proc: subprocess.Popen,
    stdout_reader: Optional[_BoundedPipeReader] = None,
    stderr_reader: Optional[_BoundedPipeReader] = None,
    grace_sec: float = 0.5,
) -> None:
    """Thu hồi dứt điểm tiến trình con và join các luồng reader trong giới hạn thời gian finite."""
    cleanup_err: Optional[Exception] = None

    try:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=grace_sec)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                    proc.wait(timeout=grace_sec)
                except Exception as exc:
                    cleanup_err = exc
            if proc.poll() is None and cleanup_err is None:
                cleanup_err = RuntimeError("ERR_PROBE_CLEANUP_FAILED: tiến trình con không thể kết thúc sau kill")
    except Exception as exc:
        cleanup_err = exc

    # Đóng pipe streams để unblock readers nếu còn kẹt
    for pipe_stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
        if pipe_stream and not getattr(pipe_stream, "closed", True):
            try:
                pipe_stream.close()
            except Exception as exc:
                if cleanup_err is None:
                    cleanup_err = exc

    # Join reader threads an toàn (chỉ join nếu luồng đã được khởi chạy - ident is not None)
    for reader in (stdout_reader, stderr_reader):
        if reader is not None and getattr(reader, "ident", None) is not None:
            try:
                reader.join(timeout=grace_sec)
                if reader.is_alive() and cleanup_err is None:
                    cleanup_err = RuntimeError(
                        f"ERR_PROBE_CLEANUP_FAILED: {getattr(reader, 'name', 'reader')} không thể kết thúc sau thời gian chờ"
                    )
            except Exception as exc:
                if cleanup_err is None:
                    cleanup_err = exc

    if cleanup_err is not None:
        msg = str(cleanup_err)
        if msg.startswith("ERR_PROBE_CLEANUP_FAILED:"):
            raise cleanup_err
        raise RuntimeError(
            f"ERR_PROBE_CLEANUP_FAILED: không thể thu hồi hoàn toàn tiến trình con: {cleanup_err}"
        ) from cleanup_err


def _capture_source_freshness(path_str: str, *, is_after: bool = False) -> SourceFreshnessStat:
    """Chụp và xác thực nghiêm ngặt ảnh chụp trạng thái hệ thống tệp của nguồn video."""
    try:
        st = os.lstat(path_str)
    except FileNotFoundError:
        if is_after:
            raise ValueError("ERR_SOURCE_MUTATED: tệp nguồn đã bị xóa trong quá trình probe") from None
        raise FileNotFoundError(f"ERR_SOURCE_FILE_NOT_FOUND: không tìm thấy tệp nguồn {path_str}") from None
    except (OSError, PermissionError) as err:
        err_prefix = "không thể đọc lại thông tin tệp nguồn sau probe" if is_after else "không thể đọc thông tin tệp nguồn"
        raise OSError(f"ERR_SOURCE_STAT_FAILED: {err_prefix}: {err}") from err

    if stat.S_ISLNK(st.st_mode):
        if is_after:
            raise ValueError("ERR_SOURCE_MUTATED: tệp nguồn đã bị chuyển thành symlink sau khi probe")
        raise ValueError(f"ERR_INVALID_SOURCE_PATH: file_path là symlink, bị từ chối: {path_str}")

    if not stat.S_ISREG(st.st_mode):
        if is_after:
            raise ValueError("ERR_SOURCE_MUTATED: tệp nguồn đã bị thay đổi thành không phải regular file")
        raise ValueError("ERR_INVALID_SOURCE_PATH: tệp nguồn không phải regular file (thư mục, fifo, socket, device)")

    # Kiểm tra chặt chẽ sự tồn tại thực tế của trường nanosecond, loại bỏ hoàn toàn fallback sang float
    if not hasattr(st, "st_mtime_ns") or not hasattr(st, "st_ctime_ns"):
        raise OSError("ERR_SOURCE_STAT_FAILED: nền tảng không hỗ trợ hoặc thiếu trường st_mtime_ns/st_ctime_ns")

    st_mtime_ns = st.st_mtime_ns
    st_ctime_ns = st.st_ctime_ns
    st_size = getattr(st, "st_size", None)
    st_dev = getattr(st, "st_dev", None)
    st_ino = getattr(st, "st_ino", None)

    for val, name in (
        (st_size, "st_size"),
        (st_dev, "st_dev"),
        (st_ino, "st_ino"),
    ):
        if type(val) is not int or type(val) is bool or val < 0:
            raise OSError(
                f"ERR_SOURCE_STAT_FAILED: trường {name} của hệ thống tệp không hợp lệ: "
                f"nhận {type(val).__name__ if val is not None else 'None'}"
            )

    for val, name in (
        (st_mtime_ns, "st_mtime_ns"),
        (st_ctime_ns, "st_ctime_ns"),
    ):
        if type(val) is not int or type(val) is bool:
            raise OSError(
                f"ERR_SOURCE_STAT_FAILED: trường {name} của hệ thống tệp không hợp lệ: "
                f"nhận {type(val).__name__ if val is not None else 'None'}"
            )

    return SourceFreshnessStat(
        file_size_bytes=st_size,
        mtime_ns=st_mtime_ns,
        ctime_ns=st_ctime_ns,
        device_id=st_dev,
        inode_number=st_ino,
    )


def acquire_local_video_header(
    file_path: Union[str, Path],
    *,
    ffprobe_path: Optional[Union[str, Path]] = None,
    selected_stream_index: Optional[int] = None,
    timeout_sec: float = 5.0,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> LocalHeaderAcquisitionResult:
    """Đọc tiêu đề tệp video cục bộ qua ffprobe một cách an toàn và có giới hạn chặn trần.

    Bảo đảm:
    - Không sửa PATH, cwd, hay biến môi trường toàn cục.
    - Kiểm tra loại tệp cục bộ (regular file only; từ chối symlink cuối, URI/URL, directory, FIFO, socket, device).
    - Subprocess shell=False, stdin=DEVNULL, protocol_whitelist file, tách bạch argv.
    - Streaming pipes stdout (512KB) và stderr (64KB) chống tràn bộ nhớ.
    - Quản lý vòng đời tiến trình dứt điểm: timeout, cancellation, error đều terminate/kill và join readers sạch sẽ.
    - Kiểm tra độ tươi mới hai đầu stat (TOCTOU freshness guard).
    - Phân tích cú pháp qua parse_ffprobe_video_header đã nghiệm thu; báo cáo HEADER_REPORTED thuần túy.
    """
    # 1. Xác thực tham số đầu vào trước khi thực thi
    if not isinstance(file_path, (str, Path)):
        raise ValueError(
            f"ERR_INVALID_PROBE_ARGUMENT: file_path phải là str hoặc Path, "
            f"nhận: {type(file_path).__name__}"
        )
    path_str = str(file_path)
    if not path_str or path_str.isspace() or "\x00" in path_str:
        raise ValueError("ERR_INVALID_PROBE_ARGUMENT: file_path không được rỗng hoặc chứa NUL")

    # Từ chối mọi chuỗi mang định dạng giao thức mạng
    if "://" in path_str:
        raise ValueError("ERR_INVALID_SOURCE_PATH: file_path không được là URL hoặc URI giao thức mạng")

    # Xác thực timeout_sec
    if type(timeout_sec) is bool or not isinstance(timeout_sec, (int, float)):
        raise ValueError(
            f"ERR_INVALID_PROBE_ARGUMENT: timeout_sec phải là số thực hoặc nguyên dương, "
            f"nhận: {type(timeout_sec).__name__}"
        )
    # Kiểm tra chặn trần int lớn trước khi chuyển đổi float để tránh OverflowError
    try:
        if timeout_sec <= 0 or timeout_sec > 60:
            raise ValueError(
                "ERR_INVALID_PROBE_ARGUMENT: timeout_sec phải nằm trong khoảng (0, 60] hữu hạn"
            )
    except OverflowError:
        raise ValueError("ERR_INVALID_PROBE_ARGUMENT: timeout_sec vượt quá giới hạn biểu diễn") from None

    if isinstance(timeout_sec, float):
        if math.isnan(timeout_sec) or math.isinf(timeout_sec):
            raise ValueError("ERR_INVALID_PROBE_ARGUMENT: timeout_sec không được là NaN hoặc Inf")

    # Xác thực selected_stream_index
    if selected_stream_index is not None:
        if (
            type(selected_stream_index) is not int
            or type(selected_stream_index) is bool
            or selected_stream_index < 0
        ):
            raise ValueError(
                f"ERR_INVALID_STREAM_SELECTION: selected_stream_index phải là int >= 0 (không phải bool), "
                f"nhận: {type(selected_stream_index).__name__}"
            )

    # Xác thực cancelled_fn
    if cancelled_fn is not None and not callable(cancelled_fn):
        raise ValueError(
            f"ERR_INVALID_PROBE_ARGUMENT: cancelled_fn phải là callable hoặc None, "
            f"nhận: {type(cancelled_fn).__name__}"
        )

    # 2. Kiểm tra tính hợp lệ của tệp nguồn và chụp stat_before
    stat_before = _capture_source_freshness(path_str, is_after=False)
    canonical_path = os.path.abspath(path_str)

    # 3. Định vị binary ffprobe rõ ràng, không side-effect
    if ffprobe_path is not None:
        if not isinstance(ffprobe_path, (str, Path)):
            raise ValueError(
                f"ERR_INVALID_PROBE_ARGUMENT: ffprobe_path phải là str hoặc Path, "
                f"nhận: {type(ffprobe_path).__name__}"
            )
        bin_path = str(ffprobe_path)
        if not bin_path or bin_path.isspace() or "\x00" in bin_path:
            raise ValueError("ERR_INVALID_PROBE_ARGUMENT: ffprobe_path không được rỗng hoặc chứa NUL")
        if not os.path.exists(bin_path) or not os.path.isfile(bin_path):
            raise FileNotFoundError(f"ERR_FFPROBE_NOT_FOUND: binary ffprobe chỉ định không tồn tại: {bin_path}")
    else:
        located = shutil.which("ffprobe")
        if not located:
            raise FileNotFoundError("ERR_FFPROBE_NOT_FOUND: không tìm thấy binary ffprobe trong hệ thống")
        bin_path = located

    # 4. Kiểm tra hủy trước khi spawn tiến trình con
    _check_cancellation(cancelled_fn)

    # 5. Khởi tạo tiến trình con có kiểm soát
    cmd = [
        bin_path,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-protocol_whitelist", "file",
        "-i", canonical_path,
    ]

    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": clean_media_subprocess_env(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as err:
        raise RuntimeError("ERR_PROBE_EXECUTION_FAILED: không thể khởi chạy tiến trình ffprobe") from err

    # 6. Giám sát tiến trình và đọc streaming pipes đồng thời có giới hạn trần
    stdout_reader: Optional[_BoundedPipeReader] = None
    stderr_reader: Optional[_BoundedPipeReader] = None
    primary_failure: Optional[Exception] = None

    try:
        stdout_reader = _BoundedPipeReader(proc.stdout, MAX_PROBE_STDOUT_BYTES, "ffprobe-stdout-reader")
        stderr_reader = _BoundedPipeReader(proc.stderr, MAX_PROBE_STDERR_BYTES, "ffprobe-stderr-reader")
        stdout_reader.start()
        stderr_reader.start()

        deadline = time.monotonic() + timeout_sec

        while True:
            # Kiểm tra hủy trong suốt thời gian giám sát
            _check_cancellation(cancelled_fn)

            # Kiểm tra tràn giới hạn byte
            if stdout_reader.overflow or stderr_reader.overflow:
                raise ValueError("ERR_PROBE_METADATA_LIMIT: dữ liệu đầu ra từ ffprobe vượt quá giới hạn chặn trần")

            # Kiểm tra lỗi ngoại lệ từ luồng đọc
            if stdout_reader.exception is not None or stderr_reader.exception is not None:
                exc = stdout_reader.exception or stderr_reader.exception
                raise RuntimeError(f"ERR_PROBE_EXECUTION_FAILED: lỗi đọc pipe tiến trình: {exc}") from exc

            # Kiểm tra tiến trình kết thúc
            ret = proc.poll()
            if ret is not None:
                break

            # Kiểm tra quá hạn thời gian
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("ERR_PROBE_TIMEOUT: tiến trình ffprobe vượt quá thời gian chờ cho phép")

            sleep_step = min(0.02, max(0.001, deadline - now))
            time.sleep(sleep_step)

        # Chờ hai reader đọc nốt phần còn lại tới EOF trong khi tiếp tục giám sát deadline và lỗi
        drain_step = 0.01
        while stdout_reader.is_alive() or stderr_reader.is_alive():
            _check_cancellation(cancelled_fn)
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("ERR_PROBE_TIMEOUT: tiến trình ffprobe vượt quá thời gian chờ khi chờ luồng đọc kết thúc")
            if stdout_reader.overflow or stderr_reader.overflow:
                raise ValueError("ERR_PROBE_METADATA_LIMIT: dữ liệu đầu ra từ ffprobe vượt quá giới hạn chặn trần")
            if stdout_reader.exception is not None or stderr_reader.exception is not None:
                exc = stdout_reader.exception or stderr_reader.exception
                raise RuntimeError(f"ERR_PROBE_EXECUTION_FAILED: lỗi đọc pipe tiến trình: {exc}") from exc

            if stdout_reader.is_alive():
                stdout_reader.join(timeout=drain_step)
            if stderr_reader.is_alive():
                stderr_reader.join(timeout=drain_step)

        # Kiểm tra hủy ngay sau khi reader threads kết thúc
        _check_cancellation(cancelled_fn)

        # Kiểm tra quá hạn deadline
        if time.monotonic() >= deadline:
            raise TimeoutError("ERR_PROBE_TIMEOUT: tiến trình ffprobe vượt quá thời gian chờ cho phép")

        # Tái kiểm tra ngoại lệ từ reader threads sau join
        if stdout_reader.exception is not None or stderr_reader.exception is not None:
            exc = stdout_reader.exception or stderr_reader.exception
            raise RuntimeError(f"ERR_PROBE_EXECUTION_FAILED: lỗi đọc pipe tiến trình sau join: {exc}") from exc

        # Bắt buộc đạt clean EOF
        if not stdout_reader.clean_eof:
            raise RuntimeError("ERR_PROBE_EXECUTION_FAILED: luồng stdout không đạt clean EOF")
        if not stderr_reader.clean_eof:
            raise RuntimeError("ERR_PROBE_EXECUTION_FAILED: luồng stderr không đạt clean EOF")

        # Kiểm tra tràn bộ đệm sau khi kết thúc
        if stdout_reader.overflow or stderr_reader.overflow:
            raise ValueError("ERR_PROBE_METADATA_LIMIT: dữ liệu đầu ra từ ffprobe vượt quá giới hạn chặn trần")

        # Đảm bảo tiến trình con đã thu hồi xong và kiểm tra mã trả về
        if proc.poll() is None:
            rem_time = max(0.001, deadline - time.monotonic())
            try:
                proc.wait(timeout=rem_time)
            except subprocess.TimeoutExpired:
                raise TimeoutError("ERR_PROBE_TIMEOUT: tiến trình ffprobe không kết thúc sau khi luồng đọc đã EOF")

        if proc.returncode != 0:
            raise RuntimeError(f"ERR_PROBE_EXECUTION_FAILED: ffprobe kết thúc với mã lỗi {proc.returncode}")

        # Đóng pipe streams an toàn
        if proc.stdout and not getattr(proc.stdout, "closed", True):
            proc.stdout.close()
        if proc.stderr and not getattr(proc.stderr, "closed", True):
            proc.stderr.close()

    except Exception as exc:
        primary_failure = exc
        try:
            _cleanup_child_and_readers(proc, stdout_reader, stderr_reader)
        except Exception as cleanup_err:
            raise cleanup_err from primary_failure
        if not str(primary_failure).startswith("ERR_"):
            raise RuntimeError(f"ERR_PROBE_EXECUTION_FAILED: {primary_failure}") from primary_failure
        raise primary_failure

    # 7. Phân tích cú pháp JSON nghiêm ngặt
    stdout_bytes = stdout_reader.get_data()
    try:
        stdout_str = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ValueError("ERR_INVALID_PROBE_PAYLOAD: dữ liệu stdout từ ffprobe không phải UTF-8 hợp lệ") from err

    if not stdout_str or stdout_str.isspace():
        raise ValueError("ERR_INVALID_PROBE_PAYLOAD: stdout từ ffprobe rỗng")

    def _strict_json_constant(val: str) -> None:
        raise ValueError(f"ERR_INVALID_PROBE_PAYLOAD: JSON chứa hằng số không chuẩn: {val}")

    def _strict_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                raise ValueError("ERR_INVALID_PROBE_PAYLOAD: JSON chứa khóa trùng lặp trong đối tượng")
            d[k] = v
        return d

    try:
        payload = json.loads(
            stdout_str,
            parse_constant=_strict_json_constant,
            object_pairs_hook=_strict_object_pairs_hook,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as err:
        msg = str(err)
        if not msg.startswith("ERR_INVALID_PROBE_PAYLOAD:"):
            raise ValueError("ERR_INVALID_PROBE_PAYLOAD: dữ liệu JSON từ ffprobe không hợp lệ") from err
        raise

    # 8. Phân tích tiêu đề video qua parser đã nghiệm thu
    video_header = parse_ffprobe_video_header(
        payload,
        selected_stream_index=selected_stream_index,
    )

    # 9. Phân tích thời lượng container từ format
    format_dur: Optional[Fraction] = None
    if "format" in payload:
        format_dur = _parse_format_duration(payload["format"])

    # 10. Chụp stat_after và đối soát độ tươi mới của nguồn (TOCTOU protection)
    stat_after = _capture_source_freshness(path_str, is_after=True)
    if (
        stat_after.file_size_bytes != stat_before.file_size_bytes
        or stat_after.mtime_ns != stat_before.mtime_ns
        or stat_after.ctime_ns != stat_before.ctime_ns
        or stat_after.device_id != stat_before.device_id
        or stat_after.inode_number != stat_before.inode_number
    ):
        raise ValueError("ERR_SOURCE_MUTATED: tệp nguồn đã bị sửa đổi hoặc tráo đổi trong quá trình probe")

    # Kiểm tra hủy lần cuối trước khi công bố kết quả
    _check_cancellation(cancelled_fn)

    return LocalHeaderAcquisitionResult(
        file_path=canonical_path,
        video_header=video_header,
        format_duration_sec=format_dur,
        freshness=stat_before,
    )
