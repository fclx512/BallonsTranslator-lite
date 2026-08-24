from typing import List, Tuple, Union

import os
import os.path as osp

import cv2
import numpy as np
from qtpy.QtCore import QEvent, QLineF, QPointF, QProcess, QRectF, QSizeF, Qt, QTimer, Signal
from qtpy.QtGui import QBrush, QColor, QCursor, QFontMetrics, QPainter, QPen, QPixmap
from qtpy.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QGraphicsEllipseItem,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from utils.config import DrawPanelConfig, pcfg
from utils.imgproc_utils import enlarge_window
from utils.io_utils import imread, imwrite
from utils.logger import logger
from utils.logger import logger as LOGGER
from utils.message import create_info_dialog
from utils.shared import CONFIG_COMBOBOX_HEIGHT, CONFIG_COMBOBOX_SHORT

from .canvas import Canvas
from .configpanel import InpaintConfigPanel
from .crop_rect_item import CropRectItem
from .custom_widget import ColorPickerLabel, PaintQSlider, SeparatorWidget, Widget
from .drawing_commands import InpaintUndoCommand, StrokeItemUndoCommand
from .funcmaps import get_maskseg_method
from .image_edit import ImageEditMode, PenShape, PixmapItem, StrokeImgItem
from .misc import ndarray2pixmap
from .module_manager import ModuleManager

INPAINT_BRUSH_COLOR = QColor(127, 0, 127, 127)
MAX_PEN_SIZE = 1000
MIN_PEN_SIZE = 1
TOOLNAME_POINT_SIZE = 13

# Glyphs cycled by the canvas "online repair in progress" overlay spinner.
BUSY_SPINNER_CHARS = ("◐", "◓", "◑", "◒")

# Aspect ratios offered for the online-LLM inpaint crop tool. These are the
# union of ratios Meshy's image-to-image endpoint supports across its models.
RATIO_OPTIONS = [
    ("1:1", 1.0),
    ("16:9", 16.0 / 9.0),
    ("9:16", 9.0 / 16.0),
    ("4:3", 4.0 / 3.0),
    ("3:4", 3.0 / 4.0),
    ("3:2", 3.0 / 2.0),
    ("2:3", 2.0 / 3.0),
]


def ratio_from_label(label: str) -> float:
    for lab, ratio in RATIO_OPTIONS:
        if lab == label:
            return ratio
    return 16.0 / 9.0


class DrawToolCheckBox(QCheckBox):
    checked = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stateChanged.connect(self.on_state_changed)

    def mousePressEvent(self, event) -> None:
        if self.isChecked():
            return
        return super().mousePressEvent(event)

    def on_state_changed(self, state: int) -> None:
        if self.isChecked():
            self.checked.emit()


class ToolNameLabel(QLabel):
    def __init__(self, fix_width=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        font = self.font()
        font.setPointSizeF(TOOLNAME_POINT_SIZE)
        fmt = QFontMetrics(font)

        if fix_width is not None:
            self.setFixedWidth(fix_width)
            text_width = fmt.width(self.text())
            if text_width > fix_width * 0.95:
                font_size = TOOLNAME_POINT_SIZE * fix_width * 0.95 / text_width
                font.setPointSizeF(font_size)
        self.setFont(font)


class CropControls(Widget):
    """Ratio-crop control row shared by the brush and box-select panels.

    Bundles the aspect-ratio combo, the crop-mode toggle and the ``Inpaint``
    button that dispatches the cropped region to an online LLM inpainter.  A
    single canonical crop state lives in ``DrawingPanel``; both panel instances
    are kept in sync so switching tools never loses the user's crop.
    """

    cropRatioChanged = Signal(str)
    cropModeChanged = Signal(bool)
    inpaintClicked = Signal()
    clearMaskClicked = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.llm_active = False

        self.ratio_combo = QComboBox(self)
        for label, _ratio in RATIO_OPTIONS:
            self.ratio_combo.addItem(label)
        self.ratio_combo.currentTextChanged.connect(self._on_ratio_changed)
        self.mode_check = QCheckBox(self.tr("Crop mode"))
        self.mode_check.toggled.connect(self._on_mode_changed)
        self.inpaint_btn = QPushButton(self.tr("Inpaint"))
        self.inpaint_btn.clicked.connect(self.inpaintClicked.emit)
        self.clear_mask_btn = QPushButton(self.tr("Clear mask"))
        self.clear_mask_btn.setToolTip(
            self.tr("Erase every mask you have drawn inside the crop.")
        )
        self.clear_mask_btn.clicked.connect(self.clearMaskClicked.emit)

        row = QHBoxLayout()
        row.addWidget(ToolNameLabel(100, self.tr("Crop Ratio")))
        row.addWidget(self.ratio_combo, 1)
        row.addWidget(self.mode_check)

        button_row = QHBoxLayout()
        button_row.addWidget(self.inpaint_btn)
        button_row.addWidget(self.clear_mask_btn)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        layout.addLayout(button_row)
        # Per-model aspect-ratio support reference, shown alongside the crop
        # controls for the online image models (Meshy family) that the ratio-crop
        # tool talks to. Lives on the shared CropControls so it appears in both
        # the brush (InpaintPanel) and box-select (RectPanel) panels.
        self.aspect_note = QLabel(
            self.tr("Aspect ratios: 1:1 on every model. gpt-image-2 also supports 3:2 and 2:3. Other models also support 16:9, 9:16, 4:3 and 3:4.")
        )
        self.aspect_note.setWordWrap(True)
        self.aspect_note.setObjectName("InpaintAspectNote")
        layout.addWidget(self.aspect_note)
        layout.setSpacing(14)

        self._update_btn_visibility()

    # ── public API ──

    def ratio(self) -> str:
        return self.ratio_combo.currentText()

    def mode(self) -> bool:
        return self.mode_check.isChecked()

    def set_ratio(self, label: str):
        idx = self.ratio_combo.findText(label)
        if idx >= 0:
            self.ratio_combo.blockSignals(True)
            self.ratio_combo.setCurrentIndex(idx)
            self.ratio_combo.blockSignals(False)

    def set_mode(self, checked: bool):
        self.mode_check.blockSignals(True)
        self.mode_check.setChecked(bool(checked))
        self.mode_check.blockSignals(False)
        self._update_btn_visibility()

    def set_llm_active(self, active: bool):
        self.llm_active = bool(active)
        self.setVisible(self.llm_active)
        self._update_btn_visibility()

    # ── internal ──

    def _on_ratio_changed(self, label: str):
        self.cropRatioChanged.emit(label)

    def _on_mode_changed(self, checked: bool):
        self._update_btn_visibility()
        self.cropModeChanged.emit(bool(checked))

    def _update_btn_visibility(self):
        self.inpaint_btn.setVisible(self.llm_active)
        self.clear_mask_btn.setVisible(self.llm_active)


class InpaintPanel(Widget):
    thicknessChanged = Signal(int)
    cropRatioChanged = Signal(str)
    cropModeChanged = Signal(bool)
    inpaintClicked = Signal()
    clearMaskClicked = Signal()
    llmActiveChanged = Signal(bool)

    def __init__(self, inpainter_panel: InpaintConfigPanel, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.thicknessSlider = PaintQSlider()
        self.thicknessSlider.setRange(MIN_PEN_SIZE, MAX_PEN_SIZE)
        self.thicknessSlider.valueChanged.connect(self.on_thickness_changed)
        self.thicknessSlider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        thickness_layout = QHBoxLayout()
        thickness_label = ToolNameLabel(100, self.tr("Thickness"))
        thickness_layout.addWidget(thickness_label)
        thickness_layout.addWidget(self.thicknessSlider)
        thickness_layout.setSpacing(10)

        shape_label = ToolNameLabel(100, self.tr("Shape"))
        self.shapeCombobox = QComboBox(self)
        self.shapeCombobox.addItems(
            [
                self.tr("Circle"),
                self.tr("Rectangle"),
            ]
        )
        self.shapeChanged = self.shapeCombobox.currentIndexChanged
        shape_layout = QHBoxLayout()
        shape_layout.addWidget(shape_label)
        shape_layout.addWidget(self.shapeCombobox)

        self.inpaint_layout = inpaint_layout = QHBoxLayout()
        inpaint_layout.addWidget(ToolNameLabel(100, self.tr("Inpainter")))
        self.inpainter_panel = inpainter_panel

        # ── Online-LLM ratio-crop tool (shared with the box-select panel) ──
        self.crop_controls = CropControls()
        self.crop_controls.cropRatioChanged.connect(self.cropRatioChanged.emit)
        self.crop_controls.cropModeChanged.connect(self.cropModeChanged.emit)
        self.crop_controls.inpaintClicked.connect(self.inpaintClicked.emit)
        self.crop_controls.clearMaskClicked.connect(self.clearMaskClicked.emit)

        # Brush-specific body (thickness / shape) — hidden while the ratio-crop
        # mode is active so the panel shows one unambiguous operation at a time.
        self.brush_widget = Widget()
        brush_layout = QVBoxLayout(self.brush_widget)
        brush_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        brush_layout.addLayout(thickness_layout)
        brush_layout.addLayout(shape_layout)
        brush_layout.setSpacing(14)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(inpaint_layout)
        layout.addWidget(self.brush_widget)
        layout.addWidget(self.crop_controls)
        layout.setSpacing(14)

        self._llm_active = False
        self.inpainter_panel.module_changed.connect(self._on_inpainter_changed)
        self._on_inpainter_changed(self.inpainter_panel.module_combobox.currentText())

    # ── inpainter / crop state ──

    def current_inpainter(self) -> str:
        return self.inpainter_panel.module_combobox.currentText()

    def crop_ratio(self) -> str:
        return self.crop_controls.ratio()

    def crop_mode(self) -> bool:
        return self.crop_controls.mode()

    def set_crop_state(self, ratio_label: str, crop_mode: bool):
        self.crop_controls.set_ratio(ratio_label)
        self.crop_controls.set_mode(bool(crop_mode))

    def _on_inpainter_changed(self, name: str):
        self._llm_active = name == "LLMInpaint"
        self.llmActiveChanged.emit(self._llm_active)
        self._update_crop_controls()

    def _update_crop_controls(self):
        self.crop_controls.set_llm_active(self._llm_active)

    def set_crop_mode_active(self, active: bool):
        """Hide the brush body while the ratio-crop mode is active."""
        self.brush_widget.setVisible(not active)

    def on_thickness_changed(self):
        if self.thicknessSlider.hasFocus():
            self.thicknessChanged.emit(self.thicknessSlider.value())

    def showEvent(self, e) -> None:
        self.inpaint_layout.addWidget(self.inpainter_panel.module_combobox)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self.inpaint_layout.removeWidget(self.inpainter_panel.module_combobox)
        return super().hideEvent(e)

    @property
    def shape(self):
        return self.shapeCombobox.currentIndex()


class PenConfigPanel(Widget):
    thicknessChanged = Signal(int)
    colorChanged = Signal(list)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.thicknessSlider = PaintQSlider()
        self.thicknessSlider.setRange(MIN_PEN_SIZE, MAX_PEN_SIZE)
        self.thicknessSlider.valueChanged.connect(self.on_thickness_changed)
        self.thicknessSlider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.alphaSlider = PaintQSlider()
        self.alphaSlider.setRange(0, 255)
        self.alphaSlider.setValue(255)
        self.alphaSlider.valueChanged.connect(self.on_alpha_changed)

        self.colorPicker = ColorPickerLabel()
        self.colorPicker.colorChanged.connect(self.on_color_changed)

        color_label = ToolNameLabel(None, self.tr("Color"))
        alpha_label = ToolNameLabel(None, self.tr("Alpha"))
        color_layout = QHBoxLayout()
        color_layout.addWidget(color_label)
        color_layout.addWidget(self.colorPicker)
        color_layout.addWidget(alpha_label)
        color_layout.addWidget(self.alphaSlider)

        thickness_layout = QHBoxLayout()
        thickness_label = ToolNameLabel(100, self.tr("Thickness"))
        thickness_layout.addWidget(thickness_label)
        thickness_layout.addWidget(self.thicknessSlider)
        thickness_layout.setSpacing(10)

        shape_label = ToolNameLabel(100, self.tr("Shape"))
        self.shapeCombobox = QComboBox(self)
        self.shapeCombobox.addItems(
            [
                self.tr("Circle"),
                self.tr("Rectangle"),
            ]
        )
        self.shapeChanged = self.shapeCombobox.currentIndexChanged
        shape_layout = QHBoxLayout()
        shape_layout.addWidget(shape_label)
        shape_layout.addWidget(self.shapeCombobox)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(color_layout)
        layout.addLayout(thickness_layout)
        layout.addLayout(shape_layout)
        layout.setSpacing(20)

    def on_thickness_changed(self):
        if self.thicknessSlider.hasFocus():
            self.thicknessChanged.emit(self.thicknessSlider.value())

    def on_alpha_changed(self):
        color = self.colorPicker.rgba()
        color = [color[0], color[1], color[2], self.alphaSlider.value()]
        self.colorPicker.setPickerColor(color)
        self.colorChanged.emit(color)

    def on_color_changed(self):
        color = self.colorPicker.rgba()
        color = [color[0], color[1], color[2], self.alphaSlider.value()]
        self.colorChanged.emit(color)

    @property
    def shape(self):
        return self.shapeCombobox.currentIndex()


class RectPanel(Widget):
    dilate_ksize_changed = Signal()
    method_changed = Signal(int)
    delete_btn_clicked = Signal()
    inpaint_btn_clicked = Signal()
    cropRatioChanged = Signal(str)
    cropModeChanged = Signal(bool)
    cropInpaintClicked = Signal()
    clearMaskClicked = Signal()

    def __init__(self, inpainter_panel: InpaintConfigPanel, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.dilate_label = ToolNameLabel(100, self.tr("Dilate"))
        self.dilate_slider = PaintQSlider()
        self.dilate_slider.setRange(0, 100)
        self.dilate_slider.valueChanged.connect(self.dilate_ksize_changed)
        self.methodComboBox = QComboBox()
        self.methodComboBox.setFixedHeight(CONFIG_COMBOBOX_HEIGHT)
        self.methodComboBox.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.methodComboBox.addItems(
            [self.tr("method 1"), self.tr("method 2"), self.tr("Use Existing Mask")]
        )
        self.methodComboBox.activated.connect(self.on_inpaint_seg_method_changed)
        self.autoChecker = QCheckBox(self.tr("Auto"))
        self.autoChecker.setToolTip(self.tr("run inpainting automatically."))
        self.autoChecker.stateChanged.connect(self.on_auto_changed)
        self.inpaint_btn = QPushButton(self.tr("Inpaint"))
        self.inpaint_btn.setToolTip(self.tr("Space"))
        self.inpaint_btn.clicked.connect(self.inpaint_btn_clicked)
        self.delete_btn = QPushButton(self.tr("Delete"))
        self.delete_btn.setToolTip(self.tr("Ctrl+D"))
        self.delete_btn.clicked.connect(self.delete_btn_clicked)
        self.btnlayout = QHBoxLayout()
        self.btnlayout.addWidget(self.inpaint_btn)
        self.btnlayout.addWidget(self.delete_btn)

        self.inpaint_layout = inpaint_layout = QHBoxLayout()
        inpaint_layout.addWidget(ToolNameLabel(100, self.tr("Inpainter")))
        self.inpainter_panel = inpainter_panel

        glayout = QGridLayout()
        glayout.addWidget(self.dilate_label, 0, 0)
        glayout.addWidget(self.dilate_slider, 0, 1)
        glayout.addWidget(self.autoChecker, 1, 0)
        glayout.addWidget(self.methodComboBox, 1, 1)

        # Box-select body (mask controls + Inpaint/Delete) — hidden while the
        # ratio-crop mode is active so the panel shows one unambiguous operation
        # at a time (2026-08-24).
        self.box_select_widget = Widget()
        box_layout = QVBoxLayout(self.box_select_widget)
        box_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        box_layout.addLayout(glayout)
        box_layout.addLayout(self.btnlayout)
        box_layout.setSpacing(8)

        # ── Online-LLM ratio-crop tool (shared with the brush panel) ──
        self.crop_controls = CropControls()
        self.crop_controls.cropRatioChanged.connect(self.cropRatioChanged.emit)
        self.crop_controls.cropModeChanged.connect(self.cropModeChanged.emit)
        self.crop_controls.inpaintClicked.connect(self.cropInpaintClicked.emit)
        self.crop_controls.clearMaskClicked.connect(self.clearMaskClicked.emit)
        self.inpainter_panel.module_changed.connect(self._on_inpainter_changed)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(inpaint_layout)
        layout.addWidget(self.box_select_widget)
        layout.addWidget(self.crop_controls)
        layout.setSpacing(14)

        self._on_inpainter_changed(self.inpainter_panel.module_combobox.currentText())

    def _on_inpainter_changed(self, name: str):
        self.crop_controls.set_llm_active(name == "LLMInpaint")

    def set_crop_mode_active(self, active: bool):
        """Hide the box-select body while the ratio-crop mode is active."""
        self.box_select_widget.setVisible(not active)

    def showEvent(self, e) -> None:
        self.inpaint_layout.addWidget(self.inpainter_panel.module_combobox)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self.inpaint_layout.removeWidget(self.inpainter_panel.module_combobox)
        return super().hideEvent(e)

    def on_inpaint_seg_method_changed(self):
        pcfg.drawpanel.rectool_method = self.methodComboBox.currentIndex()

    def on_auto_changed(self):
        if self.autoChecker.isChecked():
            self.inpaint_btn.hide()
            self.delete_btn.hide()
            pcfg.drawpanel.rectool_auto = True
        else:
            pcfg.drawpanel.rectool_auto = False
            self.inpaint_btn.show()
            self.delete_btn.show()

    def auto(self) -> bool:
        return self.autoChecker.isChecked()

    def post_process_mask(self, mask: np.ndarray) -> np.ndarray:
        if mask is None:
            return None
        ksize = self.dilate_slider.value()
        if ksize == 0:
            return mask
        element = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * ksize + 1, 2 * ksize + 1), (ksize, ksize)
        )
        return cv2.dilate(mask, element)


class DrawingPanel(Widget):
    scale_tool_pos: QPointF = None

    def __init__(
        self, canvas: Canvas, inpainter_panel: InpaintConfigPanel, *args, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.module_manager: ModuleManager = None
        self.canvas = canvas
        self.inpaint_stroke: StrokeImgItem = None
        self.rect_inpaint_dict: dict = None
        self.inpaint_mask_array: np.ndarray = None
        self.extracted_imask_array: np.ndarray = None

        border_pen = QPen(INPAINT_BRUSH_COLOR, 3, Qt.PenStyle.DashLine)
        self.inpaint_mask_item: PixmapItem = PixmapItem(border_pen)
        self.scale_circle = QGraphicsEllipseItem()

        canvas.finish_painting.connect(self.on_finish_painting)
        canvas.finish_erasing.connect(self.on_finish_erasing)
        canvas.ctrl_relesed.connect(self.on_canvasctrl_released)
        canvas.begin_scale_tool.connect(self.on_begin_scale_tool)
        canvas.scale_tool.connect(self.on_scale_tool)
        canvas.end_scale_tool.connect(self.on_end_scale_tool)
        canvas.scalefactor_changed.connect(self.on_canvas_scalefactor_changed)
        canvas.end_create_rect.connect(self.on_end_create_rect)

        self.currentTool: DrawToolCheckBox = None
        self.handTool = DrawToolCheckBox()
        self.handTool.setObjectName("DrawHandTool")
        self.handTool.checked.connect(self.on_use_handtool)
        self.handTool.stateChanged.connect(self.on_handchecker_changed)
        self.inpaintTool = DrawToolCheckBox()
        self.inpaintTool.setObjectName("DrawInpaintTool")
        self.inpaintTool.checked.connect(self.on_use_inpainttool)
        self.inpaintConfigPanel = InpaintPanel(inpainter_panel)
        self.inpaintConfigPanel.thicknessChanged.connect(self.setInpaintToolWidth)
        self.inpaintConfigPanel.shapeChanged.connect(self.setInpaintShape)
        self.inpaintConfigPanel.cropRatioChanged.connect(self._on_crop_ratio_changed)
        self.inpaintConfigPanel.cropModeChanged.connect(self._on_crop_mode_changed)
        self.inpaintConfigPanel.inpaintClicked.connect(self.runInpaint)
        self.inpaintConfigPanel.llmActiveChanged.connect(self._on_llm_active_changed)
        self.inpaintConfigPanel.clearMaskClicked.connect(self._on_clear_crop_mask)
        self.crop_rect_item = CropRectItem(parent=self.canvas.baseLayer)
        self.crop_rect_item.setVisible(False)
        self.crop_rect_item.on_released = self._on_crop_rect_released
        self._crop_ratio = 16.0 / 9.0
        self._crop_ratio_label = "16:9"
        self._crop_mode = False
        self._crop_active = False
        # Whether a crop has been established (so the rect stays visible as the
        # generation range even after crop mode is closed) — set on first LLM
        # use and cleared when the inpainter is not LLM or the page changes.
        self._crop_setup = False
        # Accumulated user mask (full-image uint8 0/255) built from brush strokes
        # and box-selects, clipped to the crop. Consumed by the one-shot crop
        # inpaint; reset after dispatch / page change / Delete.
        self._crop_mask_array: np.ndarray = None

        # Non-blocking "online repair in progress" indicator over the canvas.
        # Mouse-transparent so it never blocks canvas interaction; only shown
        # while an online-LLM repair is in flight, then replaced by a short
        # "finished" confirmation.
        self._busy_overlay_shown = False
        self._busy_spinner_index = 0
        self._busy_overlay = QLabel(self.canvas.gv.viewport())
        self._busy_overlay.setObjectName("CanvasBusyOverlay")
        self._busy_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._busy_overlay.setFixedWidth(220)
        self._busy_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._busy_overlay.setStyleSheet(
            "#CanvasBusyOverlay { background-color: rgba(20,20,20,190);"
            " color: #fff; border-radius: 8px; padding: 8px 14px; font-size: 13px; }"
        )
        self._busy_overlay.hide()
        self._busy_spinner_timer = QTimer(self)
        self._busy_spinner_timer.setInterval(140)
        self._busy_spinner_timer.timeout.connect(self._on_busy_spinner_tick)
        self.canvas.gv.viewport().installEventFilter(self)

        self.rectTool = DrawToolCheckBox()
        self.rectTool.setObjectName("DrawRectTool")
        self.rectTool.checked.connect(self.on_use_recttool)
        self.rectTool.stateChanged.connect(self.on_rectchecker_changed)
        self.rectPanel = RectPanel(inpainter_panel)
        self.rectPanel.inpaint_btn_clicked.connect(self.on_rect_inpaintbtn_clicked)
        self.rectPanel.delete_btn_clicked.connect(self.on_rect_deletebtn_clicked)
        self.rectPanel.dilate_ksize_changed.connect(self.on_rectool_ksize_changed)
        self.rectPanel.cropRatioChanged.connect(self._on_crop_ratio_changed)
        self.rectPanel.cropModeChanged.connect(self._on_crop_mode_changed)
        self.rectPanel.cropInpaintClicked.connect(self.runInpaint)
        self.rectPanel.clearMaskClicked.connect(self._on_clear_crop_mask)

        self.penTool = DrawToolCheckBox()
        self.penTool.setObjectName("DrawPenTool")
        self.penTool.checked.connect(self.on_use_pentool)
        self.penConfigPanel = PenConfigPanel()
        self.penConfigPanel.thicknessChanged.connect(self.setPenToolWidth)
        self.penConfigPanel.colorChanged.connect(self.setPenToolColor)
        self.penConfigPanel.shapeChanged.connect(self.setPenShape)

        toolboxlayout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        toolboxlayout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        toolboxlayout.addWidget(self.handTool)
        toolboxlayout.addWidget(self.inpaintTool)
        toolboxlayout.addWidget(self.penTool)
        toolboxlayout.addWidget(self.rectTool)

        self.canvas.painting_pen = self.pentool_pen = QPen(
            Qt.GlobalColor.black,
            1,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        self.canvas.erasing_pen = self.erasing_pen = QPen(
            Qt.GlobalColor.black,
            1,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        self.inpaint_pen = QPen(
            INPAINT_BRUSH_COLOR,
            1,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

        self.toolConfigStackwidget = QStackedWidget()
        self.toolConfigStackwidget.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum
        )
        self.toolConfigStackwidget.addWidget(self.inpaintConfigPanel)
        self.toolConfigStackwidget.addWidget(self.penConfigPanel)
        self.toolConfigStackwidget.addWidget(self.rectPanel)

        self.maskTransperancySlider = PaintQSlider()
        self.maskTransperancySlider.valueChanged.connect(
            self.canvas.setMaskTransparencyBySlider
        )
        masklayout = QHBoxLayout()
        masklayout.addWidget(ToolNameLabel(130, self.tr("Mask Opacity")))
        masklayout.addWidget(self.maskTransperancySlider)

        layout = QVBoxLayout(self)
        layout.addLayout(toolboxlayout)
        layout.addWidget(SeparatorWidget())
        layout.addWidget(self.toolConfigStackwidget)
        layout.addWidget(SeparatorWidget())
        layout.addLayout(masklayout)
        layout.addWidget(SeparatorWidget())
        ps_layout = QHBoxLayout()
        self.psEditBtn = QPushButton(self.tr("Edit in Photoshop"))
        self.psEditBtn.clicked.connect(self.on_edit_in_photoshop)
        self.psRefreshBtn = QPushButton(self.tr("Refresh from Photoshop"))
        self.psRefreshBtn.clicked.connect(self.on_refresh_from_photoshop)
        ps_layout.addWidget(self.psEditBtn)
        ps_layout.addWidget(self.psRefreshBtn)
        layout.addLayout(ps_layout)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._sync_crop_controls()
        self._update_crop_active()

    def setCurrentToolByName(self, tool_name: str):
        try:
            set_method = f"on_use_{tool_name}tool"
            set_method = getattr(self, set_method)
            set_method()
            if self.currentTool is not None:
                self.currentTool.setChecked(True)
        except Exception:
            LOGGER.error(f"{set_method} not found in drawing panel")

    def shortcutSetCurrentToolByName(self, tool_name: str):
        if self.isVisible():
            self.setCurrentToolByName(tool_name)

    def setShortcutTip(self, tool_name: str, shortcut: str):
        try:
            tool = f"{tool_name}Tool"
            tool: QStackedWidget = getattr(self, tool)
            tool.setToolTip(f"{shortcut}")
        except Exception:
            LOGGER.error(f"{tool} not found in drawing panel")

    def initDLModule(self, module_manager: ModuleManager):
        self.module_manager = module_manager
        module_manager.canvas_inpaint_finished.connect(self.on_inpaint_finished)
        module_manager.inpaint_thread.inpaint_failed.connect(self.on_inpaint_failed)

    def setInpaintToolWidth(self, width):
        self.inpaint_pen.setWidthF(width)
        pcfg.drawpanel.inpainter_width = width
        if self.isVisible():
            self.setInpaintCursor()

    def setInpaintShape(self, shape: int):
        self.setInpaintCursor()
        pcfg.drawpanel.inpainter_shape = shape
        self.canvas.painting_shape = shape

    def setPenToolWidth(self, width):
        self.pentool_pen.setWidthF(width)
        self.erasing_pen.setWidthF(width)
        pcfg.drawpanel.pentool_width = self.pentool_pen.widthF()
        if self.isVisible():
            self.setPenCursor()

    def setPenToolColor(self, color: Union[QColor, Tuple, List]):
        if not isinstance(color, QColor):
            color = QColor(*[max(0, min(255, int(c))) for c in color])
        self.pentool_pen.setColor(color)
        pcfg.drawpanel.pentool_color = [
            color.red(),
            color.green(),
            color.blue(),
            color.alpha(),
        ]
        if self.isVisible():
            self.setPenCursor()
        self.penConfigPanel.colorPicker.setPickerColor(color)
        self.penConfigPanel.alphaSlider.setValue(color.alpha())

    def setPenShape(self, shape: int):
        self.setPenCursor()
        self.canvas.painting_shape = shape
        pcfg.drawpanel.pentool_shape = shape

    def on_use_handtool(self) -> None:
        if self.currentTool is not None and self.currentTool != self.handTool:
            self.currentTool.setChecked(False)
        self.currentTool = self.handTool
        pcfg.drawpanel.current_tool = ImageEditMode.HandTool
        self.canvas.clear_canvas_cursor()
        self.canvas.gv.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._update_crop_active()

    def on_use_inpainttool(self) -> None:
        if self.currentTool is not None and self.currentTool != self.inpaintTool:
            self.currentTool.setChecked(False)
        self.currentTool = self.inpaintTool
        pcfg.drawpanel.current_tool = ImageEditMode.InpaintTool
        self.canvas.painting_pen = self.inpaint_pen
        self.canvas.erasing_pen = self.inpaint_pen
        self.canvas.painting_shape = self.inpaintConfigPanel.shape
        self.toolConfigStackwidget.setCurrentWidget(self.inpaintConfigPanel)
        if self.isVisible():
            self.canvas.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setInpaintCursor()
        self._update_crop_active()

    def on_use_pentool(self) -> None:
        if self.currentTool is not None and self.currentTool != self.penTool:
            self.currentTool.setChecked(False)
        self.currentTool = self.penTool
        pcfg.drawpanel.current_tool = ImageEditMode.PenTool
        self.canvas.painting_pen = self.pentool_pen
        self.canvas.painting_shape = self.penConfigPanel.shape
        self.canvas.erasing_pen = self.erasing_pen
        self.toolConfigStackwidget.setCurrentWidget(self.penConfigPanel)
        if self.isVisible():
            self.canvas.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setPenCursor()
        self._update_crop_active()

    def on_use_recttool(self) -> None:
        if self.currentTool is not None and self.currentTool != self.rectTool:
            self.currentTool.setChecked(False)
        self.currentTool = self.rectTool
        pcfg.drawpanel.current_tool = ImageEditMode.RectTool
        self.toolConfigStackwidget.setCurrentWidget(self.rectPanel)
        self.canvas.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCrossCursor()
        self._update_crop_active()

    def set_config(self, config: DrawPanelConfig):
        self.setPenToolWidth(config.pentool_width)
        self.setPenToolColor(config.pentool_color)
        self.penConfigPanel.thicknessSlider.setValue(int(config.pentool_width))
        self.penConfigPanel.shapeCombobox.setCurrentIndex(config.pentool_shape)

        self.setInpaintToolWidth(config.inpainter_width)
        self.inpaintConfigPanel.thicknessSlider.setValue(int(config.inpainter_width))
        self.inpaintConfigPanel.shapeCombobox.setCurrentIndex(config.inpainter_shape)
        self._set_crop_config(config.inpaint_crop_ratio, config.inpaint_crop_mode)

        self.rectPanel.dilate_slider.setValue(config.recttool_dilate_ksize)
        self.rectPanel.autoChecker.setChecked(config.rectool_auto)
        self.rectPanel.methodComboBox.setCurrentIndex(config.rectool_method)
        if config.current_tool == ImageEditMode.HandTool:
            self.handTool.setChecked(True)
        elif config.current_tool == ImageEditMode.InpaintTool:
            self.inpaintTool.setChecked(True)
        elif config.current_tool == ImageEditMode.PenTool:
            self.penTool.setChecked(True)
        elif config.current_tool == ImageEditMode.RectTool:
            self.rectTool.setChecked(True)

    def get_pen_cursor(
        self,
        pen_color: QColor = None,
        pen_size=None,
        draw_shape=True,
        shape=PenShape.Circle,
    ) -> QCursor:
        cross_size = 31
        cross_len = cross_size // 4
        thickness = 3
        if pen_color is None:
            pen_color = self.pentool_pen.color()
        if pen_size is None:
            pen_size = self.pentool_pen.width()
        pen_size *= self.canvas.scale_factor
        map_size = max(cross_size + 7, pen_size)
        cursor_center = map_size // 2
        pen_radius = pen_size // 2
        pen_color.setAlpha(127)
        pen = QPen(
            pen_color,
            thickness,
            Qt.PenStyle.DotLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        pen.setDashPattern([3, 6])
        if pen_size < 20:
            pen.setStyle(Qt.PenStyle.SolidLine)

        cur_pixmap = QPixmap(QSizeF(map_size, map_size).toSize())
        cur_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(cur_pixmap)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if draw_shape:
            shape_rect = QRectF(
                cursor_center - pen_radius + thickness,
                cursor_center - pen_radius + thickness,
                pen_size - 2 * thickness,
                pen_size - 2 * thickness,
            )
            if shape == PenShape.Circle:
                painter.drawEllipse(shape_rect)
            elif shape == PenShape.Rectangle:
                painter.drawRect(shape_rect)
            else:
                raise NotImplementedError
            # elif shape == PenShape.Triangle:
            # painter.drawPolygon
        cross_left = (map_size - 1 - cross_size) // 2
        cross_right = map_size - cross_left

        pen = QPen(Qt.GlobalColor.white, 5, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        cross_hline0 = QLineF(
            cross_left, cursor_center, cross_left + cross_len, cursor_center
        )
        cross_hline1 = QLineF(
            cross_right - cross_len, cursor_center, cross_right, cursor_center
        )
        cross_vline0 = QLineF(
            cursor_center, cross_left, cursor_center, cross_left + cross_len
        )
        cross_vline1 = QLineF(
            cursor_center, cross_right - cross_len, cursor_center, cross_right
        )
        painter.drawLines([cross_hline0, cross_hline1, cross_vline0, cross_vline1])
        pen.setWidth(3)
        pen.setColor(Qt.GlobalColor.black)
        painter.setPen(pen)
        painter.drawLines([cross_hline0, cross_hline1, cross_vline0, cross_vline1])
        painter.end()
        return QCursor(cur_pixmap)

    def on_incre_pensize(self):
        self.scalePen(1.1)

    def on_decre_pensize(self):
        self.scalePen(0.9)
        pass

    def scalePen(self, scale_factor):
        if self.currentTool == self.penTool:
            val = self.pentool_pen.widthF()
            new_val = round(int(val * scale_factor))
            if scale_factor > 1:
                new_val = max(val + 1, new_val)
            else:
                new_val = min(val - 1, new_val)
            self.penConfigPanel.thicknessSlider.setValue(int(new_val))
            self.setPenToolWidth(self.penConfigPanel.thicknessSlider.value())

        elif self.currentTool == self.inpaintTool:
            val = self.inpaint_pen.widthF()
            new_val = round(int(val * scale_factor))
            if scale_factor > 1:
                new_val = max(val + 1, new_val)
            else:
                new_val = min(val - 1, new_val)
            self.inpaintConfigPanel.thicknessSlider.setValue(int(new_val))
            self.setInpaintToolWidth(self.inpaintConfigPanel.thicknessSlider.value())

    def showEvent(self, event) -> None:
        if self.currentTool is not None:
            self.currentTool.setChecked(False)
            self.currentTool.setChecked(True)
        return super().showEvent(event)

    def on_finish_painting(self, stroke_item: StrokeImgItem):
        stroke_item.finishPainting()
        if not self.canvas.imgtrans_proj.img_valid:
            self.canvas.removeItem(stroke_item)
            return
        if self.currentTool == self.penTool:
            rect, mask, _ = stroke_item.clip(mask_only=True)
            if mask is not None:
                proj = self.canvas.imgtrans_proj
                mx, my, mw, mh = rect
                mask_roi = proj.mask_array[my : my + mh, mx : mx + mw]
                new_mask = cv2.bitwise_or(mask_roi, mask)
                inpaint_rect = [mx, my, mx + mw, my + mh]
                redo_img = np.copy(
                    proj.inpainted_array[my : my + mh, mx : mx + mw]
                )
                self.canvas.push_undo_command(
                    InpaintUndoCommand(
                        self.canvas, redo_img, new_mask, inpaint_rect
                    )
                )
                proj.mask_array[my : my + mh, mx : mx + mw] = new_mask
                self.canvas.updateLayers()
            self.canvas.removeItem(stroke_item)
        elif self.currentTool == self.inpaintTool:
            self.inpaint_stroke = stroke_item
            if self._is_crop_masking():
                # In LLM crop-mask mode a brush stroke only marks the region to
                # be replaced — accumulate the mask and do NOT dispatch a
                # per-stroke repair (the crop "Inpaint" sends them all at once).
                self._accumulate_stroke_mask(stroke_item)
                return
            if self.canvas.gv.ctrl_pressed:
                return
            else:
                self.runInpaint()

    def on_finish_erasing(self, stroke_item: StrokeImgItem):
        stroke_item.finishPainting()
        # inpainted-erasing logic is essentially the same as inpainting
        if self.currentTool == self.inpaintTool:
            if self._is_crop_masking():
                # In LLM crop-mask mode a right-drag erases part of the mask
                # (the inverse of marking) instead of editing the image.
                rect, mask, _ = stroke_item.clip(mask_only=True)
                self.canvas.removeItem(stroke_item)
                self.inpaint_stroke = None
                if mask is not None:
                    self._erase_crop_mask(rect, mask)
                    self._update_crop_mask_preview()
                return
            rect, mask, _ = stroke_item.clip(mask_only=True)
            if mask is None:
                self.canvas.removeItem(stroke_item)
                return
            mask = 255 - mask
            mask_h, mask_w = mask.shape[:2]
            mask_x, mask_y = rect[0], rect[1]
            inpaint_rect = [mask_x, mask_y, mask_w + mask_x, mask_h + mask_y]
            origin = self.canvas.imgtrans_proj.img_array
            origin = origin[
                inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
            ]
            inpainted = self.canvas.imgtrans_proj.inpainted_array
            inpainted = inpainted[
                inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
            ]
            inpaint_mask = self.canvas.imgtrans_proj.mask_array[
                inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
            ]
            # no inpainted need to be erased
            if inpaint_mask.sum() == 0:
                self.canvas.removeItem(stroke_item)
                return
            mask = cv2.bitwise_and(mask, inpaint_mask)
            inpaint_mask = np.zeros_like(inpainted)
            inpaint_mask[mask > 0] = 1
            erased_img = inpaint_mask * inpainted + (1 - inpaint_mask) * origin
            self.canvas.push_undo_command(
                InpaintUndoCommand(self.canvas, erased_img, mask, inpaint_rect)
            )
            self.canvas.removeItem(stroke_item)

        elif self.currentTool == self.penTool:
            rect, mask, _ = stroke_item.clip(mask_only=True)
            if mask is not None:
                proj = self.canvas.imgtrans_proj
                mx, my, mw, mh = rect
                erase_mask = 255 - mask
                old_mask = np.copy(proj.mask_array[my : my + mh, mx : mx + mw])
                new_mask = cv2.bitwise_and(old_mask, erase_mask)
                inpaint_rect = [mx, my, mx + mw, my + mh]
                redo_img = np.copy(
                    proj.inpainted_array[my : my + mh, mx : mx + mw]
                )
                self.canvas.push_undo_command(
                    InpaintUndoCommand(
                        self.canvas, redo_img, new_mask, inpaint_rect
                    )
                )
                proj.mask_array[my : my + mh, mx : mx + mw] = new_mask
                self.canvas.updateLayers()
            self.canvas.removeItem(stroke_item)

    # ── Online-LLM crop tool ──

    def _inpainter_is_llm(self) -> bool:
        return self.inpaintConfigPanel.current_inpainter() == "LLMInpaint"

    def _crop_tool_active(self) -> bool:
        """Whether the current tool (or the pre-activation state) hosts a crop."""
        return self.currentTool is None or self.currentTool in (
            self.inpaintTool,
            self.rectTool,
        )

    def _is_crop_masking(self) -> bool:
        """LLM crop-mask mode: a crop exists, so brush/box-select only mark masks."""
        return (
            self._inpainter_is_llm()
            and self._crop_setup
            and self._crop_tool_active()
        )

    def _on_crop_ratio_changed(self, label: str):
        self._crop_ratio_label = label
        self._crop_ratio = ratio_from_label(label)
        self.crop_rect_item.setRatio(self._crop_ratio)
        pcfg.drawpanel.inpaint_crop_ratio = label
        self._sync_crop_controls()

    def _on_crop_mode_changed(self, checked: bool):
        self._crop_mode = bool(checked)
        pcfg.drawpanel.inpaint_crop_mode = self._crop_mode
        if checked:
            # Enabling crop mode establishes the crop; keep it visible as the
            # generation range after it is turned off.
            self._crop_setup = True
        self._sync_crop_controls()
        self._update_crop_active()

    def _on_llm_active_changed(self, is_llm: bool):
        self._update_crop_active()

    def _set_crop_config(self, ratio_label: str, crop_mode: bool):
        self._crop_ratio_label = ratio_label or "16:9"
        self._crop_ratio = ratio_from_label(self._crop_ratio_label)
        self._crop_mode = bool(crop_mode)
        self.crop_rect_item.setRatio(self._crop_ratio)
        self._sync_crop_controls()
        self._update_crop_active()

    def _sync_crop_controls(self):
        if not hasattr(self, "inpaintConfigPanel") or not hasattr(self, "rectPanel"):
            return
        # Re-assert the LLM visibility on every sync — the module combobox can
        # settle onto its saved value after the panels are built, so the first
        # open would otherwise leave the crop controls hidden until the user
        # re-picks the inpainter (2026-08-24).
        is_llm = self._inpainter_is_llm()
        for panel in (self.inpaintConfigPanel, self.rectPanel):
            panel.crop_controls.set_llm_active(is_llm)
            panel.crop_controls.set_ratio(self._crop_ratio_label)
            panel.crop_controls.set_mode(self._crop_mode)

    def _tool_natural_mode(self) -> int:
        if self.currentTool is self.inpaintTool:
            return ImageEditMode.InpaintTool
        if self.currentTool is self.penTool:
            return ImageEditMode.PenTool
        if self.currentTool is self.rectTool:
            return ImageEditMode.RectTool
        if self.currentTool is self.handTool:
            return ImageEditMode.HandTool
        return ImageEditMode.NONE

    def _tool_supports_crop(self) -> bool:
        return self.currentTool in (self.inpaintTool, self.rectTool)

    def _apply_canvas_mode(self):
        if self._crop_active:
            self.canvas.image_edit_mode = ImageEditMode.CropMode
        else:
            self.canvas.image_edit_mode = self._tool_natural_mode()

    def _update_crop_active(self):
        # Only drop crop mode when a concrete tool is active but cannot host a
        # crop (pen/hand). During config load the tool is activated afterwards,
        # so currentTool is still None here — never reset the persisted state.
        if (
            self.currentTool is not None
            and self._crop_mode
            and not self._tool_supports_crop()
        ):
            self._crop_mode = False
            pcfg.drawpanel.inpaint_crop_mode = False
            self._sync_crop_controls()
        is_llm = self._inpainter_is_llm()
        is_crop_tool = self._crop_tool_active()
        # "crop active" = the rect is being POSITIONED (editable, painting off,
        # tool body hidden). Once crop mode is closed the rect freezes but stays
        # visible as the generation range.  In LLM mode a crop always exists.
        self._crop_active = self._crop_mode and is_llm and is_crop_tool
        if is_llm and not self._crop_setup:
            self._crop_setup = True
        if not is_llm:
            self._crop_setup = False
            self._reset_crop_mask()
        self.crop_rect_item.set_editable(self._crop_active)
        # Re-assert each panel's crop-control visibility AND body visibility
        # from the CURRENT llm state on every call. The module combobox can be
        # set during config load with blockSignals, so module_changed may never
        # fire — without this, the first arrival could show only the model
        # selector (crop controls hidden + body hidden) until a tool switch
        # re-ran this (2026-08-24).
        for panel in (self.inpaintConfigPanel, self.rectPanel):
            panel.crop_controls.set_llm_active(is_llm)
            panel.set_crop_mode_active(self._crop_active)
        self._apply_canvas_mode()
        self._update_crop_visibility()

    def _update_crop_visibility(self):
        is_crop_tool = self._crop_tool_active()
        show = self._inpainter_is_llm() and self._crop_setup and is_crop_tool
        self.crop_rect_item.setVisible(show)
        if show and self.crop_rect_item.rect().width() <= 2:
            self._place_crop_default()
        if show and self.isVisible():
            # Crop editing is its own non-painting state; park the canvas on a
            # plain arrow cursor so no brush cursor can leak through the dashed
            # crop border.  While frozen (masking) keep the tool's own cursor.
            self.canvas.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
            if self._crop_active:
                self.canvas.gv.setCursor(Qt.CursorShape.ArrowCursor)

    def _place_crop_default(self):
        pr = self.canvas.baseLayer.rect()
        width, height = pr.width(), pr.height()
        if width <= 2 or height <= 2:
            return
        ratio = self._crop_ratio
        w = min(width, height * ratio)
        h = w / ratio
        self.crop_rect_item.set_pixel_rect(
            (width - w) / 2, (height - h) / 2, (width + w) / 2, (height + h) / 2
        )

    def _crop_inpaint_dict(self) -> dict:
        x0, y0, x1, y1 = self.crop_rect_item.pixel_rect()
        if x1 - x0 <= 2 or y1 - y0 <= 2:
            return None
        img_arr = self.canvas.imgtrans_proj.inpainted_array
        img = np.array(img_arr[y0:y1, x0:x1], copy=True)
        # Use the accumulated user mask clipped to the crop.  Only the marked
        # pixels get the regenerated content merged back; the rest of the crop
        # is preserved.  If nothing was marked, fall back to the full crop so a
        # bare "Inpaint" still regenerates the whole rectangle.
        mask = None
        if self._crop_mask_array is not None:
            m = self._crop_mask_array[y0:y1, x0:x1]
            if m.sum() > 0:
                mask = np.array(m, copy=True)
        if mask is None:
            mask = np.full((y1 - y0, x1 - x0), 255, np.uint8)
        return {"img": img, "mask": mask, "inpaint_rect": [x0, y0, x1, y1]}

    def _accumulate_stroke_mask(self, stroke_item: StrokeImgItem):
        """Merge a finished brush stroke's mask into the crop composite."""
        rect, mask, _ = stroke_item.clip(mask_only=True)
        self.canvas.removeItem(stroke_item)
        self.inpaint_stroke = None
        if mask is None:
            return
        self._merge_crop_mask(rect, mask)
        self._update_crop_mask_preview()

    def _merge_crop_mask(self, rect, mask):
        """OR a region-local mask (``rect=[x, y, w, h]``) into the crop composite.

        The composite lives in full-image coordinates and every region is
        clipped to the crop rectangle, since anything outside the crop was not
        sent to the API and cannot be repaired (2026-08-24).
        """
        proj = self.canvas.imgtrans_proj
        if not proj.img_valid:
            return
        if self._crop_mask_array is None:
            self._crop_mask_array = np.zeros(
                proj.inpainted_array.shape[:2], np.uint8
            )
        x, y, w, h = rect
        x0, y0, x1, y1 = self.crop_rect_item.pixel_rect()
        sx0, sy0 = max(x, x0), max(y, y0)
        sx1, sy1 = min(x + w, x1), min(y + h, y1)
        if sx1 <= sx0 or sy1 <= sy0:
            return  # entirely outside the crop — nothing to keep
        sub = np.asarray(mask[sy0 - y : sy1 - y, sx0 - x : sx1 - x])
        region = self._crop_mask_array[sy0:sy1, sx0:sx1]
        np.maximum(region, sub, out=region)

    def _update_crop_mask_preview(self):
        """Show the crop-local composite mask as a pink overlay on the canvas."""
        if self._crop_mask_array is None:
            if (
                self.inpaint_mask_item is not None
                and self.inpaint_mask_item.scene() == self.canvas
            ):
                self.canvas.removeItem(self.inpaint_mask_item)
            return
        x0, y0, x1, y1 = self.crop_rect_item.pixel_rect()
        if x1 - x0 <= 2 or y1 - y0 <= 2:
            return
        m = self._crop_mask_array[y0:y1, x0:x1]
        if m.sum() == 0:
            if (
                self.inpaint_mask_item is not None
                and self.inpaint_mask_item.scene() == self.canvas
            ):
                self.canvas.removeItem(self.inpaint_mask_item)
            return
        preview = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
        preview[:, :, [0, 2, 3]] = (m[:, :, np.newaxis] // 2).astype(np.uint8)
        self.inpaint_mask_item.setPixmap(ndarray2pixmap(preview))
        self.inpaint_mask_item.setParentItem(self.canvas.baseLayer)
        self.inpaint_mask_item.setPos(x0, y0)
        if self.inpaint_mask_item.scene() != self.canvas:
            self.canvas.addItem(self.inpaint_mask_item)
        self.inpaint_mask_item.show()

    def _reset_crop_mask(self):
        """Clear the accumulated crop mask and its canvas preview."""
        self._crop_mask_array = None
        if (
            self.inpaint_mask_item is not None
            and self.inpaint_mask_item.scene() == self.canvas
        ):
            self.canvas.removeItem(self.inpaint_mask_item)

    def _on_crop_rect_released(self):
        # The crop was just moved/resized; re-clip the composite preview to it.
        self._update_crop_mask_preview()

    # ── Mask erase / clear ─────────────────────────────────────────────

    def _erase_crop_mask(self, rect, mask):
        """Subtract a region-local erase stroke (``rect=[x, y, w, h]``) from the
        crop composite, clipped to the crop rectangle."""
        if self._crop_mask_array is None:
            return
        x, y, w, h = rect
        x0, y0, x1, y1 = self.crop_rect_item.pixel_rect()
        sx0, sy0 = max(x, x0), max(y, y0)
        sx1, sy1 = min(x + w, x1), min(y + h, y1)
        if sx1 <= sx0 or sy1 <= sy0:
            return
        sub = np.asarray(mask[sy0 - y : sy1 - y, sx0 - x : sx1 - x])
        region = self._crop_mask_array[sy0:sy1, sx0:sx1]
        region[sub > 0] = 0

    def _on_clear_crop_mask(self):
        """Wipe the whole accumulated crop mask (Clear mask button)."""
        self._reset_crop_mask()

    # ── Busy / finished indicator over the canvas ──────────────────────

    def _place_busy_overlay(self):
        vp = self.canvas.gv.viewport()
        if vp is None:
            return
        self._busy_overlay.adjustSize()
        x = max(0, (vp.width() - self._busy_overlay.width()) // 2)
        self._busy_overlay.move(x, 16)

    def _show_busy_overlay(self):
        if not self._inpainter_is_llm():
            return
        self._busy_overlay_shown = True
        self._busy_spinner_index = 0
        self._busy_overlay.setText(
            BUSY_SPINNER_CHARS[0] + " " + self.tr("Inpainting...")
        )
        self._place_busy_overlay()
        self._busy_overlay.show()
        self._busy_overlay.raise_()
        self._busy_spinner_timer.start()

    def _on_busy_spinner_tick(self):
        self._busy_spinner_index = (self._busy_spinner_index + 1) % len(
            BUSY_SPINNER_CHARS
        )
        self._busy_overlay.setText(
            BUSY_SPINNER_CHARS[self._busy_spinner_index]
            + " "
            + self.tr("Inpainting...")
        )
        self._place_busy_overlay()

    def _hide_busy_overlay(self, done: bool = False):
        if not self._busy_overlay_shown:
            return
        self._busy_overlay_shown = False
        self._busy_spinner_timer.stop()
        if done:
            self._busy_overlay.setText(self.tr("Inpainting finished"))
            self._place_busy_overlay()
            self._busy_overlay.show()
            self._busy_overlay.raise_()
            QTimer.singleShot(1600, self._busy_overlay.hide)
        else:
            self._busy_overlay.hide()

    def _notify_inpaint_busy(self):
        """A repair is already running — briefly explain why the click was ignored."""
        if self._busy_overlay_shown or not self._inpainter_is_llm():
            return
        self._busy_overlay.setText(
            self.tr("A repair is still in progress. Please wait.")
        )
        self._place_busy_overlay()
        self._busy_overlay.show()
        self._busy_overlay.raise_()
        QTimer.singleShot(1800, self._busy_overlay.hide)

    def eventFilter(self, obj, event):
        if obj is self.canvas.gv.viewport() and event.type() == QEvent.Type.Resize:
            self._place_busy_overlay()
        return super().eventFilter(obj, event)

    def runInpaint(self, inpaint_dict=None):

        if inpaint_dict is None and self._is_crop_masking():
            crop = self._crop_inpaint_dict()
            if crop is None:
                return
            # Only drop the accumulated mask once the request is actually handed
            # to the thread; if the thread is busy we keep the mask so the user
            # can retry, and tell them why.
            if not self.module_manager.canvas_inpaint(crop):
                self._notify_inpaint_busy()
                return
            self.clearInpaintItems()
            self._reset_crop_mask()
            self._apply_canvas_mode()
            self._show_busy_overlay()
            return

        if inpaint_dict is None:
            if self.inpaint_stroke is None:
                return
            elif self.inpaint_stroke.parentItem() is None:
                logger.warning("inpainting goes wrong")
                self.clearInpaintItems()
                return

            rect, mask, _ = self.inpaint_stroke.clip(mask_only=True)
            if mask is None:
                self.clearInpaintItems()
                return
            # we need to enlarge the mask window a bit to get better results
            mask_h, mask_w = mask.shape[:2]
            mask_x, mask_y = rect[0], rect[1]
            img = self.canvas.imgtrans_proj.inpainted_array
            inpaint_rect = [mask_x, mask_y, mask_w + mask_x, mask_h + mask_y]
            rect_enlarged = enlarge_window(inpaint_rect, img.shape[1], img.shape[0])
            top = mask_y - rect_enlarged[1]
            bottom = rect_enlarged[3] - inpaint_rect[3]
            left = mask_x - rect_enlarged[0]
            right = rect_enlarged[2] - inpaint_rect[2]

            mask = cv2.copyMakeBorder(
                mask, top, bottom, left, right, cv2.BORDER_CONSTANT, value=0
            )
            inpaint_rect = rect_enlarged
            img = img[
                inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
            ]
            inpaint_dict = {"img": img, "mask": mask, "inpaint_rect": inpaint_rect}

        self.canvas.image_edit_mode = ImageEditMode.NONE
        if not self.module_manager.canvas_inpaint(inpaint_dict):
            self._notify_inpaint_busy()
            return
        self._show_busy_overlay()

    def on_inpaint_finished(self, inpaint_dict):
        inpainted = inpaint_dict["inpainted"]
        inpaint_rect = inpaint_dict["inpaint_rect"]
        mask_array = self.canvas.imgtrans_proj.mask_array
        new_mask = inpaint_dict["mask"]
        mask = cv2.bitwise_or(
            new_mask,
            mask_array[
                inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
            ],
        )
        # The online-LLM inpainter regenerates the WHOLE crop, so the returned
        # image differs everywhere.  Merge back only where the user marked
        # (new_mask); everywhere else keep the crop's current pixels so the
        # unmarked part of the generation is discarded.  Local inpainters already
        # preserve unmarked pixels, so this is a no-op for them.
        src = inpaint_dict.get("img")
        if src is not None and new_mask is not None:
            try:
                if new_mask.shape[:2] == inpainted.shape[:2] == src.shape[:2]:
                    mask3 = new_mask[..., None]
                    inpainted = np.where(mask3 > 0, inpainted, src).astype(
                        inpainted.dtype
                    )
            except Exception:
                pass
        self.canvas.push_undo_command(
            InpaintUndoCommand(self.canvas, inpainted, mask, inpaint_rect)
        )
        self.clearInpaintItems()
        self._hide_busy_overlay(done=True)

    def on_inpaint_failed(self):
        if self.currentTool == self.inpaintTool and self.inpaint_stroke is not None:
            self.clearInpaintItems()
        self._hide_busy_overlay(done=False)

    def on_canvasctrl_released(self):
        # Ctrl+release finalizes a brush stroke's mask into an inpaint. In LLM
        # crop-mask mode a stroke only accumulates (no per-stroke dispatch) and
        # the crop is sent via its Inpaint button, so ignore the shortcut here
        # to avoid a stray crop dispatch.
        if (
            self.isVisible()
            and self.currentTool == self.inpaintTool
            and not self._is_crop_masking()
        ):
            self.runInpaint()

    def on_begin_scale_tool(self, pos: QPointF):

        if self.currentTool == self.penTool:
            circle_pen = QPen(self.pentool_pen)
        elif self.currentTool == self.inpaintTool:
            circle_pen = QPen(self.inpaint_pen)
        else:
            return
        pen_radius = circle_pen.widthF() / 2 * self.canvas.scale_factor

        r, g, b, a = circle_pen.color().getRgb()

        circle_pen.setWidth(3)
        circle_pen.setStyle(Qt.PenStyle.DashLine)
        circle_pen.setDashPattern([3, 6])
        self.scale_circle.setPen(circle_pen)
        self.scale_circle.setBrush(QBrush(QColor(r, g, b, 127)))
        self.scale_circle.setPos(pos - QPointF(pen_radius, pen_radius))
        pen_size = 2 * pen_radius
        self.scale_circle.setRect(0, 0, pen_size, pen_size)
        self.scale_tool_pos = pos - QPointF(pen_size, pen_size)
        self.canvas.addItem(self.scale_circle)
        self.scale_circle.setCursor(self.get_pen_cursor(draw_shape=False))

    def setCrossCursor(self) -> None:
        if not self.isVisible() or self.currentTool != self.rectTool:
            return
        self.canvas.set_canvas_cursor(self.get_pen_cursor(draw_shape=False))

    def on_scale_tool(self, pos: QPointF):
        if self.scale_tool_pos is None:
            return
        radius = pos.x() - self.scale_tool_pos.x()
        radius = max(
            min(radius, MAX_PEN_SIZE * self.canvas.scale_factor),
            MIN_PEN_SIZE * self.canvas.scale_factor,
        )
        self.scale_circle.setRect(0, 0, radius, radius)

    def on_end_scale_tool(self):
        if self.scale_tool_pos is None:
            return
        circle_size = int(self.scale_circle.rect().width() / self.canvas.scale_factor)
        self.scale_tool_pos = None
        self.canvas.removeItem(self.scale_circle)

        if self.currentTool == self.penTool:
            self.setPenToolWidth(circle_size)
            self.penConfigPanel.thicknessSlider.setValue(circle_size)
            self.setPenCursor()
        elif self.currentTool == self.inpaintTool:
            self.setInpaintToolWidth(circle_size)
            self.inpaintConfigPanel.thicknessSlider.setValue(circle_size)
            self.setInpaintCursor()

    def on_canvas_scalefactor_changed(self):
        if not self.isVisible():
            return
        if self.currentTool == self.penTool:
            self.setPenCursor()
        elif self.currentTool == self.inpaintTool:
            self.setInpaintCursor()

    def setPenCursor(self):
        self.canvas.gv.setCursor(self.get_pen_cursor(shape=self.penConfigPanel.shape))

    def setInpaintCursor(self):
        self.canvas.gv.setCursor(
            self.get_pen_cursor(
                INPAINT_BRUSH_COLOR,
                self.inpaint_pen.width(),
                shape=self.inpaintConfigPanel.shape,
            )
        )

    def on_handchecker_changed(self):
        if self.handTool.isChecked():
            self.toolConfigStackwidget.hide()
        else:
            self.toolConfigStackwidget.show()

    def on_end_create_rect(self, rect: QRectF, mode: int):
        if self.currentTool == self.rectTool:
            self.canvas.image_edit_mode = ImageEditMode.NONE
            img = self.canvas.imgtrans_proj.inpainted_array
            im_h, im_w = img.shape[:2]

            xyxy = [
                rect.x(),
                rect.y(),
                rect.x() + rect.width(),
                rect.y() + rect.height(),
            ]
            xyxy = np.array(xyxy)
            xyxy[[0, 2]] = np.clip(xyxy[[0, 2]], 0, im_w - 1)
            xyxy[[1, 3]] = np.clip(xyxy[[1, 3]], 0, im_h - 1)
            x1, y1, x2, y2 = xyxy.astype(np.int64)
            if y2 - y1 < 2 or x2 - x1 < 2:
                self.canvas.image_edit_mode = ImageEditMode.RectTool
                return
            if mode == 0:
                im = np.copy(img[y1:y2, x1:x2])
                maskseg_method = get_maskseg_method()
                inpaint_mask_array, ballon_mask, bub_dict = maskseg_method(
                    im, mask=self.canvas.imgtrans_proj.mask_array[y1:y2, x1:x2]
                )
                mask = self.rectPanel.post_process_mask(inpaint_mask_array)

                if self._is_crop_masking():
                    # In LLM crop-mask mode a box-select marks the region to be
                    # replaced — accumulate its segmented mask into the crop
                    # composite and do NOT dispatch a per-box repair.  Restore
                    # RectTool so the user can keep boxing more masks.
                    self.canvas.image_edit_mode = ImageEditMode.RectTool
                    self._merge_crop_mask([x1, y1, x2 - x1, y2 - y1], mask)
                    self._update_crop_mask_preview()
                    self.setCrossCursor()
                    return

                bground_rgb = bub_dict["bground_rgb"]
                need_inpaint = bub_dict["need_inpaint"]

                inpaint_dict = {
                    "img": im,
                    "mask": mask,
                    "inpaint_rect": [x1, y1, x2, y2],
                }
                inpaint_dict["need_inpaint"] = need_inpaint
                inpaint_dict["bground_rgb"] = bground_rgb
                inpaint_dict["ballon_mask"] = ballon_mask
                user_preview_mask = np.zeros(
                    (mask.shape[0], mask.shape[1], 4), dtype=np.uint8
                )
                user_preview_mask[:, :, [0, 2, 3]] = (
                    mask[:, :, np.newaxis] / 2
                ).astype(np.uint8)
                self.inpaint_mask_item.setPixmap(ndarray2pixmap(user_preview_mask))
                self.inpaint_mask_item.setParentItem(self.canvas.baseLayer)
                self.inpaint_mask_item.setPos(x1, y1)
                if self.rectPanel.auto():
                    self.inpaintRect(inpaint_dict)
                else:
                    self.inpaint_mask_array = inpaint_mask_array
                    self.rect_inpaint_dict = inpaint_dict
            else:  # erasing
                if self._is_crop_masking():
                    # Box erase in LLM crop-mask mode clears the mask over the
                    # box region rather than undoing image pixels.
                    self._erase_crop_mask(
                        [x1, y1, x2 - x1, y2 - y1],
                        np.full((y2 - y1, x2 - x1), 255, np.uint8),
                    )
                    self._update_crop_mask_preview()
                    self.canvas.image_edit_mode = ImageEditMode.RectTool
                    self.setCrossCursor()
                    return
                mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
                erased = self.canvas.imgtrans_proj.img_array[y1:y2, x1:x2]
                self.canvas.push_undo_command(
                    InpaintUndoCommand(self.canvas, erased, mask, [x1, y1, x2, y2])
                )
                self.canvas.image_edit_mode = ImageEditMode.RectTool
            self.setCrossCursor()

    def inpaintRect(self, inpaint_dict):
        img = inpaint_dict["img"]
        mask = inpaint_dict["mask"]
        need_inpaint = inpaint_dict["need_inpaint"]
        bground_rgb = inpaint_dict["bground_rgb"]
        ballon_mask = inpaint_dict["ballon_mask"]
        if not need_inpaint and pcfg.module.check_need_inpaint:
            bg_pixel_value = [bground_rgb[ii] for ii in range(3)]
            balloon_areas = np.where(ballon_mask > 0)
            if len(img.shape) == 3 and img.shape[2] == 4:
                avg_alpha = np.mean(img[balloon_areas][..., 3])
                avg_alpha = 0 if avg_alpha < 127 else avg_alpha
                bg_pixel_value.append(avg_alpha)
            bg_pixel_value = np.array(np.round(bg_pixel_value), dtype=np.uint8)
            img[balloon_areas] = bg_pixel_value
            self.canvas.push_undo_command(
                InpaintUndoCommand(
                    self.canvas,
                    img,
                    mask,
                    inpaint_dict["inpaint_rect"],
                    merge_existing_mask=True,
                )
            )
            self.clearInpaintItems()
        else:
            self.runInpaint(inpaint_dict=inpaint_dict)

    def on_rect_inpaintbtn_clicked(self):
        # In LLM crop-mask mode the box-select's Inpaint button (and Space)
        # dispatches the one-shot crop repair — the box body has no per-box
        # dispatch here.  Otherwise it drives the box-select inpaint as usual.
        if self._is_crop_masking():
            self.runInpaint()
            return
        if self.rect_inpaint_dict is not None:
            self.inpaintRect(self.rect_inpaint_dict)

    def on_rect_deletebtn_clicked(self):
        if self._is_crop_masking():
            self._reset_crop_mask()
            return
        self.clearInpaintItems()

    def on_rectool_ksize_changed(self):
        pcfg.drawpanel.recttool_dilate_ksize = self.rectPanel.dilate_slider.value()
        if (
            self.currentTool != self.rectTool
            or self.inpaint_mask_array is None
            or self.inpaint_mask_item is None
        ):
            return
        mask = self.rectPanel.post_process_mask(self.inpaint_mask_array)
        self.rect_inpaint_dict["mask"] = mask
        user_preview_mask = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        user_preview_mask[:, :, [0, 2, 3]] = (mask[:, :, np.newaxis] / 2).astype(
            np.uint8
        )
        self.inpaint_mask_item.setPixmap(ndarray2pixmap(user_preview_mask))

    def on_rectchecker_changed(self):
        if not self.rectTool.isChecked():
            self.clearInpaintItems()

    def hideEvent(self, e) -> None:
        self.clearInpaintItems()
        return super().hideEvent(e)

    def clearInpaintItems(self):

        self.rect_inpaint_dict = None
        self.inpaint_mask_array = None
        if self._is_crop_masking():
            # In LLM crop-mask mode the accumulated composite is consumed by the
            # one-shot crop inpaint (which resets it), not by tool switches — so
            # keep it (and its preview) across tool changes.
            self._update_crop_mask_preview()
        elif self.inpaint_mask_item is not None:
            if self.inpaint_mask_item.scene() == self.canvas:
                self.canvas.removeItem(self.inpaint_mask_item)

        if self.inpaint_stroke is not None:
            if self.inpaint_stroke.scene() == self.canvas:
                self.canvas.removeItem(self.inpaint_stroke)
            self.inpaint_stroke = None

        # Restore whichever mode is active for the current tool — including a
        # live crop mode (CropMode), which must survive a crop "Inpaint" run.
        self._apply_canvas_mode()

    def handle_page_changed(self):
        self.clearInpaintItems()
        # The crop is preserved across pages (same ratio), but the accumulated
        # mask only applies to the page it was drawn on — drop it.
        self._reset_crop_mask()
        # Re-place the crop centered if the page's image size changed (e.g. it
        # was tiny before the image loaded).
        self._update_crop_visibility()

    # ── Photoshop external editing ──────────────────────────────────────

    @staticmethod
    def _find_photoshop_path_registry():
        """Locate Photoshop.exe via Windows Registry.

        Returns the full path string, or ``None`` if not found.
        """
        try:
            import winreg
        except ImportError:
            return None

        # 1) App Paths — most reliable when set by the installer
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
            ) as key:
                path = winreg.QueryValue(key, None)
                if path and osp.exists(path):
                    return osp.normpath(path)
        except OSError:
            pass

        # 2) Adobe Photoshop versioned key — iterate to find the latest
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Adobe\Photoshop",
            ) as key:
                versions = []
                i = 0
                while True:
                    try:
                        versions.append(winreg.EnumKey(key, i))
                        i += 1
                    except OSError:
                        break
                versions.sort(reverse=True)
                for ver in versions:
                    try:
                        with winreg.OpenKey(key, f"{ver}\\ApplicationPath") as vkey:
                            path = winreg.QueryValue(vkey, None)
                            if path and osp.exists(path):
                                return osp.normpath(path)
                    except OSError:
                        continue
        except OSError:
            pass

        return None

    def on_edit_in_photoshop(self):
        """Save the current inpainted image as PNG and open it in Photoshop."""
        proj = self.canvas.imgtrans_proj
        if not proj.img_valid or proj.current_img is None:
            create_info_dialog(
                self.tr("No project or image is open. Open a project first.")
            )
            return

        # Resolve Photoshop path
        ps_path = pcfg.drawpanel.photoshop_path
        if not ps_path or not osp.exists(ps_path):
            found = self._find_photoshop_path_registry()
            if found:
                ps_path = found
                pcfg.drawpanel.photoshop_path = found
            else:
                create_info_dialog(
                    self.tr("Photoshop was not found.\n\nPlease set the path in:\nSettings → Inpainter → Photoshop Path")
                )
                return

        # Write the current inpainted image as PNG
        inpainted_dir = proj.inpainted_dir()
        os.makedirs(inpainted_dir, exist_ok=True)
        basename = osp.splitext(proj.current_img)[0]
        png_path = osp.join(inpainted_dir, f"{basename}.png")

        imwrite(png_path, proj.inpainted_array, ext=".png")

        # Launch Photoshop (detached — we don't wait for it)
        if not QProcess.startDetached(ps_path, [png_path]):
            create_info_dialog(
                self.tr("Failed to launch Photoshop. Please check the path in Settings.")
            )

    def on_refresh_from_photoshop(self):
        """Reload the PNG that Photoshop saved back into the project."""
        proj = self.canvas.imgtrans_proj
        if not proj.img_valid or proj.current_img is None:
            return

        basename = osp.splitext(proj.current_img)[0]
        png_path = osp.join(proj.inpainted_dir(), f"{basename}.png")

        if not osp.exists(png_path):
            create_info_dialog(
                self.tr("No edited image found. Please save your changes in Photoshop first.")
            )
            return

        edited = imread(png_path)
        if edited is None:
            create_info_dialog(
                self.tr("Failed to read the edited image file.")
            )
            return

        # Photoshop may save PNG with an alpha channel (RGBA).
        # Drop it to match project's 3-channel inpainted_array.
        if edited.ndim == 3 and edited.shape[-1] == 4:
            edited = np.ascontiguousarray(edited[..., :3])

        h, w = proj.img_array.shape[:2]
        eh, ew = edited.shape[:2]
        if eh != h or ew != w:
            create_info_dialog(
                self.tr("Image dimensions changed. Please undo in Photoshop and save again with the original dimensions.")
            )
            return

        # Create an undo command for the full image so the user can revert
        full_rect = [0, 0, w, h]
        full_mask = np.ones((h, w), dtype=np.uint8)
        undo_cmd = InpaintUndoCommand(
            self.canvas, edited, full_mask, full_rect
        )
        self.canvas.push_undo_command(undo_cmd)

        # Persist to the configured intermediate format too
        proj.save_inpainted(proj.current_img, proj.inpainted_array)

        # Note: canvas.updateLayers() is already called inside
        # InpaintUndoCommand.redo() pushed above, so the display is fresh.
