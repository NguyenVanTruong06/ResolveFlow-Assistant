# G07-07A2a — Codex review, 2026-11-02

**CHANGES_REQUIRED**, không nghiệm thu backend và chưa mở 07A2b. G07-07A1 giữ accepted.

Report SHA-256: `b309920044bcf3567e4123f7cd10e7c31d2ffbf38b2b5724b66445152f2ee010` đã khớp; đúng hai module mới, hashes khớp report. Codex chạy nguyên nhóm năm file: **71 passed / 2.85s / exit 0**. Test log mới và probe source/log/JSON: `.bridge/ux-evidence/G07-07A2a-CODEX-REVIEW/`. Probe exit 0 nghĩa harness chạy xong, KHÔNG nghĩa các negative cases đạt: JSON ghi những lỗi đã xác nhận.

Positive source-only (0 storage calls) và managed assignment đúng provenance đều completed/checks_passed=True/ready=False. Không thấy đường thi công Resolve/UI mới. Giữ các hành vi này.

## A — P1: authority failures bị báo đạt

- `source_catalog_readiness.py:449-475`: provider trả None không ghi lỗi; nhánh xử lý bị bỏ qua, all_catalog_ok vẫn True → checks_passed=True, zero assignment observations.
- `:509-530`: lỗi chụp marker bị nuốt hoặc trả None identity. Final gate `:890-901` chỉ kiểm tra khi initial identity không None, làm mất quyền kiểm tra. Probe PermissionError ở initial marker vẫn báo True.
- Direct public inspector default storage_context_reader=None chụp disk config nhưng không so với loaded config/root/library identity. Probe disk library_id DIFFERENT_LIBRARY, loaded lib_test_01 vẫn True. Worker default có context reader không sửa được public core default này.
- `:553-564, :630-647`: str()/strip()/optional source_kind khiến thiếu catalog.source_kind được coi khớp; probe xóa source_kind vẫn True. Exact ID/type/provenance cần kiểm tra raw, không ép kiểu hoặc dùng metadata yêu cầu đè catalog.

## B — P1: owned request/result và terminal chưa đúng cam kết

- Request ID chỉ kiểm tra not request_id (`:54`), nhận whitespace-only. approval_record_to_dict ở ngoài controlled try (`:79-82`) làm deleted approval member thoát AttributeError.
- Worker `:65` giữ chính object caller. Probe request.generation đổi sau construction làm worker đổi theo. Xóa caller approval.approval_id sau construction → worker.run thoát AttributeError, **0 terminal**, phá exactly-once.
- Result __post_init__ (`:171`) không own nested observations/diagnostics. Caller append list làm result đổi theo. Worker so generation bằng ==, nên True==1 lọt: nominal result có generation=True, checks_passed=True, empty source observations vẫn phát completed. Cần strict result structure và owned boundary, không chỉ isinstance/equality.

## C — P1/P2: source metadata proof và cancel checkpoint còn hở

- Stat identity chỉ inode/size/mtime_ns. Probe rewrite cùng size + restore mtime, inode giữ nguyên nhưng ctime đổi: checks_passed vẫn True. Dùng cùng metadata identity dev/ino/size/mtime_ns/ctime_ns cho source/master/marker/registry; đây là stat observation, không gọi là full-content proof.
- Callback progress(100) chạy sau cancel checkpoint cuối (`:923-927`). Probe callback yêu cầu cancel → direct inspector vẫn completed/checks_passed=True. Worker có discard sau return là tốt, nhưng public core phải có checkpoint sau callback và trong các vòng metadata/catalog dài.

## Finite closure

Task G07-07A2a-R1 gộp A/B/C trong đúng hai module + hai tests hiện hành. Default IO test phải có một fixture StorageManager thật ở tmp_path, không chỉ MockStorageManager. Không sửa StorageManager/ActionGuard/contracts cũ, không UI/Resolve/content hashing. Không mở thêm design round. Chỉ khi các probes đổi sang kết quả mong đợi và positive controls giữ xanh mới xem xét 07A2b.
