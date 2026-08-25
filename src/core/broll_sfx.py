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
