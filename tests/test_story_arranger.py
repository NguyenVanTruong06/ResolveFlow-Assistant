import numpy as np
import pytest
from src.core import story_arranger as sa


def _events(total=300.0, video="a.mp4", offset=0.0, rec0=0.0):
    return [{"video_path": video, "src_in": offset, "src_out": offset + total, "rec_in": rec0,
             "rec_out": rec0 + total, "fps": 30, "speed": 1.0, "punch_in": False}]


def _subs():
    # Lời thoại có khoảng nghỉ rõ ràng; câu hook ở ~150s, câu chốt ở cuối
    rows = [(1, 8, "Chào mừng các bạn đến với vlog hôm nay"), (20, 30, "Đi dạo quanh phố cổ buổi sáng"),
            (60, 70, "Quán này đông khách lắm"), (100, 110, "Mình đang đi tiếp"),
            (150, 155, "Wow nhìn này, không thể tin được!"), (200, 210, "Cảnh ở đây rất đẹp"),
            (290, 298, "Tóm lại hãy đăng ký kênh nhé")]
    return [{"start": a, "end": b, "text": t} for a, b, t in rows]


def _signals(total=300):
    energy = np.full(total, -40.0)
    energy[150:153] = -12.0
    activity = np.full(total, 0.03)
    activity[148:158] = 0.45
    return {"a.mp4": energy}, {"a.mp4": activity}


def _blocks():
    e, a = _signals()
    blocks = sa.build_blocks(_events(), _subs(), e, a, chapter_max=0)
    return sa.tag_roles(blocks)


def test_blocks_cover_timeline_without_gaps():
    blocks = _blocks()
    assert blocks[0].t0 == 0.0 and blocks[-1].t1 == 300.0
    assert all(abs(x.t1 - y.t0) < 1e-6 for x, y in zip(blocks, blocks[1:]))
    assert all(3.0 <= b.duration <= 28.0 for b in blocks)


def test_roles_intro_outro_hook_climax():
    blocks = _blocks()
    by = {r: [b for b in blocks if b.role == r] for r in ("intro", "outro", "hook", "climax")}
    assert blocks[0].role == "intro" and blocks[-1].role == "outro"
    assert len(by["hook"]) == 1 and by["hook"][0].t0 <= 155 <= by["hook"][0].t1
    assert len(by["climax"]) == 1


def test_cold_open_copies_hook_and_keeps_rest():
    blocks = _blocks()
    arr = sa.arrange(blocks, "cold_open", cold_open_len=6.0)
    assert arr.changed and arr.items[0].is_copy and arr.items[0].t1 - arr.items[0].t0 <= 6.0
    assert [i.block_id for i in arr.items[1:]] == [b.id for b in blocks]
    assert abs(arr.total_seconds - (300.0 + (arr.items[0].t1 - arr.items[0].t0))) < 1e-6


def test_rising_action_orders_scenes_by_intensity_keeping_intro_outro():
    blocks = _blocks()
    for b in blocks:                      # dựng 3 "cảnh" giả: đầu / giữa (cường độ cao) / cuối
        b.scene = 0 if b.t0 < 100 else (1 if b.t0 < 200 else 2)
    for b in blocks:
        b.intensity = 0.9 if b.scene == 0 else (0.1 if b.scene == 1 else 0.5)
    blocks[0].role, blocks[-1].role = "intro", "outro"
    arr = sa.arrange(blocks, "rising_action")
    ids = [i.block_id for i in arr.items]
    assert ids[0] == blocks[0].id and ids[-1] == blocks[-1].id
    scenes = [next(b.scene for b in blocks if b.id == i) for i in ids[1:-1]]
    assert scenes == sorted(scenes, key=lambda s: {1: 0, 2: 1, 0: 2}[s])   # 0.1 -> 0.5 -> 0.9, tăng dần
    assert sorted(ids) == sorted(b.id for b in blocks)                     # không mất/không trùng khối


def test_shorts_formula_budget_and_order():
    blocks = _blocks()
    arr = sa.arrange(blocks, "shorts", target_seconds=40.0)
    assert arr.total_seconds <= 40.0 + 1e-6
    assert arr.items[0].role == "hook" and arr.items[-1].role == "outro"
    middle = [i.t0 for i in arr.items[1:-1]]
    assert middle == sorted(middle)


def test_keep_intent_is_identity():
    blocks = _blocks()
    arr = sa.arrange(blocks, "keep")
    assert not arr.changed and len(arr.items) == len(blocks)


def test_apply_arrangement_shifts_subtitles_markers_and_copies():
    blocks = _blocks()
    arr = sa.arrange(blocks, "cold_open", cold_open_len=6.0)
    subs = _subs()
    marks = [{"time": 152.0, "duration": 1.0, "name": "x", "color": "Red"}]
    ev, su, mk = sa.apply_arrangement(_events(), subs, marks, arr)
    assert all(abs(a["rec_out"] - b["rec_in"]) < 1e-3 for a, b in zip(ev, ev[1:]))
    assert abs(ev[-1]["rec_out"] - arr.total_seconds) < 1e-3
    hook_texts = [s for s in su if "Wow" in s["text"]]
    assert len(hook_texts) == 2 and hook_texts[0]["start"] < 6.0          # bản chép ở đầu + bản gốc
    assert sum(1 for m in mk if m["name"] == "x") == 2                     # marker cũng được chép theo
    assert mk[0]["name"].startswith("🧭")                                   # marker vai trò ở đầu mỗi mục


def test_slice_events_respects_speed():
    ev = [{"video_path": "a", "src_in": 0.0, "src_out": 40.0, "rec_in": 0.0, "rec_out": 10.0, "speed": 4.0}]
    out = sa.slice_events(ev, 5.0, 10.0)
    assert out[0]["src_in"] == 20.0 and out[0]["src_out"] == 40.0


def test_multi_clip_blocks_start_new_scene_per_clip():
    e1 = _events(100, "a.mp4")[0]
    e2 = _events(100, "b.mp4", rec0=100.0)[0]
    subs = [{"start": 5, "end": 10, "text": "Clip một nói chuyện"}, {"start": 105, "end": 110, "text": "Clip hai nói chuyện"}]
    blocks = sa.build_blocks([e1, e2], subs, chapter_max=0)
    assert len({b.scene for b in blocks}) == 2


class FakeSelector:
    def __init__(self, data):
        self.data = data

    def complete_json(self, prompt):
        return self.data


def test_llm_roles_valid_and_invalid():
    blocks = _blocks()
    n = len(blocks)
    good = {"roles": {"0": "intro", str(n - 1): "outro", "3": "hook"}, "reasoning": "ok"}
    roles, why = sa.llm_assign_roles(blocks, "cold_open", "k", selector=FakeSelector(good))
    assert roles[3] == "hook" and why == "ok"
    sa.tag_roles(blocks, roles)
    assert blocks[3].role == "hook" and blocks[3].reason.startswith("LLM")
    with pytest.raises(ValueError):
        sa.llm_assign_roles(blocks, "cold_open", "k", selector=FakeSelector({"roles": {"0": "ông_vua"}}))
    with pytest.raises(ValueError):
        sa.llm_assign_roles(blocks, "cold_open", "k", selector=FakeSelector({"roles": {"999": "hook"}}))
    with pytest.raises(ValueError):
        sa.llm_assign_roles(blocks, "cold_open", "k", selector=FakeSelector({}))


def test_story_review_state_move_and_toggle():
    from src.core.review_state import StoryReviewState
    blocks = _blocks()
    arr = sa.arrange(blocks, "shorts", target_seconds=60.0)
    state = StoryReviewState(arr, blocks, target_seconds=60.0)

    # Initial summary
    sm = state.summary()
    assert sm["enabled_count"] == len(arr.items)
    assert sm["total_seconds"] == round(arr.total_seconds, 2)

    # Move down item 0
    orig_first_id = state.items[0].block_id
    orig_second_id = state.items[1].block_id
    assert state.move_down(0)
    assert state.items[0].block_id == orig_second_id
    assert state.items[1].block_id == orig_first_id

    # Undo move
    assert state.can_undo
    assert state.undo()
    assert state.items[0].block_id == orig_first_id

    # Redo move
    assert state.can_redo
    assert state.redo()
    assert state.items[0].block_id == orig_second_id

    # Move up back
    assert state.move_up(1)
    assert state.items[0].block_id == orig_first_id

    # Toggle disable item 0
    assert state.toggle_enabled(0)
    assert state.items[0].enabled is False
    sm2 = state.summary()
    assert sm2["enabled_count"] == len(arr.items) - 1
    assert sm2["total_seconds"] < sm["total_seconds"]

    # Restore AI plan
    assert state.restore_ai_plan()
    assert state.items[0].enabled is True
    assert state.items[0].block_id == orig_first_id


def test_apply_arrangement_skips_disabled_items():
    blocks = _blocks()
    arr = sa.arrange(blocks, "cold_open", cold_open_len=6.0)
    # Disable hook copy item
    arr.items[0].enabled = False
    subs = _subs()
    marks = [{"time": 152.0, "duration": 1.0, "name": "x", "color": "Red"}]
    ev, su, mk = sa.apply_arrangement(_events(), subs, marks, arr)
    # Hook copy should be skipped, timeline total matches original blocks
    assert abs(ev[-1]["rec_out"] - 300.0) < 1e-3

