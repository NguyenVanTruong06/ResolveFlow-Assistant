import pytest
from PySide6.QtTest import QTest

from src.ui.director.director_window import DirectorWindow


@pytest.mark.parametrize("width", [1024, 1280, 1600])
def test_director_web_panel_fits_viewport_after_resize(qapp, width):
    window = DirectorWindow()
    window.resize(width, 760)
    window.show()
    window.go_to_step(3)
    page = window.page_review
    page.lbl_batch_summary.setText("Lô 1 · " + "clip_đã_phân_tích " * 35)
    page.common_review_widget.lbl_proposal_meta.setText("manifest_" + "1234567890" * 12)
    QTest.qWait(80)
    try:
        assert page.scroll_web.horizontalScrollBar().maximum() == 0
        assert page.p_web.width() <= page.scroll_web.viewport().width()
        for control in (
            page.btn_submit_batch_draft,
            page.btn_check_source_evidence,
            page.btn_paste_slot2,
            page.common_review_widget.btn_approve_proposal,
        ):
            page.scroll_web.ensureWidgetVisible(control)
            QTest.qWait(20)
            assert control.visibleRegion().boundingRect() == control.rect()
    finally:
        window.close()


def test_corner_menu_routes_to_the_existing_window_signals(qapp):
    from src.ui.widgets.app_header_actions import AppHeaderActions

    actions = AppHeaderActions()
    requests = []
    for name in ("studio", "director", "dock", "settings", "ai_setup"):
        getattr(actions, name + "_requested").connect(lambda name=name: requests.append(name))
        getattr(actions, "action_" + name).trigger()
    assert requests == ["studio", "director", "dock", "settings", "ai_setup"]
    actions.close()
