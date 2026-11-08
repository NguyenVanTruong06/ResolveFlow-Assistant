"""
src/services/insert_service.py
InsertService: Coordinates inserting assets (Titles, SFX, LUTs, Fusion Pastes) into DaVinci Resolve.
Supports mode: 'auto' (safe empty track), 'new_track', 'fusion_paste'.
Provides safe undo() within 30 seconds.

Strict Clean Architecture: NO QtWidgets or QtGui allowed.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEventLoop, QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication
from src.services.resolve_gateway import ResolveGateway
from src.services.resolve_state import ResolveStateService

logger = logging.getLogger("ResolveFlow.InsertService")


def _no_timeline_message(auto: Any = None) -> str:
    """Thông báo lỗi rõ ràng khi không lấy được Timeline (phân biệt chưa kết nối / chưa có timeline)."""
    msg = str(getattr(auto, "last_timeline_error", "") or "").strip() if auto is not None else ""
    return msg or (
        "Không tìm thấy Timeline đang mở trong DaVinci Resolve. "
        "Hãy mở một Timeline trên trang Edit rồi bấm chèn lại."
    )


def to_asset_dict(asset: Any) -> Dict[str, Any]:
    """Chuyển đổi an toàn mọi đối tượng Asset (Pydantic model, StudioAsset, dict) sang dict chuẩn."""
    if not asset:
        return {}
    if isinstance(asset, dict):
        d = dict(asset)
    elif hasattr(asset, "model_dump") and callable(asset.model_dump):
        d = dict(asset.model_dump())
        d["_raw_obj"] = asset
    elif hasattr(asset, "dict") and callable(asset.dict):
        d = dict(asset.dict())
        d["_raw_obj"] = asset
    elif hasattr(asset, "__dict__"):
        d = dict(asset.__dict__)
        d["_raw_obj"] = asset
    else:
        d = {"id": str(asset), "name": str(asset), "_raw_obj": asset}

    raw = d.get("_raw_obj") or asset
    type_name = type(raw).__name__
    fp = str(d.get("file_path") or d.get("path") or "").strip()
    ext = os.path.splitext(fp)[1].lower() if fp else ""
    raw_tab = str(d.get("tab") or "").lower()

    if (type_name == "TransitionStylePreset" or hasattr(raw, "transition_type")
            or raw_tab in ("trans", "transition", "transitions")
            or str(d.get("category") or "").lower() in ("trans", "transition", "transitions")
            or "transitions" in fp.lower()):
        d["tab"] = "trans"
        d["sub"] = d.get("category", "Transition")
        d["category"] = "trans"
    elif type_name == "TextStylePreset":
        d["tab"] = "text"
        d["sub"] = d.get("category", "Kinetic")
        d["category"] = "text"
    elif raw_tab in ("meme", "memes", "broll", "video", "overlay", "overlays") or ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp", ".svg"):
        d["tab"] = "overlay" if raw_tab in ("overlay", "overlays") or "overlay" in fp.lower() else "meme"
        d["sub"] = d.get("sub") or d.get("category") or ("Overlay" if d["tab"] == "overlay" else "Meme")
        d["category"] = d["tab"]
    elif raw_tab in ("sfx", "bgm", "audio") or ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a"):
        d["tab"] = "bgm" if raw_tab == "bgm" else "sfx"
        d["sub"] = d.get("sub") or d.get("category") or "SFX"
        d["category"] = d["tab"]
    elif raw_tab in ("lut", "luts") or ext in (".cube", ".3dl", ".dctl", ".dctle", ".drx", ".dpx"):
        d["tab"] = "lut"
        d["sub"] = d.get("sub") or d.get("category") or "LUT"
        d["category"] = "lut"
    elif raw_tab in ("trans", "transition", "transitions") or "transitions" in fp.lower():
        d["tab"] = "trans"
        d["sub"] = d.get("sub") or d.get("category") or "Transition"
        d["category"] = "trans"
    else:
        d["tab"] = d.get("tab") or "text"
        d["category"] = d.get("category") or "text"

    return d


@dataclass
class InsertResult:
    ok: bool
    track: str = ""             # e.g. "V2", "A3"
    start_tc: str = ""          # e.g. "01:00:04:12"
    start_frame: int = -1
    duration_sec: float = 0.0
    item_name: str = ""
    item_ref: Any = None
    error: str = ""
    inserted_at: float = field(default_factory=time.time)
    message: str = ""


class InsertService(QObject):
    """
    Coordinates asset insertions and safe undo.
    """
    insertion_finished = Signal(object)  # InsertResult

    def __init__(
        self,
        gateway: ResolveGateway,
        state_service: Optional[ResolveStateService] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._gateway = gateway
        self._state_service = state_service
        self._last_results: List[InsertResult] = []

    def insert(
        self,
        asset: Any,
        mode: str = "auto",
        duration: Optional[float] = None,
        preferred_track: Optional[int] = None,
        timeout: float = 15.0,
        extra_params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> InsertResult:
        """
        Synchronous/blocking insert wrapper using local QEventLoop.
        Safe to call from UI handlers without freezing since QEventLoop processes events.
        """
        result_holder: List[InsertResult] = []
        loop = QEventLoop()

        def _on_done(res: InsertResult):
            result_holder.append(res)
            if loop.isRunning():
                loop.quit()

        self.insert_async(
            asset,
            mode=mode,
            duration=duration,
            preferred_track=preferred_track,
            callback=_on_done,
            extra_params=extra_params,
            **kwargs
        )

        # If callback already fired synchronously (e.g. fusion paste, direct error)
        if result_holder:
            return result_holder[0]

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(int(timeout * 1000))

        loop.exec()

        if timer.isActive():
            timer.stop()

        if result_holder:
            return result_holder[0]
        return InsertResult(ok=False, error="Insertion timed out or aborted")

    def insert_async(
        self,
        asset: Any,
        mode: str = "auto",
        duration: Optional[float] = None,
        preferred_track: Optional[int] = None,
        callback: Optional[Callable[[InsertResult], None]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Asynchronously inserts an asset based on its category and mode.
        """
        asset_dict = to_asset_dict(asset)
        explicit_duration: Optional[float] = duration

        # Action-time Availability & Authority Guard (G04-04C2-I)
        from src.core.action_guard import check_action_availability, ActionGuardResult
        try:
            guard_res = check_action_availability(asset_dict, intent="insert")
        except Exception as e:
            guard_res = ActionGuardResult(
                allowed=False,
                status="offline",
                result_code="ERR_GUARD_EXCEPTION",
                reason=f"Lỗi kiểm tra tính khả dụng: {e}",
            )

        title_id = str(asset_dict.get("id") or asset_dict.get("name") or "karaoke_pop")
        name = str(asset_dict.get("name") or title_id or "Asset")

        if not guard_res.allowed:
            res = InsertResult(
                ok=False,
                item_name=name,
                error=guard_res.reason,
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)
            return

        cat = str(asset_dict.get("category") or asset_dict.get("tab") or "").lower()
        file_path = guard_res.resolved_path or str(asset_dict.get("file_path") or asset_dict.get("path") or "")
        ext = os.path.splitext(file_path)[1].lower() if file_path else ""

        if extra_params:
            if extra_params.get("duration") is not None and extra_params.get("duration") != "":
                try:
                    explicit_duration = float(extra_params["duration"])
                except Exception:
                    pass
            if extra_params.get("text"):
                name = str(extra_params["text"])
            if preferred_track is None:
                if "preferred_track" in extra_params:
                    try:
                        preferred_track = int(extra_params["preferred_track"])
                    except Exception:
                        pass
                elif "track_mode" in extra_params:
                    tm = str(extra_params["track_mode"]).strip()
                    if tm.lower() == "new":
                        mode = "new_track"
                    elif tm.lower() not in ("auto", "timeline_track"):
                        import re
                        digits = re.findall(r"\d+", tm)
                        if digits:
                            try:
                                preferred_track = int(digits[0])
                            except Exception:
                                pass

        # 1. Mode: Explicit Fusion Paste (Ctrl+V into Fusion node tree)
        if mode == "fusion_paste":
            self._insert_fusion_paste(name, file_path, callback)
            return

        # Check filebacked .setting macro: Truthful guidance, 0 gateway, 0 carrier, 0 Recent
        if ext == ".setting" or file_path.lower().endswith(".setting") or asset_dict.get("template_type") == "macro":
            res = InsertResult(
                ok=False,
                item_name=name,
                error="Tài nguyên mẫu Fusion (.setting) chưa hỗ trợ chèn tự động lên Edit Timeline; vui lòng bấm 'Sao chép mã Fusion' để sao chép node hoặc dùng phím tắt vào Fusion Page.",
                inserted_at=time.time(),
            )
            if callback:
                callback(res)
            self.insertion_finished.emit(res)
            return

        eff_duration = explicit_duration if explicit_duration is not None else 3.0

        # 2. Video Transitions (Ensure native guidance or Effects Library installation, never implicit Fusion paste or title)
        if cat in ("trans", "transitions", "transition") or "transitions" in file_path.lower():
            self._insert_transition(name, file_path, mode, eff_duration, callback, extra_params=extra_params)
            return

        # 3. Implicit Fusion page paste for titles, text, and fx (Chỉ áp dụng khi không có explicit timeline intent)
        explicit_timeline = bool(extra_params and (extra_params.get("explicit_timeline") or extra_params.get("intent") == "timeline"))
        if not explicit_timeline and self._state_service and self._state_service.current_page == "fusion" and cat in ("titles", "text", "fx"):
            self._insert_fusion_paste(name, file_path, callback)
            return

        # 4. SFX / BGM Audio insertion
        if ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a") or cat in ("sfx", "bgm", "audio", "sound"):
            self._insert_audio(name, file_path, mode, eff_duration, preferred_track, callback, extra_params=extra_params)
            return

        # 5. LUT / DCTL / PowerGrade Application
        if ext in (".cube", ".3dl", ".dctl", ".dctle", ".drx", ".dpx") or cat in ("luts", "lut", "color"):
            self._insert_lut(name, file_path, callback, extra_params=extra_params)
            return

        # 6. Video / Meme / B-Roll / Overlay insertion (Any video file format or still image or meme category)
        if ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp", ".svg") or cat in ("meme", "memes", "broll", "video", "overlay", "overlays", "green_screen", "sticker", "stickers"):
            if explicit_duration is not None:
                vid_duration = explicit_duration
            else:
                raw_dsec = asset_dict.get("duration_sec")
                vid_duration = 0.0
                if raw_dsec is not None and not isinstance(raw_dsec, bool):
                    try:
                        import math
                        fd = float(raw_dsec)
                        if math.isfinite(fd) and fd > 0.0:
                            vid_duration = fd
                    except Exception:
                        pass
            self._insert_video(name, file_path, mode, vid_duration, preferred_track, callback, extra_params=extra_params)
            return

        # 7. Procedural Text / Title via ResolveGateway
        from src.core.text_preset import TextStylePreset
        raw_obj = asset_dict.get("_raw_obj") or asset_dict.get("native_preset") or asset
        is_text = (
            cat in ("text", "title", "titles")
            or isinstance(raw_obj, TextStylePreset)
            or isinstance(asset, TextStylePreset)
        )
        if is_text:
            self._insert_procedural_text(asset_dict, name, mode, eff_duration, preferred_track, callback, extra_params=extra_params)
            return

        # 8. Fusion Title / Template -> Title Worker (QProcess fallback)
        self._insert_title_worker(name, mode, eff_duration, preferred_track, callback, title_id=title_id)

    def _insert_procedural_text(
        self,
        asset_dict: Dict[str, Any],
        name: str,
        mode: str,
        duration: float,
        preferred_track: Optional[int],
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Chèn tiêu đề / Text cơ bản qua ResolveGateway và resolve_bridge."""
        extra = dict(extra_params or {})
        text_content = str(extra.get("text") or name or "ResolveFlow Title")
        font_name = str(extra.get("font") or asset_dict.get("font") or "Arial")

        # Style / weight mapping
        raw_weight = str(extra.get("weight") or asset_dict.get("weight") or "bold").lower()
        if raw_weight in ("extra_bold", "extrabold", "black"):
            weight_style = "Black"
        elif raw_weight in ("regular", "normal", "light"):
            weight_style = "Regular"
        else:
            weight_style = "Bold"

        # Size conversion: UI slider (12..120 pt) to Fusion (0.04..0.25)
        raw_size = extra.get("size", asset_dict.get("size", 48))
        try:
            sz = float(raw_size)
            if sz > 1.0:
                fusion_size = round(max(0.04, min(0.25, sz / 600.0)), 4)
            else:
                fusion_size = max(0.04, min(0.25, sz))
        except Exception:
            fusion_size = 0.08

        # Colors & Stroke
        color_hex = str(extra.get("color") or asset_dict.get("standard_color") or "#FFFFFF")
        accent_hex = str(extra.get("highlight_color") or extra.get("box_glow_hex") or asset_dict.get("highlight_color") or "#FFD400")
        stroke_color = str(extra.get("stroke_color") or asset_dict.get("outline_color") or "#000000")
        try:
            stroke_width = float(extra.get("stroke_width", asset_dict.get("outline_width", 0.12)))
        except Exception:
            stroke_width = 0.12
        box_color = extra.get("box_glow_hex") or asset_dict.get("box_color")
        glow_color = extra.get("box_glow_hex") or asset_dict.get("glow_color")
        timing_curve = str(extra.get("timing_curve") or extra.get("curve") or asset_dict.get("timing_curve") or "spring")

        # Style mapping:
        anim = str(extra.get("animation") or asset_dict.get("animation") or "pop").lower()
        from src.core.fusion.titles import TITLES
        anim_map = {
            "pop": "spring_pop",
            "bounce": "spring_pop",
            "spring": "spring_pop",
            "typewriter": "typewriter",
            "glow": "neon_pulse",
            "neon_pulse": "neon_pulse",
            "box": "highlighter",
            "box_highlight": "highlighter",
            "highlighter": "highlighter",
            "glitch": "rgb_glitch",
            "rgb_glitch": "rgb_glitch",
            "paper": "paper_cutout",
            "paper_cutout": "paper_cutout",
            "slide": "lower_third",
            "static": "lower_third",
            "lower_third": "lower_third",
        }
        style_key = anim_map.get(anim, "spring_pop")
        if style_key not in TITLES:
            cand_id = str(asset_dict.get("id") or "")
            if cand_id in TITLES:
                style_key = cand_id
            else:
                style_key = "spring_pop"

        target_track = preferred_track or 2

        def _task(resolve):
            tl = None
            proj = None
            if resolve and hasattr(resolve, "GetProjectManager"):
                pm = resolve.GetProjectManager()
                proj = pm.GetCurrentProject() if pm else None
                tl = proj.GetCurrentTimeline() if proj else None

            if not tl:
                if proj is not None:
                    # Kết nối sống nhưng project chưa mở Timeline -> báo rõ ràng.
                    try:
                        proj_name = proj.GetName() if hasattr(proj, "GetName") else ""
                        tl_count = int(proj.GetTimelineCount() or 0) if hasattr(proj, "GetTimelineCount") else 1
                    except Exception:
                        proj_name, tl_count = "", 1
                    if tl_count <= 0:
                        raise RuntimeError(
                            f"Project '{proj_name}' trong DaVinci chưa có Timeline nào. "
                            "Hãy tạo hoặc mở một Timeline (trang Edit) rồi bấm chèn lại."
                        )
                    raise RuntimeError(_no_timeline_message(None))
                # Không có handle Resolve hợp lệ (gateway chưa kết nối) -> báo lỗi kết nối.
                raise RuntimeError(
                    "Chưa kết nối được DaVinci Resolve (không lấy được Project/Timeline). "
                    "Hãy mở DaVinci Resolve, mở một Timeline và bật Preferences > System > General > "
                    "External scripting using = Local, rồi thử lại."
                )

            from src.core.resolve_api import current_record_frame, _nominal_fps
            start_frame, current_tc = current_record_frame(tl, proj)
            _, fps = _nominal_fps(proj)
            if not fps or fps <= 0:
                fps = 24.0

            # Quantize duration strictly: positive at least 1 frame
            dur_frames = max(1, int(round(duration * fps)))

            title_params = {
                "text": text_content,
                "font": font_name,
                "style": weight_style,
                "size": fusion_size,
                "color": color_hex,
                "accent": accent_hex,
                "outline_color": stroke_color,
                "outline_width": stroke_width,
                "box_color": box_color,
                "glow_color": glow_color,
                "timing_curve": timing_curve,
                "duration_frames": dur_frames,
            }

            from src.core.resolve_bridge import insert_title_at_playhead, _get_safe_uid
            proj_name = proj.GetName() if proj and hasattr(proj, "GetName") else ""
            proj_id = _get_safe_uid(proj)
            tl_name = tl.GetName() if tl and hasattr(tl, "GetName") else ""
            tl_id = _get_safe_uid(tl)

            created_item = insert_title_at_playhead(
                style=style_key,
                params=title_params,
                track_index=target_track,
                on_conflict="next_free",
                record_frame=start_frame,
                resolve=resolve,
                expected_project_id=proj_id,
                expected_timeline_id=tl_id,
            )

            actual_name = created_item.GetName() if hasattr(created_item, "GetName") else f"RF {text_content}"
            actual_start = created_item.GetStart() if hasattr(created_item, "GetStart") else start_frame
            actual_dur = created_item.GetDuration() if hasattr(created_item, "GetDuration") else dur_frames
            actual_end = actual_start + actual_dur
            unique_id = _get_safe_uid(created_item)

            actual_track = None
            if hasattr(tl, "GetItemListInTrack"):
                for tr in range(1, int(tl.GetTrackCount("video") or 8) + 1):
                    tr_items = tl.GetItemListInTrack("video", tr) or []
                    for it in tr_items:
                        it_uid = _get_safe_uid(it)
                        if (unique_id and it_uid and it_uid == unique_id) or it == created_item or it is created_item:
                            actual_track = tr
                            break
                    if actual_track is not None:
                        break

            track_label = f"V{actual_track}" if actual_track is not None else ""

            return {
                "ok": True,
                "item_name": actual_name,
                "track": track_label,
                "track_index": actual_track,
                "start_frame": actual_start,
                "end_frame": actual_end,
                "duration_frames": actual_dur,
                "duration_sec": actual_dur / fps,
                "start_tc": current_tc,
                "project_name": proj_name,
                "project_id": proj_id,
                "timeline_name": tl_name,
                "timeline_id": tl_id,
                "unique_id": unique_id,
            }

        def _finish(data):
            track_label = data.get("track", "")
            track_index = data.get("track_index")
            envelope = {
                "kind": "title",
                "project_name": data.get("project_name", ""),
                "project_id": data.get("project_id", ""),
                "timeline_name": data.get("timeline_name", ""),
                "timeline_id": data.get("timeline_id", ""),
                "track": track_label,
                "track_index": track_index,
                "start_frame": data.get("start_frame", 0),
                "end_frame": data.get("end_frame", 0),
                "duration_frames": data.get("duration_frames", 0),
                "item_name": data.get("item_name", name),
                "unique_id": data.get("unique_id", ""),
            }
            track_msg = f" ({track_label})" if track_label else ""
            res = InsertResult(
                ok=True,
                track=track_label,
                item_name=data.get("item_name", name),
                start_frame=data.get("start_frame", -1),
                duration_sec=data.get("duration_sec", duration),
                start_tc=data.get("start_tc", ""),
                item_ref=envelope,
                inserted_at=time.time(),
                message=f"Đã chèn “{data.get('item_name', name)}” tại Playhead{track_msg}",
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err):
            res = InsertResult(
                ok=False,
                item_name=name,
                error=str(err),
                inserted_at=time.time(),
            )
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_finish, errback=_on_error)

    def _insert_transition(
        self,
        name: str,
        file_path: str,
        mode: str,
        duration: float,
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Chuẩn bị chuyển cảnh native: copy tên, tìm vết cắt và hướng dẫn kéo từ Effects."""
        app = QApplication.instance()
        if app:
            app.clipboard().setText(name)

        def _task(resolve):
            from src.core.resolve_bridge import prepare_native_transition
            return prepare_native_transition(name, resolve)

        def _finish(data):
            res = InsertResult(
                ok=True, track="Transitions", item_name=name,
                start_frame=data.get("start_frame", -1),
                start_tc=data.get("start_tc", ""), message=data["message"],
            )
            # Không ghi lịch sử Undo: thao tác này không tạo clip.
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err):
            res = InsertResult(ok=False, item_name=name, error=str(err))
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_finish, errback=_on_error)

    def _insert_fusion_paste(self, name: str, file_path: str, callback: Optional[Callable[[InsertResult], None]]):
        """Fast-pastes node text into Fusion using fast_paste.py."""
        try:
            from src.core.fast_paste import fast_paste_to_fusion
            code = ""
            if file_path and os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            elif file_path:
                code = file_path

            resolve_obj = self._gateway._worker.get_resolve_object()
            res_dict = fast_paste_to_fusion(code, resolve_app=resolve_obj)
            ok = bool(res_dict.get("ok", False))
            res = InsertResult(
                ok=ok,
                track="Fusion",
                item_name=name,
                error="" if ok else str(res_dict.get("message") or res_dict.get("error") or "Fast paste failed"),
                inserted_at=time.time(),
            )
        except Exception as e:
            res = InsertResult(ok=False, item_name=name, error=str(e), inserted_at=time.time())

        self._record_result(res)
        if callback:
            callback(res)
        self.insertion_finished.emit(res)

    @staticmethod
    def _find_free_audio_track(timeline, start_frame: int, end_frame: int) -> int:
        """Tìm khoảng trống [start, end), giữ A1 cho lời thoại; thêm track nếu cần."""
        count = int(timeline.GetTrackCount("audio"))
        for track in range(2, count + 1):
            if hasattr(timeline, "GetIsTrackLocked") and timeline.GetIsTrackLocked("audio", track):
                continue
            items = timeline.GetItemListInTrack("audio", track)
            if items is None:
                continue  # Không xác minh được track trống.
            occupied = False
            for item in items:
                left = float(item.GetStart())
                right = left + float(item.GetDuration())
                if left < end_frame and right > start_frame:
                    occupied = True
                    break
            if not occupied:
                return track
        target = max(2, count + 1)
        for _ in range(count, target):
            if not hasattr(timeline, "AddTrack") or not timeline.AddTrack("audio"):
                raise RuntimeError("Hết track audio trống và không thể tạo track mới.")
        return target

    def _insert_audio(
        self,
        name: str,
        file_path: str,
        mode: str,
        duration: float,
        preferred_track: Optional[int],
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Inserts an audio SFX/BGM onto a free audio track via ResolveGateway."""
        def _task(resolve):
            tl = None
            proj = None
            if resolve and hasattr(resolve, "GetProjectManager"):
                pm = resolve.GetProjectManager()
                proj = pm.GetCurrentProject() if pm else None
                tl = proj.GetCurrentTimeline() if proj else None

            if not tl:
                from src.core.resolve_api import ResolveAutomation
                auto = self._gateway._worker.get_automation() or ResolveAutomation()
                if not auto.is_connected() and not self._gateway._worker.get_resolve_object():
                    auto.connect()
                tl = auto.get_active_timeline()
                proj = auto.current_project

            if not tl:
                raise RuntimeError(_no_timeline_message(locals().get("auto")))

            import math
            from src.core.resolve_api import current_record_frame, _nominal_fps
            start_frame, current_tc = current_record_frame(tl, proj)
            _, fps = _nominal_fps(proj)
            end_frame = start_frame + max(1, math.ceil(duration * fps))

            # Determine audio track
            current_count = int(tl.GetTrackCount("audio"))
            if mode == "auto" and not preferred_track:
                track_num = self._find_free_audio_track(tl, start_frame, end_frame)
            else:
                track_num = max(2, current_count + 1) if mode == "new_track" else (preferred_track or 2)
                for _ in range(current_count, track_num):
                    if not tl.AddTrack("audio"):
                        raise RuntimeError("Không thể tạo track audio mới.")

            # Trích xuất volume offset nếu có truyền từ extra_params (ví dụ BGM -18dB)
            vol_offset = None
            if extra_params:
                raw_vol = extra_params.get("volume_offset", extra_params.get("volume_offset_db"))
                if isinstance(raw_vol, (int, float)) and not isinstance(raw_vol, bool) and not math.isnan(raw_vol) and not math.isinf(raw_vol):
                    vol_offset = float(raw_vol)

            # Chèn đúng frame đã dùng để tìm track trống.
            from src.core.resolve_api import ResolveAutomation
            auto = self._gateway._worker.get_automation() or ResolveAutomation()
            if not auto.is_connected() and not self._gateway._worker.get_resolve_object():
                auto.connect()
            sfx_kwargs = {"target_track": track_num, "record_frame": start_frame}
            if vol_offset is not None:
                sfx_kwargs["volume_offset_db"] = vol_offset
            ok = auto.insert_sfx_to_track(file_path, **sfx_kwargs)

            if not ok:
                raise RuntimeError("Failed to insert audio to track")

            return {
                "track": f"A{track_num}",
                "start_frame": start_frame,
                "start_tc": current_tc,
                "duration": duration,
                "name": name,
            }

        def _on_success(data: dict):
            res = InsertResult(
                ok=True,
                track=data["track"],
                start_frame=data["start_frame"],
                start_tc=data["start_tc"],
                duration_sec=data["duration"],
                item_name=data["name"],
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err: str):
            res = InsertResult(ok=False, item_name=name, error=err, inserted_at=time.time())
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_on_success, errback=_on_error)

    def _insert_lut(
        self,
        name: str,
        file_path: str,
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Applies a 3D LUT or installs a DCTL/PowerGrade into DaVinci Resolve."""
        def _task(resolve):
            tl = None
            proj = None
            if resolve and hasattr(resolve, "GetProjectManager"):
                pm = resolve.GetProjectManager()
                proj = pm.GetCurrentProject() if pm else None
                tl = proj.GetCurrentTimeline() if proj else None

            from src.core.resolve_api import ResolveAutomation
            auto = self._gateway._worker.get_automation() or ResolveAutomation()
            if resolve:
                auto.resolve = resolve
                if proj:
                    auto.current_project = proj

            if not tl:
                if not auto.is_connected() and not self._gateway._worker.get_resolve_object():
                    auto.connect()
                tl = auto.get_active_timeline()
                proj = proj or auto.current_project

            if not file_path or not os.path.exists(file_path):
                raise FileNotFoundError(f"Không tìm thấy file LUT/DCTL: {file_path}")

            ext = os.path.splitext(file_path)[1].lower()
            tc = tl.GetCurrentTimecode() if (tl and hasattr(tl, "GetCurrentTimecode")) else "01:00:00:00"

            if ext in (".dctl", ".dctle"):
                from src.core.asset_manager import AssetManager
                dest = AssetManager.install_dctl_file(file_path)
                if proj and hasattr(proj, "RefreshLUTList"):
                    try:
                        proj.RefreshLUTList()
                    except Exception:
                        pass
                try:
                    app = QApplication.instance()
                    if app:
                        cb = QApplication.clipboard()
                        if cb:
                            cb.setText(name)
                except Exception:
                    pass
                msg = (
                    f"Đã cài đặt DCTL '{name}' vào DaVinci Resolve ({dest.parent.name})! "
                    f"Mở trang Color → kéo hiệu ứng OpenFX 'DCTL' vào Node và chọn '{name}' (đã sao chép tên)."
                )
                return tc, msg, "Color DCTL", name

            if ext in (".drx", ".dpx"):
                import shutil
                from src.core.asset_manager import AssetManager
                pg_dir = AssetManager.get_lut_install_dir().parent / "PowerGrades" / "ResolveFlow"
                pg_dir.mkdir(parents=True, exist_ok=True)
                src_p = Path(file_path)
                dest = pg_dir / src_p.name
                shutil.copy2(src_p, dest)
                for sib in src_p.parent.iterdir():
                    if sib.is_file() and sib.stem == src_p.stem and sib.suffix.lower() in (".dpx", ".png", ".jpg", ".jpeg"):
                        shutil.copy2(sib, pg_dir / sib.name)
                try:
                    app = QApplication.instance()
                    if app:
                        cb = QApplication.clipboard()
                        if cb:
                            cb.setText(str(dest))
                except Exception:
                    pass
                msg = (
                    f"Đã chuẩn bị PowerGrade '{name}'! Mở trang Color → Gallery → chuột phải chọn 'Import' "
                    f"từ thư mục {pg_dir} (đã sao chép đường dẫn)."
                )
                return tc, msg, "Color Gallery", name

            if not tl:
                raise RuntimeError(_no_timeline_message(auto))

            target_track = None
            skip_existing = True
            if extra_params:
                if "track" in extra_params:
                    try:
                        target_track = int(extra_params["track"])
                    except (ValueError, TypeError):
                        pass
                elif "preferred_track" in extra_params:
                    try:
                        target_track = int(extra_params["preferred_track"])
                    except (ValueError, TypeError):
                        pass
                if "skip_existing" in extra_params:
                    skip_existing = bool(extra_params["skip_existing"])

            res_dict = auto.apply_lut_to_current_clip(
                lut_path=os.path.abspath(file_path),
                target_track=target_track,
                skip_existing=skip_existing,
            )

            from collections.abc import Mapping
            from src.core.contracts.status_contracts import (
                MutationStatus,
                lut_result_to_mutation_outcome,
            )

            # Phân loại kết quả qua hợp đồng mutation chuẩn hóa
            mut_status, evidence = lut_result_to_mutation_outcome(res_dict)

            msg = res_dict.get("message", "") if isinstance(res_dict, Mapping) else ""
            clip_name = res_dict.get("clip_name", "") if isinstance(res_dict, Mapping) else ""
            track_idx = res_dict.get("track", 1) if isinstance(res_dict, Mapping) else 1

            if mut_status == MutationStatus.APPLIED:
                # applied > 0 và failed == 0 (kể cả khi skipped > 0)
                return tc, msg or f"Đã áp LUT vào clip '{clip_name}'", f"V{track_idx}", clip_name
            elif mut_status == MutationStatus.SKIPPED:
                # Bỏ qua hợp lệ (clip đã có LUT): giữ compatibility ok=False, thông báo chưa thay đổi
                raise RuntimeError(msg or f"Clip '{clip_name}' đã có LUT, chưa thay đổi")
            elif mut_status == MutationStatus.FAILED:
                # Thất bại hoàn toàn: applied == 0 và failed > 0
                raise RuntimeError(msg or f"Áp LUT vào clip '{clip_name}' thất bại")
            elif mut_status == MutationStatus.PARTIAL_APPLIED:
                # Đột biến một phần: applied > 0 và failed > 0 (báo lỗi vì InsertResult chưa có trục partial)
                partial_msg = f"Đã áp {evidence.applied_count} clip, lỗi {evidence.failed_count} clip."
                if msg:
                    partial_msg += f" Chi tiết: {msg}"
                raise RuntimeError(partial_msg)
            elif mut_status == MutationStatus.UNKNOWN:
                # Thiếu bộ đếm bằng chứng hoặc tất cả counters đều bằng 0
                warn_msg = "Kết quả áp LUT chưa xác minh (thiếu bộ đếm bằng chứng hoặc tất cả bằng 0)."
                if msg:
                    warn_msg += f" Chi tiết: {msg}"
                raise RuntimeError(warn_msg)
            else:
                raise RuntimeError(msg or f"Trạng thái áp LUT không xác định: {mut_status.value}")

        def _on_success(result_data):
            if isinstance(result_data, tuple):
                tc, msg, track_name, clip_name = result_data
            else:
                tc, msg, track_name, clip_name = result_data, f"Đã áp LUT '{name}'", "V1", ""
            res = InsertResult(
                ok=True,
                track=track_name,
                start_tc=tc,
                item_name=name,
                message=msg,
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err: str):
            res = InsertResult(
                ok=False,
                item_name=name,
                error=err,
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_on_success, errback=_on_error)

    def _insert_video(
        self,
        name: str,
        file_path: str,
        mode: str,
        duration: float,
        preferred_track: Optional[int],
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """Inserts a Video/Meme/B-Roll/Overlay onto a video track (default V3) via ResolveGateway."""
        def _task(resolve):
            from src.core.resolve_api import ResolveAutomation
            auto = self._gateway._worker.get_automation() or ResolveAutomation()
            if not auto.is_connected() and not self._gateway._worker.get_resolve_object():
                auto.connect()
            tl = auto.get_active_timeline()
            if not tl:
                raise RuntimeError(_no_timeline_message(auto))

            current_tc = tl.GetCurrentTimecode() if hasattr(tl, "GetCurrentTimecode") else "01:00:00:00"

            track_num = preferred_track or 3
            if mode == "new_track":
                if hasattr(tl, "GetTrackCount"):
                    track_num = tl.GetTrackCount("video") + 1
            elif mode == "timeline_track" and preferred_track:
                track_num = preferred_track

            target_path = os.path.abspath(file_path) if file_path and os.path.exists(os.path.abspath(file_path)) else file_path
            if not target_path or not os.path.isfile(target_path):
                raise RuntimeError(f"Không tìm thấy tệp media cho '{name}'")

            dur_sec = duration if duration > 0 else None
            ok = auto.insert_media_at_playhead(
                media_path=target_path,
                target_track=track_num,
                duration_sec=dur_sec,
            )
            if not ok:
                raise RuntimeError(f"Chèn video '{name}' vào Timeline thất bại")

            return {
                "track": f"V{track_num}",
                "start_tc": current_tc,
                "duration": duration,
                "name": name,
            }

        def _on_success(data: dict):
            res = InsertResult(
                ok=True,
                track=data["track"],
                start_tc=data["start_tc"],
                duration_sec=data["duration"],
                item_name=data["name"],
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err: str):
            res = InsertResult(ok=False, item_name=name, error=err, inserted_at=time.time())
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_on_success, errback=_on_error)

    def _insert_title_worker(
        self,
        name: str,
        mode: str,
        duration: float,
        preferred_track: Optional[int],
        callback: Optional[Callable[[InsertResult], None]],
        title_id: Optional[str] = None,
    ):
        """Executes title_insert_worker.py asynchronously using QProcess."""
        worker_script = Path(__file__).resolve().parent.parent / "core" / "title_insert_worker.py"
        if not worker_script.exists():
            res = InsertResult(ok=False, item_name=name, error="title_insert_worker.py not found")
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)
            return

        # Pause poller during worker run to avoid fusionscript IPC contention
        if self._state_service and hasattr(self._state_service, "pause_polling"):
            self._state_service.pause_polling()

        tid = title_id or name
        process = QProcess(self)
        args = [
            str(worker_script),
            "--title", str(tid),
            "--display-name", str(name),
            "--duration", str(duration),
            "--mode", str(mode),
        ]
        if preferred_track:
            args.extend(["--preferred-track", str(preferred_track)])

        output_chunks: List[str] = []

        def _read_stdout():
            data = process.readAllStandardOutput().data().decode("utf-8", errors="replace")
            output_chunks.append(data)

        def _on_finished(exit_code: int, exit_status: QProcess.ExitStatus):
            if self._state_service and hasattr(self._state_service, "resume_polling"):
                self._state_service.resume_polling()

            full_output = "".join(output_chunks)
            res = self._parse_worker_output(full_output, name, duration, exit_code)
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)
            process.deleteLater()

        process.readyReadStandardOutput.connect(_read_stdout)
        process.finished.connect(_on_finished)
        process.start(sys.executable, args)

    def _parse_worker_output(self, output: str, name: str, duration: float, exit_code: int) -> InsertResult:
        """Parses CHUNDVC_RESULT:{...} from worker stdout."""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("CHUNDVC_RESULT:"):
                payload_str = line[len("CHUNDVC_RESULT:"):]
                try:
                    data = json.loads(payload_str)
                    if data.get("ok"):
                        track_idx = data.get("track", 2)
                        return InsertResult(
                            ok=True,
                            track=f"V{track_idx}" if isinstance(track_idx, int) else str(track_idx),
                            start_tc=data.get("timecode", ""),
                            start_frame=data.get("start", -1),
                            duration_sec=duration,
                            item_name=data.get("item_name") or name,
                            inserted_at=time.time(),
                        )
                    else:
                        return InsertResult(
                            ok=False,
                            item_name=name,
                            error=data.get("error") or data.get("detail") or "Worker reported failure",
                            inserted_at=time.time(),
                        )
                except Exception as e:
                    logger.warning("Failed to parse worker JSON: %s", e)

        return InsertResult(
            ok=False,
            item_name=name,
            error=f"Worker exited with code {exit_code} without result payload",
            inserted_at=time.time(),
        )

    def _record_result(self, result: InsertResult):
        if result.ok:
            self._last_results.append(result)
            if len(self._last_results) > 20:
                self._last_results.pop(0)

    def undo(self, result: Optional[InsertResult] = None) -> Tuple[bool, str]:
        """
        Safely deletes the last inserted item within 30 seconds using timeline.DeleteClips(..., False).
        """
        target = result or (self._last_results[-1] if self._last_results else None)
        if not target:
            return False, "Không có thao tác nào gần đây để hoàn tác"

        # Check 30s timeout
        elapsed = time.time() - target.inserted_at
        if elapsed > 30.0:
            return False, f"Đã quá thời gian cho phép hoàn tác ({int(elapsed)}s > 30s)"

        def _undo_task(resolve):
            tl = None
            proj = None
            if resolve and hasattr(resolve, "GetProjectManager"):
                pm = resolve.GetProjectManager()
                proj = pm.GetCurrentProject() if pm else None
                tl = proj.GetCurrentTimeline() if proj else None

            is_title = isinstance(target.item_ref, dict) and target.item_ref.get("kind") == "title"

            if not tl and not is_title:
                from src.core.resolve_api import ResolveAutomation
                auto = self._gateway._worker.get_automation() or ResolveAutomation()
                tl = auto.get_active_timeline()

            if not tl:
                raise RuntimeError("Không tìm thấy Timeline đang mở")

            # Title-specific Undo contract: Xác minh project, timeline và clip identity tuyệt đối
            if is_title:
                ref = target.item_ref
                from src.core.resolve_bridge import _get_safe_uid

                cur_proj_name = proj.GetName() if proj and hasattr(proj, "GetName") else ""
                cur_proj_id = _get_safe_uid(proj)
                cur_tl_name = tl.GetName() if tl and hasattr(tl, "GetName") else ""
                cur_tl_id = _get_safe_uid(tl)

                raw_stored_proj = ref.get("project_id")
                raw_stored_tl = ref.get("timeline_id")
                stored_proj_id = raw_stored_proj.strip() if isinstance(raw_stored_proj, str) and not isinstance(raw_stored_proj, bool) else ""
                stored_tl_id = raw_stored_tl.strip() if isinstance(raw_stored_tl, str) and not isinstance(raw_stored_tl, bool) else ""

                if not stored_proj_id or not stored_tl_id:
                    raise RuntimeError("Không thể hoàn tác: Thiếu Project ID hoặc Timeline ID ổn định để định danh an toàn.")

                if ref.get("project_name") and cur_proj_name and ref["project_name"] != cur_proj_name:
                    raise RuntimeError("Không thể hoàn tác: Project hoặc Timeline trong DaVinci Resolve đã thay đổi.")
                if not cur_proj_id or cur_proj_id != stored_proj_id:
                    raise RuntimeError("Không thể hoàn tác: Project ID trong DaVinci Resolve đã thay đổi hoặc không thể xác minh.")

                if ref.get("timeline_name") and cur_tl_name and ref["timeline_name"] != cur_tl_name:
                    raise RuntimeError("Không thể hoàn tác: Project hoặc Timeline trong DaVinci Resolve đã thay đổi.")
                if not cur_tl_id or cur_tl_id != stored_tl_id:
                    raise RuntimeError("Không thể hoàn tác: Timeline ID trong DaVinci Resolve đã thay đổi hoặc không thể xác minh.")

                raw_uid = ref.get("unique_id")
                uid = raw_uid.strip() if isinstance(raw_uid, str) and not isinstance(raw_uid, bool) else ""
                if not uid:
                    raise RuntimeError("Không thể hoàn tác: Clip không có Unique ID ổn định để định danh an toàn.")

                track_index = ref.get("track_index")
                if track_index is None:
                    raise RuntimeError("Không thể hoàn tác: Không xác định được track của clip.")

                items = tl.GetItemListInTrack("video", track_index) or []
                matching_items = [it for it in items if _get_safe_uid(it) == uid]

                if len(matching_items) == 0:
                    raise RuntimeError(f"Clip '{target.item_name}' (ID: {uid}) không còn tồn tại trên {target.track}.")
                if len(matching_items) > 1:
                    raise RuntimeError(f"Không thể hoàn tác: Phát hiện nhiều clip có cùng Unique ID trên {target.track}.")

                item_to_delete = matching_items[0]
                ok = tl.DeleteClips([item_to_delete], False)
                if not ok:
                    raise RuntimeError("Lệnh DeleteClips từ DaVinci Resolve thất bại.")
                return True

            track_type = "video" if target.track.startswith("V") else ("audio" if target.track.startswith("A") else "video")
            try:
                track_index = int(target.track[1:])
            except Exception:
                track_index = 2

            if not hasattr(tl, "GetItemListInTrack"):
                raise RuntimeError("API DaVinci Resolve không hỗ trợ GetItemListInTrack")

            items = tl.GetItemListInTrack(track_type, track_index) or []
            item_to_delete = None

            for it in items:
                it_name = it.GetName() if hasattr(it, "GetName") else ""
                it_start = it.GetStart() if hasattr(it, "GetStart") else -999

                # Match by name and frame position
                if it_name == target.item_name:
                    if target.start_frame < 0 or abs(it_start - target.start_frame) <= 5:
                        item_to_delete = it
                        break

            if not item_to_delete:
                raise RuntimeError(f"Clip '{target.item_name}' không còn ở vị trí ban đầu trên {target.track}")

            if not hasattr(tl, "DeleteClips"):
                raise RuntimeError("API DaVinci Resolve không hỗ trợ DeleteClips")

            ok = tl.DeleteClips([item_to_delete], False)  # ripple=False (Lift delete)
            if not ok:
                raise RuntimeError("DeleteClips thất bại")
            return True

        try:
            self._gateway.submit_sync(_undo_task, timeout=4.0)
            if target in self._last_results:
                self._last_results.remove(target)
            return True, f"Đã hoàn tác chèn '{target.item_name}' trên {target.track}"
        except Exception as e:
            return False, f"Lỗi hoàn tác: {e}"
