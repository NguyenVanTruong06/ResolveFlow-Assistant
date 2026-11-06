"""
src/ui/director/workers/asset_matching_worker.py
=============================================================================
Worker chạy ngầm QThread cho tính năng Gợi ý / Cố vấn tài nguyên (Advisory Matching):
- Nhận AssetDemandBatch bất biến và generation monotonic.
- Hoàn toàn độc lập với GUI: 0 raw plan dict, 0 widget reference, 0 Resolve proxy.
- Constructor 0 IO, 0 gọi storage factory, 0 gọi storage_context_reader.
- Chạy storage_factory trên worker thread; gọi storage.assets(raise_on_error=True) đúng 1 lần.
- Additive MatchingStorageContext: kiểm tra cấu hình kho trước provider và trước completed terminal.
- Nối thật OfflineAssetMatchingService và PlanDemandAdapter.assemble.
- Phát đúng 1 tín hiệu logic: terminal = Signal(object) mang AssetMatchingOutcome.
- Giữ nguyên native QThread.finished cho dọn dẹp vật lý C++.
- Cơ chế cooperative cancel đa tầng, cancel() có hiệu lực bền vững ngay cả trước khi start().
=============================================================================
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal

from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetMatchingSidecar,
    validate_demand_batch,
)
from src.core.matching_service import OfflineAssetMatchingService
from src.core.plan_demand_adapter import PlanDemandAdapter


@dataclass(frozen=True)
class MatchingStorageContext:
    """Ngữ cảnh cấu hình kho lưu trữ quan sát được tại thời điểm worker thực thi."""
    root: str
    library_id: str

    def __post_init__(self):
        if not isinstance(self.root, str) or not self.root.strip():
            raise ValueError("root của MatchingStorageContext bắt buộc là chuỗi ký tự không rỗng.")
        if not isinstance(self.library_id, str) or not self.library_id.strip():
            raise ValueError("library_id của MatchingStorageContext bắt buộc là chuỗi ký tự không rỗng.")
        # Thẩm định UTF-8 không chứa lone surrogates
        self.root.encode("utf-8")
        self.library_id.encode("utf-8")


def read_matching_storage_context(storage: Any) -> MatchingStorageContext:
    """Đọc nghiêm ngặt bounded config từ storage.config_file trên worker và đối soát loaded config."""
    if storage is None or not hasattr(storage, "config_file"):
        raise ValueError("storage không hợp lệ hoặc thiếu thuộc tính config_file.")

    config_path = Path(storage.config_file)
    from src.core.storage_manager import _strict_read_json
    raw_cfg = _strict_read_json(config_path)

    if not isinstance(raw_cfg, dict):
        raise ValueError("Nội dung config_file không phải là dict hợp lệ.")

    disk_root = raw_cfg.get("root")
    disk_lib_id = raw_cfg.get("library_id")

    if not isinstance(disk_root, str) or not disk_root.strip():
        raise ValueError("Tệp cấu hình kho thiếu hoặc có 'root' không hợp lệ.")
    if not isinstance(disk_lib_id, str) or not disk_lib_id.strip():
        raise ValueError("Tệp cấu hình kho thiếu hoặc có 'library_id' không hợp lệ.")

    # Đối soát với loaded config trên instance storage (nếu có)
    loaded_cfg = getattr(storage, "_config", None)
    if isinstance(loaded_cfg, dict):
        loaded_root = loaded_cfg.get("root")
        loaded_lib_id = loaded_cfg.get("library_id")
        if loaded_root != disk_root or loaded_lib_id != disk_lib_id:
            raise ValueError("Cấu hình kho trên đĩa không khớp với cấu hình đã tải của StorageManager.")

    return MatchingStorageContext(root=disk_root, library_id=disk_lib_id)


@dataclass(frozen=True)
class AssetMatchingOutcome:
    """Kết quả hoàn tất của tiến trình Worker cố vấn tài nguyên."""
    status: str  # 'completed' | 'error' | 'cancelled'
    generation: int
    plan_revision: str
    sidecar: Optional[AssetMatchingSidecar] = None
    error_message: str = ""
    storage_context: Optional[MatchingStorageContext] = None

    def __post_init__(self):
        if self.status not in ("completed", "error", "cancelled"):
            raise ValueError(f"status không hợp lệ: '{self.status}'. Chỉ nhận 'completed', 'error' hoặc 'cancelled'.")
        if type(self.generation) is not int or type(self.generation) is bool or self.generation < 0:
            raise ValueError("generation bắt buộc là số nguyên >= 0 (không nhận bool).")
        if not isinstance(self.plan_revision, str) or not self.plan_revision.strip():
            raise ValueError("plan_revision bắt buộc là chuỗi ký tự không rỗng.")

        if self.storage_context is not None and not isinstance(self.storage_context, MatchingStorageContext):
            raise ValueError("storage_context bắt buộc phải là MatchingStorageContext hoặc None.")

        if self.status == "completed":
            if not isinstance(self.sidecar, AssetMatchingSidecar):
                raise ValueError("sidecar bắt buộc phải là thể hiện của AssetMatchingSidecar khi status='completed'.")
            if self.error_message:
                raise ValueError("error_message bắt buộc phải để trống khi status='completed'.")
        else:
            if self.sidecar is not None:
                raise ValueError("sidecar bắt buộc phải là None khi status != 'completed'.")
            if self.storage_context is not None:
                raise ValueError("storage_context bắt buộc phải là None khi status != 'completed'.")
            if self.status == "error":
                if not isinstance(self.error_message, str) or not self.error_message.strip():
                    raise ValueError("error_message bắt buộc phải có nội dung khi status='error'.")
            elif self.status == "cancelled":
                if self.error_message != "":
                    raise ValueError("error_message bắt buộc phải để trống khi status='cancelled'.")


class AssetMatchingWorker(QThread):
    """Worker QThread thực thi tra cứu tài nguyên cố vấn ngoại tuyến trên luồng nền."""
    terminal = Signal(object)  # Phát đúng 1 AssetMatchingOutcome khi kết thúc

    def __init__(
        self,
        batch: AssetDemandBatch,
        generation: int,
        *,
        storage_factory: Optional[Callable[[], Any]] = None,
        storage_context_reader: Optional[Callable[[Any], MatchingStorageContext]] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)

        # 1. Thẩm định tham số đầu vào (Constructor thuần túy in-memory, 0 IO, 0 factory call)
        validate_demand_batch(batch)

        if type(generation) is not int or type(generation) is bool or generation < 0:
            raise ValueError("generation bắt buộc là số nguyên >= 0 (không nhận bool).")

        if storage_factory is not None and not callable(storage_factory):
            raise ValueError("storage_factory nếu được truyền bắt buộc phải là callable (hàm không đối số).")

        if storage_context_reader is not None and not callable(storage_context_reader):
            raise ValueError("storage_context_reader nếu được truyền bắt buộc phải là callable.")

        self._batch = batch
        self._generation = generation
        self._plan_revision = batch.plan_revision
        self._storage_factory = storage_factory
        self._storage_context_reader = storage_context_reader

        # Cờ cooperative cancel bền vững (hoạt động ngay cả khi gọi cancel() trước khi start())
        self._cancel_requested: bool = False
        self._terminal_emitted: bool = False

    def cancel(self) -> None:
        """Yêu cầu dừng cooperative; an toàn idempotent, có hiệu lực ngay cả trước khi start()."""
        self._cancel_requested = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        """Kiểm tra tổng hợp cờ cancel nội bộ và Qt interruption request."""
        return self._cancel_requested or self.isInterruptionRequested()

    def _emit_terminal(self, outcome: AssetMatchingOutcome) -> None:
        """Bảo đảm phát chính xác 1 lần duy nhất tín hiệu terminal."""
        if not self._terminal_emitted:
            self._terminal_emitted = True
            self.terminal.emit(outcome)

    def run(self) -> None:
        """Điểm thực thi chính trên luồng nền."""
        # Checkpoint 0: Kiểm tra hủy trước khi thực hiện bất kỳ thao tác nào
        if self._is_cancelled():
            self._emit_terminal(AssetMatchingOutcome(
                status="cancelled",
                generation=self._generation,
                plan_revision=self._plan_revision
            ))
            return

        try:
            # Revalidate batch cấu trúc trước khi xử lý
            validate_demand_batch(self._batch)

            # Trường hợp 1: Không có requests hợp lệ (0 eligible demands)
            # Không gọi storage_factory, context reader hay provider; lắp ráp trực tiếp sidecar để giữ đúng các counters
            if len(self._batch.requests) == 0:
                if self._is_cancelled():
                    self._emit_terminal(AssetMatchingOutcome(
                        status="cancelled",
                        generation=self._generation,
                        plan_revision=self._plan_revision
                    ))
                    return

                sidecar = PlanDemandAdapter.assemble(self._batch, ())

                if self._is_cancelled():
                    self._emit_terminal(AssetMatchingOutcome(
                        status="cancelled",
                        generation=self._generation,
                        plan_revision=self._plan_revision
                    ))
                    return

                self._emit_terminal(AssetMatchingOutcome(
                    status="completed",
                    generation=self._generation,
                    plan_revision=self._plan_revision,
                    sidecar=sidecar,
                    storage_context=None
                ))
                return

            # Trường hợp 2: Có ít nhất 1 request cần tra cứu
            # Checkpoint 1: Trước khi khởi tạo storage và gọi I/O
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            # Khởi tạo instance StorageManager trên luồng worker
            if self._storage_factory is not None:
                storage = self._storage_factory()
            else:
                from src.core.storage_manager import StorageManager
                storage = StorageManager()

            # Checkpoint 1.5: Ngay sau khi khởi tạo storage (bảo đảm nếu cancel xảy ra trong factory thì dừng ngay)
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            # Context reader lần 1: Sau khi tạo storage, trước provider
            ctx1: Optional[MatchingStorageContext] = None
            if self._storage_context_reader is not None:
                ctx1 = self._storage_context_reader(storage)
                if not isinstance(ctx1, MatchingStorageContext):
                    raise ValueError("storage_context_reader phải trả về instance của MatchingStorageContext.")

            # Checkpoint 1.7: Sau context reader lần 1
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            # Định nghĩa catalog provider: gọi assets(raise_on_error=True)
            # Kiểm tra cancel tại entry của provider trước khi thực hiện bất kỳ lệnh đọc I/O nào
            def catalog_provider() -> list:
                if self._is_cancelled():
                    raise InterruptedError("Tiến trình worker đã bị hủy trước khi đọc tài nguyên kho.")
                return storage.assets(raise_on_error=True)

            service = OfflineAssetMatchingService(catalog_provider=catalog_provider)

            # Checkpoint 1.8: Trước khi bắt đầu gọi service
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            # Thực thi matching hàng loạt (Noninterruptible load/matching section)
            results = service.match_batch(self._batch.requests)

            # Checkpoint 2: Sau khi matching hoàn tất
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            # Lắp ráp báo cáo tư vấn sidecar
            sidecar = PlanDemandAdapter.assemble(self._batch, results)

            # Context reader lần 2: Sau match/assembly, trước completed terminal
            ctx2: Optional[MatchingStorageContext] = None
            if self._storage_context_reader is not None:
                ctx2 = self._storage_context_reader(storage)
                if not isinstance(ctx2, MatchingStorageContext):
                    raise ValueError("storage_context_reader phải trả về instance của MatchingStorageContext.")
                if ctx1 != ctx2:
                    raise ValueError("Kho media đã thay đổi cấu hình trong quá trình tra cứu. Vui lòng thử lại.")

            # Checkpoint 3: Trước khi phát tín hiệu terminal
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            self._emit_terminal(AssetMatchingOutcome(
                status="completed",
                generation=self._generation,
                plan_revision=self._plan_revision,
                sidecar=sidecar,
                storage_context=ctx2
            ))

        except Exception as exc:
            # Nếu người dùng đã yêu cầu hủy trong khi xảy ra ngoại lệ, ưu tiên cancelled
            if self._is_cancelled():
                self._emit_terminal(AssetMatchingOutcome(
                    status="cancelled",
                    generation=self._generation,
                    plan_revision=self._plan_revision
                ))
                return

            err_msg = str(exc) or type(exc).__name__
            self._emit_terminal(AssetMatchingOutcome(
                status="error",
                generation=self._generation,
                plan_revision=self._plan_revision,
                error_message=f"Lỗi tra cứu tài nguyên: {err_msg}"
            ))
