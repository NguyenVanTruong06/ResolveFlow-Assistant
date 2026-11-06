# Codex quyết định — 02B1-T / 02C-D

02B1-T ACCEPTED: source hook manifest chạy dưới lock, real require_online chặn trướcrename; Codex7testPASS0.24s exit0.
02C-D ACCEPTED_WITH_BINDING_CORRECTIONS cho02C1 thu hẹp:
- Inventory read-only không tạo/acquireQLockFile vì lock thao tácfilesystem; filelockhiện diện chỉđủ nói BUSY/UNKNOWN, khôngtựremove stale. Khôngcólock khôngchứngminh staginginactive vìprepare/reviewkhônggiữregistrylock.
- Staging chỉcandidate ownershipunknown, khôngorphan/deletable. Mtime24h khôngquyềnsởhữu. Manifestkhôngtựchứngminhapproval.
- 02C1 chỉ đọc boundedregistry+liệt kêdirectchildrenstaging/packs+manifest; missingassets vàsnapshotcomparison hoãn đểkhôngstat100kmedia/đưa falsecoverage. Khôngauto-syncsnapshot tronginventory.
- Journal singlefile/schemachưađượcduyệt. PREPARED trongexample khôngkhớpenum phase. Doublefault explicitraise/log trongdoc làproposed, codehiệnchỉimplicitexceptioncontext; khôngviết nhưalreadyimplemented.
- Khônggọi lấyđượclock/read-onlyscan là snapshottransactionconsistent. Kiểmrecheckidentity vàstattrướcsau giúpnhậndiệnchanges, vẫnkhônghứa zeroTOCTOU.
