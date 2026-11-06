# Changelog

All notable changes to the **ChunDVC (ResolveFlow Assistant)** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-06

### Added
- **Luxury EdgeDock Overlay System (`src/ui/lux/`):**
  - Adaptive 6-state edge overlay (`Hidden`, `Rail`, `Peek`, `Open`, `Pinned`, `Wide`) for non-intrusive NLE companion workflows.
  - Strict `Qt.FocusPolicy.NoFocus` policy across passive browsing widgets, preventing accidental keyboard shortcut hijacking from DaVinci Resolve.
  - Global Command Palette (`Ctrl+Alt+Space`) for instant keyboard-driven search and quick actions.
  - DaVinci Resolve Page Context Router dynamically updating recommended assets across `Edit`, `Cut`, `Fusion`, `Color`, `Fairlight`, `Deliver`, and `Media` pages.
  - Sub-15ms local search engine using SQLite FTS5 for instant indexing of B-roll, SFX, BGM, overlays, memes, and titles.
- **ChunDVC Studio Asset Hub (`src/ui/studio/` & `src/ui/tabs/`):**
  - 4-column studio browser for structured categorization of editing assets.
  - Pack Ingest Wizard featuring multi-stage inspection, classification review, companion preview preservation, and guarded transactional commits.
  - Multi-drive storage inventory system supporting external SSDs with automatic online/offline status detection and cached metadata browsing.
  - Fast audio/video hover preview and playhead insertion directly to DaVinci Resolve timelines.
- **4-Step AI Director Pipeline (`src/ui/director/`):**
  - Step 1 (Source Intake): Batch ingest with SHA-256 fingerprinting, Faster-Whisper local speech transcription with word-level timestamps, and visual frame sampling cache.
  - Step 2 (Style & Brief): Built-in and custom style presets (`Vlog Hook`, `Podcast Multi-Cam`, `Shorts 9:16`, `Documentary`), pacing sliders, and `CreativeContext` intake.
  - Step 3 (Common Proposal Review): Unified editorial review table displaying primary cuts, proposed asset assignments, and explicit proposed omissions with rational frame-geometry verification via `PlanGeometryAssembler`.
  - Step 4 (Build & Readiness Preflight): Source availability preflight, catalog readiness verification, session recovery inspection, and deterministic execution dispatch.
- **Packaging & Cross-Platform Distribution (Gate G10):**
  - Debian package installer (`package_deb.sh`) generating `dist/resolveflow-assistant_1.0.0_amd64.deb` and automated `dist/SHA256SUMS`.
  - Multi-platform PyInstaller standalone builder (`build_standalone.py`) with `--verify-imports` and `--dry-run` CLI validation modes.
  - GitHub Actions automated release pipeline (`.github/workflows/release.yml`) targeting Python 3.12 on Linux and Windows.
  - Full automated packaging and documentation test suites (`tests/tools/test_release_packaging_config.py` and `tests/tools/test_release_docs_and_readme.py`).

### Changed
- Migrated default runtime environment to **Python 3.12+** with PySide6 (Qt6).
- Standardized UI theme to **NLE Obsidian Studio Dark Theme** matching native DaVinci Resolve interface palettes (`#1E1E1E` background, `#282828` panels, `#FF8C00` warm amber active accents).
- Refactored `CommonProposalReviewWidget` table layout to completely eliminate horizontal scrollbar overflow at standard `1200x750` resolutions across all three tabs (`Primary Cuts`, `Proposed Asset Assignments`, `Proposed Omissions`).
- Synchronized standalone PyInstaller specification (`resolveflow.spec`) with dynamic resource directory existence guards (`os.path.exists`) to prevent packaging errors on clean environments.

### Fixed
- Fixed shebang in `build_standalone.py` from `#!/usr/bin/env bash` to `#!/usr/bin/env python3`.
- Removed obsolete and dead module references (`src.ui.app`, `src.ui.components.mini_timeline`) from packaging configurations and spec files.
- Eliminated uncaught `AdmissionRecordValidationError` exceptions in admission coordinator by encapsulating DTO record instantiation inside `try ... except AdmissionLedgerError:` blocks.
- Fixed table cell clipping in proposal review widgets using `ScrollPerPixel`, `ElideRight`, and rich tooltip inspection.
- Enforced non-empty string element validation on `unresolved_prerequisites` tuples.

### Security & Governance (B2-M1)
- **B2-M1 Single-Owner Governance Ratification:**
  - Formally ratified single-owner operational governance charter (`B2-M1`) for all Plan 07B7 and Gate G07 execution boundaries.
  - Officially closed Gate G07 with cryptographic non-burning pre-validation priority between `DispatcherAdmissionVerifierCore` and `DurableAdmissionLedger`.
  - Retained Gate G07 canary dispatch harness (`.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`) and unit suites as permanent post-release regression test baselines.
  - Enforced fail-closed replay prevention across compound identifiers (`workorder_id`, `execution_id`, `preflight_run_id`, `admission_id`).
