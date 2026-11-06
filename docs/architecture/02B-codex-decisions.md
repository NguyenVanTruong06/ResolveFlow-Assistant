# Codex quyết định — 02A-R2 / 02B-D

02A ACCEPTED: Codex đọc source và chạy69focusedtestPASS22.03s exit0. Các root/marker/core safety và fail-closed lstat đã đạt trongscope; không khẳng định chống mọiTOCTOU/physicalunplug.
02B-D ACCEPTED_WITH_BINDING_CORRECTIONS:
- Giao02B1 commit post-publication snapshot failure, không đổi snapshot() returncontract vì các caller khác (configure/migrate/link) cầnfailsemantics riêng.
- Registry published là completion boundary trong tiến trình hiện tại; không gọi fsync file+rename là cam kết power-lossdurability tuyệtđối nếu chưa fsyncdirectory/kiểmfilesystem.
- require_online đã có ngay trước rename tronglock: viếtfaulttest chứngminh markerchange bịchặn, khôngthêm wrappercheck trùng lặp nếu khôngcần.
- Lỗi snapshot sau publish khôngrollbackpack; additive snapshot_synced/warnings trong commitresult. UI hiện giữ payload pack_committed nên codeUI cóthể khôngcầnđổi, nhưng chưa hiểnthịwarning riêng.
- Không thay số đo bằngước lượng: bỏ99.9%riskclaim và60kasset “an toàn” khi chưađo. F1 khônghứa payloadnguyênvẹn sauexclude, F3 rollbackcóthể thấtbại khiunplug, F4 snapshot đangtronglock tới khiunlock; tempfileprefix thực tế .write-. Mô tảcode thật.
- Recovery/sharding/cleanup/locktimeoutpolicy/otherwritertransactions deferred. Worker khôngtựnghiệmthu.
