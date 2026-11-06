# G04-D — Quyết định Codex

Trạng thái: ACCEPTED_WITH_BINDING_CORRECTIONS. Review code, không chạy pytest/app trong lượt thiết kế.

## Phát hiện đã đối chiếu
- edge_dock.py:set_assets bổ sung custom tabs từ assets; _on_profile_changed thay bằng profile.categories và kiểm tra saved category chỉ trong profile.categories. Custom chip có thể biến mất; chưa có bằng chứng xóa taxonomy/media. P1, không phải P0 mất dữ liệu.
- main.py:create_studio_manager chỉ nối Studio.assets_updated sang Dock; refresh_storage polling cũng chỉ cập nhật Dock. Studio đang mở có thể stale. P1; không kết luận crash/mất dữ liệu.
- StudioWindow chỉ expose assets_updated và tab_assets; TabAssets._rescan_assets quét lại bundled. Đề xuất 3 modules bus/Dock/main chưa chỉ ra receiver cập nhật all_assets/model/rail/Inspector của Studio mà không rescan. Không phê duyệt implementation đồng bộ hiện tại. Event bus không đồng nghĩa unified source.
- AssetListModel.index_of_asset_id đã có map ID. Có thể tránh quét proxy trong handler bằng source lookup + mapFromSource, nhưng không tuyên bố toàn bộ filtering Qt là O(1). Để task riêng, không gộp sửa category.

## Chính sách được chốt
1. Trên profile có thư viện: profile categories theo thứ tự gốc, nối custom taxonomy hợp lệ ở cuối; giữ label tiếng Việt và ID. Giữ cả category rỗng được đăng ký. Profile không có thư viện tiếp tục ẩn nav/grid như hiện hành; không tự đổi special page UX.
2. Giữ hybrid bundled + storage, không migration/xóa file; giữ metadata/ID hiện hành. Một notification bus tương lai chỉ invalidation/wiring, không thay nguồn chân lý JSON.
3. Offline vẫn duyệt metadata; preview/drag/insert không được thao tác media unavailable. Offline/missing/staging và root identity sẽ thiết kế ở 04C; chưa được chứng minh chỉ bởi guard offline hiện tại.

## Sửa nhận định tài liệu
- set_category hiện đã reset sub/category tag/pack; set_sub_category về all đã clear tag. Không mô tả chúng như logic còn thiếu.
- Predicate hiện tại: C AND S AND T AND P AND Q AND M. Trong S cụ thể: submatch OR tagmatch; trong T cụ thể: tagmatch OR submatch. S và T là hai điều kiện độc lập AND khi cùng bật.
- Không có benchmark mới cho <1ms hoặc giật lag; chỉ ghi mục tiêu/chưa đo. _GLOBAL_ASSET_CACHE không tự chứng minh duplicate scans.
- Phân biệt CODE_VERIFIED (đọc code) và thực nghiệm; không ghi rollback hiện hành là giao dịch crash-safe nguyên tử (journal vẫn deferred).

## Thứ tự hữu hạn
G04-04A1-I sửa category khi profile đổi (một production module). Sau nghiệm thu, chốt receiver/snapshot/generation/signal ownership trước implementation đồng bộ; không dựng library_bus.py cho đủ kiến trúc. 04B filtering/search và 04C guards còn deferred.
