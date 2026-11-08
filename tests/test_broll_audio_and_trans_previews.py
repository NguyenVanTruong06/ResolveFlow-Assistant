import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.core.pack_ingest import PackIngestService, guess
from src.core.asset_indexer import AssetIndexer
from src.core.storage_manager import StorageManager
from src.core.resolve_api import ResolveAutomation


def test_pack_ingest_preserves_transition_previews(tmp_path):
    """Kiểm tra pack_ingest không xóa các video / hình ảnh preview chuyển cảnh khi commit."""
    sm = StorageManager(config_dir=tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "external")

    zip_path = tmp_path / "test_trans.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Transitions/Whip_Pan.setting", "Fusion setting content")
        zf.writestr("Transitions/previews/Whip_Pan.mp4", "mp4 preview video")
        zf.writestr("Transitions/previews/Whip_Pan.png", "png thumbnail image")

    svc = PackIngestService(sm)
    job = svc.prepare(zip_path)

    # Đảm bảo master template được tự động gắn companion preview mp4 & png
    master_item = next(it for it in job["assets"] if "Whip_Pan.setting" in it["relative_path"])
    assert master_item.get("preview_relative_path") == "Transitions/previews/Whip_Pan.mp4"
    assert master_item.get("thumbnail_relative_path") == "Transitions/previews/Whip_Pan.png"

    committed = svc.commit(job)
    assert committed["pack_id"] is not None

    # Lấy thư mục gói từ registry và kiểm tra các tệp preview vẫn còn nguyên vẹn trên đĩa, không bị unlink
    pack_entry = next(p for p in sm.registry()["packs"] if p["id"] == committed["pack_id"])
    pack_folder = sm.root / pack_entry["folder"]
    all_files = [p.name for p in pack_folder.rglob("*") if p.is_file()]
    assert "Whip_Pan.setting" in all_files
    assert "Whip_Pan.mp4" in all_files
    assert "Whip_Pan.png" in all_files


def test_asset_indexer_scan_transitions_finds_preview_videos(tmp_path):
    """Kiểm tra AssetIndexer tự động tìm và gán preview_path từ video mp4 tương ứng."""
    trans_dir = tmp_path / "templates" / "transitions"
    previews_dir = trans_dir / "previews" / "SpeedRamp"
    previews_dir.mkdir(parents=True, exist_ok=True)

    # Setting file
    setting_file = trans_dir / "SpeedRamp" / "Zoom_In.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("Fusion")

    # Preview video in subfolder
    preview_mp4 = previews_dir / "Zoom_In.mp4"
    preview_mp4.write_text("mp4")

    indexer = AssetIndexer(base_dir=tmp_path)
    AssetIndexer.clear_cache()
    items = indexer.scan_transitions(force_refresh=True)

    matched = [it for it in items if it["name"] == "Zoom_In"]
    assert len(matched) == 1
    assert matched[0]["preview_path"] == str(preview_mp4)
    assert matched[0]["thumbnail_path"] == str(preview_mp4)


def test_resolve_api_insert_media_preserves_audio():
    """
    Kiểm tra insert_media_at_playhead với thuộc tính chuẩn của DaVinci Resolve thực tế:
    - GetClipProperty('Audio Channels') trả rỗng '', nhưng GetAudioMapping() / 'Audio Ch' / 'Type' báo có stereo audio.
    - TimelineItem thực tế KHÔNG có hàm GetMediaType() (chỉ có GetTrackTypeAndIndex / GetLinkedItems).
    - Tạo track audio 'stereo' (không dùng mặc định mono), chèn đồng bộ cả Video (V3) + Audio (A2) tại recordFrame,
      không truyền endFrame gây cắt cụt sample âm thanh khi chèn nguyên clip, và liên kết clip qua SetClipsLinked.
    """
    api = ResolveAutomation()

    mock_timeline = MagicMock()
    mock_timeline.GetCurrentTimecode.return_value = "01:00:00:00"
    mock_timeline.GetStartFrame.return_value = 86400
    audio_tracks = [1]  # Ban đầu chỉ có A1 (thoại chính)

    def _get_track_count(track_type):
        return 3 if track_type == "video" else audio_tracks[0]

    def _add_track(track_type, *args):
        if track_type == "audio":
            audio_tracks[0] += 1
        return True

    mock_timeline.GetTrackCount.side_effect = _get_track_count
    mock_timeline.AddTrack.side_effect = _add_track
    mock_timeline.GetIsTrackLocked.return_value = False
    mock_timeline.GetTrackSubType.return_value = "stereo"
    mock_timeline.GetItemListInTrack.return_value = []

    mock_media_pool = MagicMock()
    mock_item = MagicMock()
    real_props = {
        "File Path": "/dummy/broll_with_sound.mp4",
        "Frames": "180",
        "FPS": "30.0",
        "Audio Ch": "2",
        "Audio Codec": "AAC",
        "Type": "Video + Audio",
    }
    mock_item.GetClipProperty.side_effect = lambda prop=None: (
        dict(real_props) if not prop else real_props.get(prop, "")
    )
    mock_item.GetAudioMapping.return_value = '{"embedded_audio_channels": 2, "track_mapping": {"1": {"type": "Stereo"}}}'
    mock_item.GetName.return_value = "broll_with_sound.mp4"

    # TimelineItem thật trong Resolve KHÔNG có GetMediaType()
    class FakeResolveTimelineItem:
        def __init__(self, track_type: str, track_idx: int):
            self._track_type = track_type
            self._track_idx = track_idx

        def GetTrackTypeAndIndex(self):
            return [self._track_type, self._track_idx]

        def GetLinkedItems(self):
            return []

        def GetDuration(self):
            return 180

    placed_video_item = FakeResolveTimelineItem("video", 3)
    placed_audio_item = FakeResolveTimelineItem("audio", 2)
    mock_media_pool.AppendToTimeline.side_effect = [
        [placed_video_item],
        [placed_audio_item],
    ]

    mock_project = MagicMock()
    mock_project.GetMediaPool.return_value = mock_media_pool
    api.current_project = mock_project

    with patch.object(api, "is_connected", return_value=True), \
         patch.object(api, "get_active_timeline", return_value=mock_timeline), \
         patch.object(api, "import_media_to_media_pool", return_value=None), \
         patch.object(api, "get_media_pool_clips", return_value=[mock_item]), \
         patch("src.core.resolve_api.current_record_frame", return_value=(86400, "01:00:00:00")), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.isfile", return_value=False):

        ok = api.insert_media_at_playhead("/dummy/broll_with_sound.mp4", target_track=3, duration_sec=None)
        assert ok is True

        # Phải tạo track audio stereo thay vì mono mặc định
        mock_timeline.AddTrack.assert_any_call("audio", "stereo")

        calls = mock_media_pool.AppendToTimeline.call_args_list
        assert len(calls) == 2

        video_spec = calls[0][0][0][0]
        assert video_spec["trackIndex"] == 3
        assert video_spec["recordFrame"] == 86400
        assert video_spec["mediaType"] == 1

        audio_spec = calls[1][0][0][0]
        assert audio_spec["trackIndex"] == 2
        assert audio_spec["recordFrame"] == 86400
        assert audio_spec["mediaType"] == 2
        assert "startFrame" not in audio_spec
        assert "endFrame" not in audio_spec

        # Phải liên kết Video + Audio giống hành vi kéo thả trực tiếp
        mock_timeline.SetClipsLinked.assert_called_once_with([placed_video_item, placed_audio_item], True)


def test_resolve_api_insert_media_skips_busy_or_mono_audio_track():
    """Khi A2 đang bận hoặc là mono, âm thanh của B-roll/Meme phải sang A3 stereo trống thay vì ghi đè A2/A1."""
    api = ResolveAutomation()

    mock_timeline = MagicMock()
    mock_timeline.GetCurrentTimecode.return_value = "01:00:00:00"
    mock_timeline.GetStartFrame.return_value = 86400
    mock_timeline.GetTrackCount.side_effect = lambda t: 3 if t == "video" else 3
    mock_timeline.GetIsTrackLocked.return_value = False
    mock_timeline.GetTrackSubType.side_effect = lambda t, idx: "mono" if idx == 2 else "stereo"

    busy_clip = MagicMock()
    busy_clip.GetStart.return_value = 86390
    busy_clip.GetDuration.return_value = 100
    mock_timeline.GetItemListInTrack.side_effect = lambda t, idx: [busy_clip] if (t == "audio" and idx == 2) else []

    mock_media_pool = MagicMock()
    mock_item = MagicMock()
    mock_item.GetClipProperty.side_effect = lambda prop=None: {
        "File Path": "/dummy/meme.mp4",
        "Frames": "120",
        "Audio Ch": "2",
    }.get(prop, "") if prop else {"File Path": "/dummy/meme.mp4", "Frames": "120", "Audio Ch": "2"}
    mock_item.GetAudioMapping.return_value = '{"embedded_audio_channels": 2}'
    mock_item.GetName.return_value = "meme.mp4"

    v_item = MagicMock()
    a_item = MagicMock()
    mock_media_pool.AppendToTimeline.side_effect = [[v_item], [a_item]]

    mock_project = MagicMock()
    mock_project.GetMediaPool.return_value = mock_media_pool
    api.current_project = mock_project

    with patch.object(api, "is_connected", return_value=True), \
         patch.object(api, "get_active_timeline", return_value=mock_timeline), \
         patch.object(api, "import_media_to_media_pool", return_value=None), \
         patch.object(api, "get_media_pool_clips", return_value=[mock_item]), \
         patch("src.core.resolve_api.current_record_frame", return_value=(86400, "01:00:00:00")), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.isfile", return_value=False):

        ok = api.insert_media_at_playhead("/dummy/meme.mp4", target_track=3, duration_sec=None)
        assert ok is True

        audio_spec = mock_media_pool.AppendToTimeline.call_args_list[1][0][0][0]
        assert audio_spec["trackIndex"] == 3
        assert audio_spec["mediaType"] == 2
        mock_timeline.SetClipsLinked.assert_called_once_with([v_item, a_item], True)


def test_studio_and_edgedock_meme_insert_do_not_truncate_full_clip_to_3s(qapp, tmp_path):
    """
    Đảm bảo cả StudioAsset (TabAssets) lẫn EdgeDock / InsertService.insert(meme_dict)
    KHÔNG cắt cụt video Meme/B-roll về 3.0s mặc định mà truyền duration_sec=None (trọn vẹn clip & âm thanh).
    """
    from src.ui.tabs.tab_assets import TabAssets, StudioAsset
    from src.services.insert_service import InsertService
    from src.services.resolve_gateway import ResolveGateway

    meme_file = tmp_path / "viral_meme_8s.mp4"
    meme_file.write_bytes(b"FAKE_MP4_WITH_AUDIO")

    auto_mock = MagicMock()
    auto_mock.insert_media_at_playhead.return_value = True

    gateway = MagicMock(spec=ResolveGateway)

    def fake_submit(task, callback=None, errback=None):
        resolve = MagicMock()
        tl = MagicMock()
        tl.GetCurrentTimecode.return_value = "01:00:00:00"
        tl.GetTrackCount.return_value = 3
        auto_mock.get_active_timeline.return_value = tl
        with patch("src.core.resolve_api.ResolveAutomation", return_value=auto_mock):
            res_dict = task(resolve)
            if callback:
                callback(res_dict)

    gateway.submit.side_effect = fake_submit
    gateway._worker = MagicMock()
    gateway._worker.get_automation.return_value = auto_mock
    gateway._worker.get_resolve_object.return_value = MagicMock()

    service = InsertService(gateway)

    # 1. Luồng EdgeDock (gọi service.insert không truyền duration)
    res_dock = service.insert(
        {
            "id": "meme_dock_01",
            "name": "Viral Meme 8s",
            "tab": "meme",
            "category": "meme",
            "file_path": str(meme_file),
            "availability": "online",
        },
        mode="auto",
    )
    assert res_dock.ok is True
    assert auto_mock.insert_media_at_playhead.call_args[1]["duration_sec"] is None

    # 2. Luồng Studio TabAssets với StudioAsset quét từ ổ đĩa (mặc định duration hiển thị = 3.0, duration_sec = None)
    auto_mock.insert_media_at_playhead.reset_mock()
    tab = TabAssets(headless=True)
    try:
        tab._insert_service = service
        tab.selected_asset = StudioAsset(
            id="meme_studio_01",
            name="Viral Meme 8s Studio",
            tab="meme",
            category="trending",
            file_path=str(meme_file),
        )
        tab._insert_at_playhead()
        assert auto_mock.insert_media_at_playhead.called
        assert auto_mock.insert_media_at_playhead.call_args[1]["duration_sec"] is None
    finally:
        tab.close()
