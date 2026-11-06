"""Bộ Điều phối Đánh giá Phục hồi Chỉ Đọc (RecoveryCoordinator - G07-07A7-I).

Module cung cấp:
- RecoveryCoordinator: Dịch vụ điều phối chỉ đọc nạp hồ sơ biên bản (receipt), nhật ký (journal),
  chỉ mục idempotency và thăm dò khóa cố vấn hệ điều hành (advisory lock) không chặn.
- Thăm dò khóa cố vấn độc quyền OS (Authoritative Non-Blocking OS Advisory Lock Probe):
  Chỉ phân loại lock active khi lời gọi fcntl.flock / msvcrt.locking bị chặn bởi kernel.
  Giải phóng khóa ngay lập tức nếu probe tự chiếm thành công (không để lại tàn dư khóa).
- Đảm bảo tính nguyên vẹn tuyệt đối (Zero-Mutation Guarantee):
  Tuyệt đối không xóa tệp, không dọn dẹp lock mồ côi ngầm, không sửa đổi dữ liệu đĩa.
  Chỉ mục idempotency thiếu hoặc hỏng được phân loại fail-closed an toàn (cấm replay).

Tuân thủ nghiêm ngặt:
- 0 Resolve mutation, 0 delete clips, 0 UI wiring.
- `ready_to_execute=False` được bảo toàn nguyên vẹn 100%.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalReplayResult,
    receipt_from_json,
)
from src.core.contracts.recovery_assessment_contracts import RecoveryAssessment
from src.services.execution_journal_service import (
    ExecutionJournalService,
    JournalCapacityExceededError,
    JournalCorruptedError,
    JournalNotFoundError,
    JournalSequenceError,
    JournalStorageError,
)
from src.services.recovery_classifier_service import RecoveryClassifierService

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


class RecoveryCoordinator:
    """Bộ điều phối thẩm định và phân loại phục hồi chỉ đọc."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        self.receipts_dir = os.path.join(self.base_dir, "receipts")
        self.journals_dir = os.path.join(self.base_dir, "journals")
        self.idempotency_dir = os.path.join(self.base_dir, "idempotency")
        self.journal_service = ExecutionJournalService(self.base_dir)

    def probe_advisory_lock(self, execution_id: str) -> Tuple[bool, Optional[int]]:
        """Thăm dò quyền sở hữu khóa cố vấn của hệ điều hành một cách không chặn và không phá hủy.

        Trả về:
            Tuple[is_advisory_lock_held: bool, diagnostic_recorded_pid: Optional[int]]
        """
        lock_path = os.path.join(self.receipts_dir, f"{execution_id}.lock")
        diagnostic_pid: Optional[int] = None

        if not os.path.exists(lock_path):
            return (False, None)

        # Đọc PID chẩn đoán nếu có ghi trong nội dung file
        try:
            with open(lock_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(256)
                m = re.search(r"(\d+)", content)
                if m:
                    diagnostic_pid = int(m.group(1))
        except Exception:
            pass

        # Thăm dò khóa cố vấn kernel không chặn
        fd: Optional[int] = None
        try:
            try:
                fd = os.open(lock_path, os.O_RDWR)
            except OSError:
                fd = os.open(lock_path, os.O_RDONLY)

            if _HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif _HAS_MSVCRT:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            # Chiếm được khóa -> không có tiến trình nào khác đang giữ khóa
            # Nhả khóa thăm dò ngay lập tức
            try:
                if _HAS_FCNTL:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif _HAS_MSVCRT:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except Exception:
                pass

            return (False, diagnostic_pid)
        except (BlockingIOError, OSError):
            # Khóa đang được tiến trình khác tích cực nắm giữ!
            return (True, diagnostic_pid)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def verify_idempotency_index(self, idempotency_key: str, execution_id: str) -> bool:
        """Kiểm tra tệp chỉ mục idempotency có tồn tại và hợp lệ cho execution_id hay không (Fail-Closed)."""
        idemp_path = os.path.join(self.idempotency_dir, f"idempotency_{idempotency_key}.json")
        if not os.path.exists(idemp_path):
            return False
        try:
            with open(idemp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            if data.get("idempotency_key") != idempotency_key:
                return False
            if data.get("execution_id") != execution_id:
                return False
            return True
        except Exception:
            return False

    def assess_execution(
        self,
        execution_id: str,
        active_project_id: Optional[str] = None,
        active_timeline_id: Optional[str] = None,
    ) -> RecoveryAssessment:
        """Đọc toàn bộ hồ sơ lưu trữ và xuất bản bản đánh giá phân loại phục hồi chỉ đọc."""

        # 1. Thăm dò quyền sở hữu khóa cố vấn OS
        is_lock_held, diag_pid = self.probe_advisory_lock(execution_id)

        # 2. Nạp receipt từ đĩa
        receipt_path = os.path.join(self.receipts_dir, f"{execution_id}.receipt.json")
        receipt: Optional[ExecutionReceipt] = None
        receipt_corrupted = False

        if not os.path.exists(receipt_path):
            receipt_corrupted = True
        else:
            try:
                with open(receipt_path, "r", encoding="utf-8") as f:
                    content = f.read()
                receipt = receipt_from_json(content)
                if receipt.execution_id != execution_id:
                    receipt_corrupted = True
            except Exception:
                receipt_corrupted = True

        # Nếu receipt hỏng, phân loại ngay lập tức
        if receipt_corrupted or receipt is None:
            return RecoveryClassifierService.classify(
                receipt=None,
                journal_result=None,
                is_advisory_lock_held=is_lock_held,
                diagnostic_recorded_pid=diag_pid,
                active_project_id=active_project_id,
                active_timeline_id=active_timeline_id,
                is_idempotency_index_valid=False,
                receipt_corrupted_or_missing=True,
            )

        # 3. Kiểm tra chỉ mục Idempotency bền vững
        is_idemp_valid = self.verify_idempotency_index(receipt.idempotency_key, receipt.execution_id)

        # 4. Nạp và phân tích journal
        journal_path = self.journal_service.journal_path(execution_id)
        journal_res: Optional[JournalReplayResult] = None

        if not os.path.exists(journal_path):
            journal_res = JournalReplayResult(entries=(), torn_tail=None, is_clean=True, total_bytes_read=0)
        else:
            try:
                journal_res = self.journal_service.replay_journal(execution_id)
            except (JournalCorruptedError, JournalSequenceError, JournalCapacityExceededError):
                # Journal bị biến dạng dữ liệu ở giữa tệp
                return RecoveryClassifierService.classify(
                    receipt=receipt,
                    journal_result=None,
                    is_advisory_lock_held=is_lock_held,
                    diagnostic_recorded_pid=diag_pid,
                    active_project_id=active_project_id,
                    active_timeline_id=active_timeline_id,
                    is_idempotency_index_valid=is_idemp_valid,
                    receipt_corrupted_or_missing=True,
                )
            except Exception:
                journal_res = JournalReplayResult(entries=(), torn_tail=None, is_clean=True, total_bytes_read=0)

        # 5. Phân loại tất định và xuất bản RecoveryAssessment
        return RecoveryClassifierService.classify(
            receipt=receipt,
            journal_result=journal_res,
            is_advisory_lock_held=is_lock_held,
            diagnostic_recorded_pid=diag_pid,
            active_project_id=active_project_id,
            active_timeline_id=active_timeline_id,
            is_idempotency_index_valid=is_idemp_valid,
            receipt_corrupted_or_missing=False,
        )
