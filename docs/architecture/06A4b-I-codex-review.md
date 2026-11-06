# G06-06A4b checkpoint review — 2026-10-29

Trạng thái: CHANGES_REQUESTED. User đã yêu cầu tiếp tụcG06; publication giữPUBLISHED_VERIFIED, không mở lại kho/Drive. Worker checkpoint192tests không đồng nghĩa Codex đã nghiệm thu.

## Bằng chứng hiện hành
Codex chạy đúng focusedcommand cũ sau khi nguồn demo đã dọn:192passed in46.18s,exit0. Không fullsuite/model/Resolve/desktop. Evidence probes tại .bridge/ux-evidence/G06-06A4b-CODEX-REVIEW/{core-probes.json,geometry-probes.json,terminal-probe.json}.

## A — Strict JSON/schema chưa strict (P1)
decode_strict_batch_draft_json tìm dấu{...} và cắt bỏ surroundingtext; PREFIX <json> SUFFIX và text ngoài một codefence đều accepted. Không đúng wholeobject/wholefence contract.
Validator: action=True bị biến thànhkeep; role=dict được str(); keep speedInf bị bỏqua; source_window_ref khôngtồntại vẫnaccepted khi range.window_ids rỗng; end_sec=10**400 némOverflowError. manifest_id=' M ' được accepted khi expected='M' do strip. Hai summary khác nhau sau4096ký tự bịtruncate thành cùngdigest. Đã tái hiện, không phải suy đoán.

## B — Geometry integrity/immutability (P1)
BatchSourceRange cho phép partition_index==total_partitions trong khi A1 zero-based range(n_parts); window_ids tuple chứa dict mutable được chấp nhận và thay đổi theo externalalias. float(bigint) chưacontrolled. Worker snapshotnormalizer tin typedBatchSourceRange và str()windowIDs.
compute_snapshot_id dùng delimiters/join, chưa canonicalstructured nhưcheckpoint ghi: windowIDs('a,b','c') và('a','b,c') tạo cùngsnapshot_id với khácgeometry. Geometry-backed snapshots phải có proof canonical mới; legacy snapshots khônggeometry vẫn giữ copycompatibility/intake denied.

## C — Ledger authority/lifecycle (P1)
Codex gọi production terminal trên fixturememory với ownedpending/envelope hợp lệ nhưng validated_entry.segment.end_sec=9999 ngoài range[0,10] vàdigestcũ; entry vẫn được lưu ledger. UI dựng DTO trực tiếp từ dict, chưa bind/recheck innerIDs/geometry/segments/digest/counters với pending/currentsnapshot. Không được tin nhãn validated_entry như proof.
Replacement dùng blocking QMessageBox.question, checksource/currentselection trước dialog nhưng không kiểmtra lại sau nestedloop. Workorder gốc yêu cầu unchecked opt-in/nonblocking. Terminal còn setsubmitEnabledTrue trước physicalQThread.finished; malformeddirectrequesttext được constructor reject saugeneration/cancelmutations. Đây là CODE_VERIFIED gaps cần finite regression.

## Giao việc
G06-06A4b-R1 đúng3functionalmodules: batch_draft_contracts.py, prompt_preparation_worker.py, review_page.py + tests/docs. Không sửa A1/A2/StoryCopilot/G05/DirectorWindow/library modules; director_state message đã đủ, giữ nguyên. Sửa3nhóm theo binding06A4b hiệncó, không feature mới/synthesis/matching. Chưa chuyển06B/06C hoặc desktopacceptance khi3gatechưađạt.
