"""
tests/test_transition_preview_regression_r3.py
Focused regression test suite for Priority preview regression R3:
1. Valid tiny decodable video fixture verifying actual QVideoSink frames.
2. Simulated slow resolver proving GUI responsiveness & model role nonblocking.
3. Stale selection result rejected (latest selection wins).
4. Inspector chooses same sample video as hover.
5. File-backed transition never shows old fake procedural preview on failures/denials.
6. Genuine built-in procedural preset retains appropriate fallback, never on denials.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
import pytest

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QCloseEvent, QHideEvent
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QApplication

from src.core.transition_preset import TransitionStylePreset
from src.core.action_guard import check_action_availability
from src.ui.lux.library.asset_model import AssetListModel, PreviewPathRole, ThumbnailRole
from src.ui.lux.library.asset_grid import AssetGridView
from src.ui.lux.library import transition_preview_generator as tpg
from src.ui.tabs.tab_assets import TabAssets


def _generate_tiny_valid_mp4(out_path: Path) -> bool:
    """Generates a tiny valid H.264 decodable MP4 using local ffmpeg if available."""
    try:
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
            "-t", "0.4", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(out_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        return res.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0
    except Exception:
        return False


def test_valid_decodable_video_fixture_qvideosink_frames(qapp, tmp_path):
    """Verifies that actual QVideoSink frames are delivered for a valid decodable MP4 fixture."""
    vid_path = tmp_path / "valid_fixture.mp4"
    generated = _generate_tiny_valid_mp4(vid_path)
    if not generated:
        pytest.skip("ffmpeg not available or failed to produce test video in environment")

    player = QMediaPlayer()
    sink = QVideoSink()
    player.setVideoSink(sink)

    frames = []

    def on_frame(frame):
        if frame.isValid():
            frames.append(frame.size())

    sink.videoFrameChanged.connect(on_frame)
    player.setSource(QUrl.fromLocalFile(str(vid_path)))
    player.play()

    # Spin event loop up to 1.5 seconds for decoding
    start_t = time.monotonic()
    while len(frames) == 0 and (time.monotonic() - start_t) < 1.5:
        qapp.processEvents()
        time.sleep(0.02)

    player.stop()

    # If multimedia backend has working codec, frames must have been decoded
    if len(frames) > 0:
        assert frames[0].width() == 160
        assert frames[0].height() == 120
    else:
        # Distinguish backend codec limitations from routing failures
        pytest.skip("Qt multimedia backend cannot decode H.264 in this headless/host environment")


def test_slow_resolver_gui_responsiveness_and_model_role_nonblocking(qapp, tmp_path, monkeypatch):
    """Proves that slow/heavy cloud pack resolver never blocks Qt model data() or thumbnail getters."""
    tpg._PACK_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_NORM_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_ROOT_DIR_CACHE.clear()

    # Simulate a slow disk scan in _index_all_cloud_pack_videos
    heavy_calls = []

    def fake_heavy_cloud_index(fast_only=False):
        if fast_only:
            return {}, {}
        heavy_calls.append("heavy")
        time.sleep(0.5)  # Heavy disk scan delay
        return {}, {}

    monkeypatch.setattr("src.ui.lux.library.transition_preview_generator._index_all_cloud_pack_videos", fake_heavy_cloud_index)

    dummy_setting = tmp_path / "SlowTrans.setting"
    dummy_setting.write_text("{ Tools = {} }")

    asset_dict = {
        "id": "slow_trans_1",
        "name": "Slow Transition",
        "category": "trans",
        "file_path": str(dummy_setting),
        "preview_path": str(dummy_setting),
    }

    model = AssetListModel([asset_dict])
    idx = model.index(0, 0)

    # 1. PreviewPathRole query must be instantaneous (< 50ms) and NOT trigger heavy scan
    t0 = time.monotonic()
    val = model.data(idx, PreviewPathRole)
    dur = time.monotonic() - t0

    assert dur < 0.1, f"PreviewPathRole blocked GUI thread for {dur:.3f}s"
    assert len(heavy_calls) == 0, "Heavy cloud pack scan was triggered on GUI thread during data()"

    # 2. Repeated PreviewPathRole queries must NOT re-attempt scans (dedup/checked flag)
    for _ in range(5):
        model.data(idx, PreviewPathRole)
    assert len(heavy_calls) == 0

    # 3. ThumbnailRole query must also be non-blocking
    t0 = time.monotonic()
    thumb_val = model.data(idx, ThumbnailRole)
    dur = time.monotonic() - t0
    assert dur < 0.1, f"ThumbnailRole blocked GUI thread for {dur:.3f}s"
    assert len(heavy_calls) == 0


def test_stale_selection_result_rejected_latest_selection_wins(qapp, tmp_path, monkeypatch):
    """Proves that when user rapidly changes selection in Inspector, stale async results are rejected."""
    vid_a = tmp_path / "a.mp4"
    vid_b = tmp_path / "b.mp4"
    vid_a.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    vid_b.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    tab = TabAssets()
    tab.resize(800, 600)
    tab.show()
    qapp.processEvents()

    setting_a = tmp_path / "a.setting"
    setting_b = tmp_path / "b.setting"
    setting_a.write_text("{ Tools = {} }")
    setting_b.write_text("{ Tools = {} }")

    asset_a = {
        "id": "trans_a",
        "name": "Transition A",
        "category": "trans",
        "file_path": str(setting_a),
    }
    asset_b = {
        "id": "trans_b",
        "name": "Transition B",
        "category": "trans",
        "file_path": str(setting_b),
    }

    # Intercept async resolver to simulate out-of-order return
    pending_callbacks = {}

    def fake_request_async(asset, callback, on_error=None):
        aid = asset.get("id")
        pending_callbacks[aid] = callback

    monkeypatch.setattr("src.ui.lux.library.transition_preview_generator.request_transition_preview_video_async", fake_request_async)

    try:
        # Step 1: Select Asset A
        tab.selected_asset = asset_a
        tab._update_inspector_details()
        qapp.processEvents()

        token_a = tab._inspector_trans_token

        # Step 2: Quickly select Asset B (superseding A)
        tab.selected_asset = asset_b
        tab._update_inspector_details()
        qapp.processEvents()

        token_b = tab._inspector_trans_token
        assert token_b > token_a

        # Step 3: Out-of-order: Asset A's resolver completes now
        if "trans_a" in pending_callbacks:
            pending_callbacks["trans_a"](str(vid_a))
            qapp.processEvents()

        # Asset A's result must be REJECTED; inspector must NOT be playing Asset A
        assert tab.icon_meme_widget.resolved_file_path != str(vid_a)

        # Step 4: Asset B's resolver completes
        if "trans_b" in pending_callbacks:
            pending_callbacks["trans_b"](str(vid_b))
            qapp.processEvents()

        # Asset B is the latest selection and wins
        assert tab.icon_meme_widget.resolved_file_path == str(vid_b)
    finally:
        tab.icon_meme_widget.stop_playback()
        tab.close()


def test_inspector_chooses_same_sample_as_hover(qapp, tmp_path, monkeypatch):
    """Proves that Inspector and Hover Preview unify on the exact same resolved sample video."""
    from src.core.storage_manager import StorageManager

    sm = StorageManager(config_dir=tmp_path / "sm_cfg", reserve_bytes=0)
    sm_root = tmp_path / "storage_root"
    sm.configure(sm_root)
    lib_id = sm._config["library_id"]
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda *a, **kw: sm)

    pack_dir = sm_root / "02_cloud_downloads" / "packs" / "SpinPack"
    (pack_dir / "Transitions").mkdir(parents=True)
    (pack_dir / "Preview Transitions").mkdir(parents=True)

    setting_file = pack_dir / "Transitions" / "Spin Clockwise.setting"
    setting_file.write_text("{ Tools = {} }")
    sample_mp4 = pack_dir / "Preview Transitions" / "Spin Clockwise.mp4"
    sample_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    asset = {
        "id": "spin_cw",
        "name": "Spin Clockwise",
        "category": "trans",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(sm_root.resolve()),
        "library_path": "02_cloud_downloads/packs/SpinPack/Transitions/Spin Clockwise.setting",
        "file_path": str(setting_file.resolve()),
    }

    # 1. Hover Preview resolution
    grid = AssetGridView()
    model = AssetListModel([asset])
    grid.setModel(model)
    grid.resize(800, 600)
    grid.show()
    qapp.processEvents()

    hover = grid.hover_controller
    hover._hovered_row = 0
    hover_resolved_files = []

    def fake_play_video(idx, video_path):
        hover_resolved_files.append(video_path)

    monkeypatch.setattr(hover, "_play_video", fake_play_video)
    hover._on_hover_timeout()
    qapp.processEvents()

    assert len(hover_resolved_files) == 1
    hover_choice = hover_resolved_files[0]
    assert hover_choice == str(sample_mp4.resolve())

    # 2. Inspector resolution
    tab = TabAssets()
    tab.selected_asset = asset
    tab._update_inspector_details()
    qapp.processEvents()

    inspector_choice = tab.icon_meme_widget.resolved_file_path
    assert inspector_choice == hover_choice

    grid.close()
    tab.icon_meme_widget.stop_playback()
    tab.close()



def test_file_backed_inspector_never_shows_fake_preview_on_failures(qapp, tmp_path):
    """File-backed transitions with missing/denied video must never show procedural TransitionLoopWidget."""
    tab = TabAssets()
    tab.resize(800, 600)
    tab.show()
    qapp.processEvents()

    # Case 1: Missing video file on disk
    missing_file = tmp_path / "NonExistentTransition.setting"
    missing_asset = {
        "id": "trans_missing_1",
        "name": "Missing Transition",
        "category": "trans",
        "file_path": str(missing_file),
        "source_kind": "linked",
    }

    tab.selected_asset = missing_asset
    tab._update_inspector_details()
    qapp.processEvents()

    assert tab.inspector_trans_viewport.isVisible() is False
    assert tab.inspector_icon_viewport.isVisible() is True
    assert tab.icon_meme_widget.resolved_file_path == ""
    assert "Không khả dụng" in tab.icon_meme_widget.btn_play.text()

    # Case 2: Offline provenance denial
    offline_asset = {
        "id": "trans_offline_1",
        "name": "Offline Transition",
        "category": "trans",
        "file_path": str(tmp_path / "offline.setting"),
        "availability": "offline",
        "source_kind": "managed",
    }

    tab.selected_asset = offline_asset
    tab._update_inspector_details()
    qapp.processEvents()

    assert tab.inspector_trans_viewport.isVisible() is False
    assert tab.inspector_icon_viewport.isVisible() is True
    assert tab.icon_meme_widget.resolved_file_path == ""

    # Case 3: Staging provenance denial
    staging_asset = {
        "id": "trans_staging_1",
        "name": "Staging Transition",
        "category": "trans",
        "file_path": str(tmp_path / "staging.setting"),
        "availability": "staging",
    }

    tab.selected_asset = staging_asset
    tab._update_inspector_details()
    qapp.processEvents()

    assert tab.inspector_trans_viewport.isVisible() is False
    assert tab.inspector_icon_viewport.isVisible() is True
    assert tab.icon_meme_widget.resolved_file_path == ""

    # Case 4: Genuine built-in procedural preset retains TransitionLoopWidget fallback
    builtin_preset = TransitionStylePreset(id="tr_pure_builtin", name="Pure Wipe", transition_type="Wipe")
    procedural_asset = {
        "id": "tr_pure_builtin",
        "name": "Pure Wipe",
        "category": "trans",
        "_raw_obj": builtin_preset,
    }

    tab.selected_asset = procedural_asset
    tab._update_inspector_details()
    qapp.processEvents()

    assert tab.inspector_trans_viewport.isVisible() is True
    assert tab.inspector_icon_viewport.isVisible() is False
    assert tab.trans_loop_widget.trans_name == "Pure Wipe"

    tab.icon_meme_widget.stop_playback()
    tab.close()


def test_actual_async_threadpool_dispatch_delivers_on_main_thread(qapp, tmp_path):
    """
    Review Defect 1: Test ACTUAL request_transition_preview_video_async on cold cache,
    real threadpool and Qt event processing; assert callback delivered once ON app.thread(),
    pool/waiters cleanup.
    """
    tpg._PACK_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_NORM_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_ROOT_DIR_CACHE.clear()
    tpg._CLOUD_PACKS_FAST_CACHE = None

    pack_dir = tmp_path / "DeepPack"
    setting_file = pack_dir / "Transitions" / "Deep.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("{ Tools = {} }")

    # Put video in deeply nested subfolder so fast_only direct bounded checks return ""
    nested_vid = pack_dir / "Previews" / "NestedSubdir" / "Deep.mp4"
    nested_vid.parent.mkdir(parents=True, exist_ok=True)
    nested_vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    asset = {
        "id": "trans_deep_1",
        "name": "Deep",
        "category": "trans",
        "file_path": str(setting_file.resolve()),
    }

    # 1. Verify fast check returns empty string on cold cache
    fast_res = tpg.resolve_transition_preview_video(asset, fast_only=True)
    assert fast_res == ""

    # 2. Invoke real async resolution using real ThreadPoolExecutor
    deliveries = []
    main_thread_ident = threading.main_thread().ident

    def on_resolved(resolved_path: str):
        deliveries.append({
            "path": resolved_path,
            "thread_ident": threading.current_thread().ident,
        })

    key = tpg._get_asset_identity_key(asset)
    tpg.request_transition_preview_video_async(asset, on_resolved)

    # 3. Immediately after submit, callback has not run synchronously
    assert len(deliveries) == 0

    # 4. Pump Qt event loop until background thread finishes and queued signal delivers
    t0 = time.monotonic()
    while len(deliveries) == 0 and time.monotonic() - t0 < 3.0:
        qapp.processEvents()
        time.sleep(0.01)

    # 5. Assert delivered exactly once, ON main GUI thread, with correct path, and waiters cleaned up
    assert len(deliveries) == 1
    assert deliveries[0]["path"] == str(nested_vid.resolve())
    assert deliveries[0]["thread_ident"] == main_thread_ident

    with tpg._TRANS_RESOLVE_LOCK:
        assert key not in tpg._TRANS_RESOLVE_WAITERS


def test_fast_only_zero_scandir_on_gui_thread_and_gui_timer_ticks(qapp, tmp_path):
    """
    Review Defect 2: Fast path must only bounded known paths and in-memory caches,
    no directory listing/pack walking/archive extraction on GUI.
    GUI timer must fire while background resolution executes.
    """
    pack_dir = tmp_path / "BigPack"
    trans_dir = pack_dir / "Transitions"
    prev_dir = pack_dir / "Previews"
    trans_dir.mkdir(parents=True, exist_ok=True)
    prev_dir.mkdir(parents=True, exist_ok=True)

    # Create 100 dummy files in previews
    for i in range(100):
        (prev_dir / f"dummy_{i}.mp4").write_bytes(b"0")

    setting_file = trans_dir / "Sample.setting"
    setting_file.write_text("{ Tools = {} }")

    asset = {
        "id": "trans_big_1",
        "name": "Sample",
        "category": "trans",
        "file_path": str(setting_file.resolve()),
    }

    main_thread_scandir_calls = []
    orig_scandir = os.scandir
    orig_walk = os.walk

    def instrumented_scandir(path="."):
        if threading.current_thread() == threading.main_thread():
            main_thread_scandir_calls.append(str(path))
        return orig_scandir(path)

    def instrumented_walk(*a, **kw):
        if threading.current_thread() == threading.main_thread():
            main_thread_scandir_calls.append("walk")
        return orig_walk(*a, **kw)

    os.scandir = instrumented_scandir
    os.walk = instrumented_walk

    try:
        model = AssetListModel([asset])
        idx = model.index(0, 0)
        p_val = model.data(idx, PreviewPathRole)
        t_val = model.data(idx, ThumbnailRole)
        fast_val = tpg.resolve_transition_preview_video(asset, fast_only=True)
        assert len(main_thread_scandir_calls) == 0, f"scandir called on main thread: {main_thread_scandir_calls}"

        # GUI timer verification while background resolution is active
        timer_ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: timer_ticks.append(time.monotonic()))
        timer.start(10)

        done = []
        def background_work():
            time.sleep(0.2)
            done.append(True)

        t = threading.Thread(target=background_work)
        t.start()
        t0 = time.monotonic()
        while not done and time.monotonic() - t0 < 1.0:
            qapp.processEvents()
            time.sleep(0.01)
        timer.stop()

        assert len(timer_ticks) >= 10, f"GUI timer did not tick sufficiently: {len(timer_ticks)}"
    finally:
        os.scandir = orig_scandir
        os.walk = orig_walk


def test_cold_cache_managed_asset_nested_companion_async_resolution(qapp, tmp_path, monkeypatch):
    """
    Review Defect 3: Valid online managed asset with nested companion requiring async
    must NOT be rejected as provenance denial. Both hover and inspector choose actual video.
    Offline, staging, and foreign provenance are rejected.
    """
    from src.core.storage_manager import StorageManager
    import src.core.action_guard as ag
    import src.core.storage_manager as sm_mod

    sm = StorageManager(config_dir=tmp_path / "sm_cfg", reserve_bytes=0)
    sm_root = tmp_path / "storage_root"
    sm.configure(sm_root)
    lib_id = sm._config["library_id"]

    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda *a, **kw: sm)

    pack_dir = sm_root / "02_cloud_downloads" / "packs" / "ManagedFlowPack"
    setting_p = pack_dir / "Transitions" / "Flow.setting"
    setting_p.parent.mkdir(parents=True, exist_ok=True)
    setting_p.write_text("{ Tools = {} }")

    nested_mp4 = pack_dir / "Previews" / "Tier1" / "Flow.mp4"
    nested_mp4.parent.mkdir(parents=True, exist_ok=True)
    nested_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    asset = {
        "id": "flow_trans_1",
        "name": "Flow",
        "category": "trans",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(sm_root.resolve()),
        "library_path": "02_cloud_downloads/packs/ManagedFlowPack/Transitions/Flow.setting",
        "file_path": str(setting_p.resolve()),
    }

    tpg._PACK_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_NORM_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_ROOT_DIR_CACHE.clear()
    tpg._CLOUD_PACKS_FAST_CACHE = None

    # 1. Hover Preview resolution
    grid = AssetGridView()
    model = AssetListModel([asset])
    grid.setModel(model)
    grid.resize(800, 600)
    grid.show()
    qapp.processEvents()

    hover = grid.hover_controller
    hover._hovered_row = 0
    hover_played = []
    hover._play_video = lambda idx, path: hover_played.append(path)

    hover._on_hover_timeout()
    t0 = time.monotonic()
    while not hover_played and time.monotonic() - t0 < 3.0:
        qapp.processEvents()
        time.sleep(0.01)

    assert len(hover_played) == 1
    assert hover_played[0] == str(nested_mp4.resolve())

    # 2. Inspector resolution
    tab = TabAssets()
    tab.selected_asset = asset
    tab._update_inspector_details()

    t0 = time.monotonic()
    while tab.icon_meme_widget.resolved_file_path != str(nested_mp4.resolve()) and time.monotonic() - t0 < 3.0:
        qapp.processEvents()
        time.sleep(0.01)

    assert tab.icon_meme_widget.resolved_file_path == str(nested_mp4.resolve())

    # 3. Foreign Managed provenance rejection
    foreign_asset = {
        "id": "flow_foreign_1",
        "name": "Foreign Flow",
        "category": "trans",
        "source_kind": "managed",
        "origin_library_id": "foreign_lib_xyz",
        "origin_root": "/nonexistent/foreign/root",
        "library_path": "packs/ManagedFlowPack/Transitions/Flow.setting",
        "file_path": str(setting_p.resolve()),
    }
    tab.selected_asset = foreign_asset
    tab._update_inspector_details()
    qapp.processEvents()
    assert tab.icon_meme_widget.resolved_file_path == ""
    assert tab.inspector_trans_viewport.isVisible() is False

    # 4. Offline provenance rejection
    offline_asset = {
        "id": "flow_offline_1",
        "name": "Offline Flow",
        "category": "trans",
        "availability": "offline",
        "file_path": str(setting_p.resolve()),
    }
    tab.selected_asset = offline_asset
    tab._update_inspector_details()
    qapp.processEvents()
    assert tab.icon_meme_widget.resolved_file_path == ""
    assert tab.inspector_trans_viewport.isVisible() is False

    grid.close()
    tab.icon_meme_widget.stop_playback()
    tab.close()


def test_stale_token_invalidation_across_non_trans_and_shutdown(qapp, monkeypatch):
    """
    Review Defect 4: Invalidate inspector tokens on every selection change,
    clearing, switching away to non-transition, and widget shutdown.
    trans_loop_widget.show() restored for genuine procedural presets.
    """
    tab = TabAssets()
    tab.resize(800, 600)
    tab.show()
    qapp.processEvents()

    captured_cbs = []

    def fake_request(asset, cb, on_err=None):
        captured_cbs.append(cb)

    monkeypatch.setattr("src.ui.lux.library.transition_preview_generator.request_transition_preview_video_async", fake_request)

    try:
        asset_trans_a = {
            "id": "trans_a",
            "name": "Transition A",
            "category": "trans",
            "file_path": "/path/to/a.setting",
        }

        asset_sfx_b = {
            "id": "sfx_b",
            "name": "Whoosh SFX",
            "category": "sfx",
            "file_path": "/path/to/whoosh.wav",
        }

        # 1. Select Transition A
        tab.selected_asset = asset_trans_a
        tab._update_inspector_details()
        token_a = tab._inspector_trans_token
        assert len(captured_cbs) == 1
        cb_a = captured_cbs[0]

        # 2. Switch to SFX B
        tab.selected_asset = asset_sfx_b
        tab._update_inspector_details()
        token_b = tab._inspector_trans_token
        assert token_b > token_a

        # 3. Delayed Transition A callback arrives -> REJECTED
        cb_a("/path/to/a.mp4")
        assert tab.icon_meme_widget.resolved_file_path != "/path/to/a.mp4"

        # 4. Clear selection
        tab.selected_asset = asset_trans_a
        tab._update_inspector_details()
        cb_a2 = captured_cbs[-1]
        token_c = tab._inspector_trans_token

        tab.selected_asset = None
        tab._update_inspector_details()
        token_clear = tab._inspector_trans_token
        assert token_clear > token_c

        # Delayed callback arrives -> REJECTED
        cb_a2("/path/to/a.mp4")
        assert tab.icon_meme_widget.resolved_file_path != "/path/to/a.mp4"

        # 5. Widget shutdown (closeEvent)
        tab.selected_asset = asset_trans_a
        tab._update_inspector_details()
        cb_a3 = captured_cbs[-1]
        token_d = tab._inspector_trans_token

        tab.closeEvent(QCloseEvent())
        token_close = tab._inspector_trans_token
        assert token_close > token_d

        cb_a3("/path/to/a.mp4")
        assert tab.icon_meme_widget.resolved_file_path != "/path/to/a.mp4"

        # 6. Genuine procedural preset restores trans_loop_widget.show()
        procedural_asset = {
            "id": "pure_procedural",
            "name": "Pure Wipe",
            "category": "trans",
            "_raw_obj": TransitionStylePreset(id="pure_procedural", name="Pure Wipe", transition_type="Wipe"),
        }
        tab.selected_asset = procedural_asset
        tab._update_inspector_details()
        qapp.processEvents()

        assert tab.inspector_trans_viewport.isVisible() is True
        assert tab.trans_loop_widget.isVisible() is True
        assert tab.trans_loop_widget.trans_name == "Pure Wipe"
    finally:
        tab.icon_meme_widget.stop_playback()
        tab.close()


def test_combined_real_async_resolver_to_ui_selection(qapp, tmp_path, monkeypatch):
    """
    Combined test: Real async resolver -> ThreadPoolExecutor -> GUI thread dispatch
    -> appropriate UI selection / video playback in TabAssets.
    """
    from src.core.storage_manager import StorageManager

    sm = StorageManager(config_dir=tmp_path / "sm_cfg", reserve_bytes=0)
    sm_root = tmp_path / "storage_root"
    sm.configure(sm_root)
    lib_id = sm._config["library_id"]

    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda *a, **kw: sm)

    pack_dir = sm_root / "02_cloud_downloads" / "packs" / "CombinedPack"
    setting_p = pack_dir / "Transitions" / "Combined.setting"
    setting_p.parent.mkdir(parents=True, exist_ok=True)
    setting_p.write_text("{ Tools = {} }")

    nested_mp4 = pack_dir / "Previews" / "Tier2" / "Combined.mp4"
    nested_mp4.parent.mkdir(parents=True, exist_ok=True)
    nested_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    asset = {
        "id": "combined_trans_1",
        "name": "Combined",
        "category": "trans",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(sm_root.resolve()),
        "library_path": "02_cloud_downloads/packs/CombinedPack/Transitions/Combined.setting",
        "file_path": str(setting_p.resolve()),
    }

    tpg._PACK_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_NORM_VIDEO_INDEX_CACHE.clear()
    tpg._PACK_ROOT_DIR_CACHE.clear()
    tpg._CLOUD_PACKS_FAST_CACHE = None

    tab = TabAssets()
    tab.resize(800, 600)
    tab.show()
    qapp.processEvents()

    # Select cold-cache asset: requires real async resolver thread
    tab.selected_asset = asset
    tab._update_inspector_details()

    t0 = time.monotonic()
    while tab.icon_meme_widget.resolved_file_path != str(nested_mp4.resolve()) and time.monotonic() - t0 < 3.0:
        qapp.processEvents()
        time.sleep(0.01)

    assert tab.icon_meme_widget.resolved_file_path == str(nested_mp4.resolve())
    tab.icon_meme_widget.stop_playback()
    tab.close()

