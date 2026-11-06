import json
import zipfile
from pathlib import Path
import pytest
from src.core.storage_manager import StorageManager, StorageOfflineError
from src.core.pack_ingest import PackIngestService


def manager(tmp_path):
    sm = StorageManager(config_dir=tmp_path / 'config', reserve_bytes=0)
    sm.configure(tmp_path / 'external')
    return sm


def test_unconfigured_and_unplugged_never_create_root(tmp_path):
    sm = StorageManager(config_dir=tmp_path / 'config', reserve_bytes=0)
    with pytest.raises(StorageOfflineError): sm.require_online()
    sm.configure(tmp_path / 'drive' / 'lib')
    root = sm.root
    root.rename(tmp_path / 'unplugged')
    assert not sm.online
    with pytest.raises(StorageOfflineError): sm.new_staging()
    assert not root.exists()


def test_review_override_commit_offline_snapshot(tmp_path):
    sm = manager(tmp_path)
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('city/shot.mp4', b'video')
        z.writestr('readme.txt', 'guide')
    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    video = next(a for a in job['assets'] if a['relative_path'].endswith('.mp4'))
    assert video['category'] == 'broll'
    video.update(category='memes', subcategory='Review Công Nghệ', tags=['Chill Vlog'])
    assert next(a for a in job['assets'] if a['relative_path'].endswith('.txt'))['excluded']
    svc.commit(job)
    assets = sm.assets()
    assert len(assets) == 1 and assets[0]['category'] == 'memes'
    assert Path(assets[0]['file_path']).read_bytes() == b'video'
    original_id = assets[0]['id']
    sm.root.rename(tmp_path / 'unplugged')
    cached = sm.assets()
    assert cached[0]['id'] == original_id and cached[0]['availability'] == 'offline'
    assert not sm.root.exists()


@pytest.mark.parametrize('name', ['../escape.mp4', '/abs.mp4', 'C:/drive.mp4', 'a/../../x.mp4'])
def test_bad_zip_never_writes_outside(tmp_path, name):
    sm = manager(tmp_path)
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z: z.writestr(name, b'x')
    with pytest.raises(ValueError): PackIngestService(sm).prepare(archive)
    assert sm.assets() == []


def test_low_space_does_not_publish(tmp_path, monkeypatch):
    sm = manager(tmp_path)
    src = tmp_path / 'movie.mp4'; src.write_bytes(b'x')
    monkeypatch.setattr(sm, 'ensure_space', lambda *args: (_ for _ in ()).throw(OSError('No space')))
    with pytest.raises(OSError): PackIngestService(sm).prepare(src)
    assert sm.assets() == []


def test_linked_source_not_copied_and_custom_taxonomy(tmp_path):
    sm = manager(tmp_path)
    src = tmp_path / 'stock'; src.mkdir(); (src / 'a.mp4').write_bytes(b'large')
    sm.add_category('product', 'Sản phẩm')
    sm.link_folder(src, category='product')
    assert sm.assets()[0]['file_path'] == str(src / 'a.mp4')
    assert not list((sm.root / '03_local_linked').rglob('*.mp4'))
    assert sm.taxonomy()['product'] == 'Sản phẩm'


def test_migrate_verifies_and_switches_root(tmp_path):
    sm = manager(tmp_path)
    src = tmp_path / 'a.wav'; src.write_bytes(b'sound')
    svc = PackIngestService(sm); svc.commit(svc.prepare(src))
    asset_id = sm.assets()[0]['id']; old = sm.root
    sm.migrate(tmp_path / 'new-drive')
    assert sm.assets()[0]['id'] == asset_id
    assert Path(sm.assets()[0]['file_path']).read_bytes() == b'sound'
    assert old.exists()  # verified copy retains source by default


def test_excluded_and_duplicate_commit(tmp_path):
    sm = manager(tmp_path); src = tmp_path / 'a.cube'; src.write_bytes(b'lut')
    svc = PackIngestService(sm); job = svc.prepare(src)
    job['assets'][0]['excluded'] = True
    with pytest.raises(ValueError): svc.commit(job)
    job['assets'][0]['excluded'] = False
    svc.commit(job)
    with pytest.raises((ValueError, FileNotFoundError)): svc.commit(job)


def test_cancel_migration_leaves_active_config_and_media(tmp_path):
    sm = manager(tmp_path); old = sm.root
    with pytest.raises(InterruptedError): sm.migrate(tmp_path/'target', cancel=lambda: True)
    assert sm.root == old and sm.online
    assert StorageManager(sm.config_dir, reserve_bytes=0).root == old


def test_zip_symlink_and_duplicate_case_rejected(tmp_path):
    sm = manager(tmp_path); archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('A.mp4',b'a'); z.writestr('a.mp4',b'b')
    with pytest.raises(ValueError): PackIngestService(sm).prepare(archive)
    with zipfile.ZipFile(archive, 'w') as z:
        info=zipfile.ZipInfo('outside'); info.create_system=3; info.external_attr=0o120777 << 16
        z.writestr(info,'/tmp')
    with pytest.raises(ValueError): PackIngestService(sm).prepare(archive)


def test_manifest_tags_and_relative_metadata_preserved(tmp_path):
    sm=manager(tmp_path); source=tmp_path/'pack'; source.mkdir()
    (source/'shot.mp4').write_bytes(b'media')
    (source/'pack_manifest.json').write_text(json.dumps({'name':'Kho Riêng', 'version':'2.0','assets':[
        {'relative_path':'shot.mp4','category':'overlays','subcategory':'grain','tags':['Review Công Nghệ']}]}),encoding='utf-8')
    svc=PackIngestService(sm); job=svc.prepare(source)
    assert job['name']=='Kho Riêng' and job['assets'][0]['tags']==['Review Công Nghệ']
    svc.commit(job); ui=sm.ui_assets()[0]
    assert ui['category']=='overlay' and ui['sub']=='grain'
    assert not Path(ui['library_path']).is_absolute()


def test_direct_http_unknown_size_guard(tmp_path, monkeypatch):
    import io
    sm=manager(tmp_path)
    class Response(io.BytesIO):
        status = 200
        headers = {}
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**kw:Response(b'video'))
    svc=PackIngestService(sm)
    job=svc.download('https://example.invalid/stock.mp4')
    assert job['assets'][0]['category']=='broll'


def test_migrate_cleanup_only_managed_media_after_verification(tmp_path):
    sm=manager(tmp_path); src=tmp_path/'s.wav'; src.write_bytes(b'sound')
    svc=PackIngestService(sm); svc.commit(svc.prepare(src)); old=sm.root
    sm.migrate(tmp_path/'new', delete_source=True)
    assert not (old/'02_cloud_downloads').exists()
    assert (old/'.chundvc_meta/registry.json').exists()
    assert Path(sm.assets()[0]['file_path']).read_bytes()==b'sound'
    assert src.exists()


def test_unlink_preserves_source_and_metadata_offline(tmp_path):
    sm=manager(tmp_path); source=tmp_path/'linked'; source.mkdir()
    (source/'v.mp4').write_bytes(b'video'); sm.link_folder(source)
    source_id=sm.links()[0]['id']; sm.unlink_folder(source_id)
    assert sm.links()==[] and sm.assets()==[] and (source/'v.mp4').exists()


def test_migration_rejects_nested_destination_through_symlink(tmp_path):
    sm=manager(tmp_path); alias=tmp_path/'alias'; alias.symlink_to(sm.root, target_is_directory=True)
    with pytest.raises(ValueError): sm.migrate(alias/'nested')


def test_bad_manifest_tags_rejected_before_review(tmp_path):
    sm=manager(tmp_path); source=tmp_path/'pack'; source.mkdir()
    (source/'v.mp4').write_bytes(b'video')
    (source/'manifest.json').write_text(json.dumps({'assets':[
        {'relative_path':'v.mp4','category':'broll','tags':42}]}))
    with pytest.raises(ValueError, match='tags'): PackIngestService(sm).prepare(source)
