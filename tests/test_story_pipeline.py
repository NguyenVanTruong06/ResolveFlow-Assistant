import os
import pytest
from unittest.mock import patch, MagicMock

from src.core.story_pipeline import (
    ChapterResult,
    MasterStoryResult,
    MasterStoryPipeline
)
from src.core.vlog_hook import HookSegment
from src.core.folder_scanner import FolderGroup


def test_process_chapter_group_basic():
    grp = FolderGroup(
        name="01_DiChuyen",
        rel_path="01_DiChuyen",
        abs_path="/tmp/01_DiChuyen",
        video_paths=["/tmp/01_DiChuyen/clip1.mp4", "/tmp/01_DiChuyen/clip2.mp4"],
        chapter_order=1,
        role_hint="intro"
    )

    subs = {
        "/tmp/01_DiChuyen/clip1.mp4": [{"start": 1.0, "end": 4.0, "text": "Hôm nay chúng ta bắt đầu chuyến đi!"}]
    }

    with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
        ch_res = MasterStoryPipeline.process_chapter_group(
            group=grp,
            subtitles_by_video=subs,
            clip_duration=2.5
        )

        assert ch_res.chapter_name == "01_DiChuyen"
        assert ch_res.chapter_index == 1
        assert len(ch_res.events) == 2
        assert ch_res.total_duration == 20.0
        assert len(ch_res.hook_candidates) == 2
        assert len(ch_res.subtitles) == 1
        assert len(ch_res.markers) == 2


def test_select_global_hook():
    c1 = HookSegment(
        video_path="clip1.mp4",
        src_in=0.0,
        src_out=2.5,
        duration=2.5,
        score=4.0,
        reason="Đoạn mở đầu"
    )
    c2 = HookSegment(
        video_path="clip_climax.mp4",
        src_in=10.0,
        src_out=15.0,
        duration=5.0,
        score=9.5,
        reason="Cảnh cây tùng khổng lồ và suýt ngã!",
        text="Ôi không thể tin được nhìn kìa!"
    )
    c3 = HookSegment(
        video_path="clip3.mp4",
        src_in=5.0,
        src_out=8.0,
        duration=3.0,
        score=6.0,
        reason="Cảnh qua cầu"
    )

    best_hook = MasterStoryPipeline.select_global_hook([c1, c2, c3], target_hook_len=4.0)
    assert best_hook is not None
    assert best_hook.video_path == "clip_climax.mp4"
    assert best_hook.score == 9.5
    assert best_hook.duration == 4.0
    assert "Ôi không thể tin được" in best_hook.text


def test_assemble_master_timeline():
    # Chapter 1
    ch1 = ChapterResult(
        chapter_name="01_ChuanBi",
        chapter_index=0,
        video_paths=["ch1_v1.mp4"],
        events=[{"video_path": "ch1_v1.mp4", "src_in": 0.0, "src_out": 10.0, "rec_in": 0.0, "rec_out": 10.0}],
        subtitles=[{"start": 1.0, "end": 4.0, "text": "Bắt đầu xuất phát"}],
        markers=[{"time": 0.0, "name": "Start Ch1"}],
        hook_candidates=[HookSegment(video_path="ch1_v1.mp4", src_in=1.0, src_out=3.0, duration=2.0, score=5.0, reason="Xuất phát")],
        total_duration=10.0,
        role_hint="intro"
    )

    # Chapter 2 (chứa khoảnh khắc kịch tính nhất)
    ch2 = ChapterResult(
        chapter_name="02_CayTung",
        chapter_index=1,
        video_paths=["ch2_v1.mp4"],
        events=[{"video_path": "ch2_v1.mp4", "src_in": 0.0, "src_out": 20.0, "rec_in": 0.0, "rec_out": 20.0}],
        subtitles=[{"start": 5.0, "end": 9.0, "text": "Wow cây tùng khổng lồ!"}],
        markers=[{"time": 0.0, "name": "Start Ch2"}],
        hook_candidates=[HookSegment(video_path="ch2_v1.mp4", src_in=5.0, src_out=9.0, duration=4.0, score=10.0, reason="Cực đỉnh", text="Wow cây tùng khổng lồ!")],
        total_duration=20.0,
        role_hint="climax"
    )

    master = MasterStoryPipeline.assemble_master_timeline(
        project_name="Vlog_Chuyen_Di",
        chapters=[ch1, ch2],
        enable_final_hook=True,
        hook_duration=4.0
    )

    # Global Hook được chọn từ Chapter 2 và đưa lên Frame 0:00 của Master Timeline
    assert master.global_hook is not None
    assert master.global_hook.video_path == "ch2_v1.mp4"
    
    # Event đầu tiên là Global Hook
    assert master.master_events[0]["is_hook"] is True
    assert master.master_events[0]["rec_in"] == 0.0
    assert master.master_events[0]["rec_out"] == 4.0

    # Tiếp theo là Chapter 1 (từ giây 4.0 đến 14.0)
    assert master.master_events[1]["rec_in"] == 4.0
    assert master.master_events[1]["rec_out"] == 14.0

    # Tiếp theo là Chapter 2 (từ giây 14.0 đến 34.0)
    assert master.master_events[2]["rec_in"] == 14.0
    assert master.master_events[2]["rec_out"] == 34.0

    # Tổng thời lượng = Hook (4s) + Ch1 (10s) + Ch2 (20s) = 34s
    assert master.total_duration == 34.0
    assert any(m["color"] == "Magenta" for m in master.master_markers)
