from src.core import story_planner as sp
from src.core.ai_director import AIDirector, AIDirectorConfig
from src.core.autocut import CutSegment


def _subs():
    return [
        {"start": 0.0, "end": 3.0, "text": "ừm à"},
        {"start": 3.0, "end": 8.0, "text": "Sự thật quan trọng nhất về dựng video là gì?"},
        {"start": 8.0, "end": 14.0, "text": "Đây là phần giải thích khá dài dòng không có gì đặc biệt cả"},
        {"start": 14.0, "end": 20.0, "text": "Bí quyết là luôn giữ nhịp cắt 3 giây một lần."},
        {"start": 20.0, "end": 26.0, "text": "Tóm lại hãy đăng ký kênh để xem thêm nhé."},
    ]


def test_score_prefers_emphasis_over_filler():
    subs = _subs()
    assert sp.score_subtitle(subs[1]) > sp.score_subtitle(subs[0])
    assert sp.score_subtitle(subs[3]) > sp.score_subtitle(subs[2])


def test_plan_viral_picks_hook_and_payoff_in_order():
    selected, markers, roles = sp.plan_viral(_subs(), target=17.0)
    starts = [s["start"] for s in selected]
    assert starts == sorted(starts)
    assert sum(s["end"] - s["start"] for s in selected) <= 17.0
    assert 0.0 not in starts  # câu toàn từ đệm không được chọn làm hook
    assert "hook" in roles.values() and "payoff" in roles.values()
    assert {m["name"] for m in markers} >= {"🔥 Hook", "🎯 Payoff"}


def test_plan_summary_covers_intro_and_outro():
    subs = [{"start": i * 10.0, "end": i * 10.0 + 8.0, "text": f"Câu số {i} nói về nội dung dựng video chuyên nghiệp"}
            for i in range(20)]
    selected, _, roles = sp.plan_summary(subs, target=60.0)
    starts = [s["start"] for s in selected]
    assert starts[0] < 30.0 and starts[-1] > 150.0
    assert sum(s["end"] - s["start"] for s in selected) <= 60.0


def test_bridge_tiny_cuts_respects_protected():
    segs = [CutSegment(start=0, end=2, action="keep"), CutSegment(start=2, end=2.2, action="cut"),
            CutSegment(start=2.2, end=4, action="keep"), CutSegment(start=4, end=4.2, action="cut"),
            CutSegment(start=4.2, end=6, action="keep")]
    out = sp.bridge_tiny_cuts(segs, [(4.0, 4.2)], 0.35)
    assert [s.action for s in out] == ["keep", "keep", "keep", "cut", "keep"]


def test_split_long_takes_at_sentence_boundary():
    subs = [{"start": 0, "end": 9, "text": "a"}, {"start": 10, "end": 20, "text": "b"}]
    out, n = sp.split_long_takes([CutSegment(start=0, end=20, action="keep")], subs, 12.0)
    assert n == 1 and len(out) == 2 and abs(out[0].end - 9.5) < 1e-6


def test_director_pacing_stats_and_min_punch():
    director = AIDirector(AIDirectorConfig(mode="clean_talk", remove_bad_takes=False))
    subs = [{"start": 1.0, "end": 9.0, "text": "Câu một nói khá dài về nội dung"},
            {"start": 9.5, "end": 20.0, "text": "Câu hai cũng dài không kém cạnh gì câu một"}]
    res = director.process_semantic_cut(subs, [(0.7, 20.3)], 25.0)
    assert res["stats"]["long_takes_split"] >= 1
    assert res["stats"]["avg_shot_length"] > 0
    assert any(s.punch_in for s in res["segments"])


def test_drop_orphan_slivers_keeps_speech():
    subs = [{"start": 5.0, "end": 8.0, "text": "x"}]
    segs = [CutSegment(start=0.85, end=1.0, action="keep"), CutSegment(start=1.0, end=2.0, action="cut"),
            CutSegment(start=4.9, end=5.1, action="keep"), CutSegment(start=5.1, end=8.0, action="keep")]
    out = sp.drop_orphan_slivers(segs, subs, min_keep=0.3)
    assert out[0].action == "cut"        # mảnh đệm không có thoại
    assert out[2].action == "keep"       # mảnh ngắn nhưng chạm lời thoại thì giữ


def test_filler_only_sentence_is_cut_with_audio():
    words = [{"word": "ừm", "start": 8.0, "end": 8.5, "probability": 0.9},
             {"word": "à", "start": 8.5, "end": 9.0, "probability": 0.9}]
    subs = [{"start": 3.0, "end": 7.0, "text": "Xin chào các bạn đến với video này"},
            {"start": 8.0, "end": 9.0, "text": "ừm à", "words": words}]
    d = AIDirector(AIDirectorConfig(mode="clean_talk", remove_bad_takes=False, remove_repeated_phrases=False))
    res = d.process_semantic_cut(subs, [(2.7, 9.3)], 12.0)
    assert len(res["subtitles"]) == 1
    assert all(not (s.start < 9.0 and s.end > 8.0 and s.action == "keep") for s in res["segments"])
