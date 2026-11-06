# G06-06B3-I2-P1 — Strict storage accepted; worker checkpoint còn thiếu

2026-10-29. Report `52d0593fba505a445840e216eff07a6649217d1a2ce761bc0826d82ea2b454b8` đã đọc/hash khớp. Fresh focused suite8files theo task: **204 passed in1.92s, exit0**. Codex đọc strict/legacy assets branches, worker và hai new test files.

## Evidence / scope accepted
`.bridge/ux-evidence/G06-06B3-I2-P1-CODEX-REVIEW/probes.json`:
- Real temporary StorageManager/config/registry/media qua QThread thật ->completed,matched1; không config người dùng.
- Registry thật corrupt ->worker error, sidecarNone, không empty-catalog success.
- Injected health READ_ONLY/FULL vẫn đọc populated registry.
- Strict helper post-read metadata vượt ceiling16 sau stat nhỏ ->ValueError.

Strict opt-in read **ACCEPTED_WITH_RECORDED_LIMITS trong scope hiện tại**; không mở lại storage implementation ở R1. Legacy behavior đã có focused regressions xanh. Chưa filesystem transaction/hot-unplug guarantee hoặc desktop/physical storage fault verification; số byte được bound không đồng nghĩa IO có timeout. P1 tổng thể chưa đóng chỉ vì checkpoint worker bên dưới.

## Một production residual đã reproduce
Cancel xảy ra trong storage_factory (ví dụ user hủy trong lúc StorageManager đọc config). Factory gọi worker.cancel() rồi trả storage; worker vẫn gọi storage.assets(raise_on_error=True) vì không check cancel giữa factory và provider. Probe ghi calls=['factory','assets'], terminal cancelled. Terminal đúng nhưng scan IO đáng lẽ không được bắt đầu sau cancel đã observed. Workorder đã yêu cầu checkpoint trước provider.

Sửa hẹp `asset_matching_worker.py`: check cancel sau factory và tại entry catalog_provider trước assets call, đảm bảo cancelled terminal duy nhất/no sidecar/0assets khi cancel có trước provider. Có thể dùng InterruptedError trong provider wrapper rồi existing cancel-priority handler; không retry/provider fallback hoặc sửa service. Actual read đã bắt đầu vẫn cooperative ở checkpoint sau, không hứa interrupt syscalls.

## Test harness còn phải theo workorder gốc
Worker tests dùng wait(2000/3000) không assert return và thiếu finally cancel/wait; một số lưu state lên test-function attribute (có thể stale qua lần chạy). Đổi sang fixture/helper sở hữu workers, deadline/processEvents và finally dọn đúng worker, assert native finished + isRunningFalse; local collected outcomes/counters. Không assert thời gian tuyệt đối/terminate hoặc dispose running thread.
Thêm tests checkpoint sau factory và controlled pre-provider, cancel sau matching/trước assembly hoặc terminal, matching/assembly injected failures, default StorageManager factory được khởi tạo trên worker thread. Những case này thuộc acceptance ban đầu, không feature mới. Các patch lớp/classmethod phải restore descriptors đúng.

## Next design
R1 locally green rồi làm P2 design-only, không sửa ReviewPage. Cần source-grounded caller-owned plan revision/context và pending ownership/root freshness, GUI advisory rendering, invalidation hooks và close pending/native finished. Không lặp lại design worker-only như đã có; tập trung modules receiver thật. Một report A code-tested/B design-ready. Không mở pure06B1/B2/adapter/StorageManager hoặc shutdown/Drive.
