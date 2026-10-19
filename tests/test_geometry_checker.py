import pytest
from src.core.geometry_checker import (
    BoundingBox,
    SafeZonePreset
)


def test_bounding_box_properties():
    box = BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.6)
    assert pytest.approx(box.width) == 0.4
    assert pytest.approx(box.height) == 0.4
    assert pytest.approx(box.center_x) == 0.3
    assert pytest.approx(box.center_y) == 0.4
    assert pytest.approx(box.area) == 0.16


def test_bounding_box_intersection():
    b1 = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5)
    b2 = BoundingBox(x_min=0.3, y_min=0.3, x_max=0.7, y_max=0.7)
    b3 = BoundingBox(x_min=0.6, y_min=0.6, x_max=0.9, y_max=0.9)

    # b1 và b2 giao nhau
    assert b1.intersects(b2) is True
    assert b2.intersects(b1) is True
    assert pytest.approx(b1.intersection_area(b2)) == 0.04

    # IoU = 0.04 / (0.16 + 0.16 - 0.04) = 0.04 / 0.28
    expected_iou = 0.04 / 0.28
    assert pytest.approx(b1.iou(b2), rel=1e-3) == expected_iou

    # b1 và b3 không giao nhau
    assert b1.intersects(b3) is False
    assert b1.intersection_area(b3) == 0.0
    assert b1.iou(b3) == 0.0


def test_bounding_box_clamp():
    # Vùng an toàn từ 0.1 đến 0.9 mỗi chiều
    container = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9)

    # Box bị lẹm qua bên phải (x_max = 0.95, width = 0.25)
    out_right = BoundingBox(x_min=0.7, y_min=0.2, x_max=0.95, y_max=0.4)
    clamped_r = out_right.clamp(container)
    assert clamped_r.x_max <= container.x_max
    assert pytest.approx(clamped_r.width) == 0.25
    assert pytest.approx(clamped_r.x_min) == 0.65

    # Box bị lẹm xuống dưới (y_max = 0.95, height = 0.15)
    out_bottom = BoundingBox(x_min=0.3, y_min=0.8, x_max=0.6, y_max=0.95)
    clamped_b = out_bottom.clamp(container)
    assert clamped_b.y_max <= container.y_max
    assert pytest.approx(clamped_b.height) == 0.15
    assert pytest.approx(clamped_b.y_min) == 0.75


def test_safe_zone_presets():
    tiktok_safe = SafeZonePreset.get_safe_zone("tiktok", "9:16")
    assert isinstance(tiktok_safe, BoundingBox)
    # TikTok tránh mép phải (nút like/comment) nên x_max < 0.88
    assert tiktok_safe.x_max <= 0.86
    # Tránh thanh caption dưới cùng
    assert tiktok_safe.y_max <= 0.84

    shorts_safe = SafeZonePreset.get_safe_zone("youtube_shorts", "9:16")
    assert isinstance(shorts_safe, BoundingBox)
    assert shorts_safe.x_max <= 0.86

    broadcast_16_9 = SafeZonePreset.get_safe_zone("standard", "16:9")
    assert isinstance(broadcast_16_9, BoundingBox)
    # Title safe 10%
    assert broadcast_16_9.x_min == 0.10
    assert broadcast_16_9.x_max == 0.90


def test_check_subtitle_face_collision():
    from src.core.geometry_checker import GeometryConflictChecker

    checker = GeometryConflictChecker(platform="tiktok", aspect_ratio="9:16")

    # Trường hợp 1: Mặt người ở vị trí rất thấp, đè lên phụ đề đáy (y=0.80 -> 0.92)
    face_low = BoundingBox(x_min=0.35, y_min=0.55, x_max=0.65, y_max=0.85)
    default_sub = BoundingBox(x_min=0.15, y_min=0.80, x_max=0.85, y_max=0.92)

    has_collision, adj_sub = checker.check_subtitle_face_collision(face_low, default_sub)
    assert has_collision is True
    # Phụ đề phải được nâng lên cao hơn mặt hoặc ngang ngực trên (y_max <= face_low.y_min)
    assert adj_sub.y_max <= face_low.y_min or adj_sub.y_max <= 0.60
    # Phụ đề sau khi nâng vẫn phải nằm trong safe zone
    assert adj_sub.y_min >= checker.safe_zone.y_min

    # Trường hợp 2: Mặt người ở vị trí bình thường/cao (y=0.15 -> 0.45)
    face_high = BoundingBox(x_min=0.35, y_min=0.15, x_max=0.65, y_max=0.45)
    has_collision2, adj_sub2 = checker.check_subtitle_face_collision(face_high, default_sub)
    assert has_collision2 is False
    assert adj_sub2.y_min == default_sub.y_min
    assert adj_sub2.y_max == default_sub.y_max


def test_resolve_pip_placement():
    from src.core.geometry_checker import GeometryConflictChecker

    checker = GeometryConflictChecker(platform="tiktok", aspect_ratio="9:16")

    # Trường hợp 1: Mặt người đứng ở nửa bên phải (x: 0.55 -> 0.85)
    face_right = BoundingBox(x_min=0.55, y_min=0.20, x_max=0.85, y_max=0.55)
    scale, pan_x, pan_y, corner = checker.resolve_pip_placement(face_right)
    # PiP phải dời sang góc dưới bên trái (pan_x < 0)
    assert corner == "bottom_left"
    assert pan_x < 0.0
    assert pan_y < 0.0 # Trong Resolve FCPXML, góc dưới thường pan_y âm

    # Trường hợp 2: Mặt người đứng ở nửa bên trái (x: 0.15 -> 0.45)
    face_left = BoundingBox(x_min=0.15, y_min=0.20, x_max=0.45, y_max=0.55)
    scale, pan_x, pan_y, corner = checker.resolve_pip_placement(face_left)
    # PiP phải dời sang góc dưới bên phải (pan_x > 0)
    assert corner == "bottom_right"
    assert pan_x > 0.0
    assert pan_y < 0.0

    # Trường hợp 3: Mặt người ngồi chính giữa đáy (x: 0.35 -> 0.65, y: 0.60 -> 0.95)
    face_bottom_center = BoundingBox(x_min=0.35, y_min=0.60, x_max=0.65, y_max=0.95)
    scale, pan_x, pan_y, corner = checker.resolve_pip_placement(face_bottom_center)
    # 2 góc dưới đều bị cản/chật -> PiP phải chuyển lên góc trên
    assert corner in ["top_right", "top_left"]
    assert pan_y > 0.0 # Góc trên pan_y dương


def test_audit_shotbook_plan_resolves_pip_and_reports():
    from src.core.shotbook_orchestrator import ShotbookPlan, ShotbookItem, ShotRole, LayoutMode
    from src.core.geometry_checker import GeometryConflictChecker, ConflictType, ConflictResolution

    checker = GeometryConflictChecker(platform="tiktok", aspect_ratio="9:16")

    # Item ban đầu đặt pan_x = 0.28 (góc phải)
    item = ShotbookItem(
        segment_index=0,
        start_time=1.0,
        end_time=5.0,
        text="Xem trên web...",
        role=ShotRole.EVIDENCE,
        layout_mode=LayoutMode.PIP_CORNER,
        zoom_scale=0.35,
        pan_x=0.28,
        pan_y=-0.32
    )
    plan = ShotbookPlan(items=[item])

    # Dữ liệu nhận diện khuôn mặt: Người nói đứng lệch bên phải (x: 0.60 -> 0.85)
    face_detections = [
        {
            "time": 2.5,
            "box": [0.60, 0.20, 0.85, 0.50]
        }
    ]

    adjusted_plan, reports = checker.audit_shotbook_plan(plan, face_detections=face_detections)

    # PiP phải tự động chuyển sang góc trái (pan_x < 0)
    assert adjusted_plan.items[0].pan_x < 0.0
    # Phải có báo cáo xung đột
    assert len(reports) >= 1
    assert reports[0].conflict_type == ConflictType.PIP_FACE_COLLISION
    assert reports[0].resolution == ConflictResolution.FLIP_PIP_CORNER


