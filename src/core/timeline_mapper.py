"""
src/core/timeline_mapper.py
Bộ ánh xạ thời gian thống nhất (TimelineMapper) cho ResolveFlow.

Quy tắc:
1. Tính TOÀN BỘ bằng frame nguyên dựa trên FPS (hỗ trợ phân số chuẩn NTSC như 30000/1001, 24000/1001).
2. Là NGUỒN DUY NHẤT về vị trí cursor trên timeline. Không nơi nào khác được tự cộng dồn cursor.
3. Hỗ trợ 2 chế độ speed: 'timeremap' hoặc 'off' (mặc định 'off' lưu QSettings).
4. Cung cấp hàm ánh xạ ngược xuôi src_to_timeline và segments_containing cho phụ đề và video.
"""

import os
import logging
from fractions import Fraction
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def get_speed_mode() -> str:
    """Đọc chế độ xử lý tốc độ (speed_mode) từ QSettings, mặc định là 'off'."""
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("ChunDVC", "ResolveFlow")
        val = str(settings.value("timeline/speed_mode", "off")).strip().lower()
        if val in ("timeremap", "off"):
            return val
    except Exception as e:
        logger.debug(f"Không thể đọc speed_mode từ QSettings: {e}")
    return "off"


def set_speed_mode(mode: str) -> None:
    """Ghi cấu hình speed_mode vào QSettings ('timeremap' hoặc 'off')."""
    clean_mode = str(mode).strip().lower()
    if clean_mode not in ("timeremap", "off"):
        raise ValueError(f"speed_mode không hợp lệ: {mode}. Chỉ hỗ trợ 'timeremap' hoặc 'off'.")
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("ChunDVC", "ResolveFlow")
        settings.setValue("timeline/speed_mode", clean_mode)
    except Exception as e:
        logger.warning(f"Không thể ghi speed_mode vào QSettings: {e}")


@dataclass
class PlacedSegment:
    """Đại diện một phân đoạn video được định vị trên timeline."""
    segment_index: int
    clip_id: str
    src_in_sec: float
    src_out_sec: float
    speed: float = 1.0
    role: str = "build"
    pad_sec: float = 0.3
    clip_duration_sec: float = 0.0
    start_tc_frames: int = 0
    clip_name: str = ""
    file_path: str = ""
    title: str = ""

    # Thuộc tính frame nguyên do TimelineMapper tính toán
    applied_speed: float = 1.0
    src_in_frames: int = 0
    src_out_frames: int = 0
    src_dur_frames: int = 0
    rec_in_frames: int = 0
    rec_out_frames: int = 0
    rec_dur_frames: int = 0
    fps: float = 30.0

    @property
    def timeline_in_sec(self) -> float:
        return round(self.rec_in_frames / self.fps, 4) if self.fps > 0 else 0.0

    @property
    def timeline_out_sec(self) -> float:
        return round(self.rec_out_frames / self.fps, 4) if self.fps > 0 else 0.0

    @property
    def timeline_dur_sec(self) -> float:
        return round(self.rec_dur_frames / self.fps, 4) if self.fps > 0 else 0.0



class TimelineMapper:
    """
    Bộ định vị & ánh xạ thời gian trục timeline chuẩn frame nguyên.
    Đảm bảo:
    - rec_in của segment k+1 == rec_out của segment k (tuyệt đối không hở, không chồng).
    - Phụ đề, SFX, hình ảnh và video dùng chung một nguồn tọa độ timeline duy nhất.
    """

    def __init__(
        self,
        fps: Union[float, Fraction, int, str] = 30.0,
        speed_mode: Optional[str] = None
    ):
        self.raw_fps = fps
        self.fps_fraction = self._parse_fps_fraction(fps)
        self.fps: float = float(self.fps_fraction)

        self.speed_mode: str = (speed_mode or get_speed_mode()).strip().lower()
        if self.speed_mode not in ("timeremap", "off"):
            logger.warning(f"speed_mode '{self.speed_mode}' không hợp lệ, chuyển về 'off'.")
            self.speed_mode = "off"

        self.segments: List[PlacedSegment] = []
        self.cursor_frames: int = 0
        self.warnings: List[str] = []

    @staticmethod
    def _parse_fps_fraction(fps_input: Union[float, Fraction, int, str]) -> Fraction:
        """Chuẩn hóa FPS sang Fraction chính xác."""
        if isinstance(fps_input, Fraction):
            return fps_input
        if isinstance(fps_input, int):
            return Fraction(fps_input, 1)
        
        try:
            f_val = float(fps_input)
            if abs(f_val - 29.97) < 0.02:
                return Fraction(30000, 1001)
            elif abs(f_val - 23.976) < 0.02:
                return Fraction(24000, 1001)
            elif abs(f_val - 59.94) < 0.02:
                return Fraction(60000, 1001)
            elif abs(f_val - round(f_val)) < 1e-4:
                return Fraction(round(f_val), 1)
            else:
                return Fraction(f_val).limit_denominator(10000)
        except Exception:
            return Fraction(30, 1)

    def add_segment(
        self,
        clip_id: str,
        src_in_sec: float,
        src_out_sec: float,
        speed: float = 1.0,
        role: str = "build",
        pad_sec: float = 0.3,
        clip_duration_sec: float = 0.0,
        start_tc_frames: int = 0,
        clip_name: str = "",
        file_path: str = "",
        title: str = ""
    ) -> PlacedSegment:
        """
        Thêm một segment vào timeline và tính toán vị trí frame nguyên.
        """
        seg_idx = len(self.segments)

        # Xử lý speed_mode
        req_speed = float(speed if speed > 0.01 else 1.0)
        if self.speed_mode == "off":
            if abs(req_speed - 1.0) > 1e-3:
                warn = f"speed_mode='off': Ép tốc độ clip {clip_id} ({clip_name}) từ {req_speed:.2f}x về 1.0x."
                self.warnings.append(warn)
                logger.warning(warn)
            applied_speed = 1.0
        else:
            applied_speed = req_speed

        fps_val = self.fps

        # 1. Tính toán source frames kèm padding
        src_in_f = max(0, round((src_in_sec - pad_sec) * fps_val))
        if clip_duration_sec > 0:
            max_clip_f = round(clip_duration_sec * fps_val)
            src_out_f = min(max_clip_f, round((src_out_sec + pad_sec) * fps_val))
        else:
            src_out_f = round((src_out_sec + pad_sec) * fps_val)

        # Đảm bảo độ dài tối thiểu 1 frame
        if src_out_f <= src_in_f:
            src_out_f = src_in_f + 1

        src_dur_f = src_out_f - src_in_f

        # 2. Tính toán timeline recording frames
        rec_dur_f = max(1, round(src_dur_f / applied_speed))
        rec_in_f = self.cursor_frames
        rec_out_f = rec_in_f + rec_dur_f

        # Cập nhật cursor timeline
        self.cursor_frames = rec_out_f

        placed = PlacedSegment(
            segment_index=seg_idx,
            clip_id=clip_id,
            src_in_sec=src_in_sec,
            src_out_sec=src_out_sec,
            speed=req_speed,
            role=role,
            pad_sec=pad_sec,
            clip_duration_sec=clip_duration_sec,
            start_tc_frames=start_tc_frames,
            clip_name=clip_name,
            file_path=file_path,
            title=title,
            applied_speed=applied_speed,
            src_in_frames=src_in_f,
            src_out_frames=src_out_f,
            src_dur_frames=src_dur_f,
            rec_in_frames=rec_in_f,
            rec_out_frames=rec_out_f,
            rec_dur_frames=rec_dur_f,
            fps=fps_val
        )
        self.segments.append(placed)
        return placed

    def src_to_timeline(
        self,
        segment_index: int,
        t_src_sec: float,
        clamp_to_segment: bool = True
    ) -> Optional[int]:
        """
        Ánh xạ một mốc thời gian nguồn (t_src_sec) sang chỉ số frame trên timeline.
        Quy tắc:
        - Nếu clamp_to_segment=True và t_src_sec nằm ngoài [src_in_sec, src_out_sec] -> trả về None.
        - Dùng applied_speed thực tế của segment để co giãn frame tương ứng.
        """
        if segment_index < 0 or segment_index >= len(self.segments):
            return None

        seg = self.segments[segment_index]
        tol = 0.001  # Dung sai sai số làm tròn float 1ms

        pad = 0.0 if clamp_to_segment else seg.pad_sec
        if t_src_sec < (seg.src_in_sec - pad - tol) or t_src_sec > (seg.src_out_sec + pad + tol):
            return None

        # Tính khoảng cách frame từ điểm cắt nguồn đầu tiên (đã bao gồm pad)
        t_src_f = round(t_src_sec * self.fps)
        offset_src_f = t_src_f - seg.src_in_frames

        # Co giãn theo speed và cộng vào rec_in_frames của segment
        timeline_offset_f = round(offset_src_f / seg.applied_speed)
        rec_f = seg.rec_in_frames + timeline_offset_f
        return rec_f

    def src_to_timeline_sec(
        self,
        segment_index: int,
        t_src_sec: float,
        clamp_to_segment: bool = True
    ) -> Optional[float]:
        """Chuyển đổi thời gian nguồn sang thời gian giây trên timeline."""
        rec_f = self.src_to_timeline(segment_index, t_src_sec, clamp_to_segment=clamp_to_segment)
        if rec_f is None:
            return None
        return round(rec_f / self.fps, 4)

    def segments_containing(self, clip_identifier: str, t_src_sec: float) -> List[Tuple[int, PlacedSegment]]:
        """
        Tìm tất cả các segment trên timeline chứa mốc thời gian nguồn t_src_sec của clip.
        Hỗ trợ so khớp qua clip_id, clip_name, hoặc tên file.
        """
        results: List[Tuple[int, PlacedSegment]] = []
        tol = 0.001
        clean_id = clip_identifier.strip().lower()

        for idx, seg in enumerate(self.segments):
            matched = (
                seg.clip_id.lower() == clean_id or
                seg.clip_name.lower() == clean_id or
                os.path.basename(seg.file_path).lower() == clean_id or
                os.path.splitext(seg.clip_name.lower())[0] == clean_id or
                os.path.splitext(os.path.basename(seg.file_path).lower())[0] == clean_id
            )
            if matched:
                if (seg.src_in_sec - tol) <= t_src_sec <= (seg.src_out_sec + tol):
                    results.append((idx, seg))
        return results

    def get_total_duration_frames(self) -> int:
        """Tổng số frame trên timeline."""
        return self.cursor_frames

    def get_total_duration_sec(self) -> float:
        """Tổng thời lượng timeline tính bằng giây."""
        return round(self.cursor_frames / self.fps, 4)

    def to_events(self) -> List[Dict[str, Any]]:
        """
        Xuất danh sách events chuẩn bị cho FCPXML / FCP7 XML.
        Mỗi event chứa đầy đủ thông số frame nguyên và metadata.
        """
        events = []
        for seg in self.segments:
            events.append({
                "clip_id": seg.clip_id,
                "video_path": seg.file_path,
                "clip_name": seg.clip_name,
                "name": seg.clip_name,
                "src_in": seg.src_in_frames / self.fps,
                "src_out": seg.src_out_frames / self.fps,
                "rec_in": seg.rec_in_frames / self.fps,
                "rec_out": seg.rec_out_frames / self.fps,
                "src_in_frames": seg.src_in_frames,
                "src_out_frames": seg.src_out_frames,
                "src_dur_frames": seg.src_dur_frames,
                "rec_in_frames": seg.rec_in_frames,
                "rec_out_frames": seg.rec_out_frames,
                "rec_dur_frames": seg.rec_dur_frames,
                "start_tc_frames": seg.start_tc_frames,
                "speed": seg.applied_speed,
                "role": seg.role,
                "title": seg.title,
                "track": 1
            })
        return events

    def map_segment_subtitles(
        self,
        seg_idx: int,
        raw_subtitles: List[Dict[str, Any]],
        vpath: str = ""
    ) -> List[Dict[str, Any]]:
        """Ánh xạ danh sách phụ đề cho một segment đã đặt trên timeline."""
        return add_subtitles_for_range(self, seg_idx, raw_subtitles, vpath=vpath)


def add_subtitles_for_range(
    mapper_or_vpath: Any,
    seg_idx_or_start: Any = None,
    raw_subs_or_end: Any = None,
    base_rec: float = 0.0,
    speed_val: float = 1.0,
    vpath: str = "",
    subtitles_by_path: Optional[Dict[str, List[Dict[str, Any]]]] = None
) -> List[Dict[str, Any]]:
    """
    Hàm ánh xạ phụ đề thống nhất giữa convert_plan_to_resolve_timeline và get_final_cut_words.
    Hỗ trợ 2 kiểu gọi:
    1. Chuẩn TimelineMapper: add_subtitles_for_range(mapper, seg_idx, raw_subs, vpath=...)
    2. Chuẩn truyền tham số lẻ: add_subtitles_for_range(vpath, src_start, src_end, base_rec, speed_val, subtitles_by_path=...)
    """
    if isinstance(mapper_or_vpath, TimelineMapper):
        mapper: TimelineMapper = mapper_or_vpath
        seg_idx: int = int(seg_idx_or_start)
        raw_subs: List[Dict[str, Any]] = raw_subs_or_end or []
        if seg_idx < 0 or seg_idx >= len(mapper.segments):
            return []
        placed = mapper.segments[seg_idx]
        actual_vpath = vpath or placed.file_path

        out_subs = []
        for s in raw_subs:
            s_st = float(s.get("start", 0.0))
            s_en = float(s.get("end", 0.0))
            if s_en > placed.src_in_sec and s_st < placed.src_out_sec:
                sub_st_sec = mapper.src_to_timeline_sec(seg_idx, max(s_st, placed.src_in_sec))
                sub_en_sec = mapper.src_to_timeline_sec(seg_idx, min(s_en, placed.src_out_sec))
                if sub_st_sec is None or sub_en_sec is None or sub_en_sec <= sub_st_sec:
                    continue

                sub_words = []
                words_data = s.get("words", [])
                if words_data:
                    for w in words_data:
                        w_st = float(w.get("start", s_st))
                        w_en = float(w.get("end", s_en))
                        w_txt = str(w.get("word", "")).strip()
                        if w_txt and w_en > placed.src_in_sec and w_st < placed.src_out_sec:
                            w_rec_st = mapper.src_to_timeline_sec(seg_idx, max(w_st, placed.src_in_sec))
                            w_rec_en = mapper.src_to_timeline_sec(seg_idx, min(w_en, placed.src_out_sec))
                            if w_rec_st is not None and w_rec_en is not None and w_rec_en > w_rec_st:
                                sub_words.append({
                                    "word": w_txt,
                                    "start": w_rec_st,
                                    "end": w_rec_en
                                })
                else:
                    txt_words = str(s.get("text", "")).strip().split()
                    if txt_words:
                        w_step = (s_en - s_st) / max(len(txt_words), 1)
                        for idx, w_txt in enumerate(txt_words):
                            w_st = s_st + idx * w_step
                            w_en = s_st + (idx + 1) * w_step
                            if w_txt and w_en > placed.src_in_sec and w_st < placed.src_out_sec:
                                w_rec_st = mapper.src_to_timeline_sec(seg_idx, max(w_st, placed.src_in_sec))
                                w_rec_en = mapper.src_to_timeline_sec(seg_idx, min(w_en, placed.src_out_sec))
                                if w_rec_st is not None and w_rec_en is not None and w_rec_en > w_rec_st:
                                    sub_words.append({
                                        "word": w_txt,
                                        "start": w_rec_st,
                                        "end": w_rec_en
                                    })

                out_subs.append({
                    "start": sub_st_sec,
                    "end": sub_en_sec,
                    "text": s.get("text", "").strip(),
                    "words": sub_words,
                    "video_path": actual_vpath
                })
        return out_subs

    # Trường hợp gọi tham số lẻ (legacy / fallback)
    vpath_str = str(mapper_or_vpath)
    src_start = float(seg_idx_or_start or 0.0)
    src_end = float(raw_subs_or_end or 0.0)
    temp_mapper = TimelineMapper(fps=30.0, speed_mode="timeremap")
    temp_mapper.cursor_frames = round(base_rec * 30.0)
    temp_mapper.add_segment(
        clip_id="TEMP",
        src_in_sec=src_start,
        src_out_sec=src_end,
        speed=speed_val,
        pad_sec=0.0,
        file_path=vpath_str
    )
    raw_list = []
    if subtitles_by_path:
        raw_list = subtitles_by_path.get(vpath_str) or []
        if not raw_list:
            v_base = os.path.basename(vpath_str).lower()
            for k, v in subtitles_by_path.items():
                if os.path.basename(k).lower() == v_base:
                    raw_list = v
                    break
    return add_subtitles_for_range(temp_mapper, 0, raw_list, vpath=vpath_str)

