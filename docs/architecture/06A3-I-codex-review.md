# G06-06A3-I — Codex review, 2026-10-28

**CHANGES_REQUESTED — G06-06A3-R1.** Fresh đúng finite group6files: **201 passed in 100.56s, exit0**. Các tests xanh không bao phủ các counterexamples bên dưới. Không sửa production/tests trong review.

## A — Copy chưa có thẩm quyền snapshot/nguồn/byte đúng hợp đồng

prompt_preparation_worker.py:132–187:
- baseline_sources missing/empty thành tuple rỗng; worker không kiểm tra nguồn nào rồi success. Probe MISSING_BASELINES → True/success.
- Không đối chiếu baseline paths với req.video_paths. Snapshot baseline của file A, request paths chứa file B khác vẫn success.
- Chỉ so size/mtime (:161), bỏ ctime/dev/ino dù baseline có đủ. Probe os.replace file cùng size, khôi phục mtime, inode đã đổi → success sai.
- Copy không kiểm tra prompt byte cap/integrity. Probe mutable snapshot rendered_prompt='x'*40000 → success prompt_bytes40000 dù request cap32768.
- Snapshot được gọi là bất biến nhưng :570–598 là dict chứa tuple các dict (batches và baseline_sources), request giữ nguyên reference. Deepcopy lúc emit chỉ tách alias một lần, không làm snapshot/request bất biến. UI :2753 còn thay context_signature của producer bằng UI tuple, làm mất namespace evidence.

Cần validated owned immutable snapshot; copy bắt buộc đầy đủ ordered source inventory/identity/context/manifest, reject missing/malformed/mismatch trước IO, recheck mọi identity evidence, verify selected render bytes/signature/cap. Không chạy producer/model/planner để copy. Empty baseline không thành success. Chuỗi copy phải đúng bản prepare.

## B — UI validation/lifecycle/feedback còn sai

review_page.py:2535 kiểm tra range nhưng không strict type; selected_batch_index=True đi qua rồi dataclass ValueError tại :2601 sau khi generation đã tăng/cancel workers. Probe GEN_DELTA1. Index='0' gây TypeError thoát boundary. Hợp đồng là validate trước state mutation, 0 generation/cancel/dispatch khi invalid.

:2798–2818 không kiểm tra current combobox selection/batch snapshot identity trước clipboard. Probe request chọn0, current UI đã chọn1, active request/context vẫn khớp → clipboard bị ghi OLD_SELECTION.

:2782 bật Copy ở logical prepare success dù physical registry còn worker. Probe registry={'still_physical':object()} → Copy enabled True. Không đợi _on_prompt_worker_thread_finished nên physical-busy contract bị vi phạm.

Payload :482–492 dùng items_count/rendered_bytes nhưng UI :2763–2764/:3429 đọc item_count/prompt_bytes. Probe actual-shape item2/distinct1/bytes1234 → label 'Lô 1/1 (0 clips, 0 B)'. Summary thiếu partitions/reasons/adapter warnings; unaccounted duration chưa biết bị gọi 'chưa phân tích' không đúng. Getter mismatch trả empty nhưng stale controls/preview chưa được dọn thống nhất. Preview còn tự cắt20dòng thay vì đầy đủ chuỗi <=32KiB.

Wait helper của tests chỉ thoát sau15s mà không assert all-idle/deadline, nên không phải bằng chứng không thread leak. Thêm deadline assertion + finally cleanup và kiểm tra logical/physical riêng, không sleeps cố định để che race.

## C — Thời lượng và sản phẩm chưa trung thực

prompt_preparation_worker.py:380–400 sửa manifest duration<=0 bằng clips_data duration từ cache. Probe request manifest C07 duration0, producer row duration60 → output metadata duration60 và planned1. Đây là thay SSOT để qua A1, trái binding A1/A2; bỏ đoạn sync này. Unknown duration phải unaccounted; tests dummy không đo được media phải cấp manifest metadata hợp lệ nếu ca cần known duration.

story_copilot.py:313–325 còn hardcode clip_id C01, mốc10/25.5s và câu 'Hệ thống ResolveFlow sẽ sáp nhập và đồng bộ tự động lên DaVinci Resolve'. First slice chỉ draft từng batch, chưa merge06C/G07. Đổi sang template không fake source ID/times và thông báo giới hạn đúng chức năng; giữ global refs/bounds thực trong envelope. Không sửa V1 truyền thống trong scope này.

## Kết luận

R1 cùng3modules, không thêm framework/design gate. Giữ prepare0clipboard, source-only/full-render measurement đã có; đóng A/B/C bằng regression qua worker thật + Qt boundaries. A1/A2/G05 không mở lại. No full suite/model/Resolve/desktop/shutdown. Evidence Codex tại .bridge/ux-evidence/G06-06A3-I-CODEX-REVIEW/.
