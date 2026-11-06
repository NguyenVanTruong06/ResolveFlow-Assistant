# G04-04C2-D — REVISE_BEFORE_IMPLEMENTATION

Đã đối chiếu StorageManager.assets, DTO source kinds, TabAssets adapter, to_asset_dict và InsertService. Không chạy pytest/app trong lượt design. Chưa phê duyệt production guard.

## Giữ các chính sách
- Managed từ rootA cũ phải bị chặn khi active rootB; linked media có thể độc lập primaryroot nếu nguồn và path được xác minh; native transition guidance không phụ thuộc previewMP4.
- Metadata inspect vẫn được; staging/missing không được tự biến online. Quy tắc cachedoffline linked/native exceptions cần thứ tự rõ, không blanket deny trước khi phân loại intent/source.
- First consumer chỉ InsertService.insert_async; drag/preview, Studio direct-video/Fusion paths chưa được bảo vệ bởi nó. Không gọi đây là single chokepoint cho toànapp.

## Bốn lỗi thiết kế cần đóng
1. Source enums thật managed/linked/builtin/generated (asset_dto.py), không bundled. Builtin source không đồng nghĩa mọi asset không cần file: builtin WAV/LUT/MP4 vẫn cần media; chỉ recognized generative preset/native-guidance được miễn theo intent. Managed text thiếufile không được fallback thành TextStylePreset generator chỉ vì category=text.
2. Authority trace thiếu Studio: TabAssets._dict_to_studio_asset hiện giữ source_kind/source_id/pack_id nhưng KHÔNG copy library_path/external_path/originlibraryID/root. Không nói chỉ thiếu libraryID. Guard chỉto_asset_dict không làm metadata đãmất xuất hiện lại. Nêu chính xác các adapter/producers cần additive runtimefields nếu cần, không giấu module thứ3.
3. Path containment chỉ phát hiện khácrootpath, không chứng minh origin libraryID, nhất là cùngpath đổi marker+config. Lexical is_relative_to còn bỏ qua symlink/..; cần normalized/resolved path policy và path-file predicate (not directory), handling lỗi stat/permission. Không hứa crash/TOCTOU proof hoặc pure nếu helper mặc định IO. Failclosed khi authority mâu thuẫn, compatibilityfallback khi legacy thiếu phải quyết định rõ bằngfixture, không blanketallow/bandự đoán.
4. Native transition precedence chưa nhất quán: cachedmissing check trước native sẽ chặn guidance chỉ vì previewmissing. Text khôngfile bypass trướcmanaged cũng sai. Cần algorithm đầy đủ return cho legacy/nonmanaged ordinary media, unknown source/intent, knownstates và exceptionsprovider/filepredicate; callback/result schema đúng. _record_result/insertion_finished hiệncó, nhưng cần xem callback/signal/order synchronous wrapper vàguard-resolvedpath dùngthế nào, không gọi transition trực tiếp ngoài dispatch đangcó.

## Bằng chứng cần bổ sung hữu hạn
Bảng quyết định một hàng cho mỗi case đặc biệt, exact fields đi qua full Dock+Studio trace, exact minimal slice theo dependency thực. Nếu cần producer/adapter trước, đề xuất duy nhất slice đó thay vì guard thiếuauthority. Không cần audit/reset lại04A/G03, không benchmarks hoặc tests designphase.

## Review D-R1 — ACCEPTED_WITH_BINDING_DECISIONS
Chọn Phương án A, G04-04C2-P-I trước runtimeguard. Ba modules: storage_manager.py, contracts/asset_dto.py, tab_assets.py. Không tích hợp guard với legacy containment giả identity.
- Bổ sung runtime origin_library_id/origin_root từ provider biết origin, bảo toàn qua DTO/Studio. Snapshot offline mới giữ origin trong envelope cục bộ, không ghi vào registry hoặc pack manifest. Snapshot legacy thiếuorigin giữ unknown(None/absent), không lấy current config để stamp identity xác minh giả.
- Registry ingest hiện có thể thiếu source_kind; runtime StorageManager.assets có thể suy managed từ library_path hoặc linked từ external_path hợp lệ khi trường nguồn thiếu; explicit source_kind vẫn giữ. Không thêm enum hoặc mutate persisted records.
- Query2 policy: permission/EIO là không truy cập được, reason code riêng, không khẳng định filemissing; implementation guard chưaactive. os.path.isfile có thể nuốt OSError nên helperbool mặc định không phân loại chính xác Permission/EIO như thiết kế hứa; chốt bằng stat/exceptions khi đến guard.
- Query3: legacy ordinarymedia fileexists có thể compatibilityallow sau này, nhưng không được hạ known managed thành legacy để bỏ identity. Guard policy chưaactive.
- Loại bỏ claim95%, microseconds,0ms, loại race hoàn toàn và singlechokepoint toànapp. Immediate callback chỉ không vào eventloop chờtimeout; pathrecheck vẫnbest-effort.
- MatrixsamepathIDswap không được ghi alwaysblocked cho legacy thiếuorigin. Native cachedmissing vìpreview là trườnghợp intent-specific; staging vẫndeny.

## Quyết định triển khai sau P accepted
- Guard first consumer insert_async, core action_guard.py không Qt/UI/Resolve. Chỉ hỗ trợ insert (vàinspect metadata nếu cần); preview/drag chưaconsumer, không hứa đãguard.
- Known managed phải có origin_library_id/root và paths hợp lệ, fresh provider online/currentID match, resolved path thuộc đúngroot và expectedlibrary_path. Missingorigin trả reason cần refresh, không degrade knownmanaged sanglegacy. Legacyordinarymedia không sourcekind/storage ownership chỉrequire regularfile, cachedoffline/missing/staging vẫndeny; linked có thể reevaluate cachedoffline theo actual externalpath, không cầnrootonline.
- Định nghĩa procedural bằng actual runtime capability/type, không chỉ source_kind=generated hoặc categorytext; filebacked generated/builtin media vẫnregularfile. Native guidance được miễn previewmissing chointent insert, staging vẫndeny, clipboard/gateway thuộc service.
- Default filesystem cần stat-mode và exception classification để không hứa Permission/EIO từ os.path.isfile vốnnuốt lỗi. Không scan/hash, không latencyclaims. os.path.realpath/pathresolution chỉbest-effort TOCTOU.
- Permission/EIO là inaccessible/offline +stablecode, không fake missing; None/invalidpath/input phải trả lỗi cócode, không throw vào UI. Guard failure callback/result/history/signal như existingcontract, no gatewaytask.
