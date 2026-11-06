# Codex review — G01-01B1-T-I

Kết luận: ACCEPTED trong phạm vi helper thuần Python, chưa chứng minh runtime Resolve hoặc TimelineMapper đã đổi.

Codex chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_time_contracts.py tests/test_timeline_mapper.py -q
25 passed in1.07s, exit0.
Probe thêm: roundtrip1203signedframe trên24,24000/1001,30000/1001; signedhalf-even so với Fraction; explicit2997/100 giữ nguyên: PASS.

Nhận xét thiết kế:
- arithmeticFraction, half-even vàrangehalf-open đúng trongscope đãkiểm.
- Aliasmetadata23.98 đượcchấpnhận chohelper; explicitFraction2398/100 hoặcstring"2398/100" vẫnphải giữ tỷ lệ, không coi mọiFPSgần nó làNTSC.
- “0 codeprodmodified” phải hiểu là chưa sửa existingruntime; time_contracts.py mới chính là productionmodule. Không hứa đã sửa driftapp hay sourcePTS precision đãmất từfile.
- Không chuyển parserlegacy rộng0.02 sangstrictparsermới một cáchâm thầm. Boundary integration tiếp theo chỉlượngtửhóa nguồn trongadd_segment bằngFPSFraction màmapperđang có.
- Status/InsertResult/LUT/DFtimecode/QSettings chưa được phép sửa.

Quyết định: giao G01-01B2-Q-I, đúng một hàmproduction chính + tests/docs. Kết thúc sau characterization và kiểm tra arithmetic; chưa nối Resolve thật.
