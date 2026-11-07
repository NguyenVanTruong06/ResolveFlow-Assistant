"""Nhập pack có kiểm duyệt: staging trên kho, dự đoán metadata, commit atomic."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path
from collections.abc import Mapping
from typing import Callable, Optional
from src.core.contracts.asset_dto import is_safe_relative_path
from src.core.storage_manager import StorageManager, StorageOfflineError, MAX_JSON, atomic_json, read_json, safe_relative, CATEGORIES

logger = logging.getLogger(__name__)

VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}
AUDIO = {'.wav', '.mp3', '.flac', '.aac', '.m4a', '.ogg'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp'}
MAX_FILES = 100_000
MAX_MANIFEST_CANDIDATES = 128
MAX_PACK_STREAM_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB trần tải gói HTTP trực tiếp
MAX_UNCOMPRESSED_PACK_BYTES = 20 * 1024 * 1024 * 1024  # 20 GiB trần dung lượng giải nén gói ZIP
MAX_MANIFEST_BYTES = 2 * 1024 * 1024  # 2 MiB trần dung lượng manifest (cả declared lẫn actual)



def validate_draft_taxonomy(value) -> dict[str, str]:
    """Xác thực cả ở UI và core; draft không tự trở thành dữ liệu lưu trữ."""
    from src.core.contracts.asset_dto import is_valid_custom_category_id

    if not isinstance(value, Mapping):
        raise ValueError('Danh mục nháp phải là một mapping ID và nhãn.')
    result = {}
    for cid, label in value.items():
        if not is_valid_custom_category_id(cid):
            raise ValueError('ID danh mục: chữ thường, số, dấu _, tối đa 48 ký tự; không dùng ID dành riêng.')
        if not isinstance(label, str):
            raise ValueError('Nhãn danh mục phải là chuỗi.')
        result[cid] = label.strip()[:100] or cid
    return result


def _discover_manifest_candidate(payload: Path, cancel: Callable[[], bool]) -> Optional[Path]:
    """
    Khám phá và chọn duy nhất một manifest theo thứ tự ưu tiên:
    1. Root pack_manifest.json
    2. Root manifest.json
    3. Nested manifest gần root nhất (độ sâu nhỏ nhất, pack_manifest trước manifest, rồi POSIX path).
    Giới hạn MAX_MANIFEST_CANDIDATES = 128. Không follow symlinks.
    """
    candidate_paths: list[Path] = []
    for directory, dirs, files in os.walk(payload, followlinks=False):
        if cancel():
            raise InterruptedError('Đã hủy kiểm tra gói.')
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if name in ('pack_manifest.json', 'manifest.json'):
                candidate_paths.append(Path(directory) / name)
                if len(candidate_paths) > MAX_MANIFEST_CANDIDATES:
                    raise ValueError('Quá nhiều file manifest tiềm năng trong gói; hãy chia nhỏ.')

    if not candidate_paths:
        return None

    root_pack = payload / 'pack_manifest.json'
    root_plain = payload / 'manifest.json'

    if root_pack in candidate_paths:
        return root_pack
    if root_plain in candidate_paths:
        return root_plain

    def _candidate_sort_key(p: Path):
        rel = p.relative_to(payload)
        return (len(rel.parts), 0 if p.name == 'pack_manifest.json' else 1, rel.as_posix())

    candidate_paths.sort(key=_candidate_sort_key)
    return candidate_paths[0]


def _is_macos_junk(rel: Path) -> bool:
    """Nhận diện tệp rác hệ thống macOS/Windows (__MACOSX, ._* AppleDouble, .DS_Store, Thumbs.db)."""
    return (
        '__MACOSX' in rel.parts
        or rel.name.startswith('._')
        or rel.name.lower() in ('.ds_store', 'thumbs.db', 'desktop.ini')
    )


def guess(path: Path):
    name = path.as_posix().lower(); ext = path.suffix.lower()
    category = 'broll'; excluded = False
    parts_lower = {p.lower() for p in path.parts}
    if parts_lower & {'previews', 'thumbnails'}:
        category, excluded = 'templates', True
    elif parts_lower & {'tutorials', 'docs', 'help'}:
        category, excluded = 'docs', True
    elif ext == '.cube': category = 'luts'
    elif ext in {'.setting', '.drfx', '.comp'}: category = 'templates'
    elif ext in AUDIO: category = 'bgm' if any(s in name for s in ('bgm', 'music', 'nhạc', 'chill', 'lofi')) else 'sfx'
    elif ext in VIDEO | IMAGES:
        if any(s in name for s in ('transition', 'chuyển cảnh')): category, excluded = 'templates', True
        elif any(s in name for s in ('meme', 'reaction')): category = 'memes'
        elif any(s in name for s in ('overlay', 'grain', 'light_leak', 'light leak', 'vhs', 'dust')): category = 'overlays'
    else: excluded = True
    return category, excluded


def _has_nearby_macro_file(path: Path, root: Path) -> bool:
    """Kiểm tra thư mục cha hoặc thư mục anh em có chứa file .setting / .drfx hay không."""
    curr = path.parent
    while True:
        try:
            for child in curr.iterdir():
                if child.is_file() and child.suffix.lower() in {'.setting', '.drfx', '.comp'}:
                    return True
        except OSError:
            pass
        if curr == root or curr.parent == curr:
            break
        curr = curr.parent
    return False


def inspect_tree(root: Path, source_id: str, cancel=lambda: False):
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if d != '__MACOSX' and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(files):
            if cancel(): raise InterruptedError('Đã hủy kiểm tra gói.')
            path = Path(directory) / name
            if path.is_symlink(): raise ValueError('Không nhập symlink trong pack.')
            rel = path.relative_to(root)
            if _is_macos_junk(rel):
                continue
            if name in ('manifest.json', 'pack_manifest.json'): continue
            category, excluded = guess(rel)
            ext = path.suffix.lower()
            if ext in IMAGES and (
                re.search(r'_[0-9]{3}$', path.stem)
                or path.stem.lower() in ('paper', 'texture', 'mask')
            ) and _has_nearby_macro_file(path, root):
                excluded = True
            result.append({'id': uuid.uuid5(uuid.NAMESPACE_URL, source_id + ':' + rel.as_posix()).hex,
                           'relative_path': rel.as_posix(), 'name': path.stem, 'category': category,
                           'subcategory': rel.parent.name if rel.parent != Path('.') else '', 'tags': [],
                           'template_type': 'trans' if 'trans' in rel.as_posix().lower() else 'text',
                           'excluded': excluded, 'size_bytes': path.stat().st_size})
            if len(result) > MAX_FILES: raise ValueError('Gói vượt100.000file; hãy chia nhỏ.')
    return result


class PackIngestService:
    def __init__(self, storage: StorageManager): self.storage = storage

    def prepare(self, source, cancel=lambda: False, progress=lambda text: None, pack_id=None, pack_name=None):
        self.storage.require_online()
        job_dir = self.storage.new_staging()
        payload = job_dir / 'payload'; payload.mkdir()
        try:
            source = Path(source)
            if source.is_symlink(): raise ValueError('Không nhập symlink; dùng liên kết thư mục.')
            if source.is_file() and zipfile.is_zipfile(source):
                self._extract(source, payload, cancel, progress)
            elif source.is_dir():
                records = inspect_tree(source, uuid.uuid4().hex, cancel)
                self.storage.ensure_space(sum(a['size_bytes'] for a in records))
                for item in records:
                    rel = safe_relative(item['relative_path']); dest = payload / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self.storage.copy_file(source / rel, dest, cancel, progress)
                # Metadata pack nhỏ được giữ lại để đọc phân loại.
                for name in ('manifest.json', 'pack_manifest.json'):
                    p = source / name
                    if p.is_file() and p.stat().st_size <= 2 * 1024**2:
                        self.storage.copy_file(p, payload / name, cancel, progress)
            elif source.is_file():
                self.storage.copy_file(source, payload / source.name, cancel, progress)
            else: raise FileNotFoundError('Nguồn nhập không tồn tại.')
            return self.inspect_job(job_dir, pack_name or source.stem, cancel, pack_id=pack_id)
        except Exception:
            if self.storage.online: shutil.rmtree(job_dir, ignore_errors=True)
            raise

    def _extract(self, source, payload, cancel, progress):
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES: raise ValueError('Gói có quá nhiều file.')
            seen = set(); total = 0
            for entry in entries:
                if cancel(): raise InterruptedError('Đã hủy giải nén.')
                rel = safe_relative(entry.filename)
                key = rel.as_posix().casefold()
                if key in seen: raise ValueError('ZIP có tên file trùng hoặc khác hoa/thường.')
                seen.add(key)
                if stat.S_ISLNK(entry.external_attr >> 16): raise ValueError('ZIP chứa symlink.')
                if entry.flag_bits & 1: raise ValueError('ZIP có mật khẩu chưa được hỗ trợ.')
                total += entry.file_size
                if total > MAX_UNCOMPRESSED_PACK_BYTES:
                    raise ValueError('Tổng dung lượng giải nén vượt giới hạn 20 GiB.')
                if rel.name in ('manifest.json', 'pack_manifest.json') and entry.file_size > MAX_MANIFEST_BYTES:
                    raise ValueError('Manifest quá lớn.')
            self.storage.ensure_space(total)
            total_written = 0
            for entry in entries:
                if cancel(): raise InterruptedError('Đã hủy giải nén.')
                self.storage.ensure_space()
                rel = safe_relative(entry.filename)
                dest = payload / rel
                if entry.is_dir(): dest.mkdir(parents=True, exist_ok=True); continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                is_manifest = rel.name in ('manifest.json', 'pack_manifest.json')
                with archive.open(entry) as src, open(dest, 'xb') as dst:
                    while block := src.read(1024**2):
                        if cancel(): raise InterruptedError('Đã hủy giải nén.')
                        if total_written + len(block) > MAX_UNCOMPRESSED_PACK_BYTES:
                            raise ValueError('Tổng dung lượng giải nén vượt giới hạn 20 GiB.')
                        if is_manifest and written + len(block) > MAX_MANIFEST_BYTES:
                            raise ValueError('Manifest quá lớn.')
                        self.storage.ensure_space(len(block))
                        dst.write(block)
                        written += len(block)
                        total_written += len(block)
                if written != entry.file_size: raise ValueError('Kích thước giải nén không khớp.')
                progress(entry.filename)

    def inspect_job(self, directory, name='Pack', cancel=lambda: False, pack_id=None):
        directory = Path(directory); payload = directory / 'payload'
        pack_id = str(pack_id).strip() if pack_id else uuid.uuid4().hex
        records = inspect_tree(payload, pack_id, cancel)
        if not records:
            raise ValueError('Gói không có file để kiểm duyệt.')

        warnings: list[str] = []
        manifest_suggestions: dict[str, dict] = {}
        job_name = name
        job_version = '1.0'

        candidate_path = _discover_manifest_candidate(payload, cancel)
        if candidate_path is not None:
            # 1. Thẩm định tính an toàn đường dẫn và symlink
            try:
                st = os.lstat(candidate_path)
            except OSError:
                raise ValueError('Không thể đọc thông tin file manifest.')

            if stat.S_ISLNK(st.st_mode) or candidate_path.is_symlink():
                raise ValueError('Manifest là symlink hoặc trỏ ra ngoài.')
            if not stat.S_ISREG(st.st_mode):
                raise ValueError('Manifest không phải file thông thường.')

            curr = payload
            for part in candidate_path.relative_to(payload).parts:
                curr = curr / part
                if os.path.islink(curr) or curr.is_symlink():
                    raise ValueError('Đường dẫn manifest chứa symlink.')

            if not candidate_path.resolve().is_relative_to(payload.resolve()):
                raise ValueError('Manifest thoát khỏi thư mục gói.')

            # 2. Đọc có giới hạn MAX_MANIFEST_BYTES + 1 và postcheck growth
            skip_hints = False
            with open(candidate_path, 'rb') as f:
                raw_bytes = f.read(MAX_MANIFEST_BYTES + 1)

            if len(raw_bytes) > MAX_MANIFEST_BYTES:
                warnings.append(f'Manifest quá lớn (> {MAX_MANIFEST_BYTES} bytes); bỏ qua gợi ý metadata.')
                skip_hints = True
            else:
                st_post = os.lstat(candidate_path)
                if st_post.st_size > MAX_MANIFEST_BYTES:
                    warnings.append('Manifest quá lớn; bỏ qua gợi ý metadata.')
                    skip_hints = True

            # 3. Parse JSON và kiểm tra kiểu object
            metadata: dict = {}
            if not skip_hints:
                try:
                    content_str = raw_bytes.decode('utf-8')
                    parsed = json.loads(content_str)
                    if isinstance(parsed, dict):
                        metadata = parsed
                    else:
                        warnings.append('Manifest không phải JSON object; bỏ qua gợi ý metadata.')
                        skip_hints = True
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    warnings.append(f'Manifest JSON không hợp lệ: {exc}; bỏ qua gợi ý metadata.')
                    skip_hints = True

            # 4. Schema version
            if not skip_hints and 'schema_version' in metadata:
                sv = metadata['schema_version']
                if type(sv) is not int or sv != 1:
                    warnings.append(f'Phiên bản manifest schema_version={sv!r} không được hỗ trợ; bỏ qua gợi ý metadata.')
                    skip_hints = True

            # 5. Top-level name & version
            if not skip_hints:
                if 'name' in metadata:
                    raw_n = metadata['name']
                    if isinstance(raw_n, str) and raw_n.strip():
                        job_name = raw_n.strip()[:200]
                    elif raw_n is not None and not isinstance(raw_n, str):
                        warnings.append("Trường 'name' cấp cao trong manifest không phải chuỗi; bỏ qua.")

                if 'version' in metadata:
                    raw_v = metadata['version']
                    if isinstance(raw_v, str) and raw_v.strip():
                        job_version = raw_v.strip()[:200]
                    elif raw_v is not None and not isinstance(raw_v, str):
                        warnings.append("Trường 'version' cấp cao trong manifest không phải chuỗi; bỏ qua.")

                # 6. Assets list
                if 'assets' in metadata:
                    entries = metadata['assets']
                    if not isinstance(entries, list):
                        warnings.append("Trường 'assets' trong manifest không phải danh sách; bỏ qua toàn bộ gợi ý metadata.")
                        skip_hints = True
                    else:
                        exact_records_map = {item['relative_path']: item for item in records}
                        casefold_records_map: dict[str, dict] = {}
                        casefold_records_ambiguity: set[str] = set()
                        for item in records:
                            k = item['relative_path'].casefold()
                            if k in casefold_records_map:
                                casefold_records_ambiguity.add(k)
                            else:
                                casefold_records_map[k] = item

                        prefix = candidate_path.parent.relative_to(payload)
                        seen_manifest_keys: set[str] = set()
                        referenced_companions: set[str] = set()
                        tax = self.storage.taxonomy()

                        # Thu thập trước toàn bộ master paths được khai báo trong manifest để chặn role collision
                        declared_masters: set[str] = set()
                        declared_masters_casefold: set[str] = set()
                        for e_check in entries:
                            if isinstance(e_check, dict):
                                raw_rel_m = e_check.get('relative_path')
                                if isinstance(raw_rel_m, str) and is_safe_relative_path(raw_rel_m):
                                    entry_rel_m = safe_relative(raw_rel_m)
                                    target_rel_m = (prefix / entry_rel_m) if prefix != Path('.') else entry_rel_m
                                    if prefix == Path('.') or target_rel_m.is_relative_to(prefix):
                                        m_posix = target_rel_m.as_posix()
                                        declared_masters.add(m_posix)
                                        declared_masters_casefold.add(m_posix.casefold())

                        for entry in entries:
                            if cancel():
                                raise InterruptedError('Đã hủy kiểm tra gói.')
                            if not isinstance(entry, dict):
                                warnings.append('Entry trong manifest không phải JSON object; bỏ qua.')
                                continue

                            raw_rel = entry.get('relative_path')
                            if not isinstance(raw_rel, str) or not is_safe_relative_path(raw_rel):
                                warnings.append(f"Entry trong manifest có relative_path không hợp lệ: {raw_rel!r}; bỏ qua.")
                                continue

                            entry_rel = safe_relative(raw_rel)
                            target_rel = (prefix / entry_rel) if prefix != Path('.') else entry_rel
                            if prefix != Path('.') and not target_rel.is_relative_to(prefix):
                                warnings.append(f"Entry manifest '{raw_rel}' thoát khỏi thư mục cha '{prefix}'; bỏ qua.")
                                continue

                            target_posix = target_rel.as_posix()
                            target_casefold = target_posix.casefold()

                            if target_casefold in seen_manifest_keys:
                                warnings.append(f"Manifest chứa nhiều entry trùng lặp (không phân biệt hoa/thường): '{target_posix}'; giữ entry đầu tiên.")
                                continue
                            seen_manifest_keys.add(target_casefold)

                            if target_casefold in casefold_records_ambiguity:
                                warnings.append(f"Đĩa thực tế có nhiều file cùng tên khác hoa/thường cho '{target_posix}'; bỏ qua gợi ý manifest.")
                                continue

                            item = exact_records_map.get(target_posix)
                            if item is None:
                                item = casefold_records_map.get(target_casefold)

                            if item is None:
                                warnings.append(f"Manifest khai báo file không tồn tại trên đĩa: '{target_posix}'; bỏ qua.")
                                continue

                            # Xử lý liên kết Preview Companion (nhận cả canonical và alias)
                            raw_prev_canonical = None
                            if 'preview_relative_path' in entry:
                                v_prp = entry['preview_relative_path']
                                if v_prp is not None:
                                    if not isinstance(v_prp, str) or isinstance(v_prp, bool):
                                        raise ValueError('manifest.preview_relative_path phải là chuỗi.')
                                    if not is_safe_relative_path(v_prp):
                                        raise ValueError(f"manifest.preview_relative_path không phải đường dẫn tương đối an toàn: {v_prp!r}.")
                                    raw_prev_canonical = v_prp

                            raw_prev_alias = None
                            if 'preview_path' in entry:
                                v_pp = entry['preview_path']
                                if v_pp is not None:
                                    if not isinstance(v_pp, str) or isinstance(v_pp, bool):
                                        raise ValueError('manifest.preview_path phải là chuỗi.')
                                    if not is_safe_relative_path(v_pp):
                                        raise ValueError(f"manifest.preview_path không phải đường dẫn tương đối an toàn: {v_pp!r}.")
                                    raw_prev_alias = v_pp

                            if raw_prev_canonical is not None and raw_prev_alias is not None:
                                if safe_relative(raw_prev_canonical) != safe_relative(raw_prev_alias):
                                    raise ValueError(f"Xung đột giữa preview_relative_path ({raw_prev_canonical!r}) và preview_path ({raw_prev_alias!r}): giá trị không khớp.")

                            chosen_prev = raw_prev_canonical or raw_prev_alias
                            if chosen_prev:
                                p_rel = safe_relative(chosen_prev)
                                target_p = (prefix / p_rel) if prefix != Path('.') else p_rel
                                if prefix != Path('.') and not target_p.is_relative_to(prefix):
                                    raise ValueError(f"Companion preview '{chosen_prev}' thoát khỏi thư mục cha '{prefix}'.")
                                target_p_posix = target_p.as_posix()
                                target_p_disk = payload / target_p

                                if target_p_disk.is_symlink():
                                    raise ValueError(f"Companion preview '{target_p_posix}' là symlink không hợp lệ.")
                                if target_p_disk.exists() and not target_p_disk.resolve().is_relative_to(payload.resolve()):
                                    raise ValueError(f"Companion preview '{target_p_posix}' thoát khỏi thư mục gói.")

                                if target_p_posix.casefold() == item['relative_path'].casefold():
                                    raise ValueError(f"Companion preview không thể tự trỏ tới chính master: '{target_p_posix}'.")
                                if target_p_disk.suffix.lower() in {'.setting', '.drfx', '.comp'}:
                                    raise ValueError(f"Companion preview không được là một master template: '{target_p_posix}'.")
                                if target_p_posix.casefold() in declared_masters_casefold or target_p_posix in declared_masters:
                                    raise ValueError(f"Companion preview '{target_p_posix}' đồng thời được khai báo là master asset độc lập trong manifest.")
                                if target_p_posix.casefold() in casefold_records_ambiguity:
                                    raise ValueError(f"Companion preview '{target_p_posix}' bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường.")
                                if target_p_disk.parent.is_dir():
                                    try:
                                        sibling_names_cf = [f.name.casefold() for f in target_p_disk.parent.iterdir() if not f.is_symlink()]
                                        if sibling_names_cf.count(target_p_disk.name.casefold()) > 1:
                                            raise ValueError(f"Companion preview '{target_p_posix}' bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường.")
                                    except OSError:
                                        pass

                                matched_comp = exact_records_map.get(target_p_posix)
                                if matched_comp is None:
                                    matched_comp = casefold_records_map.get(target_p_posix.casefold())

                                if matched_comp is None and not target_p_disk.is_file():
                                    warnings.append(f"Manifest khai báo preview không tồn tại trên đĩa: '{target_p_posix}'; bỏ qua liên kết preview.")
                                else:
                                    actual_p_posix = matched_comp['relative_path'] if matched_comp else target_p_posix
                                    item['preview_relative_path'] = actual_p_posix
                                    referenced_companions.add(actual_p_posix)

                            # Xử lý liên kết Thumbnail Companion (nhận cả canonical và alias)
                            raw_thumb_canonical = None
                            if 'thumbnail_relative_path' in entry:
                                v_trp = entry['thumbnail_relative_path']
                                if v_trp is not None:
                                    if not isinstance(v_trp, str) or isinstance(v_trp, bool):
                                        raise ValueError('manifest.thumbnail_relative_path phải là chuỗi.')
                                    if not is_safe_relative_path(v_trp):
                                        raise ValueError(f"manifest.thumbnail_relative_path không phải đường dẫn tương đối an toàn: {v_trp!r}.")
                                    raw_thumb_canonical = v_trp

                            raw_thumb_alias = None
                            if 'thumbnail_path' in entry:
                                v_tp = entry['thumbnail_path']
                                if v_tp is not None:
                                    if not isinstance(v_tp, str) or isinstance(v_tp, bool):
                                        raise ValueError('manifest.thumbnail_path phải là chuỗi.')
                                    if not is_safe_relative_path(v_tp):
                                        raise ValueError(f"manifest.thumbnail_path không phải đường dẫn tương đối an toàn: {v_tp!r}.")
                                    raw_thumb_alias = v_tp

                            if raw_thumb_canonical is not None and raw_thumb_alias is not None:
                                if safe_relative(raw_thumb_canonical) != safe_relative(raw_thumb_alias):
                                    raise ValueError(f"Xung đột giữa thumbnail_relative_path ({raw_thumb_canonical!r}) và thumbnail_path ({raw_thumb_alias!r}): giá trị không khớp.")

                            chosen_thumb = raw_thumb_canonical or raw_thumb_alias
                            if chosen_thumb:
                                t_rel = safe_relative(chosen_thumb)
                                target_t = (prefix / t_rel) if prefix != Path('.') else t_rel
                                if prefix != Path('.') and not target_t.is_relative_to(prefix):
                                    raise ValueError(f"Companion thumbnail '{chosen_thumb}' thoát khỏi thư mục cha '{prefix}'.")
                                target_t_posix = target_t.as_posix()
                                target_t_disk = payload / target_t

                                if target_t_disk.is_symlink():
                                    raise ValueError(f"Companion thumbnail '{target_t_posix}' là symlink không hợp lệ.")
                                if target_t_disk.exists() and not target_t_disk.resolve().is_relative_to(payload.resolve()):
                                    raise ValueError(f"Companion thumbnail '{target_t_posix}' thoát khỏi thư mục gói.")

                                if target_t_posix.casefold() == item['relative_path'].casefold():
                                    raise ValueError(f"Companion thumbnail không thể tự trỏ tới chính master: '{target_t_posix}'.")
                                if target_t_disk.suffix.lower() in {'.setting', '.drfx', '.comp'}:
                                    raise ValueError(f"Companion thumbnail không được là một master template: '{target_t_posix}'.")
                                if target_t_posix.casefold() in declared_masters_casefold or target_t_posix in declared_masters:
                                    raise ValueError(f"Companion thumbnail '{target_t_posix}' đồng thời được khai báo là master asset độc lập trong manifest.")
                                if target_t_posix.casefold() in casefold_records_ambiguity:
                                    raise ValueError(f"Companion thumbnail '{target_t_posix}' bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường.")
                                if target_t_disk.parent.is_dir():
                                    try:
                                        sibling_names_cf = [f.name.casefold() for f in target_t_disk.parent.iterdir() if not f.is_symlink()]
                                        if sibling_names_cf.count(target_t_disk.name.casefold()) > 1:
                                            raise ValueError(f"Companion thumbnail '{target_t_posix}' bị mơ hồ do đĩa chứa nhiều file cùng tên khác hoa/thường.")
                                    except OSError:
                                        pass

                                matched_thumb = exact_records_map.get(target_t_posix)
                                if matched_thumb is None:
                                    matched_thumb = casefold_records_map.get(target_t_posix.casefold())

                                if matched_thumb is None and not target_t_disk.is_file():
                                    warnings.append(f"Manifest khai báo thumbnail không tồn tại trên đĩa: '{target_t_posix}'; bỏ qua liên kết thumbnail.")
                                else:
                                    actual_t_posix = matched_thumb['relative_path'] if matched_thumb else target_t_posix
                                    item['thumbnail_relative_path'] = actual_t_posix
                                    referenced_companions.add(actual_t_posix)

                            if 'tags' in entry:
                                v_tags = entry['tags']
                                if not isinstance(v_tags, list) or any(not isinstance(t, str) for t in v_tags):
                                    raise ValueError('manifest.tags phải là danh sách chuỗi.')
                                item['tags'] = [t[:100] for t in v_tags[:50]]

                            if 'excluded' in entry:
                                v_exc = entry['excluded']
                                if not isinstance(v_exc, bool):
                                    raise ValueError('manifest.excluded phải là true/false.')
                                item['excluded'] = v_exc

                            if 'name' in entry:
                                v_name = entry['name']
                                if not isinstance(v_name, str):
                                    raise ValueError('manifest.name phải là chuỗi.')
                                if v_name.strip():
                                    item['name'] = v_name.strip()[:200]

                            if 'subcategory' in entry:
                                v_sub = entry['subcategory']
                                if not isinstance(v_sub, str):
                                    raise ValueError('manifest.subcategory phải là chuỗi.')
                                item['subcategory'] = v_sub[:200]

                            if 'category' in entry:
                                v_cat = entry['category']
                                if not isinstance(v_cat, str):
                                    raise ValueError('manifest.category phải là chuỗi.')
                                v_cat_clean = v_cat.strip()[:200]
                                if v_cat_clean in tax or v_cat_clean in CATEGORIES:
                                    item['category'] = v_cat_clean
                                else:
                                    actual_rel = item['relative_path']
                                    if actual_rel not in manifest_suggestions:
                                        manifest_suggestions[actual_rel] = {}
                                    manifest_suggestions[actual_rel]['category'] = v_cat_clean
                                    warnings.append(f"Manifest gợi ý danh mục chưa đăng ký '{v_cat_clean}' cho '{actual_rel}'; giữ nguyên phân loại tự động '{item['category']}'.")

                            if 'template_type' in entry:
                                v_tt = entry['template_type']
                                if not isinstance(v_tt, str):
                                    raise ValueError('manifest.template_type phải là chuỗi.')
                                tt_lower = v_tt.strip().lower()
                                if tt_lower == 'title':
                                    tt_lower = 'text'
                                elif tt_lower == 'transition':
                                    tt_lower = 'trans'
                                if tt_lower in ('text', 'trans'):
                                    item['template_type'] = tt_lower
                                else:
                                    warnings.append(f"Manifest khai báo template_type không hợp lệ '{v_tt}' cho '{item['relative_path']}'; giữ nguyên '{item['template_type']}'.")

                        # Loại bỏ các file companion đã được master tham chiếu khỏi danh sách asset cards
                        if referenced_companions:
                            records = [r for r in records if r['relative_path'] not in referenced_companions]

        # Tự động dò tìm và liên kết video / ảnh minh họa (companion preview) cho các template (.setting, .drfx)
        # Giúp bảo toàn video minh họa chuyển cảnh cho các gói zip cộng đồng không có manifest.json
        if candidate_path is None:
            candidate_previews: dict[str, str] = {}
            candidate_thumbs: dict[str, str] = {}
            tutorial_videos: list[str] = []
            template_masters = [
                r for r in records
                if Path(r['relative_path']).suffix.lower() in {'.setting', '.drfx', '.comp'}
            ]
            for r in records:
                p_rel = r['relative_path']
                ext_l = Path(p_rel).suffix.lower()
                stem_l = Path(p_rel).stem.lower().strip()
                if ext_l in VIDEO:
                    candidate_previews[stem_l] = p_rel
                    if any(k in stem_l for k in ('tutorial', 'how-to-use', 'how_to_use', 'preview', 'demo')):
                        tutorial_videos.append(p_rel)
                elif ext_l in IMAGES:
                    if not re.search(r'_[0-9]{3}$', stem_l) and stem_l not in ('paper', 'texture', 'mask'):
                        candidate_thumbs[stem_l] = p_rel

            auto_matched_companions = set()
            for r in template_masters:
                stem_l = Path(r['relative_path']).stem.lower().strip()
                if not r.get('preview_relative_path'):
                    if stem_l in candidate_previews:
                        r['preview_relative_path'] = candidate_previews[stem_l]
                        auto_matched_companions.add(candidate_previews[stem_l])
                    else:
                        for c_stem, c_rel in candidate_previews.items():
                            if len(stem_l) >= 6 and len(c_stem) >= 6 and (stem_l in c_stem or c_stem in stem_l):
                                r['preview_relative_path'] = c_rel
                                auto_matched_companions.add(c_rel)
                                break
                        if not r.get('preview_relative_path') and len(template_masters) <= 3 and len(tutorial_videos) == 1:
                            r['preview_relative_path'] = tutorial_videos[0]
                            auto_matched_companions.add(tutorial_videos[0])

                if not r.get('thumbnail_relative_path'):
                    if stem_l in candidate_thumbs:
                        r['thumbnail_relative_path'] = candidate_thumbs[stem_l]
                        auto_matched_companions.add(candidate_thumbs[stem_l])
                    else:
                        for c_stem, c_rel in candidate_thumbs.items():
                            if len(stem_l) >= 6 and len(c_stem) >= 6 and (stem_l in c_stem or c_stem in stem_l):
                                r['thumbnail_relative_path'] = c_rel
                                auto_matched_companions.add(c_rel)
                                break

            if auto_matched_companions:
                # Loại bỏ các file companion đã được master tham chiếu để tránh lặp card
                records = [r for r in records if r['relative_path'] not in auto_matched_companions]

        # Kiểm tra gói trùng lặp theo cặp (name, version) trong registry của kho
        try:
            reg = self.storage.registry()
            clean_name = str(job_name).strip()
            clean_ver = str(job_version).strip()
            is_duplicate = False
            for p in (reg.get('packs', []) if isinstance(reg, dict) else []):
                if isinstance(p, dict):
                    p_name = str(p.get('name', '')).strip()
                    p_ver = str(p.get('version', '')).strip()
                    if p_name == clean_name and p_ver == clean_ver:
                        is_duplicate = True
                        break
            if is_duplicate:
                warnings.append(f"Kho đã có gói '{clean_name}' phiên bản '{clean_ver}'; tiếp tục nhập sẽ tạo bản sao độc lập song song.")
        except Exception:
            warnings.append("Chưa kiểm tra được gói trùng do không đọc được registry của kho.")

        return {
            'job_dir': str(directory),
            'pack_id': pack_id,
            'name': str(job_name),
            'version': str(job_version),
            'assets': records,
            'warnings': warnings,
            'manifest_suggestions': manifest_suggestions,
        }

    def commit(self, job, cancel=lambda: False, progress=lambda text: None):
        sm = self.storage
        drafts = validate_draft_taxonomy(job.get('draft_taxonomy', {}))
        sm.require_online()
        initial_root = sm.root
        initial_id = sm._config.get('library_id')

        def check_identity():
            if sm.root != initial_root or sm._config.get('library_id') != initial_id:
                raise StorageOfflineError('Danh tính kho đã thay đổi; không ghi hoặc khôi phục sang kho khác.')
            if initial_root.is_symlink() or sm.meta.is_symlink() or (sm.meta / 'library.json').is_symlink():
                raise StorageOfflineError('Đường dẫn kho không an toàn.')
            sm.require_online()

        directory = Path(job['job_dir'])
        if directory.parent != sm.meta / 'staging' or directory.is_symlink():
            raise ValueError('Staging không thuộc kho đang chọn.')
        payload = directory / 'payload'
        if not payload.is_dir() or payload.is_symlink():
            raise FileNotFoundError('Staging không tồn tại hoặc đã được nhập.')
        chosen = [dict(a) for a in job['assets'] if not a.get('excluded')]
        if not chosen:
            raise ValueError('Hãy chọn ít nhất1file để nhập.')
        seen = set()
        for item in chosen:
            rel = safe_relative(item['relative_path'])
            path = payload / rel
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(payload.resolve()):
                raise ValueError('File staging không hợp lệ.')
            if rel.as_posix().casefold() in seen:
                raise ValueError('Asset trùng trong manifest.')
            seen.add(rel.as_posix().casefold())
            item['tags'] = list(dict.fromkeys(str(t).strip()[:100] for t in item.get('tags', []) if str(t).strip()))[:50]
            item['size_bytes'] = path.stat().st_size

        # Preflight closure của companions được các chosen masters tham chiếu
        referenced_companions: set[str] = set()
        for item in chosen:
            if item.get('preview_relative_path'):
                referenced_companions.add(str(safe_relative(item['preview_relative_path'])))
            if item.get('thumbnail_relative_path'):
                referenced_companions.add(str(safe_relative(item['thumbnail_relative_path'])))

        for comp_rel in referenced_companions:
            comp_path = payload / comp_rel
            if not comp_path.is_file() or comp_path.is_symlink() or not comp_path.resolve().is_relative_to(payload.resolve()):
                raise ValueError(f"File companion không hợp lệ hoặc không tồn tại: '{comp_rel}'.")

        if cancel():
            raise InterruptedError('Đã hủy trước khi nhập kho.')
        slug = re.sub(r'[^\w.-]+', '_', job['name'], flags=re.UNICODE)[:60].strip('.') or 'Pack'
        version = re.sub(r'[^\w.-]+', '_', job['version'])[:24]
        folder = initial_root / '02_cloud_downloads/packs' / f'{slug}_{version}_{job["pack_id"]}'
        manifest = {'schema_version': 1, 'pack_id': job['pack_id'], 'name': job['name'], 'version': job['version'], 'assets': chosen}
        with sm.lock():
            check_identity()
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            tax_path = sm.meta / 'taxonomy.json'
            if drafts:
                if tax_path.is_symlink():
                    raise ValueError('Taxonomy là symlink không an toàn.')
                tax = read_json(tax_path, None)
                if not isinstance(tax, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tax.items()):
                    raise ValueError('Taxonomy bị lỗi; không ghi đè bằng danh mục mặc định.')
            else:
                tax = sm.taxonomy()
            merged_tax = dict(tax)
            for cid, label in drafts.items():
                if cid in tax and tax[cid] != label:
                    raise ValueError(f"Danh mục '{cid}' đã tồn tại với nhãn khác; hãy kiểm duyệt lại.")
                merged_tax.setdefault(cid, label)
            for item in chosen:
                if item['category'] not in merged_tax:
                    raise ValueError('Danh mục chưa đăng ký: ' + str(item['category']))

            registry = sm.registry()
            target_pid = str(job['pack_id']).strip().lower()
            registry['packs'] = [
                p for p in registry.get('packs', [])
                if str(p.get('id', p.get('pack_id', ''))).lower() != target_pid
            ]
            registry['assets'] = [
                a for a in registry.get('assets', [])
                if str(a.get('pack_id', '')).lower() != target_pid
            ]
            for item in chosen:
                item_up = {
                    'pack_id': job['pack_id'],
                    'pack_name': job['name'],
                    'library_path': (folder / item['relative_path']).relative_to(initial_root).as_posix()
                }
                if item.get('preview_relative_path'):
                    item_up['preview_relative_path'] = str(safe_relative(item['preview_relative_path']))
                if item.get('thumbnail_relative_path'):
                    item_up['thumbnail_relative_path'] = str(safe_relative(item['thumbnail_relative_path']))
                item.update(item_up)

            registry['packs'].append({'id': job['pack_id'], 'name': job['name'], 'version': job['version'], 'folder': folder.relative_to(initial_root).as_posix()})
            registry['assets'].extend(chosen)
            # Serialize trước mọi thay đổi để lỗi metadata không publish pack nửa chừng.
            reg_bytes = json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
            tax_bytes = json.dumps(merged_tax, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8') if drafts else b''
            if len(reg_bytes) > 15 * 1024**2:
                raise ValueError('Registry quá lớn; cần chia kho.')
            if len(manifest_bytes) > MAX_JSON or len(tax_bytes) > MAX_JSON:
                raise ValueError('Metadata vượt giới hạn 16 MiB.')
            sm.ensure_space(max(2 * 1024**2, len(reg_bytes) + len(manifest_bytes) + len(tax_bytes)))
            if cancel():
                raise InterruptedError('Đã hủy trước khi nhập kho.')
            atomic_json(payload / 'pack_manifest.json', manifest)
            # Loại file bị exclude khỏi payload trước commit, giữ các master đã chọn, companions VÀ tài nguyên nội bộ của macro (.setting / .drfx)
            keep = {safe_relative(a['relative_path']).as_posix() for a in chosen} | {safe_relative(c).as_posix() for c in referenced_companions} | {'pack_manifest.json'}
            chosen_setting_dirs = {
                (payload / safe_relative(a['relative_path'])).parent
                for a in chosen
                if Path(a['relative_path']).suffix.lower() == '.setting'
            }
            has_chosen_drfx = any(Path(a['relative_path']).suffix.lower() == '.drfx' for a in chosen)
            for path in payload.rglob('*'):
                if path.is_file():
                    rel = path.relative_to(payload)
                    if _is_macos_junk(rel):
                        path.unlink()
                        continue
                    p_posix = rel.as_posix()
                    rel_parts_lower = {pt.lower() for pt in rel.parts}
                    # Bảo toàn các file ảnh/texture con nằm cạnh .setting macro (như paper.jpg, Center/*.png, Rect/*.png)
                    is_macro_support = (
                        path.suffix.lower() in IMAGES
                        and not (rel_parts_lower & {'previews', 'thumbnails'})
                        and any(s_dir in path.parents for s_dir in chosen_setting_dirs)
                    )
                    # Bảo toàn kho preview đi kèm gói .drfx (như Preview Transitions/*.mp4, Help/How-To-Use.mp4)
                    is_drfx_preview = (
                        has_chosen_drfx
                        and path.suffix.lower() in (VIDEO | IMAGES)
                        and any(k in p_posix.lower() for k in ('preview', 'thumb', 'tutorial', 'how-to-use', 'help'))
                    )
                    if p_posix not in keep and not is_macro_support and not is_drfx_preview:
                        path.unlink()
            check_identity()
            renamed = False
            tax_written = False
            try:
                os.rename(payload, folder)
                renamed = True
                if merged_tax != tax:
                    check_identity()
                    atomic_json(tax_path, merged_tax)
                    tax_written = True
                check_identity()
                atomic_json(sm.meta / 'registry.json', registry)
            except Exception as primary:
                # Hai thao tác hoàn nguyên độc lập; lỗi một phía không che mất lỗi gốc.
                rollback_errors = []
                if tax_written:
                    try:
                        check_identity()
                        if tax_path.is_symlink() or read_json(tax_path, None) != merged_tax:
                            raise ValueError('Taxonomy đã thay đổi bên ngoài; không ghi đè khi hoàn nguyên.')
                        atomic_json(tax_path, tax)
                    except Exception as exc:
                        rollback_errors.append(f'taxonomy: {exc}')
                if renamed:
                    try:
                        check_identity()
                        os.rename(folder, payload)
                    except Exception as exc:
                        rollback_errors.append(f'folder: {exc}')
                if rollback_errors:
                    detail = '; '.join(rollback_errors)
                    logger.error('Nhập pack lỗi: %s; khôi phục chưa hoàn tất: %s', primary, detail)
                    raise RuntimeError(f'Nhập pack thất bại: {primary}. Chưa khôi phục hoàn toàn: {detail}') from primary
                raise

            snapshot_synced = True
            warnings = []
            try:
                sm.snapshot()
            except (OSError, ValueError, StorageOfflineError) as exc:
                logger.warning('Pack %s đã publish nhưng cập nhật snapshot thất bại: %s', job.get('pack_id'), exc)
                snapshot_synced = False
                warnings.append(f'Pack đã nhập vào kho nhưng bộ đệm offline chưa được đồng bộ: {exc}')
        shutil.rmtree(directory, ignore_errors=True)
        progress('Đã nhập pack và cập nhật chỉ mục.')
        try:
            from src.core.pack_manager import PackManager
            installed_paths = [str(folder / item['relative_path']) for item in chosen]
            PackManager.record_installed_pack(
                job.get('pack_id'),
                folder_name=folder.name,
                installed_paths=installed_paths
            )
        except Exception as exc:
            logger.warning("Không thể ghi nhận installed_pack trong commit: %s", exc)
        return {'pack_id': job['pack_id'], 'count': len(chosen),
                'snapshot_synced': snapshot_synced, 'warnings': warnings}

    def discard(self, job):
        self.storage.require_online()
        path = Path(job['job_dir'])
        if path.parent == self.storage.meta / 'staging' and not path.is_symlink(): shutil.rmtree(path, ignore_errors=True)

    def download(self, url, cancel=lambda: False, progress=lambda text: None, pack_id=None, pack_name=None):
        from src.core.cloud_downloader import GoogleDriveDownloader, parse_google_drive_url, TransferBudget, sanitize_filename
        info = parse_google_drive_url(url)
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Cần liên kết HTTP(S) hoặc Google Drive hợp lệ.')
        directory = self.storage.new_staging(); download = directory / 'download'; download.mkdir()
        budget = TransferBudget(MAX_PACK_STREAM_BYTES)
        def guard(size=0):
            if cancel(): raise InterruptedError('Đã hủy tải.')
            self.storage.ensure_space(size)
        try:
            if info is None:
                import urllib.request
                filename = Path(unquote(parsed.path)).name or 'download.zip'
                filename = re.sub(r'[<>:"/\\|?*]', '_', filename).strip('. ') or 'download.zip'
                target = download / filename
                guard()
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'ChunDVC/1.0'}), timeout=30) as response:
                    # 1. Xác thực HTTP status: Chỉ chấp nhận 200 int cho fresh full response
                    status = getattr(response, 'status', None)
                    if status is None and hasattr(response, 'code'):
                        status = response.code
                    if status is None and hasattr(response, 'getcode'):
                        status = response.getcode()
                    if status is None or type(status) is not int or status != 200:
                        raise ValueError(f"HTTP status không hợp lệ: {status}. Chỉ chấp nhận 200.")

                    # 2. Xác thực MIME Content-Type: Chặn text/html và application/xhtml+xml
                    content_type = (response.headers.get('Content-Type') or '').lower()
                    if 'text/html' in content_type or 'application/xhtml+xml' in content_type:
                        raise ValueError("Liên kết trả về trang web HTML thay vì tệp tài nguyên.")

                    # 3. Xác thực declared Content-Length
                    raw_len = response.headers.get('Content-Length')
                    has_declared_len = False
                    declared_len = 0
                    if raw_len is not None and str(raw_len).strip().isdigit():
                        declared_len = int(str(raw_len).strip())
                        has_declared_len = True
                        if declared_len > MAX_PACK_STREAM_BYTES:
                            raise ValueError(
                                f"Kích thước gói tải ({declared_len} bytes) vượt quá giới hạn an toàn {MAX_PACK_STREAM_BYTES} bytes."
                            )
                        guard(declared_len)
                    else:
                        guard(0)

                    # 4. Stream từng block kèm kiểm tra cap, cancel, ENOSPC và gom prefix buffer 512 bytes
                    written = 0
                    prefix_buf = bytearray()
                    PREFIX_LIMIT = 512
                    html_checked = False

                    def check_html_prefix(buf: bytes | bytearray) -> None:
                        header_bytes = bytes(buf[:PREFIX_LIMIT]).lstrip(b'\xef\xbb\xbf \t\r\n')
                        header_lower = header_bytes.decode('utf-8', errors='ignore').lower()
                        html_markers = (
                            '<!doctype html', '<html', '<head', '<body', '<title',
                            'quota exceeded', 'too many users',
                        )
                        if any(marker in header_lower for marker in html_markers):
                            raise ValueError("Nội dung tải về là trang web HTML hoặc thông báo lỗi, không phải tệp tài nguyên.")

                    with open(target, 'xb') as output:
                        while block := response.read(1024**2):
                            if cancel():
                                raise InterruptedError('Đã hủy tải.')
                            if written + len(block) > MAX_PACK_STREAM_BYTES:
                                raise ValueError(
                                    f"Dung lượng tải về vượt quá giới hạn cho phép {MAX_PACK_STREAM_BYTES} bytes."
                                )
                            guard(len(block))

                            if not html_checked:
                                prefix_buf.extend(block)
                                written += len(block)
                                if len(prefix_buf) >= PREFIX_LIMIT:
                                    check_html_prefix(prefix_buf)
                                    html_checked = True
                                    output.write(prefix_buf)
                                    prefix_buf.clear()
                                    progress(f'{filename}: {written/1024**2:.1f} MiB')
                            else:
                                output.write(block)
                                written += len(block)
                                progress(f'{filename}: {written/1024**2:.1f} MiB')

                        # Xử lý trường hợp tệp ngắn hơn PREFIX_LIMIT chạm EOF trước 512 bytes
                        if not html_checked:
                            if len(prefix_buf) == 0:
                                raise ValueError("Tệp tải về rỗng (0 bytes).")
                            check_html_prefix(prefix_buf)
                            html_checked = True
                            output.write(prefix_buf)
                            prefix_buf.clear()
                            progress(f'{filename}: {written/1024**2:.1f} MiB')

                    # 5. Xác thực sau khi stream xong
                    if written == 0:
                        raise ValueError("Tệp tải về rỗng (0 bytes).")
                    if has_declared_len and written != declared_len:
                        raise ValueError(f"Dữ liệu tải về không đầy đủ (đã tải {written}/{declared_len} bytes).")

                return self._inspect_downloaded(directory, target, cancel, progress, pack_id=pack_id, pack_name=pack_name)
            if info.resource_type == 'folder':
                if cancel(): raise InterruptedError('Đã hủy tải.')
                import gdown
                files = gdown.download_folder(id=info.resource_id, output=str(download), quiet=True, use_cookies=False, skip_download=True)
                if cancel(): raise InterruptedError('Đã hủy tải.')
                if not files or len(files) > MAX_FILES:
                    raise ValueError('Không đọc được folder Drive hoặc folder quá lớn.')

                seen_keys = set()
                validated_items = []
                download_root = download.resolve()

                for item in files:
                    if cancel(): raise InterruptedError('Đã hủy tải.')
                    local_p = str(item.local_path)
                    if os.path.isabs(local_p):
                        raw_path = Path(local_p)
                        if not raw_path.is_relative_to(download):
                            raise ValueError('Drive trả đường dẫn ngoài staging.')
                        rel = raw_path.relative_to(download)
                    else:
                        raw_path = Path(local_p)
                        if '..' in raw_path.parts or raw_path.is_absolute():
                            raise ValueError(f'Đường dẫn không an toàn: {local_p}')
                        rel = safe_relative(local_p)

                    if '..' in rel.parts:
                        raise ValueError(f'Đường dẫn không an toàn: {local_p}')

                    curr = download
                    for part in rel.parts:
                        curr = curr / part
                        if os.path.islink(curr) or curr.is_symlink():
                            raise ValueError(f'Thư mục staging chứa symlink: {rel}')

                    target = curr
                    if not target.resolve().is_relative_to(download_root):
                        raise ValueError('Drive trả đường dẫn ngoài staging.')

                    sanitized_name = sanitize_filename(rel.name)
                    if not sanitized_name:
                        raise ValueError(f'Tên tệp không hợp lệ: {rel.name}')

                    canonical_rel = rel.parent / sanitized_name
                    key = canonical_rel.as_posix().casefold()
                    if key in seen_keys:
                        raise ValueError(f'Thư mục Drive chứa tệp trùng tên hoặc xung đột hoa/thường: {canonical_rel}')
                    seen_keys.add(key)

                    final_target = target.parent / sanitized_name
                    validated_items.append((item.id, final_target))

                for file_id, target in validated_items:
                    if cancel(): raise InterruptedError('Đã hủy tải.')
                    guard()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    GoogleDriveDownloader.download_file(
                        file_id, str(target.parent), custom_filename=target.name,
                        cancel_checker=cancel,
                        progress_callback=lambda p: progress(f'{p.filename}: {p.percent:.0f}%'),
                        write_guard=guard, budget=budget,
                    )

                (directory / 'payload').mkdir()
                # Thư mục tải đã nằm trên cùng volume: không copy thêm footage.
                shutil.rmtree(directory / 'payload'); os.rename(download, directory / 'payload')
                return self.inspect_job(directory, pack_name or 'Google Drive', cancel, pack_id=pack_id)
            path = GoogleDriveDownloader.download_file(info.resource_id, str(download), cancel_checker=cancel,
                progress_callback=lambda p: progress(f'{p.filename}: {p.percent:.0f}%'), write_guard=guard, budget=budget)
            return self._inspect_downloaded(directory, Path(path), cancel, progress, pack_id=pack_id, pack_name=pack_name)
        except Exception:
            if self.storage.online: shutil.rmtree(directory, ignore_errors=True)
            raise

    def _inspect_downloaded(self, directory, path, cancel, progress, pack_id=None, pack_name=None):
        payload = directory / 'payload'; payload.mkdir()
        if zipfile.is_zipfile(path):
            self._extract(path, payload, cancel, progress)
            path.unlink()
        else:
            self.storage.require_online()
            os.rename(path, payload / path.name)
        return self.inspect_job(directory, pack_name or path.stem, cancel, pack_id=pack_id)
