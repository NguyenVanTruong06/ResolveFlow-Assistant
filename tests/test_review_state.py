import pytest
from src.core.ai_director import ProposedSegment
from src.core.review_state import ReviewState
from src.core import story_planner


def _segs():
    return [
        ProposedSegment(id=0, start=0, end=4, text="a", decision="keep", confidence=0.9, reason="ok", approved=True),
        ProposedSegment(id=1, start=4, end=6, text="b", decision="cut", confidence=1.0, reason="vấp", approved=False),
        ProposedSegment(id=2, start=6, end=10, text="c", decision="keep", confidence=0.5, reason="ok", approved=True),
    ]


def test_bulk_undo_redo():
    st = ReviewState(_segs())
    assert st.cut_all() and not any(s.approved for s in st.segments)
    assert st.undo() == [0, 2]
    assert [s.approved for s in st.segments] == [True, False, True]
    assert st.redo() == [0, 2]
    assert not any(s.approved for s in st.segments)


def test_noop_not_recorded_and_new_change_clears_redo():
    st = ReviewState(_segs())
    assert st.set_approved([0], True) is False and not st.can_undo
    st.toggle([0]); st.undo()
    assert st.can_redo
    st.toggle([1])
    assert not st.can_redo


def test_restore_ai_suggestion_and_attention():
    st = ReviewState(_segs())
    st.keep_all()
    st.restore_ai_suggestion()
    assert [s.approved for s in st.segments] == [True, False, True]
    assert st.needs_attention(0.7) == [1, 2]


def test_summary():
    s = ReviewState(_segs()).summary()
    assert s["kept_count"] == 2 and s["total_count"] == 3
    assert s["kept_seconds"] == 8 and s["total_seconds"] == 10 and s["saved_percent"] == 20.0


def test_pacing_presets_monotonic_and_fallback():
    r, b, f = (story_planner.get_pacing(k) for k in ("relaxed", "balanced", "fast"))
    assert r["min_cut_gap"] > b["min_cut_gap"] > f["min_cut_gap"]
    assert r["max_static_shot"] > b["max_static_shot"] > f["max_static_shot"]
    assert story_planner.get_pacing("nope") == b
