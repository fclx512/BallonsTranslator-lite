"""页码区间控件：两个页码选择框 + 一条带完成度的区间轨（复刻上游）。

复刻自上游 ``ballontranslator/ui/page_range_progress.py``，三件套原样搬过来：
`PageRangeSpinBox`（右侧一对 chevron 步进按钮）、`PageProgressRangeBar`
（轨道上按页填充完成度 + 半透明选区 + 两个圆手柄 + 悬停读出页码/页名）、
`PageRangeProgressWidget`（把两者拼成一行：起止框 + 完成度标签）。

与上游的差异：

* 强调色取主题色（``themeColor()``）而非上游硬编码的蓝——fork 换主题时
  轨道、选区、手柄要跟着走。
* 去掉了 PyQt5 兼容 shim（``getattr(QPainter, 'RenderHint', ...)`` 等），
  本仓库只跑 PyQt6。
* 页码框不做 Blender 式拖拽：它右侧有 chevron 步进按钮，按下区已被按钮
  占用，且页码是离散序号、拖动调值没有意义（上游行为如此）。

区间含义：``(1, 页数)`` 即全部页面，所以调用方不需要「全部页面」开关——
上游的 ``RunPipelineDialog`` 正是这么用的。
"""

from qtpy.QtCore import QPoint, QRect, QRectF, QSignalBlocker, QSize, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.icon_rendering import render_svg_pixmap

from .helper import borderColor, themeColor, widgetBackgroundColor


def themed_icon(filename: str) -> str:
    """惰性解析图标主题路径（避免 ui.misc 早导入环）。"""
    from ui.misc import themed_icon_path

    return themed_icon_path(filename)


class PageRangeSpinBox(QSpinBox):
    """页码选择框：无原生箭头，右端一对 chevron 步进按钮（悬停高亮）。"""

    ICON_SIZE = 12

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMouseTracking(True)
        self._hover_button = ""

    def _button_rects(self):
        button_size = 16
        gap = 1
        right = self.width() - 4
        y = (self.height() - button_size) // 2
        up_rect = QRect(right - button_size, y, button_size, button_size)
        down_rect = QRect(
            up_rect.left() - gap - button_size, y, button_size, button_size
        )
        return up_rect, down_rect

    @staticmethod
    def _event_pos(event) -> QPoint:
        return event.position().toPoint()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        up_rect, down_rect = self._button_rects()
        for name, rect, icon_name in (
            ("down", down_rect, "chevron-down.svg"),
            ("up", up_rect, "chevron-up.svg"),
        ):
            if self._hover_button == name and self.isEnabled():
                hover = QColor(themeColor())
                hover.setAlpha(32)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(hover)
                painter.drawRoundedRect(QRectF(rect), 3, 3)
            pixmap = render_svg_pixmap(
                themed_icon(icon_name),
                self.ICON_SIZE,
                self.ICON_SIZE,
                self.devicePixelRatioF(),
            )
            x = rect.center().x() - self.ICON_SIZE // 2
            y = rect.center().y() - self.ICON_SIZE // 2
            painter.drawPixmap(x, y, pixmap)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self._event_pos(event)
            up_rect, down_rect = self._button_rects()
            if up_rect.contains(pos):
                self.stepUp()
                event.accept()
                return
            if down_rect.contains(pos):
                self.stepDown()
                event.accept()
                return
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = self._event_pos(event)
        up_rect, down_rect = self._button_rects()
        hover_button = (
            "up"
            if up_rect.contains(pos)
            else "down"
            if down_rect.contains(pos)
            else ""
        )
        if hover_button != self._hover_button:
            self._hover_button = hover_button
            self.update()
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_button:
            self._hover_button = ""
            self.update()
        return super().leaveEvent(event)


class PageProgressRangeBar(QWidget):
    """页码轨道：按页填充完成度，叠加可拖拽的闭区间选区与悬停读出。"""

    range_changed = Signal(int, int)

    TRACK_HEIGHT = 5
    TRACK_Y = 18
    HANDLE_RADIUS = 7
    TRACK_SIDE_MARGIN = 0
    HEIGHT = 40

    def __init__(self, page_names, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.page_names = list(page_names)
        self.finished_pages = [False] * len(self.page_names)
        self.start_index = 0
        self.end_index = max(0, len(self.page_names) - 1)
        self.hover_page_index = -1
        self._active_handle = ""
        self._hover_handle_index = -1
        self.setMouseTracking(True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    @property
    def page_count(self) -> int:
        return len(self.page_names)

    @property
    def finished_count(self) -> int:
        return sum(self.finished_pages)

    def sizeHint(self) -> QSize:
        return QSize(280, self.HEIGHT)

    def set_finished_pages(self, finished_pages) -> None:
        finished = [bool(value) for value in finished_pages]
        page_count = self.page_count
        self.finished_pages = (finished + [False] * page_count)[:page_count]
        self.update()

    def set_range(self, start: int, end: int, emit: bool = True) -> None:
        """写入 1 基页码区间（含两端）；越界与倒序在此收口。"""
        if not self.page_names:
            return
        start = max(1, min(int(start), self.page_count))
        end = max(start, min(int(end), self.page_count))
        changed = (start - 1, end - 1) != (self.start_index, self.end_index)
        self.start_index = start - 1
        self.end_index = end - 1
        if changed:
            self.update()
            if emit:
                self.range_changed.emit(start, end)

    def _track_rect(self) -> QRectF:
        margin = max(self.HANDLE_RADIUS + 2, self.TRACK_SIDE_MARGIN)
        return QRectF(
            margin,
            self.TRACK_Y,
            max(1, self.width() - margin * 2),
            self.TRACK_HEIGHT,
        )

    def _page_x(self, page_index: int) -> float:
        track = self._track_rect()
        if not self.page_names:
            return track.left()
        if self.page_count == 1:
            return track.center().x()
        step_width = track.width() / (self.page_count - 1)
        return track.left() + page_index * step_width

    def _page_index_at_x(self, x: float) -> int:
        if not self.page_names:
            return -1
        if self.page_count == 1:
            return 0
        track = self._track_rect()
        normalized = (x - track.left()) / max(1.0, track.width())
        page_index = round(normalized * (self.page_count - 1))
        return max(0, min(page_index, self.page_count - 1))

    def _selection_rect(self, track: QRectF) -> QRectF:
        start_x = self._page_x(self.start_index)
        end_x = self._page_x(self.end_index)
        return QRectF(
            start_x,
            track.top(),
            max(0.0, end_x - start_x),
            track.height(),
        )

    @staticmethod
    def _event_pos(event):
        return event.position()

    def _handle_index_at(self, pos) -> int:
        if not self.page_names:
            return -1
        track_y = self._track_rect().center().y()
        hit_radius = self.HANDLE_RADIUS + 2
        for page_index in {self.start_index, self.end_index}:
            dx = pos.x() - self._page_x(page_index)
            dy = pos.y() - track_y
            if dx * dx + dy * dy <= hit_radius * hit_radius:
                return page_index
        return -1

    def _set_hover_position(self, pos) -> None:
        handle_index = self._handle_index_at(pos)
        if handle_index >= 0:
            hover_index = handle_index
        elif self._track_rect().adjusted(0, -5, 0, 5).contains(pos):
            hover_index = self._page_index_at_x(pos.x())
        else:
            hover_index = -1

        changed = (
            hover_index != self.hover_page_index
            or handle_index != self._hover_handle_index
        )
        self.hover_page_index = hover_index
        self._hover_handle_index = handle_index
        if changed:
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self._track_rect()
        empty_color = self.palette().color(QPalette.ColorRole.Mid)
        empty_color.setAlpha(105)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(empty_color)
        painter.drawRoundedRect(track, self.TRACK_HEIGHT / 2, self.TRACK_HEIGHT / 2)

        if self.page_names:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(
                track, self.TRACK_HEIGHT / 2, self.TRACK_HEIGHT / 2
            )
            painter.save()
            painter.setClipPath(clip_path)
            segment_width = track.width() / self.page_count
            accent = themeColor()
            selection = QColor(accent)
            selection.setAlpha(38)
            painter.fillRect(self._selection_rect(track), selection)
            for index, finished in enumerate(self.finished_pages):
                if not finished:
                    continue
                segment = QRectF(
                    track.left() + index * segment_width,
                    track.top(),
                    segment_width + 0.5,
                    track.height(),
                )
                painter.fillRect(segment, accent)
            painter.restore()

            for index in {self.start_index, self.end_index}:
                center_x = self._page_x(index)
                center_y = track.center().y()
                handle_rect = QRectF(
                    center_x - self.HANDLE_RADIUS,
                    center_y - self.HANDLE_RADIUS,
                    self.HANDLE_RADIUS * 2,
                    self.HANDLE_RADIUS * 2,
                )
                painter.setPen(QPen(borderColor(), 1))
                painter.setBrush(widgetBackgroundColor())
                painter.drawEllipse(handle_rect)

                is_active = (
                    self._active_handle == "overlap"
                    or self._active_handle == "start"
                    and index == self.start_index
                    or self._active_handle == "end"
                    and index == self.end_index
                )
                inner_radius = (
                    3
                    if is_active
                    else 4.5
                    if index == self._hover_handle_index
                    else 3.5
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawEllipse(
                    QRectF(
                        center_x - inner_radius,
                        center_y - inner_radius,
                        inner_radius * 2,
                        inner_radius * 2,
                    )
                )

        if self.hover_page_index >= 0:
            self._paint_hover_info(
                painter,
                track,
                draw_line=(
                    not self._active_handle and self._hover_handle_index < 0
                ),
            )
        painter.end()

    def _paint_hover_info(
        self, painter: QPainter, track: QRectF, draw_line: bool = True
    ) -> None:
        """悬停读出：轨道上方是页名、下方是页码，中间一根指示竖线。"""
        page_index = self.hover_page_index
        x = self._page_x(page_index)
        if draw_line:
            line_color = QColor(themeColor())
            line_color.setAlpha(190)
            painter.setPen(QPen(line_color, 1))
            painter.drawLine(
                int(x), 13, int(x), int(track.bottom() + 13)
            )

        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_color = self.palette().color(QPalette.ColorRole.WindowText)
        text_color.setAlpha(205)
        painter.setPen(text_color)
        page_name = metrics.elidedText(
            self.page_names[page_index],
            Qt.TextElideMode.ElideMiddle,
            min(180, max(60, self.width() // 2)),
        )
        name_width = metrics.horizontalAdvance(page_name)
        name_x = max(2, min(int(x - name_width / 2), self.width() - name_width - 2))
        painter.drawText(name_x, metrics.ascent() + 1, page_name)

        index_text = str(page_index + 1)
        index_width = metrics.horizontalAdvance(index_text)
        painter.drawText(
            int(x - index_width / 2),
            int(track.bottom() + metrics.ascent() + 3),
            index_text,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.page_names:
            return super().mousePressEvent(event)
        pos = self._event_pos(event)
        start_distance = abs(pos.x() - self._page_x(self.start_index))
        end_distance = abs(pos.x() - self._page_x(self.end_index))
        if self.start_index == self.end_index:
            # 两端重合时先记成 overlap，拖动方向决定最后带走哪一个
            self._active_handle = "overlap"
        else:
            self._active_handle = (
                "start" if start_distance <= end_distance else "end"
            )
        self._move_active_handle(self._page_index_at_x(pos.x()))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = self._event_pos(event)
        if self._active_handle:
            self._move_active_handle(self._page_index_at_x(pos.x()))
            event.accept()
            return
        self._set_hover_position(pos)
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._active_handle:
            self._active_handle = ""
            self._set_hover_position(self._event_pos(event))
            self.update()
            event.accept()
            return
        return super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self.hover_page_index = -1
        self._active_handle = ""
        self._hover_handle_index = -1
        self.update()
        return super().leaveEvent(event)

    def _move_active_handle(self, page_index: int) -> None:
        old_range = (self.start_index, self.end_index)
        if self._active_handle == "overlap":
            if page_index < self.start_index:
                self._active_handle = "start"
            elif page_index > self.end_index:
                self._active_handle = "end"
            else:
                self.hover_page_index = self.start_index
                self._hover_handle_index = self.start_index
                self.update()
                return
        if self._active_handle == "start":
            self.start_index = min(page_index, self.end_index)
            self.hover_page_index = self.start_index
            self._hover_handle_index = self.start_index
        elif self._active_handle == "end":
            self.end_index = max(page_index, self.start_index)
            self.hover_page_index = self.end_index
            self._hover_handle_index = self.end_index
        self.update()
        if old_range != (self.start_index, self.end_index):
            self.range_changed.emit(self.start_index + 1, self.end_index + 1)


class PageRangeProgressWidget(QWidget):
    """页码区间行：起止页码框 + 完成度标签 + 下方的区间轨。"""

    range_changed = Signal(int, int)

    def __init__(self, page_names, start: int = 1, end: int = None, parent=None):
        super().__init__(parent)
        self.setObjectName("RunPipelinePageRangeProgress")
        self.page_names = list(page_names)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        range_row = QWidget(self)
        range_row.setObjectName("RunPipelinePageRangeRow")
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(2, 0, 2, 0)
        range_layout.setSpacing(8)
        range_label = QLabel(self.tr("Pages to Run"), range_row)
        range_label.setObjectName("RunPipelineSettingLabel")
        range_layout.addWidget(range_label)

        self.range_start = PageRangeSpinBox(range_row)
        self.range_start.setObjectName("RunPipelineRangeStart")
        self.range_end = PageRangeSpinBox(range_row)
        self.range_end.setObjectName("RunPipelineRangeEnd")
        page_count = len(self.page_names)
        maximum = max(1, page_count)
        saved_end = maximum if end is None else end
        start = max(1, min(int(start), maximum))
        saved_end = max(start, min(int(saved_end), maximum))
        for selector in (self.range_start, self.range_end):
            selector.setRange(1, maximum)
            selector.setEnabled(page_count > 0)
            selector.setFixedWidth(82)
        self.range_start.setValue(start)
        self.range_end.setValue(saved_end)
        range_layout.addWidget(self.range_start)
        range_layout.addWidget(QLabel("-", range_row))
        range_layout.addWidget(self.range_end)
        range_layout.addStretch(1)
        self.progress_label = QLabel(range_row)
        self.progress_label.setObjectName("RunPipelineSettingLabel")
        self.progress_label.setTextFormat(Qt.TextFormat.RichText)
        range_layout.addWidget(self.progress_label)
        layout.addWidget(range_row)

        self.range_bar = PageProgressRangeBar(self.page_names, self)
        self.range_bar.set_range(start, saved_end, emit=False)
        self._update_progress_label()
        layout.addWidget(self.range_bar)

        self.range_start.valueChanged.connect(self._on_start_changed)
        self.range_end.valueChanged.connect(self._on_end_changed)
        self.range_bar.range_changed.connect(self._on_bar_range_changed)

    def set_finished_pages(self, finished_pages) -> None:
        self.range_bar.set_finished_pages(finished_pages)
        self._update_progress_label()

    def range_values(self):
        """当前区间的 1 基起止页码 ``(start, end)``（含两端）。"""
        return self.range_start.value(), self.range_end.value()

    def set_range(self, start: int, end: int) -> None:
        self.range_bar.set_range(start, end)

    def _update_progress_label(self) -> None:
        self.progress_label.setText(
            f'{self.tr("progress")} '
            f'<span style="color: {themeColor().name()};">'
            f"{self.range_bar.finished_count}</span>/{self.range_bar.page_count}"
        )

    def _on_start_changed(self, value: int) -> None:
        if value > self.range_end.value():
            blocker = QSignalBlocker(self.range_end)
            self.range_end.setValue(value)
            del blocker
        self.range_bar.set_range(value, self.range_end.value(), emit=False)
        self.range_changed.emit(value, self.range_end.value())

    def _on_end_changed(self, value: int) -> None:
        if value < self.range_start.value():
            blocker = QSignalBlocker(self.range_start)
            self.range_start.setValue(value)
            del blocker
        self.range_bar.set_range(self.range_start.value(), value, emit=False)
        self.range_changed.emit(self.range_start.value(), value)

    def _on_bar_range_changed(self, start: int, end: int) -> None:
        start_blocker = QSignalBlocker(self.range_start)
        end_blocker = QSignalBlocker(self.range_end)
        self.range_start.setValue(start)
        self.range_end.setValue(end)
        del start_blocker, end_blocker
        self.range_changed.emit(start, end)
