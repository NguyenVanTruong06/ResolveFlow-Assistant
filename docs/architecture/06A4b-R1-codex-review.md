# G06-06A4b-R1 — review bản cập nhật mới nhất

Report hiện hành SHA256 bf0b976e5fb944e893d0f5390a7de16e59a04fdd7dff75965d8847865bd3f075 (thay report43f248... trong cùng lượt). Trạng thái: CORE_RESIDUALS_VERIFIED / UI_CHANGES_REQUESTED.

## Đã xác nhận
Codex chạy lại đúngsuite trên code mới nhất:201passed/49.95s/exit0. Tái chạy probes: nan_counter/bool_count/bool_speed/list_role đều rejected, geometrybigint controlledValueError, headersnapshotcollision=False, oldgarbage denied. Artifact .bridge/ux-evidence/G06-06A4b-R1-CODEX-REVIEW/residual-probes-latest.json. JSONstrict/opt-incheckbox và canonicalgeometry mới giữ nguyên, không mở lại3nhóm đã sửa.

## UI residual1 — valid echo bị từ chối (P1, reproduced)
request_prompt_preparation gán expected_binding_mode=user_associated_current_snapshot mỗi khi associate=True. Nhưng core đúng hợp đồng sẽ trả echo_matched khi payload có snapshot_id khớp. Nút UI luôn gửi associate=True nên optional valid echo bịIntegrityGate reject.
Codex chạy production ReviewPage→prepareworker→intakeworker trên QThread thực, cache/manifest/sources synthetic metadata fixtures độc lập, không inference/decoder; payload cósnapshot_id đúng. Kết quả valid_echo_plus_association_accepted=False, statusIntegrityGatefailed. Artifact echo-positive-probe.json. Đây là positive route regression cần sửa, không phải chống maliciousJSON mới.

## UI residual2 — pending required proof vẫn có fallback
Terminal truyền expected_raw_payload_sha256=pending.get(...) OR val_dict.raw_payload_sha256 và expected_binding_mode=pending.get(...) OR result.binding_mode. Thiếu/malformedownedpending thì lại tin kết quả tự khai. Phải yêu cầu capturedrawhash tồn tại/strict và bindingpolicy trongpending; không duy trì fallback chỉ để tests cũ pass.
Một số fixture terminal tests trước thiếu project_name nên bịreject ngoài integritygate. Workerđã thêm field trongbảnmới; R2 cần positivecontrol/gatespy bảo đảm testđãthật sự tới intendedboundary, đồngthờibổsung pendingproofbắtbuộc. Không tự suy bindingmode bằng parseJSON trênGUI.

## UI residual3 — replacement race khi entry biến mất
Old-digest comparison chỉ chạy khi existing_entry còn tồn tại. Expectedold≠None nhưng currententry bịxóa thì vẫn cóthểpublish lại entrycũ. So currentdigest (None nếuabsent) vớicapturedold vôđiềukiện trướcpublish; preserve lastgood và không resurrectstale. CODE_VERIFIED, sửa cùngmột UIfile.

## Quyết định
G06-06A4b-R2-THEN-AUTO-UI, production scope duy nhất review_page.py + tests; không thêm design/core/workerrefactor. A sửa3UIresidual, B componentdesktopharness hữu hạn nếu A xanh. Chưa06A4b gateaccepted hoặc06B/06C/G07. Publication/2backups giữ nguyên.
