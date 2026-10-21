"""
ChunDVC / ResolveFlow - Live Capture Preset from Timeline.

Extracts TextPlus / Fusion comp from the timeline clip directly under the playhead,
normalizes away MediaOut / Saver nodes, formats into standard MacroOperator,
auto-relinks media paths, and installs into DaVinci Resolve's Edit/Titles/ChunDVC directory.
"""
from __future__ import annotations

import os
import platform
import re
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


_TOOLS_START_RE = re.compile(r'^\s*Tools\s*=\s*(?:ordered\(\)\s*)?\{', re.IGNORECASE)
_MEDIA_OUT_START_RE = re.compile(r'^\s*(?:MediaOut\d*|Saver\d*)\s*=\s*\w+\s*\{', re.IGNORECASE)
_TEXT_TOOL_RE = re.compile(r'\b(TextPlus|Text3D)\b', re.IGNORECASE)


def get_davinci_titles_dir(subfolder: str = "ChunDVC") -> str:
    """
    Returns the absolute path to DaVinci Resolve's user Titles template directory.
    """
    sys_name = platform.system()
    if sys_name == "Windows":
        appdata = os.getenv("APPDATA", "")
        base = os.path.join(
            appdata,
            "Blackmagic Design",
            "DaVinci Resolve",
            "Support",
            "Fusion",
            "Templates",
            "Edit",
            "Titles",
        )
    elif sys_name == "Darwin":
        base = os.path.expanduser(
            "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/Titles"
        )
    else:
        # Linux
        base = os.path.expanduser(
            "~/.local/share/DaVinciResolve/Fusion/Templates/Edit/Titles"
        )

    target_dir = os.path.join(base, subfolder) if subfolder else base
    os.makedirs(target_dir, exist_ok=True)
    return os.path.abspath(target_dir)


def safe_preset_name(name: str, limit: int = 40) -> str:
    """Sanitize preset name for file system safety."""
    if not name:
        name = "ChunDVC_Preset"
    cleaned = re.sub(r'[^0-9A-Za-zÀ-ỹ.-]+', '_', str(name))
    cleaned = re.sub(r'_+', '_', cleaned).strip(' ._')
    if not cleaned:
        cleaned = "ChunDVC_Preset"
    return cleaned[:max(8, int(limit))]



def extract_tools_without_mediaout(lines: List[str]) -> Optional[str]:
    """
    Parses a Fusion .comp file, extracts the contents of the Tools table,
    and strips any top-level MediaOut or Saver nodes to prevent port collisions.
    """
    inside = False
    depth = 0
    skipping_media_out = False
    collected: List[str] = []

    for line in lines:
        if not inside:
            if _TOOLS_START_RE.match(line):
                inside = True
                delta = line.count('{') - line.count('}')
                depth = max(1, delta)
            continue

        before_depth = depth
        delta = line.count('{') - line.count('}')

        if not skipping_media_out:
            if before_depth == 1 and _MEDIA_OUT_START_RE.match(line):
                skipping_media_out = True
                depth += delta
                if depth <= 1:
                    skipping_media_out = False
                continue
        else:
            depth += delta
            if depth <= 1:
                skipping_media_out = False
            continue

        new_depth = depth + delta
        if new_depth <= 0:
            break

        collected.append(line)
        depth = new_depth

    body = "".join(collected).strip()
    if len(body) < 10:
        return None

    return body


def format_fusion_code(raw_code: str, preset_name: str) -> str:
    """
    Wraps raw tool definitions in a standard DaVinci Resolve MacroOperator structure.
    """
    raw_code = str(raw_code or "").strip()
    if "MacroOperator" in raw_code:
        return re.sub(r'Name\s*=\s*"[^"]+"', f'Name = "{preset_name}"', raw_code, count=1)

    if raw_code.startswith("{") and raw_code.endswith("}"):
        inner = raw_code[1:-1].strip()
        return (
            f'MacroOperator {{\n'
            f'\tName = "{preset_name}",\n'
            f'\tPassThrough = true,\n'
            f'\tMacroVersion = 1,\n'
            f'\tInputs = {{}},\n'
            f'\tOutputs = {{}},\n'
            f'\t{inner}\n'
            f'}}'
        )

    return (
        f'MacroOperator {{\n'
        f'\tName = "{preset_name}",\n'
        f'\tPassThrough = true,\n'
        f'\tMacroVersion = 1,\n'
        f'\tInputs = {{}},\n'
        f'\tOutputs = {{}},\n'
        f'\tTools = ordered() {{\n\t\t'
        f'{raw_code}\n'
        f'\t}}\n'
        f'}}'
    )


def auto_relink_setting_file(
    content: str, media_index: Optional[Dict[str, str]] = None
) -> str:
    """
    Normalizes texture/file paths in Fusion .setting code:
    - Converts backslashes to forward slashes.
    - If media_index is provided, replaces localized file paths with known asset paths.
    """
    media_index = media_index or {}

    def replacer(match: re.Match) -> str:
        prefix = match.group(1)
        original_path = match.group(2)
        base_filename = os.path.basename(original_path.replace("\\", "/"))

        if base_filename in media_index:
            new_path = media_index[base_filename].replace("\\", "/")
            return f'{prefix}"{new_path}"'

        norm = original_path.replace("\\", "/")
        return f'{prefix}"{norm}"'

    pattern = re.compile(r'(?i)((?:Clip|Filename)\s*=\s*(?:Input\s*\{\s*Value\s*=\s*)?)"([^"]+)"')
    return pattern.sub(replacer, content)



def capture_playhead_text_preset(
    preset_name: str,
    resolve_app: Optional[Any] = None,
    log_callback: Optional[Any] = None,
    dest_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Finds the active Text+/Fusion clip under the playhead, extracts its composition,
    converts it to a MacroOperator .setting file, and writes it to DaVinci's Titles template directory.

    Returns dict with keys:
        - ok (bool): True if succeeded.
        - error (str, optional): Error code if failed.
        - setting_path (str, optional): Target .setting path.
        - item_name (str, optional): Name of captured timeline item.
        - message (str): Human-readable message.
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if resolve_app is None:
        from src.core.resolve_bridge import load_resolve_api
        resolve_app, _ = load_resolve_api()

    if not resolve_app:
        return {"ok": False, "error": "resolve_not_found", "message": "Không kết nối được với DaVinci Resolve."}

    try:
        page = str(resolve_app.GetCurrentPage() or "").lower()
        if page != "edit":
            resolve_app.OpenPage("edit")
            time.sleep(0.15)
    except Exception:
        pass

    pm = resolve_app.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    if not project:
        return {"ok": False, "error": "project_not_found", "message": "Không có dự án nào đang mở trong DaVinci Resolve."}

    timeline = project.GetCurrentTimeline()
    if not timeline:
        return {"ok": False, "error": "timeline_not_found", "message": "Không có timeline nào đang mở trên trang Edit."}

    # Determine playhead frame
    from src.core.resolve_bridge import playhead_frame
    try:
        cur_frame = playhead_frame(timeline)
    except Exception:
        cur_frame = 0

    track_count = timeline.GetTrackCount("video")
    captured_item = None
    captured_comp = None
    captured_track = 0

    log(f" 🔍 Đang quét clip Fusion/Text+ tại Playhead (Frame {cur_frame}) trên {track_count} video track...")

    for t_idx in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", t_idx) or []
        for it in items:
            try:
                s = int(it.GetStart())
                e = int(it.GetEnd())
            except Exception:
                continue

            if s <= cur_frame < e:
                # Clip is under the playhead
                comp_names = it.GetFusionCompNameList() or []
                if isinstance(comp_names, dict):
                    comp_names = list(comp_names.values())

                if comp_names:
                    # Prefer first comp
                    comp = it.GetFusionCompByName(comp_names[0]) or it.GetFusionCompByIndex(1)
                    if comp:
                        captured_item = it
                        captured_comp = comp
                        captured_track = t_idx
                        break
        if captured_item:
            break

    if not captured_item or not captured_comp:
        return {
            "ok": False,
            "error": "no_clip_at_playhead",
            "message": f"Không tìm thấy clip Fusion hoặc Text+ nào dưới vị trí Playhead (Frame {cur_frame}). Hãy di chuyển Playhead đến đúng clip cần hút.",
        }

    clean_name = safe_preset_name(preset_name)
    target_folder = dest_dir or get_davinci_titles_dir("ChunDVC")
    os.makedirs(target_folder, exist_ok=True)
    setting_path = os.path.join(target_folder, f"{clean_name}.setting")

    # Export comp to temp file
    temp_dir = tempfile.gettempdir()
    temp_comp_path = os.path.join(temp_dir, f"capture_{clean_name}_{int(time.time())}.comp")

    try:
        if os.path.exists(temp_comp_path):
            os.remove(temp_comp_path)

        success = captured_comp.Export(temp_comp_path)
        if not success or not os.path.isfile(temp_comp_path):
            return {
                "ok": False,
                "error": "comp_export_failed",
                "message": "Không thể xuất Composition từ clip. Hãy kiểm tra quyền ghi tệp tạm.",
            }

        with open(temp_comp_path, "r", encoding="utf-8", errors="replace") as fh:
            comp_lines = fh.readlines()

        full_text = "".join(comp_lines)
        if not _TEXT_TOOL_RE.search(full_text):
            log(" ⚠️ Cảnh báo: Clip được chọn không chứa node TextPlus/Text3D rõ ràng, sẽ hút toàn bộ cấu trúc công cụ.")

        clean_tools = extract_tools_without_mediaout(comp_lines)
        if not clean_tools:
            # Fallback: if no Tools table extracted, use raw text
            clean_tools = full_text

        macro_code = format_fusion_code(clean_tools, clean_name)
        final_code = auto_relink_setting_file(macro_code)

        with open(setting_path, "w", encoding="utf-8") as out_fh:
            out_fh.write(final_code)

        log(f" ✔ Đã lưu Preset thành công vào: {setting_path}")
        return {
            "ok": True,
            "setting_path": setting_path,
            "item_name": captured_item.GetName() or clean_name,
            "track_index": captured_track,
            "message": f"Đã hút thành công style từ track V{captured_track} thành preset '{clean_name}'.",
        }

    except Exception as e:
        return {
            "ok": False,
            "error": "capture_exception",
            "message": f"Lỗi trong quá trình hút preset: {str(e)}",
        }
    finally:
        if os.path.exists(temp_comp_path):
            try:
                os.remove(temp_comp_path)
            except Exception:
                pass
