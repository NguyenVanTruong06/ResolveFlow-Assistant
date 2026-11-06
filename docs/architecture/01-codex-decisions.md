# Quyết định Codex — G01-D-R1

## Kết quả review
A chấp nhận trong phạm vi regression: Codex chạy2node settings→cancel + commit1/cancel2, 2 passed in2.35s, exit0. Không kết luận toànsuite hếtflaky. Việc auto_prepare cùng manualprepare gây race vẫn là phân tích helper, không phải bằng chứng hai callback GUI chạy song song; guard busy đã có.
B chấp nhận hướng thiết kế với các ràng buộc bắt buộc dưới đây. Chỉ giao01A-I;01B/01C còn PROPOSED.

## Trả lời ba câu hỏi
1. Duyệt src/core/contracts/asset_dto.py: Python thuần, khôngQt/IO/Resolve.
2. Duyệt StorageManager.ui_assets làm consumer đầu tiên, giữ signature và output tương thích. Không đồng thời đổi ingest/model/view.
3. Duyệt read-fallback cócontains+validation và giữ keycũ về nguyên tắc.01C chưa giao. Khi thực hiện phải kiểm QSettings.status sau sync vì lỗi ghi không nhất thiết ném exception; rollback chỉ giữ giá trị cũ, không bảo đảm các thay đổi mới cũng có trong domain cũ. Primary hợp lệ thắng; primary sai kiểu có thể fallback, không nói “luôn thắng” vô điều kiện.

## Ràng buộc thắng các ví dụ draft còn lệch
- broll→broll, không overlay. icon/sticker không meme.
- V1 hiện lưu template_type=text/trans.01A không ghi đổi sangtitle/transition; có thể nhận alias mới ở adapter nhưng đầu ra UI và serializer v1 phải text/trans. Không đổi versionroot registry hay dữ liệu cũ.
- Custom ID vẫn lowercaseASCII/số/underscore theo StorageManager.add_category. Ví dụ product_review, label/tag “Review Công Nghệ”. “âm thanh” là alias sfx, không customID hợp lệ cho fixture.
- Linked fixture cần source_id; managed/library_path ví dụ phải khớp02_cloud_downloads/packs/... thực tế. Builtin IDs giữ nguyên. Không tính lại ID/hash của bất kỳ record cũ.
- DTO persisted canonical khác UI compatibilityview cócategory/tabtext/trans/sốít. Không gắn cùng một typekhẳngđịnh categorycanonical rồi trả categoryUI. Readadapter phải hỗ trợ legacy record thiếu source_kind, coreimport khôngpack, linked cũ; strictvalidator của bản ghi mới không được làm mất chúng khỏiui_assets.
- Identity/revision và sourceUUID là đề xuất về sau. Không biến content_hash lấy mẫu đầu/cuối thành căn cước duy nhất của footage ở01A.
- Allowlist không đủ kiểm loại nestedvalue: validate tags/paths/numbers, loại _raw_obj/Qt/native refs khỏipayload, reject malformed allowedfields. Không serialize runtime objects thành repr/str để né lỗi.
- New record duration: finite positive hoặcNone.0/-1 legacy chỉ normalize khi đọc. Negativekhác/NaN/Infinity/bool/sai kiểu phải cóvalidationerror, không âm thầm biến thànhNone. Unknown/newerschema không được ghi ngược thànhv1.
- Source frameoffset86400 chỉ ví dụ24fps; không áp chungNTSC. Rounding PTS→timelineoffset và cộngstartframe phải tách, metadataPTS không bịép.01B chưađượcgiao.
- Remaining draft errors nhưundo_last trongslice phải đổi theo API thật undo(result=None), sai số8scope khi liệt kê10module phải sửa trongtài liệu, không đổi code production để khớptài liệu.
