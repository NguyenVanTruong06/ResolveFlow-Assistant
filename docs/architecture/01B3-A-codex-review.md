# Codex review — G01-01B3-A-I

ACCEPTED trong phạm vi pure mutation contract.
Codex đọc module/tests và chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_status_contracts.py -q
67 passed in 0.16s, exit 0.

Mapping validation, optional counters, precedence, frozen evidence, integer precision và raw_status preservation khớp quyết định. Không có consumer integration; kết quả này không chứng minh thao tác LUT trên Resolve thật.

Bước tiếp G01-01B3-B-I: chỉ consumer InsertService._insert_lut. Không sửa producer resolve_api để thêm canonical fields; adapter đã ở contracts, gọi tại service là đủ. Đổi sơ đồ/docs nếu vẫn đề xuất đặt adapter vào resolve_api.
