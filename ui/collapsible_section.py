from qtpy.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from qtpy.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from .custom_widget.view_panel import chevron_down, chevron_right
from .custom_widget.widget import Widget


class CollapsibleSection(Widget):
    """A collapsible section with animated expand/collapse.

    Parameters
    ----------
    header_position : str
        ``'top'`` — header on top, content expands downward (default).
        ``'bottom'`` — header on bottom, content expands upward.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title,
        content,
        parent=None,
        duration=350,
        expanded=True,
        header_position="top",
    ):
        super().__init__(parent)
        self._expanded = expanded
        self._content = content
        self._cached_height = 0
        self._header_position = header_position

        self._header_widget = Widget(self)
        self._header_widget.setFixedHeight(26)
        self._header_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_widget.mousePressEvent = self._on_header_clicked

        self._arrow_label = QLabel(self._header_widget)
        self._arrow_label.setFixedSize(20, 20)

        self._title_label = QLabel(title, self._header_widget)
        font = self._title_label.font()
        font.setPointSizeF(12)
        self._title_label.setFont(font)

        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)
        header_layout.addWidget(self._arrow_label)
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        self._content_wrapper = Widget(self)
        wrapper_layout = QVBoxLayout(self._content_wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(2)

        if header_position == "top":
            main_layout.addWidget(self._header_widget)
            main_layout.addWidget(self._content_wrapper)
        else:
            main_layout.addWidget(self._content_wrapper)
            main_layout.addWidget(self._header_widget)

        self._anim = QPropertyAnimation(self._content_wrapper, b"maximumHeight")
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutExpo)
        self._anim.finished.connect(self._on_anim_finished)

        self._update_arrow()
        if not expanded:
            self._content_wrapper.setMaximumHeight(0)
            self._content_wrapper.hide()

    def isExpanded(self):
        return self._expanded

    def setExpanded(self, expand, animated=True):
        if expand == self._expanded:
            return
        self._expanded = expand
        self._update_arrow()

        self._anim.stop()

        if expand:
            self._content_wrapper.show()
            target = (
                self._cached_height
                if self._cached_height > 0
                else self._content.sizeHint().height()
            )
            self._anim.setStartValue(0)
            self._anim.setEndValue(target)
        else:
            self._cached_height = self._content_wrapper.height()
            self._anim.setStartValue(self._cached_height)
            self._anim.setEndValue(0)

        from utils.config import pcfg

        if pcfg.animation_fps < 0:
            self._apply_final_state()
            return

        if animated:
            self._anim.start()
        else:
            self._apply_final_state()

    def _on_anim_finished(self):
        self._apply_final_state()
        self.toggled.emit(self._expanded)

    def _apply_final_state(self):
        if self._expanded:
            self._content_wrapper.setMaximumHeight(16777215)
        else:
            self._content_wrapper.hide()

    def _on_header_clicked(self, event):
        self.setExpanded(not self._expanded)

    def _update_arrow(self):
        if self._expanded:
            self._arrow_label.setPixmap(chevron_down())
        else:
            self._arrow_label.setPixmap(chevron_right())
