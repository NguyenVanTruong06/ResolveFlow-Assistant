# Review G06-06C1b-D — 2026-10-29

Report SHA256 `c3903c66365fca32d8975e8bbe722550b82dd50d7191894ae4f6b839d2475a70` đã đối chiếu đúng. Hướng proposal/approval và D01–D03 được nhận với ràng buộc dưới đây. Review này chỉ đọc docs/source; không chạy pytest/app/model/Resolve.

## Những điểm cần sửa khi triển khai, không thêm vòng design-only

1. `DemandMatchEntry` thực có `result`, và candidates nằm trong `entry.result.candidates`; không có `candidate_assets`. Source kinds hiện có managed/linked/builtin/generated, không user/catalog. Không gọi API phantom SourceResolver.find_first. Các tuyến legacy chưa được sửa vẫn deferred, không sử dụng chúng trong snapshot mới.
2. Schema segments rút gọn bỏ `global_hook`, strategy_summary, target_platform và các trường khác của Slot1; kinetic rows rút gọn mất pages/words/style. Vì vậy không thể nói hash rút gọn đã bao phủ toàn bản dựng. Phải giữ đầy đủ JSON Slot1/Slot2 hiện có, bao gồm trường chưa dùng trong slice này, không làm projection mất dữ liệu.
3. `manifest_id` ngắn hiện hành không là full file proof và không bao phủ mọi thay đổi path/duration/FPS. Proposal cần capture source-binding metadata từ caller, hash cùng nội dung; đây vẫn chỉ snapshot, không kiểm chứng source live. Assignment cần giữ origin/source/pack metadata để sau này phân biệt hai kho cùngassetID; không chỉ asset_id+enum giả.
4. Revision không tự hash chính nó. Không thêm clock/random IDs trong pure core. Derived creative signature/revision được tính từ payload, không tin digest AI tự khai. UUID/time approval nếu cần phải do owner cung cấp sau; slice đầu chưa viết approval factory.
5. Omissions phải gắn từng demand, không chỉ count. Hai omissions khác nhau cùng count không có nghĩa cùng phạm vi duyệt. Demand không được đồng thời assign và omit. Không dùng nhãn ENTIRE_PLAN_BATCH để khiến bản dựng toàn dự án bị hiểu là batch draft có thể chạy; scope duyệt sau sẽ là toàn proposal được bind chính xác.
6. Các fixtures approval rút gọn/counts/digest minh họa không là payload normalize đầy đủ hoặc hashes đã đo. Hiệu đính nhãn/fixtures theo code thực khi hoàn thành slice; đừng viết test hợp thức hóa dữ liệu không đầy đủ rồi claim toàn approval đã xong.

## Lát cắt được cấp: G06-06C1b1-I

Đúng một pure module mới `src/core/contracts/edit_plan_proposal_contracts.py`: owned bounded **EditPlanProposal snapshot**, C1a creative context, full raw Slot1/Slot2 và source-binding metadata, proposed assignment/omission records; normalize/export/hash. Chưa AppOwnedApprovalRecord, event owner, state manager hoặc geometry/consumer. Không tạo sáu DTO rút gọn rồi mất metadata. Tên/public shape chính xác tại workorder là ràng buộc triển khai.

Snapshot giữ đề xuất; hash không chứng minh đủ semantic constraints, geometry, file availability, user approval hay permission mutation. Reference-only và storedURLs vẫn không cấp footage/network authority. C1a đã accepted, không mở lại nó nếu chưa có prerequisite cụ thể. Giữ D01–D03, không user questions mới/Resolve/G07/shutdown.
