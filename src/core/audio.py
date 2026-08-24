import os
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
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Không tìm thấy tệp video nguồn tại: {video_path}")

        try:
            # Tương đương lệnh: ffmpeg -y -i <video_path> -ac 1 -ar 16000 <output_wav_path>
            stream = ffmpeg.input(video_path)
            stream = ffmpeg.output(stream, output_wav_path, ac=1, ar=16000, loglevel="error")
            ffmpeg.run(stream, overwrite_output=True)
            return True
        except ffmpeg.Error as e:
            # Lấy thông báo lỗi chi tiết từ stderr của FFmpeg
            stderr_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
            raise RuntimeError(f"Lỗi FFmpeg khi trích xuất âm thanh: {stderr_msg}") from e

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
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Không tìm thấy tệp tại: {file_path}")

        try:
            probe = ffmpeg.probe(file_path)
            format_info = probe.get("format", {})
            duration = format_info.get("duration")
            if duration is not None:
                return float(duration)
            raise ValueError("Không tìm thấy trường thời lượng (duration) trong siêu dữ liệu định dạng.")
        except ffmpeg.Error as e:
            stderr_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
            raise RuntimeError(f"Lỗi ffprobe khi đọc siêu dữ liệu: {stderr_msg}") from e
