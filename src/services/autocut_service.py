"""
src/services/autocut_service.py
Dịch vụ AutoCut 1-Click trực tiếp trên DaVinci Resolve Timeline (Phương pháp 2: API Instant Assembly).
Tự động phát hiện video trên Timeline đang mở, phân tích khoảng lặng và tạo ngay Timeline đã cắt & hút sát clip.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Dict, Any

from src.core.resolve_api import ResolveAutomation
from src.core.autocut import (
    AudioCutConfig, CutSegment, SilenceDetector, EDLGenerator, get_media_metadata
)

logger = logging.getLogger("ResolveFlow.InstantAutoCut")


@dataclass
class AutoCutResult:
    ok: bool
    message: str
    cuts_count: int = 0
    original_duration_sec: float = 0.0
    new_duration_sec: float = 0.0
    duration_saved_sec: float = 0.0
    original_timeline_name: str = ""
    new_timeline_name: str = ""
    generated_file_path: str = ""
    source_file_path: str = ""

    @property
    def error_message(self) -> str:
        return "" if self.ok else self.message


class InstantAutoCutService:
    """
    Dịch vụ thực thi AutoCut tức thì từ DaVinci Resolve Timeline:
    1. Lấy thông tin clip video từ Timeline đang mở qua Resolve API.
    2. Trích xuất âm thanh và nhận diện khoảng lặng (SilenceDetector).
    3. Tạo và mở ngay Timeline [AutoCut] mới trong DaVinci Resolve.
    """

    def __init__(
        self,
        resolve_automation: Optional[ResolveAutomation] = None,
        config: Optional[AudioCutConfig] = None
    ):
        self.resolve_automation = resolve_automation or ResolveAutomation()
        self.config = config or AudioCutConfig(
            min_silent_duration=0.4,
            silence_threshold_db=-38.0,
            pre_roll=0.06,
            post_roll=0.08,
            min_gap_merge=0.15
        )

    def extract_audio(self, video_path: str, temp_dir: Optional[str] = None) -> Optional[str]:
        """Trích xuất âm thanh chuẩn PCM 16kHz Mono từ video để phân tích nhanh."""
        if not video_path or not os.path.exists(video_path):
            return None

        # Nếu tệp đã là WAV
        if video_path.lower().endswith(".wav"):
            return video_path

        out_dir = temp_dir or tempfile.gettempdir()
        stem = Path(video_path).stem
        out_wav = os.path.join(out_dir, f"autocut_extract_{stem}.wav")

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            out_wav
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            if res.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 44:
                return out_wav
        except Exception as e:
            logger.error(f"Error extracting audio with ffmpeg: {e}")

        return None

    def detect_active_timeline_media(self) -> Tuple[Optional[Any], List[str]]:
        """Phát hiện Timeline đang mở và danh sách tệp video nguồn trên Track 1 hoặc selection."""
        timeline = self.resolve_automation.get_active_timeline()
        if not timeline:
            return None, []

        video_paths = self.resolve_automation.auto_detect_video_paths()
        if not video_paths:
            # Dự phòng: quét thủ công Track 1
            try:
                items = timeline.GetItemListInTrack("video", 1) or []
                for itm in items:
                    pool_item = itm.GetMediaPoolItem()
                    if pool_item:
                        fp = pool_item.GetClipProperty("File Path")
                        if fp and os.path.exists(fp) and fp not in video_paths:
                            video_paths.append(fp)
            except Exception:
                pass

        return timeline, video_paths

    def run_instant_autocut(
        self,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> AutoCutResult:
        """
        Thực hiện toàn bộ quy trình AutoCut 1-Click trên Timeline đang mở.
        """
        try:
            return self._run_instant_autocut(progress_callback)
        except Exception as exc:
            logger.exception("AutoCut API failure")
            return AutoCutResult(ok=False, message=f"Lỗi API DaVinci Resolve: {exc}")

    def _run_instant_autocut(self, progress_callback=None) -> AutoCutResult:
        def report(pct: int, msg: str):
            if progress_callback:
                progress_callback(pct, msg)

        report(5, "Đang kết nối tới DaVinci Resolve...")
        if not self.resolve_automation.is_connected():
            if not self.resolve_automation.connect():
                err = self.resolve_automation.last_connect_error or "Vui lòng mở DaVinci Resolve và mở một dự án."
                return AutoCutResult(
                    ok=False,
                    message=f"Không thể kết nối DaVinci Resolve: {err}"
                )

        report(15, "Đang quét video trên Timeline hiện tại...")
        timeline, video_paths = self.detect_active_timeline_media()
        if not timeline:
            return AutoCutResult(
                ok=False,
                message="Không tìm thấy Timeline nào đang mở trong DaVinci Resolve!"
            )

        if not video_paths:
            return AutoCutResult(
                ok=False,
                message="Không tìm thấy clip video nào trên Timeline hiện tại để cắt!"
            )

        video_path = video_paths[0]
        orig_name = timeline.GetName()
        if not os.path.isfile(video_path):
            return AutoCutResult(ok=False, message=f"Không tìm thấy tệp nguồn: {video_path}",
                                 source_file_path=video_path)
        project = self.resolve_automation.current_project
        if not project:
            return AutoCutResult(ok=False, message="Lỗi API DaVinci: không có Project đang mở.")
        original_id = timeline.GetUniqueId()

        report(25, f"Nguồn: {Path(video_path).name} — đang trích xuất âm thanh...")
        wav_path = self.extract_audio(video_path)
        if not wav_path or not os.path.exists(wav_path):
            return AutoCutResult(
                ok=False,
                message=f"Không thể trích xuất âm thanh từ video '{os.path.basename(video_path)}'."
            )

        report(45, "AI đang dò tìm khoảng lặng và phân tích giọng nói...")
        try:
            segments = SilenceDetector.detect_silence_from_wav(wav_path, self.config)
        except Exception as e:
            return AutoCutResult(
                ok=False,
                message=f"Lỗi khi phân tích khoảng lặng âm thanh: {e}"
            )

        keep_segments = [s for s in segments if s.action == "keep"]
        cut_segments = [s for s in segments if s.action == "cut"]
        cuts_count = len(cut_segments)

        orig_dur = sum(s.duration for s in segments)
        new_dur = sum(s.duration for s in keep_segments)
        saved_dur = max(0.0, orig_dur - new_dur)

        if not keep_segments:
            return AutoCutResult(
                ok=False,
                message="Toàn bộ video là khoảng lặng hoặc không tìm thấy câu thoại nào hợp lệ!"
            )

        if cuts_count == 0:
            return AutoCutResult(
                ok=True,
                message=f"Nguồn: {Path(video_path).name}. Video rất lưu loát, không có khoảng lặng nào vượt ngưỡng cần cắt! Giữ Timeline '{orig_name}'.",
                source_file_path=video_path,
                cuts_count=0,
                original_duration_sec=orig_dur,
                new_duration_sec=orig_dur,
                duration_saved_sec=0.0,
                original_timeline_name=orig_name,
                new_timeline_name=orig_name
            )

        report(70, f"Đã phát hiện {cuts_count} khoảng lặng (rút ngắn {saved_dur:.1f}s). Đang tạo Timeline...")
        keep_intervals = [(s.start, s.end) for s in keep_segments]
        new_tl_name = f"[AutoCut] {orig_name}"

        temp_dir = tempfile.gettempdir()
        edl_path = os.path.join(temp_dir, f"ResolveFlow_AutoCut_{Path(video_path).stem}.edl")
        try:
            EDLGenerator.create_edl(video_path, keep_intervals, edl_path)
        except Exception as e:
            return AutoCutResult(
                ok=False,
                message=f"Lỗi tạo cấu trúc cắt gọt EDL: {e}"
            )

        report(85, "Đang mở Timeline mới trên DaVinci Resolve...")
        new_timeline = None
        if self.resolve_automation.current_project:
            media_pool = self.resolve_automation.current_project.GetMediaPool()
            if media_pool:
                try:
                    new_timeline = media_pool.ImportTimelineFromFile(
                        edl_path,
                        {"timelineName": new_tl_name, "importOption": "auto"}
                    )
                except Exception as e:
                    logger.warning(f"ImportTimelineFromFile failed, fallback to import_edl_to_timeline: {e}")

        if not new_timeline:
            # Fallback
            ok = self.resolve_automation.import_edl_to_timeline(
                edl_path=edl_path,
                video_path=video_paths,
                timeline_name=new_tl_name
            )
            if ok:
                new_timeline = self.resolve_automation.get_active_timeline()

        # Xác minh đối tượng thuộc Project và khác timeline gốc, kể cả khi fallback báo True.
        candidate_id = new_timeline.GetUniqueId() if new_timeline else None
        verified = False
        if candidate_id and candidate_id != original_id:
            for index in range(1, int(project.GetTimelineCount()) + 1):
                item = project.GetTimelineByIndex(index)
                if item and item.GetUniqueId() == candidate_id:
                    verified = True
                    break
        if not verified:
            return AutoCutResult(
                ok=False, message="Lỗi API DaVinci: không xác minh được timeline mới trong Project.",
                original_timeline_name=orig_name, source_file_path=video_path,
                generated_file_path=edl_path,
            )
        new_tl_name = new_timeline.GetName()
        activated = False

        # Đặt Timeline mới làm Timeline đang mở trên màn hình DaVinci
        if new_timeline and self.resolve_automation.current_project:
            try:
                activated = bool(project.SetCurrentTimeline(new_timeline))
            except Exception:
                pass

        message = (f"Nguồn: {Path(video_path).name}. Đã tạo Timeline '{new_tl_name}': "
                   f"cắt {cuts_count} khoảng lặng, rút ngắn {saved_dur:.1f}s.")
        if not activated:
            message += " Không thể chuyển sang timeline mới; hãy mở trong Media Pool."
        report(100, message)
        return AutoCutResult(
            ok=True,
            message=message,
            cuts_count=cuts_count,
            original_duration_sec=orig_dur,
            new_duration_sec=new_dur,
            duration_saved_sec=saved_dur,
            original_timeline_name=orig_name,
            new_timeline_name=new_tl_name,
            generated_file_path=edl_path,
            source_file_path=video_path
        )
