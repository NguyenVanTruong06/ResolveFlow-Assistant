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


class StoryReviewState:
    """
    Quản lý trạng thái duyệt và sắp xếp lại các khối kịch bản Story Blocks (Phase Story Review).
    Hỗ trợ: đổi thứ tự (Move Up/Down), Bật/Tắt phân đoạn (Enable/Disable), Hoàn tác (Undo/Redo) và tính toán thời lượng.
    """
    MAX_HISTORY = 200

    def __init__(self, arrangement: Any, blocks: Sequence[Any], target_seconds: float = 60.0):
        self.arrangement = arrangement
        self.items = list(arrangement.items) if arrangement else []
        self.blocks = list(blocks)
        self.blocks_by_id = {b.id: b for b in self.blocks}
        self.target_seconds = target_seconds
        self._initial_state = [item.model_copy() for item in self.items]
        self._undo: List[List[Any]] = []
        self._redo: List[List[Any]] = []

    def _snapshot(self) -> List[Any]:
        return [item.model_copy() for item in self.items]

    def _record_change(self):
        self._undo.append(self._snapshot())
        del self._undo[:-self.MAX_HISTORY]
        self._redo.clear()

    def move_up(self, index: int) -> bool:
        """Di chuyển khối lên vị trí trước đó 1 bậc."""
        if index <= 0 or index >= len(self.items):
            return False
        self._undo.append(self._snapshot())
        self._redo.clear()
        self.items[index - 1], self.items[index] = self.items[index], self.items[index - 1]
        self.arrangement.items = self.items
        return True

    def move_down(self, index: int) -> bool:
        """Di chuyển khối xuống vị trí kế tiếp 1 bậc."""
        if index < 0 or index >= len(self.items) - 1:
            return False
        self._undo.append(self._snapshot())
        self._redo.clear()
        self.items[index], self.items[index + 1] = self.items[index + 1], self.items[index]
        self.arrangement.items = self.items
        return True

    def set_enabled(self, index: int, enabled: bool) -> bool:
        if 0 <= index < len(self.items):
            if self.items[index].enabled == enabled:
                return False
            self._undo.append(self._snapshot())
            self._redo.clear()
            self.items[index].enabled = enabled
            return True
        return False

    def toggle_enabled(self, index: int) -> bool:
        if 0 <= index < len(self.items):
            return self.set_enabled(index, not self.items[index].enabled)
        return False

    def enable_all(self) -> bool:
        self._undo.append(self._snapshot())
        self._redo.clear()
        for it in self.items:
            it.enabled = True
        return True

    def disable_all(self) -> bool:
        self._undo.append(self._snapshot())
        self._redo.clear()
        for it in self.items:
            it.enabled = False
        return True

    def restore_ai_plan(self) -> bool:
        """Khôi phục lại kế hoạch sắp xếp ban đầu của AI."""
        self._undo.append(self._snapshot())
        self._redo.clear()
        self.items = [item.model_copy() for item in self._initial_state]
        self.arrangement.items = self.items
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        current = self._snapshot()
        prev = self._undo.pop()
        self._redo.append(current)
        self.items = prev
        self.arrangement.items = self.items
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        current = self._snapshot()
        next_state = self._redo.pop()
        self._undo.append(current)
        self.items = next_state
        self.arrangement.items = self.items
        return True

    def summary(self) -> Dict[str, Any]:
        enabled_items = [it for it in self.items if getattr(it, "enabled", True)]
        total_sec = sum(max(0.0, it.t1 - it.t0) for it in enabled_items)
        return {
            "enabled_count": len(enabled_items),
            "total_count": len(self.items),
            "total_seconds": round(total_sec, 2),
            "target_seconds": self.target_seconds,
            "difference_to_target": round(total_sec - self.target_seconds, 2)
        }

