import io
import stat
import zipfile
from pathlib import Path
import pytest
from src.core.storage_manager import StorageManager
import src.core.pack_ingest as pack_ingest_mod
from src.core.pack_ingest import (
    PackIngestService,
    MAX_FILES,
    MAX_UNCOMPRESSED_PACK_BYTES,
    MAX_MANIFEST_BYTES,
)


def create_storage(tmp_path):
    sm = StorageManager(config_dir=tmp_path / 'config', reserve_bytes=0)
    sm.configure(tmp_path / 'storage_root')
    return sm


def test_constants_defined():
    assert MAX_FILES == 100_000
    assert MAX_UNCOMPRESSED_PACK_BYTES == 20 * 1024 * 1024 * 1024
    assert MAX_MANIFEST_BYTES == 2 * 1024 * 1024


def test_declared_cumulative_over_cap_rejected_in_preflight_before_write(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_UNCOMPRESSED_PACK_BYTES', 1000)

    archive = tmp_path / 'over_cap.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'a' * 600)
        z.writestr('file2.mp4', b'b' * 500)  # total declared = 1100 > 1000

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='vượt giới hạn 20 GiB'):
        svc.prepare(archive)

    # Staging must have been cleaned up on error
    staging_dirs = list((sm.meta / 'staging').iterdir())
    assert len(staging_dirs) == 0


def test_exact_cap_normal_zip_roundtrip_passes(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_UNCOMPRESSED_PACK_BYTES', 1000)

    archive = tmp_path / 'exact_cap.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'a' * 600)
        z.writestr('file2.mp4', b'b' * 400)  # total declared = 1000 == 1000

    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    assert job['pack_id'] is not None
    assert len(job['assets']) == 2


def test_actual_cumulative_over_cap_blocked_midstream_before_violating_write(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_UNCOMPRESSED_PACK_BYTES', 1000)

    archive = tmp_path / 'lying_header.zip'
    # Create real zip with small content so zip structure is valid
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'a' * 400)
        z.writestr('file2.mp4', b'b' * 400)

    # Fake stream that emits more than declared / cap on file2
    orig_open = zipfile.ZipFile.open

    class LyingStream(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)

    def mock_open(self, name, mode='r', **kwargs):
        entry_name = name.filename if hasattr(name, 'filename') else name
        if entry_name == 'file2.mp4':
            # Returns 700 bytes instead of 400, total cumulative becomes 400 + 700 = 1100 > 1000
            return LyingStream(b'x' * 700)
        return orig_open(self, name, mode, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', mock_open)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='vượt giới hạn 20 GiB'):
        svc.prepare(archive)

    # Staging cleaned up
    assert list((sm.meta / 'staging').iterdir()) == []


def test_manifest_declared_over_cap_rejected_in_preflight(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_MANIFEST_BYTES', 500)

    archive = tmp_path / 'big_manifest.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'video')
        z.writestr('pack_manifest.json', b' ' * 501)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='Manifest quá lớn'):
        svc.prepare(archive)


def test_nested_manifest_declared_over_cap_rejected(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_MANIFEST_BYTES', 500)

    archive = tmp_path / 'nested_manifest.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'video')
        z.writestr('nested/folder/manifest.json', b' ' * 501)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='Manifest quá lớn'):
        svc.prepare(archive)


def test_manifest_actual_over_cap_rejected_midstream(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr(pack_ingest_mod, 'MAX_MANIFEST_BYTES', 500)

    archive = tmp_path / 'lying_manifest.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'video')
        z.writestr('nested/pack_manifest.json', b'{}')  # declared size is tiny

    orig_open = zipfile.ZipFile.open

    def mock_open(self, name, mode='r', **kwargs):
        entry_name = name.filename if hasattr(name, 'filename') else name
        if 'manifest.json' in entry_name:
            return io.BytesIO(b'{"key": "' + b'x' * 600 + b'"}')  # 600 > 500
        return orig_open(self, name, mode, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', mock_open)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='Manifest quá lớn'):
        svc.prepare(archive)


def test_high_compression_valid_file_passes_under_absolute_cap(tmp_path):
    sm = create_storage(tmp_path)
    archive = tmp_path / 'high_compression.zip'

    # Create 200,000 bytes of repetitive data (typical for LUT or Fusion .setting with lots of zeros/spaces)
    lut_data = b'0.000000 0.000000 0.000000\n' * 7000  # ~203 KB
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('preset.cube', lut_data)

    compressed_size = archive.stat().st_size
    uncompressed_size = len(lut_data)
    assert uncompressed_size / compressed_size > 50  # Highly compressed

    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    assert len(job['assets']) == 1
    assert job['assets'][0]['category'] == 'luts'


def test_preflight_cancel_interrupts_immediately(tmp_path):
    sm = create_storage(tmp_path)
    archive = tmp_path / 'test.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('a.mp4', b'a' * 100)

    svc = PackIngestService(sm)
    with pytest.raises(InterruptedError, match='Đã hủy giải nén'):
        svc.prepare(archive, cancel=lambda: True)

    assert list((sm.meta / 'staging').iterdir()) == []


def test_midstream_cancel_interrupts_and_cleans_up(tmp_path):
    sm = create_storage(tmp_path)
    archive = tmp_path / 'multi.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'a' * 100)
        z.writestr('file2.mp4', b'b' * 100)

    call_count = 0

    def cancel_fn():
        nonlocal call_count
        call_count += 1
        return call_count > 3  # cancel during extraction

    svc = PackIngestService(sm)
    with pytest.raises(InterruptedError, match='Đã hủy giải nén'):
        svc.prepare(archive, cancel=cancel_fn)

    assert list((sm.meta / 'staging').iterdir()) == []


def test_crc_or_read_error_cleans_up_staging(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    archive = tmp_path / 'crc_corrupt.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'corrupt_me')

    orig_open = zipfile.ZipFile.open

    def mock_open(self, name, mode='r', **kwargs):
        class BrokenStream(io.BytesIO):
            def read(self, *args):
                raise zipfile.BadZipFile('CRC mismatch')
        return BrokenStream(b'xyz')

    monkeypatch.setattr(zipfile.ZipFile, 'open', mock_open)

    svc = PackIngestService(sm)
    with pytest.raises(zipfile.BadZipFile):
        svc.prepare(archive)

    assert list((sm.meta / 'staging').iterdir()) == []


def test_enospc_error_cleans_up_staging(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    archive = tmp_path / 'test.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('file1.mp4', b'test')

    monkeypatch.setattr(sm, 'ensure_space', lambda *a, **kw: (_ for _ in ()).throw(OSError(28, 'No space left on device')))

    svc = PackIngestService(sm)
    with pytest.raises(OSError, match='No space left'):
        svc.prepare(archive)

    assert list((sm.meta / 'staging').iterdir()) == []


def test_existing_security_guards_regression(tmp_path):
    sm = create_storage(tmp_path)
    svc = PackIngestService(sm)

    # 1. Path traversal
    bad_traversal = tmp_path / 'bad_traversal.zip'
    with zipfile.ZipFile(bad_traversal, 'w') as z:
        z.writestr('../escape.mp4', b'x')
    with pytest.raises(ValueError):
        svc.prepare(bad_traversal)

    # 2. Casefold collision
    bad_collision = tmp_path / 'bad_collision.zip'
    with zipfile.ZipFile(bad_collision, 'w') as z:
        z.writestr('Item.mp4', b'x')
        z.writestr('item.mp4', b'y')
    with pytest.raises(ValueError, match='trùng hoặc khác hoa/thường'):
        svc.prepare(bad_collision)

    # 3. Symlink entry
    bad_symlink = tmp_path / 'bad_symlink.zip'
    with zipfile.ZipFile(bad_symlink, 'w') as z:
        info = zipfile.ZipInfo('symlink_entry')
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        z.writestr(info, '/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        svc.prepare(bad_symlink)
