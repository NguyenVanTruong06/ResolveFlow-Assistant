"""
tests/core/test_offline_asset_matcher.py
=============================================================================
Finite TDD Test Suite cho G06-06B1-I:
Pure Offline Asset Matcher & Contracts.

Kiểm tra:
1. Matcher constructor validation (catalog type, length bounds, alias_groups limits, caller mutation isolation).
2. Catalog intake & quarantine (schema invalid, staging, missing availability, duplicate IDs quarantine).
3. Request validation & category aliases (overlay->overlays, text->templates(text), conflicting template_type, bounds).
4. Duration filtering (known inside/outside bounds, unknown with/without bounds).
5. Availability filtering (offline default exclude vs opt-in reconnect flag, staging/missing always exclude).
6. Master reference verification (preview-only exclude, companion-as-master exclude, templates native guidance flag).
7. Deterministic scoring (exact name/stem/id +40, name token +25, tag tokens +15 each cap 30, alias +15, subcategory +10, Vietnamese NFKD, threshold 15, cap 100).
8. Tie-breaking (score DESC, online before offline, normalized name ASC, raw asset ID ASC, NO managed bonus).
9. Empty and unmatched states (ERR_CATALOG_EMPTY, ERR_CATALOG_INVALID, ERR_ASSETS_OFFLINE, ERR_NO_MATCH_FOUND).
=============================================================================
"""
from __future__ import annotations

import copy
import dataclasses
import math
import pytest

from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
)
from src.core.offline_asset_matcher import OfflineAssetMatcher


def _make_valid_record(
    asset_id: str,
    name: str,
    category: str = "broll",
    subcategory: str = "general",
    tags: list[str] | None = None,
    duration_sec: float | None = 10.0,
    availability: str = "online",
    source_kind: str = "managed",
    template_type: str | None = None,
    pack_id: str = "pack_01",
    pack_name: str = "Default Pack",
    **kwargs,
) -> dict:
    """Tạo bản ghi asset hợp lệ theo validate_asset_dict và StorageManager.assets()."""
    rec = {
        "schema_version": 1,
        "id": asset_id,
        "name": name,
        "source_kind": source_kind,
        "category": category,
        "subcategory": subcategory,
        "tags": list(tags or []),
        "template_type": template_type,
        "size_bytes": 1024 * 1024,
        "pack_id": pack_id,
        "pack_name": pack_name,
        "availability": availability,
        "duration_sec": duration_sec,
    }
    if source_kind == "managed":
        rec["relative_path"] = f"{category}/{name}.mp4"
        rec["library_path"] = f"02_cloud_downloads/packs/{pack_id}/{category}/{name}.mp4"
    elif source_kind == "linked":
        rec["external_path"] = f"/media/external/{name}.mov"
        rec["source_id"] = "src_ext_01"
    elif source_kind in ("builtin", "generated"):
        rec["relative_path"] = f"01_core/{name}.setting"
        rec["library_path"] = f"01_core/{name}.setting"

    rec.update(kwargs)
    return rec


# =============================================================================
# 1. CONSTRUCTOR VALIDATION & IMMUTABILITY
# =============================================================================

def test_matcher_constructor_valid_catalog_and_immutability():
    """Khởi tạo thành công với list/tuple và cô lập bộ nhớ với caller."""
    raw_catalog = [
        _make_valid_record("ast_01", "Drone Sunset"),
        _make_valid_record("ast_02", "City Traffic"),
    ]
    matcher = OfflineAssetMatcher(raw_catalog)
    assert matcher is not None

    # Caller mutate catalog sau khởi tạo không ảnh hưởng đến matcher
    raw_catalog.append(_make_valid_record("ast_03", "Forest Rain"))
    raw_catalog[0]["name"] = "Mutated Name"

    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Drone Sunset"))
    assert res.is_matched is True
    assert len(res.candidates) == 1
    assert res.candidates[0].asset_id == "ast_01"
    assert res.candidates[0].name == "Drone Sunset"


def test_matcher_constructor_invalid_catalog_structure():
    """Catalog không phải sequence hữu hạn hoặc vượt quá 100,000 records ném ValueError."""
    with pytest.raises(ValueError, match="catalog"):
        OfflineAssetMatcher("invalid_string_catalog")  # type: ignore

    with pytest.raises(ValueError, match="catalog"):
        OfflineAssetMatcher({"not": "a list"})  # type: ignore

    with pytest.raises(ValueError, match="catalog"):
        OfflineAssetMatcher(None)  # type: ignore

    # Vượt quá giới hạn 100,000 phần tử
    huge_catalog = [1] * 100_001
    with pytest.raises(ValueError, match="100000"):
        OfflineAssetMatcher(huge_catalog)  # type: ignore


def test_matcher_constructor_alias_groups_validation():
    """alias_groups phải là Mapping hợp lệ, tối đa 64 nhóm, tối đa 32 từ/nhóm."""
    # Hợp lệ
    valid_aliases = {
        "fast": ["nhanh", "rapid", "chớp"],
        "funny": ["hài hước", "vui", "joke"],
    }
    matcher = OfflineAssetMatcher([], alias_groups=valid_aliases)
    assert matcher is not None

    # Không phải Mapping
    with pytest.raises(ValueError, match="alias_groups"):
        OfflineAssetMatcher([], alias_groups=["not", "a", "dict"])  # type: ignore

    # Vượt 64 groups
    too_many_groups = {f"grp_{i}": ["term"] for i in range(65)}
    with pytest.raises(ValueError, match="64"):
        OfflineAssetMatcher([], alias_groups=too_many_groups)

    # Vượt 32 terms trong 1 group
    too_many_terms = {"large": [f"t_{i}" for i in range(33)]}
    with pytest.raises(ValueError, match="32"):
        OfflineAssetMatcher([], alias_groups=too_many_terms)

    # Key hoặc term không phải chuỗi non-blank
    with pytest.raises(ValueError, match="chuỗi"):
        OfflineAssetMatcher([], alias_groups={123: ["term"]})  # type: ignore
    with pytest.raises(ValueError, match="chuỗi"):
        OfflineAssetMatcher([], alias_groups={"grp": [123]})  # type: ignore


# =============================================================================
# 2. CATALOG INTAKE, QUARANTINE & DUPLICATE IDENTIFIERS
# =============================================================================

def test_matcher_catalog_quarantine_invalid_rows():
    """Row không hợp lệ schema hoặc thiếu availability bị cách ly và cảnh báo."""
    bad_rows = [
        "not_a_dict",
        {"id": "bad_1"},  # Thiếu name, schema_version,...
        _make_valid_record("bad_2", "Missing Avail", availability=""),  # availability rỗng
        _make_valid_record("bad_3", "None Avail", availability=None),  # availability None
        _make_valid_record("bad_4", "Unknown Avail", availability="unknown_xyz"),
    ]
    good_record = _make_valid_record("good_01", "Valid Drone Clip")
    catalog = bad_rows + [good_record]

    matcher = OfflineAssetMatcher(catalog)
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Valid Drone Clip"))

    assert res.is_matched is True
    assert len(res.candidates) == 1
    assert res.candidates[0].asset_id == "good_01"
    # Số bản ghi bị loại bỏ đúng bằng số bad_rows (5)
    assert res.rejected_record_count == len(bad_rows)
    assert len(res.warnings) >= len(bad_rows)


def test_matcher_catalog_quarantine_duplicate_asset_ids():
    """Nếu nhiều bản ghi có cùng asset_id, TOÀN BỘ nhóm trùng ID bị cách ly an toàn."""
    rec1 = _make_valid_record("dup_id", "First Version")
    rec2 = _make_valid_record("dup_id", "Second Version")
    rec3 = _make_valid_record("unique_id", "Unique Clip")

    matcher = OfflineAssetMatcher([rec1, rec2, rec3])
    # Tra cứu clip unique -> thành công
    res_uniq = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Unique Clip"))
    assert res_uniq.is_matched is True
    assert res_uniq.candidates[0].asset_id == "unique_id"
    assert res_uniq.rejected_record_count == 2  # Cả 2 bản ghi dup_id bị loại
    assert any("dup_id" in w for w in res_uniq.warnings)

    # Tra cứu clip trùng -> không tìm thấy vì đã bị cách ly
    res_dup = matcher.match(OfflineAssetMatchRequest("req_2", "broll", "First Version"))
    assert res_dup.is_matched is False


def test_matcher_catalog_handles_unserializable_extras_without_deepcopy_error():
    """Record chứa runtime extra _raw_obj không thể deepcopy được bỏ qua an toàn và record vẫn hợp lệ."""
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("Cannot deepcopy uncopyable object!")

    rec = _make_valid_record("safe_01", "Safe Clip")
    rec["_raw_obj"] = Uncopyable()  # Runtime extra không được dùng

    # Không được ném RuntimeError
    matcher = OfflineAssetMatcher([rec])
    # _raw_obj được bỏ qua khi trích xuất persisted view, record hợp lệ và được match
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Safe Clip"))
    assert res.rejected_record_count == 0
    assert res.is_matched is True
    assert res.candidates[0].asset_id == "safe_01"


# =============================================================================
# 3. REQUEST VALIDATION & CATEGORY ALIAS RESOLUTION
# =============================================================================

def test_matcher_request_validation_rules():
    """Request phải tuân thủ nghiêm ngặt các kiểu dữ liệu và giới hạn."""
    # Rỗng request_id / category / query_text
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("", "broll", "query")
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "", "query")
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "")

    # Category không hợp lệ (không canonical, không custom, không alias)
    matcher = OfflineAssetMatcher([])
    with pytest.raises(ValueError, match="Category"):
        matcher.match(OfflineAssetMatchRequest("req_1", "invalid category with space!", "query"))
    with pytest.raises(ValueError, match="Category"):
        matcher.match(OfflineAssetMatchRequest("req_1", "123_starts_with_number", "query"))
    with pytest.raises(ValueError, match="Category"):
        matcher.match(OfflineAssetMatchRequest("req_1", "all", "query"))  # 'all' là ID dành riêng (reserved)

    # top_k ngoài 1..20 hoặc bool
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", top_k=0)
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", top_k=21)
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", top_k=True)  # type: ignore

    # min/max duration bounds
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", min_duration_sec=-1.0)
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", max_duration_sec=0.0)
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", min_duration_sec=10.0, max_duration_sec=5.0)
    with pytest.raises(ValueError):
        OfflineAssetMatchRequest("req_1", "broll", "query", min_duration_sec=float("nan"))


def test_matcher_category_aliases_and_template_type():
    """Ánh xạ UI aliases (overlay, meme, lut, text, trans) sang Canonical Categories."""
    records = [
        _make_valid_record("ov_1", "Vignette Overlay", category="overlays"),
        _make_valid_record("me_1", "Funny Cat Meme", category="memes"),
        _make_valid_record("lut_1", "Teal Orange LUT", category="luts"),
        _make_valid_record("tmpl_txt", "Karaoke Title", category="templates", template_type="text"),
        _make_valid_record("tmpl_tr", "Flash Transition", category="templates", template_type="trans"),
    ]
    matcher = OfflineAssetMatcher(records)

    # 1. 'overlay' -> 'overlays'
    r1 = matcher.match(OfflineAssetMatchRequest("req_1", "overlay", "Vignette Overlay"))
    assert r1.is_matched is True
    assert r1.category == "overlays"

    # 2. 'meme' -> 'memes'
    r2 = matcher.match(OfflineAssetMatchRequest("req_2", "meme", "Funny Cat Meme"))
    assert r2.is_matched is True
    assert r2.category == "memes"

    # 3. 'lut' -> 'luts'
    r3 = matcher.match(OfflineAssetMatchRequest("req_3", "lut", "Teal Orange LUT"))
    assert r3.is_matched is True
    assert r3.category == "luts"

    # 4. 'text' -> 'templates' với template_type='text'
    r4 = matcher.match(OfflineAssetMatchRequest("req_4", "text", "Karaoke Title"))
    assert r4.is_matched is True
    assert r4.category == "templates"
    assert r4.candidates[0].template_type == "text"

    # 5. 'trans' -> 'templates' với template_type='trans'
    r5 = matcher.match(OfflineAssetMatchRequest("req_5", "trans", "Flash Transition"))
    assert r5.is_matched is True
    assert r5.category == "templates"
    assert r5.candidates[0].template_type == "trans"
    assert r5.candidates[0].is_native_guidance is True

    # 6. Xung đột alias và explicit template_type -> ValueError
    with pytest.raises(ValueError, match="template_type"):
        matcher.match(OfflineAssetMatchRequest("req_6", "text", "Flash Transition", template_type="trans"))

    with pytest.raises(ValueError, match="template_type"):
        matcher.match(OfflineAssetMatchRequest("req_7", "broll", "Some Clip", template_type="text"))


def test_matcher_custom_category():
    """Hỗ trợ danh mục custom hợp lệ theo pure helper is_valid_custom_category_id."""
    rec = _make_valid_record("cust_01", "Sponsor Logo", category="custom_sponsor")
    matcher = OfflineAssetMatcher([rec])

    res = matcher.match(OfflineAssetMatchRequest("req_1", "custom_sponsor", "Sponsor Logo"))
    assert res.is_matched is True
    assert res.category == "custom_sponsor"
    assert res.candidates[0].asset_id == "cust_01"


# =============================================================================
# 4. DURATION FILTERING (KNOWN VS UNKNOWN)
# =============================================================================

def test_matcher_duration_filtering_rules():
    """Thẩm định thời lượng: unknown duration chỉ được giữ khi không có ràng buộc min/max."""
    records = [
        _make_valid_record("ast_short", "Short Clip", duration_sec=3.0),
        _make_valid_record("ast_med", "Medium Clip", duration_sec=15.0),
        _make_valid_record("ast_long", "Long Clip", duration_sec=60.0),
        _make_valid_record("ast_unknown", "Unknown Duration Clip", duration_sec=None),
    ]
    matcher = OfflineAssetMatcher(records)

    # 1. Không có min/max: cả 4 clip đều được giữ nếu khớp query
    r_all = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Clip", top_k=10))
    assert len(r_all.candidates) == 4

    # 2. Có min=10.0, max=30.0:
    # - Short (3.0s) bị loại vì < 10
    # - Long (60.0s) bị loại vì > 30
    # - Unknown (None) BẮT BUỘC BỊ LOẠI VÌ THIẾU BẰNG CHỨNG (Decision 5)
    # - Medium (15.0s) ĐƯỢC GIỮ
    r_filtered = matcher.match(OfflineAssetMatchRequest(
        "req_2", "broll", "Clip", min_duration_sec=10.0, max_duration_sec=30.0, top_k=10
    ))
    assert len(r_filtered.candidates) == 1
    assert r_filtered.candidates[0].asset_id == "ast_med"

    # 3. Equality min=15.0, max=15.0 (bounds inclusive)
    r_exact = matcher.match(OfflineAssetMatchRequest(
        "req_3", "broll", "Clip", min_duration_sec=15.0, max_duration_sec=15.0
    ))
    assert len(r_exact.candidates) == 1
    assert r_exact.candidates[0].asset_id == "ast_med"


# =============================================================================
# 5. AVAILABILITY & RECONNECT FLAG
# =============================================================================

def test_matcher_availability_opt_in_and_reconnect_flag():
    """Offline asset mặc định bị loại, chỉ trả về khi allow_offline=True và có cờ requires_reconnect."""
    records = [
        _make_valid_record("ast_online", "Whoosh Sound", category="sfx", availability="online"),
        _make_valid_record("ast_offline", "Whoosh Sound 2", category="sfx", availability="offline"),
    ]
    matcher = OfflineAssetMatcher(records)

    # 1. allow_offline=False: chỉ nhận ast_online
    r_online_only = matcher.match(OfflineAssetMatchRequest("req_1", "sfx", "Whoosh Sound", allow_offline=False))
    assert len(r_online_only.candidates) == 1
    assert r_online_only.candidates[0].asset_id == "ast_online"
    assert r_online_only.candidates[0].requires_reconnect is False

    # 2. allow_offline=True: nhận cả hai, ast_offline có requires_reconnect=True
    r_both = matcher.match(OfflineAssetMatchRequest("req_2", "sfx", "Whoosh Sound", allow_offline=True, top_k=10))
    assert len(r_both.candidates) == 2
    cand_offline = next(c for c in r_both.candidates if c.asset_id == "ast_offline")
    assert cand_offline.requires_reconnect is True
    assert cand_offline.availability == "offline"


# =============================================================================
# 6. MASTER REFERENCE & COMPANION PREVIEWS
# =============================================================================

def test_matcher_preview_only_and_companion_rejection():
    """Bản ghi chỉ có preview companion mà không có master reference hợp lệ bị loại bỏ."""
    # Bản ghi chỉ có preview_relative_path, library_path rỗng/không an toàn
    rec_preview_only = {
        "schema_version": 1,
        "id": "ast_preview_only",
        "name": "Preview Only Clip",
        "source_kind": "managed",
        "category": "broll",
        "subcategory": "test",
        "tags": ["broll"],
        "size_bytes": 1024,
        "pack_id": "pack_1",
        "pack_name": "Pack 1",
        "preview_relative_path": "previews/clip.mp4",
        "library_path": "",  # Rỗng -> không có master
        "relative_path": "",
        "availability": "online",
    }
    matcher = OfflineAssetMatcher([rec_preview_only])
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Preview Only Clip"))
    assert res.is_matched is False
    assert res.rejected_record_count == 1


def test_matcher_candidate_does_not_leak_runtime_paths():
    """Candidate output tuyệt đối không trả về file_path hoặc preview_path."""
    rec = _make_valid_record("ast_path_check", "Path Check Clip")
    rec["file_path"] = "/secret/root/path.mp4"
    rec["preview_path"] = "/secret/root/preview.mp4"

    matcher = OfflineAssetMatcher([rec])
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Path Check Clip"))
    assert res.is_matched is True
    cand = res.candidates[0]
    assert not hasattr(cand, "file_path")
    assert not hasattr(cand, "preview_path")


# =============================================================================
# 7. DETERMINISTIC SCORING & VIETNAMESE ACCENTS
# =============================================================================

def test_matcher_scoring_signals_and_vietnamese_normalization():
    """Chấm điểm minh bạch theo tín hiệu và chuẩn hóa tiếng Việt bỏ dấu NFKD."""
    records = [
        # 1. Exact query match (+40) + name token (+25) = 65
        _make_valid_record("ast_exact", "Cinematic Sunset", tags=["video"]),
        # 2. Name token (+25) + 1 tag token (+15) = 40
        _make_valid_record("ast_token", "Sunset Over Ocean", tags=["cinematic"]),
        # 3. Only 1 tag token (+15)
        _make_valid_record("ast_tag", "Mountain Landscape", tags=["sunset"]),
        # 4. Tiếng Việt có dấu: "Hào hứng vui nhộn"
        _make_valid_record("ast_vn", "Nhạc Hào Hứng", tags=["vui nhộn", "sôi động"]),
        # 5. Điểm dưới 15 -> loại
        _make_valid_record("ast_low", "Completely Irrelevant", tags=["unrelated"]),
    ]
    matcher = OfflineAssetMatcher(records)

    # Truy vấn exact "Cinematic Sunset"
    res1 = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Cinematic Sunset"))
    cand_exact = next(c for c in res1.candidates if c.asset_id == "ast_exact")
    assert cand_exact.match_score >= 65
    assert "exact_name" in cand_exact.matched_signals
    assert "name_token" in cand_exact.matched_signals

    # Truy vấn tiếng Việt không dấu "hao hung" -> khớp "Hào Hứng" (+25 name_token)
    res2 = matcher.match(OfflineAssetMatchRequest("req_2", "broll", "hao hung"))
    assert res2.is_matched is True
    assert res2.candidates[0].asset_id == "ast_vn"

    # Truy vấn "sunset" đơn thuần -> ast_tag nhận +15 (vừa đủ ngưỡng 15)
    res3 = matcher.match(OfflineAssetMatchRequest("req_3", "broll", "sunset"))
    cand_tag = next(c for c in res3.candidates if c.asset_id == "ast_tag")
    assert cand_tag.match_score >= 15
    assert "tag_token" in cand_tag.matched_signals


def test_matcher_alias_group_matching():
    """Khớp nhóm alias mở rộng từ điển ngoài (+15 điểm)."""
    aliases = {
        "shake": ["rung", "giật", "vibrate"],
        "fast": ["nhanh", "rapid", "chớp"],
    }
    rec = _make_valid_record("ast_shake", "Camera Vibrate Effect", category="sfx")
    matcher = OfflineAssetMatcher([rec], alias_groups=aliases)

    # Truy vấn "rung" -> khớp alias group 'shake' với "Vibrate" trong name
    res = matcher.match(OfflineAssetMatchRequest("req_1", "sfx", "rung"))
    assert res.is_matched is True
    assert res.candidates[0].asset_id == "ast_shake"
    assert "alias_group" in res.candidates[0].matched_signals


# =============================================================================
# 8. TIE-BREAKING & SAME BASENAME DISAMBIGUATION
# =============================================================================

def test_matcher_tie_breaking_order():
    """Hòa điểm: Online trước Offline, sau đó normalized name ASC, sau đó raw ID ASC."""
    # 3 assets có cùng score vì cùng có name token 'whoosh' (+25)
    records = [
        _make_valid_record("ast_b", "Whoosh B", category="sfx", availability="online"),
        _make_valid_record("ast_a", "Whoosh A", category="sfx", availability="online"),
        _make_valid_record("ast_off", "Whoosh 0", category="sfx", availability="offline"),
    ]
    matcher = OfflineAssetMatcher(records)

    res = matcher.match(OfflineAssetMatchRequest("req_1", "sfx", "whoosh", allow_offline=True))
    assert len(res.candidates) == 3

    # Thứ tự mong đợi:
    # 1. Whoosh A (online, name 'whoosh a' < 'whoosh b')
    # 2. Whoosh B (online)
    # 3. Whoosh 0 (offline xếp sau online dù cùng score)
    assert res.candidates[0].asset_id == "ast_a"
    assert res.candidates[1].asset_id == "ast_b"
    assert res.candidates[2].asset_id == "ast_off"


def test_matcher_same_basename_different_packs_both_retained():
    """Cùng tên file nhưng khác pack/ID vẫn là 2 candidates độc lập."""
    rec1 = _make_valid_record("ast_pack1_whoosh", "whoosh.wav", category="sfx", pack_id="pack_1", pack_name="Pack 1")
    rec2 = _make_valid_record("ast_pack2_whoosh", "whoosh.wav", category="sfx", pack_id="pack_2", pack_name="Pack 2")

    matcher = OfflineAssetMatcher([rec1, rec2])
    res = matcher.match(OfflineAssetMatchRequest("req_1", "sfx", "whoosh"))

    assert len(res.candidates) == 2
    cand_ids = {c.asset_id for c in res.candidates}
    assert cand_ids == {"ast_pack1_whoosh", "ast_pack2_whoosh"}


# =============================================================================
# 9. UNMATCHED REASONS & STRUCTURED ERROR CODES
# =============================================================================

def test_matcher_unmatched_empty_catalog():
    """Kho rỗng trả về ERR_CATALOG_EMPTY và gợi ý nạp pack."""
    matcher = OfflineAssetMatcher([])
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))

    assert res.is_matched is False
    assert res.candidates == ()
    assert res.unmatched_reason == "ERR_CATALOG_EMPTY"
    assert "nạp thêm gói tài nguyên" in res.suggested_action.lower()


def test_matcher_unmatched_all_invalid_catalog():
    """Kho toàn bản ghi lỗi bị cách ly trả về ERR_CATALOG_INVALID."""
    bad_catalog = [{"not": "valid"}, {"bad": 123}]
    matcher = OfflineAssetMatcher(bad_catalog)
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))

    assert res.is_matched is False
    assert res.unmatched_reason == "ERR_CATALOG_INVALID"


def test_matcher_unmatched_assets_offline():
    """Có tài nguyên khớp nhưng bị offline và allow_offline=False trả về ERR_ASSETS_OFFLINE."""
    rec = _make_valid_record("ast_off_only", "Super Secret Drone", availability="offline")
    matcher = OfflineAssetMatcher([rec])

    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Super Secret Drone", allow_offline=False))
    assert res.is_matched is False
    assert res.unmatched_reason == "ERR_ASSETS_OFFLINE"
    assert "kết nối ổ cứng" in res.suggested_action.lower()


def test_matcher_unmatched_no_match_found():
    """Kho có tài nguyên hợp lệ nhưng không khớp từ khóa trả về ERR_NO_MATCH_FOUND."""
    rec = _make_valid_record("ast_nature", "Pine Tree Forest")
    matcher = OfflineAssetMatcher([rec])

    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Quantum Computer Physics"))
    assert res.is_matched is False
    assert res.unmatched_reason == "ERR_NO_MATCH_FOUND"


# =============================================================================
# 10. R1 REGRESSION & PROBES TESTS (GROUPS A - F)
# =============================================================================

def test_probe_request_bigint_overflow_error_prevented():
    """Group A: min/max duration nhận bigint (10**400) ném ValueError có kiểm soát, không OverflowError."""
    with pytest.raises(ValueError, match="overflow"):
        OfflineAssetMatchRequest("req_1", "broll", "sunset", min_duration_sec=10**400)

    with pytest.raises(ValueError, match="overflow"):
        OfflineAssetMatchRequest("req_1", "broll", "sunset", max_duration_sec=10**400)


def test_probe_request_lone_surrogate_rejected():
    """Group A: Chuỗi UTF-8 chứa lone surrogate bị từ chối bằng ValueError."""
    with pytest.raises(ValueError, match="lone surrogate"):
        OfflineAssetMatchRequest("req_1", "broll", "query \ud800 bad")

    with pytest.raises(ValueError, match="lone surrogate"):
        OfflineAssetMatchRequest("req_\ud800", "broll", "query")

    with pytest.raises(ValueError, match="lone surrogate"):
        OfflineAssetMatchRequest("req_1", "broll_\ud800", "query")


def test_probe_request_revalidate_on_match_entry():
    """Group A: match() re-validate mọi trường, kể cả khi frozen DTO bị can thiệp thuộc tính."""
    matcher = OfflineAssetMatcher([_make_valid_record("ast_01", "Sunset")])
    req = OfflineAssetMatchRequest("req_1", "broll", "sunset")

    # Can thiệp top_k = False
    object.__setattr__(req, "top_k", False)
    with pytest.raises(ValueError, match="top_k"):
        matcher.match(req)

    # Khôi phục top_k, can thiệp query_text = []
    object.__setattr__(req, "top_k", 5)
    object.__setattr__(req, "query_text", [])
    with pytest.raises(ValueError, match="query_text"):
        matcher.match(req)

    # Can thiệp allow_offline = "yes"
    object.__setattr__(req, "query_text", "sunset")
    object.__setattr__(req, "allow_offline", "yes")
    with pytest.raises(ValueError, match="allow_offline"):
        matcher.match(req)


def test_probe_runtime_raw_obj_ignored_without_deepcopy_or_repr():
    """Group B: Catalog item chứa runtime extra _raw_obj=object() không bị validate_asset_dict từ chối."""
    class UncopyableHandle:
        def __deepcopy__(self, memo):
            raise RuntimeError("TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI DEEPCOPY TRÊN RUNTIME HANDLE!")
        def __repr__(self):
            raise RuntimeError("TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI REPR TRÊN RUNTIME HANDLE!")

    rec = _make_valid_record("ast_01", "Sunset")
    rec["_raw_obj"] = UncopyableHandle()

    matcher = OfflineAssetMatcher([rec])
    assert matcher._rejected_count == 0

    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))
    assert res.is_matched is True
    assert len(res.candidates) == 1
    assert res.candidates[0].asset_id == "ast_01"


def test_probe_runtime_origin_library_id_validation():
    """Group B: origin_library_id sai kiểu bị quarantine, kiểu đúng được giữ lại."""
    # List thay vì str -> quarantine
    bad_rec = _make_valid_record("ast_bad", "Sunset", origin_library_id=["library"])
    matcher_bad = OfflineAssetMatcher([bad_rec])
    assert matcher_bad._rejected_count == 1
    assert "origin_library_id" in matcher_bad._warnings[0]

    # Valid str -> pass và truyền sang candidate
    good_rec = _make_valid_record("ast_good", "Sunset", origin_library_id="lib_vietnam_01")
    matcher_good = OfflineAssetMatcher([good_rec])
    assert matcher_good._rejected_count == 0
    res = matcher_good.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))
    assert res.is_matched is True
    assert res.candidates[0].origin_library_id == "lib_vietnam_01"


def test_probe_master_companion_pack_relative_coordinates():
    """Group C: Managed asset có relative_path trùng preview_relative_path bị loại trừ an toàn."""
    rec = _make_valid_record(
        "ast_preview_only",
        "demo.mp4",
        category="broll",
        relative_path="previews/demo.mp4",
        library_path="02_cloud_downloads/packs/pack_01/previews/demo.mp4",
        preview_relative_path="previews/demo.mp4",
    )
    matcher = OfflineAssetMatcher([rec])
    assert matcher._rejected_count == 1
    assert "preview/thumbnail companion" in matcher._warnings[0]


def test_probe_builtin_generated_companion_as_master_rejected():
    """Group C: Builtin/generated asset trỏ relative_path vào preview hoặc thiếu relative/file path bị loại."""
    # Case 1: Trùng preview_relative_path
    rec1 = _make_valid_record(
        "ast_b1",
        "demo.setting",
        category="templates",
        template_type="trans",
        source_kind="builtin",
        relative_path="previews/demo.setting",
        preview_relative_path="previews/demo.setting",
    )
    matcher1 = OfflineAssetMatcher([rec1])
    assert matcher1._rejected_count == 1

    # Case 2: Chỉ có library_path, thiếu cả relative_path và file_path
    rec2 = _make_valid_record(
        "ast_b2",
        "demo.setting",
        category="templates",
        template_type="trans",
        source_kind="builtin",
    )
    del rec2["relative_path"]  # Chỉ còn library_path
    matcher2 = OfflineAssetMatcher([rec2])
    assert matcher2._rejected_count == 1


def test_probe_missing_and_staging_availability_not_schema_invalid():
    """Group D: availability missing/staging là hợp lệ (không tăng rejected_count), catalog chỉ có missing trả ERR_NO_MATCH_FOUND."""
    rec_missing = _make_valid_record("ast_miss", "Sunset", availability="missing")
    rec_staging = _make_valid_record("ast_stage", "Sunrise", availability="staging")

    matcher = OfflineAssetMatcher([rec_missing, rec_staging])
    # Không tăng rejected_count
    assert matcher._rejected_count == 0
    assert len(matcher._warnings) == 0

    # Lọc cứng: missing/staging không bao giờ là candidate, kể cả khi allow_offline=True
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "sunset", allow_offline=True))
    assert res.is_matched is False
    # Không phải ERR_CATALOG_INVALID mà là ERR_NO_MATCH_FOUND
    assert res.unmatched_reason == "ERR_NO_MATCH_FOUND"


def test_probe_query_extension_exact_stem_score():
    """Group E: Query 'Sunset.mp4' với name 'Sunset' đạt đúng 65 điểm (exact 40 + name token 25)."""
    rec = _make_valid_record("ast_sunset", "Sunset", category="broll")
    matcher = OfflineAssetMatcher([rec])

    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "Sunset.mp4"))
    assert res.is_matched is True
    assert len(res.candidates) == 1
    assert res.candidates[0].match_score == 65
    assert "exact_name" in res.candidates[0].matched_signals
    assert "name_token" in res.candidates[0].matched_signals


def test_probe_alias_normalized_collision_rejected_both_orders():
    """Group E: Xung đột normalized alias key (Mood vs mood) bị ném ValueError ở cả 2 thứ tự truyền."""
    # Thứ tự 1: Mood trước, mood sau
    with pytest.raises(ValueError, match="Xung đột alias group key"):
        OfflineAssetMatcher([], alias_groups={"Mood": ["calm"], "mood": ["rain"]})

    # Thứ tự 2: mood trước, Mood sau
    with pytest.raises(ValueError, match="Xung đột alias group key"):
        OfflineAssetMatcher([], alias_groups={"mood": ["rain"], "Mood": ["calm"]})


def test_probe_warning_budget_and_shuffle_invariance():
    """Group F: 100 invalid rows có ID 2000 chars không vượt quá 64 entries và 16384 bytes; shuffle không đổi kết quả."""
    long_id = "ast_" + ("x" * 2000)
    invalid_rows = []
    for i in range(100):
        invalid_rows.append({
            "schema_version": 1,
            "id": f"{long_id}_{i:03d}",
            "availability": "invalid_availability_val",
        })

    matcher1 = OfflineAssetMatcher(invalid_rows)
    assert matcher1._rejected_count == 100
    assert len(matcher1._warnings) <= 64

    total_bytes = sum(len(w.encode("utf-8")) for w in matcher1._warnings)
    assert total_bytes <= 16384
    # Marker lược bớt xuất hiện ở cuối
    assert "[Đã lược bớt các cảnh báo chẩn đoán vượt quá giới hạn an toàn]" in matcher1._warnings[-1]

    # Đảo thứ tự (shuffle): cảnh báo chẩn đoán vẫn hoàn toàn giống nhau
    shuffled_rows = list(reversed(invalid_rows))
    matcher2 = OfflineAssetMatcher(shuffled_rows)
    assert matcher2._warnings == matcher1._warnings
    assert matcher2._rejected_count == matcher1._rejected_count


def test_probe_exact_scores_oracle():
    """Group E: Kiểm tra giá trị điểm số chính xác của từng signal đơn lẻ và kết hợp."""
    # 1. Tag-only match: đúng 15 điểm
    rec_tag = _make_valid_record("ast_tag", "Delta", tags=["rain"])
    m_tag = OfflineAssetMatcher([rec_tag])
    res_tag = m_tag.match(OfflineAssetMatchRequest("req_1", "broll", "rain"))
    assert res_tag.candidates[0].match_score == 15

    # 2. Alias-only match: đúng 15 điểm
    rec_alias = _make_valid_record("ast_alias", "Delta", tags=["storm"])
    m_alias = OfflineAssetMatcher([rec_alias], alias_groups={"weather": ["storm", "rain"]})
    res_alias = m_alias.match(OfflineAssetMatchRequest("req_1", "broll", "rain"))
    assert res_alias.candidates[0].match_score == 15

    # 3. Subcategory-only match (10 điểm): dưới ngưỡng 15 điểm -> không match
    rec_sub = _make_valid_record("ast_sub", "Delta", subcategory="storm")
    m_sub = OfflineAssetMatcher([rec_sub])
    res_sub = m_sub.match(OfflineAssetMatchRequest("req_1", "broll", "storm"))
    assert res_sub.is_matched is False
    assert res_sub.unmatched_reason == "ERR_NO_MATCH_FOUND"

    # 4. Hai tags trùng: 2 * 15 = 30 điểm
    rec_2tags = _make_valid_record("ast_2tags", "Delta", tags=["rain", "storm"])
    m_2tags = OfflineAssetMatcher([rec_2tags])
    res_2tags = m_2tags.match(OfflineAssetMatchRequest("req_1", "broll", "rain storm"))
    assert res_2tags.candidates[0].match_score == 30

    # 5. Tất cả signals kết hợp: 40 (exact) + 25 (name) + 30 (tags) + 15 (alias) + 10 (sub) = 120 -> clamp 100
    rec_all = _make_valid_record(
        "ast_all",
        "Rain Storm",
        category="broll",
        subcategory="nature",
        tags=["thunder", "lightning"],
    )
    m_all = OfflineAssetMatcher([rec_all], alias_groups={"tempest": ["thunder", "flash"]})
    res_all = m_all.match(
        OfflineAssetMatchRequest(
            "req_1",
            "broll",
            "Rain Storm",
            scene_context="thunder lightning flash nature",
        )
    )
    assert res_all.candidates[0].match_score == 100


# =============================================================================
# 11. R2 RESIDUAL PROBES TESTS (BRANCHES A / B / F)
# =============================================================================

def test_probe_residual_utf8_id_and_source_id_quarantined():
    """R2 (Nhóm B): asset_id hoặc source_id chứa lone surrogate bị quarantine, không lọt candidate."""
    # 1. ID chứa lone surrogate
    rec_bad_id = _make_valid_record("bad_\ud800", "Sunset")
    matcher1 = OfflineAssetMatcher([rec_bad_id])
    assert matcher1._rejected_count == 1
    assert "id không hợp lệ hoặc chứa ký tự UTF-8 không hợp lệ" in matcher1._warnings[0]
    res1 = matcher1.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))
    assert res1.is_matched is False

    # 2. source_id chứa lone surrogate
    rec_bad_source = _make_valid_record("good_id", "Sunset", source_kind="linked", source_id="src_\ud800")
    matcher2 = OfflineAssetMatcher([rec_bad_source])
    assert matcher2._rejected_count == 1
    assert "source_id không hợp lệ" in matcher2._warnings[0]
    res2 = matcher2.match(OfflineAssetMatchRequest("req_1", "broll", "sunset"))
    assert res2.is_matched is False


def test_probe_residual_warning_unicode_sanitized_and_mixed_good():
    """R2 (Nhóm F): Lỗi schema chứa lone surrogate trong category không gây UnicodeEncodeError, good row vẫn match."""
    bad_rec = _make_valid_record("bad_cat", "Sunset", category="bad_\ud800")
    good_rec = _make_valid_record("good_row", "Sunrise", category="broll")

    # Không được ném UnicodeEncodeError
    matcher = OfflineAssetMatcher([bad_rec, good_rec])
    assert matcher._rejected_count == 1

    # Toàn bộ warnings mã hóa UTF-8 an toàn
    for w in matcher._warnings:
        encoded = w.encode("utf-8")
        assert isinstance(encoded, bytes)

    # Good row vẫn được match bình thường
    res = matcher.match(OfflineAssetMatchRequest("req_1", "broll", "sunrise"))
    assert res.is_matched is True
    assert len(res.candidates) == 1
    assert res.candidates[0].asset_id == "good_row"
    assert res.rejected_record_count == 1


def test_probe_residual_public_type_rejects_simplenamespace_and_non_request():
    """R2 (Nhóm A): Nominal isinstance OfflineAssetMatchRequest gate từ chối SimpleNamespace, dict, None bằng ValueError."""
    import types

    matcher = OfflineAssetMatcher([_make_valid_record("ast_01", "Sunset")])

    # 1. SimpleNamespace có đầy đủ tất cả các trường
    ns_full = types.SimpleNamespace(
        request_id="req_1",
        category="broll",
        query_text="sunset",
        scene_context="",
        emotion_or_mood="",
        template_type=None,
        subcategory_constraint=None,
        min_duration_sec=None,
        max_duration_sec=None,
        top_k=5,
        allow_offline=False,
    )
    with pytest.raises(ValueError, match="OfflineAssetMatchRequest"):
        matcher.match(ns_full)  # type: ignore

    # 2. SimpleNamespace chỉ có một vài trường (partial)
    ns_partial = types.SimpleNamespace(request_id="r", category="broll", query_text="sunset")
    with pytest.raises(ValueError, match="OfflineAssetMatchRequest"):
        matcher.match(ns_partial)  # type: ignore

    # 3. None, dict, list, int, object()
    for bad_req in (None, {}, [], 123, object()):
        with pytest.raises(ValueError, match="OfflineAssetMatchRequest"):
            matcher.match(bad_req)  # type: ignore
