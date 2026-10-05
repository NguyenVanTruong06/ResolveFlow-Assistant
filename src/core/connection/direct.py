import os
from typing import List, Dict, Any, Optional
from .base import IResolveConnection

class DirectConnection(IResolveConnection):
    """
    Kết nối trực tiếp qua dvr_script (Chỉ hỗ trợ DaVinci Resolve Studio).
    """
    def __init__(self):
        self.resolve = None
        self._connect()

    def _connect(self):
        try:
            import DaVinciResolveScript as dvr_script
            self.resolve = dvr_script.scriptapp("Resolve")
        except Exception:
            self.resolve = None

    def mode_name(self) -> str:
        return "DIRECT (Resolve Studio)"

    def is_connected(self) -> bool:
        return self.resolve is not None

    def get_media_pool_file_paths(self) -> List[str]:
        if not self.is_connected(): return []
        pm = self.resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if not proj: return []
        # Giản lược: Trả về danh sách rỗng (Cần implement đệ quy lấy clip name/path)
        return []

    def import_media_to_media_pool(self, paths: List[str], log_callback: Optional[Any] = None) -> bool:
        if not self.is_connected(): return False
        pm = self.resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if not proj: return False
        mp = proj.GetMediaPool()
        items = mp.ImportMedia(paths)
        return len(items) > 0

    def get_active_timeline_name(self) -> Optional[str]:
        if not self.is_connected(): return None
        pm = self.resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if not proj: return None
        tl = proj.GetCurrentTimeline()
        return tl.GetName() if tl else None

    def import_edl_to_timeline(self, edl_path: str, log_callback: Optional[Any] = None) -> bool:
        if not self.is_connected(): return False
        pm = self.resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if not proj: return False
        mp = proj.GetMediaPool()
        tl = mp.ImportTimelineFromFile(os.path.abspath(edl_path))
        return tl is not None

    def get_current_playhead_seconds(self) -> float:
        if not self.is_connected(): return 0.0
        # ... implement api code here
        return 0.0

    def apply_look_lut(self, lut_path: str, scope: str = "timeline", skip_existing: bool = True, log_callback: Optional[Any] = None) -> bool:
        return False
