# Linux Standardization and DaVinci Resolve Linux Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize the ResolveFlow-Assistant codebase for Linux (Ubuntu 24.04), eliminating Windows-specific path dependencies, upgrading FCPXML/FCP7 URI generation, preloading FusionScript API on Linux, calibrating LUT and template installation paths, optimizing PySide6 XCB/FFmpeg media backends, and mitigating DaVinci Resolve Linux AAC codec limitations.

**Architecture:** Introduce `PosixPathAdapter` in `src/utils/path_utils.py` as the single source of truth for cross-platform path resolution and RFC 3986 URI formatting. Decouple DaVinci Resolve connection into a robust bridge with `load_resolve_api()` using ctypes preloading for `libfusionscript.so`. Abstract LUT and Fusion template paths into `src/core/asset_manager.py` with multi-tier Linux/Windows fallbacks. Add AAC detection and automatic PCM conversion in `src/core/audio_processor.py` to prevent silent audio on Linux DaVinci Resolve.

**Tech Stack:** Python 3.12, `pathlib.Path`, `ctypes`, PySide6 (Qt6 xcb / FFmpeg media backend), DaVinci Resolve Studio Linux API (`fusionscript.so`), FFmpeg / FFprobe, Apple FCPXML 1.9 & FCP7 XML.

**Spec:** User directive for Linux refactoring covering 6 technical tasks: path standardization, XML URI compliance, Resolve bridge preloading, LUT/template path management, Qt UI optimization, and AAC codec handling.

## Global Constraints
- Target environment: Ubuntu 24.04 LTS (Noble), Python 3.12, DaVinci Resolve Studio Linux (`/opt/resolve`).
- Absolute path resolution: All media paths must call `.expanduser().resolve()` before database storage, JSON serialization, or passing to FFmpeg / DaVinci Resolve.
- RFC 3986 XML URIs: Every `<asset ... src="...">` and `<pathurl>` must be formatted with `file:///` (3 slashes) and quoted with `urllib.parse.quote(posix_path, safe="/")`.
- Dynamic Resolve loading: `DaVinciResolveScript` must NOT be imported at module top-level before environment configuration.
- No Windows binary hardcoding: Never reference `ffmpeg.exe` or `ffprobe.exe` directly; use `shutil.which("ffmpeg")` / `shutil.which("ffprobe")`.
- Backward compatibility: Existing Windows and macOS compatibility must be maintained alongside first-class Linux support.

## Review Focus
1. Paths with spaces or non-ASCII characters: Verify `PosixPathAdapter.to_fcpxml_uri` handles spaces as `%20` while keeping `/` unescaped for DaVinci Resolve XML parser.
2. DaVinci Resolve Linux absence of AAC decoder: Check that MP4 files with AAC audio automatically produce extracted 48kHz PCM WAV files or remuxed MOV files so the audio is not muted in Resolve.
3. System permission limitations on `/var/BlackmagicDesign` or `/opt/resolve`: Ensure `AssetManager` falls back gracefully to `~/.local/share/DaVinciResolve/` if system paths are not writable.
4. Qt6 Multimedia backend on Linux Wayland/X11: Verify `QT_QPA_PLATFORM=xcb` and `QT_MEDIA_BACKEND=ffmpeg` are set before `QApplication` instantiation.
5. Drag and Drop with `QDrag`: Ensure `QMimeData.setUrls()` uses `QUrl.fromLocalFile()` and `drag.exec(Qt.DropAction.CopyAction)` without throwing deprecation warnings or failing drags.

---

### Task 1: Khởi tạo Module `src/utils/path_utils.py` (Xóa bỏ phụ thuộc Windows Path)

**Files:**
- Create: `src/utils/__init__.py`
- Create: `src/utils/path_utils.py`
- Create: `tests/test_path_utils.py`

**Interfaces:**
- Produces:
  ```python
  class PosixPathAdapter:
      @staticmethod
      def resolve_path(path: Union[str, Path]) -> Path: ...
      @staticmethod
      def to_posix(path: Union[str, Path]) -> str: ...
      @staticmethod
      def to_fcpxml_uri(path: Union[str, Path]) -> str: ...
      @staticmethod
      def to_local_qurl(path: Union[str, Path]) -> QUrl: ...
  ```

- [ ] **Step 1: Write the failing test for `PosixPathAdapter`**

```python
# tests/test_path_utils.py
import os
from pathlib import Path
from src.utils.path_utils import PosixPathAdapter

def test_resolve_path():
    p = PosixPathAdapter.resolve_path("~/test_file.mp4")
    assert isinstance(p, Path)
    assert p.is_absolute()
    assert "~" not in str(p)

def test_to_posix():
    win_path = r"C:\Users\Video\clip.mp4"
    posix = PosixPathAdapter.to_posix(win_path)
    assert "\\" not in posix
    assert posix.endswith("clip.mp4")

def test_to_fcpxml_uri_posix():
    path = "/media/user/Videos/clip sample.mp4"
    uri = PosixPathAdapter.to_fcpxml_uri(path)
    assert uri.startswith("file:///")
    assert "clip%20sample.mp4" in uri
    assert "//media/user" not in uri or uri.startswith("file:///media/user")

def test_to_fcpxml_uri_preserves_slashes():
    path = "/path/to/my video.mp4"
    uri = PosixPathAdapter.to_fcpxml_uri(path)
    assert uri == "file:///path/to/my%20video.mp4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_path_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.utils'`

- [ ] **Step 3: Implement `src/utils/path_utils.py` and `src/utils/__init__.py`**

Implement `PosixPathAdapter` with `resolve_path`, `to_posix`, `to_fcpxml_uri`, and `to_local_qurl` with full RFC 3986 encoding (`urllib.parse.quote(posix_str, safe="/")`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_path_utils.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/ tests/test_path_utils.py
git commit -m "feat(utils): implement PosixPathAdapter for Linux path standardization"
```

---

### Task 2: Nâng cấp bộ sinh XML (`src/core/fcpxml_generator.py` & `src/core/xml_exporter.py`)

**Files:**
- Create: `src/core/xml_exporter.py` (facade/alias pointing to `FCPXMLGenerator` with `PosixPathAdapter`)
- Modify: `src/core/fcpxml_generator.py`
- Test: `tests/test_fcpxml_generator.py`

**Interfaces:**
- Consumes: `from src.utils.path_utils import PosixPathAdapter`
- Produces: `FCPXMLGenerator.generate_timeline_fcpxml(...)`, `FCPXMLGenerator.generate_fcp7_xml(...)` using `PosixPathAdapter.to_fcpxml_uri`

- [ ] **Step 1: Write a test verifying RFC 3986 URI in generated FCPXML & FCP7 XML**

Add to `tests/test_fcpxml_generator.py`:
```python
def test_fcpxml_asset_uri_rfc3986_with_spaces(tmp_path):
    clip = tmp_path / "My Clip With Spaces.mp4"
    clip.touch()
    out_xml = tmp_path / "test_spaces.fcpxml"
    events = [{
        "video_path": str(clip),
        "src_in": 0.0, "src_out": 2.0,
        "rec_in": 0.0, "rec_out": 2.0, "fps": 25.0
    }]
    FCPXMLGenerator.generate_timeline_fcpxml(events, str(out_xml))
    with open(out_xml, "r", encoding="utf-8") as f:
        content = f.read()
    assert "file:///" in content
    assert "My%20Clip%20With%20Spaces.mp4" in content
```

- [ ] **Step 2: Run test to verify behavior**

Run: `.venv/bin/pytest tests/test_fcpxml_generator.py::test_fcpxml_asset_uri_rfc3986_with_spaces -v`
Expected: Verify failure or absence of RFC 3986 encoding.

- [ ] **Step 3: Update `src/core/fcpxml_generator.py` and create `src/core/xml_exporter.py`**

In `src/core/fcpxml_generator.py`:
- Replace manual URL formatting (lines 433-437, 471-472, 748-753) with `PosixPathAdapter.to_fcpxml_uri(path)`.
- Ensure all `<asset id="..." src="...">` and `<pathurl>` use the RFC 3986 URI format.
In `src/core/xml_exporter.py`:
- Re-export `FCPXMLGenerator` as `XMLExporter` and `FCPXMLBuilder` for architecture conformity.

- [ ] **Step 4: Run all FCPXML tests to ensure regression-free behavior**

Run: `.venv/bin/pytest tests/test_fcpxml_generator.py -v`
Expected: ALL 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/fcpxml_generator.py src/core/xml_exporter.py tests/test_fcpxml_generator.py
git commit -m "feat(xml): standardize FCPXML and FCP7 XML asset URIs using PosixPathAdapter"
```

---

### Task 3: Cải tiến kết nối Resolve (`src/core/resolve_api.py` & `src/core/resolve_bridge.py`)

**Files:**
- Create: `src/core/resolve_bridge.py`
- Modify: `src/core/resolve_api.py`
- Test: `tests/test_resolve_bridge.py`

**Interfaces:**
- Produces:
  ```python
  def load_resolve_api() -> Tuple[Optional[Any], str]:
      """
      Configures environment variables, preloads libfusionscript.so via ctypes.CDLL,
      dynamically imports DaVinciResolveScript, and returns (resolve_app, status_message).
      """
  ```

- [ ] **Step 1: Write unit test for `load_resolve_api` and `ResolveBridge`**

```python
# tests/test_resolve_bridge.py
from src.core.resolve_bridge import load_resolve_api, ResolveBridge

def test_load_resolve_api_env_setup():
    app, msg = load_resolve_api()
    # When DaVinci Resolve is not running, app is None but error message is descriptive
    assert app is None or hasattr(app, "GetProjectManager")
    assert isinstance(msg, str)

def test_resolve_bridge_instance():
    bridge = ResolveBridge()
    assert hasattr(bridge, "connect")
    assert hasattr(bridge, "is_connected")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_resolve_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.resolve_bridge'`

- [ ] **Step 3: Implement `src/core/resolve_bridge.py` and integrate with `src/core/resolve_api.py`**

- In `src/core/resolve_bridge.py`:
  - Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB`.
  - On Linux, if `/opt/resolve/libs/Fusion/fusionscript.so` exists, preload with `ctypes.CDLL(..., mode=ctypes.RTLD_GLOBAL)`.
  - Inject `/opt/resolve/Developer/Scripting/Modules` into `sys.path`.
  - Import `DaVinciResolveScript as dvr_script` inside `load_resolve_api()`, never at top-level.
- In `src/core/resolve_api.py`:
  - Delegate `connect()` to `load_resolve_api()` while preserving `ResolveAutomation` methods.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_resolve_bridge.py tests/test_resolve_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/resolve_bridge.py src/core/resolve_api.py tests/test_resolve_bridge.py
git commit -m "feat(resolve): introduce resolve_bridge with ctypes preloading and dynamic API loader"
```

---

### Task 4: Cập nhật trình quản lý cài đặt LUTs & Fusion Templates (`src/core/asset_manager.py`)

**Files:**
- Create: `src/core/asset_manager.py`
- Modify: `src/core/text_preset.py`
- Modify: `src/core/transition_preset.py`
- Modify: `src/ui/tabs/tab_assets.py`
- Test: `tests/test_asset_manager.py`

**Interfaces:**
- Produces:
  ```python
  class AssetManager:
      @staticmethod
      def get_fusion_template_dir(category: str = "Titles") -> Path: ...
      @staticmethod
      def get_lut_install_dir() -> Path: ...
      @staticmethod
      def install_setting_file(src_path: Path, category: str, name: str) -> Path: ...
      @staticmethod
      def install_lut_file(src_path: Path, name: str) -> Path: ...
  ```

- [ ] **Step 1: Write unit tests for `AssetManager` paths and installation**

```python
# tests/test_asset_manager.py
from pathlib import Path
from src.core.asset_manager import AssetManager

def test_fusion_template_dir_linux():
    titles_dir = AssetManager.get_fusion_template_dir("Titles")
    assert isinstance(titles_dir, Path)
    assert "ResolveFlow" in str(titles_dir)
    assert "Titles" in str(titles_dir)

def test_lut_install_dir_linux():
    lut_dir = AssetManager.get_lut_install_dir()
    assert isinstance(lut_dir, Path)
    assert "ResolveFlow" in str(lut_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_asset_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.asset_manager'`

- [ ] **Step 3: Implement `src/core/asset_manager.py`**

- Target setting directory on Linux: `~/.local/share/DaVinciResolve/Fusion/Templates/Edit/{Category}/ResolveFlow/`
- Target LUT directory on Linux: `/var/BlackmagicDesign/DaVinci Resolve/Support/LUT/ResolveFlow/` with fallback to `~/.local/share/DaVinciResolve/LUT/ResolveFlow/` and `/opt/resolve/LUT/ResolveFlow/` (checking `os.access(..., os.W_OK)`).
- Update `src/core/text_preset.py` and `src/core/transition_preset.py` and `src/ui/tabs/tab_assets.py` to use `AssetManager`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_asset_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/asset_manager.py src/core/text_preset.py src/core/transition_preset.py src/ui/tabs/tab_assets.py tests/test_asset_manager.py
git commit -m "feat(assets): centralize Fusion template and LUT installation in AssetManager for Linux"
```

---

### Task 5: Tối ưu hóa `QMediaPlayer` và `QDrag` cho môi trường Linux (`src/main.py` & `src/ui/`)

**Files:**
- Modify: `src/main.py`
- Modify: `src/ui/tabs/tab_assets.py`
- Modify: `src/ui/tabs/tab_export.py`
- Modify: `src/ui/tabs/tab_copilot.py`
- Modify: `src/ui/tabs/tab_sfx.py`
- Test: `tests/test_ui.py` & manual launch test

**Interfaces:**
- Environment setup: `QT_QPA_PLATFORM=xcb` & `QT_MEDIA_BACKEND=ffmpeg` before `QApplication(sys.argv)`.
- Player sources: `QMediaPlayer.setSource(QUrl.fromLocalFile(str(PosixPathAdapter.resolve_path(path))))`.
- Drag and drop: `QMimeData.setUrls([QUrl.fromLocalFile(...)])` and `drag.exec(Qt.DropAction.CopyAction)`.

- [ ] **Step 1: Check environment variables and drag calls in UI**

Inspect `src/main.py` to ensure `os.environ.setdefault("QT_QPA_PLATFORM", "xcb")` and `os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")` are in place.

- [ ] **Step 2: Update `src/main.py`**

Add environment setup prior to `from PySide6.QtWidgets import QApplication` and `QApplication(sys.argv)`.

- [ ] **Step 3: Update `QMediaPlayer.setSource()` and `QDrag.exec()` across UI tabs**

- Replace any `drag.exec_(Qt.CopyAction)` with `drag.exec(Qt.DropAction.CopyAction)` in `tab_assets.py` and `tab_export.py`.
- Wrap all `setSource` calls with `PosixPathAdapter.to_local_qurl(path)` ensuring `QUrl.fromLocalFile(str(resolved_path))` is used everywhere.

- [ ] **Step 4: Run UI test suite**

Run: `.venv/bin/pytest tests/test_ui.py tests/test_studio_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py src/ui/tabs/ tests/
git commit -m "feat(ui): configure Qt6 xcb/ffmpeg media backends and standardize QUrl/QDrag for Linux"
```

---

### Task 6: Xử lý giới hạn Codec AAC của DaVinci Resolve Linux (`src/core/ffmpeg_utils.py` & `src/core/audio_processor.py`)

**Files:**
- Create: `src/core/ffmpeg_utils.py`
- Create: `src/core/audio_processor.py`
- Modify: `src/core/autocut.py` (integrate Linux AAC warning / auto-conversion)
- Modify: `src/core/fcpxml_generator.py` (allow pairing PCM WAV audio tracks on Linux)
- Test: `tests/test_audio_processor.py`

**Interfaces:**
- Produces:
  ```python
  def get_ffmpeg_path() -> Optional[str]: ...
  def get_ffprobe_path() -> Optional[str]: ...
  def inspect_audio_codec(media_path: Union[str, Path]) -> Optional[str]: ...
  def is_linux_aac_conflict(media_path: Union[str, Path]) -> bool: ...
  def extract_pcm_wav(video_path: Union[str, Path], output_wav: Optional[Union[str, Path]] = None) -> Path: ...
  def remux_to_mov_pcm(video_path: Union[str, Path], output_mov: Optional[Union[str, Path]] = None) -> Path: ...
  ```

- [ ] **Step 1: Write unit tests for audio codec inspection and AAC handling**

```python
# tests/test_audio_processor.py
from unittest.mock import patch, MagicMock
from src.core.audio_processor import AudioProcessor, inspect_audio_codec, is_linux_aac_conflict

def test_inspect_audio_codec_mock():
    mock_probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"}
        ]
    }
    with patch("ffmpeg.probe", return_value=mock_probe):
        codec = inspect_audio_codec("mock_video.mp4")
        assert codec == "aac"

def test_is_linux_aac_conflict_mock():
    with patch("src.core.audio_processor.inspect_audio_codec", return_value="aac"), \
         patch("sys.platform", "linux"):
        assert is_linux_aac_conflict("mock_video.mp4") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_audio_processor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.audio_processor'`

- [ ] **Step 3: Implement `src/core/ffmpeg_utils.py` and `src/core/audio_processor.py`**

- `ffmpeg_utils.py`:
  - Locate `ffmpeg` and `ffprobe` using `shutil.which`. No `.exe` hardcoding.
  - Safe subprocess wrappers.
- `audio_processor.py`:
  - `inspect_audio_codec(path)`: extracts audio stream codec using ffprobe.
  - `is_linux_aac_conflict(path)`: returns True if on Linux and codec is `aac`.
  - `extract_pcm_wav(video_path, output_wav=None)`: produces 48000Hz 16-bit PCM WAV (`-c:a pcm_s16le -ar 48000`).
  - `remux_to_mov_pcm(video_path, output_mov=None)`: remuxes container copying video and converting audio to PCM (`-c:v copy -c:a pcm_s16le -ar 48000`).

- [ ] **Step 4: Connect AAC resolution to FCPXML generation and pipeline**

In `fcpxml_generator.py` and `autocut.py`:
- When building timeline events on Linux: if `is_linux_aac_conflict(clip_path)` is True, automatically generate or link the accompanying 48kHz PCM WAV track as audio asset so DaVinci Resolve on Linux has sound on the timeline.

- [ ] **Step 5: Run tests to verify all pass**

Run: `.venv/bin/pytest tests/test_audio_processor.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/core/ffmpeg_utils.py src/core/audio_processor.py tests/test_audio_processor.py src/core/fcpxml_generator.py
git commit -m "feat(audio): resolve DaVinci Resolve Linux AAC codec issue with automatic PCM extraction"
```

---

### Task 7: Full System Verification and Regression Testing

- [ ] **Step 1: Run complete test suite**
Run: `.venv/bin/pytest tests/ -v`
Expected: All non-ffmpeg dependent tests PASS (and when ffmpeg is installed, 100% PASS).

- [ ] **Step 2: Check `test_bridge.py`**
Run: `.venv/bin/python test_bridge.py`
Expected: Clean load of `DaVinciResolveScript` via `resolve_bridge` without errors.

- [ ] **Step 3: Final sanity check on launch script**
Run: `./run_resolveflow.sh --help` or check syntax with `bash -n run_resolveflow.sh`.
