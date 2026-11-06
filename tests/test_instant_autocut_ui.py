import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.ui.lux.shell.dock_header import DockHeaderWidget
from src.ui.lux.shell.dock_rail import DockRailWidget
from src.ui.lux.palette.command_registry import CommandRegistry
from src.services.autocut_service import AutoCutResult


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_dock_header_instant_autocut_button(qapp):
    header = DockHeaderWidget()
    assert hasattr(header, "btn_instant_autocut")
    assert header.btn_instant_autocut is not None
    assert "AutoCut" in header.btn_instant_autocut.toolTip()

    clicked_spied = []
    header.instant_autocut_clicked.connect(lambda: clicked_spied.append(True))
    header.btn_instant_autocut.click()

    assert len(clicked_spied) == 1


def test_dock_rail_instant_autocut_button(qapp):
    rail = DockRailWidget()
    assert hasattr(rail, "btn_instant_autocut")
    assert rail.btn_instant_autocut is not None
    assert "AutoCut" in rail.btn_instant_autocut.toolTip()

    clicked_spied = []
    rail.instant_autocut_clicked.connect(lambda: clicked_spied.append(True))
    rail.btn_instant_autocut.click()

    assert len(clicked_spied) == 1


def test_command_registry_instant_autocut():
    reg = CommandRegistry()
    cmd = reg.get("cmd_instant_autocut")
    assert cmd is not None
    assert "cắt khoảng lặng 1-click" in cmd.name

    # Test search by different keywords
    res1 = reg.search(">cắt")
    assert any(c.id == "cmd_instant_autocut" for c in res1)

    res2 = reg.search("autocut")
    assert any(c.id == "cmd_instant_autocut" for c in res2)

    res3 = reg.search("1-click")
    assert any(c.id == "cmd_instant_autocut" for c in res3)


def test_edge_dock_instant_autocut_integration(qapp):
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    dock = EdgeDockWindow()
    assert hasattr(dock, "run_instant_autocut")

    with patch("src.core.resolve_api.ResolveAutomation") as MockAuto:
        mock_inst = MagicMock()
        mock_inst.is_connected.return_value = False
        mock_inst.connect.return_value = False
        mock_inst.last_connect_error = "Chưa mở DaVinci Resolve"
        MockAuto.return_value = mock_inst

        # Trigger autocut when not connected
        dock.run_instant_autocut()
        assert dock._autocut_worker.wait(3000)
        qapp.processEvents()

        toast = dock._get_toast()
        assert "Chưa mở DaVinci Resolve" in toast.lbl_msg.text()
        dock.close()


def test_edge_dock_palette_command_trigger(qapp):
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    dock = EdgeDockWindow()
    with patch.object(dock, "run_instant_autocut") as mock_run:
        dock._on_palette_command_executed("cmd_instant_autocut")
        mock_run.assert_called_once()
