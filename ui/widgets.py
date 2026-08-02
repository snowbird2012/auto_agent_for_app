"""Reusable UI widgets for the prototype."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def label(text: str, object_name: str = "") -> QLabel:
    item = QLabel(text)
    if object_name:
        item.setObjectName(object_name)
    return item


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, note: str, tone: str = "blue") -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(126)
        colors = {
            "blue": ("#3b82f6", "#122d52"),
            "green": ("#34d399", "#10362f"),
            "orange": ("#f59e0b", "#3a2b13"),
            "pink": ("#f472b6", "#3c2038"),
        }
        accent, bg = colors[tone]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        top.addWidget(label(title, "Muted"))
        top.addStretch()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{accent}; background:{bg}; border-radius:10px; padding:1px 6px;")
        top.addWidget(dot)
        outer.addLayout(top)
        self.value_label = label(value, "Metric")
        outer.addWidget(self.value_label)
        hint = label(note, "Small")
        hint.setStyleSheet(f"color:{accent};")
        outer.addWidget(hint)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(label(title, "PageTitle"))
        if subtitle:
            layout.addWidget(label(subtitle, "Muted"))


class MiniChart(QWidget):
    """Dependency-free decorative line chart used by the dashboard."""

    def __init__(self, values: list[int]) -> None:
        super().__init__()
        self.values = values
        self.setMinimumHeight(175)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(8, 12, -8, -22)
        grid_pen = QPen(QColor("#20314a"), 1)
        for index in range(5):
            y = rect.top() + rect.height() * index / 4
            painter.setPen(grid_pen)
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        if len(self.values) < 2:
            return
        high = max(self.values) or 1
        low = min(self.values)
        span = max(high - low, 1)
        points = []
        for index, value in enumerate(self.values):
            x = rect.left() + rect.width() * index / (len(self.values) - 1)
            y = rect.bottom() - rect.height() * (value - low) / span
            points.append((x, y))

        path = QPainterPath()
        path.moveTo(*points[0])
        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            mid_x = (previous[0] + current[0]) / 2
            path.cubicTo(mid_x, previous[1], mid_x, current[1], current[0], current[1])
        painter.setPen(QPen(QColor("#4b91ff"), 3))
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#80aef8"))
        for x, y in points:
            painter.drawEllipse(int(x - 3), int(y - 3), 6, 6)


class DeviceStatusCard(QFrame):
    def __init__(self, name: str, model: str, status: str, task: str, progress: int) -> None:
        super().__init__()
        self.setObjectName("DeviceCard")
        self.setStyleSheet("QFrame#DeviceCard { background:#0d192b; border:1px solid #1c2d45; border-radius:10px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        phone = QLabel("AD")
        phone.setStyleSheet("font-size:28px; color:#67a2ff;")
        top.addWidget(phone)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(label(name, "SectionTitle"))
        names.addWidget(label(model, "Small"))
        top.addLayout(names)
        top.addStretch()
        pill = label(status, "PillGreen" if status == "运行中" else "PillBlue")
        top.addWidget(pill, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        layout.addSpacing(6)
        layout.addWidget(label(task, "Muted"))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(progress)
        bar.setTextVisible(False)
        layout.addWidget(bar)


def card_layout(margins: int = 16) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(12)
    return frame, layout
