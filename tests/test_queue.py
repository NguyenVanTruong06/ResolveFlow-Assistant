import pytest
from src.core.queue import BatchProcessorQueue, BatchJob

def test_batch_job_status() -> None:
    job = BatchJob("video.mp4")
    assert job.status == "pending"
    assert job.progress == 0
    
    job.mark_processing()
    assert job.status == "processing"
    
    job.mark_completed()
    assert job.status == "completed"
    assert job.progress == 100
    
    job.mark_failed("FFmpeg error")
    assert job.status == "failed"
    assert job.error_msg == "FFmpeg error"

def test_queue_operations() -> None:
    queue = BatchProcessorQueue()
    assert len(queue.jobs) == 0
    assert queue.get_progress() == 0.0
    
    job1 = queue.add_video("video1.mp4")
    job2 = queue.add_video("video2.mp4")
    
    assert len(queue.jobs) == 2
    assert len(queue.get_pending_jobs()) == 2
    
    job1.progress = 50
    assert queue.get_progress() == 25.0  # (50 + 0) / 2
    
    job1.mark_completed()
    job2.mark_completed()
    assert queue.get_progress() == 100.0
    
    queue.clear()
    assert len(queue.jobs) == 0
