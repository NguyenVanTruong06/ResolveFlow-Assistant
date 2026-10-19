#!/usr/bin/env python3
"""
ResolveFlow Assistant - SFX Generator Tool
Công cụ dòng lệnh tạo nhanh bộ hiệu ứng âm thanh SFX 48kHz vào assets/sfx.
"""
import os
import sys

# Đảm bảo đường dẫn gốc của dự án có trong sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.sfx_generator import SFX_REGISTRY, SAMPLE_RATE, generate_all_sfx, generate_sfx

if __name__ == "__main__":
    output_dir = os.path.join(ROOT_DIR, "assets", "sfx")
    print(f"Đang sinh {len(SFX_REGISTRY)} tệp SFX vào: {output_dir}")
    count = generate_all_sfx(output_dir)
    print(f"Hoàn tất sinh {count} tệp âm thanh SFX chuẩn 48000Hz 16-bit PCM.")
