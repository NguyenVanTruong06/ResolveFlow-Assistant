"""
src/ui/director/pages/build_page.py
Bước 4: Xác Nhận & Kiểm Tra Nguồn, Tài Nguyên.
Bao gồm:
- Thẻ tóm tắt toàn bộ lựa chọn (Footage, Kiểu dựng, Tính năng, Câu thoại, Kịch bản, Định dạng FCPXML).
- Tùy chọn: Phủ phụ đề Kinetic lên Track V2, Beat-sync với nhạc nền.
- Trình chọn nhạc nền kèm nút mở Thư viện Nhạc nền.
- Bảng hiển thị kết quả kiểm tra nguồn & tài nguyên (readonly, max 200 dòng, không thumbnail/IO).
"""

from typing import Any, List, Tuple, Optional, Mapping
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal
from src.ui.director.director_state import DirectorConfig
from src.ui.director.workers.recovery_assessment_worker import RecoveryAssessmentWorker
from src.ui.director.widgets.recovery_assessment_dialog import RecoveryAssessmentDialog


class BuildSummaryCard(QFrame):
    """Thẻ tóm tắt toàn bộ lựa chọn trước khi kiểm tra xuất bản."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame#build_summary_card {
                background-color: #202227;
                border: 1px solid #2e3138;
                border-left: 3px solid #d94f3d;
                border-radius: 5px;
                padding: 12px;
            }
        """)
        self.setObjectName("build_summary_card")
        self._init_ui()

    def _init_ui(self):
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(14, 14, 14, 14)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(10)

        # Labels
        self.lbl_footage = QLabel("0 clip · 00:00")
        self.lbl_style = QLabel("Vlog có Hook")
        self.lbl_features = QLabel("Không")
        self.lbl_dialogue = QLabel("Giữ 0 · Cắt 0")
        self.lbl_script = QLabel("Gemini (Cloud)")
        self.lbl_export = QLabel("FCPXML 1.9 → Timeline mới trong DaVinci Resolve")

        fields = [
            ("Footage nguồn", self.lbl_footage),
            ("Kiểu dựng", self.lbl_style),
            ("Tính năng", self.lbl_features),
            ("Câu thoại", self.lbl_dialogue),
            ("Kịch bản", self.lbl_script),
            ("Định dạng xuất", self.lbl_export),
        ]

        for row, (name, val_widget) in enumerate(fields):
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet("color: #71717a; font-size: 11.5px; font-weight: 500;")
            self.grid.addWidget(lbl_name, row, 0)

            val_widget.setStyleSheet("color: #e4e4e7; font-size: 12px; font-weight: 600;")
            self.grid.addWidget(val_widget, row, 1)

    def update_summary(self, config: DirectorConfig):
        clip_count = len(config.video_paths)
        self.lbl_footage.setText(f"{clip_count} clip đã nạp" if clip_count > 0 else "Chưa chọn clip")

        style_map = {
            "podcast": "Podcast, phỏng vấn",
            "shorts": "Shorts, TikTok 9:16",
            "vlog": "Vlog có Hook",
            "custom": "Tự chỉnh"
        }
        style_name = style_map.get(config.style, config.style)
        self.lbl_style.setText(f"{style_name} · {config.footage_type} · {config.pacing}")

        feat_str = ", ".join(config.enabled_features) if config.enabled_features else "Mặc định"
        self.lbl_features.setText(feat_str)

        if config.web_prompt_pasted_data:
            self.lbl_script.setText(f"Web Prompt ({config.web_prompt_summary or 'Đã dán'})")
        elif config.ai_engine_mode == "ollama":
            self.lbl_script.setText("Local Ollama (Qwen 2.5)")
        else:
            self.lbl_script.setText(f"Cloud API ({config.cloud_provider.upper()})")


class BuildPage(QWidget):
    """Trang Bước 4: Kiểm tra nguồn & tài nguyên, xác nhận cấu hình dựng timeline."""
    bgm_browse_requested = Signal()
    config_changed = Signal()
    readiness_cancel_requested = Signal()
    recovery_inspection_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kinetic = True
        self._beat_sync = True
        self._bgm_name = "Chill Vlog Lofi"
        self._recovery_worker: Optional[RecoveryAssessmentWorker] = None
        self._recovery_dialog: Optional[RecoveryAssessmentDialog] = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        lbl_h3 = QLabel("Bước 4: Xác nhận cấu hình & Kiểm tra nguồn, tài nguyên")
        lbl_h3.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl_h3)

        # Card tóm tắt
        self.summary_card = BuildSummaryCard(self)
        layout.addWidget(self.summary_card)

        # Tùy chọn bổ trợ
        lbl_opt = QLabel("Tùy chọn bổ trợ:")
        lbl_opt.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 4px;")
        layout.addWidget(lbl_opt)

        self.chk_kinetic = QCheckBox("Phủ phụ đề Kinetic Subtitle lên Track Video 2 (Style: Karaoke Pop)", self)
        self.chk_kinetic.setChecked(True)
        self.chk_kinetic.setStyleSheet("font-size: 12px; color: #e4e4e7;")
        self.chk_kinetic.toggled.connect(self._on_kinetic_toggled)
        layout.addWidget(self.chk_kinetic)

        self.chk_beatsync = QCheckBox("Beat-sync chuyển cảnh và punch-in theo nhịp nhạc nền", self)
        self.chk_beatsync.setChecked(True)
        self.chk_beatsync.setStyleSheet("font-size: 12px; color: #e4e4e7;")
        self.chk_beatsync.toggled.connect(self._on_beatsync_toggled)
        layout.addWidget(self.chk_beatsync)

        # Hàng nhạc nền
        h_bgm = QHBoxLayout()
        h_bgm.setSpacing(6)
        self.lbl_bgm = QLabel(f"Nhạc nền: {self._bgm_name} ·")
        self.lbl_bgm.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        h_bgm.addWidget(self.lbl_bgm)

        btn_pick_bgm = QPushButton("Đổi trong thư viện Nhạc nền", self)
        btn_pick_bgm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pick_bgm.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #f59e0b;
                border: none;
                font-size: 11.5px;
                font-weight: 600;
                text-decoration: underline;
            }
            QPushButton:hover {
                color: #fbbf24;
            }
        """)
        btn_pick_bgm.clicked.connect(self.bgm_browse_requested.emit)
        h_bgm.addWidget(btn_pick_bgm)
        h_bgm.addStretch(1)
        layout.addLayout(h_bgm)

        # -------------------------------------------------------------
        # Khu vực kiểm tra nguồn & tài nguyên (Readiness Feedback)
        # -------------------------------------------------------------
        lbl_readiness_hdr = QLabel("Kiểm tra nguồn footage & tài nguyên thư viện:")
        lbl_readiness_hdr.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 4px;")
        layout.addWidget(lbl_readiness_hdr)

        # Hàng trạng thái & nút Hủy
        h_status = QHBoxLayout()
        h_status.setSpacing(10)

        self.lbl_readiness_status = QLabel("Chưa kiểm tra. Nhấn 'Kiểm tra nguồn & tài nguyên' để bắt đầu.", self)
        self.lbl_readiness_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_readiness_status.setWordWrap(True)
        self.lbl_readiness_status.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        h_status.addWidget(self.lbl_readiness_status, 1)

        self.btn_cancel_readiness = QPushButton("✕ Hủy kiểm tra", self)
        self.btn_cancel_readiness.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel_readiness.setStyleSheet("""
            QPushButton {
                background-color: rgba(239, 68, 68, 0.15);
                color: #fca5a5;
                border: 1px solid rgba(239, 68, 68, 0.35);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.25);
                color: #fecaca;
            }
        """)
        self.btn_cancel_readiness.setVisible(False)
        self.btn_cancel_readiness.clicked.connect(self.readiness_cancel_requested.emit)
        h_status.addWidget(self.btn_cancel_readiness, 0)
        layout.addLayout(h_status)

        # Banner số dòng hiển thị
        self.lbl_readiness_count = QLabel("", self)
        self.lbl_readiness_count.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_readiness_count.setStyleSheet("color: #71717a; font-size: 11px;")
        self.lbl_readiness_count.setVisible(False)
        layout.addWidget(self.lbl_readiness_count)

        # Bảng quan sát thuần văn bản (Readonly plain text)
        self.table_readiness = QTableWidget(0, 4, self)
        self.table_readiness.setHorizontalHeaderLabels(["Loại", "Định danh", "Trạng thái", "Chi tiết"])
        self.table_readiness.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_readiness.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_readiness.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_readiness.verticalHeader().setVisible(False)
        self.table_readiness.horizontalHeader().setStretchLastSection(True)
        self.table_readiness.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_readiness.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_readiness.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_readiness.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_readiness.setMinimumHeight(120)
        self.table_readiness.setMaximumHeight(170)
        self.table_readiness.setStyleSheet("""
            QTableWidget {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                gridline-color: #27272a;
                color: #e4e4e7;
                font-size: 11.5px;
            }
            QHeaderView::section {
                background-color: #202024;
                color: #a1a1aa;
                padding: 4px 8px;
                border: none;
                border-right: 1px solid #27272a;
                border-bottom: 1px solid #27272a;
                font-weight: 600;
                font-size: 11.5px;
            }
        """)
        layout.addWidget(self.table_readiness)

        # -------------------------------------------------------------
        # Khu vực đối soát phục hồi phiên thi công cũ (Recovery Inspection)
        # -------------------------------------------------------------
        h_recovery = QHBoxLayout()
        self.btn_inspect_recovery = QPushButton("🔍 Đối soát phục hồi phiên thi công cũ", self)
        self.btn_inspect_recovery.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_inspect_recovery.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #ffffff;
            }
        """)
        self.btn_inspect_recovery.clicked.connect(self._on_inspect_recovery_clicked)
        h_recovery.addWidget(self.btn_inspect_recovery)
        h_recovery.addStretch(1)
        layout.addLayout(h_recovery)

        layout.addStretch(1)

    def _on_kinetic_toggled(self, checked: bool):
        self._kinetic = checked
        self.config_changed.emit()

    def _on_beatsync_toggled(self, checked: bool):
        self._beat_sync = checked
        self.config_changed.emit()

    def update_summary(self, config: DirectorConfig):
        self.summary_card.update_summary(config)

    def set_bgm(self, name: str):
        self._bgm_name = name
        self.lbl_bgm.setText(f"Nhạc nền: {name} ·")

    def load_from_config(self, config: DirectorConfig):
        self._kinetic = config.kinetic_subtitles_v2
        self.chk_kinetic.setChecked(config.kinetic_subtitles_v2)
        self._beat_sync = config.beat_sync
        self.chk_beatsync.setChecked(config.beat_sync)
        if config.bgm_name:
            self.set_bgm(config.bgm_name)
        self.summary_card.update_summary(config)

    def save_to_config(self, config: DirectorConfig):
        config.kinetic_subtitles_v2 = self._kinetic
        config.beat_sync = self._beat_sync
        config.bgm_name = self._bgm_name

    def set_readiness_busy(self, busy: bool) -> None:
        """Cập nhật trạng thái đang kiểm tra hoặc rảnh của tiến trình tiền kiểm."""
        self.btn_cancel_readiness.setVisible(busy)
        self.btn_cancel_readiness.setEnabled(busy)
        self.chk_kinetic.setEnabled(not busy)
        self.chk_beatsync.setEnabled(not busy)
        if busy:
            self.lbl_readiness_status.setText("⏳ Đang kiểm tra nguồn và tài nguyên thư viện...")
            self.lbl_readiness_status.setStyleSheet("color: #f59e0b; font-size: 11.5px; font-weight: 500;")

    def show_readiness_result(self, result: Any, target_authority: Optional[Any] = None) -> None:
        """Hiển thị kết quả kiểm tra nguồn và tài nguyên từ SourceCatalogReadinessResult."""
        status = getattr(result, "status", "")
        checks_passed = getattr(result, "checks_passed", False)
        ready_to_execute = getattr(result, "ready_to_execute", False)
        # Ràng buộc G07-07A5-I: ready_to_execute luôn False trong toàn bộ lát cắt hiện tại
        assert ready_to_execute is False

        self._last_target_authority = target_authority

        src_exec_ev = getattr(result, "source_execution_evidence", None)
        has_deep_evidence = src_exec_ev is not None and isinstance(src_exec_ev, (dict, Mapping)) and len(src_exec_ev) > 0

        # Cập nhật thông điệp trạng thái và execution lock banner
        if status == "completed" and checks_passed:
            if has_deep_evidence:
                msg = (
                    "✓ Nguồn footage, tài nguyên và hình học kịch bản đã được xác minh chuyên sâu (CFR & Full SHA-256). "
                    "🔒 Thi công timeline đang khóa (ready_to_execute=False)."
                )
            else:
                msg = (
                    "✓ Nguồn và tài nguyên khả dụng (siêu dữ liệu). (Kịch bản chưa quét bằng chứng chuyên sâu). "
                    "🔒 Thi công timeline đang khóa (ready_to_execute=False)."
                )
            self.lbl_readiness_status.setText(msg)
            self.lbl_readiness_status.setStyleSheet("color: #4ade80; font-size: 11.5px; font-weight: 500;")
        elif status == "cancelled":
            self.lbl_readiness_status.setText("⚠️ Tiến trình kiểm tra đã bị hủy theo yêu cầu. 🔒 Thi công timeline đang khóa.")
            self.lbl_readiness_status.setStyleSheet("color: #fbbf24; font-size: 11.5px; font-weight: 500;")
        elif status == "failed" or not checks_passed:
            diags = getattr(result, "diagnostics", ())
            diag_summary = "; ".join(diags[:2]) if diags else "Thiếu nguồn hoặc kho tài nguyên không khả dụng."
            self.lbl_readiness_status.setText(f"❌ Không vượt qua kiểm tra: {diag_summary} 🔒 Thi công timeline đang khóa.")
            self.lbl_readiness_status.setStyleSheet("color: #f87171; font-size: 11.5px; font-weight: 500;")
        else:
            self.lbl_readiness_status.setText(f"Trạng thái: {status}. 🔒 Thi công timeline đang khóa.")
            self.lbl_readiness_status.setStyleSheet("color: #a1a1aa; font-size: 11.5px; font-weight: 500;")

        # Thu thập các dòng kết quả dạng văn bản thuần
        rows: List[Tuple[str, str, str, str]] = []

        is_authority_mode = target_authority is not None

        # 1. Trạng thái phê duyệt biên tập (Editorial State - hiển thị trong chế độ thẩm quyền đích)
        if is_authority_mode:
            prop_id = getattr(result, "proposal_id", "")
            prop_rev = getattr(result, "proposal_revision", "")
            appr_id = getattr(result, "approval_id", "")
            if prop_id and appr_id:
                rows.append((
                    "Phê duyệt biên tập",
                    f"{prop_id[:12]}",
                    "Đã phê duyệt",
                    f"Hồ sơ: {appr_id[:12]}, Phiên bản: {prop_rev[:12]} (Toàn kịch bản)",
                ))

            # 2. Quan sát Đích DaVinci Resolve (Target Observation)
            t_ok = getattr(target_authority, "ok", False)
            p_name = getattr(target_authority, "project_name", "") or "DaVinci Resolve"
            tl_name = getattr(target_authority, "timeline_name", "") or "Timeline"
            t_fps = getattr(target_authority, "timeline_fps", None)
            if t_ok and t_fps is not None:
                w = getattr(target_authority, "width", 0)
                h = getattr(target_authority, "height", 0)
                dim_str = f", {w}x{h}" if w and h else ""
                rows.append((
                    "Đích Resolve",
                    f"{p_name} / {tl_name}",
                    "Timeline hợp lệ",
                    f"FPS: {t_fps} (chuẩn tắc){dim_str}",
                ))
            else:
                err_c = getattr(target_authority, "error_code", "ERR_TARGET_UNAVAILABLE")
                err_m = getattr(target_authority, "error_message", "Chưa kết nối timeline Resolve.")
                rows.append((
                    "Đích Resolve",
                    f"{p_name} / {tl_name}",
                    f"Chưa sẵn sàng ({err_c})",
                    err_m,
                ))

        # 3. Nguồn Footage (Source Observations & Deep Evidence)
        src_obs = getattr(result, "source_observations", ())
        src_exec_map = src_exec_ev if isinstance(src_exec_ev, (dict, Mapping)) else {}
        for obs in src_obs:
            cid = getattr(obs, "clip_id", "")
            kind = "Nguồn footage"
            ident = cid
            err_code = getattr(obs, "error_code", "")
            is_readable = getattr(obs, "is_readable", False)
            recheck_passed = getattr(obs, "recheck_passed", False)

            ev_env = src_exec_map.get(cid)
            if ev_env is not None and getattr(ev_env, "complete_scan", False) and getattr(ev_env, "frame_evidence", None):
                fe = getattr(ev_env, "frame_evidence", None)
                fe_stat = getattr(fe, "evidence_status", "")
                fe_frames = getattr(fe, "total_frames", 0)
                fe_fps = getattr(fe, "source_fps", "")
                sha = getattr(ev_env, "observed_full_sha256", "")
                sha_short = f"{sha[:10]}..." if sha else ""
                if fe_stat == "VERIFIED_CFR" and not err_code:
                    st = "Xác minh CFR"
                    detail = f"{getattr(obs, 'path', '')} [SHA: {sha_short}, {fe_frames} frames @ {fe_fps}]"
                else:
                    st = f"Lỗi ({fe_stat or err_code})"
                    detail = f"{getattr(obs, 'path', '')} - {getattr(obs, 'error_message', '')}"
            else:
                if not err_code and is_readable and recheck_passed:
                    st = "Khả dụng"
                else:
                    st = f"Lỗi ({err_code or 'Không khả dụng'})"
                path = getattr(obs, "path", "")
                err_msg = getattr(obs, "error_message", "")
                detail = f"{path} - {err_msg}" if err_msg else path
            rows.append((kind, ident, st, detail))

        # 4. Hình học kịch bản Formula B (nếu có)
        geom = getattr(result, "source_geometry", None)
        if geom is not None:
            g_status = getattr(geom, "status", "")
            if g_status == "GEOMETRY_VERIFIED":
                g_st = "Đã xác minh"
                tf = getattr(geom, "total_timeline_frames", 0)
                fps_val = getattr(geom, "timeline_fps", "")
                g_detail = f"Tổng {tf} frames @ {fps_val} (Formula B)"
            else:
                g_st = f"Lỗi ({g_status})"
                issues = getattr(geom, "issues", ())
                g_detail = "; ".join(getattr(i, "message", str(i)) for i in issues[:2]) if issues else g_status
            rows.append(("Hình học kịch bản", "Formula B", g_st, g_detail))

        # 5. Catalog master tài nguyên (Catalog Observations)
        cat_obs = getattr(result, "catalog_observations", ())
        for obs in cat_obs:
            source_kind = getattr(obs, "source_kind", "")
            kind = f"Tài nguyên ({source_kind})" if source_kind else "Tài nguyên"
            demand = getattr(obs, "demand_id", "")
            asset_id = getattr(obs, "asset_id", "")
            ident = f"{demand} → {asset_id}" if demand else asset_id
            guard_status = getattr(obs, "guard_status", "")
            guard_allowed = getattr(obs, "guard_allowed", False)
            err_code = getattr(obs, "error_code", "")
            if guard_allowed and not err_code:
                st = f"Khả dụng ({guard_status})"
            else:
                st = f"Từ chối ({err_code or guard_status})"
            resolved = getattr(obs, "resolved_master_path", "")
            err_msg = getattr(obs, "error_message", "")
            detail = f"{resolved} - {err_msg}" if err_msg else (resolved or guard_status)
            rows.append((kind, ident, st, detail))

        # 6. Khóa thi công (Execution Lock state row - hiển thị trong chế độ thẩm quyền đích)
        if is_authority_mode:
            rows.append((
                "Thi công timeline",
                "Trạng thái khóa",
                "ĐANG KHÓA",
                "ready_to_execute=False (Milestone M1 chỉ hỗ trợ tiền kiểm, chưa mở executor)",
            ))

        total_count = len(rows)
        MAX_DISPLAY_ROWS = 200
        display_rows = rows[:MAX_DISPLAY_ROWS]

        if total_count > MAX_DISPLAY_ROWS:
            self.lbl_readiness_count.setText(f"Hiển thị {len(display_rows)}/tổng số {total_count} dòng")
            self.lbl_readiness_count.setVisible(True)
        elif total_count > 0:
            self.lbl_readiness_count.setText(f"Hiển thị {total_count}/{total_count} dòng")
            self.lbl_readiness_count.setVisible(True)
        else:
            self.lbl_readiness_count.setText("Không có dòng quan sát nào.")
            self.lbl_readiness_count.setVisible(True)

        self.table_readiness.setRowCount(len(display_rows))
        for r_idx, (col0, col1, col2, col3) in enumerate(display_rows):
            it0 = QTableWidgetItem(str(col0))
            it1 = QTableWidgetItem(str(col1))
            it2 = QTableWidgetItem(str(col2))
            it3 = QTableWidgetItem(str(col3))
            for it in (it0, it1, it2, it3):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_readiness.setItem(r_idx, 0, it0)
            self.table_readiness.setItem(r_idx, 1, it1)
            self.table_readiness.setItem(r_idx, 2, it2)
            self.table_readiness.setItem(r_idx, 3, it3)

    def clear_readiness_result(self, message: str = "") -> None:
        """Xóa trắng bảng kết quả và đặt lại thông điệp trạng thái."""
        self.table_readiness.setRowCount(0)
        self.lbl_readiness_count.setText("")
        self.lbl_readiness_count.setVisible(False)
        default_msg = "Chưa kiểm tra. Nhấn 'Kiểm tra nguồn & tài nguyên' để bắt đầu."
        self.lbl_readiness_status.setText(message or default_msg)
        self.lbl_readiness_status.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")

    def _on_inspect_recovery_clicked(self) -> None:
        """Kích hoạt yêu cầu đối soát phục hồi cho phiên thi công."""
        self.recovery_inspection_requested.emit()

    def launch_recovery_assessment(
        self,
        base_dir: str,
        execution_id: str,
        authority_snapshot: Optional[Any] = None,
    ) -> None:
        """Khởi động worker đối soát ngoài luồng GUI và chuẩn bị mở hộp thoại chỉ đọc."""
        if self._recovery_worker is not None and self._recovery_worker.isRunning():
            return

        self._recovery_worker = RecoveryAssessmentWorker(
            base_dir=base_dir,
            execution_id=execution_id,
            authority_snapshot=authority_snapshot,
            parent=self,
        )
        self._recovery_worker.assessment_completed.connect(self._on_recovery_assessment_completed)
        self._recovery_worker.assessment_io_error.connect(self._on_recovery_assessment_io_error)
        self._recovery_worker.assessment_failed.connect(self._on_recovery_assessment_failed)
        self._recovery_worker.start()

    def _on_recovery_assessment_completed(self, assessment: Any) -> None:
        if self._recovery_dialog is None:
            self._recovery_dialog = RecoveryAssessmentDialog(assessment=assessment, parent=self)
        else:
            self._recovery_dialog.show_assessment(assessment)
        self._recovery_dialog.show()
        self._recovery_dialog.raise_()
        self._recovery_dialog.activateWindow()

    def _on_recovery_assessment_io_error(self, err_msg: str) -> None:
        if self._recovery_dialog is None:
            self._recovery_dialog = RecoveryAssessmentDialog(io_error=err_msg, parent=self)
        else:
            self._recovery_dialog.show_io_error(err_msg)
        self._recovery_dialog.show()
        self._recovery_dialog.raise_()
        self._recovery_dialog.activateWindow()

    def _on_recovery_assessment_failed(self, err_msg: str) -> None:
        if self._recovery_dialog is None:
            self._recovery_dialog = RecoveryAssessmentDialog(io_error=err_msg, parent=self)
        else:
            self._recovery_dialog.show_io_error(err_msg)
        self._recovery_dialog.show()
        self._recovery_dialog.raise_()
        self._recovery_dialog.activateWindow()

