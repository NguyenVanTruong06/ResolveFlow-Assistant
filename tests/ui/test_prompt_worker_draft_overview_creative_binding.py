"""
tests/ui/test_prompt_worker_draft_overview_creative_binding.py
=============================================================================
Kiểm thử tích hợp Worker Prompt Preparation cho nhánh Thẩm định Bản thảo (validate_batch_draft)
và Tổng quan Dự án (copy_project_overview) với Ngữ cảnh Sáng tạo (Creative Context).
Tuân thủ đầy đủ các nhóm nghiệm thu G06-06C3-S2c:
- TC01: Legacy None/{} direct/batch/draft/overview unchanged keys/IDs/policy.
- TC02: Positive nonempty draft validation & real overview rendering với matching proof.
- TC03: Tripwires chặn đứng trước IO cho mọi trường hợp mismatch/conflict/malformed signature.
- TC06: Worker trần ngân sách UTF-8 byte cap: equality pass, 1-byte overflow fail-closed.
- TC07: Constructor capture & mutation isolation qua deterministic held barrier.
- TC08: Post-render source deletion/mutation & cooperative cancel yields 0 success/artifact.
- TC09: Artifact transport helper (validate_project_overview_result_against_artifact).
- TC10: Real QThread physical lifecycle, cooperative cancel & thread cleanup.
=============================================================================
"""

import copy
import dataclasses
import hashlib
import json
import os
import stat
import time
import pytest
from typing import Dict, Any, Tuple, Optional
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

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
    ERR_OVERVIEW_BUDGET_OVERFLOW,
)
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationRequest,
    PromptPreparationWorker,
    PreparedBatchSnapshot,
    PreparedBatchItem,
    BaselineSourceEntry,
    UnaccountedClipEntry,
    AdapterIssueEntry,
    compute_snapshot_id,
    validate_and_normalize_snapshot,
    compute_project_overview_ledger_signature,
    PreparedProjectOverviewArtifact,
    validate_project_overview_result_against_artifact,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_worker_environment(tmp_path, monkeypatch):
    """Cô lập toàn diện môi trường kiểm thử: HOME, XDG, visual cache."""
    fake_home = str(tmp_path / "fake_home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "fake_xdg_config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fake_xdg_cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "fake_xdg_data"))
    monkeypatch.setenv("RESOLVEFLOW_VISUAL_CACHE_DIR", str(tmp_path / "fake_visual_cache"))


_RETAINED_RUNNING_WORKERS: list[PromptPreparationWorker] = []


def _create_dummy_video(path: str, size: int = 1024, fill_byte: bytes = b"A") -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(fill_byte * size)
    return os.path.normpath(os.path.abspath(path))


def _make_dummy_baseline(path: str) -> BaselineSourceEntry:
    norm_p = os.path.normpath(os.path.abspath(path))
    st = os.stat(norm_p)
    return BaselineSourceEntry(
        path=norm_p,
        st_size=st.st_size,
        st_mtime_ns=st.st_mtime_ns,
        st_ctime_ns=getattr(st, "st_ctime_ns", 0),
        st_ino=st.st_ino,
        st_dev=st.st_dev,
    )


def _make_dummy_draft_entry(
    manifest_id: str,
    snapshot_id: str,
    batch_id: str,
    source_ranges: Tuple[BatchSourceRange, ...] = None,
    clip_id: str = "C01",
    received_at: str = "2026-10-30T10:00:00",
    summary: str = "Chiến lược phân bổ cảnh quay",
    duration_sec: float = 30.0,
) -> ValidatedBatchDraftEntry:
    if source_ranges is None:
        source_ranges = (
            BatchSourceRange(
                clip_id=clip_id,
                source_in_sec=0.0,
                source_out_sec=duration_sec,
                partition_index=0,
                total_partitions=1,
                window_ids=("W01", "W02"),
            ),
        )
    r0 = source_ranges[0]
    payload = {
        "manifest_id": manifest_id,
        "snapshot_id": snapshot_id,
        "batch_id": batch_id,
        "batch_strategy_summary": summary,
        "timeline_segments": [
            {
                "clip_id": r0.clip_id,
                "start_sec": r0.source_in_sec,
                "end_sec": min(r0.source_in_sec + duration_sec, r0.source_out_sec),
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Đoạn thoại mở đầu",
            }
        ],
    }
    res = validate_batch_draft_payload(
        payload,
        expected_manifest_id=manifest_id,
        expected_snapshot_id=snapshot_id,
        expected_batch_id=batch_id,
        source_ranges=source_ranges,
    )
    assert res.ok and res.entry is not None
    if received_at != res.entry.received_at:
        return dataclasses.replace(res.entry, received_at=received_at)
    return res.entry


def _build_snapshot_fixture(
    tmp_path,
    creative_context_signature: Optional[str] = None,
) -> Tuple[PreparedBatchSnapshot, str, str, str]:
    """Tạo PreparedBatchSnapshot bất biến và 2 video clip thật trên đĩa."""
    v1 = _create_dummy_video(str(tmp_path / "clip_a.mp4"), size=2048)
    v2 = _create_dummy_video(str(tmp_path / "clip_b.mp4"), size=4096)

    m_id = "man_s2c_01"
    p_name = "ChunDVC_Project"
    policy = "source_only_v1"

    b_entry1 = _make_dummy_baseline(v1)
    b_entry2 = _make_dummy_baseline(v2)

    r1 = BatchSourceRange(
        clip_id="C01",
        source_in_sec=0.0,
        source_out_sec=60.0,
        partition_index=0,
        total_partitions=1,
        window_ids=("W01", "W02"),
    )
    r2 = BatchSourceRange(
        clip_id="C02",
        source_in_sec=0.0,
        source_out_sec=90.0,
        partition_index=0,
        total_partitions=1,
        window_ids=("W03",),
    )

    p1 = "PROMPT B01 DUMMY CONTENT"
    p2 = "PROMPT B02 DUMMY CONTENT"
    bp1 = PreparedBatchItem(
        "B01",
        0,
        1,
        60.0,
        p1,
        len(p1.encode("utf-8")),
        hashlib.sha256(p1.encode("utf-8")).hexdigest(),
        "sig1",
        ("C01",),
        1,
        source_ranges=(r1,),
    )
    bp2 = PreparedBatchItem(
        "B02",
        1,
        1,
        90.0,
        p2,
        len(p2.encode("utf-8")),
        hashlib.sha256(p2.encode("utf-8")).hexdigest(),
        "sig2",
        ("C02",),
        1,
        source_ranges=(r2,),
    )

    s_id = compute_snapshot_id(
        m_id,
        p_name,
        (b_entry1, b_entry2),
        (bp1, bp2),
        "ev_sig_test",
        32768,
        formatter_policy=policy,
        creative_context_signature=creative_context_signature,
    )

    snap = PreparedBatchSnapshot(
        manifest_id=m_id,
        project_name=p_name,
        snapshot_id=s_id,
        formatter_policy=policy,
        total_batches=2,
        planned_items_count=2,
        distinct_planned_clips_count=2,
        planned_clip_ids=("C01", "C02"),
        planned_clip_ids_count=2,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(bp1, bp2),
        baseline_sources=(b_entry1, b_entry2),
        evidence_context_signature="ev_sig_test",
        ui_context_signature="ui_sig_fixed_abc123",
        creative_context_signature=creative_context_signature,
    )
    return snap, v1, v2, s_id


def _wait_for_worker(worker: PromptPreparationWorker, timeout_sec: float = 5.0) -> Tuple[bool, str, Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    terminal_res = []

    def _on_finished(r_id, ok, msg, res):
        terminal_res.append((ok, msg, res))

    worker.finished_signal.connect(_on_finished)
    worker.start()

    app = QApplication.instance()
    try:
        while time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            if terminal_res and worker.isFinished():
                break
            time.sleep(0.01)
    finally:
        if not worker.isFinished():
            worker.cancel()
            worker.wait(1000)
        if worker.isRunning():
            _RETAINED_RUNNING_WORKERS.append(worker)

    if not terminal_res:
        pytest.fail(f"Worker không phát tín hiệu terminal sau {timeout_sec}s!")
    assert worker.isFinished(), "Worker phải kết thúc vật lý!"
    assert not worker.isRunning(), "Worker không được còn trạng thái running!"
    return terminal_res[0]


# =============================================================================
# TC01: LEGACY NONE / {} PARITY FOR DRAFT INTAKE & OVERVIEW
# =============================================================================

def test_tc01_legacy_parity_draft_intake_and_project_overview(tmp_path, qapp):
    """TC01: Khi không có creative_context (None hoặc {}), payload trả về giữ nguyên key legacy, không có creative_context_signature."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=None)

    # 1. Draft Intake legacy
    raw_draft_json = json.dumps({
        "manifest_id": snap.manifest_id,
        "snapshot_id": snap.snapshot_id,
        "batch_id": "B01",
        "batch_strategy_summary": "Chiến lược phân bổ cảnh quay legacy",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 30.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Legacy test",
            }
        ],
    })

    req_draft = PromptPreparationRequest(
        request_id="req_draft_legacy",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1, v2),
        prepared_batch_snapshot=snap,
        selected_batch_index=0,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=snap.batches[0].rendered_sha256,
        expected_formatter_policy=snap.formatter_policy,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        batch_draft_text=raw_draft_json,
        creative_context=None,
    )
    w_draft = PromptPreparationWorker(req_draft)
    ok_d, msg_d, res_d = _wait_for_worker(w_draft)
    assert ok_d is True
    assert msg_d == "success"
    assert "creative_context_signature" not in res_d
    assert res_d["intent"] == "validate_batch_draft"
    assert res_d["batch_id"] == "B01"

    # 2. Overview legacy
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=None,
    )
    exp_ledger_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    req_ov = PromptPreparationRequest(
        request_id="req_ov_legacy",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_ledger_sig,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context={},  # canonical empty dict
    )
    w_ov = PromptPreparationWorker(req_ov)
    ok_o, msg_o, res_o = _wait_for_worker(w_ov)
    assert ok_o is True
    assert msg_o == "success"
    assert "creative_context_signature" not in res_o
    art = w_ov.get_project_overview_artifact()
    assert art is not None
    assert art.creative_context_signature is None
    assert validate_project_overview_result_against_artifact(res_o, art) is True


# =============================================================================
# TC02: POSITIVE NONEMPTY DRAFT VALIDATION & OVERVIEW RENDERING
# =============================================================================

def test_tc02_positive_nonempty_draft_intake_and_overview_with_signature(tmp_path, qapp):
    """TC02: Positive run với context sáng tạo: trả về creative_context_signature chính xác trong payload và artifact."""
    cc = {
        "free_brief": "Định hướng phim tài liệu du lịch",
        "style_preset": "documentary",
        "target_duration_sec": 120.0,
    }
    cc_sig = compute_creative_context_signature(normalize_creative_context(cc))

    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=cc_sig)

    # 1. Draft Intake positive
    raw_draft_json = json.dumps({
        "manifest_id": snap.manifest_id,
        "snapshot_id": snap.snapshot_id,
        "batch_id": "B01",
        "batch_strategy_summary": "Chiến lược phân bổ cảnh quay creative",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 30.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Creative draft",
            }
        ],
    })

    req_draft = PromptPreparationRequest(
        request_id="req_draft_pos",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1, v2),
        prepared_batch_snapshot=snap,
        selected_batch_index=0,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=snap.batches[0].rendered_sha256,
        expected_formatter_policy=snap.formatter_policy,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        batch_draft_text=raw_draft_json,
        creative_context=cc,
    )
    w_draft = PromptPreparationWorker(req_draft)
    ok_d, msg_d, res_d = _wait_for_worker(w_draft)
    assert ok_d is True
    assert msg_d == "success"
    assert res_d.get("creative_context_signature") == cc_sig

    # 2. Overview positive
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=cc,  # matching duplicate
    )
    exp_ledger_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    req_ov = PromptPreparationRequest(
        request_id="req_ov_pos",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_ledger_sig,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=cc,
    )
    w_ov = PromptPreparationWorker(req_ov)
    ok_o, msg_o, res_o = _wait_for_worker(w_ov)
    assert ok_o is True
    assert msg_o == "success"
    assert res_o.get("creative_context_signature") == cc_sig
    art = w_ov.get_project_overview_artifact()
    assert art is not None
    assert art.creative_context_signature == cc_sig
    assert validate_project_overview_result_against_artifact(res_o, art) is True


# =============================================================================
# TC03: PRE-IO MISMATCH / CONFLICT DENIAL TRIPWIRES (ZERO IO)
# =============================================================================

@pytest.mark.parametrize("mismatch_kind", [
    "snap_legacy_req_creative",
    "snap_creative_req_legacy",
    "signature_mismatch",
    "overview_context_conflict",
    "missing_request_capture_with_overview_cc",
])
def test_tc03_pre_io_denial_tripwires_assert_zero_io(tmp_path, qapp, mismatch_kind, monkeypatch):
    """TC03: Mọi tổ hợp lệch chữ ký / context conflict bị từ chối trước bất kỳ IO nào (0 stat, 0 formatter, 0 validator)."""
    cc_a = {"free_brief": "Brief Alpha"}
    cc_b = {"free_brief": "Brief Beta"}
    sig_a = compute_creative_context_signature(normalize_creative_context(cc_a))
    sig_b = compute_creative_context_signature(normalize_creative_context(cc_b))

    if mismatch_kind == "snap_legacy_req_creative":
        snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=None)
        req_cc = cc_a
        ov_cc = None
    elif mismatch_kind == "snap_creative_req_legacy":
        snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig_a)
        req_cc = None
        ov_cc = None
    elif mismatch_kind == "signature_mismatch":
        snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig_a)
        req_cc = cc_b
        ov_cc = None
    elif mismatch_kind == "overview_context_conflict":
        snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig_a)
        req_cc = cc_a
        ov_cc = cc_b  # Xung đột với request cc
    elif mismatch_kind == "missing_request_capture_with_overview_cc":
        snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=None)
        req_cc = None
        ov_cc = cc_a  # Request rỗng nhưng overview lại có
    else:
        raise ValueError(mismatch_kind)

    # 1. Kiểm thử nhánh validate_batch_draft (nếu liên quan đến request vs snapshot)
    if mismatch_kind in ("snap_legacy_req_creative", "snap_creative_req_legacy", "signature_mismatch"):
        raw_draft_json = json.dumps({
            "manifest_id": snap.manifest_id,
            "snapshot_id": snap.snapshot_id,
            "batch_id": "B01",
            "batch_strategy_summary": "Test",
            "timeline_segments": [],
        })
        req_draft = PromptPreparationRequest(
            request_id=f"req_mismatch_{mismatch_kind}",
            generation=1,
            intent="validate_batch_draft",
            video_paths=(v1, v2),
            prepared_batch_snapshot=snap,
            selected_batch_index=0,
            expected_snapshot_id=snap.snapshot_id,
            expected_batch_id="B01",
            expected_rendered_sha256=snap.batches[0].rendered_sha256,
            expected_formatter_policy=snap.formatter_policy,
            expected_manifest_id=snap.manifest_id,
            expected_project_name=snap.project_name,
            batch_draft_text=raw_draft_json,
            creative_context=req_cc,
        )

        io_called = []
        real_stat = os.stat
        def spy_stat(*args, **kwargs):
            io_called.append(("stat", args))
            return real_stat(*args, **kwargs)

        monkeypatch.setattr(os, "stat", spy_stat)

        w_draft = PromptPreparationWorker(req_draft)
        ok_d, msg_d, res_d = _wait_for_worker(w_draft)
        assert ok_d is False
        assert msg_d == "invalid_snapshot"
        assert res_d.get("error_code") == "ERR_STALE_SNAPSHOT"
        assert len(io_called) == 0, f"Tripwire kích hoạt! Phát hiện {len(io_called)} lần gọi IO trước khi từ chối snapshot."

    # 2. Kiểm thử nhánh copy_project_overview
    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=ov_cc,
    )
    exp_ledger_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    req_ov = PromptPreparationRequest(
        request_id=f"req_ov_mismatch_{mismatch_kind}",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_ledger_sig,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=req_cc,
    )

    io_called_ov = []
    real_stat_ov = os.stat
    def spy_stat_ov(*args, **kwargs):
        io_called_ov.append(("stat", args))
        return real_stat_ov(*args, **kwargs)

    monkeypatch.setattr(os, "stat", spy_stat_ov)

    w_ov = PromptPreparationWorker(req_ov)
    ok_o, msg_o, res_o = _wait_for_worker(w_ov)
    assert ok_o is False
    assert msg_o == "invalid_snapshot"
    assert w_ov.get_project_overview_artifact() is None
    assert len(io_called_ov) == 0, f"Tripwire kích hoạt! Phát hiện {len(io_called_ov)} lần gọi IO trước khi từ chối overview snapshot."


def test_tc03_worker_propagation_deleted_overview_member_denied_pre_io(tmp_path, qapp, monkeypatch):
    """TC03 / C matrix:
    - Xóa trường creative_context trên ProjectOverviewContextInput TRƯỚC construction -> exactly 1 invalid_snapshot, 0 IO, 0 artifact.
    - Positive control 1: duplicate None nhận request capture -> thành công, artifact có signature.
    - Positive control 2: duplicate matching non-empty -> thành công, artifact có signature.
    - Missing whole overview input -> invalid_snapshot.
    """
    cc = {"free_brief": "Propagation test brief"}
    sig = compute_creative_context_signature(normalize_creative_context(cc))
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig)

    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    # --- Case 1: Member deletion BEFORE constructor ---
    ctx_del = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=cc,
    )
    object.__delattr__(ctx_del, "creative_context")

    req_del = PromptPreparationRequest(
        request_id="req_prop_del",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_del,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=cc,
    )

    io_called = []
    real_stat = os.stat
    def spy_stat(*args, **kwargs):
        io_called.append(("stat", args))
        return real_stat(*args, **kwargs)
    monkeypatch.setattr(os, "stat", spy_stat)

    w_del = PromptPreparationWorker(req_del)
    ok_d, msg_d, res_d = _wait_for_worker(w_del)
    assert ok_d is False
    assert msg_d == "invalid_snapshot"
    assert "bị thiếu trường creative_context" in res_d.get("error", "")
    assert len(io_called) == 0, f"Tripwire: phát hiện {len(io_called)} lần gọi IO trước khi từ chối!"
    assert w_del.get_project_overview_artifact() is None

    # --- Case 2: Positive control 1 - well-formed None duplicate receiving request capture ---
    ctx_none = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=None,
    )
    req_none = dataclasses.replace(req_del, request_id="req_prop_none", project_overview_context=ctx_none)
    w_none = PromptPreparationWorker(req_none)
    ok_n, msg_n, res_n = _wait_for_worker(w_none)
    assert ok_n is True
    assert msg_n == "success"
    assert res_n.get("creative_context_signature") == sig
    art_n = w_none.get_project_overview_artifact()
    assert art_n is not None and art_n.creative_context_signature == sig

    # --- Case 3: Positive control 2 - matching nonempty duplicate ---
    ctx_match = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=cc,
    )
    req_match = dataclasses.replace(req_del, request_id="req_prop_match", project_overview_context=ctx_match)
    w_match = PromptPreparationWorker(req_match)
    ok_m, msg_m, res_m = _wait_for_worker(w_match)
    assert ok_m is True
    assert msg_m == "success"
    assert res_m.get("creative_context_signature") == sig
    art_m = w_match.get_project_overview_artifact()
    assert art_m is not None and art_m.creative_context_signature == sig

    # --- Case 4: Missing whole overview input ---
    req_missing_ov = dataclasses.replace(req_del, request_id="req_prop_missing_ov", project_overview_context=None)
    w_missing = PromptPreparationWorker(req_missing_ov)
    ok_mis, msg_mis, res_mis = _wait_for_worker(w_missing)
    assert ok_mis is False
    assert msg_mis == "invalid_snapshot"
    assert "project_overview_context bị thiếu" in res_mis.get("error", "")
    assert w_missing.get_project_overview_artifact() is None


# =============================================================================
# TC06: WORKER BUDGET EQUALITY & 1-BYTE OVERFLOW
# =============================================================================

def test_tc06_worker_overview_budget_equality_and_overflow(tmp_path, qapp):
    """TC06: Worker copy_project_overview tôn trọng trần max_prompt_bytes: equality pass, 1-byte overflow fail."""
    cc = {"free_brief": "Budget overview probe"}
    sig = compute_creative_context_signature(normalize_creative_context(cc))
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig)

    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)

    # 1. Thăm dò độ dài prompt thực tế
    ctx_probe = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=32768,
        creative_context=cc,
    )
    res_probe = render_project_overview_prompt(ctx_probe)
    assert res_probe.ok is True
    exact_bytes = res_probe.rendered_bytes

    # 2. Worker với max_prompt_bytes == exact_bytes (Equality Pass)
    s_id_exact = compute_snapshot_id(
        snap.manifest_id,
        snap.project_name,
        snap.baseline_sources,
        snap.batches,
        snap.evidence_context_signature,
        exact_bytes,
        formatter_policy=snap.formatter_policy,
        creative_context_signature=snap.creative_context_signature,
    )
    snap_exact = dataclasses.replace(snap, max_prompt_bytes=exact_bytes, snapshot_id=s_id_exact)
    e1_exact = _make_dummy_draft_entry(snap.manifest_id, s_id_exact, "B01", source_ranges=snap.batches[0].source_ranges)
    e2_exact = _make_dummy_draft_entry(snap.manifest_id, s_id_exact, "B02", source_ranges=snap.batches[1].source_ranges)
    ctx_exact = dataclasses.replace(
        ctx_probe,
        max_prompt_bytes=exact_bytes,
        snapshot_id=s_id_exact,
        received_drafts=(e1_exact, e2_exact),
    )
    exp_sig_exact = compute_project_overview_ledger_signature(s_id_exact, (e1_exact, e2_exact))

    req_exact = PromptPreparationRequest(
        request_id="req_worker_exact",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=exact_bytes,
        prepared_batch_snapshot=snap_exact,
        project_overview_context=ctx_exact,
        expected_snapshot_id=s_id_exact,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig_exact,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=cc,
    )
    w_exact = PromptPreparationWorker(req_exact)
    ok_ex, msg_ex, res_ex = _wait_for_worker(w_exact)
    assert ok_ex is True
    assert msg_ex == "success"
    assert res_ex["rendered_bytes"] == exact_bytes

    # 3. Worker với max_prompt_bytes == exact_bytes - 1 (1-Byte Overflow Fail)
    s_id_over = compute_snapshot_id(
        snap.manifest_id,
        snap.project_name,
        snap.baseline_sources,
        snap.batches,
        snap.evidence_context_signature,
        exact_bytes - 1,
        formatter_policy=snap.formatter_policy,
        creative_context_signature=snap.creative_context_signature,
    )
    snap_over = dataclasses.replace(snap, max_prompt_bytes=exact_bytes - 1, snapshot_id=s_id_over)
    e1_over = _make_dummy_draft_entry(snap.manifest_id, s_id_over, "B01", source_ranges=snap.batches[0].source_ranges)
    e2_over = _make_dummy_draft_entry(snap.manifest_id, s_id_over, "B02", source_ranges=snap.batches[1].source_ranges)
    ctx_over = dataclasses.replace(
        ctx_probe,
        max_prompt_bytes=exact_bytes - 1,
        snapshot_id=s_id_over,
        received_drafts=(e1_over, e2_over),
    )
    exp_sig_over = compute_project_overview_ledger_signature(s_id_over, (e1_over, e2_over))

    req_over = PromptPreparationRequest(
        request_id="req_worker_over",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=exact_bytes - 1,
        prepared_batch_snapshot=snap_over,
        project_overview_context=ctx_over,
        expected_snapshot_id=s_id_over,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig_over,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=cc,
    )
    w_over = PromptPreparationWorker(req_over)
    ok_ov, msg_ov, res_ov = _wait_for_worker(w_over)
    assert ok_ov is False
    assert msg_ov == "budget_error"
    assert res_ov.get("error_code") == ERR_OVERVIEW_BUDGET_OVERFLOW
    assert res_ov.get("required_bytes") == exact_bytes
    assert w_over.get_project_overview_artifact() is None


# =============================================================================
# TC07: CONSTRUCTOR CAPTURE & BARRIER MUTATION ISOLATION
# =============================================================================

def test_tc07_constructor_capture_and_held_barrier_mutation_isolation(tmp_path, qapp, monkeypatch):
    """TC07: Đột biến request dict / context sau constructor hoặc tại held barrier không thay đổi kết quả worker."""
    import threading

    raw_cc = {"free_brief": "Brief ban đầu tại constructor"}
    sig = compute_creative_context_signature(normalize_creative_context(raw_cc))
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=sig)

    e1 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    e2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)

    ctx_input = ProjectOverviewContextInput(
        manifest_id=snap.manifest_id,
        project_name=snap.project_name,
        snapshot_id=snap.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap.batches[1].source_ranges),
        ),
        received_drafts=(e1, e2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        creative_context=raw_cc,
    )
    exp_sig = compute_project_overview_ledger_signature(snap.snapshot_id, (e1, e2))

    req = PromptPreparationRequest(
        request_id="req_barrier_test",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1, v2),
        style_intent="travel_vlog",
        max_prompt_bytes=snap.max_prompt_bytes,
        prepared_batch_snapshot=snap,
        project_overview_context=ctx_input,
        expected_snapshot_id=snap.snapshot_id,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        expected_ledger_signature=exp_sig,
        expected_ui_context_signature=snap.ui_context_signature,
        creative_context=raw_cc,
    )

    # Cài đặt rào cản đồng bộ giới hạn xác định (Deterministic Bounded Synchronization Barrier)
    barrier_reached = threading.Event()
    barrier_release = threading.Event()

    from src.core.project_overview_contracts import render_project_overview_prompt as orig_render

    def _synced_render(ctx):
        barrier_reached.set()
        if not barrier_release.wait(timeout=5.0):
            raise TimeoutError("ERR_BARRIER_TIMEOUT: barrier_release không được set trong 5.0s")
        return orig_render(ctx)

    monkeypatch.setattr(
        "src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt",
        _synced_render,
    )

    worker = PromptPreparationWorker(req)

    # 1. Đột biến raw_cc của caller SAU KHI worker khởi tạo nhưng TRƯỚC start
    raw_cc["free_brief"] = "Brief ĐÃ BỊ ĐỘT BIẾN SAU INIT"

    try:
        worker.start()
        # Chờ worker thật sự chạy và dừng lại tại rào cản
        assert barrier_reached.wait(timeout=5.0) is True, "Worker không chạm tới rào cản đồng bộ!"

        # 2. Khi worker đang bị giữ tại rào cản: đột biến request creative_context & project_overview_context
        mut_cc = normalize_creative_context({"free_brief": "Brief ĐỘT BIẾN TẠI BARRIER TRÊN REQUEST"})
        object.__setattr__(worker.request, "creative_context", mut_cc)
        mut_ctx = dataclasses.replace(ctx_input, creative_context=mut_cc)
        object.__setattr__(worker.request, "project_overview_context", mut_ctx)

        # Giải phóng rào cản
        barrier_release.set()

        # Chạy worker và thu hoạch kết quả
        ok, msg, res = _wait_for_worker(worker)
        assert ok is True
        assert msg == "success"
        assert "Brief ban đầu tại constructor" in res["rendered_prompt"]
        assert "Brief ĐÃ BỊ ĐỘT BIẾN SAU INIT" not in res["rendered_prompt"]
        assert "Brief ĐỘT BIẾN TẠI BARRIER TRÊN REQUEST" not in res["rendered_prompt"]
        assert res["creative_context_signature"] == sig
    finally:
        barrier_release.set()
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)


# =============================================================================
# TC08: POST-RENDER CANCEL & SOURCE MUTATION / DELETION
# =============================================================================

def test_tc08_post_render_source_mutation_deletion_and_cancel(tmp_path, qapp):
    """TC08: Formatter hoàn thành nhưng xóa/đột biến source file hoặc cancel trước wrapper returns -> fail closed, 0 artifact."""
    from src.core.project_overview_contracts import render_project_overview_prompt as real_render

    cc = {"free_brief": "Post-render boundary"}
    sig = compute_creative_context_signature(normalize_creative_context(cc))

    # --- Subcase A: Xóa file nguồn ngay sau khi render_project_overview_prompt chạy xong ---
    snap_a, v1_a, v2_a, s_id_a = _build_snapshot_fixture(tmp_path / "sub_a", creative_context_signature=sig)
    e1_a = _make_dummy_draft_entry(snap_a.manifest_id, snap_a.snapshot_id, "B01", source_ranges=snap_a.batches[0].source_ranges)
    e2_a = _make_dummy_draft_entry(snap_a.manifest_id, snap_a.snapshot_id, "B02", source_ranges=snap_a.batches[1].source_ranges)
    ctx_a = ProjectOverviewContextInput(
        manifest_id=snap_a.manifest_id,
        project_name=snap_a.project_name,
        snapshot_id=snap_a.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap_a.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap_a.batches[1].source_ranges),
        ),
        received_drafts=(e1_a, e2_a),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_a.max_prompt_bytes,
        creative_context=cc,
    )
    exp_sig_a = compute_project_overview_ledger_signature(snap_a.snapshot_id, (e1_a, e2_a))

    def hook_delete_after_render(ctx):
        res = real_render(ctx)
        if os.path.exists(v1_a):
            os.remove(v1_a)
        return res

    req_del = PromptPreparationRequest(
        request_id="req_post_del",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1_a, v2_a),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_a.max_prompt_bytes,
        prepared_batch_snapshot=snap_a,
        project_overview_context=ctx_a,
        expected_snapshot_id=snap_a.snapshot_id,
        expected_manifest_id=snap_a.manifest_id,
        expected_project_name=snap_a.project_name,
        expected_ledger_signature=exp_sig_a,
        expected_ui_context_signature=snap_a.ui_context_signature,
        creative_context=cc,
    )

    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_delete_after_render):
        w_del = PromptPreparationWorker(req_del)
        ok_d, msg_d, res_d = _wait_for_worker(w_del)
        assert ok_d is False
        assert msg_d == "source_missing"
        assert w_del.get_project_overview_artifact() is None

    # --- Subcase B: Đột biến file nguồn ngay sau khi render xong ---
    snap_b, v1_b, v2_b, s_id_b = _build_snapshot_fixture(tmp_path / "sub_b", creative_context_signature=sig)
    e1_b = _make_dummy_draft_entry(snap_b.manifest_id, snap_b.snapshot_id, "B01", source_ranges=snap_b.batches[0].source_ranges)
    e2_b = _make_dummy_draft_entry(snap_b.manifest_id, snap_b.snapshot_id, "B02", source_ranges=snap_b.batches[1].source_ranges)
    ctx_b = ProjectOverviewContextInput(
        manifest_id=snap_b.manifest_id,
        project_name=snap_b.project_name,
        snapshot_id=snap_b.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap_b.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap_b.batches[1].source_ranges),
        ),
        received_drafts=(e1_b, e2_b),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_b.max_prompt_bytes,
        creative_context=cc,
    )
    exp_sig_b = compute_project_overview_ledger_signature(snap_b.snapshot_id, (e1_b, e2_b))

    def hook_mutate_after_render(ctx):
        res = real_render(ctx)
        with open(v1_b, "ab") as f:
            f.write(b"TAMPER_DATA")
        return res

    req_mut = PromptPreparationRequest(
        request_id="req_post_mut",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1_b, v2_b),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_b.max_prompt_bytes,
        prepared_batch_snapshot=snap_b,
        project_overview_context=ctx_b,
        expected_snapshot_id=snap_b.snapshot_id,
        expected_manifest_id=snap_b.manifest_id,
        expected_project_name=snap_b.project_name,
        expected_ledger_signature=exp_sig_b,
        expected_ui_context_signature=snap_b.ui_context_signature,
        creative_context=cc,
    )
    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_mutate_after_render):
        w_mut = PromptPreparationWorker(req_mut)
        ok_m, msg_m, res_m = _wait_for_worker(w_mut)
        assert ok_m is False
        assert msg_m == "source_changed"
        assert w_mut.get_project_overview_artifact() is None

    # --- Subcase C: Formatter xong -> worker.cancel() trước khi wrapper trả về ---
    snap_c, v1_c, v2_c, s_id_c = _build_snapshot_fixture(tmp_path / "sub_c", creative_context_signature=sig)
    e1_c = _make_dummy_draft_entry(snap_c.manifest_id, snap_c.snapshot_id, "B01", source_ranges=snap_c.batches[0].source_ranges)
    e2_c = _make_dummy_draft_entry(snap_c.manifest_id, snap_c.snapshot_id, "B02", source_ranges=snap_c.batches[1].source_ranges)
    ctx_c = ProjectOverviewContextInput(
        manifest_id=snap_c.manifest_id,
        project_name=snap_c.project_name,
        snapshot_id=snap_c.snapshot_id,
        batches=(
            BatchPartitionRef("B01", 0, snap_c.batches[0].source_ranges),
            BatchPartitionRef("B02", 1, snap_c.batches[1].source_ranges),
        ),
        received_drafts=(e1_c, e2_c),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_c.max_prompt_bytes,
        creative_context=cc,
    )
    exp_sig_c = compute_project_overview_ledger_signature(snap_c.snapshot_id, (e1_c, e2_c))

    req_cancel = PromptPreparationRequest(
        request_id="req_post_cancel",
        generation=1,
        intent="copy_project_overview",
        video_paths=(v1_c, v2_c),
        style_intent="travel_vlog",
        max_prompt_bytes=snap_c.max_prompt_bytes,
        prepared_batch_snapshot=snap_c,
        project_overview_context=ctx_c,
        expected_snapshot_id=snap_c.snapshot_id,
        expected_manifest_id=snap_c.manifest_id,
        expected_project_name=snap_c.project_name,
        expected_ledger_signature=exp_sig_c,
        expected_ui_context_signature=snap_c.ui_context_signature,
        creative_context=cc,
    )
    w_cancel = PromptPreparationWorker(req_cancel)

    def hook_cancel_after_render(ctx):
        res = real_render(ctx)
        w_cancel.cancel()
        return res

    with patch("src.ui.director.workers.prompt_preparation_worker.render_project_overview_prompt", side_effect=hook_cancel_after_render):
        ok_c, msg_c, res_c = _wait_for_worker(w_cancel)
        assert ok_c is False
        assert msg_c == "cancelled"
        assert w_cancel.get_project_overview_artifact() is None


# =============================================================================
# TC09: ARTIFACT TRANSPORT HELPER VALIDATION
# =============================================================================

def test_tc09_artifact_transport_helper_exhaustive_checks():
    """TC09 / B matrix: validate_project_overview_result_against_artifact kiểm tra nghiêm ngặt creative_context_signature."""
    overview_dummy = ProjectOverviewRenderResult(
        ok=True,
        error_code="",
        message="OK",
        rendered_prompt="PROMPT CONTENT DUMMY",
        rendered_bytes=len("PROMPT CONTENT DUMMY".encode("utf-8")),
        rendered_sha256=hashlib.sha256("PROMPT CONTENT DUMMY".encode("utf-8")).hexdigest(),
        formatter_policy="project_overview_v1",
        received_batches_count=2,
        total_batches_expected=2,
        missing_batch_ids=(),
        all_batch_drafts_received=True,
        source_coverage_complete=True,
        required_bytes=len("PROMPT CONTENT DUMMY".encode("utf-8")),
    )
    valid_sig = "a" * 64

    art_creative = PreparedProjectOverviewArtifact(
        request_id="req_art_01",
        generation=1,
        intent="copy_project_overview",
        snapshot_id="snap_01",
        manifest_id="man_01",
        project_name="Proj",
        ledger_signature="b" * 64,
        formatter_policy="project_overview_v1",
        max_prompt_bytes=32768,
        context_signature="ui_sig_123",
        overview=overview_dummy,
        creative_context_signature=valid_sig,
    )

    base_result = {
        "request_id": "req_art_01",
        "generation": 1,
        "intent": "copy_project_overview",
        "snapshot_id": "snap_01",
        "manifest_id": "man_01",
        "project_name": "Proj",
        "ledger_signature": "b" * 64,
        "formatter_policy": "project_overview_v1",
        "max_prompt_bytes": 32768,
        "rendered_prompt": "PROMPT CONTENT DUMMY",
        "rendered_bytes": overview_dummy.rendered_bytes,
        "rendered_sha256": overview_dummy.rendered_sha256,
        "received_batches_count": 2,
        "total_batches_expected": 2,
        "all_batch_drafts_received": True,
        "source_coverage_complete": True,
        "missing_batch_ids": (),
        "creative_context_signature": valid_sig,
    }

    # 1. Positive control: Valid creative artifact + matching transport -> True
    assert validate_project_overview_result_against_artifact(base_result, art_creative) is True

    # 2. B Matrix: Xóa trường instance creative_context_signature trên artifact bằng object.__delattr__
    art_deleted = copy.copy(art_creative)
    object.__delattr__(art_deleted, "creative_context_signature")

    # 2a. Transport vắng mặt signature -> False (R1-B regression: không được ngộ nhận là legacy)
    res_missing_sig = copy.deepcopy(base_result)
    del res_missing_sig["creative_context_signature"]
    assert validate_project_overview_result_against_artifact(res_missing_sig, art_deleted) is False

    # 2b. Transport null signature -> False
    res_null_sig = copy.deepcopy(base_result)
    res_null_sig["creative_context_signature"] = None
    assert validate_project_overview_result_against_artifact(res_null_sig, art_deleted) is False

    # 2c. Transport giữ nguyên signature -> False
    assert validate_project_overview_result_against_artifact(base_result, art_deleted) is False

    # 3. Forged typed artifact qua object.__new__ thiếu trường creative_context_signature -> False
    art_forged = object.__new__(PreparedProjectOverviewArtifact)
    object.__setattr__(art_forged, "request_id", "req_art_01")
    object.__setattr__(art_forged, "generation", 1)
    object.__setattr__(art_forged, "intent", "copy_project_overview")
    object.__setattr__(art_forged, "snapshot_id", "snap_01")
    object.__setattr__(art_forged, "manifest_id", "man_01")
    object.__setattr__(art_forged, "project_name", "Proj")
    object.__setattr__(art_forged, "ledger_signature", "b" * 64)
    object.__setattr__(art_forged, "formatter_policy", "project_overview_v1")
    object.__setattr__(art_forged, "max_prompt_bytes", 32768)
    object.__setattr__(art_forged, "context_signature", "ui_sig_123")
    object.__setattr__(art_forged, "overview", overview_dummy)
    # Cố tình không khởi tạo creative_context_signature

    assert validate_project_overview_result_against_artifact(base_result, art_forged) is False
    assert validate_project_overview_result_against_artifact(res_missing_sig, art_forged) is False

    # 4. Valid legacy default None & explicit None artifacts -> True với legacy transport
    art_leg_default = PreparedProjectOverviewArtifact(
        request_id="req_art_01",
        generation=1,
        intent="copy_project_overview",
        snapshot_id="snap_01",
        manifest_id="man_01",
        project_name="Proj",
        ledger_signature="b" * 64,
        formatter_policy="project_overview_v1",
        max_prompt_bytes=32768,
        context_signature="ui_sig_123",
        overview=overview_dummy,
        # creative_context_signature mặc định là None
    )
    assert validate_project_overview_result_against_artifact(res_missing_sig, art_leg_default) is True

    art_leg_explicit = dataclasses.replace(art_creative, creative_context_signature=None)
    assert validate_project_overview_result_against_artifact(res_missing_sig, art_leg_explicit) is True

    # 5. Invalid type / digest matrix
    # 5a. Chữ ký viết HOA -> False
    res_upper_sig = copy.deepcopy(base_result)
    res_upper_sig["creative_context_signature"] = ("A" * 64)
    assert validate_project_overview_result_against_artifact(res_upper_sig, art_creative) is False

    # 5b. Chữ ký bị tampered khác với artifact -> False
    res_tampered_sig = copy.deepcopy(base_result)
    res_tampered_sig["creative_context_signature"] = "c" * 64
    assert validate_project_overview_result_against_artifact(res_tampered_sig, art_creative) is False

    # 5c. Chữ ký kiểu không hợp lệ (int, bool, độ dài sai) -> False
    for bad_sig in (12345, True, False, "a" * 63, "a" * 65, "g" * 64):
        res_bad_sig = copy.deepcopy(base_result)
        res_bad_sig["creative_context_signature"] = bad_sig
        assert validate_project_overview_result_against_artifact(res_bad_sig, art_creative) is False

    # 5d. Forged prompt cùng kích thước nhưng sai nội dung (kèm tự tính sha256) -> False
    res_forged_prompt = copy.deepcopy(base_result)
    res_forged_prompt["rendered_prompt"] = "FORGED CONTENT DUMMY"
    res_forged_prompt["rendered_sha256"] = hashlib.sha256(b"FORGED CONTENT DUMMY").hexdigest()
    assert validate_project_overview_result_against_artifact(res_forged_prompt, art_creative) is False

    # 5e. Legacy artifact nhưng result lại có creative_context_signature -> False
    assert validate_project_overview_result_against_artifact(base_result, art_leg_explicit) is False


# =============================================================================
# TC10: REAL QTHREAD PHYSICAL LIFECYCLE & CLEANUP
# =============================================================================

def test_tc10_real_qthread_physical_lifecycle_and_cleanup(tmp_path, qapp):
    """TC10: Worker kết thúc vật lý qua QThread thật; cooperative cancel hoạt động sạch sẽ."""
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, creative_context_signature=None)

    raw_draft_json = json.dumps({
        "manifest_id": snap.manifest_id,
        "snapshot_id": snap.snapshot_id,
        "batch_id": "B01",
        "batch_strategy_summary": "Chiến lược phân bổ",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "start_sec": 0.0,
                "end_sec": 30.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "TC10 test",
            }
        ],
    })

    req = PromptPreparationRequest(
        request_id="req_tc10_succ",
        generation=1,
        intent="validate_batch_draft",
        video_paths=(v1, v2),
        prepared_batch_snapshot=snap,
        selected_batch_index=0,
        expected_snapshot_id=snap.snapshot_id,
        expected_batch_id="B01",
        expected_rendered_sha256=snap.batches[0].rendered_sha256,
        expected_formatter_policy=snap.formatter_policy,
        expected_manifest_id=snap.manifest_id,
        expected_project_name=snap.project_name,
        batch_draft_text=raw_draft_json,
    )

    worker = PromptPreparationWorker(req)
    assert worker.isFinished() is False
    assert worker.isRunning() is False

    try:
        ok, msg, res = _wait_for_worker(worker)
        assert ok is True
        assert msg == "success"
        assert worker.isFinished() is True
        assert worker.isRunning() is False
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    # 2. Cooperative cancel test
    req_cancel = dataclasses.replace(req, request_id="req_tc10_cancel")
    w_cancel = PromptPreparationWorker(req_cancel)
    w_cancel.cancel()
    try:
        ok_c, msg_c, res_c = _wait_for_worker(w_cancel)
        assert ok_c is False
        assert msg_c == "cancelled"
        assert w_cancel.isFinished() is True
        assert w_cancel.isRunning() is False
    finally:
        if w_cancel.isRunning():
            w_cancel.cancel()
            w_cancel.wait(2000)
