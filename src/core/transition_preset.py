import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple
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


class TransitionMacroGenerator:
    """
    Trình khởi tạo file mẫu Fusion Macro (.setting) cho các hiệu ứng chuyển cảnh.
    Tương thích 100% với DaVinci Resolve Free và Studio thông qua Drag & Drop vào timeline.
    """

    @staticmethod
    def generate_setting_content(preset: TransitionStylePreset) -> str:
        """Sinh chuỗi cú pháp Lua Fusion Macro Transition tương thích DaVinci Resolve."""
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '', f"ResolveFlow_{preset.id.title()}")

        last_tool = "Dissolve1"
        extra_tools = ""

        if preset.transition_type == "transform":
            last_tool = "Transform1"
            extra_tools = """\t\t\t\tTransform1 = Transform {
\t\t\t\t\tInputs = {
\t\t\t\t\t\tMotionBlur = Input { Value = 1, },
\t\t\t\t\t\tQuality = Input { Value = 4, },
\t\t\t\t\t\tShutterAngle = Input { Value = 180, },
\t\t\t\t\t\tInput = Input {
\t\t\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\t\t\tSource = "Output",
\t\t\t\t\t\t},
\t\t\t\t\t},
\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 330, 36.3 } },
\t\t\t\t},
"""
        elif preset.transition_type == "zoom":
            last_tool = "Blur1"
            extra_tools = """\t\t\t\tBlur1 = DirectionalBlur {
\t\t\t\t\tInputs = {
\t\t\t\t\t\tType = Input { Value = 1, },
\t\t\t\t\t\tLength = Input { Value = 0.05, },
\t\t\t\t\t\tInput = Input {
\t\t\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\t\t\tSource = "Output",
\t\t\t\t\t\t},
\t\t\t\t\t},
\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 330, 36.3 } },
\t\t\t\t},
"""
        elif preset.transition_type == "glitch":
            last_tool = "Displace1"
            extra_tools = """\t\t\t\tDisplace1 = Displace {
\t\t\t\t\tInputs = {
\t\t\t\t\t\tType = Input { Value = 1, },
\t\t\t\t\t\tXOffset = Input { Value = 0.02, },
\t\t\t\t\t\tInput = Input {
\t\t\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\t\t\tSource = "Output",
\t\t\t\t\t\t},
\t\t\t\t\t},
\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 330, 36.3 } },
\t\t\t\t},
"""
        elif preset.transition_type == "glow":
            last_tool = "Glow1"
            extra_tools = """\t\t\t\tGlow1 = Glo {
\t\t\t\t\tInputs = {
\t\t\t\t\t\tXGlowSize = Input { Value = 15, },
\t\t\t\t\t\tInput = Input {
\t\t\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\t\t\tSource = "Output",
\t\t\t\t\t\t},
\t\t\t\t\t},
\t\t\t\t\tViewInfo = OperatorInfo { Pos = { 330, 36.3 } },
\t\t\t\t},
"""

        setting_str = f"""{{
\tTools = ordered() {{
\t\t{clean_name} = MacroOperator {{
\t\t\tInputs = ordered() {{
\t\t\t\tInput1 = InstanceInput {{
\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\tSource = "Background",
\t\t\t\t\tName = "From",
\t\t\t\t}},
\t\t\t\tInput2 = InstanceInput {{
\t\t\t\t\tSourceOp = "Dissolve1",
\t\t\t\t\tSource = "Foreground",
\t\t\t\t\tName = "To",
\t\t\t\t}},
\t\t\t\tTransition = InstanceInput {{
\t\t\t\t\tSourceOp = "AnimCurves1",
\t\t\t\t\tSource = "Transition",
\t\t\t\t}},
\t\t\t}},
\t\t\tOutputs = {{
\t\t\t\tMainOutput1 = InstanceOutput {{
\t\t\t\t\tSourceOp = "{last_tool}",
\t\t\t\t\tSource = "Output",
\t\t\t\t}}
\t\t\t}},
\t\t\tViewInfo = GroupInfo {{ Pos = {{ 0, 0 }} }},
\t\t\tTools = ordered() {{
\t\t\t\tDissolve1 = Dissolve {{
\t\t\t\t\tTransitions = {{
\t\t\t\t\t\t[0] = "DFVisualTransition",
\t\t\t\t\t}},
\t\t\t\t\tInputs = {{
\t\t\t\t\t\tMix = Input {{
\t\t\t\t\t\t\tSourceOp = "AnimCurves1",
\t\t\t\t\t\t\tSource = "Value",
\t\t\t\t\t\t}},
\t\t\t\t\t}},
\t\t\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 220, 36.3 }} }},
\t\t\t\t}},
\t\t\t\tAnimCurves1 = AnimCurves {{
\t\t\t\t\tInputs = {{
\t\t\t\t\t\tCurve = Input {{ Value = FuID {{ "EaseInOut" }}, }},
\t\t\t\t\t}},
\t\t\t\t}},
{extra_tools}\t\t\t}},
\t\t}}
\t}}
}}"""
        return setting_str

    @staticmethod
    def export_setting_file(preset: TransitionStylePreset, output_path: str) -> str:
        """Xuất preset chuyển cảnh ra file .setting."""
        content = TransitionMacroGenerator.generate_setting_content(preset)
        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    @staticmethod
    def get_davinci_resolve_transitions_dir() -> str:
        """Lấy đường dẫn thư mục Fusion Transitions Template của DaVinci Resolve trên Windows."""
        app_data = os.environ.get("APPDATA", "")
        if app_data:
            return os.path.join(app_data, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Templates", "Edit", "Transitions", "ResolveFlow")
        home = os.path.expanduser("~")
        mac_path = os.path.join(home, "Library", "Application Support", "Blackmagic Design", "DaVinci Resolve", "Fusion", "Templates", "Edit", "Transitions", "ResolveFlow")
        if os.path.exists(os.path.dirname(mac_path)):
            return mac_path
        return os.path.join(home, "ResolveFlow_Transitions")

    @classmethod
    def install_transitions_to_davinci_resolve(
        cls,
        presets: Optional[List[TransitionStylePreset]] = None,
        target_dir: Optional[str] = None
    ) -> Tuple[int, str]:
        """Tự động cài đặt danh sách Presets chuyển cảnh vào thư mục Effects Library của DaVinci Resolve."""
        if presets is None:
            presets = BUILTIN_TRANSITIONS

        if not target_dir:
            target_dir = cls.get_davinci_resolve_transitions_dir()

        os.makedirs(target_dir, exist_ok=True)

        installed_count = 0
        for p in presets:
            clean_name = re.sub(r'[^\w\s-]', '', p.name).strip().replace(' ', '_')
            filename = f"ResolveFlow_{clean_name}.setting"
            dest_path = os.path.join(target_dir, filename)
            cls.export_setting_file(p, dest_path)
            installed_count += 1

        return installed_count, target_dir

