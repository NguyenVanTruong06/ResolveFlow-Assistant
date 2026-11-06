# G04-04C3-I — CODE ACCEPTED

Codex đọc bind_model/disconnect/modeldestroyed, stop/resetactive, EndOfMedia gate và AssetGridView.setModel/hide/close hooks. Chạy đúng5files: **34 passed in4.24s, final exit0**.
Đạt scope: model invalidation dừng pending hover/player/overlay; oldmodel detach, duplicatebind no-op, lateEndOfMedia saustop khôngplay, hovermới saureset hoạtđộng, playerlazy giữ. Gate actualcode dùng _is_active_playback, không kiểm _hovered_row>=0 như report tóm tắt; không thêm claim khôngcó.
Giới hạn: testsmodel reset/removal phầnlớn kiểm pendingtimer, không phải mọi actualdecoder/hardware scenario; chưadesktop/hotplugverified. Không coi dataChanged từngfield hoặc dynamicroot/filepreview đãguard; không hứa fullQObjectdisposecoverage chỉ từ34tests.
Next G04-04C4-D: design Studio insert entry/payload preservation. Không mở lại04C3 trừ bug tái hiện.
