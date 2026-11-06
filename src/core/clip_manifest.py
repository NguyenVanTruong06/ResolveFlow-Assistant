"""
ClipManifest & Unified Pipeline Data Provider
==============================================
Đảm bảo prompt và timeline dùng chung một danh sách clip có ID cố định,
đóng băng thứ tự theo thời gian thực tế, bất kể thứ tự chọn tệp hay đổi đường dẫn.
"""

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.core.chronological_sorter import get_clip_shot_time, sort_clips_chronologically


def compute_content_hash(file_path: str) -> str:
    """
    content_hash = sha256(file_size + 1MB đầu + 1MB cuối).
    KHÔNG phụ thuộc vào đường dẫn tuyệt đối hay mtime,
    đảm bảo khi đổi ổ đĩa / đổi máy / copy sang thư mục khác vẫn nhận diện đúng file.
    """
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return ""
    try:
        file_size = os.path.getsize(file_path)
    except Exception:
        return ""

    hasher = hashlib.sha256()
    hasher.update(str(file_size).encode("utf-8"))

    ONE_MB = 1024 * 1024
    try:
        with open(file_path, "rb") as f:
            if file_size <= 2 * ONE_MB:
                hasher.update(f.read())
            else:
                hasher.update(f.read(ONE_MB))
                f.seek(file_size - ONE_MB)
                hasher.update(f.read(ONE_MB))
    except Exception:
        return ""

    return hasher.hexdigest()


def compute_manifest_id(clips: List["ClipEntry"]) -> str:
    """manifest_id = 8 ký tự đầu sha256 của danh sách (clip_id, content_hash) theo thứ tự."""
    hasher = hashlib.sha256()
    for c in clips:
        hasher.update(f"{c.clip_id}:{c.content_hash};".encode("utf-8"))
    return hasher.hexdigest()[:8]


@dataclass
class ClipEntry:
    clip_id: str                      # "C01", "C02"… (2 chữ số, 3 nếu >= 100 clip)
    content_hash: str                 # sha256(size + 1MB đầu + 1MB cuối)
    file_name: str                    # Tên tệp (vd: "DJI_0001.MP4")
    file_size: int                    # Kích thước tệp (bytes)
    duration_sec: float               # Thời lượng video (giây)
    fps: float                        # Tốc độ khung hình (vd: 30.0)
    start_tc_frames: int              # Timecode bắt đầu tính bằng frame
    shot_time: str                    # ISO giờ địa phương (vd: "2026-08-31T11:46:57")
    shot_time_source: str             # "filename" | "container" | "mtime" | "none"
    current_path: str                 # Đường dẫn tuyệt đối hiện tại trên đĩa

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClipEntry":
        return cls(
            clip_id=str(data.get("clip_id", "C01")),
            content_hash=str(data.get("content_hash", "")),
            file_name=str(data.get("file_name", "")),
            file_size=int(data.get("file_size", 0)),
            duration_sec=float(data.get("duration_sec", 0.0)),
            fps=float(data.get("fps", 30.0)),
            start_tc_frames=int(data.get("start_tc_frames", 0)),
            shot_time=str(data.get("shot_time", "")),
            shot_time_source=str(data.get("shot_time_source", "none")),
            current_path=str(data.get("current_path", ""))
        )


@dataclass
class ClipManifest:
    project_name: str
    manifest_id: str
    clips: List[ClipEntry] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def get_entry_by_id(self, clip_id: str) -> Optional[ClipEntry]:
        cid_norm = clip_id.strip().upper()
        for c in self.clips:
            if c.clip_id.upper() == cid_norm:
                return c
        return None

    def get_entry_by_index(self, index: int) -> Optional[ClipEntry]:
        if 1 <= index <= len(self.clips):
            return self.clips[index - 1]
        for c in self.clips:
            digits = re.findall(r'\d+', c.clip_id)
            if digits and int(digits[0]) == index:
                return c
        return None

    def get_entry_by_hash(self, content_hash: str) -> Optional[ClipEntry]:
        if not content_hash:
            return None
        for c in self.clips:
            if c.content_hash == content_hash:
                return c
        return None

    def add_clip(self, file_path: str) -> ClipEntry:
        """
        Thêm clip mới vào manifest:
        - Nếu content_hash đã có trong manifest -> cập nhật current_path nếu cần, giữ nguyên ID.
        - Nếu là clip mới -> gắn ID tiếp theo ở cuối danh sách (không đánh số lại các clip cũ).
        - Cập nhật manifest_id và lưu atomic.
        """
        p_abs = os.path.abspath(file_path)
        c_hash = compute_content_hash(p_abs)
        if c_hash:
            existing = self.get_entry_by_hash(c_hash)
            if existing:
                if not os.path.exists(existing.current_path) and os.path.exists(p_abs):
                    existing.current_path = p_abs
                    self.updated_at = datetime.now().isoformat()
                    self.save()
                return existing

        # Gắn ID tiếp theo ở cuối
        next_idx = len(self.clips) + 1
        num_digits = 3 if next_idx >= 100 or (self.clips and len(self.clips[0].clip_id) > 3) else 2
        clip_id = f"C{next_idx:0{num_digits}d}"

        entry = _probe_and_create_clip_entry(p_abs, clip_id, c_hash)
        self.clips.append(entry)
        self.manifest_id = compute_manifest_id(self.clips)
        self.updated_at = datetime.now().isoformat()
        self.save()
        return entry

    def rebuild_order(self):
        """
        Explicit function: Sắp xếp lại toàn bộ clip theo trình tự thời gian (chronological)
        và đánh số lại clip_id (C01, C02...), cập nhật manifest_id.
        """
        sorted_entries = sort_clips_chronologically(self.clips)
        num_digits = 3 if len(sorted_entries) >= 100 else 2
        for idx, e in enumerate(sorted_entries):
            e.clip_id = f"C{idx+1:0{num_digits}d}"
        self.clips = sorted_entries
        self.manifest_id = compute_manifest_id(self.clips)
        self.updated_at = datetime.now().isoformat()
        self.save()

    def relink(self, folder: str) -> int:
        """
        Khi current_path không tồn tại, tìm trong folder file cùng file_name + file_size,
        xác nhận content_hash, cập nhật current_path.
        Không tìm thấy -> đánh dấu missing, không đoán mò.
        """
        if not os.path.isdir(folder):
            return 0

        relinked_count = 0
        candidates: Dict[Tuple[str, int], List[str]] = {}
        for root, _, files in os.walk(folder):
            for fn in files:
                full_p = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(full_p)
                    candidates.setdefault((fn, sz), []).append(full_p)
                except Exception:
                    continue

        for entry in self.clips:
            if not os.path.exists(entry.current_path):
                key = (entry.file_name, entry.file_size)
                if key in candidates:
                    for cand_path in candidates[key]:
                        if compute_content_hash(cand_path) == entry.content_hash:
                            entry.current_path = os.path.abspath(cand_path)
                            relinked_count += 1
                            break

        if relinked_count > 0:
            self.updated_at = datetime.now().isoformat()
            self.save()
        return relinked_count

    def save(self, target_path: Optional[str] = None, cache_dir: Optional[str] = None):
        """Lưu manifest xuống đĩa dưới dạng atomic (file tạm rồi rename)."""
        fpath = target_path or self.get_default_manifest_path(self.project_name, cache_dir=cache_dir)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        tmp_path = f"{fpath}.tmp_{os.getpid()}_{int(time.time() * 1000)}"

        data = {
            "project_name": self.project_name,
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "clips": [c.to_dict() for c in self.clips]
        }

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, fpath)

    @classmethod
    def load(cls, project_name: str, cache_dir: Optional[str] = None) -> Optional["ClipManifest"]:
        fpath = cls.get_default_manifest_path(project_name, cache_dir=cache_dir)
        if not os.path.exists(fpath):
            return None
        return cls.load_from_path(fpath)

    @classmethod
    def load_from_path(cls, fpath: str) -> Optional["ClipManifest"]:
        if not os.path.exists(fpath):
            return None
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            clips = [ClipEntry.from_dict(c) for c in data.get("clips", [])]
            return cls(
                project_name=str(data.get("project_name", "default_project")),
                manifest_id=str(data.get("manifest_id", "")),
                clips=clips,
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", ""))
            )
        except Exception:
            return None

    @classmethod
    def get_default_manifest_path(cls, project_name: str, cache_dir: Optional[str] = None) -> str:
        safe_name = re.sub(r'[^\w\-_\.]', '_', project_name).strip('_') or "default_project"
        manifests_dir = cache_dir or os.path.expanduser("~/.resolveflow_cache/manifests")
        os.makedirs(manifests_dir, exist_ok=True)
        return os.path.join(manifests_dir, f"{safe_name}.json")

    @classmethod
    def create(
        cls,
        file_paths: List[str],
        project_name: str = "Vlog_Project",
        cache_dir: Optional[str] = None,
        *,
        metadata_provider: Optional[Callable[[str], Any]] = None,
        cancelled_fn: Optional[Callable[[], bool]] = None,
        persist: bool = True,
    ) -> "ClipManifest":
        """
        Khởi tạo manifest mới từ danh sách file:
        - Sắp xếp thứ tự một lần duy nhất theo diễn tiến thời gian (chronological).
        - Đóng băng thứ tự và gán ID (C01, C02...).
        - Tính manifest_id và lưu atomic (nếu persist=True).
        """
        if metadata_provider is not None and not callable(metadata_provider):
            raise ValueError("ERR_INVALID_METADATA_PROVIDER: metadata_provider phải là callable hoặc None")
        if cancelled_fn is not None and not callable(cancelled_fn):
            raise ValueError("ERR_INVALID_CANCELLED_FN: cancelled_fn phải là callable hoặc None")
        if type(persist) is not bool:
            raise ValueError("ERR_INVALID_PERSIST_FLAG: persist phải là strict bool")

        def _check_cancel() -> None:
            if cancelled_fn is not None:
                res = cancelled_fn()
                if type(res) is not bool:
                    raise RuntimeError("ERR_PROBE_CANCELLED_CALLBACK: cancelled_fn phải trả về strict bool")
                if res:
                    raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ tạo manifest bị hủy bởi caller")

        _check_cancel()

        # Loại bỏ các phần tử trùng lặp theo đường dẫn chuẩn
        unique_paths = []
        seen = set()
        for p in file_paths:
            p_abs = os.path.abspath(p)
            if p_abs not in seen:
                seen.add(p_abs)
                unique_paths.append(p_abs)

        _check_cancel()

        # Sắp xếp theo dòng thời gian
        sorted_paths = sort_clips_chronologically(unique_paths)

        _check_cancel()

        num_digits = 3 if len(sorted_paths) >= 100 else 2
        entries: List[ClipEntry] = []
        for idx, p in enumerate(sorted_paths):
            _check_cancel()
            cid = f"C{idx+1:0{num_digits}d}"
            c_hash = compute_content_hash(p)
            _check_cancel()
            entry = _probe_and_create_clip_entry(
                p,
                cid,
                c_hash,
                metadata_provider=metadata_provider,
                cancelled_fn=cancelled_fn,
            )
            _check_cancel()
            entries.append(entry)

        _check_cancel()

        m_id = compute_manifest_id(entries)
        manifest = cls(
            project_name=project_name,
            manifest_id=m_id,
            clips=entries
        )

        _check_cancel()

        if persist:
            manifest.save(cache_dir=cache_dir)

        return manifest

    @classmethod
    def get_or_create(cls, file_paths: List[str], project_name: str = "Vlog_Project", cache_dir: Optional[str] = None) -> "ClipManifest":
        """Nạp manifest hiện có hoặc tạo mới nếu chưa tồn tại."""
        existing = cls.load(project_name, cache_dir=cache_dir)
        if existing and existing.clips:
            # Kiểm tra xem có file nào mới chưa có trong manifest không
            existing_hashes = {c.content_hash for c in existing.clips}
            existing_names = {c.file_name for c in existing.clips}
            changed = False
            for p in file_paths:
                p_abs = os.path.abspath(p)
                fn = os.path.basename(p_abs)
                ch = compute_content_hash(p_abs) if os.path.exists(p_abs) else ""
                if ch and ch not in existing_hashes:
                    existing.add_clip(p_abs)
                    existing_hashes.add(ch)
                    changed = True
                elif not ch and fn not in existing_names:
                    existing.add_clip(p_abs)
                    existing_names.add(fn)
                    changed = True
            if changed:
                existing.save(cache_dir=cache_dir)
            return existing
        return cls.create(file_paths, project_name=project_name, cache_dir=cache_dir)


_MISSING_MEMBER = object()


def _get_required_member(obj: Any, attr_name: str, type_label: str) -> Any:
    """Đọc thuộc tính bắt buộc của dataclass, từ chối bằng ValueError nếu thuộc tính bị thiếu."""
    val = getattr(obj, attr_name, _MISSING_MEMBER)
    if val is _MISSING_MEMBER:
        raise ValueError(
            f"ERR_INVALID_METADATA_RESULT: {type_label} thiếu thuộc tính bắt buộc: '{attr_name}'"
        )
    return val


def _validate_and_reconstruct_header_result(res: Any) -> Any:
    """
    Tái tạo và xác thực nghiêm ngặt đối tượng kết quả siêu dữ liệu (LocalHeaderAcquisitionResult).
    Từ chối các đối tượng mạo danh, bị đột biến, thiếu thuộc tính bắt buộc, sai kiểu hoặc
    chứa dữ liệu hỏng trước bất kỳ phép chuẩn hóa đường dẫn, chuyển đổi số hay so sánh độ tươi mới nào.
    """
    from fractions import Fraction
    from src.core.contracts.time_contracts import RationalFPS
    from src.core.geometry.local_metadata_acquisition import (
        LocalHeaderAcquisitionResult,
        SourceFreshnessStat,
    )
    from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader

    # 1. Nominal type check
    if not isinstance(res, LocalHeaderAcquisitionResult) or type(res) is not LocalHeaderAcquisitionResult:
        raise ValueError("ERR_INVALID_METADATA_RESULT: kết quả của metadata_provider phải là LocalHeaderAcquisitionResult")

    # 2. Đọc 4 trường bắt buộc của LocalHeaderAcquisitionResult
    fp = _get_required_member(res, "file_path", "LocalHeaderAcquisitionResult")
    vh = _get_required_member(res, "video_header", "LocalHeaderAcquisitionResult")
    dur_raw = _get_required_member(res, "format_duration_sec", "LocalHeaderAcquisitionResult")
    fr = _get_required_member(res, "freshness", "LocalHeaderAcquisitionResult")

    # 3. file_path: strict non-empty str, không chứa NUL, bắt buộc là đường dẫn tuyệt đối
    if type(fp) is not str or not fp or "\x00" in fp:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: file_path phải là str không rỗng và không chứa NUL, nhận: {type(fp).__name__}")
    if not os.path.isabs(fp):
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: file_path phải là đường dẫn tuyệt đối, nhận: {fp!r}")

    # 4. format_duration_sec: None hoặc Fraction dương (> 0)
    dur: Optional[Fraction] = None
    if dur_raw is not None:
        if type(dur_raw) is not Fraction:
            raise ValueError(f"ERR_INVALID_METADATA_RESULT: format_duration_sec phải là Fraction hoặc None, nhận: {type(dur_raw).__name__}")
        num = getattr(dur_raw, "numerator", _MISSING_MEMBER)
        den = getattr(dur_raw, "denominator", _MISSING_MEMBER)
        if (
            num is _MISSING_MEMBER
            or den is _MISSING_MEMBER
            or type(num) is not int
            or type(num) is bool
            or type(den) is not int
            or type(den) is bool
            or num <= 0
            or den <= 0
        ):
            raise ValueError("ERR_INVALID_METADATA_RESULT: format_duration_sec phải là Fraction dương (> 0)")
        dur = Fraction(num, den)

    # 5. video_header: strict VideoStreamHeader và đọc 6 trường bắt buộc
    if not isinstance(vh, VideoStreamHeader) or type(vh) is not VideoStreamHeader:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: video_header phải là VideoStreamHeader, nhận: {type(vh).__name__ if vh is not None else 'None'}")

    vh_stream_index = _get_required_member(vh, "stream_index", "VideoStreamHeader")
    vh_codec_name = _get_required_member(vh, "codec_name", "VideoStreamHeader")
    vh_r_frame_rate = _get_required_member(vh, "r_frame_rate", "VideoStreamHeader")
    vh_avg_frame_rate = _get_required_member(vh, "avg_frame_rate", "VideoStreamHeader")
    vh_reported_nb_frames = _get_required_member(vh, "reported_nb_frames", "VideoStreamHeader")
    vh_time_base = _get_required_member(vh, "time_base", "VideoStreamHeader")

    if type(vh_stream_index) is not int or type(vh_stream_index) is bool or vh_stream_index < 0:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: stream_index phải là int >= 0 (không phải bool), nhận: {type(vh_stream_index).__name__}")

    if vh_r_frame_rate is not None:
        if not isinstance(vh_r_frame_rate, RationalFPS) or type(vh_r_frame_rate) is not RationalFPS:
            raise ValueError(f"ERR_INVALID_METADATA_RESULT: r_frame_rate phải là RationalFPS hoặc None, nhận: {type(vh_r_frame_rate).__name__}")

    if vh_avg_frame_rate is not None:
        if not isinstance(vh_avg_frame_rate, RationalFPS) or type(vh_avg_frame_rate) is not RationalFPS:
            raise ValueError(f"ERR_INVALID_METADATA_RESULT: avg_frame_rate phải là RationalFPS hoặc None, nhận: {type(vh_avg_frame_rate).__name__}")

    if vh_time_base is not None and type(vh_time_base) is not Fraction:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: time_base phải là Fraction hoặc None, nhận: {type(vh_time_base).__name__}")

    try:
        owned_vh = VideoStreamHeader(
            stream_index=vh_stream_index,
            codec_name=vh_codec_name,
            r_frame_rate=vh_r_frame_rate,
            avg_frame_rate=vh_avg_frame_rate,
            reported_nb_frames=vh_reported_nb_frames,
            time_base=vh_time_base,
        )
    except Exception as err:
        msg = str(err)
        if msg.startswith("ERR_"):
            raise
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: video_header không hợp lệ: {err}") from err

    # 6. freshness: strict SourceFreshnessStat và đọc 5 trường bắt buộc
    if not isinstance(fr, SourceFreshnessStat) or type(fr) is not SourceFreshnessStat:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: freshness phải là SourceFreshnessStat, nhận: {type(fr).__name__ if fr is not None else 'None'}")

    fr_file_size_bytes = _get_required_member(fr, "file_size_bytes", "SourceFreshnessStat")
    fr_mtime_ns = _get_required_member(fr, "mtime_ns", "SourceFreshnessStat")
    fr_ctime_ns = _get_required_member(fr, "ctime_ns", "SourceFreshnessStat")
    fr_device_id = _get_required_member(fr, "device_id", "SourceFreshnessStat")
    fr_inode_number = _get_required_member(fr, "inode_number", "SourceFreshnessStat")

    if type(fr_file_size_bytes) is not int or type(fr_file_size_bytes) is bool or fr_file_size_bytes < 0:
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: freshness file_size_bytes phải là int >= 0 (không phải bool), nhận: {type(fr_file_size_bytes).__name__}")

    try:
        owned_fr = SourceFreshnessStat(
            file_size_bytes=fr_file_size_bytes,
            mtime_ns=fr_mtime_ns,
            ctime_ns=fr_ctime_ns,
            device_id=fr_device_id,
            inode_number=fr_inode_number,
        )
    except Exception as err:
        msg = str(err)
        if msg.startswith("ERR_"):
            raise
        raise ValueError(f"ERR_INVALID_METADATA_RESULT: freshness không hợp lệ: {err}") from err

    # 7. Reconstruct validated and owned LocalHeaderAcquisitionResult
    return LocalHeaderAcquisitionResult(
        file_path=str(fp),
        video_header=owned_vh,
        format_duration_sec=dur,
        freshness=owned_fr,
    )


def _probe_and_create_clip_entry(
    file_path: str,
    clip_id: str,
    content_hash: str,
    *,
    metadata_provider: Optional[Callable[[str], Any]] = None,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> ClipEntry:
    """Trích xuất siêu dữ liệu và khởi tạo ClipEntry."""
    if metadata_provider is not None and not callable(metadata_provider):
        raise ValueError("ERR_INVALID_METADATA_PROVIDER: metadata_provider phải là callable hoặc None")
    if cancelled_fn is not None and not callable(cancelled_fn):
        raise ValueError("ERR_INVALID_CANCELLED_FN: cancelled_fn phải là callable hoặc None")

    def _check_cancel() -> None:
        if cancelled_fn is not None:
            res = cancelled_fn()
            if type(res) is not bool:
                raise RuntimeError("ERR_PROBE_CANCELLED_CALLBACK: cancelled_fn phải trả về strict bool")
            if res:
                raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")

    _check_cancel()

    fname = os.path.basename(file_path)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    shot_time, shot_time_source = get_clip_shot_time(file_path)

    _check_cancel()

    dur = 0.0
    fps = 30.0
    start_tc = 0

    if metadata_provider is None:
        # Giữ nguyên nhánh tương thích cũ khi provider is None
        if os.path.exists(file_path):
            try:
                from src.core.ffmpeg_utils import get_video_metadata
                meta = get_video_metadata(file_path)
                if meta:
                    dur = float(meta.get("duration", 0.0) or 0.0)
                    fps = float(meta.get("fps", 30.0) or 30.0)
                    start_tc = int(meta.get("start_tc_frames", 0) or 0)
            except Exception:
                pass
    else:
        # Gọi provider một lần duy nhất cho nguồn
        _check_cancel()
        raw_res = metadata_provider(file_path)
        _check_cancel()

        # Reconstruct và revalidate độc lập trước bất kỳ phép sử dụng nào
        res = _validate_and_reconstruct_header_result(raw_res)

        # Chuẩn hóa và so sánh đường dẫn (res.file_path đã được xác thực là abs path)
        norm_req = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
        norm_res = os.path.normcase(os.path.normpath(res.file_path))
        if norm_req != norm_res:
            raise ValueError(f"ERR_METADATA_SOURCE_MISMATCH: kết quả header ({norm_res}) không khớp nguồn ({norm_req})")

        # Ánh xạ format_duration_sec
        if res.format_duration_sec is None:
            dur = 0.0
        else:
            try:
                dur_val = float(res.format_duration_sec)
                dur = dur_val if (math.isfinite(dur_val) and dur_val > 0.0) else 0.0
            except (OverflowError, ValueError):
                dur = 0.0

        # Ánh xạ fps qua .as_float() từ RationalFPS hợp lệ
        rate_obj = res.video_header.avg_frame_rate or res.video_header.r_frame_rate
        if rate_obj is not None:
            try:
                fps_val = rate_obj.as_float()
                fps = fps_val if (math.isfinite(fps_val) and fps_val > 0.0) else 0.0
            except (OverflowError, ValueError):
                fps = 0.0
        else:
            fps = 0.0

        start_tc = 0

    _check_cancel()

    return ClipEntry(
        clip_id=clip_id,
        content_hash=content_hash,
        file_name=fname,
        file_size=file_size,
        duration_sec=dur,
        fps=fps,
        start_tc_frames=start_tc,
        shot_time=shot_time,
        shot_time_source=shot_time_source,
        current_path=os.path.abspath(file_path)
    )


def _normalize_duration(val: Any) -> Tuple[float, bool]:
    """
    Chuẩn hóa thời lượng clip thành finite positive float.
    Trả về (dur_sec, is_known).
    Các giá trị không hợp lệ (None, 'bad', NaN, Inf, bool, <= 0, > 1e8) trả về (0.0, False).
    """
    if val is None or isinstance(val, bool):
        return 0.0, False
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        return 0.0, False
    if math.isnan(f) or math.isinf(f) or f <= 0.0 or f > 1e8:
        return 0.0, False
    return f, True


def _validate_raw_segment(seg: Any) -> Optional[Dict[str, Any]]:
    """
    Xác thực nghiêm ngặt từng phân đoạn phụ đề thô:
    - seg phải là dict
    - start, end: số hữu hạn, KHÔNG phải bool, 0 <= start < end
    - an toàn trước bigint / OverflowError / NaN / Inf
    - text: chuỗi str, không rỗng sau khi strip
    Trả về dict chuẩn hóa {'start': float, 'end': float, 'text': str} nếu hợp lệ, ngược lại None.
    """
    if not isinstance(seg, dict):
        return None
    s_start = seg.get("start")
    s_end = seg.get("end")
    s_text = seg.get("text")

    if s_start is None or isinstance(s_start, bool):
        return None
    if s_end is None or isinstance(s_end, bool):
        return None

    try:
        f_start = float(s_start)
        f_end = float(s_end)
    except (TypeError, ValueError, OverflowError):
        return None

    if math.isnan(f_start) or math.isinf(f_start) or f_start < 0.0 or f_start > 1e8:
        return None
    if math.isnan(f_end) or math.isinf(f_end) or f_end <= f_start or f_end > 1e8:
        return None

    if not isinstance(s_text, str):
        return None
    txt = s_text.strip()
    if not txt:
        return None

    return {"start": f_start, "end": f_end, "text": txt}


def build_clips_data(
    manifest: ClipManifest,
    cache_mgr=None,
    model: str = "small",
    lang: str = "vi",
    project_structure: Optional[Any] = None,
    visual_index=None,
    *,
    verify_audio: bool = False,
    visual_context: Optional[Dict[str, Any]] = None,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """
    Hàm DUY NHẤT để dựng danh sách clips_data chuẩn hóa cho toàn bộ hệ thống (ReviewPage & AutoWindow).
    Đặc điểm:
    - Thứ tự clip cố định 100% theo manifest (đã đóng băng).
    - Có clip_id ("C01") và clip_index (số nguyên từ clip_id).
    - BỎ hoàn toàn audio_peak_sec và visual_motion.
    - Loại bỏ fake duration 60.0s: unknown/invalid duration -> 0.0 và duration_known=False.
    - Bỏ qua các clip có duration xác định và 0 < duration < 1.0s khỏi danh sách kịch bản prompt.
    - Hỗ trợ verify_audio (True gọi verify_full=True, False gọi 3 args cũ).
    - Hỗ trợ cooperative cancellation qua cancelled_fn ném InterruptedError.
    - Hỗ trợ visual_context kiểm tra lệch signature và giữ nguyên user notes.
    """
    if cancelled_fn and cancelled_fn():
        raise InterruptedError("Tác vụ dựng dữ liệu clip bị hủy bởi người dùng")

    if cache_mgr is None:
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()

    from src.core.whisper_cleaner import clean_whisper_segments
    if visual_index is None:
        from src.core.visual_index import VisualIndex
        visual_index = VisualIndex()

    clips_data: List[Dict[str, Any]] = []

    for idx, clip in enumerate(manifest.clips):
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("Tác vụ dựng dữ liệu clip bị hủy bởi người dùng")

        digits = re.findall(r'\d+', clip.clip_id)
        clip_index_int = int(digits[0]) if digits else (idx + 1)
        cpath = clip.current_path

        try:
            # 1. Pre-stat kiểm tra nguồn
            source_availability = "available"
            st_pre = None
            if cpath:
                try:
                    st_pre = os.stat(cpath)
                except FileNotFoundError:
                    source_availability = "source_missing"
                except Exception:
                    source_availability = "source_error"
            else:
                source_availability = "source_missing"

            # 2. Chuẩn hóa duration từ manifest
            dur, dur_known = _normalize_duration(clip.duration_sec)

            # 3. Tra cứu cache audio
            cached = None
            if cache_mgr is not None and cpath and source_availability == "available":
                if cancelled_fn and cancelled_fn():
                    raise InterruptedError("Tác vụ dựng dữ liệu clip bị hủy bởi người dùng")
                try:
                    if verify_audio:
                        cached = cache_mgr.get_cached_scan(
                            cpath, model, lang, verify_full=True, cancelled_fn=cancelled_fn
                        )
                    else:
                        cached = cache_mgr.get_cached_scan(cpath, model, lang)
                except InterruptedError:
                    raise
                except Exception:
                    cached = None

            if not dur_known and isinstance(cached, dict):
                c_dur, c_dur_known = _normalize_duration(cached.get("clip_dur") or cached.get("duration"))
                if c_dur_known:
                    dur = c_dur
                    dur_known = True

            # Bỏ clip có duration xác định và 0 < duration < 1.0s khỏi prompt
            if dur_known and (0.0 < dur < 1.0):
                continue

            # 4. Tra cứu visual index
            visual = None
            if visual_index is not None and cpath and source_availability == "available":
                if cancelled_fn and cancelled_fn():
                    raise InterruptedError("Tác vụ dựng dữ liệu clip bị hủy bởi người dùng")
                try:
                    visual = visual_index.load(cpath, clip.content_hash)
                except InterruptedError:
                    raise
                except Exception:
                    visual = None

            if cancelled_fn and cancelled_fn():
                raise InterruptedError("Tác vụ dựng dữ liệu clip bị hủy bởi người dùng")

            # 5. Post-stat kiểm tra nguồn sau khi đọc cache & visual
            if source_availability == "available" and cpath:
                try:
                    st_post = os.stat(cpath)
                    if st_pre is not None:
                        if (st_post.st_mtime_ns != st_pre.st_mtime_ns) or (st_post.st_size != st_pre.st_size):
                            source_availability = "source_changed"
                except FileNotFoundError:
                    source_availability = "source_missing"
                except Exception:
                    source_availability = "source_error"

            if source_availability != "available":
                cached = None
                visual = None

            # 6. Xác định Chapter name
            chap_name = "Chung"
            if project_structure:
                try:
                    for grp in getattr(project_structure, "groups", []):
                        if cpath in getattr(grp, "video_paths", []):
                            chap_name = grp.name
                            break
                except Exception:
                    pass
            if chap_name == "Chung" and cpath:
                try:
                    p_dir = os.path.basename(os.path.dirname(cpath))
                    if p_dir and not p_dir.startswith("_"):
                        chap_name = p_dir
                except Exception:
                    pass

            # 7. Xử lý audio analysis status, authority và dialogue intervals
            dialogue_intervals = []
            clip_text = ""
            clean_subs = []
            audio_authority = "unavailable"
            speech_status = "unscanned"
            audio_analysis_status = "unscanned"

            if verify_audio:
                from src.core.cache_manager import validate_v3_payload
                if cached is None:
                    audio_authority = "unavailable"
                    speech_status = "unscanned"
                    audio_analysis_status = "unscanned"
                elif not isinstance(cached, dict):
                    audio_authority = "unavailable"
                    speech_status = "scan_error"
                    audio_analysis_status = "failed"
                else:
                    full_sha = cached.get("full_content_sha256")
                    is_v3_valid = validate_v3_payload(
                        cached,
                        expected_full_sha256=full_sha if isinstance(full_sha, str) else None,
                        expected_model=model,
                        expected_lang=lang,
                    )
                    if not is_v3_valid:
                        audio_authority = "unavailable"
                        speech_status = "scan_error"
                        audio_analysis_status = "failed"
                    else:
                        c_status = cached.get("status")
                        is_no_audio = (cached.get("no_audio_track") is True) or (c_status == "no_audio_track")
                        raw_subs = cached.get("raw_subtitles", [])

                        if c_status == "error":
                            audio_authority = "unavailable"
                            speech_status = "scan_error"
                            audio_analysis_status = "failed"
                        elif is_no_audio:
                            if raw_subs and len(raw_subs) > 0:
                                # Dữ liệu mâu thuẫn: có cờ không âm thanh nhưng lại có transcript
                                audio_authority = "unavailable"
                                speech_status = "scan_error"
                                audio_analysis_status = "failed"
                            else:
                                audio_authority = "verified"
                                speech_status = "no_dialogue_detected"
                                audio_analysis_status = "no_audio_track"
                        elif not raw_subs:
                            audio_authority = "verified"
                            speech_status = "no_dialogue_detected"
                            audio_analysis_status = "completed_empty"
                        else:
                            valid_raw_subs = []
                            for rs in raw_subs:
                                v_seg = _validate_raw_segment(rs)
                                if v_seg is not None:
                                    valid_raw_subs.append(v_seg)
                            if not valid_raw_subs:
                                speech_status = "scan_error"
                                audio_analysis_status = "failed"
                                audio_authority = "unavailable"
                            else:
                                clean_subs = clean_whisper_segments(valid_raw_subs, language=lang)
                                if clean_subs:
                                    speech_status = "transcribed"
                                    audio_analysis_status = "transcribed"
                                    audio_authority = "verified"
                                    for cs in clean_subs:
                                        st = float(cs.get("start", 0.0))
                                        en = float(cs.get("end", 0.0))
                                        t = str(cs.get("text", "")).strip()
                                        if t:
                                            dialogue_intervals.append({"start": st, "end": en, "text": t})
                                    clip_text = " ".join([d["text"] for d in dialogue_intervals]).strip()
                                else:
                                    speech_status = "no_dialogue_detected"
                                    audio_analysis_status = "completed_empty"
                                    audio_authority = "verified"
            else:
                if cached is None:
                    audio_authority = "unavailable"
                    speech_status = "unscanned"
                    audio_analysis_status = "unscanned"
                elif not isinstance(cached, dict):
                    audio_authority = "unavailable"
                    speech_status = "scan_error"
                    audio_analysis_status = "failed"
                else:
                    c_status = cached.get("status")
                    is_no_audio = (cached.get("no_audio_track") is True) or (c_status == "no_audio_track")
                    raw_subs = cached.get("raw_subtitles")

                    if c_status == "error":
                        audio_authority = "unavailable"
                        speech_status = "scan_error"
                        audio_analysis_status = "failed"
                    elif is_no_audio:
                        if raw_subs and isinstance(raw_subs, list) and len(raw_subs) > 0:
                            audio_authority = "unavailable"
                            speech_status = "scan_error"
                            audio_analysis_status = "failed"
                        else:
                            audio_authority = "candidate"
                            speech_status = "no_dialogue_detected"
                            audio_analysis_status = "no_audio_track"
                    elif "raw_subtitles" not in cached or raw_subs is None or not isinstance(raw_subs, list):
                        audio_authority = "unavailable"
                        speech_status = "scan_error"
                        audio_analysis_status = "failed"
                    else:
                        if len(raw_subs) == 0:
                            audio_authority = "candidate"
                            speech_status = "no_dialogue_detected"
                            audio_analysis_status = "completed_empty"
                        else:
                            valid_raw_subs = []
                            for rs in raw_subs:
                                v_seg = _validate_raw_segment(rs)
                                if v_seg is not None:
                                    valid_raw_subs.append(v_seg)
                            if not valid_raw_subs:
                                speech_status = "scan_error"
                                audio_analysis_status = "failed"
                                audio_authority = "unavailable"
                            else:
                                clean_subs = clean_whisper_segments(valid_raw_subs, language=lang)
                                if clean_subs:
                                    speech_status = "transcribed"
                                    audio_analysis_status = "transcribed"
                                    audio_authority = "candidate"
                                    for cs in clean_subs:
                                        st = float(cs.get("start", 0.0))
                                        en = float(cs.get("end", 0.0))
                                        t = str(cs.get("text", "")).strip()
                                        if t:
                                            dialogue_intervals.append({"start": st, "end": en, "text": t})
                                    clip_text = " ".join([d["text"] for d in dialogue_intervals]).strip()
                                else:
                                    speech_status = "no_dialogue_detected"
                                    audio_analysis_status = "completed_empty"
                                    audio_authority = "candidate"

            # 8. Xử lý B-roll (chỉ gán khi được chỉ định rõ trong project_structure)
            is_broll = False
            if project_structure:
                for group in getattr(project_structure, "groups", []):
                    if cpath in getattr(group, "video_paths", []) and getattr(group, "is_broll", False):
                        is_broll = True
                        break

            # 9. Xử lý visual index & visual_context
            visual_segments = []
            quarantined_visual_segments = []
            vis_stale_excluded_count = 0
            vis_coverage = {}
            vis_analyzed_coverage = {}
            vis_analysis_status = "unverified"
            vis_status = "unscanned"
            vis_samples = 0
            vis_contact_sheet = ""

            if isinstance(visual, dict) and source_availability == "available":
                vis_status = visual.get("status", "unscanned")
                vis_coverage = visual.get("coverage") or {}
                vis_samples = len(visual.get("samples") or [])
                vis_contact_sheet = visual.get("contact_sheet_path", "")

                analysis = visual.get("analysis") or {}
                vis_analysis_status = analysis.get("status", "unverified")
                vis_analyzed_coverage = analysis.get("coverage") or {}
                sig_data = analysis.get("signature_data") or {}

                raw_segs = visual.get("visual_segments") or visual.get("descriptions") or []
                copied_segs = [dict(s) for s in raw_segs if isinstance(s, dict)]

                is_mismatch = False
                if visual_context is not None and isinstance(visual_context, dict):
                    if not sig_data:
                        is_mismatch = True
                    else:
                        req_model = visual_context.get("vision_model") or visual_context.get("model")
                        if req_model:
                            sig_model = sig_data.get("model") or sig_data.get("vision_model")
                            if not sig_model or str(req_model).strip().lower() != str(sig_model).strip().lower():
                                is_mismatch = True

                        req_ep = visual_context.get("vision_endpoint") or visual_context.get("endpoint")
                        if req_ep:
                            sig_ep = sig_data.get("endpoint") or sig_data.get("vision_endpoint")
                            if not sig_ep or str(req_ep).rstrip("/").lower() != str(sig_ep).rstrip("/").lower():
                                is_mismatch = True

                        sig_samp = sig_data.get("sampling") or {}
                        for k in ("window_sec", "frames_per_window", "max_windows"):
                            if k in visual_context:
                                if k not in sig_samp or visual_context[k] != sig_samp[k]:
                                    is_mismatch = True

                        req_max_ref = visual_context.get("max_refinements")
                        if req_max_ref is None and isinstance(visual_context.get("refinement_policy"), dict):
                            req_max_ref = visual_context["refinement_policy"].get("max_refinements")
                        if req_max_ref is not None:
                            sig_max_ref = sig_data.get("max_refinements")
                            if sig_max_ref is None and isinstance(sig_data.get("refinement_policy"), dict):
                                sig_max_ref = sig_data["refinement_policy"].get("max_refinements")
                            if sig_max_ref is None or req_max_ref != sig_max_ref:
                                is_mismatch = True

                        for k, req_v in visual_context.items():
                            if k.startswith("enable_"):
                                sig_v = sig_data.get(k)
                                if req_v != sig_v:
                                    is_mismatch = True

                if vis_analysis_status == "stale":
                    is_mismatch = True

                if is_mismatch:
                    vis_analysis_status = "stale"
                    usable_segs = []
                    quarantine = []
                    for s in copied_segs:
                        if s.get("origin") == "user":
                            usable_segs.append(s)
                        else:
                            s["analysis_status"] = "stale"
                            quarantine.append(s)
                    visual_segments = usable_segs
                    quarantined_visual_segments = quarantine
                    vis_stale_excluded_count = len(quarantine)
                else:
                    visual_segments = copied_segs
                    quarantined_visual_segments = []
                    vis_stale_excluded_count = 0

            if source_availability != "available":
                clip_text = ""
                dialogue_intervals = []
                visual_segments = []
                quarantined_visual_segments = []
                vis_stale_excluded_count = 0
                audio_authority = "unavailable"

            clips_data.append({
                "clip_id": clip.clip_id,
                "clip_index": clip_index_int,
                "id": clip_index_int,
                "name": clip.file_name,
                "clip_name": clip.file_name,
                "path": cpath,
                "chapter": chap_name,
                "duration": round(dur, 1),
                "duration_sec": dur,
                "duration_known": dur_known,
                "transcript": clip_text,
                "speech": clip_text,
                "is_broll": is_broll,
                "speech_status": speech_status,
                "audio_analysis_status": audio_analysis_status,
                "audio_authority": audio_authority,
                "source_availability": source_availability,
                "visual_status": vis_status,
                "visual_coverage": vis_coverage,
                "visual_analyzed_coverage": vis_analyzed_coverage,
                "visual_analysis_status": vis_analysis_status,
                "visual_segments": visual_segments,
                "quarantined_visual_segments": quarantined_visual_segments,
                "visual_stale_excluded_count": vis_stale_excluded_count,
                "visual_samples": vis_samples,
                "visual_contact_sheet": vis_contact_sheet,
                "dialogue_intervals": dialogue_intervals,
                "manifest_id": manifest.manifest_id
            })

        except InterruptedError:
            raise
        except Exception:
            dur, dur_known = _normalize_duration(clip.duration_sec)
            clips_data.append({
                "clip_id": clip.clip_id,
                "clip_index": clip_index_int,
                "id": clip_index_int,
                "name": clip.file_name,
                "clip_name": clip.file_name,
                "path": cpath,
                "chapter": "Chung",
                "duration": round(dur, 1),
                "duration_sec": dur,
                "duration_known": dur_known,
                "transcript": "",
                "speech": "",
                "is_broll": False,
                "speech_status": "scan_error",
                "audio_analysis_status": "failed",
                "audio_authority": "unavailable",
                "source_availability": "source_error",
                "visual_status": "unscanned",
                "visual_coverage": {},
                "visual_analyzed_coverage": {},
                "visual_analysis_status": "unverified",
                "visual_segments": [],
                "quarantined_visual_segments": [],
                "visual_stale_excluded_count": 0,
                "visual_samples": 0,
                "visual_contact_sheet": "",
                "dialogue_intervals": [],
                "manifest_id": manifest.manifest_id
            })

    return clips_data
