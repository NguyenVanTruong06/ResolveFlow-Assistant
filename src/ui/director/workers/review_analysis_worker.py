"""
src/ui/director/workers/review_analysis_worker.py
=============================================================================
Luồng QThread nạp siêu dữ liệu (Metadata Loader) phục vụ ReviewPage:
- Đọc ngoại tuyến không chặn GUI: Audio ScanCacheManager (Schema v3) & VisualIndex.
- Xử lý bất đồng bộ, hỗ trợ cooperative cancellation (không dùng QThread.terminate).
- Bảo toàn xuất xứ (provenance) nguyên bản từ producer:
  - Speech status: transcribed, completed_empty, no_audio_track, unscanned, failed, unavailable, cancelled, source_missing, source_changed.
  - Visual observations: window_id, start_sec, end_sec, description, observed_actions, evidence_times, origin, model, analysis_status.
- Trả về payload độc lập theo generation token, bảo đảm an toàn khi người dùng thao tác nhanh.
- Outer fail-safe: đảm bảo đúng 1 tín hiệu terminal trong mọi kịch bản (success, cancel, error).
=============================================================================
"""

import os
import copy
import math
import logging
import urllib.parse
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


def _normalize_duration(raw_dur: Any) -> Tuple[float, bool]:
    """Chuẩn hóa an toàn thời lượng (> 0.0), trả về (duration_sec, is_known)."""
    if raw_dur is None or isinstance(raw_dur, bool):
        return 0.0, False
    try:
        val = float(raw_dur)
        if math.isnan(val) or math.isinf(val) or val <= 0.0 or val > 1e7:
            return 0.0, False
        return val, True
    except (TypeError, ValueError, OverflowError):
        return 0.0, False


def _normalize_timestamp(raw_ts: Any) -> Tuple[float, bool]:
    """Chuẩn hóa an toàn timestamp (>= 0.0), trả về (timestamp_sec, is_valid)."""
    if raw_ts is None or isinstance(raw_ts, bool):
        return 0.0, False
    try:
        val = float(raw_ts)
        if math.isnan(val) or math.isinf(val) or val < 0.0 or val > 1e7:
            return 0.0, False
        return val, True
    except (TypeError, ValueError, OverflowError):
        return 0.0, False


def _normalize_endpoint(url: str) -> str:
    """Chuẩn hóa URL endpoint (bỏ trailing slash, chuẩn hóa host loopback)."""
    if not url:
        return ""
    u = url.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlparse(u)
        netloc = parsed.netloc.lower()
        if netloc.startswith("localhost"):
            netloc = "127.0.0.1" + netloc[9:]
        return urllib.parse.urlunparse((parsed.scheme.lower() or "http", netloc, parsed.path, "", "", ""))
    except Exception:
        return u


@dataclass(frozen=True)
class ReviewAnalysisRequest:
    """Yêu cầu nạp siêu dữ liệu phân tích nguồn cho ReviewPage (Immutable Value Object)."""
    request_id: str
    generation: int
    video_paths: Tuple[str, ...]
    clip_infos: Tuple[Any, ...] = ()
    snapshot: Optional[Any] = None
    whisper_model: str = "small"
    whisper_language: str = "vi"
    vision_model: str = ""
    vision_endpoint: str = "http://127.0.0.1:11434"
    enable_visual_describe: bool = False
    runtime_results: Optional[Dict[str, Any]] = None
    window_sec: float = 8.0
    frames_per_window: int = 2
    max_windows: int = 10
    max_refinements: int = 1


class ReviewAnalysisWorker(QThread):
    """Worker QThread nạp siêu dữ liệu đọc (Read-Only) cho ReviewPage."""

    started_signal = Signal(str)                          # (request_id)
    progress_signal = Signal(str, int, int)               # (request_id, current, total)
    clip_loaded_signal = Signal(str, str, dict)           # (request_id, clip_path, payload)
    finished_signal = Signal(str, bool, str, dict)        # (request_id, success, message, summary)

    def __init__(
        self,
        request: ReviewAnalysisRequest,
        cache_mgr: Optional[Any] = None,
        visual_index: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.request = request
        self._is_cancelled = False
        self._terminal_emitted = False

        # Chỉ lưu tham số DI, KHÔNG tạo ScanCacheManager/VisualIndex trên GUI thread
        self._cache_mgr_arg = cache_mgr
        self._visual_index_arg = visual_index
        self._cache_mgr = None
        self._visual_index = None

    def cancel(self):
        """Yêu cầu dừng cooperative luồng nạp."""
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def _emit_terminal(self, success: bool, message: str, summary: Dict[str, Any]):
        if not self._terminal_emitted:
            self._terminal_emitted = True
            self.finished_signal.emit(self.request.request_id, success, message, copy.deepcopy(summary))

    def run(self):
        req_id = self.request.request_id
        paths = list(self.request.video_paths)
        total = len(paths)

        summary: Dict[str, Any] = {
            "total_clips": total,
            "loaded_clips": 0,
            "usable_clips": 0,
            "invalid_source_clips": 0,
            "failed_clips": 0,
            "cache_miss_clips": 0,
            "clips_with_speech": 0,
            "clips_silent": 0,
            "clips_with_visual": 0,
            "speech_status_counts": {},
            "visual_status_counts": {},
        }

        try:
            self.started_signal.emit(req_id)

            if total == 0:
                self._emit_terminal(True, "zero_clips", summary)
                return

            if self._is_cancelled:
                self._emit_terminal(False, "cancelled", summary)
                return

            # Khởi tạo lazy các manager trên luồng worker nền
            if self._cache_mgr_arg is not None:
                self._cache_mgr = self._cache_mgr_arg
            else:
                from src.core.cache_manager import ScanCacheManager
                self._cache_mgr = ScanCacheManager()

            if self._visual_index_arg is not None:
                self._visual_index = self._visual_index_arg
            else:
                from src.core.visual_index import VisualIndex
                self._visual_index = VisualIndex()

            from src.core.whisper_cleaner import clean_whisper_segments

            for idx, raw_path in enumerate(paths):
                if self._is_cancelled:
                    self._emit_terminal(False, "cancelled", summary)
                    return

                norm_path = os.path.normpath(os.path.abspath(raw_path))
                display_name = os.path.basename(norm_path)

                try:
                    # Pre-stat kiểm tra nguồn
                    pre_stat = None
                    try:
                        pre_stat = os.stat(norm_path)
                    except (FileNotFoundError, OSError) as stat_err:
                        logger.debug("Không thể đọc stat của %s: %s", display_name, stat_err)

                    if pre_stat is None:
                        # Tệp nguồn không tồn tại trên đĩa
                        payload = {
                            "clip_path": norm_path,
                            "display_name": display_name,
                            "duration_sec": 0.0,
                            "duration_known": False,
                            "speech_status": "source_missing",
                            "subtitle_rows": [],
                            "visual_status": "source_missing",
                            "visual_observations": [],
                            "visual_coverage": {},
                            "stale_warning": "Tệp nguồn không tồn tại trên đĩa",
                            "has_speech": False,
                            "is_silent": False,
                            "error": "source_missing",
                        }
                        summary["loaded_clips"] += 1
                        summary["invalid_source_clips"] += 1
                        summary["speech_status_counts"]["source_missing"] = summary["speech_status_counts"].get("source_missing", 0) + 1
                        summary["visual_status_counts"]["source_missing"] = summary["visual_status_counts"].get("source_missing", 0) + 1
                        self.clip_loaded_signal.emit(req_id, norm_path, payload)
                        self.progress_signal.emit(req_id, idx + 1, total)
                        continue

                    # 1. Tra cứu thời lượng (an toàn, không crash)
                    duration_sec = 0.0
                    duration_known = False
                    for ci in self.request.clip_infos:
                        ci_path = getattr(ci, "path", ci) if not isinstance(ci, str) else ci
                        if ci_path and os.path.normpath(os.path.abspath(str(ci_path))) == norm_path:
                            d_val, d_ok = _normalize_duration(getattr(ci, "duration_sec", None))
                            if d_ok:
                                duration_sec = d_val
                                duration_known = True
                            break

                    # 2. Bóc tách siêu dữ liệu Lời thoại (Speech Evidence)
                    speech_status = "unscanned"
                    raw_subtitles: List[Dict[str, Any]] = []

                    # Kiểm tra runtime result khớp snapshot đầy đủ
                    runtime_valid = False
                    runtime_clip = None
                    snap = self.request.snapshot
                    if snap is not None and self.request.runtime_results:
                        snap_paths = tuple(os.path.normpath(os.path.abspath(p)) for p in getattr(snap, "video_paths", ()))
                        req_paths = tuple(os.path.normpath(os.path.abspath(p)) for p in self.request.video_paths)
                        snap_w_model = getattr(snap, "whisper_model", "")
                        snap_w_lang = getattr(snap, "whisper_language", "")
                        
                        paths_match = (set(snap_paths) == set(req_paths) and norm_path in snap_paths)
                        audio_match = (snap_w_model == self.request.whisper_model and snap_w_lang == self.request.whisper_language)
                        v_match = True
                        if self.request.enable_visual_describe or getattr(snap, "enable_visual_describe", False):
                            v_match = (
                                bool(getattr(snap, "enable_visual_describe", False)) == bool(self.request.enable_visual_describe)
                                and getattr(snap, "vision_model", "") == self.request.vision_model
                            )
                        
                        if paths_match and audio_match and v_match:
                            if norm_path in self.request.runtime_results:
                                runtime_clip = self.request.runtime_results[norm_path]
                                runtime_valid = True

                    if runtime_valid and runtime_clip:
                        rt_audio = runtime_clip.get("audio_status")
                        if rt_audio in ("no_audio_track", "no_speech", "completed_empty"):
                            speech_status = "no_audio_track" if rt_audio == "no_audio_track" else "completed_empty"
                            raw_subtitles = []
                        elif rt_audio in ("failed", "unavailable", "cancelled", "skipped"):
                            speech_status = rt_audio
                            raw_subtitles = []
                        elif rt_audio in ("cached", "new", "transcribed"):
                            raw_subs = runtime_clip.get("raw_subtitles")
                            if not isinstance(raw_subs, list):
                                speech_status = "unscanned"
                                raw_subtitles = []
                            elif len(raw_subs) > 0:
                                speech_status = "transcribed"
                                raw_subtitles = raw_subs
                            else:
                                speech_status = "completed_empty"
                                raw_subtitles = []
                        else:
                            speech_status = "unscanned"
                            raw_subtitles = []
                    else:
                        cached_audio = self._cache_mgr.get_cached_scan(
                            norm_path,
                            self.request.whisper_model,
                            self.request.whisper_language
                        )

                        if isinstance(cached_audio, dict):
                            if not duration_known:
                                d_val, d_ok = _normalize_duration(cached_audio.get("clip_dur") or cached_audio.get("duration"))
                                if d_ok:
                                    duration_sec = d_val
                                    duration_known = True

                            if cached_audio.get("status") == "error":
                                speech_status = "failed"
                            elif cached_audio.get("no_audio_track") is True:
                                speech_status = "no_audio_track"
                            else:
                                raw_subs = cached_audio.get("raw_subtitles")
                                if raw_subs is None or not isinstance(raw_subs, list):
                                    speech_status = "unscanned"
                                    raw_subtitles = []
                                elif len(raw_subs) > 0:
                                    speech_status = "transcribed"
                                    raw_subtitles = raw_subs
                                else:
                                    if cached_audio.get("status") in ("complete", "completed", "cached", "new"):
                                        speech_status = "completed_empty"
                                        raw_subtitles = []
                                    else:
                                        speech_status = "unscanned"
                                        raw_subtitles = []

                    subtitle_rows: List[List[Any]] = []
                    if raw_subtitles and isinstance(raw_subtitles, list):
                        try:
                            clean_subs = clean_whisper_segments(raw_subtitles)
                            for sub in clean_subs:
                                if not isinstance(sub, dict):
                                    continue
                                st_val, st_ok = _normalize_timestamp(sub.get("start", 0.0))
                                en_val, en_ok = _normalize_timestamp(sub.get("end", 0.0))
                                if not st_ok or not en_ok or en_val <= st_val:
                                    continue
                                h = int(st_val // 3600)
                                m = int((st_val % 3600) // 60)
                                s = int(st_val % 60)
                                tc = f"{h:02d}:{m:02d}:{s:02d}"
                                txt = str(sub.get("text", "")).strip()
                                if txt:
                                    subtitle_rows.append([tc, txt, 0, ""])
                        except Exception as clean_err:
                            logger.debug("Lỗi làm sạch phụ đề: %s", clean_err)

                    # 3. Bóc tách siêu dữ liệu Thị giác (Visual Observations & Nested Schema)
                    visual_observations: List[Dict[str, Any]] = []
                    visual_coverage: Dict[str, Any] = {}
                    visual_status = "unscanned"
                    stale_warning: Optional[str] = None

                    cached_visual = self._visual_index.load(norm_path)

                    if isinstance(cached_visual, dict):
                        analysis_block = cached_visual.get("analysis", {})
                        sig_data = analysis_block.get("signature_data", {})
                        visual_status = analysis_block.get("status") or cached_visual.get("status") or "unknown"
                        # Phân biệt rõ analyzed coverage và sampling overview coverage
                        visual_coverage = analysis_block.get("coverage") or cached_visual.get("coverage", {})

                        stored_model = sig_data.get("model") or cached_visual.get("model") or cached_visual.get("vision_model")
                        stored_endpoint = sig_data.get("endpoint") or cached_visual.get("vision_endpoint")
                        sig_sampling = sig_data.get("sampling", {})
                        sig_refinement = sig_data.get("refinement_policy", {})

                        if self.request.enable_visual_describe and stored_model:
                            mismatch_reasons = []
                            if self.request.vision_model and stored_model != self.request.vision_model:
                                mismatch_reasons.append(f"mô hình '{stored_model}' ≠ '{self.request.vision_model}'")
                            if self.request.vision_endpoint and stored_endpoint:
                                req_ep = _normalize_endpoint(self.request.vision_endpoint)
                                st_ep = _normalize_endpoint(stored_endpoint)
                                if req_ep != st_ep:
                                    mismatch_reasons.append(f"endpoint '{stored_endpoint}' ≠ '{self.request.vision_endpoint}'")
                            if sig_sampling:
                                w_sec = sig_sampling.get("window_sec")
                                if w_sec is not None and abs(float(w_sec) - float(self.request.window_sec)) > 0.01:
                                    mismatch_reasons.append(f"cửa sổ {w_sec}s ≠ {self.request.window_sec}s")
                                fpw = sig_sampling.get("frames_per_window")
                                if fpw is not None and int(fpw) != int(self.request.frames_per_window):
                                    mismatch_reasons.append(f"mẫu khung {fpw} ≠ {self.request.frames_per_window}")
                                mw = sig_sampling.get("max_windows")
                                if mw is not None and int(mw) != int(self.request.max_windows):
                                    mismatch_reasons.append(f"số cửa sổ tối đa {mw} ≠ {self.request.max_windows}")
                            if sig_refinement:
                                mr = sig_refinement.get("max_refinements")
                                if mr is not None and int(mr) != int(self.request.max_refinements):
                                    mismatch_reasons.append(f"ngân sách tinh chỉnh {mr} ≠ {self.request.max_refinements}")

                            if mismatch_reasons:
                                stale_warning = f"Lệch cấu hình mô tả thị giác ({', '.join(mismatch_reasons)})"

                        descs = cached_visual.get("descriptions") or cached_visual.get("visual_segments") or []
                        for d in descs:
                            if not isinstance(d, dict):
                                continue
                            origin = str(d.get("origin", ""))
                            obs_status = d.get("analysis_status", "described")

                            # Cảnh báo lệch cấu hình CHỈ áp dụng cho quan sát sinh bởi mô hình AI;
                            # Ghi chú thủ công của người dùng (origin='user') luôn được bảo toàn 100%!
                            if stale_warning and origin != "user":
                                obs_status = "stale"

                            st_val, _ = _normalize_timestamp(d.get("start_sec", d.get("time_sec", 0.0)))
                            en_val, _ = _normalize_timestamp(d.get("end_sec", 0.0))

                            visual_observations.append({
                                "window_id": str(d.get("window_id", "")),
                                "start_sec": st_val,
                                "end_sec": en_val,
                                "description": str(d.get("description", "")).strip(),
                                "observed_actions": d.get("observed_actions", []),
                                "evidence_times": d.get("evidence_times", []),
                                "evidence_frame_ids": d.get("evidence_frame_ids", []),
                                "analysis_status": obs_status,
                                "origin": origin,
                                "model": str(d.get("model", stored_model or "")),
                            })

                    # Post-Stat: Luôn kiểm tra nguồn không bị đổi/xóa giữa chừng
                    is_invalid_post = False
                    if pre_stat is not None:
                        try:
                            post_stat = os.stat(norm_path)
                            mtime_changed = (
                                getattr(post_stat, "st_mtime_ns", None) != getattr(pre_stat, "st_mtime_ns", None)
                                if hasattr(post_stat, "st_mtime_ns") and hasattr(pre_stat, "st_mtime_ns")
                                else post_stat.st_mtime != pre_stat.st_mtime
                            )
                            size_changed = (post_stat.st_size != pre_stat.st_size)
                            ino_changed = (
                                getattr(post_stat, "st_ino", None) != getattr(pre_stat, "st_ino", None)
                                if hasattr(post_stat, "st_ino") and hasattr(pre_stat, "st_ino")
                                else False
                            )
                            dev_changed = (
                                getattr(post_stat, "st_dev", None) != getattr(pre_stat, "st_dev", None)
                                if hasattr(post_stat, "st_dev") and hasattr(pre_stat, "st_dev")
                                else False
                            )
                            if mtime_changed or size_changed or ino_changed or dev_changed:
                                stale_warning = (stale_warning + "; " if stale_warning else "") + "Tệp nguồn đã bị chỉnh sửa trên đĩa"
                                speech_status = "source_changed"
                                visual_status = "source_changed"
                                # Tệp đổi: xóa dữ liệu cũ, không publish cho UI
                                subtitle_rows = []
                                visual_observations = []
                                visual_coverage = {}
                                is_invalid_post = True
                        except (FileNotFoundError, OSError):
                            stale_warning = "Tệp nguồn bị mất trong quá trình đọc"
                            speech_status = "source_missing"
                            visual_status = "source_missing"
                            # Tệp mất: xóa dữ liệu cũ, không publish cho UI
                            subtitle_rows = []
                            visual_observations = []
                            visual_coverage = {}
                            is_invalid_post = True

                    # Check cancel trước khi publish
                    if self._is_cancelled:
                        self._emit_terminal(False, "cancelled", summary)
                        return

                    if is_invalid_post:
                        payload = {
                            "clip_path": norm_path,
                            "display_name": display_name,
                            "duration_sec": duration_sec,
                            "duration_known": duration_known,
                            "speech_status": speech_status,
                            "subtitle_rows": [],
                            "visual_status": visual_status,
                            "visual_observations": [],
                            "visual_coverage": {},
                            "stale_warning": stale_warning,
                            "has_speech": False,
                            "is_silent": False,
                            "error": speech_status,
                        }
                        summary["loaded_clips"] += 1
                        summary["invalid_source_clips"] += 1
                        summary["speech_status_counts"][speech_status] = summary["speech_status_counts"].get(speech_status, 0) + 1
                        summary["visual_status_counts"][visual_status] = summary["visual_status_counts"].get(visual_status, 0) + 1
                        self.clip_loaded_signal.emit(req_id, norm_path, payload)
                        self.progress_signal.emit(req_id, idx + 1, total)
                        continue

                    # Định nghĩa chính xác is_silent: CHỈ khi có bằng chứng âm thanh không có thoại / không có track
                    has_speech = (speech_status == "transcribed" and len(subtitle_rows) > 0)
                    is_silent = (speech_status in ("completed_empty", "no_audio_track") and len(subtitle_rows) == 0)

                    payload = {
                        "clip_path": norm_path,
                        "display_name": display_name,
                        "duration_sec": duration_sec,
                        "duration_known": duration_known,
                        "speech_status": speech_status,
                        "subtitle_rows": subtitle_rows,
                        "visual_status": visual_status,
                        "visual_observations": visual_observations,
                        "visual_coverage": visual_coverage,
                        "stale_warning": stale_warning,
                        "has_speech": has_speech,
                        "is_silent": is_silent,
                    }

                    # Cập nhật thống kê trung thực
                    summary["loaded_clips"] += 1
                    summary["usable_clips"] += 1
                    if has_speech:
                        summary["clips_with_speech"] += 1
                    elif is_silent:
                        summary["clips_silent"] += 1

                    if visual_observations:
                        summary["clips_with_visual"] += 1

                    # Nhận diện cache miss: nguồn online nhưng không có bằng chứng phân tích nào từ cả hai kênh
                    has_speech_analysis = (has_speech or is_silent or speech_status in ("failed", "unavailable", "cancelled"))
                    has_visual_analysis = (bool(visual_observations) or visual_status in ("completed", "partial", "failed"))
                    if not has_speech_analysis and not has_visual_analysis:
                        summary["cache_miss_clips"] += 1

                    summary["speech_status_counts"][speech_status] = summary["speech_status_counts"].get(speech_status, 0) + 1
                    summary["visual_status_counts"][visual_status] = summary["visual_status_counts"].get(visual_status, 0) + 1

                    self.clip_loaded_signal.emit(req_id, norm_path, payload)
                    self.progress_signal.emit(req_id, idx + 1, total)

                except Exception as clip_err:
                    logger.warning("Lỗi xử lý clip %s: %s", display_name, clip_err)
                    # Isolate lỗi từng clip: không làm sập cả request, giữ clip trong inventory!
                    payload = {
                        "clip_path": norm_path,
                        "display_name": display_name,
                        "duration_sec": 0.0,
                        "duration_known": False,
                        "speech_status": "failed",
                        "subtitle_rows": [],
                        "visual_status": "failed",
                        "visual_observations": [],
                        "visual_coverage": {},
                        "stale_warning": f"Lỗi nạp siêu dữ liệu: {str(clip_err)}",
                        "has_speech": False,
                        "is_silent": False,
                        "error": str(clip_err),
                    }
                    summary["loaded_clips"] += 1
                    summary["failed_clips"] = summary.get("failed_clips", 0) + 1
                    summary["last_error"] = str(clip_err)
                    summary["speech_status_counts"]["failed"] = summary["speech_status_counts"].get("failed", 0) + 1
                    summary["visual_status_counts"]["failed"] = summary["visual_status_counts"].get("failed", 0) + 1
                    self.clip_loaded_signal.emit(req_id, norm_path, payload)
                    self.progress_signal.emit(req_id, idx + 1, total)
                    continue

            usable_count = summary.get("usable_clips", 0)
            invalid_count = summary.get("invalid_source_clips", 0)
            failed_count = summary.get("failed_clips", 0)
            cache_miss_count = summary.get("cache_miss_clips", 0)

            if usable_count == 0:
                if invalid_count > 0 and failed_count == 0:
                    self._emit_terminal(False, "source_unavailable", summary)
                elif failed_count > 0 and invalid_count == 0:
                    last_err = summary.get("last_error", "all_failed")
                    self._emit_terminal(False, f"all_failed: {last_err}", summary)
                else:
                    self._emit_terminal(False, "source_unavailable", summary)
            elif usable_count < total:
                self._emit_terminal(True, "partial", summary)
            else:
                # usable_count == total (tất cả nguồn khả dụng)
                if cache_miss_count == total:
                    self._emit_terminal(True, "no_data", summary)
                else:
                    self._emit_terminal(True, "completed", summary)

        except Exception as exc:
            logger.exception("Lỗi ngoại lệ trong luồng ReviewAnalysisWorker: %s", exc)
            summary["error"] = str(exc)
            self._emit_terminal(False, f"worker_error: {exc}", summary)
