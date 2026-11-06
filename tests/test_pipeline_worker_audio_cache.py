"""
Unit and integration tests for PipelineWorker audio cache verification.
G05-05A-I Acceptance Test Suite
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication

from src.ui.auto.auto_window import PipelineWorker
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_pipeline_worker_skips_whisper_when_all_clips_verified(tmp_path, qapp):
    """Khi tất cả các clip đều có v3 verified cache (kể cả clip có raw_subtitles=[]), không tải mô hình Whisper."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video1 = str(tmp_path / "clip1.mp4")
    video2 = str(tmp_path / "clip2.mp4")
    with open(video1, "wb") as f:
        f.write(b"VIDEO_1_CONTENT" * 50)
    with open(video2, "wb") as f:
        f.write(b"VIDEO_2_SILENT_BROLL" * 50)

    h1, p1 = compute_full_file_sha256(video1)
    h2, p2 = compute_full_file_sha256(video2)

    cache_mgr.save_scan_result(
        video1,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Clip 1"}]},
        model_size="small",
        language="vi",
        verified_identity=(h1, p1)
    )
    # Clip 2 là video không thoại (raw_subtitles=[])
    cache_mgr.save_scan_result(
        video2,
        {"clip_dur": 8.0, "raw_subtitles": []},
        model_size="small",
        language="vi",
        verified_identity=(h2, p2)
    )

    worker = PipelineWorker(
        video_paths=[video1, video2],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model") as mock_load_model, \
         patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Không tải mô hình Whisper
        mock_load_model.assert_not_called()
        # Worker kết thúc thành công với cả 2 clip nạp từ cache
        assert len(worker.clip_data_out_cache) == 2
        assert worker.clip_data_out_cache[0]["raw_subtitles"][0]["text"] == "Clip 1"
        assert worker.clip_data_out_cache[1]["raw_subtitles"] == []


def test_pipeline_worker_single_miss_transcribes_only_missing_clip(tmp_path, qapp):
    """Khi 1 clip có cache và 1 clip chưa có cache, chỉ tải model và transcribe đúng clip thiếu."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video1 = str(tmp_path / "cached_clip.mp4")
    video2 = str(tmp_path / "uncached_clip.mp4")
    with open(video1, "wb") as f:
        f.write(b"CACHED_CONTENT" * 50)
    with open(video2, "wb") as f:
        f.write(b"UNCACHED_CONTENT" * 50)

    h1, p1 = compute_full_file_sha256(video1)
    cache_mgr.save_scan_result(
        video1,
        {"clip_dur": 12.0, "raw_subtitles": [{"start": 0.0, "end": 3.0, "text": "Đã có cache"}]},
        model_size="small",
        language="vi",
        verified_identity=(h1, p1)
    )

    worker = PipelineWorker(
        video_paths=[video1, video2],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    mock_transcribe = MagicMock(return_value=[{"start": 1.0, "end": 4.0, "text": "Clip mới"}])

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model") as mock_load_model, \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=12.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Model được tải vì có video2 thiếu cache
        mock_load_model.assert_called_once()
        # transcribe chỉ được gọi đúng 1 lần cho video2, KHÔNG gọi cho video1
        assert mock_transcribe.call_count == 1

        # video2 được lưu vào cache v3 sau khi quét
        hit_v2 = cache_mgr.get_cached_scan(video2, model_size="small", language="vi", verify_full=True)
        assert hit_v2 is not None
        assert hit_v2["raw_subtitles"][0]["text"] == "Clip mới"


def test_pipeline_worker_cancellation_during_hash_aborts_cleanly(tmp_path, qapp):
    """Khi có yêu cầu ngắt (is_interrupted) trong khi băm, không tải model, không transcribe, không ghi cache."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "cancel_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"CANCEL_VIDEO_DATA" * 50)

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    # Đặt ngắt ngay trước khi preflight băm
    worker.is_interrupted = True

    mock_load_model = MagicMock()
    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.audio.AudioExtractor.extract_audio") as mock_extract, \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # 0 model load, 0 audio extract, 0 save
        mock_load_model.assert_not_called()
        mock_extract.assert_not_called()
        assert not any(f.startswith("v3_") for f in os.listdir(cache_dir))


def test_pipeline_worker_hashes_each_file_only_once_in_stable_run(tmp_path, qapp):
    """Trong chu trình xử lý ổn định, mỗi tệp chỉ được full-hash đúng 1 lần (tái sử dụng identity run-local)."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "single_hash.mp4")
    with open(video, "wb") as f:
        f.write(b"STABLE_PAYLOAD_FOR_SINGLE_HASH" * 100)

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    from src.core import cache_manager as cm_module
    orig_hash_fn = cm_module.compute_full_file_sha256
    hash_spy = MagicMock(side_effect=orig_hash_fn)

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.cache_manager.compute_full_file_sha256", hash_spy), \
         patch("src.ui.auto.auto_window.compute_full_file_sha256", hash_spy), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=[{"start": 0.0, "end": 2.0, "text": "Test"}]), \
         patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # compute_full_file_sha256 chỉ được gọi đúng 1 lần cho video này ở preflight trong toàn bộ quy trình
        assert hash_spy.call_count == 1


def test_pipeline_worker_source_mutated_after_preflight_aborts_cleanly(tmp_path, qapp):
    """Khi file nguồn bị thay đổi sau preflight, worker dừng fail-safe, 0 extract, 0 transcribe, 0 save."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "mutated_mid_run.mp4")
    with open(video, "wb") as f:
        f.write(b"INITIAL_PREFLIGHT_DATA" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Bản cũ"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    finished_events = []
    step_events = []
    worker.finished_signal.connect(lambda s, m: finished_events.append((s, m)))
    worker.step_signal.connect(lambda s, st: step_events.append((s, st)))

    def mutate_file(msg):
        if "Bỏ qua nạp Whisper AI" in msg or "Scan Cache" in msg:
            with open(video, "wb") as f:
                f.write(b"MUTATED_DIFFERENT_DATA_123456789" * 60)

    worker.log_signal.connect(mutate_file)

    mock_load_model = MagicMock()
    mock_transcribe = MagicMock()
    mock_extract = MagicMock()

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Dừng fail-safe: 0 model load, 0 transcribe, 0 extract
        mock_load_model.assert_not_called()
        mock_transcribe.assert_not_called()
        mock_extract.assert_not_called()

        # Tín hiệu kết thúc báo lỗi source_mutated_after_preflight
        assert ("speech_to_text", "error") in step_events
        assert (False, "source_mutated_after_preflight") in finished_events


def test_pipeline_worker_source_mutated_during_transcribe_does_not_publish_v3(tmp_path, qapp):
    """Khi file bị thay đổi trong quá trình transcribe (cùng size và khôi phục mtime), worker từ chối ghi cache v3."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "transcribe_mutate.mp4")
    with open(video, "wb") as f:
        f.write(b"TRANS_ORIGINAL_DATA" * 50)

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    def fake_transcribe(*args, **kwargs):
        # File bị sửa đổi tại chỗ trong khi đang transcribe, khôi phục mtime
        st_before = os.stat(video)
        with open(video, "r+b") as f:
            f.seek(0)
            f.write(b"TRANS_MUTATED_DATA!" * 50)
            f.flush()
            os.fsync(f.fileno())
        os.utime(video, ns=(st_before.st_mtime_ns, st_before.st_mtime_ns))
        return [{"start": 0.0, "end": 2.0, "text": "Đoạn mới"}]

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", side_effect=fake_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Tuyệt đối không publish cache v3 hoặc fallback legacy cho file đã bị đổi
        json_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        assert len(json_caches) == 0


def test_pipeline_worker_cancel_mid_stream_hash(tmp_path, qapp):
    """Khi có yêu cầu hủy sau khi đã đọc ít nhất 1 chunk stream hash, dừng an toàn, 0 model, 0 extract, 0 save."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "big_cancel_clip.mp4")
    # File 3MB (ít nhất 3 chunks 1MB)
    with open(video, "wb") as f:
        f.write(b"STREAM_CANCEL_PAYLOAD" * (3 * 1024 * 1024 // 21))

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    finished_events = []
    worker.finished_signal.connect(lambda s, m: finished_events.append((s, m)))

    from src.core import cache_manager as cm_module
    orig_hash_fn = cm_module.compute_full_file_sha256

    def cancelling_hash_fn(file_path, cancelled_fn=None, chunk_size=1024 * 1024):
        # Thiết lập hủy sau khi hàm hash được gọi
        def check_after_chunk():
            worker.is_interrupted = True
            return True
        return orig_hash_fn(file_path, cancelled_fn=check_after_chunk, chunk_size=chunk_size)

    mock_load_model = MagicMock()
    mock_extract = MagicMock()

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.ui.auto.auto_window.compute_full_file_sha256", side_effect=cancelling_hash_fn), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Dừng an toàn không load model, không extract, không ghi cache
        mock_load_model.assert_not_called()
        mock_extract.assert_not_called()
        json_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        assert len(json_caches) == 0

        # Phải đi vào luồng ngắt (interrupted), không bị biến đổi thành lỗi terminal missing_verified_identity
        assert (False, "missing_verified_identity") not in finished_events


def test_pipeline_worker_cancel_before_publication(tmp_path, qapp):
    """Khi có yêu cầu hủy sau transcribe nhưng trước/khi ghi cache, 0 cache được xuất bản."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "cancel_before_pub.mp4")
    with open(video, "wb") as f:
        f.write(b"DATA_BEFORE_PUBLICATION" * 50)

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    def transcribe_and_interrupt(*args, **kwargs):
        # Kích hoạt ngắt ngay sau khi transcribe xong
        worker.is_interrupted = True
        return [{"start": 0.0, "end": 2.0, "text": "Transcript"}]

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", side_effect=transcribe_and_interrupt), \
         patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # 0 cache được ghi (kể cả v3 lẫn legacy)
        json_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        assert len(json_caches) == 0


def test_pipeline_worker_phase2_disk_restoration(tmp_path, qapp):
    """Kiểm tra khôi phục cache v3 từ đĩa trong Phase 2: hit nạp đúng, miss để trống, cancel dừng sạch."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video_hit = str(tmp_path / "phase2_hit.mp4")
    video_miss = str(tmp_path / "phase2_miss.mp4")
    with open(video_hit, "wb") as f:
        f.write(b"PHASE2_HIT_PAYLOAD" * 50)
    with open(video_miss, "wb") as f:
        f.write(b"PHASE2_MISS_PAYLOAD" * 50)

    h_hit, p_hit = compute_full_file_sha256(video_hit)
    cache_mgr.save_scan_result(
        video_hit,
        {"clip_dur": 15.0, "raw_subtitles": [{"start": 0.0, "end": 3.0, "text": "Khôi phục Phase 2"}]},
        model_size="small",
        language="vi",
        verified_identity=(h_hit, p_hit)
    )

    # 1. Hit: Nạp thành công từ đĩa vào clip_data_out_cache
    worker_hit = PipelineWorker(
        video_paths=[video_hit],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        phase=2,
        use_cache=True,
        clip_data_cache=None,
        enable_subtitles=True
    )

    mock_load_model = MagicMock()
    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker_hit.run()

        mock_load_model.assert_not_called()
        assert len(worker_hit.clip_data_out_cache) == 1
        assert worker_hit.clip_data_out_cache[0]["raw_subtitles"][0]["text"] == "Khôi phục Phase 2"

    # 2. Miss: File không có cache trên đĩa thì danh sách trống
    worker_miss = PipelineWorker(
        video_paths=[video_miss],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        phase=2,
        use_cache=True,
        clip_data_cache=None,
        enable_subtitles=True
    )

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker_miss.run()

        assert len(worker_miss.clip_data_out_cache) == 0

    # 3. Cancel: Khi có ngắt, dừng sạch không xử lý
    worker_cancel = PipelineWorker(
        video_paths=[video_hit],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        phase=2,
        use_cache=True,
        clip_data_cache=None,
        enable_subtitles=True
    )
    worker_cancel.is_interrupted = True

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker_cancel.run()

        assert len(worker_cancel.clip_data_out_cache) == 0


def test_pipeline_worker_preflight_hash_failure_aborts_immediately_without_loading_model(tmp_path, qapp):
    """Khi băm preflight thất bại (trả về None, None do IO/unstable) mà không phải cancel: dừng fail-safe lập tức trước khi tải model."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "io_fail_video.mp4")
    with open(video, "wb") as f:
        f.write(b"SAMPLE_VIDEO_CONTENT" * 50)

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    finished_events = []
    step_events = []
    worker.finished_signal.connect(lambda s, m: finished_events.append((s, m)))
    worker.step_signal.connect(lambda s, st: step_events.append((s, st)))

    mock_load_model = MagicMock()
    mock_extract = MagicMock()
    mock_transcribe = MagicMock()

    # compute_full_file_sha256 trả về (None, None) do lỗi I/O (không bị ngắt)
    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.ui.auto.auto_window.compute_full_file_sha256", return_value=(None, None)), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Dừng fail-safe ngay tại preflight: 0 model load, 0 extract, 0 transcribe, 0 save
        mock_load_model.assert_not_called()
        mock_extract.assert_not_called()
        mock_transcribe.assert_not_called()

        # Phát tín hiệu lỗi terminal failed
        assert ("speech_to_text", "error") in step_events
        assert (False, "missing_verified_identity") in finished_events

        # Không ghi bất kỳ cache nào
        json_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        assert len(json_caches) == 0



def test_pipeline_worker_stt_only_cache_with_run_cut_true_dispatches_detector_without_whisper(tmp_path, qapp):
    """Khi cache là STT-only (chỉ có transcript) và run_cut=True: 0 Whisper transcribe nhưng detector thật được dispatch, output không bị fullclip giả."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "stt_only_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"STT_ONLY_VIDEO_CONTENT" * 50)

    h, p = compute_full_file_sha256(video)
    # Lưu cache STT-only: KHÔNG có silence_keep_intervals, KHÔNG có cut_segments
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 10.0, "raw_subtitles": [{"start": 1.0, "end": 4.0, "text": "Thoại từ STT"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=True,
        speed_up_silence=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    mock_load_model = MagicMock()
    mock_transcribe = MagicMock()
    mock_detect_silence = MagicMock(return_value=[(1.0, 4.0)])
    mock_extract = MagicMock()

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", mock_detect_silence), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # 1. Không gọi Whisper transcribe vì transcript đã có trong cache
        mock_load_model.assert_not_called()
        mock_transcribe.assert_not_called()

        # 2. Detector được dispatch để tính khoảng lặng
        mock_detect_silence.assert_called_once()

        # 3. Output không phải false fullclip [(0.0, 10.0)]
        assert len(worker.clip_data_out_cache) == 1
        cdata = worker.clip_data_out_cache[0]
        assert cdata["raw_subtitles"][0]["text"] == "Thoại từ STT"
        assert cdata["silence_keep_intervals"] != [(0.0, 10.0)]


def test_pipeline_worker_stt_only_cache_with_run_cut_false_skips_detector(tmp_path, qapp):
    """Khi cache là STT-only nhưng run_cut=False: 0 Whisper transcribe và 0 SilenceDetector (không cần detector)."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "stt_only_nocut.mp4")
    with open(video, "wb") as f:
        f.write(b"STT_ONLY_NOCUT_CONTENT" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {"clip_dur": 8.0, "raw_subtitles": [{"start": 0.0, "end": 3.0, "text": "Thoại không cắt"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    mock_load_model = MagicMock()
    mock_transcribe = MagicMock()
    mock_detect_silence = MagicMock()
    mock_extract = MagicMock()

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", mock_detect_silence), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        mock_load_model.assert_not_called()
        mock_transcribe.assert_not_called()
        mock_detect_silence.assert_not_called()

        assert len(worker.clip_data_out_cache) == 1
        assert worker.clip_data_out_cache[0]["raw_subtitles"][0]["text"] == "Thoại không cắt"


def test_pipeline_worker_full_cache_skips_whisper_and_detector(tmp_path, qapp):
    """Khi cache đã có đầy đủ cả transcript lẫn silence/cut data: 0 Whisper transcribe và 0 SilenceDetector."""
    cache_dir = str(tmp_path / "cache")
    cache_mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "full_cache_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"FULL_CACHE_CONTENT" * 50)

    h, p = compute_full_file_sha256(video)
    cache_mgr.save_scan_result(
        video,
        {
            "clip_dur": 12.0,
            "raw_subtitles": [{"start": 0.0, "end": 5.0, "text": "Full cache transcript"}],
            "silence_keep_intervals": [(0.0, 5.0)],
            "cut_segments": [{"start": 0.0, "end": 5.0, "action": "keep", "speed": 1.0}],
            "speedup_segments": []
        },
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    worker = PipelineWorker(
        video_paths=[video],
        model_size="small",
        language="vi",
        run_cut=True,
        speed_up_silence=False,
        silence_db=-35.0,
        min_duration=0.5,
        use_cache=True,
        enable_subtitles=True
    )

    mock_load_model = MagicMock()
    mock_transcribe = MagicMock()
    mock_detect_silence = MagicMock()
    mock_extract = MagicMock()

    with patch("src.ui.auto.auto_window.ScanCacheManager", return_value=cache_mgr), \
         patch("src.core.transcriber.ResolveTranscriber.load_model", mock_load_model), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", mock_transcribe), \
         patch("src.core.audio.AudioExtractor.extract_audio", mock_extract), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", mock_detect_silence), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        
        worker.run()

        # Đã có full cache -> 0 Whisper transcribe, 0 SilenceDetector
        mock_load_model.assert_not_called()
        mock_transcribe.assert_not_called()
        mock_detect_silence.assert_not_called()

        assert len(worker.clip_data_out_cache) == 1
        cdata = worker.clip_data_out_cache[0]
        assert cdata["raw_subtitles"][0]["text"] == "Full cache transcript"
        assert [tuple(i) for i in cdata["silence_keep_intervals"]] == [(0.0, 5.0)]
