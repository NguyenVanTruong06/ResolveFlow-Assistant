import os
from pathlib import Path
from typing import Dict, List, Any, Optional


class AssetIndexer:
    def __init__(self, base_dir: str = "assets"):
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

        self.ensure_dirs()

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
                item = {
                    "id": self._generate_id(file_path),
                    "name": file_path.stem,
                    "file_path": str(file_path),
                    "thumbnail_path": None
                }
                if find_thumb:
                    item["thumbnail_path"] = self._find_thumbnail(file_path)
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

    def scan_music_assets(self) -> Dict[str, List[Dict[str, Any]]]:
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
            return results

        recognized_exts = {".mp3", ".wav", ".m4a", ".aac", ".flac"}

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

        return results

    def scan_sfx(self) -> list[dict]:
        return self._scan_dir(self.sfx_dir, ['.wav'])

    def scan_luts(self) -> list[dict]:
        return self._scan_dir(self.luts_dir, ['.cube'])

    def scan_memes(self) -> list[dict]:
        return self._scan_dir(self.memes_dir, ['.mp4', '.mov'], find_thumb=True)

    def scan_overlays(self) -> list[dict]:
        return self._scan_dir(self.overlays_dir, ['.mp4', '.mov', '.png'], find_thumb=True)

    def scan_titles(self) -> list[dict]:
        return self._scan_dir(self.titles_dir, ['.setting'], find_thumb=True)

    def scan_transitions(self) -> list[dict]:
        return self._scan_dir(self.transitions_dir, ['.setting'], find_thumb=True)

    def scan_all_assets(self) -> Dict[str, List[Dict[str, Any]]]:
        music_data = self.scan_music_assets()
        all_bgm = [item for items in music_data.values() for item in items]
        all_assets: Dict[str, List[Dict[str, Any]]] = {
            "sfx": self.scan_sfx(),
            "luts": self.scan_luts(),
            "memes": self.scan_memes(),
            "overlays": self.scan_overlays(),
            "titles": self.scan_titles(),
            "transitions": self.scan_transitions(),
            "bgm": all_bgm,
        }
        for mood, items in music_data.items():
            all_assets[f"bgm_{mood}"] = items
        return all_assets
