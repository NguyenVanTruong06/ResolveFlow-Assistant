import os
import sys
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class SubtitleConfig(BaseModel):
    """
    Lớp cấu hình phong cách chữ của phụ đề sử dụng Pydantic.
    """
    max_chars_per_line: int = Field(
        default=42, ge=10, le=80,
        description="Số lượng ký tự tối đa trên một dòng trước khi ngắt câu"
    )
    font_name: str = Field(
        default="Arial",
        description="Tên phông chữ hiển thị (ví dụ: Arial, Calibri...)"
    )
    font_size: int = Field(
        default=48, ge=10, le=200,
        description="Kích thước chữ"
    )
    color_hex: str = Field(
        default="#FFFFFF",
        description="Mã màu chữ dạng Hex (mặc định là Trắng)"
    )
    shadow_enabled: bool = Field(
        default=True,
        description="Bật hiệu ứng đổ bóng cho chữ để tăng độ tương phản đọc"
    )

class ResolveAutomation:
    """
    Lớp điều khiển tương tác tự động hóa với DaVinci Resolve thông qua cổng fusionscript API.
    """
    def __init__(self):
        self.resolve = None
        self.project_manager = None
        self.current_project = None

    def connect(self) -> bool:
        """
        Kết nối tới ứng dụng DaVinci Resolve đang chạy.
        Tự động đăng ký đường dẫn Modules của Resolve trên Windows vào sys.path.

        Returns:
            bool: True nếu kết nối thành công.
        """
        # Đường dẫn Module lập trình mặc định của DaVinci Resolve trên Windows
        resolve_script_path = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
        if os.path.exists(resolve_script_path) and resolve_script_path not in sys.path:
            sys.path.append(resolve_script_path)

        try:
            import DaVinciResolveScript as dvr_script
            # Gọi ứng dụng Resolve thông qua cổng FusionScript
            self.resolve = dvr_script.scriptapp("Resolve")
            if self.resolve:
                self.project_manager = self.resolve.GetProjectManager()
                self.current_project = self.project_manager.GetCurrentProject()
                return True
        except (ImportError, AttributeError):
            # Không tìm thấy thư viện SDK hoặc Resolve chưa được khởi chạy
            pass
        return False

    def ensure_resolve_running(self, log_callback: Optional[Any] = None) -> bool:
        """
        Kiểm tra xem DaVinci Resolve có đang chạy không.
        Nếu không, tự động khởi chạy ứng dụng Resolve.exe từ đường dẫn mặc định trên Windows.

        Args:
            log_callback (callable, optional): Hàm ghi log.

        Returns:
            bool: True nếu kết nối thành công (sau khi đã khởi chạy hoặc nếu đã chạy sẵn).
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if self.connect():
            return True

        resolve_exe = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
        if os.path.exists(resolve_exe):
            log(" 🔍 Không tìm thấy DaVinci Resolve đang chạy. Đang tự động mở DaVinci Resolve...")
            import subprocess
            import time
            try:
                subprocess.Popen([resolve_exe])
                log(" 🚀 Đang khởi động DaVinci Resolve... Vui lòng chờ vài giây và mở một dự án (Project).")
                
                # Thử kết nối lại trong vòng 15 giây
                for i in range(15):
                    time.sleep(1)
                    if self.connect():
                        log(" ✔ Đã kết nối thành công tới DaVinci Resolve!")
                        return True
                log(" ⚠ DaVinci Resolve đang tải. Vui lòng đảm bảo bạn đã mở một Project cụ thể.")
            except Exception as e:
                log(f" ❌ Không thể khởi chạy DaVinci Resolve tự động: {str(e)}")
        else:
            log(" ❌ Không tìm thấy đường dẫn cài đặt mặc định của DaVinci Resolve tại C:\\Program Files\\...")
        return False

    def auto_detect_video_path(self) -> Optional[str]:
        """
        Tự động phát hiện đường dẫn tệp video đang hoạt động trong DaVinci Resolve.
        Thử theo thứ tự ưu tiên:
        1. Thử lấy từ các clip đang được chọn trong Media Pool (GetSelectedClips).
        2. Thử lấy từ clip nằm ngay dưới thanh trượt Playhead trên Timeline hiện tại (GetCurrentVideoItem).
        3. Thử lấy clip đầu tiên trên track Video 1 của Timeline hiện tại.

        Returns:
            str: Đường dẫn tuyệt đối tới tệp video nguồn, hoặc None nếu không phát hiện được.
        """
        if not self.resolve or not self.current_project:
            if not self.connect():
                return None

        # 1. Thử lấy từ các clip đang chọn trong Media Pool
        try:
            media_pool = self.current_project.GetMediaPool()
            if media_pool:
                selected_clips = media_pool.GetSelectedClips()
                if selected_clips:
                    file_path = selected_clips[0].GetClipProperty("File Path")
                    if file_path and os.path.exists(file_path):
                        return file_path
        except Exception:
            pass

        # 2. Thử lấy từ clip dưới Playhead trên Timeline hiện tại
        try:
            timeline = self.get_active_timeline()
            if timeline:
                current_video_item = timeline.GetCurrentVideoItem()
                if current_video_item:
                    media_pool_item = current_video_item.GetMediaPoolItem()
                    if media_pool_item:
                        file_path = media_pool_item.GetClipProperty("File Path")
                        if file_path and os.path.exists(file_path):
                            return file_path
        except Exception:
            pass

        # 3. Thử lấy clip đầu tiên trên track Video 1
        try:
            timeline = self.get_active_timeline()
            if timeline:
                video_items = timeline.GetItemListInTrack("video", 1)
                if video_items:
                    for item in video_items:
                        media_pool_item = item.GetMediaPoolItem()
                        if media_pool_item:
                            file_path = media_pool_item.GetClipProperty("File Path")
                            if file_path and os.path.exists(file_path):
                                return file_path
        except Exception:
            pass

        return None

    def get_active_timeline(self) -> Optional[Any]:
        """
        Lấy đối tượng Timeline đang mở và hoạt động trong DaVinci Resolve.

        Returns:
            object: Đối tượng Timeline của Resolve hoặc None nếu thất bại.
        """
        if not self.resolve or not self.current_project:
            if not self.connect():
                return None
        return self.current_project.GetCurrentTimeline()

    @staticmethod
    def generate_srt(subtitles: List[Dict[str, Any]], output_path: str) -> str:
        """
        Sinh tệp phụ đề định dạng SRT tiêu chuẩn từ mảng dữ liệu.
        Đây là giải pháp dự phòng và phân phối đa nền tảng tuyệt đối ổn định.

        Args:
            subtitles (List[Dict[str, Any]]): Mảng dữ liệu phụ đề chứa start, end, text.
            output_path (str): Đường dẫn lưu tệp SRT.

        Returns:
            str: Đường dẫn tệp SRT đã được tạo.
        """
        def format_time(seconds: float) -> str:
            # Chuyển đổi giây sang định dạng giờ:phút:giây,mili-giây của SRT (HH:MM:SS,mmm)
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            ms = int(round((seconds % 1) * 1000))
            if ms == 1000:
                ms = 999
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

        lines = []
        for idx, sub in enumerate(subtitles, 1):
            start_str = format_time(sub["start"])
            end_str = format_time(sub["end"])
            text = sub["text"]
            lines.append(f"{idx}")
            lines.append(f"{start_str} --> {end_str}")
            lines.append(f"{text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path

    def insert_subtitles_to_timeline(
        self,
        subtitles: List[Dict[str, Any]],
        config: SubtitleConfig,
        output_srt_path: Optional[str] = None,
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Thực hiện vẽ/chèn phụ đề trực tiếp lên Timeline DaVinci Resolve đang mở.

        Args:
            subtitles (List[Dict[str, Any]]): Mảng dữ liệu phụ đề cần vẽ.
            config (SubtitleConfig): Cấu hình kiểu dáng chữ.
            output_srt_path (str, optional): Đường dẫn tệp SRT đầu ra mong muốn.
            log_callback (callable, optional): Hàm để ghi log về GUI hoặc console.

        Returns:
            bool: True nếu chèn thành công hoặc xuất file SRT thành công.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        timeline = self.get_active_timeline()
        
        # Đường dẫn tệp SRT đầu ra
        if output_srt_path:
            srt_path = output_srt_path
        else:
            srt_path = os.path.join(os.environ.get("TEMP", "."), "temp_subtitles.srt")
            
        self.generate_srt(subtitles, srt_path)

        if not timeline:
            log(" ❌ Không tìm thấy Timeline DaVinci Resolve đang mở.")
            log(f" [INFO] Đã xuất file phụ đề SRT cục bộ thành công tại:\n 👉 {os.path.abspath(srt_path)}")
            return True

        try:
            # DaVinci Resolve 18+ cung cấp hàm ImportSubtitle trực tiếp trên đối tượng Timeline
            # Chèn tự động thành một Subtitle Track độc lập
            success = timeline.ImportSubtitle(srt_path)
            if success:
                log(" ✔ Tự động đồng bộ và chèn phụ đề thành công lên Timeline DaVinci Resolve!")
                return True
        except Exception as e:
            log(f" ⚠️ Lỗi khi import phụ đề vào Resolve: {str(e)}")

        log(" ➖ Kết nối thành công Resolve nhưng phiên bản API hiện tại yêu cầu thao tác kéo thả.")
        log(f" Đường dẫn file SRT:\n 👉 {os.path.abspath(srt_path)}")
        return True

    def import_edl_to_timeline(
        self,
        edl_path: str,
        video_path: str,
        timeline_name: str,
        log_callback: Optional[Any] = None
    ) -> bool:
        """
        Import tệp EDL để tạo Timeline mới đã được cắt khoảng lặng trong DaVinci Resolve.

        Args:
            edl_path (str): Đường dẫn tệp EDL (.edl).
            video_path (str): Đường dẫn tệp video gốc.
            timeline_name (str): Tên Timeline mới muốn tạo.
            log_callback (callable, optional): Hàm ghi log.

        Returns:
            bool: True nếu import thành công.
        """
        def log(msg: str):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        if not self.resolve or not self.current_project:
            if not self.connect():
                log(" ❌ Không thể kết nối tới ứng dụng DaVinci Resolve để import EDL.")
                return False

        media_pool = self.current_project.GetMediaPool()
        if not media_pool:
            log(" ❌ Không tìm thấy Media Pool trong dự án DaVinci Resolve hiện tại.")
            return False

        import_options = {
            "timelineName": timeline_name,
            "importSourceClips": True,
            "sourceClipsPath": os.path.abspath(os.path.dirname(video_path))
        }

        try:
            timeline = media_pool.ImportTimelineFromFile(os.path.abspath(edl_path), import_options)
            if timeline:
                log(f" 🎉 Đã tự động import EDL và tạo Timeline '{timeline_name}' thành công trong DaVinci Resolve!")
                return True
        except Exception as e:
            log(f" ⚠️ Lỗi khi gọi API ImportTimelineFromFile: {str(e)}")

        log(" ➖ Cần import thủ công tệp EDL vào DaVinci Resolve.")
        return False

def is_vertical_video(video_path: str) -> bool:
    """
    Kiểm tra xem video là dọc (portrait) hay ngang (landscape) bằng ffprobe.
    """
    try:
        import ffmpeg
        probe = ffmpeg.probe(video_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            width = int(video_stream.get('width', 1920))
            height = int(video_stream.get('height', 1080))
            return height > width
    except Exception:
        pass
    return False

def split_subtitles(subtitles: List[Dict[str, Any]], max_chars: int) -> List[Dict[str, Any]]:
    """
    Tách các phân đoạn phụ đề dựa trên số ký tự tối đa trên một dòng,
    sử dụng thông tin từ đơn (word-level timestamps) để tính toán mốc thời gian chính xác.
    """
    new_subtitles = []
    words = []
    
    for seg in subtitles:
        if seg.get("words"):
            words.extend(seg["words"])
        else:
            # Dự phòng nếu không có thông tin từ đơn
            text_words = seg["text"].split()
            if text_words:
                num_words = len(text_words)
                seg_dur = seg["end"] - seg["start"]
                word_dur = seg_dur / num_words if num_words > 0 else 0
                for i, w_text in enumerate(text_words):
                    words.append({
                        "word": w_text,
                        "start": seg["start"] + i * word_dur,
                        "end": seg["start"] + (i + 1) * word_dur
                    })
                    
    if not words:
        return subtitles

    current_segment_words = []
    current_len = 0
    
    for word_info in words:
        word = word_info["word"].strip()
        if not word:
            continue
            
        additional_len = len(word) + (1 if current_len > 0 else 0)
        
        # Kiểm tra khoảng cách im lặng giữa 2 từ để ngắt câu tự nhiên
        silence_gap = 0.0
        if current_segment_words:
            silence_gap = word_info["start"] - current_segment_words[-1]["end"]
            
        if (current_len > 0 and current_len + additional_len > max_chars) or silence_gap > 1.5:
            if current_segment_words:
                new_subtitles.append({
                    "start": current_segment_words[0]["start"],
                    "end": current_segment_words[-1]["end"],
                    "text": " ".join([w["word"].strip() for w in current_segment_words])
                })
            current_segment_words = [word_info]
            current_len = len(word)
        else:
            current_segment_words.append(word_info)
            current_len += additional_len
            
    if current_segment_words:
        new_subtitles.append({
            "start": current_segment_words[0]["start"],
            "end": current_segment_words[-1]["end"],
            "text": " ".join([w["word"].strip() for w in current_segment_words])
        })
        
    return new_subtitles
