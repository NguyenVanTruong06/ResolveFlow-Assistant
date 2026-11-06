"""
tests/ui/test_source_settings_compat.py
Kiểm thử tính tương thích cấu hình Whisper của SourcePage:
- Primary: QSettings("ChunDVC", "ResolveFlow")
- Legacy: QSettings("ResolveFlow", "ResolveFlowAssistant")
- Precedence: Primary valid > Legacy valid > Default
- Import-on-read: Legacy hợp lệ được sao chép sang Primary 1 lần (không xóa legacy)
- Đọc độc lập từng key (whisper_model, whisper_lang)
- Validation: str không rỗng, không toàn whitespace; không chấp nhận bool, int, list, dict, None
- Setters: Ghi vào Primary only, kiểm tra sync/status, không mirror về Legacy
- Lỗi ghi/sync không làm mất giá trị runtime, không crash ứng dụng
- load_from_config không ghi đè vào QSettings
"""

import os
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.ui.director.director_state import DirectorConfig
from src.ui.director.pages import source_page
from src.ui.director.pages.source_page import (
    PRIMARY_SETTINGS_ORG,
    PRIMARY_SETTINGS_APP,
    LEGACY_SETTINGS_ORG,
    LEGACY_SETTINGS_APP,
    _is_valid_whisper_setting_value,
    _read_whisper_setting,
    _write_whisper_setting,
    _import_legacy_whisper_setting,
    SourcePage,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def isolated_ini_settings(tmp_path, monkeypatch):
    """
    Tạo hai tệp INI hoàn toàn độc lập trong tmp_path cho Primary và Legacy settings.
    Tuyệt đối không đụng tới ~/.config hoặc registry của user.
    """
    primary_file = tmp_path / "primary.ini"
    legacy_file = tmp_path / "legacy.ini"

    primary_s = QSettings(str(primary_file), QSettings.Format.IniFormat)
    legacy_s = QSettings(str(legacy_file), QSettings.Format.IniFormat)

    # Monkeypatch hàm cấp phát settings để SourcePage tự động dùng tệp INI cô lập
    monkeypatch.setattr(source_page, "_get_primary_settings", lambda: QSettings(str(primary_file), QSettings.Format.IniFormat))
    monkeypatch.setattr(source_page, "_get_legacy_settings", lambda: QSettings(str(legacy_file), QSettings.Format.IniFormat))

    return primary_s, legacy_s, primary_file, legacy_file


# ---------------------------------------------------------------------------
# 1. Validation tests
# ---------------------------------------------------------------------------

def test_validation_valid_values():
    """Chuỗi hợp lệ không bị rỗng hoặc toàn khoảng trắng."""
    assert _is_valid_whisper_setting_value("small") is True
    assert _is_valid_whisper_setting_value("medium") is True
    assert _is_valid_whisper_setting_value("vi") is True
    assert _is_valid_whisper_setting_value("en") is True
    assert _is_valid_whisper_setting_value("auto") is True
    assert _is_valid_whisper_setting_value("custom-whisper-v2") is True
    # Giá trị có khoảng trắng bao quanh vẫn được coi là hợp lệ (không trim để tránh đổi cache identity)
    assert _is_valid_whisper_setting_value("  custom_model  ") is True


def test_validation_invalid_values():
    """Bác bỏ dữ liệu hỏng: rỗng, toàn whitespace, None, bool, số, list, dict."""
    assert _is_valid_whisper_setting_value("") is False
    assert _is_valid_whisper_setting_value("   ") is False
    assert _is_valid_whisper_setting_value("\t\n") is False
    assert _is_valid_whisper_setting_value(None) is False
    assert _is_valid_whisper_setting_value(True) is False
    assert _is_valid_whisper_setting_value(False) is False
    assert _is_valid_whisper_setting_value(123) is False
    assert _is_valid_whisper_setting_value(0) is False
    assert _is_valid_whisper_setting_value(3.14) is False
    assert _is_valid_whisper_setting_value([]) is False
    assert _is_valid_whisper_setting_value(["small"]) is False
    assert _is_valid_whisper_setting_value({}) is False


# ---------------------------------------------------------------------------
# 2. Read Precedence & Independence tests
# ---------------------------------------------------------------------------

def test_read_setting_primary_precedence(isolated_ini_settings):
    """Primary có giá trị hợp lệ thì Primary thắng, không đọc hay ghi đè từ Legacy."""
    primary, legacy, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "medium")
    primary.sync()
    legacy.setValue("whisper_model", "base")
    legacy.sync()

    res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    assert res == "medium"
    assert legacy.value("whisper_model") == "base"


def test_read_setting_independent_keys(isolated_ini_settings):
    """Từng key được đọc độc lập: primary có model, legacy có lang."""
    primary, legacy, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "base")
    primary.sync()
    legacy.setValue("whisper_model", "large")
    legacy.setValue("whisper_lang", "en")
    legacy.sync()

    model = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    lang = _read_whisper_setting("whisper_lang", "vi", primary=primary, legacy=legacy)

    assert model == "base"  # từ primary, không bị legacy "large" ghi đè
    assert lang == "en"     # từ legacy fallback và import sang primary

    # Sau khi đọc, primary đã được import whisper_lang="en"
    assert primary.value("whisper_lang") == "en"
    # Legacy không bị thay đổi
    assert legacy.value("whisper_lang") == "en"
    assert legacy.value("whisper_model") == "large"


def test_read_setting_absent_primary_imports_legacy(isolated_ini_settings):
    """Khi Primary chưa có cấu hình, import giá trị hợp lệ từ Legacy sang Primary."""
    primary, legacy, _, _ = isolated_ini_settings
    assert not primary.contains("whisper_model")
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    assert res == "medium"

    # Primary phải có giá trị vừa import
    assert primary.contains("whisper_model")
    assert primary.value("whisper_model") == "medium"
    # Legacy vẫn nguyên vẹn
    assert legacy.value("whisper_model") == "medium"


def test_read_setting_invalid_primary_falls_back_to_legacy(isolated_ini_settings):
    """Khi Primary có dữ liệu rỗng hoặc sai kiểu, fallback sang Legacy và import lại."""
    primary, legacy, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "   ")
    primary.sync()
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    assert res == "medium"
    assert primary.value("whisper_model") == "medium"


def test_read_setting_both_invalid_returns_default(isolated_ini_settings):
    """Khi cả hai domain đều absent hoặc invalid, trả về default và KHÔNG ghi default."""
    primary, legacy, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "")
    legacy.setValue("whisper_model", "   ")
    primary.sync()
    legacy.sync()

    res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    assert res == "small"
    # Không tự ý ghi default vào settings
    assert primary.value("whisper_model") == ""
    assert legacy.value("whisper_model") == "   "


def test_read_setting_second_initialization_no_duplicate_import(isolated_ini_settings):
    """Lần 1 import từ Legacy; lần 2 đọc thẳng Primary, không gọi setValue import lặp."""
    primary, legacy, _, _ = isolated_ini_settings
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    # Lần 1: Primary rỗng, import từ Legacy
    val1 = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
    assert val1 == "medium"
    assert primary.value("whisper_model") == "medium"

    # Theo dõi xem primary.setValue có bị gọi ở lần 2 không
    with patch.object(primary, "setValue", wraps=primary.setValue) as mock_set:
        val2 = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
        assert val2 == "medium"
        mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Import & Write Safety / Error Handling
# ---------------------------------------------------------------------------

def test_import_failure_does_not_lose_read_value(isolated_ini_settings):
    """Nếu ghi hoặc sync vào Primary gặp lỗi, giá trị đọc từ Legacy vẫn được trả về đầy đủ."""
    primary, legacy, _, _ = isolated_ini_settings
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    # Giả lập primary.setValue ném exception
    with patch.object(primary, "setValue", side_effect=OSError("Read-only filesystem")):
        res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
        assert res == "medium"


def test_read_setting_import_sync_exception_preserves_legacy_value(isolated_ini_settings, caplog):
    """Nếu primary.sync() ném exception khi import, giá trị legacy vẫn được trả về nguyên vẹn."""
    primary, legacy, _, _ = isolated_ini_settings
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    with patch.object(primary, "sync", side_effect=OSError("Disk sync error")):
        with caplog.at_level("WARNING"):
            res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
        assert res == "medium"
        assert legacy.value("whisper_model") == "medium"
        assert "Ngoại lệ khi import cấu hình 'whisper_model' sang primary domain" in caplog.text


@pytest.mark.parametrize("error_status", [QSettings.Status.AccessError, QSettings.Status.FormatError])
def test_read_setting_import_status_error_preserves_legacy_value(isolated_ini_settings, error_status, caplog):
    """Nếu status() của Primary trả về AccessError hoặc FormatError, vẫn trả giá trị legacy."""
    primary, legacy, _, _ = isolated_ini_settings
    legacy.setValue("whisper_model", "medium")
    legacy.sync()

    with patch.object(primary, "status", return_value=error_status):
        with caplog.at_level("WARNING"):
            res = _read_whisper_setting("whisper_model", "small", primary=primary, legacy=legacy)
        assert res == "medium"
        assert legacy.value("whisper_model") == "medium"
        assert "Không thể import cấu hình 'whisper_model' sang primary domain" in caplog.text


@pytest.mark.parametrize("error_status", [QSettings.Status.AccessError, QSettings.Status.FormatError])
def test_import_legacy_status_error(isolated_ini_settings, error_status, caplog):
    """_import_legacy_whisper_setting trả về False khi status là AccessError hoặc FormatError."""
    primary, _, _, _ = isolated_ini_settings
    with patch.object(primary, "sync") as mock_sync, \
         patch.object(primary, "status", return_value=error_status):
        with caplog.at_level("WARNING"):
            ok = _import_legacy_whisper_setting(primary, "whisper_model", "medium")
        assert ok is False
        mock_sync.assert_called_once()
        assert "Không thể import cấu hình 'whisper_model' sang primary domain" in caplog.text


def test_write_setting_primary_only(isolated_ini_settings):
    """_write_whisper_setting chỉ ghi vào Primary, không ghi vào Legacy."""
    primary, legacy, _, _ = isolated_ini_settings
    legacy.setValue("whisper_model", "old_model")
    legacy.sync()

    ok = _write_whisper_setting("whisper_model", "large", primary=primary)
    assert ok is True
    assert primary.value("whisper_model") == "large"
    assert legacy.value("whisper_model") == "old_model"


def test_write_failure_does_not_crash(isolated_ini_settings):
    """Lỗi khi setValue/sync trả về False chứ không làm sập tiến trình."""
    primary, _, _, _ = isolated_ini_settings
    with patch.object(primary, "sync", side_effect=RuntimeError("Disk write failure")):
        ok = _write_whisper_setting("whisper_model", "large", primary=primary)
        assert ok is False


@pytest.mark.parametrize("error_status", [QSettings.Status.AccessError, QSettings.Status.FormatError])
def test_write_setting_status_error(isolated_ini_settings, error_status, caplog):
    """_write_whisper_setting trả về False khi status là AccessError hoặc FormatError."""
    primary, _, _, _ = isolated_ini_settings
    with patch.object(primary, "sync") as mock_sync, \
         patch.object(primary, "status", return_value=error_status):
        with caplog.at_level("WARNING"):
            ok = _write_whisper_setting("whisper_model", "large", primary=primary)
        assert ok is False
        mock_sync.assert_called_once()
        assert "Lỗi ghi cấu hình 'whisper_model' vào primary settings" in caplog.text


def test_write_whisper_setting_sync_exception(isolated_ini_settings, caplog):
    """_write_whisper_setting trả về False khi sync ném exception."""
    primary, _, _, _ = isolated_ini_settings
    with patch.object(primary, "sync", side_effect=OSError("Sync IO Error")):
        with caplog.at_level("WARNING"):
            ok = _write_whisper_setting("whisper_model", "large", primary=primary)
        assert ok is False
        assert "Ngoại lệ khi ghi cấu hình 'whisper_model' vào primary settings" in caplog.text


# ---------------------------------------------------------------------------
# 4. SourcePage UI Integration & Regressions
# ---------------------------------------------------------------------------

def test_source_page_init_loads_settings_correctly(isolated_ini_settings, qapp):
    """SourcePage khởi tạo đọc đúng cấu hình từ Primary/Legacy và không phát tín hiệu thừa."""
    primary, legacy, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "medium")
    legacy.setValue("whisper_lang", "en")
    primary.sync()
    legacy.sync()

    page = SourcePage()
    try:
        assert page._whisper_model == "medium"
        assert page._whisper_lang == "en"
        assert "Whisper medium · en" in page.btn_whisper_chip.text()
    finally:
        page.close()


def test_source_page_setters_update_runtime_and_primary(isolated_ini_settings, qapp):
    """_set_model và _set_lang ghi vào Primary, cập nhật nhãn và phát tín hiệu whisper_changed."""
    primary, legacy, _, _ = isolated_ini_settings
    page = SourcePage()
    signal_received = []
    page.whisper_changed.connect(lambda m, l: signal_received.append((m, l)))

    try:
        with patch("src.ui.director.pages.source_page.is_model_on_disk", return_value=True):
            page._set_model("large")
            page._set_lang("auto")

        assert page._whisper_model == "large"
        assert page._whisper_lang == "auto"
        assert "Whisper large · auto" in page.btn_whisper_chip.text()

        # Primary được cập nhật
        assert primary.value("whisper_model") == "large"
        assert primary.value("whisper_lang") == "auto"

        # Signal được phát đầy đủ
        assert ("large", "vi") in signal_received or ("large", "auto") in signal_received
        assert signal_received[-1] == ("large", "auto")
    finally:
        page.close()


def test_source_page_setters_write_failure_preserves_runtime_choice(isolated_ini_settings, qapp):
    """Nếu ghi vào primary thất bại, lựa chọn runtime của user vẫn được bảo toàn."""
    page = SourcePage()
    try:
        with patch("src.ui.director.pages.source_page.is_model_on_disk", return_value=True), \
             patch("src.ui.director.pages.source_page._write_whisper_setting", return_value=False):
            page._set_model("medium")
            assert page._whisper_model == "medium"
    finally:
        page.close()


def test_source_page_setter_real_sync_failure_preserves_runtime_and_signals(isolated_ini_settings, qapp):
    """Kiểm thử thực tế khi primary.sync() ném lỗi I/O: runtime choice, label, signal và recheck_cache vẫn hoạt động."""
    primary, _, _, _ = isolated_ini_settings
    page = SourcePage()
    signals = []
    page.whisper_changed.connect(lambda m, l: signals.append((m, l)))

    try:
        with patch("src.ui.director.pages.source_page.is_model_on_disk", return_value=True), \
             patch.object(primary, "sync", side_effect=OSError("Disk full on sync")):
            with patch.object(page, "recheck_cache") as mock_recheck:
                page._set_model("large")
                mock_recheck.assert_called_once()

        assert page._whisper_model == "large"
        assert "Whisper large" in page.btn_whisper_chip.text()
        assert ("large", "vi") in signals
    finally:
        page.close()


def test_load_from_config_overrides_runtime_without_persisting_to_settings(isolated_ini_settings, qapp):
    """load_from_config chỉ ghi đè runtime trong phiên mở project, không persist vào QSettings."""
    primary, _, _, _ = isolated_ini_settings
    primary.setValue("whisper_model", "small")
    primary.setValue("whisper_lang", "vi")
    primary.sync()

    page = SourcePage()
    try:
        config = DirectorConfig(whisper_model="large", whisper_language="en")
        page.load_from_config(config)

        # Runtime được ghi đè
        assert page._whisper_model == "large"
        assert page._whisper_lang == "en"

        # QSettings Primary KHÔNG bị thay đổi bởi project config
        assert primary.value("whisper_model") == "small"
        assert primary.value("whisper_lang") == "vi"
    finally:
        page.close()


def test_custom_model_and_auto_lang_preserved(isolated_ini_settings, qapp):
    """Tên model tùy biến và ngôn ngữ 'auto' không bị loại trừ hay ép kiểu."""
    primary, _, _, _ = isolated_ini_settings
    custom_model_name = "whisper-large-v3-turbo-custom"
    primary.setValue("whisper_model", custom_model_name)
    primary.setValue("whisper_lang", "auto")
    primary.sync()

    page = SourcePage()
    try:
        assert page._whisper_model == custom_model_name
        assert page._whisper_lang == "auto"
    finally:
        page.close()
