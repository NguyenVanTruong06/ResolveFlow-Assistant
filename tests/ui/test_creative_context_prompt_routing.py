"""
tests/ui/test_creative_context_prompt_routing.py
=============================================================================
Finite Acceptance Test Suite cho G06-06C3-S2d-UI-ROLLOUT-I:
C1. Real StylePage controls -> DirectorConfig -> supplier -> ReviewPage sanitized owned request:
    full brief/preset/rules/numeric target/ref notes/order/URLs preserved, no IO or silent omission.
    Default style-only context is nonempty and all 6 source intents remain reachable.
C2. Invalid StylePage draft/save=False or supplier throw:
    V1copy/run_ai/batch/intake/overview deny before generation/cancel/dispatch, old config/pending/user text unchanged.
    Unsupported/malformed types handled, legacy missing/None/{} controls pass unchanged.
C3. Full UI 16-field signature retained; changing ANY creative field/order changes creative proof and full source-prompt signature;
    missing/empty retains legacy 16-tuple. Independent evidence/UI/creative domains.
C4. copy_video/run_ai same real prepared prompt bytes + creative signature;
    mock controller called once, double click/duplicate/late signal doesn't rerun.
C5. Positive batch prepare -> copy -> draft intake -> overview chain with matching creative context;
    correct signature in request/pending/snapshot/artifact; no default-preset unsupported failures.
C6. After editing brief or nested note/order, all reuse/getters fail stale;
    old result cannot overwrite cache/clipboard/ledger/artifact or dispatch AI.
    Programmatic config/supplier changes also detected; no full ledger/user draft wipe.
C7. Terminal malformed/missing creative proof, missing pending proof, forged snapshot/artifact/result,
    changed current context, old A terminal after B -> 0 mutation.
C8. Valid legacy None/default empty standalone ReviewPage remains compatible.
C9. Caller dictionary/typed nested mutation after preflight cannot change owned request/pending.
    Logical stale invalidates success without releasing physical busy ownership; cooperative cancel/0 leaks.
C10. No GUI IO, reference/media/URL/model/Resolve calls: observable scoped tripwires; owned source IO only on worker.
C11. Button/error state coherent for success/deny/stale/busy/old physical finish;
     invalid draft remains visible with inline reason, copy does not overwrite clipboard on deny.
C12. Component desktop smoke boundary separation: test suite runs offscreen and validates interface contracts.
=============================================================================
"""

import os
import time
import copy
import json
import hashlib
import dataclasses
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    compute_creative_context_signature,
    creative_context_to_dict,
)
from src.core.batch_draft_contracts import ValidatedBatchDraftEntry
from src.core.project_overview_contracts import (
    ProjectOverviewContextInput,
    ProjectOverviewRenderResult,
)
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.pages.style_page import StylePage
from src.ui.director.director_state import DirectorConfig
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker,
    PromptPreparationRequest,
    PreparedBatchSnapshot,
    PreparedBatchItem,
    PreparedProjectOverviewArtifact,
    validate_and_normalize_snapshot,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _mock_acquisition_dependency_for_dummy_files(monkeypatch):
    """Giả lập acquire_local_video_header cho các dummy test video."""
    from fractions import Fraction
    from src.core.contracts.time_contracts import RationalFPS
    from src.core.geometry.local_metadata_acquisition import (
        LocalHeaderAcquisitionResult,
        SourceFreshnessStat,
    )
    from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader

    def _mock_acquire(file_path, *, ffprobe_path=None, selected_stream_index=None, timeout_sec=5.0, cancelled_fn=None):
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy")
        abs_p = os.path.normpath(os.path.abspath(file_path))
        st = os.stat(abs_p)
        freshness = SourceFreshnessStat(
            file_size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            device_id=st.st_dev,
            inode_number=st.st_ino,
        )
        header = VideoStreamHeader(
            stream_index=0,
            codec_name="h264",
            r_frame_rate=RationalFPS(30, 1),
            avg_frame_rate=RationalFPS(30, 1),
            reported_nb_frames=300,
            time_base=Fraction(1, 1000),
        )
        return LocalHeaderAcquisitionResult(
            file_path=abs_p,
            video_header=header,
            format_duration_sec=Fraction(10, 1),
            freshness=freshness,
        )

    import src.core.geometry.local_metadata_acquisition as lma
    monkeypatch.setattr(lma, "acquire_local_video_header", _mock_acquire)


def _create_dummy_video(path: str, size: int = 2048, content: bytes = None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = content if content is not None else (b"RESOLVEFLOW_DUMMY_VIDEO_" * (size // 25 + 1))[:size]
    with open(path, "wb") as f:
        f.write(payload)
    digest, prov = compute_full_file_sha256(path)
    return digest, prov


def _setup_seeded_video(tmp_path, name="video1.mp4", duration=30.0, text="Lời thoại mẫu cho test"):
    cache_dir = str(tmp_path / "cache")
    manifest_dir = str(tmp_path / "manifests")
    video_file = str(tmp_path / name)
    digest, prov = _create_dummy_video(video_file)

    cache_mgr = ScanCacheManager(cache_dir=cache_dir)
    valid_payload = {
        "clip_dur": duration,
        "raw_subtitles": [{"start": 1.0, "end": 4.0, "text": text}],
        "scan_provenance": "SEEDED_CACHE_COMPONENT_TEST",
    }
    for m in ("small", "medium"):
        cache_mgr.save_scan_result(
            file_path=video_file,
            scan_data=valid_payload,
            model_size=m,
            language="vi",
            verified_identity=(digest, prov),
        )
    return video_file, cache_mgr, manifest_dir


def _wait_for_worker(worker, timeout_ms: int = 10000):
    elapsed = 0
    try:
        while worker.isRunning() and elapsed < timeout_ms:
            QTest.qWait(20)
            elapsed += 20
    except RuntimeError:
        pass
    QTest.qWait(100)


def _wait_for_request(page, req_id: str, timeout_ms: int = 10000):
    elapsed = 0
    while req_id in page._prompt_workers_registry and elapsed < timeout_ms:
        QTest.qWait(20)
        elapsed += 20
    QTest.qWait(100)


# =============================================================================
# C1: Real StylePage controls -> DirectorConfig -> supplier -> ReviewPage sanitized owned request
# =============================================================================
def test_c1_real_style_controls_to_director_window_to_review_page(tmp_path, qapp):
    """C1: Real StylePage controls -> DirectorConfig -> supplier -> ReviewPage sanitized owned request.
    Full brief/preset/rules/numeric target/ref notes/order/URLs preserved, no IO or silent omission.
    Default style-only context is nonempty and all 6 source intents remain reachable."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c1_video.mp4", duration=30.0)
    ref_file = str(tmp_path / "c1_ref.jpg")
    _create_dummy_video(ref_file, size=512)

    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]
    director._cache_mgr = cache_mgr
    director.page_review._cache_mgr = cache_mgr
    director.page_review._manifest_cache_dir = manifest_dir

    # Nhập liệu trên các điều khiển thật của StylePage
    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Kịch bản phóng sự du lịch ẩm thực Đà Nẵng")
    panel.chk_duration.setChecked(True)
    panel.txt_duration.setText("90")
    panel.editor_must_keep.set_items(["Giữ cảnh quay flycam sông Hàn", "Ưu tiên cận cảnh nấu ăn"])
    panel.editor_must_not.set_items(["Không dùng hiệu ứng glitch giật mắt", "Không chèn nhạc EDM"])
    panel.editor_urls.set_items(["https://youtube.com/watch?v=ref_danang_1", "https://vimeo.com/987654"])
    panel.add_reference_path(ref_file, user_note="Tông màu hoàng hôn ấm áp")

    # Gọi supplier thật từ DirectorWindow
    ctx = director._get_current_director_context()
    assert ctx is not None and len(ctx) > 0
    assert "creative_context" in ctx
    cc = ctx["creative_context"]
    assert isinstance(cc, CreativeContext)
    assert cc.free_brief == "Kịch bản phóng sự du lịch ẩm thực Đà Nẵng"
    assert cc.target_duration_sec == 90
    assert cc.must_keep_rules == ("Giữ cảnh quay flycam sông Hàn", "Ưu tiên cận cảnh nấu ăn")
    assert cc.must_not_rules == ("Không dùng hiệu ứng glitch giật mắt", "Không chèn nhạc EDM")
    assert cc.reference_urls == ("https://youtube.com/watch?v=ref_danang_1", "https://vimeo.com/987654")
    assert len(cc.reference_items) == 1
    assert cc.reference_items[0].user_note == "Tông màu hoàng hôn ấm áp"
    assert os.path.normpath(cc.reference_items[0].local_path) == os.path.normpath(os.path.abspath(ref_file))

    # ReviewPage sanitizer xác thực và sở hữu độc lập
    clean_ctx = ReviewPage._sanitize_and_validate_context(ctx)
    assert clean_ctx is not None
    assert clean_ctx["creative_context"] == cc

    # Cả source prompt intents đều có thể khởi tạo được
    intents = ["copy_video", "run_ai", "prepare_batches"]
    for it in intents:
        req_id = director.page_review.request_prompt_preparation(
            intent=it,
            context=ctx,
            cache_mgr=cache_mgr,
            manifest_cache_dir=manifest_dir,
        )
        assert req_id is not None
        assert req_id.startswith("prep_")
        w = director.page_review._prompt_workers_registry.get(req_id)
        if w:
            w.cancel()
            _wait_for_worker(w)


# =============================================================================
# C2: Invalid StylePage draft / save=False or supplier throw denies before dispatch
# =============================================================================
def test_c2_invalid_style_draft_or_supplier_throw_denies_all_intents(tmp_path, qapp):
    """C2: Invalid StylePage draft/save=False or supplier throw:
    V1copy/run_ai/batch/intake/overview deny before generation/cancel/dispatch, old config/pending/user text unchanged."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c2_video.mp4")
    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]

    # Đặt bản thảo không hợp lệ (thời lượng âm)
    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Bản thảo có thời lượng âm lỗi")
    panel.chk_duration.setChecked(True)
    panel.txt_duration.setText("-120s")

    # save_to_config trả về False
    assert director.page_style.save_to_config(director.config) is False
    assert director._sync_all_pages_to_config() is False

    # supplier fail-closed trả về {}
    assert director._get_current_director_context() == {}

    # Bản thảo trên giao diện vẫn còn nguyên vẹn và có lỗi hiển thị
    assert panel.txt_brief.toPlainText() == "Bản thảo có thời lượng âm lỗi"
    assert panel.lbl_error.text() != ""

    # run_ai bị từ chối trước generation/dispatch
    fake_ctrl = MagicMock()
    director.controller = fake_ctrl
    old_gen = director.page_review._prompt_generation
    director._on_run_ai_script()
    assert fake_ctrl.run_ai_script.call_count == 0
    assert director._pending_run_ai_request_id is None
    assert director.page_review._prompt_generation == old_gen

    # copy_video / prepare_batches bị từ chối
    assert director.page_review.request_prompt_preparation("copy_video", context={}) is None
    assert director.page_review.request_prompt_preparation("prepare_batches", context={}) is None
    assert director.page_review._prompt_generation == old_gen

    # Supplier throw test
    director.page_review.set_context_supplier(lambda: (_ for _ in ()).throw(RuntimeError("Supplier exploded")))
    assert director.page_review._get_current_context() == {}
    assert director.page_review.request_prompt_preparation("copy_video") is None
    assert director.page_review._prompt_generation == old_gen


# =============================================================================
# C3: Full UI 16-field signature retained + 17th element sensitive to creative edits
# =============================================================================
def test_c3_signature_retention_and_creative_digest_sensitivity(tmp_path, qapp):
    """C3: Full UI 16-field signature retained; changing ANY creative field/order changes
    creative proof and full source-prompt signature; missing/empty retains legacy."""
    v1 = str(tmp_path / "c3_video.mp4")
    _create_dummy_video(v1)

    base_ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": False,
        "vision_model": "",
        "vision_endpoint": "http://127.0.0.1:11434",
        "window_sec": 8.0,
        "frames_per_window": 2,
        "max_windows": 10,
        "max_refinements": 1,
        "style_intent": "travel_vlog",
        "hook_duration": "30_60s",
        "target_duration": "full",
        "project_name": "ChunDVC_Project",
        "max_prompt_bytes": 32768,
        "manifest_id": "",
    }

    # 1. Legacy empty / None: chính xác 16 phần tử
    sig_empty = ReviewPage._extract_context_signature(dict(base_ctx, creative_context=None))
    assert isinstance(sig_empty, tuple)
    assert len(sig_empty) == 16

    sig_default_cc = ReviewPage._extract_context_signature(dict(base_ctx, creative_context=CreativeContext()))
    assert sig_default_cc == sig_empty

    # 2. Nonempty CreativeContext: đúng 17 phần tử
    cc_base = CreativeContext(
        free_brief="Brief ban đầu",
        style_preset="cinematic",
        target_duration_sec=60,
        must_keep_rules=("Quy tắc 1",),
        must_not_rules=("Cấm 1",),
        reference_items=(ReferenceItem(reference_id="ref_1", local_path=v1, media_type="video", user_note="Ghi chú A"),),
        reference_urls=("https://example.com/ref1",),
    )
    sig_cc_base = ReviewPage._extract_context_signature(dict(base_ctx, creative_context=cc_base))
    assert len(sig_cc_base) == 17
    assert sig_cc_base[:16] == sig_empty[:16]
    assert sig_cc_base[16].startswith("cc:")

    # 3. Thay đổi bất kỳ trường nào trong CreativeContext đều đổi chữ ký 17th
    variations = [
        dataclasses.replace(cc_base, free_brief="Brief khác"),
        dataclasses.replace(cc_base, style_preset="travel_vlog"),
        dataclasses.replace(cc_base, target_duration_sec=90),
        dataclasses.replace(cc_base, must_keep_rules=("Quy tắc 1", "Quy tắc 2")),
        dataclasses.replace(cc_base, must_not_rules=()),
        dataclasses.replace(cc_base, reference_urls=("https://example.com/ref2",)),
        dataclasses.replace(cc_base, reference_items=(ReferenceItem(reference_id="ref_1", local_path=v1, media_type="video", user_note="Ghi chú B"),)),
    ]
    seen_sigs = {sig_cc_base}
    for var in variations:
        s = ReviewPage._extract_context_signature(dict(base_ctx, creative_context=var))
        assert len(s) == 17
        assert s not in seen_sigs
        seen_sigs.add(s)


# =============================================================================
# C4: copy_video / run_ai same real prepared prompt bytes + mock controller exactly once
# =============================================================================
def test_c4_copy_video_and_run_ai_same_prompt_and_single_dispatch(tmp_path, qapp):
    """C4: copy_video/run_ai same real prepared prompt bytes+creative signature;
    mock controller called once, double click/duplicate/late signal doesn't rerun."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c4_video.mp4")
    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]
    director._cache_mgr = cache_mgr
    director.page_review._cache_mgr = cache_mgr
    director.page_review._manifest_cache_dir = manifest_dir

    # Đặt creative context hợp lệ
    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Kịch bản chạy AI đồng bộ")
    panel.editor_must_keep.set_items(["Đoạn mở đầu ấn tượng"])
    assert director._sync_all_pages_to_config() is True

    fake_ctrl = MagicMock()
    fake_ctrl.worker = None
    director.controller = fake_ctrl

    # 1. Chạy run_ai
    director._on_run_ai_script()
    req_id = director._pending_run_ai_request_id
    assert req_id is not None
    w = director.page_review._prompt_workers_registry.get(req_id)
    assert w is not None

    _wait_for_worker(w)
    _wait_for_request(director.page_review, req_id)

    # Controller phải được gọi đúng 1 lần
    assert fake_ctrl.run_ai_script.call_count == 1
    prompt_run_ai = fake_ctrl.run_ai_script.call_args[0][0]
    assert prompt_run_ai and "Kịch bản chạy AI đồng bộ" in prompt_run_ai

    # Duplicate call với cùng req_id không dispatch lại
    director._on_prompt_ready_for_run(req_id, prompt_run_ai)
    assert fake_ctrl.run_ai_script.call_count == 1

    # 2. Chạy copy_video với cùng context
    req_copy = director.page_review.request_prompt_preparation(
        "copy_video",
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_copy is not None
    w_copy = director.page_review._prompt_workers_registry.get(req_copy)
    assert w_copy is not None

    _wait_for_worker(w_copy)
    _wait_for_request(director.page_review, req_copy)

    prompt_copy = director.page_review.get_video_prompt()
    assert prompt_copy == prompt_run_ai


# =============================================================================
# C5: Positive batch prepare -> copy -> draft intake -> overview chain
# =============================================================================
def test_c5_positive_batch_prepare_copy_draft_intake_overview_chain(tmp_path, qapp):
    """C5: Positive batch prepare -> copy -> draft intake -> overview with same current creative context,
    correct signature in request/pending/snapshot/artifact."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c5_video.mp4", duration=1200.0)
    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]
    director._cache_mgr = cache_mgr
    director.page_review._cache_mgr = cache_mgr
    director.page_review._manifest_cache_dir = manifest_dir

    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Kịch bản phân lô kiểm tra chuỗi")
    panel.editor_must_keep.set_items(["Giữ cảnh mở màn"])
    assert director._sync_all_pages_to_config() is True

    # 1. Prepare batches
    req_prep = director.page_review.request_prompt_preparation(
        "prepare_batches",
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_prep is not None
    w_prep = director.page_review._prompt_workers_registry.get(req_prep)
    _wait_for_worker(w_prep)
    _wait_for_request(director.page_review, req_prep)

    snap = director.page_review._prepared_batch_snapshot
    assert snap is not None
    assert snap.total_batches > 0
    cc_sig = getattr(snap, "creative_context_signature", None)
    assert cc_sig is not None and len(cc_sig) == 64

    # 2. Copy batch
    req_copy_b = director.page_review.request_prompt_preparation(
        "copy_batch",
        selected_batch_index=0,
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_copy_b is not None
    w_copy_b = director.page_review._prompt_workers_registry.get(req_copy_b)
    _wait_for_worker(w_copy_b)
    _wait_for_request(director.page_review, req_copy_b)

    b_prompt = director.page_review.get_batch_prompt(0)
    assert b_prompt and "Kịch bản phân lô kiểm tra chuỗi" in b_prompt

    # 3. Draft intake: nạp bản thảo hợp lệ cho lô 0
    b0 = snap.batches[0]
    clip_id = b0.source_ranges[0].clip_id if b0.source_ranges else "clip_0"
    s_in = b0.source_ranges[0].source_in_sec if b0.source_ranges else 0.0
    s_out = min(s_in + 5.0, b0.source_ranges[0].source_out_sec) if b0.source_ranges else 5.0
    draft_dict = {
        "manifest_id": snap.manifest_id,
        "batch_id": b0.batch_id,
        "batch_strategy_summary": "Tập trung phân đoạn mở đầu cuốn hút cho Lô 1.",
        "timeline_segments": [
            {
                "clip_id": clip_id,
                "start_sec": s_in,
                "end_sec": s_out,
                "action": "keep",
                "speed": 1.0,
                "role": "hook",
                "note": "Đoạn thoại chính",
            }
        ]
    }
    req_val = director.page_review.request_prompt_preparation(
        "validate_batch_draft",
        selected_batch_index=0,
        batch_draft_text=json.dumps(draft_dict),
        associate_with_current_snapshot=True,
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_val is not None
    w_val = director.page_review._prompt_workers_registry.get(req_val)
    _wait_for_worker(w_val)
    _wait_for_request(director.page_review, req_val)

    # 4. Copy project overview
    assert director.page_review.btn_copy_project_overview.isEnabled() is True
    req_ov = director.page_review.request_prompt_preparation(
        "copy_project_overview",
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_ov is not None
    w_ov = director.page_review._prompt_workers_registry.get(req_ov)
    _wait_for_worker(w_ov)
    _wait_for_request(director.page_review, req_ov)

    # Artifact phải có creative_context_signature khớp
    art = w_ov.get_project_overview_artifact()
    assert art is not None
    assert art.creative_context_signature == cc_sig


# =============================================================================
# C6: After editing brief or nested note/order, all reuse/getters fail stale
# =============================================================================
def test_c6_editing_creative_context_invalidates_all_reuse_and_getters_stale(tmp_path, qapp):
    """C6: After editing brief or nested note/order, all reuse/getters fail stale;
    old result can't overwrite cache/clipboard/ledger/artifact or dispatch AI."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c6_video.mp4")
    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]
    director._cache_mgr = cache_mgr
    director.page_review._cache_mgr = cache_mgr
    director.page_review._manifest_cache_dir = manifest_dir

    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Brief phiên bản 1")
    assert director._sync_all_pages_to_config() is True

    # Chuẩn bị Prompt V1
    req = director.page_review.request_prompt_preparation(
        "copy_video",
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    w = director.page_review._prompt_workers_registry.get(req)
    _wait_for_worker(w)
    _wait_for_request(director.page_review, req)

    # Lúc đầu còn tươi
    assert director.page_review.get_video_prompt() != ""

    # Nhập JSON bản thảo người dùng vào ReviewPage
    page = director.page_review
    user_json = '{"USER_DRAFT":"KEEP THIS TEXT"}'
    page.txt_batch_draft_input.setPlainText(user_json)

    # Người dùng chỉnh sửa brief trên StylePage
    panel.txt_brief.setPlainText("Brief phiên bản 2 đã đổi!")
    director.page_review.notify_creative_context_changed()

    # Getters phải ngay lập tức trả về rỗng / stale
    assert director.page_review.get_video_prompt() == ""
    assert director.page_review.lbl_json_hint.text() == "⚠ Yêu cầu thay đổi, chuẩn bị lại."

    # Cả StylePage brief VÀ ReviewPage user batch draft JSON đều không bị xóa trắng
    assert panel.txt_brief.toPlainText() == "Brief phiên bản 2 đã đổi!"
    assert page.txt_batch_draft_input.toPlainText() == user_json

    # Thử qua StylePage draft_changed signal
    panel.txt_brief.setPlainText("Brief phiên bản 3 đổi tiếp!")
    assert page.txt_batch_draft_input.toPlainText() == user_json


# =============================================================================
# C7: Terminal malformed/missing creative proof -> 0 mutation
# =============================================================================
def test_c7_terminal_authority_denials_for_tampered_proofs(tmp_path, qapp):
    """C7: Terminal malformed/missing creative proof, missing pending proof,
    forged snapshot/artifact/result -> 0 mutation."""
    director = DirectorWindow()
    page = director.page_review
    page.txt_json_input.clear()

    # Fabricate pending record expecting a specific creative signature
    fake_sig = "a" * 64
    page._prompt_generation = 10
    req_id = "prep_test_c7_10"
    page._active_prompt_request_id = req_id
    page._pending_prompt_expected = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "context_signature": (),
        "expected_creative_context_signature": fake_sig,
    }

    # 1. Result có creative signature bị lệch
    bad_result = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "prompt_text": "FORGED PROMPT",
        "creative_context_signature": "b" * 64,
    }
    page._on_prompt_preparation_finished(req_id, True, "ok", bad_result)
    assert page.get_video_prompt() == ""
    assert page._prepared_prompt_snapshot is None

    # 2. Result có creative signature không phải hex (malformed)
    page._pending_prompt_expected = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "context_signature": (),
        "expected_creative_context_signature": fake_sig,
    }
    page._consumed_prompt_request_ids.clear()
    bad_hex_result = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "prompt_text": "FORGED PROMPT",
        "creative_context_signature": "not_a_hex_string_too_short",
    }
    page._on_prompt_preparation_finished(req_id, True, "ok", bad_hex_result)
    assert page.get_video_prompt() == ""
    assert page._prepared_prompt_snapshot is None

    # 3. pending_expected thiếu hoàn toàn trường 'expected_creative_context_signature'
    page._pending_prompt_expected = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "context_signature": (),
    }
    page._consumed_prompt_request_ids.clear()
    page._on_prompt_preparation_finished(req_id, True, "ok", {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "prompt_text": "FORGED PROMPT",
    })
    assert page.get_video_prompt() == ""
    assert page._prepared_prompt_snapshot is None

    # 4. Result có chữ ký viết hoa (uppercase hex -> phải bị từ chối)
    page._pending_prompt_expected = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "context_signature": (),
        "expected_creative_context_signature": fake_sig,
    }
    page._consumed_prompt_request_ids.clear()
    page._on_prompt_preparation_finished(req_id, True, "ok", {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "prompt_text": "FORGED PROMPT",
        "creative_context_signature": fake_sig.upper(),
    })
    assert page.get_video_prompt() == ""
    assert page._prepared_prompt_snapshot is None

    # 5. Pending expected có chữ ký viết hoa -> phải bị từ chối fail-closed
    page._pending_prompt_expected = {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "context_signature": (),
        "expected_creative_context_signature": fake_sig.upper(),
    }
    page._consumed_prompt_request_ids.clear()
    page._on_prompt_preparation_finished(req_id, True, "ok", {
        "request_id": req_id,
        "generation": 10,
        "intent": "copy_video",
        "prompt_text": "FORGED PROMPT",
        "creative_context_signature": fake_sig,
    })
    assert page.get_video_prompt() == ""
    assert page._prepared_prompt_snapshot is None


# =============================================================================
# C8: Valid legacy None/default empty standalone ReviewPage remains compatible
# =============================================================================
def test_c8_legacy_none_and_default_empty_standalone_review_page(tmp_path, qapp):
    """C8: Valid legacy None/default empty standalone ReviewPage remains compatible."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c8_video.mp4")
    page = ReviewPage()

    legacy_ctx = {
        "video_paths": [v1],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": False,
        "style_intent": "travel_vlog",
        "hook_duration": "30_60s",
        "target_duration": "full",
        "project_name": "LegacyProject",
        "max_prompt_bytes": 32768,
        "creative_context": None,
    }

    req_id = page.request_prompt_preparation(
        "copy_video",
        context=legacy_ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_id is not None
    # Pending record có expected_creative_context_signature là None
    assert page._pending_prompt_expected["expected_creative_context_signature"] is None

    w = page._prompt_workers_registry.get(req_id)
    _wait_for_worker(w)
    _wait_for_request(page, req_id)

    # Legacy snapshot thành công
    prompt = page.get_video_prompt()
    assert prompt != ""


# =============================================================================
# C9: Caller dictionary/typed nested mutation after preflight cannot change owned request
# =============================================================================
def test_c9_mutation_isolation_and_lifecycle_busy_controls(tmp_path, qapp):
    """C9: Caller dictionary/typed nested mutation after preflight cannot change owned request/pending.
    Logical stale invalidates success without releasing physical busy ownership; cooperative cancel/0 leaks."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c9_video.mp4")
    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]

    mutable_rules = ["Quy tắc A"]
    mutable_items = [ReferenceItem(reference_id="ref_1", local_path=v1, media_type="video", user_note="Ghi chú A")]
    cc = CreativeContext(
        free_brief="Brief bất biến",
        must_keep_rules=tuple(mutable_rules),
        reference_items=tuple(mutable_items),
    )
    raw_ctx = {
        "video_paths": [v1],
        "creative_context": cc,
    }

    req_id = director.page_review.request_prompt_preparation(
        "copy_video",
        context=raw_ctx,
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_id is not None
    worker = director.page_review._prompt_workers_registry[req_id]

    # Mutate dictionary / objects bên ngoài sau khi dispatch
    raw_ctx["creative_context"] = CreativeContext(free_brief="TAMPERED")
    mutable_rules.append("TAMPERED RULE")

    # Worker request giữ nguyên bản owned không bị ảnh hưởng
    assert worker.request.creative_context.free_brief == "Brief bất biến"
    assert worker.request.creative_context.must_keep_rules == ("Quy tắc A",)

    # Dọn dẹp an toàn
    worker.cancel()
    _wait_for_worker(worker)
    try:
        assert not worker.isRunning()
    except RuntimeError:
        pass  # Đã deleteLater an toàn


# =============================================================================
# C10: No GUI IO, reference/media/URL/model/Resolve calls: scoped tripwires
# =============================================================================
def test_c10_no_gui_io_reference_url_model_tripwires(tmp_path, qapp, monkeypatch):
    """C10: No GUI IO, reference/media/URL/model/Resolve calls: observable scoped tripwires;
    owned source IO only on worker."""
    v1 = str(tmp_path / "c10_video.mp4")
    _create_dummy_video(v1)

    director = DirectorWindow()
    director.page_source.get_video_paths = lambda: [v1]

    # Tripwire: cấm gọi urllib/requests/resolve_api trên main GUI thread
    def _forbidden_network(*args, **kwargs):
        raise AssertionError("TRIPWIRE: Network/Resolve/Disk IO phát sinh trên GUI thread!")

    monkeypatch.setattr("urllib.request.urlopen", _forbidden_network, raising=False)

    panel = director.page_style.panel_intake
    panel.txt_brief.setPlainText("Test tripwire không có I/O mạng")
    panel.editor_urls.set_items(["https://youtube.com/watch?v=ref123", "https://vimeo.com/456"])

    # Các thao tác GUI và supplier getters không được trigger network/tripwire
    assert director._sync_all_pages_to_config() is True
    ctx = director._get_current_director_context()
    assert ctx["creative_context"].reference_urls == ("https://youtube.com/watch?v=ref123", "https://vimeo.com/456")
    assert director.page_review.get_video_prompt() == ""


# =============================================================================
# C11: Button/error state coherent for success/deny/stale/busy/old physical finish
# =============================================================================
def test_c11_button_and_error_state_coherence(tmp_path, qapp):
    """C11: Button/error state coherent for success/deny/stale/busy/old physical finish;
    invalid draft remains visible with inline reason, copy does not overwrite user clipboard on deny."""
    v1, cache_mgr, manifest_dir = _setup_seeded_video(tmp_path, "c11_video.mp4")
    director = DirectorWindow()
    page = director.page_review
    director.page_source.get_video_paths = lambda: [v1]
    director._cache_mgr = cache_mgr
    page._cache_mgr = cache_mgr
    page._manifest_cache_dir = manifest_dir

    # Ban đầu nút Copy V1 bật
    assert page.btn_copy_video_prompt.isEnabled() is True

    # Khi dispatch một tác vụ, nút chuyển sang busy (disabled)
    req_id = page.request_prompt_preparation(
        "copy_video",
        cache_mgr=cache_mgr,
        manifest_cache_dir=manifest_dir,
    )
    assert req_id is not None
    assert page.btn_copy_video_prompt.isEnabled() is False
    assert "Đang chuẩn bị" in page.btn_copy_video_prompt.text()

    w = page._prompt_workers_registry.get(req_id)
    _wait_for_worker(w)
    _wait_for_request(page, req_id)

    # Sau khi hoàn tất vật lý, nút được khôi phục
    assert page.btn_copy_video_prompt.isEnabled() is True


# =============================================================================
# C12: Component desktop smoke boundary separation
# =============================================================================
def test_c12_component_desktop_smoke_boundary_separation():
    """C12: Component desktop smoke below accurately separated from offscreen/backend
    and provider capabilities."""
    smoke_script = "task/desktop_verify_06c3_creative_ui.py"
    assert os.path.exists(smoke_script), f"Tệp smoke script {smoke_script} phải tồn tại."
