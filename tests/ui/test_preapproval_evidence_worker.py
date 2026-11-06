"""
tests/ui/test_preapproval_evidence_worker.py
=============================================================================
Kiểm thử bộ phận cho PreapprovalEvidenceWorker (G07-07A4-I).
Bao phủ các nhóm T05, T06: Real worker lifecycle, cancel, InterruptedError.
=============================================================================
"""

import time
import pytest
from PySide6.QtCore import QCoreApplication

from src.core.geometry.source_evidence_service import (
    SourceEvidenceBatchResult,
    SourceEvidenceItemRequest,
    SourceExecutionEvidenceEnvelope,
    SourceFreshnessStat,
)
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.contracts.time_contracts import RationalFPS
from src.ui.director.workers.preapproval_evidence_worker import (
    PreapprovalEvidenceRequest,
    PreapprovalEvidenceTerminalPayload,
    PreapprovalEvidenceWorker,
)


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


SAMPLE_SHA = "c" * 64


def _make_dummy_envelope(clip_id="C01", manifest_id="mf_001", path="/dummy/c01.mp4", status="VERIFIED_CFR"):
    fe = SourceFrameEvidence(
        manifest_id=manifest_id,
        clip_id=clip_id,
        source_fps=RationalFPS(30, 1),
        total_frames=300,
        evidence_status=status,
    )
    freshness = SourceFreshnessStat(
        file_size_bytes=1000,
        mtime_ns=1000,
        ctime_ns=1000,
        device_id=1,
        inode_number=2,
    )
    return SourceExecutionEvidenceEnvelope(
        clip_id=clip_id,
        manifest_id=manifest_id,
        file_path=path,
        stream_index=0,
        expected_full_sha256=None,
        observed_full_sha256=SAMPLE_SHA,
        freshness=freshness,
        frame_evidence=fe,
        complete_scan=(status == "VERIFIED_CFR"),
        profile_policy="decoded_frame_uniform_ticks_v1",
    )


class MockSourceEvidenceService:
    def __init__(self, mode="success", delay=0.01):
        self.mode = mode
        self.delay = delay
        self.call_count = 0

    def acquire_evidence_for_batch(self, request, *, cancelled_fn=None, progress_fn=None):
        self.call_count += 1
        if cancelled_fn and cancelled_fn():
            return SourceEvidenceBatchResult(status="cancelled", evidence_by_clip={})

        if self.delay > 0:
            time.sleep(self.delay)

        if cancelled_fn and cancelled_fn():
            return SourceEvidenceBatchResult(status="cancelled", evidence_by_clip={})

        if self.mode == "interrupted":
            raise InterruptedError("Simulated scanner interrupted error")

        if self.mode == "exception":
            raise RuntimeError("Simulated unexpected crash")

        if self.mode == "failed":
            return SourceEvidenceBatchResult(status="failed", evidence_by_clip={}, diagnostics=("Batch error",))

        # Mode success: Trả về 1 clip CFR và 1 clip HEADER_REPORTED_ONLY
        ev_map = {}
        for it in request.items:
            if it.clip_id == "C01":
                ev_map["C01"] = _make_dummy_envelope(clip_id="C01", manifest_id=it.manifest_id, path=it.file_path, status="VERIFIED_CFR")
            else:
                ev_map[it.clip_id] = _make_dummy_envelope(clip_id=it.clip_id, manifest_id=it.manifest_id, path=it.file_path, status="HEADER_REPORTED_ONLY")

        return SourceEvidenceBatchResult(status="completed", evidence_by_clip=ev_map)


def _wait_for_worker(worker, qapp, timeout_sec=3.0):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if worker.isFinished() and not worker.isRunning():
            qapp.processEvents()
            return True
        worker.wait(50)
        qapp.processEvents()
    qapp.processEvents()
    return worker.isFinished() and not worker.isRunning()


# =============================================================================
# T05: Real Worker Lifecycle & Truthful K/N
# =============================================================================

def test_t05_real_worker_success_and_truthful_kn(qapp):
    items = (
        SourceEvidenceItemRequest(clip_id="C01", manifest_id="mf_001", file_path="/dummy/c01.mp4"),
        SourceEvidenceItemRequest(clip_id="C02", manifest_id="mf_001", file_path="/dummy/c02.mp4"),
    )
    req = PreapprovalEvidenceRequest(
        request_id="req_001",
        generation=1,
        manifest_id="mf_001",
        input_signature=SAMPLE_SHA,
        items=items,
    )
    mock_svc = MockSourceEvidenceService(mode="success", delay=0.01)
    worker = PreapprovalEvidenceWorker(req, service=mock_svc)

    term_payloads = []
    finished_calls = []
    worker.terminal_signal.connect(term_payloads.append)
    worker.finished.connect(lambda: finished_calls.append(True))

    worker.start()
    _wait_for_worker(worker, qapp)

    # Exactly 1 terminal signal
    assert len(term_payloads) == 1
    assert len(finished_calls) == 1

    payload = term_payloads[0]
    assert isinstance(payload, PreapprovalEvidenceTerminalPayload)
    assert payload.status == "completed"
    assert payload.request_id == "req_001"
    assert payload.generation == 1
    assert payload.input_signature == SAMPLE_SHA

    # Truthful K/N: C01 verified, C02 header-only (unverified)
    assert payload.verified_count == 1
    assert payload.total_count == 2
    assert "C01" in payload.verified_evidence_by_clip
    assert "C02" not in payload.verified_evidence_by_clip
    assert "C02" in payload.raw_evidence_by_clip


# =============================================================================
# T06: Cancel & Error Handling
# =============================================================================

def test_t06_cancel_before_start(qapp):
    items = (
        SourceEvidenceItemRequest(clip_id="C01", manifest_id="mf_001", file_path="/dummy/c01.mp4"),
    )
    req = PreapprovalEvidenceRequest(
        request_id="req_002",
        generation=1,
        manifest_id="mf_001",
        input_signature=SAMPLE_SHA,
        items=items,
    )
    mock_svc = MockSourceEvidenceService(mode="success")
    worker = PreapprovalEvidenceWorker(req, service=mock_svc)

    term_payloads = []
    worker.terminal_signal.connect(term_payloads.append)

    # Cancel trước khi start
    worker.cancel()
    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(term_payloads) == 1
    assert term_payloads[0].status == "cancelled"
    assert mock_svc.call_count == 0  # Không bao giờ gọi service


def test_t06_interrupted_error_mapped_to_cancelled(qapp):
    items = (
        SourceEvidenceItemRequest(clip_id="C01", manifest_id="mf_001", file_path="/dummy/c01.mp4"),
    )
    req = PreapprovalEvidenceRequest(
        request_id="req_003",
        generation=1,
        manifest_id="mf_001",
        input_signature=SAMPLE_SHA,
        items=items,
    )
    mock_svc = MockSourceEvidenceService(mode="interrupted", delay=0.01)
    worker = PreapprovalEvidenceWorker(req, service=mock_svc)

    term_payloads = []
    worker.terminal_signal.connect(term_payloads.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(term_payloads) == 1
    assert term_payloads[0].status == "cancelled"


def test_t06_unexpected_exception_mapped_to_failed(qapp):
    items = (
        SourceEvidenceItemRequest(clip_id="C01", manifest_id="mf_001", file_path="/dummy/c01.mp4"),
    )
    req = PreapprovalEvidenceRequest(
        request_id="req_004",
        generation=1,
        manifest_id="mf_001",
        input_signature=SAMPLE_SHA,
        items=items,
    )
    mock_svc = MockSourceEvidenceService(mode="exception", delay=0.01)
    worker = PreapprovalEvidenceWorker(req, service=mock_svc)

    term_payloads = []
    worker.terminal_signal.connect(term_payloads.append)

    worker.start()
    _wait_for_worker(worker, qapp)

    assert len(term_payloads) == 1
    assert term_payloads[0].status == "failed"
    assert "Simulated unexpected crash" in term_payloads[0].diagnostics[0]


def test_r1_group_b_worker_owned_and_bijective_graph(qapp):
    import dataclasses as dc
    from types import SimpleNamespace

    item = SourceEvidenceItemRequest("C01", "mf_001", "/dummy/c01.mp4")
    def req(**kw):
        data = dict(request_id="req", generation=1, manifest_id="mf_001", input_signature=SAMPLE_SHA, items=(item,))
        data.update(kw)
        return PreapprovalEvidenceRequest(**data)

    # 1. worker_owns_request and request_owns_item
    r = req()
    w = PreapprovalEvidenceWorker(r)
    assert w.request is not r
    assert r.items[0] is not item

    # 2. Rejections on malformed requests
    with pytest.raises(ValueError, match="ERR_INVALID_INPUT_SIGNATURE"):
        req(input_signature="not-a-sha")
    with pytest.raises(ValueError, match="ERR_INVALID_BUDGET"):
        req(timeout_per_file_sec=True)
    with pytest.raises(ValueError, match="ERR_DUPLICATE_CLIP_ID"):
        req(items=(item, item))
    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        req(manifest_id="other")
    with pytest.raises(ValueError, match="ERR_PREBOUND_SHA"):
        req(items=(dc.replace(item, expected_full_sha256="f" * 64),))

    # 3. Bijective graph checks
    def run_svc(raw):
        worker = PreapprovalEvidenceWorker(req(), service=SimpleNamespace(acquire_evidence_for_batch=lambda *a, **kw: raw))
        res = []
        worker.terminal_signal.connect(res.append)
        worker.run()
        assert len(res) == 1
        return res[0]

    positive = SourceEvidenceBatchResult("completed", {"C01": _make_dummy_envelope()})
    assert run_svc(positive).status == "completed"

    missing = SourceEvidenceBatchResult("completed", {})
    assert run_svc(missing).status == "failed"

    extra = SourceEvidenceBatchResult("completed", {"C01": _make_dummy_envelope(), "C02": _make_dummy_envelope(clip_id="C02")})
    assert run_svc(extra).status == "failed"

    wrong = SourceEvidenceBatchResult("completed", {"C01": _make_dummy_envelope(path="/foreign/source.mp4")})
    assert run_svc(wrong).status == "failed"

    # 4. Immutability of terminal payload mappings
    prod = run_svc(positive)
    with pytest.raises((TypeError, ValueError, AttributeError)):
        prod.verified_evidence_by_clip["C01"]["stream_index"] = 9
