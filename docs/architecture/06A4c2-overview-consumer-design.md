# G06-06A4c2-D — Thiết Kế Consumer Project Overview Bất Đồng Bộ và Quyền Copy

> HIỆN HÀNH 2026-10-29: Overview consumer và 06A ACCEPTED_WITH_RECORDED_LIMITS theo 06A-gate-acceptance.md, fresh58tests/2.35s/exit0 và kế thừa desktop component limits. Active G06-06B1-I theo 06B-codex-decisions.md; các pending/review entries cũ bên dưới là lịch sử.

> **HIỆN HÀNH 2026-10-29:** Đã hoàn thành workorder **G06-06A4c2b-R1-THEN-06B-D**.
> **Tiến độ triển khai:**
> - Phần A (UI Boundary Closure): Đã hoàn tất 12/12 unit tests và 58/58 finite suite tests (`tests/ui/test_project_overview_consumer.py`, `tests/ui/test_project_overview_worker.py`, `tests/ui/test_project_overview_slot_guard.py`) với exit code 0 sạch sẽ. Toàn bộ các lỗ hổng preflight (corrupted counter `True`, corrupted object, malformed key, mismatched inner ID) và terminal corrupted ledger/pending gate đã được đóng fail-closed an toàn với nhãn lỗi tiếng Việt rõ ràng.
> - Phần B (06B Design-Only): Đã hoàn thành tài liệu thiết kế tự chứa `docs/architecture/06B-offline-asset-matching-design.md`.

**Trạng thái tài liệu:** `IMPLEMENTED_AND_BOUNDARY_CLOSED`  
**Cấp độ Worker:** `HIGH`  
**Ngày cập nhật:** 2026-10-29  
**Mục tiêu:** Cung cấp tài liệu thiết kế chuẩn xác cho adapter, vòng đời QThread bất đồng bộ, cổng thẩm quyền độ tươi (freshness authority), chính sách IO nguồn nhẹ, cơ chế kiểm định artifact thuộc sở hữu ứng dụng (App-Owned Artifact) và hành vi ghi Clipboard cho kịch bản Tổng quan dự án (Project Overview).  
**Phạm vi:** Tài liệu kỹ thuật đồng bộ giữa thiết kế ban đầu, chỉ đạo ràng buộc của Tech Lead Codex và mã nguồn thực tế đã hoàn thành tại `G06-06A4c2a-R1`.

---

## 1. CALLGRAPH + RECEIVER (ĐỐI CHIẾU SOURCE VÀ LUỒNG UX HỮU HẠN)

### 1.1. Hiện trạng đối chiếu mã nguồn (`[CODE_VERIFIED]`)
Vùng giao diện phân lô kịch bản (Batch Section) hiện được bố trí tại `src/ui/director/pages/review_page.py:750–920` bên trong `lay_batch = QVBoxLayout()` thuộc `frame_batch_section`:
1. `self.btn_prepare_batches`: nút bấm phân lô (`review_page.py:750–768`), kích hoạt `_on_prepare_batches_clicked()` (`review_page.py:4050–4068`) -> gọi `request_prompt_preparation(intent="prepare_batches")`.
2. `self.cbo_batch_selector`: combobox chọn lô (`review_page.py:769–781`), lắng nghe `currentIndexChanged` -> gọi `_on_batch_selection_changed()` (`review_page.py:4080–4097`).
3. `self.btn_copy_selected_batch`: nút sao chép lô đang chọn (`review_page.py:783–808`), kích hoạt `_on_copy_selected_batch_clicked()` (`review_page.py:4070–4078`) -> gọi `request_prompt_preparation(intent="copy_batch", selected_batch_index=sel_idx)`.
4. `self.lbl_batch_summary`: hiển thị tóm tắt lô, số clips, clip chưa phân lô, trần byte (`review_page.py:812–814`).
5. `self.txt_batch_preview`: khung xem trước prompt của lô đang chọn (`review_page.py:816–830`).
6. Khu vực nạp bản thảo lô (Batch Draft Intake) (`review_page.py:832–911`): gồm `self.txt_batch_draft_input`, `self.btn_submit_batch_draft`, `self.chk_replace_existing_draft`, `self.lbl_batch_draft_status`. Nút nạp kích hoạt `_on_submit_batch_draft_clicked()` (`review_page.py:4099–4156`) -> gọi `request_prompt_preparation(intent="validate_batch_draft", ...)`.
7. Tiến trình chuẩn bị Prompt và QThread Registry:
   - `request_prompt_preparation()` (`review_page.py:2637–2957`): thẩm định context, snapshot, hủy cooperative các worker cũ, tăng `self._prompt_generation`, tạo record `self._pending_prompt_expected`, tạo `PromptPreparationRequest` và `PromptPreparationWorker(QThread)`.
   - `_on_prompt_preparation_finished()` (`review_page.py:2959–3548`): slot Qt nhận kết quả logic từ worker nền, kiểm tra exactly-once, active request, generation match, context freshness, pending expected record authority, kiểm tra envelope, và điều phối nhánh theo `expected_intent`.
   - `_on_prompt_worker_thread_finished()` (`review_page.py:1850–1925`): slot Qt dọn dẹp vật lý `worker.deleteLater()`, loại worker khỏi `self._prompt_workers_registry`, tính aggregate busy của registry để mở/khóa các nút UI (`btn_copy_video_prompt`, `btn_prepare_batches`, `btn_copy_selected_batch`, `btn_submit_batch_draft`).

### 1.2. Đề xuất vị trí Action và Điều khiển UI (`[DEFERRED TO 06A4c2b-I]`)
Trong `lay_batch`, bên dưới `lbl_batch_summary` và ngay trên `txt_batch_preview`, bố trí một hàng điều khiển tổng quan dự án riêng biệt (`h_overview = QHBoxLayout()`):
- Nút bấm chính: `self.btn_copy_project_overview = QPushButton("📋 Sao Chép Tổng Quan Dự Án")`.
  - Mặc định khởi tạo: `setEnabled(False)`.
  - Kết nối: `self.btn_copy_project_overview.clicked.connect(self._on_copy_project_overview_clicked)`.
- Nhãn trạng thái tổng quan: `self.lbl_overview_status = QLabel("")`, font size 10px, hiển thị tiến độ và trạng thái (chưa có bản thảo, đang xử lý, đã chép, lỗi dung lượng, stale).

### 1.3. Lựa chọn duy nhất một luồng UX hữu hạn: Explicit Async Copy
Ta chọn duy nhất mô hình **Explicit Async Copy (Sao chép bất đồng bộ rõ ràng qua một thao tác người dùng)**:
1. **Lý do lựa chọn:**
   - Hoàn toàn đồng nhất với cơ chế tương tác hiện hành của `btn_copy_video_prompt` (V1) (`review_page.py:2639, 3505–3525`) và `btn_copy_selected_batch` (`review_page.py:783, 3217–3325`): Người dùng bấm nút -> giao diện hiển thị trạng thái bận -> worker QThread chạy ngầm thẩm định và kết xuất -> khi hoàn tất hợp lệ, Main Thread ghi Clipboard một lần duy nhất và đổi nhãn nút trong 2.5 giây.
   - Tránh phát sinh thêm bước trung gian thừa (Prepare-then-Copy) gây lúng túng khi người dùng muốn dán nhanh sang mô hình AI vĩ mô.
   - Dữ liệu tổng quan là prompt text hoàn chỉnh (khối JSON ngữ cảnh + hướng dẫn tổng hợp + schema mẫu), đã được giới hạn bởi trần 32KiB của core formatter.
2. **Quy tắc chuyển trạng thái hữu hạn:**
   - **Zero-draft (Chưa có bản thảo nào trong ledger):** Nút `btn_copy_project_overview` bị vô hiệu hóa (`setEnabled(False)`) nếu `_batch_drafts_ledger` cho snapshot hiện tại rỗng. Nếu người dùng bằng cách nào đó kích hoạt, `request_prompt_preparation` từ chối fail-closed ngay tại Main Thread, `lbl_overview_status` hiển thị: `"⚠ Chưa có bản thảo lô nào trong phiên để tổng hợp tổng quan."`.
   - **Partial drafts (Đã có một số lô nhưng chưa đủ 100%):** Nút `btn_copy_project_overview` được kích hoạt (`setEnabled(True)`). Formatter thuần (`project_overview_contracts.py`) đã hỗ trợ partial inputs đầy đủ: liệt kê chính xác các lô đã nhận (`received_batches_count`), chỉ rõ `missing_batch_ids`, đánh dấu `all_batch_drafts_received=False`. Khi copy thành công, `lbl_overview_status` báo: `"✓ Đã chép Tổng quan: Nhận {K}/{N} lô (Thiếu {N-K} lô chưa nạp bản thảo)."`.
   - **Budget Overflow (Vượt trần 32,768 bytes):** Worker phát hiện `rendered_bytes > max_prompt_bytes` hoặc formatter trả `ok=False, error_code=ERR_OVERVIEW_BUDGET_OVERFLOW`. Worker trả về terminal failure `budget_error`. Main Thread KHÔNG ghi clipboard, giữ nguyên MIME hiện tại của clipboard hệ thống. `lbl_overview_status` báo: `"⚠ Kịch bản tổng quan ({required_bytes} B) vượt quá trần 32,768 bytes."`.
   - **Stale Context / Stale Ledger:** Dữ liệu thay đổi lúc worker đang chạy. Main Thread phát hiện qua cổng thẩm quyền (Section 3), hủy bỏ thao tác ghi clipboard, `lbl_overview_status` báo: `"⚠ Dữ liệu đã thay đổi trong lúc chuẩn bị tổng quan. Bỏ qua kết quả cũ."`.
3. **Ứng xử với Combobox `cbo_batch_selector` (Không sao chép máy móc selected_batch gate):**
   - Với `copy_batch`: hành động gắn liền với một lô cụ thể; nếu người dùng chuyển sang lô khác trên combobox trong lúc worker đang chạy (`res_idx != current_ui_index`), kết quả lô cũ bị hủy bỏ (`review_page.py:3246–3249`).
   - Với `project_overview`: Tổng quan dự án thuộc phạm vi **toàn bộ snapshot hiện hành và toàn bộ tập bản thảo trong ledger của snapshot đó**. Việc người dùng chuyển đổi combobox chọn lô chỉ thay đổi góc nhìn xem trước của từng lô đơn lẻ (`_update_batch_preview`), HOÀN TOÀN KHÔNG làm thay đổi nội dung tổng quan toàn dự án.
   - Do đó, **thao tác đổi combobox chọn lô KHÔNG được phép hủy bỏ hay từ chối kết quả của Project Overview Worker**.
4. **Bảo toàn bất biến các thành phần khác:**
   - Tuyệt đối không ghi đè Dual Plan Slots: `self._video_plan_data`, `self._kinetic_plan_data` (`review_page.py:925–970`).
   - Không chạm transcript, không mutate `self._manifest`, không can thiệp controller hay DaVinci Resolve scripting API.

---

## 2. APP-OWNED INPUT & ADAPTER (TRÍCH XUẤT DỮ LIỆU BẤT BIẾN)

### 2.1. Nguồn dữ liệu hiện hành trong `ReviewPage`
1. `self._prepared_batch_snapshot` (`[CODE_VERIFIED]`): Lưu đối tượng `PreparedBatchSnapshot` (hoặc dict tương thích được chuẩn hóa qua `validate_and_normalize_snapshot`).
   - Chứa các kiểu DTO thực tế: `snapshot_id: str`, `manifest_id: str`, `project_name: str`, `batches: Tuple[PreparedBatchItem, ...]`, `unaccounted_clips: Tuple[UnaccountedClipEntry, ...]`, `distinct_planned_clips_count: int`, `baseline_sources: Tuple[BaselineSourceEntry, ...]`, `max_prompt_bytes: int`, `ui_context_signature: Any`.
2. `self._batch_drafts_ledger` (`[CODE_VERIFIED]`): Bảng băm lưu trữ các bản thảo phân lô hợp lệ trong RAM theo cặp khóa `(snapshot_id, batch_id) -> ValidatedBatchDraftEntry` (`review_page.py:3469`).
3. Ngữ cảnh UI hiện tại: `curr_ctx = self._get_current_context()` (`review_page.py:2654`), `curr_sig = self._extract_context_signature(curr_ctx)` (`review_page.py:2658`).

### 2.2. GUI Capture An Toàn Trên Main Thread
Mọi thao tác trích xuất và thẩm định tại Main Thread phải tuyệt đối an toàn, không IO đĩa, không deepcopy các cấu trúc lớn:
1. **Lọc nghiêm ngặt bản thảo theo Current Snapshot ID:**
   - Ledger trong RAM lưu trữ bản thảo của nhiều snapshot cũ nếu người dùng đã phân lô nhiều lần trong phiên.
   - Thuật toán trích xuất:
     ```python
     # [Adapter Logic cho Consumer kế tiếp]
     current_snap_id = norm_snap.snapshot_id
     current_ledger_entries: List[ValidatedBatchDraftEntry] = []
     for (s_id, b_id), entry in self._batch_drafts_ledger.items():
         if s_id == current_snap_id:
             current_ledger_entries.append(entry)
     ```
   - **Bảo đảm loại trừ:** Toàn bộ records thuộc snapshot cũ (`s_id != current_snap_id`) bị gạt bỏ hoàn toàn, tuyệt đối không lọt vào tập bản thảo đầu vào của tổng quan hiện tại.
2. **Chuyển đổi dữ liệu sang Pure Core Value Objects (`project_overview_contracts.py`):**
   - **Chuyển đổi Lô sang `BatchPartitionRef`:**
     Mỗi `b` trong `norm_snap.batches` được chuyển đổi thành `BatchPartitionRef`:
     ```python
     overview_batches = tuple(
         BatchPartitionRef(
             batch_id=b.batch_id,
             batch_index=b.batch_index,
             source_ranges=b.source_ranges,  # Tuple của BatchSourceRange bất biến
         )
         for b in norm_snap.batches
     )
     ```
   - **Chuyển đổi Clip không thể phân lô sang `OverviewUnaccountedClip`:**
     Dùng direct DTO accessor nghiêm ngặt, từ chối dữ liệu thiếu hoặc sai kiểu, không dùng fallback `getattr(...) or str(u.get(...))` làm che giấu lỗi:
     ```python
     overview_unaccounted = tuple(
         OverviewUnaccountedClip(
             clip_id=u.clip_id,
             reason=u.reason,
             message=u.message,
         )
         for u in norm_snap.unaccounted_clips
     )
     ```
   - **Tập bản thảo đã nhận (`received_drafts`):**
     Truyền danh sách `tuple(current_ledger_entries)` (mỗi mục đã là `ValidatedBatchDraftEntry` bất biến sau khi qua `verify_validated_entry_integrity` ở bước nạp draft).
   - **Khởi tạo DTO Ngữ cảnh Bất biến:**
     ```python
     context_input = ProjectOverviewContextInput(
         manifest_id=norm_snap.manifest_id,
         project_name=norm_snap.project_name,
         snapshot_id=norm_snap.snapshot_id,
         batches=overview_batches,
         received_drafts=tuple(current_ledger_entries),
         unaccounted_clips=overview_unaccounted,
         style_intent=curr_ctx.get("style_intent", self._style_intent),
         max_prompt_bytes=norm_snap.max_prompt_bytes,
     )
     ```
3. **Phân định rõ ranh giới sở hữu và kiểu dữ liệu (Zero QObject / Mutable Alias):**
   - DTO `ProjectOverviewContextInput` và các lớp con là `@dataclass(frozen=True)`, chỉ chứa các kiểu nguyên thủy (`str`, `int`, `float`, `tuple`).
   - Tuyệt đối cấm truyền tham chiếu `self` (`ReviewPage`), `QWidget`, `QObject`, hay `Resolve Automation` proxy vào request hoặc worker.

---

## 3. THẨM QUYỀN ĐỘ TƯƠI VÀ BẢO TOÀN DANH TÍNH (AUTHORITY / FRESHNESS)

### 3.1. Sự khác biệt giữa Semantic Ledger Signature, Full Prompt Hash và App-Owned Artifact
- **Semantic Ledger Signature (`ledger_signature`):** Đại diện cho chữ ký ngữ nghĩa của tập bản thảo hiện hành trong snapshot tại thời điểm dispatch request.
  Được tính toán qua helper chuẩn hóa nghiêm ngặt (G06-06A4c2a-R1):
  `compute_project_overview_ledger_signature(snapshot_id: str, entries: Sequence[ValidatedBatchDraftEntry]) -> str`
  - Thẩm định 100% field types TRƯỚC mọi thao tác hash/membership/sort/to_dict/float.
  - Bọc toàn bộ try/except chuyển `TypeError`, `OverflowError`, `AttributeError` thành `ValueError` fail-closed (ngăn chặn rò rỉ `batch_id=[] -> TypeError`, `batch_id=None -> hash`, `segments=None -> TypeError`, `total_selected_duration_sec=10**400 -> OverflowError`, bool-as-int counters/duration).
  - Bỏ trường audit-only `received_at`, chỉ băm các trường ngữ nghĩa (`manifest_id`, `batch_id`, `snapshot_id`, `binding_mode`, `batch_strategy_summary`, `segments`, `source_ranges`, `raw_payload_sha256`, `draft_digest`, `total_cuts_count`, `total_selected_duration_sec`).
  - Sắp xếp tăng dần theo `batch_id` để đảm bảo tính quyết định luận (deterministic).
  - Nghiêm ngặt từ chối bản thảo có snapshot_id lạ, trùng lặp batch_id, hoặc trường không chuẩn.
- **Owned Immutable Context Proof (`_freeze_context_signature`):**
  - Khắc phục triệt để nguy cơ caller mutate context signature sau khi capture: helper `_freeze_context_signature` đệ quy deep-freeze mọi sequence/mapping thành primitive immutable structures (`tuple`, `str`, `int`, `float`, `bool`).
  - Từ chối `None`, chuỗi rỗng/chỉ chứa khoảng trắng, cấu trúc rỗng, non-finite float, lone surrogates, hoặc các kiểu đối tượng phức tạp / UI / Resolve.
  - Cả `req.expected_ui_context_signature` và `artifact.context_signature` đều sở hữu bản sao immutable độc lập, không bị biến đổi khi caller append/modify đối tượng gốc.
- **Cổng Ràng Buộc Bắt Buộc Trước Source IO (Required Bindings Gate):**
  - `request_id`: bắt buộc là chuỗi văn bản không rỗng hợp lệ (nonblank strict str), không phải bool.
  - `generation`: bắt buộc là strict int >= 0 (không phải bool).
  - `expected_ui_context_signature`: bắt buộc phải hiện diện (non-None, non-empty), freeze và đối soát 100% khớp với `snapshot.ui_context_signature`.
  - **Zero Fallback:** Tuyệt đối không fallback sang `evidence_context_signature` khi snapshot hoặc request thiếu UI signature.
  - `style_intent`: đối soát chính xác `req.style_intent == context.style_intent` (cho phép cả hai cùng `""`, không dùng truthiness `if req.style_intent` để bypass).
  - **Budget Ba Chiều:** Bắt buộc `snapshot.max_prompt_bytes == req.max_prompt_bytes == context.max_prompt_bytes`, strict int trong khoảng 1..32,768.
- **App-Owned Artifact (`PreparedProjectOverviewArtifact`):**
  - Worker sở hữu một `@dataclass(frozen=True)` chứa toàn bộ các thuộc tính kết xuất:
    `request_id`, `generation`, `intent`, `snapshot_id`, `manifest_id`, `project_name`, `ledger_signature`, `formatter_policy`, `max_prompt_bytes`, `context_signature`, `overview: ProjectOverviewRenderResult`.
  - Artifact chỉ được gán sau khi toàn bộ quy trình (normalize snapshot, required bindings, đối soát context, pre-stat sources, format, post-stat sources) hoàn thành mỹ mãn.
  - Được truy xuất qua phương thức `worker.get_project_overview_artifact()`. Trả về `None` nếu worker bị hủy (`cancel()`) hoặc gặp lỗi.
- **Xác thực kết quả độc lập (`validate_project_overview_result_against_artifact`):**
  - Result dict trả về qua Qt signal chỉ đóng vai trò là một transport message sao chép rời.
  - Main Thread không được tự lấy hash từ result dict làm expected hash, mà bắt buộc phải đối soát qua:
    `validate_project_overview_result_against_artifact(result: Dict[str, Any], artifact: PreparedProjectOverviewArtifact) -> bool`
  - Bắt buộc trường `max_prompt_bytes` phải hiện diện trong result, kiểu strict int (không phải bool), và khớp `artifact.max_prompt_bytes`.
  - Hàm này tính lại actual UTF-8 bytes và actual SHA-256 của `rendered_prompt` trong `result` và so khớp từng trường envelope với `artifact.overview`. Kẻ tấn công sửa nội dung prompt và tự cập nhật hash trong result dict vẫn sẽ bị từ chối fail-closed vì lệch với owned artifact của worker.

### 3.2. Bảng Trace Biến Động và Cổng Kiểm Soát (Trace Matrix)

| # | Tình huống biến động | Cách phát hiện tại Main Thread | Cổng kiểm soát (Gate) | Hành vi Fail-Closed / Bảo toàn |
|---|---|---|---|---|
| 1 | **Draft A bị sửa / ghi đè** (Digest thay đổi cùng snapshot) | `curr_ledger_sig != pending_expected["expected_ledger_signature"]` | Ledger Freshness Gate | Bỏ qua kết quả cũ; 0 copy; giữ nguyên clipboard hiện tại; báo lỗi stale trên label. |
| 2 | **Thêm bản thảo mới** cho lô khác trong lúc worker chạy | `curr_ledger_sig != pending_expected["expected_ledger_signature"]` | Ledger Freshness Gate | Bỏ qua kết quả cũ; 0 copy; không chép bản tổng quan thiếu lô mới nạp. |
| 3 | **Bản thảo bị xóa** khỏi ledger trong lúc worker chạy | `curr_ledger_sig != pending_expected["expected_ledger_signature"]` | Ledger Freshness Gate | Bỏ qua kết quả cũ; 0 copy. |
| 4 | **Snapshot bị hủy / Phân lô lại** (`_invalidate_prepared_batches`) | `curr_snap is None` hoặc `curr_snap.snapshot_id != pending_expected["snapshot_id"]` | Snapshot Freshness Gate | Bỏ qua kết quả cũ; 0 copy; yêu cầu người dùng phân lô lại. |
| 5 | **Cấu hình UI thay đổi** (Project name, Style, Video paths) | `curr_sig != pending_expected["context_signature"]` | Context Signature Gate | Bỏ qua kết quả cũ; 0 copy; vô hiệu hóa snapshot cũ. |
| 6 | **Worker A kết thúc sau Worker B** (Superseded request) | `req_id != self._active_prompt_request_id` hoặc `res_gen != self._prompt_generation` | Generation & Active Request Gate | Bỏ qua toàn bộ signal của Worker A; không đè dữ liệu của Worker B. |
| 7 | **Same-envelope Prompt bị sửa kèm tự sửa hash** | `validate_project_overview_result_against_artifact(res, artifact) is False` | App-Owned Artifact Gate | Từ chối copy; ghi log warning; giữ nguyên clipboard MIME. |
| 8 | **Combobox chọn lô thay đổi** (`cbo_batch_selector`) | `cbo_batch_selector.currentIndex() != res_idx` | **BỎ QUA GATE NÀY** (Overview áp dụng toàn project, không phụ thuộc selected batch) | Cho phép tiếp tục thẩm định và copy bình thường. |
| 9 | **Thiếu UI signature hoặc snapshot mất UI proof** | `req.expected_ui_context_signature is None` hoặc `snapshot.ui_context_signature is None` | Required Bindings Gate | Từ chối fail-closed (`invalid_snapshot`); 0 source stat; 0 artifact. |
| 10| **Budget ba chiều không khớp** | `snapshot.max_prompt_bytes != req != ctx` | Three-Way Budget Gate | Từ chối fail-closed (`invalid_snapshot`); 0 source stat; 0 artifact. |
| 11| **Style Intent mismatch** | `req.style_intent != ctx_input.style_intent` | Style Intent Gate | Từ chối fail-closed (`invalid_snapshot`); 0 source stat; 0 artifact. |

---

## 4. CHÍNH SÁCH KIỂM TRA TỆP NGUỒN (SOURCE IO POLICY)

### 4.1. Thực tế tạo lập `baseline_sources` trong hệ thống
Hàm `prepare_batches` (`prompt_preparation_worker.py:1685–1697`) xây dựng `baseline_sources` từ tất cả các đường dẫn video có stat evidence hợp lệ trên đĩa, không chỉ thu hẹp trong danh sách `planned_clip_ids`. Do đó:
- `baseline_sources` và `unaccounted_clips` **không phải là hai tập hợp loại trừ lẫn nhau**.
- Một clip có thể có file tồn tại nhưng vẫn bị liệt kê vào `unaccounted_clips` do thời lượng không xác định hoặc lỗi phân tích.

### 4.2. Chính sách nguồn nghiêm ngặt cho Project Overview (`[CODE_VERIFIED]`)
1. **Đối với toàn bộ `baseline_sources` có trong snapshot:**
   - Worker thực hiện **Pre-check và Post-check** nghiêm ngặt trước và sau khi định dạng prompt.
   - Kiểm tra đầy đủ 7 tiêu chí: `os.path.exists`, `os.path.isfile`, `os.access(R_OK)`, `stat.S_ISREG`, `st.st_size`, `st.st_mtime_ns`, `st.st_ctime_ns`, `st.st_ino`, `st.st_dev`.
   - Danh sách đường dẫn đã chuẩn hóa trong request phải khớp thứ tự 100% với `snapshot.baseline_sources`.
   - Nếu tệp nguồn bị mất: phát terminal `("source_missing", {"clip_path": ...})`.
   - Nếu tệp nguồn bị thay đổi metadata hoặc kích thước: phát terminal `("source_changed", {"clip_path": ...})`.
   - Không bỏ qua kiểm tra một baseline source chỉ vì clip tương ứng chưa có bản thảo hoặc không phân được lô.
2. **Đối với `unaccounted_clips` (Clip không thể phân lô):**
   - Worker tuyệt đối **KHÔNG tự ý suy đoán path, không nạp manifest/cache, và KHÔNG gọi `os.stat` trên `unaccounted_clips`**.
   - Sự hiện diện của `unaccounted_clips` không làm chặn quy trình overview nếu các file trong `baseline_sources` vẫn toàn vẹn. Formatter thuần tự động đánh dấu `source_coverage_complete = False`.

---

## 5. VÒNG ĐỜI LUỒNG, TRẠNG THÁI BẬN VÀ QUYỀN CLIPBOARD (TERMINAL / BUSY)

### 5.1. Quản lý Vòng Đời và Aggregate Busy (`[CODE_VERIFIED]` Integration)
1. **Đăng ký Worker:**
   - Mỗi worker tạo ra được đăng ký vào `self._prompt_workers_registry[req_id] = worker` (`review_page.py:2935`).
   - Kết nối tín hiệu an toàn:
     - `worker.started_signal.connect(...)`
     - `worker.finished_signal.connect(self._on_prompt_preparation_finished)`
     - `worker.finished.connect(lambda: self._on_prompt_worker_thread_finished(req_id, worker))`
2. **Exactly-Once Terminal Emission:**
   - `PromptPreparationWorker` sử dụng cờ nội bộ `self._terminal_emitted = False`. Phương thức `_emit_terminal()` đảm bảo `finished_signal` chỉ được phát ra duy nhất một lần trong toàn bộ vòng đời QThread.
   - Main Thread sử dụng tập `self._consumed_prompt_request_ids` để bỏ qua mọi tín hiệu trùng lặp.
3. **Cancellation Lifecycle:**
   - Khi gọi `worker.cancel()`, worker đánh dấu cờ `_is_cancelled = True` và lập tức reset `_project_overview_artifact = None`.
   - Các điểm kiểm tra cancellation được đặt trước IO, giữa các lần stat baseline, sau khi format, và trước khi lưu artifact.
   - Getter `get_project_overview_artifact()` bảo đảm trả về `None` nếu worker bị cancel, ngay cả khi format đã chạy xong.
4. **Aggregate Physical Busy Management:**
   - Khi `self._prompt_workers_registry` còn worker: toàn bộ các nút hành động tiếp tục bị khóa (`setEnabled(False)`).
   - Khi toàn bộ QThread kết thúc vật lý: phục hồi trạng thái kích hoạt của các nút theo thẩm quyền dữ liệu hiện hành.
5. **Không nới lỏng an toàn luồng:**
   - Tuyệt đối cấm sử dụng `QThread.terminate()`. Chỉ sử dụng cooperative cancellation qua `worker.cancel()`.
   - Cấm lồng ghép `QEventLoop`, cấm retry timer vô hạn, cấm phát tán qua event bus toàn cục.

### 5.2. Quyền Clipboard (Preserved MIME Policy)
1. **Success Path (Thành công):**
   - Đúng một hành động click người dùng -> đúng một lần thực hiện ghi clipboard:
     ```python
     QApplication.clipboard().setText(prompt_text)
     ```
   - Nhãn nút đổi thành: `btn_copy_project_overview.setText("✓ Đã chép Tổng quan!")`.
   - Sau 2500ms, khôi phục nhãn gốc qua `QTimer.singleShot` nếu request_id và generation vẫn là active request.
2. **Failure / Cancel / Stale / Overflow Path (Thất bại / Hủy / Hết hạn / Tràn trần):**
   - Tuyệt đối **KHÔNG gọi `QApplication.clipboard().clear()`**, không ghi đè chuỗi rỗng hay thông báo lỗi vào clipboard của hệ điều hành.
   - Toàn bộ dữ liệu MIME hiện tại trong clipboard của người dùng được bảo toàn 100%.

---

## 6. LƯỢC ĐỒ YÊU CẦU, KẾT QUẢ VÀ BẢO VỆ PARSER (SCHEMA / RESULT / PREREQUISITES)

### 6.1. Trường Đã Triển Khai Trong `PromptPreparationRequest` (`[CODE_VERIFIED]`)
Trong `src/ui/director/workers/prompt_preparation_worker.py`:
```python
project_overview_context: Optional[Any] = None  # Instance của ProjectOverviewContextInput
expected_ledger_signature: Optional[str] = None  # Chuỗi 64-hex SHA-256 của tập bản thảo
```

### 6.2. Lược Đồ Kết Quả Trả Về (Result Envelope) (`[CODE_VERIFIED]`)
Khi hoàn thành `intent == "copy_project_overview"`, worker phát ra dictionary `result` trong `finished_signal`:

| Trường | Kiểu dữ liệu | Nguồn gốc / Thẩm quyền | Mục đích sử dụng |
|---|---|---|---|
| `request_id` | `str` (strict) | Request echo | Đối soát exactly-once và active request |
| `generation` | `int` (strict) | Request echo | Ngăn chặn race condition từ thế hệ cũ |
| `intent` | `str` (`"copy_project_overview"`) | Request echo | Nhận diện đúng nhánh xử lý tại slot |
| `snapshot_id` | `str` (strict) | Snapshot SSOT | Đối soát khớp snapshot hiện hành |
| `manifest_id` | `str` (strict) | Snapshot SSOT | Đối soát khớp manifest |
| `project_name` | `str` (strict) | Snapshot SSOT | Đối soát khớp tên dự án |
| `ledger_signature` | `str` (strict, 64-hex) | Worker verification | Đối chiếu chữ ký ledger ngữ nghĩa |
| `rendered_prompt` | `str` | Pure Formatter output | Chuỗi prompt kịch bản hoàn chỉnh |
| `rendered_bytes` | `int` | Pure Formatter output | Dung lượng UTF-8 thực tế của prompt |
| `rendered_sha256` | `str` (64-hex) | Pure Formatter output | Digest toàn vẹn nội dung prompt |
| `formatter_policy` | `str` (`"project_overview_v1"`) | Pure Formatter output | Nhận diện chính sách định dạng |
| `received_batches_count` | `int` | Pure Formatter output | Số lượng lô đã nạp bản thảo |
| `total_batches_expected` | `int` | Pure Formatter output | Tổng số lô theo kế hoạch |
| `all_batch_drafts_received`| `bool` | Pure Formatter output | Cờ hoàn tất toàn bộ bản thảo lô |
| `source_coverage_complete`| `bool` | Pure Formatter output | Cờ bao phủ 100% nguồn (không có unaccounted) |
| `missing_batch_ids` | `Tuple[str, ...]` | Pure Formatter output | Danh sách ID các lô còn thiếu |

### 6.3. Bounded Macro JSON Prerequisite Trước Khi Mở UI
- **Thực tế mã nguồn:** Hàm `parse_web_prompt_json(..., target_slot='slot1'/'slot2')` tiếp nhận JSON payload từ web/clipboard vào Slot 1/Slot 2.
- **Rủi ro:** Kịch bản Macro JSON tổng quan dự án chứa `kind: "project_overview_draft"` và không có `batch_id`. Bộ parser generic hiện tại nếu nhận JSON này sẽ parse object đầu tiên mà không kiểm định `kind`, dẫn đến nguy cơ ghi dữ liệu sai lệch vào `self._video_plan_data` hoặc `self._kinetic_plan_data`.
- **Ràng buộc bắt buộc (Binding Prerequisite):**
  - Trước khi mở nút bấm sao chép kịch bản tổng quan trên UI (`btn_copy_project_overview`), hệ thống bắt buộc phải có **bộ lọc chặn (bounded guard)** đối với `kind == "project_overview_draft"` ngay tại ranh giới parser và slot boundaries của ReviewPage.
  - Khi phát hiện macro overview dán vào slot, hệ thống phải giữ nguyên trạng thái last-good của Slots 1 & 2 và đưa ra thông báo rõ ràng: chưa hỗ trợ nhập kịch bản tổng quan vào timeline.
  - Bảo toàn tính tương thích ngược với các batch payload không có trường `kind`.
  - **Giới hạn phạm vi:** Không sửa đổi parser hay UI trong lát cắt `06A4c2a-I`. Prerequisite này được lập hồ sơ cho lát cắt UI tiếp theo (`06A4c2b-I`).

---

## 7. KẾT QUẢ KIỂM THỬ HỮU HẠN ĐÃ THỰC THI (VERIFIED SUITE)

Lát cắt backend worker đã được kiểm thử toàn diện với **31 ca test** độc lập tại `tests/ui/test_project_overview_worker.py`:

### Nhóm 1: Helper Chữ Ký Ledger Bất Biến & Nghiêm Ngặt (Group B)
1. `test_compute_ledger_signature_positive_and_determinism`: Chữ ký quyết định luận, độc lập thứ tự đầu vào.
2. `test_compute_ledger_signature_received_at_invariance`: Bỏ qua `received_at`, chỉ băm semantic content.
3. `test_compute_ledger_signature_semantic_changes_detected`: Thay đổi summary, duration, cuts làm đổi signature.
4. `test_compute_ledger_signature_add_delete_detected`: Thêm/xóa draft làm đổi signature.
5. `test_compute_ledger_signature_validation_rejections`: Từ chối draft có snapshot_id lạ, trùng batch_id, số nonfinite.
6. `test_compute_ledger_signature_batch_id_malformed_shapes_raise_value_error`: `batch_id` dạng list, None, rỗng, bool, dict đều ném `ValueError`.
7. `test_compute_ledger_signature_segments_malformed_shapes_raise_value_error`: `segments` dạng None, bool, hoặc chứa item lạ ném `ValueError`.
8. `test_compute_ledger_signature_duration_overflow_and_bool_raise_value_error`: `total_selected_duration_sec` tràn số `10**400`, bool, NaN, Inf ném `ValueError`.
9. `test_compute_ledger_signature_counts_bool_negative_raise_value_error`: `total_cuts_count` dạng bool hoặc số âm ném `ValueError`.
10. `test_compute_ledger_signature_raw_hash_and_digest_shapes_raise_value_error`: SHA không đủ 64-hex hoặc binding_mode lạ ném `ValueError`.

### Nhóm 2: Xác Thực Kết Quả Đối Soát Artifact Sở Hữu (Group C)
11. `test_validate_result_against_artifact_positive`: Xác thực 100% khớp giữa result và owned artifact.
12. `test_validate_result_against_artifact_tamper_detection`: Phát hiện prompt bị sửa dù kẻ tấn công tự sửa hash/bytes.
13. `test_validate_result_against_artifact_bool_and_envelope_rejections`: Từ chối bool-as-int, thiếu trường envelope, đặc biệt bắt buộc `max_prompt_bytes` strict int.

### Nhóm 3: Thẩm Định Request & Cổng Ràng Buộc Bắt Buộc (Group A)
14. `test_request_validation_for_copy_project_overview`: Thẩm định strict kiểu dữ liệu trong `PromptPreparationRequest` (request_id nonblank, generation int>=0, expected_ledger_signature 64-hex).
15. `test_overview_request_missing_expected_ui_signature_rejected`: `expected_ui_context_signature` bị thiếu (None/rỗng) -> fail-closed `invalid_snapshot`, 0 stat, 0 artifact.
16. `test_overview_snapshot_missing_ui_signature_rejected`: Snapshot mất `ui_context_signature` (None/rỗng) dù request có -> fail-closed, zero fallback sang evidence signature.
17. `test_overview_mismatched_ui_signature_rejected`: `expected_ui_context_signature` khác với `snapshot.ui_context_signature` -> fail-closed `invalid_snapshot`.
18. `test_overview_blank_request_id_rejected`: `request_id` rỗng hoặc bool -> bị từ chối fail-closed.
19. `test_overview_style_empty_vs_nonempty_rejected`: Style mismatch (`req.style_intent == ""` vs `"travel_vlog"`) -> fail-closed; cho phép khi cả hai cùng rỗng.
20. `test_overview_three_way_budget_mismatch_rejected`: Budget ba chiều mismatch (`snapshot.max_prompt_bytes != req != ctx`) -> fail-closed `invalid_snapshot`.

### Nhóm 4: Thực Thi Luồng QThread Thật, IO Nguồn & Vòng Đời (Group C Lifecycle)
21. `test_worker_overview_partial_drafts_real_qthread`: QThread thật cho partial drafts (2/3 lô), lưu artifact, validate khớp.
22. `test_worker_overview_full_drafts_with_unaccounted`: QThread thật cho full 3/3 drafts + clip unaccounted, không stat file unaccounted.
23. `test_worker_overview_zero_drafts_returns_no_data`: 0 draft trả về terminal `no_data`, artifact là `None`.
24. `test_worker_overview_budget_overflow_returns_budget_error`: Vượt trần 32KiB trả về `budget_error`, kèm `required_bytes`.
25. `test_worker_overview_context_geometry_mismatch_rejected`: Lệch geometry source_ranges bị từ chối fail-closed.
26. `test_worker_overview_source_pre_and_post_recheck`: Pre-check bắt file bị xóa (`source_missing`), post-check bắt file bị sửa (`source_changed`).
27. `test_worker_overview_cancel_lifecycle`: Hủy cooperative trước khi chạy phát terminal `cancelled`, artifact là `None`.
28. `test_overview_caller_mutable_ui_signature_deep_frozen_and_owned`: Caller mutate nested list/dict sau capture không làm đổi expected signature hoặc artifact (deep-frozen owned immutable tuple).
29. `test_worker_overview_post_render_mutation_triggers_source_changed`: Pre-check pass; formatter hook đổi file size trên đĩa trước return -> post-check bắt `source_changed`, exactly 1 terminal failure, artifact None (kèm spy xác nhận formatter đã chạy).
30. `test_worker_overview_cancel_inflight_during_render`: Hủy cooperative gọi trong lúc render prompt -> terminal `cancelled` sạch, getter None, physical finished.
31. `test_worker_overview_exception_in_render_handled_safely`: Exception nảy sinh trong formatter được bắt an toàn, phát đúng 1 terminal failure, không leak QThread, artifact là None.

**Kết quả Finite Suite (105/105 tests PASS in 2.35s, exit code 0):**
- `tests/ui/test_project_overview_worker.py`: 31/31 PASSED
- `tests/core/test_project_overview_contracts.py`: 37/37 PASSED
- `tests/core/test_batch_draft_contracts.py`: 17/17 PASSED
- `tests/ui/test_batch_draft_intake.py`: 20/20 PASSED
- Log kiểm thử đã được lưu đầy đủ tại: `.bridge/ux-evidence/G06-06A4c2a-R1/pytest_run.log`.

---

# BỔ SUNG KIẾN TRÚC GỘP G06-06A4c2b-I (OVERVIEW UI, SIGNATURE THẬT & MACRO SLOT GUARDS)

Sau khi Codex nghiệm thu `G06-06A4c2a-R1` (report SHA256: `0218204e4ea522ecfb783971003f78d3b4ab2a940cef65d60107131a97d685b2`), phần tích hợp tiêu thụ UI đã được hoàn tất và nghiệm thu toàn diện qua 4 phần gộp A → B → C → D.

## 1. Section A: Sửa Compatibility Signature Thật (`_freeze_context_signature`)
- **Vấn đề tồn dư từ R1:** Helper freeze chữ ký trước đây áp dụng quy tắc nonblank str cho mọi lá con, dẫn đến việc từ chối `vision_model=""` hợp lệ khi người dùng tắt tính năng mô tả hình ảnh (`enable_visual_describe=False`).
- **Giải pháp kiến trúc:**
  - Phân biệt rõ `is_root=True` (yêu cầu root proof: nonblank str hoặc nonempty tuple/list/dict có cấu trúc) với dữ liệu con lồng nhau (`is_root=False`).
  - Cho phép lá con là chuỗi rỗng `""` (cho `vision_model=""` khi inactive), tuple/list rỗng `()` (danh sách tệp rỗng), `None`, số nguyên/thực hữu hạn, bool.
  - Kiểm soát chu trình tham chiếu (cycle detection) qua tập `seen: Set[int]`, ném `ValueError` thay vì để xảy ra `RecursionError`.
  - Từ chối các dict có mixed key types (không dùng repr-sort ép kiểu mơ hồ thành hợp lệ).
  - Bảo toàn 100% tính bất biến (deep-frozen immutable tuple) đối với mọi mutation từ caller bên ngoài.

## 2. Section C: Macro Schema Slot Guards (Chặn Nạp Nhầm Vào Slots)
- **Cơ chế nhận diện:** Thêm helper `contains_project_overview_draft(data, max_depth=8, max_visits=64) -> bool` trong `director_state.py`.
  - Nhận diện các marker schema của macro overview: `kind == "project_overview_draft"`, `project_overview_version`, `macro_strategy_summary`, `batches_coverage`.
  - Duyệt an toàn với độ sâu tối đa 8 và tối đa 64 node, cho phép safe DAG alias, ngắt sớm và fail-closed khi gặp true cycle hoặc cạn budget.
  - Định nghĩa thông báo từ chối tiếng Việt thân thiện và chính xác: `PROJECT_OVERVIEW_REJECT_MSG = "Dữ liệu JSON là Kịch Bản Tổng Quan Dự Án (Macro Overview), chưa phải kịch bản phân đoạn V1/V2. Vui lòng không nạp vào ô phân đoạn."`
- **Các cổng bảo vệ 4 lớp:**
  1. *Parser Gate:* `parse_web_prompt_json` kiểm tra marker macro TRƯỚC mọi luồng `target_slot` ('slot1', 'slot2', 'auto', 'dual', 'fallback').
  2. *Direct Apply Gate:* `ReviewPage._apply_json_data` kiểm tra `contains_project_overview_draft(data)` ngay đầu hàm; cập nhật `lbl_json_hint` và `paste_zone.set_error`, từ chối mọi mutation vào Slot 1 và Slot 2.
  3. *Dropzone Gate:* `ReviewPage._on_dropzone_json_recognized` chặn macro JSON, hiển thị lỗi và giữ nguyên cấu hình.
  4. *Config Anti-Half-Restore Gate:* `ReviewPage.load_from_config` duyệt toàn bộ ứng viên plan (`web_prompt_pasted_data`, `video_plan_data`, `kinetic_plan_data`). Nếu có bất kỳ ứng viên nào là macro overview hoặc batch draft, giữ nguyên 100% last-good plans của cả 2 slots và bookkeeping, tuyệt đối không half-restore một plan khác.

## 3. Section B: Explicit Async Copy Toàn Project (Overview Consumer UI)
- **Thành phần giao diện:**
  - `btn_copy_project_overview`: Nút bấm với nhãn chuẩn `"📋 Sao Chép Tổng Quan Dự Án"`, mặc định disabled.
  - `lbl_overview_status`: Nhãn trạng thái hiển thị tiến độ và kết quả trung thực (`M/N lô`, số clip chưa phân lô).
  - Helper `_update_overview_button_state()`: Làm mới trạng thái nút cực nhanh (cheap GUI refresh), chỉ enabled khi: có snapshot hiện tại, ledger có >= 1 draft, không có close pending, và không có worker vật lý nào đang chạy trong `_prompt_workers_registry`. Tuyệt đối không thực hiện IO đĩa/cache/model/formatter trên UI thread.
- **Quy trình Dispatch:**
  - Người dùng bấm nút -> gọi `_on_copy_project_overview_clicked()`.
  - Thẩm định 3 chiều TRƯỚC cancel/increment/dispatch: context signature, snapshot_id, ledger entries hiện tại.
  - Tạo pending record sở hữu chặt chẽ: `request_id`, `generation`, `intent="copy_project_overview"`, `expected_ledger_signature`, `expected_ui_context_signature`, `expected_snapshot_id`, `expected_manifest_id`, `expected_project_name`, `worker_ref`.
- **Quy trình Terminal Success & Ownership:**
  - `_on_prompt_preparation_finished`: Kiểm tra `req_id == self._active_prompt_request_id` và khớp `_pending_prompt_expected`.
  - Recheck tính toàn vẹn 3 chiều: current context, current snapshot, tính lại `compute_project_overview_ledger_signature` trên toàn bộ draft hiện có trong ledger. Nếu phát hiện ledger bị thêm/xóa/sửa bản thảo (stale signature) hoặc snapshot bị hủy -> hủy bỏ sao chép (0 copy).
  - Việc chuyển đổi lựa chọn lô trên combobox đơn thuần KHÔNG làm stale overview prompt.
  - Lấy artifact qua `get_project_overview_artifact()` từ chính worker do pending record sở hữu (không chỉ tra cứu registry vật lý).
  - Xác thực nghiêm ngặt bằng `validate_project_overview_result_against_artifact(result, artifact)`.
  - Đúng 1 lần `clipboard.setText(artifact.prompt_text)`. Mọi trường hợp lỗi/hủy/lệch hash/stale -> 0 lần gọi setText/clear, bảo toàn 100% dữ liệu clipboard của người dùng.
  - Cập nhật nhãn trạng thái `lbl_overview_status` trung thực: `✓ Đã sao chép tổng quan dự án (M/N lô, ...)!`.

## 4. Section D: Component Desktop Verification Harness
- **Harness:** `task/desktop_verify_06a4c2b.py`.
- **Môi trường & An toàn:**
  - Phiên độc quyền `tempfile.mkdtemp(prefix="rf_06a4c2b_ui_")`.
  - Cô lập hoàn toàn `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` trong session root; bảo vệ tuyệt đối thư mục HOME của người dùng.
  - Tạo video fixture 1201s (64x64, 1fps) bằng ffmpeg; seed cache scan v3 với provenance `SEEDED_CACHE_COMPONENT_TEST`.
  - Sử dụng chữ ký sản xuất thật: `enable_visual_describe=False`, `vision_model=""`.
  - Chụp ảnh `WIDGET_RENDER` đầy đủ 6 ca kiểm thử.
  - Đo đếm số worker vật lý sau cleanup qua `isRunning()`. Xóa session root chỉ khi `still_running == 0`.
  - Khôi phục an toàn clipboard người dùng qua length-framed MIME digest tracking.
  - Normal run: exit code 0.
  - Controlled fault probe (`--probe-fault-injection`): exit code 2 sau khi worker đã start và được hủy/chờ cooperatively.

---

## 5. Kết Quả Kiểm Thử Toàn Diện (Finite Test Suite & Desktop Harness)

### A. Finite Pytest Suite (141/141 passed in 54.73s, exit code 0):
```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_project_overview_consumer.py tests/ui/test_project_overview_slot_guard.py tests/ui/test_project_overview_worker.py tests/ui/test_prompt_preparation_routing.py tests/ui/test_batch_draft_intake.py tests/ui/test_batch_json_slot_guard.py -q
```
- `tests/ui/test_project_overview_consumer.py`: **6/6 PASSED**
- `tests/ui/test_project_overview_slot_guard.py`: **11/11 PASSED**
- `tests/ui/test_project_overview_worker.py`: **35/35 PASSED**
- `tests/ui/test_prompt_preparation_routing.py`: **52/52 PASSED**
- `tests/ui/test_batch_draft_intake.py`: **20/20 PASSED**
- `tests/ui/test_batch_json_slot_guard.py`: **17/17 PASSED**

### B. Component Desktop Smoke Harness:
- Normal run: `DISPLAY=:1 .venv/bin/python3 task/desktop_verify_06a4c2b.py` -> **EXIT CODE 0**
- Controlled fault injection: `DISPLAY=:1 .venv/bin/python3 task/desktop_verify_06a4c2b.py --probe-fault-injection` -> **EXIT CODE 2**
- Toàn bộ bằng chứng screenshots, logs, và metrics được lưu tại: `.bridge/ux-evidence/G06-06A4c2b-I/`.

