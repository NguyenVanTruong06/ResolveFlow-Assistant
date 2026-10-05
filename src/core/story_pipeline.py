"""
Master Story Pipeline & Progressive Chapter Assembly Engine.
Hỗ trợ quy trình dựng cuốn chiếu theo từng Chapter (tải/xử lý từng phần cho video nặng)
kết hợp tuyển chọn Hook đỉnh cao (Global Hook Selection) ở bước cuối cùng trước khi ghép Master Timeline.
"""

import os
from typing import List, Dict, Any, Optional, Tuple, Callable
from pydantic import BaseModel, Field

from src.core.vlog_hook import HookSegment, VlogHookGenerator
from src.core.folder_scanner import FolderGroup, ProjectFolderStructure
from src.core.autocut import seconds_to_timecode


class ChapterResult(BaseModel):
    """Kết quả xử lý & dựng của một Chapter đơn lẻ."""
    chapter_name: str
    chapter_index: int
    video_paths: List[str] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    subtitles: List[Dict[str, Any]] = Field(default_factory=list)
    markers: List[Dict[str, Any]] = Field(default_factory=list)
    hook_candidates: List[HookSegment] = Field(default_factory=list)
    total_duration: float = 0.0
    role_hint: str = "build"


class MasterStoryResult(BaseModel):
    """Kết quả ghép nối toàn bộ dự án Master Timeline."""
    project_name: str
    chapters: List[ChapterResult] = Field(default_factory=list)
    global_hook: Optional[HookSegment] = None
    all_hook_candidates: List[HookSegment] = Field(default_factory=list)
    master_events: List[Dict[str, Any]] = Field(default_factory=list)
    master_subtitles: List[Dict[str, Any]] = Field(default_factory=list)
    master_markers: List[Dict[str, Any]] = Field(default_factory=list)
    total_duration: float = 0.0


class MasterStoryPipeline:
    """
    Động cơ điều phối xử lý theo từng Chapter và đúc kết Hook cuối cùng.
    """

    @classmethod
    def process_chapter_group(
        cls,
        group: FolderGroup,
        subtitles_by_video: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        wav_by_video: Optional[Dict[str, str]] = None,
        clip_duration: float = 2.5
    ) -> ChapterResult:
        """
        Xử lý một nhóm Chapter:
        - Thu thập và phân tích các video trong Chapter.
        - Trích xuất các Hook candidates sáng giá.
        - Dựng các events mốc thời gian độc lập.
        """
        subtitles_by_video = subtitles_by_video or {}
        wav_by_video = wav_by_video or {}

        events: List[Dict[str, Any]] = []
        subtitles: List[Dict[str, Any]] = []
        markers: List[Dict[str, Any]] = []
        hook_candidates: List[HookSegment] = []

        cursor = 0.0

        for vpath in group.video_paths:
            subs = subtitles_by_video.get(vpath, [])
            wav = wav_by_video.get(vpath)
            
            # 1. Trích xuất Hook Candidate của clip này
            try:
                hook_seg = VlogHookGenerator.extract_highlight_from_clip(
                    video_path=vpath,
                    wav_path=wav,
                    subtitles=subs,
                    clip_duration=clip_duration
                )
                if hook_seg:
                    hook_candidates.append(hook_seg)
            except Exception:
                pass

            # 2. Xây dựng events cơ bản cho Chapter (hoặc từ autocut nếu có)
            # Ước lượng độ dài clip
            from src.core.audio import AudioExtractor
            try:
                clip_dur = AudioExtractor.get_audio_duration(vpath)
            except Exception:
                clip_dur = 10.0

            rec_in = cursor
            rec_out = cursor + clip_dur
            events.append({
                "video_path": vpath,
                "src_in": 0.0,
                "src_out": clip_dur,
                "rec_in": rec_in,
                "rec_out": rec_out,
                "speed": 1.0,
                "punch_in": False,
                "chapter": group.name
            })

            # Dịch chuyển phụ đề theo timeline của Chapter
            for s in subs:
                subtitles.append({
                    "start": cursor + s.get("start", 0.0),
                    "end": cursor + s.get("end", 0.0),
                    "text": s.get("text", ""),
                    "video_path": vpath
                })

            # Đánh dấu Marker đầu clip
            markers.append({
                "time": rec_in,
                "duration": 1.0,
                "name": f"🎬 {group.name}: {os.path.basename(vpath)}",
                "color": "Green" if not group.is_broll else "Cyan",
                "note": f"Vai trò: {group.role_hint.upper()}"
            })

            cursor = rec_out

        return ChapterResult(
            chapter_name=group.name,
            chapter_index=group.chapter_order,
            video_paths=group.video_paths,
            events=events,
            subtitles=subtitles,
            markers=markers,
            hook_candidates=hook_candidates,
            total_duration=cursor,
            role_hint=group.role_hint
        )

    @classmethod
    def select_global_hook(
        cls,
        all_candidates: List[HookSegment],
        target_hook_len: float = 4.0
    ) -> Optional[HookSegment]:
        """
        Tuyển chọn 1 Hook sáng giá nhất từ toàn bộ các Chapter trong dự án.
        """
        if not all_candidates:
            return None

        # Sắp xếp theo điểm số chất lượng (từ khóa thoại + năng lượng âm thanh)
        ranked = sorted(all_candidates, key=lambda c: c.score, reverse=True)
        best = ranked[0]

        # Giới hạn độ dài trong khoảng target_hook_len
        actual_len = min(best.duration, target_hook_len)
        return HookSegment(
            video_path=best.video_path,
            src_in=best.src_in,
            src_out=round(best.src_in + actual_len, 3),
            duration=actual_len,
            score=best.score,
            reason=f"🔥 Top 1 Global Hook: {best.reason}",
            text=best.text
        )

    @classmethod
    def assemble_master_timeline(
        cls,
        project_name: str,
        chapters: List[ChapterResult],
        enable_final_hook: bool = True,
        hook_duration: float = 4.0
    ) -> MasterStoryResult:
        """
        Ghép nối toàn bộ các Chapter thành Master Timeline hoàn chỉnh:
        [Global Hook] ➔ [Chapter 1] ➔ [Chapter 2] ➔ [Chapter 3] ...
        """
        # Thu thập toàn bộ Hook candidates
        all_candidates: List[HookSegment] = []
        for ch in chapters:
            all_candidates.extend(ch.hook_candidates)

        global_hook = None
        if enable_final_hook and all_candidates:
            global_hook = cls.select_global_hook(all_candidates, target_hook_len=hook_duration)

        master_events: List[Dict[str, Any]] = []
        master_subs: List[Dict[str, Any]] = []
        master_markers: List[Dict[str, Any]] = []

        cursor = 0.0

        # 1. Chèn Global Hook lên đầu Timeline nếu bật
        if global_hook:
            hook_dur = global_hook.duration
            master_events.append({
                "video_path": global_hook.video_path,
                "src_in": global_hook.src_in,
                "src_out": global_hook.src_out,
                "rec_in": cursor,
                "rec_out": cursor + hook_dur,
                "speed": 1.0,
                "punch_in": True,
                "punch_in_scale": 1.15,
                "is_hook": True,
                "reason": global_hook.reason
            })

            if global_hook.text:
                master_subs.append({
                    "start": cursor,
                    "end": cursor + hook_dur,
                    "text": global_hook.text,
                    "video_path": global_hook.video_path
                })

            master_markers.append({
                "time": cursor,
                "duration": hook_dur,
                "name": "🔥 GLOBAL HOOK (Khoảnh khắc đắt nhất)",
                "color": "Magenta",
                "note": f"{global_hook.reason}: {os.path.basename(global_hook.video_path)}"
            })

            cursor += hook_dur

        # 2. Lần lượt nối các Chapter theo thứ tự
        sorted_chapters = sorted(chapters, key=lambda c: c.chapter_index)
        for ch in sorted_chapters:
            # Marker phân đoạn Chapter
            master_markers.append({
                "time": cursor,
                "duration": 2.0,
                "name": f"📂 CHAPTER [{ch.chapter_name}]",
                "color": "Blue",
                "note": f"Vai trò: {ch.role_hint.upper()}"
            })

            ch_offset = cursor
            for ev in ch.events:
                new_ev = dict(ev)
                ev_dur = ev["rec_out"] - ev["rec_in"]
                new_ev["rec_in"] = cursor
                new_ev["rec_out"] = cursor + ev_dur
                master_events.append(new_ev)
                cursor += ev_dur

            for s in ch.subtitles:
                new_s = dict(s)
                s_dur = s["end"] - s["start"]
                new_s["start"] = ch_offset + s["start"]
                new_s["end"] = ch_offset + s["end"]
                master_subs.append(new_s)

            for m in ch.markers:
                new_m = dict(m)
                new_m["time"] = ch_offset + m["time"]
                master_markers.append(new_m)

        return MasterStoryResult(
            project_name=project_name,
            chapters=chapters,
            global_hook=global_hook,
            all_hook_candidates=all_candidates,
            master_events=master_events,
            master_subtitles=master_subs,
            master_markers=master_markers,
            total_duration=cursor
        )
