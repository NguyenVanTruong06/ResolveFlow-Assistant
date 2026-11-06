"""
tests/ui/test_common_review_harness_evidence.py
=============================================================================
Finite Fake & Boundary Tests cho:
TASK_ID: G06-EVIDENCE-CLOSE-THEN-G07-D (Common Proposal Review Desktop Smoke)

Kiểm thử finite fake / boundaries:
1. Early setup failure ngay sau session/env setup: assert finally gọi, env restored, session_root removed, 0 leaks.
2. Clipboard init failure: assert finally gọi, env restored, session removed.
3. DirectorWindow constructor failure: assert finally gọi, env restored, session removed.
4. Failure sau show: assert finally gọi, DirectorWindow closed, env restored, session removed.
5. Hit-test negative assertion: widget ngoài viewport bị từ chối hit-test khi chưa cuộn.
6. Hit-test positive assertion: scroll-to-visible đưa điểm vào viewport.rect(), visibleRegion(), widgetAt().
7. Horizontal scroll measurement: xác thực phát hiện layout cần cuộn ngang trung thực.
=============================================================================
"""

import os
import sys
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget, QPushButton, QVBoxLayout
from PySide6.QtCore import Qt, QPoint

from task.desktop_verify_06c3_common_review import (
    run_harness,
    verify_and_scroll_to_visible,
    safe_visible_click,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    return app


def test_early_failure_after_session_setup(qapp):
    """Kiểm tra failure ngay sau session_root/env setup: finally dọn dẹp sạch sẽ."""
    exit_code = run_harness(fail_stage="after_session_setup")
    assert exit_code == 1

    metrics_file = Path(".bridge/ux-evidence/G06-EVIDENCE-CLOSE/desktop_metrics_after_session_setup.json")
    assert metrics_file.exists()
    import json
    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "EARLY_SETUP_FAILED_SAFELY_EXIT_1"
    cleanup = data["cleanup"]
    assert cleanup["session_ownership_status"] == "REMOVED"
    assert cleanup["env_cleanup"] == "RESTORED"
    assert cleanup["window_cleanup"] == "NONE"
    assert len(cleanup["running_workers_after"]) == 0


def test_early_failure_clipboard_init(qapp):
    """Kiểm tra failure tại bước khởi tạo clipboard."""
    exit_code = run_harness(fail_stage="clipboard_init_failure")
    assert exit_code == 1

    metrics_file = Path(".bridge/ux-evidence/G06-EVIDENCE-CLOSE/desktop_metrics_clipboard_init_failure.json")
    assert metrics_file.exists()
    import json
    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "EARLY_SETUP_FAILED_SAFELY_EXIT_1"
    cleanup = data["cleanup"]
    assert cleanup["session_ownership_status"] == "REMOVED"
    assert cleanup["env_cleanup"] == "RESTORED"
    assert cleanup["window_cleanup"] == "NONE"


def test_early_failure_director_constructor(qapp):
    """Kiểm tra failure trong constructor DirectorWindow."""
    exit_code = run_harness(fail_stage="constructor_failure")
    assert exit_code == 1

    metrics_file = Path(".bridge/ux-evidence/G06-EVIDENCE-CLOSE/desktop_metrics_constructor_failure.json")
    assert metrics_file.exists()
    import json
    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "EARLY_SETUP_FAILED_SAFELY_EXIT_1"
    cleanup = data["cleanup"]
    assert cleanup["session_ownership_status"] == "REMOVED"
    assert cleanup["env_cleanup"] == "RESTORED"
    assert cleanup["window_cleanup"] == "NONE"


def test_early_failure_after_show(qapp):
    """Kiểm tra failure sau khi show window: window phải được đóng an toàn."""
    exit_code = run_harness(fail_stage="after_show")
    assert exit_code == 1

    metrics_file = Path(".bridge/ux-evidence/G06-EVIDENCE-CLOSE/desktop_metrics_after_show.json")
    assert metrics_file.exists()
    import json
    data = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "EARLY_SETUP_FAILED_SAFELY_EXIT_1"
    cleanup = data["cleanup"]
    assert cleanup["session_ownership_status"] == "REMOVED"
    assert cleanup["env_cleanup"] == "RESTORED"
    assert cleanup["window_cleanup"] == "CLOSED"


def test_negative_off_viewport_hit_test(qapp):
    """Kiểm tra negative: target ở ngoài viewport bị từ chối hit-test khi auto_scroll=False."""
    sa = QScrollArea()
    sa.resize(400, 300)
    content = QWidget()
    content.resize(800, 2000)
    btn_bottom = QPushButton("Bottom Button", content)
    btn_bottom.setGeometry(100, 1500, 150, 40)
    sa.setWidget(content)
    sa.show()
    qapp.processEvents()

    ok, pt, reason, m = verify_and_scroll_to_visible(sa, btn_bottom, qapp, auto_scroll=False)
    assert ok is False
    assert "HIT_TEST_DENIED" in reason
    assert m["in_viewport_rect"] is False

    # Thử click với auto_scroll=False phải ném ngoại lệ
    with pytest.raises(RuntimeError, match="ERR_WIDGET_NOT_HITTABLE"):
        safe_visible_click(sa, btn_bottom, qapp, auto_scroll=False)

    sa.close()


def test_positive_scroll_to_visible_and_hit_test(qapp):
    """Kiểm tra positive: auto_scroll=True đưa target vào viewport và hit-test thành công."""
    sa = QScrollArea()
    sa.resize(400, 300)
    content = QWidget()
    content.resize(800, 2000)
    btn_bottom = QPushButton("Bottom Button", content)
    btn_bottom.setGeometry(100, 1500, 150, 40)
    sa.setWidget(content)
    sa.show()
    qapp.processEvents()

    clicked = []
    btn_bottom.clicked.connect(lambda: clicked.append(True))

    m = safe_visible_click(sa, btn_bottom, qapp, auto_scroll=True)
    assert m["in_viewport_rect"] is True
    assert m["in_visible_region"] is True
    assert m["widget_at_match"] is True
    assert len(clicked) == 1

    sa.close()


def test_horizontal_scroll_measurement(qapp):
    """Kiểm tra đo đạc phát hiện cuộn ngang khi layout rộng hơn viewport."""
    sa = QScrollArea()
    sa.resize(400, 300)
    content = QWidget()
    content.resize(1058, 600)  # Kích thước rộng giống CommonProposalReviewWidget
    sa.setWidget(content)
    sa.show()
    qapp.processEvents()

    hbar = sa.horizontalScrollBar()
    assert hbar is not None
    assert hbar.maximum() > 0
    horizontal_scroll_required = bool(hbar.maximum() > 0)
    assert horizontal_scroll_required is True

    sa.close()
