import os
import sys
import json
import xml.etree.ElementTree as ET
from unittest.mock import patch, MagicMock

# Force UTF-8 encoding for standard outputs
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Đảm bảo import được các module từ src
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.ui.app import PipelineWorker
from src.core.ai_director import ProposedSegment, AIDirector, AIDirectorConfig
from src.core.autocut import AudioCutConfig, SilenceDetector
from src.core.resolve_api import map_subtitles_to_timeline

def create_mock_video_file(path: str):
    """Tạo một file giả lập video"""
    with open(path, "w", encoding="utf-8") as f:
        f.write("mock video content")

def parse_fcpxml_clips(fcpxml_path: str):
    """Đọc tệp FCPXML và trích xuất thông tin các clip trong spine và các title phụ đề"""
    tree = ET.parse(fcpxml_path)
    root = tree.getroot()
    
    clips = []
    titles = []
    
    # Duyệt qua các asset-clip trong spine
    for asset_clip in root.findall(".//asset-clip"):
        name = asset_clip.get("name")
        offset = asset_clip.get("offset")
        start = asset_clip.get("start")
        duration = asset_clip.get("duration")
        
        # Hàm convert fraction string (e.g. "1000/1000s" hoặc "1.5s") sang float
        def parse_time(t_str):
            if not t_str:
                return 0.0
            t_str = t_str.rstrip('s')
            if '/' in t_str:
                num, den = map(float, t_str.split('/'))
                return num / den
            return float(t_str)
            
        clip_info = {
            "name": name,
            "offset_seconds": parse_time(offset),
            "start_seconds": parse_time(start),
            "duration_seconds": parse_time(duration),
            "nested_titles": []
        }
        
        # Tìm các title phụ đề lồng bên trong asset-clip
        for title in asset_clip.findall(".//title"):
            title_offset = title.get("offset")
            title_duration = title.get("duration")
            text_style = title.find(".//text-style")
            text = text_style.text if text_style is not None else ""
            if not text:
                # Nếu karaoke style, text ghép từ nhiều text-style
                spans = title.findall(".//text-style")
                text = " ".join(span.text.strip() for span in spans if span.text)
                
            clip_info["nested_titles"].append({
                "text": text.strip(),
                "offset_seconds": parse_time(title_offset),
                "duration_seconds": parse_time(title_duration)
            })
            
        clips.append(clip_info)
        
    return clips

def run_debug_pipeline():
    video_path = os.path.abspath("test_video_source.mp4")
    create_mock_video_file(video_path)
    
    # 1. Định nghĩa dữ liệu giả lập
    # Video dài 10 giây.
    # SilenceDetector phát hiện âm thanh/tiếng nói ở 3 khoảng (giữ lại):
    # - Đoạn 1: 0.0s đến 3.0s
    # - Đoạn 2: 4.0s đến 7.0s
    # - Đoạn 3: 8.0s đến 10.0s
    # (Tương đương im lặng ở: 3.0s-4.0s và 7.0s-8.0s)
    silence_keep_intervals = [(0.0, 3.0), (4.0, 7.0), (8.0, 10.0)]
    
    # Dữ liệu phụ đề Whisper trả về (ở video gốc):
    # - Sub 1: 0.5s đến 2.5s ("Xin chào các bạn đã đến với ResolveFlow")
    # - Sub 2: 4.5s đến 6.5s ("Đây là câu nói thử bị vấp cần phải cắt bỏ")
    # - Sub 3: 8.5s đến 9.5s ("Chúc các bạn một ngày tốt lành")
    raw_subtitles = [
        {
            "start": 0.5,
            "end": 2.5,
            "text": "Xin chào các bạn đã đến với ResolveFlow",
            "words": [
                {"word": "Xin", "start": 0.5, "end": 0.8},
                {"word": "chào", "start": 0.8, "end": 1.1},
                {"word": "các", "start": 1.1, "end": 1.4},
                {"word": "bạn", "start": 1.4, "end": 1.7},
                {"word": "đến", "start": 1.7, "end": 2.0},
                {"word": "với", "start": 2.0, "end": 2.2},
                {"word": "ResolveFlow", "start": 2.2, "end": 2.5}
            ]
        },
        {
            "start": 4.5,
            "end": 6.5,
            "text": "Đây là câu nói thử bị vấp cần phải cắt bỏ",
            "words": [
                {"word": "Đây", "start": 4.5, "end": 4.8},
                {"word": "là", "start": 4.8, "end": 5.0},
                {"word": "câu", "start": 5.0, "end": 5.3},
                {"word": "nói", "start": 5.3, "end": 5.5},
                {"word": "thử", "start": 5.5, "end": 5.8},
                {"word": "bị", "start": 5.8, "end": 6.0},
                {"word": "vấp", "start": 6.0, "end": 6.2},
                {"word": "cắt", "start": 6.2, "end": 6.4},
                {"word": "bỏ", "start": 6.4, "end": 6.5}
            ]
        },
        {
            "start": 8.5,
            "end": 9.5,
            "text": "Chúc các bạn một ngày tốt lành",
            "words": [
                {"word": "Chúc", "start": 8.5, "end": 8.7},
                {"word": "các", "start": 8.7, "end": 8.9},
                {"word": "bạn", "start": 8.9, "end": 9.1},
                {"word": "ngày", "start": 9.1, "end": 9.3},
                {"word": "tốt", "start": 9.3, "end": 9.4},
                {"word": "lành", "start": 9.4, "end": 9.5}
            ]
        }
    ]
    
    print("--- [BƯỚC 1] KHỞI CHẠY PIPELINE GIẢ LẬP ĐỂ THU THẬP BẰNG CHỨNG ---")
    
    # Chạy Phase 1
    worker1 = PipelineWorker(
        video_paths=[video_path],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=1,
        use_cache=False
    )
    
    with patch("src.core.audio.AudioExtractor.extract_audio"):
        with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
            with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=raw_subtitles):
                    with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=silence_keep_intervals):
                        worker1.run()
                        
    # Kiểm tra đề xuất AI
    proposed_segs = worker1.proposed_segments
    print(f"\nAI Director đề xuất {len(proposed_segs)} phân đoạn:")
    for p in proposed_segs:
        print(f"  - ID {p.id}: [{p.start}s - {p.end}s] '{p.text}' -> Quyết định: {p.decision} (Reason: {p.reason})")
        
    # Giả lập hành vi người dùng phê duyệt trên GUI:
    # Người dùng chọn CẮT bỏ đoạn vấp (Sub 2).
    # Mặc định BadTakeDetector tự động đánh dấu cut rồi, nhưng để chắc chắn ta set approved = False cho đoạn ID = 1.
    proposed_segs[1].approved = False
    
    # Chạy Phase 2 để xuất bản dựng
    worker2 = PipelineWorker(
        video_paths=[video_path],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=2,
        proposed_segments_override=proposed_segs,
        clip_data_cache=worker1.clip_data_out_cache,
        use_cache=False
    )
    
    with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
        with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
            worker2.run()
            
    # Lấy thông tin kết quả xuất ra
    fcpxml_path = os.path.abspath("test_video_source_Timeline_CatLoc.fcpxml")
    if not os.path.exists(fcpxml_path):
        print(f"Lỗi: Không tìm thấy file FCPXML kết quả tại: {fcpxml_path}")
        return
        
    # Đọc FCPXML clips
    fcpxml_clips = parse_fcpxml_clips(fcpxml_path)
    
    # Dọn dẹp file tạm
    # try:
    #     os.remove(video_path)
    #     os.remove(fcpxml_path)
    #     os.remove(os.path.abspath("test_video_source_Timeline_CatLoc.edl"))
    #     os.remove(os.path.abspath("test_video_source_PhuDe_VideoDaCat.srt"))
    #     os.remove(os.path.abspath("test_video_source_PhuDe_Karaoke_VideoDaCat.fcpxml"))
    #     os.remove(os.path.abspath("test_video_source_PhuDe_VideoGoc.srt"))
    #     os.remove(os.path.abspath("test_video_source_PhuDe_Karaoke_VideoGoc.fcpxml"))
    #     os.remove(os.path.abspath("test_video_source_BaoCao_NhatKyXuLy.md"))
    # except Exception:
    #     pass
        
    # --- THU THẬP VÀ IN DANH SÁCH THEO YÊU CẦU BƯỚC 1 ---
    
    # a) Danh sách segments của module CẮT quyết định giữ lại / xóa
    # SilenceDetector trả về keep_intervals = [(0.0, 3.0), (4.0, 7.0), (8.0, 10.0)]
    # Sau khi qua AI Director (apply_approved_segments), đoạn (4.5, 6.5) bị cắt bỏ
    # Do đó, khoảng giữ lại thực tế của module cắt là:
    # Đoạn 1: 0.0s đến 3.0s (giữ nguyên)
    # Đoạn 2: 4.0s đến 7.0s trừ đi đoạn (4.5, 6.5) -> tách làm 2 đoạn: (4.0, 4.5) và (6.5, 7.0)
    # Đoạn 3: 8.0s đến 10.0s (giữ nguyên)
    # Tổng danh sách giữ lại mong muốn: [(0.0, 3.0), (4.0, 4.5), (6.5, 7.0), (8.0, 10.0)]
    # Hãy in ra danh sách thực tế của module cắt
    
    # Ta tái lập lại hàm áp dụng của AI Director để lấy kết quả chính xác
    cut_config = AudioCutConfig(min_silent_duration=0.5, silence_threshold_db=-35.0, padding_seconds=0.25)
    ai_config = AIDirectorConfig(mode="clean_talk", remove_bad_takes=True, enable_punch_in=True)
    director = AIDirector(ai_config)
    
    ai_res = director.apply_approved_segments(
        subtitles=raw_subtitles,
        proposed_segments=proposed_segs,
        silence_keep_intervals=silence_keep_intervals,
        total_duration=10.0
    )
    
    actual_keep_intervals = ai_res["keep_intervals"]
    actual_cut_subtitles = ai_res["subtitles"]
    
    # b) Danh sách clips được ghi vào FCPXML
    # c) Phụ đề cuối cùng xuất ra cho bản "video đã cắt"
    
    # Chuẩn bị dữ liệu ghi file debug_segments.json
    mapped_subs = map_subtitles_to_timeline(raw_subtitles, actual_keep_intervals, 0.0)
    debug_data = {
        "a_cutter_decision": {
            "keep_intervals": actual_keep_intervals,
            "explanation": "Danh sách các đoạn video/audio giữ lại để dựng thành phim"
        },
        "b_fcpxml_clips": [
            {
                "name": clip["name"],
                "timeline_offset": clip["offset_seconds"],
                "media_start": clip["start_seconds"],
                "media_duration": clip["duration_seconds"],
                "nested_titles": clip["nested_titles"]
            }
            for clip in fcpxml_clips
        ],
        "c_subtitle_timestamps": [
            {
                "text": sub["text"],
                "start": sub["start"],
                "end": sub["end"],
                "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in sub.get("words", [])]
            }
            for sub in mapped_subs
        ]
    }

    with open("debug_segments.json", "w", encoding="utf-8") as df:
        json.dump(debug_data, df, ensure_ascii=False, indent=2)
        
    print("\n--- KẾT QUẢ ĐÃ GHI VÀO debug_segments.json ---")
    print(json.dumps(debug_data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    run_debug_pipeline()
