# G05-05C2a-I — Codex review (2026-10-27)
**NEEDS_FIX — G05-05C2a-R1.** Chưa nghiệm thu C2a; không mở UI C2b.

## Bằng chứng mới
Fresh command:
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_prompt_authority_consumption.py tests/test_clip_manifest.py tests/test_story_copilot.py tests/test_visual_footage.py -q
**54 passed in 7.31s, exit0**. Đã đọc report, hai core modules, regression mới và API validator thật. Probes tmp/DI core-only, không GPU/Resolve/network.

## Nhận các phần đã làm
Unknown duration không còn60s, manifest IDs/order và eligible short-clip policy giữ; verify_full=True đã được truyền đúng API; verified miss không gọi candidate; source post-stat có clear evidence; bounded mode có UTF8cap/ValueError khi khôngfit, JSON skeleton còn nguyên trong finite tests.
Không đồng nghĩa authority/provenance/omission contract đã đạt.

## P1-A: Evidence và nhãn verified
audio_authority được gán chỉ từ verify_audio và cached is not None, không validate payload. Probe provider trả {}, raw_subtitles=None hoặc [] thiếu schema/evidence đều ra authority=verified, status=completed_empty.
Probe schema_version=99 và subs start=-3,end=-1 ->verified/transcribed và transcript='negative times'. Validated cache v3 thật dùng field cache_schema_version, không schema_version.
Ngay cả candidate missing raw_subtitles cũng rơi completed_empty vì mọi dict chưa error/no_audio cuối cùng vào nhánh clean_subs rỗng.
Test candidate_vs_verified hiện dùng MagicMock trả status/raw_subtitles mà thiếu mọi v3evidence; test v3empty chưa bảo vệ contract thật.

## P1-B: Stale machine evidence vẫn usable, provenance mất khi format
visual_context model=new vs stored model=old: build_clips_data.visual_segments vẫn chứa STALE_MACHINE_SECRET và USER_NOTE. Prompt bounded32768 vẫn chứa STALE_MACHINE_SECRET; không render literal origin local_model:old hoặc origin=user. Tests cho phép “stale HOẶC exclude”, trái binding yêu cầu exclude khỏi usableevidence.
Missing required signature fields không coi mismatch; max_refinements/enable flag chưa kiểm. Formatter chỉ dùng samplingcoverage, chưa render analyzedcoverage/origin/model đầy đủ ở summary/detail.

## P1-C: Mức budget thấp làm mất cảnh báo
Binary-search cap tối thiểu cho1clip có20speechintervals+2visual: formatter trả6701bytes, nhưng không có bất kỳ omission notice hoặc stalewarning. include_details=False bỏ cảdata lẫn warnings; min_prompt khôngreservewarningfooter.
Cần skeleton+IDs+provenance/warning+actualomissions là tối thiểu trước optionaldetails/assets; khôngfit thìexpliciterror. Không được “vừa cap” nhờ che chất lượng/thiếu dữ liệu.

## Gate sửa hữu hạn
Giữ đúng2coremodules. TDD theoA/B/C, bốn testfiles cũ. Không feature/design mới/UI/network/live, chưa C2b/G05closed. NghỉsauG05,khôngshutdown.
