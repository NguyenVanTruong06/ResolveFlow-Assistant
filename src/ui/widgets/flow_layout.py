"""Qt layout for tool rows that wrap to the available width."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _arrange(self, rect, *, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        spacing = max(0, self.spacing())
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            width = min(size.width(), max(0, area.width()))
            height = item.heightForWidth(width) if item.hasHeightForWidth() else size.height()
            if x > area.x() and x + width > area.right() + 1:
                x = area.x()
                y += row_height + spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(x, y, width, height))
            x += width + spacing
            row_height = max(row_height, height)
        return y + row_height - rect.y() + margins.bottom()
