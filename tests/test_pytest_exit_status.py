"""Kiểm tra mã thoát thật của pytest trong tiến trình con có Qt."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize(('body', 'status'), [('def test_ok(): pass', 0), ('def test_bad(): assert False', 1), ('raise RuntimeError("collection")', 2)])
def test_pytest_exit_status(tmp_path, body, status):
    config = Path(__file__).with_name('conftest.py').read_text()
    (tmp_path / 'conftest.py').write_text(config)
    (tmp_path / 'test_sample.py').write_text(body)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONUNBUFFERED='1')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == status, result.stdout + result.stderr
    assert ('passed' if status == 0 else 'error' if status == 2 else 'failed') in result.stdout
