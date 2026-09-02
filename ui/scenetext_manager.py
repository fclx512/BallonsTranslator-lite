import copy
from typing import List, Tuple, Union

import numpy as np
from qtpy.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,  # zoom rebuild debounce
    Signal,
)
from qtpy.QtGui import (
    QClipboard,
    QKeyEvent,
    QTextCharFormat,
    QTextCursor,
)
from qtpy.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

from .custom_widget import GroupFrame, TextCheckerLabel

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils import shared
from utils.fontformat import FontFormat
from utils.imgproc_utils import get_block_mask
from utils.text_alignment import (
    align_bottom,
    align_horizontal_center,
    align_left,
    align_right,
    align_top,
    align_vertical_center,
    distribute_horizontal,
    distribute_vertical,
)

from .canvas import Canvas
from .panel_rail import PanelRail
from .text_panel import FontFormatPanel
from .textedit_area import (
    QVBoxLayout,
    SourceTextEdit,
    TextEditListScrollArea,
    TransPairWidget,
    TransTextEdit,
    Widget,
)
from .textedit_commands import (
    ApplyFontformatCommand,
    MoveBlkItemsCommand,
    MultiPasteCommand,
    PageReplaceAllCommand,
    PageReplaceOneCommand,
    ResetAngleCommand,
    ReshapeItemCommand,
    RotateItemCommand,
    SqueezeCommand,
    sync_text_by_diff,
)
from .textitem import TextBlkItem, TextBlock


class CreateItemCommand(QUndoCommand):
    def __init__(self, blk_item: TextBlkItem, ctrl, parent=None):
        super().__init__(parent)
        self.blk_item = blk_item
        self.ctrl: SceneTextManager = ctrl
        self.op_count = -1
        self.ctrl.addTextBlock(self.blk_item)
        self.pairw = self.ctrl.pairwidget_list[self.blk_item.idx]
        self.ctrl.txtblkShapeControl.setBlkItem(self.blk_item)

    def redo(self):
        if self.op_count < 0:
            self.op_count += 1
            self.blk_item.setSelected(True)
            return
        self.ctrl.recoverTextblkItemList([self.blk_item], [self.pairw])

    def undo(self):
        self.ctrl.deleteTextblkItemList([self.blk_item], [self.pairw])


class EmptyCommand(QUndoCommand):
    def __init__(self, parent=None):
        super().__init__(parent=parent)


class DeleteBlkItemsCommand(QUndoCommand):
    def __init__(self, blk_list: List[TextBlkItem], mode: int, ctrl, parent=None):
        super().__init__(parent)
        self.op_counter = 0
        self.blk_list = []
        self.pwidget_list: List[TransPairWidget] = []
        self.ctrl: SceneTextManager = ctrl
        self.sw = self.ctrl.canvas.search_widget
        self.canvas: Canvas = ctrl.canvas
        self.mode = mode

        self.undo_img_list = []
        self.redo_img_list = []
        self.inpaint_rect_lst = []
        self.mask_pnts = []
        img_array = self.canvas.imgtrans_proj.inpainted_array
        mask_array = self.canvas.imgtrans_proj.mask_array
        original_array = self.canvas.imgtrans_proj.img_array

        self.search_rstedit_list: List[SourceTextEdit] = []
        self.search_counter_list = []
        self.highlighter_list = []
        self.old_counter_sum = self.sw.counter_sum
        self.sw_changed = False

        blk_list.sort(key=lambda blk: blk.idx)

        for blkitem in blk_list:
            if not isinstance(blkitem, TextBlkItem):
                continue
            self.blk_list.append(blkitem)
            pw: TransPairWidget = ctrl.pairwidget_list[blkitem.idx]
            self.pwidget_list.append(pw)

            if mode == 1:
                is_empty = False
                msk, xyxy = get_block_mask(
                    blkitem.absBoundingRect(), mask_array, blkitem.rotation()
                )
                if msk is None:
                    is_empty = True
                if is_empty:
                    self.undo_img_list.append(None)
                    self.redo_img_list.append(None)
                    self.inpaint_rect_lst.append(None)
                    self.mask_pnts.append(None)
                else:
                    x1, y1, x2, y2 = xyxy
                    self.mask_pnts.append(np.where(msk))
                    self.undo_img_list.append(np.copy(img_array[y1:y2, x1:x2]))
                    self.redo_img_list.append(np.copy(original_array[y1:y2, x1:x2]))
                    self.inpaint_rect_lst.append([x1, y1, x2, y2])

            rst_idx = self.sw.get_result_edit_index(pw.e_trans)
            if rst_idx != -1:
                self.sw_changed = True
                highlighter = self.sw.highlighter_list.pop(rst_idx)
                counter = self.sw.search_counter_list.pop(rst_idx)
                self.sw.counter_sum -= counter
                if self.sw.current_edit == pw.e_trans:
                    highlighter.set_current_span(-1, -1)
                self.search_rstedit_list.append(
                    self.sw.search_rstedit_list.pop(rst_idx)
                )
                self.search_counter_list.append(counter)
                self.highlighter_list.append(highlighter)

            rst_idx = self.sw.get_result_edit_index(pw.e_source)
            if rst_idx != -1:
                self.sw_changed = True
                highlighter = self.sw.highlighter_list.pop(rst_idx)
                counter = self.sw.search_counter_list.pop(rst_idx)
                self.sw.counter_sum -= counter
                if self.sw.current_edit == pw.e_trans:
                    highlighter.set_current_span(-1, -1)
                self.search_rstedit_list.append(
                    self.sw.search_rstedit_list.pop(rst_idx)
                )
                self.search_counter_list.append(counter)
                self.highlighter_list.append(highlighter)

        self.new_counter_sum = self.sw.counter_sum
        if self.sw_changed:
            if self.sw.counter_sum > 0:
                idx = self.sw.get_result_edit_index(self.sw.current_edit)
                if self.sw.current_cursor is not None and idx != -1:
                    self.sw.result_pos = self.sw.highlighter_list[idx].matched_map[
                        self.sw.current_cursor.position()
                    ]
                    if idx > 0:
                        self.sw.result_pos += sum(self.sw.search_counter_list[:idx])
                    self.sw.updateCounterText()
                else:
                    self.sw.setCurrentEditor(self.sw.search_rstedit_list[0])
            else:
                self.sw.setCurrentEditor(None)

        self.ctrl.deleteTextblkItemList(self.blk_list, self.pwidget_list)

    def redo(self):

        if self.mode == 1:
            self.canvas.saved_drawundo_step -= 1
            img_array = self.canvas.imgtrans_proj.inpainted_array
            mask_array = self.canvas.imgtrans_proj.mask_array
            for mskpnt, inpaint_rect, redo_img in zip(
                self.mask_pnts, self.inpaint_rect_lst, self.redo_img_list
            ):
                if mskpnt is None:
                    continue
                x1, y1, x2, y2 = inpaint_rect
                img_array[y1:y2, x1:x2][mskpnt] = redo_img[mskpnt]
                mask_array[y1:y2, x1:x2][mskpnt] = 0
            self.canvas.updateLayers()

        if self.op_counter == 0:
            self.op_counter += 1
            return

        self.ctrl.deleteTextblkItemList(self.blk_list, self.pwidget_list)
        if self.sw_changed:
            self.sw.counter_sum = self.new_counter_sum
            cursor_removed = False
            for edit in self.search_rstedit_list:
                idx = self.sw.get_result_edit_index(edit)
                if idx != -1:
                    self.sw.search_rstedit_list.pop(idx)
                    self.sw.search_counter_list.pop(idx)
                    self.sw.highlighter_list.pop(idx)
                if edit == self.sw.current_edit:
                    cursor_removed = True
            if cursor_removed:
                if self.sw.counter_sum > 0:
                    self.sw.setCurrentEditor(self.sw.search_rstedit_list[0])
                else:
                    self.sw.setCurrentEditor(None)

    def undo(self):

        if self.mode == 1:
            self.canvas.saved_drawundo_step += 1
            img_array = self.canvas.imgtrans_proj.inpainted_array
            mask_array = self.canvas.imgtrans_proj.mask_array
            for mskpnt, inpaint_rect, undo_img in zip(
                self.mask_pnts, self.inpaint_rect_lst, self.undo_img_list
            ):
                if mskpnt is None:
                    continue
                x1, y1, x2, y2 = inpaint_rect
                img_array[y1:y2, x1:x2][mskpnt] = undo_img[mskpnt]
                mask_array[y1:y2, x1:x2][mskpnt] = 255
            self.canvas.updateLayers()

        self.ctrl.recoverTextblkItemList(self.blk_list, self.pwidget_list)
        if self.sw_changed:
            self.sw.counter_sum = self.old_counter_sum
            self.sw.search_rstedit_list += self.search_rstedit_list
            self.sw.search_counter_list += self.search_counter_list
            self.sw.highlighter_list += self.highlighter_list
            self.sw.updateCounterText()


class PasteBlkItemsCommand(QUndoCommand):
    def __init__(
        self,
        blk_list: List[TextBlkItem],
        pwidget_list: List[TransPairWidget],
        ctrl,
        parent=None,
    ):
        super().__init__(parent)
        self.op_counter = 0
        self.blk_list = blk_list
        self.ctrl: SceneTextManager = ctrl
        blk_list.sort(key=lambda blk: blk.idx)

        self.ctrl.canvas.block_selection_signal = True
        for blkitem in blk_list:
            blkitem.setSelected(True)
        self.ctrl.on_incanvas_selection_changed()
        self.ctrl.canvas.block_selection_signal = False
        self.pwidget_list = pwidget_list

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        self.ctrl.recoverTextblkItemList(self.blk_list, self.pwidget_list)

    def undo(self):
        self.ctrl.deleteTextblkItemList(self.blk_list, self.pwidget_list)


class MergeTextBlksCommand(QUndoCommand):
    """合并多个文字块为一个（含撤消）。"""

    def __init__(
        self,
        survivor_blkitem,
        survivor_pairwidget,
        removed_blkitems,
        removed_pairwidgets,
        merged_blk_data,
        survivor_original_blk,
        survivor_original_xyxy,  # 合并瞬间宿主的实际位置 [x1, y1, x2, y2]
        ctrl,
        parent=None,
    ):
        super().__init__(parent)
        self.survivor_blkitem = survivor_blkitem
        self.survivor_pairwidget = survivor_pairwidget
        self.removed_blkitems = removed_blkitems
        self.removed_pairwidgets = removed_pairwidgets
        self.merged_blk_data = merged_blk_data
        self.survivor_original_blk = survivor_original_blk
        self.survivor_original_xyxy = survivor_original_xyxy
        self.ctrl: SceneTextManager = ctrl
        self.op_counter = 0

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            # redo() called inside push_undo_command — apply merge now
        # 1. 更新宿主数据
        self.survivor_blkitem.blk = self.merged_blk_data
        self.survivor_blkitem.setPlainText(self.merged_blk_data.translation)
        self.survivor_pairwidget.e_trans.setPlainText(
            self.merged_blk_data.translation
        )
        self.survivor_pairwidget.e_source.setPlainText(
            self.merged_blk_data.get_text()
        )
        # 1b. 扩张文本框到合并区域（setRect 同时更新 pos、_display_rect、layout maxSize）
        # padding=False：xyxy 已是完整外框，不加 document margin 避免偏移
        # 注意 setRect 接受 [x, y, w, h] 格式，而 merged_blk_data.xyxy 是 [x1, y1, x2, y2]
        xyxy = self.merged_blk_data.xyxy
        self.survivor_blkitem.setRect(
            [xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]],
            padding=False, repaint=True,
        )
        # 先选中宿主，让 delete 后的 selection 回调能正确同步
        self.survivor_blkitem.setSelected(True)
        # 2. 移除被合并块
        self.ctrl.deleteTextblkItemList(
            self.removed_blkitems, self.removed_pairwidgets
        )

    def undo(self):
        # 1. 恢复宿主原始数据
        self.survivor_blkitem.blk = self.survivor_original_blk
        self.survivor_blkitem.setPlainText(
            self.survivor_original_blk.translation
        )
        self.survivor_pairwidget.e_trans.setPlainText(
            self.survivor_original_blk.translation
        )
        self.survivor_pairwidget.e_source.setPlainText(
            self.survivor_original_blk.get_text()
        )
        # 1b. 用合并瞬间记录的实际位置恢复，而非可能过时的 blk.xyxy
        xyxy = self.survivor_original_xyxy
        self.survivor_blkitem.setRect(
            [xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]],
            padding=False, repaint=True,
        )
        # 2. 恢复被移除块
        self.ctrl.recoverTextblkItemList(
            self.removed_blkitems, self.removed_pairwidgets
        )


class PasteSrcItemsCommand(QUndoCommand):
    def __init__(self, src_list: List[SourceTextEdit], paste_list: List[str]):
        super().__init__()
        self.src_list = src_list
        self.paste_list = paste_list
        self.ori_text_list = [src.toPlainText() for src in src_list]

    def redo(self):
        for src, text in zip(self.src_list, self.paste_list):
            src.setPlainText(text)

    def undo(self):
        for src, text in zip(self.src_list, self.ori_text_list):
            src.setPlainText(text)


class RearrangeBlksCommand(QUndoCommand):
    def __init__(self, rmap: Tuple, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl: SceneTextManager = ctrl
        self.src_ids, self.tgt_ids = rmap[0], rmap[1]

        self.nr = len(self.src_ids)
        self.src2tgt = {}
        self.tgt2src = {}
        for s, t in zip(self.src_ids, self.tgt_ids):
            self.src2tgt[s] = t
            self.tgt2src[t] = s
        self.visible_ = None
        self.redo_visible_idx = self.undo_visible_idx = None
        if len(rmap) > 2:
            self.redo_visible_idx, self.undo_visible_idx = rmap[2]

    def redo(self):
        self.rearange_blk_ids(self.src_ids, self.tgt_ids, self.redo_visible_idx)

    def undo(self):
        self.rearange_blk_ids(self.tgt_ids, self.src_ids, self.undo_visible_idx)

    def rearange_blk_ids(self, src_ids, tgt_ids, visible_idx=None):
        src_ids = np.array(src_ids)
        tgt_ids = np.array(tgt_ids)
        src_order_ids = np.argsort(src_ids)[::-1]

        src_ids = src_ids[src_order_ids]
        tgt_ids = tgt_ids[src_order_ids]

        blks: List[TextBlkItem] = []
        pws: List[TransPairWidget] = []
        for pos, pos_tgt in zip(src_ids, tgt_ids):
            pw = self.ctrl.pairwidget_list.pop(pos)
            if visible_idx == pos_tgt:
                pw.hide()
            blk = self.ctrl.textblk_item_list.pop(pos)
            pws.append(pw)
            blks.append(blk)

        tgt_order_ids = np.argsort(tgt_ids)
        for ii in tgt_order_ids:
            pos = tgt_ids[ii]
            self.ctrl.textblk_item_list.insert(pos, blks[ii])

            self.ctrl.textEditList.insertPairWidget(pws[ii], pos)
            self.ctrl.pairwidget_list.insert(pos, pws[ii])

        self.ctrl.updateTextBlkItemIdx(set(tgt_ids))
        if visible_idx is not None:
            pw_ct = self.ctrl.pairwidget_list[visible_idx]
            pw_ct.show()
            self.ctrl.textEditList.ensureWidgetVisible(pw_ct, yMargin=pw.height())


class TextPanel(Widget):
    def __init__(self, app: QApplication, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        layout = QVBoxLayout(self)

        self.textEditList = TextEditListScrollArea(self)
        self.formatpanel = FontFormatPanel(app, self)

        # 上部：格式编辑区（GroupFrame）+ 左缘窄栏（功能图标，展开的
        # 浮层面板锚定在窄栏左侧画布区，见 ui/panel_rail.py / rail_dock_panel.py）
        format_row = QWidget(self)
        format_row_layout = QHBoxLayout(format_row)
        format_row_layout.setContentsMargins(0, 0, 0, 0)
        # No gap between the 28px rail tube and the format frame: the rail was
        # widened for the icon enlargement and the fixed-width right panel was
        # clipping the text-style area.  Reclaiming this sliver lets the format
        # area expand leftward again.
        format_row_layout.setSpacing(0)
        self.rail = PanelRail(format_row)
        format_row_layout.addWidget(self.rail)
        self.format_frame = GroupFrame(format_row)
        self.format_frame.setObjectName("formatOuterFrame")
        format_layout = QVBoxLayout(self.format_frame)
        format_layout.setContentsMargins(5, 5, 5, 5)
        format_layout.addWidget(self.formatpanel)
        format_row_layout.addWidget(self.format_frame, 1)
        layout.addWidget(format_row)

        # 下部：文本编辑区（工具栏 + 文本框列表同框）；行编号/选中锚色/
        # 拖拽在行卡片内（textedit_area）
        self.sourceBtn = TextCheckerLabel(self.tr("Source"))
        self.transBtn = TextCheckerLabel(self.tr("Translation"))
        self.textToolBar = QHBoxLayout()
        self.textToolBar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.textToolBar.addWidget(self.sourceBtn)
        self.textToolBar.addWidget(self.transBtn)
        self.textToolBar.setStretch(0, 1)
        self.textToolBar.setStretch(1, 1)
        self.textToolBar.setContentsMargins(0, 0, 0, 0)
        self.textToolBar.setSpacing(0)

        text_frame = GroupFrame(self)
        text_frame.setObjectName("textEditOuterFrame")
        text_layout = QVBoxLayout(text_frame)
        text_layout.setContentsMargins(5, 5, 5, 5)
        text_layout.addLayout(self.textToolBar)
        text_layout.addWidget(self.textEditList)
        layout.addWidget(text_frame, 1)

        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.formatpanel.install_annotation_launcher(self.rail)
        self.formatpanel.install_emphasis_launcher(self.rail)
        self.formatpanel.install_transform_launcher(self.rail)
        self.formatpanel.install_textstyle_launcher(self.rail)

    def showEvent(self, event) -> None:
        self.formatpanel.on_textpanel_visibility(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.formatpanel.on_textpanel_visibility(False)
        super().hideEvent(event)


class SceneTextManager(QObject):
    new_textblk = Signal(int)

    def __init__(
        self,
        app: QApplication,
        mainwindow: QWidget,
        canvas: Canvas,
        textpanel: TextPanel,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.app = app
        self.mainwindow = mainwindow
        self.canvas = canvas
        canvas.switch_text_item.connect(self.on_switch_textitem)
        self.canvas.scalefactor_changed.connect(self.adjustSceneTextRect)
        self.canvas.end_create_textblock.connect(self.onEndCreateTextBlock)
        self.canvas.paste2selected_textitems.connect(self.on_paste2selected_textitems)
        self.canvas.delete_textblks.connect(self.onDeleteBlkItems)
        self.canvas.copy_textblks.connect(self.onCopyBlkItems)
        self.canvas.paste_textblks.connect(self.onPasteBlkItems)
        self.canvas.format_textblks.connect(self.onFormatTextblks)
        self.canvas.reset_angle.connect(self.onResetAngle)
        self.canvas.squeeze_blk.connect(self.onSqueezeBlk)
        self.canvas.align_textblks.connect(self.onAlignTextBlks)
        self.canvas.merge_textblks.connect(self.on_merge_textblks)
        self.canvas.incanvas_selection_changed.connect(
            self.on_incanvas_selection_changed
        )
        self.txtblkShapeControl = canvas.txtblkShapeControl
        self.textpanel = textpanel
        self.textEditList = textpanel.textEditList
        self.textEditList.focus_out.connect(self.on_textedit_list_focusout)
        self.textEditList.textpanel_contextmenu_requested.connect(
            self.canvas.on_create_contextmenu
        )
        self.textEditList.selection_changed.connect(
            self.on_transwidget_selection_changed
        )
        self.textEditList.rearrange_blks.connect(self.on_rearrange_blks)
        self.canvas.reorder_textblks.connect(self.textEditList.move_selected)

        self.formatpanel = textpanel.formatpanel
        self.formatpanel.textstyle_panel.apply_fontfmt.connect(self.onFormatTextblks)

        self.imgtrans_proj = self.canvas.imgtrans_proj
        self.textblk_item_list: List[TextBlkItem] = []
        self.pairwidget_list: List[TransPairWidget] = self.textEditList.pairwidget_list

        self.hovering_transwidget: TransTextEdit = None
        # 行悬停→画布块描边闪烁的当前持有项（见 _flash_row_item）
        self._row_flash_item: TextBlkItem = None

        self.prev_blkitem: TextBlkItem = None

        # Debounced pixmap cache rebuild after view zoom
        self._rebuild_timer = QTimer()
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_item_caches)

    def on_switch_textitem(
        self,
        switch_delta: int,
        key_event: QKeyEvent = None,
        current_editing_widget: Union[SourceTextEdit, TransTextEdit] = None,
    ):
        n_blk = len(self.textblk_item_list)
        if n_blk < 1:
            return

        editing_blk = None
        if current_editing_widget is None:
            editing_blk = self.editingTextItem()
            if editing_blk is not None:
                tgt_idx = editing_blk.idx + switch_delta
            else:
                sel_blks = self.canvas.selected_text_items(sort=False)
                if len(sel_blks) == 0:
                    return
                sel_blk = sel_blks[0]
                tgt_idx = sel_blk.idx + switch_delta
        else:
            tgt_idx = current_editing_widget.idx + switch_delta

        if tgt_idx < 0:
            tgt_idx += n_blk
        elif tgt_idx >= n_blk:
            tgt_idx -= n_blk
        blk = self.textblk_item_list[tgt_idx]

        if current_editing_widget is None:
            if editing_blk is None:
                self.canvas.block_selection_signal = True
                self.canvas.clearSelection()
                blk.setSelected(True)
                self.canvas.block_selection_signal = False
                self.canvas.gv.ensureVisible(blk)
                self.txtblkShapeControl.setBlkItem(blk)
                edit = self.pairwidget_list[tgt_idx].e_trans
                self.changeHoveringWidget(edit)
                self.textEditList.set_selected_list([blk.idx])
            else:
                editing_blk.endEdit()
                editing_blk.setSelected(False)
                self.txtblkShapeControl.setBlkItem(blk)
                blk.setSelected(True)
                blk.startEdit()
                self.canvas.gv.ensureVisible(blk)
        else:
            self.textblk_item_list[current_editing_widget.idx].setSelected(False)
            current_pw = self.pairwidget_list[tgt_idx]
            is_trans = isinstance(current_editing_widget, TransTextEdit)
            if is_trans:
                w = current_pw.e_trans
            else:
                w = current_pw.e_source

            self.changeHoveringWidget(w)
            w.setFocus()

        if key_event is not None:
            key_event.accept()

    def setTextEditMode(self, edit: bool = False):
        if edit:
            self.textpanel.show()
            self.canvas.textLayer.show()
        else:
            self.txtblkShapeControl.setBlkItem(None)
            self.textpanel.hide()
            self.textpanel.formatpanel.set_textblk_item()
            self.canvas.textLayer.hide()

    def adjustSceneTextRect(self):
        self.txtblkShapeControl.updateBoundingRect()
        # Debounced pixmap cache rebuild after zoom — avoids repeated
        # rebuilds during pinch/scroll zoom gestures.
        self._rebuild_timer.start(100)

    def _rebuild_item_caches(self):
        """Rebuild item rendering caches after zoom."""
        for blk_item in self.textblk_item_list:
            # Switch DeviceCoordinateCache <-> NoCache across the high-zoom
            # limit, then force a repaint at the new cache policy.
            blk_item.refresh_cache_policy()
            blk_item.update()

    def clearSceneTextitems(self):
        self.hovering_transwidget = None
        self.txtblkShapeControl.setBlkItem(None)
        self.canvas.clear_text_transform_controls()
        for blkitem in self.textblk_item_list:
            blkitem.release_render_resources()
            self.canvas.removeItem(blkitem)
        self.textblk_item_list.clear()
        self.textEditList.clearAllSelected()
        for textwidget in self.pairwidget_list:
            self.textEditList.removeWidget(textwidget)
        self.pairwidget_list.clear()

    def updateSceneTextitems(self):
        self.hovering_transwidget = None
        self.txtblkShapeControl.setBlkItem(None)
        self.clearSceneTextitems()
        for textblock in self.imgtrans_proj.current_block_list():
            if textblock.font_family is None or textblock.font_family.strip() == "":
                textblock.font_family = self.formatpanel.familybox.currentText()
            self.addTextBlock(textblock)

    def addTextBlock(self, blk: Union[TextBlock, TextBlkItem] = None) -> TextBlkItem:
        if isinstance(blk, TextBlkItem):
            blk_item = blk
            blk_item.idx = len(self.textblk_item_list)
            blk_item.refresh_seq_badge()
        else:
            blk_item = TextBlkItem(
                blk, len(self.textblk_item_list), show_rect=self.canvas.textblock_mode
            )
        self.addTextBlkItem(blk_item)

        pair_widget = TransPairWidget(blk, len(self.pairwidget_list))
        self.pairwidget_list.append(pair_widget)
        self.textEditList.addPairWidget(pair_widget)
        pair_widget.e_source.setPlainText(blk_item.blk.get_text())
        pair_widget.e_source.focus_in.connect(self.on_transwidget_focus_in)
        pair_widget.e_source.ensure_scene_visible.connect(
            self.on_ensure_textitem_svisible
        )
        pair_widget.e_source.push_undo_stack.connect(self.on_push_edit_stack)
        pair_widget.e_source.redo_signal.connect(self.on_textedit_redo)
        pair_widget.e_source.undo_signal.connect(self.on_textedit_undo)
        pair_widget.e_source.focus_out.connect(self.on_pairw_focusout)

        pair_widget.e_trans.setPlainText(blk_item.toPlainText())
        pair_widget.e_trans.focus_in.connect(self.on_transwidget_focus_in)
        pair_widget.e_trans.propagate_user_edited.connect(
            self.on_propagate_transwidget_edit
        )
        pair_widget.e_trans.ensure_scene_visible.connect(
            self.on_ensure_textitem_svisible
        )
        pair_widget.e_trans.push_undo_stack.connect(self.on_push_edit_stack)
        pair_widget.e_trans.redo_signal.connect(self.on_textedit_redo)
        pair_widget.e_trans.undo_signal.connect(self.on_textedit_undo)
        pair_widget.e_trans.focus_out.connect(self.on_pairw_focusout)
        # 行悬停双向联动（行→画布）：原文/译文框与行号槽共用同一闪烁路径
        pair_widget.e_source.hover_enter.connect(self.on_row_hover)
        pair_widget.e_source.hover_leave.connect(self.on_row_leave)
        pair_widget.e_trans.hover_enter.connect(self.on_row_hover)
        pair_widget.e_trans.hover_leave.connect(self.on_row_leave)
        pair_widget.drag_move.connect(self.textEditList.handle_drag_pos)
        pair_widget.pw_drop.connect(self.textEditList.on_pw_dropped)

        self.new_textblk.emit(blk_item.idx)
        return blk_item

    def addTextBlkItem(self, textblk_item: TextBlkItem) -> TextBlkItem:
        self.textblk_item_list.append(textblk_item)
        textblk_item.setParentItem(self.canvas.textLayer)
        textblk_item.begin_edit.connect(self.onTextBlkItemBeginEdit)
        textblk_item.end_edit.connect(self.onTextBlkItemEndEdit)
        textblk_item.hover_enter.connect(self.onTextBlkItemHoverEnter)
        textblk_item.leftbutton_pressed.connect(self.onLeftbuttonPressed)
        textblk_item.moving.connect(self.onTextBlkItemMoving)
        textblk_item.moved.connect(self.onTextBlkItemMoved)
        textblk_item.reshaped.connect(self.onTextBlkItemReshaped)
        textblk_item.rotated.connect(self.onTextBlkItemRotated)
        textblk_item.push_undo_stack.connect(self.on_push_textitem_undostack)
        textblk_item.undo_signal.connect(self.on_textedit_undo)
        textblk_item.redo_signal.connect(self.on_textedit_redo)
        textblk_item.propagate_user_edited.connect(self.on_propagate_textitem_edit)
        textblk_item.doc_size_changed.connect(self.onTextBlkItemSizeChanged)
        textblk_item.pasted.connect(self.onBlkitemPaste)
        return textblk_item

    def deleteTextblkItemList(
        self, blkitem_list: List[TextBlkItem], p_widget_list: List[TransPairWidget]
    ):
        selection_changed = False
        for blkitem, p_widget in zip(blkitem_list, p_widget_list):
            if blkitem.isSelected():
                selection_changed = True
            self.canvas.removeItem(
                blkitem
            )  # removeItem itself will block incanvas_selection_changed
            self.textblk_item_list.remove(blkitem)
            self.pairwidget_list.remove(p_widget)
            self.textEditList.removeWidget(p_widget)
        self.updateTextBlkItemIdx()
        self.txtblkShapeControl.setBlkItem(None)
        if selection_changed:
            # it must be called after updateTextBlkItemIdx if blk.idx changed
            self.on_incanvas_selection_changed()

    def recoverTextblkItemList(
        self, blkitem_list: List[TextBlkItem], p_widget_list: List[TransPairWidget]
    ):
        self.canvas.block_selection_signal = True
        for blkitem, p_widget in zip(blkitem_list, p_widget_list):
            self.textblk_item_list.insert(blkitem.idx, blkitem)
            blkitem.setParentItem(self.canvas.textLayer)
            self.pairwidget_list.insert(p_widget.idx, p_widget)
            self.textEditList.insertPairWidget(p_widget, p_widget.idx)
            if self.txtblkShapeControl.blk_item is not None and blkitem.isSelected():
                blkitem.setSelected(False)
        self.updateTextBlkItemIdx()
        self.on_incanvas_selection_changed()
        self.canvas.block_selection_signal = False

    def onTextBlkItemSizeChanged(self, idx: int):
        if idx >= len(self.textblk_item_list):
            return
        blk_item = self.textblk_item_list[idx]
        if not self.txtblkShapeControl.reshaping:
            if self.txtblkShapeControl.blk_item == blk_item:
                self.txtblkShapeControl.updateBoundingRect()

    @property
    def app_clipborad(self) -> QClipboard:
        return self.app.clipboard()

    def onBlkitemPaste(self, idx: int):
        blk_item = self.textblk_item_list[idx]
        text = self.app_clipborad.text()
        cursor = blk_item.textCursor()
        cursor.insertText(text)

    def onTextBlkItemBeginEdit(self, blk_id: int):
        blk_item = self.textblk_item_list[blk_id]
        self.txtblkShapeControl.setBlkItem(blk_item)
        self.canvas.editing_textblkitem = blk_item
        self.formatpanel.set_textblk_item(blk_item)
        self.txtblkShapeControl.startEditing()
        e_trans = self.pairwidget_list[blk_item.idx].e_trans
        self.changeHoveringWidget(e_trans)

    def changeHoveringWidget(self, edit: SourceTextEdit):
        if self.hovering_transwidget is not None and self.hovering_transwidget != edit:
            self.hovering_transwidget.setHoverEffect(False)
        self.hovering_transwidget = edit
        if edit is not None:
            pw = self.pairwidget_list[edit.idx]
            h = pw.height()
            if shared.USE_PYSIDE6:
                self.textEditList.ensureWidgetVisible(pw, ymargin=h)
            else:
                self.textEditList.ensureWidgetVisible(pw, yMargin=h)
            edit.setHoverEffect(True)

    def onLeftbuttonPressed(self, blk_id: int):
        blk_item = self.textblk_item_list[blk_id]
        self.txtblkShapeControl.setBlkItem(blk_item)
        selections: List[TextBlkItem] = self.canvas.selectedItems()
        if len(selections) > 1:
            for item in selections:
                item.oldPos = item.pos()
        self.changeHoveringWidget(self.pairwidget_list[blk_id].e_trans)

    def onTextBlkItemEndEdit(self, blk_id: int):
        self.canvas.editing_textblkitem = None
        self.textblk_item_list[blk_id].setSelected(True)
        self.txtblkShapeControl.endEditing()
        # 退出内联编辑 = 编辑会话边界：落账未闭合的键入会话/格式化手势。
        self.canvas.commit_edit_sessions()

    def on_row_hover(self, idx: int):
        if self.is_editting():
            return
        self._flash_row_item(idx)

    def on_row_leave(self, idx: int):
        self._flash_row_item(-1)

    def _flash_row_item(self, idx: int):
        """行悬停时在画布上临时点亮对应块的描边框（draw_rect 闪烁）。

        只接管原本未显示描边的块；常显描边（textblock_mode）的块不
        归我们回收，避免与全局显示选项打架。
        """
        old = self._row_flash_item
        if old is not None:
            self._row_flash_item = None
            try:
                if old.scene() is not None:
                    old.draw_rect = False
                    old.update()
            except RuntimeError:
                pass
        if idx < 0 or idx >= len(self.textblk_item_list):
            return
        item = self.textblk_item_list[idx]
        try:
            if item.draw_rect:
                return
            item.draw_rect = True
            item.update()
        except RuntimeError:
            return
        self._row_flash_item = item

    def editingTextItem(self) -> TextBlkItem:
        if (
            self.txtblkShapeControl.isVisible()
            and self.canvas.editing_textblkitem is not None
        ):
            return self.canvas.editing_textblkitem
        return None

    def savePrevBlkItem(self, blkitem: TextBlkItem):
        self.prev_blkitem = blkitem
        self.prev_textCursor = QTextCursor(self.prev_blkitem.textCursor())

    def is_editting(self):
        blk_item = self.txtblkShapeControl.blk_item
        return blk_item is not None and blk_item.is_editting()

    def onTextBlkItemHoverEnter(self, blk_id: int):
        if self.is_editting():
            return
        blk_item = self.textblk_item_list[blk_id]
        if not blk_item.hasFocus():
            self.txtblkShapeControl.setBlkItem(blk_item)

    def onTextBlkItemMoving(self, item: TextBlkItem):
        self.txtblkShapeControl.updateBoundingRect()

    def onTextBlkItemMoved(self):
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) > 0:
            self.canvas.push_undo_command(
                MoveBlkItemsCommand(selected_blks, self.txtblkShapeControl)
            )

    def onTextBlkItemReshaped(self, item: TextBlkItem):
        self.canvas.push_undo_command(ReshapeItemCommand(item))

    def onTextBlkItemRotated(self, new_angle: float):
        blk_item = self.txtblkShapeControl.blk_item
        if blk_item:
            self.canvas.push_undo_command(
                RotateItemCommand(blk_item, new_angle, self.txtblkShapeControl)
            )

    def onDeleteBlkItems(self, mode: int):
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) == 0 and self.txtblkShapeControl.blk_item is not None:
            selected_blks.append(self.txtblkShapeControl.blk_item)
        if len(selected_blks) > 0:
            self.canvas.push_undo_command(
                DeleteBlkItemsCommand(selected_blks, mode, self)
            )

    def onCopyBlkItems(self):
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) == 0 and self.txtblkShapeControl.blk_item is not None:
            selected_blks.append(self.txtblkShapeControl.blk_item)

        if len(selected_blks) == 0:
            return

        self.canvas.clipboard_blks.clear()
        if self.canvas.text_change_unsaved():
            self.updateTextBlkList()

        pos = selected_blks[0].blk.bounding_rect()
        pos_x = int(pos[0] + pos[2] / 2)
        pos_y = int(pos[1] + pos[3] / 2)

        textlist = []
        for blkitem in selected_blks:
            blk = copy.deepcopy(blkitem.blk)
            blk.adjust_pos(-pos_x, -pos_y)
            self.canvas.clipboard_blks.append(blk)
            textlist.append(blkitem.toPlainText().strip())
        textlist = "\n".join(textlist)
        self.app_clipborad.setText(textlist, QClipboard.Mode.Clipboard)

    def onPasteBlkItems(self, pos: QPointF):
        if pos is None:
            pos_x, pos_y = 0, 0
        else:
            pos_x, pos_y = pos.x(), pos.y()
            pos_x = int(pos_x / self.canvas.scale_factor)
            pos_y = int(pos_y / self.canvas.scale_factor)
        blkitem_list, pair_widget_list = [], []
        for blk in self.canvas.clipboard_blks:
            blk = copy.deepcopy(blk)
            blk.adjust_pos(pos_x, pos_y)
            blkitem = self.addTextBlock(blk)
            pairw = self.pairwidget_list[-1]
            blkitem_list.append(blkitem)
            pair_widget_list.append(pairw)
        if len(blkitem_list) > 0:
            self.canvas.clearSelection()
            self.canvas.push_undo_command(
                PasteBlkItemsCommand(blkitem_list, pair_widget_list, self)
            )
            if len(blkitem_list) == 1:
                self.formatpanel.set_textblk_item(blkitem_list[0])
            else:
                self.formatpanel.set_textblk_item(multi_select=True)

    def onFormatTextblks(self, fmt: FontFormat = None):
        if fmt is None:
            fmt = self.formatpanel.global_format
        self.apply_fontformat(fmt)

    def onResetAngle(self):
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) > 0:
            self.canvas.push_undo_command(
                ResetAngleCommand(selected_blks, self.txtblkShapeControl)
            )

    def onSqueezeBlk(self):
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) > 0:
            self.canvas.push_undo_command(
                SqueezeCommand(selected_blks, self.txtblkShapeControl)
            )

    def onAlignTextBlks(self, operation: str):
        """Handle batch alignment operations from context menu."""
        selected_blks = self.canvas.selected_text_items()
        if len(selected_blks) < 2:
            return

        op_map = {
            "left": align_left,
            "right": align_right,
            "top": align_top,
            "bottom": align_bottom,
            "hcenter": align_horizontal_center,
            "vcenter": align_vertical_center,
            "dist_h": distribute_horizontal,
            "dist_v": distribute_vertical,
        }
        fn = op_map.get(operation)
        if fn is None:
            return

        new_positions = fn(selected_blks)
        if not new_positions:
            return

        moved = []
        for item, new_pos in new_positions.items():
            item.oldPos = item.pos()
            item.setPos(new_pos)
            moved.append(item)

        if moved:
            self.canvas.push_undo_command(
                MoveBlkItemsCommand(moved, self.txtblkShapeControl)
            )

    def on_incanvas_selection_changed(self):
        if self.canvas.textEditMode():
            textitems = self.canvas.selected_text_items()
            self.textEditList.set_selected_list([t.idx for t in textitems])
            if len(textitems) == 1:
                self.formatpanel.set_textblk_item(textitems[-1])
            else:
                self.formatpanel.set_textblk_item(multi_select=bool(textitems))



    def restore_charfmts(
        self,
        blkitem: TextBlkItem,
        text: str,
        new_text: str,
        char_fmts: List[QTextCharFormat],
    ):
        cursor = blkitem.textCursor()
        cpos = 0
        num_text = len(new_text)
        num_fmt = len(char_fmts)
        blkitem.layout.relayout_on_changed = False
        blkitem.repaint_on_changed = False
        if num_text >= num_fmt:
            for fmt_i in range(num_fmt):
                fmt = char_fmts[fmt_i]
                ori_char = text[fmt_i].strip()
                if ori_char == "":
                    continue
                else:
                    if cursor.atEnd():
                        break
                    matched = False
                    while cpos < num_text:
                        if new_text[cpos] == ori_char:
                            matched = True
                            break
                        cpos += 1
                    if matched:
                        cursor.clearSelection()
                        cursor.setPosition(cpos)
                        cursor.setPosition(cpos + 1, QTextCursor.MoveMode.KeepAnchor)
                        cursor.setCharFormat(fmt)
                        cursor.setBlockCharFormat(fmt)
                        cpos += 1
        blkitem.repaint_on_changed = True
        blkitem.layout.relayout_on_changed = True
        blkitem.layout.reLayout()
        blkitem.repaint_background()

    def onEndCreateTextBlock(self, rect: QRectF):
        xyxy = np.array([rect.x(), rect.y(), rect.right(), rect.bottom()])
        xyxy = np.round(xyxy).astype(np.int32)
        block = TextBlock(xyxy)
        xywh = np.copy(xyxy)
        xywh[[2, 3]] -= xywh[[0, 1]]
        block.set_lines_by_xywh(xywh)
        block.src_is_vertical = self.formatpanel.global_format.vertical
        blk_item = TextBlkItem(
            block, len(self.textblk_item_list), set_format=False, show_rect=True
        )
        blk_item.set_fontformat(self.formatpanel.global_format)
        self.canvas.push_undo_command(CreateItemCommand(blk_item, self))

    def on_paste2selected_textitems(self):
        blkitems = self.canvas.selected_text_items()
        text = self.app_clipborad.text()

        num_blk = len(blkitems)
        if num_blk < 1:
            return

        if num_blk > 1:
            text_list = text.rstrip().split("\n")
            num_text = len(text_list)
            if num_text > 1:
                if num_text > num_blk:
                    text_list = text_list[:num_blk]
                elif num_text < num_blk:
                    text_list = text_list + [text_list[-1]] * (num_blk - num_text)
                text = text_list

        etrans = [self.pairwidget_list[blkitem.idx].e_trans for blkitem in blkitems]
        self.canvas.push_undo_command(MultiPasteCommand(text, blkitems, etrans))

    def onRotateTextBlkItem(self, item: TextBlock):
        self.canvas.push_undo_command(RotateItemCommand(item))

    def on_transwidget_focus_in(self, idx: int):
        if self.is_editting():
            textitm = self.editingTextItem()
            textitm.endEdit()
            self.pairwidget_list[textitm.idx].e_trans.setHoverEffect(False)
            self.textEditList.clearAllSelected()

        # 原文编辑器获得焦点：开启原文键入会话（before = 当前全文）。
        # 原文无镜像可抓 before，只能靠 focus_in 预捕。
        edit = self.sender()
        if type(edit) is SourceTextEdit:
            self.canvas.note_source_focus_in(edit)

        if idx < len(self.textblk_item_list):
            blk_item = self.textblk_item_list[idx]
            self.canvas.gv.ensureVisible(blk_item)
            self.txtblkShapeControl.setBlkItem(blk_item)

    def on_textedit_redo(self):
        self.canvas.redo_textedit()

    def on_pairw_focusout(self, idx: int):
        # Cache restore is handled by TextBlkItem.endEdit → refresh_cache_policy;
        # the former text_rendering config branch was removed with it.
        # 失焦 = 编辑会话边界：键入会话/格式化手势各落一条快照命令。
        self.canvas.commit_edit_sessions()

    def on_textedit_undo(self):
        self.canvas.undo_textedit()

    def on_push_textitem_undostack(self, num_steps: int, is_formatting: bool):
        # 3a 快照命令制：键入已由 propagate 登记（on_propagate_textitem_edit
        # → canvas.note_typing_edit），此处只接管格式化变更——并入 canvas
        # 的格式化手势，手势闭合时落一条 FormatGestureCommand。
        if is_formatting:
            blkitem: TextBlkItem = self.sender()
            self.canvas.note_formatting_edit(blkitem, self.textpanel.formatpanel)

    def on_merge_textblks(self):
        """画布右键"Merge"：按列表 idx 顺序合并选中的文字块为一个。"""
        blkitems = self.canvas.selected_text_items()
        if len(blkitems) < 2:
            return

        # 按列表 idx 排序（尊重用户排好的阅读顺序）
        blkitems.sort(key=lambda b: b.idx)

        # 先将 UI 中的文字同步到 blk 数据（画布输入的文字存在 QTextDocument 中，
        # 尚未写回 blk.translation / blk.text）
        for b in blkitems:
            blk = b.blk
            if not b.document().isEmpty():
                blk.translation = b.toPlainText()
            else:
                blk.translation = ""
            pw = self.pairwidget_list[b.idx]
            blk.text = [pw.e_source.toPlainText()]

        survivor = blkitems[0]
        removed = blkitems[1:]

        # 记录宿主合并瞬间的实际位置（用于撤回时精确恢复）
        abr = survivor.absBoundingRect()
        survivor_original_xyxy = [abr[0], abr[1], abr[0] + abr[2], abr[1] + abr[3]]

        # 深拷贝宿主原始数据（用于撤消）
        survivor_original_blk = copy.deepcopy(survivor.blk)

        # 深拷贝被移除块数据（用于恢复 UI）
        removed_blkitems = []
        removed_pairwidgets = []
        for blkitem in removed:
            removed_blkitems.append(blkitem)
            removed_pairwidgets.append(self.pairwidget_list[blkitem.idx])

        # 构造合并后数据
        merged_blk = self._build_merged_blk(blkitems)

        self.canvas.push_undo_command(
            MergeTextBlksCommand(
                survivor, self.pairwidget_list[survivor.idx],
                removed_blkitems, removed_pairwidgets,
                merged_blk, survivor_original_blk,
                survivor_original_xyxy, self
            )
        )

    def _build_merged_blk(self, blkitems: list):
        """按列表顺序合并多个 TextBlkItem 的 TextBlock 数据。"""
        merged = copy.deepcopy(blkitems[0].blk)
        texts = []
        translations = []
        rich_texts = []
        all_lines = []
        x1s, y1s, x2s, y2s = [], [], [], []

        for b in blkitems:
            blk = b.blk
            texts.append(blk.get_text())
            trans = blk.translation if isinstance(blk.translation, str) else ""
            translations.append(trans)
            rich_texts.append(blk.rich_text if isinstance(blk.rich_text, str) else "")
            for line in (blk.lines if blk.lines else []):
                all_lines.append(line)
            # 使用 item 当前实际位置而非 blk.xyxy（可能因拖动而不同）
            abr = b.absBoundingRect()
            bx1, by1 = abr[0], abr[1]
            bx2, by2 = bx1 + abr[2], by1 + abr[3]
            x1s.append(bx1)
            y1s.append(by1)
            x2s.append(bx2)
            y2s.append(by2)

        merged.text = texts
        # 只保留非空译文，空块不产生换行
        non_empty_trans = [t.strip() for t in translations if t.strip()]
        merged.translation = "\n".join(non_empty_trans)
        merged.rich_text = "<br>".join(rich_texts)
        merged.xyxy = [min(x1s), min(y1s), max(x2s), max(y2s)]
        merged.lines = all_lines
        merged.region_mask = None
        merged.region_inpaint_dict = None
        merged.merged = True
        return merged

    def on_push_edit_stack(self, num_steps: int):
        # 3a 快照命令制：译文侧键入已由 propagate 登记
        # （on_propagate_transwidget_edit → canvas.note_typing_edit），此处只
        # 接管原文面板——原文无镜像可抓 before，靠 push 信号登记进 focus_in
        # 开启的键入会话。
        edit: Union[TransTextEdit, SourceTextEdit] = self.sender()
        if type(edit) is SourceTextEdit:
            self.canvas.note_source_edit(
                edit, edit.change_from, edit.change_removed, edit.change_added
            )

    def on_propagate_textitem_edit(
        self, pos: int, removed: int, added_text: str, joint_previous: bool
    ):
        blk_item: TextBlkItem = self.sender()
        edit = self.pairwidget_list[blk_item.idx].e_trans
        # 内联编辑回写面板：全文对账。in_acts 挡掉镜像写回触发的再次同步。
        # before 快照在同步前抓：镜像侧（e_trans）此时尚持旧文。
        if not edit.in_acts:
            before_text = edit.toPlainText()
            edit.in_acts = True
            try:
                changed = sync_text_by_diff(
                    edit, blk_item.toPlainText(), joint_previous
                )
            finally:
                edit.in_acts = False
            if changed:
                self.canvas.note_typing_edit(
                    blk_item, edit, before_text, pos, removed, len(added_text)
                )

    def on_propagate_transwidget_edit(self, joint_previous: bool):
        edit: TransTextEdit = self.sender()
        blk_item = self.textblk_item_list[edit.idx]
        if blk_item.isEditing():
            blk_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # before 快照在镜像同步前抓：item 侧此时尚持旧文。
        before_text = blk_item.toPlainText()
        if sync_text_by_diff(
            blk_item, edit.toPlainText(), joint_previous
        ):
            self.canvas.note_typing_edit(
                blk_item, edit, before_text,
                edit.change_from, edit.change_removed, edit.change_added,
            )

    def apply_fontformat(self, fontformat: FontFormat):
        selected_blks = self.canvas.selected_text_items()
        trans_widget_list = []
        for blk in selected_blks:
            trans_widget_list.append(self.pairwidget_list[blk.idx].e_trans)
        if len(selected_blks) > 0:
            self.canvas.push_undo_command(
                ApplyFontformatCommand(selected_blks, trans_widget_list, fontformat)
            )
            if self.formatpanel.global_mode():
                if id(self.formatpanel.active_text_style_format()) != id(fontformat):
                    self.formatpanel.deactivate_style_label()
                self.formatpanel.on_active_textstyle_label_changed()
            else:
                self.formatpanel.set_active_format(fontformat)

    def on_transwidget_selection_changed(self):
        selitems = self.canvas.selected_text_items()
        selset = {pw.idx: pw for pw in self.textEditList.checked_list}
        self.canvas.block_selection_signal = True
        for blkitem in selitems:
            if blkitem.idx not in selset:
                blkitem.setSelected(False)
            else:
                selset.pop(blkitem.idx)
        for idx in selset:
            self.textblk_item_list[idx].setSelected(True)
        self.canvas.block_selection_signal = False

    def on_textedit_list_focusout(self):
        fw = self.app.focusWidget()
        focusing_edit = isinstance(fw, (SourceTextEdit, TransTextEdit))
        if fw == self.canvas.gv or focusing_edit:
            self.textEditList.clearDrag()
        if focusing_edit:
            self.textEditList.clearAllSelected()

    def on_rearrange_blks(self, mv_map: Tuple[np.ndarray]):
        edit_list = self.textEditList

        # Capture pre-reorder state: position + visual snapshot for every widget
        pre_state = []
        for w in edit_list.pairwidget_list:
            pre_state.append((w, w.pos(), w.size(), w.grab()))

        # Execute reorder (sync — calls RearraneBlksCommand.redo() immediately)
        self.canvas.push_undo_command(RearrangeBlksCommand(mv_map, self))

        # Find widgets that actually moved and build ghost overlay slides
        scroll_content = edit_list.scrollContent
        anim_group = QParallelAnimationGroup()
        ghosts = []

        for w, old_pos, old_size, snapshot in pre_state:
            new_pos = w.pos()
            if old_pos == new_pos:
                continue
            ghost = QLabel(scroll_content)
            ghost.setPixmap(snapshot)
            ghost.setFixedSize(old_size)
            ghost.move(old_pos)
            ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            ghost.show()
            ghosts.append(ghost)

            anim = QPropertyAnimation(ghost, b"pos")
            anim.setDuration(150)
            anim.setStartValue(old_pos)
            anim.setEndValue(new_pos)
            anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
            anim_group.addAnimation(anim)

        if not ghosts:
            return

        def cleanup():
            for g in ghosts:
                g.deleteLater()

        anim_group.finished.connect(cleanup)
        anim_group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def updateTextBlkItemIdx(self, sel_ids: set = None):
        for ii, blk_item in enumerate(self.textblk_item_list):
            if sel_ids is not None and ii not in sel_ids:
                continue
            blk_item.idx = ii
            blk_item.refresh_seq_badge()
            self.pairwidget_list[ii].updateIndex(ii)
        cl = self.textEditList.checked_list
        if len(cl) != 0:
            cl.sort(key=lambda x: x.idx)

    def updateTextBlkList(self):
        cbl = self.imgtrans_proj.current_block_list()
        if cbl is None:
            return
        cbl.clear()
        for blk_item, trans_pair in zip(self.textblk_item_list, self.pairwidget_list):
            # 右侧译文框是纯文本的权威视图：常规编辑已由 diff 对账实时写回
            # item，这里兜底任何漏网路径（如同步信号建立前的初始文本），
            # 确保 JSON 与成图都以用户实际输入为准。
            panel_txt = trans_pair.e_trans.toPlainText()
            if panel_txt != blk_item.toPlainText():
                blk_item.setPlainText(panel_txt)
            if not blk_item.document().isEmpty():
                blk_item.blk.rich_text = blk_item.toHtml()
                blk_item.blk.translation = blk_item.toPlainText()
            else:
                blk_item.blk.rich_text = ""
                blk_item.blk.translation = ""
            blk_item.blk.text = [trans_pair.e_source.toPlainText()]
            blk_item.blk._bounding_rect = blk_item.absBoundingRect()
            blk_item.updateBlkFormat()
            cbl.append(blk_item.blk)

    def updateTranslation(self):
        for blk_item, transwidget in zip(self.textblk_item_list, self.pairwidget_list):
            transwidget.e_trans.setPlainText(blk_item.blk.translation)
            blk_item.setPlainText(blk_item.blk.translation)
        self.canvas.clear_text_stack()

    def showTextblkItemRect(self, draw_rect: bool):
        for blk_item in self.textblk_item_list:
            blk_item.draw_rect = draw_rect
            # Ensure DeviceCoordinateCache is invalidated so paint() is called.
            # The border rendering fix lives in TextBlkItem.paint() — the slow
            # path now draws the border on top (SourceOver) even in crisp mode.
            blk_item.update()

    def set_blkitems_selection(
        self, selected: bool, blk_items: List[TextBlkItem] = None
    ):
        self.canvas.block_selection_signal = True
        if blk_items is None:
            blk_items = self.textblk_item_list
        for blk_item in blk_items:
            blk_item.setSelected(selected)
        self.canvas.block_selection_signal = False
        self.on_incanvas_selection_changed()

    def on_ensure_textitem_svisible(self):
        edit: Union[TransTextEdit, SourceTextEdit] = self.sender()
        self.changeHoveringWidget(edit)
        self.canvas.gv.ensureVisible(self.textblk_item_list[edit.idx])
        self.txtblkShapeControl.setBlkItem(self.textblk_item_list[edit.idx])

    def on_page_replace_one(self):
        self.canvas.push_undo_command(PageReplaceOneCommand(self.canvas.search_widget))

    def on_page_replace_all(self):
        self.canvas.push_undo_command(PageReplaceAllCommand(self.canvas.search_widget))
