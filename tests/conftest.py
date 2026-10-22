import sys
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def pytest_unconfigure(config):
    import os
    # On Linux with PySide6, Python finalization collides with Qt C++ static destructors.
    # Exit cleanly using the exit code set by pytest without triggering widget destructor segfaults.
    exit_code = getattr(config.option, "exitstatus", 0)
    os._exit(exit_code if exit_code is not None else 0)
