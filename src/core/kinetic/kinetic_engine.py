#!/usr/bin/env python3
"""
ResolveFlow Assistant - Kinetic Subtitle PoC
============================================

Whisper word timestamps + Director Text Plan (JSON)  ->  1 Fusion comp / page
-> đặt lên V2 của timeline Resolve đang mở.

Kiến trúc "carrier clip":
  * API Insert*IntoTimeline (titles/generators/Fusion comps) KHÔNG nhận trackIndex
    và không đặt được thời gian chính xác -> không dùng.
  * Thay vào đó: một clip "carrier" trong suốt (ProRes 4444, ngắn) được
    MediaPool.AppendToTimeline({trackIndex, recordFrame, startFrame, endFrame})
    -> chính xác track + frame. Sau đó TimelineItem.ImportFusionComp(<file.comp>)
    gắn comp chứa Text+ / Transform / keyframe đã "nướng" sẵn từ Whisper.
  * Comp không dùng MediaIn, nên nội dung carrier không quan trọng; nó chỉ cho
    thời lượng + vị trí.

Chạy thử không cần Resolve (chỉ sinh .comp):
    python resolveflow_kinetic.py --demo --out build --dry-run
Chạy thật (Resolve Studio đang mở, External scripting = Local):
    python resolveflow_kinetic.py --words words.json --plan plan.json --out build

Các chỗ ghi "CALIBRATE"/"VERIFY" là giả định cần kiểm tra một lần trên
phiên bản Resolve của bạn (xem phần giới hạn trong câu trả lời).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 0. Cấu hình canvas
# --------------------------------------------------------------------------
CANVAS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
# Fusion: (0,0) = góc dưới-trái, (1,1) = góc trên-phải.
POS_Y = {
    "9:16": {"top": 0.74, "center": 0.50, "bottom": 0.30},   # tránh UI TikTok/Reels
    "16:9": {"top": 0.85, "center": 0.50, "bottom": 0.14},
    "1:1": {"top": 0.82, "center": 0.50, "bottom": 0.18},
    "4:5": {"top": 0.80, "center": 0.50, "bottom": 0.22},
}
MAX_LINE_WIDTH = 0.86          # tỉ lệ chiều rộng khung tối đa của 1 dòng
WORD_GAP_EM = 0.30             # khoảng cách giữa các từ (đơn vị em)
# CALIBRATE: 1 đơn vị Text+ "Size" tương ứng bao nhiêu chiều rộng khung cho 1 em.
# Cách đo: render Text+ "MMMMMMMMMM" Size=0.1, đo độ rộng (px)/10/0.1/width.
SIZE_TO_EM = 1.0
LEAD_IN_FRAMES = 2             # page hiện sớm hơn từ đầu tiên 2 frame
TAIL_FRAMES = 6                # giữ page sau từ cuối nếu còn chỗ


# --------------------------------------------------------------------------
# 1. Model dữ liệu
# --------------------------------------------------------------------------
@dataclass
class WordFx:
    text: str
    start_f: int                 # frame tương đối trong page
    end_f: int
    emphasis: str = "none"
    animation: str = "scale_pop"
    highlight: tuple = (1, 0.85, 0)
    hold_highlight: bool = False
    cx: float = 0.5              # tâm từ (layout)
    width: float = 0.1


@dataclass
class Page:
    idx: int
    start_f: int                 # frame tuyệt đối (tính từ 0 của timeline)
    end_f: int
    position: str
    intro: str
    preset: dict
    words: list = field(default_factory=list)

    @property
    def duration(self) -> int:
        return self.end_f - self.start_f


def hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# --------------------------------------------------------------------------
# 2. Plan: validate + auto-plan dự phòng khi chưa gọi AI
# --------------------------------------------------------------------------
def auto_plan(words: list, aspect="9:16", max_words=3, max_chars=16, gap_s=0.30) -> dict:
    """Plan tối thiểu không cần AI: tách page theo số từ/ký tự/khoảng nghỉ/dấu câu."""
    pages, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            chars = sum(len(words[j]["word"]) for j in cur) + len(w["word"])
            if (len(cur) >= max_words or chars > max_chars
                    or w["start"] - prev["end"] > gap_s
                    or prev["word"].rstrip()[-1:] in ".,!?;:"):
                pages.append(cur)
                cur = []
        cur.append(i)
    if cur:
        pages.append(cur)
    return {
        "schema_version": "1.0",
        "canvas": {"aspect": aspect},
        "presets": {"hormozi": {
            "font": "Montserrat", "style": "Black", "size": 0.085, "uppercase": True,
            "text_color": "#FFFFFF", "highlight_color": "#FFD400",
            "outline_color": "#000000", "outline_width": 0.08,
            "default_animation": "scale_pop"}},
        "pages": [{"word_range": [p[0], p[-1]], "preset": "hormozi"} for p in pages],
    }


def generate_kinetic_prompt(words: list, aspect: str = "9:16", font: str = "Montserrat") -> str:
    """Tạo Prompt chuẩn Kinetic Typography kèm danh sách từ ngữ [i] để người dùng gửi cho Claude/ChatGPT."""
    indexed_list = [f"[{i}] {w['word']} ({w['start']:.2f}s - {w['end']:.2f}s)" for i, w in enumerate(words)]
    indexed_text = " ".join(indexed_list)

    return f"""# 🎯 BẠN LÀ CHUYÊN GIA KINETIC TYPOGRAPHY & PHỤ ĐỀ HOẠT ẢNH CHO DAVINCI RESOLVE
## 🧠 NHIỆM VỤ:
Phân tích danh sách từ ngữ bên dưới và lập bản thiết kế phụ đề động (Director Text Plan) cho video định dạng {aspect}.

## 🎬 DANH SÁCH TỪ NGỮ CỦA VIDEO KÈM CHỈ SỐ [i]:
{indexed_text}

## ⚡ NGUYÊN TẮC THIẾT KẾ PHỤ ĐỀ HOẠT ẢNH (KINETIC / HORMOZI):
1. **Chia Page hợp lý:** Mỗi cụm từ (page) chỉ gồm 1 đến 3 từ (tối đa 4 từ). Ngắt theo nhịp thở tự nhiên, dấu câu và trọng tâm ý nghĩa.
2. **Các Page phải liên tục & phủ hết mọi từ:** `word_range` bắt đầu từ [0, b], sau đó [b+1, c]... đến hết từ cuối cùng ({len(words)-1}). Không được nhảy cóc hay bỏ sót từ.
3. **Chọn từ khóa đinh để nhấn mạnh (10% - 15% tổng số từ):**
   - Chỉ gán `emphasis: "high"` hoặc `"medium"` cho các con số, số tiền, động từ hành động mạnh, kết quả, hoặc từ biểu lộ cảm xúc cực độ.
   - Các từ còn lại KHÔNG cần liệt kê vào mảng `words` (sẽ tự động dùng mặc định).
4. **Hiệu ứng có sẵn (animation):**
   - `kinetic_bounce`: Nảy phóng to lò xo kết hợp tâng nhẹ trục Y (phù hợp từ khóa cao trào).
   - `scale_pop`: Phóng to nảy lò xo tại chỗ (mặc định cho câu).
   - `neon_glow`: Hiệu ứng đèn neon phát sáng viền.
   - `box_swipe`: Dải màu quét nền dạ quang từ trái sang phải phía sau chữ.
   - `glitch`: Giật rung màu điện ảnh (dùng tiết chế cho từ mang tính sốc/bất ngờ).
5. **Vị trí (position):** Mặc định `"bottom"` (đã né vùng an toàn giao diện TikTok/Reels/Shorts). Dùng `"center"` cho câu hook mở đầu nếu cần.
6. **TUYỆT ĐỐI KHÔNG GÕ LẠI TRANSCRIPT & KHÔNG TỰ BỊA TIMESTAMP:** Mọi từ đều tham chiếu qua chỉ số `i` và `word_range`. `text_override` chỉ dùng khi muốn sửa lỗi chính tả Whisper hoặc viết số (ví dụ: "1000" thay vì "một nghìn").

## 📤 YÊU CẦU ĐẦU RA (OUTPUT FORMAT):
Hãy trả về ĐÚNG MỘT KHỐI JSON DUY NHẤT (trong cặp dấu ```json ... ```) theo cấu trúc chuẩn sau:

```json
{{
  "schema_version": "1.0",
  "canvas": {{ "aspect": "{aspect}" }},
  "presets": {{
    "hormozi": {{
      "font": "{font}",
      "style": "Black",
      "size": 0.085,
      "uppercase": true,
      "text_color": "#FFFFFF",
      "highlight_color": "#FFD400",
      "outline_color": "#000000",
      "outline_width": 0.08,
      "default_animation": "scale_pop"
    }}
  }},
  "pages": [
    {{
      "word_range": [0, 1],
      "preset": "hormozi",
      "position": "center",
      "words": [
        {{ "i": 0, "emphasis": "high", "animation": "kinetic_bounce" }},
        {{ "i": 1, "emphasis": "high", "animation": "kinetic_bounce", "hold_highlight": true }}
      ]
    }},
    {{ "word_range": [2, 4], "preset": "hormozi" }}
  ]
}}
```
👉 Hãy xuất ra bản JSON hoàn chỉnh phủ hết {len(words)} từ trên!"""


def validate_plan(plan: dict, n_words: int, schema_path: str | None = None) -> None:
    """Schema (nếu có jsonschema) + các ràng buộc ngữ nghĩa JSON Schema không diễn đạt được."""
    if schema_path and os.path.exists(schema_path):
        try:
            import jsonschema
            with open(schema_path, encoding="utf-8") as f:
                jsonschema.validate(plan, json.load(f))
        except ImportError:
            print("[warn] chưa cài jsonschema - bỏ qua kiểm tra schema", file=sys.stderr)
    expected = 0
    for k, p in enumerate(plan["pages"]):
        a, b = p["word_range"]
        if a != expected or b < a or b - a > 3:
            raise ValueError(f"page {k}: word_range {p['word_range']} không liên tục / quá 4 từ (mong đợi bắt đầu {expected})")
        if p["preset"] not in plan["presets"]:
            raise ValueError(f"page {k}: preset '{p['preset']}' không tồn tại")
        for fx in p.get("words", []):
            if not a <= fx["i"] <= b:
                raise ValueError(f"page {k}: word i={fx['i']} nằm ngoài word_range")
        expected = b + 1
    if expected != n_words:
        raise ValueError(f"plan chỉ phủ {expected}/{n_words} từ")


# --------------------------------------------------------------------------
# 3. Layout + timing
# --------------------------------------------------------------------------
def measure_em(text: str, preset: dict) -> float:
    """Độ rộng chữ theo đơn vị em. Dùng Pillow + font thật nếu có, không thì ước lượng."""
    ff = preset.get("font_file")
    if ff and os.path.exists(ff):
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(ff, 200)
            return font.getlength(text) / 200.0
        except Exception:
            pass
    return 0.64 * len(text)  # chữ hoa đậm ~0.64 em/ký tự


def build_pages(words: list, plan: dict, fps: float) -> list:
    aspect = plan["canvas"]["aspect"]
    sec2f = lambda s: int(round(s * fps))
    raw = plan["pages"]
    pages = []
    for k, p in enumerate(raw):
        a, b = p["word_range"]
        preset = plan["presets"][p["preset"]]
        fx_map = {fx["i"]: fx for fx in p.get("words", [])}
        start = max(0, sec2f(words[a]["start"]) - LEAD_IN_FRAMES)
        if pages:
            start = max(start, pages[-1].end_f)          # không chồng lấn trên cùng track
        end = sec2f(words[b]["end"]) + TAIL_FRAMES
        if k + 1 < len(raw):
            nxt = max(0, sec2f(words[raw[k + 1]["word_range"][0]]["start"]) - LEAD_IN_FRAMES)
            end = min(end, nxt)
        end = max(end, start + 3)
        page = Page(k, start, end, p.get("position", "bottom"), p.get("intro", "pop"), preset)
        for i in range(a, b + 1):
            fx = fx_map.get(i, {})
            text = fx.get("text_override", words[i]["word"].strip())
            if preset.get("uppercase", True):
                text = text.upper()
            page.words.append(WordFx(
                text=text,
                start_f=max(0, sec2f(words[i]["start"]) - start),
                end_f=max(1, sec2f(words[i]["end"]) - start),
                emphasis=fx.get("emphasis", "none"),
                animation=fx.get("animation", preset["default_animation"]),
                highlight=hex_rgb(fx.get("highlight_color", preset["highlight_color"])),
                hold_highlight=fx.get("hold_highlight", False),
            ))
        # ---- layout 1 dòng, tự co size nếu tràn ----
        size = preset["size"]
        widths = [measure_em(w.text, preset) for w in page.words]
        total_em = sum(widths) + WORD_GAP_EM * (len(widths) - 1)
        em = size * SIZE_TO_EM
        if total_em * em > MAX_LINE_WIDTH:
            em = MAX_LINE_WIDTH / total_em
        page.size = em / SIZE_TO_EM
        page.y = POS_Y[aspect][page.position]
        x = 0.5 - total_em * em / 2
        for w, wem in zip(page.words, widths):
            w.width = wem * em
            w.cx = x + w.width / 2
            x += w.width + WORD_GAP_EM * em
        pages.append(page)
    return pages


# --------------------------------------------------------------------------
# 4. Curves (spring/elastic được "nướng" thành keyframe -> editor thấy trong Spline editor)
# --------------------------------------------------------------------------
AMP = {"none": 0.12, "medium": 0.22, "high": 0.38}


def spring(t0: int, fps: float, amp: float, damp=7.0, freq=3.0, dur=0.55, base=1.0) -> dict:
    """base + amp * e^(-damp t) * sin(2π f t): nảy vượt rồi dao động tắt dần."""
    n = int(dur * fps)
    keys = {t0 + k: base + amp * math.exp(-damp * k / fps) * math.sin(2 * math.pi * freq * k / fps)
            for k in range(n)}
    keys[t0 + n] = base
    return keys


def intro_spring(fps: float, from_scale=0.72, damp=8.0, freq=2.6, dur=0.4) -> dict:
    n = int(dur * fps)
    keys = {k: 1 - (1 - from_scale) * math.exp(-damp * k / fps) * math.cos(2 * math.pi * freq * k / fps)
            for k in range(n)}
    keys[n] = 1.0
    return keys


def step_keys(segments: list) -> dict:
    """segments: [(frame, value)] -> giá trị nhảy bậc (giữ 2 key liền nhau)."""
    keys = {}
    prev = None
    for f, v in sorted(segments):
        if prev is not None and f - 1 > max(keys):
            keys[f - 1] = prev
        keys[f] = v
        prev = v
    return keys


# --------------------------------------------------------------------------
# 5. Sinh file Fusion .comp (định dạng Lua table, giống copy/paste node trong Fusion)
# --------------------------------------------------------------------------
def lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


class CompWriter:
    def __init__(self, width: int, height: int, last_frame: int):
        self.W, self.H, self.last = width, height, last_frame
        self.blocks: list = []
        self.ypos = 0

    def _info(self, x: int) -> str:
        return f"ViewInfo = OperatorInfo {{ Pos = {{ {x * 110}, {self.ypos * 66} }} }},"

    def spline(self, name: str, keys: dict, linear=True) -> str:
        flags = ", Flags = { Linear = true }" if linear else ""
        body = ",\n\t\t\t".join(f"[{f}] = {{ {v:.5f}{flags} }}" for f, v in sorted(keys.items()))
        self.blocks.append(f"\t\t{name} = BezierSpline {{\n\t\t\tSplineColor = {{ Red = 240, Green = 120, Blue = 30 }},\n"
                           f"\t\t\tNameSet = true,\n\t\t\tKeyFrames = {{\n\t\t\t{body}\n\t\t\t}}\n\t\t}},")
        return f'Input {{ SourceOp = "{name}", Source = "Value", }}'

    def xypath(self, name: str, xkeys: dict, ykeys: dict) -> str:
        xs = self.spline(name + "X", xkeys)
        ys = self.spline(name + "Y", ykeys)
        self.blocks.append(f"\t\t{name} = XYPath {{\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n"
                           f"\t\t\tInputs = {{\n\t\t\t\tX = {xs},\n\t\t\t\tY = {ys},\n\t\t\t}},\n\t\t}},")
        return f'Input {{ SourceOp = "{name}", Source = "Value", }}'

    def tool(self, name: str, tool_id: str, inputs: dict, x: int = 0) -> str:
        lines = []
        for k, v in inputs.items():
            if isinstance(v, str) and v.startswith("Input {"):
                lines.append(f"\t\t\t\t{k} = {v},")
            elif isinstance(v, tuple):
                lines.append(f"\t\t\t\t{k} = Input {{ Value = {{ {v[0]:.5f}, {v[1]:.5f} }}, }},")
            elif isinstance(v, str):
                lines.append(f"\t\t\t\t{k} = Input {{ Value = {lua_str(v)}, }},")
            else:
                lines.append(f"\t\t\t\t{k} = Input {{ Value = {v}, }},")
        self.blocks.append(f"\t\t{name} = {tool_id} {{\n\t\t\tInputs = {{\n" + "\n".join(lines) +
                           f"\n\t\t\t}},\n\t\t\t{self._info(x)}\n\t\t}},")
        return f'Input {{ SourceOp = "{name}", Source = "Output", }}'

    def frame_inputs(self) -> dict:
        return {"GlobalOut": self.last, "Width": self.W, "Height": self.H, "UseFrameFormatSettings": 0}

    def render(self) -> str:
        return ("Composition {\n\tCurrentTime = 0,\n"
                f"\tRenderRange = {{ 0, {self.last} }},\n\tGlobalRange = {{ 0, {self.last} }},\n"
                "\tHiQ = true,\n\tPlaybackUpdateMode = 0,\n\tVersion = \"DaVinci Resolve Fusion\",\n"
                "\tSavedOutputs = 0,\n\tHeldTools = 0,\n\tDisabledTools = 0,\n\tLockedTools = 0,\n"
                "\tAudioOffset = 0,\n\tAutoRenderRange = true,\n\tResumable = true,\n\tOutputClips = { },\n"
                "\tTools = ordered() {\n" + "\n".join(self.blocks) + "\n\t},\n}\n")


def page_to_comp(page: Page, fps: float, W: int, H: int, frame_offset: int = 0) -> tuple:
    """Trả về (comp_text, {tool_name: text}) - map tên tool để set lại text qua API sau khi import."""
    last = page.duration - 1
    cw = CompWriter(W, H, last + frame_offset)
    o = frame_offset
    sh = lambda keys: {f + o: v for f, v in keys.items()}   # dời keyframe nếu comp không bắt đầu ở 0
    pr = page.preset
    base = hex_rgb(pr["text_color"])
    outline = hex_rgb(pr.get("outline_color", "#000000"))
    rng = random.Random(page.idx)
    text_map = {}

    bg = cw.tool("BG", "Background", {**cw.frame_inputs(), "TopLeftRed": 0, "TopLeftGreen": 0,
                                      "TopLeftBlue": 0, "TopLeftAlpha": 0}, x=0)
    prev = bg
    for i, w in enumerate(page.words):
        cw.ypos = i + 1
        n = f"W{i}"
        s, e = w.start_f, max(w.end_f, w.start_f + 2)
        hl_end = last if w.hold_highlight else e
        use_box = w.animation == "box_swipe"

        # ---- màu chữ: bậc thang base -> highlight -> base ----
        if use_box:      # chữ giữ màu base, highlight nằm ở hộp nền
            col = {c: {0: base[c]} for c in range(3)}
        else:
            col = {c: step_keys([(0, base[c]), (s, w.highlight[c])] +
                                ([(hl_end, base[c])] if hl_end < last else []))
                   for c in range(3)}
        text_inputs = {
            **cw.frame_inputs(),
            "StyledText": w.text,
            "Font": pr["font"], "Style": pr.get("style", "Black"),
            "Size": round(page.size, 5),
            "Center": (w.cx, page.y),
            "Red1": cw.spline(n + "R", sh(col[0])),
            "Green1": cw.spline(n + "G", sh(col[1])),
            "Blue1": cw.spline(n + "B", sh(col[2])),
            # VERIFY: shading element 2 mặc định = Outline trong Text+
            "Enabled2": 1, "Thickness2": pr.get("outline_width", 0.08),
            "Red2": outline[0], "Green2": outline[1], "Blue2": outline[2],
        }
        node = cw.tool(n + "_Text", "TextPlus", text_inputs, x=1)
        text_map[n + "_Text"] = w.text

        if w.animation == "neon_glow":
            gain = {0: 0.0, s: 0.0, s + 3: 2.5, e: 2.0, min(e + 5, last): 0.0}
            node = cw.tool(n + "_Glow", "SoftGlow", {"Gain": cw.spline(n + "Gain", sh(gain)),
                                                     "Threshold": 0.0, "XGlowSize": 14.0, "Input": node}, x=2)
        if use_box:
            pad = 0.25 * page.size * SIZE_TO_EM
            bw, x0 = w.width + 2 * pad, w.cx - w.width / 2 - pad
            sw = max(3, int(0.12 * fps))
            wkeys = {0: 0.0, s: 0.0, s + sw: bw, hl_end: bw, min(hl_end + sw, last): 0.0} if hl_end < last \
                else {0: 0.0, s: 0.0, s + sw: bw}
            cx = {f: x0 + v / 2 for f, v in wkeys.items()}           # giữ mép trái cố định -> "swipe" sang phải
            rect = cw.tool(n + "_Rect", "RectangleMask", {
                "MaskWidth": W, "MaskHeight": H, "PixelAspect": (1.0, 1.0),
                "Center": cw.xypath(n + "_RC", sh(cx), {o: page.y}),
                "Width": cw.spline(n + "_RW", sh(wkeys)),
                # CALIBRATE: kiểm tra Height mask Fusion theo đơn vị nào trên khung dọc/ngang của bạn
                "Height": round(page.size * SIZE_TO_EM * 1.25, 5),
                "CornerRadius": 0.25}, x=2)
            box = cw.tool(n + "_Box", "Background", {
                **cw.frame_inputs(), "TopLeftRed": w.highlight[0], "TopLeftGreen": w.highlight[1],
                "TopLeftBlue": w.highlight[2], "TopLeftAlpha": 1,
                "EffectMask": rect.replace('"Output"', '"Mask"')}, x=3)
            node = cw.tool(n + "_BoxMrg", "Merge", {"Background": box, "Foreground": node}, x=4)

        # ---- Transform: scale pop / bounce / glitch quanh tâm của từ ----
        amp = AMP[w.emphasis]
        xf = {"Pivot": (w.cx, page.y), "Input": node}
        if w.animation in ("scale_pop", "neon_glow", "box_swipe"):
            xf["Size"] = cw.spline(n + "S", sh({0: 1.0, **spring(s, fps, amp, damp=14.0)}))
        elif w.animation == "kinetic_bounce":
            xf["Size"] = cw.spline(n + "S", sh({0: 1.0, **spring(s, fps, amp * 1.3, damp=6.0, freq=2.6)}))
            hop = spring(s, fps, 0.025 * (1 + 2 * amp), damp=6.0, freq=2.6, base=0.5)
            xf["Center"] = cw.xypath(n + "C", sh({0: 0.5}), sh({0: 0.5, **hop}))
        elif w.animation == "glitch":
            jit = range(s, min(s + 7, last))
            cxk = {0: 0.5, **{f: 0.5 + rng.uniform(-0.012, 0.012) for f in jit}, min(s + 7, last): 0.5}
            cyk = {0: 0.5, **{f: 0.5 + rng.uniform(-0.006, 0.006) for f in jit}, min(s + 7, last): 0.5}
            ang = {0: 0.0, **{f: rng.uniform(-4, 4) for f in jit}, min(s + 7, last): 0.0}
            xf["Center"] = cw.xypath(n + "C", sh(cxk), sh(cyk))
            xf["Angle"] = cw.spline(n + "A", sh(ang))
            xf["Size"] = cw.spline(n + "S", sh({0: 1.0, **spring(s, fps, amp, damp=20.0)}))
        node = cw.tool(n + "_Xf", "Transform", xf, x=5)
        prev = cw.tool(f"Merge{i}", "Merge", {"Background": prev, "Foreground": node}, x=6)

    cw.ypos = len(page.words) + 1
    if page.intro == "pop":
        prev = cw.tool("PageXf", "Transform", {"Pivot": (0.5, page.y), "Input": prev,
                                               "Size": cw.spline("PageS", sh(intro_spring(fps)))}, x=7)
    cw.tool("MediaOut1", "MediaOut", {"Index": "0", "Input": prev}, x=8)
    return cw.render(), text_map


# --------------------------------------------------------------------------
# 6. Carrier clip trong suốt (ffmpeg) - chỉ cần dài bằng page dài nhất
# --------------------------------------------------------------------------
def make_carrier(path: str, seconds: float, W: int, H: int, fps: float) -> str:
    if os.path.exists(path):
        return path
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Cần ffmpeg để tạo carrier.mov (hoặc tự tạo 1 clip ProRes 4444 trong suốt).")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={W}x{H}:r={fps},format=rgba",
        "-t", f"{seconds:.2f}", "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", path], check=True, capture_output=True)
    return path


# --------------------------------------------------------------------------
# 7. Resolve
# --------------------------------------------------------------------------
def get_resolve():
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        for p in ("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
                  r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
                  "/opt/resolve/Developer/Scripting/Modules"):
            if os.path.isdir(p):
                sys.path.append(p)
        import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise RuntimeError("Không kết nối được Resolve (cần Studio + Preferences > General > External scripting = Local)")
    return resolve


def place_on_timeline(pages: list, plan: dict, out_dir: str, track: int = 2) -> None:
    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    tl = project.GetCurrentTimeline()
    mp = project.GetMediaPool()
    fps = float(tl.GetSetting("timelineFrameRate"))
    W, H = int(tl.GetSetting("timelineResolutionWidth")), int(tl.GetSetting("timelineResolutionHeight"))
    tl_start = tl.GetStartFrame()            # thường 86400 (01:00:00:00) - recordFrame PHẢI cộng offset này

    while tl.GetTrackCount("video") < track:
        tl.AddTrack("video")
    tl.SetTrackName("video", track, "RF_KINETIC")
    old = tl.GetItemListInTrack("video", track) or []
    if old:                                   # idempotent: chạy lại = dựng lại toàn bộ track
        tl.DeleteClips(old, False)

    longest = max(p.duration for p in pages) / fps + 1
    carrier_path = make_carrier(os.path.join(out_dir, "rf_carrier.mov"), longest, W, H, fps)
    carrier = next((c for c in mp.GetCurrentFolder().GetClipList()
                    if c.GetClipProperty("File Path") == os.path.abspath(carrier_path)), None) \
        or mp.ImportMedia([os.path.abspath(carrier_path)])[0]

    for page in pages:
        items = mp.AppendToTimeline([{
            "mediaPoolItem": carrier, "startFrame": 0, "endFrame": page.duration - 1,
            "trackIndex": track, "recordFrame": tl_start + page.start_f, "mediaType": 1}])
        if not items:
            print(f"[warn] page {page.idx}: AppendToTimeline thất bại", file=sys.stderr)
            continue
        item = items[0]
        item.SetClipColor("Orange")
        comp_path = os.path.join(out_dir, f"page_{page.idx:04d}.comp")
        comp = item.ImportFusionComp(os.path.abspath(comp_path))
        if comp is None:
            print(f"[warn] page {page.idx}: ImportFusionComp thất bại", file=sys.stderr)
            continue
        # VERIFY: comp của clip có thể không bắt đầu ở frame 0 -> dời keyframe rồi import lại.
        offset = int(comp.GetAttrs().get("COMPN_GlobalStart", 0) or 0)
        if offset:
            txt, _ = page_to_comp(page, fps, W, H, frame_offset=offset)
            with open(comp_path, "w", encoding="utf-8") as f:
                f.write(txt)
            name = item.GetFusionCompNameList()[-1]
            comp = item.ImportFusionComp(os.path.abspath(comp_path))
            item.DeleteFusionCompByName(name)
        # Set lại text qua API (an toàn với Unicode tiếng Việt hơn là chỉ dựa vào file)
        _, text_map = page_to_comp(page, fps, W, H)
        for tool_name, text in text_map.items():
            tool = comp.FindTool(tool_name)
            if tool:
                tool.SetInput("StyledText", text)
    print(f"Đã đặt {len(pages)} page lên V{track}.")


# --------------------------------------------------------------------------
# 8. CLI
# --------------------------------------------------------------------------
DEMO_WORDS = [
    {"word": "Thành", "start": 0.20, "end": 0.50}, {"word": "công", "start": 0.50, "end": 0.90},
    {"word": "không", "start": 1.05, "end": 1.30}, {"word": "đến", "start": 1.30, "end": 1.50},
    {"word": "từ", "start": 1.50, "end": 1.70}, {"word": "may", "start": 1.70, "end": 1.95},
    {"word": "mắn.", "start": 1.95, "end": 2.40}, {"word": "Nó", "start": 2.80, "end": 3.00},
    {"word": "đến", "start": 3.00, "end": 3.20}, {"word": "từ", "start": 3.20, "end": 3.35},
    {"word": "1000", "start": 3.35, "end": 3.80}, {"word": "lần", "start": 3.80, "end": 4.05},
    {"word": "thử.", "start": 4.05, "end": 4.60},
]
DEMO_PLAN = {
    "schema_version": "1.0",
    "canvas": {"aspect": "9:16"},
    "presets": {"hormozi": {"font": "Montserrat", "style": "Black", "size": 0.085, "uppercase": True,
                            "text_color": "#FFFFFF", "highlight_color": "#FFD400",
                            "outline_color": "#000000", "outline_width": 0.08,
                            "default_animation": "scale_pop"}},
    "pages": [
        {"word_range": [0, 1], "preset": "hormozi", "position": "center",
         "words": [{"i": 0, "emphasis": "high", "animation": "kinetic_bounce"},
                   {"i": 1, "emphasis": "high", "animation": "kinetic_bounce", "hold_highlight": True}]},
        {"word_range": [2, 4], "preset": "hormozi"},
        {"word_range": [5, 6], "preset": "hormozi",
         "words": [{"i": 6, "emphasis": "high", "animation": "glitch", "highlight_color": "#FF3B3B"}]},
        {"word_range": [7, 9], "preset": "hormozi"},
        {"word_range": [10, 12], "preset": "hormozi",
         "words": [{"i": 10, "emphasis": "high", "animation": "box_swipe", "text_override": "1.000"},
                   {"i": 12, "emphasis": "medium", "animation": "neon_glow", "highlight_color": "#39FF14"}]},
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", help="Whisper word timestamps JSON")
    ap.add_argument("--plan", help="Director Text Plan JSON (bỏ trống = auto plan)")
    ap.add_argument("--schema", default=os.path.join(os.path.dirname(__file__), "director_text_plan.schema.json"))
    ap.add_argument("--out", default="build")
    ap.add_argument("--fps", type=float, default=30.0, help="chỉ dùng cho --dry-run")
    ap.add_argument("--track", type=int, default=2)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="chỉ sinh .comp, không chạm vào Resolve")
    a = ap.parse_args()

    if a.demo:
        words, plan = DEMO_WORDS, DEMO_PLAN
    else:
        with open(a.words, encoding="utf-8") as f:
            words = json.load(f)
        plan = json.load(open(a.plan, encoding="utf-8")) if a.plan else auto_plan(words)
    validate_plan(plan, len(words), a.schema)

    if a.dry_run:
        fps = a.fps
        W, H = CANVAS[plan["canvas"]["aspect"]]
    else:
        tl = get_resolve().GetProjectManager().GetCurrentProject().GetCurrentTimeline()
        fps = float(tl.GetSetting("timelineFrameRate"))
        W, H = int(tl.GetSetting("timelineResolutionWidth")), int(tl.GetSetting("timelineResolutionHeight"))

    os.makedirs(a.out, exist_ok=True)
    pages = build_pages(words, plan, fps)
    manifest = []
    for p in pages:
        txt, _ = page_to_comp(p, fps, W, H)
        with open(os.path.join(a.out, f"page_{p.idx:04d}.comp"), "w", encoding="utf-8") as f:
            f.write(txt)
        manifest.append({"page": p.idx, "start_f": p.start_f, "end_f": p.end_f,
                         "words": [(w.text, w.start_f, w.end_f, w.animation, w.emphasis) for w in p.words]})
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"Sinh {len(pages)} comp vào {a.out}/ @ {fps} fps, {W}x{H}")

    if not a.dry_run:
        place_on_timeline(pages, plan, a.out, a.track)


if __name__ == "__main__":
    main()
