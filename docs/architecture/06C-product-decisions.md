# Quyết định sản phẩm AI ChunDVC — User và Codex

Ngày ghi nhận: 2026-10-29. Đây là quyết định hướng sản phẩm; không phải tuyên bố tính năng đã triển khai hay nghiệm thu.

## D01 — AI đề xuất toàn bộ, user duyệt/sửa một lượt

**USER_APPROVED.** User chọn nguyên văn:

> AI đề xuất trọn bản dựng và tài nguyên, tớ duyệt/sửa một lượt trước khi dựng.

Luồng mục tiêu cho Web, Local và API: phân tích nguồn → lập bản dựng đề xuất kèm tài nguyên → user xem/sửa và xác nhận bản đề xuất → kiểm tra điều kiện thực thi → dựng. Cách trao đổi prompt/JSON của từng chế độ vẫn khác nhau; quyết định này không tự biến Web thành API hay tự upload footage.

- AI được đề xuất cả mạch cắt và tài nguyên phù hợp có trong dữ liệu được cấp.
- Không bắt user xác nhận từng B-roll/SFX như mặc định bắt buộc.
- User có thể sửa từng mục khi cần, rồi duyệt chung bản dựng.
- Không tự dựng lên Resolve trước bước duyệt. Tạo đề xuất không phải quyền thi công.
- Không coi điểm matching NN/100 là xác suất đúng hoặc bằng chứng user đã duyệt. Không dùng ngưỡng90 làm cơ chế tự duyệt.
- Tài nguyên không có thật không được biến thành file giả; phần chưa giải quyết phải được trình bày trung thực.

**Hệ quả thiết kế cần đưa vào 06C/G07, chưa code ở task layout:** bản đề xuất và bản được duyệt phải phân biệt; liên kết approval với đúng phiên bản plan/asset assignment để không thi công một nội dung khác sau khi user đã duyệt. API/schema, UI và cách invalidation cụ thể còn phải qua thiết kế/kiểm thử.

## D02 — Đề xuất bỏ qua B-roll/SFX thiếu trong lượt duyệt chung

**USER_APPROVED.** User chọn nguyên văn:

> Đề xuất bỏ qua B-roll/SFX thiếu trong lượt duyệt chung để tiếp tục dựng thô mạch chính (Rough-cut omission có cảnh báo rõ ràng, không chặn cả bản dựng).

- AI/app giữ lại nhu cầu chưa giải quyết và đề xuất omission rõ ràng trong bản dựng để user duyệt cùng D01. Không biến phần thiếu thành đã chèn thành công.
- Lượt duyệt chung phải cho thấy mục nào thiếu, vị trí/đoạn bị ảnh hưởng và đề xuất không chèn B-roll/SFX đó. User có thể thay thế/bổ sung hoặc đồng ý dựng thô tiếp.
- Không buộc xác nhận từng mục hoặc mở thêm một hộp thoại cho mỗi tài nguyên thiếu. Chấp thuận bản đề xuất có cảnh báo là bước duyệt được chọn, không tự thi công trước khi duyệt.
- Phạm vi D02 là B-roll/SFX bổ trợ thiếu trong khi footage chính hợp lệ. Không tự mở rộng thành quyền bỏ qua footage chính, lỗi manifest/identity/time bounds, mọi hiệu ứng khác hoặc yêu cầu bắt buộc của khách. Các lỗi này cần xử lý đúng contract, không che dưới nhãn omission.
- Omission được duyệt không tự cho phép tải/generate tài nguyên thay thế, chèn placeholder/carrier hoặc đổi sang asset ngẫu nhiên.

**Hệ quả thiết kế, chưa triển khai:** omission có bằng chứng/phạm vi rõ trong proposal và liên kết với revision được duyệt. Nếu preflight phát hiện tài nguyên mới bị mất ngoài phạm vi omission đã duyệt, phải đưa thay đổi đó về lượt xem/duyệt phù hợp, không âm thầm mở rộng phần bỏ qua. Cách biểu diễn JSON/UI cụ thể sẽ được thiết kế ở06C/G07.

## D03 — Brief tự do + preset và reference local riêng; link chỉ lưu

**USER_APPROVED.** User chọn nguyên văn:

> Brief tự do + preset, nạp ảnh/video tham khảo riêng; link được lưu nhưng app chưa tự xem link (khuyên dùng cho bản đầu).

- Đầu vào dự kiến gồm brief tự do (yêu cầu khách/cá nhân, bắt buộc và cấm), preset phong cách và ảnh/video tham khảo được chọn trên máy.
- Reference được đăng ký riêng với vai trò reference-only, không tự nhập vào footage được phép dùng trong bản dựng, không trở thành B-roll hay asset assignment chỉ vì đã được nạp.
- Link hiệu ứng/video/kênh được lưu dưới dạng dữ liệu tham chiếu. Không tự mở URL, tải media, crawl kênh, xem nội dung từ xa hay nói đã hiểu phong cách trong link khi chưa phân tích nội dung.
- Phân tích nội dung reference local phải nêu phần nào dựa trên transcript/khung hình thực và phần nào là mô tả của user. Có file/link không đồng nghĩa đã phân tích; phần không hỗ trợ/chưa phân tích phải hiển thị trung thực. Việc phân tích nhịp cắt, kiểu chữ, màu, chuyển động liên tục là khả năng cần khảo sát và thiết kế, không mặc nhiên đã có từ local VLM caption.
- Cùng brief/preset/reference context được đưa vào luồng Web/Local/API ở bước chuẩn bị dữ liệu phù hợp; không tự upload media thô hoặc toàn bộ ảnh/video reference lên cloud. Payload cụ thể sẽ được thiết kế theo chức năng hiện có.
- Quyết định không mở thêm crawler, tải link tự động, mô hình mới hay dịch vụ trả phí bắt buộc. Không biến link hiệu ứng thành tài nguyên đã cài trong kho.

## Trạng thái checkpoint sản phẩm

D01, D02 và D03 đã được user duyệt. Không hỏi lại ba lựa chọn này. Các quyết định kỹ thuật về schema, capability reference, source/time binding và invalidation do Codex/Worker đề xuất trong thiết kế hữu hạn; chỉ hỏi user khi phát sinh thay đổi phạm vi/tradeoff sản phẩm mới thực sự cần chọn.

## Công việc đang được phép tiếp tục

G06-06B đã accepted trong recorded limits tại06B-gate-acceptance.md. Checkpoint ba quyết định đã hoàn tất; mở G06-06C-PRODUCT-ALIGN-D để cập nhật thiết kế hiện hữu và đề xuất một first slice triển khai nhỏ. Đây chưa phải quyền code toàn bộ06C/G07. Worker chỉ thực hiện scope workorder hiện hành.
