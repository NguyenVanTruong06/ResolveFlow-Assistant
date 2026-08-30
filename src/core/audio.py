import os
import subprocess
import ffmpeg

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

        try:
            # Tương đương lệnh: ffmpeg -y -i <video_path> -ac 1 -ar 16000 -vn <output_wav_path>
            stream = ffmpeg.input(norm_video)
            stream = ffmpeg.output(stream, norm_output, ac=1, ar=16000, vn=None, loglevel="error")
            ffmpeg.run(stream, overwrite_output=True)
        except ffmpeg.Error as e:
            # Fallback trực tiếp bằng subprocess nếu ffmpeg-python gặp sự cố
            try:
                cmd = ["ffmpeg", "-y", "-i", norm_video, "-ac", "1", "-ar", "16000", "-vn", norm_output]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
                if res.returncode != 0:
                    stderr_msg = res.stderr or (e.stderr.decode("utf-8") if e.stderr else str(e))
                    raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {stderr_msg}") from e
            except Exception as sub_e:
                stderr_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
                raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {stderr_msg}") from sub_e

        if not os.path.exists(norm_output) or os.path.getsize(norm_output) == 0:
            # Thử lần cuối bằng lệnh subprocess trực tiếp
            cmd = ["ffmpeg", "-y", "-i", norm_video, "-ac", "1", "-ar", "16000", "-vn", norm_output]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
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
            probe = ffmpeg.probe(norm_path)
            format_info = probe.get("format", {})
            duration = format_info.get("duration")
            if duration is not None:
                return float(duration)
            raise ValueError("Không tìm thấy trường thời lượng (duration) trong siêu dữ liệu định dạng.")
        except ffmpeg.Error as e:
            stderr_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
            raise RuntimeError(f"Lỗi ffprobe khi đọc siêu dữ liệu: {stderr_msg}") from e
