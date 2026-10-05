from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class IResolveConnection(ABC):
    """
    Giao diện chuẩn hóa cho mọi kết nối tới DaVinci Resolve.
    Che giấu hoàn toàn việc đang dùng Studio (DIRECT), Free (BRIDGE) hay Xuất file (MANUAL).
    """

    @abstractmethod
    def mode_name(self) -> str:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def get_media_pool_file_paths(self) -> List[str]:
        pass

    @abstractmethod
    def import_media_to_media_pool(self, paths: List[str], log_callback: Optional[Any] = None) -> bool:
        pass

    @abstractmethod
    def get_active_timeline_name(self) -> Optional[str]:
        pass

    @abstractmethod
    def import_edl_to_timeline(self, edl_path: str, log_callback: Optional[Any] = None) -> bool:
        """
        Nhập EDL vào Timeline. Trả về True nếu thành công. 
        Nếu mode là MANUAL, sẽ hiển thị hướng dẫn cho người dùng nhập tay và trả về True.
        """
        pass
    
    @abstractmethod
    def get_current_playhead_seconds(self) -> float:
        """
        Lấy vị trí Playhead. Trả về 0.0 nếu thất bại hoặc MANUAL.
        """
        pass
        
    @abstractmethod
    def apply_look_lut(self, lut_path: str, scope: str = "timeline", skip_existing: bool = True, log_callback: Optional[Any] = None) -> bool:
        pass
