"""
src/ui/director/workers/kinetic_preparation_worker.py
=============================================================================
Worker QThread chuẩn bị kịch bản Kinetic Typography (V2) bất đồng bộ cho ReviewPage:
- Toàn bộ I/O, hash cache Whisper v3, giải quyết hình học timeline, và bóc tách
  từ vựng (Kinetic Dataset) chạy 100% trên luồng nền, giải phóng hoàn toàn GUI thread.
- Tái sử dụng module pure: src/core/kinetic/kinetic_dataset_mapper.py.
- Xác thực cache nghiêm ngặt (verify_full=True), từ chối falsy, phrase-only (word_timestamps_missing).
- Pre-stat & post-stat kiểm tra tệp nguồn: tệp đổi/mất -> fail-closed ngay lập tức.
- Giới hạn ngân sách UTF-8 32768 bytes: quá trần -> budget_error (không cắt vụn schema).
- Với số từ > 300: chuyển sang chế độ style_only (40 mẫu) cho Prompt, nhưng bảo toàn
  đầy đủ 100% từ ngữ cho AutoPlan offline.
- Tách biệt tín hiệu kết thúc logic (finished_signal) và vòng đời vật lý (QThread.finished).
- Hỗ trợ Dependency Injection cho cache_mgr, manifest_cache_dir và metadata_provider.
=============================================================================
"""

import os
import stat
import math
import copy
import logging
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Any, Optional, Tuple, List, Union

from PySide6.QtCore import QThread, Signal

from src.core.contracts.time_contracts import RationalFPS, pts_to_frame, frame_to_pts
from src.core.kinetic.kinetic_dataset_mapper import (
    SourceWord,
    WordPlacement,
    MappedWord,
    KineticDataset,
    extract_source_words,
    map_words_to_timeline,
)
from src.core.kinetic.kinetic_engine import (
    auto_plan,
    validate_plan,
    generate_kinetic_prompt,
)

logger = logging.getLogger(__name__)


def _validate_finite_float(val: Any, default: Optional[float] = None, min_val: Optional[float] = None, max_val: Optional[float] = None) -> Optional[float]:
    """Kiểm tra và trả về float hữu hạn hợp lệ, loại trừ bool, NaN, Inf, non-numeric hoặc ngoài khoảng min/max."""
    if val is None:
        return default
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float, str, Fraction)):
        try:
            f = float(val)
            if math.isinf(f) or math.isnan(f):
                return None
            if min_val is not None and f < min_val:
                return None
            if max_val is not None and f > max_val:
                return None
            return f
        except (ValueError, TypeError, OverflowError):
            return None
    return None


# =============================================================================
# 1. KINETIC PREPARATION REQUEST (IMMUTABLE VALUE OBJECT)
# =============================================================================

@dataclass(frozen=True)
class KineticPreparationRequest:
    """Yêu cầu chuẩn bị kịch bản Kinetic Typography (V2).

    Chỉ chứa dữ liệu nguyên thủy (primitive data), tuyệt đối không chứa QWidget
    hay Resolve script proxies.
    """
    request_id: str
    generation: int
    intent: str  # 'copy_kinetic' | 'auto_plan' | 'copy_transcript'
    context_primitives: Dict[str, Any]
    manifest_metadata: Optional[Dict[str, Any]] = None
    video_plan_data: Optional[Dict[str, Any]] = None
    aspect: str = "9:16"
    font: str = "Montserrat"
    max_prompt_bytes: int = 32768
    timeline_fps: Optional[Any] = None

    def __post_init__(self):
        if not isinstance(self.request_id, str) or type(self.request_id) is bool or not self.request_id.strip():
            raise ValueError(f"request_id phải là chuỗi không rỗng: {self.request_id!r}")
        if type(self.generation) is not int or self.generation < 0:
            raise TypeError(f"generation phải là int >= 0: {self.generation!r}")
        if self.intent not in ("copy_kinetic", "auto_plan", "copy_transcript"):
            raise ValueError(f"intent không hợp lệ: {self.intent!r}")
        if not isinstance(self.aspect, str) or type(self.aspect) is bool or not self.aspect.strip():
            raise ValueError(f"aspect phải là chuỗi không rỗng: {self.aspect!r}")
        if not isinstance(self.font, str) or type(self.font) is bool or not self.font.strip():
            raise ValueError(f"font phải là chuỗi không rỗng: {self.font!r}")
        if type(self.max_prompt_bytes) is not int or self.max_prompt_bytes < 1 or self.max_prompt_bytes > 32768:
            raise ValueError(f"max_prompt_bytes phải trong khoảng 1..32768, nhận được: {self.max_prompt_bytes}")
        if not isinstance(self.context_primitives, dict):
            raise TypeError("context_primitives phải là dict.")

        if self.timeline_fps is not None:
            if isinstance(self.timeline_fps, bool):
                raise TypeError("timeline_fps không thể là boolean")
            try:
                from src.core.contracts.time_contracts import RationalFPS
                rat = RationalFPS.from_value(self.timeline_fps)
                if rat.as_float() <= 0:
                    raise ValueError(f"timeline_fps phải dương: {self.timeline_fps}")
            except Exception as e:
                raise ValueError(f"timeline_fps không hợp lệ: {self.timeline_fps}") from e

        # Deepcopy dictionaries để bảo đảm tính bất biến tuyệt đối
        object.__setattr__(self, "context_primitives", copy.deepcopy(self.context_primitives))
        if self.manifest_metadata is not None:
            if not isinstance(self.manifest_metadata, dict):
                raise TypeError("manifest_metadata phải là dict hoặc None.")
            object.__setattr__(self, "manifest_metadata", copy.deepcopy(self.manifest_metadata))
        if self.video_plan_data is not None:
            if not isinstance(self.video_plan_data, dict):
                raise TypeError("video_plan_data phải là dict hoặc None.")
            object.__setattr__(self, "video_plan_data", copy.deepcopy(self.video_plan_data))


# =============================================================================
# 2. KINETIC PREPARATION WORKER (QTHREAD)
# =============================================================================

class KineticPreparationWorker(QThread):
    """Luồng worker chuẩn bị kịch bản Kinetic Typography cho ReviewPage."""

    started_signal = Signal(str)                          # (request_id)
    finished_signal = Signal(str, bool, str, dict)        # (request_id, success, message, result)

    def __init__(
        self,
        request: KineticPreparationRequest,
        cache_mgr: Optional[Any] = None,
        manifest_cache_dir: Optional[str] = None,
        metadata_provider: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.request = request
        self._is_cancelled = False
        self._terminal_emitted = False

        # Dependency Injection cho kiểm thử ngoại tuyến
        self._cache_mgr_arg = cache_mgr
        self._manifest_cache_dir = manifest_cache_dir
        self._metadata_provider = metadata_provider

    def cancel(self):
        """Yêu cầu dừng cooperative luồng worker."""
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def _emit_terminal(self, success: bool, message: str, result: Dict[str, Any]):
        """Đảm bảo đúng 1 lần phát tín hiệu kết thúc logic (terminal signal)."""
        if not self._terminal_emitted:
            self._terminal_emitted = True
            self.finished_signal.emit(
                self.request.request_id,
                success,
                message,
                copy.deepcopy(result)
            )

    def run(self):
        try:
            req = self.request
            req_id = req.request_id
            ctx = req.context_primitives
            paths = list(ctx.get("video_paths") or [])

            self.started_signal.emit(req_id)

            if not paths:
                self._emit_terminal(False, "zero_clips", {"error": "Không có tệp video nào để tạo kịch bản."})
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 1. Preflight: kiểm tra tệp nguồn tồn tại, quyền đọc và trạng thái ban đầu
            st_pres: Dict[str, os.stat_result] = {}
            for p in paths:
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return
                norm_p = os.path.normpath(os.path.abspath(p))
                if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                    self._emit_terminal(
                        False,
                        "source_missing",
                        {"error": f"Tệp nguồn không tồn tại hoặc không phải là tệp: {norm_p}", "clip_path": norm_p}
                    )
                    return
                if not os.access(norm_p, os.R_OK):
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Không có quyền đọc tệp nguồn: {norm_p}", "clip_path": norm_p}
                    )
                    return
                try:
                    st = os.stat(norm_p)
                    if not stat.S_ISREG(st.st_mode):
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Tệp nguồn không phải là regular file: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    st_pres[norm_p] = st
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Lỗi đọc trạng thái tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p}
                    )
                    return

            # 2. Xây dựng hoặc tái sử dụng ClipManifest chính xác
            from src.core.clip_manifest import ClipManifest, ClipEntry

            manifest: Optional[ClipManifest] = None

            # 2a. Reconstruct từ request.manifest_metadata nếu có
            if req.manifest_metadata and isinstance(req.manifest_metadata, dict):
                meta_proj = str(req.manifest_metadata.get("project_name", "")).strip()
                meta_clips = req.manifest_metadata.get("clips") or []
                if meta_proj == ctx.get("project_name", "ChunDVC_Project") and meta_clips and req.manifest_metadata.get("manifest_id"):
                    meta_paths = [os.path.normpath(os.path.abspath(c.get("current_path") or c.get("path") or "")) for c in meta_clips if isinstance(c, dict)]
                    req_norm = [os.path.normpath(os.path.abspath(p)) for p in paths]
                    if meta_paths == req_norm:
                        try:
                            clips = [ClipEntry.from_dict(c) for c in meta_clips]
                            manifest = ClipManifest(
                                project_name=meta_proj,
                                manifest_id=str(req.manifest_metadata["manifest_id"]),
                                clips=clips,
                                created_at=str(req.manifest_metadata.get("created_at", "")),
                                updated_at=str(req.manifest_metadata.get("updated_at", "")),
                            )
                        except Exception as me:
                            logger.warning("Không thể reconstruct pure ClipManifest từ request.manifest_metadata: %s", me)
                            manifest = None

            # 2b. Nạp từ manifest_cache_dir nếu khớp exact paths
            cache_dir = self._manifest_cache_dir or ctx.get("manifest_cache_dir")
            if manifest is None and cache_dir:
                existing = ClipManifest.load(ctx.get("project_name", "ChunDVC_Project"), cache_dir=cache_dir)
                if existing and existing.clips:
                    existing_norm = [os.path.normpath(os.path.abspath(c.current_path)) for c in existing.clips]
                    req_norm = [os.path.normpath(os.path.abspath(p)) for p in paths]
                    if existing_norm == req_norm:
                        manifest = existing

            # 2c. Tạo mới manifest cách ly
            if manifest is None:
                isolated_dir = cache_dir
                if isolated_dir is None:
                    isolated_dir = os.path.join(os.path.expanduser("~/.resolveflow_cache/manifests/requests"), req.request_id)
                manifest = ClipManifest.create(paths, project_name=ctx.get("project_name", "ChunDVC_Project"), cache_dir=isolated_dir)

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 3. Khởi tạo ScanCacheManager
            if self._cache_mgr_arg is not None:
                cache_mgr = self._cache_mgr_arg
            else:
                from src.core.cache_manager import ScanCacheManager
                cache_mgr = ScanCacheManager()

            w_model = str(ctx.get("whisper_model", "small"))
            w_lang = str(ctx.get("whisper_language") or ctx.get("whisper_lang") or "vi")

            # 4. Đọc và xác thực cache Whisper v3 cho toàn bộ clip nguồn
            from src.core.cache_manager import validate_v3_payload

            cached_scans_by_clip_id: Dict[str, Dict[str, Any]] = {}
            for c in manifest.clips:
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", {})
                    return
                cpath = c.current_path
                cached = cache_mgr.get_cached_scan(
                    cpath,
                    w_model,
                    w_lang,
                    verify_full=True,
                    cancelled_fn=self.is_cancelled
                )
                c_disp_name = getattr(c, "file_name", None) or getattr(c, "clip_name", None) or os.path.basename(getattr(c, "current_path", "")) or c.clip_id
                if cached is None:
                    self._emit_terminal(
                        False,
                        "not_analyzed",
                        {"error": f"Clip '{c_disp_name}' ({c.clip_id}) chưa được phân tích Whisper hoặc cache không hợp lệ.", "clip_id": c.clip_id}
                    )
                    return

                # Schema v3 payload validation
                if not validate_v3_payload(cached, expected_model=w_model, expected_lang=w_lang):
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Payload cache của clip '{c_disp_name}' ({c.clip_id}) không hợp lệ theo Schema v3.", "clip_id": c.clip_id}
                    )
                    return

                # Kiểm tra trạng thái phân tích / producer error độc lập từng trường
                producer_statuses = [
                    cached.get("audio_analysis_status"),
                    cached.get("status"),
                    cached.get("producer_status"),
                    cached.get("speech_status"),
                ]
                error_msg = cached.get("error")
                has_error = bool(error_msg) or any(s == "error" for s in producer_statuses if s is not None)
                if has_error:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Clip '{c_disp_name}' có trạng thái phân tích lỗi trong cache: {error_msg or 'status=error'}", "clip_id": c.clip_id}
                    )
                    return

                # Kiểm tra mâu thuẫn: no_audio/no_audio_track/completed_empty + nonempty raw_subtitles
                raw_subs = cached.get("raw_subtitles")
                has_subs = isinstance(raw_subs, list) and len(raw_subs) > 0
                is_no_audio = (
                    any(s in ("no_audio", "no_audio_track", "completed_empty") for s in producer_statuses if s is not None)
                    or bool(cached.get("no_audio"))
                    or bool(cached.get("no_audio_track"))
                )
                if is_no_audio and has_subs:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Mâu thuẫn cache clip '{c_disp_name}': được đánh dấu no_audio nhưng chứa phụ đề thoại.", "clip_id": c.clip_id, "kind": "corrupted_cache_contradiction"}
                    )
                    return

                cached_scans_by_clip_id[c.clip_id] = cached

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 5. Post-stat: Kiểm tra tệp nguồn không bị biến đổi trong lúc đọc cache
            for p in paths:
                norm_p = os.path.normpath(os.path.abspath(p))
                st_pre = st_pres.get(norm_p)
                try:
                    st_post = os.stat(norm_p)
                    if st_pre is not None:
                        if (
                            (st_post.st_mtime_ns != st_pre.st_mtime_ns)
                            or (st_post.st_size != st_pre.st_size)
                            or (st_post.st_ctime_ns != st_pre.st_ctime_ns)
                            or (st_post.st_dev != st_pre.st_dev)
                            or (st_post.st_ino != st_pre.st_ino)
                        ):
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {"error": f"Tệp nguồn đã bị thay đổi trong quá trình phân tích: {norm_p}", "clip_path": norm_p}
                            )
                            return
                except FileNotFoundError:
                    self._emit_terminal(
                        False,
                        "source_missing",
                        {"error": f"Tệp nguồn đã bị xóa: {norm_p}", "clip_path": norm_p}
                    )
                    return
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Lỗi kiểm tra tệp nguồn: {norm_p} ({exc})", "clip_path": norm_p}
                    )
                    return

            # 6. Bóc tách SourceWord cho từng clip qua extract_source_words
            source_words_by_clip: Dict[str, Tuple[SourceWord, ...]] = {}
            for c in manifest.clips:
                c_disp_name = getattr(c, "file_name", None) or getattr(c, "clip_name", None) or os.path.basename(getattr(c, "current_path", "")) or c.clip_id
                cached = cached_scans_by_clip_id[c.clip_id]
                dur_raw = cached.get("clip_dur") or getattr(c, "duration_sec", 0.0) or 0.0
                try:
                    dur_sec = float(dur_raw)
                except Exception:
                    dur_sec = 0.0

                if dur_sec <= 0:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Clip '{c_disp_name}' ({c.clip_id}) có thời lượng không hợp lệ: {dur_raw}", "clip_id": c.clip_id}
                    )
                    return

                producer_statuses = [
                    cached.get("audio_analysis_status"),
                    cached.get("status"),
                    cached.get("producer_status"),
                    cached.get("speech_status"),
                ]
                raw_subs = cached.get("raw_subtitles")
                is_no_audio = (
                    any(s in ("no_audio", "no_audio_track", "completed_empty") for s in producer_statuses if s is not None)
                    or bool(cached.get("no_audio"))
                    or bool(cached.get("no_audio_track"))
                )

                if is_no_audio and not raw_subs:
                    source_words_by_clip[c.clip_id] = ()
                elif raw_subs == []:
                    source_words_by_clip[c.clip_id] = ()
                elif isinstance(raw_subs, list) and raw_subs:
                    try:
                        words_tuple = extract_source_words(
                            raw_subs,
                            clip_id=c.clip_id,
                            duration_sec=dur_sec
                        )
                        source_words_by_clip[c.clip_id] = words_tuple
                    except ValueError as ve:
                        if "word_timestamps_missing" in str(ve):
                            self._emit_terminal(
                                False,
                                "word_timestamps_missing",
                                {"error": f"Clip '{c_disp_name}' ({c.clip_id}) có thoại nhưng thiếu word timestamps: {ve}", "clip_id": c.clip_id}
                            )
                            return
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Lỗi trích xuất từ vựng clip '{c_disp_name}': {ve}", "clip_id": c.clip_id}
                        )
                        return
                    except Exception as ee:
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Lỗi cấu trúc dữ liệu clip '{c_disp_name}': {ee}", "clip_id": c.clip_id}
                        )
                        return
                else:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Clip '{c_disp_name}' có raw_subtitles không hợp lệ.", "clip_id": c.clip_id}
                    )
                    return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 7. Xác định FPS chính xác (RationalFPS)
            fps_val = None
            if req.timeline_fps is not None:
                fps_val = req.timeline_fps
            elif req.video_plan_data and req.video_plan_data.get("fps"):
                fps_val = req.video_plan_data.get("fps")
            elif self._metadata_provider:
                try:
                    fps_val = self._metadata_provider(paths[0]).get("fps")
                except Exception:
                    pass
            else:
                try:
                    from src.core.autocut import get_media_metadata
                    m_fps = get_media_metadata(paths[0]).get("fps")
                    if m_fps and float(m_fps) > 0:
                        fps_val = float(m_fps)
                except Exception:
                    pass

            if fps_val is None:
                self._emit_terminal(
                    False,
                    "not_ready",
                    {"error": "Không xác định được FPS của timeline hoặc video nguồn.", "reason": "unknown_fps"}
                )
                return

            try:
                fps_rat = RationalFPS.from_value(fps_val)
            except Exception as fe:
                self._emit_terminal(
                    False,
                    "not_ready",
                    {"error": f"FPS không hợp lệ: {fps_val} ({fe})", "reason": "invalid_fps"}
                )
                return

            # 8. Giải quyết hình học các đoạn cắt (WordPlacement)
            placements: List[WordPlacement] = []

            # Xây dựng bảng tra cứu clip trong manifest
            clips_by_id: Dict[str, ClipEntry] = {c.clip_id: c for c in manifest.clips}
            clips_by_index: Dict[str, ClipEntry] = {str(i + 1): c for i, c in enumerate(manifest.clips)}
            clips_by_path: Dict[str, ClipEntry] = {os.path.normpath(os.path.abspath(c.current_path)): c for c in manifest.clips}

            # Kiểm tra tên basename có duy nhất hay bị trùng lặp
            basename_counts: Dict[str, int] = {}
            for c in manifest.clips:
                bname = os.path.basename(c.current_path).lower()
                basename_counts[bname] = basename_counts.get(bname, 0) + 1

            clips_by_unique_basename: Dict[str, ClipEntry] = {}
            for c in manifest.clips:
                bname = os.path.basename(c.current_path).lower()
                if basename_counts[bname] == 1:
                    clips_by_unique_basename[bname] = c

            def resolve_segment_clip(seg_dict: Dict[str, Any], tag: str) -> ClipEntry:
                declared_refs: List[Tuple[str, Any]] = []
                for key in ("clip_id", "clip_index", "clip_name", "path", "current_path"):
                    val = seg_dict.get(key)
                    if val is not None and (not isinstance(val, str) or val.strip()):
                        declared_refs.append((key, val))

                if not declared_refs:
                    raise LookupError(f"Phân đoạn '{tag}' không khai báo bất kỳ clip ref nào.")

                matched_entries: Dict[str, ClipEntry] = {}
                for ref_type, ref_val in declared_refs:
                    entry = None
                    ref_str = str(ref_val).strip()
                    if ref_type == "clip_id":
                        entry = clips_by_id.get(ref_str)
                    elif ref_type == "clip_index":
                        entry = clips_by_index.get(ref_str)
                    elif ref_type in ("path", "current_path"):
                        norm_p = os.path.normpath(os.path.abspath(ref_str))
                        entry = clips_by_path.get(norm_p)
                    elif ref_type == "clip_name":
                        if ref_str in clips_by_id:
                            entry = clips_by_id[ref_str]
                        elif ref_str in clips_by_index:
                            entry = clips_by_index[ref_str]
                        else:
                            bname = os.path.basename(ref_str).lower()
                            if bname in clips_by_unique_basename:
                                entry = clips_by_unique_basename[bname]
                            elif bname in basename_counts and basename_counts[bname] > 1:
                                raise ValueError(f"Tham chiếu clip_name '{ref_str}' không rõ ràng vì có nhiều tệp cùng tên '{bname}' trong dự án.")

                    if entry is None:
                        raise LookupError(f"Không tìm thấy clip tham chiếu {ref_type}='{ref_val}' trong manifest.")
                    matched_entries[entry.clip_id] = entry

                if len(matched_entries) > 1:
                    conflicts = [f"{k}='{v}' -> {entry.clip_id}" for (k, v), entry in zip(declared_refs, matched_entries.values())]
                    raise ValueError(f"Xung đột clip ref trong phân đoạn '{tag}': {', '.join(conflicts)} trỏ tới các clip khác nhau.")

                return list(matched_entries.values())[0]

            # Nhánh 8A: Đã có Video Plan (Slot 1)
            v_plan = req.video_plan_data
            has_plan = bool(v_plan and isinstance(v_plan, dict) and (
                v_plan.get("manifest_id") or v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments") or v_plan.get("global_hook")
            ))

            if has_plan:
                plan_mid = str(v_plan.get("manifest_id") or "").strip()
                if not plan_mid or plan_mid != manifest.manifest_id:
                    self._emit_terminal(
                        False,
                        "manifest_mismatch",
                        {"error": f"VideoPlan manifest_id '{plan_mid}' không khớp với manifest nguồn '{manifest.manifest_id}'."}
                    )
                    return

                from src.core.timeline_mapper import TimelineMapper
                mapper = TimelineMapper(fps=fps_rat.as_fraction(), speed_mode="off")
                segment_tags: List[str] = []

                # Nạp Global Hook nếu có
                gh = v_plan.get("global_hook")
                if gh and isinstance(gh, dict):
                    try:
                        clip_entry = resolve_segment_clip(gh, "hook")
                    except ValueError as ve:
                        self._emit_terminal(False, "ambiguous_clip_reference", {"error": str(ve)})
                        return
                    except LookupError as le:
                        self._emit_terminal(False, "clip_not_found", {"error": str(le)})
                        return

                    raw_spd = gh.get("speed", 1.0) if gh.get("speed") is not None else 1.0
                    spd = _validate_finite_float(raw_spd, min_val=0.01, max_val=100.0)
                    if spd is None or abs(spd - 1.0) > 1e-3:
                        self._emit_terminal(
                            False,
                            "unsupported_speed",
                            {"error": f"Tốc độ {raw_spd} của hook không hợp lệ hoặc chưa được hỗ trợ (chỉ hỗ trợ số hữu hạn 1.0x).", "speed": raw_spd}
                        )
                        return

                    raw_st = gh.get("start_sec", 0.0) if gh.get("start_sec") is not None else 0.0
                    raw_en = gh.get("end_sec", 0.0) if gh.get("end_sec") is not None else 0.0
                    st_val = _validate_finite_float(raw_st, min_val=0.0)
                    en_val = _validate_finite_float(raw_en, min_val=0.0)
                    if st_val is None or en_val is None:
                        self._emit_terminal(
                            False,
                            "invalid_time_range",
                            {"error": f"Mốc thời gian hook không hợp lệ: start_sec={raw_st}, end_sec={raw_en}"}
                        )
                        return

                    clip_dur = float(cached_scans_by_clip_id.get(clip_entry.clip_id, {}).get("clip_dur") or getattr(clip_entry, "duration_sec", 0.0) or 3600.0)

                    st = max(0.0, st_val)
                    en = en_val
                    if clip_dur > 0:
                        en = min(clip_dur, en)
                    if en > st:
                        mapper.add_segment(
                            clip_id=clip_entry.clip_id,
                            src_in_sec=st,
                            src_out_sec=en,
                            speed=1.0,
                            role="hook",
                            pad_sec=0.3,
                            clip_duration_sec=clip_dur,
                            clip_name=getattr(clip_entry, "file_name", None) or os.path.basename(clip_entry.current_path),
                            file_path=clip_entry.current_path,
                            title=gh.get("hook_title") or "Intro Teaser",
                        )
                        segment_tags.append("hook")

                # Nạp Timeline Segments
                segs = v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments") or []
                for s_idx, seg_dict in enumerate(segs):
                    if not isinstance(seg_dict, dict):
                        continue
                    tag = f"seg_{s_idx}"
                    try:
                        clip_entry = resolve_segment_clip(seg_dict, tag)
                    except ValueError as ve:
                        self._emit_terminal(False, "ambiguous_clip_reference", {"error": str(ve)})
                        return
                    except LookupError as le:
                        self._emit_terminal(False, "clip_not_found", {"error": str(le)})
                        return

                    raw_spd = seg_dict.get("speed", 1.0) if seg_dict.get("speed") is not None else 1.0
                    spd = _validate_finite_float(raw_spd, min_val=0.01, max_val=100.0)
                    if spd is None or abs(spd - 1.0) > 1e-3:
                        self._emit_terminal(
                            False,
                            "unsupported_speed",
                            {"error": f"Tốc độ {raw_spd} của phân đoạn {tag} không hợp lệ hoặc chưa được hỗ trợ (chỉ hỗ trợ 1.0x).", "speed": raw_spd}
                        )
                        return

                    raw_st = seg_dict.get("start_sec", 0.0) if seg_dict.get("start_sec") is not None else 0.0
                    raw_en = seg_dict.get("end_sec", 0.0) if seg_dict.get("end_sec") is not None else 0.0
                    st_val = _validate_finite_float(raw_st, min_val=0.0)
                    en_val = _validate_finite_float(raw_en, min_val=0.0)
                    if st_val is None or en_val is None:
                        self._emit_terminal(
                            False,
                            "invalid_time_range",
                            {"error": f"Mốc thời gian phân đoạn {tag} không hợp lệ: start_sec={raw_st}, end_sec={raw_en}"}
                        )
                        return

                    clip_dur = float(cached_scans_by_clip_id.get(clip_entry.clip_id, {}).get("clip_dur") or getattr(clip_entry, "duration_sec", 0.0) or 3600.0)

                    st = max(0.0, st_val)
                    en = en_val
                    if clip_dur > 0:
                        en = min(clip_dur, en)
                    if en > st:
                        mapper.add_segment(
                            clip_id=clip_entry.clip_id,
                            src_in_sec=st,
                            src_out_sec=en,
                            speed=1.0,
                            role=seg_dict.get("role") or "build",
                            pad_sec=0.3,
                            clip_duration_sec=clip_dur,
                            clip_name=getattr(clip_entry, "file_name", None) or os.path.basename(clip_entry.current_path),
                            file_path=clip_entry.current_path,
                            title=seg_dict.get("chapter_name") or seg_dict.get("title") or tag,
                        )
                        segment_tags.append(tag)

                # Chuyển đổi PlacedSegment từ mapper sang WordPlacement
                for idx, placed in enumerate(mapper.segments):
                    tag = segment_tags[idx] if idx < len(segment_tags) else ("hook" if placed.role == "hook" else f"seg_{idx}")
                    inst_id = f"{tag}_{placed.clip_id}_{idx}"
                    placements.append(
                        WordPlacement(
                            clip_id=placed.clip_id,
                            instance_id=inst_id,
                            src_in_sec=frame_to_pts(placed.src_in_frames, fps_rat),
                            src_out_sec=frame_to_pts(placed.src_out_frames, fps_rat),
                            rec_in_frame=placed.rec_in_frames,
                            rec_out_frame=placed.rec_out_frames,
                            applied_speed=Fraction(1, 1),
                        )
                    )

            # Nhánh 8B: Chưa có Video Plan
            else:
                review_cuts_modified = bool(ctx.get("review_cuts_modified", False))
                review_cuts = ctx.get("review_cuts")
                # Nếu người dùng đã sửa đổi keep/cut trong Review nhưng chưa có VideoPlan:
                # Báo explicit edit_plan_required thay vì âm thầm bỏ qua ý định của người dùng
                if review_cuts_modified or (review_cuts is not None and len(review_cuts) > 0 and review_cuts_modified):
                    self._emit_terminal(
                        False,
                        "edit_plan_required",
                        {"error": "Đã có thay đổi cắt/giữ trong bảng Review nhưng chưa có Video Plan để xác định hình học dựng cuối. Vui lòng tạo Video Plan trước."}
                    )
                    return

                # Chế độ source_preview đầy đủ clip theo thứ tự manifest
                timeline_cursor_frame = 0
                for p_idx, c in enumerate(manifest.clips):
                    cached_info = cached_scans_by_clip_id.get(c.clip_id, {})
                    dur_raw = cached_info.get("clip_dur") or getattr(c, "duration_sec", 0.0) or 0.0
                    try:
                        dur_sec = float(dur_raw)
                    except Exception:
                        dur_sec = 0.0
                    if dur_sec <= 0:
                        continue

                    dur_frac = Fraction(str(round(dur_sec, 6)))
                    dur_f = pts_to_frame(dur_frac, fps_rat)
                    if dur_f <= 0:
                        continue

                    rec_in_f = timeline_cursor_frame
                    rec_out_f = timeline_cursor_frame + dur_f
                    timeline_cursor_frame = rec_out_f

                    placements.append(
                        WordPlacement(
                            clip_id=c.clip_id,
                            instance_id=f"prev_{p_idx}_{c.clip_id}",
                            src_in_sec=Fraction(0, 1),
                            src_out_sec=dur_frac,
                            rec_in_frame=rec_in_f,
                            rec_out_frame=rec_out_f,
                            applied_speed=Fraction(1, 1),
                        )
                    )

            if not placements:
                self._emit_terminal(
                    False,
                    "no_data",
                    {"error": "Không có đoạn cắt nào hợp lệ để tạo dòng thời gian Kinetic."}
                )
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            # 9. Ánh xạ từ vựng sang Timeline qua pure mapper
            try:
                dataset: KineticDataset = map_words_to_timeline(
                    source_words_by_clip,
                    placements,
                    fps=fps_rat
                )
            except Exception as me:
                self._emit_terminal(
                    False,
                    "mapping_error",
                    {"error": f"Lỗi ánh xạ từ vựng sang dòng thời gian: {me}"}
                )
                return

            # Chuyển đổi MappedWord sang định dạng seconds cho downstream engine
            engine_words: List[Dict[str, Any]] = []
            for w in dataset.words:
                start_s = round(float(frame_to_pts(w.record_start_frame, fps_rat)), 3)
                end_s = round(float(frame_to_pts(w.record_end_frame, fps_rat)), 3)
                engine_words.append({
                    "word": w.text,
                    "start": start_s,
                    "end": end_s,
                    "index": w.index,
                    "clip_id": w.clip_id,
                    "instance_id": w.instance_id,
                    "record_start_frame": w.record_start_frame,
                    "record_end_frame": w.record_end_frame,
                    "clipped": w.clipped,
                })

            # 10. Xử lý theo từng Intent cụ thể
            result_payload: Dict[str, Any] = {
                "request_id": req_id,
                "generation": req.generation,
                "intent": req.intent,
                "aspect": req.aspect,
                "font": req.font,
                "manifest_id": manifest.manifest_id,
                "words": engine_words,
                "count": len(engine_words),
                "warnings": list(dataset.warnings),
                "timing_mode": dataset.timing_mode,
                "is_final_cut": has_plan,
                "is_source_preview": not has_plan,
            }

            # Intent A: copy_kinetic
            if req.intent == "copy_kinetic":
                if not engine_words:
                    self._emit_terminal(
                        False,
                        "no_dialogue",
                        {"error": "Không có từ thoại nào trong bản dựng để tạo Kinetic Prompt.", "words": []}
                    )
                    return

                try:
                    prompt_text = generate_kinetic_prompt(
                        engine_words,
                        aspect=req.aspect,
                        font=req.font
                    )
                except Exception as gpe:
                    self._emit_terminal(False, "formatter_error", {"error": f"Lỗi tạo Prompt Kinetic: {gpe}"})
                    return

                prompt_bytes = len(prompt_text.encode("utf-8"))
                if prompt_bytes > req.max_prompt_bytes:
                    self._emit_terminal(
                        False,
                        "budget_error",
                        {
                            "error": f"Kịch bản Kinetic ({prompt_bytes} bytes) vượt quá giới hạn ngân sách {req.max_prompt_bytes} bytes.",
                            "prompt_bytes": prompt_bytes,
                            "max_prompt_bytes": req.max_prompt_bytes
                        }
                    )
                    return

                mode = "style_only" if len(engine_words) > 300 else "standard"
                result_payload["prompt_text"] = prompt_text
                result_payload["prompt_bytes"] = prompt_bytes
                result_payload["mode"] = mode

            # Intent B: auto_plan
            elif req.intent == "auto_plan":
                if not engine_words:
                    self._emit_terminal(
                        False,
                        "no_dialogue",
                        {"error": "Không có từ thoại nào trong bản dựng để tự động tạo Kinetic Plan.", "words": []}
                    )
                    return

                try:
                    plan = auto_plan(
                        engine_words,
                        aspect=req.aspect,
                        max_words=3,
                        gap_s=0.45,
                        emphasis_ratio=0.12
                    )
                    validate_plan(plan, len(engine_words))
                except Exception as ape:
                    self._emit_terminal(False, "planner_error", {"error": f"Lỗi tạo kịch bản Kinetic offline: {ape}"})
                    return

                result_payload["plan"] = plan
                result_payload["pages_count"] = len(plan.get("pages", []))

            # Intent C: copy_transcript
            elif req.intent == "copy_transcript":
                if not engine_words:
                    self._emit_terminal(
                        False,
                        "no_dialogue",
                        {"error": "Không có từ thoại nào trong bản dựng để trích xuất transcript.", "words": []}
                    )
                    return

                transcript_str = " ".join([
                    f"[{w['index']}] {w['word']} ({w['start']:.2f}s - {w['end']:.2f}s)"
                    for w in engine_words
                ])
                result_payload["transcript_text"] = transcript_str

            # 11. Final Guard NGAY TRƯỚC Success: Recheck cancel + stat identity toàn bộ file nguồn
            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            for p in paths:
                norm_p = os.path.normpath(os.path.abspath(p))
                st_pre = st_pres.get(norm_p)
                try:
                    if not os.path.exists(norm_p) or not os.path.isfile(norm_p):
                        self._emit_terminal(
                            False,
                            "source_missing",
                            {"error": f"Tệp nguồn đã bị xóa trước khi hoàn tất: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    st_post = os.stat(norm_p)
                    if not stat.S_ISREG(st_post.st_mode):
                        self._emit_terminal(
                            False,
                            "source_error",
                            {"error": f"Tệp nguồn không còn là regular file: {norm_p}", "clip_path": norm_p}
                        )
                        return
                    if st_pre is not None:
                        if (
                            (st_post.st_size != st_pre.st_size)
                            or (st_post.st_mtime_ns != st_pre.st_mtime_ns)
                            or (st_post.st_ctime_ns != st_pre.st_ctime_ns)
                            or (st_post.st_dev != st_pre.st_dev)
                            or (st_post.st_ino != st_pre.st_ino)
                        ):
                            self._emit_terminal(
                                False,
                                "source_changed",
                                {"error": f"Tệp nguồn đã bị thay đổi trước khi hoàn tất: {norm_p}", "clip_path": norm_p}
                            )
                            return
                except Exception as exc:
                    self._emit_terminal(
                        False,
                        "source_error",
                        {"error": f"Lỗi kiểm tra tệp nguồn cuối: {norm_p} ({exc})", "clip_path": norm_p}
                    )
                    return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", {})
                return

            self._emit_terminal(True, "success", result_payload)

        except InterruptedError:
            self._emit_terminal(False, "cancelled", {})
        except Exception as exc:
            logger.exception("Lỗi không mong muốn trong KineticPreparationWorker: %s", exc)
            self._emit_terminal(False, "unexpected_error", {"error": str(exc)})
