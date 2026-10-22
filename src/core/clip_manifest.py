"""
ClipManifest & Unified Pipeline Data Provider
==============================================
Đảm bảo prompt và timeline dùng chung một danh sách clip có ID cố định,
đóng băng thứ tự theo thời gian thực tế, bất kể thứ tự chọn tệp hay đổi đường dẫn.
"""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

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
    def create(cls, file_paths: List[str], project_name: str = "Vlog_Project", cache_dir: Optional[str] = None) -> "ClipManifest":
        """
        Khởi tạo manifest mới từ danh sách file:
        - Sắp xếp thứ tự một lần duy nhất theo diễn tiến thời gian (chronological).
        - Đóng băng thứ tự và gán ID (C01, C02...).
        - Tính manifest_id và lưu atomic.
        """
        # Loại bỏ các phần tử trùng lặp theo đường dẫn chuẩn
        unique_paths = []
        seen = set()
        for p in file_paths:
            p_abs = os.path.abspath(p)
            if p_abs not in seen:
                seen.add(p_abs)
                unique_paths.append(p_abs)

        # Sắp xếp theo dòng thời gian
        sorted_paths = sort_clips_chronologically(unique_paths)

        num_digits = 3 if len(sorted_paths) >= 100 else 2
        entries: List[ClipEntry] = []
        for idx, p in enumerate(sorted_paths):
            cid = f"C{idx+1:0{num_digits}d}"
            c_hash = compute_content_hash(p)
            entry = _probe_and_create_clip_entry(p, cid, c_hash)
            entries.append(entry)

        m_id = compute_manifest_id(entries)
        manifest = cls(
            project_name=project_name,
            manifest_id=m_id,
            clips=entries
        )
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


def _probe_and_create_clip_entry(file_path: str, clip_id: str, content_hash: str) -> ClipEntry:
    """Trích xuất siêu dữ liệu và khởi tạo ClipEntry."""
    fname = os.path.basename(file_path)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    shot_time, shot_time_source = get_clip_shot_time(file_path)

    dur = 0.0
    fps = 30.0
    start_tc = 0

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


def build_clips_data(
    manifest: ClipManifest,
    cache_mgr=None,
    model: str = "small",
    lang: str = "vi",
    project_structure: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Hàm DUY NHẤT để dựng danh sách clips_data chuẩn hóa cho toàn bộ hệ thống (ReviewPage & AutoWindow).
    Đặc điểm:
    - Thứ tự clip cố định 100% theo manifest (đã đóng băng).
    - Có clip_id ("C01") và clip_index (số nguyên từ clip_id).
    - BỎ hoàn toàn audio_peak_sec và visual_motion.
    - Bỏ qua các clip có duration < 1.0s khỏi danh sách kịch bản prompt.
    """
    if cache_mgr is None:
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()

    from src.core.whisper_cleaner import clean_whisper_segments

    clips_data: List[Dict[str, Any]] = []

    for idx, clip in enumerate(manifest.clips):
        dur = float(clip.duration_sec)
        cpath = clip.current_path

        # Tra cứu cache
        cached = cache_mgr.get_cached_scan(cpath, model, lang)
        if cached and dur <= 0:
            dur = float(cached.get("clip_dur") or cached.get("duration") or 0.0)

        # Bỏ clip có duration < 1.0s khỏi prompt
        if 0 < dur < 1.0:
            continue

        # Xác định Chapter name
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

        raw_subs = cached.get("raw_subtitles", []) if cached else []
        clean_subs = clean_whisper_segments(raw_subs) if raw_subs else []

        dialogue_intervals = []
        clip_text = ""
        is_broll = False

        if clean_subs:
            for cs in clean_subs:
                st = float(cs.get("start", 0.0))
                en = float(cs.get("end", 0.0))
                t = str(cs.get("text", "")).strip()
                if t:
                    dialogue_intervals.append({"start": st, "end": en, "text": t})
            clip_text = " ".join([d["text"] for d in dialogue_intervals]).strip()
        else:
            is_broll = True
            clip_text = "[CẢNH B-ROLL / PHONG CẢNH - KHÔNG CÓ THOẠI]"

        if dur <= 0:
            dur = 60.0

        digits = re.findall(r'\d+', clip.clip_id)
        clip_index_int = int(digits[0]) if digits else (idx + 1)

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
            "transcript": clip_text,
            "speech": clip_text,
            "is_broll": is_broll,
            "dialogue_intervals": dialogue_intervals,
            "manifest_id": manifest.manifest_id
        })

    return clips_data
