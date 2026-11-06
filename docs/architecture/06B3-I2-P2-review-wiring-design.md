# Tài Liệu Thiết Kế Kiến Trúc: G06-06B3-I2-P2-R2 — ReviewPage Advisory UI Wiring, Manifest Authority, Result Graph & Thread Residuals

> TRẠNG THÁI HIỆN HÀNH: CODE_ACCEPTED_WITH_RECORDED_LIMITS. Codex fresh 273 tests/54.48s/exit 0 và residual probes PASS. Xem 06B3-I2-P2-code-acceptance.md; desktop 06B là bước tiếp theo, chưa đóng G06.

**Tác giả:** Antigravity (Pair Programming với Tech Lead Codex)  
**Ngày cập nhật:** 2026-10-29  
**Nhiệm vụ:** `TASK_ID: G06-06B3-I2-P2-R2 — Close manifest, result graph và running-thread residuals`  
**Mục tiêu:** Tích hợp giao diện cố vấn tài nguyên ngoại tuyến (`AssetAdvisoryDrawer`), cơ chế neo ngữ cảnh kho (`MatchingStorageContext`), và liên kết an toàn luồng (`AssetMatchingWorker`) vào `ReviewPage` và vòng đời ứng dụng `DirectorWindow`, đảm bảo 100% tính an toàn luồng, chống stale UI, phân định chính xác thẩm quyền manifest (raw vs active), tái thẩm định toàn diện đồ thị kết quả sidecar (demands + results + candidates), và kiểm soát vòng đời luồng đang chạy khi start() gặp sự cố.

---

## 1. Điểm Neo Giao Diện & Trình Diễn Cố Vấn (Slot 1 & Advisory Drawer)

### 1.1. Điểm Neo Nút Bấm Hành Động & Physical Busy State (Slot 1 Action)
- **Tệp nguồn:** [src/ui/director/pages/review_page.py](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/director/pages/review_page.py)
- **Vị trí:** `self.card_slot_video` (Khung hiển thị kịch bản Video Plan V1, bố cục `h_slot_vid_top`).
- **Thành phần bổ sung:**
  - Nút bấm explicit action: `self.btn_match_assets = QPushButton("💡 Gợi ý tài nguyên")`.
  - Nhãn trạng thái riêng biệt: `self.lbl_matching_status = QLabel("")` đặt cạnh nút bấm.
  - Định dạng PlainText: Cả `lbl_matching_status` và tất cả các labels thông tin đều được cấu hình `setTextFormat(Qt.TextFormat.PlainText)` để triệt tiêu nguy cơ XSS hoặc lỗi định dạng HTML rich text.
  - **Quản lý trạng thái vật lý (`_update_matching_button_state`):** Nút bấm bị vô hiệu hóa chừng nào `is_matching_busy()` còn trả về `True` (tức `len(self._matching_workers_registry) > 0`). Khi người dùng thay đổi kịch bản video plan trong lúc worker cũ đang chạy ngầm, nút bấm vẫn được giữ `Disabled` cho đến khi worker cũ hoàn tất vòng đời C++ native `finished`. Khi registry thực sự rỗng và có kịch bản hợp lệ, nút bấm mới được mở lại `Enabled`.

### 1.2. Khung Trình Diễn Đọc-Chỉ-Đọc (Read-Only Compact Drawer / Model-View)
- **Tệp nguồn:** [src/ui/director/widgets/asset_advisory_drawer.py](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/director/widgets/asset_advisory_drawer.py)
- **Thành phần hiển thị:**
  - `AssetAdvisoryDrawer(QFrame)` đặt co giãn bên dưới `plan_status_layout`.
  - `QTableView` đi kèm `AssetAdvisoryModel(QAbstractTableModel)` bọc `DemandMatchEntry` từ `AssetMatchingSidecar`.
  - 7 cột dữ liệu hiển thị thực tế:
    1. `Vị trí / Cảnh`: Phân định nguồn `B-roll #<index+1> @ <sec>s` hoặc `SFX #<index+1>`.
    2. `Phân loại`: `category` hoặc `raw_type` (memes, overlays, sfx, music).
    3. `Từ khóa`: `query_text` hoặc `raw_query_hint` (Plain text thuần túy).
    4. `Thời lượng`: `desired_duration_sec` dạng `<sec>s` (ví dụ `3.0s`) hoặc `-`.
    5. `Trạng thái`: Huy hiệu màu (Badge): `✓ Khớp` (#4ade80), `✕ Không khớp` (#a1a1aa), `⚠ Không hợp lệ` (#f87171), `— Chưa hỗ trợ` (#facc15), `∅ Từ khóa rỗng` (#fb923c), `⚠ Vượt hạn ngạch` (#f87171).
    6. `Ứng viên hàng đầu`: Tên tài nguyên kèm gói và tính khả dụng `[Pack] (online/offline)`. Chỉ hiển thị tham khảo, không tự động chọn.
    7. `Điểm phù hợp`: Định dạng số nguyên từ vựng chuẩn **`NN/100`** (ví dụ `85/100`). Tuyệt đối **CẤM tỷ lệ phần trăm `confidence%`** hay float.
  - Thanh thống kê phân hoạch đầy đủ (Summary partition counters): Hiển thị tổng số mục, đếm chi tiết `Khớp`, `Không khớp`, `Không hợp lệ`, `Từ khóa rỗng`, `Chưa hỗ trợ`, `Vượt hạn ngạch`.
  - Nhãn thông tin kho media: `lbl_storage_info` hiển thị ID kho media đã tra cứu, chỉ nhận đúng kiểu `isinstance(storage_context, MatchingStorageContext)`.
  - Nhãn cảnh báo stale chân thực (Truthful Stale Banner): `lbl_stale_banner` với nội dung **"Kết quả cũ — bấm Gợi ý để cập nhật"** (màu vàng nhạt). Phương thức `set_error(message, retain_stale=True)` đảm bảo banner stale không bị ẩn đi khi các dòng hiển thị trên bảng vẫn thuộc về kịch bản cũ đã bị lỗi khi cập nhật.
  - Banner lỗi non-modal: `lbl_error_banner` hiển thị lỗi tiếng Việt có kiểm soát, không bật dialog/popup modal làm đơ giao diện.
- **Ranh giới an toàn nghiêm ngặt (Strict Scope Boundaries):**
  - **Read-only 100%**: Chỉ mang tính chất tư vấn tham khảo trực quan cho biên tập viên.
  - **KHÔNG có preview / hover preview / autoplay**: Không khởi tạo audio/video player trên drawer.
  - **KHÔNG auto-pick / insert / apply**: Không tự động điền đường dẫn vào timeline hay media pool Resolve.
  - **KHÔNG sửa đổi `asset_file`**: Giữ nguyên vẹn tuyệt đối nội dung của `_video_plan_data`.
  - **KHÔNG sử dụng QWidget per-asset card grid**: Sử dụng thuần túy `QTableView` gọn nhẹ.

---

## 2. Luồng Dữ Liệu & Adapter SSOT Context Builder (Nhóm A & R2 Residuals)

```
[self._video_plan_data] (Raw Dict trên ReviewPage)
         │
         ▼ (_build_matching_context: Bóc tách qua PlanDemandAdapter SSOT)
   [1. Precheck Array Bounds: broll_inserts + sfx_inserts <= 4096]
   [2. Manifest Authority: raw_plan_manifest_id vs active_manifest_id vs effective]
       (Mismatch giữa 2 valid nonblank IDs -> ném ValueError kiểm soát)
   [3. Preflight String & Size Budget: MAX_MATCH_CONTEXT_BYTES = 524288]
   [4. Clone plan_for_adapter với effective_manifest_id (0 raw plan mutation)]
   [5. PlanDemandAdapter.extract(data, plan_revision="__canonical_placeholder__")]
   [6. Canonical Material DTOs: raw_manifest, active_manifest, effective_manifest, demands, options]
   [7. SHA-256 Băm Deterministic JSON ──► plan_revision: "rev_<64_hex_chars>"]
   [8. PlanDemandAdapter.extract(data, plan_revision=plan_revision)]
         │
         ▼ (AssetDemandBatch: Frozen DTO, canonical demands, valid requests)
[AssetMatchingWorker(batch, generation, storage_context_reader=read_matching_storage_context)]
         │
         ▼ (Background QThread: Context Reader 1 -> factory -> assets -> match -> assemble -> Context Reader 2)
[AssetMatchingOutcome] (Frozen DTO: status, generation, plan_revision, sidecar, storage_context)
         │
         ▼ (Queued Signal to GUI Slot: Xác thực worker identity, nominal type, assemble revalidation)
[ReviewPage Advisory State] ──► Cập nhật Model / Drawer / Last-Good State
```

### 2.1. Phân Định Thẩm Quyền Manifest & Precheck Mảng Nguồn (Nhóm A Residuals)
- **Precheck số lượng mục mảng nguồn:** Kiểm tra `len(broll_inserts) + len(sfx_inserts) <= 4096` ngay từ đầu trước khi bước vào vòng lặp preflight iterator, ngăn chặn hoàn toàn việc duyệt iterator trên mảng quá khổ.
- **Tách bạch thẩm quyền Manifest:**
  * `raw_plan_manifest_id`: ID trích từ kịch bản `raw_plan.get("manifest_id")`.
  * `active_manifest_id`: ID trích từ thực thể manifest hiện hành trong bộ nhớ `self._manifest.manifest_id`.
  * `effective_manifest_id`: Giá trị có hiệu lực cho batch (ưu tiên raw nếu có, nếu raw absent/None thì dùng active; cả hai None vẫn hợp lệ).
  * **Mismatch guard:** Khi cả `raw_plan_manifest_id` và `active_manifest_id` đều là chuỗi nonblank hợp lệ nhưng khác nhau -> lập tức ném `ValueError("Lệch định danh manifest: ...")` trước khi tăng generation, cancel workers, hay dispatch.
  * **Tách biệt trong Material:** Cả `raw_plan_manifest_id`, `active_manifest_id`, và `effective_manifest_id` đều được lưu rõ ràng trong từ điển `material` dùng để băm SHA-256. Bất kỳ sự thay đổi nào đối với `active_manifest_id` (ví dụ `mA -> mB` hoặc `mA -> None`) đều làm thay đổi `canonical_bytes` và sinh ra mã revision mới, triệt tiêu nguy cơ active manifest bị raw manifest che mất.
  * `pending["manifest_id"]` được gán chính xác bằng `batch.manifest_id`.

### 2.2. Shared Owned Matching Context Builder
- **Nguyên tắc SSOT:** Sử dụng trực tiếp `PlanDemandAdapter` làm Single Source of Truth cho toàn bộ logic trích xuất nhu cầu tài nguyên, không tự trích xuất thủ công trên UI.
- **Bao phủ toàn diện các trường nghiệp vụ:**
  - `PlanDemandAdapter` tự động fallback từ `description` sang `asset_file` -> `suggested_file`.
  - Chuẩn hóa các category alias (`meme` -> `memes`, `overlay` -> `overlays`, `music` -> `music`, `sfx` -> `sfx`).
  - Hỗ trợ đầy đủ `desired_duration_sec`, `timeline_sec`, `query_source`, và `rejection_reason`.
  - Bất kỳ sự thay đổi nào đối với `asset_file` hoặc `duration_sec` trong kịch bản đều lập tức thay đổi `canonical_material` và tạo ra mã băm SHA-256 mới.
- **Đồng bộ tham số truy vấn:** Cả `top_k` và `allow_offline` đều được đưa vào `canonical_material` và đồng bộ chính xác với `AssetMatchingService.match_batch`.
- **Chặn trần preflight an toàn:** Hằng số `MAX_MATCH_CONTEXT_BYTES = 524288` (512 KiB) được kiểm tra trước khi cấp phát bộ nhớ.

---

## 3. Thẩm Định Quyền Hạn Terminal & Vòng Đời Luồng An Toàn (Nhóm B & C Residuals)

### 3.1. Thẩm Quyền Terminal & Tái Thẩm Định Toàn Diện Đồ Thị Kết Quả (`_on_matching_terminal`)
Mọi tín hiệu terminal gửi từ background worker vào GUI slot `_on_matching_terminal(self, worker, req_id, outcome)` đều phải vượt qua các lớp phòng vệ an toàn nghiêm ngặt và nằm trọn trong khối `try ... except` để ngăn 0 exception rò rỉ ra ngoài Qt event loop:
1. **Thẩm định danh tính Worker & Pending Worker:**
   - Worker phải đúng là worker đang được theo dõi: `self._matching_workers_registry.get(req_id) is worker`.
   - Worker phải khớp chính xác với pending worker: `pending.get("worker") is worker`.
2. **Thẩm định định dạng Outcome & Strict Int Generation:**
   - `outcome` bắt buộc phải là instance của `AssetMatchingOutcome`.
   - `outcome.generation` bắt buộc phải là `int` chuẩn (chặn bool).
   - Thế hệ và revision phải khớp đơn điệu với pending: `outcome.generation == self._matching_generation` và `outcome.plan_revision == pending["plan_revision"]`.
3. **Revalidation Pending Batch:**
   - Gọi `validate_demand_batch(batch)` để thẩm định lại tính toàn vẹn của batch đã lưu trong pending.
4. **Revalidation Toàn Diện Sidecar Result Graph qua `PlanDemandAdapter.assemble`:**
   - Kiểm tra `isinstance(sidecar, AssetMatchingSidecar)`, khớp `plan_revision` và `manifest_id`.
   - Kiểm tra cấu trúc entries: `isinstance(entry, DemandMatchEntry)`, `len(entries) == len(batch.demands)`, `entry.demand == batch.demands[i]`.
   - Với eligible demands: bắt buộc phải có `isinstance(entry.result, OfflineAssetMatchResult)`; thu thập danh sách `eligible_results`.
   - Với noneligible demands: bắt buộc `entry.result is None`.
   - **Tái lắp ráp chuẩn tắc:** Gọi `canonical_sidecar = PlanDemandAdapter.assemble(batch, eligible_results)`. Phương thức này thẩm định toàn diện:
     * `res.query_text == req.query_text`, `res.category == req.category` (chặn foreign query).
     * Mọi candidate phải là `OfflineAssetCandidate` hợp lệ (chặn arbitrary objects).
     * `is_matched` strict boolean, khớp với số lượng candidates (>0).
     * Khớp 1-to-1 giữa eligible requests và results (chặn thiếu/thừa/trùng kết quả).
   - Đối soát `sidecar == canonical_sidecar` và chỉ công bố kết quả đã qua thẩm định chuẩn tắc.
5. **Thẩm định Ngữ Cảnh Kho Lưu Trữ (MatchingStorageContext):**
   - Với batch có requests (`len(batch.requests) > 0`): `storage_ctx` bắt buộc phải là `MatchingStorageContext`. Cả `root` và `library_id` phải là chuỗi ký tự UTF-8 không rỗng (chặn các trường hợp bị tráo thành list `[]` hoặc lone surrogates).
   - Với batch không có request (toàn bộ noneligible): `storage_ctx` là `None` hoặc hợp lệ.
6. **Nhất quán trạng thái Error / Cancelled:**
   - Khi status là `error` hoặc `cancelled`, `outcome.sidecar` và `outcome.storage_context` bắt buộc phải là `None`.
7. **Tiêu thụ Pending Context đúng một lần:**
   - `self._matching_pending_context = None` được thực thi để đảm bảo tính idempotent.

### 3.2. Quản Lý Thất Bại Khởi Chạy Khi Thread Đã Chạy (Nhóm C Residuals)
Trong `_on_match_assets_clicked`, việc kiểm tra lỗi khởi chạy không dựa đơn thuần vào biến cờ `started`, mà quan sát bằng chứng vòng đời thực tế:
- **Nếu Worker đang thực sự chạy (`worker.isRunning()` hoặc `worker.running == True`):**
  * **RETAIN trong registry:** Giữ nguyên tham chiếu trong `self._matching_workers_registry` (không gọi pop).
  * **Cancel:** Gọi `worker.cancel()` đúng một lần.
  * **KHÔNG gọi `deleteLater()`:** Tuyệt đối không xóa QThread khi vòng lặp C++ đang thực thi.
  * **Giữ Busy Disabled:** Nút bấm hành động tiếp tục giữ `Disabled` vì `is_matching_busy()` vẫn là `True`.
  * **Native Finished Cleanup:** Chờ tín hiệu native `finished` từ luồng C++ để dọn dẹp vật lý, gọi `deleteLater()` an toàn, và mở lại nút bấm.
- **Nếu Non-running Failure (ctor lỗi hoặc start() lỗi trước khi luồng chạy):**
  * Rollback riêng lượt yêu cầu sở hữu: xóa khỏi registry, xóa pending context, gọi `worker.deleteLater()`.
  * **Không giảm thế hệ `_matching_generation`**: Giữ tính chất đơn điệu tăng dần (strictly monotonic).
  * Bảo toàn trạng thái loading tổng hợp của các tiến trình phân tích khác (`_prompt_workers_registry`, `_kinetic_workers_registry`).
  * Hiển thị banner lỗi có kiểm soát trên UI và khôi phục trạng thái nút bấm qua `_update_matching_button_state()`.

---

## 4. Ngữ Cảnh Kho Lưu Trữ (Worker Storage Context Evidence)

### 4.1. Frozen Dataclass & Bounded Helper
- Bổ sung `MatchingStorageContext(root: str, library_id: str)` (frozen=True) trong `asset_matching_worker.py`:
  - Thẩm định chuỗi UTF-8 nghiêm ngặt, không rỗng, không chứa lone surrogates.
  - Không chứa timestamp giả định; chỉ phản ánh cấu hình kho được quan sát thực tế trên worker.
- Bổ sung helper `read_matching_storage_context(storage)`:
  - Đọc tệp cấu hình kho trên đĩa qua kiểm tra giới hạn dung lượng nghiêm ngặt của core strict loader (`MAX_JSON = 16777216`, tức 16 MiB).
  - Đối soát cấu hình vừa đọc trên đĩa với cấu hình trong bộ nhớ để phát hiện tráo đổi thư mục kho giữa chừng.

### 4.2. Hai Lần Đối Soát (Pre/Post Matching Verification)
- Trên luồng worker, context reader được thực thi 2 lần:
  1. Lần 1: Sau khi tạo storage factory và trước khi gọi `assets(raise_on_error=True)`.
  2. Lần 2: Sau khi chạy xong matcher và assembler, trước khi đóng gói outcome.
- Nếu cấu hình kho bị tráo đổi giữa 2 lần đọc (`ctx1 != ctx2`), worker lập tức phát lỗi tiếng Việt "Kho lưu trữ đã bị thay đổi trong lúc xử lý; vui lòng gợi ý lại" và trả về outcome error mà không lưu sidecar.

---

## 5. Kết Quả Kiểm Thử (Acceptance Verification)

Toàn bộ bộ kiểm thử hữu hạn 273 tests đạt 100% PASS:
- `tests/ui/test_review_page_advisory_wiring.py`: **37/37 PASSED** (Bao phủ toàn diện các bài test gốc và các test mở rộng theo 3 nhóm residual A-C và 5 probe checks).
- `tests/ui/test_asset_matching_worker.py`: **22/22 PASSED** (Bao phủ worker cancellations, exceptions, storage context reader pre/post, và teardown assertions).
- `tests/ui/test_prompt_preparation_routing.py`: **52/52 PASSED**.
- `tests/ui/test_review_page_batch_prompt.py`: **104/104 PASSED**.
- `tests/core/test_storage_assets_strict.py`: **9/9 PASSED**.
- `tests/core/test_plan_demand_adapter.py`: **34/34 PASSED**.
- `tests/core/test_matching_service.py`: **15/15 PASSED**.
- **Tổng cộng: 273 passed trong 59.67s, exit code 0.**


