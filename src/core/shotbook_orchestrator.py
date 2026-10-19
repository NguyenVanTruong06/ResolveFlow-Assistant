"""
Bộ điều phối khung hình động theo kịch bản (Dynamic SHOTBOOK Orchestrator).
Phân vai ngữ nghĩa câu thoại (Hook, Evidence, Punchline, Normal) và tự động tính toán
layout phóng to (Punch-in 1.18x) hoặc thu nhỏ góc (PiP 0.35x) cho DaVinci Resolve.
"""

from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from src.core.evidence_grabber import EvidenceTarget


class ShotRole(str, Enum):
    HOOK = "hook"               # Câu mở đầu giật gân, câu hỏi kích thích
    PUNCHLINE = "punchline"     # Đoạn cao trào, con số bất ngờ, nhấn mạnh
    EVIDENCE = "evidence"       # Đoạn đưa bằng chứng website, tài liệu, B-Roll
    OPINION = "opinion"         # Bày tỏ quan điểm cá nhân
    SUMMARY = "summary"         # Tóm tắt, kêu gọi hành động (CTA)
    NORMAL = "normal"           # Kể chuyện / thuyết trình thông thường


class LayoutMode(str, Enum):
    FULL_CENTER = "full_center" # Khung hình người nói trung tâm chuẩn (Scale 1.0)
    PUNCH_IN = "punch_in"       # Phóng to nhẹ trung tâm nhấn mạnh (Scale 1.15 - 1.25)
    PIP_CORNER = "pip_corner"   # Thu nhỏ người nói xuống góc làm PiP (Scale ~0.35)


class ShotbookItem(BaseModel):
    """Mỗi phân cảnh trong bảng điều phối SHOTBOOK."""
    segment_index: int = Field(default=0)
    start_time: float = Field(..., description="Thời gian bắt đầu (giây)")
    end_time: float = Field(..., description="Thời gian kết thúc (giây)")
    text: str = Field(default="", description="Nội dung câu thoại")
    role: ShotRole = Field(default=ShotRole.NORMAL, description="Vai trò kịch bản")
    layout_mode: LayoutMode = Field(default=LayoutMode.FULL_CENTER, description="Chế độ khung hình")
    zoom_scale: float = Field(default=1.0, description="Hệ số zoom phóng to/thu nhỏ")
    pan_x: float = Field(default=0.0, description="Độ dời trục ngang (-1.0 đến +1.0)")
    pan_y: float = Field(default=0.0, description="Độ dời trục dọc (-1.0 đến +1.0)")
    evidence_path: Optional[str] = Field(default=None, description="Đường dẫn ảnh/clip B-Roll bằng chứng nếu có")
    sfx_tag: Optional[str] = Field(default=None, description="Hiệu ứng âm thanh gợi ý")


class ShotbookPlan(BaseModel):
    """Toàn bộ kế hoạch điều phối khung hình theo kịch bản của video."""
    items: List[ShotbookItem] = Field(default_factory=list)


class ShotbookOrchestrator:
    """Bộ não phân tích và kiến tạo kế hoạch SHOTBOOK."""

    HOOK_CUES = [
        "làm thế nào", "tại sao", "bí mật", "hướng dẫn", "đừng", "bất ngờ",
        "1 triệu", "triệu đô", "cách tốt nhất", "how to", "why", "secret",
        "stop doing", "chưa từng thấy", "cực đỉnh", "đây là lý do", "bạn có biết"
    ]

    PUNCHLINE_CUES = [
        "kinh ngạc", "kỷ lục", "sai lầm", "shock", "wow", "boom",
        "cực sốc", "không thể tin", "bùng nổ", "nguy hiểm", "chính xác"
    ]

    def __init__(self, punch_in_scale: float = 1.18, pip_scale: float = 0.35):
        self.punch_in_scale = punch_in_scale
        self.pip_scale = pip_scale

    def calculate_pip_transform(self, face_side: str = "left") -> Tuple[float, float, float]:
        """
        Tính toán (scale, pan_x, pan_y) cho chế độ PiP ở góc dưới màn hình.
        Nếu mặt người nói ở bên trái -> PiP dời sang góc dưới phải (pan_x > 0).
        Nếu mặt người nói ở bên phải -> PiP dời sang góc dưới trái (pan_x < 0).
        """
        pan_x = 0.28 if face_side == "left" else -0.28
        pan_y = -0.32 # Luôn ở góc dưới
        return self.pip_scale, pan_x, pan_y

    def create_shotbook_plan(
        self,
        segments: List[Dict[str, Any]],
        evidence_targets: Optional[List[EvidenceTarget]] = None,
        aspect_ratio: str = "9:16"
    ) -> ShotbookPlan:
        """
        Phân tích danh sách các đoạn thoại (từ Whisper) và danh sách bằng chứng web
        để tạo bảng phân cảnh thi công SHOTBOOK hoàn chỉnh.
        """
        items: List[ShotbookItem] = []
        evidence_list = evidence_targets or []

        for idx, seg in enumerate(segments):
            st = float(seg.get("start", 0.0))
            et = float(seg.get("end", st + 2.0))
            txt = str(seg.get("text", "")).strip()
            txt_lower = txt.lower()

            # 1. Kiểm tra bằng chứng web (Evidence)
            matching_evidence = None
            for ev in evidence_list:
                # Kiểm tra giao thoa thời gian hoặc từ khóa xuất hiện trong câu
                if (ev.start_time <= et and ev.end_time >= st) or (ev.keyword.lower() in txt_lower):
                    matching_evidence = ev
                    break

            if matching_evidence:
                scale, px, py = self.calculate_pip_transform(face_side="left")
                items.append(ShotbookItem(
                    segment_index=idx,
                    start_time=st,
                    end_time=et,
                    text=txt,
                    role=ShotRole.EVIDENCE,
                    layout_mode=LayoutMode.PIP_CORNER,
                    zoom_scale=scale,
                    pan_x=px,
                    pan_y=py,
                    evidence_path=matching_evidence.evidence_path,
                    sfx_tag="pop_bubble_clean.wav"
                ))
                continue

            # 2. Kiểm tra Hook (ở 4 giây đầu HOẶC có câu hỏi / từ khóa hook)
            is_hook = False
            if st < 4.0:
                if txt.endswith("?") or any(cue in txt_lower for cue in self.HOOK_CUES):
                    is_hook = True
            elif any(cue in txt_lower for cue in self.HOOK_CUES):
                is_hook = True

            if is_hook:
                items.append(ShotbookItem(
                    segment_index=idx,
                    start_time=st,
                    end_time=et,
                    text=txt,
                    role=ShotRole.HOOK,
                    layout_mode=LayoutMode.PUNCH_IN,
                    zoom_scale=self.punch_in_scale,
                    pan_x=0.0,
                    pan_y=0.0,
                    sfx_tag="whoosh_fast_short.wav"
                ))
                continue

            # 3. Kiểm tra Punchline / Cao trào
            if any(cue in txt_lower for cue in self.PUNCHLINE_CUES):
                items.append(ShotbookItem(
                    segment_index=idx,
                    start_time=st,
                    end_time=et,
                    text=txt,
                    role=ShotRole.PUNCHLINE,
                    layout_mode=LayoutMode.PUNCH_IN,
                    zoom_scale=self.punch_in_scale,
                    pan_x=0.0,
                    pan_y=0.0,
                    sfx_tag="vine_boom_sub_drop.wav"
                ))
                continue

            # 4. Kể chuyện thông thường
            items.append(ShotbookItem(
                segment_index=idx,
                start_time=st,
                end_time=et,
                text=txt,
                role=ShotRole.NORMAL,
                layout_mode=LayoutMode.FULL_CENTER,
                zoom_scale=1.0,
                pan_x=0.0,
                pan_y=0.0,
                sfx_tag=None
            ))

        return ShotbookPlan(items=items)

    def audit_with_geometry_checker(
        self,
        plan: ShotbookPlan,
        face_detections: Optional[List[Dict[str, Any]]] = None,
        platform: str = "tiktok",
        aspect_ratio: str = "9:16"
    ) -> Tuple[ShotbookPlan, List[Any]]:
        """Rà soát và giải quyết xung đột bố cục (PiP che mặt, phụ đề) trên kịch bản."""
        from src.core.geometry_checker import GeometryConflictChecker
        checker = GeometryConflictChecker(platform=platform, aspect_ratio=aspect_ratio)
        return checker.audit_shotbook_plan(plan, face_detections=face_detections)

