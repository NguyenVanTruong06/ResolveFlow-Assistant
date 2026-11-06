# Thiết Kế Kiến Trúc 03C-D: Vòng Đời Nhập Kho, Xử Lý Hàng Đợi & Trải Nghiệm Kết Quả (Ingest Lifecycle, Queue & Result UX)

- **Mã thiết kế:** `03C-D`
- **Tài liệu tham chiếu:** `docs/architecture/03-ingestion.md`, `docs/architecture/03B3-draft-taxonomy-design.md`, `docs/architecture/03B3-codex-decisions.md`, `docs/architecture/03B3-codex-review.md`
- **Mục tiêu:** Chuẩn hóa toàn bộ vòng đời tác vụ nhập kho (`PackIngestWizard`, `LibraryJob`, `PackIngestService`), ma trận chuyển trạng thái giữa các luồng nền và giao diện, cơ chế bảo toàn dữ liệu hàng đợi khi dừng một phần (Partial Queue Processing), phân định rõ ràng giữa cảnh báo metadata khi review và cảnh báo hệ thống sau khi commit, cùng đề xuất lát cắt thực thi duy nhất `03C1`.

---

## 1. Callgraph Thực Tế & Ràng Buộc Vòng Đời Luồng Nền (Actual Callgraph & Thread Invariants)

Hệ thống nhập kho media vận hành dựa trên sự phối hợp giữa giao diện người dùng PySide6 (`PackIngestWizard`), luồng thực thi nền `LibraryJob` (`QThread`), và dịch vụ lõi lưu trữ `PackIngestService`.

### 1.1. Callgraph Chi Tiết Của `PackIngestWizard` (`file:hàm:dòng`)

```
[Khởi Tạo] PackIngestWizard.__init__ (pack_ingest_wizard.py:97-164)
  │   - Khởi tạo self._draft_taxonomy = {} (dòng 104)
  │   - Khởi tạo self._prepare_timer = QTimer(self, singleShot=True) (dòng 105-107)
  │   - Invariants: self._terminal=False, self._closing=False, self.job=None, self.worker=None
  │   - Nếu auto_prepare=True: kích hoạt _prepare_timer.start(0) (dòng 163)
  ▼
[Pha 1: Quét/Tải] _prepare (pack_ingest_wizard.py:402-414)
  │   - Kiểm tra rào chắn: if self._terminal or self._closing or self.worker is not None or self.job is not None: return
  │   - Reset cảnh báo, xóa _draft_taxonomy trong RAM
  │   - Gọi _run(operation, 'prepare')
  ▼
[Thực Thi Nền] _run (pack_ingest_wizard.py:228-238)
  │   - Khởi tạo self.worker = LibraryJob(operation, self) (dòng 232)
  │   - Kết nối: progress -> setText, result -> _outcome=(True, res), error -> _outcome=(False, err), finished -> _finished
  │   - worker.start() (dòng 237)
  ▼
[Thu Hoạch] _finished (pack_ingest_wizard.py:424-471)
  │   - worker = self.worker; self.worker = None; worker.deleteLater() (dòng 425-427)
  │   - Kiểm tra self._terminal hoặc self._closing:
  │       + Nếu 'commit' thành công: emit pack_committed, emit library_changed, gọi super().accept() (dòng 430-438)
  │       + Nếu 'prepare' xong mà đang đóng: gọi _run(discard, 'discard') để dọn staging (dòng 439-442)
  │       + Nếu 'discard' xong: gọi super().reject() (dòng 443-447)
  │   - Nếu trạng thái bình thường (Interactive):
  │       + 'prepare' thành công: nạp assets vào model, cập nhật UI, hiển thị warning_widget (dòng 453-457)
  │       + 'commit' thành công: dọn draft, emit pack_committed, emit library_changed, super().accept() (dòng 461-466)
  │       + 'commit' lỗi: giữ nguyên self._draft_taxonomy trong RAM, cập nhật lbl_status với lỗi để user sửa và thử lại (dòng 467-470)
  ▼
[Pha 2: Nhập Kho] _commit (pack_ingest_wizard.py:415-423)
  │   - table.setFocus() để commit editor đang mở trên bảng
  │   - Cập nhật name, version; deepcopy job
  │   - job['draft_taxonomy'] = dict(self._draft_taxonomy) (dòng 421)
  │   - Gọi _run(service.commit, 'commit') (dòng 422)
  ▼
[Hủy Bỏ / Đóng] reject & closeEvent (pack_ingest_wizard.py:479-508)
      - self._draft_taxonomy.clear(); self._terminal = True; dừng _prepare_timer (dòng 480-483)
      - Nếu worker đang chạy: worker.requestInterruption(); self._closing = True; chờ finished ngắt an toàn (dòng 484-485)
      - Nếu có staging job: gọi _run(service.discard, 'discard') để dọn đĩa (dòng 486-491)
      - Trong closeEvent: nếu worker đang chạy -> event.ignore() để luồng nền dọn xong mới đóng thật (dòng 499-507)
```

### 1.2. Các Điểm Vào Hàng Đợi (Batch Entrypoints) Trong Hệ Thống
1. **`EdgeDock._handle_dropped_packs` (`src/ui/lux/shell/edge_dock.py:1333-1375`)**:
   - Nhận danh sách các file kéo thả `pack_files: List[str]`.
   - Vòng lặp duyệt tuần tự từng file, mở `PackIngestWizard(parent=self, source=fn, auto_prepare=True)`.
   - Bắt tín hiệu `pack_committed` và kiểm tra `(committed[0] or dialog.is_pack_committed()) and res == Accepted`.
   - Nếu user Hủy hoặc đóng dialog giữa chừng: `break` dừng ngay hàng đợi, không mở tiếp các file sau.
   - Gọi `self.refresh_storage_assets()` và hiển thị toast kết quả tổng kết.
2. **`TabAssets._handle_dropped_packs` (`src/ui/tabs/tab_assets.py:7470-7520`)**:
   - Tương tự EdgeDock: duyệt tuần tự `paths`, mở wizard cho từng gói với cờ `auto_prepare=True`.
   - Kiểm soát `_ingest_in_progress`, bảo vệ chống re-entrancy.
   - Nếu user hủy hoặc gặp exception ở gói thứ N: đánh dấu `canceled = True`, ngắt hàng đợi, gọi `self._rescan_assets()` cho tập con đã nhập thành công.
3. **`TabAssets._on_starter_pack_clicked` (`src/ui/tabs/tab_assets.py:5405-5450`)**:
   - Điểm vào đơn lẻ cho Starter Pack (SFX & LUTs) thông qua `prepare_factory`.
   - Khóa nút `btn_starter_pack.setEnabled(False)` và phục hồi an toàn trong `finally:`.
4. **`TabAssetHub._review_pack` (`src/ui/tabs/tab_asset_hub.py:566-571`)**:
   - Điểm vào cho tải gói từ Google Drive / Cloud Hub, kết nối `dialog.library_changed` với `_library_refreshed`.

### 1.3. Các Bất Biến Luồng & Sở Hữu (Thread & Ownership Invariants)
- **Không suy diễn "có signal là an toàn":** Việc kết nối signal-slot Qt không tự động ngăn chặn segfault nếu đối tượng C++ bị hủy khi luồng phụ vẫn đang chạy (`QThread` hoặc `QThreadPool`).
- **Invariants bắt buộc:**
  1. `worker.deleteLater()` chỉ được gọi khi worker đã phát tín hiệu `finished`.
  2. Bất kỳ thao tác đóng cửa sổ nào (`closeEvent`, `reject`, `appquit`) khi worker đang chạy **BẮT BUỘC** phải gọi `worker.requestInterruption()`, đặt cờ `_closing = True`, và `event.ignore()` chờ worker kết thúc vòng lặp I/O rồi mới hoàn tất hủy widget.
  3. `LibraryJob` sở hữu tham chiếu đến tác vụ nền, nhưng **không bao giờ** trực tiếp can thiệp vào các widget UI; mọi cập nhật UI phải thông qua các tín hiệu `progress`, `result`, `error` được Qt marshal về luồng chính (Main GUI Thread).

---

## 2. Ma Trận Trạng Thái & Sự Kiện Vòng Đời (State / Event Matrix)

| Trạng Thái Hiện Tại | Định Nghĩa & Thuộc Tính | Sự Kiện: User Bấm HỦY (`reject`) | Sự Kiện: User Đóng [X] (`closeEvent`) | Sự Kiện: Tác Vụ Lỗi (`error`) | Sự Kiện: Tác Vụ Xong (`result`) |
|---|---|---|---|---|---|
| **1. PREPARE_PENDING** | Wizard vừa khởi tạo, `_prepare_timer` đang đếm lùi 0ms. Chưa có worker, chưa có staging. | Dừng timer ngay. `_draft_taxonomy.clear()`. Đóng dialog với `Rejected`. Đĩa nguyên vẹn. | Dừng timer. `event.accept()`. Không sinh worker, không sinh staging. | Không áp dụng. | Timer timeout $\to$ chuyển sang `PREPARING_WORKING`. |
| **2. PREPARING_WORKING** | `LibraryJob` đang chạy quét/tải (`prepare`/`download`). `self.worker is not None`. | `worker.requestInterruption()`. Đặt `_closing=True`. Chờ worker thoát $\to$ tự dọn staging rác. | `event.ignore()`. Chuyển tiếp sang `reject()` để ngắt worker an toàn trước khi đóng. | Hiển thị lỗi lên `lbl_status`. Nút "1. Quét & kiểm tra" được bật lại để user sửa nguồn. | Chuyển sang `REVIEW_IDLE`. Nạp assets vào table model. Hiển thị cảnh báo nếu có. |
| **3. REVIEW_IDLE** | Job đã sẵn sàng trong RAM (`self.job is not None`). Worker rảnh. User đang sửa nhãn/draft. | Khởi chạy worker dọn staging (`discard`). Khi dọn xong $\to$ `super().reject()`. | `event.ignore()` trong lúc dọn staging. Khi discard hoàn tất $\to$ `event.accept()`. | Không áp dụng (chờ user tương tác). | User bấm Commit $\to$ chuyển sang `COMMIT_CRITICAL`. |
| **4. COMMIT_CRITICAL** | `LibraryJob` đang chạy `service.commit()` dưới `sm.lock()`. Đang rename payload & ghi JSON. | **Vô hiệu hóa Hủy.** (Nút Hủy disabled). Cờ cancel chỉ được kiểm tra trước khi publish. | `event.ignore()`. Chờ commit hoàn tất để xác định đã publish hay chưa. | **Giữ nguyên RAM draft.** Báo lỗi lên `lbl_status`. Giữ nguyên assets để user thử lại. | Chuyển sang `PUBLISHED`. |
| **5. PUBLISHED** | Pack folder và `registry.json` đã ghi xong. Staging đã xóa. Snapshot đang đồng bộ. | Đã hoàn tất. Không thể hủy. | Gọi `super().accept()`. Emit `pack_committed` và `library_changed`. | Snapshot lỗi: ghi log warning, coi như commit thành công (`snapshot_synced=False`). | Dọn RAM draft. Emit `pack_committed`. Emit `library_changed`. `super().accept()`. |
| **6. DISCARDING** | Worker nền đang xóa thư mục staging sau khi người dùng hủy. `self._closing=True`. | Đang chạy, bỏ qua các click tiếp theo. | `event.ignore()` cho đến khi worker dọn dẹp xong. | Log cảnh báo dọn dẹp thất bại, vẫn cho phép đóng dialog `super().reject()`. | Thư mục staging đã xóa sạch. Gọi `super().reject()`. Giải phóng dialog. |
| **7. CLOSED_TERMINAL** | `_terminal=True`. Dialog đã đóng. Bộ nhớ RAM được giải phóng. | Không áp dụng. | Không áp dụng. | Không áp dụng. | Không áp dụng. |

### 2.1. Quy Tắc Phát Tín Hiệu Thành Công Chuẩn Xác (Exactly-Once Success Emission)
- Nếu user bấm nút đóng [X] đúng lúc tác vụ commit vừa publish thành công vào `registry.json`:
  - `_finished()` phát hiện `self._kind == 'commit' and ok`.
  - Bắt buộc phải thực hiện:
    1. `self._pack_committed = True`
    2. `self.pack_committed.emit(result)` (đúng 1 lần duy nhất).
    3. `self.library_changed.emit()` (đúng 1 lần duy nhất).
    4. `super().accept()`.
  - Quy tắc này đảm bảo tầng gọi (EdgeDock hoặc TabAssets) không bao giờ bị mất tín hiệu cập nhật thư viện đối với các gói đã ghi đĩa thành công.

---

## 3. Xử Lý Hàng Đợi Từng Phần (Queue Partial Processing Guarantee)

### 3.1. Kịch Bản & Phân Tích
Khi người dùng kéo thả nhiều gói cùng lúc (ví dụ `[pack1.zip, pack2.zip, pack3.zip]`):
- **Gói 1 (pack1.zip):** Người dùng duyệt và bấm **"2. Nhập vào kho"** $\to$ Commit thành công vào `registry.json` và lưu vào thư mục `packs/`.
- **Gói 2 (pack2.zip):** Cửa sổ wizard mở ra cho gói 2. Người dùng xem bảng review thấy không phù hợp và bấm **"Hủy / Dọn staging"** (hoặc gặp lỗi đĩa hỏng).

### 3.2. Yêu Cầu Bảo Toàn Dữ Liệu Tuyệt Đối
1. **Bảo Toàn Gói 1:** Gói 1 đã hoàn tất giao dịch ghi đĩa; trạng thái của gói 1 trong `registry.json`, `taxonomy.json` và thư mục kho phải được **bảo toàn nguyên vẹn 100%**. Tuyệt đối không có chuyện rollback gói 1 chỉ vì gói 2 bị hủy.
2. **Dừng Hàng Đợi Ngay Lập Tức:** Khi gói 2 bị hủy hoặc gặp lỗi, hàng đợi dừng lại ngay (`break`), không tự động mở tiếp `pack3.zip`.
3. **Làm Mới Giao Diện Trung Thực:**
   - Cả `EdgeDock` và `TabAssets` đều thực hiện:
     ```python
     if committed_packs:
         self.refresh_storage_assets() # hoặc self._rescan_assets()
     ```
   - Đảm bảo danh mục và các asset của gói 1 xuất hiện đầy đủ ngay lập tức trên UI.
4. **Thông Báo Kết Quả Rõ Ràng:**
   - Toast thông báo minh bạch:
     - Trường hợp thành công một phần: `"ℹ️ Đã nhập 1/3 gói (đã dừng hàng đợi)."`
     - Trường hợp lỗi tại gói 2: `"❌ Lỗi nhập kho cho pack2.zip: <nguyên nhân> (đã dừng hàng đợi)."`

> [!NOTE]
> **Hiện Trạng Kiểm Thử:** Kịch bản này đã được kiểm chứng bằng bài test tự động `test_commit_pack1_then_cancel_pack2_reports_partial` trong `tests/ui/test_pack_ingest_wizard.py` và đang pass 100%. Không cần lập kế hoạch viết lại các bài test trùng lặp vô ích cho phần này.

---

## 4. Phân Định Cảnh Báo: Metadata Review vs. Snapshot Post-Publish

Một trong những điểm quan trọng của trải nghiệm người dùng là phân biệt rành mạch giữa 2 loại cảnh báo có bản chất hoàn toàn khác nhau:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. CẢNH BÁO METADATA KHI REVIEW (Pre-Commit Inspection Warnings)        │
├────────────────────────────────────────────────────────────────────────┤
│ • Thời điểm: Trong lúc đang xem xét gói (REVIEW_IDLE).                 │
│ • Nguồn sinh: inspect_job() / clip_manifest / rules kiểm tra gói.      │
│ • Nội dung: Category chưa đăng ký, template_type lạ, file hỏng...     │
│ • Giao diện: Thanh màu vàng warning_widget + Hộp thoại chi tiết 16 KiB.│
│ • Tính chất: Tham khảo trước khi người dùng quyết định nhập kho.        │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ 2. CẢNH BÁO HỆ THỐNG SAU COMMIT (Post-Publish Snapshot Warnings)       │
├────────────────────────────────────────────────────────────────────────┤
│ • Thời điểm: Sau khi pack ĐÃ ĐƯỢC GHI vào registry.json & packs folder.│
│ • Nguồn sinh: commit() khi sm.snapshot() thất bại (cache lỗi, ENOSPC). │
│ • Nội dung: "Pack đã nhập nhưng bộ đệm offline chưa được đồng bộ".    │
│ • Trạng thái: snapshot_synced = False, warnings = [...]                │
│ • Giao diện: Thông báo Toast hoặc Status sau khi đóng Wizard.          │
│ • Tính chất: Pack ĐÃ THÀNH CÔNG, tuyệt đối không báo LỖI (Fail).       │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.1. Bảng Ma Trận Phản Hồi Kết Quả Commit (Result Feedback Matrix)

| Tình Huống | Kết Quả Từ `commit()` | Phản Hồi UI (Toast / Banner) | Hành Vi Hệ Thống |
|---|---|---|---|
| **Thành Công Hoàn Toàn** | `snapshot_synced=True`, `warnings=[]` | `✅ Đã nhập thành công N đạo cụ vào kho!` | Thư viện cập nhật đầy đủ, cache offline đồng bộ hoàn hảo. |
| **Thành Công Kèm Cảnh Báo Cache** | `snapshot_synced=False`, `warnings=['...']` | `✅ Đã nhập thành công N đạo cụ (⚠️ bộ đệm offline chưa đồng bộ)` | **KHÔNG BÁO LỖI.** Không cho phép user bấm retry (vì pack đã nằm trong kho, retry sẽ bị lỗi "Pack đã tồn tại"). |
| **Thất Bại Trước Publish** | Exception ném ra từ `commit()` (`ValueError`, `OSError`) | `❌ Không thể nhập gói: <nguyên nhân>` | Giao dịch đã rollback an toàn; `_draft_taxonomy` giữ nguyên trong RAM để user sửa lỗi và thử lại. |
| **Hàng Đợi Dừng Từng Phần** | Danh sách kết quả `len(committed) < len(total)` | `ℹ️ Đã nhập M/N gói (đã dừng hàng đợi).` | Làm mới giao diện cho M gói đã nhập, giải phóng tài nguyên. |

---

## 5. Vòng Đời Danh Mục Tạm Trong RAM (Draft Taxonomy Lifecycle)

Lát cắt `03B3-I` vừa được triển khai đã chính thức xác lập vòng đời cho Custom Draft Categories:

1. **Khởi Tạo:** `PackIngestWizard._draft_taxonomy: dict[str, str]` được khởi tạo rỗng trong RAM.
2. **Thao Tác Người Dùng:**
   - Khi bấm `+ Danh mục`: xác thực bằng `validate_draft_taxonomy` từ `asset_dto.py` (chặn reserved IDs, kiểm tra regex `^[a-z][a-z0-9_]{0,47}$`).
   - Nếu ID đã có trong kho $\to$ thông báo giữ nguyên nhãn gốc của kho.
   - Nếu ID mới $\to$ thêm vào `_draft_taxonomy`.
3. **Hiển Thị Hợp Nhất (Merged View):**
   - `effective_taxonomy = dict(_draft_taxonomy) | dict(storage.taxonomy())`.
   - Cả combobox gán hàng loạt và `CategoryDelegate` trong bảng đều đọc từ `effective_taxonomy`.
4. **Bảo Toàn Khi Lỗi Commit:**
   - Nếu việc commit gặp lỗi (ví dụ hết dung lượng đĩa khi ghi metadata), `_finished()` **không xóa** `_draft_taxonomy`. Người dùng không bị mất công sức đã cấu hình danh mục và có thể thử lại sau khi giải phóng đĩa.
5. **Hủy Bỏ An Toàn (Zero Disk Write):**
   - Khi người dùng bấm Hủy hoặc đóng wizard: `_draft_taxonomy.clear()`.
   - Tuyệt đối không ghi bất kỳ byte nào vào `taxonomy.json` trên đĩa.
6. **Không Ngộ Nhận ACID:**
   - Multi-file transaction trên filesystem dưới `sm.lock()` là thao tác có bảo vệ (guarded sequence) với cơ chế hoàn nguyên tốt nhất có thể (best-effort rollback), không phải hệ quản trị cơ sở dữ liệu phân tán chuẩn ACID Two-Phase Commit.

---

## 6. Chính Sách Phát Hiện Gói Trùng Lặp (Duplicate Name & Version Policy)

### 6.1. Hiện Trạng Kỹ Thuật
- Hiện tại, trong `PackIngestService.commit()`, định danh thư mục pack được tính theo:
  `folder = initial_root / '02_cloud_downloads/packs' / f'{slug}_{version}_{job["pack_id"]}'`
- Vì `job["pack_id"]` là một UUID mới ngẫu nhiên (sinh ra khi quét/tải gói), nếu người dùng vô tình tải hoặc kéo thả một gói media 2 lần (cùng tên, cùng phiên bản):
  - Hệ thống sẽ sinh 2 thư mục pack riêng biệt.
  - `registry['packs']` sẽ chứa 2 bản ghi có cùng `name` và `version`.
  - Các asset bên trong có thể bị trùng lặp hiển thị trên giao diện thư viện.

### 6.2. Đề Xuất Chính Sách Cho Tương Lai: Bản Sao Độc Lập Song Song (Side-by-Side Independent Copy)
- **Nguyên tắc cốt lõi:**
  1. **Không âm thầm ghi đè hoặc xóa pack cũ:** Việc tự ý xóa pack cũ có thể làm hỏng các liên kết timeline DaVinci Resolve đang sử dụng file từ pack đó.
  2. **Cảnh báo sớm khi Review:**
     - Trong quá trình `inspect_job` / `_prepare`, hệ thống kiểm tra `sm.registry()['packs']`.
     - Nếu phát hiện đã tồn tại pack có cùng `(name, version)`:
       Bổ sung cảnh báo vào danh sách:
       `"Kho đã có gói cùng tên và phiên bản '{name} v{version}'. Tiếp tục nhập sẽ tạo một bản sao độc lập (side-by-side copy)."`
  3. **Quyền Lựa Chọn Rõ Ràng Của Người Dùng:**
     - Người dùng có thể đổi tên pack / đổi số phiên bản trên ô `QLineEdit` của Wizard trước khi bấm Commit.
     - Hoặc bấm tiếp tục Commit nếu thực sự muốn có một bản sao độc lập mới.
     - Hoặc bấm Hủy để loại bỏ việc nhập trùng.

---

## 7. Đề Xuất Lát Cắt Thực Thi Duy Nhất: `03C1-I` (Single Implementation Slice $\le 2$ Modules)

Để hoàn thiện trọn vẹn Gate G03 mà không phát sinh rủi ro phân tán hay refactor quá đà, đề xuất một lát cắt thực thi duy nhất **03C1-I** tập trung vào việc **hoàn thiện phản hồi trải nghiệm người dùng sau commit và cảnh báo trùng lặp khi review**:

### 7.1. Chi Tiết Lát Cắt `03C1-I`
- **Module 1: `src/core/pack_ingest.py` (Inspection Warning for Duplicate Pack)**
  - Trong `inspect_job()` (hoặc `prepare()`):
    - Đọc `registry = self.storage.registry()`.
    - Kiểm tra xem cặp `(name, version)` của job có trùng với gói nào đã tồn tại trong `registry.get('packs', [])` hay không.
    - Nếu trùng: thêm một cảnh báo rõ ràng vào `warnings`:
      `f"Kho lưu trữ đã có gói '{job_name}' phiên bản '{job_version}'. Tiếp tục nhập sẽ tạo bản sao độc lập song song."`
- **Module 2: `src/ui/tabs/tab_assets.py` (Post-Publish Snapshot Warning Toast)**
  - Trong `_on_committed` của `_on_starter_pack_clicked` và `_handle_dropped_packs`:
    - Kiểm tra `payload.get('snapshot_synced')`.
    - Nếu `False`: hiển thị toast cảnh báo:
      `f"✅ Đã nhập {count} đạo cụ vào Kho! (⚠️ Bộ đệm offline chưa đồng bộ: kiểm tra dung lượng ổ đĩa)."`
    - Nếu `True`: giữ nguyên thông báo thành công xanh chuẩn mực.

### 7.2. Kế Hoạch Kiểm Thử Hữu Hạn Cho `03C1-I`
1. `test_inspect_job_warns_on_duplicate_name_and_version`: Kiểm tra `warnings` chứa thông báo bản sao song song khi `(name, version)` đã có trong registry.
2. `test_post_publish_snapshot_warning_toast_presentation`: Kiểm tra toast hiển thị cảnh báo bộ đệm offline khi `snapshot_synced=False` mà không coi là commit thất bại.

---

## 8. Tổng Hợp Gate G03 & Danh Mục Hoãn Lại Hợp Lý (Deferred Scope to G04/G08/G09)

### 8.1. Bảng Trạng Thái Các Lát Cắt Của Gate G03 (Media Ingestion & Storage Architecture)

| Phân Vùng | Nhiệm Vụ | Trạng Thái Hiện Tại | Ghi Chú Kỹ Thuật |
|---|---|---|---|
| **03-01** | Hợp đồng DTO & Indexer | **HOÀN THÀNH** | Quản lý danh mục, cấu trúc pack manifest, asset DTO chuẩn hóa. |
| **03-02** | Storage Manager & Atomic JSON | **HOÀN THÀNH** | Ghi file nguyên tử qua rename, multi-folder links, disk quota guard. |
| **03A** | Zip Extraction & Security Bounds | **HOÀN THÀNH** | Chặn zip slip, symlink, giới hạn tải HTTP/Drive 10 GiB, giải nén ZIP 20 GiB / 100.000 files, manifest 2 MiB. |
| **03S** | Starter Pack Staging & Producer | **HOÀN THÀNH** | Sinh SFX & LUTs nội bộ an toàn vào staging, chuyển hướng qua Wizard. |
| **03B1** | Manifest Hints & Warning Inspection | **HOÀN THÀNH** | Gợi ý danh mục lạ, template_type, bảo toàn dự đoán gốc. |
| **03B2** | Warning UI & Global UTF-8 Budget | **HOÀN THÀNH** | Ngân sách chi tiết $\le 16384$ bytes, thoát sạch exit code 0. |
| **03B3** | RAM-only Draft Taxonomy & Guarded Commit | **HOÀN THÀNH** | Quản lý danh mục tạm trong RAM, commit có khóa và rollback có kiểm soát. |
| **03C1** | Duplicate Hint & Studio Result UX | **HOÀN THÀNH** | Cảnh báo gói trùng lặp khi review, phản hồi cảnh báo snapshot offline trên Studio TabAssets. |
| **03C2** | Dock/Hub Surface Feedback & Partial Queue | **HOÀN THÀNH** | Phản hồi cảnh báo snapshot offline trên EdgeDock và Cloud Hub (`AssetHubWidget`), bảo toàn partial commit và refresh khi hàng đợi lỗi/hủy giữa chừng. |

### 8.2. Danh Mục Các Hạng Mục Hoãn Lại Hợp Lý (Legitimate Deferred Constraints)
Các hạng mục sau được phân định phạm vi rõ ràng theo đúng ROADMAP chính thức:
- **Phạm vi hoàn tất của Gate G03:** Toàn bộ 3 UI surfaces (Studio `TabAssets`, Dock `EdgeDock`, Hub `AssetHubWidget`) đã thống nhất xử lý phản hồi post-publish và partial queue.
- **Kiểm tra thủ công trên Desktop thật (Manual Desktop Acceptance):** Các test tự động chạy ở chế độ offscreen (`QT_QPA_PLATFORM=offscreen`) không thể thay thế cho nghiệm thu trực quan trên desktop có DaVinci Resolve đang chạy. Các bước kiểm tra thủ công hữu hạn được liệt kê tại Mục 8.3.
- **G04 (Library UI & Search/Selection):** Hoàn thiện giao diện thư viện, tìm kiếm thời gian thực, lọc phân cấp, gắn thẻ bộ sưu tập và chọn lựa asset trong Studio/Dock.
- **G08 (System Hardware & Thread Lifecycle Drain):** Quản lý triệt để vòng đời ngầm của `HardwareStatsWorker` và telemetry trong `StudioWindow` khi đóng cửa sổ ứng dụng trên môi trường thực tế.
- **G09 (DaVinci Resolve Physical Integration Tests):** Kiểm thử tương tác trực tiếp với timeline Resolve API đang chạy thật trên desktop (chèn clip playhead, áp LUT trực tiếp).
- **Lỗi Rollback Chưa Hoàn Tất (Incomplete Rollback):** Trong multi-file transaction, nếu một nhánh rollback thất bại (ví dụ folder rename lỗi trong khi taxonomy đã revert), hệ thống ghi log chi tiết và thông báo trạng thái "Chưa khôi phục hoàn toàn" chứ không tự ý xóa dữ liệu hay giả định đã an toàn.

### 8.3. Checklist Kiểm Thử Thủ Công Trên Desktop (Manual User Desktop Test Checklist)
*(Dành cho người dùng kiểm chứng trực quan trên giao diện thực tế khi toàn bộ Gate G03 được nghiệm thu)*:
- [ ] **Kéo thả 1 gói ZIP vào EdgeDock:** Xác nhận thanh dock mở Wizard kiểm duyệt, bảng review hiển thị đúng danh sách file.
- [ ] **Thêm Danh mục Mới trong Wizard rồi bấm HỦY:** Mở `taxonomy.json` trong thư mục kho kiểm tra $\to$ xác nhận danh mục mới **không** xuất hiện.
- [ ] **Thêm Danh mục Mới và Bấm NHẬP VÀO KHO:** Xác nhận pack được nhập thành công, danh mục mới xuất hiện trên Category Rail của TabAssets.
- [ ] **Kéo thả đồng thời 2 gói ZIP:** Bấm Nhập gói 1, bấm Hủy gói 2 $\to$ Xác nhận gói 1 đã vào kho và giao diện hiển thị thông báo "Đã nhập 1/2 gói (đã dừng hàng đợi)".


## Nghiệm thu Codex sau03C2
31focusedtestsPASS28.37s exit0. Studio/Dock/Hub postpublishsnapshotwarning vàpartialqueue đã có scopeaccepted.
Chờdesktop4mục tại03-desktop-acceptance.md; chưafullhardware/Driveauth/Resolveacceptance.
