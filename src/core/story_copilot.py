import os
import re
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple, Union, Sequence
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CopilotHookPlan(BaseModel):
    """Kế hoạch Hook mở đầu do AI Copilot chỉ định."""
    clip_id: Optional[str] = None
    clip_index: int
    clip_name: str = ""
    start_sec: float
    end_sec: float
    hook_title: str
    reason: str
    punch_in: bool = True


class CopilotSegmentPlan(BaseModel):
    """Một phân đoạn trong mạch câu chuyện được AI Copilot sắp xếp."""
    clip_id: Optional[str] = None
    clip_index: int
    clip_name: str = ""
    chapter_name: str = ""
    start_sec: float
    end_sec: float
    action: str = "keep"  # "keep" hoặc "speedup"
    speed: float = 1.0
    role: str = "build"   # "intro", "build", "climax", "outro", "broll"
    note: str = ""


class CopilotMediaInsert(BaseModel):
    """Gợi ý chèn B-Roll/Meme (Track Video 2) hoặc SFX (Track Audio 2) của AI Copilot."""
    timeline_sec: float
    duration_sec: float = 2.5  # Thời lượng chèn chuẩn (2.0s - 3.0s tránh đè mất câu nói)
    src_in: float = 0.0        # Điểm bắt đầu cắt trong clip meme
    asset_file: str = ""       # Tên file trong kho assets (vd: do_mixi_ao_that_day.mp4, pewpew_nhin_lau_lam_roi.mp4)
    asset_type: str = "meme"   # "meme", "green_screen", "broll", "sfx"
    description: str = ""      # Lý do chèn
    suggested_file: str = ""   # Tương thích ngược


class CopilotDirectorPlan(BaseModel):
    """Toàn bộ bản thiết kế dựng phim do Cloud AI trả về."""
    manifest_id: Optional[str] = None
    strategy_summary: str = ""
    target_platform: str = "YouTube / Vlog"
    global_hook: Optional[CopilotHookPlan] = None
    timeline_segments: List[CopilotSegmentPlan] = Field(default_factory=list)
    broll_inserts: List[CopilotMediaInsert] = Field(default_factory=list)
    sfx_inserts: List[CopilotMediaInsert] = Field(default_factory=list)
    viral_headlines: List[str] = Field(default_factory=list)
    call_to_action: str = ""
    seo_hashtags: List[str] = Field(default_factory=list)


class StoryCopilot:
    """
    Bộ động cơ sinh Prompt, Gọi Trực Tiếp AI (Ollama / Cloud API) & Bóc tách Bản vẽ Đạo diễn.
    """

    FORMAT_PROMPTS = {
        "travel_vlog": """# 🎯 BẠN LÀ TỔNG ĐẠO DIỄN NỘI DUNG & BIÊN KỊCH DAILY VLOG / TRAVEL VLOG (CHIEF STORYTELLER)
## 🧠 NGUYÊN TẮC BIÊN TẬP VLOG:
1. **Tôn Trọng Mạch Thời Gian (Chronological Flow):** Giữ câu chuyện theo diễn tiến thực tế của chuyến đi.
2. **Nhịp Thở Tự Nhiên & Giữ Trọn Vẹn Câu Thoại:**
   - Tuyệt đối KHÔNG cắt ngang giữa chừng khi nhân vật đang nói (tránh bị cụt chữ, mất nhịp).
   - Hãy để câu nói kết thúc trọn vẹn, nghỉ thở tự nhiên rồi mới chuyển cảnh (10s - 35s/phân đoạn).
   - Bạn CHỈ CẦN CHỈ ĐỊNH CHÍNH XÁC điểm bắt đầu và kết thúc của câu chuyện. Hệ thống sẽ TỰ ĐỘNG tính toán khoảng đệm an toàn.
3. **Đoạn Mở Đầu Teaser / Highlight Hook Độc Lập (30s - 60s):**
   - Đối với Vlog, hãy chọn một đoạn cao trào gay cấn, hài hước hoặc một câu chuyện dạo đầu kịch tính kéo dài từ 30s đến 60s (hoặc tối thiểu 15s-30s) để làm Teaser Hook mở đầu.
   - Đoạn này đặt độc lập ở đầu video để kích thích người xem tò mò xem hết vlog.
   - Trong JSON, đặt tên `hook_title` rõ ràng (Ví dụ: `🔥 [TEASER HOOK 45s] - Tên đoạn mở đầu`) để trên DaVinci Resolve hiển thị rõ ràng, dễ nhận biết và tách sửa.
4. **Điều Tiết SFX & Meme (Tuyệt Đối Không Lạm Dụng / Spam):**
   - Chỉ chèn SFX khi THỰC SỰ có điểm rơi cảm xúc hoặc chuyển cảnh quan trọng (tối đa 1 SFX mỗi 20-30 giây).
   - Không spam liên tục cùng một âm thanh (như Vine Boom, Whoosh). Phải đa dạng âm thanh hoặc để không gian yên tĩnh tự nhiên.
   - Toàn bộ video dài chỉ chèn 2-4 meme/broll đắt giá nhất, không chèn tràn lan.
""",
        "tiktok_short": """# 🎯 BẠN LÀ CHUYÊN GIA DỰNG VIDEO NGẮN VIRAL (TIKTOK / REELS / SHORTS 9:16)
## 🧠 NGUYÊN TẮC DỰNG SHORTS VIRAL:
1. **Hook 3 Giây Đầu Cực Gắt:** Chọn câu phát ngôn gây tò mò/sốc nhất đặt ngay Frame 0:00.
2. **Bảo Toàn Ý Nghĩa Lời Thoại:** Cắt khoảng lặng thừa nhưng TUYỆT ĐỐI không cắt đứt giữa từ hay giữa câu nói (không gây giật giọng, mất nhịp).
3. **Tổng Thời Lượng Mục Tiêu:** 45s đến 60s, chắt lọc chỉ lấy tinh hoa của toàn bộ clip.
4. **Tiết Chế SFX & Meme:** Chỉ chèn âm thanh (Vine Boom, Whoosh, Pop) đúng điểm rơi bất ngờ. Không spam âm thanh liên tục gây nhức đầu. Tối đa 1-2 meme cho toàn clip.
""",
        "podcast_summary": """# 🎯 BẠN LÀ ĐẠO DIỄN TALKING HEAD / PODCAST & HIGHLIGHTS
## 🧠 NGUYÊN TẮC BIÊN TẬP PODCAST:
1. **Dọn Dẹp Ngữ Nghĩa Nhưng Tròn Vành Rõ Chữ:** Loại bỏ khoảng lặng thừa nhưng giữ trọn vẹn từng câu nói, không cắt giữa chừng chữ.
2. **Cấu Trúc Luận Điểm Rõ Ràng:** Giữ trọn vẹn từng ý kiến, câu chuyện của diễn giả.
3. **Punch-in Zoom:** Đổi góc máy cận khi chuyển ý quan trọng.
4. **SFX Điểm Nhấn Tinh Tế:** Ding / Pop chỉ khi xuất hiện từ khóa hoặc số liệu chính, tuyệt đối không lạm dụng.
"""
    }

    MARKETING_FRAMEWORK_PROMPT = FORMAT_PROMPTS["travel_vlog"]

    @classmethod
    def get_available_assets_summary(cls) -> str:
        """Liệt kê các meme, green screen và sound effects có sẵn trong kho assets."""
        lines = ["## 🎭 DANH SÁCH TÀI NGUYÊN MEME & B-ROLL CÓ SẴN TRONG KHO (DÙNG ĐIỀN VÀO asset_file):"]
        
        base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))
        memes_dir = os.path.join(base_dir, "broll_memes", "memes")
        gs_dir = os.path.join(base_dir, "broll_memes", "green_screen")
        sfx_dir = os.path.join(base_dir, "sfx")

        meme_desc_map = {
            "01_funny_wow_reaction.mp4": "Reaction Wow bất ngờ, trầm trồ",
            "02_facepalm_toang_reaction.mp4": "Reaction Facepalm đỡ trán, bất lực / toang",
            "anh_da_den_cham_hoi.mp4": "Anh da đen chấm hỏi Nick Young (Khi ngơ ngác, không hiểu chuyện gì)",
            "bieu_hien_cua_su_luon_leo.mp4": "Thầy Giáo Ba 'Đó là những biểu hiện của sự lươn lẹo' (Khi lấp liếm, nói dối)",
            "boman_dap_ban_cay_cu.mp4": "Bomman đập bàn gào thét cay cú CS:GO (Khi ức chế, bùng nổ)",
            "boman_thong_tin_chuan_chua.mp4": "Bomman 'Mày thông tin chuẩn chưa anh em' (Khi nghi ngờ tính xác thực)",
            "cat_vibing_head.mp4": "Chú mèo gật đầu theo điệu nhạc (Khi nghe nhạc chill, vibe, nhảy múa)",
            "chua_gap_truong_hop_nay.mp4": "Cụ bà 'Tôi năm nay hơn 70 tuổi chưa gặp trường hợp nào thế này' (Khi gặp chuyện kỳ quặc)",
            "con_dung_cai_nit.mp4": "Tiến Bịp 'Còn đúng cái nịt' (Khi mất sạch, trắng tay, không còn gì)",
            "de_et_hai_huoc.mp4": "Dễ ẹt / Quá đơn giản (Khi giải quyết việc một cách dễ dàng)",
            "do_mixi_ao_that_day.mp4": "Khá Bảnh & Độ Mixi 'Ảo thật đấy' (Khi gặp chuyện bất ngờ, không tưởng)",
            "do_mixi_cay_cu_toang.mp4": "Độ Mixi cay cú hài hước (Khi bị toang, game over)",
            "do_mixi_cuoi_sac_sua.mp4": "Độ Mixi cười sặc sụa ngoác mồm (Khi tấu hài, trêu chọc)",
            "do_mixi_may_mu_a.mp4": "Độ Mixi 'Mày không thấy à / Mày bị mù à' (Khi ai đó không chú ý)",
            "huan_hoa_hong_co_lam_moi_co_an.mp4": "Huấn Hoa Hồng 'Có làm thì mới có ăn' (Nói về lao động, đạo lý tiền bạc)",
            "hut_tiep_di.mp4": "Hút tiếp đi / Cạn lời (Khi ai đó làm chuyện ngáo ngơ)",
            "joker_cuoi_dien_dai.mp4": "Joker cười điên dại (Cảnh kịch tính, điên rồ)",
            "khaby_lame_nhun_vai.mp4": "Khaby Lame nhún vai bất lực chỉ tay (Khi việc đơn giản bị làm phức tạp)",
            "may_chon_cai_chet.mp4": "À thì ra mày chọn cái chết (Khi thách thức, trêu ngươi nguy hiểm)",
            "may_dung_co_mong_tuong.mp4": "Độ Mixi 'Mày đừng có mộng tưởng' (Khi ai đó ảo tưởng sức mạnh)",
            "meme_meo_khoc_banana.mp4": "Mèo chuối Banana khóc thút thít (Khi buồn bã, đáng thương, mất mát)",
            "nhin_thay_roi.mp4": "Tôi đã nhìn thấy rồi / Bắt quả tang tại trận",
            "pewpew_gioi_nhat_la_boc_phet.mp4": "PewPew 'Anh thì cái gì cũng giỏi, nhưng giỏi nhất là bốc phét' (Khi chém gió, nổ to)",
            "pewpew_nhin_lau_lam_roi.mp4": "PewPew 'Bố mày nhịn mày lâu lắm rồi đấy' (Khi tức giận bùng nổ)",
            "pewpew_so_qua_co.mp4": "PewPew 'Ui sợ, quá sợ, sợ quá phải bắn nó thôi' (Khi cà khịa đối thủ)",
            "phai_toi_danh_cho_may_nhat.mp4": "Phải tôi tôi đánh cho mấy nhát rồi (Khi bức xúc, bất bình)",
            "rat_la_thuyet_phuc.mp4": "Nghe rất chi là thuyết phục (Khi nghe lập luận buồn cười nhưng hợp lý)",
            "sao_may_ngu_the_ha.mp4": "Sao mày ngu thế hả (Khi đồng đội làm hỏng việc ngớ ngẩn)",
            "thay_ba_gat_cay_cu.mp4": "Thầy Giáo Ba cay cú gắt gỏng (Khi bị troll, chơi bẩn)",
            "thay_ba_reaction.mp4": "Thầy Giáo Ba reaction khó hiểu / hoang mang",
            "tuyet_qua_hao_han.mp4": "Tuyệt quá! Thế mới là hảo hán chứ (Khen ngợi anh hùng, đẳng cấp)",
            "vo_tay_vo_tay.mp4": "Vỗ tay! Vỗ tay! (Cổ vũ, chúc mừng nhiệt liệt)",
            "xach_balo_len_va_di.mp4": "Xách ba lô lên và đi (Bắt đầu hành trình phiêu lưu, du lịch)",
            "01_subscribe_bell_notification.mp4": "Nút Subscribe & Chuông YouTube phông xanh",
            "02_dramatic_countdown_321.mp4": "Đếm ngược kịch tính 3-2-1 -> GO!",
            "03_breaking_news_banner.mp4": "Tin nóng Breaking News",
            "04_censored_pixel_bar.mp4": "Thanh đen che miệng Censored + tiếng bíp",
        }

        if os.path.exists(memes_dir):
            for fn in sorted(os.listdir(memes_dir)):
                if fn.lower().endswith(('.mp4', '.mov', '.gif')):
                    desc = meme_desc_map.get(fn, "Meme clip video ngắn")
                    lines.append(f"- `{fn}`: {desc}")

        if os.path.exists(gs_dir):
            for fn in sorted(os.listdir(gs_dir)):
                if fn.lower().endswith(('.mp4', '.mov')):
                    desc = meme_desc_map.get(fn, "Green Screen Animation")
                    lines.append(f"- `{fn}`: {desc}")

        lines.append("")
        lines.append("## 🔊 DANH SÁCH HIỆU ỨNG ÂM THANH SFX CÓ SẴN (DÙNG ĐIỀN VÀO asset_file CỦA sfx_inserts):")
        if os.path.exists(sfx_dir):
            for fn in sorted(os.listdir(sfx_dir)):
                if fn.lower().endswith('.wav'):
                    lines.append(f"- `{fn}`")

        return "\n".join(lines)

    @classmethod
    def generate_copilot_prompt(
        cls,
        project_name: str,
        clips_data: List[Dict[str, Any]],
        project_structure: Optional[Any] = None,
        story_intent: str = "travel_vlog",
        target_duration: str = "full",
        hook_duration: str = "30_60s",
        music_beats: Optional[List[float]] = None,
        manifest_id: Optional[str] = None
    ) -> str:
        """
        Tạo Prompt hoàn chỉnh chứa đầy đủ Skill Marketing + Dữ liệu Video + Danh sách Meme để người dùng dán vào Claude/ChatGPT.
        """
        framework = cls.FORMAT_PROMPTS.get(story_intent, cls.FORMAT_PROMPTS.get("travel_vlog", cls.MARKETING_FRAMEWORK_PROMPT))
        mid = manifest_id or getattr(project_structure, "manifest_id", None) or (clips_data[0].get("manifest_id") if clips_data else None)

        # Lọc bỏ clip có thời lượng < 1.0s khỏi danh sách kịch bản
        valid_clips = [
            c for c in clips_data
            if float(c.get("duration", c.get("duration_sec", 0.0))) >= 1.0 or float(c.get("duration", c.get("duration_sec", 0.0))) <= 0
        ]

        lines = [
            framework,
            cls.get_available_assets_summary(),
            "",
            f"## 📁 THÔNG TIN DỰ ÁN: {project_name}",
        ]
        if mid:
            lines.append(f"- MANIFEST_ID: {mid}")
        lines.extend([
            f"- Tổng số clip: {len(valid_clips)} video",
            f"- Ý đồ dựng mục tiêu: {story_intent.upper()}",
            f"- Thời lượng mục tiêu: {target_duration.upper()}",
        ])

        if hook_duration == "30_60s":
            lines.extend([
                "",
                "## 🔥 YÊU CẦU ĐẶC BIỆT VỀ ĐOẠN HOOK MỞ ĐẦU (30s - 60s CHO VLOG):",
                "- Đối với định dạng Vlog/Storytelling, đoạn `global_hook` BẮT BUỘC DÀI TỪ 30 ĐẾN 60 GIÂY (`end_sec - start_sec` trong khoảng 30.0s đến 60.0s).",
                "- Hãy chọn một phân đoạn cao trào, gay cấn, hài hước hoặc một câu chuyện dạo đầu kịch tính kéo dài 30s - 60s để cuốn hút người xem ngay từ đầu.",
                "- Đặt tên `hook_title` bắt đầu bằng `🔥 [TEASER HOOK]` kèm thời lượng và tóm tắt (Ví dụ: `🔥 [TEASER HOOK 45s] CÚ TWIST BẤT NGỜ`) để trên DaVinci Resolve hiển thị rõ ràng, dễ nhận biết và tách sửa."
            ])
        elif hook_duration == "15_30s":
            lines.extend([
                "",
                "## 🔥 YÊU CẦU ĐẶC BIỆT VỀ ĐOẠN HOOK MỞ ĐẦU (15s - 30s):",
                "- Đoạn `global_hook` cần dài từ 15 đến 30 giây (`end_sec - start_sec` từ 15.0s đến 30.0s).",
                "- Đặt tên `hook_title` bắt đầu bằng `🔥 [TEASER HOOK]` để dễ nhận diện trên timeline DaVinci Resolve."
            ])
        elif hook_duration == "3_5s":
            lines.extend([
                "",
                "## 🔥 YÊU CẦU ĐẶC BIỆT VỀ ĐOẠN HOOK MỞ ĐẦU (3s - 5s SHORTS):",
                "- Đoạn `global_hook` dài từ 3 đến 5 giây, giật tít tức thì cho video ngắn."
            ])
        elif hook_duration == "none":
            lines.extend([
                "",
                "## ℹ️ KHÔNG CẦN ĐOẠN HOOK RIÊNG:",
                "- Hãy để `\"global_hook\": null` và đi thẳng vào câu chuyện chính trong `timeline_segments`."
            ])

        if music_beats:
            beat_strs = [f"{b:.1f}" for b in music_beats[:30]] # Chỉ lấy 30 beats tiêu biểu tránh nổ token
            lines.extend([
                "",
                "## 🎵 CÁC MỐC NHỊP (BEAT DROP) CỦA NHẠC NỀN:",
                f"- Nhịp điệu rơi vào các giây: {', '.join(beat_strs)}",
                "👉 YÊU CẦU ĐẶC BIỆT: Hãy ưu tiên điều chỉnh `timeline_sec` của `broll_inserts` và chuyển cảnh KHỚP với các mốc nhịp này để tạo hiệu ứng Beat-Sync đã mắt nhất."
            ])

        def _fmt_tc(sec_val: float) -> str:
            m = int(sec_val // 60)
            s = int(sec_val % 60)
            return f"{m:02d}:{s:02d}"

        lines.extend([
            "",
            "## 🎬 DANH SÁCH DỮ LIỆU CÁC CLIP NGUỒN (THEO THỜI GIAN THỰC TẾ):",
            "| ID | Tên Clip | Chương/Thư mục | Độ dài | Phân loại | Đoạn thoại chính | Tóm tắt nội dung |",
            "| :---: | :--- | :--- | :---: | :---: | :---: | :--- |"
        ])

        for idx, clip in enumerate(valid_clips):
            c_id = str(clip.get("clip_id") or f"C{idx+1:02d}")
            c_name = clip.get("name", clip.get("clip_name", f"Clip_{idx+1}"))
            c_chap = clip.get("chapter", "Chung")
            dur = float(clip.get("duration", clip.get("duration_sec", 0.0)))
            is_broll = bool(clip.get("is_broll", False))
            intervals = clip.get("dialogue_intervals") or clip.get("speech_intervals") or []

            text = clip.get("transcript", clip.get("speech", clip.get("transcription", ""))).strip()
            text_snippet = text.replace("\n", " ").replace("|", "-")
            if is_broll or not text_snippet or "[CẢNH B-ROLL" in text_snippet:
                is_broll = True
                type_label = "🏞️ B-Roll"
                speech_range = "*(Không lời)*"
                text_snippet = "Cảnh thiên nhiên / B-roll không lời"
            else:
                type_label = "💬 Thoại"
                if intervals:
                    speech_range = f"{_fmt_tc(intervals[0]['start'])} - {_fmt_tc(intervals[-1]['end'])}"
                else:
                    speech_range = f"00:00 - {_fmt_tc(dur)}"
                if len(text_snippet) > 80:
                    text_snippet = text_snippet[:75] + "..."

            lines.append(f"| {c_id} | `{c_name}` | {c_chap} | {dur:.1f}s | {type_label} | {speech_range} | {text_snippet} |")

        lines.extend([
            "",
            "## ⏱️ CHI TIẾT LỜI THOẠI & TIMESTAMPS TỪNG CÂU (ĐÃ LỌC NHIỄU & CĂN CHUẨN THỜI GIAN):",
            "*(Dựa vào mốc thời gian [start - end] bên dưới để chọn start_sec / end_sec bao trọn vẹn câu thoại và chèn Meme/SFX vào các khoảng lặng giữa câu!)*",
            ""
        ])

        for idx, clip in enumerate(valid_clips):
            c_id = str(clip.get("clip_id") or f"C{idx+1:02d}")
            c_name = clip.get("name", clip.get("clip_name", f"Clip_{idx+1}"))
            dur = float(clip.get("duration", clip.get("duration_sec", 0.0)))
            is_broll = bool(clip.get("is_broll", False))
            intervals = clip.get("dialogue_intervals") or clip.get("speech_intervals") or []
            text = clip.get("transcript", "").strip()

            if is_broll or not intervals or "[CẢNH B-ROLL" in text:
                lines.append(f"- **Clip {c_id}** (`{c_name}` - {dur:.1f}s):\n  * 🏞️ *[TOÀN BỘ CLIP LÀ B-ROLL / PHONG CẢNH THIÊN NHIÊN ({dur:.1f}s)]*")
            else:
                lines.append(f"- **Clip {c_id}** (`{c_name}` - {dur:.1f}s):")
                # Liệt kê từng câu thoại (tối đa 12 câu tiêu biểu để tránh nổ token)
                limit_intervals = intervals[:12]
                for s in limit_intervals:
                    s_st = float(s.get("start", 0.0))
                    s_en = float(s.get("end", 0.0))
                    s_txt = str(s.get("text", "")).strip().replace("\n", " ")
                    lines.append(f'  * [{_fmt_tc(s_st)} - {_fmt_tc(s_en)}] "{s_txt}"')
                if len(intervals) > 12:
                    rem = len(intervals) - 12
                    lines.append(f"  * *(và {rem} câu thoại tiếp theo đến {_fmt_tc(dur)})*")
                # Nếu cuối clip có khoảng lặng lớn (> 3s), báo cho AI biết đó là b-roll
                last_en = float(intervals[-1].get("end", 0.0))
                if dur - last_en >= 3.0:
                    lines.append(f"  * 🍃 *(Khoảng lặng {_fmt_tc(last_en)} - {_fmt_tc(dur)}: B-roll / Cảnh quan)*")

        lines.extend([
            "",
            "---",
            "",
            "## 🧠 NGUYÊN TẮC BIÊN TẬP DÀNH CHO TỔNG ĐẠO DIỄN (CHIEF STORYTELLER):",
            "1. **BẢO TOÀN LỜI THOẠI (KHÔNG CẮT CỤT / MẤT NHỊP):**",
            "   - Hãy nhìn vào mốc thời gian chi tiết của từng câu ở phần trên để chọn `start_sec` và `end_sec` bao trùm trọn vẹn câu nói của nhân vật.",
            "   - Hệ thống DaVinci Resolve sẽ TỰ ĐỘNG BÙ ĐỆM AN TOÀN 0.3s ở hai đầu cắt để câu nói không bao giờ bị cụt âm.",
            "   - TUYỆT ĐỐI KHÔNG cắt giữa chừng khi nhân vật đang nói dở.",
            "2. **KHAI THÁC CÁC CLIP B-ROLL / PHONG CẢNH KHÔNG CÓ THOẠI (🏞️):**",
            "   - Các clip B-roll không lời là tài nguyên quý giá để tạo nhịp thở cho video.",
            "   - Hãy cắt các đoạn 3s - 5s đặt làm cảnh chuyển tiếp (transition) giữa 2 phân đoạn nói chuyện khác nhau.",
            "   - Hoặc khai thác trong `broll_inserts` trên Video Track 2 khi nhân vật đang nói về phong cảnh.",
            "   - Không dùng speed khác 1.0 (đường xuất hiện tại không hỗ trợ). Mọi action 'speedup' sẽ được đưa về 1.0x.",
            "3. **ĐIỂM RƠI SFX & MEME (ĐẶT VÀO KHOẢNG LẶNG, TRÁNH ĐÈ THOẠI):**",
            "   - TUYỆT ĐỐI KHÔNG chèn SFX hoặc Meme đè lên lúc nhân vật đang nói câu quan trọng.",
            "   - Hãy đặt `timeline_sec` rơi vào khoảng lặng ngay sau câu nói (punchline) để tạo tiếng cười hoặc điểm nhấn.",
            "   - Tần suất: Tối đa 1 SFX mỗi 20-30 giây. Toàn bộ video dài chỉ dùng 2-4 meme đắt giá nhất.",
            "",
            "---",
            "",
            "## 📤 YÊU CẦU ĐẦU RA (OUTPUT FORMAT):",
            "Hãy phân tích và trả về ĐÚNG MỘT KHỐI JSON DUY NHẤT (trong cặp dấu ```json ... ```) theo cấu trúc chuẩn sau đây để phần mềm ResolveFlow tự động đọc và dựng ngay:",
            "",
            "```json",
            "{",
            f'  "manifest_id": "{mid or "..."}",',
            '  "strategy_summary": "Giải thích ngắn gọn lý do chọn Hook và cách sắp xếp mạch chuyện",',
            '  "target_platform": "YouTube Vlog / Shorts",',
            '  "global_hook": {',
            '    "clip_index": 1,',
            '    "clip_name": "Tên clip được chọn làm hook",',
            '    "start_sec": 10.0,',
            '    "end_sec": 50.0,',
            '    "hook_title": "🔥 [TEASER HOOK 40s] CÂU CHUYỆN BẤT NGỜ MỞ ĐẦU",',
            '    "reason": "Đoạn cao trào gay cấn dài 40s mở đầu kích thích người xem xem hết vlog",',
            '    "punch_in": true',
            "  },",
            '  "timeline_segments": [',
            "    {",
            '      "clip_index": 1,',
            '      "clip_name": "Tên clip",',
            '      "chapter_name": "01_Mở đầu",',
            '      "start_sec": 0.0,',
            '      "end_sec": 12.5,',
            '      "action": "keep",',
            '      "speed": 1.0,',
            '      "role": "intro",',
            '      "note": "Giới thiệu chuyến đi"',
            "    }",
            "  ],",
            '  "broll_inserts": [',
            "    {",
            '      "timeline_sec": 14.5,',
            '      "duration_sec": 2.5,',
            '      "src_in": 0.0,',
            '      "asset_file": "do_mixi_ao_that_day.mp4",',
            '      "asset_type": "meme",',
            '      "description": "Độ Mixi Ảo thật đấy khi thấy điều kỳ quặc"',
            "    }",
            "  ],",
            '  "sfx_inserts": [',
            "    {",
            '      "timeline_sec": 14.5,',
            '      "duration_sec": 1.0,',
            '      "asset_file": "vine_boom.wav",',
            '      "asset_type": "sfx",',
            '      "description": "Tiếng nổ vine boom nhấn mạnh cú sốc"',
            "    }",
            "  ],",
            '  "viral_headlines": [',
            '    "5 tiêu đề giật tít kích thích CTR cao nhất"',
            "  ],",
            '  "call_to_action": "Câu kêu gọi hành động ở cuối video",',
            '  "seo_hashtags": ["#Vlog", "#DuLich", "#ResolveFlow"]',
            "}",
            "```",
            "",
            "👉 BẮT BUỘC giữ nguyên 'manifest_id' trong khối JSON kết quả nếu có!",
            "👉 Hãy phân tích thật kỹ toàn bộ danh sách clip trên và xuất bản khối JSON tối ưu nhất!"
        ])

        return "\n".join(lines)

    @classmethod
    def parse_copilot_response(cls, response_text: str) -> CopilotDirectorPlan:
        """
        Bóc tách và xác thực chuỗi JSON do Claude/ChatGPT/Gemini trả về.
        """
        if not response_text or not isinstance(response_text, str):
            raise ValueError("Nội dung phản hồi từ AI trống.")

        clean = response_text.strip()
        # Chuẩn hóa các ký tự Unicode vô hình, dấu cách không ngắt dòng và BOM
        clean = (
            clean.replace("\xa0", " ")
            .replace("\u202f", " ")
            .replace("\u2007", " ")
            .replace("\u3000", " ")
            .replace("\u200b", "")
            .replace("\ufeff", "")
            .replace("\u200c", "")
            .replace("\u200d", "")
        )

        # 1. Trích xuất khối ```json ... ``` nếu có
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # Tìm cặp dấu ngoặc nhọn đầu tiên và cuối cùng
            start_brace = clean.find("{")
            end_brace = clean.rfind("}")
            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                json_str = clean[start_brace:end_brace + 1]
            else:
                json_str = clean

        # Thay thế ngoặc kép và ngoặc đơn thông minh (smart/curly quotes)
        json_str = re.sub(r'[\u201c\u201d\u201e\u201f\xab\xbb]', '"', json_str)
        json_str = re.sub(r'[\u2018\u2019\u201a\u201b]', "'", json_str)

        # Loại bỏ các comment thường gặp trong JSON sinh bởi LLM (// hoặc /* */)
        json_str = re.sub(r'(?<!:)\/\/(?![\/\\])[^\r\n]*', '', json_str)
        json_str = re.sub(r'\/\*[\s\S]*?\*\/', '', json_str)

        # Loại bỏ trailing comma trước } hoặc ] (lỗi cú pháp phổ biến của AI)
        json_str = re.sub(r',\s*([\]}])', r'\1', json_str)

        data = None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            # Fallback 1: Thử ast.literal_eval nếu AI trả về dict dạng Python
            try:
                import ast
                val = ast.literal_eval(json_str)
                if isinstance(val, dict):
                    data = val
            except Exception:
                pass

            # Fallback 2: Chuyển đổi keys bọc bằng dấu nháy đơn ' sang "
            if data is None:
                try:
                    fixed = re.sub(r"(?<=[{\s,])\'([a-zA-Z0-9_]+)\'\s*:", r'"\1":', json_str)
                    data = json.loads(fixed)
                except Exception:
                    pass

            if data is None:
                raise ValueError(f"Không thể đọc định dạng JSON từ phản hồi của AI: {e}")

        if not isinstance(data, dict):
            raise ValueError("Dữ liệu JSON không đúng cấu trúc đối tượng (Object).")

        # Chuẩn hóa Global Hook
        hook_obj = None
        raw_hook = data.get("global_hook")
        if isinstance(raw_hook, dict):
            try:
                c_idx = 1
                try:
                    c_idx = int(raw_hook.get("clip_index", 1))
                except (ValueError, TypeError):
                    c_idx = 1

                hook_obj = CopilotHookPlan(
                    clip_index=c_idx,
                    clip_name=str(raw_hook.get("clip_name", "")).strip(),
                    start_sec=float(raw_hook.get("start_sec", 0.0)),
                    end_sec=float(raw_hook.get("end_sec", 4.0)),
                    hook_title=str(raw_hook.get("hook_title", "ĐIỀU BẤT NGỜ NHẤT")),
                    reason=str(raw_hook.get("reason", "Hook mở đầu")),
                    punch_in=bool(raw_hook.get("punch_in", True))
                )
            except Exception:
                hook_obj = None

        # Chuẩn hóa Timeline Segments
        segments_list: List[CopilotSegmentPlan] = []
        raw_segs = data.get("timeline_segments", [])
        if isinstance(raw_segs, list):
            for s in raw_segs:
                if isinstance(s, dict):
                    try:
                        c_idx = 1
                        try:
                            c_idx = int(s.get("clip_index", 1))
                        except (ValueError, TypeError):
                            c_idx = 1

                        segments_list.append(CopilotSegmentPlan(
                            clip_index=c_idx,
                            clip_name=str(s.get("clip_name", "")).strip(),
                            chapter_name=str(s.get("chapter_name", "")),
                            start_sec=float(s.get("start_sec", 0.0)),
                            end_sec=float(s.get("end_sec", 5.0)),
                            action=str(s.get("action", "keep")),
                            speed=float(s.get("speed", 1.0)),
                            role=str(s.get("role", "build")),
                            note=str(s.get("note", ""))
                        ))
                    except Exception:
                        pass

        # Chuẩn hóa B-Roll inserts
        broll_list: List[CopilotMediaInsert] = []
        raw_brolls = data.get("broll_inserts", [])
        if isinstance(raw_brolls, list):
            for b in raw_brolls:
                if isinstance(b, dict):
                    try:
                        broll_list.append(CopilotMediaInsert(
                            timeline_sec=float(b.get("timeline_sec", 0.0)),
                            duration_sec=float(b.get("duration_sec", 2.5)),
                            src_in=float(b.get("src_in", 0.0)),
                            asset_file=str(b.get("asset_file", b.get("suggested_file", ""))).strip(),
                            asset_type=str(b.get("asset_type", "meme")),
                            description=str(b.get("description", "")),
                            suggested_file=str(b.get("suggested_file", "")).strip()
                        ))
                    except Exception:
                        pass

        # Chuẩn hóa SFX inserts
        sfx_list: List[CopilotMediaInsert] = []
        raw_sfx = data.get("sfx_inserts", [])
        if isinstance(raw_sfx, list):
            for sf in raw_sfx:
                if isinstance(sf, dict):
                    try:
                        sfx_list.append(CopilotMediaInsert(
                            timeline_sec=float(sf.get("timeline_sec", 0.0)),
                            duration_sec=float(sf.get("duration_sec", 1.0)),
                            src_in=float(sf.get("src_in", 0.0)),
                            asset_file=str(sf.get("asset_file", sf.get("suggested_file", ""))).strip(),
                            asset_type=str(sf.get("asset_type", "sfx")),
                            description=str(sf.get("description", "")),
                            suggested_file=str(sf.get("suggested_file", "")).strip()
                        ))
                    except Exception:
                        pass

        mid_val = data.get("manifest_id")
        manifest_id_parsed = str(mid_val).strip() if mid_val else None

        return CopilotDirectorPlan(
            manifest_id=manifest_id_parsed,
            strategy_summary=str(data.get("strategy_summary", "")),
            target_platform=str(data.get("target_platform", "YouTube / Vlog")),
            global_hook=hook_obj,
            timeline_segments=segments_list,
            broll_inserts=broll_list,
            sfx_inserts=sfx_list,
            viral_headlines=[str(h) for h in data.get("viral_headlines", []) if h],
            call_to_action=str(data.get("call_to_action", "")),
            seo_hashtags=[str(tag) for tag in data.get("seo_hashtags", []) if tag]
        )

    @classmethod
    def run_ollama_inference(
        cls,
        prompt: str,
        model: str = "qwen2.5:7b-instruct",
        endpoint: str = "http://localhost:11434",
        timeout: float = 120.0
    ) -> Tuple[CopilotDirectorPlan, str]:
        """
        Gửi prompt trực tiếp sang Local Ollama server và nhận về bản vẽ kịch bản JSON.
        """
        url = f"{endpoint.rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                raw_text = res_body.get("response", "")
                plan = cls.parse_copilot_response(raw_text)
                return plan, raw_text
        except urllib.error.URLError as e:
            raise ConnectionError(f"Không thể kết nối đến Ollama tại {endpoint}. Hãy đảm bảo Ollama đang chạy: {e}")
        except Exception as e:
            raise RuntimeError(f"Lỗi khi xử lý với Local AI Ollama: {e}")

    @classmethod
    def run_cloud_api_inference(
        cls,
        prompt: str,
        api_key: str,
        provider: str = "deepseek",
        model: Optional[str] = None,
        timeout: float = 120.0
    ) -> Tuple[CopilotDirectorPlan, str]:
        """
        Gửi prompt trực tiếp sang Cloud API (Google Gemini / DeepSeek / OpenAI / Claude) qua chuẩn chat completions hoặc native REST.
        """
        if not api_key:
            raise ValueError("Vui lòng cung cấp API Key.")

        key_clean = api_key.strip()
        provider_clean = (provider or "deepseek").lower()

        # Tự động nhận diện nhà cung cấp nếu người dùng dán key đặc trưng
        if key_clean.startswith("AQ.") or key_clean.startswith("AIza"):
            provider_clean = "gemini"
        elif key_clean.startswith("sk-ant-"):
            provider_clean = "claude"

        if "gemini" in provider_clean or "google" in provider_clean:
            # Google Gemini - Ưu tiên OpenAI-compatible endpoint, tự động chuyển đổi sang native nếu cần
            url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            default_model = "gemini-3.5-flash"
        elif "deepseek" in provider_clean:
            url = "https://api.deepseek.com/v1/chat/completions"
            default_model = "deepseek-chat"
        elif "openai" in provider_clean or "gpt" in provider_clean:
            url = "https://api.openai.com/v1/chat/completions"
            default_model = "gpt-4o"
        elif "claude" in provider_clean or "anthropic" in provider_clean:
            url = "https://api.anthropic.com/v1/messages"
            default_model = "claude-3-7-sonnet-20250219"
        else:
            url = "https://api.deepseek.com/v1/chat/completions"
            default_model = "deepseek-chat"

        chosen_model = model or default_model

        if "claude" in provider_clean or "anthropic" in provider_clean:
            payload = {
                "model": chosen_model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}]
            }
            headers = {
                "x-api-key": key_clean,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        else:
            payload = {
                "model": chosen_model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You are a professional video director and editor. Respond with a valid JSON plan."},
                    {"role": "user", "content": prompt}
                ]
            }
            headers = {
                "Authorization": f"Bearer {key_clean}",
                "Content-Type": "application/json"
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                if "claude" in provider_clean or "anthropic" in provider_clean:
                    raw_text = res_body.get("content", [{}])[0].get("text", "")
                else:
                    raw_text = res_body.get("choices", [{}])[0].get("message", {}).get("content", "")
                plan = cls.parse_copilot_response(raw_text)
                return plan, raw_text
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            # Nếu Gemini trả về 404 cho model hiện tại, tự động fallback sang gemini-3-flash-preview
            if ("gemini" in provider_clean or "google" in provider_clean) and e.code == 404 and chosen_model != "gemini-3-flash-preview":
                try:
                    payload["model"] = "gemini-3-flash-preview"
                    retry_data = json.dumps(payload).encode("utf-8")
                    retry_req = urllib.request.Request(url, data=retry_data, headers=headers)
                    with urllib.request.urlopen(retry_req, timeout=timeout) as resp:
                        res_body = json.loads(resp.read().decode("utf-8"))
                        raw_text = res_body.get("choices", [{}])[0].get("message", {}).get("content", "")
                        plan = cls.parse_copilot_response(raw_text)
                        return plan, raw_text
                except Exception:
                    pass
            raise RuntimeError(f"Cloud API trả về lỗi ({e.code}): {err_body}")
        except Exception as e:
            raise RuntimeError(f"Lỗi khi gửi yêu cầu đến Cloud API: {e}")

    @classmethod
    def _find_clip_path(
        cls,
        clip_index: Optional[Union[int, str]],
        clip_name: Optional[str],
        video_paths_by_index: Dict[Any, str],
        warnings_collector: Optional[List[str]] = None,
        return_tuple: bool = False
    ) -> Union[Optional[str], Tuple[Optional[str], List[str]]]:
        """
        Tìm kiếm đường dẫn tệp video tối ưu nhất từ bản vẽ kịch bản dựa trên ClipManifest.
        Thứ tự ưu tiên:
        1. clip_id / clip_index (khóa chính).
        2. clip_name khớp chính xác (chỉ khi không có hoặc không tìm thấy clip_index).
        3. Nếu có cả hai mà mâu thuẫn -> ưu tiên dùng index, ghi cảnh báo nêu cả hai.
        4. KHÔNG dùng fuzzy matching hay fallback theo vị trí mảng. Nếu không tìm thấy -> trả None + cảnh báo.
        """
        import unicodedata
        warnings: List[str] = []

        if not video_paths_by_index:
            warn = "Danh sách đường dẫn video (path_map) rỗng."
            warnings.append(warn)
            if warnings_collector is not None:
                warnings_collector.append(warn)
            return (None, warnings) if return_tuple else None

        def norm_txt(s: str) -> str:
            clean = str(s).strip().strip("`'\"“”‘’").strip()
            return unicodedata.normalize('NFC', clean.lower())

        path_from_index: Optional[str] = None
        path_from_name: Optional[str] = None

        # 1. Tra cứu theo clip_id / clip_index (khóa chính)
        if clip_index is not None:
            # Tra trực tiếp key
            if clip_index in video_paths_by_index:
                cand = video_paths_by_index[clip_index]
                if cand:
                    path_from_index = cand

            # Thử biến thể chữ hoa / chữ thường nếu là chuỗi (vd "C01", "c01")
            if not path_from_index and isinstance(clip_index, str):
                for k in (clip_index.upper(), clip_index.lower()):
                    if k in video_paths_by_index:
                        cand = video_paths_by_index[k]
                        if cand:
                            path_from_index = cand
                            break

            # Thử trích xuất số nguyên từ chuỗi (vd "Clip 3", "#3", 3, "3")
            if not path_from_index:
                digits = re.findall(r'\d+', str(clip_index))
                if digits:
                    idx_int = int(digits[0])
                    for k in (f"C{idx_int:02d}", f"C{idx_int:03d}", idx_int, str(idx_int), f"clip_{idx_int}", f"clip {idx_int}"):
                        if k in video_paths_by_index:
                            cand = video_paths_by_index[k]
                            if cand:
                                path_from_index = cand
                                break

        # 2. Tra cứu theo clip_name (chính xác filename hoặc stem)
        if clip_name:
            raw_cname = str(clip_name).strip().strip("`'\"“”‘’").strip()
            clean_name = norm_txt(os.path.basename(raw_cname))
            stem_name = norm_txt(os.path.splitext(os.path.basename(raw_cname))[0])
            rel_name = norm_txt(raw_cname.replace('\\', '/'))

            for cand_key in (clean_name, stem_name, rel_name, norm_txt(raw_cname)):
                if cand_key in video_paths_by_index:
                    cand = video_paths_by_index[cand_key]
                    if cand:
                        path_from_name = cand
                        break

            if not path_from_name:
                for p in video_paths_by_index.values():
                    if not isinstance(p, str):
                        continue
                    p_base = norm_txt(os.path.basename(p))
                    p_stem = norm_txt(os.path.splitext(os.path.basename(p))[0])
                    if p_base == clean_name or p_stem == stem_name:
                        path_from_name = p
                        break

        # 3. Phân xử (Resolution)
        resolved_path: Optional[str] = None
        if path_from_index:
            if path_from_name and os.path.abspath(path_from_index) != os.path.abspath(path_from_name):
                # Mâu thuẫn giữa clip_index và clip_name
                warn = (
                    f"Xung đột giữa clip_index={clip_index} ({os.path.basename(path_from_index)}) "
                    f"và clip_name='{clip_name}' ({os.path.basename(path_from_name)}): Ưu tiên sử dụng clip_index."
                )
                warnings.append(warn)
                logger.warning(warn)
                if warnings_collector is not None:
                    warnings_collector.append(warn)
            resolved_path = path_from_index
        elif path_from_name:
            resolved_path = path_from_name
        else:
            warn = f"Không tìm thấy clip tương ứng cho clip_index={clip_index}, clip_name='{clip_name}'. Phân đoạn sẽ bị loại bỏ."
            warnings.append(warn)
            logger.warning(warn)
            if warnings_collector is not None:
                warnings_collector.append(warn)
            resolved_path = None

        if return_tuple:
            return (resolved_path, warnings)
        return resolved_path

    @classmethod
    def convert_plan_to_resolve_timeline(
        cls,
        plan: CopilotDirectorPlan,
        video_paths_by_index: Dict[Any, str],
        subtitles_by_path: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        bgm_files: Optional[Sequence[str]] = None,
        music_beats: Optional[Sequence[float]] = None,
        fps: float = 30.0,
        speed_mode: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Chuyển đổi bản vẽ Copilot thành danh sách Events (Track 1 & Track 2), Subtitles, Markers sẵn sàng nạp lên DaVinci Resolve.
        Sử dụng TimelineMapper để đảm bảo mọi frame và vị trí phụ đề được tính toán đồng nhất theo frame nguyên.
        
        Returns:
            Tuple[events, subtitles, markers]
        """
        from src.core.broll_sfx import GlobalAssetPool
        from src.core.autocut import get_media_metadata
        from src.core.timeline_mapper import TimelineMapper
        asset_pool = GlobalAssetPool.get_instance()

        events: List[Dict[str, Any]] = []
        subtitles: List[Dict[str, Any]] = []
        markers: List[Dict[str, Any]] = []

        base_asset_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))

        # Xác định FPS chuẩn từ clip đầu tiên nếu có
        real_fps = float(fps)
        first_v = None
        for p in video_paths_by_index.values():
            if isinstance(p, str) and os.path.exists(p):
                first_v = p
                break
        if first_v:
            try:
                m_fps = get_media_metadata(first_v).get("fps")
                if m_fps and float(m_fps) > 0:
                    real_fps = float(m_fps)
            except Exception as e:
                logger.debug(f"Không thể đọc fps từ {first_v}: {e}")

        mapper = TimelineMapper(fps=real_fps, speed_mode=speed_mode)

        # 1. Nạp Global Hook vào TimelineMapper nếu có
        hook_placed = None
        if plan.global_hook:
            h = plan.global_hook
            vpath = cls._find_clip_path(getattr(h, "clip_id", None) or h.clip_index, h.clip_name, video_paths_by_index)
            if vpath and os.path.exists(vpath):
                meta = get_media_metadata(vpath)
                max_dur = meta.get("duration", 0.0) or 3600.0
                st = max(0.0, float(h.start_sec))
                en = float(h.end_sec)
                if max_dur > 0:
                    en = min(max_dur, en)
                if en > st:
                    clean_hook_title = h.hook_title or "Intro Teaser"
                    hook_placed = mapper.add_segment(
                        clip_id=getattr(h, "clip_id", None) or f"C{h.clip_index:02d}",
                        src_in_sec=st,
                        src_out_sec=en,
                        speed=1.0,
                        role="hook",
                        pad_sec=0.3,
                        clip_duration_sec=max_dur,
                        clip_name=os.path.basename(vpath),
                        file_path=vpath,
                        title=f"🔥 [TEASER HOOK] {clean_hook_title}"
                    )

        # 2. Nạp các phân đoạn kịch bản vào TimelineMapper (Track 1)
        for seg in plan.timeline_segments:
            vpath = cls._find_clip_path(getattr(seg, "clip_id", None) or seg.clip_index, seg.clip_name, video_paths_by_index)
            if not vpath or not os.path.exists(vpath):
                continue

            meta = get_media_metadata(vpath)
            max_dur = meta.get("duration", 0.0) or 3600.0
            st = max(0.0, float(seg.start_sec))
            en = float(seg.end_sec)
            if max_dur > 0:
                en = min(max_dur, en)
            if en <= st:
                continue

            mapper.add_segment(
                clip_id=getattr(seg, "clip_id", None) or f"C{seg.clip_index:02d}",
                src_in_sec=st,
                src_out_sec=en,
                speed=float(seg.speed or 1.0),
                role=seg.role or "build",
                pad_sec=0.3,
                clip_duration_sec=max_dur,
                clip_name=os.path.basename(vpath),
                file_path=vpath,
                title=seg.chapter_name or os.path.basename(vpath)
            )

        # 3. Tạo events và markers trực tiếp từ TimelineMapper (A-Roll Track 1)
        color_map = {
            "intro": "Blue",
            "build": "Yellow",
            "climax": "Red",
            "outro": "Purple",
            "broll": "Cyan",
            "dialogue": "Green",
            "hook": "Magenta"
        }

        for seg_idx, placed in enumerate(mapper.segments):
            dur_sec = placed.rec_dur_frames / mapper.fps
            is_hook = (placed.role == "hook")
            events.append({
                "video_path": placed.file_path,
                "src_in": placed.src_in_frames / mapper.fps,
                "src_out": placed.src_out_frames / mapper.fps,
                "rec_in": placed.rec_in_frames / mapper.fps,
                "rec_out": placed.rec_out_frames / mapper.fps,
                "src_in_frames": placed.src_in_frames,
                "src_out_frames": placed.src_out_frames,
                "src_dur_frames": placed.src_dur_frames,
                "rec_in_frames": placed.rec_in_frames,
                "rec_out_frames": placed.rec_out_frames,
                "rec_dur_frames": placed.rec_dur_frames,
                "speed": placed.applied_speed,
                "punch_in": is_hook,
                "punch_in_scale": 1.15 if is_hook else 1.0,
                "is_hook": is_hook,
                "track": 1,
                "clip_name": placed.title or placed.clip_name,
                "chapter": placed.title or placed.clip_name,
                "role": placed.role,
                "note": placed.title
            })

            m_time = placed.rec_in_frames / mapper.fps
            m_dur = min(dur_sec, 2.0)
            markers.append({
                "time": m_time,
                "duration": m_dur,
                "name": f"🎬 [{placed.role.upper()}] {placed.title[:30]}",
                "color": color_map.get(placed.role.lower(), "Green"),
                "note": f"Phân đoạn: {placed.title}"
            })

        from src.core.timeline_mapper import TimelineMapper, add_subtitles_for_range

        # 4. Trích xuất phụ đề đồng bộ 100% qua mapper.src_to_timeline_sec
        if subtitles_by_path:
            for seg_idx, placed in enumerate(mapper.segments):
                vpath = placed.file_path
                clip_subs = subtitles_by_path.get(vpath) or subtitles_by_path.get(os.path.abspath(vpath)) or []
                if not clip_subs:
                    v_base = os.path.basename(vpath).lower()
                    for k, v in subtitles_by_path.items():
                        if os.path.basename(k).lower() == v_base:
                            clip_subs = v
                            break

                mapped_subs = add_subtitles_for_range(mapper, seg_idx, clip_subs, vpath=vpath)
                subtitles.extend(mapped_subs)
        elif hook_placed and plan.global_hook and plan.global_hook.hook_title:
            subtitles.append({
                "start": hook_placed.rec_in_frames / mapper.fps,
                "end": min((hook_placed.rec_in_frames + hook_placed.rec_dur_frames) / mapper.fps, (hook_placed.rec_in_frames / mapper.fps) + 5.0),
                "text": plan.global_hook.hook_title,
                "words": [],
                "video_path": hook_placed.file_path
            })

        cursor = mapper.get_total_duration_sec()

        # 3. Chèn B-Roll / Meme Inserts (Track 2) - Tự động cắt gọt đúng duration_sec
        for broll in plan.broll_inserts:
            t_sec = broll.timeline_sec
            dur = max(0.5, broll.duration_sec)  # Giới hạn chuẩn 1.5s - 3.0s

            # Tìm file thực tế qua GlobalAssetPool trước, fallback duyệt thư mục
            resolved_asset_path = ""
            if broll.asset_file:
                resolved_asset_path = asset_pool.resolve_meme(broll.asset_file) or ""

            if not resolved_asset_path and broll.asset_file and os.path.exists(base_asset_dir):
                for root_dir, _, files in os.walk(base_asset_dir):
                    for f in files:
                        if f.lower() == broll.asset_file.lower():
                            resolved_asset_path = os.path.join(root_dir, f)
                            break
                    if resolved_asset_path:
                        break

            if resolved_asset_path and os.path.exists(resolved_asset_path):
                events.append({
                    "video_path": resolved_asset_path,
                    "src_in": broll.src_in,
                    "src_out": broll.src_in + dur,
                    "rec_in": t_sec,
                    "rec_out": t_sec + dur,
                    "speed": 1.0,
                    "track": 2,
                    "is_broll_overlay": True,
                    "note": broll.description
                })

            if 0.0 <= t_sec <= cursor + 10.0:
                file_label = f" ({broll.asset_file})" if broll.asset_file else ""
                markers.append({
                    "time": t_sec,
                    "duration": dur,
                    "name": f"🎬 [B-ROLL TRACK 2]{file_label} {broll.description}",
                    "color": "Cyan",
                    "note": f"Gợi ý chèn B-Roll / Meme lên Video Track 2 ({dur:.1f}s): {broll.description}"
                })

        # 4. Chèn SFX Inserts (Audio Track 3)
        last_sfx_time = -999.0
        for sfx in sorted(plan.sfx_inserts, key=lambda x: x.timeline_sec):
            t_sec = sfx.timeline_sec
            if t_sec - last_sfx_time < 10.0:
                continue
            last_sfx_time = t_sec
            dur = max(0.2, sfx.duration_sec)
            
            resolved_sfx_path = ""
            if sfx.asset_file:
                resolved_sfx_path = asset_pool.resolve_sfx(sfx.asset_file) or ""

            if not resolved_sfx_path and sfx.asset_file and os.path.exists(base_asset_dir):
                for root_dir, _, files in os.walk(base_asset_dir):
                    for f in files:
                        if f.lower() == sfx.asset_file.lower():
                            resolved_sfx_path = os.path.join(root_dir, f)
                            break
                    if resolved_sfx_path:
                        break

            if resolved_sfx_path and os.path.exists(resolved_sfx_path):
                events.append({
                    "video_path": resolved_sfx_path,
                    "src_in": sfx.src_in,
                    "src_out": sfx.src_in + dur,
                    "rec_in": t_sec,
                    "rec_out": t_sec + dur,
                    "speed": 1.0,
                    "track": 3,
                    "is_sfx": True,
                    "note": sfx.description
                })

            if 0.0 <= t_sec <= cursor + 5.0:
                file_label = f" ({sfx.asset_file})" if sfx.asset_file else ""
                markers.append({
                    "time": t_sec,
                    "duration": dur,
                    "name": f"🔊 [SFX TRACK 3]{file_label} {sfx.description}",
                    "color": "Green",
                    "note": f"Hiệu ứng âm thanh SFX ({dur:.1f}s): {sfx.description}"
                })

        # 5. Thêm Marker CTA ở cuối nếu có
        if plan.call_to_action and cursor > 0:
            cta_start = max(0.0, cursor - 5.0)
            markers.append({
                "time": cta_start,
                "duration": 5.0,
                "name": "📣 CALL TO ACTION (CTA)",
                "color": "Purple",
                "note": plan.call_to_action
            })

        # 6. Thêm Beat Drop Markers nếu có music_beats
        if music_beats:
            for b_sec in music_beats:
                b_val = float(b_sec)
                markers.append({
                    "time": b_val,
                    "duration": 0.1,
                    "name": "🎵 Beat Drop",
                    "color": "Cyan",
                    "note": "Mốc nhịp nhạc nền (Auto Beat-Sync)"
                })

        return events, subtitles, markers

    @classmethod
    def add_subtitles_for_range(cls, *args, **kwargs):
        """Hàm ánh xạ phụ đề đồng bộ qua TimelineMapper."""
        from src.core.timeline_mapper import add_subtitles_for_range as _asr
        return _asr(*args, **kwargs)


add_subtitles_for_range = StoryCopilot.add_subtitles_for_range
