# Asset Auto-Indexer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an `AssetIndexer` that dynamically scans the `assets/` directory for real `.setting`, `.wav`, `.cube`, and `.mp4` files and populates the `TabAssets` UI.

**Architecture:** A new core utility `AssetIndexer` will recursively scan defined directories for supported extensions, returning instances of `StudioAsset`. `TabAssets` will be updated to load these dynamic lists instead of static mockups. `tab_assets.py` will route these real file paths into the Drag & Drop and DaVinci API methods.

**Tech Stack:** Python 3.11, pathlib, PySide6

**Spec:** `docs/superpowers/specs/2026-10-07-asset-auto-indexer.md`

## Global Constraints

- Must remain 100% compatible with DaVinci Resolve Free via Drag & Drop.
- Python 3.11+. Standard library `os`, `glob`, `pathlib`.
- UI must remain stable; if `assets/` folders are missing, handle gracefully.

## Review Focus

- Empty `assets/` directory: Should not crash the UI, should display empty lists or fallback to built-in presets.
- Missing `.png` thumbnails for `.setting` files: Should fallback to a generic placeholder icon.
- Special characters in filenames: IDs and file paths must handle Vietnamese Unicode safely.
- MIME Data generation: Must properly resolve absolute paths for existing files instead of generating temporary ones when `file_path` is provided.

---

### Task 1: Create Asset Indexer Core

**Files:**
- Create: `src/core/asset_indexer.py`
- Modify: `tests/test_asset_indexer.py` (Create new test file)

**Requirements:**
- Implement `AssetIndexer` class with classmethods: `scan_sfx()`, `scan_luts()`, `scan_memes()`, `scan_titles()`, `scan_transitions()`.
- Each method accepts an optional `base_path` defaulting to `"assets"`.
- It dynamically maps file extensions (e.g. `.wav` -> SFX, `.cube` -> LUT, `.setting` -> Title/Transition).
- It extracts a clean name from the filename.
- For `.setting` files, it checks if an image with the same name (e.g. `Vlog.png`) exists in the same folder, returning it as the `thumbnail_path` or `svg_path`.
- Returns a list of dictionaries or an agreed interface that `TabAssets` can consume to build `StudioAsset` instances.
- Ensure the directories `assets/templates/titles/` and `assets/templates/transitions/` are created if they don't exist.

**Steps:**
- [ ] Create `src/core/asset_indexer.py` and implement the directory scanning logic.
- [ ] Add `os.makedirs` for the expected asset directories so they exist.
- [ ] Write `tests/test_asset_indexer.py` with dummy files in a temp directory to verify scanning logic.
- [ ] Run `pytest tests/test_asset_indexer.py`.
- [ ] Commit with message: "feat(core): implement AssetIndexer for scanning local DaVinci templates"

---

### Task 2: Integrate Indexer into TabAssets

**Files:**
- Modify: `src/ui/tabs/tab_assets.py`
- Modify: `tests/test_tab_assets.py`

**Requirements:**
- In `TabAssets.__init__`, replace the direct assignment of `MOCKUP_SFX`, `MOCKUP_LUTS`, `MOCKUP_MEMES` with calls to `AssetIndexer`.
- Combine built-in presets (`BUILTIN_PRESETS`) with the dynamically scanned `.setting` Titles/Transitions. The `StudioAsset` wrapper should accept a `file_path` attribute.
- Ensure `StudioAsset` class is updated to hold `file_path` and `thumbnail_path`.
- Update `_render_text_thumbnail`, `_render_transition_thumbnail`, etc. to display the `thumbnail_path` image if provided, bypassing the programmatic drawing if a real thumbnail exists.

**Steps:**
- [ ] Update `StudioAsset` definition in `tab_assets.py` to include `file_path: str = ""` and `thumbnail_path: str = ""`.
- [ ] Import `AssetIndexer` into `tab_assets.py`.
- [ ] Modify `load_assets` or `__init__` to fetch from `AssetIndexer`.
- [ ] Update the UI renderers (`_render_text_thumbnail`, etc.) to draw a `QPixmap` if `thumbnail_path` is present.
- [ ] Run `pytest tests/test_tab_assets.py tests/test_ui.py`.
- [ ] Commit with message: "feat(assets): integrate AssetIndexer into TabAssets UI"

---

### Task 3: Route Real Files to Drag & Drop / DaVinci Actions

**Files:**
- Modify: `src/ui/tabs/tab_assets.py`

**Requirements:**
- Update `_create_drag_mime_data`: If the selected asset has a valid `file_path` on disk, construct the `QUrl.fromLocalFile` using that absolute path instead of triggering the fallback generator.
- Update `_insert_at_playhead`: If inserting SFX, LUT, or Meme, use the real `file_path`.
- For `_install_to_fusion`: If the asset has a `.setting` `file_path`, copy it directly to `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Templates\Edit\Titles\` (or Transitions) instead of exporting from the Python generator.

**Steps:**
- [ ] Update `_create_drag_mime_data` to check for `getattr(asset, "file_path", "")`.
- [ ] Update `_insert_at_playhead` logic to use the real file path.
- [ ] Update `_install_to_fusion` to perform a direct `shutil.copy2` when `file_path` is a valid `.setting` file.
- [ ] Run `pytest tests/test_tab_assets.py tests/test_ui.py`.
- [ ] Run full test suite `pytest tests/`.
- [ ] Commit with message: "feat(assets): route real asset file paths to DaVinci insertion and drag-drop logic"
