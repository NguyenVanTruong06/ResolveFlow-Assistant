import os
import pytest
from src.core.marketing_engine import (
    MarketingPsychologyScorer,
    MarketingViralPackGenerator,
    HookAngleAnalysis,
    ViralPackReport
)
from src.core import story_arranger as sa


def test_marketing_scorer_loss_aversion():
    text = "Đừng bao giờ mắc sai lầm này khi edit video"
    res = MarketingPsychologyScorer.analyze_hook_sentence(text)
    assert res is not None
    assert res.angle_type == "loss_aversion"
    assert res.confidence_score >= 9.0
    assert "ĐỪNG MẮC PHẢI SAI LẦM" in res.recommended_text_overlay


def test_marketing_scorer_curiosity():
    text = "Bí mật đằng sau kênh YouTube 1 triệu subs ít ai biết"
    res = MarketingPsychologyScorer.analyze_hook_sentence(text)
    assert res is not None
    assert res.angle_type == "curiosity"
    assert "SỰ THẬT" in res.recommended_text_overlay


def test_marketing_scorer_transformation():
    text = "Kỹ thuật giúp bạn thay đổi hoàn toàn chất lượng video"
    res = MarketingPsychologyScorer.analyze_hook_sentence(text)
    assert res is not None
    assert res.angle_type == "transformation"
    assert res.confidence_score >= 8.5


def test_marketing_scorer_question():
    text = "Bạn có biết cách DaVinci Resolve tự động cắt video?"
    res = MarketingPsychologyScorer.analyze_hook_sentence(text)
    assert res is not None
    assert res.angle_type == "question"
    assert res.confidence_score >= 8.0


def test_marketing_scorer_neutral_sentence():
    text = "Hôm nay trời nhiều mây và có gió nhẹ"
    res = MarketingPsychologyScorer.analyze_hook_sentence(text)
    assert res is None


def test_viral_pack_generator_output(tmp_path):
    subtitles = [
        {"start": 0.0, "end": 4.0, "text": "Đừng bao giờ dựng video theo cách cũ này!"},
        {"start": 5.0, "end": 9.0, "text": "Hôm nay mình sẽ chỉ cho các bạn bí mật để tăng tốc."},
        {"start": 10.0, "end": 15.0, "text": "Sau 1 tháng áp dụng kết quả sẽ làm bạn bất ngờ."},
        {"start": 16.0, "end": 20.0, "text": "Hãy like và follow kênh nhé."}
    ]

    report = MarketingViralPackGenerator.generate_viral_pack(
        project_name="Vlog_DuLich_DaLat",
        subtitles=subtitles,
        video_duration=20.0
    )

    assert isinstance(report, ViralPackReport)
    assert len(report.top_headlines) == 5
    assert len(report.best_hooks) >= 1
    assert len(report.cta_recommendations) >= 3
    assert len(report.seo_hashtags) > 0

    # Test Markdown export
    out_md = os.path.join(tmp_path, "test_viral_pack.md")
    MarketingViralPackGenerator.export_markdown_report(report, out_md)
    assert os.path.exists(out_md)

    content = open(out_md, "r", encoding="utf-8").read()
    assert "BẢNG 5 TIÊU ĐỀ GIẬT TÍT" in content
    assert "CALL-TO-ACTION" in content
    assert "HASHTAGS" in content


def test_marketing_intents_in_story_arranger():
    """Kiểm tra các intent mới trong story_arranger: aida, pas, open_loop."""
    import numpy as np
    events = [{"video_path": "test.mp4", "src_in": 0.0, "src_out": 120.0, "rec_in": 0.0,
               "rec_out": 120.0, "fps": 30, "speed": 1.0, "punch_in": False}]
    subs = [
        {"start": 1.0, "end": 5.0, "text": "Chào mừng các bạn đến với kênh"},
        {"start": 10.0, "end": 16.0, "text": "Nếu bạn đang gặp rắc rối khi edit video"},
        {"start": 30.0, "end": 35.0, "text": "Đây chính là giải pháp đỉnh cao nhất"},
        {"start": 60.0, "end": 65.0, "text": "Bí mật mà chưa ai từng chia sẻ"},
        {"start": 110.0, "end": 118.0, "text": "Hãy bấm đăng ký kênh ngay hôm nay"}
    ]
    energy = {"test.mp4": np.full(120, -30.0)}
    activity = {"test.mp4": np.full(120, 0.2)}

    blocks = sa.build_blocks(events, subs, energy, activity, chapter_max=0)
    blocks = sa.tag_roles(blocks)

    # 1. Test AIDA
    arr_aida = sa.arrange(blocks, "aida", target_seconds=60.0)
    assert arr_aida.total_seconds <= 60.0 + 1e-6
    assert len(arr_aida.items) > 0

    # 2. Test PAS
    arr_pas = sa.arrange(blocks, "pas", target_seconds=60.0)
    assert arr_pas.total_seconds <= 60.0 + 1e-6
    assert len(arr_pas.items) > 0

    # 3. Test Open-Loop
    arr_open_loop = sa.arrange(blocks, "open_loop", target_seconds=60.0)
    assert arr_open_loop.total_seconds <= 60.0 + 1e-6
    assert len(arr_open_loop.items) > 0
    # First item should be a copy teaser or hook
    assert arr_open_loop.items[0].is_copy or arr_open_loop.items[0].role in ("hook", "climax", "intro")
