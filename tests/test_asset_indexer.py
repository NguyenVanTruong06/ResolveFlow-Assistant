import os
import pytest
from pathlib import Path
from src.core.asset_indexer import AssetIndexer

@pytest.fixture
def temp_assets_dir(tmp_path):
    # Set up temp directories and files
    base_dir = tmp_path / "assets"
    indexer = AssetIndexer(base_dir=str(base_dir))
    
    # Create some dummy files
    (indexer.sfx_dir / "whoosh.wav").touch()
    (indexer.luts_dir / "cinematic.cube").touch()
    
    # Memes (utf8 filename test)
    (indexer.memes_dir / "việt_nam.mp4").touch()
    
    # Titles with thumbnail
    (indexer.titles_dir / "title1.setting").touch()
    (indexer.titles_dir / "title1.png").touch()
    (indexer.titles_dir / "title2.setting").touch()
    
    # Transitions with thumbnail
    (indexer.transitions_dir / "trans1.setting").touch()
    (indexer.transitions_dir / "trans1.jpg").touch()
    
    return base_dir, indexer

def test_scan_sfx(temp_assets_dir):
    base_dir, indexer = temp_assets_dir
    results = indexer.scan_sfx()
    assert len(results) == 1
    assert results[0]['name'] == 'whoosh'
    assert results[0]['file_path'].endswith('.wav')

def test_scan_luts(temp_assets_dir):
    base_dir, indexer = temp_assets_dir
    results = indexer.scan_luts()
    assert len(results) == 1
    assert results[0]['name'] == 'cinematic'

def test_scan_memes(temp_assets_dir):
    base_dir, indexer = temp_assets_dir
    results = indexer.scan_memes()
    assert len(results) == 1
    assert results[0]['name'] == 'việt_nam'

def test_scan_titles(temp_assets_dir):
    base_dir, indexer = temp_assets_dir
    results = indexer.scan_titles()
    assert len(results) == 2
    
    # Check thumbnails
    title1 = next(r for r in results if r['name'] == 'title1')
    title2 = next(r for r in results if r['name'] == 'title2')
    
    assert title1['thumbnail_path'] is not None
    assert title1['thumbnail_path'].endswith('.png')
    assert title2['thumbnail_path'] is None

def test_scan_transitions(temp_assets_dir):
    base_dir, indexer = temp_assets_dir
    results = indexer.scan_transitions()
    assert len(results) == 1
    assert results[0]['thumbnail_path'] is not None
    assert results[0]['thumbnail_path'].endswith('.jpg')
