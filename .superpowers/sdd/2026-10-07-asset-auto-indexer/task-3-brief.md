# Task 3: Route Real Files to DaVinci Actions

**Goal:** Modify `tab_assets.py` to seamlessly route real asset file paths to DaVinci Resolve interactions (Drag & Drop, Insert at Playhead).

**Context:** The UI now displays real assets (SFX, LUTs, Macros). However, the DaVinci interaction buttons (`_create_drag_mime_data`, `_insert_at_playhead`) currently use programmatic mock code (generating silent WAVs or minimal XML/Settings on the fly) if `file_path` is not properly handled. We need to prioritize routing the real files.

**Requirements & Global Constraints:**
- Must remain 100% compatible with DaVinci Resolve Free via Drag & Drop.
- Do not remove the old mock generation code entirely; keep it as a fallback in case a real `file_path` is somehow missing.

**Steps to Execute:**
1. In `src/ui/tabs/tab_assets.py`, update `_create_drag_mime_data`:
   - Check if `getattr(asset, "file_path", "")` exists and is a valid file.
   - If yes, use `mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(asset.file_path))])` and return immediately.
2. Update `_insert_at_playhead`:
   - For `is_sfx`: Prioritize inserting the actual file in `getattr(self.selected_asset, "file_path", "")` into track A2.
   - For `is_lut`: Apply the actual `file_path` LUT.
   - For Memes/Overlays: Log that it's requested or implement insertion to V3 (using generic `insert_media_at_playhead` if it exists in ResolveAutomation, else emit).
3. Update `_install_to_fusion`:
   - If the selected asset has a valid `.setting` `file_path`, copy it (using `shutil.copy2`) directly to the DaVinci template directory (`%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Templates\Edit\Titles\` or `Transitions\`) instead of exporting a generated macro.
4. Run tests: `pytest tests/test_tab_assets.py tests/test_ui.py`.
5. Commit with message: `feat(assets): route real asset file paths to DaVinci insertion and drag-drop logic`.

**Deliverable:** DaVinci interaction logic is fully wired to real files. Write findings to `task-3-report.md`. Reply DONE.
