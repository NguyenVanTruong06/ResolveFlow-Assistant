# G06-06A4c — Pure Project Overview Formatter Acceptance, 2026-10-29

**ACCEPTED_WITH_RECORDED_LIMITS** cho pure core formatter. Chưa nghiệm thu UI overview, macro importer hoặc toàn Milestone G06.

Report cuối đã đọc và SHA256 khớp `876a09c87edc3b9299f15625447e92411d6d7cb9274cd8b8e4ce9fa7001a1008`.

## Bằng chứng nghiệm thu

- Codex kiểm tra source `src/core/project_overview_contracts.py` và các regression tests R1/R2, không chỉ dựa báo cáo Worker.
- Chạy mới đúng hai file `tests/core/test_project_overview_contracts.py tests/core/test_batch_draft_contracts.py`: **54 passed in 0.37s, exit 0**. Không full repository suite, không desktop/model/Resolve.
- Range nhỏ 0.0001/0.0004 giữ được hai mốc riêng trong structured context; JSON example/context escape và round-trip metadata; summary không truncate.
- Gate UTF-8 cut clip_id/action/role/note trước helper và controlled Unicode error tại helper boundary. Constructor không còn đổi window_ids sai thành empty metadata; ownership reconstruction không còn blanket fallback về original alias.
- Các scalar metadata ngoài JSON dùng escaping cùng dòng. Regression kiểm một static instruction header và hai JSON blocks parse được; không tuyên bố bảo vệ tuyệt đối model khỏi prompt injection.
- Coverage derived phân biệt draft received với source gaps; zero drafts/invalid/overflow trả kết quả không copyable. Equality byte budget và SHA256 của full prompt được kiểm tra. Timestamp audit và budget parameter không thay semantic prompt.

## Giới hạn chốt

1. Pure API chỉ kiểm self-consistency của input app cung cấp và authority hình học của context. Nó không chứng minh media còn nguyên trên đĩa, ledger còn hiện hành hoặc user đã approve external JSON. Consumer phải lấy dữ liệu từ current snapshot/ledger thuộc app và kiểm stale trước copy.
2. Frozen/owned DTO bảo vệ mutation thông thường của caller collections trong các ca kiểm thử; không có tuyên bố mọi thao tác phá đối tượng runtime bằng Python đều được ngăn chặn.
3. Giới hạn 32768 UTF-8 bytes áp dụng **output prompt**, không phải token guarantee, benchmark 3h footage hay hard memory/CPU bound cho mọi input tùy ý.
4. Chưa nút UI/worker/clipboard overview, chưa importer `project_overview_draft`, persistence, matching hoặc timeline assembly. Ledger A4b vẫn RAM; old-snapshot records không được dùng làm current overview.

## Bước tiếp theo

**G06-06A4c2-D**: thiết kế consumer async/copy dựa trên hai modules `prompt_preparation_worker.py` và `review_page.py` hiện có. Chỉ design, chưa sửa production/test. Giữ nguyên contract formatter và các gate trước; publication PUBLISHED_VERIFIED và backups không mở lại.
