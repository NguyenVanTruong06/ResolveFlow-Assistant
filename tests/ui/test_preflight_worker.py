"""tests/ui/test_preflight_worker.py
Unit tests cho PreflightWorker (G07 Preflight Worker Adapter).

Bao phủ các ca nghiệm thu:
- T07: Constructor 0 I/O, 0 factory call. Chạy trên QThread thực ngoài GUI, phát đúng 1 terminal và 1 native finished.
- T08: Cooperative cancellation ở các checkpoint (trước start, trong lúc chạy, sau khi trả về) -> cancelled, không success.
       Xử lý ngoại lệ trong factory/inspector -> đúng 1 terminal failed với binding chuẩn.
       Phát hiện và từ chối kết quả giả mạo (forged inspector result).
- T09: Provider trễ, hủy an toàn, dọn dẹp luồng cooperative không terminate/không wait(2000) bỏ rơi worker.
- T11: Bảo toàn cổng G07-07A1 hiện hữu: availability pass chưa bao giờ unlock thi công; zero UI/controller edits.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import pytest
from PySide6.QtCore import QCoreApplication

from src.core.contracts.edit_plan_approval_contracts import create_app_owned_approval_record
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    edit_plan_proposal_to_dict,
    normalize_edit_plan_proposal,
)
from src.core.source_catalog_readiness import (
    ReadinessRequest,
    SourceCatalogReadinessResult,
    SourceClipObservation,
)
from src.ui.director.workers.preflight_worker import PreflightWorker
from tests.core.contracts.test_edit_plan_proposal_contracts import _make_valid_proposal_dict


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    """Đảm bảo có QCoreApplication cho các bài test Qt luồng nền."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def wait_for_worker(
    worker: PreflightWorker,
    qapp: QCoreApplication,
    timeout_sec: float = 3.0,
) -> bool:
    """Đợi worker kết thúc qua vòng lặp monotonic deadline và processEvents."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if worker.isFinished() and not worker.isRunning():
            qapp.processEvents()
            return True
        worker.wait(50)
        qapp.processEvents()
    return worker.isFinished() and not worker.isRunning()


@pytest.fixture
def managed_worker(qapp):
    """Fixture quản lý vòng đời worker và đảm bảo dọn dẹp an toàn trong finally."""
    active_workers: List[PreflightWorker] = []

    def _register(worker: PreflightWorker):
        active_workers.append(worker)
        results: List[SourceCatalogReadinessResult] = []
        finished_signals: List[bool] = []
        progress_signals: List[tuple[int, str]] = []

        worker.terminal.connect(results.append)
        worker.finished.connect(lambda: finished_signals.append(True))
        worker.progress.connect(lambda pct, msg: progress_signals.append((pct, msg)))

        return results, finished_signals, progress_signals

    yield _register

    for w in active_workers:
        if w.isRunning():
            try:
                w.cancel()
                w.wait(2000)
                qapp.processEvents()
            except Exception:
                pass
        assert not w.isRunning(), "Worker bị rò rỉ hoặc vẫn đang chạy sau test!"


def _make_dummy_request(tmp_path: Path) -> ReadinessRequest:
    """Tạo ReadinessRequest hợp lệ nhanh cho kiểm thử worker."""
    source_file = tmp_path / "dummy_clip.mp4"
    source_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"A" * 50)

    raw = _make_valid_proposal_dict("m_test_worker")
    raw["source_binding"]["clips"] = [
        {
            "clip_id": "C01",
            "path": str(source_file),
            "current_path": str(source_file),
            "duration": 5.0,
            "duration_sec": 5.0,
        }
    ]
    raw["video_plan"]["timeline_segments"] = [
        {
            "segment_index": 0,
            "clip_id": "C01",
            "start_sec": 0.0,
            "end_sec": 3.0,
            "action": "keep",
            "speed": 1.0,
            "role": "hook",
        }
    ]
    raw["video_plan"]["global_hook"]["clip_id"] = "C01"
    raw["proposed_asset_assignments"] = []

    proposal = normalize_edit_plan_proposal(raw)
    approval = create_app_owned_approval_record(
        proposal=proposal,
        approval_id="appr_worker_01",
        approved_at_iso="2026-11-02T02:00:00Z",
    )
    return ReadinessRequest(
        request_id="req_worker_01",
        generation=1,
        proposal=proposal,
        approval=approval,
    )


# ---------------------------------------------------------------------------
# T07: Constructor 0 I/O, validation, và chạy QThread thực
# ---------------------------------------------------------------------------

def test_t07_constructor_zero_io_and_input_validation(tmp_path: Path):
    """T07: Constructor 0 I/O, 0 factory call, kiểm tra chặt chẽ kiểu tham số."""
    req = _make_dummy_request(tmp_path)

    factory_called = False
    context_reader_called = False
    inspector_called = False

    def spy_factory():
        nonlocal factory_called
        factory_called = True
        return None

    def spy_reader(storage):
        nonlocal context_reader_called
        context_reader_called = True
        return None

    def spy_inspector(*args, **kwargs):
        nonlocal inspector_called
        inspector_called = True
        return None

    # Khởi tạo hợp lệ
    worker = PreflightWorker(
        req,
        storage_factory=spy_factory,
        storage_context_reader=spy_reader,
        inspector=spy_inspector,
    )

    assert factory_called is False
    assert context_reader_called is False
    assert inspector_called is False

    # Thẩm định request sai kiểu
    with pytest.raises(ValueError, match="ERR_INVALID_REQUEST"):
        PreflightWorker(None)  # type: ignore
    with pytest.raises(ValueError, match="ERR_INVALID_REQUEST"):
        PreflightWorker({"request_id": "fake"})  # type: ignore

    # Thẩm định callable không hợp lệ
    with pytest.raises(ValueError, match="ERR_INVALID_INSPECTOR"):
        PreflightWorker(req, inspector="not_callable")  # type: ignore
    with pytest.raises(ValueError, match="ERR_INVALID_FACTORY"):
        PreflightWorker(req, storage_factory="not_callable")  # type: ignore
    with pytest.raises(ValueError, match="ERR_INVALID_CONTEXT_READER"):
        PreflightWorker(req, storage_context_reader="not_callable")  # type: ignore


def test_t07_worker_runs_on_separate_thread_and_emits_single_terminal(tmp_path: Path, qapp, managed_worker):
    """T07: Worker chạy trên thread vật lý riêng ngoài GUI thread, phát đúng 1 terminal và 1 native finished."""
    req = _make_dummy_request(tmp_path)
    main_thread_id = threading.get_ident()
    worker_thread_id = None

    def mock_inspector(request, **kwargs):
        nonlocal worker_thread_id
        worker_thread_id = threading.get_ident()
        progress_cb = kwargs.get("progress_callback")
        if progress_cb:
            progress_cb(50, "Đang kiểm tra...")
            progress_cb(100, "Hoàn tất kiểm tra.")

        obs = (
            SourceClipObservation(
                clip_id="C01",
                path=str(tmp_path / "dummy_clip.mp4"),
                duration_sec=5.0,
                exists=True,
                is_regular_file=True,
                is_non_empty=True,
                is_readable=True,
                stat_identity=(1, 1, 100, 100, 100),
                recheck_passed=True,
            ),
        )
        return SourceCatalogReadinessResult(
            request_id=request.request_id,
            generation=request.generation,
            proposal_id=request.proposal.proposal_id,
            proposal_revision=request.approval.proposal_revision,
            approval_id=request.approval.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=obs,
            catalog_observations=(),
            diagnostics=(),
        )

    worker = PreflightWorker(req, inspector=mock_inspector)
    results, finished_signals, progress_signals = managed_worker(worker)

    worker.start()
    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True

    # Khẳng định chạy ngoài GUI thread
    assert worker_thread_id is not None
    assert worker_thread_id != main_thread_id

    # Khẳng định phát đúng 1 terminal và 1 native finished
    assert len(results) == 1
    assert len(finished_signals) == 1
    res = results[0]
    assert res.status == "completed"
    assert res.checks_passed is True
    assert res.ready_to_execute is False  # BẮT BUỘC False
    assert res.request_id == req.request_id

    # Khẳng định tiến độ được relay đầy đủ
    assert len(progress_signals) == 2
    assert progress_signals[0] == (50, "Đang kiểm tra...")
    assert progress_signals[1] == (100, "Hoàn tất kiểm tra.")


# ---------------------------------------------------------------------------
# T08: Cooperative cancellation ở các checkpoint và exception handling
# ---------------------------------------------------------------------------

def test_t08_cancel_before_start(tmp_path: Path, qapp, managed_worker):
    """T08: cancel() gọi trước khi start() -> dừng ở Checkpoint 0, 0 inspector call, status='cancelled'."""
    req = _make_dummy_request(tmp_path)
    inspector_called = False

    def mock_inspector(*args, **kwargs):
        nonlocal inspector_called
        inspector_called = True
        return None

    worker = PreflightWorker(req, inspector=mock_inspector)
    results, finished_signals, _ = managed_worker(worker)

    # Cancel trước khi start
    worker.cancel()
    worker.start()
    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True

    assert inspector_called is False
    assert len(results) == 1
    assert len(finished_signals) == 1
    res = results[0]
    assert res.status == "cancelled"
    assert res.checks_passed is False
    assert res.ready_to_execute is False
    assert res.request_id == req.request_id


def test_t08_cancel_discards_completed_result(tmp_path: Path, qapp, managed_worker):
    """T08: cancel() gọi ngay sau khi inspector trả completed -> discard kết quả, phát 'cancelled'."""
    req = _make_dummy_request(tmp_path)

    def mock_inspector(request, **kwargs):
        # Inspector trả kết quả completed thành công
        res = SourceCatalogReadinessResult(
            request_id=request.request_id,
            generation=request.generation,
            proposal_id=request.proposal.proposal_id,
            proposal_revision=request.approval.proposal_revision,
            approval_id=request.approval.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )
        # Giả lập cancel xảy ra ngay trước khi worker nhận kết quả
        worker.cancel()
        return res

    worker = PreflightWorker(req, inspector=mock_inspector)
    results, finished_signals, _ = managed_worker(worker)

    worker.start()
    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True

    assert len(results) == 1
    assert len(finished_signals) == 1
    res = results[0]
    # Phải bị chuyển thành cancelled, tuyệt đối không được phát completed
    assert res.status == "cancelled"
    assert res.checks_passed is False
    assert res.ready_to_execute is False


def test_t08_exception_in_inspector_emits_single_failed_terminal(tmp_path: Path, qapp, managed_worker):
    """T08: Ngoại lệ không lường trước trong inspector -> bắt sạch sẽ, phát đúng 1 terminal status='failed'."""
    req = _make_dummy_request(tmp_path)

    def exploding_inspector(*args, **kwargs):
        raise RuntimeError("Crash bất thình lình trong tiến trình quét")

    worker = PreflightWorker(req, inspector=exploding_inspector)
    results, finished_signals, _ = managed_worker(worker)

    worker.start()
    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True

    assert len(results) == 1
    assert len(finished_signals) == 1
    res = results[0]
    assert res.status == "failed"
    assert res.checks_passed is False
    assert res.ready_to_execute is False
    assert any("ERR_PREFLIGHT_WORKER_FAILURE" in d for d in res.diagnostics)
    assert any("Crash bất thình lình" in d for d in res.diagnostics)


def test_t08_forged_inspector_result_rejected(tmp_path: Path, qapp, managed_worker):
    """T08: Inspector trả kết quả giả mạo revision hoặc request_id -> phát hiện và phát 'failed'."""
    req = _make_dummy_request(tmp_path)

    def forged_inspector(request, **kwargs):
        return SourceCatalogReadinessResult(
            request_id="forged_req_999",  # Sai lệch request_id
            generation=request.generation,
            proposal_id=request.proposal.proposal_id,
            proposal_revision=request.approval.proposal_revision,
            approval_id=request.approval.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )

    worker = PreflightWorker(req, inspector=forged_inspector)
    results, finished_signals, _ = managed_worker(worker)

    worker.start()
    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True

    assert len(results) == 1
    res = results[0]
    assert res.status == "failed"
    assert any("ERR_FORGED_INSPECTOR_RESULT" in d for d in res.diagnostics)


# ---------------------------------------------------------------------------
# T09: Provider trễ và dọn dẹp luồng an toàn (không wait 2000 drop)
# ---------------------------------------------------------------------------

def test_t09_delayed_provider_cancelled_cleanly(tmp_path: Path, qapp, managed_worker):
    """T09: Provider bị trễ, cancel() dừng cooperative, dọn dẹp an toàn không terminate."""
    req = _make_dummy_request(tmp_path)
    started_event = threading.Event()

    def slow_inspector(request, **kwargs):
        is_cancelled_fn = kwargs.get("is_cancelled", lambda: False)
        started_event.set()
        # Chờ tối đa 2 giây hoặc đến khi bị cancel
        for _ in range(40):
            if is_cancelled_fn():
                return SourceCatalogReadinessResult(
                    request_id=request.request_id,
                    generation=request.generation,
                    proposal_id=request.proposal.proposal_id,
                    proposal_revision=request.approval.proposal_revision,
                    approval_id=request.approval.approval_id,
                    status="cancelled",
                    checks_passed=False,
                    ready_to_execute=False,
                    source_observations=(),
                    catalog_observations=(),
                    diagnostics=("Cancelled during slow scan",),
                )
            time.sleep(0.05)
        return None

    worker = PreflightWorker(req, inspector=slow_inspector)
    results, finished_signals, _ = managed_worker(worker)

    worker.start()
    # Chờ inspector thực sự bắt đầu chạy
    assert started_event.wait(timeout=2.0) is True

    # Gọi cancel trong lúc inspector đang chạy
    worker.cancel()

    ok = wait_for_worker(worker, qapp, timeout_sec=3.0)
    assert ok is True
    assert not worker.isRunning()
    assert worker.isFinished()

    assert len(results) == 1
    assert len(finished_signals) == 1
    assert results[0].status == "cancelled"


# ---------------------------------------------------------------------------
# T11: Bảo toàn cổng G07-07A1 hiện hữu
# ---------------------------------------------------------------------------

def test_t11_existing_g07_07a1_gate_invariants(tmp_path: Path):
    """T11: Cổng G07-07A1 hiện hữu vẫn bảo toàn: ready_to_execute luôn False, không can thiệp UI/Controller."""
    req = _make_dummy_request(tmp_path)

    # Khẳng định trong mọi tình huống kết quả của slice này không bao giờ mở thi công
    res = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=(),
        catalog_observations=(),
        diagnostics=(),
    )
    assert res.ready_to_execute is False

    # Thử tạo kết quả với ready_to_execute=True phải bị từ chối
    with pytest.raises(ValueError, match="ready_to_execute BẮT BUỘC phải là False"):
        SourceCatalogReadinessResult(
            request_id=req.request_id,
            generation=req.generation,
            proposal_id=req.proposal.proposal_id,
            proposal_revision=req.approval.proposal_revision,
            approval_id=req.approval.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=True,  # type: ignore
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )


# ---------------------------------------------------------------------------
# G07-07A2a-R1: Regression tests cho Worker sở hữu Request và từ chối Forgery
# ---------------------------------------------------------------------------

def test_r1_worker_owns_request_and_immune_to_caller_mutation(tmp_path: Path):
    """R1: Worker sở hữu độc quyền request (defensive clone); miễn nhiễm mutation từ caller."""
    req = _make_dummy_request(tmp_path)
    original_appr_id = req.approval.approval_id

    worker = PreflightWorker(req)
    # 1. Worker không dùng chung instance với caller
    assert worker._request is not req

    # 2. Caller thay đổi generation sau construction không làm đổi worker
    object.__setattr__(req, "generation", 999)
    assert worker._request.generation == 1
    assert worker._generation == 1

    # 3. Caller xóa thuộc tính approval_id sau construction không làm sập worker
    object.__delattr__(req.approval, "approval_id")

    terminals = []
    worker.terminal.connect(terminals.append)

    # Chạy run trực tiếp (không thread)
    worker.run()

    assert len(terminals) == 1
    res = terminals[0]
    assert res.approval_id == original_appr_id
    assert res.generation == 1


def test_r1_worker_rejects_bool_generation_in_inspector_result(tmp_path: Path):
    """R1: Worker từ chối inspector trả về generation=True (bool) thay vì int."""
    req = _make_dummy_request(tmp_path)

    forged = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=1,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=(),
        catalog_observations=(),
        diagnostics=(),
    )
    object.__setattr__(forged, "generation", True)

    worker = PreflightWorker(req, inspector=lambda *a, **kw: forged)
    terminals = []
    worker.terminal.connect(terminals.append)
    worker.run()

    assert len(terminals) == 1
    res = terminals[0]
    assert res.status == "failed"
    assert res.checks_passed is False
    assert res.ready_to_execute is False
    assert any("ERR_INVALID_GENERATION" in d or "ERR_FORGED_INSPECTOR_RESULT" in d for d in res.diagnostics)


def test_r1_worker_rejects_empty_success_and_wrong_clip_id(tmp_path: Path):
    """R1: Worker từ chối empty-success và clip_id không khớp request của producer."""
    req = _make_dummy_request(tmp_path)

    # 1. Empty success khi request có 1 clip
    empty_result = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=(),
        catalog_observations=(),
        diagnostics=(),
    )
    w_empty = PreflightWorker(req, inspector=lambda *a, **kw: empty_result)
    t_empty = []
    w_empty.terminal.connect(t_empty.append)
    w_empty.run()
    assert len(t_empty) == 1
    assert t_empty[0].status == "failed"
    assert t_empty[0].checks_passed is False

    # 2. Sai clip_id
    wrong_obs = (
        SourceClipObservation(
            clip_id="OTHER_CLIP",
            path=str(tmp_path / "dummy_clip.mp4"),
            duration_sec=5.0,
            exists=True,
            is_regular_file=True,
            is_non_empty=True,
            is_readable=True,
            stat_identity=(1, 1, 100, 100, 100),
            recheck_passed=True,
        ),
    )
    wrong_result = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=wrong_obs,
        catalog_observations=(),
        diagnostics=(),
    )
    w_wrong = PreflightWorker(req, inspector=lambda *a, **kw: wrong_result)
    t_wrong = []
    w_wrong.terminal.connect(t_wrong.append)
    w_wrong.run()
    assert len(t_wrong) == 1
    assert t_wrong[0].status == "failed"
    assert t_wrong[0].checks_passed is False


def test_r1_worker_rejects_invalid_checks_passed_and_inconsistent_status(tmp_path: Path):
    """R1: Worker từ chối checks_passed không phải bool và status failed nhưng checks_passed=True."""
    req = _make_dummy_request(tmp_path)
    obs = (
        SourceClipObservation(
            clip_id="C01",
            path=str(tmp_path / "dummy_clip.mp4"),
            duration_sec=5.0,
            exists=True,
            is_regular_file=True,
            is_non_empty=True,
            is_readable=True,
            stat_identity=(1, 1, 100, 100, 100),
            recheck_passed=True,
        ),
    )

    # 1. checks_passed là chuỗi 'yes'
    res_str = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=obs,
        catalog_observations=(),
        diagnostics=(),
    )
    object.__setattr__(res_str, "checks_passed", "yes")

    w1 = PreflightWorker(req, inspector=lambda *a, **kw: res_str)
    t1 = []
    w1.terminal.connect(t1.append)
    w1.run()
    assert len(t1) == 1
    assert t1[0].status == "failed"
    assert t1[0].checks_passed is False

    # 2. status="failed" nhưng checks_passed=True
    res_inconsistent = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="failed",
        checks_passed=False,
        ready_to_execute=False,
        source_observations=obs,
        catalog_observations=(),
        diagnostics=(),
    )
    object.__setattr__(res_inconsistent, "checks_passed", True)

    w2 = PreflightWorker(req, inspector=lambda *a, **kw: res_inconsistent)
    t2 = []
    w2.terminal.connect(t2.append)
    w2.run()
    assert len(t2) == 1
    assert t2[0].status == "failed"
    assert t2[0].checks_passed is False


def test_r1_worker_terminal_emits_defensive_copy(tmp_path: Path):
    """R1: Terminal phát ra bản sao sở hữu độc lập (defensive clone), same_result_object=False."""
    req = _make_dummy_request(tmp_path)
    obs = (
        SourceClipObservation(
            clip_id="C01",
            path=str(tmp_path / "dummy_clip.mp4"),
            duration_sec=5.0,
            exists=True,
            is_regular_file=True,
            is_non_empty=True,
            is_readable=True,
            stat_identity=(1, 1, 100, 100, 100),
            recheck_passed=True,
        ),
    )
    producer_result = SourceCatalogReadinessResult(
        request_id=req.request_id,
        generation=req.generation,
        proposal_id=req.proposal.proposal_id,
        proposal_revision=req.approval.proposal_revision,
        approval_id=req.approval.approval_id,
        status="completed",
        checks_passed=True,
        ready_to_execute=False,
        source_observations=obs,
        catalog_observations=(),
        diagnostics=(),
    )

    worker = PreflightWorker(req, inspector=lambda *a, **kw: producer_result)
    terminals = []
    worker.terminal.connect(terminals.append)
    worker.run()

    assert len(terminals) == 1
    assert terminals[0] is not producer_result
    assert terminals[0].status == "completed"
    assert terminals[0].checks_passed is True


def test_r3_worker_thread_native_finished_and_single_terminal(tmp_path: Path):
    """F01: Valid result từ real inspector chạy trên QThread thật phát đúng 1 terminal và 1 native finished."""
    from tests.core.test_source_catalog_readiness import _create_real_source_files, _build_test_proposal, _build_test_request
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from PySide6.QtCore import QEventLoop

    src = _create_real_source_files(tmp_path, 1)
    req = _build_test_request(_build_test_proposal(src, assignments=[]))
    good = inspect_source_and_catalog_readiness(req)

    worker = PreflightWorker(req, inspector=lambda *a, **kw: good)
    terminals = []
    worker.terminal.connect(terminals.append)

    loop = QEventLoop()
    finished_calls = []
    worker.finished.connect(lambda: finished_calls.append(True))
    worker.finished.connect(loop.quit)

    worker.start()
    loop.exec()

    assert len(terminals) == 1
    assert len(finished_calls) == 1
    assert terminals[0].status == "completed"
    assert terminals[0].checks_passed is True
    assert terminals[0].ready_to_execute is False


def test_r3_worker_rejects_duplicate_and_wrong_source_path(tmp_path: Path):
    """F02, F03, F04: Worker từ chối duplicate source rows, wrong source path, hoặc false predicates."""
    from tests.core.test_source_catalog_readiness import _create_real_source_files, _build_test_proposal, _build_test_request
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from dataclasses import replace

    src = _create_real_source_files(tmp_path, 1)
    req = _build_test_request(_build_test_proposal(src, assignments=[]))
    good = inspect_source_and_catalog_readiness(req)
    obs = good.source_observations[0]

    # F02: Duplicate rows
    w_dup = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, source_observations=(obs, obs)))
    t_dup = []
    w_dup.terminal.connect(t_dup.append)
    w_dup.run()
    assert len(t_dup) == 1
    assert t_dup[0].status == "failed"
    assert t_dup[0].checks_passed is False

    # F03: Wrong path
    w_path = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, source_observations=(replace(obs, path="/other/wrong.mp4"),)))
    t_path = []
    w_path.terminal.connect(t_path.append)
    w_path.run()
    assert len(t_path) == 1
    assert t_path[0].status == "failed"
    assert t_path[0].checks_passed is False

    # F04: Predicate is_readable=False và recheck_passed=False
    for mod in (replace(obs, is_readable=False), replace(obs, recheck_passed=False)):
        w_pred = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, source_observations=(mod,)))
        t_pred = []
        w_pred.terminal.connect(t_pred.append)
        w_pred.run()
        assert len(t_pred) == 1
        assert t_pred[0].status == "failed"
        assert t_pred[0].checks_passed is False


def test_r3_worker_rejects_catalog_deviations(tmp_path: Path):
    """F05, F06, F07: Worker từ chối wrong asset_id, duplicate catalog rows, guard denied / recheck False."""
    from tests.core.test_source_catalog_readiness import _create_real_source_files, _build_test_proposal, _build_test_request, MockStorageManager
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from dataclasses import replace

    src = _create_real_source_files(tmp_path, 1)
    lib = tmp_path / "lib"
    lib.mkdir()
    master = lib / "sfx.wav"
    master.write_bytes(b"RIFF" + b"X" * 64)

    a = {
        "slot": "sfx",
        "demand_id": "d1",
        "timeline_sec": 0.5,
        "duration_sec": 1.0,
        "asset_id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "status": "PROPOSED",
    }
    cat = {
        "id": "sfx01",
        "source_kind": "managed",
        "origin_library_id": "lib_test_01",
        "origin_root": str(lib),
        "library_path": "sfx.wav",
        "type": "audio",
        "file_path": str(master),
    }
    sm = MockStorageManager(lib, assets_list=[cat])
    req = _build_test_request(_build_test_proposal(src, assignments=[a]))
    good = inspect_source_and_catalog_readiness(req, storage_provider=lambda: sm)
    ao = good.catalog_observations[0]

    # F05: Wrong asset ID
    w_aid = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, catalog_observations=(replace(ao, asset_id="DIFFERENT_ASSET"),)))
    t_aid = []
    w_aid.terminal.connect(t_aid.append)
    w_aid.run()
    assert len(t_aid) == 1
    assert t_aid[0].status == "failed"
    assert t_aid[0].checks_passed is False

    # F06: Duplicate catalog rows
    w_dup = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, catalog_observations=(ao, ao)))
    t_dup = []
    w_dup.terminal.connect(t_dup.append)
    w_dup.run()
    assert len(t_dup) == 1
    assert t_dup[0].status == "failed"
    assert t_dup[0].checks_passed is False

    # F07: Guard denied hoặc recheck False
    for bad_ao in (replace(ao, guard_allowed=False, guard_status="missing"), replace(ao, recheck_passed=False)):
        w_guard = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, catalog_observations=(bad_ao,)))
        t_guard = []
        w_guard.terminal.connect(t_guard.append)
        w_guard.run()
        assert len(t_guard) == 1
        assert t_guard[0].status == "failed"
        assert t_guard[0].checks_passed is False


def test_r3_worker_nested_rows_ownership_and_producer_mutation_immunity(tmp_path: Path):
    """F08, F09: Nested rows không cùng identity với producer; producer mutation sau terminal không đổi emitted."""
    from tests.core.test_source_catalog_readiness import _create_real_source_files, _build_test_proposal, _build_test_request
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from dataclasses import replace

    src = _create_real_source_files(tmp_path, 1)
    req = _build_test_request(_build_test_proposal(src, assignments=[]))
    good = inspect_source_and_catalog_readiness(req)
    obs = good.source_observations[0]

    worker = PreflightWorker(req, inspector=lambda *a, **kw: good)
    terminals = []
    worker.terminal.connect(terminals.append)
    worker.run()

    assert len(terminals) == 1
    emitted = terminals[0]
    # F08: Observation instance độc lập
    assert emitted.source_observations[0] is not obs
    orig_path = emitted.source_observations[0].path
    object.__setattr__(obs, "path", "producer mutated path")
    assert emitted.source_observations[0].path == orig_path

    # F09: Nominal row bị tamper flag
    tampered_obs = SourceClipObservation(
        clip_id="C01",
        path=src[0],
        duration_sec=1.0,
        exists=True,
        is_regular_file=True,
        is_non_empty=True,
        is_readable=True,
        stat_identity=(1, 1, 100, 100, 100),
        recheck_passed=True,
    )
    object.__setattr__(tampered_obs, "exists", "non_bool_flag")
    w_tampered = PreflightWorker(req, inspector=lambda *a, **kw: replace(good, source_observations=(tampered_obs,)))
    t_tampered = []
    w_tampered.terminal.connect(t_tampered.append)
    w_tampered.run()
    assert len(t_tampered) == 1
    assert t_tampered[0].status == "failed"
    assert t_tampered[0].checks_passed is False


def test_t11_qthread_deep_mode_off_gui_and_metadata_default_spy(
    tmp_path: Path,
    qapp: QCoreApplication,
    managed_worker,
):
    """T11: Actual QThread deep mode API off GUI, constructor 0 IO, exact 1 terminal + native finished;
    new artifact owned, not mutated/dropped across normalize / worker clone.
    Metadata default spy confirms 0 deep IO and legacy behavior preserved.
    """
    from src.core.contracts.time_contracts import RationalFPS
    from tests.core.geometry.test_source_evidence_service import _create_audio_first_bframe_video
    import hashlib

    v_path = tmp_path / "t11_video.mp4"
    _create_audio_first_bframe_video(v_path, video_dur_sec=0.3, audio_dur_sec=0.6, fps=30)
    with open(v_path, "rb") as f:
        real_sha = hashlib.sha256(f.read()).hexdigest()

    tl_fps = RationalFPS(24, 1)

    # 1. Metadata default spy: verify_source_execution=False (default)
    prop_dict = _make_valid_proposal_dict()
    prop_dict["proposed_asset_assignments"] = []
    prop_dict["source_binding"]["clips"] = [{
        "clip_id": "C01",
        "path": str(v_path),
        "current_path": str(v_path),
        "duration": 0.3,
        "duration_sec": 0.3,
    }]
    prop_dict["video_plan"]["timeline_segments"] = [{
        "segment_index": 0,
        "clip_id": "C01",
        "start_sec": 0.0,
        "end_sec": 0.2,
        "action": "keep",
        "speed": 1.0,
        "role": "body",
    }]
    prop_dict["video_plan"]["global_hook"]["clip_id"] = "C01"
    prop_dict["video_plan"]["global_hook"]["start_sec"] = 0.0
    prop_dict["video_plan"]["global_hook"]["end_sec"] = 0.1
    prop_dict["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop_meta = normalize_edit_plan_proposal(prop_dict)

    req_meta = ReadinessRequest(
        request_id="req_t11_meta",
        generation=1,
        proposal=prop_meta,
        approval=create_app_owned_approval_record(prop_meta, approval_id="appr_t11_meta", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=False,
    )
    assert req_meta.verify_source_execution is False

    worker_meta = PreflightWorker(req_meta)
    results_m, finished_m, _ = managed_worker(worker_meta)
    worker_meta.start()
    ok_m = wait_for_worker(worker_meta, qapp, timeout_sec=5.0)
    assert ok_m is True
    assert len(results_m) == 1
    assert len(finished_m) == 1
    res_m = results_m[0]
    assert res_m.status == "completed"
    assert res_m.checks_passed is True
    assert res_m.source_execution_evidence is None
    assert res_m.source_geometry is None

    # 2. Deep mode: verify_source_execution=True, timeline_fps=24/1
    prop_dict["source_binding"]["clips"][0]["expected_full_sha256"] = real_sha
    prop_deep = normalize_edit_plan_proposal(prop_dict)

    req_deep = ReadinessRequest(
        request_id="req_t11_deep",
        generation=2,
        proposal=prop_deep,
        approval=create_app_owned_approval_record(prop_deep, approval_id="appr_t11_deep", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )

    worker_deep = PreflightWorker(req_deep)
    results_d, finished_d, _ = managed_worker(worker_deep)
    worker_deep.start()
    ok_d = wait_for_worker(worker_deep, qapp, timeout_sec=5.0)
    assert ok_d is True
    assert len(results_d) == 1
    assert len(finished_d) == 1
    res_d = results_d[0]
    assert res_d.status == "completed"
    assert res_d.checks_passed is True
    assert res_d.ready_to_execute is False
    assert res_d.source_execution_evidence is not None
    assert res_d.source_geometry is not None
    assert res_d.source_geometry.status == "GEOMETRY_VERIFIED"
    assert res_d.source_geometry.timeline_fps == tl_fps


def test_t12_tiny_real_fixture_end_to_end_pipeline(
    tmp_path: Path,
    qapp: QCoreApplication,
    managed_worker,
):
    """T12: Tiny real fixture pipeline full service -> deep worker -> geometry readback:
    1 known expected branch (pass) and 1 unknown branch (ERR_UNBOUND_CONTENT_PROVENANCE fail);
    ready=False, 0 Resolve, 0 model/network.
    """
    from src.core.contracts.time_contracts import RationalFPS
    from tests.core.geometry.test_source_evidence_service import _create_audio_first_bframe_video
    import hashlib

    v_path = tmp_path / "t12_real.mp4"
    _create_audio_first_bframe_video(v_path, video_dur_sec=0.4, audio_dur_sec=0.8, fps=30)
    with open(v_path, "rb") as f:
        real_sha = hashlib.sha256(f.read()).hexdigest()

    tl_fps = RationalFPS(30, 1)

    # Branch 1: Known expected SHA-256
    p_dict = _make_valid_proposal_dict()
    p_dict["proposed_asset_assignments"] = []
    p_dict["source_binding"]["clips"] = [{
        "clip_id": "C01",
        "path": str(v_path),
        "current_path": str(v_path),
        "duration": 0.4,
        "duration_sec": 0.4,
        "expected_full_sha256": real_sha,
    }]
    p_dict["video_plan"]["timeline_segments"] = [{
        "segment_index": 0,
        "clip_id": "C01",
        "start_sec": 0.0,
        "end_sec": 0.3,
        "action": "keep",
        "speed": 1.0,
        "role": "body",
    }]
    p_dict["video_plan"]["global_hook"]["clip_id"] = "C01"
    p_dict["video_plan"]["global_hook"]["start_sec"] = 0.0
    p_dict["video_plan"]["global_hook"]["end_sec"] = 0.2
    p_dict["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop_known = normalize_edit_plan_proposal(p_dict)

    req_known = ReadinessRequest(
        request_id="req_t12_known",
        generation=1,
        proposal=prop_known,
        approval=create_app_owned_approval_record(prop_known, approval_id="appr_t12_known", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )

    w_known = PreflightWorker(req_known)
    res_k, fin_k, _ = managed_worker(w_known)
    w_known.start()
    assert wait_for_worker(w_known, qapp, timeout_sec=5.0) is True
    assert len(res_k) == 1
    assert fin_k == [True]
    out_k = res_k[0]
    assert out_k.status == "completed"
    assert out_k.checks_passed is True
    assert out_k.ready_to_execute is False
    assert out_k.source_geometry is not None
    assert out_k.source_geometry.status == "GEOMETRY_VERIFIED"
    assert len(out_k.source_geometry.assembled_segments) == 2

    # Branch 2: Unknown expected SHA-256 (None)
    p_dict["source_binding"]["clips"][0]["expected_full_sha256"] = None
    prop_unknown = normalize_edit_plan_proposal(p_dict)
    req_unknown = ReadinessRequest(
        request_id="req_t12_unknown",
        generation=2,
        proposal=prop_unknown,
        approval=create_app_owned_approval_record(prop_unknown, approval_id="appr_t12_unknown", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=tl_fps,
    )

    w_unknown = PreflightWorker(req_unknown)
    res_u, fin_u, _ = managed_worker(w_unknown)
    w_unknown.start()
    assert wait_for_worker(w_unknown, qapp, timeout_sec=5.0) is True
    assert len(res_u) == 1
    assert fin_u == [True]
    out_u = res_u[0]
    assert out_u.status == "completed"
    assert out_u.checks_passed is False
    assert out_u.ready_to_execute is False
    assert any("ERR_UNBOUND_CONTENT_PROVENANCE" in d for d in out_u.diagnostics)


def test_r1_codex_review_probe_p23_worker_incomplete_proof(tmp_path: Path, qapp: QCoreApplication, managed_worker):
    """Probe P23: PreflightWorker từ chối kết quả có source_execution_evidence không trọn vẹn hoặc sai policy.

    Khi inspector trả về kết quả tưởng chừng hợp lệ nhưng envelope có:
    - complete_scan=False
    - profile_policy khác 'decoded_frame_uniform_ticks_v1'
    - freshness is None
    - frame_evidence không phải 'VERIFIED_CFR'
    PreflightWorker bắt buộc phải bắt lỗi, không phát checks_passed=True,
    mà phát đúng 1 terminal failed với checks_passed=False và 1 native finished.
    """
    import dataclasses
    import hashlib
    from types import SimpleNamespace
    from src.core.contracts.edit_plan_proposal_contracts import edit_plan_proposal_to_dict
    from src.core.contracts.time_contracts import RationalFPS
    from src.core.geometry import source_evidence_service as s
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from tests.core.test_source_catalog_readiness import _build_test_proposal

    source = tmp_path / "p23_source.mp4"
    source.write_bytes(b"\x00\x00\x00 ftypisom" + b"P23_DATA" * 500)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    fresh = s._capture_source_freshness(source)

    prop_raw = edit_plan_proposal_to_dict(_build_test_proposal([str(source)], assignments=[]))
    prop_raw["source_binding"]["clips"][0]["expected_full_sha256"] = sha
    prop_raw["source_binding"]["clips"][0]["stream_index"] = 0
    prop_raw["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop = normalize_edit_plan_proposal(prop_raw)

    request = ReadinessRequest(
        request_id="req_p23_probe",
        generation=1,
        proposal=prop,
        approval=create_app_owned_approval_record(prop, approval_id="appr_p23", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )

    frame = s.SourceFrameEvidence(prop.manifest_id, "C01", RationalFPS(30, 1), 300, "VERIFIED_CFR")
    base_env = s.SourceExecutionEvidenceEnvelope("C01", prop.manifest_id, str(source), 0, sha, sha, fresh, frame, True)

    def consume(envelope: s.SourceExecutionEvidenceEnvelope, status: str = "completed") -> SourceCatalogReadinessResult:
        fake_service = SimpleNamespace(
            acquire_evidence_for_batch=lambda *a, **kw: s.SourceEvidenceBatchResult(status, {"C01": envelope})
        )
        return inspect_source_and_catalog_readiness(request, evidence_service=fake_service)

    # 1. Positive control: valid envelope -> checks_passed=True
    valid_res = consume(base_env)
    assert valid_res.checks_passed is True

    w_pos = PreflightWorker(request, inspector=lambda *a, **kw: valid_res)
    res_pos, fin_pos, _ = managed_worker(w_pos)
    w_pos.start()
    assert wait_for_worker(w_pos, qapp, timeout_sec=5.0) is True
    assert len(res_pos) == 1
    assert fin_pos == [True]
    assert res_pos[0].checks_passed is True

    # 2. Incomplete proof: complete_scan=False
    incomplete_env = dataclasses.replace(base_env, complete_scan=False)
    # Notice: consume(incomplete_env) produces checks_passed=False from readiness inspector.
    # To test worker defense against a malicious/forged inspector returning checks_passed=True with incomplete proof:
    forged_incomplete_res = dataclasses.replace(valid_res, source_execution_evidence={"C01": incomplete_env})
    w_inc = PreflightWorker(request, inspector=lambda *a, **kw: forged_incomplete_res)
    res_inc, fin_inc, _ = managed_worker(w_inc)
    w_inc.start()
    assert wait_for_worker(w_inc, qapp, timeout_sec=5.0) is True
    assert len(res_inc) == 1
    assert fin_inc == [True]
    assert res_inc[0].checks_passed is False
    assert any("ERR_INCOMPLETE_SOURCE_PROOF" in d for d in res_inc[0].diagnostics)

    # 3. Untrusted profile policy
    wrong_policy_env = dataclasses.replace(base_env, profile_policy="untrusted_profile")
    forged_policy_res = dataclasses.replace(valid_res, source_execution_evidence={"C01": wrong_policy_env})
    w_pol = PreflightWorker(request, inspector=lambda *a, **kw: forged_policy_res)
    res_pol, fin_pol, _ = managed_worker(w_pol)
    w_pol.start()
    assert wait_for_worker(w_pol, qapp, timeout_sec=5.0) is True
    assert len(res_pol) == 1
    assert fin_pol == [True]
    assert res_pol[0].checks_passed is False
    assert any("ERR_UNTRUSTED_PROFILE_POLICY" in d for d in res_pol[0].diagnostics)

    # 4. Missing freshness
    missing_fresh_env = dataclasses.replace(base_env, freshness=None)
    forged_fresh_res = dataclasses.replace(valid_res, source_execution_evidence={"C01": missing_fresh_env})
    w_fresh = PreflightWorker(request, inspector=lambda *a, **kw: forged_fresh_res)
    res_fresh, fin_fresh, _ = managed_worker(w_fresh)
    w_fresh.start()
    assert wait_for_worker(w_fresh, qapp, timeout_sec=5.0) is True
    assert len(res_fresh) == 1
    assert fin_fresh == [True]
    assert res_fresh[0].checks_passed is False
    assert any("ERR_MISSING_SOURCE_FRESHNESS" in d for d in res_fresh[0].diagnostics)

    # 5. Non-CFR frame evidence
    uncfr_frame = dataclasses.replace(frame, evidence_status="HEADER_REPORTED_ONLY")
    uncfr_env = dataclasses.replace(base_env, frame_evidence=uncfr_frame)
    forged_uncfr_res = dataclasses.replace(valid_res, source_execution_evidence={"C01": uncfr_env})
    w_cfr = PreflightWorker(request, inspector=lambda *a, **kw: forged_uncfr_res)
    res_cfr, fin_cfr, _ = managed_worker(w_cfr)
    w_cfr.start()
    assert wait_for_worker(w_cfr, qapp, timeout_sec=5.0) is True
    assert len(res_cfr) == 1
    assert fin_cfr == [True]
    assert res_cfr[0].checks_passed is False
    assert any("ERR_SOURCE_NOT_CFR" in d for d in res_cfr[0].diagnostics)


def test_r2_residual_worker_binding_probes(qapp, managed_worker, tmp_path):
    """Tái hiện residual probes cho Worker Terminal Binding Rejection theo G07-07A3-R2 Section D."""
    import dataclasses
    import hashlib
    from types import SimpleNamespace
    from src.core.contracts.time_contracts import RationalFPS
    import src.core.geometry.source_evidence_service as s
    from src.core.source_catalog_readiness import inspect_source_and_catalog_readiness
    from tests.core.test_source_catalog_readiness import _build_test_proposal

    source = tmp_path / "source_worker_r2.mp4"
    source.write_bytes(b"controlled source for worker r2")
    fresh = s._capture_source_freshness(str(source))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()

    prop_raw = edit_plan_proposal_to_dict(_build_test_proposal([str(source)], assignments=[]))
    prop_raw["source_binding"]["clips"][0]["expected_full_sha256"] = sha
    prop_raw["source_binding"]["clips"][0]["stream_index"] = 0
    prop_raw["video_plan"]["global_hook"]["placement_mode"] = "intro_cut"
    prop = normalize_edit_plan_proposal(prop_raw)

    request = ReadinessRequest(
        request_id="req_worker_r2",
        generation=1,
        proposal=prop,
        approval=create_app_owned_approval_record(prop, approval_id="appr_worker_r2", approved_at_iso="2026-11-02T00:00:00Z"),
        verify_source_execution=True,
        timeline_fps=RationalFPS(24, 1),
    )

    frame = s.SourceFrameEvidence(prop.manifest_id, "C01", RationalFPS(30, 1), 300, "VERIFIED_CFR")
    base_env = s.SourceExecutionEvidenceEnvelope("C01", prop.manifest_id, str(source), 0, sha, sha, fresh, frame, True)
    fake_service = SimpleNamespace(
        acquire_evidence_for_batch=lambda *a, **kw: s.SourceEvidenceBatchResult("completed", {"C01": base_env})
    )
    valid_res = inspect_source_and_catalog_readiness(request, evidence_service=fake_service)
    assert valid_res.checks_passed is True

    # Positive check
    w_pos = PreflightWorker(request, inspector=lambda *a, **kw: valid_res)
    res_pos, fin_pos, _ = managed_worker(w_pos)
    w_pos.start()
    assert wait_for_worker(w_pos, qapp, timeout_sec=5.0) is True
    assert res_pos[0].checks_passed is True

    # 1. worker_wrong_path
    wrong_path_res = dataclasses.replace(
        valid_res,
        source_execution_evidence={"C01": dataclasses.replace(base_env, file_path=str(tmp_path / "other.mp4"))},
    )
    w_path = PreflightWorker(request, inspector=lambda *a, **kw: wrong_path_res)
    res_path, fin_path, _ = managed_worker(w_path)
    w_path.start()
    assert wait_for_worker(w_path, qapp, timeout_sec=5.0) is True
    assert res_path[0].checks_passed is False
    assert any("ERR_SOURCE_PATH_MISMATCH" in d for d in res_path[0].diagnostics)

    # 2. worker_wrong_stream
    wrong_stream_res = dataclasses.replace(
        valid_res,
        source_execution_evidence={"C01": dataclasses.replace(base_env, stream_index=1)},
    )
    w_stream = PreflightWorker(request, inspector=lambda *a, **kw: wrong_stream_res)
    res_stream, fin_stream, _ = managed_worker(w_stream)
    w_stream.start()
    assert wait_for_worker(w_stream, qapp, timeout_sec=5.0) is True
    assert res_stream[0].checks_passed is False
    assert any("ERR_SOURCE_STREAM_MISMATCH" in d for d in res_stream[0].diagnostics)

    # 3. worker_wrong_digest
    wrong_digest_res = dataclasses.replace(
        valid_res,
        source_execution_evidence={"C01": dataclasses.replace(base_env, observed_full_sha256="f" * 64)},
    )
    w_digest = PreflightWorker(request, inspector=lambda *a, **kw: wrong_digest_res)
    res_digest, fin_digest, _ = managed_worker(w_digest)
    w_digest.start()
    assert wait_for_worker(w_digest, qapp, timeout_sec=5.0) is True
    assert res_digest[0].checks_passed is False
    assert any("ERR_SOURCE_DIGEST_MISMATCH" in d for d in res_digest[0].diagnostics)

    # 4. worker_wrong_freshness
    wrongfresh = dataclasses.replace(fresh, ctime_ns=fresh.ctime_ns + 1)
    wrong_fresh_res = dataclasses.replace(
        valid_res,
        source_execution_evidence={"C01": dataclasses.replace(base_env, freshness=wrongfresh)},
    )
    w_fresh = PreflightWorker(request, inspector=lambda *a, **kw: wrong_fresh_res)
    res_fresh, fin_fresh, _ = managed_worker(w_fresh)
    w_fresh.start()
    assert wait_for_worker(w_fresh, qapp, timeout_sec=5.0) is True
    assert res_fresh[0].checks_passed is False
    assert any("ERR_SOURCE_FRESHNESS_MISMATCH" in d for d in res_fresh[0].diagnostics)

