"""
tests/ui/test_asset_matching_worker.py
=============================================================================
Finite TDD Unit Test Suite cho AssetMatchingWorker & AssetMatchingOutcome:
- Thẩm định AssetMatchingOutcome frozen DTO (status, generation, sidecar, error_message).
- Thẩm định AssetMatchingWorker constructor (0 IO, 0 factory call, typegate generation/factory).
- Luồng batch requests rỗng: 0 factory call, hoàn tất thành công với sidecar và counters trung thực.
- Hủy sớm trước start: exactly 1 terminal 'cancelled', 0 factory call.
- Hủy trong lúc tạo storage factory: calls=['factory'], assets 0, outcome 'cancelled'.
- Hủy tại entry catalog_provider: assets 0, outcome 'cancelled'.
- Hủy trong quá trình đọc assets: outcome 'cancelled'.
- Hủy sau matching / trước assembly: outcome 'cancelled'.
- Hủy sau assembly / trước terminal: outcome 'cancelled'.
- Fault injection matcher / assemble: outcome 'error', sidecar=None.
- Cancel ưu tiên khi gặp exception đồng thời (cancel wins over fault).
- Bắt lỗi factory và provider: ném StorageOfflineError -> exactly 1 terminal 'error', sidecar=None.
- Default factory path (storage_factory=None): khởi tạo StorageManager trên luồng worker với strict kwargs.
- Happy path QThread thật: chạy offscreen trên thread riêng biệt, phát terminal 'completed', sidecar hợp lệ.
- Test harness chuẩn hóa: monotonic deadline, processEvents, native finished và isRunning=False assertions,
  dọn dẹp an toàn trong finally (zero leak worker).
=============================================================================
"""

import threading
import time
from typing import Any, List, Optional
import pytest
from PySide6.QtCore import QCoreApplication

from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetMatchingSidecar,
)
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.core.storage_manager import StorageOfflineError
from src.ui.director.workers.asset_matching_worker import (
    AssetMatchingOutcome,
    AssetMatchingWorker,
    MatchingStorageContext,
    read_matching_storage_context,
)


@pytest.fixture(scope="module")
def qapp():
    """Đảm bảo có QCoreApplication cho các bài test Qt luồng nền."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def wait_for_worker(
    worker: AssetMatchingWorker,
    qapp: QCoreApplication,
    timeout_sec: float = 3.0
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
    """Fixture quản lý vòng đời worker và đảm bảo dọn dẹp sạch sẽ trong finally."""
    active_workers: List[AssetMatchingWorker] = []

    def _register(worker: AssetMatchingWorker):
        active_workers.append(worker)
        outcomes: List[AssetMatchingOutcome] = []
        finished_signals: List[bool] = []
        worker.terminal.connect(outcomes.append)
        worker.finished.connect(lambda: finished_signals.append(True))
        return outcomes, finished_signals

    yield _register

    # Teardown an toàn: cancel và wait mọi worker còn chạy
    for w in active_workers:
        if w.isRunning():
            try:
                w.cancel()
                w.wait(2000)
                qapp.processEvents()
            except Exception:
                pass
        assert not w.isRunning(), "Worker bị leak hoặc vẫn đang chạy sau test!"


# =============================================================================
# Nhóm 1: AssetMatchingOutcome DTO Tests
# =============================================================================

def test_outcome_validations():
    # 1. Status không hợp lệ
    with pytest.raises(ValueError, match="status không hợp lệ"):
        AssetMatchingOutcome(status="unknown", generation=1, plan_revision="rev1")

    # 2. Generation không hợp lệ (âm hoặc bool)
    with pytest.raises(ValueError, match="generation bắt buộc là số nguyên >= 0"):
        AssetMatchingOutcome(status="cancelled", generation=-1, plan_revision="rev1")

    with pytest.raises(ValueError, match="generation bắt buộc là số nguyên >= 0"):
        AssetMatchingOutcome(status="cancelled", generation=True, plan_revision="rev1")  # type: ignore

    # 3. Completed bắt buộc có sidecar
    with pytest.raises(ValueError, match="sidecar bắt buộc phải là thể hiện"):
        AssetMatchingOutcome(status="completed", generation=1, plan_revision="rev1", sidecar=None)

    # 4. Error bắt buộc có error_message và sidecar=None
    with pytest.raises(ValueError, match="error_message bắt buộc phải có nội dung"):
        AssetMatchingOutcome(status="error", generation=1, plan_revision="rev1", error_message="")

    sidecar = AssetMatchingSidecar(plan_revision="rev1", manifest_id=None, entries=())
    with pytest.raises(ValueError, match="sidecar bắt buộc phải là None"):
        AssetMatchingOutcome(status="error", generation=1, plan_revision="rev1", sidecar=sidecar, error_message="Lỗi")

    # 5. Cancelled bắt buộc sidecar=None và error_message=""
    outcome_c = AssetMatchingOutcome(status="cancelled", generation=2, plan_revision="rev2")
    assert outcome_c.status == "cancelled"
    assert outcome_c.sidecar is None
    assert outcome_c.error_message == ""


# =============================================================================
# Nhóm 2: AssetMatchingWorker Constructor & 0 IO Tests
# =============================================================================

def test_worker_constructor_validations(qapp):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_ctor")

    # Generation là bool
    with pytest.raises(ValueError, match="generation bắt buộc là số nguyên >= 0"):
        AssetMatchingWorker(batch, True)  # type: ignore

    # Generation âm
    with pytest.raises(ValueError, match="generation bắt buộc là số nguyên >= 0"):
        AssetMatchingWorker(batch, -1)

    # Storage factory không phải callable
    with pytest.raises(ValueError, match="storage_factory"):
        AssetMatchingWorker(batch, 1, storage_factory="not_a_callable")  # type: ignore

    # Constructor không gọi factory và không thực hiện IO
    factory_called = False
    def spy_factory():
        nonlocal factory_called
        factory_called = True
        return None

    worker = AssetMatchingWorker(batch, 1, storage_factory=spy_factory)
    assert factory_called is False
    assert worker._generation == 1
    assert worker._plan_revision == "rev_ctor"


# =============================================================================
# Nhóm 3: Batch Requests Rỗng (0 eligible demands)
# =============================================================================

def test_worker_empty_requests_batch_faithful_counters(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": ""}]}  # description rỗng -> empty_query
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_empty_req")
    assert len(batch.requests) == 0
    assert len(batch.demands) == 1
    assert batch.demands[0].status == "empty_query"

    factory_called = False
    def spy_factory():
        nonlocal factory_called
        factory_called = True
        return None

    worker = AssetMatchingWorker(batch, 1, storage_factory=spy_factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Factory hoàn toàn không bị gọi vì không có requests
    assert factory_called is False

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == "completed"
    assert outcome.generation == 1
    assert outcome.plan_revision == "rev_empty_req"
    assert outcome.sidecar is not None
    assert outcome.sidecar.total_demands == 1
    assert outcome.sidecar.empty_query_count == 1
    assert outcome.sidecar.matched_count == 0


# =============================================================================
# Nhóm 4: Cooperative Cancellation Tests
# =============================================================================

def test_worker_cancel_before_start(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel")

    factory_called = False
    def spy_factory():
        nonlocal factory_called
        factory_called = True
        return None

    worker = AssetMatchingWorker(batch, 1, storage_factory=spy_factory)
    outcomes, finished_signals = managed_worker(worker)

    # Hủy trước khi gọi start()
    worker.cancel()

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert factory_called is False
    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].sidecar is None
    assert outcomes[0].error_message == ""


def test_worker_cancel_during_storage_factory(qapp, managed_worker):
    """Factory gọi worker.cancel() trước khi trả storage: calls=['factory'], assets=0."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_fac")

    calls = []

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            calls.append("assets")
            return []

    worker = None
    def cancelling_factory():
        calls.append("factory")
        if worker is not None:
            worker.cancel()
        return MockStorage()

    worker = AssetMatchingWorker(batch, 2, storage_factory=cancelling_factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Kiểm tra chính xác cuộc gọi: đúng 1 factory, 0 assets
    assert calls == ["factory"]
    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].generation == 2
    assert outcomes[0].sidecar is None
    assert outcomes[0].error_message == ""


def test_worker_cancel_at_provider_boundary(qapp, managed_worker):
    """Kiểm tra huỷ ngay trước khi provider gọi storage.assets."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_bound")

    assets_called = False

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            nonlocal assets_called
            assets_called = True
            return []

    worker = None
    def factory():
        # Gọi cancel sau factory nhưng trước khi service.match_batch chạy
        res = MockStorage()
        worker.cancel()
        return res

    worker = AssetMatchingWorker(batch, 3, storage_factory=factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert assets_called is False
    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].sidecar is None


def test_worker_cancel_inside_provider_assets(qapp, managed_worker):
    """Hủy diễn ra trong lúc storage.assets() đang thực thi."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_inside")

    class MockStorage:
        def __init__(self, target_worker):
            self.w = target_worker

        def assets(self, *, raise_on_error: bool = False):
            self.w.cancel()
            return []

    worker = None
    def factory():
        return MockStorage(worker)

    worker = AssetMatchingWorker(batch, 5, storage_factory=factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].generation == 5
    assert outcomes[0].sidecar is None


def test_worker_cancel_after_matching_before_assembly(qapp, managed_worker, monkeypatch):
    """Hủy xảy ra ngay sau khi match_batch hoàn tất nhưng trước assemble."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_after_match")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    worker = AssetMatchingWorker(batch, 6, storage_factory=MockStorage)
    outcomes, finished_signals = managed_worker(worker)

    from src.core.matching_service import OfflineAssetMatchingService
    orig_match_batch = OfflineAssetMatchingService.match_batch

    def mock_match_batch(self, requests):
        res = orig_match_batch(self, requests)
        worker.cancel()
        return res

    monkeypatch.setattr(OfflineAssetMatchingService, "match_batch", mock_match_batch)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].generation == 6
    assert outcomes[0].sidecar is None


def test_worker_cancel_after_assembly_before_terminal(qapp, managed_worker, monkeypatch):
    """Hủy xảy ra ngay sau khi PlanDemandAdapter.assemble hoàn tất."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_after_asm")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    worker = AssetMatchingWorker(batch, 7, storage_factory=MockStorage)
    outcomes, finished_signals = managed_worker(worker)

    orig_assemble = PlanDemandAdapter.assemble

    def mock_assemble(b, results):
        sidecar = orig_assemble(b, results)
        worker.cancel()
        return sidecar

    monkeypatch.setattr(PlanDemandAdapter, "assemble", mock_assemble)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].generation == 7
    assert outcomes[0].sidecar is None


# =============================================================================
# Nhóm 5: Fault Injection & Exceptions Handling
# =============================================================================

def test_worker_storage_factory_exception(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_err_fac")

    def broken_factory():
        raise RuntimeError("Khởi tạo storage thất bại.")

    worker = AssetMatchingWorker(batch, 3, storage_factory=broken_factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 3
    assert outcomes[0].sidecar is None
    assert "Khởi tạo storage thất bại" in outcomes[0].error_message


def test_worker_provider_storage_offline_error(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_err_prov")

    class OfflineStorage:
        def assets(self, *, raise_on_error: bool = False):
            if raise_on_error:
                raise StorageOfflineError("Ổ đĩa lưu trữ đã bị ngắt kết nối.")
            return []

    worker = AssetMatchingWorker(batch, 4, storage_factory=OfflineStorage)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 4
    assert outcomes[0].sidecar is None
    assert "Ổ đĩa lưu trữ đã bị ngắt kết nối" in outcomes[0].error_message


def test_worker_matching_exception(qapp, managed_worker, monkeypatch):
    """Ngoại lệ xảy ra trong matcher -> exactly 1 error outcome."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_err_match")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    from src.core.matching_service import OfflineAssetMatchingService
    def mock_match_batch_err(self, requests):
        raise ValueError("Lỗi matching thuật toán mô phỏng.")

    monkeypatch.setattr(OfflineAssetMatchingService, "match_batch", mock_match_batch_err)

    worker = AssetMatchingWorker(batch, 8, storage_factory=MockStorage)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 8
    assert outcomes[0].sidecar is None
    assert "Lỗi matching thuật toán mô phỏng" in outcomes[0].error_message


def test_worker_assembly_exception(qapp, managed_worker, monkeypatch):
    """Ngoại lệ xảy ra trong adapter assemble -> exactly 1 error outcome."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_err_asm")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    def mock_assemble_err(b, results):
        raise RuntimeError("Lỗi lắp ráp adapter mô phỏng.")

    monkeypatch.setattr(PlanDemandAdapter, "assemble", mock_assemble_err)

    worker = AssetMatchingWorker(batch, 9, storage_factory=MockStorage)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 9
    assert outcomes[0].sidecar is None
    assert "Lỗi lắp ráp adapter mô phỏng" in outcomes[0].error_message


def test_worker_cancel_wins_over_exception(qapp, managed_worker):
    """Khi có cả lỗi và cờ hủy cùng diễn ra, cancel luôn chiến thắng."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_cancel_wins")

    worker = None
    def failing_and_cancelling_factory():
        worker.cancel()
        raise RuntimeError("Lỗi nghiêm trọng trong factory.")

    worker = AssetMatchingWorker(batch, 11, storage_factory=failing_and_cancelling_factory)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "cancelled"
    assert outcomes[0].generation == 11
    assert outcomes[0].sidecar is None
    assert outcomes[0].error_message == ""


# =============================================================================
# Nhóm 6: Default Factory Path & QThread Execution
# =============================================================================

def test_worker_default_factory_path_on_worker_thread(qapp, managed_worker, monkeypatch):
    """Kiểm tra nhánh storage_factory=None: StorageManager được khởi tạo trên worker thread."""
    main_thread_id = threading.get_ident()
    ctor_thread_id = None
    assets_thread_id = None
    assets_kwargs = {}

    class FakeStorageManager:
        def __init__(self):
            nonlocal ctor_thread_id
            ctor_thread_id = threading.get_ident()

        def assets(self, **kwargs):
            nonlocal assets_thread_id, assets_kwargs
            assets_thread_id = threading.get_ident()
            assets_kwargs = kwargs
            return []

    import src.core.storage_manager
    monkeypatch.setattr(src.core.storage_manager, "StorageManager", FakeStorageManager)

    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_def_factory")

    # Constructor không gọi factory, ctor_thread_id vẫn là None
    worker = AssetMatchingWorker(batch, 12, storage_factory=None)
    assert ctor_thread_id is None

    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Khởi tạo diễn ra trên worker thread, không phải GUI/main thread
    assert ctor_thread_id is not None
    assert ctor_thread_id != main_thread_id
    assert assets_thread_id == ctor_thread_id
    assert assets_kwargs == {"raise_on_error": True}

    assert len(outcomes) == 1
    assert outcomes[0].status == "completed"
    assert outcomes[0].generation == 12


def test_worker_happy_path_thread_execution(qapp, managed_worker):
    main_thread_id = threading.get_ident()
    worker_thread_id = None

    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "laughing cat"}
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_happy")

    class MockHappyStorage:
        def assets(self, *, raise_on_error: bool = False):
            nonlocal worker_thread_id
            worker_thread_id = threading.get_ident()
            return [
                {
                    "id": "asset_cat_01",
                    "schema_version": 1,
                    "name": "laughing cat viral",
                    "category": "memes",
                    "subcategory": "cat",
                    "file_path": "/fake/path/cat.mp4",
                    "relative_path": "memes/cat.mp4",
                    "source_kind": "managed",
                    "pack_id": "pack_memes_01",
                    "pack_name": "Cat Memes Pack",
                    "library_path": "memes/cat.mp4",
                    "availability": "online",
                    "size_bytes": 1024 * 1024
                }
            ]

    worker = AssetMatchingWorker(batch, 10, storage_factory=MockHappyStorage)
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Xác nhận provider chạy trên thread riêng khác main thread
    assert worker_thread_id is not None
    assert worker_thread_id != main_thread_id

    # Xác nhận outcome thành công
    assert len(outcomes) == 1
    assert outcomes[0].status == "completed"
    assert outcomes[0].generation == 10
    assert outcomes[0].plan_revision == "rev_happy"
    assert outcomes[0].sidecar is not None
    assert outcomes[0].sidecar.total_demands == 1
    assert outcomes[0].sidecar.matched_count == 1
    assert outcomes[0].sidecar.entries[0].is_matched is True
    assert len(outcomes[0].sidecar.entries[0].candidates) > 0
    assert outcomes[0].sidecar.entries[0].candidates[0].asset_id == "asset_cat_01"


# =============================================================================
# Nhóm 7: MatchingStorageContext & Storage Freshness Tests
# =============================================================================

def test_matching_storage_context_validations():
    # 1. Root không hợp lệ
    with pytest.raises(ValueError, match="root"):
        MatchingStorageContext(root="", library_id="lib1")
    with pytest.raises(ValueError, match="root"):
        MatchingStorageContext(root=None, library_id="lib1")  # type: ignore

    # 2. Library ID không hợp lệ
    with pytest.raises(ValueError, match="library_id"):
        MatchingStorageContext(root="/path/to/root", library_id="")
    with pytest.raises(ValueError, match="library_id"):
        MatchingStorageContext(root="/path/to/root", library_id=None)  # type: ignore

    # 3. Context hợp lệ
    ctx = MatchingStorageContext(root="/path/to/root", library_id="lib_alpha")
    assert ctx.root == "/path/to/root"
    assert ctx.library_id == "lib_alpha"


def test_read_matching_storage_context_helper(tmp_path):
    import json
    # 1. Storage không hợp lệ / thiếu config_file
    with pytest.raises(ValueError, match="config_file"):
        read_matching_storage_context(None)

    class DummyStorage:
        pass
    with pytest.raises(ValueError, match="config_file"):
        read_matching_storage_context(DummyStorage())

    # 2. Config file thiếu trên đĩa -> StorageOfflineError
    cfg_file = tmp_path / "storage.json"
    class MissingCfgStorage:
        config_file = cfg_file

    with pytest.raises(StorageOfflineError):
        read_matching_storage_context(MissingCfgStorage())

    # 3. Config file corrupt cú pháp JSON -> ValueError
    cfg_file.write_text("corrupted { json", encoding="utf-8")
    with pytest.raises(ValueError, match="bị hỏng"):
        read_matching_storage_context(MissingCfgStorage())

    # 4. Config file thiếu root hoặc library_id -> ValueError
    cfg_file.write_text(json.dumps({"root": "/media/storage"}), encoding="utf-8")
    with pytest.raises(ValueError, match="library_id"):
        read_matching_storage_context(MissingCfgStorage())

    # 5. Config file lệch với storage._config -> ValueError
    cfg_file.write_text(json.dumps({"root": "/media/disk_root", "library_id": "lib_disk"}), encoding="utf-8")
    class MismatchedStorage:
        config_file = cfg_file
        _config = {"root": "/media/other_root", "library_id": "lib_disk"}

    with pytest.raises(ValueError, match="không khớp"):
        read_matching_storage_context(MismatchedStorage())

    # 6. Hợp lệ: khớp cả đĩa và loaded _config
    cfg_file.write_text(json.dumps({"root": "/media/disk_root", "library_id": "lib_disk"}), encoding="utf-8")
    class ValidStorage:
        config_file = cfg_file
        _config = {"root": "/media/disk_root", "library_id": "lib_disk"}

    ctx = read_matching_storage_context(ValidStorage())
    assert ctx.root == "/media/disk_root"
    assert ctx.library_id == "lib_disk"


def test_worker_with_storage_context_reader_happy(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "laughing cat"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_ctx_happy")

    class MockContextStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    expected_ctx = MatchingStorageContext(root="/media/root", library_id="lib_123")
    reader_calls = 0

    def mock_reader(storage):
        nonlocal reader_calls
        reader_calls += 1
        return expected_ctx

    worker = AssetMatchingWorker(
        batch,
        15,
        storage_factory=MockContextStorage,
        storage_context_reader=mock_reader
    )
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Kiểm tra reader được gọi 2 lần (pre-provider và post-assemble)
    assert reader_calls == 2

    assert len(outcomes) == 1
    assert outcomes[0].status == "completed"
    assert outcomes[0].generation == 15
    assert outcomes[0].storage_context == expected_ctx


def test_worker_storage_context_changed_between_reads_error(qapp, managed_worker):
    """Khi cấu hình kho thay đổi giữa chừng (root switch), worker phải báo lỗi có kiểm soát."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_ctx_switch")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    read_count = 0
    def switching_reader(storage):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return MatchingStorageContext(root="/media/root_A", library_id="lib_1")
        return MatchingStorageContext(root="/media/root_B", library_id="lib_1")

    worker = AssetMatchingWorker(
        batch,
        16,
        storage_factory=MockStorage,
        storage_context_reader=switching_reader
    )
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 16
    assert outcomes[0].sidecar is None
    assert outcomes[0].storage_context is None
    assert "Kho media đã thay đổi cấu hình" in outcomes[0].error_message


def test_worker_storage_context_reader_exception(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_ctx_err")

    class MockStorage:
        def assets(self, *, raise_on_error: bool = False):
            return []

    def broken_reader(storage):
        raise RuntimeError("Lỗi đọc ngữ cảnh kho mô phỏng.")

    worker = AssetMatchingWorker(
        batch,
        17,
        storage_factory=MockStorage,
        storage_context_reader=broken_reader
    )
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    assert len(outcomes) == 1
    assert outcomes[0].status == "error"
    assert outcomes[0].generation == 17
    assert outcomes[0].sidecar is None
    assert outcomes[0].storage_context is None
    assert "Lỗi đọc ngữ cảnh kho mô phỏng" in outcomes[0].error_message


def test_worker_empty_requests_with_context_reader_no_call(qapp, managed_worker):
    plan = {"broll_inserts": [{"asset_type": "meme", "description": ""}]}  # empty query -> 0 requests
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_empty_ctx")

    reader_called = False
    def spy_reader(storage):
        nonlocal reader_called
        reader_called = True
        return MatchingStorageContext(root="/root", library_id="lib")

    worker = AssetMatchingWorker(
        batch,
        18,
        storage_context_reader=spy_reader
    )
    outcomes, finished_signals = managed_worker(worker)

    worker.start()
    assert wait_for_worker(worker, qapp, timeout_sec=3.0) is True
    assert worker.isRunning() is False
    assert len(finished_signals) == 1

    # Không có requests -> không gọi context reader, storage_context=None
    assert reader_called is False
    assert len(outcomes) == 1
    assert outcomes[0].status == "completed"
    assert outcomes[0].generation == 18
    assert outcomes[0].storage_context is None

