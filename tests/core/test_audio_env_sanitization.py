import os
import sys
import subprocess
import wave
import struct
import pytest
from src.utils.subprocess_utils import clean_media_subprocess_env
from src.core.audio import AudioExtractor
from src.core.ffmpeg_utils import run_ffmpeg, run_ffprobe, get_ffmpeg_path


def test_clean_media_subprocess_env_orig_precedence():
    """
    Theo ngữ nghĩa PyInstaller: Khi ORIG tồn tại, phải khôi phục giá trị ORIG rồi làm sạch;
    TUYỆT ĐỐI KHÔNG gộp các đường dẫn nạp từ LD_LIBRARY_PATH hiện tại.
    """
    env = {
        "LD_LIBRARY_PATH_ORIG": "/custom/valid/lib:/opt/resolve/libs",
        "LD_LIBRARY_PATH": "/injected/pyinstaller/bundle/lib:/another/injected/lib",
        "PATH": "/usr/bin:/bin",
    }
    cleaned = clean_media_subprocess_env(env)

    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert "LD_LIBRARY_PATH" in cleaned
    ld_parts = cleaned["LD_LIBRARY_PATH"].split(os.pathsep)
    assert ld_parts == ["/custom/valid/lib"]
    # Các đường dẫn injected hiện tại không được xuất hiện
    assert "/injected/pyinstaller/bundle/lib" not in ld_parts
    assert "/another/injected/lib" not in ld_parts
    assert "/opt/resolve/libs" not in ld_parts


def test_clean_media_subprocess_env_empty_orig():
    """
    Khi LD_LIBRARY_PATH_ORIG rỗng (môi trường gốc không có LD_LIBRARY_PATH),
    không được dùng fallback sang LD_LIBRARY_PATH bị tiêm, loại bỏ hoàn toàn khóa LD_LIBRARY_PATH.
    """
    env = {
        "LD_LIBRARY_PATH_ORIG": "",
        "LD_LIBRARY_PATH": "/injected/pyinstaller/bundle/lib",
        "PATH": "/usr/bin:/bin",
    }
    cleaned = clean_media_subprocess_env(env)
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert "LD_LIBRARY_PATH" not in cleaned
    assert cleaned["PATH"] == "/usr/bin:/bin"


def test_clean_media_subprocess_env_arbitrary_meipass_root(monkeypatch):
    """
    Kiểm tra ranh giới trích xuất tạm PyInstaller sys._MEIPASS tùy ý được nhận diện và lọc chính xác.
    """
    arbitrary_root = "/tmp/arbitrary_frozen_extraction_root_12345"
    monkeypatch.setattr(sys, "_MEIPASS", arbitrary_root, raising=False)

    env = {
        "LD_LIBRARY_PATH": f"{arbitrary_root}/lib:{arbitrary_root}:/usr/local/lib",
    }
    cleaned = clean_media_subprocess_env(env)
    assert cleaned.get("LD_LIBRARY_PATH") == "/usr/local/lib"


def test_clean_media_subprocess_env_valid_lookalikes():
    """
    Đảm bảo không bị substring overfilter trên các thư mục hợp lệ của người dùng
    có chứa chuỗi con giống marker (như _mei_lookalike, internal_project, resolve_lookalike).
    """
    lookalikes = [
        "/home/user/_mei_lookalike/lib",
        "/custom/valid/internal_project/lib",
        "/opt/resolve_lookalike/lib",
    ]
    env = {
        "LD_LIBRARY_PATH": os.pathsep.join(lookalikes),
        "PATH": "/usr/bin:/bin",
    }
    cleaned = clean_media_subprocess_env(env)
    assert "LD_LIBRARY_PATH" in cleaned
    ld_parts = cleaned["LD_LIBRARY_PATH"].split(os.pathsep)
    for lookalike in lookalikes:
        assert lookalike in ld_parts


def test_clean_media_subprocess_env_excludes_actual_resolve_libs():
    """
    Thư viện ô nhiễm thực tế của DaVinci Resolve (/opt/resolve/libs, Developer, fusionscript)
    bắt buộc phải bị loại trừ hoàn toàn.
    """
    env = {
        "LD_LIBRARY_PATH": "/opt/resolve/libs:/opt/resolve/Developer:/usr/lib:/usr/local/lib",
    }
    cleaned = clean_media_subprocess_env(env)
    ld_parts = cleaned.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    assert "/opt/resolve/libs" not in ld_parts
    assert "/opt/resolve/Developer" not in ld_parts
    assert "/usr/lib" in ld_parts
    assert "/usr/local/lib" in ld_parts


def test_clean_media_subprocess_env_does_not_mutate_parent_os_environ(monkeypatch):
    """Đảm bảo không làm biến đổi os.environ của tiến trình cha."""
    orig_env = dict(os.environ)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/resolve/libs")
    _ = clean_media_subprocess_env()
    assert os.environ.get("LD_LIBRARY_PATH") == "/opt/resolve/libs"


@pytest.mark.skipif(not os.path.exists("/opt/resolve/libs"), reason="Cần môi trường có /opt/resolve/libs")
def test_reproduce_contaminated_vs_clean_ffmpeg_exit():
    """
    Tái hiện độc lập:
    - Chạy ffmpeg với LD_LIBRARY_PATH=/opt/resolve/libs gây lỗi exit code 127 (av_bessel_i0).
    - Chạy ffmpeg qua clean_media_subprocess_env thành công với exit code 0.
    """
    ffmpeg_bin = get_ffmpeg_path() or "ffmpeg"

    # 1. Ô nhiễm cố tình
    contaminated_env = dict(os.environ)
    contaminated_env["LD_LIBRARY_PATH"] = "/opt/resolve/libs"
    bad_res = subprocess.run(
        [ffmpeg_bin, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=contaminated_env,
    )
    assert bad_res.returncode == 127
    assert "av_bessel_i0" in bad_res.stderr

    # 2. Làm sạch
    clean_env = clean_media_subprocess_env(contaminated_env)
    good_res = subprocess.run(
        [ffmpeg_bin, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=clean_env,
    )
    assert good_res.returncode == 0
    assert "ffmpeg version" in good_res.stdout


def test_audio_extractor_extract_and_duration_under_contaminated_env(tmp_path, monkeypatch):
    """
    Kiểm thử tích hợp thực tế: AudioExtractor xử lý tệp âm thanh thành công
    ngay cả khi tiến trình cha bị ô nhiễm LD_LIBRARY_PATH=/opt/resolve/libs.
    """
    wav_path = os.path.join(tmp_path, "sample_1s.wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"<{16000}h", *([0] * 16000)))

    if os.path.exists("/opt/resolve/libs"):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/resolve/libs")

    out_wav = os.path.join(tmp_path, "extracted_output.wav")

    ok = AudioExtractor.extract_audio(wav_path, out_wav)
    assert ok is True
    assert os.path.exists(out_wav)
    assert os.path.getsize(out_wav) > 0

    dur = AudioExtractor.get_audio_duration(wav_path)
    assert 0.95 <= dur <= 1.05
