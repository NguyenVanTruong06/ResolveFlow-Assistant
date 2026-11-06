"""
Kiểm thử cho cảnh báo phát hiện gói trùng lặp (Duplicate Name & Version Warning):
- inspect_job cảnh báo khi tên và phiên bản trùng với gói đã có trong kho
- Khác phiên bản hoặc khác tên không cảnh báo
- Lỗi đọc registry sinh cảnh báo 'Chưa kiểm tra được gói trùng...'
- Commit hai gói trùng tên/version tạo 2 bản sao độc lập (side-by-side) với 2 pack_id riêng
"""

from pathlib import Path
import pytest

from src.core.storage_manager import StorageManager
from src.core.pack_ingest import PackIngestService


def test_inspect_job_warns_on_duplicate_name_and_version(tmp_path):
    """inspect_job phát hiện gói trùng (name, version) trong registry và thêm cảnh báo."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    # Giả lập registry đã có 1 pack
    reg = storage.registry()
    reg["packs"].append({
        "id": "existing_pack_01",
        "name": "Cinematic LUTs",
        "version": "2.0",
        "folder": "02_cloud_downloads/packs/Cinematic_LUTs_2.0_existing_pack_01"
    })
    from src.core.storage_manager import atomic_json
    atomic_json(storage.meta / "registry.json", reg)

    # Tạo staging pack để inspect
    service = PackIngestService(storage)
    staging = storage.new_staging()
    payload = staging / "payload"
    payload.mkdir()
    (payload / "cube.cube").write_text("LUT")

    manifest = {
        "schema_version": 1,
        "name": " Cinematic LUTs ",  # có khoảng trắng để test strip
        "version": " 2.0 ",
        "assets": [{"relative_path": "cube.cube", "category": "luts"}]
    }
    atomic_json(payload / "pack_manifest.json", manifest)

    result = service.inspect_job(staging)
    warnings = result.get("warnings", [])

    expected = "Kho đã có gói 'Cinematic LUTs' phiên bản '2.0'; tiếp tục nhập sẽ tạo bản sao độc lập song song."
    assert any(expected in w for w in warnings), f"Cảnh báo gói trùng không tìm thấy trong: {warnings}"


def test_inspect_job_no_warning_when_different_version_or_name(tmp_path):
    """inspect_job không cảnh báo khi khác phiên bản hoặc khác tên."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    reg = storage.registry()
    reg["packs"].append({
        "id": "p1",
        "name": "Audio Pack",
        "version": "1.0",
        "folder": "packs/Audio_Pack_1.0_p1"
    })
    from src.core.storage_manager import atomic_json
    atomic_json(storage.meta / "registry.json", reg)

    service = PackIngestService(storage)

    # 1. Cùng tên, khác version (1.1 thay vì 1.0)
    staging1 = storage.new_staging()
    payload1 = staging1 / "payload"
    payload1.mkdir()
    (payload1 / "hit.wav").write_bytes(b"wav")
    atomic_json(payload1 / "pack_manifest.json", {
        "name": "Audio Pack",
        "version": "1.1",
        "assets": [{"relative_path": "hit.wav", "category": "sfx"}]
    })
    res1 = service.inspect_job(staging1)
    assert not any("tiếp tục nhập sẽ tạo bản sao độc lập" in w for w in res1.get("warnings", []))

    # 2. Khác tên, cùng version (1.0)
    staging2 = storage.new_staging()
    payload2 = staging2 / "payload"
    payload2.mkdir()
    (payload2 / "hit2.wav").write_bytes(b"wav")
    atomic_json(payload2 / "pack_manifest.json", {
        "name": "Audio Pack Pro",
        "version": "1.0",
        "assets": [{"relative_path": "hit2.wav", "category": "sfx"}]
    })
    res2 = service.inspect_job(staging2)
    assert not any("tiếp tục nhập sẽ tạo bản sao độc lập" in w for w in res2.get("warnings", []))


def test_inspect_job_preserves_other_warnings_alongside_duplicate(tmp_path):
    """Cảnh báo trùng lặp không ghi đè hoặc làm mất các cảnh báo manifest khác."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    reg = storage.registry()
    reg["packs"].append({
        "id": "p1",
        "name": "Mixed Pack",
        "version": "1.0",
        "folder": "packs/Mixed_1.0_p1"
    })
    from src.core.storage_manager import atomic_json
    atomic_json(storage.meta / "registry.json", reg)

    service = PackIngestService(storage)
    staging = storage.new_staging()
    payload = staging / "payload"
    payload.mkdir()
    (payload / "sample.mp4").write_bytes(b"mp4")

    # Manifest có danh mục lạ 'unknown_cat' và trùng (name, version)
    atomic_json(payload / "pack_manifest.json", {
        "name": "Mixed Pack",
        "version": "1.0",
        "assets": [{"relative_path": "sample.mp4", "category": "unknown_cat"}]
    })

    result = service.inspect_job(staging)
    warnings = result.get("warnings", [])

    assert any("tiếp tục nhập sẽ tạo bản sao độc lập" in w for w in warnings)
    assert any("chưa đăng ký 'unknown_cat'" in w for w in warnings)


def test_inspect_job_warns_when_registry_read_fails(tmp_path, monkeypatch):
    """Khi đọc registry gặp lỗi ngoại lệ, inspect_job thông báo chưa kiểm tra được gói trùng."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    service = PackIngestService(storage)
    staging = storage.new_staging()
    payload = staging / "payload"
    payload.mkdir()
    (payload / "sample.mp4").write_bytes(b"mp4")
    from src.core.storage_manager import atomic_json
    atomic_json(payload / "pack_manifest.json", {
        "name": "Test Pack",
        "version": "1.0",
        "assets": [{"relative_path": "sample.mp4", "category": "broll"}]
    })

    def broken_registry():
        raise RuntimeError("Lỗi đĩa cứng không đọc được registry")

    monkeypatch.setattr(storage, "registry", broken_registry)

    result = service.inspect_job(staging)
    warnings = result.get("warnings", [])

    assert any("Chưa kiểm tra được gói trùng" in w for w in warnings)


def test_commit_duplicate_name_version_creates_independent_side_by_side_copy(tmp_path):
    """Commit hai gói trùng name và version tạo 2 bản sao song song với 2 pack_id riêng."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")
    service = PackIngestService(storage)
    from src.core.storage_manager import atomic_json

    # 1. Nhập gói 1
    staging1 = storage.new_staging()
    payload1 = staging1 / "payload"
    payload1.mkdir()
    (payload1 / "sfx1.wav").write_bytes(b"sound_one")
    atomic_json(payload1 / "pack_manifest.json", {
        "name": "Duplicate Pack",
        "version": "1.0",
        "assets": [{"relative_path": "sfx1.wav", "category": "sfx"}]
    })
    job1 = service.inspect_job(staging1)
    res1 = service.commit(job1)

    # Kiểm tra gói 1 đã vào registry
    reg1 = storage.registry()
    assert len(reg1["packs"]) == 1
    pack1_meta = reg1["packs"][0]
    pack1_file = storage.root / pack1_meta["folder"] / "sfx1.wav"
    assert pack1_file.read_bytes() == b"sound_one"

    # 2. Nhập gói 2 cùng name và version
    staging2 = storage.new_staging()
    payload2 = staging2 / "payload"
    payload2.mkdir()
    (payload2 / "sfx1.wav").write_bytes(b"sound_two_modified")
    atomic_json(payload2 / "pack_manifest.json", {
        "name": "Duplicate Pack",
        "version": "1.0",
        "assets": [{"relative_path": "sfx1.wav", "category": "sfx"}]
    })
    job2 = service.inspect_job(staging2)
    # Xác nhận inspect_job cảnh báo
    assert any("tiếp tục nhập sẽ tạo bản sao độc lập song song" in w for w in job2["warnings"])

    # Thực hiện commit gói 2
    res2 = service.commit(job2)

    # 3. Xác minh tính độc lập: 2 packs trong registry, 2 IDs khác nhau
    reg2 = storage.registry()
    assert len(reg2["packs"]) == 2
    p1 = reg2["packs"][0]
    p2 = reg2["packs"][1]
    assert p1["id"] != p2["id"]
    assert p1["folder"] != p2["folder"]

    # File của gói 1 vẫn nguyên vẹn không bị đè
    assert pack1_file.read_bytes() == b"sound_one"
    # File của gói 2 được ghi tại thư mục riêng
    pack2_file = storage.root / p2["folder"] / "sfx1.wav"
    assert pack2_file.read_bytes() == b"sound_two_modified"
