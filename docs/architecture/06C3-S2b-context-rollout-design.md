# G06-06C3-S2b — Thiết Kế Kiến Trúc Hoàn Chỉnh: Triển Khai Ngữ Cảnh Sáng Tạo (UI Coordinator & Long-Video Batch Rollout)

**Mã tài liệu:** `docs/architecture/06C3-S2b-context-rollout-design.md`  
**Trạng thái thiết kế:** DESIGN_READY (Phục vụ Codex Review & Lựa chọn lát cắt triển khai tiếp theo)  
**Nhiệm vụ:** `G06-06C3-S2a-R1-THEN-S2b-D` (Part B)  
**Tác giả:** Antigravity (Pair Programming cùng Tech Lead Codex)  
**Ngày lập:** 2026-10-30  
**Ràng buộc thiết kế:** Thuần tài liệu thiết kế (DESIGN ONLY) — KHÔNG sửa code `src/` hay `tests/` trong phần B. Mọi dẫn chiếu mã nguồn đều dựa trên file:hàm:dòng thực tế đã kiểm chứng (CODE_VERIFIED).

---

## 1. Luồng Điều Phối Chuẩn Tắc (True Producer → Owned Request → Result → Dispatch Path)

### 1.1. Hiện trạng chuỗi cung ứng thực tế (CODE_VERIFIED)

1. **Producer bản thảo (`StylePage`):**
   - File: `src/ui/director/pages/style_page.py:1377`
   - Phương thức: `StylePage.save_to_config(config) -> bool`
   - Hàm thẩm định nội bộ: `CreativeContextPanel.save_to_config(config) -> bool`
   - Cơ chế bảo vệ: Trả về `False` nếu bản thảo UI chứa lỗi (chuỗi rỗng bị cấm, ký tự NUL, vượt ngân sách 32KiB, vượt 64 items tham chiếu, trùng `reference_id`). Khi trả về `False`, UI hiển thị lỗi tại chỗ và **tuyệt đối không ghi đè** vào `config`.

2. **Chủ sở hữu cấu hình (`DirectorConfig`):**
   - File: `src/ui/director/director_state.py:1082-1110`
   - Phương thức: `DirectorConfig.get_creative_context() -> CreativeContext` và `DirectorConfig.set_creative_context(val)`
   - Cơ chế bảo vệ: `get_creative_context()` trả về một `CreativeContext` bất biến chuẩn tắc thông qua `normalize_creative_context`. `set_creative_context` từ chối `None` hoặc non-mapping, bảo toàn tính bất biến và không làm bẩn cấu hình hợp lệ trước đó.

3. **Bộ đồng bộ UI toàn cục (`DirectorWindow._sync_all_pages_to_config`):**
   - File: `src/ui/director/director_window.py:937-943`
   - Hiện trạng:
     ```python
     def _sync_all_pages_to_config(self):
         self.page_source.save_to_config(self.config)
         self.page_style.save_to_config(self.config)
         self.adv_panel.save_to_config(self.config)
         self.page_review.save_to_config(self.config)
         self.page_build.save_to_config(self.config)
     ```
     *Lỗ hổng hiện tại:* Bỏ qua giá trị `bool` trả về của `self.page_style.save_to_config(self.config)`. Nếu `save_to_config` trả về `False`, hệ thống vẫn tiếp tục đọc cấu hình cũ từ `config` (Silent Fallback), vi phạm nguyên tắc Gate 0.

4. **Bộ thu thập ngữ cảnh (`DirectorWindow._get_current_director_context`):**
   - File: `src/ui/director/director_window.py:785-874`
   - Hiện trạng: Đọc các trường kỹ thuật từ `page_source` (`video_paths`, `whisper_model`, `vision_endpoint`...) và hằng số phong cách `style = getattr(self.config, "style", STYLE_VLOG)` (sử dụng các hằng số `STYLE_VLOG`, `STYLE_PODCAST`... thay vì phụ thuộc cứng vào literal "travel_vlog").
   - *Hiện trạng:* Hoàn toàn chưa nạp khóa `"creative_context"` từ `self.config.get_creative_context()`. UI wiring vẫn đang DEFERRED.

5. **Bộ lọc và thẩm định Allowlist (`ReviewPage._sanitize_and_validate_context`):**
   - File: `src/ui/director/pages/review_page.py:3055-3240`
   - Hiện trạng: Chỉ kiểm tra các trường nguyên thủy (`video_paths`, `max_prompt_bytes`, `whisper_model`...). Chưa có logic normalize và validate cho `"creative_context"`.

6. **Bộ phát tác vụ nền (`ReviewPage.request_prompt_preparation`):**
   - File: `src/ui/director/pages/review_page.py:3479-3930`
   - Quản lý vòng đời: Sinh `request_id`, tăng `generation`, đăng ký worker vào `_prompt_workers_registry`, ghi nhận `_pending_prompt_expected`.

7. **Worker xử lý nền (`PromptPreparationWorker`):**
   - File: `src/ui/director/workers/prompt_preparation_worker.py:1155-2892`
   - **CODE_VERIFIED (S2a Hoàn Tất):**
     - Bắt giữ bản sao sở hữu độc lập `_captured_creative_context` tại `__init__`, không chia sẻ tham chiếu với caller. Bẫy lỗi thành `_captured_creative_error`.
     - Tại ranh giới `run()`: phát `invalid_config` nếu có lỗi bắt giữ, re-validate trước khi I/O.
     - Truyền `captured_cc` vào `StoryCopilot.generate_copilot_prompt`, đính kèm `creative_context_signature` vào `result_payload`.

8. **Dispatcher điều phối cuối (`ReviewPage._on_prompt_worker_finished` & `DirectorWindow._on_prompt_ready_for_run`):**
   - File: `src/ui/director/pages/review_page.py:4470-4660` và `src/ui/director/director_window.py:895-928`
   - Cơ chế bảo vệ: Đối soát generation, đối soát `_pending_run_ai_request_id`, exactly-once qua `_dispatched_run_ai_request_ids`.

---

### 1.2. Kiến trúc luồng điều phối chuẩn tắc đề xuất

```
[ StylePage / CreativeContextPanel ] 
      │ (Người dùng chỉnh sửa bản thảo UI: brief, rules, refs)
      ▼
StylePage.save_to_config(config) ──(False: Draft lỗi / Overbudget)──> Dừng ngay tại Gate 0 (Báo lỗi inline, 0 dispatch, 0 generation delta)
      │ (True: Bản thảo hợp lệ)
      ▼
DirectorConfig.set_creative_context(canonical_dict)
      │
      ▼
DirectorWindow._get_current_director_context()
      │ Nạp: raw_ctx["creative_context"] = self.config.get_creative_context()
      ▼
ReviewPage._sanitize_and_validate_context(raw_ctx)
      │ Thẩm định allowlist & normalize: clean_ctx["creative_context"] = normalize_creative_context(raw_cc)
      ▼
ReviewPage.request_prompt_preparation(intent, clean_ctx)
      │ 1. Sinh request_id mới, tăng generation (+1)
      │ 2. Xác định 2 chữ ký độc lập (Two Independent Proof Domains):
      │    - expected_creative_context_signature = compute_creative_context_signature(clean_ctx["creative_context"])
      │    - expected_ui_context_signature = <chữ ký cấu hình toàn trang ReviewPage, tách biệt hoàn toàn>
      │ 3. Khởi tạo: req = PromptPreparationRequest(..., creative_context=clean_ctx["creative_context"])
      │ 4. Lưu _pending_prompt_expected: {
      │        "generation": gen,
      │        "expected_creative_context_signature": expected_creative_sig,
      │        "expected_ui_context_signature": expected_ui_sig,
      │        ...
      │    }
      ▼
PromptPreparationWorker [QThread Nền]
      │ __init__: Bắt giữ bản sao độc lập (owned normalized snapshot)
      │ run(): Re-validate tại ranh giới; kiểm tra intent support
      │ Formatter: StoryCopilot tạo prompt chứa khối canonical JSON
      │ Terminal: Emit finished_signal kèm creative_context_signature riêng trong payload
      ▼
ReviewPage._on_prompt_worker_finished()
      │ Đối soát Terminal Binding (Hai rào chắn độc lập):
      │ - req_id khớp registry & generation == expected.generation
      │ - payload["creative_context_signature"] == expected.expected_creative_context_signature
      │ - Đối soát UI config (nếu có snapshot: ui_context_signature được replace riêng, không băm trong snapshot_id)
      │ Nạp prompt vào bộ đệm UI: self._video_prompt = payload["prompt_text"]
      ▼
Dispatcher Terminal:
      ├─► [Sao chép Clipboard]: Ghi prompt_text vào QClipboard (copy_video / copy_batch)
      └─► [Chạy AI Trực tiếp]: Phát tín hiệu prompt_ready_for_run(req_id, prompt_text)
            │
            ▼
      DirectorWindow._on_prompt_ready_for_run(req_id, prompt_text)
            │ - Kiểm tra pending_id & exactly-once guard
            │ - Thẩm định self.page_review.get_video_prompt() == prompt_text
            │ - Tiêu thụ ID và dispatch tới controller.run_ai_script(prompt_text)
```

#### Quy tắc ràng buộc cốt lõi:
1. **Tuân thủ tuyệt đối `save_to_config == False`:** Khi `self.page_style.save_to_config(self.config)` trả về `False`, `DirectorWindow` phải hủy bỏ thao tác ngay lập tức. Không sinh `request_id`, không tăng `generation`, không kích hoạt worker, không fallback về cấu hình cũ âm thầm.
2. **Không đánh tráo ngữ cảnh (No Smuggling into style_intent):** Nghiêm cấm việc ghép nối chuỗi `free_brief` hay `must_keep_rules` vào chuỗi phân loại `style_intent`. `style_intent` giữ nguyên vai trò mã enum định dạng (ví dụ `"travel_vlog"`, `"cinematic_nostalgic"`), còn `creative_context` là một cấu trúc độc lập có chữ ký số riêng.
3. **Tái sử dụng cơ chế an toàn hiện có:** Tận dụng 100% hệ thống `_prompt_workers_registry`, `_pending_prompt_expected`, `_dispatched_run_ai_request_ids`, và kiểm tra thế hệ (`generation`), không dựng thêm hệ thống job hay queue song song.

---

## 2. Bản Đồ Ánh Xạ Toàn Bộ Các Intent Tạo Prompt (Mapping All Prompt Intents)

Hệ thống ResolveFlow-Assistant hiện có 7 intent tạo prompt được phân bổ trên các trang và worker khác nhau. Dưới đây là ma trận ánh xạ chi tiết:

| Intent | Context Producer | Format Consumer | Snapshot / Replay Binding | Trạng thái an toàn & Tiến độ (Status) |
| :--- | :--- | :--- | :--- | :--- |
| **`copy_video`** | `StylePage` → `DirectorConfig` → `ReviewPage` | `StoryCopilot.generate_copilot_prompt` | `creative_context_signature` đối soát trực tiếp giữa preflight và terminal. | **ĐÃ HỖ TRỢ BACKEND (S2a)**; UI rollout toàn cục đang DEFERRED (PROPOSED). |
| **`run_ai`** | `StylePage` → `DirectorConfig` → `ReviewPage` | `StoryCopilot.generate_copilot_prompt` | Cùng cấu trúc chữ ký với `copy_video`; bảo vệ thêm qua `_dispatched_run_ai_request_ids`. | **ĐÃ HỖ TRỢ BACKEND (S2a)**; UI rollout toàn cục đang DEFERRED (PROPOSED). |
| **`prepare_batches`** | `StylePage` → `DirectorConfig` → `ReviewPage` | `StoryCopilot.build_batch_prompt_wrapper` + `batch_planner.partition_manifest_into_batches` | `PreparedBatchSnapshot` nhúng trường riêng `creative_context_signature`; `compute_snapshot_id` băm domain độc lập. | **ĐÃ HỖ TRỢ BACKEND (S2b)**; UI rollout toàn cục đang DEFERRED (PROPOSED). |
| **`copy_batch`** | `ReviewPage` (chọn lô từ snapshot đã chuẩn bị) | Không format lại (Replay `PreparedBatchItem.rendered_prompt`) | Đối soát 6 trường expected bindings và so khớp `creative_context_signature` trước khi I/O. | **ĐÃ HỖ TRỢ BACKEND (S2b)**; Replay nguyên văn prompt đóng băng; mismatch context từ chối `invalid_snapshot`. |
| **`validate_batch_draft`**| Người dùng dán JSON AI trả về vào `ReviewPage` | `batch_draft_contracts.validate_batch_draft_payload` | Đối soát hình học phân vùng nguồn (`BatchSourceRange`) và `snapshot_id`. | **CHƯA HỖ TRỢ CHO NONEMPTY CONTEXT (BLOCKER)**. Tiếp tục từ chối fail-closed `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT`. Cần chính sách an toàn riêng trước UI rollout. |
| **`copy_project_overview`**| `ReviewPage` (tổng hợp các bản thảo lô đã tiếp nhận) | `PromptPreparationWorker` (nhánh `copy_project_overview`) | `PreparedProjectOverviewArtifact` nhúng `expected_ledger_signature` và `snapshot_id`. | **CHƯA HỖ TRỢ CHO NONEMPTY CONTEXT (BLOCKER)**. Tiếp tục từ chối fail-closed `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT`. Cần chính sách an toàn riêng trước UI rollout. |
| **`copy_kinetic` / `auto_plan` (V2 Kinetic)** | `AutoWindow` / `ReviewPage` (Track V2) | `KineticPreparationWorker` / `kinetic_engine.generate_kinetic_prompt` | Thuần túy đọc subtitle intervals và từ vựng Whisper từ `kinetic_dataset_mapper`. | Tách biệt hoàn toàn (Separate Subsystem); không nhận `creative_context` đạo diễn. Không giả mạo phân tích VLM hay phong cách thị giác. |

---

## 3. Trình Tự Triển Khai Tránh Thoái Lui Phân Lô Video Dài (Sequencing & Long-Video Batch Planning)

### 3.1. Nguy cơ thoái lui nghiêm trọng (Regression Risk Analysis)
- `StylePage` khi khởi tạo hoặc mở lên luôn lưu cấu hình mặc định: `style_preset="travel_vlog"` (thậm chí khi người dùng không nhập `free_brief`).
- Nếu nạp `creative_context` vào `_get_current_director_context()` tại UI **TRƯỚC KHI** backend batch planner hỗ trợ `creative_context`:
  - `clean_ctx["creative_context"]` sẽ mang giá trị `CreativeContext(style_preset="travel_vlog")`.
  - Đây là một context không rỗng (`is_nonempty_cc == True` vì khác `CreativeContext()`).
  - Khi người dùng nhấn nút **"📦 2. CHUẨN BỊ PHÂN LÔ TỰ ĐỘNG"** (`prepare_batches`), worker sẽ ném ngay ngoại lệ hoặc emit terminal lỗi:
    `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT: creative_context chỉ được hỗ trợ cho intent ('copy_video', 'run_ai')`
  - **Hệ quả:** Toàn bộ tính năng phân lô video dài (Long-Video Batch Planning) — một tính năng cốt lõi của G06 — sẽ bị tê liệt hoàn toàn đối với mọi người dùng mở `StylePage`!

### 3.2. Chiến lược trình tự an toàn (Safe Sequencing Strategy)

Nghiêm cấm định nghĩa lại context có nghĩa thành rỗng hoặc xóa bỏ `style_preset` để qua mặt rào chắn. Trình tự triển khai BẮT BUỘC phải thực hiện theo 2 giai đoạn:

```
[ GIAI ĐOẠN 1: Mở rộng Backend Batch Context (<= 2 module backend) — ĐÃ HOÀN TẤT TRONG S2b ]
    1. src/core/story_copilot.py: Thêm creative_context vào build_batch_prompt_wrapper
    2. src/ui/director/workers/prompt_preparation_worker.py: 
       - Cho phép intent 'prepare_batches' và 'copy_batch' nhận creative_context
       - Đưa creative JSON vào Batch Evidence Wrapper prefix trước phân lô
       - Thêm creative_context_signature vào PreparedBatchSnapshot và compute_snapshot_id
       - Đối soát creative_context_signature tại copy_batch trước source/cache IO
       - ui_context_signature giữ nguyên độc lập cho UI config (không bị băm trong snapshot_id)
    ==> Kết quả: prepare_batches và copy_batch xử lý mượt mà cả khi có creative_context.

[ GIAI ĐOẠN 2: Chính sách An toàn cho validate_batch_draft & copy_project_overview (Backend Pre-requisite) ]
    - Xây dựng chính sách an toàn rõ ràng cho việc xử lý hoặc từ chối creative context trong bản thảo đơn lô và tổng quan dự án.

[ GIAI ĐOẠN 3: Nối dây UI Producer & Coordinator (<= 2 module UI — ĐANG DEFERRED) ]
    1. src/ui/director/director_window.py: Thẩm định save_to_config == True và nạp context
    2. src/ui/director/pages/review_page.py: Sanitize context và đối soát terminal signature
    ==> Lưu ý: Toàn bộ giao diện UI chỉ được kích hoạt sau khi đã giải quyết rào chắn blocker của Giai đoạn 2!
```

---

## 4. Danh Tính Ngân Sách và Nội Dung Cho Batch Wrapper (Exact Budget & Content Identity)

### 4.1. Cấu trúc Wrapper phân lô chứa Ngữ cảnh Sáng tạo
Trong `StoryCopilot.build_batch_prompt_wrapper(project_name, story_intent, ..., creative_context=None)`:
- Khối `creative_context` được render chuẩn tắc theo cấu trúc canonical JSON tương tự như trong `generate_copilot_prompt`.
- Khối này được đặt trang trọng trong phần `prefix_lines`, ngay sau `## 📁 THÔNG TIN DỰ ÁN TOÀN CỤC`, trước khi mở khối `## 📦 GÓI DỮ LIỆU BẰNG CHỨNG PHÂN LÔ (BATCH EVIDENCE ENVELOPE)`.
- Các hướng dẫn bao quanh (framing guidelines) nhấn mạnh:
  * "Yêu cầu đạo diễn và tài liệu tham khảo áp dụng chung cho toàn bộ mạch phim."
  * "Đường dẫn tệp và URL là siêu dữ liệu, AI không tải về hay đọc media thực tế."
  * "Các phân đoạn trong gói bằng chứng này cần tuân thủ các quy tắc must_keep_rules và must_not_rules trong phạm vi thời lượng của lô."

### 4.2. Bảo toàn thuật toán hình học và trần ngân sách (Byte Authority)
- **Không thay đổi trần byte:** Ngân sách cho mỗi lô vẫn giữ nguyên tuyệt đối `max_prompt_bytes = 32768` (32KiB).
- **Ước tính kích thước (Size Estimates):** Kích thước của `prefix` sẽ tăng thêm số byte của khối creative context JSON. Bộ lập lịch `BatchBudgetConfig` trong `batch_planner.py` đã tính toán `prompt_prefix` và `prompt_suffix` vào ngân sách trừ lùi trước khi phân chia item clip, do đó thuật toán đóng gói partition của A1/A2 hoàn toàn tự động điều chỉnh số lượng clip trong mỗi lô mà **không cần viết lại thuật toán hình học/thời gian**.
- **Chính sách Chữ Ký Tách Biệt & Vô hiệu hóa Snapshot (Independent Signature Domains):**
  - **Tách biệt tuyệt đối 2 domain chữ ký:**
    * `ui_context_signature`: Là chữ ký toàn bộ cấu hình ReviewPage, được ReviewPage gán thông qua `dataclasses.replace` (khoảng dòng 4122) SAU KHI worker hoàn thành. Thuộc tính này NẰM NGOÀI phép tính `compute_snapshot_id`. Tuyệt đối KHÔNG băm hay tái sử dụng trường này cho ngữ cảnh sáng tạo vì sẽ làm hỏng toàn bộ cơ chế replacement hiện có.
    * `creative_context_signature`: Là trường mới tùy chọn ở CUỐI `PreparedBatchSnapshot`, nhận giá trị chuỗi 64-hex lowercase chuẩn tắc sinh từ `compute_creative_context_signature(captured_cc)`. Trường này được băm trực tiếp vào `compute_snapshot_id` với domain phân tách `creative_context_sig_v1:{sig}\n` khi khác `None`.
  - **Tương thích ngược 100%:** Khi `creative_context_signature=None` (legacy), `compute_snapshot_id` tạo ra fingerprint byte-for-byte y hệt phiên bản cũ cho cả hai chế độ geometry v2 và nongeometry. Hàm `to_dict()` chỉ phát sinh khóa này khi khác `None`.
  - **Phát hiện sửa đổi & Chống làm giả (Tamper Proof):** Mọi hành vi sửa đổi/xóa `creative_context_signature` mà giữ snapshot ID cũ đều bị `validate_and_normalize_snapshot` phát hiện và từ chối (`None`). Khi gọi `copy_batch`, chữ ký từ request so sánh trực tiếp với snapshot trước I/O; mọi sai lệch giữa brief/preset/rules dẫn tới từ chối `invalid_snapshot` ngay lập tức.

---

## 5. Ràng Buộc Điều Phối Thực Thi Trực Tiếp (Direct Run Binding: Web / Local / Cloud)

### 5.1. Chia sẻ thống nhất điểm ràng buộc kịch bản
- Cả ba chế độ chạy AI:
  1. **Web Clipboard Manual Copy:** Người dùng nhấn nút "Sao Chép Kịch Bản", paste vào giao diện Web LLM (ChatGPT, Claude, Gemini Web).
  2. **Local Ollama / VLLM Run:** Gọi endpoint cục bộ không qua mạng ngoài.
  3. **Cloud API Run:** Gọi Gemini / OpenAI API chính thức qua backend client.
- **Ràng buộc bất biến:** Cả 3 chế độ đều chia sẻ **CHÍNH XÁC CÙNG MỘT NỘI DUNG PROMPT** được sinh ra từ `StoryCopilot.generate_copilot_prompt` kèm khối canonical JSON của `creative_context`. Tuyệt đối không có chuyện Web nhận prompt một kiểu, Local/Cloud nhận prompt một kiểu khác.

### 5.2. Ranh giới mạng và siêu dữ liệu (Zero IO / Deferred Networking)
- Các tài liệu tham khảo trong `creative_context` (`reference_items` và `reference_urls`) tiếp tục duy trì nguyên tắc **Metadata-Only**:
  - Không thực hiện bất kỳ lệnh `open()`, đọc file nhị phân, đọc metadata hình ảnh của file tham chiếu.
  - Không thực hiện bất kỳ HTTP GET/POST để tải nội dung URL tham chiếu.
  - Không truyền file tham chiếu sang DaVinci Resolve API.
- Logic kết nối mạng thực tế của Provider (Ollama/Gemini/OpenAI) vẫn tiếp tục được hoãn lại (deferred) và kiểm thử qua mock trong các giai đoạn tiếp theo.
- Việc lưu trữ cấu hình xuống đĩa dự án (Project Disk Persistence `.rfproject`) và module Review/Approval tổng thể (G07) tiếp tục hoãn lại theo đúng ranh giới phân tách.

---

## 6. Ma Trận Thẩm Định Nghiệp Vụ (Behavior Acceptance Matrix — 12 Kịch Bản)

| ID | Tình huống kiểm thử | Dữ liệu đầu vào | Hành vi kỳ vọng tại Preflight & Worker | Hành vi kỳ vọng tại Dispatcher / Terminal |
| :--- | :--- | :--- | :--- | :--- |
| **B01** | Legacy Empty Context | `creative_context = None` hoặc `{}` | Không tạo khối Markdown Creative Context; `result_payload` không có khóa `creative_context_signature`. | Khớp 100% từng byte legacy; sao chép clipboard và run_ai bình thường. |
| **B02** | Default Preset duy nhất | Chỉ chọn `style_preset="cinematic_nostalgic"`, brief rỗng | Khối JSON chuẩn tắc chứa `style_preset` cùng các trường mặc định chuẩn tắc (`target_duration_sec: null`, `free_brief: ''`, `must_keep_rules: []`, `must_not_rules: []`, `reference_items: []`, `reference_urls: []`); `creative_context_signature` sinh từ đối tượng `CreativeContext` chuẩn hóa bao gồm các trường mặc định này. | Chữ ký hợp lệ; prompt chứa chỉ dẫn phong cách chuẩn xác. |
| **B03** | Toàn bộ Siêu Dữ Liệu | Đầy đủ brief, rules, refs cục bộ và URL, số thực khoa học | Render đầy đủ khối canonical JSON; kiểm tra roundtrip giải mã thành công 100%. | Chữ ký khớp chuẩn; clipboard nhận prompt đầy đủ thông tin chỉ đạo. |
| **B04** | Bản thảo UI lỗi (Invalid Draft) | `free_brief` chứa NUL hoặc note vượt 32KiB | `StylePage.save_to_config` trả về `False`; `DirectorWindow` chặn đứng tại Gate 0. | **0 dispatch**, không tăng `generation`, không kích hoạt worker, không dùng cấu hình cũ. |
| **B05** | Chỉnh sửa khi đang chạy (In-flight Edit) | Sửa `StylePage` khi worker đang chạy tác vụ phân tích | Worker hoàn thành mang generation cũ hoặc chữ ký cũ; `ReviewPage` đối soát thấy lệch thế hệ. | Bỏ qua kết quả cũ; không ghi đè vào `_video_prompt`; không phát dispatch nhầm. |
| **B06** | Lệch ngữ cảnh lô (Batch Mismatch) | Sửa brief sau khi đã phân lô; ấn `copy_batch` | `PreparedBatchSnapshot.creative_context_signature` không khớp chữ ký ngữ cảnh yêu cầu sao chép. | Chặn đứng trước source/cache IO với `invalid_snapshot`, không công bố prompt. |
| **B07** | Vượt trần ngân sách (Overbudget) | Brief quá dài khiến tổng prompt vượt 32768 bytes | `StoryCopilot` fail-closed; worker phát `budget_error`. | Không công bố prompt; hiển thị thông báo yêu cầu người dùng rút gọn brief. |
| **B08** | Tính nhất quán giữa Copy và Run | Cùng một cấu hình ngữ cảnh sáng tạo | `copy_video` và `run_ai` sinh ra prompt_text và chữ ký giống nhau từng byte. | Đảm bảo tính tương đương giữa trải nghiệm copy thủ công và chạy AI tự động. |
| **B09** | Rào chắn Exactly-Once | Nhấp đúp nhanh vào nút "Chạy AI Kịch Bản" | `_dispatched_run_ai_request_ids` tiêu thụ `request_id` ngay lần dispatch đầu tiên. | Lần gọi thứ 2 bị chặn bởi guard; bộ điều khiển chỉ nhận đúng 1 lệnh thực thi. |
| **B10** | Rào chắn Zero IO tham chiếu | Khai báo đường dẫn và URL giả lập trong `reference_items` | Tripwire kiểm soát các lệnh IO, mạng và Resolve API. | **0 subprocess**, **0 network requests**, **0 media reads** trên các file tham chiếu. |
| **B11** | An toàn phân hệ Tổng quan & Kinetic | Gọi `copy_project_overview` hoặc `copy_kinetic` | Nhận diện intent; từ chối hoặc chuyển hướng an toàn không làm hỏng dữ liệu. | Không bịa đặt phân tích VLM; không gây crash giao diện; thông báo rõ phạm vi. |
| **B12** | Đóng cửa sổ sạch (Clean Close) | Đóng `DirectorWindow` khi worker đang chuẩn bị prompt | Bắt `closeEvent`, kích hoạt cooperative cancel; đợi worker dừng vật lý trước khi thoát. | Không rò rỉ QThread; không sinh ngoại lệ Qt; không gọi callback sau khi destroy. |

---

## 7. Lựa Chọn Lát Cắt Triển Khai Tiếp Theo (Next Implementation Slice)

### 7.1. Đánh giá lựa chọn
Dựa trên phân tích nguy cơ thoái lui tại Mục 3:
- **Nếu chọn lát cắt UI Coordinator trước (`director_window.py` + `review_page.py`):** Sẽ ngay lập tức làm hỏng tính năng `prepare_batches` vì backend chưa hỗ trợ `creative_context`.
- **Nếu chọn lát cắt Backend Batch Support trước (`story_copilot.py` + `batch_planner.py` / `prompt_preparation_worker.py`):**
  - Mở rộng khả năng tiếp nhận `creative_context` cho `prepare_batches` và `copy_batch`.
  - Khung bao bằng chứng lô (Batch Evidence Envelope) được bổ sung khối ngữ cảnh sáng tạo.
  - Khi đó, việc nối dây UI ở bước sau sẽ hoàn toàn trơn tru và an toàn 100%.

### 7.2. Đề xuất lát cắt triển khai duy nhất: `G06-06C3-S2b-BACKEND-BATCH-SLICE`
- **Số lượng module:** Đúng **2 module backend**:
  1. `src/core/story_copilot.py`:
     - Cập nhật hàm `build_batch_prompt_wrapper` nhận thêm `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None`.
     - Kết xuất khối Markdown `creative_context` chuẩn tắc vào `prefix_lines`.
  2. `src/ui/director/workers/prompt_preparation_worker.py`:
     - Cho phép `creative_context` không rỗng đi qua `PromptPreparationRequest` cho intent `prepare_batches` và `copy_batch`.
     - Truyền `captured_cc` vào `StoryCopilot.build_batch_prompt_wrapper`.
     - Nạp `creative_context_signature` vào `PreparedBatchSnapshot` và `compute_snapshot_id` (giữ nguyên độc lập `ui_context_signature`).
     - Đối soát chữ ký tại `copy_batch` trước khi thực hiện source/cache IO.
     - Duy trì từ chối fail-closed `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT` đối với `validate_batch_draft` và `copy_project_overview`.
- **Không tạo thêm framework mới:** Sử dụng 100% hợp đồng dữ liệu hiện có (`CreativeContext`, `PreparedBatchSnapshot`, `BatchBudgetConfig`).
- **Phạm vi kiểm thử:** Bộ kiểm thử đơn vị ngoại tuyến cho batch prompt wrapper và batch planner snapshot binding.
- **Lát cắt sau đó (UI Wiring Slice):** Sau khi các rào chắn blocker của draft/overview được thiết kế chính sách an toàn, sẽ tiến hành nối dây `director_window.py` và `review_page.py` mà không sợ bất kỳ nguy cơ thoái lui nào. Không khẳng định rằng input từ StylePage đã chạm tới AI hay tất cả provider đã qua kiểm thử trực tiếp.

---

## 8. Hiện Trạng Triển Khai Giao Diện UI & Kết Quả Kiểm Chứng (G06-06C3-S2d-UI-ROLLOUT-I)

**Trạng thái:** `UI_IMPLEMENTED_AND_VERIFIED`  
**Nhiệm vụ:** `G06-06C3-S2d-UI-ROLLOUT-I`  
**Các module sản xuất đã sửa đổi (ĐÚNG 2 module):**
1. `src/ui/director/director_window.py`
2. `src/ui/director/pages/review_page.py`

### 8.1. Các cơ chế điều phối đã triển khai thực tế
1. **Cổng bảo vệ Fail-Closed tại `DirectorWindow`:**
   - `_sync_all_pages_to_config(self) -> bool` trả về `bool` tường minh; kiểm tra `page_style.save_to_config` đầu tiên. Nếu `False`, hủy bỏ ngay lập tức, không cho phép dùng cấu hình cũ hay tiếp tục chuyển bước/dựng timeline.
   - `_on_run_ai_script()` dừng ngay lập tức nếu sync hoặc ngữ cảnh sáng tạo không hợp lệ trước bất kỳ thao tác worker hay dispatch nào.
   - `_get_current_director_context()` thu thập bản thảo hiện tại qua `save_to_config`, chuẩn hóa và đính kèm `creative_context` sở hữu độc lập vào raw context. Nếu bản thảo lỗi, trả về `{}` (zero fallback).
   - Tái sử dụng tín hiệu `page_style.draft_changed` và `config_changed` với reentrancy guard (`_in_style_sync`) để thông báo `ReviewPage.notify_creative_context_changed()` tức thì.

2. **Thẩm định, Chữ ký và Quyền lực Terminal tại `ReviewPage`:**
   - Mở rộng bộ trích xuất chữ ký ngữ cảnh `_extract_context_signature`: giữ nguyên tuple 16 trường cơ sở cho legacy/empty; nối thêm phần tử thứ 17 `f"cc:{cc_sig}"` khi có ngữ cảnh sáng tạo không rỗng.
   - Khởi tạo `PromptPreparationRequest` với `creative_context` sở hữu độc lập cho toàn bộ 6 nguồn intent (`copy_video`, `run_ai`, `prepare_batches`, `copy_batch`, `validate_batch_draft`, `copy_project_overview`).
   - Độc lập lưu `expected_creative_context_signature` trong `_pending_prompt_expected` (bắt buộc 64 hex lowercase khi có ngữ cảnh; explicit `None` cho legacy).
   - Quyền lực terminal: đối soát 3 chiều giữa `pending_expected`, `current_context` và `result_payload`. Bất kỳ sự sai lệch nào đều bị từ chối trước khi ghi clipboard, cập nhật ledger, xuất bản manifest hay dispatch AI.
   - Cơ chế mất hiệu lực tức thì (Immediate Invalidation): khi sửa brief/rules/refs, getters trả về rỗng, nhãn cảnh báo trung thực `"⚠ Yêu cầu thay đổi, chuẩn bị lại."`, không xóa trắng bản thảo người dùng hay lịch sử ledger.

3. **Bằng chứng kiểm thử hữu hạn & Desktop Smoke:**
   - Bộ kiểm thử hữu hạn 224/224 ca kiểm thử đạt 100% (`tests/ui/test_creative_context_prompt_routing.py` + 5 test file liên quan).
   - Desktop smoke harness trên ứng dụng thật (`task/desktop_verify_06c3_creative_ui.py`): 4/4 ca kiểm thử đạt `COMPONENT_DESKTOP_VERIFIED`, probe fault-injection thoát mã 2 an toàn.

> [!NOTE]
> Giao diện xem xét / phê duyệt đề xuất chung (Common Proposal Review / Approval UI — S3) tiếp tục được **HOÃN LẠI (DEFERRED)** theo đúng ranh giới kiến trúc. Không có bất kỳ lệnh gọi mạng provider hay thay đổi timeline Resolve nào được thực thi.

---
*Tài liệu thiết kế G06-06C3-S2b đã hoàn tất và được cập nhật hiện trạng triển khai UI thực tế.*
