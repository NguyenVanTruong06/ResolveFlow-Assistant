from src.core.transition_preset import TransitionStylePreset, BUILTIN_TRANSITIONS, TransitionMacroGenerator

def test_transition_preset_schema():
    preset = TransitionStylePreset(
        id="whip_pan_left",
        name="Whip Pan Left",
        category="motion",
        badge_icon="💨",
        transition_type="transform"
    )
    assert preset.id == "whip_pan_left"
    assert len(BUILTIN_TRANSITIONS) >= 3


def test_builtin_transitions():
    for item in BUILTIN_TRANSITIONS:
        assert isinstance(item, TransitionStylePreset)
        assert item.id
        assert item.name
        assert item.category
        assert item.badge_icon
        assert item.transition_type


def test_generate_and_install_setting(tmp_path):
    preset = BUILTIN_TRANSITIONS[0]
    out_file = tmp_path / f"{preset.id}.setting"
    TransitionMacroGenerator.export_setting_file(preset, str(out_file))
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "MacroOperator" in content or "GroupOperator" in content or "Tools = ordered()" in content


def test_install_transitions_to_davinci_resolve(tmp_path):
    count, target_dir = TransitionMacroGenerator.install_transitions_to_davinci_resolve(
        target_dir=str(tmp_path)
    )
    assert count == len(BUILTIN_TRANSITIONS)
    for preset in BUILTIN_TRANSITIONS:
        assert (tmp_path / f"ChunDVC_{preset.id}.setting").exists() or (tmp_path / f"ResolveFlow_{preset.id}.setting").exists() or any(
            (f.name.startswith("ChunDVC_") or f.name.startswith("ResolveFlow_")) and f.name.endswith(".setting")
            for f in tmp_path.glob("*.setting")
        )


