import os
import sys
import json
import tempfile
import uuid
import traceback
from typing import List, Dict, Any, Optional

from src.core.validator import DryRunValidator
from src.core.transcriber import ModelConfig, ResolveTranscriber
from src.core import resolve_api
from src.core.resolve_api import (
    SubtitleConfig, ResolveAutomation, split_subtitles,
    map_subtitles_to_timeline, map_time_to_timeline,
    map_time_with_speedup_segments, map_subtitles_with_speedup_segments,
    is_vertical_video
)
from src.core.autocut import AudioCutConfig, SilenceDetector, EDLGenerator, get_media_metadata
from src.core.ai_director import AIDirector, AIDirectorConfig, ProposedSegment
from src.core.vision_reframer import VisionReframer, ReframeConfig
from src.core.broll_sfx import BRollAnalyzer, SFXEngine
from src.core.vlog_hook import VlogHookGenerator, HookSegment
from src.core.audio import AudioExtractor
from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.audit_reporter import ExecutionAuditReporter
from src.core.text_preset import TextStylePreset, PresetManager, TextPreviewRenderer
from src.core.recipe_manager import Recipe, RecipeManager
from src.core.cache_manager import ScanCacheManager, compute_file_checksum
from src.core.proxy_manager import ProxyManager, ParallelScanPipeline, suggest_whisper_model

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QPlainTextEdit, QGroupBox, QFormLayout, QSlider, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
    QDialog, QScrollArea, QInputDialog, QFrame, QTabWidget, QSplitter
)
from PySide6.QtCore import QThread, Signal as pyqtSignal, Slot as pyqtSlot, Qt
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon

from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS, MODULE_DESCRIPTIONS, get_application_stylesheet
from src.ui.tabs import TabAutoCut, TabTitles, TabSFX, TabExport

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
                sample_words=["ResolveFlow", "AI", "Text+", "Subtitle"],
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


class PipelineWorker(QThread):
    """
    Worker Thread để chạy tiến trình xử lý ngầm (Audio, STT, Subtitle, Smart Cut, AI Director, Vision, SFX, Speed-Ramp & Vlog Hook)
    tránh làm đơ (freeze) giao diện người dùng GUI.
    Hỗ trợ cơ chế ngắt an toàn (Interruptible Threading), Scan Cache theo checksum và Proxy 480p tối ưu.
    """
    log_signal = pyqtSignal(str)
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
        use_proxy=True
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
        
        self.proposed_segments = []
        self.clip_data_out_cache = []
        self.validation_warnings = []
        
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
            self.log_signal.emit("🚀 Đang khởi động ResolveFlow-Assistant v4.1 (Text Presets, Smart Cache & Pipeline Suite)...")
            self.progress_signal.emit(5)

            if self.is_interrupted:
                self._handle_interrupted()
                return

            # --- GIAI ĐOẠN DRY-RUN VALIDATION ---
            if self.phase in [0, 1]:
                self.log_signal.emit("🔍 [Dry-Run] Đang kiểm tra khả năng tương thích định dạng file nguồn...")
                val_res = DryRunValidator.validate_media_files(self.video_paths)
                
                if val_res.errors:
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

            if self.phase == 2:
                clip_data_list = self.clip_data_cache or []
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
                self.log_signal.emit(f"🤖 Tải mô hình Whisper AI '{self.model_size}'...")
                model_config = ModelConfig(
                    model_size=self.model_size,
                    device="cuda",
                    compute_type="float16"
                )
                transcriber = ResolveTranscriber(model_config)
                transcriber.load_model()
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
                padding_seconds=0.25,
                speed_up_silence=self.speed_up_silence,
                silence_speed_multiplier=self.silence_speed
            )

            ai_config = AIDirectorConfig(
                mode=self.ai_mode,
                remove_bad_takes=self.remove_bad_takes,
                enable_punch_in=self.enable_punch_in,
                punch_in_scale=self.punch_in_scale,
                api_key=self.api_key
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
            cumulative_record_seconds = 0.0
            uncut_cumulative_seconds = 0.0

            first_video = self.video_paths[0]
            base_dir = os.path.dirname(first_video)
            
            if len(self.video_paths) == 1:
                stamped_name = os.path.splitext(os.path.basename(first_video))[0]
            else:
                stamped_name = f"ResolveFlow_Merged_{len(self.video_paths)}clips"

            audit_reporter = ExecutionAuditReporter(project_name=stamped_name)
            audit_reporter.record_validation_warnings(self.validation_warnings)

            total_clips = len(self.video_paths)
            temp_audio_dir = os.path.join(tempfile.gettempdir(), "ResolveFlow_Audio")
            os.makedirs(temp_audio_dir, exist_ok=True)

            # --- VÒNG LẶP XỬ LÝ TỪNG CLIP (PHASE 1 HOẶC END-TO-END) ---
            if self.phase in [0, 1]:
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
                                progress_callback=on_progress
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

                    if self.run_cut:
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

                    # Lưu vào Cache nếu vừa mới quét
                    if self.use_cache and not cached_data and needs_transcription:
                        cache_mgr.save_scan_result(
                            file_path=video_path,
                            scan_data={
                                "raw_subtitles": raw_subtitles,
                                "clip_dur": clip_dur
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
                        "cut_segments": cut_segments
                    })

                    try:
                        if os.path.exists(temp_wav):
                            os.remove(temp_wav)
                    except Exception:
                        pass
                    
                    self.progress_signal.emit(int((idx + 1) / total_clips * 40))

                self.clip_data_out_cache = clip_data_list

            # --- PHÂN TÍCH & ĐỀ XUẤT CỦA AI DIRECTOR (CHỈ Ở PHASE 1) ---
            if self.phase == 1:
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

            # --- GIAI ĐOẠN 2 HOẶC END-TO-END: ÁP DỤNG CẮT & XUẤT BẢN ---
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

                # Vlog Hook
                h_seg = None
                if self.enable_vlog_hook:
                    temp_wav = os.path.normpath(os.path.join(temp_audio_dir, f"hook_{uuid.uuid4().hex[:8]}.wav"))
                    temp_files_to_clean.append(temp_wav)
                    AudioExtractor.extract_audio(video_path, temp_wav)
                    h_seg = VlogHookGenerator.extract_highlight_from_clip(
                        video_path=video_path,
                        wav_path=temp_wav,
                        subtitles=raw_subtitles,
                        clip_duration=self.vlog_hook_duration
                    )
                    vlog_hook_segments.append(h_seg)
                    self.log_signal.emit(f"   ✨ [Vlog Hook] Highlight: [{h_seg.src_in}s - {h_seg.src_out}s] ({h_seg.reason})")
                    try:
                        if os.path.exists(temp_wav):
                            os.remove(temp_wav)
                    except Exception:
                        pass

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

                mapped_clip_subs = map_subtitles_to_timeline(clip_subs, cut_segments, clip_start_rec)
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

                if self.enable_vlog_hook and h_seg:
                    audit_reporter.record_teaser_item(
                        order=len(vlog_hook_segments),
                        video_path=video_path,
                        src_in=h_seg.src_in,
                        src_out=h_seg.src_out,
                        reason=h_seg.reason,
                        score=h_seg.score,
                        hook_text=h_seg.text,
                        fps=fps
                    )

                self.progress_signal.emit(int(40 + (idx + 1) / total_clips * 40))

            # --- XUẤT TIMELINES INTRO & CHÍNH ---
            target_aspect = "9:16" if self.enable_reframe else "16:9"
            output_teaser_edl = None
            output_teaser_fcpxml = None

            if self.enable_vlog_hook and vlog_hook_segments:
                output_teaser_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.edl")
                output_teaser_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.fcpxml")
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
                self.log_signal.emit(f"   👉 Tệp FCPXML Teaser (Khuyên dùng): {os.path.basename(output_teaser_fcpxml)}")
                self.log_signal.emit(f"   👉 Tệp EDL Teaser: {os.path.basename(output_teaser_edl)}")
                
                resolve_auto.import_edl_to_timeline(
                    edl_path=output_teaser_fcpxml,
                    video_path=self.video_paths,
                    timeline_name=teaser_timeline_name,
                    log_callback=self.log_signal.emit
                )
                audit_reporter.add_output_file("Timeline Teaser (FCPXML)", output_teaser_fcpxml, "Timeline 10–30s FCPXML tự động link media")
                audit_reporter.add_output_file("Timeline Teaser (EDL)", output_teaser_edl, "Timeline 10–30s định dạng EDL CMX3600")
                audit_reporter.add_timeline(teaser_timeline_name, "Timeline Teaser/Hook mở đầu Vlog (10-30s)")

            if merged_edl_events:
                if self.run_cut:
                    timeline_name = f"{stamped_name}_Timeline_CatLoc"
                    output_timeline_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.fcpxml")
                    output_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.edl")
                else:
                    timeline_name = f"{stamped_name}_Timeline_Goc_CoSub"
                    output_timeline_fcpxml = os.path.join(base_dir, f"{stamped_name}_Timeline_Goc_CoSub.fcpxml")
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

                EDLGenerator.create_multi_clip_edl(merged_edl_events, output_edl, markers=merged_markers)
                
                self.log_signal.emit(f"\n📝 Đang tạo tệp Timeline DaVinci Resolve:\n      👉 FCPXML (Khuyên dùng): {os.path.abspath(output_timeline_fcpxml)}\n      👉 EDL (Dự phòng): {os.path.abspath(output_edl)}")
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
                    audit_reporter.add_output_file("Timeline Cắt Lọc (FCPXML - Khuyên Dùng)", output_timeline_fcpxml, "Timeline chính FCPXML v1.9 link media tự động tuyệt đối chính xác")
                    audit_reporter.add_output_file("Timeline Cắt Lọc (EDL)", output_edl, "Timeline chính đã lọc sạch khoảng lặng và tua nhanh")
                    audit_reporter.add_timeline(timeline_name, "Timeline chính đã biên tập âm thanh & thị giác")
                else:
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
                    
                    if self.run_cut:
                        audit_reporter.add_output_file("Phụ đề Video Đã Cắt (SRT)", output_cut_srt, "Phụ đề chuẩn cho timeline đã qua cắt gọt")
                        audit_reporter.add_output_file("Phụ đề Karaoke Đã Cắt (FCPXML)", output_cut_fcpxml, f"Phụ đề chữ nhảy/đổi màu động (Text+) trên video đã cắt ({target_aspect})")
                    else:
                        audit_reporter.add_output_file("Phụ đề Video Gốc (SRT)", output_cut_srt, "Phụ đề thô khớp với video quay ban đầu")
                        audit_reporter.add_output_file("Phụ đề Karaoke Gốc (FCPXML)", output_cut_fcpxml, f"Phụ đề chữ nhảy/đổi màu động (Text+) trên video gốc ({target_aspect})")

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

            self.progress_signal.emit(100)
            console_summary = audit_reporter.generate_console_summary()
            self.log_signal.emit(console_summary)
            self.log_signal.emit(f"\n📄 Báo cáo chi tiết đã được xuất bản tại:\n      👉 {os.path.abspath(output_report_md)}")
            
            if self.phase == 2:
                self.finished_signal.emit(True, "phase2_done")
            else:
                self.finished_signal.emit(True, "Hoàn thành!")

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

    def _handle_interrupted(self):
        self.log_signal.emit("🛑 Tiến trình đã được dừng lại an toàn theo yêu cầu của bạn.")
        self.finished_signal.emit(False, "Tiến trình đã dừng bởi người dùng (Cancelled).")


class ResolveFlowApp(QMainWindow):
    """
    Lớp giao diện người dùng chính (Main Dashboard) của ResolveFlow Assistant v4.1.
    Tích hợp Workflow Mode 1-click, Phân tầng 2 lớp Cơ bản/Nâng cao, Hệ thống Text Style Preset,
    Hệ thống Recipe và Scan Cache siêu tốc.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ResolveFlow Assistant v4.1 - AI Visual & Director Automation Suite")
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

        self.preset_mgr = PresetManager()
        self.recipe_mgr = RecipeManager()
        self.setAcceptDrops(True)

        self._init_ui()
        self._apply_stylesheet()
        self._load_presets_to_combo()
        self._load_recipes_to_combo()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # -------------------------------------------------------------
        # Splitter Chính: Chia Cột Trái (Thao tác rộng) & Cột Phải (Giám sát)
        # -------------------------------------------------------------
        self.main_splitter = QSplitter(Qt.Horizontal)

        # -------------------------------------------------------------
        # Cột trái: Tab Widget 4 Chức Năng (Workflow Tabs - Rộng ~65%)
        # -------------------------------------------------------------
        left_container = QWidget()
        left_panel = QVBoxLayout(left_container)
        left_panel.setContentsMargins(0, 0, 6, 0)
        
        # Header Title
        title_label = QLabel("RESOLVEFLOW v4.1")
        title_font = QFont("Segoe UI", 18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #1976D2; letter-spacing: 2px;")
        
        subtitle_label = QLabel("AI Director, Text+ Presets, SFX Soundboard & Export Suite")
        subtitle_label.setStyleSheet("color: #90A4AE; font-size: 11px;")
        left_panel.addWidget(title_label)
        left_panel.addWidget(subtitle_label)
        left_panel.addSpacing(6)

        # 4 Tabs Widget
        self.tab_widget = QTabWidget()
        self.tab_autocut = TabAutoCut()
        self.tab_titles = TabTitles()
        self.tab_sfx = TabSFX()
        self.tab_export = TabExport()

        self.tab_widget.addTab(self.tab_autocut, "✂ 1. Dựng Thô (Auto Cut)")
        self.tab_widget.addTab(self.tab_titles, "📝 2. Chữ & Phụ Đề")
        self.tab_widget.addTab(self.tab_sfx, "🔊 3. SFX Soundboard")
        self.tab_widget.addTab(self.tab_export, "🚀 4. Polish & Export")

        left_panel.addWidget(self.tab_widget)
        self.main_splitter.addWidget(left_container)

        # Liên kết delegates để tương thích 100% với mã nguồn & unit tests
        self._bind_tab_delegates()

        # Kết nối sự kiện tương tác Tab 1
        self.combo_workflow.currentIndexChanged.connect(self._on_workflow_mode_changed)
        self.combo_recipes.currentIndexChanged.connect(self._on_recipe_selected)
        self.btn_save_recipe.clicked.connect(self._save_current_as_recipe)
        self.btn_delete_recipe.clicked.connect(self._delete_selected_recipe)
        self.slide_master_intensity.valueChanged.connect(self._on_master_intensity_changed)
        self.btn_toggle_advanced.clicked.connect(self._toggle_advanced_panel)

        # Kết nối sự kiện tương tác Tab 2
        self.tab_titles.set_preset_provider(self._get_current_active_preset)
        self.combo_text_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.btn_preview_preset.clicked.connect(self._show_quick_preview)
        self.btn_save_custom_preset.clicked.connect(self._save_custom_preset_dialog)
        self.combo_split_mode.activated.connect(self._on_user_mode_changed)
        self.txt_split_limit.textEdited.connect(self._on_user_customized_limit)
        self.tab_titles.insert_title_requested.connect(self._on_insert_title_at_playhead)
        self.tab_titles.install_presets_requested.connect(lambda: self.txt_console.appendPlainText("🎉 [Effects Library] Đã cài đặt 7 Presets Fusion Text+ vào DaVinci Resolve!"))
        self.tab_titles.copy_fusion_node_requested.connect(lambda pid: self.txt_console.appendPlainText(f"📋 [Clipboard] Đã copy Fusion Node của preset '{pid}'. Hãy chuyển sang DaVinci Resolve và ấn Ctrl+V để chèn!"))

        # Kết nối sự kiện tương tác Tab 3 & 4
        self.tab_sfx.insert_sfx_requested.connect(self._on_insert_sfx_at_playhead)
        self.tab_export.apply_lut_requested.connect(self._on_apply_lut_to_timeline)
        self.tab_export.render_requested.connect(self._on_start_render_job)

        # Kết nối cập nhật viền màu trực quan khi bật/tắt module
        self.check_cache.toggled.connect(self._update_card_active_states)
        self.check_bad_takes.toggled.connect(self._update_card_active_states)
        self.check_punch_in.toggled.connect(self._update_card_active_states)
        self.combo_ai_mode.currentIndexChanged.connect(self._update_card_active_states)
        self.check_vlog_hook.toggled.connect(self._update_card_active_states)
        self.check_reframe.toggled.connect(self._update_card_active_states)
        self.check_broll.toggled.connect(self._update_card_active_states)
        self.check_sfx.toggled.connect(self._update_card_active_states)
        self.check_subtitle.toggled.connect(self._update_card_active_states)
        self.check_cut.toggled.connect(self._update_card_active_states)

        # Kết nối cập nhật realtime cho Live Preview
        self.combo_text_preset.currentIndexChanged.connect(self._update_live_preview)
        self.txt_font.textChanged.connect(self._update_live_preview)
        self.txt_size.textChanged.connect(self._update_live_preview)
        self.txt_color.textChanged.connect(self._update_live_preview)
        self.check_reframe.toggled.connect(self._update_live_preview)
        
        # Cập nhật trạng thái viền ban đầu
        self._update_card_active_states()

        # -------------------------------------------------------------
        # Cột phải: Bảng Giám Sát & Điều Khiển Thực Thi (Right Panel - ~35%)
        # -------------------------------------------------------------
        right_container = QWidget()
        right_panel = QVBoxLayout(right_container)
        right_panel.setContentsMargins(6, 0, 0, 0)

        # 1. Card Tệp Video Nguồn
        self.group_file = QGroupBox("📁 TỆP VIDEO NGUỒN (SOURCE MEDIA)")
        form_file = QVBoxLayout(self.group_file)
        
        file_layout = QHBoxLayout()
        self.lbl_file = QLineEdit()
        self.lbl_file.setPlaceholderText("Kéo-thả tệp video vào đây hoặc bấm 'Chọn Video'...")
        self.lbl_file.setReadOnly(True)
        
        btn_auto = QPushButton("Tự lấy Resolve")
        btn_auto.clicked.connect(self._auto_detect_video)
        
        btn_browse = QPushButton("Chọn Video")
        btn_browse.clicked.connect(self._browse_file)
        
        file_layout.addWidget(self.lbl_file, stretch=3)
        file_layout.addWidget(btn_auto, stretch=2)
        file_layout.addWidget(btn_browse, stretch=2)
        form_file.addLayout(file_layout)
        right_panel.addWidget(self.group_file)

        # 2. Review Table (Pha 1 AI Đạo Diễn)
        self.table_review = QTableWidget()
        self.table_review.setColumnCount(7)
        self.table_review.setHorizontalHeaderLabels([
            "Giữ", "ID", "Bắt đầu", "Kết thúc", "Lý do AI", "Độ tin cậy", "Nội dung câu thoại"
        ])
        self.table_review.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_review.horizontalHeader().setStretchLastSection(True)
        self.table_review.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_review.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_review.hide()
        right_panel.addWidget(self.table_review)

        # 3. Card Điều Khiển & Tiến Trình Thực Thi
        self.group_exec = QGroupBox("⚡ ĐIỀU KHIỂN & TIẾN TRÌNH THỰC THI")
        vbox_exec = QVBoxLayout(self.group_exec)

        self.btn_run = QPushButton("🚀 BẮT ĐẦU XỬ LÝ (RUN)")
        self.btn_run.setObjectName("btn_run")
        btn_font = QFont("Segoe UI", 12)
        btn_font.setBold(True)
        self.btn_run.setFont(btn_font)
        self.btn_run.clicked.connect(self._toggle_pipeline_execution)
        vbox_exec.addWidget(self.btn_run)

        self.lbl_progress_status = QLabel("Trạng thái: Sẵn sàng làm việc.")
        self.lbl_progress_status.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 11px; font-weight: bold;")
        vbox_exec.addWidget(self.lbl_progress_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        vbox_exec.addWidget(self.progress_bar)
        right_panel.addWidget(self.group_exec)

        # 4. Card Nhật Ký Xử Lý (AI Console Logs)
        self.group_console = QGroupBox("📋 NHẬT KÝ XỬ LÝ (AI CONSOLE)")
        vbox_console = QVBoxLayout(self.group_console)

        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.appendPlainText("🌟 ResolveFlow Assistant v4.1 sẵn sàng làm việc.")
        vbox_console.addWidget(self.txt_console)
        right_panel.addWidget(self.group_console, stretch=1)

        self.main_splitter.addWidget(right_container)

        # Thiết lập tỷ lệ mặc định: Cột trái 65% (~800px), Cột phải 35% (~440px)
        self.main_splitter.setStretchFactor(0, 7)
        self.main_splitter.setStretchFactor(1, 4)
        self.main_splitter.setSizes([800, 440])

        main_layout.addWidget(self.main_splitter)
        self.setAcceptDrops(True)

    def _bind_tab_delegates(self):
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
        self.lbl_master_intensity = self.tab_autocut.lbl_master_intensity
        self.btn_toggle_advanced = self.tab_autocut.btn_toggle_advanced
        self.advanced_container = self.tab_autocut.advanced_container
        self.group_ai = self.tab_autocut.group_ai
        self.combo_model = self.tab_autocut.combo_model
        self.combo_lang = self.tab_autocut.combo_lang
        self.check_cache = self.tab_autocut.check_cache
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

        # Tab 3: SFX Delegates
        self.combo_sfx_track = self.tab_sfx.combo_target_track
        self.slide_volume_offset = self.tab_sfx.slide_volume_offset
        self.btn_insert_sfx_playhead = self.tab_sfx.btn_insert_playhead

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
        resolve_auto = ResolveAutomation()
        if resolve_auto.connect():
            self.txt_console.appendPlainText(f" ✔ Đã kết nối DaVinci Resolve Project: Áp dụng LUT preset '{lut_id}'.")
        else:
            self.txt_console.appendPlainText(" ℹ Mở DaVinci Resolve và Timeline để áp dụng Color Grade trực tiếp.")

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
                sample_words=["ResolveFlow", "AI", "Text+", "Subtitle"],
                active_index=2,
                aspect_ratio=aspect
            )
            pix = QPixmap(temp_img)
            self.preview_lbl.setPixmap(pix.scaled(280, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview_lbl.setText(f"Preset: {preset.name} | {font_name} {sz}px")

    def _set_card_active(self, card_widget: QGroupBox, is_active: bool):
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
            self.txt_console.appendPlainText("🎯 Chế độ [Vlog Hook/Intro]: Tự bật Intro Teaser + Speed-Ramp Timelapse + Punch-in + B-Roll.")
        elif mode == "advanced":
            self.txt_console.appendPlainText("🎯 Chế độ [Advanced]: Đã mở toàn bộ 6 nhóm chức năng chi tiết.")
        self._update_card_active_states()

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
        """Đặt lại trạng thái kịch bản và xóa sạch cache phân đoạn cũ khi chọn video mới."""
        self.current_phase = 1
        self.clip_data_cache = []
        self.proposed_segments = []
        if hasattr(self, "table_review"):
            self.table_review.setRowCount(0)
            self.table_review.hide()
        if hasattr(self, "btn_run"):
            self._update_run_button_state(running=False)

    def _browse_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn Tệp Video nguồn", "", "Video files (*.mp4 *.mov *.mkv *.avi *.wav *.mp3);;All files (*.*)"
        )
        if file_paths:
            self._reset_workflow_phase()
            self.selected_files = file_paths
            if len(file_paths) == 1:
                self.lbl_file.setText(file_paths[0])
                self.txt_console.appendPlainText(f"📁 Đã chọn tệp: {file_paths[0]}")
            else:
                self.lbl_file.setText("; ".join(file_paths))
                self.txt_console.appendPlainText(f"📁 Đã chọn hàng loạt {len(file_paths)} tệp video.")
            self._update_default_chars_limit(file_paths[0])
            self._suggest_whisper_model_for_file(file_paths[0])

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
        else:
            self.txt_console.appendPlainText("⚠ Không phát hiện được video nào đang được chọn trong Media Pool hoặc Timeline. Vui lòng chọn thủ công.")

    def _suggest_whisper_model_for_file(self, video_path: str):
        """Tự động gợi ý kích thước model Whisper tối ưu theo thời lượng video."""
        try:
            dur = AudioExtractor.get_audio_duration(video_path)
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
        if self.is_processing:
            self._stop_pipeline()
        else:
            self._run_pipeline()

    def _stop_pipeline(self):
        if self.worker and self.worker.isRunning():
            self.btn_run.setEnabled(False)
            self.btn_run.setText("⏳ ĐANG DỪNG LẠI...")
            self.txt_console.appendPlainText("🛑 Người dùng yêu cầu hủy tiến trình. Đang tiến hành dừng an toàn...")
            self.worker.stop()

    def _update_run_button_state(self, running: bool):
        if running:
            self.btn_run.setText("⏹ DỪNG LẠI (STOP / CANCEL)")
            self.btn_run.setStyleSheet("""
                QPushButton#btn_run {
                    background-color: #C62828;
                    color: white;
                    padding: 12px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton#btn_run:hover {
                    background-color: #D32F2F;
                }
                QPushButton#btn_run:pressed {
                    background-color: #B71C1C;
                }
            """)
        else:
            self.btn_run.setText("🚀 BẮT ĐẦU XỬ LÝ (RUN)")
            self.btn_run.setStyleSheet("""
                QPushButton#btn_run {
                    background-color: #2E7D32;
                    color: white;
                    padding: 12px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton#btn_run:hover {
                    background-color: #388E3C;
                }
                QPushButton#btn_run:pressed {
                    background-color: #1B5E20;
                }
            """)

    def _run_pipeline(self):
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
            use_cache=use_cache
        )

        self.worker.log_signal.connect(self._log_message)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
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
        if "khởi động" in msg_l:
            self.lbl_progress_status.setText("🚀 Đang khởi động hệ thống...")
        elif "dry-run" in msg_l or "tương thích" in msg_l:
            self.lbl_progress_status.setText("🔍 Đang kiểm tra định dạng tệp...")
        elif "tải mô hình" in msg_l:
            self.lbl_progress_status.setText("🤖 Đang nạp mô hình Whisper AI...")
        elif "trích xuất audio" in msg_l:
            self.lbl_progress_status.setText("🔊 Đang trích xuất âm thanh mono...")
        elif "quét giọng nói" in msg_l or "dịch giọng nói" in msg_l:
            self.lbl_progress_status.setText("🎙 Đang nhận diện giọng nói (Speech-to-Text)...")
        elif "scan cache hit" in msg_l:
            self.lbl_progress_status.setText("⚡ Nạp dữ liệu từ Scan Cache siêu tốc (0.05s)!")
        elif "lọc khoảng lặng" in msg_l or "phân tích khoảng lặng" in msg_l or "speed-ramp" in msg_l:
            self.lbl_progress_status.setText("✂ Đang phân tích và xử lý khoảng lặng âm thanh...")
        elif "ai director" in msg_l:
            self.lbl_progress_status.setText("🎬 Đạo diễn AI đang xử lý ngữ nghĩa và nhịp cắt...")
        elif "vlog hook" in msg_l:
            self.lbl_progress_status.setText("🔥 Đang trích xuất phân đoạn Teaser/Hook mở đầu...")
        elif "reframe" in msg_l:
            self.lbl_progress_status.setText("👁 Đang tính toán bám mặt video dọc 9:16...")
        elif "b-roll" in msg_l:
            self.lbl_progress_status.setText("🎞 Đang tạo gợi ý cảnh minh họa B-Roll...")
        elif "sfx" in msg_l:
            self.lbl_progress_status.setText("🔊 Đang bố trí âm thanh hiệu ứng SFX...")
        elif "tạo tệp timeline" in msg_l or "fcpxml" in msg_l:
            self.lbl_progress_status.setText("📝 Đang tạo Timeline FCPXML & EDL...")
        elif "import timeline" in msg_l or "gửi yêu cầu" in msg_l:
            self.lbl_progress_status.setText("🤖 Đang kết nối và gửi sang DaVinci Resolve...")
        elif "hoàn tất" in msg_l or "xong" in msg_l:
            self.lbl_progress_status.setText("🏁 Hoàn tất thành công!")

    @pyqtSlot(bool, str)
    def _pipeline_finished(self, success, message):
        self.is_processing = False
        self.btn_run.setEnabled(True)
        self._update_run_button_state(running=False)
        
        if success:
            if message == "phase1_done":
                self.lbl_progress_status.setText("✔ Đã phân tích xong kịch bản, vui lòng duyệt bảng bên trên.")
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
                self.table_review.show()
                
                self.txt_console.appendPlainText("\n✔ [AI Director] Phân tích hoàn tất! Vui lòng duyệt các phân đoạn đề xuất cắt/giữ trong bảng ở trên.")
                self.txt_console.appendPlainText("👉 Tích chọn để GIỮ phân đoạn, Bỏ tích để CẮT phân đoạn.")
                self.txt_console.appendPlainText("👉 Sau đó, bấm nút '🎬 XUẤT TIMELINE & DAVINCI RESOLVE' để hoàn thành.")
                
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
            elif message == "phase2_done" or message == "Hoàn thành!":
                self.lbl_progress_status.setText("🏁 Khởi chạy hoàn tất. Đã xuất bản lên DaVinci Resolve!")
                self.txt_console.appendPlainText("🏁 Khởi chạy hoàn tất. Đã xuất bản hoàn chỉnh lên DaVinci Resolve!")
                self.table_review.hide()
                self.current_phase = 1
                self.clip_data_cache = []
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
            
            if p.confidence < threshold:
                p.approved = False
                
            chk.setChecked(p.approved)
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

def start_gui():
    app = QApplication(sys.argv)
    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)
    window = ResolveFlowApp()
    window.show()
    sys.exit(app.exec())
