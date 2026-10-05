import os
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


def pydantic_dump(model: BaseModel) -> Dict[str, Any]:
    """Hỗ trợ serialize Pydantic model trên cả v1 (Python 3.10) và v2 (Python 3.11+)."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    elif hasattr(model, "dict"):
        return model.dict()
    return vars(model)


class TransitionStylePreset(BaseModel):
    """
    Schema định nghĩa Preset chuyển cảnh DaVinci Resolve Fusion (.setting macro).
    Tương thích 100% với DaVinci Resolve (kể cả bản Free qua Drag & Drop).
    """
    id: str = Field(..., description="ID định danh duy nhất của preset")
    name: str = Field(..., description="Tên hiển thị người dùng")
    category: str = Field(default="motion", description="Danh mục phân loại kỹ xảo chuyển cảnh")
    badge_icon: str = Field(default="💨", description="Biểu tượng Emoji đại diện")
    transition_type: str = Field(default="transform", description="Loại kỹ thuật chuyển cảnh Fusion (transform, zoom, glitch, glow...)")
    tags: List[str] = Field(default_factory=list, description="Danh sách từ khóa tìm kiếm & phong cách")
    description: str = Field(default="", description="Mô tả kỹ thuật hoạt ảnh và phong cách chuyển cảnh")
    duration_frames: int = Field(default=24, description="Thời lượng mặc định của chuyển cảnh (số frame)")
    custom_parameters: Dict[str, Any] = Field(default_factory=dict, description="Tham số tùy chỉnh bổ sung cho macro")


BUILTIN_TRANSITIONS: List[TransitionStylePreset] = [
    TransitionStylePreset(
        id="whip_pan_left",
        name="Whip Pan Left",
        category="motion",
        badge_icon="💨",
        transition_type="transform",
        tags=["WhipPan", "Fast", "Motion", "Dynamic", "Action"],
        description="Chuyển cảnh lướt nhanh sang trái kết hợp nhòe chuyển động (Motion Blur) mạnh mẽ.",
        duration_frames=24,
    ),
    TransitionStylePreset(
        id="zoom_blur_in",
        name="Zoom Blur In",
        category="zoom",
        badge_icon="🔍",
        transition_type="zoom",
        tags=["Zoom", "Blur", "Impact", "Energy", "Fast"],
        description="Hiệu ứng phóng to quang học cực nhanh tạo điểm nhấn chuyển tiếp tràn đầy năng lượng.",
        duration_frames=20,
    ),
    TransitionStylePreset(
        id="rgb_glitch",
        name="RGB Glitch",
        category="glitch",
        badge_icon="⚡",
        transition_type="glitch",
        tags=["Glitch", "RGB", "Cyber", "Distortion", "Retro"],
        description="Nhiễu kênh màu RGB phân tách điện tử phong cách Cyberpunk và công nghệ số.",
        duration_frames=16,
    ),
    TransitionStylePreset(
        id="light_leak",
        name="Light Leak",
        category="film",
        badge_icon="✨",
        transition_type="glow",
        tags=["LightLeak", "Warm", "Cinematic", "Film", "Soft"],
        description="Vệt sáng lóa ống kính ấm áp tự nhiên kết nối 2 khung hình mượt mà và nghệ thuật.",
        duration_frames=30,
    ),
]
