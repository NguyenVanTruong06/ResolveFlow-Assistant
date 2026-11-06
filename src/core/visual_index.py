"""Lấy mẫu hình ảnh của footage nguồn, lưu JSON để dùng lại giữa các lần dựng.

Đây là bằng chứng hình ảnh có timestamp; chưa phải mô hình nhận diện nội dung.
Mô tả chỉ xuất hiện khi một bộ phân tích hình ảnh hoặc người dùng ghi rõ.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


VISUAL_SCHEMA_VERSION = 3
ANALYSIS_VERSION = "temporal-windows-v3"


def _number(value, label):
    import math
    if isinstance(value, bool):
        raise ValueError(f"{label} không hợp lệ")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} không hợp lệ") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} phải hữu hạn")
    return number


def _labels(value):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError("objects/observed_actions phải là danh sách chuỗi")
    return list(dict.fromkeys(x.strip()[:80] for x in value if x.strip()))[:12]


def _reject_constant(value):
    raise ValueError(f"JSON chứa số không hữu hạn: {value}")


def validate_segment(
    item: Dict[str, Any],
    duration_sec: Optional[float],
    allowed_evidence_times: Optional[set] = None,
    default_origin: str = "user",
    default_model: str = "manual",
    default_version: str = ANALYSIS_VERSION,
) -> Dict[str, Any]:
    """Validate temporal model output; legacy user notes có thể là annotation điểm.

    Đoạn thời gian dùng khoảng [start, end), evidence không được nằm ở end.
    Khi có allowed_evidence_times, provenance luôn do ứng dụng cung cấp.
    """
    strict = allowed_evidence_times is not None
    if not isinstance(item, dict) or not isinstance(item.get("description"), str) or not item["description"].strip():
        raise ValueError("Phân đoạn thiếu mô tả hợp lệ")
    duration = _number(duration_sec, "duration") if duration_sec is not None else None
    if duration is not None and duration <= 0 or strict and duration is None:
        raise ValueError("Thiếu thời lượng video hợp lệ")
    start = _number(item.get("start_sec", item.get("time_sec")), "start_sec")
    if start < 0 or duration is not None and start >= duration:
        raise ValueError("Mốc bắt đầu ngoài video")
    end = _number(item["end_sec"], "end_sec") if item.get("end_sec") is not None else None
    point = end is None
    if strict and point:
        raise ValueError("Model phải trả đoạn có start/end")
    if end is not None and (end <= start or duration is not None and end > duration):
        raise ValueError("Phải có 0 <= start < end <= duration")

    objects = _labels(item.get("objects", []))
    actions = _labels(item.get("observed_actions", []))
    raw_evidence = item.get("evidence_times", [])
    if not isinstance(raw_evidence, list) or len(raw_evidence) > 32:
        raise ValueError("Danh sách evidence_times không hợp lệ")
    evidence = sorted(set(_number(t, "evidence") for t in raw_evidence))
    for stamp in evidence:
        if stamp < start or end is not None and stamp >= end:
            raise ValueError("Evidence phải nằm trong đoạn quan sát")
        if duration is not None and stamp >= duration:
            raise ValueError("Evidence ngoài video")
        if strict and stamp not in allowed_evidence_times:
            raise ValueError("Evidence không thuộc frame đã gửi")
    if strict and not evidence:
        raise ValueError("Model chưa cung cấp bằng chứng hình ảnh")

    status = item.get("analysis_status", "described")
    if status not in ("described", "unknown", "needs_review", "error", "cancelled"):
        status = "needs_review"
    if point or duration is None:
        status = "needs_review"
    if strict and actions and len(evidence) < 2:
        actions = []
        status = "needs_review"
    if strict and status != "described":
        actions = []
    result = {
        "time_sec": start,
        "description": item["description"].strip().replace("\n", " ")[:200],
        "objects": objects, "observed_actions": actions,
        "evidence_times": evidence, "analysis_status": status,
        "observation_kind": "point" if point else "segment",
        "origin": default_origin if strict else str(item.get("origin") or default_origin),
        "model": default_model if strict else str(item.get("model") or default_model),
        "analysis_version": default_version if strict else str(item.get("analysis_version") or default_version),
    }
    if not point:
        result.update(start_sec=start, end_sec=end)
    return result


class VisualIndex:
    """Quản lý mẫu hình ảnh riêng với cache Whisper và kho Asset Library."""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or os.environ.get("RESOLVEFLOW_VISUAL_CACHE_DIR") or Path.home() / ".resolveflow_cache" / "visual")

    @staticmethod
    def _fingerprint(path: Path) -> Dict[str, int]:
        stat = path.stat()
        return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    @staticmethod
    def _identity(path: Path, content_hash: Optional[str]) -> str:
        if content_hash:
            return content_hash
        # Phân biệt hai file cùng tên; fingerprint kiểm tra thay đổi sau đó.
        return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]

    def _file(self, path: Path, content_hash: Optional[str]) -> Path:
        return self.cache_dir / f"{self._identity(path, content_hash)}.json"

    def load(self, video_path: os.PathLike | str, content_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
        path = Path(video_path)
        try:
            fingerprint = self._fingerprint(path)
            candidates = [self._file(path, content_hash)]
            if content_hash:
                candidates.append(self._file(path, None))
            for candidate in candidates:
                try:
                    with candidate.open(encoding="utf-8") as stream:
                        data = json.load(stream, parse_constant=_reject_constant)
                except (OSError, ValueError, TypeError):
                    continue
                if (isinstance(data, dict) and data.get("schema_version") in (1, 2, 3)
                        and data.get("fingerprint") == fingerprint):
                    if not isinstance(data.get("descriptions", []), list):
                        continue
                    data["descriptions"] = [d for d in data.get("descriptions", []) if isinstance(d, dict)]
                    data["visual_segments"] = data["descriptions"]
                    if data["schema_version"] < VISUAL_SCHEMA_VERSION:
                        # Model cache cũ thiếu signature/evidence: giữ để tra cứu, không dùng như quan sát đã xác minh.
                        notes = data.get("descriptions", [])
                        data["legacy_descriptions"] = [d for d in notes if isinstance(d, dict) and d.get("origin") != "user"]
                        data["descriptions"] = [
                            {**d, "analysis_status": "needs_review", "observed_actions": [],
                             "review_reason": "legacy_provenance_unverified"}
                            for d in notes if isinstance(d, dict) and d.get("origin") == "user"
                        ]
                        data["visual_segments"] = data["descriptions"]
                        data.pop("analysis", None)
                    return data
            return None
        except (OSError, ValueError, TypeError):
            return None

    def _write(self, path: Path, data: Dict[str, Any], content_hash: Optional[str]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._file(path, content_hash)
        fd, tmp = tempfile.mkstemp(prefix=".visual-", suffix=".json", dir=self.cache_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if target.is_file():
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                    if isinstance(old, dict) and old.get("fingerprint") != data.get("fingerprint"):
                        import shutil
                        history = self.cache_dir / "history"
                        history.mkdir(exist_ok=True)
                        revision = hashlib.sha256(json.dumps(old.get("fingerprint"), sort_keys=True).encode()).hexdigest()[:16]
                        shutil.copy2(target, history / f"{target.stem}-{revision}.json")
                except (ValueError, TypeError):
                    pass
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save_descriptions(
        self,
        video_path: os.PathLike | str,
        descriptions: List[Dict[str, Any]],
        content_hash: Optional[str] = None,
        origin: str = "user",
        model: Optional[str] = None,
        analysis_version: str = ANALYSIS_VERSION,
    ) -> Dict[str, Any]:
        """Ghi chú giải hình ảnh có mốc thời gian và hành động quan sát được."""
        path = Path(video_path)
        current = self.load(path, content_hash) or {
            "schema_version": VISUAL_SCHEMA_VERSION,
            "fingerprint": self._fingerprint(path),
            "samples": [],
            "status": "awaiting_description",
        }
        limit = float(current["duration_sec"]) if current.get("duration_sec") else None
        cleaned = []
        for item in descriptions:
            cleaned_item = validate_segment(
                item,
                duration_sec=limit,
                default_origin=origin,
                default_model=model or origin,
                default_version=analysis_version,
            )
            cleaned.append(cleaned_item)
        current["descriptions"] = sorted(cleaned, key=lambda x: x.get("start_sec", x["time_sec"]))
        current["schema_version"] = VISUAL_SCHEMA_VERSION
        current["visual_segments"] = current["descriptions"]
        current["status"] = "described" if current["descriptions"] else "awaiting_description"
        self._write(path, current, content_hash)
        return current

    @staticmethod
    def _windows_ready(data):
        try:
            duration = _number(data["duration_sec"], "duration")
            windows = data["windows"]
            if not isinstance(windows, list) or not windows:
                return False
            for window in windows:
                start, end = _number(window["start_sec"], "start"), _number(window["end_sec"], "end")
                if not 0 <= start < end <= duration or not isinstance(window["samples"], list):
                    return False
                for sample in window["samples"]:
                    if not start <= _number(sample["time_sec"], "PTS") < end or not Path(sample["image_path"]).is_file():
                        return False
            return True
        except (KeyError, TypeError, ValueError, OSError):
            return False

    def prepare_windows(
        self, video_path, window_sec=8.0, frames_per_window=4, max_windows=24,
        cancelled=None, content_hash=None,
    ):
        """Tạo cửa sổ có ngân sách; coverage công bố phần chưa được lấy mẫu."""
        import math
        import shutil
        from src.core.visual_sampling import VideoSampler, SAMPLER_VERSION, group_samples

        window_sec = _number(window_sec, "window_sec")
        if not 0 < window_sec <= 60 or not isinstance(frames_per_window, int) or not 2 <= frames_per_window <= 8 or not isinstance(max_windows, int) or not 1 <= max_windows <= 120:
            raise ValueError("Ngân sách cửa sổ/khung hình không hợp lệ")
        if cancelled and cancelled():
            raise InterruptedError("Đã hủy lấy mẫu hình ảnh")
        path = Path(video_path)
        fingerprint = self._fingerprint(path)
        config = {"window_sec": window_sec, "frames_per_window": frames_per_window,
                  "max_windows": max_windows, "sampler_version": SAMPLER_VERSION}
        cached = self.load(path, content_hash) or {}
        if cached.get("temporal_config") == config and self._windows_ready(cached):
            return {**cached, "cache_hit": True}
        root = self.cache_dir / "frames"
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="temporal-", dir=root))
        sampler = None
        committed = False
        try:
            sampler = VideoSampler(path)
            duration = sampler.duration_sec
            if duration <= 0:
                raise ValueError("Video có thời lượng không hợp lệ")
            count = math.ceil(duration / window_sec)
            if count <= 0:
                raise ValueError("Không thể tạo cửa sổ cho video có thời lượng 0")
            selected = list(range(count)) if count <= max_windows else (
                [count // 2] if max_windows == 1 else
                [round(i * (count - 1) / (max_windows - 1)) for i in range(max_windows)]
            )
            windows = []
            for ordinal in selected:
                start, end = ordinal * window_sec, min((ordinal + 1) * window_sec, duration)
                samples, seen = [], set()
                for j in range(frames_per_window):
                    target = start + (j + 0.5) * (end - start) / frames_per_window
                    sample = sampler.sample(target, staging / f"{ordinal:05d}-{j}.jpg", cancelled)
                    if sample and start <= sample["time_sec"] < end and sample["time_sec"] not in seen:
                        samples.append(sample)
                        seen.add(sample["time_sec"])
                windows.append({"window_id": f"W{ordinal:05d}", "start_sec": start,
                                "end_sec": end, "samples": group_samples(samples)})
            if cancelled and cancelled():
                raise InterruptedError("Đã hủy lấy mẫu hình ảnh")
            if self._fingerprint(path) != fingerprint:
                raise ValueError("Video thay đổi trong khi lấy mẫu")
            user_notes = [d for d in cached.get("descriptions", []) if d.get("origin") == "user"]
            valid_count = sum(1 for w in windows if len(w.get("samples", [])) >= 2)
            is_complete = count > 0 and len(selected) == count and len(windows) == count and valid_count == count
            data = {
                **cached,
                "schema_version": VISUAL_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "duration_sec": duration,
                "temporal_config": config,
                "windows": windows,
                "coverage": {
                    "complete": is_complete,
                    "total_windows": count,
                    "selected_windows": len(selected),
                    "sampled_windows": len(windows),
                    "valid_windows": valid_count,
                    "sampled_ranges": [[w["start_sec"], w["end_sec"]] for w in windows],
                },
                "status": "sampled",
                "descriptions": user_notes,
                "visual_segments": user_notes,
            }
            data.pop("analysis", None)
            self._write(path, data, content_hash)
            committed = True
            return {**data, "cache_hit": False}
        finally:
            if sampler:
                sampler.close()
            if not committed:
                shutil.rmtree(staging, ignore_errors=True)

    def refine_window(self, video_path, window, frames_per_window=8, cancelled=None):
        """Tăng mẫu đúng một vùng chưa rõ, vẫn có giới hạn số frame."""
        import shutil
        from src.core.visual_sampling import VideoSampler, group_samples
        if not isinstance(frames_per_window, int) or not 2 <= frames_per_window <= 8:
            raise ValueError("Số frame bổ sung phải từ 2 tới 8")
        root = self.cache_dir / "frames"
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="refine-", dir=root))
        sampler = None
        try:
            sampler = VideoSampler(video_path)
            start, end = window["start_sec"], window["end_sec"]
            samples, seen = [], set()
            for i in range(frames_per_window):
                target = start + (i + 0.5) * (end - start) / frames_per_window
                sample = sampler.sample(target, staging / f"{i}.jpg", cancelled)
                if sample and start <= sample["time_sec"] < end and sample["time_sec"] not in seen:
                    samples.append(sample)
                    seen.add(sample["time_sec"])
            if cancelled and cancelled():
                raise InterruptedError("Đã hủy bổ sung ảnh")
            return {**window, "samples": group_samples(samples)}
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if sampler:
                sampler.close()

    def scan(
        self,
        video_path: os.PathLike | str,
        interval_sec: float = 10.0,
        max_samples: int = 120,
        content_hash: Optional[str] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Lấy ảnh thu nhỏ theo thời gian; gọi từ worker, không gọi trên GUI thread."""
        if interval_sec <= 0 or max_samples <= 0:
            raise ValueError("interval_sec và max_samples phải dương")
        path = Path(video_path)
        cached = self.load(path, content_hash)
        if (cached and cached.get("sampler_version") == "pyav-pts-v1"
                and cached.get("interval_sec") == interval_sec
                and cached.get("max_samples") == max_samples
                and cached.get("samples")
                and Path(cached.get("contact_sheet_path", "")).is_file()
                and all(Path(s["image_path"]).is_file() for s in cached["samples"])):
            return {**cached, "cache_hit": True}

        import shutil
        from src.core.visual_sampling import VideoSampler

        fingerprint = self._fingerprint(path)
        root = self.cache_dir / "frames"
        root.mkdir(parents=True, exist_ok=True)
        image_dir = Path(tempfile.mkdtemp(prefix="overview-", dir=root))
        sampler = None
        committed = False
        try:
            sampler = VideoSampler(path)
            duration_sec = sampler.duration_sec
            step = max(interval_sec, duration_sec / max_samples)
            times = [i * step for i in range(max_samples) if i * step < duration_sec]
            samples = []
            for ordinal, target in enumerate(times):
                sample = sampler.sample(target, image_dir / f"{ordinal:04d}.jpg", cancelled, width=320)
                if sample and sample["time_sec"] < duration_sec:
                    samples.append(sample)
            if not samples:
                raise ValueError(f"Không lấy được khung hình: {path}")
            if cancelled and cancelled():
                raise InterruptedError("Đã hủy lấy mẫu hình ảnh")
            if self._fingerprint(path) != fingerprint:
                raise ValueError("Video thay đổi trong khi lấy mẫu")
            contact_sheet = self._contact_sheet(samples, image_dir)
            if cached and cached.get("fingerprint") == fingerprint:
                # Cùng nguồn: bảo lưu temporal_config, windows, coverage, analysis và descriptions hiện có
                data = dict(cached)
                data.update({
                    "schema_version": VISUAL_SCHEMA_VERSION,
                    "fingerprint": fingerprint,
                    "duration_sec": duration_sec,
                    "interval_sec": interval_sec,
                    "max_samples": max_samples,
                    "samples": samples,
                    "sampler_version": "pyav-pts-v1",
                    "contact_sheet_path": str(contact_sheet),
                })
                if "status" not in data:
                    data["status"] = "sampled"
                if "descriptions" not in data:
                    data["descriptions"] = []
                data["visual_segments"] = data["descriptions"]
            else:
                data = {
                    "schema_version": VISUAL_SCHEMA_VERSION, "fingerprint": fingerprint,
                    "duration_sec": duration_sec, "interval_sec": interval_sec,
                    "max_samples": max_samples, "status": "sampled", "samples": samples,
                    "sampler_version": "pyav-pts-v1", "contact_sheet_path": str(contact_sheet),
                    "descriptions": [], "visual_segments": [],
                }
            self._write(path, data, content_hash)
            committed = True
            return {**data, "cache_hit": False}
        finally:
            if sampler:
                sampler.close()
            if not committed:
                shutil.rmtree(image_dir, ignore_errors=True)

    @staticmethod
    def _contact_sheet(samples: List[Dict[str, Any]], image_dir: Path) -> Path:
        """Tạo bảng ảnh nhỏ để người dùng có thể đính kèm khi hỏi AI thị giác."""
        from PIL import Image, ImageDraw

        selected = samples[::max(1, (len(samples) + 29) // 30)][:30]
        width, height, columns = 200, 142, 5
        rows = (len(selected) + columns - 1) // columns
        sheet = Image.new("RGB", (width * columns, height * rows), "#18181b")
        draw = ImageDraw.Draw(sheet)
        for index, sample in enumerate(selected):
            with Image.open(sample["image_path"]) as photo:
                photo.thumbnail((width - 8, height - 24))
                x = (index % columns) * width + (width - photo.width) // 2
                y = (index // columns) * height + 3
                sheet.paste(photo.convert("RGB"), (x, y))
            stamp = float(sample["time_sec"])
            label = f"{int(stamp // 60):02d}:{int(stamp % 60):02d}"
            draw.text(((index % columns) * width + 7, (index // columns) * height + height - 19), label, fill="white")
        destination = image_dir / "contact_sheet.jpg"
        sheet.save(destination, quality=85)
        return destination
