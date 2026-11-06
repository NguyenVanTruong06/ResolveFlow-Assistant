# G07-07A7-D-R1: THIẾT KẾ ĐỐI SOÁT & PHÂN LOẠI PHỤC HỒI CHỈ ĐỌC (BẢN HIỆU CHỈNH R1)
*(Read-Only Recovery Decision, Classification and Reconciliation Architectural Design - Revision 1)*

> **Kế thừa & Căn cứ kỹ thuật:**
> - Quyết định nghiệm thu mã nguồn Codex: `docs/architecture/07A6-I-code-acceptance.md` (`ACCEPTED_WITH_RECORDED_LIMITS`, 198 tests passed in 31.70s, exit 0).
> - Thiết kế bền vững tiền đột biến: `docs/architecture/07A6-receipt-journal-recovery-design.md` (SHA-256: `d220218baf387377173018e6f5ff90954b5502b373db51fe0fd044e88dc44cc1`).
> - Kết quả thẩm định Codex cho 07A7-D: `docs/architecture/07A7-codex-review.md` (`CHANGES_REQUIRED` - 5 điểm hiệu chỉnh bắt buộc).
> - Phiếu công tác hiện hành: `.bridge/task.md` (`TASK_ID: G07-07A7-D-R1`, `MODE: DESIGN_ONLY_RECOVERY_RECONCILIATION_REFINEMENT`).
> - Trạng thái Gate hiện tại: **Gate G07 / Milestone M1 CHƯA HOÀN TẤT**; `ready_to_execute=False` được bảo toàn tuyệt đối; 0 timeline mutation, 0 executor creation.
> - **RÀNG BUỘC THIẾT KẾ THUẦN TÚY (DESIGN ONLY):** Chính xác 0 dòng sửa đổi trong `src/`, 0 sửa đổi test sản phẩm, 0 thực thi pytest/app/Resolve/AI model/mạng ngoài. Không commit/push/clean/stash git.

- **TASK_ID:** `G07-07A7-D-R1`
- **WORKER_LEVEL:** `HIGH`
- **MODE:** `DESIGN_ONLY_RECOVERY_RECONCILIATION_REFINEMENT`
- **MỤC TIÊU HIỆU CHỈNH CHÍNH (5 ĐIỂM BẮT BUỘC TỪ CODEX):**
  1. **Quyền sở hữu tiến trình độc quyền qua OS Advisory Lock:** Phân loại tiến trình đang hoạt động (`STATE_CLEAN_ACTIVE_LOCKED`) dựa DUY NHẤT vào kết quả thăm dò khóa cố vấn hệ điều hành không chặn (`fcntl.flock` / `msvcrt.locking`). Thông tin PID chỉ là siêu dữ liệu chẩn đoán (`diagnostic_recorded_pid`), không có giá trị phân định quyền sở hữu do rủi ro xoay vòng PID (PID reuse), khác biệt không gian tên (container/PID namespaces) và ranh giới quyền hạn tín hiệu OS.
  2. **Dòng rách là hiện tượng quan sát vật lý (Observed Anomaly), không suy đoán nguyên nhân sự cố:** Coi dòng cuối JSONL bị ngắt quãng thuần túy là dị thường quan sát trên tệp (`OBSERVED_INCOMPLETE_TRAILING_RECORD`), cấm phỏng đoán nguyên nhân cắt điện hay hoảng loạn nhân (kernel panic). Đồng thời thừa nhận minh bạch: nhật ký không thể chứng minh các bản ghi đã commit trước đó là tập hợp toàn vẹn các đột biến thực tế trên DaVinci Resolve.
  3. **Chẩn đoán phục hồi có giới hạn trần và được làm sạch an toàn (Bounded & Sanitized Diagnostics):** Loại bỏ hoàn toàn raw bytes và arbitrary dictionary tự do trong DTOs. Thay thế bằng các trường kích thước giới hạn nghiêm ngặt: số lượng byte (`torn_tail_byte_count`), băm kiểm tra (`torn_tail_sha256`), bản xem trước đã thoát ký tự điều khiển (`torn_tail_preview_escaped` tối đa 256 ký tự) và chuỗi tóm tắt tham số làm sạch (`action_params_summary` tối đa 512 ký tự) nhằm ngăn chặn tràn bộ nhớ và tấn công chèn mã điều khiển giao diện (UI injection).
  4. **Chỉ mục Idempotency hỏng hoặc thiếu là trạng thái đóng-an-toàn (Fail-Closed):** Mọi trường hợp tệp chỉ mục idempotency bị biến dạng hoặc thiếu hụt khi receipt tồn tại đều bắt buộc phân loại là `STATE_ARTIFACT_CORRUPTED_OR_MISSING` / `STATE_IDEMPOTENCY_UNVERIFIABLE`. Tuyệt đối cấm coi đây là trạng thái cho phép chạy lại hoặc phát lại kịch bản. Bảo tồn nguyên trạng phục vụ điều tra.
  5. **Khuyến nghị là hành động bù trừ ứng viên chờ người vận hành phê duyệt (Candidate Compensation for Operator Review):** Bản ghi `ACTION_COMMITTED` là bằng chứng ứng dụng đã ghi nhận kết quả tại thời điểm thi công, KHÔNG PHẢI bằng chứng độc lập rằng timeline DaVinci Resolve hiện tại vẫn chứa item đó. Đổi tên khuyến nghị thành hành động bù trừ ứng viên; tuyệt đối không hứa hẹn rollback hoàn hảo hay bảo đảm khôi phục timeline nguyên trạng.

---

## 1. ĐỐI CHIẾU CALLGRAPH HIỆN TRẠNG & HỢP ĐỒNG THỰC TẾ (SOURCE-GROUNDED TRACE)

Đối chiếu trực tiếp mã nguồn hiện hành tại repository, phân loại rõ ràng giữa các thành phần đã kiểm chứng bằng mã nguồn thực tế (`[CODE_VERIFIED]`), các cơ chế đề xuất mới (`[PROPOSED]`), và các ranh giới bất định (`[AMBIGUITY_BOUNDARIES]`):

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                  HẠ TẦNG LƯU TRỮ ĐÃ NGHIỆM THU (CODE_VERIFIED in G07-07A6-I)                          |
+-----------------------------------------------------------------------------------------------------------------------+
 [src/core/contracts/execution_receipt_contracts.py] [CODE_VERIFIED]
      |-- DTOs: ExecutionReceipt, JournalEntry, SourceFileDigestBinding, CatalogAssetDigestBinding, TargetTrackReservation
      |-- Chẩn đoán: TornTailDiagnostic, JournalReplayResult
      |-- Idempotency: compute_idempotency_key(proposal_id, rev, approval_id, project_id, timeline_id)
      +-- Giới hạn trần: MAX_RECEIPT_BYTES (5 MiB), MAX_LINE_BYTES (64 KiB), MAX_JOURNAL_BYTES (50 MiB), MAX_JOURNAL_RECORDS (50,000)

 [src/services/execution_receipt_service.py] [CODE_VERIFIED]
      |-- ExecutionReceiptService: create_receipt, load_receipt, transition_state
      |-- Ghi đĩa 2 chặng nguyên tử: .tmp.<pid>.<uuid> -> flush() -> fsync(fd) -> os.replace -> fsync parent dir
      |-- Khóa Idempotency bền vững: check_idempotency, IdempotencyDuplicateActiveError, IdempotencyDuplicateCompletedError
      +-- Khóa độc quyền tiến trình: ExecutionLockContext (fcntl.flock / msvcrt.locking trên .lock)

 [src/services/execution_journal_service.py] [CODE_VERIFIED]
      |-- ExecutionJournalService: append_entry (WAL flush + fsync per line), replay_journal
      |-- Phát hiện torn-tail phi phá hủy: TornTailDiagnostic (bảo tồn 100% byte gốc, KHÔNG truncate)
      |-- Reverse iterator: replay_reverse_committed_entries (lọc action COMMITTED chưa có ROLLBACK_DONE)
      +-- Tiện ích sao lưu cách ly: create_isolated_recovery_copy

 [src/core/resolve_bridge.py] [CODE_VERIFIED]
      +-- query_active_timeline_authority(): Đọc project_id, timeline_id, timeline_name, RationalFPS (fail-closed, 0 fallback)
```

### 1.1. Tầng Phục hồi & Đối soát Đề xuất (`[PROPOSED]`)

```
+-----------------------------------------------------------------------------------------------------------------------+
|                              G07-07A7-R1: ĐỐI SOÁT & PHÂN LOẠI PHỤC HỒI CHỈ ĐỌC (PROPOSED)                           |
+-----------------------------------------------------------------------------------------------------------------------+
 [RecoveryCoordinator (MỚI - Bộ điều phối chỉ đọc an toàn)]
      |
      |--> Thăm dò quyền sở hữu khóa cố vấn (Authoritative Advisory Lock Probe):
      |         |-- Mở tệp `.lock` và thử chiếm khóa non-blocking (LOCK_EX | LOCK_NB)
      |         |-- NẾU bị chặn (BlockingIOError / EAGAIN / EACCES):
      |         |     -> Khóa ĐANG BỊ CHIẾM bởi tiến trình khác -> Xếp loại STATE_CLEAN_ACTIVE_LOCKED
      |         +-- NẾU chiếm thành công:
      |               -> Khóa RẢNH (không bị chiếm) -> Lập tức nhả khóa (LOCK_UN) & đóng fd.
      |               -> Đọc PID trong receipt/lock chỉ như `diagnostic_recorded_pid` (không dùng làm quyền sở hữu).
      |
      |--> Nạp và kiểm tra tính toàn vẹn hồ sơ (Fail-Closed Artifact Verification):
      |         |-- Nạp ExecutionReceipt (báo STATE_ARTIFACT_CORRUPTED_OR_MISSING nếu hỏng/thiếu)
      |         |-- Kiểm tra tệp chỉ mục idempotency tương ứng (báo STATE_ARTIFACT_CORRUPTED_OR_MISSING nếu hỏng/thiếu)
      |         +-- Nạp ExecutionJournal qua replay_journal (phát hiện torn-tail hoặc lỗi cấu trúc JSONL)
      |
      |--> Thăm dò trạng thái DaVinci Resolve hiện tại (Read-Only Target Identity Matching):
      |         +-- Gọi query_active_timeline_authority() để đối chiếu project_id & timeline_id
      |
      v
 [RecoveryClassifierService (MỚI - Thuật toán phân loại tất định thuần túy)]
      |
      |--> Xử lý phân tích (Pure Deterministic Classification):
      |         |-- Phân loại 1 trong 8 Crash States (STATE_CLEAN_TERMINAL đến STATE_AMBIGUOUS_IN_DOUBT)
      |         |-- Trích xuất danh sách hành động ứng viên bù trừ (CandidateCompensatingAction)
      |         |-- Trích xuất danh sách hành động bất định (AmbiguousActionDetail)
      |         +-- Giới hạn trần và làm sạch dữ liệu chẩn đoán (Bounded & Sanitized Preview / Summaries)
      |
      +--> Xuất bản kết quả:
                +-- RecoveryAssessment (Frozen DTO chỉ đọc, chứa khuyến nghị CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW)
```

---

## 2. LƯỢC ĐỒ PHÂN LOẠI TRẠNG THÁI SỰ CỐ TẤT ĐỊNH (DETERMINISTIC CRASH STATE CLASSIFICATION)

Hệ thống xác lập **8 trạng thái sự cố tất định**, loại bỏ hoàn toàn các phỏng đoán heuristics và giả định nhân quả:

```
                                  SƠ ĐỒ PHÂN NHÁNH TRẠNG THÁI SỰ CỐ TẤT ĐỊNH
                                                       |
                             +-------------------------+-------------------------+
                             | Kiểm tra tính toàn vẹn hồ sơ lưu trữ              |
                             | (Receipt hợp lệ VÀ Idempotency Index hợp lệ)?     |
                             +-------------------------+-------------------------+
                                        | Có                                | Không / Corrupted / Missing
                                        v                                   v
                  +---------------------+---------------------+    [STATE_ARTIFACT_CORRUPTED_OR_MISSING]
                  | Trạng thái của Receipt?                   |    (Khóa phân tích, giữ nguyên hiện trường)
                  +---------------------+---------------------+
                   /          |                  |           \
          COMPLETED     INITIALIZED          EXECUTING       ROLLED_BACK / ABORTED
              |               |                  |                     |
              |         Journal rỗng?            |                     v
              |         /           \            |            [STATE_CLEAN_TERMINAL]
            Có v       v Không       |           |            (NO_ACTION_NEEDED)
      [STATE_CLEAN_TERMINAL]         |           v
                  [STATE_INITIALIZED_NO_JOURNAL] Thăm dò OS Advisory Lock không chặn?
                                                 /                  \
                                     Bị chặn    v                    v Không bị chặn (Khóa rảnh)
                            [STATE_CLEAN_ACTIVE_LOCKED]       +------------------------------------+
                            (Tiến trình khác đang chạy)       | Dòng cuối cùng của Journal là gì?  |
                                                              +------------------------------------+
                                                               /              |                 \
                                                   ACTION_COMMITTED     ACTION_PENDING        TORN TAIL
                                                          |                   |                  |
                                                          v                   v                  v
                                                [STATE_COMMITTED_    [STATE_AMBIGUOUS_   [STATE_TORN_TAIL_
                                                CANDIDATE_           IN_DOUBT]           DETECTED]
                                                COMPENSATION]        (Manual Review      (Observed Tail
                                                (Candidate review)    Required)           Anomaly)
```

### 2.1. Chi tiết 8 Trạng thái Sự cố Tất định:

1. **`STATE_CLEAN_TERMINAL` (Hoàn tất Sạch sẽ):**
   - **Đặc trưng:** Receipt có `state` là `COMPLETED`, `ROLLED_BACK`, hoặc `ABORTED`. Chỉ mục idempotency đồng bộ. Tệp nhật ký kết thúc bằng `FINISH` hoặc `ROLLBACK_DONE`. Thăm dò lock xác nhận không có tiến trình nào đang chiếm khóa.
   - **Đánh giá:** Phiên thi công đã kết thúc hoàn hảo hoặc đã được hủy/hoàn tác sạch sẽ trong quá khứ.
   - **Khuyến nghị:** `NO_ACTION_NEEDED` (Không cần can thiệp).

2. **`STATE_INITIALIZED_NO_JOURNAL` (Khởi tạo nhưng Chưa Phát Đột biến):**
   - **Đặc trưng:** Receipt có `state == "INITIALIZED"`, nhưng tệp `.journal.jsonl` không tồn tại hoặc có kích thước 0 byte. Thăm dò OS advisory lock xác nhận khóa rảnh.
   - **Đánh giá:** Tiến trình dừng sau khi tạo receipt nhưng TRƯỚC KHI phát ra bất kỳ lệnh đột biến Resolve nào và trước khi ghi dòng journal đầu tiên.
   - **Khuyến nghị:** `SAFE_TO_ABORT_AFTER_CONFIRMATION` (Người vận hành xác nhận đánh dấu receipt chuyển sang `ABORTED` và dọn lock mồ côi mà không cần can thiệp DaVinci Resolve).

3. **`STATE_CLEAN_ACTIVE_LOCKED` (Tiến trình Đang Chiếm Khóa Hệ Điều Hành Hợp lệ):**
   - **Đặc trưng:** Thăm dò khóa cố vấn hệ điều hành không chặn (`fcntl.flock` trên POSIX hoặc `msvcrt.locking` trên Windows) trả về mã lỗi `BlockingIOError`, `EAGAIN`, `EWOULDBLOCK`, hoặc `EACCES`.
   - **Đánh giá:** Có một tiến trình đang tích cực chiếm giữ quyền sở hữu phiên làm việc. Siêu dữ liệu PID ghi nhận trong receipt/lock (`diagnostic_recorded_pid`) chỉ phục vụ hiển thị thông tin hỗ trợ gỡ lỗi.
   - **Khuyến nghị:** `CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB` (Tuyệt đối cấm can thiệp, cấm đọc ghi đè hoặc can thiệp phiên đang thi công).

4. **`STATE_COMMITTED_CANDIDATE_COMPENSATION` (Đã Commit Một Phần Theo Hồ Sơ Ứng Dụng):**
   - **Đặc trưng:** Receipt có `state == "EXECUTING"`, thăm dò OS advisory lock xác nhận khóa rảnh. Dòng cuối cùng hợp lệ của journal là `ACTION_COMMITTED` hoặc `ACTION_FAILED`.
   - **Đánh giá:** Ứng dụng ghi nhận một số clip đã được gửi thành công tới DaVinci Resolve trước khi tiến trình dừng. Tuy nhiên, bản ghi này chỉ là bằng chứng rằng ứng dụng đã nhận phản hồi API tại thời điểm đó; nó KHÔNG PHẢI bằng chứng độc lập rằng timeline Resolve hiện tại vẫn còn giữ nguyên trạng các clip này (người dùng có thể đã chỉnh sửa hoặc xóa thủ công).
   - **Khuyến nghị:** `CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW` (Trích xuất danh sách ứng viên clip ID cần xem xét lift-delete bù trừ; bắt buộc người vận hành đối soát và phê duyệt trước khi thi công; không cam kết khôi phục timeline hoàn hảo).

5. **`STATE_TORN_TAIL_DETECTED` (Dị Thường Dòng Cuối Bị Ngắt Quãng Đã Quan Sát):**
   - **Đặc trưng:** Replay journal phát hiện dòng cuối bị ngắt quãng phi cú pháp (`is_torn == True`). Thăm dò OS advisory lock xác nhận khóa rảnh.
   - **Đánh giá:** Quan sát thấy một đoạn văn bản/byte dở dang ở cuối tệp nhật ký (`OBSERVED_INCOMPLETE_TRAILING_RECORD`). Tuyệt đối không suy đoán nguyên nhân sự cố là do cắt điện đột ngột hay lỗi tiến trình. Các dòng ghi nhận hợp lệ phía trước vẫn có thể trích xuất làm ứng viên bù trừ sau khi người vận hành xem xét.
   - **Khuyến nghị:** `TORN_TAIL_OBSERVED_OPERATOR_REVIEW` (Bảo tồn 100% tệp gốc không truncate; trích xuất chẩn đoán dòng rách đã được làm sạch an toàn; cung cấp danh sách ứng viên bù trừ từ các dòng hợp lệ trước đó cho người vận hành).

6. **`STATE_AMBIGUOUS_IN_DOUBT` (Ranh Giới Bất Định Cốt Lõi - Không Thể Xác Minh Từ Đĩa):**
   - **Đặc trưng:** Dòng cuối cùng của journal là `ACTION_PENDING`, không có bản ghi `ACTION_COMMITTED` hay `ACTION_FAILED` tương ứng. Thăm dò OS advisory lock xác nhận khóa rảnh.
   - **Đánh giá:** Tiến trình dừng sau khi ghi lệnh chuẩn bị gửi qua IPC socket tới Resolve, nhưng trước khi kết quả được xác nhận và ghi bền vững xuống đĩa. Không thể chứng minh từ đĩa xem clip đã thực sự được chèn vào timeline hay chưa.
   - **Khuyến nghị:** `MANUAL_TIMELINE_INSPECTION_REQUIRED` (TUYỆT ĐỐI CẤM TỰ ĐỘNG CHẠY LẠI, TỰ ĐỘNG XÓA HOẶC SUY ĐOÁN TRẠNG THÁI; người vận hành bắt buộc phải đối chiếu trực quan trên giao diện DaVinci Resolve).

7. **`STATE_IDEMPOTENCY_DUPLICATE_BLOCKED` (Trùng Lặp Khóa Idempotency Bền Vững):**
   - **Đặc trưng:** Yêu cầu kiểm tra hoặc tạo mới có `idempotency_key` trùng khớp với một bản ghi idempotency đã tồn tại ở trạng thái `COMPLETED`, `INITIALIZED`, hoặc `EXECUTING`.
   - **Đánh giá:** Nỗ lực chạy lại cùng một kịch bản đã được phê duyệt trên cùng một target timeline.
   - **Khuyến nghị:** `REJECT_DUPLICATE` (Chặn đứng thi công trùng lặp, bảo vệ dữ liệu hiện hữu).

8. **`STATE_ARTIFACT_CORRUPTED_OR_MISSING` (Hồ Sơ Lưu Trữ Biến Dạng hoặc Chỉ Mục Idempotency Không Khả Dụng):**
   - **Đặc trưng:** Tệp receipt không thể parse cú pháp JSON, hoặc journal bị hỏng dữ liệu ở giữa tệp, HOẶC tệp chỉ mục idempotency bị thiếu hụt/hỏng khi receipt tồn tại (`STATE_IDEMPOTENCY_UNVERIFIABLE`).
   - **Đánh giá:** Hệ thống lưu trữ bị can thiệp ngoài luồng hoặc lỗi I/O vật lý. Việc thiếu hoặc hỏng chỉ mục idempotency tuyệt đối KHÔNG ĐƯỢC coi là tín hiệu an toàn để chạy lại kịch bản.
   - **Khuyến nghị:** `FORENSIC_QUARANTINE_REQUIRED` (Khóa toàn bộ thao tác, giữ nguyên hiện trường tệp đĩa, cấm phát lại hoặc chạy lại dưới mọi hình thức, yêu cầu kỹ thuật viên can thiệp).

---

## 3. LƯỢC ĐỒ DTO ĐÁNH GIÁ & KHUYẾN NGHỊ PHỤC HỒI CHỈ ĐỌC (READ-ONLY RECOVERY DTOs)

Mô hình hóa dữ liệu thẩm định phục hồi bằng các dataclass bất biến (frozen DTOs). Toàn bộ dữ liệu chẩn đoán được giới hạn trần nghiêm ngặt và làm sạch để phòng vệ tràn bộ nhớ và tấn công chèn ký tự điều khiển giao diện:

```python
"""Định nghĩa hợp đồng DTO Đánh giá & Phân loại Phục hồi Chỉ đọc (G07-07A7-R1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Literal

# Giới hạn an toàn nghiêm ngặt cho chẩn đoán phục hồi
MAX_TORN_TAIL_PREVIEW_BYTES: int = 256
MAX_PARAMS_SUMMARY_CHARS: int = 512
MAX_UNCERTAINTY_REASON_CHARS: int = 256

CrashStateEnum = Literal[
    "STATE_CLEAN_TERMINAL",
    "STATE_INITIALIZED_NO_JOURNAL",
    "STATE_CLEAN_ACTIVE_LOCKED",
    "STATE_COMMITTED_CANDIDATE_COMPENSATION",
    "STATE_TORN_TAIL_DETECTED",
    "STATE_AMBIGUOUS_IN_DOUBT",
    "STATE_IDEMPOTENCY_DUPLICATE_BLOCKED",
    "STATE_ARTIFACT_CORRUPTED_OR_MISSING",
]

RecoveryActionType = Literal[
    "NO_ACTION_NEEDED",                           # Phiên sạch đã hoàn tất, không làm gì
    "SAFE_TO_ABORT_AFTER_CONFIRMATION",           # Operator xác nhận chuyển sang ABORTED
    "CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB",      # Khóa OS đang bị chiếm, cấm can thiệp
    "CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW", # Đề xuất ứng viên bù trừ chờ operator duyệt
    "TORN_TAIL_OBSERVED_OPERATOR_REVIEW",         # Dòng rách quan sát được, chờ operator duyệt
    "MANUAL_TIMELINE_INSPECTION_REQUIRED",        # Bất định: Bắt buộc operator kiểm tra mắt thường
    "REJECT_DUPLICATE",                           # Trùng lặp idempotency, chặn thi công
    "FORENSIC_QUARANTINE_REQUIRED",               # Hồ sơ/chỉ mục hỏng, khóa cách ly điều tra
]


@dataclass(frozen=True)
class CandidateCompensatingAction:
    """Chi tiết một hành động ứng viên được ứng dụng ghi nhận COMMITTED có thể xem xét bù trừ."""
    seq: int
    action_type: str
    resolve_item_id: str
    track_type: str
    track_index: int
    clip_name: Optional[str]
    action_params_summary: str  # Chuỗi tóm tắt tham số đã làm sạch (tối đa 512 ký tự, không chứa control chars)


@dataclass(frozen=True)
class AmbiguousActionDetail:
    """Chi tiết hành động rơi vào vùng bất định (ACTION_PENDING chưa có xác nhận kết quả)."""
    seq: int
    action_type: str
    track_type: str
    track_index: int
    action_params_summary: str  # Chuỗi tóm tắt tham số đã làm sạch (tối đa 512 ký tự)
    uncertainty_reason: str     # Lý do bất định (tối đa 256 ký tự)


@dataclass(frozen=True)
class TargetIdentityMatchReport:
    """Kết quả đối chiếu danh tính Project và Timeline giữa Receipt và DaVinci Resolve thực tế."""
    receipt_project_id: str
    receipt_timeline_id: str
    active_project_id: Optional[str]
    active_timeline_id: Optional[str]
    is_project_matched: bool
    is_timeline_matched: bool
    drift_detected: bool
    authority_available: bool


@dataclass(frozen=True)
class RecoveryAssessment:
    """Hồ sơ đánh giá toàn diện tình trạng sự cố và khuyến nghị phục hồi (Read-Only Frozen DTO)."""
    execution_id: str
    idempotency_key: str
    classified_state: CrashStateEnum
    recommended_action: RecoveryActionType
    requires_operator_confirmation: bool
    explanation_for_operator: str

    # Quyền sở hữu độc quyền & Chẩn đoán tiến trình (Advisory Lock Authority vs Diagnostic PID)
    is_advisory_lock_held: bool              # Thước đo thẩm quyền duy nhất dựa trên OS advisory lock probe
    diagnostic_recorded_pid: Optional[int]   # PID ghi nhận trong receipt/lock (CHỈ DÙNG ĐỂ CHẨN ĐOÁN)

    # Thống kê nhật ký & Ứng viên bù trừ
    total_journal_entries_count: int
    candidate_compensations: Tuple[CandidateCompensatingAction, ...]
    ambiguous_actions: Tuple[AmbiguousActionDetail, ...]

    # Chẩn đoán dòng rách an toàn (Bounded & Sanitized Diagnostics, không chứa raw bytes)
    torn_tail_detected: bool
    torn_tail_line_number: Optional[int]
    torn_tail_byte_count: int                # Số lượng byte của đoạn rách quan sát được
    torn_tail_sha256: Optional[str]          # Mã băm SHA-256 hex 64 ký tự của đoạn rách phục vụ forensic
    torn_tail_preview_escaped: Optional[str] # Chuỗi xem trước tối đa 256 ký tự đã thoát ký tự điều khiển

    # Đối chiếu danh tính Resolve thực tế
    target_match_report: TargetIdentityMatchReport
```

---

## 4. MÔ HÌNH SỰ CỐ ĐỐI CHIẾU & RANH GIỚI BẤT ĐỊNH (CRASH MODELING & AMBIGUITY BOUNDARY)

### 4.1. Bản Chất Tách Rời Giữa Nhật Ký Ứng Dụng và Trạng Thái Timeline Resolve Thực Tế
- **Nguyên lý phân ly trạng thái (Decoupled State Reality):** 
  Socket IPC giữa Python và DaVinci Resolve daemon không có cơ chế phân tán giao dịch hai chặng (Distributed 2PC). 
  Một bản ghi `ACTION_COMMITTED` trong nhật ký `.journal.jsonl` là **bằng chứng duy nhất rằng ứng dụng đã nhận được kết quả thành công từ API Resolve tại thời điểm đó**. 
  Bản ghi này **KHÔNG PHẢI là bằng chứng bảo đảm rằng timeline Resolve hiện tại vẫn chứa nguyên vẹn item đó**, bởi vì:
  1. Người vận hành hoặc dựng phim (editor) có thể đã dùng chuột di chuyển, cắt gọt, kéo dài, hoặc xóa item trên timeline sau thời điểm sự cố.
  2. Một script tự động hóa khác hoặc một plugin khác có thể đã can thiệp vào timeline.
  3. Dự án DaVinci Resolve có thể đã được hoàn tác bằng lệnh Cmd/Ctrl+Z thủ công trong giao diện Resolve.
- **Hệ quả thiết kế:** Bất kỳ thao tác hoàn tác nào trong tương lai chỉ được xem là **Hành động Bù trừ Ứng viên (Candidate Compensation)** và bắt buộc phải được người vận hành xem xét trực quan trước khi thực hiện. Hệ thống **tuyệt đối không cam kết hoàn tác tự động hoàn hảo hay bảo đảm khôi phục timeline nguyên trạng**.

### 4.2. Bảng Phân Tích Các Khoảng Thời Gian Dừng Đột Ngột (Interruption Windows Analysis)

| Điểm gián đoạn (Interruption Window) | Trạng thái trên Đĩa (`Receipt` & `Journal`) | Trạng thái Thực tế trong Resolve | Khả năng Chứng minh từ Hồ sơ Lưu trữ | Phân loại Bắt buộc |
| :--- | :--- | :--- | :--- | :--- |
| **Window 1: Trước khi ghi WAL `ACTION_PENDING`** | Dòng cuối là `ACTION_COMMITTED` của bước trước. | Chưa nhận lệnh bước tiếp theo. | **Chứng minh được**: Ứng dụng chưa phát lệnh bước mới. | `STATE_COMMITTED_CANDIDATE_COMPENSATION` |
| **Window 2: Đang gửi IPC socket tới Resolve** | Dòng cuối là `ACTION_PENDING`. | Socket IPC có thể đã nhận một phần hoặc chưa nhận. | **BẤT ĐỊNH (KHÔNG THỂ CHỨNG MINH)**. | `STATE_AMBIGUOUS_IN_DOUBT` |
| **Window 3: Resolve đang chèn clip lên Timeline** | Dòng cuối là `ACTION_PENDING`. | Clip đang được tạo hoặc đã xuất hiện trên timeline. | **BẤT ĐỊNH (KHÔNG THỂ CHỨNG MINH)**. | `STATE_AMBIGUOUS_IN_DOUBT` |
| **Window 4: Resolve chèn xong, trước khi Python nhận kết quả** | Dòng cuối là `ACTION_PENDING`. | Clip đã nằm hoàn chỉnh trên timeline Resolve. | **BẤT ĐỊNH (KHÔNG THỂ CHỨNG MINH)**. | `STATE_AMBIGUOUS_IN_DOUBT` |
| **Window 5: Python nhận kết quả, đang ghi `ACTION_COMMITTED`** | Dòng cuối bị ngắt quãng (`OBSERVED_INCOMPLETE_TRAILING_RECORD`). | Clip đã nằm trên timeline Resolve. | **Quan sát thấy dị thường dòng cuối**: Không khẳng định nguyên nhân. | `STATE_TORN_TAIL_DETECTED` |
| **Window 6: Sau khi `ACTION_COMMITTED` đã `fsync()`** | Dòng cuối là `ACTION_COMMITTED` có `resolve_item_id`. | Clip đã được ứng dụng ghi nhận tạo thành công. | **Chứng minh được ghi nhận**: Ứng dụng đã ghi nhận kết quả. | `STATE_COMMITTED_CANDIDATE_COMPENSATION` |

### 4.3. Ranh Giới Bất Định Cốt Lõi Khi Gặp `ACTION_PENDING` Mồ Côi
- Khi dòng cuối cùng của nhật ký là `ACTION_PENDING`:
  - Hệ thống **tuyệt đối không được suy đoán** lệnh đã thành công hay thất bại.
  - Nếu tự động chạy lại -> Nguy cơ chèn clip trùng lặp đè lên timeline.
  - Nếu tự động xóa mò -> Nguy cơ xóa nhầm clip có sẵn của người dùng.
- **Quy tắc Bất biến:** Toàn bộ trường hợp `ACTION_PENDING` mồ côi bắt buộc phải phân loại là `STATE_AMBIGUOUS_IN_DOUBT` kèm khuyến nghị `MANUAL_TIMELINE_INSPECTION_REQUIRED`.

---

## 5. RÀO CHẮN DANH TÍNH ĐÍCH & QUY TẮC PHÊ DUYỆT CỦA OPERATOR (TARGET IDENTITY & OPERATOR APPROVAL)

### 5.1. Thẩm Quyền Sở Hữu Khóa Hệ Điều Hành (Advisory Lock Ownership Authority)
- **Quy trình thăm dò không chặn (Non-blocking Lock Probe Protocol):**
  1. Bộ điều phối mở file descriptor tới tệp `.lock` với cờ `os.O_RDWR` (hoặc `os.O_CREAT | os.O_RDWR`).
  2. Thực hiện gọi kiểm tra khóa không chặn:
     - Trên POSIX: `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`.
     - Trên Windows: `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)`.
  3. Xử lý kết quả thăm dò tất định:
     - **Nếu gặp lỗi chặn (`BlockingIOError`, `EAGAIN`, `EWOULDBLOCK`, `EACCES`):**
       Khóa đang được một tiến trình khác tích cực nắm giữ. Đóng `fd` ngay lập tức. Xác lập trạng thái `is_advisory_lock_held = True` -> Phân loại `STATE_CLEAN_ACTIVE_LOCKED`.
     - **Nếu chiếm khóa thành công:**
       Khóa hoàn toàn tự do (không có tiến trình nào đang chạy phiên này). Bộ điều phối **lập tức nhả khóa** (`fcntl.flock(fd, fcntl.LOCK_UN)` / `msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)`) và đóng `fd`. Xác lập trạng thái `is_advisory_lock_held = False`.
- **Vai trò của PID:**
  - PID đọc được từ tệp `.lock` hoặc `receipt` chỉ được gán vào trường `diagnostic_recorded_pid` phục vụ hiển thị log cho kỹ thuật viên.
  - Tuyệt đối cấm sử dụng `os.kill(pid, 0)` làm thẩm quyền xác định tiến trình còn sở hữu khóa, vì:
    - Rủi ro xoay vòng PID (PID reuse) của kernel: một tiến trình hệ thống khác hoàn toàn ngẫu nhiên có thể mang cùng PID đó sau khi tiến trình cũ chết.
    - Không gian tên container / chroot / PID namespace: PID bên ngoài và bên trong có thể khác biệt.
    - Ranh giới quyền hạn: `os.kill` có thể trả về `EPERM` đối với tiến trình thuộc người dùng khác.

### 5.2. Rào Chắn Danh Tính Đích Tuyệt Đối (Strict Target Identity Gate)
1. **Kiểm tra DaVinci Resolve Đang Mở:**
   - Gọi `query_active_timeline_authority()`.
   - Nếu DaVinci Resolve chưa bật hoặc chưa mở project/timeline: `authority_available = False`. Báo cáo trạng thái đích chưa sẵn sàng, cấm đề xuất thi công bù trừ.
2. **Khớp Danh Tính Mạnh (Strong UID Matching):**
   - So sánh:
     $$\text{active\_project\_id} == \text{receipt.project\_id} \quad \text{VÀ} \quad \text{active\_timeline\_id} == \text{receipt.timeline\_id}$$
   - Nếu có bất kỳ sự sai lệch nào (`drift_detected = True`):
     - Gắn cờ cảnh báo danh tính đích bị lệch (`TargetIdentityDrift`).
     - Khóa cứng toàn bộ khả năng thực thi bù trừ; yêu cầu người vận hành mở đúng Project và Timeline ban đầu trong DaVinci Resolve.

### 5.3. Quy Tắc Phê Duyệt Của Người Vận Hành (Operator Approval Policy)
- Trong mọi tình huống sự cố:
  - Tầng đối soát chỉ xuất bản bản đánh giá `RecoveryAssessment` kèm danh sách ứng viên `candidate_compensations`.
  - Giao diện người dùng phải hiển thị rõ ràng cảnh báo:
    *"Phát hiện phiên thi công gián đoạn. Ứng dụng đã ghi nhận 3 clip chèn trên track V1. Đây là danh sách ứng viên bù trừ cần bạn kiểm tra trực quan trên Timeline Resolve trước khi quyết định hoàn tác."*
  - Chỉ khi người vận hành bấm nút xác nhận phê duyệt có chủ đích, hệ thống mới được phép chuyển giao danh sách sang gateway thi công ở các milestone sau.

---

## 6. MA TRẬN KIỂM CHỨNG HỮU HẠN (FINITE RECONCILIATION FAULT MATRIX)

Ma trận kiểm chứng 12 kịch bản phân loại đối soát độc lập, bao quát toàn diện các trường hợp biên và 5 điểm hiệu chỉnh:

| Mã test | Kịch bản kiểm chứng sự cố | Dữ liệu đầu vào (`Receipt` / `Journal` / `Lock Probe` / `Idempotency`) | Trạng thái phân loại kỳ vọng (`classified_state`) | Khuyến nghị hành động (`recommended_action`) | Quyền Lock OS (`is_advisory_lock_held`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-REC-01** | **Clean Terminal Completed** | Receipt `COMPLETED`, Journal có dòng `FINISH`, Lock probe: Rảnh, Idemp: Valid. | `STATE_CLEAN_TERMINAL` | `NO_ACTION_NEEDED` | `False` |
| **TC-REC-02** | **Clean Terminal Rolled Back** | Receipt `ROLLED_BACK`, Journal có `ROLLBACK_DONE`, Lock probe: Rảnh, Idemp: Valid. | `STATE_CLEAN_TERMINAL` | `NO_ACTION_NEEDED` | `False` |
| **TC-REC-03** | **Crash Before First Journal Line** | Receipt `INITIALIZED`, Journal rỗng / không tồn tại, Lock probe: Rảnh, Idemp: Valid. | `STATE_INITIALIZED_NO_JOURNAL` | `SAFE_TO_ABORT_AFTER_CONFIRMATION` | `False` |
| **TC-REC-04** | **Active Process Running (Advisory Lock Held)** | Receipt `EXECUTING`, Lock probe: **BỊ CHẶN** (`EAGAIN`/`EWOULDBLOCK`), PID ghi nhận mang tính chẩn đoán. | `STATE_CLEAN_ACTIVE_LOCKED` | `CONCURRENT_JOB_RUNNING_DO_NOT_DISTURB` | `True` |
| **TC-REC-05** | **Clean Partial Commit (1 Clip)** | Receipt `EXECUTING`, Lock probe: Rảnh, Journal kết thúc bằng `ACTION_COMMITTED` seq 1. | `STATE_COMMITTED_CANDIDATE_COMPENSATION` | `CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW` | `False` |
| **TC-REC-06** | **Clean Partial Commit (Multiple Clips)** | Receipt `EXECUTING`, Lock probe: Rảnh, 3 clips COMMITTED, clip 4 FAILED. | `STATE_COMMITTED_CANDIDATE_COMPENSATION` | `CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW` | `False` |
| **TC-REC-07** | **Interruption Mid-Mutation (Ambiguous)** | Receipt `EXECUTING`, Lock probe: Rảnh, dòng cuối là `ACTION_PENDING`. | `STATE_AMBIGUOUS_IN_DOUBT` | `MANUAL_TIMELINE_INSPECTION_REQUIRED` | `False` |
| **TC-REC-08** | **Torn Tail Observed Mid-Commit** | Receipt `EXECUTING`, Lock probe: Rảnh, dòng cuối JSON bị cắt cụt ký tự (`is_torn == True`). | `STATE_TORN_TAIL_DETECTED` | `TORN_TAIL_OBSERVED_OPERATOR_REVIEW` | `False` |
| **TC-REC-09** | **Duplicate Idempotency Active** | Yêu cầu mới trùng `idempotency_key` với phiên đang `INITIALIZED` / `EXECUTING`. | `STATE_IDEMPOTENCY_DUPLICATE_BLOCKED` | `REJECT_DUPLICATE` | Không phụ thuộc |
| **TC-REC-10** | **Duplicate Idempotency Completed** | Yêu cầu mới trùng `idempotency_key` với phiên đã `COMPLETED`. | `STATE_IDEMPOTENCY_DUPLICATE_BLOCKED` | `REJECT_DUPLICATE` | Không phụ thuộc |
| **TC-REC-11** | **Target Identity Drifted** | Receipt chỉ định `tl_uid_1`, nhưng Resolve đang mở `tl_uid_2`. | `STATE_COMMITTED_CANDIDATE_COMPENSATION` (kèm `drift_detected=True`) | Khóa nút bù trừ; yêu cầu mở đúng timeline | `False` |
| **TC-REC-12** | **Corrupted or Missing Idempotency Index** | Receipt tồn tại nhưng tệp chỉ mục idempotency bị thiếu hoặc hỏng cấu trúc JSON. | `STATE_ARTIFACT_CORRUPTED_OR_MISSING` | `FORENSIC_QUARANTINE_REQUIRED` (Fail-closed, cấm chạy lại) | Không phụ thuộc |

---

## 7. ĐỀ XUẤT DUY NHẤT LÁT CẮT CODE KẾ TIẾP: G07-07A7-I
*(Proposed Next Code Implementation Slice: Recovery Classifier & Read-Only Coordinator)*

Để hiện thực hóa cơ chế đối soát phục hồi mà **tuyệt đối không gây đột biến timeline Resolve** và **bảo toàn 100% ranh giới an toàn**, đề xuất duy nhất một lát cắt code tiếp theo:

### 7.1. Định Danh & Phạm Vi Cho Phép (Scope Allowlist)
- **Tên lát cắt:** `G07-07A7-I`
- **Chế độ:** `READ_ONLY_RECOVERY_CLASSIFIER_ONLY`
- **Phạm vi cho phép (Đúng 3 production modules thuần túy về DTO và dịch vụ đối soát chỉ đọc):**
  1. `src/core/contracts/recovery_assessment_contracts.py` (MỚI): 
     - Định nghĩa các frozen DTOs: `RecoveryAssessment`, `CandidateCompensatingAction`, `AmbiguousActionDetail`, `TargetIdentityMatchReport`, `CrashStateEnum`, `RecoveryActionType`.
     - Định nghĩa các giới hạn trần và hàm làm sạch an toàn: `MAX_TORN_TAIL_PREVIEW_BYTES`, `MAX_PARAMS_SUMMARY_CHARS`, hàm làm sạch chuỗi `sanitize_diagnostics_string`.
  2. `src/services/recovery_classifier_service.py` (MỚI): 
     - Triển khai thuật toán thuần túy phân loại 8 trạng thái sự cố từ `ExecutionReceipt`, `JournalReplayResult`, kết quả thăm dò OS advisory lock và tệp chỉ mục idempotency.
     - Trích xuất danh sách ứng viên bù trừ `candidate_compensations` và các mục bất định `ambiguous_actions` với dữ liệu tóm tắt đã làm sạch.
  3. `src/services/recovery_coordinator.py` (MỚI): 
     - Dịch vụ điều phối chỉ đọc: Thăm dò khóa cố vấn hệ điều hành không chặn trên tệp `.lock` (an toàn race-condition, không phụ thuộc PID), nạp receipt từ `ExecutionReceiptService`, kiểm tra tệp chỉ mục idempotency (fail-closed nếu thiếu/hỏng), nạp journal từ `ExecutionJournalService`, gọi `query_active_timeline_authority` để đối chiếu danh tính, xuất bản `RecoveryAssessment`.
- **Ràng buộc an toàn bất biến:**
  - **TUYỆT ĐỐI 0 LỆNH RESOLVE MUTATION, 0 LỆNH DELETE CLIPS.**
  - `ready_to_execute=False` được bảo toàn nguyên vẹn 100%.
  - 0 sửa đổi giao diện người dùng (UI wiring hoãn lại cho milestone sau).
  - Không tự động kích hoạt rollback ngầm khi chưa có sự xác nhận từ người vận hành.
  - Không xóa bỏ tệp rác hay tệp rách (bảo tồn nguyên vẹn hiện trường đĩa phục vụ forensic).

---

## 8. KẾT LUẬN & TRẠNG THÁI BÀN GIAO CHO CODEX

Tài liệu thiết kế kiến trúc hiệu chỉnh `07A7-recovery-reconciliation-design.md` (R1) đã giải quyết triệt để và trọn vẹn cả 5 phát hiện của Tech Lead Codex:
1. **Thẩm quyền khóa cố vấn OS:** Phân định quyền sở hữu dựa trên kết quả thăm dò non-blocking advisory lock (`fcntl.flock` / `msvcrt.locking`); PID chỉ là siêu dữ liệu chẩn đoán hỗ trợ.
2. **Quan sát vật lý dị thường dòng rách:** Định danh dòng rách là hiện tượng quan sát tệp ngắt quãng (`OBSERVED_INCOMPLETE_TRAILING_RECORD`), không suy diễn nguyên nhân cắt điện; thừa nhận tính phân ly giữa nhật ký ứng dụng và timeline Resolve thực tế.
3. **Dữ liệu chẩn đoán giới hạn và làm sạch:** Loại bỏ raw bytes và arbitrary dictionary, thay bằng `torn_tail_preview_escaped` (tối đa 256 ký tự), `torn_tail_sha256`, `torn_tail_byte_count` và `action_params_summary` (tối đa 512 ký tự).
4. **Fail-Closed khi chỉ mục idempotency hỏng/thiếu:** Xếp loại `STATE_ARTIFACT_CORRUPTED_OR_MISSING` / `STATE_IDEMPOTENCY_UNVERIFIABLE`, tuyệt đối cấm chạy lại kịch bản.
5. **Ứng viên bù trừ chờ người vận hành xem xét:** Đổi tên khuyến nghị thành `CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW`, loại bỏ mọi khẳng định sai lệch về khả năng hoàn tác bảo đảm hoặc khôi phục hoàn hảo.

Tài liệu sẵn sàng bàn giao cho Tech Lead Codex thẩm định và phê duyệt cho công tác tiếp theo.
