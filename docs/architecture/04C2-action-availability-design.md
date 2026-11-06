# THIẾT KẾ VÀ ĐẶC TẢ KIẾN TRÚC G04-04C2: BẢO TOÀN ORIGIN VÀ KIỂM TRA THẨM QUYỀN THAO TÁC (ACTION-TIME AUTHORITY & AVAILABILITY)

- **Mã định danh:** `G04-04C2-I-R2` (Giữ generated media có file hợp lệ)
- **Trạng thái:**
  - **Phương án A (Data Authority & Adapter Enrichment - `G04-04C2-P-I`):** `ACCEPTED` (Codex Tech Lead đã nghiệm thu 86 tests PASS exit 0)
  - **Action Guard tại `InsertService` (`G04-04C2-I` / `R1`):** `CHANGES_REQUESTED` (Codex review phát hiện regression chặn generated file)
  - **Hiệu đính Generated Media & Dependency Failures (`G04-04C2-I-R2`):** `IMPLEMENTED_PENDING_REVIEW` (Đã hoàn thiện mã nguồn và bộ kiểm thử TDD 128 tests PASS exit 0, đang chờ Tech Lead Codex nghiệm thu)
- **Cấp độ Worker:** `HIGH` (Duy trì nghiêm ngặt theo chỉ thị Tech Lead Codex và người dùng)
- **Tác giả:** Antigravity (Worker)
- **Tech Lead thẩm định:** Codex Tech Lead
- **Phiên bản:** Revision 5 (Tách bạch rõ ràng 2 dạng generated: procedural runtime generator vs file-backed generated media)
- **Ngày lập:** 2026-10-26

---

## 1. KHẢO SÁT VÀ ĐỐI CHIẾU MÃ NGUỒN CỤ THỂ (FILE:HÀM:DÒNG)

```
[CODE_VERIFIED]  Mã nguồn hiện có đang chạy và đã được kiểm chứng bằng test suite
[PROPOSED]       Đặc tả thay đổi/bổ sung trong kế hoạch
[DEFERRED]       Các consumer/route được hoãn lại cho các lát cắt tiếp theo
```

### 1.1. Core Action Guard Module (`src/core/action_guard.py`)[CODE_VERIFIED]
- **Kiến trúc thiết kế:**
  - Module thuần Python (`pure Python`), độc lập hoàn toàn với Qt (`PySide6`), UI widgets, DaVinci Resolve API (`fusionscript`), network và write I/O.
  - Hỗ trợ Dependency Injection 100%: Cho phép inject `storage_provider`, `stat_fn`, và `realpath_fn` phục vụ kiểm thử đơn vị không cần truy cập đĩa thực tế hoặc giả lập các lỗi ngoại lệ hệ thống (`PermissionError`, `EIO`, v.v.).
  - Hợp đồng Fail-Closed tuyệt đối (Failure-Safe Contract): Mọi ngoại lệ từ phía OS (`PermissionError`, `EACCES`, `EIO`, `ENOENT`), ngoại lệ từ dependency (`storage_provider`, `realpath_fn`, `stat_fn`), hoặc dữ liệu trả về sai cấu trúc đều được bắt an toàn trong helper và chuyển hóa thành `ActionGuardResult` có cấu trúc rõ ràng. Tuyệt đối không rò rỉ ngoại lệ ra caller.

- **Cấu trúc Dữ liệu Kết quả `ActionGuardResult`:**
  ```python
  @dataclass(frozen=True)
  class ActionGuardResult:
      allowed: bool
      status: str              # 4-Enum chuẩn: "online", "offline", "missing", "staging"
      result_code: str         # Mã kết quả chi tiết phục vụ máy và ghi log
      reason: str              # Thông điệp phản hồi thân thiện với người dùng (tiếng Việt)
      resolved_path: str = ""  # Đường dẫn tuyệt đối an toàn đã giải quyết symlink
      is_native_guidance: bool = False  # Cờ đánh dấu hiệu ứng hướng dẫn native Resolve
  ```

- **Chữ ký Hàm và API Công khai:**
  ```python
  def check_action_availability(
      asset: Mapping[str, Any],
      intent: str = "insert",
      storage_provider: Optional[Any] = None,
      stat_fn: Optional[Callable[[Union[str, Path]], os.stat_result]] = None,
      realpath_fn: Optional[Callable[[Union[str, Path]], str]] = None,
  ) -> ActionGuardResult:
  ```

### 1.2. Chính Sách Xử Lý Tài Nguyên `source_kind == "generated"` [CODE_VERIFIED]

Hệ thống phân định chính xác 2 dạng tài nguyên sinh động (`generated`):
1. **Dạng 1 — Procedural Runtime Generator:**
   - Điều kiện: Có runtime capability/object thực thụ (đối tượng thuộc kiểu `TextStylePreset` hoặc có callable `.generate`).
   - Hành vi: Được miễn trừ kiểm tra file vật lý trên đĩa; được phép thực thi ngay với kết quả:
     `ActionGuardResult(allowed=True, status="online", result_code="OK_PROCEDURAL_PRESET", reason="Sẵn sàng sinh mã Fusion động.")`.
   - **Ràng Buộc Sống Còn:** Chỉ áp dụng khi `source_kind != "managed"`. Tài nguyên kho quản lý dạng text (file-backed `.setting` / `.drfx`) bắt buộc phải đi qua thẩm quyền kho và kiểm tra file vật lý, tuyệt đối cấm fallback sang generator.
2. **Dạng 2 — File-Backed Generated Media (Audio AI, Footage Rendered, v.v.):**
   - Điều kiện: `source_kind == "generated"` nhưng không có runtime generator object (là file media thông thường trên đĩa).
   - Hành vi: Đi qua nhánh kiểm tra file vật lý chuẩn xác (Branch 7):
     - Kiểm tra trạng thái cache (`offline`, `missing`).
     - Kiểm tra đường dẫn `file_path`: nếu rỗng $\to$ `ERR_MEDIA_PATH_EMPTY`.
     - Kiểm tra path traversal `..` $\to$ `ERR_INVALID_PATH_TRAVERSAL`.
     - Giải quyết đường dẫn an toàn qua `_safe_resolve_path`.
     - Kiểm tra file vật lý qua `_safe_stat_file`: nếu mất file $\to$ `ERR_FILE_NOT_FOUND`; nếu lỗi quyền $\to$ `ERR_FILESYSTEM_IO_OR_PERMISSION`.
     - Nếu file tồn tại và là regular file $\to$ trả về `ActionGuardResult(allowed=True, status="online", result_code="OK_GENERATED_MEDIA_VERIFIED", resolved_path=resolved_file)`.

### 1.3. Hợp Đồng Xử Lý Lỗi Đầu Vào & Lỗi Phụ Thuộc (Input & Dependency Failure Contract) [CODE_VERIFIED]

1. **Xác thực Kiểu và Giá Trị `source_kind`:**
   - Chỉ khi `source_kind` khuyết thiếu hoàn toàn trong dict hoặc có giá trị tường minh là `None` mới được phép tiếp tục theo diện tương thích dữ liệu cũ (legacy compatibility).
   - Nếu `source_kind` được truyền vào với bất kỳ kiểu dữ liệu nào không phải chuỗi (`[]`, `{}`, `bool`, `int`), hoặc chuỗi rỗng/chỉ chứa khoảng trắng, hoặc chuỗi không nằm trong `VALID_SOURCE_KINDS` (`{"managed", "linked", "builtin", "generated"}`):
     $\to$ Từ chối ngay với: `allowed=False`, `status="offline"`, `result_code="ERR_INVALID_SOURCE_KIND"`.
   - **Bảo vệ Type-check trước Lookup:** Luôn kiểm tra `isinstance(source_kind, str)` trước khi tra cứu set/membership để tránh phát sinh `TypeError: unhashable type: 'list'` hay `unhashable type: 'dict'`.
   - **Chính sách Xem Metadata (`intent == "inspect"`):** Cho phép xem thông số kỹ thuật metadata ngay từ đầu (Step 1) dựa trên `asset` Mapping mà không bắt buộc thẩm định tính hợp lệ của file media vật lý.

2. **Bọc Ngoại Lệ An Toàn Cho `realpath_fn` (`_safe_resolve_path`):**
   - Nếu `path_val` rỗng, không phải `str`/`Path` $\to$ trả về `ERR_MEDIA_PATH_EMPTY` (`status="missing"`).
   - Nếu `realpath_fn` ném `PermissionError` hoặc `OSError` với `errno` là `EACCES`, `EPERM`, `EIO` $\to$ trả về `ERR_FILESYSTEM_IO_OR_PERMISSION` (`status="offline"`).
   - Nếu `realpath_fn` ném `FileNotFoundError` hoặc `OSError` với `errno` là `ENOENT` $\to$ trả về `ERR_FILE_NOT_FOUND` (`status="missing"`).
   - Nếu `realpath_fn` ném ngoại lệ khác (kế thừa `Exception`) $\to$ trả về `ERR_PATH_RESOLVE_FAILURE` (`status="offline"`).
   - Nếu `realpath_fn` trả về giá trị không hợp lệ (`None`, chuỗi rỗng, không phải `str`/`Path`) $\to$ trả về `ERR_PATH_RESOLVE_FAILURE` (`status="offline"`).

3. **Bọc Ngoại Lệ An Toàn Cho `stat_fn` (`_safe_stat_file`):**
   - Bắt các ngoại lệ `FileNotFoundError`, `ENOENT` $\to$ `ERR_FILE_NOT_FOUND` (`status="missing"`).
   - Bắt các ngoại lệ `PermissionError`, `EACCES`, `EPERM`, `EIO` $\to$ `ERR_FILESYSTEM_IO_OR_PERMISSION` (`status="offline"`).
   - Bắt các ngoại lệ khác $\to$ `ERR_STAT_FAILURE` / `ERR_FILESYSTEM_ACCESS` (`status="offline"`).
   - Kiểm tra kết quả `st`: Bắt buộc phải có thuộc tính `st_mode` có kiểu `int`, nếu thiếu hoặc sai kiểu $\to$ trả về `ERR_STAT_FAILURE` (`status="offline"`).
   - Kiểm tra định dạng qua `stat.S_ISREG(mode)`: Nếu là thư mục $\to$ `ERR_PATH_IS_DIRECTORY`, nếu không phải file thông thường $\to$ `ERR_NOT_REGULAR_FILE` (`status="missing"`).

4. **Bọc Ngoại Lệ Truy Cập Thuộc Tính `StorageManager`:**
   - Việc gọi `storage_provider()`, truy cập thuộc tính `storage.online`, `storage.root`, hoặc `storage._config` được bọc toàn diện trong `try...except Exception`:
     Nếu phát sinh ngoại lệ $\to$ trả về `ERR_STORAGE_PROVIDER_FAILURE` (`status="offline"`).
   - Nếu `_config` không phải Mapping $\to$ fallback an toàn sang `getattr(storage, "library_id", None)` mà không gây `AttributeError`.

5. **Nguyên Tắc Root / Path Checks (Best-Effort With Fail-Closed):**
   - Quá trình kiểm tra ranh giới thư mục gốc (`root`) và ranh giới an toàn (`safe_relative`) được thực thi theo phương thức best-effort nhưng luôn đảm bảo tính chất fail-closed: Bất kỳ sai lệch, lỗi cú pháp `..`, hoặc thoát symlink nào cũng đều dẫn đến từ chối thao tác ngay lập tức.

---

## 2. TRÌNH TỰ ĐÁNH GIÁ (ORDER OF PRECEDENCE) [CODE_VERIFIED]

1. **Thẩm tra Định dạng Đầu vào (Input Validation Gate):** `ERR_INVALID_ASSET_INPUT`.
2. **Thẩm tra Ý định Thao tác (Intent Gate):** Hỗ trợ `"insert"` và `"inspect"` (`OK_INSPECT_ONLY`). Intent lạ $\to$ `ERR_UNKNOWN_INTENT`.
3. **Cổng Chặn Tài Nguyên Staging (Staging Gate):** `availability == "staging"` $\to$ `ERR_STAGING_UNPUBLISHED`.
4. **Xác Thực `source_kind` (Source Kind Validation Gate):** Explicit invalid $\to$ `ERR_INVALID_SOURCE_KIND`. None/khuyết thiếu $\to$ chuyển tiếp sang legacy.
5. **Ngoại Lệ Hướng Dẫn Chuyển Cảnh Native Resolve:** `category in ("trans", "transition")` $\to$ `OK_NATIVE_TRANSITION_GUIDANCE`.
6. **Cổng Preset Sinh Mã Động (Procedural Generator Gate):** Chỉ cho phép khi có generator runtime thực sự (như `TextStylePreset`). Cấm tuyệt đối fallback cho managed text templates.
7. **Thẩm Định Tài Nguyên Kho Chính (Managed Asset Gate):** Đối chiếu provenance (`origin_library_id`, `origin_root`), identity gate, ranh giới root, lexical traversal, symlink escape, và `_safe_stat_file`.
8. **Thẩm Định Tài Nguyên Liên Kết Ngoài (Linked Asset Gate):** `external_path` là nguồn chân lý, đối chiếu conflict, lexical traversal, và `_safe_stat_file`.
9. **Thẩm Định Tài Nguyên Tích Hợp (Builtin), File-backed Generated và Legacy Thường:** Cached status check, lexical traversal, `_safe_resolve_path`, và `_safe_stat_file` (phân loại `OK_BUILTIN_MEDIA_VERIFIED`, `OK_GENERATED_MEDIA_VERIFIED`, `OK_LEGACY_MEDIA`).

---

## 3. TÍCH HỢP TẠI `InsertService.insert_async` (FIRST CONSUMER ONLY)

- **Người tiêu dùng duy nhất (First Consumer):** Điểm chặn được đặt tại đầu hàm `InsertService.insert_async` sau `to_asset_dict(asset)`.
- **Hành vi khi bị chặn:** Trả về `InsertResult(ok=False)`, gọi 1 lần `callback`, phát 1 lần signal `insertion_finished`, 0 tác vụ Resolve Gateway, 0 ghi nhận undo history.
- **Wrapper đồng bộ `insert()`:** Trả về tức thì mà không khởi động `QTimer` và không enter `QEventLoop.exec()`.
- **Hành vi khi cho phép:** Sử dụng `guard_res.resolved_path` an toàn cho dispatch; giữ nguyên luồng native guidance và các logic lõi audio/LUT/fusion.

---

## 4. RANH GIỚI VÀ CÁC THÀNH PHẦN TRÌ HOÃN (DEFERRED ROUTES)

> [!WARNING]
> Lát cắt `G04-04C2-I-R2` chỉ kích hoạt Action Guard tại một consumer duy nhất: `InsertService.insert_async`.
> Tuyệt đối không tự ý tuyên bố toàn bộ ứng dụng an toàn ("Safe across whole app").

Các thành phần sau đây tiếp tục được **TRÌ HOÃN (DEFERRED)** một cách có chủ đích:
1. **Direct Video Playback trong Studio:** Chưa gắn guard trực tiếp trước khi nạp vào video player widget.
2. **Fusion Node Pasting Trực Tiếp:** Các đường dẫn paste trực tiếp vào node graph mà không qua `InsertService` chưa có guard riêng.
3. **Kéo Thả (Drag & Drop Consumer):** Chưa có action guard chặn drag event tại tầng View/Delegate.
4. **Hover Preview Consumer:** Bộ xem trước khi rê chuột vẫn hoạt động theo luồng cache/preview hiện hành.
5. **Vô Hiệu Hóa Mô Hình Hàng Loạt (Model-Level Invalidation):** Việc tự động cập nhật lại toàn bộ `AssetModel` khi kho offline sẽ được thực hiện tại tầng Model/View riêng biệt.
