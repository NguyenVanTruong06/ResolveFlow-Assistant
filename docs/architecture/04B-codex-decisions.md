# G04-04B-D — ACCEPTED_WITH_BINDING_CORRECTIONS

Review dựa code hiện tại, không pytest/app trong lượt design này.

## Phát hiện và quyết định
1. P1 search truncation confirmed: SearchIndex.search mặc định limit50, return results[:limit]; proxy _apply_search gọi không limit. Top50 trước facets làm mất cả kết quả51+ và có thể tạo empty state giả khi category/pack chỉ khớp ngoài top50. Ưu tiên sửa riêng.
2. Cho phép limit=0 trả đầy đủ tập matches đã tính/sắp score; giữ default50 và positive limits cho CommandPalette/callers hiện tại. Proxy gọi explicit limit=0. Không thêm trần1000 vì nó vẫn sai tính đầy đủ. Virtualization chỉ giảm rendering, không biến scoring/sort thành miễn phí; không claim <1ms.
3. Actual suggestion handler là EdgeDockWindow._on_suggested_asset, không phải _on_suggestion_clicked trong report. Header.search_text hiện là @property str, không sửa sang method vì ghi nhớ cũ. Audio preview block có thật. Chốt đề xuất UX: suggestion là locate/select/openInspector thụ động, không tự insert/play; thay đổi này tách04C để test preview riêng. Không coi mọi autoplay là P0/P1 kỹ thuật khi chưa đo ảnh hưởng, ghi UX policy change.
4. Linear scan selected-visible có thật nhưng latency chưa benchmark; mapFromSource chỉ bỏ own loop, không bảo đảm toàn filter O(1). Tách optimization ra task sau nếu cần; chưa sửaStudio/_refresh_grid.

## Lát cắt được duyệt
G04-04B1-I: đúng2production modules src/services/search_index.py và src/ui/lux/library/asset_filter.py. Không sửaedge_dock/suggestion/selection hay đổi taxonomy ID/schema. Integration test query cùng facets trên >50matches phải kiểm tra actualIDsets.

04A desktop sync đãaccepted trongscope; chuyển trang Resolve thật cònCHƯA TEST. ToànG04 chưaaccepted.
