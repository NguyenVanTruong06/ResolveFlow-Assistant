# UX/UI AUDIT CONTEXT: RESOLVEFLOW-ASSISTANT (CHUNDVC)
> **Mục đích tài liệu:** Báo cáo trích xuất cấu trúc kỹ thuật, widget tree, cơ chế tương tác và design tokens của toàn bộ codebase dành cho Lead Designer thực hiện kiểm toán (audit) và tái thiết kế giao diện UX/UI.  
> **Nguyên tắc biên soạn:** Chỉ đọc từ mã nguồn thực tế (read-only), trích dẫn chính xác `file:dòng`, không suy đoán, các mục không tồn tại trong mã nguồn được ghi rõ "KHÔNG TÌM THẤY".

---

## A. Cấu trúc dự án

### 1. Cây thư mục `src/` (tới cấp file) & Số dòng mỗi file `.py` trong `src/ui/`

```text
src/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── ai_director.py
│   ├── asset_cache.py
│   ├── asset_indexer.py
│   ├── asset_manager.py
│   ├── audio.py
│   ├── audio_beat.py
│   ├── audio_normalizer.py
│   ├── audio_processor.py
│   ├── audit_reporter.py
│   ├── autocut.py
│   ├── broll_sfx.py
│   ├── cache_manager.py
│   ├── cloud_downloader.py
│   ├── connection/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── bridge.py
│   │   ├── direct.py
│   │   └── manual.py
│   ├── contextual_suggester.py
│   ├── edit_policy.py
│   ├── evidence_grabber.py
│   ├── fast_paste.py
│   ├── fcpxml_generator.py
│   ├── ffmpeg_utils.py
│   ├── folder_scanner.py
│   ├── fusion/
│   │   ├── __init__.py
│   │   ├── common.py
│   │   ├── lua.py
│   │   ├── overlays.py
│   │   ├── titles.py
│   │   ├── transitions.py
│   │   └── validate.py
│   ├── geometry_checker.py
│   ├── kinetic/
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── director_text_plan.schema.json
│   │   ├── fusion_templates/
│   │   │   └── RF_KineticTitle.setting
│   │   ├── kinetic_engine.py
│   │   └── sample_page_0004.comp
│   ├── llm_director.py
│   ├── lut_generator.py
│   ├── marketing_engine.py
│   ├── motion_analyzer.py
│   ├── preset_capture.py
│   ├── proxy_manager.py
│   ├── queue.py
│   ├── recipe_manager.py
│   ├── resolve_api.py
│   ├── resolve_bridge.py
│   ├── review_state.py
│   ├── scene_activity.py
│   ├── sfx_generator.py
│   ├── shotbook_orchestrator.py
│   ├── smart_cutout.py
│   ├── source_resolver.py
│   ├── starter_pack.py
│   ├── starter_pack_downloader.py
│   ├── story_arranger.py
│   ├── story_copilot.py
│   ├── story_pipeline.py
│   ├── story_planner.py
│   ├── text_preset.py
│   ├── thumbnail_generator.py
│   ├── title_insert_worker.py
│   ├── transcriber.py
│   ├── transcript_quality.py
│   ├── transition_preset.py
│   ├── validator.py
│   ├── vision_reframer.py
│   ├── vlog_hook.py
│   └── xml_exporter.py
├── main.py
├── resolve_scripts/
│   └── ChunDVC_Bridge.py
├── ui/
│   ├── __init__.py
│   ├── app.py
│   ├── auto/
│   │   └── auto_window.py
│   ├── bubble/
│   │   └── floating_bubble.py
│   ├── icons.py
│   ├── lux/
│   │   ├── __init__.py
│   │   ├── drag_drop_tracker.py
│   │   ├── lux_effects.py
│   │   ├── macro_card.py
│   │   ├── panel_edge_rail.py
│   │   └── window_anchor.py
│   ├── resolveflow_ui.html
│   ├── studio/
│   │   └── studio_window.py
│   ├── tabs/
│   │   ├── __init__.py
│   │   ├── tab_assets.py
│   │   ├── tab_autocut.py
│   │   ├── tab_copilot.py
│   │   ├── tab_export.py
│   │   └── tab_sfx.py
│   ├── theme.py
│   └── widgets/
│       ├── copilot_dialog.py
│       ├── drive_dialog.py
│       ├── mini_timeline.py
│       ├── section_card.py
│       └── step_progress_widget.py
└── utils/
    ├── __init__.py
    └── path_utils.py
```

#### Số dòng mỗi file `.py` trong `src/ui/`:
| File | Đường dẫn | Số dòng |
| :--- | :--- | :--- |
| `__init__.py` | `src/ui/__init__.py` | 0 |
| `app.py` | `src/ui/app.py` | 4,585 |
| `auto_window.py` | `src/ui/auto/auto_window.py` | 6,368 |
| `floating_bubble.py` | `src/ui/bubble/floating_bubble.py` | 713 |
| `icons.py` | `src/ui/icons.py` | 181 |
| `__init__.py` | `src/ui/lux/__init__.py` | 21 |
| `drag_drop_tracker.py` | `src/ui/lux/drag_drop_tracker.py` | 28 |
| `lux_effects.py` | `src/ui/lux/lux_effects.py` | 344 |
| `macro_card.py` | `src/ui/lux/macro_card.py` | 471 |
| `panel_edge_rail.py` | `src/ui/lux/panel_edge_rail.py` | 282 |
| `window_anchor.py` | `src/ui/lux/window_anchor.py` | 190 |
| `studio_window.py` | `src/ui/studio/studio_window.py` | 324 |
| `__init__.py` | `src/ui/tabs/__init__.py` | 11 |
| `tab_assets.py` | `src/ui/tabs/tab_assets.py` | 6,664 |
| `tab_autocut.py` | `src/ui/tabs/tab_autocut.py` | 436 |
| `tab_copilot.py` | `src/ui/tabs/tab_copilot.py` | 603 |
| `tab_export.py` | `src/ui/tabs/tab_export.py` | 492 |
| `tab_sfx.py` | `src/ui/tabs/tab_sfx.py` | 265 |
| `theme.py` | `src/ui/theme.py` | 673 |
| `copilot_dialog.py` | `src/ui/widgets/copilot_dialog.py` | 306 |
| `drive_dialog.py` | `src/ui/widgets/drive_dialog.py` | 252 |
| `mini_timeline.py` | `src/ui/widgets/mini_timeline.py` | 458 |
| `section_card.py` | `src/ui/widgets/section_card.py` | 57 |
| `step_progress_widget.py` | `src/ui/widgets/step_progress_widget.py` | 82 |
| **Tổng cộng UI code** | **24 files** | **23,425 dòng** |

---

### 2. Entry point & Cửa sổ chính

- **Entry point:** Hàm `start_app()` trong `src/main.py:14-73`.
```python
# src/main.py:14-26
def start_app():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False) # Đóng cửa sổ con không thoát app, chỉ thoát khi chuột phải vào bubble
    
    # 1. Khởi tạo Bubble (Trung tâm điều hướng - Singleton)
    bubble = FloatingBubbleWidget()
    
    # 2. Khởi tạo 2 Cửa sổ (Singleton)
    auto_win = AutoWindow()
    auto_win.bubble = bubble # Cho phép AutoWindow gọi update_progress trên Bubble
    
    studio_win = StudioWindow()
```
- **Điểm chạm hiển thị đầu tiên:** `FloatingBubbleWidget` (`src/ui/bubble/floating_bubble.py:279`). Người dùng click vào quả cầu để mở `AutoWindow` hoặc `StudioWindow`.
- **Cửa sổ làm việc chính:**
  1. `AutoWindow` (`src/ui/auto/auto_window.py:1771`):
     - Kế thừa: `QMainWindow`.
     - Window flags: Mặc định của hệ điều hành (không gán cờ frameless).
     - Thuộc tính kích thước: `resize(1280, 880)`, `setMinimumSize(980, 640)`.
  2. `StudioWindow` (`src/ui/studio/studio_window.py:163`):
     - Kế thừa: `QMainWindow`.
     - Window flags: Mặc định, có toggle động cờ `Qt.WindowStaysOnTopHint` qua nút Ghim (`studio_window.py:318`).
     - Thuộc tính kích thước: `resize(1140, 740)`, `setMinimumSize(980, 620)`.
  3. `FloatingBubbleWidget` (`src/ui/bubble/floating_bubble.py:296`):
     - Kế thừa: `QWidget`.
     - Window flags: `Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow`.
     - Thuộc tính widget: `WA_TranslucentBackground = True`, `WA_ShowWithoutActivating = True`.
  4. `BubbleTrayPopup` (`src/ui/bubble/floating_bubble.py:44`):
     - Kế thừa: `QWidget`.
     - Window flags: `Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow`.
  5. `EdgeDrawerWindow` (`src/ui/lux/panel_edge_rail.py:38-42`):
     - Kế thừa: `QWidget`.
     - Window flags: `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool`.

---

### 3. Dependencies & Versions (`requirements.txt`)

Tệp `pyproject.toml`: **KHÔNG TÌM THẤY** (dự án quản lý qua `requirements.txt`).

Nội dung chính xác từ `requirements.txt:1-8`:
```text
faster-whisper==1.2.1
ffmpeg-python==0.2.0
numpy>=1.24.0,<3.0.0
pydantic>=2.0.0,<3.0.0
PySide6>=6.5.0
pillow>=10.0.0
pytest>=7.0.0
gdown>=5.0.0
```
- `opencv` (`cv2`): **KHÔNG TÌM THẤY** trong `requirements.txt` (được import tuỳ chọn có `try/except` trong `src/core/motion_analyzer.py:125`).
- `pynput` / `keyboard`: **KHÔNG TÌM THẤY**.

---

## B. Widget tree từng màn hình

### 1. TabCopilot
- **Class / File / Layout gốc:** `TabCopilot` tại `src/ui/tabs/tab_copilot.py:16`. Layout gốc: `QVBoxLayout` chứa `QScrollArea`, bên trong là container `QVBoxLayout` (`tab_copilot.py:38-50`).
- **Cây widget con:**
  - `QScrollArea` (no frame, HBar: Off)
    - `container` (`QWidget`, spacing 12)
      - `Card 1` (`SectionCard`: "🎯 1. CHỌN ĐỊNH DẠNG VIDEO & PHONG CÁCH DỰNG", accent `#8B5CF6`)
        - `QFormLayout`:
          - `combo_story_format` (`QComboBox`)
          - `combo_target_duration` (`QComboBox`)
          - `combo_hook_duration` (`QComboBox`)
      - `Card 2` (`SectionCard`: "🧠 2. CHỌN ĐỘNG CƠ ĐẠO DIỄN AI", accent `#38BDF8`)
        - `QHBoxLayout` chứa 3 radio button: `radio_free_prompt`, `radio_local_ollama`, `radio_cloud_api`
        - `stack_engine` (`QStackedWidget`):
          - Page 0 (Free Web): `lbl_free_hint`, `btn_quick_copy`, `btn_copy_kinetic_prompt`, `btn_open_copilot`, `btn_one_click_kinetic`
          - Page 1 (Local Ollama): `txt_ollama_endpoint` (`QLineEdit`), `combo_ollama_model` (`QComboBox`), `btn_run_ollama`
          - Page 2 (Cloud API): `combo_cloud_provider` (`QComboBox`), `txt_cloud_key` (`QLineEdit` password), `btn_run_api`
      - `Card 3` (`SectionCard`: "🎵 3. CHỌN NHẠC NỀN & AUTO BEAT-SYNC", accent `#06B6D4`)
        - `QFormLayout`:
          - `combo_bgm_mode` (`QComboBox`)
          - `btn_browse_bgm` (`QPushButton`), `btn_preview_bgm` (`QPushButton`)
          - `chk_beat_sync` (`QCheckBox`)
          - `lbl_bgm_file_info` (`QLabel`)
      - `Card 4` (`SectionCard`: "📄 4. KHUNG DÁN KỊCH BẢN JSON ➔ XUẤT TIMELINE", accent `#10B981`)
        - `txt_json_input` (`QPlainTextEdit`, min-height: 160px)
        - `QHBoxLayout`: `btn_load_file` (`QPushButton`), `btn_apply` (`QPushButton`)
- **Danh sách controls & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | `combo_story_format` | QComboBox | Phong cách Video | N/A (đọc khi tạo prompt) |
  | `combo_target_duration`| QComboBox | Thời lượng mục tiêu | N/A (đọc khi tạo prompt) |
  | `combo_hook_duration` | QComboBox | Đoạn Hook Mở Đầu | N/A (đọc khi tạo prompt) |
  | `radio_free_prompt` | QRadioButton | 📋 Miễn Phí Web (0đ) | `toggled -> stack.setCurrentIndex(0)` |
  | `radio_local_ollama` | QRadioButton | 💻 AI Local (GPU) | `toggled -> stack.setCurrentIndex(1)` |
  | `radio_cloud_api` | QRadioButton | ⚡ Cloud API (Key) | `toggled -> stack.setCurrentIndex(2)` |
  | `btn_quick_copy` | QPushButton | 📋 1. Prompt Dựng Video | `quick_copy_prompt_requested.emit` |
  | `btn_copy_kinetic_prompt` | QPushButton | ✨ 2. Prompt Chữ Kinetic | `copy_kinetic_prompt_requested.emit` |
  | `btn_open_copilot` | QPushButton | 🧠 Tone... | `open_copilot_dialog_requested.emit` |
  | `btn_one_click_kinetic`| QPushButton | 🚀 1-CLICK: TỰ PHỦ PHỤ ĐỀ KINETIC... | `one_click_kinetic_requested.emit` |
  | `txt_ollama_endpoint` | QLineEdit | Địa chỉ Ollama | N/A |
  | `combo_ollama_model` | QComboBox | Model Local | N/A |
  | `btn_run_ollama` | QPushButton | 🚀 1-CLICK: AI LOCAL TỰ ĐỘNG LÊN KỊCH BẢN... | `_on_run_ollama_clicked` |
  | `combo_cloud_provider`| QComboBox | Nhà cung cấp | N/A |
  | `txt_cloud_key` | QLineEdit | API Key | `_on_cloud_key_changed` |
  | `btn_run_api` | QPushButton | 🚀 1-CLICK: CLOUD AI TỰ ĐỘNG LÊN KỊCH BẢN... | `_on_run_api_clicked` |
  | `combo_bgm_mode` | QComboBox | Nhạc nền (BGM) | `_on_bgm_mode_changed` |
  | `btn_browse_bgm` | QPushButton | 📁 Chọn tệp... | `browse_custom_bgm_file` |
  | `btn_preview_bgm` | QPushButton | ▶ Nghe Thử | `_toggle_bgm_preview` |
  | `chk_beat_sync` | QCheckBox | ⚡ Tự động Beat-Sync | N/A |
  | `txt_json_input` | QPlainTextEdit | JSON Input | N/A |
  | `btn_load_file` | QPushButton | 📂 Nạp File .JSON | `browse_json_requested.emit` |
  | `btn_apply` | QPushButton | ⚡ THI CÔNG TIMELINE SANG DAVINCI (0.1s) | `_on_apply_clicked` |
- **Luồng thao tác chính:** Chọn Format/Duration/Hook -> Chọn Động cơ AI (hoặc bấm Copy Prompt sang Claude/ChatGPT) -> Dán JSON vào `txt_json_input` -> Bấm `btn_apply` (`⚡ THI CÔNG TIMELINE SANG DAVINCI`) -> Chuyển sang `AutoWindow._apply_json_text_plan` -> Tạo FCPXML 1.9 & FCP7 XML -> Gọi DaVinci API nạp timeline.
- **Hàm core gọi:** `StoryCopilot.generate_copilot_prompt`, `convert_plan_to_resolve_timeline`, `AudioBeatDetector.detect_beats`, `ResolveAutomation.import_fcpxml_timeline`.
- **Trùng lặp:** Bộ chọn BGM và Beat-Sync trong Card 3 trùng với BGM scanner trong `TabAssets` (Rail `bgm`).

---

### 2. TabAutoCut
- **Class / File / Layout gốc:** `TabAutoCut` tại `src/ui/tabs/tab_autocut.py:24`. Layout gốc: `QVBoxLayout` chứa `QScrollArea`, bên trong là `content_widget` có `QVBoxLayout` (`tab_autocut.py:36-46`).
- **Cây widget con:**
  - `banner_onboarding` (`QFrame`: hướng dẫn bắt đầu nhanh)
  - `group_wf` (`SectionCard`: "🎯 CHẾ ĐỘ DỰNG TỰ ĐỘNG", accent `PRIMARY`): `combo_workflow`
  - `group_recipe` (`SectionCard`: "📋 QUẢN LÝ RECIPE", accent `WARNING`): `combo_recipes`, `btn_save_recipe`, `btn_delete_recipe`
  - `group_master` (`SectionCard`: "🎛️ BỘ ĐIỀU KHIỂN TỔNG", accent `SUCCESS`):
    - `lbl_master_intensity`, `slide_master_intensity` (`QSlider` 1..3)
    - `combo_video_type` (`QComboBox`)
    - `combo_pacing` (`QComboBox`)
  - `group_story` (`SectionCard`: "🧭 TẦNG 2: MẠCH KỊCH BẢN & SẮP XẾP Ý ĐỒ", accent `WARNING`):
    - `combo_story_intent`, `combo_story_target`, `txt_api_key`
  - `group_timeline` (`SectionCard`: "⏱️ XEM TRƯỚC TIMELINE THU NHỎ"): `mini_timeline` (`MiniTimelineWidget`)
  - `btn_toggle_advanced` (`QPushButton`: ẩn/hiện `advanced_container`)
  - `advanced_container` (`QWidget`, chứa chi tiết 3 tầng):
    - Tầng 1.1: `group_cut` (`SectionCard`): `check_cut`, `check_scene_guard`, `check_speedup`, `combo_audio_track`, `slide_db` (-60..-10), `slide_dur` (2..50)
    - Tầng 1.2: `group_director` (`SectionCard`): `combo_ai_mode`, `check_bad_takes`, `check_repeats`, `txt_confidence_threshold`, `check_hide_weak_subs`
    - Tầng 2.2: `group_vlog_hook` (`SectionCard`): `check_vlog_hook`, `combo_hook_dur`, `combo_hook_total`
    - Tầng 3: `group_v4` (`SectionCard`): `check_punch_in`, `check_reframe`, `check_broll`, `check_sfx`, `check_evidence`, `check_geometry`
  - `btn_run_autocut` (`QPushButton`: "✂ 🚀 XUẤT TIMELINE CẮT THÔ (OFFLINE)")
- **Danh sách controls & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | `combo_workflow` | QComboBox | Mục tiêu dựng | N/A |
  | `combo_recipes` | QComboBox | Danh sách Recipe | N/A |
  | `btn_save_recipe` | QPushButton | 💾 Lưu Recipe... | N/A |
  | `btn_delete_recipe` | QPushButton | 🗑 Xóa | N/A |
  | `slide_master_intensity` | QSlider | Mức độ cắt vấp & im lặng | N/A |
  | `combo_video_type` | QComboBox | Kiểu video | N/A |
  | `combo_pacing` | QComboBox | Nhịp dựng | N/A |
  | `combo_story_intent` | QComboBox | Ý đồ kịch bản toàn video | N/A |
  | `combo_story_target` | QComboBox | Thời lượng Shorts mục tiêu | N/A |
  | `txt_api_key` | QLineEdit | API Key | N/A |
  | `btn_toggle_advanced` | QPushButton | ⚙ Tùy chỉnh nâng cao ▾ | Toggle `advanced_container.setVisible` |
  | `check_cut` | QCheckBox | Kích hoạt xử lý khoảng lặng | N/A |
  | `check_scene_guard` | QCheckBox | 🎞️ Bảo vệ cảnh quay | N/A |
  | `check_speedup` | QCheckBox | ⚡ Tua nhanh khoảng lặng | N/A |
  | `combo_audio_track` | QComboBox | Track Audio phân tích | N/A |
  | `slide_db` | QSlider | Ngưỡng im lặng (dB) | `valueChanged -> lbl_db.setText` |
  | `slide_dur` | QSlider | Thời lượng tối thiểu | `valueChanged -> lbl_dur.setText` |
  | `combo_ai_mode` | QComboBox | Chế độ phân tích thoại | N/A |
  | `check_bad_takes` | QCheckBox | 🚫 Tự động phát hiện nói vấp | N/A |
  | `check_repeats` | QCheckBox | 🔁 Loại câu nói lặp lại | N/A |
  | `txt_confidence_threshold`| QLineEdit | Ngưỡng tin cậy | N/A |
  | `check_hide_weak_subs` | QCheckBox | Ẩn phụ đề khi không chắc | N/A |
  | `check_vlog_hook` | QCheckBox | Tự động tạo Teaser | N/A |
  | `combo_hook_dur` | QComboBox | Độ dài mỗi khoảnh khắc hook | N/A |
  | `combo_hook_total` | QComboBox | Tổng thời lượng Teaser | N/A |
  | `check_punch_in` | QCheckBox | 🔍 Hiệu ứng Auto Punch-in | N/A |
  | `check_reframe` | QCheckBox | 📱 Auto Re-framing | N/A |
  | `check_broll` | QCheckBox | 🎬 Chèn B-Roll / Meme | N/A |
  | `check_sfx` | QCheckBox | 🔊 Chèn âm thanh SFX | N/A |
  | `check_evidence` | QCheckBox | 🌐 Săn ảnh Web B-Roll | N/A |
  | `check_geometry` | QCheckBox | 🛡️ Khử xung đột bố cục | N/A |
  | `btn_run_autocut` | QPushButton | ✂ 🚀 XUẤT TIMELINE CẮT THÔ | `run_autocut_requested.emit` |
- **Luồng thao tác chính:** Chọn Workflow/Recipe -> Cấu hình ngưỡng im lặng/Bad takes -> Bấm `btn_run_autocut` -> `PipelineWorker` chạy tách audio, Whisper STT, `SilenceDetector`, `AIDirector`, `VlogHookGenerator` -> Trả về `ReviewState` hiển thị bảng duyệt câu thoại -> Bấm xuất timeline XML -> Import DaVinci Resolve.
- **Hàm core gọi:** `autocut.SilenceDetector`, `ai_director.AIDirector`, `vlog_hook.VlogHookGenerator`, `story_arranger.StoryArranger`, `review_state.ReviewState`.
- **Trùng lặp:** Checkbox B-roll và SFX trong Tầng 3 trùng với Kho Đạo Cụ (`TabAssets`) và `TabSFX`.

---

### 3. TabAssets
- **Class / File / Layout gốc:** `TabAssets` tại `src/ui/tabs/tab_assets.py:3177`. Layout gốc: `QHBoxLayout` gồm `self.rail` và `self.assets_splitter` (`QSplitter` ngang) (`tab_assets.py:3297, 4541-4554`).
- **Cây widget con (Kiến trúc 4 Cột):**
  - **Cột 1: Rail điều hướng chính** (`self.rail`, `QFrame`, fixed width: 78px)
    - 9 nút danh mục dọc: `text` (🔤 Chữ), `lut` (🎨 Màu), `trans` (🎬 Chuyển cảnh), `icon` (🏷️ Sticker), `overlay` (🎞️ Lớp phủ), `meme` (🎭 Meme), `sfx` (🔊 Âm thanh), `bgm` (🎵 Nhạc nền), `fav` (❤️ Mẫu của tôi).
  - **Cột 2: Danh mục con** (`self.cats`, `QFrame`, min width: 160px)
    - `lbl_cats_head` (`QLabel`)
    - `scroll_cats` (`QScrollArea`) chứa `cats_container` (`QVBoxLayout`)
    - `btn_starter_pack` (`QPushButton`: "📦 Nạp Gói Starter Pack")
    - `btn_scan_local` (`QPushButton`: "➕ Quét thư mục cá nhân")
  - **Cột 3: Main Section & Grid** (`self.main_section`, `QWidget`, min width: 300px)
    - `main_head` (`QFrame`, fixed height 52px):
      - `btn_toggle_cats` ("☰ Nhóm"), `lbl_main_cat`, `lbl_count_badge`, `btn_resolve_status`, `btn_rescan`, `btn_import_assets`, `txt_search` (width 220px), `btn_toggle_inspector` ("◫ Chi tiết")
    - `filter_bar` (`QFrame`, fixed height 40px):
      - 3 nút lọc: `btn_filter_all`, `btn_filter_fav`, `btn_filter_recent`
      - Phóng to/Thu nhỏ lưới: `btn_size_compact` (5 cột), `btn_size_medium` (3 cột), `btn_size_large` (2 cột)
      - `slide_card_cols` (`QSlider` 2..5), `lbl_cols_val`
      - `chk_hover_preview` (`QCheckBox`: "🔊 Rê chuột nghe thử")
    - `suggestion_bar` (`QFrame`: thanh đạo cụ gợi ý thông minh từ transcript)
    - `scroll_grid` (`QScrollArea`) -> `grid_container` -> `grid` (`QGridLayout` chứa hàng trăm `AssetCard`)
  - **Cột 4: Bảng tinh chỉnh Inspector** (`self.inspector`, `QFrame`, min width: 260px)
    - `scroll_insp` (`QScrollArea`) -> `insp_content`:
      - Header: `lbl_live_title`, `inspector_aspect_btn` ("📐 16:9")
      - Viewports đa năng: `inspector_text_preview` (Live Canvas), `inspector_lut_viewport`, `inspector_sfx_viewport`, `inspector_trans_viewport`, `inspector_icon_viewport`
      - Thông tin: `lbl_insp_title`, `lbl_insp_sub`
      - Kéo thả master: `preview_lbl` (`DraggableAssetLabel`)
      - Bộ điều khiển: `txt_single_title`, `slide_title_dur` (1.0s..10.0s)
      - Swatches màu (Chữ, Nổi bật, Viền, Hộp/Glow)
      - Chips Animation (Pop, Bounce, Typewriter...) & Curve (Spring, Ease, Linear)
      - `combo_target_track` (V2, V3, V1)
      - `btn_trans_head`, `btn_trans_tail`
    - Cụm nút hành động (`insp_acts`):
      - `btn_insert_playhead` ("🚀 Chèn Vào Timeline Tại Playhead")
      - Hàng nút phụ: `btn_drag_davinci`, `btn_fast_paste_fusion`, `btn_copy_fusion`, `btn_install_presets`, `btn_capture_playhead`
- **Danh sách controls chính & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | 9 nút rail (`rail_btns`) | QPushButton | text, lut, trans... | `_filter_by_rail` |
  | `btn_starter_pack` | QPushButton | 📦 Nạp Gói Starter Pack | `_on_starter_pack_clicked` |
  | `btn_scan_local` | QPushButton | ➕ Quét thư mục cá nhân | `_scan_local_folder` |
  | `btn_toggle_cats` | QPushButton | ☰ Nhóm | `_toggle_cats_visibility` |
  | `btn_toggle_inspector`| QPushButton | ◫ Chi tiết | `_toggle_inspector_visibility` |
  | `btn_resolve_status` | QPushButton | ⚪ Kiểm tra DaVinci | `_check_resolve_connection` |
  | `btn_rescan` | QPushButton | 🔄 Quét lại kho | `_rescan_assets` |
  | `btn_import_assets` | QPushButton | ➕ Nhập tệp... | `_import_files_or_folders` |
  | `txt_search` | QLineEdit | 🔍 Tìm kiếm đạo cụ... | `_refresh_grid` |
  | `btn_filter_all/fav/recent`| QPushButton | Tất cả, Yêu thích, Gần đây | `_set_filter_mode` |
  | `btn_size_compact/med/lg` | QPushButton | Nhỏ (5), Vừa (3), Lớn (2) | `_on_grid_cols_changed` |
  | `slide_card_cols` | QSlider | Cỡ thẻ (2..5) | `_on_grid_cols_changed` |
  | `chk_hover_preview` | QCheckBox | 🔊 Rê chuột nghe thử | `_on_hover_preview_toggled` |
  | `inspector_aspect_btn`| QPushButton| 📐 16:9 / 9:16 | `_toggle_inspector_aspect_ratio` |
  | `btn_insert_playhead` | QPushButton | 🚀 Chèn Vào Timeline Tại Playhead | `_insert_at_playhead` |
  | `btn_fast_paste_fusion`| QPushButton| ⚡ Dán Fusion | `_on_fast_paste_fusion_clicked` |
  | `btn_copy_fusion` | QPushButton | 📋 Sao Chép | `_copy_fusion_code` |
  | `btn_install_presets` | QPushButton | 📥 Cài Đặt | `_install_to_fusion` |
  | `btn_capture_playhead`| QPushButton | 📥 Hút Preset | `_capture_preset_from_playhead` |
- **Luồng thao tác chính:** Chọn tab ở Rail 1 -> Chọn Category con ở Cột 2 -> Lưới Cột 3 hiển thị các card -> Rê chuột nghe thử SFX hoặc xem canvas animation -> Bấm chọn card -> Cột 4 Inspector cập nhật thông số -> Người dùng bấm "🚀 Chèn Vào Timeline Tại Playhead" (gọi worker/API) hoặc kéo thả trực tiếp sang DaVinci Resolve.
- **Hàm core gọi:** `AssetIndexer.scan_all_assets`, `ContextualSuggester.suggest_for_segment`, `SmartCutout.auto_cutout`, `fast_paste_to_fusion`, `ResolveAutomation.insert_title_at_playhead`, `ResolveAutomation.insert_sfx_to_track`.
- **Trùng lặp:** Chứa cả tab SFX (trùng `TabSFX`) và tab LUT (trùng `TabExport`).

---

### 4. TabSFX
- **Class / File / Layout gốc:** `TabSFX` tại `src/ui/tabs/tab_sfx.py:34`. Layout gốc: `QVBoxLayout` chứa `QScrollArea`, bên trong là `content_widget` có `QVBoxLayout` (`tab_sfx.py:84-94`).
- **Cây widget con:**
  - `banner` (`QFrame`: tiêu đề SFX Soundboard Studio)
  - `group_pad` (`QGroupBox`: "🎹 BẢNG LƯỚI SFX SOUNDBOARD"):
    - `grid_pad` (`QGridLayout`, 3 hàng x 4 cột): chứa 12 `SFXPadButton` (height 68px): Whoosh, Pop, Ding, Click, Camera, Glitch, Riser, Deep Sub, Vine Boom, Censor Beep, Punch, Laser.
  - `group_cfg` (`QGroupBox`: "🎚 THIẾT LẬP ÂM LƯỢNG & TRACK CHÈN"):
    - `lbl_selected_sfx` (`QLabel`)
    - `combo_target_track` (`QComboBox`: Track Audio 2, 3, 4)
    - `slide_volume_offset` (`QSlider`: -30 dB .. 0 dB, default -12 dB), `lbl_volume_offset`
    - `check_auto_sfx` (`QCheckBox`: "Bật AI Auto-SFX")
  - `group_loudnorm` (`QGroupBox`: "🎚 CHUẨN HÓA ÂM LƯỢNG GIỌNG NÓI"):
    - `check_loudnorm` (`QCheckBox`), `combo_loudnorm_preset` (`QComboBox`: YouTube -14 LUFS, Podcast -16 LUFS, EBU R128 -23 LUFS)
  - `group_actions` (`QGroupBox`: "⚡ THAO TÁC TRỰC TIẾP VỚI TIMELINE"):
    - `btn_insert_playhead` (`QPushButton`: "🚀 Chèn SFX Vào Vị Trí Playhead")
    - `btn_open_sfx_folder` (`QPushButton`), `btn_open_broll_folder` (`QPushButton`)
- **Danh sách controls & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | 12 nút `SFXPadButton` | QPushButton | Biểu tượng + Tên SFX | `_on_pad_clicked(sid, fn)` |
  | `combo_target_track` | QComboBox | Track Audio đích | N/A |
  | `slide_volume_offset` | QSlider | Auto Volume Offset | `valueChanged -> lbl_volume_offset.setText` |
  | `check_auto_sfx` | QCheckBox | Bật AI Auto-SFX | N/A |
  | `check_loudnorm` | QCheckBox | Bật chuẩn hóa âm lượng | N/A |
  | `combo_loudnorm_preset`| QComboBox | Chuẩn âm lượng mục tiêu | N/A |
  | `btn_insert_playhead` | QPushButton | 🚀 Chèn SFX Vào Vị Trí Playhead | `_on_insert_playhead_clicked` |
  | `btn_open_sfx_folder` | QPushButton | 📂 Mở Thư Mục SFX | `_open_sfx_folder` |
  | `btn_open_broll_folder`| QPushButton | 🎬 Mở Thư Mục B-Roll & Memes | `_open_broll_folder` |
- **Luồng thao tác chính:** Click vào pad để nghe thử âm thanh -> Chỉnh âm lượng bù trừ và chọn track -> Bấm `btn_insert_playhead` -> Phát signal `insert_sfx_requested` -> `ResolveAutomation.insert_sfx_to_track` chèn file WAV vào đúng playhead DaVinci.
- **Hàm core gọi:** `QSoundEffect` (playback preview), `winsound` / `paplay` (fallback), `ResolveAutomation.insert_sfx_to_track`.
- **Trùng lặp:** 100% chức năng danh mục SFX, phát nghe thử và chèn tại Playhead bị trùng lặp hoàn toàn với tab `sfx` trong `TabAssets`.

---

### 5. TabExport
- **Class / File / Layout gốc:** `TabExport` tại `src/ui/tabs/tab_export.py:193`. Layout gốc: `QVBoxLayout` chứa `QScrollArea`, bên trong là `content_widget` có `QVBoxLayout` (`tab_export.py:212-223`).
- **Cây widget con:**
  - `banner` (`QFrame`: tiêu đề bộ màu điện ảnh)
  - `filter_box` (`QFrame`):
    - `txt_search` (`QLineEdit`: tìm kiếm tone màu)
    - `h_pills` (`QHBoxLayout`): 6 category pills (`all`, `cinematic`, `vlog`, `cyber`, `vintage`, `bw`)
  - `group_color` (`QGroupBox`: "🎨 THƯ VIỆN BỘ MÀU CHUẨN"):
    - `grid_cards` (`QGridLayout` 2 cột chứa các `DraggableLUTCard`)
  - `group_lut_ctrl` (`QGroupBox`: "🎚 ĐIỀU KHIỂN CHUẨN MÀU NHANH"):
    - `combo_lut` (`QComboBox`), `combo_lut_scope` (`QComboBox`: timeline/clips), `check_lut_skip_existing` (`QCheckBox`), `btn_scan_luts` (`QPushButton`), `btn_apply_lut` (`QPushButton`), `btn_open_luts` (`QPushButton`)
  - `group_render` (`QGroupBox`: "🚀 CẤU HÌNH XUẤT BẢN NHANH"):
    - `combo_render_preset` (`QComboBox`: TikTok 9:16, YouTube 1080p, YouTube 4K, Podcast Audio), `txt_custom_render_name` (`QLineEdit`)
  - `group_render_act` (`QGroupBox`: "⚡ KẾT XUẤT DAVINCI RESOLVE"):
    - `btn_start_render` (`QPushButton`: "🚀 Thêm Vào Render Queue & Bắt Đầu Render")
    - `lbl_render_info` (`QLabel`)
- **Danh sách controls & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | `txt_search` | QLineEdit | Tìm phong cách màu... | `_filter_cards` |
  | 6 nút `pill_buttons` | QPushButton | Tất cả, Điện ảnh, Pastel... | `_on_category_pill_clicked` |
  | Thẻ `DraggableLUTCard` | QFrame | Thẻ xem trước LUT 3D | Click chọn & Drag sang Color Page |
  | `combo_lut` | QComboBox | Bộ màu đang chọn | `_on_combo_lut_changed` |
  | `combo_lut_scope` | QComboBox | Phạm vi áp dụng | N/A |
  | `check_lut_skip_existing`| QCheckBox | Bỏ qua nơi đã có LUT | N/A |
  | `btn_scan_luts` | QPushButton | 🔍 Kiểm tra LUT đã gắn | N/A (kết nối ở auto_window) |
  | `btn_apply_lut` | QPushButton | ✨ Áp Dụng Bộ Màu Này Vào Timeline...| `_on_apply_lut_clicked` |
  | `btn_open_luts` | QPushButton | 📂 Mở Thư Mục 10 LUTs... | `_open_luts_folder` |
  | `combo_render_preset` | QComboBox | Mục tiêu xuất bản | N/A |
  | `txt_custom_render_name`| QLineEdit | Tên tệp xuất bản | N/A |
  | `btn_start_render` | QPushButton | 🚀 Thêm Vào Render Queue & Bắt Đầu Render | `_on_start_render_clicked` |
- **Luồng thao tác chính:** Lọc/chọn LUT card -> Bấm áp dụng màu hoặc kéo file `.cube` vào DaVinci Color Page -> Chọn Render preset -> Bấm `btn_start_render` -> Gửi lệnh sang Deliver Page của DaVinci qua API.
- **Hàm core gọi:** `lut_generator.LUT3DGenerator`, `lut_generator.BUILTIN_COLOR_LOOKS`, `ResolveAutomation.scan_existing_luts`, `ResolveAutomation.apply_look_lut`, `ResolveAutomation.set_render_preset_and_queue`.
- **Trùng lặp:** Toàn bộ thư viện LUT và thao tác kéo thả `.cube` bị trùng với tab `lut` trong `TabAssets`.

---

### 6. panel_edge_rail.py (`EdgeDrawerWindow`)
- **Class / File / Layout gốc:** `EdgeDrawerWindow` tại `src/ui/lux/panel_edge_rail.py:24`. Layout gốc: `QVBoxLayout` chứa `self.frame`, bên trong chứa `self.stack` (`QStackedWidget`) (`panel_edge_rail.py:71-86`).
- **Cây widget con:**
  - `self.frame` (`QFrame#lux_drawer_panel`, margin 4, padding 0)
    - `self.stack` (`QStackedWidget`):
      - **Page 0: Collapsed Rail (40px)** (`self.rail_widget`, `QVBoxLayout`, HCenter alignment):
        - `btn_rail_logo` (`QPushButton` "⚡", 30x30px)
        - `btn_rail_expand` (`QPushButton` "«" hoặc "»", 30x30px)
        - `addStretch(1)`
        - 3 nút Quick Peek: `btn_peek_copilot` ("🧠", 30x30px), `btn_peek_cut` ("✂", 30x30px), `btn_peek_titles` ("🗂️", 30x30px)
        - `addStretch(1)`
        - `lbl_status_dot` (`QLabel` "●", xanh lá #10B981, font 14px)
      - **Page 1: Expanded Full View (440px)** (`self.expanded_widget`, `QVBoxLayout`, margin 6, spacing 6):
        - `header_row` (`QHBoxLayout`): `lbl_brand` ("CHUNDVC DOCK"), `badge` ("PRO 4.0"), `btn_restore` ("🪟 Chuẩn"), `btn_collapse` ("»" hoặc "«", 28x26px)
        - `content_container` (`QWidget`, layout `content_layout`) chứa widget trung tâm được nhúng qua `set_central_widget(widget)`
- **Danh sách controls & handler:**
  | Control | Loại | Label | Handler |
  | :--- | :--- | :--- | :--- |
  | `btn_rail_logo` | QPushButton | ⚡ | `self.expand` |
  | `btn_rail_expand` | QPushButton | « / » | `self.expand` |
  | `btn_peek_copilot`| QPushButton | 🧠 | `self.expand` |
  | `btn_peek_cut` | QPushButton | ✂ | `self.expand` |
  | `btn_peek_titles` | QPushButton | 🗂️ | `self.expand` |
  | `btn_restore` | QPushButton | 🪟 Chuẩn | `restore_standard_window_requested.emit` |
  | `btn_collapse` | QPushButton | » / « | `self.collapse` |
- **Luồng thao tác:** Chuột rê vào mép -> Rail thò ra 12px (peek) -> Click vào nút expand -> Mở rộng 440px -> Hiển thị workspace con nhúng bên trong -> Click collapse để thu gọn về 40px.

---

## C. Edge Rail / Drawer

1. **Cách định vị cạnh màn hình:**
   - Sử dụng `QApplication.primaryScreen().availableGeometry()` làm toạ độ dự phòng (`src/ui/lux/panel_edge_rail.py:203-208`).
   - Có tự động dò cửa sổ DaVinci Resolve qua `src/ui/lux/window_anchor.py`:
     - Trên **Linux**: Dùng subprocess gọi lệnh `wmctrl -l -G` hoặc `xdotool search --onlyvisible --name "DaVinci Resolve"` rồi đọc `xdotool getwindowgeometry` (`window_anchor.py:48-87`).
     - Trên **Windows**: Dùng `ctypes.windll.user32.EnumWindows` kết hợp `GetWindowTextW` và `GetWindowRect` tìm window có title chứa `"DaVinci Resolve"` (`window_anchor.py:90-120`).
2. **Window flags:**
   - `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool` (`src/ui/lux/panel_edge_rail.py:39-41`).
   - `X11BypassWindowManagerHint`: **KHÔNG TÌM THẤY** trong file.
3. **Cơ chế hover / peek / slide:**
   - Animation slide mở rộng / thu nhỏ: Dùng `QVariantAnimation(self)`, duration `240ms`, easing `QEasingCurve.Type.OutCubic` (`panel_edge_rail.py:51-55`).
   - Cơ chế Peek reveal: Kích hoạt khi chuột di chuyển vào mép (`enterEvent` -> `_peek_reveal(True)`). Độ dịch chuyển hotspot là `12px` (`PEEK_OFFSET = 12`) (`panel_edge_rail.py:34, 251-265`).
   - Độ rộng: Thu gọn `COLLAPSED_WIDTH = 40px`, Mở rộng `EXPANDED_WIDTH = 440px`.
4. **Khi drawer 440px, TabAssets 4 cột được co lại thế nào?**
   - **KHÔNG CO TỰ ĐỘNG.** Trong `TabAssets`, tổng chiều rộng tối thiểu của 4 cột là **798px** (Rail: 78px, Categories: min 160px, Main Grid: min 300px, Inspector: min 260px). Khi nhúng vào `EdgeDrawerWindow` (440px), giao diện bị chèn ép mạnh hoặc tràn khung trừ khi người dùng tự tay bấm nút ẩn `btn_toggle_cats` ("☰ Nhóm") và `btn_toggle_inspector` ("◫ Chi tiết").
5. **Bug / TODO / FIXME đã biết trong file này:**
   - Từ khoá `TODO`, `FIXME`, `BUG`: **KHÔNG TÌM THẤY**.
   - Vấn đề thực tế trong code: Chưa có listener phát hiện kích thước cửa sổ thu hẹp để tự động kích hoạt `_toggle_cats_visibility()` và `_toggle_inspector_visibility()`.

---

## D. Tích hợp DaVinci Resolve

### 1. Polling `resolve.GetCurrentPage()`
- Có poll liên tục `resolve.GetCurrentPage()` không: **KHÔNG CÓ POLLING LIÊN TỤC.**
- `GetCurrentPage()` chỉ được gọi **đúng lúc kích hoạt thao tác (on-demand)** tại:
  1. `src/core/fast_paste.py:187`: Kiểm tra xem Resolve có đang ở trang Fusion không, nếu không thì gọi `resolve_app.OpenPage("fusion")`.
  2. `src/core/title_insert_worker.py:150`: Kiểm tra xem có đang ở trang Edit không, nếu không thì mở trang Edit.
  3. `src/core/preset_capture.py:209`: Đảm bảo đang ở trang Edit khi hút preset.
  4. `src/core/resolve_api.py:1903`: Đảm bảo trang Edit khi import v2.

### 2. Cách kết nối `DaVinciResolveScript`
- Thiết lập môi trường trong `src/core/resolve_bridge.py:55-114`:
  - **Linux:** Thư viện `libfusionscript.so` tại `/opt/resolve/libs/Fusion/` hoặc `/opt/resolve/libs/`. Được nạp trước bằng `ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)`. Thư mục Module: `/opt/resolve/Developer/Scripting/Modules`.
  - **Windows:** Thư viện `fusionscript.dll` tại `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll`. Thư mục Module: `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules`.
  - **macOS:** Thư viện `fusionscript.dylib` tại `/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/`. Thư mục Module: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules`.
- Xử lý khi Resolve chưa mở:
  - Hàm `connect()` trong `src/core/resolve_api.py:106-140` kiểm tra trước tiến trình (`pgrep -f resolve` trên Linux) và bọc lời gọi trong `concurrent.futures.ThreadPoolExecutor(max_workers=1)` với timeout **2.0 giây**. Nếu quá hạn, trả về `False` và thông báo lỗi qua `self.last_connect_error`, tuyệt đối không làm đóng băng UI.

### 3. `fast_paste.py`: Focus & Giả lập Ctrl+V
- Focus cửa sổ DaVinci Resolve (`src/core/fast_paste.py:16-99`):
  - **Linux:** Gọi `wmctrl -a "DaVinci Resolve"` hoặc `xdotool search --onlyvisible --name "DaVinci Resolve" windowactivate`.
  - **Windows:** Dùng `ctypes.windll.user32.FindWindowW(None, "DaVinci Resolve")` hoặc quét `EnumWindows`, sau đó gọi `SetForegroundWindow(hwnd)`.
  - **macOS:** Dùng `osascript -e 'tell application "DaVinci Resolve" to activate'`.
- Giả lập phím Ctrl+V (`src/core/fast_paste.py:102-143`):
  - **Linux:** `subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"])`.
  - **Windows:** `ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)` (Ctrl down), `keybd_event(0x56, 0, 0, 0)` (V down), rồi nhả phím tương ứng.
  - **Fallback:** `pyautogui.hotkey("ctrl", "v")`.

### 4. `drag_drop_tracker.py`: Phát hiện thả ngoài app lên Timeline
- Cơ chế (`src/ui/lux/drag_drop_tracker.py:12-28`):
  - Lấy toạ độ chuột toàn cục `QCursor.pos()`.
  - Lấy bounding box `(x, y, w, h)` của cửa sổ DaVinci Resolve qua `get_resolve_window_rect()`.
  - Kiểm tra `(rx <= px <= rx + rw) and (ry <= py <= ry + rh)`.
  - **Xác định vùng timeline cụ thể:** **KHÔNG TÌM THẤY.** Code chỉ kiểm tra chuột có nằm trong khung bao toàn bộ cửa sổ DaVinci Resolve hay không, không bóc tách riêng toạ độ của timeline track.

### 5. `title_insert_worker.py`: Giao tiếp Process Worker & UI
- Giao tiếp giữa Worker và UI (`src/core/resolve_api.py:1999-2047` & `src/core/title_insert_worker.py:22-41`):
  - Spawns subprocess độc lập: `sys.executable -m src.core.title_insert_worker --title ... --duration ...`.
  - Worker in ra stdout dòng tiền tố: `CHUNDVC_RESULT:<JSON_PAYLOAD>`.
  - Main UI process dùng `subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)` đọc từng dòng stdout, parse JSON payload để cập nhật trạng thái.
  - Hỗ trợ thêm cờ `--result-file` để ghi file JSON tạm chống tràn buffer.
- Thời gian chèn trung bình: **KHÔNG TÌM THẤY** trong log hay code (chỉ có timeout quy định là `15.0s`).

---

## E. Hotkey toàn cục và nền tảng

1. **Hotkey toàn cục (Global Hotkey):**
   - Đã có global hotkey chưa: **CHƯA CÓ.** Không có thư viện background hook như `pynput` hay `keyboard`.
   - Ứng dụng chỉ dùng `QShortcut` cục bộ trong bảng duyệt câu thoại của cửa sổ ứng dụng (`Space`, `Undo`, `Redo`, `Alt+Up`, `Alt+Down` trong `src/ui/app.py:1933-1977` và `src/ui/auto/auto_window.py`).
   - Hoạt động trên Wayland không: **KHÔNG ÁP DỤNG.**
2. **Phân nhánh theo X11 / Wayland / Windows:**
   - Trong `src/main.py:5-7`: Bắt buộc ép chạy trên X11 (XCB), hoàn toàn bỏ qua Wayland:
     ```python
     if sys.platform.startswith("linux"):
         os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
         os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
     ```
   - Các điểm phân nhánh nền tảng khác:
     - `src/core/resolve_bridge.py`: Đường dẫn `/opt/resolve/` (Linux) vs `C:\ProgramData` (Windows).
     - `src/core/fast_paste.py`: `xdotool`/`wmctrl` (Linux) vs `ctypes user32` (Windows).
     - `src/ui/lux/window_anchor.py`: `wmctrl`/`xdotool` (Linux) vs `EnumWindows` (Windows).
     - `src/ui/studio/studio_window.py:43`: Đọc CPU/RAM qua `psutil` hoặc `kernel32` (Windows) vs fallback.
     - `src/ui/tabs/tab_sfx.py:72`: Phát âm thanh qua `winsound` (Windows) vs `paplay`/`aplay` (Linux).
     - `src/utils/path_utils.py:54`: Font chữ `Arial` (Windows) vs `Liberation Sans` (Linux).
3. **System Tray Icon:**
   - `QSystemTrayIcon`: **KHÔNG TÌM THẤY** (Không có biểu tượng khay hệ thống cạnh đồng hồ).
   - Thay vào đó, app dùng widget quả cầu nổi `FloatingBubbleWidget` (`src/ui/bubble/floating_bubble.py`) chạy thường trực trên Desktop.

---

## F. Mô hình thread & hiệu năng

### 1. Danh sách `QThread` / `QThreadPool` / `threading` / `multiprocessing`
1. `PipelineWorker(QThread)` (`src/ui/auto/auto_window.py:172` & `src/ui/app.py:163`): Chạy toàn bộ pipeline nặng ngoại tuyến: trích xuất audio, Whisper STT, cắt khoảng lặng, tính toán visual activity, phân tích beat, xuất XML.
2. `LLMDirectorWorker(QThread)` (`src/ui/auto/auto_window.py:122` & `src/ui/app.py:113`): Gửi request HTTP tới Ollama hoặc Cloud AI (Gemini, DeepSeek, Claude, OpenAI) để lấy kịch bản JSON mà không giật lag giao diện.
3. `StarterPackWorker(QThread)` (`src/core/starter_pack_downloader.py:180`): Tải và giải nén bộ tài nguyên Starter Pack (SFX, LUTs, BGM, Memes) chạy ngầm.
4. `CloudDownloadWorker(QThread)` (`src/ui/widgets/drive_dialog.py:21`): Tải dữ liệu từ Google Drive qua `gdown`.
5. `CacheCheckWorker(QRunnable)` (`src/ui/auto/auto_window.py:1747`): Chạy trên `QThreadPool.globalInstance()`, tính mã băm MD5 footage để kiểm tra Scan Cache có sẵn hay chưa.
6. `HardwareStatsWorker(QRunnable)` (`src/ui/studio/studio_window.py:28`): Chạy trên `QThreadPool.globalInstance()`, định kỳ 5 giây đo CPU, GPU, RAM.
7. `ThreadPoolExecutor(max_workers=4)` (`src/core/asset_cache.py:33`): Worker pool chuyên xử lý trích xuất thumbnail video và cache ảnh.
8. `ThreadPoolExecutor(max_workers=2)` (`src/core/proxy_manager.py:109`): Quét và tạo proxy song song.
9. `ThreadPoolExecutor(max_workers=1)` (`src/core/resolve_api.py:131`): Kiểm tra kết nối DaVinci Resolve với timeout 2.0s chống nghẽn main thread.
10. `Subprocess Worker`: `title_insert_worker.py` được thực thi dưới dạng tiến trình độc lập qua `subprocess.run` (`src/core/resolve_api.py:2029`).
11. `threading.Thread(target=server_loop)` (`src/resolve_scripts/ChunDVC_Bridge.py:110`): Server socket lắng nghe lệnh trong script nhúng của DaVinci Resolve.

### 2. Cách thumbnail async đẩy về UI
- Cơ chế trong `src/core/asset_cache.py:197-241`:
  - `request_thumbnail_async(media_path, callback)` kiểm tra cache trên đĩa `~/.cache/chundvc/thumbnails/{hash}.jpg`.
  - Nếu chưa có, đẩy tác vụ vào `_THREAD_POOL` (`ThreadPoolExecutor`).
  - Khi hoàn thành, gọi hàm `_dispatch_callback`:
    ```python
    # src/core/asset_cache.py:236
    QTimer.singleShot(0, lambda: callback(result))
    ```
  - Bắn trực tiếp qua event loop của Qt Main Thread, gọi hàm callback `_on_async_thumb_ready(thumb_path)` gán `QPixmap` vào `QLabel` của thẻ `AssetCard` thủ công.

### 3. Grid asset: Model/View hay QWidget Card hàng loạt?
- Cơ chế: **Tạo hàng loạt QWidget card thủ công** (`AssetCard` kế thừa `QFrame`), không dùng `QListView` hay `QAbstractListModel`.
- Toàn bộ danh sách card được lưu trong mảng `self.card_widgets` (`src/ui/tabs/tab_assets.py:3266`). Mỗi lần lọc, app duyệt mảng, gọi `card.setVisible(True/False)` và gán lại toạ độ vào `self.grid.addWidget(card, r, c)`.
- Số card tối đa trong kho hiện tại: **580+ cards** (435 SFX, 68 memes, 23 LUTs, 19 BGM, 24 Text presets, 6 Transitions, 23 Overlays).

### 4. Hover video preview
- Trên Grid Cards (`AssetCard`): **Chưa có video preview khi hover.** Khi hover chỉ phát âm thanh xem trước đối với SFX/BGM bằng `_shared_player: QMediaPlayer`. Đối với video meme/animation, thẻ hiển thị thumbnail tĩnh được extract qua ffmpeg.
- Trong Inspector (Cột 4): **Đã có video preview** qua widget `IconMemePreviewWidget` (`src/ui/tabs/tab_assets.py:1286`) sử dụng `QMediaPlayer` + `QVideoWidget`.
- Trong `MacroCard` (`src/ui/lux/macro_card.py:23`): Hover preview là hình vẽ mô phỏng 2D procedural canvas bằng `QPainter` chạy timer 40ms (~30 FPS), không dùng video file thực tế.

---

## G. Dữ liệu asset & tìm kiếm

### 1. Cấu trúc thư mục asset & Schema metadata
Thư mục gốc: `assets/`
```text
assets/
├── broll_memes/
│   ├── cinematic/ (1 file .mp4)
│   ├── green_screen/ (4 file .mp4)
│   ├── memes/ (102 files: 34 .mp4, 34 .mov, 34 .png)
│   └── overlays/ (18 files .png)
├── luts/ (23 files .cube)
├── music/
│   ├── chill_vlog/ (1 .mp3, 4 .wav)
│   ├── cinematic/ (1 .mp3, 4 .wav)
│   ├── funny/ (1 .mp3, 3 .wav)
│   └── upbeat_trend/ (1 .mp3, 4 .wav)
├── sfx/ (50 files .wav)
├── starter_pack/ (Hàng trăm file BGM, SFX dạng .wav, .mp3, .ogg)
└── templates/
    ├── Titles/ (8 files .setting)
    └── Transitions/ (6 files .setting)
```
Bản ghi mẫu schema preset Text+ JSON (`presets/text_styles/kinetic_hormozi.json:1-29`):
```json
{
  "id": "kinetic_hormozi",
  "name": "Alex Hormozi Pop (Kinetic Bounce)",
  "category": "kinetic",
  "badge_icon": "💥",
  "tags": [
    "Viral",
    "Hormozi",
    "TikTok",
    "Shorts",
    "SpringPop",
    "HighEnergy"
  ],
  "description": "Chữ nảy lò xo (Spring curve) bám từng từ khóa chính, màu vàng neon/xanh chuối viền đen dày tương phản cao cho Shorts/Reels triệu view.",
  "font": "Impact",
  "size": 54,
  "weight": "extra_bold",
  "standard_color": "#FFFFFF",
  "highlight_color": "#FFE600",
  "outline_color": "#000000",
  "outline_width": 0.18,
  "animation": "bounce",
  "timing_curve": "spring",
  "position_y_16_9": 0.15,
  "position_y_9_16": 0.35,
  "box_color": null,
  "glow_color": null,
  "gradient_colors": null
}
```

### 2. Danh sách Category & Số lượng thực tế
Số lượng đếm trực tiếp qua `AssetIndexer.scan_all_assets()`:
- **SFX (Hiệu ứng âm thanh):** 435 files (50 trong `assets/sfx/` + 385 trong `assets/starter_pack/SFX/`).
- **Memes & B-Roll:** 68 video files (`.mp4`, `.mov`) trong `assets/broll_memes/memes/`.
- **LUTs (Bộ màu 3D):** 23 files `.cube` trong `assets/luts/`.
- **BGM (Nhạc nền):** 19 files (5 `chill_vlog`, 5 `upbeat_trend`, 5 `cinematic`, 4 `funny`).
- **Titles (Mẫu chữ Text+):** 8 files `.setting` trong `assets/templates/Titles/` (+ 24 built-in preset JSON trong `presets/text_styles/`).
- **Transitions (Chuyển cảnh):** 6 files `.setting` trong `assets/templates/Transitions/` (+ 4 built-in code macros).
- **Overlays (Lớp phủ đồ hoạ):** 23 files `.png` trong `assets/overlays/` và `assets/broll_memes/overlays/`.

### 3. Định dạng file preset
- `.setting`: Macro node tree của DaVinci Resolve Fusion (chứa cấu hình TextPlus, Transform, AnimCurves, Keyframes).
- `.drfx`: **KHÔNG TÌM THẤY** trong mã nguồn (dự án chưa hỗ trợ đóng gói bundle `.drfx`).
- `.mp4`, `.mov`: Video clips (Meme, Green screen, B-roll).
- `.wav`, `.mp3`, `.ogg`: File âm thanh (SFX, BGM).
- `.cube`: Bảng tra cứu màu 3D LUT (33x33x33 hoặc 65x65x65).
- `.json`: Cấu hình text preset và recipe workflow.

### 4. Thuật toán tìm kiếm tiếng Việt không dấu
Được định nghĩa tại `src/core/motion_analyzer.py:79-110`:
```python
# src/core/motion_analyzer.py:79-87
def strip_accents(text: str) -> str:
    """Removes Vietnamese tone marks / accents for fuzzy query matching."""
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFKD', text)
    stripped = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return stripped.replace('đ', 'd').replace('Đ', 'D').lower()

def matches_query(query: str, tags_or_text: List[str] | str) -> bool:
    if not query:
        return True
    clean_q = strip_accents(query.strip().lower())
    if not clean_q:
        return True
    target_list = [tags_or_text] if isinstance(tags_or_text, str) else list(tags_or_text)
    for item in target_list:
        clean_item = strip_accents(str(item).lower())
        if clean_q in clean_item or clean_item in clean_q:
            return True
    return False
```
- Cơ chế: Khử dấu qua `unicodedata.normalize('NFKD')`, thay thế `đ/Đ` thành `d/D`, so sánh 2 chiều (`clean_q in clean_item or clean_item in clean_q`). Không có thuật toán chấm điểm Levenshtein hay Ranking theo trọng số.
- Bảng từ điển alias 2 ngôn ngữ (`BILINGUAL_TAG_MAP` trong `src/core/motion_analyzer.py:21-76`):
  ```python
  BILINGUAL_TAG_MAP = {
      "shake": ["shake", "camera shake", "glitch", "vibrate", "rung", "giat", "rung giat", "rung giật", "giật", "nẩy", "chan dong"],
      "zoom_in": ["zoom", "zoom_in", "scale", "scale_up", "phong to", "phóng to", "zoom vao", "zoom vào", "tien lai"],
      "zoom_out": ["zoom", "zoom_out", "scale_down", "shrink", "thu nho", "thu nhỏ", "zoom ra", "lui lai"],
      "slide_up": ["slide", "slide_up", "up", "rise", "truot len", "trượt lên", "bay len", "noi len"],
      "slide_down": ["slide", "slide_down", "down", "drop", "truot xuong", "trượt xuống", "roi xuong", "ha xuong"],
      "slide_left": ["slide", "slide_left", "left", "pan_left", "truot trai", "trượt trái", "qua trai", "sang trai"],
      "slide_right": ["slide", "slide_right", "right", "pan_right", "truot phai", "trượt phải", "qua phai", "sang phai"],
      "fade_in": ["fade", "fade_in", "dissolve", "appear", "mo dan", "mờ dần", "hien dan", "hiện dần", "sang dan"],
      "fade_out": ["fade", "fade_out", "disappear", "mo dan", "mờ dần", "bien mat", "biến mất", "toi dan"],
      "rapid_cuts": ["rapid", "cut", "transition", "flash", "nhanh", "chớp", "chuyen canh", "chuyển cảnh"],
      "static": ["static", "still", "tinh", "tĩnh", "dung yen", "đứng yên", "khong dong"],
      "text": ["text", "title", "typography", "subtitle", "caption", "chu", "chữ", "tieu de", "phu de"],
      "animated": ["animated", "animation", "motion", "chuyen dong", "chuyển động", "hoat hoa"],
      "glow": ["glow", "neon", "shine", "light", "phat sang", "phát sáng", "da quang", "den neon"]
  }
  ```

### 5. Output của `motion_analyzer`
- Output: Dictionary gồm `motion` (loại chuyển động), `directions` (mảng hướng chuyển động), `tags` (danh sách keyword 2 thứ tiếng), `confidence` (float 0..1), `motion_energy` (float đo cường độ quang thông Farneback) (`src/core/motion_analyzer.py:310-316`).
- Nơi lưu trữ: Được lưu trực tiếp vào thuộc tính in-memory `item.motion_tags` của đối tượng `StudioAsset` / `MacroCard` (`src/ui/tabs/tab_assets.py:4853`). Khi người dùng gõ từ khoá tìm kiếm, `matches_query` so sánh trực tiếp với trường này.

---

## H. Theme / Design tokens

### 1. Bảng màu, Font, Kích thước từ `src/ui/theme.py`

```python
# src/ui/theme.py:9-85
class ThemeColors:
    # 1. Backgrounds (Nền)
    BG_CANVAS = "#131316"       # --bg-0 (Màn hình desktop / Canvas tối)
    BG_MAIN = "#18181b"         # --bg-1 (Nền chính của các cửa sổ)
    BG_CARD = "#1e1e24"         # --bg-2 (Nền thẻ Card, GroupBox, Panel)
    BG_INPUT = "#26262d"        # --bg-3 (Nền ô nhập liệu, button phụ)
    BG_CARD_ACTIVE = "#2f2f37"  # --bg-4 (Nền khi active / hover)
    BG_HEADER = "#18181b"       # Nền thanh tiêu đề
    BG_CONSOLE = "#0e0e11"      # Nền khung nhật ký AI Console

    # 2. Borders & Lines (Đường viền)
    BORDER_DEFAULT = "#27272a"  # --line (Viền mặc định)
    BORDER_HOVER = "#33333a"    # --line-2 (Viền khi rê chuột)
    BORDER_FOCUS = "#8b5cf6"    # --violet-hi (Viền khi focus)
    BORDER_LINE3 = "#45454e"    # --line-3 (Viền nổi)
    BORDER_ACTIVE = "#7c3aed"   # --violet (Viền kích hoạt)
    BORDER_ACTIVE_BLUE = "#06b6d4" # --cyan (Viền media / progress)

    # 3. Typography Colors (Màu chữ)
    TEXT_PRIMARY = "#ededf0"    # --text (Chữ chính)
    TEXT_SECONDARY = "#a8a8b3"  # --text-2 (Chữ phụ)
    TEXT_MUTED = "#74747f"      # --text-3 (Chữ mờ / chú thích)
    TEXT_ACCENT = "#8b5cf6"     # --violet-hi (Chữ điểm nhấn)
    TEXT_SUCCESS = "#86efac"    # Chữ xanh lá thành công
    TEXT_WARNING = "#fcd34d"    # Chữ vàng cảnh báo
    TEXT_ERROR = "#fca5a5"      # Chữ đỏ lỗi

    # 4. Brand & Action (Tím Violet)
    PRIMARY = "#7c3aed"         # --violet (Màu tím thương hiệu)
    PRIMARY_HOVER = "#8b5cf6"   # --violet-hi
    PRIMARY_PRESSED = "#6d28d9"
    PRIMARY_TEXT = "#ffffff"    # Màu chữ trên nút tím
    VIOLET = "#7c3aed"
    VIOLET_HI = "#8b5cf6"
    VIOLET_LO = "rgba(124, 58, 237, 0.16)"

    # 5. Media, Progress & Playhead (Xanh Cyan)
    CYAN = "#06b6d4"            # --cyan (Màu tiến độ, playhead)
    CYAN_HI = "#22d3ee"         # --cyan-hi
    CYAN_LO = "rgba(6, 182, 212, 0.15)"

    # 6. Status Colors (Trạng thái chức năng)
    GREEN = "#22c55e"
    AMBER = "#f59e0b"
    RED = "#ef4444"
    PINK = "#ec4899"
    SUCCESS = "#22c55e"
    SUCCESS_HOVER = "#16a34a"
    SUCCESS_PRESSED = "#15803d"
    SUCCESS_LIGHT = "#22d3ee"
    DANGER = "#ef4444"
    DANGER_HOVER = "#dc2626"
    DANGER_PRESSED = "#b91c1c"
    ERROR = "#ef4444"
    ERROR_HOVER = "#dc2626"
    INFO = "#06b6d4"
    INFO_LIGHT = "#22d3ee"
    WARNING = "#f59e0b"
    WARNING_HOVER = "#d97706"

class ThemeFonts:
    FAMILY = "'Inter', 'Segoe UI Variable Text', 'Segoe UI', 'Ubuntu', 'Noto Sans', 'Cantarell', system-ui, sans-serif"
    FAMILY_MONO = "'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'Consolas', 'DejaVu Sans Mono', monospace"

    SIZE_TITLE = 16
    SIZE_SUBTITLE = 12
    SIZE_SECTION = 13
    SIZE_BODY = 12.5
    SIZE_SMALL = 11.5
    SIZE_FLOOR = 11.5
    SIZE_BTN_RUN = 14
```
- Spacing & Margins phổ biến: Layout margins `16px` (màn hình lớn), `8px` hoặc `6px` (panel con); Spacing giữa các widget `10px` - `12px`.
- Border Radius tiêu chuẩn: `6px` (button nhỏ, input), `8px` (button chính, frame phụ), `10px` (card, modal container), `12px` - `14px` (status pill badge).

### 2. Thành phần dùng chung trong `src/ui/lux/`
1. `RippleOverlay` (`lux_effects.py`): Lớp phủ bán trong suốt tạo hiệu ứng gợn sóng toả tròn động (radial ripple) khi click chuột.
2. `SegmentIndicator` (`lux_effects.py`): Thanh trượt định vị phân đoạn với hoạt ảnh chuyển động mượt mà bằng easing curve.
3. `get_luxury_dark_stylesheet()` (`lux_effects.py`): Bộ định dạng QSS Glassmorphism tối sang trọng dành riêng cho dock và panel.
4. `MacroCard` (`macro_card.py`): Thẻ hiển thị mẫu tỷ lệ 16:9 với hover preview canvas 30 FPS, nhãn danh mục và hỗ trợ kéo thả.
5. `MacroGridWidget` (`macro_card.py`): Khung lưới 2 cột cuộn mượt gom nhóm các MacroCard kèm thanh tìm kiếm và lọc danh mục.
6. `EdgeDrawerWindow` (`panel_edge_rail.py`): Bảng điều khiển dạng ngăn kéo áp sát mép màn hình (thu gọn 40px, mở rộng 440px).
7. `ResolveWindowAnchor` (`window_anchor.py`): Bộ dò toạ độ cửa sổ DaVinci Resolve tự động trên Windows và Linux để neo bảng điều khiển.
8. `is_mouse_over_resolve()` (`drag_drop_tracker.py`): Hàm kiểm tra toạ độ con trỏ chuột có đang nằm đè lên cửa sổ DaVinci Resolve hay không.

---

## I. Copilot & AutoCut

### 1. Model của Copilot & Dữ liệu Timeline thật
- **Các model hỗ trợ:**
  - Local Ollama: `qwen2.5:7b-instruct`, `qwen2.5:14b-instruct`, `llama3.1:8b`, `deepseek-r1:7b`, `deepseek-r1:14b` (`tab_copilot.py:215-219`).
  - Cloud API: `gemini-3.5-flash` (fallback `gemini-3-flash-preview`), `deepseek-chat`, `gpt-4o`, `claude-3-7-sonnet-20250219` (`src/core/story_copilot.py:569-581`).
- **Có đọc dữ liệu timeline thật không:**
  - **KHÔNG đọc trực tiếp các clip đang xếp dở trên timeline hiện tại của DaVinci.**
  - Copilot đọc từ **dữ liệu footage nguồn đã phân tích trong cache** (`self.clip_data_cache` gồm danh sách clip, thời lượng, transcript từng câu có word timestamps từ Whisper, cao trào âm thanh, visual activity).
  - Từ dữ liệu đó, LLM sinh JSON kịch bản và tool gọi API nạp thành một timeline mới hoàn chỉnh trên DaVinci Resolve.

### 2. AutoCut: Tham số Silence, Preview & Undo
- **Tham số Silence Detection:**
  - Ngưỡng âm lượng im lặng: `silence_thresh_db` (Slider `-60 dB` đến `-10 dB`, mặc định `-35 dB`).
  - Thời lượng im lặng tối thiểu: `min_silence_len` (Slider `0.2s` đến `5.0s`, mặc định `0.5s`).
  - Track âm thanh phân tích: Track 1, Track 2, hoặc gộp chung.
  - Bảo vệ cảnh quay (`check_scene_guard`): Nếu vùng im lặng có chuyển động hình ảnh thì giữ lại hoặc tua nhanh thay vì cắt.
  - Tua nhanh khoảng lặng (`check_speedup`): Tua 8x thay vì xóa bỏ.
- **Có preview trước khi cắt không:** **CÓ.**
  - Hiển thị trực quan trên `MiniTimelineWidget` (`src/ui/widgets/mini_timeline.py`) bằng các khối màu (thoại, im lặng cắt bỏ, teaser hook).
  - Bảng duyệt câu thoại `self.table_review` trong `AutoWindow` cho phép xem trước từng câu thoại kèm trạng thái Giữ/Cắt trước khi bấm xuất timeline.
- **Có Undo không:** **CÓ.**
  - Lớp `ReviewState` (`src/core/review_state.py:10-80`) quản lý stack Undo/Redo lên đến **200 bước lịch sử** (`MAX_HISTORY = 200`), gán phím tắt `Ctrl+Z` (`QKeySequence.Undo`).

### 3. Liên kết kết quả giữa Copilot và AutoCut
- **CÓ LIÊN KẾT THÔNG QUA DỮ LIỆU CHUNG.**
- Khi người dùng nạp video ở Bước 1, toàn bộ transcript từ Whisper và thông số âm thanh được lưu vào `self.clip_data_cache` và disk cache `~/.resolveflow_cache/`.
- Cả `TabAutoCut` (cắt thô offline) và `TabCopilot` (đạo diễn AI) đều đọc chung từ kho dữ liệu `clip_data_cache` này. Hai tab không truyền signal trực tiếp cho nhau mà thông qua trạng thái chung của cửa sổ cha `AutoWindow`.

---

## J. Export

- **Danh sách Preset xuất bản (`src/ui/tabs/tab_export.py:360-365` & `src/core/resolve_api.py:1145-1175`):**
  1. `tiktok_916`: TikTok / Reels / Shorts (Độ phân giải dọc 1080x1920 60fps, container MP4, codec H.264, bitrate ~15 Mbps).
  2. `youtube_1080p`: YouTube Full HD (1920x1080 60fps, container MP4, codec H.264, bitrate ~25 Mbps).
  3. `youtube_4k`: YouTube 4K Ultra HD (3840x2160 60fps, container MP4, codec H.265 Master).
  4. `podcast_audio`: Podcast Audio Only (Định dạng WAV 48kHz, LinearPCM Master).
- **Có dùng Resolve render API không:** **CÓ.**
  - Gọi qua `src/core/resolve_api.py:1132-1205`:
    - `project.SetCurrentRenderFormatAndCodec(cfg["format"], cfg["codec"])`
    - `project.SetRenderSettings({"CustomResolution": True, "TargetDir": ...})`
    - `job_id = project.AddRenderJob()`
    - Gọi tiếp `project.StartRendering(job_id)` (tuỳ chọn).
- **Cách hiển thị tiến độ:**
  - **CHƯA CÓ thanh tiến độ thời gian thực (Progress Bar).**
  - App chỉ hiển thị dòng text thông báo `lbl_render_info` ("Đã thêm công việc kết xuất thành công vào Render Queue của Resolve (Job ID: ...)") hoặc in ra Console. Chưa có cơ chế vòng lặp định kỳ thăm dò `project.GetRenderJobStatus(job_id)` để cập nhật % tiến độ render trên UI.

---

## K. Tổng hợp vấn đề kỹ thuật & Điểm nghẽn UI

### 1. Rà soát comment TODO / FIXME / HACK
- Trong toàn bộ mã nguồn thư mục `src/`: **KHÔNG TÌM THẤY bất kỳ chú thích `TODO`, `FIXME` hay `HACK` nào.**

### 2. Các điểm gượng ép trong kiến trúc UX/UI (Code Smells)

1. **File UI nguyên khối khổng lồ (Monolithic Files):**
   - `src/ui/tabs/tab_assets.py` dài tới **6,664 dòng**. File này vừa quản lý 4 cột UI, vừa xử lý logic quét đĩa, parse file `.setting` Fusion, quản lý âm thanh `QMediaPlayer`, tính toán màu sắc, tạo drag mime data, và xử lý timer live preview canvas.
   - `src/ui/auto/auto_window.py` dài **6,368 dòng** và `src/ui/app.py` dài **4,585 dòng**. Logic xử lý nghiệp vụ (gọi Whisper, phân tích âm thanh, sinh XML, gọi DaVinci API) bị trộn lẫn trực tiếp với mã tạo layout giao diện.
2. **Tồn tại 2 cửa sổ chính song song gây phân mảnh code:**
   - `src/ui/app.py` (cửa sổ giao diện tab truyền thống cũ) và `src/ui/auto/auto_window.py` (cửa sổ 3 cột mới) cùng tồn tại và lặp lại gần như 80% logic xử lý tab, signal và worker.
3. **Widget ẩn tạo ra chỉ để chống lỗi unit test cũ:**
   - Trong `src/ui/tabs/tab_assets.py:3304-3329`, một loạt widget cũ (`group_presets`, `check_subtitle`, `combo_text_preset`, `btn_preview_preset`, `txt_font`, `txt_size`, `txt_color`...) được khởi tạo rồi gọi ngay lệnh `.hide()` chỉ để tránh crash khi các test case cũ hoặc `app.py` truy cập vào thuộc tính.
4. **Hiệu năng Grid Card kém tối ưu (Không dùng Virtual Scrolling):**
   - Khi kho tài nguyên có hàng trăm items, `TabAssets` tạo hàng trăm đối tượng `AssetCard` (mỗi thẻ là một `QFrame` chứa nhiều layout con, nút bấm, label). Khi filter hoặc tìm kiếm, hệ thống lặp qua hàng trăm widget để ẩn/hiện thủ công thay vì sử dụng kiến trúc Model/View chuẩn (`QListView` + `QStyledItemDelegate`) của Qt.
5. **Hard-coded kích thước điểm ảnh (Pixel Dimensions):**
   - Các kích thước được fix cứng: Rail width `78px`, card height `148px`, drawer width `440px`, collapsed rail `40px`.
   - Xung đột kích thước: Khi nhúng `TabAssets` (yêu cầu tối thiểu `798px` để hiển thị đủ 4 cột) vào `EdgeDrawerWindow` (chỉ rộng `440px`), các cột bị ép dẹp hoặc vỡ layout nếu người dùng không tự tay ẩn bớt 2 cột.
6. **Chức năng trùng lặp giữa các tab:**
   - Tab SFX: Xuất hiện độc lập tại `TabSFX` (Soundboard 12 pad) và xuất hiện lại lần thứ hai trong `TabAssets` (Rail `sfx` với hơn 430 file).
   - Tab LUT / Màu sắc: Xuất hiện độc lập tại `TabExport` (lưới thẻ Shotdeck) và lặp lại lần thứ hai trong `TabAssets` (Rail `lut`).
7. **Signal chồng chéo nhiều tầng:**
   - Luồng điều hướng và báo cáo tiến trình phải nhảy qua 3-4 trạm trung gian: `FloatingBubbleWidget` ↔ `AutoWindow` ↔ `StudioWindow` ↔ `PipelineWorker`, làm tăng độ phức tạp khi gỡ lỗi và bảo trì.
