import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple, Union
from pydantic import BaseModel, Field


class CopilotHookPlan(BaseModel):
    """Kế hoạch Hook mở đầu do AI Copilot chỉ định."""
    clip_index: int
    clip_name: str = ""
    start_sec: float
    end_sec: float
    hook_title: str
    reason: str
    punch_in: bool = True


class CopilotSegmentPlan(BaseModel):
    """Một phân đoạn trong mạch câu chuyện được AI Copilot sắp xếp."""
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
3. **Global Hook Độc Lập:** 3-5s câu nói hoặc cú twist đắt giá nhất để mở đầu.
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
        story_intent: str = "vlog_hook",
        music_beats: Optional[List[float]] = None
    ) -> str:
        """
        Tạo Prompt hoàn chỉnh chứa đầy đủ Skill Marketing + Dữ liệu Video + Danh sách Meme để người dùng dán vào Claude/ChatGPT.
        """
        framework = cls.FORMAT_PROMPTS.get(story_intent, cls.FORMAT_PROMPTS.get("travel_vlog", cls.MARKETING_FRAMEWORK_PROMPT))
        lines = [
            framework,
            cls.get_available_assets_summary(),
            "",
            f"## 📁 THÔNG TIN DỰ ÁN: {project_name}",
            f"- Tổng số clip: {len(clips_data)} video",
            f"- Ý đồ dựng mục tiêu: {story_intent.upper()}",
        ]

        if music_beats:
            beat_strs = [f"{b:.1f}" for b in music_beats[:30]] # Chỉ lấy 30 beats tiêu biểu tránh nổ token
            lines.extend([
                "",
                "## 🎵 CÁC MỐC NHỊP (BEAT DROP) CỦA NHẠC NỀN:",
                f"- Nhịp điệu rơi vào các giây: {', '.join(beat_strs)}",
                "👉 YÊU CẦU ĐẶC BIỆT: Hãy ưu tiên điều chỉnh `timeline_sec` của `broll_inserts` và chuyển cảnh KHỚP với các mốc nhịp này để tạo hiệu ứng Beat-Sync đã mắt nhất."
            ])

        lines.extend([
            "",
            "## 🎬 DANH SÁCH DỮ LIỆU CÁC CLIP NGUỒN ĐÃ QUÉT:",
            "| ID | Tên Clip | Chương/Thư mục | Độ dài | Cao trào âm thanh | Chuyển động | Nội dung lời thoại (Transcript) |",
            "| :---: | :--- | :--- | :---: | :---: | :---: | :--- |"
        ])

        for idx, clip in enumerate(clips_data):
            c_name = clip.get("name", f"Clip_{idx+1}")
            c_chap = clip.get("chapter", "Chung")
            dur = clip.get("duration", 0.0)
            peak = clip.get("audio_peak_sec", 0.0)
            motion = clip.get("visual_motion", "vừa")
            text = clip.get("transcript", clip.get("speech", "")).strip()
            text_snippet = text.replace("\n", " ").replace("|", "-")
            if not text_snippet:
                text_snippet = "*(Cảnh quay visual / B-Roll không lời)*"
            elif len(text_snippet) > 120:
                text_snippet = text_snippet[:115] + "..."

            lines.append(f"| {idx+1} | `{c_name}` | {c_chap} | {dur:.1f}s | {peak:.1f}s | {motion} | {text_snippet} |")

        lines.extend([
            "",
            "---",
            "",
            "## ⚠️ QUY TẮC BẮT BUỘC VỀ LỜI THOẠI & ÂM THANH (CHỐNG MẤT NHỊP / LẠM DỤNG SFX):",
            "1. **BẢO TOÀN LỜI THOẠI (KHÔNG CẮT CỤT / MẤT NHỊP):**",
            "   - Khi nhân vật đang nói, TUYỆT ĐỐI KHÔNG cắt giữa chừng câu thoại làm cụt chữ hoặc mất nhịp.",
            "   - Hãy để câu nói kết thúc trọn vẹn, nghỉ thở tự nhiên rồi mới chuyển cảnh. Bạn CHỈ CẦN chọn đúng mốc thời gian, hệ thống sẽ TỰ ĐỘNG BÙ ĐỆM (padding) 0.3s.",
            "   - Nếu một clip là một câu chuyện/câu nói liền mạch, hãy giữ nguyên từ 0.0s đến hết độ dài clip thay vì cắt vụn.",
            "2. **ĐIỀU TIẾT HIỆU ỨNG ÂM THANH SFX (CHỐNG LẠM DỤNG / SPAM):**",
            "   - TUYỆT ĐỐI KHÔNG lạm dụng SFX (như spam liên tục vine_boom, whoosh). Việc chèn quá nhiều SFX làm video bị rẻ tiền, nhức tai và đè mất tiếng nhân vật.",
            "   - Tần suất: Tối đa 1 SFX mỗi 15-30 giây. Hệ thống backend sẽ TỰ ĐỘNG LOẠI BỎ các SFX chèn quá dày đặc (< 10 giây).",
            "   - Phải đa dạng SFX, KHÔNG dùng lặp lại 1 âm thanh duy nhất nhiều lần.",
            "   - SFX chỉ kéo dài 0.5s - 1.0s, không chèn đè lên lúc nhân vật đang nói câu thoại quan trọng.",
            "3. **B-ROLL & MEME HỢP LÝ:**",
            "   - Toàn bộ video dài chỉ nên chèn 2-4 meme/broll đắt giá nhất.",
            "   - Video ngắn (Shorts/TikTok) chỉ chèn 1-2 meme. Tránh chèn liên tục làm loãng câu chuyện.",
            "",
            "---",
            "",
            "## 📤 YÊU CẦU ĐẦU RA (OUTPUT FORMAT):",
            "Hãy phân tích và trả về ĐÚNG MỘT KHỐI JSON DUY NHẤT (trong cặp dấu ```json ... ```) theo cấu trúc chuẩn sau đây để phần mềm ResolveFlow tự động đọc và dựng ngay:",
            "",
            "```json",
            "{",
            '  "strategy_summary": "Giải thích ngắn gọn lý do chọn Hook và cách sắp xếp mạch chuyện",',
            '  "target_platform": "YouTube Vlog / Shorts",',
            '  "global_hook": {',
            '    "clip_index": 12,',
            '    "clip_name": "Tên clip được chọn làm hook",',
            '    "start_sec": 14.0,',
            '    "end_sec": 18.0,',
            '    "hook_title": "CHỮ CHÈN MÀN HÌNH GIẬT TÍT (TEXT OVERLAY)",',
            '    "reason": "Lý do vì sao đoạn này là hook đỉnh nhất",',
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

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
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

        return CopilotDirectorPlan(
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
        Gửi prompt trực tiếp sang Cloud API (DeepSeek / OpenAI / Claude) qua chuẩn chat completions.
        """
        if not api_key:
            raise ValueError("Vui lòng cung cấp API Key.")

        provider_clean = (provider or "deepseek").lower()
        if "deepseek" in provider_clean:
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
                "x-api-key": api_key,
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
                "Authorization": f"Bearer {api_key}",
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
            raise RuntimeError(f"Cloud API trả về lỗi ({e.code}): {err_body}")
        except Exception as e:
            raise RuntimeError(f"Lỗi khi gửi yêu cầu đến Cloud API: {e}")

    @classmethod
    def _find_clip_path(
        cls,
        clip_index: Optional[Union[int, str]],
        clip_name: Optional[str],
        video_paths_by_index: Dict[Any, str]
    ) -> Optional[str]:
        """
        Tìm kiếm đường dẫn tệp video tối ưu nhất từ bản vẽ kịch bản.
        Hỗ trợ đối soát đa tầng: tên file, đường dẫn tương đối, stem, unicode normalization,
        bóc tách số ID từ text, và fuzzy match.
        """
        import unicodedata
        if not video_paths_by_index:
            return None

        def norm_txt(s: str) -> str:
            clean = s.strip().strip("`'\"“”‘’").strip()
            return unicodedata.normalize('NFC', clean.lower())

        # 1. Khớp theo clip_name nếu có
        if clip_name:
            raw_cname = str(clip_name).strip().strip("`'\"“”‘’").strip()
            clean_name = norm_txt(os.path.basename(raw_cname))
            stem_name = norm_txt(os.path.splitext(os.path.basename(raw_cname))[0])
            rel_name = norm_txt(raw_cname.replace('\\', '/'))

            # Kiểm tra key trực tiếp trong dict
            for cand_key in (clean_name, stem_name, rel_name, norm_txt(raw_cname)):
                if cand_key in video_paths_by_index:
                    cand = video_paths_by_index[cand_key]
                    if cand and os.path.exists(cand):
                        return cand

            # Quét các đường dẫn giá trị trong map
            for p in video_paths_by_index.values():
                if not isinstance(p, str):
                    continue
                p_norm = norm_txt(p.replace('\\', '/'))
                p_base = norm_txt(os.path.basename(p))
                p_stem = norm_txt(os.path.splitext(os.path.basename(p))[0])

                if p_base == clean_name or p_stem == stem_name:
                    if os.path.exists(p):
                        return p
                if rel_name and (p_norm.endswith(rel_name) or rel_name in p_norm):
                    if os.path.exists(p):
                        return p

        # 2. Khớp theo clip_index (hỗ trợ số nguyên, chuỗi, hoặc text chứa số như "Clip 13", "#13")
        if clip_index is not None:
            if clip_index in video_paths_by_index:
                cand = video_paths_by_index[clip_index]
                if cand and os.path.exists(cand):
                    return cand

            # Trích xuất số nguyên từ chuỗi
            digits = re.findall(r'\d+', str(clip_index))
            if digits:
                idx_int = int(digits[0])
                for k in (idx_int, str(idx_int), idx_int - 1, str(idx_int - 1)):
                    if k in video_paths_by_index:
                        cand = video_paths_by_index[k]
                        if cand and os.path.exists(cand):
                            return cand

        # 3. Fallback: Nếu clip_name chứa số thứ tự (ví dụ: "Clip 12", "Video 5")
        if clip_name:
            digits_name = re.findall(r'\d+', str(clip_name))
            if digits_name:
                idx_int = int(digits_name[0])
                for k in (idx_int, str(idx_int)):
                    if k in video_paths_by_index:
                        cand = video_paths_by_index[k]
                        if cand and os.path.exists(cand):
                            return cand

        # 4. Fallback: Fuzzy matching trên toàn bộ danh sách đường dẫn
        if clip_name:
            c_lower = norm_txt(os.path.basename(str(clip_name)))
            if c_lower and len(c_lower) >= 3:
                for p in video_paths_by_index.values():
                    if not isinstance(p, str):
                        continue
                    p_base = norm_txt(os.path.basename(p))
                    if c_lower in p_base or p_base in c_lower:
                        if os.path.exists(p):
                            return p

        return None

    @classmethod
    def convert_plan_to_resolve_timeline(
        cls,
        plan: CopilotDirectorPlan,
        video_paths_by_index: Dict[Any, str]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Chuyển đổi bản vẽ Copilot thành danh sách Events (Track 1 & Track 2), Subtitles, Markers sẵn sàng nạp lên DaVinci Resolve.
        
        Returns:
            Tuple[events, subtitles, markers]
        """
        from src.core.broll_sfx import GlobalAssetPool
        from src.core.autocut import get_media_metadata
        asset_pool = GlobalAssetPool.get_instance()

        events: List[Dict[str, Any]] = []
        subtitles: List[Dict[str, Any]] = []
        markers: List[Dict[str, Any]] = []

        base_asset_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))

        cursor = 0.0

        # 1. Chèn Global Hook lên đầu Timeline nếu có
        if plan.global_hook:
            h = plan.global_hook
            vpath = cls._find_clip_path(h.clip_index, h.clip_name, video_paths_by_index)
            if vpath and os.path.exists(vpath):
                meta = get_media_metadata(vpath)
                max_dur = meta.get("duration", 0.0) or 3600.0
                
                start_val = max(0.0, h.start_sec - 0.3)
                end_val = h.end_sec + 0.3
                if max_dur > 0:
                    end_val = min(max_dur, end_val)

                if start_val < end_val:
                    dur = max(0.5, end_val - start_val)
                    events.append({
                        "video_path": vpath,
                        "src_in": start_val,
                        "src_out": end_val,
                        "rec_in": cursor,
                        "rec_out": cursor + dur,
                        "speed": 1.0,
                        "punch_in": h.punch_in,
                        "punch_in_scale": 1.15,
                        "is_hook": True,
                        "track": 1,
                        "reason": h.reason
                    })

                    markers.append({
                        "time": cursor,
                        "duration": dur,
                        "name": f"🔥 GLOBAL HOOK: {h.hook_title}",
                        "color": "Magenta",
                        "note": h.reason
                    })

                    if h.hook_title:
                        subtitles.append({
                            "start": cursor,
                            "end": cursor + dur,
                            "text": h.hook_title,
                            "video_path": vpath
                        })

                    cursor += dur

        # 2. Chèn các phân đoạn theo thứ tự kịch bản (Track 1)
        for seg in plan.timeline_segments:
            vpath = cls._find_clip_path(seg.clip_index, seg.clip_name, video_paths_by_index)
            if not vpath or not os.path.exists(vpath):
                continue

            meta = get_media_metadata(vpath)
            max_dur = meta.get("duration", 0.0) or 3600.0
            
            start_val = max(0.0, seg.start_sec - 0.3)
            end_val = seg.end_sec + 0.3
            if max_dur > 0:
                end_val = min(max_dur, end_val)
            
            if start_val >= end_val:
                continue

            dur = max(0.2, end_val - start_val)
            rec_dur = dur / seg.speed if seg.speed > 0 else dur

            events.append({
                "video_path": vpath,
                "src_in": start_val,
                "src_out": end_val,
                "rec_in": cursor,
                "rec_out": cursor + rec_dur,
                "speed": seg.speed,
                "punch_in": False,
                "track": 1,
                "chapter": seg.chapter_name,
                "role": seg.role,
                "note": seg.note
            })

            # Màu marker theo vai trò
            color_map = {
                "intro": "Blue",
                "build": "Yellow",
                "climax": "Red",
                "outro": "Purple",
                "broll": "Cyan",
                "dialogue": "Green",
                "hook": "Magenta"
            }
            m_color = color_map.get(seg.role.lower(), "Green")

            markers.append({
                "time": cursor,
                "duration": min(rec_dur, 2.0),
                "name": f"🎬 [{seg.role.upper()}] {seg.chapter_name or os.path.basename(vpath)}",
                "color": m_color,
                "note": seg.note
            })

            cursor += rec_dur

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

        return events, subtitles, markers
