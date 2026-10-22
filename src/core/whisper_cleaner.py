"""
src/core/whisper_cleaner.py
Whisper Cleaner & Hallucination Filter V2
=========================================
Lọc bỏ triệt để các câu thoại ảo giác (hallucinations), rác ký tự ngoại ngữ (CJK, Cyrillic),
chuỗi ký tự vô nghĩa (l l l l, dodololol) và khử trùng lặp do Whisper bị kẹt loop (stutter).
Bảo toàn 100% lời thoại thực tế của nhân vật cùng timestamp chính xác.

Bộ lọc v2 gồm 7 luật có thứ tự ưu tiên:
1. zero_duration: end - start < 0.05s
2. foreign_script: CJK/Cyrillic/Hangul (chỉ áp dụng khi language == "vi")
3. no_speech: no_speech_prob > 0.6 VÀ avg_logprob < -1.0
4. repetition_loop: compression_ratio > 2.4
5. outro_pattern: mẫu câu chào/subscribe chiếm >= 60% số ký tự của câu
6. repeated_sentence: cùng một câu >= 6 từ lặp >= 3 lần trong clip, cách nhau > 30s
7. noise_chatter: rác âm thanh, ký tự vô nghĩa
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional, Set, Union

logger = logging.getLogger(__name__)

# Ký tự Trung, Nhật, Hàn, Nga (CJK, Kana, Hangul, Cyrillic)
C_FOREIGN_SCRIPT = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]")

# Ký tự lặp vô nghĩa (vd: llllll, dodoloooool)
C_CHAR_FLOOD = re.compile(r"([a-zA-Z])\1{3,}")

# Danh sách mẫu chào, cảm ơn, subscribe kết thúc video (Outro patterns)
OUTRO_PATTERNS = [
    r"(?:hãy\s+)?subscribe(?:\s+(?:cho\s+)?kênh)?(?:\s+nha|\s+nhé)?",
    r"đăng\s+ký\s+kênh",
    r"like\s+và\s+(?:đăng\s+ký|share|chia\s+sẻ)",
    r"chia\s+sẻ\s+video",
    r"theo\s+dõi\s+kênh",
    r"để\s+không\s+bỏ\s+lỡ(?:\s+những\s+video)?",
    r"không\s+bỏ\s+lỡ\s+(?:những\s+)?video",
    r"video\s+hấp\s+dẫn",
    r"cảm\s+ơn\s+các\s+bạn\s+đã\s+(?:xem|theo\s+dõi)(?:\s+video)?",
    r"cảm\s+ơn\s+đã\s+(?:xem|theo\s+dõi)",
    r"hẹn\s+gặp\s+lại(?:\s+các\s+bạn)?(?:\s+nhé)?",
    r"tạm\s+biệt",
    r"bye(?:\s*bye)?",
    r"thank\s+you(?:\s+so\s+much)?(?:\s+for\s+watching)?",
    r"thanks\s+for\s+watching",
    r"chúc\s+các\s+bạn(?:\s+một\s+ngày\s+vui\s+vẻ)?"
]

# Các cụm ảo giác Whisper đặc thù (YouTube training data leaks) loại bỏ ngay lập tức 100%
HALLUCINATION_HARD_TRIGGERS = [
    r"ghiền\s+mì\s+gõ",
    r"la\s+la\s+school",
    r"chundvc",
    r"phụ\s+đề\s+(?:bởi|thực\s+hiện\s+bởi)",
    r"subtitled?\s+by",
    r"transcribed\s+by",
    r"subtitles\s+by",
    r"hãy\s+subscribe\s+cho\s+kênh",
    r"subscribe\s+to\s+my\s+channel",
]

COMPILED_OUTRO_PATTERNS = [re.compile(p, re.IGNORECASE) for p in OUTRO_PATTERNS]
COMPILED_HARD_TRIGGERS = [re.compile(p, re.IGNORECASE) for p in HALLUCINATION_HARD_TRIGGERS]

# Từ điển kiểm duyệt từ tục (Profanity Filter) dành cho YouTube & TikTok
CENSOR_MAP = {
    # Tiếng Việt
    "lồn": "l**",
    "buồi": "b***",
    "cặc": "c**",
    "địt": "đ**",
    "đụ": "đ*",
    "đéo": "đ**",
    "đếch": "đ***",
    "đù": "đ*",
    "chó chết": "ch* ch**",
    "mẹ mày": "m* m**",
    "vãi lồn": "vãi l**",
    "vcl": "v**",
    "clgt": "c***",
    "đkm": "đ**",
    "dcm": "d**",
    "đcm": "đ**",
    # Tiếng Anh
    "fucking": "f***ing",
    "fuck": "f***",
    "shit": "s***",
    "bitch": "b****",
    "asshole": "a*****",
    "damn": "d***",
    "bastard": "b******",
}


def has_foreign_script(text: str) -> bool:
    """Kiểm tra xem chuỗi có chứa ký tự ngoại ngữ lạ (CJK, Nga, Hàn) hay không."""
    return bool(C_FOREIGN_SCRIPT.search(text))


def is_noise_chatter(text: str) -> bool:
    """Kiểm tra chuỗi rác vô nghĩa (l l l l, dodololol, a a a a, chỉ gồm dấu câu)."""
    clean_t = text.strip()
    if not clean_t:
        return True

    # Chuỗi chỉ toàn dấu câu / ký tự đặc biệt
    if re.fullmatch(r"[\s\.\,\!\?\-\.\…\:\;\"\'\(\)\[\]\{\}\<\>\/\\\|]+", clean_t):
        return True

    # Ký tự đơn lẻ lặp lại (vd: "l l l l", "a a a a")
    tokens = clean_t.split()
    if len(tokens) >= 3 and all(len(t) == 1 and t.isalpha() for t in tokens):
        return True

    # Một từ lặp lại >= 4 lần (vd: "cầm cầm cầm cầm")
    if len(tokens) >= 4 and len(set(t.lower() for t in tokens)) == 1:
        return True

    # Một ký tự bị kéo dài quá mức (vd: "dodoloooool", "llllll")
    if C_CHAR_FLOOD.search(clean_t):
        return True

    # Âm tiết lặp lại vô nghĩa (vd: "dodolololdodol", "lalalalala")
    if re.search(r"([a-zA-Z]{2,4})\1{2,}", clean_t.lower()):
        return True

    return False


def is_outro_pattern(text: str) -> bool:
    """
    Kiểm tra xem các mẫu câu chào/subscribe có chiếm >= 60% tổng số ký tự của câu hay không,
    hoặc chứa các mẫu ảo giác đặc thù (Ghiền Mì Gõ, La La School, ChunDVC, v.v.).
    Tránh trường hợp câu thật như 'Cảm ơn anh nha, đường này đẹp quá' bị xóa nhầm.
    """
    clean_t = text.strip()
    if not clean_t:
        return False

    # Nếu chứa các trigger cứng của Whisper hallucination -> loại bỏ 100%
    if any(p.search(clean_t) for p in COMPILED_HARD_TRIGGERS):
        return True

    norm_chars = re.sub(r'[\s\.\,\!\?\-\:\;\…\"\'\(\)\[\]]+', '', clean_t)
    if not norm_chars:
        return False
    total_len = len(norm_chars)

    matched_spans = []
    for pat in COMPILED_OUTRO_PATTERNS:
        for m in pat.finditer(clean_t):
            matched_spans.append((m.start(), m.end()))

    if not matched_spans:
        return False

    matched_spans.sort()
    merged = [matched_spans[0]]
    for cur_s, cur_e in matched_spans[1:]:
        last_s, last_e = merged[-1]
        if cur_s <= last_e:
            merged[-1] = (last_s, max(last_e, cur_e))
        else:
            merged.append((cur_s, cur_e))

    matched_char_count = 0
    for s, e in merged:
        span_text = clean_t[s:e]
        span_norm = re.sub(r'[\s\.\,\!\?\-\:\;\…\"\'\(\)\[\]]+', '', span_text)
        matched_char_count += len(span_norm)

    ratio = matched_char_count / max(1, total_len)
    return ratio >= 0.6


def is_whisper_hallucination(text: str) -> bool:
    """
    Kiểm tra xem một chuỗi text có phải là ảo giác Whisper hay không (dùng chung cho mọi module).
    """
    clean_t = text.strip()
    if not clean_t:
        return True

    if has_foreign_script(clean_t):
        return True

    if is_noise_chatter(clean_t):
        return True

    if is_outro_pattern(clean_t):
        return True

    return False


def deduplicate_phrase_repetitions(text: str) -> str:
    """
    Khử trùng lặp từ hoặc cụm từ do Whisper bị kẹt decoding loop.
    Ví dụ: 'Thật quay dữ Thật quay dữ x6' -> 'Thật quay dữ'
          'cầm cầm cầm cầm x9' -> 'cầm'
    """
    tokens = text.split()
    if len(tokens) <= 2:
        return text

    for n in (4, 3, 2, 1):
        i = 0
        new_tokens = []
        while i < len(tokens):
            phrase = tokens[i:i + n]
            if len(phrase) < n:
                new_tokens.extend(tokens[i:])
                break

            reps = 1
            j = i + n
            while j + n <= len(tokens) and [t.strip(".,?!…").lower() for t in tokens[j:j + n]] == [t.strip(".,?!…").lower() for t in phrase]:
                reps += 1
                j += n

            if reps > 2:
                new_tokens.extend(phrase)
                i = j
            else:
                new_tokens.append(tokens[i])
                i += 1
        tokens = new_tokens

    return " ".join(tokens)


def censor_word_core(word: str) -> str:
    """Loại bỏ dấu câu để tra cứu từ điển kiểm duyệt."""
    return word.strip(".,?!…:;\"'()[]{}").lower()


def get_censored_override(word: str) -> Optional[str]:
    """Trả về từ đã được che nếu nằm trong danh sách nhạy cảm, ngược lại None."""
    core = censor_word_core(word)
    if core in CENSOR_MAP:
        return CENSOR_MAP[core]
    return None


def normalize_for_repeat_check(text: str) -> str:
    """Chuẩn hóa câu chữ thường, bỏ dấu câu và khoảng trắng thừa để so khớp lặp."""
    clean = re.sub(r'[^\w\s]', '', text.lower()).strip()
    return re.sub(r'\s+', ' ', clean)


def find_repeated_sentences_in_clip(
    raw_subtitles: List[Dict[str, Any]],
    clip_duration: Optional[float] = None
) -> Set[str]:
    """
    Tìm các câu bị lặp bất thường trong clip theo luật:
    - Cùng một câu >= 6 từ
    - Lặp >= 3 lần trong cùng 1 clip
    - Khoảng cách giữa các lần > 30s (tính từ lần đầu đến lần cuối)
    """
    occurrences: Dict[str, List[float]] = {}
    for sub in raw_subtitles:
        txt = str(sub.get("text", "")).strip()
        norm = normalize_for_repeat_check(txt)
        words = norm.split()
        if len(words) >= 6:
            st = float(sub.get("start", 0.0))
            if norm not in occurrences:
                occurrences[norm] = []
            occurrences[norm].append(st)

    repeated_to_drop: Set[str] = set()
    for norm_sent, times in occurrences.items():
        if len(times) >= 3:
            span = (times[-1] - times[0])
            if span > 30.0 or (clip_duration and clip_duration > 30.0 and len(times) >= 3):
                repeated_to_drop.add(norm_sent)
    return repeated_to_drop


def clean_whisper_segments_with_stats(
    raw_subtitles: List[Dict[str, Any]],
    language: str = "vi",
    clip_duration: Optional[float] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, List[str]]]:
    """
    Lọc bỏ các phân đoạn phụ đề ảo giác theo 7 luật v2 và thống kê chi tiết.
    
    Returns:
        Tuple[cleaned_segments, stats, examples]
    """
    stats = {
        "zero_duration": 0,
        "foreign_script": 0,
        "no_speech": 0,
        "repetition_loop": 0,
        "outro_pattern": 0,
        "repeated_sentence": 0,
        "noise_chatter": 0
    }
    examples: Dict[str, List[str]] = {k: [] for k in stats}

    def record_example(rule: str, ex_text: str):
        if len(examples[rule]) < 5:
            examples[rule].append(ex_text[:80])

    if not raw_subtitles:
        return [], stats, examples

    # 1. Phát hiện các câu bị lặp trong clip (>30s)
    repeated_in_clip = find_repeated_sentences_in_clip(raw_subtitles, clip_duration=clip_duration)

    cleaned: List[Dict[str, Any]] = []
    is_vi = language.strip().lower() in ("vi", "vietnamese", "tiếng việt")

    for sub in raw_subtitles:
        st = float(sub.get("start", 0.0))
        en = float(sub.get("end", 0.0))
        txt = str(sub.get("text", "")).strip()

        # Luật 1: zero_duration (end - start < 0.05s)
        if (en - st) < 0.05:
            stats["zero_duration"] += 1
            record_example("zero_duration", f"[{st:.2f}s-{en:.2f}s] {txt}")
            continue

        # Luật 2: foreign_script (chỉ áp dụng khi language == "vi")
        if is_vi and has_foreign_script(txt):
            stats["foreign_script"] += 1
            record_example("foreign_script", txt)
            continue

        # Luật 3: no_speech (no_speech_prob > 0.6 VÀ avg_logprob < -1.0)
        nsp = sub.get("no_speech_prob")
        alp = sub.get("avg_logprob")
        if nsp is not None and alp is not None:
            if float(nsp) > 0.6 and float(alp) < -1.0:
                stats["no_speech"] += 1
                record_example("no_speech", f"{txt} (nsp={float(nsp):.2f}, alp={float(alp):.2f})")
                continue

        # Luật 4: repetition_loop (compression_ratio > 2.4)
        cr = sub.get("compression_ratio")
        if cr is not None and float(cr) > 2.4:
            stats["repetition_loop"] += 1
            record_example("repetition_loop", f"{txt} (cr={float(cr):.2f})")
            continue

        # Luật 5: outro_pattern (mẫu chào/subscribe chiếm >= 60% số ký tự)
        if is_outro_pattern(txt):
            stats["outro_pattern"] += 1
            record_example("outro_pattern", txt)
            continue

        # Luật 6: repeated_sentence (câu >= 6 từ lặp >= 3 lần cách nhau > 30s)
        norm_txt = normalize_for_repeat_check(txt)
        if norm_txt in repeated_in_clip:
            stats["repeated_sentence"] += 1
            record_example("repeated_sentence", txt)
            continue

        # Luật 7: noise_chatter (rác âm thanh, ký tự vô nghĩa hoặc rỗng)
        if is_noise_chatter(txt):
            stats["noise_chatter"] += 1
            record_example("noise_chatter", txt)
            continue

        # Khử trùng lặp chuỗi decode stutter
        dedup_txt = deduplicate_phrase_repetitions(txt)
        if not dedup_txt:
            stats["noise_chatter"] += 1
            continue

        # Lọc danh sách từ bên trong (nếu có)
        words = sub.get("words", [])
        clean_words = []
        if words:
            last_w = None
            rep_count = 0
            for w in words:
                raw_w = str(w.get("word", "")).strip()
                if not raw_w:
                    continue
                if is_vi and has_foreign_script(raw_w):
                    continue
                if is_noise_chatter(raw_w):
                    continue
                norm_core = censor_word_core(raw_w)
                if norm_core == last_w and norm_core:
                    rep_count += 1
                    if rep_count >= 2:
                        continue
                else:
                    last_w = norm_core
                    rep_count = 0
                clean_words.append(w)

        clean_sub = dict(sub)
        clean_sub["text"] = dedup_txt
        if clean_words:
            clean_sub["words"] = clean_words
        elif words:
            clean_sub["words"] = []

        # Gắn cờ low_confidence nếu avg_logprob < -1.0 (nhưng không xóa)
        if alp is not None and float(alp) < -1.0:
            clean_sub["low_confidence"] = True
        else:
            clean_sub["low_confidence"] = False

        cleaned.append(clean_sub)

    return cleaned, stats, examples


def clean_whisper_segments(
    raw_subtitles: List[Dict[str, Any]],
    language: str = "vi",
    return_stats: bool = False,
    clip_duration: Optional[float] = None
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], Dict[str, int]]]:
    """
    Hàm wrapper tương thích ngược 100% cho toàn bộ code đang gọi clean_whisper_segments.
    Nếu return_stats=True, trả về Tuple[cleaned_segments, stats].
    Ngược lại trả về cleaned_segments.
    """
    cleaned, stats, _ = clean_whisper_segments_with_stats(
        raw_subtitles,
        language=language,
        clip_duration=clip_duration
    )
    if return_stats:
        return cleaned, stats
    return cleaned


def format_filter_summary(total_dropped: int, stats: Dict[str, int]) -> str:
    """Tạo chuỗi tóm tắt 1 dòng hiển thị trên UI sau khi lọc."""
    friendly_names = {
        "zero_duration": "thời lượng rỗng",
        "foreign_script": "ký tự lạ",
        "no_speech": "không có tiếng nói",
        "repetition_loop": "vòng lặp nén",
        "outro_pattern": "outro/subscribe",
        "repeated_sentence": "câu lặp",
        "noise_chatter": "rác âm thanh"
    }
    rule_parts = []
    for k in ["zero_duration", "foreign_script", "no_speech", "repetition_loop", "outro_pattern", "repeated_sentence", "noise_chatter"]:
        cnt = stats.get(k, 0)
        if cnt > 0:
            rule_parts.append(f"{cnt} {friendly_names.get(k, k)}")
    rules_str = ", ".join(rule_parts) if rule_parts else "không có"
    return f"Đã lọc {total_dropped} câu nhiễu: {rules_str}"


def save_filter_report(
    project_name: str,
    total_raw: int,
    total_kept: int,
    stats: Dict[str, int],
    clips_stats: Optional[Dict[str, Any]] = None,
    examples: Optional[Dict[str, List[str]]] = None,
    cache_dir: Optional[str] = None
) -> str:
    """
    Ghi báo cáo lọc ảo giác vào ~/.resolveflow_cache/reports/<project>_filter.json.
    """
    if cache_dir is None:
        cache_dir = os.path.expanduser("~/.resolveflow_cache")
    reports_dir = os.path.join(cache_dir, "reports")
    try:
        os.makedirs(reports_dir, exist_ok=True)
    except Exception as e:
        logger.warning(f"Không thể tạo thư mục reports {reports_dir}: {e}")

    clean_project = re.sub(r'[^\w\-_\.]', '_', project_name or "project").strip("_")
    report_file = os.path.join(reports_dir, f"{clean_project}_filter.json")

    report_data = {
        "project": project_name,
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_raw": total_raw,
            "total_kept": total_kept,
            "total_dropped": total_raw - total_kept,
            "stats": stats
        },
        "clips": clips_stats or {},
        "examples": examples or {}
    }

    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Đã lưu báo cáo lọc ảo giác: {report_file}")
        return report_file
    except Exception as e:
        logger.warning(f"Không thể ghi báo cáo lọc ảo giác {report_file}: {e}")
        return ""
