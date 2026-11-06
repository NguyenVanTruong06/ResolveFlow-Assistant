"""
src/core/storage_inventory.py
Dịch vụ kiểm kê kho chỉ đọc (Bounded Read-only Storage Inventory):
- Kiểm kê có giới hạn (max_items, budget_sec, trần metadata direct 32 MiB, cancel).
- Ngân sách đọc & Dung sai (Budget Allowance):
  * Direct metadata budget: tối đa 32 MiB cho các thao tác đọc trực tiếp trong inventory
    (registry.json, pack_manifest.json, và library.json recheck).
  * Delegated health check allowance: get_state() thực hiện đọc kiểm tra marker riêng biệt
    (được giới hạn độc lập bởi MAX_JSON + 1 theo StorageManager API hiện có, ~16 MiB).
  * Sentinel read allowance: mỗi thao tác đọc có sentinel buffer tối đa 1 byte để phát hiện tràn.
  * Worst-case direct + health + sentinel được ghi nhận rõ ràng, không claim 32 MiB tuyệt đối cho toàn bộ process.
- Trạng thái báo cáo: complete, partial, cancelled, offline, busy, error.
- Phân loại items: staging_candidate, unregistered_pack, invalid_entry với ownership="unknown".
- An toàn chỉ đọc (Pure Read-Only): Không ghi, không xóa, không acquire/remove lock, không sửa registry/snapshot.
- Không quét thư mục liên kết 03_local_linked, không đọc/hash media.
"""

from __future__ import annotations

import json
import logging
import math
import os
import stat
import time
from pathlib import Path
from typing import Callable

from src.core.storage_manager import (
    StorageManager,
    StorageState,
    MAX_JSON,
    safe_relative,
)

logger = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 2 * 1024 * 1024  # 2 MiB per pack_manifest.json
MAX_TOTAL_METADATA_BYTES = 32 * 1024 * 1024  # 32 MiB total direct metadata budget


def _bounded_read_file(
    file_path: Path,
    per_file_limit: int,
    remaining_budget: int,
) -> tuple[bytes, bool, bool]:
    """
    Đọc tệp có giới hạn ngân sách và phát hiện tràn (sentinel +1 byte).
    Trả về: (raw_bytes, file_overflow, budget_overflow)
    - file_overflow: True nếu số byte trong file vượt quá per_file_limit.
    - budget_overflow: True nếu số byte đọc được vượt quá remaining_budget.
    """
    read_limit = min(per_file_limit, remaining_budget)
    with open(file_path, "rb") as f:
        data = f.read(read_limit + 1)
    file_overflow = len(data) > per_file_limit
    budget_overflow = len(data) > remaining_budget
    return data, file_overflow, budget_overflow


class StorageInventoryService:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def scan_inventory(
        self,
        cancel: Callable[[], bool] = lambda: False,
        *,
        max_items: int = 1000,
        budget_sec: float = 2.0,
    ) -> dict:
        """
        Quét kiểm kê kho tài nguyên media với các ràng buộc chỉ đọc và giới hạn tài nguyên.
        Trả về báo cáo có cấu trúc:
        {
            'status': 'complete' | 'partial' | 'cancelled' | 'offline' | 'busy' | 'error',
            'library_id': str | None,
            'items': list[dict],
            'warnings': list[str],
            'scanned_items': int,
            'truncated': bool,
            'consistency': 'unverified',
        }
        """
        # 1. Xác thực tham số đầu vào (Bounds Check & Big-int Protection)
        if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items <= 0:
            raise ValueError("max_items phải là số nguyên dương")
        if not isinstance(budget_sec, (int, float)) or isinstance(budget_sec, bool):
            raise ValueError("budget_sec phải là số dương hữu hạn")
        try:
            val = float(budget_sec)
            if not math.isfinite(val) or val <= 0:
                raise ValueError("budget_sec phải là số dương hữu hạn")
            budget_float = val
        except (OverflowError, ValueError) as exc:
            raise ValueError("budget_sec phải là số dương hữu hạn") from exc

        # 2. Khởi tạo ngân sách và đồng hồ TRƯỚC KHI thực hiện bất kỳ lệnh I/O nào
        start_time = time.monotonic()
        try:
            deadline = start_time + budget_float
        except (OverflowError, ValueError) as exc:
            raise ValueError("budget_sec quá lớn") from exc

        expected_id = self.storage._config.get("library_id")
        warnings: list[str] = []
        items: list[dict] = []
        scanned_items = 0
        truncated = False
        total_metadata_read = 0
        dir_scan_errors: list[str] = []
        entry_io_errors: list[str] = []

        def check_limits() -> bool:
            nonlocal truncated
            if cancel():
                return False
            if time.monotonic() >= deadline or total_metadata_read >= MAX_TOTAL_METADATA_BYTES:
                truncated = True
                return False
            return True

        def _make_report(final_status: str, is_truncated: bool = False) -> dict:
            return {
                "status": final_status,
                "library_id": expected_id,
                "items": items,
                "warnings": warnings,
                "scanned_items": scanned_items,
                "truncated": is_truncated,
                "consistency": "unverified",
            }

        # 3. Kiểm tra hủy ngay sau khi xác thực
        if not check_limits():
            return _make_report("cancelled" if cancel() else "partial", is_truncated=truncated)

        # 4. Kiểm tra trạng thái kho qua get_state()
        state = self.storage.get_state()
        if state == StorageState.OFFLINE:
            warnings.append("Kho đang ở trạng thái ngoại tuyến (OFFLINE).")
            return _make_report("offline")
        if state == StorageState.ERROR:
            warnings.append("Kho đang ở trạng thái lỗi cấu trúc (ERROR).")
            return _make_report("error")
        # READ_ONLY, FULL, ONLINE được phép scan chỉ đọc

        if not check_limits():
            return _make_report("cancelled" if cancel() else "partial", is_truncated=truncated)

        # 5. Kiểm tra sự hiện diện của tệp khóa (Lock Presence Check)
        meta_dir = self.storage.meta
        lock_file = meta_dir / "registry.lock"
        try:
            lock_file.lstat()
            warnings.append("Kho đang có tiến trình khác hoạt động (registry.lock tồn tại).")
            return _make_report("busy")
        except FileNotFoundError:
            pass  # Không có lock, an toàn để đọc
        except OSError as exc:
            warnings.append(f"Không thể kiểm tra tệp khóa registry.lock: {exc}")
            return _make_report("error")

        if not check_limits():
            return _make_report("cancelled" if cancel() else "partial", is_truncated=truncated)

        # 6. Đọc có giới hạn external registry.json và xác thực tính thẩm quyền (Authority Validation)
        reg_file = meta_dir / "registry.json"
        registered_folders: dict[str, str] = {}  # canonical relative path -> pack_id
        registered_pack_ids: set[str] = set()

        try:
            reg_st = reg_file.lstat()
            if stat.S_ISLNK(reg_st.st_mode):
                warnings.append("Tệp registry.json là symlink không an toàn.")
                return _make_report("error")
            if not stat.S_ISREG(reg_st.st_mode):
                warnings.append("Tệp registry.json không phải là tệp thường.")
                return _make_report("error")
            if reg_st.st_size > MAX_JSON:
                warnings.append(f"Tệp registry.json vượt quá kích thước an toàn ({MAX_JSON} bytes).")
                return _make_report("error")

            remaining_bytes = MAX_TOTAL_METADATA_BYTES - total_metadata_read
            if remaining_bytes <= 0:
                truncated = True
                return _make_report("partial", is_truncated=True)

            try:
                reg_bytes, file_overflow, budget_overflow = _bounded_read_file(
                    reg_file, MAX_JSON, remaining_bytes
                )
                total_metadata_read += len(reg_bytes)
            except OSError as exc:
                warnings.append(f"Lỗi đọc registry.json: {exc}")
                return _make_report("error")

            if file_overflow:
                warnings.append("Tệp registry.json bị phình to quá giới hạn an toàn trong khi đọc.")
                return _make_report("error")
            if budget_overflow or total_metadata_read >= MAX_TOTAL_METADATA_BYTES:
                truncated = True
                return _make_report("partial", is_truncated=True)

            registry_data = json.loads(reg_bytes.decode("utf-8"))
            if not isinstance(registry_data, dict):
                warnings.append("Tệp registry.json không phải là JSON object hợp lệ.")
                return _make_report("error")

            # schema_version nếu key hiện diện thì bắt buộc là int không bool và == 1; missing key giữ legacy compatibility
            if "schema_version" in registry_data:
                schema_ver = registry_data["schema_version"]
                if type(schema_ver) is not int or schema_ver != 1:
                    warnings.append("Tệp registry.json có schema_version không hợp lệ.")
                    return _make_report("error")

            if not isinstance(registry_data.get("packs"), list) or not isinstance(registry_data.get("assets"), list):
                warnings.append("Tệp registry.json thiếu danh sách packs hoặc assets hợp lệ.")
                return _make_report("error")

            # Xác thực từng bản ghi pack trong registry
            for pack_entry in registry_data["packs"]:
                if not check_limits():
                    return _make_report("cancelled" if cancel() else "partial", is_truncated=truncated)

                if not isinstance(pack_entry, dict):
                    warnings.append("Bản ghi pack trong registry.json không phải là dict.")
                    return _make_report("error")

                pid = pack_entry.get("id")
                folder_str = pack_entry.get("folder")
                if not isinstance(pid, str) or not pid.strip():
                    warnings.append("Bản ghi pack trong registry.json thiếu ID hợp lệ.")
                    return _make_report("error")
                if not isinstance(folder_str, str) or not folder_str.strip():
                    warnings.append(f"Bản ghi pack '{pid}' trong registry.json thiếu đường dẫn folder.")
                    return _make_report("error")

                try:
                    norm_path = safe_relative(folder_str)
                except ValueError as exc:
                    warnings.append(f"Đường dẫn folder không an toàn trong registry.json: {folder_str} ({exc})")
                    return _make_report("error")

                # Bắt buộc là thư mục con trực tiếp dưới 02_cloud_downloads/packs
                parts = norm_path.parts
                if len(parts) != 3 or parts[0] != "02_cloud_downloads" or parts[1] != "packs":
                    warnings.append(
                        f"Đường dẫn '{folder_str}' không phải là thư mục con trực tiếp của 02_cloud_downloads/packs."
                    )
                    return _make_report("error")

                norm_folder = norm_path.as_posix()
                if norm_folder in registered_folders:
                    warnings.append(f"Xung đột thư mục '{norm_folder}' được đăng ký nhiều lần trong registry.json.")
                    return _make_report("error")
                if pid in registered_pack_ids:
                    warnings.append(f"Xung đột pack_id '{pid}' được đăng ký nhiều lần trong registry.json.")
                    return _make_report("error")

                registered_folders[norm_folder] = pid
                registered_pack_ids.add(pid)

        except FileNotFoundError:
            warnings.append("Không tìm thấy tệp registry.json trên kho.")
            return _make_report("error")
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            warnings.append(f"Tệp registry.json bị hỏng cú pháp: {exc}")
            return _make_report("error")
        except OSError as exc:
            warnings.append(f"Lỗi đọc registry.json: {exc}")
            return _make_report("error")

        seen_unregistered_pack_ids: dict[str, str] = {}  # pack_id -> rel_folder

        # 7. Quét trực tiếp con thư mục staging (.chundvc_meta/staging)
        staging_dir = meta_dir / "staging"
        staging_accessible = False
        try:
            st_st = staging_dir.lstat()
            if stat.S_ISLNK(st_st.st_mode):
                warnings.append("Thư mục staging là symlink không an toàn.")
                dir_scan_errors.append("staging")
            elif not stat.S_ISDIR(st_st.st_mode):
                warnings.append("Đường dẫn staging không phải là thư mục.")
                dir_scan_errors.append("staging")
            else:
                staging_accessible = True
        except FileNotFoundError:
            pass  # Thư mục staging chưa tạo là hợp lệ theo chính sách optional
        except OSError as exc:
            warnings.append(f"Không thể kiểm tra thư mục staging: {exc}")
            dir_scan_errors.append("staging")

        if staging_accessible and check_limits():
            try:
                with os.scandir(staging_dir) as entries:
                    for entry in entries:
                        if not check_limits():
                            break
                        if scanned_items >= max_items:
                            truncated = True
                            break
                        scanned_items += 1

                        rel_path = f".chundvc_meta/staging/{entry.name}"
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(st.st_mode):
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Symlink không an toàn trong thư mục staging",
                                })
                            elif stat.S_ISDIR(st.st_mode):
                                items.append({
                                    "kind": "staging_candidate",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Thư mục staging dở dang",
                                })
                            else:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Tệp không hợp lệ trong thư mục staging",
                                })
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            entry_io_errors.append(f"staging/{entry.name}: {exc}")
                            warnings.append(f"Không thể kiểm tra mục staging '{entry.name}': {exc}")
                            items.append({
                                "kind": "invalid_entry",
                                "relative_path": rel_path,
                                "pack_id": None,
                                "ownership": "unknown",
                                "reason": f"Lỗi truy vấn thư mục con staging: {exc}",
                            })
            except OSError as exc:
                warnings.append(f"Không thể quét danh sách thư mục staging: {exc}")
                dir_scan_errors.append("staging")

        # 8. Quét trực tiếp con thư mục packs (02_cloud_downloads/packs)
        packs_dir = self.storage.root / "02_cloud_downloads" / "packs"
        packs_accessible = False
        try:
            p_st = packs_dir.lstat()
            if stat.S_ISLNK(p_st.st_mode):
                warnings.append("Thư mục packs là symlink không an toàn.")
                dir_scan_errors.append("packs")
            elif not stat.S_ISDIR(p_st.st_mode):
                warnings.append("Đường dẫn packs không phải là thư mục.")
                dir_scan_errors.append("packs")
            else:
                packs_accessible = True
        except FileNotFoundError:
            pass  # Thư mục packs chưa tạo là hợp lệ
        except OSError as exc:
            warnings.append(f"Không thể kiểm tra thư mục packs: {exc}")
            dir_scan_errors.append("packs")

        if packs_accessible and check_limits() and not truncated:
            try:
                with os.scandir(packs_dir) as entries:
                    for entry in entries:
                        if not check_limits():
                            break
                        if scanned_items >= max_items:
                            truncated = True
                            break
                        scanned_items += 1

                        rel_path = f"02_cloud_downloads/packs/{entry.name}"
                        rel_folder = rel_path
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(st.st_mode):
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Symlink không an toàn trong thư mục packs",
                                })
                                continue
                            if not stat.S_ISDIR(st.st_mode):
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Tệp thường nằm ngoài cấu trúc pack",
                                })
                                continue

                            # Thư mục pack hợp lệ: Nếu đã đăng ký trong registry, bỏ qua
                            if rel_path in registered_folders:
                                continue

                            # Chưa đăng ký: Kiểm tra tệp pack_manifest.json
                            manifest_path = Path(entry.path) / "pack_manifest.json"
                            try:
                                mf_st = manifest_path.stat(follow_symlinks=False)
                                if stat.S_ISLNK(mf_st.st_mode):
                                    items.append({
                                        "kind": "invalid_entry",
                                        "relative_path": rel_path,
                                        "pack_id": None,
                                        "ownership": "unknown",
                                        "reason": "pack_manifest.json là symlink không an toàn",
                                    })
                                    continue
                                if not stat.S_ISREG(mf_st.st_mode):
                                    items.append({
                                        "kind": "invalid_entry",
                                        "relative_path": rel_path,
                                        "pack_id": None,
                                        "ownership": "unknown",
                                        "reason": "Thiếu hoặc sai kiểu tệp pack_manifest.json",
                                    })
                                    continue
                                if mf_st.st_size > MAX_MANIFEST_BYTES:
                                    items.append({
                                        "kind": "invalid_entry",
                                        "relative_path": rel_path,
                                        "pack_id": None,
                                        "ownership": "unknown",
                                        "reason": "pack_manifest.json vượt quá 2 MiB",
                                    })
                                    continue
                            except FileNotFoundError:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Thiếu tệp pack_manifest.json",
                                })
                                continue
                            except OSError as exc:
                                entry_io_errors.append(f"pack_manifest_stat {entry.name}: {exc}")
                                warnings.append(
                                    f"Không thể kiểm tra tệp pack_manifest.json của gói '{entry.name}': {exc}"
                                )
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": f"Lỗi kiểm tra tệp pack_manifest.json: {exc}",
                                })
                                continue

                            # Đọc manifest có kiểm tra giới hạn ngân sách
                            if not check_limits():
                                break

                            remaining_bytes = MAX_TOTAL_METADATA_BYTES - total_metadata_read
                            if remaining_bytes <= 0:
                                truncated = True
                                break

                            try:
                                mf_bytes, file_overflow, budget_overflow = _bounded_read_file(
                                    manifest_path, MAX_MANIFEST_BYTES, remaining_bytes
                                )
                                total_metadata_read += len(mf_bytes)
                            except FileNotFoundError:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "Thiếu tệp pack_manifest.json",
                                })
                                continue
                            except OSError as exc:
                                entry_io_errors.append(f"pack_manifest_read {entry.name}: {exc}")
                                warnings.append(
                                    f"Không thể đọc tệp pack_manifest.json của gói '{entry.name}': {exc}"
                                )
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": f"Lỗi đọc pack_manifest.json: {exc}",
                                })
                                continue

                            # Post-check length: kiểm tra file có phình to sau lstat
                            if file_overflow:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "pack_manifest.json vượt quá giới hạn 2 MiB trong khi đọc",
                                })
                                continue
                            if budget_overflow or total_metadata_read >= MAX_TOTAL_METADATA_BYTES:
                                truncated = True
                                break

                            try:
                                manifest_data = json.loads(mf_bytes.decode("utf-8"))
                            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": f"pack_manifest.json hỏng cú pháp: {exc}",
                                })
                                continue

                            if not isinstance(manifest_data, dict):
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "pack_manifest.json không phải là JSON object",
                                })
                                continue

                            mf_schema = manifest_data.get("schema_version")
                            if type(mf_schema) is not int or mf_schema != 1:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "pack_manifest.json có schema_version không hợp lệ",
                                })
                                continue

                            pack_id = manifest_data.get("pack_id")
                            if not isinstance(pack_id, str) or not pack_id.strip():
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": None,
                                    "ownership": "unknown",
                                    "reason": "pack_manifest.json thiếu pack_id hợp lệ",
                                })
                                continue

                            assets_list = manifest_data.get("assets")
                            if not isinstance(assets_list, list):
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_path,
                                    "pack_id": pack_id,
                                    "ownership": "unknown",
                                    "reason": "manifest.assets không phải là danh sách",
                                })
                                continue

                            # Kiểm tra nghiêm ngặt từng asset kèm check_limits giữa các asset
                            invalid_asset = False
                            validation_interrupted = False
                            for asset in assets_list:
                                if not check_limits():
                                    validation_interrupted = True
                                    break

                                if not isinstance(asset, dict):
                                    invalid_asset = True
                                    break
                                asset_rel = asset.get("relative_path")
                                if not isinstance(asset_rel, str) or not asset_rel.strip():
                                    invalid_asset = True
                                    break
                                try:
                                    p = safe_relative(asset_rel)
                                    if p.as_posix() in (".", ""):
                                        invalid_asset = True
                                        break
                                except ValueError:
                                    invalid_asset = True
                                    break

                            if validation_interrupted:
                                # Validation bị gián đoạn, không được phân loại pack là hợp lệ!
                                break

                            if invalid_asset:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": pack_id,
                                    "ownership": "unknown",
                                    "reason": "manifest.assets chứa asset sai cấu trúc hoặc đường dẫn không an toàn",
                                })
                                continue

                            # Kiểm tra xung đột pack_id
                            if pack_id in registered_pack_ids:
                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": pack_id,
                                    "ownership": "unknown",
                                    "reason": f"Trùng lặp pack_id '{pack_id}' với gói đã đăng ký",
                                })
                                continue

                            if pack_id in seen_unregistered_pack_ids:
                                # Xung đột với một gói unregistered đã duyệt trước đó
                                # Cập nhật lại gói trước thành conflict/invalid_entry
                                for prev in items:
                                    if prev.get("pack_id") == pack_id and prev["kind"] == "unregistered_pack":
                                        prev["kind"] = "invalid_entry"
                                        prev["reason"] = f"Xung đột trùng lặp pack_id '{pack_id}' giữa các gói"

                                items.append({
                                    "kind": "invalid_entry",
                                    "relative_path": rel_folder,
                                    "pack_id": pack_id,
                                    "ownership": "unknown",
                                    "reason": f"Xung đột trùng lặp pack_id '{pack_id}' giữa các gói",
                                })
                                continue

                            seen_unregistered_pack_ids[pack_id] = rel_folder
                            items.append({
                                "kind": "unregistered_pack",
                                "relative_path": rel_folder,
                                "pack_id": pack_id,
                                "ownership": "unknown",
                                "reason": "Gói media trên đĩa chưa được đăng ký trong registry",
                            })

                        except FileNotFoundError:
                            continue
                        except OSError as exc:
                            entry_io_errors.append(f"packs/{entry.name}: {exc}")
                            warnings.append(f"Không thể kiểm tra mục packs '{entry.name}': {exc}")
                            items.append({
                                "kind": "invalid_entry",
                                "relative_path": rel_folder,
                                "pack_id": None,
                                "ownership": "unknown",
                                "reason": f"Lỗi truy vấn thư mục pack: {exc}",
                            })
            except OSError as exc:
                warnings.append(f"Không thể quét danh sách thư mục packs: {exc}")
                dir_scan_errors.append("packs")

        # 9. Kiểm tra hủy/lỗi trước khi tái kiểm tra danh tính kho
        if cancel():
            return _make_report("cancelled", is_truncated=False)

        if dir_scan_errors:
            if "staging" in dir_scan_errors and "packs" in dir_scan_errors:
                return _make_report("error", is_truncated=False)
            return _make_report("partial", is_truncated=True)

        if entry_io_errors:
            if scanned_items - len(entry_io_errors) <= 0:
                return _make_report("error", is_truncated=False)
            return _make_report("partial", is_truncated=True)

        if truncated:
            return _make_report("partial", is_truncated=True)

        # 10. Tái kiểm tra danh tính kho trước khi trả kết quả (Recheck Identity)
        marker_file = meta_dir / "library.json"
        remaining_bytes = MAX_TOTAL_METADATA_BYTES - total_metadata_read
        if remaining_bytes <= 0:
            truncated = True
            return _make_report("partial", is_truncated=True)

        try:
            m_st = marker_file.lstat()
            if stat.S_ISLNK(m_st.st_mode) or not stat.S_ISREG(m_st.st_mode):
                warnings.append("Danh tính kho đã thay đổi hoặc bị ngắt kết nối trong khi quét.")
                return _make_report("offline", is_truncated=False)
        except OSError:
            warnings.append("Danh tính kho đã thay đổi hoặc bị ngắt kết nối trong khi quét.")
            return _make_report("offline", is_truncated=False)

        try:
            marker_bytes, file_overflow, budget_overflow = _bounded_read_file(
                marker_file, 4096, remaining_bytes
            )
            total_metadata_read += len(marker_bytes)
        except OSError:
            warnings.append("Danh tính kho đã thay đổi hoặc bị ngắt kết nối trong khi quét.")
            return _make_report("offline", is_truncated=False)

        if budget_overflow or total_metadata_read >= MAX_TOTAL_METADATA_BYTES:
            truncated = True
            return _make_report("partial", is_truncated=True)

        if file_overflow:
            warnings.append("Tệp library.json vượt quá kích thước an toàn trong khi đọc.")
            return _make_report("offline", is_truncated=False)

        try:
            marker_data = json.loads(marker_bytes.decode("utf-8"))
            if not isinstance(marker_data, dict) or marker_data.get("library_id") != expected_id:
                warnings.append("Danh tính kho đã thay đổi hoặc bị ngắt kết nối trong khi quét.")
                return _make_report("offline", is_truncated=False)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            warnings.append("Danh tính kho đã thay đổi hoặc bị ngắt kết nối trong khi quét.")
            return _make_report("offline", is_truncated=False)

        # 11. Quyết định trạng thái cuối (Report Truth)
        if cancel():
            return _make_report("cancelled", is_truncated=False)

        if truncated:
            return _make_report("partial", is_truncated=True)

        return _make_report("complete", is_truncated=False)
