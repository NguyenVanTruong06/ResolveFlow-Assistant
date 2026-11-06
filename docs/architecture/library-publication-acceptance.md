# ChunDVC — nghiệm thu xuất kho và publication

Ngày2026-10-29. Trạng thái: ACCEPTED_WITH_RECORDED_LIMITS / PUBLISHED_VERIFIED.
Phạm vi hoàn thành: tách dữ liệu tài nguyên cũ khỏi source mặc định, bảo toàn bản lưu đầy đủ ngoài app, upload và đối chiếu checksum Google Drive. Đây không phải nghiệm thu toàn bộ roadmap hoặc G06.

## File đã xuất bản
[ChunDVC_Legacy_Library_v1.0.zip](https://drive.google.com/file/d/1gBEYJFza_9Lu6E0Bn-Zik5AefFmynANl/view?usp=drivesdk)
- FileID: 1gBEYJFza_9Lu6E0Bn-Zik5AefFmynANl.
- Parent: 1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq.
- Revision: 0B-JYT8O9SIWdVEJTcWRaT29RNk5PbEw0UFRIdlhReklQdHRNPQ.
- Size: 3,415,854,045 bytes.
- MD5: aa00d288fbf7b3632f14d8ebd62b897b.
- SHA256: f27aefb09759a4304599703fbaf4d281b28a67846fdb4891eae3d5cf8d4389ad.

Codex đã trực tiếp xác nhận file/name/size/parent/revision trên Drive ở lượt trước và tính full-stream MD5/SHA256 của ZIP local tại02A. Task LIB-PUBLISH-REMOTE-CHECK bổ sung Google Drive API v3 serverdigest tại remote-metadata.json. Lượt này Codex đọc lại report hash3c0923bc9823f78e8e64e8e768218d8ad53b8d3f00e5e9b6341b33c501164c25, tự đối chiếu artifact metadata/comparison với các giá trị local đã xác minh, fileID/parent/revision khớp, size/MD5/SHA256 đều match. Không chạy lại3GBdownload/hash hoặc pytest vì không thay code.
Bằng chứng: .bridge/ux-evidence/LIB-PUBLISH-REMOTE-CHECK/{remote-metadata.json,comparison.json,codex-acceptance-comparison.json}. Remote API checksum được Worker thu thập; không diễn đạt rằng normalized connector của Codex trực tiếp trả digest (connector bỏ fields và downloadlimit256MiB đã ghi nhận).

## Kho local và code
- Giữ2bản ngoài app tại /media/vanntruongg/Ventoy/ChunDVC_Publish/runs/20261029_011655_e6c3a144/: ZIP và6594originals trong restore_verification/; không tự xóa sau upload.
-6576samplefiles retired theo journal;6metadata runtime và12recipes chưa rõ giữ nguyên.
- Catalog demo mặc định không nạp/reseed; engine, custom/managed source được giữ. Master/preview/thumbnail được phân biệt khi nhập pack.
- Local acceptance/test/probe/UI boundaries giữ nguyên tại library-publication-local-acceptance.md:77focusedtests/7.61s/exit0, fixtureprobes và component widgetrender. Không thêm claim playback/Resolve/physicalaudio hoặc general migration safety.
- ZIP này là fullbackup, chưa phải một installableassetpack nhậpwizard1click; các pack phân phối từngcategory/optionalproceduralstyle import chưa triển khai trong phạm vi này.

## Chốt và điểm dừng
Gỡ blocker quyền/checksum của publication. Không cần upload lại, thay fileID, đổi sharing hoặc dọn thêm nguồn/backup. Anti dừng chờ nhiệm vụ mới, không tạo thêmreport để lặp relay. G06-06A4b checkpoint vẫn chưa được Codex review, không tự đánh dấuaccepted hoặc tựresume. Không toànroadmapcomplete/shutdown.
