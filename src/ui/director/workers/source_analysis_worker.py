"""
SourceAnalysisWorker & SourceAnalysisConfig
===========================================
Worker phân tích nguồn độc lập chạy trên background QThread.
Điều phối tuần tự: Audio STT (tái dùng v3 verified cache) + Visual Sampling (tái dùng VisualIndex).
Cách ly 100% khỏi DaVinci Resolve, không tạo _TIMELINE_IMPORT, không mutate pipeline/timeline.
"""

import copy
import math
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from src.core.cache_manager import (
    ScanCacheManager,
    compute_full_file_sha256,
    normalize_language_code,
    validate_file_identity,
)
from src.core.transcriber import ModelConfig, ResolveTranscriber
from src.core.visual_index import VisualIndex
from src.core.whisper_cleaner import clean_whisper_segments_with_stats
from src.core.audio import AudioExtractor


@dataclass(frozen=True)
class SourceAnalysisConfig:
    """Snapshot cấu hình bất biến cho phiên phân tích nguồn."""
    job_id: str
    video_paths: Tuple[str, ...]
    whisper_model: str = "small"
    whisper_language: str = "vi"
    enable_audio: bool = True
    enable_visual_sampling: bool = True
    visual_interval_sec: float = 2.0
    visual_max_samples: int = 120
    local_files_only: bool = True
    # Các trường phụ gia (additive) cho Stage 3 Mô tả thị giác (Visual Describe)
    enable_visual_describe: bool = False
    vision_model: str = ""
    vision_endpoint: str = "http://127.0.0.1:11434"
    window_sec: float = 8.0
    frames_per_window: int = 2
    max_windows: int = 10
    max_refinements: int = 1


def _default_probe_has_audio(video_path: str) -> bool:
    """Thực hiện probe stream metadata bằng ffprobe/PyAV bounded để kiểm tra có luồng audio không."""
    from src.core.autocut import get_media_metadata
    meta = get_media_metadata(video_path)
    return bool(meta.get("has_audio", False))


def _default_describer_fn(*args, **kwargs):
    """Hàm mô tả thị giác mặc định gọi core describe_with_ollama (lazy import)."""
    from src.core.visual_describer import describe_with_ollama
    return describe_with_ollama(*args, **kwargs)


def _default_local_model_info_fn(model: str, endpoint: str = "http://127.0.0.1:11434"):
    """Hàm tra cứu thông tin mô hình thị giác mặc định qua local_model_info (lazy import)."""
    from src.core.visual_describer import local_model_info
    return local_model_info(model, endpoint=endpoint)


def _default_get_duration(video_path: str) -> float:
    """Lấy thời lượng container của tệp video bằng ffprobe/AudioExtractor/get_media_metadata."""
    try:
        from src.core.audio import AudioExtractor
        dur = AudioExtractor.get_audio_duration(video_path)
        if _is_valid_positive_duration(dur):
            return float(dur)
    except Exception:
        pass
    try:
        from src.core.autocut import get_media_metadata
        meta = get_media_metadata(video_path)
        dur = meta.get("duration", 0.0)
        if _is_valid_positive_duration(dur):
            return float(dur)
    except Exception:
        pass
    return 0.0


def _is_valid_positive_duration(dur: Any) -> bool:
    """Kiểm tra thời lượng phải là số hữu hạn thực dương (> 0)."""
    if not isinstance(dur, (int, float)):
        return False
    if math.isnan(dur) or math.isinf(dur):
        return False
    return dur > 0.0


class SourceAnalysisWorker(QThread):
    """
    Worker thực thi phân tích nguồn ngoại tuyến trên luồng nền.
    Tách biệt hoàn toàn khỏi UI Widget và DaVinci Resolve.
    """

    # Signals mang job_id
    progress_signal = Signal(str, int, int)              # (job_id, current_step, total_steps)
    status_signal = Signal(str, str, dict)               # (job_id, clip_path, clip_result_dict)
    counters_signal = Signal(str, dict)                  # (job_id, counters_dict)
    finished_signal = Signal(str, bool, str, dict)       # (job_id, success, message, counters_dict)
    log_signal = Signal(str, str)                        # (job_id, log_text)

    def __init__(
        self,
        config: SourceAnalysisConfig,
        parent=None,
        cache_mgr: Optional[Any] = None,
        visual_index: Optional[Any] = None,
        transcriber_factory: Optional[Callable[[ModelConfig], Any]] = None,
        audio_probe_fn: Optional[Callable[[str], bool]] = None,
        hash_fn: Optional[Callable[..., Tuple[Any, Any]]] = None,
        validate_identity_fn: Optional[Callable[..., bool]] = None,
        describer_fn: Optional[Callable[..., Any]] = None,
        local_model_info_fn: Optional[Callable[..., Any]] = None,
        duration_fn: Optional[Callable[[str], float]] = None,
    ):
        super().__init__(parent)
        self.config = config
        self._is_cancelled = False
        self._terminal_emitted = False

        # DI dependencies cho kiểm thử
        self._cache_mgr = cache_mgr if cache_mgr is not None else ScanCacheManager()
        self._visual_index = visual_index if visual_index is not None else VisualIndex()
        self._transcriber_factory = transcriber_factory if transcriber_factory is not None else ResolveTranscriber
        self._audio_probe_fn = audio_probe_fn if audio_probe_fn is not None else _default_probe_has_audio
        self._hash_fn = hash_fn if hash_fn is not None else compute_full_file_sha256
        self._validate_identity_fn = validate_identity_fn if validate_identity_fn is not None else validate_file_identity
        self._describer_fn = describer_fn if describer_fn is not None else _default_describer_fn
        self._local_model_info_fn = local_model_info_fn if local_model_info_fn is not None else _default_local_model_info_fn
        self._duration_fn = duration_fn if duration_fn is not None else _default_get_duration

        # Per-clip state tracker: nguồn chân lý duy nhất cho bộ đếm và trạng thái
        paths = list(self.config.video_paths)
        self._clip_states: List[Dict[str, Any]] = []
        for vp in paths:
            self._clip_states.append({
                "path": vp,
                "audio_state": "pending" if self.config.enable_audio else "skipped",
                "visual_state": "pending" if self.config.enable_visual_sampling else "skipped",
                "visual_describe_state": "pending" if self.config.enable_visual_describe else "skipped",
                "no_speech": False,
                "error": None,
                "cache_saved": None,
                "persisted": None,
            })

    def cancel(self):
        """Yêu cầu hủy tiến trình an toàn (cooperative cancellation)."""
        self._is_cancelled = True

    def _compute_counters(self) -> dict:
        """Tính toán snapshot bộ đếm từ per-clip state model đảm bảo phân hoạch chuẩn tắc."""
        audio_counts = {
            "cached": 0,
            "new": 0,
            "no_audio_track": 0,
            "failed": 0,
            "unavailable": 0,
            "skipped": 0,
            "cancelled": 0,
            "pending": 0,
            "no_speech": 0,  # Thuộc tính phụ của completed audio (raw_subtitles == [])
        }
        visual_counts = {
            "cached": 0,
            "new": 0,
            "failed": 0,
            "skipped": 0,
            "cancelled": 0,
            "pending": 0,
        }
        visual_describe_counts = {
            "cached": 0,
            "new": 0,
            "partial": 0,
            "unavailable": 0,
            "failed": 0,
            "skipped": 0,
            "cancelled": 0,
            "pending": 0,
        }

        for clip in self._clip_states:
            a_st = clip["audio_state"]
            if a_st in audio_counts:
                audio_counts[a_st] += 1
            if clip.get("no_speech"):
                audio_counts["no_speech"] += 1

            v_st = clip["visual_state"]
            if v_st in visual_counts:
                visual_counts[v_st] += 1

            vd_st = clip.get("visual_describe_state", "skipped")
            if vd_st in visual_describe_counts:
                visual_describe_counts[vd_st] += 1

        return {
            "total_clips": len(self._clip_states),
            "audio": audio_counts,
            "visual": visual_counts,
            "visual_describe": visual_describe_counts,
        }

    def _terminalize_pending(self, reason: str = "cancelled"):
        """Chuyển toàn bộ các stage còn ở trạng thái pending thành reason (cancelled hoặc failed)."""
        for clip in self._clip_states:
            if clip["audio_state"] == "pending":
                clip["audio_state"] = reason
            if clip["visual_state"] == "pending":
                clip["visual_state"] = reason
            if clip.get("visual_describe_state") == "pending":
                clip["visual_describe_state"] = reason

    def _emit_counters(self):
        """Phát counters_signal với deep copy độc lập, tránh race condition với consumer."""
        snapshot = copy.deepcopy(self._compute_counters())
        self.counters_signal.emit(self.config.job_id, snapshot)

    def _emit_status(self, clip_path: str, clip_res: dict):
        """Phát status_signal với deep copy độc lập cho từng clip."""
        snapshot = copy.deepcopy(clip_res)
        self.status_signal.emit(self.config.job_id, clip_path, snapshot)

    def _emit_terminal(self, success: bool, message: str, counters: dict):
        """Bảo đảm phát đúng EXACTLY 1 lần finished_signal cho toàn bộ vòng đời phiên."""
        if not self._terminal_emitted:
            self._terminal_emitted = True
            snapshot = copy.deepcopy(counters)
            self.finished_signal.emit(self.config.job_id, success, message, snapshot)

    def run(self):
        job_id = self.config.job_id
        paths = list(self.config.video_paths)
        total_clips = len(paths)

        # 1. Reject 0 clips ngay từ đầu
        if total_clips == 0:
            counters = self._compute_counters()
            self.log_signal.emit(job_id, "⚠️ Không có clip nào trong danh sách quét.")
            self._emit_terminal(False, "zero_clips", counters)
            return

        # 2. Reject khi tất cả các stages đều bị tắt (nothing selected)
        if (not self.config.enable_audio
                and not self.config.enable_visual_sampling
                and not self.config.enable_visual_describe):
            counters = self._compute_counters()
            self.log_signal.emit(job_id, "⚠️ Không có công đoạn phân tích nào được chọn (Audio, Visual và Mô tả AI đều tắt).")
            self._emit_terminal(False, "nothing_selected", counters)
            return

        # Enforce offline: source worker luôn offline, không bao giờ tải model qua mạng
        if not self.config.local_files_only:
            self.log_signal.emit(
                job_id,
                "⚠️ [Cảnh báo] SourceAnalysisWorker luôn hoạt động ở chế độ ngoại tuyến (enforce local_files_only=True)."
            )

        job_temp_dir: Optional[str] = None
        transcriber: Optional[Any] = None
        has_partial_error = False

        try:
            # Cấp phát thư mục tạm độc quyền trong try/except
            try:
                job_temp_dir = tempfile.mkdtemp(prefix=f"chundvc_source_{job_id}_")
            except Exception as tmp_err:
                self.log_signal.emit(job_id, f"❌ Không thể tạo thư mục tạm: {tmp_err}")
                self._terminalize_pending("failed")
                self._emit_counters()
                self._emit_terminal(False, f"temp_dir_allocation_failed: {tmp_err}", self._compute_counters())
                return

            self.log_signal.emit(job_id, f"🚀 Bắt đầu phân tích nguồn {total_clips} clip (Job: {job_id})...")

            for idx, vp in enumerate(paths):
                # Ranh giới hủy trước mỗi clip
                if self._is_cancelled:
                    self._terminalize_pending("cancelled")
                    self._emit_counters()
                    self._emit_terminal(False, "cancelled", self._compute_counters())
                    return

                self.progress_signal.emit(job_id, idx, total_clips)
                clip_name = os.path.basename(vp)
                self.log_signal.emit(job_id, f"\n🎬 [{idx+1}/{total_clips}] Phân tích nguồn: {clip_name}")

                clip_res = {
                    "path": vp,
                    "audio_status": self._clip_states[idx]["audio_state"],
                    "visual_status": self._clip_states[idx]["visual_state"],
                    "visual_describe_status": self._clip_states[idx]["visual_describe_state"],
                    "no_speech": False,
                    "raw_subtitles": [],
                    "audio_segments": [],
                    "clip_dur": 0.0,
                    "descriptions": [],
                    "analysis_quality": None,
                    "analysis_coverage": None,
                    "cache_saved": None,
                    "persisted": None,
                    "error": None,
                    "warning": None,
                }

                # ----------------- CHẶNG 0: BĂM FULL SHA256 & IDENTITY -----------------
                if not os.path.exists(vp) or not os.path.isfile(vp):
                    clip_res["audio_status"] = "failed"
                    clip_res["visual_status"] = "failed"
                    clip_res["visual_describe_status"] = "failed" if self.config.enable_visual_describe else "skipped"
                    clip_res["error"] = "file_not_found"
                    self._clip_states[idx]["audio_state"] = "failed"
                    self._clip_states[idx]["visual_state"] = "failed"
                    if self.config.enable_visual_describe:
                        self._clip_states[idx]["visual_describe_state"] = "failed"
                    has_partial_error = True
                    self._emit_status(vp, clip_res)
                    self._emit_counters()
                    continue

                full_hash = None
                prov = None
                try:
                    full_hash, prov = self._hash_fn(vp, cancelled_fn=lambda: self._is_cancelled)
                except Exception as hash_exc:
                    clip_res["audio_status"] = "failed"
                    clip_res["visual_status"] = "failed"
                    clip_res["visual_describe_status"] = "failed" if self.config.enable_visual_describe else "skipped"
                    clip_res["error"] = f"hash_error: {str(hash_exc)}"
                    self._clip_states[idx]["audio_state"] = "failed"
                    self._clip_states[idx]["visual_state"] = "failed"
                    if self.config.enable_visual_describe:
                        self._clip_states[idx]["visual_describe_state"] = "failed"
                    has_partial_error = True
                    self.log_signal.emit(job_id, f"   ❌ Lỗi băm định danh nguồn: {hash_exc}")
                    self._emit_status(vp, clip_res)
                    self._emit_counters()
                    continue

                # Ranh giới hủy sau khi băm
                if self._is_cancelled:
                    self._terminalize_pending("cancelled")
                    self._emit_counters()
                    self._emit_terminal(False, "cancelled", self._compute_counters())
                    return

                # Xác thực identity trước khi phân tích
                identity_ok = False
                try:
                    identity_ok = bool(full_hash and prov and self._validate_identity_fn(vp, prov))
                except Exception as id_exc:
                    identity_ok = False
                    clip_res["error"] = f"identity_validation_error: {id_exc}"

                if not identity_ok:
                    clip_res["audio_status"] = "failed"
                    clip_res["visual_status"] = "failed"
                    clip_res["visual_describe_status"] = "failed" if self.config.enable_visual_describe else "skipped"
                    clip_res["error"] = clip_res["error"] or "identity_hash_failed"
                    self._clip_states[idx]["audio_state"] = "failed"
                    self._clip_states[idx]["visual_state"] = "failed"
                    if self.config.enable_visual_describe:
                        self._clip_states[idx]["visual_describe_state"] = "failed"
                    has_partial_error = True
                    self._emit_status(vp, clip_res)
                    self._emit_counters()
                    continue

                # ----------------- CHẶNG 1: AUDIO STT & SILENCE -----------------
                source_stable = True

                if self.config.enable_audio:
                    cached = None
                    try:
                        cached = self._cache_mgr.get_cached_scan(
                            vp,
                            self.config.whisper_model,
                            self.config.whisper_language,
                            verify_full=True,
                            cancelled_fn=lambda: self._is_cancelled,
                            verified_identity=(full_hash, prov)
                        )
                    except Exception as cache_lookup_exc:
                        self.log_signal.emit(job_id, f"   ⚠️ Lỗi tra cứu cache v3: {cache_lookup_exc}")
                        cached = None

                    # Ranh giới hủy sau khi tra cứu cache
                    if self._is_cancelled:
                        self._terminalize_pending("cancelled")
                        self._emit_counters()
                        self._emit_terminal(False, "cancelled", self._compute_counters())
                        return

                    if cached:
                        cached_subs = cached.get("raw_subtitles", [])
                        cached_dur = cached.get("clip_dur", 0.0)
                        clip_res["raw_subtitles"] = cached_subs
                        clip_res["audio_segments"] = cached_subs
                        clip_res["clip_dur"] = cached_dur
                        clip_res["cache_saved"] = True
                        clip_res["persisted"] = True

                        is_cached_no_audio = bool(
                            cached.get("no_audio_track")
                            or cached.get("no_audio")
                            or cached.get("audio_analysis_status") == "no_audio_track"
                            or cached.get("producer_status") == "no_audio_track"
                        )

                        if is_cached_no_audio and not cached_subs:
                            clip_res["audio_status"] = "no_audio_track"
                            clip_res["no_audio_track"] = True
                            self._clip_states[idx]["audio_state"] = "no_audio_track"
                            self.log_signal.emit(job_id, f"   ⚡ [Audio Cache Hit - No Audio] {clip_name} nạp từ v3 cache.")
                        else:
                            clip_res["audio_status"] = "cached"
                            self._clip_states[idx]["audio_state"] = "cached"
                            if not cached_subs:
                                clip_res["no_speech"] = True
                                self._clip_states[idx]["no_speech"] = True
                            self.log_signal.emit(job_id, f"   ⚡ [Audio Cache Hit] {clip_name} nạp từ v3 cache.")
                    else:
                        # Cache Miss: Probe audio stream metadata
                        has_audio = None
                        try:
                            has_audio = self._audio_probe_fn(vp)
                        except Exception as probe_exc:
                            clip_res["audio_status"] = "failed"
                            clip_res["error"] = f"audio_probe_error: {str(probe_exc)}"
                            self._clip_states[idx]["audio_state"] = "failed"
                            has_partial_error = True
                            self.log_signal.emit(job_id, f"   ❌ Lỗi probe audio: {str(probe_exc)}")
                            has_audio = None

                        # Ranh giới hủy sau khi probe
                        if self._is_cancelled:
                            self._terminalize_pending("cancelled")
                            self._emit_counters()
                            self._emit_terminal(False, "cancelled", self._compute_counters())
                            return

                        if has_audio is False:
                            clip_res["no_audio_track"] = True

                            # 1. Lấy duration thật
                            clip_dur = 0.0
                            try:
                                clip_dur = self._duration_fn(vp)
                            except Exception as dur_exc:
                                clip_dur = 0.0
                                self.log_signal.emit(job_id, f"   ⚠️ Lỗi đo thời lượng: {dur_exc}")

                            if not _is_valid_positive_duration(clip_dur):
                                clip_res["audio_status"] = "failed"
                                clip_res["error"] = f"invalid_audio_duration: {clip_dur}"
                                clip_res["cache_saved"] = False
                                clip_res["persisted"] = False
                                self._clip_states[idx]["audio_state"] = "failed"
                                has_partial_error = True
                                self.log_signal.emit(
                                    job_id,
                                    f"   ❌ Thời lượng clip không hợp lệ ({clip_dur}). Không xuất bản negative cache."
                                )
                            elif not self._validate_identity_fn(vp, prov):
                                source_stable = False
                                clip_res["audio_status"] = "failed"
                                clip_res["error"] = "source_mutated_during_analysis"
                                clip_res["cache_saved"] = False
                                clip_res["persisted"] = False
                                self._clip_states[idx]["audio_state"] = "failed"
                                has_partial_error = True
                                self.log_signal.emit(
                                    job_id,
                                    f"   ❌ Nguồn '{clip_name}' bị thay đổi trong lúc phân tích. Hủy xuất bản cache."
                                )
                            elif self._is_cancelled:
                                self._terminalize_pending("cancelled")
                                self._emit_counters()
                                self._emit_terminal(False, "cancelled", self._compute_counters())
                                return
                            else:
                                # Xuất bản verified v3 negative cache
                                save_res = False
                                try:
                                    save_res = self._cache_mgr.save_scan_result(
                                        vp,
                                        {
                                            "clip_dur": clip_dur,
                                            "raw_subtitles": [],
                                            "no_audio_track": True,
                                            "no_audio": True,
                                            "audio_analysis_status": "no_audio_track",
                                            "producer_status": "no_audio_track",
                                        },
                                        model_size=self.config.whisper_model,
                                        language=self.config.whisper_language,
                                        verified_identity=(full_hash, prov),
                                        cancelled_fn=lambda: self._is_cancelled,
                                    )
                                except Exception as save_exc:
                                    save_res = False
                                    self.log_signal.emit(
                                        job_id, f"   ⚠️ Ngoại lệ khi lưu cache v3: {save_exc}"
                                    )

                                clip_res["audio_status"] = "no_audio_track"
                                clip_res["raw_subtitles"] = []
                                clip_res["audio_segments"] = []
                                clip_res["clip_dur"] = clip_dur
                                self._clip_states[idx]["audio_state"] = "no_audio_track"

                                if save_res:
                                    clip_res["cache_saved"] = True
                                    clip_res["persisted"] = True
                                    self.log_signal.emit(job_id, f"   🔇 Video không có luồng âm thanh: {clip_name} (đã lưu negative cache v3)")
                                else:
                                    clip_res["cache_saved"] = False
                                    clip_res["persisted"] = False
                                    clip_res["warning"] = "cache_save_failed"
                                    self.log_signal.emit(
                                        job_id,
                                        f"   ⚠️ Lưu cache không thành công cho {clip_name}."
                                    )
                        elif has_audio is True:
                            # Nạp transcriber lazy nếu chưa nạp (luôn enforce local_files_only=True)
                            if transcriber is None:
                                try:
                                    cfg = ModelConfig(
                                        model_size=self.config.whisper_model,
                                        device="cuda",
                                        compute_type="float16"
                                    )
                                    transcriber = self._transcriber_factory(cfg)
                                    transcriber.load_model(
                                        log_callback=lambda msg: self.log_signal.emit(job_id, f"   🤖 {msg}"),
                                        local_files_only=True  # Bắt buộc offline
                                    )
                                except Exception as model_exc:
                                    err_str = str(model_exc)
                                    # Phân loại unavailable nếu thiếu model file
                                    is_missing = (
                                        isinstance(model_exc, (FileNotFoundError, OSError))
                                        or "not found" in err_str.lower()
                                        or "local" in err_str.lower()
                                        or "offline" in err_str.lower()
                                    )
                                    classification = "unavailable" if is_missing else "failed"
                                    clip_res["audio_status"] = classification
                                    clip_res["error"] = f"model_load_error: {err_str}"
                                    self._clip_states[idx]["audio_state"] = classification
                                    has_partial_error = True
                                    self.log_signal.emit(
                                        job_id,
                                        f"   ⚠️ Mô hình Whisper không khả dụng ({classification}): {err_str}"
                                    )
                                    transcriber = None

                            # Ranh giới hủy sau khi nạp transcriber
                            if self._is_cancelled:
                                self._terminalize_pending("cancelled")
                                self._emit_counters()
                                self._emit_terminal(False, "cancelled", self._compute_counters())
                                return

                            if transcriber is not None:
                                temp_wav = os.path.join(
                                    job_temp_dir, f"{uuid.uuid4().hex[:8]}_{clip_name}.wav"
                                )
                                try:
                                    if self._is_cancelled:
                                        raise InterruptedError("Cancelled before audio extraction")

                                    AudioExtractor.extract_audio(vp, temp_wav)

                                    if self._is_cancelled:
                                        raise InterruptedError("Cancelled during audio extraction")

                                    norm_lang = normalize_language_code(self.config.whisper_language)
                                    lang_code = None if norm_lang == "auto" else norm_lang

                                    raw_subs = transcriber.transcribe(
                                        temp_wav,
                                        language=lang_code,
                                        is_cancelled_callback=lambda: self._is_cancelled,
                                    )

                                    if self._is_cancelled:
                                        raise InterruptedError("Cancelled during transcription")

                                    # Lấy duration và kiểm tra tính hợp lệ
                                    clip_dur = 0.0
                                    try:
                                        clip_dur = AudioExtractor.get_audio_duration(vp)
                                    except Exception as dur_exc:
                                        clip_dur = 0.0
                                        self.log_signal.emit(job_id, f"   ⚠️ Lỗi đo thời lượng: {dur_exc}")

                                    if not _is_valid_positive_duration(clip_dur):
                                        clip_res["audio_status"] = "failed"
                                        clip_res["error"] = f"invalid_audio_duration: {clip_dur}"
                                        self._clip_states[idx]["audio_state"] = "failed"
                                        has_partial_error = True
                                        self.log_signal.emit(
                                            job_id,
                                            f"   ❌ Thời lượng âm thanh không hợp lệ ({clip_dur}). Không xuất bản cache."
                                        )
                                    else:
                                        target_filter_lang = (
                                            norm_lang
                                            if norm_lang != "auto"
                                            else (getattr(transcriber, "last_detected_language", "vi") or "vi")
                                        )
                                        clean_subs, _, _ = clean_whisper_segments_with_stats(
                                            raw_subs,
                                            language=target_filter_lang,
                                            clip_duration=clip_dur
                                        )

                                        # Kiểm tra tính ổn định nguồn trước khi xuất bản cache
                                        if not self._validate_identity_fn(vp, prov):
                                            source_stable = False
                                            clip_res["audio_status"] = "failed"
                                            clip_res["error"] = "source_mutated_during_analysis"
                                            self._clip_states[idx]["audio_state"] = "failed"
                                            has_partial_error = True
                                            self.log_signal.emit(
                                                job_id,
                                                f"   ❌ Nguồn '{clip_name}' bị thay đổi trong lúc phân tích. Hủy xuất bản cache."
                                            )
                                        elif self._is_cancelled:
                                            raise InterruptedError("Cancelled before cache publication")
                                        else:
                                            # Xuất bản cache v3
                                            save_res = False
                                            try:
                                                save_res = self._cache_mgr.save_scan_result(
                                                    vp,
                                                    {
                                                        "clip_dur": clip_dur,
                                                        "raw_subtitles": clean_subs,
                                                    },
                                                    model_size=self.config.whisper_model,
                                                    language=self.config.whisper_language,
                                                    verified_identity=(full_hash, prov),
                                                    cancelled_fn=lambda: self._is_cancelled
                                                )
                                            except Exception as save_exc:
                                                save_res = False
                                                self.log_signal.emit(
                                                    job_id, f"   ⚠️ Ngoại lệ khi lưu cache v3: {save_exc}"
                                                )

                                            clip_res["audio_status"] = "new"
                                            clip_res["raw_subtitles"] = clean_subs
                                            clip_res["audio_segments"] = clean_subs
                                            clip_res["clip_dur"] = clip_dur
                                            self._clip_states[idx]["audio_state"] = "new"

                                            if not clean_subs:
                                                clip_res["no_speech"] = True
                                                self._clip_states[idx]["no_speech"] = True

                                            if save_res:
                                                clip_res["cache_saved"] = True
                                                clip_res["persisted"] = True
                                            else:
                                                clip_res["cache_saved"] = False
                                                clip_res["persisted"] = False
                                                clip_res["warning"] = "cache_save_failed"
                                                self.log_signal.emit(
                                                    job_id,
                                                    f"   ⚠️ Lưu cache không thành công cho {clip_name}. "
                                                    "Kết quả giữ trong phiên hiện tại."
                                                )

                                            self.log_signal.emit(
                                                job_id,
                                                f"   ✔ Hoàn tất bóc tách thoại cho {clip_name} ({len(clean_subs)} câu)."
                                            )
                                except InterruptedError:
                                    self._terminalize_pending("cancelled")
                                    self._emit_counters()
                                    self._emit_terminal(False, "cancelled", self._compute_counters())
                                    return
                                except Exception as trans_exc:
                                    clip_res["audio_status"] = "failed"
                                    clip_res["error"] = f"transcription_error: {str(trans_exc)}"
                                    self._clip_states[idx]["audio_state"] = "failed"
                                    has_partial_error = True
                                    self.log_signal.emit(job_id, f"   ❌ Lỗi dịch giọng nói: {str(trans_exc)}")
                                finally:
                                    if os.path.exists(temp_wav):
                                        try:
                                            os.remove(temp_wav)
                                        except Exception:
                                            pass
                else:
                    # Audio bị disabled trong config -> skipped
                    clip_res["audio_status"] = "skipped"
                    self._clip_states[idx]["audio_state"] = "skipped"

                # ----------------- CHẶNG 2: VISUAL SAMPLING -----------------
                # Ranh giới hủy trước visual sampling
                if self._is_cancelled:
                    self._terminalize_pending("cancelled")
                    self._emit_counters()
                    self._emit_terminal(False, "cancelled", self._compute_counters())
                    return

                if self.config.enable_visual_sampling and source_stable:
                    # Kiểm tra lại identity trước sampling
                    if not self._validate_identity_fn(vp, prov):
                        clip_res["visual_status"] = "failed"
                        clip_res["error"] = (clip_res["error"] or "") + " source_mutated_before_visual"
                        self._clip_states[idx]["visual_state"] = "failed"
                        has_partial_error = True
                        self.log_signal.emit(job_id, f"   ❌ Nguồn '{clip_name}' bị thay đổi trước khi lấy mẫu hình ảnh.")
                    else:
                        try:
                            self.log_signal.emit(job_id, f"   🖼 Lấy mẫu khung hình thị giác: {clip_name}")
                            v_res = self._visual_index.scan(
                                vp,
                                interval_sec=self.config.visual_interval_sec,
                                max_samples=self.config.visual_max_samples,
                                cancelled=lambda: self._is_cancelled
                            )

                            # Ranh giới hủy ngay sau khi visual sampling kết thúc
                            if self._is_cancelled:
                                self._terminalize_pending("cancelled")
                                self._emit_counters()
                                self._emit_terminal(False, "cancelled", self._compute_counters())
                                return

                            if v_res.get("cache_hit", False):
                                clip_res["visual_status"] = "cached"
                                self._clip_states[idx]["visual_state"] = "cached"
                                self.log_signal.emit(job_id, f"   ⚡ [Visual Cache Hit] {clip_name} nạp từ visual cache.")
                            else:
                                clip_res["visual_status"] = "new"
                                self._clip_states[idx]["visual_state"] = "new"
                                self.log_signal.emit(job_id, f"   ✔ Hoàn tất trích xuất khung hình mẫu cho {clip_name}.")
                        except InterruptedError:
                            self._terminalize_pending("cancelled")
                            self._emit_counters()
                            self._emit_terminal(False, "cancelled", self._compute_counters())
                            return
                        except Exception as v_exc:
                            clip_res["visual_status"] = "failed"
                            clip_res["error"] = (clip_res["error"] or "") + f" visual_sampling_error: {str(v_exc)}"
                            self._clip_states[idx]["visual_state"] = "failed"
                            has_partial_error = True
                            self.log_signal.emit(job_id, f"   ❌ Lỗi lấy mẫu khung hình: {str(v_exc)}")
                else:
                    if not source_stable:
                        clip_res["visual_status"] = "failed"
                        self._clip_states[idx]["visual_state"] = "failed"
                        has_partial_error = True
                    else:
                        clip_res["visual_status"] = "skipped"
                        self._clip_states[idx]["visual_state"] = "skipped"

                # ----------------- CHẶNG 3: VISUAL DESCRIBE (STAGE 3 OPT-IN) -----------------
                # Ranh giới hủy trước visual describe
                if self._is_cancelled:
                    self._terminalize_pending("cancelled")
                    self._emit_counters()
                    self._emit_terminal(False, "cancelled", self._compute_counters())
                    return

                if self.config.enable_visual_describe and source_stable:
                    # Kiểm tra lại identity trước khi describe
                    if not self._validate_identity_fn(vp, prov):
                        clip_res["visual_describe_status"] = "failed"
                        clip_res["error"] = (clip_res["error"] or "") + " source_mutated_before_visual_describe"
                        self._clip_states[idx]["visual_describe_state"] = "failed"
                        has_partial_error = True
                        self.log_signal.emit(job_id, f"   ❌ Nguồn '{clip_name}' bị thay đổi trước khi mô tả thị giác.")
                    else:
                        vision_model = (self.config.vision_model or "").strip()
                        if not vision_model:
                            clip_res["visual_describe_status"] = "unavailable"
                            clip_res["error"] = (clip_res["error"] or "") + " missing_vision_model"
                            self._clip_states[idx]["visual_describe_state"] = "unavailable"
                            has_partial_error = True
                            self.log_signal.emit(job_id, "   ⚠️ Thiếu cấu hình tên mô hình thị giác (vision_model rỗng).")
                        else:
                            # 1. Kiểm tra mô hình ngoại tuyến qua local_model_info_fn
                            model_info = None
                            try:
                                model_info = self._local_model_info_fn(
                                    vision_model,
                                    endpoint=self.config.vision_endpoint
                                )
                            except Exception as minfo_exc:
                                err_s = str(minfo_exc)
                                err_s_lower = err_s.lower()
                                is_unavail = (
                                    "connection" in err_s_lower
                                    or "not found" in err_s_lower
                                    or "refused" in err_s_lower
                                    or "unavailable" in err_s_lower
                                    or "chưa cài" in err_s_lower
                                    or "chưa có" in err_s_lower
                                    or "missing" in err_s_lower
                                    or "remote" in err_s_lower
                                    or "cloud" in err_s_lower
                                    or "offline" in err_s_lower
                                    or "không kết nối" in err_s_lower
                                    or "hãy chọn model" in err_s_lower
                                    or isinstance(minfo_exc, (ConnectionError, FileNotFoundError, OSError))
                                )
                                classification = "unavailable" if is_unavail else "failed"
                                clip_res["visual_describe_status"] = classification
                                clip_res["error"] = (clip_res["error"] or "") + f" vision_model_error: {err_s}"
                                self._clip_states[idx]["visual_describe_state"] = classification
                                has_partial_error = True
                                self.log_signal.emit(
                                    job_id,
                                    f"   ⚠️ Mô hình thị giác '{vision_model}' không khả dụng ({classification}): {err_s}"
                                )
                                model_info = None

                            if self._is_cancelled:
                                self._terminalize_pending("cancelled")
                                self._emit_counters()
                                self._emit_terminal(False, "cancelled", self._compute_counters())
                                return

                            if model_info is not None:
                                run_evidence = {}
                                try:
                                    self.log_signal.emit(
                                        job_id,
                                        f"   👁 Mô tả nội dung hình ảnh bằng {vision_model}: {clip_name}"
                                    )
                                    try:
                                        descs = self._describer_fn(
                                            vp,
                                            model=vision_model,
                                            endpoint=self.config.vision_endpoint,
                                            index=self._visual_index,
                                            window_sec=self.config.window_sec,
                                            frames_per_window=self.config.frames_per_window,
                                            max_windows=self.config.max_windows,
                                            max_refinements=self.config.max_refinements,
                                            cancelled=lambda: self._is_cancelled,
                                            run_evidence=run_evidence,
                                        )
                                    except TypeError as te:
                                        if "run_evidence" in str(te):
                                            descs = self._describer_fn(
                                                vp,
                                                model=vision_model,
                                                endpoint=self.config.vision_endpoint,
                                                index=self._visual_index,
                                                window_sec=self.config.window_sec,
                                                frames_per_window=self.config.frames_per_window,
                                                max_windows=self.config.max_windows,
                                                max_refinements=self.config.max_refinements,
                                                cancelled=lambda: self._is_cancelled,
                                            )
                                        else:
                                            raise

                                    if self._is_cancelled:
                                        raise InterruptedError("Cancelled during visual describe")

                                    post_data = self._visual_index.load(vp) or {}
                                    if isinstance(post_data, dict):
                                        post_analysis = post_data.get("analysis", {})
                                        descs_post = post_data.get("descriptions", [])
                                    else:
                                        post_analysis = getattr(post_data, "analysis", {}) or {}
                                        descs_post = getattr(post_data, "descriptions", [])

                                    if isinstance(post_analysis, dict):
                                        quality_status = post_analysis.get("status") or post_analysis.get("analysis_status") or "unknown"
                                        raw_coverage = post_analysis.get("coverage", {})
                                        post_wins = post_analysis.get("windows", [])
                                        post_errors = post_analysis.get("errors", [])
                                    else:
                                        quality_status = getattr(post_analysis, "status", getattr(post_analysis, "analysis_status", "unknown"))
                                        raw_coverage = getattr(post_analysis, "coverage", {})
                                        post_wins = getattr(post_analysis, "windows", [])
                                        post_errors = getattr(post_analysis, "errors", [])

                                    if not isinstance(raw_coverage, dict):
                                        raw_coverage = {}
                                    if not isinstance(post_wins, list):
                                        post_wins = []

                                    # Gom nhóm các window states duy nhất theo window_id: refinement retry cùng ID chỉ tính 1 window
                                    unique_window_states = {}
                                    for st in post_wins:
                                        if isinstance(st, dict) and "window_id" in st:
                                            unique_window_states[st["window_id"]] = st
                                        elif hasattr(st, "window_id"):
                                            unique_window_states[getattr(st, "window_id")] = st

                                    # Fallback nếu mock fixture cũ chỉ cung cấp descriptions thay vì windows
                                    if not unique_window_states and (descs or descs_post):
                                        for d in (descs or descs_post):
                                            if isinstance(d, dict) and "window_id" in d:
                                                unique_window_states[d["window_id"]] = {"window_id": d["window_id"], "status": "described"}

                                    total_wins = raw_coverage.get("total_windows", len(unique_window_states))
                                    selected_wins = raw_coverage.get("selected_windows", len(unique_window_states))
                                    sampled_valid_wins = raw_coverage.get("valid_windows", raw_coverage.get("sampled_windows", selected_wins))

                                    described_wins = sum(1 for st in unique_window_states.values() if (st.get("status") if isinstance(st, dict) else getattr(st, "status", None)) == "described")
                                    needs_review_wins = sum(1 for st in unique_window_states.values() if (st.get("status") if isinstance(st, dict) else getattr(st, "status", None)) == "needs_review")
                                    error_wins = sum(1 for st in unique_window_states.values() if (st.get("status") if isinstance(st, dict) else getattr(st, "status", None)) == "error")
                                    insufficient_wins = sum(1 for st in unique_window_states.values() if (st.get("status") if isinstance(st, dict) else getattr(st, "status", None)) == "insufficient_evidence")
                                    pending_refinement_wins = sum(1 for st in unique_window_states.values() if (st.get("refinement_pending") if isinstance(st, dict) else getattr(st, "refinement_pending", False)))

                                    # Analyzed windows là các window có validated observations (described hoặc needs_review)
                                    analyzed_wins = described_wins + needs_review_wins

                                    # True full coverage: toàn bộ clip được phân tích đầy đủ không giới hạn ngân sách và không lỗi
                                    is_full_coverage = (
                                        total_wins > 0
                                        and selected_wins == total_wins
                                        and analyzed_wins == total_wins
                                        and error_wins == 0
                                        and pending_refinement_wins == 0
                                        and quality_status == "complete"
                                    )

                                    coverage_info = {
                                        "total_windows": total_wins,
                                        "selected_windows": selected_wins,
                                        "sampled_valid_windows": sampled_valid_wins,
                                        "analyzed_windows": analyzed_wins,
                                        "described_windows": described_wins,
                                        "needs_review_windows": needs_review_wins,
                                        "error_windows": error_wins,
                                        "insufficient_windows": insufficient_wins,
                                        "pending_refinement_windows": pending_refinement_wins,
                                        "complete": is_full_coverage,
                                    }

                                    clip_res["descriptions"] = descs or descs_post
                                    clip_res["analysis_quality"] = quality_status
                                    clip_res["analysis_coverage"] = coverage_info
                                    clip_res["coverage"] = coverage_info

                                    has_evidence = bool(run_evidence and "cache_reused" in run_evidence)
                                    actual_cached = (
                                        has_evidence
                                        and run_evidence.get("cache_reused") is True
                                        and run_evidence.get("inference_requests", -1) == 0
                                    )

                                    if total_wins == 0 or selected_wins == 0:
                                        vd_state = "partial"
                                        has_partial_error = True
                                    elif quality_status == "complete" and is_full_coverage:
                                        if actual_cached:
                                            vd_state = "cached"
                                        else:
                                            vd_state = "new"
                                    elif quality_status in ("complete", "partial", "needs_review"):
                                        vd_state = "partial"
                                        has_partial_error = True
                                    else:
                                        vd_state = "failed"
                                        has_partial_error = True

                                    clip_res["visual_describe_status"] = vd_state
                                    self._clip_states[idx]["visual_describe_state"] = vd_state
                                    self.log_signal.emit(
                                        job_id,
                                        f"   ✔ Hoàn tất mô tả hình ảnh cho {clip_name} ({vd_state}, "
                                        f"chất lượng: {quality_status}, {analyzed_wins}/{selected_wins} cửa sổ, {len(clip_res['descriptions'])} phân đoạn)."
                                    )

                                except InterruptedError:
                                    self._terminalize_pending("cancelled")
                                    self._emit_counters()
                                    self._emit_terminal(False, "cancelled", self._compute_counters())
                                    return
                                except Exception as desc_exc:
                                    clip_res["visual_describe_status"] = "failed"
                                    clip_res["error"] = (clip_res["error"] or "") + f" visual_describe_error: {str(desc_exc)}"
                                    self._clip_states[idx]["visual_describe_state"] = "failed"
                                    has_partial_error = True
                                    self.log_signal.emit(job_id, f"   ❌ Lỗi mô tả hình ảnh: {str(desc_exc)}")
                else:
                    if not source_stable:
                        clip_res["visual_describe_status"] = "failed" if self.config.enable_visual_describe else "skipped"
                        self._clip_states[idx]["visual_describe_state"] = clip_res["visual_describe_status"]
                        if self.config.enable_visual_describe:
                            has_partial_error = True
                    else:
                        clip_res["visual_describe_status"] = "skipped"
                        self._clip_states[idx]["visual_describe_state"] = "skipped"

                # Phát tín hiệu cập nhật trạng thái clip và bộ đếm
                self._emit_status(vp, clip_res)
                self._emit_counters()

            # Ranh giới kiểm tra hủy cuối cùng trước khi công bố thành công
            if self._is_cancelled:
                self._terminalize_pending("cancelled")
                self._emit_counters()
                self._emit_terminal(False, "cancelled", self._compute_counters())
                return

            # Hoàn thành toàn bộ danh sách clip
            self.progress_signal.emit(job_id, total_clips, total_clips)
            final_success = not has_partial_error
            final_msg = "completed" if final_success else "partial_failure"
            self.log_signal.emit(job_id, f"\n🎉 Phân tích nguồn hoàn tất: {final_msg}")
            self._emit_terminal(final_success, final_msg, self._compute_counters())

        except Exception as global_exc:
            # Bắt mọi unhandled error bảo đảm không bao giờ thoát QThread với 0 terminal
            try:
                self.log_signal.emit(job_id, f"❌ Lỗi ngoài dự kiến trong phiên phân tích: {global_exc}")
            except Exception:
                pass
            self._terminalize_pending("failed")
            try:
                self._emit_counters()
            except Exception:
                pass
            self._emit_terminal(False, f"global_failure: {str(global_exc)}", self._compute_counters())
        finally:
            # 1. Unload transcriber model an toàn
            if transcriber is not None and hasattr(transcriber, "unload_model"):
                try:
                    transcriber.unload_model()
                except Exception:
                    pass

            # 2. Xóa sạch thư mục temp độc quyền của job này CHỈ NẾU đã được cấp phát
            if job_temp_dir is not None and os.path.exists(job_temp_dir):
                try:
                    shutil.rmtree(job_temp_dir, ignore_errors=True)
                except Exception:
                    pass
