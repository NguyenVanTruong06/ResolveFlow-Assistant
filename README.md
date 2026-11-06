# ChunDVC (ResolveFlow Assistant) — v1.0.0

[![Release v1.0.0](https://img.shields.io/badge/Release-v1.0.0-blue.svg)](https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-green.svg)](https://www.python.org/)
[![PySide6 (Qt6)](https://img.shields.io/badge/PySide6-Qt6-orange.svg)](https://pypi.org/project/PySide6/)
[![DaVinci Resolve 18 / 19+](https://img.shields.io/badge/DaVinci%20Resolve-18%20%2F%2019+-purple.svg)](https://www.blackmagicdesign.com/products/davinciresolve)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**ChunDVC (ResolveFlow Assistant)** is a local-first, high-speed AI video rough-cut editing assistant and non-intrusive studio asset hub engineered specifically for **DaVinci Resolve 18 and 19+** on **Linux** and **Windows**.

Instead of sending hours of multi-gigabyte raw footage to external cloud services, ChunDVC performs high-precision speech transcription and visual frame analysis locally, lets editors configure creative direction with bounded prompts, presents a unified editorial proposal review table, and executes approved cuts and asset placements directly on DaVinci Resolve timelines.

---

## 🏛 Three-Pillar NLE Obsidian Studio Architecture

ChunDVC is organized around three modular, non-intrusive studio pillars designed to seamlessly integrate alongside your NLE workflow:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   ChunDVC v1.0.0 Studio Architecture                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┼───────────────────────────────┐
    │                               │                               │
    ▼                               ▼                               ▼
┌─────────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐
│ 1. Luxury EdgeDock      │ │ 2. Studio Asset Hub     │ │ 3. 4-Step AI Director   │
│    (src/ui/lux/)        │ │    (src/ui/studio/)     │ │    (src/ui/director/)   │
├─────────────────────────┤ ├─────────────────────────┤ ├─────────────────────────┤
│ • 6-State Edge Snapping │ │ • 4-Column Workspace    │ │ • Step 1: Source Intake │
│ • NoFocus Preservation  │ │ • External Pack Ingest  │ │ • Step 2: Style & Brief │
│ • Command Palette       │ │ • Multi-Drive Storage   │ │ • Step 3: Proposal Rev. │
│ • Context Router        │ │ • Fast Hover Preview    │ │ • Step 4: Build Preflt. │
│ • <15ms SQLite FTS5     │ │ • Safe Drag-and-Drop    │ │ • B2-M1 Execution Gate  │
└─────────────────────────┘ └─────────────────────────┘ └─────────────────────────┘
```

### 1. Luxury EdgeDock Overlay (`src/ui/lux/`)
- **6-State Adaptive Snapping:** Seamlessly expands and collapses across `Hidden`, `Rail`, `Peek`, `Open`, `Pinned`, and `Wide` states along your primary display edge.
- **NLE Keyboard Focus Preservation:** Uses `Qt.FocusPolicy.NoFocus` across all passive browsing widgets so your DaVinci Resolve playback shortcuts (`J-K-L`, `Space`, `Ctrl+B`) are never stolen.
- **Instant Command Palette (`Ctrl+Alt+Space`):** Quick keyboard search across all studio actions, audio routing, pack categories, and timeline tools.
- **DaVinci Page Context Router:** Automatically adapts shown tools and recommended assets depending on the active page: `Edit`, `Cut`, `Fusion`, `Color`, `Fairlight`, `Deliver`, or `Media`.
- **Sub-15ms Real-time Search:** Powered by local SQLite FTS5 full-text indexing across B-roll, SFX, BGM, overlays, memes, and Fusion macro titles.

### 2. ChunDVC Studio Asset Hub (`src/ui/studio/` & `src/ui/tabs/`)
- **4-Column Studio Workspace:** Categorized hierarchy dividing sound effects, background music, LUTs, video overlays, animated titles, and transitions.
- **Pack Ingest Wizard:** Automated unpacker, inspector, taxonomy classifier, and guarded commit pipeline that preserves companion preview media without polluting timeline assets.
- **Multi-Drive Storage Manager:** Configure storage roots on high-capacity internal or external SSDs with active online/offline status tracking and graceful offline metadata browsing.
- **Smart Playhead Insertion & Safe Drag-and-Drop:** Insert assets at the current Resolve playhead or drag them directly into the Media Pool and timeline tracks.

### 3. 4-Step AI Director (`src/ui/director/`)
- **Step 1 (`Source Intake`):** Multi-clip ingest with SHA-256 fingerprinting, Faster-Whisper local speech transcription with word-level timestamps, and local visual frame sampling.
- **Step 2 (`Style & Brief`):** Select editing styles (`Vlog Hook`, `Podcast Multi-Cam`, `Shorts 9:16`, `Documentary`), configure pacing thresholds, and provide custom `CreativeContext` prompts.
- **Step 3 (`Common Proposal Review`):** Unified editorial review table displaying:
  - *Primary Cuts:* Silence-removal intervals, speaker selections, and speech cut points.
  - *Proposed Asset Assignments:* Contextually suggested B-roll, SFX, and title overlays.
  - *Proposed Omissions:* Explicit, non-silent tracking of optional assets omitted due to duration or density budgets.
  - *Geometry & Overflow Protection:* Validated by `PlanGeometryAssembler` with horizontal overflow reduced to `[0, 0, 0]` at `1200x750` via pixel scrolling, elision, and full tooltip inspection.
- **Step 4 (`Build & Readiness Preflight`):** Preflight validation, catalog readiness verification, session recovery assessment, and deterministic timeline execution governed under **`B2-M1 (Single-Owner Governance)`**.

---

## 🚀 Installation & Quick Start

### Option 1: Ubuntu / Debian Package (`.deb`) — Recommended for Linux

Download the verified `v1.0.0` Debian installer:

```bash
# Verify checksum before installing
sha256sum -c SHA256SUMS

# Install package
sudo dpkg -i resolveflow-assistant_1.0.0_amd64.deb

# Auto-resolve any missing runtime dependencies (ffmpeg, libxcb)
sudo apt-get install -f
```

Launch from your application menu or run `resolveflow-assistant` in terminal.

### Option 2: Standalone Portable Bundle (Linux `.tar.gz` / Windows `.zip`)

No system installation or Python environment required:

```bash
# Linux
tar -xzf ResolveFlow-Assistant-v1.0.0-linux-x64.tar.gz
cd ResolveFlow-Assistant
./launch_resolveflow.sh

# Windows
# Extract ResolveFlow-Assistant-v1.0.0-windows-x64.zip and run:
Launch_ResolveFlow.bat
```

### Option 3: Run from Source (`Python 3.12+`)

```bash
# 1. Clone repository
git clone https://github.com/NguyenVanTruong06/ResolveFlow-Assistant.git
cd ResolveFlow-Assistant

# 2. Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Launch ChunDVC Assistant
python -m src.main
```

---

## 🛠 Local Release Packaging

ChunDVC includes standalone build and packaging scripts for cross-platform distribution:

```bash
# 1. Verify 100% of internal src.* modules resolve without dead references
python build_standalone.py --verify-imports

# 2. Run pre-build dry-run configuration check
python build_standalone.py --dry-run

# 3. Build standalone binary bundle via PyInstaller
python build_standalone.py

# 4. Package Debian (.deb) installer and generate SHA256SUMS (Linux)
./package_deb.sh 1.0.0
```

---

## 🧪 Testing & Verification

ChunDVC maintains a comprehensive test suite across UI components, packaging configurations, and admission security gates:

```bash
# Run UI & Desktop Studio Acceptance Suite (119 tests)
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/ -v --tb=short

# Run Release Packaging & Documentation Verification Suite
.venv/bin/pytest tests/tools/test_release_packaging_config.py tests/tools/test_release_docs_and_readme.py -v --tb=short

# Run Retained Gate G07 Post-Release Regression Suite (Admission & Ledger)
.venv/bin/pytest tests/services/test_dispatcher_admission_*.py -v --tb=short

# Run Retained Gate G07 Canary Dispatch Test Harness
.venv/bin/python .bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py
```

---

## 🛠 Core Tools & Capabilities

ChunDVC equips DaVinci Resolve editors with a comprehensive suite of offline AI and workflow acceleration tools:

| Tool | Icon | Capability & Description | Typical Speed |
|---|:---:|---|:---:|
| **Instant AutoCut** | ⚡ | Automatically detects audio silence intervals and ripple-cuts active timelines with configurable padding (0.2s–0.5s) | `< 2s` |
| **AI Director & Story Copilot** | 🤖 | Analyzes multi-clip footage, generates structured rough-cut proposals, and suggests contextual B-roll & SFX | Real-time |
| **Local Speech Transcription** | 🎙 | High-accuracy local speech-to-text using `Faster-Whisper` (100% offline, zero cloud data leaks) with word-level timestamps | `5–8x realtime` |
| **Studio Asset Hub** | 📦 | High-speed browser for SFX, BGM, Memes, Overlays, LUTs, and animated Fusion titles | `< 15ms FTS5` |
| **Cloud On-Demand Cache** | ☁️ | Browse cloud asset catalogs instantly and download individual items on-demand with SHA-256 local verification | `< 0.5s / item` |
| **Luxury EdgeDock** | 🪟 | 6-state adaptive snapping overlay on your monitor's edge with zero NLE keyboard focus stealing | Instant |
| **Command Palette** | ⌨️ | Global fuzzy-search launcher (`Ctrl+Alt+Space`) across 100+ DaVinci actions, timeline tools, and audio routing | `< 10ms` |
| **1-Click 3D LUT Pipeline** | 🎨 | Instant preview and application of cinematic Rec.709, Film Emulation, and Vlog grading directly to timeline clips | Instant |
| **Audio Beat & Normalizer** | 🎵 | Beat marker detection for rhythm cutting and automatic EBU R128 loudness normalization | `~ 1s / track` |
| **Fusion Title Generator** | 📝 | 1-click generation of animated Text+ lower thirds, captions, and kinetic typography presets | Instant |
| **Multi-Drive Storage Manager** | 💾 | Hot-swap and manage internal or external SSD asset libraries with automatic online/offline status detection | Instant |

---

## 🤝 Contributing

We welcome bug reports, workflow enhancements, and community contributions. Please review our [Contribution Guidelines](CONTRIBUTING.md) before submitting pull requests.

To report bugs or suggest features, please use our structured [GitHub Issue Templates](.github/ISSUE_TEMPLATE/).

---

## 📄 License & Credits

Source code is released under the [MIT License](LICENSE).

- Created by **Nguyen Van Truong** ([@NguyenVanTruong06](https://github.com/NguyenVanTruong06)).
- *Disclaimer:* ChunDVC (ResolveFlow Assistant) is an independent open-source project and is not affiliated with or endorsed by Blackmagic Design. DaVinci Resolve is a registered trademark of Blackmagic Design Pty. Ltd.
