# Floating Edge Drawer & Luxury Dark UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Floating Edge Drawer & Luxury Dark UI system for ChunDVC (learning from Akitt Pro), featuring micro-interactions (`RippleOverlay`, `SegmentIndicator`, dark glassmorphism styling), a collapsible/expandable `EdgeDrawerWindow` with `EdgeRevealHotspot`, a 2-column `MacroCard` grid with live hover preview and drag-and-drop/double-click insertion, cross-platform `ResolveWindowAnchor`, and a toggle mode in `ChunDVCApp`.

**Architecture:** 
- Keep all existing business logic intact in `src/ui/tabs/` (`TabCopilot`, `TabAutoCut`, `TabAssets`, `TabSFX`, `TabExport`).
- Create an independent subsystem package at `src/ui/lux/` with 4 core modules:
  1. `lux_effects.py`: Micro-interaction primitives (Ripple, animated SegmentIndicator, Dark Glassmorphism QSS).
  2. `macro_card.py`: 16:9 preset cards with live hover preview (>150ms) and timeline insertion.
  3. `window_anchor.py`: Cross-platform DaVinci Resolve window coordinate detector and anchor tracker.
  4. `panel_edge_rail.py`: Non-blocking frameless floating drawer with collapsed rail, reveal hotspot, and expanded tab host.
- Provide a clean two-way toggle mode in `ChunDVCApp` (`src/ui/app.py`) allowing instant switching between standard window and edge drawer without interrupting active state or pipelines.

**Tech Stack:**
- Python 3.10+
- PySide6 (Qt6): `QWidget`, `QPropertyAnimation`, `QVariantAnimation`, `QEasingCurve.Type.OutCubic`, `QPainter`, `QPainterPath`, `QGraphicsDropShadowEffect`, `QDrag`, `QMimeData`
- Linux (`wmctrl`, `xdotool`, `procfs`) & Windows (`ctypes.windll.user32`)

**Spec:** User brief for [TASK] NÂNG CẤP GIAO DIỆN CHUNDVC THÀNH DẠNG "FLOATING EDGE DRAWER & LUXURY DARK UI" (HỌC HỎI TỪ AKITT PRO).

## Global Constraints
- Preserve 100% of existing core logic and signals in `src/ui/tabs/`.
- All animations must be non-blocking using `QPropertyAnimation` / `QVariantAnimation` with `QEasingCurve.Type.OutCubic`. No Qt event-loop blocking loops.
- Module independence: all new UI code lives cleanly in `src/ui/lux/`.
- Styling follows luxury dark glassmorphism: background `rgba(14, 17, 22, 0.95)`, border `1px solid rgba(255, 255, 255, 0.08)`, corner radius 9px-12px, font `"Segoe UI Variable Text", "Segoe UI", sans-serif`.

## Review Focus
1. **Headless / Linux CI Compatibility**: Qt widget tests must run cleanly under `offscreen` platform without crashing when OpenGL/GLX is uninitialized.
2. **Window Collision Safety**: `EdgeDrawerWindow` frameless / stays-on-top flags must not lock desktop focus or prevent closing.
3. **Hover Timer Leak Prevention**: `MacroCard` hover preview timers must stop cleanly on mouse leave and widget destruction.
4. **Anchor Detection Fallback**: When DaVinci Resolve is not running or window is minimized, `ResolveWindowAnchor` must fall back smoothly to the primary screen edge without raising exceptions.
5. **Tab Reparenting / Swapping Integrity**: Switching between normal window and drawer must not break existing delegate bindings or drop attached Qt signals.

---

### Task 1: Micro-Interactions System (`src/ui/lux/lux_effects.py`)

**Files:**
- Create: `src/ui/lux/__init__.py`
- Create: `src/ui/lux/lux_effects.py`
- Test: `tests/test_lux_effects.py`

- [ ] **Step 1.1**: Write tests in `tests/test_lux_effects.py` covering:
  - `RippleOverlay`: attaches to target, starts ripple on coordinate, animates radius/alpha, auto-hides when complete.
  - `SegmentIndicator`: sets active segment index, computes target geometry, animates position using `QEasingCurve.Type.OutCubic`.
  - `get_luxury_dark_stylesheet`: returns valid QSS containing glassmorphism variables, dark backgrounds, and border styling.
- [ ] **Step 1.2**: Implement `src/ui/lux/__init__.py` and `src/ui/lux/lux_effects.py`:
  - `RippleOverlay(QWidget)` with `Qt.WidgetAttribute.WA_TransparentForMouseEvents`, expanding radial painter path, fading alpha from 0.35 to 0 in 300ms.
  - `SegmentIndicator(QWidget)` with horizontal sliding bar, gradient accent `#3C9A8C` -> `#3dcee1`, and `QPropertyAnimation` on geometry.
  - `get_luxury_dark_stylesheet()` generating comprehensive luxury dark palette.
- [ ] **Step 1.3**: Run `pytest tests/test_lux_effects.py` and confirm 100% pass.

---

### Task 2: Preset Card Grid with Live Hover Preview (`src/ui/lux/macro_card.py`)

**Files:**
- Create: `src/ui/lux/macro_card.py`
- Test: `tests/test_macro_card.py`

- [ ] **Step 2.1**: Write tests in `tests/test_macro_card.py` covering:
  - `MacroCard` initialization with metadata (id, name, category, duration, icon).
  - Hover > 150ms starts preview animation loop timer; `mouseLeaveEvent` stops timer and resets frame.
  - Double click emits `insert_requested(preset_id)` signal.
  - Drag initiation on mouse move beyond threshold.
  - `MacroGridWidget`: populates cards in 2-column layout, handles search filtering.
- [ ] **Step 2.2**: Implement `src/ui/lux/macro_card.py`:
  - `MacroCard(QFrame)`: 16:9 preview container, badge overlays, hover timer (150ms delay), cyclic frame rendering, double-click and drag-and-drop support.
  - `MacroGridWidget(QWidget)`: 2-column QScrollArea, search filter line edit, category filter chips, dynamic card creation.
- [ ] **Step 2.3**: Run `pytest tests/test_macro_card.py` and confirm 100% pass.

---

### Task 3: DaVinci Resolve Window Anchor (`src/ui/lux/window_anchor.py`)

**Files:**
- Create: `src/ui/lux/window_anchor.py`
- Test: `tests/test_window_anchor.py`

- [ ] **Step 3.1**: Write tests in `tests/test_window_anchor.py` covering:
  - Linux `wmctrl` / `xdotool` parsing logic with mocked command outputs.
  - Windows `user32` mock fallback logic.
  - Screen geometry fallback when no Resolve window found.
  - Dock coordinate calculation for both Collapsed (38px) and Expanded (440px) states.
  - Periodic tracker timer (`AnchorTracker`) emitting updates on geometry changes.
- [ ] **Step 3.2**: Implement `src/ui/lux/window_anchor.py`:
  - `get_resolve_window_rect()` cross-platform resolver.
  - `calculate_dock_geometry(anchor_rect, drawer_width, screen_rect, edge="right")`.
  - `AnchorTracker(QObject)` with `QTimer` (interval 1500ms) to track Resolve position changes and signal updates.
- [ ] **Step 3.3**: Run `pytest tests/test_window_anchor.py` and confirm 100% pass.

---

### Task 4: Floating Edge Drawer Container (`src/ui/lux/panel_edge_rail.py`)

**Files:**
- Create: `src/ui/lux/panel_edge_rail.py`
- Test: `tests/test_panel_edge_rail.py`

- [ ] **Step 4.1**: Write tests in `tests/test_panel_edge_rail.py` covering:
  - `EdgeDrawerWindow` creation with frameless & stays-on-top flags.
  - `collapse()` and `expand()` geometry animations with `OutCubic` curve.
  - `EdgeRevealHotspot` peek effect (sliding out ~12px on hover).
  - Tab hosting container and header toggle button.
- [ ] **Step 4.2**: Implement `src/ui/lux/panel_edge_rail.py`:
  - `EdgeDrawerWindow(QWidget)`:
    - Collapsed rail (width 38px): logo icon, tab peek icons, status dot, expand chevron.
    - Expanded body (width 440px): luxury dark header, `SegmentIndicator` tab bar, container area, collapse button.
    - Animation controller using `QPropertyAnimation` on window geometry.
    - `EdgeRevealHotspot` hovering corridor near the screen edge.
- [ ] **Step 4.3**: Run `pytest tests/test_panel_edge_rail.py` and confirm 100% pass.

---

### Task 5: Integration & Mode Toggle in `ChunDVCApp` (`src/ui/app.py`)

**Files:**
- Modify: `src/ui/app.py`
- Test: `tests/test_lux_integration.py`

- [ ] **Step 5.1**: Write integration tests in `tests/test_lux_integration.py` covering:
  - `ChunDVCApp` toggle between Standard Window and Edge Drawer modes.
  - Tab state and child widgets remain intact across mode switches.
  - `MacroCard` selection/double-click triggers `insert_title_at_playhead` handler properly.
- [ ] **Step 5.2**: Update `src/ui/app.py`:
  - Add "📌 Chế Độ Thanh Trượt (Edge Drawer)" toggle button to top header.
  - Instantiate `EdgeDrawerWindow` lazily or on demand.
  - Wire mode switching to smoothly transfer or show tabs in the drawer container.
  - Ensure all tab delegates, pipelines, and buttons remain 100% operational in both modes.
- [ ] **Step 5.3**: Run all test suites:
  - `pytest tests/test_lux_effects.py tests/test_macro_card.py tests/test_window_anchor.py tests/test_panel_edge_rail.py tests/test_lux_integration.py`
  - `pytest tests/test_tab_assets.py tests/test_resolve_nested_importer.py`
  - Verify 100% pass across all tests.
