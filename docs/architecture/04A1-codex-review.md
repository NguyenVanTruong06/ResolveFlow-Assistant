# Review G04-04A1-I

Trạng thái: CHANGES_REQUESTED — một residual bug có probe tái hiện, không mở lại toàn task.

Codex chạy đúng lệnh:
`QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_dock_profile_taxonomy.py tests/ui/test_edge_dock.py tests/ui/test_pack_surface_feedback.py -q`
Kết quả: **33 passed in 4.12s, exit 0**.

Đã đọc helper và profile restore: custom taxonomy được giữ, shared helper, một lần taxonomy/rebuild, profile rỗng giữ special page. Không kết luận 100% coverage hoặc desktop acceptance. Test offline hiện mock taxonomy(), chỉ chứng minh sử dụng kết quả offline-provider; không chứng minh lại snapshot IO thực. Báo cáo có line numbers/test split không khớp hiện tại; yêu cầu cập nhật theo output thực thay vì suy tổng.

## Residual R1 — set_assets giữ sub đã mất
Read-only offscreen probe (Resolve native bị chặn, taxonomy fake; không ghi kho):
1. Asset p có tab/category probe_custom, sub Old, storage_managed=True.
2. Chọn category probe_custom, sub Old.
3. set_assets thay asset p bằng cùng ID/category nhưng sub New.
4. Kết quả: `navsub=Old proxysub=Old available=['Tất cả', 'New'] rows=0`.

Root cause: CategoryNav.set_categories chỉ reset sub khi category mất; EdgeDock.set_assets chỉ đồng bộ category, không kiểm tra sub hiện tại còn trong effective definition. Vì category giữ nguyên, set_category không reset filter. _on_profile_changed có valid_subs check nhưng set_assets không dùng. Trường hợp category mất hoàn toàn đã probe PASS (nav text, sub Tất cả, proxy text/all, rows1); không cần viết lại nhánh đó.

Chốt sửa: set_assets phải bảo toàn sub hợp lệ và reset sub không còn hợp lệ ở cả nav/proxy sau rebuild, sử dụng conventions all/Tất cả hiện hành. Có regression chứng minh Old->New làm asset xuất hiện lại. Không mở rộng bus, performance hoặc offline architecture.

## Review R1 — ACCEPTED
Codex đọc set_assets và hai regression mới; kiểm tra sub hiện tại thuộc valid_subs, bảo toàn sub hợp lệ, reset nav/proxy và memory nếu sub mất. Regression Old->New kiểm tra rowCount1 và profile roundtrip; regression KeepMe bảo toàn lựa chọn.
Chạy lại đúng ba file ở trên: **35 passed in 4.25s, exit 0** (12 profile taxonomy +13 EdgeDock +10 surface feedback). Lát cắt 04A1 và R1 ACCEPTED trong phạm vi code/offscreen; desktop chưa xác minh riêng cho sửa profile/sub. Không suy rộng thành G04 accepted hoặc snapshot IO thực đã test. Bước tiếp: G04-04A2-D, thiết kế receiver Studio, chưa implementation bus.
