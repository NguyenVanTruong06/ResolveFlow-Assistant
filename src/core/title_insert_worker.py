"""
ChunDVC (ResolveFlow) Title Insert Worker.

One-shot worker process that executes Fusion Title insertion via the
NEW-NESTED-NO-RIPPLE-V2 algorithm and exits immediately.
This isolates the long-running GUI from fusionscript COM proxy leaks,
thread locks, and UI freezing.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from typing import Any, Dict, Optional

_RESULT_FILE = ""


def emit(payload: Dict[str, Any]) -> None:
    """Prints result token for stdout parsing and writes to result-file if set."""
    text = "CHUNDVC_RESULT:" + json.dumps(payload, ensure_ascii=False)
    try:
        print(text, flush=True)
    except Exception:
        pass

    global _RESULT_FILE
    if _RESULT_FILE:
        try:
            target = os.path.abspath(_RESULT_FILE)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            temp = target + ".tmp"
            with open(temp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(temp, target)
        except Exception:
            pass


def setup_worker_resolve_environment() -> None:
    """Prepares library paths and module paths for DaVinciResolveScript."""
    candidates = []
    explicit = os.environ.get("CHUNDVC_DVR_MODULE_DIR", "").strip()
    if explicit:
        candidates.append(explicit)

    api_root = os.environ.get("RESOLVE_SCRIPT_API", "").strip()
    if api_root:
        candidates.append(os.path.join(api_root, "Modules"))
        candidates.append(api_root)

    if sys.platform.startswith("linux"):
        default_api = "/opt/resolve/Developer/Scripting"
        linux_lib_candidates = [
            "/opt/resolve/libs/Fusion/fusionscript.so",
            "/opt/resolve/libs/Fusion/libfusionscript.so",
            "/opt/resolve/libs/fusionscript.so",
            "/opt/resolve/libs/libfusionscript.so",
        ]
        if not api_root and os.path.isdir(default_api):
            api_root = default_api
        lib_path = os.environ.get("RESOLVE_SCRIPT_LIB")
        if not lib_path:
            lib_path = next((c for c in linux_lib_candidates if os.path.isfile(c)), None)

        if lib_path and os.path.exists(lib_path):
            os.environ["RESOLVE_SCRIPT_LIB"] = lib_path
            try:
                ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass

        candidates.extend([
            "/opt/resolve/Developer/Scripting/Modules",
            "/home/resolve/Developer/Scripting/Modules",
        ])

    elif sys.platform.startswith("darwin"):
        candidates.append(
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        )
        if not os.environ.get("RESOLVE_SCRIPT_LIB"):
            lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.dylib"
            if os.path.exists(lib):
                os.environ["RESOLVE_SCRIPT_LIB"] = lib

    else:
        # Windows
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        candidates.extend([
            os.path.join(program_data, r"Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"),
            r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
        ])
        if not os.environ.get("RESOLVE_SCRIPT_LIB"):
            lib = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
            if os.path.exists(lib):
                os.environ["RESOLVE_SCRIPT_LIB"] = lib

    if api_root:
        os.environ["RESOLVE_SCRIPT_API"] = api_root
        mod_dir = os.path.join(api_root, "Modules")
        if mod_dir not in candidates:
            candidates.insert(0, mod_dir)

    for p in candidates:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    global _RESULT_FILE
    parser = argparse.ArgumentParser(description="ChunDVC Title Insertion Worker")
    parser.add_argument("--title", required=True, help="Title Template Name / Style")
    parser.add_argument("--display-name", default="", help="Display name for generated assets")
    parser.add_argument("--timeline", default="", help="Target timeline name")
    parser.add_argument("--preferred-track", type=int, default=2, help="Preferred target video track")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration in seconds")
    parser.add_argument("--record-frame", type=int, default=-1, help="Specific start record frame (-1 for playhead)")
    parser.add_argument("--result-file", default="", help="Path to write JSON result file")

    args = parser.parse_args()
    _RESULT_FILE = str(args.result_file or "").strip()

    setup_worker_resolve_environment()

    try:
        import DaVinciResolveScript as dvr
    except ImportError as exc:
        emit({
            "ok": False,
            "error": "import_dvr_failed",
            "detail": str(exc),
        })
        return 2

    try:
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            emit({
                "ok": False,
                "error": "resolve_not_found",
                "detail": "scriptapp('Resolve') returned None",
            })
            return 3

        # Ensure Edit page
        page = str(resolve.GetCurrentPage() or "").lower()
        if page != "edit":
            resolve.OpenPage("edit")
            time.sleep(0.12)

        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject() if pm else None
        if not project:
            emit({"ok": False, "error": "project_not_found"})
            return 4

        timeline = project.GetCurrentTimeline()
        preferred_tl = str(args.timeline or "").strip()
        if preferred_tl:
            if timeline is None or str(timeline.GetName()) != preferred_tl:
                tl_count = project.GetTimelineCount() or 0
                for i in range(1, int(tl_count) + 1):
                    cand = project.GetTimelineByIndex(i)
                    if cand and str(cand.GetName()) == preferred_tl:
                        project.SetCurrentTimeline(cand)
                        timeline = cand
                        break

        if not timeline:
            emit({"ok": False, "error": "timeline_not_found"})
            return 5

        # Execute NEW-NESTED-NO-RIPPLE-V2 insertion
        from src.core.resolve_api import import_edit_preset_v2

        rec_frame = None if args.record_frame < 0 else args.record_frame
        result = import_edit_preset_v2(
            title_name=args.title,
            display_name=args.display_name or args.title,
            preferred_track=args.preferred_track,
            record_frame=rec_frame,
            duration_sec=args.duration,
            resolve_app=resolve,
        )

        emit(result)
        return 0 if result.get("ok") else 6

    except Exception as exc:
        emit({
            "ok": False,
            "error": "worker_exception",
            "detail": str(exc),
        })
        return 7


if __name__ == "__main__":
    sys.exit(main())
