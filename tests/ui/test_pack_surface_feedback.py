"""
Kiểm thử phản hồi kết quả nhập kho (Surface Result Feedback UX) trên EdgeDock và TabAssetHub:
- EdgeDock:
  + Tất cả pack thành công bình thường (snapshot synced hoặc missing legacy) -> toast thành công chuẩn
  + Tổng hợp snapshot False + True -> toast thành công kèm ghi chú cảnh báo bộ đệm offline
  + Pack 1 False + Pack 2 Cancel -> Dừng queue (pack 3 không mở), refresh pack 1, toast partial 1/3 kèm note
  + Pack 1 False + Pack 2 Error -> Dừng queue (pack 3 không mở), refresh pack 1, toast partial 1/3 kèm lỗi pack 2 và note
  + 0 success + Error -> Báo lỗi, không báo thành công ảo
  + Guard _ingest_in_progress và _active_ingest_wizard reset sạch sau mọi kịch bản
- TabAssetHub:
  + Pack commit với snapshot_synced=True -> Không hiện QMessageBox.warning
  + Pack commit với missing flag legacy -> Không hiện QMessageBox.warning
  + Pack commit với snapshot_synced=False -> Hiện QMessageBox.warning("Gói đã nhập vào kho; bộ đệm offline chưa đồng bộ.")
  + Wizard bị cancel/reject -> Không hiện cảnh báo, không refresh kho
  + Refresh signal được kích hoạt đúng 1 lần từ library_changed, không nhân đôi
"""

import os
import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QWidget, QDialog, QMessageBox

from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.tabs.tab_asset_hub import AssetHubWidget
from src.ui.widgets import pack_ingest_wizard as wizard_module


class MinimalDockHost(QWidget):
    """Host tối giản kiểm thử trực tiếp hàm EdgeDockWindow._handle_dropped_packs mà không spawn telemetry/hotkeys."""
    def __init__(self):
        super().__init__()
        self._ingest_in_progress = False
        self._active_ingest_wizard = None
        self._toast = None
        self.refreshed_count = 0

    def refresh_storage_assets(self):
        self.refreshed_count += 1

MinimalDockHost._handle_dropped_packs = EdgeDockWindow._handle_dropped_packs


class MinimalHubHost(QWidget):
    """Host tối giản kiểm thử trực tiếp hàm AssetHubWidget._review_pack mà không spawn full Studio window."""
    def __init__(self):
        super().__init__()
        self.refresh_calls = 0

    def _library_refreshed(self):
        self.refresh_calls += 1

MinimalHubHost._review_pack = AssetHubWidget._review_pack


# ==========================================
# CÁC TEST CHO EDGEDOCK
# ==========================================

def test_dock_drop_all_normal_synced(qapp, tmp_path, monkeypatch):
    """Dock: Tất cả 3 pack thành công (synced=True hoặc missing), không hiện warning note."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"p1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"p2")
    p3 = tmp_path / "pack3.zip"; p3.write_bytes(b"p3")

    orig_cls = wizard_module.PackIngestWizard
    opened = []

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            src = self.input_source.text()
            opened.append(src)
            # p1 synced True, p2 legacy missing, p3 synced True
            synced = True if "pack2" not in src else None
            payload = {"count": 3}
            if synced is not None:
                payload["snapshot_synced"] = synced
            self.pack_committed.emit(payload)
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1), str(p2), str(p3)])

    assert len(opened) == 3
    assert host.refreshed_count == 1
    assert host._toast is not None
    msg = host._toast.lbl_msg.text()
    assert "Đã nhập 3 gói vào kho!" in msg
    assert "chưa đồng bộ" not in msg
    assert host._toast.lbl_icon.text() == "✓"
    assert host._ingest_in_progress is False
    assert host._active_ingest_wizard is None
    host.close()


def test_dock_drop_false_and_true_aggregate(qapp, tmp_path, monkeypatch):
    """Dock: 2 pack thành công, 1 pack có snapshot_synced=False -> Tổng hợp cảnh báo 1 gói."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"p1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"p2")

    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            src = self.input_source.text()
            synced = False if "pack1" in src else True
            self.pack_committed.emit({"count": 5, "snapshot_synced": synced})
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1), str(p2)])

    assert host.refreshed_count == 1
    assert host._toast is not None
    msg = host._toast.lbl_msg.text()
    assert "Đã nhập 2 gói vào kho!" in msg
    assert "⚠️ 1 gói chưa đồng bộ bộ đệm offline" in msg
    assert host._ingest_in_progress is False
    host.close()


def test_dock_drop_false_and_cancel2_stops_queue(qapp, tmp_path, monkeypatch):
    """Dock: 3 paths, pack 1 thành công (False), pack 2 bị hủy -> Dừng queue, pack 3 không mở, báo 1/3 kèm note."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"p1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"p2")
    p3 = tmp_path / "pack3.zip"; p3.write_bytes(b"p3")

    orig_cls = wizard_module.PackIngestWizard
    opened = []

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            src = self.input_source.text()
            opened.append(src)
            if "pack1" in src:
                self.pack_committed.emit({"count": 4, "snapshot_synced": False})
                return QDialog.DialogCode.Accepted
            else:
                return QDialog.DialogCode.Rejected

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1), str(p2), str(p3)])

    assert len(opened) == 2
    assert str(p3) not in opened, "Pack 3 tuyệt đối không được mở khi pack 2 bị hủy!"
    assert host.refreshed_count == 1, "Phải refresh asset của pack 1 đã commit!"
    assert host._toast is not None
    msg = host._toast.lbl_msg.text()
    assert "1/3" in msg
    assert "dừng hàng đợi" in msg
    assert "⚠️ 1 gói chưa đồng bộ bộ đệm offline" in msg
    assert "Lỗi" not in msg
    assert host._toast.lbl_icon.text() == "ℹ"
    assert host._ingest_in_progress is False
    assert host._active_ingest_wizard is None
    host.close()


def test_dock_drop_false_and_error2_partial_refresh(qapp, tmp_path, monkeypatch):
    """Dock: 3 paths, pack 1 thành công (False), pack 2 ném Exception -> Dừng queue, pack 3 không mở, refresh pack 1, toast partial kèm lỗi."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"p1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"p2")
    p3 = tmp_path / "pack3.zip"; p3.write_bytes(b"p3")

    orig_cls = wizard_module.PackIngestWizard
    opened = []

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            src = self.input_source.text()
            opened.append(src)
            if "pack1" in src:
                self.pack_committed.emit({"count": 2, "snapshot_synced": False})
                return QDialog.DialogCode.Accepted
            elif "pack2" in src:
                raise RuntimeError("Tệp zip bị hỏng")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1), str(p2), str(p3)])

    assert len(opened) == 2
    assert str(p3) not in opened, "Pack 3 tuyệt đối không được mở khi pack 2 gặp lỗi!"
    assert host.refreshed_count == 1, "Phải refresh asset của pack 1 dù pack 2 bị lỗi!"
    assert host._toast is not None
    msg = host._toast.lbl_msg.text()
    assert "1/3" in msg
    assert "dừng hàng đợi" in msg
    assert "Tệp zip bị hỏng" in msg
    assert "⚠️ 1 gói chưa đồng bộ bộ đệm offline" in msg
    assert host._toast.lbl_icon.text() == "ℹ"
    assert host._ingest_in_progress is False
    assert host._active_ingest_wizard is None
    host.close()


def test_dock_drop_zero_success_failure(qapp, tmp_path, monkeypatch):
    """Dock: Gói đầu tiên bị lỗi (0 success) -> Báo lỗi, không báo thành công ảo, không refresh."""
    p1 = tmp_path / "bad_pack.zip"; p1.write_bytes(b"bad")

    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            raise ValueError("Không đọc được định dạng")

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1)])

    assert host.refreshed_count == 0
    assert host._toast is not None
    msg = host._toast.lbl_msg.text()
    assert "Không đọc được định dạng" in msg
    assert "Đã nhập" not in msg
    assert host._ingest_in_progress is False
    assert host._active_ingest_wizard is None
    host.close()


def test_dock_drop_guard_reset_on_cancel(qapp, tmp_path, monkeypatch):
    """Dock: Hủy ngay từ gói 1 (0 success cancel) -> Reset guards sạch sẽ, không báo success ảo."""
    p1 = tmp_path / "pack.zip"; p1.write_bytes(b"data")

    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalDockHost()
    host._handle_dropped_packs([str(p1)])

    assert host.refreshed_count == 0
    assert host._ingest_in_progress is False
    assert host._active_ingest_wizard is None
    # Không hiện toast thành công ảo
    if host._toast is not None:
        assert "Đã nhập" not in host._toast.lbl_msg.text()
    host.close()


# ==========================================
# CÁC TEST CHO TABASSETHUB
# ==========================================

def test_hub_review_pack_snapshot_true_no_warning(qapp, monkeypatch):
    """Hub: Pack commit thành công với snapshot_synced=True -> Không hiện cảnh báo, refresh 1 lần."""
    orig_cls = wizard_module.PackIngestWizard
    warn_calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warn_calls.append(a))

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 5, "snapshot_synced": True})
            self.library_changed.emit()
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalHubHost()
    host._review_pack(source="dummy_pack.zip")

    assert len(warn_calls) == 0, "Không được hiện QMessageBox.warning khi snapshot_synced=True!"
    assert host.refresh_calls == 1
    host.close()


def test_hub_review_pack_snapshot_missing_no_warning(qapp, monkeypatch):
    """Hub: Pack commit với missing flag legacy -> Không hiện cảnh báo, refresh 1 lần."""
    orig_cls = wizard_module.PackIngestWizard
    warn_calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warn_calls.append(a))

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 5})
            self.library_changed.emit()
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalHubHost()
    host._review_pack(source="legacy_pack.zip")

    assert len(warn_calls) == 0, "Không được hiện QMessageBox.warning khi thiếu snapshot_synced!"
    assert host.refresh_calls == 1
    host.close()


def test_hub_review_pack_snapshot_false_shows_warning(qapp, monkeypatch):
    """Hub: Pack commit với snapshot_synced=False -> Hiện QMessageBox.warning nêu rõ bộ đệm offline chưa đồng bộ."""
    orig_cls = wizard_module.PackIngestWizard
    warn_calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warn_calls.append((title, text)))

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 3, "snapshot_synced": False})
            self.library_changed.emit()
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalHubHost()
    host._review_pack(source="offline_pack.zip")

    assert len(warn_calls) == 1, "Phải hiện đúng 1 cảnh báo QMessageBox.warning!"
    title, text = warn_calls[0]
    assert "Gói đã nhập vào kho; bộ đệm offline chưa đồng bộ." in text
    assert host.refresh_calls == 1
    host.close()


def test_hub_review_pack_rejected_or_cancel_no_warning(qapp, monkeypatch):
    """Hub: Người dùng hủy dialog -> Không hiện cảnh báo, không refresh."""
    orig_cls = wizard_module.PackIngestWizard
    warn_calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warn_calls.append(a))

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    host = MinimalHubHost()
    host._review_pack(source="canceled_pack.zip")

    assert len(warn_calls) == 0
    assert host.refresh_calls == 0
    host.close()
