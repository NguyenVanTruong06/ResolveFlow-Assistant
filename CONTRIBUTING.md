# Contributing to ChunDVC (ResolveFlow Assistant)

Thank you for your interest in contributing to **ChunDVC (ResolveFlow Assistant)**! We welcome contributions, bug fixes, documentation improvements, and feature proposals.

This guide outlines our development workflow, coding standards, and testing procedures.

---

## 🛠 Development Environment Setup

### Prerequisites
- **Python:** 3.12+ (tested with Python 3.12.3)
- **FFmpeg & FFprobe:** Installed and accessible in your system `PATH`
- **DaVinci Resolve:** Version 18 or 19+ (Studio or Free) for NLE integration testing
- **Git**

### Setting Up Local Environment

```bash
# 1. Fork and clone the repository
git clone https://github.com/NguyenVanTruong06/ResolveFlow-Assistant.git
cd ResolveFlow-Assistant

# 2. Create and activate a Python 3.12 virtual environment
python3.12 -m venv .venv
source .venv/bin/activate       # On Linux/macOS
# .venv\Scripts\activate        # On Windows

# 3. Upgrade pip and install development dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller pytest pytest-cov
```

---

## 📁 Repository Layout & Layer Architecture

ChunDVC adheres to a strict separation of concerns across application layers:

- `src/core/`: Pure Python business logic, data contracts, mathematical frame calculations, and file format parsers. **Must not import Qt or UI widgets.**
- `src/services/`: Service layer handling background workers, file I/O, local model coordination, database transactions, and DaVinci Resolve gateway adapters.
- `src/ui/`: PySide6 (Qt6) interface components:
  - `src/ui/lux/`: Luxury EdgeDock overlay, Command Palette, and Context Router.
  - `src/ui/studio/`: 4-column studio asset browser and storage manager.
  - `src/ui/director/`: 4-step AI Director wizard and proposal review tables.
  - `src/ui/theme.py`: Obsidian dark studio color palette and typography.
- `tests/`: Automated unit and integration test suites:
  - `tests/core/`: Pure computational logic and schema tests.
  - `tests/services/`: Service coordination and ledger security tests.
  - `tests/ui/`: PySide6 interface component tests.
  - `tests/tools/`: Packaging and release configuration tests.

---

## 🧪 Testing Guidelines

ChunDVC maintains high test rigor. All code contributions must be accompanied by appropriate test coverage.

### Running Headless Qt Tests
Because many UI components require a Qt display server, run UI tests headlessly using `QT_QPA_PLATFORM=offscreen`:

```bash
# Run all UI acceptance tests
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/ -v --tb=short

# Run a specific UI test file
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/test_common_proposal_review.py -v
```

### Running Core & Service Tests
Core and service tests do not require a display server:

```bash
# Run core batch planner tests
.venv/bin/pytest tests/core/test_batch_planner.py -v

# Run admission verifier and replay ledger tests
.venv/bin/pytest tests/services/test_dispatcher_admission_*.py -v
```

### Running Release Packaging & Import Verification
Before submitting changes to imports, always verify that all modules are resolvable and standalone builds are valid:

```bash
# Check that 100% of internal src.* modules resolve without missing imports
python build_standalone.py --verify-imports

# Run packaging dry-run check
python build_standalone.py --dry-run

# Run automated packaging test suite
.venv/bin/pytest tests/tools/test_release_packaging_config.py tests/tools/test_release_docs_and_readme.py -v
```

---

## 📐 Coding Standards

- **Formatting & Style:** Follow PEP 8 guidelines. Use 4 spaces for indentation.
- **Type Annotations:** All new functions and methods must include static type annotations (`from typing import ...` or Python 3.12 native generics).
- **Immutability & Safety:** Core data contracts must be immutable frozen dataclasses (`@dataclass(frozen=True)`) with explicit `__post_init__` type and boundary validation.
- **No Private Keys in Code:** Never generate or commit Ed25519 private keys. Use deterministic synthetic public keys for testing.
- **Fail-Closed Principle:** Security, ledger, and preflight operations must fail closed upon any anomaly or invariant violation.

---

## 🚀 Pull Request Checklist

Before submitting a pull request, please verify the following:

- [ ] Branch is created from `main` with a clear, descriptive name (e.g., `feat/audio-router-stereo-split`, `fix/table-tooltip-wrap`).
- [ ] Code follows project architecture guidelines (no QWidget imports in `src/core/`).
- [ ] All tests pass locally (`QT_QPA_PLATFORM=offscreen pytest tests/ui/` and `pytest tests/tools/`).
- [ ] `python build_standalone.py --verify-imports` reports `100% (237/237) 'src.*' modules verified`.
- [ ] PR description clearly explains the problem solved, design decisions made, and test evidence.
- [ ] No temporary, backup (`*.bak_original`), or private credentials committed.

---

## 💬 Community & Questions

If you encounter any issues or have questions regarding development:
- Open a [GitHub Discussion](https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/discussions) or submit an [Issue](https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/issues).
- Be respectful and adhere to standard open-source collaboration etiquette.
