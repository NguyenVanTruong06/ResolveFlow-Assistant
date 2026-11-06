"""
src/core/offline_asset_matcher.py
=============================================================================
Động cơ Tra Cứu & Khớp Tài Nguyên Ngoại Tuyến Thuần Túy (Pure Offline Asset Matcher).

RÀNG BUỘC KỸ THUẬT:
- Thuần Python 3.12 Standard Library (dataclasses, re, unicodedata, posixpath).
- TUYỆT ĐỐI KHÔNG import PySide6 / UI / ResolveGateway / Network / Filesystem I/O.
- Không khởi tạo StorageManager, SearchIndex, ActionGuard.
- Bất biến (immutable), an toàn vòng lặp, tất định 100% (deterministic).
=============================================================================
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import posixpath
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

from src.core.contracts.asset_dto import (
    CANONICAL_STORAGE_CATEGORIES,
    PERSISTED_ALLOWLIST,
    is_absolute_lexical_path,
    is_safe_relative_path,
    is_valid_custom_category_id,
    validate_asset_dict,
)
from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
    validate_match_request,
)


UI_CATEGORY_ALIASES: Dict[str, str] = {
    "overlay": "overlays",
    "meme": "memes",
    "lut": "luts",
    "text": "templates",
    "trans": "templates",
}

KNOWN_EXTENSIONS: Tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".aac", ".flac",
    ".cube", ".setting", ".png", ".jpg", ".jpeg"
)

MAX_WARNING_ENTRIES = 64
MAX_WARNING_BYTES = 16384
TRUNCATION_MARKER = "... [Đã lược bớt các cảnh báo chẩn đoán vượt quá giới hạn an toàn]"


def _is_utf8_valid(val: Any) -> bool:
    """Kiểm tra chuỗi ký tự có thể mã hóa UTF-8 hợp lệ không (loại bỏ lone surrogates)."""
    if not isinstance(val, str):
        return False
    try:
        val.encode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeError):
        return False


def _sanitize_diagnostic_text(text: str) -> str:
    """Chuẩn hóa và khử mã các ký tự lone surrogate trong chuỗi cảnh báo để đảm bảo 100% hợp lệ UTF-8."""
    if not isinstance(text, str):
        return ""
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _strip_accents(text: str) -> str:
    """Loại bỏ dấu thanh tiếng Việt (NFKD), chuyển 'đ/Đ' -> 'd', viết thường và strip."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.replace("đ", "d").replace("Đ", "d").casefold().strip()


def _tokenize(text: str) -> List[str]:
    """Tách từ khóa thuần chữ số theo chuẩn hóa không dấu."""
    norm = _strip_accents(text)
    if not norm:
        return []
    return [t for t in re.split(r"[^a-z0-9]+", norm) if t]


def _stem(text: str) -> str:
    """Loại bỏ đuôi tệp mở rộng phổ biến nếu có."""
    norm = _strip_accents(text)
    for ext in KNOWN_EXTENSIONS:
        if norm.endswith(ext):
            return norm[:-len(ext)]
    return norm


def _norm_posix(path_str: str) -> str:
    """Chuẩn hóa đường dẫn tương đối dạng posix thống nhất."""
    return posixpath.normpath(path_str.replace("\\", "/")).strip("/")


def _resolve_category(raw_cat: str, explicit_template_type: Optional[str]) -> Tuple[str, Optional[str]]:
    """Phân giải category của request (chấp nhận canonical 7, custom ID, hoặc UI aliases)."""
    if not isinstance(raw_cat, str) or not raw_cat.strip() or not _is_utf8_valid(raw_cat):
        raise ValueError(f"Category không hợp lệ hoặc chứa ký tự không hỗ trợ: {raw_cat!r}")
    cat = raw_cat.strip().lower()

    derived_template_type = None
    if cat == "text":
        resolved_cat = "templates"
        derived_template_type = "text"
    elif cat == "trans":
        resolved_cat = "templates"
        derived_template_type = "trans"
    elif cat in UI_CATEGORY_ALIASES:
        resolved_cat = UI_CATEGORY_ALIASES[cat]
    elif cat in CANONICAL_STORAGE_CATEGORIES:
        resolved_cat = cat
    elif is_valid_custom_category_id(cat):
        resolved_cat = cat
    else:
        raise ValueError(f"Category không hợp lệ hoặc không được hỗ trợ: {raw_cat!r}")

    final_template_type = explicit_template_type
    if derived_template_type is not None:
        if explicit_template_type is not None and explicit_template_type != derived_template_type:
            raise ValueError(
                f"Xung đột template_type giữa category alias '{raw_cat}' "
                f"và template_type='{explicit_template_type}'"
            )
        final_template_type = derived_template_type

    if resolved_cat != "templates" and final_template_type is not None:
        raise ValueError(f"template_type chỉ được áp dụng cho danh mục 'templates' (hiện tại: '{resolved_cat}')")

    return resolved_cat, final_template_type


def _has_valid_master_reference(row: Dict[str, Any]) -> bool:
    """Kiểm tra bản ghi có metadata tham chiếu master file hợp lệ hay chỉ là preview/thumbnail companion."""
    source_kind = row.get("source_kind")

    if source_kind == "managed":
        lib_p = row.get("library_path")
        rel_p = row.get("relative_path")
        pack_id = row.get("pack_id")
        if not isinstance(pack_id, str) or not pack_id.strip() or not _is_utf8_valid(pack_id):
            return False
        if not isinstance(lib_p, str) or not is_safe_relative_path(lib_p):
            return False
        if not isinstance(rel_p, str) or not is_safe_relative_path(rel_p):
            return False

        # So sánh cùng hệ tọa độ pack-relative: relative_path vs preview_relative_path / thumbnail_relative_path
        norm_rel = _norm_posix(rel_p)
        prev_p = row.get("preview_relative_path")
        if isinstance(prev_p, str) and prev_p.strip():
            if norm_rel == _norm_posix(prev_p):
                return False
        thumb_p = row.get("thumbnail_relative_path")
        if isinstance(thumb_p, str) and thumb_p.strip():
            if norm_rel == _norm_posix(thumb_p):
                return False

        # So sánh library_path trực tiếp với companion nếu library_path trỏ thẳng vào companion
        norm_lib = _norm_posix(lib_p)
        if isinstance(prev_p, str) and prev_p.strip() and norm_lib == _norm_posix(prev_p):
            return False
        if isinstance(thumb_p, str) and thumb_p.strip() and norm_lib == _norm_posix(thumb_p):
            return False

        return True

    elif source_kind == "linked":
        ext_p = row.get("external_path")
        src_id = row.get("source_id")
        if not isinstance(src_id, str) or not src_id.strip() or not _is_utf8_valid(src_id):
            return False
        if not isinstance(ext_p, str) or not is_absolute_lexical_path(ext_p):
            return False

        norm_ext = posixpath.normpath(ext_p.replace("\\", "/"))
        # Loại trừ nếu external_path trùng với known runtime companion paths
        for comp_k in ("preview_path", "preview_file_path", "preview_external_path", "thumbnail_path"):
            comp_val = row.get(comp_k)
            if isinstance(comp_val, str) and is_absolute_lexical_path(comp_val):
                if norm_ext == posixpath.normpath(comp_val.replace("\\", "/")):
                    return False
        return True

    elif source_kind in ("builtin", "generated"):
        # Bắt buộc phải có file_path hoặc relative_path hợp lệ; library_path đơn thuần KHÔNG đủ điều kiện master
        rel_p = row.get("relative_path")
        file_p = row.get("file_path")
        has_valid_rel = isinstance(rel_p, str) and is_safe_relative_path(rel_p)
        has_valid_file = isinstance(file_p, str) and is_absolute_lexical_path(file_p)

        if not has_valid_rel and not has_valid_file:
            return False

        # Nếu có relative_path, kiểm tra xem có trùng companion pack/library relative không
        if has_valid_rel:
            norm_rel = _norm_posix(rel_p)
            prev_p = row.get("preview_relative_path")
            if isinstance(prev_p, str) and prev_p.strip():
                if norm_rel == _norm_posix(prev_p):
                    return False
            thumb_p = row.get("thumbnail_relative_path")
            if isinstance(thumb_p, str) and thumb_p.strip():
                if norm_rel == _norm_posix(thumb_p):
                    return False

        # Nếu có file_path, kiểm tra xem có trùng runtime absolute companion không
        if has_valid_file:
            norm_file = posixpath.normpath(file_p.replace("\\", "/"))
            for comp_k in ("preview_path", "preview_file_path", "thumbnail_path", "thumbnail_file_path"):
                comp_val = row.get(comp_k)
                if isinstance(comp_val, str) and is_absolute_lexical_path(comp_val):
                    if norm_file == posixpath.normpath(comp_val.replace("\\", "/")):
                        return False

        return True

    return False


@dataclass(frozen=True)
class _CleanAsset:
    """Đại diện nội bộ đã được xác thực và bất biến của một bản ghi catalog."""
    asset_id: str
    name: str
    category: str
    subcategory: str
    source_kind: str
    availability: str
    duration_sec: Optional[float]
    pack_id: Optional[str]
    pack_name: str
    source_id: Optional[str]
    origin_library_id: Optional[str]
    template_type: Optional[str]
    tags: Tuple[str, ...]
    normalized_name: str
    normalized_stem: str
    name_tokens: Set[str]
    tag_tokens: Set[str]
    subcategory_tokens: Set[str]


class OfflineAssetMatcher:
    """Động cơ so khớp tài nguyên ngoại tuyến thuần Python."""

    def __init__(
        self,
        catalog: Sequence[Dict[str, Any]],
        *,
        alias_groups: Optional[Mapping[str, Sequence[str]]] = None,
    ):
        if not isinstance(catalog, (list, tuple)):
            raise ValueError("catalog phải là một sequence hữu hạn (list hoặc tuple).")
        if len(catalog) > 100_000:
            raise ValueError("Kích thước catalog vượt quá giới hạn tối đa 100000 bản ghi.")

        self._raw_catalog_count = len(catalog)

        # 1. Thẩm định và sao chép bất biến alias_groups (Chống collision và ép UTF-8)
        self._alias_groups: Dict[str, Tuple[str, ...]] = {}
        if alias_groups is not None:
            if not isinstance(alias_groups, Mapping):
                raise ValueError("alias_groups phải là một Mapping (dict).")
            if len(alias_groups) > 64:
                raise ValueError("alias_groups không được vượt quá 64 nhóm.")

            for g_key, terms in alias_groups.items():
                if not isinstance(g_key, str) or not g_key.strip() or not _is_utf8_valid(g_key):
                    raise ValueError("Key của alias_groups phải là chuỗi UTF-8 không rỗng.")
                norm_key = _strip_accents(g_key)
                if norm_key in self._alias_groups:
                    raise ValueError(f"Xung đột alias group key sau chuẩn hóa: '{g_key}'")

                if not isinstance(terms, (list, tuple)):
                    raise ValueError("Terms trong alias_groups phải là danh sách hoặc tuple.")
                if len(terms) > 32:
                    raise ValueError(f"Nhóm '{g_key}' trong alias_groups không được vượt quá 32 từ.")

                norm_terms = []
                for t in terms:
                    if not isinstance(t, str) or not t.strip() or not _is_utf8_valid(t):
                        raise ValueError("Từ trong alias_groups phải là chuỗi UTF-8 không rỗng.")
                    norm_terms.append(_strip_accents(t))
                self._alias_groups[norm_key] = tuple(norm_terms)

        # 2. Quét trước để phát hiện ID trùng lặp (Duplicate ID Quarantine)
        id_counts: Dict[str, int] = {}
        for item in catalog:
            if isinstance(item, dict):
                a_id = item.get("id")
                if isinstance(a_id, str) and a_id.strip() and _is_utf8_valid(a_id):
                    id_counts[a_id] = id_counts.get(a_id, 0) + 1

        duplicate_ids: Set[str] = {a_id for a_id, count in id_counts.items() if count > 1}

        # 3. Phân loại và tiếp nhận bản ghi sạch (Quarantine bad rows qua persisted view)
        self._valid_records: List[_CleanAsset] = []
        raw_warnings: List[str] = []
        self._rejected_count: int = 0

        for item in catalog:
            if not isinstance(item, dict):
                self._rejected_count += 1
                raw_warnings.append("Bản ghi không phải là dictionary hợp lệ.")
                continue

            a_id_raw = item.get("id")
            if not isinstance(a_id_raw, str) or not a_id_raw.strip() or not _is_utf8_valid(a_id_raw):
                self._rejected_count += 1
                sanitized_id = _sanitize_diagnostic_text(str(a_id_raw))[:64]
                raw_warnings.append(
                    _sanitize_diagnostic_text(f"Asset '{sanitized_id}': id không hợp lệ hoặc chứa ký tự UTF-8 không hợp lệ (lone surrogate).")
                )
                continue

            a_id = a_id_raw
            diag_prefix = f"Asset '{_sanitize_diagnostic_text(a_id)[:64]}'"

            # Trích xuất persisted view theo PERSISTED_ALLOWLIST (bỏ qua _raw_obj và runtime handles mà không gọi handle)
            persisted_view = {k: item[k] for k in PERSISTED_ALLOWLIST if k in item}
            is_valid, errs = validate_asset_dict(persisted_view)
            if not is_valid:
                self._rejected_count += 1
                err_summary = "; ".join(_sanitize_diagnostic_text(e) for e in errs[:3])
                raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: schema không hợp lệ ({err_summary})"))
                continue

            if a_id in duplicate_ids:
                self._rejected_count += 1
                raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: trùng lặp asset_id. Toàn bộ nhóm trùng bị cách ly an toàn."))
                continue

            # Kiểm tra availability enum 4 giá trị chuẩn: online, offline, missing, staging
            avail = item.get("availability")
            if not isinstance(avail, str) or avail not in ("online", "offline", "missing", "staging"):
                self._rejected_count += 1
                sanitized_avail = _sanitize_diagnostic_text(repr(avail))
                raw_warnings.append(
                    _sanitize_diagnostic_text(
                        f"{diag_prefix}: availability không hợp lệ ({sanitized_avail}). Chỉ chấp nhận 'online', 'offline', 'missing', 'staging'."
                    )
                )
                continue

            # Thẩm định riêng origin_library_id nếu có
            orig_lib = item.get("origin_library_id")
            clean_origin_library_id: Optional[str] = None
            if orig_lib is not None:
                if type(orig_lib) is bool or not isinstance(orig_lib, str) or not orig_lib.strip() or not _is_utf8_valid(orig_lib):
                    self._rejected_count += 1
                    raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: origin_library_id phải là chuỗi UTF-8 không rỗng hoặc None."))
                    continue
                clean_origin_library_id = orig_lib

            # Thẩm định source_id nếu có
            src_id_val = item.get("source_id")
            if src_id_val is not None:
                if not isinstance(src_id_val, str) or not src_id_val.strip() or not _is_utf8_valid(src_id_val):
                    self._rejected_count += 1
                    raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: source_id không hợp lệ hoặc chứa ký tự UTF-8 không hợp lệ."))
                    continue

            # Thẩm định category UTF-8
            cat_val = item.get("category")
            if not isinstance(cat_val, str) or not _is_utf8_valid(cat_val):
                self._rejected_count += 1
                raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: category chứa ký tự UTF-8 không hợp lệ."))
                continue

            # Thẩm định UTF-8 cho các đường dẫn khi có mặt
            path_corrupted = False
            for p_k in ("relative_path", "library_path", "external_path", "file_path", "preview_relative_path", "thumbnail_relative_path"):
                p_v = item.get(p_k)
                if p_v is not None:
                    if not isinstance(p_v, str) or not _is_utf8_valid(p_v):
                        self._rejected_count += 1
                        raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: {p_k} chứa ký tự UTF-8 không hợp lệ."))
                        path_corrupted = True
                        break
            if path_corrupted:
                continue

            # Thẩm định master reference an toàn cùng hệ tọa độ
            if not _has_valid_master_reference(item):
                self._rejected_count += 1
                raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: không có master reference hợp lệ hoặc là preview/thumbnail companion."))
                continue

            # Thẩm định UTF-8 cho các trường văn bản tiêu thụ
            name_str = item.get("name")
            if not isinstance(name_str, str) or not _is_utf8_valid(name_str):
                self._rejected_count += 1
                raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: name chứa ký tự UTF-8 không hợp lệ."))
                continue

            sub_raw = item.get("subcategory")
            sub_str = ""
            if sub_raw is not None:
                if not isinstance(sub_raw, str) or not _is_utf8_valid(sub_raw):
                    self._rejected_count += 1
                    raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: subcategory chứa ký tự UTF-8 không hợp lệ."))
                    continue
                sub_str = sub_raw

            pack_name_raw = item.get("pack_name")
            pack_name_str = ""
            if pack_name_raw is not None:
                if not isinstance(pack_name_raw, str) or not _is_utf8_valid(pack_name_raw):
                    self._rejected_count += 1
                    raw_warnings.append(_sanitize_diagnostic_text(f"{diag_prefix}: pack_name chứa ký tự UTF-8 không hợp lệ."))
                    continue
                pack_name_str = pack_name_raw

            # Xử lý tags
            tags_list = item.get("tags") or []
            tag_tokens: Set[str] = set()
            clean_tags: List[str] = []
            tags_invalid = False
            if isinstance(tags_list, list):
                for tg in tags_list:
                    if not isinstance(tg, str) or not _is_utf8_valid(tg):
                        tags_invalid = True
                        break
                    clean_tags.append(tg)
                    tag_tokens.update(_tokenize(tg))
            if tags_invalid:
                self._rejected_count += 1
                raw_warnings.append(f"{diag_prefix}: tags chứa phần tử không phải chuỗi UTF-8 hợp lệ.")
                continue

            # Xử lý an toàn trường duration_sec
            raw_dur = item.get("duration_sec")
            dur_sec: Optional[float] = None
            if raw_dur is not None and not isinstance(raw_dur, bool) and isinstance(raw_dur, (int, float)):
                try:
                    dur_sec = float(raw_dur)
                except OverflowError:
                    dur_sec = None

            norm_name = _strip_accents(name_str)
            norm_stem = _stem(name_str)
            name_tokens = set(_tokenize(name_str))
            sub_tokens = set(_tokenize(sub_str))

            clean_rec = _CleanAsset(
                asset_id=a_id,
                name=name_str,
                category=str(item.get("category", "")),
                subcategory=sub_str,
                source_kind=str(item.get("source_kind", "")),
                availability=avail,
                duration_sec=dur_sec,
                pack_id=item.get("pack_id"),
                pack_name=pack_name_str,
                source_id=item.get("source_id"),
                origin_library_id=clean_origin_library_id,
                template_type=item.get("template_type"),
                tags=tuple(clean_tags),
                normalized_name=norm_name,
                normalized_stem=norm_stem,
                name_tokens=name_tokens,
                tag_tokens=tag_tokens,
                subcategory_tokens=sub_tokens,
            )
            self._valid_records.append(clean_rec)

        # 4. Giới hạn ngân sách cảnh báo chẩn đoán tất định (Group F: Bounded Diagnostics)
        # Sắp xếp các cảnh báo theo thứ tự bảng chữ cái để độc lập hoàn toàn với thứ tự duyệt catalog
        raw_warnings.sort()
        marker_bytes = len(TRUNCATION_MARKER.encode("utf-8"))
        final_warnings: List[str] = []
        current_bytes = 0
        is_truncated = False

        for w in raw_warnings:
            # Cắt ngắn thông điệp cá nhân nếu quá dài và khử triệt để lone surrogates
            sanitized = _sanitize_diagnostic_text(w)
            clean_w = sanitized if len(sanitized) <= 200 else (sanitized[:197] + "...")
            w_bytes = len(clean_w.encode("utf-8"))

            if (len(final_warnings) >= MAX_WARNING_ENTRIES - 1) or (current_bytes + w_bytes + marker_bytes > MAX_WARNING_BYTES):
                is_truncated = True
                break
            final_warnings.append(clean_w)
            current_bytes += w_bytes

        if is_truncated:
            final_warnings.append(TRUNCATION_MARKER)

        self._warnings = tuple(final_warnings)

    def match(self, request: OfflineAssetMatchRequest) -> OfflineAssetMatchResult:
        """Thực thi so khớp yêu cầu với danh mục tài nguyên nội bộ."""
        # Cổng thẩm định yêu cầu: dùng chung hàm kiểm tra toàn diện, revalidate cả frozen request
        validate_match_request(request)

        # 1. Phân giải danh mục chuẩn hóa và template_type
        target_cat, target_template_type = _resolve_category(request.category, request.template_type)

        # 2. Xử lý sớm các trường hợp kho rỗng hoặc toàn bộ hỏng
        if self._raw_catalog_count == 0:
            return OfflineAssetMatchResult(
                request_id=request.request_id,
                category=target_cat,
                query_text=request.query_text,
                candidates=(),
                is_matched=False,
                unmatched_reason="ERR_CATALOG_EMPTY",
                suggested_action="Kho tài nguyên hiện đang trống. Vui lòng nạp thêm gói tài nguyên qua Trình nạp Pack.",
                warnings=self._warnings,
                rejected_record_count=self._rejected_count,
            )

        if len(self._valid_records) == 0:
            return OfflineAssetMatchResult(
                request_id=request.request_id,
                category=target_cat,
                query_text=request.query_text,
                candidates=(),
                is_matched=False,
                unmatched_reason="ERR_CATALOG_INVALID",
                suggested_action="Không tìm thấy bản ghi tài nguyên hợp lệ trong kho metadata.",
                warnings=self._warnings,
                rejected_record_count=self._rejected_count,
            )

        # 3. Tiền xử lý các token từ ngữ cảnh truy vấn
        norm_query = _strip_accents(request.query_text)
        query_stem = _stem(request.query_text)
        query_tokens = _tokenize(request.query_text)
        scene_tokens = _tokenize(request.scene_context)
        emotion_tokens = _tokenize(request.emotion_or_mood)
        all_query_tokens = set(query_tokens + scene_tokens + emotion_tokens)

        # Chuẩn hóa subcategory constraint nếu có
        norm_sub_constraint = _strip_accents(request.subcategory_constraint) if request.subcategory_constraint else None

        has_duration_constraint = (request.min_duration_sec is not None) or (request.max_duration_sec is not None)

        scored_candidates: List[OfflineAssetCandidate] = []
        had_offline_candidates: bool = False

        for rec in self._valid_records:
            # Lọc cứng missing và staging (Group D: luôn bị loại trừ khỏi candidate, không xét offline)
            if rec.availability in ("missing", "staging"):
                continue

            # Lọc cứng theo category
            if rec.category != target_cat:
                continue

            # Lọc cứng theo template_type nếu có
            if target_cat == "templates" and target_template_type is not None:
                if rec.template_type != target_template_type:
                    continue

            # Lọc cứng theo subcategory constraint nếu có
            if norm_sub_constraint is not None:
                if _strip_accents(rec.subcategory) != norm_sub_constraint:
                    continue

            # Lọc cứng theo duration (Decision 5: unknown duration bị loại khi có min/max)
            if has_duration_constraint:
                if rec.duration_sec is None:
                    continue
                if request.min_duration_sec is not None and rec.duration_sec < request.min_duration_sec:
                    continue
                if request.max_duration_sec is not None and rec.duration_sec > request.max_duration_sec:
                    continue

            # 4. Chấm điểm các tín hiệu mềm (Soft Scoring Signals)
            score = 0
            signals: List[str] = []

            # Tín hiệu 1: Exact match query với name, stem hoặc asset_id (+40, tối đa 1 lần)
            # Group E: Strip known extensions ở cả query lẫn name; asset_id so sánh nguyên dạng không strip
            if (
                norm_query == rec.normalized_name
                or norm_query == rec.normalized_stem
                or query_stem == rec.normalized_name
                or query_stem == rec.normalized_stem
                or norm_query == _strip_accents(rec.asset_id)
            ):
                score += 40
                signals.append("exact_name")

            # Tín hiệu 2: Trùng token trong name (+25, tối đa 1 lần)
            if any(t in rec.name_tokens for t in all_query_tokens):
                score += 25
                signals.append("name_token")

            # Tín hiệu 3: Trùng token trong tags (+15 mỗi token duy nhất, cap 30)
            matching_tags = [t for t in all_query_tokens if t in rec.tag_tokens]
            if matching_tags:
                tag_pts = min(30, len(matching_tags) * 15)
                score += tag_pts
                signals.append("tag_token")

            # Tín hiệu 4: Khớp qua alias_groups (+15, tối đa 1 lần)
            alias_matched = False
            for grp_key, grp_terms in self._alias_groups.items():
                if any(t == grp_key or t in grp_terms for t in all_query_tokens):
                    rec_all_tokens = rec.name_tokens | rec.tag_tokens | rec.subcategory_tokens
                    if any(ct == grp_key or ct in grp_terms for ct in rec_all_tokens):
                        alias_matched = True
                        break
            if alias_matched:
                score += 15
                signals.append("alias_group")

            # Tín hiệu 5: Trùng token trong subcategory (+10, tối đa 1 lần)
            if any(t in rec.subcategory_tokens for t in all_query_tokens):
                score += 10
                signals.append("subcategory_token")

            total_score = min(100, score)
            # Ngưỡng tối thiểu để được coi là ứng viên
            if total_score < 15:
                continue

            # 5. Kiểm tra availability và opt-in (Decision 3)
            if rec.availability == "offline":
                had_offline_candidates = True
                if not request.allow_offline:
                    continue

            is_native = (rec.category == "templates" and rec.template_type == "trans")
            requires_rec = (rec.availability == "offline")

            cand = OfflineAssetCandidate(
                asset_id=rec.asset_id,
                name=rec.name,
                category=rec.category,
                subcategory=rec.subcategory,
                source_kind=rec.source_kind,
                availability=rec.availability,
                match_score=total_score,
                matched_signals=tuple(signals),
                duration_sec=rec.duration_sec,
                pack_id=rec.pack_id,
                pack_name=rec.pack_name,
                source_id=rec.source_id,
                origin_library_id=rec.origin_library_id,
                template_type=rec.template_type,
                is_native_guidance=is_native,
                requires_reconnect=requires_rec,
            )
            scored_candidates.append(cand)

        # 6. Sắp xếp tất định 100% (Deterministic Stable Sort - Decision 4, 10: ZERO managed bonus)
        scored_candidates.sort(
            key=lambda c: (
                -c.match_score,
                1 if c.availability == "offline" else 0,
                _strip_accents(c.name),
                c.asset_id,
            )
        )

        top_candidates = tuple(scored_candidates[:request.top_k])

        # 7. Xây dựng kết quả hoàn chỉnh
        if top_candidates:
            return OfflineAssetMatchResult(
                request_id=request.request_id,
                category=target_cat,
                query_text=request.query_text,
                candidates=top_candidates,
                is_matched=True,
                unmatched_reason=None,
                suggested_action=None,
                warnings=self._warnings,
                rejected_record_count=self._rejected_count,
            )

        # Xử lý khi không có candidates
        if had_offline_candidates and not request.allow_offline:
            reason = "ERR_ASSETS_OFFLINE"
            action = "Tài nguyên phù hợp đang ở trạng thái ngoại tuyến. Vui lòng kết nối ổ cứng Kho hoặc bật tùy chọn allow_offline."
        else:
            reason = "ERR_NO_MATCH_FOUND"
            action = "Không tìm thấy tài nguyên phù hợp trong danh mục hiện tại. Vui lòng thử từ khóa khác hoặc điều chỉnh bộ lọc."

        return OfflineAssetMatchResult(
            request_id=request.request_id,
            category=target_cat,
            query_text=request.query_text,
            candidates=(),
            is_matched=False,
            unmatched_reason=reason,
            suggested_action=action,
            warnings=self._warnings,
            rejected_record_count=self._rejected_count,
        )
