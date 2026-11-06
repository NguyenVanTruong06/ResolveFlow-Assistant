# ChunDVC — nghiệm thu kho local, 2026-10-29

Trạng thái: LOCAL_CLEANUP_ACCEPTED_WITH_RECORDED_LIMITS. Remote publication: UPLOAD_BLOCKED_PERMISSION, chưa hoàn thành toàn bộ yêu cầu upload. Không đóng G06 hoặc toàn roadmap.

## Thẩm quyền và kết quả
User yêu cầu gom toàn bộ kho cũ (meme VN/text/LUT/SFX/BGM/overlays/transitions), xuất1ZIP ngoài app và upload Drive, dọn nguồn mẫu trong code. Task cuối LIB-PUBLISH-02B-R2, report SHA256 bda1b83c94d2cd289d94b05cdf250447a8344b435f9bde82fb8c846a40240926 đã được Codex nhận/review.

- Ingestion giữ master/MP4-preview/PNG-thumbnail thành quan hệ phụ thuộc; companions không tạo thẻ/B-roll độc lập. Shared/excluded dependencies và path authority được giữ trong phạm vi tests đã giao.
- BUILTIN_PRESETS/BUILTIN_TRANSITIONS/MOCKUP catalogs mặc định rỗng, không tự seed24JSON; engine/model/generator còn, có technical text default không tạo librarycard. Custom/managed snapshot vẫn được nạp.
- Source retirement đã có6576uniqueentries;6runtime metadata và12recipes chưa rõ vẫn giữ. Codex audit trước đó:0counterpartmissing/sizebad,0retiredsourcecòn trong repo. Không dùng công cụ destructive trên dữ liệu thật ở các lượt review/fix.
- Bản lưu /media/vanntruongg/Ventoy/ChunDVC_Publish/runs/20261029_011655_e6c3a144/zip/ChunDVC_Legacy_Library_v1.0.zip:3415854045bytes, SHA256f27aefb09759a4304599703fbaf4d281b28a67846fdb4891eae3d5cf8d4389ad, MD5aa00d288fbf7b3632f14d8ebd62b897b. Full-stream digest đã được Codex xác minh tại02A;6594restoredoriginals và full24text/4transition catalogs còn ngoài app.

## Bằng chứng mới trong lượt nghiệm thu
Fresh đúng8focusedfiles:77passed in7.61s,exit0; không fulltests/Resolve/inference/desktoprerun. Codex3fixtureprobes độc lập: corrupted same-size backup giữ source; actualjournal.pending_entries đọc được từdisk ngay trước unlink; rollback no-overwrite giữ raced USER_NEW file và0tmpstageleft. Evidence .bridge/ux-evidence/LIB-PUBLISH-02B-R2-CODEX-REVIEW/residual-probes.json.
Source _persist_journal thực sự tồn tại và được gọi trước unlink, dùng tempfile/flush/fsync/atomicreplace; counterpart được hash lại; rollback dùng exclusive stage + os.link. Pending journal recovery cho source đã mất có trong reader. Các nhận định trướcR2 không còn dùng như trạng thái hiện hành.

## Component UI acceptance và giới hạn
Worker normal/fault metrics platform=xcb,exit0/exit2,sessionremoved=true,remainingownedwindows0. Codex đọc harness/log/metrics và xem2ảnh importedStudio/Dock: đúng3transitioncards, StudioInspector đã chọnasset; ảnh khác emptyviews. ActualPackIngestService prepare/commit vào ownedStorageRoot,3masters+6companions. ActionGuard intentpreview với injectedownedstorage cho phép đúng companionMP4, khác master.setting.
Mức nghiệm thu: COMPONENT_WIDGET_RENDER + PREVIEW_PATH_AUTHORITY, không fullapp/Resolveintegration hoặc playback/physicalaudio. Playback NOT_TESTED. StudioInspector trong harness còn hiện Không khả dụng vì UIdefaultprovider khác injectedownedstorage; ảnh chứng minh cards/selection, không chứng minh nútPlay hoạt động end-to-end.
Chỉ số owned_running_workers trong metrics harness đang là literal0, không phải registrymeasurement; không nghiệm thu claim threadleakproof chỉ từ trường này. Normal/fault chứng minh window/sessioncleanup theo code hiện tại; không chạy inferenceworkers trong smoke. Không mở vòng evidence mới chỉ để nâng mức claim này.
Tooling nghiệm thu theo probes/fixture scope, không cam kết công cụ migration đa tiến trình hoặc chống mọi race/powerloss/unsafejournal. Không chạy lại apply trên source đã retire, không sửa dữ liệu thật để minh họa rollback. Export tool cũ có limits về mount/nooverwrite đã ghi02A; không dùng nó để overwrite run tốt.

## Phần còn lại và điểm dừng
Drive root https://drive.google.com/drive/folders/1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq. Lần write thực tế gần nhất403insufficientParentPermissions cho hoangphudakha@gmail.com; chưa có observedfileID/uploadthànhcông. Cần user cấp Editor hoặc tự upload ZIP đã có, sau đó readbacksize+checksum. Không retrywrite khi chưa có thay đổi quyền, không bật publicsharing hoặc ghi đè các ZIP cũ.
ZIP này là fullbackup, chưa phải installablepack1click; các pack từngcategory/optionaltextimport chưa được giao trong nghiệm thu này. Giữ nguyên2localbackups, không xóa originals để tiết kiệm chỗ. G06-06A4b giữcheckpoint chờ hướng dẫn tiếp; chưa được Codex nghiệm thu vì chỉ có Workercheckpointclaim. Mailbox chờ user hỗ trợ quyền Drive, không tựresumeG06/shutdown.


## Upload cập nhật2026-10-29 — thay trạng thái403 ở trên
User ownerđãupload file https://drive.google.com/file/d/1gBEYJFza_9Lu6E0Bn-Zik5AefFmynANl/view và báo Editorgrant. Codex trực tiếp đọcname/MIMEzip/size3415854045/parentroot1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq và revisionuploaderhoortoo.h@gmail.com, đúngfileđích. Remote=UPLOAD_CONFIRMED_METADATA_ONLY, không cònblockedpermission. Providerdigest không có trongnormalizedtooloutput; rawfetch413vượt256MiBlimit nên chưaverifyremotechecksum. Active read-onlytask LIB-PUBLISH-REMOTE-CHECK dùngexistinguploaderclient nếu có; không reupload/sourcecleanup/G06resume.


## Chốt publication2026-10-29
Remote checksum readback size/MD5/SHA256 đã match bản local; Codex đã review artifacts và ghi PUBLISHED_VERIFIED tại docs/architecture/library-publication-acceptance.md. Các blocker403/256MiBconnector ở trên là lịch sử, không còn chặn publication; không cần upload/test/cấp quyền lại. Giữ2backups và giới hạn localacceptance, không tựresumeG06.
