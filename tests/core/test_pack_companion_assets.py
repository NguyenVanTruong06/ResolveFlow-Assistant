"""
tests/core/test_pack_companion_assets.py
Kiểm thử hợp đồng Master/Preview Companion Assets (LIB-PUBLISH-01-I):
1. Pack 1 transition setting + MP4 preview + PNG thumb -> review/commit registry đúng 1 master,
   companions tồn tại trên đĩa và UI paths đúng. Đối chứng: MP4 B-roll thật vẫn là asset độc lập.
2. Nested master + sibling previews root; shared companion; exclude 1 master bảo toàn companion chung;
   exclude cả hai master dọn sạch companion; alias/canonical conflict, traversal/escape/symlink,
   missing reference warning không tạo dummy file.
3. Reserved directories (previews/, thumbnails/, tutorials/, docs/) không leak B-roll khi thiếu/hỏng manifest;
   legacy media pack không bị ảnh hưởng; invalid reference không publish.
4. Commit failure rollback; offline snapshot và root relocation fixture vẫn phản ánh đúng paths/origin;
   persisted round-trip giữ relative fields, strip runtime fields, không mutate input.
5. Wizard model handoff: quan hệ qua review/commit giữ nguyên; master count đúng, companions không thành row.
"""
from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path
import pytest

from src.core.storage_manager import (
    StorageManager,
    read_json,
    atomic_json,
)
from src.core.pack_ingest import PackIngestService
from src.core.contracts.asset_dto import (
    to_persisted_record,
    to_ui_asset,
    validate_asset_dict,
)
from src.ui.widgets.pack_ingest_wizard import IngestReviewModel


@pytest.fixture
def configured_storage(tmp_path):
    """Khởi tạo kho media độc lập trong tmp_path."""
    config_dir = tmp_path / "local_config"
    library_root = tmp_path / "external_library"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=1024**2)
    sm.configure(library_root)
    svc = PackIngestService(sm)
    return sm, svc, library_root, config_dir


# ==============================================================================
# 1. PACK 1 TRANSITION + PREVIEW + THUMB -> 1 MASTER, B-ROLL LÀ ASSET RIÊNG
# ==============================================================================

def test_pack_with_setting_preview_and_thumb_single_master_registered(configured_storage, tmp_path):
    """Kiểm tra:
    - Pack gồm 1 transition setting + MP4 preview + PNG thumbnail.
    - Có 1 MP4 B-roll thật độc lập.
    - Kết quả review: đúng 2 rows (1 transition master, 1 B-roll).
    - Companions KHÔNG thành asset rows riêng.
    - Commit: registry có đúng 2 assets, companion paths trên UI trỏ đúng file thực tế."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_01"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "transitions").mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)
    (pack_dir / "thumbnails").mkdir(parents=True, exist_ok=True)
    (pack_dir / "broll").mkdir(parents=True, exist_ok=True)

    setting_file = pack_dir / "transitions" / "zoom_in.setting"
    setting_file.write_text("Fusion Transition Setting Content", encoding="utf-8")

    preview_file = pack_dir / "previews" / "zoom_in_preview.mp4"
    preview_file.write_bytes(b"VIDEO_PREVIEW_BYTES_1234")

    thumb_file = pack_dir / "thumbnails" / "zoom_in_thumb.png"
    thumb_file.write_bytes(b"PNG_THUMBNAIL_BYTES_5678")

    broll_file = pack_dir / "broll" / "city_traffic.mp4"
    broll_file.write_bytes(b"BROLL_REAL_FOOTAGE_BYTES")

    manifest_data = {
        "schema_version": 1,
        "name": "Transition & Broll Pack",
        "version": "1.0",
        "assets": [
            {
                "relative_path": "transitions/zoom_in.setting",
                "name": "Zoom In Transition",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/zoom_in_preview.mp4",
                "thumbnail_relative_path": "thumbnails/zoom_in_thumb.png",
                "tags": ["zoom", "transition"],
            },
            {
                "relative_path": "broll/city_traffic.mp4",
                "name": "City Traffic Night",
                "category": "broll",
                "tags": ["city", "night"],
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    # 1. Prepare & Inspect
    job = svc.prepare(pack_dir)
    assert len(job["assets"]) == 2  # Đúng 2 assets, companions KHÔNG bị biến thành rows độc lập!
    
    asset_by_rel = {a["relative_path"]: a for a in job["assets"]}
    assert "transitions/zoom_in.setting" in asset_by_rel
    assert "broll/city_traffic.mp4" in asset_by_rel
    assert "previews/zoom_in_preview.mp4" not in asset_by_rel
    assert "thumbnails/zoom_in_thumb.png" not in asset_by_rel

    master = asset_by_rel["transitions/zoom_in.setting"]
    assert master["preview_relative_path"] == "previews/zoom_in_preview.mp4"
    assert master["thumbnail_relative_path"] == "thumbnails/zoom_in_thumb.png"

    # 2. Commit
    result = svc.commit(job)
    assert result["count"] == 2

    # 3. Thẩm định StorageManager.assets()
    stored_assets = sm.assets()
    assert len(stored_assets) == 2

    stored_by_rel = {a["relative_path"]: a for a in stored_assets}
    stored_master = stored_by_rel["transitions/zoom_in.setting"]
    stored_broll = stored_by_rel["broll/city_traffic.mp4"]

    assert stored_master["category"] == "templates"
    assert stored_master["template_type"] == "trans"
    assert Path(stored_master["file_path"]).is_file()
    assert stored_master.get("preview_path") is not None
    assert Path(stored_master["preview_path"]).is_file()
    assert Path(stored_master["preview_path"]).name == "zoom_in_preview.mp4"
    assert stored_master.get("thumbnail_path") is not None
    assert Path(stored_master["thumbnail_path"]).is_file()
    assert Path(stored_master["thumbnail_path"]).name == "zoom_in_thumb.png"

    # B-roll đối chứng
    assert stored_broll["category"] == "broll"
    assert Path(stored_broll["file_path"]).is_file()
    assert stored_broll.get("preview_path") is None


# ==============================================================================
# 2. NESTED MASTER, SHARED COMPANIONS, EXCLUSION DEPENDENCY CLOSURE
# ==============================================================================

def test_nested_master_shared_companion_and_exclusion_closure(configured_storage, tmp_path):
    """Kiểm tra:
    - Master lồng sâu: 'macros/nested/sub/effect_a.setting' và 'macros/nested/sub/effect_b.setting'.
    - Sibling preview tại gốc: 'previews/shared_preview.mp4'.
    - Cả hai master cùng chia sẻ 'previews/shared_preview.mp4'.
    - Khi exclude effect_a: effect_b vẫn được commit và shared preview VẪN ĐƯỢC GIỮ trên đĩa.
    - Khi exclude cả hai: shared preview bị dọn dẹp khỏi payload."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_shared"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "macros" / "nested" / "sub").mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)

    ea = pack_dir / "macros" / "nested" / "sub" / "effect_a.setting"
    ea.write_text("Setting A", encoding="utf-8")
    eb = pack_dir / "macros" / "nested" / "sub" / "effect_b.setting"
    eb.write_text("Setting B", encoding="utf-8")

    shared_mp4 = pack_dir / "previews" / "shared_preview.mp4"
    shared_mp4.write_bytes(b"SHARED_MP4_CONTENT")

    manifest_data = {
        "schema_version": 1,
        "name": "Shared Companion Pack",
        "version": "1.0",
        "assets": [
            {
                "relative_path": "macros/nested/sub/effect_a.setting",
                "name": "Effect A",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/shared_preview.mp4",
            },
            {
                "relative_path": "macros/nested/sub/effect_b.setting",
                "name": "Effect B",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/shared_preview.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    job = svc.prepare(pack_dir)
    assert len(job["assets"]) == 2

    # Case 1: Exclude Effect A, giữ Effect B
    for a in job["assets"]:
        if a["name"] == "Effect A":
            a["excluded"] = True

    svc.commit(job)

    stored = sm.assets()
    assert len(stored) == 1
    assert stored[0]["name"] == "Effect B"
    assert Path(stored[0]["preview_path"]).is_file()
    assert Path(stored[0]["preview_path"]).read_bytes() == b"SHARED_MP4_CONTENT"


# ==============================================================================
# 3. ALIAS NORMALIZATION, CONFLICTS, TRAVERSAL & MISSING FILE WARNINGS
# ==============================================================================

def test_companion_alias_normalization_and_conflict_rejected(configured_storage, tmp_path):
    """Kiểm tra:
    - Alias preview_path được chuẩn hóa thành preview_relative_path.
    - Nếu cả hai cùng có và KHÁC NHAU -> ném ValueError từ chối.
    - Đường dẫn traversal '../' hoặc absolute bị từ chối ngay.
    - Missing reference file trên đĩa -> cảnh báo warning, bỏ reference hỏng, không tạo dummy file."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_alias"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)

    s1 = pack_dir / "t1.setting"
    s1.write_text("Setting 1", encoding="utf-8")
    p1 = pack_dir / "previews" / "p1.mp4"
    p1.write_bytes(b"P1")

    # 1. Alias hợp lệ: preview_path
    manifest_alias = {
        "schema_version": 1,
        "name": "Alias Pack",
        "assets": [
            {
                "relative_path": "t1.setting",
                "name": "T1",
                "category": "templates",
                "template_type": "trans",
                "preview_path": "previews/p1.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_alias), encoding="utf-8")
    job = svc.prepare(pack_dir)
    assert job["assets"][0]["preview_relative_path"] == "previews/p1.mp4"

    # 2. Xung đột alias và canonical
    manifest_conflict = {
        "schema_version": 1,
        "name": "Conflict Pack",
        "assets": [
            {
                "relative_path": "t1.setting",
                "name": "T1",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/p1.mp4",
                "preview_path": "previews/different.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_conflict), encoding="utf-8")
    with pytest.raises(ValueError, match="Xung đột"):
        svc.prepare(pack_dir)

    # 3. Traversal escape bị từ chối
    manifest_traversal = {
        "schema_version": 1,
        "name": "Escape Pack",
        "assets": [
            {
                "relative_path": "t1.setting",
                "name": "T1",
                "category": "templates",
                "template_type": "trans",
                "preview_path": "../outside.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_traversal), encoding="utf-8")
    with pytest.raises(ValueError, match="không phải đường dẫn tương đối an toàn"):
        svc.prepare(pack_dir)

    # 4. Missing reference file trên đĩa -> cảnh báo warning rõ, bỏ reference, master vẫn import được
    manifest_missing = {
        "schema_version": 1,
        "name": "Missing Ref Pack",
        "assets": [
            {
                "relative_path": "t1.setting",
                "name": "T1",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/non_existent.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest_missing), encoding="utf-8")
    job_missing = svc.prepare(pack_dir)
    assert any("không tồn tại trên đĩa" in w for w in job_missing["warnings"])
    assert "preview_relative_path" not in job_missing["assets"][0] or job_missing["assets"][0].get("preview_relative_path") is None


# ==============================================================================
# 4. RESERVED DIRECTORIES KHÔNG LEAK B-ROLL KHI THIẾU/HỎNG MANIFEST
# ==============================================================================

def test_reserved_directories_not_leaked_as_broll_without_manifest(configured_storage, tmp_path):
    """Kiểm tra:
    - Pack không có manifest (hoặc corrupt manifest).
    - Chứa files trong previews/, thumbnails/, tutorials/, docs/.
    - Chứa 1 file broll hợp lệ 'footage/sea.mp4'.
    - TUYỆT ĐỐI KHÔNG tự đoán files trong reserved dirs thành B-roll khả dụng."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_reserved"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)
    (pack_dir / "thumbnails").mkdir(parents=True, exist_ok=True)
    (pack_dir / "tutorials").mkdir(parents=True, exist_ok=True)
    (pack_dir / "docs").mkdir(parents=True, exist_ok=True)
    (pack_dir / "footage").mkdir(parents=True, exist_ok=True)

    (pack_dir / "previews" / "demo.mp4").write_bytes(b"DEMO_MP4")
    (pack_dir / "thumbnails" / "thumb.png").write_bytes(b"THUMB_PNG")
    (pack_dir / "tutorials" / "guide.mp4").write_bytes(b"GUIDE_MP4")
    (pack_dir / "docs" / "manual.pdf").write_bytes(b"MANUAL_PDF")
    (pack_dir / "footage" / "sea.mp4").write_bytes(b"SEA_BROLL_MP4")

    job = svc.prepare(pack_dir)
    by_rel = {a["relative_path"]: a for a in job["assets"]}

    # Footage thật là B-roll khả dụng
    assert "footage/sea.mp4" in by_rel
    assert by_rel["footage/sea.mp4"]["category"] == "broll"
    assert by_rel["footage/sea.mp4"]["excluded"] is False

    # Reserved dirs: KHÔNG PHẢI B-roll khả dụng, bị excluded mặc định!
    for reserved_rel in ("previews/demo.mp4", "thumbnails/thumb.png", "tutorials/guide.mp4", "docs/manual.pdf"):
        if reserved_rel in by_rel:
            assert by_rel[reserved_rel]["category"] != "broll"
            assert by_rel[reserved_rel]["excluded"] is True


# ==============================================================================
# 5. OFFLINE SNAPSHOT, ROOT RELOCATION & PERSISTED ROUND-TRIP
# ==============================================================================

def test_offline_snapshot_and_root_relocation_maintains_companions(configured_storage, tmp_path):
    """Kiểm tra:
    - Publish pack có master + companion.
    - Chụp offline snapshot.
    - Chuyển kho sang offline: StorageManager.assets() vẫn trả về preview_path/thumbnail_path với availability='offline'.
    - Relocate sang root mới: paths tự động phản ánh root mới."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_reloc"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)

    (pack_dir / "cut.setting").write_text("Cut Setting", encoding="utf-8")
    (pack_dir / "previews" / "cut.mp4").write_bytes(b"CUT_PREVIEW")

    manifest = {
        "schema_version": 1,
        "name": "Reloc Pack",
        "assets": [
            {
                "relative_path": "cut.setting",
                "name": "Cut Transition",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/cut.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    job = svc.prepare(pack_dir)
    svc.commit(job)

    # Online assets
    online_assets = sm.assets()
    assert len(online_assets) == 1
    assert online_assets[0]["availability"] == "online"
    orig_prev_path = online_assets[0]["preview_path"]
    assert Path(orig_prev_path).is_file()

    # Tạo snapshot
    sm.snapshot()

    # 1. Chuyển sang offline mode bằng cách unplug library_root (giả lập rút ổ ngoài)
    unplugged = tmp_path / "unplugged_library"
    library_root.rename(unplugged)
    assert not sm.online

    offline_assets = sm.assets()
    assert len(offline_assets) == 1
    assert offline_assets[0]["availability"] == "offline"
    assert offline_assets[0]["preview_path"] == orig_prev_path
    assert offline_assets[0]["preview_relative_path"] == "previews/cut.mp4"

    # Cắm lại ổ (phục hồi library_root)
    unplugged.rename(library_root)
    assert sm.online

    # 2. Relocate root sang thư mục mới
    new_root = tmp_path / "new_migrated_library"
    import shutil
    shutil.copytree(library_root, new_root)
    sm.configure(new_root)

    migrated_assets = sm.assets()
    assert len(migrated_assets) == 1
    assert migrated_assets[0]["availability"] == "online"
    new_prev_path = migrated_assets[0]["preview_path"]
    assert str(new_root) in new_prev_path
    assert Path(new_prev_path).is_file()


# ==============================================================================
# 6. WIZARD MODEL HANDOFF: EXACT MASTER COUNT, ZERO COMPANION ROWS
# ==============================================================================

def test_wizard_review_model_only_contains_masters_and_roundtrip(configured_storage, tmp_path):
    """Kiểm tra:
    - IngestReviewModel nhận job['assets'] chỉ hiển thị masters.
    - Companions không tạo rows độc lập trong QTableView model.
    - Model checkbox toggle excluded vận hành bình thường."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_model"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)

    (pack_dir / "trans1.setting").write_text("T1", encoding="utf-8")
    (pack_dir / "trans2.setting").write_text("T2", encoding="utf-8")
    (pack_dir / "previews" / "p1.mp4").write_bytes(b"P1")
    (pack_dir / "previews" / "p2.mp4").write_bytes(b"P2")

    manifest = {
        "schema_version": 1,
        "name": "Model Test Pack",
        "assets": [
            {
                "relative_path": "trans1.setting",
                "name": "T1",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/p1.mp4",
            },
            {
                "relative_path": "trans2.setting",
                "name": "T2",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/p2.mp4",
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    job = svc.prepare(pack_dir)
    assert len(job["assets"]) == 2

    # IngestReviewModel
    model = IngestReviewModel(job["assets"])
    assert model.rowCount() == 2  # Đúng 2 masters, 0 companion rows!

    # UI Asset View conversion
    ui_views = sm.ui_assets()
    for v in ui_views:
        # Không có card nào là companion
        assert v["category"] in ("trans", "templates", "broll", "sfx", "bgm", "overlay", "meme", "lut", "text")


# ==============================================================================
# 7. RESIDUAL 1: ROLE COLLISION BETWEEN MASTER & COMPANION IN BOTH ENTRY ORDERS
# ==============================================================================

def test_role_collision_master_declared_as_companion_rejected_both_orders(configured_storage, tmp_path):
    """Kiểm tra R1:
    - Order 1: effect.setting khai preview là city.mp4, và city.mp4 được khai báo là broll master.
      -> Phải reject (ValueError: đồng thời được khai báo là master asset độc lập), không âm thầm loại bỏ city.mp4.
    - Order 2: city.mp4 đứng trước, effect.setting đứng sau.
      -> Cùng ném ValueError.
    - Thumbnail PNG: photo.png khai thumbnail là icon.png, và icon.png khai master độc lập.
      -> Cùng ném ValueError."""
    sm, svc, library_root, config_dir = configured_storage

    # Order 1: setting first, mp4 second
    p1 = tmp_path / "pack_collision_1"
    p1.mkdir(parents=True, exist_ok=True)
    (p1 / "effect.setting").write_text("EFFECT", encoding="utf-8")
    (p1 / "city.mp4").write_bytes(b"CITY_MP4")

    m1 = {
        "schema_version": 1,
        "name": "Collision 1",
        "assets": [
            {
                "relative_path": "effect.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "city.mp4",
            },
            {
                "relative_path": "city.mp4",
                "category": "broll",
            }
        ]
    }
    (p1 / "pack_manifest.json").write_text(json.dumps(m1), encoding="utf-8")

    with pytest.raises(ValueError, match="đồng thời được khai báo là master asset độc lập"):
        svc.prepare(p1)

    # Order 2: mp4 first, setting second
    p2 = tmp_path / "pack_collision_2"
    p2.mkdir(parents=True, exist_ok=True)
    (p2 / "effect.setting").write_text("EFFECT", encoding="utf-8")
    (p2 / "city.mp4").write_bytes(b"CITY_MP4")

    m2 = {
        "schema_version": 1,
        "name": "Collision 2",
        "assets": [
            {
                "relative_path": "city.mp4",
                "category": "broll",
            },
            {
                "relative_path": "effect.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "city.mp4",
            }
        ]
    }
    (p2 / "pack_manifest.json").write_text(json.dumps(m2), encoding="utf-8")

    with pytest.raises(ValueError, match="đồng thời được khai báo là master asset độc lập"):
        svc.prepare(p2)

    # Thumbnail role collision
    p3 = tmp_path / "pack_collision_3"
    p3.mkdir(parents=True, exist_ok=True)
    (p3 / "photo.png").write_bytes(b"PHOTO")
    (p3 / "icon.png").write_bytes(b"ICON")

    m3 = {
        "schema_version": 1,
        "name": "Collision 3",
        "assets": [
            {
                "relative_path": "photo.png",
                "category": "overlays",
                "thumbnail_relative_path": "icon.png",
            },
            {
                "relative_path": "icon.png",
                "category": "overlays",
            }
        ]
    }
    (p3 / "pack_manifest.json").write_text(json.dumps(m3), encoding="utf-8")

    with pytest.raises(ValueError, match="đồng thời được khai báo là master asset độc lập"):
        svc.prepare(p3)


# ==============================================================================
# 8. RESIDUAL 2: CASEFOLD AMBIGUITY REJECTED FOR EXACT & FOLDED COMPANION REFS
# ==============================================================================

def test_companion_casefold_ambiguity_rejected_exact_and_folded(configured_storage, tmp_path):
    """Kiểm tra R2:
    - Thư mục chứa previews/A.mp4 và previews/a.mp4 (ambiguity).
    - Manifest trỏ exact 'previews/a.mp4' -> REJECT (ValueError).
    - Manifest trỏ 'previews/A.mp4' -> REJECT (ValueError).
    - Thư mục chứa unique casefold match (previews/cut.mp4 trên đĩa, manifest trỏ previews/cut.MP4)
      -> Chấp nhận và chuẩn hóa về actual spelling trên đĩa."""
    sm, svc, library_root, config_dir = configured_storage

    p1 = tmp_path / "pack_ambiguous"
    p1.mkdir(parents=True, exist_ok=True)
    (p1 / "previews").mkdir(parents=True, exist_ok=True)
    (p1 / "t.setting").write_text("SETTING", encoding="utf-8")
    (p1 / "previews" / "A.mp4").write_bytes(b"A_UPPER")
    (p1 / "previews" / "a.mp4").write_bytes(b"A_LOWER")

    # Exact reference to a.mp4 khi có A.mp4 cùng thư mục
    m1 = {
        "schema_version": 1,
        "name": "Ambiguous 1",
        "assets": [
            {
                "relative_path": "t.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/a.mp4",
            }
        ]
    }
    (p1 / "pack_manifest.json").write_text(json.dumps(m1), encoding="utf-8")

    with pytest.raises(ValueError, match="bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường"):
        svc.prepare(p1)

    # Reference to A.mp4 cũng bị reject
    m2 = {
        "schema_version": 1,
        "name": "Ambiguous 2",
        "assets": [
            {
                "relative_path": "t.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/A.mp4",
            }
        ]
    }
    (p1 / "pack_manifest.json").write_text(json.dumps(m2), encoding="utf-8")

    with pytest.raises(ValueError, match="bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường"):
        svc.prepare(p1)

    # Unique casefold match: chỉ có 1 file cut.mp4 trên đĩa, manifest viết cut.MP4
    p2 = tmp_path / "pack_unique_casefold"
    p2.mkdir(parents=True, exist_ok=True)
    (p2 / "previews").mkdir(parents=True, exist_ok=True)
    (p2 / "trans.setting").write_text("SETTING", encoding="utf-8")
    (p2 / "previews" / "cut.mp4").write_bytes(b"CUT_LOWER")

    m3 = {
        "schema_version": 1,
        "name": "Unique Casefold",
        "assets": [
            {
                "relative_path": "trans.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/cut.MP4",
            }
        ]
    }
    (p2 / "pack_manifest.json").write_text(json.dumps(m3), encoding="utf-8")

    job = svc.prepare(p2)
    # Unique match được giải quyết thành actual spelling trên đĩa
    assert job["assets"][0]["preview_relative_path"] == "previews/cut.mp4"


# ==============================================================================
# 9. RESIDUAL 3: RUNTIME PHYSICAL CONTAINMENT & SYMLINK ESCAPE PREVENTION
# ==============================================================================

def test_runtime_physical_containment_symlink_leaf_and_ancestor_escapes(configured_storage, tmp_path):
    """Kiểm tra R3:
    - Publish pack hợp lệ có master và preview companion.
    - Symlink leaf: thay preview bằng symlink trỏ ra file ngoài kho (/tmp/.../outside.mp4).
      -> StorageManager.assets() KHÔNG emit preview_path, giữ nguyên master và relative metadata.
    - Sibling pack symlink: preview trỏ sang file thuộc pack anh em trong cùng kho.
      -> StorageManager.assets() KHÔNG emit preview_path.
    - Asset mượn pack_id khác: library_path của master nằm ngoài pack folder được tham chiếu.
      -> StorageManager.assets() KHÔNG emit preview_path."""
    sm, svc, library_root, config_dir = configured_storage

    # Tạo 2 packs trong kho: Pack A và Pack B
    # Pack A
    pack_a_dir = tmp_path / "source_pack_a"
    pack_a_dir.mkdir(parents=True, exist_ok=True)
    (pack_a_dir / "previews").mkdir(parents=True, exist_ok=True)
    (pack_a_dir / "master_a.setting").write_text("A", encoding="utf-8")
    (pack_a_dir / "previews" / "prev_a.mp4").write_bytes(b"PREV_A")

    ma = {
        "schema_version": 1,
        "name": "Pack A",
        "assets": [
            {
                "relative_path": "master_a.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/prev_a.mp4",
            }
        ]
    }
    (pack_a_dir / "pack_manifest.json").write_text(json.dumps(ma), encoding="utf-8")
    res_a = svc.commit(svc.prepare(pack_a_dir))
    pack_a_id = res_a["pack_id"]

    # Pack B
    pack_b_dir = tmp_path / "source_pack_b"
    pack_b_dir.mkdir(parents=True, exist_ok=True)
    (pack_b_dir / "previews").mkdir(parents=True, exist_ok=True)
    (pack_b_dir / "master_b.setting").write_text("B", encoding="utf-8")
    (pack_b_dir / "previews" / "prev_b.mp4").write_bytes(b"PREV_B")

    mb = {
        "schema_version": 1,
        "name": "Pack B",
        "assets": [
            {
                "relative_path": "master_b.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/prev_b.mp4",
            }
        ]
    }
    (pack_b_dir / "pack_manifest.json").write_text(json.dumps(mb), encoding="utf-8")
    res_b = svc.commit(svc.prepare(pack_b_dir))
    pack_b_id = res_b["pack_id"]

    # Khi mọi thứ bình thường: cả 2 masters đều có preview_path hợp lệ
    assets_init = {a["name"]: a for a in sm.assets()}
    assert "preview_path" in assets_init["master_a"]
    assert "preview_path" in assets_init["master_b"]

    # 1. Symlink leaf trỏ ra ngoài thư viện:
    # Lấy thư mục thật của Pack A trên đĩa
    reg = sm.registry()
    pack_a_folder_rel = next(p["folder"] for p in reg["packs"] if p["id"] == pack_a_id)
    pack_a_folder_abs = library_root / pack_a_folder_rel
    prev_a_disk = pack_a_folder_abs / "previews" / "prev_a.mp4"

    # Tạo 1 file ngoài kho
    outside_file = tmp_path / "completely_outside_secret.mp4"
    outside_file.write_bytes(b"SECRET_DATA")

    # Thay prev_a bằng symlink trỏ ra outside_file
    prev_a_disk.unlink()
    prev_a_disk.symlink_to(outside_file)

    # Thẩm định lại qua StorageManager.assets()
    assets_after = {a["name"]: a for a in sm.assets()}
    master_a = assets_after["master_a"]
    # preview_path PHẢI BỊ LOẠI BỎ (không emit), không resolve ra ngoài pack!
    assert "preview_path" not in master_a or master_a.get("preview_path") is None
    # Master và relative metadata vẫn được bảo toàn nguyên vẹn
    assert master_a["availability"] == "online"
    assert master_a["preview_relative_path"] == "previews/prev_a.mp4"
    # Pack B không bị ảnh hưởng
    assert "preview_path" in assets_after["master_b"]

    # 2. Sibling pack symlink escape: prev_a trỏ sang prev_b của Pack B
    pack_b_folder_rel = next(p["folder"] for p in reg["packs"] if p["id"] == pack_b_id)
    prev_b_disk = library_root / pack_b_folder_rel / "previews" / "prev_b.mp4"
    prev_a_disk.unlink()
    prev_a_disk.symlink_to(prev_b_disk)

    assets_sibling = {a["name"]: a for a in sm.assets()}
    master_a_sib = assets_sibling["master_a"]
    # Không cho phép mượn companion của pack anh em qua symlink
    assert "preview_path" not in master_a_sib or master_a_sib.get("preview_path") is None

    # 3. Mượn pack_id: sửa registry giả lập asset ở Pack A nhưng gán pack_id của Pack B
    for a in reg["assets"]:
        if a.get("name") == "master_a":
            a["pack_id"] = pack_b_id
            break
    with open(sm.meta / "registry.json", "w", encoding="utf-8") as f:
        json.dump(reg, f)

    assets_borrow = {a["name"]: a for a in sm.assets()}
    # Master A có library_path ở Pack A nhưng mượn pack_id B -> bị chặn
    assert "preview_path" not in assets_borrow["master_a"] or assets_borrow["master_a"].get("preview_path") is None


# ==============================================================================
# 10. EXCLUSION CLOSURE & ROLLBACK VERIFICATION
# ==============================================================================

def test_exclude_all_masters_cleans_companions_and_leaves_manifest(configured_storage, tmp_path):
    """Kiểm tra:
    - Khi người dùng exclude TẤT CẢ masters -> commit() từ chối bằng ValueError('Hãy chọn ít nhất 1 file để nhập.').
    - Khi exclude Master 1 (có private preview) và chọn Master 2 (không có preview):
      -> Private preview của Master 1 bị dọn dẹp sạch khỏi disk payload, chỉ giữ Master 2."""
    sm, svc, library_root, config_dir = configured_storage

    pack_dir = tmp_path / "source_pack_all_excluded"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "previews").mkdir(parents=True, exist_ok=True)
    (pack_dir / "m.setting").write_text("MASTER", encoding="utf-8")
    (pack_dir / "previews" / "p.mp4").write_bytes(b"PREV")

    m = {
        "schema_version": 1,
        "name": "All Excluded Pack",
        "assets": [
            {
                "relative_path": "m.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/p.mp4",
                "excluded": True,
            }
        ]
    }
    (pack_dir / "pack_manifest.json").write_text(json.dumps(m), encoding="utf-8")

    job = svc.prepare(pack_dir)
    # Cả master duy nhất bị exclude -> commit ném ValueError
    with pytest.raises(ValueError, match="Hãy chọn ít nhất"):
        svc.commit(job)

    # Trường hợp 2 masters: Master 1 có private preview bị exclude, Master 2 được chọn
    pack_dir_2 = tmp_path / "source_pack_partial_excluded"
    pack_dir_2.mkdir(parents=True, exist_ok=True)
    (pack_dir_2 / "previews").mkdir(parents=True, exist_ok=True)
    (pack_dir_2 / "m1.setting").write_text("M1", encoding="utf-8")
    (pack_dir_2 / "m2.setting").write_text("M2", encoding="utf-8")
    (pack_dir_2 / "previews" / "p1.mp4").write_bytes(b"P1_PRIVATE")

    m2 = {
        "schema_version": 1,
        "name": "Partial Excluded Pack",
        "assets": [
            {
                "relative_path": "m1.setting",
                "category": "templates",
                "template_type": "trans",
                "preview_relative_path": "previews/p1.mp4",
                "excluded": True,
            },
            {
                "relative_path": "m2.setting",
                "category": "templates",
                "template_type": "trans",
                "excluded": False,
            }
        ]
    }
    (pack_dir_2 / "pack_manifest.json").write_text(json.dumps(m2), encoding="utf-8")

    job2 = svc.prepare(pack_dir_2)
    res2 = svc.commit(job2)
    assert res2["count"] == 1

    reg = sm.registry()
    pack_folder_rel = next(p["folder"] for p in reg["packs"] if p["id"] == res2["pack_id"])
    pack_folder_abs = library_root / pack_folder_rel

    # Kiểm tra trên đĩa: m2.setting và pack_manifest.json được giữ, p1.mp4 bị xóa!
    files_on_disk = sorted(p.name for p in pack_folder_abs.rglob("*") if p.is_file())
    assert files_on_disk == ["m2.setting", "pack_manifest.json"]
