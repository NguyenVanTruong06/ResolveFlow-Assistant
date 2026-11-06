# 06C — Binding decisions sau PRODUCT-ALIGN-D, 2026-10-29

Report SHA256 verified: `7a86051079b6e7396462c0afa29b3a783d545e7b402ab4f50204a22bfae837db`.
Kết luận: DESIGN_DIRECTION_ACCEPTED_WITH_BINDING_SCOPE. D01–D03 giữ nguyên tại06C-product-decisions.md; không hỏi lại user. Chưa có code06C được nghiệm thu.

## 1. Phạm vi và first slice

Luồng proposal → một lượt duyệt → preflight, omission bổ trợ và reference-only đã đúng hướng. First slice đề xuất gồm9DTOs +3adapter/geometry functions vẫn quá rộng cho một gate đầu tiên. Giao G06-06C1a-I: **một module** `src/core/contracts/edit_plan_contracts.py`, chỉ CreativeContext/ReferenceItem input contract, immutable ownership, bounded serialization và metadata fingerprint. Không proposal/approval/geometry/UI/prompt wiring trong slice này. Blueprint còn lại là PROPOSED_DEFERRED, không tạo stub để giả đã hoàn tất.

Mục tiêu nhỏ có ích: cùng một brief/preset/reference input có biểu diễn chuẩn để Web/Local/API và full proposal binding sau này dùng lại; reference-only không tự biến thành footage. Không claim feature đã hiện trong app khi chưa wiring consumer.

## 2. Quantization cho video về sau

Codex kiểm tra lại bằng Fraction: source30, timeline24, [1,2017/1000)s, speed1 → native[30,61), FormulaA24frames, FormulaB25frames. Chọn B cho **video cuts có source-native CFR/frame metadata đủ và đã kiểm chứng**: half-even boundaries, rồi rational rescale native span sang record FPS/applied speed. Dùng time contracts hiện có; không sửa mapper trongC1a.

Đây là policy dữ liệu video, không bằng chứng Resolve tự đồng bộ audio. Xóa claim audio tự canh/khớp tuyệt đối; cần kiểm chứng A/V và word boundaries trong G07. Nếu VFR, source FPS/native bounds chưa có bằng chứng hoặc quantization tạo span0/bounds ngoài nguồn thì không tự clamp/min-one-frame/bịa30fps; xử lý unsupported/unresolved/blocked tại adapter phù hợp. Semantics audio/linked clips và conversion API phải chốt ở phần execution riêng.

## 3. Corrections thiết kế đưa chung vào C1a, không thêm vòng D

- DirectorConfig thực tế có style=STYLE_VLOG và ai_engine_mode; không style='travel_vlog'/ai_engine_mode chưa có. PromptPreparationRequest có target_duration là chuỗi và các trường thực tại source; future creative_context là PROPOSED, không tự đổi các consumer cũ.
- Defaults input contract không tự suy60s hoặc travel_vlog: duration=None, preset rỗng nghĩa chưa đặt. Reference chỉ image/video theoD03, không tự mở thêm audio intake.
- User note là nội dung user cung cấp, không chứng minh sự thật; transcript/caption chỉ có evidence khi producer thực đã tạo và source/config binding hợp lệ. Không ghi mọi reference PROVEN_TRANSCRIPT/PROVEN_SAMPLE_FRAMES ngay khi nạp. Lấy mẫu theo config/budget hiện có, không tự claim1–2fps cố định.
- Chuỗi must_keep/must_not không tự chứng minh semantic compliance. Những yêu cầu như giữ câu slogan cần source-range/evidence hoặc user review phù hợp; pure schema validator không tự phát hiện ý nghĩa lời nói.
- Omission đã duyệt không mất dấu nguồn nhu cầu; primary source/identity/timing lỗi vẫn block. Approval là app-owned event gắn full revision, không tin JSONAI; NN/100 không phải confidence/authorization.

## 4. Verification boundary

Review này đọc report/docs/source, kiểm tra số học Fraction; không chạy pytest/app/model/Resolve. First slice sẽ có focused tests riêng. Không mở lại06B/usabilityG08/Drive/backups, không shutdown. G06/G07 chưa hoàn tất.
