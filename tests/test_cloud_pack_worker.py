import pytest
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from src.core.pack_manager import PackManager, PackManifest
from src.ui.tabs.cloud_pack_worker import CloudPackInstallWorker


def test_cloud_pack_worker_local_install(tmp_path: Path):
    # Setup a local test pack
    meme_file = tmp_path / "funny.mp4"
    meme_file.write_text("dummy")
    pack_path = tmp_path / "test.rfpack"
    manifest = PackManifest(
        id="test_cloud_pack",
        name="Test Cloud Pack",
        version="1.0.0",
        author="Tester",
        description="Demo",
        categories=["meme"]
    )
    PackManager.create_pack(str(pack_path), manifest, {"meme": [str(meme_file)]})

    # Test worker installing existing file
    target_lib = tmp_path / "target_lib"
    worker = CloudPackInstallWorker(
        pack_manifest=manifest,
        direct_file_path=str(pack_path),
        target_assets_dir=str(target_lib)
    )

    signals_received = []
    worker.progress_signal.connect(lambda pct, msg: signals_received.append((pct, msg)))

    finished_result = []
    worker.finished_signal.connect(lambda ok, msg, counts: finished_result.append((ok, msg, counts)))

    worker.run()

    assert len(finished_result) == 1
    ok, msg, counts = finished_result[0]
    assert ok is True
    assert counts.get("meme") == 1
    assert (target_lib / "broll_memes" / "memes" / "funny.mp4").exists()


def test_cloud_pack_worker_cancellation(tmp_path: Path):
    manifest = PackManifest(id="cancel_test", name="Cancel Test")
    worker = CloudPackInstallWorker(pack_manifest=manifest)
    worker.cancel()
    assert worker.is_cancelled is True
