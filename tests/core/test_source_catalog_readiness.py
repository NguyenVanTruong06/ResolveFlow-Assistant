"""tests/core/test_source_catalog_readiness.py
Kiểm thử bộ thẩm định siêu dữ liệu nguồn và thư viện tài nguyên (G07 Source & Catalog Readiness).

Bao phủ các ca nghiệm thu:
- T01: Nguồn hợp lệ thuần túy (source-only) -> completed, checks_passed=True, ready_to_execute=False, catalog not_required, 0 storage calls.
- T02: Kịch bản có tài nguyên managed/linked trực tuyến đúng provenance -> guard resolved master, không dùng preview, READ_ONLY/FULL đọc được. Thiếu file/empty/directory/access error fail truthful.
- T03: Storage offline/error fail-closed dù assets() trả snapshot. Lỗi provider/registry không thành empty success. Kịch bản không có assignment không bị chặn.
- T04: Trùng asset ID, thiếu asset ID, sai lệch provenance, thiếu master file đều fail. Không tự ý tạo omission (D02).
- T05: Thay đổi tệp nguồn hoặc hoán đổi cấu hình kho trong quá trình quét -> final recheck phát hiện và fail-closed.
- T06: Thẩm định ReadinessRequest chặt chẽ (int không bool, revision khớp, UTF-8 hợp lệ, immutable).
- T10: Giới hạn catalog trần (100k), đối phó ngoại lệ bất thường, 0 Resolve/model/network calls.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    create_app_owned_approval_record,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    edit_plan_proposal_to_dict,
    normalize_edit_plan_proposal,
)
from src.core.source_catalog_readiness import (
    MAX_CATALOG_RECORDS,
    AssetAssignmentObservation,
    ReadinessRequest,
    SourceCatalogReadinessResult,
    SourceClipObservation,
    inspect_source_and_catalog_readiness,
)
from src.core.storage_manager import StorageManager, StorageState, atomic_json
from tests.core.contracts.test_edit_plan_proposal_contracts import _make_valid_proposal_dict


# ---------------------------------------------------------------------------
# Helpers tạo dữ liệu kiểm thử
# ---------------------------------------------------------------------------

def _create_real_source_files(tmp_path: Path, count: int = 2) -> List[str]:
    """Tạo tệp nguồn thực trên đĩa tạm với kích thước > 0 byte."""
    paths = []
    for i in range(count):
        p = tmp_path / f"source_clip_{i:02d}.mp4"
        p.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"X" * 100)
        paths.append(str(p))
    return paths


def _build_test_proposal(
    source_paths: List[str],
    assignments: Optional[List[Dict[str, Any]]] = None,
    manifest_id: str = "m_test01",
) -> EditPlanProposal:
    """Tạo proposal hợp lệ với các đường dẫn clip nguồn thực tế."""
    raw = _make_valid_proposal_dict(manifest_id)

    # Cập nhật clips trong source_binding
    clips = []
    for idx, p in enumerate(source_paths):
        cid = f"C{idx + 1:02d}"
        clips.append({
            "clip_id": cid,
            "path": p,
            "current_path": p,
            "duration": 10.0,
            "duration_sec": 10.0,
        })
    raw["source_binding"]["clips"] = clips

    # Cập nhật timeline_segments để khớp với các clip_ids
    raw["video_plan"]["timeline_segments"] = [
        {
            "segment_index": 0,
            "clip_id": "C01",
            "start_sec": 0.0,
            "end_sec": 5.0,
            "action": "keep",
            "speed": 1.0,
            "role": "hook",
        }
    ]
    raw["video_plan"]["global_hook"]["clip_id"] = "C01"

    if assignments is not None:
        raw["proposed_asset_assignments"] = assignments
    else:
        raw["proposed_asset_assignments"] = []

    return normalize_edit_plan_proposal(raw)


def _build_test_request(
    proposal: EditPlanProposal,
    request_id: str = "req_test_001",
    generation: int = 1,
) -> ReadinessRequest:
    """Tạo ReadinessRequest với approval được tạo chuẩn xác từ proposal."""
    rev = compute_proposal_revision(proposal)
    approval = create_app_owned_approval_record(
        proposal=proposal,
        approval_id="appr_test_001",
        approved_at_iso="2026-11-02T02:00:00Z",
    )
    return ReadinessRequest(
        request_id=request_id,
        generation=generation,
        proposal=proposal,
        approval=approval,
    )


class MockStorageManager:
    """Mock StorageManager an toàn cho kiểm thử với cấu hình đĩa tạm đầy đủ."""

    def __init__(
        self,
        root_dir: Path,
        state: StorageState = StorageState.ONLINE,
        assets_list: Optional[List[Dict[str, Any]]] = None,
        raise_on_assets: Optional[Exception] = None,
    ):
        self.root = str(root_dir)
        self.state = state
        self._assets_list = assets_list or []
        self._raise_on_assets = raise_on_assets
        self.library_id = "lib_test_01"

        # Thiết lập thư mục cấu hình và marker trên đĩa tạm
        meta_dir = root_dir / ".chundvc_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        self.marker_file = meta_dir / "library.json"
        self.marker_file.write_text(json.dumps({"library_id": "lib_test_01", "created": "2026-11-02"}), encoding="utf-8")

        self.registry_file = meta_dir / "registry.json"
        self.registry_file.write_text(json.dumps({"version": 1, "items": len(self._assets_list)}), encoding="utf-8")

        cfg_dir = root_dir / ".chundvc_cfg"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = str(cfg_dir / "storage_config.json")
        self._config = {"root": str(root_dir), "library_id": "lib_test_01"}
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._config, f)

    @property
    def online(self) -> bool:
        return self.state in (StorageState.ONLINE, StorageState.READ_ONLY, StorageState.FULL)

    def get_state(self) -> StorageState:
        return self.state

    def assets(self, *, raise_on_error: bool = False) -> List[Dict[str, Any]]:
        if self._raise_on_assets:
            raise self._raise_on_assets
        return self._assets_list


# ---------------------------------------------------------------------------
# T01: Valid source-only
# ---------------------------------------------------------------------------

def test_t01_valid_source_only(tmp_path: Path):
    """T01: Clip nguồn hợp lệ, không có asset assignment -> checks_passed=True, ready=False, 0 storage calls."""
    source_files = _create_real_source_files(tmp_path, 2)
    proposal = _build_test_proposal(source_files, assignments=[])
    request = _build_test_request(proposal)

    factory_called = 0

    def fail_storage_factory():
        nonlocal factory_called
        factory_called += 1
        raise AssertionError("Storage factory tuyệt đối không được gọi khi assignments rỗng!")

    progress_events = []

    def on_progress(pct: int, msg: str):
        progress_events.append((pct, msg))

    result = inspect_source_and_catalog_readiness(
        request,
        storage_provider=fail_storage_factory,
        progress_callback=on_progress,
    )

    assert result.status == "completed"
    assert result.checks_passed is True
    assert result.ready_to_execute is False  # BẮT BUỘC False
    assert len(result.source_observations) == 2
    for obs in result.source_observations:
        assert obs.exists is True
        assert obs.is_regular_file is True
        assert obs.is_non_empty is True
        assert obs.is_readable is True
        assert obs.recheck_passed is True
        assert obs.error_code == ""

    assert len(result.catalog_observations) == 0
    assert factory_called == 0
    assert any("bỏ qua kiểm tra catalog" in d for d in result.diagnostics)
    assert len(progress_events) > 0


# ---------------------------------------------------------------------------
# T02: Live managed/linked assignment với provenance chuẩn
# ---------------------------------------------------------------------------

def test_t02_live_assignment_with_correct_provenance(tmp_path: Path):
    """T02: Có assignment hợp lệ, kho online/read_only/full -> ActionGuard master resolved, không dùng preview."""
    source_files = _create_real_source_files(tmp_path, 1)

    # Tạo tệp master thực
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)
    master_file = lib_dir / "sfx_pop.wav"
    master_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 50)

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_01",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    catalog_item = {
        "id": "sfx_01",
        "asset_id": "sfx_01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "origin_root": str(lib_dir),
        "library_path": "sfx_pop.wav",
        "type": "audio",
        "file_path": str(master_file),
        "path": str(master_file),
    }

    # Thử nghiệm với StorageState.ONLINE
    storage = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=[catalog_item])
    result = inspect_source_and_catalog_readiness(
        request,
        storage_provider=lambda: storage,
    )
    assert result.status == "completed"
    assert result.checks_passed is True
    assert result.ready_to_execute is False
    assert len(result.catalog_observations) == 1
    cat_obs = result.catalog_observations[0]
    assert cat_obs.found_in_catalog is True
    assert cat_obs.provenance_matched is True
    assert cat_obs.guard_allowed is True
    assert cat_obs.guard_status == "online"
    assert cat_obs.resolved_master_path == str(master_file)
    assert cat_obs.recheck_passed is True

    # Thử nghiệm với StorageState.READ_ONLY và FULL
    storage.state = StorageState.READ_ONLY
    res_ro = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage)
    assert res_ro.checks_passed is True

    storage.state = StorageState.FULL
    res_full = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage)
    assert res_full.checks_passed is True


def test_t02_negative_missing_or_corrupt_source(tmp_path: Path):
    """T02 Negative: Clip nguồn thiếu, rỗng 0-byte, hoặc là thư mục -> fail truthful."""
    # Clip 1: tồn tại; Clip 2: không tồn tại
    real_clip = tmp_path / "real.mp4"
    real_clip.write_bytes(b"content")
    missing_clip = tmp_path / "ghost.mp4"

    proposal = _build_test_proposal([str(real_clip), str(missing_clip)], assignments=[])
    request = _build_test_request(proposal)

    result = inspect_source_and_catalog_readiness(request)
    assert result.status == "completed"
    assert result.checks_passed is False
    assert result.source_observations[0].exists is True
    assert result.source_observations[1].exists is False
    assert result.source_observations[1].error_code == "ERR_SOURCE_NOT_FOUND"

    # Nguồn là thư mục
    dir_clip = tmp_path / "folder_as_clip.mp4"
    dir_clip.mkdir()
    proposal_dir = _build_test_proposal([str(dir_clip)], assignments=[])
    req_dir = _build_test_request(proposal_dir)
    res_dir = inspect_source_and_catalog_readiness(req_dir)
    assert res_dir.checks_passed is False
    assert res_dir.source_observations[0].error_code == "ERR_SOURCE_NOT_REGULAR_FILE"

    # Nguồn rỗng 0 byte
    empty_clip = tmp_path / "empty.mp4"
    empty_clip.touch()
    proposal_empty = _build_test_proposal([str(empty_clip)], assignments=[])
    req_empty = _build_test_request(proposal_empty)
    res_empty = inspect_source_and_catalog_readiness(req_empty)
    assert res_empty.checks_passed is False
    assert res_empty.source_observations[0].error_code == "ERR_SOURCE_EMPTY"


# ---------------------------------------------------------------------------
# T03: Offline snapshot và lỗi provider fail-closed
# ---------------------------------------------------------------------------

def test_t03_offline_snapshot_denied(tmp_path: Path):
    """T03: Storage OFFLINE hoặc ERROR dù assets() trả danh sách snapshot -> fail-closed."""
    source_files = _create_real_source_files(tmp_path, 1)
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)
    master_file = lib_dir / "sfx_pop.wav"
    master_file.write_bytes(b"WAVE")

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_01",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    catalog_item = {
        "id": "sfx_01",
        "asset_id": "sfx_01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "path": str(master_file),
    }

    # Storage OFFLINE
    storage_offline = MockStorageManager(lib_dir, state=StorageState.OFFLINE, assets_list=[catalog_item])
    res_offline = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage_offline)
    assert res_offline.checks_passed is False
    assert res_offline.catalog_observations[0].guard_status == "offline"
    assert res_offline.catalog_observations[0].error_code == "ERR_STORAGE_OFFLINE"

    # Storage ERROR
    storage_err = MockStorageManager(lib_dir, state=StorageState.ERROR, assets_list=[catalog_item])
    res_err = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage_err)
    assert res_err.checks_passed is False

    # assets(raise_on_error=True) ném ngoại lệ
    storage_exc = MockStorageManager(lib_dir, state=StorageState.ONLINE, raise_on_assets=RuntimeError("Corrupt DB"))
    res_exc = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage_exc)
    assert res_exc.checks_passed is False
    assert any("ERR_STORAGE_ASSETS_FAILED" in d for d in res_exc.diagnostics)


def test_t03_no_assignments_not_blocked_by_offline_storage(tmp_path: Path):
    """T03: Kịch bản không có assignment không bị chặn bởi storage offline."""
    source_files = _create_real_source_files(tmp_path, 1)
    proposal = _build_test_proposal(source_files, assignments=[])
    request = _build_test_request(proposal)

    # Storage OFFLINE được cung cấp nhưng không được dùng
    lib_dir = tmp_path / "storage_root"
    storage_offline = MockStorageManager(lib_dir, state=StorageState.OFFLINE)
    res = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage_offline)
    assert res.checks_passed is True
    assert res.ready_to_execute is False


# ---------------------------------------------------------------------------
# T04: Trùng asset ID, thiếu asset ID, provenance mismatch, D02 nguyên vẹn
# ---------------------------------------------------------------------------

def test_t04_duplicate_asset_id_denial(tmp_path: Path):
    """T04: Catalog chứa trùng asset_id -> từ chối toàn bộ, không lấy item đầu."""
    source_files = _create_real_source_files(tmp_path, 1)
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)
    master_file = lib_dir / "sfx_pop.wav"
    master_file.write_bytes(b"WAVE")

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_dup",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    # 2 item cùng ID sfx_dup
    catalog_items = [
        {"id": "sfx_dup", "asset_id": "sfx_dup", "source_kind": "managed", "origin_library_id": "lib_test_01", "path": str(master_file)},
        {"id": "sfx_dup", "asset_id": "sfx_dup", "source_kind": "managed", "origin_library_id": "lib_test_01", "path": str(master_file)},
    ]
    storage = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=catalog_items)
    result = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage)

    assert result.checks_passed is False
    assert result.catalog_observations[0].duplicate_in_catalog is True
    assert result.catalog_observations[0].error_code == "ERR_DUPLICATE_ASSET_ID"


def test_t04_missing_asset_and_provenance_mismatch_preserve_d02(tmp_path: Path):
    """T04: Asset thiếu hoặc sai provenance -> fail, proposal/approval/omissions nguyên vẹn 0 auto-omit."""
    source_files = _create_real_source_files(tmp_path, 1)
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)
    master_file = lib_dir / "sfx_pop.wav"
    master_file.write_bytes(b"WAVE")

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_missing",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    storage = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=[])
    result = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage)

    assert result.checks_passed is False
    assert result.catalog_observations[0].found_in_catalog is False
    assert result.catalog_observations[0].error_code == "ERR_ASSET_NOT_FOUND_IN_CATALOG"

    # Ràng buộc D02: Đề xuất và danh sách omission gốc tuyệt đối không đổi
    assert [o["demand_id"] for o in request.proposal.proposed_omissions] == ["dem_broll_01"]
    assert request.approval.accepted_omission_ids == ("dem_broll_01",)

    # Sai provenance (source_kind khác)
    item_wrong_kind = {
        "id": "sfx_missing",
        "asset_id": "sfx_missing",
        "source_kind": "linked",  # Proposal yêu cầu 'managed'
        "origin_library_id": "lib_test_01",
        "path": str(master_file),
    }
    storage_prov = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=[item_wrong_kind])
    res_prov = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage_prov)
    assert res_prov.checks_passed is False
    assert res_prov.catalog_observations[0].provenance_matched is False
    assert res_prov.catalog_observations[0].error_code == "ERR_PROVENANCE_MISMATCH"


# ---------------------------------------------------------------------------
# T05: Mutation trong lúc quét (Final Recheck Phase)
# ---------------------------------------------------------------------------

def test_t05_source_stat_mutation_detected(tmp_path: Path):
    """T05: Clip nguồn bị sửa đổi kích thước/mtime trong quá trình quét -> final recheck bắt và fail."""
    source_files = _create_real_source_files(tmp_path, 1)
    proposal = _build_test_proposal(source_files, assignments=[])
    request = _build_test_request(proposal)

    # Giả lập thay đổi mtime bằng stat_fn tùy biến
    call_count = 0
    real_stat = os.stat

    def mutating_stat(path_str: str):
        nonlocal call_count
        st = real_stat(path_str)
        call_count += 1
        # Ở lần stat thứ hai (recheck), trả về mtime khác
        if call_count > 1:
            mock_st = MagicMock(wraps=st)
            mock_st.st_mtime_ns = getattr(st, "st_mtime_ns", 0) + 1_000_000_000
            return mock_st
        return st

    result = inspect_source_and_catalog_readiness(request, stat_fn=mutating_stat)
    assert result.checks_passed is False
    assert result.source_observations[0].recheck_passed is False
    assert result.source_observations[0].error_code == "ERR_SOURCE_MUTATED_DURING_SCAN"


def test_t05_storage_config_swap_detected(tmp_path: Path):
    """T05: Cấu hình kho trên đĩa bị thay đổi trong lúc quét -> final recheck bắt và fail."""
    source_files = _create_real_source_files(tmp_path, 1)
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)
    master_file = lib_dir / "sfx_pop.wav"
    master_file.write_bytes(b"WAVE")

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_01",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    catalog_item = {
        "id": "sfx_01",
        "asset_id": "sfx_01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "origin_root": str(lib_dir),
        "library_path": "sfx_pop.wav",
        "type": "audio",
        "path": str(master_file),
    }
    storage = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=[catalog_item])

    def storage_context_reader_with_swap(stg):
        # Trả về ngữ cảnh khác nhau ở lần 1 và lần 2
        storage_context_reader_with_swap.count = getattr(storage_context_reader_with_swap, "count", 0) + 1
        return f"context_version_{storage_context_reader_with_swap.count}"

    result = inspect_source_and_catalog_readiness(
        request,
        storage_provider=lambda: storage,
        storage_context_reader=storage_context_reader_with_swap,
    )
    assert result.checks_passed is False
    assert any("ERR_STORAGE_CONFIG_SWAPPED" in d for d in result.diagnostics)


# ---------------------------------------------------------------------------
# T06: Thẩm định ReadinessRequest chặt chẽ
# ---------------------------------------------------------------------------

def test_t06_request_validation_and_immutability(tmp_path: Path):
    """T06: Thẩm định request_id, generation, revision mismatch, input tampering."""
    source_files = _create_real_source_files(tmp_path, 1)
    proposal = _build_test_proposal(source_files, assignments=[])
    rev = compute_proposal_revision(proposal)
    approval = create_app_owned_approval_record(proposal, approval_id="appr_01", approved_at_iso="2026-11-02T02:00:00Z")

    # 1. request_id rỗng hoặc không phải str
    with pytest.raises(ValueError, match="ERR_INVALID_REQUEST_ID"):
        ReadinessRequest("", 0, proposal, approval)
    with pytest.raises(ValueError, match="ERR_INVALID_REQUEST_ID"):
        ReadinessRequest(None, 0, proposal, approval)  # type: ignore

    # 2. generation là bool hoặc số âm
    with pytest.raises(ValueError, match="ERR_INVALID_GENERATION"):
        ReadinessRequest("req_01", True, proposal, approval)  # bool bị từ chối
    with pytest.raises(ValueError, match="ERR_INVALID_GENERATION"):
        ReadinessRequest("req_01", -1, proposal, approval)

    # 3. Revision mismatch giữa proposal và approval
    tampered_approval = AppOwnedApprovalRecord(
        approval_id="appr_01",
        proposal_id=proposal.proposal_id,
        proposal_revision="a" * 64,  # Khác revision thực
        approved_at_iso="2026-11-02T02:00:00Z",
        accepted_omission_ids=(),
        approval_scope="ENTIRE_PROPOSAL",
    )
    with pytest.raises(ValueError, match="ERR_REVISION_MISMATCH"):
        ReadinessRequest("req_01", 0, proposal, tampered_approval)

    # 4. Scope không phải ENTIRE_PROPOSAL
    dict_approval = {
        "approval_id": "appr_01",
        "proposal_id": proposal.proposal_id,
        "proposal_revision": rev,
        "approved_at_iso": "2026-11-02T02:00:00Z",
        "accepted_omission_ids": (),
        "approval_scope": "VIDEO_ONLY",
    }
    with pytest.raises(ValueError, match="ERR_INVALID_SCOPE"):
        ReadinessRequest("req_01", 0, proposal, dict_approval)

    # 5. Caller mutation sau construction không ảnh hưởng request
    req = ReadinessRequest("req_01", 0, proposal, approval)
    # req.generation = 5  # Frozen dataclass cấm gán
    with pytest.raises(Exception):
        req.generation = 5  # type: ignore


# ---------------------------------------------------------------------------
# T10: Catalog overflow (>100k) và bảo toàn ràng buộc bảo mật
# ---------------------------------------------------------------------------

def test_t10_catalog_overflow_denied(tmp_path: Path):
    """T10: Danh mục vượt trần MAX_CATALOG_RECORDS (100k) -> fail-closed."""
    source_files = _create_real_source_files(tmp_path, 1)
    lib_dir = tmp_path / "storage_root"
    lib_dir.mkdir(parents=True, exist_ok=True)

    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_sfx_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_01",
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        }
    ]
    proposal = _build_test_proposal(source_files, assignments=assignments)
    request = _build_test_request(proposal)

    # Danh mục tràn 100,001 items
    huge_catalog = [{"id": f"sfx_{i}", "source_kind": "managed", "origin_library_id": "lib_test_01"} for i in range(MAX_CATALOG_RECORDS + 1)]
    storage = MockStorageManager(lib_dir, state=StorageState.ONLINE, assets_list=huge_catalog)

    result = inspect_source_and_catalog_readiness(request, storage_provider=lambda: storage)
    assert result.checks_passed is False
    assert any("ERR_CATALOG_OVERFLOW" in d for d in result.diagnostics)


def test_t10_cancellation_checkpoints(tmp_path: Path):
    """T10: Hủy giữa chừng trả về status='cancelled', checks_passed=False, ready=False."""
    source_files = _create_real_source_files(tmp_path, 2)
    proposal = _build_test_proposal(source_files, assignments=[])
    request = _build_test_request(proposal)

    # Hủy ngay lập tức
    res_cancel_early = inspect_source_and_catalog_readiness(request, is_cancelled=lambda: True)
    assert res_cancel_early.status == "cancelled"
    assert res_cancel_early.checks_passed is False
    assert res_cancel_early.ready_to_execute is False


# ---------------------------------------------------------------------------
# G07-07A2a-R1: Regression tests từ các kết quả Codex Review Probe
# ---------------------------------------------------------------------------

def test_r1_real_storage_manager_positive_and_disk_mismatch(tmp_path: Path):
    """R1: Fixture StorageManager thật trên đĩa tmp_path: positive pass và disk mismatch fail-closed."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    real_sm = StorageManager(config_dir=config_dir, reserve_bytes=1024)
    real_sm.configure(storage_root)

    meta = storage_root / ".chundvc_meta"
    reg_file = meta / "registry.json"
    reg = json.loads(reg_file.read_text(encoding="utf-8"))

    master_file = storage_root / "01_core" / "sfx_real.wav"
    master_file.write_bytes(b"RIFF" + b"X" * 60)

    lib_id = real_sm._config["library_id"]
    reg["assets"].append({
        "id": "sfx_real_01",
        "asset_id": "sfx_real_01",
        "type": "audio",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "library_path": "01_core/sfx_real.wav",
        "path": str(master_file),
        "file_path": str(master_file),
    })
    atomic_json(reg_file, reg)
    real_sm.snapshot()

    src_files = _create_real_source_files(tmp_path, 1)
    assignments = [
        {
            "slot": "sfx",
            "demand_id": "dem_real_01",
            "timeline_sec": 0.5,
            "duration_sec": 1.0,
            "asset_id": "sfx_real_01",
            "source_kind": "managed",
            "origin_library_id": lib_id,
            "status": "PROPOSED",
        }
    ]
    prop = _build_test_proposal(src_files, assignments=assignments)
    req = _build_test_request(prop)

    # 1. Positive inspection với StorageManager thật
    pos_res = inspect_source_and_catalog_readiness(req, storage_provider=lambda: real_sm)
    assert pos_res.status == "completed"
    assert pos_res.checks_passed is True
    assert pos_res.ready_to_execute is False
    assert len(pos_res.catalog_observations) == 1
    assert pos_res.catalog_observations[0].guard_status == "online"

    # 2. Disk config mismatch: thay đổi library_id trên disk storage.json
    disk_cfg_path = Path(real_sm.config_file)
    disk_cfg = json.loads(disk_cfg_path.read_text(encoding="utf-8"))
    disk_cfg["library_id"] = "DIFFERENT_LIBRARY_ID"
    disk_cfg_path.write_text(json.dumps(disk_cfg), encoding="utf-8")

    neg_res = inspect_source_and_catalog_readiness(req, storage_provider=lambda: real_sm)
    assert neg_res.status == "completed"
    assert neg_res.checks_passed is False
    assert neg_res.ready_to_execute is False
    assert any("ERR_STORAGE_CONFIG_MISMATCH" in d for d in neg_res.diagnostics)


def test_r1_whitespace_request_id_denied(tmp_path: Path):
    """R1: Request ID chỉ chứa khoảng trắng phải bị từ chối với ValueError."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)
    with pytest.raises(ValueError, match="ERR_INVALID_REQUEST_ID"):
        ReadinessRequest("   ", 1, prop, req.approval)


def test_r1_deleted_approval_member_raises_value_error(tmp_path: Path):
    """R1: Xóa thuộc tính trên approval phải raise ValueError có kiểm soát, không leak AttributeError."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    bad_req = _build_test_request(prop)
    object.__delattr__(bad_req.approval, "approval_id")
    with pytest.raises(ValueError, match="ERR_APPROVAL_VALIDATION_FAILED"):
        ReadinessRequest("req_r1_del", 1, prop, bad_req.approval)


def test_r1_source_same_size_ctime_rewrite_detected(tmp_path: Path):
    """R1: Ghi đè tệp nguồn cùng kích thước và khôi phục mtime vẫn bị phát hiện qua ctime_ns."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)

    old = os.stat(src[0])
    calls = 0

    def changing_stat(path):
        nonlocal calls
        if str(path) == src[0]:
            calls += 1
            if calls == 2:
                Path(path).write_bytes(b"Z" * old.st_size)
                os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        return os.stat(path)

    r = inspect_source_and_catalog_readiness(req, stat_fn=changing_stat)
    assert r.status == "completed"
    assert r.checks_passed is False
    assert r.ready_to_execute is False
    assert r.source_observations[0].recheck_passed is False
    assert r.source_observations[0].error_code == "ERR_SOURCE_MUTATED_DURING_SCAN"


def test_r1_storage_provider_none_fails_closed_with_observations(tmp_path: Path):
    """R1: storage_provider trả None phải fail-closed và sinh đủ bản ghi quan sát offline."""
    src = _create_real_source_files(tmp_path, 1)
    assignment = {
        "slot": "sfx",
        "demand_id": "d1",
        "timeline_sec": 0.5,
        "duration_sec": 1.0,
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "status": "PROPOSED",
    }
    prop = _build_test_proposal(src, assignments=[assignment])
    req = _build_test_request(prop)

    r = inspect_source_and_catalog_readiness(req, storage_provider=lambda: None)
    assert r.status == "completed"
    assert r.checks_passed is False
    assert r.ready_to_execute is False
    assert any("ERR_STORAGE_UNAVAILABLE" in d for d in r.diagnostics)
    assert len(r.catalog_observations) == 1
    assert r.catalog_observations[0].guard_status == "offline"


def test_r1_initial_marker_stat_error_fails_closed(tmp_path: Path):
    """R1: Lỗi đọc marker ban đầu (PermissionError) phải fail-closed."""
    src = _create_real_source_files(tmp_path, 1)
    lib = tmp_path / "lib_marker_err"
    lib.mkdir(parents=True, exist_ok=True)
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF" + b"X" * 60)

    assignment = {
        "slot": "sfx",
        "demand_id": "d1",
        "timeline_sec": 0.5,
        "duration_sec": 1.0,
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "status": "PROPOSED",
    }
    prop = _build_test_proposal(src, assignments=[assignment])
    req = _build_test_request(prop)

    cat = {
        "id": "sfx01",
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "origin_root": str(lib),
        "library_path": "sfx.wav",
        "type": "audio",
        "file_path": str(master),
        "path": str(master),
    }
    sm = MockStorageManager(lib, assets_list=[cat])

    initial_failures = 0

    def stat_initial_fault(path):
        nonlocal initial_failures
        if str(path) == str(sm.marker_file) and initial_failures == 0:
            initial_failures += 1
            raise PermissionError("probe marker read denied")
        return os.stat(path)

    r = inspect_source_and_catalog_readiness(req, storage_provider=lambda: sm, stat_fn=stat_initial_fault)
    assert r.checks_passed is False
    assert any("ERR_STORAGE_MARKER_STAT_FAILED" in d for d in r.diagnostics)


def test_r1_missing_catalog_source_kind_fails_closed(tmp_path: Path):
    """R1: Mục trong catalog thiếu source_kind phải fail-closed."""
    src = _create_real_source_files(tmp_path, 1)
    lib = tmp_path / "lib_skind"
    lib.mkdir(parents=True, exist_ok=True)
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF" + b"X" * 60)

    assignment = {
        "slot": "sfx",
        "demand_id": "d1",
        "timeline_sec": 0.5,
        "duration_sec": 1.0,
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "status": "PROPOSED",
    }
    prop = _build_test_proposal(src, assignments=[assignment])
    req = _build_test_request(prop)

    cat = {
        "id": "sfx01",
        "asset_id": "sfx01",
        # Thiếu "source_kind"
        "origin_library_id": "lib_test_01",
        "origin_root": str(lib),
        "library_path": "sfx.wav",
        "type": "audio",
        "file_path": str(master),
        "path": str(master),
    }
    sm = MockStorageManager(lib, assets_list=[cat])

    r = inspect_source_and_catalog_readiness(req, storage_provider=lambda: sm)
    assert r.checks_passed is False
    assert r.catalog_observations[0].provenance_matched is False


def test_r1_cancel_in_final_progress_callback(tmp_path: Path):
    """R1: Gọi cancel ngay trong callback tiến độ 100% phải trả về status='cancelled'."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)

    cancelled = False

    def final_progress(pct, msg):
        nonlocal cancelled
        if pct == 100:
            cancelled = True

    r = inspect_source_and_catalog_readiness(
        req,
        is_cancelled=lambda: cancelled,
        progress_callback=final_progress,
    )
    assert r.status == "cancelled"
    assert r.checks_passed is False


def test_r1_result_immutability_defensive_copies(tmp_path: Path):
    """R1: SourceCatalogReadinessResult sở hữu sequences của nó, không cho phép caller biến đổi qua list alias."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)

    mutable = []
    result = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=prop.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=mutable,
        catalog_observations=[],
        diagnostics=[],
    )
    mutable.append("caller mutated")
    assert result.source_observations == ()
    assert isinstance(result.source_observations, tuple)


def test_r1_public_entry_rejects_stale_approval_revision_before_stat_io(tmp_path: Path):
    """R1: Ngõ vào public inspector từ chối request bị stale approval revision TRƯỚC mọi stat I/O."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)
    object.__setattr__(req.approval, "proposal_revision", "f" * 64)

    stat_calls = []

    def stat_spy(path):
        stat_calls.append(str(path))
        return os.stat(path)

    with pytest.raises(ValueError, match="ERR_REVISION_MISMATCH"):
        inspect_source_and_catalog_readiness(req, stat_fn=stat_spy)

    # Khẳng định bị chặn trước khi bất kỳ lệnh stat nào được gọi
    assert len(stat_calls) == 0


def test_r1_cancel_during_catalog_enumeration_stops_early(tmp_path: Path):
    """R1: Yêu cầu hủy trong khi lập chỉ mục catalog dừng ngay lập tức, không duyệt hết toàn bộ kho."""
    src = _create_real_source_files(tmp_path, 1)
    lib = tmp_path / "lib_cancel_enum"
    lib.mkdir(parents=True, exist_ok=True)
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF" + b"X" * 60)

    assignment = {
        "slot": "sfx",
        "demand_id": "d1",
        "timeline_sec": 0.5,
        "duration_sec": 1.0,
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "status": "PROPOSED",
    }
    prop = _build_test_proposal(src, assignments=[assignment])
    req = _build_test_request(prop)

    cancelled = False

    class CountedList(list):
        yielded = 0

        def __iter__(self):
            nonlocal cancelled
            for item in super().__iter__():
                self.yielded += 1
                cancelled = True
                yield item

    entries = CountedList([{"id": f"a{i}"} for i in range(100)])
    sm = MockStorageManager(lib, assets_list=entries)

    r = inspect_source_and_catalog_readiness(
        req,
        storage_provider=lambda: sm,
        is_cancelled=lambda: cancelled,
    )
    assert r.status == "cancelled"
    assert entries.yielded == 1


def test_r1_result_denies_mutable_and_invalid_observation_types(tmp_path: Path):
    """R1: SourceCatalogReadinessResult từ chối phần tử quan sát không đúng kiểu SourceClipObservation / AssetAssignmentObservation."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)

    with pytest.raises(ValueError, match="ERR_INVALID_OBSERVATION_TYPE"):
        SourceCatalogReadinessResult(
            request_id=req.request_id,
            generation=req.generation,
            proposal_id=prop.proposal_id,
            proposal_revision=req.approval.proposal_revision,
            approval_id=req.approval.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=({"unexpected": "runtime object"},),  # type: ignore
            catalog_observations=(),
            diagnostics=(),
        )


def test_r3_final_source_recheck_cancel_stops_immediately(tmp_path: Path):
    """F11: Cancel tại final source row đầu với 5 nguồn -> 0 stat sau cancel cho các row còn lại."""
    many_dir = tmp_path / "many"
    many_dir.mkdir()
    many = _create_real_source_files(many_dir, 5)
    prop = _build_test_proposal(many, assignments=[])
    req = _build_test_request(prop)

    seen = {}
    cancelled = False
    after = []

    def statfn_src(path):
        nonlocal cancelled
        key = str(path)
        seen[key] = seen.get(key, 0) + 1
        if cancelled:
            after.append(key)
        # Hủy tại nhịp recheck thứ 2 của file đầu tiên
        if key == many[0] and seen[key] == 2:
            cancelled = True
        return os.stat(path)

    mr = inspect_source_and_catalog_readiness(req, stat_fn=statfn_src, is_cancelled=lambda: cancelled)
    assert mr.status == "cancelled"
    assert mr.checks_passed is False
    assert len(after) == 0


def test_r3_final_master_recheck_cancel_stops_immediately(tmp_path: Path):
    """F12: Cancel tại final master row đầu với nhiều assignments -> 0 stat các master còn lại."""
    src = _create_real_source_files(tmp_path, 1)
    lib = tmp_path / "lib"
    lib.mkdir()

    masters = []
    cat_items = []
    assign_items = []
    for i in range(4):
        mf = lib / f"sound_{i}.wav"
        mf.write_bytes(b"RIFF" + b"M" * 64)
        masters.append(str(mf))
        aid = f"sfx_{i}"
        did = f"d_{i}"
        cat_items.append({
            "id": aid,
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "origin_root": str(lib),
            "library_path": f"sound_{i}.wav",
            "type": "audio",
            "file_path": str(mf),
        })
        assign_items.append({
            "slot": "sfx",
            "demand_id": did,
            "timeline_sec": float(i),
            "duration_sec": 1.0,
            "asset_id": aid,
            "source_kind": "managed",
            "origin_library_id": "lib_test_01",
            "status": "PROPOSED",
        })

    sm = MockStorageManager(lib, assets_list=cat_items)
    prop = _build_test_proposal(src, assignments=assign_items)
    req = _build_test_request(prop)

    seen = {}
    cancelled = False
    after = []

    def statfn_master(path):
        nonlocal cancelled
        key = str(path)
        seen[key] = seen.get(key, 0) + 1
        if cancelled:
            after.append(key)
        # Hủy tại nhịp recheck của master đầu tiên
        if key == masters[0] and seen[key] == 2:
            cancelled = True
        return os.stat(path)

    mr = inspect_source_and_catalog_readiness(
        req,
        storage_provider=lambda: sm,
        stat_fn=statfn_master,
        is_cancelled=lambda: cancelled,
    )
    assert mr.status == "cancelled"
    assert mr.checks_passed is False
    assert len(after) == 0


def test_r3_result_and_normalizer_reject_raw_types_before_coercion(tmp_path: Path):
    """F10: SourceCatalogReadinessResult và normalize_readiness_result từ chối raw types invalid trước coercion."""
    src = _create_real_source_files(tmp_path, 1)
    prop = _build_test_proposal(src, assignments=[])
    req = _build_test_request(prop)

    from src.core.source_catalog_readiness import normalize_readiness_result

    # 1. source_observations là False hoặc None
    for bad_seq in (False, None, {}):
        with pytest.raises(ValueError):
            SourceCatalogReadinessResult(
                request_id=req.request_id,
                generation=req.generation,
                proposal_id=prop.proposal_id,
                proposal_revision=req.approval.proposal_revision,
                approval_id=req.approval.approval_id,
                status="completed",
                checks_passed=True,
                ready_to_execute=False,
                source_observations=bad_seq,  # type: ignore
                catalog_observations=(),
                diagnostics=(),
            )

    # 2. diagnostics là dict hoặc None
    for bad_diag in (None, {}, False):
        with pytest.raises(ValueError):
            SourceCatalogReadinessResult(
                request_id=req.request_id,
                generation=req.generation,
                proposal_id=prop.proposal_id,
                proposal_revision=req.approval.proposal_revision,
                approval_id=req.approval.approval_id,
                status="completed",
                checks_passed=True,
                ready_to_execute=False,
                source_observations=(),
                catalog_observations=(),
                diagnostics=bad_diag,  # type: ignore
            )

    # 3. checks_passed là non-bool
    with pytest.raises(ValueError, match="ERR_INVALID_CHECKS_PASSED"):
        SourceCatalogReadinessResult(
            request_id=req.request_id,
            generation=req.generation,
            proposal_id=prop.proposal_id,
            proposal_revision=req.approval.proposal_revision,
            approval_id=req.approval.approval_id,
            status="completed",
            checks_passed="yes",  # type: ignore
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )


def test_t06_expected_sha_unbound_mismatch_and_pass(tmp_path: Path):
    """T06: Expected SHA absent -> UNBOUND/checks False; wrong SHA -> mismatch; matched request -> pass."""
    from src.core.contracts.time_contracts import RationalFPS
    from tests.core.geometry.test_source_evidence_service import _create_audio_first_bframe_video
    import hashlib

    v_path = tmp_path / "t06_video.mp4"
    _create_audio_first_bframe_video(v_path, video_dur_sec=0.5, audio_dur_sec=1.0, fps=30)
    with open(v_path, "rb") as f:
        real_sha = hashlib.sha256(f.read()).hexdigest()

    tl_fps = RationalFPS(24, 1)

    # Nhánh 1: Clip KHÔNG có expected_full_sha256 trong proposal
    prop_unbound = _build_test_proposal([str(v_path)], assignments=[])
    req_unbound = ReadinessRequest(
        request_id="req_unbound",
        generation=1,
        proposal=prop_unbound,
        approval=create_app_owned_approval_record(prop_unbound, approval_id="appr_unbound", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )
    res_unbound = inspect_source_and_catalog_readiness(req_unbound)
    assert res_unbound.status == "completed"
    assert res_unbound.checks_passed is False
    assert any("ERR_UNBOUND_CONTENT_PROVENANCE" in d for d in res_unbound.diagnostics)
    assert res_unbound.ready_to_execute is False

    # Nhánh 2: Clip có expected_full_sha256 SAI
    prop_dict = _make_valid_proposal_dict()
    prop_dict["proposed_asset_assignments"] = []
    prop_dict["source_binding"]["clips"] = [{
        "clip_id": "C01",
        "path": str(v_path),
        "current_path": str(v_path),
        "duration": 10.0,
        "duration_sec": 10.0,
        "expected_full_sha256": "0" * 64,
    }]
    prop_dict["video_plan"]["timeline_segments"] = [{
        "segment_index": 0,
        "clip_id": "C01",
        "start_sec": 0.0,
        "end_sec": 0.4,
        "action": "keep",
        "speed": 1.0,
        "role": "body",
    }]
    prop_dict["video_plan"]["global_hook"]["clip_id"] = "C01"
    prop_dict["video_plan"]["global_hook"]["start_sec"] = 0.0
    prop_dict["video_plan"]["global_hook"]["end_sec"] = 0.2
    prop_dict["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop_wrong = normalize_edit_plan_proposal(prop_dict)
    req_wrong = ReadinessRequest(
        request_id="req_wrong",
        generation=1,
        proposal=prop_wrong,
        approval=create_app_owned_approval_record(prop_wrong, approval_id="appr_wrong", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )
    res_wrong = inspect_source_and_catalog_readiness(req_wrong)
    assert res_wrong.status == "completed"
    assert res_wrong.checks_passed is False
    assert any("ERR_SOURCE_DIGEST_MISMATCH" in d for d in res_wrong.diagnostics)

    # Nhánh 3: Clip có expected_full_sha256 ĐÚNG
    prop_dict["source_binding"]["clips"][0]["expected_full_sha256"] = real_sha
    prop_right = normalize_edit_plan_proposal(prop_dict)
    req_right = ReadinessRequest(
        request_id="req_right",
        generation=1,
        proposal=prop_right,
        approval=create_app_owned_approval_record(prop_right, approval_id="appr_right", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )
    res_right = inspect_source_and_catalog_readiness(req_right)
    assert res_right.status == "completed"
    assert res_right.checks_passed is True
    assert res_right.ready_to_execute is False
    assert res_right.source_execution_evidence is not None
    assert res_right.source_geometry is not None
    assert res_right.source_geometry.status == "GEOMETRY_VERIFIED"


def test_t10_formula_b_via_consumer_table(tmp_path: Path):
    """T10: Real Formula B via consumer table: sourcefps != tlfps, speed, bounds -> geometry revision matches."""
    from src.core.contracts.time_contracts import RationalFPS
    from tests.core.geometry.test_source_evidence_service import _create_audio_first_bframe_video
    import hashlib

    # Video 30fps duration 0.5s (15 frames)
    v_path = tmp_path / "t10_video.mp4"
    _create_audio_first_bframe_video(v_path, video_dur_sec=0.5, audio_dur_sec=1.0, fps=30)
    with open(v_path, "rb") as f:
        real_sha = hashlib.sha256(f.read()).hexdigest()

    # Timeline 24fps
    tl_fps = RationalFPS(24, 1)

    prop_dict = _make_valid_proposal_dict()
    prop_dict["proposed_asset_assignments"] = []
    prop_dict["source_binding"]["clips"] = [{
        "clip_id": "C01",
        "path": str(v_path),
        "current_path": str(v_path),
        "duration": 0.5,
        "duration_sec": 0.5,
        "expected_full_sha256": real_sha,
    }]
    prop_dict["video_plan"]["timeline_segments"] = [{
        "segment_index": 0,
        "clip_id": "C01",
        "start_sec": 0.0,
        "end_sec": 0.3,
        "action": "speedup",
        "speed": 1.5,
        "role": "body",
    }]
    prop_dict["video_plan"]["global_hook"]["clip_id"] = "C01"
    prop_dict["video_plan"]["global_hook"]["start_sec"] = 0.0
    prop_dict["video_plan"]["global_hook"]["end_sec"] = 0.1
    prop_dict["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    proposal = normalize_edit_plan_proposal(prop_dict)

    req = ReadinessRequest(
        request_id="req_t10",
        generation=1,
        proposal=proposal,
        approval=create_app_owned_approval_record(proposal, approval_id="appr_t10", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )
    res = inspect_source_and_catalog_readiness(req)
    assert res.status == "completed"
    assert res.checks_passed is True
    assert res.source_geometry is not None
    assert res.source_geometry.status == "GEOMETRY_VERIFIED"
    assert res.source_geometry.timeline_fps == tl_fps
    assert res.source_geometry.proposal_id == proposal.proposal_id
    assert res.source_geometry.proposal_revision == compute_proposal_revision(proposal)


def test_r1_codex_review_probes_p13_to_p22(tmp_path: Path):
    """Kiểm chứng hồi quy các probes P13–P22 từ Codex review cho consumer deep mode."""
    import dataclasses
    import hashlib
    from fractions import Fraction
    from types import SimpleNamespace
    import src.core.geometry.source_evidence_service as s
    from src.core.contracts.time_contracts import RationalFPS
    from src.core.contracts.edit_plan_proposal_contracts import edit_plan_proposal_to_dict

    source = tmp_path / "source_p13.mp4"
    source.write_bytes(b"owned_source_fixture_p13")
    fresh = s._capture_source_freshness(str(source))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()

    prop_raw = edit_plan_proposal_to_dict(_build_test_proposal([str(source)], assignments=[]))
    prop_raw['source_binding']['clips'][0]['expected_full_sha256'] = sha
    prop_raw['source_binding']['clips'][0]['stream_index'] = 0
    prop_raw['video_plan']['global_hook']['placement_mode'] = 'intro_cut'
    prop = normalize_edit_plan_proposal(prop_raw)

    request = ReadinessRequest(
        'review_probe',
        1,
        prop,
        create_app_owned_approval_record(prop, approval_id='approval', approved_at_iso='2026-11-02T00:00:00Z'),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )

    frame = s.SourceFrameEvidence(prop.manifest_id, 'C01', RationalFPS(30, 1), 300, 'VERIFIED_CFR')
    env = s.SourceExecutionEvidenceEnvelope('C01', prop.manifest_id, str(source), 0, sha, sha, fresh, frame, True)

    def consume(envelope=env, status='completed'):
        fake_service = SimpleNamespace(acquire_evidence_for_batch=lambda *a, **kw: s.SourceEvidenceBatchResult(status, {'C01': envelope}))
        return inspect_source_and_catalog_readiness(request, evidence_service=fake_service)

    # P13: consumer_positive
    res_pos = consume()
    assert res_pos.checks_passed is True

    # P14–P18: negative mutations trên envelope
    for name, changes in [
        ('incomplete_proof', dict(complete_scan=False)),
        ('wrong_path', dict(file_path=str(tmp_path / 'other.mp4'))),
        ('wrong_stream', dict(stream_index=9)),
        ('missing_freshness', dict(freshness=None)),
        ('wrong_policy', dict(profile_policy='untrusted_profile')),
    ]:
        res_neg = consume(dataclasses.replace(env, **changes))
        assert res_neg.checks_passed is False, f"Failed for {name}"

    # P19: consumer_failed_batch
    assert consume(status='failed').checks_passed is False

    # P20: consumer_final_freshness_after_deep_scan
    original = source.read_bytes()
    def stale_service(*a, **kw):
        source.write_bytes(original + b'CHANGED_AFTER_METADATA')
        return s.SourceEvidenceBatchResult('completed', {'C01': env})

    stale = inspect_source_and_catalog_readiness(request, evidence_service=SimpleNamespace(acquire_evidence_for_batch=stale_service))
    assert stale.checks_passed is False
    source.write_bytes(original)

    # P21: malformed_deep_stream_preio_stat_calls
    bad_raw = edit_plan_proposal_to_dict(prop)
    bad_raw['source_binding']['clips'][0]['stream_index'] = True
    bad_prop = normalize_edit_plan_proposal(bad_raw)
    bad_approval = create_app_owned_approval_record(bad_prop, approval_id='bad_approval', approved_at_iso='2026-11-02T00:00:00Z')
    stat_calls = []
    def stat_hook(path):
        stat_calls.append(path)
        return source.stat()

    with pytest.raises(ValueError):
        malformed = ReadinessRequest('bad_deep', 1, bad_prop, bad_approval, verify_source_execution=True, timeline_fps=RationalFPS(24, 1))
        inspect_source_and_catalog_readiness(malformed, stat_fn=stat_hook, evidence_service=SimpleNamespace(acquire_evidence_for_batch=lambda *a, **kw: None))
    assert len(stat_calls) == 0  # 0 stat calls before validation failure

    # P22: geometry_issue_diagnostic_uses_real_field
    blocked_raw = edit_plan_proposal_to_dict(prop)
    blocked_raw['video_plan']['global_hook'].pop('placement_mode')
    blocked_prop = normalize_edit_plan_proposal(blocked_raw)
    blocked_req = ReadinessRequest(
        'blocked_geometry',
        1,
        blocked_prop,
        create_app_owned_approval_record(blocked_prop, approval_id='blocked', approved_at_iso='2026-11-02T00:00:00Z'),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )
    blocked_res = inspect_source_and_catalog_readiness(
        blocked_req,
        evidence_service=SimpleNamespace(acquire_evidence_for_batch=lambda *a, **kw: s.SourceEvidenceBatchResult('completed', {'C01': env})),
    )
    assert not any('has no attribute' in d for d in blocked_res.diagnostics)
    assert any('ERR_GEOMETRY_' in d for d in blocked_res.diagnostics)


def test_r2_residual_consumer_binding_probes(tmp_path: Path):
    """Tái hiện residual probes cho Consumer & Catalog Authority theo G07-07A3-R2 Section D."""
    import dataclasses
    import hashlib
    from types import SimpleNamespace
    from src.core.contracts.time_contracts import RationalFPS
    import src.core.geometry.source_evidence_service as s

    source = tmp_path / "source_r2.mp4"
    source.write_bytes(b"controlled source for r2 consumer test")
    fresh = s._capture_source_freshness(str(source))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()

    prop_raw = edit_plan_proposal_to_dict(_build_test_proposal([str(source)], assignments=[]))
    prop_raw["source_binding"]["clips"][0].update(expected_full_sha256=sha, stream_index=0)
    prop_raw["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop = normalize_edit_plan_proposal(prop_raw)
    req = ReadinessRequest(
        "req_r2",
        1,
        prop,
        create_app_owned_approval_record(prop, approval_id="appr_r2", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )
    frame = s.SourceFrameEvidence(req.proposal.manifest_id, "C01", RationalFPS(30, 1), 300, "VERIFIED_CFR")
    env = s.SourceExecutionEvidenceEnvelope("C01", req.proposal.manifest_id, str(source), 0, sha, sha, fresh, frame, True)

    def consume(envelope=env, request=req, **kw):
        service = SimpleNamespace(acquire_evidence_for_batch=lambda *a, **k: s.SourceEvidenceBatchResult("completed", {"C01": envelope}))
        return inspect_source_and_catalog_readiness(request, evidence_service=service, **kw)

    # 1. positive control
    assert consume().checks_passed is True

    # 2. consumer_wrong_five_stat
    wrongfresh = dataclasses.replace(fresh, ctime_ns=fresh.ctime_ns + 1)
    assert consume(dataclasses.replace(env, freshness=wrongfresh)).checks_passed is False

    # 3. consumer_foreign_frame_manifest
    wrongframe = dataclasses.replace(frame, manifest_id="foreign_manifest")
    assert consume(dataclasses.replace(env, manifest_id="foreign_manifest", frame_evidence=wrongframe)).checks_passed is False

    # 4. deep_final_master & deep_final_registry
    lib = tmp_path / "lib_r2"
    lib.mkdir()
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF WAVE master")
    catalog = [dict(id="sfx_01", asset_id="sfx_01", source_kind="managed", origin_library_id="lib_test_01", origin_root=str(lib), library_path="sfx.wav", type="audio", file_path=str(master), path=str(master))]
    storage = MockStorageManager(lib, assets_list=catalog)
    assigned = json.loads(json.dumps(prop_raw))
    assigned["proposed_asset_assignments"] = [dict(slot="sfx", demand_id="d1", timeline_sec=0.5, duration_sec=1, asset_id="sfx_01", source_kind="managed", origin_library_id="lib_test_01", status="PROPOSED")]
    assigned_prop = normalize_edit_plan_proposal(assigned)
    assigned_req = ReadinessRequest(
        "req_r2_assigned",
        1,
        assigned_prop,
        create_app_owned_approval_record(assigned_prop, approval_id="appr_r2_a", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )
    assert consume(request=assigned_req, storage_provider=lambda: storage).checks_passed is True

    for target in (master, storage.registry_file):
        prior = target.read_bytes()
        def changed_service(*a, **kw):
            target.write_bytes(prior + b"CHANGED_DURING_DEEP")
            return s.SourceEvidenceBatchResult("completed", {"C01": env})
        changed = inspect_source_and_catalog_readiness(assigned_req, storage_provider=lambda: storage, evidence_service=SimpleNamespace(acquire_evidence_for_batch=changed_service))
        assert changed.checks_passed is False
        target.write_bytes(prior)


def test_r3_final_checklist_catalog_context_probes(tmp_path: Path):
    """Kiểm tra Point 3: config_swapped_during_deep bị từ chối chính xác theo G07-07A3-R3."""
    import hashlib
    from types import SimpleNamespace
    from src.core.contracts.time_contracts import RationalFPS
    import src.core.geometry.source_evidence_service as s

    source = tmp_path / "source_r3.mp4"
    source.write_bytes(b"controlled source r3")
    fresh = s._capture_source_freshness(str(source))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()

    lib = tmp_path / "lib_r3"
    lib.mkdir()
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF WAVE")
    catalog = [dict(id="sfx_01", asset_id="sfx_01", source_kind="managed", origin_library_id="lib_test_01", origin_root=str(lib), library_path="sfx.wav", type="audio", file_path=str(master), path=str(master))]
    storage = MockStorageManager(lib, assets_list=catalog)

    raw = edit_plan_proposal_to_dict(_build_test_proposal([str(source)], assignments=[]))
    raw["source_binding"]["clips"][0].update(expected_full_sha256=sha, stream_index=0)
    raw["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    raw["proposed_asset_assignments"] = [dict(slot="sfx", demand_id="d1", timeline_sec=0.5, duration_sec=1, asset_id="sfx_01", source_kind="managed", origin_library_id="lib_test_01", status="PROPOSED")]
    prop = normalize_edit_plan_proposal(raw)
    deep = ReadinessRequest(
        "r3_req",
        1,
        prop,
        create_app_owned_approval_record(prop, approval_id="a", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )
    frame = s.SourceFrameEvidence(prop.manifest_id, "C01", RationalFPS(30, 1), 300, "VERIFIED_CFR")
    env = s.SourceExecutionEvidenceEnvelope("C01", prop.manifest_id, str(source), 0, sha, sha, fresh, frame, True)

    def service(*a, **kw):
        return s.SourceEvidenceBatchResult("completed", {"C01": env})

    before = inspect_source_and_catalog_readiness(deep, storage_provider=lambda: storage, evidence_service=SimpleNamespace(acquire_evidence_for_batch=service))
    assert before.checks_passed is True

    def changed(*a, **kw):
        Path(storage.config_file).write_text(json.dumps(dict(root=str(tmp_path / "new_selected_root"), library_id="other_library")))
        return service()

    result = inspect_source_and_catalog_readiness(deep, storage_provider=lambda: storage, evidence_service=SimpleNamespace(acquire_evidence_for_batch=changed))
    assert result.checks_passed is False
    assert any("ERR_STORAGE_CONFIG_SWAPPED" in d for d in result.diagnostics)
