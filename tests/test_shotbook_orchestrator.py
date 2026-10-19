"""
Unit test cho ShotbookOrchestrator (src/core/shotbook_orchestrator.py).
"""

import pytest
from src.core.evidence_grabber import EvidenceTarget
from src.core.shotbook_orchestrator import (
    ShotRole,
    LayoutMode,
    ShotbookItem,
    ShotbookPlan,
    ShotbookOrchestrator,
)


def test_shotbook_classifies_hook_at_beginning():
    orchestrator = ShotbookOrchestrator()
    segments = [
        {"start": 0.5, "end": 3.0, "text": "Làm thế nào để tạo video triệu view trong 5 phút?"},
        {"start": 3.2, "end": 7.0, "text": "Hôm nay tôi sẽ chia sẻ cho bạn một công cụ cực kỳ hữu ích."},
    ]
    plan = orchestrator.create_shotbook_plan(segments)
    
    assert len(plan.items) == 2
    # Câu đầu tiên là câu hỏi ở 3 giây đầu -> phải là HOOK
    assert plan.items[0].role == ShotRole.HOOK
    assert plan.items[0].layout_mode == LayoutMode.PUNCH_IN
    assert plan.items[0].zoom_scale > 1.10


def test_shotbook_classifies_evidence_with_pip():
    orchestrator = ShotbookOrchestrator()
    segments = [
        {"start": 10.0, "end": 15.0, "text": "Các bạn hãy truy cập vào github.com/YeJe-cpu/SeeCut để xem mã nguồn."},
    ]
    evidence_targets = [
        EvidenceTarget(
            keyword="github.com/YeJe-cpu/SeeCut",
            target_url="https://github.com/YeJe-cpu/SeeCut",
            start_time=10.0,
            end_time=15.0,
            evidence_path="data/evidence/test.png"
        )
    ]
    plan = orchestrator.create_shotbook_plan(segments, evidence_targets=evidence_targets)
    
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.role == ShotRole.EVIDENCE
    assert item.layout_mode == LayoutMode.PIP_CORNER
    assert item.zoom_scale <= 0.40  # Thu nhỏ thành PiP
    assert item.evidence_path == "data/evidence/test.png"
    # Pan tọa độ góc dưới
    assert abs(item.pan_x) > 0.1
    assert item.pan_y < 0.0


def test_shotbook_normal_talk_full_center():
    orchestrator = ShotbookOrchestrator()
    segments = [
        {"start": 20.0, "end": 25.0, "text": "Đây là câu chuyện bình thường tôi muốn kể cho các bạn."},
    ]
    plan = orchestrator.create_shotbook_plan(segments)
    
    assert len(plan.items) == 1
    assert plan.items[0].role == ShotRole.NORMAL
    assert plan.items[0].layout_mode == LayoutMode.FULL_CENTER
    assert plan.items[0].zoom_scale == 1.0
    assert plan.items[0].pan_x == 0.0
    assert plan.items[0].pan_y == 0.0


def test_shotbook_audit_with_geometry_checker():
    orchestrator = ShotbookOrchestrator()
    segments = [
        {"start": 10.0, "end": 15.0, "text": "Truy cập vào trang web sau để tải về."},
    ]
    evidence_targets = [
        EvidenceTarget(
            keyword="trang web",
            target_url="https://example.com",
            start_time=10.0,
            end_time=15.0,
            evidence_path="data/evidence/test.png"
        )
    ]
    plan = orchestrator.create_shotbook_plan(segments, evidence_targets=evidence_targets)
    assert plan.items[0].pan_x > 0.0  # Mặc định góc phải

    # Face lệch phải -> audit phải tự lật sang góc trái
    face_detections = [{"time": 12.0, "box": [0.65, 0.20, 0.85, 0.50]}]
    adj_plan, reports = orchestrator.audit_with_geometry_checker(plan, face_detections=face_detections)

    assert adj_plan.items[0].pan_x < 0.0  # Lật sang góc trái
    assert len(reports) >= 1

