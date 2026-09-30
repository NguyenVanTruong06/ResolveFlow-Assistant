"""
ReviewState - trạng thái duyệt kịch bản Phase 1 cho editor.

Bọc danh sách ProposedSegment (sửa trực tiếp tại chỗ để phần còn lại của pipeline dùng chung)
và cung cấp: thao tác hàng loạt, Hoàn tác / Làm lại, lọc câu cần xem và thống kê thời lượng còn lại.
"""
from typing import Any, Dict, List, Sequence


class ReviewState:
    MAX_HISTORY = 200

    def __init__(self, segments: Sequence[Any]):
        self.segments = list(segments)
        self._undo: List[Dict[int, bool]] = []
        self._redo: List[Dict[int, bool]] = []

    # --- Thay đổi trạng thái ---
    def _apply(self, changes: Dict[int, bool]) -> bool:
        """Áp dụng {row: approved_mới}; chỉ ghi lịch sử nếu thật sự có thay đổi."""
        old = {r: self.segments[r].approved for r, v in changes.items()
               if 0 <= r < len(self.segments) and self.segments[r].approved != v}
        if not old:
            return False
        for r in old:
            self.segments[r].approved = changes[r]
        self._undo.append(old)
        del self._undo[:-self.MAX_HISTORY]
        self._redo.clear()
        return True

    def set_approved(self, rows: Sequence[int], value: bool) -> bool:
        return self._apply({r: value for r in rows})

    def toggle(self, rows: Sequence[int]) -> bool:
        return self._apply({r: not self.segments[r].approved for r in rows if 0 <= r < len(self.segments)})

    def keep_all(self) -> bool:
        return self.set_approved(range(len(self.segments)), True)

    def cut_all(self) -> bool:
        return self.set_approved(range(len(self.segments)), False)

    def invert(self) -> bool:
        return self.toggle(range(len(self.segments)))

    def restore_ai_suggestion(self) -> bool:
        """Đưa toàn bộ về đề xuất gốc của AI (keep -> giữ, cut -> cắt)."""
        return self._apply({i: s.decision == "keep" for i, s in enumerate(self.segments)})

    # --- Hoàn tác / Làm lại ---
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> List[int]:
        """Trả về danh sách hàng bị đổi để UI đồng bộ lại."""
        if not self._undo:
            return []
        old = self._undo.pop()
        redo = {r: self.segments[r].approved for r in old}
        for r, v in old.items():
            self.segments[r].approved = v
        self._redo.append(redo)
        return sorted(old)

    def redo(self) -> List[int]:
        if not self._redo:
            return []
        new = self._redo.pop()
        undo = {r: self.segments[r].approved for r in new}
        for r, v in new.items():
            self.segments[r].approved = v
        self._undo.append(undo)
        return sorted(new)

    # --- Truy vấn ---
    def needs_attention(self, threshold: float) -> List[int]:
        """Câu AI không chắc chắn hoặc AI đề xuất cắt: nơi editor nên xem trước."""
        return [i for i, s in enumerate(self.segments) if s.confidence < threshold or s.decision == "cut"]

    def summary(self) -> Dict[str, float]:
        total = sum(max(0.0, s.end - s.start) for s in self.segments)
        kept = sum(max(0.0, s.end - s.start) for s in self.segments if s.approved)
        return {
            "kept_count": sum(1 for s in self.segments if s.approved),
            "total_count": len(self.segments),
            "kept_seconds": kept,
            "total_seconds": total,
            "saved_percent": round((1 - kept / total) * 100, 1) if total > 0 else 0.0,
        }
