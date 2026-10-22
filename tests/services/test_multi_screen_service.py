"""
Unit tests for MultiScreenService and Dual-Monitor Workflow.
"""
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QSettings

from src.services.multi_screen_service import MultiScreenService, get_multi_screen_service
from src.ui.studio.studio_window import StudioWindow
from src.ui.lux.shell.dock_settings_dialog import DockSettingsDialog


@pytest.fixture(autouse=True)
def clean_display_settings():
    settings = QSettings("ChunDVC", "ResolveFlow")
    orig_auto = settings.value("display/auto_open_studio_on_secondary", None)
    orig_screen = settings.value("display/studio_screen_index", None)
    yield
    if orig_auto is not None:
        settings.setValue("display/auto_open_studio_on_secondary", orig_auto)
    else:
        settings.remove("display/auto_open_studio_on_secondary")
    if orig_screen is not None:
        settings.setValue("display/studio_screen_index", orig_screen)
    else:
        settings.remove("display/studio_screen_index")


def test_multi_screen_service_singleton_and_basics(qapp):
    svc = get_multi_screen_service()
    assert svc is not None
    assert isinstance(svc, MultiScreenService)

    screens = svc.get_screens()
    assert isinstance(screens, list)
    count = svc.get_screen_count()
    assert count == len(screens)
    assert svc.is_multi_monitor() == (count >= 2)


def test_multi_screen_service_screens_info(qapp):
    svc = get_multi_screen_service()
    info = svc.get_screens_info()
    assert isinstance(info, list)
    assert len(info) == svc.get_screen_count()

    for item in info:
        assert "index" in item
        assert "name" in item
        assert "is_primary" in item
        assert "width" in item and item["width"] > 0
        assert "height" in item and item["height"] > 0
        assert "avail_width" in item


def test_multi_screen_service_other_screen_index(qapp):
    svc = get_multi_screen_service()
    count = svc.get_screen_count()
    if count >= 2:
        assert svc.get_other_screen_index(0) == 1
        assert svc.get_other_screen_index(1) == 0
    else:
        assert svc.get_other_screen_index(0) == 0


def test_multi_screen_service_settings_persistence(qapp):
    svc = get_multi_screen_service()

    svc.set_preferred_studio_screen(1)
    assert svc.get_preferred_studio_screen() == 1

    svc.set_preferred_studio_screen(0)
    assert svc.get_preferred_studio_screen() == 0

    svc.set_auto_open_studio_on_secondary(True)
    assert svc.is_auto_open_studio_on_secondary() is True

    svc.set_auto_open_studio_on_secondary(False)
    assert svc.is_auto_open_studio_on_secondary() is False


def test_multi_screen_move_widget_safe(qapp):
    svc = get_multi_screen_service()
    w = QWidget()
    w.resize(400, 300)
    w.show()

    success = svc.move_widget_to_screen(w, 0)
    assert success is True

    screen_idx = svc.get_screen_index_of_widget(w)
    assert screen_idx == 0

    if svc.is_multi_monitor():
        success_sec = svc.move_widget_to_screen(w, 1)
        assert success_sec is True
        assert svc.get_screen_index_of_widget(w) == 1

    w.close()


def test_studio_window_screen_switch_button(qapp):
    win = StudioWindow()
    win.show()

    assert hasattr(win, "btn_switch_screen")
    svc = get_multi_screen_service()

    if svc.is_multi_monitor():
        assert win.btn_switch_screen.isEnabled()
        curr_text = win.btn_switch_screen.text()
        assert "🖥️ Sang Màn" in curr_text

        # Click button to switch screen
        win.btn_switch_screen.click()
        new_text = win.btn_switch_screen.text()
        assert new_text != curr_text
    else:
        assert not win.btn_switch_screen.isEnabled()
        assert "1 Màn Hình" in win.btn_switch_screen.text()

    win.close()


def test_dock_settings_dialog_dual_monitor_controls(qapp):
    dlg = DockSettingsDialog()
    assert hasattr(dlg, "chk_auto_secondary")

    svc = get_multi_screen_service()
    if svc.is_multi_monitor():
        assert dlg.chk_auto_secondary.isEnabled()

    signals_received = []
    dlg.auto_secondary_studio_changed.connect(lambda v: signals_received.append(v))

    dlg.chk_auto_secondary.setChecked(False)
    assert svc.is_auto_open_studio_on_secondary() is False
    assert signals_received[-1] is False

    dlg.chk_auto_secondary.setChecked(True)
    assert svc.is_auto_open_studio_on_secondary() is True
    assert signals_received[-1] is True

    # Test dock screen selection
    dock_scr_signals = []
    dlg.dock_screen_changed.connect(lambda s: dock_scr_signals.append(s))
    if hasattr(dlg, "rad_dock_scr1"):
        dlg.rad_dock_scr1.setChecked(True)
        assert svc.get_preferred_dock_screen() == 0
        if dlg.rad_dock_scr2.isEnabled():
            dlg.rad_dock_scr2.setChecked(True)
            assert svc.get_preferred_dock_screen() == 1
            assert dock_scr_signals[-1] == 1
            dlg.rad_dock_scr1.setChecked(True)
            assert svc.get_preferred_dock_screen() == 0
            assert dock_scr_signals[-1] == 0

    dlg.close()

