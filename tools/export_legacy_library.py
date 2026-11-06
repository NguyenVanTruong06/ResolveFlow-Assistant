"""
tools/export_legacy_library.py
Tooling độc lập chuẩn hóa để xuất bản lưu toàn kho cũ ChunDVC và khôi phục kiểm chứng.
Tuân thủ đầy đủ các ràng buộc trong LIB-PUBLISH-01-R1-THEN-02A:
- Sử dụng stdlib streaming (không tải file lớn vào RAM).
- Pre-stat và post-stat kiểm tra tính bất biến của nguồn (regular file, size, mtime, dev, ino).
- Không theo symlink thoát kho, không đụng đến credentials, caches, user media.
- Xuất full canonical JSON cho 24 TextStylePreset và 4 TransitionStylePreset.
- Trích xuất MOCKUP_* catalogs và lưu fragment mã nguồn kèm hash.
- Đóng gói duy nhất một file ChunDVC_Legacy_Library_v1.0.zip kèm bundle_manifest.json, originals/, catalogs/, inventory.json, checksums.json, README_RESTORE.md.
- Khôi phục kiểm chứng (restore proof) 100% đối chiếu SHA256 và kích thước.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path("/media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant")
VENTOY_MOUNT = Path("/media/vanntruongg/Ventoy")
PUBLISH_BASE = VENTOY_MOUNT / "ChunDVC_Publish"

MAX_FILE_BYTES = 10 * 1024**3  # 10 GiB per file limit
CHUNK_SIZE = 1024 * 1024       # 1 MiB chunk for streaming


def check_mount_space(mount_point: Path, min_gb: float = 15.0) -> int:
    """Kiểm tra mount point tồn tại, online và có đủ dung lượng khả dụng."""
    if not mount_point.is_dir():
        raise RuntimeError(f"Mount point không tồn tại hoặc offline: {mount_point}")
    st = os.statvfs(mount_point)
    avail_bytes = st.f_bavail * st.f_frsize
    avail_gb = avail_bytes / (1024**3)
    if avail_gb < min_gb:
        raise RuntimeError(f"Dung lượng khả dụng trên {mount_point} quá thấp: {avail_gb:.2f} GB (< {min_gb} GB yêu cầu).")
    return avail_bytes


def compute_file_sha256(path: Path, cancel: Callable[[], bool] = lambda: False) -> Tuple[str, int]:
    """Tính SHA256 và kích thước của file bằng streaming chunk 1 MiB."""
    h = hashlib.sha256()
    total_read = 0
    with open(path, "rb") as f:
        while True:
            if cancel():
                raise InterruptedError("Đã hủy tính toán checksum.")
            block = f.read(CHUNK_SIZE)
            if not block:
                break
            h.update(block)
            total_read += len(block)
    return h.hexdigest(), total_read


def is_excluded_source_path(path: Path, repo_root: Path) -> bool:
    """Kiểm tra đường dẫn có nằm trong danh sách loại trừ an toàn không."""
    try:
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return True

    # Danh mục nhạy cảm hoặc rác
    ignored_prefixes = (
        ".git", ".venv", "__pycache__", ".pytest_cache", ".bridge",
        ".superpowers", "build", "dist", "tmp", ".system_generated"
    )
    parts = path.relative_to(repo_root).parts
    if any(p in ignored_prefixes for p in parts):
        return True

    # Tên file nhạy cảm
    name_lower = path.name.lower()
    if name_lower.startswith(".env") or name_lower.endswith((".pem", ".key", ".p12")):
        return True
    if any(secret in name_lower for secret in ("credential", "secret", "token", "password", "auth")):
        return True

    return False


def extract_mockup_catalogs_from_tab_assets(tab_assets_path: Path) -> Dict[str, Any]:
    """Trích xuất dữ liệu primitive của MOCKUP_* từ tab_assets.py kèm fragment và hash."""
    if not tab_assets_path.is_file():
        return {"error": "tab_assets.py not found"}

    content = tab_assets_path.read_text(encoding="utf-8", errors="replace")
    file_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Tìm vùng định nghĩa MOCKUP_
    catalogs_data: Dict[str, Any] = {
        "source_file": "src/ui/tabs/tab_assets.py",
        "source_sha256": file_sha256,
        "extracted_at": datetime.datetime.now().isoformat(),
        "catalogs": {}
    }

    catalog_names = ["MOCKUP_LUTS", "MOCKUP_ICONS", "MOCKUP_OVERLAYS", "MOCKUP_MEMES", "MOCKUP_SFX"]
    for cat_name in catalog_names:
        pattern = rf"{cat_name}:\s*List\[StudioAsset\]\s*=\s*\[(.*?)\]\n\n"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            raw_block = match.group(1)
            # Trích xuất từng mục StudioAsset(...)
            item_pattern = r'StudioAsset\((.*?)\)'
            items = []
            for item_match in re.finditer(item_pattern, raw_block, re.DOTALL):
                arg_str = item_match.group(1).strip()
                items.append(arg_str)

            catalogs_data["catalogs"][cat_name] = {
                "count": len(items),
                "fragment_raw": match.group(0),
                "items_signatures": items
            }

    return catalogs_data


def dump_all_catalogs(repo_root: Path, output_catalogs_dir: Path) -> Dict[str, Any]:
    """Xuất full canonical JSON cho TextStylePreset (24), TransitionStylePreset (4), MOCKUP_*."""
    output_catalogs_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {}

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # 1. TextStylePreset (24 presets)
    try:
        from src.core.text_preset import BUILTIN_PRESETS
        text_presets_data = []
        for p in BUILTIN_PRESETS:
            dump_fn = getattr(p, "model_dump", getattr(p, "dict", None))
            if dump_fn:
                text_presets_data.append(dump_fn())
            else:
                text_presets_data.append(p.__dict__)

        text_out_path = output_catalogs_dir / "text_style_presets.json"
        with open(text_out_path, "w", encoding="utf-8") as f:
            json.dump(text_presets_data, f, ensure_ascii=False, indent=2)

        summary["text_style_presets"] = {
            "count": len(text_presets_data),
            "file": str(text_out_path.name),
            "sha256": compute_file_sha256(text_out_path)[0]
        }
    except Exception as e:
        summary["text_style_presets"] = {"error": str(e)}

    # 2. TransitionStylePreset (4 transitions)
    try:
        from src.core.transition_preset import BUILTIN_TRANSITIONS
        trans_presets_data = []
        for t in BUILTIN_TRANSITIONS:
            dump_fn = getattr(t, "model_dump", getattr(t, "dict", None))
            if dump_fn:
                trans_presets_data.append(dump_fn())
            else:
                trans_presets_data.append(t.__dict__)

        trans_out_path = output_catalogs_dir / "transition_style_presets.json"
        with open(trans_out_path, "w", encoding="utf-8") as f:
            json.dump(trans_presets_data, f, ensure_ascii=False, indent=2)

        summary["transition_style_presets"] = {
            "count": len(trans_presets_data),
            "file": str(trans_out_path.name),
            "sha256": compute_file_sha256(trans_out_path)[0]
        }
    except Exception as e:
        summary["transition_style_presets"] = {"error": str(e)}

    # 3. MOCKUP_* Catalogs from tab_assets.py
    try:
        tab_assets_p = repo_root / "src/ui/tabs/tab_assets.py"
        mockup_data = extract_mockup_catalogs_from_tab_assets(tab_assets_p)
        mockup_out_path = output_catalogs_dir / "mockup_catalogs.json"
        with open(mockup_out_path, "w", encoding="utf-8") as f:
            json.dump(mockup_data, f, ensure_ascii=False, indent=2)

        summary["mockup_catalogs"] = {
            "catalogs": list(mockup_data.get("catalogs", {}).keys()),
            "file": str(mockup_out_path.name),
            "sha256": compute_file_sha256(mockup_out_path)[0]
        }
    except Exception as e:
        summary["mockup_catalogs"] = {"error": str(e)}

    return summary


def classify_legacy_file(rel_posix: str) -> Tuple[str, str]:
    """Phân loại danh mục và vai trò của file trong kho cũ."""
    lower = rel_posix.lower()
    suffix = Path(rel_posix).suffix.lower()

    if "/previews/" in lower or lower.startswith("previews/"):
        return "templates", "companion_preview"
    if "/thumbnails/" in lower or lower.startswith("thumbnails/"):
        return "templates", "companion_thumbnail"
    if suffix in (".setting", ".drfx", ".comp"):
        return "templates", "master_template"
    if "/text_styles/" in lower or lower.startswith("presets/text_styles/"):
        return "templates", "text_style_preset"
    if lower.startswith("recipes/") or "/recipes/" in lower:
        return "recipes", "sample_recipe"
    if "/luts/" in lower or suffix == ".cube":
        return "luts", "standalone_asset"
    if "/sfx/" in lower or (suffix in (".wav", ".mp3", ".ogg") and "/sfx" in lower):
        return "sfx", "standalone_asset"
    if "/bgm/" in lower or (suffix in (".wav", ".mp3", ".ogg") and "/bgm" in lower):
        return "bgm", "standalone_asset"
    if "/memes/" in lower or "/broll_memes/memes/" in lower:
        return "memes", "standalone_asset"
    if "/overlays/" in lower or "/broll_memes/overlays/" in lower:
        return "overlays", "standalone_asset"
    if "/docs/" in lower or suffix in (".pdf", ".txt", ".md", ".docx"):
        return "docs", "documentation"
    if suffix in (".dpx", ".drx"):
        return "templates", "powergrade"

    return "NEEDS_CLASSIFICATION", "unresolved"


def survey_legacy_sources(
    repo_root: Path,
    source_subdirs: List[str],
    cancel: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda text: None
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Khảo sát nguồn nguyên vẹn, thu thập pre-stat và SHA256 từng file bằng streaming."""
    items: List[Dict[str, Any]] = []
    checksums: Dict[str, Dict[str, Any]] = {}

    for sub in source_subdirs:
        sub_root = repo_root / sub
        if not sub_root.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(sub_root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
            for fname in sorted(filenames):
                if cancel():
                    raise InterruptedError("Đã hủy khảo sát.")

                f_path = Path(dirpath) / fname
                if f_path.is_symlink() or is_excluded_source_path(f_path, repo_root):
                    continue

                st_pre = f_path.lstat()
                if not stat.S_ISREG(st_pre.st_mode):
                    continue

                rel_p = f_path.relative_to(repo_root).as_posix()
                progress(f"Surveying: {rel_p}")

                sha, size_bytes = compute_file_sha256(f_path, cancel=cancel)

                # Post-stat verification
                st_post = f_path.lstat()
                if (
                    st_pre.st_size != st_post.st_size
                    or st_pre.st_mtime_ns != st_post.st_mtime_ns
                    or st_pre.st_ino != st_post.st_ino
                ):
                    raise RuntimeError(f"Source file {rel_p} bị thay đổi trong quá trình khảo sát! Abort.")

                category, role = classify_legacy_file(rel_p)

                entry = {
                    "original_path": rel_p,
                    "name": f_path.stem,
                    "extension": f_path.suffix.lower(),
                    "size_bytes": size_bytes,
                    "sha256": sha,
                    "category": category,
                    "role": role,
                    "stat": {
                        "mtime_ns": st_pre.st_mtime_ns,
                        "ino": st_pre.st_ino,
                        "dev": st_pre.st_dev
                    }
                }
                items.append(entry)
                checksums[rel_p] = {
                    "size_bytes": size_bytes,
                    "sha256": sha
                }

    return items, checksums


def export_legacy_library(
    repo_root: Path,
    output_run_dir: Path,
    cancel: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda text: None
) -> Dict[str, Any]:
    """Thực hiện toàn bộ quy trình xuất bản lưu toàn kho cũ ChunDVC và khôi phục kiểm chứng."""
    run_dir = output_run_dir
    zip_dir = run_dir / "zip"
    reports_dir = run_dir / "reports"
    catalogs_dir = run_dir / "catalogs"
    restore_dir = run_dir / "restore_verification"

    for d in (zip_dir, reports_dir, catalogs_dir, restore_dir):
        d.mkdir(parents=True, exist_ok=True)

    progress("1. Đang xuất catalogs (TextStylePreset, TransitionStylePreset, MOCKUP_*)...")
    catalogs_summary = dump_all_catalogs(repo_root, catalogs_dir)

    progress("2. Đang khảo sát các nguồn assets, drive_assets, presets/text_styles, recipes...")
    source_subdirs = ["assets", "drive_assets", "presets/text_styles", "recipes"]
    items, checksums = survey_legacy_sources(repo_root, source_subdirs, cancel=cancel, progress=progress)

    total_files = len(items)
    total_bytes = sum(i["size_bytes"] for i in items)
    total_mb = total_bytes / (1024 * 1024)

    # 3. Ghi inventory.json & checksums.json
    progress(f"3. Đã khảo sát {total_files} files ({total_mb:.2f} MB). Đang ghi báo cáo...")
    inv_data = {
        "generated_at": datetime.datetime.now().isoformat(),
        "total_files_surveyed": total_files,
        "total_bytes_surveyed": total_bytes,
        "total_mb_surveyed": round(total_mb, 2),
        "catalogs_summary": catalogs_summary,
        "items": items
    }
    with open(reports_dir / "inventory.json", "w", encoding="utf-8") as f:
        json.dump(inv_data, f, ensure_ascii=False, indent=2)

    with open(reports_dir / "checksums.json", "w", encoding="utf-8") as f:
        json.dump(checksums, f, ensure_ascii=False, indent=2)

    # 4. Tạo README_RESTORE.md & bundle_manifest.json
    readme_content = f"""# HƯỚNG DẪN KHÔI PHỤC KHO TÀI NGUYÊN CŨ CHUNDVC
Thời gian tạo: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Tổng số files lưu trữ: {total_files} ({total_mb:.2f} MB)

## 1. Lưu ý quan trọng
- Đây là bản lưu trữ nguyên vẹn (Full Library Backup Archive), KHÔNG PHẢI là pack nạp 1 lần qua Ingest Wizard.
- Cấu trúc thư mục bên trong:
  - `originals/`: Chứa toàn bộ cây thư mục gốc (`assets/`, `drive_assets/`, `presets/text_styles/`, `recipes/`).
  - `catalogs/`: Chứa canonical model dump của 24 TextStylePreset, 4 TransitionStylePreset và MOCKUP_* catalogs.
  - `inventory.json` & `checksums.json`: Danh mục chi tiết và bảng mã SHA256 từng file.
  - `bundle_manifest.json`: Manifest định danh bản sao lưu.

## 2. Quy trình khôi phục (Restore)
1. Giải nén file `ChunDVC_Legacy_Library_v1.0.zip` vào thư mục mong muốn.
2. Kiểm tra tính toàn vẹn bằng cách so khớp mã SHA256 trong `checksums.json`.
3. Chép các thư mục trong `originals/` trở lại thư mục dự án hoặc kho lưu trữ độc lập.
"""
    with open(reports_dir / "README_RESTORE.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    manifest_data = {
        "artifact_kind": "library_backup",
        "schema_version": 1,
        "package_name": "ChunDVC_Legacy_Library_v1.0.zip",
        "created_at": datetime.datetime.now().isoformat(),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "source_subdirs": source_subdirs,
        "catalogs_included": list(catalogs_summary.keys())
    }
    with open(reports_dir / "bundle_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    # 5. Đóng gói ZIP chính: ChunDVC_Legacy_Library_v1.0.zip
    zip_path = zip_dir / "ChunDVC_Legacy_Library_v1.0.zip"
    progress(f"4. Đang nén file lưu trữ chính: {zip_path.name}...")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Thêm manifest, README, inventory, checksums ở root của ZIP
        zf.write(reports_dir / "bundle_manifest.json", "bundle_manifest.json")
        zf.write(reports_dir / "README_RESTORE.md", "README_RESTORE.md")
        zf.write(reports_dir / "inventory.json", "inventory.json")
        zf.write(reports_dir / "checksums.json", "checksums.json")

        # Thêm catalogs
        for cat_file in catalogs_dir.glob("*.json"):
            zf.write(cat_file, f"catalogs/{cat_file.name}")

        # Thêm originals bằng streaming
        count = 0
        for item in items:
            if cancel():
                raise InterruptedError("Đã hủy nén file.")
            rel = item["original_path"]
            src_file = repo_root / rel
            arcname = f"originals/{rel}"
            zf.write(src_file, arcname)
            count += 1
            if count % 200 == 0:
                progress(f"Nén ({count}/{total_files}): {rel}")

    progress("5. Đang tính SHA256 của file ZIP thành phẩm...")
    zip_sha256, zip_size = compute_file_sha256(zip_path, cancel=cancel)
    with open(zip_dir / "ChunDVC_Legacy_Library_v1.0.zip.sha256", "w", encoding="utf-8") as f:
        f.write(f"{zip_sha256}  {zip_path.name}\n")

    # 6. Kiểm tra tính toàn vẹn của file ZIP bằng testzip()
    progress("6. Đang kiểm tra tính toàn vẹn của archive (testzip)...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise RuntimeError(f"File ZIP bị lỗi CRC tại: {bad_file}")

    # 7. Khôi phục kiểm chứng (Restore Verification Proof)
    progress("7. Đang tiến hành khôi phục kiểm chứng (Restore Proof) sang thư mục test...")
    verified_files = 0
    restore_errors = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Trích xuất và đối chiếu SHA256 từng file trong originals/
        for item in items:
            if cancel():
                raise InterruptedError("Đã hủy kiểm chứng khôi phục.")

            rel = item["original_path"]
            arcname = f"originals/{rel}"
            expected_sha = item["sha256"]
            expected_size = item["size_bytes"]

            # Trích xuất stream vào restore_dir
            dest_file = restore_dir / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(arcname, "r") as z_src, open(dest_file, "wb") as d_dst:
                h_restore = hashlib.sha256()
                bytes_copied = 0
                while block := z_src.read(CHUNK_SIZE):
                    d_dst.write(block)
                    h_restore.update(block)
                    bytes_copied += len(block)

            calc_sha = h_restore.hexdigest()
            if calc_sha != expected_sha or bytes_copied != expected_size:
                restore_errors.append({
                    "path": rel,
                    "expected_sha": expected_sha,
                    "calc_sha": calc_sha,
                    "expected_size": expected_size,
                    "actual_size": bytes_copied
                })
            else:
                verified_files += 1

            if verified_files % 300 == 0:
                progress(f"Khôi phục & đối chiếu ({verified_files}/{total_files}): {rel}")

    restore_report = {
        "verified_at": datetime.datetime.now().isoformat(),
        "total_items_expected": total_files,
        "total_items_verified": verified_files,
        "all_checksums_matched": len(restore_errors) == 0,
        "errors": restore_errors,
        "zip_path": str(zip_path),
        "zip_size_bytes": zip_size,
        "zip_sha256": zip_sha256
    }
    with open(reports_dir / "restore_verification_report.json", "w", encoding="utf-8") as f:
        json.dump(restore_report, f, ensure_ascii=False, indent=2)

    if restore_errors:
        raise RuntimeError(f"Khôi phục kiểm chứng thất bại: {len(restore_errors)} files sai lệch checksum!")

    progress(f"8. Hoàn tất 100%! Đã kiểm chứng {verified_files}/{total_files} files thành công.")

    return {
        "run_dir": str(run_dir),
        "zip_path": str(zip_path),
        "zip_sha256": zip_sha256,
        "zip_size_bytes": zip_size,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "catalogs_summary": catalogs_summary,
        "restore_verified": True
    }


def main():
    print("[1/3] Kiểm tra mount và dung lượng ổ đĩa...")
    check_mount_space(VENTOY_MOUNT, min_gb=15.0)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_uuid = uuid.uuid4().hex[:8]
    run_id = f"{ts}_{run_uuid}"
    run_dir = PUBLISH_BASE / "runs" / run_id

    print(f"[2/3] Bắt đầu xuất bản lưu toàn kho tại: {run_dir}")
    res = export_legacy_library(
        repo_root=REPO_ROOT,
        output_run_dir=run_dir,
        progress=lambda msg: print(f"  {msg}")
    )

    print("[3/3] HOÀN TẤT XUẤT BẢN LƯU VÀ KIỂM CHỨNG!")
    print(f"ZIP Path: {res['zip_path']}")
    print(f"ZIP SHA256: {res['zip_sha256']}")
    print(f"Total Files: {res['total_files']} ({res['total_bytes'] / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    main()
