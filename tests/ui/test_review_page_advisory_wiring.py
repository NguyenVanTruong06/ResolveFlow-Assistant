"""
tests/ui/test_review_page_advisory_wiring.py
=============================================================================
Finite Unit Test Suite cho ReviewPage Advisory Wiring (G06-06B3-I2-P2):
- Bảng cố vấn hiển thị đọc-chỉ-đọc, đúng trường DTO, điểm NN/100, đầy đủ phân hoạch counters.
- Shared matching context builder bóc tách cả B-roll lẫn SFX, full SHA-256 revision, chặn trần 512 KiB.
- Kích hoạt tra cứu: nút chuyển trạng thái bận, 0 can thiệp biến đổi kịch bản gốc.
- Không có yêu cầu hợp lệ -> 0 cuộc gọi tới storage/reader, hoàn tất thành công với counters chuẩn.
- Chống stale terminal: thế hệ cũ, kịch bản thay đổi giữa chừng, lệch ngữ cảnh kho -> từ chối xuất kết quả.
- Bắt lỗi kho / provider error: hiển thị banner lỗi tiếng Việt có kiểm soát, không bật dialog popup.
- Đổi kịch bản hợp lệ -> gắn nhãn 'Kết quả cũ — bấm Gợi ý để cập nhật', không dùng summary xanh của kịch bản cũ.
- Lệch Manifest ID -> từ chối kịch bản, hủy matching đang chạy, vô hiệu hóa nút bấm.
- Tích hợp vòng đời DirectorWindow close-pending: pending chờ physical finished và phát analysis_workers_idle.
=============================================================================
"""

import copy
import json
import time
from typing import Any, Dict, List, Optional
import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetMatchingSidecar,
    DemandMatchEntry,
)
from src.core.offline_asset_matcher import (
    OfflineAssetCandidate,
    OfflineAssetMatchResult,
)
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.core.storage_manager import StorageOfflineError
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.widgets.asset_advisory_drawer import (
    AssetAdvisoryDrawer,
    AssetAdvisoryModel,
)
from src.ui.director.workers.asset_matching_worker import (
    AssetMatchingOutcome,
    AssetMatchingWorker,
    MatchingStorageContext,
)


@pytest.fixture(scope="module")
def qapp():
    """Đảm bảo có QApplication cho toàn bộ các bài test giao diện."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def wait_for_condition(condition_fn, qapp, timeout_sec: float = 3.0) -> bool:
    """Đợi điều kiện đạt được qua vòng lặp monotonic deadline và processEvents."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition_fn():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return bool(condition_fn())


@pytest.fixture
def managed_review_page(qapp):
    """Fixture khởi tạo ReviewPage và đảm bảo dọn dẹp sạch sẽ các worker luồng nền sau test."""
    page = ReviewPage()
    page._headless_mode = True

    yield page

    # Teardown: Hủy và đợi mọi worker kết thúc
    page.shutdown_analysis_workers()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not page.is_analysis_idle():
        qapp.processEvents()
        time.sleep(0.02)
    assert page.is_analysis_idle(), "ReviewPage worker bị leak hoặc vẫn đang chạy sau test!"
    page.deleteLater()
    qapp.processEvents()


# =============================================================================
# Ca 1: Model & Drawer Rendering, DTO Fields, Counters, Điểm NN/100
# =============================================================================

def test_drawer_model_rendering_and_counters(qapp):
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "laughing cat", "timeline_sec": 12.5, "duration_sec": 3.0}
        ],
        "sfx_inserts": [
            {"asset_type": "sfx", "description": ""}  # empty description -> empty_query
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_test_render")
    assert len(batch.demands) == 2

    # Tạo ứng viên và kết quả match cho demand 0 (broll)
    cand1 = OfflineAssetCandidate(
        asset_id="cat_01",
        name="Laughing Cat Meme",
        category="memes",
        subcategory="humor",
        source_kind="managed",
        availability="online",
        match_score=85,
        matched_signals=("name_token",),
        duration_sec=3.0,
        pack_id="p1",
        pack_name="Viral Pack",
    )
    res1 = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text=batch.requests[0].query_text,
        is_matched=True,
        candidates=(cand1,),
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res1,))

    drawer = AssetAdvisoryDrawer()
    ctx = MatchingStorageContext(root="/media/library", library_id="lib_alpha")
    drawer.set_sidecar(sidecar, storage_context=ctx)

    model = drawer._model
    assert model.rowCount() == 2
    assert model.columnCount() == 7

    # Cột 0: Vị trí / Cảnh
    idx0 = model.index(0, 0)
    assert model.data(idx0) == "B-roll #1 @ 12.5s"
    idx1_0 = model.index(1, 0)
    assert model.data(idx1_0) == "SFX #1"

    # Cột 1: Phân loại
    assert model.data(model.index(0, 1)) == "memes"
    assert model.data(model.index(1, 1)) == "sfx"

    # Cột 2: Từ khóa
    assert model.data(model.index(0, 2)) == "laughing cat"
    assert model.data(model.index(1, 2)) == "(Rỗng)"

    # Cột 3: Thời lượng
    assert model.data(model.index(0, 3)) == "3.0s"
    assert model.data(model.index(1, 3)) == "-"

    # Cột 4: Trạng thái
    assert model.data(model.index(0, 4)) == "✓ Khớp"
    assert model.data(model.index(1, 4)) == "∅ Từ khóa rỗng"

    # Cột 5: Ứng viên hàng đầu
    assert "Laughing Cat Meme" in model.data(model.index(0, 5))
    assert "[Viral Pack]" in model.data(model.index(0, 5))
    assert model.data(model.index(1, 5)) == "(empty_query)"

    # Cột 6: Điểm phù hợp (dạng NN/100, cấm confidence%)
    score_str = model.data(model.index(0, 6))
    assert score_str == "85/100"
    assert "%" not in score_str

    # Thống kê và thông tin kho
    assert "Tổng: 2 mục" in drawer.lbl_summary.text()
    assert "Khớp: 1" in drawer.lbl_summary.text()
    assert "Từ khóa rỗng: 1" in drawer.lbl_summary.text()
    assert "lib_alpha" in drawer.lbl_storage_info.text()

    # Read-only flag
    flags = model.flags(idx0)
    assert flags & Qt.ItemFlag.ItemIsEnabled
    assert not (flags & Qt.ItemFlag.ItemIsEditable)

    drawer.deleteLater()


# =============================================================================
# Ca 2: Context Builder: B-roll + SFX, Full SHA-256, Chặn Trần 512 KiB
# =============================================================================

def test_context_builder_broll_and_sfx_and_oversize(managed_review_page):
    page = managed_review_page

    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "laughing cat", "timeline_sec": 5.0}
        ],
        "sfx_inserts": [
            {"asset_type": "sfx", "description": "whoosh transition", "timeline_sec": 5.1}
        ],
        "ignored_extra_field": "do_not_serialize_me_random_large_string" * 100,
    }

    batch, rev, canonical_bytes = page._build_matching_context(plan)
    assert isinstance(batch, AssetDemandBatch)
    assert len(batch.demands) == 2
    assert rev.startswith("rev_")
    assert len(rev) == 68  # 'rev_' (4) + 64 hex SHA-256 = 68
    assert b"ignored_extra_field" not in canonical_bytes
    assert len(canonical_bytes) <= 524288

    # Đổi thứ tự demand -> thay đổi revision
    plan_swapped = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "different cat", "timeline_sec": 5.0}
        ],
        "sfx_inserts": [
            {"asset_type": "sfx", "description": "whoosh transition", "timeline_sec": 5.1}
        ],
    }
    _, rev_swapped, _ = page._build_matching_context(plan_swapped)
    assert rev != rev_swapped

    # Chặn trần dung lượng MAX_MATCH_CONTEXT_BYTES (512 KiB)
    plan_oversize = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "x" * 600000}
        ]
    }
    with pytest.raises(ValueError, match="vượt trần"):
        page._build_matching_context(plan_oversize)


# =============================================================================
# Ca 3: Happy Path Dispatch & Terminal: 0 Mutation, Khớp Cả 2 Domains
# =============================================================================

def test_happy_path_dispatch_and_completed_terminal(qapp, managed_review_page, tmp_path, monkeypatch):
    page = managed_review_page

    # Chuẩn bị tệp cấu hình kho tạm thật
    cfg_file = tmp_path / "storage.json"
    cfg_file.write_text(json.dumps({"root": str(tmp_path), "library_id": "lib_happy"}), encoding="utf-8")

    class MockStorage:
        config_file = cfg_file
        _config = {"root": str(tmp_path), "library_id": "lib_happy"}

        def assets(self, *, raise_on_error: bool = False):
            return [
                {
                    "id": "asset_cat_01",
                    "schema_version": 1,
                    "name": "Cat Meme Viral",
                    "category": "memes",
                    "subcategory": "cat",
                    "file_path": str(tmp_path / "cat.mp4"),
                    "relative_path": "memes/cat.mp4",
                    "source_kind": "managed",
                    "pack_id": "p_cat",
                    "pack_name": "Cat Pack",
                    "library_path": "memes/cat.mp4",
                    "availability": "online",
                    "size_bytes": 1024,
                },
                {
                    "id": "asset_sfx_01",
                    "schema_version": 1,
                    "name": "Whoosh Transition",
                    "category": "sfx",
                    "subcategory": "transitions",
                    "file_path": str(tmp_path / "whoosh.wav"),
                    "relative_path": "sfx/whoosh.wav",
                    "source_kind": "managed",
                    "pack_id": "p_sfx",
                    "pack_name": "SFX Pack",
                    "library_path": "sfx/whoosh.wav",
                    "availability": "online",
                    "size_bytes": 512,
                },
            ]

    raw_plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "Cat Meme Viral", "timeline_sec": 2.0}
        ],
        "sfx_inserts": [
            {"asset_type": "sfx", "description": "Whoosh Transition", "timeline_sec": 2.5}
        ],
    }
    raw_plan_copy = copy.deepcopy(raw_plan)

    page._video_plan_data = raw_plan
    page._on_video_plan_changed()

    assert page.btn_match_assets.isEnabled() is True

    # Monkeypatch StorageManager constructor để trả về MockStorage
    import src.core.storage_manager
    monkeypatch.setattr(src.core.storage_manager, "StorageManager", MockStorage)

    # Click nút Gợi ý tài nguyên
    page.btn_match_assets.click()

    # Nút bấm lập tức bị khóa (physical busy)
    assert page.btn_match_assets.isEnabled() is False
    assert page.is_matching_busy() is True

    # Chờ worker kết thúc
    assert wait_for_condition(lambda: not page.is_matching_busy(), qapp, timeout_sec=3.0) is True

    # Nút bấm mở lại sau khi registry rỗng
    assert page.btn_match_assets.isEnabled() is True
    assert "✓ Đã gợi ý (2/2 mục)" in page.lbl_matching_status.text()

    # Drawer cập nhật kết quả và hiển thị kho
    assert page.asset_advisory_drawer._model.rowCount() == 2
    assert "lib_happy" in page.asset_advisory_drawer.lbl_storage_info.text()

    # Đảm bảo kịch bản gốc nguyên vẹn từng byte (Zero Mutation)
    assert page._video_plan_data == raw_plan_copy
    assert "asset_file" not in page._video_plan_data["broll_inserts"][0]


# =============================================================================
# Ca 4: Empty Demands: 0 Storage Calls & 0 Context Reader Calls
# =============================================================================

def test_empty_or_noneligible_demands_zero_storage_call(qapp, managed_review_page, monkeypatch):
    page = managed_review_page

    factory_called = False
    def failing_factory():
        nonlocal factory_called
        factory_called = True
        raise RuntimeError("Không được phép gọi factory!")

    import src.core.storage_manager
    monkeypatch.setattr(src.core.storage_manager, "StorageManager", failing_factory)

    raw_plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": ""}  # empty description -> empty_query
        ]
    }
    page._video_plan_data = raw_plan
    page._on_video_plan_changed()

    page.btn_match_assets.click()

    assert wait_for_condition(lambda: not page.is_matching_busy(), qapp, timeout_sec=3.0) is True
    assert factory_called is False
    assert "✓ Đã gợi ý (0/1 mục)" in page.lbl_matching_status.text()
    assert page.asset_advisory_drawer._model.rowCount() == 1


# =============================================================================
# Ca 5: Chống Stale Terminal (Thế Hệ Cũ Đến Muộn Bị Bỏ Qua)
# =============================================================================

def test_stale_generation_and_revision_denied(qapp, managed_review_page):
    page = managed_review_page

    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_stale_1")
    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 2
    req_id = "match_req_2_abcd1234"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 2,
        "plan_revision": "rev_stale_2",
        "canonical_bytes": b"{}",
        "batch": batch,
        "worker": dummy,
    }
    page._video_plan_data = plan
    page._matching_workers_registry[req_id] = dummy

    stale_outcome = AssetMatchingOutcome(
        status="completed",
        generation=1,  # Lệch generation so với active (2)
        plan_revision="rev_stale_1",
        sidecar=sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, stale_outcome)
        # Đảm bảo outcome bị từ chối, last_good_sidecar không bị gán
        assert page._last_good_matching_sidecar is None
        assert page.asset_advisory_drawer._model.rowCount() == 0
    finally:
        page._matching_workers_registry.clear()


# =============================================================================
# Ca 6: Chống Stale Khi Kịch Bản Thay Đổi Giữa Chừng
# =============================================================================

def test_stale_plan_change_during_execution_denied(qapp, managed_review_page):
    page = managed_review_page

    plan_a = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch_a, rev_a, bytes_a = page._build_matching_context(plan_a)
    res_a = OfflineAssetMatchResult(
        request_id=batch_a.requests[0].request_id,
        category=batch_a.requests[0].category,
        query_text=batch_a.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar_a = PlanDemandAdapter.assemble(batch_a, (res_a,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 5
    req_id = "match_req_5_xyz"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 5,
        "plan_revision": rev_a,
        "canonical_bytes": bytes_a,
        "batch": batch_a,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy

    # Kịch bản bị thay đổi trên GUI trong lúc worker đang chạy
    plan_b = {"broll_inserts": [{"asset_type": "meme", "description": "clip B MODIFIED"}]}
    page._video_plan_data = plan_b

    outcome_a = AssetMatchingOutcome(
        status="completed",
        generation=5,
        plan_revision=rev_a,
        sidecar=sidecar_a,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome_a)
        # Recompute phát hiện plan hiện tại không khớp -> từ chối publish
        assert page._last_good_matching_sidecar is None
        assert page.asset_advisory_drawer._model.rowCount() == 0
    finally:
        page._matching_workers_registry.clear()


# =============================================================================
# Ca 7: Từ Chối Outcome Completed Khi Thiếu Storage Context Cho Requests Hợp Lệ
# =============================================================================

def test_storage_context_missing_on_completed_nonempty_denied(qapp, managed_review_page):
    page = managed_review_page

    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)
    assert len(batch.requests) == 1
    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 6
    req_id = "match_req_6_nocontext"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 6,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    # Outcome hoàn tất nhưng thiếu storage_context (storage_context=None)
    malformed_outcome = AssetMatchingOutcome(
        status="completed",
        generation=6,
        plan_revision=rev,
        sidecar=sidecar,
        storage_context=None,
    )

    try:
        page._on_matching_terminal(dummy, req_id, malformed_outcome)
        # Bị từ chối và báo lỗi có kiểm soát
        assert page._last_good_matching_sidecar is None
        assert "Thiếu hoặc sai định dạng ngữ cảnh kho media" in page.lbl_matching_status.text() or "Ngữ cảnh kho media không hợp lệ" in page.lbl_matching_status.text()
        assert not page.asset_advisory_drawer.lbl_error_banner.isHidden()
    finally:
        page._matching_workers_registry.clear()


# =============================================================================
# Ca 8: Bắt Lỗi Kho / Provider Error: Hiển Thị Banner Non-Modal (0 Popup)
# =============================================================================

def test_provider_error_non_modal_banner(qapp, managed_review_page, tmp_path, monkeypatch):
    page = managed_review_page

    cfg_file = tmp_path / "storage.json"
    cfg_file.write_text(json.dumps({"root": str(tmp_path), "library_id": "lib_err"}), encoding="utf-8")

    class FailingStorage:
        config_file = cfg_file
        _config = {"root": str(tmp_path), "library_id": "lib_err"}

        def assets(self, *, raise_on_error: bool = False):
            raise StorageOfflineError("Ổ đĩa kho media đã bị ngắt kết nối.")

    import src.core.storage_manager
    monkeypatch.setattr(src.core.storage_manager, "StorageManager", FailingStorage)

    raw_plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    page._video_plan_data = raw_plan
    page._on_video_plan_changed()

    page.btn_match_assets.click()

    assert wait_for_condition(lambda: not page.is_matching_busy(), qapp, timeout_sec=3.0) is True

    # Lỗi hiển thị trên banner drawer và label, không bật modal popup làm đơ GUI
    assert "Ổ đĩa kho media đã bị ngắt kết nối" in page.lbl_matching_status.text()
    assert not page.asset_advisory_drawer.lbl_error_banner.isHidden()
    assert "Ổ đĩa kho media đã bị ngắt kết nối" in page.asset_advisory_drawer.lbl_error_banner.text()


# =============================================================================
# Ca 9: Đổi Kịch Bản Hợp Lệ: Gắn Nhãn Stale (Không Dùng Summary Xanh Kịch Bản Cũ)
# =============================================================================

def test_plan_change_marks_last_good_as_stale(qapp, managed_review_page):
    page = managed_review_page

    plan_old = {"broll_inserts": [{"asset_type": "meme", "description": "cat", "timeline_sec": 1.0}]}
    batch_old = PlanDemandAdapter.extract(plan_old, plan_revision="rev_old")
    res_old = OfflineAssetMatchResult(
        request_id=batch_old.requests[0].request_id,
        category=batch_old.requests[0].category,
        query_text=batch_old.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar_old = PlanDemandAdapter.assemble(batch_old, (res_old,))
    page._last_good_matching_sidecar = sidecar_old
    page.asset_advisory_drawer.set_sidecar(sidecar_old)

    # Nạp kịch bản mới
    new_plan = {"broll_inserts": [{"asset_type": "meme", "description": "dog"}]}
    page._video_plan_data = new_plan
    page._on_video_plan_changed()

    # Nhãn trạng thái chuyển thành cảnh báo stale
    assert page.lbl_matching_status.text() == "Kết quả cũ — bấm Gợi ý để cập nhật"
    assert not page.asset_advisory_drawer.lbl_stale_banner.isHidden()
    assert page.btn_match_assets.isEnabled() is True


# =============================================================================
# Ca 10: Lệch Manifest ID: Từ Chối Kịch Bản & Hủy Matching
# =============================================================================

def test_manifest_mismatch_clears_matching(qapp, managed_review_page):
    page = managed_review_page

    class MockManifest:
        manifest_id = "manifest_project_current_123"

    page._manifest = MockManifest()

    # Dữ liệu kịch bản mang manifest_id khác
    mismatched_data = {
        "type": "video_plan",
        "data": {
            "manifest_id": "manifest_different_999",
            "timeline_segments": [{"title": "Cảnh 1"}],
        },
        "summary": "Kịch bản lệch",
    }

    page._apply_json_data(mismatched_data, "Kịch bản lệch")

    # Kịch bản bị từ chối
    assert page._video_plan_data is None
    assert page.btn_match_assets.isEnabled() is False
    assert page.lbl_matching_status.text() == "✕ Kịch bản bị từ chối"


# =============================================================================
# Ca 11: Shutdown Analysis Workers & Idle Signal Phát Ra Khi Matching Registry Trống
# =============================================================================

def test_shutdown_analysis_workers_and_director_close_pending(qapp, managed_review_page, tmp_path):
    page = managed_review_page

    cfg_file = tmp_path / "storage.json"
    cfg_file.write_text(json.dumps({"root": str(tmp_path), "library_id": "lib_slow"}), encoding="utf-8")

    class SlowStorage:
        config_file = cfg_file
        _config = {"root": str(tmp_path), "library_id": "lib_slow"}

        def assets(self, *, raise_on_error: bool = False):
            time.sleep(0.15)
            return []

    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_slow")

    worker = AssetMatchingWorker(
        batch=batch,
        generation=1,
        storage_factory=SlowStorage,
        storage_context_reader=lambda s: MatchingStorageContext(root=str(tmp_path), library_id="lib_slow"),
        parent=page,
    )
    req_id = "slow_req_1"
    page._matching_workers_registry[req_id] = worker
    worker.finished.connect(lambda: page._on_matching_finished(req_id, worker))

    worker.start()
    assert page.is_analysis_idle() is False

    # Đóng ứng dụng khi worker đang chạy
    is_idle_before = page.shutdown_analysis_workers()
    assert is_idle_before is False  # Vẫn còn worker đang chạy vật lý
    assert worker.isInterruptionRequested() is True

    # Chờ dọn dẹp vật lý hoàn tất
    idle_emitted = False
    def on_idle():
        nonlocal idle_emitted
        idle_emitted = True

    page.analysis_workers_idle.connect(on_idle)

    assert wait_for_condition(lambda: page.is_analysis_idle(), qapp, timeout_sec=3.0) is True
    assert idle_emitted is True


# =============================================================================
# Ca 12: Tích Hợp Đóng Thực DirectorWindow Qua CloseEvent
# =============================================================================

def test_director_window_close_pending_integration(qapp, tmp_path):
    """Kiểm chứng DirectorWindow.closeEvent thật: pending chờ physical finished rồi mới đóng cửa sổ."""
    window = DirectorWindow()
    window.page_review._headless_mode = True
    page_review = window.page_review

    cfg_file = tmp_path / "storage.json"
    cfg_file.write_text(json.dumps({"root": str(tmp_path), "library_id": "lib_win"}), encoding="utf-8")

    class SlowStorage:
        config_file = cfg_file
        _config = {"root": str(tmp_path), "library_id": "lib_win"}

        def assets(self, *, raise_on_error: bool = False):
            time.sleep(0.1)
            return []

    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_win")

    worker = AssetMatchingWorker(
        batch=batch,
        generation=1,
        storage_factory=SlowStorage,
        storage_context_reader=lambda s: MatchingStorageContext(root=str(tmp_path), library_id="lib_win"),
        parent=page_review,
    )
    req_id = "win_req_1"
    page_review._matching_workers_registry[req_id] = worker
    worker.finished.connect(lambda: page_review._on_matching_finished(req_id, worker))

    worker.start()

    # Kích hoạt closeEvent của DirectorWindow khi worker đang chạy
    close_event = QCloseEvent()
    window.closeEvent(close_event)

    # Event bị ignore vì có worker matching đang chạy!
    assert close_event.isAccepted() is False
    assert window._source_close_pending is True

    # Đợi worker kết thúc và window hoàn tất đóng qua _on_sources_idle
    assert wait_for_condition(lambda: window._is_closed is True, qapp, timeout_sec=3.0) is True

    window.deleteLater()
    qapp.processEvents()


# =============================================================================
# Nhóm A: Context Builder SSOT, Fields, Options và Budget Preflight
# =============================================================================

def test_context_builder_asset_file_changes_signature(managed_review_page):
    """Thay đổi asset_file (city.mp4 -> dog.mp4) làm thay đổi query_text và SHA-256 revision."""
    page = managed_review_page
    plan_city = {"broll_inserts": [{"asset_type": "meme", "asset_file": "city.mp4"}]}
    plan_dog = {"broll_inserts": [{"asset_type": "meme", "asset_file": "dog.mp4"}]}

    batch1, rev1, bytes1 = page._build_matching_context(plan_city)
    batch2, rev2, bytes2 = page._build_matching_context(plan_dog)

    assert rev1 != rev2
    assert bytes1 != bytes2
    assert batch1.demands[0].query_text == "city"
    assert batch2.demands[0].query_text == "dog"


def test_context_builder_duration_sec_changes_signature(managed_review_page):
    """Thay đổi duration_sec (2.5 -> 9.0) làm thay đổi desired_duration_sec và SHA-256 revision."""
    page = managed_review_page
    plan_short = {"broll_inserts": [{"asset_type": "meme", "description": "city", "duration_sec": 2.5}]}
    plan_long = {"broll_inserts": [{"asset_type": "meme", "description": "city", "duration_sec": 9.0}]}

    batch1, rev1, bytes1 = page._build_matching_context(plan_short)
    batch2, rev2, bytes2 = page._build_matching_context(plan_long)

    assert rev1 != rev2
    assert bytes1 != bytes2
    assert batch1.demands[0].desired_duration_sec == 2.5
    assert batch2.demands[0].desired_duration_sec == 9.0


def test_context_builder_options_hash_strictly_matches_dispatch(managed_review_page):
    """Tùy chọn options (top_k, allow_offline) được đồng bộ tuyệt đối giữa material hash và dispatched requests."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}

    # Trường hợp 1: Defaults (top_k=5, allow_offline=False)
    batch_def, rev_def, bytes_def = page._build_matching_context(plan)
    decoded_def = json.loads(bytes_def.decode("utf-8"))
    assert decoded_def["options"] == {"allow_offline": False, "top_k": 5}
    assert batch_def.requests[0].top_k == 5
    assert batch_def.requests[0].allow_offline is False

    # Trường hợp 2: Tùy biến (top_k=7, allow_offline=True)
    batch_opt, rev_opt, bytes_opt = page._build_matching_context(plan, top_k=7, allow_offline=True)
    decoded_opt = json.loads(bytes_opt.decode("utf-8"))
    assert decoded_opt["options"] == {"allow_offline": True, "top_k": 7}
    assert batch_opt.requests[0].top_k == 7
    assert batch_opt.requests[0].allow_offline is True
    assert rev_def != rev_opt


def test_context_builder_filename_and_manifest_budget_preflight(managed_review_page):
    """Preflight chặn trần 512 KiB bắt được filename hoặc manifest_id quá lớn trước khi encode."""
    page = managed_review_page

    huge_filename = "a" * 600000 + ".mp4"
    plan_huge_file = {"broll_inserts": [{"asset_type": "meme", "asset_file": huge_filename}]}
    with pytest.raises(ValueError, match="vượt trần"):
        page._build_matching_context(plan_huge_file)

    huge_manifest = "m" * 600000
    plan_huge_manifest = {
        "manifest_id": huge_manifest,
        "broll_inserts": [{"asset_type": "meme", "description": "cat"}]
    }
    with pytest.raises(ValueError, match="vượt trần"):
        page._build_matching_context(plan_huge_manifest)


# =============================================================================
# Nhóm B: Exact Terminal / Pending Authority và Safe Receipt
# =============================================================================

def test_terminal_foreign_sidecar_rejected(qapp, managed_review_page):
    """Sidecar hợp lệ nhưng mang revision ngoại lai ('rev_foreign') bị từ chối công bố."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    batch_foreign = PlanDemandAdapter.extract(plan, plan_revision="rev_foreign")
    res_foreign = OfflineAssetMatchResult(
        request_id=batch_foreign.requests[0].request_id,
        category=batch_foreign.requests[0].category,
        query_text=batch_foreign.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar_foreign = PlanDemandAdapter.assemble(batch_foreign, (res_foreign,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 10
    req_id = "req_foreign_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 10,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    outcome = AssetMatchingOutcome(
        status="completed",
        generation=10,
        plan_revision=rev,
        sidecar=sidecar_foreign,  # Sidecar mang revision 'rev_foreign'
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert page.asset_advisory_drawer._model.rowCount() == 0
        assert "Sidecar thuộc phiên bản ngoại lai" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_untyped_simple_namespace_rejected(qapp, managed_review_page):
    """Outcome không phải AssetMatchingOutcome (SimpleNamespace) bị từ chối, không báo thành công."""
    import types
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 11
    req_id = "req_untyped_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 11,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    duck_outcome = types.SimpleNamespace(
        status="completed",
        generation=11,
        plan_revision=rev,
        sidecar=None,
    )

    try:
        page._on_matching_terminal(dummy, req_id, duck_outcome)
        assert page._last_good_matching_sidecar is None
        assert "Dữ liệu outcome không hợp lệ" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_invalid_storage_context_object_no_exception(qapp, managed_review_page):
    """storage_context=object() không làm bung ngoại lệ ra ngoài Qt slot và bị từ chối an toàn."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 12
    req_id = "req_obj_ctx_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 12,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    bad_ctx_outcome = AssetMatchingOutcome(
        status="completed",
        generation=12,
        plan_revision=rev,
        sidecar=sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )
    object.__setattr__(bad_ctx_outcome, "storage_context", object())

    try:
        # Không được ném AttributeError thoát ra ngoài slot
        page._on_matching_terminal(dummy, req_id, bad_ctx_outcome)
        assert page._last_good_matching_sidecar is None
        assert "Ngữ cảnh kho media không hợp lệ" in page.lbl_matching_status.text() or "sai định dạng" in page.asset_advisory_drawer.lbl_error_banner.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_wrong_worker_instance_ignored(qapp, managed_review_page):
    """Outcome từ worker khác gửi tới bị bỏ qua hoàn toàn, không thay đổi pending hay UI."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    class DummyWorker:
        def cancel(self): pass

    legit_worker = DummyWorker()
    impostor_worker = DummyWorker()

    page._matching_generation = 13
    req_id = "req_impostor_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 13,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "worker": legit_worker,
    }
    page._matching_workers_registry[req_id] = legit_worker
    page._video_plan_data = plan

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))
    outcome = AssetMatchingOutcome(
        status="completed",
        generation=13,
        plan_revision=rev,
        sidecar=sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(impostor_worker, req_id, outcome)
        # Bị từ chối vì worker không khớp; pending context vẫn nguyên vẹn
        assert page._last_good_matching_sidecar is None
        assert page._matching_pending_context is not None
    finally:
        page._matching_workers_registry.clear()


# =============================================================================
# Nhóm C: Physical Busy, Truthful Stale và Plain-Text Labels
# =============================================================================

def test_plan_change_keeps_button_disabled_while_matching_busy(qapp, managed_review_page):
    """Khi đổi kịch bản trong lúc worker cũ đang chạy, nút gợi ý giữ disabled cho tới khi worker cũ kết thúc."""
    page = managed_review_page

    plan_a = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    page._video_plan_data = plan_a

    class MockWorker:
        def cancel(self): pass

    worker = MockWorker()
    req_id = "busy_worker_1"
    page._matching_workers_registry[req_id] = worker
    page.btn_match_assets.setEnabled(False)

    # Đổi plan khi worker vẫn đang có trong registry
    plan_b = {"broll_inserts": [{"asset_type": "meme", "description": "clip B"}]}
    page._video_plan_data = plan_b
    page._on_video_plan_changed()

    # Nút bấm PHẢI giữ Disabled vì matching registry còn 1 entry!
    assert page.btn_match_assets.isEnabled() is False

    # Giả lập worker cũ kết thúc vật lý
    page._on_matching_finished(req_id, worker)

    # Bây giờ registry rỗng và plan B hợp lệ -> Nút bấm tự động mở lại!
    assert page.btn_match_assets.isEnabled() is True


def test_drawer_set_error_retains_stale_banner_on_old_rows(qapp):
    """Drawer.set_error với retain_stale=True không xóa dấu 'Kết quả cũ' nếu đang giữ rows cũ."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat", "duration_sec": 2.0}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_stale_test")
    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    drawer = AssetAdvisoryDrawer()
    drawer.set_sidecar(sidecar, MatchingStorageContext(root="/root", library_id="lib"))
    drawer.set_stale(True)
    assert not drawer.lbl_stale_banner.isHidden()

    # Có lỗi từ job mới xảy ra
    drawer.set_error("Ổ đĩa kho media lỗi", retain_stale=True)
    assert not drawer.lbl_error_banner.isHidden()
    assert not drawer.lbl_stale_banner.isHidden()  # Stale banner PHẢI giữ nguyên!


def test_labels_plain_text_format(managed_review_page):
    """Tất cả nhãn trạng thái và metadata đều cấu hình PlainText format chống injection."""
    page = managed_review_page
    drawer = page.asset_advisory_drawer

    assert page.lbl_matching_status.textFormat() == Qt.TextFormat.PlainText
    assert drawer.lbl_title.textFormat() == Qt.TextFormat.PlainText
    assert drawer.lbl_stale_banner.textFormat() == Qt.TextFormat.PlainText
    assert drawer.lbl_summary.textFormat() == Qt.TextFormat.PlainText
    assert drawer.lbl_storage_info.textFormat() == Qt.TextFormat.PlainText
    assert drawer.lbl_error_banner.textFormat() == Qt.TextFormat.PlainText


# =============================================================================
# Nhóm D: Failed Constructor / Start Rollback
# =============================================================================

def test_constructor_failure_handled_without_escape_and_restores_state(qapp, managed_review_page, monkeypatch):
    """Ngoại lệ trong AssetMatchingWorker constructor không bung ra ngoài slot, rollback request và mở lại nút."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._video_plan_data = plan
    page._on_video_plan_changed()

    def failing_worker_ctor(*args, **kwargs):
        raise RuntimeError("controlled constructor failure")

    import src.ui.director.pages.review_page
    monkeypatch.setattr(src.ui.director.pages.review_page, "AssetMatchingWorker", failing_worker_ctor)

    # Click nút bấm kích hoạt tra cứu
    page.btn_match_assets.click()

    # Ngoại lệ bị bắt gọn gàng: 0 exception leak
    assert len(page._matching_workers_registry) == 0
    assert page._matching_pending_context is None
    assert page.btn_match_assets.isEnabled() is True
    assert "controlled constructor failure" in page.lbl_matching_status.text()
    assert not page.asset_advisory_drawer.lbl_error_banner.isHidden()


# =============================================================================
# G06-06B3-I2-P2-R2 Acceptance Tests (Groups A, B, C)
# =============================================================================

class MockManifestObj:
    def __init__(self, manifest_id: Optional[str]):
        self.manifest_id = manifest_id


def test_manifest_raw_and_active_combinations(managed_review_page):
    """Bao phủ các tổ hợp raw_manifest và active_manifest: trùng khớp, thiếu 1 trong 2, cả 2 None, và mismatch."""
    page = managed_review_page

    # 1. Cả 2 đều có giá trị hợp lệ và trùng nhau -> Positive
    plan_same = {"manifest_id": "mA", "broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._manifest = MockManifestObj("mA")
    batch_same, rev_same, bytes_same = page._build_matching_context(plan_same)
    assert batch_same.manifest_id == "mA"
    mat_same = json.loads(bytes_same.decode("utf-8"))
    assert mat_same["raw_plan_manifest_id"] == "mA"
    assert mat_same["active_manifest_id"] == "mA"
    assert mat_same["effective_manifest_id"] == "mA"

    # 2. Raw thiếu/None, active có giá trị hợp lệ -> Positive, batch lấy active
    plan_no_raw = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._manifest = MockManifestObj("mB")
    batch_active, rev_active, bytes_active = page._build_matching_context(plan_no_raw)
    assert batch_active.manifest_id == "mB"
    mat_active = json.loads(bytes_active.decode("utf-8"))
    assert mat_active["raw_plan_manifest_id"] is None
    assert mat_active["active_manifest_id"] == "mB"
    assert mat_active["effective_manifest_id"] == "mB"

    # 3. Cả 2 đều None -> Positive
    page._manifest = None
    batch_none, rev_none, bytes_none = page._build_matching_context(plan_no_raw)
    assert batch_none.manifest_id is None
    mat_none = json.loads(bytes_none.decode("utf-8"))
    assert mat_none["raw_plan_manifest_id"] is None
    assert mat_none["active_manifest_id"] is None
    assert mat_none["effective_manifest_id"] is None

    # 4. Cả 2 đều có giá trị hợp lệ nhưng lệch nhau (raw='mA', active='mB') -> ValueError kiểm soát
    page._manifest = MockManifestObj("mB")
    with pytest.raises(ValueError, match="Lệch định danh manifest"):
        page._build_matching_context(plan_same)

    # 5. Active thay đổi mA -> mB làm đổi revision ngữ cảnh
    page._manifest = MockManifestObj("mA")
    _, rev1, _ = page._build_matching_context(plan_no_raw)
    page._manifest = MockManifestObj("mB")
    _, rev2, _ = page._build_matching_context(plan_no_raw)
    assert rev1 != rev2

    # 6. Active thay đổi mA -> None làm đổi revision ngữ cảnh
    page._manifest = None
    _, rev3, _ = page._build_matching_context(plan_no_raw)
    assert rev1 != rev3


def test_manifest_wrong_types_and_surrogates(managed_review_page):
    """Manifest ID sai kiểu dữ liệu hoặc chứa lone surrogates bị từ chối có kiểm soát."""
    page = managed_review_page

    plan_bad_type = {"manifest_id": 12345, "broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    with pytest.raises(ValueError, match="raw_plan manifest_id bắt buộc là chuỗi UTF-8"):
        page._build_matching_context(plan_bad_type)

    plan_surrogate = {"manifest_id": "bad_\ud800_id", "broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    with pytest.raises(ValueError, match="chứa ký tự UTF-8 không hợp lệ"):
        page._build_matching_context(plan_surrogate)

    page._manifest = MockManifestObj("bad_\ud800_active")
    plan_normal = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    with pytest.raises(ValueError, match="active manifest_id chứa ký tự UTF-8 không hợp lệ"):
        page._build_matching_context(plan_normal)


def test_oversized_source_array_precheck_before_iteration(managed_review_page):
    """Mảng nguồn broll + sfx vượt quá 4096 mục bị chặn ngay trước khi lặp preflight."""
    page = managed_review_page
    huge_brolls = [{"asset_type": "meme", "description": "cat"}] * 4097
    plan_huge = {"broll_inserts": huge_brolls}
    with pytest.raises(ValueError, match="vượt quá giới hạn 4096 mục"):
        page._build_matching_context(plan_huge)


def test_terminal_inner_result_query_mismatch_rejected(qapp, managed_review_page):
    """Result bên trong sidecar có query_text lệch với request bị từ chối, 0 publish."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 20
    req_id = "req_foreign_query_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 20,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    valid_res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    entry = DemandMatchEntry(demand=batch.demands[0], result=valid_res)
    # Mutate query_text
    object.__setattr__(valid_res, "query_text", "unauthorized_foreign_dog_query")

    bad_sidecar = AssetMatchingSidecar(
        plan_revision=rev,
        manifest_id=batch.manifest_id,
        entries=(entry,)
    )
    outcome = AssetMatchingOutcome(
        status="completed",
        generation=20,
        plan_revision=rev,
        sidecar=bad_sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert page.asset_advisory_drawer._model.rowCount() == 0
        assert "Kết quả tra cứu vi phạm cấu trúc" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_inner_result_bad_candidate_payload_rejected(qapp, managed_review_page):
    """Result bên trong sidecar chứa candidate object() không hợp lệ bị từ chối."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 21
    req_id = "req_bad_candidate_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 21,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    cand = OfflineAssetCandidate(
        asset_id="c1",
        name="cand1",
        category="memes",
        subcategory="sub",
        source_kind="video",
        availability="offline",
        match_score=85,
        matched_signals=(),
        duration_sec=3.0,
        pack_id="pack1",
        pack_name="Pack One",
    )
    valid_res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(cand,),
        is_matched=True,
    )
    entry = DemandMatchEntry(demand=batch.demands[0], result=valid_res)
    # Mutate candidate thành object()
    object.__setattr__(valid_res, "candidates", (object(),))

    bad_sidecar = AssetMatchingSidecar(
        plan_revision=rev,
        manifest_id=batch.manifest_id,
        entries=(entry,)
    )
    outcome = AssetMatchingOutcome(
        status="completed",
        generation=21,
        plan_revision=rev,
        sidecar=bad_sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert page.asset_advisory_drawer._model.rowCount() == 0
        assert "Kết quả tra cứu vi phạm cấu trúc" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_inner_result_contradictory_matched_flags_rejected(qapp, managed_review_page):
    """Result mang is_matched=True nhưng candidates rỗng bị từ chối."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 22
    req_id = "req_contradictory_flags_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 22,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    valid_res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    entry = DemandMatchEntry(demand=batch.demands[0], result=valid_res)
    # Mutate is_matched thành True khi candidates rỗng
    object.__setattr__(valid_res, "is_matched", True)

    bad_sidecar = AssetMatchingSidecar(
        plan_revision=rev,
        manifest_id=batch.manifest_id,
        entries=(entry,)
    )
    outcome = AssetMatchingOutcome(
        status="completed",
        generation=22,
        plan_revision=rev,
        sidecar=bad_sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert "Kết quả tra cứu vi phạm cấu trúc" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_missing_or_extra_results_rejected(qapp, managed_review_page):
    """Sidecar thiếu hoặc thừa kết quả so với số lượng requests mong đợi bị từ chối."""
    page = managed_review_page
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "cat"},
            {"asset_type": "meme", "description": "dog"}
        ]
    }
    batch, rev, canonical_bytes = page._build_matching_context(plan)
    assert len(batch.requests) == 2

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 23
    req_id = "req_missing_result_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 23,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    single_res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    incomplete_sidecar = AssetMatchingSidecar(
        plan_revision=rev,
        manifest_id=batch.manifest_id,
        entries=(
            DemandMatchEntry(demand=batch.demands[0], result=single_res),
            DemandMatchEntry(demand=batch.demands[1], result=None),
        )
    )
    outcome = AssetMatchingOutcome(
        status="completed",
        generation=23,
        plan_revision=rev,
        sidecar=incomplete_sidecar,
        storage_context=MatchingStorageContext(root="/root", library_id="lib"),
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert page._matching_pending_context is None
    finally:
        page._matching_workers_registry.clear()


def test_terminal_typed_storage_context_mutated_root_or_empty_denied(qapp, managed_review_page):
    """MatchingStorageContext bị mutate root=[] hoặc rỗng bị từ chối an toàn, 0 exception."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category=batch.requests[0].category,
        query_text=batch.requests[0].query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_CANDIDATE",
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 24
    req_id = "req_mutated_ctx_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 24,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    ctx = MatchingStorageContext(root="/root", library_id="lib")
    object.__setattr__(ctx, "root", [])  # Mutate root thành list

    outcome = AssetMatchingOutcome(
        status="completed",
        generation=24,
        plan_revision=rev,
        sidecar=sidecar,
        storage_context=ctx,
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert "Cấu hình kho media bị rỗng" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_valid_no_request_sidecar_published(qapp, managed_review_page):
    """Kịch bản có demand nhưng 0 request hợp lệ (noneligible) xuất bản sidecar thành công với context=None."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "unknown_cat", "description": ""}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)
    assert len(batch.requests) == 0
    assert len(batch.demands) == 1
    assert batch.demands[0].status in ("unhandled", "empty_query")

    sidecar = PlanDemandAdapter.assemble(batch, ())

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 25
    req_id = "req_no_request_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 25,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    outcome = AssetMatchingOutcome(
        status="completed",
        generation=25,
        plan_revision=rev,
        sidecar=sidecar,
        storage_context=None,
    )

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is not None
        assert "✓ Đã gợi ý (0/1 mục)" in page.lbl_matching_status.text()
    finally:
        page._matching_workers_registry.clear()


def test_terminal_error_or_cancel_with_sidecar_denied(qapp, managed_review_page):
    """Outcome trạng thái error nhưng mang sidecar vi phạm tính nhất quán, bị từ chối."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    batch, rev, canonical_bytes = page._build_matching_context(plan)
    sidecar = PlanDemandAdapter.assemble(batch, (
        OfflineAssetMatchResult(
            request_id=batch.requests[0].request_id,
            category=batch.requests[0].category,
            query_text=batch.requests[0].query_text,
            candidates=(),
            is_matched=False,
            unmatched_reason="ERR_NO_CANDIDATE",
        ),
    ))

    class DummyWorker:
        def cancel(self): pass

    dummy = DummyWorker()
    page._matching_generation = 26
    req_id = "req_err_with_sidecar_test"
    page._matching_pending_context = {
        "req_id": req_id,
        "generation": 26,
        "plan_revision": rev,
        "canonical_bytes": canonical_bytes,
        "batch": batch,
        "manifest_id": batch.manifest_id,
        "worker": dummy,
    }
    page._matching_workers_registry[req_id] = dummy
    page._video_plan_data = plan

    outcome = AssetMatchingOutcome(
        status="error",
        generation=26,
        plan_revision=rev,
        sidecar=None,
        storage_context=None,
        error_message="Lỗi kho",
    )
    object.__setattr__(outcome, "sidecar", sidecar)

    try:
        page._on_matching_terminal(dummy, req_id, outcome)
        assert page._last_good_matching_sidecar is None
        assert page._matching_pending_context is None
    finally:
        page._matching_workers_registry.clear()


def test_start_error_with_running_thread_retains_and_cancels(qapp, managed_review_page, monkeypatch):
    """Khi start() ném ngoại lệ nhưng worker báo isRunning()=True: RETAIN trong registry, cancel, giữ busy."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._video_plan_data = plan
    page._on_video_plan_changed()

    from PySide6.QtCore import QObject, Signal

    class FakeRunningWorker(QObject):
        terminal = Signal(object)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self._running = False
            self.cancel_called = False
            self.deleted = False

        def isRunning(self):
            return self._running

        def cancel(self):
            self.cancel_called = True

        def deleteLater(self):
            self.deleted = True

        def start(self):
            self._running = True
            raise RuntimeError("Start failed after thread started")

    import src.ui.director.pages.review_page
    monkeypatch.setattr(src.ui.director.pages.review_page, "AssetMatchingWorker", FakeRunningWorker)

    page.btn_match_assets.click()

    # 1. Worker PHẢI được giữ lại trong registry (không được xóa vì thread đang chạy)
    assert len(page._matching_workers_registry) == 1
    worker = list(page._matching_workers_registry.values())[0]

    # 2. Worker PHẢI được gọi cancel
    assert worker.cancel_called is True

    # 3. Worker KHÔNG được gọi deleteLater khi còn chạy
    assert worker.deleted is False

    # 4. Nút bấm PHẢI giữ Disabled vì còn busy
    assert page.btn_match_assets.isEnabled() is False

    # 5. Khi thread hoàn tất vật lý -> finished signal dọn dẹp
    req_id = list(page._matching_workers_registry.keys())[0]
    page._on_matching_finished(req_id, worker)

    assert len(page._matching_workers_registry) == 0
    assert worker.deleted is True
    assert page.btn_match_assets.isEnabled() is True


def test_start_error_before_running_rolls_back_cleanly(qapp, managed_review_page, monkeypatch):
    """Khi start() ném ngoại lệ trước khi thread chạy: rollback riêng request, deleteLater và mở lại nút."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._video_plan_data = plan
    page._on_video_plan_changed()

    from PySide6.QtCore import QObject, Signal

    class FakeNonRunningWorker(QObject):
        terminal = Signal(object)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.deleted = False

        def isRunning(self):
            return False

        def deleteLater(self):
            self.deleted = True

        def start(self):
            raise RuntimeError("Start failed before thread running")

    import src.ui.director.pages.review_page
    monkeypatch.setattr(src.ui.director.pages.review_page, "AssetMatchingWorker", FakeNonRunningWorker)

    page.btn_match_assets.click()

    assert len(page._matching_workers_registry) == 0
    assert page._matching_pending_context is None
    assert page.btn_match_assets.isEnabled() is True
    assert "Không thể khởi chạy tra cứu" in page.lbl_matching_status.text()


def test_other_running_jobs_aggregate_loading_preserved(qapp, managed_review_page, monkeypatch):
    """Lỗi matching worker không làm ảnh hưởng trạng thái loading của các worker khác."""
    page = managed_review_page
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "cat"}]}
    page._video_plan_data = plan

    # Giả lập một prompt worker đang chạy trong registry khác
    class OtherWorker:
        def cancel(self): pass

    page._prompt_workers_registry["prompt_job_1"] = OtherWorker()
    assert page.is_analysis_loading() is True

    def failing_worker_ctor(*args, **kwargs):
        raise RuntimeError("constructor failure")

    import src.ui.director.pages.review_page
    monkeypatch.setattr(src.ui.director.pages.review_page, "AssetMatchingWorker", failing_worker_ctor)

    try:
        page.btn_match_assets.click()
        # Worker khác vẫn còn -> is_analysis_loading() PHẢI là True
        assert page.is_analysis_loading() is True
    finally:
        page._prompt_workers_registry.clear()


