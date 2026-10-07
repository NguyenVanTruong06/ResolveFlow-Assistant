# Unified Asset Auto-Indexer Spec

## 1. Goal
Replace hardcoded Python mockup data for DaVinci Resolve assets (SFX, LUTs, Titles, Transitions, Memes) with a dynamic Local Auto-Indexer that scans the `assets/` directory for actual `.wav`, `.cube`, `.mp4`, and `.setting` files, along with `.png` thumbnails.

## 2. Context
Currently, `tab_assets.py` uses hardcoded lists (`MOCKUP_SFX`, `MOCKUP_LUTS`, `MOCKUP_MEMES`) and generative preset classes (`BUILTIN_PRESETS`, `BUILTIN_TRANSITIONS`). While generative classes are useful, real-world professional usage requires importing complex, hand-crafted `.setting` files (Fusion Macros) and real media files. The UI is built and beautiful, but it needs to reflect the real data on disk.

## 3. Architecture & Approach
We will implement an `AssetIndexer` utility that scans the `assets/` directory tree.
- **Directory Structure Expected**:
  - `assets/sfx/` (*.wav, *.mp3)
  - `assets/luts/` (*.cube)
  - `assets/broll_memes/memes/` (*.mp4, *.mov)
  - `assets/templates/titles/` (*.setting + *.png thumbnail)
  - `assets/templates/transitions/` (*.setting + *.png thumbnail)
- **Scanning Logic**:
  - For each category, the indexer scans the respective folder.
  - It creates `StudioAsset` instances for each found file.
  - If a file `Vlog.setting` exists, it looks for `Vlog.png` or `Vlog.jpg` in the same folder to use as the thumbnail icon (or extracts metadata if possible, but fallback to default icon).
  - The ID of the asset is the file name (without extension) or a clean hash. The `file_path` is the absolute path to the file.
- **Integration**:
  - In `tab_assets.py`, instead of `self.all_luts = MOCKUP_LUTS`, we call `AssetIndexer.scan_luts()`.
  - Same for Memes, SFX, Titles, and Transitions.
  - Update `_insert_at_playhead` and `_create_drag_mime_data` to check if `getattr(self.selected_asset, 'file_path')` exists, and if so, use that real file instead of generating a dummy one.
  - For `.setting` files, `_create_drag_mime_data` will simply point the `QUrl` to the existing `.setting` file, allowing native Drag & Drop into DaVinci Resolve Free.

## 4. Global Constraints
- Must remain 100% compatible with DaVinci Resolve Free via Drag & Drop.
- Python 3.11+. Standard library `os`, `glob`, `pathlib`.
- UI must remain stable; if `assets/` folders are missing, handle gracefully (e.g., return empty list or fallback to built-ins).

## 5. Implementation Tasks
1. Create `src/core/asset_indexer.py`.
2. Update `src/ui/tabs/tab_assets.py` to use `AssetIndexer`.
3. Verify Drag & Drop MIME data routing for real `.setting`, `.cube`, `.wav`, and `.mp4` files.
