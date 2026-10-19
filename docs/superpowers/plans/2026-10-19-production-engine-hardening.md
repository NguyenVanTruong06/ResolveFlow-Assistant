# Production Engine Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform ResolveFlow-Assistant from a working prototype into a battle-tested, production-ready video pipeline with frame-exact timebase snapping, Vietnamese audio speech padding, variable frame rate (VFR) guards, and true 1-click DaVinci Resolve auto-import with seamless FCPXML-to-FCP7 fallback.

**Architecture:** 
1. Introduce rational frame-boundary snapping `snap_time_to_frame(seconds, fps)` ensuring every in/out/offset in FCPXML 1.9 and FCP7 XML computes on exact integer frame multiples `(int_frames * frame_duration)` preventing black flash gaps and cumulative sync drifts.
2. Upgrade `src/core/autocut.py` with 50ms pre-roll / 80ms post-roll and <0.15s close-segment merging to preserve Vietnamese trailing consonants and avoid jittery micro-cuts.
3. Implement `detect_vfr_status(video_path)` in `src/core/ffmpeg_utils.py` with cached metadata comparisons and clear AI Console warnings for >0.5 fps discrepancies.
4. Enhance `src/core/resolve_api.py` with `auto_import_timeline_to_resolve(xml_path, timeline_name)` providing auto-project creation, dual-format fallback (.fcpxml -> .xml), and Edit Page focus, paired with a dynamic 1-click DaVinci button and floating bubble status updates in `src/ui/auto/auto_window.py`.

**Tech Stack:** Python 3.10-3.12, PySide6, FFmpeg/ffprobe, DaVinci Resolve Scripting API, pytest.

**Spec:** Direct user requirements for Production Engine Hardening (Frame-Exact Timebase, Audio Padding, VFR Guard & 1-Click DaVinci Import).

---

## Global Constraints
- Python 3.10+ compatibility across Linux, Windows, and macOS.
- All timeline rational time expressions must use exact integer frame counts `{frames * fd_num}/{fd_den}s` or exact NTSC timebase without raw float concatenation.
- Headless execution for PySide6 GUI tests (`QT_QPA_PLATFORM=offscreen`).
- Backward compatibility: preserve all existing `ResolveAutomation` method signatures and existing presets.
- Preserve 100% test pass rate across the full test suite.

## Review Focus
1. 23.976 / 29.97 / 59.94 NTSC fractional fps must calculate correct rational denominators (e.g. `1001/30000s`) without producing fractional frame counts.
2. Two consecutive speech segments spaced by `< 0.15s` must merge cleanly after applying `pre_roll=0.05` and `post_roll=0.08`.
3. VFR detector must handle non-fractional strings, zero division, and missing `avg_frame_rate` gracefully.
4. Auto-import must succeed even if no project was initially open in DaVinci Resolve (auto-creates `ResolveFlow_AutoProject`).
5. Fallback from `.fcpxml` to `.xml` must trigger immediately when DaVinci Resolve API returns `None` or empty video track.

---

### Task 1: Frame-Boundary Snapping (`snap_time_to_frame`) & Rational Timebase

**Files:**
- Modify: `src/core/fcpxml_generator.py`
- Modify: `src/core/autocut.py`
- Test: `tests/test_fcpxml_generator.py`

**Interfaces:**
- Produces: `snap_time_to_frame(seconds: float, fps: float) -> float` in `src/core/fcpxml_generator.py` and `src/core/autocut.py`
- Produces: `seconds_to_frame_count(seconds: float, fps: float) -> int` helper in `src/core/fcpxml_generator.py`

- [ ] **Step 1: Write unit tests for `snap_time_to_frame` and integer frame snapping**

```python
# In tests/test_fcpxml_generator.py
def test_snap_time_to_frame_various_fps():
    from src.core.fcpxml_generator import FCPXMLGenerator
    # 30 fps -> frame duration = 1/30 = 0.033333...s
    assert FCPXMLGenerator.snap_time_to_frame(0.034, 30.0) == 1.0 / 30.0
    assert FCPXMLGenerator.snap_time_to_frame(0.000, 30.0) == 0.0
    assert FCPXMLGenerator.snap_time_to_frame(1.000, 24.0) == 1.0
    # 29.97 fps frame snapping
    snapped = FCPXMLGenerator.snap_time_to_frame(1.0, 29.97)
    assert abs(snapped - round(1.0 * 29.97) / 29.97) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails before implementation**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_fcpxml_generator.py -k test_snap_time_to_frame -v`
Expected: FAIL (`AttributeError: type object 'FCPXMLGenerator' has no attribute 'snap_time_to_frame'`)

- [ ] **Step 3: Implement `snap_time_to_frame` and frame calculation refactoring**

In `src/core/fcpxml_generator.py`:
- Add static method `snap_time_to_frame(seconds: float, fps: float) -> float`:
  ```python
  @staticmethod
  def snap_time_to_frame(seconds: float, fps: float) -> float:
      if fps <= 0:
          fps = 30.0
      frame_idx = round(seconds * fps)
      return frame_idx / fps
  ```
- Add static method `seconds_to_frame_count(seconds: float, fps: float) -> int`:
  ```python
  @staticmethod
  def seconds_to_frame_count(seconds: float, fps: float) -> int:
      if fps <= 0:
          fps = 30.0
      return int(round(seconds * fps))
  ```
- In `generate_timeline_fcpxml` and `generate_fcp7_xml`: ensure all segment in/out calculations snap via `to_f(snap_time_to_frame(sec, real_fps))` and use strict frame-grid arithmetic.
- In `src/core/autocut.py`: export `snap_time_to_frame` and incorporate it into `seconds_to_timecode` and interval snapping.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_fcpxml_generator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/fcpxml_generator.py src/core/autocut.py tests/test_fcpxml_generator.py
git commit -m "feat(core): implement frame-boundary snapping snap_time_to_frame to prevent 1-frame black gaps"
```

---

### Task 2: Audio Pre-roll / Post-roll Padding & Close Segments Merging

**Files:**
- Modify: `src/core/autocut.py`
- Test: `tests/test_autocut.py`

**Interfaces:**
- Modifies: `AudioCutConfig` in `src/core/autocut.py` to support `pre_roll: float = 0.05`, `post_roll: float = 0.08`, and `min_gap_merge: float = 0.15`.
- Modifies: `detect_silence_from_wav` and `merge_speech_with_silence_intervals` in `src/core/autocut.py` to apply asymmetric padding and merge segments closer than 0.15s.

- [ ] **Step 1: Write unit tests for asymmetric padding and close-gap merging**

```python
# In tests/test_autocut.py
def test_audio_padding_and_close_segment_merging():
    from src.core.autocut import AudioCutConfig, CutSegment, SilenceDetector
    # If segments are [1.0, 2.0] and [2.10, 3.0]
    # With pre_roll=0.05 and post_roll=0.08:
    # Seg 1 becomes [0.95, 2.08]
    # Seg 2 becomes [2.05, 3.08]
    # Since 2.05 < 2.08, they overlap and must merge into [0.95, 3.08]
    # Also if gap < 0.15s, they merge.
```

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_autocut.py -k test_audio_padding -v`
Expected: FAIL

- [ ] **Step 3: Implement asymmetric padding and merging in `src/core/autocut.py`**

- In `AudioCutConfig`:
  ```python
  pre_roll: float = 0.05   # 50ms trước khi mở miệng
  post_roll: float = 0.08  # 80ms sau khi dứt lời giữ âm gió tiếng Việt
  min_gap_merge: float = 0.15 # Tự động gộp nếu khoảng cách < 150ms
  ```
- In `detect_silence_from_wav`:
  Apply `new_start = max(0.0, start - config.pre_roll)` and `new_end = min(duration, end + config.post_roll)`.
  When appending to `padded_keep_intervals`:
  ```python
  if padded_keep_intervals:
      prev_start, prev_end = padded_keep_intervals[-1]
      # Gộp nếu chồng chập hoặc khoảng cách giữa 2 đoạn nhỏ hơn min_gap_merge (0.15s)
      if new_start <= prev_end + config.min_gap_merge:
          padded_keep_intervals[-1] = (prev_start, max(prev_end, new_end))
      else:
          padded_keep_intervals.append((new_start, new_end))
  else:
      padded_keep_intervals.append((new_start, new_end))
  ```

- [ ] **Step 4: Run test to verify pass**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_autocut.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/autocut.py tests/test_autocut.py
git commit -m "feat(autocut): add asymmetric pre/post-roll padding and close segment auto-merge"
```

---

### Task 3: VFR (Variable Frame Rate) Guard & Console Alert

**Files:**
- Modify: `src/core/ffmpeg_utils.py`
- Modify: `src/core/validator.py`
- Modify: `src/ui/auto/auto_window.py`
- Test: `tests/test_validator.py`

**Interfaces:**
- Produces: `detect_vfr_status(video_path: Union[str, Path]) -> Dict[str, Any]` in `src/core/ffmpeg_utils.py`
  - Returns: `{"is_vfr": bool, "r_fps": float, "avg_fps": float, "fps_diff": float, "warning": Optional[str]}`

- [ ] **Step 1: Write unit test for `detect_vfr_status`**

```python
# In tests/test_validator.py
def test_detect_vfr_status():
    from src.core.ffmpeg_utils import detect_vfr_status
    # Test CFR simulation vs VFR simulation with mocked probe
```

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_validator.py -k test_detect_vfr_status -v`
Expected: FAIL

- [ ] **Step 3: Implement `detect_vfr_status` in `src/core/ffmpeg_utils.py` and integrate alert**

- In `src/core/ffmpeg_utils.py`:
  Implement `detect_vfr_status`:
  - Query `r_frame_rate` (nominal) and `avg_frame_rate` (average) via `ffprobe` (cached in `_VIDEO_META_CACHE`).
  - Calculate `fps_diff = abs(r_fps - avg_fps)`.
  - If `fps_diff > 0.5`: flag `is_vfr = True` and generate a human-readable warning recommending CFR transcode.
- In `src/ui/auto/auto_window.py`:
  During video loading / inspection, check `detect_vfr_status`. If `is_vfr`, emit an amber warning to `txt_console`:
  `"⚠️ [VFR Cảnh Báo] Phát hiện video tốc độ khung hình biến thiên (VFR: {avg_fps:.2f}fps vs {r_fps:.2f}fps). DaVinci Resolve có thể bị trôi tiếng nếu clip dài! Khuyên dùng Proxy CFR."`

- [ ] **Step 4: Run test to verify pass**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_validator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/ffmpeg_utils.py src/core/validator.py src/ui/auto/auto_window.py tests/test_validator.py
git commit -m "feat(vfr): add detect_vfr_status guard and AI console warning for variable frame rate footage"
```

---

### Task 4: True 1-Click DaVinci Resolve Auto-Import & Fallback

**Files:**
- Modify: `src/core/resolve_api.py`
- Modify: `src/ui/auto/auto_window.py`
- Test: `tests/test_resolve_api.py`

**Interfaces:**
- Produces: `auto_import_timeline_to_resolve(xml_path: str, timeline_name: Optional[str] = None, log_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]` in `src/core/resolve_api.py`

- [ ] **Step 1: Write unit test for `auto_import_timeline_to_resolve` with mock Resolve API**

```python
# In tests/test_resolve_api.py
def test_auto_import_timeline_to_resolve_fallback():
    from src.core.resolve_api import ResolveAutomation
    # Test that when importing .fcpxml returns None, it falls back to .xml
    # Test that project auto-creation occurs if current_project is None
```

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_resolve_api.py -k test_auto_import_timeline -v`
Expected: FAIL

- [ ] **Step 3: Implement `auto_import_timeline_to_resolve` in `src/core/resolve_api.py`**

- In `ResolveAutomation`:
  - Implement `auto_import_timeline_to_resolve(xml_path: str, timeline_name: Optional[str] = None, log_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]`:
    - Check connection via `self.connect()`. If not connected, return `(False, "Chưa kết nối được với DaVinci Resolve.")`.
    - If `not self.current_project`: attempt to get or create `project_manager.CreateProject("ResolveFlow_AutoProject")` or `LoadProject`.
    - Resolve candidate paths: if `.fcpxml`, candidate 1 is `.fcpxml`, candidate 2 is `.xml`. If `.xml`, candidate 1 is `.xml`, candidate 2 is `.fcpxml`.
    - Import via `media_pool.ImportTimelineFromFile(candidate_path, {"timelineName": timeline_name, "importSourceClips": True})`.
    - If successful, call `self.resolve.OpenPage("edit")` and return `(True, f"Đã nạp thành công Timeline '{timeline_name}' vào DaVinci Resolve!")`.
    - If candidate 1 fails, try candidate 2 immediately.
- In `src/ui/auto/auto_window.py`:
  - When pipeline reaches 100%:
    - If `connected`: invoke `auto_import_timeline_to_resolve`, set `FloatingBubbleWidget.set_pipeline_status("connected")`.
    - If not connected or import failed: display a prominent button `btn_1click_resolve` ("🚀 Đẩy ngay vào DaVinci Resolve (1-Click)") in the results card so the user can launch DaVinci Resolve and click once without re-running AI.

- [ ] **Step 4: Run test to verify pass**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/test_resolve_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/resolve_api.py src/ui/auto/auto_window.py tests/test_resolve_api.py
git commit -m "feat(resolve): add auto_import_timeline_to_resolve with auto-project and FCP7 fallback"
```

---

### Task 5: Full Regression Testing & Verification

**Files:**
- Test: `tests/`

- [ ] **Step 1: Run complete test suite**

Run: `PYTHONPATH=$PWD:$PWD/.venv/lib/python3.12/site-packages python3 -m pytest tests/`
Expected: 100% PASS with 0 failures.

- [ ] **Step 2: Run dry-run validation with Cát Tiên 2N1D story plan**

Run a script generating both `.fcpxml` and `.xml` from `recipes/vlog_cat_tien_2n1d.json`, verifying that all frames are clean integers and no gaps exist.

- [ ] **Step 3: Commit and summarize**

```bash
git commit -m "chore(release): complete production engine hardening verification"
```
