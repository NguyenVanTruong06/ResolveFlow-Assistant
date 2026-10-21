"""
ResolveFlow <-> DaVinci Resolve bridge.

Two real ways to put an animated title on the timeline (no FCPXML involved):

1. CARRIER (default, recommended)
   - A transparent PNG sequence at timeline resolution is imported once into a
     "ResolveFlow" bin (one file on disk, the rest are hard links).
   - MediaPool.AppendToTimeline() places it with an exact trackIndex (V2),
     recordFrame (= playhead) and length (= duration_frames).
   - TimelineItem.ImportFusionComp() loads a generated .comp whose macro carries
     the user's text/font/size/colour already baked in, then it is made active.
   => exact track, exact position, exact duration, animation intact.

2. TEMPLATE
   - Titles installed in Fusion/Templates/Edit/Titles are inserted with
     Timeline.InsertFusionTitleIntoTimeline(), then the published inputs are set
     through the Fusion comp. Resolve picks the track itself, so we lock every
     other video track during the insert and verify the landing track.
   => editable later in the Inspector exactly like a stock Fusion title; track
      and duration control are best-effort (Resolve API limitation).

Works from Resolve's Console (bmd / resolve already defined) and from an
external Python 3.6+ process (Preferences > General > External scripting = Local).
"""
from __future__ import annotations

import builtins
import ctypes
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.core.fusion import (OVERLAYS, TITLES, TRANSITIONS, TitleParams, build_library, build_title,
                             export_title_comp)
from src.core.fusion.common import parse_color

APP_NAME = "ResolveFlow"
CARRIER_BIN = "ResolveFlow Carriers"


class ResolveFlowError(RuntimeError):
    pass


# =============================================================================
# Connecting to Resolve
# =============================================================================
def setup_resolve_environment() -> Tuple[Optional[str], Optional[str]]:
    """
    Tự động xác định và thiết lập biến môi trường RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB
    đồng thời nạp trước thư viện chia sẻ libfusionscript.so bằng ctypes.RTLD_GLOBAL trên Linux.

    Returns:
        Tuple[api_dir, lib_path]
    """
    api_dir = os.environ.get("RESOLVE_SCRIPT_API")
    lib_path = os.environ.get("RESOLVE_SCRIPT_LIB")

    if sys.platform.startswith("linux"):
        default_api = "/opt/resolve/Developer/Scripting"
        linux_lib_candidates = [
            "/opt/resolve/libs/Fusion/fusionscript.so",
            "/opt/resolve/libs/Fusion/libfusionscript.so",
            "/opt/resolve/libs/fusionscript.so",
            "/opt/resolve/libs/libfusionscript.so",
        ]
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path:
            lib_path = next((c for c in linux_lib_candidates if os.path.isfile(c)), None)
            if not lib_path:
                lib_path = linux_lib_candidates[0]

        # Nạp trước thư viện chia sẻ fusionscript với cờ RTLD_GLOBAL để các symbol C++ được chia sẻ
        if lib_path and os.path.exists(lib_path):
            try:
                ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass

    elif sys.platform.startswith("darwin"):
        default_api = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        default_lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.dylib"
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path and os.path.isfile(default_lib):
            lib_path = default_lib
    else:
        # Windows
        default_api = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        default_lib = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path and os.path.isfile(default_lib):
            lib_path = default_lib

    if api_dir:
        os.environ["RESOLVE_SCRIPT_API"] = api_dir
        modules_dir = os.path.join(api_dir, "Modules")
        if os.path.isdir(modules_dir) and modules_dir not in sys.path:
            sys.path.append(modules_dir)

    if lib_path:
        os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    return api_dir, lib_path


def load_resolve_api() -> Tuple[Optional[Any], str]:
    """
    Nạp đối tượng ứng dụng DaVinci Resolve thông qua FusionScript API.
    Tuyệt đối không import DaVinciResolveScript ở top-level khi chưa thiết lập môi trường.

    Returns:
        Tuple[resolve_app, status_message]
    """
    setup_resolve_environment()

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError as e:
        return None, f"Không thể import module DaVinciResolveScript: {e}"

    try:
        resolve_app = dvr_script.scriptapp("Resolve")
        if resolve_app:
            return resolve_app, "Kết nối DaVinci Resolve thành công."
        return None, (
            "DaVinci Resolve không phản hồi. Hãy mở ứng dụng DaVinci Resolve và kiểm tra "
            "Preferences > General > External scripting using = Local."
        )
    except Exception as e:
        return None, (
            "Resolve từ chối kết nối scripting ngoài. Hãy bật Preferences > General > External scripting using = Local; "
            "lưu ý bản DaVinci Resolve Free thường không hỗ trợ scripting ngoài (cần Studio). "
            "Bạn vẫn có thể import file .fcpxml thủ công bằng File > Import > Timeline."
        )


class ResolveBridge:
    """
    Cầu nối điều khiển DaVinci Resolve API cung cấp các phương thức quản lý kết nối chuẩn hóa.
    """
    def __init__(self):
        from src.core.resolve_api import ResolveAutomation
        self._automation = ResolveAutomation()

    def connect(self) -> bool:
        return self._automation.connect()

    def is_connected(self) -> bool:
        return self._automation.is_connected()

    def ensure_resolve_running(self, log_callback: Optional[Any] = None) -> bool:
        return self._automation.ensure_resolve_running(log_callback=log_callback)

    def __getattr__(self, name):
        return getattr(self._automation, name)


def _default_script_paths() -> List[str]:
    if sys.platform.startswith("win"):
        pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [os.path.join(pd, "Blackmagic Design", "DaVinci Resolve", "Support", "Developer",
                             "Scripting", "Modules")]
    if sys.platform == "darwin":
        return ["/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"]
    return ["/opt/resolve/Developer/Scripting/Modules", "/home/resolve/Developer/Scripting/Modules"]


def _default_script_lib() -> Optional[str]:
    if sys.platform.startswith("win"):
        return r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
    if sys.platform == "darwin":
        return "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    for p in ("/opt/resolve/libs/Fusion/fusionscript.so", "/home/resolve/libs/Fusion/fusionscript.so"):
        if os.path.exists(p):
            return p
    return None


def get_resolve():
    """Return the Resolve app object (Console global, or bmd.scriptapp('Resolve'))."""
    main = sys.modules.get("__main__")
    obj = getattr(builtins, "resolve", None) or getattr(main, "resolve", None)
    if obj is not None:
        return obj
    app, _ = load_resolve_api()
    if app is not None:
        return app
    bmd = getattr(builtins, "bmd", None) or getattr(main, "bmd", None)
    if bmd is not None:
        resolve = bmd.scriptapp("Resolve")
        if resolve is not None:
            return resolve
    raise ResolveFlowError("Resolve is not running, or Preferences > System > General > "
                           "'External scripting using' is set to None.")


@dataclass
class Ctx:
    resolve: Any
    project: Any
    timeline: Any
    media_pool: Any


def context(require_timeline: bool = True) -> Ctx:
    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise ResolveFlowError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if require_timeline and timeline is None:
        raise ResolveFlowError("No timeline is open. Open a timeline on the Edit page first.")
    return Ctx(resolve, project, timeline, project.GetMediaPool())


# =============================================================================
# Template installation
# =============================================================================
def _os_templates_root() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
        return os.path.join(base, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion",
                            "Templates", "Edit")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
                                  "Fusion/Templates/Edit")
    return os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Templates/Edit")


def templates_root(resolve=None) -> str:
    """Ask Fusion for the real 'Templates:' path map; fall back to the per-user OS default."""
    try:
        resolve = resolve or get_resolve()
        mapped = resolve.Fusion().MapPath("Templates:Edit")
        if mapped:
            return os.path.normpath(mapped)
    except Exception:
        pass
    return _os_templates_root()


def install_templates(root: Optional[str] = None) -> Dict[str, List[str]]:
    """
    Write all 8 titles, 6 transitions and 5 overlay generators to
    .../Fusion/Templates/Edit/{Titles,Transitions,Generators}.
    Resolve scans this folder at launch: restart Resolve once after first install.
    """
    root = root or templates_root(None)
    return build_library(root)


# =============================================================================
# Timecode
# =============================================================================
def parse_fps(rate: Any) -> Tuple[float, int]:
    fps = float(str(rate).replace(",", "."))
    nominal = int(round(fps))
    return fps, nominal


def tc_to_frames(tc: str, nominal: int, drop: bool = False) -> int:
    m = re.match(r"^(\d+):(\d+):(\d+)[:;.](\d+)$", tc.strip())
    if not m:
        raise ValueError("Bad timecode %r" % tc)
    h, mi, s, f = (int(x) for x in m.groups())
    frames = ((h * 3600 + mi * 60 + s) * nominal) + f
    if drop or ";" in tc:
        d = 2 * (nominal // 30)
        total_min = 60 * h + mi
        frames -= d * (total_min - total_min // 10)
    return frames


def frames_to_tc(frames: int, nominal: int, drop: bool = False) -> str:
    sep = ":"
    if drop:
        d = 2 * (nominal // 30)
        per10 = nominal * 600 - d * 9
        per1 = nominal * 60 - d
        tens, rem = divmod(frames, per10)
        if rem > d:
            frames += d * 9 * tens + d * ((rem - d) // per1)
        else:
            frames += d * 9 * tens
        sep = ";"
    f = frames % nominal
    s = (frames // nominal) % 60
    m = (frames // (nominal * 60)) % 60
    h = frames // (nominal * 3600)
    return "%02d:%02d:%02d%s%02d" % (h, m, s, sep, f)


def timeline_rate(timeline) -> Tuple[float, int, bool]:
    fps, nominal = parse_fps(timeline.GetSetting("timelineFrameRate") or 24)
    drop = str(timeline.GetSetting("timelineDropFrameTimecode") or "0") in ("1", "true", "True")
    return fps, nominal, drop


def playhead_frame(timeline) -> int:
    _, nominal, drop = timeline_rate(timeline)
    return tc_to_frames(timeline.GetCurrentTimecode(), nominal, drop)


# =============================================================================
# Transparent carrier media
# =============================================================================
def _png_rgba_transparent(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    row = b"\x00" + b"\x00" * (width * 4)
    comp = zlib.compressobj(9)
    body = b"".join(comp.compress(row) for _ in range(height)) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", body) + chunk(b"IEND", b"")


def cache_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def ensure_carrier_sequence(width: int, height: int, frames: int) -> Tuple[str, int]:
    """Create rf_carrier_000000.png ... (hard links to one file). Returns (printf pattern, count)."""
    frames = max(1, int(frames))
    d = os.path.join(cache_dir(), "carrier_%dx%d" % (width, height))
    os.makedirs(d, exist_ok=True)
    first = os.path.join(d, "rf_carrier_000000.png")
    if not os.path.exists(first):
        with open(first, "wb") as fh:
            fh.write(_png_rgba_transparent(width, height))
    for i in range(1, frames):
        p = os.path.join(d, "rf_carrier_%06d.png" % i)
        if os.path.exists(p):
            continue
        try:
            os.link(first, p)
        except OSError:
            shutil.copyfile(first, p)
    return os.path.join(d, "rf_carrier_%06d.png"), frames


def _find_or_create_bin(media_pool, name: str):
    root = media_pool.GetRootFolder()
    for f in root.GetSubFolderList() or []:
        if f.GetName() == name:
            return f
    return media_pool.AddSubFolder(root, name)


def _clip_frames(clip) -> int:
    try:
        return int(str(clip.GetClipProperty("Frames")).strip() or 0)
    except (ValueError, TypeError):
        return 0


def get_carrier_clip(ctx: Ctx, frames_needed: int, width: int, height: int):
    """Re-use a carrier in the ResolveFlow bin if long enough, otherwise import a longer one."""
    bin_ = _find_or_create_bin(ctx.media_pool, CARRIER_BIN)
    tag = "rf_carrier_"
    best = None
    for clip in bin_.GetClipList() or []:
        path = str(clip.GetClipProperty("File Path") or "")
        res = str(clip.GetClipProperty("Resolution") or "")
        if tag in path and res.replace(" ", "") in ("%dx%d" % (width, height), "") \
                and _clip_frames(clip) >= frames_needed:
            if best is None or _clip_frames(clip) < _clip_frames(best):
                best = clip
    if best is not None:
        return best
    length = max(frames_needed, 3600)               # import generously, reuse later
    pattern, count = ensure_carrier_sequence(width, height, length)
    prev = ctx.media_pool.GetCurrentFolder()
    ctx.media_pool.SetCurrentFolder(bin_)
    try:
        items = ctx.media_pool.ImportMedia([{"FilePath": pattern, "StartIndex": 0, "EndIndex": count - 1}])
    finally:
        if prev is not None:
            ctx.media_pool.SetCurrentFolder(prev)
    if not items:
        raise ResolveFlowError("Could not import carrier sequence %s" % pattern)
    return items[0]


# =============================================================================
# Track helpers
# =============================================================================
def _overlaps(timeline, track: int, start: int, end: int) -> bool:
    for it in timeline.GetItemListInTrack("video", track) or []:
        s, e = it.GetStart(), it.GetEnd()
        if s < end and e > start:
            return True
    return False


def resolve_track(timeline, wanted: int, start: int, end: int, on_conflict: str) -> int:
    while timeline.GetTrackCount("video") < wanted:
        if not timeline.AddTrack("video"):
            raise ResolveFlowError("Could not add video track V%d" % wanted)
    if not _overlaps(timeline, wanted, start, end) or on_conflict == "overwrite":
        if timeline.GetIsTrackLocked("video", wanted):
            raise ResolveFlowError("Track V%d is locked." % wanted)
        return wanted
    if on_conflict == "error":
        raise ResolveFlowError("V%d already has a clip at the playhead." % wanted)
    t = wanted + 1                                      # "next_free": first free track above
    while True:
        if t > timeline.GetTrackCount("video"):
            timeline.AddTrack("video")
            return t
        if not _overlaps(timeline, t, start, end) and not timeline.GetIsTrackLocked("video", t):
            return t
        t += 1


# =============================================================================
# Public: insert animated title at playhead
# =============================================================================
def insert_title_at_playhead(style: str, params: Optional[dict] = None, track_index: int = 2,
                             on_conflict: str = "next_free", record_frame: Optional[int] = None,
                             clip_color: str = "Orange"):
    """
    Put an animated ResolveFlow title at the playhead on V{track_index} (default V2).

    params: TitleParams fields, e.g.
        {"text": "XIN CHÀO", "font": "Open Sans", "style": "Bold", "size": 0.09,
         "color": "#FFFFFF", "accent": "#FFD400", "duration_frames": 90, "delay_frames": 0}
    on_conflict: "next_free" | "overwrite" | "error"
    Returns the new TimelineItem.
    """
    if style not in TITLES:
        raise ResolveFlowError("Unknown style %r. Options: %s" % (style, ", ".join(TITLES)))
    ctx = context()
    ctx.resolve.OpenPage("edit")
    tl = ctx.timeline
    p = TitleParams.from_dict(params or {})
    fps, nominal, _ = timeline_rate(tl)
    w = int(tl.GetSetting("timelineResolutionWidth") or 1920)
    h = int(tl.GetSetting("timelineResolutionHeight") or 1080)
    dur = max(1, int(p.duration_frames))
    start = playhead_frame(tl) if record_frame is None else int(record_frame)
    track = resolve_track(tl, track_index, start, start + dur, on_conflict)

    carrier = get_carrier_clip(ctx, dur, w, h)
    placed = ctx.media_pool.AppendToTimeline([{
        "mediaPoolItem": carrier, "startFrame": 0, "endFrame": dur - 1,
        "trackIndex": track, "recordFrame": start, "mediaType": 1,
    }])
    if not placed:
        raise ResolveFlowError("AppendToTimeline failed (track V%d, frame %d)." % (track, start))
    item = placed[0]

    comp_path = os.path.join(cache_dir(), "comps", "%s_%d_%d.comp" % (style, start, track))
    export_title_comp(style, p, comp_path, w, h, fps)
    comp = item.ImportFusionComp(comp_path)
    if comp is None:
        tl.DeleteClips([item], False)
        raise ResolveFlowError("ImportFusionComp failed for %s" % comp_path)
    names = list(item.GetFusionCompNameList() or [])
    if names:
        item.LoadFusionCompByName(names[-1])          # imported comp becomes the active one
        for n in names[:-1]:                          # drop the default passthrough comp
            try:
                item.DeleteFusionCompByName(n)
            except Exception:
                pass
    try:
        item.SetName("RF " + build_title(style, p).name.replace("RF_", "").replace("_", " "))
    except Exception:
        pass
    if clip_color:
        item.SetClipColor(clip_color)
    return item


# -----------------------------------------------------------------------------
# TEMPLATE mode (installed .setting titles)
# -----------------------------------------------------------------------------
def _param_inputs(p: TitleParams) -> Dict[str, Any]:
    r, g, b = parse_color(p.color)
    v: Dict[str, Any] = {
        "Text": p.text, "Font": p.font, "Style": p.style, "Size": p.size,
        "Red": r, "Green": g, "Blue": b,
        "Delay": p.delay_frames, "Speed": p.speed,
        "Position": {1: p.position[0], 2: p.position[1]},
        "FadeIn": p.fade_in_frames, "FadeOut": p.fade_out_frames,
        "SubText": p.subtitle, "AuthorText": p.subtitle,
    }
    ar, ag, ab = parse_color(p.accent)
    for k in ("Marker", "Line", "Border", "Paper"):
        v.update({k + "Red": ar, k + "Green": ag, k + "Blue": ab})
    return v


def apply_title_params(item, params: dict) -> int:
    """Set published Inspector inputs of the ResolveFlow macro inside a title item."""
    p = TitleParams.from_dict(params or {})
    comp = item.GetFusionCompByIndex(1)
    if comp is None:
        return 0
    tool = None
    for t in (comp.GetToolList(False) or {}).values():
        if str(t.Name).startswith("RF_"):
            tool = t
            break
    if tool is None:
        return 0
    n = 0
    for key, val in _param_inputs(p).items():
        try:
            if tool.SetInput(key, val) is not False:
                n += 1
        except Exception:
            continue
    return n


def insert_installed_title_at_playhead(style: str, params: Optional[dict] = None, track_index: int = 2):
    """
    Uses Timeline.InsertFusionTitleIntoTimeline(<template name>). Requires install_templates()
    + a Resolve restart. Resolve chooses the destination track itself; we lock every other
    video track for the duration of the call and report where it landed.
    """
    ctx = context()
    ctx.resolve.OpenPage("edit")
    tl = ctx.timeline
    name = build_title(style).name
    while tl.GetTrackCount("video") < track_index:
        tl.AddTrack("video")
    count = tl.GetTrackCount("video")
    saved = {i: tl.GetIsTrackLocked("video", i) for i in range(1, count + 1)}
    try:
        for i in range(1, count + 1):
            tl.SetTrackLock("video", i, i != track_index)
        item = tl.InsertFusionTitleIntoTimeline(name)
    finally:
        for i, locked in saved.items():
            tl.SetTrackLock("video", i, bool(locked))
    if item is None:
        raise ResolveFlowError("Resolve could not insert %r. Run install_templates() and restart Resolve." % name)
    landed = None
    try:
        landed = item.GetTrackTypeAndIndex()[1]
    except Exception:
        pass
    apply_title_params(item, params or {})
    if landed is not None and int(landed) != track_index:
        sys.stderr.write("[ResolveFlow] Title landed on V%s instead of V%d; "
                         "use insert_title_at_playhead() for exact placement.\n" % (landed, track_index))
    return item


# =============================================================================
# CLI
# =============================================================================
def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="resolveflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="install all templates into Resolve's Fusion/Templates/Edit")
    ex = sub.add_parser("export", help="write .setting files to a folder")
    ex.add_argument("out")
    ins = sub.add_parser("insert", help="insert an animated title at the playhead")
    ins.add_argument("style", choices=sorted(TITLES))
    ins.add_argument("--text", default="RESOLVEFLOW")
    ins.add_argument("--subtitle", default="")
    ins.add_argument("--font", default="Open Sans")
    ins.add_argument("--font-style", default="Bold")
    ins.add_argument("--size", type=float, default=0.08)
    ins.add_argument("--color", default="#FFFFFF")
    ins.add_argument("--accent", default="#FFD400")
    ins.add_argument("--duration", type=int, default=90)
    ins.add_argument("--delay", type=float, default=0)
    ins.add_argument("--track", type=int, default=2)
    ins.add_argument("--mode", choices=["carrier", "template"], default="carrier")
    a = ap.parse_args(argv)
    if a.cmd == "install":
        for k, v in install_templates().items():
            print("%-12s %d files -> %s" % (k, len(v), os.path.dirname(v[0]) if v else "-"))
        print("Restart DaVinci Resolve to see them in the Effects library.")
    elif a.cmd == "export":
        for k, v in build_library(a.out).items():
            print(k, len(v))
    else:
        params = {"text": a.text, "subtitle": a.subtitle, "font": a.font, "style": a.font_style,
                  "size": a.size, "color": a.color, "accent": a.accent,
                  "duration_frames": a.duration, "delay_frames": a.delay}
        fn = insert_title_at_playhead if a.mode == "carrier" else insert_installed_title_at_playhead
        item = fn(a.style, params, track_index=a.track)
        print("Inserted", item.GetName(), "at frame", item.GetStart())


if __name__ == "__main__":
    _cli()
