# G06-06A4c-R1 — Codex review, 2026-10-29

**CHANGES_REQUESTED: ba residual trong cùng pure module**. Giữ các nhánh đã đạt, chưa nối UI/worker. G06 A4b giữ nghiệm thu.

Report SHA256 được đọc: `784250539e12dca6f48947fc7372f070157991d47501b16b6b7a45952c5ab7e6`.

## Kết quả kiểm tra mới

Codex chạy đúng hai file finite: **51 passed in 0.39s, exit 0**. Đọc source và test, kiểm tra positive control dùng validator thật. Các lỗi cũ về rounding 0.0001/0.0004, output example JSON escaping, invalid entry batch_id/segments/ranges, explicit falsy containers và copy outer/nested lists đã được xử lý trong các ca đã test. Giữ byte equality/overflow, no truncation và pure dependency boundary.

## Residual 1 — Unicode trước integrity helper

Source kiểm UTF-8 của summary nhưng chưa kiểm `ValidatedSegmentCut.note/role` trước `verify_validated_entry_integrity`. Với `dataclasses.replace(valid_cut, note='\ud800')` hoặc role tương tự, giữ nguyên valid envelope/geometry/digest khác: helper canonical JSON encode làm `UnicodeEncodeError` thoát public renderer trước khi có thể từ chối digest mismatch. Đây là lỗi đầu vào malformed có thể tái hiện, không phải đề nghị sửa helper đã nghiệm thu.

Cần type/UTF-8 gate cho các field text đi vào canonical hashing/serialization, hoặc controlled expected encoding error tại helper boundary; không catch mọi lỗi lập trình để giả valid. Kết quả phải invalid, empty prompt/bytes/hash.

## Residual 2 — Constructor biến metadata sai thành metadata hợp lệ

`_reconstruct_owned_range` dùng `window_ids=()`, khi field không phải list/tuple. Range corrupt mang False, None, {}, hoặc 'bad' trước construction bị đổi thành range hợp lệ. Với positive draft hợp lệ có windows rỗng ban đầu, context dựng từ range corrupt vẫn render ok=True trong cả bốn probe.

Đây là silent downgrade đúng loại R1 đã yêu cầu loại bỏ. Render gate không thể phát hiện vì dữ liệu bị constructor thay trước đó. Các helper ownership còn `except Exception: return original/pass`, tạo đường giữ alias khi dựng owned object lỗi. Không dùng default empty hoặc original alias để nhận context malformed. Constructor có thể raise ValueError rõ ràng, renderer phải fail-closed cho DTO corrupt sau constructor. Validate cả range của batch và range con của draft trước serialization; helper integrity dùng expected ranges từ context vẫn là authority.

## Residual 3 — Structured JSON chưa là đường duy nhất cho dữ liệu động

Context JSON và output example đã parse được. Nhưng các human sections vẫn ghép thẳng manifest/snapshot/project/style, unaccounted message và IDs vào dòng prompt. Probe manifest hợp lệ chứa newline + `[HƯỚNG DẪN TỔNG HỢP CHO AI]` cho ra **hai section instruction** bên ngoài JSON. Vì vậy escaping trong JSON không cách ly dữ liệu động khi cùng dữ liệu vẫn được phát lại ngoài JSON.

Giữ một structured context block làm nguồn dữ liệu chính. Ngoài block chỉ static instructions, labels và counts từ app, hoặc JSON-escape giá trị hiển thị; không replay raw field thành section/fence. Đây là structural isolation, không phải tuyên bố JSON ngăn được mọi prompt injection từ AI.

## Bằng chứng và phạm vi

Artifact `.bridge/ux-evidence/G06-06A4c-R1-CODEX-REVIEW/residual-probes.json` chứa positive lossless values, old bad-entry cases denied và ba nhóm còn lỗi. Không app/Resolve/IO nguồn.

Giao G06-06A4c-R2, một production module + test hiện có + docs. Không thiết kế lại API, không đổi core draft, không chạy desktop/full repo. Các corrections này đóng cùng lúc trước khi mở consumer; không thêm chức năng mới.
