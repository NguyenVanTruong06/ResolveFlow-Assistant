import io
import os
import shutil
import urllib.request
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.core.storage_manager import StorageManager
from src.core.cloud_downloader import GoogleDriveDownloader, TransferBudget
from src.core.pack_ingest import PackIngestService, MAX_PACK_STREAM_BYTES, MAX_FILES


def create_storage(tmp_path):
    sm = StorageManager(config_dir=tmp_path / 'config', reserve_bytes=0)
    sm.configure(tmp_path / 'storage_root')
    return sm


# ---------------------------------------------------------------------------
# 1. Unit tests for TransferBudget
# ---------------------------------------------------------------------------

def test_transfer_budget_validation():
    # Strict positive int
    budget = TransferBudget(1000)
    assert budget.max_bytes == 1000
    assert budget.total_consumed == 0
    assert budget.remaining == 1000

    # Invalid inputs must raise ValueError without coercion
    for invalid in [0, -10, True, False, "1000", 1000.5, None]:
        with pytest.raises(ValueError):
            TransferBudget(invalid)

    # check_declared
    budget.check_declared(500)
    budget.check_declared(1000)  # exact remaining passes
    with pytest.raises(ValueError, match="vượt quá hạn mức còn lại"):
        budget.check_declared(1001)

    for invalid in [-1, True, False, "500", None]:
        with pytest.raises(ValueError):
            budget.check_declared(invalid)

    # consume
    budget.consume(400)
    assert budget.total_consumed == 400
    assert budget.remaining == 600

    # check_declared against remaining
    budget.check_declared(600)  # passes
    with pytest.raises(ValueError, match="vượt quá hạn mức còn lại"):
        budget.check_declared(601)

    # exact cap consume
    budget.consume(600)
    assert budget.total_consumed == 1000
    assert budget.remaining == 0

    # over cap consume
    with pytest.raises(ValueError, match="vượt quá giới hạn cho phép"):
        budget.consume(1)


# ---------------------------------------------------------------------------
# 2. Single file tests: declared over cap, stream over cap, exact cap
# ---------------------------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, headers=None, status=200):
        super().__init__(data)
        self.headers = headers or {}
        self.status = status
        self.code = status

    def getcode(self):
        return self.status

    def close(self):
        pass


def test_drive_single_file_declared_over_cap_rejected(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    resp = FakeResponse(
        b"x" * 200,
        headers={"Content-Length": "1500", "Content-Type": "application/octet-stream"}
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="vượt quá hạn mức còn lại"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)

    # Ensure no part or target files left
    assert not list(out_dir.glob("*.part"))


def test_drive_single_file_streaming_over_cap_blocked_before_write(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    # Stream sends 1200 bytes without Content-Length
    resp = FakeResponse(
        b"x" * 1200,
        headers={"Content-Type": "application/octet-stream"}
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="vượt quá giới hạn cho phép"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)

    assert not list(out_dir.glob("*.part"))
    assert not list(out_dir.glob("GoogleDrive_file123.mp4"))


def test_drive_single_file_exact_cap_passes(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    payload = b"VALID_BINARY_DATA_" + b"0" * 982  # exactly 1000 bytes
    resp = FakeResponse(
        payload,
        headers={"Content-Length": "1000", "Content-Type": "application/octet-stream"}
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    target = GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)
    assert os.path.exists(target)
    assert os.path.getsize(target) == 1000
    assert budget.total_consumed == 1000


# ---------------------------------------------------------------------------
# 3. HTML Error vs Virus Warning Confirmation
# ---------------------------------------------------------------------------

def test_drive_terminal_error_html_rejected_before_write(tmp_path, monkeypatch):
    budget = TransferBudget(50_000)
    # HTML error page > 30 KiB disguised as application/octet-stream
    html_data = b"<!DOCTYPE html><html><head><title>Too Many Users</title></head><body>Quota exceeded</body></html>" + b" " * 35000
    resp = FakeResponse(
        html_data,
        headers={"Content-Type": "application/octet-stream"}
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="trang web HTML hoặc thông báo lỗi"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)

    assert not list(out_dir.glob("*.part"))
    assert not list(out_dir.glob("*.mp4"))


def test_drive_virus_warning_confirmation_succeeds(tmp_path, monkeypatch):
    budget = TransferBudget(2000)
    confirm_html = b"""
    <html><body>
    <form action="https://drive.usercontent.google.com/download" method="get">
      <input type="hidden" name="id" value="file123">
      <input type="hidden" name="confirm" value="xyz123">
    </form>
    </body></html>
    """
    first_resp = FakeResponse(confirm_html, headers={"Content-Type": "text/html"})
    binary_data = b"BINARY_VIDEO_CONTENT_" + b"1" * 500
    second_resp = FakeResponse(binary_data, headers={"Content-Type": "video/mp4", "Content-Length": str(len(binary_data))})

    responses = [first_resp, second_resp]
    def mock_open(req, *args, **kw):
        return responses.pop(0)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=mock_open))

    out_dir = tmp_path / "out"
    target = GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)
    assert os.path.exists(target)
    assert os.path.getsize(target) == len(binary_data)


def test_drive_oversized_confirmation_html_fails(tmp_path, monkeypatch):
    budget = TransferBudget(2000)
    # HTML confirmation > 1 MiB
    huge_html = b"<html><body>" + b"A" * (1024 * 1024 + 10) + b"</body></html>"
    resp = FakeResponse(huge_html, headers={"Content-Type": "text/html"})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="quá lớn"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)


# ---------------------------------------------------------------------------
# 4. Status code non-200, empty file, length mismatch, no gdown fallback
# ---------------------------------------------------------------------------

def test_drive_non_200_status_fails(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    resp = FakeResponse(b"Error", headers={"Content-Type": "application/octet-stream"}, status=403)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="HTTP status không hợp lệ"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)


def test_drive_empty_file_fails(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    resp = FakeResponse(b"", headers={"Content-Type": "application/octet-stream"})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "out"
    with pytest.raises(ValueError, match="rỗng"):
        GoogleDriveDownloader.download_file("file123", str(out_dir), budget=budget)


def test_drive_pack_fails_closed_no_gdown_fallback_even_with_write_guard_none(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    # Fake error from opener
    def mock_open(*args, **kw):
        raise urllib.error.URLError("Network unreachable")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=mock_open))

    # Mock gdown to detect if it's called
    gdown_called = False
    class MockGdown:
        @staticmethod
        def download(*args, **kw):
            nonlocal gdown_called
            gdown_called = True
            return "target"

    monkeypatch.setattr("sys.modules", dict(**os.sys.modules, gdown=MockGdown))

    out_dir = tmp_path / "out"
    with pytest.raises(Exception):
        GoogleDriveDownloader.download_file("file123", str(out_dir), write_guard=None, budget=budget)

    assert not gdown_called, "gdown fallback must NOT be called in pack mode (budget is not None)!"


# ---------------------------------------------------------------------------
# 5. Folder Aggregate Cumulative Budget & Pre-validation (PackIngestService)
# ---------------------------------------------------------------------------

def test_drive_folder_aggregate_cumulative_budget_enforced(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    monkeypatch.setattr("src.core.pack_ingest.MAX_PACK_STREAM_BYTES", 1000)

    # 3 files: file1 = 400 bytes, file2 = 400 bytes, file3 = 300 bytes (total 1100 > 1000)
    folder_id = "folder123"
    url = f"https://drive.google.com/drive/folders/{folder_id}"

    staging_dir = sm.meta / "staging"
    # Mock gdown.download_folder
    class MockGdownItem:
        def __init__(self, file_id, local_path):
            self.id = file_id
            self.local_path = local_path

    def mock_download_folder(id, output, **kw):
        return [
            MockGdownItem("f1", os.path.join(output, "sub", "a.wav")),
            MockGdownItem("f2", os.path.join(output, "sub", "b.wav")),
            MockGdownItem("f3", os.path.join(output, "sub", "c.wav")),
        ]

    import gdown
    monkeypatch.setattr(gdown, "download_folder", mock_download_folder)

    # Mock opener to serve data for f1, f2, f3
    def mock_open(req, *args, **kw):
        req_url = req.full_url if hasattr(req, "full_url") else str(req)
        if "f1" in req_url:
            return FakeResponse(b"A" * 400, headers={"Content-Type": "audio/wav"})
        elif "f2" in req_url:
            return FakeResponse(b"B" * 400, headers={"Content-Type": "audio/wav"})
        elif "f3" in req_url:
            return FakeResponse(b"C" * 300, headers={"Content-Type": "audio/wav"})
        return FakeResponse(b"X" * 100)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=mock_open))

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match="vượt quá giới hạn cho phép"):
        svc.download(url)

    # Staging must have been cleaned up
    assert list((sm.meta / "staging").iterdir()) == []


def test_drive_folder_prevalidation_path_traversal_rejected(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    folder_id = "folder_traversal"
    url = f"https://drive.google.com/drive/folders/{folder_id}"

    class MockGdownItem:
        def __init__(self, file_id, local_path):
            self.id = file_id
            self.local_path = local_path

    def mock_download_folder(id, output, **kw):
        return [
            MockGdownItem("f1", os.path.join(output, "..", "escape.mp4")),
        ]

    import gdown
    monkeypatch.setattr(gdown, "download_folder", mock_download_folder)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match="ngoài staging|không an toàn"):
        svc.download(url)

    assert list((sm.meta / "staging").iterdir()) == []


def test_drive_folder_prevalidation_casefold_collision_rejected(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    folder_id = "folder_casefold"
    url = f"https://drive.google.com/drive/folders/{folder_id}"

    class MockGdownItem:
        def __init__(self, file_id, local_path):
            self.id = file_id
            self.local_path = local_path

    def mock_download_folder(id, output, **kw):
        return [
            MockGdownItem("f1", os.path.join(output, "Media.mp4")),
            MockGdownItem("f2", os.path.join(output, "media.mp4")),
        ]

    import gdown
    monkeypatch.setattr(gdown, "download_folder", mock_download_folder)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match="trùng tên hoặc xung đột hoa/thường"):
        svc.download(url)


# ---------------------------------------------------------------------------
# 6. Source Footage compatibility: budget=None allows large files
# ---------------------------------------------------------------------------

def test_source_footage_compat_unlimited_no_budget(tmp_path, monkeypatch):
    # When budget=None, downloads exceed fake cap (e.g. 5000 bytes) without error
    large_data = b"SOURCE_FOOTAGE_RAW_PRORES_FRAME_" + b"1" * 4000
    resp = FakeResponse(
        large_data,
        headers={"Content-Length": str(len(large_data)), "Content-Type": "video/mp4"}
    )
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    out_dir = tmp_path / "footage"
    # budget omitted / None
    target = GoogleDriveDownloader.download_file("footage123", str(out_dir))
    assert os.path.exists(target)
    assert os.path.getsize(target) == len(large_data)


# ---------------------------------------------------------------------------
# 7. G03-03A3-R1 Probes & Integrity Tests
# ---------------------------------------------------------------------------

class SpyResponse(FakeResponse):
    def __init__(self, data: bytes, headers=None, status=200):
        super().__init__(data, headers, status)
        self.close_count = 0

    def close(self):
        self.close_count += 1


def test_pack_drive_strict_http_status_variants(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    out_dir = tmp_path / "strict_status"

    # Case A: status missing completely (no status, code, getcode)
    class NoStatusResponse(io.BytesIO):
        headers = {"Content-Type": "audio/wav"}
        def close(self): pass

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: NoStatusResponse(b"AUDIO" * 20)))
    with pytest.raises(ValueError, match="HTTP status không hợp lệ"):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)

    # Case B: status is None
    resp = FakeResponse(b"AUDIO" * 20, headers={"Content-Type": "audio/wav"}, status=None)
    resp.status = None
    resp.code = None
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))
    with pytest.raises(ValueError, match="HTTP status không hợp lệ"):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)

    # Case C: non-int types (True, 200.0, "200")
    for bad_status in [True, 200.0, "200", 206]:
        resp = FakeResponse(b"AUDIO" * 20, headers={"Content-Type": "audio/wav"}, status=bad_status)
        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))
        with pytest.raises(ValueError, match="HTTP status không hợp lệ"):
            GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)

    # Case D: status via getcode() only returning int 200
    class GetcodeResponse(io.BytesIO):
        headers = {"Content-Type": "audio/wav", "Content-Length": "100"}
        def getcode(self): return 200
        def close(self): pass

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: GetcodeResponse(b"A" * 100)))
    target = GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)
    assert os.path.exists(target)


def test_drive_folder_sanitization_collision_rejected_before_transfer(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    url = "https://drive.google.com/drive/folders/folder_sanitization"

    class MockGdownItem:
        def __init__(self, file_id, local_path):
            self.id = file_id
            self.local_path = local_path

    # a?.wav and a*.wav both sanitize to a_.wav
    def mock_download_folder(id, output, **kw):
        return [
            MockGdownItem("f1", os.path.join(output, "a?.wav")),
            MockGdownItem("f2", os.path.join(output, "a*.wav")),
        ]

    import gdown
    monkeypatch.setattr(gdown, "download_folder", mock_download_folder)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match="trùng tên hoặc xung đột hoa/thường"):
        svc.download(url)


def test_drive_folder_ancestor_symlink_inside_staging_rejected_before_transfer(tmp_path, monkeypatch):
    sm = create_storage(tmp_path)
    url = "https://drive.google.com/drive/folders/folder_symlink_ancestor"

    # We will hook mock_download_folder to create a symlink inside staging/download
    class MockGdownItem:
        def __init__(self, file_id, local_path):
            self.id = file_id
            self.local_path = local_path

    def mock_download_folder(id, output, **kw):
        real_dir = os.path.join(output, "real_sub")
        os.makedirs(real_dir, exist_ok=True)
        link_dir = os.path.join(output, "link_sub")
        os.symlink(real_dir, link_dir)
        return [
            MockGdownItem("f1", os.path.join(link_dir, "x.wav")),
        ]

    import gdown
    monkeypatch.setattr(gdown, "download_folder", mock_download_folder)

    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match="symlink"):
        svc.download(url)


def test_drive_pack_mode_preexisting_target_or_part_not_overwritten(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    out_dir = tmp_path / "preexisting"
    os.makedirs(out_dir, exist_ok=True)

    # 1. Preexisting target file
    target_file = out_dir / "GoogleDrive_f1.mp4"
    target_file.write_bytes(b"ORIGINAL_TARGET_BYTES")

    resp = FakeResponse(b"NEW_BYTES", headers={"Content-Type": "audio/wav"})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp))

    with pytest.raises((FileExistsError, ValueError)):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)

    # Original bytes must be preserved
    assert target_file.read_bytes() == b"ORIGINAL_TARGET_BYTES"

    # 2. Preexisting part file
    target_file.unlink()
    part_file = out_dir / "GoogleDrive_f1.mp4.part"
    part_file.write_bytes(b"ORIGINAL_PART_BYTES")

    with pytest.raises((FileExistsError, ValueError)):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)

    assert part_file.read_bytes() == b"ORIGINAL_PART_BYTES"


def test_drive_response_closed_on_all_error_branches(tmp_path, monkeypatch):
    budget = TransferBudget(1000)
    out_dir = tmp_path / "resp_closed"

    # Case A: preflight check_declared raises
    resp_a = SpyResponse(b"X" * 100, headers={"Content-Length": "2000", "Content-Type": "audio/wav"})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp_a))
    with pytest.raises(ValueError):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)
    assert resp_a.close_count >= 1, "Response must be closed on check_declared failure!"

    # Case B: HTTP status error raises
    resp_b = SpyResponse(b"X" * 100, headers={"Content-Type": "audio/wav"}, status=500)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp_b))
    with pytest.raises(ValueError):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)
    assert resp_b.close_count >= 1, "Response must be closed on status error!"

    # Case C: HTML prefix error raises
    html_data = b"<!DOCTYPE html><html><body>Error</body></html>" + b" " * 600
    resp_c = SpyResponse(html_data, headers={"Content-Type": "application/octet-stream"})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *args, **kw: resp_c))
    with pytest.raises(ValueError):
        GoogleDriveDownloader.download_file("f1", str(out_dir), budget=budget)
    assert resp_c.close_count >= 1, "Response must be closed on HTML prefix error!"

