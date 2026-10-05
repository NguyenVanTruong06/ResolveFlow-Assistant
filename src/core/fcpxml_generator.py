import os
import html
from typing import List, Dict, Any, Optional, Union
from src.core.text_preset import TextStylePreset, PresetManager, hex_to_fcpxml_rgba

class FCPXMLGenerator:
    """
    Tạo tệp FCPXML (Final Cut Pro XML v1.9) chuyên nghiệp tích hợp:
    - Hệ thống Text Style Presets (Karaoke Pop, Bounce Word, Box Highlight, Glow/Neon, Clean Outline, Gradient Fill, Slide-in).
    - Hiệu ứng phụ đề động Karaoke (đổi màu chữ, viền nét và phóng to từ đang nói theo word timestamps).
    - Hỗ trợ định dạng khung hình Ngang 16:9 (1920x1080) và Dọc 9:16 (1080x1920).
    - Tương thích hoàn hảo với DaVinci Resolve Free và Studio (Text+/Fusion Text).
    """
    @staticmethod
    def _resolve_preset(
        preset: Optional[Union[TextStylePreset, str]] = None,
        font_name: str = "Arial",
        font_size: int = 48,
        standard_color: str = "1 1 1 1",
        highlight_color: str = "1 0.84 0 1",
        aspect_ratio: str = "16:9"
    ) -> Dict[str, Any]:
        """Chuẩn hóa preset và các thông số kiểu dáng Text+."""
        if isinstance(preset, str):
            mgr = PresetManager()
            preset_obj = mgr.get_preset(preset)
        elif isinstance(preset, TextStylePreset):
            preset_obj = preset
        else:
            preset_obj = None

        if preset_obj:
            f_name = preset_obj.font
            f_size = preset_obj.size
            std_col = preset_obj.get_fcpxml_standard_color()
            hl_col = preset_obj.get_fcpxml_highlight_color()
            out_col = preset_obj.get_fcpxml_outline_color()
            out_w = preset_obj.outline_width
            anim = preset_obj.animation
            bold_val = "1" if preset_obj.weight in ["bold", "extra_bold"] else "0"
            
            # Tính toán highlight size
            if anim in ["pop", "bounce"]:
                hl_size = int(f_size * 1.25)
            elif anim == "box_highlight":
                hl_size = f_size
            else:
                hl_size = int(f_size * 1.15) if anim != "static" else f_size

            # Stroke XML attributes
            stroke_attrs = ""
            if out_w > 0:
                stroke_attrs = f' strokeColor="{out_col}" strokeWidth="{int(out_w * 20)}"'

            return {
                "font_name": f_name,
                "font_size": f_size,
                "highlight_size": hl_size,
                "standard_color": std_col,
                "highlight_color": hl_col,
                "outline_color": out_col,
                "stroke_attrs": stroke_attrs,
                "bold": bold_val,
                "animation": anim,
                "preset": preset_obj
            }
        else:
            # Fallback về tham số truyền tay
            std_col = hex_to_fcpxml_rgba(standard_color)
            hl_col = hex_to_fcpxml_rgba(highlight_color)
            return {
                "font_name": font_name,
                "font_size": font_size,
                "highlight_size": int(font_size * 1.2),
                "standard_color": std_col,
                "highlight_color": hl_col,
                "outline_color": "0 0 0 1",
                "stroke_attrs": ' strokeColor="0 0 0 1" strokeWidth="2"',
                "bold": "1",
                "animation": "pop",
                "preset": None
            }

    @staticmethod
    def _get_fcpxml_frame_duration(fps: float) -> str:
        """Tính toán chuỗi frameDuration chuẩn Apple FCPXML v1.9 theo FPS thực tế."""
        try:
            fps_r = round(float(fps), 2)
            if fps_r in [23.97, 23.98]:
                return "1001/24000s"
            elif fps_r == 29.97:
                return "1001/30000s"
            elif fps_r == 59.94:
                return "1001/60000s"
            elif fps_r == 24.0:
                return "1/24s"
            elif fps_r == 25.0:
                return "1/25s"
            elif fps_r == 30.0:
                return "1/30s"
            elif fps_r == 50.0:
                return "1/50s"
            elif fps_r == 60.0:
                return "1/60s"
            else:
                i_fps = int(round(fps)) if fps > 0 else 30
                return f"1/{i_fps}s"
        except Exception:
            return "1/30s"

    @staticmethod
    def generate_karaoke_fcpxml(
        subtitles: List[Dict[str, Any]], 
        output_path: str, 
        fps: float = 30.0,
        font_name: str = "Arial",
        font_size: int = 48,
        standard_color: str = "1 1 1 1",
        highlight_color: str = "1 0.84 0 1",
        aspect_ratio: str = "16:9",
        markers: Optional[List[Dict[str, Any]]] = None,
        preset: Optional[Union[TextStylePreset, str]] = None,
        **kwargs
    ) -> str:
        format_name = "FFVideoFormat1080x1920p" if aspect_ratio == "9:16" else "FFVideoFormat1080p"
        frame_dur_str = FCPXMLGenerator._get_fcpxml_frame_duration(fps)
        
        style = FCPXMLGenerator._resolve_preset(
            preset=preset,
            font_name=font_name,
            font_size=font_size,
            standard_color=standard_color,
            highlight_color=highlight_color,
            aspect_ratio=aspect_ratio
        )

        f_name = style["font_name"]
        f_size = style["font_size"]
        hl_size = style["highlight_size"]
        std_col = style["standard_color"]
        hl_col = style["highlight_color"]
        stroke_attrs = style["stroke_attrs"]
        bold_val = style["bold"]
        
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE fcpxml>',
            '<fcpxml version="1.9">',
            '  <resources>',
            f'    <format id="r1" name="{format_name}" frameDuration="{frame_dur_str}"/>',
            '    <effect id="r2" name="Text+" uid=".../Titles.localized/Bumper.localized/Text+.localized"/>',
            '  </resources>',
            '  <library>',
            '    <event name="ResolveFlow Project">',
            '      <project name="ResolveFlow Timeline">',
            f'        <sequence duration="3600s" format="r1" tcStart="0s">',
            '          <spine>',
            '            <gap name="Gap" offset="0s" duration="3600s">'
        ]

        for idx, sub in enumerate(subtitles):
            words = sub.get("words", [])
            card_start = sub["start"]
            card_end = sub["end"]
            card_text = sub["text"]

            if not words or style["animation"] == "static":
                start_ms = int(card_start * 1000)
                dur_ms = int((card_end - card_start) * 1000)
                if dur_ms <= 0:
                    continue
                
                title_xml = FCPXMLGenerator._build_title_element(
                    offset_ms=start_ms,
                    dur_ms=dur_ms,
                    text=html.escape(card_text),
                    font_name=f_name,
                    font_size=f_size,
                    color=std_col,
                    stroke_attrs=stroke_attrs,
                    bold=bold_val
                )
                lines.append(title_xml)
                continue

            for w_idx, active_word in enumerate(words):
                w_start = active_word.get("start", card_start)
                if w_idx < len(words) - 1:
                    w_end = words[w_idx + 1].get("start", active_word.get("end", card_end))
                else:
                    w_end = max(active_word.get("end", card_end), card_end)
                
                w_start = max(card_start, min(card_end, w_start))
                w_end = max(card_start, min(card_end, w_end))
                
                if w_end <= w_start:
                    w_end = w_start + 0.1

                w_start_ms = int(w_start * 1000)
                w_dur_ms = int((w_end - w_start) * 1000)
                if w_dur_ms <= 0:
                    continue

                text_spans = []
                for sub_w in words:
                    raw_word = sub_w["word"].strip() + " "
                    escaped_word = html.escape(raw_word)
                    if sub_w == active_word:
                        text_spans.append(
                            f'<text-style ref="ts_highlight">{escaped_word}</text-style>'
                        )
                    else:
                        text_spans.append(
                            f'<text-style ref="ts_normal">{escaped_word}</text-style>'
                        )

                text_content = "".join(text_spans)

                title_xml = f"""
              <title ref="r2" offset="{w_start_ms}/1000s" duration="{w_dur_ms}/1000s" start="0s" role="Video">
                <text>
                  {text_content}
                </text>
                <text-style-def id="ts_normal">
                  <text-style font="{f_name}" fontSize="{f_size}" fontColor="{std_col}"{stroke_attrs} alignment="center" bold="{bold_val}"/>
                </text-style-def>
                <text-style-def id="ts_highlight">
                  <text-style font="{f_name}" fontSize="{hl_size}" fontColor="{hl_col}"{stroke_attrs} alignment="center" bold="1"/>
                </text-style-def>
              </title>"""
                lines.append(title_xml)

        if markers:
            for m in markers:
                m_start_ms = int(m.get("time", 0.0) * 1000)
                m_dur_ms = int(m.get("duration", 1.0) * 1000)
                m_name = html.escape(m.get("name", "Marker"))
                m_note = html.escape(m.get("note", ""))
                marker_xml = f'              <marker start="{m_start_ms}/1000s" duration="{m_dur_ms}/1000s" value="{m_name}" note="{m_note}"/>'
                lines.append(marker_xml)

        lines.extend([
            '            </gap>',
            '          </spine>',
            '        </sequence>',
            '      </project>',
            '    </event>',
            '  </library>',
            '</fcpxml>'
        ])

        content = "\n".join(lines)
        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    @staticmethod
    def _build_title_element(
        offset_ms: int,
        dur_ms: int,
        text: str,
        font_name: str,
        font_size: int,
        color: str,
        stroke_attrs: str = "",
        bold: str = "0",
        lane: Optional[str] = None
    ) -> str:
        escaped_text = html.escape(text)
        lane_attr = f' lane="{lane}"' if lane else ''
        return f"""
              <title ref="r2" offset="{offset_ms}/1000s" duration="{dur_ms}/1000s" start="0s" role="Video"{lane_attr}>
                <text>
                  <text-style ref="ts_normal">{escaped_text}</text-style>
                </text>
                <text-style-def id="ts_normal">
                  <text-style font="{font_name}" fontSize="{font_size}" fontColor="{color}"{stroke_attrs} alignment="center" bold="{bold}"/>
                </text-style-def>
              </title>"""

    @staticmethod
    def get_clip_start_frame_and_tc(file_path: str, target_fps: int = 30) -> tuple:
        """
        Trích xuất timecode gốc từ file media và quy đổi thành frame count + NDF timecode string
        tương thích 100% với cơ chế import của DaVinci Resolve.
        """
        import subprocess, json, re
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream_tags=timecode:format_tags=timecode', '-of', 'json', file_path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            d = json.loads(res.stdout)
        except Exception:
            return 0, '00:00:00:00', 'NDF'

        tc_str = None
        for st in d.get('streams', []):
            tc = st.get('tags', {}).get('timecode')
            if tc:
                tc_str = tc
                break
        if not tc_str:
            tc_str = d.get('format', {}).get('tags', {}).get('timecode')

        if not tc_str:
            return 0, '00:00:00:00', 'NDF'

        is_df = ';' in tc_str or ',' in tc_str
        parts = [int(p) for p in re.split(r'[:;,]', tc_str) if p.isdigit()]
        if len(parts) < 4:
            return 0, '00:00:00:00', 'NDF'

        hh, mm, ss, ff = parts[0], parts[1], parts[2], parts[3]

        if is_df:
            total_minutes = hh * 60 + mm
            total_frames = (hh * 3600 + mm * 60 + ss) * target_fps + ff
            dropped_frames = 2 * (total_minutes - total_minutes // 10)
            real_frames = total_frames - dropped_frames
        else:
            real_frames = (hh * 3600 + mm * 60 + ss) * target_fps + ff

        ff_ndf = real_frames % target_fps
        total_s = real_frames // target_fps
        ss_ndf = total_s % 60
        total_m = total_s // 60
        mm_ndf = total_m % 60
        hh_ndf = total_m // 60
        ndf_str = f'{hh_ndf:02d}:{mm_ndf:02d}:{ss_ndf:02d}:{ff_ndf:02d}'

        return real_frames, ndf_str, 'NDF'

    @staticmethod
    def generate_timeline_fcpxml(
        events: List[Dict[str, Any]], 
        output_xml_path: str,
        timeline_name: str = "ResolveFlow Cut Timeline",
        fps: float = 30.0,
        aspect_ratio: str = "16:9",
        subtitles: Optional[List[Dict[str, Any]]] = None,
        font_name: str = "Arial",
        font_size: int = 48,
        standard_color: str = "1 1 1 1",
        highlight_color: str = "1 0.84 0 1",
        preset: Optional[Union[TextStylePreset, str]] = None,
        markers: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> str:
        """
        Sinh tệp FCPXML v1.9 tạo dựng Timeline hoàn chỉnh (Clips + Audio + Cuts)
        và chèn phụ đề Karaoke động (Text+) trên Track Video 2 theo preset đã chọn.
        """
        import pathlib
        from src.core.autocut import get_media_metadata

        format_name = "FFVideoFormat1080x1920p" if aspect_ratio == "9:16" else "FFVideoFormat1080p"
        frame_dur_str = FCPXMLGenerator._get_fcpxml_frame_duration(fps)

        style = FCPXMLGenerator._resolve_preset(
            preset=preset,
            font_name=font_name,
            font_size=font_size,
            standard_color=standard_color,
            highlight_color=highlight_color,
            aspect_ratio=aspect_ratio
        )
        f_name = style["font_name"]
        f_size = style["font_size"]
        hl_size = style["highlight_size"]
        std_col = style["standard_color"]
        hl_col = style["highlight_color"]
        stroke_attrs = style["stroke_attrs"]
        bold_val = style["bold"]

        # ---- Lưới khung hình: mọi mốc offset/start/duration là bội số nguyên của 1 khung hình ----
        # (làm tròn mili-giây từng clip gây khe hở/chồng lấn 1 khung và lệch hình-tiếng khi có hàng nghìn clip)
        fd_num, fd_den = [int(x) for x in frame_dur_str.rstrip("s").split("/")]
        real_fps = fd_den / fd_num

        def fr(n_frames: int) -> str:
            return "0s" if n_frames == 0 else f"{n_frames * fd_num}/{fd_den}s"

        def to_f(sec: float) -> int:
            return int(round(sec * real_fps))

        clip_metadata_db = kwargs.get("clip_metadata_db") or {}

        # Sử dụng dict.fromkeys để giữ nguyên vẹn thứ tự clip và loại bỏ trùng lặp (không làm đảo lộn thứ tự như set())
        unique_paths = list(dict.fromkeys([ev["video_path"] for ev in events]))
        asset_map = {}
        meta_map = {}
        asset_tc_map = {}
        
        resources_xml = [
            f'    <format id="r_fmt" name="{format_name}" frameDuration="{frame_dur_str}"/>'
        ]
        if subtitles:
            resources_xml.append(
                '    <effect id="r2" name="Text+" uid=".../Titles.localized/Bumper.localized/Text+.localized"/>'
            )

        for i, path in enumerate(unique_paths, 1):
            asset_id = f"r_asset_{i}"
            asset_map[path] = asset_id

            meta = {}
            if clip_metadata_db:
                if path in clip_metadata_db:
                    meta = clip_metadata_db[path]
                else:
                    base = os.path.basename(path)
                    for k, v in clip_metadata_db.items():
                        if os.path.basename(k) == base:
                            meta = v
                            break
            if not meta:
                meta = get_media_metadata(path)
            meta_map[path] = meta

            # Lấy timecode gốc của file để DaVinci Resolve link chính xác frame vật lý
            if "start_tc_frames" in meta or "tc_frames" in meta:
                start_tc_f = meta.get("start_tc_frames", meta.get("tc_frames", 0))
            else:
                start_tc_f, _, _ = FCPXMLGenerator.get_clip_start_frame_and_tc(path, int(round(real_fps)))
            asset_tc_map[path] = start_tc_f

            # Không dùng as_uri() vì nó mã hóa phần trăm (ví dụ: %20), làm Resolve trên Windows bị lỗi Media Offline
            abs_path = os.path.abspath(path).replace('\\', '/')
            if not abs_path.startswith('/'):
                file_url = f"file:///{abs_path}"
            else:
                file_url = f"file://{abs_path}"
            clip_name = html.escape(os.path.basename(path))
            
            total_dur_sec = meta.get("duration", 0.0)
            matching_evs = [ev.get("src_out", 0.0) for ev in events if ev.get("video_path") == path]
            if matching_evs:
                total_dur_sec = max(total_dur_sec, max(matching_evs) + 0.1)
            elif total_dur_sec <= 0.0:
                total_dur_sec = 3600.0
            dur_frames = to_f(total_dur_sec)
            has_audio_val = "1" if meta.get("has_audio", True) else "0"

            escaped_file_url = html.escape(file_url)
            start_tc_str_val = fr(start_tc_f)
            dur_tc_str_val = fr(dur_frames)
            resources_xml.append(
                f"""    <asset id="{asset_id}" name="{clip_name}" src="{escaped_file_url}" start="{start_tc_str_val}" duration="{dur_tc_str_val}" hasVideo="1" format="r_fmt" hasAudio="{has_audio_val}">
      <metadata>
        <md key="com.apple.proapps.spotlight.kMDItemContentType" value="public.movie"/>
      </metadata>
    </asset>"""
            )

        # Kế hoạch từng clip: vị trí trên timeline được CỘNG DỒN từ thời lượng ĐẦU RA (đã tính tốc độ),
        # nên các clip luôn nối liền nhau, không bao giờ chồng lấn, kể cả đoạn tua nhanh.
        plan: List[Optional[Dict[str, Any]]] = []
        cursor_f = 0
        for ev in events:
            max_file_dur = meta_map.get(ev["video_path"], {}).get("duration", 0.0)
            src_out_sec = ev["src_out"]
            if max_file_dur > 0:
                # Giới hạn src_out không vượt quá thời lượng vật lý của tệp video để chống Media Offline ở đuôi clip
                src_out_sec = min(src_out_sec, max_file_dur)
            src_in_f = to_f(max(0.0, ev["src_in"]))
            src_out_f = to_f(src_out_sec)
            src_dur_f = src_out_f - src_in_f
            if src_dur_f < 1:
                plan.append(None)
                continue
            speed = float(ev.get("speed", 1.0) or 1.0)
            retimed = abs(speed - 1.0) > 1e-3
            out_dur_f = max(1, int(round(src_dur_f / speed)))
            plan.append({"src_in_f": src_in_f, "src_out_f": src_out_f, "src_dur_f": src_dur_f,
                         "out_dur_f": out_dur_f, "offset_f": cursor_f, "retimed": retimed})
            cursor_f += out_dur_f

        # Nhóm các title phụ đề theo từng event clip trên timeline để neo (anchor) đúng chuẩn FCPXML v1.9.
        # Clip tua nhanh không gắn title (không có lời thoại đáng kể, và mốc neo trong clip retime dễ sai).
        def anchored_title(ev_idx: int, ev: Dict[str, Any], ov_start: float, ov_end: float) -> Optional[tuple]:
            pl = plan[ev_idx]
            if pl is None or pl["retimed"]:
                return None
            v_path = ev["video_path"]
            start_tc_f = asset_tc_map.get(v_path, 0)
            clip_start_f = start_tc_f + pl["src_in_f"]
            offset_f = clip_start_f + to_f(ov_start - ev["rec_in"])
            dur_f = max(1, to_f(ov_end - ov_start))
            dur_f = min(dur_f, (start_tc_f + pl["src_out_f"]) - offset_f)
            if dur_f < 1 or offset_f < clip_start_f:
                return None
            return fr(offset_f), fr(dur_f)

        clip_titles_map = {ev_idx: [] for ev_idx in range(len(events))}
        if subtitles and events:
            for idx, sub in enumerate(subtitles):
                words = sub.get("words", [])
                card_start = sub["start"]
                card_end = sub["end"]
                card_text = sub["text"]

                if not words or style["animation"] == "static":
                    if card_end <= card_start:
                        continue

                    for ev_idx, ev in enumerate(events):
                        ov_start = max(card_start, ev["rec_in"])
                        ov_end = min(card_end, ev["rec_out"])
                        if ov_end > ov_start:
                            at = anchored_title(ev_idx, ev, ov_start, ov_end)
                            if not at:
                                continue
                            title_xml = f"""
                <title ref="r2" offset="{at[0]}" duration="{at[1]}" start="0s" role="Video" lane="1">
                  <text>
                    <text-style ref="ts_normal">{html.escape(card_text)}</text-style>
                  </text>
                  <text-style-def id="ts_normal">
                    <text-style font="{f_name}" fontSize="{f_size}" fontColor="{std_col}"{stroke_attrs} alignment="center" bold="{bold_val}"/>
                  </text-style-def>
                </title>"""
                            clip_titles_map[ev_idx].append(title_xml)
                else:
                    for w_idx, w in enumerate(words):
                        w_start = w.get("start", card_start)
                        w_end = w.get("end", card_end)
                        if w_end <= w_start:
                            continue

                        for ev_idx, ev in enumerate(events):
                            ov_start = max(w_start, ev["rec_in"])
                            ov_end = min(w_end, ev["rec_out"])
                            if ov_end > ov_start:
                                at = anchored_title(ev_idx, ev, ov_start, ov_end)
                                if not at:
                                    continue

                                word_text_xml = []
                                for i_idx, iw in enumerate(words):
                                    is_active = (i_idx == w_idx)
                                    ts_ref = "ts_highlight" if is_active else "ts_normal"
                                    word_text_xml.append(
                                        f'<text-style ref="{ts_ref}">{html.escape(iw.get("word", ""))}</text-style>'
                                    )
                                full_word_text = " ".join(word_text_xml)

                                word_title_xml = f"""
                <title ref="r2" offset="{at[0]}" duration="{at[1]}" start="0s" role="Video" lane="1">
                  <text>{full_word_text}</text>
                  <text-style-def id="ts_highlight">
                    <text-style font="{f_name}" fontSize="{hl_size}" fontColor="{hl_col}"{stroke_attrs} alignment="center" bold="{bold_val}"/>
                  </text-style-def>
                  <text-style-def id="ts_normal">
                    <text-style font="{f_name}" fontSize="{f_size}" fontColor="{std_col}"{stroke_attrs} alignment="center" bold="{bold_val}"/>
                  </text-style-def>
                </title>"""
                                clip_titles_map[ev_idx].append(word_title_xml)

        spine_elements = []
        for ev_idx, ev in enumerate(events):
            pl = plan[ev_idx]
            if pl is None:
                continue
            v_path = ev["video_path"]
            asset_id = asset_map[v_path]
            clip_name = html.escape(os.path.basename(v_path))
            reel_name = html.escape(os.path.splitext(os.path.basename(v_path))[0])
            start_tc_f = asset_tc_map.get(v_path, 0)
            clip_start_f = start_tc_f + pl["src_in_f"]

            inner_titles = "".join(clip_titles_map.get(ev_idx, []))

            # Đoạn tua nhanh: timeMap ánh xạ thời gian ĐẦU RA (0 -> out_dur) sang thời gian NGUỒN (0 -> src_dur)
            time_map = ""
            if pl["retimed"]:
                time_map = (
                    f'\n              <timeMap>'
                    f'<timept time="0s" value="0s" interp="linear"/>'
                    f'<timept time="{fr(pl["out_dur_f"])}" value="{fr(pl["src_dur_f"])}" interp="linear"/>'
                    f'</timeMap>'
                )

            clip_xml = f"""
            <asset-clip name="{clip_name}" ref="{asset_id}" offset="{fr(pl["offset_f"])}" start="{fr(clip_start_f)}" duration="{fr(pl["out_dur_f"])}" format="r_fmt" audioRole="dialogue">{time_map}{inner_titles}
            </asset-clip>"""
            spine_elements.append(clip_xml)

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE fcpxml>',
            '<fcpxml version="1.9">',
            '  <resources>',
            "\n".join(resources_xml),
            '  </resources>',
            '  <library>',
            '    <event name="ResolveFlow Cuts">',
            f'      <project name="{timeline_name}">',
            f'        <sequence format="r_fmt" tcStart="0s">',
            '          <spine>',
            "".join(spine_elements),
            '          </spine>',
            '        </sequence>',
            '      </project>',
            '    </event>',
            '  </library>',
            '</fcpxml>'
        ]

        parent_dir = os.path.dirname(output_xml_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_xml_path, "w", encoding="utf-8") as f:
            f.write("\n".join(xml_lines))

        return output_xml_path

    @staticmethod
    def generate_fcp7_xml(
        events: list,
        output_xml_path: str,
        timeline_name: str = "ResolveFlow Cut Timeline",
        fps: float = 30.0,
        aspect_ratio: str = "16:9",
        broll_inserts: list = None,
        sfx_inserts: list = None,
        markers: list = None,
        **kwargs
    ) -> str:
        """
        Sinh tệp Final Cut Pro 7 XML (xmeml v5) chuyên nghiệp tương thích 100% với DaVinci Resolve Windows:
        - Track V1: Video A-Roll chính (cắt gọt chính xác).
        - Track V2: B-Roll / Meme minh họa (nếu có).
        - Track A1/A2: Stereo Audio thoại gốc.
        - Track A3: Âm thanh hiệu ứng SFX (Whoosh, Pop, Bell...).
        - Track A4/A5: Âm thanh kèm theo của video B-Roll (nếu có).
        """
        import re
        import xml.etree.ElementTree as ET
        import xml.dom.minidom as minidom
        from src.core.autocut import get_media_metadata
        from src.core.broll_sfx import GlobalAssetPool

        asset_pool = GlobalAssetPool.get_instance()

        real_fps = float(fps)
        timebase_int = int(round(real_fps))
        timebase_str = str(timebase_int)
        is_ntsc = abs(real_fps - 29.97) < 0.05 or abs(real_fps - 23.976) < 0.05 or abs(real_fps - 59.94) < 0.05
        ntsc_str = "TRUE" if is_ntsc else "FALSE"
        w_val = "1080" if aspect_ratio == "9:16" else "1920"
        h_val = "1920" if aspect_ratio == "9:16" else "1080"

        def sec_to_frame(sec: float) -> int:
            return int(round(sec * real_fps))

        def clean_path_url(path: str) -> str:
            p = os.path.abspath(path).replace('\\', '/')
            if not p.startswith('/'):
                return f"file:///{p}"
            return f"file://{p}"

        def clean_str(val: Any) -> str:
            if not val:
                return ""
            s = str(val)
            return re.sub(r'[\U00010000-\U0010ffff]', '', s).strip()

        def _get_val(obj, *keys, default=None):
            for k in keys:
                if isinstance(obj, dict) and k in obj:
                    return obj[k]
                elif hasattr(obj, k):
                    val = getattr(obj, k)
                    if val is not None:
                        return val
            return default

        clip_metadata_db = kwargs.get("clip_metadata_db") or {}

        # 1. Thu thập metadata của các clip nguồn
        clip_db = {}
        def get_or_create_file_meta(vpath: str) -> dict:
            data = None
            if clip_metadata_db:
                if vpath in clip_metadata_db:
                    data = dict(clip_metadata_db[vpath])
                else:
                    base = os.path.basename(vpath)
                    for k, v in clip_metadata_db.items():
                        if os.path.basename(k) == base:
                            data = dict(v)
                            break

            if data is None:
                base_upper = os.path.basename(vpath).upper()
                if base_upper in clip_db:
                    return clip_db[base_upper]
                if vpath in clip_db:
                    return clip_db[vpath]

                meta = get_media_metadata(vpath)
                dur_sec = meta.get("duration", 0.0)
                dur_frames = max(1, sec_to_frame(dur_sec))
                data = {
                    "name": os.path.basename(vpath),
                    "path": os.path.abspath(vpath),
                    "pathurl": clean_path_url(vpath),
                    "duration": dur_sec,
                    "dur_frames": dur_frames,
                    "has_audio": meta.get("has_audio", True),
                    "channels": 2
                }

            # Đảm bảo timecode được trích xuất chính xác từ file vật lý nếu chưa có
            if "start_tc_frames" not in data and "tc_frames" not in data:
                tc_frames, tc_str, tc_fmt = FCPXMLGenerator.get_clip_start_frame_and_tc(vpath, timebase_int)
                data["start_tc_frames"] = tc_frames
                data["start_tc_str"] = tc_str
                data["tc_format"] = tc_fmt
            else:
                data["start_tc_frames"] = data.get("start_tc_frames", data.get("tc_frames", 0))
                data["start_tc_str"] = data.get("start_tc_str", data.get("timecode", "00:00:00:00"))
                data["tc_format"] = data.get("tc_format", "NDF")

            base_upper = os.path.basename(vpath).upper()
            clip_db[base_upper] = data
            clip_db[vpath] = data
            return data

        file_elements_cache = set()
        def add_file_node(parent: ET.Element, file_meta: dict, file_id: str, is_audio_only: bool = False):
            if file_id in file_elements_cache:
                return ET.SubElement(parent, "file", id=file_id)

            file_elements_cache.add(file_id)
            file_el = ET.SubElement(parent, "file", id=file_id)
            ET.SubElement(file_el, "name").text = file_meta["name"]
            ET.SubElement(file_el, "pathurl").text = file_meta.get("pathurl", clean_path_url(file_meta.get("path", "")))
            f_rate = ET.SubElement(file_el, "rate")
            ET.SubElement(f_rate, "timebase").text = timebase_str
            ET.SubElement(f_rate, "ntsc").text = ntsc_str
            ET.SubElement(file_el, "duration").text = str(file_meta.get("dur_frames", max(1, sec_to_frame(file_meta.get("duration", 1.0)))))

            tc_node = ET.SubElement(file_el, "timecode")
            tc_r = ET.SubElement(tc_node, "rate")
            ET.SubElement(tc_r, "timebase").text = timebase_str
            ET.SubElement(tc_r, "ntsc").text = ntsc_str
            ET.SubElement(tc_node, "string").text = file_meta.get("start_tc_str", "00:00:00:00")
            ET.SubElement(tc_node, "frame").text = str(file_meta.get("start_tc_frames", 0))
            ET.SubElement(tc_node, "displayformat").text = file_meta.get("tc_format", "NDF")

            media_el = ET.SubElement(file_el, "media")
            if not is_audio_only:
                v_media = ET.SubElement(media_el, "video")
                sc_v = ET.SubElement(v_media, "samplecharacteristics")
                ET.SubElement(sc_v, "width").text = w_val
                ET.SubElement(sc_v, "height").text = h_val
            if file_meta.get("has_audio", True) or is_audio_only:
                a_media = ET.SubElement(media_el, "audio")
                sc_a = ET.SubElement(a_media, "samplecharacteristics")
                ET.SubElement(sc_a, "depth").text = "16"
                ET.SubElement(sc_a, "samplerate").text = "48000"
            return file_el

        # Tính tổng thời lượng trước để ghi duration đúng chuẩn FCP7 XML
        total_timeline_frames = 0
        for ev in events:
            s_in = max(0.0, float(ev.get("src_in", 0.0)))
            s_out = float(ev.get("src_out", 0.0))
            if s_out > s_in:
                total_timeline_frames += max(1, sec_to_frame(s_out - s_in))

        # 2. Xây dựng XMEML Root chuẩn Apple Final Cut Pro 7 / DaVinci Resolve
        xmeml = ET.Element("xmeml", version="5")
        sequence = ET.SubElement(xmeml, "sequence")
        ET.SubElement(sequence, "name").text = clean_str(timeline_name)
        seq_dur_node = ET.SubElement(sequence, "duration")
        seq_dur_node.text = str(total_timeline_frames)

        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = timebase_str
        ET.SubElement(rate, "ntsc").text = ntsc_str

        # Timecode chuẩn của Sequence
        tc_seq = ET.SubElement(sequence, "timecode")
        tc_seq_rate = ET.SubElement(tc_seq, "rate")
        ET.SubElement(tc_seq_rate, "timebase").text = timebase_str
        ET.SubElement(tc_seq_rate, "ntsc").text = ntsc_str
        ET.SubElement(tc_seq, "string").text = "00:00:00:00"
        ET.SubElement(tc_seq, "frame").text = "0"
        ET.SubElement(tc_seq, "displayformat").text = "DF" if ntsc_str == "TRUE" else "NDF"

        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")

        # Format Video Sequence (Bắt buộc cho DaVinci Resolve)
        v_format = ET.SubElement(video, "format")
        sc_v_seq = ET.SubElement(v_format, "samplecharacteristics")
        ET.SubElement(sc_v_seq, "width").text = w_val
        ET.SubElement(sc_v_seq, "height").text = h_val
        ET.SubElement(sc_v_seq, "pixelaspectratio").text = "square"
        rate_v_seq = ET.SubElement(sc_v_seq, "rate")
        ET.SubElement(rate_v_seq, "timebase").text = timebase_str
        ET.SubElement(rate_v_seq, "ntsc").text = ntsc_str

        v_track1 = ET.SubElement(video, "track") # Track V1 Main

        audio = ET.SubElement(media, "audio")
        # Format Audio Sequence (Bắt buộc cho DaVinci Resolve)
        a_format = ET.SubElement(audio, "format")
        sc_a_seq = ET.SubElement(a_format, "samplecharacteristics")
        ET.SubElement(sc_a_seq, "depth").text = "16"
        ET.SubElement(sc_a_seq, "samplerate").text = "48000"

        a_track1 = ET.SubElement(audio, "track") # A1 Main L
        a_track2 = ET.SubElement(audio, "track") # A2 Main R

        current_timeline_frame = 0

        # 3. Duyệt và xếp các đoạn A-Roll lên V1, A1, A2
        for idx, ev in enumerate(events, 1):
            vpath = ev.get("video_path")
            if not vpath or (not os.path.exists(vpath) and vpath not in clip_metadata_db):
                continue
            f_meta = get_or_create_file_meta(vpath)
            f_id = f"file-v1-{idx}"

            src_in_sec = max(0.0, float(ev.get("src_in", 0.0)))
            src_out_sec = float(ev.get("src_out", 0.0))
            max_dur = f_meta.get("duration", 0.0)
            if max_dur > 0:
                src_out_sec = min(src_out_sec, max_dur)
            dur_sec = max(0.0, src_out_sec - src_in_sec)
            if dur_sec <= 0.01:
                continue

            tc_base = f_meta.get("start_tc_frames", 0)
            in_f = tc_base + sec_to_frame(src_in_sec)
            dur_f = max(1, sec_to_frame(dur_sec))
            out_f = in_f + dur_f
            clip_dur_f = tc_base + f_meta.get("dur_frames", 1)

            # Video V1
            v_item = ET.SubElement(v_track1, "clipitem", id=f"clipitem-v1-{idx}")
            ET.SubElement(v_item, "name").text = f_meta["name"]
            ET.SubElement(v_item, "duration").text = str(clip_dur_f)
            v_rate = ET.SubElement(v_item, "rate")
            ET.SubElement(v_rate, "timebase").text = timebase_str
            ET.SubElement(v_rate, "ntsc").text = ntsc_str
            ET.SubElement(v_item, "start").text = str(current_timeline_frame)
            ET.SubElement(v_item, "end").text = str(current_timeline_frame + dur_f)
            ET.SubElement(v_item, "in").text = str(in_f)
            ET.SubElement(v_item, "out").text = str(out_f)
            add_file_node(v_item, f_meta, f_id, is_audio_only=False)

            st_v = ET.SubElement(v_item, "sourcetrack")
            ET.SubElement(st_v, "mediatype").text = "video"
            ET.SubElement(st_v, "trackindex").text = "1"

            # Audio A1 (Left)
            a_item1 = ET.SubElement(a_track1, "clipitem", id=f"clipitem-a1-{idx}")
            ET.SubElement(a_item1, "name").text = f_meta["name"]
            ET.SubElement(a_item1, "duration").text = str(clip_dur_f)
            a_rate1 = ET.SubElement(a_item1, "rate")
            ET.SubElement(a_rate1, "timebase").text = timebase_str
            ET.SubElement(a_rate1, "ntsc").text = ntsc_str
            ET.SubElement(a_item1, "start").text = str(current_timeline_frame)
            ET.SubElement(a_item1, "end").text = str(current_timeline_frame + dur_f)
            ET.SubElement(a_item1, "in").text = str(in_f)
            ET.SubElement(a_item1, "out").text = str(out_f)
            add_file_node(a_item1, f_meta, f_id, is_audio_only=False)

            st_a1 = ET.SubElement(a_item1, "sourcetrack")
            ET.SubElement(st_a1, "mediatype").text = "audio"
            ET.SubElement(st_a1, "trackindex").text = "1"

            # Audio A2 (Right)
            a_item2 = ET.SubElement(a_track2, "clipitem", id=f"clipitem-a2-{idx}")
            ET.SubElement(a_item2, "name").text = f_meta["name"]
            ET.SubElement(a_item2, "duration").text = str(clip_dur_f)
            a_rate2 = ET.SubElement(a_item2, "rate")
            ET.SubElement(a_rate2, "timebase").text = timebase_str
            ET.SubElement(a_rate2, "ntsc").text = ntsc_str
            ET.SubElement(a_item2, "start").text = str(current_timeline_frame)
            ET.SubElement(a_item2, "end").text = str(current_timeline_frame + dur_f)
            ET.SubElement(a_item2, "in").text = str(in_f)
            ET.SubElement(a_item2, "out").text = str(out_f)
            add_file_node(a_item2, f_meta, f_id, is_audio_only=False)

            st_a2 = ET.SubElement(a_item2, "sourcetrack")
            ET.SubElement(st_a2, "mediatype").text = "audio"
            ET.SubElement(st_a2, "trackindex").text = "2"

            # Liên kết Audio và Video (Link) để DaVinci khóa đồng bộ
            for itm in (v_item, a_item1, a_item2):
                lv = ET.SubElement(itm, "link")
                ET.SubElement(lv, "linkclipref").text = f"clipitem-v1-{idx}"
                ET.SubElement(lv, "mediatype").text = "video"
                ET.SubElement(lv, "trackindex").text = "1"
                ET.SubElement(lv, "clipindex").text = str(idx)

                la1 = ET.SubElement(itm, "link")
                ET.SubElement(la1, "linkclipref").text = f"clipitem-a1-{idx}"
                ET.SubElement(la1, "mediatype").text = "audio"
                ET.SubElement(la1, "trackindex").text = "1"
                ET.SubElement(la1, "clipindex").text = str(idx)
                ET.SubElement(la1, "groupindex").text = "1"

                la2 = ET.SubElement(itm, "link")
                ET.SubElement(la2, "linkclipref").text = f"clipitem-a2-{idx}"
                ET.SubElement(la2, "mediatype").text = "audio"
                ET.SubElement(la2, "trackindex").text = "2"
                ET.SubElement(la2, "clipindex").text = str(idx)
                ET.SubElement(la2, "groupindex").text = "1"

            current_timeline_frame += dur_f

        # 4. Duyệt và xếp B-Roll lên Video Track 2 (V2) & Audio B-Roll (A4/A5)
        if broll_inserts:
            broll_clips = []
            broll_a1_clips = []
            broll_a2_clips = []
            for b_idx, b_item in enumerate(broll_inserts, 1):
                raw_file = _get_val(b_item, "video_path", "asset_file", "clip_name", "query", "matched_path", default="")
                resolved_p = None
                if raw_file and (os.path.exists(str(raw_file)) or (clip_metadata_db and str(raw_file) in clip_metadata_db)):
                    resolved_p = str(raw_file)
                else:
                    resolver = getattr(asset_pool, "resolve_broll", getattr(asset_pool, "resolve_meme", None))
                    resolved_p = resolver(str(raw_file)) if resolver else None

                if not resolved_p or (not os.path.exists(resolved_p) and resolved_p not in clip_metadata_db):
                    continue

                b_meta = get_or_create_file_meta(resolved_p)
                tl_start_sec = float(_get_val(b_item, "timeline_sec", "time", "start_time", default=0.0))
                dur_sec = float(_get_val(b_item, "duration_sec", "duration", default=3.0))

                b_start_f = sec_to_frame(tl_start_sec)
                b_dur_f = max(1, sec_to_frame(dur_sec))
                b_tc_base = b_meta.get("start_tc_frames", 0)
                b_in_f = b_tc_base
                b_out_f = b_in_f + b_dur_f
                b_clip_dur_f = b_tc_base + b_meta.get("dur_frames", 1)
                f_broll_id = f"file-broll-{b_idx}"

                b_clip = ET.Element("clipitem", id=f"clipitem-broll-{b_idx}")
                ET.SubElement(b_clip, "name").text = b_meta["name"]
                ET.SubElement(b_clip, "duration").text = str(b_clip_dur_f)
                b_rate = ET.SubElement(b_clip, "rate")
                ET.SubElement(b_rate, "timebase").text = timebase_str
                ET.SubElement(b_rate, "ntsc").text = ntsc_str
                ET.SubElement(b_clip, "start").text = str(b_start_f)
                ET.SubElement(b_clip, "end").text = str(b_start_f + b_dur_f)
                ET.SubElement(b_clip, "in").text = str(b_in_f)
                ET.SubElement(b_clip, "out").text = str(b_out_f)
                add_file_node(b_clip, b_meta, f_broll_id, is_audio_only=False)

                st_b = ET.SubElement(b_clip, "sourcetrack")
                ET.SubElement(st_b, "mediatype").text = "video"
                ET.SubElement(st_b, "trackindex").text = "1"
                broll_clips.append(b_clip)

                # B-Roll Audio A4 / A5 nếu có
                if b_meta.get("has_audio", False):
                    b_a1 = ET.Element("clipitem", id=f"clipitem-broll-a1-{b_idx}")
                    ET.SubElement(b_a1, "name").text = b_meta["name"]
                    ET.SubElement(b_a1, "duration").text = str(b_clip_dur_f)
                    b_ar1 = ET.SubElement(b_a1, "rate")
                    ET.SubElement(b_ar1, "timebase").text = timebase_str
                    ET.SubElement(b_ar1, "ntsc").text = ntsc_str
                    ET.SubElement(b_a1, "start").text = str(b_start_f)
                    ET.SubElement(b_a1, "end").text = str(b_start_f + b_dur_f)
                    ET.SubElement(b_a1, "in").text = str(b_in_f)
                    ET.SubElement(b_a1, "out").text = str(b_out_f)
                    add_file_node(b_a1, b_meta, f_broll_id, is_audio_only=True)
                    st_b_a1 = ET.SubElement(b_a1, "sourcetrack")
                    ET.SubElement(st_b_a1, "mediatype").text = "audio"
                    ET.SubElement(st_b_a1, "trackindex").text = "1"
                    broll_a1_clips.append(b_a1)

                    b_a2 = ET.Element("clipitem", id=f"clipitem-broll-a2-{b_idx}")
                    ET.SubElement(b_a2, "name").text = b_meta["name"]
                    ET.SubElement(b_a2, "duration").text = str(b_clip_dur_f)
                    b_ar2 = ET.SubElement(b_a2, "rate")
                    ET.SubElement(b_ar2, "timebase").text = timebase_str
                    ET.SubElement(b_ar2, "ntsc").text = ntsc_str
                    ET.SubElement(b_a2, "start").text = str(b_start_f)
                    ET.SubElement(b_a2, "end").text = str(b_start_f + b_dur_f)
                    ET.SubElement(b_a2, "in").text = str(b_in_f)
                    ET.SubElement(b_a2, "out").text = str(b_out_f)
                    add_file_node(b_a2, b_meta, f_broll_id, is_audio_only=True)
                    st_b_a2 = ET.SubElement(b_a2, "sourcetrack")
                    ET.SubElement(st_b_a2, "mediatype").text = "audio"
                    ET.SubElement(st_b_a2, "trackindex").text = "2"
                    broll_a2_clips.append(b_a2)

            if broll_clips:
                v_track2 = ET.SubElement(video, "track")
                for c in broll_clips:
                    v_track2.append(c)

        # 5. Duyệt và xếp SFX lên Audio Track 3 (A3) - Chỉ tạo track khi có audio thật
        if sfx_inserts:
            sfx_clips = []
            for s_idx, s_item in enumerate(sfx_inserts, 1):
                raw_file = _get_val(s_item, "audio_path", "asset_file", "sfx_type", default="")
                resolved_p = None
                if raw_file and (os.path.exists(str(raw_file)) or (clip_metadata_db and str(raw_file) in clip_metadata_db)):
                    resolved_p = str(raw_file)
                else:
                    resolved_p = asset_pool.resolve_sfx(str(raw_file))

                if not resolved_p or (not os.path.exists(resolved_p) and resolved_p not in clip_metadata_db):
                    continue

                s_meta = get_or_create_file_meta(resolved_p)
                tl_start_sec = float(_get_val(s_item, "timeline_sec", "time", default=0.0))
                dur_sec = float(_get_val(s_item, "duration_sec", "duration", default=1.0))

                s_start_f = sec_to_frame(tl_start_sec)
                s_dur_f = max(1, sec_to_frame(dur_sec))
                s_tc_base = s_meta.get("start_tc_frames", 0)
                s_in_f = s_tc_base
                s_out_f = s_in_f + s_dur_f
                s_clip_dur_f = s_tc_base + s_meta.get("dur_frames", 1)

                s_clip = ET.Element("clipitem", id=f"clipitem-sfx-{s_idx}")
                ET.SubElement(s_clip, "name").text = s_meta["name"]
                ET.SubElement(s_clip, "duration").text = str(s_clip_dur_f)
                s_rate = ET.SubElement(s_clip, "rate")
                ET.SubElement(s_rate, "timebase").text = timebase_str
                ET.SubElement(s_rate, "ntsc").text = ntsc_str
                ET.SubElement(s_clip, "start").text = str(s_start_f)
                ET.SubElement(s_clip, "end").text = str(s_start_f + s_dur_f)
                ET.SubElement(s_clip, "in").text = str(s_in_f)
                ET.SubElement(s_clip, "out").text = str(s_out_f)
                add_file_node(s_clip, s_meta, f"file-sfx-{s_idx}", is_audio_only=True)

                st_sfx = ET.SubElement(s_clip, "sourcetrack")
                ET.SubElement(st_sfx, "mediatype").text = "audio"
                ET.SubElement(st_sfx, "trackindex").text = "1"
                sfx_clips.append(s_clip)

            if sfx_clips:
                a_track3 = ET.SubElement(audio, "track")
                for c in sfx_clips:
                    a_track3.append(c)

        # Gắn thêm track A4, A5 cho Audio B-Roll nếu có
        if broll_inserts and 'broll_a1_clips' in locals() and broll_a1_clips:
            a_track4 = ET.SubElement(audio, "track")
            for c in broll_a1_clips:
                a_track4.append(c)
            a_track5 = ET.SubElement(audio, "track")
            for c in broll_a2_clips:
                a_track5.append(c)

        # 6. Thêm Markers lên Sequence (đã làm sạch emoji)
        if markers:
            for m in markers:
                m_name = clean_str(m.get("name", "Marker"))
                m_note = clean_str(m.get("note", ""))
                m_el = ET.SubElement(sequence, "marker")
                ET.SubElement(m_el, "name").text = m_name
                ET.SubElement(m_el, "comment").text = m_note
                m_in = sec_to_frame(float(m.get("time", 0.0)))
                m_dur = sec_to_frame(float(m.get("duration", 1.0)))
                ET.SubElement(m_el, "in").text = str(m_in)
                ET.SubElement(m_el, "out").text = str(m_in + max(1, m_dur))

        # 7. Cập nhật lại tổng thời lượng chính xác ở đầu Sequence
        seq_dur_node.text = str(current_timeline_frame)

        parent_dir = os.path.dirname(output_xml_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        rough_string = ET.tostring(xmeml, encoding="utf-8")
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8")

        with open(output_xml_path, "wb") as f:
            f.write(pretty_xml)

        return output_xml_path
