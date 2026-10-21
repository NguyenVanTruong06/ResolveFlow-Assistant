"""
Unit tests for high-performance AssetCache and Thumbnail Generator.
"""
import os
import time
import tempfile
import threading
import pytest
from PIL import Image

from src.core.asset_cache import (
    get_file_hash_key,
    get_folder_hash,
    extract_thumbnail,
    request_thumbnail_async,
    scan_and_cache_metadata,
    clear_asset_cache
)


@pytest.fixture(autouse=True)
def clean_cache():
    clear_asset_cache()
    yield
    clear_asset_cache()


def test_get_file_hash_key():
    with tempfile.NamedTemporaryFile("w", suffix=".png", delete=False) as f:
        f.write("mock content")
        path = f.name

    try:
        k1 = get_file_hash_key(path)
        k2 = get_file_hash_key(path)
        assert k1 == k2
        assert len(k1) == 32
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_extract_thumbnail_image():
    # Create test image 400x300
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img = Image.new("RGBA", (400, 300), color=(255, 100, 50, 255))
        img.save(f.name)
        img_path = f.name

    try:
        thumb_path = extract_thumbnail(img_path, target_w=320, target_h=180)
        assert thumb_path is not None
        assert os.path.isfile(thumb_path)
        assert thumb_path.endswith(".jpg")

        # Verify output size
        with Image.open(thumb_path) as out:
            assert out.size == (320, 180)

        # Second call should be instant cache hit
        thumb_path_2 = extract_thumbnail(img_path, target_w=320, target_h=180)
        assert thumb_path_2 == thumb_path
    finally:
        if os.path.exists(img_path):
            os.remove(img_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)


def test_request_thumbnail_async():
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        img = Image.new("RGB", (200, 200), color=(0, 200, 100))
        img.save(f.name)
        img_path = f.name

    thumb_result = []
    done_event = threading.Event()

    def _callback(path):
        thumb_result.append(path)
        done_event.set()

    try:
        request_thumbnail_async(img_path, _callback, target_w=160, target_h=90)
        assert done_event.wait(timeout=5.0)
        assert len(thumb_result) == 1
        assert os.path.isfile(thumb_result[0])
    finally:
        if os.path.exists(img_path):
            os.remove(img_path)
        if thumb_result and os.path.exists(thumb_result[0]):
            os.remove(thumb_result[0])


def test_scan_and_cache_metadata():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a primary .setting referencing an auxiliary bg.png
        setting_file = os.path.join(tmp_dir, "CoolTitle.setting")
        aux_file = os.path.join(tmp_dir, "bg_texture.png")
        stand_alone = os.path.join(tmp_dir, "viral_meme.mp4")

        with open(setting_file, "w") as f:
            f.write('{ Tools = ordered() { Loader { Inputs = { Clip = "bg_texture.png" } } } }')
        with open(aux_file, "w") as f:
            f.write("texture")
        with open(stand_alone, "w") as f:
            f.write("video")

        items = scan_and_cache_metadata(tmp_dir)
        ids = [it["id"] for it in items]

        # CoolTitle and viral_meme should be visible
        assert "cooltitle" in ids
        assert "viral_meme" in ids

        # bg_texture should be hidden because it was referenced inside CoolTitle.setting
        assert "bg_texture" not in ids
