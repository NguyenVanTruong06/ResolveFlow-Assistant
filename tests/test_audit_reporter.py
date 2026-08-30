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
