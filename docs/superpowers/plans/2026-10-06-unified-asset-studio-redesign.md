# Unified Asset Studio Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nâng cấp toàn diện phân hệ Kho Đạo Cụ (`src/ui/tabs/tab_assets.py`) trong ứng dụng Python Desktop đạt độ trực quan, sinh động 100% như thiết kế Web nguyên mẫu (`src/ui/resolveflow_ui.html`) với các bộ vẽ thẻ card chuyên biệt, bảng tinh chỉnh Inspector và tích hợp kéo thả vào DaVinci Resolve.

**Architecture:** Sử dụng kiến trúc Native PySide6 Custom Renderers kế thừa layout 4 cột hiện tại; tích hợp các bộ vẽ `QPainter`/Rich HTML cho thẻ Chữ, Canvas vẽ sóng âm đa cột cho SFX, dải Palette 4 cột cho LUT, và khung xem trước tương tác (16:9/9:16, Split Slider, Playback) trên bảng Inspector.

**Tech Stack:** Python 3.11, PySide6 (QtWidgets, QtGui, QtCore, QtMultimedia), DaVinci Resolve Scripting API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-unified-asset-studio-redesign.md`

## Global Constraints
- Phải tương thích 100% với cả bản DaVinci Resolve Free (qua Drag & Drop) và Studio (qua Scripting API).
- Mã Fusion Macro tạo ra phải tuân thủ đúng cú pháp node của DaVinci Resolve Fusion (`.setting`).
- Kích thước font chữ trong stylesheet phải là số nguyên pixel (`px`), không sử dụng số thực thập phân.
- Giao diện tối màu Zinc-900 `#18181b`, điểm nhấn Violet `#8b5cf6` và Cyan `#06b6d4`.

## Review Focus
1. Thẻ Chữ (Text) phải hiển thị đúng các kiểu dáng typographic độc bản (Hormozi 2 dòng vàng viền đen, Karaoke pop sáng từ active, Box highlight đỏ, Neon glow cyan).
2. Thẻ SFX phải có sóng âm dạng cột mô phỏng đúng envelope của âm thanh và có nút nghe thử phát được audio.
3. Bảng Inspector phải có nút gạt tỉ lệ 16:9 và 9:16 cho Text preview và thanh so sánh Split cho LUT.
4. Kéo thả (Drag & Drop) từ thẻ ra ngoài phải đóng gói đúng chuẩn MIME URL file `.setting` / `.cube` / `.wav` / `.mp4`.
5. Đổi tab giữa 5 nhóm (Chữ, Màu, Hiệu ứng, Sticker, Âm thanh, Yêu thích) phải mượt mà và cập nhật đúng danh mục con và bộ đếm.

---

### Task 1: Bộ hiển thị thẻ đạo cụ trực quan (Visual Card Renderers)

**Files:**
- Modify: `src/ui/tabs/tab_assets.py:260-450`
- Test: `tests/test_tab_assets.py`

**Interfaces:**
- Consumes: `StudioAsset`, `TextStylePreset`, `BUILTIN_PRESETS`, `ThemeColors`.
- Produces: `AssetCard` với các phương thức vẽ chuyên biệt: `_render_text_thumbnail()`, `_render_sfx_thumbnail()`, `_render_lut_thumbnail()`, `_render_transition_thumbnail()`, `_render_icon_thumbnail()`.

- [ ] **Step 1: Write the failing test for visual card renderers**

```python
def test_visual_card_renderers(qapp):
    from src.ui.tabs.tab_assets import AssetCard, StudioAsset, MOCKUP_LUTS, MOCKUP_SFX
    from src.core.text_preset import BUILTIN_PRESETS
    
    # 1. Thẻ Chữ Hormozi Pop
    card_text = AssetCard(BUILTIN_PRESETS["alex_hormozi"])
    assert card_text.thumb is not None
    assert card_text.findChild(QLabel) is not None
    
    # 2. Thẻ SFX Waveform
    card_sfx = AssetCard(MOCKUP_SFX[0])
    assert getattr(card_sfx, "waveform_widget", None) is not None
    assert getattr(card_sfx, "btn_quick_play", None) is not None
    
    # 3. Thẻ LUT Palette
    card_lut = AssetCard(MOCKUP_LUTS[0])
    assert getattr(card_lut, "palette_layout", None) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_visual_card_renderers -v`
Expected: FAIL (AttributeError hoặc thiếu widget)

- [ ] **Step 3: Implement Visual Card Renderers in `src/ui/tabs/tab_assets.py`**

Triển khai các bộ vẽ trong `AssetCard._init_ui()`:
- `_render_text_thumbnail()`: Tạo QLabel hỗ trợ Rich HTML hiển thị đúng styling từng kiểu (Hormozi, Karaoke, Box, Neon, VHS, Marker).
- `_render_sfx_thumbnail()`: Tạo `WaveformWidget` kế thừa `QFrame` vẽ 16 cột sóng âm theo `envelope` (hump, hit, rise, flat) và nút `btn_quick_play`.
- `_render_lut_thumbnail()`: Vẽ 4 cột màu palette với viền bo góc tinh tế và badge thể loại.
- `_render_transition_thumbnail()`: Vẽ visual motion icon, sọc scanline và badge frame count (`16f`, `24f`, `30f`).
- `_render_icon_thumbnail()`: Vẽ icon SVG hoặc emoji meme lớn.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_visual_card_renderers -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ui/tabs/tab_assets.py tests/test_tab_assets.py
git commit -m "feat(assets): implement high-fidelity visual card renderers"
```

---

### Task 2: Bảng tinh chỉnh chi tiết (Inspector Live Preview Viewports)

**Files:**
- Modify: `src/ui/tabs/tab_assets.py:650-850`
- Test: `tests/test_tab_assets.py`

**Interfaces:**
- Consumes: `AssetCard.selected_signal`, `StudioAsset`.
- Produces: `InspectorPanel` với `TextPreviewWidget` (hỗ trợ chuyển đổi 16:9 / 9:16), `LutSplitPreviewWidget` (hỗ trợ kéo slider so sánh Gốc/Sau), và `SFXWaveformPlayerWidget`.

- [ ] **Step 1: Write the failing test for inspector live preview**

```python
def test_inspector_live_previews(qapp):
    from src.ui.tabs.tab_assets import TabAssets, MOCKUP_LUTS
    from src.core.text_preset import BUILTIN_PRESETS
    
    tab = TabAssets()
    
    # 1. Chọn Text và chuyển đổi 16:9 sang 9:16
    tab._on_asset_selected(BUILTIN_PRESETS["karaoke_pop"])
    assert tab.inspector_aspect_btn is not None
    tab.inspector_aspect_btn.click() # Chuyển 9:16
    assert tab.current_aspect_ratio == "9:16"
    
    # 2. Chọn LUT và kiểm tra split slider
    tab._on_asset_selected(MOCKUP_LUTS[0])
    assert tab.lut_split_slider is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_inspector_live_previews -v`
Expected: FAIL (AttributeError)

- [ ] **Step 3: Implement Live Preview Viewports in `TabAssets` Inspector**

- Xây dựng vùng `preview_box` trên cùng Inspector:
  * Khi chọn Text: Hiển thị khung chữ lớn với nút gạt `16:9` / `9:16`.
  * Khi chọn LUT: Hiển thị 2 nửa ảnh Gốc / Sau kèm thanh kéo Split Slider (0% - 100%).
  * Khi chọn SFX: Hiển thị sóng âm lớn Big Waveform Canvas với thời lượng và nút Play/Stop mượt mà.
  * Khi chọn Transition: Hiển thị vòng lặp hoạt cảnh giao nhau của 2 khung hình.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_inspector_live_previews -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ui/tabs/tab_assets.py tests/test_tab_assets.py
git commit -m "feat(assets): add inspector live preview viewports and aspect ratio switcher"
```

---

### Task 3: Bộ điều khiển tham số & Tích hợp hành động DaVinci Resolve

**Files:**
- Modify: `src/ui/tabs/tab_assets.py:850-1150`
- Test: `tests/test_tab_assets.py`

**Interfaces:**
- Consumes: `FusionSettingGenerator`, `TransitionMacroGenerator`, `ResolveAutomation`.
- Produces: `_insert_at_playhead()`, `_generate_drag_mime_data()`, `_copy_fusion_macro()`, `_install_to_fusion()`.

- [ ] **Step 1: Write the failing test for actions and drag & drop**

```python
def test_asset_actions_and_drag_drop(qapp):
    from src.ui.tabs.tab_assets import TabAssets
    from src.core.text_preset import BUILTIN_PRESETS
    
    tab = TabAssets()
    tab._on_asset_selected(BUILTIN_PRESETS["alex_hormozi"])
    
    # Kiểm tra copy Fusion macro
    macro_code = tab._get_current_fusion_macro_code()
    assert "TextPlus" in macro_code or "MacroOperator" in macro_code
    
    # Kiểm tra tạo tệp kéo thả
    mime_data = tab._create_drag_mime_data()
    assert mime_data.hasUrls()
    assert len(mime_data.urls()) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_asset_actions_and_drag_drop -v`
Expected: FAIL

- [ ] **Step 3: Implement parameter controls & action buttons**

- Bổ sung các nhóm điều khiển tham số:
  * Kiểu chữ: Dropdown Font, Slider Size (24-140), Segmented Weight, Stroke thickness.
  * Bảng màu Swatches: 4 nút chọn màu (Chữ, Highlight, Viền, Hộp/Glow).
  * Chuyển động: Chips Animation (Pop, Bounce, Typewriter, Slide, Box, Glow) + Timing curve.
  * Track đích: V2 / V3 / Audio 2 (-12dB).
- Hoàn thiện 4 nút hành động:
  * `btn_insert_playhead`: Gọi Resolve Scripting API để chèn tại vị trí con trỏ playhead.
  * `btn_drag_davinci`: Bật chế độ Native QDrag với MIME data file `.setting`, `.cube`, `.wav`.
  * `btn_copy_fusion`: Copy mã Fusion Macro vào clipboard kèm Toast thông báo.
  * `btn_install_fusion`: Lưu file `.setting` vào thư mục Fusion Templates của DaVinci.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_tab_assets.py::test_asset_actions_and_drag_drop -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ui/tabs/tab_assets.py tests/test_tab_assets.py
git commit -m "feat(assets): add inspector parameter controls and DaVinci actions"
```

---

### Task 4: Kiểm thử toàn diện & Tích hợp Main Window

**Files:**
- Modify: `src/ui/auto/auto_window.py`
- Test: `tests/test_ui.py`, `tests/test_tab_assets.py`

- [ ] **Step 1: Write integration tests**

```python
def test_tab_assets_integrated_in_auto_window(qapp):
    from src.ui.auto.auto_window import AutoWindow
    window = AutoWindow()
    assert hasattr(window, "tab_titles")
    assert window.tab_titles.rail_btns is not None
    assert len(window.tab_titles.rail_btns) >= 5
    window.close()
    window.deleteLater()
```

- [ ] **Step 2: Run full test suite to verify no regressions**

Run: `venv\Scripts\python.exe -m pytest tests/test_ui.py tests/test_tab_assets.py`
Expected: PASS 100%

- [ ] **Step 3: Commit**

```bash
git add src/ui/auto/auto_window.py tests/test_ui.py tests/test_tab_assets.py
git commit -m "feat(assets): verify complete unified asset studio integration"
```
