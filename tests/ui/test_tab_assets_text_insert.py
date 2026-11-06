"""
tests/ui/test_tab_assets_text_insert.py
Kiểm thử TDD cho nhánh Text trong TabAssets._insert_at_playhead (G04-04C11-I):
1. Bảo toàn authority metadata nguồn gốc của selected_asset, extra_params chứa primitive.
2. Async dispatch qua svc.insert_async, không dùng QEventLoop, không gọi direct ResolveAutomation, không emit insert_title_requested.
3. Pending lock chống duplicate click; observer timeout 15s cảnh báo nhưng không mở khóa, không autorerun.
4. Late callback khi user đã chuyển selection sang asset B: giữ nguyên selection B, ghi Recent đúng asset A.
5. Filebacked .setting: Toast hướng dẫn trung thực, 0 job gửi service, 0 Recent add.
6. Kết quả thất bại: Toast lỗi, 0 Recent add.
7. Inspector button nhãn và tooltip tiếng Việt công bố rõ giới hạn render cơ bản.
"""
import time
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.text_preset import TextStylePreset
from src.ui.tabs.tab_assets import TabAssets, StudioAsset, RecentManager
from src.services.insert_service import InsertResult


class AsyncSpyInsertService:
    """Mock InsertService để test luồng bất đồng bộ trong TabAssets."""
    def __init__(self):
        self.calls = []
        self._pending_callbacks = []

    def insert_async(self, asset, mode="auto", duration=3.0, preferred_track=None, callback=None, extra_params=None, **kwargs):
        call_info = {
            "asset": asset,
            "mode": mode,
            "duration": duration,
            "preferred_track": preferred_track,
            "extra_params": extra_params or {},
            "callback": callback,
        }
        self.calls.append(call_info)
        if callback:
            self._pending_callbacks.append((callback, call_info))

    def trigger_callback(self, index=0, ok=True, message="Đã chèn", error="", track="V2"):
        if index < len(self._pending_callbacks):
            cb, info = self._pending_callbacks[index]
            res = InsertResult(
                ok=ok,
                track=track,
                item_name=info["extra_params"].get("text", "Title"),
                message=message,
                error=error,
                inserted_at=time.time(),
            )
            cb(res)


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


def test_tab_assets_text_insert_preserves_original_asset_authority(tab_assets_headless):
    """1. Giữ nguyên authority metadata của selected_asset, tách riêng extra_params primitive."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    preset = TextStylePreset(id="kinetic_orig", name="Kinetic Original", font="Arial", size=50)
    asset = StudioAsset(
        id="kinetic_orig",
        name="Kinetic Original",
        tab="text",
        category="text",
        native_preset=preset,
    )
    asset.source_kind = "builtin"
    asset.origin_library_id = "lib_builtin"
    asset.origin_root = "/builtin/root"
    asset.library_path = "/builtin/root/preset"
    tab.selected_asset = asset

    # Đổi thông số trên Inspector
    tab.txt_single_title.setText("NỘI DUNG TÙY BIẾN")
    tab.slide_font_size.setValue(72)

    tab._insert_at_playhead()

    assert len(spy_svc.calls) == 1
    call = spy_svc.calls[0]
    payload = call["asset"]

    # Authority metadata được giữ nguyên 100%
    assert payload.id == "kinetic_orig"
    assert payload.source_kind == "builtin"
    assert payload.origin_library_id == "lib_builtin"
    assert payload.origin_root == "/builtin/root"
    assert payload.library_path == "/builtin/root/preset"
    assert tab.selected_asset is asset  # Không bị thay thế bằng clone

    # Extra params chứa dữ liệu primitive
    extra = call["extra_params"]
    assert extra["text"] == "NỘI DUNG TÙY BIẾN"
    assert extra["size"] == 72
    assert "QWidget" not in str(type(extra["size"]))


def test_tab_assets_text_insert_async_no_direct_automation_nor_signal_emit(tab_assets_headless, monkeypatch):
    """2. Procedural text gọi insert_async, không gọi direct ResolveAutomation, không emit insert_title_requested."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    preset = TextStylePreset(id="title_pop", name="Title Pop")
    tab.selected_asset = StudioAsset(id="title_pop", name="Title Pop", tab="text", category="text", native_preset=preset)

    signals_emitted = []
    tab.insert_title_requested.connect(lambda *args: signals_emitted.append(args))

    def fake_auto_init():
        raise RuntimeError("ResolveAutomation should NOT be initialized on UI thread during insert!")

    monkeypatch.setattr("src.core.resolve_api.ResolveAutomation", fake_auto_init)

    tab._insert_at_playhead()

    assert len(spy_svc.calls) == 1
    assert len(signals_emitted) == 0


def test_tab_assets_text_insert_pending_lock_blocks_duplicate(tab_assets_headless):
    """3. Khóa pending chống duplicate click; nút bấm bị disable khi đang chèn."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    preset = TextStylePreset(id="title_pending", name="Title Pending")
    tab.selected_asset = StudioAsset(id="title_pending", name="Title Pending", tab="text", category="text", native_preset=preset)

    # Click lần 1
    tab._insert_at_playhead()
    assert len(spy_svc.calls) == 1
    assert not tab.btn_insert_title_playhead.isEnabled()
    assert "Đang chèn" in tab.btn_insert_title_playhead.text()

    # Click lần 2 khi chưa có callback
    toasts = []
    tab._show_toast = toasts.append
    tab._insert_at_playhead()

    assert len(spy_svc.calls) == 1  # Không dispatch lần 2
    assert any("Đang thực hiện" in t or "vui lòng đợi" in t for t in toasts)


def test_tab_assets_text_insert_observer_timeout_warns_without_unlock_nor_rerun(tab_assets_headless):
    """4. Observer timeout cảnh báo đang xử lý chậm; tuyệt đối không mở khóa và không rerun."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    preset = TextStylePreset(id="title_timeout", name="Title Timeout")
    tab.selected_asset = StudioAsset(id="title_timeout", name="Title Timeout", tab="text", category="text", native_preset=preset)

    toasts = []
    tab._show_toast = toasts.append

    tab._insert_at_playhead()
    assert len(spy_svc.calls) == 1

    # Kích hoạt observer timeout
    if hasattr(tab, "_insert_observer_timer") and tab._insert_observer_timer:
        tab._insert_observer_timer.timeout.emit()

    assert any("mất nhiều thời gian" in t or "chậm" in t for t in toasts)
    assert not tab.btn_insert_title_playhead.isEnabled()  # Nút vẫn khóa
    assert len(spy_svc.calls) == 1  # Không rerun duplicate


def test_tab_assets_text_insert_late_callback_resolves_cleanly_on_selection_change(tab_assets_headless, monkeypatch):
    """5. Late callback khi user đã chuyển selection sang asset B: giữ nguyên selection B, ghi Recent đúng asset A."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    preset_a = TextStylePreset(id="asset_A", name="Asset A")
    asset_a = StudioAsset(id="asset_A", name="Asset A", tab="text", category="text", native_preset=preset_a)
    preset_b = TextStylePreset(id="asset_B", name="Asset B")
    asset_b = StudioAsset(id="asset_B", name="Asset B", tab="text", category="text", native_preset=preset_b)

    recent_added = []
    monkeypatch.setattr(RecentManager, "add", recent_added.append)

    # User chọn A và bấm chèn
    tab.selected_asset = asset_a
    tab._insert_at_playhead()

    # Trong khi chờ, user click chọn asset B
    tab.selected_asset = asset_b

    # Callback của A trả về thành công
    spy_svc.trigger_callback(index=0, ok=True, message="Đã chèn A", track="V2")

    # Selection hiện tại vẫn phải là B
    assert tab.selected_asset is asset_b
    # Recent ghi nhận đúng asset A đã được chèn
    assert "asset_A" in recent_added
    assert "asset_B" not in recent_added
    # Nút bấm được mở khóa lại
    assert tab.btn_insert_title_playhead.isEnabled()


def test_tab_assets_text_insert_filebacked_setting_truthful_guidance_zero_job(tab_assets_headless, monkeypatch):
    """6. Filebacked .setting: Toast hướng dẫn trung thực, 0 job gửi service, 0 Recent add."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    recent_added = []
    monkeypatch.setattr(RecentManager, "add", recent_added.append)
    toasts = []
    tab._show_toast = toasts.append

    asset = StudioAsset(
        id="setting_asset",
        name="Setting Macro",
        tab="text",
        category="text",
        file_path="/path/to/macro.setting",
    )
    tab.selected_asset = asset

    tab._insert_at_playhead()

    assert len(spy_svc.calls) == 0
    assert len(recent_added) == 0
    assert any("chưa hỗ trợ chèn tự động" in t or ".setting" in t for t in toasts)


def test_tab_assets_text_insert_failed_result_zero_recent(tab_assets_headless, monkeypatch):
    """7. Kết quả thất bại: Toast báo lỗi, 0 Recent add."""
    tab = tab_assets_headless
    spy_svc = AsyncSpyInsertService()
    tab._insert_service = spy_svc

    recent_added = []
    monkeypatch.setattr(RecentManager, "add", recent_added.append)
    toasts = []
    tab._show_toast = toasts.append

    preset = TextStylePreset(id="title_fail", name="Title Fail")
    tab.selected_asset = StudioAsset(id="title_fail", name="Title Fail", tab="text", category="text", native_preset=preset)

    tab._insert_at_playhead()
    spy_svc.trigger_callback(index=0, ok=False, error="Timeline bị khóa")

    assert len(recent_added) == 0
    assert any("Timeline bị khóa" in t for t in toasts)
    assert tab.btn_insert_title_playhead.isEnabled()


def test_tab_assets_text_inspector_label_and_tooltip(tab_assets_headless):
    """8. Inspector button nhãn và tooltip tiếng Việt công bố rõ giới hạn render cơ bản."""
    tab = tab_assets_headless
    preset = TextStylePreset(id="title_hint", name="Title Hint")
    asset = StudioAsset(id="title_hint", name="Title Hint", tab="text", category="text", native_preset=preset)

    tab.selected_asset = asset
    tab._update_inspector_details()

    btn_text = tab.btn_insert_title_playhead.text()
    tooltip = tab.btn_insert_title_playhead.toolTip()

    assert "Chèn Text cơ bản" in btn_text
    assert "viền" in tooltip or "curve" in tooltip or "cơ bản" in tooltip
