import zipfile
import pytest
from pathlib import Path

from src.core.pack_manager import PackManager, PackManifest
from src.core.asset_indexer import AssetIndexer


def test_is_non_meme_video_classification():
    # Should identify transitions and tutorial previews
    assert AssetIndexer._is_non_meme_video("3D Box Bounce - Down.mp4") is True
    assert AssetIndexer._is_non_meme_video("Pan Tilt - Down.mp4") is True
    assert AssetIndexer._is_non_meme_video("Persp Basic Simple - Out Up.mp4") is True
    assert AssetIndexer._is_non_meme_video("Camera Flight Set 1 - Down.mp4") is True
    assert AssetIndexer._is_non_meme_video("01 - Step-By-Step Tutorial.mp4") is True
    assert AssetIndexer._is_non_meme_video("preview.mp4", filepath="/path/Preview Transitions/Fisheye.mp4") is True

    # Real memes should NOT be classified as non-meme
    assert AssetIndexer._is_non_meme_video("do_mixi_ao_that_day.mp4") is False
    assert AssetIndexer._is_non_meme_video("pewpew_so_qua_co.mov") is False
    assert AssetIndexer._is_non_meme_video("cat_dancing_meme.mp4") is False
    assert AssetIndexer._is_non_meme_video("01_funny_wow_reaction.mp4") is False


def test_pack_manager_routes_transition_previews_out_of_memes(tmp_path):
    # Create a mock transition pack with drfx, preview mp4, tutorial mp4, and sfx wav
    pack_zip = tmp_path / "test_transitions_pack.zip"
    with zipfile.ZipFile(pack_zip, "w") as zf:
        zf.writestr("Transitions for DR/TestTransition.drfx", b"mock drfx")
        zf.writestr("Preview Transitions/3D Box Bounce - Down.mp4", b"mock video")
        zf.writestr("Preview Transitions/3D Box Bounce - Down.png", b"mock png")
        zf.writestr("Help/01 - Step-By-Step Tutorial.mp4", b"mock tutorial")
        zf.writestr("GR Sound FX/whoosh.wav", b"mock sound")

    target_lib = tmp_path / "assets"
    counts = PackManager.install_pack(str(pack_zip), target_base_dir=str(target_lib))

    # Check installed file locations
    memes_dir = target_lib / "broll_memes" / "memes"
    if memes_dir.exists():
        files_in_memes = list(memes_dir.rglob("*.mp4"))
        assert len(files_in_memes) == 0, "No transition or tutorial videos should ever end up in memes!"

    # Verify transitions previews directory received the preview files
    trans_preview_dir = target_lib / "templates" / "transitions" / "previews"
    pack_folder = "Test Transitions Pack"
    assert (trans_preview_dir / pack_folder / "3D Box Bounce - Down.mp4").exists()
    assert (trans_preview_dir / pack_folder / "3D Box Bounce - Down.png").exists()

    # Verify tutorial went to docs
    docs_dir = target_lib / "docs" / "packs"
    assert (docs_dir / pack_folder / "01 - Step-By-Step Tutorial.mp4").exists()

    # Verify sound fx went to sfx
    sfx_dir = target_lib / "sfx"
    assert (sfx_dir / pack_folder / "whoosh.wav").exists()


def test_scan_memes_filters_out_transition_previews(tmp_path):
    mock_base = tmp_path / "mock_assets"
    memes_dir = mock_base / "broll_memes" / "memes"
    memes_dir.mkdir(parents=True, exist_ok=True)

    # 1 legitimate meme
    (memes_dir / "do_mixi_ao_that_day.mp4").write_text("meme")
    # 1 transition preview mistakenly present
    (memes_dir / "3D Box Bounce - Down.mp4").write_text("trans")

    indexer = AssetIndexer(base_dir=mock_base)
    AssetIndexer.clear_cache()
    memes = indexer.scan_memes(force_refresh=True)

    meme_names = [m["name"] for m in memes]
    assert "do_mixi_ao_that_day" in meme_names
    assert "3D Box Bounce - Down" not in meme_names
