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
        preset: Optional[Union[TextStylePreset, str]] = None
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
        preset: Optional[Union[TextStylePreset, str]] = None
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

        # Sử dụng dict.fromkeys để giữ nguyên vẹn thứ tự clip và loại bỏ trùng lặp (không làm đảo lộn thứ tự như set())
        unique_paths = list(dict.fromkeys([ev["video_path"] for ev in events]))
        asset_map = {}
        meta_map = {}
        
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
            meta = get_media_metadata(path)
            meta_map[path] = meta

            # Không dùng as_uri() vì nó mã hóa phần trăm (ví dụ: %20), làm Resolve trên Windows bị lỗi Media Offline
            abs_path = os.path.abspath(path).replace('\\', '/')
            if not abs_path.startswith('/'):
                file_url = f"file://localhost/{abs_path}"
            else:
                file_url = f"file://localhost{abs_path}"
            clip_name = html.escape(os.path.basename(path))
            reel_name = html.escape(os.path.splitext(os.path.basename(path))[0])
            
            total_dur_sec = meta.get("duration", 0.0)
            matching_evs = [ev.get("src_out", 0.0) for ev in events if ev.get("video_path") == path]
            if matching_evs:
                total_dur_sec = max(total_dur_sec, max(matching_evs) + 0.1)
            elif total_dur_sec <= 0.0:
                total_dur_sec = 3600.0
            dur_ms = int(total_dur_sec * 1000)
            has_audio_val = "1" if meta.get("has_audio", True) else "0"

            resources_xml.append(
                f"""    <asset id="{asset_id}" name="{clip_name}" src="{file_url}" start="0s" duration="{dur_ms}/1000s" hasVideo="1" format="r_fmt" hasAudio="{has_audio_val}">
      <metadata>
        <md key="com.apple.proapps.studio.reel" value="{reel_name}"/>
        <md key="com.apple.proapps.spotlight.kMDItemContentType" value="public.movie"/>
      </metadata>
    </asset>"""
            )

        # Nhóm các title phụ đề theo từng event clip trên timeline để neo (anchor) đúng chuẩn FCPXML v1.9
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
                    
                    # Neo title vào các clip tương ứng trên timeline
                    for ev_idx, ev in enumerate(events):
                        ov_start = max(card_start, ev["rec_in"])
                        ov_end = min(card_end, ev["rec_out"])
                        if ov_end > ov_start:
                            src_offset = ev["src_in"] + (ov_start - ev["rec_in"])
                            dur = ov_end - ov_start
                            offset_ms = int(round(src_offset * 1000))
                            dur_ms = int(round(dur * 1000))
                            if dur_ms <= 0:
                                continue
                            
                            title_xml = f"""
                <title ref="r2" offset="{offset_ms}/1000s" duration="{dur_ms}/1000s" start="0s" role="Video" lane="1">
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

                        # Neo word vào các clip tương ứng trên timeline
                        for ev_idx, ev in enumerate(events):
                            ov_start = max(w_start, ev["rec_in"])
                            ov_end = min(w_end, ev["rec_out"])
                            if ov_end > ov_start:
                                src_offset = ev["src_in"] + (ov_start - ev["rec_in"])
                                dur = ov_end - ov_start
                                offset_ms = int(round(src_offset * 1000))
                                dur_ms = int(round(dur * 1000))
                                if dur_ms <= 0:
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
                <title ref="r2" offset="{offset_ms}/1000s" duration="{dur_ms}/1000s" start="0s" role="Video" lane="1">
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
            v_path = ev["video_path"]
            asset_id = asset_map[v_path]
            clip_name = html.escape(os.path.basename(v_path))
            reel_name = html.escape(os.path.splitext(os.path.basename(v_path))[0])
            
            meta_file = meta_map.get(v_path, {})
            max_file_dur = meta_file.get("duration", 0.0)
            
            src_in_sec = max(0.0, ev["src_in"])
            src_out_sec = ev["src_out"]
            if max_file_dur > 0:
                # Giới hạn src_out không vượt quá thời lượng vật lý của tệp video để chống Media Offline ở đuôi clip
                src_out_sec = min(src_out_sec, max_file_dur)
            duration_sec = max(0.0, src_out_sec - src_in_sec)
            
            if duration_sec <= 0.03:
                continue

            offset_ms = int(ev["rec_in"] * 1000)
            src_start_ms = int(src_in_sec * 1000)
            dur_ms = int(duration_sec * 1000)

            inner_titles = "".join(clip_titles_map.get(ev_idx, []))

            clip_xml = f"""
            <asset-clip name="{clip_name}" ref="{asset_id}" offset="{offset_ms}/1000s" start="{src_start_ms}/1000s" duration="{dur_ms}/1000s" format="r_fmt" audioRole="dialogue">
              <metadata>
                <md key="com.apple.proapps.studio.reel" value="{reel_name}"/>
              </metadata>{inner_titles}
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
