# G06-06C3-S2c — Thiết Kế Kiến Trúc Hoàn Chỉnh: Ràng Buộc Ngữ Cảnh Sáng Tạo Cho Tiếp Nhận Bản Thảo & Kịch Bản Tổng Quan Dự Án
*(Creative Context Binding Design for Batch Draft Intake and Project Overview)*

**Mã tài liệu:** `docs/architecture/06C3-S2c-draft-overview-binding-design.md`  
**Trạng thái:** BACKEND_IMPLEMENTED (Đã triển khai mã nguồn backend hoàn tất — Chờ Codex Review)  
**Nhiệm vụ:** `G06-06C3-S2c-BACKEND-DRAFT-OVERVIEW-SLICE`  
**Tác giả:** Antigravity (Pair Programming cùng Tech Lead Codex)  
**Ngày cập nhật:** 2026-10-30  
**Ràng buộc:** Đã triển khai xong đúng 2 module sản xuất (`src/core/project_overview_contracts.py` và `src/ui/director/workers/prompt_preparation_worker.py`). Mọi module UI và hợp đồng khác đóng băng hoàn toàn (FROZEN). Giao diện UI ReviewPage và DirectorWindow được đánh dấu DEFERRED.

---

## 1. Cây Gọi Hàm Thực Tế & Rào Chắn Hiện Hành (Actual Callgraph and Current Gates)

### 1.1. Hiện trạng chuỗi API thực tế (CODE_VERIFIED)

1. **Rào chắn Thẩm định Request (`PromptPreparationRequest`):**
   - File: `src/ui/director/workers/prompt_preparation_worker.py:1130-1176`
   - Phương thức: `PromptPreparationRequest.__post_init__`
   - *Rào chắn hiện hành:*
     ```python
     # Dòng 1175-1176:
     if owned_cc != empty_cc and self.intent not in ("copy_video", "run_ai", "prepare_batches", "copy_batch"):
         raise ValueError(f"ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT: creative_context chỉ được hỗ trợ cho intent ('copy_video', 'run_ai', 'prepare_batches', 'copy_batch'), nhận intent: {self.intent!r}")
     ```
   - *Hệ quả:* Cả `validate_batch_draft` và `copy_project_overview` hiện đang bị từ chối fail-closed với ngoại lệ `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT` nếu truyền vào `creative_context` không rỗng.

2. **Rào chắn Sở hữu Worker Constructor & Run Gate (`PromptPreparationWorker`):**
   - File: `src/ui/director/workers/prompt_preparation_worker.py:1180-1288`
   - Phương thức: `PromptPreparationWorker.__init__` (dòng 1180-1245) và `PromptPreparationWorker.run` (dòng 1270-1288)
   - *Hiện trạng:* Constructor bắt giữ bản sao sở hữu độc lập `_captured_creative_context` qua `normalize_creative_context(raw_cc)`. Rào chắn run gate tại dòng 1277-1283 sao chép danh sách cho phép từ Request và emit `unsupported_intent` trước bất kỳ thao tác I/O nào.

3. **Nhánh Thẩm định Bản thảo Phân lô (`validate_batch_draft`):**
   - File: `src/ui/director/workers/prompt_preparation_worker.py:1564-1863`
   - Phương thức: `PromptPreparationWorker.run` (khối `req.intent == "validate_batch_draft"`)
   - *Cơ chế hiện hành:*
     - Dòng 1570: Thẩm định snapshot qua `validate_and_normalize_snapshot`.
     - Dòng 1580-1655: Đối soát 6 trường expected binding bắt buộc (`expected_snapshot_id`, `expected_batch_id`, `expected_rendered_sha256`, `expected_formatter_policy`, `expected_manifest_id`, `expected_project_name`).
     - Dòng 1696-1705: Thẩm định sự tồn tại của `source_ranges` (Prepared Geometry Check).
     - Dòng 1707-1750: Pre-validation source recheck (kiểm tra tồn tại và mtime/size/inode tệp nguồn).
     - Dòng 1752-1803: Gọi pure validator `validate_batch_draft_payload(raw_json, selected_batch.source_ranges, ...)`.
     - Dòng 1810-1835: Post-validation source recheck.
     - Dòng 1842-1862: Emit terminal `success` mang `validated_entry` dictionary.
   - *Khoảng trống:* Chưa có kiểm tra so khớp giữa ngữ cảnh sáng tạo đang chạy và snapshot. Một snapshot đã nhúng `creative_context_signature` hiện chưa đối soát chữ ký này tại nhánh `validate_batch_draft`.

4. **Nhánh Kịch bản Tổng quan Dự án (`copy_project_overview`):**
   - File: `src/ui/director/workers/prompt_preparation_worker.py:1865-2250`
   - Phương thức: `PromptPreparationWorker.run` (khối `req.intent == "copy_project_overview"`)
   - *Cơ chế hiện hành:*
     - Dòng 1879-1915: Thẩm định 4 trường expected binding (`expected_snapshot_id`, `expected_manifest_id`, `expected_project_name`, `expected_ledger_signature`).
     - Dòng 1916-1923: Thẩm định `req.project_overview_context` kiểu `ProjectOverviewContextInput`.
     - Dòng 1926-1970: Đối soát snapshot và `ui_context_signature` (bắt buộc, zero fallback).
     - Dòng 2102-2121: Đối soát chữ ký ledger tính toán với `expected_ledger_signature`.
     - Dòng 2146-2180: Pre-check tệp nguồn cơ sở.
     - Dòng 2187: Gọi pure core formatter `render_project_overview_prompt(ctx_input)`.
     - Dòng 2217-2222: Post-check tệp nguồn cơ sở.
     - Dòng 2228-2242: Đóng gói `PreparedProjectOverviewArtifact`.
   - *Khoảng trống:* `ProjectOverviewContextInput` hiện chỉ nhận `(manifest_id, project_name, snapshot_id, batches, received_drafts, unaccounted_clips, style_intent, max_prompt_bytes)` (xem `src/core/project_overview_contracts.py:153-163`), chưa có trường nạp `creative_context` và chưa kết xuất khối canonical JSON ngữ cảnh sáng tạo trong kịch bản tổng quan.

5. **Sổ cái Tiếp nhận Bản thảo (`ReviewPage._batch_drafts_ledger`):**
   - File: `src/ui/director/pages/review_page.py:4292-4425`
   - Cấu trúc: Khóa `(snapshot_id, batch_id) -> ValidatedBatchDraftEntry`.
   - Cổng thẩm định: Dòng 4393 gọi `verify_validated_entry_integrity`. Dòng 4412 kiểm tra chống ghi đè đua `Replacement Race Check`.

---

## 2. Bảng Sự Thật Ràng Buộc & Không Gian Danh Tính (Binding and Domain Truth Table)

### 2.1. Phân định rõ ràng ba không gian danh tính độc lập (Three Independent Identity Domains)

1. **`creative_context_signature`:** Chữ ký SHA256 64-hex lowercase chuẩn tắc sinh từ đối tượng `CreativeContext` chuẩn hóa (`compute_creative_context_signature`). Nằm trong `PreparedBatchSnapshot`, tham gia trực tiếp vào `compute_snapshot_id` với domain `creative_context_sig_v1:{sig}\n` (khi non-None).
2. **`ui_context_signature`:** Chữ ký cấu hình toàn trang ReviewPage. Hoàn toàn độc lập với ngữ cảnh sáng tạo, được gán vào snapshot thông qua `dataclasses.replace` (ReviewPage dòng 4122) và **KHÔNG** tham gia vào `compute_snapshot_id`.
3. **`ledger_signature`:** Chữ ký tổng hợp các bản thảo phân lô đã được tiếp nhận trong sổ cái, tính từ `compute_project_overview_ledger_signature(snapshot_id, received_drafts)`.

### 2.2. Ma trận Sự thật Ràng buộc Toàn diện (Exhaustive Binding Truth Table)

Bảng dưới đây quy định hành vi bắt buộc cho cả hai intent `validate_batch_draft` và `copy_project_overview`:

| Trường hợp | Snapshot `creative_context_signature` | Request / Current `captured_cc` | Trạng thái Chữ ký Request (`captured_cc_sig`) | Hành vi Thẩm định & Rào chắn (Gating Behavior) | Kết quả Đầu ra (Terminal Emission) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **C1. Legacy Parity** | `None` (Legacy snapshot) | `None` hoặc `{}` (Empty) | `None` | **PASS (Legacy Matching)**. Bảo toàn tương thích ngược 100%. Không sinh khối creative context. | `success` (draft nạp sổ cái / overview tạo prompt legacy). |
| **C2. Nonempty Req + Legacy Snap** | `None` (Legacy snapshot) | Chứa brief / preset / rules / refs | `64-hex string` | **FAIL-CLOSED TRƯỚC IO**. Yêu cầu có ngữ cảnh nhưng snapshot là legacy không chứa chữ ký. | `invalid_snapshot` (ERR_STALE_SNAPSHOT), 0 IO, 0 ledger publish, 0 prompt. |
| **C3. Empty Req + Creative Snap** | `64-hex string` (Active creative snapshot) | `None` hoặc `{}` (Empty) | `None` | **FAIL-CLOSED TRƯỚC IO**. Snapshot đòi hỏi ngữ cảnh sáng tạo nhưng yêu cầu lại để trống. | `invalid_snapshot` (ERR_STALE_SNAPSHOT), 0 IO, 0 ledger publish, 0 prompt. |
| **C4. Mismatch Signatures** | `64-hex string` (Chữ ký $S_1$) | Chứa brief / rules khác snapshot | `64-hex string` (Chữ ký $S_2 \ne S_1$) | **FAIL-CLOSED TRƯỚC IO**. Chữ ký ngữ cảnh sáng tạo của yêu cầu không khớp snapshot phân lô. | `invalid_snapshot` (ERR_STALE_SNAPSHOT), 0 IO, 0 ledger publish, 0 prompt. |
| **C5. Matching Signatures** | `64-hex string` (Chữ ký $S_1$) | Cùng brief / preset / rules / refs | `64-hex string` (Chữ ký $S_1$) | **PASS (Owned Consistent Matching)**. Thỏa mãn rào chắn, tiếp tục bước kiểm tra tệp nguồn. | `success` (draft nạp sổ cái / overview prompt chứa canonical creative JSON). |

### 2.3. Nguyên tắc Ranh giới Thẩm quyền (Authority Boundary Principles)

1. **Thẩm quyền Thuộc về Ứng dụng (App-Owned Authority):** Bản thảo AI gửi về (`batch_draft_text`) có thể chứa siêu dữ liệu nhắc lại (echoed metadata). Hệ thống **TUYỆT ĐỐI KHÔNG** coi siêu dữ liệu do AI phản hồi là thẩm quyền chân lý của ứng dụng. Không tin cậy bất kỳ trường sáng tạo nào trong JSON của AI.
2. **Hình học Bất biến theo Ngữ nghĩa (No Geometry Mutation via Brief Semantics):** Bộ thẩm định hình học `validate_batch_draft` không sửa đổi, mở rộng hay co ngắn các đoạn cắt (`cuts`), tốc độ (`speed`) hay dải nguồn (`source_ranges`) dựa trên ngữ nghĩa của chỉ đạo nghệ thuật (`free_brief`). Hình học được đo đạc và đối soát duy nhất theo sự thật vật lý từ scan cache và snapshot (`source_ranges`).
3. **Chống Thay thế Đua & Vô hiệu hóa Snapshot Lỗi thời (Replacement Race Isolation & Snapshot Invalidation):** Bản thảo được nạp vào sổ cái `_batch_drafts_ledger` với khóa `(snapshot_id, batch_id)`. Lưu ý đính chính kiến trúc: việc người dùng gõ/chỉnh sửa brief trên UI **KHÔNG** tự động làm đổi `snapshot_id` ngay lập tức trong bộ nhớ; chỉ khi hành động `prepare_batches` được kích hoạt lại thì `snapshot_id` mới mới được sinh ra (mang hàm băm `creative_context_signature` mới). Do đó, tại tầng UI tương lai, cơ chế vô hiệu hóa (current-context invalidation) khi người dùng sửa `StylePage` là bắt buộc để chặn người dùng gửi yêu cầu cũ với snapshot đã lỗi thời. Khi snapshot mới được sinh ra, các bản thảo cũ thuộc snapshot cũ sẽ không bao giờ bị ghi đè nhầm vào snapshot mới.

---

## 3. Bộ Định Dạng, Trần Ngân Sách, Tính Tương Thích & Đường Dẫn Dữ Liệu Sở Hữu (Exact Formatter, Budget Cap, Compatibility and Owned Data Path)

### 3.1. Mở rộng Hợp đồng Dữ liệu `ProjectOverviewContextInput` tương thích ngược

Tại `src/core/project_overview_contracts.py:153-189`:
- Thêm trường tùy chọn `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None` vào **CUỐI** danh sách tham số của `ProjectOverviewContextInput`.
- Trong `__post_init__`:
  ```python
  # Chuẩn hóa phòng vệ và sở hữu bất biến:
  if self.creative_context is not None:
      if isinstance(self.creative_context, bool) or not isinstance(self.creative_context, (Mapping, CreativeContext)):
          raise ValueError(f"creative_context phải là Mapping hoặc CreativeContext, nhận: {type(self.creative_context).__name__}")
      try:
          owned_cc = normalize_creative_context(self.creative_context)
          object.__setattr__(self, "creative_context", owned_cc)
      except Exception as e:
          raise ValueError(f"creative_context không hợp lệ trong ProjectOverviewContextInput: {e}")
  ```
- **Tương thích ngược (Backward Compatibility):** Khi không truyền `creative_context` hoặc truyền `None`/`{}`, `self.creative_context` nhận `None` hoặc `CreativeContext()` rỗng. Mọi mã nguồn gọi hiện hành giữ nguyên hành vi 100%.

### 3.2. Kết xuất Khối Ngữ Cảnh Sáng Tạo trong `render_project_overview_prompt`

Tại `src/core/project_overview_contracts.py`:
1. **Tuần tự hóa JSON Khối Ngữ cảnh Dự án (`context_json_dict`):**
   - Lưu ý đính chính: `CreativeContext` là pure dataclass và **KHÔNG** có phương thức `.to_dict()`. Hàm thuần túy `creative_context_to_dict(context_input.creative_context)` được sử dụng để chuyển đổi sang dictionary chuẩn tắc.
   - Khi `context_input.creative_context` không rỗng:
     Bổ sung khóa `"creative_context"` duy nhất một lần vào `context_json_dict`:
     ```python
     if norm_cc != empty_cc:
         context_json_dict["creative_context"] = creative_context_to_dict(norm_cc)
     ```
2. **Khung Tĩnh Chỉ dẫn Siêu dữ liệu (Static Metadata Framing):**
   - Chỉ dẫn đạo diễn và tài liệu tham khảo được tích hợp trong cấu trúc JSON dữ liệu có sẵn. Khung tĩnh nêu rõ:
     "Yêu cầu đạo diễn và tài liệu tham khảo áp dụng cho toàn bộ cấu trúc mạch phim vĩ mô. Đường dẫn tệp cục bộ và liên kết URL chỉ là siêu dữ liệu (metadata), hệ thống chưa xem, tải về hay phân tích nội dung tệp/liên kết thực tế và không bảo đảm tuân thủ ngữ nghĩa tự động."
   - Bảo toàn đúng 2 khối JSON nguyên bản (khối dữ liệu `context_json_dict` và khối schema đầu ra), **TUYỆT ĐỐI KHÔNG NHÂN ĐÔI KHỐI JSON** (no duplicated creative JSON block) và không nội suy thô chuỗi người dùng vào câu lệnh chỉ dẫn.
3. **Áp trần Ngân sách Nghiêm ngặt (Strict Budget Cap $\le 32768$ Bytes):**
   - Toàn bộ nội dung prompt sau khi render được mã hóa UTF-8:
     `prompt_bytes = full_rendered_prompt.encode("utf-8")`
     `required_bytes = len(prompt_bytes)`
   - So sánh với `max_bytes = context_input.max_prompt_bytes` (bắt buộc $1 \le max\_bytes \le 32768$):
     * Nếu `required_bytes <= max_bytes`: Chấp thuận, tính `rendered_sha256 = hashlib.sha256(prompt_bytes).hexdigest()`, trả về `ProjectOverviewRenderResult(ok=True, ...)`.
     * Nếu `required_bytes > max_bytes`: **FAIL-CLOSED NGAY LẬP TỨC** với mã lỗi `ERR_OVERVIEW_BUDGET_OVERFLOW`. Tuyệt đối **KHÔNG CẮT XÉN** âm thầm (zero silent trimming), không lược bỏ các rules, brief, hay bản tóm tắt chiến lược lô.

### 3.3. Chính Sách Xử Lý Ngữ Cảnh Trùng Lặp (Duplicate Context Ownership & Conflict Policy)

Tại `PromptPreparationWorker`:
1. **Sở hữu Độc lập tại Constructor (Independent Construction Capture):**
   Constructor độc lập chuẩn hóa và sở hữu cả hai nguồn: `_captured_creative_context` từ `request.creative_context` và `_captured_overview_cc` từ `request.project_overview_context.creative_context`. Mọi đột biến của caller sau khởi tạo hoặc tại barrier đều bị cô lập hoàn toàn.
2. **Chính sách Giải quyết Ngữ cảnh Trùng lặp (Resolution Policy):**
   - Trường hợp Legacy / Omitted: Nếu `ctx_input.creative_context` để trống (`None` hoặc `{}`), nó được phép tiếp nhận giá trị sở hữu từ `_captured_creative_context` sau khi vượt qua cổng đối soát chữ ký snapshot.
   - Trường hợp Explicit Nonempty: Nếu `ctx_input.creative_context` có giá trị rõ ràng, chữ ký của nó **BẮT BUỘC PHẢI KHỚP TUYỆT ĐỐI** với `_captured_creative_context` và `snapshot.creative_context_signature`.
   - Xung đột hoặc Sai dạng (Conflict / Malformed): Nếu có bất kỳ sự sai lệch chữ ký hoặc lỗi dạng thức nào giữa hai trường ngữ cảnh, worker **TỪ CHỐI NGAY LẬP TỨC TRƯỚC BẤT KỲ IO NÀO** (`invalid_snapshot` / `ERR_STALE_SNAPSHOT`), tuyệt đối không âm thầm ghi đè hay nuốt lỗi.

### 3.4. Đóng gói Artifact & Hàm Thẩm định Nghiêm ngặt (`validate_project_overview_result_against_artifact`)

Tại `src/ui/director/workers/prompt_preparation_worker.py`:
- Dataclass `PreparedProjectOverviewArtifact` được bổ sung trường `creative_context_signature: Optional[str] = None` với xác thực định dạng 64-hex lowercase nghiêm ngặt tại `__post_init__`.
- Hàm thẩm định `validate_project_overview_result_against_artifact`:
  * Đối soát chữ ký ngữ cảnh sáng tạo: artifact creative đòi hỏi kết quả có chữ ký 64-hex khớp chính xác; artifact legacy từ chối mọi kết quả có chữ ký; chữ ký viết hoa, thiếu hoặc bị làm giả đều bị từ chối `False`.
  * Tính toán lại độc lập `hashlib.sha256` của `rendered_prompt` và kiểm tra độ dài bytes với artifact sở hữu.
  * Giữ nguyên mọi bước đối soát metadata, ledger, snapshot và coverage trước đây.

### 3.5. Cơ chế Bảo vệ Nhất quán Kiểu Dữ liệu & Lưu trữ Thực thể (Typed Consistency Guard & Instance Storage Validation — G06-06C3-S2c-R1)

Theo đánh giá mã nguồn của Tech Lead Codex (`docs/architecture/06C3-S2c-codex-review.md`), Python `@dataclass(frozen=True)` khi khai báo giá trị mặc định (như `creative_context: Optional[...] = None`) sẽ gán thuộc tính mặc định ở cấp độ lớp (`Class.creative_context = None`). Do đó, nếu một thuộc tính thực thể bị xóa bỏ thông qua `object.__delattr__(inst, "creative_context")`, phương thức `getattr(inst, "creative_context", None)` hoặc `hasattr(inst, "creative_context")` vẫn âm thầm truy xuất thuộc tính mặc định của lớp cha, dẫn đến việc nuốt lỗi và kết xuất sai lệch ngữ cảnh sáng tạo:
1. **Trợ thủ Kiểm định Lưu trữ Thực thể (`_has_initialized_field`):**
   - Được triển khai cục bộ (private helper) tại cả `src/core/project_overview_contracts.py` và `src/ui/director/workers/prompt_preparation_worker.py`.
   - Hàm kiểm tra trực tiếp từ điển lưu trữ cấp thực thể `object.__getattribute__(instance, "__dict__")`, hoàn toàn không dựa dẫm vào fallback thuộc tính cấp lớp hay truthiness.
2. **Bảo vệ Bộ Kết xuất (`render_project_overview_prompt`):**
   - Thẩm định `_has_initialized_field(context_input, "creative_context")`.
   - Trường hợp trường bị xóa (`object.__delattr__`) hoặc đối tượng bị làm giả qua `object.__new__`: Fail-closed ngay lập tức, trả về `ok=False`, `error_code="ERR_OVERVIEW_INVALID_CONTEXT"`, `rendered_prompt=""`.
   - Bảo toàn 100% tính tương thích cho các trường hợp legacy hợp lệ: khởi tạo mặc định `None`, truyền `None` tường minh, hoặc từ điển rỗng `{}`.
3. **Bảo vệ Đối soát Artifact (`validate_project_overview_result_against_artifact`):**
   - Thẩm định `_has_initialized_field(artifact, "creative_context_signature")`.
   - Nếu thuộc tính bị xóa khỏi thực thể artifact hoặc artifact giả mạo thiếu trường, hàm trả về `False` dứt khoát, ngay cả khi transport payload cố tình bỏ khóa chữ ký để giả lập legacy.
4. **Bảo vệ Bắt giữ Khởi tạo Worker (`PromptPreparationWorker.__init__` & Run Gate):**
   - Constructor kiểm tra `_has_initialized_field(request.project_overview_context, "creative_context")`.
   - Nếu phát hiện trường bị thiếu/xóa trên `ProjectOverviewContextInput`, worker ghi nhận `_captured_overview_cc_error` và nhánh `copy_project_overview` tại `run` sẽ phát ngay `(False, "invalid_snapshot", ...)` trước bất kỳ thao tác I/O nào.
   - Ngăn chặn hoàn toàn việc `dataclasses.replace` vô tình che giấu một trường bị thiếu.

---

## 4. Bộ $\le$ 10 Kịch Bản Nghiệm Thu (Finite $\le 10$ Acceptance Cases)

| Mã Case | Tên Kịch Bản | Dữ Liệu Đầu Vào | Điều Kiện Thẩm Định Ranh Giới | Kết Quả Kỳ Vọng (Assertions) |
| :--- | :--- | :--- | :--- | :--- |
| **TC-01** | Legacy Parity Draft & Overview | `creative_context = None` hoặc `{}` cho cả draft intake và overview | Snapshot legacy không có `creative_context_signature` | Khớp 100% từng byte legacy; không sinh khối Markdown creative context; 0 ngoại lệ. |
| **TC-02** | Valid Draft Intake Matching Signature | Snapshot chứa $Sig_A$; Request chứa $CC_A$ khớp chữ ký; payload draft hợp lệ | So khớp `captured_cc_sig == snapshot.creative_context_signature` | Nhánh `validate_batch_draft` thành công; emit `(True, "success", payload)`; nạp vào sổ cái. |
| **TC-03** | Mismatched Draft Intake Denied Before IO | Snapshot chứa $Sig_A$; Request chứa $CC_B$ (lệch brief/rules) hoặc `None` | Kiểm tra mismatch TRƯỚC pre-validation source stat check | Bị từ chối ngay lập tức; emit `(False, "invalid_snapshot", ...)`; **0 source stat/isfile/exists calls**; 0 ledger publish. |
| **TC-04** | Draft Intake Ignores AI-Echoed Creative Data | JSON draft chứa trường sáng tạo giả mạo (`"creative_context": {"fake": 1}`) | Validator chỉ đối soát hình học dải nguồn `source_ranges` | Bỏ qua dữ liệu giả mạo; tính toàn vẹn hình học quyết định tính hợp lệ; không thay đổi cuts/speed. |
| **TC-05** | Overview Prompt Includes Canonical Creative JSON | Snapshot chứa $Sig_A$; Request chứa $CC_A$; đầy đủ brief, rules, refs | `ProjectOverviewContextInput` nạp $CC_A$; render prompt | Prompt chứa khối canonical JSON; giải mã `json.loads` khớp 100%; `required_bytes <= 32768`. |
| **TC-06** | Overview Budget Overflow Fails Closed | Brief hoặc rules quá lớn khiến tổng prompt vượt quá `max_prompt_bytes` | Đo đạc chính xác toàn bộ kích thước UTF-8 bytes | Trả về `ERR_OVERVIEW_BUDGET_OVERFLOW` kèm `required_bytes` chính xác; **0 prompt publication**; không cắt xén. |
| **TC-07** | Overview Mismatch Denied Before IO | Snapshot chứa $Sig_A$; Request chứa $CC_B$ hoặc rỗng | So khớp chữ ký tại cổng đầu vào của `copy_project_overview` | Emit `(False, "invalid_snapshot", ...)`; **0 source IO calls**; 0 artifact publication. |
| **TC-08** | Overview Artifact Retains Matching Signature | Worker chạy thành công nhánh `copy_project_overview` | Kiểm tra thuộc tính của `PreparedProjectOverviewArtifact` | `artifact.creative_context_signature == Sig_A`; độc lập với `ledger_signature` và `ui_context_signature`. |
| **TC-09** | Caller Mutation Isolation Before & After Start | Đột biến dict của caller trước start và gán đè request tại rào cản barrier | Worker bắt giữ owned snapshot độc lập tại `__init__` | Kết quả prompt và artifact giữ nguyên brief ban đầu; không bị ảnh hưởng bởi đột biến. |
| **TC-10** | Post-Render Cancel / Source Deletion Fails Closed | Render overview thành công, sau đó xóa tệp nguồn hoặc cancel worker | Post-validation source recheck và cờ `_is_cancelled` | Emit `source_missing` hoặc `cancelled`; **0 success**; không công bố artifact lỗi thời. |

---

## 5. Đề Xuất Duy Nhất 1 Lát Cắt Triển Khai & Điều Kiện Tiên Quyết UI (Next Implementation Slice & UI Prerequisites)

### 5.1. Đề xuất Lát Cắt Triển Khai Duy Nhất: `G06-06C3-S2c-BACKEND-DRAFT-OVERVIEW-SLICE`

Thực hiện chỉnh sửa mã nguồn backend trên **đúng 2 module sản xuất**:

1. **Module 1: `src/core/project_overview_contracts.py`**
   - Mở rộng dataclass `ProjectOverviewContextInput` thêm trường tùy chọn ở cuối:
     `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None`
   - Bổ sung logic chuẩn hóa phòng vệ tại `__post_init__` qua `normalize_creative_context`.
   - Cập nhật hàm `render_project_overview_prompt` để bổ sung khối canonical JSON của `creative_context` vào cấu trúc ngữ cảnh và văn bản kịch bản tổng quan, đo đạc kích thước đầy đủ trong trần `max_prompt_bytes <= 32768`.

2. **Module 2: `src/ui/director/workers/prompt_preparation_worker.py`**
   - Mở rộng danh sách intent được hỗ trợ ngữ cảnh sáng tạo trong `PromptPreparationRequest.__post_init__` và `PromptPreparationWorker.run`:
     Bổ sung `"validate_batch_draft"` và `"copy_project_overview"` vào tuple cho phép:
     `("copy_video", "run_ai", "prepare_batches", "copy_batch", "validate_batch_draft", "copy_project_overview")`
   - Tại nhánh `validate_batch_draft` (dòng 1564): Cài đặt rào chắn so khớp `captured_cc_sig` với `snapshot.creative_context_signature` trước khi thực hiện source IO. Mismatch hoặc lệch legacy/creative phát `invalid_snapshot`.
   - Tại nhánh `copy_project_overview` (dòng 1865): Cài đặt rào chắn so khớp `captured_cc_sig` với `snapshot.creative_context_signature`, truyền `captured_cc` vào `ProjectOverviewContextInput`, và nạp `creative_context_signature` vào `PreparedProjectOverviewArtifact`.

### 5.2. Hiện Trạng Triển Khai Điều Phối Giao Diện UI Thực Tế (G06-06C3-S2d-UI-ROLLOUT-I)

**Trạng thái:** `UI_IMPLEMENTED_AND_VERIFIED`  
Đã triển khai hoàn tất và kiểm chứng toàn diện việc nối dây UI điều phối tại **đúng 2 module sản xuất**: `src/ui/director/director_window.py` và `src/ui/director/pages/review_page.py`.

1. **Tuân thủ Tuyệt đối `StylePage.save_to_config == False`:**
   Tại `DirectorWindow._sync_all_pages_to_config`: Thẩm định `self.page_style.save_to_config(self.config)` trước tiên. Nếu trả về `False` (do bản thảo chứa lỗi hoặc vượt trần ngân sách), hàm trả về `False` ngay lập tức. Chặn đứng chuyển bước (`_on_btn_primary_clicked`), dựng timeline (`_start_build_timeline`) và chạy AI (`_on_run_ai_script`). Zero fallback về cấu hình cũ, bảo tồn nguyên vẹn bản thảo lỗi của người dùng.
2. **Ràng buộc Chữ ký Độc lập tại ReviewPage Dispatcher & Terminal:**
   Phân tách hoàn toàn giữa `expected_creative_context_signature` (bắt buộc 64 hex lowercase khi có ngữ cảnh; explicit `None` cho legacy) và `ui_context_signature` (mở rộng tuple 17 phần tử có tiền tố `cc:` cho ngữ cảnh không rỗng). Đối soát 3 chiều giữa pending record, current context và result payload tại mọi intent.
3. **Vô hiệu hóa Trạng thái khi Bản thảo Sáng tạo Thay đổi (Invalidation on Edit):**
   Khi người dùng sửa đổi bất kỳ trường nào trong `StylePage` (`free_brief`, `style_preset`, `must_keep_rules`, `must_not_rules`, thứ tự hoặc ghi chú `reference_items`):
   - Tín hiệu `draft_changed` gọi `notify_creative_context_changed()`.
   - Vô hiệu hóa `_prepared_prompt_snapshot`, hủy các lô đã phân, hiển thị cảnh báo trung thực `"⚠ Yêu cầu thay đổi, chuẩn bị lại."`.
   - Getters trả về chuỗi rỗng; chặn mọi hành vi sao chép stale; không xóa trắng bản thảo hay lịch sử ledger.
4. **Nhất quán Prompt Chuẩn bị Giữa các Provider:**
   `copy_video` và `run_ai` sử dụng chung đúng văn bản prompt và chữ ký chuẩn bị. Bộ bảo vệ exactly-once ngăn chặn phát lệnh lặp tới controller.
5. **Cách ly Tuyệt đối Phân hệ V2 Kinetic:**
   Phân hệ Kinetic (`auto_plan`, `copy_kinetic`) tiếp tục tách biệt độc lập, không tiêu thụ `creative_context` và giữ nguyên 100% kiểm thử routing hiện có.
6. **Bằng chứng Nghiệm thu:**
   - 224/224 ca kiểm thử Pytest đạt 100% (`tests/ui/test_creative_context_prompt_routing.py` + 5 test file routing/batch/intake/overview/kinetic).
   - Component desktop smoke harness (`task/desktop_verify_06c3_creative_ui.py`): 4/4 kịch bản đạt `COMPONENT_DESKTOP_VERIFIED`, probe fault-injection cleanup đạt mã thoát 2 an toàn.

> [!NOTE]
> Giao diện xem xét / phê duyệt đề xuất chung (Common Proposal Review / Approval UI — S3) tiếp tục được **HOÃN LẠI (DEFERRED)**. Mọi cuộc gọi mạng provider và thay đổi timeline Resolve đều nằm ngoài phạm vi lát cắt này.

---
*Tài liệu thiết kế G06-06C3-S2c đã hoàn tất và được cập nhật hiện trạng triển khai UI thực tế.*