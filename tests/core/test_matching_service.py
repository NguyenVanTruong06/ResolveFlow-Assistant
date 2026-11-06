"""
tests/core/test_matching_service.py
=============================================================================
Finite TDD Test Suite cho G06-06B2-I:
Per-Batch Catalog Provider Bridge (OfflineAssetMatchingService).

Kiểm tra:
1. Constructor: callable provider required, 0 provider calls at init, alias groups ownership and bounds.
2. Request validation: requests sequence check, max batch size 256, empty batch returns () with 0 provider calls,
   non-request rejection, duplicate request_id rejection before provider call.
3. Provider calls: exactly 1 call per non-empty valid batch, single matcher instance per batch, exact order.
4. Per-batch freshness: next batch fetches fresh catalog, reflecting root/catalog changes without stale cache.
5. Error propagation: provider exceptions (OSError, RuntimeError) propagate untouched; malformed catalog raises ValueError.
6. Matcher delegation: empty catalog, offline opt-in, missing/staging, master vs companion, native guidance.
=============================================================================
"""
from __future__ import annotations

import types
from typing import Any, Dict, List
from unittest.mock import MagicMock
import pytest

from src.core.contracts.asset_matching_contracts import (
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
)
from src.core.matching_service import (
    MAX_BATCH_SIZE,
    OfflineAssetMatchingService,
)


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
    """Tạo bản ghi asset hợp lệ chuẩn schema."""
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
# 1. CONSTRUCTOR VALIDATION & ZERO PROVIDER CALLS AT INIT
# =============================================================================

def test_service_constructor_valid_and_zero_provider_calls():
    """Khởi tạo thành công; tuyệt đối không gọi catalog_provider trong constructor."""
    mock_provider = MagicMock(return_value=[_make_valid_record("ast_01", "Sunset")])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    assert service is not None
    # Provider chưa hề được gọi
    assert mock_provider.call_count == 0


def test_service_constructor_invalid_provider_callable():
    """catalog_provider không phải callable ném ValueError."""
    for bad_provider in (None, "not_callable", 123, [], {}, object()):
        with pytest.raises(ValueError, match="callable"):
            OfflineAssetMatchingService(catalog_provider=bad_provider)  # type: ignore


def test_service_constructor_alias_groups_ownership_and_bounds():
    """alias_groups tuân thủ giới hạn 64 nhóm, 32 từ, UTF-8 và cô lập mutation từ caller."""
    caller_aliases = {
        "vibrate": ["rung", "giat"],
    }
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider, alias_groups=caller_aliases)

    # Caller mutate danh sách ngoài
    caller_aliases["vibrate"].append("lac")
    caller_aliases["new_group"] = ["test"]

    # Dữ liệu nội bộ của service không bị thay đổi
    assert service._alias_groups is not None
    assert service._alias_groups["vibrate"] == ("rung", "giat")
    assert "new_group" not in service._alias_groups

    # Xung đột normalized key bị từ chối
    with pytest.raises(ValueError, match="Xung đột alias group key"):
        OfflineAssetMatchingService(
            catalog_provider=mock_provider,
            alias_groups={"Mood": ["calm"], "mood": ["rain"]},
        )


# =============================================================================
# 2. REQUEST & BATCH STRUCTURAL VALIDATION (0 PROVIDER CALLS ON FAILURE)
# =============================================================================

def test_service_match_batch_empty_returns_empty_tuple_zero_provider_calls():
    """Batch rỗng trả về () ngay lập tức và không gọi catalog_provider."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    res = service.match_batch([])
    assert res == ()
    assert mock_provider.call_count == 0

    res_tuple = service.match_batch(())
    assert res_tuple == ()
    assert mock_provider.call_count == 0


def test_service_match_batch_invalid_sequence_type():
    """requests không phải list/tuple (generator, dict, string, None) ném ValueError trước khi gọi provider."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    gen = (OfflineAssetMatchRequest(f"req_{i}", "broll", "sunset") for i in range(2))
    with pytest.raises(ValueError, match="Sequence"):
        service.match_batch(gen)  # type: ignore
    assert mock_provider.call_count == 0

    with pytest.raises(ValueError, match="Sequence"):
        service.match_batch("invalid_string_requests")  # type: ignore
    assert mock_provider.call_count == 0


def test_service_match_batch_size_limit_exceeded():
    """Vượt quá MAX_BATCH_SIZE (256) ném ValueError và 0 provider call."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    oversized = [OfflineAssetMatchRequest(f"req_{i}", "broll", "query") for i in range(MAX_BATCH_SIZE + 1)]
    with pytest.raises(ValueError, match="256"):
        service.match_batch(oversized)
    assert mock_provider.call_count == 0


def test_service_match_batch_rejects_non_request_objects_zero_provider_calls():
    """Batch chứa đối tượng không phải OfflineAssetMatchRequest bị ném ValueError trước khi gọi provider."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    # SimpleNamespace bị từ chối
    ns = types.SimpleNamespace(request_id="r", category="broll", query_text="sunset")
    with pytest.raises(ValueError, match="OfflineAssetMatchRequest"):
        service.match_batch([ns])  # type: ignore
    assert mock_provider.call_count == 0

    # Primitives bị từ chối
    for bad_item in (None, {}, [], 123, "req_string"):
        with pytest.raises(ValueError, match="OfflineAssetMatchRequest"):
            service.match_batch([bad_item])  # type: ignore
        assert mock_provider.call_count == 0


def test_service_match_batch_rejects_duplicate_request_id_zero_provider_calls():
    """Trùng lặp request_id trong cùng một batch bị ném ValueError trước khi gọi provider."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    req1 = OfflineAssetMatchRequest("same_id", "broll", "sunset")
    req2 = OfflineAssetMatchRequest("same_id", "sfx", "whoosh")

    with pytest.raises(ValueError, match="Trùng lặp request_id"):
        service.match_batch([req1, req2])
    assert mock_provider.call_count == 0


def test_service_match_batch_revalidates_mutated_request_zero_provider_calls():
    """Request bị can thiệp thuộc tính sau post_init (top_k=False, lone surrogate) bị ném ValueError trước provider."""
    mock_provider = MagicMock(return_value=[])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    req = OfflineAssetMatchRequest("req_1", "broll", "sunset")
    object.__setattr__(req, "top_k", False)

    with pytest.raises(ValueError, match="top_k"):
        service.match_batch([req])
    assert mock_provider.call_count == 0


# =============================================================================
# 3. SINGLE CALL PER BATCH & ORDER PRESERVATION
# =============================================================================

def test_service_match_request_single_call_and_exact_result():
    """match_request gọi provider đúng 1 lần và trả về kết quả khớp duy nhất."""
    rec = _make_valid_record("ast_01", "Sunset Drone", category="broll")
    mock_provider = MagicMock(return_value=[rec])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    req = OfflineAssetMatchRequest("req_01", "broll", "Sunset Drone")
    res = service.match_request(req)

    assert isinstance(res, OfflineAssetMatchResult)
    assert res.is_matched is True
    assert res.candidates[0].asset_id == "ast_01"
    assert mock_provider.call_count == 1


def test_service_match_batch_single_call_for_multiple_requests():
    """Batch gồm nhiều requests chỉ gọi catalog_provider ĐÚNG 1 LẦN duy nhất."""
    rec1 = _make_valid_record("ast_broll", "Sunset Drone", category="broll")
    rec2 = _make_valid_record("ast_sfx", "Cinematic Whoosh", category="sfx")
    rec3 = _make_valid_record("ast_bgm", "Epic Piano", category="bgm")

    mock_provider = MagicMock(return_value=[rec1, rec2, rec3])
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    requests = [
        OfflineAssetMatchRequest("req_1", "broll", "Sunset Drone"),
        OfflineAssetMatchRequest("req_2", "sfx", "Cinematic Whoosh"),
        OfflineAssetMatchRequest("req_3", "bgm", "Epic Piano"),
    ]

    results = service.match_batch(requests)

    # Đúng 1 provider call
    assert mock_provider.call_count == 1
    # Số lượng kết quả bằng số requests và đúng thứ tự
    assert len(results) == 3
    assert results[0].request_id == "req_1"
    assert results[0].candidates[0].asset_id == "ast_broll"

    assert results[1].request_id == "req_2"
    assert results[1].candidates[0].asset_id == "ast_sfx"

    assert results[2].request_id == "req_3"
    assert results[2].candidates[0].asset_id == "ast_bgm"


# =============================================================================
# 4. PER-BATCH FRESHNESS & ZERO STALE CACHE
# =============================================================================

def test_service_next_batch_fetches_fresh_catalog_without_stale_cache():
    """Mỗi batch tiếp theo gọi provider mới, phản ánh sự thay đổi trạng thái kho ngay lập tức."""
    catalog_call_1 = [_make_valid_record("ast_01", "Alpha Sunset", category="broll")]
    catalog_call_2 = [_make_valid_record("ast_02", "Beta Traffic", category="broll")]

    provider_calls = [catalog_call_1, catalog_call_2]
    def dynamic_provider():
        return provider_calls.pop(0)

    service = OfflineAssetMatchingService(catalog_provider=dynamic_provider)

    # Batch 1: Có ast_01 (Alpha Sunset)
    req1 = OfflineAssetMatchRequest("req_1", "broll", "Alpha Sunset")
    res1 = service.match_batch([req1])
    assert res1[0].is_matched is True
    assert res1[0].candidates[0].asset_id == "ast_01"

    # Batch 2: Kho đã cập nhật sang catalog_call_2 (chỉ còn Beta Traffic, không còn Alpha Sunset)
    req2 = OfflineAssetMatchRequest("req_2", "broll", "Alpha Sunset")
    res2 = service.match_batch([req2])
    assert res2[0].is_matched is False
    assert res2[0].unmatched_reason == "ERR_NO_MATCH_FOUND"

    # Kiểm tra ast_02 khớp với từ khóa mới
    req3 = OfflineAssetMatchRequest("req_3", "broll", "Beta Traffic")
    # Cần nạp thêm vào dynamic_provider để gọi batch 3
    catalog_call_3 = [_make_valid_record("ast_02", "Beta Traffic", category="broll")]
    provider_calls.append(catalog_call_3)
    res3 = service.match_batch([req3])
    assert res3[0].is_matched is True
    assert res3[0].candidates[0].asset_id == "ast_02"


# =============================================================================
# 5. ERROR PROPAGATION (ZERO SWALLOW / ZERO STALE FALLBACK)
# =============================================================================

def test_service_provider_exception_propagates_untouched():
    """Nếu catalog_provider ném OSError hoặc exception khác, lỗi được ném ra nguyên bản."""
    def failing_provider():
        raise OSError("Ổ cứng lưu trữ đã bị ngắt kết nối vật lý (unplug).")

    service = OfflineAssetMatchingService(catalog_provider=failing_provider)
    req = OfflineAssetMatchRequest("req_1", "broll", "sunset")

    with pytest.raises(OSError, match="ngắt kết nối vật lý"):
        service.match_batch([req])


def test_service_provider_malformed_catalog_propagates_value_error():
    """Nếu catalog_provider trả về cấu trúc sai (không phải list/tuple hoặc >100k), ném ValueError từ Matcher."""
    mock_bad_provider = MagicMock(return_value="not_a_list")
    service = OfflineAssetMatchingService(catalog_provider=mock_bad_provider)
    req = OfflineAssetMatchRequest("req_1", "broll", "sunset")

    with pytest.raises(ValueError, match="sequence"):
        service.match_batch([req])


# =============================================================================
# 6. MATCHER DELEGATION INTEGRITY
# =============================================================================

def test_service_matcher_delegation_features():
    """Service ủy quyền đầy đủ các quy tắc nghiệp vụ của Matcher (quarantine, offline, template, native)."""
    records = [
        # 1. Hợp lệ online
        _make_valid_record("ast_valid", "City Sunrise", category="broll"),
        # 2. Offline asset
        _make_valid_record("ast_off", "Secret Footage", category="broll", availability="offline"),
        # 3. Native transition
        _make_valid_record("ast_trans", "Flash White", category="templates", template_type="trans"),
        # 4. Bad row (quarantined)
        {"id": "bad_row_no_schema"},
    ]
    mock_provider = MagicMock(return_value=records)
    service = OfflineAssetMatchingService(catalog_provider=mock_provider)

    # Tra cứu broll bình thường -> ast_valid khớp, bad row bị cảnh báo
    res_broll = service.match_request(OfflineAssetMatchRequest("req_1", "broll", "City Sunrise"))
    assert res_broll.is_matched is True
    assert res_broll.candidates[0].asset_id == "ast_valid"
    assert res_broll.rejected_record_count == 1

    # Tra cứu offline khi allow_offline=False -> ERR_ASSETS_OFFLINE
    res_off_denied = service.match_request(OfflineAssetMatchRequest("req_2", "broll", "Secret Footage", allow_offline=False))
    assert res_off_denied.is_matched is False
    assert res_off_denied.unmatched_reason == "ERR_ASSETS_OFFLINE"

    # Tra cứu offline khi allow_offline=True -> matched kèm requires_reconnect
    res_off_allowed = service.match_request(OfflineAssetMatchRequest("req_3", "broll", "Secret Footage", allow_offline=True))
    assert res_off_allowed.is_matched is True
    assert res_off_allowed.candidates[0].requires_reconnect is True

    # Tra cứu transition native guidance
    res_trans = service.match_request(OfflineAssetMatchRequest("req_4", "templates", "Flash White", template_type="trans"))
    assert res_trans.is_matched is True
    assert res_trans.candidates[0].is_native_guidance is True
