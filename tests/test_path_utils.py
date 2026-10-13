import os
from pathlib import Path
from src.utils.path_utils import PosixPathAdapter

def test_resolve_path():
    p = PosixPathAdapter.resolve_path("~/test_file.mp4")
    assert isinstance(p, Path)
    assert p.is_absolute()
    assert "~" not in str(p)

def test_to_posix():
    win_path = r"C:\Users\Video\clip.mp4"
    posix = PosixPathAdapter.to_posix(win_path)
    assert "\\" not in posix
    assert posix.endswith("clip.mp4")

def test_to_fcpxml_uri_posix():
    path = "/media/user/Videos/clip sample.mp4"
    uri = PosixPathAdapter.to_fcpxml_uri(path)
    assert uri.startswith("file:///")
    assert "clip%20sample.mp4" in uri
    assert not uri.startswith("file:////")

def test_to_fcpxml_uri_preserves_slashes():
    path = "/path/to/my video.mp4"
    uri = PosixPathAdapter.to_fcpxml_uri(path)
    assert uri == "file:///path/to/my%20video.mp4"

def test_to_local_qurl():
    path = "/path/to/media.mp4"
    url = PosixPathAdapter.to_local_qurl(path)
    assert url.isLocalFile()
    assert url.toLocalFile() == str(Path(path).expanduser().resolve())
