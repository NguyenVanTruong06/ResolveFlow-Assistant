"""
Bộ máy gợi ý Asset thông minh theo ngữ cảnh lời thoại (Contextual Asset Suggester).
Phân tích cảm xúc, từ khóa kịch bản (CapCut/Shotbook AI style) và đề xuất
hiệu ứng âm thanh (SFX), Meme, Mẫu chữ Text+ và Hiệu ứng chuyển cảnh phù hợp nhất.
"""

import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class SuggestedAsset:
    """Đối tượng đề xuất kèm lý do và độ khớp."""
    asset: Any
    reason: str
    confidence: float
    category_type: str


class ContextualSuggester:
    """Bộ phân tích cảm xúc lời thoại và đề xuất đạo cụ hậu kỳ thông minh."""

    EMOTION_RULES = {
        "hai_huoc": {
            "label": "😂 Hài hước / Châm biếm",
            "keywords": [
                "hài", "buồn cười", "troll", "cười", "haha", "ngớ ngẩn", "vãi",
                "ối dồi ôi", "ảo thật", "đùa", "lầy", "toang", "ngáo", "ngu ngốc",
                "vô lý", "trêu", "chọc", "funny", "joke", "meme", "lmao", "lol"
            ],
            "target_tags": [
                "hai_huoc", "vietnam_viral", "laugh", "funny", "boing", "bonk",
                "comic", "meme", "clown", "fart", "troll"
            ],
        },
        "giat_minh": {
            "label": "💥 Giật mình / Cao trào",
            "keywords": [
                "sốc", "bất ngờ", "kinh ngạc", "không thể tin", "boom", "kinh khủng",
                "nguy hiểm", "bùng nổ", "kinh hoàng", "sai lầm", "chết tiệt", "cực sốc",
                "đột nhiên", "chấn động", "giật mình", "hoảng hốt", "cảnh báo", "shock",
                "dramatic", "khủng khiếp", "không ngờ"
            ],
            "target_tags": [
                "giat_minh", "reaction", "vine_boom", "boom", "braam", "hit", "shock",
                "giat_gan", "impact", "dramatic", "thud", "metal"
            ],
        },
        "diem_nhan": {
            "label": "💡 Điểm nhấn / Chú ý",
            "keywords": [
                "bạn có biết", "tại sao", "làm thế nào", "bí mật", "chú ý", "quan trọng",
                "đặc biệt", "lưu ý", "nhớ rằng", "số 1", "triệu đô", "kỷ lục", "chính xác",
                "mẹo", "cách làm", "bật mí", "phát hiện", "đây là", "nhìn này", "xem này",
                "secret", "how to", "why", "stop", "chưa từng"
            ],
            "target_tags": [
                "diem_nhan", "bell", "ding", "pop", "bubble", "coin", "highlight",
                "bounce", "kinetic", "ping", "chime", "pill"
            ],
        },
        "chuyen_canh": {
            "label": "🎬 Chuyển cảnh / Bước tiếp",
            "keywords": [
                "tiếp theo", "sau đó", "bước tiếp", "ngoài ra", "tuy nhiên", "nhưng mà",
                "mặt khác", "hãy cùng", "tóm lại", "sang phần", "chuyển sang", "kết thúc",
                "bắt đầu", "next", "then", "transition", "chuyển qua", "tổng kết"
            ],
            "target_tags": [
                "chuyen_canh", "whoosh", "swish", "slide", "zoom", "whip", "minimal",
                "clean", "transition"
            ],
        },
        "cong_nghe": {
            "label": "💻 Công nghệ / Phần mềm",
            "keywords": [
                "công nghệ", "phần mềm", "website", "ứng dụng", "máy tính", "laptop",
                "code", "ai", "trí tuệ nhân tạo", "dữ liệu", "hệ thống", "cấu hình",
                "benchmark", "link", "giao diện", "thông số", "tech", "software",
                "app", "pc", "tool", "lập trình", "thiết bị"
            ],
            "target_tags": [
                "cong_nghe", "tech", "keyboard", "glitch", "neon", "cyberpunk", "typing",
                "ui", "beep", "review"
            ],
        },
    }

    def __init__(self, assets: Optional[List[Any]] = None):
        self.assets = assets or []

    def set_assets(self, assets: List[Any]):
        """Cập nhật kho assets khả dụng."""
        self.assets = assets

    def _normalize_text(self, text: str) -> str:
        return " " + re.sub(r"[^\w\s]", " ", text.lower()) + " "

    def analyze_emotion_scores(self, text: str, role: Optional[str] = None) -> Dict[str, float]:
        """Tính điểm phù hợp cho từng nhóm cảm xúc từ câu thoại và vai trò kịch bản."""
        scores: Dict[str, float] = {k: 0.0 for k in self.EMOTION_RULES}
        norm_text = self._normalize_text(text)

        # 1. Quét từ khóa
        for emo_key, data in self.EMOTION_RULES.items():
            for kw in data["keywords"]:
                if kw in norm_text:
                    scores[emo_key] += 1.5

        # 2. Tăng cường theo vai trò kịch bản (ShotRole từ Shotbook)
        if role:
            role_lower = str(role).lower()
            if "hook" in role_lower:
                scores["diem_nhan"] += 2.0
                scores["chuyen_canh"] += 1.0
            elif "punchline" in role_lower:
                scores["giat_minh"] += 3.0
            elif "evidence" in role_lower:
                scores["cong_nghe"] += 2.5
                scores["diem_nhan"] += 1.5
            elif "summary" in role_lower:
                scores["chuyen_canh"] += 2.0

        return scores

    def suggest_for_text(
        self,
        text: str,
        role: Optional[str] = None,
        max_suggestions: int = 6
    ) -> List[SuggestedAsset]:
        """
        Đề xuất danh sách đạo cụ phù hợp nhất với câu thoại đầu vào.
        Trả về các SuggestedAsset bao gồm âm thanh, hình ảnh/meme và mẫu chữ.
        """
        if not self.assets:
            return []

        scores = self.analyze_emotion_scores(text, role=role)
        
        # Sắp xếp các cảm xúc theo điểm cao nhất
        sorted_emotions = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_emotion, top_score = sorted_emotions[0]

        # Nếu không có từ khóa nào khớp và không có vai trò kịch bản, dùng bộ mặc định cân bằng
        if top_score <= 0.0:
            return self._get_fallback_suggestions(max_suggestions)

        active_emotions = [k for k, s in sorted_emotions if s > 0.0]
        if not active_emotions:
            active_emotions = [top_emotion]

        candidates: List[SuggestedAsset] = []
        seen_ids = set()

        for asset in self.assets:
            asset_id = getattr(asset, "id", "")
            if asset_id in seen_ids:
                continue

            asset_tab = getattr(asset, "tab", "")
            asset_cat = str(getattr(asset, "category", "")).lower()
            asset_sub = str(getattr(asset, "sub", "")).lower()
            asset_name = str(getattr(asset, "name", "")).lower()

            # Tính điểm asset với các emotion đang active
            best_match_score = 0.0
            best_match_reason = ""

            for emo in active_emotions:
                emo_data = self.EMOTION_RULES[emo]
                weight = scores[emo]
                tags = emo_data["target_tags"]

                match_level = 0.0
                if asset_cat in tags or asset_sub in tags:
                    match_level += 3.0
                if any(t in asset_id.lower() or t in asset_name for t in tags):
                    match_level += 2.0

                if match_level > 0.0:
                    current_score = match_level * weight
                    if current_score > best_match_score:
                        best_match_score = current_score
                        best_match_reason = emo_data["label"]

            if best_match_score > 0.0:
                confidence = min(0.99, round(best_match_score / 15.0 + 0.4, 2))
                candidates.append(
                    SuggestedAsset(
                        asset=asset,
                        reason=best_match_reason,
                        confidence=confidence,
                        category_type=asset_tab
                    )
                )
                seen_ids.add(asset_id)

        # Sắp xếp theo confidence giảm dần
        candidates.sort(key=lambda x: x.confidence, reverse=True)

        # Phân phối đa dạng (đa chủng loại: SFX, Meme, Text, Transition)
        diversified: List[SuggestedAsset] = []
        by_tab: Dict[str, List[SuggestedAsset]] = {}
        for c in candidates:
            by_tab.setdefault(c.category_type, []).append(c)

        # Ưu tiên lấy ít nhất 1 cái từ các loại có kết quả (SFX -> Meme -> Text -> Trans -> LUT)
        priority_tabs = ["sfx", "meme", "text", "trans", "lut", "bgm"]
        for tab in priority_tabs:
            if tab in by_tab and by_tab[tab]:
                diversified.append(by_tab[tab].pop(0))
                if len(diversified) >= max_suggestions:
                    break

        # Nếu còn chỗ, lấp đầy các ứng viên điểm cao còn lại
        if len(diversified) < max_suggestions:
            remaining = [c for c in candidates if c not in diversified]
            diversified.extend(remaining[: max_suggestions - len(diversified)])

        return diversified[:max_suggestions]

    def _get_fallback_suggestions(self, max_suggestions: int = 6) -> List[SuggestedAsset]:
        """Gợi ý các asset phổ biến tiêu chuẩn khi câu thoại trung tính."""
        fallback_candidates = []
        for asset in self.assets:
            aid = getattr(asset, "id", "")
            atab = getattr(asset, "tab", "")
            # Chọn những asset tiêu biểu
            if any(k in aid.lower() for k in ["ding", "vine_boom", "whoosh", "bounce", "clean", "mixi", "slide"]):
                fallback_candidates.append(
                    SuggestedAsset(
                        asset=asset,
                        reason="⚡ Phổ biến & Nổi bật",
                        confidence=0.75,
                        category_type=atab
                    )
                )
            if len(fallback_candidates) >= max_suggestions:
                break
        
        # Nếu vẫn ít hơn, lấy vài asset đầu tiên
        if len(fallback_candidates) < max_suggestions:
            for asset in self.assets:
                if not any(fc.asset.id == asset.id for fc in fallback_candidates):
                    fallback_candidates.append(
                        SuggestedAsset(
                            asset=asset,
                            reason="✨ Đạo cụ hữu ích",
                            confidence=0.60,
                            category_type=getattr(asset, "tab", "")
                        )
                    )
                if len(fallback_candidates) >= max_suggestions:
                    break

        return fallback_candidates[:max_suggestions]

    def suggest_for_shotbook_item(self, item: Any, max_suggestions: int = 6) -> List[SuggestedAsset]:
        """Hỗ trợ trực tiếp từ đối tượng ShotbookItem của ShotbookOrchestrator."""
        text = getattr(item, "text", "")
        role = getattr(item, "role", None)
        return self.suggest_for_text(text, role=role, max_suggestions=max_suggestions)
