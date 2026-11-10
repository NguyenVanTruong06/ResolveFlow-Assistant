import json
import os
import subprocess
import ffmpeg

from src.core.ffmpeg_utils import get_ffmpeg_path, run_ffprobe
from src.utils.subprocess_utils import clean_media_subprocess_env, no_window_kwargs


class AudioExtractor:
    """
    Lớp xử lý trích xuất âm thanh từ tệp video nguồn sử dụng công cụ FFmpeg.
    """

    @staticmethod
    def extract_audio(video_path: str, output_wav_path: str) -> bool:
        """
        Trích xuất kênh âm thanh từ video sang định dạng WAV chuẩn (16kHz, mono, 16-bit).
        Đây là định dạng tối ưu nhất cho đầu vào của mô hình Speech-to-Text Whisper.

        Args:
            video_path (str): Đường dẫn tuyệt đối tới tệp video nguồn.
            output_wav_path (str): Đường dẫn lưu tệp WAV kết quả.

        Returns:
            bool: True nếu trích xuất thành công.

        Raises:
            FileNotFoundError: Nếu không tìm thấy tệp video nguồn.
            RuntimeError: Nếu FFmpeg gặp lỗi trong quá trình xử lý.
        """
        norm_video = os.path.normpath(os.path.abspath(video_path))
        norm_output = os.path.normpath(os.path.abspath(output_wav_path))

        if not os.path.exists(norm_video):
            raise FileNotFoundError(f"Không tìm thấy tệp video nguồn tại: {norm_video}")

        # Đảm bảo thư mục cha của output tồn tại
        out_dir = os.path.dirname(norm_output)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        ffmpeg_bin = get_ffmpeg_path() or "ffmpeg"
        clean_env = clean_media_subprocess_env()
        nw_kwargs = no_window_kwargs()

        # 1. Primary path: ffmpeg-python node graph compile kết hợp subprocess với clean_env
        try:
            stream = ffmpeg.input(norm_video)
            stream = ffmpeg.output(stream, norm_output, ac=1, ar=16000, vn=None, loglevel="error")
            cmd = ffmpeg.compile(stream, cmd=ffmpeg_bin, overwrite_output=True)
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
                env=clean_env,
                **nw_kwargs,
            )
            if res.returncode != 0:
                stderr_msg = res.stderr.strip() if res.stderr else f"ffmpeg returned exit code {res.returncode}"
                raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {stderr_msg}")
        except Exception as e:
            # 2. Fallback path: Lệnh subprocess trực tiếp với clean_env
            try:
                fb_cmd = [ffmpeg_bin, "-y", "-i", norm_video, "-ac", "1", "-ar", "16000", "-vn", norm_output]
                fb_res = subprocess.run(
                    fb_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                    encoding="utf-8",
                    errors="replace",
                    env=clean_env,
                    **nw_kwargs,
                )
                if fb_res.returncode != 0:
                    fb_stderr = fb_res.stderr.strip() if fb_res.stderr else str(fb_res.returncode)
                    raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {fb_stderr}") from e
            except Exception as sub_e:
                # Bảo toàn đầy đủ stderr gốc, không làm mất chuỗi lỗi chính xác
                raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {sub_e}") from sub_e

        if not os.path.exists(norm_output) or os.path.getsize(norm_output) == 0:
            raise RuntimeError(f"Không thể tạo tệp âm thanh WAV tại: {norm_output}")

        return True

    @staticmethod
    def get_audio_duration(file_path: str) -> float:
        """
        Lấy thời lượng (giây) của tệp âm thanh hoặc video sử dụng công cụ ffprobe.

        Args:
            file_path (str): Đường dẫn tới tệp cần phân tích.

        Returns:
            float: Thời lượng tệp tính bằng giây.

        Raises:
            FileNotFoundError: Nếu không tìm thấy tệp tin.
            RuntimeError: Nếu ffprobe gặp lỗi hoặc không tìm thấy dữ liệu thời lượng.
        """
        norm_path = os.path.normpath(os.path.abspath(file_path))
        if not os.path.exists(norm_path):
            raise FileNotFoundError(f"Không tìm thấy tệp tại: {norm_path}")

        try:
            from src.core.resolve_api import _VIDEO_META_CACHE
            if norm_path in _VIDEO_META_CACHE and _VIDEO_META_CACHE[norm_path].get("duration", 0.0) > 0:
                return _VIDEO_META_CACHE[norm_path]["duration"]
        except Exception:
            pass

        # Thực thi qua run_ffprobe trực tiếp với môi trường đã khử ô nhiễm
        try:
            res = run_ffprobe(
                [
                    "-v", "error",
                    "-show_format",
                    "-show_streams",
                    "-of", "json",
                    norm_path,
                ],
                timeout=30,
            )
        except Exception as probe_err:
            raise RuntimeError(f"Lỗi ffprobe khi đọc siêu dữ liệu: {probe_err}") from probe_err

        if res.returncode != 0:
            stderr_msg = res.stderr.strip() if res.stderr else f"ffprobe returned exit code {res.returncode}"
            raise RuntimeError(f"Lỗi ffprobe khi đọc siêu dữ liệu: {stderr_msg}")

        try:
            data = json.loads(res.stdout)
            format_info = data.get("format", {})
            duration = format_info.get("duration")
            if duration is not None:
                dur_val = float(duration)
                try:
                    from src.core.resolve_api import _VIDEO_META_CACHE
                    if norm_path not in _VIDEO_META_CACHE:
                        _VIDEO_META_CACHE[norm_path] = {}
                    _VIDEO_META_CACHE[norm_path]["duration"] = dur_val
                except Exception:
                    pass
                return dur_val
            raise ValueError("Không tìm thấy trường thời lượng (duration) trong siêu dữ liệu định dạng.")
        except Exception as parse_exc:
            raise RuntimeError(f"Lỗi phân tích cú pháp siêu dữ liệu ffprobe: {parse_exc}") from parse_exc
