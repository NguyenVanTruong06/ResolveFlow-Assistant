# G06-06C3-S2 — Kiến trúc Nối Ngữ cảnh Sáng tạo (CreativeContext) vào Prompt Kịch bản

**Trạng thái tài liệu:** S2a_IMPLEMENTED_AWAITING_REVIEW (UI Coordinator & S2b Deferred)  
**Mã nhiệm vụ:** `G06-06C3-S1-FINAL-THEN-S2a-I`  
**Tác giả:** Antigravity (Pair Programming cùng Tech Lead Codex)  
**Ngày cập nhật:** 2026-10-30  
**Phạm vi:** Backend S2a Consumer Core đã hoàn thành (StoryCopilot + PromptPreparationWorker); UI Coordinator (S2b) và Live Parity tiếp tục hoãn lại theo đúng ranh giới phân tách. Không tuyên bố các giá trị nhập trên StylePage hiện tại đã được AI sử dụng.

---

## 1. Đường Dẫn Dữ Liệu Chuẩn Tắc (Canonical Data Path)

### 1.1. Hiện trạng đã kiểm chứng (CODE_VERIFIED)

1. **`src/ui/director/director_state.py:1082-1110` (`DirectorConfig.get_creative_context` & `set_creative_context`)**:
   - `config.creative_context_dict` lưu trữ canonical dictionary (JSON primitives).
   - `get_creative_context()` trả về một `CreativeContext` bất biến thông qua `normalize_creative_context(self.creative_context_dict)`. Xử lý an toàn trường hợp legacy `hasattr` thiếu thuộc tính, `None`, hoặc `{}` bằng cách trả về canonical empty context.
   - `set_creative_context(val)` kiểm tra mutation isolation, reject non-mapping/None mà không làm biến dạng cấu hình hợp lệ trước đó.

2. **`src/ui/director/pages/style_page.py:91-1050` (`StylePage` & `CreativeContextPanel`)**:
   - `StylePage.save_to_config(config)` thu thập bản thảo UI qua `self.adv_panel.save_to_config(config)`.
   - `save_to_config` trả về `bool` (`save_ok`): `True` nếu dữ liệu hợp lệ và đã lưu thành công; `False` nếu có lỗi thẩm định (chuỗi rỗng bị cấm, note chứa NUL, vượt ngân sách bytes 32KiB, vượt quá 64 items, trùng ID). Khi `False`, giữ nguyên UI draft và không làm bẩn `config`.

3. **`src/ui/director/director_window.py:937-942` (`DirectorWindow._sync_all_pages_to_config`)**:
   ```python
   def _sync_all_pages_to_config(self):
       self.page_source.save_to_config(self.config)
       self.page_style.save_to_config(self.config)
       self.adv_panel.save_to_config(self.config)
       self.page_review.save_to_config(self.config)
       self.page_build.save_to_config(self.config)
   ```
   *Hiện trạng:* Bỏ qua giá trị trả về của `save_to_config`!

4. **`src/ui/director/director_window.py:785-873` (`DirectorWindow._get_current_director_context`)**:
   - Đọc các trường kỹ thuật từ `page_source` (`video_paths`, `whisper_model`, `vision_endpoint`...).
   - Đọc `style_preset = getattr(self.config, "style_preset", "travel_vlog")`.
   - *Hiện trạng:* Hoàn toàn **chưa đọc** `config.get_creative_context()`, chưa có key `"creative_context"` trong `raw_ctx`.

5. **`src/ui/director/pages/review_page.py:3055-3240` (`ReviewPage._sanitize_and_validate_context`)**:
   - Thẩm định whitelist các trường kỹ thuật (`video_paths`, `max_prompt_bytes`...).
   - *Hiện trạng:* Chưa thẩm định trường `"creative_context"` (sẽ triển khai tại lát cắt S2b).

6. **`src/ui/director/workers/prompt_preparation_worker.py:1058-1153` (`PromptPreparationRequest` & `PromptPreparationWorker`)**:
   - **CODE_VERIFIED (S2a Implemented):**
     - Bổ sung trường `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None` tại cuối danh sách trường.
     - `__post_init__` thẩm định và lưu trữ `CreativeContext` bất biến do worker sở hữu; từ chối context không rỗng đối với các intent chưa hỗ trợ (`prepare_batches`, `copy_batch`, `validate_batch_draft`, `copy_project_overview`) với lỗi `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT`.
     - `PromptPreparationWorker` bắt giữ snapshot `_captured_raw_creative_context` ngay khi khởi tạo; thẩm định lại trước I/O; truyền snapshot vào `StoryCopilot.generate_copilot_prompt`; và đính kèm `creative_context_signature` vào `result_payload` khi thành công.

7. **`src/core/story_copilot.py:340-396` (`StoryCopilot.generate_copilot_prompt`)**:
   - **CODE_VERIFIED (S2a Implemented):**
     - Bổ sung tham số keyword-only `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None`.
     - Thẩm định độc lập tại ranh giới; None/rỗng bảo toàn 100% từng byte legacy.
     - Kết xuất khối Markdown chuẩn tắc `## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO (CREATIVE CONTEXT):` chứa canonical escaped JSON từ `creative_context_to_dict` (`ensure_ascii=False`, `allow_nan=False`, `indent=2`, `sort_keys=True`), bảo toàn trung thực kiểu số khoa học, không dùng `:g` gây mất độ chính xác.
     - Bao gồm khối creative context đầy đủ trong mọi candidate của bậc thang ngân sách và khung xương tối thiểu (fail-closed nếu vượt quá `max_prompt_bytes`).
     - Lưu ý: Tuyến `copy_batch` tái sử dụng snapshot lô có sẵn; tuyến chuẩn bị lô dùng `build_batch_prompt_wrapper`, không đi qua `generate_copilot_prompt`. Trường `reference_urls` là danh sách các chuỗi URL đơn thuần (không có trường ghi chú riêng cho từng URL).

---

### 1.2. Kiến trúc đề xuất (PROPOSED)

```
[ StylePage / CreativeContextPanel ] 
      │ (Người dùng nhập draft: brief, rules, refs)
      ▼
StylePage.save_to_config(config) ──(False: Draft lỗi / Overbudget)──> Báo lỗi inline, Chặn điều phối (Fail-Closed)
      │ (True: Hợp lệ)
      ▼
DirectorConfig.set_creative_context(canonical_dict)
      │
      ▼
DirectorWindow._get_current_director_context()
      │ Đọc: ctx["creative_context"] = config.get_creative_context()
      ▼
ReviewPage._sanitize_and_validate_context(ctx)
      │ Thẩm định: normalize_creative_context(ctx["creative_context"])
      ▼
ReviewPage.request_prompt_preparation(intent, context)
      │ Khởi tạo: PromptPreparationRequest(..., creative_context=normalized_context)
      ▼
PromptPreparationWorker.run() [QThread Nền]
      │
      ▼
StoryCopilot.generate_copilot_prompt(..., creative_context=req.creative_context)
      │
      ▼
Bounded Prompt Text (Đầy đủ Markdown + Khối Creative Context + JSON Schema)
```

#### Quy tắc phân định thẩm quyền (Ownership & Gate 0):
- **Chủ sở hữu duy nhất của cấu hình hợp lệ:** `DirectorConfig` (thông qua `get_creative_context()` / `set_creative_context()`).
- **Chủ sở hữu của bản thảo đang sửa:** `StylePage` (`_current_draft` trong `CreativeContextPanel`).
- **Chặn đứng tại Gate 0 (Không dùng thầm lặng cấu hình cũ):**
  - Tại `DirectorWindow._sync_all_pages_to_config() -> bool`:
    ```python
    # PROPOSED: src/ui/director/director_window.py:hàm:_sync_all_pages_to_config
    def _sync_all_pages_to_config(self) -> bool:
        self.page_source.save_to_config(self.config)
        style_ok = self.page_style.save_to_config(self.config)
        self.adv_panel.save_to_config(self.config)
        self.page_review.save_to_config(self.config)
        self.page_build.save_to_config(self.config)
        return style_ok
    ```
  - Tại `DirectorWindow._on_run_ai_script()`:
    Nếu `_sync_all_pages_to_config()` trả về `False`, ghi nhận log cảnh báo, hiển thị banner nhắc nhở người dùng sửa lỗi trên tab Style/Creative, và **ngắt ngay lập tức**, không gọi `request_prompt_preparation`. Tuyệt đối không dùng cấu hình hợp lệ cũ ("last-good config") khi người dùng đang có bản thảo nhập liệu bị lỗi.
- **Không đóng gói lén lút (No Alias/Free-text Packing):**
  - Tuyệt đối cấm ghép `free_brief` hay `rules` vào chuỗi `style_intent` (ví dụ: cấm format kiểu `"travel_vlog | brief: ..."`).
  - Trường `creative_context` phải là một trường độc lập cấp 1 có cấu trúc `CreativeContext` chuẩn tắc, giữ nguyên phân tách giữa phong cách dựng nền tảng (`style_intent`) và chỉ thị đạo diễn tùy biến (`CreativeContext`).

---

## 2. Chuẩn Hóa Theo Yêu Cầu, Chữ Ký Ngữ Cảnh, Hủy Bỏ Snapshot & Cổng Bận Vật Lý

### 2.1. Mã băm chữ ký chuẩn tắc (Context Signature)

- **CODE_VERIFIED:** `src/core/contracts/edit_plan_contracts.py:336-356` đã có sẵn hàm `compute_creative_context_signature(context: CreativeContext) -> str`.
  - Hàm này tuần tự hóa canonical dict, encode UTF-8 JSON (`sort_keys=True`, không khoảng trắng dư thừa), và tính SHA-256 (64 hex lowercase).
  - Hoàn toàn tất định (deterministic), không I/O, nhạy cảm với thứ tự của `must_keep_rules`, `must_not_rules`, `reference_items`, `reference_urls`.

- **PROPOSED:** Mở rộng `ReviewPage._extract_context_signature(ctx)` (`src/ui/director/pages/review_page.py:3312-3351`):
  ```python
  # PROPOSED: src/ui/director/pages/review_page.py:hàm:_extract_context_signature:dòng:3347
  raw_creative = ctx.get("creative_context")
  if isinstance(raw_creative, CreativeContext):
      creative_sig = compute_creative_context_signature(raw_creative)
  elif isinstance(raw_creative, dict):
      try:
          norm_c = normalize_creative_context(raw_creative)
          creative_sig = compute_creative_context_signature(norm_c)
      except Exception:
          creative_sig = "INVALID_CREATIVE_CONTEXT"
  else:
      creative_sig = "EMPTY_CREATIVE_CONTEXT"

  return (
      norm_paths, w_model, w_lang, v_enable, v_model, v_endpoint,
      window_sec, frames_per_window, max_windows, max_refinements,
      style, hook, target, proj, budget, manifest_id,
      creative_sig  # <--- Bổ sung thành phần thứ 17
  )
  ```

### 2.2. Vô hiệu hóa Snapshot & Cơ chế Hủy tác vụ cũ (Invalidation & Physical Busy Gates)

1. **Vô hiệu hóa Snapshot khi dữ liệu thay đổi:**
   - Trong `ReviewPage`: Thuộc tính `self._last_prepared_prompt_signature` lưu signature của lần chuẩn bị gần nhất.
   - Khi người dùng thay đổi bất kỳ nội dung nào trong `StylePage` (sửa brief, thêm rule, đổi thứ tự ref):
     `_extract_context_signature` sinh ra `creative_sig` khác biệt.
   - Khi `request_prompt_preparation` được gọi:
     `curr_sig != self._last_prepared_prompt_signature` lập tức đánh dấu cache prompt cũ là **stale** (vô giá trị), buộc worker phải tái sinh prompt từ ngữ cảnh mới.

2. **Hủy tác vụ Worker đang chạy (Cancellation on Re-trigger):**
   - Không sinh thêm hệ thống QThread/Generation/Approval mới. Tận dụng nguyên vẹn cơ chế sẵn có:
     - `ReviewPage._active_prompt_worker` quản lý worker hiện thời.
     - Khi có request mới: `self._active_prompt_worker.cancel()`, worker kiểm tra cờ `self._is_cancelled` định kỳ và dừng an toàn tại các checkpoint.
     - Callback slot đối chiếu `request_id == self._pending_request_id`. Nếu không khớp, gói kết quả bị hủy âm thầm (dropped silently).

3. **Cổng bận vật lý (Physical Busy Gate):**
   - Trong suốt thời gian worker đang xử lý trên background thread:
     - Nút `btn_copy_prompt` và `btn_run_ai` bị disable (`setEnabled(False)`).
     - Spinner/Progress bar hiển thị trạng thái chuẩn bị.
     - Ngăn ngừa tình trạng người dùng click liên tiếp gây duplicate dispatch.

---

## 3. Định Dạng Prompt Giới Hạn & Siêu Dữ Liệu Tham Khảo Trung Thực

### 3.1. Cấu trúc Khối Ngữ Cảnh Sáng Tạo trong Prompt

Khối ngữ cảnh sáng tạo được đặt ngay dưới phần thông tin dự án cơ bản và trước bảng danh mục clip nguồn, sử dụng định dạng JSON chuẩn tắc có ranh giới rõ ràng:

```markdown
## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO (CREATIVE CONTEXT):
Các chỉ thị dưới đây là yêu cầu biên tập từ người dùng. Đường dẫn tệp cục bộ và liên kết URL chỉ là siêu dữ liệu (metadata); hệ thống chưa xem, tải về hay phân tích nội dung tệp/liên kết thực tế; các ghi chú là do người dùng tự cung cấp, không phải quan sát thị giác từ mô hình AI. Mô hình không cam kết tuân thủ ngữ nghĩa tuyệt đối hay hiểu nội dung tệp ngoài siêu dữ liệu được cung cấp dưới đây.

```json
{
  "free_brief": "Video hành trình khám phá Tây Bắc...",
  "must_keep_rules": [
    "Bắt buộc giữ trọn vẹn lời chào mở đầu của nhân vật tại bản Tả Phìn."
  ],
  "must_not_rules": [
    "Không cắt giữa chừng tiếng cười của người dân bản địa."
  ],
  "reference_items": [
    {
      "local_path": "/assets/references/intro_template.mp4",
      "media_type": "video",
      "reference_id": "ref_intro_01",
      "role": "reference_only",
      "user_note": "Tone màu ấm áp giống cảnh hoàng hôn"
    }
  ],
  "reference_urls": [
    "https://youtube.com/watch?v=sample1"
  ],
  "style_preset": "travel_vlog",
  "target_duration_sec": 180.0
}
```
```

### 3.2. Tính Trung Thực Về Khả Năng Của Mô Hình (Honest Metadata Boundaries)

- **Cấm tuyên bố sai lệch:**
  - Tuyệt đối KHÔNG thông báo cho LLM rằng "hệ thống đã phân tích video tham khảo" hoặc "đã xem tệp hình ảnh tại đường dẫn X".
  - Ghi chú rõ ràng: `(Chỉ đọc ghi chú người dùng; tệp tham khảo chưa được nạp vào footage hay phân tích VLM)`.
  - Không cam kết mô hình tuân thủ 100% phong cách từ một đường dẫn tệp trên ổ cứng.
  - `reference_urls` là danh sách chuỗi URL đơn thuần (không có trường ghi chú riêng theo URL).

### 3.3. Quản Lý Ngân Sách UTF-8 Bytes Nghiêm Ngặt (Strict Byte Budget)

- **CODE_VERIFIED:**
  - `src/core/contracts/edit_plan_contracts.py:28` quy định `MAX_CREATIVE_CONTEXT_BYTES = 32768` (32KiB).
  - `src/core/story_copilot.py:362-364` kiểm tra `max_prompt_bytes`.
- **Cơ chế phân bổ ngân sách:**
  - Ngân sách `max_prompt_bytes` (mặc định 32KiB) dành cho toàn bộ Prompt: Base Header + Creative Context Block + Clips Table + Output Schema.
  - Khung xương (Base Header, Output Schema, Creative Context Block) có mức ưu tiên cao nhất:
    - Nếu kích thước của Khung xương + Creative Context vượt quá `max_prompt_bytes`, ném `ValueError` fail-closed ("không đủ chứa khung xương kịch bản tối thiểu").
    - **TUYỆT ĐỐI KHÔNG** âm thầm cắt bớt rule, brief hay reference note của người dùng để nhồi nhét.
    - Việc cắt tỉa chỉ được thực hiện trên danh sách transcript chi tiết của các clip nguồn (kèm thông báo lược bớt trung thực), bảo đảm nguyên vẹn 100% chỉ thị đạo diễn.
- **Bảo toàn số thực khoa học và số nguyên lớn:**
  - Định dạng JSON chuẩn tắc qua `json.dumps(..., ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)` bảo toàn 100% độ chính xác của số thực (kể cả số mũ `1e-07`, `1e20`) và số nguyên lớn, TUYỆT ĐỐI không dùng định dạng `:g` gây mất độ chính xác.

---

## 4. Truy Vết Ba Chế Độ Điều Phối (Web Copy, Local Ollama, Paid Cloud API)

```
                     [ StoryCopilot.generate_copilot_prompt ]
                                       │
                                       ▼
                         Canonical Prompt Text (UTF-8)
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
[ 1. Chế độ Sao chép Web ]   [ 2. Chế độ Local Ollama ]   [ 3. Chế độ Paid Cloud API ]
  (intent: copy_video)         (intent: run_ai [ollama])    (intent: run_ai [cloud])
         │                             │                             │
         ▼                             ▼                             ▼
QClipboard.setText()         HTTP POST /api/generate        Google GenAI SDK / OpenAI
  Dán vào ChatGPT/Claude       Local QThread Worker          Direct Tool/Message API
         │                             │                             │
         └─────────────────────────────┴─────────────────────────────┘
          Bảo toàn 100% ngữ cảnh chuẩn tắc - Không có độ lệch ngữ nghĩa (Zero Drift)
```

### 4.1. Điểm Chuẩn Bị Dùng Chung (Common Preparation Point)
- Cả 3 chế độ đều đi qua cùng một điểm chuẩn bị duy nhất:
  `ReviewPage.request_prompt_preparation(intent, context)` -> `PromptPreparationWorker` -> `StoryCopilot.generate_copilot_prompt`.
- Kết quả sinh ra là một chuỗi văn bản Prompt chuẩn tắc duy nhất, tuân thủ cùng một cấu trúc Markdown và cùng trần dung lượng byte.
- Lưu ý: Intent hỗ trợ trong S2a là `copy_video` và `run_ai`. Tuyến `copy_batch` tái sử dụng snapshot lô có sẵn; tuyến `prepare_batches` sử dụng `build_batch_prompt_wrapper`, không đi qua `generate_copilot_prompt`.

### 4.2. Điểm Điều Phối Chung và Tuyến Rẽ Nhánh (Dispatch & Divergence Trace)
1. **Chế độ 1: Sao chép Web (`intent="copy_video"`):**
   - `ReviewPage._on_prompt_worker_success` nhận prompt text, gọi `QGuiApplication.clipboard().setText(prompt_text)`.
   - Hiển thị Toast thông báo: "Đã sao chép kịch bản vào Clipboard! Hãy dán vào Claude hoặc ChatGPT".
2. **Chế độ 2: Local Ollama (`intent="run_ai"` với provider="ollama"):**
   - `ReviewPage._on_prompt_worker_success` phát tín hiệu `prompt_ready_for_run(req_id, prompt_text)`.
   - `DirectorWindow._on_prompt_ready_for_run` nhận tín hiệu, gọi `DirectorController.run_ai_script(prompt_text)`.
   - Controller gửi payload `{ "model": "...", "prompt": prompt_text, "stream": False }` tới endpoint Ollama cục bộ qua HTTP.
3. **Chế độ 3: Paid Cloud API (`intent="run_ai"` với provider="gemini"/"openai"):**
   - Nhận cùng `prompt_text` từ `DirectorWindow._on_prompt_ready_for_run`.
   - Gửi prompt qua Client SDK của Cloud Provider (chưa triển khai trong bước này; được đánh dấu là `FUTURE_ADAPTER`).
   - Gửi prompt qua Client SDK của Cloud Provider (chưa triển khai trong bước này; được đánh dấu là `FUTURE_ADAPTER`).

**Cam kết chống lệch ngữ nghĩa (Zero Semantic Drift):**
Tuyệt đối không có trường hợp chế độ Web nhận được đầy đủ `rules` còn chế độ Ollama hay Cloud API bị lược bỏ hoặc thay thế bằng tóm tắt. Cả 3 chế độ đều nhận cùng một văn bản kịch bản được sinh ra từ cùng một hàm chuẩn hóa.

---

## 5. Ranh Giới Tiêu Thụ Bị Trì Hoãn (Deferred Consumers & Boundaries)

Các thành phần sau đây được xác định rõ ràng là **trì hoãn (DEFERRED)**, không thuộc phạm vi của lát cắt S2a:

1. **Đấu nối Điều phối UI (UI Coordinator / Request Supplier / Full Signature / S2b):**
   - Đấu nối từ `StylePage` qua `DirectorConfig`, `DirectorWindow`, và `ReviewPage` vào `request_prompt_preparation`.
   - Chưa tích hợp `creative_context_signature` vào context signature tổng thể của `ReviewPage`.
   - **LƯU Ý TRUNG THỰC:** Tuyệt đối không tuyên bố các giá trị nhập trên StylePage hiện tại đã được AI sử dụng (No claim that current StylePage inputs are already used by AI).
   - Đấu nối UI tương lai (S2b) bắt buộc phải:
     - Tôn trọng cờ `save_to_config == False` (chặn điều phối tại Gate 0 khi draft lỗi).
     - Bao phủ hoặc từ chối có kiểm soát đối với các tuyến batch/overview (`prepare_batches`, `copy_batch`, `validate_batch_draft`, `copy_project_overview`), tuyệt đối không âm thầm thế chỗ `None`.
2. **Kiểm thử trực tiếp End-to-End với Web / Ollama / Cloud Live Parity:**
   - S2a chỉ thẩm định tính toàn vẹn của API consumer và parity offline giữa `copy_video` và `run_ai` trong `PromptPreparationWorker`.
   - Việc gọi thực tế tới Ollama daemon, Cloud API live, hoặc hệ thống clipboard desktop thực tế được hoãn lại.
3. **Lưu trữ xuống đĩa khi khởi động lại dự án (Disk Persistence / Autosave):**
   - Hiện tại `CreativeContext` chỉ tồn tại trong bộ nhớ RAM (`DirectorConfig.creative_context_dict`).
   - Việc serialize ra file `project.json` hoặc cơ chế autosave phục hồi khi mở lại app được trì hoãn sang nhiệm vụ persistence riêng biệt.
4. **Widget xem xét ngữ cảnh chung trên ReviewPage (Common Review Widget):**
   - `StylePage` hiện là nơi duy nhất chỉnh sửa và hiển thị giao diện nhập liệu sáng tạo.
   - Việc hiển thị một thẻ tóm tắt (Creative Summary Card) trên `ReviewPage` để người dùng duyệt nhanh trước khi bấm chạy AI được trì hoãn.
5. **Tiền kiểm và nạp Footage vào DaVinci Resolve (G07 Preflight & Media Pool Intake):**
   - Toàn bộ các tệp trong `reference_items` mang thuộc tính `role="reference_only"`.
   - Chúng **KHÔNG** được import vào DaVinci Resolve Media Pool, không được cắt ghép trên timeline, và không tham gia vào pipeline dựng video G07.
6. **Phân tích thị giác VLM trên tệp tham khảo (VLM Style Analysis):**
   - Không chạy mô hình thị giác (Vision LLM) để phân tích bảng màu, ánh sáng hay bố cục của các tệp tham khảo trong bước S2.

---

## 6. Ma Trận Kịch Bản Nghiệm Thu Giới Hạn (<= 10 Finite Acceptance Scenarios)

| Mã ca | Tên kịch bản | Đầu vào & Điều kiện kiểm thử | Kết quả mong đợi | Trạng thái S2a |
| :--- | :--- | :--- | :--- | :--- |
| **SCN-01** | Legacy / Empty Context | Cấu hình cũ không có key `creative_context_dict` hoặc truyền `{}` | Tạo `CreativeContext()` mặc định, Prompt không chứa khối Creative Context rỗng, không phát sinh ngoại lệ | **PASSED (tests/core & tests/ui)** |
| **SCN-02** | Vietnamese & Multiline Preservation | `free_brief` tiếng Việt có dấu, `must_keep_rules` chứa `\n` và dấu ngoặc kép, gạch chéo ngược | Prompt bảo toàn nguyên vẹn 100% từng byte UTF-8 của lời dặn và quy tắc nhiều dòng qua JSON canonical | **PASSED (tests/core)** |
| **SCN-03** | Order & ID Determinism | Rules và reference items theo thứ tự xác định | Signature và JSON block xuất ra đúng thứ tự, không bị xáo trộn do sort hay set | **PASSED (tests/core)** |
| **SCN-04** | Gate 0 Rejection on Invalid Draft | `StylePage` có ô nhập rule chứa ký tự cấm hoặc ghi chú chứa NUL (`save_to_config` trả về `False`) | Bấm Run AI / Copy bị chặn ngay tại Gate 0; hiển thị thông báo lỗi; không dùng thầm lặng context cũ | *Hoãn lại (S2b UI)* |
| **SCN-05** | Byte Budget Overflow Fail-Closed | Nhập khối creative context cực lớn khiến tổng prompt vượt `max_prompt_bytes` (32KiB) | Ném `ValueError` có kiểm soát, emit `budget_error`, không sinh prompt cụt; exact equality accepted | **PASSED (tests/core)** |
| **SCN-06** | Invalidation on Mid-flight Edit | Đang chạy worker chuẩn bị prompt, người dùng sửa brief trên `StylePage` | Context signature thay đổi, worker cũ bị cancel, kết quả cũ bị hủy âm thầm | *Hoãn lại (S2b UI)* |
| **SCN-07** | Tri-Mode Parity Trace | Kích hoạt lần lượt Web Copy (`copy_video`) và Run AI (`run_ai`) với cùng cấu hình | Cả 2 chế độ đều nhận được chuỗi `prompt_text` và `creative_context_signature` giống hệt nhau từng ký tự | **PASSED (tests/ui)** |
| **SCN-08** | Physical Busy Gate Concurrency | Người dùng click đúp hoặc spam nút Run AI liên tục | Nút bị vô hiệu hóa ngay từ click đầu tiên; chỉ có duy nhất 1 worker được khởi chạy | *Hoãn lại (S2b UI)* |
| **SCN-09** | Post-format Source Re-check | Tệp video nguồn bị xóa khỏi ổ đĩa ngay trong lúc worker đang format prompt | Checkpoint cuối cùng phát hiện file mất, huỷ bỏ emit terminal success, báo lỗi `source_missing` | **PASSED (tests/ui)** |
| **SCN-10** | Reference Isolation (Zero I/O) | Thêm URL và đường dẫn tệp local vào `reference_items` | Không có kết nối mạng kiểm tra URL; không có hàm đọc/stat nội dung tệp tham khảo; Resolve Media Pool không đổi | **PASSED (tests/core)** |

---

## 7. Phân Kỳ Lát Cắt Triển Khai (Implementation Slices & Status)

### 7.1. Phân Tích Sự Phụ Thuộc (Dependency Analysis)
Để hoàn thành việc nối từ người sản xuất (Producer: `StylePage` / `DirectorConfig`) tới người tiêu thụ (Consumer: `StoryCopilot` / `PromptPreparationWorker` / `DirectorWindow`), bắt buộc phải có sự tham gia của:
1. `src/core/story_copilot.py` (Định dạng prompt)
2. `src/ui/director/workers/prompt_preparation_worker.py` (Truyền tải request bất biến)
3. `src/ui/director/pages/review_page.py` (Thẩm định context và trích xuất signature)
4. `src/ui/director/director_window.py` (Đọc cấu hình và điều phối Gate 0)

Hệ thống được chia thành 2 lát cắt độc lập có ranh giới nghiêm ngặt:

---

### 7.2. Lát Cắt 1 (S2a: Backend Consumer Core) — ĐÃ HOÀN THÀNH (IMPLEMENTED)

- **Mục tiêu:** Xây dựng năng lực tiếp nhận và định dạng `CreativeContext` bên trong `StoryCopilot` và `PromptPreparationWorker` độc lập với giao diện Qt.
- **Tệp chỉnh sửa chính xác (Đúng 2 files):**
  1. `src/core/story_copilot.py`:
     - Bổ sung keyword-only `creative_context: Optional[Union[Mapping[str, Any], CreativeContext]] = None`.
     - Thẩm định độc lập tại ranh giới công khai; None/rỗng bảo toàn 100% từng byte legacy.
     - Khối Markdown `## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO` với canonical escaped JSON từ `creative_context_to_dict`, bảo toàn số thực và số nguyên lớn, cảnh báo trung thực metadata.
     - Đưa toàn vẹn khối creative block vào mọi candidate của bậc thang phân bổ ngân sách và khung xương tối thiểu; fail-closed khi vượt ngân sách bytes.
  2. `src/ui/director/workers/prompt_preparation_worker.py`:
     - Bổ sung trường `creative_context` tại cuối `PromptPreparationRequest`.
     - Thẩm định, chuẩn hóa và lưu trữ `CreativeContext` bất biến do worker sở hữu trong `__post_init__`; từ chối context không rỗng đối với các intent chưa hỗ trợ (`prepare_batches`, `copy_batch`, `validate_batch_draft`, `copy_project_overview`) với lỗi `ERR_CREATIVE_CONTEXT_UNSUPPORTED_INTENT`.
     - Worker bắt giữ snapshot `_captured_raw_creative_context` tại `__init__`, bảo vệ trước mọi đột biến caller sau construction/start.
     - Thẩm định lại snapshot trước mọi source/cache IO trong `run()`; truyền `creative_context=captured_cc` vào `StoryCopilot.generate_copilot_prompt`.
     - Đính kèm `creative_context_signature` (dẫn xuất từ owned value) vào `result_payload` khi thành công với context không rỗng.
- **Kiểm thử nghiệm thu:**
  - `tests/core/test_story_copilot_creative_binding.py`: 6 tests PASSED (0.35s).
  - `tests/ui/test_prompt_worker_creative_binding.py`: 9 tests PASSED.
  - Tổng suite Part B: 17 passed in 8.26s (exit 0).

---

### 7.3. Lát Cắt Tiếp Theo (S2b: UI Coordinator & Request Supplier Wiring) — TRÌ HOÃN (NEXT)
- **Mục tiêu:** Đấu nối `DirectorWindow` và `ReviewPage` với `CreativeContext` từ `StylePage`.
- **Tệp dự kiến:**
  1. `src/ui/director/director_window.py`: Cập nhật `_sync_all_pages_to_config` (Gate 0 fail-closed khi save_to_config trả về False) và `_get_current_director_context`.
  2. `src/ui/director/pages/review_page.py`: Cập nhật `_sanitize_and_validate_context` và `_extract_context_signature`.
- **Kiểm thử nghiệm thu:** Kiểm chứng SCN-04, SCN-06, SCN-08.
