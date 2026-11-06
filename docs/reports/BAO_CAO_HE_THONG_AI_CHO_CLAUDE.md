# Báo cáo Phân tích Kỹ thuật Hệ thống AI / LLM Subsystem (ResolveFlow-Assistant)

> **Chế độ kiểm toán:** READ-ONLY (Chỉ đọc, không can thiệp tệp tin mã nguồn).  
> **Phạm vi phân tích:** Toàn bộ phân hệ AI/LLM bao gồm trích xuất & tiền xử lý âm thanh, nhận dạng giọng nói (ASR), làm sạch transcript, kiến tạo prompt, thực thi suy luận LLM (Cloud & Local Ollama), trích xuất & xác thực JSON, và ánh xạ kịch bản sang Timeline DaVinci Resolve.

---

## 1. Pipeline Map (Sơ đồ luồng xử lý toàn tuyến)

| Bước | Tác vụ | File / Hàm phụ trách | Bản chất | Chi tiết xử lý |
| :---: | :--- | :--- | :---: | :--- |
| **1** | Trích xuất Audio từ Video nguồn | `extract_audio` (`src/core/audio.py#L38-L95`) | **Deterministic** | Chạy FFmpeg tách track âm thanh sang WAV 16kHz Mono PCM chuẩn đầu vào cho Whisper. |
| **2** | Nhận dạng giọng nói (STT) & sinh Word Timestamps | `ResolveTranscriber.transcribe` (`src/core/transcriber.py#L182-L268`) | **AI-driven** | Dùng mô hình faster-whisper (CTranslate2) sinh transcript kèm word-level timestamps và độ tin cậy (`probability`). |
| **3** | Lọc ảo giác cơ bản (Regex Hallucination Guard) | `is_whisper_hallucination` (`src/core/transcriber.py#L35-L55`) | **Deterministic** | Loại bỏ câu chào kết thúc kênh, kêu gọi like/subscribe đặc trưng của Whisper khi gặp audio tĩnh. |
| **4** | Tách phân đoạn bị giãn thời gian (Stretched Segments) | `split_stretched_segments` (`src/core/transcriber.py#L58-L100`) | **Deterministic** | Ngắt phân đoạn khi khoảng cách giữa 2 từ > 2.5s; giới hạn từ kéo dài > 4.0s về 1.0s. |
| **5** | Làm sạch sâu Whisper (Ký tự lạ, lặp vô tận, kiểm duyệt) | `clean_whisper_segments` (`src/core/whisper_cleaner.py#L125-L185`) | **Deterministic** | Khử ký tự CJK/Cyrillic (`C_FOREIGN_SCRIPT`), khử lặp chuỗi rác (`is_noise_chatter`), khử lặp cụm từ liên tiếp (`deduplicate_phrase_repetitions`), và kiểm duyệt từ cấm (`CENSOR_MAP`). |
| **6** | Quét cao trào âm thanh & Sắp xếp theo dòng thời gian | `find_audio_climax` & `sort_clips_chronologically` (`src/core/chronological_sorter.py`) | **Deterministic** | Đo peak RMS audio và sắp xếp clip theo metadata EXIF / QuickTime Creation Time / Filename time. |
| **7** | Kiến tạo Prompt Đạo diễn Video (V1) | `StoryCopilot.generate_copilot_prompt` (`src/core/story_copilot.py#L104-L385`) | **Deterministic** | Tuần tự hóa bảng clip, 12 câu thoại đại diện mỗi clip, danh mục Meme/SFX và quy tắc biên kịch. |
| **8** | Kiến tạo Prompt Phụ đề Động Kinetic (V2) | `generate_kinetic_prompt` (`src/core/kinetic/kinetic_engine.py#L230-L345`) | **Deterministic** | Đánh số chỉ số từ `[i]` dựa trên transcript đã cắt gọt của Final Cut (`get_final_cut_words`). |
| **9** | Thực thi suy luận LLM (Inference) | `run_cloud_api_inference`, `run_ollama_inference`, hoặc Copy-Paste thủ công | **AI-driven** | LLM (Gemini, DeepSeek, GPT-4o, Claude hoặc Qwen Local) đọc cấu trúc dự án và tạo kịch bản JSON. |
| **10** | Trích xuất cú pháp & Chuẩn hóa JSON phản hồi | `StoryCopilot.parse_copilot_response` & `parse_web_prompt_json` (`src/core/story_copilot.py#L387-L457` & `src/ui/director/director_state.py#L241-L374`) | **Deterministic** | Làm sạch ký tự Unicode vô hình, dấu ngoặc thông minh, comment, trailing commas, nhận dạng Dual Slot. |
| **11** | Kiểm tra ràng buộc Schema & Ngữ nghĩa | `parse_copilot_response` (V1) & `validate_plan` (V2) | **Deterministic** | Chuyển đổi sang Pydantic Model (`CopilotDirectorPlan`) và kiểm tra độ liên tục của `word_range`. |
| **12** | Ánh xạ Kịch bản sang Timeline DaVinci Resolve | `StoryCopilot.convert_plan_to_resolve_timeline` (`src/core/story_copilot.py#L801-L1075`) | **Deterministic** | Ghép Track 1 (Story/Hook + đệm 0.3s), Track 2 (Meme/B-Roll), Track 3 (SFX lọc giãn cách 10s), tính toán Timecode cursor và điều chỉnh tốc độ. |

---

## 2. Transcription (Hệ thống nhận dạng giọng nói)

### 2.1. Thư viện, Mô hình & Tham số
- **Thư viện:** `faster-whisper` (dựa trên CTranslate2) được tải và khởi tạo tại `ResolveTranscriber._load_model` (`src/core/transcriber.py#L120-L170`).
- **Mô hình hỗ trợ:** Tự động cấu hình qua `ModelConfig` với các kích cỡ `tiny`, `base`, `small` (mặc định), `medium`, `large-v3`.
- **Thiết bị & Kiểu dữ liệu:** Hỗ trợ `device="cuda"` (nếu có NVIDIA GPU) hoặc `"cpu"`; `compute_type` tương ứng là `float16` / `int8_float16` trên GPU, và `int8` / `float32` trên CPU (`src/core/transcriber.py#L125-L135`).
- **Tham số chạy `model.transcribe` (`src/core/transcriber.py#L213-L224`):**
  - `language`: Ngôn ngữ do người dùng chọn (mặc định `"vi"`).
  - `vad_filter=True`: Kích hoạt bộ lọc Silero VAD tích hợp trong faster-whisper để cắt bỏ các đoạn tĩnh.
  - `vad_parameters=dict(min_silence_duration_ms=500)`: Ngưỡng phát hiện khoảng lặng tối thiểu 500ms.
  - `word_timestamps=True`: Bắt buộc để trích xuất mốc thời gian chi tiết từng từ.
  - `beam_size=5`: Tăng độ chính xác beam search.
  - `condition_on_previous_text=False`: Vô hiệu hóa việc phụ thuộc vào text trước đó để triệt tiêu lỗi lặp vòng vô tận đặc thù của Whisper.

### 2.2. Lọc văn bản lặp & Ảo giác (Hallucination / Noise Filtering)
Hệ thống sử dụng đường ống làm sạch 3 lớp hoàn chỉnh:
1. **Lọc câu cảm ơn/ảo giác tĩnh:** Hàm `is_whisper_hallucination` dùng Regex quét các mẫu rác kinh điển:  
   `r"(cảm ơn|theo dõi|đăng ký|subscribe|hẹn gặp lại|like and sub|tạm biệt|chúc các bạn|chia sẻ clip)"`.
2. **Khử ký tự ngoại lai & tạp âm vô nghĩa:** Hàm `clean_whisper_segments` loại bỏ:
   - Ký tự Trung, Nhật, Hàn, Cyrillic qua Regex:  
     `C_FOREIGN_SCRIPT = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]')` (`src/core/whisper_cleaner.py#L22`).
   - Chuỗi rác chatter âm thanh (ví dụ: `l l l l`, `dodololol`, `la la la`) qua `is_noise_chatter`.
3. **Khử lặp cụm từ (Deduplication):** Hàm `deduplicate_phrase_repetitions` quét n-gram từ 1 đến 5 từ và xóa các cụm từ bị lặp liên tiếp $\ge 3$ lần.
4. **Kiểm duyệt ngôn từ (Censorship):** `censor_profanity` đối soát bảng `CENSOR_MAP` để thay thế các từ nhạy cảm tiếng Việt bằng ký tự ẩn (`đ**`, `l**`, `c**`).

### 2.3. Cấu trúc dữ liệu của Một Từ và Một Clip

**Cấu trúc một Từ (Word Dict):** (`src/core/transcriber.py#L260-L267`)
```python
{
    "word": str,          # Ví dụ: "Khám"
    "start": float,       # Mốc bắt đầu tính bằng giây trên clip nguồn (ví dụ: 10.05)
    "end": float,         # Mốc kết thúc tính bằng giây trên clip nguồn (ví dụ: 10.45)
    "probability": float  # Độ tin cậy mô hình Whisper gán cho từ (0.0 đến 1.0)
}
```

**Cấu trúc một Clip sau quét Scan Cache (Clip Scan Dict):** (`src/core/transcriber.py#L270-L315` & `src/ui/director/pages/source_page.py#L312-L340`)
```python
{
    "clip_index": int,                # Thứ tự 1-based sau khi sắp xếp theo thời gian
    "clip_name": str,                 # Tên tệp (ví dụ: "DJI_0001.MP4")
    "path": str,                      # Đường dẫn tuyệt đối đến tệp video
    "duration": float,                # Tổng độ dài clip (ví dụ: 60.0)
    "audio_peak_sec": float,          # Giây xảy ra âm thanh cao trào nhất (ví dụ: 15.2)
    "visual_motion": str,             # Cường độ chuyển động ("thấp", "vừa", "cao")
    "is_broll": bool,                 # True nếu không có lời thoại hoặc toàn cảnh phong cảnh
    "transcript": str,                # Toàn bộ nội dung lời thoại ghép liền
    "dialogue_intervals": [           # Danh sách phân đoạn câu thoại đã lọc
        {
            "start": float,           # Giây bắt đầu câu (ví dụ: 10.0)
            "end": float,             # Giây kết thúc câu (ví dụ: 13.5)
            "text": str,              # Nội dung câu
            "words": List[dict]       # Danh sách các từ chi tiết (Word Dict)
        }
    ]
}
```

---

## 3. Prompt Construction (Cấu trúc sinh Prompt)

### 3.1. Trích dẫn đầy đủ các Template Prompt

#### A. Marketing Framework Templates (Video Plan V1)
Được định nghĩa tại `StoryCopilot.FORMAT_PROMPTS`:
```python
    FORMAT_PROMPTS = {
        "travel_vlog": """# 🎯 BẠN LÀ TỔNG ĐẠO DIỄN NỘI DUNG & BIÊN KỊCH DAILY VLOG / TRAVEL VLOG (CHIEF STORYTELLER)
## 🧠 NGUYÊN TẮC BIÊN TẬP VLOG:
1. **Tôn Trọng Mạch Thời Gian (Chronological Flow):** Giữ câu chuyện theo diễn tiến thực tế của chuyến đi.
2. **Nhịp Thở Tự Nhiên & Giữ Trọn Vẹn Câu Thoại:**
   - Tuyệt đối KHÔNG cắt ngang giữa chừng khi nhân vật đang nói (tránh bị cụt chữ, mất nhịp).
   - Hãy để câu nói kết thúc trọn vẹn, nghỉ thở tự nhiên rồi mới chuyển cảnh (10s - 35s/phân đoạn).
   - Bạn CHỈ CẦN CHỈ ĐỊNH CHÍNH XÁC điểm bắt đầu và kết thúc của câu chuyện. Hệ thống sẽ TỰ ĐỘNG tính toán khoảng đệm an toàn.
3. **Đoạn Mở Đầu Teaser / Highlight Hook Độc Lập (30s - 60s):**
   - Đối với Vlog, hãy chọn một đoạn cao trào gay cấn, hài hước hoặc một câu chuyện dạo đầu kịch tính kéo dài từ 30s đến 60s (hoặc tối thiểu 15s-30s) để làm Teaser Hook mở đầu.
   - Đoạn này đặt độc lập ở đầu video để kích thích người xem tò mò xem hết vlog.
   - Trong JSON, đặt tên `hook_title` rõ ràng (Ví dụ: `🔥 [TEASER HOOK 45s] - Tên đoạn mở đầu`) để trên DaVinci Resolve hiển thị rõ ràng, dễ nhận biết và tách sửa.
4. **Điều Tiết SFX & Meme (Tuyệt Đối Không Lạm Dụng / Spam):**
   - Chỉ chèn SFX khi THỰC SỰ có điểm rơi cảm xúc hoặc chuyển cảnh quan trọng (tối đa 1 SFX mỗi 20-30 giây).
   - Không spam liên tục cùng một âm thanh (như Vine Boom, Whoosh). Phải đa dạng âm thanh hoặc để không gian yên tĩnh tự nhiên.
   - Toàn bộ video dài chỉ chèn 2-4 meme/broll đắt giá nhất, không chèn tràn lan.
""",
        "tiktok_short": """# 🎯 BẠN LÀ CHUYÊN GIA DỰNG VIDEO NGẮN VIRAL (TIKTOK / REELS / SHORTS 9:16)
## 🧠 NGUYÊN TẮC DỰNG SHORTS VIRAL:
1. **Hook 3 Giây Đầu Cực Gắt:** Chọn câu phát ngôn gây tò mò/sốc nhất đặt ngay Frame 0:00.
2. **Bảo Toàn Ý Nghĩa Lời Thoại:** Cắt khoảng lặng thừa nhưng TUYỆT ĐỐI không cắt đứt giữa từ hay giữa câu nói (không gây giật giọng, mất nhịp).
3. **Tổng Thời Lượng Mục Tiêu:** 45s đến 60s, chắt lọc chỉ lấy tinh hoa của toàn bộ clip.
4. **Tiết Chế SFX & Meme:** Chỉ chèn âm thanh (Vine Boom, Whoosh, Pop) đúng điểm rơi bất ngờ. Không spam âm thanh liên tục gây nhức đầu. Tối đa 1-2 meme cho toàn clip.
""",
        "podcast_summary": """# 🎯 BẠN LÀ ĐẠO DIỄN TALKING HEAD / PODCAST & HIGHLIGHTS
## 🧠 NGUYÊN TẮC BIÊN TẬP PODCAST:
1. **Dọn Dẹp Ngữ Nghĩa Nhưng Tròn Vành Rõ Chữ:** Loại bỏ khoảng lặng thừa nhưng giữ trọn vẹn từng câu nói, không cắt giữa chừng chữ.
2. **Cấu Trúc Luận Điểm Rõ Ràng:** Giữ trọn vẹn từng ý kiến, câu chuyện của diễn giả.
3. **Punch-in Zoom:** Đổi góc máy cận khi chuyển ý quan trọng.
4. **SFX Điểm Nhấn Tinh Tế:** Ding / Pop chỉ khi xuất hiện từ khóa hoặc số liệu chính, tuyệt đối không lạm dụng.
"""
    }
```

#### B. Kinetic Typography Prompt Template (Subtitles V2)
Được định nghĩa tại `generate_kinetic_prompt`:
- **Khi danh sách từ > 300 từ (Tối ưu hóa Token, điều hướng sang Preset & Offline Engine):**
```python
    return f"""# 🎯 BẠN LÀ CHUYÊN GIA NGHỆ THUẬT KINETIC TYPOGRAPHY CHO DAVINCI RESOLVE
## ℹ️ THÔNG BÁO TỐI ƯU HÓA TOKEN & TỰ ĐỘNG HÓA OFFLINE (0đ TOKEN):
Video này có tổng cộng **{n_words} từ ngữ** (tương đương ~{n_words // 2} trang phụ đề động).
Do số lượng từ quá lớn (vượt quá giới hạn output token của Claude/ChatGPT), bạn **KHÔNG CẦN** xuất toàn bộ mảng `pages` cho 15.000 từ.
👉 Phần mềm ResolveFlow đã có nút **'⚡ Tự động tạo kịch bản Kinetic (Offline - 0đ Token)'** trên giao diện Slot 2 để tự động ngắt trang 100% chuẩn xác trong 0.05 giây!
## 🎨 NHIỆM VỤ CỦA BẠN:
Hãy phân tích ngữ cảnh của video (mẫu lời thoại: `{sample_words}...`) và thiết kế **Bộ Preset Phong Cách Phụ Đề (Presets & Palette)** định dạng `{aspect}` đẹp nhất, trả về đúng định dạng JSON bên dưới:
```json
{{
  "schema_version": "1.0",
  "canvas": {{ "aspect": "{aspect}" }},
  "presets": {{
    "hormozi": {{
      "font": "{font}",
      "style": "Black",
      "size": 0.085,
      "uppercase": true,
      "text_color": "#FFFFFF",
      "highlight_color": "#FFD400",
      "outline_color": "#000000",
      "outline_width": 0.08,
      "default_animation": "scale_pop"
    }},
    "climax": {{
      "font": "{font}",
      "style": "Black",
      "size": 0.095,
      "uppercase": true,
      "text_color": "#FF2A85",
      "highlight_color": "#00F0FF",
      "outline_color": "#000000",
      "outline_width": 0.09,
      "default_animation": "kinetic_bounce"
    }}
  }},
  "pages": [
    {{ "word_range": [0, 2], "preset": "hormozi" }}
  ]
}}
```
"""
```

- **Khi danh sách từ $\le$ 300 từ (Prompt chi tiết từng từ):**
```python
    return f"""# 🎯 BẠN LÀ CHUYÊN GIA KINETIC TYPOGRAPHY & PHỤ ĐỀ HOẠT ẢNH CHO DAVINCI RESOLVE
## 🧠 NHIỆM VỤ:
Phân tích danh sách từ ngữ bên dưới và lập bản thiết kế phụ đề động (Director Text Plan) cho video định dạng {aspect}.
## 🎬 DANH SÁCH TỪ NGỮ CỦA VIDEO KÈM CHỈ SỐ [i]:
{indexed_text}
## ⚡ NGUYÊN TẮC THIẾT KẾ PHỤ ĐỀ HOẠT ẢNH (KINETIC / HORMOZI):
1. **Chia Page hợp lý:** Mỗi cụm từ (page) chỉ gồm 1 đến 3 từ (tối đa 4 từ). Ngắt theo nhịp thở tự nhiên, dấu câu và trọng tâm ý nghĩa.
2. **Các Page phải liên tục & phủ hết mọi từ:** `word_range` bắt đầu từ [0, b], sau đó [b+1, c]... đến hết từ cuối cùng ({len(words)-1}). Không được nhảy cóc hay bỏ sót từ.
3. **Chọn từ khóa đinh để nhấn mạnh (10% - 15% tổng số từ):**
   - Chỉ gán `emphasis: "high"` hoặc `"medium"` cho các con số, số tiền, động từ hành động mạnh, kết quả, hoặc từ biểu lộ cảm xúc cực độ.
   - Các từ còn lại KHÔNG cần liệt kê vào mảng `words` (sẽ tự động dùng mặc định).
4. **Hiệu ứng có sẵn (animation):**
   - `kinetic_bounce`: Nảy phóng to lò xo kết hợp tâng nhẹ trục Y (phù hợp từ khóa cao trào).
   - `scale_pop`: Phóng to nảy lò xo tại chỗ (mặc định cho câu).
   - `neon_glow`: Hiệu ứng đèn neon phát sáng viền.
   - `box_swipe`: Dải màu quét nền dạ quang từ trái sang phải phía sau chữ.
   - `glitch`: Giật rung màu điện ảnh (dùng tiết chế cho từ mang tính sốc/bất ngờ).
5. **Vị trí (position):** Mặc định `"bottom"` (đã né vùng an toàn giao diện TikTok/Reels/Shorts). Dùng `"center"` cho câu hook mở đầu nếu cần.
6. **TUYỆT ĐỐI KHÔNG GÕ LẠI TRANSCRIPT & KHÔNG TỰ BỊA TIMESTAMP:** Mọi từ đều tham chiếu qua chỉ số `i` và `word_range`. `text_override` chỉ dùng khi muốn sửa lỗi chính tả Whisper hoặc viết số (ví dụ: "1000" thay vì "một nghìn").
## 📤 YÊU CẦU ĐẦU RA (OUTPUT FORMAT):
Hãy trả về ĐÚNG MỘT KHỐI JSON DUY NHẤT (trong cặp dấu ```json ... ```) theo cấu trúc chuẩn sau:
```json
{{
  "schema_version": "1.0",
  "canvas": {{ "aspect": "{aspect}" }},
  "presets": {{
    "hormozi": {{
      "font": "{font}",
      "style": "Black",
      "size": 0.085,
      "uppercase": true,
      "text_color": "#FFFFFF",
      "highlight_color": "#FFD400",
      "outline_color": "#000000",
      "outline_width": 0.08,
      "default_animation": "scale_pop"
    }}
  }},
  "pages": [
    {{
      "word_range": [0, 1],
      "preset": "hormozi",
      "position": "center",
      "words": [
        {{ "i": 0, "emphasis": "high", "animation": "kinetic_bounce" }},
        {{ "i": 1, "emphasis": "high", "animation": "kinetic_bounce", "hold_highlight": true }}
      ]
    }},
    {{ "word_range": [2, 4], "preset": "hormozi" }}
  ]
}}
```
👉 Hãy xuất ra bản JSON hoàn chỉnh phủ hết {len(words)} từ trên!"""
```

### 3.2. Thành phần Hardcoded vs. Generated
- **Hardcoded:**
  - Bản đồ mô tả 38 meme (`meme_desc_map` tại `src/core/story_copilot.py#L109-L147`).
  - Các cấu trúc câu lệnh nguyên tắc biên tập và schema JSON mẫu.
  - Danh sách presets mặc định (`hormozi`, `climax`).
- **Generated (Động):**
  - Quét thư mục vật lý `assets/broll_memes` và `assets/sfx` để lọc ra các tệp `.mp4`, `.mov`, `.wav` có mặt trên đĩa (`src/core/story_copilot.py#L149-L167`).
  - Bảng tổng kết Metadata của tất cả các clip (`| ID | Tên Clip | ... |`) sinh tại `src/core/story_copilot.py#L239-L270`.
  - Chi tiết từng câu thoại và mốc thời gian (cắt tối đa 12 câu đại diện/clip để chống tràn token) sinh tại `src/core/story_copilot.py#L278-L303`.
  - Danh sách 30 điểm beat drop âm nhạc đầu tiên sinh tại `src/core/story_copilot.py#L224-L230`.

### 3.3. Tuần tự hóa bảng Clip và Danh sách từ
- **Tuần tự hóa bảng Clip:** Chuyển từng `clip` thành một hàng Markdown bảng:  
  `| {idx+1} | {c_name} | {c_chap} | {dur:.1f}s | {type_label} | {speech_range} | {peak:.1f}s | {motion} | {text_snippet} |`
- **Tuần tự hóa mốc câu thoại:** Duyệt qua `dialogue_intervals` lấy tối đa 12 câu, định dạng:  
  `* [{_fmt_tc(s_st)} - {_fmt_tc(s_en)}] "{s_txt}"`
- **Tuần tự hóa từ ngữ Kinetic:**  
  Chuỗi ghép nối: `f"[{i}] {w['word']} ({w['start']:.2f}s - {w['end']:.2f}s)"`

### 3.4. Ước tính kích thước Token cho dữ liệu thực tế (67 clips, ~15.000 từ)
- **Prompt V1 (Kịch bản Video Plan):**
  - Khung hướng dẫn + Thư viện Meme & SFX: $\approx 1.800$ tokens.
  - Bảng tổng hợp 67 clip ($67 \times 45$ tokens): $\approx 3.000$ tokens.
  - Chi tiết câu thoại (giới hạn 12 câu/clip, $\approx 67 \times 12 \times 15$ tokens): $\approx 12.000$ tokens.
  - Schema mẫu và chỉ dẫn: $\approx 800$ tokens.
  - $\rightarrow$ **Tổng kích thước Prompt V1:** $\approx \mathbf{17.600}$ **tokens** (Nằm an toàn trong context window 32k - 128k của Claude/GPT-4o/Gemini).
- **Prompt V2 (Nếu đưa thô 15.000 từ):**
  - $15.000 \text{ từ} \times 3.5 \text{ tokens/từ}$ (kèm timestamp) $\approx 52.000$ input tokens. Khi xuất ra, 15.000 từ cần $\approx 6.000$ trang JSON, đòi hỏi $> 100.000$ output tokens (vượt quá giới hạn output 4.096 hoặc 8.192 tokens của LLM hiện nay).
  - **Cơ chế tối ưu hóa hiện tại:** `get_final_cut_words` (`src/ui/director/pages/review_page.py#L1145-L1215`) chỉ trích xuất các từ nằm trong các đoạn cắt cuối của Video Plan ($\approx 300 - 800$ từ $\approx 1.200$ tokens). Nếu vẫn $> 300$ từ, hệ thống chủ động chuyển sang chế độ Preset Consulting $\approx 400$ tokens và giao việc ngắt trang cho offline engine `auto_plan` (`src/core/kinetic/kinetic_engine.py#L99-L228`).

---

## 4. LLM Invocation (Thực thi suy luận LLM)

### 4.1. Nhà cung cấp, Tên Model, Endpoint & Thư viện
Triển khai độc lập qua thư viện chuẩn `urllib.request` (không bắt buộc cài OpenAI/Anthropic/Google SDK nặng nề) tại `src/core/story_copilot.py#L558-L690`:

| Nhà cung cấp | Model mặc định | Endpoint API | Giao thức |
| :--- | :--- | :--- | :--- |
| **Google Gemini** | `gemini-3.5-flash` *(fallback: `gemini-3-flash-preview`)* | `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` | OpenAI-compatible REST |
| **DeepSeek** | `deepseek-chat` | `https://api.deepseek.com/v1/chat/completions` | OpenAI-compatible REST |
| **OpenAI** | `gpt-4o` | `https://api.openai.com/v1/chat/completions` | OpenAI-compatible REST |
| **Anthropic Claude** | `claude-3-7-sonnet-20250219` | `https://api.anthropic.com/v1/messages` | Anthropic Native REST |
| **Local Ollama** | `qwen2.5:7b-instruct` | `http://localhost:11434/api/generate` | Ollama Native REST |

### 4.2. Tham số suy luận
- **JSON Mode / Structure Output:**
  - Đối với Gemini, DeepSeek, OpenAI: Cấu hình `response_format={"type": "json_object"}` (`src/core/story_copilot.py#L650`).
  - Đối với Ollama: Cấu hình `"format": "json"`, `"stream": False` (`src/core/story_copilot.py#L573-L574`).
  - Đối với Anthropic Claude: Không có cờ `response_format`, bắt buộc dựa vào chỉ dẫn trong Prompt và kiểm soát đầu ra.
- **Vai trò tin nhắn (Roles):**
  - OpenAI / Gemini / DeepSeek:
    - `system`: `"You are a professional video director and editor. Respond with a valid JSON plan."`
    - `user`: Toàn bộ nội dung Prompt sinh ra.
  - Claude: Chỉ truyền mảng `messages: [{"role": "user", "content": prompt}]` và Header `anthropic-version: "2023-06-01"`.
- **Giới hạn Token & Nhiệt độ (Temperature):**
  - Claude được đặt cứng `max_tokens: 4096` (`src/core/story_copilot.py#L639`).
  - Đối với OpenAI / Gemini / DeepSeek: Không khai báo `temperature` và `max_tokens` trong payload (sử dụng giá trị mặc định của nhà cung cấp, thường là temperature 1.0).

### 4.3. Cơ chế gọi tự động vs. Copy-Paste thủ công
Hệ thống hỗ trợ song song cả hai luồng:
1. **Gọi tự động trực tiếp:** Người dùng bấm nút "Gọi AI Trực Tiếp" trên UI, kích hoạt `run_cloud_api_inference` (`src/core/story_copilot.py#L594`) hoặc `run_ollama_inference` (`src/core/story_copilot.py#L559`).
2. **Copy-Paste thủ công từ Web Chat:** Người dùng bấm "Copy Prompt", dán vào ChatGPT / Claude Web UI, sau đó copy phản hồi dán vào ô nhập liệu. Hệ thống sẽ bóc tách qua `parse_web_prompt_json` (`src/ui/director/director_state.py#L241-L374`).

### 4.4. Phân đoạn đầu vào (Chunking/Splitting)
- Đối với Prompt V1 (Dựng video): **KHÔNG CÓ chunking**. Toàn bộ 67 clip được tuần tự hóa trong 1 prompt duy nhất (nhờ kỹ thuật giới hạn 12 câu thoại/clip).
- Đối với Prompt V2 (Kinetic Typography): Hệ thống chia nhánh theo điều kiện `len(words) > 300`: Nếu vượt quá 300 từ, prompt bị cắt ngắn thành 40 từ mẫu và hướng dẫn người dùng sử dụng engine ngoại tuyến.

---

## 5. Response Handling (Xử lý & Chuẩn hóa phản hồi)

### 5.1. Mã nguồn trích xuất JSON
Trích dẫn nguyên văn thuật toán làm sạch và trích xuất JSON tại `StoryCopilot.parse_copilot_response` (`src/core/story_copilot.py#L387-L457`):

```python
    @classmethod
    def parse_copilot_response(cls, response_text: str) -> CopilotDirectorPlan:
        """
        Bóc tách và xác thực chuỗi JSON do Claude/ChatGPT/Gemini trả về.
        """
        if not response_text or not isinstance(response_text, str):
            raise ValueError("Nội dung phản hồi từ AI trống.")

        clean = response_text.strip()
        
        # Chuẩn hóa các ký tự Unicode vô hình, dấu cách không ngắt dòng và BOM
        clean = (
            clean.replace("\xa0", " ")
            .replace("\u202f", " ")
            .replace("\u2007", " ")
            .replace("\u3000", " ")
            .replace("\u200b", "")
            .replace("\ufeff", "")
            .replace("\u200c", "")
            .replace("\u200d", "")
        )

        # 1. Trích xuất khối ```json ... ``` nếu có
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # Tìm cặp dấu ngoặc nhọn đầu tiên và cuối cùng
            start_brace = clean.find("{")
            end_brace = clean.rfind("}")
            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                json_str = clean[start_brace:end_brace + 1]
            else:
                json_str = clean

        # Thay thế ngoặc kép và ngoặc đơn thông minh (smart/curly quotes)
        json_str = re.sub(r'[\u201c\u201d\u201e\u201f\xab\xbb]', '"', json_str)
        json_str = re.sub(r'[\u2018\u2019\u201a\u201b]', "'", json_str)

        # Loại bỏ các comment thường gặp trong JSON sinh bởi LLM (// hoặc /* */)
        json_str = re.sub(r'(?<!:)\/\/(?![\/\\])[^\r\n]*', '', json_str)
        json_str = re.sub(r'\/\*[\s\S]*?\*\/', '', json_str)

        # Loại bỏ trailing comma trước } hoặc ] (lỗi cú pháp phổ biến của AI)
        json_str = re.sub(r',\s*([\]}])', r'\1', json_str)

        data = None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            # Fallback 1: Thử ast.literal_eval nếu AI trả về dict dạng Python
            try:
                import ast
                val = ast.literal_eval(json_str)
                if isinstance(val, dict):
                    data = val
            except Exception:
                pass
            
            # Fallback 2: Chuyển đổi keys bọc bằng dấu nháy đơn ' sang "
            if data is None:
                try:
                    fixed = re.sub(r"(?<=[{\s,])\'([a-zA-Z0-9_]+)\'\s*:", r'"\1":', json_str)
                    data = json.loads(fixed)
                except Exception:
                    pass

            if data is None:
                raise ValueError(f"Không thể đọc định dạng JSON từ phản hồi của AI: {e}")

        if not isinstance(data, dict):
            raise ValueError("Dữ liệu JSON không đúng cấu trúc đối tượng (Object).")
```

Ngoài ra, khi người dùng dán vào UI, hàm `_extract_all_json_objects` dùng `json.JSONDecoder().raw_decode` để quét và bóc tách nhiều object JSON độc lập từ cùng một chuỗi text (hỗ trợ trường hợp Claude trả về cả Video Plan V1 và Kinetic Plan V2 trong cùng 1 tin nhắn).

### 5.2. Xác thực Schema (Checked vs. NOT Checked)
- **Những gì ĐƯỢC kiểm tra (Checked):**
  - **Ép kiểu dữ liệu từng phần:**
    - `clip_index` được ép sang số nguyên `int(raw.get("clip_index", 1))` (`src/core/story_copilot.py#L465, L490`).
    - `start_sec`, `end_sec`, `speed`, `duration_sec`, `timeline_sec` được ép sang float (`src/core/story_copilot.py#L472-L473, L498-L501, L516-L518`).
    - Bọc trong `try-except` cho từng phần tử: Nếu 1 đoạn phân đoạn bị lỗi cú pháp, nó sẽ bị bỏ qua thay vì làm sập toàn bộ kịch bản (`src/core/story_copilot.py#L505-L506`).
  - **Kiểm tra ngữ nghĩa Kinetic Plan:** Hàm `validate_plan` kiểm tra:
    - `word_range` phải là mảng 2 số nguyên tăng dần `[b, e]`.
    - Các page phải phủ liên tục từ $0$ đến $N-1$ không được hổng hay trùng lặp.
    - `preset` tham chiếu phải tồn tại trong từ điển presets.
- **Những gì KHÔNG ĐƯỢC kiểm tra (NOT Checked):**
  - **Kiểm tra biên thời lượng clip nguồn tại bước parse:** Không kiểm tra `end_sec` có lớn hơn tổng thời lượng thực tế của clip hay không (việc này bị hoãn lại đến khâu timeline conversion).
  - **Kiểm tra thứ tự logic thời gian (`start_sec < end_sec`):** Parser cho phép nhận cả trường hợp `start_sec >= end_sec`; đoạn sai chỉ bị âm thầm loại bỏ khi gọi `convert_plan_to_resolve_timeline` (`src/core/story_copilot.py#L937`).
  - **Kiểm tra tính hợp lệ của mốc `timeline_sec` cho Meme/SFX:** Parser không đối chiếu xem `timeline_sec` của B-Roll/SFX có nằm ngoài tổng thời lượng video hay không.
  - **Kiểm tra file asset tồn tại:** Parser chấp nhận mọi chuỗi `asset_file` do LLM bịa ra; việc kiểm tra tệp chỉ diễn ra ở bước dựng timeline.

### 5.3. Xử lý lỗi, Retry & Fallback
- **Tự động Fallback Model Gemini:** Khi gọi Google Gemini gặp mã lỗi HTTP 404 Not Found (do model `gemini-3.5-flash` chưa được kích hoạt trên một số tài khoản), hệ thống tự động đổi `payload["model"] = "gemini-3-flash-preview"` và retry ngay lập tức (`src/core/story_copilot.py#L675-L686`).
- **Fallback cú pháp:** Nếu `json.loads` lỗi, thử `ast.literal_eval`, sau đó thử Regex sửa single quotes `\'([a-zA-Z0-9_]+)\'\s*:` thành `"\1":` (`src/core/story_copilot.py#L435-L450`).

---

## 6. Post-processing & Resolve Mapping (Hậu xử lý & Ánh xạ DaVinci Resolve)

### 6.1. Vị trí thực thi đệm 0.3s, Lọc SFX (<10s) và Giới hạn Meme
Tất cả các quy tắc an toàn được áp dụng tập trung tại `StoryCopilot.convert_plan_to_resolve_timeline`:
- **Khoảng đệm an toàn 0.3s:**
  - Áp dụng cho Teaser Hook:  
    `start_val = max(0.0, h.start_sec - 0.3)`  
    `end_val = h.end_sec + 0.3` (`src/core/story_copilot.py#L874-L875`)
  - Áp dụng cho Timeline Segments:  
    `start_val = max(0.0, seg.start_sec - 0.3)`  
    `end_val = seg.end_sec + 0.3` (`src/core/story_copilot.py#L932-L933`)
- **Lọc giãn cách SFX tối thiểu 10s:**  
  `if t_sec - last_sfx_time < 10.0: continue` (`src/core/story_copilot.py#L1028-L1029`).
- **Giới hạn thời lượng Meme/B-Roll:**  
  `dur = max(0.5, broll.duration_sec)` (`src/core/story_copilot.py#L985`).  
  Đồng thời, hàm `get_available_assets_summary` và chỉ dẫn prompt giới hạn toàn bộ video chỉ dùng 2-4 meme.

### 6.2. Cơ chế ánh xạ Index / Timestamps sang Timeline DaVinci Resolve
- **Khớp Clip Nguồn đa tầng (`_find_clip_path`):** (`src/core/story_copilot.py#L692-L799`)
  - *Tầng 1:* Khớp chính xác `clip_name`, stem filename, normalized unicode lowercase.
  - *Tầng 2:* Khớp theo `clip_index` (hỗ trợ số nguyên, chuỗi, hoặc dạng `#13`, `Clip 13`).
  - *Tầng 3:* Trích xuất số ID có trong chuỗi `clip_name`.
  - *Tầng 4:* Fuzzy matching chuỗi con trên danh sách đường dẫn.
  - *Tầng 5:* Fallback vị trí mảng danh sách tệp nguồn hiện có trên đĩa.
- **Tính toán tọa độ Record Timeline (`rec_in`, `rec_out`):**
  - Sử dụng con trỏ tích lũy `cursor = 0.0` (`src/core/story_copilot.py#L826`).
  - Nếu có Hook: Đặt Hook tại `rec_in = 0.0`, `rec_out = dur`. Cập nhật `cursor += dur`.
  - Phân đoạn tiếp theo: `rec_in = cursor`, `rec_out = cursor + rec_dur` (trong đó `rec_dur = dur / seg.speed`). Cập nhật `cursor += rec_dur` (`src/core/story_copilot.py#L947-L980`).
- **Phân bổ Track:**
  - **Video Track 1 / Audio Track 1:** Đoạn thoại chính (`role: story, dialogue, intro, climax`) và Teaser Hook.
  - **Video Track 2:** B-Roll chèn đè hoặc Meme Clip overlay (`src/core/story_copilot.py#L1009`).
  - **Audio Track 3:** Hiệu ứng âm thanh SFX (`src/core/story_copilot.py#L1024`).
- **Ánh xạ Phụ đề theo Tọa độ Toàn cục:**  
  Hàm `add_subtitles_for_range` biến đổi mốc thời gian từ nguồn sang đích:
  $$\text{timeline\_word\_start} = \text{base\_rec} + \frac{\max(\text{word\_start}, \text{src\_start}) - \text{src\_start}}{\text{speed}}$$

### 6.3. Các giả định ngầm có thể bị phá vỡ (Known Fragile Assumptions)
1. **Lệch pha giữa `timeline_sec` của LLM và cursor thực tế:** LLM được yêu cầu sinh `timeline_sec` cho SFX và B-Roll dựa trên ước lượng nhịp truyện. Nhưng sau khi hệ thống tự động thêm đệm $+0.3\text{s}$ và điều chỉnh tốc độ speed (ví dụ 1.5x), tổng thời lượng thực tế của Track 1 bị co dãn. Điểm chèn SFX của LLM có thể rơi lệch khỏi khoảng lặng dự kiến.
2. **Khớp nối 1-based index giữa UI và LLM:** Bảng prompt đánh số ID từ 1 đến $N$. Nếu danh sách clip bị lọc hoặc sắp xếp lại trên UI mà prompt không được sinh lại đồng bộ, `clip_index` của LLM sẽ trỏ sai video clip nguồn.
3. **Giả định `max_dur = 3600.0s` khi probe metadata thất bại:** Tại `src/core/story_copilot.py#L872, L930`, nếu `get_media_metadata` trả về 0, hệ thống gán mặc định thời lượng tối đa là 3600 giây. Nếu LLM sinh `end_sec` vượt xa thời lượng clip thực (ví dụ clip dài 10s nhưng LLM sinh 40s), clip sẽ bị out-of-range trên DaVinci Resolve.

---

## 7. Weaknesses Observed (Điểm yếu quan sát được & Xếp hạng mức độ)

### Mức độ: CAO (High Severity)
1. **Thiếu kiểm soát temperature trong lệnh gọi Cloud API:**
   - *Vị trí:* `src/core/story_copilot.py#L648-L655`.
   - *Chi tiết:* Payload gửi sang OpenAI/Gemini/DeepSeek không đặt tham số `temperature` (sẽ dùng mặc định là 1.0). Ở mức nhiệt độ cao này, LLM rất dễ sinh số ảo giác cho `start_sec` và `end_sec` (ví dụ `start_sec: 15.0`, `end_sec: 95.0` trong khi clip chỉ dài 60s). Cần đặt cứng `temperature=0.1` hoặc `0.2` cho tác vụ trích xuất kịch bản kỹ thuật.
2. **Thiếu cơ chế điều chỉnh động `timeline_sec` của SFX/B-Roll theo phân đoạn:**
   - *Vị trí:* `src/core/story_copilot.py#L984, L1027`.
   - *Chi tiết:* LLM không thể tính toán nhẩm chính xác tổng thời lượng tích lũy của 50 clip sau khi bù đệm 0.3s và nhân hệ số tốc độ speed. Việc yêu cầu LLM đưa ra `timeline_sec` tuyệt đối dẫn đến hiện tượng SFX/Meme bị dồn cục ở đầu video hoặc vượt quá đuôi video. Nên thiết kế schema cho phép B-Roll/SFX neo tương đối theo phân đoạn (ví dụ: `anchor_segment: 3, offset_sec: 1.5`).

### Mức độ: TRUNG BÌNH (Medium Severity)
3. **Cắt cụt thông tin câu thoại ở mốc cố định 12 câu/clip:**
   - *Vị trí:* `src/core/story_copilot.py#L290` (`limit_intervals = intervals[:12]`).
   - *Chi tiết:* Đối với clip dài 5-10 phút có 50 câu thoại, LLM hoàn toàn "mù" thông tin từ câu thứ 13 trở đi (*"(và 38 câu thoại tiếp theo đến 08:30)"*). Nếu diễn biến quan trọng nằm ở nửa sau của clip, LLM không thể chọn đoạn cắt đó vào kịch bản.
4. **Fallback 3600.0s khi Probe độ dài tệp thất bại:**
   - *Vị trí:* `src/core/story_copilot.py#L872, L930`.
   - *Chi tiết:* Nếu FFprobe không đọc được thời lượng tệp (do phân vùng mount exFAT/NTFS hoặc tệp tạm), hệ thống dùng 3600s. Đệm `end_val = seg.end_sec + 0.3` sẽ không bị chặn lại, gây lỗi import Media Out of Range trên DaVinci Resolve.

### Mức độ: THẤP (Low Severity)
5. **Mã nguồn Fallback lồng nhau quá nhiều trong Parser JSON:**
   - *Vị trí:* `src/core/story_copilot.py#L435-L450`.
   - *Chi tiết:* Việc dùng `ast.literal_eval` kết hợp Regex thay thế ngoặc nhọn có thể vô tình làm thay đổi nội dung chuỗi tiếng Việt nếu trong lời thoại có chứa dấu nháy đơn `'` (ví dụ từ viết tắt tiếng Anh hoặc trích dẫn).

---

## 8. Sample Data (Dữ liệu mẫu chuẩn Schema)

### 8.1. Fixture đầu vào tối thiểu hợp lệ (Minimal Valid Input Data)
Cấu trúc danh sách clip đầu vào cung cấp cho `generate_copilot_prompt` (trích xuất từ fixture kiểm thử thực tế tại `tests/ui/test_director_enhancements.py#L104-L139`):

```json
[
  {
    "clip_index": 1,
    "name": "DJI_0001.MP4",
    "chapter": "01_RungChe",
    "duration": 60.0,
    "audio_peak_sec": 12.5,
    "visual_motion": "vừa",
    "is_broll": false,
    "transcript": "Khám phá rừng chè tuyệt đẹp",
    "dialogue_intervals": [
      {
        "start": 10.0,
        "end": 14.0,
        "text": "Khám phá rừng chè tuyệt đẹp",
        "words": [
          {"word": "Khám", "start": 10.0, "end": 10.5, "probability": 0.95},
          {"word": "phá", "start": 10.5, "end": 11.0, "probability": 0.98},
          {"word": "rừng", "start": 11.0, "end": 11.5, "probability": 0.92},
          {"word": "chè", "start": 11.5, "end": 12.0, "probability": 0.97},
          {"word": "tuyệt", "start": 12.0, "end": 12.5, "probability": 0.89},
          {"word": "đẹp", "start": 12.5, "end": 13.0, "probability": 0.99}
        ]
      }
    ]
  },
  {
    "clip_index": 2,
    "name": "DJI_0002.MP4",
    "chapter": "01_RungChe",
    "duration": 45.0,
    "audio_peak_sec": 0.0,
    "visual_motion": "chậm",
    "is_broll": true,
    "transcript": "[CẢNH B-ROLL KHÔNG LỜI]",
    "dialogue_intervals": []
  }
]
```

### 8.2. Fixture đầu ra mong đợi từ LLM (Expected Valid Output JSON)
Khối JSON chuẩn xác thực tương thích 100% với `CopilotDirectorPlan` và `ReviewPage`:

#### A. Video Plan V1 Output:
```json
{
  "strategy_summary": "Mở đầu bằng cảnh đắt giá nhất giới thiệu rừng chè, sau đó chuyển sang nhịp điệu khám phá với cảnh quay toàn cảnh B-roll.",
  "target_platform": "YouTube Vlog",
  "global_hook": {
    "clip_index": 1,
    "clip_name": "DJI_0001.MP4",
    "start_sec": 10.0,
    "end_sec": 14.0,
    "hook_title": "🔥 [TEASER HOOK 4s] KHÁM PHÁ RỪNG CHÈ",
    "reason": "Câu nói mở đầu ấn tượng tạo sự tò mò",
    "punch_in": true
  },
  "timeline_segments": [
    {
      "clip_index": 1,
      "clip_name": "DJI_0001.MP4",
      "chapter_name": "01_Mở đầu",
      "start_sec": 10.0,
      "end_sec": 14.0,
      "action": "keep",
      "speed": 1.0,
      "role": "intro",
      "note": "Lời chào mở đầu giới thiệu điểm đến"
    },
    {
      "clip_index": 2,
      "clip_name": "DJI_0002.MP4",
      "chapter_name": "01_Mở đầu",
      "start_sec": 0.0,
      "end_sec": 5.0,
      "action": "keep",
      "speed": 1.5,
      "role": "broll",
      "note": "Cảnh toàn đồi chè tiếp nối lời chào"
    }
  ],
  "broll_inserts": [
    {
      "timeline_sec": 3.8,
      "duration_sec": 2.0,
      "src_in": 0.0,
      "asset_file": "01_funny_wow_reaction.mp4",
      "asset_type": "meme",
      "description": "Reaction trầm trồ khi nhìn thấy cảnh rừng chè"
    }
  ],
  "sfx_inserts": [
    {
      "timeline_sec": 4.0,
      "duration_sec": 1.0,
      "src_in": 0.0,
      "asset_file": "whoosh.wav",
      "asset_type": "sfx",
      "description": "Tiếng vụt chuyển cảnh sang flycam"
    }
  ],
  "viral_headlines": [
    "Khám Phá Rừng Chè Đẹp Nhất Việt Nam!",
    "Chuyến Đi Bất Ngờ Đến Đồi Chè Bí Ẩn"
  ],
  "call_to_action": "Đăng ký kênh để đón xem tập tiếp theo nhé!",
  "seo_hashtags": ["#Vlog", "#DuLich", "#RungChe"]
}
```

#### B. Kinetic Plan V2 Output (Tương thích `validate_plan`):
```json
{
  "schema_version": "1.0",
  "canvas": {
    "aspect": "16:9"
  },
  "presets": {
    "hormozi": {
      "font": "Montserrat",
      "style": "Black",
      "size": 0.085,
      "uppercase": true,
      "text_color": "#FFFFFF",
      "highlight_color": "#FFD400",
      "outline_color": "#000000",
      "outline_width": 0.08,
      "default_animation": "scale_pop"
    },
    "climax": {
      "font": "Montserrat",
      "style": "Black",
      "size": 0.095,
      "uppercase": true,
      "text_color": "#FF2A85",
      "highlight_color": "#00F0FF",
      "outline_color": "#000000",
      "outline_width": 0.09,
      "default_animation": "kinetic_bounce"
    }
  },
  "pages": [
    {
      "word_range": [0, 1],
      "preset": "hormozi",
      "position": "bottom",
      "words": [
        {"i": 0, "emphasis": "high", "animation": "scale_pop"}
      ]
    },
    {
      "word_range": [2, 3],
      "preset": "hormozi",
      "position": "bottom"
    },
    {
      "word_range": [4, 5],
      "preset": "climax",
      "position": "bottom",
      "words": [
        {"i": 5, "emphasis": "high", "animation": "kinetic_bounce", "hold_highlight": true}
      ]
    }
  ]
}
```
