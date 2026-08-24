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

    def insert_subtitles_to_timeline(self, subtitles: List[Dict[str, Any]], config: SubtitleConfig) -> bool:
        """
        Thực hiện vẽ/chèn phụ đề trực tiếp lên Timeline DaVinci Resolve đang mở.

        Args:
            subtitles (List[Dict[str, Any]]): Mảng dữ liệu phụ đề cần vẽ.
            config (SubtitleConfig): Cấu hình kiểu dáng chữ.

        Returns:
            bool: True nếu chèn thành công hoặc xuất file SRT thành công.
        """
        timeline = self.get_active_timeline()
        
        # Đường dẫn tệp SRT đầu ra
        temp_srt = os.path.join(os.environ.get("TEMP", "."), "temp_subtitles.srt")
        self.generate_srt(subtitles, temp_srt)

        if not timeline:
            print(" [INFO] Đã xuất file phụ đề SRT cục bộ thành công.")
            return True

        try:
            # DaVinci Resolve 18+ cung cấp hàm ImportSubtitle trực tiếp trên đối tượng Timeline
            # Chèn tự động thành một Subtitle Track độc lập
            success = timeline.ImportSubtitle(temp_srt)
            if success:
                print(" ✔ Tự động đồng bộ và chèn phụ đề thành công lên Timeline DaVinci Resolve!")
                return True
        except Exception:
            pass

        print(" ➖ Kết nối thành công Resolve nhưng phiên bản API hiện tại yêu cầu thao tác kéo thả.")
        print(f" Đường dẫn file SRT: {temp_srt}")
        return True
