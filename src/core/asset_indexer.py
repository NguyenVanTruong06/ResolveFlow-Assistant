import os
from pathlib import Path
from typing import Dict, List, Any, Optional

# Bộ nhớ đệm toàn cục (Singleton Cache) tránh quét ổ đĩa nhiều lần
_GLOBAL_ASSET_CACHE: Dict[str, Any] = {}


class AssetIndexer:
    @classmethod
    def clear_cache(cls):
        """Xóa toàn bộ bộ nhớ đệm quét tài nguyên."""
        _GLOBAL_ASSET_CACHE.clear()

    def _get_cache_key(self, category: str) -> str:
        try:
            resolved_base = str(self.base_dir.resolve())
        except Exception:
            resolved_base = str(self.base_dir)
        return f"{resolved_base}_{category}"

    def __init__(self, base_dir: str = "assets", settings_scope: str = "ResolveFlow"):
        self.settings_scope = settings_scope
        self.base_dir = Path(base_dir)
        if (self.base_dir / "assets").is_dir():
            target_base = self.base_dir / "assets"
        else:
            target_base = self.base_dir

        self.sfx_dir = target_base / "sfx"
        self.luts_dir = target_base / "luts"
        self.memes_dir = target_base / "broll_memes" / "memes"
        self.titles_dir = target_base / "templates" / "titles"
        self.transitions_dir = target_base / "templates" / "transitions"
        self.overlays_dir = target_base / "broll_memes" / "overlays"
        self.music_dir = target_base / "music"
        self.starter_pack_dir = target_base / "starter_pack"
        self.generators_dir = target_base / "templates" / "Generators"

        self.titles_dirs = [
            target_base / "templates" / "titles",
            target_base / "templates" / "Titles",
        ]
        self.transitions_dirs = [
            target_base / "templates" / "transitions",
            target_base / "templates" / "Transitions",
        ]
        self.overlays_dirs = [
            target_base / "broll_memes" / "overlays",
            target_base / "templates" / "Generators",
            target_base / "templates" / "generators",
        ]

        self.ensure_dirs()

    def get_linked_folders(self) -> List[str]:
        """Lấy danh sách các thư mục ngoài đang được kết nối từ QSettings."""
        from PySide6.QtCore import QSettings
        settings = QSettings("ChunDVC", self.settings_scope)
        raw = settings.value("library/linked_folders", [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        elif not isinstance(raw, list):
            raw = []
        valid = []
        for p in raw:
            p_obj = Path(p)
            if p_obj.is_dir():
                norm = str(p_obj.resolve())
                if norm not in valid:
                    valid.append(norm)
        return valid

    def add_linked_folder(self, folder_path: str) -> bool:
        """Thêm một thư mục máy tính ngoài vào danh sách quét."""
        p = Path(folder_path).resolve()
        if not p.is_dir():
            return False
        from PySide6.QtCore import QSettings
        settings = QSettings("ChunDVC", self.settings_scope)
        current = self.get_linked_folders()
        norm = str(p)
        if norm not in current:
            current.append(norm)
            settings.setValue("library/linked_folders", current)
            self.clear_cache()
            return True
        return False

    def remove_linked_folder(self, folder_path: str) -> bool:
        """Gỡ bỏ thư mục máy tính ngoài khỏi danh sách quét."""
        norm = str(Path(folder_path).resolve())
        from PySide6.QtCore import QSettings
        settings = QSettings("ChunDVC", self.settings_scope)
        current = self.get_linked_folders()
        if norm in current:
            current.remove(norm)
            settings.setValue("library/linked_folders", current)
            self.clear_cache()
            return True
        return False

    def ensure_dirs(self):
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self.luts_dir.mkdir(parents=True, exist_ok=True)
        self.memes_dir.mkdir(parents=True, exist_ok=True)
        self.titles_dir.mkdir(parents=True, exist_ok=True)
        self.transitions_dir.mkdir(parents=True, exist_ok=True)
        self.overlays_dir.mkdir(parents=True, exist_ok=True)
        self.music_dir.mkdir(parents=True, exist_ok=True)
        for mood in ["chill_vlog", "upbeat_trend", "cinematic", "funny"]:
            (self.music_dir / mood).mkdir(parents=True, exist_ok=True)

    def _generate_id(self, path: Path) -> str:
        # Use a deterministic hash for stable IDs
        import hashlib
        return hashlib.md5(str(path).encode()).hexdigest()[:8]

    def _find_thumbnail(self, path: Path) -> str | None:
        png_path = path.with_suffix('.png')
        if png_path.exists():
            return str(png_path)
        jpg_path = path.with_suffix('.jpg')
        if jpg_path.exists():
            return str(jpg_path)

        # If it's a video file, try extracting first frame using ffmpeg
        if path.suffix.lower() in ('.mp4', '.mov', '.webm', '.mkv'):
            try:
                if not path.is_file() or path.stat().st_size < 100:
                    return None
            except Exception:
                return None
            import shutil, subprocess
            ffmpeg = shutil.which('ffmpeg')
            if ffmpeg:
                try:
                    cmd = [ffmpeg, '-y', '-ss', '00:00:00.5', '-i', str(path), '-vframes', '1', '-vf', 'scale=320:-1', str(png_path)]
                    res = subprocess.run(cmd, capture_output=True, timeout=5)
                    if res.returncode == 0 and png_path.exists():
                        return str(png_path)
                except Exception:
                    pass
        return None

    def _scan_dir(self, directory: Path, extensions: list[str], find_thumb: bool = False) -> list[dict]:
        results = []
        if not directory.exists():
            return results

        target_exts = [ext.lower() for ext in extensions]

        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() in target_exts:
                try:
                    rel_parts = file_path.relative_to(directory).parts
                    pack_name = rel_parts[0] if len(rel_parts) > 1 else "Mặc định"
                except Exception:
                    pack_name = "Mặc định"

                item = {
                    "id": self._generate_id(file_path),
                    "name": file_path.stem,
                    "file_path": str(file_path),
                    "path": str(file_path),
                    "thumbnail_path": None,
                    "is_external": False,
                    "source_folder": str(directory),
                    "pack_name": pack_name,
                    "sub_category": pack_name,
                }
                if find_thumb:
                    item["thumbnail_path"] = self._find_thumbnail(file_path)
                results.append(item)
        return results

    def _scan_external_for_category(self, category: str, extensions: list[str], find_thumb: bool = False) -> list[dict]:
        results = []
        for ext_folder_str in self.get_linked_folders():
            ext_dir = Path(ext_folder_str)
            if not ext_dir.is_dir():
                continue
            for item in self._scan_dir(ext_dir, extensions, find_thumb=find_thumb):
                item["is_external"] = True
                item["source_folder"] = ext_folder_str
                if category == "sfx":
                    parent_hint = Path(item["file_path"]).parent.name
                    item["category"] = self._detect_sfx_emotion(item["name"], parent_hint)
                elif category == "memes":
                    item["category"] = self._detect_meme_emotion(item["name"])
                results.append(item)
        return results

    def _get_audio_duration(self, filepath: Path) -> float:
        # 1. If .wav, try wave module
        if filepath.suffix.lower() == ".wav":
            try:
                import wave
                with wave.open(str(filepath), "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    if rate > 0 and frames > 0:
                        return float(frames / rate)
            except Exception:
                pass

        # 2. Try EDLGenerator / ffprobe metadata
        try:
            from src.core.autocut import EDLGenerator
            dur = EDLGenerator.get_video_duration(str(filepath))
            if dur > 0:
                return float(dur)
        except Exception:
            pass

        # 3. Fallback duration
        return 30.0

    def scan_music_assets(self, force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        key = self._get_cache_key("music")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return {k: [dict(item) for item in v] for k, v in _GLOBAL_ASSET_CACHE[key].items()}

        results: Dict[str, List[Dict[str, Any]]] = {
            "chill_vlog": [],
            "upbeat_trend": [],
            "cinematic": [],
            "funny": []
        }

        target_dir = self.music_dir
        if not target_dir.exists() and (self.base_dir / "assets" / "music").exists():
            target_dir = self.base_dir / "assets" / "music"
        elif not target_dir.exists() and (self.base_dir / "music").exists():
            target_dir = self.base_dir / "music"

        if not target_dir.exists():
            _GLOBAL_ASSET_CACHE[key] = results
            return {k: [dict(item) for item in v] for k, v in results.items()}

        recognized_exts = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

        for mood_folder in sorted(target_dir.iterdir()):
            if not mood_folder.is_dir():
                continue
            mood = mood_folder.name
            if mood not in results:
                results[mood] = []

            audio_files = [
                f for f in sorted(mood_folder.rglob("*"))
                if f.is_file() and f.suffix.lower() in recognized_exts
            ]

            for idx, file_path in enumerate(audio_files, 1):
                duration_sec = self._get_audio_duration(file_path)
                fn = file_path.name
                item = {
                    "id": f"bgm_{mood}_{idx}",
                    "name": os.path.splitext(fn)[0],
                    "file_path": os.path.normpath(os.path.abspath(str(file_path))),
                    "category": "bgm",
                    "mood": mood,
                    "duration": float(duration_sec),
                }
                results[mood].append(item)

        _GLOBAL_ASSET_CACHE[key] = results
        return {k: [dict(item) for item in v] for k, v in results.items()}

    @staticmethod
    def _detect_sfx_emotion(stem: str, folder_hint: str = "") -> str:
        s = f"{stem} {folder_hint}".lower()
        if any(k in s for k in ["whoosh", "swoosh", "riser", "swell", "chuyen", "trans", "reverse", "whip", "fast"]):
            return "chuyen_canh"
        elif any(k in s for k in ["boom", "braam", "bass", "drop", "hit", "shock", "pulse", "tim", "heartbeat", "impact", "thud", "punch", "slam", "crash"]):
            return "giat_minh"
        elif any(k in s for k in ["cuoi", "laugh", "airhorn", "horn", "bonk", "de_keu", "funny", "coi", "fart", "slip", "boing"]):
            return "hai_huoc"
        elif any(k in s for k in ["pop", "ding", "bell", "shutter", "ting", "camera", "cash", "coin", "tada", "success", "magic"]):
            return "diem_nhan"
        elif any(k in s for k in ["glitch", "click", "bleep", "beep", "censor", "type", "keyboard", "laser", "tech", "zap", "scifi", "sci-fi", "digital", "switch", "ui", "interface", "select", "mouse"]):
            return "cong_nghe"
        return "accent"

    @staticmethod
    def _detect_meme_emotion(stem: str) -> str:
        s = stem.lower()
        if any(k in s for k in ["do_mixi", "pewpew", "con_dung_cai_nit", "huan", "thay_ba", "boman", "vtv", "xach_balo", "vo_tay", "may_mu"]):
            return "vietnam_viral"
        elif any(k in s for k in ["khaby", "meo_khoc", "banana", "cat_vibin", "wow", "facepalm", "nhun_vai", "reaction"]):
            return "reaction"
        elif any(k in s for k in ["cham_hoi", "da_den", "confused", "question", "hoi_cham"]):
            return "cham_hoi"
        elif any(k in s for k in ["cay_cu", "dap_ban", "so_qua", "nhin_lau", "toang"]):
            return "cay_cu"
        elif any(k in s for k in ["green_screen", "phong_xanh"]):
            return "green_screen"
        return "trending"

    def scan_sfx(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("sfx")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        target_dirs = [self.sfx_dir]
        if hasattr(self, "starter_pack_dir") and (self.starter_pack_dir / "SFX").exists():
            target_dirs.append(self.starter_pack_dir / "SFX")
        elif (self.base_dir / "starter_pack" / "SFX").exists():
            target_dirs.append(self.base_dir / "starter_pack" / "SFX")

        seen_names = set()
        res = []
        for sdir in target_dirs:
            if not sdir.exists():
                continue
            for item in self._scan_dir(sdir, ['.wav', '.mp3', '.ogg', '.flac', '.m4a']):
                base_name = item["name"]
                if base_name in seen_names:
                    continue
                seen_names.add(base_name)
                parent_hint = Path(item["file_path"]).parent.name
                item["category"] = self._detect_sfx_emotion(item["name"], parent_hint)
                res.append(item)

        # Quét bổ sung từ các thư mục máy tính ngoài được liên kết
        ext_sfx = self._scan_external_for_category("sfx", ['.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac'])
        for item in ext_sfx:
            base_name = item["name"]
            if base_name not in seen_names:
                seen_names.add(base_name)
                res.append(item)

        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    def scan_luts(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("luts")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        res = self._scan_dir(self.luts_dir, ['.cube'])
        seen_names = {x["name"] for x in res}
        ext_luts = self._scan_external_for_category("luts", ['.cube'])
        for item in ext_luts:
            if item["name"] not in seen_names:
                seen_names.add(item["name"])
                res.append(item)
        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    @staticmethod
    def _is_non_meme_video(filename: str, filepath: str = "") -> bool:
        """Kiểm tra video có phải là video demo chuyển cảnh, tutorial, hay tài liệu kỹ thuật không phải meme."""
        lower_p = (filepath or filename).lower()
        if any(k in lower_p for k in [
            "preview transition", "transitions/preview", "transition/preview",
            "transitions for dr", "tutorial", "help/text", "readme", "instruction"
        ]):
            return True
        fn_lower = filename.lower()
        trans_keywords = [
            "3d box", "pan tilt", "pan bounce", "pan hit", "pan warp", "persp ",
            "fisheye", "camera flight", "particles flight", "dist poly", "dist plank",
            "sh square", "shape arrow", "shape arc", "shape parall", "parallax pan",
            "spin zoom", "whip pan", "seamless transition"
        ]
        if any(fn_lower.startswith(k) or f"_{k}" in fn_lower or f" - {k}" in fn_lower for k in trans_keywords):
            return True
        return False

    def scan_memes(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("memes")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        res = self._scan_dir(self.memes_dir, ['.mp4', '.mov'], find_thumb=True)
        res = [item for item in res if not self._is_non_meme_video(item["name"], item.get("file_path", ""))]
        for item in res:
            item["category"] = self._detect_meme_emotion(item["name"])

        seen_names = {x["name"] for x in res}
        ext_memes = self._scan_external_for_category("memes", ['.mp4', '.mov', '.webm', '.mkv'], find_thumb=True)
        for item in ext_memes:
            if not self._is_non_meme_video(item["name"], item.get("file_path", "")) and item["name"] not in seen_names:
                seen_names.add(item["name"])
                res.append(item)

        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    def scan_overlays(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("overlays")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        seen = set()
        res = []
        target_dirs = getattr(self, "overlays_dirs", [self.overlays_dir])
        for odir in target_dirs:
            if not odir.exists():
                continue
            for item in self._scan_dir(odir, ['.mp4', '.mov', '.png', '.setting'], find_thumb=True):
                if not self._is_non_meme_video(item["name"], item.get("file_path", "")) and item["name"] not in seen:
                    seen.add(item["name"])
                    res.append(item)

        ext_ov = self._scan_external_for_category("overlays", ['.mp4', '.mov', '.png', '.setting', '.webm'], find_thumb=True)
        for item in ext_ov:
            if not self._is_non_meme_video(item["name"], item.get("file_path", "")) and item["name"] not in seen:
                seen.add(item["name"])
                res.append(item)

        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    def scan_titles(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("titles")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        seen = set()
        res = []
        target_dirs = getattr(self, "titles_dirs", [self.titles_dir])
        for tdir in target_dirs:
            if not tdir.exists():
                continue
            for item in self._scan_dir(tdir, ['.setting'], find_thumb=True):
                if item["name"] not in seen:
                    seen.add(item["name"])
                    res.append(item)

        ext_titles = self._scan_external_for_category("titles", ['.setting'], find_thumb=True)
        for item in ext_titles:
            if item["name"] not in seen:
                seen.add(item["name"])
                res.append(item)

        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    def scan_transitions(self, force_refresh: bool = False) -> list[dict]:
        key = self._get_cache_key("transitions")
        if not force_refresh and key in _GLOBAL_ASSET_CACHE:
            return [dict(x) for x in _GLOBAL_ASSET_CACHE[key]]
        seen = set()
        res = []
        target_dirs = getattr(self, "transitions_dirs", [self.transitions_dir])

        import zipfile
        previews_base = self.transitions_dir / "previews"
        installed_base = self.transitions_dir / "installed"
        installed_base.mkdir(parents=True, exist_ok=True)

        # 1. Unpack any .drfx bundles into installed/<pack_name>/ if not already unpacked
        for tdir in target_dirs:
            if not tdir.exists():
                continue
            for root, dirs, files in os.walk(tdir):
                if "previews" in root or "installed" in root:
                    continue
                for f in files:
                    if f.endswith(".drfx"):
                        drfx_file = Path(root) / f
                        pack_name = drfx_file.stem
                        dest_pack = installed_base / pack_name
                        needs_unpack = True
                        if dest_pack.exists() and any(dest_pack.rglob("*.setting")):
                            needs_unpack = False
                        if needs_unpack:
                            try:
                                with zipfile.ZipFile(drfx_file, "r") as z:
                                    for m in z.namelist():
                                        if m.endswith(".setting") or m.endswith(".png"):
                                            z.extract(m, dest_pack)
                            except Exception as exc:
                                logger.warning("Could not unpack drfx %s: %s", drfx_file, exc)

        # 2. Scan all .setting files
        for tdir in target_dirs:
            if not tdir.exists():
                continue
            for file_path in tdir.rglob("*.setting"):
                if not file_path.is_file():
                    continue
                parts = file_path.parts
                stem = file_path.stem

                if "installed" in parts:
                    idx = parts.index("installed")
                    pack_name = parts[idx + 1] if len(parts) > idx + 1 else "Transitions"
                    category = parts[-3] if len(parts) >= 3 else pack_name
                    sub_category = parts[-2] if len(parts) >= 2 else pack_name
                else:
                    try:
                        rel_parts = file_path.relative_to(tdir).parts
                        pack_name = rel_parts[0] if len(rel_parts) > 1 else "Mặc định"
                    except Exception:
                        pack_name = "Mặc định"
                    category = "Mặc định"
                    sub_category = pack_name

                # Unique key: pack_name + stem
                item_key = f"{pack_name}_{stem}"
                if item_key in seen:
                    continue
                seen.add(item_key)

                # Thumbnail lookup
                thumb = self._find_thumbnail(file_path)
                if not thumb and previews_base.exists():
                    p_png = previews_base / pack_name / f"{stem}.png"
                    if p_png.exists():
                        thumb = str(p_png)
                    else:
                        p_flat_png = previews_base / f"{stem}.png"
                        if p_flat_png.exists():
                            thumb = str(p_flat_png)

                # Preview video lookup (.mp4)
                preview_video = None
                if previews_base.exists():
                    p_mp4 = previews_base / pack_name / f"{stem}.mp4"
                    if p_mp4.exists():
                        preview_video = str(p_mp4)
                    else:
                        p_flat_mp4 = previews_base / f"{stem}.mp4"
                        if p_flat_mp4.exists():
                            preview_video = str(p_flat_mp4)

                item = {
                    "id": self._generate_id(file_path),
                    "name": stem,
                    "tab": "trans",
                    "category": category,
                    "sub_category": sub_category,
                    "pack_name": pack_name,
                    "file_path": str(file_path),
                    "path": str(file_path),
                    "thumbnail_path": thumb,
                    "preview_path": preview_video or str(file_path),
                    "duration": 0.8,
                    "is_external": False,
                    "source_folder": str(file_path.parent),
                }
                res.append(item)

        # 3. External folders
        ext_trans = self._scan_external_for_category("transitions", [".setting"], find_thumb=True)
        for item in ext_trans:
            item_key = f"ext_{item['name']}"
            if item_key not in seen:
                seen.add(item_key)
                res.append(item)

        _GLOBAL_ASSET_CACHE[key] = res
        return [dict(x) for x in res]

    def scan_managed_assets(self):
        """Kho ngoài đã kiểm duyệt; không quét lại/ghi thumbnail vào nguồn."""
        from src.core.storage_manager import StorageManager
        return StorageManager().ui_assets()

    def scan_all_assets(self, force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        music_data = self.scan_music_assets(force_refresh=force_refresh)
        all_bgm = [item for items in music_data.values() for item in items]
        all_assets: Dict[str, List[Dict[str, Any]]] = {
            "sfx": self.scan_sfx(force_refresh=force_refresh),
            "luts": self.scan_luts(force_refresh=force_refresh),
            "memes": self.scan_memes(force_refresh=force_refresh),
            "overlays": self.scan_overlays(force_refresh=force_refresh),
            "titles": self.scan_titles(force_refresh=force_refresh),
            "transitions": self.scan_transitions(force_refresh=force_refresh),
            "bgm": all_bgm,
        }
        all_assets["managed"] = self.scan_managed_assets()
        for mood, items in music_data.items():
            all_assets[f"bgm_{mood}"] = items
        return all_assets
