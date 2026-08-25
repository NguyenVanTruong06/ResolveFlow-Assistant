import os
import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QPlainTextEdit, QGroupBox, QFormLayout, QSlider, QFileDialog
)
from PySide6.QtCore import QThread, Signal as pyqtSignal, Slot as pyqtSlot, Qt
from PySide6.QtGui import QFont, QColor

class PipelineWorker(QThread):
    """
    Worker Thread để chạy tiến trình xử lý ngầm (Audio, STT, Subtitle, Smart Cut, AI Director, Vision, SFX, Speed-Ramp & Vlog Hook)
    tránh làm đơ (freeze) giao diện người dùng GUI.
    Hỗ trợ cơ chế ngắt an toàn (Interruptible Threading) và thu hồi triệt để VRAM/RAM khi người dùng bấm dừng.
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
        api_key=None
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
        
        # Cờ kiểm soát ngắt luồng an toàn
        self.is_interrupted = False

    def stop(self):
        """
        Yêu cầu dừng tiến trình ngầm một cách an toàn.
        """
        self.is_interrupted = True
        self.log_signal.emit("🛑 Đang gửi tín hiệu dừng khẩn cấp... Thu hồi tài nguyên và dọn dẹp bộ nhớ.")

    def run(self):
        transcriber = None
        temp_files_to_clean = []
        try:
            self.log_signal.emit("🚀 Đang khởi động ResolveFlow-Assistant v4.1 (Visual, Audio & Smart Hook Suite)...")
            self.progress_signal.emit(5)

            if self.is_interrupted:
                self._handle_interrupted()
                return

            # Khởi tạo mô hình Whisper AI một lần duy nhất cho toàn danh sách
            self.log_signal.emit(f"🤖 Tải mô hình Whisper AI '{self.model_size}'...")
            from src.core.transcriber import ModelConfig, ResolveTranscriber
            
            model_config = ModelConfig(
                model_size=self.model_size,
                device="cuda",
                compute_type="float16"
            )
            transcriber = ResolveTranscriber(model_config)
            transcriber.load_model()

            if self.is_interrupted:
                self._handle_interrupted()
                return
            
            # Kết nối DaVinci Resolve để kiểm tra
            from src.core.resolve_api import SubtitleConfig, ResolveAutomation, split_subtitles, map_subtitles_to_timeline
            resolve_auto = ResolveAutomation()
            resolve_auto.ensure_resolve_running(log_callback=self.log_signal.emit)

            # Cấu hình các bộ xử lý
            from src.core.autocut import AudioCutConfig, SilenceDetector, EDLGenerator
            from src.core.ai_director import AIDirector, AIDirectorConfig
            from src.core.vision_reframer import VisionReframer, ReframeConfig
            from src.core.broll_sfx import BRollAnalyzer, SFXEngine
            from src.core.vlog_hook import VlogHookGenerator, HookSegment
            from src.core.audio import AudioExtractor

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

            # Danh sách tích lũy các khoảng thoại và phụ đề để ghép nối
            merged_edl_events = []
            merged_cut_subtitles = []
            merged_orig_subtitles = []
            merged_markers = []
            all_broll_cues = []
            all_sfx_cues = []
            vlog_hook_segments = []
            cumulative_record_seconds = 0.0

            # Lấy thư mục của tệp đầu tiên để làm đầu ra cho tệp ghép nối
            first_video = self.video_paths[0]
            base_dir = os.path.dirname(first_video)
            
            # Tên file cho tệp ghép nối tổng hợp
            if len(self.video_paths) == 1:
                stamped_name = os.path.splitext(os.path.basename(first_video))[0]
            else:
                stamped_name = f"ResolveFlow_Merged_{len(self.video_paths)}clips"

            from src.core.audit_reporter import ExecutionAuditReporter
            audit_reporter = ExecutionAuditReporter(project_name=stamped_name)

            total_clips = len(self.video_paths)
            for idx, video_path in enumerate(self.video_paths):
                if self.is_interrupted:
                    self._handle_interrupted()
                    return

                self.log_signal.emit(f"\n🎬 [Clip {idx+1}/{total_clips}] Bắt đầu xử lý: {os.path.basename(video_path)}")
                
                # Trích xuất âm thanh
                clip_base_name = os.path.splitext(os.path.basename(video_path))[0]
                temp_wav = os.path.join(base_dir, f"{clip_base_name}_temp_extracted.wav")
                temp_files_to_clean.append(temp_wav)
                
                self.log_signal.emit("   🔊 Trích xuất audio...")
                AudioExtractor.extract_audio(video_path, temp_wav)

                if self.is_interrupted:
                    self._handle_interrupted()
                    return
                
                # Transcribe
                self.log_signal.emit("   🎙 Dịch giọng nói...")
                lang_code = None if self.language == "Auto" else ("vi" if self.language == "Tiếng Việt" else "en")
                raw_subtitles = transcriber.transcribe(
                    temp_wav, 
                    language=lang_code,
                    is_cancelled_callback=lambda: self.is_interrupted
                )

                if self.is_interrupted:
                    self._handle_interrupted()
                    return

                self.log_signal.emit(f"   ✔ Phát hiện {len(raw_subtitles)} đoạn thoại.")
                
                # Ngắt câu phụ đề của clip theo chế độ và giới hạn người dùng thiết lập
                clip_subs = split_subtitles(
                    raw_subtitles, 
                    limit=self.split_limit, 
                    mode=self.split_mode
                )
                merged_orig_subtitles.extend(clip_subs)

                # Trích xuất Vlog Hook Highlight nếu tính năng được bật
                h_seg = None
                if self.enable_vlog_hook:
                    h_seg = VlogHookGenerator.extract_highlight_from_clip(
                        video_path=video_path,
                        wav_path=temp_wav,
                        subtitles=raw_subtitles,
                        clip_duration=self.vlog_hook_duration
                    )
                    vlog_hook_segments.append(h_seg)
                    self.log_signal.emit(f"   ✨ [Vlog Hook] Highlight: [{h_seg.src_in}s - {h_seg.src_out}s] ({h_seg.reason})")

                # Lọc khoảng lặng cơ bản
                keep_intervals = []
                speedup_segments = []

                if self.run_cut:
                    if self.speed_up_silence:
                        self.log_signal.emit(f"   ⚡ [Speed-Ramp] Phân tích khoảng lặng để tua nhanh ({self.silence_speed}x)...")
                        speedup_segments = SilenceDetector.detect_intervals_with_speedup(temp_wav, cut_config, self.silence_speed)
                        keep_intervals = [(s["start"], s["end"]) for s in speedup_segments if s["type"] == "voice"]
                    else:
                        self.log_signal.emit("   ✂ Lọc khoảng lặng âm lượng...")
                        keep_intervals = SilenceDetector.detect_silence_from_wav(temp_wav, cut_config)

                if self.is_interrupted:
                    self._handle_interrupted()
                    return

                # Áp dụng Đạo Diễn AI (AI Director)
                try:
                    clip_dur = AudioExtractor.get_audio_duration(video_path)
                except Exception:
                    clip_dur = 0.0

                ai_punch_events = []
                if self.run_cut and self.ai_mode != "silence_only":
                    self.log_signal.emit(f"   🎬 [AI Director] Kích hoạt chế độ '{self.ai_mode}'...")
                    ai_res = director.process_semantic_cut(
                        subtitles=clip_subs,
                        silence_keep_intervals=keep_intervals,
                        total_duration=clip_dur,
                        language="vi" if self.language == "Tiếng Việt" else "en"
                    )
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

                # Xử lý Thị giác: Auto Re-framing 9:16
                if self.enable_reframe and keep_intervals:
                    self.log_signal.emit("   👁 [Vision Reframer] Tính toán bám mặt chuyển đổi sang video dọc 9:16...")
                    reframe_events = reframer.generate_reframe_timeline_events(keep_intervals)
                    self.log_signal.emit(f"   ✔ Đã tính toán {len(reframe_events)} mốc Smooth Pan 9:16.")

                # Xử lý: AI B-Roll Inserter (Track Video 2)
                if self.enable_broll and clip_subs:
                    broll_cues = BRollAnalyzer.extract_broll_cues(clip_subs)
                    if broll_cues:
                        all_broll_cues.extend(broll_cues)
                        self.log_signal.emit(f"   🎞 [AI B-Roll] Gợi ý {len(broll_cues)} cảnh minh họa trên Video Track 2.")
                        for bc in broll_cues:
                            merged_markers.append({
                                "time": bc.start + cumulative_record_seconds,
                                "duration": bc.duration,
                                "name": f"🎞 B-Roll: {bc.keyword}",
                                "note": f"Tìm: {bc.search_prompt}",
                                "color": "Magenta"
                            })

                # Xử lý: Auto SFX Engine (Track Audio 2)
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
                            merged_markers.append({
                                "time": sc.time + cumulative_record_seconds,
                                "duration": sc.duration,
                                "name": f"🔊 SFX: {sc.sfx_type.upper()}",
                                "note": sc.note,
                                "color": "Cyan"
                            })

                # Xử lý ghép nối (Stitching) & Ánh xạ mốc thời gian phụ đề
                fps = EDLGenerator.get_video_fps(video_path)
                clip_start_rec = cumulative_record_seconds

                if self.speed_up_silence and speedup_segments:
                    for seg in speedup_segments:
                        is_speed = (seg["type"] == "speedup")
                        rec_dur = seg["rec_duration"]
                        rec_start = cumulative_record_seconds
                        rec_end = rec_start + rec_dur

                        merged_edl_events.append({
                            "video_path": video_path,
                            "src_in": seg["start"],
                            "src_out": seg["end"],
                            "rec_in": rec_start,
                            "rec_out": rec_end,
                            "fps": fps,
                            "is_speedup": is_speed,
                            "speed": seg.get("speed", 1.0)
                        })

                        if is_speed:
                            merged_markers.append({
                                "time": rec_start,
                                "duration": rec_dur,
                                "name": f"⚡ Fast-Forward ({self.silence_speed}x)",
                                "note": "Cú chuyển cảnh tua nhanh Timelapse",
                                "color": "Purple"
                            })

                        cumulative_record_seconds = rec_end

                    mapped_clip_subs = map_subtitles_to_timeline(clip_subs, keep_intervals, clip_start_rec)
                    merged_cut_subtitles.extend(mapped_clip_subs)

                elif keep_intervals:
                    for k_idx, (k_start, k_end) in enumerate(keep_intervals):
                        dur = k_end - k_start
                        rec_start = cumulative_record_seconds
                        rec_end = rec_start + dur
                        
                        is_punch = self.enable_punch_in and (k_idx % 2 == 1)
                        merged_edl_events.append({
                            "video_path": video_path,
                            "src_in": k_start,
                            "src_out": k_end,
                            "rec_in": rec_start,
                            "rec_out": rec_end,
                            "fps": fps,
                            "punch_in": is_punch,
                            "punch_in_scale": self.punch_in_scale
                        })
                        cumulative_record_seconds = rec_end
                        
                    mapped_clip_subs = map_subtitles_to_timeline(clip_subs, keep_intervals, clip_start_rec)
                    merged_cut_subtitles.extend(mapped_clip_subs)
                else:
                    if clip_dur > 0:
                        rec_start = cumulative_record_seconds
                        rec_end = rec_start + clip_dur
                        merged_edl_events.append({
                            "video_path": video_path,
                            "src_in": 0.0,
                            "src_out": clip_dur,
                            "rec_in": rec_start,
                            "rec_out": rec_end,
                            "fps": fps
                        })
                        cumulative_record_seconds = rec_end
                        
                    merged_cut_subtitles.extend(clip_subs)

                # Ghi nhận vào Nhật ký Kiểm toán (Execution Audit Reporter)
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

                # Dọn dẹp tệp WAV tạm thời của clip hiện tại
                try:
                    if os.path.exists(temp_wav):
                        os.remove(temp_wav)
                except Exception:
                    pass

                # Cập nhật tiến độ hàng đợi
                progress_val = int((idx + 1) / total_clips * 75)
                self.progress_signal.emit(progress_val)

            if self.is_interrupted:
                self._handle_interrupted()
                return

            # Xử lý tạo Timeline Vlog Hook / Intro Teaser nếu được kích hoạt
            output_teaser_edl = None
            if self.enable_vlog_hook and vlog_hook_segments:
                output_teaser_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_Intro_Teaser.edl")
                self.log_signal.emit(f"\n🎬 [Vlog Hook Generator] Đang tạo Timeline Highlight Teaser ({len(vlog_hook_segments)} clips)...")
                VlogHookGenerator.generate_teaser_edl(
                    segments=vlog_hook_segments,
                    output_edl_path=output_teaser_edl,
                    project_name=stamped_name
                )
                self.log_signal.emit(f"   👉 Tệp EDL Teaser: {os.path.basename(output_teaser_edl)}")
                teaser_timeline_name = f"{stamped_name}_Timeline_Intro_Teaser"
                resolve_auto.import_edl_to_timeline(
                    edl_path=output_teaser_edl,
                    video_path=first_video,
                    timeline_name=teaser_timeline_name,
                    log_callback=self.log_signal.emit
                )
                audit_reporter.add_output_file("Timeline Teaser (EDL)", output_teaser_edl, "Timeline 10–30s ghép các đoạn highlight mở đầu vlog")
                audit_reporter.add_timeline(teaser_timeline_name, "Timeline Teaser/Hook mở đầu Vlog (10-30s)")

            if self.is_interrupted:
                self._handle_interrupted()
                return

            # Phụ đề cho Timeline ĐÃ CẮT (khớp 100% với EDL timeline)
            from src.core.fcpxml_generator import FCPXMLGenerator
            target_aspect = "9:16" if self.enable_reframe else "16:9"
            
            if self.run_cut and merged_edl_events:
                output_cut_srt = os.path.join(base_dir, f"{stamped_name}_PhuDe_VideoDaCat.srt")
                resolve_auto.generate_srt(merged_cut_subtitles, output_cut_srt)
                
                output_cut_fcpxml = os.path.join(base_dir, f"{stamped_name}_PhuDe_Karaoke_VideoDaCat.fcpxml")
                FCPXMLGenerator.generate_karaoke_fcpxml(
                    subtitles=merged_cut_subtitles,
                    output_path=output_cut_fcpxml,
                    font_name=self.font_name,
                    font_size=self.font_size,
                    aspect_ratio=target_aspect,
                    markers=merged_markers
                )
                
                # Tạo file EDL ghép nối và import vào Resolve
                output_edl = os.path.join(base_dir, f"{stamped_name}_Timeline_CatLoc.edl")
                self.log_signal.emit(f"\n📝 Đang tạo tệp Edit Decision List (EDL v4.1) tại:\n      👉 {os.path.abspath(output_edl)}")
                EDLGenerator.create_multi_clip_edl(merged_edl_events, output_edl, markers=merged_markers)
                
                self.log_signal.emit("🤖 Đang gửi yêu cầu import EDL sang DaVinci Resolve...")
                timeline_name = f"{stamped_name}_Timeline_CatLoc"
                resolve_auto.import_edl_to_timeline(
                    edl_path=output_edl,
                    video_path=first_video,
                    timeline_name=timeline_name,
                    log_callback=self.log_signal.emit
                )

                audit_reporter.add_output_file("Timeline Cắt Lọc (EDL)", output_edl, "Timeline chính đã lọc sạch khoảng lặng và tua nhanh")
                audit_reporter.add_output_file("Phụ đề Video Đã Cắt (SRT)", output_cut_srt, "Phụ đề chuẩn cho timeline đã qua cắt gọt")
                audit_reporter.add_output_file("Phụ đề Karaoke Đã Cắt (FCPXML)", output_cut_fcpxml, f"Phụ đề chữ nhảy/đổi màu động (Text+) trên video đã cắt ({target_aspect})")
                audit_reporter.add_timeline(timeline_name, "Timeline chính đã biên tập âm thanh & thị giác")

            # Xuất file danh sách B-Roll gợi ý (nếu có)
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

            # Phụ đề cho Video GỐC CHƯA CẮT
            output_orig_srt = os.path.join(base_dir, f"{stamped_name}_PhuDe_VideoGoc.srt")
            resolve_auto.generate_srt(merged_orig_subtitles, output_orig_srt)
            audit_reporter.add_output_file("Phụ đề Video Gốc (SRT)", output_orig_srt, "Phụ đề thô khớp với video quay ban đầu")
            
            output_orig_fcpxml = os.path.join(base_dir, f"{stamped_name}_PhuDe_Karaoke_VideoGoc.fcpxml")
            FCPXMLGenerator.generate_karaoke_fcpxml(
                subtitles=merged_orig_subtitles,
                output_path=output_orig_fcpxml,
                font_name=self.font_name,
                font_size=self.font_size,
                aspect_ratio=target_aspect
            )
            audit_reporter.add_output_file("Phụ đề Karaoke Gốc (FCPXML)", output_orig_fcpxml, "Phụ đề chữ nhảy/đổi màu động trên video gốc")

            # Xuất Báo cáo Kiểm toán & Minh bạch hóa Thực thi (.md)
            output_report_md = os.path.join(base_dir, f"{stamped_name}_BaoCao_NhatKyXuLy.md")
            audit_reporter.export_markdown_report(output_report_md)
            audit_reporter.add_output_file("Báo cáo Nhật ký Xử lý (MD)", output_report_md, "Thống kê chi tiết: thời lượng cắt, đoạn trích intro, số từ/câu")

            self.progress_signal.emit(100)
            
            # Xuất báo cáo trực quan ra GUI Console
            console_summary = audit_reporter.generate_console_summary()
            self.log_signal.emit(console_summary)
            self.log_signal.emit(f"\n📄 Báo cáo chi tiết đã được xuất bản tại:\n      👉 {os.path.abspath(output_report_md)}")
            self.finished_signal.emit(True, "Hoàn thành!")

        except Exception as e:
            if self.is_interrupted:
                self._handle_interrupted()
            else:
                self.log_signal.emit(f"❌ Gặp lỗi nghiêm trọng: {str(e)}")
                self.finished_signal.emit(False, str(e))
        finally:
            # Dọn dẹp tệp WAV tạm còn sót
            for temp_f in temp_files_to_clean:
                try:
                    if os.path.exists(temp_f):
                        os.remove(temp_f)
                except Exception:
                    pass
            # Thu hồi VRAM / RAM của Whisper model
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
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ResolveFlow Assistant v4.1 - AI Visual & Director Automation Suite")
        self.resize(1060, 820)
        self.worker = None
        self.selected_files = []
        self.is_processing = False
        
        # Cờ theo dõi người dùng đã chủ động tùy chỉnh giới hạn phụ đề
        self.user_customized_limit = False

        self._init_ui()
        self._apply_stylesheet()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # -------------------------------------------------------------
        # Cột trái: Bảng điều khiển cấu hình (Left Panel)
        # -------------------------------------------------------------
        left_panel = QVBoxLayout()
        main_layout.addLayout(left_panel, stretch=2)

        title_label = QLabel("RESOLVEFLOW v4.1")
        title_font = QFont("Segoe UI", 18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #1976D2; letter-spacing: 2px;")
        
        subtitle_label = QLabel("AI Visual, Audio, Director & Smart Vlog Hook Suite")
        subtitle_label.setStyleSheet("color: #90A4AE; font-size: 11px;")
        left_panel.addWidget(title_label)
        left_panel.addWidget(subtitle_label)
        left_panel.addSpacing(6)

        # Group 1: AI Model Configuration
        group_ai = QGroupBox("CẤU HÌNH AI WHISPER")
        form_ai = QFormLayout(group_ai)
        
        self.combo_model = QComboBox()
        self.combo_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.combo_model.setCurrentText("small")
        form_ai.addRow("Kích thước Model:", self.combo_model)

        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["Auto", "Tiếng Việt", "Tiếng Anh"])
        form_ai.addRow("Ngôn ngữ đầu vào:", self.combo_lang)
        
        left_panel.addWidget(group_ai)

        # Group 2: AI Director & Semantic Cutting
        group_director = QGroupBox("🎬 ĐẠO DIỄN AI (AI DIRECTOR)")
        form_director = QFormLayout(group_director)

        self.combo_ai_mode = QComboBox()
        self.combo_ai_mode.addItem("Lọc sạch nói vấp & từ đệm (Clean Talk)", "clean_talk")
        self.combo_ai_mode.addItem("Trích xuất Video Ngắn Viral (Shorts 60s)", "viral_shorts")
        self.combo_ai_mode.addItem("Tóm tắt Highlight (Podcast / Summary)", "podcast_summary")
        self.combo_ai_mode.addItem("Chỉ cắt im lặng (Classic Silence Cut)", "silence_only")
        form_director.addRow("Chế độ kịch bản:", self.combo_ai_mode)

        self.check_bad_takes = QCheckBox("Tự động phát hiện & xóa câu nói vấp")
        self.check_bad_takes.setChecked(True)
        form_director.addRow(self.check_bad_takes)

        self.check_punch_in = QCheckBox("Hiệu ứng Auto Punch-in (Zoom luân phiên)")
        self.check_punch_in.setChecked(True)
        form_director.addRow(self.check_punch_in)

        left_panel.addWidget(group_director)

        # Group 3: Smart Vlog Hook / Intro Generator (v4.1 TÍNH NĂNG MỚI)
        group_vlog_hook = QGroupBox("🔥 VLOG HOOK / INTRO TEASER (v4.1)")
        form_vlog_hook = QFormLayout(group_vlog_hook)

        self.check_vlog_hook = QCheckBox("Tự động tạo Teaser/Hook mở đầu (10-30s)")
        self.check_vlog_hook.setChecked(False)
        form_vlog_hook.addRow(self.check_vlog_hook)

        self.combo_hook_dur = QComboBox()
        self.combo_hook_dur.addItem("1.5s / clip (Cắt nhanh giật gân)", 1.5)
        self.combo_hook_dur.addItem("2.0s / clip (Tiêu chuẩn cân đối)", 2.0)
        self.combo_hook_dur.addItem("2.5s / clip (Đủ trọn vẹn câu thoại)", 2.5)
        self.combo_hook_dur.addItem("3.0s / clip (Trích đoạn mở rộng)", 3.0)
        self.combo_hook_dur.setCurrentIndex(1)
        form_vlog_hook.addRow("Thời lượng mỗi clip:", self.combo_hook_dur)

        left_panel.addWidget(group_vlog_hook)

        # Group 4: AI Visual & Multi-Track Audio
        group_v4 = QGroupBox("👑 THỊ GIÁC & ĐA TẦNG MEDIA")
        form_v4 = QFormLayout(group_v4)

        self.check_reframe = QCheckBox("Auto Re-framing (Bám mặt sang video dọc 9:16)")
        self.check_reframe.setChecked(False)
        form_v4.addRow(self.check_reframe)

        self.check_broll = QCheckBox("Tự động gợi ý cảnh minh họa B-Roll (Track Video 2)")
        self.check_broll.setChecked(True)
        form_v4.addRow(self.check_broll)

        self.check_sfx = QCheckBox("Tự động chèn âm thanh hiệu ứng SFX (Track Audio 2)")
        self.check_sfx.setChecked(True)
        form_v4.addRow(self.check_sfx)

        left_panel.addWidget(group_v4)

        # Group 5: Subtitle Style & Dual Split Modes (v4.1 NÂNG CẤP)
        group_sub = QGroupBox("KIỂU DÁNG & NGẮT CÂU PHỤ ĐỀ")
        form_sub = QFormLayout(group_sub)
        
        self.combo_split_mode = QComboBox()
        self.combo_split_mode.addItem("Số ký tự tối đa (Max Characters)", "characters")
        self.combo_split_mode.addItem("Số từ tối đa (Max Words)", "words")
        self.combo_split_mode.activated.connect(self._on_user_mode_changed)
        form_sub.addRow("Chế độ ngắt câu:", self.combo_split_mode)

        self.txt_split_limit = QLineEdit("42")
        self.txt_split_limit.textEdited.connect(self._on_user_customized_limit)
        form_sub.addRow("Giới hạn ngắt dòng:", self.txt_split_limit)

        self.txt_font = QLineEdit("Arial")
        form_sub.addRow("Phông chữ (Font):", self.txt_font)
        
        self.txt_size = QLineEdit("48")
        form_sub.addRow("Cỡ chữ (Size):", self.txt_size)
        
        self.txt_color = QLineEdit("#FFFFFF")
        form_sub.addRow("Màu chữ (Hex):", self.txt_color)
        
        left_panel.addWidget(group_sub)

        # Group 6: Smart Silent Cut Parameters & Speed-Ramp
        group_cut = QGroupBox("CẮT KHOẢNG LẶNG & SPEED-RAMP")
        form_cut = QFormLayout(group_cut)
        
        self.check_cut = QCheckBox("Kích hoạt xử lý khoảng lặng")
        self.check_cut.setChecked(True)
        form_cut.addRow(self.check_cut)

        self.check_speedup = QCheckBox("⚡ Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp 8x)")
        self.check_speedup.setChecked(False)
        form_cut.addRow(self.check_speedup)

        self.slide_db = QSlider(Qt.Horizontal)
        self.slide_db.setRange(-60, -10)
        self.slide_db.setValue(-35)
        self.lbl_db = QLabel("-35 dB")
        self.slide_db.valueChanged.connect(lambda v: self.lbl_db.setText(f"{v} dB"))
        h_db_layout = QHBoxLayout()
        h_db_layout.addWidget(self.slide_db)
        h_db_layout.addWidget(self.lbl_db)
        form_cut.addRow("Ngưỡng im lặng (dB):", h_db_layout)

        self.slide_dur = QSlider(Qt.Horizontal)
        self.slide_dur.setRange(2, 50)
        self.slide_dur.setValue(5)
        self.lbl_dur = QLabel("0.5 s")
        self.slide_dur.valueChanged.connect(lambda v: self.lbl_dur.setText(f"{v/10:.1f} s"))
        h_dur_layout = QHBoxLayout()
        h_dur_layout.addWidget(self.slide_dur)
        h_dur_layout.addWidget(self.lbl_dur)
        form_cut.addRow("Thời lượng tối thiểu:", h_dur_layout)

        left_panel.addWidget(group_cut)
        left_panel.addStretch(1)

        # -------------------------------------------------------------
        # Cột phải: Log Console & Action Buttons (Right Panel)
        # -------------------------------------------------------------
        right_panel = QVBoxLayout()
        main_layout.addLayout(right_panel, stretch=3)

        # File selection header
        file_layout = QHBoxLayout()
        self.lbl_file = QLineEdit()
        self.lbl_file.setPlaceholderText("Vui lòng chọn tệp video nguồn...")
        self.lbl_file.setReadOnly(True)
        
        btn_auto = QPushButton("Tự lấy từ Resolve")
        btn_auto.clicked.connect(self._auto_detect_video)
        
        btn_browse = QPushButton("Chọn Video")
        btn_browse.clicked.connect(self._browse_file)
        
        file_layout.addWidget(self.lbl_file)
        file_layout.addWidget(btn_auto)
        file_layout.addWidget(btn_browse)
        right_panel.addLayout(file_layout)
        right_panel.addSpacing(8)

        # Console Logs
        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.appendPlainText("🌟 ResolveFlow Assistant v4.1 (AI Visual, Director & Vlog Hook Suite) sẵn sàng làm việc.")
        right_panel.addWidget(self.txt_console)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        right_panel.addWidget(self.progress_bar)
        right_panel.addSpacing(8)

        # Action Button (Nút Bắt đầu / Dừng lại đa luồng thông minh)
        self.btn_run = QPushButton("🚀 BẮT ĐẦU XỬ LÝ (RUN)")
        self.btn_run.setObjectName("btn_run")
        btn_font = QFont("Segoe UI", 12)
        btn_font.setBold(True)
        self.btn_run.setFont(btn_font)
        self.btn_run.clicked.connect(self._toggle_pipeline_execution)
        right_panel.addWidget(self.btn_run)

    def _apply_stylesheet(self):
        stylesheet = """
        QWidget {
            background-color: #121214;
            color: #E0E0E6;
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        QLabel {
            font-size: 13px;
        }
        QGroupBox {
            border: 2px solid #2A2A35;
            border-radius: 8px;
            margin-top: 10px;
            font-weight: bold;
            color: #1976D2;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QLineEdit, QComboBox {
            background-color: #1E1E24;
            border: 1px solid #3A3A4A;
            border-radius: 4px;
            padding: 5px;
            color: #E0E0E6;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1px solid #1976D2;
        }
        QPushButton {
            background-color: #1976D2;
            color: white;
            border: none;
            border-radius: 4px;
            padding: 8px 15px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #2196F3;
        }
        QPushButton:pressed {
            background-color: #0D47A1;
        }
        QPushButton#btn_run {
            background-color: #2E7D32;
            padding: 12px;
            border-radius: 6px;
        }
        QPushButton#btn_run:hover {
            background-color: #388E3C;
        }
        QPushButton#btn_run:pressed {
            background-color: #1B5E20;
        }
        QProgressBar {
            border: 1px solid #3A3A4A;
            border-radius: 4px;
            text-align: center;
            background-color: #1E1E24;
            height: 18px;
        }
        QProgressBar::chunk {
            background-color: #2E7D32;
            width: 10px;
        }
        QPlainTextEdit {
            background-color: #0B0B0C;
            border: 1px solid #2A2A35;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            color: #81C784;
            font-size: 12px;
        }
        """
        self.setStyleSheet(stylesheet)

    def _on_user_customized_limit(self):
        """
        Đánh dấu người dùng đã chủ động tùy chỉnh giới hạn.
        Ngăn hệ thống tự động ghi đè giá trị này khi phát hiện video.
        """
        self.user_customized_limit = True

    def _on_user_mode_changed(self):
        """
        Khi người dùng đổi chế độ ngắt câu (Characters vs Words), gợi ý giá trị tương ứng nhưng vẫn giữ cờ tùy chỉnh.
        """
        self.user_customized_limit = True
        mode = self.combo_split_mode.currentData()
        if mode == "words":
            self.txt_split_limit.setText("6")
        else:
            self.txt_split_limit.setText("42")

    def _browse_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn Tệp Video nguồn", "", "Video files (*.mp4 *.mov *.mkv *.avi *.wav *.mp3);;All files (*.*)"
        )
        if file_paths:
            self.selected_files = file_paths
            if len(file_paths) == 1:
                self.lbl_file.setText(file_paths[0])
                self.txt_console.appendPlainText(f"📁 Đã chọn tệp: {file_paths[0]}")
            else:
                self.lbl_file.setText("; ".join(file_paths))
                self.txt_console.appendPlainText(f"📁 Đã chọn hàng loạt {len(file_paths)} tệp video.")
            self._update_default_chars_limit(file_paths[0])

    def _auto_detect_video(self):
        from src.core.resolve_api import ResolveAutomation
        resolve_auto = ResolveAutomation()
        self.txt_console.appendPlainText("🔍 Đang kết nối DaVinci Resolve để tự động tìm video...")
        
        if not resolve_auto.connect():
            self.txt_console.appendPlainText("❌ Lỗi: Không thể kết nối tới DaVinci Resolve. Đảm bảo phần mềm đang mở và đã bật API scripting.")
            return
            
        file_paths = resolve_auto.auto_detect_video_paths()
        if file_paths:
            self.selected_files = file_paths
            if len(file_paths) == 1:
                self.lbl_file.setText(file_paths[0])
                self.txt_console.appendPlainText(f"✔ Tự động phát hiện video thành công!\n👉 Tệp: {file_paths[0]}")
            else:
                self.lbl_file.setText("; ".join(file_paths))
                self.txt_console.appendPlainText(f"✔ Tự động phát hiện {len(file_paths)} video từ Resolve thành công!")
            self._update_default_chars_limit(file_paths[0])
        else:
            self.txt_console.appendPlainText("⚠ Không phát hiện được video nào đang được chọn trong Media Pool hoặc Timeline. Vui lòng chọn thủ công.")

    def _update_default_chars_limit(self, video_path):
        """
        Cập nhật gợi ý giới hạn phụ đề theo tỷ lệ video,
        TUY NHIÊN BẮT BUỘC tôn trọng cấu hình tùy chỉnh của người dùng nếu đã được chỉnh sửa.
        """
        if self.user_customized_limit:
            self.txt_console.appendPlainText("⚙ Giữ nguyên cấu hình giới hạn phụ đề tùy chỉnh của người dùng (Không tự động ghi đè).")
            return

        try:
            from src.core.resolve_api import is_vertical_video
            mode = self.combo_split_mode.currentData() or "characters"
            is_vert = is_vertical_video(video_path)
            
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
        """
        Xử lý sự kiện bấm nút Hành động: Chuyển đổi giữa Bắt đầu (Run) và Dừng lại (Stop/Cancel).
        """
        if self.is_processing:
            self._stop_pipeline()
        else:
            self._run_pipeline()

    def _stop_pipeline(self):
        """
        Gửi yêu cầu hủy tiến trình ngầm và cập nhật trạng thái giao diện.
        """
        if self.worker and self.worker.isRunning():
            self.btn_run.setEnabled(False)
            self.btn_run.setText("⏳ ĐANG DỪNG LẠI...")
            self.txt_console.appendPlainText("🛑 Người dùng yêu cầu hủy tiến trình. Đang tiến hành dừng an toàn...")
            self.worker.stop()

    def _update_run_button_state(self, running: bool):
        """
        Cập nhật màu sắc và nội dung nút bấm Run / Stop.
        """
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
        video_paths = getattr(self, "selected_files", [])
        if not video_paths:
            video_path = self.lbl_file.text()
            if video_path:
                video_paths = [video_path]

        if not video_paths:
            self.txt_console.appendPlainText("❌ Lỗi: Vui lòng chọn tệp video trước khi khởi chạy!")
            return

        self.is_processing = True
        self._update_run_button_state(running=True)
        self.progress_bar.setValue(0)

        # Thu thập thông số cấu hình từ UI
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

        # Khởi tạo Worker Thread để chạy nền
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
            vlog_hook_duration=vlog_hook_duration
        )

        # Kết nối tín hiệu
        self.worker.log_signal.connect(self._log_message)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self._pipeline_finished)

        # Chạy thread
        self.worker.start()

    @pyqtSlot(str)
    def _log_message(self, message):
        self.txt_console.appendPlainText(message)

    @pyqtSlot(bool, str)
    def _pipeline_finished(self, success, message):
        self.is_processing = False
        self.btn_run.setEnabled(True)
        self._update_run_button_state(running=False)
        
        if success:
            self.txt_console.appendPlainText("🏁 Khởi chạy hoàn tất. Đã xuất bản hoàn chỉnh lên DaVinci Resolve!")
        else:
            if "dừng" in message.lower() or "cancel" in message.lower():
                self.txt_console.appendPlainText(f"🛑 {message}")
            else:
                self.txt_console.appendPlainText(f"❌ Tiến trình bị lỗi dừng lại: {message}")

def start_gui():
    app = QApplication(sys.argv)
    window = ResolveFlowApp()
    window.show()
    sys.exit(app.exec())
