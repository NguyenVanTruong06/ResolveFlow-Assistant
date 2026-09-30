import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent, QPaintEvent, QRegion
from src.ui.widgets.mini_timeline import (
    MiniTimelineWidget,
    TimelineBlock,
    TimelineBlockType
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


@pytest.fixture
def timeline_widget(qapp):
    w = MiniTimelineWidget()
    w.resize(600, 150)
    yield w
    w.deleteLater()


def test_timeline_block_properties():
    b = TimelineBlock(
        start_sec=5.0,
        end_sec=12.5,
        block_type=TimelineBlockType.VOICE,
        label="Test Talk",
        track_index=0
    )
    assert b.start_sec == 5.0
    assert b.end_sec == 12.5
    assert b.duration == 7.5
    assert b.block_type == TimelineBlockType.VOICE


def test_mini_timeline_initial_defaults(timeline_widget):
    w = timeline_widget
    assert w.duration == 60.0
    assert len(w.blocks) > 0
    assert w.stats_kept_dur > 0
    assert w.stats_cut_dur > 0


def test_mini_timeline_set_duration_and_clear(timeline_widget):
    w = timeline_widget
    w.set_duration(120.0, fps=60.0)
    assert w.duration == 120.0
    assert w.fps == 60.0

    w.clear()
    assert len(w.blocks) == 0
    assert w.stats_kept_dur == 0.0
    assert w.stats_cut_dur == 0.0


def test_mini_timeline_set_data_with_keep_intervals(timeline_widget):
    w = timeline_widget
    keeps = [(5.0, 15.0), (20.0, 30.0)]
    w.set_timeline_data(duration=40.0, keep_intervals=keeps)

    assert w.duration == 40.0
    # Should have 4 blocks: [0-5 Cut], [5-15 Voice], [15-20 Cut], [20-30 Voice], [30-40 Cut]
    voice_blocks = [b for b in w.blocks if b.block_type == TimelineBlockType.VOICE]
    cut_blocks = [b for b in w.blocks if b.block_type == TimelineBlockType.SILENCE_CUT]

    assert len(voice_blocks) == 2
    assert len(cut_blocks) == 3
    assert w.stats_kept_dur == 20.0
    assert w.stats_cut_dur == 20.0


def test_mini_timeline_set_data_with_speedup_and_teasers(timeline_widget):
    w = timeline_widget
    speedups = [
        {"start": 0.0, "end": 5.0, "type": "speedup"},
        {"start": 5.0, "end": 20.0, "type": "voice"}
    ]
    teasers = [
        {"src_in": 10.0, "src_out": 14.0, "order": 1}
    ]
    subs = [
        {"start": 6.0, "end": 9.0, "text": "Hello world"}
    ]
    w.set_timeline_data(
        duration=25.0,
        speedup_segments=speedups,
        teaser_items=teasers,
        subtitles=subs
    )

    assert w.stats_speedup_dur == 5.0
    assert w.stats_kept_dur == 15.0
    assert any(b.block_type == TimelineBlockType.HOOK_TEASER for b in w.blocks)
    assert any(b.block_type == TimelineBlockType.SUBTITLE for b in w.blocks)


def test_mini_timeline_time_coordinate_conversions(timeline_widget):
    w = timeline_widget
    w.set_duration(100.0)

    # _time_to_x at t=0.0 -> margin_left
    x0 = w._time_to_x(0.0, width=500.0, margin_left=10, margin_right=10)
    assert x0 == 10.0

    # _time_to_x at t=50.0 -> midpoint (250.0)
    x50 = w._time_to_x(50.0, width=500.0, margin_left=10, margin_right=10)
    assert x50 == 250.0

    # Inverse _x_to_time at x=250.0 -> 50.0
    t = w._x_to_time(250.0, width=500.0, margin_left=10, margin_right=10)
    assert abs(t - 50.0) < 0.001


def test_mini_timeline_playhead_scrubbing(timeline_widget):
    w = timeline_widget
    w.set_duration(60.0)

    signals = []
    w.playhead_changed.connect(lambda t: signals.append(t))

    w.set_playhead_seconds(15.5)
    assert w.playhead_sec == 15.5

    # Simulate mouse press to scrub
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(200.0, 50.0),
        QPointF(200.0, 50.0),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    w.mousePressEvent(event)
    assert w.is_scrubbing is True
    assert len(signals) == 1

    # Simulate mouse release
    rel_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(200.0, 50.0),
        QPointF(200.0, 50.0),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    w.mouseReleaseEvent(rel_event)
    assert w.is_scrubbing is False


def test_mini_timeline_update_proposed_segments(timeline_widget):
    w = timeline_widget
    w.set_timeline_data(duration=30.0, keep_intervals=[(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)])

    class MockProposed:
        def __init__(self, start, end, decision, approved):
            self.start = start
            self.end = end
            self.decision = decision
            self.approved = approved

    # Propose to cut block [10-20]
    props = [
        MockProposed(0.0, 10.0, "keep", True),
        MockProposed(10.0, 20.0, "cut", True)
    ]
    w.update_proposed_segments(props)

    # Block at 10.0 should now be SILENCE_CUT
    b10 = next(b for b in w.blocks if abs(b.start_sec - 10.0) < 0.2)
    assert b10.block_type == TimelineBlockType.SILENCE_CUT


def test_mini_timeline_paint_event_no_crash(timeline_widget):
    w = timeline_widget
    w.set_duration(50.0)
    # Trigger paintEvent directly via render / repaint
    w.repaint()
    assert True
