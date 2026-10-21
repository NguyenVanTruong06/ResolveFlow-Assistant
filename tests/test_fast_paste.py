import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_focus_davinci_resolve_mock():
    from src.core.fast_paste import focus_davinci_resolve

    with patch("src.core.fast_paste.platform.system", return_value="Linux"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert focus_davinci_resolve() is True

    with patch("src.core.fast_paste.platform.system", return_value="Windows"):
        mock_ctypes = MagicMock()
        mock_ctypes.windll.user32.FindWindowW.return_value = 12345
        mock_ctypes.windll.user32.SetForegroundWindow.return_value = 1
        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            assert focus_davinci_resolve() is True


def test_simulate_paste_hotkey_mock():
    from src.core.fast_paste import simulate_paste_hotkey

    with patch("src.core.fast_paste.platform.system", return_value="Linux"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert simulate_paste_hotkey() is True


def test_fast_paste_to_fusion_flow(qapp):
    from src.core.fast_paste import fast_paste_to_fusion

    mock_resolve = MagicMock()
    mock_resolve.GetCurrentPage.return_value = "fusion"

    sample_lua = 'TextPlus { Inputs = { StyledText = Input { Value = "FastPaste" } } }'

    with patch("src.core.fast_paste.focus_davinci_resolve", return_value=True):
        with patch("src.core.fast_paste.simulate_paste_hotkey", return_value=True):
            res = fast_paste_to_fusion(
                macro_code=sample_lua,
                resolve_app=mock_resolve,
            )
            assert res["ok"] is True
            assert res["mode"] == "fusion"
            assert res["pasted"] is True

            # Verify clipboard contains the Lua code
            clipboard_text = QApplication.clipboard().text()
            assert "StyledText" in clipboard_text
