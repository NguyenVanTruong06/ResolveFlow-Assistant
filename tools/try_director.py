"""
Chạy thử AI Director trên dữ liệu thô, không cần mở GUI hay DaVinci Resolve.

Hai cách nạp dữ liệu:
  1) Từ file phụ đề JSON đã có (nhanh, lặp lại được, không cần GPU):
       python tools/try_director.py --subs tools/sample_data/sample_subtitles.json
  2) Từ video thật (Whisper + quét lặng, lưu lại JSON để lần sau dùng cách 1):
       python tools/try_director.py --video "D:/clip.mp4" --model small --save-subs clip_subs.json

Tùy chọn hay dùng: --mode clean_talk|viral_shorts|podcast_summary  --pacing relaxed|balanced|fast
                   --no-repeats  --target 60
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.ai_director import AIDirector, AIDirectorConfig
from src.core.review_state import ReviewState
from src.core import story_planner


def load_from_video(path, model, lang, save_to):
    from src.core.audio import AudioExtractor
    from src.core.transcriber import ModelConfig, ResolveTranscriber
    wav = os.path.join(tempfile.gettempdir(), "rf_try_director.wav")
    if not AudioExtractor.extract_audio(path, wav):
        sys.exit("Không tách được âm thanh (kiểm tra FFmpeg trong PATH).")
    tr = ResolveTranscriber(ModelConfig(model_size=model))
    tr.load_model(print)
    subs = tr.transcribe(wav, language=lang)
    duration = AudioExtractor.get_audio_duration(wav)
    if save_to:
        with open(save_to, "w", encoding="utf-8") as f:
            json.dump({"duration": duration, "subtitles": subs}, f, ensure_ascii=False, indent=2)
        print(f"Đã lưu phụ đề vào {save_to}")
    return subs, duration, wav


def fmt(sec):
    return f"{int(sec // 60):02d}:{sec % 60:05.2f}"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252 không in được tiếng Việt
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--subs", help="File JSON: {'duration': s, 'subtitles': [{start,end,text,words?}]}")
    src.add_argument("--video", help="Video thật để Whisper nhận dạng")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None, help="vi / en (mặc định tự nhận)")
    ap.add_argument("--save-subs", help="Lưu phụ đề nhận dạng ra JSON để dùng lại")
    ap.add_argument("--mode", default="clean_talk")
    ap.add_argument("--pacing", default="balanced", choices=list(story_planner.PACING_PRESETS))
    ap.add_argument("--target", type=float, default=None, help="Thời lượng mục tiêu (viral/summary)")
    ap.add_argument("--no-repeats", action="store_true")
    args = ap.parse_args()

    wav = None
    if args.subs:
        with open(args.subs, encoding="utf-8") as f:
            data = json.load(f)
        subs = data["subtitles"]
        duration = data.get("duration") or subs[-1]["end"] + 1.0
    else:
        subs, duration, wav = load_from_video(args.video, args.model, args.lang, args.save_subs)

    pace = story_planner.get_pacing(args.pacing)
    cfg = AIDirectorConfig(
        mode=args.mode, remove_repeated_phrases=not args.no_repeats,
        target_duration_seconds=args.target,
        min_cut_gap=pace["min_cut_gap"], max_static_shot=pace["max_static_shot"],
        min_punch_in_duration=pace["min_punch_in_duration"],
    )
    director = AIDirector(cfg)
    proposed = director.generate_proposed_segments(subs)

    # Khoảng giữ: quét lặng thật nếu có wav, nếu không thì giữ nguyên vùng có thoại
    keep = None
    if wav:
        from src.core.autocut import AudioCutConfig, SilenceDetector
        segs = SilenceDetector.detect_silence_from_wav(wav, AudioCutConfig(padding_seconds=pace["padding_seconds"]))
        keep = segs
    else:
        keep = [(max(0.0, s["start"] - pace["padding_seconds"]), s["end"] + pace["padding_seconds"]) for s in subs]

    state = ReviewState(proposed)
    print("\n=== ĐỀ XUẤT CỦA AI (✔ giữ / ✖ cắt) ===")
    for p in proposed:
        print(f"{'✔' if p.approved else '✖'} [{fmt(p.start)}-{fmt(p.end)}] {p.confidence*100:5.1f}%  {p.reason:<34} {p.text[:60]}")

    result = director.apply_approved_segments(subs, proposed, keep, duration)
    st, sm = result["stats"], state.summary()
    print("\n=== TÓM TẮT ===")
    print(f"Giữ {sm['kept_count']}/{sm['total_count']} câu; thoại còn {sm['kept_seconds']:.0f}s/{sm['total_seconds']:.0f}s "
          f"(rút gọn {sm['saved_percent']:.0f}%)")
    print(f"Nhịp ({args.pacing}): shot TB {st['avg_shot_length']}s | {st['cuts_per_minute']} nhát cắt/phút | "
          f"shot dài nhất {st['longest_shot']}s | cảnh dài đã tách {st['long_takes_split']}")
    print(f"Bad take xóa: {st['removed_bad_takes']} | Punch-in: {st['punch_ins_created']} | Phương pháp: {st['selection_method']}")
    print("\n=== TIMELINE SAU CẮT ===")
    for seg in result["segments"]:
        if seg.action != "cut":
            tag = f"{seg.action}{' +zoom' if seg.punch_in else ''}"
            print(f"  {fmt(seg.start)} → {fmt(seg.end)}  ({seg.duration:5.1f}s)  {tag}")


if __name__ == "__main__":
    main()
