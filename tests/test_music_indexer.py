import os
import wave
import struct
import math
import pytest
from pathlib import Path
from src.core.asset_indexer import AssetIndexer

def create_dummy_wav(path: Path, duration_sec: float = 2.0, sample_rate: int = 22050):
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write simple sine wave
        samples = [
            int(32767.0 * 0.3 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
            for i in range(num_samples)
        ]
        wf.writeframes(struct.pack(f"<{num_samples}h", *samples))

def test_scan_music_assets(tmp_path):
    music_dir = tmp_path / "assets" / "music" / "chill_vlog"
    music_dir.mkdir(parents=True)
    sample_file = music_dir / "sunset_chill.mp3"
    sample_file.write_bytes(b"dummy mp3 data")
    
    indexer = AssetIndexer(base_dir=str(tmp_path))
    res = indexer.scan_music_assets()
    
    assert "chill_vlog" in res
    assert len(res["chill_vlog"]) == 1
    item = res["chill_vlog"][0]
    assert item["name"] == "sunset_chill"
    assert item["category"] == "bgm"
    assert item["mood"] == "chill_vlog"
    assert item["duration"] == 30.0  # Fallback duration for dummy mp3
    assert os.path.isabs(item["file_path"])

def test_scan_all_assets_includes_bgm(tmp_path):
    music_dir = tmp_path / "assets" / "music" / "upbeat_trend"
    music_dir.mkdir(parents=True)
    sample_file = music_dir / "groove_hit.wav"
    create_dummy_wav(sample_file, duration_sec=3.5)
    
    indexer = AssetIndexer(base_dir=str(tmp_path))
    all_assets = indexer.scan_all_assets()
    
    assert "bgm" in all_assets
    assert any(item["name"] == "groove_hit" for item in all_assets["bgm"])
    matching = [item for item in all_assets["bgm"] if item["name"] == "groove_hit"][0]
    assert pytest.approx(matching["duration"], 0.1) == 3.5
    assert matching["category"] == "bgm"
    assert matching["mood"] == "upbeat_trend"

def test_scan_music_recognized_extensions(tmp_path):
    cinematic_dir = tmp_path / "assets" / "music" / "cinematic"
    cinematic_dir.mkdir(parents=True)
    (cinematic_dir / "track1.flac").write_bytes(b"flac data")
    (cinematic_dir / "track2.m4a").write_bytes(b"m4a data")
    (cinematic_dir / "track3.aac").write_bytes(b"aac data")
    (cinematic_dir / "ignore_me.txt").write_text("not music")
    
    indexer = AssetIndexer(base_dir=str(tmp_path))
    res = indexer.scan_music_assets()
    
    assert "cinematic" in res
    names = {item["name"] for item in res["cinematic"]}
    assert "track1" in names
    assert "track2" in names
    assert "track3" in names
    assert "ignore_me" not in names
