"""
src/core/action_guard.py
Bộ thẩm định thẩm quyền và tính khả dụng tại thời điểm thao tác (Action-Time Authority & Availability Guard).

RÀNG BUỘC KỸ THUẬT:
- Thuần Python 3.12, KHÔNG phụ thuộc PySide6 / UI / Resolve / Network / Write I/O.
- Quyết định thuần khiết (pure decision) khi được inject mock providers.
- Default read I/O minh bạch qua os.stat / os.path.realpath.
- Không rò rỉ ngoại lệ (fail-closed, failure-safe contract).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat
from typing import Any, Callable, Dict, Literal, Optional, Tuple, Union

from src.core.contracts.asset_dto import VALID_SOURCE_KINDS, is_safe_relative_path

# Nghiêm ngặt đúng 4 enum của hệ thống, không tự chế thêm "unknown"
AvailabilityStatus = Literal["online", "offline", "missing", "staging"]


@dataclass(frozen=True)
class ActionGuardResult:
    allowed: bool
    status: AvailabilityStatus   # "online" | "offline" | "missing" | "staging"
    result_code: str              # Mã chẩn đoán kỹ thuật (VD: ERR_FOREIGN_ROOT, OK_LINKED_MEDIA_VERIFIED)
    reason: str                   # Thông điệp tiếng Việt thân thiện với người dùng
    resolved_path: str = ""       # Đường dẫn media đã được giải mã realpath an toàn
    is_native_guidance: bool = False # Cờ xác định hiệu ứng chuyển cảnh native của DaVinci


def _safe_resolve_path(
    path_val: Any,
    realpath_fn: Callable[[str], str],
) -> Tuple[Optional[str], Optional[ActionGuardResult]]:
    """
    Giải quyết realpath an toàn, bắt mọi ngoại lệ OS/dependency và kiểm tra cấu trúc dữ liệu trả về.
    Trả về (resolved_path_str, None) nếu thành công, hoặc (None, ActionGuardResult) nếu lỗi.
    """
    if not isinstance(path_val, (str, Path)) or not str(path_val).strip():
        return None, ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_MEDIA_PATH_EMPTY",
            reason="Đường dẫn tài nguyên rỗng hoặc không hợp lệ.",
        )
    p_str = str(path_val)
    try:
        resolved = realpath_fn(p_str)
    except PermissionError:
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_FILESYSTEM_IO_OR_PERMISSION",
            reason="Không thể giải quyết đường dẫn file do lỗi phân quyền.",
        )
    except FileNotFoundError:
        return None, ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_FILE_NOT_FOUND",
            reason="Không tìm thấy file nguồn hoặc đường dẫn không tồn tại.",
        )
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EIO):
            return None, ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_FILESYSTEM_IO_OR_PERMISSION",
                reason="Không thể giải quyết đường dẫn file do lỗi phân quyền hoặc I/O ổ đĩa.",
            )
        elif exc.errno == errno.ENOENT:
            return None, ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_FILE_NOT_FOUND",
                reason="Không tìm thấy file nguồn hoặc đường dẫn không tồn tại.",
            )
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_FILESYSTEM_ACCESS",
            reason=f"Lỗi hệ thống file khi giải quyết đường dẫn: {exc}",
        )
    except Exception as exc:
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_PATH_RESOLVE_FAILURE",
            reason=f"Lỗi ngoại lệ khi giải quyết đường dẫn: {exc}",
        )

    if not isinstance(resolved, (str, Path)) or not str(resolved).strip():
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_PATH_RESOLVE_FAILURE",
            reason="Đường dẫn đã giải quyết trả về kiểu dữ liệu không hợp lệ.",
        )

    return str(resolved), None


def _safe_stat_file(
    resolved_file: str,
    stat_fn: Callable[[str], Any],
) -> Tuple[Optional[Any], Optional[ActionGuardResult]]:
    """
    Thực hiện stat file an toàn, bắt mọi ngoại lệ OS/dependency và kiểm tra cấu trúc st_mode.
    Trả về (st_result, None) nếu thành công, hoặc (None, ActionGuardResult) nếu lỗi.
    """
    try:
        st = stat_fn(resolved_file)
    except FileNotFoundError:
        return None, ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_FILE_NOT_FOUND",
            reason="Không tìm thấy file nguồn trong kho chính (file không tồn tại).",
        )
    except PermissionError:
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_FILESYSTEM_IO_OR_PERMISSION",
            reason="Không thể truy cập file do lỗi phân quyền.",
        )
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None, ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_FILE_NOT_FOUND",
                reason="Không tìm thấy file nguồn trong kho chính (file không tồn tại).",
            )
        elif exc.errno in (errno.EACCES, errno.EPERM, errno.EIO):
            return None, ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_FILESYSTEM_IO_OR_PERMISSION",
                reason="Không thể truy cập file do lỗi phân quyền hoặc I/O ổ đĩa.",
            )
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_FILESYSTEM_ACCESS",
            reason=f"Lỗi truy cập hệ thống file: {exc}",
        )
    except Exception as exc:
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_STAT_FAILURE",
            reason=f"Ngoại lệ khi đọc thông số file: {exc}",
        )

    if st is None or not hasattr(st, "st_mode"):
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_STAT_FAILURE",
            reason="Kết quả stat file không hợp lệ (thiếu st_mode).",
        )

    try:
        mode = st.st_mode
        if not isinstance(mode, int):
            return None, ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STAT_FAILURE",
                reason="Thông số st_mode không hợp lệ.",
            )
    except Exception as exc:
        return None, ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_STAT_FAILURE",
            reason=f"Lỗi truy cập st_mode: {exc}",
        )

    if not stat.S_ISREG(mode):
        is_dir = stat.S_ISDIR(mode)
        return None, ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_PATH_IS_DIRECTORY" if is_dir else "ERR_NOT_REGULAR_FILE",
            reason="Đường dẫn trỏ tới thư mục, không phải file media hợp lệ." if is_dir else "Đường dẫn không phải file media thông thường.",
        )

    return st, None


ActionIntent = Literal["insert", "inspect", "drag", "preview", "fusion_export"]


def check_action_availability(
    asset: Any,
    intent: ActionIntent = "insert",
    storage_provider: Optional[Callable[[], Any]] = None,
    stat_fn: Optional[Callable[[str], Any]] = None,
    realpath_fn: Optional[Callable[[str], str]] = None,
    bundled_root: Optional[Union[str, Path]] = None,
) -> ActionGuardResult:
    """
    Thẩm định tính khả dụng và thẩm quyền của tài nguyên trước khi thực hiện thao tác.
    
    Tham số:
    - asset: Mapping/dict chứa metadata của tài nguyên (UIAssetView hoặc DTO dict).
    - intent: Ý định thao tác ("insert", "inspect", "drag", "preview", "fusion_export").
    - storage_provider: Hàm cung cấp StorageManager instance (mặc định khởi tạo StorageManager).
    - stat_fn: Hàm lấy stat file (mặc định os.stat).
    - realpath_fn: Hàm giải mã symlink/realpath (mặc định os.path.realpath).
    - bundled_root: Đường dẫn thư mục assets bundled của ứng dụng (mặc định project assets).
    """
    # 0. Kiểm tra tính hợp lệ của đầu vào và intent
    if not isinstance(asset, Mapping):
        return ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_INVALID_ASSET_INPUT",
            reason="Dữ liệu tài nguyên không hợp lệ (phải là Mapping/dict).",
        )

    if intent not in ("insert", "inspect", "drag", "preview", "fusion_export"):
        return ActionGuardResult(
            allowed=False,
            status="offline",
            result_code="ERR_UNKNOWN_INTENT",
            reason=f"Ý định thao tác '{intent}' không được hỗ trợ bởi hệ thống.",
        )

    # 1. Ý định xem metadata (inspect): Luôn cho phép đọc thông số ngay cả khi offline/missing
    if intent == "inspect":
        st = asset.get("availability")
        norm_st: AvailabilityStatus = st if st in ("online", "offline", "missing", "staging") else "online"
        return ActionGuardResult(
            allowed=True,
            status=norm_st,
            result_code="OK_INSPECT_ONLY",
            reason="Xem thông số metadata tài nguyên.",
        )

    # 2. Kiểm tra trạng thái Staging: Tài nguyên chưa publish/commit tuyệt đối không được dùng
    if asset.get("availability") == "staging":
        return ActionGuardResult(
            allowed=False,
            status="staging",
            result_code="ERR_STAGING_UNPUBLISHED",
            reason="Tài nguyên đang trong quá trình tải/staging — chưa sẵn sàng sử dụng.",
        )

    # 2b. Kiểm tra tính hợp lệ của source_kind (nếu được cung cấp tường minh)
    # RÀNG BUỘC CHẶT CHẼ: Chỉ khi khuyết thiếu hoàn toàn hoặc None mới được tiếp tục theo diện legacy.
    # Bất kỳ giá trị explicit nào không phải chuỗi hoặc không thuộc VALID_SOURCE_KINDS đều bị từ chối ngay.
    source_kind = asset.get("source_kind")
    if source_kind is not None:
        if (
            not isinstance(source_kind, str)
            or not source_kind.strip()
            or source_kind.strip() not in VALID_SOURCE_KINDS
        ):
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_INVALID_SOURCE_KIND",
                reason=f"Loại nguồn tài nguyên (source_kind) không hợp lệ (nhận '{source_kind}').",
            )
        source_kind = source_kind.strip()

    # Tiêm phụ thuộc mặc định (Default IO)
    if realpath_fn is None:
        realpath_fn = os.path.realpath
    if stat_fn is None:
        stat_fn = os.stat
    if storage_provider is None:
        def _default_storage_provider():
            from src.core.storage_manager import StorageManager
            return StorageManager()
        storage_provider = _default_storage_provider

    # Phân tích các đặc tính nhận diện của tài nguyên
    cat = str(asset.get("category") or "").lower()
    tab = str(asset.get("tab") or "").lower()
    tt = str(asset.get("template_type") or "").lower()
    raw_obj = asset.get("_raw_obj")
    if hasattr(raw_obj, "native_preset") and getattr(raw_obj, "native_preset") is not None:
        raw_obj = getattr(raw_obj, "native_preset")
    elif asset.get("native_preset") is not None:
        raw_obj = asset.get("native_preset")
    raw_type = type(raw_obj).__name__ if raw_obj is not None else ""

    # 3. Hiệu ứng chuyển cảnh Native (Native Transition Guidance):
    # Với intent == "drag": Bị từ chối hoàn toàn (ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED).
    # Với intent == "insert": Hướng dẫn thủ công từ Effects Library.
    TRANSITION_TAGS = ("trans", "transition", "transitions")
    is_transition_effect = (
        cat in TRANSITION_TAGS
        or tab in TRANSITION_TAGS
        or tt in TRANSITION_TAGS
        or raw_type == "TransitionStylePreset"
        or (raw_obj is not None and hasattr(raw_obj, "transition_type"))
    )
    if is_transition_effect:
        if intent == "fusion_export":
            return ActionGuardResult(
                allowed=False,
                status="online",
                result_code="ERR_NATIVE_TRANSITION_FUSION_UNSUPPORTED",
                reason="Chuyển cảnh native không hỗ trợ xuất mã Fusion node; hãy sử dụng hiệu ứng từ Effects Library.",
                is_native_guidance=True,
            )
        elif intent == "drag":
            return ActionGuardResult(
                allowed=False,
                status="online",
                result_code="ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED",
                reason="Chuyển cảnh native không hỗ trợ kéo thả trực tiếp vào timeline; hãy sử dụng hiệu ứng từ Effects Library.",
                is_native_guidance=True,
            )
        elif intent == "insert":
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code="OK_NATIVE_TRANSITION_GUIDANCE",
                reason="Sẵn sàng hướng dẫn chuyển cảnh native từ Effects Library.",
                is_native_guidance=True,
            )
        # Với intent == "preview": Không trả về guidance sớm ở đây; tiếp tục để xác minh file demo preview

    # Kiểm tra xung đột metadata nguồn khi intent in ("drag", "preview", "fusion_export") (trả lại nguyên trạng cho insert/inspect)
    if intent in ("drag", "preview", "fusion_export"):
        if source_kind == "linked" and (asset.get("storage_managed") is True or bool(asset.get("library_path"))):
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_CONFLICTING_SOURCE_METADATA",
                reason="Tài nguyên liên kết ngoài xung đột với đánh dấu do kho quản lý.",
            )
        if source_kind in ("builtin", "generated") and (
            asset.get("storage_managed") is True
            or bool(asset.get("library_path"))
            or bool(asset.get("external_path"))
            or bool(str(asset.get("origin_library_id") or "").strip())
            or bool(str(asset.get("origin_root") or "").strip())
        ):
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_CONFLICTING_SOURCE_METADATA",
                reason="Xung đột metadata nguồn tài nguyên.",
            )
        if source_kind == "managed" and bool(asset.get("external_path")):
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_CONFLICTING_SOURCE_METADATA",
                reason="Tài nguyên do kho quản lý không được chứa đường dẫn liên kết ngoài.",
            )
        if source_kind is None and bool(asset.get("external_path")) and (
            asset.get("storage_managed") is True or bool(asset.get("library_path"))
        ):
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_CONFLICTING_SOURCE_METADATA",
                reason="Xung đột metadata nguồn tài nguyên.",
            )

    # Khởi tạo effective_source_kind=source_kind; chỉ suy luận bổ sung cho intent drag, preview và fusion_export.
    # Insert/inspect giữ nguyên policy cho trường hợp source_kind missing/None để bảo toàn legacy.
    effective_source_kind = source_kind
    if intent in ("drag", "preview", "fusion_export"):
        if source_kind is None:
            if asset.get("storage_managed") is True or bool(asset.get("library_path")):
                effective_source_kind = "managed"
            elif bool(asset.get("external_path")):
                effective_source_kind = "linked"
            else:
                effective_source_kind = None

        # Không có linked evidence nhưng có origin_library_id HOẶC origin_root -> fail-closed ERR_ORIGIN_UNVERIFIED
        # trước procedural/legacy fallback
        has_origin = bool(str(asset.get("origin_library_id") or "").strip()) or bool(
            str(asset.get("origin_root") or "").strip()
        )
        if has_origin and effective_source_kind != "linked" and effective_source_kind != "managed":
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_ORIGIN_UNVERIFIED",
                reason="Tài nguyên trong kho chưa được xác thực nguồn gốc — hãy làm mới danh mục kho.",
            )

    # 3b. Kiểm tra tài nguyên đa phương tiện không hỗ trợ xuất Fusion (Non-Fusion Media):
    if intent == "fusion_export":
        cand_master = str(asset.get("file_path") or asset.get("path") or "").lower()
        NON_FUSION_EXTS = (
            ".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg",
            ".webp", ".svg", ".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a",
            ".cube", ".3dl"
        )
        NON_FUSION_TAGS = (
            "meme", "memes", "overlay", "overlays", "broll", "video",
            "sticker", "stickers", "green_screen", "sfx", "bgm", "audio",
            "sound", "lut", "luts", "color"
        )
        from src.core.text_preset import TextStylePreset
        is_real_text_preset = isinstance(raw_obj, TextStylePreset)
        if not is_real_text_preset and not cand_master.endswith(".setting"):
            if cand_master.endswith(NON_FUSION_EXTS) or cat in NON_FUSION_TAGS or tab in NON_FUSION_TAGS:
                return ActionGuardResult(
                    allowed=False,
                    status="online",
                    result_code="ERR_NON_FUSION_MEDIA_UNSUPPORTED",
                    reason="Tài nguyên đa phương tiện (Video/Audio/LUT) không hỗ trợ xuất mã Fusion node.",
                )

    # 4. Preset sinh mã động tại runtime (Procedural Generator Preset):
    if intent == "drag":
        if effective_source_kind != "managed":
            from src.core.text_preset import TextStylePreset
            # Bỏ OR raw_type == TextStylePreset: chỉ domain isinstance TextStylePreset thật
            if isinstance(raw_obj, TextStylePreset):
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_PROCEDURAL_DRAG_SETTING",
                    reason="Sẵn sàng xuất file setting Fusion cho kéo thả chữ nghệ thuật.",
                    resolved_path="",
                )
            if (
                cat in ("text", "title")
                or tab in ("text", "title")
                or tt in ("text", "title")
                or (raw_obj is not None and callable(getattr(raw_obj, "generate", None)))
            ):
                if not asset.get("file_path") and not asset.get("path"):
                    return ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_PROCEDURAL_DRAG_UNSUPPORTED",
                        reason="Loại preset sinh mã này chưa được hỗ trợ kéo thả trực tiếp.",
                    )
    elif intent == "preview":
        if effective_source_kind != "managed":
            from src.core.text_preset import TextStylePreset
            if isinstance(raw_obj, TextStylePreset):
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_PROCEDURAL_PREVIEW_CANVAS",
                    reason="Sẵn sàng vẽ preview canvas cho chữ nghệ thuật.",
                    resolved_path="",
                )
            if (
                cat in ("text", "title")
                or tab in ("text", "title")
                or tt in ("text", "title")
                or (raw_obj is not None and callable(getattr(raw_obj, "generate", None)))
            ):
                if not asset.get("preview_path") and not asset.get("file_path") and not asset.get("path"):
                    return ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_PROCEDURAL_PREVIEW_UNSUPPORTED",
                        reason="Loại preset chữ này không có media preview và không hỗ trợ vẽ canvas động.",
                        resolved_path="",
                    )
    elif intent == "fusion_export":
        if effective_source_kind != "managed":
            from src.core.text_preset import TextStylePreset
            if isinstance(raw_obj, TextStylePreset):
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_PROCEDURAL_FUSION_EXPORT",
                    reason="Sẵn sàng xuất mã Fusion node từ preset kiểu chữ.",
                    resolved_path="",
                )
            if (
                cat in ("text", "title")
                or tab in ("text", "title")
                or tt in ("text", "title")
                or (raw_obj is not None and callable(getattr(raw_obj, "generate", None)))
            ):
                if not asset.get("file_path") and not asset.get("path"):
                    return ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_PROCEDURAL_FUSION_UNSUPPORTED",
                        reason="Loại preset này không hỗ trợ xuất mã Fusion node.",
                    )
    else:
        # Giữ nguyên logic insert
        is_procedural_generator = (
            source_kind != "managed"
            and (
                raw_type == "TextStylePreset"
                or (raw_obj is not None and callable(getattr(raw_obj, "generate", None)))
            )
        )
        if is_procedural_generator:
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code="OK_PROCEDURAL_PRESET",
                reason="Sẵn sàng sinh mã Fusion động.",
            )

    # 5. Thẩm định tài nguyên do Kho lưu trữ quản lý (source_kind == "managed")
    if effective_source_kind == "managed":
        origin_lib_id = asset.get("origin_library_id")
        origin_root = asset.get("origin_root")

        # 5.1. Bắt buộc phải có bằng chứng định danh gốc
        if not origin_lib_id or not isinstance(origin_lib_id, str) or not origin_lib_id.strip():
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_ORIGIN_UNVERIFIED",
                reason="Tài nguyên trong kho chưa được xác thực nguồn gốc — hãy làm mới danh mục kho.",
            )
        if not origin_root or not isinstance(origin_root, str) or not origin_root.strip():
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_ORIGIN_UNVERIFIED",
                reason="Tài nguyên trong kho chưa được xác thực nguồn gốc — hãy làm mới danh mục kho.",
            )

        # 5.2. Kiểm tra tính khả dụng của StorageManager
        try:
            storage = storage_provider() if callable(storage_provider) else storage_provider
        except Exception as exc:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_PROVIDER_FAILURE",
                reason=f"Không thể kết nối dịch vụ quản lý kho lưu trữ: {exc}",
            )

        try:
            is_online = bool(getattr(storage, "online", False))
        except Exception as exc:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_PROVIDER_FAILURE",
                reason=f"Lỗi kiểm tra trạng thái kho lưu trữ: {exc}",
            )

        if storage is None or not is_online:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_OFFLINE",
                reason="Kho Offline — hãy kết nối lại đúng ổ đĩa.",
            )

        # 5.3. Đối chiếu danh tính kho (Identity Gate)
        try:
            cfg = getattr(storage, "_config", None)
            if isinstance(cfg, Mapping):
                active_lib_id = cfg.get("library_id")
            else:
                active_lib_id = getattr(storage, "library_id", None)
        except Exception as exc:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_PROVIDER_FAILURE",
                reason=f"Lỗi đọc cấu hình định danh kho: {exc}",
            )

        if not active_lib_id or active_lib_id != origin_lib_id:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_IDENTITY_MISMATCH",
                reason="Định danh kho lưu trữ hiện tại không khớp với tài nguyên đã nạp.",
            )

        try:
            active_root_val = getattr(storage, "root", None)
            if not isinstance(active_root_val, (str, Path)):
                return ActionGuardResult(
                    allowed=False,
                    status="offline",
                    result_code="ERR_STORAGE_PROVIDER_FAILURE",
                    reason="Thư mục kho chính không hợp lệ.",
                )
            active_root_str = str(active_root_val)
        except Exception as exc:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_STORAGE_PROVIDER_FAILURE",
                reason=f"Không thể truy cập thư mục kho chính: {exc}",
            )

        # 5.4. Đối chiếu ranh giới thư mục gốc (Root Match)
        norm_active_root, err = _safe_resolve_path(active_root_str, realpath_fn)
        if err:
            return err
        norm_origin_root, err = _safe_resolve_path(origin_root, realpath_fn)
        if err:
            return err

        if norm_active_root != norm_origin_root:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_FOREIGN_ROOT",
                reason="Tài nguyên thuộc kho lưu trữ cũ (khác thư mục kho đang mở).",
            )

        # 5.5. Kiểm tra tính an toàn của đường dẫn tương đối (Lexical Relative Path)
        lib_path = asset.get("library_path") or asset.get("relative_path")
        if not lib_path or not isinstance(lib_path, str):
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_MISSING_LIBRARY_PATH",
                reason="Thiếu đường dẫn tương đối trong kho lưu trữ.",
            )

        if not is_safe_relative_path(lib_path):
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_INVALID_PATH_TRAVERSAL",
                reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
            )

        from src.core.storage_manager import safe_relative
        try:
            safe_rel = safe_relative(lib_path)
            expected_path = Path(norm_active_root) / safe_rel
        except ValueError:
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_INVALID_PATH_TRAVERSAL",
                reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
            )

        fp = asset.get("file_path")
        if fp:
            norm_fp, err = _safe_resolve_path(str(fp), realpath_fn)
            if err:
                return err
            norm_exp, err = _safe_resolve_path(str(expected_path), realpath_fn)
            if err:
                return err
            if norm_fp != norm_exp:
                return ActionGuardResult(
                    allowed=False,
                    status="offline",
                    result_code="ERR_PATH_MISMATCH",
                    reason="Đường dẫn file không khớp với vị trí chuẩn trong kho.",
                )
            target_file = norm_fp
        else:
            target_file = str(expected_path)

        # 5.6. Chống thoát khỏi kho qua Symlink (Symlink Escape Gate)
        resolved_file, err = _safe_resolve_path(target_file, realpath_fn)
        if err:
            return err

        try:
            Path(resolved_file).relative_to(Path(norm_active_root))
        except ValueError:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_SYMLINK_ESCAPE",
                reason="Đường dẫn tài nguyên thoát khỏi phạm vi an toàn của kho.",
            )
        except Exception as exc:
            return ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_SYMLINK_ESCAPE",
                reason=f"Lỗi kiểm tra ranh giới đường dẫn: {exc}",
            )

        # 5.7. Phép thử file thông thường trên đĩa (Regular File Predicate & Stat)
        if intent == "preview":
            # 1. Thử preview candidate trước
            cand_prev = asset.get("preview_path")
            candidate_valid = False
            candidate_resolved: Optional[str] = None
            candidate_err: Optional[ActionGuardResult] = None

            if cand_prev and isinstance(cand_prev, (str, Path)) and str(cand_prev).strip():
                prev_str = str(cand_prev).strip()
                if ".." in Path(prev_str).parts:
                    candidate_err = ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_INVALID_PATH_TRAVERSAL",
                        reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
                    )
                else:
                    resolved_prev, p_err = _safe_resolve_path(prev_str, realpath_fn)
                    if p_err:
                        candidate_err = p_err
                    else:
                        # Candidate phải nằm trong active_root của kho
                        try:
                            Path(resolved_prev).relative_to(Path(norm_active_root))
                            p_st, p_err = _safe_stat_file(resolved_prev, stat_fn)
                            if not p_err:
                                candidate_valid = True
                                candidate_resolved = resolved_prev
                            else:
                                candidate_err = p_err
                        except ValueError:
                            candidate_err = ActionGuardResult(
                                allowed=False,
                                status="offline",
                                result_code="ERR_SYMLINK_ESCAPE",
                                reason="Đường dẫn tài nguyên thoát khỏi phạm vi an toàn của kho.",
                            )

            if candidate_valid and candidate_resolved is not None:
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_PREVIEW_VERIFIED",
                    reason="Xem trước tài nguyên kho hợp lệ.",
                    resolved_path=candidate_resolved,
                )

            # 2. Fallback sang master file_path (đã qua kiểm tra symlink escape trên norm_active_root)
            st, err = _safe_stat_file(resolved_file, stat_fn)
            if not err:
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_PREVIEW_VERIFIED",
                    reason="Xem trước từ file master kho hợp lệ.",
                    resolved_path=resolved_file,
                )

            # Cả preview candidate và master file_path đều không khả dụng
            if candidate_err is not None:
                return candidate_err

            if err.result_code == "ERR_FILE_NOT_FOUND":
                return ActionGuardResult(
                    allowed=False,
                    status="missing",
                    result_code="ERR_FILE_NOT_FOUND",
                    reason="Không tìm thấy file preview hoặc master trong kho.",
                )
            return err

        st, err = _safe_stat_file(resolved_file, stat_fn)
        if err:
            if err.result_code == "ERR_FILE_NOT_FOUND":
                return ActionGuardResult(
                    allowed=False,
                    status="missing",
                    result_code="ERR_FILE_NOT_FOUND",
                    reason="Không tìm thấy file nguồn trong kho chính (file không tồn tại).",
                )
            return err

        if intent == "fusion_export":
            if not resolved_file.lower().endswith(".setting"):
                return ActionGuardResult(
                    allowed=False,
                    status="online",
                    result_code="ERR_NON_FUSION_MEDIA_UNSUPPORTED",
                    reason="Tài nguyên đa phương tiện (Video/Audio/LUT) không hỗ trợ xuất mã Fusion node.",
                )
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code="OK_FILEBACKED_FUSION_EXPORT",
                reason="Sẵn sàng xuất mã Fusion từ tệp mẫu .setting trong kho.",
                resolved_path=resolved_file,
            )

        return ActionGuardResult(
            allowed=True,
            status="online",
            result_code="OK_MANAGED_MEDIA_VERIFIED",
            reason="Tài nguyên kho hợp lệ.",
            resolved_path=resolved_file,
        )

    # 6. Thẩm định tài nguyên Liên kết Ngoài (source_kind == "linked")
    if effective_source_kind == "linked":
        ext_p = asset.get("external_path")
        fp = asset.get("file_path")

        # Nguồn chân lý là external_path; phát hiện xung đột nếu cả 2 cùng có và khác nhau
        if ext_p and fp:
            norm_ext, err = _safe_resolve_path(str(ext_p), realpath_fn)
            if err:
                return err
            norm_fp, err = _safe_resolve_path(str(fp), realpath_fn)
            if err:
                return err
            if norm_ext != norm_fp:
                return ActionGuardResult(
                    allowed=False,
                    status="offline",
                    result_code="ERR_PATH_CONFLICT",
                    reason="Xung đột giữa external_path và file_path của tài nguyên liên kết.",
                )
            target_file = norm_ext
        elif ext_p:
            target_file = str(ext_p)
        elif fp:
            target_file = str(fp)
        else:
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_LINKED_PATH_MISSING",
                reason="Tài nguyên liên kết thiếu đường dẫn ngoài.",
            )

        # Kiểm tra traversal
        if ".." in Path(target_file).parts:
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_INVALID_PATH_TRAVERSAL",
                reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
            )

        resolved_file, err = _safe_resolve_path(target_file, realpath_fn)
        if err:
            return err

        # Xác thực anchor external_path trước khi cấp quyền (cho cả companion và master)
        anchor_st, anchor_err = _safe_stat_file(resolved_file, stat_fn)
        if anchor_err:
            if anchor_err.result_code == "ERR_FILE_NOT_FOUND":
                return ActionGuardResult(
                    allowed=False,
                    status="missing",
                    result_code="ERR_FILE_NOT_FOUND",
                    reason="Không tìm thấy file liên kết ngoài trên đĩa.",
                )
            return anchor_err

        # Anchor đã được xác thực an toàn trên đĩa
        if intent == "preview":
            cand_prev = asset.get("preview_path")
            if cand_prev and isinstance(cand_prev, (str, Path)) and str(cand_prev).strip():
                prev_str = str(cand_prev).strip()
                if ".." in Path(prev_str).parts:
                    return ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_INVALID_PATH_TRAVERSAL",
                        reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
                    )
                resolved_cand, p_err = _safe_resolve_path(prev_str, realpath_fn)
                if not p_err:
                    cand_p = Path(resolved_cand)
                    anchor_p = Path(resolved_file)
                    is_same = (cand_p == anchor_p)
                    is_companion = (
                        cand_p.parent == anchor_p.parent
                        and cand_p.stem == anchor_p.stem
                        and cand_p.suffix.lower() in ('.mp4', '.mov', '.gif', '.webm', '.wav', '.mp3', '.aac')
                    )
                    if is_same or is_companion:
                        p_st, p_err = _safe_stat_file(resolved_cand, stat_fn)
                        if not p_err:
                            return ActionGuardResult(
                                allowed=True,
                                status="online",
                                result_code="OK_LINKED_PREVIEW_VERIFIED",
                                reason="Xem trước tài nguyên liên kết ngoài khả dụng.",
                                resolved_path=resolved_cand,
                            )

            # Fallback sang verified anchor master (đã qua anchor_err check ở trên)
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code="OK_LINKED_PREVIEW_VERIFIED",
                reason="Xem trước tài nguyên liên kết ngoài khả dụng.",
                resolved_path=resolved_file,
            )

        if intent == "fusion_export":
            if not resolved_file.lower().endswith(".setting"):
                return ActionGuardResult(
                    allowed=False,
                    status="online",
                    result_code="ERR_NON_FUSION_MEDIA_UNSUPPORTED",
                    reason="Tài nguyên đa phương tiện (Video/Audio/LUT) không hỗ trợ xuất mã Fusion node.",
                )
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code="OK_FILEBACKED_FUSION_EXPORT",
                reason="Sẵn sàng xuất mã Fusion từ tệp mẫu .setting liên kết ngoài.",
                resolved_path=resolved_file,
            )

        # intent in ("insert", "drag")
        return ActionGuardResult(
            allowed=True,
            status="online",
            result_code="OK_LINKED_MEDIA_VERIFIED",
            reason="Tài nguyên liên kết ngoài khả dụng.",
            resolved_path=resolved_file,
        )

    # 7. Tài nguyên Builtin, Generated (cần file) và Tài nguyên Legacy thường
    # Chính sách từ chối nếu cache ghi nhận offline hoặc missing
    cached_avail = asset.get("availability")
    if cached_avail in ("offline", "missing"):
        if not (intent == "preview" and asset.get("preview_path")):
            return ActionGuardResult(
                allowed=False,
                status=cached_avail,
                result_code=f"ERR_CACHED_{cached_avail.upper()}",
                reason=f"Tài nguyên đã được đánh dấu {cached_avail} trong hệ thống.",
            )

    if intent == "preview":
        if effective_source_kind == "builtin" or is_transition_effect:
            b_root = bundled_root
            if b_root is None:
                b_root = Path(__file__).resolve().parent.parent.parent / "assets"
            norm_bundled_root, b_err = _safe_resolve_path(str(b_root), realpath_fn)
            if b_err:
                return b_err
            if not norm_bundled_root or not os.path.isabs(norm_bundled_root):
                return ActionGuardResult(
                    allowed=False,
                    status="offline",
                    result_code="ERR_STORAGE_PROVIDER_FAILURE",
                    reason="Thư mục tài nguyên ứng dụng (bundled_root) không hợp lệ.",
                )

            cand_prev = asset.get("preview_path")
            candidate_valid = False
            candidate_resolved = None
            candidate_err = None

            if cand_prev and isinstance(cand_prev, (str, Path)) and str(cand_prev).strip():
                prev_str = str(cand_prev).strip()
                if ".." in Path(prev_str).parts:
                    candidate_err = ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_INVALID_PATH_TRAVERSAL",
                        reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
                    )
                else:
                    resolved_prev, p_err = _safe_resolve_path(prev_str, realpath_fn)
                    if p_err:
                        candidate_err = p_err
                    else:
                        try:
                            Path(resolved_prev).relative_to(Path(norm_bundled_root))
                            p_st, p_err = _safe_stat_file(resolved_prev, stat_fn)
                            if not p_err:
                                candidate_valid = True
                                candidate_resolved = resolved_prev
                            else:
                                candidate_err = p_err
                        except ValueError:
                            candidate_err = ActionGuardResult(
                                allowed=False,
                                status="offline",
                                result_code="ERR_SYMLINK_ESCAPE",
                                reason="Đường dẫn tài nguyên thoát khỏi phạm vi an toàn của ứng dụng.",
                            )
            if candidate_valid and candidate_resolved is not None:
                return ActionGuardResult(
                    allowed=True,
                    status="online",
                    result_code="OK_BUILTIN_PREVIEW_VERIFIED",
                    reason="Xem trước tài nguyên ứng dụng hợp lệ.",
                    resolved_path=candidate_resolved,
                )

            # Fallback sang master file trong norm_bundled_root
            target_master = asset.get("file_path") or asset.get("path")
            if target_master and isinstance(target_master, (str, Path)) and str(target_master).strip():
                master_str = str(target_master).strip()
                if ".." in Path(master_str).parts:
                    if candidate_err is not None:
                        return candidate_err
                    return ActionGuardResult(
                        allowed=False,
                        status="missing",
                        result_code="ERR_INVALID_PATH_TRAVERSAL",
                        reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
                    )
                resolved_master, m_err = _safe_resolve_path(master_str, realpath_fn)
                if not m_err:
                    try:
                        Path(resolved_master).relative_to(Path(norm_bundled_root))
                        st, err = _safe_stat_file(resolved_master, stat_fn)
                        if not err:
                            return ActionGuardResult(
                                allowed=True,
                                status="online",
                                result_code="OK_BUILTIN_PREVIEW_VERIFIED",
                                reason="Xem trước tài nguyên ứng dụng hợp lệ.",
                                resolved_path=resolved_master,
                            )
                    except ValueError:
                        pass

            if candidate_err is not None:
                return candidate_err

            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_FILE_NOT_FOUND",
                reason="Không tìm thấy file preview hoặc master trong tài nguyên ứng dụng.",
            )

        # Generated hoặc Legacy (file-backed) preview
        cand_prev = asset.get("preview_path")
        candidate_valid = False
        candidate_resolved = None
        candidate_err = None

        if cand_prev and isinstance(cand_prev, (str, Path)) and str(cand_prev).strip():
            prev_str = str(cand_prev).strip()
            if ".." in Path(prev_str).parts:
                candidate_err = ActionGuardResult(
                    allowed=False,
                    status="missing",
                    result_code="ERR_INVALID_PATH_TRAVERSAL",
                    reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
                )
            else:
                resolved_prev, p_err = _safe_resolve_path(prev_str, realpath_fn)
                if p_err:
                    candidate_err = p_err
                else:
                    target_master = asset.get("file_path") or asset.get("path")
                    if target_master:
                        resolved_master_cand, m_err = _safe_resolve_path(str(target_master), realpath_fn)
                        if not m_err:
                            cp = Path(resolved_prev)
                            mp = Path(resolved_master_cand)
                            is_same = (cp == mp)
                            is_companion = (cp.parent == mp.parent and cp.stem == mp.stem)
                            if is_same or is_companion:
                                p_st, p_err = _safe_stat_file(resolved_prev, stat_fn)
                                if not p_err:
                                    candidate_valid = True
                                    candidate_resolved = resolved_prev
                                else:
                                    candidate_err = p_err
                            else:
                                candidate_err = ActionGuardResult(
                                    allowed=False,
                                    status="offline",
                                    result_code="ERR_ARBITRARY_PROXY_DENIED",
                                    reason="File xem trước không cùng quan hệ nguồn với file gốc.",
                                )
        if candidate_valid and candidate_resolved is not None:
            code = "OK_GENERATED_PREVIEW_VERIFIED" if effective_source_kind == "generated" else "OK_LEGACY_PREVIEW"
            return ActionGuardResult(
                allowed=True,
                status="online",
                result_code=code,
                reason="Xem trước tài nguyên khả dụng.",
                resolved_path=candidate_resolved,
            )

        # Fallback sang master file cho Generated / Legacy
        target_file = asset.get("file_path") or asset.get("path")
        if not target_file or not isinstance(target_file, str) or not target_file.strip():
            if candidate_err is not None:
                return candidate_err
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_MEDIA_PATH_EMPTY",
                reason="Tài nguyên không có đường dẫn file hợp lệ.",
            )

        if ".." in Path(target_file).parts:
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_INVALID_PATH_TRAVERSAL",
                reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
            )

        resolved_file, err = _safe_resolve_path(target_file, realpath_fn)
        if err:
            return err

        st, err = _safe_stat_file(resolved_file, stat_fn)
        if err:
            if candidate_err is not None:
                return candidate_err
            if err.result_code == "ERR_FILE_NOT_FOUND":
                return ActionGuardResult(
                    allowed=False,
                    status="missing",
                    result_code="ERR_FILE_NOT_FOUND",
                    reason="Không tìm thấy file media trên đĩa.",
                )
            return err

        res_code = "OK_GENERATED_PREVIEW_VERIFIED" if effective_source_kind == "generated" else "OK_LEGACY_PREVIEW"
        return ActionGuardResult(
            allowed=True,
            status="online",
            result_code=res_code,
            reason="Tài nguyên khả dụng.",
            resolved_path=resolved_file,
        )

    target_file = asset.get("file_path") or asset.get("path")
    if not target_file or not isinstance(target_file, str) or not target_file.strip():
        return ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_MEDIA_PATH_EMPTY",
            reason="Tài nguyên không có đường dẫn file hợp lệ.",
        )

    if ".." in Path(target_file).parts:
        return ActionGuardResult(
            allowed=False,
            status="missing",
            result_code="ERR_INVALID_PATH_TRAVERSAL",
            reason="Đường dẫn tài nguyên chứa phân đoạn không an toàn (..).",
        )

    resolved_file, err = _safe_resolve_path(target_file, realpath_fn)
    if err:
        return err

    st, err = _safe_stat_file(resolved_file, stat_fn)
    if err:
        if err.result_code == "ERR_FILE_NOT_FOUND":
            return ActionGuardResult(
                allowed=False,
                status="missing",
                result_code="ERR_FILE_NOT_FOUND",
                reason="Không tìm thấy file media trên đĩa.",
            )
        return err

    if intent == "fusion_export":
        if not resolved_file.lower().endswith(".setting"):
            return ActionGuardResult(
                allowed=False,
                status="online",
                result_code="ERR_NON_FUSION_MEDIA_UNSUPPORTED",
                reason="Tài nguyên đa phương tiện (Video/Audio/LUT) không hỗ trợ xuất mã Fusion node.",
            )
        return ActionGuardResult(
            allowed=True,
            status="online",
            result_code="OK_FILEBACKED_FUSION_EXPORT",
            reason="Sẵn sàng xuất mã Fusion từ tệp mẫu .setting.",
            resolved_path=resolved_file,
        )

    if effective_source_kind == "builtin":
        res_code = "OK_BUILTIN_MEDIA_VERIFIED"
    elif effective_source_kind == "generated":
        res_code = "OK_GENERATED_MEDIA_VERIFIED"
    else:
        res_code = "OK_LEGACY_MEDIA"
    return ActionGuardResult(
        allowed=True,
        status="online",
        result_code=res_code,
        reason="Tài nguyên khả dụng.",
        resolved_path=resolved_file,
    )
