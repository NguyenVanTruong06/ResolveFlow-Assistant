"""Lấy frame bằng PTS giải mã, giữ riêng mốc yêu cầu seek và mốc thực tế."""

from pathlib import Path

SAMPLER_VERSION = "pyav-pts-v2-scene-hints"


def group_samples(samples):
    """Gợi ý đổi cảnh từ khác biệt ảnh; không dùng để tự kết luận nội dung."""
    previous = None
    group = 0
    result = []
    for sample in samples:
        signature = sample.get("visual_signature")
        if signature and previous and len(signature) == len(previous):
            before, after = sum(previous) / len(previous) / 255, sum(signature) / len(signature) / 255
            difference = sum(abs(a - b) for a, b in zip(previous, signature)) / len(signature) / 255
            dark_cut = (before > .06 and after < .02) or (after > .06 and before < .02)
            if difference > .22 or dark_cut:
                group += 1
        result.append({**sample, "shot_group": group})
        previous = signature
    return result


class VideoSampler:
    def __init__(self, path):
        import av
        self.container = av.open(str(path))
        try:
            self.stream = self.container.streams.video[0]
            self.stream.thread_type = "AUTO"
            first = next(self.container.decode(self.stream))
            if first.pts is None:
                raise ValueError("Video không có PTS để xác minh mốc nguồn")
            self.origin = float(self.stream.start_time * self.stream.time_base) if self.stream.start_time is not None else float(first.pts * first.time_base)
            self.duration_sec = float(self.stream.duration * self.stream.time_base) if self.stream.duration else float(self.container.duration or 0) / av.time_base
            if self.duration_sec <= 0:
                raise ValueError("Không xác định được thời lượng video")
        except BaseException:
            self.container.close()
            raise

    def close(self):
        self.container.close()

    def sample(self, target, output: Path, cancelled=None, width=640):
        offset = int((target + self.origin) / self.stream.time_base)
        self.container.seek(offset, stream=self.stream, backward=True, any_frame=False)
        for frame in self.container.decode(self.stream):
            if cancelled and cancelled():
                raise InterruptedError("Đã hủy lấy mẫu hình ảnh")
            if frame.pts is None:
                continue
            actual = float(frame.pts * frame.time_base) - self.origin
            if actual + 1e-7 < target:
                continue
            image = frame.to_image()
            image.thumbnail((width, width))
            image.save(output, quality=85)
            return {
                "requested_time_sec": round(target, 6),
                "time_sec": round(actual, 6),
                "timestamp_basis": "decoded_pts",
                "image_path": str(output),
                "visual_signature": list(image.convert("L").resize((16, 9)).tobytes()),
            }
        return None
