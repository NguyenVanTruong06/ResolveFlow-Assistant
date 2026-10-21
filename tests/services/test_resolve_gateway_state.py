"""
tests/services/test_resolve_gateway_state.py
Tests for ResolveGateway and ResolveStateService:
- Gateway runs tasks sequentially on dedicated thread without blocking.
- Connection signal and retry behavior.
- ResolveStateService: page_changed is NOT emitted if page remains unchanged.
"""
import time
import pytest
from PySide6.QtCore import QCoreApplication

from src.services.resolve_gateway import ResolveGateway
from src.services.resolve_state import ResolveStateService


class StubTimeline:
    def __init__(self, name="Timeline 1", tc="01:00:00:00"):
        self.name = name
        self.tc = tc

    def GetName(self):
        return self.name

    def GetCurrentTimecode(self):
        return self.tc


class StubProject:
    def __init__(self, timeline=None):
        self.timeline = timeline or StubTimeline()

    def GetCurrentTimeline(self):
        return self.timeline


class StubProjectManager:
    def __init__(self, project=None):
        self.project = project or StubProject()

    def GetCurrentProject(self):
        return self.project


class StubResolve:
    def __init__(self, page="edit"):
        self.page = page
        self.pm = StubProjectManager()

    def GetCurrentPage(self):
        return self.page

    def GetProjectManager(self):
        return self.pm


@pytest.fixture
def qapp():
    app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication([])
    return app


def test_gateway_sequential_execution(qapp):
    """Verifies that submitted tasks execute sequentially on the worker thread."""
    gateway = ResolveGateway()
    execution_order = []

    def task_1():
        time.sleep(0.05)
        execution_order.append(1)
        return "result_1"

    def task_2():
        execution_order.append(2)
        return "result_2"

    res_1 = gateway.submit_sync(task_1)
    res_2 = gateway.submit_sync(task_2)

    assert res_1 == "result_1"
    assert res_2 == "result_2"
    assert execution_order == [1, 2], "Tasks must run in the exact submitted order"

    gateway.shutdown()


def test_state_service_no_duplicate_page_signal(qapp):
    """Verifies that ResolveStateService only emits page_changed when page actually changes."""
    mock_resolve = StubResolve(page="edit")

    gateway = ResolveGateway(resolve_override=mock_resolve)
    state_service = ResolveStateService(gateway, poll_interval_ms=50)

    page_changes = []
    tc_changes = []

    state_service.page_changed.connect(lambda p: page_changes.append(p))
    state_service.playhead_changed.connect(lambda tc: tc_changes.append(tc))

    # Process events for 200ms (multiple poll ticks)
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.02)

    # First detection sets page to 'edit' -> should emit exactly ONCE
    assert page_changes == ["edit"], f"Expected exactly ['edit'], got {page_changes}"

    # Now change page to 'fusion'
    mock_resolve.page = "fusion"

    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.02)

    assert page_changes == ["edit", "fusion"], f"Expected ['edit', 'fusion'], got {page_changes}"

    # Verify timecode change
    mock_resolve.pm.project.timeline.tc = "01:00:05:00"

    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.02)

    assert "01:00:05:00" in tc_changes

    gateway.shutdown()


def test_state_service_stops_when_disconnected(qapp):
    """Verifies that polling stops when connection is lost."""
    mock_resolve = StubResolve(page="edit")
    gateway = ResolveGateway(resolve_override=mock_resolve)
    state_service = ResolveStateService(gateway, poll_interval_ms=50)

    # Allow worker initialization events to process
    for _ in range(5):
        qapp.processEvents()
        time.sleep(0.02)
    assert state_service.is_connected

    # Disconnect
    gateway.set_resolve_override(None)
    for _ in range(5):
        qapp.processEvents()
        time.sleep(0.02)

    assert not state_service.is_connected
    assert not state_service._timer.isActive()

    gateway.shutdown()
