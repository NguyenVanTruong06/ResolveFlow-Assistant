#!/usr/bin/env python3
"""
tools/retire_legacy_library.py

Công cụ dọn dẹp (retire) dữ liệu mẫu cũ (LIB-PUBLISH-02B / 02B-R1):
- Đọc trusted inventory.json và checksums.json từ run export đã kiểm chứng.
- Kiểm tra tính toàn vẹn 2 lớp chặt chẽ:
  1. Bản sao lưu trong RUN/restore_verification/<path> tồn tại, regular, hash/size khớp.
  2. Nguồn hiện tại trong repo chưa bị sửa đổi (kích thước, fingerprint, inode, dev và SHA256 khớp expected).
- Bổ sung 4 cơ chế bảo vệ an toàn cao cấp (02B-R1):
  * Probe 1: Re-validate toàn bộ fingerprint (size, mtime_ns, ctime_ns, ino, dev, sha256) ngay trước khi unlink. Chặn xóa file thay thế.
  * Probe 2: Re-validate sự tồn tại và hash của counterpart sao lưu trên volume ngoài ngay trước khi unlink. Chặn xóa khi mất bản sao lưu.
  * Probe 3: Preflight kiểm tra ghi journal trước khi xóa; ghi nhận pending journal entry bền vững (crash-safe) trước mutation.
  * Probe 4: Rollback stage file trung gian, kiểm tra hash của backup và staged file TRƯỚC KHI publish vào đích; không ghi đè user replacement; không bao giờ để lại file hỏng tại destination.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("LegacyLibraryRetirement")

BUFFER_SIZE = 1024 * 1024  # 1MB buffer

# Danh sách tệp runtime metadata bắt buộc phải giữ lại cho app hoạt động
KEEP_METADATA_PATHS: Set[str] = {
    "assets/starter_pack_manifest.json",
    "assets/starter_pack.lock.json",
    "assets/community_catalog_template.json",
    "assets/system_architecture.png",
    "assets/sequence_diagram.png",
    "assets/broll_memes/README.md",
}

# Target prefixes được phép xem xét retire
ALLOWED_RETIRE_PREFIXES: Tuple[str, ...] = (
    "assets/",
    "drive_assets/",
    "presets/text_styles/",
)


def compute_sha256(file_path: Path) -> str:
    """Tính toán SHA256 digest của một file theo luồng an toàn."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(BUFFER_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_zip_integrity(zip_path: Path, expected_sha256: Optional[str] = None) -> bool:
    """Xác thực tệp ZIP sao lưu tồn tại, không rỗng và khớp hash nếu được cung cấp."""
    if not zip_path.exists() or not zip_path.is_file():
        logger.error("ZIP backup không tồn tại: %s", zip_path)
        return False
    if expected_sha256:
        actual_hash = compute_sha256(zip_path)
        if actual_hash != expected_sha256:
            logger.error("ZIP SHA256 không khớp! Expected %s, got %s", expected_sha256, actual_hash)
            return False
    return True


def inspect_path_safety(base_dir: Path, rel_path: str) -> Optional[Path]:
    """Kiểm tra đường dẫn không thoát ra ngoài base_dir và không phải symlink nguy hiểm."""
    target = (base_dir / rel_path).resolve()
    base_resolved = base_dir.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        return None
    return target


def classify_and_plan(
    repo_root: Path,
    run_dir: Path,
    checksums: Dict[str, Dict[str, Any]],
    expected_zip_hash: Optional[str] = None
) -> Dict[str, Any]:
    """
    Khảo sát toàn bộ files từ checksums.json, đối chứng với repo và restore_verification.
    Trả về kế hoạch phân loại chi tiết.
    """
    restore_dir = run_dir / "restore_verification"
    if not restore_dir.exists() or not restore_dir.is_dir():
        raise FileNotFoundError(f"Thư mục đối chứng không tồn tại: {restore_dir}")

    # Tìm file ZIP trong run_dir/zip
    zip_candidates = list((run_dir / "zip").glob("*.zip"))
    if not zip_candidates:
        raise FileNotFoundError(f"Không tìm thấy file ZIP trong {run_dir / 'zip'}")
    zip_file = zip_candidates[0]
    if expected_zip_hash and not verify_zip_integrity(zip_file, expected_zip_hash):
        raise ValueError(f"Xác thực tính toàn vẹn của ZIP thất bại: {zip_file}")

    plan: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir.resolve()),
        "repo_root": str(repo_root.resolve()),
        "zip_path": str(zip_file.resolve()),
        "zip_size_bytes": zip_file.stat().st_size,
        "items": [],
        "summary": {
            "total_evaluated": len(checksums),
            "RETIRED": 0,
            "KEEP_RUNTIME_METADATA": 0,
            "SKIPPED_UNCERTAIN": 0,
            "SKIPPED_CHANGED": 0,
            "total_bytes_to_retire": 0,
        }
    }

    for rel_path, meta in checksums.items():
        expected_size = meta["size_bytes"]
        expected_sha = meta["sha256"]

        item_plan: Dict[str, Any] = {
            "path": rel_path,
            "expected_size": expected_size,
            "expected_sha256": expected_sha,
            "status": "SKIPPED_CHANGED",
            "reason": "",
            "source_stat": None,
            "counterpart_stat": None,
        }

        # 1. Kiểm tra quy tắc metadata cần giữ lại
        if rel_path in KEEP_METADATA_PATHS or rel_path.endswith((".py", ".gitignore")):
            item_plan["status"] = "KEEP_RUNTIME_METADATA"
            item_plan["reason"] = "Runtime metadata / configuration needed by application"
            plan["summary"]["KEEP_RUNTIME_METADATA"] += 1
            plan["items"].append(item_plan)
            continue

        # 2. Kiểm tra recipes (chưa phân định rõ user sample vs workflow thực tế)
        if rel_path.startswith("recipes/"):
            item_plan["status"] = "SKIPPED_UNCERTAIN"
            item_plan["reason"] = "Recipe workflow - kept safe without explicit user deprecation"
            plan["summary"]["SKIPPED_UNCERTAIN"] += 1
            plan["items"].append(item_plan)
            continue

        # 3. Kiểm tra custom preset subtree (tuyệt đối không retire dữ liệu người dùng)
        if rel_path.startswith("presets/text_styles/custom/"):
            item_plan["status"] = "KEEP_RUNTIME_METADATA"
            item_plan["reason"] = "User custom preset - must be preserved"
            plan["summary"]["KEEP_RUNTIME_METADATA"] += 1
            plan["items"].append(item_plan)
            continue

        # 4. Kiểm tra allowlist tiền tố
        if not any(rel_path.startswith(prefix) for prefix in ALLOWED_RETIRE_PREFIXES):
            item_plan["status"] = "SKIPPED_UNCERTAIN"
            item_plan["reason"] = "Path outside allowed retirement roots"
            plan["summary"]["SKIPPED_UNCERTAIN"] += 1
            plan["items"].append(item_plan)
            continue

        # 5. Kiểm tra an toàn đường dẫn và symlink
        src_file = repo_root / rel_path
        if src_file.is_symlink():
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = "Source path is a symlink (rejected for security)"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        safe_src = inspect_path_safety(repo_root, rel_path)
        if safe_src is None or not safe_src.exists() or not safe_src.is_file():
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = "Source file missing or escapes repo root"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        # 6. Kiểm tra counterpart trong restore_verification
        safe_counterpart = inspect_path_safety(restore_dir, rel_path)
        if safe_counterpart is None or not safe_counterpart.exists() or not safe_counterpart.is_file():
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = "Backup counterpart missing or invalid in restore_verification"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        cp_st = safe_counterpart.stat()
        item_plan["counterpart_stat"] = {"size": cp_st.st_size, "mtime_ns": cp_st.st_mtime_ns}
        if cp_st.st_size != expected_size:
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = f"Counterpart size mismatch: expected {expected_size}, got {cp_st.st_size}"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        # 7. Kiểm tra kích thước và hash của source trong repo
        src_st = safe_src.stat()
        item_plan["source_stat"] = {
            "size": src_st.st_size,
            "mtime_ns": src_st.st_mtime_ns,
            "ctime_ns": src_st.st_ctime_ns,
            "ino": src_st.st_ino,
            "dev": src_st.st_dev,
        }
        if src_st.st_size != expected_size:
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = f"Source size mismatch: expected {expected_size}, got {src_st.st_size}"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        src_hash = compute_sha256(safe_src)
        if src_hash != expected_sha:
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = f"Source SHA256 mismatch: expected {expected_sha}, got {src_hash}"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        # 8. Kiểm tra hash của counterpart
        cp_hash = compute_sha256(safe_counterpart)
        if cp_hash != expected_sha:
            item_plan["status"] = "SKIPPED_CHANGED"
            item_plan["reason"] = f"Counterpart SHA256 corrupted: expected {expected_sha}, got {cp_hash}"
            plan["summary"]["SKIPPED_CHANGED"] += 1
            plan["items"].append(item_plan)
            continue

        # Tất cả các điều kiện đối chứng 1:1 đã thỏa mãn tuyệt đối
        item_plan["status"] = "RETIRED"
        item_plan["reason"] = "Verified 1:1 duplicate in restore_verification and ZIP"
        plan["summary"]["RETIRED"] += 1
        plan["summary"]["total_bytes_to_retire"] += expected_size
        plan["items"].append(item_plan)

    return plan


def _persist_journal(journal_path: Path, journal: Dict[str, Any]) -> None:
    """
    Ghi nhật ký bền vững (durable write) xuống đĩa:
    - Tạo file tạm thời độc quyền bằng tempfile.mkstemp trong cùng thư mục cha với journal_path.
    - Ghi JSON, flush và os.fsync xuống đĩa.
    - os.replace nguyên tử đè lên journal_path.
    - os.fsync trên thư mục cha (nếu hệ thống hỗ trợ).
    """
    journal_dir = journal_path.parent
    journal_dir.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(prefix=".tmp_journal_", dir=journal_dir)
    try:
        with open(temp_fd, "w", encoding="utf-8") as f:
            json.dump(journal, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, journal_path)
        try:
            dir_fd = os.open(str(journal_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


def execute_retirement_plan(
    plan: Dict[str, Any],
    journal_path: Path,
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Thực thi kế hoạch dọn dẹp với crash-safe journaling và 4 cơ chế bảo vệ atomicity:
    - Preflight write/ownership kiểm tra journal_path trước khi unlink.
    - Re-validate toàn bộ fingerprint (ino, dev, size, mtime, ctime, sha256) ngay trước khi xóa.
    - Re-validate counterpart backup còn tồn tại và khớp cả size + sha256 trên volume ngoài ngay trước khi xóa (A1).
    - Persist journal tiến trình từng bước (pending durable write TRƯỚC mutation, committed SAU mutation) (A2).
    """
    repo_root = Path(plan["repo_root"])
    restore_dir = Path(plan["run_dir"]) / "restore_verification"

    journal: Dict[str, Any] = {
        "task_id": "LIB-PUBLISH-02B-R2",
        "execution_timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "run_dir": plan["run_dir"],
        "repo_root": plan["repo_root"],
        "zip_path": plan["zip_path"],
        "retired_entries": [],
        "pending_entries": [],
        "errors": []
    }

    if dry_run:
        logger.info("[DRY-RUN] Sẽ retire %d files (%d bytes), giữ lại %d metadata, bỏ qua %d uncertain, %d changed.",
                    plan["summary"]["RETIRED"], plan["summary"]["total_bytes_to_retire"],
                    plan["summary"]["KEEP_RUNTIME_METADATA"], plan["summary"]["SKIPPED_UNCERTAIN"],
                    plan["summary"]["SKIPPED_CHANGED"])
        return journal

    # Preflight Probe 3 (A2): Xác thực quyền ghi và tạo journal bền vững ban đầu trước mọi thao tác phá hủy
    try:
        _persist_journal(journal_path, journal)
    except Exception as preflight_err:
        logger.error("Preflight kiểm tra ghi journal thất bại, hủy bỏ toàn bộ mutation: %s", preflight_err)
        raise preflight_err

    logger.info("[APPLY] Bắt đầu dọn dẹp %d files từ repo...", plan["summary"]["RETIRED"])
    retired_count = 0

    for item in plan["items"]:
        if item["status"] != "RETIRED":
            continue

        rel_path = item["path"]
        src_path = repo_root / rel_path

        try:
            # Probe 2 (A1): Re-validate backup volume & counterpart TRƯỚC KHI unlink
            if not restore_dir.exists() or not restore_dir.is_dir():
                logger.error("Thư mục sao lưu đối chứng bị mất hoặc unmount: %s", restore_dir)
                break

            safe_counterpart = inspect_path_safety(restore_dir, rel_path)
            if safe_counterpart is None or not safe_counterpart.is_file():
                logger.error("Bản sao lưu counterpart không còn tồn tại trên ổ ngoài, bỏ qua unlink %s", rel_path)
                continue

            cp_st = safe_counterpart.stat()
            if cp_st.st_size != item["expected_size"]:
                logger.error("Kích thước counterpart bị biến đổi ngay trước khi xóa %s", rel_path)
                continue

            # BẮT BUỘC (A1): Re-hash kiểm tra nội dung counterpart ngay trước khi xóa
            if compute_sha256(safe_counterpart) != item["expected_sha256"]:
                logger.error("Nội dung (SHA256) counterpart bị hỏng ngay trước khi xóa %s", rel_path)
                continue

            # Probe 1: Re-validate toàn bộ fingerprint của file nguồn (size, mtime_ns, ctime_ns, ino, dev, sha256)
            if not src_path.exists() or src_path.is_symlink():
                logger.warning("File nguồn không tồn tại hoặc đã bị đổi thành symlink: %s", rel_path)
                continue

            current_st = src_path.stat()
            recorded_st = item["source_stat"]
            if (
                current_st.st_size != recorded_st["size"]
                or current_st.st_mtime_ns != recorded_st["mtime_ns"]
                or current_st.st_ctime_ns != recorded_st["ctime_ns"]
                or current_st.st_ino != recorded_st["ino"]
                or current_st.st_dev != recorded_st["dev"]
            ):
                logger.warning("File đã bị thay thế hoặc đổi inode/fingerprint trước khi xóa: %s", rel_path)
                continue

            if compute_sha256(src_path) != item["expected_sha256"]:
                logger.warning("File nguồn thay đổi nội dung (SHA256) ngay trước khi xóa: %s", rel_path)
                continue

            # Probe 3 (A2): Ghi nhận pending entry vào journal bền vững TRƯỚC KHI unlink
            pending_entry = {
                "path": rel_path,
                "sha256": item["expected_sha256"],
                "size_bytes": item["expected_size"],
                "backup_source": str(safe_counterpart.resolve()),
                "source_stat": recorded_st,
                "status": "pending",
                "staged_at": datetime.now(timezone.utc).isoformat(),
            }
            journal["pending_entries"].append(pending_entry)
            _persist_journal(journal_path, journal)

            # Xóa file nguồn khỏi repo sau khi đã đảm bảo 100% đối chứng và pending journal trên đĩa
            src_path.unlink()
            retired_count += 1

            # Chuyển pending entry thành committed entry và cập nhật journal bền vững trên đĩa
            committed_entry = {
                "path": rel_path,
                "sha256": item["expected_sha256"],
                "size_bytes": item["expected_size"],
                "backup_source": str(safe_counterpart.resolve()),
                "status": "committed",
                "removed_at": datetime.now(timezone.utc).isoformat(),
            }
            journal["retired_entries"].append(committed_entry)
            journal["pending_entries"] = [p for p in journal["pending_entries"] if p["path"] != rel_path]
            _persist_journal(journal_path, journal)

            # Dọn dẹp thư mục cha nếu rỗng
            parent = src_path.parent
            while parent != repo_root and parent.resolve() != repo_root.resolve():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent
                    else:
                        break
                except OSError:
                    break

        except Exception as e:
            logger.error("Lỗi khi retire %s: %s", rel_path, e)
            journal["errors"].append({"path": rel_path, "error": str(e)})

    # Ghi lại trạng thái cuối cùng
    _persist_journal(journal_path, journal)

    logger.info("[APPLY] Hoàn tất dọn dẹp %d files. Journal lưu tại %s", retired_count, journal_path)
    return journal


def rollback_from_journal(journal_path: Path) -> Tuple[int, List[str]]:
    """
    Phục hồi nguyên trạng các files đã retire dựa trên execution journal (Probe 4 & A3):
    - Hỗ trợ cả retired_entries và pending_entries (nếu source đã bị unlinked).
    - Kiểm tra tính toàn vẹn của backup counterpart TRƯỚC KHI chạm vào đích.
    - Stage vào tệp tạm thời độc quyền (unpredictable, exclusive O_CREAT|O_EXCL).
    - Flush + fsync, kiểm tra hash của staged file.
    - Publish nguyên tử qua os.link (no-overwrite).
    - Nếu destination đã có file mới của người dùng (khác hash): tuyệt đối không ghi đè, bảo toàn file người dùng.
    - Nếu destination đã có file trùng hash: ghi nhận already restored, không rewrite.
    - Khối finally luôn dọn dẹp staged file tạm, không để lại file rác hay file đích hỏng.
    """
    if not journal_path.exists():
        raise FileNotFoundError(f"Không tìm thấy journal để rollback: {journal_path}")

    with open(journal_path, "r", encoding="utf-8") as f:
        journal = json.load(f)

    repo_root = Path(journal["repo_root"])
    restored_count = 0
    errors = []

    # Thu thập toàn bộ entries: các retired_entries và các pending_entries mà file nguồn đã thực sự bị xóa
    candidates = list(journal.get("retired_entries", []))
    for p_entry in journal.get("pending_entries", []):
        p_path = repo_root / p_entry["path"]
        if not p_path.exists():
            candidates.append(p_entry)

    for entry in candidates:
        rel_path = entry["path"]
        backup_src = Path(entry["backup_source"])
        dest_path = repo_root / rel_path
        expected_sha = entry["sha256"]

        tmp_stage_path = None
        try:
            # 1. Kiểm tra backup counterpart trước khi đụng tới destination
            if not backup_src.exists() or not backup_src.is_file():
                errors.append(f"Backup counterpart không tồn tại: {backup_src}")
                continue

            if compute_sha256(backup_src) != expected_sha:
                errors.append(f"Backup counterpart bị hỏng hash trước khi rollback cho {rel_path}")
                continue

            # 2. Kiểm tra nếu destination đã tồn tại
            if dest_path.exists():
                current_hash = compute_sha256(dest_path)
                if current_hash == expected_sha:
                    # Đã được khôi phục chính xác
                    restored_count += 1
                    continue
                else:
                    logger.warning("Đích đã có file với nội dung mới của người dùng, không ghi đè: %s", rel_path)
                    continue

            # 3. Stage vào tệp tạm độc quyền (unpredictable, exclusive O_CREAT|O_EXCL trong cùng thư mục cha)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            temp_fd, temp_name = tempfile.mkstemp(prefix=".tmp_rb_", dir=dest_path.parent)
            tmp_stage_path = Path(temp_name)
            with os.fdopen(temp_fd, "wb") as f_out:
                with open(backup_src, "rb") as f_in:
                    shutil.copyfileobj(f_in, f_out)
                f_out.flush()
                os.fsync(f_out.fileno())

            if compute_sha256(tmp_stage_path) != expected_sha:
                errors.append(f"Hash của staged file không khớp cho {rel_path}")
                continue

            # 4. Atomic No-Overwrite Publish:
            # os.link tạo hardlink nguyên tử từ staged file sang dest_path.
            # Nếu dest_path xuất hiện trong quá trình copy/stage, os.link ném FileExistsError mà KHÔNG ghi đè dest_path!
            try:
                os.link(tmp_stage_path, dest_path)
                restored_count += 1
            except FileExistsError:
                # File đích đã xuất hiện
                current_hash = compute_sha256(dest_path)
                if current_hash == expected_sha:
                    restored_count += 1
                else:
                    logger.warning("Đích vừa có file mới của người dùng, không ghi đè: %s", rel_path)

        except Exception as e:
            errors.append(f"Lỗi phục hồi {rel_path}: {e}")
        finally:
            if tmp_stage_path is not None and tmp_stage_path.exists():
                try:
                    tmp_stage_path.unlink()
                except OSError:
                    pass

    logger.info("Rollback hoàn tất: đã phục hồi %d files (lỗi: %d)", restored_count, len(errors))
    return restored_count, errors


def main():
    parser = argparse.ArgumentParser(description="Retire Legacy Library from ResolveFlow Assistant (LIB-PUBLISH-02B / 02B-R1)")
    parser.add_argument("--run-dir", type=str, default="/media/vanntruongg/Ventoy/ChunDVC_Publish/runs/20261029_011655_e6c3a144",
                        help="Đường dẫn thư mục run export đã verify trên ổ ngoài")
    parser.add_argument("--repo-root", type=str, default=str(Path(__file__).parent.parent.resolve()),
                        help="Thư mục gốc của repository")
    parser.add_argument("--plan-out", type=str, default="reports/source-retirement-plan.json",
                        help="Đường dẫn xuất file kế hoạch retirement")
    parser.add_argument("--journal-out", type=str, default="reports/retirement-execution-journal.json",
                        help="Đường dẫn xuất file nhật ký execution journal")
    parser.add_argument("--apply", action="store_true", default=False,
                        help="Thực thi dọn dẹp thật sự (mặc định là dry-run)")
    parser.add_argument("--rollback", action="store_true", default=False,
                        help="Phục hồi các file đã retire dựa trên execution journal")
    parser.add_argument("--expected-zip-sha256", type=str,
                        default="f27aefb09759a4304599703fbaf4d281b28a67846fdb4891eae3d5cf8d4389ad",
                        help="SHA256 mong đợi của file ZIP backup")

    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    run_dir = Path(args.run_dir).resolve()
    plan_path = repo_root / args.plan_out
    journal_path = repo_root / args.journal_out

    if args.rollback:
        restored, errors = rollback_from_journal(journal_path)
        if errors:
            logger.error("Rollback có %d lỗi!", len(errors))
            sys.exit(1)
        sys.exit(0)

    # Đọc checksums.json từ run_dir
    checksums_path = run_dir / "reports" / "checksums.json"
    if not checksums_path.exists():
        logger.error("Không tìm thấy checksums.json tại %s", checksums_path)
        sys.exit(1)

    with open(checksums_path, "r", encoding="utf-8") as f:
        checksums = json.load(f)

    logger.info("Bắt đầu khảo sát và phân loại %d files...", len(checksums))
    plan = classify_and_plan(
        repo_root=repo_root,
        run_dir=run_dir,
        checksums=checksums,
        expected_zip_hash=args.expected_zip_sha256
    )

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    logger.info("Kế hoạch retirement đã được ghi vào %s", plan_path)

    execute_retirement_plan(
        plan=plan,
        journal_path=journal_path,
        dry_run=not args.apply
    )


if __name__ == "__main__":
    main()
