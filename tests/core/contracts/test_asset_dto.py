"""
tests/core/contracts/test_asset_dto.py
Bộ kiểm thử đơn vị cho Hợp đồng Dữ liệu Asset DTO (G01-01A-R1).
Kiểm tra tính hợp lệ đóng kín, khả năng xử lý probe biên, bảo toàn legacy UI và tuần tự hóa JSON an toàn.
"""
import copy
import json
import math
import pytest

from src.core.contracts.asset_dto import (
    CANONICAL_STORAGE_CATEGORIES,
    RESERVED_CATEGORY_IDS,
    UI_CATEGORIES,
    is_safe_relative_path,
    is_absolute_lexical_path,
    is_valid_custom_category_id,
    validate_asset_dict,
    to_persisted_record,
    to_ui_asset,
)


# ==============================================================================
# 1. FIXTURES JSON CHUẨN (THEO THIẾT KẾ ĐÃ ĐƯỢC CODEX DUYỆT)
# ==============================================================================

VALID_MANAGED_VIDEO = {
    "schema_version": 1,
    "id": "a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c5d",
    "name": "Cinematic Drone Sunset",
    "source_kind": "managed",
    "category": "broll",
    "subcategory": "nature",
    "tags": ["drone", "sunset", "4k"],
    "template_type": None,
    "size_bytes": 104857600,
    "pack_id": "p9876543-210f-4edc-ba98-76543210fedc",
    "pack_name": "Travel Pack Vol 1",
    "relative_path": "broll/nature/drone_sunset.mp4",
    "library_path": "02_cloud_downloads/packs/travel_pack_vol_1/broll/nature/drone_sunset.mp4",
    "duration_sec": 12.5,
}

VALID_LINKED_ASSET = {
    "schema_version": 1,
    "id": "f5e4d3c2-b1a0-4f9e-8d7c-6b5a4f3e2d1c",
    "name": "Studio Interview Cam A",
    "source_kind": "linked",
    "source_id": "link_ext_drive_01",
    "category": "broll",
    "subcategory": "interviews",
    "tags": ["podcast", "interview"],
    "template_type": None,
    "size_bytes": 524288000,
    "external_path": "/media/external_drive/raw_interviews/cam_a.mov",
    "duration_sec": 180.25,
}

VALID_BUILTIN_ASSET = {
    "schema_version": 1,
    "id": "kinetic_hormozi",
    "name": "Chữ động phong cách Hormozi",
    "source_kind": "builtin",
    "category": "templates",
    "subcategory": "kinetic",
    "tags": ["text", "kinetic", "highlight"],
    "template_type": "text",
    "size_bytes": 0,
    "duration_sec": None,
}

VALID_CUSTOM_CATEGORY_ASSET = {
    "schema_version": 1,
    "id": "e3d2c1b0-9a8b-7c6d-5e4f-3a2b1c0d9e8f",
    "name": "Đánh Giá Bàn Phím Cơ",
    "source_kind": "managed",
    "category": "product_review",
    "subcategory": "bàn phím",
    "tags": ["review", "bàn phím", "công nghệ"],
    "template_type": None,
    "size_bytes": 20480000,
    "pack_id": "p1122334-4556-6778-899a-aabbccddeeff",
    "pack_name": "Review Công Nghệ",
    "relative_path": "product_review/ban_phim.mp4",
    "library_path": "02_cloud_downloads/packs/review_cong_nghe/product_review/ban_phim.mp4",
    "duration_sec": 45.2,
}

VALID_TRANSITION_TEMPLATE = {
    "schema_version": 1,
    "id": "c1a2b3d4-4e5f-6a7b-8c9d-112233445566",
    "name": "Seamless Zoom In",
    "source_kind": "managed",
    "category": "templates",
    "subcategory": "zoom",
    "tags": ["seamless", "zoom"],
    "template_type": "trans",
    "size_bytes": 45000,
    "pack_id": "p5566778-8990-1122-3344-556677889900",
    "pack_name": "Pro Transitions",
    "relative_path": "templates/transitions/zoom_in.drfx",
    "library_path": "02_cloud_downloads/packs/pro_transitions/templates/transitions/zoom_in.drfx",
    "duration_sec": 1.0,
}


# ==============================================================================
# 2. KIỂM THỬ XÁC THỰC BẢN GHI MỚI & CÁC PROBE BIÊN
# ==============================================================================

def test_validate_all_valid_fixtures():
    for fix in (
        VALID_MANAGED_VIDEO,
        VALID_LINKED_ASSET,
        VALID_BUILTIN_ASSET,
        VALID_CUSTOM_CATEGORY_ASSET,
        VALID_TRANSITION_TEMPLATE,
    ):
        ok, errors = validate_asset_dict(fix)
        assert ok, f"Fixture hợp lệ bị báo lỗi: {errors}"
        assert errors == []


def test_validate_probe_source_kind_unhashable_and_non_string():
    # Probe Codex: source_kind=[] không được ném TypeError
    d_list = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_list["source_kind"] = []
    ok, errors = validate_asset_dict(d_list)
    assert not ok
    assert any("source_kind" in err.lower() for err in errors)

    d_dict = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_dict["source_kind"] = {}
    ok, errors = validate_asset_dict(d_dict)
    assert not ok

    d_none = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_none["source_kind"] = None
    ok, errors = validate_asset_dict(d_none)
    assert not ok


def test_validate_probe_template_type_unhashable():
    # Probe Codex: template_type={} không được ném TypeError
    d_dict = copy.deepcopy(VALID_TRANSITION_TEMPLATE)
    d_dict["template_type"] = {}
    ok, errors = validate_asset_dict(d_dict)
    assert not ok
    assert any("template_type" in err.lower() for err in errors)


def test_validate_probe_library_path_traversal():
    # Probe Codex: library_path='../../outside.mp4' phải bị từ chối
    d = copy.deepcopy(VALID_MANAGED_VIDEO)
    d["library_path"] = "../../outside.mp4"
    ok, errors = validate_asset_dict(d)
    assert not ok
    assert any("library_path" in err.lower() for err in errors)

    # library_path tuyệt đối cũng bị từ chối
    d_abs = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_abs["library_path"] = "/var/lib/chundvc/outside.mp4"
    ok, errors = validate_asset_dict(d_abs)
    assert not ok
    assert any("library_path" in err.lower() for err in errors)


def test_validate_probe_subcategory_and_optional_allowed_fields_type():
    # Probe Codex: subcategory=object() phải bị từ chối
    d_obj = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_obj["subcategory"] = object()
    ok, errors = validate_asset_dict(d_obj)
    assert not ok
    assert any("subcategory" in err.lower() for err in errors)

    # pack_name=object() cũng bị từ chối
    d_pn = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_pn["pack_name"] = object()
    ok, errors = validate_asset_dict(d_pn)
    assert not ok
    assert any("pack_name" in err.lower() for err in errors)


def test_validate_probe_reserved_category_ids():
    # Probe Codex: category='all' phải bị từ chối
    for res_id in RESERVED_CATEGORY_IDS:
        d = copy.deepcopy(VALID_MANAGED_VIDEO)
        d["category"] = res_id
        ok, errors = validate_asset_dict(d)
        assert not ok, f"Reserved ID '{res_id}' đáng lẽ phải bị từ chối!"
        assert any("category" in err.lower() for err in errors)


def test_validate_probe_linked_external_path_lexical():
    # Probe Codex: external_path='relative/file.mov' phải bị từ chối
    d_rel = copy.deepcopy(VALID_LINKED_ASSET)
    d_rel["external_path"] = "relative/file.mov"
    ok, errors = validate_asset_dict(d_rel)
    assert not ok
    assert any("external_path" in err.lower() for err in errors)

    # Drive-relative Windows: C:relative.mov phải bị từ chối
    d_drive_rel = copy.deepcopy(VALID_LINKED_ASSET)
    d_drive_rel["external_path"] = "C:relative.mov"
    ok, errors = validate_asset_dict(d_drive_rel)
    assert not ok
    assert any("external_path" in err.lower() for err in errors)

    # Đường dẫn tuyệt đối Windows drive C:\ và UNC \\server\share phải hợp lệ
    d_win = copy.deepcopy(VALID_LINKED_ASSET)
    d_win["external_path"] = "D:\\Footage\\Project\\cam_a.mov"
    ok, errors = validate_asset_dict(d_win)
    assert ok

    d_unc = copy.deepcopy(VALID_LINKED_ASSET)
    d_unc["external_path"] = "\\\\nas_server\\share\\cam_a.mov"
    ok, errors = validate_asset_dict(d_unc)
    assert ok


def test_validate_lexical_path_nul_byte_rejected():
    assert not is_safe_relative_path("safe/path\x00evil")
    assert not is_absolute_lexical_path("/safe/path\x00evil")


def test_validate_strict_rejects_zero_and_negative_duration():
    # Bản ghi mới: duration_sec = 0 hoặc -1 là không hợp lệ
    d_zero = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_zero["duration_sec"] = 0
    ok, errors = validate_asset_dict(d_zero)
    assert not ok
    assert any("duration_sec" in err.lower() for err in errors)

    d_neg = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_neg["duration_sec"] = -1.0
    ok, errors = validate_asset_dict(d_neg)
    assert not ok
    assert any("duration_sec" in err.lower() for err in errors)


def test_validate_huge_integer_does_not_overflow():
    d_huge = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_huge["duration_sec"] = 10**300
    ok, errors = validate_asset_dict(d_huge)
    assert ok


def test_validate_overflow_integer_duration_rejected_without_exception():
    # Probe Codex: duration_sec = 10**1000 phải trả về False + error, không văng lỗi
    d_overflow = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_overflow["duration_sec"] = 10**1000
    ok, errors = validate_asset_dict(d_overflow)
    assert not ok
    assert any("duration_sec" in err.lower() for err in errors)

    # Probe Codex: số nguyên cực lớn (>4300 chữ số) không được ném ValueError do string limit
    d_huge_digits = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_huge_digits["duration_sec"] = 10**5000
    ok, errors = validate_asset_dict(d_huge_digits)
    assert not ok
    assert any("duration_sec" in err.lower() for err in errors)


def test_to_persisted_record_overflow_duration_raises_value_error():
    # to_persisted_record phải ném ValueError cho duration không thể biểu diễn, không normalize thành None
    d_overflow = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_overflow["duration_sec"] = 10**1000
    with pytest.raises(ValueError, match="duration_sec"):
        to_persisted_record(d_overflow)


def test_validate_probe_templates_requires_template_type():
    # Probe Codex: category='templates' thiếu template_type hoặc None phải bị từ chối
    d_missing = copy.deepcopy(VALID_BUILTIN_ASSET)
    del d_missing["template_type"]
    ok, errors = validate_asset_dict(d_missing)
    assert not ok
    assert any("template_type" in err.lower() for err in errors)

    d_none = copy.deepcopy(VALID_BUILTIN_ASSET)
    d_none["template_type"] = None
    ok, errors = validate_asset_dict(d_none)
    assert not ok
    assert any("template_type" in err.lower() for err in errors)

    # validate_asset_dict nghiêm ngặt chỉ chấp nhận 'text' hoặc 'trans' v1 (không chấp nhận alias 'title' chưa chuẩn hóa)
    d_alias = copy.deepcopy(VALID_BUILTIN_ASSET)
    d_alias["template_type"] = "title"
    ok, errors = validate_asset_dict(d_alias)
    assert not ok
    assert any("template_type" in err.lower() for err in errors)

    # to_persisted_record chuẩn hóa 'title' -> 'text' trước khi validate
    persisted = to_persisted_record(d_alias)
    assert persisted["template_type"] == "text"


@pytest.mark.parametrize(
    "fixture,field,bad_val",
    [
        # Builtin với pack_id sai kiểu
        (VALID_BUILTIN_ASSET, "pack_id", {"bad": "type"}),
        (VALID_BUILTIN_ASSET, "pack_id", 123),
        (VALID_BUILTIN_ASSET, "pack_id", []),
        (VALID_BUILTIN_ASSET, "subcategory", None),  # NotRequired[str], không phải Optional[str]
        (VALID_BUILTIN_ASSET, "subcategory", 123),
        (VALID_BUILTIN_ASSET, "pack_name", None),
        (VALID_BUILTIN_ASSET, "pack_name", object()),
        (VALID_BUILTIN_ASSET, "tags", None),
        (VALID_BUILTIN_ASSET, "tags", ["valid", 123]),
        # Linked với pack_id sai kiểu và các trường optional malformed
        (VALID_LINKED_ASSET, "pack_id", {"bad": "type"}),
        (VALID_LINKED_ASSET, "pack_id", object()),
        (VALID_LINKED_ASSET, "source_id", 999),
        (VALID_LINKED_ASSET, "source_id", ""),
        (VALID_LINKED_ASSET, "external_path", "relative/path.mov"),
        (VALID_LINKED_ASSET, "external_path", None),
        # Managed với các trường optional malformed
        (VALID_MANAGED_VIDEO, "pack_id", None),  # Managed bắt buộc pack_id non-empty str
        (VALID_MANAGED_VIDEO, "pack_id", ""),
        (VALID_MANAGED_VIDEO, "pack_name", 123),
        (VALID_MANAGED_VIDEO, "subcategory", None),
        (VALID_MANAGED_VIDEO, "tags", "not-a-list"),
        (VALID_MANAGED_VIDEO, "relative_path", "../../outside.mp4"),
        (VALID_MANAGED_VIDEO, "library_path", "/absolute/path.mp4"),
    ],
)
def test_validate_table_driven_optional_fields_malformed_values(fixture, field, bad_val):
    d = copy.deepcopy(fixture)
    d[field] = bad_val
    ok, errors = validate_asset_dict(d)
    assert not ok, f"Field '{field}' với giá trị lỗi {bad_val!r} trên {fixture['source_kind']} đáng lẽ phải fail!"
    assert any(field in err.lower() for err in errors)


# ==============================================================================
# 3. KIỂM THỬ TUẦN TỰ HÓA TO_PERSISTED_RECORD
# ==============================================================================

class UncopyableHandle:
    """Đối tượng mô phỏng runtime handle có __deepcopy__ cố tình ném exception."""
    def __deepcopy__(self, memo):
        raise RuntimeError("Do not deepcopy this handle!")


def test_to_persisted_record_ignores_uncopyable_raw_handle():
    runtime_asset = copy.deepcopy(VALID_MANAGED_VIDEO)
    runtime_asset["_raw_obj"] = UncopyableHandle()
    runtime_asset["file_path"] = "/real/path/video.mp4"

    persisted = to_persisted_record(runtime_asset)
    assert "_raw_obj" not in persisted
    assert "file_path" not in persisted
    # Tuần tự hóa JSON thực tế phải thành công
    dumped = json.dumps(persisted, allow_nan=False)
    assert "Cinematic Drone Sunset" in dumped


def test_to_persisted_record_rejects_object_in_allowed_fields():
    # Probe Codex: subcategory=object() phải bị ném ValueError ở to_persisted_record
    d = copy.deepcopy(VALID_MANAGED_VIDEO)
    d["subcategory"] = object()
    with pytest.raises(ValueError, match="subcategory"):
        to_persisted_record(d)


def test_to_persisted_record_strict_write_rejects_zero_and_negative_duration():
    # Serializer strict không tự sửa 0 hoặc -1 thành None
    d_zero = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_zero["duration_sec"] = 0
    with pytest.raises(ValueError, match="duration_sec"):
        to_persisted_record(d_zero)

    d_neg = copy.deepcopy(VALID_MANAGED_VIDEO)
    d_neg["duration_sec"] = -1
    with pytest.raises(ValueError, match="duration_sec"):
        to_persisted_record(d_neg)


def test_to_persisted_record_json_dumps_allow_nan_false():
    for fix in (
        VALID_MANAGED_VIDEO,
        VALID_LINKED_ASSET,
        VALID_BUILTIN_ASSET,
        VALID_CUSTOM_CATEGORY_ASSET,
        VALID_TRANSITION_TEMPLATE,
    ):
        persisted = to_persisted_record(fix)
        # Đảm bảo json.dumps với allow_nan=False không bao giờ ném lỗi
        dumped = json.dumps(persisted, allow_nan=False)
        assert isinstance(dumped, str)


# ==============================================================================
# 4. KIỂM THỬ TO_UI_ASSET (COMPATIBILITY & LEGACY READ)
# ==============================================================================

def test_to_ui_asset_probe_preserves_sub_and_handles_inf_duration():
    # Probe Codex: sub='ambient', duration_sec=Infinity
    record = {
        "id": "probe_01",
        "name": "Ambient Sound",
        "sub": "ambient",
        "duration_sec": float("inf"),
        "category": "sfx",
    }
    ui = to_ui_asset(record)
    # 1. Bảo toàn sub='ambient', không bị ghi đè thành 'Tất cả'
    assert ui["sub"] == "ambient"
    # 2. duration_sec vô hạn phải được chuyển về None an toàn
    assert ui["duration_sec"] is None


def test_to_ui_asset_idempotence():
    raw = copy.deepcopy(VALID_CUSTOM_CATEGORY_ASSET)
    raw["sub"] = "bàn phím"
    ui1 = to_ui_asset(raw)
    ui2 = to_ui_asset(ui1)

    assert ui1["sub"] == ui2["sub"] == "bàn phím"
    assert ui1["tab"] == ui2["tab"] == "product_review"
    assert ui1["name"] == ui2["name"] == "Đánh Giá Bàn Phím Cơ"


def test_to_ui_asset_preserves_uncopyable_raw_handle_by_identity():
    handle = UncopyableHandle()
    record = {"id": "1", "name": "Item", "_raw_obj": handle}
    ui = to_ui_asset(record)
    # Bảo toàn đúng identity của đối tượng, không bị clone hay crash
    assert ui["_raw_obj"] is handle


def test_to_ui_asset_legacy_duration_zero_and_negative_normalized_to_none():
    record = {"id": "legacy_0", "name": "Zero Duration Clip", "duration_sec": 0}
    ui = to_ui_asset(record)
    assert ui["duration_sec"] is None

    record_neg = {"id": "legacy_neg", "name": "Negative Clip", "duration_sec": -1}
    ui_neg = to_ui_asset(record_neg)
    assert ui_neg["duration_sec"] is None


def test_validate_and_persist_companion_relative_paths():
    base = copy.deepcopy(VALID_MANAGED_VIDEO)
    base["preview_relative_path"] = "previews/sunset_preview.mp4"
    base["thumbnail_relative_path"] = "thumbnails/sunset_thumb.png"

    ok, errors = validate_asset_dict(base)
    assert ok is True
    assert errors == []

    persisted = to_persisted_record(base)
    assert persisted["preview_relative_path"] == "previews/sunset_preview.mp4"
    assert persisted["thumbnail_relative_path"] == "thumbnails/sunset_thumb.png"


def test_validate_rejects_malformed_companion_relative_paths():
    bad_traversal = copy.deepcopy(VALID_MANAGED_VIDEO)
    bad_traversal["preview_relative_path"] = "../outside/hack.mp4"
    ok, errors = validate_asset_dict(bad_traversal)
    assert ok is False
    assert any("preview_relative_path" in e for e in errors)

    bad_absolute = copy.deepcopy(VALID_MANAGED_VIDEO)
    bad_absolute["thumbnail_relative_path"] = "/etc/passwd.png"
    ok, errors = validate_asset_dict(bad_absolute)
    assert ok is False
    assert any("thumbnail_relative_path" in e for e in errors)

    bad_type = copy.deepcopy(VALID_MANAGED_VIDEO)
    bad_type["preview_relative_path"] = 12345
    ok, errors = validate_asset_dict(bad_type)
    assert ok is False
    assert any("preview_relative_path" in e for e in errors)


def test_to_ui_asset_preserves_companion_paths():
    record = {
        "id": "trans_01",
        "name": "Glitch Transition",
        "category": "templates",
        "template_type": "trans",
        "preview_relative_path": "previews/glitch.mp4",
        "thumbnail_relative_path": "thumbnails/glitch.png",
        "preview_path": "/var/chundvc/packs/glitch/previews/glitch.mp4",
        "thumbnail_path": "/var/chundvc/packs/glitch/thumbnails/glitch.png",
    }
    ui = to_ui_asset(record)
    assert ui["preview_relative_path"] == "previews/glitch.mp4"
    assert ui["thumbnail_relative_path"] == "thumbnails/glitch.png"
    assert ui["preview_path"] == "/var/chundvc/packs/glitch/previews/glitch.mp4"
    assert ui["thumbnail_path"] == "/var/chundvc/packs/glitch/thumbnails/glitch.png"
