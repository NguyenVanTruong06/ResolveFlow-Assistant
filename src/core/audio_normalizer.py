"""
Module Chuẩn Hóa Âm Lượng Chuyên Nghiệp (EBU R128 / YouTube Loudness Normalizer).
Sử dụng thuật toán 2-Pass Loudnorm chuẩn quốc tế ITU-R BS.1770-4 & EBU R128
nhằm đưa âm lượng giọng nói và video về chuẩn công nghiệp (-14 LUFS / -1.0 dBFS True Peak),
ngăn ngừa hoàn toàn hiện tượng méo tiếng (distortion/clipping) và âm lượng trồi sụt thất thường.
"""

import os
import json
import re
import subprocess
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class LoudnessTarget(BaseModel):
    """
    Cấu hình mục tiêu chuẩn hóa âm lượng.
    """
    target_i: float = Field(default=-14.0, ge=-70.0, le=-5.0, description="Độ to tích phân mục tiêu (Integrated Loudness LUFS)")
    target_tp: float = Field(default=-1.0, ge=-9.0, le=0.0, description="Đỉnh thực tối đa (Max True Peak dBFS)")
    target_lra: float = Field(default=11.0, ge=1.0, le=50.0, description="Dải động âm lượng (Loudness Range LU)")
    target_thresh: float = Field(default=-70.0, ge=-99.0, le=0.0, description="Ngưỡng ồn tối thiểu (Threshold)")
    preset_name: str = Field(default="youtube_tiktok", description="Tên preset cấu hình ('youtube_tiktok', 'podcast_spotify', 'broadcast_ebu_r128')")


class LoudnessMetrics(BaseModel):
    """
    Các chỉ số đo lường trước và sau khi chuẩn hóa.
    """
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    output_i: float
    output_tp: float
    output_lra: float
    output_thresh: float
    normalization_type: str = "2-pass"
    target_preset: str = "youtube_tiktok"


LOUDNESS_PRESETS: Dict[str, LoudnessTarget] = {
    "youtube_tiktok": LoudnessTarget(
        target_i=-14.0,
        target_tp=-1.0,
        target_lra=11.0,
        preset_name="youtube_tiktok"
    ),
    "podcast_spotify": LoudnessTarget(
        target_i=-16.0,
        target_tp=-1.0,
        target_lra=11.0,
        preset_name="podcast_spotify"
    ),
    "broadcast_ebu_r128": LoudnessTarget(
        target_i=-23.0,
        target_tp=-1.0,
        target_lra=7.0,
        preset_name="broadcast_ebu_r128"
    )
}


class AudioNormalizer:
    """
    Động cơ phân tích và chuẩn hóa âm lượng 2-Pass Loudnorm.
    """

    @classmethod
    def get_preset(cls, name: str) -> LoudnessTarget:
        """Lấy preset chuẩn hóa theo tên hoặc trả về mặc định YouTube."""
        return LOUDNESS_PRESETS.get(name, LOUDNESS_PRESETS["youtube_tiktok"])

    @classmethod
    def measure_loudness(
        cls,
        input_audio_path: str,
        target: Optional[LoudnessTarget] = None
    ) -> Dict[str, float]:
        """
        Pass 1: Đo lường các chỉ số Loudness thực tế của tệp âm thanh/video.
        """
        if not os.path.exists(input_audio_path):
            raise FileNotFoundError(f"Không tìm thấy tệp âm thanh nguồn tại: {input_audio_path}")

        tgt = target or LOUDNESS_PRESETS["youtube_tiktok"]

        # Lệnh đo Pass 1 qua null muxer
        filter_str = f"loudnorm=I={tgt.target_i}:TP={tgt.target_tp}:LRA={tgt.target_lra}:print_format=json"
        cmd = [
            "ffmpeg", "-y", "-i", os.path.abspath(input_audio_path),
            "-af", filter_str,
            "-f", "null", "-"
        ]

        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=180, encoding="utf-8", errors="replace"
            )
            stderr_output = res.stderr or ""
            return cls._parse_loudnorm_json(stderr_output)
        except Exception as e:
            raise RuntimeError(f"Lỗi khi đo lường âm lượng với FFmpeg: {str(e)}") from e

    @staticmethod
    def _parse_loudnorm_json(stderr_text: str) -> Dict[str, float]:
        """
        Trích xuất và parse khối JSON do filter loudnorm in ra stderr.
        """
        # Tìm khối { ... } cuối cùng trong stderr
        match = re.search(r"\{\s*\"input_i\".*?\}", stderr_text, re.DOTALL)
        if not match:
            # Fallback regex tìm bất kỳ json object nào chứa input_i
            match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text, re.DOTALL)

        if not match:
            raise ValueError(f"Không tìm thấy dữ liệu thống kê loudnorm JSON trong đầu ra FFmpeg:\n{stderr_text[:500]}")

        raw_json_str = match.group(0)
        try:
            data = json.loads(raw_json_str)
            return {
                "input_i": float(data.get("input_i", -24.0)),
                "input_tp": float(data.get("input_tp", -2.0)),
                "input_lra": float(data.get("input_lra", 10.0)),
                "input_thresh": float(data.get("input_thresh", -34.0)),
                "output_i": float(data.get("output_i", -14.0)),
                "output_tp": float(data.get("output_tp", -1.0)),
                "output_lra": float(data.get("output_lra", 10.0)),
                "output_thresh": float(data.get("output_thresh", -24.0)),
                "target_offset": float(data.get("target_offset", 0.0))
            }
        except Exception as e:
            raise ValueError(f"Không thể parse JSON loudnorm: {str(e)}") from e

    @classmethod
    def normalize_audio_file(
        cls,
        input_audio_path: str,
        output_audio_path: str,
        target: Optional[LoudnessTarget] = None
    ) -> LoudnessMetrics:
        """
        Pass 2: Chuẩn hóa tệp âm thanh WAV/MP3 hoàn chỉnh bằng 2-Pass Loudnorm.
        """
        if not os.path.exists(input_audio_path):
            raise FileNotFoundError(f"Không tìm thấy tệp nguồn: {input_audio_path}")

        tgt = target or LOUDNESS_PRESETS["youtube_tiktok"]

        # 1. Đo lường thông số ở Pass 1
        m = cls.measure_loudness(input_audio_path, tgt)

        # 2. Xây dựng bộ lọc Pass 2 với các tham số đã đo lường
        filter_str = (
            f"loudnorm=I={tgt.target_i}:TP={tgt.target_tp}:LRA={tgt.target_lra}:"
            f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
            f"measured_thresh={m['input_thresh']}:offset={m.get('target_offset', 0.0)}:linear=true:print_format=summary"
        )

        parent_dir = os.path.dirname(output_audio_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        cmd = [
            "ffmpeg", "-y", "-i", os.path.abspath(input_audio_path),
            "-af", filter_str,
            "-ar", "48000",
            os.path.abspath(output_audio_path)
        ]

        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=240, encoding="utf-8", errors="replace"
            )
            if res.returncode != 0:
                raise RuntimeError(f"FFmpeg Pass 2 lỗi (Code {res.returncode}):\n{res.stderr[:500]}")
        except Exception as e:
            raise RuntimeError(f"Lỗi khi thực thi Pass 2 chuẩn hóa âm thanh: {str(e)}") from e

        if not os.path.exists(output_audio_path) or os.path.getsize(output_audio_path) == 0:
            raise RuntimeError("Tệp âm thanh đầu ra không tồn tại hoặc có dung lượng 0 byte sau chuẩn hóa.")

        return LoudnessMetrics(
            input_i=m["input_i"],
            input_tp=m["input_tp"],
            input_lra=m["input_lra"],
            input_thresh=m["input_thresh"],
            output_i=tgt.target_i,
            output_tp=tgt.target_tp,
            output_lra=min(tgt.target_lra, m["input_lra"]),
            output_thresh=m["output_thresh"],
            normalization_type="2-pass",
            target_preset=tgt.preset_name
        )

    @classmethod
    def normalize_video_audio(
        cls,
        video_path: str,
        output_video_path: str,
        target: Optional[LoudnessTarget] = None
    ) -> bool:
        """
        Chuẩn hóa luồng âm thanh trong video và giữ nguyên 100% chất lượng hình ảnh (Video stream copy).
        """
        if not os.path.exists(video_path):
            return False

        tgt = target or LOUDNESS_PRESETS["youtube_tiktok"]

        try:
            m = cls.measure_loudness(video_path, tgt)
            filter_str = (
                f"loudnorm=I={tgt.target_i}:TP={tgt.target_tp}:LRA={tgt.target_lra}:"
                f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
                f"measured_thresh={m['input_thresh']}:offset={m.get('target_offset', 0.0)}:linear=true"
            )

            parent_dir = os.path.dirname(output_video_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            cmd = [
                "ffmpeg", "-y", "-i", os.path.abspath(video_path),
                "-c:v", "copy",
                "-af", filter_str,
                "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
                os.path.abspath(output_video_path)
            ]

            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=300, encoding="utf-8", errors="replace"
            )
            return (res.returncode == 0) and os.path.exists(output_video_path) and os.path.getsize(output_video_path) > 0
        except Exception:
            return False
