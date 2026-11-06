# Review G01-01A-R1 — Codex

Đã đối chiếu code và chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_asset_dto.py tests/test_storage_ingest.py -q
36 passed in0.59s, exit0.

Các probe trước đã đạt: unhashable source_kind trảFalse, managedlibrarypath cóguard, linkedrelativepath reject, reservedID reject, UIlegacy sub giữambient vàInfinity vềNone, runtime refs khôngdeepcopytoànrecord, standardlibrarytyping.

Còn đúng3lỗhổng strict cần đóng trước khi chốt:
1. Builtin hợp lệ + pack_id={'bad':'type'}: validateTrue. pack_id chỉ được kiểm ởnhánhmanaged, chưa kiểm optional field ởnguồnkhác.
2. Categorytemplates, bỏtemplate_type: validateTrue vàto_persisted_record xuất payload thiếu subtype. strictnewrecord khôngđược dựa fallbacktext củaUIlegacy.
3. duration_sec=10**1000: validateTrue dùfloat(value) OverflowError. Numeric seconds của DTO cần biểu diễn hữu hạn theo kiểu float mà downstream dùng;10**300 vẫn cóthể hợp lệ, khôngđặt cap thời lượngkinhdoanh tùy ý.

UIadapter vàconsumer hiện không có lỗi mới được tái hiện trongreview này. Không mở rộng cácđường nhập/schema/QSettings. GiaoR2 nhỏ rồi cho phép viết01B-D trong cùngworkorder;01Bproduction chưa được phép.
