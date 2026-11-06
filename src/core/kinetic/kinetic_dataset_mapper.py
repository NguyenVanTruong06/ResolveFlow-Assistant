"""
src/core/kinetic/kinetic_dataset_mapper.py
=============================================================================
Pure Python Module bóc tách, thẩm định và ánh xạ dữ liệu từ vựng (Kinetic Words)
từ cache Whisper v3 sang dòng thời gian bản dựng cuối (Final Cut Timeline).

Nguyên tắc kiến trúc:
1. Thuần túy thư viện chuẩn Python (Pure Python stdlib: math, fractions, dataclasses, typing).
2. Tái sử dụng hợp đồng thời gian time_contracts (RationalFPS, pts_to_frame).
3. Tuyệt đối KHÔNG import Qt (PySide6), filesystem IO, cache, network, hay AI models.
4. Caller (Worker/Loader) chịu trách nhiệm xác thực cache và source file identity (SHA-256).
   Module này thuần túy thực thi thẩm định cấu trúc (finite bounds, parent-child bounds,
   numeric validity) và thuật toán hình học ánh xạ thời gian (exact time mapping).
=============================================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from src.core.contracts.time_contracts import RationalFPS, pts_to_frame


# =============================================================================
# 1. HELPER VALIDATORS
# =============================================================================

def _to_fraction(val: Any, name: str) -> Fraction:
    """Chuyển đổi số sang Fraction chính xác, loại trừ bool, None, NaN, Inf."""
    if type(val) is bool:
        raise TypeError(f"{name} không thể là kiểu bool: {val!r}")
    if val is None:
        raise TypeError(f"{name} không thể là None")
    if isinstance(val, int):
        return Fraction(val, 1)
    if isinstance(val, Fraction):
        return val
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError(f"{name} float phải là số hữu hạn (không NaN/Inf), nhận được: {val}")
        return Fraction(str(val))
    if isinstance(val, str):
        try:
            return Fraction(val.strip())
        except Exception as e:
            raise ValueError(f"Không thể chuyển đổi {name} chuỗi '{val}' sang Fraction: {e}") from e
    raise TypeError(f"Kiểu dữ liệu của {name} không hợp lệ: {type(val).__name__}")


# =============================================================================
# 2. DATA CONTRACTS (FROZEN DATACLASSES WITH STRICT VALIDATION)
# =============================================================================

@dataclass(frozen=True)
class SourceWord:
    """Từ vựng nguồn được trích xuất từ dữ liệu phân tích lời thoại (Whisper)."""
    clip_id: str
    segment_index: int
    word_index: int
    text: str
    start_sec: Fraction
    end_sec: Fraction

    def __post_init__(self):
        if not isinstance(self.clip_id, str) or type(self.clip_id) is bool or not self.clip_id.strip():
            raise ValueError(f"clip_id phải là str không rỗng (không bool), nhận được: {self.clip_id!r}")
        for iname, ival in [("segment_index", self.segment_index), ("word_index", self.word_index)]:
            if type(ival) is not int:
                raise TypeError(f"{iname} phải là int nguyên thủy (không bool), nhận được: {ival!r} ({type(ival).__name__})")
            if ival < 0:
                raise ValueError(f"{iname} phải >= 0, nhận được: {ival}")
        if not isinstance(self.text, str) or type(self.text) is bool or not self.text.strip():
            raise ValueError(f"text phải là str không rỗng (không bool), nhận được: {self.text!r}")
        
        s_sec = _to_fraction(self.start_sec, "start_sec")
        e_sec = _to_fraction(self.end_sec, "end_sec")
        if s_sec < 0:
            raise ValueError(f"start_sec âm (< 0): {s_sec}")
        if e_sec <= s_sec:
            raise ValueError(f"end_sec ({e_sec}) phải lớn hơn start_sec ({s_sec})")
        
        if type(self.start_sec) is not Fraction:
            object.__setattr__(self, "start_sec", s_sec)
        if type(self.end_sec) is not Fraction:
            object.__setattr__(self, "end_sec", e_sec)


@dataclass(frozen=True)
class WordPlacement:
    """Hình học bố trí một đoạn cắt video trên timeline dựng cuối."""
    clip_id: str
    instance_id: str
    src_in_sec: Fraction
    src_out_sec: Fraction
    rec_in_frame: int
    rec_out_frame: int
    applied_speed: Fraction

    def __post_init__(self):
        if not isinstance(self.clip_id, str) or type(self.clip_id) is bool or not self.clip_id.strip():
            raise ValueError(f"clip_id phải là str không rỗng (không bool), nhận được: {self.clip_id!r}")
        if not isinstance(self.instance_id, str) or type(self.instance_id) is bool or not self.instance_id.strip():
            raise ValueError(f"instance_id phải là str không rỗng (không bool), nhận được: {self.instance_id!r}")

        for fname, fval in [("rec_in_frame", self.rec_in_frame), ("rec_out_frame", self.rec_out_frame)]:
            if type(fval) is not int:
                raise TypeError(f"{fname} phải là int nguyên thủy (không bool, không float), nhận được: {fval!r} ({type(fval).__name__})")
        if self.rec_in_frame < 0:
            raise ValueError(f"rec_in_frame âm: {self.rec_in_frame}")
        if self.rec_out_frame <= self.rec_in_frame:
            raise ValueError(f"rec_out_frame ({self.rec_out_frame}) phải lớn hơn rec_in_frame ({self.rec_in_frame})")

        s_in = _to_fraction(self.src_in_sec, "src_in_sec")
        s_out = _to_fraction(self.src_out_sec, "src_out_sec")
        speed = _to_fraction(self.applied_speed, "applied_speed")

        if s_in < 0:
            raise ValueError(f"src_in_sec âm: {s_in}")
        if s_out <= s_in:
            raise ValueError(f"src_out_sec ({s_out}) phải lớn hơn src_in_sec ({s_in})")
        if speed <= 0:
            raise ValueError(f"applied_speed phải dương (> 0), nhận được: {speed}")

        if type(self.src_in_sec) is not Fraction:
            object.__setattr__(self, "src_in_sec", s_in)
        if type(self.src_out_sec) is not Fraction:
            object.__setattr__(self, "src_out_sec", s_out)
        if type(self.applied_speed) is not Fraction:
            object.__setattr__(self, "applied_speed", speed)


@dataclass(frozen=True)
class MappedWord:
    """Từ vựng đã được ánh xạ chính xác sang hệ tọa độ khung hình timeline."""
    index: int
    clip_id: str
    instance_id: str
    segment_index: int
    word_index: int
    text: str
    source_start_sec: Fraction
    source_end_sec: Fraction
    record_start_frame: int
    record_end_frame: int
    clipped: bool

    def __post_init__(self):
        if not isinstance(self.clip_id, str) or type(self.clip_id) is bool or not self.clip_id.strip():
            raise ValueError(f"clip_id phải là str không rỗng: {self.clip_id!r}")
        if not isinstance(self.instance_id, str) or type(self.instance_id) is bool or not self.instance_id.strip():
            raise ValueError(f"instance_id phải là str không rỗng: {self.instance_id!r}")
        if not isinstance(self.text, str) or type(self.text) is bool or not self.text.strip():
            raise ValueError(f"text phải là str không rỗng: {self.text!r}")

        for iname, ival in [
            ("index", self.index),
            ("segment_index", self.segment_index),
            ("word_index", self.word_index),
            ("record_start_frame", self.record_start_frame),
            ("record_end_frame", self.record_end_frame),
        ]:
            if type(ival) is not int:
                raise TypeError(f"{iname} phải là int nguyên thủy (không bool, không float), nhận được: {ival!r} ({type(ival).__name__})")
        
        if self.index < 0:
            raise ValueError(f"index âm: {self.index}")
        if self.record_start_frame < 0:
            raise ValueError(f"record_start_frame âm: {self.record_start_frame}")
        if self.record_end_frame <= self.record_start_frame:
            raise ValueError(f"record_end_frame ({self.record_end_frame}) phải lớn hơn record_start_frame ({self.record_start_frame})")
        
        if type(self.clipped) is not bool:
            raise TypeError(f"clipped phải là bool, nhận được: {self.clipped!r}")

        s_s = _to_fraction(self.source_start_sec, "source_start_sec")
        s_e = _to_fraction(self.source_end_sec, "source_end_sec")
        if s_s < 0 or s_e <= s_s:
            raise ValueError(f"Thời gian nguồn không hợp lệ: [{s_s}, {s_e}]")

        if type(self.source_start_sec) is not Fraction:
            object.__setattr__(self, "source_start_sec", s_s)
        if type(self.source_end_sec) is not Fraction:
            object.__setattr__(self, "source_end_sec", s_e)


@dataclass(frozen=True)
class KineticDataset:
    """Tập dữ liệu hoàn chỉnh cho kịch bản Kinetic Typography của bản dựng."""
    words: Tuple[MappedWord, ...]
    warnings: Tuple[str, ...]
    timing_mode: str = "word_timestamps"

    def __post_init__(self):
        if not isinstance(self.words, tuple):
            object.__setattr__(self, "words", tuple(self.words))
        if not isinstance(self.warnings, tuple):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        if not isinstance(self.timing_mode, str) or type(self.timing_mode) is bool:
            raise TypeError(f"timing_mode phải là str, nhận được: {self.timing_mode!r}")


# =============================================================================
# 3. EXTRACTION & VALIDATION
# =============================================================================

def extract_source_words(
    raw_subtitles: Any,
    *,
    clip_id: str,
    duration_sec: Union[int, float, Fraction, str],
) -> Tuple[SourceWord, ...]:
    """
    Bóc tách và thẩm định danh sách từ vựng nguồn từ mảng raw_subtitles.

    Precondition:
        Caller chịu trách nhiệm xác thực tính toàn vẹn của cache và tệp nguồn (SHA-256).
        Hàm này thẩm định cấu trúc, giới hạn thời gian hữu hạn và quan hệ cha-con.

    Quy tắc thẩm định:
    - clip_id bắt buộc là chuỗi không rỗng (không bool).
    - duration_sec bắt buộc là số dương hữu hạn (> 0), không chấp nhận unknown/invalid.
    - Typegate TRƯỚC empty check: raw_subtitles bắt buộc là list hoặc tuple (không None, False, 0, dict, str).
      Chỉ khi isinstance(raw_subtitles, (list, tuple)) và len == 0 mới trả về () (no-dialogue).
    - Segment có nội dung thoại nhưng thiếu mảng 'words' -> ném ValueError("word_timestamps_missing").
    - Numeric values: không bool, hữu hạn, 0 <= start < end <= duration_sec.
    - Từ con phải nằm trong giới hạn của câu cha: seg.start <= w.start < w.end <= seg.end.
    - Text phải là chuỗi ký tự hiển thị được (sau khi strip không rỗng).
    """
    if not isinstance(clip_id, str) or type(clip_id) is bool or not clip_id.strip():
        raise ValueError(f"clip_id bắt buộc phải là chuỗi không rỗng, nhận được: {clip_id!r}")

    dur_frac = _to_fraction(duration_sec, "duration_sec")
    if dur_frac <= 0:
        raise ValueError(f"duration_sec phải là số dương (> 0), nhận được: {duration_sec}")

    # Typegate TRƯỚC empty check để chặn falsy invalid (None, False, 0, {}, "")
    if not isinstance(raw_subtitles, (list, tuple)) or type(raw_subtitles) is bool:
        raise TypeError(f"raw_subtitles phải là list hoặc tuple, nhận được: {type(raw_subtitles).__name__} ({raw_subtitles!r})")

    if len(raw_subtitles) == 0:
        return ()

    extracted_words: List[SourceWord] = []

    for seg_idx, seg in enumerate(raw_subtitles):
        if not isinstance(seg, dict):
            raise TypeError(f"Segment #{seg_idx} không phải là dict: {type(seg).__name__}")

        raw_words = seg.get("words")
        seg_text_raw = seg.get("text", "")
        if not isinstance(seg_text_raw, str) or type(seg_text_raw) is bool:
            raise TypeError(f"Segment #{seg_idx} text phải là str nguyên thủy, nhận được: {type(seg_text_raw).__name__}")
        seg_text = seg_text_raw.strip()

        # Nếu segment có text nhưng không có mảng words -> strict policy: ném lỗi
        if raw_words is None or len(raw_words) == 0:
            if seg_text:
                raise ValueError(
                    f"word_timestamps_missing: Segment #{seg_idx} có thoại ('{seg_text[:30]}') "
                    f"nhưng không có mảng word timestamps."
                )
            # Segment rỗng không có text và không có words -> bỏ qua
            continue

        if not isinstance(raw_words, (list, tuple)) or type(raw_words) is bool:
            raise TypeError(f"Trường 'words' trong segment #{seg_idx} phải là list/tuple")

        # Thẩm định thời gian của segment cha
        seg_start = _to_fraction(seg.get("start"), f"Segment #{seg_idx} start")
        seg_end = _to_fraction(seg.get("end"), f"Segment #{seg_idx} end")

        if seg_start < 0:
            raise ValueError(f"Segment #{seg_idx} start âm (< 0): {seg_start}")
        if seg_end <= seg_start:
            raise ValueError(f"Segment #{seg_idx} end ({seg_end}) phải lớn hơn start ({seg_start})")
        if seg_end > dur_frac:
            raise ValueError(f"Segment #{seg_idx} end ({seg_end}) vượt quá duration_sec ({dur_frac})")

        # Thẩm định từng từ con
        for w_idx, w in enumerate(raw_words):
            if not isinstance(w, dict):
                raise TypeError(f"Từ #{w_idx} trong segment #{seg_idx} không phải là dict")

            w_text_raw = w.get("word") if "word" in w else w.get("text")
            if not isinstance(w_text_raw, str) or type(w_text_raw) is bool:
                raise TypeError(
                    f"Từ #{w_idx} segment #{seg_idx} text phải là str nguyên thủy, "
                    f"nhận được: {type(w_text_raw).__name__}"
                )

            w_text = w_text_raw.strip()
            if not w_text:
                raise ValueError(f"Từ #{w_idx} segment #{seg_idx} có text rỗng sau khi strip")

            w_start = _to_fraction(w.get("start"), f"Word #{w_idx} segment #{seg_idx} start")
            w_end = _to_fraction(w.get("end"), f"Word #{w_idx} segment #{seg_idx} end")

            # Finite bounds kiểm tra
            if w_start < 0:
                raise ValueError(f"Word #{w_idx} segment #{seg_idx} start âm (< 0): {w_start}")
            if w_end <= w_start:
                raise ValueError(f"Word #{w_idx} segment #{seg_idx} end ({w_end}) phải lớn hơn start ({w_start})")
            if w_end > dur_frac:
                raise ValueError(f"Word #{w_idx} segment #{seg_idx} end ({w_end}) vượt quá duration_sec ({dur_frac})")

            # Quan hệ cha - con nghiêm ngặt (không arbitrary tolerance ngoài nguồn)
            if w_start < seg_start:
                raise ValueError(
                    f"Word #{w_idx} start ({w_start}) nằm ngoài segment #{seg_idx} start ({seg_start})"
                )
            if w_end > seg_end:
                raise ValueError(
                    f"Word #{w_idx} end ({w_end}) nằm ngoài segment #{seg_idx} end ({seg_end})"
                )

            extracted_words.append(
                SourceWord(
                    clip_id=clip_id,
                    segment_index=seg_idx,
                    word_index=w_idx,
                    text=w_text,
                    start_sec=w_start,
                    end_sec=w_end,
                )
            )

    return tuple(extracted_words)


# =============================================================================
# 4. TIMELINE MAPPING
# =============================================================================

def map_words_to_timeline(
    source_words_by_clip: Mapping[str, Sequence[SourceWord]],
    placements: Sequence[WordPlacement],
    *,
    fps: Union[int, float, Fraction, str, RationalFPS],
) -> KineticDataset:
    """
    Ánh xạ danh sách từ vựng nguồn vào dòng thời gian bản dựng cuối (Timeline).

    Args:
        source_words_by_clip: Từ điển các SourceWord theo clip_id.
            Mọi clip_id trong placements BẮT BUỘC phải tồn tại key trong từ điển này.
            Nếu clip không có thoại, giá trị tương ứng phải là tuple rỗng ().
        placements: Danh sách các vị trí cắt dựng hiệu dụng (WordPlacement).
        fps: Tốc độ khung hình (RationalFPS hoặc kiểu hợp lệ có thể chuyển đổi).

    Returns:
        KineticDataset chứa các MappedWord đã gán số nguyên frame chính xác,
        kèm danh sách cảnh báo (warnings).
    """
    fps_rat = RationalFPS.from_value(fps)

    # 1. Thẩm định source_words_by_clip
    if not isinstance(source_words_by_clip, Mapping) or type(source_words_by_clip) is bool:
        raise TypeError(f"source_words_by_clip phải là Mapping (dict-like), nhận được: {type(source_words_by_clip).__name__}")

    for k, words_seq in source_words_by_clip.items():
        if not isinstance(k, str) or type(k) is bool or not k.strip():
            raise ValueError(f"clip_id key trong source_words_by_clip phải là str không rỗng, nhận được: {k!r}")
        if not isinstance(words_seq, (list, tuple)) or type(words_seq) is bool:
            raise TypeError(f"Giá trị cho clip_id '{k}' trong source_words_by_clip phải là Sequence (list/tuple), nhận được: {type(words_seq).__name__}")
        for w_idx, w in enumerate(words_seq):
            if not isinstance(w, SourceWord):
                raise TypeError(f"Phần tử #{w_idx} của clip_id '{k}' không phải là SourceWord, nhận được: {type(w).__name__}")
            if w.clip_id != k:
                raise ValueError(f"SourceWord #{w_idx} có clip_id '{w.clip_id}' không khớp với key mapping '{k}'")

    # 2. Thẩm định placements & kiểm tra duplicate instance_id
    if not isinstance(placements, (list, tuple)) or type(placements) is bool:
        raise TypeError(f"placements phải là Sequence (list/tuple), nhận được: {type(placements).__name__}")

    seen_instances = set()
    for pl_idx, pl in enumerate(placements):
        if not isinstance(pl, WordPlacement):
            raise TypeError(f"Phần tử placements #{pl_idx} phải là WordPlacement, nhận được: {type(pl).__name__}")
        
        # Strict contract: Placement clip_id bắt buộc phải có key trong source_words_by_clip
        if pl.clip_id not in source_words_by_clip:
            raise KeyError(f"unknown_clip_id: Placement '{pl.instance_id}' tham chiếu clip_id '{pl.clip_id}' không tồn tại trong source_words_by_clip")

        if pl.instance_id in seen_instances:
            raise ValueError(f"Phát hiện trùng lặp instance_id: '{pl.instance_id}'")
        seen_instances.add(pl.instance_id)

        # Kiểm tra tính nhất quán hình học (Geometric consistency check)
        rec_dur_frames = pl.rec_out_frame - pl.rec_in_frame
        src_dur_sec = pl.src_out_sec - pl.src_in_sec
        eff_sec = src_dur_sec / pl.applied_speed
        expected_frames = pts_to_frame(eff_sec, fps_rat)
        if abs(rec_dur_frames - expected_frames) > 1:
            raise ValueError(
                f"Hình học placement '{pl.instance_id}' không nhất quán: "
                f"rec_frames={rec_dur_frames} lệch quá 1 frame so với expected={expected_frames} "
                f"(src_dur={src_dur_sec}, speed={pl.applied_speed}, fps={fps_rat})"
            )

    # 3. Thực hiện ánh xạ từng placement
    mapped_candidates: List[Tuple[int, int, str, int, MappedWord]] = []
    warnings: List[str] = []

    for pl in placements:
        clip_words = source_words_by_clip[pl.clip_id]
        outside_count = 0

        for w in clip_words:
            # Kiểm tra xem từ có nằm trong phạm vi cắt [src_in_sec, src_out_sec] không
            if w.end_sec <= pl.src_in_sec or w.start_sec >= pl.src_out_sec:
                outside_count += 1
                continue

            inter_start = max(w.start_sec, pl.src_in_sec)
            inter_end = min(w.end_sec, pl.src_out_sec)
            if inter_start >= inter_end:
                outside_count += 1
                continue

            # Kiểm tra xem từ có bị cắt tỉa ở biên không (boundary trimming)
            is_clipped = (inter_start > w.start_sec) or (inter_end < w.end_sec)
            if is_clipped:
                warnings.append(
                    f"Từ '{w.text}' ({w.clip_id}:{w.segment_index}:{w.word_index}) "
                    f"bị cắt ở biên placement '{pl.instance_id}' [{pl.src_in_sec}, {pl.src_out_sec}]"
                )

            # Quy đổi thời gian tương đối sang frames
            delta_start = (inter_start - pl.src_in_sec) / pl.applied_speed
            delta_end = (inter_end - pl.src_in_sec) / pl.applied_speed

            offset_start_f = pts_to_frame(delta_start, fps_rat)
            offset_end_f = pts_to_frame(delta_end, fps_rat)

            rec_start_f = int(pl.rec_in_frame + offset_start_f)
            rec_end_f = int(pl.rec_in_frame + offset_end_f)

            # Clamp vào giới hạn record của placement
            rec_start_f = max(pl.rec_in_frame, min(rec_start_f, pl.rec_out_frame))
            rec_end_f = max(pl.rec_in_frame, min(rec_end_f, pl.rec_out_frame))

            # Nếu sau khi quantize thời lượng bị co về 0 frames:
            # TUYỆT ĐỐI KHÔNG mở rộng giả 1 frame; ghi nhận warning và loại bỏ
            if rec_end_f <= rec_start_f:
                warnings.append(
                    f"drop_quantized_empty: Từ '{w.text}' ({w.clip_id}) trong placement '{pl.instance_id}' "
                    f"bị lượng tử hóa thành 0 frame [{rec_start_f}, {rec_end_f}]"
                )
                continue

            mw = MappedWord(
                index=0,  # Sẽ được đánh số lại sau khi sort
                clip_id=w.clip_id,
                instance_id=pl.instance_id,
                segment_index=w.segment_index,
                word_index=w.word_index,
                text=w.text,
                source_start_sec=w.start_sec,
                source_end_sec=w.end_sec,
                record_start_frame=rec_start_f,
                record_end_frame=rec_end_f,
                clipped=is_clipped,
            )
            mapped_candidates.append(
                (rec_start_f, rec_end_f, pl.instance_id, len(mapped_candidates), mw)
            )

        if outside_count > 0:
            warnings.append(
                f"{outside_count} từ nằm ngoài phạm vi cắt của placement '{pl.instance_id}' ({pl.clip_id})"
            )

    # 4. Sắp xếp theo dòng thời gian record thực tế (deterministic tie-breaking)
    # Thứ tự: record_start_frame -> record_end_frame -> instance_id -> original_candidate_order
    mapped_candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    # 5. Đánh số index toàn cục liên tục 0..N-1
    final_mapped_words: List[MappedWord] = []
    for g_idx, item in enumerate(mapped_candidates):
        orig_mw = item[4]
        final_mapped_words.append(replace(orig_mw, index=g_idx))

    return KineticDataset(
        words=tuple(final_mapped_words),
        warnings=tuple(warnings),
        timing_mode="word_timestamps",
    )
