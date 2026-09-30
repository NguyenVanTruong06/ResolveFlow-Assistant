import os
import pytest
from src.core.audit_reporter import ExecutionAuditReporter, ClipAuditRecord

def test_audit_reporter_clip_record():
    reporter = ExecutionAuditReporter(project_name="TestProject")
    
    raw_subs = [
        {"start": 1.0, "end": 4.0, "text": "Hôm nay tôi đi du lịch Đà Lạt.", "words": [{"word": "Hôm"}, {"word": "nay"}, {"word": "tôi"}, {"word": "đi"}, {"word": "du"}, {"word": "lịch"}, {"word": "Đà"}, {"word": "Lạt."}]},
        {"start": 6.0, "end": 9.0, "text": "Khung cảnh ở đây rất đẹp!", "words": [{"word": "Khung"}, {"word": "cảnh"}, {"word": "ở"}, {"word": "đây"}, {"word": "rất"}, {"word": "đẹp!"}]}
    ]
    split_subs = [
        {"start": 1.0, "end": 2.5, "text": "Hôm nay tôi đi"},
        {"start": 2.5, "end": 4.0, "text": "du lịch Đà Lạt."},
        {"start": 6.0, "end": 9.0, "text": "Khung cảnh ở đây rất đẹp!"}
    ]
    keep_intervals = [(1.0, 4.0), (6.0, 9.0)] # total 6.0s from 10.0s

    rec = reporter.record_clip_audit(
        clip_index=1,
        video_path="/path/to/clip_01.mp4",
        original_duration=10.0,
        silent_intervals_detected=2,
        keep_intervals=keep_intervals,
        raw_subtitles=raw_subs,
        split_subtitles_list=split_subs,
        split_mode="characters",
        split_limit=42,
        fps=30.0
    )

    assert rec.clip_name == "clip_01.mp4"
    assert rec.original_duration == 10.0
    assert rec.final_duration == 6.0
    assert rec.duration_saved == 4.0
    assert rec.percentage_reduced == 40.0
    assert rec.raw_words_count == 14
    assert rec.raw_sentences_count == 2
    assert rec.subtitle_lines_count == 3
    assert len(rec.kept_intervals) == 2
    assert "00:00:01:00" in rec.kept_intervals[0].timecode_range

def test_audit_reporter_teaser_and_manifest(tmp_path):
    reporter = ExecutionAuditReporter(project_name="Vlog_DaLat")

    reporter.record_teaser_item(
        order=1,
        video_path="clip_01.mp4",
        src_in=1.5,
        src_out=4.0,
        reason="Hook: Wow nhìn này",
        score=8.5,
        hook_text="Wow nhìn này cảnh tượng đẹp quá",
        fps=30.0
    )

    reporter.add_output_file(
        category="File Cắt Timeline (EDL)",
        file_path=os.path.join(tmp_path, "Vlog_cut.edl"),
        description="EDL timeline đã cắt"
    )
    # Tạo dummy file để add_output_file nhận diện
    with open(os.path.join(tmp_path, "Vlog_cut.edl"), "w", encoding="utf-8") as f:
        f.write("")

    reporter.add_output_file(
        category="File Cắt Timeline (EDL)",
        file_path=os.path.join(tmp_path, "Vlog_cut.edl"),
        description="EDL timeline đã cắt"
    )

    reporter.add_timeline(
        timeline_name="Vlog_DaLat_AI_Visual_Cut",
        description="Timeline chính đã cắt khoảng lặng"
    )

    # Test Console summary
    console_summary = reporter.generate_console_summary()
    assert "BÁO CÁO MINH BẠCH & NHẬT KÝ THỰC THI" in console_summary
    assert "Vlog_DaLat" in console_summary
    assert "clip_01.mp4" in console_summary
    assert "Vlog_DaLat_AI_Visual_Cut" in console_summary

    # Test Markdown export
    report_md_path = os.path.join(tmp_path, "report.md")
    reporter.export_markdown_report(report_md_path)
    assert os.path.exists(report_md_path)

    with open(report_md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    assert "# 📊 Báo cáo Minh bạch & Nhật ký Thực thi: Vlog_DaLat" in md_content
    assert "Báo cáo Trích xuất Intro Vlog Teaser" in md_content
    assert "Wow nhìn này" in md_content
    assert "Vlog_DaLat_AI_Visual_Cut" in md_content

def test_audit_reporter_thumbnail(tmp_path):
    reporter = ExecutionAuditReporter(project_name="Vlog_DaLat_Thumb")

    dummy_thumb = os.path.join(tmp_path, "thumb_01.png")
    with open(dummy_thumb, "w") as f:
        f.write("image_data")

    reporter.record_thumbnail(
        index=1,
        clip_name="vlog_cam1.mp4",
        timestamp_sec=5.25,
        timecode="00:00:05:07",
        overall_score=88.5,
        sharpness_score=110.2,
        file_path=dummy_thumb
    )

    assert len(reporter.thumbnail_items) == 1
    assert reporter.thumbnail_items[0].timecode == "00:00:05:07"

    summary = reporter.generate_console_summary()
    assert "KHUNG HÌNH VÀNG ĐỀ XUẤT LÀM THUMBNAIL" in summary
    assert "Thumb #01" in summary

    report_md_path = os.path.join(tmp_path, "report_with_thumb.md")
    reporter.export_markdown_report(report_md_path)
    with open(report_md_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    assert "Khung Hình Vàng Đề Xuất Làm Thumbnail" in md_text
    assert "Thumb #01" in md_text

def test_audit_reporter_audio_normalization(tmp_path):
    reporter = ExecutionAuditReporter(project_name="Vlog_DaLat_AudioNorm")

    reporter.record_audio_normalization(
        clip_name="voiceover_raw.wav",
        input_i=-24.5,
        input_tp=-4.2,
        output_i=-14.0,
        output_tp=-1.0,
        preset_name="youtube_tiktok"
    )

    assert len(reporter.audio_normalizations) == 1
    item = reporter.audio_normalizations[0]
    assert item.clip_name == "voiceover_raw.wav"
    assert item.gain_adjustment_db == 10.5
    assert item.output_i == -14.0

    # Console summary
    summary = reporter.generate_console_summary()
    assert "CHUẨN HÓA ÂM LƯỢNG (EBU R128 / LOUDNORM)" in summary
    assert "voiceover_raw.wav" in summary
    assert "-24.5 LUFS" in summary
    assert "-14.0 LUFS" in summary

    # Markdown export
    report_md_path = os.path.join(tmp_path, "report_audio.md")
    reporter.export_markdown_report(report_md_path)
    with open(report_md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    assert "Chuẩn Hóa Âm Lượng Giọng Nói" in md_content
    assert "voiceover_raw.wav" in md_content
    assert "-14.0 LUFS" in md_content


