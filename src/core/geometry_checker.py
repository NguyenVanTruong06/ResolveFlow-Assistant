"""
Geometry & Layout Conflict Checker (Chống xung đột bố cục).
Học hỏi từ kiến trúc SeeCut:
- Tự động phát hiện va chạm giữa phụ đề và khuôn mặt người nói (Subtitle-Face Collision).
- Tự động phát hiện va chạm giữa cửa sổ PiP/B-roll và khuôn mặt (PiP-Speaker Occlusion).
- Tự động kẹp biên an toàn (Safe Zone Clamping) tránh các nút tương tác TikTok, Reels, Shorts.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """
    Hộp bao 2D chuẩn hóa trong không gian [0.0, 1.0] x [0.0, 1.0].
    Gốc (0, 0) ở góc trên bên trái, (1, 1) ở góc dưới bên phải.
    """
    x_min: float = Field(..., ge=-0.5, le=1.5, description="Tọa độ X mép trái")
    y_min: float = Field(..., ge=-0.5, le=1.5, description="Tọa độ Y mép trên")
    x_max: float = Field(..., ge=-0.5, le=1.5, description="Tọa độ X mép phải")
    y_max: float = Field(..., ge=-0.5, le=1.5, description="Tọa độ Y mép dưới")

    @property
    def width(self) -> float:
        return max(0.0, self.x_max - self.x_min)

    @property
    def height(self) -> float:
        return max(0.0, self.y_max - self.y_min)

    @property
    def center_x(self) -> float:
        return (self.x_min + self.x_max) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y_min + self.y_max) / 2.0

    @property
    def area(self) -> float:
        return self.width * self.height

    def intersects(self, other: "BoundingBox") -> bool:
        """Kiểm tra xem 2 hộp bao có giao nhau (chồng lấn) không."""
        return not (
            self.x_max <= other.x_min or
            self.x_min >= other.x_max or
            self.y_max <= other.y_min or
            self.y_min >= other.y_max
        )

    def intersection_area(self, other: "BoundingBox") -> float:
        """Tính diện tích phần giao nhau giữa 2 hộp bao."""
        ix_min = max(self.x_min, other.x_min)
        ix_max = min(self.x_max, other.x_max)
        iy_min = max(self.y_min, other.y_min)
        iy_max = min(self.y_max, other.y_max)

        if ix_max > ix_min and iy_max > iy_min:
            return (ix_max - ix_min) * (iy_max - iy_min)
        return 0.0

    def iou(self, other: "BoundingBox") -> float:
        """Tính chỉ số Intersection over Union (IoU)."""
        i_area = self.intersection_area(other)
        if i_area <= 0.0:
            return 0.0
        u_area = self.area + other.area - i_area
        return i_area / u_area if u_area > 0 else 0.0

    def clamp(self, container: "BoundingBox") -> "BoundingBox":
        """
        Kẹp hộp bao nằm trọn vẹn trong vùng an toàn container
        mà vẫn giữ nguyên kích thước (width, height) nếu có thể.
        """
        w = min(self.width, container.width)
        h = min(self.height, container.height)

        new_x_min = self.x_min
        new_x_max = self.x_max

        if new_x_max > container.x_max:
            new_x_max = container.x_max
            new_x_min = new_x_max - w
        if new_x_min < container.x_min:
            new_x_min = container.x_min
            new_x_max = new_x_min + w

        new_y_min = self.y_min
        new_y_max = self.y_max

        if new_y_max > container.y_max:
            new_y_max = container.y_max
            new_y_min = new_y_max - h
        if new_y_min < container.y_min:
            new_y_min = container.y_min
            new_y_max = new_y_min + h

        return BoundingBox(
            x_min=round(new_x_min, 4),
            y_min=round(new_y_min, 4),
            x_max=round(new_x_max, 4),
            y_max=round(new_y_max, 4)
        )


class SafeZonePreset:
    """Các thiết lập vùng hiển thị an toàn (Title & Action Safe Zones)."""

    PRESETS = {
        "tiktok_9:16": BoundingBox(x_min=0.06, y_min=0.12, x_max=0.84, y_max=0.82),
        "youtube_shorts_9:16": BoundingBox(x_min=0.06, y_min=0.10, x_max=0.84, y_max=0.82),
        "reels_9:16": BoundingBox(x_min=0.06, y_min=0.10, x_max=0.86, y_max=0.80),
        "standard_16:9": BoundingBox(x_min=0.10, y_min=0.10, x_max=0.90, y_max=0.90),
        "standard_9:16": BoundingBox(x_min=0.08, y_min=0.10, x_max=0.86, y_max=0.85),
    }

    @classmethod
    def get_safe_zone(cls, platform: str = "tiktok", aspect_ratio: str = "9:16") -> BoundingBox:
        key = f"{platform.lower()}_{aspect_ratio}"
        if key in cls.PRESETS:
            return cls.PRESETS[key]

        # Fallback theo aspect ratio
        if aspect_ratio == "16:9":
            return cls.PRESETS["standard_16:9"]
        return cls.PRESETS["standard_9:16"]


class ConflictType(str, Enum):
    SUBTITLE_FACE_COLLISION = "subtitle_face_collision"
    PIP_FACE_COLLISION = "pip_face_collision"
    SAFE_ZONE_VIOLATION = "safe_zone_violation"


class ConflictResolution(str, Enum):
    SHIFT_SUBTITLE_UP = "shift_subtitle_up"
    FLIP_PIP_CORNER = "flip_pip_corner"
    CLAMP_TO_SAFE_ZONE = "clamp_to_safe_zone"
    NO_CONFLICT = "no_conflict"


class ConflictReport(BaseModel):
    segment_index: int = Field(default=0)
    conflict_type: ConflictType
    resolution: ConflictResolution
    adjusted_box: Optional[BoundingBox] = None
    details: str = Field(default="")


class GeometryConflictChecker:
    """
    Bộ kiểm tra và giải quyết xung đột không gian/bố cục giữa các phần tử video.
    """
    def __init__(self, platform: str = "tiktok", aspect_ratio: str = "9:16"):
        self.platform = platform
        self.aspect_ratio = aspect_ratio
        self.safe_zone = SafeZonePreset.get_safe_zone(platform, aspect_ratio)

    def check_subtitle_face_collision(
        self,
        face_box: BoundingBox,
        subtitle_box: Optional[BoundingBox] = None
    ) -> Tuple[bool, BoundingBox]:
        """
        Kiểm tra xem phụ đề có đè lên mặt người nói không.
        Nếu có va chạm (hoặc khoảng cách an toàn < 0.04), nâng phụ đề lên cao hơn mặt
        hoặc đặt vào vùng an toàn trên ngực.
        """
        sub = subtitle_box or BoundingBox(x_min=0.15, y_min=0.80, x_max=0.85, y_max=0.92)

        # Kiểm tra giao cắt trực tiếp hoặc mặt tràn xuống gần phụ đề
        has_overlap = sub.intersects(face_box) or (
            face_box.y_max > sub.y_min - 0.04 and not (face_box.x_max < sub.x_min or face_box.x_min > sub.x_max)
        )

        if not has_overlap:
            return False, sub

        # Giải quyết xung đột: Nâng phụ đề lên trên mặt
        sub_h = sub.height
        # Đặt phía trên mặt với margin 0.04
        new_y_max = face_box.y_min - 0.04
        new_y_min = new_y_max - sub_h

        # Nếu nâng lên trên mặt mà lại vượt quá safe zone trên cùng, đặt ngang ngực/bụng
        if new_y_min < self.safe_zone.y_min:
            new_y_min = self.safe_zone.y_min
            new_y_max = new_y_min + sub_h

        adj_sub = BoundingBox(
            x_min=sub.x_min,
            y_min=round(new_y_min, 4),
            x_max=sub.x_max,
            y_max=round(new_y_max, 4)
        ).clamp(self.safe_zone)

        return True, adj_sub

    def resolve_pip_placement(
        self,
        face_box: BoundingBox,
        pip_scale: float = 0.35
    ) -> Tuple[float, float, float, str]:
        """
        Tính toán góc đặt PiP không bị người nói che khuất.
        Trả về (scale, pan_x, pan_y, corner_name).
        """
        pw, ph = pip_scale, pip_scale
        candidate_corners = {
            "bottom_right": {
                "box": BoundingBox(x_min=round(1.0 - pw - 0.05, 4), y_min=round(1.0 - ph - 0.05, 4), x_max=0.95, y_max=0.95),
                "pan_x": 0.28,
                "pan_y": -0.32,
                "preference": 1.0
            },
            "bottom_left": {
                "box": BoundingBox(x_min=0.05, y_min=round(1.0 - ph - 0.05, 4), x_max=round(0.05 + pw, 4), y_max=0.95),
                "pan_x": -0.28,
                "pan_y": -0.32,
                "preference": 0.95
            },
            "top_right": {
                "box": BoundingBox(x_min=round(1.0 - pw - 0.05, 4), y_min=0.05, x_max=0.95, y_max=round(0.05 + ph, 4)),
                "pan_x": 0.28,
                "pan_y": 0.32,
                "preference": 0.70
            },
            "top_left": {
                "box": BoundingBox(x_min=0.05, y_min=0.05, x_max=round(0.05 + pw, 4), y_max=round(0.05 + ph, 4)),
                "pan_x": -0.28,
                "pan_y": 0.32,
                "preference": 0.65
            }
        }

        best_corner = "bottom_right"
        best_score = -999999.0

        for name, data in candidate_corners.items():
            box = data["box"]
            overlap = box.intersection_area(face_box)
            dist_sq = (box.center_x - face_box.center_x) ** 2 + (box.center_y - face_box.center_y) ** 2

            score = (dist_sq * 10.0) + data["preference"]
            if overlap > 0.0:
                score -= (overlap * 1000.0)

            # Nếu mặt người nằm sát đáy (y > 0.60), phạt nặng các góc dưới nếu ở gần mặt
            if face_box.center_y > 0.60 and "bottom" in name:
                if box.intersects(face_box) or abs(box.center_x - face_box.center_x) < 0.30:
                    score -= 500.0

            if score > best_score:
                best_score = score
                best_corner = name

        chosen = candidate_corners[best_corner]
        return pip_scale, chosen["pan_x"], chosen["pan_y"], best_corner

    def audit_shotbook_plan(
        self,
        shotbook_plan: Any,
        face_detections: Optional[List[Dict[str, Any]]] = None,
        subtitles: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Any, List[ConflictReport]]:
        """
        Rà soát và giải quyết các xung đột hình học (PiP che mặt, phụ đề đè mặt)
        trên toàn bộ ShotbookPlan.
        """
        reports: List[ConflictReport] = []
        if not shotbook_plan or not hasattr(shotbook_plan, "items"):
            return shotbook_plan, reports

        detections = face_detections or []

        for item in shotbook_plan.items:
            # 1. Tìm bounding box khuôn mặt đại diện trong khoảng thời gian của item
            matching_faces: List[BoundingBox] = []
            item_st = float(getattr(item, "start_time", 0.0))
            item_et = float(getattr(item, "end_time", item_st + 2.0))

            for d in detections:
                t = float(d.get("time", d.get("start", 0.0)))
                if (item_st <= t <= item_et) or (item_st - 0.5 <= t <= item_et + 0.5):
                    raw_box = d.get("box") or d.get("face_box")
                    if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
                        matching_faces.append(BoundingBox(
                            x_min=float(raw_box[0]),
                            y_min=float(raw_box[1]),
                            x_max=float(raw_box[2]),
                            y_max=float(raw_box[3])
                        ))
                    elif isinstance(raw_box, BoundingBox):
                        matching_faces.append(raw_box)

            # Nếu không tìm thấy trong khoảng thời gian cụ thể nhưng có detections chung
            if not matching_faces and detections:
                raw_box = detections[0].get("box") or detections[0].get("face_box")
                if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
                    matching_faces.append(BoundingBox(
                        x_min=float(raw_box[0]),
                        y_min=float(raw_box[1]),
                        x_max=float(raw_box[2]),
                        y_max=float(raw_box[3])
                    ))
                elif isinstance(raw_box, BoundingBox):
                    matching_faces.append(raw_box)

            if not matching_faces:
                continue

            # Lấy trung bình vị trí mặt
            avg_x_min = sum(f.x_min for f in matching_faces) / len(matching_faces)
            avg_y_min = sum(f.y_min for f in matching_faces) / len(matching_faces)
            avg_x_max = sum(f.x_max for f in matching_faces) / len(matching_faces)
            avg_y_max = sum(f.y_max for f in matching_faces) / len(matching_faces)
            rep_face = BoundingBox(
                x_min=round(avg_x_min, 4),
                y_min=round(avg_y_min, 4),
                x_max=round(avg_x_max, 4),
                y_max=round(avg_y_max, 4)
            )

            # 2. Xử lý PiP Corner
            layout_val = getattr(item.layout_mode, "value", str(getattr(item, "layout_mode", ""))).lower()
            role_val = getattr(item.role, "value", str(getattr(item, "role", ""))).lower()
            is_pip = (
                "pip" in layout_val or
                "pip" in str(getattr(item, "layout_mode", "")).lower() or
                "evidence" in role_val or
                getattr(item, "evidence_path", None) is not None
            )

            if is_pip:
                scale = float(getattr(item, "zoom_scale", 0.35))
                _, opt_px, opt_py, corner = self.resolve_pip_placement(rep_face, pip_scale=scale)

                cur_px = float(getattr(item, "pan_x", 0.0))
                cur_py = float(getattr(item, "pan_y", 0.0))
                if abs(cur_px - opt_px) > 0.05 or abs(cur_py - opt_py) > 0.05:
                    item.pan_x = opt_px
                    item.pan_y = opt_py
                    reports.append(ConflictReport(
                        segment_index=getattr(item, "segment_index", 0),
                        conflict_type=ConflictType.PIP_FACE_COLLISION,
                        resolution=ConflictResolution.FLIP_PIP_CORNER,
                        details=f"Dời PiP sang góc {corner} (pan_x={opt_px}, pan_y={opt_py}) để tránh che mặt người nói"
                    ))

        return shotbook_plan, reports


