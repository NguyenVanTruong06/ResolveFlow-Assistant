"""
High-Performance Asset Cache & Thumbnail Generator (Learning from Akitt Pro 4.0).

Features:
1. Fast file hash key (MD5 of normalized path + file size + mtime) without reading whole file.
2. Middle-frame / frame 10 video thumbnail extraction fitted into 16:9 canvas with dark luxury background.
3. Background ThreadPool worker with thread-safe Qt GUI callbacks.
4. Folder metadata scanner with auxiliary asset hiding (.setting child references) and mtime caching.
"""

from __future__ import annotations
import os
import sys
import json
import hashlib
import re
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Set, Tuple
from concurrent.futures import ThreadPoolExecutor

VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
SETTING_EXTS = {'.setting'}

_MEMORY_METADATA_CACHE: Dict[str, Dict[str, Any]] = {}
_MEMORY_THUMB_CACHE: Dict[str, str] = {}
_THREAD_POOL: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    global _THREAD_POOL
    if _THREAD_POOL is None:
        _THREAD_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ChunDVC_Thumb")
    return _THREAD_POOL


def get_cache_root() -> str:
    """Returns the platform-safe root directory for ChunDVC caches."""
    if os.name == 'nt':
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    cache_dir = os.path.join(base, "chundvc")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_thumb_cache_dir() -> str:
    d = os.path.join(get_cache_root(), "thumbnails")
    os.makedirs(d, exist_ok=True)
    return d


def get_metadata_cache_dir() -> str:
    d = os.path.join(get_cache_root(), "metadata")
    os.makedirs(d, exist_ok=True)
    return d


def get_file_hash_key(file_path: str) -> str:
    """Computes a sub-millisecond hash key based on path, size, and mtime."""
    norm_path = os.path.abspath(file_path).lower()
    try:
        stat = os.stat(file_path)
        raw = f"{norm_path}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    except OSError:
        raw = norm_path.encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def get_folder_hash(folder_path: str) -> str:
    norm = os.path.abspath(folder_path).lower().encode("utf-8")
    return hashlib.md5(norm).hexdigest()


def get_folder_mtime(folder_path: str) -> float:
    try:
        max_mtime = os.path.getmtime(folder_path)
        for root, dirs, files in os.walk(folder_path):
            for d in dirs:
                try:
                    max_mtime = max(max_mtime, os.path.getmtime(os.path.join(root, d)))
                except OSError:
                    pass
            for f in files:
                try:
                    max_mtime = max(max_mtime, os.path.getmtime(os.path.join(root, f)))
                except OSError:
                    pass
            break # Fast scan 1st level
        return max_mtime
    except OSError:
        return 0.0


def extract_thumbnail(
    media_path: str,
    target_w: int = 320,
    target_h: int = 180,
    quality: int = 85
) -> Optional[str]:
    """
    Extracts a high-quality 16:9 thumbnail from a video or image.
    Uses frame at ~10% or middle of video.
    Returns the path to the cached JPEG file, or None if extraction fails.
    """
    if not os.path.isfile(media_path):
        return None

    key = get_file_hash_key(media_path)
    cache_file = os.path.join(get_thumb_cache_dir(), f"{key}.jpg")

    # Check memory cache first
    if key in _MEMORY_THUMB_CACHE and os.path.isfile(cache_file):
        return cache_file

    # Check disk cache
    if os.path.isfile(cache_file):
        _MEMORY_THUMB_CACHE[key] = cache_file
        return cache_file

    ext = os.path.splitext(media_path)[1].lower()
    frame_rgb = None

    try:
        if ext in VIDEO_EXTS:
            try:
                import cv2
                cap = cv2.VideoCapture(media_path)
                try:
                    if cap.isOpened():
                        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                        target_frame = min(max(10, total_frames // 2), max(0, total_frames - 1)) if total_frames > 0 else 0
                        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                        ret, bgr = cap.read()
                        if not ret or bgr is None:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            ret, bgr = cap.read()
                        if ret and bgr is not None:
                            frame_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                finally:
                    cap.release()
            except ImportError:
                pass

        elif ext in IMAGE_EXTS:
            from PIL import Image
            im = Image.open(media_path)
            if ext == '.gif':
                im.seek(0)
            frame_rgb = im.convert("RGBA")

        # Fallback to PIL if video failed with cv2
        if frame_rgb is None and ext in VIDEO_EXTS:
            try:
                import av
                container = av.open(media_path)
                for frame in container.decode(video=0):
                    frame_rgb = frame.to_image().convert("RGBA")
                    break
                container.close()
            except Exception:
                pass

        if frame_rgb is None:
            return None

        # Build 16:9 letterbox canvas with luxury dark background
        from PIL import Image
        if not isinstance(frame_rgb, Image.Image):
            src_img = Image.fromarray(frame_rgb).convert("RGBA")
        else:
            src_img = frame_rgb

        canvas = Image.new("RGBA", (target_w, target_h), (14, 17, 22, 255))
        img_w, img_h = src_img.size
        ratio = min(target_w / max(1, img_w), target_h / max(1, img_h))
        new_w = max(1, int(img_w * ratio))
        new_h = max(1, int(img_h * ratio))

        resized = src_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2
        canvas.alpha_composite(resized, (offset_x, offset_y))

        temp_file = cache_file + ".tmp"
        canvas.convert("RGB").save(temp_file, "JPEG", quality=quality)
        os.replace(temp_file, cache_file)

        _MEMORY_THUMB_CACHE[key] = cache_file
        return cache_file

    except Exception:
        return None


def request_thumbnail_async(
    media_path: str,
    callback: Callable[[str], None],
    target_w: int = 320,
    target_h: int = 180
):
    """
    Asynchronously requests a thumbnail.
    If cached, executes callback immediately.
    Otherwise dispatches to worker thread pool and safely delivers result.
    """
    if not media_path:
        return

    key = get_file_hash_key(media_path)
    cache_file = os.path.join(get_thumb_cache_dir(), f"{key}.jpg")

    # Fast path: already cached
    if os.path.isfile(cache_file):
        _MEMORY_THUMB_CACHE[key] = cache_file
        _dispatch_callback(callback, cache_file)
        return

    def _worker():
        thumb = extract_thumbnail(media_path, target_w, target_h)
        if thumb and os.path.isfile(thumb):
            _dispatch_callback(callback, thumb)

    _get_executor().submit(_worker)


def _dispatch_callback(callback: Callable[[str], None], result: str):
    """Dispatches callback onto Qt Main Thread if PySide6 application is running."""
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        app = QApplication.instance()
        if app is not None and getattr(app, "thread", None):
            # Safe single-shot invocation on Qt event loop
            QTimer.singleShot(0, lambda: callback(result))
            return
    except Exception:
        pass
    callback(result)


def scan_and_cache_metadata(folder_path: str) -> List[Dict[str, Any]]:
    """
    Scans a folder for presets and media assets.
    Hides auxiliary media files that are referenced internally by .setting files.
    Caches folder metadata to disk JSON for instant reload.
    """
    if not os.path.isdir(folder_path):
        return []

    folder_path = os.path.abspath(folder_path)
    f_hash = get_folder_hash(folder_path)
    current_mtime = get_folder_mtime(folder_path)

    # Check memory cache
    cached_mem = _MEMORY_METADATA_CACHE.get(folder_path)
    if cached_mem and cached_mem.get("mtime") == current_mtime:
        return cached_mem.get("items", [])

    # Check disk cache
    cache_file = os.path.join(get_metadata_cache_dir(), f"{f_hash}.json")
    if os.path.isfile(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as jf:
                data = json.load(jf)
                if data.get("mtime") == current_mtime:
                    items = data.get("items", [])
                    _MEMORY_METADATA_CACHE[folder_path] = {"mtime": current_mtime, "items": items}
                    return items
        except Exception:
            pass

    # Full directory scan
    hidden_bases: Set[str] = set()
    found_assets: List[Dict[str, Any]] = []

    # First pass: identify setting files and their internal media dependencies
    for root, _, filenames in os.walk(folder_path):
        for f in filenames:
            f_lower = f.lower()
            full_path = os.path.join(root, f)
            if f_lower.endswith(".setting"):
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as sf:
                        setting_text = sf.read(1_000_000)
                        # Find referenced images/media inside Lua setting
                        found_media = re.findall(r'"([^"]+\.(?:png|jpg|jpeg|mp4|mov|gif|webp|wav|mp3|cube))"', setting_text, re.IGNORECASE)
                        for media_ref in found_media:
                            base_name = os.path.splitext(os.path.basename(media_ref))[0].lower()
                            hidden_bases.add(base_name)
                except Exception:
                    pass

    # Second pass: collect user-facing primary assets
    seen_ids: Set[str] = set()
    for root, _, filenames in os.walk(folder_path):
        for f in filenames:
            base_name, ext = os.path.splitext(f)
            ext_lower = ext.lower()

            if base_name.lower() in hidden_bases and ext_lower != ".setting":
                # Auxiliary media file belonging to a setting -> hide from main list
                continue

            if ext_lower in ('.setting', '.mp4', '.mov', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.wav', '.mp3'):
                asset_id = base_name.lower()
                if asset_id in seen_ids and ext_lower != ".setting":
                    continue

                seen_ids.add(asset_id)
                full_path = os.path.join(root, f)
                
                # Check for companion preview/thumbnail
                preview_file = ""
                thumb_file = ""
                for p_ext in ('.mp4', '.mov', '.gif'):
                    cand = os.path.join(root, base_name + p_ext)
                    if os.path.isfile(cand) and cand != full_path:
                        preview_file = cand
                        break

                for t_ext in ('.png', '.jpg', '.jpeg', '.webp'):
                    cand = os.path.join(root, base_name + t_ext)
                    if os.path.isfile(cand) and cand != full_path:
                        thumb_file = cand
                        break

                item_type = "setting" if ext_lower == ".setting" else "video" if ext_lower in VIDEO_EXTS else "image" if ext_lower in IMAGE_EXTS else "audio"

                found_assets.append({
                    "id": asset_id,
                    "name": base_name.replace("_", " ").title(),
                    "file_path": full_path,
                    "ext": ext_lower,
                    "type": item_type,
                    "preview_path": preview_file,
                    "thumbnail_path": thumb_file
                })

    found_assets.sort(key=lambda x: x["name"])

    # Update in-memory & disk cache
    _MEMORY_METADATA_CACHE[folder_path] = {"mtime": current_mtime, "items": found_assets}
    try:
        temp_cache = cache_file + ".tmp"
        with open(temp_cache, "w", encoding="utf-8") as jf:
            json.dump({"folder_path": folder_path, "mtime": current_mtime, "items": found_assets}, jf, ensure_ascii=False, indent=2)
        os.replace(temp_cache, cache_file)
    except Exception:
        pass

    return found_assets


def clear_asset_cache():
    """Clears in-memory caches."""
    _MEMORY_METADATA_CACHE.clear()
    _MEMORY_THUMB_CACHE.clear()
