import os
import sys
import json
import tempfile
import uuid
import traceback
from typing import List, Dict, Any, Optional, Sequence, Tuple

from src.core.validator import DryRunValidator
from src.core.transcriber import ModelConfig, ResolveTranscriber
from src.core import resolve_api
from src.core.resolve_api import (
    SubtitleConfig, ResolveAutomation, split_subtitles,
    map_subtitles_to_timeline, map_time_to_timeline,
    map_time_with_speedup_segments, map_subtitles_with_speedup_segments,
    is_vertical_video
)
from src.core.autocut import AudioCutConfig, SilenceDetector, EDLGenerator, get_media_metadata, seconds_to_timecode
from src.core.ai_director import AIDirector, AIDirectorConfig, ProposedSegment
from src.core.vision_reframer import VisionReframer, ReframeConfig
from src.core.broll_sfx import BRollAnalyzer, SFXEngine, GlobalAssetPool
from src.core.vlog_hook import VlogHookGenerator, HookSegment
from src.core.audio import AudioExtractor
from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.audit_reporter import ExecutionAuditReporter
from src.core.text_preset import TextStylePreset, PresetManager, TextPreviewRenderer
from src.core.recipe_manager import Recipe, RecipeManager
from src.core.cache_manager import ScanCacheManager, compute_file_checksum
from src.core.proxy_manager import ProxyManager, ParallelScanPipeline, suggest_whisper_model
from src.core.review_state import ReviewState, StoryReviewState
from src.core.story_arranger import ROLE_LABELS, ROLE_COLORS, Arrangement, ArrangedItem, Block
from src.core.transcript_quality import drop_low_confidence_subs
from src.core.scene_activity import compute_visual_activity
from src.core.edit_policy import POLICIES, classify_video, apply_edit_policy
from src.core import story_planner
from src.core.audio_normalizer import AudioNormalizer, LOUDNESS_PRESETS, LoudnessTarget
from src.core.marketing_engine import MarketingViralPackGenerator
from src.core.folder_scanner import ProjectFolderStructure, FolderGroup, scan_project_folder, collect_video_paths
from src.core.cloud_downloader import GoogleDriveDownloader, parse_google_drive_url
from src.core.story_copilot import StoryCopilot, CopilotDirectorPlan
from src.ui.widgets.drive_dialog import GoogleDriveImportDialog
from src.ui.widgets.copilot_dialog import StoryCopilotDialog
from src.ui.bubble.floating_bubble import FloatingBubbleWidget
from src.ui.widgets.section_card import SectionCard

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QPlainTextEdit, QGroupBox, QFormLayout, QSlider, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
    QDialog, QScrollArea, QInputDialog, QFrame, QTabWidget, QSplitter, QListWidget, QStackedWidget
)
from PySide6.QtCore import QThread, Signal as pyqtSignal, Slot as pyqtSlot, Qt
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon, QShortcut, QKeySequence

from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS, MODULE_DESCRIPTIONS, get_application_stylesheet
from src.ui.tabs import TabCopilot, TabAutoCut, TabAssets, TabSFX, TabExport
from src.ui.bubble.floating_bubble import create_vector_icon

class PreviewDialog(QDialog):
    """Hộp thoại hiển thị xem trước nhanh (Quick Preview) kiểu chữ phụ đề Text+."""
    def __init__(self, preset: TextStylePreset, aspect_ratio: str = "16:9", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Quick Preview: {preset.name} ({aspect_ratio})")
        self.resize(680, 440)
        self.setStyleSheet("background-color: #16161A; color: #E0E0E6;")

        layout = QVBoxLayout(self)

        info_lbl = QLabel(f"<b>Kiểu chữ:</b> {preset.name} | <b>Animation:</b> {preset.animation.upper()} ({preset.timing_curve}) | <b>Tỷ lệ:</b> {aspect_ratio}")
        info_lbl.setStyleSheet("color: #90CAF9; font-size: 13px;")
        layout.addWidget(info_lbl)

        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setStyleSheet("background-color: #0E0E10; border: 1px solid #2A2A35; border-radius: 6px;")
        layout.addWidget(self.img_lbl, stretch=1)

        # Render preview image
        temp_img = os.path.join(tempfile.gettempdir(), f"rf_preview_{preset.id}_{aspect_ratio}.png")
        try:
            TextPreviewRenderer.render_preview_to_file(
                preset=preset,
                output_image_path=temp_img,
                sample_words=["ChunDVC", "AI", "Text+", "Subtitle"],
                active_index=2,
                aspect_ratio=aspect_ratio
            )
            pix = QPixmap(temp_img)
            self.img_lbl.setPixmap(pix.scaled(640, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as e:
            self.img_lbl.setText(f"Không thể tạo ảnh xem trước: {e}")

        btn_close = QPushButton("Đóng (Close)")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


class LLMDirectorWorker(QThread):
    """
    Worker Thread để chạy gọi Local Ollama hoặc Cloud API ngầm
    tránh làm đơ (freeze) giao diện người dùng.
    """
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str)  # success, message, raw_json

    def __init__(
        self,
        engine_mode: str,  # "ollama" or "cloud"
        prompt: str,
        model: str = "qwen2.5:7b-instruct",
        endpoint: str = "http://localhost:11434",
        api_key: str = "",
        provider: str = "deepseek",
        parent=None
    ):
        super().__init__(parent)
        self.engine_mode = engine_mode
        self.prompt = prompt
        self.model = model
        self.endpoint = endpoint
        self.api_key = api_key
        self.provider = provider

    def run(self):
        try:
            if self.engine_mode == "ollama":
                self.log_signal.emit(f"🤖 [Local AI] Đang gửi dữ liệu sang Ollama ({self.model}) tại {self.endpoint}...")
                plan, raw_json = StoryCopilot.run_ollama_inference(
                    prompt=self.prompt,
                    model=self.model,
                    endpoint=self.endpoint
                )
            else:
                self.log_signal.emit(f"⚡ [Cloud AI] Đang gửi dữ liệu sang {self.provider.upper()} API ({self.model})...")
                plan, raw_json = StoryCopilot.run_cloud_api_inference(
                    prompt=self.prompt,
                    api_key=self.api_key,
                    provider=self.provider,
                    model=self.model
                )
            self.finished_signal.emit(True, "Thành công!", raw_json)
        except Exception as e:
            self.finished_signal.emit(False, str(e), "")


class PipelineWorker(QThread):
    """
    Worker Thread để chạy tiến trình xử lý ngầm (Audio, STT, Subtitle, Smart Cut, AI Director, Vision, SFX, Speed-Ramp & Vlog Hook)
    tránh làm đơ (freeze) giao diện người dùng GUI.
    Hỗ trợ cơ chế ngắt an toàn (Interruptible Threading), Scan Cache theo checksum và Proxy 480p tối ưu.
    """
    log_signal = pyqtSignal(str)
    step_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(
        self,
        video_paths,
        model_size,
        language,
        run_cut,
        silence_db,
        min_duration,
        split_mode="characters",
        split_limit=42,
        font_name="Arial",
        font_size=48,
        ai_mode="clean_talk",
        remove_bad_takes=True,
        enable_punch_in=True,
        punch_in_scale=1.15,
        enable_reframe=False,
        enable_broll=True,
        enable_sfx=True,
        speed_up_silence=False,
        silence_speed=8.0,
        enable_vlog_hook=False,
        vlog_hook_duration=2.0,
        api_key=None,
        enable_subtitles=True,
        phase=0,
        proposed_segments_override=None,
        clip_data_cache=None,
        text_preset_id="karaoke_pop",
        use_cache=True,
        use_proxy=True,
        enable_loudnorm=False,
        loudnorm_preset="youtube_tiktok",
        pacing="balanced",
        remove_repeated_phrases=True,
        scene_guard=True,
        fill_gaps=False,
        vlog_hook_total=20.0,
        story_intent="keep",
        story_target=60.0,
        video_type="auto",
        hide_weak_subs=True,
        story_arrangement_override=None,
        project_structure=None
    ):
        super().__init__()
        self.video_paths = video_paths
        self.model_size = model_size
        self.language = language
        self.run_cut = run_cut
        self.silence_db = silence_db
        self.min_duration = min_duration
        self.split_mode = split_mode
        self.split_limit = split_limit
        self.font_name = font_name
        self.font_size = font_size
        self.ai_mode = ai_mode
        self.remove_bad_takes = remove_bad_takes
        self.enable_punch_in = enable_punch_in
        self.punch_in_scale = punch_in_scale
        self.enable_reframe = enable_reframe
        self.enable_broll = enable_broll
        self.enable_sfx = enable_sfx
        self.speed_up_silence = speed_up_silence
        self.silence_speed = silence_speed
        self.enable_vlog_hook = enable_vlog_hook
        self.vlog_hook_duration = vlog_hook_duration
        self.api_key = api_key
        self.enable_subtitles = enable_subtitles
        self.phase = phase
        self.proposed_segments_override = proposed_segments_override
        self.clip_data_cache = clip_data_cache
        self.text_preset_id = text_preset_id
        self.use_cache = use_cache
        self.use_proxy = use_proxy
        self.enable_loudnorm = enable_loudnorm
        self.loudnorm_preset = loudnorm_preset
        self.pacing = story_planner.get_pacing(pacing)
        self.remove_repeated_phrases = remove_repeated_phrases
        self.scene_guard = scene_guard
        self.fill_gaps = fill_gaps
        self.vlog_hook_total = vlog_hook_total
        self.story_intent = story_intent
        self.video_type = video_type
        self.hide_weak_subs = hide_weak_subs
        self.story_target = story_target
        self.story_arrangement_override = story_arrangement_override
        self.project_structure = project_structure
        
        self.proposed_segments = []
        self.clip_data_out_cache = []
        self.validation_warnings = []
        self.story_blocks_out = []
        self.story_arrangement_out = None
        
        # Cờ kiểm soát ngắt luồng an toàn
        self.is_interrupted = False

    def stop(self):
        """Yêu cầu dừng tiến trình ngầm một cách an toàn."""
        self.is_interrupted = True
        self.log_signal.emit("🛑 Đang gửi tín hiệu dừng khẩn cấp... Thu hồi tài nguyên và dọn dẹp bộ nhớ.")

    def run(self):
        transcriber = None
        temp_files_to_clean = []
        try:
            self.log_signal.emit("🚀 Đang khởi động ChunDVC-Assistant v4.1 (Text Presets, Smart Cache & Pipeline Suite)...")
            self.progress_signal.emit(5)

            if self.is_interrupted:
                self._handle_interrupted()
                return

            # --- GIAI ĐOẠN DRY-RUN VALIDATION ---
            if self.phase in [0, 1]:
                self.step_signal.emit("validate", "running")
                self.log_signal.emit("🔍 [Dry-Run] Đang kiểm tra khả năng tương thích định dạng file nguồn...")
                val_res = DryRunValidator.validate_media_files(self.video_paths)
                
                if val_res.errors:
                    self.step_signal.emit("validate", "error")
                    self.log_signal.emit("❌ Lỗi tương thích nguồn (Quá trình dừng lại):")
                    for err in val_res.errors:
                        self.log_signal.emit(f"   - {err}")
                    self.finished_signal.emit(False, "Dry-run failed: " + "; ".join(val_res.errors))
                    return

                self.validation_warnings = val_res.warnings
                if val_res.warnings:
                    self.log_signal.emit("⚠️ Cảnh báo tương thích nguồn:")
                    for warn in val_res.warnings:
                        self.log_signal.emit(f"   - {warn}")
                self.step_signal.emit("validate", "done")

            cache_mgr = ScanCacheManager()

            if self.phase == 2:
                clip_data_list = list(self.clip_data_cache) if self.clip_data_cache else []
                if not clip_data_list and self.use_cache:
                    for vp in self.video_paths:
                        cached = cache_mgr.get_cached_scan(vp, self.model_size, self.language)
                        if cached:
                            raw_subs = cached.get("raw_subtitles", [])
                            dur = cached.get("clip_dur", 0.0)
                            clip_subs = split_subtitles(raw_subs, limit=self.split_limit, mode=self.split_mode)
                            clip_data_list.append({
                                "video_path": vp,
                                "clip_dur": dur,
                                "raw_subtitles": raw_subs,
                                "clip_subs": clip_subs,
                                "silence_keep_intervals": cached.get("silence_keep_intervals", [(0.0, dur)]),
                                "speedup_segments": cached.get("speedup_segments", []),
                                "cut_segments": cached.get("cut_segments", []),
                                "visual_activity": cached.get("visual_activity", None),
                                "audio_energy_db": cached.get("audio_energy_db", None)
                            })
                self.log_signal.emit(f"🎬 Khôi phục dữ liệu cache từ Phase 1 cho {len(clip_data_list)} clips...")
            else:
                clip_data_list = []

            # Khởi tạo mô hình Whisper AI
            needs_transcription = (
                self.enable_subtitles or
                (self.run_cut and self.ai_mode != "silence_only") or
                self.enable_broll or
                self.enable_vlog_hook
            )
            needs_audio_extraction = (
                needs_transcription or
                self.run_cut or
                self.enable_vlog_hook
            )

            if self.phase in [0, 1] and needs_transcription:
                self.step_signal.emit("load_model", "running")
                self.log_signal.emit(f"🤖 Tải mô hình Whisper AI '{self.model_size}'...")
                model_config = ModelConfig(
                    model_size=self.model_size,
                    device="cuda",
                    compute_type="float16"
                )
                transcriber = ResolveTranscriber(model_config)
                transcriber.load_model()
                self.step_signal.emit("load_model", "done")
            elif self.phase in [0, 1]:
                self.log_signal.emit("⚡ Bỏ qua việc tải mô hình Whisper AI do không có tính năng nào yêu cầu dịch giọng nói.")

            if self.is_interrupted:
                self._handle_interrupted()
                if transcriber:
                    try:
                        transcriber.unload_model()
                    except Exception:
                        pass
                return
            
            # Kết nối DaVinci Resolve
            resolve_auto = ResolveAutomation()
            
            if self.phase in [0, 2]:
                resolve_auto.ensure_resolve_running(log_callback=self.log_signal.emit)

            # Cấu hình các bộ xử lý
            cut_config = AudioCutConfig(
                min_silent_duration=self.min_duration,
                silence_threshold_db=self.silence_db,
                padding_seconds=self.pacing["padding_seconds"],
                speed_up_silence=self.speed_up_silence,
                silence_speed_multiplier=self.silence_speed
            )

            ai_config = AIDirectorConfig(
                mode=self.ai_mode,
                remove_bad_takes=self.remove_bad_takes,
                remove_repeated_phrases=self.remove_repeated_phrases,
                enable_punch_in=self.enable_punch_in,
                punch_in_scale=self.punch_in_scale,
                api_key=self.api_key,
                min_cut_gap=self.pacing["min_cut_gap"],
                max_static_shot=self.pacing["max_static_shot"],
                min_punch_in_duration=self.pacing["min_punch_in_duration"]
            )
            director = AIDirector(ai_config)

            reframe_config = ReframeConfig(target_aspect_ratio="9:16" if self.enable_reframe else "16:9")
            reframer = VisionReframer(reframe_config)

            cache_mgr = ScanCacheManager()

            merged_edl_events = []
            merged_cut_subtitles = []
            merged_orig_subtitles = []
            merged_markers = []
            all_broll_cues = []
            all_sfx_cues = []
            vlog_hook_segments = []
            hook_pool = []
            hook_video_order = []
            arrange_energy = {}
            arrange_activity = {}
            cumulative_record_seconds = 0.0
            uncut_cumulative_seconds = 0.0

            first_video = self.video_paths[0]
            if self.project_structure and getattr(self.project_structure, "root_path", None):
                project_root_dir = self.project_structure.root_path
            else:
                try:
                    common = os.path.commonpath([os.path.abspath(p) for p in self.video_paths])
                    project_root_dir = common if os.path.isdir(common) else os.path.dirname(common)
                except Exception:
                    project_root_dir = os.path.dirname(os.path.abspath(first_video))

            base_dir = project_root_dir
            output_dir = os.path.join(project_root_dir, "_TIMELINE_IMPORT")
            os.makedirs(output_dir, exist_ok=True)

            # Đăng ký tài nguyên dự án và toàn cục
            asset_pool = GlobalAssetPool.get_instance()
            n_pm, n_ps = asset_pool.register_project_assets(project_root_dir)
            if n_pm > 0 or n_ps > 0:
                self.log_signal.emit(f"📦 [Project Assets Pool] Đã nạp {n_pm} video B-roll/Meme và {n_ps} file âm thanh SFX từ thư mục dự án.")

            try:
                fps = EDLGenerator.get_video_fps(first_video)
            except Exception:
                fps = 30.0
            
            if self.project_structure and getattr(self.project_structure, "root_name", None):
                stamped_name = f"ChunDVC_{self.project_structure.root_name}"
            elif len(self.video_paths) == 1:
                stamped_name = os.path.splitext(os.path.basename(first_video))[0]
            else:
                stamped_name = f"ChunDVC_Merged_{len(self.video_paths)}clips"

            audit_reporter = ExecutionAuditReporter(project_name=stamped_name)
            audit_reporter.record_validation_warnings(self.validation_warnings)

            total_clips = len(self.video_paths)
            temp_audio_dir = os.path.join(tempfile.gettempdir(), "ChunDVC_Audio")
            os.makedirs(temp_audio_dir, exist_ok=True)

            # --- VÒNG LẶP XỬ LÝ TỪNG CLIP (PHASE 1 HOẶC END-TO-END) ---
            if self.phase in [0, 1]:
                self.step_signal.emit("speech_to_text", "running")
                for idx, video_path in enumerate(self.video_paths):
                    if self.is_interrupted:
                        self._handle_interrupted()
                        return

                    self.log_signal.emit(f"\n🎬 [Clip {idx+1}/{total_clips}] Phân tích nguồn: {os.path.basename(video_path)}")
                    
                    # Kiểm tra Scan Cache theo Checksum
                    cached_data = None
                    if self.use_cache and needs_transcription:
                        cached_data = cache_mgr.get_cached_scan(video_path, self.model_size, self.language)
                        if cached_data:
                            self.log_signal.emit("   ⚡ [Scan Cache Hit] Đã tìm thấy dữ liệu đệm từ trước, nạp hoàn tất trong 0.05s (Không tốn thời gian dịch lại)!")

                    clip_base_name = os.path.splitext(os.path.basename(video_path))[0]
                    unique_id = uuid.uuid4().hex[:8]
                    temp_wav = os.path.normpath(os.path.join(temp_audio_dir, f"{clip_base_name}_{unique_id}.wav"))
                    temp_files_to_clean.append(temp_wav)

                    if cached_data:
                        raw_subtitles = cached_data.get("raw_subtitles", [])
                        clip_dur = cached_data.get("clip_dur", 0.0)
                        clip_subs = split_subtitles(raw_subtitles, limit=self.split_limit, mode=self.split_mode)
                    else:
                        if needs_audio_extraction:
                            self.log_signal.emit("   🔊 Trích xuất audio mono 16kHz...")
                            AudioExtractor.extract_audio(video_path, temp_wav)

                        if self.is_interrupted:
                            self._handle_interrupted()
                            return
                        
                        if needs_transcription:
                            self.log_signal.emit("   🎙 Dịch giọng nói (Speech-to-Text Whisper)...")
                            lang_code = None if self.language == "Auto" else ("vi" if self.language == "Tiếng Việt" else "en")
                            
                            def on_progress(ratio, text_snippet):
                                pct = int(ratio * 100)
                                if pct % 20 == 0:
                                    self.log_signal.emit(f"      ⏳ Quét giọng nói: {pct}% -> \"{text_snippet[:35]}...\"")

                            raw_subtitles = transcriber.transcribe(
                                temp_wav, 
                                language=lang_code,
                                is_cancelled_callback=lambda: self.is_interrupted,
                                progress_callback=on_progress,
                                fill_gaps=self.fill_gaps
                            )

                            if self.is_interrupted:
                                self._handle_interrupted()
                                return

                            self.log_signal.emit(f"   ✔ Phát hiện {len(raw_subtitles)} đoạn thoại.")
                            clip_subs = split_subtitles(raw_subtitles, limit=self.split_limit, mode=self.split_mode)
                        else:
                            raw_subtitles = []
                            clip_subs = []

                        try:
                            clip_dur = AudioExtractor.get_audio_duration(video_path)
                        except Exception:
                            clip_dur = 0.0

                    keep_intervals = []
                    speedup_segments = []
                    cut_segments = []

                    visual_activity = None
                    if self.enable_vlog_hook or self.story_intent != "keep" or (self.run_cut and (self.scene_guard or self.video_type != "talk")):
                        self.log_signal.emit("   👁 [Scene Activity] Đo mức chuyển động hình ảnh (chỉ đọc keyframe, khá nhanh)...")
                        visual_activity = compute_visual_activity(video_path, duration=clip_dur)
                        if visual_activity is None:
                            self.log_signal.emit("   ⚠ Không đo được chuyển động hình ảnh (kiểm tra FFmpeg). Bỏ qua bảo vệ cảnh quay.")

                    # Kiểm tra xem clip có thuộc nhóm B-Roll của dự án hay không
                    is_clip_broll = False
                    if self.project_structure and getattr(self.project_structure, "groups", None):
                        for grp in self.project_structure.groups:
                            if grp.is_broll and any(os.path.normcase(os.path.abspath(video_path)) == os.path.normcase(os.path.abspath(gv)) for gv in grp.video_paths):
                                is_clip_broll = True
                                break

                    if is_clip_broll:
                        self.log_signal.emit("   🎥 [B-Roll / Footage Group] Nhận diện clip là cảnh chèn minh họa (B-Roll), bảo tồn nguyên vẹn toàn cảnh.")
                        from src.core.autocut import CutSegment
                        cut_segments = [CutSegment(start=0.0, end=clip_dur, action="keep", speed=1.0)]
                        keep_intervals = [(0.0, clip_dur)]
                    elif self.run_cut:
                        if not os.path.exists(temp_wav) or os.path.getsize(temp_wav) == 0:
                            AudioExtractor.extract_audio(video_path, temp_wav)

                        from src.core.autocut import CutSegment
                        if self.speed_up_silence:
                            self.log_signal.emit(f"   ⚡ [Speed-Ramp] Phân tích khoảng lặng để tua nhanh ({self.silence_speed}x)...")
                            raw_speedup_segs = SilenceDetector.detect_intervals_with_speedup(temp_wav, cut_config, self.silence_speed)
                            
                            cut_segments = []
                            for s in raw_speedup_segs:
                                action = "keep" if s["type"] == "voice" else "speedup"
                                cut_segments.append(CutSegment(
                                    start=s["start"],
                                    end=s["end"],
                                    action=action,
                                    speed=s.get("speed", 1.0)
                                ))
                        else:
                            self.log_signal.emit("   ✂ Lọc khoảng lặng âm lượng...")
                            raw_segments = SilenceDetector.detect_silence_from_wav(temp_wav, cut_config)
                            
                            # Chuẩn hóa nếu bị unit tests patch trả về List[Tuple]
                            if raw_segments and not isinstance(raw_segments[0], CutSegment):
                                raw_keep = sorted(raw_segments, key=lambda x: x[0])
                                dur = clip_dur if clip_dur > 0 else (raw_keep[-1][1] if raw_keep else 10.0)
                                
                                cut_segments = []
                                current_time = 0.0
                                for start, end in raw_keep:
                                    if start > current_time:
                                        cut_segments.append(CutSegment(start=current_time, end=start, action="cut", speed=1.0))
                                    cut_segments.append(CutSegment(start=start, end=end, action="keep", speed=1.0))
                                    current_time = end
                                if current_time < dur:
                                    cut_segments.append(CutSegment(start=current_time, end=dur, action="cut", speed=1.0))
                            else:
                                cut_segments = raw_segments

                        # Tư duy người dựng: loại video quyết định khoảng lặng nào đáng cắt / tua / giữ nguyên
                        speech_ratio = (sum(max(0.0, s["end"] - s["start"]) for s in raw_subtitles) / clip_dur) if clip_dur > 0 else 0.0
                        if self.video_type in POLICIES:
                            kind, vmetrics = self.video_type, {}
                        else:
                            kind, vmetrics = classify_video(speech_ratio, visual_activity)
                        policy = POLICIES[kind]
                        cut_segments, prep = apply_edit_policy(
                            cut_segments, policy, visual_activity,
                            prefer_speed=self.speed_up_silence, max_speed=self.silence_speed,
                            protect_active=(policy.protect_active and self.scene_guard)
                        )
                        self.log_signal.emit(
                            f"   🧠 [Edit Policy] Kiểu video: {policy.label}"
                            + (f" (tự nhận diện: {vmetrics})" if vmetrics else " (do bạn chọn)")
                        )
                        self.log_signal.emit(f"      Luật: {policy.summary}.")
                        self.log_signal.emit(
                            f"      Trong {prep['silences_found']} khoảng lặng: cắt {prep['cut']} ({prep['cut_seconds']:.0f}s), "
                            f"tua {prep['speedup']} ({prep['speedup_seconds']:.0f}s), giữ nguyên {prep['kept_as_is']} (nhịp thở / cảnh đang diễn ra)."
                        )

                        keep_intervals = [(seg.start, seg.end) for seg in cut_segments if seg.action == "keep"]
                        
                        # Điền speedup_segments để tương thích ngược
                        for seg in cut_segments:
                            rec_dur = seg.timeline_duration
                            note = f"⚡ Tua nhanh Timelapse ({seg.speed}x)" if seg.action == "speedup" else ("Thoại chính" if seg.action == "keep" else "Khoảng nghỉ ngắn")
                            speedup_segments.append({
                                "type": "voice" if seg.action == "keep" else "speedup",
                                "start": seg.start,
                                "end": seg.end,
                                "duration": seg.duration,
                                "speed": seg.speed,
                                "rec_duration": rec_dur,
                                "note": note
                            })
                    else:
                        if clip_dur <= 0:
                            try:
                                meta = get_media_metadata(video_path)
                                clip_dur = meta.get("duration", 0.0)
                            except Exception:
                                clip_dur = 0.0
                        if clip_dur > 0:
                            from src.core.autocut import CutSegment
                            cut_segments = [CutSegment(start=0.0, end=clip_dur, action="keep", speed=1.0)]
                            keep_intervals = [(0.0, clip_dur)]

                    # Tính audio_energy_db nếu có temp_wav
                    energy_list = None
                    if os.path.exists(temp_wav):
                        try:
                            energy_db_arr = VlogHookGenerator.audio_energy_db(temp_wav)
                            if energy_db_arr is not None:
                                energy_list = energy_db_arr.tolist()
                        except Exception:
                            energy_list = None

                    # Lưu vào Cache nếu vừa mới quét
                    if self.use_cache and not cached_data and needs_transcription:
                        cache_mgr.save_scan_result(
                            file_path=video_path,
                            scan_data={
                                "raw_subtitles": raw_subtitles,
                                "clip_dur": clip_dur,
                                "visual_activity": visual_activity.tolist() if visual_activity is not None else None,
                                "audio_energy_db": energy_list,
                                "silence_keep_intervals": keep_intervals,
                                "speedup_segments": speedup_segments
                            },
                            model_size=self.model_size,
                            language=self.language
                        )

                    # Lưu cache
                    clip_data_list.append({
                        "video_path": video_path,
                        "clip_dur": clip_dur,
                        "raw_subtitles": raw_subtitles,
                        "clip_subs": clip_subs,
                        "silence_keep_intervals": keep_intervals,
                        "speedup_segments": speedup_segments,
                        "cut_segments": cut_segments,
                        "visual_activity": visual_activity.tolist() if visual_activity is not None else (cached_data.get("visual_activity") if cached_data else None),
                        "audio_energy_db": energy_list if energy_list is not None else (cached_data.get("audio_energy_db") if cached_data else None)
                    })

                    if self.enable_loudnorm:
                        if not os.path.exists(temp_wav) or os.path.getsize(temp_wav) == 0:
                            AudioExtractor.extract_audio(video_path, temp_wav)
                        self.log_signal.emit(f"   🎚 [Loudness 2-Pass] Chuẩn hóa âm lượng EBU R128 / YouTube ({self.loudnorm_preset})...")
                        norm_wav = os.path.normpath(os.path.join(base_dir, f"{clip_base_name}_Normalized.wav"))
                        norm_metrics = AudioNormalizer.normalize_audio_file(
                            input_audio_path=temp_wav,
                            output_audio_path=norm_wav,
                            target=LOUDNESS_PRESETS.get(self.loudnorm_preset, LOUDNESS_PRESETS["youtube_tiktok"])
                        )
                        if norm_metrics:
                            audit_reporter.record_audio_normalization(
                                clip_name=os.path.basename(video_path),
                                input_i=norm_metrics.measured_i,
                                input_tp=norm_metrics.measured_tp,
                                output_i=norm_metrics.target_i,
                                output_tp=norm_metrics.target_tp,
                                preset_name=self.loudnorm_preset
                            )
                            audit_reporter.add_output_file("Âm thanh Đã Chuẩn Hóa (WAV)", norm_wav, f"Chuẩn {self.loudnorm_preset} (-14 LUFS, Peak -1.0 dBFS)")
                            self.log_signal.emit(f"      ✔ Âm lượng gốc: {norm_metrics.measured_i:.1f} LUFS -> Đạt chuẩn: {norm_metrics.target_i:.1f} LUFS (Gain: {norm_metrics.gain_delta_i:+.1f} LUFS)")

                    try:
                        if os.path.exists(temp_wav):
                            os.remove(temp_wav)
                    except Exception:
                        pass
                    
                    self.progress_signal.emit(int((idx + 1) / total_clips * 40))

                self.clip_data_out_cache = clip_data_list
                self.step_signal.emit("speech_to_text", "done")

            # --- PHÂN TÍCH & ĐỀ XUẤT CỦA AI DIRECTOR (CHỈ Ở PHASE 1) ---
            if self.phase == 1:
                self.step_signal.emit("ai_director", "running")
                self.log_signal.emit("\n🎬 [AI Director] Đang phân tích ngữ nghĩa và chuẩn bị đề xuất duyệt cắt...")
                proposed_all = []
                segment_offset = 0
                for idx, cdata in enumerate(self.clip_data_out_cache):
                    props = director.generate_proposed_segments(cdata["clip_subs"], language="vi" if self.language == "Tiếng Việt" else "en")
                    for p in props:
                        p.id = segment_offset
                        proposed_all.append(p)
                        segment_offset += 1
                
                self.proposed_segments = proposed_all
                has_cut_proposals = any(p.decision == "cut" for p in proposed_all)
                if has_cut_proposals:
                    self.log_signal.emit(f"✔ Đã lập {len(proposed_all)} phân đoạn đề xuất cắt/giữ.")
                    self.progress_signal.emit(100)
                    self.finished_signal.emit(True, "phase1_done")
                    return
                
                self.log_signal.emit(f"✔ Toàn bộ {len(proposed_all)} phân đoạn thoại đều đạt chuẩn (không có đề xuất cắt). Tự động chuyển tiếp Phase 2...")
                self.phase = 2
                self.proposed_segments_override = proposed_all
                if not resolve_auto.is_connected():
                    resolve_auto.ensure_resolve_running(log_callback=self.log_signal.emit)
                self.step_signal.emit("ai_director", "done")

            # --- GIAI ĐOẠN 2 HOẶC END-TO-END: ÁP DỤNG CẮT & XUẤT BẢN ---
            self.step_signal.emit("apply_cut", "running")
            segment_offset = 0
            for idx, cdata in enumerate(clip_data_list):
                if self.is_interrupted:
                    self._handle_interrupted()
                    return

                video_path = cdata["video_path"]
                clip_dur = cdata["clip_dur"]
                raw_subtitles = cdata["raw_subtitles"]
                clip_subs = cdata["clip_subs"]
                keep_intervals = cdata["silence_keep_intervals"]
                speedup_segments = cdata["speedup_segments"]
                cut_segments = cdata.get("cut_segments", [])
                
                if not cut_segments:
                    from src.core.autocut import CutSegment
                    if self.speed_up_silence and speedup_segments:
                        for s in speedup_segments:
                            action = "keep" if s["type"] == "voice" else "speedup"
                            cut_segments.append(CutSegment(
                                start=s["start"],
                                end=s["end"],
                                action=action,
                                speed=s.get("speed", 1.0)
                            ))
                    else:
                        raw_keep = sorted(keep_intervals, key=lambda x: x[0])
                        current_time = 0.0
                        for start, end in raw_keep:
                            if start > current_time:
                                cut_segments.append(CutSegment(start=current_time, end=start, action="cut", speed=1.0))
                            cut_segments.append(CutSegment(start=start, end=end, action="keep", speed=1.0))
                            current_time = end
                        if current_time < clip_dur:
                            cut_segments.append(CutSegment(start=current_time, end=clip_dur, action="cut", speed=1.0))
                
                try:
                    fps = EDLGenerator.get_video_fps(video_path)
                except Exception:
                    fps = 30.0

                self.log_signal.emit(f"\n🎬 [Clip {idx+1}/{total_clips}] Áp dụng cấu hình và dựng: {os.path.basename(video_path)}")

                # Tích lũy phụ đề gốc uncut
                for sub in clip_subs:
                    s_words = []
                    for w in sub.get("words", []):
                        s_words.append({
                            "word": w["word"],
                            "start": w["start"] + uncut_cumulative_seconds,
                            "end": w["end"] + uncut_cumulative_seconds
                        })
                    merged_orig_subtitles.append({
                        "start": sub["start"] + uncut_cumulative_seconds,
                        "end": sub["end"] + uncut_cumulative_seconds,
                        "text": sub["text"],
                        "words": s_words
                    })
                uncut_cumulative_seconds += clip_dur

                # Tín hiệu âm thanh + chuyển động của clip: dùng chung cho Vlog Hook và Story Arranger
                if self.enable_vlog_hook or self.story_intent != "keep":
                    import numpy as np
                    act_list = cdata.get("visual_activity")
                    act_arr = np.asarray(act_list, dtype=np.float32) if act_list else None
                    if act_arr is not None:
                        arrange_activity[video_path] = act_arr

                    energy_list = cdata.get("audio_energy_db")
                    if energy_list is not None:
                        energy_db = np.asarray(energy_list, dtype=np.float32)
                    else:
                        temp_wav = os.path.normpath(os.path.join(temp_audio_dir, f"hook_{uuid.uuid4().hex[:8]}.wav"))
                        temp_files_to_clean.append(temp_wav)
                        AudioExtractor.extract_audio(video_path, temp_wav)
                        energy_db = VlogHookGenerator.audio_energy_db(temp_wav)
                    if energy_db is not None:
                        arrange_energy[video_path] = energy_db

                if self.enable_vlog_hook:
                    cands = VlogHookGenerator.find_moment_candidates(
                        video_path=video_path,
                        total_duration=clip_dur,
                        subtitles=raw_subtitles,
                        energy_db=energy_db,
                        activity=act_arr,
                        moment_len=self.vlog_hook_duration,
                        top_k=40
                    )
                    hook_pool.extend(cands)
                    hook_video_order.append(video_path)
                    self.log_signal.emit(f"   ✨ [Vlog Hook] Đã đọc toàn clip, tìm được {len(cands)} khoảnh khắc ứng viên.")

                # Áp dụng Đạo diễn AI
                ai_punch_events = []
                if self.run_cut and self.ai_mode != "silence_only":
                    if self.phase == 2 and self.proposed_segments_override:
                        clip_props = []
                        num_props = len(clip_subs)
                        for k in range(num_props):
                            clip_props.append(self.proposed_segments_override[segment_offset + k])
                        for k, cp in enumerate(clip_props):
                            cp.id = k
                        segment_offset += num_props
                    else:
                        clip_props = director.generate_proposed_segments(clip_subs, language="vi" if self.language == "Tiếng Việt" else "en")
                        for k, cp in enumerate(clip_props):
                            cp.approved = (cp.decision == "keep")

                    self.log_signal.emit(f"   🎬 [AI Director] Áp dụng các phân đoạn thoại được duyệt...")
                    ai_res = director.apply_approved_segments(
                        subtitles=clip_subs,
                        proposed_segments=clip_props,
                        silence_keep_intervals=cut_segments,
                        total_duration=clip_dur,
                        language="vi" if self.language == "Tiếng Việt" else "en"
                    )
                    
                    cut_segments = ai_res["segments"]
                    keep_intervals = ai_res["keep_intervals"]
                    clip_subs = ai_res["subtitles"]
                    ai_punch_events = ai_res.get("punch_in_events", [])
                    stats = ai_res["stats"]
                    
                    if stats.get("removed_bad_takes", 0) > 0:
                        self.log_signal.emit(f"   ✂ [AI Director] Đã lọc {stats['removed_bad_takes']} đoạn nói vấp/thử lại câu.")
                    if stats.get("punch_ins_created", 0) > 0:
                        self.log_signal.emit(f"   🔍 [AI Director] Đã áp dụng {stats['punch_ins_created']} góc quay Punch-in Zoom ({self.punch_in_scale}x).")
                    merged_markers.extend(ai_res.get("markers", []))
                else:
                    self.log_signal.emit(f"   ✔ Giữ lại {len(keep_intervals)} phân đoạn âm thanh.")

                # Vision Reframe
                if self.enable_reframe and keep_intervals:
                    self.log_signal.emit("   👁 [Vision Reframer] Tính toán bám mặt chuyển đổi sang video dọc 9:16...")
                    reframe_events = reframer.generate_reframe_timeline_events(keep_intervals)
                    self.log_signal.emit(f"   ✔ Đã tính toán {len(reframe_events)} mốc Smooth Pan 9:16.")

                # B-Roll
                if self.enable_broll and clip_subs:
                    broll_cues = BRollAnalyzer.extract_broll_cues(clip_subs)
                    if broll_cues:
                        all_broll_cues.extend(broll_cues)
                        self.log_signal.emit(f"   🎞 [AI B-Roll] Gợi ý {len(broll_cues)} cảnh minh họa trên Video Track 2.")
                        for bc in broll_cues:
                            bc_time_mapped = map_time_to_timeline(bc.start, cut_segments, cumulative_record_seconds)
                            merged_markers.append({
                                "time": bc_time_mapped,
                                "duration": bc.duration,
                                "name": f"🎞 B-Roll: {bc.keyword}",
                                "note": f"Tìm: {bc.search_prompt}",
                                "color": "Magenta"
                            })

                # SFX
                if self.enable_sfx and keep_intervals:
                    sfx_cues = SFXEngine.generate_sfx_cues(
                        keep_intervals=keep_intervals,
                        punch_in_events=ai_punch_events,
                        broll_cues=all_broll_cues
                    )
                    if sfx_cues:
                        all_sfx_cues.extend(sfx_cues)
                        self.log_signal.emit(f"   🔊 [SFX Engine] Đã bố trí {len(sfx_cues)} điểm âm thanh hiệu ứng trên Audio Track 2.")
                        for sc in sfx_cues:
                            sc_time_mapped = map_time_to_timeline(sc.time, cut_segments, cumulative_record_seconds)
                            merged_markers.append({
                                "time": sc_time_mapped,
                                "duration": sc.duration,
                                "name": f"🔊 SFX: {sc.sfx_type.upper()}",
                                "note": sc.note,
                                "color": "Cyan"
                            })

                # Stitching (Unified CutSegment Architecture)
                clip_start_rec = cumulative_record_seconds
                for seg in cut_segments:
                    if seg.action == "cut":
                        continue

                    is_speed = (seg.action == "speedup")
                    rec_dur = seg.timeline_duration
                    rec_start = cumulative_record_seconds
                    rec_end = rec_start + rec_dur

                    is_punch = seg.punch_in
                    merged_edl_events.append({
                        "video_path": video_path,
                        "src_in": seg.start,
                        "src_out": seg.end,
                        "rec_in": rec_start,
                        "rec_out": rec_end,
                        "fps": fps,
                        "punch_in": is_punch,
                        "punch_in_scale": seg.punch_in_scale,
                        "is_speedup": is_speed,
                        "speed": seg.speed
                    })

                    if is_speed:
                        merged_markers.append({
                            "time": rec_start,
                            "duration": rec_dur,
                            "name": f"⚡ Fast-Forward ({seg.speed}x)",
                            "note": "Cú chuyển cảnh tua nhanh Timelapse",
                            "color": "Purple"
                        })

                    cumulative_record_seconds = rec_end

                display_subs = clip_subs
                if self.hide_weak_subs:
                    display_subs, hidden = drop_low_confidence_subs(clip_subs, 0.35)
                    if hidden:
                        self.log_signal.emit(f"   💬 Ẩn phụ đề của {hidden} câu Whisper không chắc chữ (hình và tiếng vẫn giữ nguyên).")
                mapped_clip_subs = map_subtitles_to_timeline(display_subs, cut_segments, clip_start_rec)
                merged_cut_subtitles.extend(mapped_clip_subs)

                # Record clip audit
                audit_reporter.record_clip_audit(
                    clip_index=idx + 1,
                    video_path=video_path,
                    original_duration=clip_dur,
                    silent_intervals_detected=len(keep_intervals) if self.run_cut else 0,
                    keep_intervals=keep_intervals,
                    raw_subtitles=raw_subtitles,
                    split_subtitles_list=clip_subs,
                    split_mode=self.split_mode,
                    split_limit=self.split_limit,
                    fps=fps
                )

                self.progress_signal.emit(int(40 + (idx + 1) / total_clips * 40))

            self.step_signal.emit("apply_cut", "done")
            # --- XUẤT TIMELINES INTRO & CHÍNH ---
            self.step_signal.emit("export", "running")
            target_aspect = "9:16" if self.enable_reframe else "16:9"
            output_teaser_edl = None
            output_teaser_fcpxml = None

            if self.enable_vlog_hook and hook_pool:
                vlog_hook_segments = VlogHookGenerator.select_teaser(
                    hook_pool, target_total=self.vlog_hook_total, video_order=hook_video_order
                )
                self.log_signal.emit(
                    f"\n✨ [Vlog Hook] Chọn {len(vlog_hook_segments)} khoảnh khắc hay nhất, "
                    f"tổng {sum(h.duration for h in vlog_hook_segments):.1f}s (mở đầu bằng điểm nhấn mạnh nhất):"
                )
                for order, h_seg in enumerate(vlog_hook_segments, 1):
                    self.log_signal.emit(
                        f"   {order}. [{h_seg.src_in:.1f}s - {h_seg.src_out:.1f}s] {os.path.basename(h_seg.video_path)} - {h_seg.reason}"
                    )
                    try:
                        h_fps = EDLGenerator.get_video_fps(h_seg.video_path)
                    except Exception:
                        h_fps = fps
                    audit_reporter.record_teaser_item(
                        order=order, video_path=h_seg.video_path, src_in=h_seg.src_in, src_out=h_seg.src_out,
                        reason=h_seg.reason, score=h_seg.score, hook_text=h_seg.text, fps=h_fps
                    )

            if self.enable_vlog_hook and vlog_hook_segments:
                output_teaser_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.edl")
                output_teaser_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.fcpxml")
                output_teaser_fcp7xml = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.xml")
                teaser_timeline_name = f"{stamped_name}_Timeline_Intro_Teaser"
                
                self.log_signal.emit(f"\n🎬 [Vlog Hook Generator] Đang tạo Timeline Highlight Teaser ({len(vlog_hook_segments)} clips)...")
                _, teaser_events = VlogHookGenerator.generate_teaser_edl(
                    segments=vlog_hook_segments,
                    output_edl_path=output_teaser_edl,
                    project_name=stamped_name
                )
                
                teaser_subs = []
                teaser_rec_time = 0.0
                for seg in vlog_hook_segments:
                    t_dur = seg.src_out - seg.src_in
                    if seg.text:
                        teaser_subs.append({
                            "start": teaser_rec_time,
                            "end": teaser_rec_time + t_dur,
                            "text": seg.text,
                            "words": []
                        })
                    teaser_rec_time += t_dur

                FCPXMLGenerator.generate_timeline_fcpxml(
                    events=teaser_events,
                    output_xml_path=output_teaser_fcpxml,
                    timeline_name=teaser_timeline_name,
                    fps=fps,
                    aspect_ratio=target_aspect,
                    subtitles=teaser_subs,
                    font_name=self.font_name,
                    font_size=self.font_size,
                    preset=self.text_preset_id
                )
                FCPXMLGenerator.generate_fcp7_xml(
                    events=teaser_events,
                    output_xml_path=output_teaser_fcp7xml,
                    timeline_name=teaser_timeline_name,
                    fps=fps,
                    aspect_ratio=target_aspect
                )

                # Đồng bộ bản sao sang output_dir (_TIMELINE_IMPORT)
                if output_dir != base_dir:
                    try:
                        import shutil
                        shutil.copy2(output_teaser_fcpxml, os.path.join(output_dir, os.path.basename(output_teaser_fcpxml)))
                        shutil.copy2(output_teaser_edl, os.path.join(output_dir, os.path.basename(output_teaser_edl)))
                        shutil.copy2(output_teaser_fcp7xml, os.path.join(output_dir, os.path.basename(output_teaser_fcp7xml)))
                    except Exception:
                        pass

                self.log_signal.emit(f"   👉 Tệp FCP7 XML Teaser (DaVinci Windows): {os.path.basename(output_teaser_fcp7xml)}")
                self.log_signal.emit(f"   👉 Tệp FCPXML Teaser: {os.path.basename(output_teaser_fcpxml)}")
                self.log_signal.emit(f"   👉 Tệp EDL Teaser: {os.path.basename(output_teaser_edl)}")
                
                resolve_auto.import_edl_to_timeline(
                    edl_path=output_teaser_fcpxml,
                    video_path=self.video_paths,
                    timeline_name=teaser_timeline_name,
                    log_callback=self.log_signal.emit
                )
                audit_reporter.add_output_file("Timeline Teaser (FCP7 XML - Windows)", output_teaser_fcp7xml, "Timeline 10–30s FCP7 XML chuẩn DaVinci Resolve Windows")
                audit_reporter.add_output_file("Timeline Teaser (FCPXML)", output_teaser_fcpxml, "Timeline 10–30s FCPXML v1.9")
                audit_reporter.add_output_file("Timeline Teaser (EDL)", output_teaser_edl, "Timeline 10–30s định dạng EDL CMX3600")
                audit_reporter.add_timeline(teaser_timeline_name, "Timeline Teaser/Hook mở đầu Vlog (10-30s)")

            if merged_edl_events:
                if self.run_cut:
                    timeline_name = f"{stamped_name}_Timeline_CatLoc"
                    output_timeline_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.fcpxml")
                    output_timeline_fcp7xml = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.xml")
                    output_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.edl")
                else:
                    timeline_name = f"{stamped_name}_Timeline_Goc_CoSub"
                    output_timeline_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_Goc_CoSub.fcpxml")
                    output_timeline_fcp7xml = os.path.join(base_dir, f"{stamped_name}_Timeline_Goc_CoSub.xml")
                    output_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_Goc_CoSub.edl")

                subs_to_embed = merged_cut_subtitles if self.enable_subtitles else None

                FCPXMLGenerator.generate_timeline_fcpxml(
                    events=merged_edl_events,
                    output_xml_path=output_timeline_fcpxml,
                    timeline_name=timeline_name,
                    fps=fps,
                    aspect_ratio=target_aspect,
                    subtitles=subs_to_embed,
                    font_name=self.font_name,
                    font_size=self.font_size,
                    preset=self.text_preset_id
                )

                FCPXMLGenerator.generate_fcp7_xml(
                    events=merged_edl_events,
                    output_xml_path=output_timeline_fcp7xml,
                    timeline_name=timeline_name,
                    fps=fps,
                    aspect_ratio=target_aspect,
                    broll_inserts=all_broll_cues if self.enable_broll else None,
                    sfx_inserts=all_sfx_cues if self.enable_sfx else None,
                    markers=merged_markers
                )

                EDLGenerator.create_multi_clip_edl(merged_edl_events, output_edl, markers=merged_markers)

                # Đồng bộ sang output_dir (_TIMELINE_IMPORT)
                if output_dir != base_dir:
                    try:
                        import shutil
                        shutil.copy2(output_timeline_fcpxml, os.path.join(output_dir, os.path.basename(output_timeline_fcpxml)))
                        shutil.copy2(output_timeline_fcp7xml, os.path.join(output_dir, os.path.basename(output_timeline_fcp7xml)))
                        shutil.copy2(output_edl, os.path.join(output_dir, os.path.basename(output_edl)))
                    except Exception:
                        pass
                
                self.log_signal.emit(f"\n📁 ĐÃ TẠO TẤT CẢ FILE TIMELINE TẠI THƯ MỤC IMPORT:\n      📂 {os.path.abspath(output_dir)}")
                self.log_signal.emit(f"      👉 FCP7 Multi-Track XML (DaVinci Windows): {os.path.abspath(output_timeline_fcp7xml)}")
                self.log_signal.emit(f"      👉 Apple FCPXML v1.9: {os.path.abspath(output_timeline_fcpxml)}")
                self.log_signal.emit(f"      👉 EDL CMX3600 (Dự phòng): {os.path.abspath(output_edl)}")
                val_res = DryRunValidator.validate_fcpxml_integrity(
                    output_timeline_fcpxml,
                    expected_media_paths=self.video_paths,
                    resolve_automation=resolve_auto
                )
                if not val_res.is_valid:
                    self.log_signal.emit(f"   ❌ Lỗi kiểm tra FCPXML: {'; '.join(val_res.errors)}")
                if val_res.warnings:
                    for warn_msg in val_res.warnings:
                        self.log_signal.emit(f"   ⚠️ {warn_msg}")

                resolve_auto.import_edl_to_timeline(
                    edl_path=output_timeline_fcpxml,
                    video_path=self.video_paths,
                    timeline_name=timeline_name,
                    log_callback=self.log_signal.emit
                )

                if self.run_cut:
                    audit_reporter.add_output_file("Timeline Cắt Lọc (FCP7 XML - Windows)", output_timeline_fcp7xml, "Timeline chính FCP7 XML Multi-Track (V1, V2 B-Roll, A1/A2, A3 SFX)")
                    audit_reporter.add_output_file("Timeline Cắt Lọc (FCPXML - Khuyên Dùng)", output_timeline_fcpxml, "Timeline chính FCPXML v1.9 link media tự động tuyệt đối chính xác")
                    audit_reporter.add_output_file("Timeline Cắt Lọc (EDL)", output_edl, "Timeline chính đã lọc sạch khoảng lặng và tua nhanh")
                    audit_reporter.add_timeline(timeline_name, "Timeline chính đã biên tập âm thanh & thị giác")
                else:
                    audit_reporter.add_output_file("Timeline Gốc (FCP7 XML - Windows)", output_timeline_fcp7xml, "Timeline gốc FCP7 XML chứa video gốc và phụ đề")
                    audit_reporter.add_output_file("Timeline Gốc (FCPXML - Khuyên Dùng)", output_timeline_fcpxml, "Timeline gốc FCPXML v1.9 chứa video gốc và phụ đề")
                    audit_reporter.add_output_file("Timeline Gốc (EDL)", output_edl, "Timeline gốc đã link media")
                    audit_reporter.add_timeline(timeline_name, "Timeline video gốc tích hợp phụ đề")

                if self.enable_subtitles:
                    output_cut_srt = os.path.join(base_dir, f"{stamped_name}_PhuDe_VideoDaCat.srt" if self.run_cut else f"{stamped_name}_PhuDe_VideoGoc.srt")
                    resolve_auto.generate_srt(merged_cut_subtitles, output_cut_srt)
                    
                    output_cut_fcpxml = os.path.join(base_dir, f"{stamped_name}_PhuDe_Karaoke_VideoDaCat.fcpxml" if self.run_cut else f"{stamped_name}_PhuDe_Karaoke_VideoGoc.fcpxml")
                    FCPXMLGenerator.generate_karaoke_fcpxml(
                        subtitles=merged_cut_subtitles,
                        output_path=output_cut_fcpxml,
                        font_name=self.font_name,
                        font_size=self.font_size,
                        aspect_ratio=target_aspect,
                        markers=merged_markers,
                        preset=self.text_preset_id
                    )

                    if output_dir != base_dir:
                        try:
                            import shutil
                            shutil.copy2(output_cut_srt, os.path.join(output_dir, os.path.basename(output_cut_srt)))
                            shutil.copy2(output_cut_fcpxml, os.path.join(output_dir, os.path.basename(output_cut_fcpxml)))
                        except Exception:
                            pass
                    
                    if self.run_cut:
                        audit_reporter.add_output_file("Phụ đề Video Đã Cắt (SRT)", output_cut_srt, "Phụ đề chuẩn cho timeline đã qua cắt gọt")
                        audit_reporter.add_output_file("Phụ đề Karaoke Đã Cắt (FCPXML)", output_cut_fcpxml, f"Phụ đề chữ nhảy/đổi màu động (Text+) trên video đã cắt ({target_aspect})")
                    else:
                        audit_reporter.add_output_file("Phụ đề Video Gốc (SRT)", output_cut_srt, "Phụ đề thô khớp với video quay ban đầu")
                        audit_reporter.add_output_file("Phụ đề Karaoke Gốc (FCPXML)", output_cut_fcpxml, f"Phụ đề chữ nhảy/đổi màu động (Text+) trên video gốc ({target_aspect})")

            if merged_edl_events and self.story_intent != "keep":
                self._export_arranged_timeline(
                    events=merged_edl_events,
                    subtitles=merged_cut_subtitles,
                    markers=merged_markers,
                    energy_by_video=arrange_energy,
                    activity_by_video=arrange_activity,
                    base_dir=base_dir,
                    stamped_name=stamped_name,
                    fps=fps,
                    target_aspect=target_aspect,
                    audit_reporter=audit_reporter,
                    resolve_auto=resolve_auto
                )

            if all_broll_cues:
                output_broll_file = os.path.join(base_dir, f"{stamped_name}_GoiY_ChenCanh_BRoll.txt")
                with open(output_broll_file, "w", encoding="utf-8") as bf:
                    bf.write(f"DANH SÁCH GỢI Ý CẢNH MINH HỌA B-ROLL (TRACK VIDEO 2)\n")
                    bf.write(f"Dự án: {stamped_name}\n")
                    bf.write("="*60 + "\n")
                    for bc_idx, bc in enumerate(all_broll_cues, 1):
                        bf.write(f"{bc_idx:02d}. [{bc.start}s - {bc.end}s] ({bc.duration}s)\n")
                        bf.write(f"    - Từ khóa: {bc.keyword}\n")
                        bf.write(f"    - Tìm kiếm: {bc.search_prompt}\n\n")
                audit_reporter.add_output_file("Gợi ý B-Roll (TXT)", output_broll_file, "Danh sách từ khóa và mốc thời gian gợi ý chèn footage minh họa")
                if output_dir != base_dir:
                    try:
                        import shutil
                        shutil.copy2(output_broll_file, os.path.join(output_dir, os.path.basename(output_broll_file)))
                    except Exception:
                        pass
                with open(output_broll_file, "w", encoding="utf-8") as bf:
                    bf.write(f"DANH SÁCH GỢI Ý CẢNH MINH HỌA B-ROLL (TRACK VIDEO 2)\n")
                    bf.write(f"Dự án: {stamped_name}\n")
                    bf.write("="*60 + "\n")
                    for bc_idx, bc in enumerate(all_broll_cues, 1):
                        bf.write(f"{bc_idx:02d}. [{bc.start}s - {bc.end}s] ({bc.duration}s)\n")
                        bf.write(f"    - Từ khóa: {bc.keyword}\n")
                        bf.write(f"    - Tìm kiếm: {bc.search_prompt}\n\n")
                audit_reporter.add_output_file("Gợi ý B-Roll (TXT)", output_broll_file, "Danh sách từ khóa và mốc thời gian gợi ý chèn footage minh họa")

            if self.enable_subtitles and self.run_cut:
                output_orig_srt = os.path.join(base_dir, f"{stamped_name}_PhuDe_VideoGoc.srt")
                resolve_auto.generate_srt(merged_orig_subtitles, output_orig_srt)
                audit_reporter.add_output_file("Phụ đề Video Gốc (SRT)", output_orig_srt, "Phụ đề thô khớp với video quay ban đầu")
                
                output_orig_fcpxml = os.path.join(base_dir, f"{stamped_name}_PhuDe_Karaoke_VideoGoc.fcpxml")
                FCPXMLGenerator.generate_karaoke_fcpxml(
                    subtitles=merged_orig_subtitles,
                    output_path=output_orig_fcpxml,
                    font_name=self.font_name,
                    font_size=self.font_size,
                    aspect_ratio=target_aspect,
                    preset=self.text_preset_id
                )
                audit_reporter.add_output_file("Phụ đề Karaoke Gốc (FCPXML)", output_orig_fcpxml, "Phụ đề chữ nhảy/đổi màu động trên video gốc")

            output_report_md = os.path.join(base_dir, f"{stamped_name}_BaoCao_NhatKyXuLy.md")
            audit_reporter.export_markdown_report(output_report_md)
            audit_reporter.add_output_file("Báo cáo Nhật ký Xử lý (MD)", output_report_md, "Thống kê chi tiết")

            try:
                subs_for_marketing = merged_cut_subtitles if merged_cut_subtitles else merged_orig_subtitles
                if subs_for_marketing:
                    viral_pack = MarketingViralPackGenerator.generate_viral_pack(
                        project_name=stamped_name,
                        subtitles=subs_for_marketing,
                        video_duration=total_initial_duration
                    )
                    output_viral_pack_md = os.path.join(base_dir, f"{stamped_name}_GoiY_Marketing_ViralPack.md")
                    MarketingViralPackGenerator.export_markdown_report(viral_pack, output_viral_pack_md)
                    audit_reporter.add_output_file("Gợi Ý Marketing & Viral Pack (MD)", output_viral_pack_md, "Bộ 5 Tiêu đề CTR cao, Hook mở đầu, Kịch bản CTA & SEO Description")
                    self.log_signal.emit(f"   🚀 [Marketing Viral Pack] Đã tạo gói gợi ý Tiêu đề & Kịch bản chuyển đổi: {os.path.basename(output_viral_pack_md)}")
            except Exception as e:
                self.log_signal.emit(f"   ⚠ Lỗi khi tạo Viral Marketing Pack: {e}")

            self.progress_signal.emit(100)
            console_summary = audit_reporter.generate_console_summary()
            self.log_signal.emit(console_summary)
            self.log_signal.emit(f"\n📄 Báo cáo chi tiết đã được xuất bản tại:\n      👉 {os.path.abspath(output_report_md)}")
            
            if self.phase == 2:
                self.finished_signal.emit(True, "phase2_done")
            else:
                self.finished_signal.emit(True, "Hoàn thành!")
            self.step_signal.emit("export", "done")

        except Exception as e:
            if self.is_interrupted:
                self._handle_interrupted()
            else:
                tb_str = traceback.format_exc()
                self.log_signal.emit(f"❌ Gặp lỗi nghiêm trọng: {str(e)}")
                self.log_signal.emit(f"📋 Chi tiết lỗi hệ thống:\n{tb_str}")
                if 'audit_reporter' in locals() and audit_reporter:
                    try:
                        output_report_md = os.path.join(base_dir, f"{stamped_name}_BaoCao_NhatKyXuLy.md")
                        with open(output_report_md, "a", encoding="utf-8") as rf:
                            rf.write(f"\n\n## ⚠️ NHẬT KÝ LỖI HỆ THỐNG (ERROR LOG)\n```text\n{tb_str}\n```\n")
                    except Exception:
                        pass
                self.finished_signal.emit(False, f"{type(e).__name__}: {str(e)}")
                self.step_signal.emit("export", "error")  # Mark last known step as error, widget will ignore if invalid
        finally:
            for temp_f in temp_files_to_clean:
                try:
                    if os.path.exists(temp_f):
                        os.remove(temp_f)
                except Exception:
                    pass
            if transcriber:
                try:
                    transcriber.unload_model()
                except Exception:
                    pass

    def _export_arranged_timeline(self, events, subtitles, markers, energy_by_video, activity_by_video,
                                  base_dir, stamped_name, fps, target_aspect, audit_reporter, resolve_auto):
        """Sắp xếp timeline đã cắt theo ý đồ và xuất thành timeline riêng (_SapXep); timeline gốc được giữ nguyên."""
        from src.core import story_arranger as sa
        self.log_signal.emit(f"\n🧭 [Story Arranger] Ý đồ: {sa.INTENTS.get(self.story_intent, self.story_intent)}")
        try:
            blocks = sa.build_blocks(events, subtitles, energy_by_video, activity_by_video)
            if self.story_arrangement_override:
                arrangement = self.story_arrangement_override
                self.log_signal.emit(f"   🧭 [Story Arranger] Áp dụng cấu hình kịch bản do bạn tinh chỉnh ({len(arrangement.items)} mục)...")
            else:
                roles_override = None
                if self.api_key:
                    try:
                        roles_override, why = sa.llm_assign_roles(blocks, self.story_intent, self.api_key)
                        self.log_signal.emit(f"   🤖 LLM đã gán vai trò cho {len(roles_override)} khối. {why[:160]}")
                    except Exception as e:
                        self.log_signal.emit(f"   ⚠ Không dùng được LLM ({str(e)[:100]}), chuyển sang chấm điểm cục bộ.")

                if not roles_override and self.project_structure and getattr(self.project_structure, "groups", None):
                    folder_hints = {}
                    for b in blocks:
                        src_v = b.first_src[0] if b.first_src else None
                        if src_v:
                            for grp in self.project_structure.groups:
                                if grp.role_hint in ("intro", "outro", "climax", "build") and any(
                                    os.path.normcase(os.path.abspath(src_v)) == os.path.normcase(os.path.abspath(gv)) for gv in grp.video_paths
                                ):
                                    folder_hints[b.id] = grp.role_hint
                                    break
                    if folder_hints:
                        roles_override = folder_hints
                        self.log_signal.emit(f"   📂 Tự động phân vai trò cho {len(folder_hints)} khối theo cấu trúc thư mục ({len(self.project_structure.groups)} nhóm).")

                sa.tag_roles(blocks, roles_override)
                arrangement = sa.arrange(blocks, self.story_intent, target_seconds=self.story_target)

            self.story_blocks_out = blocks
            self.story_arrangement_out = arrangement

            if not arrangement.changed and not self.story_arrangement_override:
                self.log_signal.emit("   ℹ Không tìm được cách sắp xếp khác thứ tự gốc (ít cảnh / không có Hook rõ). Giữ nguyên timeline.")
                return

            new_events, new_subs, new_markers = sa.apply_arrangement(events, subtitles, markers, arrangement)
            plan_lines = sa.describe(arrangement, blocks)
            self.log_signal.emit(f"   ✔ {len(blocks)} khối -> {len(arrangement.items)} mục, tổng {arrangement.total_seconds:.0f}s. Kế hoạch:")
            for ln in plan_lines[:15]:
                self.log_signal.emit(f"     {ln}")
            if len(plan_lines) > 15:
                self.log_signal.emit(f"     ... còn {len(plan_lines) - 15} mục (xem file kế hoạch)")

            timeline_name = f"{stamped_name}_Timeline_SapXep"
            out_fcpxml = os.path.join(base_dir, f"{timeline_name}.fcpxml")
            out_fcp7xml = os.path.join(base_dir, f"{timeline_name}.xml")
            out_edl = os.path.join(base_dir, f"{timeline_name}.edl")
            out_plan = os.path.join(base_dir, f"{stamped_name}_KeHoach_SapXep.txt")
            FCPXMLGenerator.generate_timeline_fcpxml(
                events=new_events, output_xml_path=out_fcpxml, timeline_name=timeline_name, fps=fps,
                aspect_ratio=target_aspect, subtitles=new_subs if self.enable_subtitles else None,
                font_name=self.font_name, font_size=self.font_size, preset=self.text_preset_id
            )
            FCPXMLGenerator.generate_fcp7_xml(
                events=new_events,
                output_xml_path=out_fcp7xml,
                timeline_name=timeline_name,
                fps=fps,
                aspect_ratio=target_aspect,
                markers=new_markers
            )
            EDLGenerator.create_multi_clip_edl(new_events, out_edl, markers=new_markers)
            with open(out_plan, "w", encoding="utf-8") as f:
                f.write(f"Ý đồ: {sa.INTENTS.get(self.story_intent, self.story_intent)}\n\n" + "\n".join(plan_lines))
            self.log_signal.emit(f"   👉 Timeline đã sắp xếp (FCP7 XML - Windows): {os.path.basename(out_fcp7xml)}")
            self.log_signal.emit(f"   👉 Timeline đã sắp xếp (FCPXML): {os.path.basename(out_fcpxml)}")
            resolve_auto.import_edl_to_timeline(
                edl_path=out_fcpxml, video_path=self.video_paths, timeline_name=timeline_name,
                log_callback=self.log_signal.emit
            )
            audit_reporter.add_output_file("Timeline Sắp Xếp Có Ý Đồ (FCP7 XML - Windows)", out_fcp7xml, f"Ý đồ: {sa.INTENTS.get(self.story_intent)}")
            audit_reporter.add_output_file("Timeline Sắp Xếp Có Ý Đồ (FCPXML)", out_fcpxml, f"Ý đồ: {sa.INTENTS.get(self.story_intent)}")
            audit_reporter.add_output_file("Kế hoạch sắp xếp (TXT)", out_plan, "Danh sách khối, vai trò và thứ tự mới để duyệt")
            audit_reporter.add_timeline(timeline_name, "Timeline đã sắp xếp theo ý đồ (timeline gốc vẫn được giữ)")
        except Exception as e:
            self.log_signal.emit(f"   ⚠ Story Arranger gặp lỗi, bỏ qua bước sắp xếp (timeline gốc không bị ảnh hưởng): {e}")

    def _handle_interrupted(self):
        self.log_signal.emit("🛑 Tiến trình đã được dừng lại an toàn theo yêu cầu của bạn.")
        self.finished_signal.emit(False, "Tiến trình đã dừng bởi người dùng (Cancelled).")


class PipelineStepTrackerWidget(QFrame):
    """
    Hiển thị 6 bước tiến trình chi tiết chuẩn theo bản thiết kế:
    1. Kiểm tra (Validation & source clips)
    2. Tải AI (Faster-Whisper model loading)
    3. Nhận diện (Speech-to-text / Silence / Cache)
    4. Đạo diễn AI (Script & Semantic Director)
    5. Biên tập (Auto-cut / Punch-in / B-roll / SFX / Subtitles)
    6. Xuất bản (Generate FCPXML / Export DaVinci)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame#step_tracker_frame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 4px;
            }
        """)
        self.setObjectName("step_tracker_frame")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)

        self.step_keys = [
            "validate",
            "load_model",
            "speech_to_text",
            "ai_director",
            "apply_cut",
            "export"
        ]

        self.step_configs = {
            "validate": {
                "name": "Kiểm tra",
                "desc": "Kiểm tra tệp video & định dạng nguồn",
                "default_time": "0:04"
            },
            "load_model": {
                "name": "Tải AI",
                "desc": "Faster-Whisper AI model",
                "default_time": "0:09"
            },
            "speech_to_text": {
                "name": "Nhận diện",
                "desc": "Lời thoại, khoảng lặng, chuyển động",
                "default_time": "2:41"
            },
            "ai_director": {
                "name": "Đạo diễn AI",
                "desc": "Lọc vấp, dựng mạch, tìm Hook",
                "default_time": "0:38"
            },
            "apply_cut": {
                "name": "Biên tập",
                "desc": "Punch-in, B-roll, SFX, phụ đề",
                "default_time": "0:21"
            },
            "export": {
                "name": "Xuất bản",
                "desc": "FCPXML 1.9 cho DaVinci Resolve",
                "default_time": "0:03"
            }
        }

        self.rows = {}
        self.start_times = {}

        for key in self.step_keys:
            cfg = self.step_configs[key]
            row_widget = QWidget()
            row_lay = QHBoxLayout(row_widget)
            row_lay.setContentsMargins(0, 2, 0, 2)
            row_lay.setSpacing(8)

            lbl_icon = QLabel("○")
            lbl_icon.setFixedSize(22, 22)
            lbl_icon.setAlignment(Qt.AlignCenter)
            lbl_icon.setStyleSheet("""
                background-color: transparent;
                color: #52525b;
                border: 1.5px solid #3f3f46;
                border-radius: 11px;
                font-size: 11px;
            """)

            vbox_text = QVBoxLayout()
            vbox_text.setContentsMargins(0, 0, 0, 0)
            vbox_text.setSpacing(1)

            lbl_name = QLabel(cfg["name"])
            lbl_name.setStyleSheet("color: #71717a; font-size: 12px; font-weight: 600;")

            lbl_desc = QLabel(cfg["desc"])
            lbl_desc.setStyleSheet("color: #52525b; font-size: 10px;")

            vbox_text.addWidget(lbl_name)
            vbox_text.addWidget(lbl_desc)

            lbl_time = QLabel("")
            lbl_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl_time.setStyleSheet("color: #71717a; font-size: 11px; font-family: monospace;")

            row_lay.addWidget(lbl_icon)
            row_lay.addLayout(vbox_text, stretch=1)
            row_lay.addWidget(lbl_time)

            self.layout.addWidget(row_widget)
            self.rows[key] = {
                "widget": row_widget,
                "icon": lbl_icon,
                "name": lbl_name,
                "desc": lbl_desc,
                "time": lbl_time,
                "status": "idle"
            }

    def set_step_detail(self, step_key: str, desc: str):
        if step_key in self.rows:
            self.rows[step_key]["desc"].setText(desc)

    def set_step_status(self, step_key: str, status: str, detail: str = ""):
        import time
        if step_key not in self.rows:
            return

        now = time.time()
        row = self.rows[step_key]
        row["status"] = status

        if detail:
            row["desc"].setText(detail)

        if status == "running":
            step_idx = self.step_keys.index(step_key)
            for i in range(step_idx):
                prior_key = self.step_keys[i]
                if self.rows[prior_key]["status"] in ["idle", "running"]:
                    self._set_row_done(prior_key)

            self.start_times[step_key] = now
            row["icon"].setText("●")
            row["icon"].setStyleSheet("""
                background-color: rgba(6, 182, 212, 0.2);
                color: #22d3ee;
                border: 1.5px solid #06b6d4;
                border-radius: 11px;
                font-weight: bold;
                font-size: 12px;
            """)
            row["name"].setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: bold;")
            row["desc"].setStyleSheet("color: #a1a1aa; font-size: 10px;")
            if not row["time"].text():
                row["time"].setText("...")
            row["time"].setStyleSheet("color: #22d3ee; font-size: 11px; font-family: monospace; font-weight: bold;")

        elif status == "done":
            self._set_row_done(step_key)

        elif status == "error":
            row["icon"].setText("✕")
            row["icon"].setStyleSheet("""
                background-color: rgba(239, 68, 68, 0.2);
                color: #f87171;
                border: 1.5px solid #ef4444;
                border-radius: 11px;
                font-weight: bold;
                font-size: 11px;
            """)
            row["name"].setStyleSheet("color: #f87171; font-size: 12px; font-weight: bold;")
            row["time"].setText("Lỗi")
            row["time"].setStyleSheet("color: #ef4444; font-size: 11px;")

        elif status == "stopped":
            row["icon"].setText("⏹")
            row["icon"].setStyleSheet("""
                background-color: rgba(239, 68, 68, 0.15);
                color: #f87171;
                border: 1.5px solid rgba(239, 68, 68, 0.5);
                border-radius: 11px;
                font-size: 10px;
            """)
            row["name"].setStyleSheet("color: #fca5a5; font-size: 12px;")
            row["time"].setText("Đã dừng")
            row["time"].setStyleSheet("color: #ef4444; font-size: 10px;")

    def _set_row_done(self, step_key: str):
        import time
        row = self.rows[step_key]
        row["status"] = "done"
        row["icon"].setText("✓")
        row["icon"].setStyleSheet("""
            background-color: #15803d;
            color: #ffffff;
            border: 1.5px solid #22c55e;
            border-radius: 11px;
            font-weight: bold;
            font-size: 11px;
        """)
        row["name"].setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 500;")
        row["desc"].setStyleSheet("color: #71717a; font-size: 10px;")

        start_t = self.start_times.get(step_key)
        if start_t:
            elapsed = max(1, int(time.time() - start_t))
            m = elapsed // 60
            s = elapsed % 60
            row["time"].setText(f"{m}:{s:02d}")
        elif not row["time"].text() or row["time"].text() in ["...", "0%"]:
            row["time"].setText(self.step_configs[step_key]["default_time"])
        row["time"].setStyleSheet("color: #71717a; font-size: 11px; font-family: monospace;")

    def update_step_progress(self, step_key: str, pct: int):
        if step_key in self.rows and self.rows[step_key]["status"] == "running":
            self.rows[step_key]["time"].setText(f"{pct}%")
            self.rows[step_key]["time"].setStyleSheet("color: #22d3ee; font-size: 11px; font-family: monospace; font-weight: bold;")

    def mark_stopped(self):
        for key in self.step_keys:
            if self.rows[key]["status"] == "running":
                self.set_step_status(key, "stopped")

    def reset(self):
        self.start_times.clear()
        for key in self.step_keys:
            row = self.rows[key]
            row["status"] = "idle"
            row["icon"].setText("○")
            row["icon"].setStyleSheet("""
                background-color: transparent;
                color: #52525b;
                border: 1.5px solid #3f3f46;
                border-radius: 11px;
                font-size: 11px;
            """)
            row["name"].setStyleSheet("color: #71717a; font-size: 12px; font-weight: 600;")
            row["desc"].setStyleSheet("color: #52525b; font-size: 10px;")
            row["desc"].setText(self.step_configs[key]["desc"])
            row["time"].setText("")


class AutoWindow(QMainWindow):
    """
    Lớp giao diện người dùng chính (Main Dashboard) của ChunDVC v1.0.
    Tích hợp Workflow Mode 1-click, Phân tầng 2 lớp Cơ bản/Nâng cao, Hệ thống Text Style Preset,
    Hệ thống Recipe và Scan Cache siêu tốc.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChunDVC v1.0 - AI Visual & Director Automation Suite")
        self.resize(1280, 880)
        self.setMinimumSize(1080, 720)
        self.worker = None
        self.selected_files = []
        self.is_processing = False
        
        self.user_customized_limit = False
        self.current_phase = 1
        self.clip_data_cache = []
        self.proposed_segments = []
        self.validation_warnings = []
        self.project_structure: Optional[ProjectFolderStructure] = None
        self.story_review_state: Optional[StoryReviewState] = None

        self.preset_mgr = PresetManager()
        self.recipe_mgr = RecipeManager()
        self.setAcceptDrops(True)

        self.bubble = None

        self.workflow_stage = 1
        self._init_ui()
        self._apply_stylesheet()
        self._load_presets_to_combo()
        self._load_recipes_to_combo()
        self._set_workflow_stage(1)

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        window_layout = QVBoxLayout(main_widget)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.setSpacing(0)

        # =========================================================================
        # 1. HEADER (Titlebar)
        # =========================================================================
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background-color: #111113; border-bottom: 1px solid #27272a;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        h_layout.setSpacing(12)

        lbl_logo = QLabel("⚡")
        lbl_logo.setStyleSheet("font-size: 16px; color: #a78bfa;")
        h_layout.addWidget(lbl_logo)

        lbl_title = QLabel("AI Director")
        lbl_title.setStyleSheet("color: #f4f4f5; font-size: 14px; font-weight: bold;")
        h_layout.addWidget(lbl_title)

        lbl_crumb = QLabel("/ ResolveFlow_H264")
        lbl_crumb.setStyleSheet("color: #71717a; font-size: 13px;")
        h_layout.addWidget(lbl_crumb)

        h_layout.addStretch()

        pill_status = QLabel("● Đang nhận diện lời thoại")
        pill_status.setStyleSheet("background-color: rgba(139,92,246,0.15); color: #c4b5fd; padding: 4px 10px; border-radius: 12px; font-size: 12px;")
        h_layout.addWidget(pill_status)

        pill_resolve = QLabel("● DaVinci Resolve đã kết nối")
        pill_resolve.setStyleSheet("background-color: rgba(34,197,94,0.15); color: #86efac; padding: 4px 10px; border-radius: 12px; font-size: 12px;")
        h_layout.addWidget(pill_resolve)

        window_layout.addWidget(header)

        # =========================================================================
        # 2. MAIN WORKSPACE (Grid 3 Cột: Left 300px | Center Stretch | Right 340px)
        # =========================================================================
        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        # -------------------------------------------------------------------------
        # CỘT TRÁI (300px): Nguồn footage, Dự án, Cache, Danh sách chương
        # -------------------------------------------------------------------------
        # -------------------------------------------------------------------------
        # CỘT TRÁI (300px): Nguồn footage, Dự án, Cache, Danh sách chương
        # -------------------------------------------------------------------------
        a_left = QFrame()
        a_left.setFixedWidth(292)
        a_left.setStyleSheet("background-color: #111113; border: none;")
        l_vbox = QVBoxLayout(a_left)
        l_vbox.setContentsMargins(14, 14, 14, 14)
        l_vbox.setSpacing(12)

        # Dropzone (Vùng nhận diện kéo thả viền tím cách điệu - Nhỏ gọn, 1 ô duy nhất)
        self.drop_frame = QFrame()
        self.drop_frame.setObjectName("drop_frame")
        self.drop_frame.setFixedHeight(102)
        self.drop_frame.setStyleSheet("""
            QFrame#drop_frame {
                border: 1.5px dashed rgba(167, 139, 250, 0.45);
                border-radius: 12px;
                background-color: #18181b;
            }
            QFrame#drop_frame:hover {
                border-color: #a78bfa;
                background-color: rgba(139, 92, 246, 0.12);
            }
            QFrame#drop_frame QLabel {
                border: none;
                background-color: transparent;
            }
        """)
        drop_layout = QVBoxLayout(self.drop_frame)
        drop_layout.setContentsMargins(10, 8, 10, 8)
        drop_layout.setSpacing(3)
        drop_layout.setAlignment(Qt.AlignCenter)

        # Icon tải xuống nhỏ gọn, bo góc viền tím
        lbl_drop_icon = QLabel()
        lbl_drop_icon.setFixedSize(32, 32)
        lbl_drop_icon.setAlignment(Qt.AlignCenter)
        lbl_drop_icon.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(139, 92, 246, 0.25), stop:1 rgba(6, 182, 212, 0.18));
            border: 1px solid rgba(167, 139, 250, 0.35);
            border-radius: 8px;
        """)
        lbl_drop_icon.setPixmap(create_vector_icon("download", "#c4b5fd", 18))

        lbl_drop_txt = QLabel("Thả thư mục footage vào đây")
        lbl_drop_txt.setAlignment(Qt.AlignCenter)
        lbl_drop_txt.setStyleSheet("font-size: 12px; font-weight: 600; color: #f4f4f5;")

        lbl_drop_sub = QLabel("hoặc chọn nguồn bên dưới")
        lbl_drop_sub.setAlignment(Qt.AlignCenter)
        lbl_drop_sub.setStyleSheet("font-size: 10px; color: #71717a;")

        drop_layout.addWidget(lbl_drop_icon, 0, Qt.AlignCenter)
        drop_layout.addWidget(lbl_drop_txt, 0, Qt.AlignCenter)
        drop_layout.addWidget(lbl_drop_sub, 0, Qt.AlignCenter)
        l_vbox.addWidget(self.drop_frame)

        # 4 Nút Nguồn 2x2 với viền và icon màu sắc chuyên nghiệp
        src_grid = QGridLayout()
        src_grid.setSpacing(6)

        self.btn_browse_folder = QPushButton("📁 Thư mục")
        self.btn_browse_folder.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                padding: 7px 9px;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
                color: #e4e4e7;
            }
            QPushButton:hover {
                border-color: rgba(245, 158, 11, 0.5);
                background-color: rgba(245, 158, 11, 0.1);
                color: #ffffff;
            }
        """)
        self.btn_browse_folder.clicked.connect(self._browse_folder)

        self.btn_browse_files = QPushButton("🎬 Tệp video")
        self.btn_browse_files.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                padding: 7px 9px;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
                color: #e4e4e7;
            }
            QPushButton:hover {
                border-color: rgba(167, 139, 250, 0.5);
                background-color: rgba(167, 139, 250, 0.1);
                color: #ffffff;
            }
        """)
        self.btn_browse_files.clicked.connect(self._browse_file)

        self.btn_from_davinci = QPushButton("🎞️ Từ DaVinci")
        self.btn_from_davinci.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                padding: 7px 9px;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
                color: #e4e4e7;
            }
            QPushButton:hover {
                border-color: rgba(56, 189, 248, 0.5);
                background-color: rgba(56, 189, 248, 0.1);
                color: #ffffff;
            }
        """)
        self.btn_from_davinci.clicked.connect(self._auto_detect_video)

        self.btn_google_drive = QPushButton("☁️ Google Drive")
        self.btn_google_drive.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                padding: 7px 9px;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
                color: #e4e4e7;
            }
            QPushButton:hover {
                border-color: rgba(52, 211, 153, 0.5);
                background-color: rgba(52, 211, 153, 0.1);
                color: #ffffff;
            }
        """)
        self.btn_google_drive.clicked.connect(self._open_google_drive_dialog)

        src_grid.addWidget(self.btn_browse_folder, 0, 0)
        src_grid.addWidget(self.btn_browse_files, 0, 1)
        src_grid.addWidget(self.btn_from_davinci, 1, 0)
        src_grid.addWidget(self.btn_google_drive, 1, 1)
        l_vbox.addLayout(src_grid)

        # Dự án & Bộ nhớ đệm (Cache)
        proj_box = QFrame()
        proj_box.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; border-radius: 10px; padding: 10px;")
        proj_layout = QVBoxLayout(proj_box)
        proj_layout.setSpacing(6)

        proj_top = QHBoxLayout()
        lbl_p_icon = QLabel("📁")
        self.lbl_proj_name = QLabel("Chưa chọn dự án")
        self.lbl_proj_name.setStyleSheet("font-weight: bold; color: #a1a1aa; font-size: 12px;")
        proj_top.addWidget(lbl_p_icon)
        proj_top.addWidget(self.lbl_proj_name, stretch=1)
        proj_layout.addLayout(proj_top)

        self.lbl_proj_meta = QLabel("Chọn thư mục hoặc tệp video bên trên")
        self.lbl_proj_meta.setStyleSheet("color: #71717a; font-size: 11px;")
        proj_layout.addWidget(self.lbl_proj_meta)

        cache_row = QHBoxLayout()
        lbl_c_title = QLabel("Bộ nhớ đệm")
        lbl_c_title.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        self.lbl_cache_badge = QLabel("0 / 0 clip")
        self.lbl_cache_badge.setStyleSheet("color: #22d3ee; font-size: 11px; font-weight: bold; background-color: rgba(6,182,212,0.15); padding: 2px 7px; border-radius: 6px;")
        cache_row.addWidget(lbl_c_title)
        cache_row.addStretch()
        cache_row.addWidget(self.lbl_cache_badge)
        proj_layout.addLayout(cache_row)

        self.cache_progress_bar = QProgressBar()
        self.cache_progress_bar.setFixedHeight(6)
        self.cache_progress_bar.setTextVisible(False)
        self.cache_progress_bar.setValue(0)
        self.cache_progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #27272a;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #06b6d4;
                border-radius: 3px;
            }
        """)
        proj_layout.addWidget(self.cache_progress_bar)

        self.lbl_cache_hint = QLabel("⚡ Tự động quét & nạp cache khi bấm [Bắt đầu dựng]")
        self.lbl_cache_hint.setStyleSheet("color: #71717a; font-size: 10px; margin-top: 3px; line-height: 1.3;")
        self.lbl_cache_hint.setWordWrap(True)
        proj_layout.addWidget(self.lbl_cache_hint)

        # Cấu hình Mô hình Whisper AI Local & Ngôn ngữ
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        lbl_model_tag = QLabel("Mô hình AI:")
        lbl_model_tag.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        model_row.addWidget(lbl_model_tag)

        self.combo_model = QComboBox()
        self.combo_model.addItems(["small", "base", "tiny", "medium", "large-v3"])
        self.combo_model.setCurrentText("small")
        self.combo_model.setStyleSheet("""
            QComboBox {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
        """)
        self.combo_model.setToolTip("Mô hình Faster-Whisper Local (small: Khuyên dùng cho Tiếng Việt)")
        model_row.addWidget(self.combo_model, stretch=1)

        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["vi", "en", "auto"])
        self.combo_lang.setCurrentText("vi")
        self.combo_lang.setStyleSheet("""
            QComboBox {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
        """)
        self.combo_lang.setToolTip("Ngôn ngữ nhận diện (vi: Tiếng Việt, en: English, auto: Tự động)")
        model_row.addWidget(self.combo_lang)
        proj_layout.addLayout(model_row)

        self.combo_model.currentIndexChanged.connect(self._check_project_cache_status)
        self.combo_lang.currentIndexChanged.connect(self._check_project_cache_status)

        # Giữ biến btn_scan_only ẩn để tương thích ngược với các slot cũ
        self.btn_scan_only = QPushButton()
        self.btn_scan_only.setVisible(False)

        l_vbox.addWidget(proj_box)

        # Danh sách Chương
        chap_head = QHBoxLayout()
        lbl_chaps = QLabel("Chương")
        lbl_chaps.setStyleSheet("font-weight: bold; color: #a1a1aa; font-size: 12px;")
        chap_head.addWidget(lbl_chaps)
        chap_head.addStretch()
        btn_sort_chap = QPushButton("Sắp xếp theo giờ quay")
        btn_sort_chap.setStyleSheet("background: transparent; color: #71717a; border: none; font-size: 11px;")
        chap_head.addWidget(btn_sort_chap)
        l_vbox.addLayout(chap_head)

        self.list_chapters = QListWidget()
        self.list_chapters.setMinimumHeight(160)
        self.list_chapters.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_chapters.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_chapters.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                color: #d4d4d8;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:hover {
                background-color: #18181b;
            }
            QListWidget::item:selected {
                background-color: #27272a;
                color: #fff;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 5px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #3f3f46;
                border-radius: 2px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #71717a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.list_chapters.addItem("⚪ (Chưa có danh sách chương)")
        l_vbox.addWidget(self.list_chapters, stretch=1)

        # Đặt a_left vào QScrollArea để hỗ trợ cuộn mượt mà trên mọi độ phân giải màn hình
        a_left_scroll = QScrollArea()
        a_left_scroll.setFixedWidth(300)
        a_left_scroll.setWidgetResizable(True)
        a_left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        a_left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        a_left_scroll.setFrameShape(QFrame.NoFrame)
        a_left_scroll.setStyleSheet("""
            QScrollArea {
                background-color: #111113;
                border-right: 1px solid #27272a;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #3f3f46;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #71717a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        a_left_scroll.setWidget(a_left)
        workspace_layout.addWidget(a_left_scroll)

        # -------------------------------------------------------------------------
        # CỘT GIỮA (Expanding): Stepper, Presets, Thiết lập nhanh, Toggles, Động cơ AI
        # -------------------------------------------------------------------------
        a_center = QWidget()
        a_center.setStyleSheet("background-color: #09090b;")
        c_outer = QVBoxLayout(a_center)
        c_outer.setContentsMargins(0, 0, 0, 0)
        c_outer.setSpacing(0)

        # Vùng cuộn ScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #09090b;")
        c_vbox = QVBoxLayout(scroll_content)
        c_vbox.setContentsMargins(28, 24, 28, 24)
        c_vbox.setSpacing(20)

        # Stepper
        stepper_layout = QHBoxLayout()
        stepper_layout.setSpacing(10)
        s1 = QLabel("✓ 1. Nguồn")
        s1.setStyleSheet("color: #86efac; font-weight: bold; font-size: 12px;")
        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setStyleSheet("color: #3f3f46;")
        line1.setFixedWidth(50)
        s2 = QLabel("● 2. Kiểu dựng")
        s2.setStyleSheet("color: #a78bfa; font-weight: bold; font-size: 12px;")
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color: #27272a;")
        line2.setFixedWidth(50)
        s3 = QLabel("○ 3. Chạy và xuất")
        s3.setStyleSheet("color: #71717a; font-weight: bold; font-size: 12px;")

        stepper_layout.addWidget(s1)
        stepper_layout.addWidget(line1)
        stepper_layout.addWidget(s2)
        stepper_layout.addWidget(line2)
        stepper_layout.addWidget(s3)
        stepper_layout.addStretch()
        c_vbox.addLayout(stepper_layout)

        # Tiêu đề
        lbl_h2 = QLabel("Bạn muốn dựng video kiểu gì?")
        lbl_h2.setStyleSheet("font-size: 19px; font-weight: bold; color: #f4f4f5;")
        c_vbox.addWidget(lbl_h2)
        lbl_sub = QLabel("Chọn một kiểu, app tự bật bộ tính năng phù hợp. Muốn chỉnh từng thông số thì mở phần nâng cao bên dưới.")
        lbl_sub.setStyleSheet("font-size: 12px; color: #71717a;")
        c_vbox.addWidget(lbl_sub)

        # Hidden Combo để tương thích backend
        self.combo_workflow = QComboBox()
        self.combo_workflow.addItem("Vlog có Hook", "vlog")
        self.combo_workflow.addItem("Podcast, Phỏng vấn", "podcast")
        self.combo_workflow.addItem("Shorts, TikTok 9:16", "shorts")
        self.combo_workflow.addItem("Tùy chỉnh", "adv")
        self.combo_workflow.currentIndexChanged.connect(self._on_workflow_mode_changed)
        self.combo_workflow.hide()
        c_vbox.addWidget(self.combo_workflow)

        # 4 Thẻ Preset Cards (Grid 4 cột)
        self.presets_grid = QGridLayout()
        self.presets_grid.setSpacing(10)

        self.preset_cards = []
        card_data = [
            ("🎙️", "Podcast, phỏng vấn", "Cắt khoảng lặng, lọc câu vấp, phụ đề viền thanh lịch", 1),
            ("📱", "Shorts, TikTok 9:16", "Cắt viral 60s, reframe dọc, karaoke sub, tự chèn SFX", 2),
            ("🎬", "Vlog có Hook", "Teaser mở đầu, punch-in, B-roll, tua nhanh khoảng lặng", 0),
            ("🎛️", "Tự chỉnh", "Mở toàn bộ 6 nhóm thông số để tinh chỉnh tay", 3),
        ]

        def make_preset_card(icon, title, desc, idx):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setChecked(idx == 0) # Vlog default
            btn.setFixedHeight(105)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid #27272a;
                    border-radius: 10px;
                    background-color: #18181b;
                    padding: 10px;
                    text-align: left;
                }
                QPushButton:hover {
                    border-color: #3f3f46;
                }
                QPushButton:checked {
                    border-color: #a78bfa;
                    background-color: rgba(139,92,246,0.12);
                }
            """)
            b_lay = QVBoxLayout(btn)
            b_lay.setContentsMargins(10, 8, 10, 8)
            b_lay.setSpacing(4)

            top_row = QHBoxLayout()
            lbl_ic = QLabel(icon)
            lbl_ic.setStyleSheet("font-size: 16px;")
            top_row.addWidget(lbl_ic)
            top_row.addStretch()
            lbl_ck = QLabel("✓" if idx == 0 else "○")
            lbl_ck.setStyleSheet("color: #a78bfa; font-weight: bold;")
            top_row.addWidget(lbl_ck)
            b_lay.addLayout(top_row)

            lbl_t = QLabel(title)
            lbl_t.setStyleSheet("font-weight: bold; font-size: 12px; color: #f4f4f5;")
            b_lay.addWidget(lbl_t)

            lbl_d = QLabel(desc)
            lbl_d.setWordWrap(True)
            lbl_d.setStyleSheet("font-size: 10px; color: #71717a;")
            b_lay.addWidget(lbl_d)

            def on_click():
                for other_btn, other_idx, other_ck, other_t in self.preset_cards:
                    is_this = (other_btn == btn)
                    other_btn.setChecked(is_this)
                    other_ck.setText("✓" if is_this else "○")
                self.combo_workflow.setCurrentIndex(idx)
                self._apply_preset_features(idx, title)

            btn.clicked.connect(on_click)
            self.preset_cards.append((btn, idx, lbl_ck, title))
            return btn

        for i, (ic, t, d, idx) in enumerate(card_data):
            c_btn = make_preset_card(ic, t, d, idx)
            self.presets_grid.addWidget(c_btn, 0, i)

        c_vbox.addLayout(self.presets_grid)

        # Khối: Thiết lập nhanh
        c_vbox.addWidget(QLabel("<b>Thiết lập nhanh</b>"))
        quick_grid = QGridLayout()
        quick_grid.setSpacing(12)

        # Footage Type
        quick_grid.addWidget(QLabel("Loại footage:"), 0, 0)
        seg_footage = QHBoxLayout()
        self.footage_buttons = {}
        for ft_name in ["Tự nhận diện", "Nói liên tục", "Hỗn hợp", "Du lịch"]:
            btn_ft = QPushButton(ft_name)
            btn_ft.setCheckable(True)
            btn_ft.setChecked(ft_name == "Du lịch")
            btn_ft.setCursor(Qt.PointingHandCursor)
            btn_ft.setStyleSheet("""
                QPushButton { background-color: #18181b; border: 1px solid #27272a; border-radius: 4px; padding: 4px 8px; font-size: 11px; color: #a1a1aa; }
                QPushButton:hover { border-color: #3f3f46; color: #fff; }
                QPushButton:checked { background-color: #8b5cf6; border-color: #a78bfa; color: #fff; font-weight: bold; }
            """)
            btn_ft.clicked.connect(lambda checked=False, name=ft_name: self._set_quick_footage(name))
            self.footage_buttons[ft_name] = btn_ft
            seg_footage.addWidget(btn_ft)
        quick_grid.addLayout(seg_footage, 0, 1)

        # Nhịp dựng
        quick_grid.addWidget(QLabel("Nhịp dựng:"), 1, 0)
        seg_pacing = QHBoxLayout()
        self.pacing_buttons = {}
        for pc_name in ["Thong thả", "Cân bằng", "Nhanh"]:
            btn_pc = QPushButton(pc_name)
            btn_pc.setCheckable(True)
            btn_pc.setChecked(pc_name == "Cân bằng")
            btn_pc.setCursor(Qt.PointingHandCursor)
            btn_pc.setStyleSheet("""
                QPushButton { background-color: #18181b; border: 1px solid #27272a; border-radius: 4px; padding: 4px 8px; font-size: 11px; color: #a1a1aa; }
                QPushButton:hover { border-color: #3f3f46; color: #fff; }
                QPushButton:checked { background-color: #8b5cf6; border-color: #a78bfa; color: #fff; font-weight: bold; }
            """)
            btn_pc.clicked.connect(lambda checked=False, name=pc_name: self._set_quick_pacing(name))
            self.pacing_buttons[pc_name] = btn_pc
            seg_pacing.addWidget(btn_pc)
        quick_grid.addLayout(seg_pacing, 1, 1)

        # Mức độ cắt vấp (Slider)
        self.lbl_master_intensity = QLabel("Mức độ cắt vấp & im lặng: VỪA (Cân bằng)")
        self.lbl_master_intensity.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        quick_grid.addWidget(self.lbl_master_intensity, 2, 0)
        self.slide_master_intensity = QSlider(Qt.Horizontal)
        self.slide_master_intensity.setRange(1, 3)
        self.slide_master_intensity.setValue(2)
        self.slide_master_intensity.setStyleSheet("""
            QSlider::groove:horizontal { height: 6px; background: #27272a; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #8b5cf6; border-radius: 3px; }
            QSlider::handle:horizontal { background: #fff; width: 14px; margin-top: -4px; margin-bottom: -4px; border-radius: 7px; }
        """)
        self.slide_master_intensity.valueChanged.connect(self._on_master_intensity_changed)
        quick_grid.addWidget(self.slide_master_intensity, 2, 1)

        c_vbox.addLayout(quick_grid)

        # Khối: Tính năng đang bật (Toggles 3x2)
        c_vbox.addWidget(QLabel("<b>Tính năng đang bật</b>"))
        tog_grid = QGridLayout()
        tog_grid.setSpacing(10)

        self.check_hook = QCheckBox("Hook mở đầu (3 câu, 5s)")
        self.check_punch_in = QCheckBox("Punch-in (Zoom 1.15x)")
        self.check_broll = QCheckBox("Gợi ý B-roll, meme (Max 4)")
        self.check_sfx = QCheckBox("Tự chèn SFX (Cách 15s)")
        self.check_reframe = QCheckBox("Reframe 9:16 (Bám mặt)")
        self.check_subtitle = QCheckBox("Phụ đề (Karaoke Pop)")
        self.check_cut = QCheckBox("Cắt khoảng lặng")

        all_chks = [self.check_hook, self.check_punch_in, self.check_broll,
                    self.check_sfx, self.check_reframe, self.check_subtitle]

        for i, chk in enumerate(all_chks):
            chk.setChecked(True)
            chk.setStyleSheet("""
                QCheckBox {
                    background-color: #18181b;
                    border: 1px solid #27272a;
                    border-radius: 8px;
                    padding: 8px;
                    font-size: 12px;
                    color: #f4f4f5;
                }
                QCheckBox:hover { border-color: #3f3f46; }
                QCheckBox::indicator { width: 16px; height: 16px; }
            """)
            tog_grid.addWidget(chk, i // 3, i % 3)

        c_vbox.addLayout(tog_grid)

        # Banner chuyển bước mượt mà sang giai đoạn AI
        self.step_transition_box = QFrame()
        self.step_transition_box.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(139,92,246,0.18), stop:1 rgba(6,182,212,0.18));
                border: 1px solid rgba(139,92,246,0.35);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        trans_lay = QHBoxLayout(self.step_transition_box)
        trans_lay.setContentsMargins(12, 8, 12, 8)

        info_vbox = QVBoxLayout()
        info_vbox.setSpacing(2)
        self.lbl_active_preset_name = QLabel("👉 Đã chọn phong cách: <b style='color:#c4b5fd;'>Vlog có Hook</b>")
        self.lbl_active_preset_name.setStyleSheet("font-size: 13px; color: #f4f4f5;")
        lbl_trans_sub = QLabel("Chuyển sang cấu hình Động cơ AI (Web Prompt / Cloud AI / Local) hoặc dán JSON kịch bản.")
        lbl_trans_sub.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        info_vbox.addWidget(self.lbl_active_preset_name)
        info_vbox.addWidget(lbl_trans_sub)
        trans_lay.addLayout(info_vbox, stretch=1)

        self.btn_goto_ai = QPushButton("⚡ Chọn Động cơ AI ➔")
        self.btn_goto_ai.setCursor(Qt.PointingHandCursor)
        self.btn_goto_ai.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #06b6d4);
                color: #ffffff;
                font-weight: bold;
                padding: 9px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a78bfa, stop:1 #22d3ee);
            }
        """)
        trans_lay.addWidget(self.btn_goto_ai)
        c_vbox.addWidget(self.step_transition_box)

        # =========================================================================
        # TÙY CHỈNH NÂNG CAO (ACCORDION & ĐỘNG CƠ AI ĐẦY ĐỦ)
        # =========================================================================
        acc_box = QFrame()
        acc_box.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; border-radius: 10px;")
        acc_layout = QVBoxLayout(acc_box)
        acc_layout.setContentsMargins(0, 0, 0, 0)
        acc_layout.setSpacing(0)

        def make_acc_row(icon, title, desc, on_click_fn=None):
            btn = QPushButton()
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #18181b;
                    border: none;
                    border-bottom: 1px solid #27272a;
                    padding: 8px 14px;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: #27272a;
                }
            """)
            r_lay = QHBoxLayout(btn)
            r_lay.setContentsMargins(0, 0, 0, 0)
            lbl_i = QLabel(icon)
            lbl_i.setStyleSheet("font-size: 14px;")
            lbl_t = QLabel(title)
            lbl_t.setStyleSheet("font-weight: 500; font-size: 12px; color: #f4f4f5;")
            lbl_d = QLabel(desc)
            lbl_d.setStyleSheet("color: #71717a; font-size: 12px;")
            r_lay.addWidget(lbl_i)
            r_lay.addWidget(lbl_t)
            r_lay.addStretch()
            r_lay.addWidget(lbl_d)
            if on_click_fn:
                btn.clicked.connect(on_click_fn)
            return btn

        def on_click_silence():
            self.txt_console.appendPlainText("✂️ [Cắt khoảng lặng]: Đã chọn mức độ dB và tua nhanh. Có thể điều chỉnh thanh trượt ở trên.")

        def on_click_director():
            self.txt_console.appendPlainText("🤖 [Đạo diễn AI]: Làm sạch thoại, lọc câu lặp và sắp xếp phân cảnh theo ý đồ.")

        def on_click_intent():
            self.txt_console.appendPlainText("📑 [Mạch kịch bản]: Giữ trọn mạch thời gian hoặc dồn câu đắt giá lên đầu.")

        def on_click_subtitles():
            self.txt_console.appendPlainText("📝 [Phụ đề]: Sẵn sàng sinh phụ đề động bám âm từng từ.")

        acc_layout.addWidget(make_acc_row("✂️", "Cắt khoảng lặng", "-30 dB · tối thiểu 0.6s · tua 8x", on_click_silence))
        acc_layout.addWidget(make_acc_row("🤖", "Đạo diễn AI", "Clean talk · lọc vấp · lọc lặp", on_click_director))
        acc_layout.addWidget(make_acc_row("📑", "Mạch kịch bản", "Giữ thứ tự thời gian", on_click_intent))
        acc_layout.addWidget(make_acc_row("📝", "Phụ đề", "Theo từ · tối đa 6 từ/dòng", on_click_subtitles))

        # Hàng Động cơ AI (Có thể bấm để bung ra)
        btn_acc_engine = QPushButton("⚡  Động cơ AI (Chọn Web / Cloud / Local)  ▾")
        btn_acc_engine.setStyleSheet("""
            QPushButton {
                background-color: #1f1f23;
                border: none;
                border-top: 1px solid #27272a;
                color: #c4b5fd;
                font-weight: bold;
                font-size: 12px;
                padding: 10px 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #27272a;
            }
        """)
        acc_layout.addWidget(btn_acc_engine)

        # Khung cấu hình Động cơ AI (Expandable Panel)
        self.ai_engine_panel = QFrame()
        self.ai_engine_panel.setStyleSheet("background-color: #111113; padding: 14px;")
        ai_panel_layout = QVBoxLayout(self.ai_engine_panel)
        ai_panel_layout.setSpacing(12)

        # 3 Nút chọn chế độ AI
        mode_btn_layout = QHBoxLayout()
        self.btn_mode_web = QPushButton("📋 Prompt Web (Miễn phí)")
        self.btn_mode_cloud = QPushButton("☁️ Cloud API (OpenAI/Gemini)")
        self.btn_mode_ollama = QPushButton("💻 Local AI (Ollama)")

        self.btn_mode_web.setCheckable(True)
        self.btn_mode_cloud.setCheckable(True)
        self.btn_mode_ollama.setCheckable(True)
        self.btn_mode_web.setChecked(True)

        mode_style = """
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                color: #a1a1aa;
            }
            QPushButton:checked {
                background-color: #8b5cf6;
                color: #fff;
                font-weight: bold;
                border-color: #a78bfa;
            }
        """
        self.btn_mode_web.setStyleSheet(mode_style)
        self.btn_mode_cloud.setStyleSheet(mode_style)
        self.btn_mode_ollama.setStyleSheet(mode_style)

        mode_btn_layout.addWidget(self.btn_mode_web)
        mode_btn_layout.addWidget(self.btn_mode_cloud)
        mode_btn_layout.addWidget(self.btn_mode_ollama)
        ai_panel_layout.addLayout(mode_btn_layout)

        # Stacked Pages cho 3 chế độ
        self.ai_stack = QStackedWidget()

        # Page 0: Prompt Web
        p_web = QWidget()
        pw_lay = QVBoxLayout(p_web)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.setSpacing(8)

        lbl_step1 = QLabel("1. Nhấn nút dưới đây để copy toàn bộ Transcript & Prompt gửi cho ChatGPT / Claude:")
        lbl_step1.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        pw_lay.addWidget(lbl_step1)

        self.btn_quick_copy = QPushButton("📋 1. Copy Prompt Đạo Diễn (Kèm Dữ Liệu Video)")
        self.btn_quick_copy.setStyleSheet("background-color: #27272a; border: 1px solid #3f3f46; color: #f4f4f5; padding: 8px; border-radius: 6px; font-weight: bold;")
        pw_lay.addWidget(self.btn_quick_copy)

        lbl_step2 = QLabel("2. Dán mã JSON kịch bản mà AI trả về vào đây:")
        lbl_step2.setStyleSheet("color: #a1a1aa; font-size: 12px; margin-top: 6px;")
        pw_lay.addWidget(lbl_step2)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText("Dán JSON kịch bản vào đây (bắt đầu bằng { 'timeline': ... })...")
        self.txt_json_input.setFixedHeight(90)
        self.txt_json_input.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; border-radius: 6px; color: #22d3ee; font-family: monospace; font-size: 11px;")
        pw_lay.addWidget(self.txt_json_input)

        self.lbl_json_hint = QLabel("💡 Dán JSON kịch bản vào ô trên · Nút [Bắt đầu dựng] bên dưới sẽ tự động kích hoạt")
        self.lbl_json_hint.setStyleSheet("color: #71717a; font-size: 11px; margin-top: 2px;")
        self.lbl_json_hint.setWordWrap(True)
        pw_lay.addWidget(self.lbl_json_hint)

        self.btn_apply_json = QPushButton("🎬 Dựng Theo Kịch Bản Này [Bắt đầu dựng]")
        self.btn_apply_json.setStyleSheet("background-color: #06b6d4; color: #000; font-weight: bold; padding: 7px; border-radius: 6px;")
        pw_lay.addWidget(self.btn_apply_json)
        self.ai_stack.addWidget(p_web)

        # Page 1: Cloud API
        p_cloud = QWidget()
        pc_lay = QVBoxLayout(p_cloud)
        pc_lay.setContentsMargins(0, 0, 0, 0)
        pc_lay.setSpacing(8)

        row_c1 = QHBoxLayout()
        row_c1.addWidget(QLabel("Nhà cung cấp:"))
        self.combo_cloud_provider = QComboBox()
        self.combo_cloud_provider.addItems(["Google Gemini 1.5 Pro", "OpenAI GPT-4o", "Anthropic Claude 3.5 Sonnet"])
        self.combo_cloud_provider.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; padding: 6px; border-radius: 4px; color: #fff;")
        row_c1.addWidget(self.combo_cloud_provider, stretch=1)
        pc_lay.addLayout(row_c1)

        row_c2 = QHBoxLayout()
        row_c2.addWidget(QLabel("API Key:"))
        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        self.txt_api_key.setPlaceholderText("sk-...")
        self.txt_api_key.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; padding: 6px; border-radius: 4px; color: #fff;")
        row_c2.addWidget(self.txt_api_key, stretch=1)
        pc_lay.addLayout(row_c2)

        self.btn_run_api = QPushButton("⚡ 1-Click: Cloud AI Tự Động Lên Kịch Bản & Dựng")
        self.btn_run_api.setStyleSheet("background-color: #8b5cf6; color: #fff; font-weight: bold; padding: 8px; border-radius: 6px;")
        pc_lay.addWidget(self.btn_run_api)
        self.ai_stack.addWidget(p_cloud)

        # Page 2: Local AI Ollama
        p_ollama = QWidget()
        po_lay = QVBoxLayout(p_ollama)
        po_lay.setContentsMargins(0, 0, 0, 0)
        po_lay.setSpacing(8)

        row_o1 = QHBoxLayout()
        row_o1.addWidget(QLabel("Mô hình Ollama:"))
        self.combo_ollama_model = QComboBox()
        self.combo_ollama_model.addItems(["llama3:latest", "qwen2.5:7b", "mistral:latest"])
        self.combo_ollama_model.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; padding: 6px; border-radius: 4px; color: #fff;")
        row_o1.addWidget(self.combo_ollama_model, stretch=1)
        po_lay.addLayout(row_o1)

        self.btn_run_ollama = QPushButton("💻 1-Click: Local AI Tự Động Lên Kịch Bản & Dựng")
        self.btn_run_ollama.setStyleSheet("background-color: #8b5cf6; color: #fff; font-weight: bold; padding: 8px; border-radius: 6px;")
        po_lay.addWidget(self.btn_run_ollama)
        self.ai_stack.addWidget(p_ollama)

        def switch_mode(idx):
            self.btn_mode_web.setChecked(idx == 0)
            self.btn_mode_cloud.setChecked(idx == 1)
            self.btn_mode_ollama.setChecked(idx == 2)
            self.ai_stack.setCurrentIndex(idx)

        self.btn_mode_web.clicked.connect(lambda: switch_mode(0))
        self.btn_mode_cloud.clicked.connect(lambda: switch_mode(1))
        self.btn_mode_ollama.clicked.connect(lambda: switch_mode(2))

        self.btn_quick_copy.clicked.connect(self._quick_copy_copilot_prompt)
        self.btn_apply_json.clicked.connect(self._toggle_pipeline_execution)
        self.txt_json_input.textChanged.connect(self._on_json_text_changed)
        self.btn_run_ollama.clicked.connect(lambda: self._run_local_ollama_pipeline("travel_vlog", self.combo_ollama_model.currentText(), "http://localhost:11434"))
        self.btn_run_api.clicked.connect(lambda: self._run_cloud_api_pipeline("travel_vlog", self.combo_cloud_provider.currentText(), self.txt_api_key.text()))

        ai_panel_layout.addWidget(self.ai_stack)
        acc_layout.addWidget(self.ai_engine_panel)

        # Toggle Expand/Collapse cho panel Động cơ AI
        def toggle_engine():
            vis = self.ai_engine_panel.isVisible()
            self.ai_engine_panel.setVisible(not vis)
            btn_acc_engine.setText("⚡  Động cơ AI (Chọn Web / Cloud / Local)  " + ("▾" if not vis else "▴"))

        btn_acc_engine.clicked.connect(toggle_engine)

        c_vbox.addWidget(acc_box)
        c_vbox.addStretch()

        scroll.setWidget(scroll_content)
        c_outer.addWidget(scroll, stretch=1)

        # Runbar ở chân cột giữa
        runbar = QFrame()
        runbar.setFixedHeight(56)
        runbar.setStyleSheet("background-color: #111113; border-top: 1px solid #27272a; padding: 0 20px;")
        rb_lay = QHBoxLayout(runbar)
        rb_lay.setContentsMargins(16, 0, 16, 0)
        rb_lay.setSpacing(12)

        self.btn_save_recipe = QPushButton("💾 Lưu recipe")
        self.btn_save_recipe.setStyleSheet("background: transparent; border: 1px solid #27272a; border-radius: 6px; padding: 6px 12px; color: #a1a1aa; font-size: 12px;")
        self.btn_save_recipe.clicked.connect(self._save_current_as_recipe)
        rb_lay.addWidget(self.btn_save_recipe)

        rb_lay.addStretch()

        self.combo_render_preset = QComboBox()
        self.combo_render_preset.addItems(["Xuất: FCPXML 1.9", "Xuất: FCP7 XML", "Xuất: EDL"])
        self.combo_render_preset.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; padding: 6px 10px; border-radius: 6px; color: #f4f4f5; font-size: 12px;")
        rb_lay.addWidget(self.combo_render_preset)

        btn_scan_mid = QPushButton("Chỉ quét nguồn")
        btn_scan_mid.setStyleSheet("background-color: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 8px 14px; color: #f4f4f5; font-weight: 500; font-size: 12px;")
        btn_scan_mid.clicked.connect(self._start_phase_1_scan)
        rb_lay.addWidget(btn_scan_mid)

        self.btn_run = QPushButton("▶ Bắt đầu dựng")
        self.btn_run.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #fff;
                font-weight: bold;
                font-size: 13px;
                padding: 8px 20px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #7c3aed;
            }
        """)
        self.btn_run.clicked.connect(self._toggle_pipeline_execution)
        rb_lay.addWidget(self.btn_run)

        c_outer.addWidget(runbar)
        workspace_layout.addWidget(a_center, stretch=1)

        # -------------------------------------------------------------------------
        # CỘT PHẢI (340px): Tiến trình, ETA, Các bước Pipeline, Stop, Nhật ký
        # -------------------------------------------------------------------------
        a_right = QFrame()
        a_right.setFixedWidth(340)
        a_right.setStyleSheet("background-color: #111113; border-left: 1px solid #27272a;")
        r_vbox = QVBoxLayout(a_right)
        r_vbox.setContentsMargins(18, 18, 18, 18)
        r_vbox.setSpacing(12)

        # Header Phần trăm lớn & Trạng thái
        pct_row = QHBoxLayout()
        self.lbl_pct_big = QLabel("0%")
        self.lbl_pct_big.setStyleSheet("font-size: 32px; font-weight: bold; color: #71717a;")
        self.lbl_eta = QLabel("Sẵn sàng")
        self.lbl_eta.setStyleSheet("color: #71717a; font-size: 12px; margin-top: 10px;")
        pct_row.addWidget(self.lbl_pct_big)
        pct_row.addStretch()
        pct_row.addWidget(self.lbl_eta)
        r_vbox.addLayout(pct_row)

        # Main Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #27272a;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #8b5cf6;
                border-radius: 3px;
            }
        """)
        r_vbox.addWidget(self.progress_bar)

        # Pipeline Steps (6 bước chuẩn theo bản thiết kế)
        self.step_tracker = PipelineStepTrackerWidget()
        self.step_progress = self.step_tracker
        self.step_names = [cfg["name"] for cfg in self.step_tracker.step_configs.values()]
        self.step_labels = [row["name"] for row in self.step_tracker.rows.values()]
        r_vbox.addWidget(self.step_tracker)

        # Nút Dừng
        self.btn_stop = QPushButton("⏹ Dừng tiến trình")
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #18181b;
                border: 1px solid rgba(239, 68, 68, 0.4);
                color: #f87171;
                border-radius: 6px;
                padding: 7px 12px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.15);
                border-color: #ef4444;
            }
        """)
        self.btn_stop.clicked.connect(self._stop_pipeline)
        self.btn_stop.setVisible(False)
        r_vbox.addWidget(self.btn_stop)

        # Nhật ký Console
        log_head = QHBoxLayout()
        lbl_log = QLabel("Nhật ký")
        lbl_log.setStyleSheet("font-weight: bold; color: #a1a1aa; font-size: 12px;")
        log_head.addWidget(lbl_log)
        log_head.addStretch()
        btn_copy_log = QPushButton("Sao chép")
        btn_copy_log.setStyleSheet("background: transparent; color: #71717a; border: none; font-size: 11px;")
        log_head.addWidget(btn_copy_log)
        r_vbox.addLayout(log_head)

        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
                color: #a1a1aa;
                font-family: monospace;
                font-size: 11px;
                padding: 6px;
            }
        """)
        r_vbox.addWidget(self.txt_console, stretch=1)

        workspace_layout.addWidget(a_right)
        window_layout.addWidget(workspace, stretch=1)

        # =========================================================================
        # 3. MINI TIMELINE (Dưới cùng: Chiều cao 178px)
        # =========================================================================
        tl_frame = QFrame()
        tl_frame.setFixedHeight(178)
        tl_frame.setStyleSheet("background-color: #09090b; border-top: 1px solid #27272a;")
        tl_layout = QVBoxLayout(tl_frame)
        tl_layout.setContentsMargins(16, 8, 16, 8)
        tl_layout.setSpacing(6)

        # Header Timeline (Tracks)
        tl_head = QHBoxLayout()
        lbl_tl_time = QLabel("Timeline 21:01")
        lbl_tl_time.setStyleSheet("font-weight: bold; color: #f4f4f5; font-size: 12px;")
        tl_head.addWidget(lbl_tl_time)
        tl_head.addSpacing(16)

        trk_info = [
            ("V2 B-roll", "#22d3ee"),
            ("V1 Footage", "#a78bfa"),
            ("A1 Thoại", "#86efac"),
            ("A2 SFX", "#fcd34d"),
            ("A3 Nhạc", "#f472b6"),
        ]
        for trk_name, trk_color in trk_info:
            lbl_trk = QLabel(f"■ {trk_name}")
            lbl_trk.setStyleSheet(f"color: {trk_color}; font-size: 11px; font-weight: 500;")
            tl_head.addWidget(lbl_trk)
            tl_head.addSpacing(8)

        tl_head.addStretch()
        tl_layout.addLayout(tl_head)

        # Body Mini Timeline Visualizer
        tl_canvas = QFrame()
        tl_canvas.setStyleSheet("background-color: #111113; border: 1px solid #1f1f23; border-radius: 6px;")
        tl_body_lay = QVBoxLayout(tl_canvas)
        tl_body_lay.setContentsMargins(8, 6, 8, 6)
        tl_body_lay.setSpacing(3)

        tracks = [
            ("#22d3ee", "V2: B-roll (Mèo khóc, Cái nịt...)"),
            ("#a78bfa", "V1: 14 clips (Hook -> Suối Tràn -> Thác)"),
            ("#86efac", "A1: Audio Thoại Clean"),
            ("#fcd34d", "A2: SFX Hits & Whooshes"),
            ("#f472b6", "A3: Nhạc Lofi Background"),
        ]
        for col, desc in tracks:
            lane = QFrame()
            lane.setFixedHeight(18)
            lane.setStyleSheet(f"background-color: rgba(255,255,255,0.03); border-left: 3px solid {col}; border-radius: 3px;")
            l_desc = QLabel(f" {desc}")
            l_desc.setStyleSheet(f"color: {col}; font-size: 10px;")
            lane_lay = QHBoxLayout(lane)
            lane_lay.setContentsMargins(4, 0, 0, 0)
            lane_lay.addWidget(l_desc)
            tl_body_lay.addWidget(lane)

        tl_layout.addWidget(tl_canvas, stretch=1)
        window_layout.addWidget(tl_frame)

        # =========================================================================
        # TƯƠNG THÍCH BACKEND (Giữ nguyên các tab ẩn và dummies)
        # =========================================================================
        self.tab_widget = QTabWidget()
        self.tab_copilot = TabCopilot()
        self.tab_autocut = TabAutoCut()
        self.tab_titles = TabAssets()
        self.tab_sfx = TabSFX()
        self.tab_export = TabExport()

        self.tab_widget.addTab(self.tab_copilot, "Kịch Bản AI")
        self.tab_widget.addTab(self.tab_autocut, "Auto Cut")
        self.tab_widget.addTab(self.tab_titles, "Chữ & Đồ Họa")
        self.tab_widget.addTab(self.tab_sfx, "SFX Soundboard")
        self.tab_widget.addTab(self.tab_export, "Polish & Export")

        self.combo_ai_engine = QComboBox()
        self.combo_ai_engine.addItems(["Prompt Web (Miễn phí)", "Cloud API (OpenAI/Gemini)", "Local (Ollama)"])

        if not hasattr(self, "combo_model"):
            self.combo_model = QComboBox()
            self.combo_model.addItems(["small", "base", "tiny", "medium", "large-v3"])
        if not hasattr(self, "combo_lang"):
            self.combo_lang = QComboBox()
            self.combo_lang.addItems(["vi", "en", "auto"])
        self.combo_llm_provider = QComboBox()
        self.btn_auto_resolve = QPushButton()
        self.btn_story_save = QPushButton()
        self.btn_scan_luts = QPushButton()
        self.combo_lut_scope = QComboBox()
        self.check_lut_skip_existing = QCheckBox()
        self.btn_s1_auto_resolve = QPushButton()
        self.btn_s1_drive = QPushButton()
        self.btn_stage1_main_scan = QPushButton()
        self.btn_s1_skip_stage2 = QPushButton()
        self.btn_back_to_stage1 = QPushButton()
        self.btn_instant_export = QPushButton()
        self.check_cache = QCheckBox()
        self.lbl_file = QLabel()

        self._bind_tab_delegates()

    def _bind_tab_delegates(self):
        # Forward modern AI widgets to the TabCopilot backend dummy
        self.tab_copilot.txt_json_input = self.txt_json_input
        self.tab_copilot.btn_quick_copy = self.btn_quick_copy

        """Liên kết các thuộc tính widget trên các Tab để duy trì tính tương thích 100%."""
        # Tab 1: Auto Cut Delegates
        self.banner_onboarding = self.tab_autocut.banner_onboarding
        self.group_wf = self.tab_autocut.group_wf
        self.combo_workflow = self.tab_autocut.combo_workflow
        self.group_recipe = self.tab_autocut.group_recipe
        self.combo_recipes = self.tab_autocut.combo_recipes
        self.btn_save_recipe = self.tab_autocut.btn_save_recipe
        self.btn_delete_recipe = self.tab_autocut.btn_delete_recipe
        self.group_master = self.tab_autocut.group_master
        self.slide_master_intensity = self.tab_autocut.slide_master_intensity
        self.combo_pacing = self.tab_autocut.combo_pacing
        self.combo_video_type = self.tab_autocut.combo_video_type
        self.check_hide_weak_subs = self.tab_autocut.check_hide_weak_subs
        self.check_scene_guard = self.tab_autocut.check_scene_guard
        self.check_fill_gaps = self.tab_autocut.check_fill_gaps
        self.combo_hook_total = self.tab_autocut.combo_hook_total
        self.combo_story_intent = self.tab_autocut.combo_story_intent
        self.combo_story_target = self.tab_autocut.combo_story_target
        self.txt_api_key = self.tab_autocut.txt_api_key
        self.check_repeats = self.tab_autocut.check_repeats
        self.lbl_master_intensity = self.tab_autocut.lbl_master_intensity
        self.group_timeline = self.tab_autocut.group_timeline
        self.mini_timeline = self.tab_autocut.mini_timeline
        self.btn_toggle_advanced = self.tab_autocut.btn_toggle_advanced
        self.advanced_container = self.tab_autocut.advanced_container
        self.group_ai = self.tab_autocut.group_ai
        # Giữ liên kết hai chiều cho các điều khiển Whisper ở Stage 1
        self.tab_autocut.combo_model = self.combo_model
        self.tab_autocut.combo_lang = self.combo_lang
        self.tab_autocut.check_cache = self.check_cache
        self.tab_autocut.check_fill_gaps = self.check_fill_gaps
        self.group_director = self.tab_autocut.group_director
        self.combo_ai_mode = self.tab_autocut.combo_ai_mode
        self.check_bad_takes = self.tab_autocut.check_bad_takes
        self.check_punch_in = self.tab_autocut.check_punch_in
        self.txt_confidence_threshold = self.tab_autocut.txt_confidence_threshold
        self.group_vlog_hook = self.tab_autocut.group_vlog_hook
        self.check_vlog_hook = self.tab_autocut.check_vlog_hook
        self.combo_hook_dur = self.tab_autocut.combo_hook_dur
        self.group_v4 = self.tab_autocut.group_v4
        self.check_reframe = self.tab_autocut.check_reframe
        self.check_broll = self.tab_autocut.check_broll
        self.check_sfx = self.tab_autocut.check_sfx
        self.group_cut = self.tab_autocut.group_cut
        self.check_cut = self.tab_autocut.check_cut
        self.check_speedup = self.tab_autocut.check_speedup
        self.slide_db = self.tab_autocut.slide_db
        self.lbl_db = self.tab_autocut.lbl_db
        self.slide_dur = self.tab_autocut.slide_dur
        self.lbl_dur = self.tab_autocut.lbl_dur
        self.combo_audio_track = self.tab_autocut.combo_audio_track

        # Tab 2: Titles Delegates
        self.group_sub = self.tab_titles.group_presets
        self.check_subtitle = self.tab_titles.check_subtitle
        self.combo_text_preset = self.tab_titles.combo_text_preset
        self.btn_preview_preset = self.tab_titles.btn_preview_preset
        self.btn_save_custom_preset = self.tab_titles.btn_save_custom_preset
        self.combo_split_mode = self.tab_titles.combo_split_mode
        self.txt_split_limit = self.tab_titles.txt_split_limit
        self.txt_font = self.tab_titles.txt_font
        self.txt_size = self.tab_titles.txt_size
        self.txt_color = self.tab_titles.txt_color
        self.preview_lbl = self.tab_titles.preview_lbl
        self.txt_single_title = self.tab_titles.txt_single_title
        self.slide_title_dur = self.tab_titles.slide_title_dur
        self.btn_insert_title_playhead = self.tab_titles.btn_insert_title_playhead
        self.btn_install_presets = self.tab_titles.btn_install_presets
        self.btn_copy_fusion = self.tab_titles.btn_copy_fusion

        # Studio Asset Signals & Provider Wiring
        self.tab_titles.set_preset_provider(self._get_current_active_preset)
        self.tab_titles.insert_title_requested.connect(self._on_insert_title_at_playhead)
        self.tab_titles.install_presets_requested.connect(
            lambda: self.txt_console.appendPlainText("🚀 [Effects Library] Đã cài đặt 7 Presets Fusion Text+ vào DaVinci Resolve!") if hasattr(self, "txt_console") else None
        )
        self.tab_titles.copy_fusion_node_requested.connect(
            lambda pid: self.txt_console.appendPlainText(f"📋 [Clipboard] Đã copy Fusion Node của preset '{pid}'. Hãy chuyển sang DaVinci Resolve và ấn Ctrl+V để chèn!") if hasattr(self, "txt_console") else None
        )
        self.tab_titles.apply_lut_requested.connect(self._on_apply_lut_to_timeline)
        self.tab_titles.insert_sfx_requested.connect(self._on_insert_sfx_at_playhead)

        # Tab 3: SFX & Audio Enhancer Delegates
        self.combo_sfx_track = self.tab_sfx.combo_target_track
        self.slide_volume_offset = self.tab_sfx.slide_volume_offset
        self.btn_insert_sfx_playhead = self.tab_sfx.btn_insert_playhead
        self.check_loudnorm = self.tab_sfx.check_loudnorm
        self.combo_loudnorm_preset = self.tab_sfx.combo_loudnorm_preset

        # Tab 4: Export Delegates
        self.combo_lut = self.tab_export.combo_lut
        self.btn_apply_lut = self.tab_export.btn_apply_lut
        self.combo_render_preset = self.tab_export.combo_render_preset
        self.btn_start_render = self.tab_export.btn_start_render


    def _get_current_active_preset(self) -> Optional[TextStylePreset]:
        """Lấy đối tượng TextStylePreset hiện tại từ giao diện."""
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        preset = self.preset_mgr.get_preset(preset_id)
        if not preset:
            return None
        try:
            sz = int(self.txt_size.text())
        except (ValueError, AttributeError):
            sz = preset.size
        font_name = self.txt_font.text().strip() if hasattr(self, "txt_font") and self.txt_font.text().strip() else preset.font
        color_val = self.txt_color.text().strip() if hasattr(self, "txt_color") and self.txt_color.text().strip() else preset.standard_color
        return TextStylePreset(
            id=preset.id,
            name=preset.name,
            font=font_name,
            size=sz,
            weight=preset.weight,
            standard_color=color_val,
            highlight_color=preset.highlight_color,
            outline_color=preset.outline_color,
            outline_width=preset.outline_width,
            animation=preset.animation,
            timing_curve=preset.timing_curve,
            position_y_16_9=preset.position_y_16_9,
            position_y_9_16=preset.position_y_9_16,
            box_color=preset.box_color,
            glow_color=preset.glow_color,
            gradient_colors=preset.gradient_colors
        )

    def _on_insert_title_at_playhead(self, text: str, preset_id: str, duration_sec: float):
        """Chèn tiêu đề / Text+ preset tại vị trí Playhead trên Timeline Resolve."""
        resolve_auto = ResolveAutomation()
        font_name = self.txt_font.text().strip() or "Arial"
        try:
            sz = int(self.txt_size.text())
        except Exception:
            sz = 48
        color_val = self.txt_color.text().strip() or "#FFFFFF"
        resolve_auto.insert_title_at_playhead(
            text=text,
            preset_id=preset_id,
            duration_sec=duration_sec,
            font_name=font_name,
            font_size=sz,
            color_hex=color_val,
            log_callback=self.txt_console.appendPlainText
        )

    def _on_insert_sfx_at_playhead(self, sfx_path: str, target_track: int, volume_offset_db: float):
        """Chèn SFX wav vào Playhead trên Timeline Resolve."""
        resolve_auto = ResolveAutomation()
        resolve_auto.insert_sfx_to_track(
            sfx_path=sfx_path,
            target_track=target_track,
            volume_offset_db=volume_offset_db,
            log_callback=self.txt_console.appendPlainText
        )

    def _on_apply_lut_to_timeline(self, lut_id: str):
        """Áp dụng màu / LUT vào Timeline."""
        self.txt_console.appendPlainText(f"🎨 [Color / LUT] Đang áp dụng phong cách màu '{lut_id}' vào Timeline...")
        from src.core.lut_generator import BUILTIN_COLOR_LOOKS
        look = next((lk for lk in BUILTIN_COLOR_LOOKS if lk.id == lut_id), None)
        if look is None:
            self.txt_console.appendPlainText(f" ❌ Không tìm thấy bộ màu '{lut_id}'.")
            return
        resolve_auto = ResolveAutomation()
        if not resolve_auto.connect():
            self.txt_console.appendPlainText(" ℹ Mở DaVinci Resolve và Timeline để áp dụng Color Grade trực tiếp.")
            return
        lut_file = os.path.join(self.tab_export.luts_dir, look.file_name)
        resolve_auto.apply_look_lut(
            lut_path=lut_file,
            scope=self.tab_export.combo_lut_scope.currentData() or "timeline",
            skip_existing=self.tab_export.check_lut_skip_existing.isChecked(),
            log_callback=self.txt_console.appendPlainText
        )

    def _on_scan_existing_luts(self):
        """Liệt kê LUT đã gắn trong Timeline để người dùng biết trước khi áp dụng thêm."""
        resolve_auto = ResolveAutomation()
        if not resolve_auto.connect():
            self.txt_console.appendPlainText(" ℹ Mở DaVinci Resolve và Timeline để kiểm tra LUT.")
            return
        info = resolve_auto.scan_existing_luts()
        if info["timeline"]:
            self.txt_console.appendPlainText(f" 🎨 LUT cấp Timeline: {', '.join(info['timeline'])}")
        if info["clips"]:
            self.txt_console.appendPlainText(f" 🎨 {len(info['clips'])}/{info['total_clips']} clip đã có LUT:")
            for name, luts in info["clips"][:10]:
                self.txt_console.appendPlainText(f"    - {name}: {', '.join(luts)}")
        if not info["timeline"] and not info["clips"]:
            self.txt_console.appendPlainText(f" 🎨 Chưa có LUT nào trong Timeline ({info['total_clips']} clip). Có thể áp dụng bộ màu mới.")

    def _on_start_render_job(self, preset_id: str, custom_name: str):
        """Gửi lệnh kết xuất sang DaVinci Resolve Deliver Page."""
        resolve_auto = ResolveAutomation()
        resolve_auto.set_render_preset_and_queue(
            preset_name=preset_id,
            custom_name=custom_name,
            log_callback=self.txt_console.appendPlainText
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        event.accept()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        valid_exts = {".mp4", ".mov", ".mkv", ".avi", ".wav", ".mp3"}
        dropped_files = [u.toLocalFile() for u in urls if os.path.splitext(u.toLocalFile())[1].lower() in valid_exts]
        
        if dropped_files:
            event.acceptProposedAction()
            self._reset_workflow_phase()
            self.selected_files = dropped_files
            if len(dropped_files) == 1:
                self.lbl_file.setText(dropped_files[0])
                self.txt_console.appendPlainText(f"📁 [Kéo-thả] Đã chọn tệp: {dropped_files[0]}")
            else:
                self.lbl_file.setText("; ".join(dropped_files))
                self.txt_console.appendPlainText(f"📁 [Kéo-thả] Đã chọn hàng loạt {len(dropped_files)} tệp video.")
            self._update_default_chars_limit(dropped_files[0])
            self._suggest_whisper_model_for_file(dropped_files[0])

    def _update_live_preview(self):
        """Cập nhật ảnh xem trước thời gian thực (Real-time Live Preview) của Text Style Preset."""
        if not hasattr(self, "preview_lbl") or not hasattr(self, "combo_text_preset"):
            return
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        preset = self.preset_mgr.get_preset(preset_id)
        if not preset:
            return

        aspect = "9:16" if (hasattr(self, "check_reframe") and self.check_reframe.isChecked()) else "16:9"
        
        try:
            sz = int(self.txt_size.text())
        except (ValueError, AttributeError):
            sz = preset.size
            
        font_name = self.txt_font.text() if hasattr(self, "txt_font") and self.txt_font.text().strip() else preset.font
        color_val = self.txt_color.text() if hasattr(self, "txt_color") and self.txt_color.text().strip() else preset.standard_color
        
        render_preset = TextStylePreset(
            id=preset.id,
            name=preset.name,
            font=font_name,
            size=sz,
            weight=preset.weight,
            standard_color=color_val,
            highlight_color=preset.highlight_color,
            outline_color=preset.outline_color,
            outline_width=preset.outline_width,
            animation=preset.animation,
            timing_curve=preset.timing_curve,
            position_y_16_9=preset.position_y_16_9,
            position_y_9_16=preset.position_y_9_16,
            box_color=preset.box_color,
            glow_color=preset.glow_color,
            gradient_colors=preset.gradient_colors
        )

        temp_img = os.path.join(tempfile.gettempdir(), f"rf_live_prev_{preset.id}_{aspect}.png")
        try:
            TextPreviewRenderer.render_preview_to_file(
                preset=render_preset,
                output_image_path=temp_img,
                sample_words=["ChunDVC", "AI", "Text+", "Subtitle"],
                active_index=2,
                aspect_ratio=aspect
            )
            pix = QPixmap(temp_img)
            self.preview_lbl.setPixmap(pix.scaled(280, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview_lbl.setText(f"Preset: {preset.name} | {font_name} {sz}px")

    def _set_card_active(self, card_widget: QWidget, is_active: bool):
        card_widget.setProperty("active", "true" if is_active else "false")
        card_widget.style().unpolish(card_widget)
        card_widget.style().polish(card_widget)

    def _update_card_active_states(self):
        if not hasattr(self, "group_ai"):
            return
        self._set_card_active(self.group_ai, self.check_cache.isChecked())
        is_director_active = self.check_bad_takes.isChecked() or self.check_punch_in.isChecked() or (self.combo_ai_mode.currentData() != "silence_only")
        self._set_card_active(self.group_director, is_director_active)
        self._set_card_active(self.group_vlog_hook, self.check_vlog_hook.isChecked())
        is_v4_active = self.check_reframe.isChecked() or self.check_broll.isChecked() or self.check_sfx.isChecked()
        self._set_card_active(self.group_v4, is_v4_active)
        self._set_card_active(self.group_sub, self.check_subtitle.isChecked())
        self._set_card_active(self.group_cut, self.check_cut.isChecked())

    def _apply_stylesheet(self):
        self.setStyleSheet(get_application_stylesheet())

    # --- HỆ THỐNG PRESET KIỂU CHỮ ---
    def _load_presets_to_combo(self):
        self.combo_text_preset.blockSignals(True)
        self.combo_text_preset.clear()
        presets = self.preset_mgr.list_presets()
        for p in presets:
            self.combo_text_preset.addItem(f"{p.badge_icon or '✨'} {p.name} [{p.animation.upper()}]", p.id)
        self.combo_text_preset.blockSignals(False)

        if hasattr(self, "tab_titles"):
            self.tab_titles.all_presets = presets
            self.tab_titles._populate_cards()

    def _on_preset_changed(self):
        preset_id = self.combo_text_preset.currentData()
        if preset_id:
            preset = self.preset_mgr.get_preset(preset_id)
            self.txt_font.setText(preset.font)
            self.txt_size.setText(str(preset.size))
            self.txt_color.setText(preset.standard_color)

    def _show_quick_preview(self):
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        preset = self.preset_mgr.get_preset(preset_id)
        aspect = "9:16" if self.check_reframe.isChecked() else "16:9"
        dlg = PreviewDialog(preset=preset, aspect_ratio=aspect, parent=self)
        dlg.exec()

    def _save_custom_preset_dialog(self):
        name, ok = QInputDialog.getText(self, "Tạo Preset Kiểu Chữ Mới", "Nhập tên cho Preset của bạn:")
        if ok and name.strip():
            p_id = name.strip().lower().replace(" ", "_")
            try:
                size_val = int(self.txt_size.text())
            except ValueError:
                size_val = 48

            new_p = TextStylePreset(
                id=p_id,
                name=name.strip(),
                font=self.txt_font.text(),
                size=size_val,
                weight="bold",
                standard_color=self.txt_color.text(),
                highlight_color="#FFD700",
                outline_color="#000000",
                outline_width=0.12,
                animation="pop",
                timing_curve="spring"
            )
            self.preset_mgr.save_custom_preset(new_p)
            self._load_presets_to_combo()
            index = self.combo_text_preset.findData(new_p.id)
            if index >= 0:
                self.combo_text_preset.setCurrentIndex(index)
            self.txt_console.appendPlainText(f"✔ Đã tạo và lưu Preset kiểu chữ mới: '{new_p.name}'")

    # --- HỆ THỐNG RECIPE ---
    def _load_recipes_to_combo(self):
        self.combo_recipes.blockSignals(True)
        self.combo_recipes.clear()
        recipes = self.recipe_mgr.list_recipes()
        for r in recipes:
            self.combo_recipes.addItem(r.name, r.id)
        self.combo_recipes.blockSignals(False)

    def _on_recipe_selected(self):
        recipe_id = self.combo_recipes.currentData()
        if not recipe_id:
            return
        recipe = self.recipe_mgr.get_recipe(recipe_id)
        if not recipe:
            return

        self._apply_recipe_to_ui(recipe)
        self.txt_console.appendPlainText(f"📋 Đã nạp cấu hình Recipe: '{recipe.name}' ({recipe.description or ''})")

    def _apply_recipe_to_ui(self, recipe: Recipe):
        # Workflow
        idx_wf = self.combo_workflow.findData(recipe.workflow_mode)
        if idx_wf >= 0:
            self.combo_workflow.blockSignals(True)
            self.combo_workflow.setCurrentIndex(idx_wf)
            self.combo_workflow.blockSignals(False)

        # AI & Director
        self.combo_model.setCurrentText(recipe.model_size)
        self.combo_lang.setCurrentText(recipe.language)
        
        idx_ai = self.combo_ai_mode.findData(recipe.ai_mode)
        if idx_ai >= 0:
            self.combo_ai_mode.setCurrentIndex(idx_ai)

        self.check_bad_takes.setChecked(recipe.remove_bad_takes)
        self.check_punch_in.setChecked(recipe.enable_punch_in)
        self.check_repeats.setChecked(recipe.remove_repeated_phrases)
        self.check_scene_guard.setChecked(recipe.scene_guard)
        self.check_fill_gaps.setChecked(recipe.fill_gaps)
        self.check_hide_weak_subs.setChecked(recipe.hide_weak_subs)
        idx_vt = self.combo_video_type.findData(recipe.video_type)
        if idx_vt >= 0:
            self.combo_video_type.setCurrentIndex(idx_vt)
        idx_si = self.combo_story_intent.findData(recipe.story_intent)
        if idx_si >= 0:
            self.combo_story_intent.setCurrentIndex(idx_si)
        idx_st = self.combo_story_target.findData(float(recipe.story_target))
        if idx_st >= 0:
            self.combo_story_target.setCurrentIndex(idx_st)
        idx_ht = self.combo_hook_total.findData(float(recipe.vlog_hook_total))
        if idx_ht >= 0:
            self.combo_hook_total.setCurrentIndex(idx_ht)
        idx_pace = self.combo_pacing.findData(recipe.pacing)
        if idx_pace >= 0:
            self.combo_pacing.setCurrentIndex(idx_pace)
        self.txt_confidence_threshold.setText(str(recipe.confidence_threshold))

        # Visual & Hook
        self.check_reframe.setChecked(recipe.enable_reframe)
        self.check_broll.setChecked(recipe.enable_broll)
        self.check_sfx.setChecked(recipe.enable_sfx)
        self.check_vlog_hook.setChecked(recipe.enable_vlog_hook)

        # Subtitle & Preset
        self.check_subtitle.setChecked(recipe.enable_subtitles)
        idx_p = self.combo_text_preset.findData(recipe.text_preset_id)
        if idx_p >= 0:
            self.combo_text_preset.setCurrentIndex(idx_p)

        idx_sp = self.combo_split_mode.findData(recipe.split_mode)
        if idx_sp >= 0:
            self.combo_split_mode.setCurrentIndex(idx_sp)
        self.txt_split_limit.setText(str(recipe.split_limit))
        self.txt_font.setText(recipe.font_name)
        self.txt_size.setText(str(recipe.font_size))

        # Cut
        self.check_cut.setChecked(recipe.run_cut)
        self.check_speedup.setChecked(recipe.speed_up_silence)
        self.slide_db.setValue(int(recipe.silence_db))
        self.slide_dur.setValue(int(recipe.min_duration * 10))
        self._update_card_active_states()

    def _save_current_as_recipe(self):
        name, ok = QInputDialog.getText(self, "Lưu Recipe Cấu Hình", "Nhập tên cho Recipe của bạn (vd: Kênh Podcast A):")
        if ok and name.strip():
            r_id = name.strip().lower().replace(" ", "_")
            try:
                split_limit_val = int(self.txt_split_limit.text())
            except ValueError:
                split_limit_val = 42
            try:
                conf_val = float(self.txt_confidence_threshold.text())
            except ValueError:
                conf_val = 0.70

            new_recipe = Recipe(
                id=r_id,
                name=name.strip(),
                description="Recipe tùy chỉnh cá nhân",
                workflow_mode=self.combo_workflow.currentData() or "advanced",
                model_size=self.combo_model.currentText(),
                language=self.combo_lang.currentText(),
                ai_mode=self.combo_ai_mode.currentData() or "clean_talk",
                remove_bad_takes=self.check_bad_takes.isChecked(),
                remove_repeated_phrases=self.check_repeats.isChecked(),
                scene_guard=self.check_scene_guard.isChecked(),
                fill_gaps=self.check_fill_gaps.isChecked(),
                vlog_hook_total=float(self.combo_hook_total.currentData() or 20.0),
                story_intent=self.combo_story_intent.currentData() or "keep",
                story_target=float(self.combo_story_target.currentData() or 60.0),
                video_type=self.combo_video_type.currentData() or "auto",
                hide_weak_subs=self.check_hide_weak_subs.isChecked(),
                pacing=self.combo_pacing.currentData() or "balanced",
                enable_punch_in=self.check_punch_in.isChecked(),
                punch_in_scale=1.15,
                confidence_threshold=conf_val,
                enable_reframe=self.check_reframe.isChecked(),
                enable_broll=self.check_broll.isChecked(),
                enable_sfx=self.check_sfx.isChecked(),
                enable_subtitles=self.check_subtitle.isChecked(),
                text_preset_id=self.combo_text_preset.currentData() or "karaoke_pop",
                split_mode=self.combo_split_mode.currentData() or "characters",
                split_limit=split_limit_val,
                font_name=self.txt_font.text(),
                font_size=int(self.txt_size.text() or 48),
                run_cut=self.check_cut.isChecked(),
                speed_up_silence=self.check_speedup.isChecked(),
                silence_speed=8.0,
                silence_db=float(self.slide_db.value()),
                min_duration=float(self.slide_dur.value() / 10.0),
                enable_vlog_hook=self.check_vlog_hook.isChecked(),
                vlog_hook_duration=float(self.combo_hook_dur.currentData() or 2.0)
            )
            self.recipe_mgr.save_recipe(new_recipe)
            self._load_recipes_to_combo()
            idx = self.combo_recipes.findData(new_recipe.id)
            if idx >= 0:
                self.combo_recipes.setCurrentIndex(idx)
            self.txt_console.appendPlainText(f"✔ Đã lưu Recipe mới: '{new_recipe.name}'")

    def _delete_selected_recipe(self):
        recipe_id = self.combo_recipes.currentData()
        if not recipe_id:
            return
        if recipe_id in ["podcast_pro", "tiktok_viral_reels", "vlog_hook_speedramp"]:
            QMessageBox.information(self, "Thông báo", "Không thể xóa recipe mặc định của hệ thống.")
            return

        confirm = QMessageBox.question(self, "Xác nhận xóa", "Bạn có chắc chắn muốn xóa Recipe này không?")
        if confirm == QMessageBox.Yes:
            self.recipe_mgr.delete_recipe(recipe_id)
            self._load_recipes_to_combo()
            self.txt_console.appendPlainText(f"✔ Đã xóa Recipe: {recipe_id}")

    # --- WORKFLOW MODES ---
    def _set_story_intent(self, intent: str):
        idx = self.combo_story_intent.findData(intent)
        if idx >= 0:
            self.combo_story_intent.setCurrentIndex(idx)

    def _on_workflow_mode_changed(self):
        mode = self.combo_workflow.currentData()
        if mode == "podcast":
            # Podcast: Silence Cut + Remove Bad Takes + Clean Talk + Sub chuẩn
            self.check_cut.setChecked(True)
            self.check_speedup.setChecked(False)
            self.check_bad_takes.setChecked(True)
            idx_ai = self.combo_ai_mode.findData("clean_talk")
            if idx_ai >= 0:
                self.combo_ai_mode.setCurrentIndex(idx_ai)
            self.check_subtitle.setChecked(True)
            idx_p = self.combo_text_preset.findData("clean_outline")
            if idx_p >= 0:
                self.combo_text_preset.setCurrentIndex(idx_p)
            self.check_reframe.setChecked(False)
            self.check_vlog_hook.setChecked(False)
            self._set_story_intent("keep")
            self.txt_console.appendPlainText("🎯 Chế độ [Podcast/Phỏng vấn]: Tự bật Silence Cut + Clean Talk + Sub viền nét thanh lịch.")
        elif mode == "shorts":
            # Shorts: Viral Shorts + Auto Re-framing 9:16 + Karaoke Pop Sub (Max Words) + Auto SFX
            self.check_cut.setChecked(True)
            idx_ai = self.combo_ai_mode.findData("viral_shorts")
            if idx_ai >= 0:
                self.combo_ai_mode.setCurrentIndex(idx_ai)
            self.check_reframe.setChecked(True)
            self.check_punch_in.setChecked(True)
            self.check_sfx.setChecked(True)
            self.check_subtitle.setChecked(True)
            idx_sp = self.combo_split_mode.findData("words")
            if idx_sp >= 0:
                self.combo_split_mode.setCurrentIndex(idx_sp)
            self.txt_split_limit.setText("6")
            idx_p = self.combo_text_preset.findData("karaoke_pop")
            if idx_p >= 0:
                self.combo_text_preset.setCurrentIndex(idx_p)
            self._set_story_intent("shorts")
            self.txt_console.appendPlainText("🎯 Chế độ [Shorts/TikTok]: Tự bật Viral 60s + Reframe 9:16 + Karaoke Pop Sub + Auto SFX.")
        elif mode == "vlog":
            # Vlog: Hook Teaser + Punch-in + B-Roll + Speed-Ramp
            self.check_vlog_hook.setChecked(True)
            self.check_punch_in.setChecked(True)
            self.check_broll.setChecked(True)
            self.check_cut.setChecked(True)
            self.check_speedup.setChecked(True)
            idx_p = self.combo_text_preset.findData("bounce_word")
            if idx_p >= 0:
                self.combo_text_preset.setCurrentIndex(idx_p)
            self._set_story_intent("cold_open")
            self.txt_console.appendPlainText("🎯 Chế độ [Vlog Hook/Intro]: Tự bật Intro Teaser + Speed-Ramp Timelapse + Punch-in + B-Roll.")
        elif mode == "advanced":
            self.txt_console.appendPlainText("🎯 Chế độ [Advanced]: Đã mở toàn bộ 6 nhóm chức năng chi tiết.")
        self._update_card_active_states()

    def _apply_preset_features(self, idx: int, title: str = ""):
        """Tự động đồng bộ các công tắc tính năng và thiết lập nhanh khi đổi Preset Card."""
        if hasattr(self, "lbl_active_preset_name") and title:
            self.lbl_active_preset_name.setText(f"👉 Đã chọn phong cách: <b style='color:#c4b5fd;'>{title}</b>")
        if idx == 0:  # Vlog có Hook
            self.check_hook.setChecked(True)
            self.check_punch_in.setChecked(True)
            self.check_broll.setChecked(True)
            self.check_sfx.setChecked(True)
            self.check_reframe.setChecked(False)
            self.check_subtitle.setChecked(True)
            self._set_quick_footage("Du lịch")
            self._set_quick_pacing("Cân bằng")
        elif idx == 1:  # Podcast, phỏng vấn
            self.check_hook.setChecked(False)
            self.check_punch_in.setChecked(False)
            self.check_broll.setChecked(False)
            self.check_sfx.setChecked(False)
            self.check_reframe.setChecked(False)
            self.check_subtitle.setChecked(True)
            self._set_quick_footage("Nói liên tục")
            self._set_quick_pacing("Cân bằng")
        elif idx == 2:  # Shorts, TikTok 9:16
            self.check_hook.setChecked(True)
            self.check_punch_in.setChecked(True)
            self.check_broll.setChecked(True)
            self.check_sfx.setChecked(True)
            self.check_reframe.setChecked(True)
            self.check_subtitle.setChecked(True)
            self._set_quick_footage("Tự nhận diện")
            self._set_quick_pacing("Nhanh")
        elif idx == 3:  # Tự chỉnh
            self.txt_console.appendPlainText("🛠 Chế độ [Tự chỉnh]: Mở toàn bộ quyền tinh chỉnh tính năng.")

    def _set_quick_footage(self, name: str):
        """Cập nhật giao diện và backend cho Loại Footage."""
        if hasattr(self, "footage_buttons"):
            for ft_name, btn in self.footage_buttons.items():
                btn.setChecked(ft_name == name)
        mapping = {"Tự nhận diện": "auto", "Nói liên tục": "talking_head", "Hỗn hợp": "mixed", "Du lịch": "travel_vlog"}
        if hasattr(self, "combo_video_type"):
            val = mapping.get(name, "auto")
            c_idx = self.combo_video_type.findData(val)
            if c_idx >= 0:
                self.combo_video_type.setCurrentIndex(c_idx)
        self.txt_console.appendPlainText(f"📹 [Loại Footage]: Đã chọn '{name}'")

    def _set_quick_pacing(self, name: str):
        """Cập nhật giao diện và backend cho Nhịp Dựng."""
        if hasattr(self, "pacing_buttons"):
            for pc_name, btn in self.pacing_buttons.items():
                btn.setChecked(pc_name == name)
        mapping = {"Thong thả": "relaxed", "Cân bằng": "balanced", "Nhanh": "fast"}
        if hasattr(self, "combo_pacing"):
            val = mapping.get(name, "balanced")
            c_idx = self.combo_pacing.findData(val)
            if c_idx >= 0:
                self.combo_pacing.setCurrentIndex(c_idx)
        self.txt_console.appendPlainText(f"⚡ [Nhịp Dựng]: Đã chọn '{name}'")

    # --- 2 LỚP UX: MASTER INTENSITY & TOGGLE ADVANCED ---
    def _on_master_intensity_changed(self, val):
        if val == 1:
            # Nhẹ
            self.lbl_master_intensity.setText("Mức độ cắt vấp & im lặng: NHẸ (Giữ tối đa nhịp tự nhiên)")
            self.slide_db.setValue(-42)
            self.slide_dur.setValue(8) # 0.8s
            self.txt_confidence_threshold.setText("0.55")
        elif val == 2:
            # Vừa
            self.lbl_master_intensity.setText("Mức độ cắt vấp & im lặng: VỪA (Tiêu chuẩn cân bằng)")
            self.slide_db.setValue(-35)
            self.slide_dur.setValue(5) # 0.5s
            self.txt_confidence_threshold.setText("0.70")
        else:
            # Mạnh
            self.lbl_master_intensity.setText("Mức độ cắt vấp & im lặng: MẠNH (Cắt dứt khoát tiết tấu nhanh)")
            self.slide_db.setValue(-28)
            self.slide_dur.setValue(3) # 0.3s
            self.txt_confidence_threshold.setText("0.85")

    def _toggle_advanced_panel(self):
        is_hidden = self.advanced_container.isHidden()
        self.advanced_container.setHidden(not is_hidden)
        if not is_hidden:
            self.btn_toggle_advanced.setText("⚙ Tùy chỉnh nâng cao (Chi tiết Module) ▾")
        else:
            self.btn_toggle_advanced.setText("⚙ Ẩn tùy chỉnh nâng cao ▴")

    def _on_user_customized_limit(self):
        self.user_customized_limit = True

    def _on_user_mode_changed(self):
        self.user_customized_limit = True
        mode = self.combo_split_mode.currentData()
        if mode == "words":
            self.txt_split_limit.setText("6")
        else:
            self.txt_split_limit.setText("42")

    def _reset_workflow_phase(self):
        if hasattr(self, "lbl_pct_big"):
            self.lbl_pct_big.setText("0%")
            self.lbl_pct_big.setStyleSheet("font-size: 32px; font-weight: bold; color: #71717a;")
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.setText("Sẵn sàng")
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(0)
        if hasattr(self, "step_tracker"):
            self.step_tracker.reset()
        if hasattr(self, "step_labels") and hasattr(self, "step_names"):
            for i, lbl in enumerate(self.step_labels):
                lbl.setText(f"○ {self.step_names[i]}")
                lbl.setStyleSheet("color: #71717a; font-size: 12px;")
        """Đặt lại trạng thái kịch bản và xóa sạch cache phân đoạn cũ khi chọn video mới."""
        self.current_phase = 1
        self.clip_data_cache = []
        self.proposed_segments = []
        if hasattr(self, "table_review"):
            self.table_review.setRowCount(0)
            self._set_review_visible(False)
        if hasattr(self, "btn_run"):
            self._update_run_button_state(running=False)

    def _browse_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn Tệp Video nguồn", "", "Video files (*.mp4 *.mov *.mkv *.avi *.wav *.mp3);;All files (*.*)"
        )
        if file_paths:
            self._reset_workflow_phase()
            self.project_structure = None
            self.selected_files = file_paths
            if len(file_paths) == 1:
                self.lbl_file.setText(file_paths[0])
                self.txt_console.appendPlainText(f"📁 Đã chọn tệp: {file_paths[0]}")
            else:
                self.lbl_file.setText("; ".join(file_paths))
                self.txt_console.appendPlainText(f"📁 Đã chọn hàng loạt {len(file_paths)} tệp video.")
            if hasattr(self, "lbl_proj_name"):
                self.lbl_proj_name.setText(os.path.basename(os.path.dirname(file_paths[0])) or "Dự án lẻ")
                self.lbl_proj_name.setStyleSheet("font-weight: bold; color: #f4f4f5; font-size: 12px;")
            if hasattr(self, "lbl_proj_meta"):
                self.lbl_proj_meta.setText(f"{len(file_paths)} tệp video")
            if hasattr(self, "list_chapters"):
                self.list_chapters.clear()
                for fp in file_paths:
                    self.list_chapters.addItem(f"● {os.path.basename(fp)}")
            self._update_default_chars_limit(file_paths[0])
            self._suggest_whisper_model_for_file(file_paths[0])
            self._check_project_cache_status()

    def _browse_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Chọn Thư mục Dự án chứa các video con", "")
        if folder_path and os.path.exists(folder_path):
            self._load_project_folder(folder_path)

    def _load_project_folder(self, folder_path: str):
        self._reset_workflow_phase()
        proj = scan_project_folder(folder_path)
        self.project_structure = proj
        if not proj.all_video_paths:
            self.txt_console.appendPlainText(f"⚠ Không tìm thấy video hợp lệ trong thư mục: {folder_path}")
            return
        
        self.selected_files = proj.all_video_paths
        self.lbl_file.setText(f"📂 [{proj.root_name}] {len(proj.groups)} nhóm • {proj.total_files} video ({proj.root_path})")
        if hasattr(self, "lbl_proj_name"):
            self.lbl_proj_name.setText(proj.root_name)
            self.lbl_proj_name.setStyleSheet("font-weight: bold; color: #f4f4f5; font-size: 12px;")
        if hasattr(self, "lbl_proj_meta"):
            self.lbl_proj_meta.setText(f"{len(proj.groups)} nhóm · {proj.total_files} video")
        if hasattr(self, "list_chapters"):
            self.list_chapters.clear()
            cache_mgr = ScanCacheManager()
            model = self.combo_model.currentText()
            lang = self.combo_lang.currentText()
            for grp in proj.groups:
                all_cached = True
                has_any = False
                for vp in grp.video_paths:
                    if cache_mgr.get_cached_scan(vp, model, lang) is not None:
                        has_any = True
                    else:
                        all_cached = False
                if all_cached and grp.video_paths:
                    dot_symbol = "🟢"
                    tag = "Cache"
                elif has_any:
                    dot_symbol = "🔵"
                    tag = "Quét"
                else:
                    dot_symbol = "⚪"
                    tag = "Chờ"
                self.list_chapters.addItem(f"{dot_symbol} {grp.name}  ({len(grp.video_paths)} clips)  [{tag}]")
        self.txt_console.appendPlainText("\n" + "="*60)
        self.txt_console.appendPlainText("🚀 [CẤU TRÚC THƯ MỤC DỰ ÁN PHÂN TẦNG ĐÃ NẠP]")
        self.txt_console.appendPlainText(proj.summary_tree())
        self.txt_console.appendPlainText("="*60 + "\n")
        
        first_video = proj.main_video_paths[0] if proj.main_video_paths else proj.all_video_paths[0]
        self._update_default_chars_limit(first_video)
        self._suggest_whisper_model_for_file(first_video)
        self._check_project_cache_status()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            if not paths:
                return
            event.acceptProposedAction()
            
            if len(paths) == 1 and os.path.isdir(paths[0]):
                self._load_project_folder(paths[0])
            else:
                videos, proj = collect_video_paths(paths)
                if proj:
                    self._load_project_folder(proj.root_path)
                elif videos:
                    self._reset_workflow_phase()
                    self.project_structure = None
                    self.selected_files = videos
                    if len(videos) == 1:
                        self.lbl_file.setText(videos[0])
                        self.txt_console.appendPlainText(f"📁 Đã kéo-thả tệp: {videos[0]}")
                    else:
                        self.lbl_file.setText("; ".join(videos))
                        self.txt_console.appendPlainText(f"📁 Đã kéo-thả {len(videos)} tệp video.")
                    self._update_default_chars_limit(videos[0])
                    self._suggest_whisper_model_for_file(videos[0])
                    self._check_project_cache_status()
                else:
                    self.txt_console.appendPlainText("⚠ Không phát hiện file video hợp lệ từ các tệp được thả vào.")
        else:
            super().dropEvent(event)

    def _open_google_drive_dialog(self):
        """Mở hộp thoại nhập liên kết Google Drive."""
        dlg = GoogleDriveImportDialog(self)
        dlg.import_completed.connect(self._on_google_drive_import_completed)
        dlg.exec()

    def _on_google_drive_import_completed(self, local_path: str, is_folder: bool):
        """Xử lý sau khi tải tệp/thư mục từ Google Drive thành công."""
        if is_folder:
            self._load_project_folder(local_path)
        else:
            self._reset_workflow_phase()
            self.project_structure = None
            self.selected_files = [local_path]
            self.lbl_file.setText(local_path)
            self.txt_console.appendPlainText(f"\n☁ [Google Drive] Đã tải về và nạp tệp thành công:\n👉 {local_path}\n")
            self._update_default_chars_limit(local_path)
            self._suggest_whisper_model_for_file(local_path)
            self._check_project_cache_status()

    def _check_project_cache_status(self):
        """Kiểm tra xem toàn bộ các video trong dự án đã có cache trong máy hay chưa và cập nhật UI."""
        raw_paths = getattr(self, "selected_files", [])
        if not raw_paths:
            if hasattr(self, "lbl_s1_cache_status"):
                self.lbl_s1_cache_status.setText("⚪ Chưa chọn video. Vui lòng chọn tệp hoặc thư mục.")
                self.lbl_s1_cache_status.setStyleSheet("background-color: #031e22; color: #39c1d3; border: 1px solid #0c3d44; border-radius: 6px; padding: 8px; font-size: 12px;")
            if hasattr(self, "lbl_cache_badge"):
                self.lbl_cache_badge.setText("⚪ Chưa chọn video")
                self.lbl_cache_badge.setStyleSheet("background-color: #031e22; color: #39c1d3; border: 1px solid #0c3d44; padding: 4px 8px; border-radius: 6px; font-weight: bold; font-size: 11px;")
            return

        cache_mgr = ScanCacheManager()
        model = self.combo_model.currentText()
        lang = self.combo_lang.currentText()

        cached_count = 0
        for vp in raw_paths:
            if cache_mgr.get_cached_scan(vp, model, lang) is not None:
                cached_count += 1

        total = len(raw_paths)
        if total > 0 and cached_count == total:
            status_msg = f"🎉 ĐÃ CÓ SẴN CACHE (100% - {total}/{total} clips)! Dữ liệu đã sẵn sàng. Bạn có thể sang Bước 2 hoặc nạp JSON để xuất timeline ngay trong 0.1s!"
            status_style = "background-color: rgba(16, 185, 129, 0.15); color: #34D399; border: 1px solid #10B981; border-radius: 6px; padding: 8px; font-weight: bold; font-size: 12px;"
            badge_txt = f"⚡ 100% Sẵn sàng ({total}/{total})"
            badge_style = "background-color: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid #10b981; padding: 2px 8px; border-radius: 999px; font-weight: bold; font-size: 11px;"
            if hasattr(self, "lbl_cache_hint"):
                self.lbl_cache_hint.setText("✓ 100% Sẵn sàng · Bấm [Bắt đầu dựng] để xuất tức thì!")
            if hasattr(self, "cache_progress_bar"):
                self.cache_progress_bar.setStyleSheet("""
                    QProgressBar { background-color: #27272a; border-radius: 3px; }
                    QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #06b6d4); border-radius: 3px; }
                """)
            if hasattr(self, "btn_s1_skip_stage2"):
                self.btn_s1_skip_stage2.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 11px; border-radius: 6px; font-size: 12px;")
        elif cached_count > 0:
            status_msg = f"⚡ Đã có cache một phần ({cached_count}/{total} clips). Bấm Quét để hoàn tất {total - cached_count} clips còn lại."
            status_style = "background-color: rgba(245, 158, 11, 0.15); color: #FBBF24; border: 1px solid #F59E0B; border-radius: 6px; padding: 8px; font-size: 12px;"
            badge_txt = f"⚡ Cache: {cached_count}/{total} ({int(cached_count/total*100)}%)"
            badge_style = "background-color: rgba(6, 182, 212, 0.15); color: #22d3ee; border: 1px solid #06b6d4; padding: 2px 8px; border-radius: 999px; font-weight: bold; font-size: 11px;"
            if hasattr(self, "lbl_cache_hint"):
                self.lbl_cache_hint.setText(f"⚡ Đã nạp {cached_count}/{total} clip · Sẽ tự quét {total - cached_count} clip khi bấm dựng")
            if hasattr(self, "cache_progress_bar"):
                self.cache_progress_bar.setStyleSheet("""
                    QProgressBar { background-color: #27272a; border-radius: 3px; }
                    QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #06b6d4); border-radius: 3px; }
                """)
        else:
            status_msg = f"🔍 Chưa có Cache ({total} clips). Sẽ tự động quét & nạp cache khi bấm [Bắt đầu dựng]."
            status_style = "background-color: rgba(56, 189, 248, 0.1); color: #3dcee1; border: 1px solid #2c9dac; border-radius: 6px; padding: 8px; font-size: 12px;"
            badge_txt = f"⚠️ Chưa có ({0}/{total})"
            badge_style = "background-color: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid #f59e0b; padding: 2px 8px; border-radius: 999px; font-weight: bold; font-size: 11px;"
            if hasattr(self, "lbl_cache_hint"):
                self.lbl_cache_hint.setText(f"Chưa có cache (0/{total} clip) · Sẽ tự quét & nạp cache khi bấm [Bắt đầu dựng]")
            if hasattr(self, "cache_progress_bar"):
                self.cache_progress_bar.setStyleSheet("""
                    QProgressBar { background-color: #27272a; border-radius: 3px; }
                    QProgressBar::chunk { background-color: #3f3f46; border-radius: 3px; }
                """)

        if hasattr(self, "cache_progress_bar"):
            p = int(cached_count / total * 100) if total else 0
            self.cache_progress_bar.setValue(p)

        if hasattr(self, "lbl_s1_cache_status"):
            self.lbl_s1_cache_status.setText(status_msg)
            self.lbl_s1_cache_status.setStyleSheet(status_style)
        if hasattr(self, "lbl_cache_badge"):
            self.lbl_cache_badge.setText(badge_txt)
            self.lbl_cache_badge.setStyleSheet(badge_style)
        if hasattr(self, "lbl_s1_media_info"):
            proj_title = self.project_structure.root_name if self.project_structure else (os.path.basename(raw_paths[0]) if total == 1 else f"{total} videos")
            self.lbl_s1_media_info.setText(f"📁 <b>Dự án:</b> {proj_title} | <b>Tổng cộng:</b> {total} video tệp")

        if hasattr(self, "btn_run") and not getattr(self, "is_processing", False):
            self._update_run_button_state(False)

    def _build_copilot_clips_data(self) -> Tuple[str, List[Dict[str, Any]]]:
        """Tổng hợp danh sách clip và transcript từ cache để phục vụ các động cơ AI."""
        if not hasattr(self, "selected_files") or not self.selected_files:
            return "", []

        clips_data = []
        cache_mgr = ScanCacheManager()
        
        for idx, vp in enumerate(self.selected_files):
            clip_name = os.path.basename(vp)
            chapter_name = "Chung"
            if self.project_structure:
                for grp in self.project_structure.groups:
                    if vp in grp.video_paths:
                        chapter_name = grp.name
                        break
            
            cached = cache_mgr.get_cached_scan(vp, self.combo_model.currentText(), self.combo_lang.currentText())
            dur = 0.0
            if cached:
                dur = float(cached.get("clip_dur", 0.0) or cached.get("duration", 0.0))
            if dur <= 0:
                try:
                    dur = EDLGenerator.get_video_duration(vp)
                except Exception:
                    dur = 0.0
            
            raw_subs = cached.get("raw_subtitles", []) if cached else []
            transcript_text = " ".join(s.get("text", "").strip() for s in raw_subs)
            
            clips_data.append({
                "id": idx + 1,
                "name": clip_name,
                "path": vp,
                "chapter": chapter_name,
                "duration": dur,
                "audio_peak_sec": dur * 0.5,
                "visual_motion": "vừa",
                "speech": transcript_text
            })

        proj_name = self.project_structure.root_name if self.project_structure else (os.path.splitext(os.path.basename(self.selected_files[0]))[0] if len(self.selected_files) == 1 else "Vlog_Project")
        return proj_name, clips_data

    def _quick_copy_copilot_prompt(self):
        """Tạo nhanh prompt đạo diễn kịch bản từ các clips và copy vào Clipboard."""
        proj_name, clips_data = self._build_copilot_clips_data()
        if not clips_data:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn hoặc thư mục dự án trước khi copy Prompt Đạo Diễn.")
            return

        fmt = "travel_vlog"
        if hasattr(self.tab_copilot, "combo_story_format"):
            fmt = self.tab_copilot.combo_story_format.currentData() or "travel_vlog"

        prompt = StoryCopilot.generate_copilot_prompt(
            project_name=proj_name,
            clips_data=clips_data,
            project_structure=self.project_structure,
            story_intent=fmt
        )

        QApplication.clipboard().setText(prompt)
        self.txt_console.appendPlainText("\n" + "="*60)
        self.txt_console.appendPlainText("📋 [AI Story Copilot] ĐÃ COPY TOÀN BỘ PROMPT ĐẠO DIỄN VÀO CLIPBOARD!")
        self.txt_console.appendPlainText(f"👉 Định dạng: {fmt.upper()} (Đã tích hợp Framework Marketing & Kho Meme).")
        self.txt_console.appendPlainText("👉 Hãy chuyển sang trình duyệt (Claude.ai hoặc ChatGPT), bấm Ctrl+V để gửi.")
        self.txt_console.appendPlainText("👉 Khi nhận được đoạn kịch bản JSON, hãy dán vào ô bên dưới và bấm 'THI CÔNG TIMELINE'!")
        self.txt_console.appendPlainText("="*60 + "\n")
        QMessageBox.information(
            self,
            "Đã Copy Prompt Đạo Diễn!",
            "📋 Đã copy toàn bộ Prompt Đạo Diễn chứa đầy đủ Lời thoại (Transcript) và Cấu trúc Clips vào Clipboard!\n\n"
            "👉 Bước tiếp theo:\n"
            "1. Mở Claude 3.7 Sonnet (claude.ai) hoặc ChatGPT.\n"
            "2. Dán prompt vào (Ctrl+V) và gửi.\n"
            "3. Copy đoạn JSON kịch bản trả về, dán vào ô Kịch Bản JSON bên dưới và bấm 'THI CÔNG TIMELINE'!"
        )

    def _run_local_ollama_pipeline(self, fmt: str, model: str, endpoint: str):
        """Chạy AI Đạo Diễn qua Local Ollama ngầm không làm đơ giao diện."""
        proj_name, clips_data = self._build_copilot_clips_data()
        if not clips_data:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn hoặc quét Cache ở Bước 1 trước khi chạy AI.")
            return

        prompt = StoryCopilot.generate_copilot_prompt(
            project_name=proj_name,
            clips_data=clips_data,
            project_structure=self.project_structure,
            story_intent=fmt
        )
        self.txt_console.appendPlainText(f"\n🧠 [Local AI] Bắt đầu yêu cầu kịch bản từ Ollama ({model})...")
        self.btn_run.setEnabled(False)

        self._llm_worker = LLMDirectorWorker(
            engine_mode="ollama",
            prompt=prompt,
            model=model,
            endpoint=endpoint,
            parent=self
        )
        self._llm_worker.log_signal.connect(self.txt_console.appendPlainText)
        self._llm_worker.finished_signal.connect(self._on_llm_worker_finished)
        self._llm_worker.start()

    def _run_cloud_api_pipeline(self, fmt: str, provider: str, api_key: str):
        """Chạy AI Đạo Diễn qua Cloud API ngầm không làm đơ giao diện."""
        proj_name, clips_data = self._build_copilot_clips_data()
        if not clips_data:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn hoặc quét Cache ở Bước 1 trước khi chạy AI.")
            return

        prompt = StoryCopilot.generate_copilot_prompt(
            project_name=proj_name,
            clips_data=clips_data,
            project_structure=self.project_structure,
            story_intent=fmt
        )
        self.txt_console.appendPlainText(f"\n⚡ [Cloud AI] Bắt đầu gửi kịch bản sang {provider.upper()} API...")
        self.btn_run.setEnabled(False)

        self._llm_worker = LLMDirectorWorker(
            engine_mode="cloud",
            prompt=prompt,
            api_key=api_key,
            provider=provider,
            parent=self
        )
        self._llm_worker.log_signal.connect(self.txt_console.appendPlainText)
        self._llm_worker.finished_signal.connect(self._on_llm_worker_finished)
        self._llm_worker.start()

    def _on_llm_worker_finished(self, success: bool, message: str, raw_json: str):
        """Xử lý khi LLM Worker hoàn tất phản hồi."""
        self.btn_run.setEnabled(True)
        if not success:
            QMessageBox.critical(self, "Lỗi AI Director", f"Không thể lấy kịch bản từ AI:\n{message}")
            self.txt_console.appendPlainText(f"❌ [Lỗi AI Director] {message}")
            return

        self.txt_console.appendPlainText("🎉 [AI Director] Đã nhận được kịch bản thành công! Đang tự động nạp Timeline...")
        self.tab_copilot.txt_json_input.setPlainText(raw_json)
        self._apply_json_text_plan(raw_json)

    def _set_ui_step_progress(self, pct: int, status: str, step_idx: int = -1):
        """Cập nhật mượt mà tiến trình UI không bao giờ bị đứng app."""
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(pct)
        if hasattr(self, "lbl_pct_big"):
            self.lbl_pct_big.setText(f"{pct}%")
            col = "#86efac" if pct >= 100 else "#f4f4f5"
            self.lbl_pct_big.setStyleSheet(f"font-size: 32px; font-weight: bold; color: {col};")
        if hasattr(self, "lbl_eta"):
            self.lbl_eta.setText(status)
        if hasattr(self, "step_labels") and hasattr(self, "step_names"):
            for i, lbl in enumerate(self.step_labels):
                if pct >= 100:
                    lbl.setText(f"✓ {self.step_names[i]}")
                    lbl.setStyleSheet("color: #86efac; font-size: 12px; font-weight: 500;")
                elif i < step_idx:
                    lbl.setText(f"✓ {self.step_names[i]}")
                    lbl.setStyleSheet("color: #86efac; font-size: 12px;")
                elif i == step_idx:
                    lbl.setText(f"● {self.step_names[i]}")
                    lbl.setStyleSheet("color: #c4b5fd; font-size: 12px; font-weight: bold;")
                else:
                    lbl.setText(f"○ {self.step_names[i]}")
                    lbl.setStyleSheet("color: #71717a; font-size: 12px;")
        if hasattr(self, "bubble") and self.bubble:
            self.bubble.update_progress(pct, status)
        QApplication.processEvents()

    def _apply_json_text_plan(self, json_text: str):
        """Phân tích và thi công kịch bản JSON nhập từ ô văn bản TabCopilot."""
        if not json_text or not json_text.strip():
            QMessageBox.warning(self, "Chưa có kịch bản JSON", "Vui lòng dán đoạn mã JSON kịch bản trước khi thi công.")
            return

        self._set_ui_step_progress(15, "Đang phân tích kịch bản JSON...", 0)
        try:
            plan = StoryCopilot.parse_copilot_response(json_text)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi phân tích JSON", f"Không thể phân tích đoạn kịch bản JSON:\n{e}")
            self._set_ui_step_progress(0, "Lỗi cú pháp JSON", -1)
            return

        self._on_copilot_plan_applied(plan)

    def _load_json_plan_from_disk(self):
        """Mở tệp JSON kịch bản (AI Copilot Director Plan) và xuất Timeline ngay lập tức trong 0.1s."""
        f_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn Tệp Kịch Bản JSON (AI Story Copilot Plan)",
            "",
            "JSON Files (*.json);;Text Files (*.txt);;All Files (*.*)"
        )
        if not f_path or not os.path.exists(f_path):
            return

        try:
            with open(f_path, "r", encoding="utf-8") as f:
                content = f.read()
            plan = StoryCopilot.parse_copilot_response(content)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi đọc tệp JSON", f"Không thể đọc hoặc phân tích tệp kịch bản JSON:\n{e}")
            return

        # Nếu chưa chọn files hoặc thư mục, tự động tìm kiếm video trong thư mục chứa file JSON hoặc thư mục con
        if not hasattr(self, "selected_files") or not self.selected_files:
            json_dir = os.path.dirname(os.path.abspath(f_path))
            detected_videos = []
            for root, _, files in os.walk(json_dir):
                for fn in files:
                    if fn.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.m4v')):
                        detected_videos.append(os.path.join(root, fn))
            if detected_videos:
                self.selected_files = detected_videos
                self.lbl_file.setText(f"{len(detected_videos)} tệp video tại {json_dir}")

        if not hasattr(self, "selected_files") or not self.selected_files:
            QMessageBox.warning(self, "Chưa có Video", "Vui lòng chọn video nguồn hoặc thư mục dự án để khớp với kịch bản JSON.")
            return

        self.txt_console.appendPlainText(f"\n📂 [Nạp Kịch Bản JSON Trực Tiếp] Đã đọc thành công tệp: {os.path.basename(f_path)}")
        self._on_copilot_plan_applied(plan)

    def _open_story_copilot_dialog(self):
        """Mở hộp thoại AI Story Copilot để xuất Prompt cho Claude/ChatGPT và nạp bản vẽ kịch bản."""
        if not hasattr(self, "selected_files") or not self.selected_files:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn tệp video hoặc thư mục dự án trước khi mở AI Copilot.")
            return

        # Chuẩn bị dữ liệu danh sách clips siêu tốc từ Cache
        clips_data = []
        cache_mgr = ScanCacheManager()
        
        for idx, vp in enumerate(self.selected_files):
            clip_name = os.path.basename(vp)
            chapter_name = "Chung"
            if self.project_structure:
                for grp in self.project_structure.groups:
                    if vp in grp.video_paths:
                        chapter_name = grp.name
                        break
            
            # Đọc từ cache
            cached = cache_mgr.get_cached_scan(vp, self.combo_model.currentText(), self.combo_lang.currentText())
            dur = 0.0
            if cached:
                dur = float(cached.get("clip_dur", 0.0) or cached.get("duration", 0.0))
            
            # Nếu chưa có cache, lấy độ dài nhanh hoặc ước lượng nhẹ để không block UI
            if dur <= 0:
                try:
                    dur = EDLGenerator.get_video_duration(vp)
                except Exception:
                    dur = 0.0
            
            raw_subs = cached.get("raw_subtitles", []) if cached else []
            transcript_text = " ".join(s.get("text", "").strip() for s in raw_subs)
            
            clips_data.append({
                "id": idx + 1,
                "name": clip_name,
                "path": vp,
                "chapter": chapter_name,
                "duration": dur,
                "audio_peak_sec": dur * 0.5,
                "visual_motion": "vừa",
                "speech": transcript_text
            })

        proj_name = self.project_structure.root_name if self.project_structure else (os.path.splitext(os.path.basename(self.selected_files[0]))[0] if len(self.selected_files) == 1 else "Vlog_Project")

        dlg = StoryCopilotDialog(
            project_name=proj_name,
            clips_data=clips_data,
            project_structure=self.project_structure,
            parent=self
        )
        dlg.plan_applied_signal.connect(self._on_copilot_plan_applied)
        dlg.exec()

    def _on_copilot_plan_applied(self, plan: CopilotDirectorPlan):
        """Áp dụng bản vẽ từ Copilot và dựng trực tiếp lên DaVinci Resolve (Hỗ trợ cả bản Free & Studio)."""
        self.txt_console.appendPlainText("\n" + "="*60)
        self.txt_console.appendPlainText(f"🧠 [AI Copilot Plan Received] Đang thi công kịch bản của Đạo diễn Cloud AI...")
        self.txt_console.appendPlainText(f"🎯 Chiến lược: {plan.strategy_summary}")
        if plan.global_hook:
            self.txt_console.appendPlainText(f"🔥 Global Hook: Clip {plan.global_hook.clip_index} ({plan.global_hook.start_sec}s - {plan.global_hook.end_sec}s) ➔ \"{plan.global_hook.hook_title}\"")
        self.txt_console.appendPlainText(f"🎬 Số phân đoạn Timeline: {len(plan.timeline_segments)} cuts")
        if plan.broll_inserts:
            self.txt_console.appendPlainText(f"🎞 Số điểm chèn B-Roll/Meme: {len(plan.broll_inserts)} cues")
        if plan.sfx_inserts:
            self.txt_console.appendPlainText(f"🔊 Số hiệu ứng SFX: {len(plan.sfx_inserts)} cues")
        self.txt_console.appendPlainText("="*60 + "\n")

        # Xác định thư mục gốc dự án
        first_v = self.selected_files[0] if hasattr(self, "selected_files") and self.selected_files else ""
        if self.project_structure and getattr(self.project_structure, "root_path", None):
            project_root_dir = self.project_structure.root_path
        else:
            try:
                common = os.path.commonpath([os.path.abspath(p) for p in self.selected_files])
                project_root_dir = common if os.path.isdir(common) else os.path.dirname(common)
            except Exception:
                project_root_dir = os.path.dirname(os.path.abspath(first_v)) if first_v else os.getcwd()

        base_d = project_root_dir
        output_dir = os.path.join(project_root_dir, "_TIMELINE_IMPORT")
        os.makedirs(output_dir, exist_ok=True)

        # Nạp kho tài nguyên B-Roll / SFX của dự án
        asset_pool = GlobalAssetPool.get_instance()
        n_pm, n_ps = asset_pool.register_project_assets(project_root_dir)
        if n_pm > 0 or n_ps > 0:
            self.txt_console.appendPlainText(f"📦 [Project Assets Pool] Đã nạp {n_pm} video B-roll/Meme và {n_ps} file SFX từ thư mục dự án.")

        # Ánh xạ phong phú index, filename, stem, relative path -> video_path
        path_map: Dict[Any, str] = {}
        for idx, vp in enumerate(getattr(self, "selected_files", [])):
            p_abs = os.path.abspath(vp)
            b_name = os.path.basename(p_abs).lower()
            s_name = os.path.splitext(b_name)[0]
            path_map[idx + 1] = p_abs
            path_map[str(idx + 1)] = p_abs
            path_map[idx] = p_abs
            path_map[str(idx)] = p_abs
            path_map[f"clip_{idx+1}"] = p_abs
            path_map[f"clip {idx+1}"] = p_abs
            path_map[f"clip{idx+1}"] = p_abs
            path_map[b_name] = p_abs
            path_map[s_name] = p_abs
            try:
                p_rel = os.path.relpath(p_abs, project_root_dir).replace('\\', '/').lower()
                path_map[p_rel] = p_abs
            except Exception:
                pass

        if self.project_structure and hasattr(self.project_structure, "all_video_paths"):
            for vp in self.project_structure.all_video_paths:
                p_abs = os.path.abspath(vp)
                b_name = os.path.basename(p_abs).lower()
                s_name = os.path.splitext(b_name)[0]
                path_map[b_name] = p_abs
                path_map[s_name] = p_abs
                try:
                    p_rel = os.path.relpath(p_abs, project_root_dir).replace('\\', '/').lower()
                    path_map[p_rel] = p_abs
                except Exception:
                    pass

        self._set_ui_step_progress(45, "Đang khớp video & tính toán cuts...", 1)
        events, subs, markers = StoryCopilot.convert_plan_to_resolve_timeline(plan, path_map)
        self._set_ui_step_progress(70, "Đang tạo file FCP7 XML & FCPXML...", 2)

        if not events:
            QMessageBox.warning(self, "Không có phân đoạn", "Bản vẽ không tìm thấy phân đoạn video hợp lệ nào.")
            return

        proj_name = self.project_structure.root_name if self.project_structure else "Copilot_Director_Cut"
        timeline_name = f"{proj_name}_Copilot_Cut"

        # Đặt toàn bộ file xuất TRỰC TIẾP trong thư mục _TIMELINE_IMPORT
        out_fcpxml = os.path.join(output_dir, f"{timeline_name}.fcpxml")
        out_fcp7xml = os.path.join(output_dir, f"{timeline_name}.xml")
        out_edl = os.path.join(output_dir, f"{timeline_name}.edl")

        # Lọc danh sách events cho track 1 chính (A-Roll)
        v1_events = [e for e in events if e.get("track", 1) == 1 and not e.get("is_broll_overlay") and not e.get("is_sfx")]
        if not v1_events:
            v1_events = events

        fps = 30.0
        if first_v:
            try:
                fps = EDLGenerator.get_video_fps(first_v)
            except Exception:
                fps = 30.0

        # 1. Sinh FCPXML v1.9 (Apple Final Cut Pro / Resolve)
        FCPXMLGenerator.generate_timeline_fcpxml(
            events=v1_events,
            output_xml_path=out_fcpxml,
            timeline_name=timeline_name,
            fps=fps,
            aspect_ratio="16:9",
            markers=markers
        )

        # 2. Sinh FCP7 XML (Multi-Track V1, V2 Meme, A1/A2 Audio, A3 SFX chuẩn DaVinci Resolve Windows)
        FCPXMLGenerator.generate_fcp7_xml(
            events=v1_events,
            output_xml_path=out_fcp7xml,
            timeline_name=timeline_name,
            fps=fps,
            aspect_ratio="16:9",
            broll_inserts=plan.broll_inserts,
            sfx_inserts=plan.sfx_inserts,
            markers=markers
        )

        # 3. Sinh EDL CMX3600
        EDLGenerator.create_multi_clip_edl(v1_events, out_edl, markers=markers)

        # 4. Tách riêng Timeline Hook (Intro Highlight Teaser) độc lập
        out_hook_fcp7xml = None
        out_hook_fcpxml = None
        hook_events = [e for e in events if e.get("is_hook")]
        if not hook_events and plan.global_hook:
            h = plan.global_hook
            vpath = StoryCopilot._find_clip_path(h.clip_index, h.clip_name, path_map)
            if vpath and os.path.exists(vpath):
                dur = max(0.5, h.end_sec - h.start_sec)
                hook_events.append({
                    "video_path": vpath,
                    "src_in": h.start_sec,
                    "src_out": h.end_sec,
                    "rec_in": 0.0,
                    "rec_out": dur,
                    "speed": 1.0,
                    "punch_in": h.punch_in,
                    "punch_in_scale": 1.15,
                    "is_hook": True,
                    "track": 1,
                    "reason": h.reason
                })

        if hook_events:
            hook_timeline_name = f"{proj_name}_Timeline_Intro_Hook"
            out_hook_fcp7xml = os.path.join(output_dir, f"{hook_timeline_name}.xml")
            out_hook_fcpxml = os.path.join(output_dir, f"{hook_timeline_name}.fcpxml")
            out_hook_edl = os.path.join(output_dir, f"{hook_timeline_name}.edl")
            
            hook_markers = [
                {
                    "time": 0.0,
                    "duration": hook_events[0]["rec_out"] - hook_events[0]["rec_in"],
                    "name": f"🔥 GLOBAL HOOK: {plan.global_hook.hook_title if plan.global_hook else 'Intro Hook'}",
                    "color": "Magenta",
                    "note": plan.global_hook.reason if plan.global_hook else ""
                }
            ]
            FCPXMLGenerator.generate_fcp7_xml(
                events=hook_events,
                output_xml_path=out_hook_fcp7xml,
                timeline_name=hook_timeline_name,
                fps=fps,
                aspect_ratio="16:9",
                markers=hook_markers
            )
            FCPXMLGenerator.generate_timeline_fcpxml(
                events=hook_events,
                output_xml_path=out_hook_fcpxml,
                timeline_name=hook_timeline_name,
                fps=fps,
                aspect_ratio="16:9",
                markers=hook_markers
            )
            EDLGenerator.create_multi_clip_edl(hook_events, out_hook_edl, markers=hook_markers)



        self.txt_console.appendPlainText(f"📁 [Xuất Toàn Bộ Timeline Vào Thư Mục _TIMELINE_IMPORT]:")
        self.txt_console.appendPlainText(f"   🎬 1. Master Timeline Multi-Track XML (DaVinci): {out_fcp7xml}")
        self.txt_console.appendPlainText(f"   🎬 1. Master Timeline Apple FCPXML: {out_fcpxml}")
        if out_hook_fcp7xml:
            self.txt_console.appendPlainText(f"   🔥 2. Dedicated Intro Hook Timeline XML: {out_hook_fcp7xml}")
            self.txt_console.appendPlainText(f"   🔥 2. Dedicated Intro Hook Timeline FCPXML: {out_hook_fcpxml}")
        self.txt_console.appendPlainText(f"   📂 Toàn bộ file lưu tập trung tại: {output_dir}")

        self._set_ui_step_progress(90, "Đang kiểm tra kết nối DaVinci...", 3)
        resolve_auto = ResolveAutomation()
        connected = resolve_auto.connect()

        if connected:
            success = resolve_auto.import_fcpxml_timeline(out_fcp7xml if os.path.exists(out_fcp7xml) else out_fcpxml)
            if success:
                self.txt_console.appendPlainText(f"🎉 Đã nạp thành công Master Timeline '{timeline_name}' vào DaVinci Resolve Studio!")
                self._set_ui_step_progress(100, "✓ Đã tạo xong Timeline!", 4)
                QMessageBox.information(
                    self,
                    "Dựng Phim Hoàn Tất!",
                    f"🎉 Đã nạp thành công Master Timeline '{timeline_name}' lên DaVinci Resolve theo đúng 100% kịch bản của AI Copilot!"
                )
                return

        # Dành cho DaVinci Resolve Free (hoặc khi chưa kết nối API Studio)
        self.txt_console.appendPlainText("💡 [DaVinci Resolve Free Mode] File Timeline FCP7 XML / FCPXML đã sẵn sàng để nạp vào DaVinci Resolve.")
        
        hook_info_html = ""
        if out_hook_fcp7xml:
            hook_info_html = f"🔥 <b>File Intro Hook Riêng Biệt:</b> <code>{os.path.basename(out_hook_fcp7xml)}</code><br>"

        self._set_ui_step_progress(100, "✓ Đã tạo xong Timeline!", 4)
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("🎉 Đã Tạo Xong Timeline Kịch Bản AI!")
        msg_box.setText(
            f"<b>✔ Toàn bộ file Timeline đã được tạo tập trung trong thư mục <code>_TIMELINE_IMPORT</code>!</b><br><br>"
            f"📁 <b>Thư mục chứa:</b> <code>{output_dir}</code><br>"
            f"🎬 <b>File Master Timeline XML:</b> <code>{os.path.basename(out_fcp7xml)}</code><br>"
            f"{hook_info_html}<br>"
            f"<b>🎬 Cách đưa vào DaVinci Resolve (Bản Free & Studio):</b><br>"
            f"1. Mở <b>DaVinci Resolve</b>.<br>"
            f"2. Bấm phím tắt <b>Ctrl + Shift + I</b> <i>(hoặc File ➔ Import Timeline ➔ Import AAF, EDL, XML...)</i>.<br>"
            f"3. Chọn file <b><code>{os.path.basename(out_fcp7xml)}</code></b> (hoặc <code>{os.path.basename(out_hook_fcp7xml) if out_hook_fcp7xml else ''}</code>).<br><br>"
            f"✨ <i>Âm thanh đối thoại Track A1/A2, Tiếng động SFX Track A3, B-Roll Track V2 và Markers đã được liên kết chuẩn 100%!</i>"
        )
        msg_box.setIcon(QMessageBox.Information)
        btn_open_folder = msg_box.addButton("📂 Mở Thư Mục Chứa File", QMessageBox.ActionRole)
        btn_copy_path = msg_box.addButton("📋 Copy Đường Dẫn", QMessageBox.ActionRole)
        btn_ok = msg_box.addButton("Đồng Ý (OK)", QMessageBox.AcceptRole)
        
        msg_box.exec()
        
        if msg_box.clickedButton() == btn_open_folder:
            import subprocess
            subprocess.run(f'explorer /select,"{os.path.abspath(out_fcp7xml)}"', shell=True)
        elif msg_box.clickedButton() == btn_copy_path:
            QApplication.clipboard().setText(os.path.abspath(out_fcp7xml))


    def _auto_detect_video(self):
        resolve_auto = ResolveAutomation()
        self.txt_console.appendPlainText("🔍 Đang kết nối DaVinci Resolve để tự động tìm video...")
        
        if not resolve_auto.connect():
            self.txt_console.appendPlainText("❌ Lỗi: Không thể kết nối tới DaVinci Resolve. Đảm bảo phần mềm đang mở và đã bật API scripting.")
            return
            
        file_paths = resolve_auto.auto_detect_video_paths()
        if file_paths:
            self._reset_workflow_phase()
            self.selected_files = file_paths
            if len(file_paths) == 1:
                self.lbl_file.setText(file_paths[0])
                self.txt_console.appendPlainText(f"✔ Tự động phát hiện video thành công!\n👉 Tệp: {file_paths[0]}")
            else:
                self.lbl_file.setText("; ".join(file_paths))
                self.txt_console.appendPlainText(f"✔ Tự động phát hiện {len(file_paths)} video từ Resolve thành công!")
            self._update_default_chars_limit(file_paths[0])
            self._suggest_whisper_model_for_file(file_paths[0])
            self._check_project_cache_status()
        else:
            self.txt_console.appendPlainText("⚠ Không phát hiện được video nào đang được chọn trong Media Pool hoặc Timeline. Vui lòng chọn thủ công.")

    def _suggest_whisper_model_for_file(self, video_path: str):
        """Tự động gợi ý kích thước model Whisper tối ưu theo thời lượng video."""
        try:
            dur = AudioExtractor.get_audio_duration(video_path)
            if dur > 0:
                self.mini_timeline.set_duration(dur)
            sugg = suggest_whisper_model(dur)
            self.txt_console.appendPlainText(f"💡 [AI Suggestion] {sugg['reason']}")
            # Nếu là video dài, đổi gợi ý sang model tối ưu
            if sugg["is_warning"] and self.combo_model.currentText() == "large-v3":
                self.combo_model.setCurrentText(sugg["suggested_model"])
        except Exception:
            pass

    def _update_default_chars_limit(self, video_path):
        if self.user_customized_limit:
            self.txt_console.appendPlainText("⚙ Giữ nguyên cấu hình giới hạn phụ đề tùy chỉnh của người dùng (Không tự động ghi đè).")
            return

        try:
            mode = self.combo_split_mode.currentData() or "characters"
            is_vert = resolve_api.is_vertical_video(video_path)
            
            if mode == "characters":
                if is_vert:
                    self.txt_split_limit.setText("22")
                    self.txt_console.appendPlainText("📱 Phát hiện Video Dọc: Tự động đổi giới hạn chữ thành 22 ký tự/dòng để vừa khung hình đứng.")
                else:
                    self.txt_split_limit.setText("42")
                    self.txt_console.appendPlainText("🖥 Phát hiện Video Ngang: Tự động đổi giới hạn chữ thành 42 ký tự/dòng (tiêu chuẩn).")
            else:
                if is_vert:
                    self.txt_split_limit.setText("4")
                    self.txt_console.appendPlainText("📱 Phát hiện Video Dọc: Tự động đặt 4 từ/card cho video ngắn Shorts/Reels.")
                else:
                    self.txt_split_limit.setText("7")
                    self.txt_console.appendPlainText("🖥 Phát hiện Video Ngang: Tự động đặt 7 từ/card.")
        except Exception:
            pass

    def _toggle_pipeline_execution(self):
        # Nếu người dùng đã dán sẵn JSON kịch bản vào ô txt_json_input, ưu tiên chạy từ kịch bản này!
        if hasattr(self, "txt_json_input"):
            json_text = self.txt_json_input.toPlainText().strip()
            if json_text.startswith("{") and ("timeline" in json_text or "timeline_segments" in json_text):
                self.txt_console.appendPlainText("🚀 [Phát hiện Kịch bản JSON] Tự động áp dụng kịch bản và xuất Timeline...")
                self._apply_json_text_plan(json_text)
                return
        if self.is_processing:
            self._stop_pipeline()
        else:
            self._run_pipeline()

    def _stop_pipeline(self):
        if self.worker and self.worker.isRunning():
            self.btn_run.setEnabled(False)
            self.btn_run.setText("⏳ ĐANG DỪNG LẠI...")
            if hasattr(self, "btn_stop"):
                self.btn_stop.setEnabled(False)
                self.btn_stop.setText("⏳ Đang dừng...")
            self.txt_console.appendPlainText("🛑 Người dùng yêu cầu hủy tiến trình. Đang tiến hành dừng an toàn...")
            if hasattr(self, "step_tracker"):
                self.step_tracker.mark_stopped()
            self.worker.stop()

    def _on_json_text_changed(self):
        """Tự động kiểm tra và đồng bộ trạng thái khi người dùng dán hoặc sửa kịch bản JSON."""
        txt = self.txt_json_input.toPlainText().strip()
        is_json = txt.startswith("{") and ("timeline" in txt or "timeline_segments" in txt or "clips" in txt)
        if is_json:
            if hasattr(self, "lbl_json_hint"):
                self.lbl_json_hint.setText("✓ Đã nhận diện Kịch bản JSON · Sẵn sàng thi công!")
                self.lbl_json_hint.setStyleSheet("color: #22d3ee; font-weight: bold; font-size: 11px;")
        else:
            if hasattr(self, "lbl_json_hint"):
                self.lbl_json_hint.setText("💡 Dán JSON kịch bản vào ô trên · Nút [Bắt đầu dựng] bên dưới sẽ tự động kích hoạt")
                self.lbl_json_hint.setStyleSheet("color: #71717a; font-size: 11px;")
        if not getattr(self, "is_processing", False):
            self._update_run_button_state(running=False)

    def _minimize_to_bubble(self):
        """Thu nhỏ ứng dụng thành widget bong bóng nổi luôn trên cùng."""
        self.hide()
        if self.bubble.pos().x() == 0 and self.bubble.pos().y() == 0:
            screen = QApplication.primaryScreen().geometry()
            self.bubble.move(screen.width() - 240, 100)
        if self.bubble: self.bubble.show()
        self.bubble.raise_()

    def _restore_from_bubble(self):
        """Khôi phục lại giao diện đầy đủ từ bong bóng nổi."""
        self.bubble.hide()
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.activateWindow()
        self.raise_()

    def closeEvent(self, event):
        if hasattr(self, "bubble") and self.bubble:
            self.bubble.close()
        super().closeEvent(event)

    def _update_run_button_state(self, running: bool):
        if hasattr(self, "btn_scan_only"):
            self.btn_scan_only.setEnabled(not running)
        if hasattr(self, "btn_instant_export"):
            self.btn_instant_export.setEnabled(not running)
        if hasattr(self, "btn_stop"):
            self.btn_stop.setVisible(running)
            self.btn_stop.setEnabled(running)
            self.btn_stop.setText("⏹ Dừng tiến trình")

        if running:
            self.btn_run.setEnabled(True)
            self.btn_run.setText("⏹ DỪNG LẠI (STOP / CANCEL)")
            self.btn_run.setStyleSheet("""
                QPushButton#btn_run {
                    background-color: #DC2626;
                    color: white;
                    padding: 10px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton#btn_run:hover {
                    background-color: #B91C1C;
                }
            """)
        else:
            self.btn_run.setEnabled(True)
            has_json = False
            if hasattr(self, "txt_json_input"):
                txt = self.txt_json_input.toPlainText().strip()
                if txt.startswith("{") and ("timeline" in txt or "timeline_segments" in txt or "clips" in txt):
                    has_json = True

            if has_json:
                self.btn_run.setText("🎬 DỰNG THEO KỊCH BẢN JSON (0.1s)")
                self.btn_run.setStyleSheet("""
                    QPushButton#btn_run {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0891b2, stop:1 #06b6d4);
                        color: #042f2e;
                        padding: 10px;
                        border-radius: 6px;
                        font-weight: bold;
                        font-size: 13px;
                    }
                    QPushButton#btn_run:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #06b6d4, stop:1 #22d3ee);
                    }
                """)
            elif getattr(self, "current_phase", 1) == 2:
                self.btn_run.setText("🎬 XUẤT TIMELINE & DAVINCI RESOLVE")
                self.btn_run.setStyleSheet("""
                    QPushButton#btn_run {
                        background-color: #0288D1;
                        color: white;
                        padding: 12px;
                        border-radius: 6px;
                        font-weight: bold;
                    }
                    QPushButton#btn_run:hover {
                        background-color: #03A9F4;
                    }
                """)
            else:
                raw_paths = getattr(self, "selected_files", [])
                cache_mgr = ScanCacheManager()
                model = self.combo_model.currentText() if hasattr(self, "combo_model") else "small"
                lang = self.combo_lang.currentText() if hasattr(self, "combo_lang") else "vi"
                total = len(raw_paths)
                cached_count = sum(1 for vp in raw_paths if cache_mgr.get_cached_scan(vp, model, lang) is not None) if total > 0 else 0

                if total > 0 and cached_count == 0:
                    self.btn_run.setText("🚀 QUÉT NGUỒN & BẮT ĐẦU DỰNG")
                elif total > 0 and cached_count < total:
                    self.btn_run.setText(f"⚡ QUÉT TIẾP & BẮT ĐẦU DỰNG ({cached_count}/{total})")
                elif total > 0 and cached_count == total:
                    self.btn_run.setText("🎬 Bắt đầu dựng (Dùng Cache 0.1s)")
                else:
                    self.btn_run.setText("🎬 Bắt đầu dựng")

                self.btn_run.setStyleSheet("""
                    QPushButton#btn_run {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #06b6d4);
                        color: white;
                        padding: 10px;
                        border-radius: 6px;
                        font-weight: bold;
                        font-size: 13px;
                    }
                    QPushButton#btn_run:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a78bfa, stop:1 #22d3ee);
                    }
                """)

    def _set_workflow_stage(self, stage: int):
        """Chuyển đổi trạng thái giao diện giữa Bước 1 (Quét & Cache) và Bước 2 (Dựng & Xuất)."""
        self.workflow_stage = stage
        if stage == 1:
            if hasattr(self, "stage_1_container"):
                self.stage_1_container.show()
            if hasattr(self, "stage_2_wrapper"):
                self.stage_2_wrapper.hide()
            elif hasattr(self, "tab_widget"):
                self.tab_widget.hide()
            if hasattr(self, "btn_stage_1"):
                self.btn_stage_1.setStyleSheet("""
                    QPushButton {
                        background-color: #2c9dac;
                        color: white;
                        font-weight: bold;
                        border: 2px solid #3dcee1;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-size: 12px;
                    }
                """)
            if hasattr(self, "btn_stage_2"):
                self.btn_stage_2.setStyleSheet("""
                    QPushButton {
                        background-color: #031e22;
                        color: #39c1d3;
                        border: 1px solid #0c3d44;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background-color: #0c3d44;
                        color: #E2E8F0;
                    }
                """)
        else:
            if hasattr(self, "stage_1_container"):
                self.stage_1_container.hide()
            if hasattr(self, "stage_2_wrapper"):
                self.stage_2_wrapper.show()
            elif hasattr(self, "tab_widget"):
                self.tab_widget.show()
            if hasattr(self, "btn_stage_1"):
                self.btn_stage_1.setStyleSheet("""
                    QPushButton {
                        background-color: #031e22;
                        color: #39c1d3;
                        border: 1px solid #0c3d44;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background-color: #0c3d44;
                        color: #E2E8F0;
                    }
                """)
            if hasattr(self, "btn_stage_2"):
                self.btn_stage_2.setStyleSheet("""
                    QPushButton {
                        background-color: #059669;
                        color: white;
                        font-weight: bold;
                        border: 2px solid #34D399;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-size: 12px;
                    }
                """)

    def _start_phase_1_scan(self):
        """Chạy riêng Phase 1: Quét nguồn, Whisper STT, Visual Activity & Nạp Cache."""
        if self.is_processing:
            self._stop_pipeline()
            return
        self.txt_console.appendPlainText("🔍 [Phase 1 Scan] Bắt đầu quét nguồn độc lập và nạp bộ nhớ đệm...")
        self.current_phase = 1
        self._run_pipeline(forced_phase=1)

    def _start_instant_export(self):
        """Chạy riêng Phase 2: Áp dụng kịch bản và xuất Timeline trong 0.1s từ Cache."""
        if self.is_processing:
            self._stop_pipeline()
            return

        raw_paths = getattr(self, "selected_files", [])
        if not raw_paths:
            t_path = self.lbl_file.text().strip()
            if t_path:
                raw_paths = [p.strip() for p in t_path.split(";") if p.strip()]
        video_paths = [os.path.abspath(p) for p in raw_paths if os.path.exists(p)]
        video_paths = list(dict.fromkeys(video_paths))
        if not video_paths:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn hoặc thư mục dự án trước khi xuất timeline.")
            return

        cache_mgr = ScanCacheManager()
        has_all_disk_cache = all(
            cache_mgr.get_cached_scan(vp, self.combo_model.currentText(), self.combo_lang.currentText()) is not None
            for vp in video_paths
        )

        if not self.clip_data_cache and not has_all_disk_cache:
            self.txt_console.appendPlainText("ℹ Chưa phát hiện bộ nhớ cache sẵn có cho video này. Đang tự động quét nguồn (Phase 1) trước...")
            self.current_phase = 1
            self._run_pipeline(forced_phase=0)
            return

        self.txt_console.appendPlainText("⚡ [Instant Export - 0.1s] Nạp dữ liệu từ Scan Cache và xuất bản theo kịch bản mới...")
        self.current_phase = 2
        self._run_pipeline(forced_phase=2)

    def _on_worker_progress(self, val: int):
        self.progress_bar.setValue(val)
        if hasattr(self, "lbl_pct_big"):
            self.lbl_pct_big.setText(f"{val}%")
            self.lbl_pct_big.setStyleSheet("font-size: 32px; font-weight: bold; color: #f4f4f5;")
        
        # Cập nhật trạng thái từng bước trên danh sách bước bên phải
        if hasattr(self, "step_tracker"):
            if val < 6:
                self.step_tracker.set_step_status("validate", "running")
            elif val < 12:
                self.step_tracker.set_step_status("load_model", "running")
            elif val < 65:
                self.step_tracker.set_step_status("speech_to_text", "running")
                self.step_tracker.update_step_progress("speech_to_text", val)
            elif val < 82:
                self.step_tracker.set_step_status("ai_director", "running")
            elif val < 94:
                self.step_tracker.set_step_status("apply_cut", "running")
            else:
                self.step_tracker.set_step_status("export", "running")

            if val >= 100:
                for k in self.step_tracker.step_keys:
                    self.step_tracker._set_row_done(k)

        # Cập nhật thanh tiến độ cache bên cột trái nếu đang quét/nạp
        if hasattr(self, "cache_progress_bar") and getattr(self, "is_processing", False):
            if getattr(self, "current_phase", 1) == 1 and val > self.cache_progress_bar.value():
                self.cache_progress_bar.setValue(val)
                if hasattr(self, "lbl_cache_badge"):
                    self.lbl_cache_badge.setText(f"⏳ Đang nạp ({val}%)")
                    self.lbl_cache_badge.setStyleSheet("background-color: rgba(139, 92, 246, 0.2); color: #c4b5fd; border: 1px solid #8b5cf6; padding: 2px 8px; border-radius: 999px; font-weight: bold; font-size: 11px;")

        status_txt = self.lbl_eta.text() if hasattr(self, "lbl_eta") else ""
        if hasattr(self, "lbl_progress_status"):
            status_txt = self.lbl_progress_status.text()
        if hasattr(self, "bubble") and self.bubble:
            self.bubble.update_progress(val, status_txt)

    def _run_pipeline(self, forced_phase=None):
        # Ưu tiên lấy từ self.selected_files nếu hợp lệ, nếu không lấy từ text trên giao diện
        raw_paths = getattr(self, "selected_files", [])
        if not raw_paths:
            t_path = self.lbl_file.text().strip()
            if t_path:
                raw_paths = [p.strip() for p in t_path.split(";") if p.strip()]

        video_paths = [os.path.abspath(p) for p in raw_paths if os.path.exists(p)]
        video_paths = list(dict.fromkeys(video_paths))

        if not video_paths:
            self.txt_console.appendPlainText("❌ Lỗi: Vui lòng chọn tệp video hợp lệ trước khi khởi chạy!")
            return

        if len(video_paths) == 1:
            self.txt_console.appendPlainText(f"🎬 Video nguồn đang xử lý: {os.path.basename(video_paths[0])}")
        else:
            self.txt_console.appendPlainText(f"🎬 Danh sách {len(video_paths)} video nguồn: {', '.join(os.path.basename(p) for p in video_paths)}")

        self.is_processing = True
        self.progress_bar.setValue(0)
        if hasattr(self, "bubble") and self.bubble:
            if self.bubble: self.bubble.update_progress(0, "Đang khởi động...")

        model_size = self.combo_model.currentText()
        language = self.combo_lang.currentText()
        ai_mode = self.combo_ai_mode.currentData() or "clean_talk"
        remove_bad_takes = self.check_bad_takes.isChecked()
        enable_punch_in = self.check_punch_in.isChecked()
        enable_reframe = self.check_reframe.isChecked()
        enable_broll = self.check_broll.isChecked()
        enable_sfx = self.check_sfx.isChecked()
        run_cut = self.check_cut.isChecked()
        speed_up_silence = self.check_speedup.isChecked()
        silence_db = float(self.slide_db.value())
        min_duration = float(self.slide_dur.value() / 10.0)
        
        split_mode = self.combo_split_mode.currentData() or "characters"
        try:
            split_limit = int(self.txt_split_limit.text())
        except ValueError:
            split_limit = 42 if split_mode == "characters" else 6

        font_name = self.txt_font.text()
        try:
            font_size = int(self.txt_size.text())
        except ValueError:
            font_size = 48

        enable_vlog_hook = self.check_vlog_hook.isChecked()
        vlog_hook_duration = float(self.combo_hook_dur.currentData() or 2.0)
        enable_subtitles = self.check_subtitle.isChecked()
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        use_cache = self.check_cache.isChecked()
        enable_loudnorm = self.check_loudnorm.isChecked() if hasattr(self, "check_loudnorm") else False
        loudnorm_preset = self.combo_loudnorm_preset.currentData() if hasattr(self, "combo_loudnorm_preset") else "youtube_tiktok"

        if forced_phase is not None:
            phase_to_run = forced_phase
        else:
            is_semantic_ai = run_cut and (ai_mode != "silence_only")
            if is_semantic_ai:
                phase_to_run = self.current_phase
                # Đảm bảo nếu người dùng đổi tệp video, cache Phase 1 của clip cũ sẽ bị hủy bỏ ngay
                cached_paths = [os.path.abspath(c.get("video_path", "")) for c in (self.clip_data_cache or [])]
                if phase_to_run == 2 and cached_paths != video_paths:
                    self._reset_workflow_phase()
                    phase_to_run = 1
            else:
                phase_to_run = 0

        if phase_to_run == 2:
            for row in range(self.table_review.rowCount()):
                chk_box = self.table_review.cellWidget(row, 0)
                if chk_box:
                    cb = chk_box.findChild(QCheckBox)
                    if cb:
                        self.proposed_segments[row].approved = cb.isChecked()

        self._update_run_button_state(running=True)
        self.step_progress.reset()

        self.worker = PipelineWorker(
            video_paths=video_paths,
            model_size=model_size,
            language=language,
            run_cut=run_cut,
            silence_db=silence_db,
            min_duration=min_duration,
            split_mode=split_mode,
            split_limit=split_limit,
            font_name=font_name,
            font_size=font_size,
            ai_mode=ai_mode,
            remove_bad_takes=remove_bad_takes,
            enable_punch_in=enable_punch_in,
            punch_in_scale=1.15,
            enable_reframe=enable_reframe,
            enable_broll=enable_broll,
            enable_sfx=enable_sfx,
            speed_up_silence=speed_up_silence,
            silence_speed=8.0,
            enable_vlog_hook=enable_vlog_hook,
            vlog_hook_duration=vlog_hook_duration,
            enable_subtitles=enable_subtitles,
            phase=phase_to_run,
            proposed_segments_override=self.proposed_segments,
            clip_data_cache=self.clip_data_cache,
            text_preset_id=preset_id,
            use_cache=use_cache,
            enable_loudnorm=enable_loudnorm,
            loudnorm_preset=loudnorm_preset,
            pacing=self.combo_pacing.currentData() or "balanced",
            remove_repeated_phrases=self.check_repeats.isChecked(),
            scene_guard=self.check_scene_guard.isChecked(),
            fill_gaps=self.check_fill_gaps.isChecked(),
            vlog_hook_total=float(self.combo_hook_total.currentData() or 20.0),
            story_intent=self.combo_story_intent.currentData() or "keep",
            story_target=float(self.combo_story_target.currentData() or 60.0),
            video_type=self.combo_video_type.currentData() or "auto",
            hide_weak_subs=self.check_hide_weak_subs.isChecked(),
            api_key=self.txt_api_key.text().strip() or None,
            story_arrangement_override=self.story_review_state.arrangement if getattr(self, "story_review_state", None) else None,
            project_structure=self.project_structure
        )

        self.worker.log_signal.connect(self._log_message)
        self.worker.step_signal.connect(self.step_progress.set_step_status)
        self.worker.progress_signal.connect(self._on_worker_progress)
        self.worker.finished_signal.connect(self._pipeline_finished)

        self.worker.start()

    def _format_user_friendly_error(self, err_msg: str) -> str:
        """Chuyển đổi lỗi kỹ thuật thô thành hướng dẫn khắc phục thân thiện cho creator."""
        err_lower = err_msg.lower()

        # Kiểm tra lỗi nội bộ mã nguồn / hệ thống (UnboundLocalError, ImportError, AttributeError...)
        is_system_error = any(
            err_type in err_msg for err_type in [
                "UnboundLocalError", "NameError", "ImportError", "AttributeError",
                "TypeError", "SyntaxError", "IndexError", "KeyError", "ZeroDivisionError"
            ]
        )
        if is_system_error:
            clean_err = err_msg.strip()
            return (
                f"❌ Đã xảy ra lỗi nội bộ trong hệ thống xử lý:\n👉 {clean_err}\n\n"
                "ℹ️ Đây là lỗi mã nguồn / nội bộ của ứng dụng, không phải do dữ liệu video của bạn.\n"
                "👉 Vui lòng sao chép thông báo này kèm tệp nhật ký '_BaoCao_NhatKyXuLy.md' hoặc log console gửi cho nhà phát triển để được hỗ trợ xử lý."
            )

        if "ffmpeg" in err_lower:
            return (
                "❌ Không tìm thấy công cụ FFmpeg trong hệ thống.\n\n"
                "👉 Cách khắc phục:\n"
                "1. Tải FFmpeg từ https://ffmpeg.org/download.html (hoặc bản build sẵn).\n"
                "2. Thêm thư mục chứa ffmpeg.exe vào biến môi trường PATH của Windows hoặc đặt file ffmpeg.exe cạnh tệp main.py."
            )
        elif "resolve" in err_lower or "davinci" in err_lower or "scripting" in err_lower:
            return (
                "❌ Không thể kết nối với DaVinci Resolve.\n\n"
                "👉 Cách khắc phục:\n"
                "1. Mở phần mềm DaVinci Resolve (bản Studio hoặc Free).\n"
                "2. Vào menu DaVinci Resolve -> Preferences -> General -> Bật 'External scripting using: Local/Network'.\n"
                "3. Mở sẵn một Project và Timeline trong Resolve rồi chạy lại."
            )
        elif "cuda" in err_lower or "out of memory" in err_lower:
            return (
                "❌ Tràn bộ nhớ GPU hoặc không tìm thấy CUDA tương thích.\n\n"
                "👉 Cách khắc phục:\n"
                "1. Chọn kích thước mô hình AI nhỏ hơn (ví dụ 'tiny' hoặc 'base') trong Cấu hình AI Whisper.\n"
                "2. Đóng bớt các ứng dụng nặng đang chiếm GPU rồi thử lại."
            )
        elif "no such file" in err_lower or "not found" in err_lower or "chọn tệp" in err_lower:
            return (
                "❌ Không tìm thấy tệp video nguồn.\n\n"
                "👉 Cách khắc phục:\n"
                "Kiểm tra lại đường dẫn video, đảm bảo tệp chưa bị xóa hoặc đổi tên."
            )
        elif "fcpxml" in err_lower or "xml" in err_lower:
            return (
                "❌ Lỗi định dạng FCPXML khi gửi sang DaVinci Resolve.\n\n"
                "👉 Cách khắc phục:\n"
                "Kiểm tra tên tệp video không chứa các ký tự đặc biệt lạ, hoặc thử nhập thủ công tệp .fcpxml đã được tạo trong thư mục dự án."
            )
        else:
            clean_err = err_msg.splitlines()[-1] if err_msg.splitlines() else err_msg
            return (
                f"❌ Gặp sự cố trong quá trình xử lý: {clean_err}\n\n"
                "👉 Gợi ý: Kiểm tra lại định dạng tệp video nguồn hoặc thử chạy với mô hình AI 'tiny'."
            )

    @pyqtSlot(str)
    def _log_message(self, message):
        self.txt_console.appendPlainText(message)
        msg_l = message.lower()
        status_str = None
        if "khởi động" in msg_l:
            status_str = "🚀 Đang khởi động..."
        elif "dry-run" in msg_l or "tương thích" in msg_l:
            status_str = "🔍 Kiểm tra tệp nguồn..."
        elif "tải mô hình" in msg_l:
            status_str = "🤖 Nạp Whisper AI..."
        elif "trích xuất audio" in msg_l:
            status_str = "🔊 Trích xuất âm thanh..."
        elif "quét giọng nói" in msg_l or "dịch giọng nói" in msg_l or "đang nghe" in msg_l:
            if "đang nghe:" in msg_l:
                clean_txt = message.strip()
                status_str = clean_txt if clean_txt.startswith("🎙") else f"🎙️ {clean_txt}"
            else:
                status_str = "🎙 Nhận diện giọng nói..."
        elif "scan cache hit" in msg_l:
            status_str = "⚡ Nạp Cache siêu tốc (0.05s)!"
        elif "lọc khoảng lặng" in msg_l or "phân tích khoảng lặng" in msg_l or "speed-ramp" in msg_l:
            status_str = "✂ Phân tích khoảng lặng..."
        elif "ai director" in msg_l:
            status_str = "🎬 Đạo diễn AI..."
        elif "vlog hook" in msg_l:
            status_str = "🔥 Tạo Teaser / Hook..."
        elif "reframe" in msg_l:
            status_str = "👁 Bám mặt dọc 9:16..."
        elif "b-roll" in msg_l:
            status_str = "🎞 Gợi ý B-Roll / Meme..."
        elif "sfx" in msg_l:
            status_str = "🔊 Chèn âm thanh SFX..."
        elif "tạo tệp timeline" in msg_l or "fcpxml" in msg_l:
            status_str = "📝 Tạo Timeline XML/EDL..."
        elif "import timeline" in msg_l or "gửi yêu cầu" in msg_l:
            status_str = "🤖 Gửi DaVinci Resolve..."
        elif "hoàn tất" in msg_l or "xong" in msg_l:
            status_str = "🏁 Hoàn tất thành công!"

        if status_str:
            if hasattr(self, "lbl_progress_status"):
                self.lbl_progress_status.setText(status_str)
            if hasattr(self, "lbl_eta"):
                self.lbl_eta.setText(status_str)
            if hasattr(self, "bubble") and self.bubble:
                if self.bubble: self.bubble.update_progress(self.progress_bar.value(), status_str)

    @pyqtSlot(bool, str)
    def _pipeline_finished(self, success, message):
        self.is_processing = False
        self.btn_run.setEnabled(True)
        self._update_run_button_state(running=False)

        if hasattr(self, "bubble") and self.bubble:
            if success:
                if self.bubble: self.bubble.update_progress(100, "Hoàn tất!" if message != "phase1_done" else "Đã quét xong!")
            else:
                if self.bubble: self.bubble.update_progress(0, "Đã dừng" if ("dừng" in message.lower() or "cancel" in message.lower()) else "Sự cố")
        
        if success:
            self._check_project_cache_status()
            if message == "phase1_done":
                self._set_workflow_stage(2)
                self.lbl_progress_status.setText("✔ Đã quét nguồn & nạp cache! Bạn có thể chọn kịch bản và bấm 'Xuất Timeline Ngay (0.1s)'.")
                self.proposed_segments = self.worker.proposed_segments
                self.clip_data_cache = self.worker.clip_data_out_cache
                self.validation_warnings = self.worker.validation_warnings
                
                if self.validation_warnings:
                    QMessageBox.warning(
                        self, "Cảnh báo tương thích nguồn (Dry-run)",
                        "Phát hiện các vấn đề tương thích file nguồn:\n\n" + 
                        "\n".join([f"- {w}" for w in self.validation_warnings]) + 
                        "\n\nVui lòng xem xét kỹ trước khi tiến hành xuất bản."
                    )
                
                self._populate_review_table()
                self._set_review_visible(True)
                
                self.txt_console.appendPlainText("\n✔ [AI Director] Phân tích hoàn tất! Dữ liệu đã lưu vào Cache.")
                self.txt_console.appendPlainText("👉 Bạn có thể đổi ý đồ kịch bản (AIDA, Shorts, Clean Talk) tùy thích.")
                self.txt_console.appendPlainText("👉 Bấm '⚡ 2. Xuất Timeline (0.1s)' hoặc '🎬 XUẤT TIMELINE & DAVINCI RESOLVE' để xuất ngay!")
                
                self.current_phase = 2
                self.btn_run.setText("🎬 XUẤT TIMELINE & DAVINCI RESOLVE")
                self.btn_run.setStyleSheet("""
                    QPushButton#btn_run {
                        background-color: #0288D1;
                        color: white;
                        padding: 12px;
                        border-radius: 6px;
                        font-weight: bold;
                    }
                    QPushButton#btn_run:hover {
                        background-color: #03A9F4;
                    }
                """)

                # Cập nhật Mini Timeline trực quan
                if self.clip_data_cache:
                    cdata = self.clip_data_cache[0]
                    self.mini_timeline.set_timeline_data(
                        duration=cdata.get("clip_dur", 60.0),
                        keep_intervals=cdata.get("silence_keep_intervals"),
                        speedup_segments=cdata.get("speedup_segments"),
                        subtitles=cdata.get("raw_subtitles")
                    )
                    self.mini_timeline.update_proposed_segments(self.proposed_segments)
            elif message == "phase2_done" or message == "Hoàn thành!":
                self.lbl_progress_status.setText("🏁 Khởi chạy hoàn tất. Đã xuất bản lên DaVinci Resolve!")
                self.txt_console.appendPlainText("🏁 Khởi chạy hoàn tất. Đã xuất bản hoàn chỉnh lên DaVinci Resolve!")
                self._set_review_visible(False)
                
                # Nạp Story Review Table nếu có kết quả sắp xếp kịch bản
                story_blocks = getattr(self.worker, "story_blocks_out", [])
                story_arr = getattr(self.worker, "story_arrangement_out", None)
                if story_blocks and story_arr and story_arr.items:
                    self.story_blocks = story_blocks
                    self.story_arrangement = story_arr
                    try:
                        tgt = float(self.combo_story_target.currentData() or 60.0)
                    except (ValueError, TypeError):
                        tgt = 60.0
                    self._populate_story_review_table(story_blocks, story_arr, target_seconds=tgt)

                # Giữ ấm clip_data_cache từ worker để cho phép xuất lại tức thì với kịch bản khác
                if getattr(self.worker, "clip_data_out_cache", None):
                    self.clip_data_cache = self.worker.clip_data_out_cache
                self.current_phase = 1
                self.proposed_segments = []
        else:
            if "dừng" in message.lower() or "cancel" in message.lower():
                self.lbl_progress_status.setText("🛑 Đã dừng tiến trình theo yêu cầu.")
                self.txt_console.appendPlainText(f"🛑 {message}")
            else:
                self.lbl_progress_status.setText("❌ Tiến trình gặp sự cố.")
                friendly_msg = self._format_user_friendly_error(message)
                self.txt_console.appendPlainText(f"\n{friendly_msg}\n")
                QMessageBox.critical(self, "Thông báo sự cố", friendly_msg)

    def _on_mini_timeline_playhead_changed(self, sec: float):
        """Khi người dùng kéo/click con trỏ Playhead trên Mini Timeline."""
        tc = seconds_to_timecode(sec, fps=getattr(self.mini_timeline, "fps", 30.0))
        self.txt_console.appendPlainText(f"⏱ [Playhead] Mốc thời gian: {tc} ({sec:.2f}s)")

    def _on_review_check_toggled(self, row_idx: int, checked: bool):
        """Khi người dùng tick/bỏ tick duyệt một phân đoạn trong bảng Phase 1 Review."""
        if 0 <= row_idx < len(self.proposed_segments):
            self.review_state.set_approved([row_idx], checked)
            self._after_review_change()

    def _populate_review_table(self):
        try:
            threshold = float(self.txt_confidence_threshold.text())
        except ValueError:
            threshold = 0.70

        self.table_review.setRowCount(len(self.proposed_segments))
        for row, p in enumerate(self.proposed_segments):
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            
            # Độ tin cậy của CHỮ không phải lý do để cắt: câu tin cậy thấp chỉ được tô vàng để xem lại
                
            chk.setChecked(p.approved)
            chk.toggled.connect(lambda c, r=row: self._on_review_check_toggled(r, c))
            chk_layout.addWidget(chk)
            self.table_review.setCellWidget(row, 0, chk_widget)

            self.table_review.setItem(row, 1, QTableWidgetItem(str(p.id)))
            self.table_review.setItem(row, 2, QTableWidgetItem(f"{p.start:.3f}s"))
            self.table_review.setItem(row, 3, QTableWidgetItem(f"{p.end:.3f}s"))
            self.table_review.setItem(row, 4, QTableWidgetItem(p.reason))
            self.table_review.setItem(row, 5, QTableWidgetItem(f"{p.confidence * 100:.1f}%"))
            self.table_review.setItem(row, 6, QTableWidgetItem(p.text))

            if p.decision == "cut":
                color = QColor(100, 30, 30)
            elif p.confidence < threshold:
                color = QColor(100, 80, 30)
            else:
                color = None

            if color:
                for col in range(1, 7):
                    item = self.table_review.item(row, col)
                    if item:
                        item.setBackground(color)

        self.review_state = ReviewState(self.proposed_segments)
        self._apply_review_filter()
        self._refresh_review_ui()

    # --- REVIEW TOOLBAR: thao tác hàng loạt, Hoàn tác, lọc, tóm tắt ---
    def _build_review_toolbar(self) -> QWidget:
        bar = QWidget()
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.btn_review_keep_all = QPushButton("✔ Giữ tất cả")
        self.btn_review_cut_all = QPushButton("✖ Cắt tất cả")
        self.btn_review_invert = QPushButton("⇄ Đảo chọn")
        self.btn_review_reset = QPushButton("🤖 Về đề xuất AI")
        self.btn_review_undo = QPushButton("↶ Hoàn tác")
        self.btn_review_redo = QPushButton("↷ Làm lại")
        for b in (self.btn_review_keep_all, self.btn_review_cut_all, self.btn_review_invert,
                  self.btn_review_reset, self.btn_review_undo, self.btn_review_redo):
            row.addWidget(b)
        self.btn_review_keep_all.clicked.connect(lambda: self._review_bulk(self.review_state.keep_all))
        self.btn_review_cut_all.clicked.connect(lambda: self._review_bulk(self.review_state.cut_all))
        self.btn_review_invert.clicked.connect(lambda: self._review_bulk(self.review_state.invert))
        self.btn_review_reset.clicked.connect(lambda: self._review_bulk(self.review_state.restore_ai_suggestion))
        self.btn_review_undo.clicked.connect(self._review_undo)
        self.btn_review_redo.clicked.connect(self._review_redo)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.check_review_attention = QCheckBox("Chỉ hiện câu cần xem (AI không chắc / đề xuất cắt)")
        self.check_review_attention.toggled.connect(self._apply_review_filter)
        self.lbl_review_summary = QLabel("")
        self.lbl_review_summary.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-weight: bold;")
        row2.addWidget(self.check_review_attention)
        row2.addStretch(1)
        row2.addWidget(self.lbl_review_summary)
        lay.addLayout(row2)

        hint = QLabel("💡 Bấm dòng để nhảy tới mốc đó trên Mini Timeline • Space: đảo Giữ/Cắt các dòng đang chọn • Ctrl+Z / Ctrl+Y: hoàn tác / làm lại")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(hint)
        return bar

    def _set_review_visible(self, visible: bool):
        self.table_review.setVisible(visible)
        self.review_toolbar.setVisible(visible)
        if visible:
            self._refresh_review_ui()

    def _selected_review_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.table_review.selectionModel().selectedRows()})

    def _sync_review_checks(self):
        """Đồng bộ ô tích trên bảng theo trạng thái thật mà không phát lại tín hiệu toggled."""
        for row in range(self.table_review.rowCount()):
            widget = self.table_review.cellWidget(row, 0)
            cb = widget.findChild(QCheckBox) if widget else None
            if cb is not None and row < len(self.proposed_segments):
                cb.blockSignals(True)
                cb.setChecked(self.proposed_segments[row].approved)
                cb.blockSignals(False)

    def _refresh_review_ui(self):
        st = self.review_state.summary()
        self.lbl_review_summary.setText(
            f"Giữ {st['kept_count']}/{st['total_count']} câu • còn {st['kept_seconds']:.0f}s / {st['total_seconds']:.0f}s "
            f"(rút gọn {st['saved_percent']:.0f}%)"
        )
        self.btn_review_undo.setEnabled(self.review_state.can_undo)
        self.btn_review_redo.setEnabled(self.review_state.can_redo)

    def _after_review_change(self):
        self._sync_review_checks()
        self.mini_timeline.update_proposed_segments(self.proposed_segments)
        self._refresh_review_ui()

    def _review_bulk(self, action):
        action()
        self._after_review_change()

    def _review_toggle_selected(self):
        rows = self._selected_review_rows()
        if rows:
            self.review_state.toggle(rows)
            self._after_review_change()

    def _review_undo(self):
        if self.review_state.undo():
            self._after_review_change()

    def _review_redo(self):
        if self.review_state.redo():
            self._after_review_change()

    def _on_review_selection_changed(self):
        rows = self._selected_review_rows()
        if rows and rows[0] < len(self.proposed_segments):
            self.mini_timeline.set_playhead_seconds(self.proposed_segments[rows[0]].start)

    def _review_threshold(self) -> float:
        try:
            return float(self.txt_confidence_threshold.text())
        except ValueError:
            return 0.70

    def _apply_review_filter(self, *_):
        only_attention = self.check_review_attention.isChecked()
        show = set(self.review_state.needs_attention(self._review_threshold())) if only_attention else None
        for row in range(self.table_review.rowCount()):
            self.table_review.setRowHidden(row, show is not None and row not in show)

    # --- STORY BLOCKS REVIEW: Duyệt và tinh chỉnh kịch bản sắp xếp ---
    def _build_story_review_toolbar(self) -> QWidget:
        bar = QWidget()
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.btn_story_move_up = QPushButton("⬆ Lên")
        self.btn_story_move_down = QPushButton("⬇ Xuống")
        self.btn_story_enable_all = QPushButton("✔ Bật hết")
        self.btn_story_disable_all = QPushButton("✖ Tắt hết")
        self.btn_story_reset = QPushButton("🤖 Về đề xuất AI")
        self.btn_story_undo = QPushButton("↶ Hoàn tác")
        self.btn_story_redo = QPushButton("↷ Làm lại")

        for b in (self.btn_story_move_up, self.btn_story_move_down, self.btn_story_enable_all,
                  self.btn_story_disable_all, self.btn_story_reset, self.btn_story_undo, self.btn_story_redo):
            row.addWidget(b)

        self.btn_story_move_up.clicked.connect(self._on_story_move_up)
        self.btn_story_move_down.clicked.connect(self._on_story_move_down)
        self.btn_story_enable_all.clicked.connect(self._story_enable_all)
        self.btn_story_disable_all.clicked.connect(self._story_disable_all)
        self.btn_story_reset.clicked.connect(self._story_reset_ai)
        self.btn_story_undo.clicked.connect(self._story_undo)
        self.btn_story_redo.clicked.connect(self._story_redo)

        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.lbl_story_summary = QLabel("")
        self.lbl_story_summary.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-weight: bold; font-size: 11px;")
        row2.addWidget(self.lbl_story_summary)
        row2.addStretch(1)
        lay.addLayout(row2)

        hint = QLabel("💡 Bấm dòng để nhảy mốc Playhead • Nút ⬆/⬇ hoặc Alt+Up/Down để đổi thứ tự khối • Space: Bật/Tắt khối")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(hint)
        return bar

    def _set_story_review_visible(self, visible: bool):
        self.table_story_review.setVisible(visible)
        self.story_review_toolbar.setVisible(visible)
        if visible:
            self._refresh_story_ui()

    def _populate_story_review_table(self, blocks: Sequence[Block], arrangement: Arrangement, target_seconds: float = 60.0):
        self.story_review_state = StoryReviewState(arrangement, blocks, target_seconds=target_seconds)
        self._render_story_review_rows()
        self._set_story_review_visible(True)

    def _render_story_review_rows(self):
        if not self.story_review_state:
            return
        items = self.story_review_state.items
        blocks_by_id = self.story_review_state.blocks_by_id

        self.table_story_review.setRowCount(len(items))
        for row, it in enumerate(items):
            b = blocks_by_id.get(it.block_id)
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(getattr(it, "enabled", True))
            chk.toggled.connect(lambda c, r=row: self._on_story_review_check_toggled(r, c))
            chk_layout.addWidget(chk)
            self.table_story_review.setCellWidget(row, 0, chk_widget)

            self.table_story_review.setItem(row, 1, QTableWidgetItem(f"#{row + 1}"))
            
            role_txt = ROLE_LABELS.get(it.role, it.role) + (" (mở đầu)" if it.is_copy else "")
            role_item = QTableWidgetItem(role_txt)
            color_name = ROLE_COLORS.get(it.role, "Blue")
            if color_name == "Green":
                role_item.setBackground(QColor(20, 90, 40))
            elif color_name == "Blue":
                role_item.setBackground(QColor(20, 60, 110))
            elif color_name == "Yellow":
                role_item.setBackground(QColor(110, 90, 20))
            elif color_name == "Red":
                role_item.setBackground(QColor(110, 30, 30))
            elif color_name == "Purple":
                role_item.setBackground(QColor(80, 30, 100))
            else:
                role_item.setBackground(QColor(30, 80, 90))
            self.table_story_review.setItem(row, 2, role_item)

            dur = it.t1 - it.t0
            dur_item = QTableWidgetItem(f"{dur:.1f}s ({it.t0:.1f}s ➔ {it.t1:.1f}s)")
            self.table_story_review.setItem(row, 3, dur_item)

            reason_item = QTableWidgetItem(it.reason or (b.reason if b else ""))
            self.table_story_review.setItem(row, 4, reason_item)

            text_snippet = (b.text if b and b.text else "(không lời / cảnh b-roll)")
            txt_item = QTableWidgetItem(text_snippet)
            self.table_story_review.setItem(row, 5, txt_item)

        self._refresh_story_ui()

    def _on_story_review_check_toggled(self, row_idx: int, checked: bool):
        if self.story_review_state and 0 <= row_idx < len(self.story_review_state.items):
            self.story_review_state.set_enabled(row_idx, checked)
            self._after_story_change()

    def _selected_story_row(self) -> Optional[int]:
        rows = self.table_story_review.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _on_story_move_up(self):
        row = self._selected_story_row()
        if row is not None and self.story_review_state:
            if self.story_review_state.move_up(row):
                self._render_story_review_rows()
                self.table_story_review.selectRow(max(0, row - 1))
                self._after_story_change()

    def _on_story_move_down(self):
        row = self._selected_story_row()
        if row is not None and self.story_review_state:
            if self.story_review_state.move_down(row):
                self._render_story_review_rows()
                self.table_story_review.selectRow(min(len(self.story_review_state.items) - 1, row + 1))
                self._after_story_change()

    def _story_toggle_selected(self):
        row = self._selected_story_row()
        if row is not None and self.story_review_state:
            self.story_review_state.toggle_enabled(row)
            self._after_story_change()

    def _story_enable_all(self):
        if self.story_review_state:
            self.story_review_state.enable_all()
            self._after_story_change()

    def _story_disable_all(self):
        if self.story_review_state:
            self.story_review_state.disable_all()
            self._after_story_change()

    def _story_reset_ai(self):
        if self.story_review_state:
            self.story_review_state.restore_ai_plan()
            self._render_story_review_rows()
            self._after_story_change()

    def _story_undo(self):
        if self.story_review_state and self.story_review_state.undo():
            self._render_story_review_rows()
            self._after_story_change()

    def _story_redo(self):
        if self.story_review_state and self.story_review_state.redo():
            self._render_story_review_rows()
            self._after_story_change()

    def _sync_story_checks(self):
        if not self.story_review_state:
            return
        for row in range(self.table_story_review.rowCount()):
            widget = self.table_story_review.cellWidget(row, 0)
            cb = widget.findChild(QCheckBox) if widget else None
            if cb is not None and row < len(self.story_review_state.items):
                cb.blockSignals(True)
                cb.setChecked(getattr(self.story_review_state.items[row], "enabled", True))
                cb.blockSignals(False)

    def _refresh_story_ui(self):
        if not self.story_review_state:
            return
        st = self.story_review_state.summary()
        diff_str = f" ({st['difference_to_target']:+.1f}s)" if st['target_seconds'] > 0 else ""
        self.lbl_story_summary.setText(
            f"🧭 Đang bật {st['enabled_count']}/{st['total_count']} khối • Tổng: {st['total_seconds']:.1f}s / {st['target_seconds']:.0f}s Target{diff_str}"
        )
        self.btn_story_undo.setEnabled(self.story_review_state.can_undo)
        self.btn_story_redo.setEnabled(self.story_review_state.can_redo)

    def _after_story_change(self):
        self._sync_story_checks()
        self._refresh_story_ui()

    def _on_story_review_selection_changed(self):
        row = self._selected_story_row()
        if row is not None and self.story_review_state and row < len(self.story_review_state.items):
            it = self.story_review_state.items[row]
            self.mini_timeline.set_playhead_seconds(it.t0)



def start_gui(): # DEPRECATED
    app = QApplication(sys.argv)
    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)
    window = ChunDVCApp()
    window.show()
    sys.exit(app.exec())

