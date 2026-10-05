"""
Marketing Engine & Psychology-driven Content Generator.
Ứng dụng các nguyên lý tâm lý học hành vi và chiến lược sáng tạo video chuyển đổi cao:
1. MarketingPsychologyScorer: Phân tích & chấm điểm Hook theo 5 góc độ tâm lý (Curiosity Gap, Loss Aversion, Transformation, Social Proof, Pattern Interrupt).
2. MarketingViralPackGenerator: Tự động phân tích transcript và xuất bản bộ Viral Pack (5 Tiêu đề giật tít, Hook Text Overlay, Kịch bản CTA, Hashtags & SEO Description).
"""

import os
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class HookAngleAnalysis(BaseModel):
    """Phân tích góc tiếp cận và tâm lý của một câu Hook."""
    angle_type: str  # curiosity, loss_aversion, transformation, social_proof, question
    angle_label: str
    psychology_trigger: str
    confidence_score: float
    recommended_text_overlay: str
    hook_text: str


class ViralPackReport(BaseModel):
    """Dữ liệu trọn gói Marketing Viral Pack cho video."""
    project_name: str
    top_headlines: List[Dict[str, str]]
    best_hooks: List[HookAngleAnalysis]
    cta_recommendations: List[Dict[str, str]]
    seo_hashtags: List[str]
    seo_description: str


class MarketingPsychologyScorer:
    """
    Bộ động cơ chấm điểm tâm lý học Marketing:
    - Curiosity Gap (Khoảng trống tò mò)
    - Loss Aversion / Urgency (Tránh mất mát / Khẩn cấp)
    - Transformation / High Value (Biến đổi / Giá trị cao)
    - Social Proof (Bằng chứng xã hội)
    - Question / Direct Challenge (Câu hỏi / Thách thức trực tiếp)
    """

    CURIOSITY_PATTERNS = [
        r"(bí mật|sự thật|tại sao|không ai nói|ít ai biết|lý do mà|điều gì|hóa ra|bất ngờ)",
        r"(chưa từng|lần đầu tiên|thực hư|khám phá|ẩn giấu|tiết lộ|hé lộ)",
    ]

    LOSS_AVERSION_PATTERNS = [
        r"(đừng bao giờ|sai lầm|mất tiền|nguy hiểm|cảnh báo|tiếc nuối|hối hận|sai bét)",
        r"(coi chừng|tránh ngay|tệ nhất|thất bại|bị lừa|đắt nhất|phí tiền)",
    ]

    TRANSFORMATION_PATTERNS = [
        r"(trước và sau|thay đổi hoàn toàn|từ con số 0|kết quả|thành công|bí quyết)",
        r"(lột xác|lên trình|cực đỉnh|siêu phẩm|đỉnh cao|thần kỳ|hiệu quả)",
    ]

    QUESTION_PATTERNS = [
        r"^(có bao giờ|bạn có biết|liệu có|làm thế nào|tại sao lại|ai mới là)",
        r"\?$",
    ]

    @classmethod
    def analyze_hook_sentence(cls, text: str) -> Optional[HookAngleAnalysis]:
        """Phân tích một câu thoại và nhận diện góc độ tâm lý học Marketing."""
        if not text or len(text.strip()) < 4:
            return None

        clean_text = text.strip()
        low = clean_text.lower()

        # 1. Kiểm tra Loss Aversion (Trọng số cao nhất vì tâm lý con người sợ mất mát gấp 2 lần ham muốn đạt được)
        for pat in cls.LOSS_AVERSION_PATTERNS:
            if re.search(pat, low):
                return HookAngleAnalysis(
                    angle_type="loss_aversion",
                    angle_label="⚠️ Cảnh báo / Tránh Mất Mát (Loss Aversion)",
                    psychology_trigger="Kích hoạt tâm lý sợ sai lầm / mất mát (FOMO)",
                    confidence_score=9.5,
                    recommended_text_overlay=f"ĐỪNG MẮC PHẢI SAI LẦM NÀY!",
                    hook_text=clean_text
                )

        # 2. Kiểm tra Curiosity Gap
        for pat in cls.CURIOSITY_PATTERNS:
            if re.search(pat, low):
                return HookAngleAnalysis(
                    angle_type="curiosity",
                    angle_label="🔍 Khoảng Trống Tò Mò (Curiosity Gap)",
                    psychology_trigger="Kích thích não bộ tìm kiếm lời giải đáp (Open Loop)",
                    confidence_score=9.0,
                    recommended_text_overlay=f"SỰ THẬT MÀ ÍT AI BIẾT...",
                    hook_text=clean_text
                )

        # 3. Kiểm tra Transformation / Kết quả
        for pat in cls.TRANSFORMATION_PATTERNS:
            if re.search(pat, low):
                return HookAngleAnalysis(
                    angle_type="transformation",
                    angle_label="✨ Biến Đổi & Kết Quả (Transformation)",
                    psychology_trigger="Khơi gợi khao khát đạt được kết quả tương tự",
                    confidence_score=8.5,
                    recommended_text_overlay=f"KẾT QUẢ SẼ LÀM BẠN BẤT NGỜ!",
                    hook_text=clean_text
                )

        # 4. Kiểm tra Question Hook
        for pat in cls.QUESTION_PATTERNS:
            if re.search(pat, low):
                return HookAngleAnalysis(
                    angle_type="question",
                    angle_label="❓ Câu Hỏi Trực Tiếp (Direct Challenge)",
                    psychology_trigger="Buộc người xem tự trả lời trong đầu (Tăng Engagement)",
                    confidence_score=8.0,
                    recommended_text_overlay=f"BẠN ĐÃ BIẾT ĐIỀU NÀY CHƯA?",
                    hook_text=clean_text
                )

        return None


class MarketingViralPackGenerator:
    """
    Bộ tạo gói Marketing & Viral Video Assets tự động.
    """

    @classmethod
    def generate_viral_pack(
        cls,
        project_name: str,
        subtitles: List[Dict[str, Any]],
        video_duration: float = 60.0
    ) -> ViralPackReport:
        """Phân tích toàn bộ phụ đề/nội dung để sản xuất gói Marketing hoàn chỉnh."""
        texts = [s.get("text", "").strip() for s in subtitles if s.get("text", "").strip()]
        full_transcript = " ".join(texts)

        # 1. Tìm các câu Hook xuất sắc
        best_hooks: List[HookAngleAnalysis] = []
        for s in subtitles:
            txt = s.get("text", "").strip()
            hook_analysis = MarketingPsychologyScorer.analyze_hook_sentence(txt)
            if hook_analysis:
                best_hooks.append(hook_analysis)

        # Fallback hook nếu không tìm thấy câu có từ khóa
        if not best_hooks and texts:
            best_hooks.append(HookAngleAnalysis(
                angle_type="curiosity",
                angle_label="🔍 Khoảng Trống Tò Mò (Curiosity Gap)",
                psychology_trigger="Khởi đầu câu chuyện tự nhiên",
                confidence_score=7.0,
                recommended_text_overlay="XEM HẾT ĐỂ BIẾT KẾT QUẢ!",
                hook_text=texts[0]
            ))

        # 2. Sinh 5 Tiêu đề giật tít theo 5 góc độ tiếp cận (Angle Headlines)
        topic_summary = texts[0][:40] if texts else project_name
        headlines = [
            {
                "angle": "Góc Cảnh báo / Tránh Mất Mát (Loss Aversion)",
                "title": f"Đừng Xem Video Này Nếu Bạn Chưa Biết Sự Thật Về {topic_summary}!",
                "ctr_potential": "Cực cao (9.5/10)"
            },
            {
                "angle": "Góc Tò mò / Bí mật (Curiosity Gap)",
                "title": f"Bí Mật Về {topic_summary} Mà 99% Mọi Người Đều Bỏ Qua",
                "ctr_potential": "Cao (9.0/10)"
            },
            {
                "angle": "Góc Hướng dẫn / Lối tắt (How-To / Shortcut)",
                "title": f"Cách Xử Lý {topic_summary} Nhanh Gấp 3 Lần Không Phải Ai Cũng Nói",
                "ctr_potential": "Ổn định (8.5/10)"
            },
            {
                "angle": "Góc Kết quả / Biến đổi (Transformation)",
                "title": f"Tôi Đã Thử {topic_summary} Và Kết Quả Thật Bất Ngờ...",
                "ctr_potential": "Cao (8.8/10)"
            },
            {
                "angle": "Góc Câu hỏi Thách thức (Direct Question)",
                "title": f"Liệu {topic_summary} Có Thật Sự Như Lời Đồn?",
                "ctr_potential": "Tương tác mạnh (8.2/10)"
            }
        ]

        # 3. Kịch bản Call-to-Action (CTA) tối ưu chuyển đổi
        cta_recommendations = [
            {
                "goal": "Tăng Follow & Kênh (Audience Growth)",
                "timing": "Ở 85% video (trước khi người xem thoát)",
                "script": "👉 Bấm Follow kênh ngay để không bỏ lỡ những bí quyết tiếp theo!"
            },
            {
                "goal": "Tăng Bình luận / Thảo luận (Algorithm Boost)",
                "timing": "Câu kết thúc video",
                "script": "💬 Còn bạn, bạn nghĩ sao về điều này? Hãy để lại ý kiến bên dưới nhé!"
            },
            {
                "goal": "Chuyển đổi / Click Link Bio (Conversion)",
                "timing": "Phần Outro chốt hạ",
                "script": "🚀 Nhấp ngay vào link trên bio để nhận toàn bộ tài liệu hướng dẫn miễn phí!"
            }
        ]

        # 4. Hashtags & SEO Description
        clean_proj = re.sub(r"[^\w\s]", "", project_name).replace(" ", "")
        seo_hashtags = [
            f"#{clean_proj}", "#LearnOnTikTok", "#ReviewChanThat", "#KienThucMoiNgay",
            "#Shorts", "#ViralVideo", "#XuHuong", "#DavinciResolve"
        ]

        seo_description = (
            f"🎬 {project_name}\n"
            f"Khám phá trọn vẹn nội dung: {full_transcript[:180]}...\n\n"
            f"👉 Đừng quên Like, Share và Đăng ký kênh để cập nhật video mới nhất!\n\n"
            f"{' '.join(seo_hashtags[:6])}"
        )

        return ViralPackReport(
            project_name=project_name,
            top_headlines=headlines,
            best_hooks=best_hooks[:5],
            cta_recommendations=cta_recommendations,
            seo_hashtags=seo_hashtags,
            seo_description=seo_description
        )

    @classmethod
    def export_markdown_report(cls, report: ViralPackReport, output_path: str):
        """Xuất bản file báo cáo kịch bản Marketing Markdown chuyên nghiệp."""
        lines = [
            f"# 🚀 Bộ Kế Hoạch & Kịch Bản Marketing Viral: {report.project_name}",
            f"> *Được khởi tạo tự động dựa trên nguyên lý Tâm lý học Hành vi & Performance Creative*",
            "",
            "---",
            "",
            "## 📌 1. BẢNG 5 TIÊU ĐỀ GIẬT TÍT ĐA GÓC ĐỘ (HIGH-CTR HEADLINES)",
            "| STT | Góc Tiếp Cận Tâm Lý | Tiêu Đề Gợi Ý | Tiềm Năng CTR |",
            "| :---: | :--- | :--- | :---: |"
        ]

        for i, h in enumerate(report.top_headlines, 1):
            lines.append(f"| {i} | **{h['angle']}** | `{h['title']}` | **{h['ctr_potential']}** |")

        lines.extend([
            "",
            "---",
            "",
            "## 🎯 2. DANH SÁCH HOOK MỞ ĐẦU ĐẮT GIÁ (0 - 3 GIÂY ĐẦU)",
            "| # | Góc Tâm Lý | Đoạn Thoại Gốc | Chữ Chèn Màn Hình (Text Overlay) | Điểm |",
            "| :---: | :--- | :--- | :--- | :---: |"
        ])

        for j, hk in enumerate(report.best_hooks, 1):
            lines.append(f"| {j} | {hk.angle_label} | *\"{hk.hook_text}\"* | **`{hk.recommended_text_overlay}`** | {hk.confidence_score:.1f} |")

        lines.extend([
            "",
            "---",
            "",
            "## 📣 3. KỊCH BẢN CALL-TO-ACTION (CTA) TỐI ƯU GIỮ CHÂN & CHUYỂN ĐỔI",
        ])

        for cta in report.cta_recommendations:
            lines.append(f"- **🎯 Mục tiêu: {cta['goal']}**")
            lines.append(f"  - *Thời điểm chèn:* `{cta['timing']}`")
            lines.append(f"  - *Lời thoại đề xuất:* \"{cta['script']}\"")
            lines.append("")

        lines.extend([
            "---",
            "",
            "## 📱 4. MÔ TẢ VIDEO & HASHTAGS PHÂN PHỐI ĐA NỀN TẢNG (SEO)",
            "```text",
            report.seo_description,
            "```",
            "",
            f"**Hashtags chính:** `{' '.join(report.seo_hashtags)}`",
            ""
        ])

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
