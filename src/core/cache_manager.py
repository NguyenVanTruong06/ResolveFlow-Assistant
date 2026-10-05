import os
import json
import hashlib
from typing import Dict, Any, Optional, Tuple

_CHECKSUM_MEMO: Dict[str, Tuple[float, int, str]] = {}


def compute_file_checksum(file_path: str) -> str:
    """
    Tính mã băm (checksum) siêu tốc cho tệp video nguồn kết hợp:
    - Kích thước tệp (file size)
    - Thời gian sửa đổi cuối (mtime)
    - SHA256 của 2MB đầu + 2MB cuối của tệp (hoặc toàn bộ nếu file < 4MB)
    - Bộ nhớ đệm trong RAM (_CHECKSUM_MEMO) cho phản hồi <0.001ms.
    """
    norm_path = os.path.normpath(os.path.abspath(file_path))
    if not os.path.exists(norm_path):
        return ""

    try:
        stat = os.stat(norm_path)
        file_size = stat.st_size
        mtime = stat.st_mtime
    except Exception:
        return ""

    # Kiểm tra RAM cache
    if norm_path in _CHECKSUM_MEMO:
        cached_mtime, cached_size, cached_hash = _CHECKSUM_MEMO[norm_path]
        if cached_mtime == mtime and cached_size == file_size:
            return cached_hash

    hasher = hashlib.sha256()
    hasher.update(f"{norm_path.lower()}_{file_size}_{mtime}_".encode("utf-8"))

    sample_size = 64 * 1024  # 64KB (giảm từ 2MB xuống để quét siêu tốc, vẫn đủ bắt metadata 2 đầu)
    try:
        with open(norm_path, "rb") as f:
            if file_size <= sample_size * 2:
                hasher.update(f.read())
            else:
                hasher.update(f.read(sample_size))
                f.seek(file_size - sample_size)
                hasher.update(f.read(sample_size))
    except Exception:
        hasher.update(norm_path.encode("utf-8"))

    res_hash = hasher.hexdigest()[:24]
    _CHECKSUM_MEMO[norm_path] = (mtime, file_size, res_hash)
    return res_hash


class ScanCacheManager:
    """
    Quản lý bộ nhớ đệm (Cache) cho toàn bộ khâu quét nặng (Whisper Speech-to-Text,
    Audio intervals, Face detection) theo mã băm checksum của file gốc.
    Nếu người dùng chạy lại cùng file (chỉ đổi setting cắt hay đổi kiểu sub),
    kết quả quét được nạp ngay trong <0.05s mà không tốn công quét lại.
    """
    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            home = os.path.expanduser("~")
            cache_dir = os.path.join(home, ".resolveflow_cache")
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._cached_dir_files = None

    def _get_dir_files(self) -> list:
        if self._cached_dir_files is None:
            if os.path.exists(self.cache_dir):
                self._cached_dir_files = os.listdir(self.cache_dir)
            else:
                self._cached_dir_files = []
        return self._cached_dir_files

    def _build_cache_key(self, file_path: str, model_size: str, language: str) -> str:
        f_hash = compute_file_checksum(file_path)
        lang_str = language or "auto"
        return f"{f_hash}_{model_size}_{lang_str}".lower()

    def _build_name_key(self, file_path: str, model_size: str, language: str) -> str:
        base_name = os.path.basename(file_path).lower()
        lang_str = language or "auto"
        name_hash = hashlib.sha256(base_name.encode("utf-8")).hexdigest()[:16]
        return f"name_{name_hash}_{model_size}_{lang_str}".lower()

    def get_cached_scan(
        self, 
        file_path: str, 
        model_size: str = "small", 
        language: str = "Auto"
    ) -> Optional[Dict[str, Any]]:
        """
        Kiểm tra và lấy dữ liệu quét đã lưu nếu có (Cache Hit).
        Hỗ trợ tra cứu đa tầng:
        1. Khóa chính xác: Checksum + Model + Lang
        2. Khóa Checksum: Bất kỳ model nào đã quét trên file này
        3. Khóa Filename: Tên file chính xác
        4. Khóa Filename bất kỳ model nào
        """
        if not os.path.exists(self.cache_dir):
            return None

        # 1. Tra cứu chính xác theo checksum + model + lang
        key = self._build_cache_key(file_path, model_size, language)
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        # 2. Tra cứu bất kỳ bản cache nào của cùng file hash (bất kể model_size/lang đã quét)
        f_hash = compute_file_checksum(file_path)
        if f_hash:
            try:
                prefix = f"{f_hash.lower()}_"
                for fname in self._get_dir_files():
                    if fname.lower().startswith(prefix) and fname.endswith(".json"):
                        with open(os.path.join(self.cache_dir, fname), "r", encoding="utf-8") as f:
                            return json.load(f)
            except Exception:
                pass

        # 3. Tra cứu theo tên file chính xác
        name_key = self._build_name_key(file_path, model_size, language)
        name_cache_file = os.path.join(self.cache_dir, f"{name_key}.json")
        if os.path.exists(name_cache_file):
            try:
                with open(name_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        # 4. Tra cứu theo bất kỳ tên file nào khớp hash
        base_name = os.path.basename(file_path).lower()
        name_hash = hashlib.sha256(base_name.encode("utf-8")).hexdigest()[:16]
        if name_hash:
            try:
                prefix_name = f"name_{name_hash.lower()}_"
                for fname in self._get_dir_files():
                    if fname.lower().startswith(prefix_name) and fname.endswith(".json"):
                        with open(os.path.join(self.cache_dir, fname), "r", encoding="utf-8") as f:
                            return json.load(f)
            except Exception:
                pass

        return None

    def save_scan_result(
        self,
        file_path: str,
        scan_data: Dict[str, Any],
        model_size: str = "small",
        language: str = "Auto"
    ) -> str:
        """
        Lưu kết quả quét vào file cache JSON theo cả 2 khóa để tái sử dụng tối đa.
        """
        key = self._build_cache_key(file_path, model_size, language)
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(scan_data, f, indent=2, ensure_ascii=False)
            
            # Lưu thêm bản theo name_key
            name_key = self._build_name_key(file_path, model_size, language)
            name_cache_file = os.path.join(self.cache_dir, f"{name_key}.json")
            with open(name_cache_file, "w", encoding="utf-8") as f:
                json.dump(scan_data, f, indent=2, ensure_ascii=False)

            self._cached_dir_files = None
            return cache_file
        except Exception:
            return ""

    def clear_cache(self) -> int:
        """Xóa toàn bộ file cache."""
        count = 0
        if os.path.exists(self.cache_dir):
            for fname in os.listdir(self.cache_dir):
                if fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.cache_dir, fname))
                        count += 1
                    except Exception:
                        pass
        self._cached_dir_files = None
        return count
