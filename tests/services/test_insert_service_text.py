"""
tests/services/test_insert_service_text.py
Kiểm thử TDD cho xử lý Text / Title trong InsertService qua ResolveGateway (G04-04C11-I):
1. Procedural Text: submit task vào Gateway 1 lần, gọi resolve_bridge với bound resolve.
2. Filebacked .setting: từ chối trung thực, 0 submit vào Gateway, callback trả lỗi rõ.
3. InsertResult: đầy đủ 10 trường schema, item_ref chứa identity envelope thuần primitive.
4. Duration quantization: tính theo FPS thực tế trên thread, tối thiểu 1 frame, không gán 24 frames tùy tiện.
5. Title-specific Undo: xác minh exact project, timeline và clip identity trước khi xóa; từ chối an toàn nếu sai project hoặc clip ambiguous.
"""
import time
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QCoreApplication

from src.services.resolve_gateway import ResolveGateway
from src.services.insert_service import InsertService, InsertResult
from src.core.text_preset import TextStylePreset


class MockTimelineItem:
    def __init__(self, name: str, start: int = 100, duration: int = 72, unique_id: str = "item_uuid_1"):
        self._name = name
        self._start = start
        self._duration = duration
        self._unique_id = unique_id

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetDuration(self):
        return self._duration

    def GetUniqueId(self):
        return self._unique_id


class MockTimeline:
    def __init__(self, name="Timeline 1", uid="tl_uuid_1"):
        self._name = name
        self._uid = uid
        self.tracks_video = {
            1: [],
            2: [],
            3: [],
        }
        self.deleted_clips = []

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetCurrentTimecode(self):
        return "01:00:00:00"

    def GetSetting(self, key):
        if key == "timelineResolutionWidth":
            return "1920"
        if key == "timelineResolutionHeight":
            return "1080"
        if key == "timelineFrameRate":
            return "24.0"
        return None

    def GetTrackCount(self, track_type="video"):
        return len(self.tracks_video)

    def GetItemListInTrack(self, track_type="video", index=2):
        return self.tracks_video.get(index, [])

    def DeleteClips(self, items, ripple=False):
        for it in items:
            self.deleted_clips.append(it)
            for tr_idx in self.tracks_video:
                if it in self.tracks_video[tr_idx]:
                    self.tracks_video[tr_idx].remove(it)
        return True


class MockProject:
    def __init__(self, timeline=None, name="Project 1", uid="proj_uuid_1"):
        self._timeline = timeline or MockTimeline()
        self._name = name
        self._uid = uid

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid

    def GetCurrentTimeline(self):
        return self._timeline


class MockProjectManager:
    def __init__(self, project=None):
        self._project = project or MockProject()

    def GetCurrentProject(self):
        return self._project


class MockResolve:
    def __init__(self, project=None):
        self.project = project or MockProject()
        self.pm = MockProjectManager(self.project)

    def GetProjectManager(self):
        return self.pm

    def OpenPage(self, page):
        pass


class DirectMockGateway:
    """Gateway giả lập thực thi task đồng bộ trên worker mà không tạo thread thật."""
    def __init__(self, resolve=None):
        self._resolve = resolve or MockResolve()
        self.submitted_tasks = []

    def submit(self, fn, callback=None, errback=None):
        self.submitted_tasks.append(fn)
        try:
            res = fn(self._resolve)
            if callback:
                callback(res)
        except Exception as e:
            if errback:
                errback(e)
            else:
                raise

    def submit_sync(self, fn, timeout=4.0):
        return fn(self._resolve)


@pytest.fixture
def mock_gateway():
    return DirectMockGateway()


def test_insert_service_procedural_text_submits_gateway_task(mock_gateway, monkeypatch):
    """1. Procedural Text: submit task vào Gateway 1 lần, gọi resolve_bridge với bound resolve."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    created_item = MockTimelineItem(name="RF Spring Pop", start=120, duration=48)
    timeline.tracks_video[2].append(created_item)

    bridge_called = []
    def fake_bridge_insert(style, params=None, track_index=2, on_conflict="next_free", record_frame=None, clip_color="Orange", resolve=None, **kwargs):
        bridge_called.append({
            "style": style, "params": params, "track_index": track_index, "resolve": resolve, "kwargs": kwargs
        })
        return created_item

    monkeypatch.setattr("src.core.resolve_bridge.insert_title_at_playhead", fake_bridge_insert)
    monkeypatch.setattr("src.core.resolve_api.current_record_frame", lambda tl, proj: (120, "01:00:05:00"))
    monkeypatch.setattr("src.core.resolve_api._nominal_fps", lambda proj: (24.0, 24.0))

    asset = TextStylePreset(
        id="spring_pop",
        name="Spring Pop Title",
        font="Arial",
        size=60,
    )
    extra_params = {
        "text": "TIÊU ĐỀ TEST",
        "font": "Arial",
        "weight": "bold",
        "size": 60,
        "color": "#FFFFFF",
        "highlight_color": "#FFD400",
        "duration": 2.0,
        "preferred_track": 2,
    }

    results = []
    svc.insert_async(asset, extra_params=extra_params, callback=results.append)

    assert len(mock_gateway.submitted_tasks) == 1
    assert len(bridge_called) == 1
    call = bridge_called[0]
    assert call["resolve"] == mock_gateway._resolve
    assert call["params"]["text"] == "TIÊU ĐỀ TEST"
    assert call["params"]["style"] == "Bold"
    assert call["params"]["duration_frames"] == 48

    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    assert res.track == "V2"
    assert res.item_name == "RF Spring Pop"
    assert res.start_frame == 120
    assert res.duration_sec == 2.0
    assert res.error == ""


def test_insert_service_text_filebacked_setting_rejected_zero_gateway(mock_gateway, tmp_path):
    """2. Filebacked .setting: từ chối trung thực, 0 submit vào Gateway, callback nhận lỗi rõ."""
    svc = InsertService(gateway=mock_gateway)
    setting_file = tmp_path / "template.setting"
    setting_file.write_text("Tools = {}")
    asset = {
        "id": "setting_macro",
        "name": "Custom Macro Title",
        "category": "text",
        "file_path": str(setting_file),
        "template_type": "macro",
    }

    results = []
    svc.insert_async(asset, callback=results.append)

    assert len(mock_gateway.submitted_tasks) == 0
    assert len(results) == 1
    res = results[0]
    assert res.ok is False
    assert "chưa hỗ trợ chèn tự động" in res.error or ".setting" in res.error


def test_insert_service_text_result_10_fields_schema(mock_gateway, monkeypatch):
    """3. InsertResult: đầy đủ 10 trường schema, item_ref chứa identity envelope thuần primitive."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    created_item = MockTimelineItem(name="RF Glow", start=0, duration=24, unique_id="uid_glow_99")
    timeline.tracks_video[2].append(created_item)

    monkeypatch.setattr("src.core.resolve_bridge.insert_title_at_playhead", lambda *args, **kwargs: created_item)
    monkeypatch.setattr("src.core.resolve_api.current_record_frame", lambda tl, proj: (0, "01:00:00:00"))
    monkeypatch.setattr("src.core.resolve_api._nominal_fps", lambda proj: (24.0, 24.0))

    asset = TextStylePreset(id="neon_pulse", name="Neon Pulse", animation="glow")
    results = []
    svc.insert_async(asset, callback=results.append)

    assert len(results) == 1
    r = results[0]
    # Kiểm tra đủ 10 trường
    assert hasattr(r, "ok") and r.ok is True
    assert hasattr(r, "track") and r.track == "V2"
    assert hasattr(r, "start_tc") and r.start_tc == "01:00:00:00"
    assert hasattr(r, "start_frame") and r.start_frame == 0
    assert hasattr(r, "duration_sec") and r.duration_sec == 1.0
    assert hasattr(r, "item_name") and r.item_name == "RF Glow"
    assert hasattr(r, "item_ref") and isinstance(r.item_ref, dict)
    assert hasattr(r, "error") and r.error == ""
    assert hasattr(r, "inserted_at") and isinstance(r.inserted_at, float)
    assert hasattr(r, "message") and len(r.message) > 0

    envelope = r.item_ref
    assert envelope["kind"] == "title"
    assert envelope["project_name"] == "Project 1"
    assert envelope["timeline_name"] == "Timeline 1"
    assert envelope["start_frame"] == 0
    assert envelope["duration_frames"] == 24
    assert envelope["unique_id"] == "uid_glow_99"


def test_insert_service_text_duration_quantization_at_least_1_frame(mock_gateway, monkeypatch):
    """4. Duration quantization: tính theo FPS trên thread, tối thiểu 1 frame, không áp 24 frames giả."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    created_item = MockTimelineItem(name="RF Short", start=0, duration=1)
    timeline.tracks_video[2].append(created_item)

    captured_params = []
    def fake_bridge(*args, **kwargs):
        captured_params.append(kwargs.get("params", {}))
        return created_item

    monkeypatch.setattr("src.core.resolve_bridge.insert_title_at_playhead", fake_bridge)
    monkeypatch.setattr("src.core.resolve_api.current_record_frame", lambda tl, proj: (0, "01:00:00:00"))
    monkeypatch.setattr("src.core.resolve_api._nominal_fps", lambda proj: (60.0, 60.0))

    asset = TextStylePreset(id="short_title", name="Short Title")
    # Thời lượng 0.005s ở 60fps = 0.3 frame -> làm tròn lên ít nhất 1 frame
    svc.insert_async(asset, extra_params={"duration": 0.005})

    assert len(captured_params) == 1
    assert captured_params[0]["duration_frames"] == 1


def test_insert_service_text_undo_exact_clip_identity(mock_gateway, monkeypatch):
    """5. Title Undo: Xóa đúng clip theo project/timeline identity và exact match."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title To Undo", start=100, duration=50, unique_id="uuid_undo_1")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title To Undo",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_undo_1",
        },
        inserted_at=time.time(),
    )
    svc._last_results.append(result)

    ok, msg = svc.undo(result)
    assert ok is True
    assert "Đã hoàn tác" in msg
    assert clip in timeline.deleted_clips
    assert result not in svc._last_results


def test_insert_service_text_undo_denied_if_project_or_timeline_changed(mock_gateway):
    """6. Title Undo: Từ chối hoàn tác nếu project hoặc timeline hiện tại khác với lúc chèn."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title Other Project", start=100, duration=50, unique_id="uuid_test_6")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title Other Project",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project DIFFERENT",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_test_6",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert "Project" in msg or "Timeline" in msg
    assert len(timeline.deleted_clips) == 0


def test_insert_service_text_undo_denied_if_clip_ambiguous(mock_gateway):
    """7. Title Undo: Từ chối hoàn tác nếu phát hiện nhiều clip trùng Unique ID."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    # 2 clip có cùng unique id trên cùng track
    clip1 = MockTimelineItem(name="RF Duplicate", start=50, duration=30, unique_id="uuid_duplicate_shared")
    clip2 = MockTimelineItem(name="RF Duplicate", start=50, duration=30, unique_id="uuid_duplicate_shared")
    timeline.tracks_video[2].extend([clip1, clip2])

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Duplicate",
        start_frame=50,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 50,
            "duration_frames": 30,
            "unique_id": "uuid_duplicate_shared",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert "nhiều clip" in msg or "trùng" in msg
    assert len(timeline.deleted_clips) == 0


def test_insert_service_text_undo_denied_if_clip_uid_mismatch_even_with_same_name_and_position(mock_gateway):
    """8. Title Undo: Clip trên track cùng tên và vị trí nhưng khác UID -> từ chối, KHÔNG xóa nhầm replacement clip."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    replacement_clip = MockTimelineItem(
        name="RF Title", start=100, duration=50, unique_id="uuid_replacement_clip"
    )
    timeline.tracks_video[2].append(replacement_clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_original_inserted_clip",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "không còn tồn tại" in msg or "Unique ID" in msg or "ID" in msg


def test_insert_service_text_undo_denied_if_clip_uid_missing(mock_gateway):
    """9. Title Undo: Thiếu Unique ID trong item_ref -> từ chối hoàn tác (unsupported), 0 Delete."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title No UID", start=100, duration=50, unique_id="")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title No UID",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "",  # Missing UID
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "Unique ID" in msg or "định danh an toàn" in msg


def test_insert_service_text_undo_denied_if_stored_ids_mismatch_current(mock_gateway):
    """10. Title Undo: Stored project/timeline ID khác với project/timeline hiện tại -> từ chối, 0 Delete."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title", start=100, duration=50, unique_id="uuid_ok")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_DIFFERENT",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_ok",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "Project" in msg or "Timeline" in msg


def test_insert_service_text_actual_track_matched_by_uid_with_different_proxy_wrapper(mock_gateway, monkeypatch):
    """11. Track readback: Khớp clip theo stable clip UID thay vì object proxy identity."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()

    # Clip thực tế nằm ở track V3
    clip_on_v3 = MockTimelineItem(name="RF V3 Title", start=50, duration=48, unique_id="uid_matched_v3")
    timeline.tracks_video[3].append(clip_on_v3)

    # Bridge trả về proxy wrapper độc lập có cùng UID nhưng khác instance
    created_proxy = MockTimelineItem(name="RF V3 Title", start=50, duration=48, unique_id="uid_matched_v3")
    assert created_proxy is not clip_on_v3

    monkeypatch.setattr("src.core.resolve_bridge.insert_title_at_playhead", lambda *args, **kwargs: created_proxy)
    monkeypatch.setattr("src.core.resolve_api.current_record_frame", lambda tl, proj: (50, "01:00:02:02"))
    monkeypatch.setattr("src.core.resolve_api._nominal_fps", lambda proj: (24.0, 24.0))

    asset = TextStylePreset(id="v3_style", name="V3 Title")
    results = []
    svc.insert_async(asset, preferred_track=2, callback=results.append)

    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    assert res.track == "V3"
    assert res.item_ref["track"] == "V3"
    assert res.item_ref["track_index"] == 3


def test_insert_service_text_missing_track_evidence_does_not_default_to_v2(mock_gateway, monkeypatch):
    """12. Track readback: Không tìm thấy bằng chứng track -> không được tự bịa ra V2."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()

    # Clip không xuất hiện trên bất kỳ track nào của timeline
    created_proxy = MockTimelineItem(name="RF Orphan", start=0, duration=24, unique_id="uid_orphan")

    monkeypatch.setattr("src.core.resolve_bridge.insert_title_at_playhead", lambda *args, **kwargs: created_proxy)
    monkeypatch.setattr("src.core.resolve_api.current_record_frame", lambda tl, proj: (0, "01:00:00:00"))
    monkeypatch.setattr("src.core.resolve_api._nominal_fps", lambda proj: (24.0, 24.0))

    asset = TextStylePreset(id="orphan_style", name="Orphan Title")
    results = []
    svc.insert_async(asset, preferred_track=2, callback=results.append)

    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    assert res.track != "V2"
    assert res.track == ""


def test_insert_service_text_no_timeline_raises_without_automation_fallback(mock_gateway):
    """13. Gateway task: Không có timeline mở -> báo lỗi ngay, không fallback ResolveAutomation."""
    svc = InsertService(gateway=mock_gateway)
    # Timeline đang đóng
    mock_gateway._resolve.project._timeline = None

    asset = TextStylePreset(id="pop", name="Title")
    results = []
    svc.insert_async(asset, callback=results.append)

    assert len(results) == 1
    res = results[0]
    assert res.ok is False
    assert "timeline" in res.error.lower()


def test_insert_service_text_undo_denied_if_stored_project_id_missing(mock_gateway):
    """14. Title Undo: Thiếu stored project_id trong item_ref -> từ chối hoàn tác (unsupported), 0 Delete."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title", start=100, duration=50, unique_id="uuid_ok")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "",  # Missing stored project ID
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_ok",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "Project ID" in msg or "định danh an toàn" in msg


def test_insert_service_text_undo_denied_if_stored_timeline_id_missing(mock_gateway):
    """15. Title Undo: Thiếu stored timeline_id trong item_ref -> từ chối hoàn tác (unsupported), 0 Delete."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    clip = MockTimelineItem(name="RF Title", start=100, duration=50, unique_id="uuid_ok")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "",  # Missing stored timeline ID
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_ok",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "Timeline ID" in msg or "định danh an toàn" in msg


def test_insert_service_text_undo_denied_if_stored_id_present_but_current_uid_empty(mock_gateway):
    """16. Title Undo: Stored ID có thật nhưng Resolve hiện tại mất UID (trả về rỗng) -> từ chối, 0 Delete."""
    svc = InsertService(gateway=mock_gateway)
    timeline = mock_gateway._resolve.project.GetCurrentTimeline()
    # Current timeline returns empty UID
    timeline._uid = ""
    clip = MockTimelineItem(name="RF Title", start=100, duration=50, unique_id="uuid_ok")
    timeline.tracks_video[2].append(clip)

    result = InsertResult(
        ok=True,
        track="V2",
        item_name="RF Title",
        start_frame=100,
        item_ref={
            "kind": "title",
            "project_name": "Project 1",
            "project_id": "proj_uuid_1",
            "timeline_name": "Timeline 1",
            "timeline_id": "tl_uuid_1",
            "track_index": 2,
            "start_frame": 100,
            "duration_frames": 50,
            "unique_id": "uuid_ok",
        },
        inserted_at=time.time(),
    )

    ok, msg = svc.undo(result)
    assert ok is False
    assert len(timeline.deleted_clips) == 0
    assert "Timeline" in msg or "ID" in msg


