# Codex review — G03-03A1-R1

ACCEPTED đúng phạm vi direct HTTP asset pack.
Codex đọc prefix/status và regression tests, chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/test_pack_http_download.py tests/test_storage_ingest.py tests/core/test_pack_ingest_transactions.py -q
64 passed in1.04s, exit0.

Shortread HTML/BOM được gom kiểm tra trước flush; pending bytes tính vào cap; valid shorttext/binary roundtrip và getcode-only200 được giữ; bỏ production mockfallback.
Prefix chỉ SNIFF512bytes; buffer thực tế có thể chứa block1MiB+511 trước flush, vẫn bounded, không mô tả memory chỉ512byte. Không khẳng định phát hiện mọiHTML/auth hoặc formatvalidation đầy đủ.
Chưa Drive/shared downloader/ZIP bounds/Starter routing. Chọn Starter bypass active làm scope kế tiếp, thiết kế producer staging và wizard lifecycle trước khi sửa3module cùng lúc.
