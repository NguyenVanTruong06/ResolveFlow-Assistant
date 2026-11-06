# Thiết kế Tiếp nhận Ngữ cảnh Sáng tạo (CreativeContext Intake) & Giao diện Duyệt Kịch bản Chung (Common Proposal Review)

**Tài liệu mã số:** `06C3-creative-intake-review-design.md`  
**Nhiệm vụ:** `G06-06C3-S1-R1-THEN-S2-D` (Đã hoàn thành sửa gộp ranh giới Part A và thiết lập thiết kế kết nối Prompt Part B tại `06C3-S2-prompt-context-binding-design.md`)  
**Tác giả:** Antigravity (Pair Programming cùng Tech Lead Codex)  
**Ngày lập:** 2026-10-30  
**Trạng thái:** `DESIGN_ALIGNED` (Đã sửa đổi toàn bộ ranh giới nhập liệu Probe 1-9; 17/17 UI tests PASS; thiết kế nối Prompt Part B DESIGN_READY)  
**Quyết định sản phẩm nền tảng:** Tuân thủ 100% các quyết định D01, D02, D03 tại `docs/architecture/06C-product-decisions.md`.

---

## 1. Callgraph Thực tế giữa Nhà sản xuất (Producer) và Bên tiêu thụ (Consumer)

Dưới đây là sơ đồ luồng dữ liệu và đồ thị gọi hàm đối chiếu chính xác từng `file:hàm:dòng` trong codebase hiện hành, phân định rành mạch giữa các thành phần đã được kiểm chứng bằng mã nguồn (`CODE_VERIFIED`), các thành phần đã hoàn thành trong lát cắt đầu tiên (`IMPLEMENTED_S1`), và các thành phần downstream tạm hoãn chờ consumer (`PROPOSED_DEFERRED`).

```mermaid
flowchart TD
    subgraph UI_Runtime ["Lớp Giao diện & Tiếp nhận Người dùng (UI Runtime)"]
        SW["StylePage (Step 2)<br/>src/ui/director/pages/style_page.py:220"]
        CIP["CreativeIntakePanel (D03 Intake)<br/>src/ui/director/pages/style_page.py:160"]
        RW["ReviewPage (Step 3)<br/>src/ui/director/pages/review_page.py:382"]
        DW["DirectorWindow (Coordinator)<br/>src/ui/director/director_window.py:36"]
    end

    subgraph State_Config ["Quản lý Cấu hình & Trạng thái Dự án"]
        DC["DirectorConfig (In-Memory SSOT)<br/>src/ui/director/director_state.py:58"]
        DCDict["creative_context_dict<br/>src/ui/director/director_state.py:82"]
    end

    subgraph Deferred_Consumers ["Các Bên Tiêu Thụ Đang Hoãn (PROPOSED_DEFERRED)"]
        PPW["PromptPreparationWorker<br/>src/ui/director/workers/prompt_preparation_worker.py:1132"]
        PPR["PromptPreparationRequest (creative_context)<br/>src/ui/director/workers/prompt_preparation_worker.py:1052"]
        CRW["CommonProposalReviewWidget (Step 3 Common Review)<br/>docs/architecture/06C3-creative-intake-review-design.md"]
        PRP["Project Durable Disk Persistence (Restart Restore)<br/>Deferred to persistence slice"]
    end

    subgraph Core_Contracts ["Hợp đồng Dữ liệu Lõi Bất biến (Core Contracts - FROZEN)"]
        CC["CreativeContext (Immutable)<br/>src/core/contracts/edit_plan_contracts.py:230"]
        EPP["EditPlanProposal (Immutable Snapshot)<br/>src/core/contracts/edit_plan_proposal_contracts.py:505"]
        PGA["assemble_plan_frame_geometry<br/>src/core/geometry/plan_geometry_assembler.py:63"]
        APR["AppOwnedApprovalRecord<br/>src/core/contracts/edit_plan_approval_contracts.py:145"]
    end

    CIP -->|get_draft / load_from_dict| SW
    SW -->|save_to_config (Transactional)| DC
    DC -->|get_creative_context / set_creative_context| DCDict
    DCDict <-->|normalize / to_dict| CC
    DW -->|_on_style_changed (Preserves draft)| SW

    %% Deferred downstream connections
    DC -.->|PROPOSED_DEFERRED| PPR
    PPR -.->|PROPOSED_DEFERRED| PPW
    CC -.->|PROPOSED_DEFERRED| EPP
    EPP -.->|PROPOSED_DEFERRED| CRW
    EPP -->|Thẩm định hình học| PGA
    EPP -->|create_app_owned_approval_record| APR
    DC -.->|PROPOSED_DEFERRED| PRP
```

### 1.1. Bảng đối chiếu Callgraph chi tiết

| Nhãn phân loại | Vị trí chính xác (`file:hàm:dòng`) | Thực thể / Chức năng | Vai trò và Trạng thái |
| :--- | :--- | :--- | :--- |
| `CODE_VERIFIED` | `src/ui/director/director_state.py:58` | `class DirectorConfig` | Dataclass lưu trữ cấu hình 4 bước của ChunDVC Director trong bộ nhớ runtime. |
| `IMPLEMENTED_S1` | `src/ui/director/director_state.py:82` | `DirectorConfig.creative_context_dict` | Canonical field duy nhất (`Dict[str, Any]` với `default_factory=dict`) lưu trữ dữ liệu JSON primitives của ngữ cảnh sáng tạo. |
| `IMPLEMENTED_S1` | `src/ui/director/director_state.py:175` | `get_creative_context() / set_creative_context()` | Chuyển đổi hai chiều thuần túy giữa primitive dict và `CreativeContext` bất biến; mutation-isolated và transactional. |
| `IMPLEMENTED_S1` | `src/ui/director/pages/style_page.py:160` | `class CreativeIntakePanel(QFrame)` | Khung tiếp nhận trực quan Bước 2: Free Brief, semantic rules, duration float/int, reference items (role=reference_only), URLs. |
| `IMPLEMENTED_S1` | `src/ui/director/pages/style_page.py:465` | `StylePage.load_from_config / save_to_config` | Tích hợp intake panel, bảo toàn bản thảo qua chu kỳ style callback, lưu transactional (trả về bool, hiện lỗi inline). |
| `CODE_VERIFIED` | `src/core/contracts/edit_plan_contracts.py:230` | `class CreativeContext` | Value Object bất biến (32KiB budget, 128 rules/kind, 64 refs, 64 URLs). |
| `CODE_VERIFIED` | `src/core/geometry/plan_geometry_assembler.py:63` | `assemble_plan_frame_geometry()` | Module thuần túy thẩm định hình học kịch bản (đã nghiệm thu tại `06C2d-gate-acceptance.md`). |
| `CODE_VERIFIED` | `src/core/contracts/edit_plan_approval_contracts.py:197` | `create_app_owned_approval_record()` | Hàm xuất xưởng `AppOwnedApprovalRecord`, tự động trích xuất toàn bộ omissions hiện có trong proposal. |
| `PROPOSED_DEFERRED` | `src/ui/director/workers/prompt_preparation_worker.py:1052` | `PromptPreparationRequest.creative_context` | Đóng gói `CreativeContext` truyền sang worker nền để sinh prompt. |
| `PROPOSED_DEFERRED` | `src/ui/director/pages/review_page.py` | `CommonProposalReviewWidget` | Giao diện duyệt kịch bản thống nhất D01/D02 tại Bước 3 (Cuts, Omissions, Checklist, Readiness). |
| `PROPOSED_DEFERRED` | Disk persistence across restarts | Project file save/reload | Lưu trữ lâu dài toàn bộ `DirectorConfig` xuống tệp dự án qua các lần restart (ReviewPage QSettings không lưu toàn bộ config). |

### 1.2. Giải quyết Xung đột và Nguồn Chân lý Duy nhất (SSOT)

1. **Một trường canonical duy nhất:**
   - Trường duy nhất được phép lưu trữ ngữ cảnh sáng tạo trong cấu hình là `DirectorConfig.creative_context_dict`. Không tạo thêm trường `creative_context` song song hay biến thể khác.
   - Giao diện `StylePage` và `CreativeIntakePanel` đóng vai trò là trình biên tập bản thảo (editor), trong khi `DirectorConfig` là trạng thái chấp thuận cuối cùng (accepted state). Bản thảo chưa lưu hoặc tạm thời lỗi có thể hiển thị trên giao diện mà không làm hỏng cấu hình tốt gần nhất.

2. **Không suy diễn thứ tự ưu tiên ngữ nghĩa (No Semantic Precedence):**
   - Core contract không chứa bộ giải quyết xung đột ngữ nghĩa tự động (`semantic resolver`) giữa `free_brief`, `style_preset`, và `rules`.
   - Cả ba trường đều đại diện cho ý đồ người dùng cung cấp và được lưu trữ nguyên vẹn dưới dạng metadata. Hệ thống không đưa ra tuyên bố tuân thủ phong cách 100% hay khẳng định giải quyết được mâu thuẫn ngữ nghĩa bằng schema.

3. **Phân định phạm vi lưu trữ (Persistence Boundary):**
   - `DirectorConfig` hiện tại là một dataclass trong bộ nhớ runtime.
   - Cơ chế `QSettings` tại `ReviewPage` chỉ phục vụ lưu trữ tham số giao diện cục bộ (như tab đang chọn, kích thước cột), hoàn toàn không thể và không được dùng để lưu trữ toàn bộ cấu hình phiên làm việc.
   - Lát cắt S1-I cung cấp tính năng lưu trữ cấu hình có chủ quyền trong bộ nhớ và khả năng roundtrip JSON primitives minh bạch (`creative_context_to_dict` ↔ `json.dumps` ↔ `json.loads` ↔ `set_creative_context`). Việc lưu trữ dự án bền vững qua các lần khởi động lại (restart restore) thuộc lát cắt riêng sau này.

---

## 2. Thiết kế Tiếp nhận (Intake) tuân thủ Tuyệt đối Quyết định D03

Quyết định sản phẩm D03 quy định: *"Brief tự do + preset, nạp ảnh/video tham khảo riêng; link được lưu nhưng app chưa tự xem link (khuyên dùng cho bản đầu)."*

### 2.1. Các thành phần tiếp nhận dữ liệu trên Giao diện
Giao diện Tiếp nhận Ngữ cảnh Sáng tạo (`CreativeIntakePanel`) đặt tại Bước 2 (`StylePage`), bao gồm:
1. **Ô nhập Yêu cầu sáng tạo tự do (Free Brief Editor):**
   - Điều khiển: `QPlainTextEdit` hỗ trợ tiếng Việt có dấu đầy đủ.
   - Placeholder: *"Nhập tóm tắt câu chuyện, phong cách cá nhân, lưu ý nhịp điệu tự do..."*.
   - Áp dụng giới hạn ngân sách byte chuẩn tắc của core: Toàn bộ context không vượt quá 32KiB (`MAX_CREATIVE_CONTEXT_BYTES = 32768`). Không áp dụng giới hạn 512KiB hoặc cắt xén âm thầm 4096 ký tự.
2. **Quy tắc Bắt buộc giữ và Cấm (Semantic Rules):**
   - `must_keep_rules` & `must_not_rules`: Điều khiển một dòng / một quy tắc (`QPlainTextEdit`), bảo toàn trọn vẹn thứ tự và nội dung tiếng Việt. Giới hạn tối đa 128 quy tắc mỗi loại (`MAX_RULES_PER_KIND = 128`).
3. **Thời lượng mục tiêu tùy chọn (Target Duration):**
   - `QCheckBox` + `QLineEdit`. Khi checkbox tắt, giá trị lưu là `None` (không tự suy diễn thời lượng mặc định).
   - Hỗ trợ giá trị số thực (float) tùy ý (ví dụ: `45.5` hoặc `88.5`), bảo toàn chính xác, không ép kiểu làm mất phần thập phân. Thẩm định qua core validator.
4. **Tài liệu Tham khảo Cục bộ (Local Reference Items):**
   - Hỗ trợ thêm file ảnh hoặc video từ máy tính với ID ổn định (`reference_id`), đường dẫn tuyệt đối cú pháp (`local_path`), `role="reference_only"`, và `user_note` có thể biên tập.
   - Không thực hiện bất kỳ thao tác đọc đĩa, hash, stat, decode, hay kiểm tra `os.path.exists` nào khi nạp cấu hình. Tệp offline/thiếu vẫn được bảo toàn nguyên vẹn metadata.
5. **Đường dẫn Tham chiếu (Reference URLs):**
   - Lưu trữ dạng metadata chuỗi nguyên thủy (tối đa 64 URL).
   - Nhãn giao diện hiển thị cảnh báo tĩnh minh bạch:  
     > ⚠️ *Link được lưu trữ dưới dạng metadata; ứng dụng chưa tự xem hoặc phân tích nội dung link.*

### 2.2. Ranh giới Bảo vệ An toàn Dữ liệu (Strict Reference Isolation)
1. **Cách ly Footage Tuyệt đối:**  
   Các file trong `reference_items` **TUYỆT ĐỐI KHÔNG** được đưa vào `DirectorConfig.video_paths`, không được đưa vào `clip_infos`, và không bao giờ xuất hiện trong `source_binding.clips`.
2. **Không tự động duyệt web / VLM / Whisper:**  
   Không kích hoạt tải video, cào dữ liệu web hoặc phân tích tự động ngầm. Ghi chú tham chiếu là ý kiến chủ quan của người dùng, được dán nhãn nguồn gốc trung thực (`provenance-labeled`).

---

## 3. Dòng Chảy Snapshot & Xử lý Chu kỳ Callback

### 3.1. Bảo toàn Bản thảo qua Chu kỳ Callback Style
Trong mã nguồn hiện hành, khi người dùng bấm chọn một Style Card:
1. `StylePage._on_style_selected(style_id)` phát tín hiệu `style_changed(style_id)`.
2. `DirectorWindow._on_style_changed(style_id)` nhận tín hiệu và gọi tuần tự:
   - `self.config.apply_style_preset(style_id)`
   - `self.page_style.load_from_config(self.config)`
   - `self.adv_panel.load_from_config(self.config)`
3. **Chính sách bảo toàn:**
   - Trong quá trình diễn ra callback này, cờ `_in_style_transition = True` được kích hoạt.
   - Hàm `StylePage.load_from_config()` cập nhật các trường kiểu dựng legacy từ config, nhưng **tuyệt đối không ghi đè** lên bản thảo đang gõ dở trên `CreativeIntakePanel` (kể cả bản thảo chưa lưu hoặc tạm thời không hợp lệ).
   - Khi nạp cấu hình có chủ đích từ bên ngoài (deliberate project load), `_in_style_transition` là `False`, bản thảo trong intake panel được thay thế bình thường bằng dữ liệu chuẩn tắc từ config.

### 3.2. Giao dịch Lưu trữ An toàn (Transactional Save)
- `StylePage.save_to_config(config)` thu thập bản thảo thô và tiến hành thẩm định qua `normalize_creative_context()` TRƯỚC KHI chỉnh sửa bất kỳ trường nào của `config`.
- Nếu phát hiện lỗi (vượt quá 32KiB, vượt quá 128 quy tắc, thời lượng âm/lỗi, duplicate ID):
  - Hiển thị thông báo lỗi inline ngắn gọn bằng tiếng Việt trên `lbl_error`.
  - Giữ nguyên toàn bộ văn bản người dùng đã nhập trên giao diện.
  - Giữ nguyên 100% cấu hình `config` hiện hành.
  - Trả về `False`.
- Nếu hợp lệ: Cập nhật legacy fields, gán `config.creative_context_dict`, ẩn lỗi inline và trả về `True`.

---

## 4. Giao diện Duyệt Kịch bản Chung Duy nhất (Common Proposal Review — D01 / D02)
*(Trạng thái: `PROPOSED_DEFERRED` — Được thiết kế kiến trúc chuẩn tắc và hoãn triển khai sang lát cắt Step 3)*

### 4.1. Quy tắc Trình bày và Tách bạch Thẩm quyền
1. **Hiển thị Tường minh Tài nguyên Thiếu (Supplemental Omissions — D02):**
   - B-roll hoặc SFX bổ trợ bị thiếu trong kho lưu trữ được đưa vào bảng `proposed_omissions` kèm cảnh báo không chặn (`non-blocking`).
   - Việc thiếu footage video chính (A-Roll primary source) bắt buộc phải ném lỗi chặn (`ERR_MISSING_SOURCE_EVIDENCE`), không được che giấu dưới danh nghĩa omission.
2. **Quy tắc Tự nhiên Cần Người Kiểm Chứng (Human Verification Checklist):**
   - Các quy tắc ngữ nghĩa `must_keep_rules` và `must_not_rules` được hiển thị dưới dạng danh mục cần con người tích chọn kiểm chứng, không tuyên bố tự động xác minh bằng schema.
3. **Phân định giữa "Phê duyệt Biên tập" và "Quyền Thi công Resolve":**
   - Việc người dùng bấm *"Phê Duyệt Kịch Bản"* chỉ xác nhận tính đồng thuận biên tập (`Editorial Consent`) và tạo ra `AppOwnedApprovalRecord`.
   - Nút *"Dựng Timeline"* chỉ được kích hoạt khi đồng thời:
     a) Có `AppOwnedApprovalRecord` hợp lệ gắn chặt với `proposal_revision`.  
     b) Hình học kịch bản đạt trạng thái `GEOMETRY_VERIFIED`.  
     c) Điều kiện tiền trạm thi công G07 (Preflight check) vượt qua thành công.

---

## 5. Sự kiện Phê duyệt Thuộc Quyền Sở hữu của Ứng dụng (App-Owned Approval Event)

### 5.1. Tái sử dụng Trực tiếp API Hợp đồng Hiện hành
- Hàm chuẩn tắc `create_app_owned_approval_record(proposal, approval_id=..., approved_at_iso=...)` tại `src/core/contracts/edit_plan_approval_contracts.py` tự động chấp nhận **toàn bộ** omissions có trong proposal (`accepted_omission_ids = tuple(o.omission_id for o in proposal.proposed_omissions)`).
- Nếu trong tương lai người dùng chỉ chấp thuận một phần omissions qua checkbox giao diện, một `EditPlanProposal` mới đã được điều chỉnh phải được tạo ra trước khi phê duyệt. Tuyệt đối không thêm tham số tùy tiện vào hàm xuất xưởng approval.
- Hàm `verify_proposal_approval_binding(proposal, approval)` kiểm tra tính liên kết toàn vẹn 6 điểm (type, proposal_id, revision, omission_ids, scope, re-validation).
- Tuyệt đối không chấp nhận bất kỳ cờ phê duyệt nào do AI tự đề xuất (`approved: true`).

---

## 6. Ma trận Kiểm thử Giới hạn (Finite Test Matrix)

| STT | Mã Kịch bản | Tình huống Kiểm thử | Trạng thái Thực thi |
| :---: | :--- | :--- | :--- |
| 1 | `TC-INTAKE-01` | Cấu hình legacy rỗng/None; default factory độc lập. | `PASSED` (`test_scenario_01_legacy_empty_config_and_default_isolation`) |
| 2 | `TC-INTAKE-02` | Roundtrip JSON tiếng Việt, preset lạ, thời lượng số thực. | `PASSED` (`test_scenario_02_vietnamese_roundtrip_canonical_json_and_arbitrary_preset`) |
| 3 | `TC-INTAKE-02b`| Preset lạ được bảo toàn cho đến khi chủ động chọn thẻ mới. | `PASSED` (`test_scenario_02b_unrecognized_preset_preserved_until_explicit_selection`) |
| 4 | `TC-INTAKE-03` | Cô lập mutation và từ chối transactional dữ liệu lỗi. | `PASSED` (`test_scenario_03_mutation_isolation_and_transactional_rejection`) |
| 5 | `TC-INTAKE-04` | Programmatic load không phát tán tín hiệu; ID ổn định. | `PASSED` (`test_scenario_04_programmatic_load_signal_guards_and_stable_ids`) |
| 6 | `TC-INTAKE-05` | Chu kỳ callback style_changed bảo toàn bản thảo dở dang. | `PASSED` (`test_scenario_05_style_changed_callback_lifecycle_preserves_unsaved_draft`) |
| 7 | `TC-INTAKE-05b`| Chu kỳ callback style_changed bảo toàn bản thảo lỗi. | `PASSED` (`test_scenario_05b_style_changed_preserves_invalid_draft`) |
| 8 | `TC-INTAKE-06` | Lưu hợp lệ cập nhật; lưu lỗi giữ nguyên config & hiện lỗi. | `PASSED` (`test_scenario_06_valid_and_invalid_save_transactions`) |
| 9 | `TC-INTAKE-07` | Kiểm soát giới hạn thực tế (32KiB, 128 rules, float duration). | `PASSED` (`test_scenario_07_core_limits_and_fractional_duration`) |
| 10 | `TC-INTAKE-08` | Thao tác tham chiếu thêm/xóa/sửa ghi chú; URLs metadata. | `PASSED` (`test_scenario_08_reference_controls_and_url_metadata`) |
| 11 | `TC-INTAKE-09` | Cô lập reference khỏi footage; nạp file offline không lỗi. | `PASSED` (`test_scenario_09_reference_isolation_and_offline_files`) |
| 12 | `TC-INTAKE-10` | Khung cuộn và truy cập tốt tại 1200x750 và 1280x800. | `PASSED` (`test_scenario_10_scroll_area_and_target_resolution_accessibility`) |
| 13 | `TC-REVIEW-01..05` | Duyệt kịch bản chung, omissions, binding revision và G07 gate. | `PROPOSED_DEFERRED` (Sẽ triển khai tại lát cắt ReviewPage chung) |

---

## 7. Đánh giá Lát cắt Triển khai Đầu tiên (`G06-06C3-S1-I`)

### 7.1. Phạm vi Đã Hoàn thành
- **Mã lát cắt:** `G06-06C3-S1-I — Creative intake and owned config roundtrip`
- **Các module thực tế:**
  1. `src/ui/director/director_state.py`: Bổ sung canonical field `creative_context_dict`, các hàm chuyển đổi `get_creative_context()` và `set_creative_context()`.
  2. `src/ui/director/pages/style_page.py`: Xây dựng `CreativeIntakePanel`, tích hợp vào `StylePage` với `QScrollArea`, xử lý an toàn giao dịch lưu/nạp và chu kỳ style callback.
  3. `tests/ui/test_creative_intake_state.py`: 12 bài kiểm thử tự động toàn diện bao phủ toàn bộ ma trận hành vi.

### 7.2. Giá trị Nhìn Thấy Được
- Người dùng có thể nhập trực tiếp ý đồ sáng tạo tự do, thời lượng mục tiêu (kể cả số lẻ), quy tắc bắt buộc/cấm, đính kèm tệp ảnh/video tham chiếu và liên kết tham khảo tại Bước 2.
- Giao diện đáp ứng mượt mà ở các độ phân giải tiêu chuẩn 1200x750 và 1280x800.
- Bản thảo được bảo vệ an toàn, không bị xóa mất khi người dùng thử bấm các thẻ phong cách khác nhau.
- Cấu hình phiên làm việc được lưu giữ transactional và có khả năng roundtrip JSON chuẩn tắc 100%.

### 7.3. Kế hoạch Tiếp theo (Next Slices)
1. **Lát cắt S2 (Prompt Binding & Async Ownership):** Nối `DirectorConfig.creative_context_dict` vào `PromptPreparationRequest` và `PromptPreparationWorker` để đưa nội dung vào Web Prompt và Cloud/Local AI prompt, kèm cơ chế vô hiệu hóa cache khi sửa input.
2. **Lát cắt S3 (Common Review Page & Approval UI):** Xây dựng giao diện duyệt kịch bản thống nhất D01/D02 tại Bước 3 và sự kiện phê duyệt App-Owned.
3. **Lát cắt S4 (G07 Preflight & Resolve Timeline Execution):** Kiểm tra điều kiện tiền trạm và thi công kịch bản lên DaVinci Resolve.
