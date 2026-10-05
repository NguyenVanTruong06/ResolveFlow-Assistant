import os
import re
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field

class BRollCue(BaseModel):
    """
    Thông tin phân đoạn chèn cảnh minh họa B-Roll trên Video Track 2.
    """
    start: float
    end: float
    duration: float
    keyword: str
    search_prompt: str
    track_index: int = 2


class SFXCue(BaseModel):
    """
    Thông tin hiệu ứng âm thanh SFX trên Audio Track 2.
    """
    time: float
    sfx_type: str = "whoosh"  # 'whoosh', 'pop', 'ding', 'click'
    duration: float = 0.5
    track_index: int = 2
    note: str = ""


class BRollAnalyzer:
    """
    Bộ phân tích ngữ nghĩa hình ảnh: Tự động trích xuất các từ khóa thị giác đắt giá
    và sinh các mốc đặt cảnh minh họa (B-Roll) trên Video Track 2.
    """

    # Danh sách các danh từ / chủ đề thị giác phổ biến
    VISUAL_KEYWORDS_VI = [
        "công nghệ", "ai", "trí tuệ nhân tạo", "tiền", "doanh thu", "lợi nhuận",
        "thị trường", "bất động sản", "máy tính", "điện thoại", "camera", "máy ảnh",
        "đồ thị", "tăng trưởng", "thành công", "khách hàng", "du lịch", "ẩm thực",
        "xe hơi", "nhà cửa", "thiết kế", "phần mềm", "lập trình", "dữ liệu"
    ]
    VISUAL_KEYWORDS_EN = [
        "technology", "ai", "artificial intelligence", "money", "revenue", "profit",
        "market", "real estate", "computer", "phone", "camera", "chart", "growth",
        "success", "customer", "travel", "food", "car", "house", "design", "coding"
    ]

    @classmethod
    def extract_broll_cues(
        cls,
        subtitles: List[Dict[str, Any]],
        min_interval_gap: float = 8.0,
        broll_duration: float = 3.5
    ) -> List[BRollCue]:
        """
        Quét qua danh sách phụ đề và trích xuất các điểm nên chèn B-Roll minh họa.

        Args:
            subtitles (List[Dict]): Danh sách phụ đề đã cắt gọt.
            min_interval_gap (float): Khoảng cách tối thiểu giữa 2 cảnh B-roll (tránh chèn quá dày).
            broll_duration (float): Thời lượng hiển thị mặc định của mỗi cảnh B-roll.

        Returns:
            List[BRollCue]: Danh sách các điểm chèn B-Roll.
        """
        cues = []
        last_broll_time = -min_interval_gap
        all_keywords = cls.VISUAL_KEYWORDS_VI + cls.VISUAL_KEYWORDS_EN

        for sub in subtitles:
            text = sub.get("text", "").lower()
            start_time = sub.get("start", 0.0)

            # Đảm bảo khoảng cách thời gian giữa 2 B-roll
            if start_time - last_broll_time < min_interval_gap:
                continue

            for kw in all_keywords:
                # Kiểm tra từ khóa xuất hiện nguyên vẹn trong câu
                if re.search(r'\b' + re.escape(kw) + r'\b', text):
                    b_start = start_time
                    b_end = b_start + broll_duration
                    search_query = f"B-roll cinematic 4k {kw}"

                    cues.append(BRollCue(
                        start=round(b_start, 3),
                        end=round(b_end, 3),
                        duration=round(broll_duration, 2),
                        keyword=kw,
                        search_prompt=search_query
                    ))
                    last_broll_time = b_end
                    break

        return cues


class SFXEngine:
    """
    Bộ động cơ âm thanh hiệu ứng (SFX Engine): Tự động phân bổ hiệu ứng âm thanh
    (Whoosh ở điểm chuyển cảnh/Punch-in, Pop/Ding ở điểm nhấn) trên Audio Track 2.
    """

    @staticmethod
    def generate_sfx_cues(
        keep_intervals: List[Tuple[float, float]],
        punch_in_events: Optional[List[Dict[str, Any]]] = None,
        broll_cues: Optional[List[BRollCue]] = None
    ) -> List[SFXCue]:
        """
        Sinh danh sách các điểm chèn hiệu ứng âm thanh trên Audio Track 2.

        Returns:
            List[SFXCue]: Mảng các sự kiện âm thanh.
        """
        sfx_list = []

        # 1. Chèn tiếng Whoosh nhẹ tại mỗi điểm Jump-cut / Punch-in
        if punch_in_events:
            for pe in punch_in_events:
                p_time = pe.get("start", 0.0)
                sfx_list.append(SFXCue(
                    time=round(p_time, 3),
                    sfx_type="whoosh",
                    duration=0.4,
                    note=f"Chuyển cảnh Punch-in {pe.get('scale', 1.15)}x"
                ))

        # 2. Chèn tiếng Pop / Ding tại điểm bắt đầu xuất hiện B-Roll
        if broll_cues:
            for bc in broll_cues:
                sfx_list.append(SFXCue(
                    time=bc.start,
                    sfx_type="pop",
                    duration=0.3,
                    note=f"Xuất hiện B-Roll '{bc.keyword}'"
                ))

        # Sắp xếp theo thứ tự thời gian
        return sorted(sfx_list, key=lambda x: x.time)


class GlobalAssetPool:
    """
    Kho Tài Nguyên Toàn Cục (Global Shared Asset Pool):
    - Tự động quét và lập chỉ mục vĩnh viễn toàn bộ Memes, Green Screen, SFX dùng chung.
    - Cung cấp API tra cứu tức thì (<0.001s) cho DaVinci Resolve Timeline Generator.
    - Hỗ trợ quét bổ sung (incremental refresh) và nạp thêm Kho B-Roll/SFX riêng của dự án.
    """
    _instance: Optional["GlobalAssetPool"] = None
    
    def __init__(self, base_asset_dir: Optional[str] = None):
        if base_asset_dir is None:
            self.base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
        else:
            self.base_dir = os.path.abspath(base_asset_dir)
            
        self.memes_index: Dict[str, str] = {}         # basename_lower -> abs_path (Global)
        self.sfx_index: Dict[str, str] = {}           # basename_lower -> abs_path (Global)
        self.project_memes_index: Dict[str, str] = {} # basename_lower -> abs_path (Project)
        self.project_sfx_index: Dict[str, str] = {}   # basename_lower -> abs_path (Project)
        self.refresh()

    @classmethod
    def get_instance(cls, base_asset_dir: Optional[str] = None) -> "GlobalAssetPool":
        if cls._instance is None:
            cls._instance = GlobalAssetPool(base_asset_dir)
        elif base_asset_dir and os.path.abspath(base_asset_dir) != cls._instance.base_dir:
            cls._instance.base_dir = os.path.abspath(base_asset_dir)
            cls._instance.refresh()
        return cls._instance

    def register_project_assets(self, project_dir: str) -> Tuple[int, int]:
        """
        Nạp thêm kho tài nguyên (B-Roll, Canh_Chen, SFX, Âm thanh) riêng của từng dự án.
        Ưu tiên sử dụng tài nguyên của dự án trước khi fallback về kho chung.
        """
        self.project_memes_index.clear()
        self.project_sfx_index.clear()
        if not project_dir or not os.path.exists(project_dir):
            return 0, 0

        for root, dirs, files in os.walk(project_dir):
            # Bỏ qua các thư mục timeline import và file tạm
            dirs[:] = [d for d in dirs if not d.startswith((".", "_timeline_import"))]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                full_path = os.path.join(root, f)
                if ext in ('.mp4', '.mov', '.webm', '.mkv'):
                    self.project_memes_index[f.lower()] = full_path
                elif ext in ('.wav', '.mp3', '.m4a', '.aac', '.flac'):
                    self.project_sfx_index[f.lower()] = full_path

        return len(self.project_memes_index), len(self.project_sfx_index)

    def refresh(self) -> Tuple[int, int]:
        """Quét và cập nhật toàn bộ chỉ mục tài nguyên toàn cục (Memes & SFX)."""
        self.memes_index.clear()
        self.sfx_index.clear()
        
        if not os.path.exists(self.base_dir):
            return 0, 0

        # Quét B-Roll Memes (toàn bộ thư mục con: green_screen, memes, cinematic,...)
        broll_dir = os.path.join(self.base_dir, "broll_memes")
        if os.path.exists(broll_dir):
            for root, _, files in os.walk(broll_dir):
                for f in files:
                    if f.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')):
                        self.memes_index[f.lower()] = os.path.join(root, f)

        # Quét SFX
        sfx_dir = os.path.join(self.base_dir, "sfx")
        if os.path.exists(sfx_dir):
            for root, _, files in os.walk(sfx_dir):
                for f in files:
                    if f.lower().endswith(('.wav', '.mp3', '.m4a', '.aac', '.flac')):
                        self.sfx_index[f.lower()] = os.path.join(root, f)

        return len(self.memes_index), len(self.sfx_index)

    def resolve_meme(self, query: str) -> Optional[str]:
        """Tìm đường dẫn tuyệt đối của video meme/b-roll theo tên file hoặc từ khóa."""
        if not query:
            return None
        if os.path.isabs(query) and os.path.exists(query):
            return query

        q = os.path.basename(query).lower()
        q_stem = os.path.splitext(q)[0]

        # 1. Tra cứu trong kho Dự án (Project-specific) trước
        if q in self.project_memes_index:
            return self.project_memes_index[q]
        for name, path in self.project_memes_index.items():
            if q_stem in name or name.startswith(q_stem):
                return path

        # 2. Tra cứu trong kho Toàn cục (Global Assets)
        if q in self.memes_index:
            return self.memes_index[q]
        for name, path in self.memes_index.items():
            if q_stem in name or name.startswith(q_stem) or q in name:
                return path

        # 3. Tra cứu từ khóa từng phần
        words = [w for w in re.split(r'[\s_\-]+', q_stem) if len(w) > 2]
        for name, path in self.memes_index.items():
            if any(w in name for w in words):
                return path

        return None

    def resolve_broll(self, query: str) -> Optional[str]:
        """Alias cho resolve_meme hỗ trợ cả B-roll và Meme."""
        return self.resolve_meme(query)

    def resolve_sfx(self, query: str) -> Optional[str]:
        """Tìm đường dẫn tuyệt đối của âm thanh SFX theo tên file hoặc từ khóa."""
        if not query:
            return None
        if os.path.isabs(query) and os.path.exists(query):
            return query

        q = os.path.basename(query).lower()
        q_stem = os.path.splitext(q)[0]

        # 1. Tra cứu trong kho Dự án trước
        if q in self.project_sfx_index:
            return self.project_sfx_index[q]
        for name, path in self.project_sfx_index.items():
            if q_stem in name or name.startswith(q_stem):
                return path

        # 2. Tra cứu trong kho Toàn cục
        if q in self.sfx_index:
            return self.sfx_index[q]
        for name, path in self.sfx_index.items():
            if q_stem in name or name.startswith(q_stem) or q in name:
                return path

        # 3. Tra cứu từ khóa từng phần
        words = [w for w in re.split(r'[\s_\-]+', q_stem) if len(w) > 2]
        for name, path in self.sfx_index.items():
            if any(w in name for w in words):
                return path

        return None

