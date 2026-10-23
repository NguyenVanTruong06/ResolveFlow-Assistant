import os
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.core.pack_manager import PackManager, PackManifest
from src.core.asset_indexer import AssetIndexer
from src.ui.tabs.tab_asset_hub import AssetHubWidget


@pytest.fixture(scope="session")
def qapp():
    import sys
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_asset_hub_full_lifecycle(tmp_path: Path, qapp):
    # 1. Create external directory with mock assets
    ext_dir = tmp_path / "MyBigDrive_Memes"
    ext_dir.mkdir()
    meme_1 = ext_dir / "pewpew_reaction.mp4"
    meme_1.write_text("mp4 fake stream")
    sfx_1 = ext_dir / "whoosh_cinema.wav"
    sfx_1.write_text("wav fake stream")

    # 2. Setup Indexer & Hub
    indexer = AssetIndexer(base_dir=str(tmp_path / "mock_assets"), settings_scope="ResolveFlowE2E")
    hub = AssetHubWidget(indexer=indexer)

    # 3. Add linked folder via indexer
    added = indexer.add_linked_folder(str(ext_dir))
    assert added is True
    assert str(ext_dir.resolve()) in indexer.get_linked_folders()

    # 4. Verify scan picks up external files with tags
    memes = indexer.scan_memes()
    assert any(m["name"] == "pewpew_reaction" and m["is_external"] is True for m in memes)

    sfxs = indexer.scan_sfx()
    assert any(s["name"] == "whoosh_cinema" and s["is_external"] is True for s in sfxs)

    # 5. Export collection to .rfpack
    pack_file = tmp_path / "exported_test.rfpack"
    manifest = PackManifest(
        id="exported_test",
        name="Exported Test Pack",
        version="1.0.0",
        author="E2ETester",
        description="E2E test pack export"
    )
    PackManager.create_pack(
        output_path=str(pack_file),
        manifest=manifest,
        category_files={
            "meme": [str(meme_1)],
            "sfx": [str(sfx_1)]
        }
    )
    assert pack_file.exists()

    # 6. Inspect exported pack
    inspected = PackManager.inspect_pack(str(pack_file))
    assert inspected.item_count == 2
    assert inspected.id == "exported_test"

    # 7. Install pack into fresh library
    fresh_lib = tmp_path / "fresh_user_library"
    counts = PackManager.install_pack(str(pack_file), target_base_dir=str(fresh_lib))
    assert counts.get("meme") == 1
    assert counts.get("sfx") == 1
    assert (fresh_lib / "broll_memes" / "memes" / "Exported Test Pack" / "pewpew_reaction.mp4").exists()
    assert (fresh_lib / "sfx" / "Exported Test Pack" / "whoosh_cinema.wav").exists()
