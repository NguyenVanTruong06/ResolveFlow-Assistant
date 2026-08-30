import os
import sys
import subprocess
import json
from typing import List, Dict, Any
from pydantic import BaseModel

class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: List[str] = []
    warnings: List[str] = []

class DryRunValidator:
    """
    Bộ kiểm tra chẩn đoán tĩnh (Dry-run Validator) giúp phân tích khả năng tương thích của các tệp nguồn
    với DaVinci Resolve và định cấu hình các cảnh báo tương ứng.
    """

    @staticmethod
    def get_video_stream_info(video_path: str) -> Dict[str, Any]:
        """
        Trích xuất thông tin chi tiết của stream video bằng ffprobe.
        """
        info = {
            "codec_name": "",
            "pix_fmt": "",
            "fps": 30.0,
            "width": 0,
            "height": 0,
            "duration": 0.0
        }
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return info

        probe = None
        try:
            import ffmpeg
            probe = ffmpeg.probe(video_path)
        except Exception:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", video_path
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
                probe = json.loads(res.stdout)
            except Exception:
                return info

        if not probe:
            return info

        video_stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
        format_data = probe.get('format', {})

        if video_stream:
            info["codec_name"] = video_stream.get("codec_name", "").lower()
            info["pix_fmt"] = video_stream.get("pix_fmt", "").lower()
            info["width"] = int(video_stream.get("width", 0))
            info["height"] = int(video_stream.get("height", 0))

            avg_frame_rate = video_stream.get('avg_frame_rate', video_stream.get('r_frame_rate', '30/1'))
            if '/' in avg_frame_rate:
                num, den = map(float, avg_frame_rate.split('/'))
                info["fps"] = num / den if den != 0 else 30.0
            else:
                try:
                    info["fps"] = float(avg_frame_rate)
                except (ValueError, TypeError):
                    info["fps"] = 30.0

        try:
            info["duration"] = float(format_data.get('duration', 0.0))
        except (ValueError, TypeError):
            pass

        return info

    @classmethod
    def validate_media_files(cls, video_paths: List[str]) -> ValidationResult:
        """
        Thực hiện chuỗi kiểm tra trước khi xuất file:
        - Tồn tại tệp và dung lượng lớn hơn 0
        - Codec H.264/H.265 10-bit cảnh báo màn hình đỏ trên Windows Resolve Free
        - FPS lệch nhau giữa các clip nguồn
        """
        result = ValidationResult()
        
        if not video_paths:
            result.is_valid = False
            result.errors.append("Không có tệp video nào được chọn để kiểm tra.")
            return result

        fps_list = []
        
        for path in video_paths:
            # 1. Kiểm tra sự tồn tại của file
            if not os.path.exists(path):
                result.is_valid = False
                result.errors.append(f"Tệp không tồn tại: {path}")
                continue
            
            if os.path.getsize(path) == 0:
                result.is_valid = False
                result.errors.append(f"Tệp video bị rỗng (0 bytes): {path}")
                continue

            # Trích xuất thông tin
            info = cls.get_video_stream_info(path)
            fps_list.append((path, info["fps"]))

            # 2. Kiểm tra codec 10-bit cho Windows Resolve Free
            codec = info["codec_name"]
            pix_fmt = info["pix_fmt"]
            
            is_10bit = "10" in pix_fmt or pix_fmt.endswith("10le") or pix_fmt.endswith("10be")
            is_h264_h265 = codec in ["h264", "hevc"]
            
            # Resolve Free trên Windows có giới hạn về H.264/H.265 10-bit
            if is_h264_h265 and is_10bit and sys.platform.startswith("win"):
                base_name = os.path.basename(path)
                result.warnings.append(
                    f"Tệp '{base_name}' sử dụng codec 10-bit ({codec.upper()} {pix_fmt}). "
                    f"DaVinci Resolve phiên bản Free trên Windows KHÔNG hỗ trợ giải mã trực tiếp định dạng này "
                    f"(có thể dẫn đến lỗi màn hình đỏ 'Media Offline'). Đề xuất: Chuyển đổi (transcode) video sang 8-bit "
                    f"hoặc định dạng ProRes/DNxHR trước khi làm việc, hoặc nâng cấp lên Resolve Studio."
                )

        # 3. Kiểm tra tính đồng nhất của Frame Rate
        if len(fps_list) > 1:
            first_path, first_fps = fps_list[0]
            for path, fps in fps_list[1:]:
                if abs(fps - first_fps) > 0.01:
                    result.warnings.append(
                        f"Tốc độ khung hình (Frame Rate) không đồng nhất: "
                        f"'{os.path.basename(first_path)}' chạy ở {first_fps:.3f} FPS, "
                        f"trong khi '{os.path.basename(path)}' chạy ở {fps:.3f} FPS. "
                        f"Trộn các tốc độ khung hình khác nhau trên cùng một Timeline có thể gây lệch mốc thời gian "
                        f"và phụ đề một vài khung hình."
                    )
                    break # Chỉ cần cảnh báo 1 lần

        return result
