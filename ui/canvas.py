import os
from typing import List, Union

import numpy as np
from qtpy.QtCore import QDateTime, QLineF, QPoint, QPointF, QRectF, QSizeF, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QCursor,
    QHideEvent,
    QKeyEvent,
    QKeySequence,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from qtpy.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneDragDropEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QLabel,
    QMenu,
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
from .texteditshapecontrol import ControlBlockItem, TextBlkShapeControl
from .textitem import TextBlkItem, TextBlock

CANVAS_SCALE_MAX = 10.0
CANVAS_SCALE_MIN = 0.01
CANVAS_SCALE_SPEED = 0.1


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
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

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
                # self.ctrlv_pressed.emit(e)
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
        if self.canvas is not None and self.canvas.preview_mode:
            painter = QPainter(self.viewport())
            pen = QPen(QColor("#e67e22"), 5)
            painter.setPen(pen)
            r = self.viewport().rect()
            painter.drawRect(r.adjusted(2, 2, -3, -3))
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
    layout_textblks = Signal()
    reset_angle = Signal()
    squeeze_blk = Signal()
    # 请求整理换行：squeeze=True 时同时收缩框
    normalize_break_requested = Signal(bool)

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
    context_menu_requested = Signal(QPoint, bool)
    incanvas_selection_changed = Signal()
    align_textblks = Signal(str)
    position_picked = Signal(int)
    switch_text_item = Signal(int, QKeyEvent)

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
        # self.default_cursor = self.gv.cursor()
        self.rubber_band = self.addWidget(QRubberBand(QRubberBand.Shape.Rectangle))
        self.rubber_band.hide()
        self.rubber_band_origin = None

        self.draw_undo_stack = QUndoStack(self)
        self.text_undo_stack = QUndoStack(self)
        self.saved_drawundo_step = 0
        self.saved_textundo_step = 0

        self.scaleFactorLabel = FadeLabel(self.gv)
        self.scaleFactorLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scaleFactorLabel.setText("100%")
        self.scaleFactorLabel.gv = self.gv

        self.previewLabel = QLabel(self.gv)
        self.previewLabel.setObjectName("PreviewLabel")
        self.previewLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previewLabel.setText(self.tr("PREVIEW"))
        self.previewLabel.adjustSize()
        self.previewLabel.setVisible(False)

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
        self.previewLayer = QGraphicsPixmapItem()
        self.previewLayer.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        self.previewLayer.setVisible(False)
        self.preview_mode = False

        self.inpaintLayer.setAcceptDrops(True)
        self.drawingLayer.setAcceptDrops(True)
        self.textLayer.setAcceptDrops(True)
        self.baseLayer.setAcceptDrops(True)

        self.base_pixmap: QPixmap = None

        self.addItem(self.baseLayer)
        self.inpaintLayer.setParentItem(self.baseLayer)
        self.drawingLayer.setParentItem(self.baseLayer)
        self.textLayer.setParentItem(self.baseLayer)
        self.previewLayer.setParentItem(self.baseLayer)
        self.txtblkShapeControl.setParentItem(self.baseLayer)

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
        self.block_selection_signal = False
        self._fit_to_window = True  # fit-to-window flag, set by MainWindow before updateCanvas()

        im_rect = QRectF(0, 0, C.SCREEN_W, C.SCREEN_H)
        self.baseLayer.setRect(im_rect)

    def on_switch_item(self, switch_delta: int, key_event: QKeyEvent = None):
        if self.textEditMode():
            self.switch_text_item.emit(switch_delta, key_event)

    def img_window_size(self):
        if self.imgtrans_proj.inpainted_valid:
            return self.inpaintLayer.pixmap().size()
        return self.baseLayer.rect().size().toSize()

    def dragEnterEvent(self, e: QGraphicsSceneDragDropEvent):

        self.drop_folder = None
        if e.mimeData().hasUrls():
            urls = e.mimeData().urls()
            ufolder = None
            for url in urls:
                furl = url.toLocalFile()
                if os.path.isdir(furl):
                    ufolder = furl
                    break
            if ufolder is not None:
                e.acceptProposedAction()
                self.drop_folder = ufolder

    def dropEvent(self, event) -> None:
        if self.drop_folder is not None:
            self.drop_open_folder.emit(self.drop_folder)
            self.drop_folder = None
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

    def _set_scene_scale(self, scale: float):
        self.scale_factor = scale
        self.baseLayer.setScale(scale)
        self.setSceneRect(
            0,
            0,
            self.baseLayer.sceneBoundingRect().width(),
            self.baseLayer.sceneBoundingRect().height(),
        )

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

        result = ndarray2pixmap(self.imgtrans_proj.inpainted_array, return_qimg=True)
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

    def toggle_preview(self):
        self.preview_mode = not self.preview_mode
        if self.preview_mode:
            if not self.imgtrans_proj or not self.imgtrans_proj.img_valid:
                self.preview_mode = False
                return
            self._enter_preview()
        else:
            self._exit_preview()

    def _enter_preview(self):
        scale_before = self.scale_factor
        tlayer_opacity_before = self.textLayer.opacity()
        tlayer_visible = self.textLayer.isVisible()
        txcontrol_visible = self.txtblkShapeControl.isVisible()
        inpaint_visible = self.inpaintLayer.isVisible()
        self._tlayer_was_visible = tlayer_visible
        self._txcontrol_was_visible = txcontrol_visible

        self.textLayer.setOpacity(1)
        if not tlayer_visible:
            self.textLayer.show()
        if not inpaint_visible:
            self.inpaintLayer.show()

        # Render at 1:1 scale without changing the viewport
        if scale_before != 1:
            self.baseLayer.setScale(1)

        self.clearSelection()
        if self.txtblkShapeControl.blk_item is not None:
            blk_item = self.txtblkShapeControl.blk_item
            if blk_item.is_editting():
                blk_item.endEdit(keep_focus=False)
            if blk_item.isSelected():
                blk_item.setSelected(False)

        # Hide shape control BEFORE rendering so it's not captured in the preview
        self.txtblkShapeControl.setVisible(False)

        result = ndarray2pixmap(self.imgtrans_proj.inpainted_array, return_qimg=False)
        canvas_sz = self.img_window_size()
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0, 0, canvas_sz.width(), canvas_sz.height())
        self.render(painter, rect, rect)
        painter.end()

        if scale_before != 1:
            self.baseLayer.setScale(scale_before)

        self.textLayer.setOpacity(tlayer_opacity_before)
        if not tlayer_visible:
            self.textLayer.hide()
        if not inpaint_visible:
            self.inpaintLayer.hide()

        self.previewLayer.setPixmap(result)
        self.previewLayer.setVisible(True)
        self.textLayer.setVisible(False)

        self.previewLabel.setVisible(True)
        self.previewLabel.raise_()
        self.previewLabel.adjustSize()

        self.gv.viewport().update()

    def _exit_preview(self):
        self.previewLayer.setVisible(False)
        if self._txcontrol_was_visible:
            self.txtblkShapeControl.setVisible(True)
        if self._tlayer_was_visible:
            self.textLayer.setVisible(True)

        self.previewLabel.setVisible(False)

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
        self.setSceneRect(
            0,
            0,
            self.baseLayer.sceneBoundingRect().width(),
            self.baseLayer.sceneBoundingRect().height(),
        )

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
        self.setSceneRect(
            0,
            0,
            self.baseLayer.sceneBoundingRect().width(),
            self.baseLayer.sceneBoundingRect().height(),
        )

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

        plw = self.previewLabel.width()
        self.previewLabel.move((gv_w - plw) // 2, 12)

    def onScaleFactorChanged(self):
        self.scaleFactorLabel.setText(f"{self.scale_factor * 100:2.0f}%")
        self.scaleFactorLabel.raise_()
        self.scaleFactorLabel.startFadeAnimation()

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
            sel_path = QPainterPath(self.rubber_band_origin)
            sel_path.addRect(self.rubber_band.geometry())
            if C.FLAG_QT6:
                self.setSelectionArea(
                    sel_path, deviceTransform=self.gv.viewportTransform()
                )
            else:
                self.setSelectionArea(
                    sel_path,
                    Qt.ItemSelectionMode.IntersectsItemBoundingRect,
                    self.gv.viewportTransform(),
                )

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
        """Restore the normal ScrollHandDrag panning mode."""
        self.gv.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def is_picking(self) -> bool:
        """Return whether we are in coordinate picking mode."""
        return self._pick_axis is not None

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

        # Coordinate picking mode — capture scene X or Y on any click
        if self._pick_axis is not None and btn == Qt.MouseButton.LeftButton:
            pos = event.scenePos()
            val = round(pos.y() if self._pick_axis == "y" else pos.x())
            self.position_picked.emit(val)
            return

        if btn == Qt.MouseButton.MiddleButton:
            self.mid_btn_pressed = True
            self.pan_initial_pos = event.screenPos()
            return

        if self.imgtrans_proj.img_valid:
            if (
                self.textblock_mode
                and len(self.selectedItems()) == 0
                and self.textEditMode()
            ):
                if btn == Qt.MouseButton.RightButton:
                    return self.startCreateTextblock(event.scenePos())
            elif self.creating_normal_rect:
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

            elif btn == Qt.MouseButton.RightButton:
                # user is drawing using eraser
                if self.painting:
                    erasing = self.image_edit_mode == ImageEditMode.PenTool
                    self.addStrokeImageItem(
                        self.inpaintLayer.mapFromScene(event.scenePos()),
                        self.erasing_pen,
                        erasing,
                    )
                else:  # rubber band selection
                    self.rubber_band_origin = event.scenePos()
                    self.rubber_band.setGeometry(
                        QRectF(
                            self.rubber_band_origin, self.rubber_band_origin
                        ).normalized()
                    )
                    self.rubber_band.show()
                    self.rubber_band.setZValue(1)

        if btn == Qt.MouseButton.LeftButton and self.txtblkShapeControl.isVisible():
            items_at = self.items(event.scenePos())
            if not any(
                isinstance(item, (TextBlkItem, ControlBlockItem)) for item in items_at
            ):
                self.txtblkShapeControl.setBlkItem(None)

        return super().mousePressEvent(event)

    @property
    def creating_normal_rect(self):
        return self.image_edit_mode == ImageEditMode.RectTool and self.editor_index == 0

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        btn = event.button()

        self.hide_rubber_band()

        Qt.MouseButton.LeftButton
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
        return super().mouseReleaseEvent(event)

    def updateCanvas(self):
        self.editing_textblkitem = None
        self.stroke_img_item = None
        self.erase_img_key = None
        self.txtblkShapeControl.setBlkItem(None)
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
            # self.gv.setCursor(self.default_cursor)
            self.gv.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
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
            menu = QMenu(self.gv)
            copy_act = menu.addAction(self.tr("Copy"))
            copy_act.setShortcut(QKeySequence.StandardKey.Copy)
            paste_act = menu.addAction(self.tr("Paste"))
            paste_act.setShortcut(QKeySequence.StandardKey.Paste)
            delete_act = menu.addAction(self.tr("Delete"))
            delete_act.setShortcut(QKeySequence("Ctrl+D"))
            copy_src_act = menu.addAction(self.tr("Copy source text"))
            copy_src_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
            paste_src_act = menu.addAction(self.tr("Paste source text"))
            paste_src_act.setShortcut(QKeySequence("Ctrl+Shift+V"))

            menu.addSeparator()

            angle_act = menu.addAction(self.tr("Reset Angle"))
            squeeze_act = menu.addAction(self.tr("Squeeze"))

            # 整理换行：仅对选中的横排块可用
            selected = self.selected_text_items()
            norm_enabled = any(not b.blk.vertical for b in selected)
            norm_act = menu.addAction(self.tr("整理换行"))
            norm_sq_act = menu.addAction(self.tr("整理换行并收缩框"))
            norm_act.setEnabled(norm_enabled)
            norm_sq_act.setEnabled(norm_enabled)

            menu.addSeparator()

            # --- Alignment submenu ---
            n_selected = len(self.selected_text_items())
            align_menu = menu.addMenu(self.tr("Align"))
            align_menu.setEnabled(n_selected >= 2)

            align_left_act = align_menu.addAction(self.tr("Align Left Edges"))
            align_right_act = align_menu.addAction(self.tr("Align Right Edges"))
            align_top_act = align_menu.addAction(self.tr("Align Top Edges"))
            align_bottom_act = align_menu.addAction(self.tr("Align Bottom Edges"))
            align_menu.addSeparator()
            align_hc_act = align_menu.addAction(self.tr("Align Horizontal Centers"))
            align_vc_act = align_menu.addAction(self.tr("Align Vertical Centers"))
            align_menu.addSeparator()
            dist_h_act = align_menu.addAction(self.tr("Distribute Horizontally"))
            dist_v_act = align_menu.addAction(self.tr("Distribute Vertically"))

            snap_act = menu.addAction(self.tr("Snap Alignment"))
            snap_act.setCheckable(True)
            snap_act.setChecked(self.alignment_enabled)

            menu.addSeparator()

            translate_act = menu.addAction(self.tr("translate"))
            ocr_act = menu.addAction(self.tr("OCR"))
            ocr_translate_act = menu.addAction(self.tr("OCR and translate"))
            ocr_translate_inpaint_act = menu.addAction(
                self.tr("OCR, translate and inpaint")
            )

            rst = menu.exec(pos)

            if rst == delete_act:
                self.delete_textblks.emit(0)
            elif rst == copy_act:
                self.on_copy()
            elif rst == paste_act:
                self.on_paste()
            elif rst == copy_src_act:
                self.copy_src_signal.emit()
            elif rst == paste_src_act:
                self.paste_src_signal.emit()
            elif rst == angle_act:
                self.reset_angle.emit()
            elif rst == squeeze_act:
                self.squeeze_blk.emit()
            elif rst == norm_act:
                self.normalize_break_requested.emit(False)
            elif rst == norm_sq_act:
                self.normalize_break_requested.emit(True)
            elif rst == align_left_act:
                self.align_textblks.emit("left")
            elif rst == align_right_act:
                self.align_textblks.emit("right")
            elif rst == align_top_act:
                self.align_textblks.emit("top")
            elif rst == align_bottom_act:
                self.align_textblks.emit("bottom")
            elif rst == align_hc_act:
                self.align_textblks.emit("hcenter")
            elif rst == align_vc_act:
                self.align_textblks.emit("vcenter")
            elif rst == dist_h_act:
                self.align_textblks.emit("dist_h")
            elif rst == dist_v_act:
                self.align_textblks.emit("dist_v")
            elif rst == snap_act:
                self.alignment_enabled = snap_act.isChecked()
            elif rst == translate_act:
                self.run_blktrans.emit(-1)
            elif rst == ocr_act:
                self.run_blktrans.emit(0)
            elif rst == ocr_translate_act:
                self.run_blktrans.emit(1)
            elif rst == ocr_translate_inpaint_act:
                self.run_blktrans.emit(2)

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
