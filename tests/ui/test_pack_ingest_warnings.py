import json
import time
import zipfile
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.storage_manager import StorageManager
from src.ui.widgets.pack_ingest_wizard import PackIngestWizard


def wait_for_wizard(wizard, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QTest.qWait(10)
        if hasattr(wizard, '_prepare_timer') and wizard._prepare_timer.isActive():
            continue
        if wizard.worker is not None:
            continue
        return
    outcome = getattr(wizard, '_outcome', None)
    lbl = wizard.lbl_status.text() if hasattr(wizard, 'lbl_status') else ''
    raise TimeoutError(f"Wizard wait timeout sau {timeout_s}s. worker={wizard.worker}, job={wizard.job is not None}, outcome={outcome}, status='{lbl}'")


def test_prepare_unknown_category_shows_warning_banner_and_preserves_guess(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('clip.mp4', b'video')
        z.writestr('manifest.json', json.dumps({
            'name': 'Alien Pack',
            'assets': [{'relative_path': 'clip.mp4', 'category': 'unknown_alien_category'}]
        }))

    wizard = PackIngestWizard(sm, source=str(archive))
    wizard.show()
    wizard._prepare()
    wait_for_wizard(wizard)

    # 1. Warning banner must be visible
    assert hasattr(wizard, 'warning_widget')
    assert wizard.warning_widget.isVisible()
    summary_text = wizard.lbl_warning_summary.text()
    assert '1 cảnh báo' in summary_text
    assert 'danh mục chưa đăng ký' in summary_text

    # 2. Model category must remain guess ('broll')
    assert wizard.model.rowCount() == 1
    assert wizard.model.assets[0]['category'] == 'broll'

    # 3. Storage taxonomy on disk is clean (no write)
    assert 'unknown_alien_category' not in sm.taxonomy()

    # 4. Details dialog inspection (monkeypatch exec to capture content)
    captured = {}
    def mock_exec(box_self):
        captured['title'] = box_self.windowTitle()
        captured['text'] = box_self.text()
        captured['detailed'] = box_self.detailedText()
        captured['text_format'] = box_self.textFormat()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, 'exec', mock_exec)
    wizard.btn_view_warnings.click()

    assert '1' in captured['text']
    assert 'unknown_alien_category' in captured['detailed']
    assert 'GỢI Ý TỪ MANIFEST' in captured['detailed']
    # Plain text format enforced
    assert captured['text_format'] == Qt.TextFormat.PlainText

    wizard.reject()
    wait_for_wizard(wizard)
    wizard.close()


def test_invalid_schema_shows_warning_and_clean_job_hides_warning(qapp, tmp_path):
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    # Job 1: Invalid schema_version: 99 -> Warning shown
    bad_archive = tmp_path / 'bad_pack.zip'
    with zipfile.ZipFile(bad_archive, 'w') as z:
        z.writestr('clip.mp4', b'video')
        z.writestr('manifest.json', json.dumps({
            'schema_version': 99,
            'assets': [{'relative_path': 'clip.mp4', 'category': 'broll'}]
        }))

    wizard = PackIngestWizard(sm, source=str(bad_archive))
    wizard.show()
    wizard._prepare()
    wait_for_wizard(wizard)

    assert wizard.warning_widget.isVisible()
    assert '1 cảnh báo' in wizard.lbl_warning_summary.text()
    assert wizard.model.assets[0]['category'] == 'broll'

    # Job 2: Clean job with valid metadata -> Warning banner must be hidden!
    good_archive = tmp_path / 'good_pack.zip'
    with zipfile.ZipFile(good_archive, 'w') as z:
        z.writestr('clean.mp4', b'video')
        z.writestr('manifest.json', json.dumps({
            'schema_version': 1,
            'assets': [{'relative_path': 'clean.mp4', 'category': 'broll'}]
        }))

    wizard.reject()
    wait_for_wizard(wizard)

    wizard.input_source.setText(str(good_archive))
    wizard._prepare()
    wait_for_wizard(wizard)

    assert not wizard.warning_widget.isVisible()
    assert wizard.lbl_warning_summary.text() == ""
    wizard.reject()
    wait_for_wizard(wizard)
    wizard.close()


def test_bounded_warnings_rendering_and_plain_text_escape(qapp, tmp_path, monkeypatch):
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    wizard = PackIngestWizard(sm)
    wizard.show()

    # Tạo một kết quả prepare giả lập có hơn 150 cảnh báo và chứa thẻ HTML độc hại
    fake_warnings = [f"Cảnh báo <b>thẻ html {i}</b> & script <script>alert(1)</script>" for i in range(150)]
    fake_suggestions = {f"file_{i}.mp4": {"category": f"bad_cat_{i}"} for i in range(150)}
    fake_result = {
        'job_dir': str(tmp_path / 'fake_staging'),
        'pack_id': 'fake123',
        'name': 'HTML Pack',
        'version': '1.0',
        'assets': [{'relative_path': 'a.mp4', 'category': 'broll', 'size_bytes': 10}],
        'warnings': fake_warnings,
        'manifest_suggestions': fake_suggestions
    }

    # Giả lập worker hoàn tất trả kết quả
    wizard._outcome = (True, fake_result)
    wizard._kind = 'prepare'
    wizard._finished()

    assert wizard.warning_widget.isVisible()
    assert '150 cảnh báo' in wizard.lbl_warning_summary.text()
    assert wizard.lbl_warning_summary.textFormat() == Qt.TextFormat.PlainText

    captured = {}
    def mock_exec(box_self):
        captured['text'] = box_self.text()
        captured['detailed'] = box_self.detailedText()
        captured['text_format'] = box_self.textFormat()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, 'exec', mock_exec)
    wizard.btn_view_warnings.click()

    # Bounded details: tối đa 100 entries, có thông báo rút gọn
    detailed = captured['detailed']
    detailed_bytes = len(detailed.encode('utf-8'))
    assert detailed_bytes <= 16384, f"detailedText UTF-8 vượt budget: {detailed_bytes} > 16384"
    assert '(Đã rút gọn' in detailed
    assert 'cảnh báo' in detailed
    assert 'gợi ý' in detailed
    assert '<script>' in detailed
    assert captured['text_format'] == Qt.TextFormat.PlainText
    # Không tạo hàng trăm widget con
    assert len(wizard.warning_widget.findChildren(QMessageBox)) <= 1

    wizard.reject()
    wait_for_wizard(wizard)
    wizard.close()


def test_global_details_utf8_budget_and_truncation_limits(qapp, tmp_path, monkeypatch):
    """
    Kiểm tra nghiêm ngặt ngân sách toàn cục UTF-8 budget <= 16384 bytes cho detailedText:
    - Tiếng Việt có dấu + emoji đa byte
    - Cảnh báo đơn lẻ siêu dài (> 20 KiB)
    - Đúng 100 entries (vừa khít trần, không rút gọn)
    - 101 entries (vượt trần đúng 1 entry, thông báo rút gọn 1 cảnh báo)
    """
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')
    wizard = PackIngestWizard(sm)
    wizard.show()

    captured = {}
    def mock_exec(box_self):
        captured['text'] = box_self.text()
        captured['detailed'] = box_self.detailedText()
        captured['text_format'] = box_self.textFormat()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, 'exec', mock_exec)

    # 1. Tiếng Việt + emoji + many suggestions
    vn_warnings = [f"Cảnh báo tiếng Việt có dấu {i} 🇻🇳🔥🎥 — kiểm tra bộ đệm UTF-8" for i in range(120)]
    vn_suggestions = {f"đường_dẫn_{i}.mp4": {"category": f"danh_mục_{i}"} for i in range(60)}
    res1 = {
        'job_dir': str(tmp_path / 'staging1'),
        'pack_id': 'p1', 'name': 'P1', 'version': '1.0',
        'assets': [{'relative_path': 'a.mp4', 'category': 'broll', 'size_bytes': 1}],
        'warnings': vn_warnings,
        'manifest_suggestions': vn_suggestions,
    }
    wizard._outcome = (True, res1)
    wizard._kind = 'prepare'
    wizard._finished()
    wizard.btn_view_warnings.click()
    detailed1 = captured['detailed']
    d1_bytes = len(detailed1.encode('utf-8'))
    assert d1_bytes <= 16384, f"UTF-8 bytes vượt budget: {d1_bytes}"
    assert 'cảnh báo' in detailed1
    assert 'gợi ý' in detailed1
    assert '🇻🇳' in detailed1

    # 2. Cảnh báo đơn lẻ siêu dài (> 20 KiB)
    long_warning = "Cảnh báo dài đặc biệt " * 1500 + "🚨💥"
    res2 = {
        'job_dir': str(tmp_path / 'staging2'),
        'pack_id': 'p2', 'name': 'P2', 'version': '1.0',
        'assets': [{'relative_path': 'a.mp4', 'category': 'broll', 'size_bytes': 1}],
        'warnings': [long_warning],
        'manifest_suggestions': {},
    }
    wizard._outcome = (True, res2)
    wizard._kind = 'prepare'
    wizard._finished()
    wizard.btn_view_warnings.click()
    detailed2 = captured['detailed']
    d2_bytes = len(detailed2.encode('utf-8'))
    assert d2_bytes <= 16384, f"Single long warning vượt budget: {d2_bytes}"
    assert '... [cắt gọn]' in detailed2

    # 3. Đúng 100 entries (vừa đủ trần 100, không có thông báo rút gọn)
    res3 = {
        'job_dir': str(tmp_path / 'staging3'),
        'pack_id': 'p3', 'name': 'P3', 'version': '1.0',
        'assets': [{'relative_path': 'a.mp4', 'category': 'broll', 'size_bytes': 1}],
        'warnings': [f"Cảnh báo ngắn số {i}" for i in range(100)],
        'manifest_suggestions': {},
    }
    wizard._outcome = (True, res3)
    wizard._kind = 'prepare'
    wizard._finished()
    wizard.btn_view_warnings.click()
    detailed3 = captured['detailed']
    d3_bytes = len(detailed3.encode('utf-8'))
    assert d3_bytes <= 16384
    assert 'TỔNG SỐ CẢNH BÁO: 100' in detailed3
    assert 'rút gọn' not in detailed3

    # 4. 101 entries (vượt trần đúng 1 entry)
    res4 = {
        'job_dir': str(tmp_path / 'staging4'),
        'pack_id': 'p4', 'name': 'P4', 'version': '1.0',
        'assets': [{'relative_path': 'a.mp4', 'category': 'broll', 'size_bytes': 1}],
        'warnings': [f"Cảnh báo ngắn số {i}" for i in range(101)],
        'manifest_suggestions': {},
    }
    wizard._outcome = (True, res4)
    wizard._kind = 'prepare'
    wizard._finished()
    wizard.btn_view_warnings.click()
    detailed4 = captured['detailed']
    d4_bytes = len(detailed4.encode('utf-8'))
    assert d4_bytes <= 16384
    assert 'Đã rút gọn thêm 1 cảnh báo' in detailed4

    wizard.reject()
    wait_for_wizard(wizard)
    wizard.close()


def test_factory_prepare_path_shows_warnings_without_autocommit(qapp, tmp_path):
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    # Factory sinh job có warning
    def factory_op(cancel, progress):
        staging = sm.new_staging()
        payload = staging / 'payload'
        payload.mkdir()
        (payload / 'item.mp4').write_bytes(b'video')
        (payload / 'manifest.json').write_text(json.dumps({
            'assets': [{'relative_path': 'item.mp4', 'category': 'custom_unregistered'}]
        }), encoding='utf-8')
        from src.core.pack_ingest import PackIngestService
        return PackIngestService(sm).inspect_job(staging)

    wizard = PackIngestWizard(sm, prepare_factory=factory_op, auto_prepare=True)
    wizard.show()
    wait_for_wizard(wizard)

    # Wizard không được tự commit, phải dừng ở review state
    assert not wizard.is_pack_committed()
    assert wizard.btn_commit.isEnabled()
    assert wizard.warning_widget.isVisible()
    assert '1 cảnh báo' in wizard.lbl_warning_summary.text()

    wizard.reject()
    wait_for_wizard(wizard)
    wizard.close()


def test_commit_and_cancel_do_not_leak_warnings_into_registry_or_taxonomy(qapp, tmp_path):
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('sound.wav', b'audio')
        z.writestr('manifest.json', json.dumps({
            'name': 'Gói Âm Thanh',
            'assets': [{'relative_path': 'sound.wav', 'category': 'custom_non_existent'}]
        }))

    committed_payloads = []
    wizard = PackIngestWizard(sm, source=str(archive))
    wizard.show()
    wizard.pack_committed.connect(committed_payloads.append)
    wizard._prepare()
    wait_for_wizard(wizard)

    assert wizard.warning_widget.isVisible()

    # Bấm commit
    wizard.btn_commit.click()
    wait_for_wizard(wizard)

    assert wizard.is_pack_committed()
    assert len(committed_payloads) == 1

    # Kiểm tra registry trên đĩa: không có warnings, suggestions hay _*
    reg = sm.registry()
    asset = reg['assets'][0]
    assert asset['name'] == 'sound'
    assert asset['category'] == 'sfx'
    assert 'warnings' not in asset
    assert 'manifest_suggestions' not in asset
    assert '_warning' not in asset
    assert '_role' not in asset

    # Taxonomy trên đĩa không bị thay đổi
    assert 'custom_non_existent' not in sm.taxonomy()
    wizard.close()
