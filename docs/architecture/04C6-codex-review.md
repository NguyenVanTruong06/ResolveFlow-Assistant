# G04-04C6-D — Codex review (2026-10-27)

Kết luận: ACCEPTED_WITH_BINDING_CORRECTIONS cho hướng thiết kế; không phê duyệt toàn bộ pseudocode preview. Chưa có code/test mới để nghiệm thu. Đã đối chiếu report, action_guard.py, asset_grid.py, hover_preview.py, insert_service.to_asset_dict và TransitionStylePreset.

1. Preview policy chưa nhất quán: bảng mục 2 deny mọi offline/missing, nhưng mục 4–5 cho phép proxy chỉ bằng isfile. File tồn tại không chứng minh ownership/cache authority. Proxy ngoài root, origin cũ/same-path ID swap và offline proxy còn thiếu hợp đồng. Không implement preview trong bước này; không được bỏ source/origin hoặc đổi category để vượt guard. Không sửa insert policy: managed/linked hiện có fresh revalidation, không bị phủ bởi bảng deny cache chung.
2. Mục 3.2 dùng “TextStylePreset hoặc source_kind builtin/generated” là quá rộng. Source kind không phải capability generator. Chỉ export đúng TextStylePreset runtime thực, không tạo preset mặc định từ ID/name. Runtime callable generate bất kỳ không đồng nghĩa tương thích FusionSettingGenerator.
3. TransitionStylePreset thực có exporter nhưng core guard hiện coi loại này là native guidance. Không tự mở ngoại lệ cho drag bằng type name. Slice này deny mọi transition theo policy nhận diện hiện hành, trước physical-file branch; cả preview MP4 có thật cũng không thành drag source. Export Fusion transition riêng phải có thiết kế/capability được duyệt sau. Không suy từ macro exporter sang việc Resolve hỗ trợ native cut transition.
4. Legacy helpers trong TabAssets chưa được bảo vệ; Inspector preview và drag cũng chưa được cover chỉ vì grid dùng shared helper. Giữ residual rõ ràng, không tuyên bố toàn ứng dụng đã guard.
5. Chọn một consumer family: grid drag. G04-04C6a-I chỉ action_guard.py + asset_grid.py. Preview và offline proxy authority deferred. Không cần thêm vòng thiết kế toàn bộ hoặc chạy desktop Resolve cho bước này.

Evidence boundary: source review; không chạy pytest/app trong lượt thiết kế này. Bằng chứng 04C5 trước đó giữ nguyên phạm vi, không bị mở lại.
