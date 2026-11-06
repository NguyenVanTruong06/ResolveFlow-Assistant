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
    """Kiểm tra insert_media_at_playhead cho phép chèn cả video và âm thanh (không khóa mediaType: 1)."""
    api = ResolveAutomation()
    
    mock_timeline = MagicMock()
    mock_timeline.GetCurrentTimecode.return_value = "01:00:00:00"
    mock_timeline.GetStartFrame.return_value = 86400
    mock_timeline.GetTrackCount.return_value = 2

    mock_media_pool = MagicMock()
    mock_item = MagicMock()
    mock_item.GetClipProperty.return_value = "2"  # 2 channels audio
    mock_item.GetName.return_value = "broll_with_sound.mp4"
    mock_media_pool.ImportMedia.return_value = [mock_item]

    placed_item = MagicMock()
    placed_item.GetMediaType.return_value = 1  # placed video
    mock_media_pool.AppendToTimeline.return_value = [placed_item]

    mock_project = MagicMock()
    mock_project.GetMediaPool.return_value = mock_media_pool
    api.current_project = mock_project

    with patch.object(api, "is_connected", return_value=True), \
         patch.object(api, "get_active_timeline", return_value=mock_timeline), \
         patch.object(api, "import_media_to_media_pool", return_value=None), \
         patch.object(api, "get_media_pool_clips", return_value=[mock_item]), \
         patch("src.core.resolve_api.current_record_frame", return_value=(86400, "01:00:00:00")), \
         patch("os.path.exists", return_value=True), \
         patch("os.path.isfile", return_value=True):
        
        ok = api.insert_media_at_playhead("/dummy/broll_with_sound.mp4", target_track=2)
        assert ok is True
        
        # Verify AppendToTimeline was called
        calls = mock_media_pool.AppendToTimeline.call_args_list
        assert len(calls) >= 1
        first_call_specs = calls[0][0][0]
        # Primary spec should not force mediaType: 1 when has_audio is True
        assert "mediaType" not in first_call_specs[0] or first_call_specs[0].get("mediaType") == 0
        
        # If placed clip was video-only, it should have called AppendToTimeline with mediaType: 2 for audio
        if len(calls) > 1:
            second_call_specs = calls[1][0][0]
            assert second_call_specs[0].get("mediaType") == 2
