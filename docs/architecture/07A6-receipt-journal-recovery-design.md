# G07-07A6-D: THIẾT KẾ BIÊN BẢN TIỀN ĐỘT BIẾN, NHẬT KÝ APPEND-ONLY & CƠ CHẾ PHỤC HỒI LŨY ĐẲNG
*(Durable Pre-Mutation Receipt, Append-Only Execution Journal, and Idempotent Recovery/Rollback Architectural Design)*

> **Kế thừa & Căn cứ kỹ thuật:**
> - Quyết định nghiệm thu Codex: `docs/architecture/07A5-I-R1-code-acceptance.md` (`ACCEPTED_WITH_RECORDED_LIMITS`).
> - Báo cáo nghiệm thu tiền kiểm G07-07A5-I-R1: SHA-256 `f8e49bb087684951b442793e576edb6b860491e768b2b505d29b7381bef315bf`.
> - Quyết định thiết kế ràng buộc Codex: `docs/architecture/07A5-design-review.md` & `07-codex-decisions.md` (C01–C09).
> - Thiết kế thi công tổng thể Gate G07: `docs/architecture/07-resolve-execution-design.md`.
> - Trạng thái Gate hiện tại: **Gate G07 / Milestone M1 CHƯA HOÀN TẤT**; `ready_to_execute=False` được bảo toàn tuyệt đối; 0 timeline mutations, 0 executor creation.
> - **RÀNG BUỘC THIẾT KẾ THUẦN TÚY (DESIGN ONLY):** Chính xác 0 dòng sửa đổi trong `src/`, 0 sửa đổi test sản phẩm, 0 thực thi pytest/app/Resolve/AI model/mạng ngoài. Không commit/push/clean/stash git. Đề xuất duy nhất một lát cắt code kế tiếp (tối đa 3 production modules).

- **TASK_ID:** `G07-07A6-D`
- **WORKER_LEVEL:** `HIGH`
- **MODE:** `DESIGN_ONLY_RECEIPT_JOURNAL_RECOVERY`
- **MỤC TIÊU KIẾN TRÚC:**
  1. Thiết kế biên bản nhận chứng tiền đột biến bền vững (`receipts/<execution_id>.receipt.json`) bảo đảm tính nguyên tử (atomic), chống ghi đè và chống chạy trùng lặp (duplicate execution protection).
  2. Thiết kế nhật ký thao tác ghi nối tiếp (`journals/<execution_id>.journal.jsonl`) tuân thủ nghiêm ngặt giao thức Write-Ahead Logging (WAL) với `fsync()` bắt buộc trước từng thao tác biến đổi DaVinci Resolve API.
  3. Xây dựng động cơ phục hồi lũy đẳng (Idempotent Recovery & Rollback Engine) xử lý triệt để 6 điểm dừng sự cố (Crash Points: CP-1 đến CP-6), bao gồm lỗi cạn đĩa (ENOSPC), từ chối quyền ghi (EACCES), lỗi đứt kết nối Resolve giữa chừng, và phục hồi sau khi crash trong chính quá trình rollback.
  4. Phân định ranh giới đột biến và bảo toàn tuyệt đối nguyên tắc đơn luồng (Single-Thread Affinity Rule) qua `ResolveGateway`, loại bỏ hoàn toàn hiện tượng deadlock hoặc xung đột RPC socket của `fusionscript`.
  5. Đối chiếu mã nguồn thực tế: Phân định minh bạch giữa các API hiện có (`[CODE_VERIFIED]`), các cơ chế đề xuất mới (`[PROPOSED]`), và các giới hạn cố hữu của DaVinci Resolve Scripting API.

---

## 1. BỐI CẢNH KIẾN TRÚC & CALLGRAPH THỰC TẾ (REAL CALLGRAPH TRACE & SOURCE GROUNDING)

Hệ thống đối chiếu mã nguồn hiện hành tại repository, phân loại rõ ràng giữa các thành phần đã kiểm chứng bằng mã nguồn thực tế (`[CODE_VERIFIED]`), các đề xuất kiến trúc mới (`[PROPOSED]`), và các giới hạn cố hữu đã được chứng minh của Blackmagic DaVinci Resolve Scripting API (`[PROVEN_LIMITS]`):

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                    BƯỚC 3: PHÊ DUYỆT BIÊN TẬP (CODE_VERIFIED)                                         |
+-----------------------------------------------------------------------------------------------------------------------+
 [ReviewPage]
      |--> get_approved_proposal_and_record() [src/ui/director/pages/review_page.py:7209] [CODE_VERIFIED]
      |         |-- Zero-IO / 1 context supplier read
      |         |-- Immutable Binding Check: verify_proposal_approval_binding() [src/core/contracts/edit_plan_approval_contracts.py:246]
      |         +-- Returns defensive tuple: (EditPlanProposal, AppOwnedApprovalRecord)
      v
+-----------------------------------------------------------------------------------------------------------------------+
|                                    BƯỚC 4: TIỀN KIỂM KHẢ DỤNG CHUYÊN SÂU (CODE_VERIFIED)                             |
+-----------------------------------------------------------------------------------------------------------------------+
 [DirectorWindow]
      |--> _start_readiness_preflight() [src/ui/director/director_window.py:548] [CODE_VERIFIED]
      |         |-- Query Resolve Authority: query_active_timeline_authority() [src/core/resolve_bridge.py:67] [CODE_VERIFIED]
      |         |   (Fail-closed, 0 float fallback, RationalFPS chuẩn xác, kiểm tra trôi danh tính ERR_RESOLVE_IDENTITY_DRIFT)
      |         |-- Opt-in Deep Request: ReadinessRequest(verify_source_execution=True, timeline_fps=target_authority.timeline_fps)
      |         |-- PreflightWorker.run() -> inspect_source_and_catalog_readiness() [src/core/source_catalog_readiness.py:749]
      |         |   (Thẩm định 5-stat, băm SHA-256 1MiB chunks, decode frames, assemble_plan_frame_geometry Formula B)
      |         +-- BuildPage.show_readiness_result(result, target_authority) [src/ui/director/pages/build_page.py:284]
      |             (Khóa cứng ready_to_execute=False; hiển thị tách bạch 5 tầng trạng thái)
      v
+-----------------------------------------------------------------------------------------------------------------------+
|                                    G07-M2+: RANH GIỚI THI CÔNG & PHỤC HỒI (PROPOSED)                                  |
+-----------------------------------------------------------------------------------------------------------------------+
 [TimelineExecutionCoordinator (MỚI - PROPOSED)]
      |
      |--> 1. Pre-Mutation Authorization Gate:
      |         |-- Kiểm tra lại Approved Proposal & Approval Record (Zero-stale check)
      |         |-- Kiểm tra Deep Readiness Result (toàn bộ checks_passed == True, GEOMETRY_VERIFIED)
      |         +-- Khóa phiên thi công: ExecutionLock (`receipts/<execution_id>.lock`)
      |
      |--> 2. Lập Biên Bản Nhận Chứng Tiền Đột Biến (Pre-Mutation Receipt):
      |         |-- Ghi tệp JSON: `receipts/<execution_id>.receipt.json`
      |         |-- Ghi 2 chặng: file `.tmp` -> flush() -> fsync(fd) -> atomic os.replace()
      |         +-- Nếu ENOSPC hoặc EACCES -> HỦY THI CÔNG NGAY LẬP TỨC (0 lệnh Resolve API)
      |
      |--> 3. Khởi tạo Nhật Ký Thao Tác Append-Only (Execution Journal):
      |         |-- Mở tệp JSONL: `journals/<execution_id>.journal.jsonl` (mode "a", buffering=1)
      |         +-- Ghi dòng khởi tạo: `{"seq": 0, "stage": "START", "execution_id": "..."}` -> fsync()
      |
      |--> 4. Điều phối Thi công Tuần tự qua ResolveGateway:
      |         |-- Với mỗi atomic edit action (k clips/titles):
      |         |     |-- Ghi WAL: `{"seq": i, "stage": "ACTION_PENDING", "action": "INSERT_CLIP", ...}` -> fsync()
      |         |     |-- ResolveGateway.submit_sync(_mutate_resolve_task, timeout=5.0)
      |         |     |-- Nhận kết quả từ Resolve API (lấy `_get_safe_uid(item)` hoặc handle)
      |         |     +-- Ghi WAL: `{"seq": i, "stage": "ACTION_COMMITTED", "resolve_item_id": "...", ...}` -> fsync()
      |         +-- Nếu có lỗi tại action i:
      |               |-- Ghi WAL: `{"seq": i, "stage": "ACTION_FAILED", "error": "..."}` -> fsync()
      |               +-- Chuyển ngay sang Quy Trình Hoàn Tác Tự Động (Automatic Reverse Rollback)
      |
      +--> 5. Động cơ Phục hồi Lũy đẳng (Idempotent Recovery & Rollback Engine):
                |-- Quét ngược journal từ dòng cuối cùng về đầu (Reverse Sequence Scan: seq i -> 1)
                |-- Chỉ xóa những ResolveItem đã được ghi nhận `ACTION_COMMITTED` và chưa có `ROLLBACK_DONE`
                |-- Xác minh danh tính Timeline UID & Project UID trước khi xóa
                |-- Gọi `timeline.DeleteClips([item], False)` (Lift delete, ripple=False)
                |-- Ghi WAL: `{"seq": k, "stage": "ROLLBACK_DONE", ...}` -> fsync()
                +-- Cập nhật Receipt: `state = "ROLLED_BACK"` (hoặc "COMPLETED" nếu thành công 100%)
```

### 1.1. Bảng đối chiếu hiện trạng thành phần mã nguồn (Source Grounding Matrix)

| Thành phần kiến trúc | Vị trí mã nguồn (`file:hàm:dòng`) | Phân loại | Hiện trạng kỹ thuật & Đánh giá rủi ro |
| :--- | :--- | :--- | :--- |
| **Active Timeline Authority** | `src/core/resolve_bridge.py:67-252` | `[CODE_VERIFIED]` | Đã nghiệm thu tại `G07-07A5-I-R1`. Đọc `RationalFPS` trực tiếp từ timeline, fail closed không fallback, chống trôi danh tính (`ERR_RESOLVE_IDENTITY_DRIFT`). |
| **Resolve Gateway Serial Worker** | `src/services/resolve_gateway.py:21-135` | `[CODE_VERIFIED]` | Chạy trên dedicated `QThread`. Điều phối task tuần tự qua hàng đợi `queue.Queue`. Cung cấp `submit()` (async) và `submit_sync()` (blocking with local `QEventLoop`). |
| **InsertService Single Undo** | `src/services/insert_service.py:988-1109` | `[CODE_VERIFIED]` | Triển khai hoàn tác cho **duy nhất 1 clip** gần nhất trong vòng 30s. Sử dụng `timeline.DeleteClips([item], False)`. Có kiểm tra đối chiếu UID của Project, Timeline và Item. **Không hỗ trợ rollback đa bước, không có journal bền vững trên đĩa.** |
| **Pre-Mutation Receipt Contract & Service** | `src/core/contracts/execution_receipt_contracts.py` & `src/services/execution_receipt_service.py` | `[PROPOSED]` | Chưa tồn tại. Cần thiết kế DTO bất biến, serialization an toàn, fsync 2 chặng và cơ chế lock file chống chạy trùng. |
| **Append-Only Journal Service** | `src/services/execution_journal_service.py` | `[PROPOSED]` | Chưa tồn tại. Cần thiết kế giao thức ghi dòng JSONL, fsync bắt buộc trước/sau mỗi lệnh Resolve API. |
| **Idempotent Recovery Engine** | `src/services/recovery_engine.py` | `[PROPOSED]` | Chưa tồn tại. Cần thiết kế cơ chế quét ngược journal, phân tích crash point, phục hồi không phụ thuộc vào trạng thái RAM, tự động lift-delete an toàn. |

### 1.2. Nhận định Rủi ro API DaVinci Resolve & Ràng buộc Thiết kế Thực tế ([OBSERVED_BEHAVIOR_AND_CONSTRAINTS])

1. **Thiếu API Giao dịch Hệ thống (Absence of Scripting Transaction/Undo API):**
   - Tài liệu DaVinci Resolve Scripting API hiện hành không công bố bất kỳ phương thức nào tương tự `timeline.BeginUndoGroup()` hay `timeline.RollbackTransaction()` cho external Python scripting.
   - Thao tác Undo của Resolve (`Edit > Undo` hoặc `Ctrl+Z`) thuộc về giao diện người dùng (GUI), không có hợp đồng API hỗ trợ giao dịch nguyên tử hay nhóm hoàn tác từ xa.
   - **Hệ quả thiết kế:** Mọi cơ chế rollback bắt buộc phải là **Application-Managed Compensation (Bù trừ do ứng dụng quản lý)**: Ứng dụng tự theo dõi danh sách đối tượng đã tạo và chủ động phát lệnh xóa bù trừ (`timeline.DeleteClips([item], False)`).
   - **Ranh giới thực tế:** Cơ chế hoàn tác bù trừ chỉ có thể dọn dẹp các clip do phiên tạo ra; **không bao giờ cam kết khôi phục 100% nguyên trạng timeline** nếu trước đó đã có tương tác biên tập khác hoặc nếu việc xóa bù trừ gặp lỗi giữa chừng. Khi gặp sự cố không thể bù trừ trọn vẹn, hệ thống phải dừng an toàn và đánh dấu `ROLLED_BACK_PARTIAL` hoặc `MANUAL_REVIEW_REQUIRED`, tuyệt đối không đoán mò hay tiếp tục ghi đè.

2. **Hành vi Thực tế của `DeleteClips` (Lift vs Ripple):**
   - Theo mã nguồn hiện có tại `src/services/insert_service.py:1098` và `src/core/resolve_bridge.py:794`, tham số thứ hai của `DeleteClips(items, False)` là cờ `ripple`: `False` thực hiện Lift Delete (xóa clip giữ nguyên vị trí, không dồn clip sau lại).
   - **Quy tắc thiết kế bất biến:** Mọi thao tác bù trừ và dọn dẹp bắt buộc phải truyền cờ `ripple=False`.

3. **Tính Đơn luồng và IPC Socket của `fusionscript`:**
   - Thư viện `fusionscript` dựa trên kết nối IPC nội bộ tới daemon của Resolve. Kinh nghiệm thực tế trong dự án ghi nhận hiện tượng race condition khi truy cập đồng thời từ nhiều luồng.
   - **Quy tắc thiết kế bất biến:** Toàn bộ lệnh gọi tới Resolve API (kể cả đọc và ghi) phải được tuần tự hóa trên một luồng duy nhất (`ResolveGateway`).

---

## 2. CẤU TRÚC & ĐẶC TẢ BIÊN BẢN TIỀN ĐỘT BIẾN (PRE-MUTATION RECEIPT SPECIFICATION)

Biên bản nhận chứng tiền đột biến (Pre-Mutation Receipt) là hợp đồng bền vững trên đĩa, được tạo ra và đồng bộ hóa thành công xuống phần cứng lưu trữ **TRƯỚC KHI** phát ra bất kỳ lệnh API nào làm biến đổi timeline.

### 2.1. Vị trí Lưu trữ & Quy tắc Đặt tên Tệp
- Thư mục lưu trữ: `.resolveflow/receipts/` (nằm trong thư mục cấu hình ứng dụng hoặc thư mục làm việc của dự án được bảo vệ).
- Đường dẫn chuẩn tắc: `.resolveflow/receipts/<execution_id>.receipt.json`
- Quy ước định danh: `<execution_id>` là một chuỗi UUIDv4 duy nhất, sinh ra tại thời điểm chuẩn bị thi công:
  `execution_id = f"exec_{uuid.uuid4().hex}"`
- Tệp khóa chạy trùng (Lock File): `.resolveflow/receipts/<execution_id>.lock`

### 2.2. Lược đồ Dữ liệu Biên bản (Pre-Mutation Receipt Schema)

Biên bản được mô hình hóa bằng dataclass/Pydantic DTO bất biến, bảo đảm kiểu dữ liệu chặt chẽ:

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Literal
from src.core.contracts.time_contracts import RationalFPS

ReceiptState = Literal[
    "INITIALIZED",  # Biên bản đã ghi xuống đĩa, chưa phát lệnh API nào
    "EXECUTING",    # Đang thực thi các thao tác trên timeline Resolve
    "COMPLETED",    # Toàn bộ kế hoạch thi công thành công 100%
    "ROLLED_BACK",  # Gặp sự cố giữa chừng, toàn bộ thay đổi đã được hoàn tác sạch sẽ
    "ABORTED"       # Bị hủy bỏ do sự cố trước khi đột biến hoặc lỗi không thể khắc phục
]

@dataclass(frozen=True)
class SourceFileDigestBinding:
    """Ràng buộc kiểm chứng toàn vẹn của tệp video nguồn tại thời điểm thi công."""
    clip_id: str
    absolute_path: str
    full_sha256: str
    stream_index: int
    stat_identity: Tuple[int, int, int, int, int]  # (dev, ino, size, mtime_ns, ctime_ns)

@dataclass(frozen=True)
class CatalogAssetDigestBinding:
    """Ràng buộc kiểm chứng tài nguyên catalog (B-roll, SFX, Title Macro)."""
    asset_id: str
    absolute_path: str
    full_sha256: str
    stat_identity: Tuple[int, int, int, int, int]

@dataclass(frozen=True)
class TargetTrackReservation:
    """Đăng ký giữ chỗ trên track đích của Resolve."""
    track_type: Literal["video", "audio"]
    track_index: int
    reserved_start_frame: int
    reserved_end_frame: int
    intended_usage: str  # "V1_CUTS", "V2_TITLES", "A1_SYNC_AUDIO", "A2_SFX"

@dataclass(frozen=True)
class ExecutionReceipt:
    """Biên bản nhận chứng tiền đột biến bất biến (Pre-Mutation Receipt)."""
    # 1. Định danh phiên & Thẩm quyền Phê duyệt
    execution_id: str
    proposal_id: str
    proposal_revision: int
    approval_id: str
    approved_at_iso: str

    # 2. Định danh Đích DaVinci Resolve (Strong Identity Binding)
    project_name: str
    project_id: str          # project.GetUniqueId() - cấm để rỗng
    timeline_name: str
    timeline_id: str         # timeline.GetUniqueId() - cấm để rỗng
    timeline_fps: RationalFPS
    timeline_resolution: Tuple[int, int]  # (width, height)

    # 3. Ràng buộc Toàn vẹn Tài nguyên Nguồn (Zero-Drift Proofs)
    source_digests: Dict[str, SourceFileDigestBinding]
    catalog_digests: Dict[str, CatalogAssetDigestBinding]

    # 4. Kế hoạch Chiếm chỗ Timeline & Khối lượng Công việc
    track_reservations: List[TargetTrackReservation]
    planned_actions_count: int

    # 5. Dấu thời gian & Trạng thái Vòng đời
    created_at_iso: str
    state: ReceiptState = "INITIALIZED"
    completed_at_iso: Optional[str] = None
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None
```

### 2.3. Giao thức Ghi Đĩa Bền vững & Tính Nguyên tử (Atomicity & fsync Protocol)

Để bảo đảm biên bản không bao giờ bị biến dạng (corrupted) hoặc ghi một nửa khi xảy ra sập nguồn (kernel panic, power loss, process SIGKILL):

1. **Quy trình Ghi 2 chặng (Two-Phase Atomic Replace):**
   ```python
   def persist_receipt_atomic(receipt: ExecutionReceipt, receipts_dir: str) -> None:
       target_path = os.path.join(receipts_dir, f"{receipt.execution_id}.receipt.json")
       tmp_path = os.path.join(receipts_dir, f"{receipt.execution_id}.receipt.json.tmp.{os.getpid()}")
       
       # Chặng 1: Ghi vào tệp tạm thời
       data_bytes = serialize_receipt_to_json(receipt).encode("utf-8")
       with open(tmp_path, "wb") as f:
           f.write(data_bytes)
           f.flush()
           os.fsync(f.fileno())  # Ép buộc controller ổ cứng ghi dữ liệu vào phiến đĩa/chip NAND
           
       # Chặng 2: Hoán đổi nguyên tử (Atomic Rename/Replace)
       os.replace(tmp_path, target_path)
       
       # Chặng 3: Fsync thư mục cha (bắt buộc trên POSIX để bảo toàn directory entry)
       if sys.platform.startswith("linux") or sys.platform.startswith("darwin"):
           dir_fd = os.open(receipts_dir, os.O_RDONLY | os.O_DIRECTORY)
           try:
               os.fsync(dir_fd)
           finally:
               os.close(dir_fd)
   ```

2. **Xử lý các lỗi hệ thống tệp nghiêm trọng (Fail-Closed System Error Handling):**
   - **`ENOSPC` (No space left on device):**
     - Khi ổ đĩa đầy, lệnh `write()` hoặc `os.fsync()` sẽ ném ngoại lệ `OSError(errno.ENOSPC)`.
     - Hệ thống bắt buộc bắt ngoại lệ, xóa tệp `.tmp` dang dở (nếu có thể), ghi log chẩn đoán `ERR_STORAGE_ENOSPC`, và **DỪNG NGAY LẬP TỨC**. Tuyệt đối không phát lệnh Resolve API nào.
   - **`EACCES` / `EPERM` (Permission Denied):**
     - Khi thư mục receipts bị mất quyền ghi hoặc phân vùng bị chuyển sang Read-Only (`EROFS`).
     - Hệ thống báo lỗi `ERR_STORAGE_PERMISSION_DENIED`, hủy phiên thi công sạch sẽ.

### 2.4. Khóa Chống Thực thi Trùng lặp & Khóa Idempotency Bền vững (Durable Idempotency & Replay Protection)

Nhằm ngăn chặn việc người dùng vô tình bấm Build 2 lần, hoặc hệ thống chạy lại kịch bản cũ đè lên kịch bản mới (kể cả khi tạo UUID `execution_id` mới):
1. **Khóa Idempotency Bền vững từ Nội dung Phê duyệt (Durable Idempotency Key):**
   - Không chỉ khóa theo `execution_id` (vốn là UUID ngẫu nhiên dễ bị lọt nếu client sinh ID mới khi retry).
   - Hệ thống dẫn xuất khóa Idempotency duy nhất từ bộ 5 thành phần nghiệp vụ bắt buộc:
     $$\text{idempotency\_key} = \text{SHA256}\left(\text{proposal\_id} + ":" + \text{str}(\text{proposal\_revision}) + ":" + \text{approval\_id} + ":" + \text{project\_id} + ":" + \text{timeline\_id}\right)$$
   - Khóa này được lưu bền vững vào tệp `.resolveflow/receipts/idempotency_<idempotency_key>.json`.
   - **Chính sách từ chối đối chiếu (Reconciliation Policy):**
     * Nếu đã có một receipt mang cùng `idempotency_key` ở trạng thái đang chạy (`INITIALIZED`, `EXECUTING`): **Từ chối ngay lập tức** với lỗi `ERR_IDEMPOTENCY_DUPLICATE_ACTIVE`.
     * Nếu đã có receipt mang cùng `idempotency_key` ở trạng thái hoàn thành (`COMPLETED`): **Từ chối ngay lập tức** với lỗi `ERR_IDEMPOTENCY_DUPLICATE_COMPLETED`.
     * Nếu receipt trước đó ở trạng thái kết thúc lỗi (`ROLLED_BACK`, `ABORTED`): Chỉ cho phép tái thực thi khi có sự xác nhận đối chiếu minh bạch (reconcile), lưu vết lịch sử phiên cũ.
2. **Cơ chế Khóa File Độc quyền (Exclusive File Creation & Advisory Lock):**
   - Tạo receipt ban đầu và lock file bằng cờ không ghi đè nguyên tử `os.O_CREAT | os.O_EXCL`.
   - Áp dụng `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` trên POSIX hoặc `msvcrt.locking` trên Windows.
   - Nếu không chiếm được lock độc quyền -> Từ chối thực thi để bảo toàn tính toàn vẹn.

---

## 3. CẤU TRÚC & GIAO THỨC NHẬT KÝ APPEND-ONLY (APPEND-ONLY JOURNAL PROTOCOL)

Nhật ký thao tác thi công (Execution Journal) là chuỗi các bản ghi sự kiện tuần tự, tuân thủ nguyên tắc **Write-Ahead Logging (WAL)**: Mọi thao tác dự định thực thi bắt buộc phải được ghi nhận và `fsync()` xuống đĩa **TRƯỚC** khi gọi API của Resolve, và kết quả trả về phải được ghi nhận ngay **SAU** khi API hoàn tất.

### 3.1. Vị trí & Định dạng Tệp
- Thư mục lưu trữ: `.resolveflow/journals/`
- Tệp nhật ký: `.resolveflow/journals/<execution_id>.journal.jsonl`
- Định dạng: **JSON Lines (JSONL)**. Mỗi dòng là một chuỗi JSON hợp lệ kết thúc bằng ký tự xuống dòng `\n`.
- Chế độ mở tệp: `open(journal_path, "a", encoding="utf-8", buffering=1)` (line buffered).

### 3.2. Cấu trúc Từng Dòng Nhật Ký (Journal Entry Schema)

Mỗi dòng trong tệp `.jsonl` mang cấu trúc tường minh:

```json
{
  "seq": 1,
  "stage": "ACTION_PENDING",
  "action_type": "INSERT_CLIP",
  "timestamp_iso": "2026-11-02T14:15:00.123456Z",
  "clip_id": "c01",
  "track_type": "video",
  "track_index": 1,
  "record_frame": 0,
  "duration_frames": 90,
  "params": {
    "file_path": "/media/project/interview_cam_a.mp4",
    "source_in_frame": 120,
    "source_out_frame": 210
  }
}
```

Và khi lệnh hoàn thành:

```json
{
  "seq": 1,
  "stage": "ACTION_COMMITTED",
  "action_type": "INSERT_CLIP",
  "timestamp_iso": "2026-11-02T14:15:00.345678Z",
  "clip_id": "c01",
  "track_type": "video",
  "track_index": 1,
  "resolve_item_id": "tl_item_9823471029384",
  "actual_start_frame": 0,
  "actual_duration_frames": 90
}
```

Nếu lệnh thất bại:

```json
{
  "seq": 1,
  "stage": "ACTION_FAILED",
  "action_type": "INSERT_CLIP",
  "timestamp_iso": "2026-11-02T14:15:00.456789Z",
  "clip_id": "c01",
  "error_code": "ERR_RESOLVE_APPEND_FAILED",
  "error_message": "MediaPool.AppendToTimeline returned empty list."
}
```

### 3.3. Danh mục Thao tác Hỗ trợ (Action Types & Parameters)

| Mã thao tác (`action_type`) | Mục đích & Trách nhiệm thi công | Tham số bắt buộc (`params`) | Dữ liệu trả về khi `COMMITTED` |
| :--- | :--- | :--- | :--- |
| `RESERVE_TRACK` | Đảm bảo track video/audio tồn tại, đủ số lượng, không bị khóa. | `{"track_type": "video"\|"audio", "track_index": int}` | `{"track_ready": true, "track_index": int}` |
| `IMPORT_CARRIER` | Import clip carrier PNG trong suốt vào bin `ResolveFlow Carriers`. | `{"width": int, "height": int, "frames": int}` | `{"media_pool_item_id": str}` |
| `INSERT_CLIP` | Chèn đoạn video/audio nguồn vào timeline tại vị trí chính xác. | `{"media_item_id": str, "track_type": str, "track_index": int, "record_frame": int, "source_in": int, "source_out": int}` | `{"resolve_item_id": str, "track_index": int, "start_frame": int}` |
| `IMPORT_FUSION_COMP` | Nạp mã Fusion `.comp` tạo tiêu đề động hoặc đồ họa vào clip. | `{"resolve_item_id": str, "comp_path": str}` | `{"comp_name": str, "active": true}` |
| `SET_CLIP_COLOR` | Gán màu nhận diện cho clip trên timeline (vd: Orange, Purple). | `{"resolve_item_id": str, "color": str}` | `{"color_applied": str}` |

### 3.4. Chu trình Ghi & Fsync Bắt buộc (Strict WAL Lifecycle)

Mỗi bước thi công tuân thủ nghiêm ngặt chu trình 4 nhịp:
1. **Nhịp 1 (WAL Advance):** Ghi dòng `ACTION_PENDING` vào file `.journal.jsonl`. Gọi `f.flush()` và `os.fsync(f.fileno())`.
2. **Nhịp 2 (Resolve API Invocation):** Chuyển task sang `ResolveGateway` để thực thi trên luồng Resolve duy nhất. Chờ phản hồi có giới hạn timeout (ví dụ: 5.0 giây).
3. **Nhịp 3 (WAL Commit hoặc Fail):**
   - Nếu API thành công và trả về ID hợp lệ: Ghi dòng `ACTION_COMMITTED` kèm `resolve_item_id`. Gọi `f.flush()` và `os.fsync(f.fileno())`.
   - Nếu API thất bại hoặc ném exception: Ghi dòng `ACTION_FAILED` kèm thông tin lỗi chi tiết. Gọi `f.flush()` và `os.fsync(f.fileno())`.
4. **Nhịp 4 (Decision Gate):** Nếu thất bại, lập tức dừng chu trình tiến, chuyển toàn bộ sang Động cơ Phục hồi Rollback.

### 3.5. Bảo Toàn Bằng Chứng & Xử Lý Dòng Cuối Bị Rách Phi Phá Hủy (Non-Destructive Torn-Tail Handling)

Khi xảy ra sập nguồn đúng lúc đang ghi dở dòng JSONL cuối cùng (torn tail - chuỗi byte bị ngắt quãng, thiếu dấu ngoặc đóng `}` hoặc ký tự xuống dòng `\n`):
1. **Nguyên tắc Bảo tồn Bằng chứng Tuyệt đối (Zero Evidence Destruction):**
   - **TUYỆT ĐỐI KHÔNG TỰ Ý CẮT CỤT (TRUNCATE) TỆP GỐC TRÊN ĐĨA.** Việc âm thầm truncate làm mất dấu vết pháp y kỹ thuật (forensic trace) của hành động cuối cùng.
2. **Cơ chế Đọc Bền vững (Safe Non-Mutating Parser):**
   - Parser đọc từng dòng có giới hạn byte (`MAX_LINE_BYTES = 65536`), giới hạn tổng số bản ghi (`MAX_JOURNAL_RECORDS = 50000`).
   - Nếu dòng cuối cùng không thể parse thành JSON hợp lệ:
     * Parser trả về cấu trúc chẩn đoán: `is_torn_tail = True`, `valid_records = [...]`, `torn_raw_bytes = b"..."`, `torn_line_offset = offset`.
     * Tệp `.journal.jsonl` gốc được giữ nguyên 100% byte trên đĩa.
     * Mọi hành động phục hồi hoặc ghi tiếp phải tạo bản sao dự phòng (backup copy) hoặc ghi record phục hồi riêng biệt, không phá hủy tệp nhật ký nguyên bản.

---

## 4. CƠ CHẾ PHỤC HỒI LŨY ĐẲNG & HOÀN TÁC (IDEMPOTENT RECOVERY & ROLLBACK ENGINE)

Động cơ phục hồi (Recovery Engine) chịu trách nhiệm đưa timeline DaVinci Resolve về trạng thái sạch sẽ ban đầu khi có bất kỳ sự cố nào xảy ra trong quá trình thi công hoặc khi ứng dụng khởi động lại sau sự cố đột ngột.

### 4.1. Phân loại 6 Điểm Dừng Sự cố (Crash Points Taxonomy)

Bảng phân tích hành vi và giải pháp cho từng điểm sập nguồn:

```
[Bắt đầu] 
   |
  (CP-1) ---> Crash trước khi Receipt ghi xong (.tmp mồ côi)
   |
[Receipt INITIALIZED]
   |
  (CP-2) ---> Crash sau Receipt, trước khi ghi dòng Journal đầu tiên
   |
[Journal seq 1: ACTION_PENDING]
   |
  (CP-3) ---> Crash trong lúc DaVinci Resolve đang chạy API (chưa rõ kết quả)
   |
[Resolve API hoàn tất]
   |
  (CP-4) ---> Crash sau khi API xong, nhưng trước khi ghi ACTION_COMMITTED
   |
[Journal seq 1: ACTION_COMMITTED ... seq K: ACTION_FAILED]
   |
  (CP-5) ---> Lỗi giữa chừng tại bước K (Partial Failure)
   |
[Đang Rollback: ROLLBACK_PENDING]
   |
  (CP-6) ---> Crash trong chính quá trình đang thực thi Rollback
   v
[Phục hồi hoàn tất / ROLLED_BACK]
```

#### Chi tiết giải pháp cho từng Crash Point:

1. **CP-1: Sập nguồn khi đang ghi Receipt (Chưa có receipt hợp lệ):**
   - **Hiện trạng:** Tệp `receipts/<execution_id>.receipt.json` không tồn tại. Có thể còn sót lại tệp tạm `*.receipt.json.tmp.*`.
   - **Xử lý phục hồi:** Khi ứng dụng khởi động lại, quét thư mục `receipts/`, dọn dẹp các tệp `.tmp` có PID không còn hoạt động. Không có bất kỳ lệnh Resolve nào được gửi đi, timeline hoàn toàn nguyên vẹn.

2. **CP-2: Sập nguồn sau khi ghi Receipt, trước khi có dòng Journal đầu tiên:**
   - **Hiện trạng:** Receipt có `state == "INITIALIZED"`. Tệp journal chưa tạo hoặc kích thước 0 byte.
   - **Xử lý phục hồi:** Nhận diện phiên chưa từng phát lệnh đột biến. Đánh dấu receipt sang `state = "ABORTED"`, ghi log chẩn đoán `ABORTED_BEFORE_FIRST_MUTATION`. Không can thiệp vào Resolve.

3. **CP-3: Sập nguồn trong lúc Resolve đang thực thi API (Dòng cuối là `ACTION_PENDING`):**
   - **Hiện trạng:** Dòng cuối cùng của journal là `{"seq": k, "stage": "ACTION_PENDING", "action_type": "INSERT_CLIP", ...}`. Không có dòng `ACTION_COMMITTED` hay `ACTION_FAILED`. Trạng thái trong Resolve không chắc chắn (có thể clip đã chèn vào hoặc chưa).
   - **Xử lý phục hồi (Heuristic Orphan Inspection & Safe Cleanup):**
     - Đọc thông tin từ dòng pending: `track_type`, `track_index`, `record_frame`, `duration_frames`.
     - Truy vấn danh sách clip hiện có trên track đó tại khung hình `record_frame` qua `timeline.GetItemListInTrack()`.
     - Nếu phát hiện clip có vị trí bắt đầu và độ dài khớp với tham số trong pending: Xác định là clip mồ côi -> Gọi `timeline.DeleteClips([orphan_item], False)`.
     - Nếu không có clip nào: Xác định lệnh API chưa kịp thực thi trước khi crash -> Bỏ qua.
     - Ghi dòng `{"seq": k, "stage": "ROLLBACK_DONE", "note": "ORPHAN_CLEANED"}` vào journal.

4. **CP-4: Sập nguồn sau khi Resolve API đã xong, trước khi kịp ghi `ACTION_COMMITTED` (Trạng thái Nghi ngờ In-Doubt):**
   - **Hiện trạng:** Dòng cuối cùng của journal vẫn là `ACTION_PENDING`, nhưng trên thực tế Resolve API có thể đã thực thi xong hoặc đang dở dang.
   - **Xử lý phục hồi (In-Doubt Boundary):**
     * Trạng thái này được phân loại nghiêm ngặt là **`IN_DOUBT / AMBIGUOUS_STATE`**.
     * Hệ thống **TUYỆT ĐỐI KHÔNG ĐƯỢC PHÉP ĐOÁN MÒ LÀ ĐÃ THÀNH CÔNG** và không bao giờ tự động tái thực thi (re-execution) vì nguy cơ chèn trùng lặp làm xáo trộn timeline.
     * Thuật toán kiểm tra track đích đối chiếu `record_frame` và tham số: Nếu phát hiện item nghi vấn -> Đánh dấu vào biên bản phục hồi, đưa ra chẩn đoán minh bạch và chuyển sang quy trình bù trừ an toàn có giám sát, hoặc yêu cầu `MANUAL_REVIEW_REQUIRED` nếu có nghi ngờ xung đột.

5. **CP-5: Lỗi giữa chừng tại bước K (Partial Failure):**
   - **Hiện trạng:** Các bước từ `1` đến `k-1` đã ghi `ACTION_COMMITTED`. Bước `k` ghi `ACTION_FAILED`.
   - **Xử lý phục hồi (Automatic Reverse Rollback):**
     - Động cơ kích hoạt chế độ rollback tự động ngay tại thời điểm xảy ra lỗi, không cần chờ khởi động lại ứng dụng.
     - Quét ngược danh sách thao tác từ `k-1` về `1`:
       * Đọc `resolve_item_id`, `track_type`, `track_index`.
       * Tìm item trên timeline bằng hàm định danh mạnh: `[it for it in tl.GetItemListInTrack(...) if _get_safe_uid(it) == resolve_item_id]`.
       * Gọi `timeline.DeleteClips([item], False)` (Lift delete, ripple=False).
       * Ghi dòng `{"seq": i, "stage": "ROLLBACK_DONE"}` -> fsync.
     - Sau khi hoàn tác hết từ `k-1` về `1`: Cập nhật Receipt thành `state = "ROLLED_BACK"`.

6. **CP-6: Sập nguồn trong chính quá trình Rollback (Partial Rollback Crash):**
   - **Hiện trạng:** Một số bước (ví dụ từ `k-1` về `m`) đã có `ROLLBACK_DONE`. Các bước từ `m-1` về `1` vẫn chỉ có `ACTION_COMMITTED`.
   - **Xử lý phục hồi (Idempotent Resume):**
     - Khi chạy lại recovery, hệ thống đọc toàn bộ journal: Lập tập hợp các `seq` đã hoàn tất rollback `completed_rollbacks = {entry.seq for entry in journal if entry.stage == "ROLLBACK_DONE"}`.
     - Tiếp tục quét ngược các `seq` còn lại chưa nằm trong `completed_rollbacks`.
     - **Tính lũy đẳng (Idempotence):** Nếu một clip vì lý do nào đó đã bị xóa trước đó nhưng chưa kịp ghi log, khi tra cứu không thấy trên timeline, thuật toán coi như bước đó đã sạch, ghi `ROLLBACK_DONE` và tiếp tục lùi bước tiếp theo mà không ném lỗi.

### 4.2. Rào Chắn An Toàn & Thẩm Quyền Phục Hồi (Recovery Safety Gates)

Để đảm bảo động cơ phục hồi không bao giờ xóa nhầm dữ liệu của người dùng:

1. **Gate 1: Thẩm Quyền Khớp Danh Tính Tuyệt Đối (Strict Identity Binding Gate):**
   - Trước khi phát bất kỳ lệnh `DeleteClips` nào, Recovery Engine phải truy vấn Resolve hiện tại:
     * `cur_proj_id = project.GetUniqueId()`
     * `cur_tl_id = timeline.GetUniqueId()`
   - So sánh với thông tin lưu trong receipt:
     `cur_proj_id == receipt.project_id` VÀ `cur_tl_id == receipt.timeline_id`.
   - Nếu người dùng đã chuyển sang Timeline khác hoặc Project khác:
     -> **TỪ CHỐI ROLLBACK NGAY LẬP TỨC**. Báo lỗi `ERR_RECOVERY_TARGET_MISMATCH`. Tuyệt đối không xóa clip trên timeline lạ!

2. **Gate 2: Rào chắn Chống Xóa Nhầm Clip Ngoài Phiên (Anti-Cross-Contamination):**
   - Chỉ xóa những clip có `resolve_item_id` trùng khớp với ID đã ghi nhận trong nhật ký `ACTION_COMMITTED` của chính `execution_id` đó.
   - Không bao giờ dùng lệnh xóa toàn bộ track (`ClearTrack`) hoặc xóa theo vùng chọn thời gian nếu không xác thực được ID của clip.

3. **Gate 3: Giới hạn Thời gian Hiệu lực Hoàn tác (Rollback Time-to-Live & Stale Protection):**
   - Nếu khoảng thời gian từ khi crash đến khi phục hồi vượt quá 24 giờ, hoặc phát hiện timeline đã có thêm các chỉnh sửa thủ công của người dùng (tổng số lượng clip thay đổi đột biến):
   - Động cơ tạm dừng, chuyển receipt sang `ABORTED_MANUAL_REVIEW_REQUIRED`, hiển thị hộp thoại cảnh báo cho người dùng thay vì tự động xóa ngầm.

---

## 5. RANH GIỚI ĐỘT BIẾN RESOLVE & RÀNG BUỘC ĐƠN LUỒNG (MUTATION BOUNDARY & SINGLE-THREAD AFFINITY)

### 5.1. Kiến Trúc Luồng Phân Lớp (Layered Threading Architecture)

Nhằm tuân thủ tuyệt đối quy tắc thread-safety của Blackmagic DaVinci Resolve và đảm bảo giao diện Qt không bị đơ giật:

```
+---------------------------------------------------------------------------------------------------+
| 1. QT GUI THREAD (Main Thread)                                                                     |
|    - Nhiệm vụ: Hiển thị giao diện Bước 4 (BuildPage), nút Bắt đầu, thanh tiến trình, log UI.      |
|    - Quy tắc: TUYỆT ĐỐI KHÔNG GỌI RESOLVE API. Không gọi blocking IO.                             |
|    - Giao tiếp: Nhận signal từ Execution Worker (`progress`, `status_changed`, `terminal`).       |
+---------------------------------------------------------------------------------------------------+
                                                  ^
                                                  | (Qt Signals / Slots)
                                                  v
+---------------------------------------------------------------------------------------------------+
| 2. EXECUTION COORDINATOR THREAD (TimelineExecutionWorker - QThread)                              |
|    - Nhiệm vụ: Quản lý vòng đời thi công, đọc proposal, tính toán Formula B, ghi Receipt,        |
|      ghi Journal xuống đĩa cứng, điều phối rollback khi gặp lỗi.                                  |
|    - Quy tắc: Không gọi trực tiếp Resolve C API; mọi lệnh biến đổi Resolve phải gửi sang Gateway. |
+---------------------------------------------------------------------------------------------------+
                                                  |
                                                  | (Queue submit / task dispatch)
                                                  v
+---------------------------------------------------------------------------------------------------+
| 3. DEDICATED RESOLVE GATEWAY THREAD (ResolveGateway._thread - QThread)                            |
|    - Nhiệm vụ: Nơi DUY NHẤT trong toàn bộ ứng dụng nắm giữ tham chiếu tới `fusionscript`.         |
|    - Quy tắc: Single-Thread Affinity tuyệt đối. Xử lý tuần tự từng task trong queue.              |
|    - Thao tác: `timeline.AddTrack`, `media_pool.AppendToTimeline`, `timeline.DeleteClips`.        |
+---------------------------------------------------------------------------------------------------+
```

### 5.2. Ranh giới Trách nhiệm giữa Gateway và Coordinator
- `ResolveGateway` chỉ đóng vai trò là **Kênh thi công mệnh lệnh (Command Executor)**: Nhận hàm callable, thực thi trong luồng có fusionscript, trả về kết quả hoặc exception. Gateway không lưu trữ business logic về proposal hay receipt.
- `TimelineExecutionWorker` đóng vai trò là **Bộ não điều phối (Orchestrator)**: Chịu trách nhiệm ghi journal WAL trước/sau lệnh, tính toán bù trừ hình học, và ra quyết định rollback.

---

## 6. MA TRẬN KIỂM CHỨNG HỮU HẠN (FINITE ACCEPTANCE MATRIX)

Ma trận nghiệm thu bao quát 12 kịch bản biên từ điều kiện lý tưởng đến các thảm họa hệ thống tệp và sập nguồn đột ngột:

| Mã test | Kịch bản kiểm chứng | Điều kiện kích hoạt | Kỳ vọng đầu ra & Hành vi hệ thống | Tiêu chí vượt qua (Pass Criteria) | Trạng thái khóa |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-RCV-01** | **Happy Path Execution** | Toàn bộ nguồn nguyên vẹn, Resolve mở đúng timeline, đĩa đủ dung lượng. | Receipt ghi `INITIALIZED` -> Journal ghi `ACTION_PENDING` / `COMMITTED` cho mọi clip -> Receipt đổi thành `COMPLETED`. | 100% clip được đặt đúng tọa độ; Receipt có `state="COMPLETED"`; 0 lỗi chẩn đoán. | `ready_to_execute=False` (ở M1) |
| **TC-RCV-02** | **ENOSPC on Receipt Creation** | Ổ đĩa bị đầy (0 bytes free) trước khi bắt đầu thi công. | Ghi tệp `.tmp` ném `OSError(ENOSPC)`. Hệ thống dừng ngay lập tức; xóa tệp tmp dang dở. | 0 lệnh Resolve API được phát ra; timeline nguyên vẹn 100%; báo `ERR_STORAGE_ENOSPC`. | `ready_to_execute=False` |
| **TC-RCV-03** | **EACCES on Receipt Directory** | Thư mục `receipts/` bị khóa quyền ghi (`chmod 0555`). | Mở tệp ném `PermissionError`. Tiến trình bị chặn ngay tại vạch xuất phát. | 0 lệnh Resolve API được phát ra; báo rõ `ERR_STORAGE_PERMISSION_DENIED`. | `ready_to_execute=False` |
| **TC-RCV-04** | **ENOSPC Mid-Journal Write** | Ổ đĩa bị đầy khi đang ghi journal tại clip thứ 3 (đã xong clip 1 & 2). | Ghi dòng `ACTION_PENDING` thất bại; kích hoạt ngay rollback dọn clip 1 & 2. | Clip 1 & 2 được xóa sạch khỏi timeline; Receipt cập nhật `ROLLED_BACK`. | `ready_to_execute=False` |
| **TC-RCV-05** | **Crash Before First Mutation (CP-1 / CP-2)** | Giả lập kill tiến trình ngay sau khi Receipt được tạo (`state="INITIALIZED"`), Journal rỗng. | Khi khởi động lại, Recovery phát hiện Journal rỗng -> Chuyển Receipt sang `ABORTED`. | Không chạm vào Resolve; dọn sạch lock file; không báo lỗi sai lệch timeline. | `ready_to_execute=False` |
| **TC-RCV-06** | **Crash Mid-Mutation (CP-3)** | Giả lập crash khi dòng cuối là `ACTION_PENDING` và Resolve chưa kịp chèn clip. | Recovery quét track đích không thấy clip mồ côi -> Ghi nhận `ROLLBACK_DONE`, bỏ qua an toàn. | Timeline sạch sẽ; Receipt chuyển sang `ROLLED_BACK`; không ném exception tìm kiếm. | `ready_to_execute=False` |
| **TC-RCV-07** | **Crash Post-Mutation Before WAL Commit (CP-4)** | Giả lập crash khi Resolve đã chèn clip nhưng chưa kịp ghi `ACTION_COMMITTED`. | Recovery phát hiện clip mồ côi tại tọa độ của `ACTION_PENDING` -> Phát lệnh `DeleteClips`. | Clip mồ côi bị xóa khỏi timeline; timeline trở về sạch sẽ; Receipt chuyển `ROLLED_BACK`. | `ready_to_execute=False` |
| **TC-RCV-08** | **Partial Failure & Auto Reverse Rollback (CP-5)** | Chèn thành công clip 1, 2; clip 3 bị lỗi (vd file macro hỏng). | Ghi `ACTION_FAILED` tại seq 3; tự động kích hoạt reverse rollback: xóa clip 2, xóa clip 1. | Timeline sạch hoàn toàn; Receipt chuyển `ROLLED_BACK`; không để lại clip rác mồ côi. | `ready_to_execute=False` |
| **TC-RCV-09** | **Resolve Disconnected Mid-Rollback** | Mất kết nối Resolve khi đang xóa dở clip 2 trong quá trình rollback. | Lệnh `DeleteClips` ném lỗi mất kết nối; Journal ghi nhận `ROLLBACK_FAILED` tại seq 2; dừng an toàn. | Receipt đánh dấu `ROLLED_BACK_PARTIAL`; phát cảnh báo đỏ yêu cầu người dùng kiểm tra thủ công. | `ready_to_execute=False` |
| **TC-RCV-10** | **Identity Drift Before Recovery** | Người dùng chuyển sang một Timeline khác trước khi chạy lệnh Recovery. | Recovery kiểm tra `timeline.GetUniqueId() != receipt.timeline_id` -> Chặn đứng hoàn toàn. | Từ chối xóa clip; báo lỗi `ERR_RECOVERY_TARGET_MISMATCH`; bảo vệ an toàn cho timeline mới. | `ready_to_execute=False` |
| **TC-RCV-11** | **Duplicate Execution Replay Attempt** | Thử gọi thi công lại với một `execution_id` đã có receipt `COMPLETED` trên đĩa. | Hệ thống phát hiện receipt đã tồn tại và không ở trạng thái rảnh -> Từ chối thực thi. | Báo lỗi `ERR_DUPLICATE_EXECUTION_BLOCKED`; 0 lệnh API được gửi đi; bảo toàn dữ liệu cũ. | `ready_to_execute=False` |
| **TC-RCV-12** | **Corrupted Journal Recovery Handling** | Tệp journal bị biến dạng dòng cuối (nửa dòng JSON do crash phần cứng). | Parser đọc journal phát hiện JSONDecodeError ở dòng cuối -> Cắt tỉa dòng lỗi, phân tích theo WAL chuẩn. | Phục hồi thành công các bước trước đó; không bị crash parser; báo log cảnh báo JSON hỏng. | `ready_to_execute=False` |

---

## 7. ĐỀ XUẤT DUY NHẤT LÁT CẮT CODE KẾ TIẾP: G07-07A6-I
*(Proposed Next Code Implementation Slice)*

Để hiện thực hóa nền tảng biên bản và nhật ký bền vững mà **tuyệt đối không làm phình to phạm vi** và **bảo toàn 100% ranh giới an toàn**, đề xuất duy nhất một lát cắt code kế tiếp:

### 7.1. Định Danh & Phạm Vi Cho Phép (Scope Allowlist)
- **Tên lát cắt:** `G07-07A6-I`
- **Chế độ:** `RECEIPT_JOURNAL_STORAGE_CONTRACTS_ONLY`
- **Ràng buộc thi công:**
  - **Đúng 3 production modules** thuần túy về hợp đồng dữ liệu và dịch vụ lưu trữ:
    1. `src/core/contracts/execution_receipt_contracts.py` (MỚI): Định nghĩa DTO bất biến `ExecutionReceipt`, `JournalEntry`, `ReceiptState`, enum các thao tác và hàm serialize/deserialize JSON/JSONL chặt chẽ.
    2. `src/services/execution_receipt_service.py` (MỚI): Triển khai ghi đĩa 2 chặng nguyên tử (`.tmp` -> `fsync` -> `os.replace`), đọc và thẩm định tính toàn vẹn của receipt, kiểm tra lock file chống chạy trùng.
    3. `src/services/execution_journal_service.py` (MỚI): Triển khai ghi nhật ký append-only JSONL với `flush()` + `fsync()` từng dòng, hỗ trợ đọc xuôi và đọc ngược (reverse iterator) phục vụ phục hồi.
  - **Ràng buộc bất biến:**
    * **TUYỆT ĐỐI 0 THAY ĐỔI TIMELINE, 0 LỆNH RESOLVE MUTATION.**
    * `ready_to_execute=False` được bảo toàn nguyên vẹn 100%.
    * Không sửa đổi bất kỳ module UI nào (`review_page.py`, `build_page.py`, `director_window.py`).
    * Viết test suite độc lập kiểm chứng toàn diện các kịch bản I/O đĩa: ghi nguyên tử, fsync, bẫy lỗi ENOSPC giả lập, xử lý JSONL hỏng dòng cuối, và kiểm tra lock file.

---

## 8. KẾT LUẬN & TRẠNG THÁI BÀN GIAO CHO CODEX

Tài liệu thiết kế `07A6-receipt-journal-recovery-design.md` đã hoàn tất việc đặc tả chi tiết, source-grounded toàn bộ hạ tầng bảo vệ chống sập nguồn và hoàn tác tự động cho Gate G07:
1. Xác lập vị trí và lược đồ bất biến của **Biên bản nhận chứng tiền đột biến** (`ExecutionReceipt`).
2. Xác lập giao thức **Write-Ahead Logging 4 nhịp** với `fsync()` bắt buộc trên **Nhật ký thao tác** (`ExecutionJournal`).
3. Xây dựng ma trận giải pháp cho **6 điểm dừng sự cố** (CP-1 đến CP-6) với tính lũy đẳng (idempotent) cao nhất.
4. Rào chắn 3 lớp bảo vệ danh tính timeline và chống xóa nhầm dữ liệu người dùng.
5. Đề xuất duy nhất lát cắt `G07-07A6-I` giới hạn trong 3 production modules lưu trữ nền tảng, hoàn toàn không gây đột biến timeline.

Tài liệu sẵn sàng bàn giao cho Tech Lead Codex thẩm định và phê duyệt trước khi bước vào giai đoạn viết mã.
