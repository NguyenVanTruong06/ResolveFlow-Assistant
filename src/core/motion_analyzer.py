"""
Motion and Visual Content Analyzer (CPU-first, 0% footprint, learning from Akitt Pro 4.0).

Analyzes video previews, GIF animations, and Fusion .setting nodes using:
1. Farneback Optical Flow downscaled to 192x108 across representative frames.
2. Motion classification: shake, zoom_in, zoom_out, slide_up/down/left/right, fade, static.
3. Node inspection: detects TextPlus, BezierSpline, Shake, Glow, etc.
4. Bilingual search tags (Vietnamese & English) with accent-insensitive matching.
"""

from __future__ import annotations
import os
import re
import unicodedata
from typing import List, Dict, Any, Optional, Tuple

VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}

# Dictionary mapping motion types to rich bilingual search tags
BILINGUAL_TAG_MAP: Dict[str, List[str]] = {
    "shake": [
        "shake", "camera shake", "glitch", "vibrate",
        "rung", "giat", "rung giat", "rung giật", "giật", "nẩy", "chan dong", "chấn động"
    ],
    "zoom_in": [
        "zoom", "zoom_in", "scale", "scale_up",
        "phong to", "phóng to", "zoom vao", "zoom vào", "tien lai", "tiến lại"
    ],
    "zoom_out": [
        "zoom", "zoom_out", "scale_down", "shrink",
        "thu nho", "thu nhỏ", "zoom ra", "lui lai", "lùi lại"
    ],
    "slide_up": [
        "slide", "slide_up", "up", "rise",
        "truot len", "trượt lên", "bay len", "bay lên", "noi len", "nổi lên"
    ],
    "slide_down": [
        "slide", "slide_down", "down", "drop",
        "truot xuong", "trượt xuống", "roi xuong", "rơi xuống", "ha xuong", "hạ xuống"
    ],
    "slide_left": [
        "slide", "slide_left", "left", "pan_left",
        "truot trai", "trượt trái", "qua trai", "qua trái", "sang trai", "sang trái"
    ],
    "slide_right": [
        "slide", "slide_right", "right", "pan_right",
        "truot phai", "trượt phải", "qua phai", "qua phải", "sang phai", "sang phải"
    ],
    "fade_in": [
        "fade", "fade_in", "dissolve", "appear",
        "mo dan", "mờ dần", "hien dan", "hiện dần", "sang dan", "sáng dần"
    ],
    "fade_out": [
        "fade", "fade_out", "disappear",
        "mo dan", "mờ dần", "bien mat", "biến mất", "toi dan", "tối dần"
    ],
    "rapid_cuts": [
        "rapid", "cut", "transition", "flash", "nhanh", "chớp", "chuyen canh", "chuyển cảnh"
    ],
    "static": [
        "static", "still", "tinh", "tĩnh", "dung yen", "đứng yên", "khong dong", "không động"
    ],
    "text": [
        "text", "title", "typography", "subtitle", "caption",
        "chu", "chữ", "tieu de", "tiêu đề", "phu de", "phụ đề"
    ],
    "animated": [
        "animated", "animation", "motion",
        "chuyen dong", "chuyển động", "hoat hoa", "hoạt họa"
    ],
    "glow": [
        "glow", "neon", "shine", "light",
        "phat sang", "phát sáng", "da quang", "dạ quang", "den neon"
    ]
}


def strip_accents(text: str) -> str:
    """Removes Vietnamese tone marks / accents for fuzzy query matching."""
    if not text:
        return ""
    # Normalize unicode to decomposed form
    nfkd = unicodedata.normalize('NFKD', text)
    stripped = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return stripped.replace('đ', 'd').replace('Đ', 'D').lower()


def matches_query(query: str, tags_or_text: List[str] | str) -> bool:
    """
    Checks if query matches any tag or text, ignoring accents and case.
    Example: 'phong to' matches 'phóng to' and 'zoom_in'.
    """
    if not query:
        return True
    
    clean_q = strip_accents(query.strip().lower())
    if not clean_q:
        return True

    if isinstance(tags_or_text, str):
        target_list = [tags_or_text]
    else:
        target_list = list(tags_or_text)

    for item in target_list:
        clean_item = strip_accents(str(item).lower())
        if clean_q in clean_item or clean_item in clean_q:
            return True
    return False


class SmartMotionAnalyzer:
    """
    Offline, lightweight CPU-first motion analyzer.
    Analyzes video motion and node setting files without GPU or external APIs.
    """

    @classmethod
    def sample_frames(cls, media_path: str, count: int = 9, target_size: Tuple[int, int] = (192, 108)) -> List[Any]:
        """
        Samples N frames from a video or GIF, downscaled to target_size for sub-millisecond CV processing.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return []

        if not os.path.isfile(media_path):
            return []

        ext = os.path.splitext(media_path)[1].lower()
        frames = []

        if ext in VIDEO_EXTS:
            cap = cv2.VideoCapture(media_path)
            try:
                if not cap.isOpened():
                    return []
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total_frames <= 0:
                    # Sequential fallback
                    for _ in range(count):
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            break
                        resized = cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)
                        frames.append(resized)
                else:
                    indices = [int(total_frames * i / max(1, count - 1)) for i in range(count)]
                    indices = [min(total_frames - 1, idx) for idx in indices]
                    for idx in sorted(set(indices)):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            resized = cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)
                            frames.append(resized)
            finally:
                cap.release()

        elif ext == '.gif':
            try:
                from PIL import Image
                im = Image.open(media_path)
                n_frames = getattr(im, "n_frames", 1)
                indices = [int(n_frames * i / max(1, count - 1)) for i in range(count)]
                for idx in sorted(set(indices)):
                    im.seek(min(n_frames - 1, idx))
                    rgb = np.array(im.convert("RGB"))
                    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                    frames.append(cv2.resize(bgr, target_size, interpolation=cv2.INTER_AREA))
            except Exception:
                pass

        elif ext in IMAGE_EXTS:
            try:
                img = cv2.imread(media_path)
                if img is not None:
                    frames.append(cv2.resize(img, target_size, interpolation=cv2.INTER_AREA))
            except Exception:
                pass

        return frames

    @classmethod
    def classify_motion(cls, frames: List[Any]) -> Dict[str, Any]:
        """
        Runs Farneback Optical Flow on sampled frames and classifies motion type.
        Identical to Akitt Pro 4.0 optical flow logic.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return {
                "motion": "unknown",
                "directions": [],
                "tags": [],
                "confidence": 0.0,
                "motion_energy": 0.0
            }

        if len(frames) < 2:
            return {
                "motion": "static" if len(frames) == 1 else "unknown",
                "directions": [],
                "tags": cls.get_bilingual_tags(["static"]) if len(frames) == 1 else [],
                "confidence": 0.5 if len(frames) == 1 else 0.0,
                "motion_energy": 0.0
            }

        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
        shifts = []
        mag_means = []
        divergences = []
        jump_ratios = []
        brightness = [float(np.mean(g)) for g in grays]

        for a, b in zip(grays, grays[1:]):
            flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 2, 13, 2, 5, 1.1, 0)
            fx = flow[..., 0]
            fy = flow[..., 1]
            mag = np.sqrt(fx * fx + fy * fy)
            active = mag > 0.32
            valid = np.count_nonzero(active)

            if valid < mag.size * 0.005:
                shifts.append((0.0, 0.0))
                mag_means.append(0.0)
                divergences.append(0.0)
            else:
                dx = float(np.median(fx[active]))
                dy = float(np.median(fy[active]))
                shifts.append((dx, dy))
                mag_means.append(float(np.mean(np.clip(mag[active], 0, 20))))

                yy, xx = np.indices(fx.shape)
                cx = (xx - fx.shape[1] / 2) / max(1, fx.shape[1] / 2)
                cy = (yy - fx.shape[0] / 2) / max(1, fx.shape[0] / 2)
                radial = (fx - dx) * cx + (fy - dy) * cy
                divergences.append(float(np.mean(radial[active])))

            jump_ratios.append(float(np.mean(cv2.absdiff(a, b) > 35)))

        mean_energy = float(np.mean(mag_means))
        motion = "unknown"
        directions = []
        raw_tags = []
        confidence = 0.0

        if mean_energy < 0.14:
            motion = "static"
            confidence = 0.85
            raw_tags = ["static"]
        else:
            shifts_arr = np.array(shifts, dtype=float)
            dx = shifts_arr[:, 0]
            dy = shifts_arr[:, 1]
            speed = np.sqrt(dx * dx + dy * dy)
            active_idx = speed > 0.2

            xmean = float(np.median(dx[active_idx])) if np.any(active_idx) else 0.0
            ymean = float(np.median(dy[active_idx])) if np.any(active_idx) else 0.0
            x_cons = float(np.mean(np.sign(dx[active_idx]) == np.sign(xmean))) if np.any(active_idx) else 0.0
            y_cons = float(np.mean(np.sign(dy[active_idx]) == np.sign(ymean))) if np.any(active_idx) else 0.0
            radial = float(np.median(divergences)) if divergences else 0.0
            brightness_delta = float(brightness[-1] - brightness[0])

            # Vector reversal count for shake detection
            reversals = 0
            vectors = [v for v in shifts if (v[0]*v[0] + v[1]*v[1]) > 0.03]
            for prev, curr in zip(vectors, vectors[1:]):
                if prev[0] * curr[0] + prev[1] * curr[1] < -0.08:
                    reversals += 1

            if reversals >= 2 and mean_energy >= 0.5:
                motion = "shake"
                confidence = 0.80
                raw_tags = ["shake"]
            elif abs(radial) > 0.35 and abs(radial) > abs(xmean + ymean) * 0.2:
                motion = "zoom_in" if radial > 0 else "zoom_out"
                confidence = 0.70
                raw_tags = [motion]
            elif abs(ymean) > 0.3 and y_cons >= 0.60 and abs(ymean) >= abs(xmean) * 0.75:
                motion = "slide_down" if ymean > 0 else "slide_up"
                confidence = min(0.85, 0.55 + y_cons * 0.25)
                directions = ["down" if ymean > 0 else "up"]
                raw_tags = [motion]
            elif abs(xmean) > 0.3 and x_cons >= 0.60:
                motion = "slide_right" if xmean > 0 else "slide_left"
                confidence = min(0.85, 0.55 + x_cons * 0.25)
                directions = ["right" if xmean > 0 else "left"]
                raw_tags = [motion]
            elif abs(brightness_delta) > 18 and mean_energy < 0.6:
                motion = "fade_in" if brightness_delta > 0 else "fade_out"
                confidence = 0.65
                raw_tags = [motion]
            elif np.mean(jump_ratios) > 0.3 and mean_energy > 0.9:
                motion = "rapid_cuts"
                confidence = 0.60
                raw_tags = ["rapid_cuts"]
            else:
                motion = "animated"
                confidence = 0.40
                raw_tags = ["animated"]

        bilingual_tags = cls.get_bilingual_tags(raw_tags)

        return {
            "motion": motion,
            "directions": directions,
            "tags": bilingual_tags,
            "confidence": round(confidence, 3),
            "motion_energy": round(mean_energy, 3)
        }

    @classmethod
    def inspect_setting_file(cls, setting_path: str) -> List[str]:
        """
        Inspects node types inside DaVinci Resolve .setting Lua files.
        Detects TextPlus, BezierSpline, Shake, Glow, Transform, etc.
        """
        if not os.path.isfile(setting_path) or not setting_path.lower().endswith(".setting"):
            return []

        tags = set()
        try:
            with open(setting_path, "r", encoding="utf-8", errors="ignore") as f:
                snippet = f.read(1_500_000).lower()

            if "textplus" in snippet or "styledtext" in snippet or "follower" in snippet:
                tags.add("text")
            if "bezierspline" in snippet or "keyframes" in snippet:
                tags.add("animated")
            if "shake" in snippet or "perturb" in snippet:
                tags.add("shake")
            if "glow" in snippet or "softglow" in snippet:
                tags.add("glow")
            if "transform" in snippet or "dve" in snippet:
                tags.add("animated")
        except Exception:
            pass

        return cls.get_bilingual_tags(list(tags))

    @classmethod
    def get_bilingual_tags(cls, raw_tags: List[str]) -> List[str]:
        """Expands raw motion/category tags into complete English & Vietnamese keywords."""
        result = set()
        for t in raw_tags:
            result.add(t)
            mapped = BILINGUAL_TAG_MAP.get(t.lower(), [])
            for m in mapped:
                result.add(m)
        return sorted(list(result))

    @classmethod
    def analyze_asset(cls, file_path: str) -> Dict[str, Any]:
        """
        One-stop analyzer for any asset: video, image, or .setting file.
        Returns motion classification, detected nodes, and consolidated bilingual tags.
        """
        if not os.path.isfile(file_path):
            return {"motion": "none", "tags": []}

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".setting":
            setting_tags = cls.inspect_setting_file(file_path)
            # Check if there is an adjacent video or gif preview
            base_no_ext = os.path.splitext(file_path)[0]
            for preview_ext in (".mp4", ".mov", ".gif", ".webm"):
                preview_file = base_no_ext + preview_ext
                if os.path.isfile(preview_file):
                    frames = cls.sample_frames(preview_file)
                    motion_res = cls.classify_motion(frames)
                    combined_tags = sorted(list(set(setting_tags + motion_res.get("tags", []))))
                    motion_res["tags"] = combined_tags
                    return motion_res

            return {
                "motion": "setting",
                "directions": [],
                "tags": setting_tags,
                "confidence": 0.9,
                "motion_energy": 0.0
            }

        elif ext in VIDEO_EXTS or ext == '.gif':
            frames = cls.sample_frames(file_path)
            return cls.classify_motion(frames)

        else:
            return {"motion": "static", "tags": cls.get_bilingual_tags(["static"])}
