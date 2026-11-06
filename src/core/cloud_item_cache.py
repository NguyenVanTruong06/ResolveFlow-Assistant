"""
Cloud On-Demand Item Cache (Lazy per-item download).

Thay vì tải nguyên gói, người dùng duyệt catalog (JSON nhẹ, vài KB) và chỉ tải
đúng 1 item khi cần dùng. File được lưu vào cache cục bộ, kiểm SHA-256 và
tái sử dụng ở lần sau (không tải lại).

Catalog format (catalog_items.json):
{
  "version": 1,
  "items": [
    {"id": "whoosh_01", "category": "sfx", "name": "Whoosh 01",
     "url": "https://.../sfx/whoosh_01.wav", "sha256": "...", "size": 123456,
     "ext": ".wav", "preview_url": "https://.../sfx/whoosh_01.ogg", "tags": ["whoosh"]}
  ]
}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_MAX_ITEM_BYTES = 512 * 1024 * 1024  # 512 MB / item
ALLOWED_SCHEMES = ("https://", "http://", "file://")


def _safe(name: str) -> str:
    cleaned = _SAFE.sub("_", str(name)).strip("._")
    return cleaned or "item"


def default_cache_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ChunDVC" / "cloud_item_cache"


@dataclass
class CloudItem:
    id: str
    category: str
    url: str
    name: str = ""
    sha256: str = ""
    size: int = 0
    ext: str = ""
    preview_url: str = ""
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict) -> "CloudItem":
        if not d.get("id") or not d.get("url") or not d.get("category"):
            raise ValueError(f"Catalog item thiếu id/url/category: {d!r}")
        ext = d.get("ext") or os.path.splitext(str(d["url"]).split("?")[0])[1]
        return cls(
            id=str(d["id"]),
            category=str(d["category"]),
            url=str(d["url"]),
            name=str(d.get("name") or d["id"]),
            sha256=str(d.get("sha256") or "").lower(),
            size=int(d.get("size") or 0),
            ext=ext if (not ext or ext.startswith(".")) else f".{ext}",
            preview_url=str(d.get("preview_url") or ""),
            tags=list(d.get("tags") or []),
        )


class CloudItemCache:
    """Tải từng item theo yêu cầu, cache cục bộ, xác minh SHA-256."""

    def __init__(
        self,
        cache_root: Optional[Path] = None,
        max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
        opener: Optional[Callable[[str], object]] = None,
        timeout: float = 30.0,
    ):
        self.cache_root = Path(cache_root) if cache_root else default_cache_root()
        self.max_item_bytes = max_item_bytes
        self.timeout = timeout
        self._opener = opener or (lambda url: urllib.request.urlopen(url, timeout=self.timeout))
        self._items: Dict[str, CloudItem] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ---------- catalog ----------
    def load_catalog(self, data: Dict | str | Path) -> List[CloudItem]:
        if isinstance(data, (str, Path)) and not str(data).lstrip().startswith("{"):
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        elif isinstance(data, str):
            data = json.loads(data)
        items = [CloudItem.from_dict(d) for d in data.get("items", [])]
        self._items = {i.id: i for i in items}
        return items

    def items(self, category: Optional[str] = None) -> List[CloudItem]:
        return [i for i in self._items.values() if category is None or i.category == category]

    def get(self, item_id: str) -> Optional[CloudItem]:
        return self._items.get(item_id)

    # ---------- cache ----------
    def local_path(self, item: CloudItem) -> Path:
        return self.cache_root / _safe(item.category) / f"{_safe(item.id)}{item.ext}"

    def is_cached(self, item: CloudItem) -> bool:
        p = self.local_path(item)
        if not p.is_file():
            return False
        if item.sha256:
            return self._sha256(p) == item.sha256
        return item.size <= 0 or p.stat().st_size == item.size

    def evict(self, item: CloudItem) -> bool:
        p = self.local_path(item)
        if p.exists():
            p.unlink()
            return True
        return False

    def cache_size_bytes(self) -> int:
        if not self.cache_root.exists():
            return 0
        return sum(f.stat().st_size for f in self.cache_root.rglob("*") if f.is_file())

    def clear(self) -> None:
        for f in sorted(self.cache_root.rglob("*"), reverse=True) if self.cache_root.exists() else []:
            f.unlink() if f.is_file() else f.rmdir()

    # ---------- fetch ----------
    def fetch(
        self,
        item: CloudItem | str,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Trả về đường dẫn file cục bộ; tải nếu chưa có trong cache."""
        if isinstance(item, str):
            found = self.get(item)
            if found is None:
                raise KeyError(f"Không có item '{item}' trong catalog")
            item = found
        if not item.url.startswith(ALLOWED_SCHEMES):
            raise ValueError(f"URL không hợp lệ: {item.url}")
        if item.size and item.size > self.max_item_bytes:
            raise ValueError(f"Item {item.id} quá lớn ({item.size} bytes)")

        with self._guard:
            lock = self._locks.setdefault(item.id, threading.Lock())
        with lock:
            target = self.local_path(item)
            if self.is_cached(item):
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".part_", dir=str(target.parent))
            hasher = hashlib.sha256()
            done = 0
            try:
                with os.fdopen(fd, "wb") as out:
                    resp = self._opener(item.url)
                    try:
                        total = item.size or int(getattr(resp, "headers", {}).get("Content-Length", 0) or 0)
                        while True:
                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            done += len(chunk)
                            if done > self.max_item_bytes:
                                raise ValueError(f"Item {item.id} vượt giới hạn {self.max_item_bytes} bytes")
                            hasher.update(chunk)
                            out.write(chunk)
                            if progress:
                                progress(done, total)
                    finally:
                        close = getattr(resp, "close", None)
                        if close:
                            close()
                if item.sha256 and hasher.hexdigest() != item.sha256:
                    raise ValueError(f"Sai SHA-256 cho item {item.id}")
                if item.size and done != item.size:
                    raise ValueError(f"Sai dung lượng item {item.id}: {done} != {item.size}")
                os.replace(tmp, target)
                return target
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def fetch_many(self, ids: Iterable[str]) -> Dict[str, Path]:
        return {i: self.fetch(i) for i in ids}

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
