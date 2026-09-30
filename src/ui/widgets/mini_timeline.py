"""
Widget hiển thị thanh Timeline Mini trực quan (Interactive Mini Timeline Preview).
Vẽ trực tiếp các khối phân đoạn video (Voice, Silent Cut, Speed-ramp Timelapse,
Vlog Hook Teaser, Subtitles) và thước đo thời gian bằng QPainter hiệu năng cao.
"""

import os
from enum import Enum
from typing import List, Dict, Any, Optional
from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QRectF, QPointF, Signal as pyqtSignal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
    QMouseEvent, QPaintEvent, QLinearGradient
)
from src.core.autocut import seconds_to_timecode


class TimelineBlockType(str, Enum):
    VOICE = "voice"            # Phân đoạn giọng nói giữ lại (Xanh lá)
    SILENCE_CUT = "cut"        # Khoảng lặng bị cắt bỏ (Đỏ)
    SPEEDUP = "speedup"        # Tua nhanh 8x (Vàng cam)
    HOOK_TEASER = "hook"       # Đoạn trích xuất Teaser (Tím neon)
    SUBTITLE = "subtitle"      # Câu phụ đề (Xanh dương)


class TimelineBlock:
    """
    Thông tin một khối hình chữ nhật trên Mini Timeline.
    """
    def __init__(
        self,
        start_sec: float,
        end_sec: float,
        block_type: TimelineBlockType,
        label: str = "",
        track_index: int = 0,
        payload: Optional[Any] = None
    ):
        self.start_sec = max(0.0, float(start_sec))
        self.end_sec = max(self.start_sec, float(end_sec))
        self.block_type = block_type
        self.label = label
        self.track_index = track_index
        self.payload = payload

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


class MiniTimelineWidget(QWidget):
    """
    Widget đồ họa vector hiển thị cấu trúc Timeline thu nhỏ.
    """
    playhead_changed = pyqtSignal(float)  # Phát tín hiệu khi người dùng click/kéo playhead (giây)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setMaximumHeight(190)
        self.setMouseTracking(True)

        self.duration: float = 60.0
        self.fps: float = 30.0
        self.playhead_sec: float = 0.0
        self.is_scrubbing: bool = False

        self.blocks: List[TimelineBlock] = []
        self.stats_kept_dur: float = 0.0
        self.stats_cut_dur: float = 0.0
        self.stats_speedup_dur: float = 0.0

        # Cấu hình màu sắc hiện đại chuẩn Cyberpunk / Studio Dark Theme
        self.COLOR_BG = QColor("#111318")
        self.COLOR_RULER_BG = QColor("#1A1D24")
        self.COLOR_RULER_TEXT = QColor("#8E95A5")
        self.COLOR_TRACK_BG = QColor("#161920")
        self.COLOR_GRID_LINE = QColor("#222733")

        self.COLOR_VOICE = QColor("#10B981")        # Xanh ngọc lục bảo Emerald
        self.COLOR_SILENCE_CUT = QColor("#EF4444")  # Đỏ Ruby
        self.COLOR_SPEEDUP = QColor("#F59E0B")      # Vàng Amber
        self.COLOR_HOOK = QColor("#8B5CF6")         # Tím Neon Purple
        self.COLOR_SUBTITLE = QColor("#3B82F6")     # Xanh Cyan Blue
        self.COLOR_PLAYHEAD = QColor("#F43F5E")     # Đỏ tươi Rose

        self._init_default_demo_data()

    def _init_default_demo_data(self):
        """Khởi tạo một số phân đoạn mẫu ban đầu khi chưa nạp file."""
        self.duration = 60.0
        self.blocks = [
            TimelineBlock(0.0, 15.0, TimelineBlockType.VOICE, "Intro", track_index=0),
            TimelineBlock(15.0, 20.0, TimelineBlockType.SILENCE_CUT, "Cut", track_index=0),
            TimelineBlock(20.0, 40.0, TimelineBlockType.VOICE, "Main Talk", track_index=0),
            TimelineBlock(40.0, 45.0, TimelineBlockType.SPEEDUP, "8x Speed", track_index=0),
            TimelineBlock(45.0, 60.0, TimelineBlockType.VOICE, "Outro", track_index=0),
        ]
        self._recompute_stats()

    def set_duration(self, duration: float, fps: float = 30.0):
        """Đặt tổng thời lượng video."""
        self.duration = max(1.0, float(duration))
        self.fps = fps if fps > 0 else 30.0
        self.playhead_sec = min(self.playhead_sec, self.duration)
        self.update()

    def clear(self):
        """Xóa toàn bộ các khối timeline."""
        self.blocks.clear()
        self.stats_kept_dur = 0.0
        self.stats_cut_dur = 0.0
        self.stats_speedup_dur = 0.0
        self.playhead_sec = 0.0
        self.update()

    def _recompute_stats(self):
        """Tính toán tổng thời lượng giữ lại, cắt bỏ và tua nhanh."""
        self.stats_kept_dur = 0.0
        self.stats_cut_dur = 0.0
        self.stats_speedup_dur = 0.0

        for b in self.blocks:
            if b.track_index == 0:
                if b.block_type == TimelineBlockType.VOICE:
                    self.stats_kept_dur += b.duration
                elif b.block_type == TimelineBlockType.SILENCE_CUT:
                    self.stats_cut_dur += b.duration
                elif b.block_type == TimelineBlockType.SPEEDUP:
                    self.stats_speedup_dur += b.duration

    def set_timeline_data(
        self,
        duration: float,
        keep_intervals: Optional[List[tuple]] = None,
        silence_intervals: Optional[List[tuple]] = None,
        speedup_segments: Optional[List[Dict[str, Any]]] = None,
        teaser_items: Optional[List[Dict[str, Any]]] = None,
        subtitles: Optional[List[Dict[str, Any]]] = None,
        fps: float = 30.0
    ):
        """
        Nạp dữ liệu phân tích từ Pipeline vào Mini Timeline.
        """
        self.duration = max(1.0, float(duration))
        self.fps = fps if fps > 0 else 30.0
        self.blocks.clear()

        # 1. Track 0: Các khối Voice / Cut / Speedup
        if speedup_segments:
            for seg in speedup_segments:
                st = seg.get("start", 0.0)
                et = seg.get("end", 0.0)
                stype = seg.get("type", "voice")
                if stype == "speedup":
                    btype = TimelineBlockType.SPEEDUP
                    lbl = f"8x ({et-st:.1f}s)"
                else:
                    btype = TimelineBlockType.VOICE
                    lbl = f"Voice ({et-st:.1f}s)"
                self.blocks.append(TimelineBlock(st, et, btype, lbl, track_index=0))
        elif keep_intervals is not None:
            # Tạo các khối Keep (Voice) và Cut (Silence)
            sorted_keeps = sorted(keep_intervals, key=lambda x: x[0])
            last_end = 0.0
            for k_start, k_end in sorted_keeps:
                if k_start > last_end + 0.05:
                    self.blocks.append(TimelineBlock(
                        last_end, k_start, TimelineBlockType.SILENCE_CUT,
                        f"Cut ({k_start - last_end:.1f}s)", track_index=0
                    ))
                self.blocks.append(TimelineBlock(
                    k_start, k_end, TimelineBlockType.VOICE,
                    f"Voice ({k_end - k_start:.1f}s)", track_index=0
                ))
                last_end = k_end
            if last_end < self.duration - 0.05:
                self.blocks.append(TimelineBlock(
                    last_end, self.duration, TimelineBlockType.SILENCE_CUT,
                    f"Cut ({self.duration - last_end:.1f}s)", track_index=0
                ))
        elif silence_intervals:
            # Chỉ có khoảng lặng
            sorted_silences = sorted(silence_intervals, key=lambda x: x[0])
            last_pos = 0.0
            for s_start, s_end in sorted_silences:
                if s_start > last_pos:
                    self.blocks.append(TimelineBlock(
                        last_pos, s_start, TimelineBlockType.VOICE, "Voice", track_index=0
                    ))
                self.blocks.append(TimelineBlock(
                    s_start, s_end, TimelineBlockType.SILENCE_CUT, "Cut", track_index=0
                ))
                last_pos = s_end
            if last_pos < self.duration:
                self.blocks.append(TimelineBlock(
                    last_pos, self.duration, TimelineBlockType.VOICE, "Voice", track_index=0
                ))
        else:
            self.blocks.append(TimelineBlock(
                0.0, self.duration, TimelineBlockType.VOICE, "Full Clip", track_index=0
            ))

        # 2. Track 1: Teaser Hooks (nếu có)
        if teaser_items:
            for t_item in teaser_items:
                s_in = t_item.get("src_in", 0.0)
                s_out = t_item.get("src_out", 0.0)
                order = t_item.get("order", 1)
                self.blocks.append(TimelineBlock(
                    s_in, s_out, TimelineBlockType.HOOK_TEASER,
                    f"Hook #{order}", track_index=1, payload=t_item
                ))

        # 3. Track 2: Subtitles (nếu có)
        if subtitles:
            for sub in subtitles:
                s_start = sub.get("start", 0.0)
                s_end = sub.get("end", 0.0)
                txt = sub.get("text", "")
                self.blocks.append(TimelineBlock(
                    s_start, s_end, TimelineBlockType.SUBTITLE,
                    txt[:15] + "..." if len(txt) > 15 else txt,
                    track_index=2, payload=sub
                ))

        self._recompute_stats()
        self.update()

    def update_proposed_segments(self, proposed_segments: List[Any]):
        """
        Cập nhật trạng thái duyệt phân đoạn (Phase 1 AI Review).
        Nếu đề xuất là 'cut' và được approve -> Đỏ; nếu uncheck hoặc 'keep' -> Xanh.
        """
        if not proposed_segments:
            return

        for p in proposed_segments:
            p_start = getattr(p, "start", 0.0)
            p_end = getattr(p, "end", 0.0)
            p_dec = getattr(p, "decision", "keep")
            p_app = getattr(p, "approved", True)

            # Tìm và cập nhật block tương ứng ở Track 0
            for b in self.blocks:
                if b.track_index == 0 and abs(b.start_sec - p_start) < 0.2:
                    if p_dec == "cut" and p_app:
                        b.block_type = TimelineBlockType.SILENCE_CUT
                        b.label = f"Cut ({b.duration:.1f}s)"
                    else:
                        b.block_type = TimelineBlockType.VOICE
                        b.label = f"Voice ({b.duration:.1f}s)"

        self._recompute_stats()
        self.update()

    def set_playhead_seconds(self, sec: float):
        """Di chuyển con trỏ Playhead đến vị trí chỉ định."""
        self.playhead_sec = max(0.0, min(float(sec), self.duration))
        self.update()

    # --- TÍNH TOÁN TỌA ĐỘ VẼ ---
    def _time_to_x(self, t: float, width: float, margin_left: float = 12, margin_right: float = 12) -> float:
        usable_w = width - margin_left - margin_right
        if self.duration <= 0:
            return margin_left
        return margin_left + (t / self.duration) * usable_w

    def _x_to_time(self, x: float, width: float, margin_left: float = 12, margin_right: float = 12) -> float:
        usable_w = width - margin_left - margin_right
        if usable_w <= 0:
            return 0.0
        rel_x = max(0.0, min(x - margin_left, usable_w))
        return (rel_x / usable_w) * self.duration

    # --- SỰ KIỆN CHUỘT ---
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_scrubbing = True
            self._handle_scrub(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent):
        pos_x = event.position().x()
        pos_y = event.position().y()

        if self.is_scrubbing:
            self._handle_scrub(pos_x)
        else:
            # Hiển thị Tooltip thông tin khi hover
            t = self._x_to_time(pos_x, self.width())
            tc_str = seconds_to_timecode(t, self.fps)

            # Tìm block đang trỏ tới
            hovered_block = None
            for b in self.blocks:
                if b.start_sec <= t <= b.end_sec:
                    hovered_block = b
                    break

            info = f"⏱ Mốc: {tc_str} ({t:.2f}s)"
            if hovered_block:
                type_name = {
                    TimelineBlockType.VOICE: "🟢 Giọng nói (Kept)",
                    TimelineBlockType.SILENCE_CUT: "🔴 Khoảng lặng (Cut)",
                    TimelineBlockType.SPEEDUP: "🟡 Tua nhanh (8x)",
                    TimelineBlockType.HOOK_TEASER: "🟣 Intro Hook",
                    TimelineBlockType.SUBTITLE: "📝 Phụ đề"
                }.get(hovered_block.block_type, "Phân đoạn")
                info += f"\nTrạng thái: {type_name}\nĐộ dài: {hovered_block.duration:.2f}s"
                if hovered_block.label:
                    info += f"\nNhãn: {hovered_block.label}"

            QToolTip.showText(self.mapToGlobal(QPointF(pos_x, pos_y).toPoint()), info, self)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_scrubbing = False

    def _handle_scrub(self, x: float):
        t = self._x_to_time(x, self.width())
        self.playhead_sec = t
        self.playhead_changed.emit(t)
        self.update()

    # --- VẼ ĐỒ HỌA (PAINT EVENT) ---
    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())
        margin_x = 12.0

        # 1. Vẽ nền tổng thể (Background Frame)
        painter.fillRect(QRectF(0, 0, w, h), self.COLOR_BG)

        # 2. Vùng Thước Đo Thời Gian (Ruler Area)
        ruler_h = 24.0
        painter.fillRect(QRectF(0, 0, w, ruler_h), self.COLOR_RULER_BG)

        # Vẽ các vạch chia thời gian trên thước đo
        font_ruler = QFont("Segoe UI", 8)
        painter.setFont(font_ruler)
        painter.setPen(QPen(self.COLOR_RULER_TEXT, 1))

        num_ticks = 6
        step_sec = self.duration / max(1, num_ticks - 1)
        for i in range(num_ticks):
            t_val = i * step_sec
            x_pos = self._time_to_x(t_val, w, margin_x, margin_x)
            painter.drawLine(QPointF(x_pos, ruler_h - 6), QPointF(x_pos, ruler_h))
            tc_display = seconds_to_timecode(t_val, self.fps)[:8] # HH:MM:SS
            painter.drawText(QRectF(x_pos - 30, 2, 60, 14), Qt.AlignCenter, tc_display)

        # 3. Vẽ các Track Timeline
        track_y_start = ruler_h + 6.0
        track0_h = 32.0   # Track chính (Voice/Cut/Speedup)
        track1_h = 16.0   # Track Hook Teaser
        track2_h = 14.0   # Track Subtitles

        # Vẽ nền các Track
        usable_w = w - 2 * margin_x
        painter.fillRect(QRectF(margin_x, track_y_start, usable_w, track0_h), self.COLOR_TRACK_BG)

        has_hooks = any(b.track_index == 1 for b in self.blocks)
        has_subs = any(b.track_index == 2 for b in self.blocks)

        if has_hooks:
            painter.fillRect(QRectF(margin_x, track_y_start + track0_h + 3, usable_w, track1_h), self.COLOR_TRACK_BG)
        if has_subs:
            sub_y = track_y_start + track0_h + (track1_h + 6 if has_hooks else 3)
            painter.fillRect(QRectF(margin_x, sub_y, usable_w, track2_h), self.COLOR_TRACK_BG)

        # 4. Vẽ các khối Block
        font_block = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(font_block)

        for b in self.blocks:
            x_in = self._time_to_x(b.start_sec, w, margin_x, margin_x)
            x_out = self._time_to_x(b.end_sec, w, margin_x, margin_x)
            bw = max(2.0, x_out - x_in)

            if b.track_index == 0:
                by = track_y_start
                bh = track0_h
            elif b.track_index == 1:
                by = track_y_start + track0_h + 3
                bh = track1_h
            else:
                by = track_y_start + track0_h + (track1_h + 6 if has_hooks else 3)
                bh = track2_h

            # Chọn màu và gradient
            if b.block_type == TimelineBlockType.VOICE:
                col = self.COLOR_VOICE
            elif b.block_type == TimelineBlockType.SILENCE_CUT:
                col = self.COLOR_SILENCE_CUT
            elif b.block_type == TimelineBlockType.SPEEDUP:
                col = self.COLOR_SPEEDUP
            elif b.block_type == TimelineBlockType.HOOK_TEASER:
                col = self.COLOR_HOOK
            else:
                col = self.COLOR_SUBTITLE

            grad = QLinearGradient(x_in, by, x_in, by + bh)
            grad.setColorAt(0.0, col.lighter(115))
            grad.setColorAt(1.0, col.darker(110))

            block_rect = QRectF(x_in, by, bw, bh)
            painter.setPen(QPen(col.darker(140), 1))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(block_rect, 3, 3)

            # Vẽ nhãn text nếu khối đủ rộng (>= 35px)
            if bw >= 35.0 and b.label:
                painter.setPen(QPen(QColor("#FFFFFF"), 1))
                painter.drawText(block_rect.adjusted(3, 0, -3, 0), Qt.AlignCenter, b.label)

        # 5. Vẽ Playhead Cursor (Con trỏ thời gian màu đỏ/hồng tươi)
        playhead_x = self._time_to_x(self.playhead_sec, w, margin_x, margin_x)
        painter.setPen(QPen(self.COLOR_PLAYHEAD, 2))
        painter.drawLine(QPointF(playhead_x, 0), QPointF(playhead_x, h - 30))

        # Đầu mút con trỏ Playhead
        painter.setBrush(QBrush(self.COLOR_PLAYHEAD))
        painter.setPen(Qt.NoPen)
        tri_top = [
            QPointF(playhead_x - 5, 0),
            QPointF(playhead_x + 5, 0),
            QPointF(playhead_x, 8)
        ]
        painter.drawPolygon(tri_top)

        # 6. Thanh Chú Thích & Thống Kê Footer (Legend Bar)
        footer_y = h - 24.0
        painter.fillRect(QRectF(0, footer_y, w, 24.0), self.COLOR_RULER_BG)

        font_legend = QFont("Segoe UI", 8)
        painter.setFont(font_legend)
        painter.setPen(QPen(QColor("#D1D5DB"), 1))

        pct_kept = (self.stats_kept_dur / self.duration * 100.0) if self.duration > 0 else 0.0
        pct_cut = (self.stats_cut_dur / self.duration * 100.0) if self.duration > 0 else 0.0

        stats_text = (
            f"🟢 Giữ lại: {self.stats_kept_dur:.1f}s ({pct_kept:.1f}%) | "
            f"🔴 Cắt bỏ: {self.stats_cut_dur:.1f}s ({pct_cut:.1f}%)"
        )
        if self.stats_speedup_dur > 0:
            stats_text += f" | 🟡 Tua 8x: {self.stats_speedup_dur:.1f}s"
        stats_text += f" | ⏱ Tổng: {self.duration:.1f}s"

        painter.drawText(QRectF(margin_x, footer_y, w - 2 * margin_x, 22.0), Qt.AlignLeft | Qt.AlignVCenter, stats_text)

        painter.end()
