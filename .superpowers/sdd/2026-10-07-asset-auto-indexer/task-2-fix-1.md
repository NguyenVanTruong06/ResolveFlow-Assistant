# Task 2 - Fix Round 1

**Review Findings to Address:**
1. **Important (Grid Breaking):** `QPixmap.scaled(160, 80, Qt.KeepAspectRatioByExpanding)` scales the image but does not crop it. Wide images will cause the `QLabel` to expand past 160px, destroying the grid layout in the UI. You must scale AND crop to exactly 160x80 (e.g., center crop).
2. **Minor (Aesthetic Bug):** `lbl.setStyleSheet("border-radius: 8px; ...")` on a `QLabel` with a set `QPixmap` does not actually clip the image corners in Qt. The square corners will stick out. You must render the `QPixmap` with a `QPainterPath` clipping path to achieve true rounded corners.

**Steps to Execute:**
1. In `src/ui/tabs/tab_assets.py` (inside `AssetCard` where the thumbnail is rendered), fix the `QPixmap` rendering logic.
2. Load the `QPixmap(thumbnail_path)`.
3. Create a target empty `QPixmap` of size 160x80 with `Qt.transparent`.
4. Use `QPainter` on the empty pixmap, add a `QPainterPath.addRoundedRect(0, 0, 160, 80, 8, 8)`, set it as clip path.
5. Scale the source image using `Qt.KeepAspectRatioByExpanding`, calculate the center offset, and draw it into the painter. This gives a perfect 160x80 center-cropped image with native rounded corners.
6. Run `pytest tests/test_tab_assets.py`.
7. Commit with message: `fix(assets): correctly crop and apply rounded corners to asset thumbnails to prevent grid breakage`.
8. Reply DONE.
