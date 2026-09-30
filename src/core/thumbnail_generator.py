"""
Module trích xuất và tối ưu hóa khung hình vàng làm Thumbnail (Thumbnail Keyframe Extractor).
Sử dụng thuật toán đo độ sắc nét (Laplacian Sharpness), phát hiện chống mờ nhòe (Anti-motion blur)
và cân bằng độ tương phản/ánh sáng nhằm tìm ra các khoảnh khắc visual đắt giá nhất.
"""

import os
import subprocess
import tempfile
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageStat
from pydantic import BaseModel, Field
from src.core.autocut import seconds_to_timecode, get_media_metadata


class ThumbnailConfig(BaseModel):
    """
    Cấu hình tham số trích xuất Thumbnail.
    """
    top_n: int = Field(default=3, ge=1, le=10, description="Số lượng khung hình đẹp nhất cần trích xuất")
    sample_interval_sec: float = Field(default=2.0, ge=0.5, le=30.0, description="Khoảng cách lấy mẫu định kỳ (giây)")
    min_time_distance_sec: float = Field(default=3.0, ge=1.0, le=60.0, description="Khoảng cách tối thiểu giữa các thumbnail được chọn (chống trùng lặp)")
    image_format: str = Field(default="PNG", description="Định dạng ảnh đầu ra ('PNG' hoặc 'JPEG')")
    min_brightness: float = Field(default=35.0, ge=0.0, le=255.0, description="Ngưỡng độ sáng tối thiểu (loại bỏ khung hình quá tối)")
    max_brightness: float = Field(default=225.0, ge=0.0, le=255.0, description="Ngưỡng độ sáng tối đa (loại bỏ khung hình quá chói/cháy sáng)")


class ThumbnailCandidate(BaseModel):
    """
    Thông tin một ứng viên khung hình Thumbnail.
    """
    index: int
    timestamp_sec: float
    timecode: str
    overall_score: float
    sharpness_score: float
    contrast_score: float
    brightness: float
    image_path: str
    width: int = 1920
    height: int = 1080


class ThumbnailExtractor:
    """
    Bộ động cơ phân tích thị giác và trích xuất Thumbnail tự động.
    """

    @staticmethod
    def compute_laplacian_variance(gray_array: np.ndarray) -> float:
        """
        Tính toán phương sai Laplacian (Laplacian Variance) bằng numpy vectorization siêu tốc.
        Chỉ số này càng cao thì hình ảnh càng sắc nét và ít bị mờ do chuyển động.
        """
        if gray_array.ndim != 2 or gray_array.shape[0] < 3 or gray_array.shape[1] < 3:
            return 0.0

        img = gray_array.astype(np.float32)
        # Nhân chập 2D với Kernel Laplacian 3x3: [[0, 1, 0], [1, -4, 1], [0, 1, 0]]
        lap = (
            img[:-2, 1:-1] +
            img[2:, 1:-1] +
            img[1:-1, :-2] +
            img[1:-1, 2:] -
            4.0 * img[1:-1, 1:-1]
        )
        return float(np.var(lap))

    @staticmethod
    def analyze_image_quality(img: Image.Image, config: ThumbnailConfig) -> Dict[str, float]:
        """
        Đánh giá chất lượng của một khung hình:
        - Sharpness (Laplacian variance)
        - Brightness & Contrast (Mean & Standard Deviation)
        - Exposure penalty nếu khung hình quá tối hoặc quá sáng
        """
        # Chuyển sang ảnh xám grayscale
        gray = img.convert("L")
        gray_arr = np.array(gray)

        sharpness = ThumbnailExtractor.compute_laplacian_variance(gray_arr)

        stat = ImageStat.Stat(gray)
        brightness = float(stat.mean[0]) if stat.mean else 128.0
        contrast = float(stat.stddev[0]) if stat.stddev else 40.0

        # Phạt điểm nếu rơi vào vùng phơi sáng cực đoan (đen xì hoặc trắng xóa)
        exposure_penalty = 1.0
        if brightness < config.min_brightness:
            diff = config.min_brightness - brightness
            exposure_penalty = max(0.05, 1.0 - (diff / config.min_brightness))
        elif brightness > config.max_brightness:
            diff = brightness - config.max_brightness
            max_diff = 255.0 - config.max_brightness
            exposure_penalty = max(0.05, 1.0 - (diff / max_diff) if max_diff > 0 else 0.5)

        # Tính toán điểm tổng hợp (Overall Quality Score)
        # Sharpness đóng vai trò chủ đạo, kết hợp với contrast và exposure
        overall_score = (sharpness * 0.7 + contrast * 2.5) * exposure_penalty

        return {
            "sharpness": round(sharpness, 2),
            "contrast": round(contrast, 2),
            "brightness": round(brightness, 2),
            "overall_score": round(overall_score, 2)
        }

    @staticmethod
    def extract_single_frame(video_path: str, timestamp_sec: float, output_image_path: str) -> bool:
        """
        Trích xuất một khung hình đơn lẻ từ video tại timestamp_sec bằng FFmpeg.
        """
        if not os.path.exists(video_path):
            return False

        parent_dir = os.path.dirname(output_image_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        cmd = [
            "ffmpeg", "-y", "-ss", f"{timestamp_sec:.3f}",
            "-i", os.path.abspath(video_path),
            "-vframes", "1",
            "-q:v", "2",
            os.path.abspath(output_image_path)
        ]

        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30, encoding="utf-8", errors="replace"
            )
            return (res.returncode == 0) and os.path.exists(output_image_path) and os.path.getsize(output_image_path) > 0
        except Exception:
            return False

    @classmethod
    def generate_thumbnails(
        cls,
        video_path: str,
        output_dir: Optional[str] = None,
        config: Optional[ThumbnailConfig] = None,
        timestamps_to_check: Optional[List[float]] = None
    ) -> List[ThumbnailCandidate]:
        """
        Quét và trích xuất danh sách Top N khung hình Thumbnail đẹp nhất.
        """
        cfg = config or ThumbnailConfig()
        meta = get_media_metadata(video_path)
        duration = meta.get("duration", 0.0)
        fps = meta.get("fps", 30.0)
        width = meta.get("width", 1920)
        height = meta.get("height", 1080)

        if duration <= 0:
            duration = 10.0

        if output_dir is None:
            output_dir = os.path.dirname(os.path.abspath(video_path))
        os.makedirs(output_dir, exist_ok=True)

        # 1. Xác định danh sách timestamp cần phân tích
        candidate_timestamps = []
        if timestamps_to_check:
            for ts in timestamps_to_check:
                if 0 <= ts <= duration:
                    candidate_timestamps.append(ts)

        # Lấy mẫu định kỳ cách đều nhau
        current_ts = min(1.0, duration * 0.1) # Bỏ qua 1s đầu tiên tránh fade-in đen
        while current_ts < duration - 0.5:
            candidate_timestamps.append(current_ts)
            current_ts += cfg.sample_interval_sec

        candidate_timestamps = sorted(list(set([round(t, 2) for t in candidate_timestamps])))
        if not candidate_timestamps:
            candidate_timestamps = [duration * 0.2, duration * 0.5, duration * 0.8]

        # 2. Phân tích từng frame qua thư mục tạm
        evaluated_candidates = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for idx, ts in enumerate(candidate_timestamps):
                temp_frame_path = os.path.join(temp_dir, f"frame_{idx:04d}.jpg")
                success = cls.extract_single_frame(video_path, ts, temp_frame_path)
                if not success:
                    continue

                try:
                    with Image.open(temp_frame_path) as img:
                        metrics = cls.analyze_image_quality(img, cfg)
                        evaluated_candidates.append({
                            "timestamp_sec": ts,
                            "metrics": metrics,
                            "temp_path": temp_frame_path
                        })
                except Exception:
                    continue

            # 3. Sắp xếp theo overall_score giảm dần và lọc chống trùng lặp thời gian
            evaluated_candidates.sort(key=lambda x: x["metrics"]["overall_score"], reverse=True)

            selected_candidates = []
            selected_timestamps = []

            for cand in evaluated_candidates:
                ts = cand["timestamp_sec"]
                # Kiểm tra khoảng cách thời gian với các frame đã chọn
                is_far_enough = all(abs(ts - s_ts) >= cfg.min_time_distance_sec for s_ts in selected_timestamps)
                if is_far_enough or len(selected_timestamps) == 0:
                    selected_candidates.append(cand)
                    selected_timestamps.append(ts)
                    if len(selected_candidates) >= cfg.top_n:
                        break

            # Nếu chưa đủ top_n, bổ sung thêm
            if len(selected_candidates) < cfg.top_n:
                for cand in evaluated_candidates:
                    if cand not in selected_candidates:
                        selected_candidates.append(cand)
                        if len(selected_candidates) >= cfg.top_n:
                            break

            # 4. Xuất các file ảnh Thumbnail kết quả chính thức vào output_dir
            results = []
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            ext = "png" if cfg.image_format.upper() == "PNG" else "jpg"

            for rank, item in enumerate(selected_candidates, 1):
                ts = item["timestamp_sec"]
                metrics = item["metrics"]
                final_filename = f"{base_name}_Thumbnail_{rank:02d}.{ext}"
                final_path = os.path.join(output_dir, final_filename)

                # Trích xuất ảnh gốc chất lượng cao nhất tại timestamp này
                cls.extract_single_frame(video_path, ts, final_path)

                tc_str = seconds_to_timecode(ts, fps=fps)
                results.append(ThumbnailCandidate(
                    index=rank,
                    timestamp_sec=ts,
                    timecode=tc_str,
                    overall_score=metrics["overall_score"],
                    sharpness_score=metrics["sharpness"],
                    contrast_score=metrics["contrast"],
                    brightness=metrics["brightness"],
                    image_path=os.path.abspath(final_path),
                    width=width,
                    height=height
                ))

            return results
