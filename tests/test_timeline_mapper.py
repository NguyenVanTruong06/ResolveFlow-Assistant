"""
tests/test_timeline_mapper.py
Unit tests cho Giai đoạn 0B: TimelineMapper, Speed Mode, FCP7 XML Time Remap và đồng bộ phụ đề.
"""

import os
import tempfile
import xml.etree.ElementTree as ET
from fractions import Fraction
import pytest

from src.core.timeline_mapper import (
    TimelineMapper,
    PlacedSegment,
    add_subtitles_for_range,
    get_speed_mode,
    set_speed_mode
)
from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.story_copilot import (
    StoryCopilot,
    CopilotDirectorPlan,
    CopilotSegmentPlan,
    CopilotHookPlan
)


# =========================================================================
# TEST 1: Tính liên tục của cursor với 50 segments qua nhiều FPS khác nhau
# =========================================================================
def test_continuity_50_segments_multiple_fps():
    """
    50 segments, dur 1.2–5.8s, alternating speed 1.0/1.5, fps 30, 29.97, 24
    -> rec_in[k+1] == rec_out[k], tổng thời lượng khớp chính xác từng frame.
    """
    for fps in [30.0, 29.97, 24.0]:
        mapper = TimelineMapper(fps=fps, speed_mode="timeremap")

        for i in range(50):
            dur = 1.2 + (i % 5) * 1.15  # dao động 1.2s -> 5.8s
            speed = 1.0 if (i % 2 == 0) else 1.5
            mapper.add_segment(
                clip_id=f"C{i:02d}",
                src_in_sec=10.0,
                src_out_sec=10.0 + dur,
                speed=speed,
                role="build" if i % 2 == 0 else "climax",
                pad_sec=0.3,
                clip_duration_sec=60.0
            )

        assert len(mapper.segments) == 50

        # Kiểm tra tính liên tục tuyệt đối: rec_in[k+1] == rec_out[k]
        for k in range(len(mapper.segments) - 1):
            cur_seg = mapper.segments[k]
            next_seg = mapper.segments[k + 1]
            assert cur_seg.rec_out_frames == next_seg.rec_in_frames, (
                f"Lệch frame tại segment {k} -> {k+1} ở FPS {fps}: "
                f"{cur_seg.rec_out_frames} != {next_seg.rec_in_frames}"
            )
            assert cur_seg.rec_dur_frames == (cur_seg.rec_out_frames - cur_seg.rec_in_frames)

        # Tổng thời lượng khớp tuyệt đối
        total_frames = mapper.get_total_duration_frames()
        assert total_frames == mapper.segments[-1].rec_out_frames
        assert total_frames == sum(s.rec_dur_frames for s in mapper.segments)


# =========================================================================
# TEST 2: Đồng bộ từ ngữ giữa add_subtitles_for_range và get_final_cut_words
# =========================================================================
def test_word_timestamps_match_between_subtitles_and_review_page(monkeypatch, tmp_path):
    """
    Mốc thời gian từng từ trích xuất từ convert_plan_to_resolve_timeline
    phải bằng 100% mốc thời gian từ get_final_cut_words cho cùng một kịch bản.
    """
    clip_path = str(tmp_path / "test_clip_01.mp4")
    with open(clip_path, "wb") as f:
        f.write(b"dummy_video_bytes")

    # Giả lập cached scan Whisper có word timestamps
    raw_subs = [
        {
            "start": 10.5,
            "end": 14.0,
            "text": "Chào mừng các bạn đã đến",
            "words": [
                {"word": "Chào", "start": 10.5, "end": 11.0},
                {"word": "mừng", "start": 11.0, "end": 11.8},
                {"word": "các", "start": 11.8, "end": 12.4},
                {"word": "bạn", "start": 12.4, "end": 13.0},
                {"word": "đã", "start": 13.0, "end": 13.4},
                {"word": "đến", "start": 13.4, "end": 14.0},
            ]
        }
    ]

    mock_cache = {
        "clip_dur": 60.0,
        "raw_subtitles": raw_subs
    }

    # Mock ScanCacheManager
    from src.core.cache_manager import ScanCacheManager
    monkeypatch.setattr(ScanCacheManager, "get_cached_scan", lambda self, *args, **kwargs: mock_cache)

    # Mock get_media_metadata
    from src.core import autocut
    monkeypatch.setattr(autocut, "get_media_metadata", lambda p: {"fps": 30.0, "duration": 60.0, "has_audio": True})

    # 1. Tạo plan và gọi convert_plan_to_resolve_timeline
    plan = CopilotDirectorPlan(
        strategy_summary="Test sync",
        target_platform="YouTube",
        timeline_segments=[
            CopilotSegmentPlan(
                clip_index=1,
                clip_name=os.path.basename(clip_path),
                start_sec=10.0,
                end_sec=15.0,
                speed=1.0,
                role="intro",
                chapter_name="Intro"
            )
        ]
    )

    vpaths = {1: clip_path}
    subs_map = {clip_path: raw_subs}

    _, subtitles, _ = StoryCopilot.convert_plan_to_resolve_timeline(
        plan=plan,
        video_paths_by_index=vpaths,
        subtitles_by_path=subs_map,
        fps=30.0
    )

    extracted_sub_words = []
    for s in subtitles:
        for w in s.get("words", []):
            extracted_sub_words.append(w)

    # 2. Gọi get_final_cut_words trên ReviewPage
    from src.ui.director.pages.review_page import ReviewPage
    from unittest.mock import MagicMock

    review_page = ReviewPage.__new__(ReviewPage)
    review_page._clip_infos = [clip_path]
    review_page._whisper_model = "small"
    review_page._whisper_lang = "vi"
    review_page._video_plan_data = {
        "timeline_segments": [
            {
                "clip_name": os.path.basename(clip_path),
                "start_sec": 10.0,
                "end_sec": 15.0,
                "speed": 1.0,
                "role": "intro"
            }
        ]
    }

    review_page._prepared_kinetic_snapshot = {
        "words": extracted_sub_words
    }

    final_cut_words = review_page.get_final_cut_words()

    # So sánh từng từ: số lượng, từ vựng và start/end phải khớp chính xác
    assert len(extracted_sub_words) == len(final_cut_words)
    for sw, fw in zip(extracted_sub_words, final_cut_words):
        assert sw["word"] == fw["word"]
        assert abs(sw["start"] - fw["start"]) < 1e-3, f"Lệch start: {sw['start']} vs {fw['start']}"
        assert abs(sw["end"] - fw["end"]) < 1e-3, f"Lệch end: {sw['end']} vs {fw['end']}"


# =========================================================================
# TEST 3: speed_mode="off" ép tốc độ về 1.0 và ghi log warning
# =========================================================================
def test_speed_mode_off_forces_1_0_and_logs_warning():
    """
    speed_mode="off": segment speed 1.5 -> forced to 1.0 + warning logged;
    subtitles tính theo tốc độ 1.0x.
    """
    mapper = TimelineMapper(fps=30.0, speed_mode="off")
    seg = mapper.add_segment(
        clip_id="C01",
        src_in_sec=10.0,
        src_out_sec=14.0,
        speed=1.5,
        pad_sec=0.0,
        clip_duration_sec=60.0
    )

    # Tốc độ áp dụng phải bị ép về 1.0
    assert seg.applied_speed == 1.0
    assert seg.src_dur_frames == 120  # 4s * 30fps
    assert seg.rec_dur_frames == 120  # bằng src_dur_frames vì 1.0x
    assert len(mapper.warnings) > 0
    assert "speed_mode='off'" in mapper.warnings[0]

    # Phụ đề tính toán theo tốc độ 1.0x
    raw_sub = [{"start": 10.0, "end": 14.0, "text": "Đoạn nói chuẩn", "words": []}]
    mapped_subs = mapper.map_segment_subtitles(0, raw_sub)
    assert len(mapped_subs) == 1
    # Duration trên timeline phải đúng 4.0s (không phải 4.0 / 1.5 = 2.67s)
    assert abs((mapped_subs[0]["end"] - mapped_subs[0]["start"]) - 4.0) < 0.05


# =========================================================================
# TEST 4: speed_mode="timeremap" tạo filter Time Remap trong FCP7 XML
# =========================================================================
def test_speed_mode_timeremap_fcp7_xml_filter(tmp_path):
    """
    speed_mode="timeremap": FCP7 XML chứa <filter> timeremap với speed 150,
    duration = round(src_dur_f / 1.5).
    """
    dummy_mov = tmp_path / "test_speed.mov"
    dummy_mov.write_bytes(b"dummy")

    mapper = TimelineMapper(fps=30.0, speed_mode="timeremap")
    mapper.add_segment(
        clip_id="C01",
        src_in_sec=10.0,
        src_out_sec=13.0,
        speed=1.5,
        pad_sec=0.0,
        clip_duration_sec=60.0,
        clip_name="test_speed.mov",
        file_path=str(dummy_mov)
    )

    events = mapper.to_events()
    out_xml = tmp_path / "speed_fcp7.xml"

    # Metadata giả lập
    clip_db = {
        str(dummy_mov): {
            "name": "test_speed.mov",
            "path": str(dummy_mov),
            "duration": 60.0,
            "dur_frames": 1800,
            "has_audio": True,
            "start_tc_frames": 0,
            "start_tc_str": "00:00:00:00",
            "tc_format": "NDF"
        }
    }

    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=str(out_xml),
        fps=30.0,
        speed_mode="timeremap",
        clip_metadata_db=clip_db
    )

    tree = ET.parse(out_xml)
    root = tree.getroot()

    # Kiểm tra tổng thời lượng sequence: 90 frames / 1.5 = 60 frames
    seq_dur = int(root.find(".//sequence/duration").text)
    assert seq_dur == 60

    # Tìm clipitem video
    v_clip = root.find(".//video/track/clipitem")
    assert v_clip is not None
    assert int(v_clip.find("start").text) == 0
    assert int(v_clip.find("end").text) == 60
    assert int(v_clip.find("in").text) == 300   # 10s * 30
    assert int(v_clip.find("out").text) == 390  # 13s * 30 (dur 90)

    # Kiểm tra <filter> Time Remap
    filter_el = v_clip.find("filter")
    assert filter_el is not None
    effect_el = filter_el.find("effect")
    assert effect_el.find("effectid").text == "timeremap"

    # Kiểm tra speed parameter = 150
    speed_param = None
    for p in effect_el.findall("parameter"):
        if p.find("parameterid").text == "speed":
            speed_param = p
            break
    assert speed_param is not None
    assert speed_param.find("value").text == "150"


# =========================================================================
# TEST 5: Padding 0.3s giới hạn ở biên clip (Clamping)
# =========================================================================
def test_pad_clamping_clip_start_and_end():
    """
    0.3s padding clamping:
    - Tại đầu clip (src_in=0.1) -> src_in_f = 0
    - Tại cuối clip (src_out=59.9, dur=60.0) -> src_out_f = 1800 (clamped).
    """
    mapper = TimelineMapper(fps=30.0)

    # Segment tại đầu clip
    seg1 = mapper.add_segment(
        clip_id="C01",
        src_in_sec=0.1,
        src_out_sec=5.0,
        pad_sec=0.3,
        clip_duration_sec=60.0
    )
    # 0.1 - 0.3 = -0.2s -> không được âm, phải clamp về 0
    assert seg1.src_in_frames == 0

    # Segment tại cuối clip
    seg2 = mapper.add_segment(
        clip_id="C02",
        src_in_sec=50.0,
        src_out_sec=59.9,
        pad_sec=0.3,
        clip_duration_sec=60.0
    )
    # 59.9 + 0.3 = 60.2s -> không được vượt quá 60.0s (1800 frames), phải clamp về 1800
    assert seg2.src_out_frames == 1800


# =========================================================================
# TEST 6: Snapshot test và kiểm tra XML cú pháp hợp lệ
# =========================================================================
def test_xml_parses_cleanly_and_snapshot_3_segments(tmp_path):
    """
    Sinh FCP7 XML và FCPXML v1.9 cho kế hoạch 3 segment (1.0x, 2.0x, 0.5x),
    khẳng định cú pháp parse XML hợp lệ và snapshot các giá trị quan trọng.
    """
    mov_path = str(tmp_path / "sample.mov")
    with open(mov_path, "wb") as f:
        f.write(b"sample_content")

    mapper = TimelineMapper(fps=30.0, speed_mode="timeremap")
    mapper.add_segment("C1", 0.0, 1.0, speed=1.0, pad_sec=0.0, clip_duration_sec=10.0, file_path=mov_path)
    mapper.add_segment("C2", 1.0, 2.0, speed=2.0, pad_sec=0.0, clip_duration_sec=10.0, file_path=mov_path)
    mapper.add_segment("C3", 2.0, 3.0, speed=0.5, pad_sec=0.0, clip_duration_sec=10.0, file_path=mov_path)

    events = mapper.to_events()
    assert len(events) == 3

    # 1. Kiểm tra FCP7 XML
    fcp7_path = str(tmp_path / "snapshot_fcp7.xml")
    clip_db = {
        mov_path: {
            "name": "sample.mov",
            "path": mov_path,
            "duration": 10.0,
            "dur_frames": 300,
            "has_audio": True,
            "start_tc_frames": 0,
            "start_tc_str": "00:00:00:00",
            "tc_format": "NDF"
        }
    }
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=fcp7_path,
        fps=30.0,
        speed_mode="timeremap",
        clip_metadata_db=clip_db
    )

    tree7 = ET.parse(fcp7_path)
    root7 = tree7.getroot()
    assert root7.tag == "xmeml"
    clipitems = root7.findall(".//video/track/clipitem")
    assert len(clipitems) == 3

    # Segment 1 (1.0x): 30 frames
    assert clipitems[0].find("start").text == "0"
    assert clipitems[0].find("end").text == "30"
    assert clipitems[0].find("filter") is None

    # Segment 2 (2.0x): 15 frames
    assert clipitems[1].find("start").text == "30"
    assert clipitems[1].find("end").text == "45"
    assert clipitems[1].find("filter") is not None

    # Segment 3 (0.5x): 60 frames
    assert clipitems[2].find("start").text == "45"
    assert clipitems[2].find("end").text == "105"
    assert clipitems[2].find("filter") is not None

    # 2. Kiểm tra FCPXML v1.9
    fcpxml_path = str(tmp_path / "snapshot_fcpxml.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=fcpxml_path,
        fps=30.0,
        clip_metadata_db=clip_db
    )

    tree_fcpxml = ET.parse(fcpxml_path)
    root_fcpxml = tree_fcpxml.getroot()
    assert root_fcpxml.tag == "fcpxml"
    asset_clips = root_fcpxml.findall(".//spine/asset-clip")
    assert len(asset_clips) == 3


# =========================================================================
# TEST 7: Regression quantization half-even điểm nửa nhạy phép trừ
# =========================================================================
def test_add_segment_subtraction_half_even_tie_regression():
    """
    Probe Codex (G01-01B2-Q-I): src_in_sec=0.35, pad_sec=0.1, FPS=30.
    Toán học:
      Fraction('0.35') - Fraction('0.1') = Fraction('0.25') = 1/4 s.
      1/4 * 30 = 15/2 = 7.5 frames -> half-even: 8 frames.
    Mã float cũ:
      (0.35 - 0.1) * 30.0 = 0.24999999999999997 * 30 = 7.499999999999999 -> round() cho 7.
    Sau khi lượng tử hóa bằng Fraction trước pts_to_frame:
      src_in_frames bắt buộc là 8!
    """
    mapper = TimelineMapper(fps=30.0)
    seg = mapper.add_segment(
        clip_id="TIE_PROBE",
        src_in_sec=0.35,
        src_out_sec=5.0,
        pad_sec=0.1,
    )
    assert seg.src_in_frames == 8, f"Kỳ vọng 8 (half-even trên 7.5), nhận được {seg.src_in_frames}"


def test_add_segment_explicit_fraction_fps_and_50_segments_continuity():
    """
    Kiểm tra TimelineMapper với Fraction explicit (vd: 2997/100) qua 50 segments:
    - rec_in[k+1] == rec_out[k]
    - cursor_frames == sum(rec_dur_frames)
    - rec_dur_frames >= 1
    """
    frac_fps = Fraction(2997, 100)
    mapper = TimelineMapper(fps=frac_fps, speed_mode="timeremap")

    for i in range(50):
        dur = 1.0 + (i % 3) * 0.5
        mapper.add_segment(
            clip_id=f"C{i:02d}",
            src_in_sec=1.0,
            src_out_sec=1.0 + dur,
            speed=1.0,
            pad_sec=0.1,
            clip_duration_sec=30.0
        )

    assert len(mapper.segments) == 50
    for k in range(len(mapper.segments) - 1):
        assert mapper.segments[k].rec_out_frames == mapper.segments[k + 1].rec_in_frames
        assert mapper.segments[k].rec_dur_frames >= 1

    assert mapper.get_total_duration_frames() == mapper.segments[-1].rec_out_frames
    assert mapper.get_total_duration_frames() == sum(s.rec_dur_frames for s in mapper.segments)


def test_add_segment_rejects_nan_and_inf_inputs():
    """
    TimelineMapper.add_segment ném ValueError khi nhận NaN hoặc Inf,
    không thêm segment và không dịch chuyển cursor_frames.
    """
    mapper = TimelineMapper(fps=30.0)
    assert mapper.cursor_frames == 0

    with pytest.raises(ValueError, match="src_in_sec"):
        mapper.add_segment("ERR", float("nan"), 5.0)
    assert mapper.cursor_frames == 0
    assert len(mapper.segments) == 0

    with pytest.raises(ValueError, match="src_out_sec"):
        mapper.add_segment("ERR", 1.0, float("inf"))
    assert mapper.cursor_frames == 0
    assert len(mapper.segments) == 0

    with pytest.raises(ValueError, match="pad_sec"):
        mapper.add_segment("ERR", 1.0, 5.0, pad_sec=float("nan"))
    assert mapper.cursor_frames == 0
    assert len(mapper.segments) == 0


# =========================================================================
# TEST 8: G01-01B2-M-I Đồng nhất lượng tử hóa trong src_to_timeline
# =========================================================================
def test_src_to_timeline_fraction_half_even_probe():
    """
    Probe Codex (G01-01B2-M-I):
    m = TimelineMapper(fps=Fraction(24000, 1001), speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)
    t = Fraction(3003, 16000)
    Toán học:
      t * fps = (3003 / 16000) * (24000 / 1001) = 9/2 = 4.5.
      Làm tròn half-even trên 4.5 (giữa 4 chẵn và 5 lẻ) bắt buộc là 4.
    Mã float cũ:
      float(t) * self.fps = 4.500000000000001 -> round() cho 5 (SAI).
    Sau khi dùng RationalFPS + pts_to_frame:
      Kỳ vọng phải là 4!
    """
    m = TimelineMapper(fps=Fraction(24000, 1001), speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)
    t = Fraction(3003, 16000)
    assert m.src_to_timeline(0, t) == 4


@pytest.mark.parametrize(
    "fps_frac,half_points",
    [
        (
            Fraction(24000, 1001),
            [
                (Fraction(9, 2), 4),    # 4.5 -> 4
                (Fraction(11, 2), 6),   # 5.5 -> 6
                (Fraction(13, 2), 6),   # 6.5 -> 6
                (Fraction(15, 2), 8),   # 7.5 -> 8
                (Fraction(17, 2), 8),   # 8.5 -> 8
            ]
        ),
        (
            Fraction(30000, 1001),
            [
                (Fraction(9, 2), 4),    # 4.5 -> 4
                (Fraction(11, 2), 6),   # 5.5 -> 6
                (Fraction(13, 2), 6),   # 6.5 -> 6
                (Fraction(15, 2), 8),   # 7.5 -> 8
                (Fraction(17, 2), 8),   # 8.5 -> 8
            ]
        ),
        (
            Fraction(24, 1),
            [
                (Fraction(9, 2), 4),    # 4.5 -> 4
                (Fraction(11, 2), 6),   # 5.5 -> 6
                (Fraction(13, 2), 6),   # 6.5 -> 6
                (Fraction(15, 2), 8),   # 7.5 -> 8
                (Fraction(17, 2), 8),   # 8.5 -> 8
            ]
        ),
    ]
)
def test_src_to_timeline_parametrized_half_frames(fps_frac, half_points):
    """
    Parametrize exact source half-frame PTS cho 24000/1001, 30000/1001, 24/1.
    t = frame_ratio / fps_frac.
    Kỳ vọng kết quả half-even độc lập.
    """
    m = TimelineMapper(fps=fps_frac, speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)
    for frame_ratio, expected_frame in half_points:
        t_src = frame_ratio / fps_frac
        res = m.src_to_timeline(0, t_src)
        assert res == expected_frame, (
            f"FPS {fps_frac}, PTS {t_src} ({float(t_src):.6f}s, frame {float(frame_ratio)}): "
            f"kỳ vọng {expected_frame}, nhận được {res}"
        )


def test_src_to_timeline_explicit_fraction_2997_100():
    """
    Kiểm tra explicit Fraction(2997, 100) rate được giữ nguyên và round half-even chính xác.
    """
    fps_frac = Fraction(2997, 100)
    m = TimelineMapper(fps=fps_frac, speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)

    # 4.5 frames: t = (9/2) / (2997/100) = 450 / 2997 = 50 / 333
    t_4_5 = Fraction(50, 333)
    assert m.src_to_timeline(0, t_4_5) == 4

    # 5.5 frames: t = (11/2) / (2997/100) = 550 / 2997
    t_5_5 = Fraction(550, 2997)
    assert m.src_to_timeline(0, t_5_5) == 6

    # Điểm nguyên: frame 10 -> t = 10 / (2997/100) = 1000 / 2997
    t_10 = Fraction(1000, 2997)
    assert m.src_to_timeline(0, t_10) == 10


def test_src_to_timeline_multi_segment_offsets_and_clamp():
    """
    Kiểm tra multi-segment:
    - rec_in != 0 (segment 1 sau segment 0)
    - source_in != 0
    - pad 0 vs pad > 0
    - clamp_to_segment=True vs clamp_to_segment=False
    - ngoài khoảng trả về None
    - invalid index trả về None
    - ngưỡng tol 1ms (0.001s)
    """
    m = TimelineMapper(fps=30.0, speed_mode="off")
    # Seg 0: 0.0 -> 5.0 (dur 5s = 150 frames, rec: 0 -> 150)
    m.add_segment("C0", 0.0, 5.0, pad_sec=0.0)
    # Seg 1: 2.0 -> 8.0, pad=0.5 (dur 6s = 180 frames, rec: 150 -> 330)
    m.add_segment("C1", 2.0, 8.0, pad_sec=0.5)

    assert m.segments[1].rec_in_frames == 150
    assert m.segments[1].src_in_frames == round((2.0 - 0.5) * 30.0)  # 1.5 * 30 = 45

    # 1. Invalid index
    assert m.src_to_timeline(-1, 3.0) is None
    assert m.src_to_timeline(2, 3.0) is None
    assert m.src_to_timeline(999, 3.0) is None

    # 2. Segment 1: clamp_to_segment=True
    # Khoảng hợp lệ: [src_in - tol, src_out + tol] = [2.0 - 0.001, 8.0 + 0.001]
    # Tại t = 2.0:
    # t_src_f = 2.0 * 30 = 60. offset_src_f = 60 - 45 = 15.
    # timeline_offset_f = 15. rec_f = 150 + 15 = 165.
    assert m.src_to_timeline(1, 2.0, clamp_to_segment=True) == 165

    # Tại t = 1.999 (nằm trong tol 1ms của src_in 2.0):
    assert m.src_to_timeline(1, 1.999, clamp_to_segment=True) is not None
    # Tại t = 1.998 (ngoài tol 1ms):
    assert m.src_to_timeline(1, 1.998, clamp_to_segment=True) is None

    # Tại t = 8.001 (nằm trong tol 1ms của src_out 8.0):
    assert m.src_to_timeline(1, 8.001, clamp_to_segment=True) is not None
    # Tại t = 8.002 (ngoài tol):
    assert m.src_to_timeline(1, 8.002, clamp_to_segment=True) is None

    # 3. Segment 1: clamp_to_segment=False
    # Cho phép padding: [src_in - pad - tol, src_out + pad + tol] = [1.5 - 0.001, 8.5 + 0.001]
    # Tại t = 1.5 (vùng pad đầu):
    # t_src_f = 1.5 * 30 = 45. offset_src_f = 45 - 45 = 0 -> rec_f = 150.
    assert m.src_to_timeline(1, 1.5, clamp_to_segment=False) == 150
    # Tại t = 1.498 (ngoài pad và tol):
    assert m.src_to_timeline(1, 1.498, clamp_to_segment=False) is None


def test_src_to_timeline_speed_modes_and_formula():
    """
    Kiểm tra speed_mode='timeremap' với applied_speed khác 1.0 (cụ thể 2.0):
    - verify source frame quantization mới + legacy timeline_offset formula:
      offset_src_f = t_src_f - seg.src_in_frames
      timeline_offset_f = round(offset_src_f / seg.applied_speed)
      rec_f = seg.rec_in_frames + timeline_offset_f
    - verify query không làm biến đổi segments hay cursor_frames.
    """
    m = TimelineMapper(fps=24.0, speed_mode="timeremap")
    m.add_segment("C_FAST", 1.0, 5.0, speed=2.0, pad_sec=0.0)

    initial_cursor = m.cursor_frames
    initial_segs_len = len(m.segments)
    seg0 = m.segments[0]
    initial_rec_dur = seg0.rec_dur_frames

    # src_in = 1.0 -> src_in_f = 24. applied_speed = 2.0.
    # Tại t = 3.0: t_src_f = 3.0 * 24 = 72.
    # offset_src_f = 72 - 24 = 48.
    # timeline_offset_f = round(48 / 2.0) = 24.
    # rec_f = 0 + 24 = 24.
    res = m.src_to_timeline(0, 3.0)
    assert res == 24

    # Query không mutate trạng thái mapper
    assert m.cursor_frames == initial_cursor
    assert len(m.segments) == initial_segs_len
    assert seg0.rec_dur_frames == initial_rec_dur


def test_src_to_timeline_sec_delegation():
    """
    src_to_timeline_sec ủy quyền cho src_to_timeline và trả về round(rec_f / self.fps, 4).
    """
    m = TimelineMapper(fps=30.0, speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)

    # Tại t = 2.0s -> rec_f = 60 -> sec = round(60 / 30.0, 4) = 2.0
    sec = m.src_to_timeline_sec(0, 2.0)
    assert sec == 2.0

    # Ngoài segment -> trả về None
    assert m.src_to_timeline_sec(0, 15.0) is None
    assert m.src_to_timeline_sec(99, 2.0) is None


def test_src_to_timeline_nonfinite_inputs():
    """
    Query với input không hữu hạn (NaN):
    - Ném ValueError khi vào tính toán arithmetic.
    - Không làm thay đổi segments hoặc cursor_frames.
    """
    m = TimelineMapper(fps=30.0, speed_mode="off")
    m.add_segment("C1", 0.0, 10.0, pad_sec=0.0)

    cursor_before = m.cursor_frames
    segs_count_before = len(m.segments)

    # NaN vượt qua eligibility guard (nan < x và nan > x đều False) và vào arithmetic
    with pytest.raises(ValueError):
        m.src_to_timeline(0, float("nan"))

    # Mapper state phải hoàn toàn bất biến
    assert m.cursor_frames == cursor_before
    assert len(m.segments) == segs_count_before

    # Inf hoặc -Inf nằm ngoài khoảng [src_in - pad - tol, src_out + pad + tol] -> trả về None
    assert m.src_to_timeline(0, float("inf")) is None
    assert m.src_to_timeline(0, float("-inf")) is None



