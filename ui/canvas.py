import os
from typing import List, Union

import numpy as np
from qtpy.QtCore import QDateTime, QLineF, QPoint, QPointF, QRectF, QSizeF, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QCursor,
    QHideEvent,
    QKeyEvent,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from qtpy.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneDragDropEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QLabel,
    QRubberBand,
    QScrollBar,
)

try:
    from qtpy.QtWidgets import QUndoCommand, QUndoStack
except ImportError:
    from qtpy.QtGui import QUndoCommand, QUndoStack

from utils import shared as C
from utils.config import pcfg
from utils.proj_imgtrans import ProjImgTrans

from .custom_widget import FadeLabel, ScrollBar
from .image_edit import DrawingLayer, ImageEditMode, StrokeImgItem
from .misc import ARROWKEY2DIRECTION, QKEY, ndarray2pixmap
from .page_search_widget import PageSearchWidget
from .texteditshapecontrol import (
    CONTROL_ITEM_DATA_KEY,
    ControlBlockItem,
    TextBlkShapeControl,
)
from .text_engine.transforms.grid_control import (
    GridControlPointItem,
    TextGridTransformControl,
)
from .textitem import TextBlkItem, TextBlock

CANVAS_SCALE_MAX = 10.0
CANVAS_SCALE_MIN = 0.01
CANVAS_SCALE_SPEED = 0.1
OVERFLOW_MARGIN_RATIO = 0.3  # 过界模式场景扩展比例
# Minimum drag (screen pixels, scaled by zoom) before a left-drag turns from
# a click into a text-block box select (2026-08-18).
MIN_RUBBER_BAND_DRAG = 4.0


class MoveByKeyCommand(QUndoCommand):
    def __init__(
        self,
        blkitems: List[TextBlkItem],
        direction: QPointF,
        shape_ctrl: TextBlkShapeControl,
    ) -> None:
        super().__init__()
        self.blkitems = blkitems
        self.direction = direction
        self.ori_pos_list = []
        self.end_pos_list = []
        self.shape_ctrl = shape_ctrl
        for blk in blkitems:
            pos = blk.pos()
            self.ori_pos_list.append(pos)
            self.end_pos_list.append(pos + direction)

    def undo(self):
        for blk, pos in zip(self.blkitems, self.ori_pos_list):
            blk.setPos(pos)
            if blk.under_ctrl and self.shape_ctrl.blk_item == blk:
                self.shape_ctrl.updateBoundingRect()

    def redo(self):
        for blk, pos in zip(self.blkitems, self.end_pos_list):
            blk.setPos(pos)
            if blk.under_ctrl and self.shape_ctrl.blk_item == blk:
                self.shape_ctrl.updateBoundingRect()

    def mergeWith(self, other: QUndoCommand) -> bool:
        canmerge = self.blkitems == other.blkitems and self.direction == other.direction
        if canmerge:
            self.end_pos_list = other.end_pos_list
        return canmerge

    def id(self):
        return 1


class CustomGV(QGraphicsView):
    ctrl_pressed = False
    scale_up_signal = Signal()
    scale_down_signal = Signal()
    scale_with_value = Signal(float)
    view_resized = Signal()
    hide_canvas = Signal()
    ctrl_released = Signal()
    canvas: QGraphicsScene = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scrollbar_h = ScrollBar(Qt.Orientation.Horizontal, self, fadeout=True)
        self.scrollbar_v = ScrollBar(Qt.Orientation.Vertical, self, fadeout=True)

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Left-drag on the canvas is the text-block box select (owned by the
        # scene); plain canvas panning is the middle button / scrollbars.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # qgraphicsview always scroll content according to wheelevent
        # which is not desired when scaling img
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.scale_up_signal.emit()
            else:
                self.scale_down_signal.emit()
            return
        return super().wheelEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == QKEY.Key_Control:
            self.ctrl_pressed = False
            self.ctrl_released.emit()
        return super().keyReleaseEvent(event)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        if key == QKEY.Key_Control:
            self.ctrl_pressed = True

        modifiers = e.modifiers()
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            if key == QKEY.Key_V:
                if self.canvas.handle_ctrlv():
                    e.accept()
                    return
            if key == QKEY.Key_C:
                if self.canvas.handle_ctrlc():
                    e.accept()
                    return

        elif (
            modifiers & Qt.KeyboardModifier.ControlModifier
            and modifiers & Qt.KeyboardModifier.ShiftModifier
        ):
            if key == QKEY.Key_C:
                self.canvas.copy_src_signal.emit()
                e.accept()
                return
            elif key == QKEY.Key_V:
                self.canvas.paste_src_signal.emit()
                e.accept()
                return
            elif key == QKEY.Key_D:
                self.canvas.delete_textblks.emit(1)
                e.accept()
                return

        return super().keyPressEvent(e)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.view_resized.emit()
        return super().resizeEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.hide_canvas.emit()
        return super().hideEvent(event)

    def event(self, e):
        if isinstance(e, QNativeGestureEvent):
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.scale_with_value.emit(e.value() + 1)
                e.setAccepted(True)

        return super().event(e)

    def dragMoveEvent(self, e: QGraphicsSceneDragDropEvent):
        super().dragMoveEvent(e)
        if e.mimeData().hasUrls():
            # issue #908, https://stackoverflow.com/questions/4177720/accepting-drops-on-a-qgraphicsscene
            e.setAccepted(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.canvas is not None and self.canvas._drag_hover_active:
            painter = QPainter(self.viewport())
            pen = QPen(QColor(64, 150, 255, 180), 3)
            painter.setPen(pen)
            r = self.viewport().rect()
            painter.drawRect(r.adjusted(2, 2, -3, -3))
            fill_color = QColor(64, 150, 255, 30)
            painter.fillRect(r, fill_color)
            painter.end()


class SnapGuideItem(QGraphicsItem):
    """Transient overlay that draws snap guide lines during text-block drag.

    Placed on the textLayer with a high Z value so it renders on top
    of all TextBlkItem instances.  Managed by Canvas — created once at
    init time, updated via :meth:`set_guides`, cleared on mouse release.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._guides: List[QLineF] = []
        self._pen = QPen(QColor(255, 0, 255), 0)  # magenta, cosmetic
        self._pen.setStyle(Qt.PenStyle.DashLine)
        self.setZValue(100)
        self.hide()

    def set_guides(self, guides: List[QLineF]):
        """Replace guide lines and show the item (or hide if empty)."""
        old_rect = self.boundingRect() if self._guides else QRectF()
        self.prepareGeometryChange()
        self._guides = guides
        if guides:
            self.show()
            self.update()
        else:
            self.hide()
        # Expand the dirty region so old *and* new guide areas redraw
        if old_rect.isValid():
            self.scene().update(old_rect)

    def clear_guides(self):
        """Convenience: set_guides with an empty list."""
        self.set_guides([])

    def boundingRect(self) -> QRectF:
        if not self._guides:
            return QRectF()
        r = QRectF()
        for line in self._guides:
            r = r.united(QRectF(line.p1(), line.p2()))
        # Add tiny margin so the cosmetic pen is not clipped
        return r.adjusted(-2, -2, 2, 2)

    def paint(self, painter: QPainter, option, widget=None):
        if not self._guides:
            return
        painter.setPen(self._pen)
        for line in self._guides:
            painter.drawLine(line)


class Canvas(QGraphicsScene):
    scalefactor_changed = Signal()
    end_create_textblock = Signal(QRectF)
    paste2selected_textitems = Signal()
    end_create_rect = Signal(QRectF, int)
    finish_painting = Signal(StrokeImgItem)
    finish_erasing = Signal(StrokeImgItem)
    delete_textblks = Signal(int)
    copy_textblks = Signal()
    paste_textblks = Signal(QPointF)
    copy_src_signal = Signal()
    paste_src_signal = Signal()

    format_textblks = Signal()
    reset_angle = Signal()
    squeeze_blk = Signal()

    reorder_textblks = Signal(str, int)

    run_blktrans = Signal(int)

    begin_scale_tool = Signal(QPointF)
    scale_tool = Signal(QPointF)
    end_scale_tool = Signal()
    canvas_undostack_changed = Signal()

    imgtrans_proj: ProjImgTrans = None
    painting_pen = QPen()
    painting_shape = 0
    erasing_pen = QPen()
    image_edit_mode = ImageEditMode.NONE

    projstate_unsaved = False
    proj_savestate_changed = Signal(bool)
    textstack_changed = Signal()
    drop_open_folder = Signal(str)
    drop_images = Signal(list)  # list of image file paths from drag-drop
    context_menu_requested = Signal(QPoint, bool)
    incanvas_selection_changed = Signal()
    align_textblks = Signal(str)
    merge_textblks = Signal()
    position_picked = Signal(int)
    switch_text_item = Signal(int, QKeyEvent)

    # Path-reorder mode
    reorder_path_finished = Signal(list)  # touched_ids in contact order

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale_factor = 1.0
        self.text_transparency = 0
        self.textblock_mode = False
        self.alignment_enabled = True
        self.snap_guide_item = SnapGuideItem()
        self.creating_textblock = False
        self._pick_axis = None  # "x" | "y" | None — canvas coordinate pick mode
        self._pick_line = None
        # Path-reorder mode (replaces grid-based Smart Reorder)
        self._reorder_mode = False
        self._reorder_drawing = False       # left button currently held
        self._reorder_path = QPainterPath()
        self._reorder_path_item: QGraphicsPathItem = None
        self._reorder_touched_blocks: List[TextBlock] = []  # TextBlock refs in contact order
        self._reorder_brush_radius = 20.0   # effective brush half-width (scene coords)
        self.create_block_origin: QPointF = None
        self.editing_textblkitem: TextBlkItem = None

        self.gv = CustomGV(self)
        self.gv.scale_down_signal.connect(self.scaleDown)
        self.gv.scale_up_signal.connect(self.scaleUp)
        self.gv.scale_with_value.connect(self.scaleBy)
        self.gv.view_resized.connect(self.onViewResized)
        self.gv.hide_canvas.connect(self.on_hide_canvas)
        self.gv.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.gv.canvas = self
        self.gv.setAcceptDrops(True)
        self.gv.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.gv.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.context_menu_requested.connect(self.on_create_contextmenu)

        if not C.FLAG_QT6:
            # mitigate https://bugreports.qt.io/browse/QTBUG-93417
            # produce blurred result, saving imgs remain unaffected
            self.gv.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self.search_widget = PageSearchWidget(self.gv)
        self.search_widget.hide()

        self.ctrl_relesed = self.gv.ctrl_released
        self.vscroll_bar = self.gv.verticalScrollBar()
        self.hscroll_bar = self.gv.horizontalScrollBar()
        self.rubber_band = self.addWidget(QRubberBand(QRubberBand.Shape.Rectangle))
        self.rubber_band.hide()
        self.rubber_band_origin = None
        self.rubber_band_dragged = False  # left-drag box select moved past the click threshold

        self.draw_undo_stack = QUndoStack(self)
        self.text_undo_stack = QUndoStack(self)
        self.saved_drawundo_step = 0
        self.saved_textundo_step = 0

        self.scaleFactorLabel = FadeLabel(self.gv)
        self.scaleFactorLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scaleFactorLabel.setText("100%")
        self.scaleFactorLabel.gv = self.gv

        self.notextLabel = QLabel(self.gv)
        self.notextLabel.setObjectName("NotextLabel")
        self.notextLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notextLabel.setText(self.tr("No-text BG"))
        self.notextLabel.setStyleSheet(
            "background: rgba(39, 174, 96, 180); color: white; "
            "padding: 4px 12px; border-radius: 6px; font-size: 15px;"
        )
        self.notextLabel.adjustSize()
        self.notextLabel.setVisible(False)
        self._layout_status_labels()

        # Empty-state hint shown when no project is loaded
        self._empty_hint_label = QLabel(self.gv)
        self._empty_hint_label.setObjectName("EmptyHintLabel")
        self._empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint_label.setText(
            self.tr("Drop images here or open a folder to start")
        )
        self._empty_hint_label.setStyleSheet(
            "color: rgba(128, 128, 128, 180); font-size: 22px; background: transparent;"
        )
        self._empty_hint_label.adjustSize()
        self._empty_hint_label.setVisible(True)

        self._drag_hover_active = False

        self.txtblkShapeControl = TextBlkShapeControl(self.gv)

        self.baseLayer = QGraphicsRectItem()
        pen = QPen()
        pen.setColor(Qt.GlobalColor.transparent)
        self.baseLayer.setPen(pen)

        self.inpaintLayer = QGraphicsPixmapItem()
        self.inpaintLayer.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        self.drawingLayer = DrawingLayer()
        self.drawingLayer.setTransformationMode(
            Qt.TransformationMode.FastTransformation
        )
        self.textLayer = QGraphicsPixmapItem()

        self.inpaintLayer.setAcceptDrops(True)
        self.drawingLayer.setAcceptDrops(True)
        self.textLayer.setAcceptDrops(True)
        self.baseLayer.setAcceptDrops(True)

        self.base_pixmap: QPixmap = None

        self.addItem(self.baseLayer)
        self.inpaintLayer.setParentItem(self.baseLayer)
        self.drawingLayer.setParentItem(self.baseLayer)
        self.textLayer.setParentItem(self.baseLayer)
        self.txtblkShapeControl.setParentItem(self.baseLayer)

        # Grid transform overlay (stage 5 follow-up): bound by the transform
        # session when a Grid card is selected; hidden otherwise.
        self.textGridControl = TextGridTransformControl()
        self.addItem(self.textGridControl)
        self.textGridControl.setParentItem(self.baseLayer)

        self.addItem(self.snap_guide_item)
        self.snap_guide_item.setParentItem(self.textLayer)

        self.scalefactor_changed.connect(self.onScaleFactorChanged)
        self.selectionChanged.connect(self.on_selection_changed)

        self.stroke_img_item: StrokeImgItem = None
        self.erase_img_key = None

        self.editor_index = 0  # 0: drawing 1: text editor
        self.mid_btn_pressed = False
        self.pan_initial_pos = QPoint(0, 0)

        self.saved_textundo_step = 0
        self.saved_drawundo_step = 0
        self.num_pushed_textstep = 0
        self.num_pushed_drawstep = 0

        self.clipboard_blks: List[TextBlock] = []

        self.drop_folder: str = None
        self._drop_images: List[str] = []
        self.block_selection_signal = False
        self._fit_to_window = True  # fit-to-window flag, set by MainWindow before updateCanvas()

        # Tiny rect for empty startup — updated to actual image size when a project opens.
        im_rect = QRectF(0, 0, 1, 1)
        self.baseLayer.setRect(im_rect)

    def on_switch_item(self, switch_delta: int, key_event: QKeyEvent = None):
        if self.textEditMode():
            self.switch_text_item.emit(switch_delta, key_event)

    def img_window_size(self):
        if self.imgtrans_proj.inpainted_valid:
            return self.inpaintLayer.pixmap().size()
        return self.baseLayer.rect().size().toSize()

    _IMG_EXTS = frozenset({".bmp", ".jpg", ".png", ".jpeg", ".webp", ".jxl"})

    def dragEnterEvent(self, e: QGraphicsSceneDragDropEvent):
        self.drop_folder = None
        self._drop_images = []

        # No drag response when a project is already open
        if self.imgtrans_proj.img_valid:
            return

        if e.mimeData().hasUrls():
            imgs = []
            for url in e.mimeData().urls():
                furl = url.toLocalFile()
                if os.path.isdir(furl):
                    self.drop_folder = furl
                    break  # folder wins immediately
                elif os.path.splitext(furl)[1].lower() in self._IMG_EXTS:
                    imgs.append(furl)

            if self.drop_folder is not None:
                e.acceptProposedAction()
                self._show_drag_hover(True)
            elif imgs:
                self._drop_images = imgs
                e.acceptProposedAction()
                self._show_drag_hover(True)

    def dragLeaveEvent(self, e: QGraphicsSceneDragDropEvent):
        self._show_drag_hover(False)
        return super().dragLeaveEvent(e)

    def dropEvent(self, event) -> None:
        if self.drop_folder is not None:
            self.drop_open_folder.emit(self.drop_folder)
        elif self._drop_images:
            self.drop_images.emit(self._drop_images)
        self.drop_folder = None
        self._drop_images = []
        self._show_drag_hover(False)
        return super().dropEvent(event)

    def textEditMode(self) -> bool:
        return self.editor_index == 1

    def drawMode(self) -> bool:
        return self.editor_index == 0

    def scaleUp(self):
        self.scaleImage(1 + CANVAS_SCALE_SPEED)

    def scaleDown(self):
        self.scaleImage(1 - CANVAS_SCALE_SPEED)

    def scaleBy(self, value: float):
        self.scaleImage(value)

    def _overflow_scene_rect(self) -> QRectF:
        """Return expanded scene rect for overflow mode, or normal rect."""
        br = self.baseLayer.sceneBoundingRect()
        if not pcfg.overflow_mode or not self.imgtrans_proj.img_valid:
            return QRectF(0, 0, br.width(), br.height())
        mw = br.width() * OVERFLOW_MARGIN_RATIO
        mh = br.height() * OVERFLOW_MARGIN_RATIO
        return QRectF(-mw, -mh, br.width() + 2 * mw, br.height() + 2 * mh)

    def _set_scene_scale(self, scale: float):
        self.scale_factor = scale
        self.baseLayer.setScale(scale)
        self.setSceneRect(self._overflow_scene_rect())

    def render_result_img(self):

        self.inpaintLayer.hide()
        tlayer_opacity_before = self.textLayer.opacity()
        tlayer_visible = self.textLayer.isVisible()
        if tlayer_opacity_before != 1:
            self.textLayer.setOpacity(1)
        if not tlayer_visible:
            self.textLayer.show()
        scale_before = self.scale_factor
        if scale_before != 1:
            hb_pos = self.hscroll_bar.value()
            vb_pos = self.vscroll_bar.value()
            self._set_scene_scale(1)

        self.clearSelection()
        if self.textEditMode() and self.txtblkShapeControl.blk_item is not None:
            blk_item = self.txtblkShapeControl.blk_item
            if blk_item.is_editting():
                blk_item.endEdit(keep_focus=False)
            if blk_item.isSelected():
                blk_item.setSelected(False)

        # Hide sequence badges before rendering the result image
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                item._hide_badge = True

        proj = self.imgtrans_proj
        base = proj.notext_array if (proj.notext_array is not None) else proj.inpainted_array
        result = ndarray2pixmap(base, return_qimg=True)
        canvas_sz = self.img_window_size()
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = QRectF(0, 0, canvas_sz.width(), canvas_sz.height())
        self.render(
            painter, rect, rect
        )  #  produce blurred result if target/source rect not specified #320
        painter.end()

        # Restore badge visibility after rendering
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                item._hide_badge = False

        if tlayer_opacity_before != 1:
            self.textLayer.setOpacity(tlayer_opacity_before)
        if not tlayer_visible:
            self.textLayer.hide()
        if scale_before != 1:
            self._set_scene_scale(scale_before)
            if self.hscroll_bar.value() != hb_pos:
                self.hscroll_bar.setValue(hb_pos)
            if self.vscroll_bar.value() != vb_pos:
                self.vscroll_bar.setValue(vb_pos)
        self.inpaintLayer.show()

        return result

    def updateLayers(self):

        if not self.imgtrans_proj.img_valid:
            return

        inpainted_as_base = self.imgtrans_proj.inpainted_valid

        if inpainted_as_base:
            self.base_pixmap = ndarray2pixmap(self.imgtrans_proj.inpainted_array)

        pixmap = self.base_pixmap.copy()
        painter = QPainter(pixmap)
        origin = QPoint(0, 0)

        if self.imgtrans_proj.img_valid and pcfg.original_transparency > 0:
            painter.setOpacity(pcfg.original_transparency)
            if inpainted_as_base:
                painter.drawPixmap(origin, ndarray2pixmap(self.imgtrans_proj.img_array))
            else:
                painter.drawPixmap(origin, pixmap)

        if (
            self.imgtrans_proj.mask_valid
            and pcfg.mask_transparency > 0
            and not self.textEditMode()
        ):
            painter.setOpacity(pcfg.mask_transparency)
            painter.drawPixmap(origin, ndarray2pixmap(self.imgtrans_proj.mask_array))

        painter.end()
        self.inpaintLayer.setPixmap(pixmap)

        self.notextLabel.setVisible(
            pcfg.use_notext_images and self.imgtrans_proj.notext_array is not None
        )
        self._layout_status_labels()

    def setMaskTransparency(self, transparency: float):
        pcfg.mask_transparency = transparency
        self.updateLayers()

    def setOriginalTransparency(self, transparency: float):
        pcfg.original_transparency = transparency
        self.updateLayers()

    def setTextLayerTransparency(self, transparency: float):
        self.textLayer.setOpacity(transparency)
        self.text_transparency = transparency

    def adjustScrollBar(self, scrollBar: QScrollBar, factor: float):
        scrollBar.setValue(
            int(factor * scrollBar.value() + ((factor - 1) * scrollBar.pageStep() / 2))
        )

    def scaleImage(self, factor: float):
        if not self.gv.isVisible() or not self.imgtrans_proj.img_valid:
            return
        s_f = self.scale_factor * factor
        s_f = np.clip(s_f, CANVAS_SCALE_MIN, CANVAS_SCALE_MAX)

        scale_changed = self.scale_factor != s_f
        self.scale_factor = s_f
        self.baseLayer.setScale(self.scale_factor)
        self.txtblkShapeControl.updateScale(self.scale_factor)

        if scale_changed:
            self.adjustScrollBar(self.gv.horizontalScrollBar(), factor)
            self.adjustScrollBar(self.gv.verticalScrollBar(), factor)
            self.scalefactor_changed.emit()
        self.setSceneRect(self._overflow_scene_rect())

    def _fitToWindow(self):
        """Scale image to fit viewport with a small margin."""
        view_w = self.gv.geometry().width()
        view_h = self.gv.geometry().height()
        img_w = self.baseLayer.rect().width()
        img_h = self.baseLayer.rect().height()
        if img_w <= 0 or img_h <= 0:
            return
        margin = 0.95
        fit_scale = min(view_w / img_w, view_h / img_h) * margin
        fit_scale = np.clip(fit_scale, CANVAS_SCALE_MIN, CANVAS_SCALE_MAX)
        scale_changed = self.scale_factor != fit_scale
        self.scale_factor = fit_scale
        self.baseLayer.setScale(self.scale_factor)
        self.txtblkShapeControl.updateScale(self.scale_factor)
        if scale_changed:
            self.scalefactor_changed.emit()
        self.setSceneRect(self._overflow_scene_rect())

    def fitToWindow(self):
        """Public alias for :meth:`_fitToWindow` (pie-menu command pool)."""
        self._fitToWindow()

    def _layout_status_labels(self):
        """Unify label sizes and position them dynamically in top-left corner."""
        # Re-adjust to content (text length may vary by i18n)
        self.notextLabel.adjustSize()
        self.notextLabel.move(8, 8)

    def setOverflowMode(self, enabled: bool):
        """Toggle overflow mode on/off and update the canvas display."""
        pcfg.overflow_mode = enabled
        self.setSceneRect(self._overflow_scene_rect())
        self.gv.viewport().update()

    def onViewResized(self):
        gv_w, gv_h = self.gv.geometry().width(), self.gv.geometry().height()

        x = gv_w - self.scaleFactorLabel.width()
        y = gv_h - self.scaleFactorLabel.height()
        pos_new = (QPointF(x, y) / 2).toPoint()
        if self.scaleFactorLabel.pos() != pos_new:
            self.scaleFactorLabel.move(pos_new)

        x = gv_w - self.search_widget.width()
        pos = self.search_widget.pos()
        pos.setX(x - 30)
        self.search_widget.move(pos)

        # Center the empty-state hint label in the viewport
        if self._empty_hint_label.isVisible():
            self._empty_hint_label.adjustSize()
            hint_x = (gv_w - self._empty_hint_label.width()) // 2
            hint_y = (gv_h - self._empty_hint_label.height()) // 2
            self._empty_hint_label.move(hint_x, hint_y)

        self._layout_status_labels()

    def onScaleFactorChanged(self):
        self.scaleFactorLabel.setText(f"{self.scale_factor * 100:2.0f}%")
        self.scaleFactorLabel.raise_()
        self.scaleFactorLabel.startFadeAnimation()

    def _show_drag_hover(self, active: bool):
        """Show/hide a semi-transparent blue overlay during drag-hover."""
        if self._drag_hover_active == active:
            return
        self._drag_hover_active = active
        self.gv.viewport().update()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw overflow boundary overlay on top of all scene items."""
        if not pcfg.overflow_mode or not self.imgtrans_proj.img_valid:
            return

        img_rect = self.baseLayer.sceneBoundingRect()

        # ── Semi-transparent dark overlay outside the image boundary ──
        painter.save()
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.setPen(Qt.PenStyle.NoPen)

        # Construct a large rect minus the image rect (OddEvenFill creates the hole)
        vp = self.gv.viewport()
        vp_rect = self.gv.mapToScene(vp.rect()).boundingRect()
        margin = max(vp_rect.width(), vp_rect.height()) * 2
        large = vp_rect.adjusted(-margin, -margin, margin, margin)
        path = QPainterPath()
        path.addRect(large)
        path.addRect(img_rect)
        painter.drawPath(path)

        # ── Red boundary line at the image edge ──
        pen = QPen(QColor(255, 60, 60, 200), 0)  # cosmetic pen (1px always)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(img_rect)

        painter.restore()

    def _update_hint_visibility(self):
        """Show the empty-state hint and clear canvas when no project is loaded."""
        visible = not self.imgtrans_proj.img_valid
        self._empty_hint_label.setVisible(visible)
        if visible:
            self.onViewResized()  # re-center the hint
            self._clear_canvas()

    def _clear_canvas(self):
        """Clear visual content from the canvas when the project is no longer valid."""
        self.editing_textblkitem = None
        self.stroke_img_item = None
        self.erase_img_key = None
        self.txtblkShapeControl.setBlkItem(None)
        self.clear_text_transform_controls()
        self.mid_btn_pressed = False
        self.search_widget.reInitialize()
        self.clearSelection()
        self.setProjSaveState(False)

        # Clear text block items from the scene
        for item in list(self.textLayer.childItems()):
            self.removeItem(item)

        # Reset pixmap layers to transparent
        if self.base_pixmap is not None:
            pixmap = self.base_pixmap.copy()
            pixmap.fill(Qt.GlobalColor.transparent)
            self.textLayer.setPixmap(pixmap)
            self.inpaintLayer.setPixmap(pixmap)

        self.drawingLayer.clearAllDrawings()

        self.baseLayer.setRect(QRectF(0, 0, 1, 1))
        self.baseLayer.setScale(1)

    def bind_text_grid_control(
        self,
        item,
        stack_index,
        *,
        begin_edit,
        preview_points,
        commit_points,
        cancel_edit,
    ):
        """Bind the Grid overlay to *item*'s selected Grid stage.

        Dispatched from ``TextTransformEditSession._sync_transform_controller``
        whenever the selected transform card is a Grid transform.
        """
        self.textGridControl.bind(
            item,
            stack_index,
            begin_edit=begin_edit,
            preview_points=preview_points,
            commit_points=commit_points,
            cancel_edit=cancel_edit,
        )

    def clear_text_transform_controls(self):
        """Detach and hide the Grid overlay (session dispatch)."""
        self.textGridControl.clear()

    def on_selection_changed(self):
        if self.txtblkShapeControl.isVisible():
            blk_item = self.txtblkShapeControl.blk_item
            if blk_item is not None and blk_item.isEditing():
                blk_item.endEdit()
        if self.hasFocus() and not self.block_selection_signal:
            self.incanvas_selection_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        modifiers = event.modifiers()
        if (
            (modifiers == Qt.KeyboardModifier.AltModifier)
            and not key == QKEY.Key_Alt
            and self.editing_textblkitem is None
        ):
            if key in {QKEY.Key_W, QKEY.Key_A, QKEY.Key_Left, QKEY.Key_Up}:
                self.on_switch_item(-1, event)
                return
            elif key in {QKEY.Key_S, QKEY.Key_D, QKEY.Key_Right, QKEY.Key_Down}:
                self.on_switch_item(1, event)
                return

        if self.editing_textblkitem is not None:
            return super().keyPressEvent(event)
        elif key in ARROWKEY2DIRECTION:
            sel_blkitems = self.selected_text_items()
            if len(sel_blkitems) > 0:
                direction = ARROWKEY2DIRECTION[key]
                cmd = MoveByKeyCommand(sel_blkitems, direction, self.txtblkShapeControl)
                self.push_undo_command(cmd)
                event.setAccepted(True)
                return
        return super().keyPressEvent(event)

    def addStrokeImageItem(self, pos: QPointF, pen: QPen, erasing: bool = False):
        if self.stroke_img_item is not None:
            self.stroke_img_item.startNewPoint(pos)
        else:
            self.stroke_img_item = StrokeImgItem(
                pen, pos, self.img_window_size(), shape=self.painting_shape
            )
            if not erasing:
                self.stroke_img_item.setParentItem(self.baseLayer)
            else:
                self.erase_img_key = str(QDateTime.currentMSecsSinceEpoch())
                compose_mode = QPainter.CompositionMode.CompositionMode_DestinationOut
                self.drawingLayer.addQImage(
                    0, 0, self.stroke_img_item._img, compose_mode, self.erase_img_key
                )

    def startCreateTextblock(self, pos: QPointF, hide_control: bool = False):
        pos = pos / self.scale_factor
        self.creating_textblock = True
        self.create_block_origin = pos
        self.gv.setCursor(Qt.CursorShape.CrossCursor)
        self.txtblkShapeControl.setBlkItem(None)
        self.clear_text_transform_controls()
        self.txtblkShapeControl.setPos(0, 0)
        self.txtblkShapeControl.setRotation(0)
        self.txtblkShapeControl.setRect(QRectF(pos, QSizeF(1, 1)))
        if hide_control:
            self.txtblkShapeControl.hideControls()
        self.txtblkShapeControl.show()

    def endCreateTextblock(self, btn=0):
        self.creating_textblock = False
        self.gv.setCursor(Qt.CursorShape.ArrowCursor)
        self.txtblkShapeControl.hide()
        textblk_created = False
        rect = self.txtblkShapeControl.rect()
        if self.creating_normal_rect:
            self.end_create_rect.emit(rect, btn)
            self.txtblkShapeControl.showControls()
        else:
            if rect.width() > 1 and rect.height() > 1:
                self.end_create_textblock.emit(rect)
                textblk_created = True
        return textblk_created

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._pick_axis is not None and self._pick_line is not None:
            pos = event.scenePos()
            scene_w = max(self.sceneRect().width() or 2000, 2000)
            scene_h = max(self.sceneRect().height() or 2000, 2000)
            if self._pick_axis == "y":
                self._pick_line.setLine(0, pos.y(), scene_w, pos.y())
            else:
                self._pick_line.setLine(pos.x(), 0, pos.x(), scene_h)
            self._pick_line.show()
            return

        # Path-reorder mode — extend stroke and check intersections
        if self._reorder_drawing:
            self._reorder_extend_stroke(event.scenePos())
            return

        if self.mid_btn_pressed:
            new_pos = event.screenPos()
            delta_pos = new_pos - self.pan_initial_pos
            self.pan_initial_pos = new_pos
            self.hscroll_bar.setValue(int(self.hscroll_bar.value() - delta_pos.x()))
            self.vscroll_bar.setValue(int(self.vscroll_bar.value() - delta_pos.y()))

        elif self.creating_textblock:
            self.txtblkShapeControl.setRect(
                QRectF(
                    self.create_block_origin, event.scenePos() / self.scale_factor
                ).normalized()
            )

        elif self.stroke_img_item is not None:
            if self.stroke_img_item.is_painting:
                pos = self.inpaintLayer.mapFromScene(event.scenePos())
                if self.erase_img_key is None:
                    # painting
                    self.stroke_img_item.lineTo(pos)
                else:
                    rect = self.stroke_img_item.lineTo(pos, update=False)
                    if rect is not None:
                        self.drawingLayer.update(rect)

        elif self.scale_tool_mode:
            self.scale_tool.emit(event.scenePos())

        elif self.rubber_band.isVisible() and self.rubber_band_origin is not None:
            self.rubber_band.setGeometry(
                QRectF(self.rubber_band_origin, event.scenePos()).normalized()
            )
            rect = self.rubber_band.geometry()
            if not self.rubber_band_dragged:
                min_drag = MIN_RUBBER_BAND_DRAG / self.scale_factor
                if rect.width() <= min_drag and rect.height() <= min_drag:
                    return  # still a click — keep the native press selection
                self.rubber_band_dragged = True
            additive = bool(
                event.modifiers()
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier
                )
            )
            self.apply_box_selection(rect, additive=additive)
            return  # the box select owns the gesture — never forward to items

        return super().mouseMoveEvent(event)

    @property
    def scale_tool_mode(self):
        return (
            self.drawMode()
            and self.gv.isVisible()
            and QApplication.keyboardModifiers() == Qt.KeyboardModifier.AltModifier
        )

    def clearToolStates(self):
        self.end_scale_tool.emit()

    def selected_text_items(self, sort: bool = True) -> List[TextBlkItem]:
        sel_textitems = []
        selitems = self.selectedItems()
        for sel in selitems:
            if isinstance(sel, TextBlkItem):
                sel_textitems.append(sel)
        if sort:
            sel_textitems.sort(key=lambda x: x.idx)
        return sel_textitems

    # ── Coordinate pick mode (for Advanced Alignment) ─────────

    def enter_pick_mode(self, axis: str):
        """Enter coordinate picking mode on the given axis.

        Args:
            axis: ``"x"`` for vertical line (picks X), ``"y"`` for horizontal (picks Y).
        """
        self._pick_axis = axis
        self.gv.setCursor(Qt.CursorShape.CrossCursor)
        self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
        scene_w = max(self.sceneRect().width() or 2000, 2000)
        scene_h = max(self.sceneRect().height() or 2000, 2000)
        self._pick_line = QGraphicsLineItem()
        pen = QPen(QColor(255, 0, 255), 0)  # magenta, cosmetic
        pen.setStyle(Qt.PenStyle.DashLine)
        self._pick_line.setPen(pen)
        self._pick_line.setZValue(200)  # above everything
        if axis == "y":
            self._pick_line.setLine(0, 0, scene_w, 0)
        else:
            self._pick_line.setLine(0, 0, 0, scene_h)
        self.addItem(self._pick_line)
        self._pick_line.hide()

    def exit_pick_mode(self):
        """Exit coordinate picking mode and clean up the guide line.

        Does NOT restore drag mode — call :meth:`restore_drag_mode`
        after the dialog fully closes so mouse events settle.
        """
        self._pick_axis = None
        self.gv.unsetCursor()
        if self._pick_line is not None:
            self.removeItem(self._pick_line)
            self._pick_line = None

    def restore_drag_mode(self):
        """Restore the normal canvas drag mode — NoDrag, because left-drag is
        the text-block box select, not a ScrollHandDrag pan — and the plain
        arrow cursor.

        ScrollHandDrag used to let Qt own the viewport cursor and mask stray
        tool cursors; with NoDrag the cursor must be reset explicitly, or a
        crosshair/pen cursor set by a tool stays stuck on the canvas.
        """
        self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.gv.setCursor(Qt.CursorShape.ArrowCursor)

    def is_picking(self) -> bool:
        """Return whether we are in coordinate picking mode."""
        return self._pick_axis is not None

    # ── Path-reorder mode ───────────────────────────────────────

    def enterReorderMode(self):
        """Enter path-drawing reorder mode.

        The user draws one or more strokes across text blocks on the canvas.
        Blocks are reordered in the sequence they are first touched.
        """
        self._reorder_mode = True
        self._reorder_drawing = False
        self._reorder_path = QPainterPath()
        self._reorder_touched_blocks.clear()
        self.gv.setCursor(Qt.CursorShape.CrossCursor)
        self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)

        # Create visual path item
        pen = QPen(QColor(52, 152, 219, 150), 4)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._reorder_path_item = QGraphicsPathItem()
        self._reorder_path_item.setPen(pen)
        self._reorder_path_item.setZValue(150)  # above textLayer children
        self._reorder_path_item.setParentItem(self.textLayer)
        self._reorder_path_item.hide()
        self.addItem(self._reorder_path_item)

    def exitReorderMode(self):
        """Exit path-drawing reorder mode and clean up."""
        self._reorder_mode = False
        self._reorder_drawing = False
        self._reorder_path = QPainterPath()
        self._reorder_touched_blocks.clear()
        self.gv.unsetCursor()
        self.restore_drag_mode()

        # Remove visual path item
        if self._reorder_path_item is not None:
            self.removeItem(self._reorder_path_item)
            self._reorder_path_item = None

        # Reset reorder badge state on all text blocks
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                if item._reorder_seq >= 0:
                    item._reorder_seq = -1
                    item.setSelected(False)
                    item.update()

    def _reorder_start_stroke(self, scene_pos: QPointF):
        """Begin a new reorder path stroke at *scene_pos*."""
        self._reorder_drawing = True
        self._reorder_path = QPainterPath()
        # Map from scene coords to textLayer local coords so the path
        # and absBoundingRect() are in the same coordinate space.
        self._reorder_path.moveTo(self.textLayer.mapFromScene(scene_pos))

    def _reorder_extend_stroke(self, scene_pos: QPointF):
        """Extend the current reorder stroke and check intersections."""
        # Map to textLayer local coords to match absBoundingRect() space
        local_pos = self.textLayer.mapFromScene(scene_pos)
        self._reorder_path.lineTo(local_pos)

        # Build a stroked (wide) version for collision detection.
        # Scale brush radius inversely with zoom so the visual hit
        # area stays consistent regardless of zoom level.
        stroker = QPainterPathStroker()
        stroker.setWidth(self._reorder_brush_radius * 2 / self.scale_factor)
        stroked = stroker.createStroke(self._reorder_path)

        # Check un-touched blocks
        for item in self.textLayer.childItems():
            if not isinstance(item, TextBlkItem):
                continue
            if any(item.blk is blk for blk in self._reorder_touched_blocks):
                continue
            br = item.absBoundingRect(qrect=True)
            if stroked.intersects(br):
                self._reorder_touched_blocks.append(item.blk)
                item.setSelected(True)
                item._reorder_seq = len(self._reorder_touched_blocks) - 1
                item.update()

        # Update visual path
        if self._reorder_path_item is not None:
            self._reorder_path_item.setPath(self._reorder_path)
            if not self._reorder_path_item.isVisible():
                self._reorder_path_item.show()

    def _reorder_end_stroke(self):
        """Finalize current stroke and emit results."""
        self._reorder_drawing = False
        # Build index from ALL canvas text items (includes unsaved
        # new blocks that aren't yet in imgtrans_proj.pages)
        canvas_blocks = [
            item.blk
            for item in self.textLayer.childItems()
            if isinstance(item, TextBlkItem)
        ]
        idx_map = {id(blk): i for i, blk in enumerate(canvas_blocks)}
        touched_ids = [
            idx_map[id(blk)]
            for blk in self._reorder_touched_blocks
            if id(blk) in idx_map
        ]
        self.reorder_path_finished.emit(touched_ids)

    # ── Snap guides ────────────────────────────────────────────

    def clear_snap_guides(self):
        """Clear all snap guide lines."""
        self.snap_guide_item.clear_guides()

    def set_snap_guides(self, guides: List[QLineF]):
        """Set snap guide lines through the overlay item."""
        self.snap_guide_item.set_guides(guides)

    def handle_ctrlv(self) -> bool:
        if not self.textEditMode():
            return False
        if (
            self.editing_textblkitem is not None
            and self.editing_textblkitem.isEditing()
        ):
            return False
        self.on_paste()
        return True

    def handle_ctrlc(self):
        if not self.textEditMode():
            return False
        if (
            self.editing_textblkitem is not None
            and self.editing_textblkitem.isEditing()
        ):
            return False
        self.on_copy()
        return True

    def scene_cursor_pos(self):
        origin = self.gv.mapFromGlobal(QCursor.pos())
        return self.gv.mapToScene(origin)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        btn = event.button()

        # Quick-menu safety net: a press that reaches the canvas necessarily
        # landed OUTSIDE the pie-menu window (it is a separate always-on-top
        # window, so on-menu presses never arrive here) — close it.  This is
        # the guaranteed path for "左键点空白关闭"; the app-level click-outside
        # filter in MainWindow is the first attempt, this catches any press
        # that still slips through for any reason (2026-08-18).
        self._dismiss_open_pie_menu()

        # Coordinate picking mode — capture scene X or Y on any click
        if self._pick_axis is not None and btn == Qt.MouseButton.LeftButton:
            pos = event.scenePos()
            val = round(pos.y() if self._pick_axis == "y" else pos.x())
            self.position_picked.emit(val)
            return

        # Path-reorder mode — start drawing a stroke
        if self._reorder_mode and btn == Qt.MouseButton.LeftButton:
            self._reorder_start_stroke(event.scenePos())
            return

        if btn == Qt.MouseButton.MiddleButton:
            self.mid_btn_pressed = True
            self.pan_initial_pos = event.screenPos()
            return

        if self.imgtrans_proj.img_valid:
            # Text-block creation mode (W / bottom-bar toggle, defaults ON via
            # pcfg.imgtrans_textblock): only the RIGHT press creates a new
            # block here — the LEFT press must fall through to the normal
            # box-select / block-move interaction below (2026-08-18), or the
            # whole left-drag gesture goes dead whenever nothing is selected.
            if (
                self.textblock_mode
                and len(self.selectedItems()) == 0
                and self.textEditMode()
                and btn == Qt.MouseButton.RightButton
            ):
                return self.startCreateTextblock(event.scenePos())

            if self.creating_normal_rect:
                if (
                    btn == Qt.MouseButton.RightButton
                    or btn == Qt.MouseButton.LeftButton
                ):
                    return self.startCreateTextblock(
                        event.scenePos(), hide_control=True
                    )

            elif btn == Qt.MouseButton.LeftButton:
                # user is drawing using the pen/inpainting tool
                if self.scale_tool_mode:
                    self.begin_scale_tool.emit(event.scenePos())
                elif self.painting:
                    self.addStrokeImageItem(
                        self.inpaintLayer.mapFromScene(event.scenePos()),
                        self.painting_pen,
                    )
                elif self.textEditMode():
                    # Left-drag = text-block box select (2026-08-18): plain
                    # presses start the rubber band; control handles keep
                    # their own drag (start_box_select decides).
                    self.start_box_select(event.scenePos())

            elif btn == Qt.MouseButton.RightButton:
                # user is drawing using eraser
                if self.painting:
                    erasing = self.image_edit_mode == ImageEditMode.PenTool
                    self.addStrokeImageItem(
                        self.inpaintLayer.mapFromScene(event.scenePos()),
                        self.erasing_pen,
                        erasing,
                    )

        if btn == Qt.MouseButton.LeftButton and self.txtblkShapeControl.isVisible():
            items_at = self.items(event.scenePos())
            # Keep the editing overlays (shape handles / grid handles) alive
            # when the press lands on one of them; only an empty-space click
            # clears the control frames.
            if not any(
                isinstance(item, TextBlkItem)
                or item.data(CONTROL_ITEM_DATA_KEY)
                for item in items_at
            ):
                self.txtblkShapeControl.setBlkItem(None)
                self.clear_text_transform_controls()

        return super().mousePressEvent(event)

    @property
    def creating_normal_rect(self):
        return self.image_edit_mode == ImageEditMode.RectTool and self.editor_index == 0

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        btn = event.button()

        # Path-reorder mode — finish stroke and emit results
        if self._reorder_drawing and btn == Qt.MouseButton.LeftButton:
            self._reorder_end_stroke()
            return

        box_selecting = self.rubber_band_dragged
        self.hide_rubber_band()

        if btn == Qt.MouseButton.MiddleButton:
            self.mid_btn_pressed = False
        textblk_created = False
        if self.creating_textblock:
            tgt = 0 if btn == Qt.MouseButton.LeftButton else 1
            textblk_created = self.endCreateTextblock(btn=tgt)
        if btn == Qt.MouseButton.RightButton:
            if self.stroke_img_item is not None:
                self.finish_erasing.emit(self.stroke_img_item)
            if self.textEditMode() and not textblk_created:
                self.context_menu_requested.emit(event.screenPos(), False)
        if btn == Qt.MouseButton.LeftButton:
            if self.stroke_img_item is not None:
                self.finish_painting.emit(self.stroke_img_item)
            elif self.scale_tool_mode:
                self.end_scale_tool.emit()
            if box_selecting:
                # A real box drag just replaced the selection — re-bind the
                # shape control so it no longer points at the pressed block.
                self._sync_shape_control_to_selection()
        return super().mouseReleaseEvent(event)

    def updateCanvas(self):
        self.editing_textblkitem = None
        self.stroke_img_item = None
        self.erase_img_key = None
        self.txtblkShapeControl.setBlkItem(None)
        self.clear_text_transform_controls()
        self.mid_btn_pressed = False
        self.search_widget.reInitialize()

        self.clearSelection()
        self.setProjSaveState(False)
        self.updateLayers()

        if self.base_pixmap is not None:
            pixmap = self.base_pixmap.copy()
            pixmap.fill(Qt.GlobalColor.transparent)
            self.textLayer.setPixmap(pixmap)

            im_rect = pixmap.rect()
            self.baseLayer.setRect(QRectF(im_rect))
            if pcfg.open_image_fit_window and self._fit_to_window:
                self._fitToWindow()
            else:
                self.scaleImage(1)
            self._fit_to_window = False  # reset after use

        self.setDrawingLayer()

    def setDrawingLayer(self, img: Union[QPixmap, np.ndarray] = None):

        self.drawingLayer.clearAllDrawings()

        if not self.imgtrans_proj.img_valid:
            return
        if img is None:
            drawing_map = self.inpaintLayer.pixmap().copy()
            drawing_map.fill(Qt.GlobalColor.transparent)
        elif not isinstance(img, QPixmap):
            drawing_map = ndarray2pixmap(img)
        else:
            drawing_map = img
        self.drawingLayer.setPixmap(drawing_map)

    def setPaintMode(self, painting: bool):
        if painting:
            self.editing_textblkitem = None
            self.textblock_mode = False
        else:
            # Leaving draw mode returns the canvas to the plain arrow cursor
            # (NoDrag — see restore_drag_mode for why the cursor is set here).
            self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.gv.setCursor(Qt.CursorShape.ArrowCursor)
            self.image_edit_mode = ImageEditMode.NONE

    @property
    def painting(self):
        return (
            self.image_edit_mode == ImageEditMode.PenTool
            or self.image_edit_mode == ImageEditMode.InpaintTool
        )

    def setMaskTransparencyBySlider(self, slider_value: int):
        self.setMaskTransparency(slider_value / 100)

    def setOriginalTransparencyBySlider(self, slider_value: int):
        self.setOriginalTransparency(slider_value / 100)

    def setTextLayerTransparencyBySlider(self, slider_value: int):
        self.setTextLayerTransparency(slider_value / 100)

    def setTextBlockMode(self, mode: bool):
        self.textblock_mode = mode

    def on_create_contextmenu(self, pos: QPoint, is_textpanel: bool):
        if self.textEditMode() and not self.creating_textblock:
            from .context_menu_config import build_context_menu

            build_context_menu(self, pos)

    @property
    def have_selected_blkitem(self):
        return len(self.selected_text_items()) > 0

    def on_paste(self, p: QPointF = None):
        if self.textEditMode():
            if p is None:
                p = self.scene_cursor_pos()
            if self.have_selected_blkitem:
                self.paste2selected_textitems.emit()
            else:
                self.paste_textblks.emit(p)

    def on_copy(self):
        if self.textEditMode():
            if self.have_selected_blkitem:
                self.copy_textblks.emit()

    def hide_rubber_band(self):
        if self.rubber_band.isVisible():
            self.rubber_band.hide()
            self.rubber_band_origin = None
        self.rubber_band_dragged = False

    # ── Left-drag box select (text blocks) ────────────────────

    def start_box_select(self, scene_pos: QPointF):
        """Begin the text-block rubber band at *scene_pos*.

        Left-drag on empty canvas is the box select (2026-08-18); left-drag on
        a text block is the MOVE gesture — the press lands on the block's
        native ``ItemIsMovable`` drag, so no rubber band starts and the block
        (or its whole selection) follows the mouse.  Control handles (shape
        resize/rotate, grid node) keep their own drag, and a block being
        text-edited keeps the text cursor.
        """
        items_at = self.items(scene_pos)
        if any(
            isinstance(item, (ControlBlockItem, GridControlPointItem))
            for item in items_at
        ):
            return  # control handles keep their own drag
        if any(isinstance(item, TextBlkItem) for item in items_at):
            return  # block press = move gesture — native ItemIsMovable drag
        self.rubber_band_origin = scene_pos
        self.rubber_band.setGeometry(
            QRectF(self.rubber_band_origin, self.rubber_band_origin).normalized()
        )
        self.rubber_band.show()
        self.rubber_band.setZValue(1)
        self.rubber_band_dragged = False

    def apply_box_selection(self, rect: QRectF, additive: bool = False):
        """Select the text blocks intersected by *rect* — and only those.

        Non-additive selection replaces the current selection; additive
        (Ctrl/Shift held) keeps it and adds the boxed blocks.
        """
        if not additive:
            self.clearSelection()
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem) and rect.intersects(
                item.sceneBoundingRect()
            ):
                item.setSelected(True)

    def _sync_shape_control_to_selection(self):
        """Re-bind the shape control after a box drag replaced the selection.

        A box drag on a block would otherwise leave the outline pointing at
        the pressed block even after the box select deselected it.
        """
        sel = self.selected_text_items()
        if len(sel) == 1:
            self.txtblkShapeControl.setBlkItem(sel[0])
        else:
            self.txtblkShapeControl.setBlkItem(None)

    def _dismiss_open_pie_menu(self):
        """Close the quick menu if one is open above this canvas.

        Called from :meth:`mousePressEvent`: a press that reaches the scene
        proves it landed outside the pie-menu window (the menu is a separate
        always-on-top window), so the menu must not stay stranded (2026-08-18).
        """
        view = self.gv
        win = view.window() if view is not None else None
        pm = getattr(win, "pie_menu", None)
        if pm is not None and pm.is_open():
            pm.cancel()

    def on_hide_canvas(self):
        self.clear_states()

    def on_activation_changed(self):
        self.clear_states()
        for textitem in self.selected_text_items():
            if textitem.isEditing():
                self.editing_textblkitem = textitem

    def clear_states(self):
        self.creating_textblock = False
        self.create_block_origin = None
        self.editing_textblkitem = None
        self.gv.ctrl_pressed = False
        if self.stroke_img_item is not None:
            self.removeItem(self.stroke_img_item)

    def setProjSaveState(self, un_saved: bool):
        if un_saved == self.projstate_unsaved:
            return
        else:
            self.projstate_unsaved = un_saved
            self.proj_savestate_changed.emit(un_saved)

    def removeItem(self, item: QGraphicsItem) -> None:
        self.block_selection_signal = True
        super().removeItem(item)
        if isinstance(item, StrokeImgItem):
            item.setParentItem(None)
            self.stroke_img_item = None
            self.erase_img_key = None
        self.block_selection_signal = False

    def get_active_undostack(self) -> QUndoStack:
        if self.textEditMode():
            return self.text_undo_stack
        elif self.drawMode():
            return self.draw_undo_stack
        return None

    def push_undo_command(self, command: QUndoCommand, update_pushed_step=True):
        if self.textEditMode():
            self.push_text_command(command, update_pushed_step)
        elif self.drawMode():
            self.push_draw_command(command, update_pushed_step)
        else:
            return

    def push_draw_command(self, command: QUndoCommand, update_pushed_step=True):
        if command is not None:
            self.draw_undo_stack.push(command)
        if update_pushed_step:
            self.num_pushed_drawstep += 1
            self.on_drawstack_changed()

    def push_text_command(self, command: QUndoCommand, update_pushed_step=True):
        if command is not None:
            self.text_undo_stack.push(command)
        if update_pushed_step:
            self.num_pushed_textstep += 1
            self.on_textstack_changed()

    def on_drawstack_changed(self):
        if (
            self.num_pushed_drawstep != self.saved_drawundo_step
            or self.num_pushed_textstep != self.saved_textundo_step
        ):
            self.setProjSaveState(True)
        else:
            self.setProjSaveState(False)

    def on_textstack_changed(self):
        if (
            self.num_pushed_textstep != self.saved_textundo_step
            or self.num_pushed_drawstep != self.saved_drawundo_step
        ):
            self.setProjSaveState(True)
        else:
            self.setProjSaveState(False)
        self.textstack_changed.emit()

    def redo_textedit(self):
        self.num_pushed_textstep += 1
        self.text_undo_stack.redo()

    def undo_textedit(self):
        if self.num_pushed_textstep > 0:
            self.num_pushed_textstep -= 1
        self.text_undo_stack.undo()

    def redo(self):
        if self.textEditMode():
            undo_stack = self.text_undo_stack
            self.num_pushed_textstep += 1
            self.on_textstack_changed()
        elif self.drawMode():
            undo_stack = self.draw_undo_stack
            self.num_pushed_drawstep += 1
            self.on_drawstack_changed()
        else:
            return
        if undo_stack is not None:
            undo_stack.redo()
            if undo_stack == self.text_undo_stack:
                self.txtblkShapeControl.updateBoundingRect()

    def undo(self):
        if self.textEditMode():
            undo_stack = self.text_undo_stack
            if self.num_pushed_textstep > 0:
                self.num_pushed_textstep -= 1
            self.on_textstack_changed()
        elif self.drawMode():
            undo_stack = self.draw_undo_stack
            if self.num_pushed_drawstep > 0:
                self.num_pushed_drawstep -= 1
            self.on_drawstack_changed()
        else:
            return
        if undo_stack is not None:
            undo_stack.undo()
            if undo_stack == self.text_undo_stack:
                self.txtblkShapeControl.updateBoundingRect()

    def clear_undostack(self, update_saved_step=False):
        if update_saved_step:
            self.saved_drawundo_step = 0
            self.saved_textundo_step = 0
            self.num_pushed_textstep = 0
            self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()
        self.text_undo_stack.clear()

    def clear_text_stack(self):
        self.num_pushed_textstep = 0
        self.text_undo_stack.clear()

    def clear_draw_stack(self):
        self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()

    def update_saved_undostep(self):
        self.saved_drawundo_step = self.num_pushed_drawstep
        self.saved_textundo_step = self.num_pushed_textstep

    def text_change_unsaved(self) -> bool:
        return self.saved_textundo_step != self.num_pushed_textstep

    def draw_change_unsaved(self) -> bool:
        return self.saved_drawundo_step != self.num_pushed_drawstep

    def prepareClose(self):
        self.blockSignals(True)
        self.text_undo_stack.blockSignals(True)
        self.draw_undo_stack.blockSignals(True)
