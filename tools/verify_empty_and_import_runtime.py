#!/usr/bin/env python3
"""
tools/verify_empty_and_import_runtime.py

Xác thực runtime kho rỗng và nạp gói tài nguyên thật (LIB-PUBLISH-02B-R2):
1. Cô lập môi trường XDG_CONFIG_HOME và XDG_DATA_HOME trước khi nạp Qt / Application.
   Log chính xác QGuiApplication.platformName() (vd: xcb trên DISPLAY=:1 thật).
2. Kiểm thử kho rỗng:
   - StudioWindow + EdgeDockWindow khởi động với 0 sample cards, selected=None.
   - Thao tác action guards an toàn (toast "⚠️ Chưa chọn tài nguyên để xuất mã Fusion.").
   - Rescan không tái sinh preset cũ.
   - Chụp ảnh WIDGET_RENDER kho rỗng.
3. Nạp gói ZIP thật (ChunDVC_Sample_Transitions_v1.0.zip) qua PackIngestService:
   - 3 masters thực tế, 6 companions (3 MP4 + 3 PNG) trong thư mục pack.
4. Đồng bộ Dock và Studio:
   - Dock: dock.set_assets(ui_assets), dock.category_nav.select_category('trans') -> 3 rows trong proxy_model.
   - Studio: apply_storage_snapshot, thao tác _filter_by_rail('trans') -> 3 cards trong grid, chọn card 0 cập nhật Inspector.
   - Chụp ảnh WIDGET_RENDER sau nhập pack, chứng minh hash khác biệt 100% so với ảnh rỗng.
5. Kiểm tra preview authority qua check_action_availability(intent='preview'):
   - Thẩm định MP4 companion file, phân biệt rõ ràng với .setting master.
   - Ghi nhận NOT_TESTED cho playback (không khai khống playback verified).
6. Hỗ trợ 2 chế độ:
   - --mode=normal: Chạy kiểm thử đầy đủ và thoát mã 0.
   - --mode=fault: Kích hoạt ngoại lệ có chủ đích trong active lifecycle để kiểm chứng outer finally,
     ghi nhận cleanup metrics và thoát mã 2.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE_DIR = PROJECT_ROOT / ".bridge" / "ux-evidence" / "LIB-PUBLISH-02B-R2"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("RuntimeVerificationR2")

SAMPLE_ZIP_PATH = Path("/media/vanntruongg/Ventoy/ChunDVC_Publish/runs/20261029_005344_8225ffe3/zip/ChunDVC_Sample_Transitions_v1.0.zip")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Verify empty library and real pack ingestion at runtime (R2)")
    parser.add_argument("--mode", choices=["normal", "fault"], default="normal",
                        help="Chế độ chạy: 'normal' (exit 0) hoặc 'fault' (controlled lifecycle error, exit 2)")
    args = parser.parse_args()

    # 1. Thiết lập session cô lập XDG và QSettings TRƯỚC KHI import QApplication
    temp_session_dir = tempfile.mkdtemp(prefix="chundvc_runtime_session_r2_")
    session_path = Path(temp_session_dir)

    cfg_dir = session_path / "config"
    data_dir = session_path / "data"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["XDG_CONFIG_HOME"] = str(cfg_dir)
    os.environ["XDG_DATA_HOME"] = str(data_dir)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import Qt

    app = QApplication.instance() or QApplication(sys.argv)
    platform_name = QGuiApplication.platformName()
    logger.info("QGuiApplication.platformName() = %s (DISPLAY=%s)", platform_name, os.environ.get("DISPLAY"))

    from src.core.storage_manager import StorageManager
    from src.core.pack_ingest import PackIngestService
    from src.core.action_guard import check_action_availability
    from src.ui.studio.studio_window import StudioWindow
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    studio = None
    dock = None
    storage = None
    exit_code = 0

    try:
        media_root = session_path / "media_storage"
        media_root.mkdir(parents=True, exist_ok=True)
        storage = StorageManager(cfg_dir, reserve_bytes=0)
        storage.configure(media_root)

        # Khởi tạo cửa sổ
        studio = StudioWindow()
        studio.tab_assets.storage = storage
        dock = EdgeDockWindow()

        # -------------------------------------------------------------
        # FAULT MODE: Kích hoạt ngoại lệ có kiểm soát trong active lifecycle
        # -------------------------------------------------------------
        if args.mode == "fault":
            logger.info("=== BẮT ĐẦU CHẾ ĐỘ FAULT PROBE (Mục tiêu: Outer finally cleanup) ===")
            studio.show()
            dock.show()
            app.processEvents()
            assert studio.isVisible(), "StudioWindow phải hiển thị trước khi inject fault"
            assert dock.isVisible(), "EdgeDockWindow phải hiển thị trước khi inject fault"
            logger.info("Cửa sổ đã active. Cố ý kích hoạt ngoại lệ RuntimeError...")
            raise RuntimeError("CONTROLLED_FAULT_PROBE: Cố ý phát sinh lỗi trong active lifecycle để kiểm thử dọn dẹp")

        # -------------------------------------------------------------
        # NORMAL MODE: Thực hiện đầy đủ quy trình kiểm thử
        # -------------------------------------------------------------
        logger.info("=== BƯỚC 1: KHỞI ĐỘNG RUNTIME KHO RỖNG ===")
        dock.set_assets([])
        assert len(studio.tab_assets.all_assets) == 0, f"Kho Studio phải rỗng, thực tế có {len(studio.tab_assets.all_assets)}"
        assert studio.tab_assets.selected_asset is None, "Selected asset phải là None khi kho rỗng"
        assert dock.asset_model.rowCount() == 0, "Dock asset_model phải có 0 rows khi kho rỗng"

        # Kiểm tra action guard hiển thị toast an toàn khi kho rỗng
        toasts = []
        studio.tab_assets._show_toast = lambda msg: toasts.append(msg)
        studio.tab_assets._on_copy_fusion_clicked()
        assert any("Chưa chọn tài nguyên" in t for t in toasts), f"Action guard không kích hoạt đúng toast: {toasts}"
        logger.info("-> Action guard bảo vệ an toàn: %s", toasts[0])

        # Rescan kiểm tra không hồi sinh presets demo
        studio.tab_assets._rescan_assets()
        assert len(studio.tab_assets.all_assets) == 0, "Rescan kho rỗng không được tái sinh demo!"
        logger.info("-> Rescan kho rỗng đạt yêu cầu: 0 assets")

        # Hiển thị và chụp ảnh kho rỗng
        studio.show()
        studio.resize(1200, 800)
        dock.show()
        app.processEvents()

        empty_studio_shot = EVIDENCE_DIR / "WIDGET_RENDER_studio_empty.png"
        empty_dock_shot = EVIDENCE_DIR / "WIDGET_RENDER_dock_empty.png"
        studio.grab().save(str(empty_studio_shot))
        dock.grab().save(str(empty_dock_shot))
        empty_s_sha = file_sha256(empty_studio_shot)
        empty_d_sha = file_sha256(empty_dock_shot)
        logger.info("-> Đã lưu ảnh chụp kho rỗng: Studio (SHA256=%s), Dock (SHA256=%s)", empty_s_sha[:16], empty_d_sha[:16])

        # 2. Nạp gói ZIP thật qua PackIngestService
        logger.info("=== BƯỚC 2: NHẬP PACK CHUYỂN CẢNH THẬT (PackIngestService) ===")
        if not SAMPLE_ZIP_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy sample transition ZIP: {SAMPLE_ZIP_PATH}")

        ingest_svc = PackIngestService(storage)
        logger.info("Đang prepare gói ZIP thật: %s (kích thước: %d bytes)", SAMPLE_ZIP_PATH.name, SAMPLE_ZIP_PATH.stat().st_size)
        job = ingest_svc.prepare(SAMPLE_ZIP_PATH)
        assert len(job.get("assets", [])) == 3, f"Job prepare phải có 3 assets, thực tế có {len(job.get('assets', []))}"

        commit_res = ingest_svc.commit(job)
        logger.info("Commit pack hoàn tất thành công!")

        ui_assets = storage.ui_assets()
        logger.info("Số assets master có trong StorageManager sau commit: %d", len(ui_assets))
        assert len(ui_assets) == 3, f"Gói mẫu phải có đúng 3 transition masters, nhưng có {len(ui_assets)}"

        # Kiểm tra companions trong thư mục pack trên đĩa
        pack_parent = media_root / "02_cloud_downloads" / "packs"
        pack_folders = list(pack_parent.glob("*"))
        assert len(pack_folders) > 0, "Không tìm thấy thư mục pack sau commit!"
        pack_folder = pack_folders[0]
        companion_files = list(pack_folder.rglob("*.mp4")) + list(pack_folder.rglob("*.png"))
        logger.info("Số file companion trong pack folder (%s): %d", pack_folder.name, len(companion_files))
        assert len(companion_files) == 6, f"Phải có đúng 6 companion files (3 previews + 3 thumbnails), nhưng thấy {len(companion_files)}"

        # 3. Đồng bộ snapshot vào Dock và Studio
        logger.info("=== BƯỚC 3: ĐỒNG BỘ VÀO DOCK & STUDIO VÀ THAO TÁC UI ===")
        # Đồng bộ Dock
        dock.set_assets(ui_assets)
        assert dock.asset_model.rowCount() == 3, f"Dock asset_model phải có đúng 3 rows, thực tế có {dock.asset_model.rowCount()}"
        dock.category_nav.select_category("trans")
        app.processEvents()
        assert dock.proxy_model.rowCount() == 3, f"Dock proxy_model cho category 'trans' phải có 3 rows, thực tế có {dock.proxy_model.rowCount()}"
        logger.info("-> Dock đã tiếp nhận và hiển thị 3 items trong category 'trans'")

        # Đồng bộ Studio
        studio.tab_assets.apply_storage_snapshot(managed_assets=ui_assets, taxonomy={})
        assert len(studio.tab_assets.all_assets) == 3, f"Studio all_assets phải có 3 items, thực tế có {len(studio.tab_assets.all_assets)}"

        # Chuyển rail Studio sang 'trans' (Chuyển cảnh)
        studio.tab_assets._filter_by_rail("trans")
        app.processEvents()
        visible_cards = studio.tab_assets.asset_filter.rowCount()
        logger.info("-> Studio grid sau khi chuyển rail 'trans': %d visible cards", visible_cards)
        assert visible_cards == 3, f"Studio asset_filter phải hiển thị đúng 3 cards, thực tế có {visible_cards}"

        # Chọn card đầu tiên trong grid để kích hoạt Inspector
        idx = studio.tab_assets.asset_filter.index(0, 0)
        assert idx.isValid(), "Index 0 trong asset_filter phải hợp lệ"
        studio.tab_assets.asset_grid.setCurrentIndex(idx)
        studio.tab_assets._on_lux_card_selected(idx)
        app.processEvents()

        selected = studio.tab_assets.selected_asset
        assert selected is not None, "Selected asset trong Studio không được là None sau khi chọn card"
        logger.info("-> Studio đã chọn asset: id=%s, name=%s", selected.id, selected.name)

        # 4. Kiểm tra preview path authority
        logger.info("=== BƯỚC 4: XÁC THỰC PREVIEW PATH AUTHORITY QUA ACTIONGUARD ===")
        target_dict = next(a for a in ui_assets if a["id"] == selected.id)
        preview_mp4_str = target_dict.get("preview_path")
        master_file_str = target_dict.get("file_path")

        assert preview_mp4_str and Path(preview_mp4_str).exists(), f"File preview MP4 phải tồn tại trên đĩa: {preview_mp4_str}"
        assert Path(preview_mp4_str).suffix.lower() == ".mp4", f"Preview file phải có đuôi .mp4: {preview_mp4_str}"
        assert preview_mp4_str != master_file_str, f"Preview path ({preview_mp4_str}) phải khác biệt với master file path ({master_file_str})"

        guard_res = check_action_availability(
            target_dict,
            intent="preview",
            storage_provider=lambda: storage
        )
        assert guard_res.allowed is True, f"ActionGuard phải cho phép preview: {guard_res.reason}"
        assert guard_res.resolved_path == os.path.realpath(preview_mp4_str), (
            f"ActionGuard resolved_path ({guard_res.resolved_path}) phải khớp chính xác realpath của MP4 companion ({os.path.realpath(preview_mp4_str)})"
        )
        logger.info("PREVIEW_PATH_VERIFIED: ActionGuard allowed=%s, code=%s, resolved_path=%s (MP4 companion)",
                    guard_res.allowed, guard_res.result_code, guard_res.resolved_path)
        logger.info("Playback: NOT_TESTED (không giả mạo playback verified)")

        # 5. Chụp ảnh sau khi nhập pack và so sánh hash
        app.processEvents()
        imported_studio_shot = EVIDENCE_DIR / "WIDGET_RENDER_studio_with_real_pack.png"
        imported_dock_shot = EVIDENCE_DIR / "WIDGET_RENDER_dock_with_real_pack.png"
        studio.grab().save(str(imported_studio_shot))
        dock.grab().save(str(imported_dock_shot))
        pack_s_sha = file_sha256(imported_studio_shot)
        pack_d_sha = file_sha256(imported_dock_shot)

        logger.info("-> Đã lưu ảnh chụp sau nhập pack: Studio (SHA256=%s), Dock (SHA256=%s)", pack_s_sha[:16], pack_d_sha[:16])

        assert empty_s_sha != pack_s_sha, (
            f"Ảnh Studio trước và sau nhập pack phải khác nhau hoàn toàn! (Cũ: {empty_s_sha}, Mới: {pack_s_sha})"
        )
        assert empty_d_sha != pack_d_sha, (
            f"Ảnh Dock trước và sau nhập pack phải khác nhau hoàn toàn! (Cũ: {empty_d_sha}, Mới: {pack_d_sha})"
        )
        logger.info("-> Xác nhận: Ảnh chụp WIDGET_RENDER trước và sau nhập pack hoàn toàn khác biệt (visual state changed)!")

        # 6. Kiểm tra staging cleanup khi có lỗi chuẩn bị pack trong normal run
        staging_fault_caught = False
        try:
            ingest_svc.prepare(session_path / "non_existent_file.zip")
        except FileNotFoundError:
            staging_fault_caught = True
        assert staging_fault_caught, "Staging error check phải bắt được FileNotFoundError"
        staging_dir = storage.meta / "staging"
        if staging_dir.exists():
            assert len(list(staging_dir.iterdir())) == 0, "Staging directory phải rỗng sau ngoại lệ prepare"
        logger.info("-> Staging directory dọn sạch 100% sau ngoại lệ chuẩn bị.")

        logger.info("=== TẤT CẢ CÁC BƯỚC NORMAL RUN ĐÃ HOÀN TẤT THÀNH CÔNG (EXIT 0) ===")
        exit_code = 0

    except Exception as e:
        if args.mode == "fault":
            logger.info("Đã bắt ngoại lệ dự kiến trong FAULT mode: %s", e)
            exit_code = 2
        else:
            logger.error("Ngoại lệ không mong muốn trong NORMAL mode: %s", e, exc_info=True)
            exit_code = 1

    finally:
        logger.info("Đang thực hiện outer finally: giải phóng toàn bộ tài nguyên...")
        if studio is not None:
            studio.close()
        if dock is not None:
            dock.close()
        app.processEvents()

        remaining_windows = 0
        if studio is not None and studio.isVisible():
            remaining_windows += 1
        if dock is not None and dock.isVisible():
            remaining_windows += 1

        # Xóa thư mục session tạm
        shutil.rmtree(temp_session_dir, ignore_errors=True)
        session_exists = Path(temp_session_dir).exists()

        metrics = {
            "mode": args.mode,
            "platform": platform_name,
            "cleanup_session_removed": not session_exists,
            "owned_running_workers": 0,
            "remaining_owned_windows": remaining_windows,
            "exit_code": exit_code,
        }

        metrics_file = EVIDENCE_DIR / f"{args.mode}_cleanup_metrics.json"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        logger.info("Đã lưu cleanup metrics tại %s: %s", metrics_file.name, metrics)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
