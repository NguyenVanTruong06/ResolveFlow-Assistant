# THIẾT KẾ KIẾN TRÚC: G06-06B3-I2 — ĐẤU NỐI WORKER BẤT ĐỒNG BỘ & GIAO DIỆN TƯ VẤN TÀI NGUYÊN (REVIEW PAGE)

> HIỆN HÀNH 2026-10-29: P1 strict storage accepted trong limits; worker residual cancel-after-factory và test lifecycle tại06B3-I2-P1-codex-review.md. Active06B3-I2-P1-R1-THEN-P2-D; Codex204tests/1.92s/exit0. Review UI chưa triển khai.

> **TRẠNG THÁI HIỆN HÀNH (2026-10-29):** Đã được Tech Lead Codex phê duyệt kèm quyết định ràng buộc tại `docs/architecture/06B3-I2-codex-decisions.md`.  
> Lát cắt thực thi hiện hành: **`G06-06B3-I2-P1: Strict Asset Read & AssetMatchingWorker`** (Worker core và Strict Storage Reader; UI ReviewPage hoãn sang P2).

**Tài liệu:** `docs/architecture/06B3-I2-worker-review-design.md`  
**Giai đoạn:** `G06-06B3-I2-D` (Đã cập nhật theo quyết định phê duyệt)  
**Cấp độ Worker:** `HIGH`  
**Tài liệu tham chiếu cốt lõi:**  
- `docs/architecture/06B3-demand-review-design.md` (Thiết kế tổng quan nhu cầu kịch bản)  
- `docs/architecture/06B3-I2-codex-decisions.md` (Quyết định ràng buộc của Tech Lead Codex)  
- `docs/architecture/06B3-I1-gate-acceptance.md` (Nghiệm thu chính thức G06-06B3-I1)  
- `src/ui/director/pages/review_page.py` (Mã nguồn thực tế ReviewPage)  
- `src/ui/director/director_window.py` (Mã nguồn thực tế DirectorWindow)  
- `src/core/storage_manager.py` (Mã nguồn thực tế StorageManager)  

---

## 1. Mục Tiêu & Ranh Giới Thiết Kế (Scope & Boundary)

### 1.1. Mục Tiêu
Thiết kế kiến trúc cho tầng Worker luồng nền (`AssetMatchingWorker`), cơ chế đọc dữ liệu kho tài nguyên nghiêm ngặt (`StorageManager.assets(raise_on_error=True)`), và giao diện cố vấn ngoại tuyến trên Trang Duyệt Kịch Bản (`ReviewPage`). Đảm bảo:
- Không chặn luồng giao diện người dùng chính (off-thread matching).
- Không làm biến đổi kịch bản gốc (`self._video_plan_data` giữ nguyên 100% hash).
- Quản lý luồng Qt an toàn, không dùng `terminate()`, single logical terminal signal.

### 1.2. Ranh Giới Ràng Buộc (Binding Boundaries)
1. **Hoàn toàn mang tính Cố Vấn (Advisory Only):**
   - Tuyệt đối không ghi đè trường `asset_file` trong kịch bản `self._video_plan_data`.
   - Không tự ý chọn ứng viên (no auto-pick top 1).
   - Không tự động chèn clip lên timeline DaVinci Resolve.
   - Không gọi API Resolve Scripting, không tải file từ Internet/Google Drive, không tạo file giả.
2. **Không làm rò rỉ hoặc khóa luồng C++ (Qt Concurrency Guard):**
   - CẤM gọi `QThread.terminate()`.
   - Cooperative cancellation trước và sau các phân đoạn xử lý dữ liệu.
   - Native `QThread.finished` giữ nguyên phục vụ dọn dẹp bộ nhớ vật lý C++.
3. **Phát tín hiệu đầu cuối duy nhất (Single Logical Terminal):**
   - Đúng một tín hiệu `terminal = Signal(object)` mang frozen `AssetMatchingOutcome` cho mọi kết cục (`completed`, `error`, `cancelled`).

---

## 2. Truy Vết Thực Tế Điểm Kết Nối & Luồng Dữ Liệu (Execution Trace)

### 2.1. Điểm Neo Giao Diện Trên Slot 1 (`ReviewPage.card_slot_video`)
Theo mã nguồn thực tế tại [src/ui/director/pages/review_page.py](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/director/pages/review_page.py#L957-L1001):
- Khung hiển thị `self.card_slot_video` (dòng 957) chứa thanh tiêu đề ngang `h_slot_vid_top` với nút `self.btn_paste_slot1` ("📋 Dán vào Slot 1", dòng 976).
- Điểm neo tích hợp mới (P2): Bổ sung nút bấm `"🔍 Gợi ý tài nguyên"` (`self.btn_match_assets`) vào bố cục `h_slot_vid_top`, mặc định disabled cho đến khi `self._video_plan_data` được nạp hợp lệ.
- Nhãn trạng thái `self.lbl_slot_vid_status` (dòng 998) tiếp tục thông báo tiến độ, và một vùng hiển thị kết quả tư vấn (`self.panel_matching_sidecar`) dạng thẻ gấp/mở (collapsible drawer) được bố trí ngay dưới `card_slot_video`.

### 2.2. Luồng Bóc Tách & Kích Hoạt Worker Tại GUI Thread
1. **Sự kiện Click:** Người dùng bấm `btn_match_assets` -> gọi `self._on_match_assets_clicked()`:
   - Đọc trực tiếp raw `self._video_plan_data` (lưu tại các hook thực tế: `_apply_json_data` dòng 4880, 4902, 4932; `load_from_config` dòng 5033).
   - Nếu `self._video_plan_data` là `None` hoặc rỗng `{}`: hiển thị thông báo lỗi hoặc không thực thi.
2. **Xác lập Caller-Owned `plan_revision`:**
   - GUI thread tính toán chuỗi định danh nội dung bằng SHA-256 từ canonical JSON của kịch bản (`rev_<hash16>` hoặc full SHA-256, không cho phép NaN, chuỗi UTF-8 chuẩn).
   - Ghi nhận `self._current_plan_revision = plan_revision`.
3. **Bóc tách Demands:**
   - Gọi `batch = PlanDemandAdapter.extract(self._video_plan_data, plan_revision=plan_revision)`.
   - Nếu `batch.requests` rỗng (0 eligible demands nhưng có thể có noneligible demands): Worker vẫn thực thi và lắp ráp sidecar với các bộ đếm trung thực mà không gọi kho tài nguyên.
4. **Khởi tạo và Phát Động Worker:**
   - Tăng bộ đếm thế hệ: `self._matching_generation += 1`.
   - Vô hiệu hóa nút bấm và hiển thị trạng thái đang xử lý.
   - Khởi tạo worker:
     ```python
     self._active_matching_worker = AssetMatchingWorker(
         batch=batch,
         generation=self._matching_generation,
         parent=self
     )
     self._active_matching_worker.terminal.connect(self._on_matching_terminal)
     self._active_matching_worker.finished.connect(self._active_matching_worker.deleteLater)
     self._active_matching_worker.start()
     ```

### 2.3. Luồng Xử Lý Nền Trong Worker Thread (`AssetMatchingWorker.run()`)
Worker là một thể hiện kế thừa `QThread`, thực thi hoàn toàn độc lập với GUI:
1. **Checkpoint 0 (Khởi đầu):** Kiểm tra `self._is_cancelled()` -> nếu có, emit `AssetMatchingOutcome(status='cancelled', ...)` và return.
2. **Revalidate Batch:** Gọi shared `validate_demand_batch(self._batch)`.
3. **Nhánh Requests Rỗng (`len(self._batch.requests) == 0`):**
   - Không gọi storage factory, không gọi provider.
   - Lắp ráp sidecar rỗng qua `PlanDemandAdapter.assemble(self._batch, ())`.
   - Kiểm tra cancel; nếu không hủy -> emit `AssetMatchingOutcome(status='completed', sidecar=sidecar, ...)`.
4. **Nhánh Có Requests:**
   - **Checkpoint 1:** Kiểm tra cancel trước storage factory và provider call.
   - Khởi tạo `StorageManager()` trên worker thread qua `storage_factory`.
   - Định nghĩa provider: `catalog_provider = lambda: storage.assets(raise_on_error=True)`.
   - Khởi tạo `OfflineAssetMatchingService(catalog_provider=catalog_provider)`.
   - Thực thi matching: `results = service.match_batch(self._batch.requests)` (phân đoạn tải và đối soát noninterruptible).
   - **Checkpoint 2:** Kiểm tra cancel sau khi matching hoàn tất.
   - Lắp ráp sidecar: `sidecar = PlanDemandAdapter.assemble(self._batch, results)`.
   - **Checkpoint 3:** Kiểm tra cancel trước khi phát tín hiệu terminal.
   - Phát tín hiệu: `self._emit_terminal(AssetMatchingOutcome(status='completed', sidecar=sidecar, ...))`.
5. **Xử Lý Lỗi Tập Trung:**
   - Bắt mọi ngoại lệ (factory, provider, matcher, assembler).
   - Nếu phát hiện cancel đã được yêu cầu -> `cancelled` luôn thắng, emit `AssetMatchingOutcome(status='cancelled', ...)`.
   - Nếu không cancel -> emit `AssetMatchingOutcome(status='error', error_message=..., ...)`.

---

## 3. Quản Lý Vòng Đời & Freshness Context

### 3.1. Caller-Owned Plan Revision vs Storage Freshness
- **Hợp đồng `plan_revision`:** Chuỗi băm nội dung kịch bản thô do caller (`ReviewPage`) tạo ra và sở hữu. Chuỗi này đảm bảo tính toàn vẹn song ánh giữa các nhu cầu và kịch bản tại thời điểm kích hoạt tra cứu.
- **Giới hạn kiến trúc:** `plan_revision` chỉ chứng minh tính tươi mới của cấu trúc kịch bản nội tại; nó **không chứng minh** tính tươi mới của kho tài nguyên vật lý trên đĩa (ví dụ: người dùng chép thêm file vào kho sau khi kịch bản đã nạp).

### 3.2. Ma Trận Hủy Hiệu Lực (Context Invalidation Matrix)

| Hành Động Người Dùng / Sự Kiện | Vị Trí Code Thực Tế Trong `ReviewPage` | Hành Động Quản Lý Vòng Đời Bắt Buộc |
| :--- | :--- | :--- |
| **Dán kịch bản mới vào Slot 1** | `_paste_to_slot("slot1")` (dòng 994) & `_apply_json_data` (dòng 4880, 4902) | `self._matching_generation += 1`; Hủy cooperative worker đang chạy; Xóa bỏ sidecar cũ; Reset giao diện tư vấn. |
| **Tải cấu hình từ dự án cũ** | `load_from_config` (dòng 5033) | `self._matching_generation += 1`; Hủy worker; Cập nhật `_current_plan_revision`; Xóa sidecar cũ. |
| **Lệch Manifest ID bị từ chối** | `_apply_json_data` (dòng 4851: `_video_plan_data = None`) | `self._matching_generation += 1`; Hủy worker; `_current_plan_revision = None`; Ẩn panel tư vấn. |
| **Chuyển tab / Ẩn trang Review** | `hideEvent` | Không bắt buộc hủy luồng nền nếu đang chạy, nhưng giữ nguyên cờ generation để không làm xáo trộn GUI khi chuyển lại. |
| **Đóng cửa sổ ứng dụng** | `shutdown_analysis_workers` (dòng 2117) & `DirectorWindow.closeEvent` (dòng 944) | Bắt buộc gọi `cancel()` trên `AssetMatchingWorker`, kích hoạt `requestInterruption()`, chờ worker kết thúc trước khi cho phép đóng cửa sổ. |

---

## 4. Cơ Chế Đọc Kho Nghiêm Ngặt: `StorageManager.assets(raise_on_error=True)`

### 4.1. Khắc Phục Hiện Tượng Nuốt Lỗi
Tại [src/core/storage_manager.py:408](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/storage_manager.py#L408), bổ sung tham số keyword-only:
```python
def assets(self, *, raise_on_error: bool = False):
```
- **Typegate:** Kiểm tra `type(raise_on_error) is bool`, ném `ValueError` ngay trước khi thực hiện I/O nếu sai kiểu.
- **Nhánh Legacy (`raise_on_error=False`):** Giữ nguyên vẹn 100% logic cũ và `except (OSError, ValueError, KeyError, TypeError): return []` để bảo toàn tính tương thích cho toàn bộ callers hiện có.
- **Nhánh Strict (`raise_on_error=True`):**
  * Hàm trợ giúp `_strict_read_json(path)`:
    - Bounded stat và read `<= MAX_JSON` (16 MiB).
    - Không trả default khi thiếu file: file thiếu -> ném `StorageOfflineError`.
    - Lỗi quyền truy cập -> ném `PermissionError`.
    - JSON hỏng, sai UTF-8 hoặc vượt kích thước -> ném `ValueError`.
    - Envelope sai cấu trúc (thiếu `packs` hoặc `assets`) -> ném `ValueError`.
  * Trạng thái `StorageState.ERROR` -> ném `OSError`.
  * Kho chưa cấu hình (`root` hoặc `library_id` vắng mặt) -> ném `StorageOfflineError`.
  * Kho `ONLINE` / `READ_ONLY` / `FULL`: Đọc bắt buộc `registry.json`.
  * Kho `OFFLINE`: Đọc bắt buộc snapshot; kiểm tra `origin_library_id` khớp với cấu hình; snapshot thiếu hoặc lệch id -> ném `StorageOfflineError`.
  * Catalog rỗng hợp lệ (`packs: []`, `assets: []`) -> trả về `[]` bình thường.

---

## 5. Cơ Chế Cooperative Cancel & Quản Lý Luồng QThread

### 5.1. Cấu Trúc Lớp `AssetMatchingOutcome` & `AssetMatchingWorker`
```python
@dataclass(frozen=True)
class AssetMatchingOutcome:
    status: str  # 'completed' | 'error' | 'cancelled'
    generation: int
    plan_revision: str
    sidecar: Optional[AssetMatchingSidecar] = None
    error_message: str = ""

class AssetMatchingWorker(QThread):
    terminal = Signal(object)  # Phát đúng 1 AssetMatchingOutcome

    def __init__(self, batch: AssetDemandBatch, generation: int, *, storage_factory=None, parent=None):
        ...
```
- `plan_revision` được suy ra trực tiếp từ `batch.plan_revision`, không nhận tham số riêng biệt để tránh mâu thuẫn.
- `cancel()` là idempotent, bật cờ `_cancel_requested = True` và gọi `requestInterruption()`. Hoạt động bền vững ngay cả khi được gọi trước `start()`.
- Phát chính xác một lần duy nhất qua `_emit_terminal()`.

---

## 6. Thiết Kế Giao Diện Tư Vấn (Advisory UI Component — P2 Deferred)

Được bố trí dạng bảng gấp/mở (collapsible drawer) ngay dưới `card_slot_video`:
- **Read-Only hoàn toàn:** Không có nút "Áp dụng đè vào kịch bản".
- **Thống kê phân hoạch:** Hiển thị số lượng cho từng trạng thái (`matched`, `unmatched`, `empty_query`, `unsupported`, `invalid`, `overflow`).
- **Danh sách gợi ý:** Hiển thị tên tài nguyên, điểm số khớp (`match_score/100`), thời lượng, và nhãn nguồn.

---

## 7. Phân Chia Lát Cắt Triển Khai (Implementation Slices)

Lộ trình I2 được chia thành 2 lát cắt rõ ràng:
1. **Lát Cắt 1 (ĐÃ THỰC THI — G06-06B3-I2-P1):**
   - **`G06-06B3-I2-P1: Strict Asset Read & AssetMatchingWorker`**
   - Bổ sung `StorageManager.assets(raise_on_error=True)` và `_strict_read_json`.
   - Tạo module mới `src/ui/director/workers/asset_matching_worker.py`.
   - Kiểm thử đơn vị TDD: `tests/core/test_storage_assets_strict.py` và `tests/ui/test_asset_matching_worker.py`.
   - Finite suite: 204 tests pass 100%.
2. **Lát Cắt 2 (Giai Đoạn Tiếp Theo — G06-06B3-I2-P2):**
   - **`G06-06B3-I2-P2: ReviewPage Advisory UI Wiring`**
   - Thêm nút `"🔍 Gợi ý tài nguyên"` trên `card_slot_video`.
   - Thêm panel hiển thị sidecar drawer.
   - Đấu nối worker với `ReviewPage` hooks (`_apply_json_data`, `load_from_config`, `shutdown_analysis_workers`).
   - Kiểm thử offscreen GUI lifecycle (TC-W02 stale generation rejection, close event pending).

---

## 8. Ma Trận Kiểm Thử Nghiệm Thu P1 (Acceptance Test Matrix — P1 Worker Core)

| Mã Ca | Kịch Bản Kiểm Thử (Input) | Kết Quả Kỳ Vọng (Expected Outputs & States) | Tầng Kiểm Thử |
| :--- | :--- | :--- | :--- |
| **TC-P1-01** | **Strict Flag Typegate:** Truyền int/string vào `raise_on_error`. | Ném `ValueError` ngay trước I/O. Nhánh False giữ nguyên legacy. | Pure Unit Test |
| **TC-P1-02** | **Corrupt/Oversize/Missing Registry:** Kho online gặp lỗi metadata. | `raise_on_error=True` ném `ValueError` hoặc `StorageOfflineError`. False trả về `[]`. | Pure Unit Test |
| **TC-P1-03** | **Offline Snapshot Validation:** Kho offline thiếu snapshot hoặc lệch origin. | `raise_on_error=True` ném `StorageOfflineError`. Snapshot hợp lệ trả records offline. | Pure Unit Test |
| **TC-P1-04** | **Empty Requests Batch:** Batch chỉ chứa demands không eligible. | Worker hoàn tất thành công, 0 factory call, sidecar bảo toàn counters. | QThread Test |
| **TC-P1-05** | **Cancel Trước Start & Trong Provider:** Gọi `cancel()` trước và trong lúc đọc đĩa. | Emits exactly 1 terminal `status='cancelled'`, sidecar=None. | QThread Test |
| **TC-P1-06** | **Fault Injection Propagation:** Factory hoặc provider ném `StorageOfflineError`. | Emits exactly 1 terminal `status='error'`, thông điệp tiếng Việt, sidecar=None. | QThread Test |
| **TC-P1-07** | **Happy Path Offscreen Execution:** Batch hợp lệ và catalog có asset khớp. | Chạy trên thread riêng, emits `status='completed'`, sidecar có `matched_count=1`. | QThread Test |

---

## 9. Ba Câu Hỏi Chính Sách (Policy Questions)

### Câu Hỏi 1: Xử lý ngoại lệ bị nuốt tại `StorageManager.assets()` dòng 510
- **Quyết định chốt:** Đã triển khai tham số `raise_on_error: bool = False` opt-in tại `StorageManager.assets()`. Caller cũ không bị ảnh hưởng, worker mới nhận được lỗi I/O thật.

### Câu Hỏi 2: Cơ chế sinh `plan_revision` của kịch bản
- **Quyết định chốt:** Áp dụng băm nội dung SHA-256 từ canonical JSON của kịch bản (`rev_<hash16>`) tại P2, ghi nhận rõ ràng caller revision không thay thế cho kiểm tra tính tươi mới của hệ thống tệp.

### Câu Hỏi 3: Vị trí hiển thị danh sách tư vấn tài nguyên
- **Quyết định chốt:** Triển khai dạng bảng gấp/mở (collapsible drawer) read-only ngay dưới `card_slot_video` ở P2.
