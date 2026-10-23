import os
import zipfile
import pytest
from pathlib import Path
from src.core.pack_manager import PackManager, PackManifest, SecurityError


def test_create_and_inspect_pack(tmp_path: Path):
    # Setup mock assets
    meme_file = tmp_path / "dog.mp4"
    meme_file.write_text("fake video content")
    sfx_file = tmp_path / "whoosh.wav"
    sfx_file.write_text("fake audio content")

    manifest = PackManifest(
        id="viral_meme_pack",
        name="Viral Meme Starter Pack",
        version="1.0.0",
        author="ChunDVC",
        description="Pack meme đỉnh cao cho cộng đồng",
        categories=["meme", "sfx"]
    )

    pack_path = tmp_path / "viral_pack.rfpack"
    category_map = {
        "meme": [str(meme_file)],
        "sfx": [str(sfx_file)]
    }

    # 1. Create pack
    created_path = PackManager.create_pack(
        output_path=str(pack_path),
        manifest=manifest,
        category_files=category_map
    )
    assert os.path.exists(created_path)
    assert created_path.endswith(".rfpack")

    # 2. Inspect pack without extraction
    inspected = PackManager.inspect_pack(created_path)
    assert inspected.id == "viral_meme_pack"
    assert inspected.name == "Viral Meme Starter Pack"
    assert inspected.item_count == 2
    assert "meme" in inspected.categories
    assert "sfx" in inspected.categories


def test_install_pack(tmp_path: Path):
    meme_file = tmp_path / "funny.mp4"
    meme_file.write_text("video 123")
    title_file = tmp_path / "kinetic.setting"
    title_file.write_text("fusion macro")

    manifest = PackManifest(
        id="combo_pack",
        name="Combo Pack",
        version="1.2.0",
        author="EditorPro",
        description="Combo title and meme",
        categories=["meme", "titles"]
    )

    pack_path = tmp_path / "combo.rfpack"
    created_path = PackManager.create_pack(
        output_path=str(pack_path),
        manifest=manifest,
        category_files={
            "meme": [str(meme_file)],
            "titles": [str(title_file)]
        }
    )

    # Target library base dir
    target_lib = tmp_path / "mock_assets"
    installed_counts = PackManager.install_pack(created_path, target_base_dir=str(target_lib))

    assert installed_counts.get("meme") == 1
    assert installed_counts.get("titles") == 1

    # Verify extracted paths match ResolveFlow asset layout
    assert (target_lib / "broll_memes" / "memes" / "funny.mp4").exists()
    assert (target_lib / "templates" / "titles" / "kinetic.setting").exists()


def test_zip_slip_prevention(tmp_path: Path):
    malicious_pack = tmp_path / "evil.rfpack"
    with zipfile.ZipFile(malicious_pack, "w") as zf:
        zf.writestr("manifest.json", '{"id":"evil","name":"Evil","version":"1.0","author":"hacker","description":"x","categories":["meme"],"item_count":1}')
        zf.writestr("../../../../../tmp/pwned.txt", "malicious payload")

    target_lib = tmp_path / "safe_dir"
    with pytest.raises(SecurityError):
        PackManager.install_pack(str(malicious_pack), target_base_dir=str(target_lib))
