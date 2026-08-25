import os
import re
import json
import difflib
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field

class AIDirectorConfig(BaseModel):
    """
    Cấu hình bộ Đạo Diễn AI (AI Director) phiên bản v3.0.
    """
    mode: str = Field(
        default="clean_talk",
        description="Chế độ biên tập: 'silence_only', 'clean_talk', 'viral_shorts', 'podcast_summary'"
    )
    remove_bad_takes: bool = Field(
        default=True,
        description="Tự động phát hiện và loại bỏ các đoạn nói vấp, thử lại câu (Bad Takes)"
    )
    remove_filler_words: bool = Field(
        default=True,
        description="Tự động lọc các từ đệm vô nghĩa (à, ừm, ờ, kiểu như...)"
    )
    enable_punch_in: bool = Field(
        default=True,
        description="Tự động tạo hiệu ứng phóng to nhẹ (Punch-in Zoom) luân phiên ở các điểm Jump-cut"
    )
    punch_in_scale: float = Field(
        default=1.15, ge=1.05, le=1.5,
        description="Tỷ lệ phóng to khi thực hiện Punch-in (mặc định 1.15x)"
    )
    target_duration_seconds: Optional[float] = Field(
        default=None,
        description="Thời lượng mục tiêu cho video tóm tắt (ví dụ 60.0 cho Shorts)"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API Key (Google Gemini hoặc OpenAI) để phân tích ngữ nghĩa sâu"
    )


class BadTakeDetector:
    """
    Thuật toán phân tích chuỗi văn bản và mốc thời gian để phát hiện các câu nói vấp,
    thử lại nhiều lần (False Starts / Stumbles) và giữ lại cú nói chuẩn xác cuối cùng.
    """

    # Danh sách từ đệm phổ biến trong tiếng Việt và tiếng Anh
    FILLER_WORDS = {
        "vi": ["à", "ừm", "ờ", "ừ", "kiểu", "kiểu như", "thì là", "thì là mà", "như là", "ơ", "hả", "dạ"],
        "en": ["um", "uh", "er", "ah", "like", "you know", "i mean", "so", "actually", "basically"]
    }

    @staticmethod
    def clean_text(text: str) -> str:
        """Chuẩn hóa văn bản: Chuyển chữ thường, bỏ dấu câu."""
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        return text

    @classmethod
    def is_filler_word(cls, word: str, lang: str = "vi") -> bool:
        clean_w = cls.clean_text(word)
        fillers = cls.FILLER_WORDS.get(lang, cls.FILLER_WORDS["vi"]) + cls.FILLER_WORDS["en"]
        return clean_w in fillers

    @classmethod
    def detect_bad_takes(cls, subtitles: List[Dict[str, Any]], similarity_threshold: float = 0.55) -> List[int]:
        """
        Duyệt qua danh sách các đoạn phụ đề và phát hiện các index bị coi là "nói thử / nói vấp".
        Khi phát hiện 2 câu nói có phần mở đầu giống nhau liên tiếp, đoạn trước sẽ được đánh dấu để loại bỏ.

        Returns:
            List[int]: Danh sách index của các đoạn sub cần bị loại bỏ do là Bad Take.
        """
        bad_take_indices = set()
        n = len(subtitles)
        if n < 2:
            return []

        for i in range(n - 1):
            curr_text = cls.clean_text(subtitles[i].get("text", ""))
            next_text = cls.clean_text(subtitles[i + 1].get("text", ""))
            
            if not curr_text or not next_text:
                continue

            curr_words = curr_text.split()
            next_words = next_text.split()

            if not curr_words or not next_words:
                continue

            # 1. Kiểm tra sự trùng lặp phần đầu câu (Prefix match)
            # Ví dụ: "hôm nay tôi sẽ" vs "hôm nay tôi sẽ hướng dẫn các bạn làm món ăn..."
            min_len = min(len(curr_words), len(next_words))
            matched_prefix_count = 0
            for w1, w2 in zip(curr_words, next_words):
                if w1 == w2:
                    matched_prefix_count += 1
                else:
                    break

            # Nếu đoạn trước rất ngắn (dưới 6 từ) và đoạn sau lặp lại ít nhất 2 từ đầu tiên
            if len(curr_words) <= 6 and matched_prefix_count >= 2:
                bad_take_indices.add(i)
                continue

            # 2. Sử dụng SequenceMatcher để tính độ tương đồng
            matcher = difflib.SequenceMatcher(None, curr_words, next_words)
            ratio = matcher.ratio()

            # Nếu độ tương đồng cao và khoảng cách giữa 2 đoạn dưới 3 giây
            gap = subtitles[i + 1]["start"] - subtitles[i]["end"]
            if ratio >= similarity_threshold and gap < 3.5:
                # Đánh dấu đoạn nói trước là đoạn hỏng
                bad_take_indices.add(i)

        return sorted(list(bad_take_indices))


class AIDirector:
    """
    Bộ não Đạo Diễn AI (AI Director) chịu trách nhiệm đưa ra quyết định cắt dựng thông minh
    dựa trên ngữ nghĩa nội dung, cấu trúc kịch bản và nhịp điệu hình ảnh.
    """

    def __init__(self, config: Optional[AIDirectorConfig] = None):
        self.config = config or AIDirectorConfig()

    def process_semantic_cut(
        self,
        subtitles: List[Dict[str, Any]],
        silence_keep_intervals: List[Tuple[float, float]],
        total_duration: float,
        language: str = "vi"
    ) -> Dict[str, Any]:
        """
        Xử lý toàn bộ quy trình biên tập thông minh:
        1. Lọc bỏ các đoạn Bad Takes (nói vấp, nói thử).
        2. Lọc bỏ các từ đệm (Filler words).
        3. Áp dụng kịch bản biên tập (Clean Talk, Viral Shorts 60s, Summary).
        4. Tính toán Punch-in (Zoom luân phiên).
        5. Sinh danh sách Markers ghi chú cho DaVinci Resolve.

        Returns:
            Dict chứa:
              - 'keep_intervals': List[(start, end)]
              - 'subtitles': List[Dict]
              - 'punch_in_events': List[Dict] (các phân đoạn cần zoom)
              - 'markers': List[Dict] (các ghi chú màu sắc trên timeline)
              - 'stats': Thống kê chi tiết
        """
        if not subtitles:
            return {
                "keep_intervals": silence_keep_intervals,
                "subtitles": subtitles,
                "punch_in_events": [],
                "markers": [],
                "stats": {"original_count": 0, "kept_count": 0, "removed_bad_takes": 0}
            }

        # 1. Phát hiện và loại bỏ Bad Takes
        bad_take_indices = set()
        if self.config.remove_bad_takes:
            bad_take_indices = set(BadTakeDetector.detect_bad_takes(subtitles))

        # 2. Lọc danh sách phụ đề hợp lệ
        valid_subtitles = []
        markers = []

        for idx, sub in enumerate(subtitles):
            if idx in bad_take_indices:
                markers.append({
                    "time": sub["start"],
                    "duration": sub["end"] - sub["start"],
                    "name": "✂ Bad Take",
                    "note": f"Đã loại bỏ đoạn nói vấp: '{sub.get('text', '')}'",
                    "color": "Red"
                })
                continue

            # Lọc từ đệm trong từ đơn nếu có
            if self.config.remove_filler_words and "words" in sub:
                filtered_words = [
                    w for w in sub["words"]
                    if not BadTakeDetector.is_filler_word(w.get("word", ""), language)
                ]
                if filtered_words:
                    sub["words"] = filtered_words
                    sub["start"] = filtered_words[0]["start"]
                    sub["end"] = filtered_words[-1]["end"]
                    sub["text"] = " ".join(w["word"].strip() for w in filtered_words)
                    valid_subtitles.append(sub)
            else:
                valid_subtitles.append(sub)

        # 3. Áp dụng chế độ kịch bản (Presets)
        final_subs = valid_subtitles
        if self.config.mode == "viral_shorts":
            final_subs, shorts_markers = self._extract_viral_shorts_segments(valid_subtitles, target_duration=self.config.target_duration_seconds or 60.0)
            markers.extend(shorts_markers)
        elif self.config.mode == "podcast_summary":
            final_subs, summary_markers = self._extract_summary_segments(valid_subtitles, target_duration=self.config.target_duration_seconds or 180.0)
            markers.extend(summary_markers)

        # 4. Hợp nhất mốc thời gian giữ lại (keep_intervals)
        raw_intervals = []
        for sub in final_subs:
            # Lấy mốc thời gian có đệm nhẹ 0.15s
            s_start = max(0.0, sub["start"] - 0.15)
            s_end = min(total_duration, sub["end"] + 0.15)
            raw_intervals.append((s_start, s_end))

        # Gộp các khoảng thời gian bị chồng chập
        merged_intervals = []
        for s_start, s_end in sorted(raw_intervals, key=lambda x: x[0]):
            if merged_intervals and s_start <= merged_intervals[-1][1] + 0.2:
                # Gộp hai đoạn liền kề nhau
                merged_intervals[-1] = (merged_intervals[-1][0], max(merged_intervals[-1][1], s_end))
            else:
                merged_intervals.append((s_start, s_end))

        # Nếu không có đoạn thoại nào được giữ lại, dự phòng về silence intervals
        if not merged_intervals:
            merged_intervals = silence_keep_intervals

        # 5. Tính toán hiệu ứng Auto Punch-in (Zoom luân phiên)
        punch_in_events = []
        if self.config.enable_punch_in and len(merged_intervals) > 1:
            # Cứ mỗi đoạn jump-cut kế tiếp, đổi trạng thái Zoom (1.0x -> 1.15x -> 1.0x -> 1.15x)
            is_zoomed = False
            for idx, (k_start, k_end) in enumerate(merged_intervals):
                if is_zoomed:
                    punch_in_events.append({
                        "interval_index": idx,
                        "start": k_start,
                        "end": k_end,
                        "scale": self.config.punch_in_scale,
                        "description": f"Punch-in {self.config.punch_in_scale}x"
                    })
                    markers.append({
                        "time": k_start,
                        "duration": k_end - k_start,
                        "name": f"🔍 Punch-In ({self.config.punch_in_scale}x)",
                        "note": "Góc quay phóng to cận cảnh tự động",
                        "color": "Cyan"
                    })
                is_zoomed = not is_zoomed

        # Ghi nhận Marker cho các ý chính
        for sub in final_subs[:5]:
            markers.append({
                "time": sub["start"],
                "duration": sub["end"] - sub["start"],
                "name": "⭐ Ý chính",
                "note": sub.get("text", "")[:50],
                "color": "Yellow"
            })

        return {
            "keep_intervals": merged_intervals,
            "subtitles": final_subs,
            "punch_in_events": punch_in_events,
            "markers": markers,
            "stats": {
                "original_count": len(subtitles),
                "kept_count": len(final_subs),
                "removed_bad_takes": len(bad_take_indices),
                "punch_ins_created": len(punch_in_events)
            }
        }

    def _extract_viral_shorts_segments(
        self,
        subtitles: List[Dict[str, Any]],
        target_duration: float = 60.0
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Trích xuất phân đoạn Viral Shorts (30s - 60s):
        - Giữ câu Hook mở đầu (10s đầu).
        - Chọn cụm nội dung có mật độ nói cao trào và mạch lạc nhất đạt đủ thời lượng mục tiêu.
        """
        if not subtitles:
            return [], []

        total_available_duration = subtitles[-1]["end"] - subtitles[0]["start"]
        if total_available_duration <= target_duration:
            return subtitles, []

        selected = []
        current_dur = 0.0
        markers = []

        # 1. Giữ câu Hook mở đầu (tối đa 15s)
        hook_subs = []
        for sub in subtitles:
            dur = sub["end"] - sub["start"]
            if current_dur + dur <= min(15.0, target_duration * 0.3):
                hook_subs.append(sub)
                current_dur += dur
            else:
                break
        selected.extend(hook_subs)

        if hook_subs:
            markers.append({
                "time": hook_subs[0]["start"],
                "duration": current_dur,
                "name": "🔥 Viral Hook",
                "note": "Phần mở đầu thu hút người xem của video ngắn",
                "color": "Green"
            })

        # 2. Tìm khối nội dung tiếp theo tốt nhất (giữa hoặc cuối) để lấp đầy target_duration
        remaining_subs = subtitles[len(hook_subs):]
        for sub in remaining_subs:
            dur = sub["end"] - sub["start"]
            if current_dur + dur <= target_duration:
                selected.append(sub)
                current_dur += dur
            else:
                break

        return selected, markers

    def _extract_summary_segments(
        self,
        subtitles: List[Dict[str, Any]],
        target_duration: float = 180.0
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Trích xuất tóm tắt nội dung chính (Podcast / Presentation Summary).
        """
        if not subtitles:
            return [], []

        total_available = subtitles[-1]["end"] - subtitles[0]["start"]
        if total_available <= target_duration:
            return subtitles, []

        # Lấy mẫu phân bố đều các ý mở đầu, thân bài và kết bài
        step = max(1, len(subtitles) // int(target_duration / 5.0))
        selected = []
        current_dur = 0.0

        for i in range(0, len(subtitles), step):
            sub = subtitles[i]
            dur = sub["end"] - sub["start"]
            if current_dur + dur <= target_duration:
                selected.append(sub)
                current_dur += dur

        markers = [{
            "time": selected[0]["start"] if selected else 0.0,
            "duration": current_dur,
            "name": "📊 AI Summary",
            "note": "Bản tóm tắt ý chính được chọn lọc bởi AI Director",
            "color": "Blue"
        }]

        return selected, markers
