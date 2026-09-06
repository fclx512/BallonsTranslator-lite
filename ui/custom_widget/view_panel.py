from qtpy.QtCore import QCoreApplication, QEvent, Qt, Signal
from qtpy.QtGui import QFontMetrics, QIcon, QMouseEvent
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)

from utils import shared
from utils.config import pcfg

from .scrollbar import ScrollBar
from .widget import Widget

CHEVRON_SIZE = 20
CHEVRON_SIZE_SMALL = 14


def chevron_down():
    return QIcon(r"icons/chevron-down.svg").pixmap(
        CHEVRON_SIZE, CHEVRON_SIZE, mode=QIcon.Mode.Normal
    )


def chevron_right():
    return QIcon(r"icons/chevron-right.svg").pixmap(
        CHEVRON_SIZE, CHEVRON_SIZE, mode=QIcon.Mode.Normal
    )


def chevron_down_small():
    return QIcon(r"icons/chevron-down.svg").pixmap(
        CHEVRON_SIZE_SMALL, CHEVRON_SIZE_SMALL, mode=QIcon.Mode.Normal
    )


def chevron_right_small():
    return QIcon(r"icons/chevron-right.svg").pixmap(
        CHEVRON_SIZE_SMALL, CHEVRON_SIZE_SMALL, mode=QIcon.Mode.Normal
    )


class HidePanelButton(QPushButton):
    pass


class ExpandLabel(Widget):
    clicked = Signal()

    def __init__(
        self, text=None, parent=None, size_type="normal", capsule=False, *args, **kwargs
    ):
        super().__init__(parent=parent, *args, **kwargs)
        self._capsule = capsule
        self.size_type = size_type
        self.textlabel = QLabel(self)
        self.textlabel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        if capsule:
            # Compact bar: no arrow, no hide button, left-aligned text with bg
            self.arrowlabel = None
            self.hidelabel = None
            self.setProperty("capsule", True)
            self.setFixedHeight(20)
            layout = QHBoxLayout(self)
            layout.addWidget(self.textlabel)
            layout.addStretch()
            layout.setContentsMargins(6, 0, 6, 0)
            layout.setSpacing(0)
        else:
            self.arrowlabel = QLabel(self)
            self.arrowlabel.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            font = self.textlabel.font()
            if size_type == "normal":
                font.setPointSizeF(10)
                self.setFixedHeight(26)
                self.arrowlabel.setFixedSize(CHEVRON_SIZE, CHEVRON_SIZE)
            elif size_type == "small":
                font.setPointSizeF(8)
                self.setFixedHeight(20)
                self.arrowlabel.setFixedSize(CHEVRON_SIZE_SMALL, CHEVRON_SIZE_SMALL)
            else:
                raise

            self.textlabel.setFont(font)
            self.hidelabel = HidePanelButton(self)
            self.hidelabel.setVisible(False)

            layout = QHBoxLayout(self)
            layout.addWidget(self.arrowlabel)
            layout.addWidget(self.textlabel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(1)
            layout.addStretch(-1)
            layout.addWidget(self.hidelabel)

        if text is not None:
            self.textlabel.setText(text)

        self.expanded = True
        self.setExpand(True)
        if capsule:
            self.style().unpolish(self)
            self.style().polish(self)

    def enterEvent(self, event) -> None:
        if self.hidelabel is not None:
            self.hidelabel.setVisible(True)
        return super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.hidelabel is not None:
            self.hidelabel.setVisible(False)
        return super().leaveEvent(event)

    def setExpand(self, expand: bool):
        self.expanded = expand
        if self.arrowlabel is not None:
            if expand:
                self.arrowlabel.setPixmap(chevron_down())
            else:
                self.arrowlabel.setPixmap(chevron_right())

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setExpand(not self.expanded)
            # 持久化由宿主 ViewWidget.set_expend_area 按 config_expand_name
            # 落地；此处不可硬编码写死某个面板的展开字段（会串写兄弟面板）
            self.clicked.emit()
        return super().mousePressEvent(e)


class PanelArea(QScrollArea):
    def __init__(
        self,
        panel_name: str,
        config_name: str,
        config_expand_name: str,
        action_name: str = None,
        title_capsule=False,
    ):
        super().__init__()
        self.scrollContent = PanelAreaContent()
        self.scrollContent.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.setWidget(self.scrollContent)
        self.setWidgetResizable(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        ScrollBar(Qt.Orientation.Vertical, self)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ScrollBar(Qt.Orientation.Horizontal, self)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.view_widget = ViewWidget(self, panel_name, title_capsule=title_capsule)
        # 卡片堆栈面板的高度同步重入护栏（_sync_scroll_content_height）
        self._syncing_content_height = False
        self.view_hide_btn_clicked = self.view_widget.view_hide_btn_clicked
        self.expand_changed = self.view_widget.expend_changed
        self.title = self.view_widget.title
        self.setTitle = self.view_widget.setTitle
        self.elidedText = self.view_widget.elidedText
        self.set_expend_area = self.view_widget.set_expend_area

        if action_name is None:
            action_name = panel_name
        self.view_widget.register_view_widget(
            config_name=config_name,
            config_expand_name=config_expand_name,
            action_name=action_name,
        )

    def setContentLayout(self, layout):
        self.scrollContent.setLayout(layout)

    def _sync_scroll_content_height(self, content_layout) -> None:
        """把卡片布局的全高暴露给滚动区（效果面板卡片堆栈用）。

        面板被父级分配小于 sizeHint 的高度时，内容仍保持自然高度。"""
        if self._syncing_content_height:
            return
        self._syncing_content_height = True
        try:
            # 覆盖式滚动条不占布局宽度；viewport 尚未定稿尺寸时 frame 仍可靠
            content_width = max(1, self.width() - 2 * self.frameWidth())
            self.scrollContent.resize(
                content_width,
                max(1, self.scrollContent.height()),
            )
            content_layout.invalidate()
            # 让响应式子控件先拿到最终宽度，再向布局索要该宽度下的高度
            content_layout.activate()
            content_height = (
                content_layout.heightForWidth(content_width)
                if content_layout.hasHeightForWidth()
                else content_layout.sizeHint().height()
            )
            self.scrollContent.setMinimumHeight(content_height)
            self.scrollContent.resize(
                content_width,
                max(content_height, self.viewport().height()),
            )
            content_layout.activate()
            settled_height = (
                content_layout.heightForWidth(content_width)
                if content_layout.hasHeightForWidth()
                else content_layout.sizeHint().height()
            )
            if settled_height != content_height:
                self.scrollContent.setMinimumHeight(settled_height)
                self.scrollContent.resize(
                    content_width,
                    max(settled_height, self.viewport().height()),
                )
                content_layout.activate()
            self.scrollContent.updateGeometry()
            self.updateGeometry()
            self.view_widget.updateGeometry()
            # 隐藏的可伸缩子控件 min height 变化后 QScrollArea 不一定刷新量程
            QCoreApplication.sendEvent(
                self, QEvent(QEvent.Type.LayoutRequest)
            )
        finally:
            self._syncing_content_height = False


class PanelGroupBox(Widget):
    """Card-style container with a title header and content area.

    Unlike QGroupBox, uses a separate QLabel for the title so CSS
    background-color never obscures the title text.
    """

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("CardTitle")
        main_layout.addWidget(self.title_label)

        self.content_area = Widget()
        self.content_area.setObjectName("CardContent")
        self._content_layout = QVBoxLayout(self.content_area)
        self._content_layout.setContentsMargins(8, 4, 8, 6)
        self._content_layout.setSpacing(0)
        main_layout.addWidget(self.content_area)

    def contentLayout(self) -> QVBoxLayout:
        """Return the layout of the content area for adding child widgets."""
        return self._content_layout


class PanelAreaContent(Widget):
    after_resized = Signal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.after_resized.emit()


class ViewWidget(Widget):
    config_name: str = ""
    config_expand_name: str = ""
    action_name: str = ""
    view_hide_btn_clicked = Signal(str)
    expend_changed = Signal()

    def __init__(
        self,
        content_widget: Widget,
        panel_name: str = None,
        parent=None,
        title_size_type="normal",
        title_capsule=False,
        *args,
        **kwargs,
    ):
        super().__init__(parent=parent, *args, **kwargs)

        self.title_label = ExpandLabel(
            panel_name, self, size_type=title_size_type, capsule=title_capsule
        )
        # In capsule mode there is no hidelabel
        if self.title_label.hidelabel is not None:
            self.title_label.hidelabel.clicked.connect(self.on_view_hide_btn_clicked)
        self.content_widget = content_widget

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_label.clicked.connect(self.set_expend_area)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def on_view_hide_btn_clicked(self):
        self.view_hide_btn_clicked.emit(self.config_name)

    def register_view_widget(
        self, config_name: str, config_expand_name: str, action_name: str
    ):
        self.config_name = config_name
        self.config_expand_name = config_expand_name
        self.action_name = action_name
        shared.register_view_widget(self)

    def set_expend_area(self, expend: bool = None, set_config: bool = True):
        if expend is None:
            return self.set_expend_area(self.title_label.expanded)
        if self.title_label.expanded != expend:
            self.title_label.setExpand(expend)
        self.content_widget.setVisible(expend)
        if set_config:
            setattr(pcfg, self.config_expand_name, expend)

    def setTitle(self, text: str):
        self.title_label.textlabel.setText(text)

    def elidedText(self, text: str):
        fm = QFontMetrics(self.title_label.font())
        return fm.elidedText(
            text, Qt.TextElideMode.ElideRight, self.content_widget.width() - 40
        )

    def title(self) -> str:
        return self.title_label.textlabel.text()
