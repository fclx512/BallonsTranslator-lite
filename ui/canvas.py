import os
from typing import List, Union

import numpy as np
from qtpy.QtCore import QDateTime, QCoreApplication, QLineF, QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
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
    QCheckBox,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneDragDropEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QMessageBox,
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

from .custom_widget import ScrollBar
from .custom_widget.notification import notification
from .image_edit import DrawingLayer, ImageEditMode, StrokeImgItem
from .misc import ARROWKEY2DIRECTION, QKEY, ndarray2pixmap
from .page_search_widget import PageSearchWidget
from .textedit_commands import (
    FormatGestureCommand,
    TypingSessionCommand,
    command_page_stale,
    replay_guard,
    resolve_blk_item,
)
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
# 编辑会话（键入会话/格式化手势）的空闲收口时长：最后一次变更静默这么久
# 即闭合落账。只作悬开兜底，常规边界（选区变化/失焦/清栈/撤销/新命令
# 推送）即时闭合，见 commit_edit_sessions 的调用点。
_EDIT_SESSION_IDLE_MS = 1500
OVERFLOW_MARGIN_RATIO = 0.3  # 过界模式场景扩展比例
# Minimum drag (screen pixels, scaled by zoom) before a left-drag turns from
# a click into a text-block box select (2026-08-18).
MIN_RUBBER_BAND_DRAG = 4.0


def _segment_rect_entry(
    start: QPointF,
    end: QPointF,
    rect: QRectF,
    padding: float = 0.0,
) -> float:
    """Return the normalized position (0..1) where a segment enters a rect.

    Ported from upstream v1.5.12 (``ballontranslator/ui/canvas.py``) so
    path-reorder numbering follows the drawn stroke's travel direction: a
    fast drag that crosses several blocks in one frame numbers them by
    entry order instead of scene z-order.  ``padding`` widens the rect on
    all sides to match the stroke brush half-width.
    """
    padding = max(0.0, padding)
    rect = rect.adjusted(-padding, -padding, padding, padding)
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    entry = 0.0
    exit_ = 1.0
    for origin, delta, lower, upper in (
        (start.x(), dx, rect.left(), rect.right()),
        (start.y(), dy, rect.top(), rect.bottom()),
    ):
        if abs(delta) < 1e-9:
            if origin < lower or origin > upper:
                return 1.0
            continue
        near = (lower - origin) / delta
        far = (upper - origin) / delta
        if near > far:
            near, far = far, near
        entry = max(entry, near)
        exit_ = min(exit_, far)
        if entry > exit_:
            return 1.0
    return max(0.0, min(1.0, entry))


class MoveByKeyCommand(QUndoCommand):
    def __init__(
        self,
        blkitems: List[TextBlkItem],
        direction: QPointF,
        shape_ctrl: TextBlkShapeControl,
    ) -> None:
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Move Text Blocks")
        )
        self.blkitems = blkitems
        self.blks = [it.blk for it in blkitems]
        self.direction = direction
        self.ori_pos_list = []
        self.end_pos_list = []
        self.shape_ctrl = shape_ctrl
        for blk in blkitems:
            pos = blk.pos()
            self.ori_pos_list.append(pos)
            self.end_pos_list.append(pos + direction)

    def _live_items(self):
        """跨页历史：按 blk 锚点重解析 live item；僵尸条目 None 占位。"""
        if command_page_stale(self, getattr(self, "_proj", None)):
            return [None] * len(self.blks)
        return [
            resolve_blk_item(blk, captured)
            for blk, captured in zip(self.blks, self.blkitems)
        ]

    def undo(self):
        for blk, pos in zip(self._live_items(), self.ori_pos_list):
            if blk is None:
                continue
            blk.setPos(pos)
            if blk.under_ctrl and self.shape_ctrl.blk_item == blk:
                self.shape_ctrl.updateBoundingRect()

    def redo(self):
        for blk, pos in zip(self._live_items(), self.end_pos_list):
            if blk is None:
                continue
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


def _blk_of_panel_edit(edit):
    """面板编辑器 → 配对 TextBlock（原文会话的跨页锚点）。"""
    try:
        pairw = edit.parentWidget()
        return getattr(pairw, "textblock", None)
    except RuntimeError:
        return None


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

    # 跨页撤销门请求跳页（mainwindow 接管切页，复用完整切页链路）
    page_jump_requested = Signal(str)

    # 组化命令撤销后请求重渲脏页（用户在确认弹窗勾选；mainwindow 接管）
    rerender_dirty_pages_requested = Signal()

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
        self.alignment_enabled = pcfg.snap_alignment
        self.snap_guide_item = SnapGuideItem()
        self.creating_textblock = False
        self._text_creation_cursor_active = False
        self._pick_axis = None  # "x" | "y" | None — canvas coordinate pick mode
        self._pick_line = None
        # Path-reorder mode (replaces grid-based Smart Reorder)
        self._reorder_mode = False
        self._reorder_drawing = False       # left button currently held
        self._reorder_path = QPainterPath()
        self._reorder_path_item: QGraphicsPathItem = None
        self._reorder_touched_blocks: List[TextBlkItem] = []  # touched TextBlkItems in contact order
        self._reorder_brush_radius = 20.0   # effective brush half-width (scene coords)
        self._reorder_last_local_pos: QPointF = None  # last stroke point in textLayer coords
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
        self.apply_undo_limit()

        # 编辑会话管理（撤销体系 3a 快照命令制）：键入会话与格式化手势在此
        # 聚拢，闭合时各落一条快照命令（TypingSessionCommand /
        # FormatGestureCommand），文档私有 undo 栈不再承担回退。
        # 手势/会话边界：选区变化、失焦、撤销重做入口、新命令推送、清栈、
        # 空闲定时器；见 docs/技术实现/撤销体系重构计划.md 4.5。
        self._typing_session = None   # dict | None，见 note_typing_edit
        self._format_gesture = None   # dict | None，见 note_formatting_edit
        self._suppress_undo_toast = False  # 历史面板跳转期间抑制撤回提示
        # 跨页撤销门：armed = (cmd, 'undo'|'redo')，等待二次确认（阶段 4）
        self._cross_undo_armed = None
        # 组化命令确认弹窗的「同时重渲染」勾选暂存（确认→撤销→emit 消费）
        self._group_undo_rerender = False
        self._edit_session_timer = QTimer(self)
        self._edit_session_timer.setSingleShot(True)
        self._edit_session_timer.setInterval(_EDIT_SESSION_IDLE_MS)
        self._edit_session_timer.timeout.connect(self.commit_edit_sessions)

        # 宿主挂 gv 而非 viewport：QAbstractScrollArea 滚动时对 viewport 做
        # 像素级 scroll（含子控件一并平移），缩放调整滚动条会把 toast 漂走；
        # gv 自身子控件不受 scroll 影响，坐标始终随窗口几何重排。
        notification.attach(self.gv)
        if self.imgtrans_proj is None or not self.imgtrans_proj.img_valid:
            notification.status(
                "empty-hint",
                self.tr("Drop images here or open a folder to start"),
                kind="hint",
                anchor="center",
            )

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

        self.saved_drawundo_step = 0
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

    def set_canvas_cursor(self, cursor) -> None:
        # Keep tool cursors in the scene hierarchy so child text and control
        # items can temporarily override them through Qt's native cursor rules.
        self.baseLayer.setCursor(cursor)

    def clear_canvas_cursor(self) -> None:
        if self.baseLayer.hasCursor():
            self.baseLayer.unsetCursor()

    def _clear_text_creation_cursor(self) -> None:
        self._text_creation_cursor_active = False
        if self.gv.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
            self.gv.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.gv.viewport().unsetCursor()

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
                item.refresh_seq_badge()
                # Re-evaluate stale overflow-clip state before painting the
                # export: a leftover flag would clip text to an old box and
                # draw the yellow overflow border into the result image.
                item.settle_overflow_state()

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
                item.refresh_seq_badge()

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

        if pcfg.use_notext_images and self.imgtrans_proj.notext_array is not None:
            notification.status("notext-bg", self.tr("No-text BG"))
        else:
            notification.status("notext-bg", None)

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

    def setOverflowMode(self, enabled: bool):
        """Toggle overflow mode on/off and update the canvas display."""
        pcfg.overflow_mode = enabled
        self.setSceneRect(self._overflow_scene_rect())
        self.gv.viewport().update()

    def onViewResized(self):
        gv_w = self.gv.geometry().width()

        x = gv_w - self.search_widget.width()
        pos = self.search_widget.pos()
        pos.setX(x - 30)
        self.search_widget.move(pos)

    def onScaleFactorChanged(self):
        notification.toast(
            f"{self.scale_factor * 100:2.0f}%",
            key="scale-factor",
            anchor="bottom-center",
            duration=1200,
        )

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
        if not self.imgtrans_proj.img_valid:
            notification.status(
                "empty-hint",
                self.tr("Drop images here or open a folder to start"),
                kind="hint",
                anchor="center",
            )
            self._clear_canvas()
        else:
            notification.status("empty-hint", None)

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
        # 选区变化 = 编辑会话边界：键入会话/格式化手势各落一条快照命令
        self.commit_edit_sessions()
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
        self._text_creation_cursor_active = self.textEditMode()
        self.create_block_origin = pos
        if self._text_creation_cursor_active:
            self.gv.viewport().setCursor(Qt.CursorShape.CrossCursor)
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
        if self._text_creation_cursor_active:
            self._clear_text_creation_cursor()
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

        result = super().mouseMoveEvent(event)
        if self._text_creation_cursor_active:
            # Creation is a modal drag, so it overrides child text cursors.
            self.gv.viewport().setCursor(Qt.CursorShape.CrossCursor)
        return result

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
        self.set_canvas_cursor(Qt.CursorShape.CrossCursor)
        self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)

        # The crosshair must stay visible while a stroke is drawn OVER text
        # blocks.  A cursor set on the view widget does not survive
        # QGraphicsScene's cursor resolution, which resets the viewport to
        # the default arrow on the first mouse move (real window).  Hanging
        # it on baseLayer — the scene item that covers the canvas — keeps it
        # there, mirroring aaad064's tool-cursor rule (2026-08-25).  Blocks'
        # own move cursors are dropped and _update_move_cursor refuses to
        # re-install one while reorder is active, so the crosshair shows
        # across the whole canvas.
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                item.unsetCursor()

        # Create visual path item
        pen = QPen(QColor(52, 152, 219, 150), 4)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._reorder_path_item = QGraphicsPathItem()
        self._reorder_path_item.setPen(pen)
        self._reorder_path_item.setZValue(150)  # above textLayer children
        self._reorder_path_item.setParentItem(self.textLayer)
        self._reorder_path_item.hide()

    def exitReorderMode(self):
        """Exit path-drawing reorder mode and clean up."""
        self._reorder_mode = False
        self._reorder_drawing = False
        self._reorder_path = QPainterPath()
        self._reorder_touched_blocks.clear()
        self._reorder_last_local_pos = None
        self.gv.unsetCursor()
        self.restore_drag_mode()
        self.clear_canvas_cursor()

        # Remove visual path item
        if self._reorder_path_item is not None:
            self.removeItem(self._reorder_path_item)
            self._reorder_path_item = None

        # Reset reorder badge state on all text blocks, and restore the
        # normal move cursor now that reorder mode is off (2026-08-25).
        for item in self.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                if item._reorder_seq >= 0:
                    item._reorder_seq = -1
                    item.setSelected(False)
                    item.refresh_seq_badge()
                item._update_move_cursor()

    def _reorder_start_stroke(self, scene_pos: QPointF):
        """Begin a new reorder path stroke at *scene_pos*."""
        self._reorder_drawing = True
        self._reorder_path = QPainterPath()
        # Map from scene coords to textLayer local coords so the path
        # and absBoundingRect() are in the same coordinate space.
        local_pos = self.textLayer.mapFromScene(scene_pos)
        self._reorder_path.moveTo(local_pos)
        self._reorder_last_local_pos = local_pos

    def _reorder_extend_stroke(self, scene_pos: QPointF):
        """Extend the current reorder stroke and check intersections.

        Hits are scored by travel order along the new stroke segment
        (upstream ``_collect_path_reorder_hits`` + ``_segment_rect_entry``),
        so numbering follows the path direction rather than scene z-order
        and a fast drag crossing several blocks in one frame numbers them
        correctly.
        """
        # Map to textLayer local coords to match absBoundingRect() space
        local_pos = self.textLayer.mapFromScene(scene_pos)
        last = (
            self._reorder_last_local_pos
            if self._reorder_last_local_pos is not None
            else local_pos
        )
        self._reorder_last_local_pos = local_pos
        self._reorder_path.lineTo(local_pos)

        # Build a stroked (wide) version of the segment for collision
        # detection. Scale brush radius inversely with zoom so the visual
        # hit area stays consistent regardless of zoom level.
        half = self._reorder_brush_radius / self.scale_factor
        segment = QPainterPath(last)
        segment.lineTo(local_pos)
        stroker = QPainterPathStroker()
        stroker.setWidth(half * 2)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        hit_area = stroker.createStroke(segment)
        if last == local_pos:
            hit_area.addEllipse(last, half, half)

        # Check un-touched blocks, ordered by where the segment enters them.
        touched = set(self._reorder_touched_blocks)
        candidates = []
        for item in self.textLayer.childItems():
            if not isinstance(item, TextBlkItem):
                continue
            if item in touched:
                continue
            br = item.absBoundingRect(qrect=True)
            if not hit_area.intersects(br):
                continue
            candidates.append(
                (_segment_rect_entry(last, local_pos, br, half), item)
            )
        candidates.sort(key=lambda hit: hit[0])
        for _entry, item in candidates:
            self._reorder_touched_blocks.append(item)
            item.setSelected(True)
            item._reorder_seq = len(self._reorder_touched_blocks) - 1
            item.refresh_seq_badge()

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
            idx_map[id(item.blk)]
            for item in self._reorder_touched_blocks
            if id(item.blk) in idx_map
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
                    erasing = False
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
        if self.creating_textblock:
            self.clear_states()
        if painting:
            self.editing_textblkitem = None
            self.textblock_mode = False
        else:
            # Leaving draw mode returns the canvas to the plain arrow cursor
            # (NoDrag — see restore_drag_mode for why the cursor is set here).
            self.clear_canvas_cursor()
            self.gv.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.gv.setCursor(Qt.CursorShape.ArrowCursor)
            self.image_edit_mode = ImageEditMode.NONE

    @property
    def painting(self):
        return self.image_edit_mode == ImageEditMode.InpaintTool

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
        self.clearToolStates()
        self.clear_states()
        for textitem in self.selected_text_items():
            if textitem.isEditing():
                self.editing_textblkitem = textitem
        # clear_states 只清引用不结束编辑态；选中项恢复扑空时以形状控件
        # 绑定项兜底，保持 is_editting 与 editingTextItem 真值一致
        if self.editing_textblkitem is None:
            blk_item = self.txtblkShapeControl.blk_item
            if blk_item is not None and blk_item.isEditing():
                self.editing_textblkitem = blk_item

    def clear_states(self):
        if self._text_creation_cursor_active:
            self._clear_text_creation_cursor()
        if self.creating_textblock:
            self.txtblkShapeControl.hide()
            self.txtblkShapeControl.showControls()
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

    # ── 编辑会话（键入会话 + 格式化手势）─────────────────────────────
    #
    # 落账模型（撤销体系 3a）：内容变更不再逐条压栈，而是先聚进会话；
    # 会话闭合（边界钩子或空闲定时器）时以一条快照命令落账。
    # - 键入会话：before 由调用方在镜像对账前抓（镜像侧尚持旧文）；相邻
    #   变更并入同一会话（Qt 合并同语义），非相邻插入闭合旧会话另起新
    #   会话（burst，护网 test_panel_typing_burst_two_commands）。
    # - 格式化手势：手势期间的预览中间值不入栈，闭合时以「基线↔终值」
    #   一条 FormatGestureCommand 落账；预览悬开期间按 Ctrl+Z = 取消
    #   手势恢复原值（目标行为 7，见 _cancel_format_gesture）。

    @property
    def _format_gesture_open(self) -> bool:
        return self._format_gesture is not None

    def note_typing_edit(
        self,
        item: TextBlkItem,
        edit,
        before_text: str,
        change_from: int,
        removed: int,
        added_len: int,
    ):
        """译文侧键入变更登记（画布/面板双向共用）。

        before_text 必须是镜像对账前的旧文（propagate handler 在同步前抓）。
        相邻性判定：change_from <= last_end <= change_from + removed
        （末尾插入/退格/Delete 均连续），否则闭合旧会话另起新会话。
        """
        session = self._typing_session
        if session is not None and (
            session["edit"] is not edit
            or (
                session["last_change_end"] is not None
                and not (
                    change_from
                    <= session["last_change_end"]
                    <= change_from + removed
                )
            )
        ):
            self._commit_typing_session()
            session = None
        if session is None:
            # 键入是手势边界：先闭合格式化手势（落账）再开新会话
            self._commit_format_gesture()
            self._typing_session = session = {
                "item": item,
                "edit": edit,
                "before_text": before_text,
                "last_change_end": None,
                "is_source": False,
            }
            self.on_textstack_changed()  # 会话开始持有未落账改动 → 脏
        session["last_change_end"] = change_from + added_len
        self._edit_session_timer.start()

    def note_source_edit(
        self, edit, change_from: int, removed: int, added_len: int
    ):
        """原文面板键入登记。原文无镜像可抓 before，会话由 focus_in 开启
        （note_source_focus_in 已捕获 before）；无会话 = 拿不到 before，
        降级忽略（该变更不可撤销，但不得崩）。"""
        session = self._typing_session
        if (
            session is None
            or session["edit"] is not edit
            or not session["is_source"]
        ):
            return
        if session["last_change_end"] is not None and not (
            change_from <= session["last_change_end"] <= change_from + removed
        ):
            # 原文 burst：闭合旧会话；新会话 before 只能由当前文重建——
            # 纯插入可逆推（剔除本次插入段），含删除则降级为当前文
            # （before==after，闭合时不落账，本次变更不可撤销）。
            self._commit_typing_session()
            text = edit.toPlainText()
            if removed == 0:
                before = text[:change_from] + text[change_from + added_len:]
            else:
                before = text
            self._typing_session = session = {
                "item": None,
                "edit": edit,
                "before_text": before,
                "last_change_end": None,
                "is_source": True,
            }
        session["last_change_end"] = change_from + added_len
        self._edit_session_timer.start()

    def note_source_focus_in(self, edit):
        """原文编辑器获得焦点：开启原文键入会话（before = 当前全文）。"""
        session = self._typing_session
        if (
            session is not None
            and session["edit"] is edit
            and session["is_source"]
        ):
            return
        self.commit_edit_sessions()
        self._typing_session = {
            "item": None,
            "edit": edit,
            "before_text": edit.toPlainText(),
            "last_change_end": None,
            "is_source": True,
        }

    def note_formatting_edit(self, item: TextBlkItem, formatpanel=None):
        """格式化变更登记：并入当前手势；首块开启手势。基线由 item 在
        is_formatting 事务入口预捕（ui/text_engine/item.py::
        _capture_ffmt_gesture_baseline），此处只收集。"""
        # 格式化是键入会话边界：先闭合键入会话（落账）再并入手势
        self._commit_typing_session()
        gesture = self._format_gesture
        if gesture is None:
            self._format_gesture = gesture = {
                "items": {},
                "formatpanel": formatpanel,
            }
            self.on_textstack_changed()
        if item not in gesture["items"]:
            baseline = item._ffmt_gesture_baseline
            if baseline is None:
                # 兜底：捕获点未覆盖的格式化路径——以当前态为基线（手势
                # 第一次变更丢 before，仍保证可落账不崩）
                baseline = (
                    item.toHtml(),
                    item.absBoundingRect(qrect=True),
                    item.get_fontformat(),
                )
            gesture["items"][item] = baseline
        self._edit_session_timer.start()

    def commit_edit_sessions(self):
        """闭合全部编辑会话（键入 + 格式化手势），各落一条快照命令。

        也是所有外部命令推送、选区变化、失焦、保存点之前的统一边界。"""
        self._commit_typing_session()
        self._commit_format_gesture()

    def _commit_typing_session(self):
        session = self._typing_session
        if session is None:
            return
        self._typing_session = None
        self._edit_session_timer.stop()
        edit = session["edit"]
        try:
            after_text = edit.toPlainText()
        except RuntimeError:
            return  # widget 已销毁（切页/重渲），改动随场景一起消失
        if after_text != session["before_text"]:
            item = None if session["is_source"] else session["item"]
            cmd = TypingSessionCommand(
                item, edit, session["before_text"], after_text
            )
            if item is None:
                # 原文会话无画布 item：blk 锚点从配对面板取（跨页重解析用）
                blk = _blk_of_panel_edit(edit)
                if blk is not None:
                    cmd.blk = blk
            self._tag_text_command(cmd)
            self.text_undo_stack.push(cmd)
        self.on_textstack_changed()

    def _commit_format_gesture(self):
        gesture = self._format_gesture
        if gesture is None:
            return
        self._format_gesture = None
        self._edit_session_timer.stop()
        entries = []
        for item, baseline in gesture["items"].items():
            try:
                before_html, before_rect, before_fmt = baseline
                entries.append(
                    {
                        "item": item,
                        "blk": item.blk,
                        "before_html": before_html,
                        "before_rect": before_rect,
                        "before_fmt": before_fmt,
                        "after_html": item.toHtml(),
                        "after_rect": item.absBoundingRect(qrect=True),
                        "after_fmt": item.get_fontformat(),
                    }
                )
                item._ffmt_gesture_baseline = None
            except RuntimeError:
                continue
        if entries:
            cmd = FormatGestureCommand(entries, gesture["formatpanel"])
            self._tag_text_command(cmd)
            self.text_undo_stack.push(cmd)
        self.on_textstack_changed()

    def _cancel_format_gesture(self):
        """预览悬开期间按 Ctrl+Z：取消手势、恢复手势前原值（目标行为 7）。
        不落命令、不产生新撤销步。"""
        gesture = self._format_gesture
        if gesture is None:
            return
        self._format_gesture = None
        self._edit_session_timer.stop()
        for item, baseline in gesture["items"].items():
            try:
                before_html, before_rect, before_fmt = baseline
                with replay_guard(item):
                    item.repaint_on_changed = False
                    try:
                        item.load_rich_text_html(before_html)
                        item.set_fontformat(before_fmt)
                        item.setRect(before_rect)
                    finally:
                        item.repaint_on_changed = True
                    item.repaint_background()
                item._ffmt_gesture_baseline = None
            except RuntimeError:
                continue
        formatpanel = gesture["formatpanel"]
        if formatpanel is not None:
            try:
                item = formatpanel.textblk_item
                if item is not None:
                    multi_size = not item.isEditing() and item.isMultiFontSize()
                    formatpanel.set_active_format(
                        item.get_fontformat(), multi_size
                    )
            except RuntimeError:
                pass
        self.on_textstack_changed()

    def _drop_edit_sessions(self):
        """清栈路径：丢弃会话状态（内容保持现状，不落账不恢复）。"""
        self._typing_session = None
        gesture = self._format_gesture
        self._format_gesture = None
        self._edit_session_timer.stop()
        if gesture is not None:
            for item in gesture["items"]:
                try:
                    item._ffmt_gesture_baseline = None
                except RuntimeError:
                    pass

    def _edit_session_dirty(self) -> bool:
        """会话持有未落账改动 = 脏（保存提示/probe 的会话侧输入）。"""
        session = self._typing_session
        if session is not None:
            try:
                if session["edit"].toPlainText() != session["before_text"]:
                    return True
            except RuntimeError:
                pass
        return self._format_gesture is not None

    # ── 命令栈推送 / 撤销重做 ────────────────────────────────────────

    def _tag_text_command(self, command: QUndoCommand):
        """跨页历史（阶段 4）：文本命令入栈前打页标签 + 捕获页代数。

        页标签驱动跨页撤销门与历史面板分组；页代数用于页屏障判定
        （栈外管线整体换新 blk_list 后，该页既有命令过期成僵尸）。"""
        if command is None:
            return
        proj = self.imgtrans_proj
        command._proj = proj
        pname = getattr(proj, "current_img", None)
        command.pagename = pname
        gen_fn = getattr(proj, "page_generation", None)
        command.page_generation = gen_fn(pname) if (gen_fn and pname) else 0

    def _disarm_cross_undo(self):
        self._cross_undo_armed = None

    def _gate_cross_page(self, cmd: QUndoCommand, direction: str, auto=False) -> bool:
        """跨页撤销门（阶段 4，用户拍板：再按一次才继续）。

        返回 True = 本次按键被拦截（已提示，等待二次确认）。僵尸命令
        无内容变更直接放行消费；命令所属页为当前页放行；跨页时第一次
        按仅提示，第二次按跳页后放行——跳页经 page_jump_requested 复用
        完整切页链路。auto=True 为历史面板行点击路径：点击即显式意图，
        直接跳页不确认。"""
        pname = getattr(cmd, "pagename", None)
        if pname is None:
            return False
        if command_page_stale(cmd, getattr(cmd, "_proj", None)):
            return False
        if pname == self.imgtrans_proj.current_img:
            self._cross_undo_armed = None
            return False
        armed = self._cross_undo_armed
        if auto or (
            armed is not None
            and armed[0] is cmd
            and armed[1] == direction
        ):
            self._cross_undo_armed = None
            self.page_jump_requested.emit(pname)
            # 跳页链路可能落账新命令（旧页 transform 提交等）：下一栈位
            # 已变时不再强推本步，交还下一次按键（线性顺序不被打破）。
            stack = self.text_undo_stack
            if direction == "undo":
                next_cmd = (
                    stack.command(stack.index() - 1) if stack.index() > 0 else None
                )
            else:
                next_cmd = (
                    stack.command(stack.index())
                    if stack.index() < stack.count()
                    else None
                )
            if next_cmd is cmd:
                return False
            return True
        self._cross_undo_armed = (cmd, direction)
        if direction == "undo":
            msg = self.tr("Next undo step is on page %1 — press again to continue")
        else:
            msg = self.tr("Next redo step is on page %1 — press again to continue")
        notification.toast(
            msg.replace("%1", pname), key="undo", duration=2500
        )
        return True

    def _notify_skipped_step(self):
        notification.toast(
            self.tr("Skipped: that page was rewritten by the pipeline"),
            key="undo",
            duration=2000,
        )

    def invalidate_text_history_for_page(self, pagename: str = None):
        """页屏障（整页写入路径）：把该页全部文本命令标记为僵尸。

        翻译回填、整页重载等原地重写页内容的路径调用——blk 对象未换新
        （代数不变），须显式标记。僵尸命令保留栈位置，undo/redo 无操作
        跳过（历史面板灰显）。"""
        pname = pagename or getattr(self.imgtrans_proj, "current_img", None)
        if pname is None:
            return
        self._drop_edit_sessions()
        self._disarm_cross_undo()
        stack = self.text_undo_stack
        for i in range(stack.count()):
            cmd = stack.command(i)
            if getattr(cmd, "pagename", None) == pname:
                cmd._page_stale = True

    def prepare_page_switch(self):
        """切页撤销语义（阶段 4 跨页历史）：文本栈不清——命令以 blk 锚点
        存活跨页，切页后仍可跨页撤销；绘制栈页级，照旧清空并复位保存
        计数。调用时机在 set_current_img 之前由调用方负责会话落账。"""
        self._disarm_cross_undo()
        self.saved_drawundo_step = 0
        self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()
        self.apply_undo_limit()

    def push_undo_command(self, command: QUndoCommand, update_pushed_step=True):
        # 任何外部命令推送都是会话边界：先闭合落账（保持时间序），再压新命令
        self.commit_edit_sessions()
        if self.textEditMode():
            self.push_text_command(command, update_pushed_step)
        elif self.drawMode():
            self.push_draw_command(command, update_pushed_step)
        else:
            return

    def push_draw_command(self, command: QUndoCommand, update_pushed_step=True):
        self.commit_edit_sessions()
        self._disarm_cross_undo()
        if command is not None:
            stack = self.draw_undo_stack
            # 同区域连续修复聚合（阶段4-3a）：栈顶同类修复命令吸收新端点，
            # 不新增栈步。仅当处于栈顶（无 redo 残尾）且已有未保存改动时
            # 聚合——对保存点所在的干净命令聚合会让撤销越过保存点，回不到
            # 已保存状态。
            if (
                stack.count() > 0
                and stack.index() == stack.count()
                and self.saved_drawundo_step != self.num_pushed_drawstep
            ):
                top = stack.command(stack.count() - 1)
                try_absorb = getattr(top, "try_absorb", None)
                if try_absorb is not None and try_absorb(command):
                    command.redo()  # 正常 push 由 Qt 调 redo，聚合路径手动等价执行
                    if update_pushed_step:
                        self.on_drawstack_changed()
                    return
            before = stack.index()
            self.draw_undo_stack.push(command)
            # 撤销上限截断最旧命令时，手工计数器与栈坐标同步平移；保存点
            # 被截掉则落 -1 不可达哨兵，保持「未保存」直到下次保存。
            truncated = before + 1 - self.draw_undo_stack.index()
            if truncated > 0:
                self.num_pushed_drawstep = max(
                    self.num_pushed_drawstep - truncated, 0
                )
                self.saved_drawundo_step = max(
                    self.saved_drawundo_step - truncated, -1
                )
        if update_pushed_step:
            self.num_pushed_drawstep += 1
            self.on_drawstack_changed()

    def push_text_command(self, command: QUndoCommand, update_pushed_step=True):
        self.commit_edit_sessions()
        self._disarm_cross_undo()
        if command is not None:
            self._tag_text_command(command)
            self.text_undo_stack.push(command)
        self.on_textstack_changed()

    def on_drawstack_changed(self):
        if (
            self.num_pushed_drawstep != self.saved_drawundo_step
            or not self.text_undo_stack.isClean()
            or self._edit_session_dirty()
        ):
            self.setProjSaveState(True)
        else:
            self.setProjSaveState(False)

    def _refresh_save_state(self):
        if (
            not self.text_undo_stack.isClean()
            or self._edit_session_dirty()
            or self.num_pushed_drawstep != self.saved_drawundo_step
        ):
            self.setProjSaveState(True)
        else:
            self.setProjSaveState(False)

    def on_textstack_changed(self):
        self._refresh_save_state()
        self.textstack_changed.emit()

    def redo_textedit(self):
        # 预览悬开期间的 redo：先闭合手势落账（redo 无取消语义）
        self.commit_edit_sessions()
        self._text_redo_step()

    def undo_textedit(self):
        # 目标行为 7：预览悬开期间按 Ctrl+Z = 取消手势恢复原值
        if self._format_gesture is not None:
            self._cancel_format_gesture()
            return
        # 键入会话先落账再撤销 → 本次 Ctrl+Z 撤销的正是刚键入的内容
        self._commit_typing_session()
        self._text_undo_step()

    def _text_redo_step(self, auto_cross_page=False):
        """文本栈重做一步（跨页门在重放前拦截）。"""
        stack = self.text_undo_stack
        stale = False
        if stack.index() < stack.count():
            cmd = stack.command(stack.index())
            if self._gate_cross_page(cmd, "redo", auto_cross_page):
                return
            stale = command_page_stale(cmd, getattr(cmd, "_proj", None))
        stack.redo()
        self.on_textstack_changed()
        self.txtblkShapeControl.updateBoundingRect()
        if stale:
            self._notify_skipped_step()

    def _text_undo_step(self, auto_cross_page=False):
        """文本栈撤销一步（跨页门在重放前拦截）。"""
        stack = self.text_undo_stack
        stale = False
        if stack.index() > 0:
            cmd = stack.command(stack.index() - 1)
            if self._gate_cross_page(cmd, "undo", auto_cross_page):
                return
            stale = command_page_stale(cmd, getattr(cmd, "_proj", None))
            if not stale and not auto_cross_page:
                # 组化命令撤销前确认（取消则本步不执行）
                if not self._confirm_group_undo(cmd):
                    return
        undo_name = stack.undoText()
        stack.undo()
        self.on_textstack_changed()
        self.txtblkShapeControl.updateBoundingRect()
        if stale:
            self._notify_skipped_step()
        else:
            self._notify_undo(undo_name)
        if self._group_undo_rerender:
            self._group_undo_rerender = False
            self.rerender_dirty_pages_requested.emit()

    def _confirm_group_undo(self, cmd) -> bool:
        """组化命令撤销前确认（阶段 4 第二批，决策 4）。

        跨页批量命令（整理换行/高级对齐）的 undo 一次还原多页数据，
        先列影响面并给「同时重渲染」选项；取消则本步不执行。仅影响
        当前页的组命令不弹（与普通撤销无异）；僵尸步与历史面板跳转
        路径（auto_cross_page）不经此门。"""
        self._group_undo_rerender = False
        summary_fn = getattr(cmd, "group_undo_summary", None)
        if not callable(summary_fn):
            return True
        pages = summary_fn() or {}
        current = getattr(self.imgtrans_proj, "current_img", None)
        if not any(p != current for p in pages):
            return True
        total = sum(pages.values())
        views = self.views()
        # 父对象取顶层窗口（应用惯例）：FramelessWindow 有 win32 原生定制，
        # 原生模态对话框挂到非顶层子控件（gv）上会 access violation
        box = QMessageBox(views[0].window() if views else None)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(cmd.text())
        box.setText(
            self.tr("This step is a batch edit spanning %1 pages (%2 blocks)")
            .replace("%1", str(len(pages)))
            .replace("%2", str(total))
        )
        lines = [
            self.tr("%1: %2 blocks").replace("%1", p).replace("%2", str(n))
            for p, n in list(pages.items())[:8]
        ]
        if len(pages) > 8:
            lines.append(
                self.tr("… and %1 more pages").replace("%1", str(len(pages) - 8))
            )
        box.setInformativeText("\n".join(lines))
        # 复选框须构造期挂父：无父临时对象在 setCheckBox 接管前可能被
        # PyQt GC 回收 → box 内部指针悬空，checkBox() 访问即 access violation
        box.setCheckBox(QCheckBox(self.tr("Re-render affected pages"), box))
        undo_btn = box.addButton(
            self.tr("Undo"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not undo_btn:
            return False
        self._group_undo_rerender = box.checkBox().isChecked()
        return True

    def _notify_undo(self, undo_name: str):
        # 撤回行为名提示（undoText 为空 = 无可撤步不提示；历史面板跳转
        # 经 _suppress_undo_toast 抑制，避免跳转路径逐步刷 toast）
        if not undo_name or self._suppress_undo_toast:
            return
        notification.toast(
            self.tr("Undo: %1").replace("%1", undo_name),
            key="undo",
            duration=1500,
        )

    def redo(self, auto_cross_page=False):
        if self.textEditMode():
            self.commit_edit_sessions()
            self._text_redo_step(auto_cross_page)
        elif self.drawMode():
            undo_stack = self.draw_undo_stack
            self.num_pushed_drawstep += 1
            self.on_drawstack_changed()
            undo_stack.redo()
        else:
            return

    def undo(self, auto_cross_page=False):
        if self.textEditMode():
            # 目标行为 7：预览悬开期间按 Ctrl+Z = 取消手势恢复原值
            if self._format_gesture is not None:
                self._cancel_format_gesture()
                return
            self._commit_typing_session()
            self._text_undo_step(auto_cross_page)
        elif self.drawMode():
            if self.num_pushed_drawstep > 0:
                self.num_pushed_drawstep -= 1
            self.on_drawstack_changed()
            self.draw_undo_stack.undo()
        else:
            return

    def clear_undostack(self, update_saved_step=False):
        # 会话状态随栈一起丢弃（内容保持现状，不落账不恢复）
        self._drop_edit_sessions()
        self._disarm_cross_undo()
        if update_saved_step:
            self.saved_drawundo_step = 0
            self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()
        self.text_undo_stack.clear()
        # 清栈后栈空，撤销步数上限在此落地（中途改设置经此生效）
        self.apply_undo_limit()

    def clear_text_stack(self):
        self._drop_edit_sessions()
        self._disarm_cross_undo()
        self.text_undo_stack.clear()
        self.apply_undo_limit()

    def clear_undostack(self, update_saved_step=False):
        # 会话状态随栈一起丢弃（内容保持现状，不落账不恢复）
        self._drop_edit_sessions()
        if update_saved_step:
            self.saved_drawundo_step = 0
            self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()
        self.text_undo_stack.clear()
        # 清栈后栈空，撤销步数上限在此落地（中途改设置经此生效）
        self.apply_undo_limit()

    def clear_text_stack(self):
        self._drop_edit_sessions()
        self.text_undo_stack.clear()
        self.apply_undo_limit()

    def clear_draw_stack(self):
        self.num_pushed_drawstep = 0
        self.draw_undo_stack.clear()
        self.apply_undo_limit()

    def apply_undo_limit(self):
        # 撤销步数上限（pcfg.undo_steps_limit，0=无限）同时作用于文本/绘制
        # 两栈。Qt 限制：setUndoLimit 仅在空栈上生效，非空栈上调用只打
        # 警告并被忽略——启动时栈空立即生效；会话中途改设置由下次清栈
        # （切页/整页管线）路径落地，见各 clear_* 处的再应用。
        self._apply_undo_limit_to(self.text_undo_stack)
        self._apply_undo_limit_to(self.draw_undo_stack)

    @staticmethod
    def _apply_undo_limit_to(stack):
        if stack.count() == 0:
            stack.setUndoLimit(pcfg.undo_steps_limit)

    def update_saved_undostep(self):
        # 保存点：未落账的会话先落账，再以 clean 机制记录干净位。
        # 只刷新保存状态、不广播 textstack_changed——保存不改文档内容，
        # 广播会让 mainwindow 清空全局搜索结果；全局替换 prepare 阶段的
        # 同步落盘恰好经此路径抹掉 searched_pattern，替换静默空转。
        self.commit_edit_sessions()
        self._disarm_cross_undo()
        self.saved_drawundo_step = self.num_pushed_drawstep
        self.text_undo_stack.setClean()
        self._refresh_save_state()

    def text_change_unsaved(self) -> bool:
        # 3a 起走 QUndoStack clean 机制（保存时 setClean）+ 会话脏标记，
        # 手工计步 num_pushed_textstep 已删除
        return not self.text_undo_stack.isClean() or self._edit_session_dirty()

    def draw_change_unsaved(self) -> bool:
        return self.saved_drawundo_step != self.num_pushed_drawstep

    def prepareClose(self):
        self.blockSignals(True)
        self.text_undo_stack.blockSignals(True)
        self.draw_undo_stack.blockSignals(True)
