"""
tests/ui/test_tab_assets_inspector_drag.py
Bộ kiểm thử TDD cho Task G04-04C7-I:
Guard Inspector drag, loại bỏ fake dummy files và bảo toàn tùy chỉnh chữ.

Bao gồm:
1. Managed media positive drag: MIME có URL thật, MIME builder không ghi RecentManager.
2. Denied media (missing, offline, staging, root ID swap, native transition): MIME rỗng, 0 file rác (/tmp/ResolveFlow_*).
3. Custom preset không thể biến denied target (staging / missing managed text / native) thành generator.
4. Text procedural customization: bảo toàn font, size, weight, stroke, animation, curve, colors, sample text; không làm biến dạng base preset.
5. DraggableAssetLabel & DraggableActionButton: kiểm tra gesture threshold, gating hasUrls và local URLs, QDrag.exec boundary.
6. Fallback của label khi không có callback: dùng shared helper có guard, không tự sinh dummy macro/txt.
"""
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QPoint, QUrl
from PySide6.QtGui import QMouseEvent, QDrag
from PySide6.QtWidgets import QApplication

from src.core.text_preset import TextStylePreset
from src.core.transition_preset import TransitionStylePreset
from src.ui.tabs.tab_assets import (
    TabAssets,
    DraggableAssetLabel,
    DraggableActionButton,
    StudioAsset,
    RecentManager,
)
from src.ui.lux.library.asset_grid import create_asset_mime_data


class MockStorage:
    def __init__(self, root: str, library_id: str, online: bool = True):
        self._root = Path(root)
        self.library_id = library_id
        self._config = {"root": str(self._root), "library_id": library_id}
        self.online = online

    @property
    def root(self):
        return self._root


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolate_thumbnail_and_temp(tmp_path, monkeypatch):
    """Cô lập thư mục tạm và thumbnail worker để tránh ô nhiễm và chạy đa luồng ngầm."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with patch("src.core.asset_cache.request_thumbnail_async"):
        yield


@pytest.fixture
def tab_assets_headless(qapp):
    """Khởi tạo TabAssets ở chế độ headless và dọn dẹp sạch sẽ sau test."""
    tab = TabAssets(headless=True)
    yield tab
    try:
        tab.close()
        tab.deleteLater()
    except Exception:
        pass
    qapp.processEvents()


# ==============================================================================
# 1. Inspector Drag Positive & Negative Guards
# ==============================================================================

def test_inspector_drag_managed_media_positive(tab_assets_headless, tmp_path):
    """Managed media hợp lệ trên đĩa -> MIME có đúng 1 local URL, builder không ghi RecentManager."""
    root = tmp_path / "lib_root"
    media_dir = root / "meme"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_file = media_dir / "cat_cry.mp4"
    media_file.write_bytes(b"video content")

    lib_id = "lib_cat"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    target = {
        "id": "meme_cat_cry",
        "name": "Cat Cry",
        "tab": "meme",
        "category": "meme",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "meme/cat_cry.mp4",
        "file_path": str(media_file),
        "availability": "online",
    }

    tab_assets_headless.selected_asset = target

    with patch("src.core.storage_manager.StorageManager", return_value=storage), \
         patch.object(RecentManager, "add") as mock_recent_add:
        mime = tab_assets_headless._create_drag_mime_data()
        # MIME builder không bao giờ được tự ghi Recent
        mock_recent_add.assert_not_called()

    assert mime.hasUrls()
    urls = mime.urls()
    assert len(urls) == 1
    assert urls[0].toLocalFile() == str(media_file.resolve())


def test_inspector_drag_missing_media_negative_no_dummy_files(tab_assets_headless, tmp_path):
    """Missing media -> MIME rỗng, không ghi file rác /tmp/ResolveFlow_*.wav/mp4, không ghi Recent."""
    target = {
        "id": "sfx_punch",
        "name": "Punch SFX",
        "tab": "sfx",
        "category": "sfx",
        "source_kind": "builtin",
        "file_path": str(tmp_path / "nonexistent_punch.wav"),
        "availability": "missing",
    }
    tab_assets_headless.selected_asset = target

    with patch.object(RecentManager, "add") as mock_recent_add:
        mime = tab_assets_headless._create_drag_mime_data()
        mock_recent_add.assert_not_called()

    assert not mime.hasUrls()
    # Kiểm tra không có file rác ResolveFlow_* nào trong tmp_path
    dummy_files = list(tmp_path.glob("ResolveFlow_*")) + list(tmp_path.glob("ChunDVC_*"))
    assert len(dummy_files) == 0


def test_inspector_drag_offline_storage_negative(tab_assets_headless, tmp_path):
    """Kho offline -> MIME rỗng."""
    root = tmp_path / "lib_root"
    media_file = root / "lut" / "creative.cube"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"LUT data")

    storage = MockStorage(root=str(root), library_id="lib_lut", online=False)

    target = {
        "id": "lut_creative",
        "name": "Creative LUT",
        "tab": "lut",
        "category": "lut",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_lut",
        "origin_root": str(root),
        "library_path": "lut/creative.cube",
        "file_path": str(media_file),
        "availability": "online",
    }
    tab_assets_headless.selected_asset = target

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        mime = tab_assets_headless._create_drag_mime_data()

    assert not mime.hasUrls()


def test_inspector_drag_staging_status_negative(tab_assets_headless, tmp_path):
    """Tài nguyên staging -> MIME rỗng fail-closed."""
    media_file = tmp_path / "staged.mp4"
    media_file.write_bytes(b"content")

    target = {
        "id": "staged_video",
        "name": "Staged Video",
        "tab": "meme",
        "source_kind": "builtin",
        "file_path": str(media_file),
        "availability": "staging",
    }
    tab_assets_headless.selected_asset = target

    mime = tab_assets_headless._create_drag_mime_data()
    assert not mime.hasUrls()


def test_inspector_drag_root_id_swap_negative(tab_assets_headless, tmp_path):
    """Tài nguyên bị tráo đổi origin_library_id so với kho đang online -> MIME rỗng."""
    root = tmp_path / "lib_root"
    media_file = root / "sound.wav"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"RIFF dummy")

    storage = MockStorage(root=str(root), library_id="lib_active_01", online=True)

    target = {
        "id": "sfx_forged",
        "name": "Forged SFX",
        "tab": "sfx",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_attacker_02",  # Sai ID!
        "origin_root": str(root),
        "file_path": str(media_file),
        "availability": "online",
    }
    tab_assets_headless.selected_asset = target

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        mime = tab_assets_headless._create_drag_mime_data()

    assert not mime.hasUrls()


def test_inspector_drag_native_transition_denied_no_macro(tab_assets_headless):
    """Chuyển cảnh native bị từ chối kéo thả (TRANSITION_DRAG_UNSUPPORTED) -> MIME rỗng, không sinh macro."""
    trans_preset = TransitionStylePreset(id="trans_zoom", name="Zoom In", transition_type="Zoom")
    target = {
        "id": "trans_zoom",
        "name": "Zoom In",
        "tab": "trans",
        "category": "trans",
        "_raw_obj": trans_preset,
    }
    tab_assets_headless.selected_asset = target

    with patch("src.core.transition_preset.TransitionMacroGenerator.export_setting_file") as mock_exp:
        mime = tab_assets_headless._create_drag_mime_data()
        mock_exp.assert_not_called()

    assert not mime.hasUrls()


# ==============================================================================
# 2. Text Customization Preservation & Anti-Bypass
# ==============================================================================

def test_inspector_drag_text_customization_preserved(tab_assets_headless, tmp_path):
    """
    Kéo thả Text procedural:
    - Thu thập đầy đủ thông số tùy biến từ Inspector (Font, Size, Weight, Stroke, Animation, Curve, Swatches, Sample Text).
    - Xuất file .setting chứa đúng các giá trị tùy biến.
    - Base preset ban đầu không bị mutate.
    """
    base_preset = TextStylePreset(
        id="title_neon",
        name="Neon Title",
        font="Arial",
        size=40,
        weight="normal",
        outline_width=0.1,
        animation="pop",
        timing_curve="spring",
        standard_color="#FFFFFF",
        highlight_color="#FACC15",
        outline_color="#000000",
        box_color="#EF4444",
        glow_color="#EF4444",
    )
    target = {
        "id": "title_neon",
        "name": "Neon Title",
        "tab": "text",
        "category": "text",
        "_raw_obj": base_preset,
    }
    tab_assets_headless.selected_asset = target

    # Tùy biến thông số trên widget của Inspector
    tab_assets_headless.txt_single_title.setText("Tiêu Đề Tùy Biến 2026")
    tab_assets_headless.combo_font.addItem("DejaVu Sans")
    tab_assets_headless.combo_font.setCurrentText("DejaVu Sans")
    tab_assets_headless.slide_font_size.setValue(72)
    if hasattr(tab_assets_headless, "slide_stroke_width"):
        tab_assets_headless.slide_stroke_width.setValue(35)  # 0.35

    if hasattr(tab_assets_headless, "weight_buttons") and "extrabold" in tab_assets_headless.weight_buttons:
        for b in tab_assets_headless.weight_buttons.values():
            b.setChecked(False)
        tab_assets_headless.weight_buttons["extrabold"].setChecked(True)

    if hasattr(tab_assets_headless, "anim_chips") and "bounce" in tab_assets_headless.anim_chips:
        for b in tab_assets_headless.anim_chips.values():
            b.setChecked(False)
        tab_assets_headless.anim_chips["bounce"].setChecked(True)

    if hasattr(tab_assets_headless, "curve_chips") and "linear" in tab_assets_headless.curve_chips:
        for b in tab_assets_headless.curve_chips.values():
            b.setChecked(False)
        tab_assets_headless.curve_chips["linear"].setChecked(True)

    tab_assets_headless.swatch_colors = {
        "text": "#112233",
        "highlight": "#445566",
        "stroke": "#778899",
        "box_glow": "#AABBCC",
    }

    from src.core.text_preset import FusionSettingGenerator
    with patch.object(FusionSettingGenerator, "export_setting_file", wraps=FusionSettingGenerator.export_setting_file) as spy_export:
        mime = tab_assets_headless._create_drag_mime_data()
        spy_export.assert_called_once()
        exported_preset = spy_export.call_args[0][0]
        assert isinstance(exported_preset, TextStylePreset)
        assert exported_preset.font == "DejaVu Sans"
        assert exported_preset.size == 72
        assert exported_preset.weight == "extra_bold"
        assert exported_preset.outline_width == 0.35
        assert exported_preset.animation == "bounce"
        assert exported_preset.timing_curve == "linear"
        assert exported_preset.standard_color == "#112233"
        assert exported_preset.highlight_color == "#445566"
        assert exported_preset.outline_color == "#778899"
        assert exported_preset.box_color == "#AABBCC"
        assert exported_preset.glow_color == "#AABBCC"
        assert spy_export.call_args[1].get("sample_text") == "Tiêu Đề Tùy Biến 2026" or spy_export.call_args[0][2] == "Tiêu Đề Tùy Biến 2026"

    assert mime.hasUrls()
    local_path = mime.urls()[0].toLocalFile()
    assert os.path.isfile(local_path)
    assert local_path.endswith(".setting")
    assert os.path.basename(local_path).startswith("ChunDVC_Drag_")

    # Đọc nội dung file .setting thật
    content = Path(local_path).read_text(encoding="utf-8")
    assert "StyledText = Input { Value = \"Tiêu Đề Tùy Biến 2026\", }" in content
    assert "Font = Input { Value = \"DejaVu Sans\", }" in content
    assert "Style = Input { Value = \"Extra_Bold\", }" in content or "Style = Input { Value = \"Extrabold\", }" in content or "Style = Input { Value = \"Bold\", }" in content
    
    # Kiểm tra conversion của size: 72 / 600.0 = 0.12
    assert "Size = Input { Value = 0.12, }" in content

    # Kiểm tra conversion của stroke: 0.35 * 0.3 = 0.105
    assert "Thickness2 = Input { Value = 0.105, }" in content

    # Đảm bảo base_preset gốc không hề bị thay đổi
    assert base_preset.font == "Arial"
    assert base_preset.size == 40
    assert base_preset.weight == "normal"
    assert base_preset.outline_width == 0.1


def test_inspector_drag_custom_preset_cannot_override_denied_target(tab_assets_headless, tmp_path):
    """
    Nếu target gốc bị guard deny (ví dụ managed text bị mất file hoặc staging):
    custom preset KHÔNG được phép qua mặt guard để xuất file setting.
    """
    denied_target = {
        "id": "managed_title_missing",
        "name": "Missing Managed Title",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_x",
        "origin_root": str(tmp_path / "lib_x"),
        "file_path": str(tmp_path / "lib_x" / "nonexistent.setting"),
        "availability": "missing",
    }
    tab_assets_headless.selected_asset = denied_target
    tab_assets_headless.txt_single_title.setText("Hacker Text Override")

    mime = tab_assets_headless._create_drag_mime_data()
    assert not mime.hasUrls()


# ==============================================================================
# 3. DraggableAssetLabel & DraggableActionButton Mouse Gesture & Exec Gating
# ==============================================================================

def test_draggable_asset_label_gating_and_gesture_cleared(qapp, tmp_path):
    """DraggableAssetLabel: gate hasUrls / local URLs, clear drag_start_pos sau threshold, gọi QDrag.exec khi hợp lệ."""
    label = DraggableAssetLabel()
    label.resize(200, 50)
    label.show()

    # Ca 1: Generator trả về MIME hợp lệ
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"content")

    def _valid_gen(p):
        m = create_asset_mime_data({
            "id": "v1",
            "name": "Valid Clip",
            "source_kind": "builtin",
            "file_path": str(valid_file),
            "availability": "online",
        })
        return m

    label.preset_getter = lambda: {"id": "v1"}
    label.mime_data_generator = _valid_gen

    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(10, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    label.mousePressEvent(press)
    assert label.drag_start_pos == QPoint(10, 10)

    move = QMouseEvent(QMouseEvent.Type.MouseMove, QPoint(25, 25), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    with patch.object(QDrag, "exec", return_value=Qt.DropAction.CopyAction) as mock_drag_exec:
        label.mouseMoveEvent(move)
        mock_drag_exec.assert_called_once()
        # Đã clear gesture sau threshold
        assert label.drag_start_pos is None

    # Ca 2: Generator trả về MIME rỗng (bị deny) -> KHÔNG gọi QDrag.exec
    def _empty_gen(p):
        from PySide6.QtCore import QMimeData
        return QMimeData()

    label.mime_data_generator = _empty_gen
    label.mousePressEvent(press)
    with patch.object(QDrag, "exec") as mock_drag_exec:
        label.mouseMoveEvent(move)
        mock_drag_exec.assert_not_called()
        assert label.drag_start_pos is None

    label.close()


def test_draggable_asset_label_fallback_without_generator_uses_guard(qapp, tmp_path):
    """DraggableAssetLabel không có mime_data_generator: dùng shared helper có guard, không tự sinh dummy file."""
    label = DraggableAssetLabel()
    label.preset_getter = lambda: {
        "id": "missing_sfx",
        "tab": "sfx",
        "source_kind": "builtin",
        "file_path": str(tmp_path / "missing.wav"),
        "availability": "missing",
    }
    label.mime_data_generator = None  # Không có generator tùy biến

    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(10, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    label.mousePressEvent(press)
    move = QMouseEvent(QMouseEvent.Type.MouseMove, QPoint(25, 25), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    with patch.object(QDrag, "exec") as mock_drag_exec:
        label.mouseMoveEvent(move)
        # Bị guard từ chối -> 0 exec
        mock_drag_exec.assert_not_called()

    # Không sinh bất kỳ file rác nào trong tmp_path
    assert len(list(tmp_path.glob("ResolveFlow_*"))) == 0
    label.close()


def test_draggable_action_button_gating_and_gesture_cleared(qapp, tmp_path):
    """DraggableActionButton: gate hasUrls / local URLs, clear gesture, gọi QDrag.exec khi hợp lệ."""
    valid_file = tmp_path / "btn_valid.mp4"
    valid_file.write_bytes(b"content")

    btn = DraggableActionButton(
        "Kéo DaVinci",
        mime_data_getter=lambda: create_asset_mime_data({
            "id": "b1",
            "name": "Button Clip",
            "source_kind": "builtin",
            "file_path": str(valid_file),
            "availability": "online",
        })
    )
    btn.resize(120, 36)
    btn.show()

    press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    btn.mousePressEvent(press)
    assert btn._drag_start_pos == QPoint(5, 5)

    move = QMouseEvent(QMouseEvent.Type.MouseMove, QPoint(20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    with patch.object(QDrag, "exec", return_value=Qt.DropAction.CopyAction) as mock_drag_exec:
        btn.mouseMoveEvent(move)
        mock_drag_exec.assert_called_once()
        assert btn._drag_start_pos is None

    # Ca denied: getter trả về empty MIME
    btn.mime_data_getter = lambda: create_asset_mime_data({
        "id": "denied",
        "source_kind": "builtin",
        "file_path": "/nonexistent.mp4",
        "availability": "missing",
    })
    btn.mousePressEvent(press)
    with patch.object(QDrag, "exec") as mock_drag_exec:
        btn.mouseMoveEvent(move)
        mock_drag_exec.assert_not_called()
        assert btn._drag_start_pos is None

    btn.close()
