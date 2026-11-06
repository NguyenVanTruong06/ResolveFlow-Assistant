# Review G01-01A-I — Codex

Phạm vi đạt: module mới không Qt/IO, consumer chỉ ui_assets, ID cũ và mapping cơ bản giữ được. Chưa nghiệm thu validation/legacy compatibility.

Codex chạy tests/core/contracts/test_asset_dto.py + tests/test_storage_ingest.py: 37 passed in 0.58s, exit0.

Probe trên managed record đầy đủ, chỉ đổi một field:
- source_kind=[]: validator ném TypeError thay vì trả False+errors.
- library_path='../../outside.mp4': validator và serializer chấp nhận.
- subcategory=object(): validation True; json.dumps(to_persisted_record(...)) ném TypeError.
- category='all': validation True dù đây là reserved UI ID.
- linked external_path='relative/file.mov': validation True dù phải là absolute path.
- UI legacy sub='ambient',duration_sec=Infinity: mất sub thành 'Tất cả', duration vẫn Infinity.

Cùng phạm vi cần sửa: typing_extensions không cần trên Python3.12; serializer đang normalize0/-1 dù chỉ legacy read được làm; deepcopy toàn UI record có thể làm hỏng runtime handle; optional allowed fields chưa validate hết.

Quyết định: giao G01-01A-R1, chỉ module DTO + regression + docs liên quan. Chưa giao01B/01C hoặc consumer mới.
