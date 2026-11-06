# Codex quyết định — G01-01B3-S-D

## Nghiệm thu
Phần A ACCEPTED: annotation chỉ float/int/Fraction; Codex chạy bộ time_contracts + timeline_mapper: 37 passed in 0.80s, exit 0. Không kiểm thử Resolve thật.
Phần B ACCEPTED_WITH_BINDING_CORRECTIONS, chưa đồng nghĩa duyệt toàn bộ status/coverage production.

## Quyết định bắt buộc
1. Adapter thuần lut_result_to_mutation_outcome đặt trong core/contracts/status_contracts.py, không import Resolve/Qt. Bước kế tiếp chỉ pure mutation contract, chưa consumer integration. apply_lut_to_current_clip là producer; _insert_lut là consumer dự kiến sau review, không gọi hai chỗ là một consumer.
2. Hoãn adapter InsertResult; thiếu counters thì UNKNOWN, không suy từ ok/message/raw status.
3. Counter thiếu hoặc None giữ None, không default zero. Từng counter hiện diện khác None vẫn phải int không bool và >=0; sai kiểu ném ValueError kể cả khi counter khác thiếu. Không đủ cả ba counter thì UNKNOWN.
4. Khi đủ counter: applied>0 & failed>0 -> PARTIAL_APPLIED; applied>0 & failed=0 -> APPLIED; applied=0 & failed>0 -> FAILED; applied=0 & failed=0 & skipped>0 -> SKIPPED; cả ba zero -> UNKNOWN. skipped không che failed. Raw status không ghi đè evidence.
5. Cancel sau applied=1/failed=0 với đủ counter: mutation APPLIED, job CANCELLED. Chỉ gọi PARTIAL_APPLIED khi có evidence failed>0; không đổi trục mutation chỉ vì job bị hủy. NO_OP_GUIDANCE cần evidence riêng do producer cung cấp; LUT adapter không tự tạo guidance từ zero counters/ok/text.
6. Coverage chưa được giao code: analyzed_ratio và sampled_ratio khác nhau. Ví dụ total12/sample4/analyzed0 -> analyzed_ratio=0, sampled_ratio=1/3; không gọi4/12 là semantic analysis ratio. Thiếu denominator giữ unknown (None), zero denominator không chứng minh complete. Chọn unit window cho thiết kế hiện tại; duration coverage cần interval union riêng, không nhét duration vào int counter. Mẫu frame không chứng minh mọi frame/hành vi đã được hiểu; fully_analyzed nếu dùng chỉ có nghĩa toàn bộ cửa sổ mục tiêu đã đạt tiêu chí cụ thể.
7. JobStatus không sở hữu cleanup shutil.rmtree. Chính sách cleanup thuộc ingest/job owner với an toàn partial commit/offline; không thêm I/O vào contract. Availability MISSING chỉ khi root online nhưng file mất; root offline không được suy missing.
8. Tài liệu slices vẫn còn phantom apply_lut_to_playhead_clip ở đầu, trạng thái time đang thực hiện, graph test_status_time không đúng implementation. Sửa trực tiếp các đoạn cũ; báo cáo không được nói đã loại bỏ hoàn toàn khi còn tồn tại.

## Bước tiếp
G01-01B3-A-I: pure mutation contract + tests, sửa docs theo các quyết định này. Coverage/job/availability implementation và consumer integration vẫn hoãn. Không cần một vòng design-only nữa.
