import os
import pytest
from pathlib import Path
from PySide6.QtCore import QSettings
from src.core.asset_indexer import AssetIndexer


@pytest.fixture(autouse=True)
def clean_qsettings():
    settings = QSettings("ChunDVC", "ResolveFlowTest")
    settings.setValue("library/linked_folders", [])
    yield
    settings.setValue("library/linked_folders", [])


def test_add_and_remove_linked_folders(tmp_path: Path):
    settings = QSettings("ChunDVC", "ResolveFlowTest")
    indexer = AssetIndexer(base_dir=str(tmp_path), settings_scope="ResolveFlowTest")

    ext_folder_1 = tmp_path / "MyDriveMemes"
    ext_folder_1.mkdir()
    ext_folder_2 = tmp_path / "DriveSFX"
    ext_folder_2.mkdir()

    indexer.add_linked_folder(str(ext_folder_1))
    assert str(ext_folder_1.resolve()) in indexer.get_linked_folders()

    indexer.add_linked_folder(str(ext_folder_2))
    folders = indexer.get_linked_folders()
    assert len(folders) == 2

    # Remove folder 1
    indexer.remove_linked_folder(str(ext_folder_1))
    folders = indexer.get_linked_folders()
    assert len(folders) == 1
    assert str(ext_folder_2.resolve()) in folders


def test_scan_includes_external_files(tmp_path: Path):
    indexer = AssetIndexer(base_dir=str(tmp_path / "internal_assets"), settings_scope="ResolveFlowTest")

    # Create an external folder
    ext_folder = tmp_path / "ExternalEditorDrive"
    ext_folder.mkdir()
    meme_file = ext_folder / "laughing_cat.mp4"
    meme_file.write_text("video dummy")
    sfx_file = ext_folder / "boom.wav"
    sfx_file.write_text("audio dummy")

    indexer.add_linked_folder(str(ext_folder))

    # Scan memes
    memes = indexer.scan_memes()
    meme_names = [m["name"] for m in memes]
    assert "laughing_cat" in meme_names

    cat_item = next(m for m in memes if m["name"] == "laughing_cat")
    assert cat_item["is_external"] is True
    assert cat_item["path"] == str(meme_file.resolve())

    # Scan SFX
    sfx_list = indexer.scan_sfx()
    sfx_names = [s["name"] for s in sfx_list]
    assert "boom" in sfx_names
    boom_item = next(s for s in sfx_list if s["name"] == "boom")
    assert boom_item["is_external"] is True
    assert boom_item["path"] == str(sfx_file.resolve())
