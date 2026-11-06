# Review G04-04C2-I — CHANGES_REQUESTED

Codex đọc action_guard/InsertService/tests và chạy4files quy định: **87 passed in3.86s, final exit0**. Common paths/gatewayfailcallback/managedorigin/nativeguidance/stat filechecks có evidence; khôngdesktopverified.

Hai probe read-only, providers injected, khôngdisk/Resolve:
1. asset {id:p,file_path:/virtual/p.wav,source_kind:[]} +regularfilestat +identityrealpath => **INVALID_SOURCE_KIND_ALLOWED=True, OK_LEGACY_MEDIA**. Unknown/malformed explicit source bị fallback legacy thay vì validate.
2. legacyfileasset, realpath_fn raises PermissionError => **RESOLVE_EXCEPTION_ESCAPED=PermissionError**. Storage.online/property và realpath còn ngoài try ở nhiều nhánh; helper không thực hiện lời hứa failure-safe/provider-error result. InsertService wrapper có catch nên route service vẫn bịblocked, nhưng corehelper không đủ contract để reuse.

Giao R1 chỉcore/action_guard.py +tests: validate explicit sourcekind vsmissing/None legacy; dependency/property/resolve exceptions thành structured denial, giữ reasoncodes. Không rewrite500lines chỉđểrefactor; không mởconsumer mới.

## Review R1
Codex chạy4files: **121 passed in3.72s, final exit0**. Sourcekind invalid gate và safe resolve/stat/provider failures xử lý hai lỗi trước; sync test đã bỏ wallclock threshold. Không nói failclosed100% toàninput chỉ từ sốtests.

Một regression mới từ R1: `if source_kind == generated: return ERR_FILE_NOT_FOUND` ngay sau procedural branch, trước regularfile check. Decision đãchốt generated có thể file-backed; source enum không thay thế file/capability. Pure injectedprobe với generated /virtual/rendered.wav, stat_fn regularfile: **GENERATED_VALID_FILE_ALLOWED=False CODE=ERR_FILE_NOT_FOUND STAT_CALLS=0**.
Giao R2 hữu hạn: generated không có recognized runtimegenerator thì đi qua file-backed checks, existingfile allow vàmissingfile deny. Không đổi procedural exemption hoặcmanagedtext/no fallback. Đây là sửa nhánh cụ thể, không yêu cầu audit/helper rewrite hayconsumer mới.

## Review R2 — CODE ACCEPTED
Codex đốichiếu generated file-backed đi Branch7/_safe_stat_file, successcode OK_GENERATED_MEDIA_VERIFIED, procedural exemption riêng và staging trướcIO; integrationgeneratedaudio dispatch dùngfiletmp. Chạy4files quy định: **128 passed in3.75s, final exit0**. Hai boundary gaps và generated regression đãđóng trongscope đãgiao; không rerun/refactor khi chưa có thay đổi mới.
Guard firstconsumer InsertService CODE ACCEPTED, chưadesktopverified. Không coi Studio directvideo/Fusion/drag/hover đượcguard bởi service này. Originmetadata đãaccepted trước. Next04C3-I chỉpreview invalidation lifecycle2modules, không mởrộng insertguard.
