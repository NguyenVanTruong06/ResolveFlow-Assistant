"""Hợp đồng Snapshot Hồ sơ Phê duyệt ChunDVC — AppOwnedApprovalRecord (G06-06C1b2-I).

Module pure Python chuẩn stdlib, định nghĩa:
- AppOwnedApprovalRecord: Frozen DTO biểu diễn hồ sơ phê duyệt thuộc quyền sở hữu của ứng dụng.
- create_app_owned_approval_record: Factory tạo bản ghi phê duyệt từ EditPlanProposal đã duyệt.
- approval_record_to_dict: Xuất bản từ điển tươi mới chứa các JSON primitives.
- verify_proposal_approval_binding: Xác minh tính liên kết toàn vẹn tuyệt đối giữa Proposal và Approval.

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI/Resolve, zero system clock/random (now()/time()/UUID).
- Immutable & owned (frozen dataclass, tuple, zero mutable runtime aliases).
- Giới hạn dung lượng UTF-8: MAX_APPROVAL_RECORD_BYTES = MAX_PROPOSAL_SNAPSHOT_BYTES + 4096 (528,384 bytes).
- Giới hạn accepted_omission_ids: len <= MAX_PROPOSED_OMISSIONS (128), pre-check 0 iterations.
- Cấm nhận authority từ AI JSON; factory là bước ghi metadata do ứng dụng làm chủ sau khi người dùng duyệt một lượt.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any, Tuple, Union

from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    MAX_PROPOSAL_SNAPSHOT_BYTES,
    MAX_PROPOSED_OMISSIONS,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)

# Dung lượng trần cho bản ghi phê duyệt: MAX_PROPOSAL_SNAPSHOT_BYTES (512 KiB) + 4096 bytes metadata
MAX_APPROVAL_RECORD_BYTES: int = MAX_PROPOSAL_SNAPSHOT_BYTES + 4096  # 528,384 bytes UTF-8

VALID_APPROVAL_SCOPE: str = "ENTIRE_PROPOSAL"
RE_LOWER_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_ISO_TIMESTAMP: re.Pattern = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


def _validate_str(val: Any, field_name: str, allow_blank: bool = False) -> str:
    """Xác thực chuỗi UTF-8 hợp lệ, không chứa ký tự NUL hoặc lone surrogates."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8 (lone surrogate): {e}") from e
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống hoặc chỉ chứa khoảng trắng")
    return val


def _validate_iso_timestamp(val: Any, field_name: str) -> str:
    """Xác thực chuỗi timestamp ISO-8601 formatrecordv1 chuẩn tắc có timezone và dấu phân cách T."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8: {e}") from e

    # Cấm khoảng trắng ở đầu hoặc cuối (không tự động trim)
    if val != val.strip():
        raise ValueError(f"ERR_INVALID_TIMESTAMP: {field_name} không được chứa khoảng trắng ở đầu hoặc cuối: {val!r}")

    # Lexical fullmatch formatrecordv1: YYYY-MM-DDTHH:MM:SS[.1..6digits](Z|+/-HH:MM)
    if not RE_ISO_TIMESTAMP.fullmatch(val):
        raise ValueError(
            f"ERR_INVALID_TIMESTAMP: {field_name} không đúng chuẩn lexical ISO-8601 formatrecordv1 "
            f"(YYYY-MM-DDTHH:MM:SS[.1..6digits](Z|+/-HH:MM)): {val!r}"
        )

    # Kiểm tra tính hợp lệ của lịch và thời gian thực qua datetime.fromisoformat
    try:
        dt = datetime.fromisoformat(val)
    except Exception as e:
        raise ValueError(f"ERR_INVALID_TIMESTAMP: {field_name} không phải định dạng ISO-8601 hợp lệ ({e}): {val!r}") from e

    # Bắt buộc phải có thông tin timezone (loại trừ naive timestamp)
    if dt.tzinfo is None:
        raise ValueError(f"ERR_NAIVE_TIMESTAMP: {field_name} phải có thông tin timezone (Z hoặc +/-HH:MM), nhận: {val!r}")

    return val


def _validate_revision_hex(val: Any, field_name: str) -> str:
    """Xác thực mã băm SHA-256 gồm đúng 64 ký tự hex viết thường."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__}")
    if not RE_LOWER_HEX_64.fullmatch(val):
        raise ValueError(f"ERR_INVALID_REVISION: {field_name} phải là chuỗi đúng 64 ký tự hex viết thường [0-9a-f], nhận: {val!r}")
    return val


def _validate_accepted_omission_ids(raw_ids: Any, field_name: str) -> Tuple[str, ...]:
    """Xác thực danh sách accepted_omission_ids, kiểm tra trần số lượng và tính duy nhất."""
    if not isinstance(raw_ids, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: {field_name} phải là list hoặc tuple, nhận: {type(raw_ids).__name__}")

    # Pre-check độ dài trước khi lặp (bảo đảm 0 iterations nếu tràn trần)
    if len(raw_ids) > MAX_PROPOSED_OMISSIONS:
        raise ValueError(f"ERR_COUNT_OVERFLOW: {field_name} có {len(raw_ids)} mục, vượt quá giới hạn {MAX_PROPOSED_OMISSIONS}")

    seen_ids: set[str] = set()
    owned_ids = []
    for idx, oid in enumerate(raw_ids):
        valid_id = _validate_str(oid, f"{field_name}[{idx}]", allow_blank=False)
        if valid_id in seen_ids:
            raise ValueError(f"ERR_DUPLICATE_OMISSION_ID: {field_name}[{idx}] ({valid_id!r}) bị trùng lặp")
        seen_ids.add(valid_id)
        owned_ids.append(valid_id)

    return tuple(owned_ids)


def _serialize_plain_approval_dict(approval: "AppOwnedApprovalRecord") -> dict[str, object]:
    """Chuyển đổi AppOwnedApprovalRecord thành plain dict chứa JSON primitives."""
    return {
        "approval_id": approval.approval_id,
        "proposal_id": approval.proposal_id,
        "proposal_revision": approval.proposal_revision,
        "approved_at_iso": approval.approved_at_iso,
        "accepted_omission_ids": list(approval.accepted_omission_ids),
        "approval_scope": approval.approval_scope,
    }


def _encode_canonical_approval_bytes(record_dict: dict[str, object]) -> bytes:
    """Mã hóa canonical JSON UTF-8 phục vụ kiểm tra byte budget."""
    return json.dumps(
        record_dict,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class AppOwnedApprovalRecord:
    """Hồ sơ phê duyệt kịch bản thuộc quyền sở hữu của ứng dụng (Immutable Value Object).
    
    Quy định:
    - Đúng 6 fields chuẩn tắc: approval_id, proposal_id, proposal_revision, approved_at_iso,
      accepted_omission_ids, approval_scope.
    - Bất biến hoàn toàn (frozen dataclass, tuple immutable).
    - Validation toàn diện thực thi thống nhất tại constructor.
    - Cấm cờ tự phong duyệt từ AI hoặc mở rộng scope ngoài ENTIRE_PROPOSAL.
    """
    approval_id: str
    proposal_id: str
    proposal_revision: str
    approved_at_iso: str
    accepted_omission_ids: Tuple[str, ...] = ()
    approval_scope: str = VALID_APPROVAL_SCOPE

    def __post_init__(self):
        # 1. approval_id & proposal_id
        aid = _validate_str(self.approval_id, "approval_id", allow_blank=False)
        object.__setattr__(self, "approval_id", aid)

        pid = _validate_str(self.proposal_id, "proposal_id", allow_blank=False)
        object.__setattr__(self, "proposal_id", pid)

        # 2. proposal_revision: 64-hex lowercase
        rev = _validate_revision_hex(self.proposal_revision, "proposal_revision")
        object.__setattr__(self, "proposal_revision", rev)

        # 3. approved_at_iso: ISO-8601 có timezone
        ts = _validate_iso_timestamp(self.approved_at_iso, "approved_at_iso")
        object.__setattr__(self, "approved_at_iso", ts)

        # 4. accepted_omission_ids: tuple unique str <= 128
        omits = _validate_accepted_omission_ids(self.accepted_omission_ids, "accepted_omission_ids")
        object.__setattr__(self, "accepted_omission_ids", omits)

        # 5. approval_scope: chỉ 'ENTIRE_PROPOSAL'
        if type(self.approval_scope) is not str or self.approval_scope != VALID_APPROVAL_SCOPE:
            raise ValueError(f"ERR_INVALID_SCOPE: approval_scope phải là {VALID_APPROVAL_SCOPE!r}, nhận: {self.approval_scope!r}")
        object.__setattr__(self, "approval_scope", VALID_APPROVAL_SCOPE)

        # 6. Byte budget check trên canonical JSON UTF-8
        plain_dict = _serialize_plain_approval_dict(self)
        canonical_bytes = _encode_canonical_approval_bytes(plain_dict)
        if len(canonical_bytes) > MAX_APPROVAL_RECORD_BYTES:
            raise ValueError(
                f"ERR_BYTE_BUDGET_OVERFLOW: Dung lượng canonical JSON ({len(canonical_bytes)} bytes) "
                f"vượt quá MAX_APPROVAL_RECORD_BYTES ({MAX_APPROVAL_RECORD_BYTES} bytes)"
            )


def create_app_owned_approval_record(
    proposal: EditPlanProposal,
    *,
    approval_id: str,
    approved_at_iso: str,
) -> AppOwnedApprovalRecord:
    """Tạo bản ghi phê duyệt thuộc quyền sở hữu của ứng dụng (AppOwnedApprovalRecord) cho bản đề xuất đã duyệt.
    
    Quy định:
    - Chỉ chấp nhận instance của EditPlanProposal (loại trừ mapping hoặc lookalike object).
    - Tái xác thực proposal qua normalize_edit_plan_proposal để bảo đảm sở hữu an toàn (owned snapshot).
    - Tự động trích xuất proposal_id và tính toán proposal_revision chuẩn tắc từ snapshot.
    - accepted_omission_ids tự động lấy danh sách demand_id của toàn bộ proposed_omissions theo đúng thứ tự.
    - Caller chỉ được truyền approval_id và approved_at_iso; không cho phép ghi đè hash, omissions hay scope.
    """
    if not isinstance(proposal, EditPlanProposal):
        raise ValueError(f"ERR_INVALID_TYPE: proposal phải là EditPlanProposal, nhận: {type(proposal).__name__}")

    owned_proposal = normalize_edit_plan_proposal(proposal)
    revision = compute_proposal_revision(owned_proposal)
    omission_ids = tuple(str(item["demand_id"]) for item in owned_proposal.proposed_omissions)

    return AppOwnedApprovalRecord(
        approval_id=approval_id,
        proposal_id=owned_proposal.proposal_id,
        proposal_revision=revision,
        approved_at_iso=approved_at_iso,
        accepted_omission_ids=omission_ids,
        approval_scope=VALID_APPROVAL_SCOPE,
    )


def approval_record_to_dict(approval: AppOwnedApprovalRecord) -> dict[str, object]:
    """Xuất bản từ điển tươi mới chứa đúng 6 fields JSON primitives của AppOwnedApprovalRecord."""
    if not isinstance(approval, AppOwnedApprovalRecord):
        raise ValueError(f"ERR_INVALID_TYPE: approval phải là AppOwnedApprovalRecord, nhận: {type(approval).__name__}")

    # Re-validate toàn diện để bảo đảm không bị mutation bypass
    validated = AppOwnedApprovalRecord(
        approval_id=approval.approval_id,
        proposal_id=approval.proposal_id,
        proposal_revision=approval.proposal_revision,
        approved_at_iso=approval.approved_at_iso,
        accepted_omission_ids=approval.accepted_omission_ids,
        approval_scope=approval.approval_scope,
    )
    return _serialize_plain_approval_dict(validated)


def verify_proposal_approval_binding(
    proposal: EditPlanProposal,
    approval: AppOwnedApprovalRecord,
) -> bool:
    """Xác minh liên kết toàn vẹn và bất biến giữa EditPlanProposal và AppOwnedApprovalRecord.
    
    Quy định:
    - Trả về True CHỈ KHI:
      1. Cả proposal và approval đều đúng nominal type.
      2. Cả hai đều vượt qua kiểm tra re-validation nghiêm ngặt.
      3. proposal_id khớp chính xác tuyệt đối.
      4. approval_scope khớp đúng 'ENTIRE_PROPOSAL'.
      5. accepted_omission_ids khớp chính xác tuple demand_id của toàn bộ proposed_omissions theo đúng thứ tự.
      6. proposal_revision khớp chính xác mã băm SHA-256 tính từ snapshot hiện hành của proposal.
    - Bất kỳ sai lệch, dữ liệu hỏng, thay đổi nội dung (stale) hoặc ngoại lệ đều trả về False có kiểm soát.
    - Zero side effects: Không thay đổi trạng thái, không hủy bỏ dữ liệu, zero I/O.
    """
    if not isinstance(proposal, EditPlanProposal) or not isinstance(approval, AppOwnedApprovalRecord):
        return False

    try:
        owned_proposal = normalize_edit_plan_proposal(proposal)
        validated_approval = AppOwnedApprovalRecord(
            approval_id=approval.approval_id,
            proposal_id=approval.proposal_id,
            proposal_revision=approval.proposal_revision,
            approved_at_iso=approval.approved_at_iso,
            accepted_omission_ids=approval.accepted_omission_ids,
            approval_scope=approval.approval_scope,
        )

        if validated_approval.proposal_id != owned_proposal.proposal_id:
            return False

        if validated_approval.approval_scope != VALID_APPROVAL_SCOPE:
            return False

        expected_omission_ids = tuple(str(item["demand_id"]) for item in owned_proposal.proposed_omissions)
        if validated_approval.accepted_omission_ids != expected_omission_ids:
            return False

        expected_revision = compute_proposal_revision(owned_proposal)
        if validated_approval.proposal_revision != expected_revision:
            return False

        return True
    except Exception:
        return False
