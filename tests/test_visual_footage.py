"""Hồi quy dữ liệu hình ảnh cho AI Director."""

from pathlib import Path

from src.core.clip_manifest import ClipEntry, ClipManifest, build_clips_data
from src.core.story_copilot import StoryCopilot


def _manifest(path: Path) -> ClipManifest:
    entry = ClipEntry(
        clip_id="C01", content_hash="video-1", file_name=path.name,
        file_size=path.stat().st_size, duration_sec=12, fps=10,
        start_tc_frames=0, shot_time="", shot_time_source="none",
        current_path=str(path),
    )
    return ClipManifest(project_name="Test", manifest_id="abcdef12", clips=[entry])


class _Cache:
    def __init__(self, result):
        self.result = result

    def get_cached_scan(self, *_args):
        return self.result


def test_missing_whisper_scan_is_not_assumed_broll(tmp_path):
    path = tmp_path / "C01.mp4"
    path.write_bytes(b"video")
    clip = build_clips_data(_manifest(path), cache_mgr=_Cache(None))[0]
    assert clip["speech_status"] == "unscanned"
    assert clip["is_broll"] is False
    prompt = StoryCopilot.generate_copilot_prompt("Test", [clip])
    assert "Chưa quét lời thoại" in prompt
    assert "Cảnh thiên nhiên" not in prompt
    assert "TOÀN BỘ CLIP LÀ B-ROLL" not in prompt


def test_empty_transcription_has_unknown_visual_content(tmp_path):
    path = tmp_path / "C01.mp4"
    path.write_bytes(b"video")
    clip = build_clips_data(_manifest(path), cache_mgr=_Cache({"raw_subtitles": []}))[0]
    assert clip["speech_status"] == "no_dialogue_detected"
    assert clip["is_broll"] is False
    prompt = StoryCopilot.generate_copilot_prompt("Test", [clip])
    assert "Không phát hiện thoại" in prompt
    assert "Chưa có mô tả hình ảnh" in prompt


def test_visual_descriptions_are_scoped_to_clip_and_timestamps(tmp_path):
    path = tmp_path / "C01.mp4"
    path.write_bytes(b"video")
    from src.core.visual_index import VisualIndex

    index = VisualIndex(str(tmp_path / "visual"))
    index.save_descriptions(
        path, [{"time_sec": 3.0, "description": "Tay mở hộp máy pha cà phê"}],
        content_hash="video-1",
    )
    clip = build_clips_data(_manifest(path), cache_mgr=_Cache({"raw_subtitles": []}), visual_index=index)[0]
    assert clip["visual_segments"][0]["description"] == "Tay mở hộp máy pha cà phê"
    prompt = StoryCopilot.generate_copilot_prompt("Test", [clip])
    assert "Tay mở hộp máy pha cà phê" in prompt
    assert "00:03" in prompt


def test_visual_index_caches_and_invalidates_changed_media(tmp_path):
    import cv2
    import numpy as np
    from src.core.visual_index import VisualIndex

    path = tmp_path / "silent.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (48, 32))
    assert writer.isOpened()
    for i in range(25):
        writer.write(np.full((32, 48, 3), i * 8, dtype=np.uint8))
    writer.release()

    index = VisualIndex(str(tmp_path / "visual"))
    first = index.scan(path, interval_sec=2.0, max_samples=10)
    assert first["status"] == "sampled"
    assert [s["time_sec"] for s in first["samples"]] == [0.0, 2.0, 4.0]
    assert all(Path(s["image_path"]).is_file() for s in first["samples"])
    second = index.scan(path, interval_sec=2.0, max_samples=10)
    assert second["samples"] == first["samples"]
    assert second["cache_hit"] is True
    assert Path(first["contact_sheet_path"]).is_file()
    path.write_bytes(b"changed")
    changed = index.load(path)
    assert changed is None


def test_source_page_scans_visuals_in_background_and_reuses_cache(tmp_path, qapp, monkeypatch):
    import time
    from src.core.visual_index import VisualIndex
    from src.ui.director.director_state import SourceClipInfo
    from src.ui.director.pages.source_page import SourcePage

    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    calls = []

    def fake_scan(self, file_path, **kwargs):
        calls.append(str(file_path))
        return {"cache_hit": len(calls) > 1}

    monkeypatch.setattr(VisualIndex, "scan", fake_scan)
    page = SourcePage()
    page._clip_infos = [SourceClipInfo(str(path), path.name, duration_sec=10)]
    page._refresh_clip_list()
    assert page.btn_visual_scan.isVisible() is False  # Chưa show parent
    page.show()
    for expected in (1, 2):
        page.btn_visual_scan.click()
        deadline = time.monotonic() + 3
        while page._workers_registry and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.002)
        assert len(calls) == expected
        assert not page._workers_registry
        assert page.btn_visual_scan.isEnabled()
    assert "cache 1" in page.btn_visual_scan.text()
    page.close()
    page.deleteLater()
    qapp.processEvents()


def test_sampled_video_reaches_prompt_without_invented_scene(tmp_path):
    import cv2
    import numpy as np
    from src.core.visual_index import VisualIndex

    path = tmp_path / "silent.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (48, 32))
    assert writer.isOpened()
    for i in range(25):
        writer.write(np.full((32, 48, 3), i * 8, dtype=np.uint8))
    writer.release()

    index = VisualIndex(str(tmp_path / "visual"))
    index.scan(path, interval_sec=2.0)
    clip = build_clips_data(_manifest(path), cache_mgr=_Cache({"raw_subtitles": []}), visual_index=index)[0]
    assert clip["visual_samples"] == 3
    assert Path(clip["visual_contact_sheet"]).is_file()
    prompt = StoryCopilot.generate_copilot_prompt("Test", [clip])
    assert "Đã lấy 3 khung hình" in prompt
    assert "nội dung chưa được mô tả" in prompt
    assert "Cảnh thiên nhiên" not in prompt


def test_visual_worker_kept_alive_until_cancelled_scan_stops(tmp_path, qapp, monkeypatch):
    import threading
    import time
    from src.core.visual_index import VisualIndex
    from src.ui.director.director_state import SourceClipInfo
    from src.ui.director.pages.source_page import SourcePage

    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    started, release = threading.Event(), threading.Event()

    def held_scan(self, file_path, **kwargs):
        started.set()
        assert release.wait(4)
        if kwargs["cancelled"]():
            raise InterruptedError("cancelled")
        return {"cache_hit": False}

    monkeypatch.setattr(VisualIndex, "scan", held_scan)
    page = SourcePage()
    page._clip_infos = [SourceClipInfo(str(path), path.name, duration_sec=10)]
    page._refresh_clip_list()
    page.show()
    page.btn_visual_scan.click()
    assert started.wait(2)
    try:
        assert page.shutdown_workers() is False
        assert page._workers_registry
        release.set()
        deadline = time.monotonic() + 4
        while page._workers_registry and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.002)
        assert not page._workers_registry
    finally:
        release.set()
        page.close()
        page.deleteLater()
        qapp.processEvents()



def _mock_temporal_source(monkeypatch, index, path):
    """Giả lập biên decoder cho test HTTP; decoder thật được test riêng."""
    data = index.load(path)
    data.update(schema_version=3, temporal_config={"fixture": True},
                windows=[{"window_id": "W0", "start_sec": 0.0, "end_sec": data["duration_sec"],
                          "samples": data["samples"]}],
                coverage={"complete": True})
    index._write(path, data, None)
    monkeypatch.setattr(index, "prepare_windows", lambda *a, **kw: index.load(path))
    monkeypatch.setattr("src.core.visual_describer.local_model_info",
                        lambda *a, **kw: {"name": "vision-local", "digest": "test-digest"})


def test_local_vision_sends_contact_sheet_and_rejects_unknown_timestamps(tmp_path, monkeypatch):
    import json
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama

    path = tmp_path / "silent.mp4"
    path.write_bytes(b"video")
    image = tmp_path / "sheet.jpg"
    image.write_bytes(b"jpeg")
    index = VisualIndex(str(tmp_path / "visual"))
    index._write(path, {
        "schema_version": 1, "fingerprint": index._fingerprint(path),
        "duration_sec": 10, "samples": [
            {"time_sec": 1.0, "image_path": str(image)},
            {"time_sec": 5.0, "image_path": str(image)},
        ],
        "contact_sheet_path": str(image), "status": "sampled",
    }, None)
    _mock_temporal_source(monkeypatch, index, path)
    payloads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": json.dumps([
                {"start_sec": 1.0, "end_sec": 4.0, "description": "Hộp trên bàn", "evidence_times": [1.0]},
                {"start_sec": 9.0, "end_sec": 10.0, "description": "Nội dung bịa", "evidence_times": [9.0]},
            ])}}).encode()

    def fake_urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = describe_with_ollama(path, model="vision-local", index=index)
    assert [d["time_sec"] for d in result] == [1.0]
    assert payloads[0]["messages"][0]["images"]
    assert index.load(path)["descriptions"][0]["description"] == "Hộp trên bàn"


def test_local_vision_preserves_user_corrected_description(tmp_path, monkeypatch):
    import json
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama

    path = tmp_path / "silent.mp4"
    path.write_bytes(b"video")
    image = tmp_path / "sheet.jpg"
    image.write_bytes(b"jpeg")
    index = VisualIndex(str(tmp_path / "visual"))
    index._write(path, {
        "schema_version": 1, "fingerprint": index._fingerprint(path),
        "duration_sec": 10, "samples": [
            {"time_sec": 1.0, "image_path": str(image)},
            {"time_sec": 5.0, "image_path": str(image)},
        ],
        "contact_sheet_path": str(image), "status": "sampled",
    }, None)
    index.save_descriptions(path, [{"time_sec": 1.0, "description": "Máy pha cà phê"}])

    _mock_temporal_source(monkeypatch, index, path)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"message": {"content": json.dumps([
                {"start_sec": 1.0, "end_sec": 4.0, "description": "Sai: máy nướng bánh", "evidence_times": [1.0]},
                {"start_sec": 5.0, "end_sec": 8.0, "description": "Cốc cà phê", "evidence_times": [5.0]},
            ])}}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    describe_with_ollama(path, model="vision-local", index=index)
    notes = index.load(path)["descriptions"]
    assert notes[0]["description"] == "Máy pha cà phê"
    assert notes[0]["origin"] == "user"
    assert notes[1]["description"] == "Cốc cà phê"


def test_local_vision_connection_failure_does_not_fake_success(tmp_path, monkeypatch):
    from urllib.error import URLError
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama

    path = tmp_path / "silent.mp4"
    path.write_bytes(b"video")
    image = tmp_path / "sheet.jpg"
    image.write_bytes(b"jpeg")
    index = VisualIndex(str(tmp_path / "visual"))
    index._write(path, {
        "schema_version": 1, "fingerprint": index._fingerprint(path),
        "duration_sec": 10, "samples": [{"time_sec": 1.0, "image_path": str(image)}],
        "contact_sheet_path": str(image), "status": "sampled",
    }, None)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: (_ for _ in ()).throw(URLError("offline")))
    try:
        describe_with_ollama(path, model="vision-local", index=index)
    except ConnectionError as exc:
        assert "Ollama" in str(exc)
    else:
        assert False, "Phải báo lỗi khi Ollama chưa chạy"
    assert not index.load(path).get("descriptions")


def test_visual_description_button_reports_actual_completed_count(tmp_path, qapp, monkeypatch):
    import time
    from src.ui.director.director_state import SourceClipInfo
    from src.ui.director.pages.source_page import SourcePage

    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    def missing_model(*args, **kwargs):
        raise ValueError("Chưa cài model kiểm thử")
    monkeypatch.setattr("src.core.visual_describer.local_model_info", missing_model)
    page = SourcePage()
    page._clip_infos = [SourceClipInfo(str(path), path.name, duration_sec=10)]
    page._refresh_clip_list()
    page.show()
    try:
        page._start_visual_description("vision-local", "http://127.0.0.1:11434")
        deadline = time.monotonic() + 3
        while page._workers_registry and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.002)
        assert not page._workers_registry
        assert "0/1" in page.btn_describe.text()
        assert page.btn_describe.isEnabled()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_close_during_description_keeps_worker_and_restores_buttons(tmp_path, qapp, monkeypatch):
    import threading
    import time
    from src.ui.director.director_state import SourceClipInfo
    from src.ui.director.pages.source_page import SourcePage

    path = tmp_path / 'video.mp4'
    path.write_bytes(b'video')
    started, release = threading.Event(), threading.Event()

    def describe(*args, **kwargs):
        started.set()
        assert release.wait(3)
        assert kwargs['cancelled']()
        raise InterruptedError('cancelled')

    monkeypatch.setattr('src.core.visual_describer.describe_with_ollama', describe)
    page = SourcePage()
    page._clip_infos = [SourceClipInfo(str(path), path.name, duration_sec=10)]
    page._refresh_clip_list()
    page.show()
    page._start_visual_description('test', 'http://localhost:11434')
    assert started.wait(2)
    try:
        page.close()
        assert page._workers_registry
        release.set()
        deadline = time.monotonic() + 3
        while page._workers_registry and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert not page._workers_registry
        page.show()
        assert page.btn_describe.isEnabled()
        assert page.btn_visual_scan.isEnabled()
    finally:
        release.set()
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_temporal_segment_validation_and_fields():
    import pytest
    from src.core.visual_index import validate_segment

    seg = {
        "start_sec": 2.0,
        "end_sec": 6.5,
        "description": "Người mở nắp hộp",
        "objects": ["hộp", "bàn"],
        "observed_actions": ["mở nắp"],
        "evidence_times": [2.0, 4.0, 6.0],
        "analysis_status": "described",
    }
    validated = validate_segment(seg, duration_sec=10.0, allowed_evidence_times={2.0, 4.0, 6.0})
    assert validated["start_sec"] == 2.0
    assert validated["end_sec"] == 6.5
    assert validated["time_sec"] == 2.0
    assert validated["description"] == "Người mở nắp hộp"
    assert validated["objects"] == ["hộp", "bàn"]
    assert validated["observed_actions"] == ["mở nắp"]
    assert validated["evidence_times"] == [2.0, 4.0, 6.0]
    assert validated["analysis_status"] == "described"
    assert validated["analysis_version"] == "temporal-windows-v3"

    # Sai mốc: start > duration hoặc end < start
    with pytest.raises(ValueError):
        validate_segment({"start_sec": 15.0, "end_sec": 16.0, "description": "Lỗi"}, duration_sec=10.0)
    with pytest.raises(ValueError):
        validate_segment({"start_sec": 5.0, "end_sec": 3.0, "description": "Lỗi"}, duration_sec=10.0)
    # Thiếu mô tả
    with pytest.raises(ValueError):
        validate_segment({"start_sec": 1.0, "end_sec": 2.0, "description": ""}, duration_sec=10.0)


def test_temporal_observation_model_structured_output(tmp_path, monkeypatch):
    import json
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama

    path = tmp_path / "action.mp4"
    path.write_bytes(b"video")
    image = tmp_path / "sheet.jpg"
    image.write_bytes(b"jpeg")
    index = VisualIndex(str(tmp_path / "visual"))
    index._write(path, {
        "schema_version": 2, "fingerprint": index._fingerprint(path),
        "duration_sec": 20.0, "samples": [
            {"time_sec": 0.0, "image_path": str(image)},
            {"time_sec": 1.0, "image_path": str(image)},
            {"time_sec": 5.0, "image_path": str(image)},
            {"time_sec": 6.0, "image_path": str(image)},
            {"time_sec": 10.0, "image_path": str(image)},
        ],
        "contact_sheet_path": str(image), "status": "sampled",
    }, None)

    _mock_temporal_source(monkeypatch, index, path)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"message": {"content": json.dumps([
                {
                    "start_sec": 0.0, "end_sec": 5.0,
                    "description": "Cầm ly nước",
                    "objects": ["ly", "nước"],
                    "observed_actions": ["nhấc ly"],
                    "evidence_times": [0.0, 1.0],
                    "analysis_status": "described"
                },
                {
                    "start_sec": 5.0, "end_sec": 10.0,
                    "description": "Rót nước vào bình",
                    "objects": ["bình", "nước"],
                    "observed_actions": ["rót nước"],
                    "evidence_times": [5.0, 6.0],
                    "analysis_status": "described"
                }
            ])}}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    results = describe_with_ollama(path, model="vision-local", index=index)
    assert len(results) == 2
    assert results[0]["observed_actions"] == ["nhấc ly"]
    assert results[1]["observed_actions"] == ["rót nước"]
    cached = index.load(path)
    assert len(cached["descriptions"]) == 2
    assert cached["descriptions"][0]["objects"] == ["ly", "nước"]


def test_prompt_preserves_temporal_distribution_across_long_clips(tmp_path):
    path = tmp_path / "long.mp4"
    path.write_bytes(b"video")
    from src.core.visual_index import VisualIndex

    index = VisualIndex(str(tmp_path / "visual"))
    # Tạo 16 đoạn phân bố từ 0s đến 160s
    segments = [
        {
            "start_sec": float(i * 10),
            "end_sec": float((i + 1) * 10),
            "description": f"Hành động giai đoạn {i}",
            "observed_actions": [f"thao tác {i}"],
            "evidence_times": [float(i * 10)],
            "analysis_status": "described",
        }
        for i in range(16)
    ]
    index.save_descriptions(path, segments, content_hash="video-long")
    entry = ClipEntry(
        clip_id="C01", content_hash="video-long", file_name="long.mp4",
        file_size=path.stat().st_size, duration_sec=160, fps=10,
        start_tc_frames=0, shot_time="", shot_time_source="none",
        current_path=str(path),
    )
    manifest = ClipManifest(project_name="Test", manifest_id="long1234", clips=[entry])
    clip = build_clips_data(manifest, cache_mgr=_Cache({"raw_subtitles": []}), visual_index=index)[0]
    prompt = StoryCopilot.generate_copilot_prompt("Test", [clip])

    # Kiểm tra cả phần đầu và phần đuôi clip đều hiện diện trong prompt (không bị cắt cụt)
    assert "Hành động giai đoạn 0" in prompt
    assert "Hành động giai đoạn 14" in prompt or "Hành động giai đoạn 15" in prompt
    assert "đoạn hình ảnh khác đến 02:40" in prompt
    assert "[Hành động: thao tác" in prompt
