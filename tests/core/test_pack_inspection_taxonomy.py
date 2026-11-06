import json
import zipfile
from pathlib import Path
import pytest
from src.core.storage_manager import StorageManager
from src.core.pack_ingest import PackIngestService, MAX_MANIFEST_BYTES


def create_test_storage(tmp_path):
    sm = StorageManager(config_dir=tmp_path / 'config', reserve_bytes=0)
    sm.configure(tmp_path / 'external')
    return sm


def test_root_manifest_pack_manifest_takes_precedence_over_manifest(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'pack_manifest.json').write_text(
        json.dumps({'name': 'Từ Pack Manifest', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'PackSub'}]}),
        encoding='utf-8'
    )
    (source / 'manifest.json').write_text(
        json.dumps({'name': 'Từ Plain Manifest', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'PlainSub'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['name'] == 'Từ Pack Manifest'
    assert job['assets'][0]['subcategory'] == 'PackSub'


def test_root_manifest_takes_precedence_over_nested(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    sub = source / 'sub'
    sub.mkdir(parents=True)
    (sub / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'name': 'Root Manifest', 'assets': [{'relative_path': 'sub/clip.mp4', 'subcategory': 'RootSub'}]}),
        encoding='utf-8'
    )
    (sub / 'pack_manifest.json').write_text(
        json.dumps({'name': 'Nested Manifest', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'NestedSub'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['name'] == 'Root Manifest'
    assert job['assets'][0]['subcategory'] == 'RootSub'


def test_invalid_root_manifest_does_not_fallback_to_nested(tmp_path):
    sm = create_test_storage(tmp_path)
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('sub/clip.mp4', b'video')
        z.writestr('manifest.json', '{ invalid json ...')
        z.writestr('sub/pack_manifest.json', json.dumps({'name': 'Nested Sneak', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'SneakSub'}]}))
    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    # Phải giữ nguyên name mặc định và guess, có warning, không nhận SneakSub
    assert job['name'] != 'Nested Sneak'
    assert job['assets'][0]['subcategory'] != 'SneakSub'
    assert any('Manifest JSON không hợp lệ' in w for w in job.get('warnings', []))


def test_nested_manifest_selected_when_no_root(tmp_path):
    sm = create_test_storage(tmp_path)
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('sub/clip.mp4', b'video')
        z.writestr('sub/manifest.json', json.dumps({'name': 'Nested Only', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'NestedSub'}]}))
    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    assert job['name'] == 'Nested Only'
    assert job['assets'][0]['subcategory'] == 'NestedSub'


def test_manifest_candidates_limit_exceeded(tmp_path):
    sm = create_test_storage(tmp_path)
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('media.mp4', b'data')
        for i in range(129):
            z.writestr(f'dir_{i}/manifest.json', '{}')
    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='Quá nhiều file manifest'):
        svc.prepare(archive)


def test_manifest_symlink_rejected(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    outside = tmp_path / 'outside_manifest.json'
    outside.write_text('{}', encoding='utf-8')
    sym_manifest = source / 'manifest.json'
    sym_manifest.symlink_to(outside)
    svc = PackIngestService(sm)
    with pytest.raises(ValueError, match='symlink'):
        svc.prepare(source)


def test_manifest_bounded_read_oversize_skipped_with_warning(tmp_path):
    sm = create_test_storage(tmp_path)
    job_dir = sm.new_staging()
    payload = job_dir / 'payload'
    payload.mkdir()
    (payload / 'clip.mp4').write_bytes(b'video')
    # Tạo manifest vượt quá 2 MiB trong staging payload
    large_comment = " " * (MAX_MANIFEST_BYTES + 10)
    (payload / 'manifest.json').write_text(f'{{"name": "Big", "comment": "{large_comment}"}}', encoding='utf-8')
    svc = PackIngestService(sm)
    job = svc.inspect_job(job_dir)
    assert job['name'] != 'Big'
    assert any('Manifest quá lớn' in w for w in job.get('warnings', []))


def test_manifest_unsupported_schema_version_skipped_with_warning(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'schema_version': 99, 'name': 'Future Pack', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'Futuristic'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['name'] != 'Future Pack'
    assert job['assets'][0]['subcategory'] != 'Futuristic'
    assert any('schema_version' in w for w in job.get('warnings', []))


def test_manifest_legacy_missing_schema_version_accepted(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'name': 'Legacy Pack', 'version': '1.5', 'assets': [{'relative_path': 'clip.mp4', 'subcategory': 'LegacySub'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['name'] == 'Legacy Pack'
    assert job['version'] == '1.5'
    assert job['assets'][0]['subcategory'] == 'LegacySub'


def test_manifest_unknown_category_preserves_guess_and_records_suggestion(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'shot.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'shot.mp4', 'category': 'unregistered_alien_category'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    # Phải giữ nguyên guess ('broll')
    assert job['assets'][0]['category'] == 'broll'
    # Phải có warning cấp job
    assert any('unregistered_alien_category' in w for w in job.get('warnings', []))
    # Phải có trong manifest_suggestions
    assert job.get('manifest_suggestions', {}).get('shot.mp4', {}).get('category') == 'unregistered_alien_category'
    # Không được tự ý ghi vào taxonomy trên đĩa
    assert 'unregistered_alien_category' not in sm.taxonomy()


def test_manifest_registered_custom_category_accepted(tmp_path):
    sm = create_test_storage(tmp_path)
    sm.add_category('podcast_talk', 'Podcast Trò Chuyện')
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'talk.mp3').write_bytes(b'audio')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'talk.mp3', 'category': 'podcast_talk'}]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['assets'][0]['category'] == 'podcast_talk'


def test_manifest_immutable_fields_cannot_be_overridden(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({
            'assets': [{
                'relative_path': 'clip.mp4',
                'id': '00000000000000000000000000000000',
                'source_kind': 'linked',
                'library_path': 'evil/path.mp4',
                'external_path': '/etc/passwd',
                'size_bytes': 999999999,
                '_raw_obj': 'handle'
            }]
        }),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    asset = job['assets'][0]
    assert asset['id'] != '00000000000000000000000000000000'
    assert asset.get('source_kind') is None
    assert asset.get('library_path') is None
    assert asset.get('external_path') is None
    assert asset.get('_raw_obj') is None
    assert asset['size_bytes'] == len(b'video')


def test_manifest_relative_path_prefix_escape_ignored(tmp_path):
    sm = create_test_storage(tmp_path)
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('root_file.mp4', b'root_media')
        z.writestr('sub/sub_file.mp4', b'sub_media')
        # Nested manifest cố gắng thoát ra ngoài prefix 'sub' bằng '../root_file.mp4'
        z.writestr('sub/manifest.json', json.dumps({'assets': [
            {'relative_path': '../root_file.mp4', 'subcategory': 'HackedSub'},
            {'relative_path': 'sub_file.mp4', 'subcategory': 'SafeSub'}
        ]}))
    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    by_path = {a['relative_path']: a for a in job['assets']}
    # root_file không bị ghi đè subcategory
    assert by_path['root_file.mp4']['subcategory'] != 'HackedSub'
    # sub_file vẫn nhận SafeSub
    assert by_path['sub/sub_file.mp4']['subcategory'] == 'SafeSub'


def test_manifest_traversal_or_absolute_relative_path_ignored(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [
            {'relative_path': '/etc/passwd', 'category': 'memes'},
            {'relative_path': '..\\outside.mp4', 'category': 'memes'},
            {'relative_path': 'clip\x00bad.mp4', 'category': 'memes'},
        ]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['assets'][0]['category'] == 'broll'
    assert any('relative_path không hợp lệ' in w for w in job.get('warnings', []))


def test_manifest_duplicate_entries_first_wins_with_warning(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [
            {'relative_path': 'clip.mp4', 'subcategory': 'FirstSub'},
            {'relative_path': 'clip.mp4', 'subcategory': 'SecondSub'},
        ]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert job['assets'][0]['subcategory'] == 'FirstSub'
    assert any('trùng lặp' in w for w in job.get('warnings', []))


def test_manifest_actual_disk_casefold_ambiguity_skipped_with_warning(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    try:
        (source / 'clip.mp4').write_bytes(b'lower')
        (source / 'CLIP.mp4').write_bytes(b'upper')
    except OSError:
        pytest.skip('Hệ điều hành không phân biệt hoa thường (case-insensitive)')
    if not ((source / 'clip.mp4').is_file() and (source / 'CLIP.mp4').is_file()):
        pytest.skip('Hệ điều hành không phân biệt hoa thường')

    (source / 'manifest.json').write_text(
        json.dumps({'assets': [
            {'relative_path': 'clip.mp4', 'subcategory': 'AmbiguousSub'},
        ]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    # Cả hai file đều không nhận AmbiguousSub vì tránh ghi đè nhầm
    for a in job['assets']:
        assert a['subcategory'] != 'AmbiguousSub'
    assert any('hoa/thường' in w for w in job.get('warnings', []))


def test_manifest_unmapped_entry_ignored_without_phantom(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'real.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [
            {'relative_path': 'real.mp4', 'subcategory': 'RealSub'},
            {'relative_path': 'phantom.mp4', 'subcategory': 'GhostSub'},
        ]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert len(job['assets']) == 1
    assert job['assets'][0]['relative_path'] == 'real.mp4'
    assert any('không tồn tại trên đĩa' in w for w in job.get('warnings', []))


def test_manifest_legacy_validation_errors(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    svc = PackIngestService(sm)

    # 1. Tags wrong type
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'clip.mp4', 'tags': 42}]}), encoding='utf-8'
    )
    with pytest.raises(ValueError, match='manifest.tags'):
        svc.prepare(source)

    # 2. Excluded wrong type
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'clip.mp4', 'excluded': 'yes'}]}), encoding='utf-8'
    )
    with pytest.raises(ValueError, match='manifest.excluded'):
        svc.prepare(source)

    # 3. Category wrong type
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'clip.mp4', 'category': 999}]}), encoding='utf-8'
    )
    with pytest.raises(ValueError, match='manifest.category'):
        svc.prepare(source)

    # 4. Template_type wrong type
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [{'relative_path': 'clip.mp4', 'template_type': True}]}), encoding='utf-8'
    )
    with pytest.raises(ValueError, match='manifest.template_type'):
        svc.prepare(source)


def test_manifest_template_type_alias_and_invalid_enum(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 't1.setting').write_bytes(b'macro')
    (source / 't2.setting').write_bytes(b'macro')
    (source / 't3.setting').write_bytes(b'macro')
    (source / 'manifest.json').write_text(
        json.dumps({'assets': [
            {'relative_path': 't1.setting', 'template_type': 'title'},
            {'relative_path': 't2.setting', 'template_type': 'transition'},
            {'relative_path': 't3.setting', 'template_type': 'zoom_fade'},
        ]}),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    by_name = {a['relative_path']: a for a in job['assets']}
    assert by_name['t1.setting']['template_type'] == 'text'
    assert by_name['t2.setting']['template_type'] == 'trans'
    # t3 có template_type không hợp lệ: giữ guess ('text') và có warning
    assert by_name['t3.setting']['template_type'] == 'text'
    assert any('zoom_fade' in w for w in job.get('warnings', []))


def test_end_to_end_prepare_commit_no_warnings_in_registry(tmp_path):
    sm = create_test_storage(tmp_path)
    source = tmp_path / 'pack'
    source.mkdir()
    (source / 'clip.mp4').write_bytes(b'video')
    (source / 'manifest.json').write_text(
        json.dumps({
            'name': 'Gói Tiếng Việt',
            'version': '1.0',
            'assets': [
                {'relative_path': 'clip.mp4', 'category': 'alien_genre', 'tags': ['Hậu Kỳ', 'Vlog']},
                {'relative_path': 'ghost.mp4', 'category': 'broll'}  # Sẽ sinh warning
            ]
        }),
        encoding='utf-8'
    )
    svc = PackIngestService(sm)
    job = svc.prepare(source)
    assert 'warnings' in job and len(job['warnings']) > 0
    assert 'manifest_suggestions' in job

    # Commit job
    res = svc.commit(job)
    assert res['count'] == 1

    # Kiểm tra registry trên đĩa
    reg = sm.registry()
    committed_asset = reg['assets'][0]
    assert committed_asset['name'] == 'clip'
    assert committed_asset['category'] == 'broll'  # fallback từ alien_genre
    assert committed_asset['tags'] == ['Hậu Kỳ', 'Vlog']
    assert 'warnings' not in committed_asset
    assert 'manifest_suggestions' not in committed_asset
    assert '_manifest_suggested_category' not in committed_asset
    assert '_warning' not in committed_asset
    assert '_role' not in committed_asset

    # Kiểm tra pack_manifest.json trong folder pack
    pack_manifest = sm.root / reg['packs'][0]['folder'] / 'pack_manifest.json'
    pm_data = json.loads(pack_manifest.read_text(encoding='utf-8'))
    assert 'warnings' not in pm_data
    assert 'manifest_suggestions' not in pm_data
    pm_asset = pm_data['assets'][0]
    assert 'warnings' not in pm_asset
    assert '_warning' not in pm_asset
