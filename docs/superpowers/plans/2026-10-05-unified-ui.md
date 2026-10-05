# Unified Asset Hub (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a centralized "Asset Hub" UI (`tab_assets.py`) that merges Titles, Transitions, LUTs, and SFX into a single grid with left-navigation categories, mimicking professional asset managers.

**Architecture:** Create `TabAssets` in PySide6. It will feature a left sidebar for category filtering (All, Titles, Transitions, LUTs, SFX). The center will dynamically populate with `VisualTechniqueCard` (reused/expanded from `tab_titles.py`) or similar asset cards depending on the category. 

**Tech Stack:** Python 3, PySide6

**Spec:** `docs/superpowers/specs/2026-10-05-unified-asset-manager-design.md`

## Global Constraints
- Do not break existing backend modules (LUT generators, SFX lists).
- Ensure the Drag & Drop workflow remains intact for Free users.

## Review Focus
- **Layout breaking on resize**: Ensure `QGridLayout` responsive wrapping works.
- **Lost functionality**: The transition must not lose the "Install to Resolve" button previously available in `tab_titles.py`.

---

### Task 1: Create the Unified TabAssets UI

**Files:**
- Create: `src/ui/tabs/tab_assets.py`
- Modify: `tests/test_ui.py` (add a basic instantiation test for TabAssets)

**Interfaces:**
- Consumes: `TextPresetManager`, `TransitionMacroGenerator`, `LUTGenerator`
- Produces: `TabAssets` QWidget.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_ui.py
from src.ui.tabs.tab_assets import TabAssets
from PySide6.QtWidgets import QApplication

def test_tab_assets_creation(qtbot):
    tab = TabAssets()
    qtbot.addWidget(tab)
    assert tab is not None
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_ui.py::test_tab_assets_creation`
Expected: FAIL (ModuleNotFoundError for `tab_assets.py`)

- [ ] **Step 3: Implement `TabAssets` in `src/ui/tabs/tab_assets.py`**
Implement the unified layout. Move the logic from `tab_titles.py` here and expand it to also load `BUILTIN_TRANSITIONS`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_ui.py`

- [ ] **Step 5: Commit**
```bash
git add src/ui/tabs/tab_assets.py tests/test_ui.py
git commit -m "feat(ui): create unified TabAssets replacing fragmented tabs"
```

### Task 2: Integrate TabAssets into Main Window

**Files:**
- Modify: `src/ui/main_window.py`

**Interfaces:**
- Consumes: `TabAssets`
- Produces: Replaces `TabTitles`, `TabExport` (LUT part), etc., with `TabAssets`.

- [ ] **Step 1: Modify `main_window.py`**
Remove old fragment tabs and add `TabAssets` as a prominent tab (e.g., Tab 2: Thư Viện Tài Nguyên / Asset Hub).

- [ ] **Step 2: Run application test**
Run: `python -m pytest tests/test_ui.py` or manually verify `python src/main.py` launches without crashes.

- [ ] **Step 3: Commit**
```bash
git add src/ui/main_window.py
git commit -m "refactor(ui): integrate unified TabAssets into MainWindow"
```
