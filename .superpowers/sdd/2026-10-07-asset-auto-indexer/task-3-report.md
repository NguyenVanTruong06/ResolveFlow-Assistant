# Task 3 Report: Route Real Files to DaVinci Actions

## Implementation Details
- Updated `_create_drag_mime_data` to check `getattr(asset, "file_path", "")` and immediately return the local path of the asset using `QUrl.fromLocalFile(os.path.abspath(file_path))` if valid.
- Updated `_insert_at_playhead` to prioritize using the actual file path for `sfx` and `lut`. For `meme` and `overlay`, added insertion to track 3 (V3) by calling `resolve_auto.insert_media_at_playhead(media_path=..., target_track=3)` if available.
- Updated `_install_to_fusion` to copy `.setting` files directly to the respective Resolve Fusion template directory using `shutil.copy2` if `file_path` is provided.

## Test Results
- Ran `python -m pytest tests/test_tab_assets.py tests/test_ui.py`
- All 55 tests passed successfully.

## Commit
- Committed with message: `feat(assets): route real asset file paths to DaVinci insertion and drag-drop logic`.
