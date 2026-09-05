"""Fork 兼容层：渲染入口已切换到新引擎 ``ui/text_engine/item.py::TextBlkItem``。

本模块不再是渲染实现（文档布局/注解/绘制全部走引擎），只保留 fork 消费方
（scenetext_manager/canvas/textedit_commands/…）依赖的 API 面：

- fork 独有信号 ``moved``/``doc_size_changed``/``hover_move``：由引擎侧信号
  （``move_interaction_finished``/``visual_geometry_changed``）桥接而来；
- fork 独有属性 ``oldPos``/``oldRect``（别名引擎的 ``_old_pos``/``_old_rect``）、
  ``_hide_badge``/``_reorder_seq``（路径重排角标）；
- fork 特性实现：角标可见性、溢出裁剪（``clip_text_overflow``/``overflow_mode``
  + 黄色提示框）、水平半角括号自动替换、变换的 ``TextTransformState`` 适配
  （引擎传 ``TextTransformStack``）、``FontFormat.bold``/``_style_name`` 补写；
- fork 交互：对齐吸附、块悬停移动光标、``moved`` 仅在位置真正变化时发出。
"""

from typing import List, Tuple, Union

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextCursor,
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

from utils.config import pcfg
from utils.fontformat import (
    FontFormat,
    TextTransformState,
    TextTransformStack,
)
from utils.text_alignment import SNAP_THRESHOLD, compute_snap
from utils.textblock import TextBlock as TextBlock

from ui.text_engine.item import TextBlkItem as _EngineTextBlkItem

TEXTRECT_SHOW_COLOR = QColor(30, 147, 229, 170)
TEXTRECT_SELECTED_COLOR = QColor(248, 64, 147, 170)


def _textrect_show_color():
    return TEXTRECT_SHOW_COLOR


class TextBlkItem(_EngineTextBlkItem):
    """新引擎 ``TextBlkItem`` + fork 兼容面。"""

    # fork 独有/签名不同的信号。引擎侧对应：``move_interaction_finished``、
    # 无（doc_size 经 visual_geometry_changed/docSizeChanged 桥接）、
    # 无（hover_move 无外部消费者，仅保留）。
    moved = Signal()
    doc_size_changed = Signal(int)
    hover_move = Signal(int)

    # 「→ ｢、」→ ｣ 双向映射（水平半角括号替换）
    _HW_CORNER_MAP = {"「": "｢", "」": "｣"}

    def __init__(
        self,
        blk: TextBlock = None,
        idx: int = 0,
        set_format=True,
        show_rect=False,
        *args,
        **kwargs,
    ):
        # 必须在 super().__init__ 之前建立：引擎 init → initTextBlock → setPlainText
        # 的首次布局若溢出，会经 size_enlarged 同步回调 on_document_enlarged → set_size，
        # 此时本行若在 super 之后会读未初始化属性。
        self._text_overflows: bool = False  # 文字超出文本框，启用裁剪 + 黄色边框
        super().__init__(blk, idx, set_format, show_rect, *args, **kwargs)
        self.oldPos = QPointF()
        self.oldRect = QRectF()
        self._hide_badge = False
        self._reorder_seq: int = -1  # >=0 when in path-reorder mode; overrides badge number
        self._block_hw_sub = False  # prevents recursion in half-width corner bracket substitution
        # 引擎侧无这两个信号，桥接（moved 保持 fork 的「位置有变化才发」语义）
        self.move_interaction_finished.connect(self._fork_bridge_moved)
        self.visual_geometry_changed.connect(self._fork_bridge_doc_size_changed)
        # 半角括号自动替换（fork 特性，引擎侧无）
        self.document().contentsChange.connect(self._on_contents_change_for_hw)
        self.refresh_seq_badge()

    # ── 属性别名（fork 消费者读写 oldPos/oldRect，引擎内部用 _old_pos/_old_rect）──

    @property
    def oldPos(self) -> QPointF:
        return self._old_pos

    @oldPos.setter
    def oldPos(self, value: QPointF) -> None:
        self._old_pos = value

    @property
    def oldRect(self) -> QRectF:
        return self._old_rect

    @oldRect.setter
    def oldRect(self, value: QRectF) -> None:
        self._old_rect = value

    # ── 信号桥接 ──────────────────────────────────────────────────────────

    def _fork_bridge_moved(self) -> None:
        if self.oldPos != self.pos():
            self.moved.emit()

    def _fork_bridge_doc_size_changed(self) -> None:
        self.doc_size_changed.emit(self.idx)

    def docSizeChanged(self) -> None:
        # 引擎侧 docSizeChanged 只同步几何；fork 消费方还依赖 doc_size_changed 信号
        super().docSizeChanged()
        self.doc_size_changed.emit(self.idx)

    # ── fork 方法（引擎侧无同名实现）───────────────────────────────────────

    def is_editting(self) -> bool:
        return self.isEditing()

    def endEdit(self, keep_focus=True) -> None:
        # 编辑结束是溢出收敛的最自然时机：文字缩短回框内时解除裁剪，
        # 避免黄框与裁剪被烘进成图（见 settle_overflow_state）。
        super().endEdit(keep_focus)
        self.settle_overflow_state()

    def settle_overflow_state(self) -> bool:
        """文字已放回框内时解除溢出裁剪状态。

        ``_text_overflows`` 除 startReshape（拖拽调框）外从不复位：文字改短
        后标志残留，导出画像会继续裁剪文本并画出金黄边框。编辑结束与导出
        前各调用一次，按当前布局尺寸重新求值——放得下即解锁，放不下继续
        保留黄框提示。
        """
        if not self._text_overflows or self.is_editting():
            return False
        size = self.documentSize()
        if size is None or size.width() <= 0 or size.height() <= 0:
            return False
        pad = self.padding()
        box = self._display_rect
        if (
            size.width() <= box.width() - pad * 2
            and size.height() <= box.height() - pad * 2
        ):
            self._text_overflows = False
            self.update()
            return True
        return False

    def get_scale(self) -> float:
        tl = self.topLevelItem()
        if tl is not None:
            return tl.scale()
        return self.scale()

    def release_render_resources(self) -> None:
        self.geometry_controller.release_render_resources()

    def refresh_seq_badge(self) -> None:
        """刷新序号角标（fork 语义：重排预览优先，其次 show_seq_badge 开关）。"""
        if self._reorder_seq >= 0:
            # _reorder_seq 是接触顺序的 0 基下标；角标按 1 基显示，
            # 且 set_order_number_override 用 max(1, ...) 兜底，直接传 0 会让
            # 第 1、2 块都显示 "1"（2026-08-25）。
            self.set_order_number_override(self._reorder_seq + 1)
        else:
            self.set_order_number_override(None)
        self.set_order_badge_visible(
            (self._reorder_seq >= 0)
            or (pcfg.show_seq_badge and not self._hide_badge)
        )
        self._sync_order_badge()

    # ── 变换（fork 语义：TextTransformState；引擎侧 set_text_transform 传 stack）──

    def set_text_transform(
        self,
        state: Union[TextTransformState, TextTransformStack] = None,
        *,
        preview: bool = False,
    ) -> bool:
        if isinstance(state, TextTransformStack):
            glyph_slant = 0.0
            if self.fontformat is not None:
                glyph_slant = getattr(self.fontformat, "glyph_slant_angle", 0.0)
            state = TextTransformState(state, glyph_slant)
        changed = self.geometry_controller.set(state, preview=preview)
        if changed:
            self.visual_geometry_changed.emit()
        return changed

    def clear_text_transform_preview(self) -> bool:
        changed = self.geometry_controller.clear_preview()
        if changed:
            self.visual_geometry_changed.emit()
        return changed

    # ── 水平半角括号替换（fork 特性，引擎侧无）──────────────────────────

    @staticmethod
    def _hw_corner_reverse_map():
        return {"｢": "「", "｣": "」"}

    def _on_contents_change_for_hw(self, pos: int, removed: int, added: int):
        """Auto-substitute 「/」→ ｢/｣ when horizontal half-width mode is on."""
        if self._block_hw_sub or added <= 0:
            return
        if self.fontformat is None or self.fontformat.vertical:
            return
        if not (
            pcfg.halfwidth_jp_corner_brackets
            and pcfg.halfwidth_jp_corner_brackets_horizontal
        ):
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
        if self._block_hw_sub or self.fontformat is None or self.fontformat.vertical:
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
        if self._block_hw_sub or self.fontformat is None or self.fontformat.vertical:
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

    # ── 溢出处理（fork：clip_text_overflow 裁剪 / 否则自动撑大）──────────

    def on_document_enlarged(self):
        if pcfg.clip_text_overflow:
            # 裁剪模式：不撑大文本框，记录溢出状态，靠 paint 裁剪 + 黄色边框。
            # 不能在此处 setMaxSize 回钳——本方法由 layout.size_enlarged 在
            # reLayout 中途触发，再改 layout 尺寸会重入几何变更导致 Qt 崩溃；
            # 显示盒子由 geometry controller 的 display_rect 决定，不随 layout
            # 的 max_height 增长。
            self._text_overflows = True
            self.update()
        else:
            size = self.documentSize()
            self.set_size(size.width(), size.height())

    def startReshape(self):
        # fork 语义：用户拖拽调整文本框 → 解除裁剪状态，恢复自动撑大行为
        self._text_overflows = False
        super().startReshape()

    def set_size(
        self,
        w: float,
        h: float,
        set_layout_maxsize=False,
        set_blk_size=True,
    ) -> None:
        # fork 语义：溢出裁剪态下盒子锁定，直到拖拽调整（startReshape）解除
        if self._text_overflows:
            return
        super().set_size(
            w, h,
            set_layout_maxsize=set_layout_maxsize,
            set_blk_size=set_blk_size,
        )

    @property
    def _display_rect(self) -> QRectF:
        return self.geometry_controller.display_rect

    @_display_rect.setter
    def _display_rect(self, rect: QRectF) -> None:
        self.geometry_controller.display_rect = rect

    def setRectFast(self, rect):
        """Fast geometry update during drag resize — skip expensive layout.

        Updates position and bounding rect immediately to keep visual sync
        with the control frame. Full text layout (setMaxSize) is deferred
        to the debounced resize timer.
        """
        padded = self.padRect(rect)
        self.prepareGeometryChange()
        self._display_rect = QRectF(0, 0, padded.width(), padded.height())
        self.setPos(padded.topLeft())

    def _get_overflow_clip_rect(self) -> QRectF:
        """Return the image boundary rect in item-local coordinates for painter clipping.

        When overflow mode is active, this clips text rendering to the canvas boundary.
        Returns an empty QRectF if no clipping is needed.
        """
        if not pcfg.overflow_mode:
            return QRectF()
        scene = self.scene()
        if scene is None or not hasattr(scene, "baseLayer"):
            return QRectF()
        img_scene_rect = scene.baseLayer.sceneBoundingRect()
        return self.mapFromScene(img_scene_rect).boundingRect()

    # ── 绘制（fork 组合顺序：溢出裁剪 → 背景 → 文本 → 边框）────────────

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget
    ) -> None:
        controller = getattr(self, "geometry_controller", None)
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
        # 直调 Qt 层绘制（文档由引擎布局渲染），绕过引擎 item.paint 的二次包装
        QGraphicsTextItem.paint(self, painter, option, widget)
        if draw_clip:
            painter.restore()

        if not self.is_editting():
            # Border always draws outside the clip
            self._draw_border_rect(painter)

    def _draw_effects_pixmap(self, painter: QPainter):
        """Draw the stroke/shadow cache at the active device scale."""
        self.effect_renderer.ensure_host_background(painter)
        background = self.effect_renderer.background_pixmap
        if background is not None:
            # Point-based draw so the cache's devicePixelRatio governs
            # raster resolution; the pixmap is already at device scale.
            painter.drawPixmap(self.boundingRect().topLeft(), background)

    def _draw_accessories(self, painter: QPainter):
        br = self.boundingRect()
        painter.save()

        self._draw_effects_pixmap(painter)

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
        painter.save()
        self._draw_effects_pixmap(painter)
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

    def padRect(self, rect: QRectF) -> QRectF:
        p = self.padding()
        P = p * 2
        return QRectF(rect.x() - p, rect.y() - p, rect.width() + P, rect.height() + P)

    def unpadRect(self, rect: QRectF) -> QRectF:
        p = -self.padding()
        P = p * 2
        return QRectF(rect.x() - p, rect.y() - p, rect.width() + P, rect.height() + P)

    # ── 交互（fork：对齐吸附 + 块悬停光标 + 条件 moved 桥）──────────────

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self.isEditing():
            scene = self.scene()
            if scene is not None and getattr(scene, "alignment_enabled", False):
                self._apply_snap()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        scene = self.scene()
        if scene is not None and hasattr(scene, "clear_snap_guides"):
            scene.clear_snap_guides()

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.hover_enter.emit(self.idx)
        self._update_move_cursor()
        return super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.hover_move.emit(self.idx)
        self._update_move_cursor()
        return super().hoverMoveEvent(event)

    def _update_move_cursor(self) -> None:
        """Show the move cursor over a draggable block; give the text
        editor's I-beam back to the block while it is being edited
        (2026-08-18).

        While path-reorder mode is active, the block's own cursor is
        suppressed so the view's crosshair ("精确选择") stays visible
        across the whole canvas while a stroke is drawn over blocks
        (2026-08-25).
        """
        canvas = self.scene()
        if canvas is not None and getattr(canvas, "_reorder_mode", False):
            self.unsetCursor()
            return
        if self.is_editting():
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

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

    # ── 格式 ──────────────────────────────────────────────────────────
    # set_fontformat / setFontFamily 无 fork 补丁：face 派生缓存由
    # utils/face_resolver.sync_face 在写入点维护，引擎 set_fontformat
    # 显式写数据层 _style_name、setFontWeight/setFontItalic 同次派生
    # （2026-09 真值化，写入点统一走 utils/face_resolver.sync_face）。

    # ── 格式 setter 兼容（引擎签名收敛，fork 消费方仍传旧 kwargs）───────

    def setLineSpacing(self, value: float, **kwargs) -> None:
        super().setLineSpacing(value)

    def setLetterSpacing(self, value: float, **kwargs) -> None:
        super().setLetterSpacing(value)

    def setLineSpacingType(self, value: int, **kwargs) -> None:
        super().setLineSpacingType(value)
