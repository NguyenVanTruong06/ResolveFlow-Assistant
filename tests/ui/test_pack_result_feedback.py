"""
Kiểm thử phản hồi kết quả nhập kho (Ingest Result Feedback UX) trên TabAssets:
- Starter pack callback hiển thị thành công bình thường khi snapshot_synced=True hoặc missing
- Starter pack callback hiển thị cảnh báo bộ đệm offline khi snapshot_synced=False mà không ném lỗi
- Batch dropped packs tổng hợp số gói chưa đồng bộ cache offline
- Batch dropped packs xử lý partial khi pack 1 thành công + pack 2 cancel hoặc pack 2 error
"""

import os
from pathlib import Path
import pytest
from PySide6.QtWidgets import QDialog

from src.core.storage_manager import StorageManager
from src.ui.tabs.tab_assets import TabAssets


@pytest.fixture
def clean_tab_assets(qapp, tmp_path, monkeypatch):
    """Khởi tạo TabAssets độc lập được gắn StorageManager tạm thời, không telemetry."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    tab = TabAssets()
    tab.storage = storage

    # Mock _rescan_assets để chạy nhanh và không phụ thuộc hệ thống file DaVinci
    monkeypatch.setattr(tab, "_rescan_assets", lambda: None)

    yield tab

    if hasattr(tab, "toast_timer") and tab.toast_timer.isActive():
        tab.toast_timer.stop()
    tab.close()


def test_starter_pack_toast_normal_when_synced_true(clean_tab_assets, monkeypatch):
    """Starter pack hiển thị toast thành công chuẩn mực khi snapshot_synced=True."""
    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 8, "snapshot_synced": True})
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._on_starter_pack_clicked()
    toast = clean_tab_assets.lbl_toast.text()
    assert "✅ Đã nhập thành công 8 đạo cụ Starter Pack (SFX & LUTs) vào Kho!" in toast
    assert "Bộ đệm offline chưa đồng bộ" not in toast


def test_starter_pack_toast_normal_when_synced_missing_legacy(clean_tab_assets, monkeypatch):
    """Starter pack hiển thị toast thành công bình thường khi thiếu trường snapshot_synced (legacy compat)."""
    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 8})  # missing flag
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._on_starter_pack_clicked()
    toast = clean_tab_assets.lbl_toast.text()
    assert "✅ Đã nhập thành công 8 đạo cụ Starter Pack (SFX & LUTs) vào Kho!" in toast
    assert "Bộ đệm offline chưa đồng bộ" not in toast


def test_starter_pack_toast_warning_when_synced_false(clean_tab_assets, monkeypatch):
    """Starter pack hiển thị cảnh báo bộ đệm offline khi snapshot_synced=False mà không báo thất bại."""
    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({
                "count": 8,
                "snapshot_synced": False,
                "warnings": ["Bộ đệm offline chưa được đồng bộ do đĩa đầy"]
            })
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._on_starter_pack_clicked()
    toast = clean_tab_assets.lbl_toast.text()
    assert "✅ Đã nhập thành công 8 đạo cụ Starter Pack (SFX & LUTs) vào Kho!" in toast
    assert "Bộ đệm offline chưa đồng bộ" in toast
    assert "❌" not in toast


def test_batch_dropped_packs_toast_all_synced(clean_tab_assets, tmp_path, monkeypatch):
    """Nhập hàng loạt thành công toàn bộ hiển thị số lượng nguồn đầy đủ."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"data1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"data2")

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            self.pack_committed.emit({"count": 2, "snapshot_synced": True})
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._import_files_or_folders([str(p1), str(p2)])
    toast = clean_tab_assets.lbl_toast.text()
    assert "✅ Đã nhập thành công 2 nguồn vào kho!" in toast
    assert "chưa đồng bộ bộ đệm offline" not in toast


def test_batch_dropped_packs_toast_with_unsynced_warning(clean_tab_assets, tmp_path, monkeypatch):
    """Nhập hàng loạt thành công nhưng có gói snapshot_synced=False hiển thị số gói chưa đồng bộ."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"data1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"data2")

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    calls = [0]
    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            calls[0] += 1
            # Gói 1 offline fail, gói 2 ok
            synced = False if calls[0] == 1 else True
            self.pack_committed.emit({"count": 2, "snapshot_synced": synced})
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._import_files_or_folders([str(p1), str(p2)])
    toast = clean_tab_assets.lbl_toast.text()
    assert "✅ Đã nhập thành công 2 nguồn vào kho!" in toast
    assert "1 gói chưa đồng bộ bộ đệm offline" in toast


def test_batch_dropped_packs_partial_cancel_with_unsynced_warning(clean_tab_assets, tmp_path, monkeypatch):
    """Gói 1 publish (nhưng snapshot fail) + Gói 2 cancel -> Báo cáo partial 1/2 và cảnh báo snapshot."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"data1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"data2")

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    calls = [0]
    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            calls[0] += 1
            if calls[0] == 1:
                self.pack_committed.emit({"count": 2, "snapshot_synced": False})
                return QDialog.DialogCode.Accepted
            else:
                return QDialog.DialogCode.Rejected

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._import_files_or_folders([str(p1), str(p2)])
    toast = clean_tab_assets.lbl_toast.text()
    assert "ℹ️ Đã nhập 1/2 nguồn" in toast
    assert "1 gói chưa đồng bộ bộ đệm offline" in toast
    assert "đã dừng hàng đợi" in toast


def test_batch_dropped_packs_partial_error_with_unsynced_warning(clean_tab_assets, tmp_path, monkeypatch):
    """Gói 1 publish (snapshot fail) + Gói 2 error -> Báo cáo partial 1/2, lỗi gói 2 và cảnh báo snapshot."""
    p1 = tmp_path / "pack1.zip"; p1.write_bytes(b"data1")
    p2 = tmp_path / "pack2.zip"; p2.write_bytes(b"data2")

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_cls = wizard_module.PackIngestWizard

    calls = [0]
    class MockWizard(orig_cls):
        def _prepare(self): pass
        def exec(self):
            calls[0] += 1
            if calls[0] == 1:
                self.pack_committed.emit({"count": 2, "snapshot_synced": False})
                return QDialog.DialogCode.Accepted
            else:
                raise RuntimeError("Lỗi đĩa cứng I/O")

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    clean_tab_assets._import_files_or_folders([str(p1), str(p2)])
    toast = clean_tab_assets.lbl_toast.text()
    assert "ℹ️ Đã nhập 1/2 nguồn" in toast
    assert "1 gói chưa đồng bộ bộ đệm offline" in toast
    assert "pack2.zip" in toast
    assert "Lỗi đĩa cứng I/O" in toast
    assert "đã dừng hàng đợi" in toast
    assert "✅ Đã nhập thành công" not in toast
