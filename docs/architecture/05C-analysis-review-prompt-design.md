# ChunDVC — G05-05C-D Thiết Kế Kiến Trúc: Tiêu Thụ Dữ Liệu Phân Tích Nguồn Trên ReviewPage & StoryCopilot (Review & Prompt Consumption Design)

- **Mã tài liệu:** `docs/architecture/05C-analysis-review-prompt-design.md`
- **Giai đoạn:** Gate G05 — Source Inventory, Speech-to-Text, Visual Understanding & Unified Cache Orchestration
- **Mã nhiệm vụ:** `G05-LIVE-EVIDENCE-R1-THEN-05C-D (Phần B: Design Only)`
- **Trạng thái:** `DESIGN_READY`
- **Cấp độ Worker:** `HIGH` (Duy trì nhất quán xuyên suốt phiên)
- **Phạm vi tuân thủ:** `DESIGN ONLY` — Tuyệt đối không sửa code `src/` hay `tests/`, không chạy pytest, không chạy ứng dụng GUI, không GPU inference, không gọi DaVinci Resolve API.
- **Tài liệu tham chiếu:**
  - `docs/architecture/05B-I3-live-local-review.md` (Codex Review & nghiệm thu có ghi nhận giới hạn)
  - `docs/architecture/05B-I3-local-vision-integration-design.md` (Thiết kế tích hợp Local VLM Stage 3)
  - `docs/architecture/05B-unified-source-analysis-design.md` (Quét nguồn hợp nhất 05B)
  - `docs/architecture/05A-audio-cache-identity-design.md` (Chuẩn hóa cache audio Schema v3)
  - `src/ui/director/workers/source_analysis_worker.py` (Worker quét nguồn 3 chặng)
  - `src/ui/director/pages/source_page.py` (Source inventory & snapshot)
  - `src/ui/director/director_window.py` (Điều phối chuyển trang và snapshot matching)
  - `src/ui/director/pages/review_page.py` (Trang duyệt nội dung và sinh prompt)
  - `src/core/clip_manifest.py` (Manifest định danh và `build_clips_data`)
  - `src/core/story_copilot.py` (Động cơ sinh Prompt biên tập phim)

---

## 1. KHẢO SÁT THỰC TẾ PIPELINE TIÊU THỤ (CODE_VERIFIED TRACE)

Để tránh hiện tượng "phantom API" (giả định hàm không tồn tại), toàn bộ chuỗi sản xuất $\rightarrow$ cầu nối $\rightarrow$ tiêu thụ dữ liệu phân tích nguồn được trace trực tiếp trên mã nguồn hiện hữu:

```text
[PRODUCER: SourceAnalysisWorker]
  ├── Stage 1: Audio STT (Whisper) -> ScanCacheManager Schema v3 [source_analysis_worker.py:382-598] (CODE_VERIFIED)
  ├── Stage 1: Visual Sampling (Overview) -> VisualIndex [source_analysis_worker.py:608-660] (CODE_VERIFIED)
  ├── Stage 2: Visual Describe (Local VLM) -> VisualIndex checkpoint [source_analysis_worker.py:669-798] (CODE_VERIFIED)
  └── Finished: StructuredAnalysisSnapshot + counters [source_analysis_worker.py:840-870] (CODE_VERIFIED)
        │
        ▼
[BRIDGE / SNAPSHOT: SourcePage & DirectorWindow]
  ├── SourcePage: lưu _runtime_status_config = StructuredAnalysisSnapshot [source_page.py:1641-1657] (CODE_VERIFIED)
  ├── SourcePage: phát source_analysis_completed(job_id, success, msg, counters) [source_page.py:1730] (CODE_VERIFIED)
  └── DirectorWindow: xác thực can_refresh_review qua snapshot matching [director_window.py:557-564] (CODE_VERIFIED)
        │
        ▼ Gọi chuyển giao
  └── DirectorWindow: page_review.load_transcripts_from_cache(paths, model, lang) [director_window.py:567-571] (CODE_VERIFIED)
        │
        ├──▶ [CONSUMER 1: ReviewPage UI]
        │     └── load_transcripts_from_cache() [review_page.py:1076-1101] (CODE_VERIFIED)
        │           ├── Chỉ nạp ScanCacheManager.get_cached_scan() (Whisper subtitles) (CODE_VERIFIED)
        │           ├── BỎ RƠI 100% clip không có lời thoại / không audio (CODE_VERIFIED - GAP THỰC TẾ)
        │           └── BỎ QUA 100% VisualIndex và mô tả thị giác trên bảng UI (CODE_VERIFIED - GAP THỰC TẾ)
        │
        └──▶ [CONSUMER 2: Prompt Producer (StoryCopilot)]
              └── ReviewPage.get_video_prompt() [review_page.py:1370-1400] (CODE_VERIFIED)
                    ├── ClipManifest.build_clips_data() [clip_manifest.py:355-472] (CODE_VERIFIED)
                    │     ├── Nạp VisualIndex.load(cpath, content_hash) [clip_manifest.py:440] (CODE_VERIFIED)
                    │     └── Trích xuất visual_segments, visual_coverage, visual_status [clip_manifest.py:463-468] (CODE_VERIFIED)
                    └── StoryCopilot.generate_copilot_prompt() [story_copilot.py:177-388] (CODE_VERIFIED)
                          ├── Cột tóm tắt thị giác 3 cảnh đầu trong bảng nguồn [story_copilot.py:296-308] (CODE_VERIFIED)
                          ├── Liệt kê chi tiết tối đa 8 đoạn mô tả thị giác [story_copilot.py:361-382] (CODE_VERIFIED)
                          └── Cảnh báo độ bao phủ incomplete hoặc status needs_review [story_copilot.py:352-360] (CODE_VERIFIED)
```

### 1.1 Điểm Phát Hiện Then Chốt:
1. **Giả định "Review bỏ qua toàn bộ thị giác" trong tài liệu cũ chỉ đúng MỘT NỬA:**
   - Trên **Giao diện ReviewPage UI** (`review_page.py:1076-1101`): Đúng 100%. Bảng review chỉ đọc `raw_subtitles`, không có bất kỳ cột, nhãn, thẻ hoặc panel nào hiển thị mô tả thị giác. Clip không lời bị biến mất hoàn toàn khỏi bảng duyệt.
   - Trong **Prompt Producer** (`clip_manifest.py:440` và `story_copilot.py:352-388`): Sai! Mã nguồn thực tế đã có cơ chế đọc `VisualIndex.load()` và format các đoạn `visual_segments` kèm cảnh báo độ bao phủ vào prompt gửi AI.
2. **Khoảng cách kiến trúc cần lấp đầy:**
   - Cần đưa thông tin thị giác lên trực quan giao diện `ReviewPage` (Model/View).
   - Bảo đảm clip không có thoại vẫn hiện diện đầy đủ, không bị biến mất và không bị phân loại nhầm thành B-roll.
   - Đồng bộ hóa Snapshot cấu hình từ `SourcePage` sang `ReviewPage` để tránh nạp dữ liệu từ các phiên chạy lệch cấu hình.

---

## 2. NGUYÊN TẮC THIẾT KẾ & MỤC TIÊU NGƯỜI DÙNG

### 2.1 Video Không Thoại / Không Audio Vẫn Phải Hiện Diện Đầy Đủ
- **Thực tế biên tập:** Một clip phong cảnh 4K quay drone 60 giây, một đoạn màn hình máy tính hướng dẫn thao tác, hay một cảnh quay cận cảnh sản phẩm không có tiếng nói nhưng là **chủ thể nội dung chính** của video, tuyệt đối **KHÔNG PHẢI TỰ ĐỘNG LÀ B-ROLL**.
- **Quy tắc kênh độc lập:**
  - **Kênh Thoại (Speech Channel):** Báo trạng thái trung thực `🔇 Không phát hiện thoại (0:00 - MM:SS)`.
  - **Kênh Thị Giác (Visual Observation Channel):** Báo rõ các mốc cảnh quay đã quan sát, ví dụ: `[00:00 - 00:08] Phong cảnh bãi biển lúc hoàng hôn`.
- **Hành vi trên UI:**
  - Clip không có thoại được hiển thị một banner/dòng danh mục rõ ràng trên `ReviewPage`:
    `🎞️ [C01 - drone_beach.mp4] 🔇 Không thoại | 🖼️ 3 cảnh quay đã phân tích`
  - Người dùng có thể xem trước các cảnh, chọn giữ lại hoặc loại bỏ phân đoạn đó trong bản dựng mà không bị ép buộc phải có lời thoại.

### 2.2 Kiến Trúc Model/View Cho Panel "Cảnh Quay" Trên ReviewPage (PROPOSED)
- **Chống phình to tài nguyên (Anti-Slop UI Rule):**
  - TUYỆT ĐỐI KHÔNG sinh hàng trăm `QWidget` card lồng nhau trong `QScrollArea`.
  - TUYỆT ĐỐI KHÔNG quét lại hệ thống tệp hoặc nạp lại ảnh/video từ đĩa trên luồng chính GUI.
- **Giải pháp kiến trúc:**
  - Tái sử dụng Qt Model/View chuẩn: Sử dụng `QTableView` hoặc `QListView` gắn với `VisualObservationModel` (kế thừa `QAbstractTableModel`).
  - Giao diện hai cột điều chỉnh được (Two-Pane Splitter Layout):
    - **Cột Trái (60%):** Bảng Lời thoại (`SpeechTranscriptTable`) — hiển thị phụ đề Whisper lọc nhiễu theo thời gian.
    - **Cột Phải (40%):** Bảng Cảnh quay & Quan sát thị giác (`VisualObservationTable`) — hiển thị các quan sát từ `VisualIndex`.
  - **Đồng bộ hóa 2 chiều (Time-Aligned Highlighting):**
    - Khi người dùng click chọn một câu thoại ở cột trái $\rightarrow$ cột phải tự động cuộn và highlight cảnh quay tương ứng với mốc thời gian đó.
    - Đối với clip không có thoại $\rightarrow$ cột trái hiển thị một dòng `[Khoảng lặng / Không thoại - MM:SS]`, cột phải hiển thị danh sách toàn bộ các cửa sổ cảnh quay đã quan sát.

---

## 3. THẨM QUYỀN TIÊU THỤ & BẢO TOÀN XUẤT XỨ (PROVENANCE INTEGRITY)

### 3.1 Thẩm Quyền Định Danh Nguồn (Source Identity Authority)
Để loại trừ triệt để việc nạp nhầm cache cũ khi tệp bị đổi tên, di chuyển hoặc ghi đè:
1. **Ứng viên nhanh cho hiển thị GUI (Fast Candidate for UI):**
   - Bộ nhận diện nhẹ `(norm_path, mtime_ns, size_bytes)`.
   - Cho phép nạp nhanh trong vài mili-giây để người dùng không bị đơ giao diện khi chuyển tab.
2. **Thẩm quyền xác thực cho thực thi AI / Export (Execution Authority):**
   - Bắt buộc kiểm tra `verified_identity = (sha256, provenance)` từ Schema v3 của `ScanCacheManager` và `content_hash` trong `VisualIndex`.
   - Nếu phát hiện tệp trên đĩa bị thay đổi sau khi quét $\rightarrow$ đánh dấu trạng thái `source_mutated_stale`, khóa nút tạo Prompt và yêu cầu quét lại.
3. **Chặn các khe hở (Seams to Block):**
   - *Khe hở Rename/Move:* Khi tệp đổi tên nhưng giữ nguyên hash $\rightarrow$ `ClipManifest` sử dụng `content_hash` để định vị lại cache, nhưng `ReviewPage` phải hiển thị rõ tên tệp mới nhất trên timeline.
   - *Khe hở Same Filename Khác Thư Mục:* Bắt buộc dùng `normalized_absolute_path` làm khóa tra cứu gốc; không bao giờ dùng `basename` đơn thuần để nạp cache.

### 3.2 Bảo Toàn Xuất Xứ Bằng Chứng Thị Giác (Fact Grounding vs Inference)
- **Nguyên tắc cốt tử:** `Model Caption != Human-Verified Fact`.
- Mô tả của mô hình thị giác chỉ là **quan sát máy móc (machine observation)** có xác suất sai sót, không phải chân lý tuyệt đối.
- **Trường dữ liệu bảo toàn provenance:**
  ```python
  @dataclass(frozen=True)
  class VisualObservationItem:
      window_id: str             # "W00000"
      start_sec: float           # 0.0
      end_sec: float             # 8.0
      description: str           # "Xe ô tô màu đỏ di chuyển qua ngã tư"
      observed_actions: Tuple[str, ...] # ("xe ô tô di chuyển",)
      evidence_times: Tuple[float, ...]  # (1.5, 4.5)
      evidence_frame_ids: Tuple[str, ...] # ("F0", "F1")
      analysis_status: str       # "described" | "needs_review" | "insufficient" | "error"
      origin: str                # "local_model:qwen3-vl:2b-instruct" | "user_annotated"
      confidence: str            # "high" | "medium" | "low"
  ```
- **Ràng buộc sinh Prompt biên tập:**
  - Prompt chỉ được truyền đạt **bằng chứng vật lý thực tế quan sát được** (màu sắc, hình dạng, phương tiện, hành động cơ bản).
  - TUYỆT ĐỐI KHÔNG đưa intent suy diễn (ví dụ: "người đàn ông đang tức giận", "kẻ trộm đang rình rập") hoặc danh tính giả định vào Prompt trừ khi có ghi chú tường minh từ người dùng (`user_annotated`).
  - Phải ghi rõ tỷ lệ bao phủ: `⚠️ Cảnh báo: Video 60s chỉ được phân tích 2/8 cửa sổ. Các vùng còn lại không có thông tin.`

### 3.3 Ngân Sách Prompt Phân Cấp Cho Dự Án Lớn (Hierarchical Summary Budget)
- Đối với dự án dài (1-3 giờ, 50-100 clip), việc nhồi nhét toàn bộ các cửa sổ mô tả thị giác vào một Prompt sẽ làm **tràn context window của LLM** và gây ảo giác.
- **Chiến lược phân cấp (Hierarchical Prompt Budget):**
  1. *Cấp độ Clip (Macro):* Mỗi clip hiển thị thời lượng, trạng thái thoại, và 1 câu tóm tắt tổng thể (overview summary).
  2. *Cấp độ Cửa sổ (Micro):* Chỉ gửi chi tiết tối đa **3 đến 5 cửa sổ tiêu biểu nhất** (các cửa sổ có sự chuyển động hoặc điểm rơi kịch tính), kèm mốc thời gian rõ ràng.
  3. Bổ sung dòng tóm tắt: `*(và N phân đoạn hình ảnh khác; xem chi tiết tại mốc thời gian...)*`.

---

## 4. TÁCH BẠCH VÒNG ĐỜI WHISPER VÀ THỊ GIÁC (LIFECYCLE SEPARATION)

1. **Clip Không Thoại (Silent Clip):**
   - Khi Whisper chạy xong và trả về `raw_subtitles = []` $\rightarrow$ Đây là kết quả **HOÀN THÀNH HỢP LỆ** (`status = "complete"`, `no_speech = True`).
   - TUYỆT ĐỐI KHÔNG coi đây là lỗi (`error`) và không được để trạng thái là chưa quét (`unscanned`).
   - `ReviewPage` phải coi clip này là sẵn sàng duyệt (Ready for Review) ở kênh thị giác.
2. **Thị Giác Tắt Hoặc Không Khả Dụng (Missing / Disabled Vision):**
   - Khi người dùng tắt mô tả thị giác hoặc model Ollama chưa bật $\rightarrow$ Kênh hình ảnh hiển thị `Chưa quét mô tả (Tắt)`.
   - Clip vẫn xuất hiện bình thường trên `ReviewPage` nếu đã có dữ liệu thoại từ Whisper.
   - TUYỆT ĐỐI KHÔNG làm clip biến mất hoặc ép buộc phải có cả 2 kênh mới được hiển thị.
3. **Cấm Buộc Quét Lại (Zero Forced Rerun):**
   - Chuyển tab từ Source sang Review tuyệt đối KHÔNG kích hoạt Whisper quét lại nếu cache Schema v3 đã tồn tại.

---

## 5. THIẾT KẾ LÁ CHẮN LỖI VÀ CHUYỂN ĐỔI TRẠNG THÁI (LATE CALLBACK & SELECTION GUARDS)

1. **Lá chắn Job Generation Token:**
   - Mỗi phiên quét nguồn tạo một `job_id` và `generation_token` duy nhất.
   - Khi người dùng chuyển tab, ẩn cửa sổ, mở lại, hoặc đổi danh sách tệp:
     - Mọi callback chạy ngầm từ worker cũ so khớp `token != active_token` sẽ bị **loại bỏ trong yên lặng (silent drop)**.
     - Tuyệt đối không bật popup dialog cảnh báo muộn gây gián đoạn thao tác người dùng.
2. **Xử lý Hủy Phân Tích (Honest Cancel State):**
   - Nếu người dùng bấm [Hủy] khi đang quét dở:
     - Các clip đã quét xong giữ nguyên kết quả hợp lệ (`status: complete`).
     - Clip đang quét dở hoặc chưa quét được gắn nhãn minh bạch `Đã hủy (Chưa hoàn tất)`.
     - `ReviewPage` chỉ nạp các phân đoạn của những clip đã hoàn tất, không giả mạo thành công toàn bộ.
3. **Không Đụng Chạm Timeline DaVinci Resolve:**
   - Suốt quá trình Review và tạo Prompt, toàn bộ dữ liệu chỉ nằm trong bộ nhớ RAM và cache local của ứng dụng.
   - Không thực hiện bất kỳ lệnh can thiệp nào vào Project/Timeline của DaVinci Resolve cho đến khi người dùng chuyển sang Bước 4 (Build Timeline).

---

## 6. LÁT CẮT TRIỂN KHAI ĐẦU TIÊN DUY NHẤT (FIRST IMPLEMENTATION SLICE: 3 MODULES)

Nhằm tuân thủ nguyên tắc triển khai hữu hạn, an toàn và dễ kiểm chứng, Slice 1 chỉ tác động trực tiếp vào **ĐÚNG 3 MODULES LIÊN KẾT CHẶT CHẼ**:

```text
[LÁT CẮT TRIỂN KHAI 1 (FIRST SLICE: 3 MODULES)]
├── 1. src/ui/director/pages/review_page.py
│     ├── Bổ sung hàm load_analysis_data(clip_infos, snapshot, cache_mgr, visual_index)
│     ├── Giữ tương thích ngược cho caller cũ load_transcripts_from_cache()
│     └── Hiển thị clip không có lời thoại (silent clips) kèm mốc quan sát thị giác trên Review
│
├── 2. src/ui/director/director_window.py
│     ├── Truyền đầy đủ StructuredAnalysisSnapshot và visual_index vào ReviewPage
│     └── Không lọc bỏ các clip không có thoại khi điều phối chuyển sang Bước 3 (Review)
│
└── 3. src/core/clip_manifest.py (build_clips_data)
      ├── Đảm bảo clip không có thoại giữ speech_status="no_dialogue_detected" thay vì rỗng
      └── Gắn kết visual_coverage và visual_analysis_status trung thực vào từng clip_data
```

### 6.1 Backlog Các Phần Còn Lại (Dependencies):
- **Slice 2 (G05-05D):** Tinh chỉnh Split-Panel UI hai cột trên `ReviewPage` với Model/View và highlight đồng bộ.
- **Slice 3 (G06):** Động cơ Story Recipe Engine và chuyển giao Timeline Plan xuống DaVinci Resolve.
- **Slice 4 (G08 Residual):** Hiệu đính bảng màu chữ trắng trên nền sáng cho các hộp thoại modal.

---

## 7. CHIẾN LƯỢC KIỂM THỬ HỮU HẠN (FINITE TEST SPECIFICATION)

Bộ kiểm thử hữu hạn không phụ thuộc GPU / mạng ngoại tuyến được định nghĩa cho Slice 1:
- **Tệp kiểm thử:** `tests/ui/test_review_page_visual_consumption.py`
- **Các ca kiểm thử trọng tâm (Mock / Tmp Isolated):**
  1. `test_silent_clip_without_speech_appears_in_review_table`: Xác minh clip không có thoại vẫn hiển thị trên bảng Review với nhãn `no_dialogue_detected` và không bị biến mất.
  2. `test_review_page_loads_both_speech_and_visual_observations`: Xác minh clip có cả 2 kênh hiển thị đầy đủ transcript và visual segments.
  3. `test_prompt_generation_includes_grounded_visual_evidence`: Xác minh `build_clips_data` và `generate_copilot_prompt` xuất ra đúng các mốc thời gian, mô tả và cảnh báo độ bao phủ.
  4. `test_snapshot_invalidation_prevents_stale_review_refresh`: Xác minh khi thay đổi vision_model hoặc endpoint, ReviewPage từ chối nạp snapshot cũ lệch cấu hình.
  5. `test_late_callback_guard_does_not_popup_dialog_when_job_cancelled`: Xác minh worker phát tín hiệu sau khi cancel không gây popup lỗi giả mạo.

---

## 8. CÁC CÂU HỎI QUY CHUẨN THIẾT KẾ (POLICY QUESTIONS & RECOMMENDATIONS)

Để đảm bảo tính nhất quán trải nghiệm người dùng, dưới đây là 3 câu hỏi chính sách kèm khuyến nghị cụ thể:

### Câu hỏi 1: Bố cục hiển thị quan sát thị giác trên giao diện `ReviewPage`?
- **Phương án A (Khuyến nghị):** Bố cục Splitter 2 cột song song (Cột Trái: Lời thoại; Cột Phải: Cảnh quay). Có thanh trượt co giãn tỷ lệ và highlight đồng bộ theo thời gian.
- **Phương án B:** Bố cục 2 Tab chuyển đổi qua lại (`[Tab 1: Lời thoại]` / `[Tab 2: Cảnh quay]`).
- **Lý do khuyến nghị:** Phương án A cho phép biên tập viên vừa đọc lời thoại vừa quan sát diễn biến hình ảnh cùng một lúc, phù hợp nhất với tư duy dựng phim chuyên nghiệp.

### Câu hỏi 2: Cách biểu diễn các khoảng lặng (silence / no-speech gaps) trong clip có thoại?
- **Phương án A (Khuyến nghị):** Giữ bảng Lời thoại chỉ chứa các câu thoại thực tế; các khoảng lặng và cảnh chèn được hiển thị tương ứng ở cột Cảnh quay bên phải.
- **Phương án B:** Chèn các dòng giả `[Khoảng lặng 5.2s - Không thoại]` trực tiếp vào giữa các dòng trong bảng Lời thoại.
- **Lý do khuyến nghị:** Phương án A giữ bảng Lời thoại sạch sẽ, đúng chuẩn phụ đề kịch bản, không gây rối mắt hoặc làm sai lệch chỉ số đếm từ vựng.

### Câu hỏi 3: Ngân sách số lượng quan sát thị giác đưa vào Prompt gửi LLM?
- **Phương án A (Khuyến nghị):** Ngân sách phân cấp: Tối đa 3 cảnh đầu trong bảng tóm tắt nguồn, tối đa 5-8 cảnh tiêu biểu trong phần chi tiết từng clip, kèm cảnh báo độ bao phủ.
- **Phương án B:** Đưa toàn bộ 100% các cửa sổ quan sát của tất cả clip vào Prompt.
- **Lý do khuyến nghị:** Phương án A bảo vệ token context của người dùng không bị tràn khi dự án có hàng chục clip, bảo đảm LLM tập trung vào các điểm kịch tính nhất mà không bị loãng thông tin.
