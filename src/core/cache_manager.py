import os
import re
import json
import math
import hashlib
import logging
import stat as stat_mod
import tempfile
from typing import Dict, Any, Optional, Tuple, List, Callable
from pathlib import Path

CACHE_SCHEMA_VERSION = 2
CACHE_SCHEMA_VERSION_V3 = 3
_CHECKSUM_MEMO: Dict[str, Tuple[float, int, str]] = {}


def normalize_language_code(lang: Optional[str]) -> str:
    """
    Chuẩn hóa mã ngôn ngữ về dạng chuẩn: 'vi', 'en', hoặc 'auto'.
    """
    if not lang:
        return "auto"
    l = str(lang).strip().lower()
    if l in ["vi", "tiếng việt", "vietnamese"]:
        return "vi"
    if l in ["en", "tiếng anh", "english"]:
        return "en"
    if l in ["auto", "tự nhận diện", "tự động"]:
        return "auto"
    return l


def compute_file_checksum(file_path: str) -> str:
    """
    Tính mã băm (checksum) siêu tốc cho tệp video nguồn kết hợp:
    - Kích thước tệp (file size)
    - Thời gian sửa đổi cuối (mtime)
    - SHA256 của 64KB đầu + 64KB cuối của tệp (hoặc toàn bộ nếu file < 128KB)
    - Bộ nhớ đệm trong RAM (_CHECKSUM_MEMO) cho phản hồi <0.001ms.
    """
    norm_path = os.path.normpath(os.path.abspath(file_path))
    if not os.path.exists(norm_path):
        return ""

    try:
        stat = os.stat(norm_path)
        file_size = stat.st_size
        mtime = stat.st_mtime
    except Exception as e:
        logging.debug(f"Không thể lấy stat của '{norm_path}': {e}")
        return ""

    # Kiểm tra RAM cache
    if norm_path in _CHECKSUM_MEMO:
        cached_mtime, cached_size, cached_hash = _CHECKSUM_MEMO[norm_path]
        if cached_mtime == mtime and cached_size == file_size:
            return cached_hash

    hasher = hashlib.sha256()
    hasher.update(f"{norm_path.lower()}_{file_size}_{mtime}_".encode("utf-8"))

    sample_size = 64 * 1024  # 64KB
    try:
        with open(norm_path, "rb") as f:
            if file_size <= sample_size * 2:
                hasher.update(f.read())
            else:
                hasher.update(f.read(sample_size))
                f.seek(file_size - sample_size)
                hasher.update(f.read(sample_size))
    except Exception as e:
        logging.warning(f"Không thể đọc nội dung file '{norm_path}' để tính checksum: {e}")
        hasher.update(norm_path.encode("utf-8"))

    res_hash = hasher.hexdigest()[:24]
    _CHECKSUM_MEMO[norm_path] = (mtime, file_size, res_hash)
    return res_hash


_SHA256_HEX_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


def is_canonical_sha256_hex(val: Any) -> bool:
    """Kiểm tra chuỗi băm SHA256 canonical 64 ký tự hex hợp lệ."""
    if not isinstance(val, str) or len(val) != 64:
        return False
    return bool(_SHA256_HEX_REGEX.match(val))


def _is_safe_finite_number(
    val: Any,
    min_val: Optional[float] = None,
    strictly_positive: bool = False
) -> bool:
    """
    Kiểm tra giá trị số hữu hạn an toàn trước bigint và OverflowError:
    - Loại trừ hoàn toàn bool, None, string, object.
    - An toàn trước các số nguyên cực lớn (bigint > 1024 bits như 10**400) không gây OverflowError.
    - Loại trừ NaN, +/-Infinity.
    - Kiểm tra điều kiện dương hữu hạn hoặc cận dưới nếu có.
    """
    if type(val) not in (int, float) or isinstance(val, bool):
        return False
    try:
        if isinstance(val, int):
            if val.bit_length() > 1024:
                return False
            f_val = float(val)
        else:
            f_val = val

        if not math.isfinite(f_val):
            return False
    except (OverflowError, ValueError):
        return False

    if strictly_positive and f_val <= 0:
        return False
    if min_val is not None and f_val < min_val:
        return False
    return True


def validate_file_identity(file_path: str, provenance: Any) -> bool:
    """
    Xác thực tính ổn định và tính toàn vẹn của tệp nguồn so với provenance đã thu thập.
    Đối chiếu nghiêm ngặt:
    - Tệp tồn tại và là regular file (không phải directory/symlink/fifo)
    - Kích thước tệp (file_size, strict int >= 0)
    - Thời gian sửa đổi nano-giây (mtime_ns, strict int)
    - Thời gian thay đổi metadata nano-giây (ctime_ns, strict int nếu platform có)
    - Device ID (dev, strict int nếu platform có)
    - Inode (ino, strict int nếu platform có)
    
    Fail-closed: Trả về False cho bất kỳ sai lệch, thiếu thông tin, sai kiểu, hoặc lỗi I/O nào.
    """
    if not isinstance(provenance, dict):
        return False

    norm_path = os.path.normpath(os.path.abspath(file_path))
    try:
        st = os.stat(norm_path)
    except Exception:
        return False

    if not stat_mod.S_ISREG(st.st_mode):
        return False

    expected_size = provenance.get("file_size")
    expected_mtime_ns = provenance.get("mtime_ns")
    if type(expected_size) is not int or isinstance(expected_size, bool) or expected_size < 0:
        return False
    if type(expected_mtime_ns) is not int or isinstance(expected_mtime_ns, bool):
        return False

    if st.st_size != expected_size:
        return False
    if st.st_mtime_ns != expected_mtime_ns:
        return False

    st_ctime = getattr(st, "st_ctime_ns", None)
    if st_ctime is not None:
        expected_ctime_ns = provenance.get("ctime_ns")
        if type(expected_ctime_ns) is not int or isinstance(expected_ctime_ns, bool):
            return False
        if st_ctime != expected_ctime_ns:
            return False

    st_dev = getattr(st, "st_dev", None)
    if st_dev is not None:
        expected_dev = provenance.get("dev")
        if type(expected_dev) is not int or isinstance(expected_dev, bool):
            return False
        if st_dev != expected_dev:
            return False

    st_ino = getattr(st, "st_ino", None)
    if st_ino is not None:
        expected_ino = provenance.get("ino")
        if type(expected_ino) is not int or isinstance(expected_ino, bool):
            return False
        if st_ino != expected_ino:
            return False

    return True


def validate_v3_payload(
    payload: Any,
    expected_full_sha256: Optional[str] = None,
    expected_model: Optional[str] = None,
    expected_lang: Optional[str] = None,
) -> bool:
    """
    Xác thực cấu trúc payload Schema v3 nghiêm ngặt:
    - schema_version là số nguyên int chính xác = 3 (không bool, không float như 3.0)
    - algorithm_version == "sha256_full_v3"
    - full_content_sha256 là chuỗi 64 ký tự hex hợp lệ, khớp với expected_full_sha256 nếu có
    - model và language_requested khớp tuyệt đối nếu có
    - clip_dur là số dương hữu hạn (an toàn trước bigint/OverflowError, không bool, không NaN/Inf, > 0)
    - raw_subtitles là list:
      * [] hợp lệ
      * nếu có phần tử: mỗi phần tử phải là Mapping với start (số >= 0 hữu hạn), end (số > start hữu hạn), text (str)
    - Các trường tùy chọn (nếu có):
      * silence_keep_intervals: list các tuple/list (start, end) hữu hạn
      * speedup_segments: list
      * cut_segments: list
    """
    if not isinstance(payload, dict):
        return False

    try:
        # 1. cache_schema_version: exact int 3
        schema_ver = payload.get("cache_schema_version")
        if type(schema_ver) is not int or schema_ver != 3:
            return False

        # 2. algorithm_version
        if payload.get("algorithm_version") != "sha256_full_v3":
            return False

        # 3. full_content_sha256: canonical 64hex
        full_sha = payload.get("full_content_sha256")
        if not is_canonical_sha256_hex(full_sha):
            return False
        if expected_full_sha256 is not None:
            if not is_canonical_sha256_hex(expected_full_sha256):
                return False
            if full_sha.lower() != expected_full_sha256.lower():
                return False

        # 4. model & language_requested
        if expected_model is not None:
            if str(payload.get("model") or "").lower().strip() != expected_model.lower().strip():
                return False
        if expected_lang is not None:
            if normalize_language_code(payload.get("language_requested")) != normalize_language_code(expected_lang):
                return False

        # 5. clip_dur: finite positive number, NOT bool, safe against bigint overflow
        dur = payload.get("clip_dur")
        if not _is_safe_finite_number(dur, strictly_positive=True):
            return False

        # 6. raw_subtitles
        subs = payload.get("raw_subtitles")
        if not isinstance(subs, list):
            return False
        for seg in subs:
            if not isinstance(seg, dict):
                return False
            s_start = seg.get("start")
            s_end = seg.get("end")
            s_text = seg.get("text")
            if not _is_safe_finite_number(s_start, min_val=0.0):
                return False
            if not _is_safe_finite_number(s_end, min_val=0.0):
                return False
            try:
                if float(s_end) <= float(s_start):
                    return False
            except (OverflowError, ValueError):
                return False
            if not isinstance(s_text, str):
                return False

        # 7. Optional fields
        if "silence_keep_intervals" in payload:
            intervals = payload["silence_keep_intervals"]
            if not isinstance(intervals, list):
                return False
            for itv in intervals:
                if not isinstance(itv, (tuple, list)) or len(itv) != 2:
                    return False
                if not _is_safe_finite_number(itv[0]):
                    return False
                if not _is_safe_finite_number(itv[1]):
                    return False
                try:
                    if float(itv[1]) < float(itv[0]):
                        return False
                except (OverflowError, ValueError):
                    return False

        if "speedup_segments" in payload:
            if not isinstance(payload["speedup_segments"], list):
                return False

        if "cut_segments" in payload:
            if not isinstance(payload["cut_segments"], list):
                return False

        return True
    except Exception:
        return False


def _reject_nan_constants(val: str) -> None:
    raise ValueError(f"JSON constants not allowed: {val}")


def compute_full_file_sha256(
    file_path: str,
    cancelled_fn: Optional[Callable[[], bool]] = None,
    chunk_size: int = 1024 * 1024
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Tính toán SHA256 toàn vẹn nội dung tệp bằng streaming 1MB từng khối.
    Bảo vệ tính ổn định tệp qua đối chiếu fstat descriptor và path stat trước/sau (TOCTOU).
    Hỗ trợ hủy an toàn qua callback `cancelled_fn`.

    Trả về:
        (sha256_hexdigest, provenance_dict) nếu thành công và tệp ổn định.
        (None, None) nếu bị hủy, tệp đang biến động (unstable), tệp không phải regular file,
        hoặc gặp lỗi I/O. Tuyệt đối không sinh mã băm fallback theo đường dẫn.
    """
    if cancelled_fn and cancelled_fn():
        return None, None

    norm_path = os.path.normpath(os.path.abspath(file_path))
    try:
        stat_before = os.stat(norm_path)
    except Exception as e:
        logging.debug(f"Không thể stat file trước khi hash '{norm_path}': {e}")
        return None, None

    if not stat_mod.S_ISREG(stat_before.st_mode):
        logging.warning(f"Bỏ qua tính full hash cho tệp không phải regular file: '{norm_path}'")
        return None, None

    hasher = hashlib.sha256()
    bytes_read = 0

    try:
        with open(norm_path, "rb") as f:
            fstat_before = os.fstat(f.fileno())
            if (fstat_before.st_size != stat_before.st_size or 
                fstat_before.st_mtime_ns != stat_before.st_mtime_ns or
                getattr(fstat_before, "st_ctime_ns", None) != getattr(stat_before, "st_ctime_ns", None) or
                getattr(fstat_before, "st_dev", None) != getattr(stat_before, "st_dev", None) or
                getattr(fstat_before, "st_ino", None) != getattr(stat_before, "st_ino", None)):
                logging.warning(f"File descriptor không khớp stat ban đầu: '{norm_path}'")
                return None, None

            while True:
                if cancelled_fn and cancelled_fn():
                    logging.info(f"Hủy băm full hash tệp theo yêu cầu: '{norm_path}'")
                    return None, None

                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)

            fstat_after = os.fstat(f.fileno())
    except Exception as e:
        logging.warning(f"Lỗi I/O khi stream full hash '{norm_path}': {e}")
        return None, None

    if cancelled_fn and cancelled_fn():
        return None, None

    try:
        stat_after = os.stat(norm_path)
    except Exception as e:
        logging.warning(f"Không thể stat file sau khi stream '{norm_path}': {e}")
        return None, None

    # Đối chiếu tính ổn định (Pre/Post Stat Consistency trên cả path và descriptor)
    if (bytes_read != stat_before.st_size or
        stat_after.st_size != stat_before.st_size or
        stat_after.st_mtime_ns != stat_before.st_mtime_ns or
        getattr(stat_after, "st_ctime_ns", None) != getattr(stat_before, "st_ctime_ns", None) or
        getattr(stat_after, "st_dev", None) != getattr(stat_before, "st_dev", None) or
        getattr(stat_after, "st_ino", None) != getattr(stat_before, "st_ino", None) or
        fstat_after.st_size != stat_before.st_size or
        fstat_after.st_mtime_ns != stat_before.st_mtime_ns or
        getattr(fstat_after, "st_ctime_ns", None) != getattr(stat_before, "st_ctime_ns", None) or
        getattr(fstat_after, "st_dev", None) != getattr(stat_before, "st_dev", None) or
        getattr(fstat_after, "st_ino", None) != getattr(stat_before, "st_ino", None)):
        logging.warning(f"Tệp bị thay đổi trong khi đang đọc băm: '{norm_path}'")
        return None, None

    digest = hasher.hexdigest().lower()
    provenance = {
        "algorithm_version": "sha256_full_v3",
        "file_size": bytes_read,
        "mtime_ns": stat_before.st_mtime_ns,
        "ctime_ns": getattr(stat_before, "st_ctime_ns", None),
        "dev": getattr(stat_before, "st_dev", None),
        "ino": getattr(stat_before, "st_ino", None),
    }
    return digest, provenance


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
        self._cached_dir_files: Optional[List[str]] = None

    def _get_dir_files(self) -> List[str]:
        if self._cached_dir_files is None:
            if os.path.exists(self.cache_dir):
                self._cached_dir_files = os.listdir(self.cache_dir)
            else:
                self._cached_dir_files = []
        return self._cached_dir_files

    def _build_cache_key(self, file_path: str, model_size: str, language: str) -> str:
        f_hash = compute_file_checksum(file_path)
        lang_str = normalize_language_code(language)
        return f"{f_hash}_{model_size}_{lang_str}".lower()

    def _build_v3_cache_key(self, full_sha256: str, model_size: str, language: str) -> str:
        m_str = (model_size or "small").lower().strip()
        lang_str = normalize_language_code(language)
        return f"v3_{full_sha256.lower()}_{m_str}_{lang_str}"

    def _build_name_key(self, file_path: str, model_size: str, language: str) -> str:
        """Deprecated name key generator. Giữ lại tương thích API cũ nhưng không dùng trong fallback."""
        base_name = os.path.basename(file_path).lower()
        lang_str = normalize_language_code(language)
        name_hash = hashlib.sha256(base_name.encode("utf-8")).hexdigest()[:16]
        return f"name_{name_hash}_{model_size}_{lang_str}".lower()

    def _atomic_write_json(
        self,
        target_path: str,
        data: Dict[str, Any],
        cancelled_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        Ghi dữ liệu JSON nguyên tử qua tệp tạm cùng thư mục và os.replace.
        Dọn dẹp tệp tạm sở hữu nếu có lỗi hoặc hủy.
        Bật allow_nan=False để chặn ghi các giá trị NaN/Inf không hợp lệ.
        """
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("Ghi cache bị hủy trước khi tạo tệp tạm")

        target_dir = os.path.dirname(os.path.abspath(target_path))
        os.makedirs(target_dir, exist_ok=True)

        temp_fd, temp_path = tempfile.mkstemp(
            dir=target_dir,
            prefix=".tmp_cache_",
            suffix=".json"
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())

            if cancelled_fn and cancelled_fn():
                raise InterruptedError("Ghi cache bị hủy trước khi os.replace")

            os.replace(temp_path, target_path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    def get_cached_scan(
        self, 
        file_path: str, 
        model_size: str = "small", 
        language: str = "vi",
        verify_full: bool = False,
        cancelled_fn: Optional[Callable[[], bool]] = None,
        verified_identity: Optional[Tuple[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Kiểm tra và lấy dữ liệu quét đã lưu nếu có (Cache Hit).
        - Nếu verify_full=False (GUI fast path): Tra cứu theo stat/sampled checksum, không đọc toàn media.
        - Nếu verify_full=True (Worker verified path): Tra cứu trực tiếp theo SHA256 toàn vẹn v3;
          xác thực payload, config, và danh sách transcript. Tuyệt đối không fallback theo tên.
        """
        if cancelled_fn and cancelled_fn():
            return None

        if not os.path.exists(self.cache_dir):
            return None

        model_str = (model_size or "small").lower().strip()
        norm_lang = normalize_language_code(language)

        if verify_full:
            full_digest: Optional[str] = None
            provenance: Optional[Dict[str, Any]] = None

            if verified_identity is not None:
                if not (isinstance(verified_identity, tuple) and len(verified_identity) == 2):
                    return None
                full_digest, provenance = verified_identity
                if not is_canonical_sha256_hex(full_digest):
                    return None
                if not isinstance(provenance, dict) or not validate_file_identity(file_path, provenance):
                    logging.warning(f"Tệp '{file_path}' không khớp với verified_identity.")
                    return None
            else:
                full_digest, provenance = compute_full_file_sha256(file_path, cancelled_fn=cancelled_fn)
                if cancelled_fn and cancelled_fn():
                    return None
                if not is_canonical_sha256_hex(full_digest) or not provenance:
                    return None

            v3_key = self._build_v3_cache_key(full_digest, model_str, norm_lang)
            v3_file = os.path.join(self.cache_dir, f"{v3_key}.json")
            if not os.path.exists(v3_file):
                return None

            try:
                with open(v3_file, "r", encoding="utf-8") as f:
                    data = json.load(f, parse_constant=_reject_nan_constants)
            except Exception as e:
                logging.warning(f"Lỗi đọc file cache v3 '{v3_file}': {e}")
                return None

            if not validate_v3_payload(
                data,
                expected_full_sha256=full_digest,
                expected_model=model_str,
                expected_lang=norm_lang,
            ):
                logging.warning(f"Cache v3 '{v3_file}' không hợp lệ hoặc dữ liệu bất thường.")
                return None

            return data

        # --- LUỒNG TRA CỨU NHANH CHO GIAO DIỆN (GUI FAST PATH) ---
        key = self._build_cache_key(file_path, model_str, norm_lang)
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Lỗi đọc file cache '{cache_file}': {e}")

        # Tương thích ngược cache cũ (chỉ khi model khớp)
        f_hash = compute_file_checksum(file_path)
        if f_hash:
            if norm_lang in ["vi", "auto"]:
                auto_key = f"{f_hash}_{model_str}_auto".lower()
                auto_file = os.path.join(self.cache_dir, f"{auto_key}.json")
                if os.path.exists(auto_file):
                    try:
                        with open(auto_file, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception as e:
                        logging.warning(f"Lỗi đọc file cache tương thích ngược '{auto_file}': {e}")

            if norm_lang == "auto":
                prefix = f"{f_hash.lower()}_{model_str}_"
                for fname in self._get_dir_files():
                    if fname.lower().startswith(prefix) and fname.endswith(".json") and not fname.startswith("name_"):
                        try:
                            with open(os.path.join(self.cache_dir, fname), "r", encoding="utf-8") as f:
                                return json.load(f)
                        except Exception as e:
                            logging.warning(f"Lỗi đọc cache prefix '{fname}': {e}")

        # LƯU Ý BẢO MẬT RÀNG BUỘC: Đã loại bỏ hoàn toàn fallback theo tên tệp (name_key)
        # để ngăn chặn triệt để nguy cơ nhận nhầm dữ liệu giữa các tệp trùng tên generic.
        return None

    def save_scan_result(
        self,
        file_path: str,
        scan_data: Dict[str, Any],
        model_size: str = "small",
        language: str = "vi",
        detected_language: Optional[str] = None,
        language_probability: Optional[float] = None,
        verified_identity: Optional[Tuple[str, Dict[str, Any]]] = None,
        cancelled_fn: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Lưu kết quả quét vào file cache JSON nguyên tử.
        - Nếu có verified_identity: Xuất bản Schema v3 với full_content_sha256 và provenance.
          Đồng thời tạo fast alias cho GUI.
        - Nếu không có verified_identity (legacy save): Lưu Schema v2 theo checksum truyền thống.
          Tuyệt đối không lưu bản fallback name_key không an toàn.
        """
        if cancelled_fn and cancelled_fn():
            return ""

        model_str = (model_size or "small").lower().strip()
        norm_lang = normalize_language_code(language)
        os.makedirs(self.cache_dir, exist_ok=True)

        if verified_identity is not None:
            if not (isinstance(verified_identity, tuple) and len(verified_identity) == 2):
                return ""
            full_digest, provenance = verified_identity
            if not is_canonical_sha256_hex(full_digest):
                return ""

            if not isinstance(provenance, dict) or not validate_file_identity(file_path, provenance):
                logging.warning(
                    f"Không lưu cache v3 cho '{file_path}': Tệp nguồn không khớp provenance đã băm."
                )
                return ""

            payload = dict(scan_data)
            payload["cache_schema_version"] = 3
            payload["algorithm_version"] = "sha256_full_v3"
            payload["full_content_sha256"] = full_digest.lower()
            payload["model"] = model_str
            payload["language_requested"] = norm_lang
            payload["detected_language"] = detected_language if detected_language is not None else payload.get("detected_language")
            payload["language_probability"] = language_probability if language_probability is not None else payload.get("language_probability")
            payload["file_size"] = provenance.get("file_size", os.path.getsize(file_path))
            payload["provenance"] = {
                "source_path": os.path.abspath(file_path),
                "mtime_ns": provenance.get("mtime_ns"),
                "ctime_ns": provenance.get("ctime_ns"),
                "dev": provenance.get("dev"),
                "ino": provenance.get("ino"),
            }

            if not validate_v3_payload(
                payload,
                expected_full_sha256=full_digest,
                expected_model=model_str,
                expected_lang=norm_lang,
            ):
                logging.warning(f"Từ chối lưu cache v3 cho '{file_path}': Payload không vượt qua validation.")
                return ""

            v3_key = self._build_v3_cache_key(full_digest, model_str, norm_lang)
            v3_file = os.path.join(self.cache_dir, f"{v3_key}.json")

            try:
                self._atomic_write_json(v3_file, payload, cancelled_fn=cancelled_fn)
                # Tạo fast alias theo compute_file_checksum
                try:
                    f_hash = compute_file_checksum(file_path)
                    if f_hash:
                        legacy_key = f"{f_hash}_{model_str}_{norm_lang}".lower()
                        alias_file = os.path.join(self.cache_dir, f"{legacy_key}.json")
                        self._atomic_write_json(alias_file, payload, cancelled_fn=cancelled_fn)
                except Exception as e:
                    logging.debug(f"Không thể ghi alias fast cache cho '{file_path}': {e}")

                self._cached_dir_files = None
                return v3_file
            except Exception as e:
                logging.warning(f"Lỗi ghi cache v3 nguyên tử cho '{file_path}': {e}")
                return ""

        # --- LEGACY SAVE (KHÔNG CÓ VERIFIED IDENTITY) ---
        f_hash = compute_file_checksum(file_path)
        if not f_hash:
            return ""

        key = f"{f_hash}_{model_str}_{norm_lang}".lower()
        cache_file = os.path.join(self.cache_dir, f"{key}.json")

        payload = dict(scan_data)
        payload["cache_schema_version"] = 2
        payload["model"] = model_str
        payload["language_requested"] = norm_lang
        payload["content_hash"] = f_hash
        try:
            payload["file_size"] = os.path.getsize(file_path)
        except Exception:
            payload["file_size"] = payload.get("file_size", 0)

        if detected_language is not None:
            payload["detected_language"] = detected_language
        elif "detected_language" not in payload:
            payload["detected_language"] = None

        if language_probability is not None:
            payload["language_probability"] = language_probability
        elif "language_probability" not in payload:
            payload["language_probability"] = None

        try:
            self._atomic_write_json(cache_file, payload, cancelled_fn=cancelled_fn)
            self._cached_dir_files = None
            return cache_file
        except Exception as e:
            logging.warning(f"Không thể lưu cache quét legacy cho '{file_path}': {e}")
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
                    except Exception as e:
                        logging.warning(f"Không thể xóa file cache '{fname}': {e}")
        self._cached_dir_files = None
        return count
