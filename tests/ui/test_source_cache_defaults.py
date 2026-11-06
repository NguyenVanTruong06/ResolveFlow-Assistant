from unittest.mock import Mock
from src.ui.director.pages import source_page as source
from src.core.cache_manager import ScanCacheManager


def test_source_default_reuses_small_cache_instead_of_gpu_upgrade(tmp_path, monkeypatch, qapp):
    settings = Mock()
    settings.value.side_effect = lambda key, default='': default
    monkeypatch.setattr(source, 'QSettings', lambda *a: settings)
    monkeypatch.setattr(source, 'suggest_default_model', lambda: 'large-v3-turbo')
    page = source.SourcePage()
    try:
        assert page._whisper_model == 'small'
        path = tmp_path / 'clip.mp4'
        path.write_bytes(b'video')
        manager = ScanCacheManager(str(tmp_path / 'cache'))
        manager.save_scan_result(str(path), {'raw_subtitles': [{'start': 0, 'end': 1, 'text': 'test'}], 'clip_dur': 1}, 'small', 'auto')
        assert manager.get_cached_scan(str(path), page._whisper_model, page._whisper_lang)
        config = source.DirectorConfig()
        page.save_to_config(config)
        assert config.whisper_model == 'small'
    finally:
        page.close()


def test_explicit_model_preference_is_preserved(monkeypatch, qapp):
    settings = Mock()
    settings.value.side_effect = lambda key, default='': {'whisper_model': 'medium', 'whisper_lang': 'vi'}.get(key, default)
    monkeypatch.setattr(source, 'QSettings', lambda *a: settings)
    page = source.SourcePage()
    assert page._whisper_model == 'medium'
    page.close()
