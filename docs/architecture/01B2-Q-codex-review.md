# Codex review — G01-01B2-Q-I

Kết luận: ACCEPTED đúng phạm vi add_segment endpoint quantization.
Codex đọc helper/conversion/add_segment và chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_time_contracts.py tests/test_timeline_mapper.py -q
28 passed in0.90s, exit0.

Fraction được tính trước +/-pad; giữfps_fraction từparser cũ; sourceendpoint rounding half-even; cursor/speedalgorithm vàPlacedSegment shape không đổi. Không nghiệm thu Resolve runtime từ XMLparse.

Boundary tiếp theo, không tính là làm lạiQ:
src_to_timeline vẫn round(t_src_sec*self.fps) bằngfloat.
Probe: mapperFPSFraction24000/1001, segment0..10s,pad0,speedoff; t=Fraction(3003,16000). Exact t*fps=4.5 → half-even4; method hiện trả5.
Giao01B2-M-I để sửa đúng sourcePTS→framequery, không đổi parser hoặc speedrounding. Sau đó mới review status khác.
