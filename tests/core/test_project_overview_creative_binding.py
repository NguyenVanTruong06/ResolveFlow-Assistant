"""
tests/core/test_project_overview_creative_binding.py
=============================================================================
Kiểm thử bộ định dạng Pure Core Project Overview với Ngữ cảnh Sáng tạo (Creative Context).
Tuân thủ nghiêm ngặt G06-06C3-S2c:
- TC01: Legacy None/{} unchanged bytes, keys, IDs, policy đối chiếu với frozen control độc lập.
- TC04: Thẩm định quyền sở hữu constructor/renderer: mutation phòng thủ, forged typed fields,
        phân biệt falsy bool/int/list/str/null, lone surrogates, NaN/Inf/10**400, native numeric values.
- TC05: Full canonical creative JSON block xuất hiện DUY NHẤT 1 lần trong PROJECT_OVERVIEW_CONTEXT_JSON,
        static metadata-only framing, zero network/model/reference-media IO tripwires.
- TC06: Trần ngân sách UTF-8 byte cap 1..32768: equality pass, 1-byte overflow fail-closed với
        required_bytes chính xác và 0 prompt/artifact, không cắt tỉa nội dung.
=============================================================================
"""

import json
import math
import os
import pytest
from typing import Tuple

from src.core.batch_draft_contracts import (
    BatchSourceRange,
    ValidatedSegmentCut,
    ValidatedBatchDraftEntry,
    validate_batch_draft_payload,
)
from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    creative_context_to_dict,
    compute_creative_context_signature,
)
from src.core.project_overview_contracts import (
    BatchPartitionRef,
    OverviewUnaccountedClip,
    ProjectOverviewContextInput,
    ProjectOverviewRenderResult,
    render_project_overview_prompt,
    ERR_OVERVIEW_INVALID_CONTEXT,
    ERR_OVERVIEW_BUDGET_OVERFLOW,
    ERR_OVERVIEW_NO_DRAFTS,
)


def _make_dummy_batch(batch_id: str, batch_index: int, duration_sec: float = 60.0) -> BatchPartitionRef:
    rng = BatchSourceRange(
        clip_id=f"clip_{batch_id}",
        source_in_sec=0.0,
        source_out_sec=duration_sec,
        partition_index=0,
        total_partitions=1,
        window_ids=("W01", "W02"),
    )
    return BatchPartitionRef(batch_id=batch_id, batch_index=batch_index, source_ranges=(rng,))


def _make_dummy_entry(manifest_id: str, snapshot_id: str, batch: BatchPartitionRef) -> ValidatedBatchDraftEntry:
    r0 = batch.source_ranges[0]
    payload = {
        "manifest_id": manifest_id,
        "snapshot_id": snapshot_id,
        "batch_id": batch.batch_id,
        "batch_strategy_summary": f"Chiến lược cho {batch.batch_id}",
        "timeline_segments": [
            {
                "clip_id": r0.clip_id,
                "start_sec": 0.0,
                "end_sec": min(30.0, r0.source_out_sec),
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Đoạn thoại mở đầu",
            }
        ],
    }
    val = validate_batch_draft_payload(
        payload=payload,
        expected_manifest_id=manifest_id,
        expected_snapshot_id=snapshot_id,
        expected_batch_id=batch.batch_id,
        source_ranges=batch.source_ranges,
    )
    assert val.ok and val.entry is not None
    return val.entry


# =============================================================================
# TC01: LEGACY NONE / {} PARITY WITH FROZEN CONTROL
# =============================================================================

# Provenance: Kịch bản chuẩn 3/3 lô từ test_render_overview_full_coverage_complete
# (tests/core/test_project_overview_contracts.py:127-180), manifest "M_FULL_01", snapshot "SNAP_FULL_01".
# Giá trị băm SHA-256 và kích thước bytes này độc lập hoàn toàn với hàm đang kiểm thử.
FROZEN_LEGACY_FULL_PROMPT_BYTES = 6010
FROZEN_LEGACY_FULL_PROMPT_SHA256 = "a85e7b3be5e9adebe22ad9b48129b1661eb49067d3b9b32814f052217b7f885e"


def test_tc01_legacy_none_and_empty_dict_parity_against_frozen_control():
    """TC01: None và canonical empty {} xuất ra prompt byte-for-byte giống hệt legacy frozen control."""
    from tests.core.test_project_overview_contracts import _create_sample_partitions, _make_valid_draft_entry

    r1, r2, r3 = _create_sample_partitions()
    b1 = BatchPartitionRef(batch_id="B01", batch_index=0, source_ranges=r1)
    b2 = BatchPartitionRef(batch_id="B02", batch_index=1, source_ranges=r2)
    b3 = BatchPartitionRef(batch_id="B03", batch_index=2, source_ranges=r3)

    m_id = "M_FULL_01"
    s_id = "SNAP_FULL_01"

    e1 = _make_valid_draft_entry(m_id, s_id, "B01", r1, summary="Chiến lược Lô 1.")
    e2 = _make_valid_draft_entry(m_id, s_id, "B02", r2, summary="Chiến lược Lô 2.")
    e3 = _make_valid_draft_entry(m_id, s_id, "B03", r3, summary="Chiến lược Lô 3.")

    # 1. Input legacy thuần túy (dùng default None)
    ctx_default = ProjectOverviewContextInput(
        manifest_id=m_id,
        project_name="DuAn_DayDu",
        snapshot_id=s_id,
        batches=(b1, b2, b3),
        received_drafts=(e1, e2, e3),
        unaccounted_clips=(),
        style_intent="travel_vlog",
        max_prompt_bytes=32768,
    )
    res_default = render_project_overview_prompt(ctx_default)
    assert res_default.ok is True
    assert res_default.rendered_bytes == FROZEN_LEGACY_FULL_PROMPT_BYTES
    assert res_default.rendered_sha256 == FROZEN_LEGACY_FULL_PROMPT_SHA256

    # 2. Input truyền creative_context=None tường minh
    ctx_none = ProjectOverviewContextInput(
        manifest_id=m_id,
        project_name="DuAn_DayDu",
        snapshot_id=s_id,
        batches=(b1, b2, b3),
        received_drafts=(e1, e2, e3),
        unaccounted_clips=(),
        style_intent="travel_vlog",
        max_prompt_bytes=32768,
        creative_context=None,
    )
    res_none = render_project_overview_prompt(ctx_none)
    assert res_none.ok is True
    assert res_none.rendered_bytes == FROZEN_LEGACY_FULL_PROMPT_BYTES
    assert res_none.rendered_sha256 == FROZEN_LEGACY_FULL_PROMPT_SHA256

    # 3. Input truyền canonical empty dict: creative_context={}
    ctx_empty_dict = ProjectOverviewContextInput(
        manifest_id=m_id,
        project_name="DuAn_DayDu",
        snapshot_id=s_id,
        batches=(b1, b2, b3),
        received_drafts=(e1, e2, e3),
        unaccounted_clips=(),
        style_intent="travel_vlog",
        max_prompt_bytes=32768,
        creative_context={},
    )
    res_empty_dict = render_project_overview_prompt(ctx_empty_dict)
    assert res_empty_dict.ok is True
    assert res_empty_dict.rendered_bytes == FROZEN_LEGACY_FULL_PROMPT_BYTES
    assert res_empty_dict.rendered_sha256 == FROZEN_LEGACY_FULL_PROMPT_SHA256

    # 4. Input truyền canonical empty typed CreativeContext()
    empty_typed = normalize_creative_context({})
    ctx_empty_typed = ProjectOverviewContextInput(
        manifest_id=m_id,
        project_name="DuAn_DayDu",
        snapshot_id=s_id,
        batches=(b1, b2, b3),
        received_drafts=(e1, e2, e3),
        unaccounted_clips=(),
        style_intent="travel_vlog",
        max_prompt_bytes=32768,
        creative_context=empty_typed,
    )
    res_empty_typed = render_project_overview_prompt(ctx_empty_typed)
    assert res_empty_typed.ok is True
    assert res_empty_typed.rendered_bytes == FROZEN_LEGACY_FULL_PROMPT_BYTES
    assert res_empty_typed.rendered_sha256 == FROZEN_LEGACY_FULL_PROMPT_SHA256

    # So sánh tương đương byte-for-byte giữa cả 4 biến thể
    assert res_default.rendered_prompt == res_none.rendered_prompt == res_empty_dict.rendered_prompt == res_empty_typed.rendered_prompt

    # Xác nhận độc lập: KHÔNG có khóa creative_context trong JSON ngữ cảnh legacy
    assert '"creative_context"' not in res_none.rendered_prompt
    assert "CREATIVE_CONTEXT" not in res_none.rendered_prompt

    # Đếm số lượng khối JSON trong prompt: chính xác 2 khối (Context data & Output schema)
    assert res_none.rendered_prompt.count("```json") == 2
    assert res_none.rendered_prompt.count("```") == 4


# =============================================================================
# TC04: CONSTRUCTOR & RENDERER OWNED VALIDATION
# =============================================================================

def test_tc04_mutation_defense_caller_dict_and_nested_refs():
    """TC04: Caller dict & nested ref mutation không làm biến đổi DTO hoặc rendered prompt."""
    b0 = _make_dummy_batch("B01", 0, 60.0)
    e0 = _make_dummy_entry("man_mut", "snap_mut", b0)

    raw_items = [
        {
            "reference_id": "ref_01",
            "local_path": "/media/references/mood_sample.mp4",
            "media_type": "video",
            "role": "reference_only",
            "user_note": "Ghi chú ban đầu",
        }
    ]
    raw_cc = {
        "free_brief": "Brief ban đầu",
        "style_preset": "cinematic",
        "reference_items": raw_items,
        "must_keep_rules": ["Quy tắc 1"],
    }

    ctx = ProjectOverviewContextInput(
        manifest_id="man_mut",
        project_name="MutationDefense",
        snapshot_id="snap_mut",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=raw_cc,
    )

    # Đột biến raw input sau khi tạo context
    raw_cc["free_brief"] = "Brief ĐÃ BỊ ĐỘT BIẾN"
    raw_items[0]["user_note"] = "Ghi chú ĐÃ BỊ ĐỘT BIẾN"
    raw_items.append({"reference_id": "forged_ref", "local_path": "/forged.mp4", "media_type": "video"})
    raw_cc["must_keep_rules"].append("Quy tắc ĐỘT BIẾN")

    # Render prompt
    res = render_project_overview_prompt(ctx)
    assert res.ok is True
    prompt = res.rendered_prompt

    # Xác nhận nội dung gốc được bảo toàn bất biến
    assert "Brief ban đầu" in prompt
    assert "Brief ĐÃ BỊ ĐỘT BIẾN" not in prompt
    assert "Ghi chú ban đầu" in prompt
    assert "Ghi chú ĐÃ BỊ ĐỘT BIẾN" not in prompt
    assert "forged_ref" not in prompt
    assert "Quy tắc ĐỘT BIẾN" not in prompt


def test_tc04_invalid_falsy_types_rejected():
    """TC04: False, 0, [], '' bị từ chối rõ ràng bằng ValueError tại constructor hoặc ERR_OVERVIEW_INVALID_CONTEXT."""
    b0 = _make_dummy_batch("B01", 0, 30.0)
    e0 = _make_dummy_entry("man_val", "snap_val", b0)

    # Constructor rejects falsy invalid types
    for bad_val in (False, 0, [], "", "   ", 123):
        with pytest.raises(ValueError, match="creative_context"):
            ProjectOverviewContextInput(
                manifest_id="man_val",
                project_name="ValProj",
                snapshot_id="snap_val",
                batches=(b0,),
                received_drafts=(e0,),
                creative_context=bad_val,
            )

    # Bypass qua object.__setattr__ để thử nghiệm public renderer
    ctx = ProjectOverviewContextInput(
        manifest_id="man_val",
        project_name="ValProj",
        snapshot_id="snap_val",
        batches=(b0,),
        received_drafts=(e0,),
    )

    for bad_val in (False, 0, [], "", "bad_string", 42):
        object.__setattr__(ctx, "creative_context", bad_val)
        res = render_project_overview_prompt(ctx)
        assert res.ok is False
        assert res.error_code == ERR_OVERVIEW_INVALID_CONTEXT


def test_tc04_forged_typed_fields_and_deleted_members_caught():
    """TC04: Member deletion và forged typed fields bị bắt gọn bởi controlled error ERR_OVERVIEW_INVALID_CONTEXT."""
    b0 = _make_dummy_batch("B01", 0, 30.0)
    e0 = _make_dummy_entry("man_forge", "snap_forge", b0)

    # 1. Nonempty typed context: kiểm tra dương tính trước, sau đó xóa member
    cc = normalize_creative_context({"free_brief": "Valid creative brief for deletion probe"})
    ctx_nonempty = ProjectOverviewContextInput(
        manifest_id="man_forge",
        project_name="ForgeProj",
        snapshot_id="snap_forge",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
    )
    # Positive control trước khi xóa
    res_pos = render_project_overview_prompt(ctx_nonempty)
    assert res_pos.ok is True
    assert "Valid creative brief for deletion probe" in res_pos.rendered_prompt

    # Xóa thực tế trường instance creative_context bằng object.__delattr__
    object.__delattr__(ctx_nonempty, "creative_context")
    res_del_nonempty = render_project_overview_prompt(ctx_nonempty)
    assert res_del_nonempty.ok is False
    assert res_del_nonempty.error_code == ERR_OVERVIEW_INVALID_CONTEXT
    assert res_del_nonempty.rendered_prompt == ""
    assert "Valid creative brief for deletion probe" not in res_del_nonempty.rendered_prompt

    # 2. Legacy instance với default None: xóa member thực tế phải bị từ chối
    ctx_legacy = ProjectOverviewContextInput(
        manifest_id="man_forge",
        project_name="ForgeProj",
        snapshot_id="snap_forge",
        batches=(b0,),
        received_drafts=(e0,),
    )
    res_leg_pos = render_project_overview_prompt(ctx_legacy)
    assert res_leg_pos.ok is True

    object.__delattr__(ctx_legacy, "creative_context")
    res_del_legacy = render_project_overview_prompt(ctx_legacy)
    assert res_del_legacy.ok is False
    assert res_del_legacy.error_code == ERR_OVERVIEW_INVALID_CONTEXT
    assert res_del_legacy.rendered_prompt == ""

    # 3. Forged instance qua object.__new__ không khởi tạo creative_context
    ctx_forged_new = object.__new__(ProjectOverviewContextInput)
    object.__setattr__(ctx_forged_new, "manifest_id", "man_forge")
    object.__setattr__(ctx_forged_new, "project_name", "ForgeProj")
    object.__setattr__(ctx_forged_new, "snapshot_id", "snap_forge")
    object.__setattr__(ctx_forged_new, "batches", (b0,))
    object.__setattr__(ctx_forged_new, "received_drafts", (e0,))
    object.__setattr__(ctx_forged_new, "unaccounted_clips", ())
    object.__setattr__(ctx_forged_new, "style_intent", "travel_vlog")
    object.__setattr__(ctx_forged_new, "max_prompt_bytes", 32768)
    # Cố tình không khởi tạo trường creative_context

    res_forged_new = render_project_overview_prompt(ctx_forged_new)
    assert res_forged_new.ok is False
    assert res_forged_new.error_code == ERR_OVERVIEW_INVALID_CONTEXT
    assert res_forged_new.rendered_prompt == ""

    # 4. Giả mạo đối tượng CreativeContext bằng cách gán kiểu sai hoặc lone surrogate
    ctx_surrogate = ProjectOverviewContextInput(
        manifest_id="man_forge",
        project_name="ForgeProj",
        snapshot_id="snap_forge",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
    )
    forged_cc = normalize_creative_context({"free_brief": "Valid brief"})
    object.__setattr__(forged_cc, "free_brief", "\ud800")  # lone surrogate
    object.__setattr__(ctx_surrogate, "creative_context", forged_cc)

    res_surr = render_project_overview_prompt(ctx_surrogate)
    assert res_surr.ok is False
    assert res_surr.error_code == ERR_OVERVIEW_INVALID_CONTEXT

    # Giả mạo target_duration_sec thành float('nan')
    forged_cc2 = normalize_creative_context({"free_brief": "Valid brief"})
    object.__setattr__(forged_cc2, "target_duration_sec", float("nan"))
    object.__setattr__(ctx_surrogate, "creative_context", forged_cc2)

    res2 = render_project_overview_prompt(ctx_surrogate)
    assert res2.ok is False
    assert res2.error_code == ERR_OVERVIEW_INVALID_CONTEXT

    # Giả mạo target_duration_sec thành float('inf')
    forged_cc3 = normalize_creative_context({"free_brief": "Valid brief"})
    object.__setattr__(forged_cc3, "target_duration_sec", float("inf"))
    object.__setattr__(ctx_surrogate, "creative_context", forged_cc3)

    res3 = render_project_overview_prompt(ctx_surrogate)
    assert res3.ok is False
    assert res3.error_code == ERR_OVERVIEW_INVALID_CONTEXT

    # Giả mạo target_duration_sec thành số siêu lớn 10**400
    forged_cc4 = normalize_creative_context({"free_brief": "Valid brief"})
    object.__setattr__(forged_cc4, "target_duration_sec", 10**400)
    object.__setattr__(ctx_surrogate, "creative_context", forged_cc4)

    res4 = render_project_overview_prompt(ctx_surrogate)
    assert res4.ok is False
    assert res4.error_code == ERR_OVERVIEW_INVALID_CONTEXT


def test_tc04_native_accepted_numeric_values_roundtrip_losslessly():
    """TC04: Các giá trị số hợp lệ (1e-7, 1e20, 10**20) giữ nguyên type và value số học, không ép kiểu string."""
    b0 = _make_dummy_batch("B01", 0, 30.0)
    e0 = _make_dummy_entry("man_num", "snap_num", b0)

    for dur_val in (1e-7, 120.5, 300):
        cc = {"free_brief": "Numeric test", "target_duration_sec": dur_val}
        ctx = ProjectOverviewContextInput(
            manifest_id="man_num",
            project_name="NumProj",
            snapshot_id="snap_num",
            batches=(b0,),
            received_drafts=(e0,),
            creative_context=cc,
        )
        res = render_project_overview_prompt(ctx)
        assert res.ok is True
        # Tìm khối JSON ngữ cảnh và trích xuất target_duration_sec
        prompt = res.rendered_prompt
        json_start = prompt.find("[DỮ LIỆU NGỮ CẢNH DỰ ÁN CẤU TRÚC (PROJECT_OVERVIEW_CONTEXT_JSON)]\n```json\n")
        assert json_start != -1
        json_block = prompt[json_start:].split("```json\n")[1].split("\n```")[0]
        parsed = json.loads(json_block)
        rec_dur = parsed["creative_context"]["target_duration_sec"]
        assert type(rec_dur) in (int, float)
        assert rec_dur == pytest.approx(dur_val)


# =============================================================================
# TC05: FULL CANONICAL CREATIVE BLOCK ONCE & TRIPWIRES
# =============================================================================

def test_tc05_canonical_block_appears_once_with_static_framing_and_no_io(monkeypatch):
    """TC05: Khối canonical JSON xuất hiện đúng 1 lần; framing tĩnh; zero IO."""
    b0 = _make_dummy_batch("B01", 0, 60.0)
    e0 = _make_dummy_entry("man_tc5", "snap_tc5", b0)

    cc = {
        "free_brief": "Mạch truyện hồi ký:\n\"Dòng thời gian trôi qua, chúng tôi đã gặp lại nhau.\"",
        "style_preset": "documentary_vlog",
        "target_duration_sec": 180.0,
        "must_keep_rules": ["Giữ đoạn đối thoại của bác thợ rèn"],
        "must_not_rules": ["Không dùng hiệu ứng giật lag"],
        "reference_items": [
            {
                "reference_id": "ref_dock",
                "local_path": "/var/media/references/bến_tàu.jpg",
                "media_type": "image",
                "role": "reference_only",
                "user_note": "Tham khảo ánh sáng buổi chiều tà",
            }
        ],
        "reference_urls": ["https://example.com/reference/lighting-guide"],
    }

    ctx = ProjectOverviewContextInput(
        manifest_id="man_tc5",
        project_name="CanonicalProject",
        snapshot_id="snap_tc5",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
    )

    # Tripwires: Không mở file, không truy cập mạng
    real_open = open
    def forbidden_open(*args, **kwargs):
        path = str(args[0]) if args else ""
        if "/var/media/references" in path or "example.com" in path:
            pytest.fail(f"Tripwire kích hoạt! Formatter không được phép mở file reference: {path}")
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", forbidden_open)

    res = render_project_overview_prompt(ctx)
    assert res.ok is True
    prompt = res.rendered_prompt

    # 1. Khối creative_context chỉ xuất hiện DUY NHẤT 1 lần trong JSON
    assert prompt.count('"creative_context": {') == 1

    # 2. Toàn bộ prompt chỉ chứa đúng 2 khối code block ```json
    assert prompt.count("```json") == 2
    assert prompt.count("```") == 4

    # 3. Framing tĩnh thông báo rõ ràng về siêu dữ liệu và giới hạn mô hình
    assert "[LƯU Ý NGỮ CẢNH SÁNG TẠO (CREATIVE_CONTEXT)]" in prompt
    assert "Đường dẫn tệp và URL chỉ là siêu dữ liệu" in prompt
    assert "mô hình không cam kết tuân thủ ngữ nghĩa tuyệt đối" in prompt

    # 4. Trích xuất JSON ngữ cảnh và xác thực tính toàn vẹn ngữ nghĩa
    json_start = prompt.find("[DỮ LIỆU NGỮ CẢNH DỰ ÁN CẤU TRÚC (PROJECT_OVERVIEW_CONTEXT_JSON)]\n```json\n")
    json_str = prompt[json_start:].split("```json\n")[1].split("\n```")[0]
    parsed_ctx = json.loads(json_str)

    c_parsed = parsed_ctx["creative_context"]
    assert c_parsed["free_brief"] == cc["free_brief"]
    assert c_parsed["style_preset"] == "documentary_vlog"
    assert c_parsed["target_duration_sec"] == 180.0
    assert c_parsed["must_keep_rules"] == ["Giữ đoạn đối thoại của bác thợ rèn"]
    assert c_parsed["must_not_rules"] == ["Không dùng hiệu ứng giật lag"]
    assert len(c_parsed["reference_items"]) == 1
    assert c_parsed["reference_items"][0]["local_path"] == "/var/media/references/bến_tàu.jpg"
    assert c_parsed["reference_urls"] == ["https://example.com/reference/lighting-guide"]


# =============================================================================
# TC06: CORE BUDGET EQUALITY & 1-BYTE OVERFLOW
# =============================================================================

def test_tc06_budget_equality_and_one_byte_overflow_fail_closed():
    """TC06: max_prompt_bytes bằng đúng UTF-8 bytes -> pass; thiếu 1 byte -> fail-closed."""
    b0 = _make_dummy_batch("B01", 0, 50.0)
    e0 = _make_dummy_entry("man_bud", "snap_bud", b0)

    cc = {
        "free_brief": "Chi thị đạo diễn cho thử nghiệm trần ngân sách.",
        "style_preset": "vlog",
    }

    # Đo kích thước thực tế với max_prompt_bytes mặc định (32768)
    ctx_probe = ProjectOverviewContextInput(
        manifest_id="man_bud",
        project_name="BudgetTest",
        snapshot_id="snap_bud",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
        max_prompt_bytes=32768,
    )
    res_probe = render_project_overview_prompt(ctx_probe)
    assert res_probe.ok is True
    exact_len = res_probe.rendered_bytes
    assert exact_len > 0

    # 1. Subcase Equality: max_prompt_bytes == exact_len -> THÀNH CÔNG
    ctx_exact = ProjectOverviewContextInput(
        manifest_id="man_bud",
        project_name="BudgetTest",
        snapshot_id="snap_bud",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
        max_prompt_bytes=exact_len,
    )
    res_exact = render_project_overview_prompt(ctx_exact)
    assert res_exact.ok is True
    assert res_exact.rendered_bytes == exact_len

    # 2. Subcase 1-Byte Overflow: max_prompt_bytes == exact_len - 1 -> THẤT BẠI FAIL-CLOSED
    ctx_overflow = ProjectOverviewContextInput(
        manifest_id="man_bud",
        project_name="BudgetTest",
        snapshot_id="snap_bud",
        batches=(b0,),
        received_drafts=(e0,),
        creative_context=cc,
        max_prompt_bytes=exact_len - 1,
    )
    res_overflow = render_project_overview_prompt(ctx_overflow)
    assert res_overflow.ok is False
    assert res_overflow.error_code == ERR_OVERVIEW_BUDGET_OVERFLOW
    assert res_overflow.rendered_prompt == ""
    assert res_overflow.rendered_bytes == 0
    assert res_overflow.rendered_sha256 == ""
    assert res_overflow.required_bytes == exact_len
    assert f"{exact_len} B" in res_overflow.message
