# Unified Asset Manager (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core generator for DaVinci Resolve Fusion Transition macros (`.setting`).

**Architecture:** Create `transition_preset.py` mimicking the successful `text_preset.py` pattern. It will define a `TransitionStylePreset` schema, a list of builtin trendy transitions (Whip Pan, Zoom Blur, Glitch), and a `TransitionMacroGenerator` to emit valid Lua-based `.setting` files.

**Tech Stack:** Python 3, Pydantic, DaVinci Fusion Scripting format (.setting)

**Spec:** `docs/superpowers/specs/2026-10-05-unified-asset-manager-design.md`

## Global Constraints
- Auto-generated `.setting` files must follow standard Fusion node syntax.
- Must remain 100% compatible with DaVinci Resolve Free version via Drag & Drop.

## Review Focus
- **Invalid characters in filename**: Preset names with special characters might break file saving. -> Test with special chars.
- **Missing directory**: The Fusion Transitions directory might not exist on the user's OS. -> Ensure `os.makedirs` handles it safely.
- **Empty preset list**: Installing an empty list of presets should not crash. -> Test empty list behavior.

---

### Task 1: TransitionStylePreset Schema and Built-in Data

**Files:**
- Create: `src/core/transition_preset.py`
- Create: `tests/test_transition_preset.py`

**Interfaces:**
- Consumes: None
- Produces: `TransitionStylePreset` (Pydantic model), `BUILTIN_TRANSITIONS` (List[TransitionStylePreset])

- [ ] **Step 1: Write the failing test**

```python
# tests/test_transition_preset.py
from src.core.transition_preset import TransitionStylePreset, BUILTIN_TRANSITIONS

def test_transition_preset_schema():
    preset = TransitionStylePreset(
        id="whip_pan_left",
        name="Whip Pan Left",
        category="motion",
        badge_icon="💨",
        transition_type="transform"
    )
    assert preset.id == "whip_pan_left"
    assert len(BUILTIN_TRANSITIONS) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transition_preset.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src.core.transition_preset'"

- [ ] **Step 3: Implement Schema in `src/core/transition_preset.py`**

Define `TransitionStylePreset` with Pydantic and define at least 3 items in `BUILTIN_TRANSITIONS` (e.g. Whip Pan Left, Zoom Blur In, RGB Glitch).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transition_preset.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/transition_preset.py tests/test_transition_preset.py
git commit -m "feat(core): add TransitionStylePreset schema and builtin data"
```

### Task 2: TransitionMacroGenerator

**Files:**
- Modify: `src/core/transition_preset.py`
- Modify: `tests/test_transition_preset.py`

**Interfaces:**
- Consumes: `TransitionStylePreset`
- Produces: `TransitionMacroGenerator.export_setting_file(preset, output_path)`, `TransitionMacroGenerator.install_transitions_to_davinci_resolve()`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_transition_preset.py
import os
from src.core.transition_preset import TransitionMacroGenerator, BUILTIN_TRANSITIONS

def test_generate_and_install_setting(tmp_path):
    preset = BUILTIN_TRANSITIONS[0]
    out_file = tmp_path / f"{preset.id}.setting"
    TransitionMacroGenerator.export_setting_file(preset, str(out_file))
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "MacroOperator" in content or "GroupOperator" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transition_preset.py::test_generate_and_install_setting -v`
Expected: FAIL with "ImportError: cannot import name 'TransitionMacroGenerator'"

- [ ] **Step 3: Implement `TransitionMacroGenerator` in `src/core/transition_preset.py`**

Implement the generator to emit a basic Fusion Macro transition template. Implement `install_transitions_to_davinci_resolve` similar to the text presets (targeting `Templates/Edit/Transitions/ResolveFlow`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transition_preset.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/transition_preset.py tests/test_transition_preset.py
git commit -m "feat(core): implement TransitionMacroGenerator for Fusion settings"
```
