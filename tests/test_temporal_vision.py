"""Ràng buộc bằng chứng, cửa sổ thời gian và cache cho local vision."""

import math
import pytest

from src.core.visual_index import validate_segment


@pytest.mark.parametrize("start,end,duration", [
    (0, 11, 10), (2, 2, 10), (math.nan, 5, 10), (0, math.inf, 10),
    (0, 5, math.inf), (0, 5, math.nan), (-1, 5, 10),
])
def test_model_segments_require_finite_bounded_duration(start, end, duration):
    with pytest.raises(ValueError):
        validate_segment(
            {"start_sec": start, "end_sec": end, "description": "Cảnh quay", "evidence_times": [1]},
            duration, {1.0}, default_origin="local_model:vision",
        )


@pytest.mark.parametrize("evidence", [[], [99], [1, 99], [math.nan], [6]])
def test_model_evidence_must_exist_and_belong_to_segment(evidence):
    with pytest.raises(ValueError):
        validate_segment(
            {"start_sec": 0, "end_sec": 5, "description": "Cốc", "evidence_times": evidence},
            10, {1.0, 6.0}, default_origin="local_model:vision",
        )


def test_single_frame_does_not_confirm_temporal_action_or_spoof_provenance():
    result = validate_segment({
        "start_sec": 0, "end_sec": 5, "description": "Tay cạnh cốc",
        "evidence_times": [1], "observed_actions": ["nhấc cốc"],
        "origin": "user", "model": "forged", "analysis_version": "forged",
    }, 10, {1.0}, default_origin="local_model:vision", default_model="vision", default_version="v3")
    assert result["observed_actions"] == []
    assert result["analysis_status"] == "needs_review"
    assert result["origin"] == "local_model:vision"
    assert result["model"] == "vision"
    assert result["analysis_version"] == "v3"


def test_manual_legacy_point_without_duration_is_not_a_fake_segment():
    result = validate_segment({"time_sec": 3, "description": "Cốc"}, None)
    assert result["observation_kind"] == "point"
    assert "end_sec" not in result
    assert result["analysis_status"] == "needs_review"


@pytest.fixture
def source_video(tmp_path):
    import cv2
    import numpy as np
    path = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (64, 48))
    assert writer.isOpened()
    for i in range(30):
        writer.write(np.full((48, 64, 3), i * 7, dtype=np.uint8))
    writer.release()
    return path


def test_temporal_windows_use_decoded_pts_and_report_partial_coverage(source_video, tmp_path):
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / "index"))
    result = index.prepare_windows(source_video, window_sec=2, frames_per_window=3, max_windows=2)
    assert len(result["windows"]) == 2
    assert result["coverage"]["complete"] is False
    assert result["windows"][-1]["end_sec"] == 6
    assert any(s["time_sec"] != s["requested_time_sec"] for w in result["windows"] for s in w["samples"])
    for window in result["windows"]:
        assert all(window["start_sec"] <= s["time_sec"] < window["end_sec"] for s in window["samples"])
        assert all(s["timestamp_basis"] == "decoded_pts" for s in window["samples"])


def test_window_cache_reuses_and_settings_invalidate_only_machine_notes(source_video, tmp_path, monkeypatch):
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / "index"))
    first = index.prepare_windows(source_video, window_sec=2, frames_per_window=3, max_windows=2)
    index.save_descriptions(source_video, [
        {"time_sec": 1, "description": "User note", "origin": "user"},
        {"start_sec": 0, "end_sec": 2, "description": "Model note", "origin": "local_model:test"},
    ])
    cached = index.prepare_windows(source_video, window_sec=2, frames_per_window=3, max_windows=2)
    assert cached["cache_hit"] is True
    assert cached["windows"] == first["windows"]
    changed = index.prepare_windows(source_video, window_sec=2, frames_per_window=4, max_windows=2)
    assert changed["cache_hit"] is False
    assert [d["description"] for d in changed["descriptions"]] == ["User note"]


def test_cancelled_windows_leave_previous_cache_intact(source_video, tmp_path):
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / "index"))
    before = index.prepare_windows(source_video, window_sec=2, frames_per_window=3, max_windows=2)
    with pytest.raises(InterruptedError):
        index.prepare_windows(source_video, window_sec=3, cancelled=lambda: True)
    assert index.load(source_video) == {k: v for k, v in before.items() if k != "cache_hit"}


@pytest.fixture
def local_vision(monkeypatch):
    import json
    import src.core.visual_describer as backend
    identity = {"name": "vision:latest", "digest": "sha256:one"}
    calls = []
    cancelled = [False]

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    def request(req, timeout):
        if req.full_url.endswith('/api/tags'):
            return Response({"models": [dict(identity)]})
        if req.full_url.endswith('/api/show'):
            return Response({"capabilities": ["vision", "completion"]})
        payload = json.loads(req.data)
        calls.append(payload)
        context = json.loads(payload["messages"][0]["content"].split("INPUT_JSON:\n")[1])
        return Response({"message": {"content": json.dumps([{
            "start_sec": context["start_sec"], "end_sec": context["end_sec"],
            "description": "Tay di chuyển cạnh cốc", "observed_actions": ["nhấc cốc"],
            "evidence_times": context["frame_times"][:2],
            "origin": "user", "model": "forged", "analysis_version": "forged",
        }])}})

    monkeypatch.setattr(backend.urllib.request, "urlopen", request)
    return identity, calls, cancelled


def test_describer_sends_windows_and_cache_tracks_digest(source_video, tmp_path, local_vision):
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama
    identity, calls, _ = local_vision
    index = VisualIndex(str(tmp_path / 'index'))
    options = dict(index=index, window_sec=2, frames_per_window=3, max_windows=3)
    result = describe_with_ollama(source_video, 'vision', **options)
    assert len(calls) == 3
    assert all(len(p["messages"][0]["images"]) == 3 for p in calls)
    assert all(d['origin'] == 'local_model:vision:latest' for d in result)
    assert all(d['model'] == 'vision:latest' for d in result)
    assert all(d['analysis_version'] != 'forged' for d in result)
    describe_with_ollama(source_video, 'vision', **options)
    assert len(calls) == 3
    identity['digest'] = 'sha256:two'
    describe_with_ollama(source_video, 'vision', **options)
    assert len(calls) == 6


def test_cancel_after_model_response_does_not_commit_analysis(source_video, tmp_path, local_vision):
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama
    index = VisualIndex(str(tmp_path / 'index'))
    # Callback trở thành true sau request đầu tiên.
    _, calls, _ = local_vision
    with pytest.raises(InterruptedError):
        describe_with_ollama(source_video, 'vision', index=index, window_sec=2,
                             frames_per_window=3, max_windows=3, cancelled=lambda: bool(calls))
    assert not index.load(source_video).get('analysis')
    assert not index.load(source_video).get('descriptions')


def test_analysis_version_and_unknown_model_digest_do_not_reuse_cache(source_video, tmp_path, local_vision, monkeypatch):
    import src.core.visual_describer as backend
    from src.core.visual_index import VisualIndex
    identity, calls, _ = local_vision
    index = VisualIndex(str(tmp_path / 'index'))
    options = dict(index=index, window_sec=2, frames_per_window=3, max_windows=3)
    backend.describe_with_ollama(source_video, 'vision', **options)
    monkeypatch.setattr(backend, 'PROMPT_VERSION', 'changed-prompt')
    backend.describe_with_ollama(source_video, 'vision', **options)
    assert len(calls) == 6
    identity['digest'] = None
    backend.describe_with_ollama(source_video, 'vision', **options)
    backend.describe_with_ollama(source_video, 'vision', **options)
    assert len(calls) == 12
    assert index.load(source_video)['analysis']['revision_known'] is False


def test_partial_sampling_is_not_reported_as_complete(source_video, tmp_path, local_vision):
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama
    index = VisualIndex(str(tmp_path / 'index'))
    describe_with_ollama(source_video, 'vision', index=index, window_sec=2, frames_per_window=3, max_windows=1)
    assert index.load(source_video)['analysis']['status'] == 'partial'
    assert index.load(source_video)['coverage']['complete'] is False


def test_changed_source_archives_user_notes_instead_of_applying_to_new_content(source_video, tmp_path):
    import json
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / 'index'))
    index.prepare_windows(source_video)
    index.save_descriptions(source_video, [{'time_sec': 1, 'description': 'User note'}])
    before = index.load(source_video)
    source_video.write_bytes(b'new media')
    index._write(source_video, {'schema_version': 3, 'fingerprint': index._fingerprint(source_video)}, None)
    archives = list((index.cache_dir / 'history').glob('*.json'))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text()) == before


def test_cloud_proxy_or_text_model_rejected_before_any_frame_send(monkeypatch):
    import src.core.visual_describer as backend
    routes = []

    def fake(endpoint, route, payload=None, timeout=30):
        routes.append(route)
        if route == '/api/tags':
            return {'models': [{'name': 'local:latest', 'digest': 'one'}]}
        return {'capabilities': ['completion'], 'remote_host': 'https://cloud.example'}

    monkeypatch.setattr(backend, '_json_request', fake)
    with pytest.raises(ValueError, match='cloud'):
        backend.local_model_info('local', 'http://localhost:11434')
    assert '/api/chat' not in routes


def test_source_changed_during_response_cannot_commit_stale_descriptions(source_video, tmp_path, local_vision):
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama
    index = VisualIndex(str(tmp_path / 'index'))

    def mutate(done, total):
        source_video.touch()

    with pytest.raises(ValueError, match='thay đổi'):
        describe_with_ollama(source_video, 'vision', index=index, window_sec=2, frames_per_window=3,
                             max_windows=3, progress=mutate)


def test_uncertain_window_gets_one_bounded_refinement(source_video, tmp_path, local_vision, monkeypatch):
    import src.core.visual_describer as backend
    import json
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / 'index'))
    original = backend._json_request
    observed = []

    def request(endpoint, route, payload=None, timeout=30):
        result = original(endpoint, route, payload, timeout)
        if route == '/api/chat':
            observed.append(len(payload['messages'][0]['images']))
            if len(observed) == 1:
                parsed = json.loads(result['message']['content'])
                parsed[0]['analysis_status'] = 'needs_review'
                result['message']['content'] = json.dumps(parsed)
        return result

    monkeypatch.setattr(backend, '_json_request', request)
    backend.describe_with_ollama(source_video, 'vision', index=index, window_sec=2,
                                 frames_per_window=3, max_windows=1, max_refinements=1)
    assert len(observed) == 2
    assert observed[0] < observed[1] <= 8
    cached = index.load(source_video)
    assert len(cached['analysis']['windows'][0]['samples']) == observed[1]


def test_action_across_visual_discontinuity_is_sent_to_review(source_video, tmp_path, local_vision, monkeypatch):
    from src.core.visual_index import VisualIndex
    from src.core.visual_describer import describe_with_ollama
    index = VisualIndex(str(tmp_path / 'index'))
    data = index.prepare_windows(source_video, window_sec=2, frames_per_window=3, max_windows=1)
    data['windows'][0]['samples'][-1]['shot_group'] = 1
    index._write(source_video, {k: v for k, v in data.items() if k != 'cache_hit'}, None)
    result = describe_with_ollama(source_video, 'vision', index=index, window_sec=2,
                                  frames_per_window=3, max_windows=1, max_refinements=0)
    assert result[0]['analysis_status'] == 'needs_review'
    assert result[0]['observed_actions'] == []
    assert result[0]['review_reason'] == 'possible_scene_change'


def test_corrupt_cache_and_legacy_machine_notes_are_not_treated_as_verified(source_video, tmp_path):
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / 'index'))
    index.cache_dir.mkdir()
    index._file(source_video, None).write_text('[]')
    assert index.load(source_video) is None
    index._file(source_video, None).write_text('{"schema_version": 3, "fingerprint": NaN}')
    assert index.load(source_video) is None
    index._write(source_video, {
        'schema_version': 2, 'fingerprint': index._fingerprint(source_video),
        'descriptions': [
            {'time_sec': 1, 'description': 'user', 'origin': 'user', 'observed_actions': ['nhấc cốc']},
            {'time_sec': 2, 'description': 'old machine', 'origin': 'local_model:old'},
        ],
    }, None)
    loaded = index.load(source_video)
    assert len(loaded['legacy_descriptions']) == 1
    assert [n['description'] for n in loaded['descriptions']] == ['user']
    assert loaded['descriptions'][0]['analysis_status'] == 'needs_review'
    assert loaded['descriptions'][0]['observed_actions'] == []


def test_malformed_window_cache_is_rebuilt(source_video, tmp_path):
    from src.core.visual_index import VisualIndex
    index = VisualIndex(str(tmp_path / 'index'))
    data = index.prepare_windows(source_video)
    data['windows'] = [{}]
    index._write(source_video, data, None)
    rebuilt = index.prepare_windows(source_video)
    assert rebuilt['cache_hit'] is False
    assert rebuilt['windows'][0]['samples']
