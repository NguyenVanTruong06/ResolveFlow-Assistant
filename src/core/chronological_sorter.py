"""
Chronological Sorter Utility
============================
Sắp xếp danh sách clip nguồn theo đúng mạch thời gian thực tế diễn ra (Chronological Order).
Thứ tự ưu tiên:
1. Timestamp trong tên tệp (DJI, GoPro, Camera) (source="filename")
2. Creation Time trong container media từ FFprobe (format_tags/stream_tags, UTC -> local time) (source="container")
3. Thời gian tạo/sửa đổi file trên đĩa (mtime) (source="mtime")
4. Tie-breaker: (shot_time, file_name, content_hash)
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple


def get_clip_shot_time(file_path: str) -> Tuple[str, str]:
    """
    Trích xuất mốc thời gian quay thực tế (ISO string định dạng địa phương) và nguồn gốc.
    Trả về: (shot_time_iso, source)
    source in {"filename", "container", "mtime", "none"}
    """
    if not file_path:
        return ("9999-99-99T99:99:99", "none")

    cpath_str = str(file_path)
    fname = os.path.basename(cpath_str)

    # 1. Regex từ tên file (ưu tiên cao nhất - DJI, GoPro, Dashcam)
    # DJI: DJI_20260831114657_0010_D.MP4 -> 2026-08-31T11:46:57
    m1 = re.search(r"(\d{4})(\d{2})(\d{2})[-_]?(\d{2})(\d{2})(\d{2})", fname)
    if m1:
        shot_time = f"{m1.group(1)}-{m1.group(2)}-{m1.group(3)}T{m1.group(4)}:{m1.group(5)}:{m1.group(6)}"
        return (shot_time, "filename")

    # Định dạng YYYY-MM-DD_HH-MM-SS hoặc YYYY-MM-DD HH-MM-SS
    m2 = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_ ](\d{2})[-_:](\d{2})[-_:](\d{2})", fname)
    if m2:
        shot_time = f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}T{m2.group(4)}:{m2.group(5)}:{m2.group(6)}"
        return (shot_time, "filename")

    # 2. Đọc creation_time trong container media qua ffprobe (nếu file tồn tại)
    if os.path.exists(cpath_str) and os.path.isfile(cpath_str):
        try:
            from src.core.ffmpeg_utils import get_ffprobe_path
            import json
            import subprocess

            ffprobe = get_ffprobe_path()
            if ffprobe:
                cmd = [
                    ffprobe, "-v", "error",
                    "-show_entries", "format_tags=creation_time:stream_tags=creation_time",
                    "-of", "json",
                    cpath_str
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
                if res.returncode == 0 and res.stdout:
                    info = json.loads(res.stdout)
                    ct_str = None
                    if "format" in info and "tags" in info["format"]:
                        ct_str = info["format"]["tags"].get("creation_time")
                    if not ct_str and "streams" in info:
                        for st in info["streams"]:
                            if "tags" in st and "creation_time" in st["tags"]:
                                ct_str = st["tags"]["creation_time"]
                                break

                    if ct_str:
                        # Thường có dạng ISO UTC: "2026-08-31T04:46:57.000000Z" hoặc "2026-08-31 04:46:57"
                        clean_ct = ct_str.strip().replace("Z", "+00:00")
                        try:
                            dt = datetime.fromisoformat(clean_ct)
                            # Đổi sang giờ máy địa phương nếu có timezone (hoặc mặc định coi là UTC)
                            if dt.tzinfo is not None:
                                dt_local = dt.astimezone()
                            else:
                                dt_local = dt.replace(tzinfo=timezone.utc).astimezone()
                            shot_time = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
                            return (shot_time, "container")
                        except Exception:
                            pass
        except Exception:
            pass

    # 3. Fallback theo mtime của file trên đĩa
    if os.path.exists(cpath_str) and os.path.isfile(cpath_str):
        try:
            mtime = os.path.getmtime(cpath_str)
            dt_local = datetime.fromtimestamp(mtime)
            shot_time = dt_local.strftime("%Y-%m-%dT%H:%M:%S")
            return (shot_time, "mtime")
        except Exception:
            pass

    # 4. Fallback cuối cùng
    return ("9999-99-99T99:99:99", "none")


def get_chronological_sort_key(clip_item: Any) -> Tuple[str, str, str]:
    """
    Trả về tuple khóa sắp xếp chuẩn xác:
    (shot_time, file_name, content_hash)
    """
    cpath = getattr(clip_item, "current_path", getattr(clip_item, "path", clip_item))
    if not isinstance(cpath, str):
        cpath = str(cpath)

    raw_fname = getattr(clip_item, "file_name", getattr(clip_item, "filename", os.path.basename(cpath)))
    fname = str(raw_fname) if not isinstance(raw_fname, str) else raw_fname

    raw_chash = getattr(clip_item, "content_hash", "")
    chash = str(raw_chash) if not isinstance(raw_chash, str) else raw_chash
    if not chash and os.path.exists(cpath):
        try:
            from src.core.clip_manifest import compute_content_hash
            chash = compute_content_hash(cpath)
        except Exception:
            chash = ""

    raw_shot_time = getattr(clip_item, "shot_time", "")
    shot_time = str(raw_shot_time) if not isinstance(raw_shot_time, str) else raw_shot_time
    if not shot_time or shot_time == "none":
        shot_time, _ = get_clip_shot_time(cpath)

    return (str(shot_time), str(fname), str(chash))


def sort_clips_chronologically(clips: List[Any]) -> List[Any]:
    """Sắp xếp danh sách clip theo đúng trình tự thời gian quay (wrapper tương thích ngược)."""
    if not clips:
        return []
    return sorted(clips, key=get_chronological_sort_key)
