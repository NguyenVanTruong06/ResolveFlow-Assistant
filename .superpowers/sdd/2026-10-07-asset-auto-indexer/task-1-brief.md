# Task 1: Create Asset Indexer Core

**Goal:** Implement `AssetIndexer` utility to scan local asset directories.

**Context:** We are moving from hardcoded lists to a local folder-scanning approach for assets. We need a core utility that scans `assets/sfx/`, `assets/luts/`, `assets/broll_memes/memes/`, `assets/templates/titles/`, and `assets/templates/transitions/` to find valid files (`.wav`, `.cube`, `.mp4`, `.setting`) and their thumbnails (`.png`).

**Requirements & Global Constraints:**
- Must remain 100% compatible with DaVinci Resolve Free via Drag & Drop.
- Python 3.11+. Use `os`, `glob`, `pathlib`.
- Handle Unicode/Vietnamese filenames correctly.
- Return structured data (list of dicts) with at least `id`, `name`, `file_path`, `thumbnail_path`.

**Steps to Execute:**
1. Create `src/core/asset_indexer.py`.
2. Implement class `AssetIndexer` with `scan_sfx()`, `scan_luts()`, `scan_memes()`, `scan_titles()`, `scan_transitions()`. Use `os.makedirs(..., exist_ok=True)` inside these methods (or in an `ensure_dirs()` method) so the folders exist. For `.setting` files, look for a `.png` or `.jpg` of the same base name for the thumbnail.
3. Write a small test `tests/test_asset_indexer.py` verifying that scanning works on a temporary directory structure.
4. Run tests: `pytest tests/test_asset_indexer.py`.
5. Commit with message: `feat(core): implement AssetIndexer for scanning local DaVinci templates`.

**Deliverable:** Working `AssetIndexer` class, tests passing, and clean git working tree. Write your findings to `task-1-report.md`.
