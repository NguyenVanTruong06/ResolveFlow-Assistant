import pytest
from src.core.kinetic.kinetic_engine import (
    auto_plan,
    validate_plan,
    build_pages,
    page_to_comp,
    DEMO_WORDS,
    DEMO_PLAN,
    CANVAS,
)

def test_demo_plan_validation():
    # DEMO_PLAN should validate successfully with DEMO_WORDS
    validate_plan(DEMO_PLAN, len(DEMO_WORDS))

def test_plan_validation_catches_invalid_range():
    bad_plan = {
        "schema_version": "1.0",
        "canvas": {"aspect": "9:16"},
        "presets": DEMO_PLAN["presets"],
        "pages": [
            {"word_range": [0, 5], "preset": "hormozi"}  # > 4 words
        ]
    }
    with pytest.raises(ValueError, match="quá 4 từ"):
        validate_plan(bad_plan, len(DEMO_WORDS))

def test_plan_validation_catches_missing_words():
    # Plan covering only 0-1, but total is 13 words
    partial_plan = {
        "schema_version": "1.0",
        "canvas": {"aspect": "9:16"},
        "presets": DEMO_PLAN["presets"],
        "pages": [
            {"word_range": [0, 1], "preset": "hormozi"}
        ]
    }
    with pytest.raises(ValueError, match="chỉ phủ 2/13 từ"):
        validate_plan(partial_plan, len(DEMO_WORDS))

def test_auto_plan_generation():
    plan = auto_plan(DEMO_WORDS, aspect="9:16", max_words=3)
    assert plan["schema_version"] == "1.0"
    assert "hormozi" in plan["presets"]
    assert len(plan["pages"]) > 0
    # Auto plan must validate cleanly
    validate_plan(plan, len(DEMO_WORDS))

def test_build_pages_and_page_to_comp():
    fps = 30.0
    W, H = CANVAS["9:16"]
    pages = build_pages(DEMO_WORDS, DEMO_PLAN, fps)
    assert len(pages) == len(DEMO_PLAN["pages"])
    
    # Test rendering each page to Fusion .comp
    for page in pages:
        comp_txt, text_map = page_to_comp(page, fps, W, H)
        assert "Composition {" in comp_txt
        assert "Tools = ordered() {" in comp_txt
        assert "MediaOut1 = MediaOut" in comp_txt
        assert "TextPlus" in comp_txt
        assert "Transform" in comp_txt
        assert len(text_map) == len(page.words)
        for tool_name, txt in text_map.items():
            assert txt  # text is non-empty


def test_auto_window_apply_kinetic_text_plan(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication
    from src.ui.auto.auto_window import AutoWindow
    import json

    app = QApplication.instance() or QApplication([])
    win = AutoWindow()
    win.selected_files = [str(tmp_path / "dummy.mp4")]
    
    # Mock place_on_timeline and QMessageBox so UI doesn't block
    monkeypatch.setattr("src.core.kinetic.kinetic_engine.place_on_timeline", MagicMock())
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", MagicMock())
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", MagicMock())

    # Call _apply_json_text_plan with DEMO_PLAN JSON
    plan_json = json.dumps(DEMO_PLAN)
    win._apply_json_text_plan(plan_json)

    # Verify comps were created in _TIMELINE_IMPORT/kinetic_comps
    out_dir = tmp_path / "_TIMELINE_IMPORT" / "kinetic_comps"
    assert out_dir.exists()
    comp_files = list(out_dir.glob("page_*.comp"))
    assert len(comp_files) == len(DEMO_PLAN["pages"])
    win.close()
    win.deleteLater()


def test_auto_window_copy_kinetic_prompt_and_one_click(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication, QMessageBox
    from src.ui.auto.auto_window import AutoWindow

    app = QApplication.instance() or QApplication([])
    win = AutoWindow()
    win.selected_files = [str(tmp_path / "dummy.mp4")]

    # Mock place_on_timeline, QMessageBox
    monkeypatch.setattr("src.core.kinetic.kinetic_engine.place_on_timeline", MagicMock())
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", MagicMock())
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", MagicMock())
    # User clicks 'Yes' to use DEMO_WORDS
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", MagicMock(return_value=QMessageBox.Yes))

    # Test copy kinetic prompt
    win._copy_kinetic_prompt_to_clipboard()
    copied = QApplication.clipboard().text()
    assert "BẠN LÀ CHUYÊN GIA KINETIC TYPOGRAPHY" in copied
    assert "[0] Thành" in copied

    # Test 1-click kinetic
    win._on_one_click_kinetic_clicked()
    out_dir = tmp_path / "_TIMELINE_IMPORT" / "kinetic_comps"
    assert out_dir.exists()
    comp_files = list(out_dir.glob("page_*.comp"))
    assert len(comp_files) > 0  # auto_plan generated pages

    win.close()
    win.deleteLater()

