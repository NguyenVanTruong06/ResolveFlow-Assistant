"""Kiểm thử hồi quy cấu hình timeout cho describe_with_ollama và local_model_info.

Đảm bảo:
1. Mặc định timeout inference là 120s (DEFAULT_INFERENCE_TIMEOUT) để đáp ứng cold start mô hình thị giác.
2. Kiểm tra readiness /api/tags và /api/show luôn được giới hạn ngắn (<= 10s).
3. Caller override được bảo toàn chính xác trên lượt gọi /api/chat.
4. Không sử dụng sleep trong unit test.
"""
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.visual_describer import (
    DEFAULT_INFERENCE_TIMEOUT,
    DEFAULT_METADATA_TIMEOUT,
    describe_with_ollama,
    local_model_info,
)
from src.core.visual_index import VisualIndex


def test_default_timeouts_constants():
    assert DEFAULT_INFERENCE_TIMEOUT == 120.0
    assert DEFAULT_METADATA_TIMEOUT == 10.0


def test_local_model_info_default_timeout():
    recorded_timeouts = []

    def mock_urlopen(request, timeout=None):
        recorded_timeouts.append((request.full_url, timeout))
        if "/api/tags" in request.full_url:
            data = {"models": [{"name": "test-model:latest", "capabilities": ["vision"]}]}
        elif "/api/show" in request.full_url:
            data = {"capabilities": ["vision"], "digest": "sha256:abc123"}
        else:
            data = {}
        return io.BytesIO(json.dumps(data).encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        info = local_model_info("test-model", "http://127.0.0.1:11434")
        assert info["name"] == "test-model:latest"
        assert len(recorded_timeouts) == 2
        for url, timeout in recorded_timeouts:
            assert timeout <= 10.0


def test_describe_with_ollama_passes_120s_to_chat_and_10s_to_metadata(tmp_path):
    recorded_calls = []

    def mock_urlopen(request, timeout=None):
        url = request.full_url
        recorded_calls.append((url, timeout))
        if "/api/tags" in url:
            data = {"models": [{"name": "qwen3-vl:2b-instruct", "capabilities": ["vision"]}]}
        elif "/api/show" in url:
            data = {"capabilities": ["vision"], "digest": "sha256:def456"}
        elif "/api/chat" in url:
            data = {
                "message": {
                    "content": json.dumps({
                        "segments": [{
                            "start_sec": 0.0,
                            "end_sec": 8.0,
                            "description": "Thử nghiệm",
                            "objects": ["máy tính"],
                            "observed_actions": [],
                            "evidence_frame_ids": ["F0", "F1"],
                            "analysis_status": "described",
                        }]
                    })
                }
            }
        else:
            data = {}
        return io.BytesIO(json.dumps(data).encode("utf-8"))

    dummy_video = tmp_path / "sample.mp4"
    dummy_video.write_bytes(b"dummy_data_12345")

    img0 = tmp_path / "f0.jpg"
    img1 = tmp_path / "f1.jpg"
    img0.write_bytes(b"fake_jpeg_0")
    img1.write_bytes(b"fake_jpeg_1")

    mock_index = MagicMock(spec=VisualIndex)
    mock_index.prepare_windows.return_value = {
        "fingerprint": "fp123",
        "duration_sec": 8.0,
        "temporal_config": {"window_sec": 8.0, "frames_per_window": 2},
        "windows": [{
            "window_id": "W00000",
            "start_sec": 0.0,
            "end_sec": 8.0,
            "samples": [
                {"time_sec": 1.0, "image_path": str(img0), "shot_group": 0},
                {"time_sec": 5.0, "image_path": str(img1), "shot_group": 0},
            ]
        }],
        "analysis": {},
        "descriptions": [],
        "coverage": {"complete": True, "total_windows": 1, "selected_windows": 1},
    }
    mock_index.load.return_value = {
        "fingerprint": "fp123",
        "temporal_config": {"window_sec": 8.0, "frames_per_window": 2},
        "windows": mock_index.prepare_windows.return_value["windows"],
        "descriptions": [],
    }

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        descs = describe_with_ollama(
            dummy_video,
            model="qwen3-vl:2b-instruct",
            index=mock_index,
        )

    assert len(descs) == 1
    # Check recorded timeouts:
    tags_calls = [t for url, t in recorded_calls if "/api/tags" in url]
    show_calls = [t for url, t in recorded_calls if "/api/show" in url]
    chat_calls = [t for url, t in recorded_calls if "/api/chat" in url]

    assert len(tags_calls) == 1 and tags_calls[0] <= 10.0
    assert len(show_calls) == 1 and show_calls[0] <= 10.0
    assert len(chat_calls) == 1 and chat_calls[0] == 120.0


def test_describe_with_ollama_caller_timeout_override(tmp_path):
    recorded_calls = []

    def mock_urlopen(request, timeout=None):
        url = request.full_url
        recorded_calls.append((url, timeout))
        if "/api/tags" in url:
            data = {"models": [{"name": "qwen3-vl:2b-instruct", "capabilities": ["vision"]}]}
        elif "/api/show" in url:
            data = {"capabilities": ["vision"], "digest": "sha256:def456"}
        elif "/api/chat" in url:
            data = {
                "message": {
                    "content": json.dumps({
                        "segments": [{
                            "start_sec": 0.0,
                            "end_sec": 8.0,
                            "description": "Thử nghiệm override",
                            "objects": [],
                            "observed_actions": [],
                            "evidence_frame_ids": ["F0", "F1"],
                            "analysis_status": "described",
                        }]
                    })
                }
            }
        else:
            data = {}
        return io.BytesIO(json.dumps(data).encode("utf-8"))

    dummy_video = tmp_path / "sample.mp4"
    dummy_video.write_bytes(b"dummy_data_12345")
    img0 = tmp_path / "f0.jpg"
    img1 = tmp_path / "f1.jpg"
    img0.write_bytes(b"fake_jpeg_0")
    img1.write_bytes(b"fake_jpeg_1")

    mock_index = MagicMock(spec=VisualIndex)
    mock_index.prepare_windows.return_value = {
        "fingerprint": "fp123",
        "duration_sec": 8.0,
        "temporal_config": {"window_sec": 8.0, "frames_per_window": 2},
        "windows": [{
            "window_id": "W00000",
            "start_sec": 0.0,
            "end_sec": 8.0,
            "samples": [
                {"time_sec": 1.0, "image_path": str(img0), "shot_group": 0},
                {"time_sec": 5.0, "image_path": str(img1), "shot_group": 0},
            ]
        }],
        "analysis": {},
        "descriptions": [],
        "coverage": {"complete": True, "total_windows": 1, "selected_windows": 1},
    }
    mock_index.load.return_value = {
        "fingerprint": "fp123",
        "temporal_config": {"window_sec": 8.0, "frames_per_window": 2},
        "windows": mock_index.prepare_windows.return_value["windows"],
        "descriptions": [],
    }

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        describe_with_ollama(
            dummy_video,
            model="qwen3-vl:2b-instruct",
            index=mock_index,
            timeout=45.0,
        )

    chat_calls = [t for url, t in recorded_calls if "/api/chat" in url]
    tags_calls = [t for url, t in recorded_calls if "/api/tags" in url]
    assert chat_calls == [45.0]
    assert tags_calls == [10.0]
