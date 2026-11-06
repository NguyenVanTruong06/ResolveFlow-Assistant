import hashlib
import io

import pytest

from src.core.cloud_item_cache import CloudItemCache


class _Resp(io.BytesIO):
    headers = {}


def _catalog(payload: bytes, **over):
    item = {
        "id": "whoosh_01", "category": "sfx", "url": "https://cdn.example/sfx/whoosh_01.wav",
        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
    }
    item.update(over)
    return {"version": 1, "items": [item]}


def test_fetch_downloads_once_and_caches(tmp_path):
    payload = b"RIFF" + b"x" * 1000
    calls = []

    def opener(url):
        calls.append(url)
        return _Resp(payload)

    cache = CloudItemCache(cache_root=tmp_path, opener=opener)
    cache.load_catalog(_catalog(payload))
    p1 = cache.fetch("whoosh_01")
    p2 = cache.fetch("whoosh_01")
    assert p1 == p2 == tmp_path / "sfx" / "whoosh_01.wav"
    assert p1.read_bytes() == payload
    assert len(calls) == 1


def test_bad_hash_rejected_and_no_partial_left(tmp_path):
    cache = CloudItemCache(cache_root=tmp_path, opener=lambda u: _Resp(b"evil"))
    cache.load_catalog(_catalog(b"good", size=0))
    with pytest.raises(ValueError):
        cache.fetch("whoosh_01")
    assert not any(f.is_file() for f in tmp_path.rglob("*"))


def test_unsafe_ids_are_sanitized(tmp_path):
    payload = b"data"
    cache = CloudItemCache(cache_root=tmp_path, opener=lambda u: _Resp(payload))
    cache.load_catalog(_catalog(payload, id="../../etc/passwd", category="../x"))
    p = cache.fetch("../../etc/passwd")
    assert tmp_path in p.parents


def test_size_limit(tmp_path):
    cache = CloudItemCache(cache_root=tmp_path, max_item_bytes=10, opener=lambda u: _Resp(b"y" * 100))
    cache.load_catalog(_catalog(b"y" * 100, sha256="", size=0))
    with pytest.raises(ValueError):
        cache.fetch("whoosh_01")
