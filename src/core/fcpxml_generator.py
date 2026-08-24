import os
import html
from typing import List, Dict, Any

class FCPXMLGenerator:
    """
    Tạo tệp FCPXML (Final Cut Pro XML) chuyên nghiệp tích hợp hiệu ứng phụ đề động Karaoke
    (đổi màu chữ và phóng to từ đang nói) tương thích hoàn hảo với DaVinci Resolve Free và Studio.
    """
    @staticmethod
    def generate_karaoke_fcpxml(
        subtitles: List[Dict[str, Any]], 
        output_path: str, 
        fps: float = 30.0,
        font_name: str = "Arial",
        font_size: int = 48,
        standard_color: str = "1 1 1 1",      # RGBA Trắng
        highlight_color: str = "1 0.84 0 1"   # RGBA Vàng (#FFD700)
    ) -> str:
        # Định nghĩa các tài nguyên
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE fcpxml>',
            '<fcpxml version="1.9">',
            '  <resources>',
            f'    <format id="r1" name="FFVideoFormat1080p" frameDuration="1/{int(fps)}s"/>',
            '    <effect id="r2" name="Text+" uid=".../Titles.localized/Bumper.localized/Text+.localized"/>',
            '  </resources>',
            '  <library>',
            '    <event name="ResolveFlow Project">',
            '      <project name="ResolveFlow Karaoke Timeline">',
            f'        <sequence duration="3600s" format="r1" tcStart="0s">',
            '          <spine>',
            '            <gap name="Gap" offset="0s" duration="3600s">'
        ]

        highlight_size = int(font_size * 1.2)  # Phóng to từ đang đọc 20%

        # Tích lũy các phần tử sub
        for idx, sub in enumerate(subtitles):
            words = sub.get("words", [])
            card_start = sub["start"]
            card_end = sub["end"]
            card_text = sub["text"]

            if not words:
                # Nếu không có từ đơn (dự phòng), xuất chữ thường không highlight
                start_ms = int(card_start * 1000)
                dur_ms = int((card_end - card_start) * 1000)
                
                title_xml = FCPXMLGenerator._build_title_element(
                    offset_ms=start_ms,
                    dur_ms=dur_ms,
                    text=html.escape(card_text),
                    font_name=font_name,
                    font_size=font_size,
                    color=standard_color
                )
                lines.append(title_xml)
                continue

            # Xuất từng phân đoạn nhỏ tương ứng với thời lượng của mỗi từ
            for w_idx, active_word in enumerate(words):
                w_start = active_word["start"]
                w_end = active_word["end"]
                
                # Cắt các mốc thời gian thừa nằm ngoài biên của sub card
                w_start = max(card_start, min(card_end, w_start))
                w_end = max(card_start, min(card_end, w_end))
                
                if w_end <= w_start:
                    continue

                w_start_ms = int(w_start * 1000)
                w_dur_ms = int((w_end - w_start) * 1000)

                # Dựng chuỗi văn bản XML chứa styling highlight từ active_word
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
                  <text-style font="{font_name}" fontSize="{font_size}" fontColor="{standard_color}" alignment="center"/>
                </text-style-def>
                <text-style-def id="ts_highlight">
                  <text-style font="{font_name}" fontSize="{highlight_size}" fontColor="{highlight_color}" alignment="center" bold="1"/>
                </text-style-def>
              </title>"""
                lines.append(title_xml)

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
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    @staticmethod
    def _build_title_element(offset_ms: int, dur_ms: int, text: str, font_name: str, font_size: int, color: str) -> str:
        escaped_text = html.escape(text)
        return f"""
              <title ref="r2" offset="{offset_ms}/1000s" duration="{dur_ms}/1000s" start="0s" role="Video">
                <text>
                  <text-style ref="ts_normal">{escaped_text}</text-style>
                </text>
                <text-style-def id="ts_normal">
                  <text-style font="{font_name}" fontSize="{font_size}" fontColor="{color}" alignment="center"/>
                </text-style-def>
              </title>"""
