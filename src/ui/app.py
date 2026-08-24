import os
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QProgressBar,
    QPlainTextEdit, QGroupBox, QFormLayout, QSlider, QFileDialog
)
from PySide6.QtCore import QThread, Signal as pyqtSignal, Slot as pyqtSlot, Qt
from PySide6.QtGui import QFont, QColor

class PipelineWorker(QThread):
    """
    Worker Thread để chạy tiến trình xử lý ngầm (Audio, STT, Subtitle, Smart Cut)
    tránh làm đơ (freeze) giao diện người dùng GUI.
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, video_paths, model_size, language, run_cut, silence_db, min_duration, max_chars, font_name, font_size):
        super().__init__()
        self.video_paths = video_paths
        self.model_size = model_size
        self.language = language
        self.run_cut = run_cut
        self.silence_db = silence_db
        self.min_duration = min_duration
        self.max_chars = max_chars
        self.font_name = font_name
        self.font_size = font_size

    def run(self):
        try:
            self.log_signal.emit("🚀 Đang khởi động ResolveFlow-Assistant v2.0 (Quy trình hàng loạt)...")
            self.progress_signal.emit(5)

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
            
            # Kết nối DaVinci Resolve để kiểm tra
            from src.core.resolve_api import SubtitleConfig, ResolveAutomation
            resolve_auto = ResolveAutomation()
            resolve_auto.ensure_resolve_running(log_callback=self.log_signal.emit)

            # Cấu hình cắt khoảng lặng
            from src.core.autocut import AudioCutConfig, SilenceDetector, EDLGenerator
            cut_config = AudioCutConfig(
                min_silent_duration=self.min_duration,
                silence_threshold_db=self.silence_db,
                padding_seconds=0.25
            )

            from src.core.audio import AudioExtractor
            from src.core.resolve_api import split_subtitles

            # Danh sách tích lũy các khoảng thoại và phụ đề để ghép nối
            merged_edl_events = []
            merged_cut_subtitles = []
            merged_orig_subtitles = []
            cumulative_record_seconds = 0.0

            # Lấy thư mục của tệp đầu tiên để làm đầu ra cho tệp ghép nối
            first_video = self.video_paths[0]
            base_dir = os.path.dirname(first_video)
            
            # Tên file cho tệp ghép nối tổng hợp
            if len(self.video_paths) == 1:
                stamped_name = os.path.splitext(os.path.basename(first_video))[0]
            else:
                stamped_name = f"ResolveFlow_Merged_{len(self.video_paths)}clips"

            total_clips = len(self.video_paths)
            for idx, video_path in enumerate(self.video_paths):
                self.log_signal.emit(f"\n🎬 [Clip {idx+1}/{total_clips}] Bắt đầu xử lý: {os.path.basename(video_path)}")
                
                # Trích xuất âm thanh
                clip_base_name = os.path.splitext(os.path.basename(video_path))[0]
                temp_wav = os.path.join(base_dir, f"{clip_base_name}_temp_extracted.wav")
                
                self.log_signal.emit(f"   🔊 Trích xuất audio...")
                AudioExtractor.extract_audio(video_path, temp_wav)
                
                # Transcribe
                self.log_signal.emit(f"   🎙 Dịch giọng nói...")
                lang_code = None if self.language == "Auto" else ("vi" if self.language == "Tiếng Việt" else "en")
                raw_subtitles = transcriber.transcribe(temp_wav, language=lang_code)
                self.log_signal.emit(f"   ✔ Phát hiện {len(raw_subtitles)} đoạn thoại.")
                
                # Ngắt câu phụ đề của clip theo giới hạn ký tự
                clip_subs = split_subtitles(raw_subtitles, self.max_chars)
                merged_orig_subtitles.extend(clip_subs)

                # Lọc khoảng lặng
                keep_intervals = []
                if self.run_cut:
                    self.log_signal.emit(f"   ✂ Lọc khoảng lặng...")
                    keep_intervals = SilenceDetector.detect_silence_from_wav(temp_wav, cut_config)
                    self.log_signal.emit(f"   ✔ Giữ lại {len(keep_intervals)} phân đoạn âm thanh.")

                # Xử lý ghép nối (Stitching) & Ánh xạ mốc thời gian phụ đề
                if keep_intervals:
                    fps = EDLGenerator.get_video_fps(video_path)
                    clip_start_rec = cumulative_record_seconds
                    
                    for k_start, k_end in keep_intervals:
                        dur = k_end - k_start
                        rec_start = cumulative_record_seconds
                        rec_end = rec_start + dur
                        
                        merged_edl_events.append({
                            "video_path": video_path,
                            "src_in": k_start,
                            "src_out": k_end,
                            "rec_in": rec_start,
                            "rec_out": rec_end,
                            "fps": fps
                        })
                        cumulative_record_seconds = rec_end
                        
                    # Ánh xạ phụ đề theo mốc thời gian của timeline đã cắt
                    from src.core.resolve_api import map_subtitles_to_timeline
                    mapped_clip_subs = map_subtitles_to_timeline(clip_subs, keep_intervals, clip_start_rec)
                    merged_cut_subtitles.extend(mapped_clip_subs)
                else:
                    # Clip câm / B-roll hoặc không bật tính năng cắt khoảng lặng: Giữ nguyên 100% thời lượng clip
                    try:
                        clip_dur = AudioExtractor.get_audio_duration(video_path)
                    except Exception:
                        clip_dur = 0.0
                    
                    if clip_dur > 0:
                        fps = EDLGenerator.get_video_fps(video_path)
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

                # Dọn dẹp tệp WAV tạm thời
                try:
                    if os.path.exists(temp_wav):
                        os.remove(temp_wav)
                except Exception:
                    pass

                # Cập nhật tiến độ hàng đợi
                progress_val = int((idx + 1) / total_clips * 80)
                self.progress_signal.emit(progress_val)

            # Phụ đề cho Timeline ĐÃ CẮT (khớp 100% với EDL timeline)
            sub_config = SubtitleConfig()
            from src.core.fcpxml_generator import FCPXMLGenerator
            
            if self.run_cut and merged_edl_events:
                output_cut_srt = os.path.join(base_dir, f"{stamped_name}_cut.srt")
                resolve_auto.generate_srt(merged_cut_subtitles, output_cut_srt)
                
                output_cut_fcpxml = os.path.join(base_dir, f"{stamped_name}_cut_karaoke.fcpxml")
                FCPXMLGenerator.generate_karaoke_fcpxml(
                    subtitles=merged_cut_subtitles,
                    output_path=output_cut_fcpxml,
                    font_name=self.font_name,
                    font_size=self.font_size
                )
                
                # Tạo file EDL ghép nối và import vào Resolve
                output_edl = os.path.join(base_dir, f"{stamped_name}_cut.edl")
                self.log_signal.emit(f"\n📝 Đang tạo tệp Edit Decision List (EDL) tại:\n      👉 {os.path.abspath(output_edl)}")
                EDLGenerator.create_multi_clip_edl(merged_edl_events, output_edl)
                
                self.log_signal.emit("🤖 Đang gửi yêu cầu import EDL sang DaVinci Resolve...")
                timeline_name = f"{stamped_name}_Silent_Cut"
                resolve_auto.import_edl_to_timeline(
                    edl_path=output_edl,
                    video_path=first_video,
                    timeline_name=timeline_name,
                    log_callback=self.log_signal.emit
                )

            # Phụ đề cho Video GỐC CHƯA CẮT (khớp 100% với video nguồn)
            output_orig_srt = os.path.join(base_dir, f"{stamped_name}_original.srt")
            resolve_auto.generate_srt(merged_orig_subtitles, output_orig_srt)
            
            output_orig_fcpxml = os.path.join(base_dir, f"{stamped_name}_original_karaoke.fcpxml")
            FCPXMLGenerator.generate_karaoke_fcpxml(
                subtitles=merged_orig_subtitles,
                output_path=output_orig_fcpxml,
                font_name=self.font_name,
                font_size=self.font_size
            )

            # Luôn lưu 1 file .srt chuẩn mang tên chính của video để kéo thả nhanh nhất
            output_main_srt = os.path.join(base_dir, f"{stamped_name}.srt")
            main_subs = merged_cut_subtitles if (self.run_cut and merged_edl_events) else merged_orig_subtitles
            resolve_auto.generate_srt(main_subs, output_main_srt)

            self.progress_signal.emit(100)
            self.log_signal.emit("\n=======================================================")
            self.log_signal.emit("🎉 HOÀN THÀNH XUẤT TỆP THÀNH CÔNG:")
            if self.run_cut and merged_edl_events:
                self.log_signal.emit("✂ [DÀNH CHO TIMELINE ĐÃ CẮT KHOẢNG LẶNG]")
                self.log_signal.emit(f"   👉 1. File cắt Timeline: {os.path.basename(output_edl)}")
                self.log_signal.emit(f"   👉 2. Phụ đề đã cắt (SRT): {os.path.basename(output_cut_srt)}")
                self.log_signal.emit(f"   👉 3. Phụ đề Karaoke nảy chữ: {os.path.basename(output_cut_fcpxml)}")
                self.log_signal.emit("\n🎬 [DÀNH CHO VIDEO GỐC CHƯA CẮT]")
                self.log_signal.emit(f"   👉 Phụ đề gốc (SRT): {os.path.basename(output_orig_srt)}")
            else:
                self.log_signal.emit(f"   👉 1. Phụ đề SRT: {os.path.basename(output_main_srt)}")
                self.log_signal.emit(f"   👉 2. Phụ đề Karaoke FCPXML: {os.path.basename(output_orig_fcpxml)}")
            self.log_signal.emit("=======================================================")
            self.finished_signal.emit(True, "Hoàn thành!")

        except Exception as e:
            self.log_signal.emit(f"❌ Gặp lỗi nghiêm trọng: {str(e)}")
            self.finished_signal.emit(False, str(e))
        finally:
            try:
                transcriber.unload_model()
            except Exception:
                pass



class ResolveFlowApp(QMainWindow):
    """
    Lớp giao diện người dùng chính (Main Dashboard) của ResolveFlow Assistant.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ResolveFlow Assistant v2.0 - AI Video Automation Suite")
        self.resize(950, 650)
        self.worker = None
        self.selected_files = []
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

        title_label = QLabel("RESOLVEFLOW")
        title_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title_label.setStyleSheet("color: #1976D2; letter-spacing: 2px;")
        
        subtitle_label = QLabel("AI Automation Suite for DaVinci Resolve")
        subtitle_label.setStyleSheet("color: #90A4AE; font-size: 11px;")
        left_panel.addWidget(title_label)
        left_panel.addWidget(subtitle_label)
        left_panel.addSpacing(15)

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

        # Group 2: Subtitle Style
        group_sub = QGroupBox("KIỂU DÁNG PHỤ ĐỀ")
        form_sub = QFormLayout(group_sub)
        
        self.txt_font = QLineEdit("Arial")
        form_sub.addRow("Phông chữ (Font):", self.txt_font)
        
        self.txt_size = QLineEdit("48")
        form_sub.addRow("Cỡ chữ (Size):", self.txt_size)
        
        self.txt_color = QLineEdit("#FFFFFF")
        form_sub.addRow("Màu chữ (Hex):", self.txt_color)
        
        self.txt_max_chars = QLineEdit("42")
        form_sub.addRow("Ký tự tối đa/dòng:", self.txt_max_chars)
        
        left_panel.addWidget(group_sub)

        # Group 3: Smart Silent Cut (Auto-Editor)
        group_cut = QGroupBox("CẮT KHOẢNG LẶNG (SMART CUT)")
        form_cut = QFormLayout(group_cut)
        
        self.check_cut = QCheckBox("Kích hoạt tự động cắt khoảng lặng")
        self.check_cut.setChecked(True)
        form_cut.addRow(self.check_cut)

        # Silence threshold dB slider
        self.slide_db = QSlider(Qt.Horizontal)
        self.slide_db.setRange(-60, -10)
        self.slide_db.setValue(-35)
        self.lbl_db = QLabel("-35 dB")
        self.slide_db.valueChanged.connect(lambda v: self.lbl_db.setText(f"{v} dB"))
        h_db_layout = QHBoxLayout()
        h_db_layout.addWidget(self.slide_db)
        h_db_layout.addWidget(self.lbl_db)
        form_cut.addRow("Ngưỡng im lặng (dB):", h_db_layout)

        # Min silence duration slider
        self.slide_dur = QSlider(Qt.Horizontal)
        self.slide_dur.setRange(2, 50) # 0.2s - 5s
        self.slide_dur.setValue(5) # 0.5s
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
        right_panel.addSpacing(10)

        # Console Logs
        self.txt_console = QPlainTextEdit()
        self.txt_console.setReadOnly(True)
        self.txt_console.appendPlainText("🌟 ResolveFlow Assistant sẵn sàng làm việc.")
        right_panel.addWidget(self.txt_console)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        right_panel.addWidget(self.progress_bar)
        right_panel.addSpacing(10)

        # Action Button
        self.btn_run = QPushButton("KHỞI CHẠY TIẾN TRÌNH TỰ ĐỘNG HÓA")
        self.btn_run.setObjectName("btn_run")
        self.btn_run.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.btn_run.clicked.connect(self._run_pipeline)
        right_panel.addWidget(self.btn_run)

    def _apply_stylesheet(self):
        # Premium Modern Dark stylesheet
        stylesheet = """
        QWidget {
            background-color: #121214;
            color: #E0E0E6;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 13px;
        }
        QGroupBox {
            border: 2px solid #2A2A35;
            border-radius: 8px;
            margin-top: 15px;
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
            padding: 6px;
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
        QProgressBar {
            border: 1px solid #3A3A4A;
            border-radius: 4px;
            text-align: center;
            background-color: #1E1E24;
            height: 20px;
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

    def _browse_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn Tệp Video nguồn", "", "Video files (*.mp4 *.mov *.mkv *.avi);;All files (*.*)"
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
        
        # Thử kết nối
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
        try:
            from src.core.resolve_api import is_vertical_video
            if is_vertical_video(video_path):
                self.txt_max_chars.setText("22")
                self.txt_console.appendPlainText("📱 Phát hiện Video Dọc: Đã tự động đổi giới hạn chữ thành 22 ký tự/dòng để vừa khung hình đứng.")
            else:
                self.txt_max_chars.setText("42")
                self.txt_console.appendPlainText("🖥 Phát hiện Video Ngang: Đã tự động đổi giới hạn chữ thành 42 ký tự/dòng (tiêu chuẩn).")
        except Exception:
            pass

    def _run_pipeline(self):
        video_paths = getattr(self, "selected_files", [])
        if not video_paths:
            video_path = self.lbl_file.text()
            if video_path:
                video_paths = [video_path]

        if not video_paths:
            self.txt_console.appendPlainText("❌ Lỗi: Vui lòng chọn tệp video trước khi khởi chạy!")
            return

        self.btn_run.setEnabled(False)
        self.progress_bar.setValue(0)

        # Thu thập thông số cấu hình từ UI
        model_size = self.combo_model.currentText()
        language = self.combo_lang.currentText()
        run_cut = self.check_cut.isChecked()
        silence_db = float(self.slide_db.value())
        min_duration = float(self.slide_dur.value() / 10.0)
        try:
            max_chars = int(self.txt_max_chars.text())
        except ValueError:
            max_chars = 42

        font_name = self.txt_font.text()
        try:
            font_size = int(self.txt_size.text())
        except ValueError:
            font_size = 48

        # Khởi tạo Worker Thread để chạy nền
        self.worker = PipelineWorker(
            video_paths=video_paths,
            model_size=model_size,
            language=language,
            run_cut=run_cut,
            silence_db=silence_db,
            min_duration=min_duration,
            max_chars=max_chars,
            font_name=font_name,
            font_size=font_size
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
        self.btn_run.setEnabled(True)
        if success:
            self.txt_console.appendPlainText("🏁 Khởi chạy hoàn tất. Đã bàn giao phụ đề lên Resolve!")
        else:
            self.txt_console.appendPlainText(f"❌ Tiến trình bị lỗi dừng lại: {message}")

def start_gui():
    app = QApplication(sys.argv)
    window = ResolveFlowApp()
    window.show()
    sys.exit(app.exec())
