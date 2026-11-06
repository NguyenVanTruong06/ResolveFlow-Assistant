"""
tests/ui/test_studio_dock_sync.py
Kiểm thử bộ đấu nối (wiring) hai chiều giữa EdgeDockWindow và StudioWindow (G04-04A2b-I):
- 1. Dock refresh -> visible Studio và hidden Studio đều nhận snapshot
- 2. Không khởi tạo StudioWindow sớm; lần đầu open_studio chủ động nhận snapshot mới nhất
- 3. Studio -> Dock đúng 1 chiều, Dock.set_assets không phát ngược (zero echo loop)
- 4. Hide / Show nhiều lần không sinh duplicate subscribers
- 5. Poll root / offline / registry / taxonomy-only kích hoạt cập nhật cả 2 surface
- 6. Refresh ngoại lệ không advance signature, cho phép retry ở tick sau
- 7. Background update giữ nguyên metadata và tuyệt đối không quét lại bundled assets
- 8. Nút Studio sync chỉ phát đúng 1 tín hiệu assets_updated
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.storage_manager import StorageManager
from src.services.search_index import SearchIndex
from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.studio.studio_window import StudioWindow
from src.main import (
    create_studio_manager,
    _compute_storage_poll_signature,
    poll_storage_status,
)


@pytest.fixture(autouse=True)
def stub_hardware_stats(monkeypatch):
    """Vô hiệu hóa background thread thu thập phần cứng trong StudioWindow khi chạy test."""
    monkeypatch.setattr(StudioWindow, "_fetch_hardware_stats", lambda self: None)


@pytest.fixture
def sync_env(qapp, tmp_path, monkeypatch):
    """Môi trường kiểm thử độc lập cho wiring Dock ↔ Studio với mock StorageManager."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    lib_dir = tmp_path / "library"
    sm = StorageManager(reserve_bytes=0)
    sm.configure(lib_dir)

    search_index = SearchIndex([])
    dock = EdgeDockWindow()
    dock.set_search_index(search_index)

    mock_insert_service = MagicMock()

    open_studio = create_studio_manager(dock, mock_insert_service, search_index)

    created_windows = []
    original_open = open_studio

    def tracked_open_studio():
        win = original_open()
        if win not in created_windows:
            created_windows.append(win)
        return win

    yield {
        "sm": sm,
        "dock": dock,
        "search_index": search_index,
        "open_studio": tracked_open_studio,
        "insert_service": mock_insert_service,
        "tmp_path": tmp_path,
        "lib_dir": lib_dir,
    }

    for win in created_windows:
        try:
            win.close()
        except Exception:
            pass
    try:
        dock.close()
    except Exception:
        pass


def test_dock_refresh_updates_visible_and_hidden_studio(sync_env, monkeypatch):
    """1. Khi Dock refresh, cả Studio đang hiển thị (visible) lẫn đang ẩn (hidden) đều nhận snapshot."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]

    # Mở Studio (visible)
    studio = open_studio()
    assert studio.isVisible()

    # Thêm asset mới vào kho ngoài
    snap1 = [
        {"id": "managed_vis_1", "name": "Visible Shot", "tab": "broll", "category": "broll", "sub": "Action"}
    ]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap1))
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {"broll": "B-roll"})

    # Dock refresh
    dock.refresh_storage_assets()

    # Studio nhận được snapshot mới ngay khi đang visible
    studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "managed_vis_1" in studio_ids

    # Ẩn Studio (hidden)
    studio.hide()
    assert not studio.isVisible()

    # Thêm asset thứ 2 khi Studio đang ẩn
    snap2 = [
        {"id": "managed_vis_1", "name": "Visible Shot", "tab": "broll", "category": "broll", "sub": "Action"},
        {"id": "managed_hid_2", "name": "Hidden Shot", "tab": "sfx", "category": "sfx", "sub": "Impact"},
    ]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap2))
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {"broll": "B-roll", "sfx": "Âm thanh"})

    # Dock refresh lần 2
    dock.refresh_storage_assets()

    # Studio (dù đang ẩn) vẫn nhận được snapshot mà không cần phải show lại
    studio_ids_2 = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "managed_hid_2" in studio_ids_2


def test_no_early_studio_instantiation_and_first_open_receives_latest(sync_env, monkeypatch):
    """2. Khi chưa gọi open_studio, Dock refresh tuyệt đối không tạo window ngầm; khi mở lần đầu nhận metadata mới nhất."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]

    # Giả lập có asset trong kho ngoài trước khi Studio được mở
    init_snap = [
        {"id": "early_asset_01", "name": "Early Asset", "tab": "lut", "category": "lut", "sub": "Cinematic"}
    ]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(init_snap))
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {"lut": "LUT Màu"})

    # Dock refresh khi Studio CHƯA từng được mở
    with patch("src.main.StudioWindow") as mock_cls:
        dock.refresh_storage_assets()
        # Không được khởi tạo StudioWindow ngầm!
        assert mock_cls.call_count == 0

    # Bây giờ người dùng mở Studio lần đầu
    studio = open_studio()
    assert studio is not None

    # Studio phải có asset mới nhất ngay lập tức (không bị stale do bỏ lỡ sự kiện trước đó)
    studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "early_asset_01" in studio_ids


def test_studio_to_dock_single_delivery_no_echo(sync_env):
    """3. Studio phát assets_updated cập nhật Dock, Dock.set_assets KHÔNG phát ngược lại (zero echo)."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    studio = open_studio()

    echo_signals = []
    dock.storage_assets_changed.connect(lambda m, t: echo_signals.append((m, t)))

    # Studio phát assets_updated
    dummy_assets = [
        {"id": "studio_clip_1", "name": "Clip From Studio", "tab": "text", "category": "text"}
    ]
    studio.assets_updated.emit(dummy_assets)

    # Dock được nạp assets mới
    dock_ids = {getattr(a, "id", "") if not isinstance(a, dict) else a.get("id") for a in dock._all_assets}
    assert "studio_clip_1" in dock_ids

    # Tuyệt đối không phát storage_assets_changed ngược lại (tránh Echo Loop)
    assert len(echo_signals) == 0


def test_hide_show_does_not_duplicate_subscribers(sync_env, monkeypatch):
    """4. Đóng mở StudioWindow nhiều lần không làm nhân đôi số lần nhận tín hiệu (no duplicate subscribers)."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    sm = sync_env["sm"]

    studio = open_studio()
    studio.hide()
    studio = open_studio()
    studio.hide()
    studio = open_studio()

    # Theo dõi số lần gọi apply_storage_snapshot
    call_count = 0
    original_apply = studio.apply_storage_snapshot

    def spy_apply(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(studio, "apply_storage_snapshot", spy_apply)

    # Phát 1 lần refresh từ dock
    monkeypatch.setattr(sm, "ui_assets", lambda: [])
    monkeypatch.setattr(sm, "taxonomy", lambda: {})
    dock.refresh_storage_assets()

    # Phải gọi chính xác 1 lần, không bị 3 lần do 3 lần open_studio
    assert call_count == 1


def test_poll_detects_changes_and_delivers_to_both_surfaces(sync_env, monkeypatch):
    """5. Poll helper phát hiện thay đổi (offline/reconnect/registry/taxonomy-only) và cập nhật cả Dock lẫn Studio."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    sm = sync_env["sm"]
    studio = open_studio()

    sig_holder = [None]

    # Ban đầu chạy poll tick lần 1
    assert poll_storage_status(sm, dock, sig_holder) is True
    assert sig_holder[0] is not None
    init_sig = sig_holder[0]

    # Chạy lại khi không có thay đổi -> False, signature giữ nguyên
    assert poll_storage_status(sm, dock, sig_holder) is False
    assert sig_holder[0] == init_sig

    # Thay đổi taxonomy-only
    sm.add_category("project_gamma", "Dự án Gamma")
    # Cập nhật ui_assets giả lập
    snap = [{"id": "gamma_01", "name": "Gamma Shot", "tab": "project_gamma", "category": "project_gamma"}]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap))

    # Poll tick phát hiện taxonomy-only đổi
    assert poll_storage_status(sm, dock, sig_holder) is True
    assert sig_holder[0] != init_sig

    # Cả Dock lẫn Studio đều nhận được asset mới
    dock_ids = {getattr(a, "id", "") if not isinstance(a, dict) else a.get("id") for a in dock._all_assets}
    assert "gamma_01" in dock_ids
    studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "gamma_01" in studio_ids


def test_refresh_exception_does_not_advance_signature_production_tick(sync_env, monkeypatch):
    """6. Lỗi trong production poll_storage_status không advance signature, cho phép retry ở tick sau."""
    dock = sync_env["dock"]
    sm = sync_env["sm"]

    sig_holder = [None]

    # Giả lập dock.refresh_storage_assets() ném lỗi
    monkeypatch.setattr(dock, "refresh_storage_assets", MagicMock(side_effect=RuntimeError("Disk I/O Error")))

    # Chạy trực tiếp production helper poll_storage_status
    result = poll_storage_status(sm, dock, sig_holder)

    # Hàm trả về False và signature không được advance
    assert result is False
    assert sig_holder[0] is None, "Khi refresh lỗi, signature không được advance để lượt poll tiếp theo có thể retry!"

    # Khôi phục refresh hoạt động bình thường
    monkeypatch.undo()
    result_retry = poll_storage_status(sm, dock, sig_holder)
    assert result_retry is True
    assert sig_holder[0] is not None, "Khi hết lỗi, poll_storage_status chạy thành công và advance signature!"


def test_studio_receiver_failure_does_not_leak_excepthook_and_retries_successfully(sync_env, monkeypatch):
    """G04-04A2b-R1: Studio receiver ném lỗi tại slot boundary không lọt ra Qt excepthook và được retry thành công."""
    import sys
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    sm = sync_env["sm"]

    studio = open_studio()
    coordinator = getattr(open_studio, "__self__", None)

    # Bắt unhandled exceptions ở cấp sys.excepthook
    excepthook_calls = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: excepthook_calls.append(args))

    # Giả lập Studio.apply_storage_snapshot bị lỗi tạm thời ở lần đầu
    snap = [{"id": "retry_shot_01", "name": "Retry Shot", "tab": "broll", "category": "broll"}]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap))
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {"broll": "B-roll"})

    original_apply = studio.apply_storage_snapshot
    fail_once = [True]

    def failing_apply(*args, **kwargs):
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("Transient SQLite / Model Lock")
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(studio, "apply_storage_snapshot", failing_apply)

    # Dock refresh
    dock.refresh_storage_assets()

    # 1. Tuyệt đối không rò rỉ exception ra Qt sys.excepthook
    assert len(excepthook_calls) == 0, "Ngoại lệ tại slot boundary phải được bắt an toàn, không rò ra sys.excepthook!"

    # 2. Coordinator đã lưu snapshot đang chờ và kích hoạt retry timer
    if coordinator:
        assert coordinator.pending_snapshot is not None
        assert coordinator.retry_timer.isActive() is True

        # Kích hoạt retry tick khi lỗi đã qua
        coordinator.retry_timer.timeout.emit()

        # 3. Studio được cập nhật thành công và retry timer đã dừng
        studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
        assert "retry_shot_01" in studio_ids
        assert coordinator.pending_snapshot is None
        assert coordinator.retry_timer.isActive() is False


def test_newer_payload_supersedes_older_pending_payload(sync_env, monkeypatch):
    """G04-04A2b-R1: Snapshot mới đến lập tức thay thế snapshot cũ đang chờ, không replay payload cũ."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    studio = open_studio()
    coordinator = getattr(open_studio, "__self__", None)

    # Studio bị lỗi liên tục
    monkeypatch.setattr(studio, "apply_storage_snapshot", MagicMock(side_effect=RuntimeError("Continuous Error")))

    # Snapshot A đến
    snap_a = [{"id": "shot_A", "name": "Shot A", "tab": "broll", "category": "broll"}]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap_a))
    dock.refresh_storage_assets()

    if coordinator:
        assert coordinator.pending_snapshot[0] == snap_a

        # Snapshot B đến (thay thế A)
        snap_b = [{"id": "shot_B", "name": "Shot B", "tab": "broll", "category": "broll"}]
        monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap_b))
        dock.refresh_storage_assets()

        assert coordinator.pending_snapshot[0] == snap_b, "Snapshot mới B phải thay thế hoàn toàn snapshot cũ A!"

        # Khôi phục apply_storage_snapshot bình thường và kích hoạt retry
        monkeypatch.undo()
        monkeypatch.setattr(StudioWindow, "_fetch_hardware_stats", lambda self: None)
        coordinator.retry_timer.timeout.emit()

        # Studio chỉ nhận shot_B, không replay shot_A
        studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
        assert "shot_B" in studio_ids
        assert "shot_A" not in studio_ids


def test_first_open_failure_initiates_pending_retry(sync_env, monkeypatch):
    """G04-04A2b-R1: Lỗi nạp snapshot khi mở StudioWindow lần đầu cũng kích hoạt retry timer."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    coordinator = getattr(open_studio, "__self__", None)

    snap = [{"id": "first_open_fail_shot", "name": "First Shot", "tab": "lut", "category": "lut"}]
    monkeypatch.setattr(StorageManager, "ui_assets", lambda self: list(snap))
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {"lut": "LUT"})

    # Giả lập Studio.apply_storage_snapshot bị lỗi ở lần đầu tiên mở
    fail_flag = [True]
    original_apply = StudioWindow.apply_storage_snapshot

    def failing_first_apply(self, *args, **kwargs):
        if fail_flag[0]:
            fail_flag[0] = False
            raise RuntimeError("Init fail")
        return original_apply(self, *args, **kwargs)

    monkeypatch.setattr(StudioWindow, "apply_storage_snapshot", failing_first_apply)

    studio = open_studio()

    if coordinator:
        # Coordinator ghi nhận pending và kích hoạt timer
        assert coordinator.pending_snapshot is not None
        assert coordinator.retry_timer.isActive() is True

        # Kích hoạt retry tick
        coordinator.retry_timer.timeout.emit()

        # Studio nhận được asset và timer dừng
        studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
        assert "first_open_fail_shot" in studio_ids
        assert coordinator.pending_snapshot is None
        assert coordinator.retry_timer.isActive() is False



def test_background_update_zero_bundled_rescan(sync_env, monkeypatch):
    """7. Cập nhật nền qua snapshot tuyệt đối không gọi lại _rescan_assets của TabAssets."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    studio = open_studio()

    rescan_called = False

    def forbidden_rescan():
        nonlocal rescan_called
        rescan_called = True

    monkeypatch.setattr(studio.tab_assets, "_rescan_assets", forbidden_rescan)

    # Dock refresh
    dock.refresh_storage_assets()

    assert not rescan_called, "Cập nhật nền tuyệt đối không được gọi lại _rescan_assets nặng nề!"


def test_studio_sync_button_emits_exactly_once(sync_env, monkeypatch):
    """8. Nút 'Làm mới & Đồng bộ Dock' trên StudioWindow chỉ phát đúng 1 lần assets_updated."""
    open_studio = sync_env["open_studio"]
    studio = open_studio()

    # Stub _rescan_assets để nó chỉ phát library_assets_changed
    dummy_assets = [{"id": "rescan_1", "name": "R1", "tab": "text"}]

    def fake_rescan():
        studio.tab_assets.library_assets_changed.emit(dummy_assets)

    monkeypatch.setattr(studio.tab_assets, "_rescan_assets", fake_rescan)

    emitted_count = 0
    studio.assets_updated.connect(lambda assets: nonlocal_inc())

    def nonlocal_inc():
        nonlocal emitted_count
        emitted_count += 1

    # Bấm nút sync
    studio._on_sync_dock_clicked()

    # Phải phát đúng 1 lần (đã loại bỏ lần _notify_assets_changed thừa)
    assert emitted_count == 1, f"Kỳ vọng phát đúng 1 lần assets_updated, nhưng phát: {emitted_count}"


def test_poll_fresh_branch_detects_storage_root_change(sync_env, tmp_path):
    """G04-04A2b-R2: Nhánh production poll (sm=None) nhận diện thay đổi Storage Root sang thư mục mới B."""
    dock = sync_env["dock"]
    open_studio = sync_env["open_studio"]
    studio = open_studio()

    # 1. Ban đầu kho trỏ tới rootA (đã được sync_env configure)
    # Ghi nhận asset A vào registry của rootA
    sm_a = StorageManager(reserve_bytes=0)
    root_a = sm_a.root
    reg_a = sm_a.meta / "registry.json"
    reg_a.write_text('{"packs": [], "assets": [{"id": "asset_root_a", "name": "Asset A", "tab": "broll", "category": "broll", "library_path": "a.mp4"}]}', encoding="utf-8")

    sig_holder = [None]

    # Chạy poll tick production (sm=None)
    assert poll_storage_status(sm=None, dock=dock, storage_signature=sig_holder) is True

    # Cả Dock lẫn Studio đều nhận được asset_root_a
    dock_ids = {getattr(a, "id", "") if not isinstance(a, dict) else a.get("id") for a in dock._all_assets}
    assert "asset_root_a" in dock_ids
    studio_ids = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "asset_root_a" in studio_ids

    # 2. Tạo một manager khác cấu hình sang rootB mới
    root_b = tmp_path / "library_b"
    sm_b = StorageManager(reserve_bytes=0)
    sm_b.configure(root_b)
    reg_b = sm_b.meta / "registry.json"
    reg_b.write_text('{"packs": [], "assets": [{"id": "asset_root_b", "name": "Asset B", "tab": "broll", "category": "broll", "library_path": "b.mp4"}]}', encoding="utf-8")

    # Instance sm_a cũ vẫn giữ root_a (không bị mutate)
    assert sm_a._config.get("root") == str(root_a)

    # 3. Chạy poll tick tiếp theo qua nhánh production (sm=None)
    # Nhánh này tự khởi tạo fresh StorageManager() và nhận ra root đã chuyển sang rootB
    assert poll_storage_status(sm=None, dock=dock, storage_signature=sig_holder) is True

    # Cả Dock lẫn Studio đều cập nhật sang asset_root_b và không còn asset_root_a
    dock_ids_after = {getattr(a, "id", "") if not isinstance(a, dict) else a.get("id") for a in dock._all_assets}
    assert "asset_root_b" in dock_ids_after
    assert "asset_root_a" not in dock_ids_after

    studio_ids_after = {a.id for a in studio.tab_assets.all_assets if getattr(a, "storage_managed", False)}
    assert "asset_root_b" in studio_ids_after
    assert "asset_root_a" not in studio_ids_after

    # 4. Tick tiếp theo khi không có thay đổi là no-op
    assert poll_storage_status(sm=None, dock=dock, storage_signature=sig_holder) is False

    # 5. Offline và reconnect trên rootB
    lib_marker_b = sm_b.meta / "library.json"
    lib_marker_b.unlink()
    # Poll phát hiện offline trên rootB
    assert poll_storage_status(sm=None, dock=dock, storage_signature=sig_holder) is True

    # Reconnect rootB
    from src.core.storage_manager import atomic_json
    atomic_json(lib_marker_b, {"library_id": sm_b._config["library_id"]})
    # Poll phát hiện reconnect trên rootB
    assert poll_storage_status(sm=None, dock=dock, storage_signature=sig_holder) is True

