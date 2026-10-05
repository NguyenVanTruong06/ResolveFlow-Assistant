# Unified Asset Manager & Fusion Transitions Design Spec

## 1. Goal
Evolve ResolveFlow-Assistant into a comprehensive Unified Asset Manager while maintaining its core AI Timeline generation capabilities. The goal is to provide a seamless, centralized library for Titles, Transitions, Effects, LUTs, and SFX, optimized specifically for our AI-first workflow.

## 2. Architecture & Approach
We will not blindly copy external tools. Instead, we adapt the concept of a centralized "Asset Hub" to our existing ecosystem (`text_preset.py`, `lut_generator.py`).

### 2.1 Core Assets Generation (Transitions)
- **File:** `src/core/transition_preset.py`
- **Responsibility:** Generate DaVinci Resolve Fusion Transition macros (`.setting` files).
- **Supported Transitions:** Whip Pan, Zoom Blur, Glitch, Light Leak.
- **Output:** Installed to `DaVinci Resolve/Support/Fusion/Templates/Edit/Transitions/ResolveFlow`.

### 2.2 Unified Asset Hub UI (Main Window)
- **Concept:** Replace the fragmented tabs with a single, cohesive dashboard.
- **Layout:**
  - **Left Sidebar:** Navigation filters (All, Titles, Transitions, LUTs, SFX).
  - **Center Grid:** Thumbnail/Card view of assets with Live Search.
  - **Right Panel / Overlay:** Asset details, preview animation, and action buttons.
- **Workflow Integration:** Assets selected here can be explicitly dragged into DaVinci Resolve (Free mode) or injected via API (Studio mode).

### 2.3 Smart Execution (API vs Manual Fallback)
- **Studio Version (API Active):** Clicking "Áp dụng" uses `resolve_api.py` to insert the asset at the playhead.
- **Free Version (API Inactive):** The UI gracefully falls back. The "Áp dụng" button becomes "Kéo & Thả (Drag & Drop)". The user clicks and drags the asset card directly into the DaVinci Resolve UI.

### 2.4 Floating Quick-Access Bubble
- **File:** `src/ui/widgets/floating_bubble.py`
- **Enhancement:** Upgrade the existing floating bubble to act as a "Mini Asset Browser". It stays always-on-top over DaVinci Resolve, allowing users to drag and drop elements without switching windows.

## 3. Implementation Phases
- **Phase 1:** Core Engineering - Develop `transition_preset.py` to generate the `.setting` files.
- **Phase 2:** UI Overhaul - Build the Unified Asset Hub.
- **Phase 3:** Mini-Panel - Enhance the `floating_bubble.py`.

## 4. Global Constraints
- Must remain 100% compatible with DaVinci Resolve Free version via Drag & Drop.
- Auto-generated `.setting` files must follow standard Fusion node syntax.
- UI built with PySide6.
