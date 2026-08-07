import math
from typing import List, Tuple, Union

import numpy as np
from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QInputMethodEvent,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from qtpy.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from utils.fontformat import FontFormat, TextTransformState, pt2px, px2pt
from utils.imgproc_utils import xywh2xyxypoly
from utils.text_alignment import SNAP_THRESHOLD, compute_snap
from utils.textblock import TextBlock

from .misc import table_pattern, td_pattern
from .scene_textlayout import HorizontalTextDocumentLayout, VerticalTextDocumentLayout

from ui.text_engine.effect_renderer import TextEffectRenderer
from ui.text_engine.geometry import TextItemGeometryController

TEXTRECT_SELECTED_COLOR = QColor(248, 64, 147, 170)
# Above this canvas zoom, DeviceCoordinateCache rasterizes text into enormous
# device-resolution bitmaps (a 300px block at 1000% becomes a ~3000px-wide
# cache) — hundreds of ms per block when it enters the viewport during pan.
# Above the limit, render natively (viewport-clipped) instead.
HIGH_ZOOM_CACHE_LIMIT = 3.0


def _textrect_show_color():
    from ui.misc import get_theme_color

    c = get_theme_color()
    c.setAlpha(170)
    return c


class TextBlkItem(QGraphicsTextItem):
    begin_edit = Signal(int)
    end_edit = Signal(int)
    hover_enter = Signal(int)
    hover_move = Signal(int)
    moved = Signal()
    moving = Signal(QGraphicsTextItem)
    rotated = Signal(float)
    reshaped = Signal(QGraphicsTextItem)
    leftbutton_pressed = Signal(int)
    doc_size_changed = Signal(int)
    pasted = Signal(int)
    redo_signal = Signal()
    undo_signal = Signal()
    push_undo_stack = Signal(int, bool)
    propagate_user_edited = Signal(int, str, bool)
    visual_geometry_changed = Signal()

    def __init__(
        self,
        blk: TextBlock = None,
        idx: int = 0,
        set_format=True,
        show_rect=False,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # Geometry ownership lives in the controller (Stage 2 port); the item
        # only keeps thin Qt hooks. Effect/background state lives in the
        # renderer (Stage 3 port).
        self.geometry_controller = TextItemGeometryController(self)
        self.effect_renderer = TextEffectRenderer(self)
        self.pre_editing = False
        self.blk: TextBlock = None
        self.fontformat: FontFormat = None
        self.repainting = False
        self.reshaping = False
        self.under_ctrl = False
        self.draw_rect = show_rect
        self.old_ffmt_values = None

        self.idx = idx
        self._hide_badge = False
        self._reorder_seq: int = -1  # >=0 when in path-reorder mode; overrides badge number

        self.stroke_qcolor = QColor(0, 0, 0)
        self.oldPos = QPointF()
        self.oldRect = QRectF()
        self.repaint_on_changed = True
        self._text_overflows: bool = False  # 文字超出文本框，启用裁剪 + 黄色边框

        self.is_formatting = False
        self.old_undo_steps = 0
        self.in_redo_undo = False
        self.change_from: int = 0
        self.change_added: int = 0
        self.input_method_from = -1
        self.input_method_text = ""
        self.block_all_input = False
        self.block_change_signal = False
        self._block_hw_sub = False  # prevents recursion in half-width corner bracket substitution

        self.layout: Union[VerticalTextDocumentLayout, HorizontalTextDocumentLayout] = (
            None
        )
        self.document().setDocumentMargin(0)
        # Auto-substitute corner brackets for horizontal half-width mode
        self.document().contentsChange.connect(self._on_contents_change_for_hw)
        self.initTextBlock(blk, set_format=set_format)
        self.setBoundingRegionGranularity(0)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.geometry_controller.finish_initialization()

    @property
    def _display_rect(self) -> QRectF:
        """Managed display rect (owned by the geometry controller)."""
        return self.geometry_controller.display_rect

    @_display_rect.setter
    def _display_rect(self, rect: QRectF) -> None:
        self.geometry_controller.display_rect = rect

    def inputMethodEvent(self, e: QInputMethodEvent):
        if not self.pre_editing:
            cursor = self.textCursor()
            self.input_method_from = cursor.selectionStart()
        if e.preeditString() == "":
            self.pre_editing = False
            self.input_method_text = e.commitString()
        else:
            self.pre_editing = True
        super().inputMethodEvent(e)

    def is_editting(self):
        return (
            self.textInteractionFlags() == Qt.TextInteractionFlag.TextEditorInteraction
        )

    def on_content_changed(self):
        if (
            (self.hasFocus() or self.is_formatting)
            and not self.pre_editing
            and not self.block_change_signal
        ):
            # self.content_changed.emit(self)
            if not self.in_redo_undo:
                undo_steps = self.document().availableUndoSteps()
                new_steps = undo_steps - self.old_undo_steps
                joint_previous = new_steps == 0

                if not self.is_formatting:
                    change_from = self.change_from
                    added_text = ""
                    if self.input_method_from != -1:
                        added_text = self.input_method_text
                        change_from = self.input_method_from
                        self.input_method_from = -1

                    elif self.change_added > 0:
                        cursor = self.textCursor()
                        cursor.setPosition(change_from)
                        cursor.setPosition(
                            change_from + self.change_added,
                            QTextCursor.MoveMode.KeepAnchor,
                        )
                        added_text = cursor.selectedText()

                    self.propagate_user_edited.emit(
                        change_from, added_text, joint_previous
                    )
                    self.change_added = 0

                if new_steps > 0:
                    self.old_undo_steps = undo_steps
                    self.push_undo_stack.emit(new_steps, self.is_formatting)

        if not (self.hasFocus() and self.pre_editing):
            if self.repaint_on_changed:
                if not self.repainting:
                    self.repaint_background()
            self.update()

    def repaint_background(self, render_scale: float = 1.0):
        return self.effect_renderer.repaint_background(render_scale)

    def docSizeChanged(self):
        self.setCenterTransform()
        self.doc_size_changed.emit(self.idx)

    def initTextBlock(self, blk: TextBlock = None, set_format=True):
        self.blk = blk
        self.fontformat = blk.fontformat
        self.geometry_controller.bind_model()
        if blk is None:
            xyxy = [0, 0, 0, 0]
            blk = TextBlock(xyxy)
            blk.lines = [xyxy]
            bx1, by1, bx2, by2 = xyxy
            xywh = np.array([[bx1, by1, bx2 - bx1, by2 - by1]])
            blk.lines = xywh2xyxypoly(xywh).reshape(-1, 4, 2).tolist()
        self.setVertical(blk.vertical)
        self.setRect(blk.bounding_rect(), update_blk_rect=False)

        if blk.angle != 0:
            self.setRotation(blk.angle)

        set_char_fmt = False
        if blk.translation:
            set_char_fmt = True

        font_fmt = blk.fontformat
        if set_format:
            self.set_fontformat(
                font_fmt,
                set_char_format=set_char_fmt,
                set_stroke_width=False,
                set_effect=False,
            )

        if not blk.rich_text:
            if blk.translation:
                self.setPlainText(blk.translation)
        else:
            self.setHtml(blk.rich_text)
            self.setLetterSpacing(font_fmt.letter_spacing, repaint_background=False)
            cursor = self.textCursor()
            cursor.clearSelection()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cfmt = cursor.charFormat()
            cursor.setCharFormat(cfmt)
            cursor.setBlockCharFormat(cfmt)
            self.setTextCursor(cursor)
        if self.fontformat.gradient_enabled:
            self.setGradientEnabled(True)
        self.setShadow(font_fmt, repaint=False)
        self.setStrokeWidth(font_fmt.stroke_width, repaint_background=False)
        self.repaint_background()

    # ── Horizontal half-width corner bracket substitution ──────────────────

    # Bidirectional map for 「↔ ｢ and 」↔ ｣
    _HW_CORNER_MAP = {"「": "｢", "」": "｣"}

    @staticmethod
    def _hw_corner_reverse_map():
        return {"｢": "「", "｣": "」"}

    def _on_contents_change_for_hw(self, pos: int, removed: int, added: int):
        """Auto-substitute 「/」→ ｢/｣ when horizontal half-width mode is on."""
        if self._block_hw_sub:
            return
        if added <= 0:
            return
        # Only applies to horizontal text blocks
        if self.fontformat.vertical:
            return
        from utils.config import pcfg
        if not (pcfg.halfwidth_jp_corner_brackets and pcfg.halfwidth_jp_corner_brackets_horizontal):
            return
        # Only check the newly-added range for corner brackets
        text = self.toPlainText()
        end = pos + added
        if end > len(text):
            return
        self._block_hw_sub = True
        try:
            for i in range(end - 1, pos - 1, -1):  # right to left to avoid index shift
                if i >= len(text):
                    continue
                ch = text[i]
                if ch in self._HW_CORNER_MAP:
                    cursor = QTextCursor(self.document())
                    cursor.setPosition(i)
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    cursor.insertText(self._HW_CORNER_MAP[ch])
        finally:
            self._block_hw_sub = False

    def apply_horizontal_halfwidth_corner_brackets(self):
        """Replace 「→ ｢, 」→ ｣ throughout the document (horizontal only)."""
        if self._block_hw_sub or self.fontformat.vertical:
            return
        self._block_hw_sub = True
        try:
            doc = self.document()
            text = doc.toPlainText()
            cursor = QTextCursor(doc)
            cursor.beginEditBlock()
            for i, ch in enumerate(text):
                if ch in self._HW_CORNER_MAP:
                    cursor.setPosition(i)
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    cursor.insertText(self._HW_CORNER_MAP[ch])
            cursor.endEditBlock()
        finally:
            self._block_hw_sub = False

    def restore_horizontal_halfwidth_corner_brackets(self):
        """Reverse: replace ｢→ 「, ｣→ 」 throughout the document (horizontal only)."""
        if self._block_hw_sub or self.fontformat.vertical:
            return
        rev_map = self._hw_corner_reverse_map()
        self._block_hw_sub = True
        try:
            doc = self.document()
            text = doc.toPlainText()
            cursor = QTextCursor(doc)
            cursor.beginEditBlock()
            for i, ch in enumerate(text):
                if ch in rev_map:
                    cursor.setPosition(i)
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    cursor.insertText(rev_map[ch])
            cursor.endEditBlock()
        finally:
            self._block_hw_sub = False

    # ───────────────────────────────────────────────────────────────────────

    def setCenterTransform(self):
        self.geometry_controller.sync_origin()

    def rect(self) -> QRectF:
        return QRectF(self.pos(), self.boundingRect().size())

    def startReshape(self):
        self.oldRect = self.absBoundingRect(qrect=True)
        self.reshaping = True
        # 拖拽调整文本框 → 解除裁剪状态，恢复自动撑大行为
        if self._text_overflows:
            self._text_overflows = False
        # 禁用背景重绘，避免拖拽全程的重复合成
        self.effect_renderer.clear_cached_surface()
        from utils.config import pcfg

        if pcfg.show_decorations_during_drag:
            # Keep stroke/shadow visible during drag — rebuild now at current size;
            # the per-frame rebuild in setRect keeps them following the box.
            self.repaint_background()

    def endReshape(self):
        self.reshaped.emit(self)
        self.reshaping = False
        self.repaint_background()

    def padRect(self, rect: QRectF) -> QRectF:
        p = self.padding()
        P = p * 2
        return QRectF(rect.x() - p, rect.y() - p, rect.width() + P, rect.height() + P)

    def unpadRect(self, rect: QRectF) -> QRectF:
        p = -self.padding()
        P = p * 2
        return QRectF(rect.x() - p, rect.y() - p, rect.width() + P, rect.height() + P)

    def setRect(
        self,
        rect: Union[List, QRectF],
        padding=True,
        repaint=True,
        update_blk_rect=True,
    ) -> None:
        self.geometry_controller.set_rect(
            rect,
            padding=padding,
            repaint=repaint,
            update_blk_rect=update_blk_rect,
        )
        self.visual_geometry_changed.emit()

    def setRectFast(self, rect):
        """Fast geometry update during drag resize — skip expensive layout.

        Updates position and bounding rect immediately to keep visual sync
        with the control frame. Full text layout (setMaxSize) is deferred
        to the debounced _apply_resize timer.
        """
        padded = self.padRect(rect)
        self.prepareGeometryChange()
        self._display_rect = QRectF(0, 0, padded.width(), padded.height())
        self.setPos(padded.topLeft())

    def documentSize(self):
        return self.layout.documentSize()

    def boundingRect(self) -> QRectF:
        controller = getattr(self, 'geometry_controller', None)
        if controller is None:
            return super().boundingRect()
        return controller.bounding_rect(super().boundingRect())

    def padding(self) -> float:
        return self.document().documentMargin()

    def setPadding(self, p: float):
        """Grow-only document margin update; True if the margin changed."""
        _p = self.padding()
        if _p >= p:
            return False
        abr = self.absBoundingRect(qrect=True)
        self.layout.relayout_on_changed = False
        self.layout.updateDocumentMargin(p)
        self.layout.relayout_on_changed = True
        self.setRect(abr, repaint=False)
        return True

    def absBoundingRect(
        self, max_h=None, max_w=None, qrect=False
    ) -> Union[List, QRectF]:
        return self.geometry_controller.absolute_rect(max_h, max_w, qrect)

    def shape(self) -> QPainterPath:
        controller = getattr(self, 'geometry_controller', None)
        if controller is None:
            return super().shape()
        return controller.shape()

    def contains(self, point: QPointF) -> bool:
        controller = getattr(self, 'geometry_controller', None)
        if controller is None:
            return super().contains(point)
        return controller.contains(point)

    def logical_unpadded_rect(self) -> QRectF:
        """Return the untransformed, effect-free block rect in item coordinates."""
        return self.geometry_controller.logical_rect()

    def refresh_cache_policy(self) -> bool:
        """Align Qt cache mode with the active rendering policy.

        Text is cached with ``DeviceCoordinateCache`` outside editing at
        normal zoom (the former "Smooth" mode); editing switches to
        ``NoCache`` inside startEdit and is restored here by endEdit.
        Above ``HIGH_ZOOM_CACHE_LIMIT`` the cache stays off outside editing
        too: DeviceCoordinateCache rasterizes at device resolution, so a
        block at 1000% zoom becomes a ~10×-scale cache bitmap (expensive on
        pan entry), while native rendering is clipped to the viewport.
        """
        if self.is_editting():
            return False
        use_cache = self.get_scale() <= HIGH_ZOOM_CACHE_LIMIT
        target = (
            QGraphicsItem.CacheMode.DeviceCoordinateCache
            if use_cache
            else QGraphicsItem.CacheMode.NoCache
        )
        if self.cacheMode() == target:
            return False
        self.setCacheMode(target)
        return True

    def release_render_resources(self) -> None:
        """Release item-owned renderer/cache state at the page boundary."""
        self.geometry_controller.release_render_resources()

    def itemChange(self, change, value):
        controller = getattr(self, 'geometry_controller', None)
        if controller is None:
            return super().itemChange(change, value)
        return controller.item_change(change, value, super().itemChange)

    def setScale(self, scale: float) -> None:
        self.setTransformOriginPoint(0, 0)
        super().setScale(scale)
        self.setCenterTransform()

    @property
    def angle(self) -> int:
        return self.blk.angle

    def toTextBlock(self) -> TextBlock:
        raise NotImplementedError

    def setAngle(self, angle: int):
        self.setCenterTransform()
        if self.blk.angle != angle:
            self.setRotation(angle)
        self.blk.angle = angle

    def setVertical(self, vertical: bool):
        if self.fontformat is not None:
            self.fontformat.vertical = vertical

        valid_layout = True
        doc = self.document()
        if self.layout is not None:
            if isinstance(self.layout, VerticalTextDocumentLayout) == vertical:
                return
            self.layout.size_enlarged.disconnect(self.on_document_enlarged)
            self.layout.documentSizeChanged.disconnect(self.docSizeChanged)
        else:
            valid_layout = False
            doc.contentsChanged.connect(self.on_content_changed)
            doc.contentsChange.connect(self.on_content_changing)

        if valid_layout:
            rect = self.rect() if self.layout is not None else None

        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        doc.documentLayout().blockSignals(True)
        from utils.config import pcfg

        if vertical:
            layout = VerticalTextDocumentLayout(
                doc,
                self.fontformat,
                punctuation_position=pcfg.punctuation_position,
                tatechuyoko_threshold=pcfg.tatechuyoko_threshold,
                halfwidth_jp_corner_brackets=pcfg.halfwidth_jp_corner_brackets,
            )
        else:
            layout = HorizontalTextDocumentLayout(
                doc,
                self.fontformat,
                punctuation_position=pcfg.punctuation_position,
                tatechuyoko_threshold=pcfg.tatechuyoko_threshold,
                halfwidth_jp_corner_brackets=pcfg.halfwidth_jp_corner_brackets,
            )

        self.layout = layout
        doc.setDocumentLayout(layout)
        layout.size_enlarged.connect(self.on_document_enlarged)
        layout.documentSizeChanged.connect(self.docSizeChanged)

        if valid_layout:
            layout.setMaxSize(rect.width(), rect.height())
            self.setCenterTransform()
            self.repaint_background()
        self.geometry_controller.initialize_layout()
        self.doc_size_changed.emit(self.idx)

    def updateUndoSteps(self):
        self.old_undo_steps = self.document().availableUndoSteps()

    def on_content_changing(self, from_: int, removed: int, added: int):
        if not self.pre_editing:
            if self.hasFocus():
                self.change_from = from_
                self.change_added = added

    def keyPressEvent(self, e: QKeyEvent) -> None:

        if self.block_all_input:
            e.setAccepted(True)
            return

        if e.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if e.key() == Qt.Key.Key_Z:
                e.accept()
                self.undo_signal.emit()
                return
            elif e.key() == Qt.Key.Key_Y:
                e.accept()
                self.redo_signal.emit()
                return
            elif e.key() == Qt.Key.Key_V:
                if self.isEditing():
                    e.accept()
                    self.pasted.emit(self.idx)
                    return
        elif (
            e.modifiers()
            == Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            if e.key() == Qt.Key.Key_Z:
                e.accept()
                self.redo_signal.emit()
                return
        elif e.key() == Qt.Key.Key_Return:
            e.accept()
            self.textCursor().insertText("\n")
            return
        return super().keyPressEvent(e)

    def undo(self) -> None:
        self.in_redo_undo = True
        self.document().undo()
        self.in_redo_undo = False
        self.old_undo_steps = self.document().availableUndoSteps()

    def redo(self) -> None:
        self.in_redo_undo = True
        self.document().redo()
        self.in_redo_undo = False
        self.old_undo_steps = self.document().availableUndoSteps()

    def on_document_enlarged(self):
        from utils.config import pcfg

        if pcfg.clip_text_overflow:
            # 裁剪模式：不撑大文本框，记录溢出状态，重置 layout 到原始尺寸
            self._text_overflows = True
            self.layout._prevent_expand = True
            self.layout.setMaxSize(
                self._display_rect.width(), self._display_rect.height()
            )
            self.layout._prevent_expand = False
            self.update()
        else:
            size = self.documentSize()
            self.set_size(size.width(), size.height())

    def get_scale(self) -> float:
        tl = self.topLevelItem()
        if tl is not None:
            return tl.scale()
        else:
            return self.scale()

    def _get_overflow_clip_rect(self) -> QRectF:
        """Return the image boundary rect in item-local coordinates for painter clipping.

        When overflow mode is active, this clips text rendering to the canvas boundary.
        Returns an empty QRectF if no clipping is needed.
        """
        from utils.config import pcfg

        if not pcfg.overflow_mode:
            return QRectF()
        scene = self.scene()
        if scene is None or not hasattr(scene, "baseLayer"):
            return QRectF()
        img_scene_rect = scene.baseLayer.sceneBoundingRect()
        return self.mapFromScene(img_scene_rect).boundingRect()

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget
    ) -> None:
        controller = getattr(self, 'geometry_controller', None)
        if controller is None:
            self._paint_native(painter, option, widget)
            return
        controller.paint_item(painter, option, widget, self._paint_native)

    def _paint_native(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget
    ) -> None:
        # subpixel antialiasing is enabled for super().paint upon drawing on some non-transparent background https://github.com/dmMaze/BallonsTranslator/issues/919
        # which can be avoided by calling super().paint first, but it results in disappeared background in editting mode
        # so the checking logic lies here

        # ── Overflow clipping ─────────────────────────────────────────
        # When overflow_mode is on, clip text content to the image boundary
        # so that only the border is visible outside the canvas.
        clip_rect = self._get_overflow_clip_rect()
        draw_clip = not clip_rect.isEmpty()
        # Translate-fill clipping: when _text_overflows, clip text to the
        # text block's own content boundary (unpadded rect) so text that
        # exceeds the box is hidden rather than expanding it.
        if self._text_overflows and not self.is_editting():
            content_clip = self.unpadRect(self.boundingRect())
            if draw_clip:
                clip_rect = clip_rect.intersected(content_clip)
            else:
                clip_rect = content_clip
                draw_clip = True

        # ── Background then text, SourceOver ──────────────────────────────
        #
        # Key ordering: background (stroke/shadow) is drawn BEFORE text
        # via SourceOver, not after via DestinationOver.  This avoids
        # relying on QGraphicsTextItem::paint() leaving the painter in
        # a clean state — after page switches, super().paint() can leave
        # stale clip/transform that breaks subsequent DestinationOver
        # compositing.
        if not self.is_editting():
            if draw_clip:
                painter.save()
                painter.setClipRect(clip_rect)
            self._draw_background_only(painter)
            if draw_clip:
                painter.restore()

        if self.is_editting():
            if draw_clip:
                painter.save()
                painter.setClipRect(clip_rect)
            self._draw_accessories(painter)
            if draw_clip:
                painter.restore()

        option.state = QStyle.State_None
        if draw_clip:
            painter.save()
            painter.setClipRect(clip_rect)
        super().paint(painter, option, widget)
        if draw_clip:
            painter.restore()

        if not self.is_editting():
            # Border and badge always draw outside the clip
            self._draw_border_rect(painter)
            self._draw_seq_badge(painter)

    def _draw_accessories(self, painter: QPainter):
        br = self.boundingRect()
        painter.save()

        if self.effect_renderer.background_pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(br.toRect(), self.effect_renderer.background_pixmap)

        draw_rect = self.draw_rect and not self.under_ctrl
        if self._text_overflows and not self.is_editting():
            pen = QPen(
                QColor(255, 200, 0, 200), 3.5 / self.get_scale(), Qt.PenStyle.SolidLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        elif self.isSelected() and not self.is_editting():
            pen = QPen(
                TEXTRECT_SELECTED_COLOR, 3.5 / self.get_scale(), Qt.PenStyle.DashLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        elif draw_rect:
            pen = QPen(
                _textrect_show_color(), 3 / self.get_scale(), Qt.PenStyle.SolidLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        painter.restore()

    def _draw_background_only(self, painter: QPainter):
        """Draw background pixmap only (no border, no badge).

        Used by the paint path where accessories must be drawn
        behind text via SourceOver, but the border needs to be on top.
        """
        br = self.boundingRect()
        painter.save()
        if self.effect_renderer.background_pixmap is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(br.toRect(), self.effect_renderer.background_pixmap)
        painter.restore()

    def _draw_border_rect(self, painter: QPainter):
        """Draw selection/display border only (no background pixmap)."""
        br = self.boundingRect()
        painter.save()
        draw_rect = self.draw_rect and not self.under_ctrl
        if self._text_overflows and not self.is_editting():
            pen = QPen(
                QColor(255, 200, 0, 200), 3.5 / self.get_scale(), Qt.PenStyle.SolidLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        elif self.isSelected() and not self.is_editting():
            pen = QPen(
                TEXTRECT_SELECTED_COLOR, 3.5 / self.get_scale(), Qt.PenStyle.DashLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        elif draw_rect:
            pen = QPen(
                _textrect_show_color(), 3 / self.get_scale(), Qt.PenStyle.SolidLine
            )
            painter.setPen(pen)
            painter.drawRect(self.unpadRect(br))
        painter.restore()

    def _draw_seq_badge(self, painter: QPainter):
        """Draw sequence number badge at top-left corner of content area."""
        from utils.config import pcfg
        if self._reorder_seq < 0 and (self._hide_badge or not pcfg.show_seq_badge):
            return
        scale = self.get_scale()
        font_size = max(6, 11 / scale)

        content_rect = self.unpadRect(self.boundingRect())

        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(font_size))

        if self._reorder_seq >= 0:
            seq_text = str(self._reorder_seq + 1)
        else:
            seq_text = str(self.idx + 1)

        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(seq_text) + 8
        text_h = fm.height() + 4

        badge_rect = QRectF(
            content_rect.x(),
            content_rect.y(),
            text_w,
            text_h,
        )

        painter.save()
        painter.setOpacity(1.0)

        if self.isSelected():
            from ui.misc import get_theme_color

            bg = get_theme_color()
            bg.setAlpha(200)
        else:
            bg = QColor(0, 0, 0, 170)

        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge_rect, 3, 3)

        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, seq_text)

        painter.restore()

    def startEdit(self, pos: QPointF = None) -> None:
        self.pre_editing = False
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        self.begin_edit.emit(self.idx)
        if pos is not None:
            hit = self.layout.hitTest(pos, None)
            cursor = self.textCursor()
            cursor.setPosition(hit)
            self.setTextCursor(cursor)

    def endEdit(self, keep_focus=True) -> None:
        self.end_edit.emit(self.idx)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.refresh_cache_policy()
        if keep_focus:
            self.setFocus()

    def isEditing(self) -> bool:
        return (
            self.textInteractionFlags() == Qt.TextInteractionFlag.TextEditorInteraction
        )

    def isMultiFontSize(self) -> bool:
        doc = self.document()
        block = doc.firstBlock()
        if block.isValid():
            it = block.begin()
            if it.atEnd():
                firstFontSize = block.charFormat().fontPointSize()
            else:
                # empty blocks causes frozen for pyside==6.8.1
                # also randomly freezes pyqt==6.6.1 https://github.com/dmMaze/BallonsTranslator/issues/736
                firstFontSize = it.fragment().charFormat().fontPointSize()
        else:
            return False
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                font_size = fragment.charFormat().fontPointSize()
                if not firstFontSize == font_size:
                    return True
                it += 1
            block = block.next()
        return False

    def minFontSize(self, to_px=True):
        doc = self.document()
        block = doc.firstBlock()
        min_font_size = self.textCursor().charFormat().fontPointSize()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                font_size = fragment.charFormat().fontPointSize()
                min_font_size = min(min_font_size, font_size)
                it += 1
            block = block.next()
        if to_px:
            min_font_size = pt2px(min_font_size)
        return min_font_size

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self.isEditing():
            self.startEdit(pos=event.pos())
        else:
            super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if self.textInteractionFlags() != Qt.TextInteractionFlag.TextEditorInteraction:
            canvas = self.scene()
            if canvas.alignment_enabled:
                self._apply_snap()
            self.moving.emit(self)

    # QT 5.15.x causing segmentation fault
    def contextMenuEvent(self, event):
        return super().contextMenuEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.oldPos = self.pos()
            self.leftbutton_pressed.emit(self.idx)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.oldPos != self.pos():
                self.moved.emit()
        self.scene().clear_snap_guides()
        super().mouseReleaseEvent(event)

    def _apply_snap(self):
        """Check alignment to nearby text blocks and snap position."""
        # Rotated text blocks are custom-designed — skip alignment
        if self.angle != 0:
            return

        canvas = self.scene()
        my_rect = self.absBoundingRect()

        # Collect content rects of all other non-rotated TextBlkItem instances
        target_rects = []
        for child in canvas.textLayer.childItems():
            if isinstance(child, TextBlkItem) and child is not self and child.angle == 0:
                target_rects.append(child.absBoundingRect())

        if not target_rects:
            canvas.clear_snap_guides()
            return

        threshold = SNAP_THRESHOLD / canvas.scale_factor
        adj_x, adj_y, guides = compute_snap(my_rect, target_rects, threshold)

        if adj_x != my_rect[0] or adj_y != my_rect[1]:
            pad = self.padding()
            self.setPos(QPointF(adj_x - pad, adj_y - pad))

        if guides:
            canvas.set_snap_guides(guides)
        else:
            canvas.clear_snap_guides()

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.hover_move.emit(self.idx)
        return super().hoverMoveEvent(event)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.hover_enter.emit(self.idx)
        return super().hoverEnterEvent(event)

    def toPixmap(self) -> QPixmap:
        pixmap = QPixmap(self.boundingRect().size().toSize())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        doc = self.document()
        doc.drawContents(painter)
        painter.end()
        return pixmap

    def toHtml(self) -> str:
        html = super().toHtml()
        tables = table_pattern.findall(html)
        if tables:
            _, td = td_pattern.findall(html)[0]
            html = tables[0] + td + "</body></html>"

        return html.replace(">\n<", "><")

    def get_fontformat(self) -> FontFormat:
        fmt = self.textCursor().charFormat()
        font = fmt.font()
        color = fmt.foreground().color()
        fontformat = self.fontformat.deepcopy()
        fontformat.frgb = [color.red(), color.green(), color.blue()]
        fontformat.font_weight = font.weight()
        fontformat.font_family = font.family()
        if self.isEditing():
            fontformat.font_size = pt2px(font.pointSizeF())
        else:
            fontformat.font_size = self.minFontSize()
        fontformat.bold = font.bold()
        fontformat.underline = font.underline()
        fontformat.italic = font.italic()
        # Preserve gradient settings
        fontformat.gradient_enabled = self.fontformat.gradient_enabled
        fontformat.gradient_start_color = self.fontformat.gradient_start_color
        fontformat.gradient_end_color = self.fontformat.gradient_end_color
        fontformat.gradient_angle = self.fontformat.gradient_angle
        fontformat.gradient_size = self.fontformat.gradient_size
        return fontformat

    def set_fontformat(
        self,
        ffmat: FontFormat,
        set_char_format=False,
        set_stroke_width=True,
        set_effect=True,
    ):
        self.repainting = True
        if self.fontformat.vertical != ffmat.vertical:
            self.setVertical(ffmat.vertical)

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        format = cursor.charFormat()
        font = self.document().defaultFont()

        font.setFamily(ffmat.font_family)
        font.setPointSizeF(max(ffmat.size_pt, 1.0))
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.NoSubpixelAntialias
        )
        if ffmat._style_name:
            font.setStyleName(ffmat._style_name)

        fweight = ffmat.font_weight
        if fweight is None:
            fweight = font.weight()
            ffmat.font_weight = fweight
        font.setBold(ffmat.bold)

        self.document().setDefaultFont(font)
        format.setFont(font)
        if ffmat.gradient_enabled:
            gradient = self.get_text_gradient(ffmat)
            format.setForeground(gradient)
        else:
            format.setForeground(QColor(*ffmat.foreground_color()))
        if not ffmat.bold:
            format.setFontWeight(fweight)
        format.setFontItalic(ffmat.italic)
        format.setFontUnderline(ffmat.underline)
        if not ffmat.vertical:
            format.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
            format.setFontLetterSpacing(ffmat.letter_spacing * 100)
        cursor.setCharFormat(format)
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.setBlockCharFormat(format)
        if set_char_format:
            cursor.setCharFormat(format)
        cursor.clearSelection()
        # https://stackoverflow.com/questions/37160039/set-default-character-format-in-qtextdocument
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
        self.stroke_qcolor = QColor(*ffmat.stroke_color())

        if set_effect:
            self.setShadow(ffmat, repaint=False)
        if set_stroke_width:
            self.setStrokeWidth(ffmat.stroke_width, repaint_background=False)
        self.setOpacity(ffmat.opacity)

        alignment_qt_flag = [
            Qt.AlignmentFlag.AlignLeft,
            Qt.AlignmentFlag.AlignCenter,
            Qt.AlignmentFlag.AlignRight,
        ][ffmat.alignment]
        doc = self.document()
        op = doc.defaultTextOption()
        op.setAlignment(alignment_qt_flag)
        doc.setDefaultTextOption(op)

        if ffmat.vertical:
            self.setLetterSpacing(ffmat.letter_spacing)
        self.setLineSpacing(ffmat.line_spacing)

        # Preserve gradient properties
        self.fontformat.gradient_enabled = ffmat.gradient_enabled
        self.fontformat.gradient_start_color = ffmat.gradient_start_color
        self.fontformat.gradient_end_color = ffmat.gradient_end_color
        self.fontformat.gradient_angle = ffmat.gradient_angle
        self.fontformat.gradient_size = ffmat.gradient_size

        self.fontformat.merge(ffmat)

        if self.fontformat.gradient_enabled:
            self.update()

        self.repainting = False
        if set_effect or set_stroke_width:
            self.repaint_background()

    def updateBlkFormat(self):
        fmt = self.get_fontformat()
        self.blk.fontformat.merge(fmt)

    def set_cursor_cfmt(
        self, cursor: QTextCursor, cfmt: QTextCharFormat, merge_char: bool = False
    ):
        doc_is_empty = self.document().isEmpty()
        if merge_char:
            self.block_change_signal = True
            cursor.mergeCharFormat(cfmt)
            self.block_change_signal = False
        cursor.mergeBlockCharFormat(cfmt)
        cursor.clearSelection()
        self.setTextCursor(cursor)
        if doc_is_empty:
            self.document().setDefaultFont(cursor.blockCharFormat().font())

    def _before_set_ffmt(self, set_selected: bool, restore_cursor: bool):
        self.is_formatting = True
        cursor = self.textCursor()

        cursor_pos = None
        if restore_cursor:
            cursor_pos = (
                (cursor.position(), cursor.anchor().__pos__())
                if restore_cursor
                else None
            )

        if set_selected:
            has_set_all = not cursor.hasSelection()
            if has_set_all:
                cursor.select(QTextCursor.SelectionType.Document)
        else:
            has_set_all = False
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)

        cursor.beginEditBlock()
        return cursor, dict(cursor_pos=cursor_pos, has_set_all=has_set_all)

    def _after_set_ffmt(
        self,
        cursor: QTextCursor,
        repaint_background: bool,
        restore_cursor: bool,
        cursor_pos: Tuple,
        has_set_all: bool,
    ):

        if restore_cursor:
            if cursor_pos is not None:
                pos1, pos2 = cursor_pos
                if has_set_all:
                    cursor.setPosition(pos1)
                else:
                    cursor.setPosition(min(pos1, pos2))
                    cursor.setPosition(max(pos1, pos2), QTextCursor.MoveMode.KeepAnchor)
                self.setTextCursor(cursor)

        if repaint_background:
            self.repaint_background()

        # endEditBlock 会触发 deferred documentChanged → reLayoutEverything，
        # 但 layout 已在之前显式调用的 reLayoutEverything/squeezeBoundingRect 中完成，
        # 抑制这次冗余重排避免潜在的 minSize 不一致。
        self.layout.relayout_on_changed = False
        cursor.endEditBlock()
        self.layout.relayout_on_changed = True
        self.is_formatting = False

    def setFontFamily(
        self,
        value: str,
        style_name: str = "",
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        self.repainting = True
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.layout.relayout_on_changed = False
        self._doc_set_font_family(value, style_name, cursor)
        self.layout.relayout_on_changed = True
        self.layout.reLayoutEverything()
        self.repainting = False
        if repaint_background:
            self.repaint_background()
        self.update()
        self.fontformat.font_family = value
        if style_name:
            self.fontformat._style_name = style_name

    def _doc_set_font_family(self, value: str, style_name: str, cursor: QTextCursor):
        from utils import shared

        actual_family = value
        actual_style = style_name
        is_merged_family = False  # 标记是否回退到了带字重后缀的原始家族名

        # 处理归并后的家族名，映射回 Qt 能识别的原始名
        if value in shared.FONT_FAMILY_ALIAS:
            raw_list = shared.FONT_FAMILY_ALIAS[value]
            matched_raw = None

            # 精确匹配：选择 Bold 时，优先找名为 "XXX Bold" 的原始家族
            if style_name:
                for raw_fam in raw_list:
                    if raw_fam.endswith(f" {style_name}"):
                        matched_raw = raw_fam
                        break

            # 常规匹配：选择 Regular 时，优先找没有字重后缀的原始家族
            if matched_raw is None and style_name in ("Regular", "Normal", ""):
                for raw_fam in raw_list:
                    if raw_fam == value or not any(
                        raw_fam.endswith(f" {s}")
                        for s in [
                            "Thin",
                            "Light",
                            "Bold",
                            "Black",
                            "Italic",
                            "Oblique",
                            "Medium",
                            "SemiBold",
                            "DemiBold",
                            "Heavy",
                            "ExtraLight",
                            "ExtraBold",
                        ]
                    ):
                        matched_raw = raw_fam
                        break

            # 兜底：使用列表中的第一个
            if matched_raw is None and raw_list:
                matched_raw = raw_list[0]

            if matched_raw is not None:
                actual_family = matched_raw
                if actual_family != value:
                    is_merged_family = True
        doc = self.document()  # <--- 修复 UnboundLocalError：补回这行关键代码
        lastpos = doc.rootFrame().lastPosition()
        if cursor.selectionStart() == 0 and cursor.selectionEnd() == lastpos:
            font = doc.defaultFont()
            font.setFamily(actual_family)
            doc.setDefaultFont(font)
        sel_start = cursor.selectionStart()
        sel_end = cursor.selectionEnd()
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()

                frag_start = fragment.position()
                frag_end = frag_start + fragment.length()
                pos2 = min(frag_end, sel_end)
                pos1 = max(frag_start, sel_start)
                if pos1 < pos2:
                    cfmt = fragment.charFormat()
                    under_line = cfmt.fontUnderline()
                    cfont = cfmt.font()

                    font = QFont(
                        actual_family, max(1, cfont.pointSize()), cfont.weight(), cfont.italic()
                    )
                    font.setPointSizeF(max(1.0, cfont.pointSizeF()))

                    # 样式名到 Qt Weight 枚举的映射
                    _style_to_qt_weight = {
                        "Thin": QFont.Weight.Thin,
                        "ExtraLight": QFont.Weight.ExtraLight,
                        "UltraLight": QFont.Weight.ExtraLight,
                        "Light": QFont.Weight.Light,
                        "Regular": QFont.Weight.Normal,
                        "Normal": QFont.Weight.Normal,
                        "Book": QFont.Weight.Normal,
                        "Medium": QFont.Weight.Medium,
                        "SemiBold": QFont.Weight.DemiBold,
                        "DemiBold": QFont.Weight.DemiBold,
                        "Bold": QFont.Weight.Bold,
                        "ExtraBold": QFont.Weight.ExtraBold,
                        "UltraBold": QFont.Weight.ExtraBold,
                        "Black": QFont.Weight.Black,
                        "Heavy": QFont.Weight.Black,
                    }

                    # 决定传给 Qt 的最终 StyleName
                    final_style_name = actual_style

                    if is_merged_family:
                        # 情况A：归并字体(如"尚古圆体 Bold")，Family已包含字重
                        # 此时 setStyleName 必须为空或 "Regular"，否则 Qt 会找不到实例
                        final_style_name = ""
                    else:
                        # 判断是否为 VF 字体的虚拟样式
                        is_virtual = actual_style in shared.VIRTUAL_FONT_STYLES.get(
                            actual_family, set()
                        )
                        if is_virtual:
                            # 情况B：VF字体的虚拟样式(如思源黑体+Bold)，Qt中没有这个实例
                            final_style_name = ""

                    font.setStyleName(final_style_name)

                    # 驱动数值字重（对 VF 字体和中间字重至关重要）
                    if actual_style in _style_to_qt_weight:
                        font.setWeight(_style_to_qt_weight[actual_style])
                        # 根据字重设置 Bold 状态，防止被默认逻辑覆盖
                        # 注意：Medium(500)/SemiBold(600)/DemiBold(600) 是中间字重，
                        # 不是 Bold(700)，调用 setBold(True) 会覆盖字重为 Bold 导致渲染变形。
                        font.setBold(
                            actual_style
                            in (
                                "Bold",
                                "ExtraBold",
                                "UltraBold",
                                "Black",
                                "Heavy",
                            )
                        )

                    font.setWordSpacing(cfont.wordSpacing())
                    font.setLetterSpacing(
                        cfont.letterSpacingType(), cfont.letterSpacing()
                    )

                    cfmt.setFont(font)
                    cfmt.setFont(font)
                    cfmt.setFontUnderline(under_line)
                    cursor.setPosition(pos1)
                    cursor.setPosition(pos2, QTextCursor.MoveMode.KeepAnchor)
                    cursor.setCharFormat(cfmt)
                it += 1
            block = block.next()
        cfmt = cursor.charFormat()
        cfmt.setFontFamily(actual_family)
        self.set_cursor_cfmt(cursor, cfmt)

    def setFontWeight(
        self,
        value: float,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        cfmt = QTextCharFormat()
        cfmt.setFontWeight(value)
        self.set_cursor_cfmt(cursor, cfmt, True)
        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def setFontItalic(
        self,
        value: bool,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        cfmt = QTextCharFormat()
        cfmt.setFontItalic(value)
        self.set_cursor_cfmt(cursor, cfmt, True)
        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def setFontUnderline(
        self,
        value: bool,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        cfmt = QTextCharFormat()
        cfmt.setFontUnderline(value)
        self.set_cursor_cfmt(cursor, cfmt, True)
        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def setGradientEnabled(
        self,
        value: bool,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        self.fontformat.gradient_enabled = value
        cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        cfmt = QTextCharFormat()
        if value:
            gradient = self.get_text_gradient()
            cfmt.setForeground(gradient)
        else:
            cfmt.setForeground(QColor(*self.fontformat.foreground_color()))

        self.set_cursor_cfmt(cursor, cfmt, True)
        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def get_text_gradient(self, fontformat: FontFormat = None, *, persistent: bool = False):
        return self.effect_renderer.get_text_gradient(fontformat, persistent=persistent)

    def _text_transform_is_neutral(self) -> bool:
        return self.geometry_controller.is_neutral()

    def _effective_text_transform(self):
        return self.geometry_controller.effective()

    def set_text_transform(
        self,
        state: TextTransformState = None,
        *,
        preview: bool = False,
    ) -> bool:
        """Apply complete stack/layout state, optionally as a preview.

        Mirrors upstream ``TextBlkItem.set_text_transform`` (v1.5.9): the
        geometry controller returns whether the visual state actually changed;
        only then is ``visual_geometry_changed`` emitted.
        """
        changed = self.geometry_controller.set(state, preview=preview)
        if changed:
            self.visual_geometry_changed.emit()
        return changed

    def clear_text_transform_preview(self) -> bool:
        changed = self.geometry_controller.clear_preview()
        if changed:
            self.visual_geometry_changed.emit()
        return changed

    def setLineSpacing(
        self,
        value: float,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        self.is_formatting = True
        self.fontformat.line_spacing = value
        self.layout.setLineSpacing(value)
        if repaint_background:
            self.repaint_background()
            self.update()
        self.is_formatting = False

    def setLineSpacingType(
        self,
        value: int,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        self.is_formatting = True
        self.fontformat.line_spacing_type = value
        self.layout.setLineSpacingType(value)
        if repaint_background:
            self.repaint_background()
            self.update()
        self.is_formatting = False

    def setPunctuationAlignment(
        self,
        value: int,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
    ):
        """Deprecated: punctuation alignment is now a global setting.
        Kept for backward compatibility. Updates global config and layout."""
        self.is_formatting = True
        self.fontformat.punctuation_alignment = value
        self.layout.setPunctuationPosition(value)
        if repaint_background:
            self.repaint_background()
            self.update()
        self.is_formatting = False

    def setLetterSpacing(
        self,
        value: float,
        repaint_background: bool = True,
        set_selected: bool = False,
        restore_cursor: bool = False,
        force=False,
    ):
        self.is_formatting = True
        self.fontformat.letter_spacing = value
        if self.fontformat.vertical:
            self.layout.setLetterSpacing(value)
        else:
            cursor = QTextCursor(self.document())
            char_fmt = QTextCharFormat()
            char_fmt.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
            char_fmt.setFontLetterSpacing(value * 100)
            cursor.select(QTextCursor.SelectionType.Document)
            self.set_cursor_cfmt(cursor, char_fmt, True)

        if repaint_background:
            self.repaint_background()
            self.update()

        self.is_formatting = False

    def setFontColor(
        self,
        value: Tuple,
        repaint_background: bool = False,
        set_selected: bool = False,
        restore_cursor: bool = False,
        force=False,
    ):
        cursor, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        cfmt = QTextCharFormat()
        cfmt.setForeground(QColor(*[max(0, min(255, int(c))) for c in value]))
        self.set_cursor_cfmt(cursor, cfmt, True)
        self._after_set_ffmt(
            cursor,
            repaint_background=repaint_background,
            restore_cursor=restore_cursor,
            **after_kwargs,
        )

    def setStrokeColor(self, scolor, **kwargs):
        if isinstance(scolor, QColor):
            self.stroke_qcolor = scolor
        else:
            clamped = [max(0, min(255, int(c))) for c in scolor[:3]]
            self.stroke_qcolor = QColor(*clamped)
        self.fontformat.srgb = [
            self.stroke_qcolor.red(),
            self.stroke_qcolor.green(),
            self.stroke_qcolor.blue(),
        ]
        self.repaint_background()
        self.update()

    def setStrokeWidth(
        self,
        stroke_width: float,
        padding=True,
        repaint_background=True,
        restore_cursor=False,
        **kwargs,
    ):

        cursor, after_kwargs = self._before_set_ffmt(
            set_selected=False, restore_cursor=restore_cursor
        )

        self.fontformat.stroke_width = stroke_width
        if stroke_width > 0 and padding:
            p = self.layout.max_font_size(to_px=True) * (stroke_width + 0.05) / 2
            self.setPadding(p)

        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)
        if repaint_background:
            self.update()

    def setRelFontSize(
        self,
        value: float,
        repaint_background: bool = False,
        set_selected: bool = False,
        restore_cursor: bool = False,
        clip_size: bool = False,
        **kwargs,
    ):
        from utils.config import pcfg

        max_pt = px2pt(pcfg.max_font_size)
        self.layout.relayout_on_changed = False
        _, after_kwargs = self._before_set_ffmt(set_selected, restore_cursor)
        doc = self.document()
        cursor = QTextCursor(doc)
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                old_font_size = fragment.charFormat().fontPointSize()
                new_font_size = min(round(old_font_size * value, 2), max_pt)
                cfmt = fragment.charFormat()
                cfmt.setFontPointSize(new_font_size)
                pos1 = fragment.position()
                pos2 = pos1 + fragment.length()
                cursor.setPosition(pos1)
                cursor.setPosition(pos2, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(cfmt)
                it += 1
            block = block.next()
        self.layout.relayout_on_changed = True
        self.layout.reLayoutEverything()
        if clip_size:
            self.squeezeBoundingRect(True, repaint=False)

        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def setFontSize(
        self,
        value: float,
        repaint_background: bool = False,
        set_selected: bool = False,
        restore_cursor: bool = False,
        clip_size: bool = False,
        **kwargs,
    ):
        """
        value should be point size
        """
        from utils.config import pcfg

        max_pt = px2pt(pcfg.max_font_size)
        value = min(value, max_pt)
        value = max(value, 1)

        cursor, after_kwargs = self._before_set_ffmt(
            set_selected=set_selected, restore_cursor=restore_cursor
        )
        self.layout.relayout_on_changed = False
        if self.fontformat.stroke_width != 0:
            repaint_background = True
        if repaint_background:
            fs = pt2px(max(self.layout.max_font_size(), value))
            self.layout.relayout_on_changed = False
            if self.fontformat.stroke_width > 0:
                self.setPadding(fs * (self.fontformat.stroke_width + 0.05) / 2)
            self.layout.relayout_on_changed = True
        cfmt = QTextCharFormat()
        cfmt.setFontPointSize(value)
        self.set_cursor_cfmt(cursor, cfmt, True)
        self.layout.relayout_on_changed = True
        self.layout.reLayoutEverything()
        if clip_size:
            self.squeezeBoundingRect(cond_on_alignment=True)

        self._after_set_ffmt(cursor, repaint_background, restore_cursor, **after_kwargs)

    def setAlignment(
        self, value, restore_cursor=False, repaint_background=True, *args, **kwargs
    ):
        cursor, after_kwargs = self._before_set_ffmt(
            set_selected=False, restore_cursor=restore_cursor
        )
        if isinstance(value, int):
            qt_align_flag = [
                Qt.AlignmentFlag.AlignLeft,
                Qt.AlignmentFlag.AlignCenter,
                Qt.AlignmentFlag.AlignRight,
            ][value]
        doc = self.document()
        op = doc.defaultTextOption()
        op.setAlignment(qt_align_flag)
        doc.setDefaultTextOption(op)
        if repaint_background:
            self.repaint_background()
            self.update()
        self.fontformat.alignment = value
        self._after_set_ffmt(
            cursor,
            repaint_background=False,
            restore_cursor=restore_cursor,
            **after_kwargs,
        )

    def get_char_fmts(self) -> List[QTextCharFormat]:
        cursor = self.textCursor()

        cursor.movePosition(QTextCursor.MoveOperation.Start)
        char_fmts = []
        while True:
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
            cursor.clearSelection()
            char_fmts.append(cursor.charFormat())
            if cursor.atEnd():
                break
        return char_fmts

    def setShadow(self, fmt: FontFormat, repaint=True):
        self.fontformat.shadow_radius = fmt.shadow_radius
        self.fontformat.shadow_strength = fmt.shadow_strength
        self.fontformat.shadow_color = fmt.shadow_color
        self.fontformat.shadow_offset = fmt.shadow_offset
        self.fontformat.shadow_include_stroke = fmt.shadow_include_stroke
        if self.fontformat.shadow_radius > 0:
            self.setPadding(self.layout.max_font_size(to_px=True))
        if repaint:
            self.repaint_background()

    def setBGAttribute(self, attr_name: str, value, repaint=True):
        setattr(self.fontformat, attr_name, value)
        if repaint:
            self.repaint_background()
            self.update()

    def setGradientAttribute(self, attr_name: str, value):
        self.old_ffmt_values = {}
        self.old_ffmt_values[attr_name] = self.fontformat[attr_name]
        setattr(self.fontformat, attr_name, value)
        self.setGradientEnabled(self.fontformat.gradient_enabled)
        self.old_ffmt_values = None

    def setOpacity(self, opacity: float):
        super().setOpacity(opacity)
        self.fontformat.opacity = opacity

    def setPlainTextAndKeepUndoStack(self, text: str):
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def squeezeBoundingRect(self, cond_on_alignment: bool = False, repaint=True):
        mh, mw = self.layout.minSize()
        if mh == 0 or mw == 0:
            return
        br = self.absBoundingRect(qrect=True)
        br_w, br_h = br.width(), br.height()

        if self.fontformat.vertical:
            if cond_on_alignment:
                mh = br.height()
        else:
            if cond_on_alignment:
                mw = br.width()

        if np.abs(br_w - mw) > 0.001 or np.abs(br_h - mh) > 0.001:
            P = self.padding() * 2
            mh += P
            mw += P
            self.set_size(mw, mh, set_layout_maxsize=True, set_blk_size=True)
            if self.under_ctrl:
                self.doc_size_changed.emit(self.idx)
            if repaint:
                self.repaint_background()

    def set_size(self, w: float, h: float, set_layout_maxsize=False, set_blk_size=True):
        """
        rotation invariant
        """

        # 裁剪模式保护：文字溢出时禁止改变 _display_rect 尺寸（撑大和缩小都不行）
        # 用户拖拽调整（startReshape 清除了 _text_overflows）后恢复正常
        if self._text_overflows:
            w = max(w, self._display_rect.width())
            h = max(h, self._display_rect.height())
            w = min(w, self._display_rect.width())
            h = min(h, self._display_rect.height())

        # 安全钳位：阻止 _display_rect 缩到 0（极端排版边界情况）
        w = max(w, 1.0)
        h = max(h, 1.0)

        self.geometry_controller.resize(
            w,
            h,
            set_layout_maxsize=set_layout_maxsize,
            set_blk_size=set_blk_size,
        )
        self.visual_geometry_changed.emit()
