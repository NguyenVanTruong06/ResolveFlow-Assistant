# G04-04A2-D — ACCEPTED_WITH_BINDING_CORRECTIONS

Review dựa trên code hiện tại; không chạy app/pytest trong lượt design này.

## Điểm chốt
- Dùng receiver managed snapshot nhẹ, không rescan bundled; không bắt buộc library_bus. Chưa chốt wiring hai chiều. Thiết kế nêu signal storage_assets_changed trên Dock nhưng scope không có edge_dock.py: không thể coi đó là API thật hoặc giấu module thứ tư. create_studio_manager cũng không expose studio_window cho closure poll ngoài hàm. Wiring cần lát cắt riêng có giao diện cụ thể.
- Bảo toàn query/category/sub còn hợp lệ, không giữ sub đã mất một cách tuyệt đối. Reset về all/Tất cả và deselect nếu asset không còn/không visible. Hidden Studio có thể cập nhật trực tiếp, nhưng ~3–5ms là UNMEASURED, xóa claim.
- Deferred mở rộng offline guards ở04C; không gỡ guard hiện có hoặc đổi offline thành online. Không tuyên bố thao tác offline đã an toàn hoàn toàn.
- Receiver không emit library_assets_changed, không rescan, không IO khi caller cung cấp cả assets và taxonomy; None mới dùng provider hiện có. [] và {} là dữ liệu hợp lệ, không fallback theo truthiness.

## Data corrections
- StudioAsset.__init__ gán sub = pack_name nếu pack_name khác Mặc định; adapter phải restore sub từ record sau constructor. Giữ sub và pack hai trục riêng.
- Không giới hạn metadata vào con số11: giữ ID/tab/category, sub/tags, paths/thumbnail/preview, duration, availability, storage_managed, pack_id/source_kind/source_id/template_type và runtime/native fields thực tế consumer sử dụng. Không invent default source_kind=managed khi contract dùng giá trị khác; preserve missing/unknown. Không deepcopy runtime refs.
- Duration từ DTO có thể None; không dựng bằng chứng thời lượng3s cho media chưa biết; bảo toàn duration_sec gốc và dùng display sentinel an toàn tương thích model. NaN/Inf/bool/huge int không được làm crash hoặc trở thành thời lượng hợp lệ.
- Preserve 8 bundled lists + local_assets theo identity/content; managed replacement chỉ ở all_assets. Managed ID không được âm thầm ghi đè bundled ID; dùng policy reject rõ cho collision không cùng owner. ID trùng trong snapshot phải reject trước đổi model, không drop âm thầm.
- Signature(root,online,registrymtime) không bao phủ taxonomy-only/root UUID swap; chưa dùng làm authority bỏ qua snapshot. Cập nhật _last signature sau thành công nếu cần, nhưng chưa short-circuit receiver theo signature cho đến khi wiring design chốt identity/taxonomy revision.

## Lát cắt được giao
G04-04A2a-I: một production module tab_assets.py, adapter + receiver + rail helper optional taxonomy, tái dùng adapter ở _merge_storage_assets. StudioWindow/main/Dock và signal wiring DEFERRED. No performance claim/no two-way-sync acceptance trong lát cắt này.
