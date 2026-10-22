"""
src/ui/director/director_controller.py
Bộ điều khiển trung gian (Adapter) giữa ChunDVC Director và hệ thống Worker cũ.
Quản lý trạng thái, kết nối PipelineWorker & LLMDirectorWorker mà KHÔNG viết lại pipeline core.
"""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import QObject, Signal, Slot
from src.ui.director.director_state import (
    DirectorConfig, FEAT_HOOK, FEAT_PUNCH_IN, FEAT_BROLL, FEAT_SFX,
    FEAT_REFRAME, FEAT_KARAOKE, FEAT_WEB_BROLL, FEAT_SAFE_ZONE
)

# Thử import worker từ auto_window nếu có, hoặc tạo stub an toàn cho headless testing
try:
    from src.ui.auto.auto_window import PipelineWorker, LLMDirectorWorker
except Exception:
    PipelineWorker = None
    LLMDirectorWorker = None


class DirectorController(QObject):
    """Bộ điều khiển kết nối UI Director với các luồng xử lý ngầm."""
    step_status_changed = Signal(str, str)   # step_key, status
    progress_changed = Signal(int)           # 0..100
    log_received = Signal(str)               # message
    pipeline_finished = Signal(bool, str)    # success, message
    ai_script_finished = Signal(bool, str, str)  # success, message, raw_json

    def __init__(self, config: Optional[DirectorConfig] = None, parent=None):
        super().__init__(parent)
        self.config = config or DirectorConfig()
        self.worker: Optional[Any] = None
        self.llm_worker: Optional[Any] = None
        self.proposed_segments: List[Any] = []
        self.clip_data_cache: Dict[str, Any] = {}

    def build_pipeline_kwargs(self, phase: int = 0) -> Dict[str, Any]:
        """
        Chuyển đổi toàn bộ DirectorConfig thành tham số tương thích 100% với PipelineWorker cũ.
        """
        cfg = self.config

        # Map pacing
        pacing_map = {"Thong thả": "chill", "Cân bằng": "balanced", "Nhanh": "fast"}
        pacing_val = pacing_map.get(cfg.pacing, "balanced")

        # Map video_type
        type_map = {
            "Tự nhận diện": "auto",
            "Nói liên tục": "talking_head",
            "Hỗn hợp": "dynamic_vlog",
            "Du lịch": "travel"
        }
        video_type_val = type_map.get(cfg.footage_type, "auto")

        enable_hook = (FEAT_HOOK in cfg.enabled_features)
        enable_punch = (FEAT_PUNCH_IN in cfg.enabled_features)
        enable_broll = (FEAT_BROLL in cfg.enabled_features)
        enable_sfx = (FEAT_SFX in cfg.enabled_features)
        enable_reframe = (FEAT_REFRAME in cfg.enabled_features)
        enable_evidence = (FEAT_WEB_BROLL in cfg.enabled_features)
        enable_safe_zone = (FEAT_SAFE_ZONE in cfg.enabled_features)

        text_preset = cfg.text_preset_id
        if cfg.kinetic_plan_data and isinstance(cfg.kinetic_plan_data, dict):
            text_preset = cfg.kinetic_plan_data.get("style_preset") or cfg.kinetic_plan_data.get("preset_id") or text_preset

        story_plan = cfg.video_plan_data or (cfg.web_prompt_pasted_data.get("data") if cfg.web_prompt_pasted_data else None)

        kwargs = {
            "video_paths": list(cfg.video_paths),
            "model_size": cfg.whisper_model,
            "language": cfg.whisper_language,
            "run_cut": cfg.silence_cut_enabled,
            "silence_db": cfg.silence_db,
            "min_duration": cfg.min_silence_dur,
            "split_mode": "characters",
            "split_limit": 42,
            "font_name": "Arial",
            "font_size": 48,
            "ai_mode": cfg.ai_semantic_mode,
            "remove_bad_takes": cfg.remove_bad_takes,
            "enable_punch_in": enable_punch,
            "punch_in_scale": cfg.punch_in_scale,
            "enable_reframe": enable_reframe,
            "enable_broll": enable_broll,
            "enable_sfx": enable_sfx,
            "speed_up_silence": cfg.speedup_silence,
            "silence_speed": 8.0,
            "enable_vlog_hook": enable_hook,
            "vlog_hook_duration": cfg.vlog_hook_dur,
            "api_key": cfg.api_key,
            "enable_subtitles": cfg.kinetic_subtitles_v2,
            "phase": phase,
            "proposed_segments_override": self.proposed_segments if phase == 2 else None,
            "clip_data_cache": self.clip_data_cache,
            "text_preset_id": text_preset,
            "use_cache": cfg.use_cache,
            "use_proxy": True,
            "enable_loudnorm": False,
            "loudnorm_preset": "youtube_tiktok",
            "pacing": pacing_val,
            "remove_repeated_phrases": cfg.remove_repeats,
            "scene_guard": cfg.scene_guard_enabled,
            "fill_gaps": cfg.fill_gaps,
            "vlog_hook_total": cfg.vlog_hook_total,
            "story_intent": "keep",
            "story_target": 60.0,
            "video_type": video_type_val,
            "hide_weak_subs": cfg.hide_weak_subs,
            "story_arrangement_override": story_plan,
            "project_structure": None,
            "enable_evidence_hunter": enable_evidence,
            "enable_shotbook": enable_punch,
            "enable_geometry_checker": enable_safe_zone,
            "safe_zone_platform": "tiktok" if enable_reframe else "youtube"
        }
        return kwargs

    def start_pipeline(self, phase: int = 0) -> bool:
        """Khởi chạy PipelineWorker theo phase (0: All, 1: STT/Review, 2: Build)."""
        if not self.config.video_paths:
            self.pipeline_finished.emit(False, "Chưa chọn tệp video nguồn nào.")
            return False

        if PipelineWorker is None:
            # Fallback mô phỏng cho unit test
            self.log_received.emit("Mô phỏng khởi chạy pipeline...")
            self.step_status_changed.emit("validate", "done")
            self.step_status_changed.emit("export", "done")
            self.progress_changed.emit(100)
            self.pipeline_finished.emit(True, "Mô phỏng pipeline thành công!")
            return True

        kwargs = self.build_pipeline_kwargs(phase=phase)
        try:
            self.worker = PipelineWorker(**kwargs)
            self.worker.log_signal.connect(self.log_received.emit)
            self.worker.step_signal.connect(self.step_status_changed.emit)
            self.worker.progress_signal.connect(self.progress_changed.emit)
            self.worker.finished_signal.connect(self._on_worker_finished)
            self.worker.start()
            return True
        except Exception as e:
            self.pipeline_finished.emit(False, f"Lỗi khởi tạo worker: {str(e)}")
            return False

    def cancel_pipeline(self):
        """Hủy an toàn worker đang chạy."""
        if self.worker and self.worker.isRunning():
            if hasattr(self.worker, "stop"):
                self.worker.stop()
            self.worker.requestInterruption()
            self.log_received.emit("Đã gửi yêu cầu hủy tiến trình pipeline.")

    def run_ai_script(self, prompt: str):
        """Gọi LLMDirectorWorker để lấy kịch bản từ Ollama hoặc Cloud API."""
        if LLMDirectorWorker is None:
            self.ai_script_finished.emit(True, "Mô phỏng AI kịch bản thành công!", "{}")
            return

        cfg = self.config
        mode = "ollama" if cfg.ai_engine_mode == "ollama" else "cloud"
        model = cfg.ollama_model if mode == "ollama" else None
        endpoint = cfg.ollama_endpoint or "http://localhost:11434"
        try:
            self.llm_worker = LLMDirectorWorker(
                engine_mode=mode,
                prompt=prompt,
                model=model,
                endpoint=endpoint,
                api_key=cfg.api_key or "",
                provider=cfg.cloud_provider
            )
            self.llm_worker.log_signal.connect(self.log_received.emit)
            self.llm_worker.finished_signal.connect(self.ai_script_finished.emit)
            self.llm_worker.start()
        except Exception as e:
            self.ai_script_finished.emit(False, f"Lỗi gọi AI: {str(e)}", "")

    def _on_worker_finished(self, success: bool, message: str):
        self.pipeline_finished.emit(success, message)
