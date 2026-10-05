from .base import IResolveConnection
from .direct import DirectConnection
from .bridge import BridgeConnection
from .manual import ManualConnection

class ConnectionManager:
    _instance: IResolveConnection = None

    @classmethod
    def get_connection(cls, preferred_mode: str = "auto") -> IResolveConnection:
        """
        Khởi tạo kết nối theo thứ tự: Direct -> Bridge -> Manual
        """
        if cls._instance:
            return cls._instance

        if preferred_mode in ["auto", "direct"]:
            conn = DirectConnection()
            if conn.is_connected():
                cls._instance = conn
                return cls._instance

        if preferred_mode in ["auto", "bridge"]:
            conn = BridgeConnection()
            if conn.is_connected():
                cls._instance = conn
                return cls._instance

        cls._instance = ManualConnection()
        return cls._instance
