from typing import List, Union

from qtpy.QtCore import QPointF
from qtpy.QtGui import QTextCursor

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.fontformat import FontFormat
from utils.proj_imgtrans import ProjImgTrans

from .misc import doc_replace, doc_replace_no_shift
from .page_search_widget import Matched, PageSearchWidget
from .textedit_area import SourceTextEdit, TransTextEdit
from .texteditshapecontrol import TextBlkShapeControl
from .textitem import TextBlkItem


def propagate_user_edit(
    src_edit: Union[TransTextEdit, TextBlkItem],
    target_edit: Union[TransTextEdit, TextBlkItem],
    pos: int,
    added_text: str,
    joint_previous: bool = False,
):
    ori_count = target_edit.document().characterCount()
    new_count = src_edit.document().characterCount()
    removed = ori_count + len(added_text) - new_count

    cursor = target_edit.textCursor()
    cursor.setPosition(pos)
    if joint_previous:
        cursor.joinPreviousEditBlock()
    else:
        cursor.beginEditBlock()
    if removed > 0:
        cursor.setPosition(pos + removed, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText(added_text)
    cursor.endEditBlock()
    target_edit.old_undo_steps = target_edit.document().availableUndoSteps()


class MoveBlkItemsCommand(QUndoCommand):
    def __init__(self, items: List[TextBlkItem], shape_ctrl: TextBlkShapeControl):
        super(MoveBlkItemsCommand, self).__init__()
        self.items = items
        self.old_pos_lst: List[QPointF] = []
        self.new_pos_lst: List[QPointF] = []
        self.shape_ctrl = shape_ctrl
        for item in items:
            padding = item.padding()
            padding = QPointF(padding, padding)
            self.old_pos_lst.append(item.oldPos + padding)
            self.new_pos_lst.append(item.pos() + padding)
            item.oldPos = item.pos()

    def redo(self):
        for item, new_pos in zip(self.items, self.new_pos_lst):
            padding = item.padding()
            padding = QPointF(padding, padding)
            item.setPos(new_pos - padding)
            self._sync_block(item)
            if self.shape_ctrl.blk_item == item and self.shape_ctrl.pos() != new_pos:
                self.shape_ctrl.setPos(new_pos)

    def undo(self):
        for item, old_pos in zip(self.items, self.old_pos_lst):
            padding = item.padding()
            padding = QPointF(padding, padding)
            item.setPos(old_pos - padding)
            self._sync_block(item)
            if self.shape_ctrl.blk_item == item and self.shape_ctrl.pos() != old_pos:
                self.shape_ctrl.setPos(old_pos)

    @staticmethod
    def _sync_block(item: TextBlkItem) -> None:
        if item.blk is not None:
            item.blk._bounding_rect = item.absBoundingRect()
            item.blk.sync_xyxy_from_bounding_rect()


class ApplyFontformatCommand(QUndoCommand):
    def __init__(
        self,
        items: List[TextBlkItem],
        trans_widget_lst: List[TransTextEdit],
        fontformat: FontFormat,
    ):
        super(ApplyFontformatCommand, self).__init__()
        self.items = items
        self.old_html_lst = []
        self.old_rect_lst = []
        self.old_fmt_lst = []
        self.new_fmt = fontformat
        self.trans_widget_lst = trans_widget_lst
        for item in items:
            self.old_html_lst.append(item.toHtml())
            self.old_fmt_lst.append(item.get_fontformat())
            self.old_rect_lst.append(item.absBoundingRect(qrect=True))

    def redo(self):
        for item, edit in zip(self.items, self.trans_widget_lst):
            item.set_fontformat(self.new_fmt, set_char_format=True)
            edit.document().clearUndoRedoStacks()

    def undo(self):
        for rect, item, html, fmt, edit in zip(
            self.old_rect_lst,
            self.items,
            self.old_html_lst,
            self.old_fmt_lst,
            self.trans_widget_lst,
        ):
            item.setHtml(html)
            item.set_fontformat(fmt)
            item.setRect(rect)
            edit.document().clearUndoRedoStacks()


class ReshapeItemCommand(QUndoCommand):
    def __init__(self, item: TextBlkItem):
        super(ReshapeItemCommand, self).__init__()
        self.item = item
        self.oldRect = item.oldRect
        self.newRect = item.absBoundingRect(qrect=True)
        self.idx = -1

    def redo(self):
        if self.idx < 0:
            self.idx += 1
            return
        self.item.setRect(self.newRect)

    def undo(self):
        self.item.setRect(self.oldRect)

    def mergeWith(self, command: QUndoCommand):
        item = command.item
        if self.item != item:
            return False
        self.newRect = item.rect()
        return True


class RotateItemCommand(QUndoCommand):
    def __init__(
        self, item: TextBlkItem, new_angle: float, shape_ctrl: TextBlkShapeControl
    ):
        super(RotateItemCommand, self).__init__()
        self.item = item
        self.old_angle = item.rotation()
        self.new_angle = new_angle
        self.shape_ctrl = shape_ctrl

    def redo(self):
        self.item.setRotation(self.new_angle)
        self.item.blk.angle = self.new_angle
        if (
            self.shape_ctrl.blk_item == self.item
            and self.shape_ctrl.rotation() != self.new_angle
        ):
            self.shape_ctrl.setRotation(self.new_angle)

    def undo(self):
        self.item.setRotation(self.old_angle)
        self.item.blk.angle = self.old_angle
        if (
            self.shape_ctrl.blk_item == self.item
            and self.shape_ctrl.rotation() != self.old_angle
        ):
            self.shape_ctrl.setRotation(self.old_angle)

    def mergeWith(self, command: QUndoCommand):
        item = command.item
        if self.item != item:
            return False
        self.new_angle = item.angle
        return True


class SqueezeCommand(QUndoCommand):
    def __init__(self, blkitem_lst: List[TextBlkItem], ctrl: TextBlkShapeControl):
        super(SqueezeCommand, self).__init__()
        self.blkitem_lst = blkitem_lst
        self.old_rect_lst = []
        self.ctrl = ctrl
        for item in blkitem_lst:
            self.old_rect_lst.append(item.absBoundingRect(qrect=True))

    def redo(self):
        for blk in self.blkitem_lst:
            blk.squeezeBoundingRect()

    def undo(self):
        for blk, rect in zip(self.blkitem_lst, self.old_rect_lst):
            blk.setRect(rect, repaint=True)
            if blk.under_ctrl:
                self.ctrl.updateBoundingRect()


class ResetAngleCommand(QUndoCommand):
    def __init__(self, blkitem_lst: List[TextBlkItem], ctrl: TextBlkShapeControl):
        super(ResetAngleCommand, self).__init__()
        self.blkitem_lst = blkitem_lst
        self.angle_lst = []
        self.ctrl = ctrl
        blkitem_lst = []
        for blk in self.blkitem_lst:
            rotation = blk.rotation()
            if rotation != 0:
                self.angle_lst.append(rotation)
                blkitem_lst.append(blk)
        self.blkitem_lst = blkitem_lst

    def redo(self):
        for blk in self.blkitem_lst:
            blk.setAngle(0)
            if self.ctrl.blk_item == blk:
                self.ctrl.setAngle(0)

    def undo(self):
        for blk, angle in zip(self.blkitem_lst, self.angle_lst):
            blk.setAngle(angle)
            if self.ctrl.blk_item == blk:
                self.ctrl.setAngle(angle)


class TextItemEditCommand(QUndoCommand):
    def __init__(
        self,
        blkitem: TextBlkItem,
        trans_edit: TransTextEdit,
        num_steps: int,
        formatpanel=None,
    ):
        super(TextItemEditCommand, self).__init__()
        self.op_counter = 0
        self.edit = trans_edit
        self.blkitem = blkitem
        self.num_steps = num_steps
        self.is_formatting = blkitem.is_formatting
        self.old_ffmt_values = self.new_ffmt_values = None
        if blkitem.is_formatting and blkitem.old_ffmt_values is not None:
            self.old_ffmt_values = blkitem.old_ffmt_values.copy()
            self.new_ffmt_values = self.old_ffmt_values.copy()
            for k in self.new_ffmt_values:
                self.new_ffmt_values[k] = getattr(blkitem.fontformat, k)
        self.formatpanel = formatpanel

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return

        self.blkitem.repaint_on_changed = False
        if self.new_ffmt_values is not None:
            for k, v in self.new_ffmt_values.items():
                self.blkitem.fontformat[k] = v
        self.blkitem.redo()
        self.blkitem.repaint_on_changed = True
        if self.num_steps > 0:
            self.blkitem.repaint_background()

        if self.is_formatting and self.blkitem == self.formatpanel.textblk_item:
            multi_size = not self.blkitem.isEditing() and self.blkitem.isMultiFontSize()
            self.formatpanel.set_active_format(
                self.blkitem.get_fontformat(), multi_size
            )

        if self.edit is not None and not self.is_formatting:
            self.edit.redo()

    def undo(self):
        self.blkitem.repaint_on_changed = False
        if self.old_ffmt_values is not None:
            for k, v in self.old_ffmt_values.items():
                self.blkitem.fontformat[k] = v
        self.blkitem.undo()
        self.blkitem.repaint_on_changed = True
        if self.num_steps > 0:
            self.blkitem.repaint_background()

        if self.is_formatting and self.blkitem == self.formatpanel.textblk_item:
            multi_size = not self.blkitem.isEditing() and self.blkitem.isMultiFontSize()
            self.formatpanel.set_active_format(
                self.blkitem.get_fontformat(), multi_size
            )

        if self.edit is not None:
            self.edit.undo()


class TextEditCommand(QUndoCommand):
    def __init__(
        self,
        edit: Union[SourceTextEdit, TransTextEdit],
        num_steps: int,
        blkitem: TextBlkItem,
    ) -> None:
        super().__init__()
        # TODO: remove it for transtextedit
        self.edit = edit
        self.blkitem = blkitem
        self.op_counter = 0
        self.num_steps = num_steps

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        self.edit.redo()
        if self.blkitem is not None:
            self.blkitem.redo()

    def undo(self):
        self.edit.undo()
        if self.blkitem is not None:
            self.blkitem.undo()


class PageReplaceOneCommand(QUndoCommand):
    def __init__(self, se: PageSearchWidget, parent=None):
        super(PageReplaceOneCommand, self).__init__(parent)
        self.op_counter = 0
        self.sw = se
        self.reptxt = self.sw.replace_editor.toPlainText()
        self.repl_len = len(self.reptxt)

        self.sel_start = self.sw.current_cursor.selectionStart()
        self.oritxt = self.sw.current_cursor.selectedText()
        self.ori_len = len(self.oritxt)
        self.edit: Union[SourceTextEdit, TransTextEdit] = self.sw.current_edit
        self.edit_is_src = type(self.edit) is SourceTextEdit
        self.blkitem = self.sw.textblk_item_list[self.sw.current_edit.idx]

        if self.sw.current_edit is not None and self.sw.isVisible():
            move = self.sw.move_cursor(1)
            if move == 0:
                self.sw.result_pos = min(
                    self.sw.counter_sum - 1, self.sw.result_pos + 1
                )
            else:
                self.sw.result_pos = 0

        if not self.edit_is_src:
            cursor = self.blkitem.textCursor()
            cursor.setPosition(self.sel_start)
            cursor.setPosition(
                self.sel_start + self.ori_len, QTextCursor.MoveMode.KeepAnchor
            )
            cursor.beginEditBlock()
            cursor.insertText(self.reptxt)
            cursor.endEditBlock()

        self.rep_cursor = self.edit.textCursor()
        self.rep_cursor.setPosition(self.sel_start)
        self.rep_cursor.setPosition(
            self.sel_start + self.ori_len, QTextCursor.MoveMode.KeepAnchor
        )
        self.rep_cursor.insertText(self.reptxt)
        self.edit.updateUndoSteps()

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return

        if self.sw.current_edit is not None and self.sw.isVisible():
            move = self.sw.move_cursor(1)
            if move == 0:
                self.sw.result_pos = min(
                    self.sw.counter_sum - 1, self.sw.result_pos + 1
                )
            else:
                self.sw.result_pos = 0

        if not self.edit_is_src:
            self.blkitem.redo()
        self.edit.redo()

    def undo(self):
        if not self.edit_is_src:
            self.blkitem.undo()
        self.sw.update_cursor_on_insert = False
        self.edit.undo()
        self.sw.update_cursor_on_insert = True
        if self.sw.current_edit is not None and self.sw.isVisible():
            move = self.sw.move_cursor(-1)
            if move == 0:
                self.sw.result_pos = max(self.sw.result_pos - 1, 0)
            else:
                self.sw.result_pos = self.sw.counter_sum - 1
            self.sw.updateCounterText()


class PageReplaceAllCommand(QUndoCommand):
    def __init__(self, search_widget: PageSearchWidget) -> None:
        super().__init__()
        self.op_counter = 0
        self.sw = search_widget

        self.rstedit_list: List[SourceTextEdit] = []
        self.blkitem_list: List[TextBlkItem] = []
        curpos_list: List[List[Matched]] = []
        for edit, highlighter in zip(
            self.sw.search_rstedit_list, self.sw.highlighter_list
        ):
            self.rstedit_list.append(edit)
            curpos_list.append(list(highlighter.matched_map.values()))

        replace = self.sw.replace_editor.toPlainText()
        for edit, curpos_lst in zip(self.rstedit_list, curpos_list):
            redo_blk = type(edit) is TransTextEdit
            if redo_blk:
                blkitem = self.sw.textblk_item_list[edit.idx]
                self.blkitem_list.append(blkitem)
            span_list = [[matched.start, matched.end] for matched in curpos_lst]
            sel_list = doc_replace(edit.document(), span_list, replace)
            if redo_blk:
                doc_replace_no_shift(blkitem.document(), sel_list, replace)
                blkitem.updateUndoSteps()

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return

        for edit in self.rstedit_list:
            edit.redo()
        for blkitem in self.blkitem_list:
            blkitem.redo()

    def undo(self):
        for edit in self.rstedit_list:
            edit.undo()
        for blkitem in self.blkitem_list:
            blkitem.undo()


class GlobalRepalceAllCommand(QUndoCommand):
    def __init__(
        self,
        sceneitem_list: dict,
        background_list: dict,
        target_text: str,
        proj: ProjImgTrans,
    ) -> None:
        super().__init__()
        self.op_counter = -1
        self.target_text = target_text
        self.proj = proj
        self.trans_list = sceneitem_list["trans"]
        self.src_list = sceneitem_list["src"]
        self.btrans_list = background_list["trans"]
        self.bsrc_list = background_list["src"]

        # Constructed synchronously on the GUI thread right after the live
        # widget references were collected, so they are guaranteed to exist
        # here. undo()/redo() may run much later and guard against widgets
        # deleted in the meantime with RuntimeError handlers.
        for trans_dict in self.trans_list:
            edit: TransTextEdit = trans_dict["edit"]
            item: TextBlkItem = trans_dict["item"]
            matched_map = trans_dict["matched_map"]
            sel_list = doc_replace(edit.document(), matched_map, target_text)

            doc_replace_no_shift(item.document(), sel_list, target_text)
            item.updateUndoSteps()

            trans_dict.pop("matched_map")

        for src_dict in self.src_list:
            edit: SourceTextEdit = src_dict["edit"]
            edit.setPlainTextAndKeepUndoStack(src_dict["replace"])
            edit.updateUndoSteps()
            src_dict.pop("replace")

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return

        for trans_dict in self.trans_list:
            try:
                trans_dict["edit"].redo()
                trans_dict["item"].redo()
            except RuntimeError:
                pass
        for src_dict in self.src_list:
            try:
                src_dict["edit"].redo()
            except RuntimeError:
                pass
        self._apply_background("replace")

    def undo(self):
        for trans_dict in self.trans_list:
            try:
                trans_dict["edit"].undo()
                trans_dict["item"].undo()
            except RuntimeError:
                pass
        for src_dict in self.src_list:
            try:
                src_dict["edit"].undo()
            except RuntimeError:
                pass
        self._apply_background("ori")

    def _apply_background(self, key: str):
        """Set background (non-current-page) blocks to their *key* state.

        Skips pages/blocks that vanished since the command was created, so a
        deleted page never crashes the undo stack. Both branches also refresh
        the lazy re-render flag so visiting the page regenerates its image.
        """
        for trans_dict in self.btrans_list:
            blk = self._get_blk(trans_dict["pagename"], trans_dict["idx"])
            if blk is not None:
                blk.translation = trans_dict[key]
                blk.rich_text = trans_dict[key + "_html"]
                self.proj.mark_page_needs_rerender(trans_dict["pagename"])

        for src_dict in self.bsrc_list:
            blk = self._get_blk(src_dict["pagename"], src_dict["idx"])
            if blk is not None:
                blk.text = [src_dict[key]]
                self.proj.mark_page_needs_rerender(src_dict["pagename"])

    def _get_blk(self, pagename: str, idx: int):
        page = self.proj.pages.get(pagename)
        if page is not None and 0 <= idx < len(page):
            return page[idx]
        return None


class MultiPasteCommand(QUndoCommand):
    def __init__(
        self,
        text_list: Union[str, List],
        blkitems: List[TextBlkItem],
        etrans: List[TransTextEdit],
    ) -> None:
        super().__init__()
        self.op_counter = -1
        self.blkitems = blkitems
        self.etrans = etrans

        if len(blkitems) > 0:
            if isinstance(text_list, str):
                text_list = [text_list] * len(blkitems)

        for blkitem, etran, text in zip(self.blkitems, self.etrans, text_list):
            etran.setPlainTextAndKeepUndoStack(text)
            blkitem.setPlainTextAndKeepUndoStack(text)

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        for blkitem, etran in zip(self.blkitems, self.etrans):
            blkitem.redo()
            etran.redo()

    def undo(self):
        for blkitem, etran in zip(self.blkitems, self.etrans):
            blkitem.undo()
            etran.undo()


class NormalizeBreaksCommand(QUndoCommand):
    """整理换行的 undo 命令。一次 push，Ctrl+Z 全部回退。

    跨页批量应用 ``normalize_softbreaks``：当前页经 live ``TextBlkItem``
    写新文本，其它页只写 ``blk.translation``（redo 时清空 ``rich_text`` 交排版器重排）
    squeeze 并入命令，保证一次 Ctrl+Z 连同收缩一起回退。
    """

    def __init__(
        self,
        proj: ProjImgTrans,
        scene_manager,
        changes: List[dict],
    ):
        """Args:
            changes: 每项 ``{pagename, block_idx, old_translation, old_rich_text,
                new_text, squeeze}``。当前页块额外存 ``old_html``/``old_rect``/``old_ffmt``。
        """
        super().__init__()
        self.proj = proj
        self.sm = scene_manager
        self.changes = changes
        self._first_redo = False

        current_pname = proj.current_img
        for ch in changes:
            if ch["pagename"] != current_pname:
                continue
            item = _find_blk_item_in(scene_manager, ch["block_idx"])
            if item is None:
                continue
            ch["old_html"] = item.toHtml()
            ch["old_rect"] = item.absBoundingRect(qrect=True)
            ch["old_ffmt"] = item.get_fontformat()

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._apply("new")

    def undo(self):
        self._apply("old")

    def _apply(self, which: str):
        """``which='new'``: 应用新文本；``which='old'``: 还原旧文本。"""
        current_pname = self.proj.current_img
        sm = self.sm
        for ch in self.changes:
            pname = ch["pagename"]
            bidx = ch["block_idx"]
            blk = self.proj.pages[pname][bidx]
            if pname != current_pname:
                # 非当前页：只改数据，无 live item
                if which == "new":
                    blk.translation = ch["new_text"]
                    blk.rich_text = ""
                else:
                    blk.translation = ch["old_translation"]
                    blk.rich_text = ch["old_rich_text"]
                continue

            # 当前页：通过 live item 写
            item = _find_blk_item_in(sm, bidx)
            if item is None:
                # 防御：item 不在就只改数据
                if which == "new":
                    blk.translation = ch["new_text"]
                    blk.rich_text = ""
                else:
                    blk.translation = ch["old_translation"]
                    blk.rich_text = ch["old_rich_text"]
                continue

            if which == "new":
                blk.translation = ch["new_text"]
                blk.rich_text = ""
                item.setPlainTextAndKeepUndoStack(ch["new_text"])
                # 新文本结构可能变了，重应用当前 char format 保证样式不丢
                item.set_fontformat(item.get_fontformat(), set_char_format=True,
                                    set_stroke_width=False, set_effect=False)
                if ch.get("squeeze", False):
                    item.squeezeBoundingRect(repaint=True)
                else:
                    item.repaint_background()
                # 同步右侧 e_trans
                try:
                    pairw = sm.pairwidget_list[bidx]
                    if pairw is not None:
                        pairw.e_trans.setPlainTextAndKeepUndoStack(ch["new_text"])
                        pairw.e_trans.document().clearUndoRedoStacks()
                except Exception:
                    pass
            else:
                blk.translation = ch["old_translation"]
                blk.rich_text = ch["old_rich_text"]
                item.setHtml(ch["old_html"])
                item.set_fontformat(ch["old_ffmt"])
                if ch.get("old_rect") is not None:
                    item.setRect(ch["old_rect"])
                else:
                    item.repaint_background()
                try:
                    pairw = sm.pairwidget_list[bidx]
                    if pairw is not None:
                        pairw.e_trans.setPlainTextAndKeepUndoStack(
                            ch["old_translation"]
                        )
                        pairw.e_trans.document().clearUndoRedoStacks()
                except Exception:
                    pass


def _find_blk_item_in(scene_manager, block_idx: int):
    """Return the live ``TextBlkItem`` for *block_idx* on the current page, or None."""
    try:
        tbi_list = scene_manager.textblk_item_list
        if 0 <= block_idx < len(tbi_list):
            return tbi_list[block_idx]
    except Exception:
        pass
    return None
