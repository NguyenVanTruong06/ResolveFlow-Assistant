"""
tests/core/test_pack_http_download.py
Kiểm thử bộ bảo vệ tải HTTP trực tiếp cho gói tài nguyên (G03-03A1-I):
- Giới hạn tải gói (MAX_PACK_STREAM_BYTES) áp dụng cho cả known và unknown Content-Length.
- Chặn tải vượt trần (chặn trước khi ghi block vi phạm).
- Chấp nhận tải bằng đúng trần (equality).
- Từ chối phản hồi rỗng (zero bytes) và tải thiếu (truncated).
- Chỉ chấp nhận HTTP Status 200 (từ chối 204, 206, 3xx).
- Xử lý sạch sẽ urllib.error.HTTPError và dọn dẹp staging.
- Chặn MIME text/html, application/xhtml+xml.
- Chặn nội dung HTML ẩn trong application/octet-stream (phát hiện qua 512 bytes đầu tiên).
- Cho phép tệp văn bản đơn lẻ hợp lệ (.cube, .setting text/plain).
- Kiểm tra cancel giữa stream, ENOSPC, và kho offline trước tải.
- Cô lập hoàn toàn: chỉ dọn staging thuộc sở hữu của job, không đụng repo/assets hay /tmp.
"""

import errno
import io
import os
import shutil
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.core.storage_manager import StorageManager, StorageOfflineError
from src.core.pack_ingest import PackIngestService
import src.core.pack_ingest as pack_ingest_mod


# ---------------------------------------------------------------------------
# Fixture & Fake Response Helper
# ---------------------------------------------------------------------------

@pytest.fixture
def http_storage(tmp_path):
    """Khởi tạo StorageManager hợp lệ trong tmp_path."""
    config_dir = tmp_path / "config"
    library_root = tmp_path / "library"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=1024**2)
    sm.configure(library_root)
    return sm, library_root


class FakeHTTPResponse:
    """Giả lập phản hồi urllib có kiểm soát chunk và context manager."""
    def __init__(
        self,
        data: bytes = b"",
        status: int = 200,
        headers: dict | None = None,
        chunk_size: int = 1024 * 1024,
    ):
        self.data = data
        self.status = status
        self.code = status
        self.headers = headers or {}
        self.chunk_size = chunk_size
        self._pos = 0

    def getcode(self):
        return self.status

    def read(self, amt=None):
        if self._pos >= len(self.data):
            return b""
        if amt is None:
            amt = len(self.data) - self._pos
        size = min(amt, self.chunk_size, len(self.data) - self._pos)
        chunk = self.data[self._pos:self._pos + size]
        self._pos += size
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def make_dummy_zip_bytes(files: dict[str, bytes]) -> bytes:
    """Tạo chuỗi bytes của tệp ZIP đơn giản."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Valid Download (ZIP & Single File)
# ---------------------------------------------------------------------------

def test_valid_zip_download_into_staging_uncommitted(http_storage):
    """Tải file ZIP hợp lệ: nạp vào staging payload, chưa ghi vào registry.json."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    zip_bytes = make_dummy_zip_bytes({
        "sfx/sound1.wav": b"RIFF....WAVEfmt ....data....",
        "pack_manifest.json": b'{"schema_version": 1, "pack_id": "p1", "assets": []}',
    })

    fake_resp = FakeHTTPResponse(
        data=zip_bytes,
        status=200,
        headers={"Content-Length": str(len(zip_bytes)), "Content-Type": "application/zip"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        job = svc.download("https://example.com/test_pack.zip")

    assert job["name"] == "test_pack"
    assert len(job["assets"]) >= 1
    # Chưa commit vào registry
    assert sm.registry()["packs"] == []
    # Thư mục staging chứa payload
    job_dir = Path(job["job_dir"])
    assert job_dir.is_dir()
    assert (job_dir / "payload").is_dir()


def test_valid_single_file_download_cube_setting(http_storage):
    """Tải tệp đơn lẻ text/plain (.cube hoặc .setting) được chấp nhận và không bị coi là HTML."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    cube_bytes = b"LUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 1.0 1.0\n"
    fake_resp = FakeHTTPResponse(
        data=cube_bytes,
        status=200,
        headers={"Content-Length": str(len(cube_bytes)), "Content-Type": "text/plain"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        job = svc.download("https://example.com/Cinematic_Teal.cube")

    assert job["name"] == "Cinematic_Teal"
    assert len(job["assets"]) == 1
    assert job["assets"][0]["category"] == "luts"


# ---------------------------------------------------------------------------
# 2. Bounds & Cap Enforcement (Known & Unknown Length)
# ---------------------------------------------------------------------------

def test_declared_content_length_exceeds_cap_rejected_before_payload(http_storage):
    """Header Content-Length vượt quá MAX_PACK_STREAM_BYTES phải bị từ chối ngay trước khi tải khối dữ liệu."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"dummy",
        status=200,
        headers={"Content-Length": "1000", "Content-Type": "application/zip"},
    )

    with patch.object(pack_ingest_mod, "MAX_PACK_STREAM_BYTES", 500), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            svc.download("https://example.com/large.zip")

    # Staging phải được dọn dẹp sạch sẽ
    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_unknown_content_length_chunks_exceeding_cap_rejected_before_write(http_storage):
    """Không có Content-Length, streaming vượt quá MAX_PACK_STREAM_BYTES phải bị chặn trước khi ghi block vi phạm."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    # 4 blocks x 200 bytes = 800 bytes; cap là 500 bytes -> bị chặn tại block 3 (sau khi ghi 400 bytes)
    big_data = b"X" * 800
    fake_resp = FakeHTTPResponse(
        data=big_data,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=200,
    )

    with patch.object(pack_ingest_mod, "MAX_PACK_STREAM_BYTES", 500), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            svc.download("https://example.com/stream.zip")

    # Staging phải được dọn dẹp sạch sẽ
    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_misleading_small_content_length_exceeding_cap(http_storage):
    """Header khai báo nhỏ hơn thực tế (vd khai báo 200 bytes nhưng stream 800 bytes) vẫn bị chặn bởi cap."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"X" * 800,
        status=200,
        headers={"Content-Length": "200", "Content-Type": "application/octet-stream"},
        chunk_size=200,
    )

    with patch.object(pack_ingest_mod, "MAX_PACK_STREAM_BYTES", 500), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            svc.download("https://example.com/cheat.zip")


def test_exact_cap_download_passes(http_storage):
    """Dung lượng tải về bằng đúng trần MAX_PACK_STREAM_BYTES (equality) được phép vượt qua."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    exact_data = b"X" * 500
    fake_resp = FakeHTTPResponse(
        data=exact_data,
        status=200,
        headers={"Content-Length": "500", "Content-Type": "application/octet-stream"},
        chunk_size=100,
    )

    with patch.object(pack_ingest_mod, "MAX_PACK_STREAM_BYTES", 500), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        job = svc.download("https://example.com/exact.dat")

    assert job["name"] == "exact"
    assert len(job["assets"]) == 1
    assert job["assets"][0]["size_bytes"] == 500


# ---------------------------------------------------------------------------
# 3. Truncated & Empty Response Protection
# ---------------------------------------------------------------------------

def test_zero_bytes_empty_response_rejected(http_storage):
    """Tải về 0 bytes phải bị từ chối và dọn staging, không được phân loại inspect."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"",
        status=200,
        headers={"Content-Length": "0", "Content-Type": "application/zip"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="0 bytes|rỗng"):
            svc.download("https://example.com/empty.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_early_truncated_response_rejected(http_storage):
    """Header Content-Length là 1000 nhưng dữ liệu chỉ gửi 300 bytes phải bị từ chối là truncated."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"X" * 300,
        status=200,
        headers={"Content-Length": "1000", "Content-Type": "application/zip"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="chưa đủ dữ liệu|không đầy đủ"):
            svc.download("https://example.com/truncated.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


# ---------------------------------------------------------------------------
# 4. HTTP Status & Network Error Handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", [204, 206, 301, 302, 400, 403, 404, 500])
def test_http_status_non_200_rejected(http_storage, bad_status):
    """Chỉ chấp nhận HTTP Status 200; các status 204, 206, 3xx, 4xx, 5xx phải bị từ chối và dọn dẹp."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"dummy",
        status=bad_status,
        headers={"Content-Type": "application/zip"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="status|HTTP"):
            svc.download("https://example.com/test.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_urllib_http_error_cleaned_up(http_storage):
    """Khi urllib ném HTTPError (vd 404 Not Found), ném lỗi rõ ràng và dọn dẹp staging."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    http_err = urllib.error.HTTPError("https://example.com/notfound.zip", 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises((urllib.error.HTTPError, ValueError)):
            svc.download("https://example.com/notfound.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


# ---------------------------------------------------------------------------
# 5. HTML Content & Quota Page Rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("html_mime", ["text/html", "text/html; charset=utf-8", "application/xhtml+xml"])
def test_html_content_type_rejected(http_storage, html_mime):
    """Content-Type dạng text/html hoặc application/xhtml+xml phải bị từ chối ngay lập tức."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"<!DOCTYPE html><html><body>Error</body></html>",
        status=200,
        headers={"Content-Type": html_mime},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="HTML|trang web"):
            svc.download("https://example.com/pack.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


@pytest.mark.parametrize("html_body", [
    b"<!DOCTYPE html><html><head><title>Login</title></head></html>",
    b"  \r\n\t <HTML><BODY>Google Drive - Quota exceeded</BODY></HTML>",
    b"\xef\xbb\xbf<!doctype html><html>Too many users</html>",
    b"<html><title>Access Denied</title><body>Please login</body></html>",
])
def test_html_body_in_octet_stream_rejected(http_storage, html_body):
    """MIME là application/octet-stream nhưng 512 bytes đầu tiên chứa mã HTML hoặc Quota Exceeded phải bị chặn."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=html_body,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="HTML|trang web"):
            svc.download("https://example.com/pack.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


# ---------------------------------------------------------------------------
# 6. Cancellation, Storage ENOSPC & Offline Pre-check
# ---------------------------------------------------------------------------

def test_cancel_during_stream_aborts_and_cleans_staging(http_storage):
    """Khi cờ cancel kích hoạt giữa chừng quá trình tải stream, dừng ngay và dọn dẹp staging."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    cancel_flag = False
    read_calls = 0

    def cancel_hook():
        return cancel_flag

    def fake_progress(text):
        nonlocal cancel_flag, read_calls
        read_calls += 1
        if read_calls >= 2:
            cancel_flag = True

    fake_resp = FakeHTTPResponse(
        data=b"X" * 1024 * 1024 * 5,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=1024 * 1024,
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(InterruptedError, match="hủy"):
            svc.download("https://example.com/pack.zip", cancel=cancel_hook, progress=fake_progress)

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_enospc_during_stream_aborts_and_cleans_staging(http_storage):
    """Khi StorageManager.ensure_space ném ENOSPC trong lúc stream, dừng ngay và dọn dẹp staging."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"X" * 1024 * 1024 * 5,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=1024 * 1024,
    )

    def fake_ensure_space(size=0):
        if size > 1024:
            raise OSError(errno.ENOSPC, "No space left on device")

    with patch.object(sm, "ensure_space", side_effect=fake_ensure_space), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(OSError) as excinfo:
            svc.download("https://example.com/pack.zip")
        assert excinfo.value.errno == errno.ENOSPC

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_root_offline_before_download_no_network_no_fallback(http_storage):
    """Nếu kho bị offline trước khi tải, require_online chặn ngay; không gọi urlopen, không fallback vào /tmp."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    # Giả lập kho bị offline (xóa marker library.json)
    (sm.meta / "library.json").unlink()

    with patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(StorageOfflineError):
            svc.download("https://example.com/pack.zip")

        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Prefix Short-read Chunking & Fragmented HTML Detection (G03-03A1-R1)
# ---------------------------------------------------------------------------

def test_one_byte_html_chunks_rejected(http_storage):
    """Khi server trả về từng byte một (short read), HTML body vẫn phải bị phát hiện và từ chối, không được inspect/register."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    html_content = b"<html><body>Quota exceeded</body></html>"
    fake_resp = FakeHTTPResponse(
        data=html_content,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=1,  # Trả về đúng 1 byte mỗi lần read(n)
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="HTML|trang web"):
            svc.download("https://example.com/preview.mp4")

    # Staging phải được dọn dẹp sạch sẽ
    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())
    assert sm.registry()["packs"] == []


def test_fragmented_bom_whitespace_and_doctype_rejected(http_storage):
    """BOM UTF-8, khoảng trắng và <!DOCTYPE html> bị phân mảnh qua nhiều chunk nhỏ (vd 2-4 bytes) vẫn phải bị bắt."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fragmented_data = b"\xef\xbb\xbf   \r\n\t  <!DOCTYPE html><html><body>Error</body></html>"
    fake_resp = FakeHTTPResponse(
        data=fragmented_data,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=3,  # Mỗi lần đọc 3 bytes
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="HTML|trang web"):
            svc.download("https://example.com/pack.zip")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_short_valid_text_eof_passes(http_storage):
    """Tệp văn bản hợp lệ ngắn hơn 512 bytes (.cube/.setting) chạm EOF trước 512 bytes vẫn được ghi đủ và inspect thành công."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    cube_text = b"LUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 1.0 1.0\n# Short valid cube < 512 bytes"
    fake_resp = FakeHTTPResponse(
        data=cube_text,
        status=200,
        headers={"Content-Type": "text/plain"},
        chunk_size=5,  # 5 bytes mỗi lần
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        job = svc.download("https://example.com/mini.cube")

    assert job["name"] == "mini"
    assert len(job["assets"]) == 1
    assert job["assets"][0]["size_bytes"] == len(cube_text)
    # File được ghi đúng nội dung và kích thước
    payload_file = Path(job["job_dir"]) / "payload" / "mini.cube"
    assert payload_file.read_bytes() == cube_text


def test_binary_long_short_reads_roundtrip(http_storage):
    """Tệp nhị phân dài được stream bằng các chunk nhỏ (short read 7 bytes) phải được ghi chính xác 100% không mất/trùng byte."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    # 1024 bytes binary không có HTML markers
    raw_binary = bytes(range(256)) * 4
    fake_resp = FakeHTTPResponse(
        data=raw_binary,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=7,  # 7 bytes per read
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        job = svc.download("https://example.com/binary.dat")

    payload_file = Path(job["job_dir"]) / "payload" / "binary.dat"
    assert payload_file.read_bytes() == raw_binary


def test_cap_during_prefix_buffering(http_storage):
    """Nếu cap bị vượt ngay trong giai đoạn gom 512 bytes prefix, phải dừng ngay và không ghi file vi phạm."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    fake_resp = FakeHTTPResponse(
        data=b"A" * 300,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=10,
    )

    with patch.object(pack_ingest_mod, "MAX_PACK_STREAM_BYTES", 50), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(ValueError, match="vượt quá giới hạn"):
            svc.download("https://example.com/cap_prefix.dat")

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


def test_cancel_during_prefix_buffering(http_storage):
    """Nếu cancel kích hoạt khi đang gom prefix buffer, phải dừng ngay với InterruptedError."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    calls = 0
    def cancel_hook():
        nonlocal calls
        calls += 1
        return calls >= 5

    fake_resp = FakeHTTPResponse(
        data=b"B" * 300,
        status=200,
        headers={"Content-Type": "application/octet-stream"},
        chunk_size=10,
    )

    with patch("urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(InterruptedError, match="hủy"):
            svc.download("https://example.com/cancel_prefix.dat", cancel=cancel_hook)

    staging_dir = sm.meta / "staging"
    assert not any(staging_dir.iterdir())


# ---------------------------------------------------------------------------
# 8. Strict HTTP Status & No Mock Bypass (G03-03A1-R1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_val", [None, "200", True, False, 200.0])
def test_strict_http_status_missing_or_wrong_type_rejected(http_storage, bad_val):
    """Status thiếu, None hoặc sai kiểu (str '200', bool True, float) phải bị từ chối bằng ValueError."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    class BadStatusResponse(io.BytesIO):
        status = bad_val
        headers = {"Content-Type": "application/octet-stream"}

    with patch("urllib.request.urlopen", return_value=BadStatusResponse(b"valid_data")):
        with pytest.raises(ValueError, match="HTTP status"):
            svc.download("https://example.com/bad_status.dat")


def test_strict_http_status_getcode_only_accepted(http_storage):
    """Đối tượng response chỉ có getcode() trả về int 200 (không có thuộc tính status) vẫn được chấp nhận hợp lệ."""
    sm, root = http_storage
    svc = PackIngestService(sm)

    class GetCodeOnlyResponse(io.BytesIO):
        headers = {"Content-Type": "application/octet-stream"}
        def getcode(self):
            return 200

    with patch("urllib.request.urlopen", return_value=GetCodeOnlyResponse(b"valid_data")):
        job = svc.download("https://example.com/getcode.dat")

    assert job["name"] == "getcode"

