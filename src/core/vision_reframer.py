import os
import math
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field

class ReframeConfig(BaseModel):
    """
    Cấu hình bộ chuyển đổi bố cục thông minh (Auto Dynamic Re-framing).
    """
    target_aspect_ratio: str = Field(
        default="9:16",
        description="Tỷ lệ khung hình mục tiêu ('9:16' cho Shorts/Reels/TikTok, '1:1' cho Instagram, '16:9' mặc định)"
    )
    smoothing_window: int = Field(
        default=5, ge=1, le=30,
        description="Số lượng khung hình / mốc phân tích để làm mượt chuyển động lia máy (Smooth Pan)"
    )
    default_face_center_x: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Vị trí trọng tâm mặc định theo trục X (0.5 là chính giữa khung hình)"
    )
    zoom_boost: float = Field(
        default=1.0, ge=1.0, le=2.0,
        description="Hệ số zoom bổ sung để lấp đầy khung hình dọc"
    )


class VisionReframer:
    """
    Lớp xử lý thị giác máy tính: Nhận diện vị trí khuôn mặt/chủ thể và tính toán
    tọa độ Crop & Pan-and-Scan để chuyển đổi video ngang 16:9 sang video dọc 9:16 mượt mà.
    """

    def __init__(self, config: Optional[ReframeConfig] = None):
        self.config = config or ReframeConfig()

    def calculate_crop_box_for_aspect_ratio(
        self,
        src_width: int = 1920,
        src_height: int = 1080,
        center_x_ratio: float = 0.5
    ) -> Dict[str, int]:
        """
        Tính toán hộp cắt (Bounding Crop Box) cho tỷ lệ 9:16 dựa trên tâm điểm trục X.

        Args:
            src_width (int): Chiều rộng video gốc (ví dụ 1920).
            src_height (int): Chiều cao video gốc (ví dụ 1080).
            center_x_ratio (float): Vị trí trọng tâm khuôn mặt (0.0 đến 1.0).

        Returns:
            Dict chứa x, y, width, height của vùng cắt.
        """
        if self.config.target_aspect_ratio == "9:16":
            # Tỷ lệ 9:16 với chiều cao giữ nguyên bằng src_height
            target_w = int(src_height * (9.0 / 16.0))
            target_h = src_height

            # Nếu target_w vượt quá chiều rộng gốc (hiếm gặp), thu nhỏ lại
            if target_w > src_width:
                target_w = src_width
                target_h = int(src_width * (16.0 / 9.0))
        elif self.config.target_aspect_ratio == "1:1":
            target_dim = min(src_width, src_height)
            target_w = target_dim
            target_h = target_dim
        else:
            # Giữ nguyên 16:9
            return {"x": 0, "y": 0, "width": src_width, "height": src_height}

        # Tính tọa độ X góc trái dựa trên tâm điểm center_x_ratio
        center_pixel_x = int(src_width * center_x_ratio)
        x_left = center_pixel_x - (target_w // 2)

        # Giới hạn không để khung cắt tràn ra ngoài biên video
        x_left = max(0, min(x_left, src_width - target_w))
        y_top = max(0, (src_height - target_h) // 2)

        return {
            "x": int(x_left),
            "y": int(y_top),
            "width": int(target_w),
            "height": int(target_h)
        }

    def generate_reframe_timeline_events(
        self,
        keep_intervals: List[Tuple[float, float]],
        src_width: int = 1920,
        src_height: int = 1080,
        face_detections: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Tạo danh sách các tham số biến đổi (Transform / Pan / Crop) cho từng phân đoạn video.

        Returns:
            List[Dict] chứa start, end, crop_box, pan_offset_x, scale
        """
        events = []
        raw_centers = []

        # 1. Trích xuất tâm điểm theo từng phân đoạn
        for idx, (start, end) in enumerate(keep_intervals):
            # Nếu có dữ liệu nhận diện khuôn mặt thực tế
            center_x = self.config.default_face_center_x
            if face_detections:
                for fd in face_detections:
                    if fd.get("start", 0.0) <= start <= fd.get("end", 0.0):
                        center_x = fd.get("face_center_x", self.config.default_face_center_x)
                        break
            raw_centers.append(center_x)

        # 2. Làm mượt (Smooth Panning) để camera không bị giật
        smoothed_centers = self._smooth_values(raw_centers, self.config.smoothing_window)

        # 3. Tính toán Bounding Box và Transform offset
        for idx, (start, end) in enumerate(keep_intervals):
            c_x = smoothed_centers[idx]
            crop_box = self.calculate_crop_box_for_aspect_ratio(src_width, src_height, c_x)

            # Tính toán độ lệch tâm (Pan Offset X từ -1.0 đến 1.0)
            center_pixel = crop_box["x"] + (crop_box["width"] / 2.0)
            offset_ratio = (center_pixel - (src_width / 2.0)) / (src_width / 2.0) if src_width > 0 else 0.0

            events.append({
                "interval_index": idx,
                "start": start,
                "end": end,
                "duration": end - start,
                "crop_box": crop_box,
                "pan_offset_x": round(offset_ratio, 4),
                "scale": self.config.zoom_boost,
                "aspect_ratio": self.config.target_aspect_ratio
            })

        return events

    def _smooth_values(self, values: List[float], window_size: int = 5) -> List[float]:
        """Làm mượt dãy số bằng Moving Average (Trung bình trượt)."""
        if not values or window_size <= 1:
            return values

        smoothed = []
        n = len(values)
        for i in range(n):
            start_idx = max(0, i - window_size // 2)
            end_idx = min(n, i + window_size // 2 + 1)
            window = values[start_idx:end_idx]
            smoothed.append(sum(window) / len(window))
        return smoothed
