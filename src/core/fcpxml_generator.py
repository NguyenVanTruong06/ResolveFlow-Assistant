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
            f'    <format id="r1" name="{format_name}" frameDuration="1/{int(fps)}s"/>',
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
        frame_dur_str = f"1001/{int(fps*1001)}s" if fps in [23.976, 29.97, 59.94] else f"1/{int(fps)}s"

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

        unique_paths = list(set([ev["video_path"] for ev in events]))
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

            file_url = pathlib.Path(os.path.abspath(path)).as_uri()
            clip_name = html.escape(os.path.basename(path))
            
            total_dur_sec = meta.get("duration", 0.0)
            dur_ms = int(total_dur_sec * 1000) if total_dur_sec > 0 else 3600000
            has_audio_val = "1" if meta.get("has_audio", True) else "0"

            resources_xml.append(
                f'    <asset id="{asset_id}" name="{clip_name}" src="{file_url}" start="0s" duration="{dur_ms}/1000s" hasVideo="1" hasAudio="{has_audio_val}"/>'
            )

        spine_elements = []
        for ev in events:
            v_path = ev["video_path"]
            asset_id = asset_map[v_path]
            clip_name = html.escape(os.path.basename(v_path))
            
            src_in_sec = ev["src_in"]
            duration_sec = ev["src_out"] - ev["src_in"]
            
            if duration_sec <= 0.03:
                continue

            offset_ms = int(ev["rec_in"] * 1000)
            src_start_ms = int(src_in_sec * 1000)
            dur_ms = int(duration_sec * 1000)

            clip_xml = f"""
            <asset-clip name="{clip_name}" ref="{asset_id}" offset="{offset_ms}/1000s" start="{src_start_ms}/1000s" duration="{dur_ms}/1000s">
            </asset-clip>"""
            spine_elements.append(clip_xml)

        # Chèn thêm phụ đề Karaoke Text+ trên lane="1" (Track Video 2)
        if subtitles:
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
                    
                    title_xml = f"""
              <title ref="r2" offset="{start_ms}/1000s" duration="{dur_ms}/1000s" start="0s" role="Video" lane="1">
                <text>
                  <text-style ref="ts_normal">{html.escape(card_text)}</text-style>
                </text>
                <text-style-def id="ts_normal">
                  <text-style font="{f_name}" fontSize="{f_size}" fontColor="{std_col}"{stroke_attrs} alignment="center" bold="{bold_val}"/>
                </text-style-def>
              </title>"""
                    spine_elements.append(title_xml)
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
              <title ref="r2" offset="{w_start_ms}/1000s" duration="{w_dur_ms}/1000s" start="0s" role="Video" lane="1">
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
                    spine_elements.append(title_xml)

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
