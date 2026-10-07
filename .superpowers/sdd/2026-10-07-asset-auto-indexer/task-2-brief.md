# Task 2: Integrate AssetIndexer into TabAssets

**Goal:** Modify `TabAssets` to load actual data using `AssetIndexer` instead of static mocks.

**Context:** Now that `AssetIndexer` works, we must inject its data into the UI. Currently, `MOCKUP_SFX`, `MOCKUP_LUTS`, `MOCKUP_MEMES`, `MOCKUP_ICONS`, `MOCKUP_OVERLAYS` are used. Also, `BUILTIN_PRESETS` and `BUILTIN_TRANSITIONS` are loaded statically.

**Global Constraints & Interfaces:**
- DaVinci Resolve Free compatibility (via Drag & Drop).
- Preserve existing standard UI. Do not break the UI grid.

**Steps to Execute:**
1. In `src/ui/tabs/tab_assets.py`, update `StudioAsset` initialization signature (or data fields) to accept `file_path: str = ""` and `thumbnail_path: str = ""`.
2. Import `AssetIndexer` from `src.core.asset_indexer`.
3. In `TabAssets.__init__`, replace static `MOCKUP_` lists with calls to `AssetIndexer`. Combine `BUILTIN_PRESETS` with the dynamic titles returned by `AssetIndexer.scan_titles()`. Do the same for transitions. For Memes/SFX/LUTs, populate `self.all_memes`, `self.all_sfx`, `self.all_luts` primarily from the indexer. Handle fallback gracefully (if empty, keep the UI working).
4. Update UI thumbnail renderers (`_render_text_thumbnail`, `_render_lut_thumbnail`, `_render_sfx_thumbnail`, `_render_transition_thumbnail`, etc.). If `thumbnail_path` exists on the asset and is a valid file, load it using `QPixmap` and draw it directly, bypassing programmatic drawing.
5. Run tests: `pytest tests/test_tab_assets.py tests/test_ui.py`. Fix any failing tests related to missing attributes.
6. Commit with message: `feat(assets): integrate AssetIndexer into TabAssets UI`.

**Deliverable:** UI perfectly integrates real asset files. Write findings to `task-2-report.md`. Reply DONE.
