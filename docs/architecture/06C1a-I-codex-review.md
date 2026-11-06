# G06-06C1a-I — CHANGES_REQUESTED, 2026-10-29

Report SHA256 verified: `96ac587512b9525e29ef8500aca45f764d0510975a48081b24c9f207a07fa58d`.
Codex fresh focused test: **21 passed in0.42s, exit0**. Chỉ đúng file tests/core/contracts/test_edit_plan_creative_context.py. Không app/model/Resolve.
Probe thực: `.bridge/ux-evidence/G06-06C1a-I-CODEX-REVIEW/probes.json`.

## A — Duration: uncontrolled overflow và policy tự thêm

`CreativeContext(target_duration_sec=10**400)` ném OverflowError tại math.isnan trước tryfloat. Contract yêu cầu ValueError kiểm soát.
Mã tự thêm MAX_DURATION_SEC_LIMIT một năm nên từ chối10**20 dù số đó finite positive và JSON trong budget; workorder không có capduration này. Test đang dùng10**20 để tự xác nhận cap sai, không thực sự exercise overflow10**400. Bỏ capthời lượng tự đặt; finite positive vẫn được kiểm tra theo contract và byte budget. Không bịa mặc định hoặc truncate/coerce.

## B — Typed/public boundary và ownership

normalize_creative_context trả nguyên typed input. to_dict/signature chỉ serialize không revalidate. Probe typed context có duration=-1: normalize/export chấp nhận và signature vẫn sinh hash; typed context free_brief32769bytes cũng được băm. ReferenceItem có role='footage' vẫn lọt qua CreativeContext vì chỉ isinstance và kiểm traID.

Yêu cầu cũ đã nói typed DTO không bypass. Các probe dùng instance bị sửa để mô phỏng dữ liệu typed không còn hợp lệ; không coi frozen là trust boundary. Revalidate ở publichelpers, revalidate/copy ReferenceItem members, own DTO mới khi normalize thay vì giữ referenceinput. Đây là validation input, không tuyên bố sandbox mọi arbitraryPythonmutation.

Tránh recursion: constructor hiện gọi publicto_dict, nên thêm validation vào helper không được tạo vòng constructor→to_dict→normalize→constructor. Dùng chung private validation/canonical serializer có phân tầng, không đánh dấu flag 'trusted' để bỏ kiểm tra public input.

## C — Raw schema và bounded processing

- range(0) được nhận là sequence trong constructor dù workorder chỉ list/tuple. Gating phải áp dụng cả raw/direct, không nhậnrange/bytes/mapping/str/bool/None.
- normalize chuyển raw_keep/not/urls thànhtuple trước constructor kiểm len. Probe CountedList129rules: enumerate129 rồi mới báo overflow; yêu cầu vượtlen thì0iteration. Check count trước mọi copy/iteration của sequence đó.
- Unknownkeys mixedtypes `{1:'x','extra':'y'}` làm sorted némTypeError. Reject malformed/unknownkeys bằng ValueError và diagnostic không sort so sánh các kiểu khác nhau; áp dụng cả context/reference mappings.
- Path `\\server` (hai backslash rồi tênhost, chưa có share) được blanketstartswith chấp nhận dù không phải WindowsUNCabsolute theoPureWindowsPath. GiữPurePosixPath/PureWindowsPath lexicalchecks; bỏ fallbackUNCprefix cho incompletepaths. Không normalize/mởfile hay đụngidentity thực.

## Acceptance R1 hữu hạn

Sửa đủA/B/C trong một productionmodule và testfile hiện hữu. Publichelper badinput phải từ chối có kiểm soát, valid context vẫn preserve rawstrings/order/defaults/JSONbytes/hash. Equality/overflow counts và UTF8bytebudget vẫn giữ nguyên, không thêm cap/suydiễn mới. Bổ sung testkeys/bigint/typedref/alias/preiteration và smoke purity(no filesystem/network) trong phạm vi calls cần kiểm. Chỉ chạy lại một file focused. Chưa consumer/UI/approval/geometry/handoff, không mởD mới hoặc hỏi lạiD01–D03.
