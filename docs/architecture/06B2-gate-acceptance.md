# G06-06B2 — ACCEPTED_WITH_RECORDED_LIMITS

2026-10-29. Report `63372f5021c74f90c76064da0892998d1e84529510e4cf0f81538127ac956b32` đã đọc và hash khớp. Codex đọc `matching_service.py` và tests; fresh finite suite ba files theo workorder: **97 passed in 0.38s, exit0**. Không cần vòng sửa production06B2.

## Bằng chứng bổ sung
`.bridge/ux-evidence/G06-06B2-CODEX-REVIEW/verification.json`: spy xác nhận constructor0provider, batch2requests1provider/1matcher; order/raw request IDs đúng; next batch phản ánh offline; provider OSError sau lượt thành công propagate cùng exception instance, không empty/last-good fallback; empty/duplicate không thêm provider call; caller sửa alias list sau construction không đổi public alias-only score15.

## Phạm vi accepted
Một module `src/core/matching_service.py`: dependency injection tường minh, synchronous per-batch provider bridge, structural validation/256bound/duplicateIDs trước provider, alias ownership, fresh catalog mỗi batch, delegation đến matcher06B1, immutable result tuple. Chưa sửa modules06B1, StorageManager, UI/worker, recipe hay Resolve.

## Recorded limits
Provider tests là injected in-memory metadata, không xác minh IO kho thật/desktop/Resolve. Service không tự đưa công việc sang background, không giữ cache liên batch, không subscribe signal hoặc khẳng định snapshot là filesystem transaction. Freshness phụ thuộc caller cung cấp provider hiện hành; stale StorageManager do caller giữ không được service tự sửa. Request scalar validation trước provider; category semantic rejection delegated matcher sau provider theo decisions06B2. Caller không được sửa request collection/DTO đồng thời trong một lượt; service không là sandbox chống arbitrary in-process mutation. Không tuyên bố performance/memory benchmark.

Các nguyên tắc06B1 vẫn áp: candidate advisory refs không paths/execution proof, offline opt-in không quyền chèn; ActionGuard hiện có áp tại action-time; native transitions guidance. User-visible matching chưa nối app, không gọi đây là toàn06B hoàn tất. Next design06B3 về explicit demand producer + Review consumer; G06-06C/G07/LIB-PUBLISH-03 chưa mở. G06-06A/06B1 accepted giữ nguyên, Drive/backups không đổi, không shutdown.
