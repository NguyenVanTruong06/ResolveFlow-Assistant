# Review G06-06B-EVIDENCE-R1-THEN-06C-D-R1 — 2026-10-29

Report SHA256 verified: `fc546ffa002665c8f5149f007d57e8fe8c51b4ca43a8003000162cb45f2edcb0`.

## Kết luận có giới hạn

Đã đọc report, normal metrics, harness, thiết kế và agenda; xem ảnh SCREEN_CAPTURE thật. P2 code/matching semantics giữ accepted trong giới hạn cũ. Không chạy pytest/app/Resolve/model mới ở lượt review này. Các test và fault probe trong báo cáo là evidence kế thừa, không phải Codex vừa chạy lại.

Desktop visibility vẫn chưa đạt: ảnh `case1_happy_path_SCREEN_CAPTURE.png` ở cửa sổ 1400x950 chỉ nhìn thấy hàng B-roll, không thấy hàng SFX. Metrics có drawer cao124 nhưng table cao140, chưa tính title/info phía trên. Child TABLE_RENDER có thể vẽ cả phần nằm ngoài clipping của parent; không chứng minh người dùng nhìn được phần đó. visualRect nằm trong viewport của table là điều kiện chưa đủ: phải nằm trong vùng hiển thị sau clipping bởi toàn bộ ancestor.

Source kiểm tra: `AssetAdvisoryDrawer` đặt table min-height140/max-height260, nhưng parent drawer bị chia ít chiều cao trong web panel của ReviewPage. Đây là lỗi layout integration thực, không yêu cầu chụp lại ảnh cùng bố cục. Lát sửa tiếp theo được phép đụng đúng hai UI modules liên quan sau khi Worker tái hiện và đo ancestor geometry; không đổi matcher, worker hoặc dữ liệu plan.

## Thiết kế 06C: hướng đã cải thiện, chưa cấp implementation

Đã tách source/timeline FPS, advisory và assignment, missing manifest, omission approval và pure metadata vs live preflight. Nhưng còn các khẳng định không khớp code:

- `ActionGuard.acquire()` không tồn tại: API thật là hàm `check_action_availability(...)` tại action_guard.py:202. Nó trả kết quả kiểm tra, không cấp transaction lock hoặc bảo đảm authority đến lúc mutate.
- ReviewPage ở382, AssetDemandBatch ở332; không dùng72/28 như bằng chứng CODE_VERIFIED. Các line khác cần rà lại một lượt; ưu tiên đúng symbol hơn số dòng.
- Fixture revision `rev_...` không là SHA256 64-hex như định nghĩa trong bảng. App-owned approval không được chứng minh bằng JSON AI tự gửi `user_approved:true`.
- `EXECUTION_READY` trong pure layer dễ gây hiểu sai: trước implementation đổi sang tên thể hiện metadata validation/ready-for-preflight; không ngụ ý files đã có thật hay được phép mutate.
- Công thức record duration phải chốt điểm lượng tử hóa: từ giây gốc hay từ native frame bounds đã lượng tử hóa. Với cut không nằm đúng frame, hai công thức không luôn tương đương. Không tự sửa mapper hoặc chọn ngầm policy. Ghi rõ quyết định này chưa chốt trong đề xuất slice.
- Agenda: tham khảo clip cần nhánh reference-only độc lập, không trộn vào nguồn được phép dựng. Điểm matching NN/100 là điểm keyword/tag hiện hành, không xác suất chọn đúng hay quality score. Mức tự động hóa sẽ do user và Codex trao đổi; không mặc định ép duyệt từng dòng hoặc tự triển khai auto-pick>=90.

Không cần thêm vòng design-only toàn diện: Worker sửa các đoạn cơ học này kèm task layout; các quyết định sản phẩm và contract mới chờ user/Codex. 06C chưa triển khai, G06 chưa đóng, không mở G07.

## Checkpoint sản phẩm

Codex đã hỏi user mức tự động hóa mong muốn: bản dựng đề xuất duyệt một lượt / chọn từng tài nguyên / tự dựng theo quy tắc opt-in. Đây là câu hỏi thiết kế, không phải quyền triển khai phần chưa chốt. Trong khi chờ, chỉ sửa lỗi layout đã chứng minh. Giữ các nhóm brief, footage riêng vs kho chung, reference và missing-asset policy để trao đổi tiếp. Không shutdown.
