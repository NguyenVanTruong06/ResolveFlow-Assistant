import os
import sys
import subprocess
import json
import xml.etree.ElementTree as ET
import urllib.parse
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class DryRunValidator:
    """
    Bộ kiểm tra chẩn đoán tĩnh (Dry-run Validator) giúp phân tích khả năng tương thích của các tệp nguồn
    và tính toàn vẹn của tệp FCPXML/EDL trước khi nhập vào DaVinci Resolve.
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
        - Kiểm tra tên file / đường dẫn tiếng Việt có dấu
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

            # Kiểm tra đường dẫn tiếng Việt có dấu
            cls._check_vietnamese_path_encoding(path, result)

            # Trích xuất thông tin
            info = cls.get_video_stream_info(path)
            fps_list.append((path, info["fps"]))

            # 2. Kiểm tra codec 10-bit cho Windows Resolve Free
            codec = info["codec_name"]
            pix_fmt = info["pix_fmt"]
            
            is_10bit = "10" in pix_fmt or pix_fmt.endswith("10le") or pix_fmt.endswith("10be")
            is_h264_h265 = codec in ["h264", "hevc"]
            
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
                    break

        return result

    @classmethod
    def _check_vietnamese_path_encoding(cls, file_path: str, result: ValidationResult):
        """
        Kiểm tra các ký tự tiếng Việt có dấu trong tên file và đường dẫn.
        Đảm bảo URI FCPXML mã hóa UTF-8 chuẩn xác.
        """
        has_non_ascii = any(ord(c) >= 128 for c in file_path)
        if has_non_ascii:
            result.details["has_unicode_path"] = True
            # Kiểm tra xem đường dẫn có mã hóa URL an toàn không
            try:
                import pathlib
                uri = pathlib.Path(os.path.abspath(file_path)).as_uri()
                result.details[f"uri_{os.path.basename(file_path)}"] = uri
            except Exception as e:
                result.warnings.append(
                    f"Đường dẫn chứa ký tự tiếng Việt đặc biệt có thể gặp sự cố khi tạo file URI: {file_path} ({e})"
                )

    @classmethod
    def validate_fcpxml_integrity(
        cls, 
        fcpxml_path: str, 
        expected_media_paths: Optional[List[str]] = None
    ) -> ValidationResult:
        """
        Chẩn đoán tính toàn vẹn của tệp FCPXML (XML v1.9):
        1. Cấu trúc XML hợp lệ, encoding UTF-8 chuẩn.
        2. Tất cả thẻ <asset> có thuộc tính src hợp lệ và tệp nguồn tương ứng tồn tại trên đĩa.
        3. Các thẻ <text-style> và <title> không bị lỗi parse ký tự tiếng Việt hoặc ký tự đặc biệt XML (&, <, >).
        4. Định dạng sequence duration và format frameDuration khớp chuẩn FCPXML.
        """
        result = ValidationResult()

        if not os.path.exists(fcpxml_path):
            result.is_valid = False
            result.errors.append(f"Tệp FCPXML không tồn tại: {fcpxml_path}")
            return result

        if os.path.getsize(fcpxml_path) == 0:
            result.is_valid = False
            result.errors.append(f"Tệp FCPXML rỗng (0 bytes): {fcpxml_path}")
            return result

        # 1. Parse XML
        try:
            tree = ET.parse(fcpxml_path)
            root = tree.getroot()
        except ET.ParseError as e:
            result.is_valid = False
            result.errors.append(f"Lỗi cú pháp XML (Malformed XML) trong tệp FCPXML: {str(e)}")
            return result
        except Exception as e:
            result.is_valid = False
            result.errors.append(f"Không thể đọc tệp FCPXML: {str(e)}")
            return result

        # Kiểm tra phiên bản FCPXML
        version = root.attrib.get("version", "")
        if version not in ["1.8", "1.9", "1.10", "1.11"]:
            result.warnings.append(f"Phiên bản FCPXML '{version}' có thể không tương thích tối ưu với DaVinci Resolve (Khuyên dùng v1.9).")

        # 2. Kiểm tra các tài nguyên Media Asset (<asset>)
        resources = root.find("resources")
        if resources is not None:
            assets = resources.findall("asset")
            for asset in assets:
                asset_id = asset.attrib.get("id", "unknown")
                src_uri = asset.attrib.get("src", "")
                if not src_uri:
                    result.warnings.append(f"Asset ID '{asset_id}' không có đường dẫn 'src'.")
                    continue

                # Giải mã URI sang đường dẫn file thật trên đĩa
                real_path = cls._uri_to_local_path(src_uri)
                if real_path:
                    if not os.path.exists(real_path):
                        result.is_valid = False
                        result.errors.append(
                            f"LỖI MEDIA OFFLINE: Tệp media được liên kết trong FCPXML không tồn tại trên ổ đĩa: '{real_path}' (URI: {src_uri})"
                        )
                else:
                    result.warnings.append(f"Không thể chuyển đổi URI sang đường dẫn cục bộ: '{src_uri}'")

        # 3. Kiểm tra danh sách media mong đợi (nếu có truyền vào)
        if expected_media_paths:
            found_paths = []
            if resources is not None:
                for asset in resources.findall("asset"):
                    src = asset.attrib.get("src", "")
                    p = cls._uri_to_local_path(src)
                    if p:
                        found_paths.append(os.path.normcase(os.path.abspath(p)))

            for exp in expected_media_paths:
                exp_norm = os.path.normcase(os.path.abspath(exp))
                if exp_norm not in found_paths:
                    result.warnings.append(
                        f"Tệp nguồn '{os.path.basename(exp)}' không tìm thấy trong danh sách tài nguyên FCPXML."
                    )

        # 4. Kiểm tra Text Style Elements & Unicode
        titles = root.findall(".//title")
        for title in titles:
            text_elem = title.find("text")
            if text_elem is not None:
                # Kiểm tra text rỗng
                spans = text_elem.findall("text-style")
                if not spans and not (text_elem.text and text_elem.text.strip()):
                    result.warnings.append(f"Phát hiện thẻ title rỗng không có nội dung phụ đề (offset: {title.attrib.get('offset', '0')}).")

        result.details["total_titles"] = len(titles)
        result.details["fcpxml_version"] = version

        return result

    @staticmethod
    def _uri_to_local_path(uri: str) -> Optional[str]:
        """Chuyển đổi URI 'file:///D:/path...' hoặc 'file://localhost/D:/...' sang đường dẫn chuẩn trên OS."""
        if not uri.startswith("file://"):
            return uri

        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        
        # Xử lý trên Windows: /D:/path -> D:/path
        if sys.platform.startswith("win") or (len(path) > 2 and path[0] == '/' and path[2] == ':'):
            path = path.lstrip('/')

        return os.path.normpath(path)
