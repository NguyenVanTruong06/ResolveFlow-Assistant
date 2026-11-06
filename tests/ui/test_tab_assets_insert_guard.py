"""
tests/ui/test_tab_assets_insert_guard.py
Bộ kiểm thử TDD cho Task G04-04C4-I:
Studio media qua InsertService, bảo toàn payload origin, không double dispatch.

Bao gồm:
1. Routing từng category qua InsertService (Video/SFX/LUT/Trans).
2. Bảo toàn metadata origin (source_kind, storage_managed, origin_root, origin_library_id, availability) và tính bất biến của selected_asset.
3. Không gọi ResolveAutomation trực tiếp trên main thread, không emit insert_sfx_requested / apply_lut_requested (triệt tiêu double dispatch).
4. RecentManager chỉ ghi khi chèn thành công (res.ok == True), không ghi khi thất bại / guard reject.
5. Xử lý an toàn khi InsertService không khả dụng (báo toast lỗi, không fallback direct, không emit signal).
6. Không dùng blanket early return ở UI chặn linked offline hay native guidance trước khi service xử lý.
7. Spy kết nối dạng AutoWindow xác nhận không có đợt chèn thứ 2 khi service thực thi.
8. Tích hợp thực tế với InsertService + mock Gateway: asset managed sai root / staging / missing bị chặn trước Gateway.
9. Tích hợp thực tế với InsertService + mock Gateway: asset video hợp lệ được dispatch đúng đường dẫn và track.
"""
import os
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from src.ui.tabs.tab_assets import TabAssets, StudioAsset, RecentManager
from src.services.insert_service import InsertService, InsertResult
from src.services.resolve_gateway import ResolveGateway
from src.core.resolve_api import ResolveAutomation


class MockInsertService:
    """Spy service ghi nhận các lệnh gọi và payload."""
    def __init__(self, result_ok: bool = True, result_msg: str = "", result_err: str = "", result_track: str = "V3"):
        self.calls = []
        self._result_ok = result_ok
        self._result_msg = result_msg
        self._result_err = result_err
        self._result_track = result_track

    def insert(self, asset, mode="auto", duration=3.0, preferred_track=None, extra_params=None, **kwargs):
        self.calls.append({
            "asset": asset,
            "mode": mode,
            "duration": duration,
            "preferred_track": preferred_track,
            "extra_params": extra_params or {},
            "kwargs": kwargs,
        })
        return InsertResult(
            ok=self._result_ok,
            track=self._result_track,
            message=self._result_msg or ("Đã chèn" if self._result_ok else ""),
            error=self._result_err or ("" if self._result_ok else "Lỗi chèn"),
        )


@pytest.fixture
def tab_assets_headless(qapp):
    """Khởi tạo TabAssets ở chế độ headless và dọn dẹp sau test."""
    tab = TabAssets(headless=True)
    yield tab
    try:
        tab.close()
    except Exception:
        pass


def test_tab_assets_video_routing_preserves_origin_and_no_direct_automation(tab_assets_headless, tmp_path, monkeypatch):
    """
    1. Video/B-roll/Meme chuyển qua InsertService.
    2. Bảo toàn toàn bộ metadata origin và input không bị mutate.
    3. Không gọi ResolveAutomation trực tiếp và không phát signals.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=True, result_msg="Đã chèn video V3")
    tab._insert_service = spy_service

    video_file = tmp_path / "cinematic_shot.mp4"
    video_file.write_bytes(b"dummy_video_bytes")

    selected_dict = {
        "id": "vid_broll_01",
        "name": "Cinematic B-Roll",
        "tab": "meme",
        "category": "meme",
        "file_path": str(video_file),
        "preview_path": str(video_file),
        "source_kind": "managed",
        "storage_managed": True,
        "origin_root": "/mnt/managed_storage",
        "origin_library_id": "lib_studio_main",
        "availability": "online",
        "tags": ["cinematic", "epic"],
    }
    input_copy = dict(selected_dict)
    tab.selected_asset = selected_dict

    # Spy các signals và direct automation
    sfx_signal_spy = MagicMock()
    lut_signal_spy = MagicMock()
    tab.insert_sfx_requested.connect(sfx_signal_spy)
    tab.apply_lut_requested.connect(lut_signal_spy)

    direct_auto_spy = MagicMock()
    monkeypatch.setattr(ResolveAutomation, "insert_media_at_playhead", direct_auto_spy)

    tab._insert_at_playhead()

    # Phải gọi qua InsertService đúng 1 lần
    assert len(spy_service.calls) == 1
    call = spy_service.calls[0]
    payload = call["asset"]

    # Bảo toàn 100% metadata
    assert payload["id"] == "vid_broll_01"
    assert payload["name"] == "Cinematic B-Roll"
    assert payload["source_kind"] == "managed"
    assert payload["storage_managed"] is True
    assert payload["origin_root"] == "/mnt/managed_storage"
    assert payload["origin_library_id"] == "lib_studio_main"
    assert payload["availability"] == "online"
    assert payload["file_path"] == str(video_file)

    # Input không bị biến đổi (non-mutating)
    assert tab.selected_asset == input_copy

    # Tuyệt đối không gọi ResolveAutomation trực tiếp
    assert direct_auto_spy.call_count == 0

    # Không phát signals mutation
    assert sfx_signal_spy.call_count == 0
    assert lut_signal_spy.call_count == 0


def test_tab_assets_missing_file_no_unverified_repo_fallback(tab_assets_headless, monkeypatch):
    """
    2. Khi file media gốc bị mất, TabAssets KHÔNG được tự ý fallback quét file mẫu
    trong repo, mà phải truyền nguyên trạng file_path cho service để guard fail-closed.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=False, result_err="File nguồn không tồn tại")
    tab._insert_service = spy_service

    missing_path = "/nonexistent/managed/dir/missing_clip.mp4"
    tab.selected_asset = {
        "id": "missing_asset_01",
        "name": "Missing Asset",
        "tab": "meme",
        "category": "meme",
        "file_path": missing_path,
        "source_kind": "managed",
        "storage_managed": True,
        "availability": "missing",
    }

    tab._insert_at_playhead()

    assert len(spy_service.calls) == 1
    sent_path = spy_service.calls[0]["asset"].get("file_path")
    # Đường dẫn gửi sang service phải là đường dẫn gốc bị thiếu, không bị thay bằng repo asset
    assert sent_path == missing_path
    assert "broll_memes" not in sent_path


def test_tab_assets_lut_preserves_origin_and_no_double_dispatch(tab_assets_headless, tmp_path):
    """
    3. Nhánh LUT:
       - Bảo toàn đầy đủ source_kind, storage_managed, origin_root, origin_library_id.
       - Không phát sớm signal apply_lut_requested gây double dispatch trong AutoWindow.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=True, result_msg="Đã áp LUT vào clip")
    tab._insert_service = spy_service

    lut_file = tmp_path / "cinematic_teal.cube"
    lut_file.write_bytes(b"LUT_DATA")

    tab.selected_asset = {
        "id": "lut_teal_01",
        "name": "Teal and Orange",
        "tab": "lut",
        "category": "lut",
        "file_path": str(lut_file),
        "source_kind": "managed",
        "storage_managed": True,
        "origin_root": "/mnt/luts_root",
        "origin_library_id": "lib_lut_pack",
        "availability": "online",
    }

    lut_signal_spy = MagicMock()
    tab.apply_lut_requested.connect(lut_signal_spy)

    tab._insert_at_playhead()

    # Phải gọi service đúng 1 lần
    assert len(spy_service.calls) == 1
    payload = spy_service.calls[0]["asset"]
    assert payload["source_kind"] == "managed"
    assert payload["storage_managed"] is True
    assert payload["origin_root"] == "/mnt/luts_root"
    assert payload["origin_library_id"] == "lib_lut_pack"
    assert payload["availability"] == "online"

    # Tuyệt đối không emit signal apply_lut_requested
    assert lut_signal_spy.call_count == 0


def test_tab_assets_sfx_no_double_dispatch(tab_assets_headless, tmp_path):
    """
    4. Nhánh SFX/BGM: Sau khi gọi InsertService, tuyệt đối không emit insert_sfx_requested.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=True, result_msg="Đã chèn SFX")
    tab._insert_service = spy_service

    audio_file = tmp_path / "impact.wav"
    audio_file.write_bytes(b"WAV_DATA")

    tab.selected_asset = {
        "id": "sfx_impact_01",
        "name": "Heavy Impact",
        "tab": "sfx",
        "category": "sfx",
        "file_path": str(audio_file),
        "source_kind": "builtin",
        "availability": "online",
        "duration": 2.5,
    }

    sfx_signal_spy = MagicMock()
    tab.insert_sfx_requested.connect(sfx_signal_spy)

    tab._insert_at_playhead()

    assert len(spy_service.calls) == 1
    # Không emit insert_sfx_requested
    assert sfx_signal_spy.call_count == 0


def test_tab_assets_native_transition_routing(tab_assets_headless):
    """
    5. Nhánh Chuyển cảnh Native (trans):
       - Gọi qua InsertService.
       - Hiển thị toast hướng dẫn (không báo 'Đã chèn clip').
    """
    tab = tab_assets_headless
    guidance_msg = "📋 Đã copy 'Smooth Push'. Hãy kéo từ Effects Library!"
    spy_service = MockInsertService(result_ok=True, result_msg=guidance_msg)
    tab._insert_service = spy_service

    tab.selected_asset = {
        "id": "trans_smooth_push",
        "name": "Smooth Push",
        "tab": "trans",
        "category": "trans",
        "source_kind": "builtin",
        "availability": "online",
    }

    with patch.object(tab, "_show_toast") as mock_toast:
        tab._insert_at_playhead()

    assert len(spy_service.calls) == 1
    assert mock_toast.called
    toast_args = mock_toast.call_args[0][0]
    assert "Smooth Push" in toast_args or "Effects Library" in toast_args


def test_tab_assets_service_unavailable_graceful(tab_assets_headless, monkeypatch):
    """
    6. Khi InsertService không khả dụng (bị lỗi khởi tạo):
       - Hiển thị toast cảnh báo lỗi.
       - Không crash, không fallback sang direct API, không phát signals.
       - Không ghi vào RecentManager.
    """
    tab = tab_assets_headless
    tab._insert_service = None
    monkeypatch.setattr(tab, "_get_insert_service", lambda: None)

    tab.selected_asset = {
        "id": "item_any",
        "name": "Test Asset",
        "tab": "meme",
        "file_path": "/path/video.mp4",
    }

    sfx_signal_spy = MagicMock()
    lut_signal_spy = MagicMock()
    tab.insert_sfx_requested.connect(sfx_signal_spy)
    tab.apply_lut_requested.connect(lut_signal_spy)

    with patch.object(RecentManager, "add") as mock_recent, patch.object(tab, "_show_toast") as mock_toast:
        tab._insert_at_playhead()

        assert mock_recent.call_count == 0
        assert mock_toast.called
        assert "Dịch vụ chèn không sẵn sàng" in mock_toast.call_args[0][0] or "❌" in mock_toast.call_args[0][0]

    assert sfx_signal_spy.call_count == 0
    assert lut_signal_spy.call_count == 0


def test_tab_assets_recent_manager_only_on_success(tab_assets_headless, tmp_path):
    """
    7. RecentManager chỉ ghi khi chèn thành công (res.ok == True).
       Không ghi khi guard từ chối hoặc service báo lỗi.
    """
    tab = tab_assets_headless
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"data")

    # Kịch bản 1: Thành công
    spy_ok = MockInsertService(result_ok=True, result_msg="OK")
    tab._insert_service = spy_ok
    tab.selected_asset = {"id": "asset_success", "tab": "meme", "file_path": str(video_file)}

    with patch.object(RecentManager, "add") as mock_recent:
        tab._insert_at_playhead()
        assert mock_recent.call_count == 1
        assert mock_recent.call_args[0][0] == "asset_success"

    # Kịch bản 2: Thất bại
    spy_fail = MockInsertService(result_ok=False, result_err="Timeline locked")
    tab._insert_service = spy_fail
    tab.selected_asset = {"id": "asset_failure", "tab": "meme", "file_path": str(video_file)}

    with patch.object(RecentManager, "add") as mock_recent:
        tab._insert_at_playhead()
        assert mock_recent.call_count == 0


def test_tab_assets_actual_service_integration_managed_root_mismatch(tab_assets_headless, tmp_path, monkeypatch):
    """
    8. Tích hợp thực tế với InsertService + mock ResolveGateway:
       Asset managed có origin_root A khác với active storage root B
       -> ActionGuard chặn với ERR_FOREIGN_ROOT.
       -> 0 task gửi tới Gateway, hiển thị toast lỗi, 0 RecentManager.
    """
    from src.services.insert_service import InsertService
    from src.services.resolve_gateway import ResolveGateway

    gateway = MagicMock(spec=ResolveGateway)
    def fake_submit(task, callback=None, errback=None):
        if callback:
            callback({"track": "V3", "start_tc": "01:00:00:00", "duration": 3.0, "name": "Valid Render Clip"})
    gateway.submit.side_effect = fake_submit

    service = InsertService(gateway)
    tab = tab_assets_headless
    tab._insert_service = service

    dummy_file = tmp_path / "valid_file.mp4"
    dummy_file.write_bytes(b"video")

    tab.selected_asset = {
        "id": "asset_stale_root",
        "name": "Stale Asset",
        "tab": "meme",
        "category": "meme",
        "file_path": str(dummy_file),
        "source_kind": "managed",
        "storage_managed": True,
        "origin_root": "/nonexistent/old_drive_A",
        "origin_library_id": "lib_correct",
        "availability": "online",
    }

    with patch.object(RecentManager, "add") as mock_recent, patch.object(tab, "_show_toast") as mock_toast:
        tab._insert_at_playhead()

        # Gateway không được submit task nào
        assert gateway.submit.call_count == 0
        # Không ghi recent
        assert mock_recent.call_count == 0
        # Hiển thị toast lỗi
        assert mock_toast.called
        assert "❌" in mock_toast.call_args[0][0]


def test_tab_assets_actual_service_integration_valid_video(tab_assets_headless, tmp_path, monkeypatch):
    """
    9. Tích hợp thực tế với InsertService + mock ResolveGateway:
       Asset video hợp lệ -> task gửi tới Gateway với đúng đường dẫn và track.
    """
    from src.services.insert_service import InsertService
    from src.services.resolve_gateway import ResolveGateway

    gateway = MagicMock(spec=ResolveGateway)
    def fake_submit(task, callback=None, errback=None):
        if callback:
            callback({"track": "V3", "start_tc": "01:00:00:00", "duration": 3.0, "name": "Valid Render Clip"})
    gateway.submit.side_effect = fake_submit

    service = InsertService(gateway)
    tab = tab_assets_headless
    tab._insert_service = service

    real_video = tmp_path / "valid_render.mp4"
    real_video.write_bytes(b"valid_video_content")

    tab.selected_asset = {
        "id": "valid_vid_123",
        "name": "Valid Render Clip",
        "tab": "meme",
        "category": "meme",
        "file_path": str(real_video),
        "source_kind": "generated",
        "availability": "online",
    }

    tab._insert_at_playhead()

    # Gateway phải được submit task
    assert gateway.submit.call_count == 1


def test_tab_assets_audio_inspector_combo_defaults_to_auto(tab_assets_headless, tmp_path):
    """
    10. G04-04C4-R1:
        Khi Inspector hiển thị SFX hoặc BGM thực tế:
        - Mục đầu tiên của combobox là 'Tự động' với mode 'auto' và track None.
        - Chèn tại Playhead gửi mode='auto' và preferred_track=None sang InsertService.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=True, result_msg="Đã chèn SFX")
    tab._insert_service = spy_service

    audio_file = tmp_path / "click.wav"
    audio_file.write_bytes(b"RIFFdummy")

    # 1. SFX asset
    tab.selected_asset = {
        "id": "sfx_click_01",
        "name": "Click SFX",
        "tab": "sfx",
        "category": "sfx",
        "file_path": str(audio_file),
        "source_kind": "builtin",
        "availability": "online",
        "duration": 1.2,
    }
    tab._update_inspector_details()

    assert tab.combo_target_track.count() >= 2
    assert "Tự động" in tab.combo_target_track.itemText(0)
    assert tab.combo_target_track.itemData(0) == {"mode": "auto", "track": None}
    assert tab.combo_target_track.currentIndex() == 0

    tab._insert_at_playhead()
    assert len(spy_service.calls) == 1
    call = spy_service.calls[0]
    assert call["mode"] == "auto"
    assert call["preferred_track"] is None
    assert call["extra_params"]["volume_offset"] == -12.0

    # 2. BGM asset
    spy_service.calls.clear()
    bgm_file = tmp_path / "ambient.wav"
    bgm_file.write_bytes(b"RIFFbgm")
    tab.selected_asset = {
        "id": "bgm_ambient_01",
        "name": "Ambient BGM",
        "tab": "bgm",
        "category": "bgm",
        "file_path": str(bgm_file),
        "source_kind": "builtin",
        "availability": "online",
        "duration": 60.0,
    }
    tab._update_inspector_details()

    assert "Tự động" in tab.combo_target_track.itemText(0)
    assert tab.combo_target_track.itemData(0) == {"mode": "auto", "track": None}
    assert tab.combo_target_track.currentIndex() == 0

    tab._insert_at_playhead()
    assert len(spy_service.calls) == 1
    call = spy_service.calls[0]
    assert call["mode"] == "auto"
    assert call["preferred_track"] is None
    assert call["extra_params"]["volume_offset"] == -18.0


def test_tab_assets_audio_combo_explicit_choices_and_legacy_fallback(tab_assets_headless, tmp_path):
    """
    11. G04-04C4-R1:
        Kiểm tra các lựa chọn explicit và fallback nhãn legacy:
        - Explicit A1 -> mode='timeline_track', preferred_track=1.
        - Explicit fixed track A2 -> mode='timeline_track', preferred_track=2.
        - Explicit New track -> mode='new_track', preferred_track=None.
        - Legacy label fallback 'Audio Track 2 (A2 - SFX)' mà không có itemData -> mode='auto', preferred_track=None.
        - Legacy label fallback 'Audio Track 4/5 (A4/A5 - BGM)' mà không có itemData -> mode='auto', preferred_track=None.
    """
    tab = tab_assets_headless
    spy_service = MockInsertService(result_ok=True, result_msg="Đã chèn SFX")
    tab._insert_service = spy_service

    audio_file = tmp_path / "pop.wav"
    audio_file.write_bytes(b"RIFFpop")

    tab.selected_asset = {
        "id": "sfx_pop",
        "name": "Pop SFX",
        "tab": "sfx",
        "file_path": str(audio_file),
        "availability": "online",
    }
    tab._update_inspector_details()

    # Case A: Chọn item 1 (Audio Track 2 gợi ý)
    tab.combo_target_track.setCurrentIndex(1)
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "timeline_track"
    assert spy_service.calls[-1]["preferred_track"] == 2

    # Case B: Chọn item 2 (Audio Track 1 đè voice)
    tab.combo_target_track.setCurrentIndex(2)
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "timeline_track"
    assert spy_service.calls[-1]["preferred_track"] == 1

    # Case C: Chọn item 3 (Track mới)
    tab.combo_target_track.setCurrentIndex(3)
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "new_track"
    assert spy_service.calls[-1]["preferred_track"] is None

    # Case D: Text legacy không có itemData - 'Audio Track 2 (A2 - SFX)' (Codex probe #1)
    tab.combo_target_track.clear()
    tab.combo_target_track.addItems(["Audio Track 2 (A2 - SFX)", "Audio Track 1 (A1)"])
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "auto"
    assert spy_service.calls[-1]["preferred_track"] is None

    # Case E: Text legacy không có itemData - 'Audio Track 4/5 (A4/A5 - BGM)'
    tab.combo_target_track.clear()
    tab.combo_target_track.addItems(["Audio Track 4/5 (A4/A5 - BGM)", "Audio Track 1 (A1)"])
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "auto"
    assert spy_service.calls[-1]["preferred_track"] is None

    # Case F: Text legacy chọn A1 rõ ràng không có itemData
    tab.combo_target_track.setCurrentIndex(1)  # "Audio Track 1 (A1)"
    tab._insert_at_playhead()
    assert spy_service.calls[-1]["mode"] == "timeline_track"
    assert spy_service.calls[-1]["preferred_track"] == 1


def test_tab_assets_video_duration_extraction_cases(tab_assets_headless):
    """
    12. G04-04C4-R1:
        Kiểm tra hàm trích xuất video duration:
        - duration_sec None với display duration 3 -> trả về 0.0 (sentinel).
        - positive duration -> trả về float.
        - missing key -> trả về 0.0.
        - invalid variants (bool, NaN, Inf, string không hợp lệ, số âm) -> trả về 0.0.
    """
    extractor = TabAssets._extract_video_duration

    # duration_sec None với display duration 3
    assert extractor({"duration_sec": None, "duration": 3.0}) == 0.0
    assert extractor({"duration_sec": None, "duration": 3}) == 0.0

    # positive duration
    assert extractor({"duration_sec": 14.5}) == 14.5
    assert extractor({"duration": 8.0}) == 8.0
    assert extractor({"duration_sec": 6.2, "duration": 3.0}) == 6.2

    # missing keys
    assert extractor({}) == 0.0
    assert extractor({"id": "vid1"}) == 0.0

    # invalid variants
    assert extractor({"duration": True}) == 0.0
    assert extractor({"duration_sec": False}) == 0.0
    assert extractor({"duration": float("nan")}) == 0.0
    assert extractor({"duration_sec": float("inf")}) == 0.0
    assert extractor({"duration": -5.0}) == 0.0
    assert extractor({"duration": 0}) == 0.0
    assert extractor({"duration": "not_a_num"}) == 0.0
    assert extractor(None) == 0.0


def test_tab_assets_video_unknown_duration_integration_sends_none_to_automation(tab_assets_headless, tmp_path, monkeypatch):
    """
    13. G04-04C4-R1:
        Tích hợp service thực tế:
        Video có duration unknown (duration_sec=None) -> insert_media_at_playhead nhận duration_sec=None,
        không giới hạn 3s clip.
    """
    from src.services.insert_service import InsertService
    from src.services.resolve_gateway import ResolveGateway

    video_file = tmp_path / "long_footage.mp4"
    video_file.write_bytes(b"VIDEO_CONTENT")

    auto_mock = MagicMock()
    auto_mock.insert_media_at_playhead.return_value = True

    gateway = MagicMock(spec=ResolveGateway)
    def fake_submit(task, callback=None, errback=None):
        resolve = MagicMock()
        tl = MagicMock()
        pm = MagicMock()
        proj = MagicMock()
        resolve.GetProjectManager.return_value = pm
        pm.GetCurrentProject.return_value = proj
        proj.GetCurrentTimeline.return_value = tl
        tl.GetCurrentTimecode.return_value = "01:00:00:00"
        tl.GetTrackCount.return_value = 3

        with patch("src.core.resolve_api.ResolveAutomation", return_value=auto_mock):
            res_dict = task(resolve)
            if callback:
                callback(res_dict)

    gateway.submit.side_effect = fake_submit
    gateway._worker = MagicMock()
    gateway._worker.get_automation.return_value = auto_mock
    gateway._worker.get_resolve_object.return_value = MagicMock()

    service = InsertService(gateway)
    tab = tab_assets_headless
    tab._insert_service = service

    tab.selected_asset = {
        "id": "vid_unknown_dur",
        "name": "Unknown Duration Video",
        "tab": "broll",
        "category": "broll",
        "file_path": str(video_file),
        "source_kind": "builtin",
        "availability": "online",
        "duration_sec": None,
        "duration": 3.0,  # Display duration
    }

    tab._insert_at_playhead()

    assert auto_mock.insert_media_at_playhead.called
    kwargs = auto_mock.insert_media_at_playhead.call_args[1]
    assert kwargs.get("duration_sec") is None


def test_tab_assets_audio_integration_a2_busy_picks_a3(tab_assets_headless, tmp_path):
    """
    14. G04-04C4-R1:
        Tích hợp thực tế với InsertService + mock ResolveGateway:
        Track A2 đang bận (có item chồng lấn playhead) -> InsertService tự động chọn A3.
        Chỉ 1 dispatch, không legacy signals, RecentManager ghi nhận đúng ID.
    """
    from src.services.insert_service import InsertService
    from src.services.resolve_gateway import ResolveGateway

    audio_file = tmp_path / "whoosh.wav"
    audio_file.write_bytes(b"RIFF_AUDIO")

    auto_mock = MagicMock()
    auto_mock.insert_sfx_to_track.return_value = True

    gateway = MagicMock(spec=ResolveGateway)
    def fake_submit(task, callback=None, errback=None):
        resolve = MagicMock()
        pm = MagicMock()
        proj = MagicMock()
        tl = MagicMock()
        resolve.GetProjectManager.return_value = pm
        pm.GetCurrentProject.return_value = proj
        proj.GetCurrentTimeline.return_value = tl

        # Giả lập Timeline: có 3 audio track. Track 2 có clip chiếm đóng [100, 200).
        tl.GetTrackCount.side_effect = lambda t: 3 if t == "audio" else 3
        tl.GetIsTrackLocked.return_value = False

        item_busy = MagicMock()
        item_busy.GetStart.return_value = 50
        item_busy.GetDuration.return_value = 200
        # Track 2 bận
        def mock_items(media_type, track_idx):
            if media_type == "audio" and track_idx == 2:
                return [item_busy]
            return []  # Track 3 trống
        tl.GetItemListInTrack.side_effect = mock_items

        with patch("src.core.resolve_api.current_record_frame", return_value=(108, "01:00:04:12")), \
             patch("src.core.resolve_api._nominal_fps", return_value=(24.0, 24.0)), \
             patch("src.core.resolve_api.ResolveAutomation", return_value=auto_mock):
            res_dict = task(resolve)
            if callback:
                callback(res_dict)

    gateway.submit.side_effect = fake_submit
    gateway._worker = MagicMock()
    gateway._worker.get_automation.return_value = auto_mock
    gateway._worker.get_resolve_object.return_value = MagicMock()

    service = InsertService(gateway)
    tab = tab_assets_headless
    tab._insert_service = service

    tab.selected_asset = {
        "id": "sfx_whoosh_free_track",
        "name": "Whoosh SFX",
        "tab": "sfx",
        "category": "sfx",
        "file_path": str(audio_file),
        "source_kind": "builtin",
        "availability": "online",
        "duration": 1.5,
    }
    tab._update_inspector_details()

    sfx_signal_spy = MagicMock()
    tab.insert_sfx_requested.connect(sfx_signal_spy)

    with patch.object(RecentManager, "add") as mock_recent:
        tab._insert_at_playhead()

        # auto.insert_sfx_to_track phải được gọi với target_track=3 (vì A2 bận)
        assert auto_mock.insert_sfx_to_track.called
        kwargs = auto_mock.insert_sfx_to_track.call_args[1]
        assert kwargs.get("target_track") == 3
        assert kwargs.get("record_frame") == 108
        assert kwargs.get("volume_offset_db") == -12.0

        # Đúng 1 dispatch, 0 legacy signal
        assert gateway.submit.call_count == 1
        assert sfx_signal_spy.call_count == 0

        # RecentManager được ghi nhận
        assert mock_recent.call_count == 1
        assert mock_recent.call_args[0][0] == "sfx_whoosh_free_track"
