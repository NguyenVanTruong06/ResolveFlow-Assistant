"""src/ui/director/workers/recovery_assessment_worker.py
Bộ thực thi luồng nền QThread đối soát và phân loại phục hồi chỉ đọc (G07-07A8-I).

RÀNG BUỘC KIẾN TRÚC & AN TOÀN:
- Tuân thủ ranh giới đơn luồng Resolve (Resolve Thread Affinity):
  Worker TUYỆT ĐỐI KHÔNG import hoặc gọi DaVinci Resolve scripting IPC hay query_active_timeline_authority.
  Worker chỉ nhận bản chụp thẩm quyền bất biến (authority_snapshot) được luồng chủ sở hữu lấy mẫu sẵn.
- Tách biệt lỗi truy cập I/O (EACCES/EIO) với biến dạng cấu trúc tệp (Artifact Corruption):
  Phát signal assessment_io_error riêng biệt; không quy chụp lỗi phân quyền là hỏng forensic.
- Không chiếm giữ khóa cố vấn (Non-retaining advisory lock probe).
- Thuần túy chỉ đọc (Zero-Mutation Guarantee): 0 ghi đĩa, 0 sửa đổi, 0 tạo executor.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from src.core.contracts.recovery_assessment_contracts import RecoveryAssessment
from src.services.recovery_coordinator import RecoveryCoordinator

logger = logging.getLogger(__name__)


class RecoveryAssessmentWorker(QThread):
    """Worker QThread nạp và phân loại hồ sơ đối soát phục hồi hoàn toàn ngoài luồng GUI."""

    assessment_completed = Signal(object)  # Phát RecoveryAssessment
    assessment_io_error = Signal(str)      # Phát chuỗi chẩn đoán lỗi I/O (EACCES/EIO)
    assessment_failed = Signal(str)        # Phát thông báo lỗi ngoại lệ ngoài dự kiến

    def __init__(
        self,
        base_dir: str | Path,
        execution_id: str,
        authority_snapshot: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._base_dir = str(base_dir)
        self._execution_id = str(execution_id)
        self._authority_snapshot = authority_snapshot

    def run(self) -> None:
        """Thực thi kiểm tra quyền đọc tệp, thăm dò khóa cố vấn và phân loại tất định trên luồng nền."""
        logger.info(
            "Khởi chạy RecoveryAssessmentWorker cho execution_id=%s (off-GUI)",
            self._execution_id,
        )

        receipts_dir = os.path.join(self._base_dir, "receipts")
        receipt_path = os.path.join(receipts_dir, f"{self._execution_id}.receipt.json")

        # 1. Thăm dò quyền truy cập I/O của hồ sơ biên nhận (bảo vệ trước lỗi EACCES/EIO)
        if os.path.exists(receipt_path):
            try:
                with open(receipt_path, "r", encoding="utf-8") as f:
                    f.read(1)
            except PermissionError as p_err:
                err_msg = f"Lỗi quyền truy cập (EACCES) khi đọc hồ sơ biên nhận: {p_err}"
                logger.warning(err_msg)
                self.assessment_io_error.emit(err_msg)
                return
            except OSError as os_err:
                if os_err.errno in (errno.EACCES, errno.EPERM, errno.EIO):
                    err_msg = f"Lỗi hệ thống tệp I/O (mã {os_err.errno}) khi đọc hồ sơ biên nhận: {os_err}"
                    logger.warning(err_msg)
                    self.assessment_io_error.emit(err_msg)
                    return

        # 2. Thăm dò quyền truy cập I/O của nhật ký thi công nếu tồn tại
        journals_dir = os.path.join(self._base_dir, "journals")
        journal_path = os.path.join(journals_dir, f"{self._execution_id}.journal.jsonl")
        if os.path.exists(journal_path):
            try:
                with open(journal_path, "r", encoding="utf-8") as f:
                    f.read(1)
            except PermissionError as p_err:
                err_msg = f"Lỗi quyền truy cập (EACCES) khi đọc nhật ký thi công: {p_err}"
                logger.warning(err_msg)
                self.assessment_io_error.emit(err_msg)
                return
            except OSError as os_err:
                if os_err.errno in (errno.EACCES, errno.EPERM, errno.EIO):
                    err_msg = f"Lỗi hệ thống tệp I/O (mã {os_err.errno}) khi đọc nhật ký thi công: {os_err}"
                    logger.warning(err_msg)
                    self.assessment_io_error.emit(err_msg)
                    return

        # 3. Trích xuất danh tính từ authority_snapshot đã lấy mẫu an toàn trên luồng chủ sở hữu
        active_project_id: Optional[str] = None
        active_timeline_id: Optional[str] = None
        if self._authority_snapshot is not None and getattr(self._authority_snapshot, "ok", False):
            active_project_id = getattr(
                self._authority_snapshot,
                "project_id",
                getattr(self._authority_snapshot, "project_name", None),
            )
            active_timeline_id = getattr(self._authority_snapshot, "timeline_id", None)

        # 4. Gọi bộ điều phối đối soát chỉ đọc
        coordinator = RecoveryCoordinator(self._base_dir)
        try:
            assessment = coordinator.assess_execution(
                execution_id=self._execution_id,
                active_project_id=active_project_id,
                active_timeline_id=active_timeline_id,
            )
            logger.info(
                "Hoàn tất đối soát: classified_state=%s, recommended_action=%s",
                assessment.classified_state,
                assessment.recommended_action,
            )
            self.assessment_completed.emit(assessment)
        except PermissionError as p_err:
            err_msg = f"Lỗi quyền truy cập (EACCES) trong quá trình đối soát: {p_err}"
            logger.warning(err_msg)
            self.assessment_io_error.emit(err_msg)
        except OSError as os_err:
            if os_err.errno in (errno.EACCES, errno.EPERM, errno.EIO):
                err_msg = f"Lỗi hệ thống tệp I/O ({os_err.errno}) trong quá trình đối soát: {os_err}"
                logger.warning(err_msg)
                self.assessment_io_error.emit(err_msg)
            else:
                err_msg = f"Lỗi hệ thống OS khi đối soát: {os_err}"
                logger.error(err_msg)
                self.assessment_failed.emit(err_msg)
        except Exception as exc:
            err_msg = f"Ngoại lệ khi đối soát phục hồi: {exc}"
            logger.exception(err_msg)
            self.assessment_failed.emit(err_msg)
