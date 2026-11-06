"""
src/core/contracts/asset_dto.py
Module hợp đồng dữ liệu Asset thuần Python (Pure Python DTO & Adapters).

RÀNG BUỘC KIẾN TRÚC:
- Dùng native typing của Python 3.12 (TypedDict, Required, NotRequired). Không dùng typing_extensions.
- TUYỆT ĐỐI KHÔNG import PySide6, QtCore, QtGui, QtWidgets.
- TUYỆT ĐỐI KHÔNG import ResolveGateway, services, UI views hay network/QSettings.
- Không thêm dependency bên ngoài (chỉ dùng Python Standard Library).
"""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Dict, List, Literal, NotRequired, Optional, Required, Set, Tuple, TypedDict


# ==============================================================================
# HẰNG SỐ DANH MỤC & NGUỒN GỐC
# ==============================================================================

CANONICAL_STORAGE_CATEGORIES: Set[str] = {
    "broll",
    "sfx",
    "bgm",
    "overlays",
    "memes",
    "luts",
    "templates",
}

UI_CATEGORIES: Set[str] = {
    "broll",
    "sfx",
    "bgm",
    "overlay",
    "meme",
    "lut",
    "text",
    "trans",
}

# Các ID điều khiển hoặc danh mục UI dành riêng không được dùng làm custom category
RESERVED_CATEGORY_IDS: Set[str] = {
    "all",
    "fav",
    "recent",
    "mine",
    "hub",
    "text",
    "trans",
    "lut",
    "meme",
    "overlay",
    "icon",
}

VALID_SOURCE_KINDS: Set[str] = {
    "managed",
    "linked",
    "builtin",
    "generated",
}

PERSISTED_ALLOWLIST: Set[str] = {
    "schema_version",
    "id",
    "name",
    "source_kind",
    "category",
    "subcategory",
    "tags",
    "template_type",
    "size_bytes",
    "pack_id",
    "pack_name",
    "relative_path",
    "library_path",
    "source_id",
    "external_path",
    "duration_sec",
    "preview_relative_path",
    "thumbnail_relative_path",
}


# ==============================================================================
# ĐỊNH NGHĨA DTO TYPEDDICT (NATIVE PYTHON 3.12)
# ==============================================================================

class PersistedAssetRecord(TypedDict, total=False):
    """Bản ghi bền vững chuẩn được phép lưu vào registry JSON trên đĩa."""
    schema_version: Required[int]
    id: Required[str]
    name: Required[str]
    source_kind: Required[Literal["managed", "linked", "builtin", "generated"]]
    category: Required[str]
    subcategory: NotRequired[str]
    tags: NotRequired[List[str]]
    template_type: NotRequired[Optional[Literal["text", "trans"]]]
    size_bytes: Required[int]
    pack_id: NotRequired[Optional[str]]
    pack_name: NotRequired[str]
    relative_path: NotRequired[str]
    library_path: NotRequired[str]
    source_id: NotRequired[str]
    external_path: NotRequired[str]
    duration_sec: NotRequired[Optional[float]]
    preview_relative_path: NotRequired[Optional[str]]
    thumbnail_relative_path: NotRequired[Optional[str]]


class UIAssetView(TypedDict, total=False):
    """View hiển thị runtime cho các thành phần giao diện (TabAssets, EdgeDock)."""
    id: Required[str]
    name: Required[str]
    tab: Required[str]
    category: Required[str]
    sub: Required[str]
    storage_managed: Required[bool]
    availability: NotRequired[str]
    file_path: NotRequired[str]
    preview_path: NotRequired[str]
    thumbnail_path: NotRequired[str]
    preview_relative_path: NotRequired[Optional[str]]
    thumbnail_relative_path: NotRequired[Optional[str]]
    tags: NotRequired[List[str]]
    template_type: NotRequired[Optional[str]]
    size_bytes: NotRequired[int]
    duration_sec: NotRequired[Optional[float]]
    pack_id: NotRequired[Optional[str]]
    pack_name: NotRequired[str]
    source_kind: NotRequired[str]
    origin_library_id: NotRequired[Optional[str]]
    origin_root: NotRequired[Optional[str]]
    library_path: NotRequired[str]
    external_path: NotRequired[str]
    relative_path: NotRequired[str]
    source_id: NotRequired[str]
    _raw_obj: NotRequired[Any]


# ==============================================================================
# CÁC HÀM KIỂM TRA ĐƯỜNG DẪN LEXICAL THUẦN TÚY (ZERO FILESYSTEM IO)
# ==============================================================================

def is_safe_relative_path(path: str) -> bool:
    """
    Kiểm tra đường dẫn tương đối lexical an toàn:
    - Bắt buộc là chuỗi không rỗng, không chứa ký tự NUL.
    - Không là đường dẫn tuyệt đối (POSIX /, Windows C:/, UNC //).
    - Không là drive-relative (C:foo).
    - Không chứa path traversal (..).
    - Không là '.' hoặc './' hoặc chứa segment rỗng (//).
    """
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return False
    norm = path.replace("\\", "/").strip()
    if norm.startswith("/") or norm.startswith("//") or bool(re.match(r"^[a-zA-Z]:", norm)):
        return False
    parts = norm.split("/")
    if any(p == ".." or p == "." or not p for p in parts):
        return False
    return True


def is_absolute_lexical_path(path: str) -> bool:
    """
    Kiểm tra đường dẫn tuyệt đối lexical không phụ thuộc OS máy chủ:
    - Tuyệt đối POSIX: bắt đầu bằng '/'
    - Tuyệt đối Windows UNC: bắt đầu bằng '\\\\' hoặc '//'
    - Tuyệt đối Windows Drive: bắt đầu bằng 'C:\\' hoặc 'C:/' (không phải C:relative)
    """
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return False
    p = path.strip()
    if p.startswith("/"):
        return True
    if p.startswith(("\\\\", "//")):
        return True
    if len(p) >= 3 and p[0].isalpha() and p[1] == ":" and p[2] in ("\\", "/"):
        return True
    return False


def is_valid_custom_category_id(cat_id: str) -> bool:
    """
    Kiểm tra ID danh mục tùy biến hợp lệ:
    - Bắt đầu bằng chữ thường, theo sau là chữ thường, số hoặc dấu gạch dưới.
    - Độ dài từ 1 đến 48 ký tự (chuẩn StorageManager.add_category).
    - Không được nằm trong RESERVED_CATEGORY_IDS.
    """
    if not isinstance(cat_id, str):
        return False
    if cat_id in RESERVED_CATEGORY_IDS:
        return False
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", cat_id))


# ==============================================================================
# VALIDATION ENGINE (STRICT CHO BẢN GHI MỚI)
# ==============================================================================

def validate_asset_dict(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Xác thực nghiêm ngặt một bản ghi Asset mới trước khi ghi vào kho bền vững.
    Luôn trả về: (True, []) nếu hợp lệ; (False, [danh sách lỗi tiếng Việt]) nếu không đạt.
    TUYỆT ĐỐI KHÔNG ném Exception ra ngoài khi input sai kiểu dữ liệu.
    """
    errors: List[str] = []

    if not isinstance(record, dict):
        return False, ["Bản ghi Asset phải là một dictionary."]

    # 1. Chặn runtime reference không được serialize
    if "_raw_obj" in record:
        errors.append("Bản ghi chứa runtime reference không hợp lệ: _raw_obj.")

    # 2. Schema version
    version = record.get("schema_version")
    if type(version) is not int or version != 1:
        errors.append(f"schema_version không được hỗ trợ: {version} (chỉ hỗ trợ phiên bản 1).")

    # 3. ID và Name
    asset_id = record.get("id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        errors.append("Trường 'id' bắt buộc phải là chuỗi không rỗng.")

    name = record.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Trường 'name' bắt buộc phải là chuỗi không rỗng.")

    # 4. Source Kind và các ràng buộc liên đới
    source_kind = record.get("source_kind")
    if not isinstance(source_kind, str) or source_kind not in VALID_SOURCE_KINDS:
        errors.append(
            f"Trường 'source_kind' không hợp lệ: {source_kind!r}. "
            f"Chỉ chấp nhận: {sorted(VALID_SOURCE_KINDS)}."
        )
    else:
        if source_kind == "managed":
            pack_id = record.get("pack_id")
            if not isinstance(pack_id, str) or not pack_id.strip():
                errors.append("Managed asset bắt buộc phải có 'pack_id' không rỗng.")

            rel_path = record.get("relative_path")
            if not isinstance(rel_path, str) or not is_safe_relative_path(rel_path):
                errors.append(f"Managed asset có 'relative_path' không an toàn hoặc rỗng: {rel_path!r}.")

            lib_path = record.get("library_path")
            if not isinstance(lib_path, str) or not is_safe_relative_path(lib_path):
                errors.append(f"Managed asset có 'library_path' không an toàn hoặc rỗng: {lib_path!r}.")

        elif source_kind == "linked":
            source_id = record.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                errors.append("Linked asset bắt buộc phải có 'source_id' không rỗng.")

            ext_path = record.get("external_path")
            if not isinstance(ext_path, str) or not is_absolute_lexical_path(ext_path):
                errors.append(f"Linked asset có 'external_path' phải là đường dẫn tuyệt đối hợp lệ: {ext_path!r}.")

    # 5. Category & Custom Category
    category = record.get("category")
    if not isinstance(category, str):
        errors.append(f"Trường 'category' phải là chuỗi, nhận được: {type(category).__name__}.")
    elif category in CANONICAL_STORAGE_CATEGORIES:
        pass
    elif is_valid_custom_category_id(category):
        pass
    else:
        if category in RESERVED_CATEGORY_IDS:
            errors.append(f"Trường 'category' không được dùng định danh dành riêng (reserved ID): '{category}'.")
        else:
            errors.append(
                f"Trường 'category' không hợp lệ: '{category}'. Phải thuộc danh mục chuẩn "
                f"({sorted(CANONICAL_STORAGE_CATEGORIES)}) hoặc ID tùy biến hợp lệ [a-z][a-z0-9_]{{0,47}}."
            )

    # 6. Template Type
    if category == "templates":
        tt = record.get("template_type")
        if "template_type" not in record or tt is None or not isinstance(tt, str) or tt not in {"text", "trans"}:
            errors.append(
                f"Tài nguyên 'templates' bắt buộc phải có 'template_type' ('text' hoặc 'trans'), "
                f"nhận được: {tt!r}."
            )
    else:
        if "template_type" in record and record.get("template_type") is not None:
            errors.append(f"Chỉ danh mục 'templates' mới có 'template_type', category '{category}' không được có.")

    # 7. Size Bytes
    size_bytes = record.get("size_bytes")
    if type(size_bytes) is not int or size_bytes < 0:
        errors.append(f"Trường 'size_bytes' phải là số nguyên không âm (không phải bool), nhận được: {size_bytes!r}.")

    # 8. Duration Sec (Bản ghi mới: None hoặc float > 0 hữu hạn; 0/-1/âm/NaN/Inf là lỗi)
    if "duration_sec" in record and record["duration_sec"] is not None:
        dur = record["duration_sec"]
        if type(dur) is bool or not isinstance(dur, (int, float)):
            errors.append(f"Trường 'duration_sec' phải là số thực hoặc None, nhận được kiểu {type(dur).__name__}.")
        else:
            try:
                dur_f = float(dur)
                if not math.isfinite(dur_f) or dur_f <= 0.0:
                    errors.append("Trường 'duration_sec' của bản ghi mới phải là số thực dương hữu hạn (> 0).")
            except OverflowError:
                errors.append("Trường 'duration_sec' chứa giá trị số quá lớn làm tràn số thực (overflow).")
            except (ValueError, TypeError):
                errors.append("Trường 'duration_sec' chứa giá trị số không hợp lệ.")

    # 9. Tags (NotRequired[List[str]])
    if "tags" in record:
        tags = record.get("tags")
        if not isinstance(tags, list):
            errors.append(f"Trường 'tags' phải là danh sách (List[str]), nhận được: {type(tags).__name__}.")
        else:
            for idx, tag in enumerate(tags):
                if not isinstance(tag, str):
                    errors.append(f"Phần tử tags[{idx}] không phải là chuỗi: {type(tag).__name__}.")
                    break

    # 10. Kiểm tra tất cả các trường tùy chọn khác trong PERSISTED_ALLOWLIST theo annotation kiểu
    if "pack_id" in record:
        p_id = record.get("pack_id")
        if source_kind != "managed":
            if p_id is not None and not isinstance(p_id, str):
                errors.append(f"Trường 'pack_id' phải là chuỗi hoặc None, nhận được: {type(p_id).__name__}.")

    if "subcategory" in record:
        subcat = record.get("subcategory")
        if not isinstance(subcat, str):
            errors.append(f"Trường 'subcategory' phải là chuỗi, nhận được: {type(subcat).__name__}.")

    if "pack_name" in record:
        pn = record.get("pack_name")
        if not isinstance(pn, str):
            errors.append(f"Trường 'pack_name' phải là chuỗi, nhận được: {type(pn).__name__}.")

    if "source_id" in record:
        sid = record.get("source_id")
        if source_kind != "linked":
            if not isinstance(sid, str):
                errors.append(f"Trường 'source_id' phải là chuỗi, nhận được: {type(sid).__name__}.")

    if "external_path" in record:
        ep = record.get("external_path")
        if source_kind != "linked":
            if not isinstance(ep, str) or not is_absolute_lexical_path(ep):
                errors.append(f"Trường 'external_path' phải là đường dẫn tuyệt đối hợp lệ: {ep!r}.")

    if "relative_path" in record:
        rp = record.get("relative_path")
        if source_kind != "managed":
            if not isinstance(rp, str) or not is_safe_relative_path(rp):
                errors.append(f"Trường 'relative_path' phải là đường dẫn tương đối an toàn: {rp!r}.")

    if "library_path" in record:
        lp = record.get("library_path")
        if source_kind != "managed":
            if not isinstance(lp, str) or not is_safe_relative_path(lp):
                errors.append(f"Trường 'library_path' phải là đường dẫn tương đối an toàn: {lp!r}.")

    if "preview_relative_path" in record:
        prp = record.get("preview_relative_path")
        if prp is not None:
            if not isinstance(prp, str) or not is_safe_relative_path(prp):
                errors.append(f"Trường 'preview_relative_path' phải là đường dẫn tương đối an toàn hoặc None, nhận được: {prp!r}.")

    if "thumbnail_relative_path" in record:
        trp = record.get("thumbnail_relative_path")
        if trp is not None:
            if not isinstance(trp, str) or not is_safe_relative_path(trp):
                errors.append(f"Trường 'thumbnail_relative_path' phải là đường dẫn tương đối an toàn hoặc None, nhận được: {trp!r}.")

    return len(errors) == 0, errors


# ==============================================================================
# ADAPTER TUẦN TỰ HÓA PERSISTENCE (EXPLICIT ALLOWLIST & STRIPPING)
# ==============================================================================

def to_persisted_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chuẩn hóa và lọc dữ liệu Asset sang bản ghi JSON an toàn để ghi đĩa:
    - Loại bỏ triệt để các tham chiếu bộ nhớ runtime (_raw_obj, file_path, availability...) trước khi xử lý.
    - Không gọi deepcopy trên _raw_obj để tránh lỗi đối tượng không thể copy.
    - Chuẩn hóa alias của template_type (title -> text, transition -> trans).
    - KHÔNG tự tiện biến duration 0 hoặc -1 thành None cho bản ghi mới (0/-1 bị từ chối).
    - Đảm bảo đầu ra có thể chạy json.dumps(output, allow_nan=False) thành công.
    - Từ chối bản ghi có schema_version mới hơn (> 1) hoặc vi phạm hợp đồng dữ liệu.
    """
    if not isinstance(record, dict):
        raise ValueError("Đầu vào to_persisted_record phải là dictionary.")

    # 1. Kiểm tra schema_version trước khi lọc
    version = record.get("schema_version", 1)
    if type(version) is int and version > 1:
        raise ValueError(
            f"Không thể ghi đĩa bản ghi có schema_version mới hơn: {version} "
            f"(hệ thống hiện tại hỗ trợ: 1)."
        )

    # 2. Lọc qua danh sách trắng (Explicit Allowlist), bóc tách runtime refs trước deepcopy
    sanitized: Dict[str, Any] = {}
    for k in PERSISTED_ALLOWLIST:
        if k in record:
            val = record[k]
            try:
                sanitized[k] = copy.deepcopy(val)
            except Exception as exc:
                raise ValueError(f"Trường '{k}' chứa đối tượng không thể sao chép hoặc tuần tự hóa: {exc}")

    sanitized.setdefault("schema_version", 1)

    # 3. Chuẩn hóa alias template_type cho chuẩn V1
    if sanitized.get("category") == "templates":
        tt = sanitized.get("template_type")
        if tt == "title":
            sanitized["template_type"] = "text"
        elif tt == "transition":
            sanitized["template_type"] = "trans"

    # 4. Xác thực nghiêm ngặt bản ghi đã lọc
    ok, errors = validate_asset_dict(sanitized)
    if not ok:
        raise ValueError(f"Bản ghi không hợp lệ để lưu trữ: {'; '.join(errors)}")

    # 5. Kiểm tra an toàn tuần tự hóa JSON thực tế
    try:
        json.dumps(sanitized, allow_nan=False)
    except Exception as exc:
        raise ValueError(f"Bản ghi không thể tuần tự hóa sang JSON hợp lệ: {exc}")

    return sanitized


# ==============================================================================
# ADAPTER CHO CONSUMER UI (UI COMPATIBILITY ADAPTER)
# ==============================================================================

def to_ui_asset(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter chuẩn hóa Asset cho tầng hiển thị UI (TabAssets, EdgeDock):
    - Ánh xạ danh mục kho sang tab UI (overlays -> overlay, memes -> meme, luts -> lut).
    - Phân tách templates thành text và trans.
    - Bảo toàn 100% các nhãn và tag tiếng Việt có dấu.
    - Ưu tiên subcategory hợp lệ, fallback sang sub đang có, sau cùng mới về 'Tất cả'.
    - Gắn cờ storage_managed = True.
    - Chuẩn hóa duration legacy: 0, -1, NaN, Infinity hoặc tràn số đều đưa về None an toàn.
    - Xử lý nhãn Offline: chỉ gắn hậu tố ' · Offline' một lần duy nhất, tránh lặp.
    - Bảo toàn _raw_obj bằng tham chiếu (identity), không deepcopy toàn bộ record gây lỗi runtime handle.
    """
    if not isinstance(record, dict):
        return {}

    # Sao chép nông dict để bảo toàn tham chiếu _raw_obj gốc
    item: Dict[str, Any] = dict(record)
    if "tags" in item and isinstance(item["tags"], list):
        item["tags"] = list(item["tags"])

    category = str(item.get("category") or "broll")

    # 1. Ánh xạ category -> tab UI
    if category == "overlays":
        tab = "overlay"
    elif category == "memes":
        tab = "meme"
    elif category == "luts":
        tab = "lut"
    elif category == "templates":
        tt = str(item.get("template_type") or "text").lower()
        if tt in ("trans", "transition"):
            tab = "trans"
        else:
            tab = "text"
    else:
        tab = category

    # 2. Cập nhật các trường bắt buộc cho UI consumer
    item["tab"] = tab
    item["category"] = tab

    # 3. Bảo toàn sub: ưu tiên subcategory hợp lệ -> fallback sub hiện có -> default 'Tất cả'
    sub_val = item.get("subcategory")
    if not isinstance(sub_val, str) or not sub_val.strip():
        sub_val = item.get("sub")
    if not isinstance(sub_val, str) or not sub_val.strip():
        sub_val = "Tất cả"
    item["sub"] = sub_val

    item["storage_managed"] = True

    # 4. Chuẩn hóa thời lượng an toàn cho UI (legacy tolerance)
    dur = item.get("duration_sec", item.get("duration"))
    if dur is not None and type(dur) not in (bool,):
        try:
            if type(dur) is int:
                item["duration_sec"] = float(dur) if dur > 0 else None
            else:
                dur_f = float(dur)
                item["duration_sec"] = dur_f if (math.isfinite(dur_f) and dur_f > 0.0) else None
        except (ValueError, TypeError, OverflowError):
            item["duration_sec"] = None
    else:
        item["duration_sec"] = None

    # 5. Xử lý nhãn Offline (không để lặp hậu tố)
    if item.get("availability") == "offline":
        name = str(item.get("name") or "")
        if not name.endswith(" · Offline"):
            item["name"] = f"{name} · Offline"

    # 6. Bảo toàn các trường nguồn gốc runtime (G04-04C2-P)
    # Nếu origin thiếu, không tự bịa (giữ None/absent rõ ràng, không chuỗi "None" hay UUID giả)
    if "origin_library_id" in item:
        item["origin_library_id"] = str(item["origin_library_id"]) if item["origin_library_id"] is not None else None
    if "origin_root" in item:
        item["origin_root"] = str(item["origin_root"]) if item["origin_root"] is not None else None

    # 7. Bảo toàn các trường companion đường dẫn tương đối và đường dẫn tuyệt đối UI
    if "preview_relative_path" in item:
        item["preview_relative_path"] = str(item["preview_relative_path"]) if item["preview_relative_path"] is not None else None
    if "thumbnail_relative_path" in item:
        item["thumbnail_relative_path"] = str(item["thumbnail_relative_path"]) if item["thumbnail_relative_path"] is not None else None
    if "preview_path" in item and item["preview_path"] is not None:
        item["preview_path"] = str(item["preview_path"])
    if "thumbnail_path" in item and item["thumbnail_path"] is not None:
        item["thumbnail_path"] = str(item["thumbnail_path"])

    return item
