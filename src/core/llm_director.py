import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Tuple, Optional, Literal

class LLMSelectionError(Exception):
    pass

class LLMSemanticSelector:
    """
    Gọi LLM (Gemini/OpenAI/Ollama) để chọn segment giữ/cắt theo ngữ nghĩa.
    Luôn có fallback về heuristic nếu lỗi bất kỳ khâu nào.
    """
    def __init__(
        self,
        api_key: Optional[str],
        provider: Literal["gemini", "openai", "ollama"] = "gemini",
        timeout_seconds: int = 15
    ):
        self.api_key = api_key
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    def select_segments(
        self,
        subtitles: List[Dict[str, Any]],
        target_duration: float,
        mode: str
    ) -> Tuple[List[int], str]:
        """
        Trả về (kept_indices, reasoning).
        Raise LLMSelectionError nếu không thể lấy kết quả hợp lệ — caller
        (ai_director.py) chịu trách nhiệm catch và fallback, KHÔNG tự fallback
        ở đây để giữ hàm này pure/dễ test.
        """
        if not self.api_key:
            raise LLMSelectionError("API Key is not provided.")

        if target_duration <= 0 or not subtitles:
            return [], ""

        # Chia batch theo cửa sổ 200 segment/lần gọi
        kept_indices = []
        reasoning_list = []
        
        batch_size = 200
        for i in range(0, len(subtitles), batch_size):
            batch = subtitles[i:i + batch_size]
            batch_kept, batch_reason = self._process_batch(batch, target_duration, i)
            kept_indices.extend(batch_kept)
            if batch_reason:
                reasoning_list.append(batch_reason)

        if not kept_indices:
            return [], "Không có segment nào được chọn."

        # Validate tổng duration thực tế của các segment được chọn
        total_kept_duration = 0.0
        for idx in kept_indices:
            if 0 <= idx < len(subtitles):
                sub = subtitles[idx]
                total_kept_duration += (sub["end"] - sub["start"])

        # Nếu lệch quá 30% so với target_duration -> raise LLMSelectionError
        if total_kept_duration < target_duration * 0.7 or total_kept_duration > target_duration * 1.3:
            raise LLMSelectionError(
                f"Tổng thời lượng đã chọn ({total_kept_duration:.1f}s) "
                f"lệch quá 30% so với target_duration ({target_duration:.1f}s)."
            )

        return kept_indices, " ".join(reasoning_list)

    def _process_batch(self, batch: List[Dict[str, Any]], target_duration: float, offset: int) -> Tuple[List[int], str]:
        prompt = self._build_prompt(batch, target_duration)
        
        try:
            response_text = self._call_api(prompt)
        except Exception as e:
            raise LLMSelectionError(f"Lỗi khi gọi API {self.provider}: {str(e)}")

        # Parse response: nếu JSON không parse được -> raise LLMSelectionError
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise LLMSelectionError(f"Không thể parse JSON từ LLM: {str(e)}")
            
        if not isinstance(data, dict):
            raise LLMSelectionError("Kết quả LLM không phải là dict.")

        batch_kept = data.get("kept_indices")
        reasoning = data.get("reasoning", "")

        if not isinstance(batch_kept, list):
            raise LLMSelectionError("Trường 'kept_indices' không phải là list.")

        # Validate các index
        absolute_kept = []
        for idx in batch_kept:
            if not isinstance(idx, int):
                raise LLMSelectionError(f"Index không hợp lệ (không phải int): {idx}")
            if idx < 0 or idx >= len(batch):
                raise LLMSelectionError(f"Index {idx} nằm ngoài range của batch (0-{len(batch)-1}).")
            
            abs_idx = idx + offset
            if abs_idx in absolute_kept:
                raise LLMSelectionError(f"Index bị trùng lặp: {abs_idx}")
                
            absolute_kept.append(abs_idx)
            
        if sorted(absolute_kept) != absolute_kept:
            absolute_kept.sort()

        return absolute_kept, reasoning

    def _build_prompt(self, subtitles: List[Dict[str, Any]], target_duration: float) -> str:
        input_list = []
        for idx, sub in enumerate(subtitles):
            input_list.append({
                "index": idx,
                "start": sub["start"],
                "end": sub["end"],
                "text": sub.get("text", "")
            })
            
        transcript_json = json.dumps(input_list, ensure_ascii=False)
        
        system_prompt = f"""Bạn là biên tập viên video chuyên nghiệp. Bạn nhận một transcript có timestamp
(dạng JSON list các segment: {{index, start, end, text}}).

Nhiệm vụ: chọn ra các segment nên GIỮ để tạo bản dựng theo mục tiêu được cho,
đảm bảo:
1. Mạch nội dung liền lạc về ngữ nghĩa (không cắt giữa câu có liên kết logic
   với câu sau, ví dụ "Vì vậy...", "Do đó...", "Đầu tiên... Thứ hai...").
2. Ưu tiên giữ: câu mở đầu gây chú ý (hook), câu chứa số liệu/kết luận/lời khuyên
   cụ thể, câu chuyển ý quan trọng.
3. Loại bỏ: lặp ý, nói vòng vo không thêm thông tin mới, đoạn off-topic.
4. Tổng thời lượng các segment GIỮ phải nằm trong khoảng
   [{target_duration * 0.85:.1f}, {target_duration * 1.15:.1f}] giây.

CHỈ trả về JSON, không kèm text giải thích, không markdown code fence:
{{
  "kept_indices": [int, ...],
  "reasoning": "tối đa 2 câu giải thích logic chọn"
}}

Nếu input rỗng hoặc target_duration <= 0, trả về {{"kept_indices": [], "reasoning": ""}}.

Input transcript:
{transcript_json}
"""
        return system_prompt

    def _call_api(self, prompt: str) -> str:
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2
                }
            }
            return self._send_request(url, payload, {"Content-Type": "application/json"})
            
        elif self.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            return self._send_request(url, payload, headers)
            
        elif self.provider == "ollama":
            url = "http://localhost:11434/api/generate"
            payload = {
                "model": "llama3",
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }
            return self._send_request(url, payload, {"Content-Type": "application/json"})
            
        else:
            raise ValueError(f"Provider {self.provider} không được hỗ trợ.")

    def _send_request(self, url: str, payload: dict, headers: dict) -> str:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                result = response.read().decode("utf-8")
                result_json = json.loads(result)
                
                if self.provider == "gemini":
                    candidates = result_json.get("candidates", [])
                    if not candidates:
                        raise Exception("Gemini không trả về kết quả.")
                    return candidates[0]["content"]["parts"][0]["text"]
                elif self.provider == "openai":
                    choices = result_json.get("choices", [])
                    if not choices:
                        raise Exception("OpenAI không trả về kết quả.")
                    return choices[0]["message"]["content"]
                elif self.provider == "ollama":
                    return result_json.get("response", "{}")
                
                return result
        except urllib.error.URLError as e:
            raise Exception(f"Lỗi kết nối hoặc timeout: {str(e)}")
