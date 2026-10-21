# Fast-Paste Fusion & Native Drag-Drop to Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Akitt Pro's two signature automation features in ChunDVC:
1. **FAST-FUSION Auto-Paste**: Read preset Lua/Macro code, inject into system clipboard, focus DaVinci Resolve window, and automatically simulate `Ctrl+V` on the Fusion page.
2. **Native Drag & Drop to Timeline**: Enable dragging `MacroCard` presets from ChunDVC directly onto the DaVinci Resolve window, detecting the release position via `QCursor.pos()` & `ResolveWindowAnchor`, and automatically inserting the preset at the playhead with 0-ripple nested timeline engine.

**Architecture:**
- Create `src/core/fast_paste.py` for cross-platform DaVinci window activation (`SetForegroundWindow` on Windows, `xdotool` / `wmctrl` on Linux) and clipboard + hotkey paste simulation.
- Create `src/ui/lux/drag_drop_tracker.py` to detect when a drag release occurs over the DaVinci Resolve window boundary.
- Upgrade `MacroCard` in `src/ui/lux/macro_card.py` to support drag drop-on-Resolve detection and a right-click / button trigger for Fast-Paste Fusion.
- Integrate into `TabAssets` (`src/ui/tabs/tab_assets.py`) and `ChunDVCApp` (`src/ui/app.py`).

**Tech Stack:**
- Python 3.10+
- PySide6 (Qt6): `QDrag`, `QMimeData`, `QCursor`, `QApplication.clipboard()`
- Platform Window Tools: Linux (`xdotool`, `wmctrl`) & Windows (`ctypes.windll.user32`)

## Global Constraints
- Preserve 100% of existing core logic and signals in `src/ui/tabs/` and `src/core/`.
- All automation must be safe against failures (e.g. if DaVinci Resolve is not found or not in foreground, fall back to copying to clipboard and displaying toast).
- Drag & Drop must be non-blocking and smoothly trigger `import_edit_preset_v2`.

---

### Task 1: Fast-Paste into Fusion Engine (`src/core/fast_paste.py`)

**Files:**
- Create: `src/core/fast_paste.py`
- Test: `tests/test_fast_paste.py`

- [ ] **Step 1.1**: Write unit tests in `tests/test_fast_paste.py`:
  - `focus_davinci_resolve`: mocked Linux/Windows execution.
  - `simulate_paste_hotkey`: mocked subprocess/hotkey.
  - `fast_paste_to_fusion`: verifies clipboard update, focus call, and paste trigger.
- [ ] **Step 1.2**: Implement `src/core/fast_paste.py`:
  - `focus_davinci_resolve() -> bool`
  - `simulate_paste_hotkey() -> bool`
  - `fast_paste_to_fusion(macro_code: str, resolve_app=None, log_callback=None) -> Dict[str, Any]`
- [ ] **Step 1.3**: Run `pytest tests/test_fast_paste.py` and confirm 100% pass.

---

### Task 2: Drag & Drop Over Resolve Detection (`src/ui/lux/drag_drop_tracker.py`)

**Files:**
- Create: `src/ui/lux/drag_drop_tracker.py`
- Test: `tests/test_drag_drop_tracker.py`

- [ ] **Step 2.1**: Write unit tests in `tests/test_drag_drop_tracker.py`:
  - `is_point_inside_rect(point, rect)` geometry math.
  - `is_mouse_over_resolve(pos)` with mocked window rect.
- [ ] **Step 2.2**: Implement `src/ui/lux/drag_drop_tracker.py`:
  - `is_point_inside_rect(point: QPoint, rect: Tuple[int, int, int, int]) -> bool`
  - `is_mouse_over_resolve(global_pos: Optional[QPoint] = None) -> bool`
- [ ] **Step 2.3**: Run `pytest tests/test_drag_drop_tracker.py` and confirm 100% pass.

---

### Task 3: Upgrade `MacroCard` & `MacroGridWidget` (`src/ui/lux/macro_card.py`)

**Files:**
- Modify: `src/ui/lux/macro_card.py`
- Test: `tests/test_macro_card.py`

- [ ] **Step 3.1**: Add `dropped_on_resolve = pyqtSignal(str)` and `fast_paste_requested = pyqtSignal(str)` to `MacroCard`.
- [ ] **Step 3.2**: In `mouseMoveEvent`, when drag finishes, inspect `QCursor.pos()` with `is_mouse_over_resolve()`. If True, emit `dropped_on_resolve(preset_id)`.
- [ ] **Step 3.3**: Add right-click context menu to `MacroCard` with "⚡ Dán nhanh vào Fusion (Ctrl+V)" and "📥 Chèn tại Playhead".
- [ ] **Step 3.4**: Run `pytest tests/test_macro_card.py` and verify all tests pass.

---

### Task 4: UI & App Integration (`src/ui/tabs/tab_assets.py` & `src/ui/app.py`)

**Files:**
- Modify: `src/ui/tabs/tab_assets.py`
- Modify: `src/ui/app.py`
- Test: `tests/test_lux_integration.py`

- [ ] **Step 4.1**: In `TabAssets`, add button `btn_fast_paste` ("⚡ Dán Fusion (Ctrl+V)") to the inspector toolbar.
- [ ] **Step 4.2**: In `ChunDVCApp`, wire `self.drawer_grid.preset_dropped_on_resolve` to `self._on_insert_title_at_playhead` and `fast_paste_requested` to `fast_paste_to_fusion`.
- [ ] **Step 4.3**: Run all test suites and verify 100% pass.
