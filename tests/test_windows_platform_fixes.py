import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

CREATE_NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_resolve_connect_windows_uses_create_no_window(monkeypatch):
    """
    Khi chạy trên Windows (sys.platform == 'win32'), ResolveAutomation.connect()
    gọi tasklist để kiểm tra Resolve.exe PHẢI truyền creationflags=CREATE_NO_WINDOW
    để không bị nháy cửa sổ console mỗi 3 giây.
    """
    from src.core.resolve_api import ResolveAutomation

    monkeypatch.delitem(sys.modules, "DaVinciResolveScript", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")

    automator = ResolveAutomation()
    captured_kwargs = []

    def fake_run(cmd, *args, **kwargs):
        captured_kwargs.append((cmd, kwargs))
        return MagicMock(returncode=0, stdout="INFO: No tasks are running...")

    with patch("subprocess.run", side_effect=fake_run):
        assert automator.connect() is False

    assert len(captured_kwargs) >= 1
    cmd, kwargs = captured_kwargs[0]
    assert "tasklist" in cmd[0].lower()
    assert kwargs.get("creationflags") == CREATE_NO_WINDOW_FLAG, (
        f"Expected creationflags={CREATE_NO_WINDOW_FLAG:#x} on Windows tasklist call, got {kwargs.get('creationflags')}"
    )


def test_find_fusionscript_lib_windows_uses_create_no_window(monkeypatch, tmp_path):
    """
    Khi _find_fusionscript_lib() chạy trên Windows và gọi powershell.exe để tìm Resolve,
    PHẢI truyền creationflags=CREATE_NO_WINDOW để không nháy cửa sổ PowerShell.
    """
    from src.core.resolve_api import ResolveAutomation

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "NonExistentProgFiles"))

    captured_kwargs = []

    def fake_run(cmd, *args, **kwargs):
        captured_kwargs.append((cmd, kwargs))
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=fake_run):
        ResolveAutomation._locate_fusionscript()

    assert len(captured_kwargs) >= 1
    cmd, kwargs = captured_kwargs[0]
    assert "powershell" in cmd[0].lower()
    assert kwargs.get("creationflags") == CREATE_NO_WINDOW_FLAG


def test_has_audio_stream_ffprobe_windows_uses_create_no_window(monkeypatch, tmp_path):
    """
    Khi _clip_has_audio gọi ffprobe trên Windows, PHẢI truyền creationflags=CREATE_NO_WINDOW.
    """
    from src.core.resolve_api import ResolveAutomation

    monkeypatch.setattr(sys, "platform", "win32")
    fake_media = tmp_path / "sample.mp4"
    fake_media.write_bytes(b"\x00" * 64)
    fake_ffprobe = tmp_path / "ffprobe.exe"
    fake_ffprobe.write_bytes(b"MZ")

    captured_kwargs = []

    def fake_run(cmd, *args, **kwargs):
        captured_kwargs.append((cmd, kwargs))
        return MagicMock(returncode=0, stdout="0\n")

    with patch("shutil.which", return_value=str(fake_ffprobe)), \
         patch("os.access", return_value=True), \
         patch("subprocess.run", side_effect=fake_run):
        res = ResolveAutomation._clip_has_audio(None, str(fake_media))

    assert res is True
    assert len(captured_kwargs) == 1
    _, kwargs = captured_kwargs[0]
    assert kwargs.get("creationflags") == CREATE_NO_WINDOW_FLAG


def test_run_title_insert_worker_isolated_windows_uses_create_no_window(monkeypatch):
    """
    Khi run_title_insert_worker_isolated gọi subprocess.run trên Windows,
    PHẢI truyền creationflags=CREATE_NO_WINDOW.
    """
    from src.core.resolve_api import run_title_insert_worker_isolated

    monkeypatch.setattr(sys, "platform", "win32")
    captured_kwargs = []

    def fake_run(cmd, *args, **kwargs):
        captured_kwargs.append((cmd, kwargs))
        return MagicMock(
            returncode=0,
            stdout='CHUNDVC_RESULT:{"ok": true, "track": 2, "start": 100}\n',
            stderr="",
        )

    with patch("subprocess.run", side_effect=fake_run):
        res = run_title_insert_worker_isolated("kinetic_hormozi")

    assert res["ok"] is True
    assert len(captured_kwargs) == 1
    _, kwargs = captured_kwargs[0]
    assert kwargs.get("creationflags") == CREATE_NO_WINDOW_FLAG


def test_windows_drag_and_drop_mime_urls_for_resolve(tmp_path):
    """
    Kiểm tra kéo thả (Drag & Drop) trên Windows cho cả 2 loại đạo cụ:
    1. File-backed media (Video .mp4, Audio .wav, LUT .cube)
    2. Procedural TextStylePreset (sinh file .setting thực tế trên đĩa)
    Đảm bảo QMimeData chứa local file URL chuẩn Windows (os.path.exists(url.toLocalFile()) == True)
    để DaVinci Resolve trên Windows nhận diện khi thả trực tiếp vào Timeline/Media Pool.
    """
    from src.ui.lux.library.asset_grid import create_asset_mime_data
    from src.core.text_preset import BUILTIN_PRESETS

    # 1. File-backed video/audio asset
    sample_mp4 = tmp_path / "meme_laugh.mp4"
    sample_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    video_asset = {
        "id": "meme_laugh",
        "name": "Meme Laugh",
        "category": "meme",
        "tab": "meme",
        "source_kind": "linked",
        "external_path": str(sample_mp4),
        "file_path": str(sample_mp4),
        "availability": "online",
    }

    mime_video = create_asset_mime_data(video_asset)
    assert mime_video.hasUrls() is True
    urls = mime_video.urls()
    assert len(urls) == 1
    assert urls[0].isLocalFile() is True
    local_file = urls[0].toLocalFile()
    assert os.path.isfile(local_file)
    assert Path(local_file).resolve() == sample_mp4.resolve()

    # 2. Procedural Text Preset -> .setting file
    preset = BUILTIN_PRESETS[0]
    mime_text = create_asset_mime_data(preset, sample_text="XIN CHÀO WINDOWS")
    assert mime_text.hasUrls() is True
    t_urls = mime_text.urls()
    assert len(t_urls) == 1
    assert t_urls[0].isLocalFile() is True
    setting_file = t_urls[0].toLocalFile()
    try:
        assert setting_file.endswith(".setting")
        assert os.path.isfile(setting_file)
        content = Path(setting_file).read_text(encoding="utf-8")
        assert "TextPlus" in content
        assert "XIN CHÀO WINDOWS" in content
    finally:
        if os.path.exists(setting_file):
            os.remove(setting_file)


def test_build_standalone_spec_includes_windows_icon():
    """
    Kiểm tra build_standalone.py sinh resolveflow.spec có cấu hình icon chuẩn cho EXE
    để file ResolveFlow-Assistant.exe hiển thị biểu tượng ChunDVC trên Windows.
    """
    import build_standalone
    spec_text = build_standalone.generate_spec_file(spec_path="")
    assert "chundvc.ico" in spec_text or "icon=" in spec_text


def test_windows_shortcut_installer_creates_launcher_and_shortcuts(tmp_path, monkeypatch):
    """
    Kiểm tra hàm cài đặt ứng dụng vào Start Menu (Programs) và Desktop trên Windows:
    - Tạo file khởi chạy không hiện cửa sổ đen (Launch_ChunDVC_Silent.vbs / pythonw)
    - Đăng ký shortcut ChunDVC.lnk trong thư mục Start Menu Programs (thư mục App của Windows)
    """
    import build_standalone

    assert hasattr(build_standalone, "install_windows_shortcuts"), (
        "build_standalone cần cung cấp hàm install_windows_shortcuts để đăng ký App vào Start Menu & Desktop trên Windows"
    )

    fake_programs = tmp_path / "StartMenu" / "Programs"
    fake_desktop = tmp_path / "Desktop"
    fake_programs.mkdir(parents=True)
    fake_desktop.mkdir(parents=True)

    res = build_standalone.install_windows_shortcuts(
        programs_dir=str(fake_programs),
        desktop_dir=str(fake_desktop),
        dry_run=True,
    )
    assert res["ok"] is True
    assert "ChunDVC.lnk" in res["start_menu_shortcut"]
    assert os.path.exists(res["ico_path"])


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows only")
def test_global_no_window_patch_applies_default_creationflags():
    """Lưới an toàn: mọi Popen (kể cả thư viện bên thứ 3) mặc định chạy ẩn console trên Windows."""
    from src.utils.subprocess_utils import install_global_no_window_patch

    assert install_global_no_window_patch() is True
    captured = {}
    original = subprocess.Popen._execute_child

    def fake_execute_child(self, *args, **kwargs):
        captured["ints"] = [a for a in args if isinstance(a, int) and not isinstance(a, bool)]
        raise OSError("stop")

    with patch.object(subprocess.Popen, "_execute_child", fake_execute_child):
        with pytest.raises(OSError):
            subprocess.Popen(["cmd.exe", "/c", "echo", "hi"])
    assert any(v & CREATE_NO_WINDOW_FLAG for v in captured["ints"] if v > 0)
    assert subprocess.Popen._execute_child is original
