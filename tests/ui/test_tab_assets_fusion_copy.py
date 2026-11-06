"""
tests/ui/test_tab_assets_fusion_copy.py
Kiểm thử TDD cho CopyFusion export trong TabAssets (G04-04C10-I).
"""
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QObject, QMimeData, QByteArray
from PySide6.QtWidgets import QApplication

from src.core.text_preset import TextStylePreset
from src.core.transition_preset import TransitionStylePreset
from src.ui.tabs.tab_assets import TabAssets, RecentManager


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
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with patch("src.core.asset_cache.request_thumbnail_async"):
        yield


@pytest.fixture
def tab_assets_headless(qapp):
    tab = TabAssets(headless=True)
    yield tab
    try:
        tab.close()
        tab.deleteLater()
    except Exception:
        pass
    qapp.processEvents()


def test_copy_fusion_procedural_customization_positive(tab_assets_headless):
    """1. Copy procedural text preset: Tùy biến từ UI được áp dụng, base preset bất biến, clipboard ghi đúng, emit 1 lần."""
    base_preset = TextStylePreset(
        id="hormozi_pop",
        name="Hormozi Pop",
        font="Arial",
        size=40,
        weight="normal",
        outline_width=0.1,
        animation="pop",
        timing_curve="spring",
        standard_color="#FFFFFF",
        highlight_color="#FACC15",
    )
    tab_assets_headless.selected_asset = {
        "id": "hormozi_pop",
        "name": "Hormozi Pop",
        "tab": "text",
        "_raw_obj": base_preset,
    }

    # Cấu hình UI overrides
    tab_assets_headless.txt_single_title.setText("Tiêu Đề Tùy Biến")
    tab_assets_headless.combo_font.addItem("DejaVu Sans")
    tab_assets_headless.combo_font.setCurrentText("DejaVu Sans")
    tab_assets_headless.slide_font_size.setValue(64)
    tab_assets_headless.slide_stroke_width.setValue(25)  # 0.25

    # Mock signal
    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    initial_clipboard = "SENTINEL_CLIPBOARD"
    QApplication.clipboard().setText(initial_clipboard)

    tab_assets_headless._on_copy_fusion_clicked()

    copied_text = QApplication.clipboard().text()
    assert copied_text != initial_clipboard
    assert "TextPlus" in copied_text
    assert "Tiêu Đề Tùy Biến" in copied_text
    assert "DejaVu Sans" in copied_text

    # Base preset không bị mutate
    assert base_preset.font == "Arial"
    assert base_preset.size == 40
    assert base_preset.outline_width == 0.1

    # Signal emit đúng 1 lần với ID
    signal_mock.assert_called_once_with("hormozi_pop")


def test_copy_fusion_managed_setting_bounded_read_positive(tab_assets_headless, tmp_path):
    """2. Copy managed .setting file: Bounded UTF-8 read, giữ nguyên nội dung gốc và newlines, 0 TextPlus giả."""
    root = tmp_path / "lib_root"
    setting_file = root / "title_pack" / "custom_skin.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    raw_setting_code = '{\n\tTools = ordered() {\n\t\tSkinRetouch = MacroOperator {\n\t\t}\n\t}\n}\n'
    setting_file.write_text(raw_setting_code, encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)

    tab_assets_headless.selected_asset = {
        "id": "skin_retouch_macro",
        "name": "Skin Retouch",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "title_pack/custom_skin.setting",
        "relative_path": "title_pack/custom_skin.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    copied = QApplication.clipboard().text()
    assert copied == raw_setting_code
    assert "MacroOperator" in copied
    assert "RF_skin_retouch_macro" not in copied  # KHÔNG sinh dummy TextPlus!
    signal_mock.assert_called_once_with("skin_retouch_macro")


def test_copy_fusion_missing_file_denied_preserves_clipboard(tab_assets_headless, tmp_path):
    """3. Managed .setting bị xóa/missing -> Từ chối fail-closed, bảo toàn 100% clipboard cũ, 0 signal."""
    root = tmp_path / "lib_root"
    setting_file = root / "missing.setting"
    storage = MockStorage(root=str(root), library_id="lib_01", online=True)

    tab_assets_headless.selected_asset = {
        "id": "missing_macro",
        "name": "Missing Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "missing.setting",
        "relative_path": "missing.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    sentinel = "PREVIOUS_USER_CLIPBOARD_TEXT_12345"
    QApplication.clipboard().setText(sentinel)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    # Clipboard phải được giữ nguyên vẹn 100%
    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_staging_denied_preserves_clipboard(tab_assets_headless, tmp_path):
    """4. Tài nguyên staging -> Từ chối fail-closed, giữ nguyên clipboard, 0 signal."""
    tab_assets_headless.selected_asset = {
        "id": "staged_macro",
        "name": "Staged Macro",
        "tab": "text",
        "source_kind": "managed",
        "library_path": "staged.setting",
        "relative_path": "staged.setting",
        "file_path": str(tmp_path / "staged.setting"),
        "availability": "staging",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    sentinel = "STAGING_SENTINEL_CLIPBOARD"
    QApplication.clipboard().setText(sentinel)

    tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_native_transition_denied_no_dummy(tab_assets_headless):
    """5. Native Transition -> Bị chặn, không sinh dummy macro/TextPlus, giữ nguyên clipboard."""
    trans_preset = TransitionStylePreset(id="trans_push", name="Push", transition_type="Push")
    tab_assets_headless.selected_asset = {
        "id": "trans_push",
        "name": "Push Transition",
        "tab": "trans",
        "category": "trans",
        "_raw_obj": trans_preset,
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    sentinel = "NATIVE_TRANSITION_CLIPBOARD_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_non_fusion_media_denied_no_dummy(tab_assets_headless, tmp_path):
    """6. Media non-fusion (MP4/WAV/CUBE) -> Bị chặn, không sinh TextPlus giả, giữ nguyên clipboard."""
    video_file = tmp_path / "meme.mp4"
    video_file.write_bytes(b"dummy")
    tab_assets_headless.selected_asset = {
        "id": "meme_video",
        "name": "Meme Video",
        "tab": "meme",
        "file_path": str(video_file),
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    sentinel = "NON_FUSION_MEDIA_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_file_exceeds_size_limit_rejected(tab_assets_headless, tmp_path):
    """7. File .setting vượt quá giới hạn MAX_FUSION_SETTING_BYTES -> Từ chối, giữ nguyên clipboard."""
    root = tmp_path / "lib_root"
    setting_file = root / "huge.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    # Ghi file 5MB + 100 bytes
    setting_file.write_bytes(b"X" * (5 * 1024 * 1024 + 100))

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)

    tab_assets_headless.selected_asset = {
        "id": "huge_macro",
        "name": "Huge Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "huge.setting",
        "relative_path": "huge.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    sentinel = "HUGE_FILE_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel


def test_copy_fusion_context_menu_stale_id_denied(tab_assets_headless):
    """8. Menu ngữ cảnh _on_lux_fusion_paste với stale asset id -> Deny, 0 clipboard write."""
    stale_asset = {"id": "non_existent_stale_id_999"}
    sentinel = "STALE_CONTEXT_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    tab_assets_headless._on_lux_fusion_paste(stale_asset)

    assert QApplication.clipboard().text() == sentinel


def test_fast_paste_short_circuits_on_denied_target(tab_assets_headless):
    """9. FastPaste short-circuits khi target bị denied/empty -> 0 fast_paste_to_fusion call."""
    tab_assets_headless.selected_asset = {
        "id": "denied_item",
        "name": "Denied Item",
        "tab": "meme",  # non-fusion
    }

    with patch("src.core.fast_paste.fast_paste_to_fusion") as mock_fp:
        tab_assets_headless._on_fast_paste_fusion_clicked()
        mock_fp.assert_not_called()


def test_copy_fusion_file_exact_limit_success(tab_assets_headless, tmp_path, monkeypatch):
    """10. Monkeypatch giới hạn nhỏ (80 bytes): File đúng 80 bytes UTF-8 đọc thành công, ghi clipboard, emit signal."""
    monkeypatch.setattr("src.ui.tabs.tab_assets.MAX_FUSION_SETTING_BYTES", 80)
    root = tmp_path / "lib_root"
    setting_file = root / "exact.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    exact_content = "{" + " " * 77 + "}\n"
    assert len(exact_content.encode("utf-8")) == 80
    setting_file.write_text(exact_content, encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)
    tab_assets_headless.selected_asset = {
        "id": "exact_macro",
        "name": "Exact Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "exact.setting",
        "relative_path": "exact.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == exact_content
    signal_mock.assert_called_once_with("exact_macro")


def test_copy_fusion_file_limit_plus_one_rejected(tab_assets_headless, tmp_path, monkeypatch):
    """11. File đúng limit + 1 bytes (81 bytes) -> Bị từ chối, giữ nguyên clipboard, 0 signal."""
    monkeypatch.setattr("src.ui.tabs.tab_assets.MAX_FUSION_SETTING_BYTES", 80)
    root = tmp_path / "lib_root"
    setting_file = root / "overflow.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    overflow_content = "{" + " " * 78 + "}\n"
    assert len(overflow_content.encode("utf-8")) == 81
    setting_file.write_text(overflow_content, encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)
    tab_assets_headless.selected_asset = {
        "id": "overflow_macro",
        "name": "Overflow Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "overflow.setting",
        "relative_path": "overflow.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)
    sentinel = "OVERFLOW_SENTINEL_CLIPBOARD"
    QApplication.clipboard().setText(sentinel)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_invalid_utf8_rejected(tab_assets_headless, tmp_path):
    """12. File .setting chứa byte không phải UTF-8 hợp lệ -> Bị từ chối, giữ nguyên clipboard."""
    root = tmp_path / "lib_root"
    setting_file = root / "invalid_utf8.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_bytes(b"Tools = \xff\xfe\x00\x00\x80")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)
    tab_assets_headless.selected_asset = {
        "id": "invalid_utf8_macro",
        "name": "Invalid UTF8 Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "invalid_utf8.setting",
        "relative_path": "invalid_utf8.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)
    sentinel = "INVALID_UTF8_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_empty_and_whitespace_file_rejected(tab_assets_headless, tmp_path):
    """13. File rỗng (0 bytes) hoặc chỉ chứa khoảng trắng -> Bị từ chối fail-closed."""
    root = tmp_path / "lib_root"
    empty_file = root / "empty.setting"
    empty_file.parent.mkdir(parents=True, exist_ok=True)
    empty_file.write_bytes(b"")

    ws_file = root / "whitespace.setting"
    ws_file.write_text("   \n\t  \r\n   ", encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)

    sentinel = "EMPTY_WS_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    for f_name, f_path in (("empty.setting", empty_file), ("whitespace.setting", ws_file)):
        tab_assets_headless.selected_asset = {
            "id": f"macro_{f_name}",
            "name": f_name,
            "tab": "text",
            "source_kind": "managed",
            "storage_managed": True,
            "origin_library_id": "lib_01",
            "origin_root": str(root),
            "library_path": f_name,
            "relative_path": f_name,
            "file_path": str(f_path),
            "availability": "online",
        }
        with patch("src.core.storage_manager.StorageManager", return_value=storage):
            tab_assets_headless._on_copy_fusion_clicked()

        assert QApplication.clipboard().text() == sentinel


def test_copy_fusion_non_regular_file_fstat_rejected(tab_assets_headless, tmp_path):
    """14. os.fstat trên file descriptor phát hiện không phải regular file -> Từ chối, giữ nguyên clipboard."""
    root = tmp_path / "lib_root"
    setting_file = root / "test_stat.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("Tools = {}", encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)
    tab_assets_headless.selected_asset = {
        "id": "fifo_macro",
        "name": "FIFO Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "test_stat.setting",
        "relative_path": "test_stat.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    sentinel = "FSTAT_FIFO_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    real_fstat = os.fstat
    def mock_fstat(fd):
        st = real_fstat(fd)
        fake_st = os.stat_result((
            stat.S_IFIFO | 0o644,
            st.st_ino, st.st_dev, st.st_nlink,
            st.st_uid, st.st_gid, st.st_size,
            st.st_atime, st.st_mtime, st.st_ctime
        ))
        return fake_st

    with patch("os.fstat", side_effect=mock_fstat):
        with patch("src.core.storage_manager.StorageManager", return_value=storage):
            tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel


def test_copy_fusion_file_read_io_error_rejected(tab_assets_headless, tmp_path):
    """15. Lỗi đọc I/O trên file descriptor (OSError) -> Bị từ chối an toàn, 0 clipboard write, 0 signal."""
    root = tmp_path / "lib_root"
    setting_file = root / "io_err.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("Tools = {}", encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)
    tab_assets_headless.selected_asset = {
        "id": "io_err_macro",
        "name": "IO Error Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "library_path": "io_err.setting",
        "relative_path": "io_err.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)
    sentinel = "IO_ERROR_SENTINEL"
    QApplication.clipboard().setText(sentinel)

    with patch("builtins.open", side_effect=OSError("Disk hardware fault")):
        with patch("src.core.storage_manager.StorageManager", return_value=storage):
            tab_assets_headless._on_copy_fusion_clicked()

    assert QApplication.clipboard().text() == sentinel
    signal_mock.assert_not_called()


def test_copy_fusion_clipboard_set_text_exception_handled(tab_assets_headless):
    """16. QApplication.clipboard().setText ném ngoại lệ -> Xử lý an toàn, 0 signal emit, 0 Recent add."""
    base_preset = TextStylePreset(id="text_clip_err", name="Text Clip Err")
    tab_assets_headless.selected_asset = {
        "id": "text_clip_err",
        "name": "Text Clip Err",
        "tab": "text",
        "_raw_obj": base_preset,
    }

    signal_mock = MagicMock()
    tab_assets_headless.copy_fusion_node_requested.connect(signal_mock)

    with patch.object(QApplication.clipboard(), "setText", side_effect=RuntimeError("Clipboard inaccessible")):
        with patch("src.ui.tabs.tab_assets.RecentManager.add") as mock_recent:
            tab_assets_headless._on_copy_fusion_clicked()
            mock_recent.assert_not_called()

    signal_mock.assert_not_called()


def test_copy_fusion_full_mime_preservation_when_denied(tab_assets_headless):
    """17. Toàn bộ MIME data (text, HTML, custom format) được bảo toàn nguyên vẹn 100% khi copy bị denied."""
    mime_before = QMimeData()
    mime_before.setText("SENTINEL_PLAIN_TEXT")
    mime_before.setHtml("<b>SENTINEL_BOLD_HTML</b>")
    mime_before.setData("application/x-resolveflow-sentinel", QByteArray(b"CUSTOM_BINARY_12345"))
    QApplication.clipboard().setMimeData(mime_before)

    # Asset bị từ chối: media non-fusion
    tab_assets_headless.selected_asset = {
        "id": "denied_non_fusion",
        "name": "Denied Non Fusion",
        "tab": "sfx",
    }

    tab_assets_headless._on_copy_fusion_clicked()

    mime_after = QApplication.clipboard().mimeData()
    assert mime_after.text() == "SENTINEL_PLAIN_TEXT"
    assert mime_after.html() == "<b>SENTINEL_BOLD_HTML</b>"
    assert bytes(mime_after.data("application/x-resolveflow-sentinel")) == b"CUSTOM_BINARY_12345"


def test_context_menu_copy_never_records_to_recent(tab_assets_headless):
    """18. Tuyệt đối KHÔNG ghi RecentManager qua menu ngữ cảnh _on_lux_fusion_paste cho cả valid lẫn denied targets."""
    valid_preset = TextStylePreset(id="context_valid_title", name="Context Valid Title")
    denied_asset = {"id": "context_denied_meme", "name": "Denied Meme", "tab": "meme"}
    tab_assets_headless.all_assets = [valid_preset, denied_asset]

    with patch("src.ui.tabs.tab_assets.RecentManager.add") as mock_recent:
        # Case A: Stale asset ID không tồn tại
        tab_assets_headless._on_lux_fusion_paste({"id": "stale_id_999"})
        mock_recent.assert_not_called()

        # Case B: Denied asset (meme) tồn tại trong all_assets
        sentinel_b = "SENTINEL_B"
        QApplication.clipboard().setText(sentinel_b)
        tab_assets_headless._on_lux_fusion_paste(denied_asset)
        assert QApplication.clipboard().text() == sentinel_b
        mock_recent.assert_not_called()

        # Case C: Valid asset (procedural text)
        tab_assets_headless._on_lux_fusion_paste(valid_preset)
        assert "TextPlus" in QApplication.clipboard().text()
        mock_recent.assert_not_called()
