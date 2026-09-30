import os
import re
import wave
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field
from src.core.autocut import seconds_to_timecode, get_video_fps, EDLGenerator

class HookSegment(BaseModel):
    """
    Thông tin phân đoạn highlight trích xuất từ một video nguồn.
    """
    video_path: str
    src_in: float
    src_out: float
    duration: float
    score: float
    reason: str
    text: Optional[str] = ""

class VlogHookConfig(BaseModel):
    """
    Cấu hình bộ tạo Highlight / Intro Teaser tự động từ nhiều video.
    """
    enabled: bool = Field(default=True, description="Bật/tắt tính năng tạo Vlog Hook / Intro")
    clip_duration: float = Field(
        default=2.0, ge=1.0, le=5.0,
        description="Thời lượng trích xuất cho mỗi phân đoạn highlight (giây, thường từ 1.0 - 3.0s)"
    )
    target_total_duration: float = Field(
        default=20.0, ge=5.0, le=60.0,
        description="Tổng thời lượng mục tiêu tối đa cho toàn bộ đoạn teaser (giây)"
    )
    prefer_voice_hooks: bool = Field(
        default=True,
        description="Ưu tiên các câu thoại chứa từ khóa kích thích tò mò (Hook phrases)"
    )

class VlogHookGenerator:
    """
    Bộ động cơ trích xuất điểm nhấn (Smart Vlog Hook/Intro Generator):
    - Quét toàn bộ danh sách video nguồn.
    - Tìm phân đoạn cao trào âm thanh (RMS Peak) hoặc các câu nói mở đầu/tò mò (Hook phrases).
    - Cắt 1 - 3 giây đắt giá nhất từ mỗi video nguồn.
    - Ghép nối liên tục tạo timeline / file EDL độc lập [Project]_Vlog_Intro_Teaser.
    """

    # Danh sách từ khóa Hook / Giật gân / Mở màn Vlog phổ biến (Tiếng Việt & Tiếng Anh)
    HOOK_KEYWORDS_VI = [
        "wow", "bất ngờ", "nhìn này", "không thể tin được", "bí mật", "đặc biệt",
        "chưa từng thấy", "chuyến đi", "hôm nay", "đỉnh cao", "đẹp quá", "quá khủng",
        "khám phá", "trải nghiệm", "kinh ngạc", "nhất định phải", "siêu phẩm", "cực đỉnh",
        "bắt đầu", "chào mừng", "tại sao", "như thế nào", "cái gì", "khủng khiếp", "thực sự"
    ]
    HOOK_KEYWORDS_EN = [
        "wow", "unbelievable", "look at this", "check this out", "secret", "never seen before",
        "insane", "crazy", "amazing", "today", "adventure", "journey", "ultimate", "oh my god",
        "you won't believe", "welcome", "why", "how", "what", "first time", "incredible"
    ]

    @classmethod
    def find_audio_energy_peak(
        cls,
        wav_path: str,
        target_duration: float = 2.0,
        sample_stride: float = 0.2
    ) -> Tuple[float, float, float]:
        """
        Quét tín hiệu âm thanh thô bằng numpy để tìm cửa sổ thời gian có năng lượng RMS cao nhất (cao trào âm thanh).

        Args:
            wav_path (str): Đường dẫn tệp WAV 16kHz mono.
            target_duration (float): Độ dài cửa sổ cần cắt (giây).
            sample_stride (float): Bước trượt cửa sổ (giây).

        Returns:
            Tuple[float, float, float]: (start_time, end_time, max_rms_score)
        """
        if not os.path.exists(wav_path):
            return (0.0, target_duration, 0.0)

        try:
            with wave.open(wav_path, 'rb') as wav:
                params = wav.getparams()
                sample_rate = params.framerate
                sample_width = params.sampwidth
                num_frames = params.nframes
                raw_frames = wav.readframes(num_frames)

            total_duration = num_frames / sample_rate
            if total_duration <= target_duration:
                return (0.0, total_duration, 1.0)

            # Chuyển sang numpy array
            if sample_width == 1:
                audio_data = (np.frombuffer(raw_frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
            elif sample_width == 2:
                audio_data = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                return (0.0, min(total_duration, target_duration), 0.5)

            window_samples = int(target_duration * sample_rate)
            stride_samples = max(1, int(sample_stride * sample_rate))

            max_rms = -1.0
            best_start_sample = 0

            # Quét cửa sổ trượt (Sliding window)
            for start_idx in range(0, len(audio_data) - window_samples + 1, stride_samples):
                window = audio_data[start_idx:start_idx + window_samples]
                rms = np.sqrt(np.mean(window ** 2))
                if rms > max_rms:
                    max_rms = float(rms)
                    best_start_sample = start_idx

            best_start_sec = best_start_sample / sample_rate
            best_end_sec = min(total_duration, best_start_sec + target_duration)
            return (round(best_start_sec, 3), round(best_end_sec, 3), round(max_rms, 4))

        except Exception:
            return (0.0, target_duration, 0.0)

    @classmethod
    def score_subtitle_hook(cls, text: str) -> Tuple[float, str]:
        """
        Đánh giá mức độ hấp dẫn của câu thoại dựa trên từ khóa Hook mở đầu và cấu trúc câu hỏi / cảm thán.

        Returns:
            Tuple[float, str]: (Điểm hook từ 0.0 - 10.0, Lý do trích xuất)
        """
        clean_text = text.lower().strip()
        score = 0.0
        reasons = []

        all_hooks = cls.HOOK_KEYWORDS_VI + cls.HOOK_KEYWORDS_EN
        matched_hooks = []
        for kw in all_hooks:
            if re.search(r'\b' + re.escape(kw) + r'\b', clean_text):
                matched_hooks.append(kw)

        if matched_hooks:
            kw_score = min(6.0, len(matched_hooks) * 2.5)
            score += kw_score
            reasons.append(f"Từ khóa '{', '.join(matched_hooks[:3])}'")

        # Tăng điểm nếu là câu cảm thán (!) hoặc câu hỏi (?)
        if "!" in text or "wow" in clean_text:
            score += 2.0
            reasons.append("Cảm thán cao trào (!)")
        if "?" in text or clean_text.startswith("tại sao") or clean_text.startswith("nhìn"):
            score += 2.5
            reasons.append("Câu hỏi kích thích tò mò (?)")

        # Độ dài câu lý tưởng cho hook (3 đến 12 từ)
        words = clean_text.split()
        if 3 <= len(words) <= 12:
            score += 1.5

        reason_str = ", ".join(reasons) if reasons else "Điểm nhấn hội thoại"
        return (round(score, 2), reason_str)

    @classmethod
    def extract_highlight_from_clip(
        cls,
        video_path: str,
        wav_path: Optional[str] = None,
        subtitles: Optional[List[Dict[str, Any]]] = None,
        clip_duration: float = 2.0
    ) -> HookSegment:
        """
        Trích xuất phân đoạn highlight vàng (Golden Hook) dài 1.0 - 3.0s từ một clip video đơn lẻ.
        Kết hợp thông minh giữa phân tích ngữ nghĩa thoại và năng lượng sóng âm.
        """
        from src.core.audio import AudioExtractor
        try:
            total_dur = AudioExtractor.get_audio_duration(video_path)
        except Exception:
            total_dur = 10.0

        target_dur = min(total_dur, clip_duration)
        best_start = 0.0
        best_end = target_dur
        best_score = 0.0
        best_reason = "Đoạn mở đầu video"
        best_text = ""

        # 1. Phân tích ngữ nghĩa thoại nếu có phụ đề
        if subtitles:
            for sub in subtitles:
                sub_text = sub.get("text", "")
                sub_start = sub.get("start", 0.0)
                sub_end = sub.get("end", sub_start + target_dur)
                
                h_score, h_reason = cls.score_subtitle_hook(sub_text)
                if h_score > best_score:
                    best_score = h_score
                    best_reason = f"Hook thoại: {h_reason}"
                    best_text = sub_text
                    
                    # Cắt đúng đoạn thoại hoặc căn chỉnh target_dur
                    sub_len = sub_end - sub_start
                    if sub_len <= target_dur:
                        best_start = sub_start
                        best_end = min(total_dur, sub_start + target_dur)
                    else:
                        best_start = sub_start
                        best_end = sub_start + target_dur

        # 2. Phân tích năng lượng âm thanh (RMS Energy Peak) từ file WAV nếu có
        found_audio_peak = False
        if wav_path and os.path.exists(wav_path):
            rms_start, rms_end, rms_score = cls.find_audio_energy_peak(wav_path, target_dur)
            # Nếu không có hook thoại hoặc năng lượng âm thanh cao trào nổi bật
            if best_score < 2.5 or (rms_score > 0.35 and best_score < 4.0):
                if rms_score > 0.15:
                    best_start = rms_start
                    best_end = min(total_dur, rms_start + target_dur)
                    best_reason = f"Âm thanh cao trào (RMS {rms_score:.2f})"
                    best_score = max(best_score, rms_score * 10.0)
                    found_audio_peak = True

        # 3. Xử lý chuyên biệt cho clip phong cảnh / Visual B-Roll không có lời thoại (Drone / Landscape / Scenery)
        # Lấy điểm cắt vàng (Golden Sweet Spot) ở 1/3 - 1/2 clip để tránh rung lắc đầu/cuối khi bấm máy
        if best_score < 2.5 and not found_audio_peak:
            sweet_start = max(0.0, (total_dur - target_dur) * 0.35)
            best_start = sweet_start
            best_end = min(total_dur, best_start + target_dur)
            best_reason = "Cảnh quay điện ảnh (Cinematic Visual B-Roll)"
            best_score = 5.0

        # Đảm bảo ranh giới thời gian hợp lệ
        if best_end > total_dur:
            best_end = total_dur
            best_start = max(0.0, best_end - target_dur)

        actual_duration = round(best_end - best_start, 3)
        return HookSegment(
            video_path=video_path,
            src_in=round(best_start, 3),
            src_out=round(best_end, 3),
            duration=actual_duration,
            score=round(best_score, 2),
            reason=best_reason,
            text=best_text
        )

    # ------------------------------------------------------------------
    # Teaser đa khoảnh khắc: đọc toàn bộ clip, gom nhiều điểm nhấn thành một teaser
    # ------------------------------------------------------------------
    @staticmethod
    def audio_energy_db(wav_path: str, step: float = 1.0) -> Optional[np.ndarray]:
        """Mức âm lượng (dB) theo từng `step` giây của tệp WAV; None nếu không đọc được."""
        try:
            from src.core.transcript_quality import read_wav_mono
            audio, sr = read_wav_mono(wav_path)
        except Exception:
            return None
        size = int(step * sr)
        n = len(audio) // size
        if n == 0:
            return None
        rms = np.sqrt(np.mean(audio[: n * size].reshape(n, size) ** 2, axis=1)) + 1e-10
        return 20 * np.log10(rms)

    @classmethod
    def find_moment_candidates(
        cls,
        video_path: str,
        total_duration: float,
        subtitles: Optional[List[Dict[str, Any]]] = None,
        energy_db: Optional[np.ndarray] = None,
        activity: Optional[np.ndarray] = None,
        moment_len: float = 2.5,
        stride: float = 1.0,
        top_k: int = 12,
        max_len: float = 4.0,
    ) -> List[HookSegment]:
        """
        Quét toàn bộ clip bằng cửa sổ trượt và chấm điểm từng khoảnh khắc theo 3 tín hiệu:
        - lời thoại hook (từ khóa, câu hỏi, cảm thán),
        - cao trào âm thanh so với mức nền của chính clip (hét, cười, tiếng động lớn),
        - chuyển động hình ảnh (cảnh hành động so với phần còn lại).
        Bỏ vùng đầu/cuối clip (rung máy lúc bấm quay) và cửa sổ "chết" (im lặng + tĩnh).
        Trả về tối đa top_k ứng viên không chồng lấn, điểm giảm dần.
        """
        if total_duration <= 0:
            return []
        n_bins = int(total_duration // stride)
        win = max(1, int(round(moment_len / stride)))
        if n_bins < win:
            return []

        base_db = float(np.median(energy_db)) if energy_db is not None and len(energy_db) else 0.0
        # Mức sàn tuyệt đối: clip gần như tĩnh không được chuẩn hóa tương đối thành 'chuyển động mạnh'
        act_ref = max(float(np.percentile(activity, 90)), 0.15) if activity is not None and len(activity) else 0.0
        subs = subtitles or []

        scored: List[Tuple[float, int, str, str, float, float]] = []
        for i in range(0, n_bins - win + 1):
            t0, t1 = i * stride, i * stride + moment_len
            parts: Dict[str, float] = {}
            if energy_db is not None and i + win <= len(energy_db):
                w = energy_db[i:i + win]
                parts["energy"] = float(np.clip((float(w.max()) - base_db) / 15.0, 0.0, 1.0))
            if activity is not None and act_ref > 0 and i + win <= len(activity):
                parts["motion"] = float(np.clip(float(np.mean(activity[i:i + win])) / act_ref, 0.0, 1.0))

            speech, sp_text, sp_reason = 0.0, "", ""
            for sub in subs:
                if sub["end"] > t0 and sub["start"] < t1:
                    hs, hr = cls.score_subtitle_hook(sub.get("text", ""))
                    if hs / 10.0 > speech:
                        speech, sp_text, sp_reason = min(1.0, hs / 10.0), sub.get("text", ""), hr
            if subs:
                parts["speech"] = speech

            if not parts:
                continue
            weights = {"speech": 0.40, "energy": 0.25, "motion": 0.35}
            wsum = sum(weights[k] for k in parts)
            score = sum(parts[k] * weights[k] for k in parts) / wsum

            dead = parts.get("energy", 1.0) < 0.05 and parts.get("motion", 1.0) < 0.15 and speech == 0.0
            if dead:
                continue
            if t0 < total_duration * 0.04 or t1 > total_duration * 0.96:
                score *= 0.5

            top = max(parts, key=lambda k: parts[k] * weights[k])
            if top == "speech" and sp_text:
                reason = f"Hook thoại: {sp_reason}"
            elif top == "energy":
                reason = f"Âm thanh cao trào (+{(float(energy_db[i:i + win].max()) - base_db):.0f} dB so với nền)"
            else:
                reason = "Chuyển động mạnh (cảnh hành động)"
            scored.append((score, i, reason, sp_text, t0, t1))

        scored.sort(key=lambda x: x[0], reverse=True)
        picked: List[HookSegment] = []
        for score, i, reason, text, t0, t1 in scored:
            start, end = t0, t1
            if text:  # căn theo câu thoại để không cắt ngang lời
                for sub in subs:
                    if sub.get("text", "") == text and sub["end"] > t0 and sub["start"] < t1:
                        start = max(0.0, sub["start"] - 0.15)
                        end = min(total_duration, max(start + 1.5, min(sub["end"] + 0.25, start + max_len)))
                        break
            if any(start < p.src_out and end > p.src_in for p in picked):
                continue
            picked.append(HookSegment(
                video_path=video_path, src_in=round(start, 3), src_out=round(end, 3),
                duration=round(end - start, 3), score=round(score * 10.0, 2), reason=reason, text=text
            ))
            if len(picked) >= top_k:
                break
        return picked

    @staticmethod
    def select_teaser(
        pool: List[HookSegment],
        target_total: float = 20.0,
        min_gap: float = 8.0,
        video_order: Optional[List[str]] = None,
        spread_seconds: float = 90.0,
    ) -> List[HookSegment]:
        """
        Chọn các khoảnh khắc để ghép teaser: lấy theo điểm cao nhất, dồn tới tổng thời lượng mục tiêu,
        giữ khoảng cách tối thiểu giữa các khoảnh khắc cùng clip và giảm điểm những khoảnh khắc nằm sát nhau
        (trong spread_seconds) để teaser trải đều nội dung thay vì dồn vào một đoạn.
        Thứ tự: khoảnh khắc mạnh nhất mở đầu (hook), phần còn lại theo trình tự thời gian.
        """
        chosen: List[HookSegment] = []
        total = 0.0
        remaining = list(pool)
        while remaining:
            best, best_eff = None, 0.0
            for cand in remaining:
                if total + cand.duration > target_total + 1e-6:
                    continue
                eff = cand.score
                too_close = False
                for c in chosen:
                    if c.video_path != cand.video_path:
                        continue
                    if cand.src_in < c.src_out + min_gap and cand.src_out > c.src_in - min_gap:
                        too_close = True
                        break
                    if abs(cand.src_in - c.src_in) < spread_seconds:
                        eff *= 0.6  # ưu tiên trải đều: khoảnh khắc gần cái đã chọn bị giảm điểm
                if too_close:
                    continue
                if eff > best_eff:
                    best, best_eff = cand, eff
            if best is None:
                break
            chosen.append(best)
            total += best.duration
            remaining.remove(best)
        if not chosen and pool:
            chosen = [max(pool, key=lambda c: c.score)]
        if not chosen:
            return []
        first = chosen[0]
        order = {p: i for i, p in enumerate(video_order or [])}
        rest = sorted(chosen[1:], key=lambda c: (order.get(c.video_path, 0), c.src_in))
        return [first] + rest

    @classmethod
    def generate_teaser_edl(
        cls,
        segments: List[HookSegment],
        output_edl_path: str,
        project_name: str = "Project"
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Sinh tệp tin EDL hoàn chỉnh cho Timeline Teaser/Hook mở đầu.
        Gắn các Marker chỉ điểm (Locator) phân biệt rõ ràng:
        - Magenta: Hook câu nói đắt giá (Voice Hook)
        - Cyan: Âm thanh cao trào (Audio Peak)
        - Green: Cảnh quay phong cảnh điện ảnh (Cinematic Visual B-Roll)

        Returns:
            Tuple[str, List[Dict[str, Any]]]: (Đường dẫn tệp EDL, Danh sách events)
        """
        lines = [
            f"TITLE: {project_name}_Vlog_Intro_Teaser",
            "FCM: NON-DROP FRAME",
            ""
        ]

        events = []
        cumulative_record_time = 0.0
        from src.core.autocut import get_media_metadata, get_reel_name

        meta_cache = {}

        for idx, seg in enumerate(segments, 1):
            clip_name = os.path.basename(seg.video_path)
            reel_id = get_reel_name(clip_name).ljust(8)
            if seg.video_path not in meta_cache:
                meta_cache[seg.video_path] = get_media_metadata(seg.video_path)
            meta = meta_cache[seg.video_path]
            fps = meta["fps"]
            start_tc_sec = meta["start_seconds"]

            rec_in = cumulative_record_time
            rec_out = rec_in + seg.duration
            cumulative_record_time = rec_out

            src_in_tc = seconds_to_timecode(seg.src_in, fps, start_tc_offset_seconds=start_tc_sec)
            src_out_tc = seconds_to_timecode(seg.src_out, fps, start_tc_offset_seconds=start_tc_sec)
            rec_in_tc = seconds_to_timecode(rec_in, fps)
            rec_out_tc = seconds_to_timecode(rec_out, fps)

            event_num = f"{idx:03d}"

            # Video Event
            lines.append(f"{event_num}  {reel_id} V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")
            lines.append(f"* COMMENT: TEASER_HOOK_CLIP_{idx} - {seg.reason}")

            # Audio Event (Track 1 & Track 2 - Stereo)
            lines.append(f"{event_num}  {reel_id} A     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")
            lines.append(f"{event_num}  {reel_id} A2    C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

            # Phân loại màu Marker thông minh trên Timeline DaVinci Resolve
            if "Hook" in seg.reason or "thoại" in seg.reason.lower():
                marker_color = "Magenta"
            elif "Âm thanh" in seg.reason or "RMS" in seg.reason:
                marker_color = "Cyan"
            else:
                marker_color = "Green"

            marker_note = f"Hook {idx}: {clip_name} ({seg.reason})"
            if seg.text:
                marker_note += f' - "{seg.text[:30]}..."'
            lines.append(f"* LOC: {rec_in_tc} {marker_color} {marker_note}")

            events.append({
                "video_path": seg.video_path,
                "src_in": seg.src_in,
                "src_out": seg.src_out,
                "rec_in": rec_in,
                "rec_out": rec_out,
                "fps": fps,
                "reason": seg.reason,
                "text": seg.text
            })

        content = "\n".join(lines)
        parent_dir = os.path.dirname(output_edl_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_edl_path, "w", encoding="utf-8") as f:
            f.write(content)

        return (output_edl_path, events)
