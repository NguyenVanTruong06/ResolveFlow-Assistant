import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.asset_indexer import AssetIndexer
from src.core.asset_manager import AssetManager
from src.core.pack_ingest import PackIngestService, guess
from src.core.pack_manager import COMMUNITY_PACKS_CATALOG, PackManager
from src.core.storage_manager import StorageManager
from src.services.insert_service import InsertService, to_asset_dict


def test_pack_ingest_guess_recognizes_dctl_and_excludes_dividers():
    cat1, exc1 = guess(Path("PFA-SkinMatch2/PFA-SkinMatch2.dctle"))
    assert cat1 == "luts"
    assert exc1 is False

    cat2, exc2 = guess(Path("MONO-Shaper-v1.1/MONO-SHAPER-RED-v1.1.dctl"))
    assert cat2 == "luts"
    assert exc2 is False

    cat3, exc3 = guess(Path("Filmverse/FilmVerse 2.0 - Method 1.drx"))
    assert cat3 == "luts"
    assert exc3 is False

    # Divider dummy files should be classified as luts but excluded from UI asset cards
    cat_div1, exc_div1 = guess(Path("PixelTools/--- PIXELTOOLS ---.dctle"))
    assert cat_div1 == "luts"
    assert exc_div1 is True

    cat_div2, exc_div2 = guess(Path("QAZI/== QAZI ==.dctle"))
    assert cat_div2 == "luts"
    assert exc_div2 is True

    cat_div3, exc_div3 = guess(Path("MONO/--------------------------------------.dctl"))
    assert cat_div3 == "luts"
    assert exc_div3 is True


def test_pack_ingest_commit_preserves_dctl_companions_and_sanitizes_colon_paths(tmp_path):
    lib_root = tmp_path / "KhoTest"
    sm = StorageManager(config_dir=tmp_path / "cfg", reserve_bytes=0)
    sm.configure(lib_root)

    zip_path = tmp_path / "test_dctl_pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Path with colon (macOS style) + companion .h header + divider + Vietnamese doc PDF
        zf.writestr("MonoNodes : V4/Shaper/MONO-SHAPER-RED.dctl", '#include "common.h"\n__DEVICE__ float3 transform() {}')
        zf.writestr("MonoNodes : V4/Shaper/common.h", "// helper header")
        zf.writestr("MonoNodes : V4/Shaper/--------------------------------------.dctl", "// divider")
        zf.writestr("MonoNodes : V4/docs/huong_dan_tieng_viet.pdf", b"%PDF-1.4 fake pdf")

    svc = PackIngestService(sm)
    job = svc.prepare(zip_path, pack_id="test_mononodes_dctl", pack_name="Test MonoNodes DCTL")
    chosen = [a for a in job["assets"] if not a["excluded"]]
    assert len(chosen) == 1
    assert chosen[0]["category"] == "luts"
    assert chosen[0]["name"] == "MONO-SHAPER-RED"

    res = svc.commit(job)
    assert res["count"] == 1

    # Verify companion .h header, divider .dctl, and docs PDF were preserved on disk
    pack_dirs = list((lib_root / "02_cloud_downloads" / "packs").iterdir())
    assert len(pack_dirs) == 1
    pack_dir = pack_dirs[0]
    assert list(pack_dir.rglob("MONO-SHAPER-RED.dctl"))
    assert list(pack_dir.rglob("common.h"))
    assert list(pack_dir.rglob("--------------------------------------.dctl"))
    assert list(pack_dir.rglob("huong_dan_tieng_viet.pdf"))


def test_pack_manager_and_indexer_support_dctl_and_dctle(tmp_path):
    zip_path = tmp_path / "sample_dctl.rfpack"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("PFA/PFA-SkinMatch2.dctle", b"encrypted dctl payload")
        zf.writestr("PFA/MONO-Test.dctl", b"plain dctl payload")
        zf.writestr("PFA/--- PIXELTOOLS ---.dctle", b"")

    target_assets = tmp_path / "assets"
    counts = PackManager.install_pack(str(zip_path), target_base_dir=str(target_assets))
    assert counts.get("luts", 0) >= 2

    AssetIndexer.clear_cache()
    indexer = AssetIndexer(base_dir=str(target_assets))
    luts = indexer.scan_luts(force_refresh=True)
    names = {item["name"] for item in luts}
    assert "PFA-SkinMatch2" in names
    assert "MONO-Test" in names
    assert "--- PIXELTOOLS ---" not in names


def test_to_asset_dict_and_insert_service_install_dctl(tmp_path, monkeypatch):
    dctl_dir = tmp_path / "pack_dctl" / "Qazi_Skin"
    dctl_dir.mkdir(parents=True)
    dctl_file = dctl_dir / "QT_Skin_Compressor_v1.0.dctle"
    dctl_file.write_bytes(b"dctl binary content")
    companion_header = dctl_dir / "qazi_helper.h"
    companion_header.write_text("// companion header", encoding="utf-8")

    lut_install_root = tmp_path / "ResolveLUT" / "ResolveFlow"
    monkeypatch.setattr(AssetManager, "get_lut_install_dir", classmethod(lambda cls: lut_install_root))

    asset_dict = to_asset_dict({
        "id": "qt_skin_comp",
        "name": "QT_Skin_Compressor_v1.0",
        "file_path": str(dctl_file),
    })
    assert asset_dict["tab"] == "lut"
    assert asset_dict["category"] == "lut"

    mock_gateway = MagicMock()
    mock_proj = MagicMock()
    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_pm.GetCurrentProject.return_value = mock_proj
    mock_resolve.GetProjectManager.return_value = mock_pm

    def _submit_sync(task_fn, callback=None, errback=None):
        try:
            res = task_fn(mock_resolve)
            if callback:
                callback(res)
        except Exception as e:
            if errback:
                errback(str(e))

    mock_gateway.submit.side_effect = _submit_sync

    with patch("src.core.action_guard.check_action_availability") as mock_guard:
        from src.core.action_guard import ActionGuardResult
        mock_guard.return_value = ActionGuardResult(
            allowed=True,
            status="online",
            result_code="OK",
            reason="",
            resolved_path=str(dctl_file),
        )
        svc = InsertService(gateway=mock_gateway)
        result = svc.insert(asset_dict)

    assert result.ok is True
    assert "DCTL" in result.message
    installed_files = list(lut_install_root.rglob("QT_Skin_Compressor_v1.0.dctle"))
    assert len(installed_files) == 1
    assert len(list(lut_install_root.rglob("qazi_helper.h"))) == 1
    assert mock_proj.RefreshLUTList.called


def test_community_packs_catalog_contains_structured_sfx_and_dctl_packs():
    by_id = {p.id: p for p in COMMUNITY_PACKS_CATALOG}
    expected = {
        "urban_street_action_sfx": 140,
        "dctl_pfa_color_suite": 4,
        "dctl_pixeltools_bundle": 27,
        "dctl_filmverse_2_1": 15,
        "dctl_mononodes_v4": 149,
        "dctl_qazi_toolkit_vietsub": 26,
    }
    for pid, count in expected.items():
        assert pid in by_id, f"Missing {pid} in COMMUNITY_PACKS_CATALOG"
        pack = by_id[pid]
        assert pack.item_count == count
        assert pack.download_url and pack.download_url.startswith("https://github.com/NguyenVanTruong06/resolveflow-packs/releases/download/v1.0.0/")
        assert pack.size_mb and pack.size_mb > 0

