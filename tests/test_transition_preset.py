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
    assert len(BUILTIN_TRANSITIONS) >= 3


def test_builtin_transitions():
    for item in BUILTIN_TRANSITIONS:
        assert isinstance(item, TransitionStylePreset)
        assert item.id
        assert item.name
        assert item.category
        assert item.badge_icon
        assert item.transition_type

