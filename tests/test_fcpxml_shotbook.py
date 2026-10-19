import os
import xml.etree.ElementTree as ET
import pytest

from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.shotbook_orchestrator import (
    ShotbookPlan,
    ShotbookItem,
    ShotRole,
    LayoutMode
)


def test_generate_timeline_fcpxml_with_shotbook_transforms(tmp_path):
    video_path = os.path.join(tmp_path, "main_video.mp4")
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 1024)

    events = [
        {
            "video_path": video_path,
            "src_in": 0.0,
            "src_out": 4.0,
            "rec_in": 0.0,
            "rec_out": 4.0,
            "fps": 30.0
        },
        {
            "video_path": video_path,
            "src_in": 4.0,
            "src_out": 8.0,
            "rec_in": 4.0,
            "rec_out": 8.0,
            "fps": 30.0
        }
    ]

    # Item 0 is HOOK -> Punch-in 1.18x
    # Item 1 is NORMAL -> Full center 1.0x
    shotbook_plan = ShotbookPlan(items=[
        ShotbookItem(
            segment_index=0,
            start_time=0.0,
            end_time=4.0,
            text="Làm thế nào để kiếm 1 triệu đô?",
            role=ShotRole.HOOK,
            layout_mode=LayoutMode.PUNCH_IN,
            zoom_scale=1.18,
            pan_x=0.0,
            pan_y=0.0
        ),
        ShotbookItem(
            segment_index=1,
            start_time=4.0,
            end_time=8.0,
            text="Đây là câu chuyện bình thường.",
            role=ShotRole.NORMAL,
            layout_mode=LayoutMode.FULL_CENTER,
            zoom_scale=1.0,
            pan_x=0.0,
            pan_y=0.0
        )
    ])

    out_xml = os.path.join(tmp_path, "timeline_with_transforms.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Shotbook Transform Test",
        shotbook_plan=shotbook_plan
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "r", encoding="utf-8") as f:
        xml_content = f.read()

    # Thẻ <adjust-transform> phải xuất hiện trên clip đầu tiên với scale 1.18
    assert "<adjust-transform" in xml_content
    assert 'scale="1.18 1.18"' in xml_content
    # Clip thứ 2 là NORMAL 1.0x không cần adjust-transform phóng to
    root = ET.fromstring(xml_content)
    spine = root.find(".//spine")
    assert spine is not None
    clips = spine.findall("asset-clip")
    assert len(clips) == 2
    # Clip 1 có transform
    tf_elem1 = clips[0].find("adjust-transform")
    assert tf_elem1 is not None
    assert tf_elem1.get("scale") == "1.18 1.18"


def test_generate_timeline_fcpxml_with_evidence_v2(tmp_path):
    video_path = os.path.join(tmp_path, "talk_video.mp4")
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 1024)

    evidence_png = os.path.join(tmp_path, "github_screenshot.png")
    with open(evidence_png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    events = [
        {
            "video_path": video_path,
            "src_in": 0.0,
            "src_out": 6.0,
            "rec_in": 0.0,
            "rec_out": 6.0,
            "fps": 30.0
        }
    ]

    shotbook_plan = ShotbookPlan(items=[
        ShotbookItem(
            segment_index=0,
            start_time=1.0,
            end_time=5.0,
            text="Theo bằng chứng trên trang chủ GitHub...",
            role=ShotRole.EVIDENCE,
            layout_mode=LayoutMode.PIP_CORNER,
            zoom_scale=0.35,
            pan_x=0.28,
            pan_y=-0.32,
            evidence_path=evidence_png
        )
    ])

    out_xml = os.path.join(tmp_path, "timeline_with_evidence.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Evidence V2 Test",
        shotbook_plan=shotbook_plan
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "r", encoding="utf-8") as f:
        xml_content = f.read()

    root = ET.fromstring(xml_content)

    # 1. Kiểm tra tài nguyên Evidence trong <resources>
    resources = root.find("resources")
    assert resources is not None
    evidence_assets = [
        a for a in resources.findall("asset")
        if "github_screenshot.png" in (a.get("name") or "")
    ]
    assert len(evidence_assets) == 1, "Phải khai báo asset cho evidence trong <resources>"

    # 2. Kiểm tra clip Evidence được neo trên Track V2 (lane="1")
    spine = root.find(".//spine")
    main_clip = spine.find("asset-clip")
    assert main_clip is not None

    # V1 clip speaker có thẻ <adjust-transform> thu nhỏ PiP 0.35
    tf = main_clip.find("adjust-transform")
    assert tf is not None
    assert tf.get("scale") == "0.35 0.35"
    assert tf.get("position") is not None

    # Track V2 evidence clip
    anchored_evidence = [
        c for c in main_clip.findall("asset-clip")
        if c.get("lane") == "1" and c.get("name") == "github_screenshot.png"
    ]
    assert len(anchored_evidence) == 1, "Phải có asset-clip evidence gắn lane='1' (Track V2)"


def test_generate_timeline_fcpxml_backward_compatible(tmp_path):
    video_path = os.path.join(tmp_path, "regular_video.mp4")
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 1024)

    events = [
        {
            "video_path": video_path,
            "src_in": 0.0,
            "src_out": 3.0,
            "rec_in": 0.0,
            "rec_out": 3.0,
            "fps": 30.0
        }
    ]

    out_xml = os.path.join(tmp_path, "regular.fcpxml")
    # Gọi không truyền shotbook_plan (hoặc truyền None)
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Regular Test"
    )

    with open(out_xml, "r", encoding="utf-8") as f:
        xml_content = f.read()

    assert "<adjust-transform" not in xml_content
    assert "r_evidence" not in xml_content
