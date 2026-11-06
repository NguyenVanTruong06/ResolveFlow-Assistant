"""
tests/services/test_insert_service.py
Tests for InsertService:
- Mock Resolve: audio / video track allocation
- Mode "auto" and "new_track"
- Mode "fusion_paste"
- Safe undo: rejected after 31s, accepted within 30s
"""
import time
from typing import List, Optional
import pytest
from PySide6.QtCore import QCoreApplication

from src.services.resolve_gateway import ResolveGateway
from src.services.insert_service import InsertService, InsertResult


class MockTimelineItem:
    def __init__(self, name: str, start: int = 100, duration: int = 50):
        self._name = name
        self._start = start
        self._duration = duration

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetDuration(self):
        return self._duration


class MockTimelineWithTracks:
    def __init__(self):
        self.tracks_video = {
            1: [MockTimelineItem("Main Footage", start=0, duration=1000)],
            2: [MockTimelineItem("Busy Title V2", start=100, duration=100)],
        }
        self.tracks_audio = {
            1: [MockTimelineItem("Dialogue", start=0, duration=1000)],
        }
        self.deleted_items: List[MockTimelineItem] = []
        self._timecode = "01:00:04:12"

    def GetName(self):
        return "Test Timeline"

    def GetCurrentTimecode(self):
        return self._timecode

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetStartFrame(self):
        return 0

    def GetTrackCount(self, track_type="video"):
        if track_type == "audio":
            return len(self.tracks_audio)
        return len(self.tracks_video)

    def GetItemListInTrack(self, track_type="video", index=1):
        if track_type == "audio":
            return self.tracks_audio.get(index, [])
        return self.tracks_video.get(index, [])

    def DeleteClips(self, items, ripple=False):
        for it in items:
            self.deleted_items.append(it)
            # Remove from tracks
            for tr_type in (self.tracks_video, self.tracks_audio):
                for tr_idx in tr_type:
                    if it in tr_type[tr_idx]:
                        tr_type[tr_idx].remove(it)
        return True


class MockProjectWithTimeline:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetCurrentTimeline(self):
        return self._timeline


class MockResolveApp:
    def __init__(self, timeline=None):
        self.timeline = timeline or MockTimelineWithTracks()
        self.project = MockProjectWithTimeline(self.timeline)

    def GetProjectManager(self):
        class _PM:
            def __init__(self, proj):
                self._proj = proj
            def GetCurrentProject(self):
                return self._proj
        return _PM(self.project)

    def GetCurrentPage(self):
        return "edit"





def test_insert_service_undo_after_31s_rejected(qapp):
    """Undo must be rejected if > 30 seconds have elapsed."""
    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    # Fake a result inserted 31 seconds ago
    res = InsertResult(
        ok=True,
        track="V2",
        start_tc="01:00:00:00",
        start_frame=100,
        item_name="Old Title",
        inserted_at=time.time() - 31.0,
    )

    success, msg = service.undo(res)
    assert not success
    assert "quá thời gian" in msg or "30s" in msg

    gateway.shutdown()


def test_insert_service_undo_within_30s_success(qapp):
    """Undo within 30s should locate the item and call DeleteClips(..., False)."""
    timeline = MockTimelineWithTracks()
    # Add an item to V2
    target_item = MockTimelineItem("Fresh Title", start=100, duration=50)
    timeline.tracks_video[2].append(target_item)

    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    res = InsertResult(
        ok=True,
        track="V2",
        start_tc="01:00:04:00",
        start_frame=100,
        item_name="Fresh Title",
        inserted_at=time.time() - 5.0,  # 5 seconds ago
    )

    success, msg = service.undo(res)
    assert success, f"Undo should succeed, got error: {msg}"
    assert target_item in timeline.deleted_items
    assert target_item not in timeline.tracks_video[2]

    gateway.shutdown()


def test_insert_service_fusion_paste_mode(monkeypatch, qapp, tmp_path):
    """Test mode='fusion_paste' routes to fast_paste and returns InsertResult."""
    called_with = []

    def mock_fast_paste(code, resolve_app=None, log_callback=None):
        called_with.append(code)
        return {"ok": True, "message": "Success"}

    monkeypatch.setattr("src.core.fast_paste.fast_paste_to_fusion", mock_fast_paste)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    setting_file = tmp_path / "macro.setting"
    setting_file.write_text("TextPlus { ... }")

    asset = {
        "id": "macro_title",
        "name": "RF Kinetic Macro",
        "category": "titles",
        "file_path": str(setting_file),
    }

    result = service.insert(asset, mode="fusion_paste")
    assert result.ok
    assert result.track == "Fusion"
    assert result.item_name == "RF Kinetic Macro"
    assert "TextPlus { ... }" in called_with

    gateway.shutdown()


def test_insert_service_audio_auto_mode_free_track(qapp, monkeypatch, tmp_path):
    """When Track A2 is busy, auto mode should select Track A3."""
    timeline = MockTimelineWithTracks()
    # A1 is dialogue, add clip to A2 making it busy
    timeline.tracks_audio[2] = [MockTimelineItem("Busy SFX A2", start=0, duration=200)]
    timeline.tracks_audio[3] = []  # A3 is empty

    resolve = MockResolveApp(timeline)

    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    wav_file = tmp_path / "whoosh.wav"
    wav_file.write_bytes(b"audio data")

    asset = {
        "id": "whoosh_01",
        "name": "Whoosh Swell",
        "category": "sfx",
        "file_path": str(wav_file),
    }

    from unittest.mock import Mock
    insert_audio = Mock(return_value=True)
    monkeypatch.setattr("src.core.resolve_api.ResolveAutomation.insert_sfx_to_track", insert_audio)
    result = service.insert(asset, mode="auto")
    insert_audio.assert_called_once_with(str(wav_file), target_track=3, record_frame=108)
    assert result.ok
    assert result.track == "A3", f"Expected Track A3 since A2 is busy, got {result.track}"
    assert result.item_name == "Whoosh Swell"

    gateway.shutdown()


def test_insert_service_audio_exhausted_tracks_error(qapp, tmp_path):
    """When all allowed tracks are busy, auto mode should fail gracefully."""
    timeline = MockTimelineWithTracks()
    # Occupy all tracks 1, 2, 3, 4
    for t in (1, 2, 3, 4):
        timeline.tracks_audio[t] = [MockTimelineItem(f"Busy {t}", start=0, duration=200)]

    resolve = MockResolveApp(timeline)

    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    wav_file = tmp_path / "impact.wav"
    wav_file.write_bytes(b"audio data")

    asset = {
        "id": "whoosh_02",
        "name": "Heavy Impact",
        "category": "sfx",
        "file_path": str(wav_file),
    }

    result = service.insert(asset, mode="auto")
    assert not result.ok
    assert "Hết track" in result.error or "trống" in result.error

    gateway.shutdown()


@pytest.mark.parametrize("left,duration,want", [(0, 100, 2), (180, 20, 2), (179, 20, 3), (100, 10, 3)])
def test_audio_track_uses_whole_insertion_interval(left, duration, want):
    timeline = MockTimelineWithTracks()
    timeline.tracks_audio[2] = [MockTimelineItem("SFX", left, duration)]
    timeline.tracks_audio[3] = []
    assert InsertService._find_free_audio_track(timeline, 100, 180) == want


def test_audio_track_created_when_existing_tracks_busy():
    timeline = MockTimelineWithTracks()
    timeline.tracks_audio[2] = [MockTimelineItem("SFX", 0, 200)]
    def add_track(kind):
        timeline.tracks_audio[len(timeline.tracks_audio) + 1] = []
        return True
    timeline.AddTrack = add_track
    assert InsertService._find_free_audio_track(timeline, 100, 180) == 3
    assert 3 in timeline.tracks_audio


@pytest.mark.parametrize("record_frame,want", [(None, 108), (0, 0), (90123, 90123)])
def test_audio_append_positions_at_record_frame(tmp_path, monkeypatch, record_frame, want):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.core.resolve_api import ResolveAutomation
    audio = tmp_path / "sound.wav"
    audio.touch()
    clip = SimpleNamespace(GetClipProperty=lambda key: str(audio))
    pool = SimpleNamespace(AppendToTimeline=Mock(return_value=[object()]))
    auto = ResolveAutomation()
    auto.resolve = object()
    auto.current_project = SimpleNamespace(GetMediaPool=lambda: pool)
    monkeypatch.setattr(auto, "get_active_timeline", lambda: MockTimelineWithTracks())
    monkeypatch.setattr(auto, "import_media_to_media_pool", lambda *a, **k: True)
    monkeypatch.setattr(auto, "get_media_pool_clips", lambda: [clip])
    assert auto.insert_sfx_to_track(str(audio), target_track=3, record_frame=record_frame)
    spec = pool.AppendToTimeline.call_args.args[0][0]
    assert spec["recordFrame"] == want
    assert spec["trackIndex"] == 3
    assert spec["mediaType"] == 2
    pool.AppendToTimeline.return_value = []
    assert not auto.insert_sfx_to_track(str(audio), record_frame=record_frame)


@pytest.mark.parametrize("metadata", [{"tab": "trans"}, {"category": "trans"}])
def test_transition_preview_never_routes_to_video(qapp, monkeypatch, metadata):
    from unittest.mock import Mock
    service = InsertService(None)
    transition = Mock()
    video = Mock()
    monkeypatch.setattr(service, "_insert_transition", transition)
    monkeypatch.setattr(service, "_insert_video", video)
    service.insert_async({**metadata, "file_path": "/preview.mp4", "preview_path": "/preview.mp4"})
    transition.assert_called_once()
    video.assert_not_called()


def test_transition_without_adjustment_template_does_not_insert_carrier(monkeypatch):
    from unittest.mock import Mock
    from src.core import resolve_bridge
    ctx = Mock()
    ctx.timeline.GetSetting.return_value = "24"
    monkeypatch.setattr(resolve_bridge, "playhead_frame", lambda tl: 108)
    monkeypatch.setattr(resolve_bridge, "resolve_track", lambda *a: 2)
    monkeypatch.setattr(resolve_bridge, "get_carrier_clip", lambda *a: object())
    ctx.media_pool.AppendToTimeline.return_value = []
    monkeypatch.setattr(resolve_bridge, "context", lambda: ctx)
    with pytest.raises(resolve_bridge.ResolveFlowError, match="Adjustment"):
        resolve_bridge.insert_transition_at_playhead("GR Seamless")
    ctx.media_pool.AppendToTimeline.assert_not_called()


def test_category_nav_wheel_reaches_hidden_categories(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from src.ui.lux.library.category_nav import CategoryNavWidget
    nav = CategoryNavWidget()
    nav.resize(360, 80)
    nav.show()
    qapp.processEvents()
    bar = nav.cat_scroll.horizontalScrollBar()
    assert bar.maximum() > 0
    for _ in range(20):
        event = QWheelEvent(QPointF(40, 15), QPointF(40, 15), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        qapp.sendEvent(nav.cat_scroll.viewport(), event)
    assert bar.value() == bar.maximum()
    nav.select_category("mine")
    assert nav.current_category == "mine"
    bar.setValue(0)
    nav.select_category("mine")
    assert bar.value() > 0
    event = QWheelEvent(QPointF(40, 15), QPointF(40, 15), QPoint(60, 0), QPoint(),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.ScrollUpdate, False)
    qapp.sendEvent(nav.cat_scroll.viewport(), event)
    assert bar.value() < bar.maximum()
    nav.close()


def test_transition_preparation_copies_name_without_inserting(qapp):
    gateway = ResolveGateway(resolve_override=MockResolveApp())
    try:
        service = InsertService(gateway)
        result = service.insert({"tab": "trans", "name": "GR Seamless", "file_path": "/preview.mp4"})
        assert result.ok
        assert result.track == "Transitions"
        assert "Effects > Video Transitions > GR Seamless Transitions" in result.message
        assert qapp.clipboard().text() == "GR Seamless"
        assert not service._last_results
    finally:
        gateway.shutdown()


@pytest.mark.parametrize("clips,want", [([(0, 100), (100, 100), (200, 100)], "01:00:04:04"),
                                        ([(0, 100), (120, 80)], None), ([], None)])
def test_prepare_transition_moves_only_to_shared_v1_cut(clips, want):
    from unittest.mock import Mock
    from src.core.resolve_bridge import prepare_native_transition
    timeline = MockTimelineWithTracks()
    timeline.tracks_video[1] = [MockTimelineItem("Video", start, duration) for start, duration in clips]
    timeline.SetCurrentTimecode = Mock(return_value=True)
    resolve = MockResolveApp(timeline)
    result = prepare_native_transition("GR Seamless", resolve)
    if want:
        timeline.SetCurrentTimecode.assert_called_once_with(want)
        assert result["start_frame"] == 100
    else:
        timeline.SetCurrentTimecode.assert_not_called()
        assert result["start_frame"] == -1
    assert "Ctrl+T" in result["message"]
    assert "mặc định" in result["message"]


def test_prepare_transition_without_resolve_still_gives_manual_guidance(monkeypatch):
    from src.core import resolve_bridge
    def disconnected():
        raise resolve_bridge.ResolveFlowError("Resolve chưa mở")
    monkeypatch.setattr(resolve_bridge, "context", disconnected)
    result = resolve_bridge.prepare_native_transition("GR Zoom")
    assert result["start_frame"] == -1
    assert "Resolve chưa mở" in result["message"]
    assert "Effects > Video Transitions" in result["message"]


def test_dock_do_insert_passes_correct_modes_and_tracks(qapp):
    from unittest.mock import MagicMock
    from src.ui.lux.shell.edge_dock import EdgeDockWindow
    mock_service = MagicMock()
    mock_service.insert.return_value = InsertResult(ok=True, track="A2")

    dock = EdgeDockWindow(insert_service=mock_service)
    try:
        sample_sfx = {
            "id": "boom_01",
            "name": "Boom SFX",
            "category": "sfx",
            "file_path": "/fake/boom.wav",
        }

        # 1. Default (no params / empty dict) -> mode="auto", preferred_track=None
        dock._do_insert(sample_sfx, {})
        assert mock_service.insert.called
        call_kwargs = mock_service.insert.call_args.kwargs
        assert call_kwargs.get("mode") == "auto"
        assert call_kwargs.get("preferred_track") is None

        # 2. Tạo track mới -> mode="new_track", preferred_track=None
        dock._do_insert(sample_sfx, {"track_mode": "new"})
        call_kwargs = mock_service.insert.call_args.kwargs
        assert call_kwargs.get("mode") == "new_track"
        assert call_kwargs.get("preferred_track") is None

        # 3. Chỉ định track cụ thể qua track_mode="A3" -> mode="timeline_track", preferred_track=3
        dock._do_insert(sample_sfx, {"track_mode": "A3"})
        call_kwargs = mock_service.insert.call_args.kwargs
        assert call_kwargs.get("mode") == "timeline_track"
        assert call_kwargs.get("preferred_track") == 3

        # 4. Chỉ định track cụ thể qua preferred_track=4 -> mode="timeline_track", preferred_track=4
        dock._do_insert(sample_sfx, {"preferred_track": 4})
        call_kwargs = mock_service.insert.call_args.kwargs
        assert call_kwargs.get("mode") == "timeline_track"
        assert call_kwargs.get("preferred_track") == 4
    finally:
        dock.close()


def test_dock_ui_to_service_default_selects_a3_when_a2_busy(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.ui.lux.shell.edge_dock import EdgeDockWindow
    from src.core.resolve_api import ResolveAutomation

    timeline = MockTimelineWithTracks()
    # A1 has Dialogue, A2 has busy SFX
    timeline.tracks_audio[2] = [MockTimelineItem("Busy SFX", start=0, duration=200)]
    def add_track(kind):
        timeline.tracks_audio[len(timeline.tracks_audio) + 1] = []
        return True
    timeline.AddTrack = add_track
    timeline._timecode = "01:00:04:12"  # frame 108

    recorded_calls = []
    def mock_insert_sfx(self, sfx_path, target_track=2, time_pos=None, volume_offset_db=-12.0, log_callback=None, record_frame=None):
        recorded_calls.append({"track": target_track, "record_frame": record_frame})
        return True

    monkeypatch.setattr(ResolveAutomation, "insert_sfx_to_track", mock_insert_sfx)

    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        dock = EdgeDockWindow(insert_service=service)
        fake_wav = tmp_path / "ding.wav"
        fake_wav.write_bytes(b"RIFFding")
        sample_sfx = {
            "id": "ding_01",
            "name": "Ding",
            "category": "sfx",
            "file_path": str(fake_wav),
        }

        # 1. UI Default: A2 bận -> service tự chọn A3, giữ đúng record_frame 108
        dock._do_insert(sample_sfx, {})
        assert len(recorded_calls) == 1
        assert recorded_calls[0]["track"] == 3
        assert recorded_calls[0]["record_frame"] == 108
        assert "A3" in dock.action_bar.lbl_info.text()

        # 2. UI New track: tạo track mới (A4)
        dock._do_insert(sample_sfx, {"track_mode": "new"})
        assert len(recorded_calls) == 2
        assert recorded_calls[1]["track"] == 4
        assert recorded_calls[1]["record_frame"] == 108

        # 3. UI Preferred track: chỉ định A2 dù bận
        dock._do_insert(sample_sfx, {"track_mode": "A2"})
        assert len(recorded_calls) == 3
        assert recorded_calls[2]["track"] == 2
        assert recorded_calls[2]["record_frame"] == 108
        dock.close()
    finally:
        gateway.shutdown()


def test_studio_insert_at_playhead_routes_through_insert_service(qapp):
    from unittest.mock import MagicMock
    from src.ui.tabs.tab_assets import TabAssets, StudioAsset

    tab = TabAssets(headless=True)
    try:
        mock_svc = MagicMock()
        mock_svc.insert.return_value = InsertResult(ok=True, track="A3")
        tab._insert_service = mock_svc

        tab.selected_asset = StudioAsset(
            id="sfx_whoosh",
            name="Whoosh Effect",
            tab="sfx",
            category="sfx",
            file_path="/fake/whoosh.wav",
        )

        # 1. Default audio insert -> mode="auto", preferred_track=None
        tab._insert_at_playhead()
        assert mock_svc.insert.called
        call_kwargs = mock_svc.insert.call_args.kwargs
        assert call_kwargs.get("mode") == "auto"
        assert call_kwargs.get("preferred_track") is None
        assert "A3" in tab.lbl_toast.text()

        # 2. Explicit A1 selected in combo_target_track -> mode="timeline_track", preferred_track=1
        if hasattr(tab, "combo_target_track"):
            tab.combo_target_track.clear()
            tab.combo_target_track.addItem("Audio Track 1 (A1)")
            tab.combo_target_track.setCurrentIndex(0)
            tab._insert_at_playhead()
            call_kwargs = mock_svc.insert.call_args.kwargs
            assert call_kwargs.get("mode") == "timeline_track"
            assert call_kwargs.get("preferred_track") == 1
    finally:
        tab.close()


def test_insert_lut_success_calls_apply_lut_to_current_clip(tmp_path, monkeypatch):
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    called_args = []
    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        called_args.append({"path": lut_path, "target_track": target_track, "skip_existing": skip_existing})
        return {
            "ok": True,
            "status": "applied",
            "applied": 1,
            "skipped": 0,
            "failed": 0,
            "clip_name": "Clip_A",
            "track": 1,
            "message": "Đã áp LUT 'cinematic.cube' vào clip 'Clip_A' (V1)",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert res.ok
        assert res.track == "V1"
        assert len(called_args) == 1
        assert called_args[0]["path"] == str(lut_file.resolve())
        assert "Đã áp" in res.message
    finally:
        gateway.shutdown()


def test_insert_lut_missing_file_fails(monkeypatch):
    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Nonexistent", "file_path": "/path/does/not/exist.cube"})
        assert not res.ok
        assert "Không tìm thấy" in res.error or "not found" in res.error.lower() or "exist" in res.error.lower()
    finally:
        gateway.shutdown()


def test_insert_lut_no_timeline_fails(tmp_path, monkeypatch):
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    monkeypatch.setattr(ResolveAutomation, "get_active_timeline", lambda self: None)

    gateway = ResolveGateway(resolve_override=None)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "Timeline" in res.error or "timeline" in res.error.lower()
    finally:
        gateway.shutdown()


def test_insert_lut_skipped_when_existing_lut(tmp_path, monkeypatch):
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": False,
            "status": "skipped",
            "applied": 0,
            "skipped": 1,
            "failed": 0,
            "clip_name": "Clip_A",
            "track": 1,
            "message": "Clip 'Clip_A' đã có LUT (Old.cube), chưa thay đổi",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "chưa thay đổi" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_core_failure(tmp_path, monkeypatch):
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": False,
            "status": "error",
            "applied": 0,
            "skipped": 0,
            "failed": 1,
            "clip_name": "Clip_A",
            "track": 1,
            "message": "Áp LUT vào clip thất bại",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "thất bại" in res.error or "failed" in res.error.lower()
    finally:
        gateway.shutdown()


def test_insert_lut_partial_applied_mixed(tmp_path, monkeypatch):
    """
    Test ĐỎ cho ca mixed: applied > 0 và failed > 0.
    Trước khi tích hợp contract: applied=2 > 0 khiến service che giấu lỗi và trả về ok=True.
    Sau khi tích hợp contract: Phải trả về ok=False, thông báo tiếng Việt nêu rõ số applied và failed.
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": False,
            "status": "error",
            "applied": 2,
            "skipped": 0,
            "failed": 1,
            "clip_name": "Clip_A",
            "track": 1,
            "message": "1 clip bị khóa node graph",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok, "Mixed applied+failed bắt buộc phải là ok=False!"
        assert "Đã áp 2" in res.error
        assert "lỗi 1" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_applied_plus_skipped(tmp_path, monkeypatch):
    """
    applied > 0, skipped > 0, failed == 0 -> APPLIED (ok=True).
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": True,
            "status": "applied",
            "applied": 2,
            "skipped": 1,
            "failed": 0,
            "clip_name": "Clip_A",
            "track": 2,
            "message": "Đã áp 2 clip, bỏ qua 1 clip đã có",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert res.ok
        assert res.track == "V2"
    finally:
        gateway.shutdown()


def test_insert_lut_all_zero_counters_fails_as_unknown(tmp_path, monkeypatch):
    """
    Cả 3 counters đều bằng 0 -> UNKNOWN -> ok=False (kể cả khi ok=True từ producer).
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": True,
            "status": "applied",
            "applied": 0,
            "skipped": 0,
            "failed": 0,
            "message": "Không có thao tác nào",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "chưa xác minh" in res.error or "thiếu bộ đếm" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_missing_counter_fails_as_unknown(tmp_path, monkeypatch):
    """
    Thiếu counter (vd: chỉ có applied, thiếu failed/skipped) -> UNKNOWN -> ok=False.
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "applied": 1,
            # thiếu skipped và failed
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "chưa xác minh" in res.error or "thiếu bộ đếm" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_invalid_counter_type_fails(tmp_path, monkeypatch):
    """
    Counter mang kiểu không hợp lệ (vd: bool hoặc string) -> ValueError -> ok=False.
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "applied": "invalid_string",
            "skipped": 0,
            "failed": 0,
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "số nguyên" in res.error or "int" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_invalid_raw_status_fails(tmp_path, monkeypatch):
    """
    raw status mang kiểu không hợp lệ (vd: int 123) -> ValueError -> ok=False.
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "applied": 1,
            "skipped": 0,
            "failed": 0,
            "status": 123,
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert not res.ok
        assert "chuỗi" in res.error or "str" in res.error
    finally:
        gateway.shutdown()


def test_insert_lut_contradictory_status_ok_valid_counters(tmp_path, monkeypatch):
    """
    Bộ đếm hợp lệ applied=1, failed=0 -> APPLIED (ok=True), kể cả khi status='error' và ok=False.
    Counters tường minh luôn quyết định!
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return {
            "ok": False,
            "status": "error",
            "applied": 1,
            "skipped": 0,
            "failed": 0,
            "clip_name": "Clip_A",
            "track": 1,
            "message": "status mâu thuẫn",
        }

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert res.ok, "Bộ đếm applied=1, failed=0 bắt buộc phải cho ok=True!"
        assert res.track == "V1"
    finally:
        gateway.shutdown()


def test_insert_lut_source_dict_not_mutated(tmp_path, monkeypatch):
    """
    Từ điển gốc của producer không bị biến đổi khi đi qua _insert_lut.
    """
    from src.core.resolve_api import ResolveAutomation
    lut_file = tmp_path / "cinematic.cube"
    lut_file.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    original_dict = {
        "ok": True,
        "status": "applied",
        "applied": 1,
        "skipped": 0,
        "failed": 0,
        "clip_name": "Clip_A",
        "track": 1,
        "message": "Gốc",
        "nested": {"a": 1},
    }

    def mock_apply_lut_to_current_clip(self, lut_path, target_track=None, skip_existing=True, log_callback=None):
        return original_dict

    monkeypatch.setattr(ResolveAutomation, "apply_lut_to_current_clip", mock_apply_lut_to_current_clip)

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    try:
        service = InsertService(gateway)
        res = service.insert({"tab": "lut", "name": "Cinematic Teal", "file_path": str(lut_file)})
        assert res.ok
        assert original_dict["applied"] == 1
        assert original_dict["nested"] == {"a": 1}
    finally:
        gateway.shutdown()



def test_category_nav_overflow_arrows_and_lut_at_360_and_420(qapp):
    from src.ui.lux.library.category_nav import CategoryNavWidget
    for width in (360, 420):
        nav = CategoryNavWidget()
        nav.resize(width, 80)
        nav.show()
        qapp.processEvents()

        # Check arrow buttons exist
        assert hasattr(nav, "btn_scroll_left")
        assert hasattr(nav, "btn_scroll_right")

        bar = nav.cat_scroll.horizontalScrollBar()
        assert bar.maximum() > 0

        # At start, left is disabled, right is enabled
        assert not nav.btn_scroll_left.isEnabled()
        assert nav.btn_scroll_right.isEnabled()

        # Scroll right via arrow button
        nav.btn_scroll_right.click()
        qapp.processEvents()
        assert bar.value() > 0
        assert nav.btn_scroll_left.isEnabled()

        # Select LUT category -> scrolls into view
        nav.select_category("lut")
        qapp.processEvents()
        assert nav.current_category == "lut"
        btn_lut = nav._cat_buttons["lut"]
        lut_pos = btn_lut.mapTo(nav.cat_scroll.viewport(), btn_lut.rect().topLeft())
        assert lut_pos.x() >= 0

        # Subcategories for LUT should only have "Tất cả" and sub_scroll should be hidden
        assert nav._categories[next(i for i, c in enumerate(nav._categories) if c["id"] == "lut")]["subs"] == ["Tất cả"]
        assert nav.sub_scroll.isHidden()

        # Scroll to maximum -> right arrow disabled
        bar.setValue(bar.maximum())
        nav._update_scroll_arrows()
        assert not nav.btn_scroll_right.isEnabled()
        assert nav.btn_scroll_left.isEnabled()

        nav.close()


def test_guard_reject_zero_gateway_callback_signal_and_sync_immediate(qapp, monkeypatch):
    """
    Khi guard từ chối (ví dụ missing file, unverified origin, staging):
    - Không submit bất kỳ task nào lên gateway (0 gateway tasks).
    - Callback được gọi đúng 1 lần với res.ok is False.
    - Signal insertion_finished được emit đúng 1 lần với res.ok is False.
    - Không ghi nhận vào lịch sử undo (service._last_results rỗng).
    - Synchronous wrapper insert() trả về ngay lập tức (không chờ loop timeout).
    """
    from unittest.mock import MagicMock
    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    mock_submit = MagicMock(wraps=gateway.submit)
    monkeypatch.setattr(gateway, "submit", mock_submit)

    try:
        service = InsertService(gateway)
        callback_calls = []
        signal_calls = []

        def on_callback(res):
            callback_calls.append(res)

        service.insertion_finished.connect(signal_calls.append)

        # Asset thiếu file trên đĩa
        rejected_asset = {
            "id": "missing_sfx",
            "name": "Missing Audio",
            "category": "sfx",
            "file_path": "/nonexistent/sfx.wav",
        }

        # 1. Kiểm tra insert_async
        service.insert_async(rejected_asset, callback=on_callback)
        qapp.processEvents()

        assert mock_submit.call_count == 0, "Không được submit gateway task nào khi guard reject"
        assert len(callback_calls) == 1
        assert not callback_calls[0].ok
        assert "không tồn tại" in callback_calls[0].error or "Không tìm thấy" in callback_calls[0].error
        assert len(signal_calls) == 1
        assert not signal_calls[0].ok
        assert len(service._last_results) == 0, "Không ghi lịch sử undo cho thao tác thất bại"

        # 2. Kiểm tra synchronous wrapper insert() trả về ngay lập tức (không enter QEventLoop)
        from unittest.mock import patch
        from PySide6.QtCore import QEventLoop
        with patch.object(QEventLoop, "exec") as mock_loop_exec:
            sync_res = service.insert(rejected_asset, timeout=5.0)
            assert not sync_res.ok
            assert mock_loop_exec.call_count == 0, "Synchronous insert không được enter QEventLoop khi guard từ chối"
        assert len(service._last_results) == 0
        assert mock_submit.call_count == 0
    finally:
        gateway.shutdown()


def test_guard_allowed_dispatches_resolved_path(qapp, tmp_path, monkeypatch):
    """
    Khi guard thông qua, đường dẫn đã giải quyết (resolved_path từ guard)
    được dùng để dispatch vào automation/gateway, không dùng preview path hay đường dẫn chưa resolve.
    """
    from src.core.resolve_api import ResolveAutomation

    timeline = MockTimelineWithTracks()
    timeline.tracks_audio[2] = []
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    # Tạo file thật và một symlink trỏ đến nó
    real_file = tmp_path / "actual_audio.wav"
    real_file.write_bytes(b"RIFFwave_data")
    symlink_file = tmp_path / "symlink_audio.wav"
    try:
        symlink_file.symlink_to(real_file)
    except OSError:
        # Nếu filesystem không hỗ trợ symlink, fallback copy
        symlink_file.write_bytes(b"RIFFwave_data")

    recorded_dispatches = []
    def mock_insert_sfx(self, sfx_path, target_track=2, time_pos=None, volume_offset_db=-12.0, log_callback=None, record_frame=None):
        recorded_dispatches.append(sfx_path)
        return True

    monkeypatch.setattr(ResolveAutomation, "insert_sfx_to_track", mock_insert_sfx)

    try:
        service = InsertService(gateway)
        asset = {
            "id": "sfx_sym",
            "name": "Symlink Audio",
            "category": "sfx",
            "file_path": str(symlink_file),
        }

        res = service.insert(asset, mode="auto")
        assert res.ok
        assert len(recorded_dispatches) == 1
        # Phải dùng realpath đã được giải quyết bởi guard
        expected_resolved = str(real_file.resolve())
        assert recorded_dispatches[0] == expected_resolved
    finally:
        gateway.shutdown()


def test_native_guidance_preserves_insert_transition_behavior(qapp, monkeypatch):
    """
    Native transition với intent='insert' được guard cho phép mà không cần file preview MP4.
    InsertService giữ nguyên luồng _insert_transition:
    - Sao chép tên transition vào clipboard.
    - Gọi prepare_native_transition (tìm cut point và trả về hướng dẫn).
    - Không ghi lịch sử Undo.
    """
    from PySide6.QtWidgets import QApplication

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    try:
        service = InsertService(gateway)
        transition_asset = {
            "id": "trans_cross_dissolve",
            "name": "Cross Dissolve",
            "category": "trans",
            "availability": "missing",  # Không có file preview/video
        }

        # Clipboard test
        app = QApplication.instance()
        if app:
            app.clipboard().clear()

        results = []
        def on_done(res):
            results.append(res)

        service.insert_async(transition_asset, callback=on_done)
        qapp.processEvents()

        # Gateway task runs asynchronously on gateway thread; wait briefly for callback
        t0 = time.time()
        while not results and time.time() - t0 < 3.0:
            qapp.processEvents()
            time.sleep(0.02)

        assert len(results) == 1
        res = results[0]
        assert res.ok
        assert res.track == "Transitions"
        assert res.item_name == "Cross Dissolve"
        assert "Cross Dissolve" in res.message or "Kéo thả" in res.message or "vết cắt" in res.message

        if app:
            assert app.clipboard().text() == "Cross Dissolve"

        assert len(service._last_results) == 0, "Native transition không ghi undo history"
    finally:
        gateway.shutdown()


def test_missing_managed_text_never_fallbacks_to_generator(qapp, tmp_path, monkeypatch):
    """
    Tài nguyên managed dạng text (file-backed .setting/.drfx) bị thiếu file trên đĩa
    bị guard chặn ngay lập tức, TUYỆT ĐỐI KHÔNG fallback sang procedural title generator.
    """
    from unittest.mock import MagicMock
    from src.core.storage_manager import StorageManager

    root = tmp_path / "lib_root"
    root.mkdir(parents=True, exist_ok=True)

    # Configure real StorageManager
    config_dir = tmp_path / "config"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=0)
    sm.configure(root)
    actual_lib_id = sm._config["library_id"]

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    # Spy on _insert_title_worker
    title_worker_spy = MagicMock()
    monkeypatch.setattr(InsertService, "_insert_title_worker", title_worker_spy)

    try:
        service = InsertService(gateway)

        # Asset managed nhưng file .setting trong kho không tồn tại
        missing_managed_text = {
            "id": "managed_macro_text",
            "name": "Kinetic Text In Storage",
            "category": "text",
            "source_kind": "managed",
            "origin_library_id": actual_lib_id,
            "origin_root": str(root),
            "library_path": "templates/text/missing_macro.setting",
            "file_path": str(root / "templates/text/missing_macro.setting"),
        }

        # Use sm as active storage manager
        monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda *a, **kw: sm)

        res = service.insert(missing_managed_text)
        assert not res.ok
        assert "không tồn tại" in res.error or "Không tìm thấy" in res.error
        assert title_worker_spy.call_count == 0, "Không được fallback sang title worker khi file managed bị thiếu!"
        assert len(service._last_results) == 0
    finally:
        gateway.shutdown()


def test_insert_service_malformed_source_kind_rejected(qapp, monkeypatch):
    """
    Asset có source_kind không hợp lệ (ví dụ type list hoặc chuỗi lạ) bị guard từ chối ngay,
    dẫn đến 0 gateway tasks, callback nhận lỗi, 1 signal và 0 undo history.
    """
    from unittest.mock import MagicMock
    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)
    mock_submit = MagicMock(wraps=gateway.submit)
    monkeypatch.setattr(gateway, "submit", mock_submit)

    try:
        service = InsertService(gateway)
        callback_calls = []
        signal_calls = []

        service.insertion_finished.connect(signal_calls.append)

        malformed_asset = {
            "id": "bad_source_clip",
            "name": "Bad Source Audio",
            "category": "sfx",
            "file_path": "/some/path.wav",
            "source_kind": ["invalid_type"],
        }

        service.insert_async(malformed_asset, callback=callback_calls.append)
        qapp.processEvents()

        assert mock_submit.call_count == 0
        assert len(callback_calls) == 1
        assert not callback_calls[0].ok
        assert "source_kind" in callback_calls[0].error or "không hợp lệ" in callback_calls[0].error
        assert len(signal_calls) == 1
        assert not signal_calls[0].ok
        assert len(service._last_results) == 0
    finally:
        gateway.shutdown()


def test_insert_service_generated_audio_file_dispatched(qapp, tmp_path, monkeypatch):
    """
    R2: Asset có source_kind='generated' với file audio thật trên đĩa được guard cho phép
    và dispatch đúng đường dẫn file tới ResolveAutomation mà không bị chặn nhầm.
    """
    from src.core.resolve_api import ResolveAutomation

    timeline = MockTimelineWithTracks()
    timeline.tracks_audio[2] = []
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    gen_wav = tmp_path / "ai_generated_speech.wav"
    gen_wav.write_bytes(b"RIFFgen_speech")

    recorded_dispatches = []
    def mock_insert_sfx(self, sfx_path, target_track=2, time_pos=None, volume_offset_db=-12.0, log_callback=None, record_frame=None):
        recorded_dispatches.append({"path": sfx_path, "track": target_track, "frame": record_frame})
        return True

    monkeypatch.setattr(ResolveAutomation, "insert_sfx_to_track", mock_insert_sfx)

    try:
        service = InsertService(gateway)
        asset = {
            "id": "gen_sfx_01",
            "name": "Generated Speech Audio",
            "category": "sfx",
            "source_kind": "generated",
            "file_path": str(gen_wav),
        }

        res = service.insert(asset, mode="auto")
        assert res.ok
        assert len(recorded_dispatches) == 1
        assert recorded_dispatches[0]["path"] == str(gen_wav.resolve())
        assert recorded_dispatches[0]["track"] == 2
    finally:
        gateway.shutdown()


def test_insert_service_video_no_repo_fallback_on_missing_file(qapp, tmp_path, monkeypatch):
    """
    04C4: Trong _insert_video, nếu file media bị xóa hoặc mất trước thời điểm worker thực thi,
    worker phải báo lỗi fail-closed, tuyệt đối KHÔNG fallback quét thư mục repo cùng tên.
    """
    from src.core.resolve_api import ResolveAutomation

    timeline = MockTimelineWithTracks()
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    dispatched_paths = []
    def mock_insert_media(self, media_path, target_track=3, duration_sec=None):
        dispatched_paths.append(media_path)
        return True

    monkeypatch.setattr(ResolveAutomation, "insert_media_at_playhead", mock_insert_media)

    # Tạo file tạm rồi xóa sau khi guard đã pass (giả lập TOCTOU hoặc file mất trước worker)
    tmp_vid = tmp_path / "meme_sample.mp4"
    tmp_vid.write_bytes(b"content")

    try:
        service = InsertService(gateway)
        asset = {
            "id": "meme_sample",
            "name": "meme_sample",
            "category": "meme",
            "source_kind": "builtin",
            "file_path": str(tmp_vid),
        }

        # Xóa file vật lý trước khi service worker chạy task
        tmp_vid.unlink()

        res = service.insert(asset, mode="auto")
        assert not res.ok
        assert "không tìm thấy" in res.error.lower()
        # Không được dispatch file nào vào Resolve
        assert len(dispatched_paths) == 0
    finally:
        gateway.shutdown()


def test_insert_service_audio_volume_offset_threading(qapp, tmp_path, monkeypatch):
    """
    04C4: extra_params['volume_offset'] hoặc 'volume_offset_db' (ví dụ -18dB cho BGM)
    được thread chính xác vào ResolveAutomation.insert_sfx_to_track.
    """
    from src.core.resolve_api import ResolveAutomation

    timeline = MockTimelineWithTracks()
    timeline.tracks_audio[2] = []
    resolve = MockResolveApp(timeline)
    gateway = ResolveGateway(resolve_override=resolve)

    audio_file = tmp_path / "bgm_loop.wav"
    audio_file.write_bytes(b"RIFFbgm")

    recorded_volume = []
    def mock_insert_sfx(self, sfx_path, target_track=2, time_pos=None, volume_offset_db=-12.0, log_callback=None, record_frame=None):
        recorded_volume.append(volume_offset_db)
        return True

    monkeypatch.setattr(ResolveAutomation, "insert_sfx_to_track", mock_insert_sfx)

    try:
        service = InsertService(gateway)
        asset = {
            "id": "bgm_chill",
            "name": "Chill BGM",
            "category": "bgm",
            "source_kind": "builtin",
            "file_path": str(audio_file),
        }

        res = service.insert(asset, mode="auto", extra_params={"volume_offset": -18.0})
        assert res.ok
        assert len(recorded_volume) == 1
        assert recorded_volume[0] == -18.0
    finally:
        gateway.shutdown()


def test_insert_service_native_transition_auto_mode_on_fusion_page(qapp):
    """
    04C4: Khi đang ở Fusion page nhưng mode='auto' và category='trans',
    thao tác phải đi qua _insert_transition (native guidance), KHÔNG đi nhầm vào _insert_fusion_paste.
    """
    from unittest.mock import MagicMock, patch

    state_service = MagicMock()
    state_service.current_page = "fusion"

    gateway = MagicMock(spec=ResolveGateway)
    service = InsertService(gateway, state_service=state_service)

    asset = {
        "id": "trans_cross_dissolve",
        "name": "Cross Dissolve",
        "category": "trans",
        "tab": "trans",
        "source_kind": "builtin",
    }

    with patch.object(service, "_insert_transition") as mock_trans, patch.object(service, "_insert_fusion_paste") as mock_fusion:
        service.insert_async(asset, mode="auto")
        assert mock_trans.call_count == 1
        assert mock_fusion.call_count == 0








