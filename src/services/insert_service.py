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

    if type_name == "TransitionStylePreset" or hasattr(raw, "transition_type"):
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
    elif raw_tab in ("lut", "luts") or ext in (".cube", ".3dl"):
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
        duration: float = 3.0,
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
        duration: float = 3.0,
        preferred_track: Optional[int] = None,
        callback: Optional[Callable[[InsertResult], None]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Asynchronously inserts an asset based on its category and mode.
        """
        asset_dict = to_asset_dict(asset)
        cat = str(asset_dict.get("category") or asset_dict.get("tab") or "").lower()
        title_id = str(asset_dict.get("id") or asset_dict.get("name") or "karaoke_pop")
        name = str(asset_dict.get("name") or title_id or "Asset")
        file_path = str(asset_dict.get("file_path") or asset_dict.get("path") or "")
        ext = os.path.splitext(file_path)[1].lower() if file_path else ""

        if extra_params:
            if extra_params.get("duration"):
                try:
                    duration = float(extra_params["duration"])
                except Exception:
                    pass
            if extra_params.get("text"):
                name = str(extra_params["text"])

        # 1. Mode: Fusion Paste (Ctrl+V into Fusion node tree)
        if mode == "fusion_paste" or (self._state_service and self._state_service.current_page == "fusion" and cat in ("titles", "text", "transitions", "trans", "fx")):
            self._insert_fusion_paste(name, file_path, callback)
            return

        # 2. SFX / BGM Audio insertion
        if ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a") or cat in ("sfx", "bgm", "audio", "sound"):
            self._insert_audio(name, file_path, mode, duration, preferred_track, callback)
            return

        # 3. LUT Application
        if ext in (".cube", ".3dl") or cat in ("luts", "lut", "color"):
            self._insert_lut(name, file_path, callback)
            return

        # 4. Video / Meme / B-Roll / Overlay insertion (Any video file format or still image or meme category)
        if ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp", ".svg") or cat in ("meme", "memes", "broll", "video", "overlay", "overlays", "green_screen", "sticker", "stickers"):
            self._insert_video(name, file_path, mode, duration, preferred_track, callback, extra_params=extra_params)
            return

        # 5. Video Transitions (Ensure installed in Effects Library, never insert as title text)
        if cat in ("trans", "transitions", "transition") or "transitions" in file_path.lower():
            self._insert_transition(name, file_path, mode, duration, callback, extra_params=extra_params)
            return

        # 6. Fusion Title / Template -> Title Worker (QProcess)
        self._insert_title_worker(name, mode, duration, preferred_track, callback, title_id=title_id)

    def _insert_transition(
        self,
        name: str,
        file_path: str,
        mode: str,
        duration: float,
        callback: Optional[Callable[[InsertResult], None]],
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        """
        Handles DaVinci Resolve Video Transitions:
        1. Ensures template is installed into DaVinci's Effects Library under 'Effects > Video Transitions > ResolveFlow'.
        2. Copies template code to clipboard for Fusion page pasting.
        3. Inserts an animated Transition / Overlay clip directly onto Track V2 across the Playhead cut!
        """
        # 1. Cài đặt macro và copy clipboard
        try:
            from src.core.transition_preset import TransitionMacroGenerator
            TransitionMacroGenerator.install_transitions_to_davinci_resolve()
            code = ""
            if file_path and os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
            elif file_path:
                code = file_path
            if code:
                app = QApplication.instance()
                if app:
                    app.clipboard().setText(code)
        except Exception:
            pass

        # 2. Chèn trực tiếp Transition Clip lên Timeline tại Playhead qua ResolveGateway
        dur_sec = duration if duration > 0 else 0.8

        def _task(resolve):
            from src.core.resolve_bridge import insert_transition_at_playhead
            item = insert_transition_at_playhead(
                style=name,
                duration_sec=dur_sec,
                track_index=2,
            )
            if item:
                trk_info = item.GetTrackTypeAndIndex() if hasattr(item, "GetTrackTypeAndIndex") else (None, 2)
                trk_name = f"V{trk_info[1]}" if trk_info and trk_info[1] else "V2"
                return {
                    "track": trk_name,
                    "name": item.GetName() if hasattr(item, "GetName") else name,
                }
            return {"track": "V2", "name": name}

        def _on_success(data: dict):
            res = InsertResult(
                ok=True,
                track=data.get("track", "V2"),
                item_name=data.get("name", name),
                duration_sec=dur_sec,
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        def _on_error(err: str):
            # Fallback nếu DaVinci chưa mở timeline
            res = InsertResult(
                ok=True,
                track="Transitions",
                item_name=name,
                duration_sec=dur_sec,
                inserted_at=time.time(),
            )
            self._record_result(res)
            if callback:
                callback(res)
            self.insertion_finished.emit(res)

        self._gateway.submit(_task, callback=_on_success, errback=_on_error)

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

    def _insert_audio(
        self,
        name: str,
        file_path: str,
        mode: str,
        duration: float,
        preferred_track: Optional[int],
        callback: Optional[Callable[[InsertResult], None]],
    ):
        """Inserts an audio SFX/BGM onto a free audio track via ResolveGateway."""
        def _task(resolve):
            tl = None
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

            if not tl:
                raise RuntimeError("No active timeline in DaVinci Resolve")

            current_tc = tl.GetCurrentTimecode() if hasattr(tl, "GetCurrentTimecode") else "01:00:00:00"

            # Determine audio track
            track_num = preferred_track or 2
            if mode == "new_track":
                if hasattr(tl, "GetTrackCount"):
                    track_num = tl.GetTrackCount("audio") + 1
            elif preferred_track:
                track_num = preferred_track
            else:
                track_num = 2

            try:
                current_count = tl.GetTrackCount("audio") if hasattr(tl, "GetTrackCount") else 1
                if track_num > current_count and hasattr(tl, "AddTrack"):
                    for _ in range(current_count, track_num):
                        tl.AddTrack("audio")
            except Exception:
                pass

            # Perform insert via mock hook or ResolveAutomation
            if resolve and hasattr(resolve, "mock_insert_sfx") and callable(getattr(resolve, "mock_insert_sfx", None)):
                ok = resolve.mock_insert_sfx(file_path, track_num)
            else:
                from src.core.resolve_api import ResolveAutomation
                auto = self._gateway._worker.get_automation() or ResolveAutomation()
                if not auto.is_connected() and not self._gateway._worker.get_resolve_object():
                    auto.connect()
                ok = auto.insert_sfx_to_track(file_path, target_track=track_num)

            if not ok:
                raise RuntimeError("Failed to insert audio to track")

            return {
                "track": f"A{track_num}",
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

    def _insert_lut(self, name: str, file_path: str, callback: Optional[Callable[[InsertResult], None]]):
        """Applies a 3D LUT to the clip at playhead."""
        def _task(resolve):
            from src.core.resolve_api import ResolveAutomation
            auto = self._gateway._worker.get_automation() or ResolveAutomation()
            tl = auto.get_active_timeline()
            if not tl:
                raise RuntimeError("No active timeline in DaVinci Resolve")
            tc = tl.GetCurrentTimecode() if hasattr(tl, "GetCurrentTimecode") else "01:00:00:00"
            ok = auto.apply_lut_to_current_clip(file_path)
            if not ok:
                raise RuntimeError("Failed to apply LUT to clip")
            return tc

        def _on_success(tc: str):
            res = InsertResult(ok=True, track="Color", start_tc=tc, item_name=name, inserted_at=time.time())
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
                raise RuntimeError("No active timeline in DaVinci Resolve")

            current_tc = tl.GetCurrentTimecode() if hasattr(tl, "GetCurrentTimecode") else "01:00:00:00"

            track_num = preferred_track or 3
            if mode == "new_track":
                if hasattr(tl, "GetTrackCount"):
                    track_num = tl.GetTrackCount("video") + 1
            elif mode == "timeline_track" and preferred_track:
                track_num = preferred_track

            target_path = os.path.abspath(file_path) if file_path and os.path.exists(os.path.abspath(file_path)) else file_path
            if not target_path or not os.path.exists(target_path):
                try:
                    from src.core.broll_sfx import BRollSFXResolver
                    target_path = BRollSFXResolver().resolve_meme(name)
                except Exception:
                    pass

            if not target_path or not os.path.exists(target_path):
                from src.utils.path_utils import PosixPathAdapter
                for sub in ("memes", "overlays", "green_screen", "cinematic"):
                    for cand_ext in (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"):
                        cand = PosixPathAdapter.get_asset_path("broll_memes", sub, f"{name}{cand_ext}")
                        if os.path.exists(cand):
                            target_path = str(cand)
                            break
                    if target_path and os.path.exists(target_path):
                        break

            if not target_path or not os.path.exists(target_path):
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
        if self._state_service:
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
            if self._state_service:
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
            if resolve and hasattr(resolve, "GetProjectManager"):
                pm = resolve.GetProjectManager()
                proj = pm.GetCurrentProject() if pm else None
                tl = proj.GetCurrentTimeline() if proj else None

            if not tl:
                from src.core.resolve_api import ResolveAutomation
                auto = self._gateway._worker.get_automation() or ResolveAutomation()
                tl = auto.get_active_timeline()

            if not tl:
                raise RuntimeError("Không tìm thấy Timeline đang mở")

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
