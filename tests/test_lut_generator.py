import os
import pytest
from src.core.lut_generator import LUT3DGenerator, BUILTIN_COLOR_LOOKS

def test_builtin_color_looks():
    assert len(BUILTIN_COLOR_LOOKS) >= 10
    look_ids = [l.id for l in BUILTIN_COLOR_LOOKS]
    assert "clean_rec709" in look_ids
    assert "warm_vlog" in look_ids
    assert "korean_pastel" in look_ids
    assert "cinematic_teal_orange" in look_ids
    assert "cyberpunk_neon" in look_ids
    assert "moody_dark" in look_ids
    assert "golden_hour" in look_ids
    assert "anime_vibrant" in look_ids
    assert "vintage_film_kodak" in look_ids
    assert "high_contrast_bw" in look_ids

def test_generate_cube_data():
    cube_data = LUT3DGenerator.generate_cube_data("cyberpunk_neon", size=17)
    assert "TITLE \"Cyberpunk Neon Nights\"" in cube_data
    assert "LUT_3D_SIZE 17" in cube_data
    lines = cube_data.strip().split("\n")
    # 17^3 = 4913 points + header lines
    assert len(lines) > 4913

def test_ensure_all_luts_exist(tmp_path):
    target_dir = str(tmp_path / "test_luts")
    count = LUT3DGenerator.ensure_all_luts_exist(target_dir)
    assert count >= 10
    files = os.listdir(target_dir)
    assert len(files) >= 10
    assert any("Cyberpunk_Neon.cube" in f for f in files)
    assert any("Korean_Pastel.cube" in f for f in files)
