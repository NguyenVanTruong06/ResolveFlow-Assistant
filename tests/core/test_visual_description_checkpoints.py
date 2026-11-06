"""Kiểm thử TDD cho lõi cache thị giác độc lập và checkpoint mô tả từng cửa sổ.

Đảm bảo:
1. Độc lập giữa Overview và Temporal stage: scan() rebuild không làm mất windows/analysis/user notes cùng nguồn.
2. Invalidation đúng khi đổi temporal config: xóa model analysis, giữ user notes.
3. Checkpoint mô tả từng cửa sổ (atomic per-window checkpointing) và trạng thái partial.
4. Hủy ngang (cancellation) bảo lưu checkpoint window trước đó, không gọi model sau khi hủy.
5. Tiếp tục (resume) với cùng signature bỏ qua window đã hoàn thành, chỉ xử lý window còn thiếu.
6. Budget-limited partial không gọi lại vô ích và không nâng khống thành complete.
7. Bảo toàn user notes khi commit (kể cả user note tạo giữa chừng).
8. Phát hiện nguồn/config thay đổi trước khi commit và abort an toàn.
9. Không bao giờ coi 0 windows là complete; counters phân tách rõ ràng.
"""

import copy
import hashlib
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.core.visual_index import VisualIndex, VISUAL_SCHEMA_VERSION, ANALYSIS_VERSION
from src.core.visual_describer import describe_with_ollama, PROMPT_VERSION, _local_endpoint


def _create_mock_image(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(path, format="JPEG")
    return path


def _setup_seeded_video(tmp_path: Path, filename: str = "video.mp4", duration: float = 20.0):
    video_path = tmp_path / filename
    video_path.write_bytes(b"dummy_video_bytes_for_testing_1234567890")
    cache_dir = tmp_path / "visual_cache"
    index = VisualIndex(str(cache_dir))
    return video_path, index


# =============================================================================
# 1. KIỂM THỬ TÍNH ĐỘC LẬP OVERVIEW VS TEMPORAL TRÊN CÙNG FINGERPRINT
# =============================================================================

def test_overview_rebuild_preserves_temporal_and_analysis_and_user_notes(tmp_path, monkeypatch):
    """Khi rebuild overview (đổi interval/max_samples hoặc thiếu contact sheet),
    scan() phải giữ nguyên temporal_config, windows, analysis và descriptions đã có."""
    video_path, index = _setup_seeded_video(tmp_path)
    img1 = _create_mock_image(tmp_path / "img1.jpg")
    img2 = _create_mock_image(tmp_path / "img2.jpg")
    cs = _create_mock_image(tmp_path / "cs.jpg")

    fp = index._fingerprint(video_path)
    initial_data = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "fingerprint": fp,
        "duration_sec": 20.0,
        "interval_sec": 10.0,
        "max_samples": 2,
        "status": "described",
        "sampler_version": "pyav-pts-v1",
        "contact_sheet_path": str(cs),
        "samples": [
            {"time_sec": 0.0, "image_path": str(img1)},
            {"time_sec": 10.0, "image_path": str(img2)},
        ],
        "temporal_config": {
            "window_sec": 10.0, "frames_per_window": 2, "max_windows": 10,
            "sampler_version": "pyav-pts-v1"
        },
        "windows": [
            {"window_id": "W00000", "start_sec": 0.0, "end_sec": 10.0, "samples": [{"time_sec": 2.0, "image_path": str(img1)}]},
            {"window_id": "W00001", "start_sec": 10.0, "end_sec": 20.0, "samples": [{"time_sec": 12.0, "image_path": str(img2)}]},
        ],
        "coverage": {"complete": True, "total_windows": 2, "sampled_windows": 2},
        "analysis": {
            "signature": "sig123", "status": "complete", "errors": [],
            "windows": [{"window_id": "W00000", "status": "described"}]
        },
        "descriptions": [
            {"time_sec": 1.0, "description": "Ghi chú của người dùng", "origin": "user"},
            {"start_sec": 0.0, "end_sec": 10.0, "time_sec": 0.0, "description": "Model mô tả W0", "origin": "local_model:qwen", "analysis_status": "described", "evidence_times": [2.0]},
        ]
    }
    initial_data["visual_segments"] = initial_data["descriptions"]
    index._write(video_path, initial_data, None)

    # Giả lập VideoSampler cho scan()
    class MockSampler:
        def __init__(self, path):
            self.duration_sec = 20.0
        def sample(self, target, output, cancelled=None, width=320):
            _create_mock_image(output)
            return {"time_sec": target, "image_path": str(output)}
        def close(self):
            pass

    monkeypatch.setattr("src.core.visual_sampling.VideoSampler", MockSampler)

    # Rebuild overview với interval_sec = 5.0 (thay đổi cấu hình overview)
    res = index.scan(video_path, interval_sec=5.0, max_samples=4)

    assert res["cache_hit"] is False
    assert res["interval_sec"] == 5.0
    # Phải BẢO TOÀN temporal_config, windows, analysis và toàn bộ descriptions
    assert res.get("temporal_config") == initial_data["temporal_config"]
    assert res.get("windows") == initial_data["windows"]
    assert res.get("analysis") == initial_data["analysis"]
    descs = res.get("descriptions", [])
    assert len(descs) == 2
    assert any(d.get("origin") == "user" for d in descs)
    assert any("Model mô tả W0" in d.get("description", "") for d in descs)


def test_temporal_config_change_invalidates_analysis_but_preserves_user_notes(tmp_path, monkeypatch):
    """Khi đổi temporal config qua prepare_windows, analysis cũ bị hủy và model descriptions bị xóa,
    nhưng user notes phải được giữ lại."""
    video_path, index = _setup_seeded_video(tmp_path)
    img1 = _create_mock_image(tmp_path / "img1.jpg")
    fp = index._fingerprint(video_path)

    initial_data = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "fingerprint": fp,
        "duration_sec": 20.0,
        "status": "described",
        "temporal_config": {"window_sec": 10.0, "frames_per_window": 2, "max_windows": 10, "sampler_version": "pyav-pts-v1"},
        "windows": [{"window_id": "W0", "start_sec": 0.0, "end_sec": 10.0, "samples": []}],
        "analysis": {"signature": "sig123", "status": "complete"},
        "descriptions": [
            {"time_sec": 1.0, "description": "User note giữ lại", "origin": "user"},
            {"start_sec": 0.0, "end_sec": 10.0, "time_sec": 0.0, "description": "Model desc bị xóa", "origin": "local_model:qwen", "evidence_times": [1.0]},
        ]
    }
    index._write(video_path, initial_data, None)

    class MockSampler:
        def __init__(self, path):
            self.duration_sec = 20.0
        def sample(self, target, output, cancelled=None, width=640):
            _create_mock_image(output)
            return {"time_sec": target, "image_path": str(output)}
        def close(self):
            pass

    monkeypatch.setattr("src.core.visual_sampling.VideoSampler", MockSampler)

    # Đổi temporal config: window_sec từ 10.0 -> 5.0
    res = index.prepare_windows(video_path, window_sec=5.0, frames_per_window=2, max_windows=10)
    assert res.get("analysis") is None
    descs = res.get("descriptions", [])
    assert len(descs) == 1
    assert descs[0]["description"] == "User note giữ lại"
    assert descs[0]["origin"] == "user"


def test_source_fingerprint_change_clears_generated_observations(tmp_path, monkeypatch):
    """Khi video nguồn bị thay đổi nội dung (fingerprint đổi), load() trả về None
    và scan() không giữ lại quan sát model cũ thuộc nguồn trước."""
    video_path, index = _setup_seeded_video(tmp_path)
    img1 = _create_mock_image(tmp_path / "img1.jpg")
    fp = index._fingerprint(video_path)

    initial_data = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "fingerprint": fp,
        "duration_sec": 20.0,
        "status": "described",
        "descriptions": [{"start_sec": 0.0, "end_sec": 5.0, "time_sec": 0.0, "description": "Old model desc", "origin": "local_model:qwen", "evidence_times": [1.0]}],
    }
    index._write(video_path, initial_data, None)

    # Thay đổi file video nguồn
    video_path.write_bytes(b"completely_new_video_content_modified")
    assert index.load(video_path) is None

    class MockSampler:
        def __init__(self, path):
            self.duration_sec = 30.0
        def sample(self, target, output, cancelled=None, width=320):
            _create_mock_image(output)
            return {"time_sec": target, "image_path": str(output)}
        def close(self):
            pass

    monkeypatch.setattr("src.core.visual_sampling.VideoSampler", MockSampler)
    new_res = index.scan(video_path, interval_sec=10.0)
    assert new_res["descriptions"] == []


# =============================================================================
# 2. KIỂM THỬ PER-WINDOW INCREMENTAL CHECKPOINT & COOPERATIVE CANCEL
# =============================================================================

def _setup_describer_env(tmp_path, monkeypatch, num_windows=2, duration=20.0):
    video_path, index = _setup_seeded_video(tmp_path, "desc_clip.mp4", duration)
    windows = []
    window_sec = duration / num_windows
    for i in range(num_windows):
        start = i * window_sec
        end = (i + 1) * window_sec
        s1 = _create_mock_image(tmp_path / f"w{i}_s1.jpg")
        s2 = _create_mock_image(tmp_path / f"w{i}_s2.jpg")
        samples = [
            {"time_sec": start + 1.0, "image_path": str(s1), "shot_group": 0},
            {"time_sec": start + 2.0, "image_path": str(s2), "shot_group": 0},
        ]
        windows.append({
            "window_id": f"W{i:05d}",
            "start_sec": start,
            "end_sec": end,
            "samples": samples,
        })

    fp = index._fingerprint(video_path)
    config = {"window_sec": window_sec, "frames_per_window": 2, "max_windows": 10, "sampler_version": "pyav-pts-v1"}
    data = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "fingerprint": fp,
        "duration_sec": duration,
        "temporal_config": config,
        "windows": windows,
        "coverage": {
            "complete": True, "total_windows": num_windows, "selected_windows": num_windows,
            "sampled_windows": num_windows, "valid_windows": num_windows, "sampled_ranges": [[w["start_sec"], w["end_sec"]] for w in windows]
        },
        "status": "sampled",
        "descriptions": [],
        "visual_segments": [],
    }
    index._write(video_path, data, None)

    monkeypatch.setattr(index, "prepare_windows", lambda *a, **kw: index.load(video_path))
    monkeypatch.setattr(
        "src.core.visual_describer.local_model_info",
        lambda *a, **kw: {"name": "test-vision-model", "digest": "sha256-fixed-digest-12345", "thinking": False}
    )
    return video_path, index, windows


def test_per_window_incremental_checkpoint_and_partial_status(tmp_path, monkeypatch):
    """Sau khi mỗi window được phân tích và validate, checkpoint phải được ghi ngay xuống đĩa với status partial."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    written_checkpoints = []
    orig_write = index._write

    def spy_write(path, data, content_hash):
        written_checkpoints.append(copy.deepcopy(data))
        return orig_write(path, data, content_hash)

    monkeypatch.setattr(index, "_write", spy_write)

    call_count = 0
    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            w_idx = call_count - 1
            w = windows[w_idx]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"],
                "end_sec": w["end_sec"],
                "description": f"Quan sát cửa sổ {w['window_id']}",
                "objects": ["vật thể"],
                "observed_actions": ["thao tác"],
                "evidence_frame_ids": ["F0", "F1"],
                "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    res = describe_with_ollama(video_path, model="test-vision-model", index=index)
    assert len(res) == 2
    assert call_count == 2

    # Kiểm tra checkpoints đã ghi: ít nhất 2 lần write (sau W0 và sau W1)
    assert len(written_checkpoints) >= 2
    # Checkpoint sau W0 phải có status partial
    ckpt_0 = written_checkpoints[0]
    assert ckpt_0["analysis"]["status"] == "partial"
    assert len(ckpt_0["analysis"]["windows"]) == 1
    assert ckpt_0["analysis"]["windows"][0]["window_id"] == "W00000"
    assert len(ckpt_0["descriptions"]) == 1

    # Checkpoint sau W1 phải là complete
    ckpt_final = written_checkpoints[-1]
    assert ckpt_final["analysis"]["status"] == "complete"
    assert len(ckpt_final["analysis"]["windows"]) == 2
    assert len(ckpt_final["descriptions"]) == 2


def test_cancellation_during_window_2_preserves_window_1_checkpoint(tmp_path, monkeypatch):
    """Khi người dùng hủy trong lúc gọi window 2:
    - InterruptedError được ném ra sạch sẽ.
    - Response của window 2 bị loại bỏ ngay, không publish/commit xuống đĩa.
    - File trên đĩa giữ lại checkpoint partial của window 1 đã hoàn thành trước đó."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    call_count = 0
    cancelled_flag = False

    def is_cancelled():
        return cancelled_flag

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count, cancelled_flag
        if route == "/api/chat":
            call_count += 1
            if call_count == 1:
                # Window 0 hoàn tất bình thường trước khi hủy
                w = windows[0]
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"],
                    "end_sec": w["end_sec"],
                    "description": "Mô tả cửa sổ 0 thành công",
                    "objects": ["cốc"],
                    "observed_actions": ["cầm cốc"],
                    "evidence_frame_ids": ["F0", "F1"],
                    "analysis_status": "described"
                }]})
                return {"message": {"content": content}}
            elif call_count == 2:
                # Cờ hủy bật trong lúc window 1 đang gửi HTTP request
                cancelled_flag = True
                w = windows[1]
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"],
                    "end_sec": w["end_sec"],
                    "description": "Mô tả cửa sổ 1 không được commit",
                    "objects": ["đĩa"],
                    "observed_actions": ["đặt đĩa"],
                    "evidence_frame_ids": ["F0", "F1"],
                    "analysis_status": "described"
                }]})
                return {"message": {"content": content}}
            else:
                raise AssertionError("Không được phép gọi thêm request sau khi đã cancel!")
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, cancelled=is_cancelled)

    assert call_count == 2

    # Kiểm tra metadata trên đĩa: checkpoint partial của W0 vẫn còn nguyên!
    saved = index.load(video_path)
    assert saved is not None
    assert saved["analysis"]["status"] == "partial"
    assert len(saved["analysis"]["windows"]) == 1
    assert saved["analysis"]["windows"][0]["window_id"] == "W00000"
    assert len(saved["descriptions"]) == 1
    assert "Mô tả cửa sổ 0 thành công" in saved["descriptions"][0]["description"]
    # Window 1 tuyệt đối không xuất hiện trên đĩa vì bị hủy trước khi commit
    assert not any("Mô tả cửa sổ 1" in d["description"] for d in saved["descriptions"])


def test_cancel_during_http_discards_response_and_does_not_commit(tmp_path, monkeypatch):
    """Probe 1: Khi cờ hủy bật ngay trong lúc request đầu tiên, response trả về sau đó
    phải bị hủy bỏ hoàn toàn và KHÔNG được gọi index._write."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    cancelled_flag = False
    writes_called = []
    orig_write = index._write

    def spy_write(path, data, content_hash):
        writes_called.append(copy.deepcopy(data))
        return orig_write(path, data, content_hash)

    monkeypatch.setattr(index, "_write", spy_write)

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal cancelled_flag
        if route == "/api/chat":
            # Đặt cancel=True ngay trong request
            cancelled_flag = True
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Response trả sau hủy phải bị bỏ",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, cancelled=lambda: cancelled_flag)

    # Đảm bảo index._write KHÔNG được gọi lần nào sau khi có cờ hủy!
    assert len(writes_called) == 0
    saved = index.load(video_path)
    assert not saved.get("descriptions")


def test_resume_same_signature_skips_completed_window_1_and_only_processes_window_2(tmp_path, monkeypatch):
    """Khi chạy tiếp (resume) với cùng signature:
    - Bỏ qua window 0 đã validate trên đĩa.
    - Chỉ gửi request cho window 1 còn thiếu.
    - Kết thúc đạt complete với cả 2 windows."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    # 1. Bước 1: Giả lập phiên trước bị hủy trong lúc gọi W1 (W0 đã checkpoint xong)
    cancelled_flag = False
    call_count = 0

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count, cancelled_flag
        if route == "/api/chat":
            call_count += 1
            if call_count == 1:
                w = windows[0]
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                    "description": "Quan sát W0 từ phiên 1",
                    "objects": ["bàn"], "observed_actions": ["chạm bàn"],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
                }]})
                return {"message": {"content": content}}
            elif call_count == 2:
                cancelled_flag = True
                return {"message": {"content": "{}"}}
            raise AssertionError("Không nên tới đây ở bước 1")
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)
    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, cancelled=lambda: cancelled_flag)

    # Xác nhận đĩa đã có W0
    saved1 = index.load(video_path)
    assert saved1["analysis"]["status"] == "partial"
    assert len(saved1["descriptions"]) == 1

    # 2. Bước 2: Resume phiên 2 (cancelled_flag = False)
    resume_calls = []

    def mock_json_request_resume(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            body = payload or {}
            user_msg = body["messages"][0]["content"]
            resume_calls.append(user_msg)
            w = windows[1]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Quan sát W1 từ phiên resume",
                "objects": ["ghế"], "observed_actions": ["kéo ghế"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_resume)

    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index)
    # CHỈ có đúng 1 request cho W1!
    assert len(resume_calls) == 1
    assert "W00001" in resume_calls[0]
    assert "W00000" not in resume_calls[0]

    # Kết quả đầy đủ 2 cửa sổ
    assert len(res2) == 2
    assert res2[0]["description"] == "Quan sát W0 từ phiên 1"
    assert res2[1]["description"] == "Quan sát W1 từ phiên resume"

    final_saved = index.load(video_path)
    assert final_saved["analysis"]["status"] == "complete"
    assert len(final_saved["analysis"]["windows"]) == 2


def test_refinement_executes_second_inference_on_refined_samples(tmp_path, monkeypatch):
    """Probe 2: Khi 1 window trả needs_review, hệ thống kích hoạt refinement và
    bắt buộc thực hiện thêm 1 lần inference thứ 2 trên bộ ảnh mẫu mới (refinements_created=1, inference_calls=2)."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    calls = []

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            calls.append(payload)
            w = windows[0]
            if len(calls) == 1:
                # Lần 1: Trả needs_review để kích hoạt refinement
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                    "description": "Cảnh chưa rõ, nghi chuyển cảnh",
                    "objects": [], "observed_actions": [],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
                }]})
                return {"message": {"content": content}}
            else:
                # Lần 2 (Refinement): Nhận bộ frame bổ sung và mô tả thành công
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                    "description": "Cảnh đã rõ sau khi bổ sung khung hình",
                    "objects": ["người", "xe"], "observed_actions": ["bước xuống xe"],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
                }]})
                return {"message": {"content": content}}
        return {}

    # Giả lập refine_window tăng gấp đôi số samples
    def mock_refine_window(path, window, frames_per_window, cancelled=None):
        extra_s1 = _create_mock_image(tmp_path / "extra_1.jpg")
        extra_s2 = _create_mock_image(tmp_path / "extra_2.jpg")
        new_samples = list(window["samples"]) + [
            {"time_sec": window["start_sec"] + 3.0, "image_path": str(extra_s1), "shot_group": 0},
            {"time_sec": window["start_sec"] + 4.0, "image_path": str(extra_s2), "shot_group": 0},
        ]
        return {**window, "samples": new_samples}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)
    monkeypatch.setattr(index, "refine_window", mock_refine_window)

    res = describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=2)

    # Khẳng định probe: ĐÃ có đúng 2 cuộc gọi inference (1 ban đầu + 1 refinement)
    assert len(calls) == 2, f"Kỳ vọng 2 inference calls, thực tế: {len(calls)}"
    assert len(calls[0]["messages"][0]["images"]) == 2
    assert len(calls[1]["messages"][0]["images"]) == 4  # Lần 2 gửi nhiều frame hơn

    # Kết quả cuối cùng phản ánh mô tả sau refinement
    assert len(res) == 1
    assert "Cảnh đã rõ sau khi bổ sung khung hình" in res[0]["description"]

    # Kiểm tra metadata trên đĩa: chỉ có 1 window duy nhất (không bị nhân đôi window_id)
    saved = index.load(video_path)
    assert len(saved["analysis"]["windows"]) == 1
    w_info = saved["analysis"]["windows"][0]
    assert w_info["status"] == "described"
    assert w_info.get("refined") is True


def test_cancel_during_precommit_load_aborts_without_write(tmp_path, monkeypatch):
    """Probe 1 R1: Khi HTTP request đã trả về thành công nhưng cờ hủy bật trong lúc
    load/chuẩn bị dữ liệu precommit, hệ thống phải abort ngay trước index._write và 0 lần ghi đĩa."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    cancelled_flag = False
    writes_called = []
    orig_write = index._write
    orig_load = index.load

    def spy_write(path, data, content_hash):
        writes_called.append(copy.deepcopy(data))
        return orig_write(path, data, content_hash)

    def cancel_on_precommit_load(path, content_hash=None):
        nonlocal cancelled_flag
        # Kích hoạt cờ hủy ngay trong lúc precommit đang load dữ liệu đĩa
        cancelled_flag = True
        return orig_load(path, content_hash)

    monkeypatch.setattr(index, "_write", spy_write)
    monkeypatch.setattr(index, "load", cancel_on_precommit_load)

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Quan sát model trả về hợp lệ",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, cancelled=lambda: cancelled_flag)

    # Khẳng định probe 1: index._write KHÔNG ĐƯỢC GỌI lần nào!
    assert len(writes_called) == 0, f"index._write bị gọi {len(writes_called)} lần dù đã cancel trước write!"


def test_resume_executes_pending_refinement_from_prior_checkpoint(tmp_path, monkeypatch):
    """Probe 2 R1: Khi phiên trước chạy base window 0 trả needs_review, tạo refinement 4 frames,
    checkpoint base đã publish nhưng bị cancel trước khi chạy inference refinement:
    - Khi resume, hệ thống KHÔNG được coi base needs_review là hoàn tất (không shortcut).
    - Hệ thống PHẢI nạp pending_samples đã lưu và thực hiện cuộc gọi inference bổ sung cho refinement.
    - Kết thúc đạt complete với đúng mô tả của refinement."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    # 1. Bước 1: Chạy phiên 1, W0 trả needs_review -> tạo refinement -> cancel ngay sau checkpoint base
    step1_cancelled = False
    step1_calls = []

    extra_s1 = _create_mock_image(tmp_path / "extra_r1_1.jpg")
    extra_s2 = _create_mock_image(tmp_path / "extra_r1_2.jpg")

    def mock_refine_window(path, window, frames_per_window, cancelled=None):
        new_samples = list(window["samples"]) + [
            {"time_sec": window["start_sec"] + 3.0, "image_path": str(extra_s1), "shot_group": 0},
            {"time_sec": window["start_sec"] + 4.0, "image_path": str(extra_s2), "shot_group": 0},
        ]
        return {**window, "samples": new_samples}

    orig_write = index._write
    def write_and_cancel_after_base(path, data, content_hash):
        res = orig_write(path, data, content_hash)
        # Nếu checkpoint base W0 có refinement_pending được commit, kích hoạt cancel ngay
        analysis = data.get("analysis", {})
        win_states = analysis.get("windows", [])
        if win_states and win_states[0].get("refinement_pending"):
            nonlocal step1_cancelled
            step1_cancelled = True
        return res

    monkeypatch.setattr(index, "refine_window", mock_refine_window)
    monkeypatch.setattr(index, "_write", write_and_cancel_after_base)

    def mock_json_request_step1(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            step1_calls.append(payload)
            w = windows[0]
            # Trả needs_review để trigger refinement
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Cảnh chưa rõ ở phiên 1",
                "objects": [], "observed_actions": [],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_step1)

    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1, cancelled=lambda: step1_cancelled)

    # Xác nhận phiên 1 dừng sau đúng 1 inference call
    assert len(step1_calls) == 1
    saved_step1 = index.load(video_path)
    assert saved_step1["analysis"]["status"] == "partial"
    st0 = saved_step1["analysis"]["windows"][0]
    assert st0["refinement_pending"] is True
    assert len(st0["pending_samples"]) == 4

    # 2. Bước 2: Resume phiên 2 (không cancel)
    # Khôi phục index._write bình thường
    monkeypatch.setattr(index, "_write", orig_write)
    step2_calls = []

    def mock_json_request_step2(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            step2_calls.append(payload)
            w = windows[0]
            # Inference của pending refinement thành công
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Cảnh đã rõ ở phiên 2 sau refinement",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_step2)

    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1)

    # KHẲNG ĐỊNH PROBE 2: Phiên 2 KHÔNG bị shortcut! ĐÃ gọi inference cho pending refinement với đủ 4 ảnh!
    assert len(step2_calls) == 1, f"Kỳ vọng 1 refinement call ở phiên resume, thực tế: {len(step2_calls)}"
    assert len(step2_calls[0]["messages"][0]["images"]) == 4  # Đúng 4 ảnh mẫu của refinement

    # Kết quả cuối cùng phản ánh mô tả thành công của refinement
    assert len(res2) == 1
    assert "Cảnh đã rõ ở phiên 2 sau refinement" in res2[0]["description"]

    saved_step2 = index.load(video_path)
    assert saved_step2["analysis"]["status"] == "complete"
    st_final = saved_step2["analysis"]["windows"][0]
    assert st_final["status"] == "described"
    assert st_final.get("refinement_completed") is True
    assert st_final.get("refinement_pending") is False


# =============================================================================
# 3. BUDGET-LIMITED PARTIAL & NO FALSE COMPLETION
# =============================================================================

def test_same_signature_budget_limited_partial_no_re_inference_status_still_partial(tmp_path, monkeypatch):
    """Khi clip có 4 cửa sổ nhưng ngân sách chỉ chọn 2 (budget-limited partial, coverage complete=False):
    - Đã xử lý xong cả 2 cửa sổ được chọn.
    - Gọi lại với cùng signature không thực hiện inference lại vô ích.
    - Status vẫn là partial (không được nâng khống thành complete vì clip chưa được bao phủ 100%)."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2, duration=40.0)

    # Đặt coverage complete = False để đại diện cho budget-limited
    data = index.load(video_path)
    data["coverage"] = {
        "complete": False,  # Chưa bao phủ hết clip
        "total_windows": 4,
        "selected_windows": 2,
        "sampled_windows": 2,
        "valid_windows": 2,
        "sampled_ranges": [[0.0, 20.0], [20.0, 40.0]],
    }
    index._write(video_path, data, None)

    call_count = 0
    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            w = windows[call_count - 1]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": f"Quan sát ngân sách {w['window_id']}",
                "objects": ["vật thể"], "observed_actions": ["hành động"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    # Lần chạy 1: xử lý 2 cửa sổ được chọn
    res1 = describe_with_ollama(video_path, model="test-vision-model", index=index)
    assert len(res1) == 2
    assert call_count == 2
    saved1 = index.load(video_path)
    assert saved1["analysis"]["status"] == "partial"
    assert saved1["analysis"]["coverage"]["complete"] is False

    # Lần chạy 2: cùng signature, tất cả cửa sổ đã chọn đã xong -> không inference lại!
    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index)
    assert len(res2) == 2
    # Số lần gọi API vẫn là 2 (không gọi thêm lần nào)
    assert call_count == 2
    saved2 = index.load(video_path)
    # Vẫn giữ trạng thái partial trung thực
    assert saved2["analysis"]["status"] == "partial"


def test_force_true_reanalyzes_all_windows_while_preserving_user_notes(tmp_path, monkeypatch):
    """Khi force=True:
    - Bắt buộc phân tích lại tất cả các cửa sổ (không dùng cache shortcut).
    - Nhưng user notes đã có vẫn được bảo tồn trong kết quả cuối cùng."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    # Thêm 1 user note vào metadata
    index.save_descriptions(video_path, [{"time_sec": 3.0, "description": "Ghi chú quý giá của user"}])

    call_count = 0
    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": f"Mô tả lần chạy {call_count}",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    # Lần 1
    res1 = describe_with_ollama(video_path, model="test-vision-model", index=index)
    assert call_count == 1
    assert any("Mô tả lần chạy 1" in d["description"] for d in res1)
    assert any("Ghi chú quý giá của user" in d["description"] for d in res1)

    # Lần 2 với force=True
    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index, force=True)
    assert call_count == 2
    assert any("Mô tả lần chạy 2" in d["description"] for d in res2)
    assert not any("Mô tả lần chạy 1" in d["description"] for d in res2)
    # User note vẫn còn nguyên!
    assert any("Ghi chú quý giá của user" in d["description"] for d in res2)


def test_signature_mismatch_does_not_reuse_old_ai_observations(tmp_path, monkeypatch):
    """Khi signature thay đổi (đổi model name hoặc digest):
    - Không tái sử dụng quan sát AI cũ như thể của model mới.
    - Phải gọi model mới để sinh quan sát mới."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    call_count = 0
    current_model = "model-alpha"

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": f"Mô tả từ {current_model}",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)
    monkeypatch.setattr(
        "src.core.visual_describer.local_model_info",
        lambda model, *a, **kw: {"name": model, "digest": f"digest-{model}", "thinking": False}
    )

    # Chạy lần 1 với model-alpha
    res1 = describe_with_ollama(video_path, model="model-alpha", index=index)
    assert call_count == 1
    assert res1[0]["description"] == "Mô tả từ model-alpha"

    # Chạy lần 2 với model-beta (mismatch signature)
    current_model = "model-beta"
    res2 = describe_with_ollama(video_path, model="model-beta", index=index)
    assert call_count == 2
    assert res2[0]["description"] == "Mô tả từ model-beta"


# =============================================================================
# 4. BẢO TOÀN DỮ LIỆU KHI CONCURRENT USER EDIT & SOURCE/CONFIG INTEGRITY GUARD
# =============================================================================

def test_user_note_added_during_inference_not_overwritten_by_commit(tmp_path, monkeypatch):
    """Nếu người dùng thêm ghi chú vào file JSON trong lúc model đang suy luận cửa sổ:
    - Khi commit checkpoint, describer phải nạp lại dữ liệu đĩa và merge user note mới vào.
    - Không được ghi đè làm mất note người dùng."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            # Giả lập người dùng ghi chú giữa chừng trước khi model trả về
            index.save_descriptions(video_path, [{"time_sec": 7.5, "description": "User note chèn giữa chừng"}])
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Quan sát từ model",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    res = describe_with_ollama(video_path, model="test-vision-model", index=index)
    saved = index.load(video_path)
    descs = saved["descriptions"]
    assert any("User note chèn giữa chừng" in d["description"] for d in descs)
    assert any("Quan sát từ model" in d["description"] for d in descs)


def test_source_or_temporal_changed_during_inference_aborts_commit(tmp_path, monkeypatch):
    """Nếu nguồn video hoặc cấu hình temporal bị thay đổi trong lúc model đang suy luận:
    - Describer phát hiện vi phạm ranh giới nguồn/cấu hình.
    - Abort an toàn, ném ValueError, không ghi đè làm hỏng index mới."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=1)

    def mock_json_request(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            # Video nguồn bị thay đổi mtime/size
            video_path.write_bytes(b"tampered_video_content_modified")
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Quan sát model",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    with pytest.raises(ValueError, match="Nguồn hoặc cấu hình đã thay đổi"):
        describe_with_ollama(video_path, model="test-vision-model", index=index)


# =============================================================================
# 5. COVERAGE COUNTERS TRUNG THỰC & PER-WINDOW ERRORS
# =============================================================================

def test_zero_windows_never_marked_complete(tmp_path, monkeypatch):
    """Video rỗng hoặc không có cửa sổ nào không bao giờ được đánh dấu complete."""
    video_path, index = _setup_seeded_video(tmp_path, "empty.mp4", duration=0.0)

    class EmptySampler:
        def __init__(self, path):
            self.duration_sec = 0.0
        def sample(self, *a, **kw):
            return None
        def close(self):
            pass

    monkeypatch.setattr("src.core.visual_sampling.VideoSampler", EmptySampler)
    with pytest.raises(Exception):
        index.prepare_windows(video_path, window_sec=8.0)


def test_window_error_recorded_in_checkpoint_partial_status(tmp_path, monkeypatch):
    """Khi 1 window gặp lỗi (ví dụ format lỗi hoặc timeout), error được ghi nhận,
    window thành công trước đó vẫn được giữ, trạng thái là partial."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    call_count = 0
    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            if call_count == 1:
                w = windows[0]
                content = json.dumps({"segments": [{
                    "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                    "description": "Cửa sổ 0 thành công",
                    "objects": ["vật"], "observed_actions": ["làm"],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
                }]})
                return {"message": {"content": content}}
            else:
                # Cửa sổ 1 trả JSON hỏng
                return {"message": {"content": "invalid json content not a segment"}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    res = describe_with_ollama(video_path, model="test-vision-model", index=index)
    # Vẫn trả về 1 description thành công của W0
    assert len(res) == 1
    assert "Cửa sổ 0 thành công" in res[0]["description"]

    saved = index.load(video_path)
    assert saved["analysis"]["status"] == "partial"
    assert len(saved["analysis"]["errors"]) > 0
    # State của W1 là error
    w1_state = next((w for w in saved["analysis"]["windows"] if w["window_id"] == "W00001"), None)
    assert w1_state is not None
    assert w1_state["status"] == "error"


# =============================================================================
# 6. R3 REGRESSIONS: REFINEMENT BUDGET RESERVATION & PUBLICATION OSERROR ABORT
# =============================================================================

def test_max_refinements_budget_preserved_across_resume_with_pending_refinement(tmp_path, monkeypatch):
    """Probe R3: Khi phiên 1 W0 trả needs_review, sinh pending refinement (max_refinements=1),
    rồi bị hủy sau base checkpoint:
    - Khi resume, ngân sách refinements_reserved phải tính cả slot pending của W0 (refinements_left=0).
    - W0 hoàn tất refinement thành công.
    - W1 tiếp theo cũng trả needs_review nhưng KHÔNG được cấp thêm refinement vì trần ngân sách = 1.
    - Tổng số refinement được cấp trên cả 2 phiên đúng bằng 1, không vượt trần."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    step1_cancelled = False
    step1_calls = []
    refine_calls = []

    extra_s1 = _create_mock_image(tmp_path / "extra_r3_1.jpg")
    extra_s2 = _create_mock_image(tmp_path / "extra_r3_2.jpg")

    def mock_refine_window(path, window, frames_per_window, cancelled=None):
        refine_calls.append(window["window_id"])
        new_samples = list(window["samples"]) + [
            {"time_sec": window["start_sec"] + 3.0, "image_path": str(extra_s1), "shot_group": 0},
            {"time_sec": window["start_sec"] + 4.0, "image_path": str(extra_s2), "shot_group": 0},
        ]
        return {**window, "samples": new_samples}

    orig_write = index._write
    def write_and_cancel_after_w0_base(path, data, content_hash):
        res = orig_write(path, data, content_hash)
        analysis = data.get("analysis", {})
        win_states = analysis.get("windows", [])
        if win_states and win_states[0].get("refinement_pending"):
            nonlocal step1_cancelled
            step1_cancelled = True
        return res

    monkeypatch.setattr(index, "refine_window", mock_refine_window)
    monkeypatch.setattr(index, "_write", write_and_cancel_after_w0_base)

    # 1. Phiên 1: W0 trả needs_review -> sinh pending refinement -> cancel sau base checkpoint
    def mock_json_request_step1(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            step1_calls.append(payload)
            w = windows[0]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": "Cảnh W0 phiên 1 cần review",
                "objects": [], "observed_actions": [],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_step1)

    with pytest.raises(InterruptedError):
        describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1, cancelled=lambda: step1_cancelled)

    assert len(step1_calls) == 1
    assert refine_calls == ["W00000"]
    saved1 = index.load(video_path)
    assert saved1["analysis"]["windows"][0]["refinement_pending"] is True

    # 2. Phiên 2: Resume (max_refinements=1)
    monkeypatch.setattr(index, "_write", orig_write)
    step2_calls = []

    def mock_json_request_step2(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            step2_calls.append(payload)
            body = payload or {}
            # Phân biệt request W0 refinement (4 frames) vs request W1 base (2 frames)
            num_images = len(body.get("messages", [{}])[0].get("images", []))
            if num_images == 4:
                # W0 refinement hoàn thành tốt
                content = json.dumps({"segments": [{
                    "start_sec": windows[0]["start_sec"], "end_sec": windows[0]["end_sec"],
                    "description": "Cảnh W0 rõ ràng sau refinement",
                    "objects": ["vật"], "observed_actions": ["làm"],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
                }]})
            else:
                # W1 base trả needs_review
                content = json.dumps({"segments": [{
                    "start_sec": windows[1]["start_sec"], "end_sec": windows[1]["end_sec"],
                    "description": "Cảnh W1 chưa rõ cần review",
                    "objects": [], "observed_actions": [],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
                }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_step2)

    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1)

    # KHẲNG ĐỊNH:
    # - Phiên 2 gọi 2 inference: W0 pending refinement (4 ảnh) và W1 base (2 ảnh)
    assert len(step2_calls) == 2
    # - refine_window KHÔNG HỀ ĐƯỢC GỌI thêm cho W1!
    assert refine_calls == ["W00000"], f"refine_window bị gọi vượt ngân sách: {refine_calls}"

    saved2 = index.load(video_path)
    assert saved2["analysis"]["status"] == "needs_review"  # Không complete giả
    assert len(saved2["analysis"]["windows"]) == 2  # Đúng 2 source windows
    assert len(saved2["descriptions"]) == 2
    w_states = {w["window_id"]: w for w in saved2["analysis"]["windows"]}
    # W0 hoàn tất refinement
    assert w_states["W00000"]["status"] == "described"
    assert w_states["W00000"]["refinement_completed"] is True
    assert w_states["W00000"]["refinement_pending"] is False
    # W1 giữ trạng thái needs_review, không có refinement nào
    assert w_states["W00001"]["status"] == "needs_review"
    assert w_states["W00001"]["refinement_completed"] is False
    assert w_states["W00001"]["refinement_pending"] is False


def test_commit_checkpoint_oserror_aborts_immediately_without_second_write(tmp_path, monkeypatch):
    """Probe R3: Có checkpoint tốt của W0, khi W1 ghi checkpoint bị lỗi index._write ném OSError:
    - Hệ thống ném thẳng OSError ra caller, KHÔNG nuốt lỗi trong try/except suy luận.
    - index._write tại bước lỗi chỉ bị gọi ĐÚNG 1 LẦN, tuyệt đối không gọi lần 2 để ghi error-checkpoint.
    - Dữ liệu checkpoint tốt trước đó trên đĩa được giữ nguyên 100% (prior JSON bytes không đổi)."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    orig_write = index._write
    w0_committed = False
    faulty_calls = 0

    def spy_and_fault_write(path, data, content_hash):
        nonlocal w0_committed, faulty_calls
        # Checkpoint đầu tiên cho W0 được ghi bình thường để tạo checkpoint tốt
        if not w0_committed:
            res = orig_write(path, data, content_hash)
            w0_committed = True
            return res
        # Khi W1 thử ghi checkpoint, inject lỗi OSError
        faulty_calls += 1
        raise OSError("Lỗi I/O ổ đĩa khi ghi checkpoint W1")

    monkeypatch.setattr(index, "_write", spy_and_fault_write)

    call_count = 0
    def mock_json_request(endpoint, route, payload=None, timeout=30):
        nonlocal call_count
        if route == "/api/chat":
            call_count += 1
            w = windows[call_count - 1]
            content = json.dumps({"segments": [{
                "start_sec": w["start_sec"], "end_sec": w["end_sec"],
                "description": f"Suy luận model thành công cho {w['window_id']}",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request)

    # Chạy W0 và W1 qua describe_with_ollama
    # Phải ném trực tiếp OSError ra ngoài caller khi commit W1
    with pytest.raises(OSError, match="Lỗi I/O ổ đĩa khi ghi checkpoint W1"):
        describe_with_ollama(video_path, model="test-vision-model", index=index)

    # KHẲNG ĐỊNH:
    # 1. Tại boundary lỗi, index._write chỉ bị gọi ĐÚNG 1 LẦN, không thử lại lần 2!
    assert faulty_calls == 1, f"Kỳ vọng index._write tại boundary lỗi gọi 1 lần rồi abort, thực tế gọi: {faulty_calls} lần!"

    # 2. Checkpoint tốt của W0 được bảo toàn nguyên vẹn trên đĩa, không bị ghi đè thành error-checkpoint
    saved = index.load(video_path)
    assert saved is not None
    assert saved["analysis"]["status"] == "partial"
    assert len(saved["analysis"]["windows"]) == 1
    assert saved["analysis"]["windows"][0]["window_id"] == "W00000"
    assert saved["analysis"]["windows"][0]["status"] == "described"
    assert len(saved["descriptions"]) == 1
    assert "W00000" in saved["descriptions"][0]["description"]
    # W1 tuyệt đối không xuất hiện dưới dạng error checkpoint trên đĩa
    assert not any(w.get("window_id") == "W00001" for w in saved["analysis"]["windows"])


def test_refinement_error_preserves_reservation_and_pending_samples_across_resume(tmp_path, monkeypatch):
    """Phase A (I3b): Khi W0 base needs_review, W0 refinement gặp lỗi suy luận (ValueError/bad JSON):
    - Checkpoint lưu W0 status: error, nhưng vẫn bảo tồn refinement_pending: True và pending_samples.
    - W1 needs_review tiếp theo KHÔNG được cấp slot refinement (vì W0 đã giữ slot duy nhất, max_refinements=1).
    - Khi resume, W0 retry thành công, W1 vẫn không được cấp thêm refinement vượt trần.
    - Không decode frame bổ sung vô hạn, tổng distinct refinement reservations <= 1."""
    video_path, index, windows = _setup_describer_env(tmp_path, monkeypatch, num_windows=2)

    extra_s1 = _create_mock_image(tmp_path / "extra_phaseA_1.jpg")
    extra_s2 = _create_mock_image(tmp_path / "extra_phaseA_2.jpg")

    refine_calls = []
    def mock_refine_window(path, window, frames_per_window, cancelled=None):
        refine_calls.append(window["window_id"])
        new_samples = list(window["samples"]) + [
            {"time_sec": window["start_sec"] + 3.0, "image_path": str(extra_s1), "shot_group": 0},
            {"time_sec": window["start_sec"] + 4.0, "image_path": str(extra_s2), "shot_group": 0},
        ]
        return {**window, "samples": new_samples}

    monkeypatch.setattr(index, "refine_window", mock_refine_window)

    # 1. Phiên 1: W0 base needs_review -> sinh W0 refinement (4 frames)
    # W0 refinement gặp lỗi model -> handler bắt lỗi -> ghi checkpoint W0 error nhưng pending
    # W1 base needs_review -> refinements_left == 0 -> không được refine!
    session1_calls = []

    def mock_json_request_s1(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            session1_calls.append(payload)
            body = payload or {}
            num_imgs = len(body.get("messages", [{}])[0].get("images", []))
            if len(session1_calls) == 1:
                # Call 1: W0 base (2 imgs) -> needs_review
                content = json.dumps({"segments": [{
                    "start_sec": windows[0]["start_sec"], "end_sec": windows[0]["end_sec"],
                    "description": "Cảnh W0 phiên 1 cần review",
                    "objects": [], "observed_actions": [],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
                }]})
                return {"message": {"content": content}}
            elif len(session1_calls) == 2:
                # Call 2: W0 refinement (4 imgs) -> lỗi model output hỏng!
                assert num_imgs == 4
                return {"message": {"content": "not valid json {broken"}}
            else:
                # Call 3: W1 base (2 imgs) -> needs_review
                content = json.dumps({"segments": [{
                    "start_sec": windows[1]["start_sec"], "end_sec": windows[1]["end_sec"],
                    "description": "Cảnh W1 phiên 1 cần review",
                    "objects": [], "observed_actions": [],
                    "evidence_frame_ids": ["F0", "F1"], "analysis_status": "needs_review"
                }]})
                return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_s1)

    res1 = describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1)
    # Cả W0 base (needs_review) và W1 base (needs_review) đều có trong descriptions
    assert len(res1) == 2
    assert any("Cảnh W0 phiên 1 cần review" in d["description"] for d in res1)
    assert any("Cảnh W1 phiên 1 cần review" in d["description"] for d in res1)

    # Kiểm tra metadata sau phiên 1:
    saved1 = index.load(video_path)
    w_states1 = {w["window_id"]: w for w in saved1["analysis"]["windows"]}
    # W0 bị error nhưng PHẢI BẢO TỒN refinement_pending và pending_samples:
    assert w_states1["W00000"]["status"] == "error"
    assert w_states1["W00000"]["refinement_pending"] is True
    assert w_states1["W00000"]["refinement_attempted"] is True
    assert len(w_states1["W00000"]["pending_samples"]) == 4

    # W1 needs_review và KHÔNG được cấp refinement:
    assert w_states1["W00001"]["status"] == "needs_review"
    assert w_states1["W00001"]["refinement_pending"] is False
    assert w_states1["W00001"]["refinement_attempted"] is False
    # refine_window chỉ được gọi đúng 1 lần cho W0:
    assert refine_calls == ["W00000"]

    # 2. Phiên 2: Resume (max_refinements=1)
    # W0 pending retry thành công với 4 ảnh mẫu
    session2_calls = []

    def mock_json_request_s2(endpoint, route, payload=None, timeout=30):
        if route == "/api/chat":
            session2_calls.append(payload)
            body = payload or {}
            num_imgs = len(body.get("messages", [{}])[0].get("images", []))
            # W0 pending refinement retry
            assert num_imgs == 4
            content = json.dumps({"segments": [{
                "start_sec": windows[0]["start_sec"], "end_sec": windows[0]["end_sec"],
                "description": "Cảnh W0 phiên 2 đã rõ sau refinement retry",
                "objects": ["vật"], "observed_actions": ["làm"],
                "evidence_frame_ids": ["F0", "F1"], "analysis_status": "described"
            }]})
            return {"message": {"content": content}}
        return {}

    monkeypatch.setattr("src.core.visual_describer._json_request", mock_json_request_s2)

    res2 = describe_with_ollama(video_path, model="test-vision-model", index=index, max_refinements=1)

    # KHẲNG ĐỊNH:
    # - Phiên 2 chỉ gọi đúng 1 inference cho W0 pending refinement retry (W1 đã xong từ phiên 1 không chạy lại)
    assert len(session2_calls) == 1
    # - refine_window KHÔNG bị gọi thêm lần nào (giữ nguyên chỉ 1 lần từ phiên 1)
    assert refine_calls == ["W00000"]

    saved2 = index.load(video_path)
    w_states2 = {w["window_id"]: w for w in saved2["analysis"]["windows"]}
    assert w_states2["W00000"]["status"] == "described"
    assert w_states2["W00000"]["refinement_completed"] is True
    assert w_states2["W00000"]["refinement_pending"] is False

    assert w_states2["W00001"]["status"] == "needs_review"
    assert w_states2["W00001"]["refinement_completed"] is False
    assert w_states2["W00001"]["refinement_pending"] is False

    # Cả 2 window đều có description
    assert len(res2) == 2
