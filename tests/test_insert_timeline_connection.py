"""
Regression: chèn B-roll/Text báo "No active timeline" sai khi handle Resolve đã cũ
(DaVinci khởi động lại / đổi project) hoặc khi project chưa có Timeline.
"""
from unittest.mock import MagicMock

from src.core.resolve_api import ResolveAutomation
from src.services.insert_service import _no_timeline_message


def _live_resolve(timeline=None, tl_count=1, name="Proj"):
    proj = MagicMock()
    proj.GetCurrentTimeline.return_value = timeline
    proj.GetTimelineCount.return_value = tl_count
    proj.GetName.return_value = name
    proj.GetMediaPool.return_value = None
    pm = MagicMock()
    pm.GetCurrentProject.return_value = proj
    resolve = MagicMock()
    resolve.GetProjectManager.return_value = pm
    resolve.GetCurrentPage.return_value = "edit"
    return resolve, proj


def test_stale_handle_reconnects_and_returns_timeline(monkeypatch):
    tl = MagicMock(name="timeline")
    fresh, fresh_proj = _live_resolve(timeline=tl)

    auto = ResolveAutomation()
    stale = MagicMock()
    stale.GetProjectManager.return_value = None  # handle chết
    auto.resolve = stale
    auto.current_project = MagicMock()  # project cũ còn cache

    def fake_connect(timeout=2.0):
        auto.resolve = fresh
        return True

    monkeypatch.setattr(auto, "connect", fake_connect)
    assert auto.get_active_timeline() is tl
    assert auto.resolve is fresh
    assert auto.current_project is fresh_proj
    assert auto.last_timeline_error == ""


def test_project_without_timeline_auto_creates_timeline():
    resolve, proj = _live_resolve(timeline=None, tl_count=0, name="New Project")
    mp = MagicMock()
    created_tl = MagicMock(name="created_tl")
    mp.CreateEmptyTimeline.return_value = created_tl
    proj.GetMediaPool.return_value = mp

    def fake_set_tl(tl):
        proj.GetCurrentTimeline.return_value = tl
    proj.SetCurrentTimeline.side_effect = fake_set_tl

    auto = ResolveAutomation()
    auto.resolve = resolve
    tl = auto.get_active_timeline(auto_create=True)
    assert tl is created_tl
    mp.CreateEmptyTimeline.assert_called_once_with("Timeline 1")
    proj.SetCurrentTimeline.assert_called_once_with(created_tl)


def test_project_without_timeline_gives_clear_message(monkeypatch):
    resolve, _ = _live_resolve(timeline=None, tl_count=0, name="Untitled Project 14")
    auto = ResolveAutomation()
    auto.resolve = resolve
    assert auto.get_active_timeline(auto_create=False) is None
    assert "Untitled Project 14" in auto.last_timeline_error
    assert "chưa có Timeline" in auto.last_timeline_error
    assert _no_timeline_message(auto) == auto.last_timeline_error


def test_not_connected_gives_connection_message(monkeypatch):
    auto = ResolveAutomation()
    monkeypatch.setattr(auto, "connect", lambda timeout=2.0: False)
    assert auto.get_active_timeline() is None
    assert "Chưa kết nối được DaVinci Resolve" in auto.last_timeline_error


def test_default_message_without_automation():
    assert "Timeline" in _no_timeline_message(None)
