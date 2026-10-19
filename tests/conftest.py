import sys
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def pytest_unconfigure(config):
    import os
    app = QApplication.instance()
    if app is not None:
        app.closeAllWindows()
        app.processEvents()
    # On Linux with PySide6, Python finalization collides with Qt C++ static destructors.
    # Exit cleanly using the exit code set by pytest.
    os._exit(config.option.exitstatus if hasattr(config.option, "exitstatus") else 0)
