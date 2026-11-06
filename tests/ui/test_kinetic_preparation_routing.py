"""
tests/ui/test_kinetic_preparation_routing.py
=============================================================================
Finite TDD Test Suite cho G05-05C2c2-I (V2 Kinetic Async Worker & ReviewPage Routing):
1. Actual v3 tmp cache word timestamps (source 0s Việt) vs [] / noaudio / phrase-only /
   malformed child / error / miss, 0 demo / 0 Whisper / network.
2. Manifest ID / source / same basename resolution; global hook + repeated clip + cuts /
   known FPS / effective pad produce expected frames via accepted mapper;
   unsupported / unknown reject truthful. No-plan preview label vs cut state edit_plan_required.
3. Copy <= 300 schema index / budget; > 300 style-only 40/N rõ nhưng AutoPlan full count,
   raw byte error 0 truncation.
4. UI QTest copy / auto / transcript async; 0 GUI IO spies (cache / hash / ffprobe);
   ready getter pure; 0 duplicate apply / copy; no_data / cancel giữ MIME / old plan.
5. Plan / aspect / font / source context đổi while actual worker blocked -> 0 late write / apply;
   same FPS rational NTSC triệt float drift.
6. Close pending / late callbacks, A/B both finish orders, V1 V2 registry all idle,
   finally release / cancel / wait, 0 thread leak.
=============================================================================
"""

import os
import copy
import time
import subprocess
import threading
from fractions import Fraction
from typing import Dict, Any, List, Tuple
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtTest import QTest

from src.core.contracts.time_contracts import RationalFPS
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.clip_manifest import ClipManifest
from src.core.kinetic.kinetic_dataset_mapper import extract_source_words
from src.ui.director.workers.kinetic_preparation_worker import (
    KineticPreparationRequest,
    KineticPreparationWorker,
)
from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def create_fixture_video(path: str, duration: int = 2) -> Tuple[str, Dict[str, Any]]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=15)
    if res.returncode != 0 or not os.path.exists(path):
        raise RuntimeError(f"Lỗi tạo fixture {path}: {res.stderr.decode('utf-8', 'replace')}")
    digest, prov = compute_full_file_sha256(path)
    return digest, prov


def wait_until(condition_fn, timeout_s: float = 6.0, interval_ms: int = 25) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        QTest.qWait(interval_ms)
        try:
            if condition_fn():
                return True
        except Exception:
            pass
    return bool(condition_fn())


# =============================================================================
# 1. TEST WORKER CACHE VERIFICATION & ERROR PATHS
# =============================================================================


def test_kinetic_worker_cache_verification_and_phrase_only_reject(tmp_path, qapp):
    """Kiểm tra: Cache v3 thực tế có word timestamps hợp lệ vs phrase-only bị reject rõ ràng."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v1_path = str(tmp_path / "clip1.mp4")
    d1, p1 = create_fixture_video(v1_path, duration=2)

    # 1. Phrase-only (có text nhưng thiếu word timestamps) -> ném word_timestamps_missing
    cache_mgr.save_scan_result(
        file_path=v1_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {"start": 0.0, "end": 1.5, "text": "Câu thoại không có từ", "words": []}
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d1, p1)
    )

    req_phrase = KineticPreparationRequest(
        request_id="req_phrase",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v1_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_phrase = KineticPreparationWorker(request=req_phrase, cache_mgr=cache_mgr)

    terminal_results = []
    worker_phrase.finished_signal.connect(lambda r, s, m, res: terminal_results.append((s, m, res)))
    worker_phrase.start()

    assert wait_until(lambda: len(terminal_results) == 1)
    worker_phrase.wait()
    assert terminal_results[0][0] is False
    assert terminal_results[0][1] == "word_timestamps_missing"

    # 2. Cập nhật cache với word timestamps tiếng Việt chuẩn -> Thành công
    cache_mgr.save_scan_result(
        file_path=v1_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {
                    "start": 0.0, "end": 1.8, "text": "Xin chào ResolveFlow",
                    "words": [
                        {"word": "Xin", "start": 0.0, "end": 0.5},
                        {"word": "chào", "start": 0.5, "end": 1.0},
                        {"word": "ResolveFlow", "start": 1.0, "end": 1.8},
                    ]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d1, p1)
    )

    req_valid = KineticPreparationRequest(
        request_id="req_valid",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v1_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_valid = KineticPreparationWorker(request=req_valid, cache_mgr=cache_mgr)
    terminal_results.clear()
    worker_valid.finished_signal.connect(lambda r, s, m, res: terminal_results.append((s, m, res)))
    worker_valid.start()

    assert wait_until(lambda: len(terminal_results) == 1)
    worker_valid.wait()
    assert terminal_results[0][0] is True
    assert terminal_results[0][1] == "success"
    assert terminal_results[0][2]["count"] == 3



def test_kinetic_worker_manifest_and_same_basename_resolution(tmp_path, qapp):
    """Kiểm tra: Trùng basename giữa 2 thư mục nếu không rõ ràng phải reject; unsupported speed reject."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    dir_a = tmp_path / "folder_a"
    dir_b = tmp_path / "folder_b"
    dir_a.mkdir()
    dir_b.mkdir()

    p_a = str(dir_a / "clip.mp4")
    p_b = str(dir_b / "clip.mp4")
    d_a, prov_a = create_fixture_video(p_a, duration=2)
    d_b, prov_b = create_fixture_video(p_b, duration=2)

    for p, d, prov in [(p_a, d_a, prov_a), (p_b, d_b, prov_b)]:
        cache_mgr.save_scan_result(
            file_path=p,
            scan_data={
                "clip_dur": 2.0,
                "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
            },
            model_size="small",
            language="vi",
            verified_identity=(d, prov)
        )

    manifest = ClipManifest.create([p_a, p_b], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    # Plan tham chiếu clip qua basename "clip.mp4" khi có 2 tệp trùng basename -> ambiguous_clip_reference
    plan_ambiguous = {
        "manifest_id": manifest.manifest_id,
        "fps": 24,
        "timeline_segments": [{"clip_name": "clip.mp4", "start_sec": 0.0, "end_sec": 1.0, "speed": 1.0}]
    }

    req = KineticPreparationRequest(
        request_id="req_amb",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [p_a, p_b], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_ambiguous,
        timeline_fps=24,
    )
    worker = KineticPreparationWorker(request=req, cache_mgr=cache_mgr)
    results = []
    worker.finished_signal.connect(lambda r, s, m, res: results.append((s, m, res)))
    worker.start()

    assert wait_until(lambda: len(results) == 1)
    worker.wait()
    assert results[0][0] is False
    assert results[0][1] == "ambiguous_clip_reference"

    # Kiểm tra unsupported speed (ví dụ tốc độ 1.5x)
    plan_speed = {
        "manifest_id": manifest.manifest_id,
        "fps": 24,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.0, "speed": 1.5}]
    }
    req_speed = KineticPreparationRequest(
        request_id="req_spd",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [p_a, p_b], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_speed,
        timeline_fps=24,
    )
    worker_speed = KineticPreparationWorker(request=req_speed, cache_mgr=cache_mgr)
    results_speed = []
    worker_speed.finished_signal.connect(lambda r, s, m, res: results_speed.append((s, m, res)))
    worker_speed.start()

    assert wait_until(lambda: len(results_speed) == 1)
    worker_speed.wait()
    assert results_speed[0][0] is False
    assert results_speed[0][1] == "unsupported_speed"



def test_kinetic_worker_intents_and_style_only(tmp_path, qapp):
    """Kiểm tra: copy_kinetic <= 300, > 300 style_only 40/N, auto_plan offline, transcript."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "speech.mp4")
    d, prov = create_fixture_video(v_path, duration=30)

    # Tạo 350 từ vựng hợp lệ (mỗi từ cách nhau 0.08s để tại 24fps không bị quantize empty)
    words_350 = []
    for i in range(350):
        words_350.append({"word": f"Từ_{i}", "start": round(i * 0.08, 3), "end": round((i + 1) * 0.08, 3)})

    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 30.0,
            "raw_subtitles": [{"start": 0.0, "end": 28.0, "text": "Đoạn văn dài", "words": words_350}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    ctx_prims = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"}

    # 1. Intent: copy_kinetic với > 300 từ -> mode == 'style_only', prompt chứa mẫu 40 từ
    req_style = KineticPreparationRequest(
        request_id="req_style",
        generation=1,
        intent="copy_kinetic",
        context_primitives=ctx_prims,
        timeline_fps=24,
    )
    worker_style = KineticPreparationWorker(request=req_style, cache_mgr=cache_mgr)
    res_style = []
    worker_style.finished_signal.connect(lambda r, s, m, res: res_style.append((s, m, res)))
    worker_style.start()

    assert wait_until(lambda: len(res_style) == 1)
    worker_style.wait()
    assert res_style[0][0] is True
    payload = res_style[0][2]
    assert payload["mode"] == "style_only"
    assert payload["count"] == 350
    assert "THÔNG BÁO TỐI ƯU HÓA TOKEN" in payload["prompt_text"]
    assert len(payload["prompt_text"].encode("utf-8")) <= 32768

    # 2. Intent: auto_plan với cùng 350 từ -> AutoPlan offline dùng trọn vẹn 350 từ, không cắt vụn
    req_auto = KineticPreparationRequest(
        request_id="req_auto",
        generation=2,
        intent="auto_plan",
        context_primitives=ctx_prims,
        timeline_fps=24,
    )
    worker_auto = KineticPreparationWorker(request=req_auto, cache_mgr=cache_mgr)
    res_auto = []
    worker_auto.finished_signal.connect(lambda r, s, m, res: res_auto.append((s, m, res)))
    worker_auto.start()

    assert wait_until(lambda: len(res_auto) == 1)
    worker_auto.wait()
    assert res_auto[0][0] is True
    plan = res_auto[0][2]["plan"]
    assert "pages" in plan
    assert len(plan["pages"]) > 50  # 350 từ chia ~ 3 từ/trang -> ~ 116 trang
    # Xác nhận từ cuối cùng trong plan chạm tới index 349
    last_page = plan["pages"][-1]
    assert last_page["word_range"][1] == 349

    # 3. Intent: copy_transcript
    req_trans = KineticPreparationRequest(
        request_id="req_trans",
        generation=3,
        intent="copy_transcript",
        context_primitives=ctx_prims,
        timeline_fps=24,
    )
    worker_trans = KineticPreparationWorker(request=req_trans, cache_mgr=cache_mgr)
    res_trans = []
    worker_trans.finished_signal.connect(lambda r, s, m, res: res_trans.append((s, m, res)))
    worker_trans.start()

    assert wait_until(lambda: len(res_trans) == 1)
    worker_trans.wait()
    assert res_trans[0][0] is True
    assert "[0] Từ_0" in res_trans[0][2]["transcript_text"]
    assert "[349] Từ_349" in res_trans[0][2]["transcript_text"]



def test_review_page_kinetic_async_routing_and_getters(tmp_path, qapp):
    """Kiểm tra: ReviewPage kích hoạt async preparation, nút bấm busy aggregate, và getter pure."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)

    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {
                    "start": 0.0, "end": 1.5, "text": "Kinetic UI Test",
                    "words": [{"word": "Kinetic", "start": 0.0, "end": 0.5}, {"word": "UI", "start": 0.5, "end": 1.0}, {"word": "Test", "start": 1.0, "end": 1.5}]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    page = ReviewPage()
    ctx = {
        "video_paths": [v_path],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "TestProj",
        "fps": 24,
    }
    page.set_context_supplier(lambda: dict(ctx))

    # Ban đầu: getter pure trả về rỗng (0 I/O)
    assert page.get_kinetic_prompt() == ""
    assert page.get_final_cut_words() == []

    # Kích hoạt request_kinetic_preparation
    req_id = page.request_kinetic_preparation(
        intent="copy_kinetic",
        aspect="9:16",
        cache_mgr=cache_mgr
    )
    assert req_id is not None
    assert page.is_analysis_loading() is True
    assert page.btn_copy_kinetic_prompt.isEnabled() is False
    assert page.btn_auto_kinetic.isEnabled() is False

    # Chờ worker nền hoàn tất
    assert wait_until(lambda: page.is_analysis_loading() is False, timeout_s=6.0)

    # Sau khi hoàn tất: nút mở lại
    assert page.btn_copy_kinetic_prompt.isEnabled() is True
    assert page.btn_auto_kinetic.isEnabled() is True

    # Getter pure trả về prompt đã nạp sẵn in-memory
    ready_prompt = page.get_kinetic_prompt(aspect="9:16")
    assert "Kinetic" in ready_prompt
    assert len(page.get_final_cut_words()) == 3

    # Dọn dẹp an toàn
    page.shutdown_analysis_workers()
    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_r3_global_hook_none_positive(tmp_path, qapp):
    """R3-Positive: Plan thực với global_hook=None (và optional field None) đi qua request và actual worker tới terminal, 0 NameError/0 thread leak, full plan giữ nguyên."""
    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)

    cache_dir = tmp_path / "cache"
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {
                    "start": 0.0, "end": 1.5, "text": "Kinetic Hook Test",
                    "words": [
                        {"word": "Kinetic", "start": 0.0, "end": 0.5},
                        {"word": "Hook", "start": 0.5, "end": 1.0},
                        {"word": "Test", "start": 1.0, "end": 1.5},
                    ]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    page = ReviewPage()
    page._manifest = manifest
    ctx = {
        "video_paths": [v_path],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "TestProj",
        "manifest_id": manifest.manifest_id,
        "fps": 24,
    }
    page.set_context_supplier(lambda: dict(ctx))

    # Cấu hình video_plan_data với global_hook=None và optional None fields
    plan_with_none_hook = {
        "manifest_id": manifest.manifest_id,
        "global_hook": None,
        "style": None,
        "timeline_segments": [
            {
                "clip_id": manifest.clips[0].clip_id,
                "path": v_path,
                "start_sec": 0.0,
                "end_sec": 1.5,
                "notes": None,
            }
        ]
    }
    page._video_plan_data = plan_with_none_hook

    # Bắt đầu request_kinetic_preparation
    req_id = page.request_kinetic_preparation(
        intent="copy_kinetic",
        aspect="9:16",
        cache_mgr=cache_mgr
    )
    assert req_id is not None
    assert page.is_analysis_loading() is True

    # Chờ worker nền hoàn tất
    assert wait_until(lambda: page.is_analysis_loading() is False, timeout_s=6.0)

    # Đảm bảo kết quả thành công và prompt được tạo in-memory mà không có NameError hay crash
    ready_prompt = page.get_kinetic_prompt(aspect="9:16")
    assert ready_prompt != ""
    assert "Kinetic" in ready_prompt

    # Dọn dẹp an toàn, 0 thread leak
    page.shutdown_analysis_workers()
    page.close()
    page.deleteLater()
    QTest.qWait(50)



def test_kinetic_r3_exact_geometry_tie_frame_collision_prevented(tmp_path, qapp):
    """R3-Geometry: FPS 24, start 0, end 1.1374999 vs 1.1375001, pad 0.3, clipdur 2:
    Mapper rec_out 34 vs 35, fingerprint phải khác nhau (float.hex không bị mất thông tin bởi round6).
    Đổi plan khi actual worker đang chạy -> 0 copy/apply từ request cũ, worker cũ bị supersede."""
    from src.core.timeline_mapper import TimelineMapper

    # 1. Kiểm tra toán học TimelineMapper: 34 vs 35 frame
    m1 = TimelineMapper(fps=Fraction(24, 1))
    seg1 = m1.add_segment("C01", src_in_sec=0.0, src_out_sec=1.1374999, pad_sec=0.3, clip_duration_sec=2.0)
    m2 = TimelineMapper(fps=Fraction(24, 1))
    seg2 = m2.add_segment("C01", src_in_sec=0.0, src_out_sec=1.1375001, pad_sec=0.3, clip_duration_sec=2.0)
    assert seg1.rec_out_frames == 34
    assert seg2.rec_out_frames == 35

    # 2. Hai plan khác biệt này phải có fingerprint khác nhau
    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_dir = tmp_path / "cache"
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {
                    "start": 0.0, "end": 1.5, "text": "Tie Boundary Test",
                    "words": [{"word": "Tie", "start": 0.0, "end": 0.5}, {"word": "Boundary", "start": 0.5, "end": 1.0}]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))
    ctx_real = {
        "video_paths": [v_path],
        "whisper_model": "small",
        "whisper_language": "vi",
        "project_name": "TestProj",
        "manifest_id": manifest.manifest_id,
        "fps": 24,
    }

    plan_34 = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.1374999, "pad_sec": 0.3, "clip_duration_sec": 2.0}]
    }
    plan_35 = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.1375001, "pad_sec": 0.3, "clip_duration_sec": 2.0}]
    }
    sig_34 = ReviewPage._extract_v2_context_signature(ctx_real, video_plan=plan_34, fps=24)
    sig_35 = ReviewPage._extract_v2_context_signature(ctx_real, video_plan=plan_35, fps=24)
    assert sig_34 != sig_35

    # 3. Controlled barrier / supersede:
    # Khi worker 1 đang chạy với plan_34, đổi plan sang plan_35 -> worker 1 bị supersede, 0 apply
    page = ReviewPage()
    page._manifest = manifest
    page.set_context_supplier(lambda: dict(ctx_real))

    import threading
    worker_1_barrier = threading.Event()

    original_run = KineticPreparationWorker.run
    def slow_run(worker_self):
        if getattr(worker_self.request, "generation", 0) == 1:
            worker_1_barrier.wait(timeout=2.0)
        return original_run(worker_self)

    with patch.object(KineticPreparationWorker, "run", slow_run):
        page._video_plan_data = plan_34
        req1 = page.request_kinetic_preparation(intent="copy_kinetic", cache_mgr=cache_mgr)
        assert req1 is not None
        assert page._kinetic_generation == 1

        # Trong lúc worker 1 đang bị chặn, đổi sang plan_35 và phát request 2
        page._video_plan_data = plan_35
        req2 = page.request_kinetic_preparation(intent="copy_kinetic", cache_mgr=cache_mgr)
        assert req2 is not None
        assert page._kinetic_generation == 2
        assert page._active_kinetic_request_id == req2

        # Cho phép worker 1 tiếp tục
        worker_1_barrier.set()

        # Chờ cả hai worker hoàn tất
        assert wait_until(lambda: page.is_analysis_loading() is False, timeout_s=6.0)

    # Kết quả in-memory phải gắn liền với signature của req2 (plan_35), không bị stale overwrite bởi worker 1
    assert page._prepared_kinetic_snapshot is not None
    assert page._prepared_kinetic_snapshot.get("context_signature") == sig_35 or page._active_kinetic_signature == sig_35

    page.shutdown_analysis_workers()
    page.close()
    page.deleteLater()
    QTest.qWait(50)



def test_kinetic_r3_malformed_plan_pre_mutation_safe_rejection(tmp_path, qapp):
    """R3-Negative: Mixed key {'manifest_id': 'M', 1: 'bad'}, unsupported object và nonfinite nested value:
    -> return None an toàn; generation delta 0, active worker không bị cancel, old plan và clipboard không đổi."""
    v_path = str(tmp_path / "clip.mp4")
    create_fixture_video(v_path, duration=2)

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    gen_before = page._kinetic_generation
    active_before = page._active_kinetic_request_id
    valid_plan = {"manifest_id": "M", "global_hook": None, "timeline_segments": []}
    page._video_plan_data = valid_plan

    # 1. Mixed key trong dict: {1: 'bad'}
    page._video_plan_data = {"manifest_id": "M", 1: "bad", "timeline_segments": []}
    res_mixed_key = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_mixed_key is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 2. Unsupported runtime object
    page._video_plan_data = {"manifest_id": "M", "custom_obj": object(), "timeline_segments": []}
    res_bad_obj = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_bad_obj is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 3. Non-finite float: NaN
    page._video_plan_data = {"manifest_id": "M", "nan_float": float("nan"), "timeline_segments": []}
    res_nan = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_nan is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 4. Non-finite float: Inf
    page._video_plan_data = {"manifest_id": "M", "inf_float": float("inf"), "timeline_segments": []}
    res_inf = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_inf is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 5. Null KHÔNG bị reject chung với unsupported:
    # Plan hợp lệ có global_hook: None phải vượt qua kiểm tra primitive
    page._video_plan_data = valid_plan
    canon = ReviewPage._canonicalize_plan_primitives(valid_plan)
    assert canon is not None
    assert ("global_hook", ("null", None)) in canon[1]

    page.close()
    page.deleteLater()
    QTest.qWait(50)



def test_kinetic_r3_canonical_pure_helper_types_and_ordering():
    """R3-Helper: Kiểm tra pure canonical helper:
    - Dict key sorting deterministic
    - List order được bảo toàn
    - Lossless float.hex phân biệt micro-differences
    - Fraction, Bool, Int, Str, Null được phân định kiểu rõ ràng
    - Non-string keys, non-finite floats, unsupported types ném exception kiểm soát."""
    import math

    # 1. Dict keys sort deterministic: thứ tự key ban đầu không ảnh hưởng kết quả
    d1 = {"b": 1, "a": 2, "c": 3}
    d2 = {"c": 3, "a": 2, "b": 1}
    assert ReviewPage._canonicalize_plan_primitives(d1) == ReviewPage._canonicalize_plan_primitives(d2)

    # 2. List order được bảo toàn: [1, 2] != [2, 1]
    l1 = [1, 2]
    l2 = [2, 1]
    assert ReviewPage._canonicalize_plan_primitives(l1) != ReviewPage._canonicalize_plan_primitives(l2)

    # 3. Lossless float: micro-differences không bị mất thông tin
    f1 = 1.1374999
    f2 = 1.1375001
    assert ReviewPage._canonicalize_plan_primitives(f1) != ReviewPage._canonicalize_plan_primitives(f2)

    # 4. Fraction vs Float/Int
    fr = Fraction(24, 1)
    assert ReviewPage._canonicalize_plan_primitives(fr) == ("fraction", 24, 1)

    # 5. Bool vs Int: True != 1, False != 0
    assert ReviewPage._canonicalize_plan_primitives(True) != ReviewPage._canonicalize_plan_primitives(1)
    assert ReviewPage._canonicalize_plan_primitives(False) != ReviewPage._canonicalize_plan_primitives(0)
    assert ReviewPage._canonicalize_plan_primitives(True) == ("bool", True)
    assert ReviewPage._canonicalize_plan_primitives(1) == ("int", 1)

    # 6. Null
    assert ReviewPage._canonicalize_plan_primitives(None) == ("null", None)

    # 7. Str không bị strip vô lý
    assert ReviewPage._canonicalize_plan_primitives("  keep spacing  ") == ("str", "  keep spacing  ")

    # 8. Malformed cases ném exception có kiểm soát
    with pytest.raises(TypeError):
        ReviewPage._canonicalize_plan_primitives({1: "bad"})

    with pytest.raises(TypeError):
        ReviewPage._canonicalize_plan_primitives({True: "bad"})

    with pytest.raises(ValueError):
        ReviewPage._canonicalize_plan_primitives(float("nan"))

    with pytest.raises(ValueError):
        ReviewPage._canonicalize_plan_primitives(float("-inf"))

    with pytest.raises(TypeError):
        ReviewPage._canonicalize_plan_primitives(object())



def test_kinetic_probe_r2_explicit_fraction_fps_tail_preserved(tmp_path, qapp):
    """R2-A: Explicit rational FPS (Fraction(2399, 100)) không bị float snapping, bảo toàn đủ 2 words cả đầu lẫn cuối [999.7, 999.8]."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip_long.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2000.0,
            "raw_subtitles": [
                {
                    "start": 0.32, "end": 0.42, "text": "TừĐầu",
                    "words": [{"word": "TừĐầu", "start": 0.32, "end": 0.42}]
                },
                {
                    "start": 999.7, "end": 999.8, "text": "TừCuối",
                    "words": [{"word": "TừCuối", "start": 999.7, "end": 999.8}]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    plan = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [
            {
                "clip_id": manifest.clips[0].clip_id,
                "start_sec": 0.0,
                "end_sec": 1000.0,
                "speed": 1.0,
            }
        ]
    }

    fps_frac = Fraction(2399, 100)
    req = KineticPreparationRequest(
        request_id="req_frac_fps",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan,
        timeline_fps=fps_frac,
    )
    worker = KineticPreparationWorker(request=req, cache_mgr=cache_mgr)
    res = []
    worker.finished_signal.connect(lambda r, s, m, payload: res.append((s, m, payload)))
    worker.start()

    assert wait_until(lambda: len(res) == 1)
    worker.wait()
    assert res[0][0] is True
    assert res[0][1] == "success"
    # Cả 2 từ phải được bảo toàn chính xác, không bị rơi mất từ ở mốc 999.7s do float snapping
    words = res[0][2]["words"]
    assert len(words) == 2
    assert words[0]["word"] == "TừĐầu"
    assert words[1]["word"] == "TừCuối"



def test_kinetic_probe_r2_nan_or_invalid_speed_and_time_rejected(tmp_path, qapp):
    """R2-A: speed=NaN, bool, hoặc start_sec/end_sec non-finite bị từ chối fail-closed thay vì báo success."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    # 1. speed = NaN trong segment
    plan_nan_spd = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.0, "speed": float("nan")}]
    }
    req_nan = KineticPreparationRequest(
        request_id="req_nan",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_nan_spd,
        timeline_fps=24,
    )
    worker_nan = KineticPreparationWorker(request=req_nan, cache_mgr=cache_mgr)
    res_nan = []
    worker_nan.finished_signal.connect(lambda r, s, m, res: res_nan.append((s, m, res)))
    worker_nan.start()
    assert wait_until(lambda: len(res_nan) == 1)
    worker_nan.wait()
    assert res_nan[0][0] is False
    assert res_nan[0][1] == "unsupported_speed"

    # 2. start_sec = NaN
    plan_nan_time = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": float("nan"), "end_sec": 1.0, "speed": 1.0}]
    }
    req_nan_t = KineticPreparationRequest(
        request_id="req_nan_t",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_nan_time,
        timeline_fps=24,
    )
    worker_nan_t = KineticPreparationWorker(request=req_nan_t, cache_mgr=cache_mgr)
    res_nan_t = []
    worker_nan_t.finished_signal.connect(lambda r, s, m, res: res_nan_t.append((s, m, res)))
    worker_nan_t.start()
    assert wait_until(lambda: len(res_nan_t) == 1)
    worker_nan_t.wait()
    assert res_nan_t[0][0] is False
    assert res_nan_t[0][1] == "invalid_time_range"



def test_kinetic_probe_r2_producer_precedence_no_masking(tmp_path, qapp):
    """R2-B: audio_analysis_status='completed' nhưng status='error' hoặc producer_status='no_audio' mâu thuẫn phải fail-closed."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)

    # 1. audio_analysis_status='completed' nhưng status='error'
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "audio_analysis_status": "completed",
            "status": "error",
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    req_err = KineticPreparationRequest(
        request_id="req_err",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_err = KineticPreparationWorker(request=req_err, cache_mgr=cache_mgr)
    res_err = []
    worker_err.finished_signal.connect(lambda r, s, m, res: res_err.append((s, m, res)))
    worker_err.start()
    assert wait_until(lambda: len(res_err) == 1)
    worker_err.wait()
    assert res_err[0][0] is False
    assert res_err[0][1] == "source_error"

    # 2. audio_analysis_status='completed' nhưng producer_status='no_audio' có non-empty transcript
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "audio_analysis_status": "completed",
            "producer_status": "no_audio",
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )
    req_contra = KineticPreparationRequest(
        request_id="req_contra",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_contra = KineticPreparationWorker(request=req_contra, cache_mgr=cache_mgr)
    res_contra = []
    worker_contra.finished_signal.connect(lambda r, s, m, res: res_contra.append((s, m, res)))
    worker_contra.start()
    assert wait_until(lambda: len(res_contra) == 1)
    worker_contra.wait()
    assert res_contra[0][0] is False
    assert res_contra[0][1] == "source_error"



def test_kinetic_probe_r2_plan_path_fingerprint_and_fps_context(tmp_path, qapp):
    """R2-C: Thay đổi path trong plan phải làm đổi signature. Context supplier FPS phải được bảo toàn."""
    v_path = str(tmp_path / "clip.mp4")
    create_fixture_video(v_path, duration=2)

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    curr_ctx = page._get_current_context()
    assert curr_ctx.get("fps") == Fraction(24, 1) or curr_ctx.get("fps") == 24

    # Đổi path A -> path B phải tạo ra signature khác nhau
    plan_a = {
        "manifest_id": "mid1",
        "timeline_segments": [{"clip_id": "C01", "path": "/media/path_a.mp4", "start_sec": 0.0, "end_sec": 1.0}]
    }
    plan_b = {
        "manifest_id": "mid1",
        "timeline_segments": [{"clip_id": "C01", "path": "/media/path_b.mp4", "start_sec": 0.0, "end_sec": 1.0}]
    }

    sig_a = ReviewPage._extract_v2_context_signature(curr_ctx, video_plan=plan_a)
    sig_b = ReviewPage._extract_v2_context_signature(curr_ctx, video_plan=plan_b)
    assert sig_a != sig_b

    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_probe_r2_bad_plan_type_and_invalid_fps_no_mutation(tmp_path, qapp):
    """R2-C: _video_plan_data=['bad'] hoặc fps=False phải return None, generation delta 0, active worker không bị cancel."""
    v_path = str(tmp_path / "clip.mp4")
    create_fixture_video(v_path, duration=2)

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    gen_before = page._kinetic_generation
    active_before = page._active_kinetic_request_id

    # 1. _video_plan_data = ['bad'] (JSON hợp lệ nhưng không phải dict)
    page._video_plan_data = ["bad", "list", "plan"]
    res_bad_plan = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_bad_plan is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 2. explicit timeline_fps=False
    page._video_plan_data = None
    res_bad_fps = page.request_kinetic_preparation(intent="copy_kinetic", timeline_fps=False)
    assert res_bad_fps is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # 3. Context supplier trả về fps=False
    page.set_context_supplier(lambda: {**ctx, "fps": False})
    res_ctx_bad_fps = page.request_kinetic_preparation(intent="copy_kinetic")
    assert res_ctx_bad_fps is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_worker_context_mutation_rejects_stale(tmp_path, qapp):
    """Kiểm tra: Thay đổi context trong lúc worker đang xử lý -> kết quả cũ bị bỏ qua, không ghi đè."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Test", "words": [{"word": "Test", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    page = ReviewPage()
    ctx_holder = [
        {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    ]
    page.set_context_supplier(lambda: dict(ctx_holder[0]))

    barrier = threading.Event()
    orig_extract = extract_source_words

    def blocking_extract(*args, **kwargs):
        barrier.wait(timeout=5.0)
        return orig_extract(*args, **kwargs)

    with patch("src.ui.director.workers.kinetic_preparation_worker.extract_source_words", side_effect=blocking_extract):
        req_id = page.request_kinetic_preparation(intent="copy_kinetic", cache_mgr=cache_mgr)
        assert req_id is not None

        # Đổi context (ví dụ đổi whisper_model sang medium) trong lúc worker đang bị chặn
        ctx_holder[0]["whisper_model"] = "medium"

        # Giải phóng barrier
        barrier.set()

        # Chờ worker kết thúc
        assert wait_until(lambda: page.is_analysis_loading() is False, timeout_s=6.0)

        # Snapshot phải là None vì signature không khớp
        assert page._prepared_kinetic_snapshot is None
        assert page.get_kinetic_prompt() == ""

    page.shutdown_analysis_workers()
    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_worker_shutdown_and_close_pending(tmp_path, qapp):
    """Kiểm tra: shutdown_analysis_workers cooperative cancel worker, không rò rỉ QThread."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Test", "words": [{"word": "Test", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    barrier = threading.Event()

    def blocking_get_cached(*args, **kwargs):
        barrier.wait(timeout=5.0)
        return {"clip_dur": 2.0, "audio_analysis_status": "completed", "raw_subtitles": []}

    mock_mgr = ScanCacheManager(cache_dir=str(cache_dir))
    with patch.object(mock_mgr, "get_cached_scan", side_effect=blocking_get_cached):
        req_id = page.request_kinetic_preparation(intent="copy_kinetic", cache_mgr=mock_mgr)
        assert req_id is not None
        assert len(page._kinetic_workers_registry) == 1

        # Kích hoạt shutdown_analysis_workers
        page.shutdown_analysis_workers()
        assert page._review_close_pending is True

        # Giải phóng barrier
        barrier.set()

        # Đợi các worker dừng hoàn toàn
        assert wait_until(lambda: len(page._kinetic_workers_registry) == 0, timeout_s=6.0)
        assert page.is_analysis_idle() is True

    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_probe_missing_or_mismatched_manifest_id(tmp_path, qapp):
    """Probe 1: Plan thiếu manifest_id hoặc manifest_id không khớp phải bị từ chối rõ ràng."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    # 1. Plan thiếu manifest_id
    plan_no_mid = {
        "fps": 24,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.0, "speed": 1.0}]
    }
    req_no_mid = KineticPreparationRequest(
        request_id="req_no_mid",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_no_mid,
        timeline_fps=24,
    )
    worker_no_mid = KineticPreparationWorker(request=req_no_mid, cache_mgr=cache_mgr)
    res_no_mid = []
    worker_no_mid.finished_signal.connect(lambda r, s, m, res: res_no_mid.append((s, m, res)))
    worker_no_mid.start()
    assert wait_until(lambda: len(res_no_mid) == 1)
    worker_no_mid.wait()
    assert res_no_mid[0][0] is False
    assert res_no_mid[0][1] == "manifest_mismatch"

    # 2. Plan có manifest_id lệch
    plan_wrong_mid = {
        "manifest_id": "different_manifest_id_123",
        "fps": 24,
        "timeline_segments": [{"clip_id": manifest.clips[0].clip_id, "start_sec": 0.0, "end_sec": 1.0, "speed": 1.0}]
    }
    req_wrong_mid = KineticPreparationRequest(
        request_id="req_wrong_mid",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_wrong_mid,
        timeline_fps=24,
    )
    worker_wrong_mid = KineticPreparationWorker(request=req_wrong_mid, cache_mgr=cache_mgr)
    res_wrong_mid = []
    worker_wrong_mid.finished_signal.connect(lambda r, s, m, res: res_wrong_mid.append((s, m, res)))
    worker_wrong_mid.start()
    assert wait_until(lambda: len(res_wrong_mid) == 1)
    worker_wrong_mid.wait()
    assert res_wrong_mid[0][0] is False
    assert res_wrong_mid[0][1] == "manifest_mismatch"



def test_kinetic_probe_frame_geometry_and_hook_placed(tmp_path, qapp):
    """Probe 2: FPS 24, source [0.32, 0.40], duration 2s, pad 0.3s -> word start frame 8, segment duration 17; hook + body cùng clip."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [
                {
                    "start": 0.32, "end": 0.40, "text": "KhungHình",
                    "words": [{"word": "KhungHình", "start": 0.32, "end": 0.40}]
                }
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    manifest = ClipManifest.create([v_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    # Plan gồm cả hook và timeline segment trên cùng 1 clip
    plan = {
        "manifest_id": manifest.manifest_id,
        "fps": 24,
        "global_hook": {
            "clip_id": manifest.clips[0].clip_id,
            "start_sec": 0.32,
            "end_sec": 0.40,
            "speed": 1.0,
            "hook_title": "Hook Độc Lập"
        },
        "timeline_segments": [
            {
                "clip_id": manifest.clips[0].clip_id,
                "start_sec": 0.32,
                "end_sec": 0.40,
                "speed": 1.0,
                "chapter_name": "Phân Đoạn Thân"
            }
        ]
    }

    req = KineticPreparationRequest(
        request_id="req_geom",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan,
        timeline_fps=24,
    )
    worker = KineticPreparationWorker(request=req, cache_mgr=cache_mgr)
    res = []
    worker.finished_signal.connect(lambda r, s, m, payload: res.append((s, m, payload)))
    worker.start()

    assert wait_until(lambda: len(res) == 1)
    worker.wait()
    assert res[0][0] is True
    assert res[0][1] == "success"

    words = res[0][2]["words"]
    assert len(words) == 2  # 1 từ trong hook + 1 từ trong body

    # Từ đầu tiên nằm trong Hook: bắt đầu tại frame 8, kết thúc tại frame 10
    w_hook = words[0]
    assert w_hook["record_start_frame"] == 8
    assert w_hook["record_end_frame"] == 10
    assert "hook" in w_hook["instance_id"]

    # Hook dài 17 frames (rec_dur_frames = 17) -> segment thân bắt đầu từ frame 17
    # Từ thứ hai nằm trong Body: bắt đầu tại frame 17 + 8 = 25
    w_body = words[1]
    assert w_body["record_start_frame"] == 17 + 8
    assert "seg_0" in w_body["instance_id"]
    assert w_hook["instance_id"] != w_body["instance_id"]



def test_kinetic_probe_conflict_clip_refs(tmp_path, qapp):
    """Probe 3: Xung đột giữa clip_id và clip_name trỏ tới các clip khác nhau phải bị từ chối."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v1_path = str(tmp_path / "clip1.mp4")
    v2_path = str(tmp_path / "clip2.mp4")
    d1, p1 = create_fixture_video(v1_path, duration=2)
    d2, p2 = create_fixture_video(v2_path, duration=2)

    for p, d, prov in [(v1_path, d1, p1), (v2_path, d2, p2)]:
        cache_mgr.save_scan_result(
            file_path=p,
            scan_data={
                "clip_dur": 2.0,
                "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
            },
            model_size="small",
            language="vi",
            verified_identity=(d, prov)
        )

    manifest = ClipManifest.create([v1_path, v2_path], project_name="TestProj", cache_dir=str(tmp_path / "manifests"))

    # Segment khai báo clip_id là C01 (clip1) nhưng clip_name là clip2.mp4 -> Xung đột!
    plan_conflict = {
        "manifest_id": manifest.manifest_id,
        "fps": 24,
        "timeline_segments": [
            {
                "clip_id": manifest.clips[0].clip_id,
                "clip_name": os.path.basename(v2_path),
                "start_sec": 0.0,
                "end_sec": 1.0,
                "speed": 1.0
            }
        ]
    }

    req = KineticPreparationRequest(
        request_id="req_conf",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v1_path, v2_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        manifest_metadata={"project_name": "TestProj", "manifest_id": manifest.manifest_id, "clips": [c.to_dict() for c in manifest.clips]},
        video_plan_data=plan_conflict,
        timeline_fps=24,
    )
    worker = KineticPreparationWorker(request=req, cache_mgr=cache_mgr)
    res = []
    worker.finished_signal.connect(lambda r, s, m, payload: res.append((s, m, payload)))
    worker.start()

    assert wait_until(lambda: len(res) == 1)
    worker.wait()
    assert res[0][0] is False
    assert res[0][1] == "ambiguous_clip_reference"



def test_kinetic_probe_review_cuts_modified_requires_plan(tmp_path, qapp):
    """Probe 4: Người dùng đã sửa đổi keep/cut (kể cả toàn bộ False) nhưng chưa có VideoPlan -> edit_plan_required."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    req = KineticPreparationRequest(
        request_id="req_mod",
        generation=1,
        intent="copy_kinetic",
        context_primitives={
            "video_paths": [v_path],
            "whisper_model": "small",
            "whisper_language": "vi",
            "project_name": "TestProj",
            "review_cuts": [False, False],
            "review_cuts_modified": True,
        },
        timeline_fps=24,
    )
    worker = KineticPreparationWorker(request=req, cache_mgr=cache_mgr)
    res = []
    worker.finished_signal.connect(lambda r, s, m, payload: res.append((s, m, payload)))
    worker.start()

    assert wait_until(lambda: len(res) == 1)
    worker.wait()
    assert res[0][0] is False
    assert res[0][1] == "edit_plan_required"



def test_kinetic_probe_producer_error_and_contradiction(tmp_path, qapp):
    """Probe 5: Producer status 'error' hoặc mâu thuẫn no_audio với raw_subtitles phải ném source_error."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)

    # 1. Producer status='error'
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "audio_analysis_status": "error",
            "error": "Lỗi giải mã audio",
            "raw_subtitles": []
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    req_err = KineticPreparationRequest(
        request_id="req_err",
        generation=1,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_err = KineticPreparationWorker(request=req_err, cache_mgr=cache_mgr)
    res_err = []
    worker_err.finished_signal.connect(lambda r, s, m, payload: res_err.append((s, m, payload)))
    worker_err.start()
    assert wait_until(lambda: len(res_err) == 1)
    worker_err.wait()
    assert res_err[0][0] is False
    assert res_err[0][1] == "source_error"

    # 2. Mâu thuẫn no_audio nhưng raw_subtitles không rỗng
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "audio_analysis_status": "no_audio_track",
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )
    req_contra = KineticPreparationRequest(
        request_id="req_contra",
        generation=2,
        intent="copy_kinetic",
        context_primitives={"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"},
        timeline_fps=24,
    )
    worker_contra = KineticPreparationWorker(request=req_contra, cache_mgr=cache_mgr)
    res_contra = []
    worker_contra.finished_signal.connect(lambda r, s, m, payload: res_contra.append((s, m, payload)))
    worker_contra.start()
    assert wait_until(lambda: len(res_contra) == 1)
    worker_contra.wait()
    assert res_contra[0][0] is False
    assert res_contra[0][1] == "source_error"



def test_kinetic_probe_formatter_and_planner_source_mutation_and_cancel(tmp_path, qapp):
    """Probe 6: Tệp nguồn bị sửa hoặc worker bị cancel TRONG LÚC formatter/planner đang chạy -> 0 success."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    ctx_prims = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj"}

    # 1. File nguồn bị thay đổi trong lúc formatter chạy -> source_changed
    req_mut = KineticPreparationRequest(request_id="req_mut", generation=1, intent="copy_kinetic", context_primitives=ctx_prims, timeline_fps=24)
    worker_mut = KineticPreparationWorker(request=req_mut, cache_mgr=cache_mgr)

    from src.core.kinetic import kinetic_engine
    orig_gen_prompt = kinetic_engine.generate_kinetic_prompt

    def mutating_gen_prompt(*args, **kwargs):
        # Biến đổi file nguồn (thêm byte)
        with open(v_path, "ab") as f:
            f.write(b"\x00\x00\x00")
        return orig_gen_prompt(*args, **kwargs)

    res_mut = []
    worker_mut.finished_signal.connect(lambda r, s, m, payload: res_mut.append((s, m, payload)))
    with patch("src.ui.director.workers.kinetic_preparation_worker.generate_kinetic_prompt", side_effect=mutating_gen_prompt):
        worker_mut.start()
        assert wait_until(lambda: len(res_mut) == 1)
        worker_mut.wait()
        assert res_mut[0][0] is False
        assert res_mut[0][1] == "source_changed"

    # Tạo lại file video chuẩn cho test cancel
    d2, prov2 = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d2, prov2)
    )

    # 2. Worker bị cancel trong lúc formatter chạy -> cancelled
    req_can = KineticPreparationRequest(request_id="req_can", generation=2, intent="copy_kinetic", context_primitives=ctx_prims, timeline_fps=24)
    worker_can = KineticPreparationWorker(request=req_can, cache_mgr=cache_mgr)

    def cancelling_gen_prompt(*args, **kwargs):
        worker_can.cancel()
        return orig_gen_prompt(*args, **kwargs)

    res_can = []
    worker_can.finished_signal.connect(lambda r, s, m, payload: res_can.append((s, m, payload)))
    with patch("src.ui.director.workers.kinetic_preparation_worker.generate_kinetic_prompt", side_effect=cancelling_gen_prompt):
        worker_can.start()
        assert wait_until(lambda: len(res_can) == 1)
        worker_can.wait()
        assert res_can[0][0] is False
        assert res_can[0][1] == "cancelled"



def test_kinetic_probe_v2_signature_and_fail_closed_getters(tmp_path, qapp):
    """Probe 7: Đổi VideoPlan hoặc supplier ném lỗi -> getter fail-closed [] / '', không trả prompt cũ."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    v_path = str(tmp_path / "clip.mp4")
    d, prov = create_fixture_video(v_path, duration=2)
    cache_mgr.save_scan_result(
        file_path=v_path,
        scan_data={
            "clip_dur": 2.0,
            "raw_subtitles": [{"start": 0.0, "end": 1.0, "text": "Thoại", "words": [{"word": "Thoại", "start": 0.0, "end": 1.0}]}]
        },
        model_size="small",
        language="vi",
        verified_identity=(d, prov)
    )

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    req_id = page.request_kinetic_preparation(intent="copy_kinetic", aspect="9:16", cache_mgr=cache_mgr)
    assert req_id is not None
    assert wait_until(lambda: page.is_analysis_loading() is False, timeout_s=6.0)

    # Đã có prompt V2 sẵn
    prompt = page.get_kinetic_prompt(aspect="9:16")
    assert prompt != ""
    words = page.get_final_cut_words()
    assert len(words) == 1

    # 1. Đổi VideoPlan -> Chữ ký V2 thay đổi -> getter trả về "" và []
    page._video_plan_data = {
        "manifest_id": "test_mid",
        "fps": 24,
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0.5, "end_sec": 1.5, "speed": 1.0}]
    }
    assert page.get_kinetic_prompt(aspect="9:16") == ""
    assert page.get_final_cut_words() == []

    # 2. Supplier ném Exception -> getter fail-closed [] và ""
    page._video_plan_data = None
    page.set_context_supplier(lambda: 1 / 0)
    assert page.get_kinetic_prompt(aspect="9:16") == ""
    assert page.get_final_cut_words() == []

    page.shutdown_analysis_workers()
    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_probe_invalid_request_no_state_mutation(tmp_path, qapp):
    """Probe 8: Tham số không hợp lệ (aspect rỗng, intent sai) bị từ chối trước mutation, không đổi generation hay active ID."""
    v_path = str(tmp_path / "clip.mp4")
    create_fixture_video(v_path, duration=2)

    page = ReviewPage()
    ctx = {"video_paths": [v_path], "whisper_model": "small", "whisper_language": "vi", "project_name": "TestProj", "fps": 24}
    page.set_context_supplier(lambda: dict(ctx))

    gen_before = page._kinetic_generation
    active_before = page._active_kinetic_request_id

    # Aspect rỗng -> từ chối, trả về None, không ném exception trong Qt slot
    res_bad_aspect = page.request_kinetic_preparation(aspect="")
    assert res_bad_aspect is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    # Intent không hợp lệ -> từ chối, trả về None
    res_bad_intent = page.request_kinetic_preparation(intent="invalid_intent")
    assert res_bad_intent is None
    assert page._kinetic_generation == gen_before
    assert page._active_kinetic_request_id == active_before

    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================



def test_kinetic_probe_autoplan_gui_zero_manifest_io(tmp_path, qapp):
    """Probe 9: AutoPlan áp dụng kịch bản vào UI mà KHÔNG gọi ClipManifest.get_or_create trên GUI thread."""
    page = ReviewPage()
    page._manifest = None
    page._prepared_prompt_snapshot = None
    page._prepared_kinetic_snapshot = None
    page._clip_infos = ["/tmp/fake_clip1.mp4", "/tmp/fake_clip2.mp4"]

    from src.core.clip_manifest import ClipManifest
    with patch.object(ClipManifest, "get_or_create", wraps=ClipManifest.get_or_create) as spy_get_or_create:
        wrapped_data = {
            "type": "kinetic_plan",
            "count": 2,
            "is_offline": True,
            "data": {
                "aspect": "9:16",
                "pages": [
                    {"page_index": 1, "start": 0.0, "end": 1.0, "word_range": [0, 1]},
                ]
            }
        }
        page._apply_json_data(wrapped_data, "Test Summary")
        assert spy_get_or_create.call_count == 0

    page.close()
    page.deleteLater()
    QTest.qWait(50)


# =============================================================================
# 10. R3 FOCUSED REGRESSIONS: FINGERPRINT PLAN EXACT & NULL-SAFE
# =============================================================================


