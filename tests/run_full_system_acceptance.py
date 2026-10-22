#!/usr/bin/env python3
"""
Test toàn diện và nghiệm thu tự động các chức năng của ResolveFlow trên DaVinci Resolve thực tế.
"""
import os
import sys
import time
import subprocess
from pathlib import Path
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from src.core.resolve_api import ResolveAutomation
from src.services.resolve_gateway import ResolveGateway
from src.services.insert_service import InsertService, to_asset_dict
from src.core.text_preset import BUILTIN_PRESETS
from src.core.transition_preset import BUILTIN_TRANSITIONS, TransitionMacroGenerator

print("=" * 60)
print("🚀 BẮT ĐẦU KIỂM THỬ TOÀN DIỆN & NGHIỆM THU RESOLVEFLOW")
print("=" * 60)

# 1. Kết nối DaVinci Resolve
auto = ResolveAutomation()
if not auto.connect():
    print("❌ Không thể kết nối tới DaVinci Resolve!")
    sys.exit(1)

tl = auto.get_active_timeline()
if not tl:
    print("❌ Không tìm thấy Timeline hoạt động!")
    sys.exit(1)

print(f"🎬 Timeline đang mở: {tl.GetName()}")
print(f"⏱ Playhead Timecode: {tl.GetCurrentTimecode()}")
print(f"📊 Tracks hiện tại: Video={tl.GetTrackCount('video')}, Audio={tl.GetTrackCount('audio')}")

gw = ResolveGateway()
srv = InsertService(gateway=gw)
results = {}

# -------------------------------------------------------------
# TEST 1: CHÈN TEXT TITLE HOẠT HỌA (KINETIC / SPRING POP)
# -------------------------------------------------------------
print("\n" + "-" * 50)
print("📝 TEST 1: Chèn Text Hoạt họa Kinetic vào Timeline...")
text_asset = {
    "id": "kinetic_hormozi",
    "name": "RESOLVEFLOW_TEST_TEXT",
    "category": "kinetic",
    "tab": "text",
    "duration": 3.0,
}
res1 = srv.insert(text_asset, duration=3.0, preferred_track=2)
print(f"Kết quả Test 1: {res1}")
results["text_insert"] = res1.ok

# -------------------------------------------------------------
# TEST 2: CHÈN MEME VIDEO (01_funny_wow_reaction.mov)
# -------------------------------------------------------------
print("\n" + "-" * 50)
print("🎬 TEST 2: Chèn Video Meme (Track V3)...")
meme_path = os.path.abspath("assets/broll_memes/memes/01_funny_wow_reaction.mov")
meme_asset = {
    "id": "meme_01_funny_wow_reaction",
    "name": "01_funny_wow_reaction",
    "file_path": meme_path,
    "category": "reaction",
    "tab": "meme",
    "duration": 3.0,
}
res2 = srv.insert(meme_asset, preferred_track=3)
print(f"Kết quả Test 2: {res2}")
results["meme_insert"] = res2.ok

# -------------------------------------------------------------
# TEST 3: CHÈN LỚP PHỦ ẢNH STILL (check_verified_green.png)
# -------------------------------------------------------------
print("\n" + "-" * 50)
print("🖼 TEST 3: Chèn Lớp phủ ảnh Still PNG (Track V3)...")
overlay_path = os.path.abspath("assets/broll_memes/overlays/check_verified_green.png")
overlay_asset = {
    "id": "overlay_check_verified_green",
    "name": "check_verified_green",
    "file_path": overlay_path,
    "category": "badges",
    "tab": "overlay",
    "duration": 3.0,
}
res3 = srv.insert(overlay_asset, preferred_track=3)
print(f"Kết quả Test 3: {res3}")
results["overlay_insert"] = res3.ok

# -------------------------------------------------------------
# TEST 4: CHÈN HIỆU ỨNG ÂM THANH SFX (whoosh.wav / pop.wav)
# -------------------------------------------------------------
print("\n" + "-" * 50)
print("🔊 TEST 4: Chèn SFX Audio vào Track Audio...")
sfx_path = os.path.abspath("assets/sfx/whoosh.wav")
if not os.path.exists(sfx_path):
    sfx_files = list(Path("assets/sfx").glob("*.wav"))
    sfx_path = str(sfx_files[0]) if sfx_files else ""

sfx_asset = {
    "id": "sfx_whoosh",
    "name": "whoosh",
    "file_path": sfx_path,
    "category": "whoosh",
    "tab": "sfx",
    "duration": 1.5,
}
res4 = srv.insert(sfx_asset, preferred_track=2)
print(f"Kết quả Test 4: {res4}")
results["sfx_insert"] = res4.ok

# -------------------------------------------------------------
# TEST 5: CÀI ĐẶT & SẴN SÀNG CHUYỂN CẢNH (TRANSITIONS)
# -------------------------------------------------------------
print("\n" + "-" * 50)
print("⚡ TEST 5: Cài đặt và kích hoạt Chuyển cảnh (Transitions)...")
trans_asset = {
    "id": "trans_whip_pan_left",
    "name": "Whip Pan Trái",
    "category": "motion",
    "tab": "trans",
    "duration": 0.5,
}
res5 = srv.insert(trans_asset)
print(f"Kết quả Test 5: {res5}")
results["trans_ready"] = res5.ok

# -------------------------------------------------------------
# BÁO CÁO TỔNG QUAN TÌNH TRẠNG TIMELINE
# -------------------------------------------------------------
print("\n" + "=" * 60)
print("📊 TỔNG HỢP CÁC TRACK TRÊN TIMELINE SAU KHI THỰC THI:")
for t in range(1, tl.GetTrackCount("video") + 1):
    items = tl.GetItemListInTrack("video", t) or []
    names = [it.GetName() for it in items[-4:]]
    print(f"  Track Video V{t} ({len(items)} clips): ... {names}")

for t in range(1, tl.GetTrackCount("audio") + 1):
    items = tl.GetItemListInTrack("audio", t) or []
    names = [it.GetName() for it in items[-3:]]
    print(f"  Track Audio A{t} ({len(items)} clips): ... {names}")

gw.shutdown()

print("\n" + "=" * 60)
print("🎯 BẢNG KẾT QUẢ NGHIỆM THU:")
all_ok = True
for k, v in results.items():
    status = "✅ PASS" if v else "❌ FAIL"
    if not v:
        all_ok = False
    print(f"  - {k:<20}: {status}")

print("=" * 60)
if all_ok:
    print("🏆 TOÀN BỘ CÁC TÍNH NĂNG ĐÃ VƯỢT QUA KIỂM THỬ THÀNH CÔNG!")
else:
    print("⚠️ Có tính năng chưa hoàn thành.")
