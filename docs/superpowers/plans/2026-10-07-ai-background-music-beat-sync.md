# AI Background Music Hot & Auto Beat-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng hệ thống tự động chọn và ghép nhạc nền hot/vlog theo Mood, tách nhịp Beat Drop siêu tốc (Beat-Sync), đưa vào Prompt Đạo Diễn AI và xuất lên Timeline DaVinci Resolve trên Audio Track riêng biệt (A4/A5) kèm Beat Markers.

**Architecture:** 
1. Tải về và cấu trúc Kho Nhạc Mẫu Miễn Phí Bản Quyền (`assets/music/*`) và cập nhật `AssetIndexer` để tự động index BGM.
2. Nâng cấp `AudioBeatDetector` với thuật toán phân tích năng lượng sóng (RMS/Onset peaks) bằng `numpy` + `ffmpeg` chạy offline < 0.5s.
3. Tích hợp lựa chọn BGM và danh sách mốc nhịp vào `StoryCopilot` prompt và `TabCopilot` UI.
4. Mở rộng `FCPXMLGenerator` để tự động xếp nhạc nền lên Track A4/A5 (hỗ trợ ghép chuỗi bài theo thời lượng) và cắm cờ Beat Markers màu Cyan.

**Tech Stack:** Python 3.11, PySide6, FFmpeg, NumPy, XML ElementTree, DaVinci Resolve FCP7 XML / FCPXML v1.9.

**Spec:** `docs/superpowers/specs/2026-10-07-ai-background-music-beat-sync.md`

## Global Constraints

- Tương thích 100% với DaVinci Resolve Free và Studio thông qua FCP7 XML và Drag & Drop.
- Không phụ thuộc vào thư viện C++ nặng hoặc librosa (dùng NumPy + FFmpeg tiêu chuẩn).
- Không commit file âm thanh dung lượng lớn hoặc bản quyền vào git (thư mục assets/music/ chỉ chứa file mẫu CC0 nhỏ nhẹ).
- Toàn bộ font size trong stylesheet phải dùng số nguyên `px` (không dùng số thập phân như `10.5px`).
- Dark theme Zinc-900 `#18181b`, accents Violet `#8b5cf6` và Cyan `#06b6d4`.
- Môi trường ảo Python tại `venv\Scripts\python.exe`.

## Review Focus

1. Tệp âm thanh không tìm thấy hoặc sai định dạng: fallback an toàn mà không làm sập ứng dụng.
2. Phân tích Beat trên file nhạc quá ngắn (< 2s) hoặc im lặng hoàn toàn: trả về mảng rỗng không gây chia cho 0.
3. Video dài hơn bài nhạc trong chế độ Single Track: tự động loop và fade out ở cuối timeline.
4. Đảm bảo Audio Track A1/A2 (thoại) và A3 (SFX) không bị xáo trộn khi thêm Track A4/A5 (BGM).
5. Đồng bộ giao diện `TabAssets` và `TabCopilot` giữa `AutoWindow` và `AppWindow` không bị phân mảnh.

---

### Task 1: Starter BGM Pack & Music Asset Indexer

**Files:**
- Create: `assets/music/chill_vlog/.gitkeep`, `assets/music/upbeat_trend/.gitkeep`, `assets/music/cinematic/.gitkeep`, `assets/music/funny/.gitkeep`
- Modify: `src/core/asset_indexer.py`
- Test: `tests/test_music_indexer.py`

**Interfaces:**
- Consumes: Standard filesystem `assets/music/`
- Produces: `AssetIndexer.scan_music_assets() -> Dict[str, List[Dict[str, Any]]]`, `AssetIndexer.scan_all_assets() -> Dict[str, List[Dict[str, Any]]]` containing `bgm` category

- [ ] **Step 1: Write the failing test in `tests/test_music_indexer.py`**

```python
import os
import pytest
from src.core.asset_indexer import AssetIndexer

def test_scan_music_assets(tmp_path):
    music_dir = tmp_path / "assets" / "music" / "chill_vlog"
    music_dir.mkdir(parents=True)
    sample_file = music_dir / "sunset_chill.mp3"
    sample_file.write_bytes(b"dummy mp3 data")
    
    indexer = AssetIndexer(base_dir=str(tmp_path))
    res = indexer.scan_music_assets()
    assert "chill_vlog" in res
    assert len(res["chill_vlog"]) == 1
    assert res["chill_vlog"][0]["name"] == "sunset_chill"
    assert res["chill_vlog"][0]["category"] == "bgm"
    assert res["chill_vlog"][0]["mood"] == "chill_vlog"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_music_indexer.py -v`  
Expected: FAIL with `AttributeError: 'AssetIndexer' object has no attribute 'scan_music_assets'`

- [ ] **Step 3: Implement `scan_music_assets` and integrate into `scan_all_assets` in `src/core/asset_indexer.py`**

Thêm hỗ trợ quét thư mục `assets/music/`, phân loại theo mood folder (`chill_vlog`, `upbeat_trend`, `cinematic`, `funny`), đọc thời lượng nhanh qua ffmpeg nếu có, và khởi tạo các thư mục cùng audio samples miễn phí bản quyền.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_music_indexer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_music_indexer.py src/core/asset_indexer.py assets/music/
git commit -m "feat(assets): add starter bgm structure and music asset indexer"
```

---

### Task 2: Ultra-Fast Audio Beat Detector (`AudioBeatDetector`)

**Files:**
- Modify: `src/core/audio_beat.py`
- Test: `tests/test_audio_beat.py`

**Interfaces:**
- Consumes: Audio file path (MP3 / WAV)
- Produces: `AudioBeatDetector.detect_beats(audio_path: str, max_beats: int = 50) -> List[float]`

- [ ] **Step 1: Write the failing test in `tests/test_audio_beat.py`**

```python
import os
import wave
import struct
import pytest
from src.core.audio_beat.py import AudioBeatDetector

def test_detect_beats_synthetic_wav(tmp_path):
    # Tạo 1 file WAV mẫu có 3 nhịp giật rõ ràng ở 0.5s, 1.0s, 1.5s
    wav_path = str(tmp_path / "test_beat.wav")
    sr = 16000
    with wave.open(wav_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = []
        for i in range(sr * 2): # 2 giây
            t = i / sr
            # Đỉnh năng lượng tại 0.5, 1.0, 1.5
            amp = 30000 if (0.48 <= t <= 0.52 or 0.98 <= t <= 1.02 or 1.48 <= t <= 1.52) else 100
            frames.append(struct.pack("<h", int(amp)))
        wf.writeframes(b"".join(frames))

    beats = AudioBeatDetector.detect_beats(wav_path)
    assert isinstance(beats, list)
    assert len(beats) >= 2
    # Phải có beat quanh 0.5s và 1.0s
    assert any(abs(b - 0.5) < 0.15 for b in beats)
    assert any(abs(b - 1.0) < 0.15 for b in beats)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_audio_beat.py -v`  
Expected: FAIL (nhớ rằng hiện tại `librosa` chưa được cài đặt nên sẽ lỗi `ModuleNotFoundError: No module named 'librosa'`)

- [ ] **Step 3: Implement NumPy + Wave/FFmpeg Beat Detection in `src/core/audio_beat.py`**

Viết thuật toán tính Short-Time Energy và Peak Picking bằng NumPy:
1. Đọc audio bằng `wave` (nếu là wav) hoặc trích xuất raw PCM bằng `ffmpeg` (nếu là mp3/m4a).
2. Tính năng lượng từng khung (hop_length 512, frame_length 1024).
3. Tìm cực đại cục bộ (local maxima) vượt ngưỡng động trung bình năng lượng.
4. Trả về danh sách thời gian giây `List[float]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_audio_beat.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_audio_beat.py src/core/audio_beat.py
git commit -m "feat(audio): add fast numpy beat detection fallback without librosa"
```

---

### Task 3: Multi-Track BGM Placement & Beat Markers in XML (`FCPXMLGenerator`)

**Files:**
- Modify: `src/core/fcpxml_generator.py`
- Modify: `src/core/story_copilot.py`
- Test: `tests/test_fcpxml_bgm.py`

**Interfaces:**
- Consumes: `bgm_files: Optional[List[str]]`, `music_beats: Optional[List[float]]`
- Produces: Multi-track FCP7 XML & FCPXML containing Track A4/A5 with BGM clips and Cyan Beat Markers

- [ ] **Step 1: Write the failing test in `tests/test_fcpxml_bgm.py`**

```python
import os
import xml.etree.ElementTree as ET
import pytest
from src.core.fcpxml_generator import FCPXMLGenerator

def test_generate_fcp7_xml_with_bgm_and_beat_markers(tmp_path):
    events = [{
        "video_path": str(tmp_path / "v1.mp4"),
        "src_in": 0.0, "src_out": 10.0,
        "rec_in": 0.0, "rec_out": 10.0,
        "speed": 1.0, "track": 1
    }]
    bgm_path = str(tmp_path / "music.mp3")
    beats = [1.5, 3.0, 6.0, 9.0]
    out_xml = str(tmp_path / "timeline.xml")

    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Test_BGM",
        fps=30.0,
        bgm_files=[bgm_path],
        music_beats=beats
    )

    tree = ET.parse(out_xml)
    root = tree.getroot()
    # Kiểm tra có ít nhất 4 audio tracks (A1, A2 thoại, A3 SFX hoặc A4/A5 BGM)
    audio_tracks = root.findall(".//media/audio/track")
    assert len(audio_tracks) >= 4
    
    # Kiểm tra marker màu Cyan cho Beat Drop
    markers = root.findall(".//marker")
    beat_markers = [m for m in markers if "Beat Drop" in (m.find("name").text or "")]
    assert len(beat_markers) == len(beats)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_fcpxml_bgm.py -v`  
Expected: FAIL with `unexpected keyword argument 'bgm_files'`

- [ ] **Step 3: Implement BGM tracks & Beat Markers in `src/core/fcpxml_generator.py` and `src/core/story_copilot.py`**

1. Trong `generate_fcp7_xml`: Thêm tham số `bgm_files: Optional[List[str]] = None`, `music_beats: Optional[List[float]] = None`.
2. Tạo 2 Audio Tracks Stereo chuyên biệt A4 & A5 cho BGM, chèn clip BGM từ 0.0 đến hết độ dài timeline (nếu video dài hơn, tự động loop hoặc nối bài tiếp theo).
3. Tạo Marker màu `Cyan` cho từng mốc trong `music_beats`.
4. Trong `story_copilot.py`: Đảm bảo `convert_plan_to_resolve_timeline` truyền `bgm_files` và `music_beats` sang timeline.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_fcpxml_bgm.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_fcpxml_bgm.py src/core/fcpxml_generator.py src/core/story_copilot.py
git commit -m "feat(timeline): support dedicated stereo bgm tracks and beat drop markers"
```

---

### Task 4: UI Integration in TabAssets, TabCopilot & AutoWindow

**Files:**
- Modify: `src/ui/tabs/tab_assets.py`
- Modify: `src/ui/tabs/tab_copilot.py`
- Modify: `src/ui/auto/auto_window.py`
- Modify: `src/ui/app.py`
- Test: `tests/test_ui_bgm.py`

**Interfaces:**
- Consumes: `TabAssets`, `TabCopilot`, `AutoWindow`, `AppWindow`
- Produces: Interactive BGM selection card, audio preview, beat detection trigger, and end-to-end timeline export

- [ ] **Step 1: Write the failing test in `tests/test_ui_bgm.py`**

```python
import pytest
from PySide6.QtWidgets import QApplication
from src.ui.tabs.tab_copilot import TabCopilot

def test_tab_copilot_bgm_controls(qtbot):
    tab = TabCopilot()
    qtbot.addWidget(tab)
    assert hasattr(tab, "combo_bgm_mode")
    assert hasattr(tab, "chk_beat_sync")
    assert tab.combo_bgm_mode.count() >= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m pytest tests/test_ui_bgm.py -v`  
Expected: FAIL with `AttributeError: 'TabCopilot' object has no attribute 'combo_bgm_mode'`

- [ ] **Step 3: Implement BGM controls in `TabCopilot`, `TabAssets`, and wire them in `AutoWindow` / `AppWindow`**

1. `TabAssets`: Thêm danh mục `🎵 Nhạc Nền (BGM)` vào danh sách bên trái. Hiển thị card bài hát, nút Play/Pause nghe thử.
2. `TabCopilot`: Thêm Card 3 chọn Nhạc Nền (Tự động theo Mood / Chọn bài trong kho / Tải bài riêng / Tắt), Checkbox Beat-Sync.
3. `AutoWindow` & `AppWindow`:
   - Khi copy prompt hoặc chạy AI: Quét Beat của bài nhạc đã chọn bằng `AudioBeatDetector`, truyền `music_beats` vào prompt.
   - Khi thi công Timeline: Truyền bài nhạc và beats sang `FCPXMLGenerator`, copy file nhạc vào `_TIMELINE_IMPORT`.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv\Scripts\python.exe -m pytest tests/test_ui_bgm.py tests/test_tab_assets.py -v`  
Expected: PASS

- [ ] **Step 5: Run full test suite to ensure zero regressions**

Run: `venv\Scripts\python.exe -m pytest tests/test_ui.py tests/test_tab_assets.py tests/test_music_indexer.py tests/test_audio_beat.py tests/test_fcpxml_bgm.py tests/test_ui_bgm.py -v`  
Expected: 100% PASS

- [ ] **Step 6: Commit**

```bash
git add src/ui/tabs/tab_assets.py src/ui/tabs/tab_copilot.py src/ui/auto/auto_window.py src/ui/app.py tests/test_ui_bgm.py
git commit -m "feat(ui): integrate bgm selection, audio preview, and auto beat-sync pipeline"
```
