"""Kiểm duyệt pack và chọn kho: bảng ảo hóa, IO nền, không copy nguồn liên kết."""
from __future__ import annotations
import copy
import itertools
from pathlib import Path
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableView, QStyledItemDelegate, QFileDialog, QMessageBox,
    QInputDialog, QAbstractItemView, QHeaderView, QCheckBox, QWidget)
from src.core.storage_manager import StorageManager
from src.core.pack_ingest import PackIngestService, validate_draft_taxonomy

STYLE = '''QDialog {background:#16171a;color:#e6e8ec} QLabel {color:#c8ccd4}
QCheckBox {color:#c8ccd4;spacing:7px}
QLineEdit,QComboBox,QTableView {background:#111214;color:#e6e8ec;border:1px solid #2e3138;border-radius:4px;padding:5px}
QLineEdit:focus,QComboBox:focus {border:1px solid #e05a47}
QPushButton {background:#23252a;color:#e6e8ec;border:1px solid #353942;border-radius:4px;padding:6px 10px;font-weight:600}
QPushButton:hover {background:#2c2f36;border-color:#4b505c} QPushButton:disabled {color:#5f6470;background:#18191c;border-color:#25272c}
QHeaderView::section {background:#1c1d21;color:#9da3ae;border:none;border-bottom:1px solid #2e3138;padding:6px;font-weight:600}'''


class LibraryJob(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)

    active_jobs = set()

    def __init__(self, operation, parent=None):
        super().__init__(parent); self.operation = operation
        self.finished.connect(lambda: LibraryJob.active_jobs.discard(self))

    def start(self):
        self.active_jobs.add(self)
        super().start()

    def run(self):
        try: self.result.emit(self.operation(self.isInterruptionRequested, self.progress.emit))
        except Exception as exc: self.error.emit(str(exc))


class IngestReviewModel(QAbstractTableModel):
    headers = ('Nhập', 'File', 'Danh mục', 'Nhóm con', 'Tags (dấu phẩy)', 'MiB')

    def __init__(self, assets=None, parent=None):
        super().__init__(parent); self.assets = assets if assets is not None else []

    def rowCount(self, parent=QModelIndex()): return 0 if parent.isValid() else len(self.assets)
    def columnCount(self, parent=QModelIndex()): return 0 if parent.isValid() else 6
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole: return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        item = self.assets[index.row()]; col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Unchecked if item.get('excluded') else Qt.CheckState.Checked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            return ('', item['relative_path'], item['category'], item.get('subcategory',''),
                    ', '.join(item.get('tags', [])), f'{item.get("size_bytes",0)/1024**2:.2f}')[col]
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 0: return flags | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in (2, 3, 4): return flags | Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid(): return False
        item = self.assets[index.row()]; col = index.column()
        if col == 0 and role == Qt.ItemDataRole.CheckStateRole:
            item['excluded'] = value not in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        elif role == Qt.ItemDataRole.EditRole and col in (2, 3, 4):
            item[{2:'category',3:'subcategory',4:'tags'}[col]] = ([t.strip() for t in str(value).split(',') if t.strip()] if col == 4 else str(value).strip())
        else: return False
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole]); return True


class CategoryDelegate(QStyledItemDelegate):
    def __init__(self, storage, parent=None, taxonomy_provider=None):
        super().__init__(parent)
        self.storage = storage
        self.taxonomy_provider = taxonomy_provider or storage.taxonomy
    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for cid, label in self.taxonomy_provider().items(): editor.addItem(f'{label} · {cid}', cid)
        return editor
    def setEditorData(self, editor, index): editor.setCurrentIndex(editor.findData(index.data(Qt.ItemDataRole.EditRole)))
    def setModelData(self, editor, model, index): model.setData(index, editor.currentData())


class PackIngestWizard(QDialog):
    pack_committed = Signal(object)
    library_changed = Signal()

    def __init__(self, storage=None, parent=None, source=None, url=None, auto_prepare=False, prepare_factory=None, pack_id=None, pack_name=None):
        if prepare_factory is not None and not callable(prepare_factory):
            raise TypeError("prepare_factory phải là một callable(cancel, progress) -> job dict.")
        super().__init__(parent); self.storage = storage or StorageManager()
        self._pack_id = pack_id
        self._pack_name = pack_name
        self.service = PackIngestService(self.storage); self.job = None; self.worker = None
        self._terminal = False; self._closing = False; self._outcome = None; self._pack_committed = False
        self._prepare_factory = prepare_factory
        self._draft_taxonomy = {}
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.timeout.connect(self._prepare)

        # Silent Default Storage: Tự động liên kết và kích hoạt kho lưu trữ mặc định
        if not self.storage.online:
            try:
                default_dir = (self.storage.config_dir.parent / "Library").resolve()
                default_dir.mkdir(parents=True, exist_ok=True)
                self.storage.reserve_bytes = 0
                self.storage.configure(default_dir)
            except Exception as exc:
                logger.warning("Không thể tự động kích hoạt kho mặc định: %s", exc)

        self.setWindowTitle('ChunDVC · Kiểm duyệt nhập kho'); self.resize(1000, 620); self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)

        # Thanh trạng thái kho lưu trữ tinh gọn
        storage_bar = QHBoxLayout()
        root_disp = str(self.storage.root) if self.storage.online else "Kho mặc định máy tính"
        self.lbl_status = QLabel(f"📁 Thư viện lưu trữ: <b style='color:#38bdf8;'>{root_disp}</b>")
        self.lbl_status.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        self.lbl_status.setWordWrap(True)
        storage_bar.addWidget(self.lbl_status, stretch=1)

        self.btn_storage = QPushButton('⚙️ Cài Đặt Kho...')
        self.btn_storage.setToolTip('Đổi thư mục lưu trữ hoặc di dời sang ổ đĩa ngoài')
        self.btn_storage.setStyleSheet(
            "QPushButton { background: transparent; color: #a1a1aa; border: 1px solid #3f3f46; "
            "border-radius: 4px; padding: 3px 8px; font-size: 11px; } "
            "QPushButton:hover { background: #27272a; color: #ffffff; border-color: #71717a; }"
        )
        self.btn_storage.clicked.connect(self._storage_settings)
        storage_bar.addWidget(self.btn_storage)

        self.btn_quick_local_storage = QPushButton('⚡ Kho Mặc Định')
        self.btn_quick_local_storage.clicked.connect(self._use_default_local_storage)
        self.btn_quick_local_storage.setVisible(not self.storage.online)
        storage_bar.addWidget(self.btn_quick_local_storage)
        layout.addLayout(storage_bar)

        row = QHBoxLayout(); self.input_source = QLineEdit(source or url or '')
        placeholder = 'Gói sinh tự động: Starter Pack (SFX & LUTs)' if prepare_factory is not None and not (source or url) else 'File ZIP/.rfpack, thư mục hoặc liên kết Google Drive'
        self.input_source.setPlaceholderText(placeholder)
        row.addWidget(self.input_source, 1)
        self.btn_choose_file = QPushButton('Chọn file'); self.btn_choose_file.clicked.connect(self._pick_file); row.addWidget(self.btn_choose_file)
        self.btn_choose_folder = QPushButton('Chọn thư mục'); self.btn_choose_folder.clicked.connect(self._pick_folder); row.addWidget(self.btn_choose_folder)
        self.btn_prepare = QPushButton('1. Quét & kiểm tra'); self.btn_prepare.clicked.connect(self._prepare); row.addWidget(self.btn_prepare)
        layout.addLayout(row)

        self._current_warnings = []
        self._current_suggestions = {}

        self.warning_widget = QWidget(self)
        warn_layout = QHBoxLayout(self.warning_widget)
        warn_layout.setContentsMargins(4, 2, 4, 2)
        self.lbl_warning_summary = QLabel(self.warning_widget)
        self.lbl_warning_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_warning_summary.setWordWrap(True)
        self.lbl_warning_summary.setStyleSheet("color: #e5c07b; font-weight: 500; font-size: 12px;")
        self.btn_view_warnings = QPushButton('⚠️ Xem cảnh báo', self.warning_widget)
        self.btn_view_warnings.clicked.connect(self._show_warnings_dialog)
        warn_layout.addWidget(self.lbl_warning_summary, 1)
        warn_layout.addWidget(self.btn_view_warnings)
        self.warning_widget.setVisible(False)
        layout.addWidget(self.warning_widget)

        names = QHBoxLayout(); self.name = QLineEdit(pack_name or 'Pack'); self.version = QLineEdit('1.0'); self.version.setMaximumWidth(100)
        names.addWidget(QLabel('Tên pack')); names.addWidget(self.name, 1); names.addWidget(QLabel('Phiên bản')); names.addWidget(self.version)
        layout.addLayout(names)
        self.model = IngestReviewModel(); self.table = QTableView(); self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setItemDelegateForColumn(2, CategoryDelegate(self.storage, self.table, self.effective_taxonomy))
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0,48); self.table.setColumnWidth(4,170)
        layout.addWidget(self.table, 1)
        apply = QHBoxLayout(); self.category = QComboBox()
        self._reload_categories(); apply.addWidget(self.category)
        create = QPushButton('+ Danh mục'); create.clicked.connect(self._add_category); apply.addWidget(create)
        self.sub = QLineEdit(); self.sub.setPlaceholderText('Nhóm con'); apply.addWidget(self.sub)
        self.tags = QLineEdit(); self.tags.setPlaceholderText('Tags, cách bằng dấu phẩy'); apply.addWidget(self.tags)
        assign = QPushButton('Gán cho hàng chọn'); assign.clicked.connect(self._apply_rows); apply.addWidget(assign)
        layout.addLayout(apply)
        layout.addWidget(QLabel('Bỏ dấu ✓ để loại file. Nhấp đúp ô để sửa. File hướng dẫn/preview chuyển cảnh mặc định không nhập.'))
        actions = QHBoxLayout(); actions.addStretch()
        self.btn_cancel = QPushButton('Hủy / Dọn staging'); self.btn_cancel.clicked.connect(self.reject); actions.addWidget(self.btn_cancel)
        self.btn_commit = QPushButton('2. Nhập vào kho'); self.btn_commit.clicked.connect(self._commit); actions.addWidget(self.btn_commit)
        layout.addLayout(actions); self._update_buttons()
        if auto_prepare and (source or url or prepare_factory is not None) and self.storage.online:
            self._prepare_timer.start(0)

    def is_pack_committed(self) -> bool:
        return self._pack_committed

    def _storage_settings(self):
        dialog = StorageSettingsDialog(self.storage, self)
        dialog.library_changed.connect(self.library_changed.emit)
        dialog.exec()
        self._reload_categories(); self._update_buttons()
        self.lbl_status.setText('Online · ' + str(self.storage.root) if self.storage.online else 'Offline · Kết nối lại kho trước khi tải.')

    def _use_default_local_storage(self):
        if self.worker:
            return
        try:
            default_dir = (self.storage.config_dir.parent / "Library").resolve()
            default_dir.mkdir(parents=True, exist_ok=True)
            self.storage.reserve_bytes = 0
            self.storage.configure(default_dir)
            self._reload_categories()
            self._update_buttons()
            self.lbl_status.setText('Online · ' + str(self.storage.root))
            self.library_changed.emit()
            if (self.input_source.text().strip() or self._prepare_factory is not None) and self.job is None:
                self._prepare()
        except Exception as exc:
            QMessageBox.warning(self, 'Không thể tạo kho mặc định', str(exc))

    def effective_taxonomy(self):
        # Danh mục có sẵn luôn ưu tiên; draft không được đổi nhãn của kho.
        return dict(self._draft_taxonomy) | dict(self.storage.taxonomy())

    def _reload_categories(self):
        selected = self.category.currentData()
        self.category.clear()
        for cid, label in self.effective_taxonomy().items():
            self.category.addItem(f'{label} · {cid}', cid)
        if selected is not None:
            self.category.setCurrentIndex(self.category.findData(selected))

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Chọn gói', '', 'Gói/media (*.zip *.rfpack *.drfx *.mp4 *.wav *.mp3 *.cube *.setting);;Tất cả (*)')
        if path: self.input_source.setText(path)
    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Chọn thư mục để sao chép (dùng Liên kết nếu không muốn copy)')
        if path: self.input_source.setText(path)

    def _add_category(self):
        if self.worker: return
        cid, ok = QInputDialog.getText(self, 'Danh mục mới', 'ID (ví dụ product_review):')
        if not ok: return
        label, ok = QInputDialog.getText(self, 'Tên danh mục', 'Tên hiển thị:')
        if not ok: return
        try:
            draft = validate_draft_taxonomy({cid: label})
            if cid in self.storage.taxonomy():
                QMessageBox.information(self, 'Danh mục đã có', 'Đã chọn danh mục có sẵn; nhãn trong kho được giữ nguyên.')
            else:
                self._draft_taxonomy.update(draft)
            self._reload_categories()
            self.category.setCurrentIndex(self.category.findData(cid))
        except Exception as exc:
            QMessageBox.warning(self, 'Không tạo được', str(exc))

    def _apply_rows(self):
        for index in self.table.selectionModel().selectedRows():
            for col, value in ((2,self.category.currentData()), (3,self.sub.text()), (4,self.tags.text())):
                self.model.setData(self.model.index(index.row(), col), value)

    def _update_buttons(self):
        busy = self.worker is not None
        has_factory = self._prepare_factory is not None
        self.btn_storage.setEnabled(not busy and self.job is None)
        if hasattr(self, 'btn_quick_local_storage'):
            self.btn_quick_local_storage.setVisible(not self.storage.online)
            self.btn_quick_local_storage.setEnabled(not busy and self.job is None)
        self.input_source.setEnabled(not busy and self.job is None and not has_factory)
        if hasattr(self, 'btn_choose_file'): self.btn_choose_file.setEnabled(not busy and self.job is None and not has_factory)
        if hasattr(self, 'btn_choose_folder'): self.btn_choose_folder.setEnabled(not busy and self.job is None and not has_factory)
        self.name.setEnabled(not busy); self.version.setEnabled(not busy)
        self.btn_prepare.setEnabled(not busy and self.storage.online and self.job is None)
        self.btn_commit.setEnabled(not busy and self.storage.online and self.job is not None)
        self.table.setEnabled(not busy)

    def _run(self, operation, kind):
        if self._terminal and kind != 'discard':
            return
        self._outcome = None; self._kind = kind
        self.worker = LibraryJob(operation, self)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.result.connect(lambda result: setattr(self, '_outcome', (True,result)))
        self.worker.error.connect(lambda error: setattr(self, '_outcome', (False,error)))
        self.worker.finished.connect(self._finished)
        self._update_buttons(); self.worker.start()

    def _reset_warnings(self):
        self._current_warnings = []
        self._current_suggestions = {}
        if hasattr(self, 'warning_widget'):
            self.warning_widget.setVisible(False)
            self.lbl_warning_summary.setText('')

    def _update_warnings_presentation(self, result: dict):
        raw_warns = result.get('warnings', []) if isinstance(result, dict) else []
        raw_sugs = result.get('manifest_suggestions', {}) if isinstance(result, dict) else {}
        self._current_warnings = [str(w) for w in raw_warns] if isinstance(raw_warns, list) else []
        self._current_suggestions = dict(raw_sugs) if isinstance(raw_sugs, dict) else {}

        if not self._current_warnings:
            self.warning_widget.setVisible(False)
            self.lbl_warning_summary.setText('')
            return

        count = len(self._current_warnings)
        has_unknown = any('chưa đăng ký' in w.lower() for w in self._current_warnings)
        summary = f"Có {count} cảnh báo kiểm tra gói — hãy xem trước khi nhập."
        if has_unknown:
            summary += " (Đã giữ nguyên phân loại tự động cho danh mục chưa đăng ký)."

        self.lbl_warning_summary.setText(summary)
        self.lbl_warning_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.warning_widget.setVisible(True)

    def _show_warnings_dialog(self):
        if not self._current_warnings:
            return None

        max_warnings = 100
        max_bytes = 16384
        max_suggestions = 50

        total_warnings_count = len(self._current_warnings)
        total_suggestions_count = len(self._current_suggestions) if isinstance(self._current_suggestions, dict) else 0

        def safe_truncate_utf8(text: str, max_b: int) -> str:
            if max_b <= 0:
                return ""
            b = text.encode('utf-8')
            if len(b) <= max_b:
                return text
            return b[:max_b].decode('utf-8', errors='ignore')

        def format_footer(trunc_warn: int, trunc_sug: int, has_truncated_line: bool = False) -> str:
            parts = []
            if trunc_warn > 0:
                parts.append(f"{trunc_warn} cảnh báo")
            if trunc_sug > 0:
                parts.append(f"{trunc_sug} gợi ý")
            if parts:
                return f"... (Đã rút gọn thêm {' và '.join(parts)} do vượt giới hạn 16 KiB)"
            if has_truncated_line:
                return "... (Một số nội dung cảnh báo dài đã được cắt bớt do vượt giới hạn 16 KiB)"
            return ""

        lines = [
            f"TỔNG SỐ CẢNH BÁO: {total_warnings_count}",
            "-" * 50
        ]
        current_bytes = sum(len(l.encode('utf-8')) for l in lines) + (len(lines) - 1)

        footer_reserve = 256
        warn_shown = 0
        warn_truncated = 0
        has_truncated_line = False

        for idx, w in enumerate(self._current_warnings, 1):
            if warn_shown >= max_warnings:
                warn_truncated = total_warnings_count - warn_shown
                break

            entry = f"{idx}. {w}"
            will_truncate = (total_warnings_count > max_warnings) or (total_suggestions_count > max_suggestions) or (warn_truncated > 0) or has_truncated_line
            budget = max_bytes - (footer_reserve if will_truncate else 0)
            cost = len(entry.encode('utf-8')) + 1

            if current_bytes + cost > budget:
                available = budget - current_bytes - 1
                suffix = "... [cắt gọn]"
                suffix_len = len(suffix.encode('utf-8'))
                if available > suffix_len + 10:
                    entry = safe_truncate_utf8(entry, available - suffix_len) + suffix
                    lines.append(entry)
                    current_bytes += len(entry.encode('utf-8')) + 1
                    warn_shown += 1
                    has_truncated_line = True
                warn_truncated = total_warnings_count - warn_shown
                break

            lines.append(entry)
            current_bytes += cost
            warn_shown += 1

        if warn_shown == min(total_warnings_count, max_warnings) and warn_truncated == 0:
            warn_truncated = total_warnings_count - warn_shown

        sug_shown = 0
        sug_truncated = 0
        if total_suggestions_count > 0:
            sug_headers = [
                "=" * 50,
                "GỢI Ý TỪ MANIFEST CHƯA ĐĂNG KÝ (HINT CHƯA DUYỆT - KHÔNG TỰ TẠO TAXONOMY):",
                "=" * 50
            ]
            will_truncate = (warn_truncated > 0) or (total_suggestions_count > max_suggestions) or has_truncated_line
            budget = max_bytes - (footer_reserve if will_truncate else 0)
            hdr_cost = sum(len(h.encode('utf-8')) + 1 for h in sug_headers)

            if current_bytes + hdr_cost > budget:
                sug_truncated = total_suggestions_count
            else:
                for h in sug_headers:
                    lines.append(h)
                    current_bytes += len(h.encode('utf-8')) + 1

                for path, sug in itertools.islice(self._current_suggestions.items(), max_suggestions):
                    if isinstance(sug, dict):
                        parts = []
                        for k, v in itertools.islice(sug.items(), 5):
                            if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                                parts.append(f"{k}: {v}")
                        val_str = ", ".join(parts) if parts else "metadata không chuẩn"
                    elif isinstance(sug, str):
                        val_str = sug[:100]
                    else:
                        val_str = "giá trị không chuẩn"

                    entry = f"• {str(path)[:150]} -> {val_str}"
                    cost = len(entry.encode('utf-8')) + 1
                    will_truncate = (warn_truncated > 0) or (total_suggestions_count > max_suggestions) or (sug_truncated > 0) or has_truncated_line
                    budget = max_bytes - (footer_reserve if will_truncate else 0)

                    if current_bytes + cost > budget:
                        sug_truncated = total_suggestions_count - sug_shown
                        break
                    lines.append(entry)
                    current_bytes += cost
                    sug_shown += 1

                if sug_shown == min(total_suggestions_count, max_suggestions) and sug_truncated == 0:
                    sug_truncated = total_suggestions_count - sug_shown

        footer = format_footer(warn_truncated, sug_truncated, has_truncated_line)
        if footer:
            lines.append(footer)

        detail_text = "\n".join(lines)
        if len(detail_text.encode('utf-8')) > max_bytes:
            detail_text = safe_truncate_utf8(detail_text, max_bytes)

        box = QMessageBox(self)
        box.setWindowTitle("Chi tiết cảnh báo kiểm tra gói")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"Có {len(self._current_warnings)} cảnh báo khi kiểm tra metadata của gói.")
        box.setDetailedText(detail_text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        return box.exec()

    def _prepare(self):
        if self._terminal or self._closing or self.worker is not None or self.job is not None:
            return
        self._reset_warnings()
        self._draft_taxonomy.clear()
        self._reload_categories()
        if self._prepare_factory is not None:
            self._run(lambda cancel, progress: self._prepare_factory(cancel, progress), 'prepare')
            return
        value = self.input_source.text().strip()
        pid = self._pack_id
        pname = self._pack_name or (self.name.text().strip() or 'Pack')
        if value.startswith(('https://', 'http://')):
            self._run(lambda cancel, progress: self.service.download(value, cancel, progress, pack_id=pid, pack_name=pname), 'prepare')
        else:
            self._run(lambda cancel, progress: self.service.prepare(value, cancel, progress, pack_id=pid, pack_name=pname), 'prepare')

    def _commit(self):
        if self._terminal or self._closing or not self.job or self.worker is not None:
            return
        self.table.setFocus()  # commit editor before copying metadata
        self.job.update(name=self.name.text().strip() or 'Pack', version=self.version.text().strip() or '1.0')
        if self._pack_id:
            self.job['pack_id'] = self._pack_id
        job = copy.deepcopy(self.job)
        job['draft_taxonomy'] = dict(self._draft_taxonomy)
        self._run(lambda cancel, progress: self.service.commit(job, cancel, progress), 'commit')

    def _finished(self):
        worker = self.worker; self.worker = None
        if worker is not None:
            worker.deleteLater()
        ok, result = self._outcome or (False,'Tác vụ kết thúc không có kết quả.')
        if self._terminal or self._closing:
            if self._kind == 'commit' and ok:
                self._reset_warnings()
                self._draft_taxonomy.clear()
                self._pack_committed = True
                self.pack_committed.emit(result if isinstance(result, dict) else {'result': result})
                self.job = None
                self.library_changed.emit()
                super().accept()
                return
            elif self._kind == 'prepare' and ok and result:
                if self.storage.online:
                    self._run(lambda cancel, progress: self.service.discard(result), 'discard')
                    return
            elif self._kind == 'discard':
                self._reset_warnings()
                self.job = None
                super().reject()
                return
            self._reset_warnings()
            self.job = None
            super().reject()
            return

        if ok and self._kind == 'prepare':
            self.job = result
            if self._pack_id:
                self.job['pack_id'] = self._pack_id
            if self._pack_name:
                self.job['name'] = self._pack_name
            self.model.beginResetModel()
            self.model.assets = result['assets']
            self.model.endResetModel()
            self.name.setText(self.job['name'])
            self.version.setText(result['version'])
            self.lbl_status.setText(f'Kiểm duyệt {len(result["assets"])} file trước khi nhập kho.')
            self._update_warnings_presentation(result)
        elif self._kind == 'discard':
            self._reset_warnings()
            self.job = None; super().reject(); return
        elif ok and self._kind == 'commit':
            self._reset_warnings()
            self._draft_taxonomy.clear()
            self._pack_committed = True
            self.pack_committed.emit(result if isinstance(result, dict) else {'result': result})
            self.job = None; self.library_changed.emit(); super().accept(); return
        else:
            self._reset_warnings()
            self.lbl_status.setText(str(result))
        self._update_buttons()

    def accept(self):
        self._draft_taxonomy.clear()
        self._terminal = True
        if hasattr(self, '_prepare_timer') and self._prepare_timer.isActive():
            self._prepare_timer.stop()
        super().accept()

    def reject(self):
        self._draft_taxonomy.clear()
        self._terminal = True
        if hasattr(self, '_prepare_timer') and self._prepare_timer.isActive():
            self._prepare_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self._closing = True; self.worker.requestInterruption(); self.lbl_status.setText('Đang hủy an toàn…'); return
        if self.job and self.storage.online:
            self._closing = True
            job = self.job
            self.job = None
            self._run(lambda cancel, progress: self.service.discard(job), 'discard')
            return
        self.job = None
        super().reject()

    def closeEvent(self, event):
        self._terminal = True
        if hasattr(self, '_prepare_timer') and self._prepare_timer.isActive():
            self._prepare_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            self.reject()
        else:
            self.reject()
            if self.worker is not None and self.worker.isRunning():
                event.ignore()
            else:
                event.accept()


class StorageSettingsDialog(QDialog):
    library_changed = Signal()

    def __init__(self, storage=None, parent=None):
        super().__init__(parent)
        self.storage = storage or StorageManager()
        self.worker = None
        self._closing = False
        self.setWindowTitle('ChunDVC · Kho Chính & Liên kết')
        self.resize(700, 360)
        self.setStyleSheet(STYLE)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)
        
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("""
            background: #111214;
            border: 1px solid #2e3138;
            border-radius: 6px;
            padding: 10px;
            font-size: 12px;
            color: #38bdf8;
            font-weight: 500;
        """)
        layout.addWidget(self.status)
        
        for text, slot in [
            ('📁 Chọn / Tạo Thư mục Kho Chính', self._choose),
            ('🚚 Di chuyển kho sang ổ khác (xác minh bản sao)', self._migrate),
            ('📦 Nhập kho legacy từ thư mục cũ sang ổ khác', self._migrate_legacy),
            ('🔗 Liên kết thư mục có sẵn (không copy)', self._link)
        ]:
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(34)
            button.clicked.connect(slot)
            layout.addWidget(button)
            
        self.cleanup_old = QCheckBox('Dọn media kho cũ sau xác minh (chỉ kho do ChunDVC quản lý)')
        layout.addWidget(self.cleanup_old)
        
        lbl_hint = QLabel('Di chuyển chỉ đổi kho sau khi xác minh. Nguồn cũ được giữ nếu chưa chọn dọn media.\nKhông tự di chuyển model AI, Whisper hay project Resolve.')
        lbl_hint.setStyleSheet("color: #71717a; font-size: 11px;")
        layout.addWidget(lbl_hint)
        
        # Thanh nút xác nhận & đóng ở dưới cùng
        btn_box = QHBoxLayout()
        btn_box.setContentsMargins(0, 8, 0, 0)
        btn_box.addStretch(1)
        
        self.btn_confirm = QPushButton("✔ Xác Nhận & Đóng")
        self.btn_confirm.setCursor(Qt.PointingHandCursor)
        self.btn_confirm.setFixedHeight(36)
        self.btn_confirm.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff4757, stop:1 #e0283b);
                color: #ffffff;
                font-weight: 700;
                font-size: 13px;
                border: 1px solid #ff6b81;
                border-radius: 6px;
                padding: 0 28px;
            }
            QPushButton:hover {
                background: #ff5252;
                border-color: #ff7979;
            }
            QPushButton:pressed {
                background: #d63031;
            }
        """)
        self.btn_confirm.clicked.connect(self._confirm_and_close)
        btn_box.addWidget(self.btn_confirm)
        layout.addLayout(btn_box)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._status)
        self.timer.start(2000)
        self._status()

    def _status(self):
        if self.worker: return
        root = self.storage._config.get('root', 'Chưa chọn kho')
        status_text = '🟢 Trực tuyến (Online)' if self.storage.online else '🔴 Ngoại tuyến (Offline — chỉ duyệt metadata)'
        self.status.setText(f"{status_text}\n📂 Đường dẫn kho: {root}")

    def _choose(self):
        if self.worker: return
        path = QFileDialog.getExistingDirectory(self, 'Chọn thư mục kho lưu trữ chính')
        if not path: return
        try:
            self.storage.configure(path)
            self._status()
            self.library_changed.emit()
            QMessageBox.information(
                self,
                'Đã Kết Nối Kho Lưu Trữ',
                f"Đã chọn và thiết lập kho lưu trữ chính thành công:\n{path}\n\nNhấn 'Xác Nhận & Đóng' để áp dụng thay đổi."
            )
        except Exception as exc:
            QMessageBox.warning(self, 'Không đổi được kho', str(exc))

    def _confirm_and_close(self):
        if self.worker:
            self._closing = True
            self.worker.requestInterruption()
            return
        self.library_changed.emit()
        self.accept()

    def accept(self):
        self.library_changed.emit()
        super().accept()

    def _run(self, operation):
        if self.worker:return
        self.worker=LibraryJob(operation,self); self.worker.progress.connect(self.status.setText)
        self.worker.error.connect(self.status.setText); self.worker.result.connect(lambda _:self.library_changed.emit())
        self.worker.finished.connect(self._done); self.worker.start()

    def _done(self):
        worker=self.worker; self.worker=None; worker.deleteLater()
        if self._closing: self.reject()

    def _migrate(self):
        if self.worker:return
        path=QFileDialog.getExistingDirectory(self,'Chọn thư mục đích TRỐNG')
        if path:
            cleanup = self.cleanup_old.isChecked()
            self._run(lambda cancel, progress:self.storage.migrate(path,cancel=cancel,progress=progress,delete_source=cleanup))

    def _migrate_legacy(self):
        if self.worker:return
        source=QFileDialog.getExistingDirectory(self,'Chọn thư mục dữ liệu cũ (nguồn được giữ nguyên)')
        if not source:return
        path=QFileDialog.getExistingDirectory(self,'Chọn thư mục đích TRỐNG')
        if path:self._run(lambda cancel, progress:self.storage.migrate(path,legacy_source=source,cancel=cancel,progress=progress))

    def _link(self):
        if self.worker:return
        path=QFileDialog.getExistingDirectory(self,'Liên kết kho có sẵn — không sao chép')
        if not path:return
        cats=self.storage.taxonomy(); keys=list(cats)
        label,ok=QInputDialog.getItem(self,'Danh mục','Loại tài nguyên:',list(cats.values()),0,False)
        if ok:self._run(lambda cancel, progress:self.storage.link_folder(path,keys[list(cats.values()).index(label)],cancel))

    def reject(self):
        if self.worker:self._closing=True; self.worker.requestInterruption(); return
        self.library_changed.emit()
        super().reject()

    def closeEvent(self,event):
        if self.worker:event.ignore(); self.reject()
        else:
            self.library_changed.emit()
            event.accept()
