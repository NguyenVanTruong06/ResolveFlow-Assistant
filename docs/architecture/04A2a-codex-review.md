# G04-04A2a-I — CHANGES_REQUESTED

Receiver/API hướng đi được giữ, chưa nghiệm thu do nhóm hồi quy fail và validation gap.

## R1: Dock all-category search refresh
Codex chạy node tests/ui/test_pack_ingest_wizard.py::test_dock_search_refresh_updates_shared_index_and_proxy_row: **1 failed in1.71s, exit1**. Shared SearchIndex đã có freshneedle nhưng rowCount0. set_assets ép proxy category all về nav text. Đây là hồi quy thuộc04A1, không quy lỗi cho receiver mới. Worker báo đúng phạm vi là phù hợp. Sửa có gate riêng, không đổi test để giấu lỗi.

## R2: Dict non-managed ID collision
Code apply_storage_snapshot tính non_managed_ids bằng getattr(a,'id','') trong khi base_assets có hỗ trợ dict. Read-only pure receiver probe với non-managed dict id=same và managed dict id=same: **COLLISION_NOT_REJECTED 2**. Đây là vi phạm policy reject trước mutate. Cùng kiểu bất nhất ở lookup matched selected asset. Cần accessor dict/object nhất quán và regression real Qt. _last_storage_sig cũng đang thay trước validation; chuyển sau apply thành công để invalid input không đổi checkpoint.

Phạm vi sửa tiếp: đúng2 production modules edge_dock.py và tab_assets.py, giữ receiver/no bus/no wiring. Không tuyên bố desktop hoặc snapshot IO được verify bởi provider mock.

## Focused rerun kết thúc
Codex chạy nhóm quy định tests/ui/test_studio_storage_snapshot.py + tests/ui/test_pack_result_feedback.py + tests/ui/test_pack_ingest_wizard.py với offscreen/PYTHONDONTWRITEBYTECODE: **32 passed, 1 failed in43.90s, final exit1**. Đúng node Dock all-search ở trên fail; không coi12 receiver tests xanh là đủ nghiệm thu.

## Review R1 — ACCEPTED
Codex đối chiếu set_assets all-mode, accessor dict/object và validation/collision trước mutation; selected ID lookup và signature sau apply. Chạy đủ5 files theo workorder: **62 passed in43.88s, final exit0**. G04-04A2a/R1 ACCEPTED trong phạm vi receiver và hai hồi quy đã giao; không đồng nghĩa toàn G04 hoặc desktop đã nghiệm thu. Không cần chạy lại nhóm này khi chưa có thay đổi mới.
Bước tiếp: G04-04A2b-I — wiring 3 modules, không bus/service mới.
