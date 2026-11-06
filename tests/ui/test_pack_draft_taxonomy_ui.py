"""Category chỉ tồn tại trong RAM cho tới khi user commit."""
import time
from PySide6.QtWidgets import QInputDialog, QMessageBox
from src.core.storage_manager import StorageManager
from src.ui.widgets.pack_ingest_wizard import PackIngestWizard


def wait_idle(wizard, qapp):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
        if wizard.worker is None:
            return
    raise AssertionError(f"worker timeout: {wizard._outcome}")


def add_category(wizard, monkeypatch, cid="product_review", label="Review Công Nghệ"):
    answers = iter([(cid, True), (label, True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: next(answers))
    wizard._add_category()


def test_add_cancel_does_not_touch_disk(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "lib")
    before = (sm.meta / "taxonomy.json").read_bytes()
    snapshot = sm._snapshot_path().read_bytes()
    wizard = PackIngestWizard(sm)
    try:
        add_category(wizard, monkeypatch)
        assert (sm.meta / "taxonomy.json").read_bytes() == before
        assert sm._snapshot_path().read_bytes() == snapshot
        assert wizard.category.findData("product_review") >= 0
        delegate = wizard.table.itemDelegateForColumn(2)
        editor = delegate.createEditor(wizard.table, None, None)
        assert editor.findData("product_review") >= 0
        wizard.reject()
        assert not wizard._draft_taxonomy
    finally:
        wizard.close()


def test_ui_commit_persists_draft(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "lib")
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"media")
    wizard = PackIngestWizard(sm, source=str(source))
    try:
        wizard._prepare()
        wait_idle(wizard, qapp)
        assert wizard.job is not None, wizard._outcome
        add_category(wizard, monkeypatch)
        wizard.model.assets[0]["category"] = "product_review"
        wizard._commit()
        wait_idle(wizard, qapp)
        assert wizard.is_pack_committed(), wizard._outcome
        assert sm.taxonomy()["product_review"] == "Review Công Nghệ"
        assert sm.registry()["assets"][0]["category"] == "product_review"
    finally:
        wizard.reject()
        wait_idle(wizard, qapp)
        wizard.close()


def test_invalid_id_and_existing_id_do_not_mutate(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "lib")
    wizard = PackIngestWizard(sm)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    try:
        add_category(wizard, monkeypatch, "all")
        assert not wizard._draft_taxonomy
        add_category(wizard, monkeypatch, "sfx", "Không ghi đè")
        assert not wizard._draft_taxonomy
        assert wizard.category.currentData() == "sfx"
    finally:
        wizard.close()


def test_failed_commit_retains_draft_and_duplicate_labels(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "lib")
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"media")
    wizard = PackIngestWizard(sm, source=str(source))
    try:
        wizard._prepare()
        wait_idle(wizard, qapp)
        add_category(wizard, monkeypatch, "cat_a", "Tư liệu")
        add_category(wizard, monkeypatch, "cat_b", "Tư liệu")
        assert "cat_a" in wizard.category.itemText(wizard.category.findData("cat_a"))
        assert "cat_b" in wizard.category.itemText(wizard.category.findData("cat_b"))
        wizard.model.assets[0]["category"] = "cat_a"
        from src.core import pack_ingest
        real_atomic = pack_ingest.atomic_json

        def fail(path, value):
            if path == sm.meta / "registry.json":
                raise OSError("registry write failed")
            real_atomic(path, value)

        with monkeypatch.context() as local:
            local.setattr(pack_ingest, "atomic_json", fail)
            wizard._commit()
            wait_idle(wizard, qapp)
        assert not wizard.is_pack_committed()
        assert wizard._draft_taxonomy == {"cat_a": "Tư liệu", "cat_b": "Tư liệu"}
        assert wizard.job["assets"][0]["category"] == "cat_a"
        assert "cat_a" not in sm.taxonomy()
        wizard._commit()
        wait_idle(wizard, qapp)
        assert wizard.is_pack_committed(), wizard._outcome
    finally:
        wizard.reject()
        wait_idle(wizard, qapp)
        wizard.close()
