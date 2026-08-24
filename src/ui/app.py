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

    def __init__(self, video_path, model_size, language, run_cut, silence_db, min_duration, max_chars):
        super().__init__()
        self.video_path = video_path
        self.model_size = model_size
        self.language = language
        self.run_cut = run_cut
        self.silence_db = silence_db
        self.min_duration = min_duration
        self.max_chars = max_chars

    def run(self):
        try:
            self.log_signal.emit("🚀 Đang khởi động ResolveFlow-Assistant...")
            self.progress_signal.emit(5)

            # Lấy đường dẫn cơ sở của tệp video
            base_dir = os.path.dirname(self.video_path)
            base_name = os.path.splitext(os.path.basename(self.video_path))[0]
            output_wav = os.path.join(base_dir, f"{base_name}_extracted.wav")

            # -------------------------------------------------------------
            # Bước 1: Trích xuất kênh âm thanh bằng FFmpeg
            # -------------------------------------------------------------
            self.log_signal.emit("🔊 Bước 1/4: Đang trích xuất audio bằng FFmpeg...")
            from src.core.audio import AudioExtractor
            AudioExtractor.extract_audio(self.video_path, output_wav)
            self.log_signal.emit("   ✔ Trích xuất âm thanh thành công.")
            self.progress_signal.emit(30)

            # -------------------------------------------------------------
            # Bước 2: Tải model AI và thực hiện nhận diện STT
            # -------------------------------------------------------------
            self.log_signal.emit(f"🤖 Bước 2/4: Đang tải mô hình Whisper AI '{self.model_size}' lên GPU/CPU...")
            from src.core.transcriber import ModelConfig, ResolveTranscriber
            
            # Cấu hình Pydantic ModelConfig
            model_config = ModelConfig(
                model_size=self.model_size,
                device="cuda",
                compute_type="float16"
            )
            transcriber = ResolveTranscriber(model_config)
            transcriber.load_model()
            
            self.log_signal.emit("   ✔ Đang tiến hành dịch giọng nói thô ngoại tuyến...")
            self.progress_signal.emit(50)
            
            # Xác định ngôn ngữ dịch (Auto nếu là tự động phát hiện)
            lang_code = None if self.language == "Auto" else ("vi" if self.language == "Tiếng Việt" else "en")
            subtitles = transcriber.transcribe(output_wav, language=lang_code)
            self.log_signal.emit(f"   ✔ Hoàn thành dịch. Phát hiện {len(subtitles)} phân đoạn.")
            self.progress_signal.emit(72)
            
            # Tách/Ngắt chữ thành các dòng ngắn gọn theo max_chars
            from src.core.resolve_api import split_subtitles
            self.log_signal.emit(f"   📝 Đang ngắt phụ đề theo giới hạn: {self.max_chars} ký tự/dòng...")
            subtitles = split_subtitles(subtitles, self.max_chars)
            self.log_signal.emit(f"   ✔ Đã xử lý ngắt câu xong.")
            self.progress_signal.emit(75)

            # -------------------------------------------------------------
            # Bước 3: Vẽ phụ đề tự động lên DaVinci Resolve
            # -------------------------------------------------------------
            self.log_signal.emit("✏ Bước 3/4: Đang kết nối DaVinci Resolve để chèn phụ đề...")
            from src.core.resolve_api import SubtitleConfig, ResolveAutomation
            resolve_auto = ResolveAutomation()
            
            # Tự động mở DaVinci Resolve nếu chưa mở
            resolve_auto.ensure_resolve_running(log_callback=self.log_signal.emit)
            
            sub_config = SubtitleConfig()
            output_srt = os.path.join(base_dir, f"{base_name}.srt")
            resolve_auto.insert_subtitles_to_timeline(
                subtitles, 
                sub_config, 
                output_srt_path=output_srt,
                log_callback=self.log_signal.emit
            )
            self.progress_signal.emit(90)

            # -------------------------------------------------------------
            # Bước 4: Tự động cắt khoảng lặng thông minh (Tùy chọn)
            # -------------------------------------------------------------
            if self.run_cut:
                self.log_signal.emit("✂ Bước 4/4: Đang phân tích sóng âm thô PCM để cắt khoảng lặng...")
                from src.core.autocut import AudioCutConfig, SilenceDetector, EDLGenerator
                
                cut_config = AudioCutConfig(
                    min_silent_duration=self.min_duration,
                    silence_threshold_db=self.silence_db,
                    padding_seconds=0.25
                )
                
                keep_intervals = SilenceDetector.detect_silence_from_wav(output_wav, cut_config)
                self.log_signal.emit(f"   ✔ Đã phát hiện {len(keep_intervals)} khoảng âm nói.")
                
                if keep_intervals:
                    output_edl = os.path.join(base_dir, f"{base_name}_cut.edl")
                    self.log_signal.emit(f"   📝 Đang tạo tệp Edit Decision List (EDL) tại:\n      👉 {os.path.abspath(output_edl)}")
                    EDLGenerator.create_edl(self.video_path, keep_intervals, output_edl)
                    
                    self.log_signal.emit("   🤖 Đang cố gắng gửi yêu cầu import EDL sang DaVinci Resolve...")
                    timeline_name = f"{base_name}_Silent_Cut"
                    resolve_auto.import_edl_to_timeline(
                        edl_path=output_edl,
                        video_path=self.video_path,
                        timeline_name=timeline_name,
                        log_callback=self.log_signal.emit
                    )
                else:
                    self.log_signal.emit("   ⚠ Không phát hiện đoạn thoại nào, bỏ qua việc tạo EDL.")
            
            # Giải phóng VRAM
            self.log_signal.emit("♻ Giải phóng bộ nhớ đệm AI VRAM...")
            transcriber.unload_model()
            
            self.progress_signal.emit(100)
            self.log_signal.emit("🎉 Quy trình tự động hóa ResolveFlow hoàn thành mỹ mãn!")
            self.finished_signal.emit(True, "Hoàn thành!")

        except Exception as e:
            self.log_signal.emit(f"❌ Gặp lỗi nghiêm trọng: {str(e)}")
            self.finished_signal.emit(False, str(e))


class ResolveFlowApp(QMainWindow):
    """
    Lớp giao diện người dùng chính (Main Dashboard) của ResolveFlow Assistant.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ResolveFlow Assistant v1.0 - AI Video Automation Suite")
        self.resize(950, 650)
        self.worker = None
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
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn Tệp Video nguồn", "", "Video files (*.mp4 *.mov *.mkv *.avi);;All files (*.*)"
        )
        if file_path:
            self.lbl_file.setText(file_path)
            self.txt_console.appendPlainText(f"📁 Đã chọn tệp: {file_path}")
            self._update_default_chars_limit(file_path)

    def _auto_detect_video(self):
        from src.core.resolve_api import ResolveAutomation
        resolve_auto = ResolveAutomation()
        self.txt_console.appendPlainText("🔍 Đang kết nối DaVinci Resolve để tự động tìm video...")
        
        # Thử kết nối
        if not resolve_auto.connect():
            self.txt_console.appendPlainText("❌ Lỗi: Không thể kết nối tới DaVinci Resolve. Đảm bảo phần mềm đang mở và đã bật API scripting.")
            return
            
        file_path = resolve_auto.auto_detect_video_path()
        if file_path:
            self.lbl_file.setText(file_path)
            self.txt_console.appendPlainText(f"✔ Tự động phát hiện video thành công!\n👉 Tệp: {file_path}")
            self._update_default_chars_limit(file_path)
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
        video_path = self.lbl_file.text()
        if not video_path:
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

        # Khởi tạo Worker Thread để chạy nền
        self.worker = PipelineWorker(
            video_path=video_path,
            model_size=model_size,
            language=language,
            run_cut=run_cut,
            silence_db=silence_db,
            min_duration=min_duration,
            max_chars=max_chars
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
