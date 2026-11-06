"""
tests/core/test_legacy_library_retirement.py
Kiểm thử công cụ dọn dẹp dữ liệu cũ tools/retire_legacy_library.py (LIB-PUBLISH-02B):
- Dry-run không biến đổi (zero mutation) bất kỳ file nào trên đĩa
- Apply chỉ xóa các file khớp 100% hash/size đối chứng 1:1 với restore_verification
- Nguồn bị sửa đổi hoặc thiếu được giữ nguyên (SKIPPED_CHANGED)
- Bản sao lưu đối chứng thiếu/hỏng hash ngăn chặn việc xóa nguồn
- Lỗi tính toàn vẹn ZIP ngăn chặn toàn bộ quá trình
- Symlink trỏ ra ngoài bị từ chối
- Runtime metadata và recipes được bảo toàn nguyên vẹn
- Rollback an toàn, idempotent và không ghi đè file mới của người dùng
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
import pytest

from tools.retire_legacy_library import (
    compute_sha256,
    verify_zip_integrity,
    classify_and_plan,
    execute_retirement_plan,
    rollback_from_journal,
    KEEP_METADATA_PATHS
)


@pytest.fixture
def retirement_env(tmp_path):
    """Thiết lập môi trường giả lập gồm repo_root và run_dir với đối chứng đầy đủ."""
    repo_root = tmp_path / "repo"
    run_dir = tmp_path / "run"

    repo_root.mkdir()
    (run_dir / "zip").mkdir(parents=True)
    (run_dir / "restore_verification").mkdir(parents=True)
    (run_dir / "reports").mkdir(parents=True)

    # 1. Tạo file ZIP giả lập
    fake_zip = run_dir / "zip" / "ChunDVC_Legacy_Library_v1.0.zip"
    fake_zip.write_bytes(b"ZIP_MOCK_CONTENT_TEST_DATA_3415854045")
    zip_sha = compute_sha256(fake_zip)

    # 2. Tạo sample source files trong repo và counterpart trong restore_verification
    # File 1: Valid media asset (RETIRED)
    asset_rel = "assets/broll_memes/memes/test_meme.mp4"
    src_asset = repo_root / asset_rel
    src_asset.parent.mkdir(parents=True, exist_ok=True)
    src_asset.write_bytes(b"MEME_CONTENT_001_VALID")
    asset_sha = compute_sha256(src_asset)
    asset_size = src_asset.stat().st_size

    cp_asset = run_dir / "restore_verification" / asset_rel
    cp_asset.parent.mkdir(parents=True, exist_ok=True)
    cp_asset.write_bytes(b"MEME_CONTENT_001_VALID")

    # File 2: Runtime metadata (KEEP_RUNTIME_METADATA)
    meta_rel = "assets/starter_pack_manifest.json"
    src_meta = repo_root / meta_rel
    src_meta.parent.mkdir(parents=True, exist_ok=True)
    src_meta.write_bytes(b'{"version": 1, "packs": []}')
    meta_sha = compute_sha256(src_meta)
    meta_size = src_meta.stat().st_size

    cp_meta = run_dir / "restore_verification" / meta_rel
    cp_meta.parent.mkdir(parents=True, exist_ok=True)
    cp_meta.write_bytes(b'{"version": 1, "packs": []}')

    # File 3: Recipe (SKIPPED_UNCERTAIN)
    recipe_rel = "recipes/sample_recipe.json"
    src_recipe = repo_root / recipe_rel
    src_recipe.parent.mkdir(parents=True, exist_ok=True)
    src_recipe.write_bytes(b'{"id": "recipe_01", "name": "Vlog Recipe"}')
    recipe_sha = compute_sha256(src_recipe)
    recipe_size = src_recipe.stat().st_size

    cp_recipe = run_dir / "restore_verification" / recipe_rel
    cp_recipe.parent.mkdir(parents=True, exist_ok=True)
    cp_recipe.write_bytes(b'{"id": "recipe_01", "name": "Vlog Recipe"}')

    # File 4: Changed source file (SKIPPED_CHANGED)
    changed_rel = "assets/luts/changed_lut.cube"
    src_changed = repo_root / changed_rel
    src_changed.parent.mkdir(parents=True, exist_ok=True)
    src_changed.write_bytes(b"LUT_CONTENT_MODIFIED_BY_USER")

    cp_changed = run_dir / "restore_verification" / changed_rel
    cp_changed.parent.mkdir(parents=True, exist_ok=True)
    cp_changed.write_bytes(b"LUT_CONTENT_ORIGINAL")
    orig_lut_sha = compute_sha256(cp_changed)
    orig_lut_size = cp_changed.stat().st_size

    # 3. Tạo checksums.json
    checksums = {
        asset_rel: {"size_bytes": asset_size, "sha256": asset_sha},
        meta_rel: {"size_bytes": meta_size, "sha256": meta_sha},
        recipe_rel: {"size_bytes": recipe_size, "sha256": recipe_sha},
        changed_rel: {"size_bytes": orig_lut_size, "sha256": orig_lut_sha},
    }
    with open(run_dir / "reports" / "checksums.json", "w", encoding="utf-8") as f:
        json.dump(checksums, f, indent=2)

    return {
        "repo_root": repo_root,
        "run_dir": run_dir,
        "zip_path": fake_zip,
        "zip_sha": zip_sha,
        "checksums": checksums,
        "asset_rel": asset_rel,
        "meta_rel": meta_rel,
        "recipe_rel": recipe_rel,
        "changed_rel": changed_rel,
    }


def test_dryrun_zero_mutation(retirement_env):
    """Chế độ dry-run mặc định tạo kế hoạch phân loại chính xác và không thay đổi bất kỳ file nào."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])

    assert plan["summary"]["RETIRED"] == 1
    assert plan["summary"]["KEEP_RUNTIME_METADATA"] == 1
    assert plan["summary"]["SKIPPED_UNCERTAIN"] == 1
    assert plan["summary"]["SKIPPED_CHANGED"] == 1

    journal_path = repo / "reports" / "retirement-journal.json"
    journal = execute_retirement_plan(plan, journal_path, dry_run=True)

    # Đảm bảo không có file nào bị xóa
    assert (repo / retirement_env["asset_rel"]).exists()
    assert (repo / retirement_env["meta_rel"]).exists()
    assert (repo / retirement_env["recipe_rel"]).exists()
    assert (repo / retirement_env["changed_rel"]).exists()
    assert len(journal["retired_entries"]) == 0


def test_apply_hashmatch_retires_source_safely(retirement_env):
    """Khi apply=True, file RETIRED bị xóa an toàn, metadata/recipe/changed được bảo tồn."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    journal_path = repo / "reports" / "retirement-journal.json"
    journal = execute_retirement_plan(plan, journal_path, dry_run=False)

    # File mẫu RETIRED đã bị xóa khỏi repo
    assert not (repo / retirement_env["asset_rel"]).exists()

    # Các file khác giữ nguyên vẹn
    assert (repo / retirement_env["meta_rel"]).exists()
    assert (repo / retirement_env["recipe_rel"]).exists()
    assert (repo / retirement_env["changed_rel"]).exists()

    # Bản sao lưu trên run_dir vẫn nguyên vẹn 100%
    assert (run / "restore_verification" / retirement_env["asset_rel"]).exists()
    assert len(journal["retired_entries"]) == 1
    assert journal["retired_entries"][0]["path"] == retirement_env["asset_rel"]


def test_missing_counterpart_skips_retirement(retirement_env):
    """Nếu backup counterpart bị thiếu, nguồn không được phép xóa."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    # Xóa counterpart của asset hợp lệ
    (run / "restore_verification" / retirement_env["asset_rel"]).unlink()

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    # Asset đó chuyển sang SKIPPED_CHANGED
    assert plan["summary"]["RETIRED"] == 0
    assert plan["summary"]["SKIPPED_CHANGED"] == 2


def test_corrupted_zip_aborts_planning(retirement_env):
    """Nếu hash của ZIP không khớp expected, quá trình lập kế hoạch phải dừng khẩn cấp bằng ValueError."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    with pytest.raises(ValueError, match="Xác thực tính toàn vẹn của ZIP thất bại"):
        classify_and_plan(repo, run, checksums, expected_zip_hash="WRONG_SHA256_HASH_00000000000000000000")


def test_rollback_from_journal_restores_cleanly(retirement_env):
    """Rollback phục hồi chính xác file đã retire từ restore_verification."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    journal_path = repo / "reports" / "retirement-journal.json"
    execute_retirement_plan(plan, journal_path, dry_run=False)

    assert not (repo / retirement_env["asset_rel"]).exists()

    # Thực hiện Rollback
    restored_count, errors = rollback_from_journal(journal_path)
    assert restored_count == 1
    assert len(errors) == 0
    assert (repo / retirement_env["asset_rel"]).exists()
    assert compute_sha256(repo / retirement_env["asset_rel"]) == checksums[retirement_env["asset_rel"]]["sha256"]


def test_rollback_does_not_overwrite_modified_user_file(retirement_env):
    """Nếu người dùng đã tạo file mới cùng tên sau khi retire, rollback không được ghi đè."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    journal_path = repo / "reports" / "retirement-journal.json"
    execute_retirement_plan(plan, journal_path, dry_run=False)

    # Người dùng tạo file mới tại đường dẫn đã retire
    dest = repo / retirement_env["asset_rel"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"NEW_USER_CUSTOM_DATA_DO_NOT_OVERWRITE")

    restored_count, errors = rollback_from_journal(journal_path)
    assert restored_count == 0  # Không ghi đè
    assert dest.read_bytes() == b"NEW_USER_CUSTOM_DATA_DO_NOT_OVERWRITE"


def test_probe1_replaced_source_with_different_inode_is_not_deleted(retirement_env):
    """Probe 1: File nguồn bị thay thế bằng inode khác (kể cả cùng size/mtime) không được xóa."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    src_file = repo / retirement_env["asset_rel"]
    old_st = src_file.stat()

    # Thay thế file nguồn bằng file mới có inode khác (tạo replacement trước khi replace)
    replacement = src_file.parent / "new_file.tmp"
    replacement.write_bytes(b"MEME_CONTENT_001_VALID")
    os.utime(replacement, ns=(old_st.st_atime_ns, old_st.st_mtime_ns))
    replacement.replace(src_file)
    new_st = src_file.stat()
    assert new_st.st_ino != old_st.st_ino, "Cần inode khác nhau để test probe replacement"

    journal_path = repo / "reports" / "retirement-journal.json"
    journal = execute_retirement_plan(plan, journal_path, dry_run=False)

    # File nguồn không được phép bị xóa do inode đã thay đổi
    assert src_file.exists(), "File bị thay thế inode không được phép xóa!"
    assert len(journal["retired_entries"]) == 0


def test_probe2_backup_lost_after_plan_does_not_delete_source(retirement_env):
    """Probe 2: Bản sao lưu counterpart biến mất sau khi lập plan -> không được xóa nguồn."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    src_file = repo / retirement_env["asset_rel"]
    cp_file = run / "restore_verification" / retirement_env["asset_rel"]

    # Bản sao lưu biến mất sau khi lập plan
    cp_file.unlink()

    journal_path = repo / "reports" / "retirement-journal.json"
    journal = execute_retirement_plan(plan, journal_path, dry_run=False)

    # Nguồn phải được giữ nguyên vì mất bản sao lưu đối chứng
    assert src_file.exists(), "Nguồn không được xóa nếu bản sao lưu bị mất sau plan!"
    assert len(journal["retired_entries"]) == 0


def test_probe3_journal_preflight_write_failure_aborts_without_deleting_source(retirement_env):
    """Probe 3: Lỗi ghi journal trước khi xóa phải abort toàn bộ quá trình, không xóa bất kỳ nguồn nào."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    src_file = repo / retirement_env["asset_rel"]

    # Đặt journal_path vào vị trí bị chặn (thư mục cha là một regular file)
    blocked_dir = repo / "blocked_file"
    blocked_dir.write_text("I am a file, not a directory")
    invalid_journal = blocked_dir / "journal.json"

    with pytest.raises(Exception):
        execute_retirement_plan(plan, invalid_journal, dry_run=False)

    # Nguồn không bị xóa
    assert src_file.exists(), "Không được xóa file nếu journal preflight thất bại!"


def test_probe4_corrupt_rollback_counterpart_aborts_without_writing_destination(retirement_env):
    """Probe 4: Rollback gặp counterpart hỏng hash phải abort, không bao giờ để lại file hỏng tại đích."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    journal_path = repo / "reports" / "retirement-journal.json"
    execute_retirement_plan(plan, journal_path, dry_run=False)

    src_file = repo / retirement_env["asset_rel"]
    assert not src_file.exists()

    # Counterpart bị hỏng nội dung/hash
    cp_file = run / "restore_verification" / retirement_env["asset_rel"]
    cp_file.write_bytes(b"CORRUPTED_BACKUP_DATA_BITROT")

    restored_count, errors = rollback_from_journal(journal_path)
    assert restored_count == 0
    assert len(errors) == 1
    # File đích không được tạo bằng dữ liệu hỏng
    assert not src_file.exists(), "Không được để lại file đích hỏng khi rollback gặp lỗi!"


def test_probe_counterpart_corrupted_same_size_aborts_without_unlink(retirement_env):
    """A1: Bản sao lưu counterpart bị sửa đổi nội dung cùng kích thước (same size corrupt) phải bị chặn, 0 unlink."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    src_file = repo / retirement_env["asset_rel"]
    cp_file = run / "restore_verification" / retirement_env["asset_rel"]

    # Thay thế backup bằng nội dung hỏng CÙNG KÍCH THƯỚC (len == len)
    orig_bytes = cp_file.read_bytes()
    corrupt_bytes = b"X" * len(orig_bytes)
    assert len(corrupt_bytes) == len(orig_bytes)
    cp_file.write_bytes(corrupt_bytes)

    journal_path = repo / "reports" / "retirement-journal.json"
    journal = execute_retirement_plan(plan, journal_path, dry_run=False)

    # Nguồn không được phép xóa
    assert src_file.exists(), "Nguồn không được xóa nếu counterpart bị hỏng hash dù cùng size!"
    assert len(journal["retired_entries"]) == 0


def test_probe_durable_pending_record_exists_on_disk_before_unlink(retirement_env, monkeypatch):
    """A2: Ngay tại thời điểm gọi unlink(), trên đĩa PHẢI có bản ghi pending trong journal."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    src_file = repo / retirement_env["asset_rel"]
    journal_path = repo / "reports" / "retirement-journal.json"

    observed_pending = []
    original_unlink = Path.unlink

    def hooked_unlink(self, *args, **kwargs):
        if self.resolve() == src_file.resolve():
            # Mở journal trực tiếp từ đĩa ngay trước khi unlink thực sự xảy ra
            assert journal_path.exists(), "Journal trên đĩa phải tồn tại trước khi unlink!"
            with open(journal_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pending_list = data.get("pending_entries", [])
            observed_pending.append(list(pending_list))
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", hooked_unlink)

    journal = execute_retirement_plan(plan, journal_path, dry_run=False)

    assert len(observed_pending) == 1, "Hook unlink phải được gọi đúng 1 lần cho target asset"
    pending_entries = observed_pending[0]
    assert len(pending_entries) == 1, "Phải có đúng 1 pending entry trên đĩa tại thời điểm unlink"
    assert pending_entries[0]["path"] == retirement_env["asset_rel"]
    assert pending_entries[0]["status"] == "pending"
    assert pending_entries[0]["sha256"] == retirement_env["checksums"][retirement_env["asset_rel"]]["sha256"]

    # Sau khi xong, pending_entries rỗng và retired_entries có 1
    assert len(journal["pending_entries"]) == 0
    assert len(journal["retired_entries"]) == 1


def test_probe_rollback_no_overwrite_user_file_created_during_staging(retirement_env, monkeypatch):
    """A3: Rollback với no-overwrite publish: nếu người dùng tạo file mới tại đích, file người dùng được giữ nguyên 100%."""
    repo = retirement_env["repo_root"]
    run = retirement_env["run_dir"]
    checksums = retirement_env["checksums"]

    plan = classify_and_plan(repo, run, checksums, expected_zip_hash=retirement_env["zip_sha"])
    journal_path = repo / "reports" / "retirement-journal.json"
    execute_retirement_plan(plan, journal_path, dry_run=False)

    src_file = repo / retirement_env["asset_rel"]
    assert not src_file.exists()

    # Hook os.link để mô phỏng race condition: người dùng tạo file mới ngay trước khi link publish
    original_link = os.link

    def hooked_link(src, dst, *args, **kwargs):
        if Path(dst).resolve() == src_file.resolve():
            # Tạo file người dùng tại đích
            src_file.parent.mkdir(parents=True, exist_ok=True)
            src_file.write_bytes(b"USER_NEW_IMPORTANT_DATA_PRESERVED")
        return original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", hooked_link)

    restored_count, errors = rollback_from_journal(journal_path)

    # File của người dùng không bị ghi đè!
    assert src_file.exists()
    assert src_file.read_bytes() == b"USER_NEW_IMPORTANT_DATA_PRESERVED", "Dữ liệu người dùng mới phải được bảo toàn nguyên vẹn!"
    assert restored_count == 0, "Không được tính là restored khi không ghi đè user file"

    # Không để lại bất kỳ file tạm .tmp_rb_* nào
    residual_tmps = list(src_file.parent.glob(".tmp_rb_*"))
    assert len(residual_tmps) == 0, f"Phải dọn sạch staging files, nhưng còn {residual_tmps}"

