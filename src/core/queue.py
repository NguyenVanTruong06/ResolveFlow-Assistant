import os
from typing import List, Callable, Optional, Any

class BatchJob:
    """
    Đại diện cho một công việc xử lý video đơn lẻ trong hàng đợi.
    """
    def __init__(self, video_path: str):
        self.video_path = video_path
        self.status: str = "pending"  # pending, processing, completed, failed
        self.error_msg: Optional[str] = None
        self.progress: int = 0

    def mark_processing(self):
        self.status = "processing"

    def mark_completed(self):
        self.status = "completed"
        self.progress = 100

    def mark_failed(self, error_msg: str):
        self.status = "failed"
        self.error_msg = error_msg

class BatchProcessorQueue:
    """
    Quản lý hàng đợi xử lý nhiều video đồng thời hoặc nối tiếp.
    """
    def __init__(self):
        self.jobs: List[BatchJob] = []
        self.current_job_index: int = -1

    def add_video(self, video_path: str) -> BatchJob:
        """
        Thêm một video mới vào hàng đợi.
        """
        job = BatchJob(video_path)
        self.jobs.append(job)
        return job

    def clear(self):
        """
        Xóa sạch hàng đợi.
        """
        self.jobs = []
        self.current_job_index = -1

    def get_pending_jobs(self) -> List[BatchJob]:
        return [job for job in self.jobs if job.status == "pending"]

    def get_progress(self) -> float:
        """
        Tính toán tiến độ tổng thể của toàn bộ hàng đợi (0.0 - 100.0).
        """
        if not self.jobs:
            return 0.0
        total_progress = sum(job.progress for job in self.jobs)
        return total_progress / len(self.jobs)
