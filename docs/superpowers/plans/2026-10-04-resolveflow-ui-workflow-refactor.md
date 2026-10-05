# ResolveFlow UI & AI Workflow Refactor Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor ResolveFlow UI architecture and editing workflows: move Whisper STT model & language configuration to Stage 1 (Scan & Cache), eliminate duplicated JSON loading buttons, upgrade Tab AI Copilot with 3-tier AI engine support (Local Ollama, Cloud API, Free Web Prompt) with content format selector, streamline Tab AutoCut for pure offline editing, and verify with tests.

**Architecture:** 
- Stage 1 (Nạp & Quét): Controls media source input, Whisper model (`large-v3`, `small`, `base`, etc.), language (`Tiếng Việt`, `Auto`, `English`), and Scan Cache toggle. Single entry point for Phase 1 scanning.
- Stage 2 Tab 1 (Kịch Bản AI Copilot): Houses the 3 AI Engines (Local Ollama GPU, Cloud API Key, Free Web Copy-Paste Prompt) + Format Selector (TikTok Short, Vlog Storytelling, Podcast/Talking Head).
- Stage 2 Tab 2 (Cắt Thô Auto Cut): Houses purely offline heuristic silence & semantic cutting controls without Whisper duplication.
- Core `story_copilot.py`: Adds direct inference support for Ollama (`/v1/chat/completions` or `/api/generate`) and Cloud API (DeepSeek/Claude/OpenAI), with automated JSON parsing.

**Tech Stack:** PySide6 (Qt for Python), Whisper / Faster-Whisper, Ollama API / OpenAI-compatible API, Pydantic, Pytest.

---

## Global Constraints
- Preserve 100% backward compatibility for all existing unit tests and DaVinci Resolve API integration.
- Maintain existing dark-modern UI stylesheet (`#0F172A`, `#1E293B`, `#38BDF8`, `#10B981`, `#8B5CF6`).
- No external heavy dependencies; use built-in `urllib.request` or `httpx` (already in requirements) for Ollama/API calls.

---

## Review Focus
1. Switching Whisper model or language in Stage 1 must immediately take effect during Phase 1 scan and save cache properly with the selected model/lang key.
2. When using Tab Copilot JSON mode, no offline silence cutting parameters should conflict or alter the AI story plan.
3. Local Ollama execution should handle connection errors gracefully (e.g. Ollama service not running) with a clear, helpful message.
4. Presets and Subtitle Text+ bindings must continue working across both Copilot and AutoCut workflows.
5. All 251+ pytest unit tests must pass after refactoring.

---

## Tasks

### Task 1: Clean up Tab AutoCut and decouple Whisper configuration
**Files:**
- Modify: `src/ui/tabs/tab_autocut.py`
- Test: `tests/test_ui.py`

- [ ] Remove Section 1.3 (NHẬN DIỆN GIỌNG NÓI WHISPER & CACHE) from `TabAutoCut` so it does not appear in Stage 2.
- [ ] Ensure `TabAutoCut` retains: Silence detection sliders (dB, min duration), Vlog-safe motion guard, Speed-ramp timelapse 8x, and Semantic clean (bad takes removal).
- [ ] Add or retain explicit offline execution button in `TabAutoCut` ("🚀 XUẤT TIMELINE CẮT THÔ OFFLINE").
- [ ] Run `python -m pytest tests/test_ui.py` and fix any missing widget attribute references by updating delegates in `app.py`.

---

### Task 2: Upgrade Stage 1 UI in MainWindow (src/ui/app.py)
**Files:**
- Modify: `src/ui/app.py`
- Test: `tests/test_ui.py`

- [ ] In Stage 1 Card 2 ("CẤU HÌNH NHẬN DIỆN LỜI THOẠI & BỘ NHỚ ĐỆM"):
  - Add `combo_whisper_model` (Options: `large-v3 (Chuẩn nhất)`, `medium`, `small (Nhanh)`, `base`, `tiny`).
  - Add `combo_whisper_lang` (Options: `Tiếng Việt`, `Auto (Tự nhận diện)`, `English`).
  - Add `check_scan_cache` checkbox ("Bật Scan Cache siêu tốc").
  - Add `check_fill_gaps` checkbox ("Tự động lấp khoảng trống phụ đề").
- [ ] In Stage 1 Card 3 ("TIẾN HÀNH QUÉT HOẶC CHUYỂN BƯỚC DỰNG"):
  - Remove redundant `btn_s1_load_json` ("Đã có Kịch Bản JSON? ➔ Nạp & Dựng Ngay").
  - Keep `btn_stage1_main_scan` ("🔍 1. BẮT ĐẦU QUÉT NGUỒN & NẠP CACHE") and `btn_s1_skip_stage2` ("⏩ Sang Bước 2: Chọn Kiểu Dựng").
- [ ] Connect Stage 1 Whisper model/language controls to `PipelineWorker` scan parameters.

---

### Task 3: Enhance StoryCopilot with Local Ollama & Cloud API Execution Engine
**Files:**
- Modify: `src/core/story_copilot.py`
- Test: `tests/test_story_copilot.py`

- [ ] Add `run_ollama_inference(prompt, model="qwen2.5:7b-instruct", endpoint="http://localhost:11434") -> CopilotDirectorPlan` method to `StoryCopilot`.
- [ ] Add `run_cloud_api_inference(prompt, api_key, provider="deepseek", model="deepseek-chat") -> CopilotDirectorPlan` method to `StoryCopilot`.
- [ ] Add format-specific prompt generator helpers (TikTok 9:16 vs Daily Vlog vs Podcast).
- [ ] Write unit tests in `tests/test_story_copilot.py` mocking Ollama and API responses.

---

### Task 4: Upgrade TabCopilot UI with 3 AI Engines & Content Format Selector
**Files:**
- Modify: `src/ui/tabs/tab_copilot.py`
- Test: `tests/test_ui.py`

- [ ] Add Content Format selector: `TikTok / Reels Short (9:16)`, `Daily Travel Vlog (16:9)`, `Podcast / Talking Head (16:9)`.
- [ ] Add 3-Tier AI Engine Selector:
  - Mode 1: `Miễn Phí Web (Copy Prompt -> Dán Claude/ChatGPT Web -> Dán JSON)`
  - Mode 2: `AI Local (Ollama - Qwen 2.5 / Llama 3)` with Host URL, Model name input, and "🚀 AI Tự Động Lên Kịch Bản (1-Click)" button.
  - Mode 3: `Cloud API (DeepSeek / Claude / OpenAI)` with API Key input, Model name, and "🚀 Chạy Cloud API (1-Click)" button.
- [ ] Update signals: `quick_copy_prompt_requested`, `run_local_ai_requested`, `run_cloud_api_requested`, `apply_json_text_requested`, `browse_json_requested`.

---

### Task 5: Bind Signals, Implement 1-Click AI Workers & Full Integration Verification
**Files:**
- Modify: `src/ui/app.py`
- Test: `tests/test_ui.py`, `tests/test_story_copilot.py`

- [ ] Implement async/threaded worker in `app.py` for Ollama and Cloud API calls to prevent UI freezing during LLM generation.
- [ ] Auto-fill the generated JSON into the timeline builder upon completion of Local AI / Cloud API.
- [ ] Run full test suite: `python -m pytest` ensuring 251+ tests pass.
- [ ] Verify clean UI launch without warnings.
