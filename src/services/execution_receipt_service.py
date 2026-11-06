"""Dịch vụ Quản lý Lưu trữ Biên bản Tiền Đột biến Bền vững (G07-07A6-I).

Module cung cấp:
- ExecutionReceiptService: Quản lý ghi đĩa nguyên tử 2 chặng, fsync bắt buộc, khóa idempotency bền vững và khóa tiến trình độc quyền.
- ExecutionLockContext: Context manager quản lý vòng đời tệp khóa độc quyền (.lock).
- Cơ chế phòng vệ hệ thống tệp: ENOSPC, EACCES, EROFS, filesystem corruption, directory fsync.

Tuân thủ nghiêm ngặt:
- 0 Resolve scripting API, 0 timeline mutation, 0 UI wiring.
- Khóa idempotency dẫn xuất từ (proposal_id, revision, approval_id, project_id, timeline_id).
- Khóa lock file chống chạy đồng thời cùng một execution_id.
- Ghi đĩa nguyên tử (.tmp -> fsync -> os.replace -> fsync directory).
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
import hashlib
import json
import os
import sys
import uuid
from typing import Any, Dict, Iterator, Optional, Tuple

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    MAX_RECEIPT_BYTES,
    VALID_RECEIPT_STATES,
    compute_idempotency_key,
    receipt_from_json,
    receipt_to_json,
)

# Thử import fcntl cho POSIX; fallback cờ lock cho Windows
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    fcntl = None  # type: ignore
    _HAS_FCNTL = False

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    msvcrt = None  # type: ignore
    _HAS_MSVCRT = False


class ReceiptStorageError(RuntimeError):
    """Lỗi nền tảng của hệ thống lưu trữ biên bản thi công."""
    pass


class IdempotencyDuplicateActiveError(ReceiptStorageError):
    """Từ chối thi công vì đã có phiên cùng nội dung duyệt đang trong trạng thái hoạt động."""
    pass


class IdempotencyDuplicateCompletedError(ReceiptStorageError):
    """Từ chối thi công vì nội dung phê duyệt này đã thi công hoàn tất trước đó."""
    pass


class ExecutionLockActiveError(ReceiptStorageError):
    """Từ chối thi công vì lock file đang bị tiến trình khác nắm giữ độc quyền."""
    pass


class ReceiptNotFoundError(ReceiptStorageError):
    """Không tìm thấy tệp biên bản thi công yêu cầu."""
    pass


class ReceiptAlreadyExistsError(ReceiptStorageError):
    """Tệp biên bản đã tồn tại trên đĩa; từ chối ghi đè."""
    pass


class TargetStateCorruptError(ReceiptStorageError):
    """Lỗi tệp trạng thái sở hữu timeline bị hỏng dữ liệu hoặc không đọc được (B4-R2-RES-03)."""
    pass


class ReceiptScanCapExceededError(ReceiptStorageError):
    """Lỗi số lượng biên bản trên đĩa vượt quá giới hạn quét an toàn (B4-R2-RES-03)."""
    pass


def _fsync_parent_dir(dir_path: str) -> None:
    """Ép buộc đồng bộ hóa directory entry của thư mục cha xuống đĩa (POSIX)."""
    if sys.platform.startswith("win"):
        return
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        dir_fd = os.open(dir_path, flags)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Bỏ qua nếu filesystem không hỗ trợ fsync trên directory descriptor
        pass


class ExecutionLockContext:
    """Quản lý tệp khóa độc quyền cho một phiên thi công."""

    def __init__(self, lock_path: str, execution_id: str):
        self.lock_path = lock_path
        self.execution_id = execution_id
        self._fd: Optional[int] = None

    def __enter__(self) -> ExecutionLockContext:
        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            if _HAS_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif _HAS_MSVCRT:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            raise ExecutionLockActiveError(
                f"ERR_EXECUTION_LOCKED: Không thể chiếm khóa {self.execution_id}: {exc}"
            ) from exc
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._fd is not None:
            try:
                if _HAS_FCNTL:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                elif _HAS_MSVCRT:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None


class ExecutionReceiptService:
    """Dịch vụ lưu trữ và điều phối vòng đời Biên bản Tiền Đột biến."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        self.receipts_dir = os.path.join(self.base_dir, "receipts")
        self.idempotency_dir = os.path.join(self.base_dir, "idempotency")
        os.makedirs(self.receipts_dir, exist_ok=True)
        os.makedirs(self.idempotency_dir, exist_ok=True)

    def _receipt_path(self, execution_id: str) -> str:
        return os.path.join(self.receipts_dir, f"{execution_id}.receipt.json")

    def _lock_path(self, execution_id: str) -> str:
        return os.path.join(self.receipts_dir, f"{execution_id}.lock")

    def _target_lock_path(self, project_id: str, timeline_id: str) -> str:
        key = hashlib.sha256(f"{project_id}:{timeline_id}".encode("utf-8")).hexdigest()
        return os.path.join(self.receipts_dir, f"target_{key}.lock")

    def _idempotency_lock_path(self, idempotency_key: str) -> str:
        return os.path.join(self.idempotency_dir, f"idempotency_{idempotency_key}.lock")

    def _idempotency_path(self, idempotency_key: str) -> str:
        return os.path.join(self.idempotency_dir, f"idempotency_{idempotency_key}.json")

    def _target_state_path(self, project_id: str, timeline_id: str) -> str:
        key = hashlib.sha256(f"{project_id}:{timeline_id}".encode("utf-8")).hexdigest()
        return os.path.join(self.receipts_dir, f"target_{key}.state.json")

    def persist_target_state(
        self,
        project_id: str,
        timeline_id: str,
        payload: Dict[str, Any],
    ) -> None:
        """Ghi bền vững trạng thái sở hữu của target timeline và fsync."""
        target_path = self._target_state_path(project_id, timeline_id)
        tmp_path = f"{target_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        raw_bytes = json.dumps(payload, indent=2).encode("utf-8")
        with open(tmp_path, "wb") as f:
            f.write(raw_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
        _fsync_parent_dir(self.receipts_dir)

    def get_target_state(self, project_id: str, timeline_id: str) -> Optional[Dict[str, Any]]:
        """Đọc bản ghi trạng thái sở hữu bền vững của target timeline từ đĩa (B4-R2-RES-03).
        
        Nếu tệp không tồn tại, trả về None.
        Nếu tệp tồn tại nhưng bị hỏng cấu trúc/không đọc được, ném TargetStateCorruptError thay vì nuốt lỗi.
        """
        path = self._target_state_path(project_id, timeline_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise TargetStateCorruptError(f"Target state file at {path} is not a valid JSON object")
            return data
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TargetStateCorruptError(f"Target state file at {path} contains malformed data: {exc}") from exc
        except OSError as exc:
            raise TargetStateCorruptError(f"Cannot read target state file at {path}: {exc}") from exc

    def find_nonterminal_receipt_for_target(
        self, project_id: str, timeline_id: str, max_scan: int = 1000
    ) -> Optional[ExecutionReceipt]:
        """Quét có giới hạn các biên bản trên đĩa tìm phiên thi công chưa kết thúc của target.
        
        B4-R2-RES-03: Nếu số lượng biên bản vượt quá max_scan, ném ReceiptScanCapExceededError
        để fail-closed thay vì cắt lát [:max_scan] gây bỏ sót phiên dở dang cũ nhất.
        """
        if not os.path.exists(self.receipts_dir):
            return None

        try:
            entries = os.listdir(self.receipts_dir)
        except OSError as exc:
            raise ReceiptStorageError(f"Cannot list receipts directory: {exc}") from exc

        receipt_files = [f for f in entries if f.endswith(".receipt.json")]
        if len(receipt_files) > max_scan:
            raise ReceiptScanCapExceededError(
                f"Receipt count ({len(receipt_files)}) exceeds max_scan limit ({max_scan}). "
                f"Refusing partial scan to avoid omitting older nonterminal executions."
            )

        for fname in receipt_files:
            exec_id = fname[:-len(".receipt.json")]
            rec = self.load_receipt(exec_id)
            if rec.project_id == project_id and rec.timeline_id == timeline_id:
                if rec.state in ("INITIALIZED", "EXECUTING"):
                    return rec

        return None

    def acquire_lock(self, execution_id: str) -> ExecutionLockContext:
        """Chiếm tệp khóa độc quyền cho một phiên thi công."""
        return ExecutionLockContext(self._lock_path(execution_id), execution_id)

    def acquire_target_lock(self, project_id: str, timeline_id: str) -> ExecutionLockContext:
        """Chiếm tệp khóa độc quyền theo danh tính target timeline (project_id, timeline_id).

        Ngăn chặn hai kế hoạch hoặc hai phiên khác nhau cùng thi công trên một timeline Resolve.
        """
        lock_path = self._target_lock_path(project_id, timeline_id)
        return ExecutionLockContext(lock_path, f"target_{project_id}_{timeline_id}")

    def acquire_idempotency_lock(self, idempotency_key: str) -> ExecutionLockContext:
        """Chiếm khóa độc quyền kiểm tra và đặt chỗ idempotency nguyên tử qua các tiến trình."""
        lock_path = self._idempotency_lock_path(idempotency_key)
        return ExecutionLockContext(lock_path, f"idemp_{idempotency_key}")

    def check_idempotency(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Kiểm tra bản ghi idempotency bền vững hiện có."""
        path = self._idempotency_path(idempotency_key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _persist_idempotency_record(
        self,
        idempotency_key: str,
        execution_id: str,
        state: str,
        created_at_iso: str,
        updated_at_iso: str,
    ) -> None:
        """Ghi nhận bản ghi khóa idempotency nguyên tử và fsync."""
        target_path = self._idempotency_path(idempotency_key)
        tmp_path = f"{target_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        payload = {
            "idempotency_key": idempotency_key,
            "execution_id": execution_id,
            "state": state,
            "created_at_iso": created_at_iso,
            "updated_at_iso": updated_at_iso,
        }
        raw_bytes = json.dumps(payload, indent=2).encode("utf-8")
        with open(tmp_path, "wb") as f:
            f.write(raw_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
        _fsync_parent_dir(self.idempotency_dir)

    def create_receipt(self, receipt: ExecutionReceipt) -> ExecutionReceipt:
        """Tạo mới và ghi bền vững một Biên bản Tiền Đột biến.

        Ràng buộc:
        - Khóa idempotency nguyên tử (acquire_idempotency_lock) chống race condition qua nhiều tiến trình.
        - Kiểm tra idempotency key trước: nếu đã có phiên ACTIVE hoặc COMPLETED -> Từ chối.
        - Tạo tệp bằng quy trình nguyên tử 2 chặng và fsync.
        - Không cho phép ghi đè lên execution_id đã tồn tại.
        """
        if not isinstance(receipt, ExecutionReceipt):
            raise ValueError("ERR_INVALID_RECEIPT: tham số phải là ExecutionReceipt")

        target_path = self._receipt_path(receipt.execution_id)
        if os.path.exists(target_path):
            raise ReceiptAlreadyExistsError(
                f"ERR_RECEIPT_EXISTS: Receipt cho {receipt.execution_id} đã tồn tại trên đĩa"
            )

        with self.acquire_idempotency_lock(receipt.idempotency_key):
            # 1. Kiểm tra Idempotency bền vững
            prior_idemp = self.check_idempotency(receipt.idempotency_key)
            if prior_idemp:
                prior_state = prior_idemp.get("state")
                if prior_state in ("INITIALIZED", "EXECUTING"):
                    raise IdempotencyDuplicateActiveError(
                        f"ERR_IDEMPOTENCY_DUPLICATE_ACTIVE: Phát hiện phiên {prior_idemp.get('execution_id')} "
                        f"đang thi công kịch bản này (state: {prior_state})"
                    )
                if prior_state == "COMPLETED":
                    raise IdempotencyDuplicateCompletedError(
                        f"ERR_IDEMPOTENCY_DUPLICATE_COMPLETED: Kịch bản này đã thi công thành công trước đó "
                        f"trong phiên {prior_idemp.get('execution_id')}"
                    )

            # 2. Ghi 2 chặng nguyên tử
            tmp_path = f"{target_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
            data_bytes = receipt_to_json(receipt).encode("utf-8")

            try:
                with open(tmp_path, "wb") as f:
                    f.write(data_bytes)
                    f.flush()
                    os.fsync(f.fileno())

                # Hoán đổi nguyên tử
                os.replace(tmp_path, target_path)
                _fsync_parent_dir(self.receipts_dir)
            except Exception:
                if os.path.exists(tmp_path):
                    with contextlib.suppress(Exception):
                        os.remove(tmp_path)
                raise

            # 3. Ghi Idempotency Index
            self._persist_idempotency_record(
                idempotency_key=receipt.idempotency_key,
                execution_id=receipt.execution_id,
                state=receipt.state,
                created_at_iso=receipt.created_at_iso,
                updated_at_iso=receipt.created_at_iso,
            )

            # 4. Ghi trạng thái Target bền vững (B4-R1-RES-02)
            self.persist_target_state(
                project_id=receipt.project_id,
                timeline_id=receipt.timeline_id,
                payload={
                    "project_id": receipt.project_id,
                    "timeline_id": receipt.timeline_id,
                    "active_execution_id": receipt.execution_id,
                    "state": receipt.state,
                    "in_doubt": False,
                    "updated_at_iso": receipt.created_at_iso,
                },
            )

        return receipt

    def load_receipt(self, execution_id: str) -> ExecutionReceipt:
        """Đọc và thẩm định toàn vẹn một Biên bản Tiền Đột biến từ đĩa."""
        target_path = self._receipt_path(execution_id)
        if not os.path.exists(target_path):
            raise ReceiptNotFoundError(f"ERR_RECEIPT_NOT_FOUND: Không tìm thấy receipt {execution_id}")

        file_size = os.path.getsize(target_path)
        if file_size > MAX_RECEIPT_BYTES:
            raise ReceiptStorageError(
                f"ERR_RECEIPT_TOO_LARGE: Dung lượng {file_size} vượt quá {MAX_RECEIPT_BYTES}"
            )

        with open(target_path, "rb") as f:
            raw_bytes = f.read()

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReceiptStorageError(f"ERR_INVALID_UTF8: Receipt không phải UTF-8 hợp lệ: {exc}") from exc

        return receipt_from_json(text)

    def transition_state(
        self,
        execution_id: str,
        new_state: str,
        updated_at_iso: str,
        completed_at_iso: Optional[str] = None,
        last_error_code: str = "",
        last_error_message: str = "",
        update_target_state: bool = True,
    ) -> ExecutionReceipt:
        """Chuyển đổi trạng thái vòng đời của Receipt một cách nguyên tử và bền vững."""
        curr = self.load_receipt(execution_id)

        if new_state not in VALID_RECEIPT_STATES:
            raise ValueError(f"ERR_INVALID_STATE: new_state {new_state!r} không hợp lệ")

        # Đồ thị chuyển trạng thái hợp lệ
        valid_transitions = {
            "INITIALIZED": ("EXECUTING", "ABORTED"),
            "EXECUTING": ("COMPLETED", "ROLLED_BACK", "ABORTED"),
            "COMPLETED": (),
            "ROLLED_BACK": (),
            "ABORTED": (),
        }

        allowed = valid_transitions.get(curr.state, ())
        if new_state not in allowed:
            raise ValueError(
                f"ERR_INVALID_TRANSITION: Không thể chuyển trạng thái từ {curr.state} sang {new_state}"
            )

        updated_receipt = replace(
            curr,
            state=new_state,
            completed_at_iso=completed_at_iso if completed_at_iso is not None else curr.completed_at_iso,
            last_error_code=last_error_code if last_error_code else curr.last_error_code,
            last_error_message=last_error_message if last_error_message else curr.last_error_message,
        )

        target_path = self._receipt_path(execution_id)
        tmp_path = f"{target_path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        data_bytes = receipt_to_json(updated_receipt).encode("utf-8")

        try:
            with open(tmp_path, "wb") as f:
                f.write(data_bytes)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, target_path)
            _fsync_parent_dir(self.receipts_dir)
        except Exception:
            if os.path.exists(tmp_path):
                with contextlib.suppress(Exception):
                    os.remove(tmp_path)
            raise

        # Cập nhật Idempotency Index
        self._persist_idempotency_record(
            idempotency_key=updated_receipt.idempotency_key,
            execution_id=updated_receipt.execution_id,
            state=updated_receipt.state,
            created_at_iso=updated_receipt.created_at_iso,
            updated_at_iso=updated_at_iso,
        )

        # Cập nhật trạng thái Target bền vững (B4-R1-RES-02)
        if update_target_state:
            self.persist_target_state(
                project_id=updated_receipt.project_id,
                timeline_id=updated_receipt.timeline_id,
                payload={
                    "project_id": updated_receipt.project_id,
                    "timeline_id": updated_receipt.timeline_id,
                    "active_execution_id": updated_receipt.execution_id,
                    "state": updated_receipt.state,
                    "in_doubt": False,
                    "updated_at_iso": updated_at_iso,
                },
            )

        return updated_receipt
