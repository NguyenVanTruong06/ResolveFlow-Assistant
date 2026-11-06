# G05-05C2c2-R1 — Codex review, 2026-10-28

Trạng thái: REVIEWED_THREE_RESIDUALS. Chưa nghiệm thu code V2.

## Kiểm chứng mới và phần đã đóng

Đọc report, hai modules, tests mới và đối chiếu các probe R1. Chạy lại đúng sáu suites: **137 passed in 111.65s, process exit 0**. Log tại .bridge/ux-evidence/G05-05C2c2-R1-CODEX-REVIEW/focused-tests.log.

Đã thấy TimelineMapper thay cursor float thủ công; probe source [0.32,0.40] ở 24fps cho word start frame8 đúng. Missing manifest_id được chặn, ref conflict có đối chiếu, source/cancel gate đã chuyển sau formatter/planner, fail-closed getters khi supplier lỗi, kinetic_plan apply tránh GUI manifest IO. Các regression này được giữ nguyên. Không yêu cầu làm lại thiết kế hay mở thêm tính năng.

Probe bổ sung offscreen dùng tiny binary/cache v3 và manifest metadata trong TemporaryDirectory. Không inference, model, network, Resolve, hay desktop. UI worker launch dùng DI fake không chạy thread; nguồn/cache mặc định không bị sửa. Các kết quả tại probe-results.json cùng thư mục.

## Residual A — FPS / numeric semantics chưa được giữ (P1)

1. ReviewPage._sanitize_and_validate_context vẫn trả allowlist V1, bỏ fps/timeline_fps/review_cuts. request_kinetic_preparation lấy FPS SAU bước này, do đó nhánh validate mới không nhận được FPS gốc. Probe supplier fps=24 -> current context không có fps; explicit request fps=False vẫn launch worker với timeline_fps=None và tăng generation. Không thể nhận diện đổi FPS hoặc từ chối FPS sai như report mô tả.
2. Worker dùng TimelineMapper(fps=fps_rat.as_float()). Constructor mapper có legacy float snapping tolerance, nên mất hợp đồng explicit rational. Probe fps=Fraction(2399,100), duration2000, plan [0,1000], hai word [0.32,0.42] và [999.7,999.8]: worker success nhưng chỉ còn1/2 words, do 23.99 bị snap về24000/1001. Truyền fps_rat.as_fraction() vào mapper hiện hành; không sửa core.
3. Các đoạn dùng float(speed or 1.0) chưa validate finite/type. Probe speed=NaN vẫn success. Cần strict finite numeric trước float/or và trước mapping; bool không phải speed/time hợp lệ. Áp dụng cho hook và body.

## Residual B — Precedence producer error vẫn chưa đầy đủ (P1)

Worker dùng audio_analysis_status OR status OR producer_status. Payload v3 có audio_analysis_status='completed' và status='error' vẫn success; lỗi ở trường sau bị che. Đây là ca producer error ưu tiên mà R1 đã yêu cầu, không thêm contract mới. Kiểm độc lập tất cả evidence hiện diện: bất kỳ error phải deny; bất kỳ no_audio evidence mâu thuẫn nonempty transcript phải deny. Không suy luận thành công từ giá trị đầu tiên.

## Residual C — Fingerprint và validate-before-mutate còn lỗ hổng (P1)

1. _extract_v2_context_signature chỉ chọn một số trường plan; path/current_path là ref worker đang tiêu thụ nhưng không nằm trong fingerprint. Probe đổi path A -> B giữ nguyên source context/range thì signature vẫn bằng nhau. Phải fingerprint primitive snapshot plan đầy đủ/deterministic, giữ thứ tự list; không chỉ selected fields. Manifest authority thực và các trường V2 phải thống nhất giữa request/getter/terminal.
2. Request dataclass vẫn được tạo SAU cancel/generation/active state. Probe _video_plan_data=['bad'] (JSON hợp lệ nhưng sai schema) gây TypeError thoát Qt slot, generation+1 và active ID đổi. Probe runtime object deepcopy lỗi cũng tương tự. Validate/snapshot và tạo request xong trong try trước mutation/cancel; invalid input giữ job cũ/clipboard/plan và không để exception thoát slot. Đặc tả primitive DTO không chỉ isinstance(dict)+deepcopy.

## Bước tiếp theo hữu hạn

R2 chỉ sửa hai modules hiện hành và regression cho đúng các residual trên. Không đổi module core, không mới feature, không full tests. Sau code đạt mới giao desktop V2/speech thật cần cho G05. Chưa G05_COMPLETE_WAIT_USER_RESUME, không G06 và không shutdown.
