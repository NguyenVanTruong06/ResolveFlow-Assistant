# G05-05C2c2-I — Codex review, 2026-10-27

Trạng thái: REVIEWED_NEEDS_FIX. Chưa nghiệm thu worker/UI V2; không đóng G05.

## Bằng chứng mới

Codex đọc report, worker, receiver/getters, tests và đối chiếu StoryCopilot.to_pipeline_events / TimelineMapper. Chạy lại đúng sáu suites trong workorder: 128 passed in 78.91s, process exit 0. Log sao lưu tại .bridge/ux-evidence/G05-05C2c2-CODEX-REVIEW/focused-tests.log.

Probe riêng chạy offscreen, nguồn binary và cache v3 thực trong TemporaryDirectory, request dùng manifest metadata; cache provider DI trả bản sao payload đã đọc verified. Không inference/network/Resolve. UI getter và apply dùng ReviewPage thật; GUI I/O bị spy, không thực hiện quét manifest. Session tự dọn. Kết quả tại cùng thư mục /probe-results.json. Đây là boundary/component evidence, không desktop hay nhận diện thoại thật.

## A — Sai hình học / thẩm quyền plan (P1)

- Worker dòng 459–467 chỉ so manifest_id khi có giá trị: plan thiếu ID vẫn success. Binding yêu cầu ID bắt buộc khi có plan.
- Dòng 483 chọn ref đầu tiên qua or, không đối chiếu các ref cùng hiện diện. Không được bỏ qua xung đột clip_id/index/name.
- Dòng 508–540 tự trừ/cộng pad, làm tròn duration và tính cursor, không sử dụng TimelineMapper đã chốt. Probe 24fps, source [0.32,0.40], pad 0.3: từ bắt đầu 0.32 được worker đặt frame 7, mapper đặt frame 8; mapper segment dài 17 frame. Đây là lỗi phụ đề lệch thật, không phải yêu cầu tối ưu mới.
- Không-plan xuất is_final_cut=False nhưng receiver vẫn ghi “Bản Dựng Cuối / 100% liên tục”. Review sửa tất cả keep thành False lọt qua any(bool(c)); cần xét trạng thái đã chỉnh sửa, không suy luận từ có ít nhất một True.

## B — Terminal evidence sai (P1)

- Check nguồn cuối ở dòng 633–675 diễn ra TRƯỚC generate_kinetic_prompt / auto_plan. Probe đổi file ngay trong formatter vẫn success; cancel trong formatter cũng success. Phải có gate nguồn + cancel sau toàn bộ xử lý, trước success duy nhất. So cả identity dev/ino/ctime_ns/mtime_ns/size như các worker đã chốt.
- Worker không gọi validate_v3_payload như report mô tả, chỉ kiểm audio_analysis_status. Payload v3 hợp lệ có producer status='error' vẫn success trong probe. Cần kiểm producer status/no_audio và mâu thuẫn trước empty transcript; không dùng falsy raw_subtitles để phân loại dữ liệu sai thành silent.
- KineticPreparationRequest deepcopy không thay thế validation primitive. run khởi tạo ctx/paths trước try, có thể mất terminal nếu payload sai. Request validation phải xong trước UI cancel/generation/state mutation.

## C — UI stale result và GUI I/O (P1)

- request/receiver/getters dùng _extract_context_signature của V1 (16 trường Source), không chứa VideoPlan, Review cuts, FPS, font, intent. Probe đổi VideoPlan vẫn getter trả OLD_PLAN_PROMPT. Phải có chữ ký riêng cho V2; không sửa yếu chữ ký V1.
- get_final_cut_words bỏ qua exception hoặc context rỗng: supplier lỗi vẫn trả words cũ. Cần fail-closed []/"".
- Probe request aspect='' ném ValueError sau khi generation đã +1 và active ID đã đổi. Validate/snapshot toàn bộ trước cancel/mutate; invalid request giữ job đang chạy và trạng thái trước.
- _apply_json_data có nhánh ClipManifest.get_or_create trên GUI khi _manifest và V1 prepared snapshot vắng. V2 AutoPlan chưa handoff manifest nên vẫn đi vào nhánh này. Spy gọi trực tiếp đường apply ghi nhận 1 call. Cần tránh I/O cho kinetic_plan hoặc handoff metadata thuần từ V2, không giả tạo V1 snapshot.
- Tooltip AutoPlan còn quảng cáo 0.05s; source_preview không được gọi là Final Cut. Đây là công bố UI thuộc contract đã giao.

## Acceptance hữu hạn

Giao R1 trên đúng hai production modules hiện hành, không mở tính năng mới hay sửa core đã accepted. Chuyển các probe trên thành regression tests, bổ sung các ca lifecycle và plan mà docstring sáu test cũ nêu nhưng thân test chưa thực sự exercise. Giữ command sáu suites; không full test. Sau code đạt mới giao smoke V2 và xác minh speech thật còn thiếu. Khi G05 thực sự đạt: G05_COMPLETE_WAIT_USER_RESUME, không tự mở G06, không shutdown.
