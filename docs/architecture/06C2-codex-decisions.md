# 06C2 — Binding decisions sau design review

2026-10-29. Design trong report `ec4c6471d1a905a09c589251850ab80da50778c266141a0e2b45b7f01c75af96` đã đọc/source-checked. DESIGN_DIRECTION_ACCEPTED_WITH_BINDING_SCOPE; không cấp code whole timeline engine.

## 1. Corrections source/authority trước khi code

- Bug `_probe_and_create_clip_entry` import get_video_metadata không tồn tại đã đối chiếu source: fresh entries có nguy cơ duration0/fps30 fallback. Ghi prerequisite cho evidence supplier, không sửa trong puregeometryslice và không coi fallback là verifiedCFR.
- detect_vfr_status là heuristic; fail có thể trả is_vfrFalse với r_fps/avg_fps0. Không dùng notis_vfr làm bằng chứng CFR. Phải phân biệt unknown/failed probe với confirmedCFR và VFR.
- pts_to_frame/add_segment source-boundary code đã dùng Fraction cho một số bước; không nói mọi phép source rounding cònfloat. Phần record speed/các legacyclamp/pad vẫn là hạn chế cụ thể. Không sửa mapper hay QSettings ở slice mới.
- Không ánh xạ timeline absoluteTC sang startFrame/endFrame như design viết. Code chèn hiện có tách source start/end và recordFrame; các adapter/API semantics cần kiểm chứngG07. Pureconverter không gọi hay tuyên bố hỗ trợ InsertMedia API chưa đối chiếu.
- SourceFrameEvidence chỉ clipID/FPS/count/flag không đủ bind với file/manifest/revision, và frozenDTO không tự là verifiedsource. Fulltimelineevidence cần sourceidentity/config/policy binding ở owner sau; slice đầu chỉ toán học caller-suppliedCFR inputs, không đặt tên nó là bằng chứng đã kiểm chứng.
- Thiết kế global_hook có hai cách placement nhưng rawplan chưa chốt mode, nên không tự chọn/drop/duplicate; fulltimelinecomposer còn deferred. Không auto-retime Slot2/assettime hoặc tuyên bố semanticmustkeep đạt bằng hình học.

## 2. First slice G06-06C2a-I

Chỉ QuantizedCFRSpan và hàm quantize_cfr_video_span tính một đoạn: exact source bounds → half-even native frames → exact rational source span/speed rescale → record duration. Caller phải cung cấp FPS rational, native framecount và explicitCFRflag; không có/False thì controlledValueError có code, không fallback. Không evidence-map/IO, proposal/approval, cursor/TC/hook hay wholeplanvalidity ở đây.

Exact API/fields/tests tại workorder. Tái dùng public time_contracts APIs, không copy private rounder hoặc float-round. Inputs source_seconds phải kiểm bounds chính xác trước rounding để mốc vừa vượt EOF không được round ngược vào trong. Zero source/record span đều lỗi, không min-one-frame/clamp.

## 3. Deferred có ghi nhận

Fulltimeline composition, evidence acquisition/identity, originTC, approvedgeometryartifact binding, hook placement, audio/wordtiming và liveResolve semantics sẽ có acceptance gate riêng. Geometry derive không tự sửa proposal/approval; UI cần hiển thị đúng kết quả trước duyệt, thay đổi proposal phải review lại. Reference/creativecontext intake và singleapprovalUI vẫn chưa implemented. Không dùng purefunction unit tests để claim xử lý mọi smartphoneVFR hay3giờfootage.
