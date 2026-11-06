# Master Implementation Plan: Gate G08 (Safe UI Polish) & Gate G10 (`v1.0.0` Public Release & English Documentation)

**Document ID:** `08-10-UI-POLISH-AND-V1-RELEASE-MASTER-PLAN`  
**Date:** `2026-11-06`  
**Prerequisite:** Gate G07 Formally Closed under `B2-M1 (Single-Owner Governance)` (`docs/architecture/07B7-GATE6-PHASE2-B2-M1-RATIFICATION-AND-G07-CLOSURE-R1.md`)  
**Orchestration Mode:** Tech Lead (`Argon 4`) -> Implementer (`Worker - 1`, `Gemini 3.8 Flash`) via `.bridge/task.md` & `.bridge/report.md`

---

## 1. Strategic Objectives & Non-Negotiable Guardrails

### 1.1 Gate G08 Objective: Safe UI Visual Polish (Zero Layout Structure Change)
- **Strict Structural Invariant:** All UI polish across `DirectorWindow`, `StudioWindow`, and `EdgeDockWindow` must be **strictly non-structural**:
  - **NO** changes to widget hierarchy (`parent`/`child` relationships), `QStackedWidget` page indices (`0..3`), or layout managers (`QVBoxLayout`, `QHBoxLayout`, `QSplitter`, 4-column Studio layout).
  - **NO** changes to widget attribute names, `objectName` values, signal/slot signatures, or contract-tested Vietnamese/English label texts asserted by `tests/ui/`.
  - **NO** changes to `EdgeDockWindow` 6-state machine (`Hidden`, `Rail`, `Peek`, `Open`, `Pinned`, `Wide`) or `Qt.FocusPolicy.NoFocus` DaVinci Resolve focus-preservation rules.
- **Concrete Visual Polish Scope:**
  1. **Unified Dark Luxury Design Tokens (`src/ui/theme.py`):** Ensure consistent typography (`Inter, Segoe UI, system-ui, sans-serif`), refined surface elevation borders (`#27272a` -> `#3f3f46` on hover), crisp focus rings (`#7c3aed`), and sleek dark scrollbars (`QScrollBar:vertical`, `QScrollBar:horizontal`) across all windows.
  2. **Director Window & Stepper Visual Refinement (`src/ui/director/director_window.py`, `src/ui/director/widgets/stepper.py`):** Polish header/footer chrome, step indicator active/completed pill transitions, disabled button states, and status badge contrast.
  3. **Common Proposal Review Table Ergonomics (`src/ui/director/widgets/common_proposal_review_widget.py`):** Address the G06-deferred horizontal scroll friction (`docs/architecture/06C3-S3-code-acceptance.md:35`) by applying compact header/cell padding, `Qt.TextElideMode.ElideRight`, helpful cell tooltips (`Qt.ItemDataRole.ToolTipRole` in `CutsTableModel`, `AssignmentsTableModel`, `OmissionsTableModel`), and styled thin horizontal/vertical scrollbars so tables remain clean and readable at `1100x720` and `1200x750` without altering any model columns or data roles.
  4. **Studio Window & Luxury EdgeDock Polish (`src/ui/studio/studio_window.py`, `src/ui/lux/shell/dock_header.py`, `src/ui/lux/shell/dock_status.py`, `src/ui/lux/lux_effects.py`):** Refine header chips, search input focus border, status bar typography, and Studio badge/card hover states.

### 1.2 Gate G10 Objective: English GitHub `README.md` & `v1.0.0` Release Installer Preparation
- **Release Packaging & Installer Alignment (`v1.0.0`):**
  1. Fix `build_standalone.py` line 1 shebang (`#!/usr/bin/env python3` instead of `#!/usr/bin/env bash`).
  2. Synchronize `get_hidden_imports()` in `build_standalone.py` and `hidden_imports` in `resolveflow.spec` with the actual production module tree (`src.main`, `src.core.*`, `src.services.*`, `src.ui.director.*`, `src.ui.lux.*`, `src.ui.studio.*`, `src.ui.tabs.*`, `src.ui.widgets.*`), removing dead legacy imports (`src.ui.app`, `src.ui.components.mini_timeline`) and safely guarding optional asset folders.
  3. Enhance `package_deb.sh` for `v1.0.0` to generate deterministic `SHA256SUMS` alongside `dist/resolveflow-assistant_1.0.0_amd64.deb` and validate package metadata.
  4. Align `.github/workflows/release.yml` to Python `3.12`, run packaging dry-run & unit verification before building Linux `.deb` / `.tar.gz` and Windows `.zip` artifacts, and publish `SHA256SUMS`.
- **English GitHub Documentation & Open-Source Release Surface:**
  1. Rewrite `README.md` in polished, comprehensive **English** for the `v1.0.0` public release (including Product Overview, Dual-Window Architecture: Luxury EdgeDock + Studio + 4-Step AI Director, Gate G01–G10 Completion Matrix with `B2-M1` Single-Owner Governance, Quick Start & Installation for `.deb` / standalone tarball / Windows `.zip` / source, External Asset Library Layout, and Post-Release Regression Suite instructions including the G07 canary suite).
  2. Create `CHANGELOG.md` (`v1.0.0` release notes), `CONTRIBUTING.md` (developer setup, testing, and PR guidelines in English), `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, and `docs/release/github-launch-v1.0.0.md`, and update `docs/architecture/ROADMAP.md` to reflect G07/G08/G10 completion.

---

## 2. Sequential Workorder Breakdown (`Worker - 1` Automation Queue)

| Order | Workorder ID | Gate | Target Files | Verification Gate |
| :--- | :--- | :--- | :--- | :--- |
| **WO-1** | `G08-UI-SAFE-POLISH-SLICE-1` | **G08** | `src/ui/theme.py`, `src/ui/director/director_window.py`, `src/ui/director/widgets/stepper.py`, `src/ui/director/widgets/common_proposal_review_widget.py` | `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/test_common_proposal_review.py tests/ui/test_director.py tests/ui/test_director_step4_approval_gate.py tests/ui/test_director_readiness_ui.py tests/ui/test_director_preflight_shutdown.py -v --tb=short` |
| **WO-2** | `G08-UI-SAFE-POLISH-SLICE-2` | **G08** | `src/ui/studio/studio_window.py`, `src/ui/lux/shell/dock_header.py`, `src/ui/lux/shell/dock_status.py`, `src/ui/lux/lux_effects.py` | `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/test_edge_dock.py tests/ui/test_studio_dock_sync.py tests/ui/test_dock_passive_suggestions.py tests/ui/test_dock_profile_taxonomy.py tests/ui/test_action_bar_availability.py -v --tb=short` |
| **WO-3** | `G10-PACKAGING-V1-RELEASE-PREP-R1` | **G10** | `build_standalone.py`, `resolveflow.spec`, `package_deb.sh`, `.github/workflows/release.yml`, `tests/tools/test_release_packaging_config.py` | `.venv/bin/python build_standalone.py --dry-run && bash -n package_deb.sh && .venv/bin/pytest tests/tools/test_release_packaging_config.py -v --tb=short` |
| **WO-4** | `G10-ENGLISH-README-AND-GITHUB-RELEASE-DOCS-R1` | **G10** | `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, `docs/release/github-launch-v1.0.0.md`, `docs/architecture/ROADMAP.md` | Documentation & release-readiness verification suite (`tests/tools/test_release_packaging_config.py` + G07 regression canary check) |

---

## 3. Detailed Specification per Workorder

### 3.1 Workorder 1: `G08-UI-SAFE-POLISH-SLICE-1` (Director Window, Stepper & Common Proposal Review Ergonomics)
- **`src/ui/theme.py`:**
  - Add reusable `get_director_scrollbar_stylesheet() -> str` (or enhance global `get_stylesheet()`) providing sleek 6px dark scrollbars (`#27272a` handle, `#3f3f46` hover, transparent track) and clean `QToolTip` styling (`#18181b` background, `#e4e4e7` text, `#3f3f46` border).
- **`src/ui/director/director_window.py` & `src/ui/director/widgets/stepper.py`:**
  - Refine QSS styling in `DirectorWindow._init_ui()` and `DirectorStepButton._update_appearance()` with consistent `Inter, 'Segoe UI', system-ui, sans-serif` font family, crisp focus/hover borders, and refined footer navigation button states (`btn_back`, `btn_next`) without changing any widget hierarchy, sizes, signals, or labels.
- **`src/ui/director/widgets/common_proposal_review_widget.py`:**
  - Add `Qt.ItemDataRole.ToolTipRole` support in `CutsTableModel.data()`, `AssignmentsTableModel.data()`, and `OmissionsTableModel.data()` so hovering any cell displays the full un-truncated value (`clip_id`, `description`, `candidate_Summary`, `reason`, `warning_message`).
  - Configure `tbl_cuts`, `tbl_assignments`, and `tbl_omissions` with `setTextElideMode(Qt.TextElideMode.ElideRight)`, `setAlternatingRowColors(True)`, compact header padding (`padding: 3px 5px`), and sleek dark scrollbars so horizontal overflow is minimized at `1100x720` / `1200x750` while preserving 100% of existing columns, models, signals, and T01–T10 acceptance tests.

### 3.2 Workorder 2: `G08-UI-SAFE-POLISH-SLICE-2` (Studio Window & Luxury EdgeDock Visual Polish)
- **`src/ui/lux/shell/dock_header.py`, `src/ui/lux/shell/dock_status.py`, `src/ui/lux/lux_effects.py`:**
  - Refine QSS tokens on `DockHeaderWidget` (search input focus glow, action icon button hover states, page chip badge contrast) and `DockStatusBar` (crisp tabular monospace timecode display and subtle top border separator) while strictly preserving `setFixedHeight(44)`, `setFixedHeight(22)`, and `Qt.FocusPolicy.NoFocus`.
- **`src/ui/studio/studio_window.py`:**
  - Refine Studio window QSS styling (toolbar button hover/active states, storage badge contrast, splitter handle hover feedback, and status bar polish) without changing the 4-column layout or any signal/slot connections.

### 3.3 Workorder 3: `G10-PACKAGING-V1-RELEASE-PREP-R1` (`v1.0.0` Standalone & Debian Installer Alignment)
- **`build_standalone.py` & `resolveflow.spec`:**
  - Fix `#!/usr/bin/env python3` shebang on line 1 of `build_standalone.py`.
  - Update `get_hidden_imports()` and `resolveflow.spec` `hidden_imports` to accurately include all production modules in `src/core`, `src/services`, and `src/ui` (`director`, `lux`, `studio`, `tabs`, `widgets`), removing obsolete imports (`src.ui.app`, `src.ui.components.mini_timeline`).
  - Make resource bundling (`assets`, `presets`, `recipes`) check `os.path.exists(...)` dynamically so PyInstaller never fails if a legacy folder is absent.
- **`package_deb.sh` & `.github/workflows/release.yml`:**
  - Enhance `package_deb.sh` to generate `dist/SHA256SUMS` for the built `.deb` and standalone tarball/directory, set `Installed-Size` dynamically, and support `v1.0.0` metadata cleanly.
  - Update `.github/workflows/release.yml` to Python `3.12`, run `python build_standalone.py --dry-run` and `pytest tests/tools/test_release_packaging_config.py`, and attach `SHA256SUMS` to the GitHub Release.
- **`tests/tools/test_release_packaging_config.py`:**
  - Add automated unit tests verifying that:
    1. Every module listed in `build_standalone.py:get_hidden_imports()` is importable in the repository environment (`importlib.util.find_spec(mod) is not None`).
    2. `build_standalone.py --dry-run` exits with code `0`.
    3. `package_deb.sh` passes `bash -n` syntax validation and targets version `1.0.0` by default.

### 3.4 Workorder 4: `G10-ENGLISH-README-AND-GITHUB-RELEASE-DOCS-R1` (English GitHub `README.md` & Release Bundle Docs)
- **`README.md` (100% English):**
  - Badges (`v1.0.0`, Python `3.12+`, PySide6, DaVinci Resolve `18/19+`, License `MIT`).
  - Executive Summary & Value Proposition of **ChunDVC (ResolveFlow Assistant)**.
  - Architecture & Key Capabilities:
    - **Luxury EdgeDock** (6-state DaVinci Resolve overlay with `NoFocus` non-intrusive workflow, Command Palette `Ctrl+K`, Context Router, Hover Preview).
    - **ChunDVC Studio** (4-column Asset Hub, External Pack Library Manager, SQLite FTS5 `<15ms` search, Safe Drag-and-Drop / Smart Insert).
    - **4-Step AI Director** (`Source` -> `Style` -> `Common Proposal Review` -> `Safe Build` with Rational Frame Geometry, App-Owned Approval Gate, and `B2-M1` Single-Owner Governance).
  - **Installation & Release Packages (`v1.0.0`):**
    - Option 1: Ubuntu/Debian `.deb` Installer (`sudo dpkg -i resolveflow-assistant_1.0.0_amd64.deb`).
    - Option 2: Standalone Linux `.tar.gz` / Windows `.zip` Portable Bundle.
    - Option 3: Run from Source (`Python 3.12`, `.venv`).
  - **Building `v1.0.0` Installers Locally:** `python build_standalone.py` & `./package_deb.sh 1.0.0`.
  - **Testing & Post-Release Regression Suite:** Running the UI/Core test suites and the retained Gate G07 Canary Regression Suite (`.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py` + `tests/services/test_dispatcher_admission_*`).
- **`CHANGELOG.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`, `docs/release/github-launch-v1.0.0.md`, `docs/architecture/ROADMAP.md`:**
  - Complete release notes for `v1.0.0`, contributor guide in English, GitHub issue forms, release checklist, and updated roadmap marking G07 (`CLOSED — B2-M1`), G08 (`COMPLETED`), and G10 (`COMPLETED`).
