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


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication([])
    return app


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


def test_insert_service_fusion_paste_mode(monkeypatch, qapp):
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

    asset = {
        "id": "macro_title",
        "name": "RF Kinetic Macro",
        "category": "titles",
        "file_path": "/path/to/macro.setting",
    }

    result = service.insert(asset, mode="fusion_paste")
    assert result.ok
    assert result.track == "Fusion"
    assert result.item_name == "RF Kinetic Macro"
    assert "/path/to/macro.setting" in called_with

    gateway.shutdown()


def test_insert_service_audio_auto_mode_free_track(qapp):
    """When Track A2 is busy, auto mode should select Track A3."""
    timeline = MockTimelineWithTracks()
    # A1 is dialogue, add clip to A2 making it busy
    timeline.tracks_audio[2] = [MockTimelineItem("Busy SFX A2", start=0, duration=200)]
    timeline.tracks_audio[3] = []  # A3 is empty

    resolve = MockResolveApp(timeline)
    resolve.mock_insert_sfx = lambda path, track: True

    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    asset = {
        "id": "whoosh_01",
        "name": "Whoosh Swell",
        "category": "sfx",
        "file_path": "/path/to/whoosh.wav",
    }

    result = service.insert(asset, mode="auto")
    assert result.ok
    assert result.track == "A3", f"Expected Track A3 since A2 is busy, got {result.track}"
    assert result.item_name == "Whoosh Swell"

    gateway.shutdown()


def test_insert_service_audio_exhausted_tracks_error(qapp):
    """When all allowed tracks are busy, auto mode should fail gracefully."""
    timeline = MockTimelineWithTracks()
    # Occupy all tracks 1, 2, 3, 4
    for t in (1, 2, 3, 4):
        timeline.tracks_audio[t] = [MockTimelineItem(f"Busy {t}", start=0, duration=200)]

    resolve = MockResolveApp(timeline)
    resolve.mock_insert_sfx = lambda path, track: True

    gateway = ResolveGateway(resolve_override=resolve)
    service = InsertService(gateway)

    asset = {
        "id": "whoosh_02",
        "name": "Heavy Impact",
        "category": "sfx",
        "file_path": "/path/to/impact.wav",
    }

    result = service.insert(asset, mode="auto")
    assert not result.ok
    assert "Hết track" in result.error or "trống" in result.error

    gateway.shutdown()
