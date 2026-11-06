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
    assert preset.category == "motion"
    # BUILTIN_TRANSITIONS đã được loại bỏ khỏi production mặc định
    assert len(BUILTIN_TRANSITIONS) == 0


def test_builtin_transitions():
    # Danh sách mặc định trong production rỗng để người dùng nhập pack tự do
    assert isinstance(BUILTIN_TRANSITIONS, list)
    assert len(BUILTIN_TRANSITIONS) == 0


def test_generate_and_install_setting(tmp_path):
    preset = TransitionStylePreset(
        id="whip_pan_left",
        name="Whip Pan Left",
        category="motion",
        badge_icon="💨",
        transition_type="transform",
        duration_frames=24
    )
    out_file = tmp_path / f"{preset.id}.setting"
    TransitionMacroGenerator.export_setting_file(preset, str(out_file))
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "MacroOperator" in content or "GroupOperator" in content or "Tools = ordered()" in content


def test_install_transitions_to_davinci_resolve(tmp_path):
    fixture_transitions = [
        TransitionStylePreset(
            id="whip_pan_left",
            name="Whip Pan Left",
            category="motion",
            badge_icon="💨",
            transition_type="transform",
            duration_frames=24
        ),
        TransitionStylePreset(
            id="zoom_blur_in",
            name="Zoom Blur In",
            category="zoom",
            badge_icon="🔍",
            transition_type="zoom",
            duration_frames=20
        )
    ]
    count, target_dir = TransitionMacroGenerator.install_transitions_to_davinci_resolve(
        presets=fixture_transitions,
        target_dir=str(tmp_path)
    )
    assert count == len(fixture_transitions)
    for preset in fixture_transitions:
        assert (tmp_path / f"ChunDVC_{preset.id}.setting").exists() or (tmp_path / f"ResolveFlow_{preset.id}.setting").exists() or any(
            (f.name.startswith("ChunDVC_") or f.name.startswith("ResolveFlow_")) and f.name.endswith(".setting")
            for f in tmp_path.glob("*.setting")
        )



