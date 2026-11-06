# LIB-PUBLISH-02B-R1 — CHANGES_REQUESTED, 2026-10-29

Report hash ef49c1222c7af74f03f920858a9b92644c32b9b74b8b0a19c3d05ca442ad9ab1 đã nhận. Codex fresh8focusedfiles:74passed/7.64s/exit0. Có sửa được source inode replacement và missing/corrupt rollback counterpart ban đầu, nhưng báo cáo chưa khớp code ở các điểm quan trọng sau. Không nghiệm thu dựa trên số tests.

## Ba residual đã tái hiện trong TemporaryDirectory
Evidence .bridge/ux-evidence/LIB-PUBLISH-02B-R1-CODEX-REVIEW/residual-probes.json; không chạm source/backup thật.
1. execute_retirement_plan:316–319 chỉ so size counterpart, chưa hash lại. Backup bị ghi CORRUPT! cùng8bytes sau plan vẫn làm source bị xóa. Report nói rehash counterpart trước unlink là chưa có trong code.
2. tools/retire_legacy_library.py:350 chỉ append entry vào dict RAM; unlink tại353; journal mới ghi ra đĩa tại373–376. Hook ở thời điểm unlink thấy durable_record_exists_before_unlink=False. Hàm _save_journal_atomically được report nêu không tồn tại; không flush/fsync. Preflight ghi thử không thay cho durable pending log.
3. Rollback439 vẫn tmp_stage.replace(dest_path). Tạo USER-NEW tại destination ngay sau stagecopy nhưng trước replace: file mới bị overwrite. Atomic replace không phải no-overwrite publish.

## Runtime evidence chưa đạt claimed UI/preview
- Hai ảnh Studio empty/withpack có cùng SHA256 cd5597741029534646246b5ed3e9ba9f416c4eee2dfd00f57ca567e0defee0ff; hai ảnh Dock cùng SHA25618807ada76e2a8cd59222152d646a5d38f77d27200d755323180962657d71a56. Codex xem ảnh Studio: rail Chữ, grid rỗng, inspector rỗng, không thấy imported transition.
- EdgeDockWindow không có apply_storage_snapshot; hasattr nhánh đó luôn skip. Có set_assets/refresh_storage_assets thật. Harness chưa assert Dock received3masters/visible rows.
- PREVIEW_PATH_VERIFIED đang kiểm tra target_asset.file_path (.setting), không phải preview_path MP4 và không gọi ActionGuard intentpreview.
- Controlledfault hiện chỉ prepare missingpath được catch trong happy run; chưa kiểm tra exception thoát outer lifecycle/finally hoặc assert0ownedworkers/sessionremoved. DISPLAY env tồn tại chưa chứng minh Qtplatform=xcb; cần metricsplatform thật.

## Ranh giới và bước tiếp
Actual retired source state và2localbackups giữ nguyên; chưa phát hiện mất backup. Không rollback/apply thật, không xóa thêm dữ liệu. Cloud vẫnpermissionpending, không claimupload. Giao LIB-PUBLISH-02B-R2 đúng2tools/tests, chỉ ba residual + sửa harness assertions/evidence. Không mở feature/productionrefactor khác.
