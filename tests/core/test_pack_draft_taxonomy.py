"""Giao dịch taxonomy draft: filesystem cô lập, không chạm kho người dùng."""
import errno
from contextlib import contextmanager

import pytest

from src.core.pack_ingest import PackIngestService
from src.core.storage_manager import StorageManager, atomic_json


@pytest.fixture
def draft_job(tmp_path):
    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "library")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"media")
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    job["draft_taxonomy"] = {"product_review": "Review Công Nghệ"}
    job["assets"][0]["category"] = "product_review"
    return sm, svc, job


def test_commit_publishes_draft_and_pack(draft_job):
    sm, svc, job = draft_job
    result = svc.commit(job)
    assert result["count"] == 1 and result["snapshot_synced"]
    assert sm.taxonomy()["product_review"] == "Review Công Nghệ"
    asset = sm.registry()["assets"][0]
    assert asset["category"] == "product_review"
    assert "draft_taxonomy" not in asset


@pytest.mark.parametrize("draft", [None, [], {"all": "X"}, {"BAD": "X"}, {"valid": 3}])
def test_invalid_draft_rejected_before_mutation(draft_job, draft):
    sm, svc, job = draft_job
    job["draft_taxonomy"] = draft
    before = (sm.meta / "taxonomy.json").read_bytes()
    with pytest.raises(ValueError):
        svc.commit(job)
    assert (sm.meta / "taxonomy.json").read_bytes() == before
    assert not sm.registry()["packs"]


@pytest.mark.parametrize("fault", ["taxonomy", "registry"])
def test_failed_publish_restores_taxonomy_and_payload(draft_job, monkeypatch, fault):
    sm, svc, job = draft_job
    sm.add_category("external_cat", "Bên ngoài")
    before = sm.taxonomy()
    from src.core import pack_ingest
    real_atomic = pack_ingest.atomic_json

    def fail(path, value):
        if path == sm.meta / f"{fault}.json":
            raise OSError(errno.EIO, "publish fault")
        real_atomic(path, value)

    monkeypatch.setattr(pack_ingest, "atomic_json", fail)
    with pytest.raises(OSError, match="publish fault"):
        svc.commit(job)
    assert sm.taxonomy() == before
    assert (sm.meta / "staging" / job["job_dir"].split("/")[-1] / "payload" / "clip.mp4").exists()
    assert not sm.registry()["packs"]


def test_snapshot_failure_keeps_published_taxonomy(draft_job, monkeypatch):
    sm, svc, job = draft_job
    monkeypatch.setattr(sm, "snapshot", lambda: (_ for _ in ()).throw(OSError("snapshot full")))
    result = svc.commit(job)
    assert not result["snapshot_synced"] and result["warnings"]
    assert "product_review" in sm.taxonomy()
    assert len(sm.registry()["packs"]) == 1


@pytest.mark.parametrize("label, succeeds", [("Review Công Nghệ", True), ("Nhãn khác", False)])
def test_latest_taxonomy_under_lock_preserved(draft_job, monkeypatch, label, succeeds):
    sm, svc, job = draft_job
    original_lock = sm.lock

    @contextmanager
    def concurrent_lock():
        with original_lock():
            tax = sm.taxonomy()
            tax.update(external_cat="Giữ nguyên", product_review=label)
            atomic_json(sm.meta / "taxonomy.json", tax)
            yield

    monkeypatch.setattr(sm, "lock", concurrent_lock)
    if succeeds:
        svc.commit(job)
    else:
        with pytest.raises(ValueError, match="tồn tại"):
            svc.commit(job)
    assert sm.taxonomy()["external_cat"] == "Giữ nguyên"
    assert sm.taxonomy()["product_review"] == label


@pytest.mark.parametrize("rollback_fault", ["taxonomy", "folder"])
def test_double_fault_keeps_primary_and_reports_incomplete(draft_job, monkeypatch, rollback_fault):
    sm, svc, job = draft_job
    from src.core import pack_ingest
    real_atomic, real_rename = pack_ingest.atomic_json, pack_ingest.os.rename
    tax_calls = 0

    def fail_atomic(path, value):
        nonlocal tax_calls
        if path == sm.meta / "registry.json":
            raise OSError("PRIMARY registry")
        if path == sm.meta / "taxonomy.json":
            tax_calls += 1
            if tax_calls == 2 and rollback_fault == "taxonomy":
                raise OSError("ROLLBACK taxonomy")
        real_atomic(path, value)

    def fail_rename(src, dst):
        if rollback_fault == "folder" and str(dst).endswith("/payload"):
            raise OSError("ROLLBACK folder")
        return real_rename(src, dst)

    monkeypatch.setattr(pack_ingest, "atomic_json", fail_atomic)
    monkeypatch.setattr(pack_ingest.os, "rename", fail_rename)
    with pytest.raises(RuntimeError, match="khôi phục") as err:
        svc.commit(job)
    assert "PRIMARY registry" in str(err.value.__cause__)
    assert "ROLLBACK" in str(err.value)
    assert not sm.registry()["packs"]
    if rollback_fault == "taxonomy":
        from pathlib import Path
        assert (Path(job["job_dir"]) / "payload").exists()
    else:
        assert "product_review" not in sm.taxonomy()


def test_external_taxonomy_change_not_blindly_restored(draft_job, monkeypatch):
    sm, svc, job = draft_job
    from src.core import pack_ingest
    real_atomic = pack_ingest.atomic_json

    def fail(path, value):
        if path == sm.meta / "registry.json":
            tax = sm.taxonomy()
            tax["external_race"] = "Bảo toàn"
            real_atomic(sm.meta / "taxonomy.json", tax)
            raise OSError("PRIMARY")
        real_atomic(path, value)

    monkeypatch.setattr(pack_ingest, "atomic_json", fail)
    with pytest.raises(RuntimeError, match="khôi phục"):
        svc.commit(job)
    assert sm.taxonomy()["external_race"] == "Bảo toàn"


def test_empty_draft_does_not_write_taxonomy(draft_job, monkeypatch):
    sm, svc, job = draft_job
    job["draft_taxonomy"] = {}
    job["assets"][0]["category"] = "broll"
    from src.core import pack_ingest
    real_atomic = pack_ingest.atomic_json

    def verify(path, value):
        assert path != sm.meta / "taxonomy.json"
        real_atomic(path, value)

    monkeypatch.setattr(pack_ingest, "atomic_json", verify)
    svc.commit(job)


@pytest.mark.parametrize("bad_tax", ["{broken", "[]", '{"sfx": 123}'])
def test_corrupt_taxonomy_not_overwritten(draft_job, bad_tax):
    sm, svc, job = draft_job
    path = sm.meta / "taxonomy.json"
    path.write_text(bad_tax)
    with pytest.raises(ValueError):
        svc.commit(job)
    assert path.read_text() == bad_tax
    assert not sm.registry()["packs"]


def test_cancel_after_lock_acquired_does_not_publish(draft_job, monkeypatch):
    sm, svc, job = draft_job
    original_lock = sm.lock
    cancelled = False

    @contextmanager
    def locked():
        nonlocal cancelled
        with original_lock():
            cancelled = True
            yield

    monkeypatch.setattr(sm, "lock", locked)
    with pytest.raises(InterruptedError):
        svc.commit(job, cancel=lambda: cancelled)
    assert "product_review" not in sm.taxonomy()
    assert not sm.registry()["packs"]


def test_identity_change_inside_lock_does_not_write(draft_job, monkeypatch):
    sm, svc, job = draft_job
    original_lock = sm.lock

    @contextmanager
    def locked():
        with original_lock():
            sm._config["library_id"] = "changed"
            atomic_json(sm.meta / "library.json", {"library_id": "changed"})
            yield

    monkeypatch.setattr(sm, "lock", locked)
    from src.core.storage_manager import StorageOfflineError
    with pytest.raises(StorageOfflineError):
        svc.commit(job)
    assert not sm.registry()["packs"]
    assert "product_review" not in sm.taxonomy()
