"""
src/core/collections_manager.py
Quản lý các bộ sưu tập tùy chỉnh (Custom Collections) của người dùng trong Kho Đạo Cụ / Yêu Thích.
Lưu trữ tại data/collections.json.
"""
from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional

logger = logging.getLogger("ResolveFlow.CollectionsManager")


class CollectionsManager:
    FILE_PATH = Path("data") / "collections.json"
    _cache: Optional[tuple] = None

    @classmethod
    def load(cls) -> Dict[str, List[str]]:
        """Tải dữ liệu danh sách bộ sưu tập {coll_name: [asset_id, ...]} (có bộ nhớ đệm theo mtime)."""
        try:
            st = cls.FILE_PATH.stat()
            cache_key = (str(cls.FILE_PATH), st.st_mtime_ns, st.st_size)
        except OSError:
            if not cls.FILE_PATH.parent.exists():
                try:
                    cls.FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            cls._cache = None
            return {}

        if cls._cache is not None and cls._cache[0] == cache_key:
            return {k: list(v) for k, v in cls._cache[1].items()}

        try:
            with open(cls.FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    normalized = {str(k): [str(x) for x in v] if isinstance(v, list) else [] for k, v in data.items()}
                    cls._cache = (cache_key, normalized)
                    return {k: list(v) for k, v in normalized.items()}
                return {}
        except Exception as e:
            logger.error(f"Error loading collections: {e}")
            return {}

    @classmethod
    def save(cls, data: Dict[str, List[str]]) -> bool:
        """Lưu toàn bộ danh mục bộ sưu tập nguyên tử."""
        path = Path(cls.FILE_PATH)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.{os.getpid()}_{time.time_ns()}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            try:
                st = path.stat()
                normalized = {str(k): [str(x) for x in v] if isinstance(v, list) else [] for k, v in data.items()}
                cls._cache = ((str(path), st.st_mtime_ns, st.st_size), normalized)
            except OSError:
                cls._cache = None
            return True
        except Exception as e:
            cls._cache = None
            logger.error(f"Error saving collections: {e}")
            try:
                if 'tmp_path' in locals() and tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            return False

    @classmethod
    def get_collection_names(cls) -> List[str]:
        """Lấy danh sách tên tất cả bộ sưu tập đã tạo."""
        data = cls.load()
        return sorted(list(data.keys()))

    @classmethod
    def create_collection(cls, name: str) -> bool:
        """Tạo bộ sưu tập mới nếu chưa tồn tại."""
        clean_name = (name or "").strip()
        if not clean_name:
            return False
        data = cls.load()
        if clean_name in data:
            return False  # Đã tồn tại
        data[clean_name] = []
        return cls.save(data)

    @classmethod
    def delete_collection(cls, name: str) -> bool:
        """Xóa bộ sưu tập."""
        clean_name = (name or "").strip()
        data = cls.load()
        if clean_name in data:
            del data[clean_name]
            return cls.save(data)
        return False

    @classmethod
    def rename_collection(cls, old_name: str, new_name: str) -> bool:
        """Đổi tên bộ sưu tập."""
        old = (old_name or "").strip()
        new = (new_name or "").strip()
        if not old or not new or old == new:
            return False
        data = cls.load()
        if old not in data or new in data:
            return False
        items = data.pop(old)
        data[new] = items
        return cls.save(data)

    @classmethod
    def get_collection_assets(cls, name: str) -> List[str]:
        """Lấy danh sách asset_id trong bộ sưu tập."""
        data = cls.load()
        return data.get(name, [])

    @classmethod
    def get_asset_collections(cls, asset_id: str) -> List[str]:
        """Tìm các bộ sưu tập mà asset_id này thuộc về."""
        if not asset_id:
            return []
        data = cls.load()
        return [cname for cname, items in data.items() if asset_id in items]

    @classmethod
    def add_asset_to_collection(cls, name: str, asset_id: str) -> bool:
        """Thêm asset vào bộ sưu tập."""
        if not name or not asset_id:
            return False
        data = cls.load()
        if name not in data:
            data[name] = []
        if asset_id not in data[name]:
            data[name].append(asset_id)
            return cls.save(data)
        return True

    add_to_collection = add_asset_to_collection

    @classmethod
    def remove_asset_from_collection(cls, name: str, asset_id: str) -> bool:
        """Xóa asset khỏi bộ sưu tập."""
        if not name or not asset_id:
            return False
        data = cls.load()
        if name in data and asset_id in data[name]:
            data[name].remove(asset_id)
            return cls.save(data)
        return True

    @classmethod
    def toggle_asset_collection(cls, name: str, asset_id: str) -> bool:
        """Bật/tắt asset trong bộ sưu tập (trả về True nếu được thêm, False nếu bị bỏ)."""
        if not name or not asset_id:
            return False
        data = cls.load()
        if name not in data:
            data[name] = [asset_id]
            return cls.save(data)
        if asset_id in data[name]:
            data[name].remove(asset_id)
            if cls.save(data):
                return False
            return True
        else:
            data[name].append(asset_id)
            return cls.save(data)

    @classmethod
    def remove_asset_from_all(cls, asset_id: str) -> bool:
        """Xóa hoàn toàn asset_id khỏi tất cả bộ sưu tập."""
        if not asset_id:
            return True
        data = cls.load()
        changed = False
        for cname, items in data.items():
            if asset_id in items:
                items.remove(asset_id)
                changed = True
        if changed:
            return cls.save(data)
        return True

    @classmethod
    def get_ungrouped_favorites(cls, all_favorites: Set[str]) -> List[str]:
        """Lấy danh sách các asset yêu thích chưa thuộc bất kỳ bộ sưu tập nào."""
        data = cls.load()
        grouped = set()
        for items in data.values():
            grouped.update(items)
        return [fid for fid in all_favorites if fid not in grouped]
