"""
src/ui/lux/library/transition_preview_generator.py
Pure PySide6 helper module for rendering DaVinci Resolve-faithful Clip A -> Clip B
transition previews, procedural thumbnails, and resolving .drfx / companion .mp4 assets.
"""
from __future__ import annotations

import hashlib
import math
import os
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import Qt, QPointF, QRect, QRectF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")

_THUMB_PIXMAP_CACHE: Dict[str, QPixmap] = {}
_GENERATED_THUMB_CACHE: Dict[str, QPixmap] = {}
_PACK_VIDEO_INDEX_CACHE: Dict[str, Tuple[Dict[str, str], str]] = {}
_CLOUD_PACKS_VIDEO_CACHE: Dict[Tuple[str, int], Tuple[Dict[str, str], Dict[str, str]]] = {}


def _get_attr(obj: Any, *keys: str, default: Any = "") -> Any:
    """Safely extracts a value from a Mapping, domain object, or raw object."""
    if obj is None:
        return default
    if isinstance(obj, str):
        if "name" in keys:
            return obj
        return default
    if isinstance(obj, Mapping):
        for k in keys:
            val = obj.get(k)
            if val is not None and val != "":
                return val
        raw = obj.get("_raw_obj")
        if raw is not None and raw is not obj:
            return _get_attr(raw, *keys, default=default)
        return default
    for k in keys:
        val = getattr(obj, k, None)
        if val is not None and val != "":
            return val
    raw = getattr(obj, "_raw_obj", None)
    if raw is not None and raw is not obj:
        return _get_attr(raw, *keys, default=default)
    return default


def _resolve_master_disk_path(asset: Any) -> str:
    """Resolves the on-disk master file_path or library_path for an asset."""
    fp = str(_get_attr(asset, "file_path", "path", "external_path", default="") or "").strip()
    if fp and os.path.isfile(fp):
        return os.path.abspath(fp)

    lib_path = str(_get_attr(asset, "library_path", "relative_path", default="") or "").strip()
    if lib_path:
        origin_root = str(_get_attr(asset, "origin_root", default="") or "").strip()
        if origin_root:
            cand = os.path.join(origin_root, lib_path)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
        try:
            from src.core.storage_manager import StorageManager
            sm = StorageManager()
            cand = os.path.join(str(sm.root), lib_path)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
        except Exception:
            pass
    return fp if (fp and os.path.isfile(fp)) else ""


def _find_pack_root(file_path: str) -> Optional[Path]:
    """Finds the enclosing pack directory under 02_cloud_downloads/packs or containing pack_manifest.json."""
    if not file_path:
        return None
    try:
        p = Path(file_path).resolve()
        curr = p.parent if p.is_file() else p
        for ancestor in [curr, *curr.parents]:
            if (ancestor / "pack_manifest.json").is_file():
                return ancestor
            if (ancestor / "Preview Transitions").is_dir() or (ancestor / "Previews").is_dir():
                return ancestor
            if ancestor.parent and ancestor.parent.name == "packs" and ancestor.parent.parent and ancestor.parent.parent.name == "02_cloud_downloads":
                return ancestor
    except Exception:
        pass
    return None


def resolve_drfx_thumbnail(asset: Any) -> str:
    """
    Resolves a representative PNG/JPG thumbnail for a transition or template asset:
    1. Checks existing thumbnail_path on disk.
    2. Checks sibling .png/.jpg next to a .setting file or thumbnail_relative_path in pack.
    3. Extracts representative .png/.jpg from inside a .drfx zip archive into a temp cache.
    """
    if asset is None or isinstance(asset, str):
        return ""

    thumb_p = str(_get_attr(asset, "thumbnail_path", "thumb_path", default="") or "").strip()
    if thumb_p and thumb_p.lower().endswith(IMAGE_EXTS) and os.path.isfile(thumb_p):
        return os.path.abspath(thumb_p)

    master_p = _resolve_master_disk_path(asset)
    if master_p:
        master_path = Path(master_p)
        pack_root = _find_pack_root(master_p)
        thumb_rel = str(_get_attr(asset, "thumbnail_relative_path", default="") or "").strip()
        if thumb_rel and pack_root is not None:
            cand_rel = pack_root / thumb_rel
            if cand_rel.is_file() and cand_rel.suffix.lower() in IMAGE_EXTS:
                return str(cand_rel.resolve())

        # Check sibling image next to .setting or .drfx file
        if master_path.suffix.lower() in (".setting", ".drfx", ".comp"):
            for ext in IMAGE_EXTS:
                sibling = master_path.with_suffix(ext)
                if sibling.is_file():
                    return str(sibling.resolve())

        # If .drfx zip archive, look inside for a matching .png or .jpg
        if master_path.suffix.lower() == ".drfx" and master_path.is_file() and zipfile.is_zipfile(master_path):
            asset_name = str(_get_attr(asset, "name", default=master_path.stem) or master_path.stem).strip()
            try:
                mtime = int(os.path.getmtime(master_path))
            except OSError:
                mtime = 0
            key_raw = f"{master_path.resolve()}:{mtime}:{asset_name.lower()}"
            digest = hashlib.sha1(key_raw.encode("utf-8", errors="ignore")).hexdigest()[:20]
            cache_dir = os.path.join(tempfile.gettempdir(), "chundvc_drfx_thumbs")
            cache_file = os.path.join(cache_dir, f"{digest}.png")
            if os.path.isfile(cache_file) and os.path.getsize(cache_file) > 0:
                return cache_file

            try:
                with zipfile.ZipFile(master_path, "r") as zf:
                    img_entries = []
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        norm_name = info.filename.replace("\\", "/")
                        parts = [pt for pt in norm_name.split("/") if pt]
                        if not parts:
                            continue
                        if "__MACOSX" in parts:
                            continue
                        fname = parts[-1]
                        if fname.startswith("._") or fname.startswith("."):
                            continue
                        if fname.lower().endswith(IMAGE_EXTS):
                            # Skip macro frame sequences like LLmotion_C_000.png or LLmotion_Rect_001.png
                            stem_l = os.path.splitext(fname)[0].lower()
                            if len(stem_l) > 4 and stem_l[-4] == "_" and stem_l[-3:].isdigit():
                                continue
                            if stem_l in ("paper", "texture", "bg", "mask"):
                                continue
                            img_entries.append((norm_name, stem_l))

                    if img_entries:
                        target_entry = img_entries[0][0]
                        target_stem = asset_name.lower().strip()
                        for entry_name, stem_l in img_entries:
                            if stem_l == target_stem or target_stem in stem_l or stem_l in target_stem:
                                target_entry = entry_name
                                break

                        os.makedirs(cache_dir, exist_ok=True)
                        data = zf.read(target_entry)
                        if data:
                            with open(cache_file, "wb") as f:
                                f.write(data)
                            return cache_file
            except Exception:
                pass

    return ""


def _index_pack_videos(pack_root: Path) -> Tuple[Dict[str, str], str]:
    """Builds and caches a lookup table of {lowercase_stem: abs_video_path} and fallback tutorial/preview video."""
    key = str(pack_root)
    if key in _PACK_VIDEO_INDEX_CACHE:
        return _PACK_VIDEO_INDEX_CACHE[key]

    by_stem: Dict[str, str] = {}
    fallback_video = ""
    fallback_priority = 999

    try:
        for f in sorted(pack_root.rglob("*")):
            if not f.is_file():
                continue
            rel_parts = f.relative_to(pack_root).parts
            if "__MACOSX" in rel_parts or f.name.startswith("._") or f.name.startswith("."):
                continue
            if f.suffix.lower() in VIDEO_EXTS:
                abs_p = str(f.resolve())
                stem_l = f.stem.lower().strip()
                by_stem.setdefault(stem_l, abs_p)
                rel_lower = f.relative_to(pack_root).as_posix().lower()
                if any(k in stem_l for k in ("tutorial", "how-to-use", "how_to_use", "preview", "demo")):
                    prio = 1 if "preview" in rel_lower else 2
                    if prio < fallback_priority:
                        fallback_priority = prio
                        fallback_video = abs_p
                elif "preview transitions" in rel_lower or "previews" in rel_lower:
                    if fallback_priority > 3:
                        fallback_priority = 3
                        fallback_video = abs_p
    except Exception:
        pass

    _PACK_VIDEO_INDEX_CACHE[key] = (by_stem, fallback_video)
    return by_stem, fallback_video


def _norm_stem(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _index_all_cloud_pack_videos() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Indexes companion .mp4/.mov/.webm preview videos across active StorageManager
    02_cloud_downloads/packs/ so extracted .setting files in assets/templates/transitions/installed/
    cleanly link back to their real Preview Transitions/*.mp4 videos.
    """
    try:
        from src.core.storage_manager import StorageManager
        sm = StorageManager()
        if not sm.online or not sm.root:
            return {}, {}
        packs_dir = Path(str(sm.root)) / "02_cloud_downloads" / "packs"
        if not packs_dir.is_dir():
            return {}, {}
        try:
            st = packs_dir.stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError:
            mtime_ns = 0
        cache_key = (str(packs_dir.resolve()), mtime_ns)
        if cache_key in _CLOUD_PACKS_VIDEO_CACHE:
            return _CLOUD_PACKS_VIDEO_CACHE[cache_key]

        exact_map: Dict[str, str] = {}
        norm_map: Dict[str, str] = {}
        for pack_sub in sorted(packs_dir.iterdir()):
            if not pack_sub.is_dir() or pack_sub.name.startswith("."):
                continue
            by_stem, _ = _index_pack_videos(pack_sub)
            for stem_l, vid_path in by_stem.items():
                if stem_l in ("tutorial", "how-to-use", "how_to_use", "demo", "preview"):
                    continue
                exact_map.setdefault(stem_l, vid_path)
                n_stem = _norm_stem(stem_l)
                if n_stem:
                    norm_map.setdefault(n_stem, vid_path)

        if len(_CLOUD_PACKS_VIDEO_CACHE) > 8:
            _CLOUD_PACKS_VIDEO_CACHE.clear()
        _CLOUD_PACKS_VIDEO_CACHE[cache_key] = (exact_map, norm_map)
        return exact_map, norm_map
    except Exception:
        return {}, {}


def resolve_transition_preview_video(asset: Any) -> str:
    """
    Resolves a playable companion .mp4/.mov/.webm video for a transition asset:
    1. Direct preview_path or file_path if it is a playable video file on disk.
    2. Matching <stem>.mp4 inside the pack's Preview Transitions/ folder or companion tutorial .mp4.
    3. Cross-reference against active StorageManager cloud packs for extracted .drfx/.setting items.
    """
    if asset is None or isinstance(asset, str):
        return ""

    for field in ("preview_path", "file_path", "path"):
        val = str(_get_attr(asset, field, default="") or "").strip()
        if val and val.lower().endswith(VIDEO_EXTS) and os.path.isfile(val):
            return os.path.abspath(val)

    master_p = _resolve_master_disk_path(asset)
    master_stem = Path(master_p).stem.lower().strip() if master_p else ""
    asset_name = str(_get_attr(asset, "name", default=master_stem) or master_stem).lower().strip()

    if master_p:
        pack_root = _find_pack_root(master_p)
        if pack_root is not None:
            prev_rel = str(_get_attr(asset, "preview_relative_path", default="") or "").strip()
            if prev_rel:
                cand = pack_root / prev_rel
                if cand.is_file() and cand.suffix.lower() in VIDEO_EXTS:
                    return str(cand.resolve())

            by_stem, fallback_video = _index_pack_videos(pack_root)
            for candidate_key in (master_stem, asset_name):
                if candidate_key and candidate_key in by_stem and os.path.isfile(by_stem[candidate_key]):
                    return by_stem[candidate_key]

            norm_targets = {_norm_stem(master_stem), _norm_stem(asset_name)} - {""}
            for stem_k, vid_path in by_stem.items():
                if _norm_stem(stem_k) in norm_targets and os.path.isfile(vid_path):
                    return vid_path

            # Use pack-level tutorial/demo video when asset is a .drfx bundle or small pack without per-item stems
            if fallback_video and os.path.isfile(fallback_video):
                if master_p.lower().endswith(".drfx") or len(by_stem) <= 3:
                    return fallback_video

        else:
            # Check sibling .mp4 next to master_p
            master_path = Path(master_p)
            for ext in VIDEO_EXTS:
                sib = master_path.with_suffix(ext)
                if sib.is_file():
                    return str(sib.resolve())

    # Cross-reference extracted .setting transitions against active cloud packs (e.g. GR-44 1,005 .mp4s)
    exact_cloud, norm_cloud = _index_all_cloud_pack_videos()
    if exact_cloud:
        for candidate_key in (master_stem, asset_name):
            if candidate_key and candidate_key in exact_cloud and os.path.isfile(exact_cloud[candidate_key]):
                return exact_cloud[candidate_key]
        for candidate_key in (master_stem, asset_name):
            nk = _norm_stem(candidate_key)
            if nk and nk in norm_cloud and os.path.isfile(norm_cloud[nk]):
                return norm_cloud[nk]

    return ""


def build_verified_video_preview_payload(base_asset: Any, video_path: str) -> Dict[str, Any]:
    """
    Constructs an ActionGuard-compliant preview payload for a resolved companion video
    without mutating insert/drag metadata on the original asset and without weakening ActionGuard:
    - Preserves original provenance/offline/staging fields if base_asset is already storage-attributed.
    - Attributes local extracted templates to the active online StorageManager ONLY when
      `video_path` genuinely resides inside the active StorageManager root.
    """
    if isinstance(base_asset, Mapping):
        payload: Dict[str, Any] = dict(base_asset)
        if "_raw_obj" not in payload:
            payload["_raw_obj"] = base_asset
    else:
        payload = {
            "id": _get_attr(base_asset, "id", "name", default=""),
            "name": _get_attr(base_asset, "name", default=""),
            "category": _get_attr(base_asset, "category", "tab", default=""),
            "tab": _get_attr(base_asset, "tab", "category", default=""),
            "sub": _get_attr(base_asset, "sub", default=""),
            "badge_icon": _get_attr(base_asset, "badge_icon", default="🎬"),
            "color_hex": _get_attr(base_asset, "color_hex", default="#06b6d4"),
            "thumbnail_path": _get_attr(base_asset, "thumbnail_path", default=""),
            "template_type": _get_attr(base_asset, "template_type", default=None),
            "source_kind": _get_attr(base_asset, "source_kind", default=None),
            "origin_library_id": _get_attr(base_asset, "origin_library_id", default=None),
            "origin_root": _get_attr(base_asset, "origin_root", default=None),
            "storage_managed": _get_attr(base_asset, "storage_managed", default=None),
            "library_path": _get_attr(base_asset, "library_path", default=None),
            "relative_path": _get_attr(base_asset, "relative_path", default=None),
            "external_path": _get_attr(base_asset, "external_path", default=None),
            "file_path": _get_attr(base_asset, "file_path", "path", default=""),
            "path": _get_attr(base_asset, "path", "file_path", default=""),
            "availability": _get_attr(base_asset, "availability", default=None),
            "_raw_obj": base_asset,
        }

    payload["preview_path"] = video_path
    if not video_path:
        return payload

    # Never override provenance for assets that already declare managed/foreign/offline/staging state
    has_existing_provenance = bool(
        payload.get("origin_library_id")
        or payload.get("origin_root")
        or payload.get("availability") in ("offline", "staging")
        or str(payload.get("source_kind") or "").lower() == "managed"
    )
    if has_existing_provenance:
        return payload

    try:
        from src.core.storage_manager import StorageManager
        sm = StorageManager()
        cfg = getattr(sm, "_config", None)
        lib_id = cfg.get("library_id") if isinstance(cfg, Mapping) else getattr(sm, "library_id", None)
        if sm.online and sm.root and lib_id:
            real_root = Path(os.path.realpath(str(sm.root)))
            real_vid = Path(os.path.realpath(video_path))
            rel_vid = real_vid.relative_to(real_root).as_posix()
            payload["source_kind"] = "managed"
            payload["storage_managed"] = True
            payload["origin_library_id"] = str(lib_id)
            payload["origin_root"] = str(sm.root)
            payload["library_path"] = rel_vid
            payload["relative_path"] = rel_vid
            payload["file_path"] = str(real_vid)
            payload["path"] = str(real_vid)
            payload["preview_path"] = str(real_vid)
    except Exception:
        pass

    return payload


def infer_transition_kind(asset_or_name: Any, kind_hint: str = "") -> str:
    """
    Classifies a transition into one of 12 DaVinci Resolve motion archetypes:
    speed_ramp, 3d_box, perspective, spin, whip_pan, zoom, glitch,
    displacement, shape, frost, paper, flash.
    """
    parts = []
    if isinstance(asset_or_name, str):
        parts.append(asset_or_name)
    elif asset_or_name is not None:
        for key in ("name", "sub", "subcategory", "category", "transition_type", "id", "relative_path", "file_path"):
            val = _get_attr(asset_or_name, key, default="")
            if val:
                parts.append(str(val))
        tags = _get_attr(asset_or_name, "tags", default=None)
        if isinstance(tags, (list, tuple, set)):
            parts.extend(str(t) for t in tags)
    if kind_hint:
        parts.append(str(kind_hint))

    combined = " ".join(parts).lower()

    # Check specialized motion styles first before directional whip_pan keywords
    rules = [
        ("paper", ("paper", "unfold", "fold", "tear", "cutout", "llmotion")),
        ("3d_box", ("3d", "box", "cube", "flip")),
        ("perspective", ("persp", "perspective", "disc", "sigm", "swing")),
        ("spin", ("spin", "rotate", "twirl", "cw", "ccw", "roll")),
        ("speed_ramp", ("speed", "ramp", "pixfix", "velocity")),
        ("glitch", ("glitch", "rgb", "vhs", "tv", "satellite", "aberration", "digital")),
        ("displacement", ("displace", "turbulent", "stretch", "elastic", "warp", "liquid", "ripple")),
        ("shape", ("shape", "circle", "iris", "wipe", "split", "rect", "diagonal", "shutter")),
        ("frost", ("frost", "ice", "particles", "dust", "texture", "blur")),
        ("flash", ("flash", "glow", "leak", "light", "luma", "fade", "dissolve", "burn", "strobe")),
        ("zoom", ("zoom", "punch", "impact", "scale", "lens", "camera", "fisheye")),
        ("whip_pan", ("whip", "pan", "slide", "push", "parallax", "left", "right", "up", "down")),
    ]

    for kind_id, keywords in rules:
        if any(kw in combined for kw in keywords):
            return kind_id

    return "whip_pan"


def _load_cached_thumb_pixmap(thumb_path: str) -> Optional[QPixmap]:
    if not thumb_path or not os.path.isfile(thumb_path):
        return None
    if thumb_path in _THUMB_PIXMAP_CACHE:
        pix = _THUMB_PIXMAP_CACHE[thumb_path]
        return pix if not pix.isNull() else None
    pix = QPixmap(thumb_path)
    if not pix.isNull():
        if len(_THUMB_PIXMAP_CACHE) > 256:
            _THUMB_PIXMAP_CACHE.clear()
        _THUMB_PIXMAP_CACHE[thumb_path] = pix
        return pix
    return None


def _draw_scene_a(painter: QPainter, rect_f: QRectF) -> None:
    """Draws simulated Shot A: warm golden/amber cinematic sunset horizon + city skyline silhouette."""
    painter.save()
    painter.setClipRect(rect_f)

    x = rect_f.x()
    y = rect_f.y()
    w = rect_f.width()
    h = rect_f.height()

    # Warm sunset sky gradient
    sky_grad = QLinearGradient(QPointF(x, y), QPointF(x, y + h))
    sky_grad.setColorAt(0.0, QColor("#1c0d06"))
    sky_grad.setColorAt(0.45, QColor("#7c2d12"))
    sky_grad.setColorAt(0.78, QColor("#d97706"))
    sky_grad.setColorAt(1.0, QColor("#fbbf24"))
    painter.fillRect(rect_f, sky_grad)

    # Glowing sun disc
    sun_r = min(w, h) * 0.22
    sun_center = QPointF(x + w * 0.35, y + h * 0.52)
    sun_grad = QRadialGradient(sun_center, sun_r * 1.6)
    sun_grad.setColorAt(0.0, QColor(254, 240, 138, 230))
    sun_grad.setColorAt(0.5, QColor(245, 158, 11, 140))
    sun_grad.setColorAt(1.0, QColor(217, 119, 6, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(sun_grad))
    painter.drawEllipse(sun_center, sun_r * 1.5, sun_r * 1.5)

    # City skyline silhouette
    horizon_y = y + h * 0.68
    painter.setBrush(QColor("#140904"))
    buildings = [
        (0.00, 0.12, 0.22),
        (0.11, 0.10, 0.34),
        (0.20, 0.14, 0.28),
        (0.33, 0.09, 0.18),
        (0.41, 0.13, 0.38),
        (0.53, 0.11, 0.25),
        (0.63, 0.15, 0.31),
        (0.77, 0.10, 0.20),
        (0.86, 0.15, 0.29),
    ]
    for bx_frac, bw_frac, bh_frac in buildings:
        bh = h * bh_frac
        painter.drawRect(QRectF(x + w * bx_frac, y + h - bh, w * bw_frac + 1, bh))
    painter.drawRect(QRectF(x, horizon_y + h * 0.12, w, h * 0.22))

    # Subtle rule-of-thirds camera grid
    grid_pen = QPen(QColor(255, 255, 255, 22), 1.0, Qt.PenStyle.DotLine)
    painter.setPen(grid_pen)
    painter.drawLine(QPointF(x + w / 3.0, y), QPointF(x + w / 3.0, y + h))
    painter.drawLine(QPointF(x + 2.0 * w / 3.0, y), QPointF(x + 2.0 * w / 3.0, y + h))
    painter.drawLine(QPointF(x, y + h / 3.0), QPointF(x + w, y + h / 3.0))

    # CLIP A badge
    font = QFont("Segoe UI", max(7, int(h * 0.065)))
    font.setBold(True)
    painter.setFont(font)
    badge_rect = QRectF(x + w * 0.06, y + h * 0.38, max(44.0, w * 0.22), max(16.0, h * 0.13))
    painter.setPen(QPen(QColor(251, 191, 36, 160), 1.0))
    painter.setBrush(QColor(24, 13, 6, 175))
    painter.drawRoundedRect(badge_rect, 4.0, 4.0)
    painter.setPen(QColor("#fde68a"))
    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "CLIP A")

    painter.restore()


def _draw_scene_b(painter: QPainter, rect_f: QRectF, thumb_pix: Optional[QPixmap] = None) -> None:
    """Draws simulated Shot B: cool cyan/teal cyberpunk night mountains + neon horizon + optional pack thumb."""
    painter.save()
    painter.setClipRect(rect_f)

    x = rect_f.x()
    y = rect_f.y()
    w = rect_f.width()
    h = rect_f.height()

    # Cool cyberpunk night gradient
    sky_grad = QLinearGradient(QPointF(x, y), QPointF(x, y + h))
    sky_grad.setColorAt(0.0, QColor("#040b16"))
    sky_grad.setColorAt(0.45, QColor("#083344"))
    sky_grad.setColorAt(0.80, QColor("#0891b2"))
    sky_grad.setColorAt(1.0, QColor("#06b6d4"))
    painter.fillRect(rect_f, sky_grad)

    # Mountain peaks silhouette
    mtn_path = QPainterPath()
    mtn_path.moveTo(x, y + h)
    mtn_path.lineTo(x, y + h * 0.65)
    mtn_path.lineTo(x + w * 0.22, y + h * 0.42)
    mtn_path.lineTo(x + w * 0.42, y + h * 0.60)
    mtn_path.lineTo(x + w * 0.68, y + h * 0.34)
    mtn_path.lineTo(x + w * 0.88, y + h * 0.56)
    mtn_path.lineTo(x + w, y + h * 0.46)
    mtn_path.lineTo(x + w, y + h)
    mtn_path.closeSubpath()
    painter.setPen(QPen(QColor("#22d3ee"), 1.2))
    painter.setBrush(QColor("#061523"))
    painter.drawPath(mtn_path)

    # If a native pack thumbnail exists, blend it subtly into Scene B so user sees pack art + motion
    if thumb_pix is not None and not thumb_pix.isNull():
        painter.save()
        painter.setOpacity(0.48)
        scaled = thumb_pix.scaled(
            int(max(1, w)),
            int(max(1, h)),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = int((scaled.width() - w) / 2) if scaled.width() > w else 0
        sy = int((scaled.height() - h) / 2) if scaled.height() > h else 0
        painter.drawPixmap(
            QRect(int(x), int(y), int(w), int(h)),
            scaled,
            QRect(sx, sy, int(min(w, scaled.width())), int(min(h, scaled.height()))),
        )
        painter.restore()

    # CLIP B badge
    font = QFont("Segoe UI", max(7, int(h * 0.065)))
    font.setBold(True)
    painter.setFont(font)
    bw = max(44.0, w * 0.22)
    bh = max(16.0, h * 0.13)
    badge_rect = QRectF(x + w - bw - w * 0.06, y + h * 0.38, bw, bh)
    painter.setPen(QPen(QColor(34, 211, 238, 180), 1.0))
    painter.setBrush(QColor(4, 15, 28, 180))
    painter.drawRoundedRect(badge_rect, 4.0, 4.0)
    painter.setPen(QColor("#67e8f9"))
    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "CLIP B")

    painter.restore()


def draw_transition_frame(
    painter: QPainter,
    rect: Any,
    asset_or_name: Any,
    progress: float = 0.5,
    frames: int = 24,
    kind: str = "",
    thumb_path: str = "",
    show_hud: bool = True,
) -> None:
    """
    Draws a rich DaVinci Resolve-style Clip A -> Clip B transition simulation inside rect
    at normalized progress (0.0 to 1.0).
    """
    rect_f = QRectF(rect)
    if rect_f.width() <= 2 or rect_f.height() <= 2:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    clip_path = QPainterPath()
    clip_path.addRoundedRect(rect_f, 6.0, 6.0)
    painter.setClipPath(clip_path)

    x = rect_f.x()
    y = rect_f.y()
    rw = rect_f.width()
    rh = rect_f.height()
    cx = x + rw * 0.5
    cy = y + rh * 0.5

    p = max(0.0, min(1.0, float(progress)))
    ease = 3.0 * p * p - 2.0 * p * p * p
    sin_p = math.sin(math.pi * p)

    resolved_kind = infer_transition_kind(asset_or_name, kind)
    eff_thumb = thumb_path or resolve_drfx_thumbnail(asset_or_name)
    thumb_pix = _load_cached_thumb_pixmap(eff_thumb) if eff_thumb else None

    # Extract display name & duration frames if available on asset
    disp_name = str(_get_attr(asset_or_name, "name", default="Transition") or "Transition").strip()
    asset_frames = _get_attr(asset_or_name, "duration_frames", "frames", default=frames)
    try:
        frames = int(asset_frames) if asset_frames else int(frames)
    except (ValueError, TypeError):
        frames = 24

    # ------------------------------------------------------------------
    # Render active transition archetype between Scene A and Scene B
    # ------------------------------------------------------------------
    if resolved_kind == "whip_pan":
        split_x = x + rw * (1.0 - ease)
        rect_a = QRectF(x - rw * ease, y, rw, rh)
        rect_b = QRectF(split_x, y, rw, rh)
        _draw_scene_a(painter, rect_a)
        _draw_scene_b(painter, rect_b, thumb_pix)

        # Horizontal motion-blur streaks
        if sin_p > 0.05:
            streak_pen = QPen(QColor(255, 255, 255, int(110 * sin_p)), max(1.5, rh * 0.02))
            painter.setPen(streak_pen)
            for i in range(6):
                sy = y + rh * (0.18 + i * 0.12)
                slen = rw * (0.18 + (i % 3) * 0.08) * sin_p
                painter.drawLine(QPointF(split_x - slen * 0.5, sy), QPointF(split_x + slen * 0.5, sy))

        # Glowing seam line
        seam_pen = QPen(QColor("#06b6d4"), max(2.0, rw * 0.01))
        painter.setPen(seam_pen)
        painter.drawLine(QPointF(split_x, y), QPointF(split_x, y + rh))

    elif resolved_kind == "speed_ramp":
        painter.save()
        painter.translate(cx, cy)
        if p < 0.5:
            scale = 1.0 + 1.35 * (p * 2.0) ** 2
            painter.scale(scale, scale)
            _draw_scene_a(painter, QRectF(-rw * 0.5, -rh * 0.5, rw, rh))
        else:
            t_out = (1.0 - p) * 2.0
            scale = 1.0 + 1.35 * (t_out ** 2)
            painter.scale(scale, scale)
            _draw_scene_b(painter, QRectF(-rw * 0.5, -rh * 0.5, rw, rh), thumb_pix)
        painter.restore()

        # Radial speed lines + chromatic velocity flash near p=0.5
        if sin_p > 0.2:
            painter.save()
            alpha = int(165 * (sin_p ** 1.5))
            painter.setPen(QPen(QColor(56, 189, 248, alpha), 1.5))
            for deg in range(0, 360, 24):
                rad = math.radians(deg)
                r_in = min(rw, rh) * 0.22
                r_out = max(rw, rh) * 0.62
                painter.drawLine(
                    QPointF(cx + math.cos(rad) * r_in, cy + math.sin(rad) * r_in),
                    QPointF(cx + math.cos(rad) * r_out, cy + math.sin(rad) * r_out),
                )
            if abs(p - 0.5) < 0.16:
                flash_alpha = int(170 * (1.0 - abs(p - 0.5) / 0.16))
                painter.fillRect(rect_f, QColor(224, 242, 254, flash_alpha))
            painter.restore()

    elif resolved_kind in ("3d_box", "perspective"):
        # Dark 3D studio stage backdrop
        painter.fillRect(rect_f, QColor("#07090e"))
        hinge_x = x + rw * (1.0 - ease)
        inset_y = rh * 0.12 * sin_p

        # Left folding face (Scene A)
        if hinge_x > x + 1:
            face_a = QRectF(x, y + inset_y * 0.5, hinge_x - x, rh - inset_y)
            _draw_scene_a(painter, face_a)
            painter.fillRect(face_a, QColor(0, 0, 0, int(130 * ease)))
            painter.setPen(QPen(QColor("#d94f3d"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(face_a)

        # Right unfolding face (Scene B)
        if hinge_x < x + rw - 1:
            face_b = QRectF(hinge_x, y + inset_y * 0.5, (x + rw) - hinge_x, rh - inset_y)
            _draw_scene_b(painter, face_b, thumb_pix)
            painter.fillRect(face_b, QColor(0, 0, 0, int(120 * (1.0 - ease))))
            painter.setPen(QPen(QColor("#06b6d4"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(face_b)

        # 3D Hinge highlight
        painter.setPen(QPen(QColor(255, 255, 255, 200), 2.0))
        painter.drawLine(QPointF(hinge_x, y + inset_y * 0.5), QPointF(hinge_x, y + rh - inset_y * 0.5))

    elif resolved_kind == "spin":
        painter.fillRect(rect_f, QColor("#09090b"))
        painter.save()
        painter.translate(cx, cy)
        angle = ease * 180.0 if p < 0.5 else (ease - 1.0) * 180.0
        scale = 1.0 + 0.55 * sin_p
        painter.rotate(angle)
        painter.scale(scale, scale)
        local_r = QRectF(-rw * 0.5, -rh * 0.5, rw, rh)
        if p < 0.5:
            _draw_scene_a(painter, local_r)
        else:
            _draw_scene_b(painter, local_r, thumb_pix)
        painter.restore()

        # Spin arc ring overlay
        if sin_p > 0.15:
            arc_r = min(rw, rh) * 0.34
            arc_pen = QPen(QColor(6, 182, 212, int(170 * sin_p)), 2.2, Qt.PenStyle.DashLine)
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), arc_r, arc_r)

    elif resolved_kind == "zoom":
        painter.save()
        painter.translate(cx, cy)
        scale = 1.0 + 1.8 * sin_p
        painter.scale(scale, scale)
        local_r = QRectF(-rw * 0.5, -rh * 0.5, rw, rh)
        if p < 0.5:
            _draw_scene_a(painter, local_r)
        else:
            _draw_scene_b(painter, local_r, thumb_pix)
        painter.restore()

        # Impact glow bloom around p=0.5
        if sin_p > 0.25:
            bloom = QRadialGradient(QPointF(cx, cy), max(rw, rh) * 0.55)
            bloom.setColorAt(0.0, QColor(255, 255, 255, int(150 * (sin_p ** 2))))
            bloom.setColorAt(0.5, QColor(6, 182, 212, int(90 * sin_p)))
            bloom.setColorAt(1.0, QColor(6, 182, 212, 0))
            painter.fillRect(rect_f, bloom)

    elif resolved_kind == "glitch":
        if p < 0.5:
            _draw_scene_a(painter, rect_f)
        else:
            _draw_scene_b(painter, rect_f, thumb_pix)

        # Horizontal scanline slice displacement + RGB split bars
        slices = 7
        slice_h = rh / slices
        for i in range(slices):
            sy = y + i * slice_h
            shift = math.sin(i * 2.1 + p * 12.0) * rw * 0.16 * sin_p
            s_rect = QRectF(x + shift, sy, rw, slice_h + 1.0)
            if (i + int(p * 10)) % 2 == 0:
                _draw_scene_b(painter, s_rect, thumb_pix)
            else:
                _draw_scene_a(painter, s_rect)
            # Chromatic RGB bars
            painter.fillRect(QRectF(x + shift - 6 * sin_p, sy, rw, slice_h * 0.35), QColor(255, 0, 85, int(95 * sin_p)))
            painter.fillRect(QRectF(x + shift + 6 * sin_p, sy + slice_h * 0.5, rw, slice_h * 0.35), QColor(0, 229, 255, int(95 * sin_p)))

    elif resolved_kind == "displacement":
        _draw_scene_a(painter, rect_f)
        strips = 9
        strip_h = rh / strips
        for i in range(strips):
            sy = y + i * strip_h
            wave = math.sin(i * 1.1 + p * math.tau) * rw * 0.18 * sin_p
            reveal_w = rw * ease + wave
            if reveal_w > 0:
                s_rect = QRectF(x + rw - max(0.0, min(rw, reveal_w)), sy, max(0.0, min(rw, reveal_w)), strip_h + 1.0)
                _draw_scene_b(painter, s_rect, thumb_pix)
                painter.setPen(QPen(QColor(6, 182, 212, int(160 * sin_p)), 1.2))
                painter.drawLine(QPointF(s_rect.left(), sy), QPointF(s_rect.left(), sy + strip_h))

    elif resolved_kind == "shape":
        _draw_scene_a(painter, rect_f)
        max_r = math.hypot(rw, rh) * 0.62
        cur_r = max(2.0, max_r * ease)

        painter.save()
        iris_path = QPainterPath()
        iris_path.addEllipse(QPointF(cx, cy), cur_r, cur_r)
        painter.setClipPath(clip_path.intersected(iris_path))
        _draw_scene_b(painter, rect_f, thumb_pix)
        painter.restore()

        # Crisp coral/cyan geometric border
        painter.setPen(QPen(QColor("#06b6d4"), 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), cur_r, cur_r)
        if cur_r > 6:
            painter.setPen(QPen(QColor("#d94f3d"), 1.5))
            painter.drawEllipse(QPointF(cx, cy), cur_r - 4.0, cur_r - 4.0)

    elif resolved_kind == "frost":
        _draw_scene_a(painter, rect_f)
        wipe_x = x + rw * (1.0 - ease)
        _draw_scene_b(painter, QRectF(wipe_x, y, rw * ease, rh), thumb_pix)

        # Frosted crystalline particle band along wipe edge
        band_w = rw * 0.18 * max(0.25, sin_p)
        frost_grad = QLinearGradient(QPointF(wipe_x - band_w, y), QPointF(wipe_x + band_w, y))
        frost_grad.setColorAt(0.0, QColor(186, 230, 253, 0))
        frost_grad.setColorAt(0.5, QColor(224, 242, 254, 195))
        frost_grad.setColorAt(1.0, QColor(56, 189, 248, 0))
        painter.fillRect(QRectF(wipe_x - band_w, y, band_w * 2.0, rh), frost_grad)

        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(14):
            py = y + ((i * 37) % 100) * 0.01 * rh
            px = wipe_x + math.sin(i * 1.7 + p * 9.0) * band_w * 0.85
            pr = 1.5 + (i % 3) * 1.2
            painter.setBrush(QColor(240, 249, 255, 210))
            painter.drawEllipse(QPointF(px, py), pr, pr)

    elif resolved_kind == "paper":
        # Stop-motion stepped unfold progression
        step_p = round(p * 6.0) / 6.0 if 0.08 < p < 0.92 else p
        fold_x = x + rw * (1.0 - step_p)
        _draw_scene_a(painter, rect_f)
        _draw_scene_b(painter, QRectF(fold_x, y, rw * step_p, rh), thumb_pix)

        # Ivory paper fold flap and crease lines
        flap_w = max(8.0, rw * 0.09 * max(0.3, sin_p))
        flap_poly = QPolygonF([
            QPointF(fold_x, y),
            QPointF(fold_x - flap_w, y + rh * 0.04),
            QPointF(fold_x - flap_w * 0.85, y + rh * 0.96),
            QPointF(fold_x, y + rh),
        ])
        painter.setPen(QPen(QColor("#a8a29e"), 1.2))
        painter.setBrush(QColor("#fef3c7"))
        painter.drawPolygon(flap_poly)

        # Crease grid marks on Scene B
        crease_pen = QPen(QColor(254, 243, 199, 75), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(crease_pen)
        painter.drawLine(QPointF(fold_x + rw * 0.25, y), QPointF(fold_x + rw * 0.25, y + rh))
        painter.drawLine(QPointF(fold_x, cy), QPointF(x + rw, cy))

    elif resolved_kind == "flash":
        if p < 0.5:
            _draw_scene_a(painter, rect_f)
        else:
            _draw_scene_b(painter, rect_f, thumb_pix)

        # Warm film-burn / white-gold lens flare peaking at p=0.5
        flare = QRadialGradient(QPointF(cx, cy), max(rw, rh) * 0.65)
        flare.setColorAt(0.0, QColor(255, 255, 255, int(235 * (sin_p ** 1.4))))
        flare.setColorAt(0.4, QColor(251, 191, 36, int(185 * sin_p)))
        flare.setColorAt(0.75, QColor(234, 88, 12, int(110 * sin_p)))
        flare.setColorAt(1.0, QColor(234, 88, 12, 0))
        painter.fillRect(rect_f, flare)

    else:
        _draw_scene_a(painter, rect_f)
        _draw_scene_b(painter, QRectF(cx, y, rw * 0.5, rh), thumb_pix)

    # Corner PIP reference if native pack thumbnail exists
    if thumb_pix is not None and not thumb_pix.isNull() and rw >= 140 and rh >= 80:
        pip_w = max(36.0, min(68.0, rw * 0.24))
        pip_h = round(pip_w * 9.0 / 16.0)
        pip_rect = QRectF(x + rw - pip_w - 6.0, y + rh - pip_h - (26.0 if show_hud else 6.0), pip_w, pip_h)
        painter.save()
        painter.setPen(QPen(QColor(6, 182, 212, 190), 1.0))
        painter.setBrush(QColor(9, 9, 11, 200))
        painter.drawRoundedRect(pip_rect, 3.0, 3.0)
        painter.drawPixmap(pip_rect.adjusted(1, 1, -1, -1).toRect(), thumb_pix)
        painter.restore()

    # ------------------------------------------------------------------
    # HUD: Top badges + Bottom NLE Timeline Mini-Bar
    # ------------------------------------------------------------------
    if show_hud:
        hud_font = QFont("Segoe UI", max(7, int(min(10, rh * 0.062))))
        hud_font.setBold(True)
        painter.setFont(hud_font)
        fm = QFontMetrics(hud_font)

        # Top-left badge: ● LIVE • KIND
        kind_label = f"● {resolved_kind.replace('_', ' ').upper()}"
        kw = fm.horizontalAdvance(kind_label) + 12
        kh = max(15, fm.height() + 4)
        tl_rect = QRectF(x + 6.0, y + 6.0, kw, kh)
        painter.setPen(QPen(QColor(16, 185, 129, 140), 1.0))
        painter.setBrush(QColor(9, 9, 11, 195))
        painter.drawRoundedRect(tl_rect, 4.0, 4.0)
        painter.setPen(QColor("#10b981"))
        painter.drawText(tl_rect, Qt.AlignmentFlag.AlignCenter, kind_label)

        # Top-right badge: {frames}f • 24fps
        fps_label = f"{frames}f • 24fps"
        fw = fm.horizontalAdvance(fps_label) + 12
        tr_rect = QRectF(x + rw - fw - 6.0, y + 6.0, fw, kh)
        painter.setPen(QPen(QColor(6, 182, 212, 130), 1.0))
        painter.setBrush(QColor(9, 9, 11, 195))
        painter.drawRoundedRect(tr_rect, 4.0, 4.0)
        painter.setPen(QColor("#67e8f9"))
        painter.drawText(tr_rect, Qt.AlignmentFlag.AlignCenter, fps_label)

        # Bottom NLE timeline mini-bar
        bar_h = max(18.0, min(24.0, rh * 0.16))
        bar_y = y + rh - bar_h
        bar_rect = QRectF(x, bar_y, rw, bar_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(9, 9, 11, 220))
        painter.drawRect(bar_rect)

        track_pad = 6.0
        track_w = max(20.0, rw - track_pad * 2.0)
        track_h = max(4.0, bar_h * 0.26)
        track_y = bar_y + bar_h - track_h - 3.0

        # Clip A segment (coral #d94f3d)
        painter.setBrush(QColor("#d94f3d"))
        painter.drawRoundedRect(QRectF(x + track_pad, track_y, track_w * 0.45, track_h), 2.0, 2.0)

        # Clip B segment (sky #0284c7)
        painter.setBrush(QColor("#0284c7"))
        painter.drawRoundedRect(QRectF(x + track_pad + track_w * 0.55, track_y, track_w * 0.45, track_h), 2.0, 2.0)

        # Transition overlap zone (cyan #06b6d4)
        painter.setBrush(QColor("#06b6d4"))
        painter.drawRoundedRect(QRectF(x + track_pad + track_w * 0.32, track_y - 1.0, track_w * 0.36, track_h + 2.0), 2.0, 2.0)

        # Transition name label in mini-bar
        elided_name = fm.elidedText(disp_name, Qt.TextElideMode.ElideRight, int(track_w - 8))
        painter.setPen(QColor("#e4e4e7"))
        painter.drawText(
            QRectF(x + track_pad, bar_y + 1.0, track_w, bar_h - track_h - 3.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided_name,
        )

        # Moving playhead line at progress p
        ph_x = x + track_pad + p * track_w
        painter.setPen(QPen(QColor("#facc15"), 1.6))
        painter.drawLine(QPointF(ph_x, bar_y + 2.0), QPointF(ph_x, y + rh - 1.0))

    # Subtle outer frame border
    painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect_f.adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)

    painter.restore()


def generate_transition_thumbnail(asset: Any, width: int = 256, height: int = 144) -> QPixmap:
    """
    Creates a QPixmap(width, height) and renders draw_transition_frame at progress = 0.5
    (show_hud=True) so every transition card in the grid has a vivid, informative thumbnail.
    """
    w = max(16, int(width))
    h = max(16, int(height))
    asset_id = str(_get_attr(asset, "id", "relative_path", "name", default="trans") or "trans")
    cache_key = f"{asset_id}:{w}x{h}"
    if cache_key in _GENERATED_THUMB_CACHE:
        cached = _GENERATED_THUMB_CACHE[cache_key]
        if not cached.isNull():
            return cached

    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    draw_transition_frame(
        painter,
        QRectF(0.0, 0.0, float(w), float(h)),
        asset,
        progress=0.5,
        show_hud=True,
    )
    painter.end()

    pixmap = QPixmap.fromImage(image)
    if len(_GENERATED_THUMB_CACHE) > 512:
        _GENERATED_THUMB_CACHE.clear()
    _GENERATED_THUMB_CACHE[cache_key] = pixmap
    return pixmap
