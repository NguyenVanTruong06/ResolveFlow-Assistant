import os
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from src.core.resolve_api import SubtitleConfig, ResolveAutomation

def test_subtitle_config_validation() -> None:
    """
    Kiểm tra tính hợp lệ của cấu hình Pydantic SubtitleConfig.
    """
    config = SubtitleConfig(max_chars_per_line=30, font_size=50, color_hex="#FF0000")
    assert config.max_chars_per_line == 30
    assert config.font_size == 50
    assert config.color_hex == "#FF0000"

    with pytest.raises(ValidationError):
        # max_chars_per_line quá nhỏ (giới hạn dưới là 10)
        SubtitleConfig(max_chars_per_line=5)

def test_generate_srt(tmp_path) -> None:
    """
    Xác thực thuật toán sinh tệp SRT từ dữ liệu giây thô.
    Đặc biệt kiểm tra định dạng thời gian HH:MM:SS,mmm.
    """
    subtitles = [
        {"start": 1.5, "end": 4.25, "text": "Chào các bạn."},
        {"start": 5.1234, "end": 8.9, "text": "ResolveFlow Assistant đang chạy."}
    ]

    output_file = os.path.join(tmp_path, "test.srt")
    ResolveAutomation.generate_srt(subtitles, output_file)

    assert os.path.exists(output_file)

    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Xác thực cấu trúc tệp SRT
    expected_lines = [
        "1",
        "00:00:01,500 --> 00:00:04,250",
        "Chào các bạn.",
        "",
        "2",
        "00:00:05,123 --> 00:00:08,900",
        "ResolveFlow Assistant đang chạy."
    ]
    for expected in expected_lines:
        assert expected in content

def test_connect_fail() -> None:
    """
    Đảm bảo khi không có ứng dụng DaVinci Resolve chạy nền, hàm connect trả về False
    mà không làm sập (crash) ứng dụng.
    """
    automator = ResolveAutomation()
    # Mocking load_resolve_api để mô phỏng không có Resolve chạy nền
    with patch('src.core.resolve_bridge.load_resolve_api', return_value=(None, "No Resolve")):
        connected = automator.connect()
        assert connected is False

def test_insert_subtitles_to_timeline_no_timeline(tmp_path) -> None:
    """
    Kiểm tra insert_subtitles_to_timeline khi không có timeline hoạt động.
    """
    automator = ResolveAutomation()
    subtitles = [
        {"start": 1.0, "end": 2.0, "text": "Test line."}
    ]
    config = SubtitleConfig()
    output_file = os.path.join(tmp_path, "output.srt")
    
    logs = []
    def log_cb(msg):
        logs.append(msg)
        
    with patch.object(automator, 'get_active_timeline', return_value=None):
        res = automator.insert_subtitles_to_timeline(
            subtitles, 
            config, 
            output_srt_path=output_file, 
            log_callback=log_cb
        )
        assert res is True
        
    assert os.path.exists(output_file)
    assert any("Không tìm thấy Timeline" in log for log in logs)
    assert any("Đã xuất file phụ đề SRT cục bộ thành công" in log for log in logs)
    assert any(os.path.abspath(output_file) in log for log in logs)

def test_import_edl_to_timeline(tmp_path) -> None:
    """
    Kiểm tra import_edl_to_timeline khi Resolve không chạy.
    """
    automator = ResolveAutomation()
    edl_file = os.path.join(tmp_path, "test.edl")
    video_file = os.path.join(tmp_path, "test.mp4")
    with open(edl_file, "w", encoding="utf-8") as f:
        f.write("")
    with open(video_file, "w", encoding="utf-8") as f:
        f.write("")
        
    logs = []
    def log_cb(msg):
        logs.append(msg)
        
    with patch.object(automator, 'connect', return_value=False):
        res = automator.import_edl_to_timeline(
            edl_path=edl_file,
            video_path=video_file,
            timeline_name="Test Timeline",
            log_callback=log_cb
        )
        assert res is False
        
    assert any("Không thể kết nối tới ứng dụng DaVinci Resolve" in log for log in logs)

def test_split_subtitles() -> None:
    from src.core.resolve_api import split_subtitles
    raw_subtitles = [
        {
            "start": 0.0,
            "end": 4.5,
            "text": "Hello world this is a test segment for resolve flow.",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.6, "end": 1.0},
                {"word": "this", "start": 1.1, "end": 1.5},
                {"word": "is", "start": 1.6, "end": 2.0},
                {"word": "a", "start": 2.1, "end": 2.2},
                {"word": "test", "start": 2.3, "end": 2.8},
                {"word": "segment", "start": 2.9, "end": 3.5},
                {"word": "for", "start": 3.6, "end": 3.9},
                {"word": "resolve", "start": 4.0, "end": 4.2},
                {"word": "flow.", "start": 4.3, "end": 4.5}
            ]
        }
    ]
    # Test 1: Chế độ ký tự (Characters mode)
    wrapped_chars = split_subtitles(raw_subtitles, max_chars=15, mode="characters")
    for seg in wrapped_chars:
        assert len(seg["text"]) <= 15
        assert seg["start"] < seg["end"]

    # Test 2: Chế độ số từ (Words mode - ví dụ 3 từ/dòng)
    wrapped_words = split_subtitles(raw_subtitles, limit=3, mode="words")
    for seg in wrapped_words:
        assert len(seg["text"].split()) <= 3
        assert seg["start"] < seg["end"]

    # Test 3: Chế độ 1 từ/dòng (Shorts single-word pop)
    single_word_subs = split_subtitles(raw_subtitles, limit=1, mode="words")
    assert len(single_word_subs) == 10
    for seg in single_word_subs:
        assert len(seg["text"].split()) == 1

    # Test 4: Tương thích ngược gọi theo positional arg
    wrapped_legacy = split_subtitles(raw_subtitles, 20)
    for seg in wrapped_legacy:
        assert len(seg["text"]) <= 20

def test_is_vertical_video(tmp_path) -> None:
    from src.core.resolve_api import is_vertical_video
    assert is_vertical_video("non_existent.mp4") is False

def test_map_subtitles_to_timeline() -> None:
    from src.core.resolve_api import map_subtitles_to_timeline, map_time_to_timeline
    
    keep_intervals = [(1.0, 3.0), (5.0, 8.0)]
    
    assert map_time_to_timeline(0.5, keep_intervals) == 0.0
    assert map_time_to_timeline(1.5, keep_intervals) == 0.5
    assert map_time_to_timeline(4.0, keep_intervals) == 2.0
    assert map_time_to_timeline(6.0, keep_intervals) == 3.0
    
    subs = [
        {
            "start": 1.2,
            "end": 2.5,
            "text": "Hello world",
            "words": [
                {"word": "Hello", "start": 1.2, "end": 1.8},
                {"word": "world", "start": 1.9, "end": 2.5}
            ]
        }
    ]
    mapped = map_subtitles_to_timeline(subs, keep_intervals, base_rec_time=0.0)
    assert len(mapped) == 1
    assert abs(mapped[0]["start"] - 0.2) < 0.001
    assert abs(mapped[0]["end"] - 1.5) < 0.001
    assert abs(mapped[0]["words"][0]["start"] - 0.2) < 0.001

def test_map_subtitles_removes_deleted_initial_greeting() -> None:
    """
    Test bắt lỗi: Khi đoạn đầu video (0.0s - 3.0s chứa câu chào) bị cắt bỏ khỏi video (Silence Cut hoặc Bad Takes),
    phụ đề của bản đã cắt KHÔNG ĐƯỢC PHÉP chứa câu chào đó.
    """
    from src.core.resolve_api import map_subtitles_to_timeline

    # Đoạn đầu 0.0s đến 3.0s bị cắt bỏ, video chỉ giữ từ 3.0s đến 10.0s
    keep_intervals = [(3.0, 10.0)]

    subs = [
        {
            "start": 0.5,
            "end": 2.5,
            "text": "Xin chào các bạn đã đến với video hôm nay",
            "words": [
                {"word": "Xin", "start": 0.5, "end": 0.9},
                {"word": "chào", "start": 1.0, "end": 1.4},
                {"word": "các", "start": 1.5, "end": 1.7},
                {"word": "bạn", "start": 1.8, "end": 2.1},
                {"word": "đã", "start": 2.2, "end": 2.5}
            ]
        },
        {
            "start": 3.5,
            "end": 6.0,
            "text": "Nội dung chính bắt đầu từ đây",
            "words": [
                {"word": "Nội", "start": 3.5, "end": 4.0},
                {"word": "dung", "start": 4.1, "end": 4.5},
                {"word": "chính", "start": 4.6, "end": 5.0},
                {"word": "bắt", "start": 5.1, "end": 5.5},
                {"word": "đầu", "start": 5.6, "end": 6.0}
            ]
        }
    ]

    mapped = map_subtitles_to_timeline(subs, keep_intervals, base_rec_time=0.0)

    # Xác nhận: Chỉ còn đúng 1 phân đoạn phụ đề (câu nội dung chính), câu chào đã bị loại bỏ 100%
    assert len(mapped) == 1
    assert "Xin chào" not in mapped[0]["text"]
    assert "Nội dung chính" in mapped[0]["text"]
    # Mốc thời gian mới trên timeline đã cắt: 3.5s - 3.0s = 0.5s
    assert abs(mapped[0]["start"] - 0.5) < 0.001
    assert abs(mapped[0]["end"] - 3.0) < 0.001


def test_resolve_media_pool_operations(tmp_path):
    from unittest.mock import MagicMock
    automator = ResolveAutomation()
    
    # 1. Khi chưa kết nối
    automator.resolve = None
    automator.current_project = None
    assert automator.is_connected() is False
    assert automator.check_clips_in_media_pool([]) == {}
    
    # 2. Giả lập kết nối thành công với Resolve Media Pool
    mock_resolve = MagicMock()
    mock_proj = MagicMock()
    mock_pool = MagicMock()
    mock_root = MagicMock()
    
    mock_clip = MagicMock()
    mock_clip.GetClipProperty.return_value = os.path.join(tmp_path, "1.mp4")
    mock_clip.GetName.return_value = "1.mp4"
    
    mock_root.GetClipList.return_value = [mock_clip]
    mock_root.GetSubFolderList.return_value = []
    mock_pool.GetRootFolder.return_value = mock_root
    mock_proj.GetMediaPool.return_value = mock_pool
    
    automator.resolve = mock_resolve
    automator.current_project = mock_proj
    assert automator.is_connected() is True
    
    # Kiểm tra get_media_pool_file_paths
    paths = automator.get_media_pool_file_paths()
    assert len(paths) == 1
    assert "1.mp4" in paths[0]
    
    # Kiểm tra check_clips_in_media_pool
    check_res = automator.check_clips_in_media_pool([
        str(tmp_path / "1.mp4"),
        str(tmp_path / "missing_clip.mp4")
    ])
    assert check_res[str(tmp_path / "1.mp4")] is True
    assert check_res[str(tmp_path / "missing_clip.mp4")] is False
    
    # Kiểm tra import_media_to_media_pool
    test_file = tmp_path / "2.mp4"
    test_file.write_bytes(b"content")
    mock_pool.ImportMedia.return_value = True
    
    logs = []
    imp_res = automator.import_media_to_media_pool([str(test_file)], log_callback=logs.append)
    assert imp_res is True
    mock_pool.ImportMedia.assert_called_once()
    assert any("Đã tự động nạp" in l for l in logs)



def test_connect_reports_readable_error_when_scripting_refused(monkeypatch):
    import sys
    import types
    from src.core.resolve_api import ResolveAutomation
    fake = types.ModuleType("DaVinciResolveScript")

    def boom(_name):
        raise SystemError("initialization of fusionscript failed without raising an exception")
    fake.scriptapp = boom
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", fake)
    r = ResolveAutomation()
    assert r.connect() is False
    assert "External scripting" in r.last_connect_error and "Free" in r.last_connect_error


def test_auto_import_timeline_to_resolve_success(tmp_path):
    from unittest.mock import MagicMock, patch
    from src.core.resolve_api import auto_import_timeline_to_resolve

    sample_fcpxml = tmp_path / "cut_test.fcpxml"
    sample_fcpxml.write_text("<fcpxml/>", encoding="utf-8")

    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_mp = MagicMock()
    mock_timeline = MagicMock()

    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_proj.GetMediaPool.return_value = mock_mp
    mock_mp.ImportTimelineFromFile.return_value = mock_timeline

    with patch("src.core.resolve_bridge.load_resolve_api", return_value=(mock_resolve, "OK")):
        success, msg = auto_import_timeline_to_resolve(str(sample_fcpxml), "Test Timeline")
        assert success is True
        assert "thành công" in msg
        mock_mp.ImportTimelineFromFile.assert_called_once_with(
            str(sample_fcpxml),
            {"timelineName": "Test Timeline", "importSourceClips": True}
        )
        mock_resolve.OpenPage.assert_called_once_with("edit")


def test_auto_import_timeline_to_resolve_autoproject(tmp_path):
    from unittest.mock import MagicMock, patch
    from src.core.resolve_api import auto_import_timeline_to_resolve

    sample_fcpxml = tmp_path / "cut_test.fcpxml"
    sample_fcpxml.write_text("<fcpxml/>", encoding="utf-8")

    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_mp = MagicMock()
    mock_timeline = MagicMock()

    # GetCurrentProject returns None initially (user at Project Manager)
    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = None
    mock_pm.LoadProject.return_value = None
    mock_pm.CreateProject.return_value = mock_proj
    mock_proj.GetMediaPool.return_value = mock_mp
    mock_mp.ImportTimelineFromFile.return_value = mock_timeline

    with patch("src.core.resolve_bridge.load_resolve_api", return_value=(mock_resolve, "OK")):
        success, msg = auto_import_timeline_to_resolve(str(sample_fcpxml))
        assert success is True
        mock_pm.CreateProject.assert_called_once_with("ResolveFlow_AutoProject")
        mock_resolve.OpenPage.assert_called_once_with("edit")


def test_auto_import_timeline_fallback_fcpxml_to_xml(tmp_path):
    from unittest.mock import MagicMock, patch
    from src.core.resolve_api import auto_import_timeline_to_resolve

    sample_fcpxml = tmp_path / "cut_test.fcpxml"
    sample_fcpxml.write_text("<fcpxml/>", encoding="utf-8")
    sample_xml = tmp_path / "cut_test.xml"
    sample_xml.write_text("<xmeml/>", encoding="utf-8")

    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_mp = MagicMock()
    mock_timeline = MagicMock()

    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_proj.GetMediaPool.return_value = mock_mp

    # Primary (.fcpxml) fails (returns None), fallback (.xml) succeeds
    def import_side_effect(path, *args, **kwargs):
        if str(path) == str(sample_fcpxml):
            return None
        if str(path) == str(sample_xml):
            return mock_timeline
        return None

    mock_mp.ImportTimelineFromFile.side_effect = import_side_effect

    with patch("src.core.resolve_bridge.load_resolve_api", return_value=(mock_resolve, "OK")):
        success, msg = auto_import_timeline_to_resolve(str(sample_fcpxml))
        assert success is True
        assert "cut_test.xml" in msg
        mock_resolve.OpenPage.assert_called_once_with("edit")


def test_connect_linux_process_detection_supports_bin_real():
    """Kiểm tra connect() trên Linux nhận diện được tiến trình /opt/resolve/bin_real/resolve."""
    automator = ResolveAutomation()
    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_proj = MagicMock()
    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_proj

    with patch("sys.platform", "linux"), \
         patch("subprocess.run") as mock_subproc, \
         patch("src.core.resolve_bridge.load_resolve_api", return_value=(mock_resolve, "OK")):
        # pgrep -f matches
        mock_subproc.return_value = MagicMock(returncode=0, stdout="14723\n")
        assert automator.connect() is True
        assert automator.is_connected() is True


def test_director_window_import_from_resolve(tmp_path):
    """Kiểm tra tính năng '⚡ Từ DaVinci' trong DirectorWindow."""
    from PySide6.QtWidgets import QApplication
    from src.ui.director.director_window import DirectorWindow

    app = QApplication.instance() or QApplication([])
    win = DirectorWindow()

    fake_video = tmp_path / "DJI_001.MP4"
    fake_video.write_text("video")

    mock_ra = MagicMock()
    mock_ra.connect.return_value = True
    mock_ra.auto_detect_video_paths.return_value = [str(fake_video)]
    mock_ra.current_project.GetName.return_value = "TestProj"

    with patch("src.core.resolve_api.ResolveAutomation", return_value=mock_ra), \
         patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe, \
         patch("PySide6.QtWidgets.QMessageBox.information"):
        mock_probe.side_effect = lambda fpath, m, l: MagicMock(
            path=fpath, filename=os.path.basename(fpath), duration_sec=10.0,
            duration_str="00:10", resolution="1080p", has_cache=False, file_size_mb=5.0
        )
        win._on_import_from_resolve()

    assert len(win.page_source._clip_infos) == 1
    assert win.page_source._clip_infos[0].filename == "DJI_001.MP4"
    win.close()


def test_insert_media_at_playhead(tmp_path):
    from src.core.resolve_api import ResolveAutomation
    auto = ResolveAutomation()
    auto.resolve = MagicMock()
    auto.current_project = MagicMock()
    auto.current_timeline = MagicMock()
    auto.current_project.GetCurrentTimeline.return_value = auto.current_timeline

    mock_mp = MagicMock()
    auto.current_project.GetMediaPool.return_value = mock_mp
    auto.current_timeline.GetTrackCount.return_value = 2
    auto.current_timeline.GetCurrentTimecode.return_value = "01:00:00:00"

    fake_clip = MagicMock()
    fake_clip.GetName.return_value = "meme.mp4"
    fake_clip.GetClipProperty.side_effect = lambda prop: "/tmp/meme.mp4" if prop == "File Path" else "90"
    auto.get_media_pool_clips = MagicMock(return_value=[fake_clip])

    mock_mp.AppendToTimeline.return_value = [MagicMock()]

    test_file = tmp_path / "meme.mp4"
    test_file.write_text("fake video data")

    with patch("src.core.resolve_api.current_record_frame", return_value=(150, "01:00:05:00")):
        res = auto.insert_media_at_playhead(str(test_file), target_track=3, duration_sec=2.5)

    assert res is True
    assert auto.current_timeline.AddTrack.called
    assert mock_mp.AppendToTimeline.called
    call_args = mock_mp.AppendToTimeline.call_args[0][0][0]
    assert call_args["trackIndex"] == 3
    assert call_args["recordFrame"] == 150
    assert call_args["mediaType"] == 1


def test_ensure_media_out_connected():
    from src.core.resolve_bridge import _ensure_media_out_connected
    comp = MagicMock()
    mo = MagicMock()
    inp = MagicMock()
    inp.GetConnectedOutput.return_value = None
    mo.FindMainInput.return_value = inp
    comp.FindTool.side_effect = lambda name: mo if name == "MediaOut1" else None

    macro = MagicMock()
    macro.GetAttrs.return_value = {"TOOLS_RegID": "MacroOperator"}
    out = MagicMock()
    macro.FindMainOutput.return_value = out
    comp.GetToolList.return_value = {"Macro1": macro, "MO": mo}

    res = _ensure_media_out_connected(comp)
    assert res is True
    inp.ConnectTo.assert_called_once_with(out)



