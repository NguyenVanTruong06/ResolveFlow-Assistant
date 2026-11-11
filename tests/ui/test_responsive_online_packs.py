import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea, QMessageBox
from src.ui.tabs.tab_asset_hub import AssetHubWidget


@pytest.mark.parametrize("width", [360, 480, 800, 1200])
def test_pack_grid_reflows_without_outer_horizontal_scrolling(qapp, tmp_path, monkeypatch, width):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    hub = AssetHubWidget()
    hub.resize(width, 760)
    hub.show()
    QTest.qWait(120)
    try:
        scroll = hub.findChild(QScrollArea)
        assert hub.width() == width
        assert scroll.horizontalScrollBar().maximum() == 0
        card = next(iter(hub._pack_cards.values()))
        for installed in (False, True):
            card.set_installed(installed)
            QTest.qWait(30)
            for button in (card.btn_download, card.btn_uninstall):
                if button.isHidden():
                    continue
                scroll.ensureWidgetVisible(button)
                QTest.qWait(20)
                assert button.visibleRegion().boundingRect() == button.rect()
        requested = []
        card.install_requested.disconnect(hub._on_install_pack_requested)
        card.install_requested.connect(requested.append)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        card.btn_download.click()
        assert requested == [card.manifest]
    finally:
        hub.close()
