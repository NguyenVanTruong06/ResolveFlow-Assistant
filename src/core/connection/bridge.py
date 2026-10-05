import json
import socket
import os
import traceback
from typing import List, Dict, Any, Optional
from .base import IResolveConnection

class BridgeConnection(IResolveConnection):
    """
    Kết nối qua socket tới script ChunDVC_Bridge đang chạy ngầm trong DaVinci Resolve.
    Dùng cho bản Free không hỗ trợ import dvr_script.
    """
    def __init__(self, host="127.0.0.1", port=17005):
        self.host = host
        self.port = port
        
    def mode_name(self) -> str:
        return "BRIDGE (Resolve Free)"
        
    def _send_request(self, command: str, payload: dict = None) -> dict:
        try:
            req = {"command": command}
            if payload:
                req["payload"] = payload
                
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(3.0)
            client.connect((self.host, self.port))
            client.sendall(json.dumps(req).encode('utf-8'))
            
            data = client.recv(4096)
            client.close()
            
            if data:
                return json.loads(data.decode('utf-8'))
            return {"status": "error", "message": "No response"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def is_connected(self) -> bool:
        resp = self._send_request("ping")
        return resp.get("status") == "ok"

    def get_media_pool_file_paths(self) -> List[str]:
        resp = self._send_request("get_media_pool_file_paths")
        if resp.get("status") == "ok":
            return resp.get("paths", [])
        return []

    def import_media_to_media_pool(self, paths: List[str], log_callback: Optional[Any] = None) -> bool:
        resp = self._send_request("import_media", {"paths": paths})
        if resp.get("status") == "ok":
            if log_callback: log_callback("✅ Đã import media qua Bridge.")
            return True
        if log_callback: log_callback(f"❌ Lỗi Bridge: {resp.get('message')}")
        return False

    def get_active_timeline_name(self) -> Optional[str]:
        resp = self._send_request("get_active_timeline_name")
        if resp.get("status") == "ok":
            return resp.get("name")
        return None

    def import_edl_to_timeline(self, edl_path: str, log_callback: Optional[Any] = None) -> bool:
        # API của DaVinci Free thường không chặn ImportTimelineFromFile nếu gọi từ trong script
        resp = self._send_request("import_edl", {"edl_path": os.path.abspath(edl_path)})
        if resp.get("status") == "ok":
            if log_callback: log_callback("✅ Nhập EDL thành công qua Bridge.")
            return True
        if log_callback: log_callback(f"⚠️ Nhập EDL qua Bridge thất bại: {resp.get('message')}. Hãy chuyển sang chế độ MANUAL.")
        return False

    def get_current_playhead_seconds(self) -> float:
        resp = self._send_request("get_playhead_seconds")
        if resp.get("status") == "ok":
            return float(resp.get("seconds", 0.0))
        return 0.0

    def apply_look_lut(self, lut_path: str, scope: str = "timeline", skip_existing: bool = True, log_callback: Optional[Any] = None) -> bool:
        resp = self._send_request("apply_lut", {"lut_path": lut_path, "scope": scope})
        if resp.get("status") == "ok":
            if log_callback: log_callback(f"✅ Đã áp LUT {os.path.basename(lut_path)} qua Bridge.")
            return True
        if log_callback: log_callback(f"❌ Áp LUT lỗi: {resp.get('message')}")
        return False
