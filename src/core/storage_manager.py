"""Kho ChunDVC: cấu hình JSON, ổ offline, liên kết và di chuyển có xác minh."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Callable

CATEGORIES = {
    'broll': 'Cảnh minh họa', 'sfx': 'Âm thanh', 'bgm': 'Nhạc nền',
    'overlays': 'Lớp phủ', 'memes': 'Meme', 'luts': 'Màu LUT', 'templates': 'Mẫu Fusion',
}
MAX_JSON = 16 * 1024 * 1024


class StorageState(str, Enum):
    ONLINE = 'online'
    READ_ONLY = 'read_only'
    FULL = 'full'
    OFFLINE = 'offline'
    ERROR = 'error'


class StorageOfflineError(OSError):
    """Kho chưa cấu hình hoặc thiết bị đang offline; không ghi fallback."""


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    if path.stat().st_size > MAX_JSON:
        raise ValueError(f'Metadata quá lớn: {path.name}')
    return json.loads(path.read_text(encoding='utf-8'))


def _strict_read_json(path: Path) -> dict:
    """Đọc tệp metadata JSON strictly: không default khi thiếu, chặn trần MAX_JSON, ném lỗi thật."""
    try:
        st = path.stat()
    except FileNotFoundError as e:
        raise StorageOfflineError(f"Không tìm thấy tệp metadata bắt buộc: {path.name}") from e
    except PermissionError as e:
        raise PermissionError(f"Không có quyền truy cập tệp metadata: {path.name}") from e
    except OSError as e:
        raise OSError(f"Lỗi truy cập tệp metadata: {path.name} ({e})") from e

    if st.st_size > MAX_JSON:
        raise ValueError(f"Metadata quá lớn ({st.st_size} > {MAX_JSON}): {path.name}")

    try:
        with open(path, "rb") as f:
            data = f.read(MAX_JSON + 1)
    except FileNotFoundError as e:
        raise StorageOfflineError(f"Không tìm thấy tệp metadata bắt buộc: {path.name}") from e
    except PermissionError as e:
        raise PermissionError(f"Không có quyền truy cập tệp metadata: {path.name}") from e
    except OSError as e:
        raise OSError(f"Lỗi truy cập tệp metadata: {path.name} ({e})") from e

    if len(data) > MAX_JSON:
        raise ValueError(f"Metadata quá lớn ({len(data)} > {MAX_JSON}): {path.name}")

    try:
        res = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Metadata JSON bị hỏng hoặc không đúng chuẩn UTF-8: {path.name} ({e})") from e

    if not isinstance(res, dict):
        raise ValueError(f"Metadata JSON không phải là dict envelope: {path.name}")
    return res


def atomic_json(path: Path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(data) > MAX_JSON:
        raise ValueError('Metadata vượt giới hạn 16 MiB; cần chia nhỏ pack.')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def safe_relative(value: str) -> Path:
    from pathlib import PurePosixPath, PureWindowsPath
    value = str(value).replace('\\', '/')
    p = PurePosixPath(value)
    if not value or p.is_absolute() or PureWindowsPath(value).drive or '..' in p.parts:
        raise ValueError(f'Đường dẫn không an toàn: {value}')
    return Path(*p.parts)


class StorageManager:
    def __init__(self, config_dir=None, reserve_bytes=None):
        if config_dir is None:
            base = os.environ.get('XDG_DATA_HOME')
            if os.name == 'nt': base = os.environ.get('LOCALAPPDATA')
            config_dir = Path(base or Path.home() / '.local' / 'share') / 'ChunDVC' / 'storage'
        self.config_dir = Path(config_dir)
        self.config_file = self.config_dir / 'storage.json'
        try:
            self._config = read_json(self.config_file, {})
            if not isinstance(self._config, dict): self._config = {}
        except (OSError, ValueError): self._config = {}
        self.reserve_bytes = int(reserve_bytes if reserve_bytes is not None else self._config.get('reserve_bytes', 256 * 1024**2))

    CORE_MANAGED_DIRS = (
        '.chundvc_meta',
        '.chundvc_meta/staging',
        '01_core',
        '02_cloud_downloads',
        '02_cloud_downloads/packs',
        '03_local_linked',
    )

    @property
    def root(self) -> Path:
        value = self._config.get('root')
        if not value: raise StorageOfflineError('Chưa chọn Thư mục Kho Chính.')
        return Path(value)

    @property
    def meta(self): return self.root / '.chundvc_meta'

    @property
    def online(self) -> bool:
        try:
            marker = read_json(self.meta / 'library.json', {})
            return bool(self._config.get('library_id') and marker.get('library_id') == self._config['library_id'])
        except (OSError, ValueError, TypeError, AttributeError): return False

    def require_online(self):
        if not self.online: raise StorageOfflineError('Kho Offline — hãy kết nối lại đúng ổ đĩa. Không tải về ổ hệ thống.')
        return self.root

    def get_state(self) -> StorageState:
        """
        Xác định trạng thái vận hành của kho media theo thứ tự ưu tiên:
        1. Identity / Safety / Errors -> OFFLINE hoặc ERROR
        2. Permissions Hint -> READ_ONLY
        3. Capacity -> FULL hoặc ONLINE
        """
        # 1. Kiểm tra cấu hình root
        if 'root' not in self._config or self._config['root'] is None or self._config['root'] == '':
            return StorageState.OFFLINE
        raw_root = self._config['root']
        if not isinstance(raw_root, str) or isinstance(raw_root, bool):
            return StorageState.ERROR

        try:
            root_path = Path(raw_root)
        except Exception:
            return StorageState.ERROR

        try:
            root_st = root_path.lstat()
        except FileNotFoundError:
            return StorageState.OFFLINE
        except PermissionError:
            return StorageState.ERROR
        except OSError:
            return StorageState.ERROR

        if stat.S_ISLNK(root_st.st_mode):
            return StorageState.ERROR
        if not stat.S_ISDIR(root_st.st_mode):
            return StorageState.ERROR

        # Kiểm tra các thư mục core managed
        for rel in self.CORE_MANAGED_DIRS:
            sub = root_path / rel
            try:
                sub_st = sub.lstat()
                if stat.S_ISLNK(sub_st.st_mode):
                    return StorageState.ERROR
                if not stat.S_ISDIR(sub_st.st_mode):
                    return StorageState.ERROR
            except FileNotFoundError:
                continue
            except PermissionError:
                return StorageState.ERROR
            except OSError:
                return StorageState.ERROR

        # Kiểm tra marker library.json
        marker_path = root_path / '.chundvc_meta' / 'library.json'
        try:
            marker_st = marker_path.lstat()
        except FileNotFoundError:
            return StorageState.OFFLINE
        except PermissionError:
            return StorageState.ERROR
        except OSError:
            return StorageState.ERROR

        if stat.S_ISLNK(marker_st.st_mode) or not stat.S_ISREG(marker_st.st_mode):
            return StorageState.ERROR

        # Giới hạn kích thước đọc marker (MAX_JSON)
        if marker_st.st_size > MAX_JSON:
            return StorageState.ERROR

        try:
            with open(marker_path, 'rb') as f:
                data = f.read(MAX_JSON + 1)
            if len(data) > MAX_JSON:
                return StorageState.ERROR
            marker = json.loads(data.decode('utf-8'))
        except PermissionError:
            return StorageState.ERROR
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return StorageState.ERROR
        except OSError:
            return StorageState.ERROR

        if not isinstance(marker, dict):
            return StorageState.ERROR

        expected_id = self._config.get('library_id')
        marker_id = marker.get('library_id')
        if not expected_id or not marker_id or marker_id != expected_id:
            return StorageState.OFFLINE

        # 2. Permissions Hint (os.access W_OK)
        try:
            if not os.access(root_path, os.W_OK):
                return StorageState.READ_ONLY
            for rel in self.CORE_MANAGED_DIRS:
                sub = root_path / rel
                if sub.exists() and not os.access(sub, os.W_OK):
                    return StorageState.READ_ONLY
        except Exception:
            return StorageState.ERROR

        # 3. Capacity (shutil.disk_usage)
        try:
            usage = shutil.disk_usage(root_path)
        except FileNotFoundError:
            return StorageState.OFFLINE
        except OSError:
            return StorageState.ERROR
        except Exception:
            return StorageState.ERROR

        if usage.free < self.reserve_bytes:
            return StorageState.FULL

        return StorageState.ONLINE

    def ensure_space(self, size=0):
        # 1. Structural safety checks TRƯỚC require_online đọc marker
        if 'root' not in self._config or self._config['root'] is None or self._config['root'] == '':
            raise StorageOfflineError('Chưa chọn Thư mục Kho Chính.')
        raw_root = self._config['root']
        if not isinstance(raw_root, str) or isinstance(raw_root, bool):
            raise StorageOfflineError('Cấu hình đường dẫn kho không hợp lệ.')

        root_path = Path(raw_root)
        try:
            root_st = root_path.lstat()
        except FileNotFoundError:
            raise StorageOfflineError('Kho Offline — hãy kết nối lại đúng ổ đĩa. Không tải về ổ hệ thống.')
        except OSError as exc:
            raise StorageOfflineError(f'Lỗi kiểm tra đường dẫn kho: {exc}')

        if stat.S_ISLNK(root_st.st_mode):
            raise StorageOfflineError('Kho có đường dẫn symlink không an toàn.')
        if not stat.S_ISDIR(root_st.st_mode):
            raise StorageOfflineError('Đường dẫn kho không phải là thư mục.')

        # Kiểm tra các thư mục core managed TRƯỚC marker để chặn symlink .chundvc_meta trước khi truy vấn file con
        for rel in self.CORE_MANAGED_DIRS:
            sub = root_path / rel
            try:
                sub_st = sub.lstat()
                if stat.S_ISLNK(sub_st.st_mode):
                    raise StorageOfflineError('Kho có đường dẫn symlink không an toàn.')
                if not stat.S_ISDIR(sub_st.st_mode):
                    raise StorageOfflineError('Thư mục kho không hợp lệ.')
            except FileNotFoundError:
                continue
            except StorageOfflineError:
                raise
            except OSError as exc:
                raise StorageOfflineError(f'Lỗi kiểm tra thư mục kho {rel}: {exc}') from exc

        # Kiểm tra marker symlink trước khi require_online đọc nó
        marker_path = root_path / '.chundvc_meta' / 'library.json'
        try:
            marker_st = marker_path.lstat()
            if stat.S_ISLNK(marker_st.st_mode):
                raise StorageOfflineError('Kho có tệp marker symlink không an toàn.')
            if not stat.S_ISREG(marker_st.st_mode):
                raise StorageOfflineError('Tệp marker kho không hợp lệ.')
        except FileNotFoundError:
            pass  # require_online sẽ ném StorageOfflineError
        except StorageOfflineError:
            raise
        except OSError as exc:
            raise StorageOfflineError(f'Lỗi kiểm tra marker kho: {exc}') from exc

        # 2. Kiểm tra identity legacy
        self.require_online()

        # 3. Kiểm tra quyền ghi (READ_ONLY guard)
        if not os.access(self.root, os.W_OK):
            err = PermissionError('Kho đang ở chế độ chỉ đọc, không thể ghi thêm dữ liệu.')
            err.errno = errno.EACCES
            raise err
        for rel in self.CORE_MANAGED_DIRS:
            sub = self.root / rel
            if sub.exists() and not os.access(sub, os.W_OK):
                err = PermissionError('Kho đang ở chế độ chỉ đọc, không thể ghi thêm dữ liệu.')
                err.errno = errno.EACCES
                raise err

        # 4. Kiểm tra dung lượng (ENOSPC guard)
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as exc:
            raise OSError(f'Không thể kiểm tra dung lượng kho: {exc}') from exc

        if free < int(size) + self.reserve_bytes:
            err = OSError(errno.ENOSPC, 'Không đủ dung lượng kho (cần giữ vùng trống dự phòng).')
            raise err

    def configure(self, root):
        """Chỉ gọi từ thao tác chọn kho rõ ràng của người dùng."""
        root = Path(root).expanduser().absolute()
        if root.is_symlink(): raise ValueError('Chọn thư mục thật thay vì symlink cho Storage Root.')
        root = root.resolve()
        for rel in ('.chundvc_meta', '.chundvc_meta/staging', '01_core', '02_cloud_downloads', '02_cloud_downloads/packs', '03_local_linked'):
            if (root / rel).is_symlink(): raise ValueError('Thư mục kho có symlink không an toàn.')
        meta = root / '.chundvc_meta'
        marker = read_json(meta / 'library.json', {})
        library_id = marker.get('library_id') or uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < self.reserve_bytes:
            raise OSError('Ổ đĩa không đủ dung lượng dự phòng.')
        for rel in ('.chundvc_meta/staging', '.chundvc_meta/thumbnails', '01_core', '02_cloud_downloads/packs', '03_local_linked'):
            (root / rel).mkdir(parents=True, exist_ok=True)
        atomic_json(meta / 'library.json', {'schema_version': 1, 'library_id': library_id})
        if not (meta / 'registry.json').exists(): atomic_json(meta / 'registry.json', {'schema_version': 1, 'packs': [], 'assets': []})
        if not (meta / 'taxonomy.json').exists(): atomic_json(meta / 'taxonomy.json', CATEGORIES)
        if not (root / '03_local_linked/links.json').exists(): atomic_json(root / '03_local_linked/links.json', [])
        self._config = {'root': str(root), 'library_id': library_id, 'reserve_bytes': self.reserve_bytes}
        atomic_json(self.config_file, self._config)
        self.snapshot()

    @contextmanager
    def lock(self):
        from PySide6.QtCore import QLockFile
        self.require_online()
        lock = QLockFile(str(self.meta / 'registry.lock'))
        if not lock.tryLock(1000): raise OSError('Kho đang cập nhật ở tác vụ khác. Hãy thử lại.')
        try: yield
        finally: lock.unlock()

    def registry(self):
        self.require_online()
        data = read_json(self.meta / 'registry.json', {'packs': [], 'assets': []})
        if not isinstance(data, dict) or not isinstance(data.get('assets'), list) or not isinstance(data.get('packs'), list): raise ValueError('Registry bị lỗi; cần khôi phục metadata.')
        return data

    def _snapshot_path(self): return self.config_dir / ('snapshot-' + str(self._config.get('library_id', 'none')) + '.json')

    def snapshot(self):
        data = self.registry()
        origin_lib_id = self._config.get('library_id')
        try:
            origin_root = str(self.root)
        except OSError:
            origin_root = None
        envelope = {
            'registry': data,
            'taxonomy': self.taxonomy(),
            'links': self.links(),
            'origin_library_id': origin_lib_id,
            'origin_root': origin_root,
        }
        atomic_json(self._snapshot_path(), envelope)

    def taxonomy(self):
        try:
            value = read_json(self.meta / 'taxonomy.json', dict(CATEGORIES)) if self.online else read_json(self._snapshot_path(), {}).get('taxonomy', dict(CATEGORIES))
            return value if isinstance(value, dict) else dict(CATEGORIES)
        except (OSError, ValueError, AttributeError): return dict(CATEGORIES)

    def add_category(self, category_id, label):
        import re
        if category_id in {'all','fav','recent','mine','hub','text','trans','lut','meme','overlay','icon'}: raise ValueError('ID dành riêng cho giao diện; chọn ID khác.')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', category_id): raise ValueError('ID danh mục: chữ thường, số, dấu _, tối đa48ký tự.')
        with self.lock():
            tax = self.taxonomy(); tax[category_id] = str(label).strip()[:100] or category_id
            atomic_json(self.meta / 'taxonomy.json', tax); self.snapshot()

    def remove_category(self, category_id: str) -> bool:
        """Xóa danh mục khỏi taxonomy và kho lưu trữ."""
        if category_id in {'all','fav','recent','mine','hub','text','trans','lut','meme','overlay','icon'}:
            raise ValueError('Không thể xóa danh mục mặc định của hệ thống.')
        if not self.online:
            return False
        with self.lock():
            tax = self.taxonomy()
            if category_id in tax:
                del tax[category_id]
                atomic_json(self.meta / 'taxonomy.json', tax)
                self.snapshot()
                return True
        return False

    def rename_category(self, category_id: str, new_label: str) -> bool:
        """Đổi tên nhãn hiển thị của danh mục trong taxonomy."""
        if not self.online:
            return False
        with self.lock():
            tax = self.taxonomy()
            tax[category_id] = str(new_label).strip()[:100] or category_id
            atomic_json(self.meta / 'taxonomy.json', tax)
            self.snapshot()
            return True

    def remove_pack(self, pack_id: str) -> bool:
        """Xóa hoàn toàn một pack khỏi kho lưu trữ, dọn dẹp các tệp media và cập nhật registry."""
        if not self.online:
            return False
        with self.lock():
            reg = self.registry()
            packs = reg.get('packs', [])
            assets = reg.get('assets', [])

            target = str(pack_id).strip().lower()
            matching_packs = [
                p for p in packs
                if str(p.get('id', '')).lower() == target
                or str(p.get('pack_id', '')).lower() == target
                or str(p.get('name', '')).lower() == target
                or target in str(p.get('id', '')).lower()
                or target in str(p.get('pack_id', '')).lower()
            ]

            kept_assets = []
            for a in assets:
                a_pid = str(a.get('pack_id', '')).lower()
                if a_pid == target or any(a_pid == str(p.get('id', p.get('pack_id', ''))).lower() for p in matching_packs):
                    p_str = a.get('path') or a.get('library_path')
                    if p_str:
                        try:
                            p_path = Path(p_str)
                            if not p_path.is_absolute():
                                p_path = self.root / p_path
                            if p_path.is_file() and str(self.root) in str(p_path):
                                p_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                else:
                    kept_assets.append(a)

            # Xóa các thư mục pack tương ứng trên đĩa
            packs_root = self.root / '02_cloud_downloads' / 'packs'
            candidate_dirs = [
                packs_root / pack_id,
            ]
            for p in matching_packs:
                fld = p.get('folder') or p.get('folder_name') or p.get('id') or p.get('pack_id')
                if fld:
                    candidate_dirs.append(self.root / fld if not Path(fld).is_absolute() else Path(fld))
                    candidate_dirs.append(packs_root / Path(fld).name)

            # Quét thêm mọi thư mục con trong packs có tên chứa hoặc kết thúc bằng target
            if packs_root.is_dir():
                for sub in packs_root.iterdir():
                    if sub.is_dir() and (sub.name.lower().endswith(f"_{target}") or target in sub.name.lower()):
                        candidate_dirs.append(sub)

            for p_path in candidate_dirs:
                if p_path.exists() and p_path.is_dir():
                    shutil.rmtree(p_path, ignore_errors=True)

            reg['packs'] = [
                p for p in packs
                if str(p.get('id', p.get('pack_id', ''))).lower() != target
                and p not in matching_packs
            ]
            reg['assets'] = kept_assets
            atomic_json(self.meta / 'registry.json', reg)
            self.snapshot()
            return True

    def _build_runtime_assets_list(
        self,
        raw_assets: list,
        packs_map: dict,
        effective_root: Path,
        online: bool,
        origin_lib_id,
        origin_root,
        *,
        strict: bool = False,
    ) -> list[dict]:
        from src.core.contracts.asset_dto import is_absolute_lexical_path, is_safe_relative_path

        resolved_effective_root: Path | None = None
        if online:
            try:
                resolved_effective_root = effective_root.resolve()
            except (ValueError, OSError):
                resolved_effective_root = None

        # Local per-call caches to eliminate thousands of redundant stat/realpath syscalls
        pack_info_cache: dict[str, tuple[Path | None, Path | None, Path | None, bool]] = {}
        companion_cache: dict[tuple[str, str], str | None] = {}
        file_exists_cache: dict[str, bool] = {}

        def _get_pack_info(pack_folder_rel: str):
            cached = pack_info_cache.get(pack_folder_rel)
            if cached is not None:
                return cached
            if not isinstance(pack_folder_rel, str) or not is_safe_relative_path(pack_folder_rel):
                res = (None, None, None, False)
                pack_info_cache[pack_folder_rel] = res
                return res
            folder_rel = safe_relative(pack_folder_rel)
            pack_root = effective_root / folder_rel
            resolved_pack_root = None
            pack_contained = False
            if online and resolved_effective_root is not None:
                try:
                    if pack_root.is_dir():
                        resolved_pack_root = pack_root.resolve()
                        pack_contained = (
                            resolved_pack_root.is_dir()
                            and resolved_pack_root.is_relative_to(resolved_effective_root)
                        )
                except (ValueError, OSError):
                    pack_contained = False
            res = (folder_rel, pack_root, resolved_pack_root, pack_contained)
            pack_info_cache[pack_folder_rel] = res
            return res

        def _resolve_companion(pack_folder_rel: str, pack_root: Path, resolved_pack_root: Path | None, rel_str: str) -> str | None:
            cache_key = (pack_folder_rel, rel_str)
            if cache_key in companion_cache:
                return companion_cache[cache_key]
            if not isinstance(rel_str, str) or not is_safe_relative_path(rel_str):
                companion_cache[cache_key] = None
                return None
            rel_path = safe_relative(rel_str)
            full_path = pack_root / rel_path
            resolved_val: str | None = None
            if online:
                if resolved_pack_root is not None:
                    try:
                        if full_path.is_relative_to(pack_root) and full_path.is_file():
                            resolved_full = full_path.resolve()
                            if resolved_full.is_file() and resolved_full.is_relative_to(resolved_pack_root):
                                resolved_val = str(full_path)
                    except (ValueError, OSError):
                        resolved_val = None
            else:
                try:
                    if full_path.is_relative_to(pack_root):
                        resolved_val = str(full_path)
                except (ValueError, OSError):
                    resolved_val = None
            companion_cache[cache_key] = resolved_val
            return resolved_val

        result = []
        for asset in raw_assets:
            if strict and not isinstance(asset, dict):
                raise ValueError("Mục asset trong registry/snapshot không phải là dict.")
            item = dict(asset)
            path = Path(item['external_path']) if item.get('external_path') else effective_root / safe_relative(item['library_path'])
            file_path_str = str(path)
            item['file_path'] = file_path_str
            if online:
                is_online_file = file_exists_cache.get(file_path_str)
                if is_online_file is None:
                    is_online_file = path.is_file()
                    file_exists_cache[file_path_str] = is_online_file
                item['availability'] = 'online' if is_online_file else 'offline'
            else:
                item['availability'] = 'offline'
            item['origin_library_id'] = origin_lib_id
            item['origin_root'] = origin_root

            # Luôn xóa bỏ runtime companion paths cũ để không bị leak khi validation thất bại
            item.pop('preview_path', None)
            item.pop('thumbnail_path', None)

            # Dựng runtime companion paths từ pack folder thuộc quyền sở hữu của registry
            p_id = item.get('pack_id')
            pack_folder_rel = packs_map.get(p_id)
            lib_p = item.get('library_path')

            if (
                pack_folder_rel
                and lib_p
                and isinstance(pack_folder_rel, str)
                and isinstance(lib_p, str)
                and is_safe_relative_path(lib_p)
            ):
                folder_rel, pack_root, resolved_pack_root, pack_contained = _get_pack_info(pack_folder_rel)
                if folder_rel is not None and pack_root is not None:
                    is_valid_pack_master = False
                    try:
                        lib_p_rel = safe_relative(lib_p)
                        if lib_p_rel.is_relative_to(folder_rel):
                            is_valid_pack_master = True
                    except (ValueError, OSError):
                        is_valid_pack_master = False

                    if is_valid_pack_master and (pack_contained if online else True):
                        p_rel_str = item.get('preview_relative_path')
                        if p_rel_str:
                            prev_val = _resolve_companion(pack_folder_rel, pack_root, resolved_pack_root, p_rel_str)
                            if prev_val is not None:
                                item['preview_path'] = prev_val

                        t_rel_str = item.get('thumbnail_relative_path')
                        if t_rel_str:
                            thumb_val = _resolve_companion(pack_folder_rel, pack_root, resolved_pack_root, t_rel_str)
                            if thumb_val is not None:
                                item['thumbnail_path'] = thumb_val

            # Suy luận source_kind runtime khi thiếu trường:
            if not item.get('source_kind'):
                ext_p = item.get('external_path')
                if ext_p and isinstance(ext_p, str) and ext_p.strip():
                    if is_absolute_lexical_path(ext_p):
                        item['source_kind'] = 'linked'
                elif lib_p and isinstance(lib_p, str) and lib_p.strip():
                    if is_safe_relative_path(lib_p):
                        item['source_kind'] = 'managed'

            result.append(item)
        return result

    def assets(self, *, raise_on_error: bool = False):
        if type(raise_on_error) is not bool:
            raise ValueError("raise_on_error bắt buộc phải là kiểu bool thuần túy.")

        if not raise_on_error:
            try:
                online = self.online
                if online:
                    data = self.registry()
                    origin_lib_id = self._config.get('library_id')
                    try:
                        origin_root = str(self.root)
                    except OSError:
                        origin_root = None
                else:
                    snap = read_json(self._snapshot_path(), {})
                    data = snap.get('registry', {})
                    origin_lib_id = snap.get('origin_library_id')
                    origin_root = snap.get('origin_root')

                effective_root = self.root if online else (Path(origin_root) if origin_root else self.root)
                packs_map = {
                    p['id']: p.get('folder')
                    for p in data.get('packs', [])
                    if isinstance(p, dict) and 'id' in p and p.get('folder')
                }
                return self._build_runtime_assets_list(
                    data.get('assets', []),
                    packs_map,
                    effective_root,
                    online,
                    origin_lib_id,
                    origin_root,
                    strict=False,
                )
            except (OSError, ValueError, KeyError, TypeError):
                return []

        # =========================================================================
        # Strict opt-in reading branch: ném ngoại lệ thật, không default []
        # =========================================================================
        state = self.get_state()
        if state == StorageState.ERROR:
            raise OSError("Kho media đang ở trạng thái lỗi cấu hình hoặc quyền truy cập (StorageState.ERROR).")

        expected_lib_id = self._config.get('library_id')
        if not expected_lib_id or not self._config.get('root'):
            raise StorageOfflineError("Kho media chưa được cấu hình hợp lệ (thiếu root hoặc library_id).")

        if state in (StorageState.ONLINE, StorageState.READ_ONLY, StorageState.FULL):
            online = True
            origin_lib_id = expected_lib_id
            origin_root = str(self.root)
            effective_root = self.root
            registry_file = self.meta / 'registry.json'
            data = _strict_read_json(registry_file)
            if not isinstance(data.get('packs'), list) or not isinstance(data.get('assets'), list):
                raise ValueError("Registry bị lỗi cấu trúc envelope (thiếu packs hoặc assets list).")
        elif state == StorageState.OFFLINE:
            online = False
            snap_path = self._snapshot_path()
            snap = _strict_read_json(snap_path)
            origin_lib_id = snap.get('origin_library_id')
            if not origin_lib_id or origin_lib_id != expected_lib_id:
                raise StorageOfflineError(
                    f"Snapshot origin_library_id ('{origin_lib_id}') không khớp cấu hình kho ('{expected_lib_id}')."
                )
            origin_root = snap.get('origin_root')
            effective_root = Path(origin_root) if origin_root else self.root
            data = snap.get('registry')
            if not isinstance(data, dict) or not isinstance(data.get('packs'), list) or not isinstance(data.get('assets'), list):
                raise ValueError("Snapshot chứa registry bị lỗi cấu trúc envelope.")
        else:
            raise StorageOfflineError(f"Trạng thái kho không hỗ trợ đọc tài nguyên: {state}")

        packs_map = {
            p['id']: p.get('folder')
            for p in data.get('packs', [])
            if isinstance(p, dict) and 'id' in p and p.get('folder')
        }
        return self._build_runtime_assets_list(
            data.get('assets', []),
            packs_map,
            effective_root,
            online,
            origin_lib_id,
            origin_root,
            strict=True,
        )

    def ui_assets(self):
        from src.core.contracts.asset_dto import to_ui_asset
        return [to_ui_asset(asset) for asset in self.assets()]

    def new_staging(self):
        self.ensure_space()
        path = self.meta / 'staging' / uuid.uuid4().hex
        path.mkdir()  # parent chỉ được tạo khi cấu hình kho, không fallback
        return path

    def copy_file(self, source, destination, cancel=lambda: False, progress=lambda text: None):
        self.ensure_space(Path(source).stat().st_size)
        digest = hashlib.sha256()
        with open(source, 'rb') as src, open(destination, 'xb') as dst:
            while block := src.read(1024**2):
                if cancel(): raise InterruptedError('Đã hủy; nguồn được giữ nguyên.')
                self.ensure_space(len(block)); dst.write(block); digest.update(block)
            dst.flush(); os.fsync(dst.fileno())
        verify = hashlib.sha256()
        with open(destination, 'rb') as dst:
            while block := dst.read(1024**2): verify.update(block)
        if verify.digest() != digest.digest(): raise OSError('Checksum bản sao không khớp.')
        progress(str(source))
        return digest.hexdigest()

    def links(self):
        try:
            value = read_json(self.root / '03_local_linked/links.json', []) if self.online else read_json(self._snapshot_path(), {}).get('links', [])
            return value if isinstance(value, list) else []
        except (OSError, ValueError, AttributeError): return []

    def unlink_folder(self, source_id):
        """Chỉ xóa đăng ký/metadata, tuyệt đối không xóa file nguồn."""
        with self.lock():
            registry = self.registry()
            registry['assets'] = [a for a in registry['assets'] if a.get('source_id') != source_id]
            links = [l for l in self.links() if l['id'] != source_id]
            atomic_json(self.root / '03_local_linked/links.json', links)
            atomic_json(self.meta / 'registry.json', registry)
            self.snapshot()

    def link_folder(self, source, category='broll', cancel=lambda: False):
        source = Path(source).expanduser().resolve()
        if not source.is_dir(): raise FileNotFoundError('Thư mục liên kết chưa kết nối.')
        if category not in self.taxonomy(): raise ValueError('Danh mục chưa đăng ký.')
        from src.core.pack_ingest import inspect_tree
        with self.lock():
            links_path = self.root / '03_local_linked/links.json'
            links = read_json(links_path, [])
            source_id = uuid.uuid5(uuid.NAMESPACE_URL, str(source)).hex
            records = inspect_tree(source, source_id, cancel)
            registry = self.registry()
            for item in records:
                item.update(category=category, external_path=str(source / safe_relative(item['relative_path'])), source_id=source_id)
            registry['assets'] = [a for a in registry['assets'] if a.get('source_id') != source_id] + [a for a in records if not a['excluded']]
            links = [l for l in links if l['id'] != source_id] + [{'id': source_id, 'path': str(source), 'category': category}]
            atomic_json(links_path, links); atomic_json(self.meta / 'registry.json', registry); self.snapshot()

    def migrate(self, destination, legacy_source=None, cancel=lambda: False, progress=lambda text: None, delete_source=False):
        """Copy/verify rồi switch. Không xóa nguồn legacy, symlink hoặc project."""
        target = Path(destination).expanduser().resolve()
        source = Path(legacy_source).resolve() if legacy_source else self.require_online().resolve()
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError('Kho đích không được trùng/nằm trong hoặc bao ngoài kho nguồn.')
        if target.exists() and any(target.iterdir()): raise ValueError('Chọn thư mục đích trống để tránh ghi đè dữ liệu.')
        candidate = StorageManager(self.config_dir, self.reserve_bytes)
        # Khởi tạo đích bằng config tạm trong RAM, không đổi cấu hình đang hoạt động.
        old_config = read_json(self.config_file, {})
        switched = False
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temp_config = Path(tempfile.mkdtemp(prefix='migration-', dir=self.config_dir))
        candidate.config_dir = temp_config; candidate.config_file = temp_config / 'storage.json'
        try:
            candidate.configure(target)
            destination_base = target / '01_core' / 'legacy' if legacy_source else target
            files = [p for p in source.rglob('*') if p.is_file() and not p.is_symlink()]
            total = sum(p.stat().st_size for p in files)
            candidate.ensure_space(total)
            for path in files:
                if cancel(): raise InterruptedError('Đã hủy di chuyển; kho cũ được giữ nguyên.')
                rel = path.relative_to(source)
                if not legacy_source and rel.as_posix() in ('.chundvc_meta/library.json', '.chundvc_meta/registry.lock'): continue
                dest = destination_base / rel
                if dest.exists(): dest.unlink()  # chỉ metadata mới khởi tạo ở đích trống
                dest.parent.mkdir(parents=True, exist_ok=True)
                candidate.copy_file(path, dest, cancel, progress)
            if legacy_source:
                from src.core.pack_ingest import inspect_tree
                registry = candidate.registry(); records = inspect_tree(destination_base, uuid.uuid4().hex, cancel)
                for item in records: item['library_path'] = str((destination_base / item['relative_path']).relative_to(target))
                registry['assets'].extend(a for a in records if not a['excluded'])
                atomic_json(candidate.meta / 'registry.json', registry)
            else:
                candidate._config['library_id'] = self._config['library_id']
                atomic_json(candidate.meta / 'library.json', {'schema_version': 1, 'library_id': self._config['library_id']})
            if cancel(): raise InterruptedError('Đã hủy trước khi chuyển cấu hình.')
            candidate.config_dir = self.config_dir; candidate.config_file = self.config_file
            candidate.snapshot()
            atomic_json(self.config_file, candidate._config)
            self._config = candidate._config
            switched = True
            if delete_source and not legacy_source:
                marker = read_json(source / '.chundvc_meta/library.json', {})
                if marker.get('library_id') != old_config.get('library_id'):
                    raise ValueError('Đã chuyển kho; không dọn nguồn vì marker nguồn đã đổi.')
                for tier in ('01_core', '02_cloud_downloads', '03_local_linked'):
                    candidate.require_online()
                    old_tier = source / tier
                    if old_tier.is_symlink(): continue
                    if old_tier.is_dir(): shutil.rmtree(old_tier)
                progress('Đã chuyển và dọn media kho cũ; metadata cũ được giữ.')
        except Exception:
            if not switched: self._config = old_config
            raise  # giữ bản sao dở trên đích để kiểm tra; không xóa nguồn
        finally:
            shutil.rmtree(temp_config, ignore_errors=True)
