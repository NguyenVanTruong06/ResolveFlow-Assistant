import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from src.core.autocut import seconds_to_timecode

class KeptIntervalAudit(BaseModel):
    """
    Thông tin chi tiết một phân đoạn âm thanh/thoại được giữ lại.
    """
    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    timecode_range: str

class HookTeaserItemAudit(BaseModel):
    """
    Thông tin một phân đoạn được đưa vào Timeline Intro Teaser.
    """
    order: int
    clip_name: str
    video_path: str
    src_in: float
    src_out: float
    duration: float
    timecode_range: str
    reason: str
    score: float
    hook_text: str = ""

class ClipAuditRecord(BaseModel):
    """
    Hồ sơ kiểm toán chi tiết cho từng video nguồn.
    """
    clip_index: int
    clip_name: str
    video_path: str
    original_duration: float
    silent_intervals_detected: int = 0
    kept_intervals: List[KeptIntervalAudit] = Field(default_factory=list)
    final_duration: float = 0.0
    duration_saved: float = 0.0
    percentage_reduced: float = 0.0
    raw_words_count: int = 0
    raw_sentences_count: int = 0
    subtitle_lines_count: int = 0
    split_mode: str = "characters"
    split_limit: int = 42

class OutputFileManifest(BaseModel):
    """
    Thông tin một tệp tin đầu ra được xuất bản.
    """
    category: str
    filename: str
    file_path: str
    description: str

class TimelineManifest(BaseModel):
    """
    Thông tin Timeline được tạo trên DaVinci Resolve.
    """
    timeline_name: str
    description: str
    status: str = "Đã đồng bộ / Tự động tạo"

class ExecutionAuditReporter:
    """
    Bộ động cơ quản lý Nhật ký Kiểm toán & Báo cáo Minh bạch (Execution Audit & Summary Report).
    Tự động ghi nhận toàn bộ thông số định lượng và xuất báo cáo Markdown / Console trực quan.
    """

    def __init__(self, project_name: str = "ResolveFlow_Project"):
        self.project_name = project_name
        self.clip_records: List[ClipAuditRecord] = []
        self.teaser_items: List[HookTeaserItemAudit] = []
        self.output_files: List[OutputFileManifest] = []
        self.timelines: List[TimelineManifest] = []
        self.fps: float = 30.0
        self.validation_warnings: List[str] = []

    def record_validation_warnings(self, warnings: List[str]):
        """
        Ghi nhận danh sách cảnh báo tương thích từ bước Dry-run Validate.
        """
        self.validation_warnings.extend(warnings)

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        Định dạng số giây thành chuỗi thời gian trực quan (MM:SS hoặc HH:MM:SS).
        """
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        if mins >= 60:
            hrs = mins // 60
            mins = mins % 60
            return f"{hrs:02d}:{mins:02d}:{secs:02d}.{ms//100:01d}s"
        return f"{mins:02d}:{secs:02d}.{ms//100:01d}s"

    def record_clip_audit(
        self,
        clip_index: int,
        video_path: str,
        original_duration: float,
        silent_intervals_detected: int,
        keep_intervals: List[tuple],
        raw_subtitles: List[Dict[str, Any]],
        split_subtitles_list: List[Dict[str, Any]],
        split_mode: str,
        split_limit: int,
        fps: float = 30.0
    ) -> ClipAuditRecord:
        """
        Ghi nhận dữ liệu kiểm toán cho một clip video cụ thể.
        """
        self.fps = fps
        clip_name = os.path.basename(video_path)

        # Tính toán các phân đoạn giữ lại
        kept_items = []
        final_dur = 0.0
        for k_idx, (k_start, k_end) in enumerate(keep_intervals, 1):
            dur = max(0.0, k_end - k_start)
            final_dur += dur
            tc_in = seconds_to_timecode(k_start, fps)
            tc_out = seconds_to_timecode(k_end, fps)
            kept_items.append(KeptIntervalAudit(
                index=k_idx,
                start_sec=round(k_start, 3),
                end_sec=round(k_end, 3),
                duration_sec=round(dur, 3),
                timecode_range=f"{tc_in} ➔ {tc_out} ({dur:.2f}s)"
            ))

        if not keep_intervals and original_duration > 0:
            final_dur = original_duration

        dur_saved = max(0.0, original_duration - final_dur)
        pct_reduced = (dur_saved / original_duration * 100.0) if original_duration > 0 else 0.0

        # Thống kê từ & câu
        total_words = 0
        for seg in raw_subtitles:
            words = seg.get("words", [])
            if words:
                total_words += len(words)
            else:
                total_words += len(seg.get("text", "").split())

        record = ClipAuditRecord(
            clip_index=clip_index,
            clip_name=clip_name,
            video_path=video_path,
            original_duration=round(original_duration, 2),
            silent_intervals_detected=silent_intervals_detected,
            kept_intervals=kept_items,
            final_duration=round(final_dur, 2),
            duration_saved=round(dur_saved, 2),
            percentage_reduced=round(pct_reduced, 1),
            raw_words_count=total_words,
            raw_sentences_count=len(raw_subtitles),
            subtitle_lines_count=len(split_subtitles_list),
            split_mode=split_mode,
            split_limit=split_limit
        )
        self.clip_records.append(record)
        return record

    def record_teaser_item(
        self,
        order: int,
        video_path: str,
        src_in: float,
        src_out: float,
        reason: str,
        score: float,
        hook_text: str = "",
        fps: float = 30.0
    ):
        """
        Ghi nhận một phân đoạn đưa vào Intro Teaser.
        """
        clip_name = os.path.basename(video_path)
        dur = max(0.0, src_out - src_in)
        tc_in = seconds_to_timecode(src_in, fps)
        tc_out = seconds_to_timecode(src_out, fps)

        item = HookTeaserItemAudit(
            order=order,
            clip_name=clip_name,
            video_path=video_path,
            src_in=round(src_in, 3),
            src_out=round(src_out, 3),
            duration=round(dur, 2),
            timecode_range=f"{tc_in} ➔ {tc_out} ({dur:.2f}s)",
            reason=reason,
            score=round(score, 1),
            hook_text=hook_text
        )
        self.teaser_items.append(item)

    def add_output_file(self, category: str, file_path: str, description: str):
        """
        Ghi nhận tệp đầu ra đã tạo.
        """
        if os.path.exists(file_path):
            self.output_files.append(OutputFileManifest(
                category=category,
                filename=os.path.basename(file_path),
                file_path=os.path.abspath(file_path),
                description=description
            ))

    def add_timeline(self, timeline_name: str, description: str, status: str = "Đã đồng bộ"):
        """
        Ghi nhận Timeline DaVinci Resolve.
        """
        self.timelines.append(TimelineManifest(
            timeline_name=timeline_name,
            description=description,
            status=status
        ))

    def generate_console_summary(self) -> str:
        """
        Tạo báo cáo tóm tắt định dạng text chuyên nghiệp để in ra GUI Console.
        """
        lines = []
        lines.append("\n" + "=" * 65)
        lines.append("📊 BÁO CÁO MINH BẠCH & NHẬT KÝ THỰC THI (EXECUTION AUDIT)")
        lines.append(f"Dự án: {self.project_name}")
        lines.append("=" * 65)

        # 1. Thống kê Cắt khoảng lặng
        lines.append("\n✂ [1. THỐNG KÊ CẮT KHOẢNG LẶNG (SILENT CUT AUDIT)]")
        total_orig = sum(c.original_duration for c in self.clip_records)
        total_final = sum(c.final_duration for c in self.clip_records)
        total_saved = max(0.0, total_orig - total_final)
        total_pct = (total_saved / total_orig * 100.0) if total_orig > 0 else 0.0

        for cr in self.clip_records:
            lines.append(f" 🎬 Clip {cr.clip_index}: {cr.clip_name}")
            lines.append(f"    - Thời lượng gốc: {self.format_duration(cr.original_duration)} ➔ Sau cắt: {self.format_duration(cr.final_duration)}")
            lines.append(f"    - Đã lọc: {cr.silent_intervals_detected} khoảng lặng | Rút ngắn: {cr.duration_saved:.1f}s ({cr.percentage_reduced}%)")
            if cr.kept_intervals:
                lines.append(f"    - Phân đoạn giữ lại ({len(cr.kept_intervals)} đoạn):")
                for ki in cr.kept_intervals[:4]:
                    lines.append(f"      • Đoạn {ki.index:02d}: {ki.timecode_range}")
                if len(cr.kept_intervals) > 4:
                    lines.append(f"      • ... và {len(cr.kept_intervals) - 4} phân đoạn khác (xem chi tiết trong file .md)")

        lines.append(f" 👉 TỔNG HỢP TOÀN DỰ ÁN: {self.format_duration(total_orig)} ➔ {self.format_duration(total_final)} (Tiết kiệm {total_saved:.1f}s ~ {total_pct:.1f}%)")

        # 2. Thống kê Phụ đề
        total_words = sum(c.raw_words_count for c in self.clip_records)
        total_sentences = sum(c.raw_sentences_count for c in self.clip_records)
        total_sub_lines = sum(c.subtitle_lines_count for c in self.clip_records)
        lines.append("\n📝 [2. THỐNG KÊ PHỤ ĐỀ (SUBTITLE METRICS)]")
        lines.append(f" - Tổng số từ đã bóc băng (Whisper STT): {total_words} từ ({total_sentences} câu thoại thô)")
        if self.clip_records:
            mode_str = "Ký tự" if self.clip_records[0].split_mode == "characters" else "Từ"
            lines.append(f" - Cấu hình ngắt dòng: Tối đa {self.clip_records[0].split_limit} {mode_str}/dòng")
        lines.append(f" - Tổng số dòng/card phụ đề đã tối ưu: {total_sub_lines} cards")

        # 3. Thống kê Intro Teaser (nếu có)
        if self.teaser_items:
            total_teaser_dur = sum(t.duration for t in self.teaser_items)
            lines.append("\n🔥 [3. BÁO CÁO TRÍCH XUẤT INTRO VLOG TEASER]")
            lines.append(f" - Tổng thời lượng Teaser: {total_teaser_dur:.2f}s ({len(self.teaser_items)} clips ghép nối liên tục)")
            for ti in self.teaser_items:
                lines.append(f"   {ti.order:02d}. {ti.clip_name} ➔ Cắt từ {ti.timecode_range} [{ti.reason}]")

        # 4. Danh mục Tệp Đầu Ra & Timeline
        lines.append("\n📁 [4. DANH MỤC TỆP ĐẦU RA & TIMELINE RESOLVE]")
        if self.timelines:
            for tl in self.timelines:
                lines.append(f" 🎬 Timeline: {tl.timeline_name} ({tl.description})")
        if self.output_files:
            for of in self.output_files:
                lines.append(f" 📄 {of.category}: {of.filename}")

        lines.append("=" * 65)
        return "\n".join(lines)

    def export_markdown_report(self, output_report_path: str) -> str:
        """
        Xuất file báo cáo định dạng Markdown chuẩn GitHub Flavored Markdown (.md).
        """
        total_orig = sum(c.original_duration for c in self.clip_records)
        total_final = sum(c.final_duration for c in self.clip_records)
        total_saved = max(0.0, total_orig - total_final)
        total_pct = (total_saved / total_orig * 100.0) if total_orig > 0 else 0.0

        total_words = sum(c.raw_words_count for c in self.clip_records)
        total_sentences = sum(c.raw_sentences_count for c in self.clip_records)
        total_sub_lines = sum(c.subtitle_lines_count for c in self.clip_records)

        lines = [
            f"# 📊 Báo cáo Minh bạch & Nhật ký Thực thi: {self.project_name}",
            "",
            "> [!NOTE]",
            f"> Báo cáo được tạo tự động bởi **ResolveFlow Assistant v4.1** nhằm kiểm toán và minh bạch hóa 100% dữ liệu đã xử lý.",
            ""
        ]

        if self.validation_warnings:
            lines.extend([
                "## ⚠️ Nhật Ký Cảnh Báo Tương Thích (Dry-run Validation)",
                "",
                "Phát hiện các vấn đề tương thích định dạng file nguồn:",
                ""
            ])
            for warn in self.validation_warnings:
                lines.append(f"- [⚠️ Cảnh báo] {warn}")
            lines.append("")

        lines.extend([
            "## 1. 📈 Tổng quan Toàn bộ Dự án",
            "",
            "| Chỉ số | Trước xử lý | Sau xử lý | Mức độ tối ưu / Tiết kiệm |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Tổng thời lượng video** | `{self.format_duration(total_orig)}` | `{self.format_duration(total_final)}` | **Giảm {total_saved:.1f}s ({total_pct:.1f}%)** |",
            f"| **Số lượng video nguồn** | `{len(self.clip_records)} clips` | `{len(self.clip_records)} clips` | `Ghép nối đồng bộ` |",
            f"| **Bóc băng Whisper** | `{total_sentences} câu thô` | `{total_sub_lines} dòng sub` | `{total_words} từ tiếng nói` |",
            "",
            "---",
            "",
            "## 2. ✂ Chi tiết Cắt Khoảng Lặng (Silent Cut Audit)",
            ""
        ])

        for cr in self.clip_records:
            lines.append(f"### 🎬 Clip {cr.clip_index}: `{cr.clip_name}`")
            lines.append(f"- **Đường dẫn tệp:** `{cr.video_path}`")
            lines.append(f"- **Thời lượng:** `{self.format_duration(cr.original_duration)}` ➔ `{self.format_duration(cr.final_duration)}` (Rút ngắn **{cr.percentage_reduced}%** / giảm `{cr.duration_saved:.2f}s`)")
            lines.append(f"- **Số khoảng lặng phát hiện:** `{cr.silent_intervals_detected}`")
            lines.append("")
            lines.append("#### Danh sách các phân đoạn âm thanh giữ lại:")
            lines.append("")
            lines.append("| STT | Mốc bắt đầu (Src In) | Mốc kết thúc (Src Out) | Thời lượng | Timecode CMX3600 |")
            lines.append("| :---: | :---: | :---: | :---: | :---: |")
            for ki in cr.kept_intervals:
                lines.append(f"| {ki.index:02d} | `{ki.start_sec:.3f}s` | `{ki.end_sec:.3f}s` | `{ki.duration_sec:.2f}s` | `{ki.timecode_range}` |")
            if not cr.kept_intervals:
                lines.append(f"| 01 | `0.000s` | `{cr.original_duration:.3f}s` | `{cr.original_duration:.2f}s` | `Giữ nguyên toàn bộ` |")
            lines.append("")

        # 3. Intro Vlog Teaser
        if self.teaser_items:
            total_teaser_dur = sum(t.duration for t in self.teaser_items)
            lines.append("---")
            lines.append("")
            lines.append("## 3. 🔥 Báo cáo Trích xuất Intro Vlog Teaser")
            lines.append(f"- **Tổng thời lượng Teaser:** `{total_teaser_dur:.2f}s`")
            lines.append(f"- **Số phân đoạn ghép nối:** `{len(self.teaser_items)} clips`")
            lines.append("")
            lines.append("| Thứ tự | Clip nguồn | Đoạn trích xuất (Timecode) | Độ dài | Lý do trích xuất & Điểm số | Nội dung câu thoại Hook |")
            lines.append("| :---: | :--- | :---: | :---: | :--- | :--- |")
            for ti in self.teaser_items:
                hook_txt_display = f'"{ti.hook_text}"' if ti.hook_text else "*Cảnh thuần hình ảnh*"
                lines.append(f"| **{ti.order:02d}** | `{ti.clip_name}` | `{ti.timecode_range}` | `{ti.duration}s` | **{ti.reason}** (Score: {ti.score}) | {hook_txt_display} |")
            lines.append("")

        # 4. Phụ đề
        lines.append("---")
        lines.append("")
        lines.append("## 4. 📝 Thống kê Cấu hình & Dòng Phụ đề (Subtitle Metrics)")
        lines.append("")
        lines.append("| Clip | Số câu thoại gốc | Tổng số từ | Dòng phụ đề hoàn chỉnh | Chế độ ngắt câu | Giới hạn |")
        lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
        for cr in self.clip_records:
            mode_lbl = "Số ký tự tối đa" if cr.split_mode == "characters" else "Số từ tối đa"
            lines.append(f"| `{cr.clip_name}` | {cr.raw_sentences_count} | {cr.raw_words_count} | **{cr.subtitle_lines_count}** | {mode_lbl} | `{cr.split_limit}` |")
        lines.append("")

        # 5. Danh mục Tệp & Timeline DaVinci Resolve
        lines.append("---")
        lines.append("")
        lines.append("## 5. 📁 Danh mục Tệp Đầu Ra & Timeline DaVinci Resolve")
        lines.append("")
        if self.timelines:
            lines.append("### 🎬 Timeline trên DaVinci Resolve:")
            for tl in self.timelines:
                lines.append(f"- **`{tl.timeline_name}`**: {tl.description} *({tl.status})*")
            lines.append("")

        if self.output_files:
            lines.append("### 📄 Danh sách Tệp Tin Đầu Ra Cục Bộ:")
            lines.append("| Loại tệp | Tên tệp tin | Đường dẫn tuyệt đối | Mô tả công dụng |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for of in self.output_files:
                lines.append(f"| **{of.category}** | `{of.filename}` | `{of.file_path}` | {of.description} |")
            lines.append("")

        content = "\n".join(lines)
        parent_dir = os.path.dirname(output_report_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_report_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_report_path
