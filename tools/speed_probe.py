#!/usr/bin/env python3
"""
tools/speed_probe.py
Công cụ kiểm thử độc lập tính năng Time Remap / Speed trên DaVinci Resolve qua FCP7 XML.

Tạo một timeline XML gồm 3 đoạn cắt liên tiếp với tốc độ khác nhau (1.0x, 2.0x, 0.5x)
để import vào DaVinci Resolve kiểm tra xem hình, tiếng, và độ dài có hoàn toàn ăn khớp không.
"""

import os
import sys
from pathlib import Path

# Thêm thư mục gốc của project vào sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.timeline_mapper import TimelineMapper, set_speed_mode
from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.autocut import get_media_metadata


def generate_speed_probe(
    video_path: str = "assets/broll_memes/memes/bieu_hien_cua_su_luon_leo.mov",
    output_xml: str = "speed_probe_timeline.xml"
):
    abs_video = os.path.abspath(video_path)
    if not os.path.exists(abs_video):
        print(f"❌ Không tìm thấy file mẫu: {abs_video}")
        sys.exit(1)

    meta = get_media_metadata(abs_video)
    fps = float(meta.get("fps") or 30.0)
    dur = float(meta.get("duration") or 3.13)
    print(f"🎬 Video nguồn: {abs_video}")
    print(f"   FPS: {fps:.2f} | Thời lượng gốc: {dur:.2f}s")

    # Đặt chế độ timeremap
    mapper = TimelineMapper(fps=fps, speed_mode="timeremap")

    # Cut 1: 0.0s -> 1.0s, speed = 1.0x
    mapper.add_segment(
        clip_id="C01",
        src_in_sec=0.0,
        src_out_sec=1.0,
        speed=1.0,
        role="build",
        pad_sec=0.0,
        clip_duration_sec=dur,
        clip_name=os.path.basename(abs_video),
        file_path=abs_video,
        title="Cut 1 (1.0x Normal Speed)"
    )

    # Cut 2: 1.0s -> 2.0s, speed = 2.0x
    mapper.add_segment(
        clip_id="C02",
        src_in_sec=1.0,
        src_out_sec=2.0,
        speed=2.0,
        role="climax",
        pad_sec=0.0,
        clip_duration_sec=dur,
        clip_name=os.path.basename(abs_video),
        file_path=abs_video,
        title="Cut 2 (2.0x Fast Speed)"
    )

    # Cut 3: 2.0s -> 3.0s, speed = 0.5x
    mapper.add_segment(
        clip_id="C03",
        src_in_sec=2.0,
        src_out_sec=3.0,
        speed=0.5,
        role="outro",
        pad_sec=0.0,
        clip_duration_sec=dur,
        clip_name=os.path.basename(abs_video),
        file_path=abs_video,
        title="Cut 3 (0.5x Slow Motion)"
    )

    events = mapper.to_events()

    print("\n📋 BẢNG THÔNG SỐ CÁC PHÂN ĐOẠN (TIMELINE MAPPER):")
    print(f"{'Idx':<4} {'Title':<28} {'Speed':<8} {'Src In-Out (f)':<18} {'Rec In-Out (f)':<18} {'Duration (f/s)':<16}")
    print("-" * 95)
    for seg in mapper.segments:
        src_io = f"{seg.src_in_frames} -> {seg.src_out_frames}"
        rec_io = f"{seg.rec_in_frames} -> {seg.rec_out_frames}"
        dur_str = f"{seg.rec_dur_frames}f ({seg.rec_dur_frames / fps:.2f}s)"
        print(f"{seg.segment_index:<4} {seg.title:<28} {seg.applied_speed:<8.1f} {src_io:<18} {rec_io:<18} {dur_str:<16}")

    total_f = mapper.get_total_duration_frames()
    total_s = mapper.get_total_duration_sec()
    print("-" * 95)
    print(f"Tổng thời lượng timeline: {total_f} frames ({total_s:.2f}s)")

    abs_out = os.path.abspath(output_xml)
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=abs_out,
        timeline_name="Speed Probe Timeline (1.0x - 2.0x - 0.5x)",
        fps=fps,
        speed_mode="timeremap"
    )

    print(f"\n✅ Đã xuất thành công FCP7 XML: {abs_out}")
    print("\n💡 HƯỚNG DẪN KIỂM TRA TRÊN DAVINCI RESOLVE:")
    print("1. Mở DaVinci Resolve.")
    print("2. Vào menu File -> Import -> Timeline... (Ctrl+Shift+I).")
    print(f"3. Chọn file XML vừa tạo: {abs_out}")
    print("4. Kiểm tra timeline:")
    print(f"   - Đoạn 1: 0s -> 1.0s (1.0x bình thường)")
    print(f"   - Đoạn 2: 1.0s -> 1.5s (2.0x tua nhanh gấp đôi)")
    print(f"   - Đoạn 3: 1.5s -> 3.5s (0.5x chuyển động chậm)")
    print(f"   - Tổng độ dài: chính xác 3.5s ({total_f} frames)")
    print("   - Không có khe hở đen, không lệch tiếng/hình.")


if __name__ == "__main__":
    vpath = sys.argv[1] if len(sys.argv) > 1 else "assets/broll_memes/memes/bieu_hien_cua_su_luon_leo.mov"
    out_xml = sys.argv[2] if len(sys.argv) > 2 else "speed_probe_timeline.xml"
    generate_speed_probe(vpath, out_xml)
