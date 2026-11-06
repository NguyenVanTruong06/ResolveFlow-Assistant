from src.core.storage_manager import StorageManager
from src.ui.widgets.pack_ingest_wizard import IngestReviewModel, PackIngestWizard
from PySide6.QtCore import Qt


def test_review_table_edits_metadata_and_excludes(qapp):
    record = {'relative_path':'city/a.mp4','category':'broll','subcategory':'city','tags':[], 'excluded':False,'size_bytes':5}
    model = IngestReviewModel([record])
    assert model.setData(model.index(0, 2), 'memes')
    assert model.setData(model.index(0, 4), 'Review Công Nghệ, Chill Vlog')
    assert record['category'] == 'memes' and len(record['tags']) == 2
    assert model.setData(model.index(0, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert record['excluded']


def test_wizard_offline_never_starts_job(qapp, tmp_path):
    storage = StorageManager(tmp_path/'config', reserve_bytes=0)
    widget = PackIngestWizard(storage)
    assert not widget.btn_prepare.isEnabled()
    assert not widget.btn_commit.isEnabled()
    assert 'Offline' in widget.lbl_status.text()
    widget.close()


def test_wizard_cancel_waits_for_running_job(qapp,tmp_path):
    from PySide6.QtTest import QTest
    import time
    sm=StorageManager(tmp_path/'cfg',reserve_bytes=0); sm.configure(tmp_path/'root')
    wizard=PackIngestWizard(sm)
    def operation(cancel,progress):
        while not cancel(): time.sleep(.005)
        raise InterruptedError('Đã hủy')
    wizard._run(operation,'prepare'); wizard.reject()
    assert wizard.worker is not None
    for _ in range(100):
        QTest.qWait(10)
        if wizard.worker is None:break
    assert wizard.worker is None


def test_committed_pack_reaches_studio_and_indexer(qapp,tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'appdata'))
    sm=StorageManager(reserve_bytes=0); sm.configure(tmp_path/'library')
    from src.core.pack_ingest import PackIngestService
    src=tmp_path/'city.mp4'; src.write_bytes(b'video')
    svc=PackIngestService(sm); svc.commit(svc.prepare(src))
    from src.ui.studio.studio_window import StudioWindow
    from src.core.asset_indexer import AssetIndexer
    win=StudioWindow(); tab=win.tab_assets
    managed=[a for a in tab.all_assets if getattr(a,'storage_managed',False)]
    assert len(managed)==1 and managed[0].tab=='broll'
    assert 'broll' in tab.rail_btns
    assert AssetIndexer().scan_managed_assets()[0]['id']==managed[0].id
    win.close()


def test_managed_subcategory_tags_reach_proxy(qapp,tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'appdata'))
    sm=StorageManager(reserve_bytes=0); sm.configure(tmp_path/'library')
    from src.core.pack_ingest import PackIngestService
    src=tmp_path/'whoosh.wav'; src.write_bytes(b'audio')
    svc=PackIngestService(sm); job=svc.prepare(src)
    job['assets'][0].update(subcategory='ambient', tags=['Review Công Nghệ'])
    svc.commit(job)
    from src.ui.studio.studio_window import StudioWindow
    win=StudioWindow(); tab=win.tab_assets
    tab._filter_by_rail('sfx'); tab._on_sub_category_clicked('ambient')
    assert tab.asset_filter.rowCount()==1
    tab._on_sub_category_clicked('Review Công Nghệ')
    assert tab.asset_filter.rowCount()==1
    win.close()


def test_studio_import_routes_to_wizard_and_guards_legacy_copy2(qapp, tmp_path, monkeypatch):
    import shutil
    from PySide6.QtWidgets import QDialog
    from PySide6.QtTest import QTest
    from src.ui.studio.studio_window import StudioWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    # Monkeypatch legacy copy2 to raise if it targets assets/ or DaVinci dirs
    real_copy2 = shutil.copy2
    def guarded_copy2(src, dst, *args, **kwargs):
        dst_s = str(dst)
        if "assets/" in dst_s or "assets\\templates" in dst_s or "Templates/Titles" in dst_s:
            raise AssertionError(f"Legacy copy2 into assets called: {dst_s}")
        return real_copy2(src, dst, *args, **kwargs)
    monkeypatch.setattr("shutil.copy2", guarded_copy2)

    # Real prepare and commit via wizard execution
    def auto_commit_exec(wizard_self):
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None:
                break
            if wizard_self.worker is None and wizard_self.job is None:
                wizard_self._prepare()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None and wizard_self.worker is None:
                break
        wizard_self._commit()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.worker is None:
                break
        QTest.qWait(50)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PackIngestWizard, "exec", auto_commit_exec)

    win = StudioWindow()
    tab = win.tab_assets
    sample_mp4 = tmp_path / 'imported_sample.mp4'
    sample_mp4.write_bytes(b'fake_mp4_bytes')

    tab._import_files_or_folders([str(sample_mp4)])

    # Verify asset is now managed in storage registry and present in Studio
    registry = sm.registry()
    assert len(registry['assets']) >= 1
    assert any(a['name'] == 'imported_sample' for a in registry['assets'])
    managed = [a for a in tab.all_assets if getattr(a, 'storage_managed', False)]
    assert any(a.name == 'imported_sample' for a in managed)
    win.close()


def test_studio_import_multi_select_same_basename_and_cancel_queue(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from PySide6.QtTest import QTest
    from src.ui.studio.studio_window import StudioWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    folder_a = tmp_path / 'folder_a'
    folder_b = tmp_path / 'folder_b'
    folder_a.mkdir()
    folder_b.mkdir()

    file_a = folder_a / 'sound.wav'
    file_b = folder_b / 'sound.wav'
    file_a.write_bytes(b'sound_a')
    file_b.write_bytes(b'sound_b')

    # Part 1: Multi-select both files with same basename -> both must commit without overwrite
    def auto_commit_exec(wizard_self):
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None:
                break
            if wizard_self.worker is None and wizard_self.job is None:
                wizard_self._prepare()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None and wizard_self.worker is None:
                break
        wizard_self._commit()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.worker is None:
                break
        QTest.qWait(50)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PackIngestWizard, "exec", auto_commit_exec)

    win = StudioWindow()
    tab = win.tab_assets
    tab._import_files_or_folders([str(file_a), str(file_b)])

    reg = sm.registry()
    assert len(reg['assets']) == 2
    # Verify both assets exist with distinct IDs
    ids = [a['id'] for a in reg['assets']]
    assert len(set(ids)) == 2
    win.close()

    # Part 2: Test cancellation stops subsequent queue ("hủy không mở nguồn sau")
    opened_sources = []
    def cancel_first_exec(wizard_self):
        src = wizard_self.input_source.text()
        opened_sources.append(src)
        wizard_self.reject()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(PackIngestWizard, "exec", cancel_first_exec)

    win2 = StudioWindow()
    tab2 = win2.tab_assets
    file_c = folder_a / 'another1.wav'
    file_d = folder_b / 'another2.wav'
    file_c.write_bytes(b'c')
    file_d.write_bytes(b'd')

    tab2._import_files_or_folders([str(file_c), str(file_d)])
    # Only the first source was opened because cancel stopped the queue!
    assert len(opened_sources) == 1
    assert opened_sources[0] == str(file_c)
    win2.close()


def test_dock_drop_routes_to_wizard_and_guards_legacy_pack_manager(qapp, tmp_path, monkeypatch):
    import zipfile
    from PySide6.QtWidgets import QDialog
    from PySide6.QtTest import QTest
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    # Guard: PackManager.install_pack must NOT be called
    def fail_legacy_install(*args, **kwargs):
        raise AssertionError("PackManager.install_pack was called!")
    monkeypatch.setattr("src.core.pack_manager.PackManager.install_pack", fail_legacy_install)

    # Real prepare and commit via wizard execution
    def auto_commit_exec(wizard_self):
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None:
                break
            if wizard_self.worker is None and wizard_self.job is None:
                wizard_self._prepare()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.job is not None and wizard_self.worker is None:
                break
        wizard_self._commit()
        for _ in range(300):
            QTest.qWait(10)
            if wizard_self.worker is None:
                break
        QTest.qWait(50)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PackIngestWizard, "exec", auto_commit_exec)

    # Create a small valid test zip
    zip_path = tmp_path / 'test_pack.zip'
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('sfx/laser.wav', b'RIFFlaser')

    dock = EdgeDockWindow()
    assert dock.fsm._is_dragging is False

    # Simulate handle dropped packs
    dock._handle_dropped_packs([str(zip_path)])

    # Verify committed into storage and dock assets refreshed
    reg = sm.registry()
    assert len(reg['packs']) == 1
    assert any(p['name'] == 'test_pack' for p in reg['packs'])
    assert dock.fsm._is_dragging is False
    assert any(getattr(a, 'name', '') == 'laser' or (isinstance(a, dict) and a.get('name') == 'laser') for a in dock._all_assets)
    dock.close()


def test_dock_drop_offline_and_cancel_policy(qapp, tmp_path, monkeypatch):
    import zipfile
    from PySide6.QtWidgets import QDialog
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata_offline'))
    # Storage unconfigured
    sm = StorageManager(reserve_bytes=0)
    assert not sm.online

    zip_a = tmp_path / 'pack_a.zip'
    zip_b = tmp_path / 'pack_b.zip'
    with zipfile.ZipFile(zip_a, 'w') as zf:
        zf.writestr('sfx/a.wav', b'a')
    with zipfile.ZipFile(zip_b, 'w') as zf:
        zf.writestr('sfx/b.wav', b'b')

    opened_packs = []
    def cancel_offline_exec(wizard_self):
        opened_packs.append(wizard_self.input_source.text())
        assert not wizard_self.storage.online
        assert not wizard_self.btn_prepare.isEnabled()
        wizard_self.reject()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(PackIngestWizard, "exec", cancel_offline_exec)

    dock = EdgeDockWindow()
    dock._handle_dropped_packs([str(zip_a), str(zip_b)])

    # Only first pack was opened before cancel broke the loop
    assert len(opened_packs) == 1
    assert opened_packs[0] == str(zip_a)
    # No registry or assets created
    assert not sm.online
    assert not (tmp_path / 'appdata_offline' / 'library').exists()
    dock.close()


def test_auto_prepare_lifecycle_cancelled_before_queued_callback(qapp, tmp_path):
    from PySide6.QtWidgets import QDialog
    sm = StorageManager(tmp_path / 'cfg', reserve_bytes=0)
    sm.configure(tmp_path / 'root')
    test_file = tmp_path / 'clip.mp4'
    test_file.write_bytes(b'video-data')

    calls = []
    real_prepare = sm
    orig_service_prepare = PackIngestWizard._prepare

    # Probe 1: immediate reject before event loop
    w = PackIngestWizard(storage=sm, source=str(test_file), auto_prepare=True)
    prepare_invocations = []
    real_prepare_call = w.service.prepare
    def tracked_prepare(src, cancel=lambda: False, progress=lambda t: None):
        prepare_invocations.append(src)
        return real_prepare_call(src, cancel, progress)
    w.service.prepare = tracked_prepare

    w.reject()
    qapp.processEvents()

    assert len(prepare_invocations) == 0
    assert w.job is None
    assert w.result() == QDialog.DialogCode.Rejected
    assert list((sm.meta / 'staging').glob('*')) == []
    w.close()

    # Probe 2: immediate accept before event loop
    w2 = PackIngestWizard(storage=sm, source=str(test_file), auto_prepare=True)
    prepare_invocations_2 = []
    def tracked_prepare_2(src, cancel=lambda: False, progress=lambda t: None):
        prepare_invocations_2.append(src)
        return real_prepare_call(src, cancel, progress)
    w2.service.prepare = tracked_prepare_2

    w2.accept()
    qapp.processEvents()

    assert len(prepare_invocations_2) == 0
    assert w2.job is None
    assert w2.result() == QDialog.DialogCode.Accepted
    w2.close()


def test_settings_then_cancel_does_not_count_as_committed_and_breaks_queue(qapp, tmp_path, monkeypatch):
    import zipfile
    from PySide6.QtWidgets import QDialog
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    zip_1 = tmp_path / 'pack_1.zip'
    zip_2 = tmp_path / 'pack_2.zip'
    with zipfile.ZipFile(zip_1, 'w') as zf:
        zf.writestr('sfx/hit.wav', b'hit-wav')
    with zipfile.ZipFile(zip_2, 'w') as zf:
        zf.writestr('sfx/laser.wav', b'laser-wav')

    # Simulation: user opens settings from wizard, modifies settings (emits library_changed), then cancels wizard
    opened_wizards = []
    def mock_exec(wizard_self):
        opened_wizards.append(wizard_self)
        # Emit library_changed as if settings dialog had saved
        wizard_self.library_changed.emit()
        assert wizard_self.is_pack_committed() is False
        wizard_self.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PackIngestWizard, "exec", mock_exec)

    dock = EdgeDockWindow()
    dock._handle_dropped_packs([str(zip_1), str(zip_2)])

    # Only first pack was processed; rejected immediately halted subsequent packs
    assert len(opened_wizards) == 1
    reg = sm.registry()
    assert len(reg['packs']) == 0
    assert len(reg['assets']) == 0
    dock.close()


def test_commit_pack1_then_cancel_pack2_reports_partial(qapp, tmp_path, monkeypatch):
    import time
    import traceback
    import zipfile
    from PySide6.QtWidgets import QApplication, QDialog
    from PySide6.QtTest import QTest
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata_partial'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    zip_1 = tmp_path / 'pack_1.zip'
    zip_2 = tmp_path / 'pack_2.zip'
    with zipfile.ZipFile(zip_1, 'w') as zf:
        zf.writestr('sfx/first.wav', b'first')
    with zipfile.ZipFile(zip_2, 'w') as zf:
        zf.writestr('sfx/second.wav', b'second')

    processed = []
    helper_errors = []

    def exec_partial(wizard_self):
        try:
            source = wizard_self.input_source.text()
            processed.append(source)
            if source == str(zip_1):
                # Dừng auto_prepare timer nếu đang chạy để tránh race condition
                if hasattr(wizard_self, '_prepare_timer') and wizard_self._prepare_timer.isActive():
                    wizard_self._prepare_timer.stop()

                # Đồng bộ gọi prepare và đợi job sẵn sàng
                wizard_self._prepare()
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    QApplication.processEvents()
                    QTest.qWait(20)
                    if wizard_self.job is not None:
                        break
                    if wizard_self.worker is None and wizard_self._outcome is not None and not wizard_self._outcome[0]:
                        break

                if wizard_self.job is None:
                    raise AssertionError(
                        f"Prepare không tạo được job trước deadline: "
                        f"outcome={getattr(wizard_self, '_outcome', None)}, "
                        f"status='{wizard_self.lbl_status.text()}', "
                        f"worker_running={wizard_self.worker.isRunning() if wizard_self.worker else False}"
                    )

                # Đồng bộ gọi commit và đợi pack committed thành công
                wizard_self._commit()
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    QApplication.processEvents()
                    QTest.qWait(20)
                    if wizard_self.is_pack_committed():
                        break
                    if wizard_self.worker is None and wizard_self._outcome is not None and not wizard_self._outcome[0]:
                        break

                if not wizard_self.is_pack_committed():
                    raise AssertionError(
                        f"Commit không hoàn thành trước deadline: "
                        f"outcome={getattr(wizard_self, '_outcome', None)}, "
                        f"status='{wizard_self.lbl_status.text()}', "
                        f"worker_running={wizard_self.worker.isRunning() if wizard_self.worker else False}"
                    )

                return QDialog.DialogCode.Accepted
            else:
                wizard_self.reject()
                return QDialog.DialogCode.Rejected
        except Exception as exc:
            helper_errors.append(traceback.format_exc())
            raise

    monkeypatch.setattr(PackIngestWizard, "exec", exec_partial)

    dock = EdgeDockWindow()
    dock._handle_dropped_packs([str(zip_1), str(zip_2)])

    assert not helper_errors, f"Lỗi xảy ra trong helper exec_partial:\n" + "\n".join(helper_errors)
    toast_text = dock._toast.lbl_msg.text() if (hasattr(dock, "_toast") and dock._toast) else "None"
    assert len(processed) == 2, (
        f"Kỳ vọng xử lý 2 gói (1 commit, 1 cancel), nhưng chỉ nhận được {len(processed)} gói: {processed}. "
        f"Toast hiện tại: '{toast_text}'"
    )
    reg = sm.registry()
    assert len(reg['packs']) == 1, f"Kỳ vọng registry có 1 pack, nhưng có: {len(reg['packs'])}"
    assert reg['packs'][0]['name'] == 'pack_1'
    assert hasattr(dock, "_toast") and dock._toast is not None
    assert "1/2" in dock._toast.lbl_msg.text()
    assert "dừng hàng đợi" in dock._toast.lbl_msg.text()
    assert "Lỗi" not in dock._toast.lbl_msg.text()
    assert dock._toast.lbl_icon.text() == "ℹ"
    dock.close()


def test_dock_search_refresh_updates_shared_index_and_proxy_row(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'appdata_dock_search'))
    sm = StorageManager(reserve_bytes=0)
    sm.configure(tmp_path / 'library')

    from src.services.search_index import SearchIndex
    from src.core.pack_ingest import PackIngestService
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    shared_idx = SearchIndex([])
    dock = EdgeDockWindow()
    dock.set_search_index(shared_idx)

    assert hasattr(dock, "search_index")
    assert dock.search_index is shared_idx

    # Active search query with no match initially (set to 'all' categories)
    dock.proxy_model.set_category("all")
    dock.proxy_model.set_search_query("freshneedle")
    assert len(shared_idx.search("freshneedle")) == 0
    assert dock.proxy_model.rowCount() == 0

    # Ingest a real file named freshneedle.mp4
    needle_file = tmp_path / 'freshneedle.mp4'
    needle_file.write_bytes(b'needle-bytes')
    svc = PackIngestService(sm)
    job = svc.prepare(needle_file)
    job['name'] = 'FreshNeedlePack'
    res = svc.commit(job)
    assert res['count'] >= 1

    # Dock refresh
    dock.refresh_storage_assets()

    # Shared index is updated
    matches = shared_idx.search("freshneedle")
    assert len(matches) == 1
    assert "freshneedle" in matches[0]["asset"]["name"].lower()

    # Proxy model shows exactly 1 row with preserved search query and category
    assert dock.proxy_model.rowCount() == 1
    assert dock.proxy_model._search_query == "freshneedle"
    assert dock.proxy_model.filter_category() == "all"

    dock.close()


def test_wizard_commit_succeeds_even_when_snapshot_fails(qapp, tmp_path):
    """
    UI integration (02B1): Khi snapshot lỗi, wizard commit:
    - Vẫn phát signal pack_committed với snapshot_synced=False
    - Vẫn phát signal library_changed
    - Dialog được chấp nhận (is_pack_committed() is True)
    - Không để lại job rác hay file rác
    """
    import errno
    import time
    from unittest.mock import patch
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from src.core.storage_manager import atomic_json

    sm = StorageManager(tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "library")
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"content")

    app = QApplication.instance()
    wizard = PackIngestWizard(sm)
    try:
        wizard.input_source.setText(str(video))
        wizard._prepare()

        # Đợi draft sẵn sàng với deadline monotonic 5s, nhường thread và kiểm tra error sớm
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if wizard.job is not None and wizard.worker is None:
                break
            outcome = getattr(wizard, "_outcome", None)
            if outcome is not None and not outcome[0]:
                pytest.fail(f"Prepare thất bại sớm: outcome={outcome}, status='{wizard.lbl_status.text()}'")
            if app:
                app.processEvents()
            time.sleep(0.005)
            QTest.qWait(10)

        if wizard.job is None:
            worker_state = wizard.worker.isRunning() if wizard.worker else False
            pytest.fail(
                f"Timeout 5s chờ prepare draft: worker_running={worker_state}, "
                f"outcome={getattr(wizard, '_outcome', None)}, status='{wizard.lbl_status.text()}'"
            )

        committed_payloads = []
        library_changed_calls = []
        wizard.pack_committed.connect(committed_payloads.append)
        wizard.library_changed.connect(lambda: library_changed_calls.append(True))

        snapshot_file = sm._snapshot_path()
        original_atomic = atomic_json

        def fail_snapshot(path, data):
            if path == snapshot_file:
                raise OSError(errno.ENOSPC, "Disk full on local snapshot")
            return original_atomic(path, data)

        with patch("src.core.storage_manager.atomic_json", side_effect=fail_snapshot):
            wizard._commit()

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if wizard.is_pack_committed() and wizard.worker is None:
                    break
                outcome = getattr(wizard, "_outcome", None)
                if outcome is not None and not outcome[0]:
                    pytest.fail(f"Commit thất bại sớm: outcome={outcome}, status='{wizard.lbl_status.text()}'")
                if app:
                    app.processEvents()
                time.sleep(0.005)
                QTest.qWait(10)

        if not wizard.is_pack_committed():
            worker_state = wizard.worker.isRunning() if wizard.worker else False
            pytest.fail(
                f"Timeout 5s chờ commit: worker_running={worker_state}, "
                f"outcome={getattr(wizard, '_outcome', None)}, status='{wizard.lbl_status.text()}'"
            )

        assert wizard.is_pack_committed() is True
        assert len(committed_payloads) == 1
        assert committed_payloads[0].get("snapshot_synced") is False
        assert len(committed_payloads[0].get("warnings", [])) > 0
        assert len(library_changed_calls) >= 1
        assert wizard.job is None
    finally:
        if wizard.worker is not None and wizard.worker.isRunning():
            wizard.reject()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and wizard.worker is not None:
                if app:
                    app.processEvents()
                time.sleep(0.005)
                QTest.qWait(10)
        wizard.close()



