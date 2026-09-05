import copy
from contextlib import contextmanager
from difflib import SequenceMatcher
from typing import Dict, List, Union

from qtpy.QtCore import QPointF
from qtpy.QtGui import QTextCursor

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.fontformat import FontFormat
from utils.proj_imgtrans import ProjImgTrans

from .misc import doc_replace, doc_replace_no_shift
from .page_search_widget import PageSearchWidget
from .textedit_area import SourceTextEdit, TransTextEdit
from .texteditshapecontrol import TextBlkShapeControl
from .textitem import TextBlkItem


def sync_text_by_diff(
    target_edit: Union[TransTextEdit, TextBlkItem],
    source_text: str,
) -> bool:
    """把 source_text 以最小差异对账进 target_edit 的文档。

    取代旧的位置式差值重放（propagate_user_edit）：差异与插入点每次都基于
    两个文档的当前全文现场计算，不再有跨文档记录插入点导致的漂移；失焦/
    漏同步的变更会在下一次变更时自动收敛。整个对账包在一个编辑块里；
    改动区外的字符格式保留。返回是否发生了实际改动。
    """
    target_doc = target_edit.document()
    target_text = target_doc.toPlainText()
    if source_text == target_text:
        return False
    cursor = QTextCursor(target_doc)
    cursor.beginEditBlock()
    try:
        # 编辑块内坐标按原始文本计算：逆序应用 opcode，左侧坐标不被动过。
        for tag, i1, i2, j1, j2 in reversed(
            SequenceMatcher(None, target_text, source_text).get_opcodes()
        ):
            if tag == "equal":
                continue
            cursor.setPosition(i1)
            if tag != "insert":
                cursor.setPosition(i2, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            if tag != "delete":
                cursor.insertText(source_text[j1:j2])
    finally:
        cursor.endEditBlock()
    return True


@contextmanager
def replay_guard(*widgets):
    """统一重放守卫：快照重放期间切断 contentsChange 全链路的命令再发射。

    undo/redo 走内容重放（sync_text_by_diff / load_rich_text_html /
    setPlainTextAndKeepUndoStack），会触发 item/面板文档的
    contentsChange；不抑制则反向发射 propagate/push 信号，被编辑会话
    管理器误认为新用户编辑。item 侧用 block_change_signal（门控
    ui/text_engine/item.py::on_content_changed 的发射段），面板侧用
    in_acts（门控 ui/textedit_area.py::handle_content_change 与镜像
    对账）；均保存/恢复旧值，嵌套重放不打破外层抑制。
    """
    saved = []
    for w in widgets:
        if w is None:
            continue
        try:
            if isinstance(w, TextBlkItem):
                saved.append((w, True, w.block_change_signal))
                w.block_change_signal = True
            else:
                saved.append((w, False, w.in_acts))
                w.in_acts = True
        except (RuntimeError, AttributeError):
            pass
    try:
        yield
    finally:
        for w, is_item, old in saved:
            try:
                if is_item:
                    w.block_change_signal = old
                else:
                    w.in_acts = old
            except RuntimeError:
                pass


class TypingSessionCommand(QUndoCommand):
    """一次键入会话的快照命令：undo/redo = 前后全文 diff 重放。

    只存纯文本前后值，重放走 sync_text_by_diff 最小差异对账——改动区
    外的逐字符格式天然保留（与 Qt 文档撤销同语义），ruby/注解不受影响。
    译文会话同时回放画布 item 与面板 e_trans；原文会话只回放 e_source
    （blkitem 为 None）。
    """

    def __init__(
        self,
        blkitem: Union[TextBlkItem, None],
        edit: Union[SourceTextEdit, TransTextEdit],
        before_text: str,
        after_text: str,
    ):
        super().__init__()
        self.blkitem = blkitem
        self.edit = edit
        self.before_text = before_text
        self.after_text = after_text
        self.op_counter = 0

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        self._replay(self.after_text)

    def undo(self):
        self._replay(self.before_text)

    def _replay(self, text: str):
        # 僵尸命令：widget 已随切页/重渲销毁时静默失效（批量重渲保栈路径，
        # 见 _rerender_dirty_pages(clear_stack=False)）；Qt 虚函数内未捕获
        # 异常会 qFatal 直接闪退。
        try:
            with replay_guard(self.blkitem, self.edit):
                if self.blkitem is not None:
                    sync_text_by_diff(self.blkitem, text)
                sync_text_by_diff(self.edit, text)
        except RuntimeError:
            return


class FormatGestureCommand(QUndoCommand):
    """一次格式化手势的快照命令：手势期间的全部预览中间值不入栈，
    闭合时以「手势前基线 ↔ 手势终值」一个撤销步落账（保住 04feaf8 的
    多选一次手势 = 一个撤销步语义，宏机制已被本命令取代）。

    每条 entry = {item, before_html, after_html, before_rect, after_rect,
    before_fmt, after_fmt}；undo/redo = 富文本 HTML + 格式模型 + 几何
    整体重放（逐字符格式保真，含多字号块）。HTML 快照格式即保存链路同
    款（0-c 探针验证往返保真）。
    """

    def __init__(self, entries: List[dict], formatpanel=None):
        super().__init__()
        self.entries = entries
        self.formatpanel = formatpanel
        self.op_counter = 0

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        self._replay(after=True)

    def undo(self):
        self._replay(after=False)

    def _replay(self, after: bool):
        suffix = "after" if after else "before"
        for entry in self.entries:
            item = entry["item"]
            try:
                with replay_guard(item):
                    item.repaint_on_changed = False
                    try:
                        item.load_rich_text_html(entry[f"{suffix}_html"])
                        item.set_fontformat(entry[f"{suffix}_fmt"])
                        item.setRect(entry[f"{suffix}_rect"])
                    finally:
                        item.repaint_on_changed = True
                    item.repaint_background()
            except RuntimeError:
                # 僵尸条目：item 已随切页/重渲销毁，静默跳过
                continue
            if self.formatpanel is not None:
                try:
                    if item == self.formatpanel.textblk_item:
                        multi_size = (
                            not item.isEditing() and item.isMultiFontSize()
                        )
                        self.formatpanel.set_active_format(
                            item.get_fontformat(), multi_size
                        )
                except RuntimeError:
                    pass


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
            try:
                with replay_guard(item, edit):
                    item.set_fontformat(self.new_fmt, set_char_format=True)
                    edit.document().clearUndoRedoStacks()
            except RuntimeError:
                continue

    def undo(self):
        for rect, item, html, fmt, edit in zip(
            self.old_rect_lst,
            self.items,
            self.old_html_lst,
            self.old_fmt_lst,
            self.trans_widget_lst,
        ):
            try:
                with replay_guard(item, edit):
                    item.setHtml(html)
                    item.set_fontformat(fmt)
                    item.setRect(rect)
                    edit.document().clearUndoRedoStacks()
            except RuntimeError:
                continue


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


class PageReplaceOneCommand(QUndoCommand):
    """页面内替换单个（PageSearchWidget 体系，非 GlobalReplaceApplier）。

    3a 起不再借道文本文档私有 undo 栈做重放：构造期抓前后全文快照，
    undo/redo = diff 重放（查找替换重构记录要求的快照化改造）。
    """

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

        # 前快照 → 施加 → 后快照
        self.before_edit_text = self.edit.toPlainText()
        self.before_item_text = (
            None if self.edit_is_src else self.blkitem.toPlainText()
        )
        with replay_guard(self.blkitem, self.edit):
            if not self.edit_is_src:
                cursor = self.blkitem.textCursor()
                cursor.setPosition(self.sel_start)
                cursor.setPosition(
                    self.sel_start + self.ori_len, QTextCursor.MoveMode.KeepAnchor
                )
                cursor.insertText(self.reptxt)

            self.rep_cursor = self.edit.textCursor()
            self.rep_cursor.setPosition(self.sel_start)
            self.rep_cursor.setPosition(
                self.sel_start + self.ori_len, QTextCursor.MoveMode.KeepAnchor
            )
            self.rep_cursor.insertText(self.reptxt)
        self.after_edit_text = self.edit.toPlainText()
        self.after_item_text = (
            None if self.edit_is_src else self.blkitem.toPlainText()
        )

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

        self._replay(after=True)

    def undo(self):
        self._replay(after=False)
        if self.sw.current_edit is not None and self.sw.isVisible():
            move = self.sw.move_cursor(-1)
            if move == 0:
                self.sw.result_pos = max(self.sw.result_pos - 1, 0)
            else:
                self.sw.result_pos = self.sw.counter_sum - 1
            self.sw.updateCounterText()

    def _replay(self, after: bool):
        try:
            self.sw.update_cursor_on_insert = False
            with replay_guard(self.blkitem, self.edit):
                if not self.edit_is_src:
                    sync_text_by_diff(
                        self.blkitem,
                        self.after_item_text if after else self.before_item_text,
                    )
                sync_text_by_diff(
                    self.edit,
                    self.after_edit_text if after else self.before_edit_text,
                )
        except RuntimeError:
            return
        finally:
            self.sw.update_cursor_on_insert = True


class PageReplaceAllCommand(QUndoCommand):
    """页面内替换全部：逐编辑器/逐块前后快照，undo/redo = diff 重放。"""

    def __init__(self, search_widget: PageSearchWidget) -> None:
        super().__init__()
        self.op_counter = 0
        self.sw = search_widget

        # entries = (edit, blkitem|None, before_edit, after_edit,
        #            before_item|None, after_item|None)
        self.entries = []
        replace = self.sw.replace_editor.toPlainText()
        for edit, highlighter in zip(
            self.sw.search_rstedit_list, self.sw.highlighter_list
        ):
            curpos_lst = list(highlighter.matched_map.values())
            is_trans = type(edit) is TransTextEdit
            blkitem = self.sw.textblk_item_list[edit.idx] if is_trans else None
            before_edit = edit.toPlainText()
            before_item = blkitem.toPlainText() if is_trans else None
            with replay_guard(blkitem, edit):
                span_list = [[matched.start, matched.end] for matched in curpos_lst]
                sel_list = doc_replace(edit.document(), span_list, replace)
                if is_trans:
                    doc_replace_no_shift(blkitem.document(), sel_list, replace)
            self.entries.append(
                (
                    edit,
                    blkitem,
                    before_edit,
                    edit.toPlainText(),
                    before_item,
                    blkitem.toPlainText() if is_trans else None,
                )
            )

    def redo(self):
        if self.op_counter == 0:
            self.op_counter += 1
            return
        self._replay(after=True)

    def undo(self):
        self._replay(after=False)

    def _replay(self, after: bool):
        for entry in self.entries:
            edit, blkitem = entry[0], entry[1]
            edit_text = entry[3] if after else entry[2]
            item_text = entry[5] if after else entry[4]
            try:
                with replay_guard(blkitem, edit):
                    sync_text_by_diff(edit, edit_text)
                    if blkitem is not None:
                        sync_text_by_diff(blkitem, item_text)
            except RuntimeError:
                continue


def _suppress_change_sync(obj, value: bool):
    """Toggle the in_redo_undo guard that gates change-driven propagation.

    While set, content changes on the edit/item document no longer emit
    ``propagate_user_edited`` / ``push_undo_stack`` (see
    ``TextBlkItem.on_content_changed`` / edit ``handle_content_change``).
    """
    try:
        obj.in_redo_undo = value
    except AttributeError:
        pass


class GlobalReplaceApplier:
    """全局替换的当前页施加器：把收集器暂存的 live widget 改动一次性落上。

    批量替换的撤销不走本类、也不进任何撤销栈：整体回滚由替换前的
    项目快照负责（``utils/proj_imgtrans.py::write_batch_backup`` /
    ``utils/proj_imgtrans.py::restore_batch_backup``），与逐块编辑的
    文档撤销栈严格分治——快照回滚是批量操作的唯一撤销路径。原
    GlobalReplaceCommand 的 undo/redo 与自动压栈的 TextEditCommand
    双重记账、场景重建后引用失效两类缺陷随命令栈路径一并移除。

    非当前页改动由收集器（``ui/global_search_widget.py::_collect_replace_targets``）
    直接写数据并标脏，不经本类。

    Args:
        sceneitem_list: ``{"src": [...], "trans": [...]}``，收集器输出的
            当前页 live widget 引用（原 GlobalRepalceAllCommand 契约不变）。
        target_text: 替换目标文本。
        proj: 项目对象。
        scene_manager: 当前页场景管理器（仅格式-only 命中块定位 item 用）。
        format_changes: ``[{pagename, block_idx, old_ffmt, new_ffmt}]``，
            old_ffmt 须为改动前深拷贝（``utils/style_query.build_query_changes``
            契约）；可为 None/空（纯文本替换）。
    """

    def __init__(
        self,
        sceneitem_list: dict,
        target_text: str,
        proj: ProjImgTrans,
        scene_manager=None,
        format_changes: List[Dict] = None,
    ) -> None:
        self.target_text = target_text
        self.proj = proj
        self.scene_manager = scene_manager
        # 深拷贝：format_changes 里的 FontFormat 若与调用方/blk 共享对象，
        # 外部原地改写（如重建场景时的排版回写）会串改本次替换的目标值
        self.format_changes: List[Dict] = [
            copy.deepcopy(ch) for ch in (format_changes or [])
        ]

        current_pname = proj.current_img
        fmt_by_idx = {
            ch["block_idx"]: ch
            for ch in self.format_changes
            if ch["pagename"] == current_pname
        }

        for trans_dict in sceneitem_list["trans"]:
            self._stage_trans(trans_dict, fmt_by_idx.pop(trans_dict["item"].idx, None))
        # 无文本命中但格式命中的当前页块
        for idx, ch in fmt_by_idx.items():
            self._stage_format_only(idx, ch)

        for src_dict in sceneitem_list["src"]:
            self._stage_src(src_dict)

        self._apply_format_data()

    # ── 构造期：施加一次改动 ────────────────────────────────────────

    def _stage_trans(self, trans_dict: Dict, fmt_change: Dict = None):
        """施加当前页译文替换：守卫期间 edit/item 两个文档各改一次，
        不触发同步链联动，也不触发自动推栈。"""
        edit = trans_dict["edit"]
        item = trans_dict["item"]

        _suppress_change_sync(edit, True)
        try:
            sel_list = doc_replace(
                edit.document(), trans_dict["matched_map"], self.target_text
            )
        finally:
            _suppress_change_sync(edit, False)
        _suppress_change_sync(item, True)
        try:
            doc_replace_no_shift(item.document(), sel_list, self.target_text)
        finally:
            _suppress_change_sync(item, False)
        trans_dict.pop("matched_map", None)

        if fmt_change is not None:
            _suppress_change_sync(item, True)
            try:
                item.set_fontformat(fmt_change["new_ffmt"], set_char_format=True)
            finally:
                _suppress_change_sync(item, False)

        # 批量替换不依赖文档撤销栈，清空涉及文档的栈：遗留零散步
        # 会在回滚后与文档状态错位
        item.document().clearUndoRedoStacks()
        edit.document().clearUndoRedoStacks()

    def _stage_format_only(self, idx: int, fmt_change: Dict):
        item = _find_blk_item_in(self.scene_manager, idx)
        if item is None:
            return  # 无 live item：仅数据层（_apply_format_data 覆盖）
        _suppress_change_sync(item, True)
        try:
            item.set_fontformat(fmt_change["new_ffmt"], set_char_format=True)
        finally:
            _suppress_change_sync(item, False)
        item.document().clearUndoRedoStacks()

    def _stage_src(self, src_dict: Dict):
        edit = src_dict["edit"]
        _suppress_change_sync(edit, True)
        try:
            edit.setPlainTextAndKeepUndoStack(src_dict["replace"])
        finally:
            _suppress_change_sync(edit, False)
        edit.document().clearUndoRedoStacks()
        src_dict.pop("replace", None)

    def _apply_format_data(self):
        """所有格式 patch 的数据层落点（含无 live item 的防御分支）。"""
        current_pname = self.proj.current_img
        for ch in self.format_changes:
            page = self.proj.pages.get(ch["pagename"])
            if page is None or not 0 <= ch["block_idx"] < len(page):
                continue
            page[ch["block_idx"]].fontformat = copy.deepcopy(ch["new_ffmt"])
            if ch["pagename"] != current_pname:
                self.proj.mark_page_needs_rerender(ch["pagename"])


class MultiPasteCommand(QUndoCommand):
    """多块粘贴：item 侧富文本 HTML 快照 + 面板纯文本快照，重放式 undo/redo。"""

    def __init__(
        self,
        text_list: Union[str, List],
        blkitems: List[TextBlkItem],
        etrans: List[TransTextEdit],
    ) -> None:
        super().__init__()
        self.op_counter = -1

        if len(blkitems) > 0:
            if isinstance(text_list, str):
                text_list = [text_list] * len(blkitems)

        # entries = (blkitem, etran, before_html, after_html,
        #            before_edit, after_edit)
        self.entries = []
        for blkitem, etran, text in zip(blkitems, etrans, text_list):
            before_html = blkitem.toHtml()
            before_edit = etran.toPlainText()
            with replay_guard(blkitem, etran):
                etran.setPlainTextAndKeepUndoStack(text)
                blkitem.setPlainTextAndKeepUndoStack(text)
            self.entries.append(
                (
                    blkitem,
                    etran,
                    before_html,
                    blkitem.toHtml(),
                    before_edit,
                    etran.toPlainText(),
                )
            )

    def redo(self):
        if self.op_counter < 0:
            self.op_counter += 1
            return
        self._replay(after=True)

    def undo(self):
        self._replay(after=False)

    def _replay(self, after: bool):
        for blkitem, etran, before_html, after_html, before_edit, after_edit in self.entries:
            try:
                with replay_guard(blkitem, etran):
                    blkitem.load_rich_text_html(after_html if after else before_html)
                    sync_text_by_diff(etran, after_edit if after else before_edit)
            except RuntimeError:
                continue


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

            try:
                if which == "new":
                    blk.translation = ch["new_text"]
                    blk.rich_text = ""
                    with replay_guard(item):
                        item.setPlainTextAndKeepUndoStack(ch["new_text"])
                        # 新文本结构可能变了，重应用当前 char format 保证样式不丢
                        item.set_fontformat(
                            item.get_fontformat(), set_char_format=True,
                            set_stroke_width=False, set_effect=False,
                        )
                    if ch.get("squeeze", False):
                        item.squeezeBoundingRect(repaint=True)
                    else:
                        item.repaint_background()
                    # 同步右侧 e_trans
                    try:
                        pairw = sm.pairwidget_list[bidx]
                        if pairw is not None:
                            with replay_guard(pairw.e_trans):
                                pairw.e_trans.setPlainTextAndKeepUndoStack(
                                    ch["new_text"]
                                )
                                pairw.e_trans.document().clearUndoRedoStacks()
                    except Exception:
                        pass
                else:
                    blk.translation = ch["old_translation"]
                    blk.rich_text = ch["old_rich_text"]
                    with replay_guard(item):
                        item.setHtml(ch["old_html"])
                        item.set_fontformat(ch["old_ffmt"])
                    if ch.get("old_rect") is not None:
                        item.setRect(ch["old_rect"])
                    else:
                        item.repaint_background()
                    try:
                        pairw = sm.pairwidget_list[bidx]
                        if pairw is not None:
                            with replay_guard(pairw.e_trans):
                                pairw.e_trans.setPlainTextAndKeepUndoStack(
                                    ch["old_translation"]
                                )
                                pairw.e_trans.document().clearUndoRedoStacks()
                    except Exception:
                        pass
            except RuntimeError:
                continue


def _find_blk_item_in(scene_manager, block_idx: int):
    """Return the live ``TextBlkItem`` for *block_idx* on the current page, or None."""
    try:
        tbi_list = scene_manager.textblk_item_list
        if 0 <= block_idx < len(tbi_list):
            return tbi_list[block_idx]
    except Exception:
        pass
    return None
