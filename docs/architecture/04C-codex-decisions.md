# G04-04C-D — ACCEPTED_WITH_BINDING_CORRECTIONS

Đã đọc code ActionBar, SharedHoverPreview, InsertService LUT và suggestion handler. Không chạy desktop/Resolve trong lượt review.

1. Availability contract hiện hành là online/offline/missing/staging (01-data-contracts.md). Preview-only và built-in là capabilities/source types trực giao, không thay enum bằng5 trạng thái mới. Thiết kế phải chứa staging và legacy thiếu cờ.
2. LUT hiện gọi apply_lut_to_current_clip, không phải apply_look_lut timeline-grade. Native trans hướng dẫn Effects, không giảmp4/carrier. Không đưa việc xuất macro .setting hiện có trong drag thành chứng cứ native transition insertion chuẩn. Giữ insertcontracts đãchốt.
3. Không có bằng chứng lifecycle modelReset/layoutChanged/rowsRemoved->stop trong hover_preview hiện tại (rg khôngthấy); ghi gap/PROPOSED, không nói đãverified. Router code không chứng minh physicalhotplug/khôngfreeze. Stat/pathcheck không đảm bảo latency O(1) trên ổmạng/tháorời hoặc rootidentity đúng; cần guard design riêng.
4. Policy: suggestion locate/select/Inspector thụ động, không tự gọi preview_asset/play/insert. Explicit hover/Play vẫn được phép với asset phù hợp.
5. ActionBar known unavailability offline/missing/staging: disableprimary, thôngbáo Việt rõ, tune vẫn xemmetadata. Menu mutation (newtrack/paste/install) cũng phải disabled để không bypass ngaycùngActionBar; copy/favorite/tune có thể còn dùng. Legacy thiếu availability giữ backwardcompat. Khi chuyển unavailable->online phải enable trởlại/reset tooltip. Đây chỉ cached-metadata UIguard, không phải recheck root/file/service/doubleclick/hotkey guards đãhoànthành.
6. Chốt silent hover missing media (khôngoverlay/khôngplay), chi tiết explicitaction báo lỗi; implement sau task04C1 khi có design/checks tương ứng. Không yêu cầu physical unplug để unittest.

Lát cắt04C1: đúng2modules edge_dock.py + action_bar.py, không sửa SharedHoverPreview/Grid/InsertService ở bước này. Dynamic availability checks và preview invalidation lifecycle DEFERRED với owner rõ trong design; selection scanoptimization cũngdeferred.
