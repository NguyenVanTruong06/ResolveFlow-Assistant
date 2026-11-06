"""Dịch vụ Quản lý Lưu trữ Nhật ký Thao tác Thi công Append-Only (G07-07A6-I).

Module cung cấp:
- ExecutionJournalService: Quản lý ghi nối tiếp (append-only) các bản ghi JSONL, fsync từng dòng, kiểm tra tuần tự tăng dần (sequence monotonicity).
- Replay an toàn có giới hạn trần: Bounded file size, line size, record count.
- Phát hiện dòng cuối bị rách phi phá hủy (Non-destructive torn-tail detection): Bảo tồn 100% byte gốc, không tự ý truncate làm mất dấu vết pháp y.
- Reverse iterator phục vụ hoàn tác bù trừ: Lọc danh sách action COMMITTED chưa có ROLLBACK_DONE.

Tuân thủ nghiêm ngặt:
- 0 Resolve scripting API, 0 timeline mutation, 0 UI wiring.
- Fsync bắt buộc trên từng dòng trước khi tiến hành bước tiếp theo.
- Xử lý crash-consistency và in-doubt boundaries an toàn.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.contracts.execution_receipt_contracts import (
    JournalEntry,
    JournalReplayResult,
    MAX_JOURNAL_BYTES,
    MAX_JOURNAL_RECORDS,
    MAX_LINE_BYTES,
    TornTailDiagnostic,
    journal_entry_to_json_line,
    reject_duplicate_journal_keys,
)


class JournalStorageError(RuntimeError):
    """Lỗi nền tảng của hệ thống lưu trữ nhật ký thi công."""
    pass


class JournalNotFoundError(JournalStorageError):
    """Không tìm thấy tệp nhật ký thi công."""
    pass


class JournalCorruptedError(JournalStorageError):
    """Tệp nhật ký bị biến dạng dữ liệu ở phần giữa tệp (không phải torn tail)."""
    pass


class JournalSequenceError(JournalStorageError):
    """Số thứ tự sequence của bản ghi không tăng dần đều (seq != prev_seq + 1)."""
    pass


class JournalCapacityExceededError(JournalStorageError):
    """Dung lượng tệp hoặc số lượng bản ghi nhật ký vượt quá ngưỡng an toàn cho phép."""
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
        pass


class ExecutionJournalService:
    """Dịch vụ quản lý tệp nhật ký thi công Append-Only."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        self.journals_dir = os.path.join(self.base_dir, "journals")
        os.makedirs(self.journals_dir, exist_ok=True)

    def journal_path(self, execution_id: str) -> str:
        return os.path.join(self.journals_dir, f"{execution_id}.journal.jsonl")

    def append_entry(self, execution_id: str, entry: JournalEntry) -> None:
        """Ghi nối tiếp một bản ghi nhật ký và fsync ngay lập tức xuống đĩa.

        Ràng buộc:
        - Kiểm tra tính tuần tự của sequence: seq == prev_seq + 1.
        - Kiểm tra giới hạn dung lượng: MAX_JOURNAL_BYTES và MAX_JOURNAL_RECORDS.
        - Fsync descriptor của file; fsync directory nếu file mới tạo.
        """
        if not isinstance(entry, JournalEntry):
            raise ValueError("ERR_INVALID_ENTRY: entry phải là JournalEntry")

        target_path = self.journal_path(execution_id)
        is_new_file = not os.path.exists(target_path)

        # 1. Kiểm tra tính tăng dần đều của seq nếu file đã tồn tại
        if not is_new_file:
            curr_size = os.path.getsize(target_path)
            if curr_size > MAX_JOURNAL_BYTES:
                raise JournalCapacityExceededError(
                    f"ERR_JOURNAL_SIZE_EXCEEDED: Tệp nhật ký hiện tại {curr_size} bytes vượt ngưỡng {MAX_JOURNAL_BYTES}"
                )
            # Đọc nhanh seq của bản ghi cuối cùng hợp lệ
            replay_res = self.replay_journal(execution_id)
            if replay_res.entries:
                last_seq = replay_res.entries[-1].seq
                if entry.seq != last_seq + 1:
                    raise JournalSequenceError(
                        f"ERR_INVALID_SEQ: entry.seq ({entry.seq}) phải bằng last_seq + 1 ({last_seq + 1})"
                    )
            else:
                if entry.seq != 0 and entry.seq != 1:
                    raise JournalSequenceError(
                        f"ERR_INVALID_SEQ: entry đầu tiên phải có seq là 0 hoặc 1, nhận: {entry.seq}"
                    )
        else:
            if entry.seq != 0 and entry.seq != 1:
                raise JournalSequenceError(
                    f"ERR_INVALID_SEQ: entry đầu tiên phải có seq là 0 hoặc 1, nhận: {entry.seq}"
                )

        line_str = journal_entry_to_json_line(entry)
        line_bytes = line_str.encode("utf-8")

        # 2. Ghi nối tiếp và fsync
        with open(target_path, "ab") as f:
            f.write(line_bytes)
            f.flush()
            os.fsync(f.fileno())

        if is_new_file:
            _fsync_parent_dir(self.journals_dir)

    def replay_journal(self, execution_id: str) -> JournalReplayResult:
        """Đọc và phân tích toàn bộ tệp nhật ký một cách an toàn và phi phá hủy.

        Xử lý dòng cuối bị rách (torn tail):
        - Nếu dòng cuối không thể parse do crash giữa chừng, KHÔNG CẮT CỤT (truncate) tệp.
        - Giữ nguyên byte gốc trên đĩa.
        - Trả về danh sách các bản ghi hợp lệ kèm TornTailDiagnostic.
        """
        target_path = self.journal_path(execution_id)
        if not os.path.exists(target_path):
            raise JournalNotFoundError(f"ERR_JOURNAL_NOT_FOUND: Không tìm thấy nhật ký {execution_id}")

        file_size = os.path.getsize(target_path)
        if file_size > MAX_JOURNAL_BYTES:
            raise JournalCapacityExceededError(
                f"ERR_JOURNAL_TOO_LARGE: Dung lượng nhật ký {file_size} vượt quá {MAX_JOURNAL_BYTES}"
            )

        valid_entries: List[JournalEntry] = []
        torn_diagnostic: Optional[TornTailDiagnostic] = None
        is_clean = True
        total_bytes = 0

        with open(target_path, "rb") as f:
            lines = f.readlines()

        total_lines = len(lines)
        if total_lines > MAX_JOURNAL_RECORDS:
            raise JournalCapacityExceededError(
                f"ERR_TOO_MANY_RECORDS: Số lượng dòng {total_lines} vượt ngưỡng {MAX_JOURNAL_RECORDS}"
            )

        prev_seq: Optional[int] = None

        for idx, raw_line in enumerate(lines):
            line_len = len(raw_line)
            total_bytes += line_len
            if line_len > MAX_LINE_BYTES:
                raise JournalStorageError(
                    f"ERR_LINE_TOO_LARGE: Dòng {idx + 1} có dung lượng {line_len} vượt {MAX_LINE_BYTES} bytes"
                )

            is_last_line = (idx == total_lines - 1)

            # Thử decode UTF-8 và parse JSON
            try:
                line_str = raw_line.decode("utf-8").strip()
                if not line_str:
                    continue
                data = json.loads(line_str, object_pairs_hook=reject_duplicate_journal_keys)
                entry = JournalEntry.from_dict(data)

                # Kiểm tra seq tăng dần
                if prev_seq is not None and entry.seq != prev_seq + 1:
                    raise JournalSequenceError(
                        f"ERR_SEQUENCE_GAP: Dòng {idx + 1} có seq={entry.seq} không liền kề prev_seq={prev_seq}"
                    )
                prev_seq = entry.seq
                valid_entries.append(entry)

            except Exception as exc:
                if is_last_line and isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
                    # Dòng cuối bị lỗi decode UTF-8 hoặc JSON bị cắt dở dang mid-token -> Nhận diện là Torn Tail (Crash mid-write)
                    is_clean = False
                    torn_diagnostic = TornTailDiagnostic(
                        is_torn=True,
                        line_number=idx + 1,
                        raw_bytes=raw_line,
                        error_message=str(exc),
                    )
                else:
                    # Lỗi ở giữa tệp hoặc vi phạm schema/duplicate key (corrupted evidence) -> Raise JournalCorruptedError
                    raise JournalCorruptedError(
                        f"ERR_JOURNAL_CORRUPTED: Dòng {idx + 1} bị hỏng hoặc vi phạm quy tắc nhật ký: {exc}"
                    ) from exc

        return JournalReplayResult(
            entries=tuple(valid_entries),
            torn_tail=torn_diagnostic,
            is_clean=is_clean,
            total_bytes_read=total_bytes,
        )

    def replay_reverse_committed_entries(self, execution_id: str) -> Tuple[JournalEntry, ...]:
        """Lọc danh sách các thao tác đã COMMITTED nhưng chưa hoàn tất ROLLBACK_DONE.

        Phục vụ Động cơ Phục hồi (Recovery Engine) quét ngược để bù trừ Lift Delete an toàn.
        """
        replay_res = self.replay_journal(execution_id)
        entries = replay_res.entries

        # Tập hợp các seq đã rollback xong
        rolled_back_seqs: Set[int] = {
            e.seq for e in entries if e.stage == "ROLLBACK_DONE"
        }

        # Lọc ngược các action đã committed mà chưa rollback
        unrolled_committed: List[JournalEntry] = []
        for e in reversed(entries):
            if e.stage == "ACTION_COMMITTED" and e.seq not in rolled_back_seqs:
                unrolled_committed.append(e)

        return tuple(unrolled_committed)

    def create_isolated_recovery_copy(self, execution_id: str, suffix: str = "") -> str:
        """Tạo bản sao dự phòng bất biến của nhật ký phục vụ điều tra forensic mà không làm thay đổi bản gốc."""
        src_path = self.journal_path(execution_id)
        if not os.path.exists(src_path):
            raise JournalNotFoundError(f"ERR_JOURNAL_NOT_FOUND: {execution_id}")

        ext_suffix = suffix if suffix else f"recovery_{int(time.time())}"
        backup_path = f"{src_path}.{ext_suffix}"
        shutil.copyfile(src_path, backup_path)
        return backup_path
