"""
tests/ui/test_action_bar_availability.py
Unit & integration tests for DockActionBar availability handling (G04-04C1-I).
"""
import pytest
from PySide6.QtWidgets import QApplication

from src.ui.lux.library.action_bar import DockActionBar


@pytest.fixture
def app():
    instance = QApplication.instance()
    if not instance:
        instance = QApplication([])
    return instance


class DummyAssetObj:
    """Object-based asset for testing object-to-dict conversion."""
    def __init__(self, asset_id: str, name: str, category: str, availability: str = "online", duration: float = 2.5):
        self.id = asset_id
        self.name = name
        self.category = category
        self.availability = availability
        self.duration = duration


def test_action_bar_default_state(app):
    """Initial state: all buttons disabled, hint displayed."""
    bar = DockActionBar()
    assert not bar.btn_primary.isEnabled()
    assert not bar.btn_tune.isEnabled()
    assert not bar.btn_more.isEnabled()
    assert "Chọn một mẫu" in bar.lbl_info.text()


def test_action_bar_online_asset_enables_all(app):
    """Online asset: primary, tune, and more buttons enabled."""
    bar = DockActionBar()
    asset = {
        "id": "sfx_whoosh",
        "name": "Whoosh Clean",
        "category": "sfx",
        "availability": "online",
        "duration": 1.2,
    }
    bar.set_asset(asset)
    assert bar.btn_primary.isEnabled()
    assert bar.btn_tune.isEnabled()
    assert bar.btn_more.isEnabled()
    assert "Whoosh Clean" in bar.lbl_info.text()
    assert "track audio trống" in bar.lbl_info.text()
    assert bar.btn_primary.toolTip() == ""


@pytest.mark.parametrize("status,expected_text", [
    ("offline", "Kho Offline — hãy kết nối lại ổ đĩa trước khi chèn"),
    ("missing", "File nguồn không tồn tại trên đĩa"),
    ("staging", "Đang đồng bộ / nạp tài nguyên — chưa sẵn sàng chèn"),
])
def test_action_bar_unavailable_states_disable_primary(app, status, expected_text):
    """Unavailable assets (offline, missing, staging) disable primary button with clear Vietnamese message."""
    bar = DockActionBar()
    asset = {
        "id": "sfx_punch",
        "name": "Heavy Punch",
        "category": "sfx",
        "availability": status,
        "duration": 0.8,
    }
    bar.set_asset(asset)
    assert not bar.btn_primary.isEnabled()
    assert bar.btn_tune.isEnabled()
    assert bar.btn_more.isEnabled()
    assert expected_text in bar.lbl_info.text()
    assert expected_text in bar.btn_primary.toolTip()


def test_action_bar_legacy_asset_without_availability_is_not_blocked(app):
    """Legacy assets without availability attribute/key default to online/available."""
    bar = DockActionBar()
    asset = {
        "id": "legacy_title",
        "name": "Legacy Neon Title",
        "category": "text",
        # No 'availability' key
    }
    bar.set_asset(asset)
    assert bar.btn_primary.isEnabled()
    assert bar.btn_tune.isEnabled()
    assert bar.btn_more.isEnabled()


def test_action_bar_object_based_asset_support(app):
    """Supports class/object based assets with attributes."""
    bar = DockActionBar()
    obj = DummyAssetObj("ovl_grain", "Film Grain 4K", "overlay", availability="offline")
    bar.set_asset(obj)
    assert not bar.btn_primary.isEnabled()
    assert bar.btn_tune.isEnabled()
    assert "Kho Offline" in bar.lbl_info.text()


def test_action_bar_re_enable_on_transition_from_offline_to_online(app):
    """Transitioning from offline asset to online asset re-enables primary button and clears tooltip."""
    bar = DockActionBar()
    offline_asset = {"id": "a1", "name": "Offline Asset", "category": "sfx", "availability": "offline"}
    bar.set_asset(offline_asset)
    assert not bar.btn_primary.isEnabled()

    online_asset = {"id": "a2", "name": "Online Asset", "category": "sfx", "availability": "online"}
    bar.set_asset(online_asset)
    assert bar.btn_primary.isEnabled()
    assert bar.btn_primary.toolTip() == ""
    assert "Kho Offline" not in bar.lbl_info.text()


def test_action_bar_set_asset_none_clears_and_disables(app):
    """Calling set_asset(None) resets to disabled state."""
    bar = DockActionBar()
    bar.set_asset({"id": "a1", "name": "Sample", "category": "sfx", "availability": "online"})
    assert bar.btn_primary.isEnabled()

    bar.set_asset(None)
    assert not bar.btn_primary.isEnabled()
    assert not bar.btn_tune.isEnabled()
    assert not bar.btn_more.isEnabled()
    assert "Chọn một mẫu" in bar.lbl_info.text()


def test_action_bar_menu_actions_enablement(app):
    """
    Menu mutation actions (new track, paste Fusion, install Fusion) are disabled when asset is unavailable,
    while non-mutating actions (copy code, favorite, open folder) remain enabled.
    """
    bar = DockActionBar()
    asset_offline = {
        "id": "tpl_text",
        "name": "Kinetic Text",
        "category": "text",
        "availability": "offline",
    }
    bar.set_asset(asset_offline)

    # Use internal menu builder helper to inspect QMenu actions without calling blocking exec()
    assert hasattr(bar, "_build_more_menu"), "DockActionBar must expose _build_more_menu for testability"
    menu = bar._build_more_menu()
    actions = {act.text(): act for act in menu.actions() if not act.isSeparator()}

    # Mutating actions MUST be disabled
    paste_act = [act for text, act in actions.items() if "Dán vào Fusion" in text]
    assert paste_act and not paste_act[0].isEnabled(), "Paste to Fusion must be disabled when offline"

    install_act = [act for text, act in actions.items() if "Cài vào thư mục Fusion" in text]
    assert install_act and not install_act[0].isEnabled(), "Install to Fusion must be disabled when offline"

    newtrack_act = [act for text, act in actions.items() if "Chèn trên track mới" in text]
    assert newtrack_act and not newtrack_act[0].isEnabled(), "Insert on new track must be disabled when offline"

    # Non-mutating actions MUST remain enabled
    copy_act = [act for text, act in actions.items() if "Sao chép code Fusion" in text]
    assert copy_act and copy_act[0].isEnabled(), "Copy code must remain enabled"

    fav_act = [act for text, act in actions.items() if "yêu thích" in text.lower()]
    assert fav_act and fav_act[0].isEnabled(), "Toggle favorite must remain enabled"

    folder_act = [act for text, act in actions.items() if "Mở thư mục chứa" in text]
    assert folder_act and folder_act[0].isEnabled(), "Open folder must remain enabled"
