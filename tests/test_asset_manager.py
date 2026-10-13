import os
from pathlib import Path
from src.core.asset_manager import AssetManager

def test_fusion_template_dir_linux():
    titles_dir = AssetManager.get_fusion_template_dir("Titles")
    assert isinstance(titles_dir, Path)
    assert "ResolveFlow" in str(titles_dir)
    assert "Titles" in str(titles_dir)

def test_fusion_template_dir_transitions():
    trans_dir = AssetManager.get_fusion_template_dir("Transitions")
    assert isinstance(trans_dir, Path)
    assert "ResolveFlow" in str(trans_dir)
    assert "Transitions" in str(trans_dir)

def test_lut_install_dir_linux():
    lut_dir = AssetManager.get_lut_install_dir()
    assert isinstance(lut_dir, Path)
    assert "ResolveFlow" in str(lut_dir)
    # Target path must be writable or fall back to user directory
    assert os.access(lut_dir.parent, os.W_OK) or os.access(lut_dir, os.W_OK)
