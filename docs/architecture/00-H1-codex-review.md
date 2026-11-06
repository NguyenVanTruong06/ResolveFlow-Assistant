# Review G00-H1-R1 — Codex

Kết luận: ba lỗi P1 được nghiệm thu trong phạm vi code/offscreen. Không coi đây là nghiệm thu thiết bị hoặc Drive thật.

Codex tự chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/ui/test_pack_ingest_wizard.py tests/test_storage_ingest.py -q
=>31 passed in23.62s, exit0. Không chạy lại fullsuite hoặc13testDock vì các probe và nhóm31đã bao phủ phần sửa.

Probe thật trên thư mục tạm:
- Reject trước owned timer: prepare_calls=0, job=None.
- Settings configureRoot→cancel: library_changed=1, pack_committed=0, registry.packs=0. Phân biệt hai tín hiệu đã đúng.
- Ingest freshneedle→Dock refresh: shared index matches=1, proxy rows=1, giữ đúng index instance.

Residual P2 tái hiện:
- Commit nguồn1, cancel nguồn2: registry ghi đúng nguồn1 nhưng _handle_dropped_packs gọi _toast.show_info(...,timecode=''). ChunDVCToast không có show_info. Catch ngoài hiện “Lỗi nhập gói: ... no attribute show_info”, sai với dữ liệu đãcommit.
- Test test_commit_pack1_then_cancel_pack2_reports_partial chỉ assert registry/count, chưa assert thông báo nên bỏ sót.

Quyết định: R2 sửa đúng call thông báo và regression, dùng show_message với args thật. Không mở rộng hotfix. Khi node test đạt, Antigravity tiếp G01-D trong cùng work order; G01 chỉ thiết kế, chưa sửa schema/QSettings. Codex sẽ review hai đầu ra ở lượt tiếp.
