"""
tests/core/test_resolve_bridge_text_insert.py
Kiểm thử TDD cho resolve_bridge.insert_title_at_playhead (G04-04C11-I):
1. Injected resolve context: sử dụng resolve được truyền vào từ Gateway task, không gọi global get_resolve().
2. Abort trước khi append nếu project hoặc timeline bị thay đổi.
3. Ánh xạ đúng các trường cơ bản vào TitleParams.
4. Dọn dẹp carrier clip khi ImportFusionComp thất bại (tl.DeleteClips).
5. Báo lỗi trung thực và danh tính clip dở dang nếu việc dọn dẹp carrier thất bại.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.core.resolve_bridge import insert_title_at_playhead, ResolveFlowError, Ctx
from src.core.fusion.titles import TITLES


class MockTimelineItem:
    def __init__(self, name="Carrier", start=0, duration=72, uid="item_uuid_1"):
        self._name = name
        self._start = start
        self._duration = duration
        self._uid = uid
        self._clip_color = ""
        self._fusion_comps = ["Composition 1"]
        self._active_comp = MagicMock()
        self._deleted = False

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name

    def GetUniqueId(self):
        return self._uid

    def GetStart(self):
        return self._start

    def GetDuration(self):
        return self._duration

    def SetClipColor(self, color):
        self._clip_color = color

    def ImportFusionComp(self, path):
        return self._active_comp

    def GetFusionCompNameList(self):
        return list(self._fusion_comps)

    def LoadFusionCompByName(self, name):
        return True

    def GetFusionCompByIndex(self, idx):
        return self._active_comp

    def DeleteFusionCompByName(self, name):
        if name in self._fusion_comps:
            self._fusion_comps.remove(name)


class MockTimeline:
    def __init__(self, name="Timeline 1", uid="tl_uuid_1"):
        self._name = name
        self._uid = uid
        self.deleted_clips = []
        self.delete_clips_result = True
        self.appended = []

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetSetting(self, key):
        if key == "timelineResolutionWidth":
            return "1920"
        if key == "timelineResolutionHeight":
            return "1080"
        if key == "timelineFrameRate":
            return "24.0"
        return None

    def GetCurrentTimecode(self):
        return "01:00:00:00"

    def GetCurrentVideoItem(self):
        return None

    def GetItemListInTrack(self, track_type, track_index):
        return []

    def GetTrackCount(self, track_type):
        return 2

    def GetIsTrackLocked(self, track_type, track_index):
        return False

    def DeleteClips(self, items, ripple=False):
        self.deleted_clips.extend(items)
        return self.delete_clips_result


class MockMediaPool:
    def __init__(self, item_to_return=None):
        self._item = item_to_return or MockTimelineItem()
        self.appended_calls = []

    def AppendToTimeline(self, clip_infos):
        self.appended_calls.append(clip_infos)
        return [self._item]


class MockProject:
    def __init__(self, timeline=None, name="Project 1", uid="proj_uuid_1"):
        self._timeline = timeline or MockTimeline()
        self._media_pool = MockMediaPool()
        self._name = name
        self._uid = uid

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetCurrentTimeline(self):
        return self._timeline

    def GetMediaPool(self):
        return self._media_pool


class MockProjectManager:
    def __init__(self, project=None):
        self._project = project or MockProject()

    def GetCurrentProject(self):
        return self._project


class MockResolve:
    def __init__(self, project=None):
        self._pm = MockProjectManager(project)
        self.pages_opened = []

    def GetProjectManager(self):
        return self._pm

    def OpenPage(self, page_name):
        self.pages_opened.append(page_name)

    def Fusion(self):
        return None


def test_insert_title_at_playhead_uses_injected_resolve(monkeypatch, tmp_path):
    """1. Injected resolve context: Bridge sử dụng resolve được truyền vào, không gọi get_resolve."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    mock_comp = MagicMock()
    item.ImportFusionComp = MagicMock(return_value=mock_comp)
    mock_proj.GetMediaPool()._item = item

    get_resolve_called = False
    def fake_get_resolve():
        nonlocal get_resolve_called
        get_resolve_called = True
        raise RuntimeError("get_resolve should not be called when resolve is injected")

    monkeypatch.setattr("src.core.resolve_bridge.get_resolve", fake_get_resolve)
    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    res_item = insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)
    assert not get_resolve_called
    assert res_item == item
    assert mock_res.pages_opened == ["edit"]


def test_insert_title_aborts_if_project_or_timeline_changed_before_append(monkeypatch, tmp_path):
    """2. Abort trước khi Append nếu project hoặc timeline bị thay đổi."""
    mock_timeline = MockTimeline(name="Timeline Initial")
    mock_proj = MockProject(timeline=mock_timeline, name="Project Initial")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    # Đổi timeline trước khi Append
    original_get_carrier = lambda ctx, dur, w, h: MagicMock()
    def switch_timeline_hook(ctx, dur, w, h):
        # Giả lập người dùng chuyển timeline khác giữa chừng
        mock_proj._timeline = MockTimeline(name="Timeline Changed")
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", switch_timeline_hook)

    with pytest.raises(ResolveFlowError, match="Timeline.*thay đổi"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)


def test_insert_title_carrier_cleanup_on_import_failure(monkeypatch, tmp_path):
    """3. ImportFusionComp trả về None -> tl.DeleteClips dọn dẹp carrier và ném ResolveFlowError."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=None)
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match="ImportFusionComp failed"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert item in mock_timeline.deleted_clips


def test_insert_title_carrier_cleanup_failure_reports_leftover(monkeypatch, tmp_path):
    """4. ImportFusionComp thất bại VÀ DeleteClips thất bại -> báo lỗi rõ ràng về clip tồn dư."""
    mock_timeline = MockTimeline()
    mock_timeline.delete_clips_result = False  # Xóa thất bại
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=None)
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match=r"carrier.*thất bại"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)


def test_insert_title_aborts_if_timeline_uid_changed_even_with_same_name(monkeypatch, tmp_path):
    """5. Same-name timeline UID swap trước khi Append: abort với 0 Append."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="tl_orig_123")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    def switch_timeline_same_name_hook(ctx, dur, w, h):
        # Timeline mới có cùng tên "Timeline 1" nhưng khác UID
        mock_proj._timeline = MockTimeline(name="Timeline 1", uid="tl_swapped_456")
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", switch_timeline_same_name_hook)

    with pytest.raises(ResolveFlowError, match=r"(Timeline.*thay đổi|Timeline UID)"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert len(mock_proj.GetMediaPool().appended_calls) == 0


def test_insert_title_aborts_if_project_uid_changed_even_with_same_name(monkeypatch, tmp_path):
    """6. Same-name project UID swap trước khi Append: abort với 0 Append."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="tl_123")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_orig_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    def switch_project_same_name_hook(ctx, dur, w, h):
        # Project mới có cùng tên "Project 1" nhưng khác UID
        swapped_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_swapped_456")
        mock_res.GetProjectManager()._project = swapped_proj
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", switch_project_same_name_hook)

    with pytest.raises(ResolveFlowError, match=r"(Project.*thay đổi|Project UID)"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert len(mock_proj.GetMediaPool().appended_calls) == 0


def test_insert_title_aborts_if_expected_context_swapped(monkeypatch, tmp_path):
    """7. Expected context đối chiếu giữa task và bridge: abort nếu expected timeline ID không khớp."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="tl_actual_123")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match=r"(Timeline ID|Project ID|không khớp)"):
        insert_title_at_playhead(
            "spring_pop", {"text": "TEST"}, resolve=mock_res,
            expected_timeline_id="tl_expected_different"
        )


def test_insert_title_carrier_cleanup_on_load_fusion_comp_false(monkeypatch, tmp_path):
    """8. LoadFusionCompByName trả về False -> DeleteClips carrier và báo lỗi."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=MagicMock())
    item.GetFusionCompNameList = MagicMock(return_value=["Composition 1"])
    item.LoadFusionCompByName = MagicMock(return_value=False)
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match="LoadFusionCompByName.*thất bại"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert item in mock_timeline.deleted_clips


def test_insert_title_carrier_cleanup_on_empty_comp_names(monkeypatch, tmp_path):
    """9. GetFusionCompNameList rỗng -> DeleteClips carrier và báo lỗi."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=MagicMock())
    item.GetFusionCompNameList = MagicMock(return_value=[])
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match="GetFusionCompNameList rỗng"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert item in mock_timeline.deleted_clips


def test_insert_title_carrier_cleanup_on_active_comp_none(monkeypatch, tmp_path):
    """10. GetFusionCompByIndex(1) trả về None -> DeleteClips carrier và báo lỗi."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=MagicMock())
    item.GetFusionCompNameList = MagicMock(return_value=["Composition 1"])
    item.LoadFusionCompByName = MagicMock(return_value=True)
    item.GetFusionCompByIndex = MagicMock(return_value=None)
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match="GetFusionCompByIndex.*không trả về comp"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert item in mock_timeline.deleted_clips


def test_insert_title_carrier_cleanup_on_media_out_connect_failure(monkeypatch, tmp_path):
    """11. Xác minh MediaOut thất bại -> DeleteClips carrier và báo lỗi."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    item = MockTimelineItem()
    item.ImportFusionComp = MagicMock(return_value=MagicMock())
    item.GetFusionCompNameList = MagicMock(return_value=["Composition 1"])
    item.LoadFusionCompByName = MagicMock(return_value=True)
    mock_proj.GetMediaPool()._item = item

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)
    monkeypatch.setattr("src.core.resolve_bridge._ensure_media_out_connected", lambda comp: False)

    with pytest.raises(ResolveFlowError, match="MediaOut"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert item in mock_timeline.deleted_clips


def test_insert_title_aborts_if_initial_timeline_uid_disappears_before_append(monkeypatch, tmp_path):
    """12. UID ban đầu có thật nhưng biến mất (trở thành rỗng) trước Append -> abort 0 Append."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="tl_valid_123")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    def uid_lost_hook(ctx, dur, w, h):
        # Timeline hiện tại mất UID (trả về rỗng)
        mock_proj._timeline = MockTimeline(name="Timeline 1", uid="")
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", uid_lost_hook)

    with pytest.raises(ResolveFlowError, match=r"(Timeline UID.*(thay đổi|bị mất)|không khớp)"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert len(mock_proj.GetMediaPool().appended_calls) == 0


def test_insert_title_aborts_if_initial_project_uid_disappears_before_append(monkeypatch, tmp_path):
    """13. Project UID ban đầu có thật nhưng biến mất trước Append -> abort 0 Append."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="tl_123")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_valid_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    def proj_uid_lost_hook(ctx, dur, w, h):
        # Project hiện tại mất UID (trả về rỗng)
        swapped_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="")
        mock_res.GetProjectManager()._project = swapped_proj
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", proj_uid_lost_hook)

    with pytest.raises(ResolveFlowError, match=r"(Project UID.*(thay đổi|bị mất)|không khớp)"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert len(mock_proj.GetMediaPool().appended_calls) == 0


def test_insert_title_aborts_if_expected_id_present_but_initial_uid_empty(monkeypatch, tmp_path):
    """14. Expected ID được giao nhưng đối tượng Resolve hiện tại không có UID (rỗng) -> abort ngay."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="proj_123")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    with pytest.raises(ResolveFlowError, match=r"(Timeline ID|không khớp)"):
        insert_title_at_playhead(
            "spring_pop", {"text": "TEST"}, resolve=mock_res,
            expected_timeline_id="tl_expected_123"
        )


def test_insert_title_aborts_if_both_missing_uid_but_project_reference_changed(monkeypatch, tmp_path):
    """15. Cả hai không có UID nhưng Project đối tượng bị đổi -> fail-closed abort."""
    mock_timeline = MockTimeline(name="Timeline 1", uid="")
    mock_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="")
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 100)

    def switch_proj_no_uid(ctx, dur, w, h):
        # Project khác instance cùng không có UID
        swapped_proj = MockProject(timeline=mock_timeline, name="Project 1", uid="")
        mock_res.GetProjectManager()._project = swapped_proj
        return MagicMock()

    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", switch_proj_no_uid)

    with pytest.raises(ResolveFlowError, match="Project.*thiếu Unique ID"):
        insert_title_at_playhead("spring_pop", {"text": "TEST"}, resolve=mock_res)

    assert len(mock_proj.GetMediaPool().appended_calls) == 0


def test_get_safe_uid_rejects_non_str_and_empty():
    """16. _get_safe_uid chỉ chấp nhận non-empty str thực; reject bool, int, list, dict, whitespace."""
    from src.core.resolve_bridge import _get_safe_uid

    class ObjWithUID:
        def __init__(self, val):
            self._val = val
        def GetUniqueId(self):
            if isinstance(self._val, Exception):
                raise self._val
            return self._val

    assert _get_safe_uid(None) == ""
    assert _get_safe_uid(ObjWithUID(False)) == ""
    assert _get_safe_uid(ObjWithUID(True)) == ""
    assert _get_safe_uid(ObjWithUID(12345)) == ""
    assert _get_safe_uid(ObjWithUID(["uid"])) == ""
    assert _get_safe_uid(ObjWithUID({"uid": "1"})) == ""
    assert _get_safe_uid(ObjWithUID("   ")) == ""
    assert _get_safe_uid(ObjWithUID(RuntimeError("COM error"))) == ""
    assert _get_safe_uid(ObjWithUID("valid_uid_123")) == "valid_uid_123"


@pytest.mark.parametrize("dur_input,expected_dur", [
    (1, 1),
    (72, 72),
    (120, 120),
    (48, 48),
])
def test_insert_title_append_bounds_end_exclusive(dur_input, expected_dur, tmp_path, monkeypatch):
    """17. G04-04C11-FINAL-R1: AppendToTimeline endFrame là exclusive (= dur), đảm bảo đúng số frame yêu cầu."""
    mock_timeline = MockTimeline()
    mock_proj = MockProject(timeline=mock_timeline)
    mock_res = MockResolve(project=mock_proj)

    monkeypatch.setattr("src.core.resolve_bridge.cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr("src.core.resolve_bridge.export_title_comp", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.core.resolve_bridge.playhead_frame", lambda tl: 86400)
    monkeypatch.setattr("src.core.resolve_bridge.get_carrier_clip", lambda ctx, dur, w, h: MagicMock())
    monkeypatch.setattr("src.core.resolve_bridge._ensure_media_out_connected", lambda comp: True)

    item = insert_title_at_playhead(
        "spring_pop",
        {"text": "Bounds Test", "duration_frames": dur_input},
        resolve=mock_res,
    )
    assert item is not None
    appended = mock_proj.GetMediaPool().appended_calls
    assert len(appended) == 1
    clip_info = appended[0][0]
    assert clip_info["startFrame"] == 0
    assert clip_info["endFrame"] == expected_dur



