from qtpy.QtCore import QDateTime, QCoreApplication
from qtpy.QtGui import QImage, QPainter

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from typing import List, Tuple

import numpy as np

from .canvas import Canvas, TextBlkItem
from .image_edit import DrawingLayer
from .textedit_area import TransPairWidget
from .textedit_commands import (
    command_page_stale,
    replay_guard,
    resolve_blk_entry,
    sync_text_by_diff,
)


class StrokeItemUndoCommand(QUndoCommand):
    def __init__(
        self, target_layer: DrawingLayer, rect: Tuple[int], qimg: QImage, erasing=False
    ):
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Eraser")
            if erasing
            else QCoreApplication.translate("UndoCommand", "Brush Stroke")
        )
        self.qimg = qimg
        self.x = rect[0]
        self.y = rect[1]
        self.target_layer = target_layer
        self.key = str(QDateTime.currentMSecsSinceEpoch())
        if erasing:
            self.compose_mode = QPainter.CompositionMode.CompositionMode_DestinationOut
        else:
            self.compose_mode = QPainter.CompositionMode.CompositionMode_SourceOver

    def undo(self):
        if self.qimg is not None:
            self.target_layer.removeQImage(self.key)
            self.target_layer.update()

    def redo(self):
        if self.qimg is not None:
            self.target_layer.addQImage(
                self.x, self.y, self.qimg, self.compose_mode, self.key
            )
            self.target_layer.scene().update()


class InpaintUndoCommand(QUndoCommand):
    # 图像命令标记（阶段4-3b）：入全局跨页栈，打页标签 + 图像代数，
    # undo/redo 只在所属页为当前页时执行（armed 门保证）
    image_history = True

    def __init__(
        self,
        canvas: Canvas,
        inpainted: np.ndarray,
        mask: np.ndarray,
        inpaint_rect: List[int],
        merge_existing_mask=False,
    ):
        super().__init__(QCoreApplication.translate("UndoCommand", "Inpaint"))
        self.canvas = canvas
        img_array = self.canvas.imgtrans_proj.inpainted_array
        mask_array = self.canvas.imgtrans_proj.mask_array
        img_view = img_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        mask_view = mask_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        self.undo_img = np.copy(img_view)
        self.undo_mask = np.copy(mask_view)
        self.redo_img = inpainted
        if merge_existing_mask:
            self.redo_mask = np.bitwise_or(mask, mask_view)
        else:
            self.redo_mask = mask
        self.inpaint_rect = inpaint_rect

    def redo(self) -> None:
        if command_page_stale(self, getattr(self, "_proj", None)):
            return
        inpaint_rect = self.inpaint_rect
        img_array = self.canvas.imgtrans_proj.inpainted_array
        mask_array = self.canvas.imgtrans_proj.mask_array
        img_view = img_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        mask_view = mask_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        img_view[:] = self.redo_img
        mask_view[:] = self.redo_mask
        self.canvas.updateLayers()

    def undo(self) -> None:
        if command_page_stale(self, getattr(self, "_proj", None)):
            return
        inpaint_rect = self.inpaint_rect
        img_array = self.canvas.imgtrans_proj.inpainted_array
        mask_array = self.canvas.imgtrans_proj.mask_array
        img_view = img_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        mask_view = mask_array[
            inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
        ]
        img_view[:] = self.undo_img
        mask_view[:] = self.undo_mask
        self.canvas.updateLayers()

    def try_absorb(self, other: "InpaintUndoCommand") -> bool:
        """同区域连续修复聚合（阶段4-3a，决策5）：吸收另一条同矩形修复
        命令的「修复后」端点，仅存首前末后，不保留中间态。吸收成功后由
        入口对 other 调 redo() 应用新状态并丢弃——成功返回 true 即承诺
        已接管 other 的 redo 端点，other 不再入栈。"""
        if type(other) is not InpaintUndoCommand:
            return False
        if self.canvas is not other.canvas:
            return False
        if self.inpaint_rect != other.inpaint_rect:
            return False
        self.redo_img = other.redo_img
        self.redo_mask = other.redo_mask
        return True


class EmptyCommand(QUndoCommand):
    def __init__(self, parent=None):
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Pipeline Write"),
            parent=parent,
        )


class RunBlkTransCommand(QUndoCommand):
    def __init__(
        self,
        canvas: Canvas,
        blkitems: List[TextBlkItem],
        transpairw_list: List[TransPairWidget],
        mode: int,
    ):
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Run Pipeline")
        )

        self.empty_command = None
        if mode > 1:
            self.empty_command = EmptyCommand()
            canvas.push_draw_command(self.empty_command)

        self.op_counter = -1
        self.blkitems = blkitems
        self.transpairw_list = transpairw_list

        # 3a 快照命令制：构造期抓前后快照（item 侧 HTML 全保真，面板侧纯
        # 文本），undo/redo = 内容重放，不再借道文本文档私有 undo 栈排水。
        # entries = (blkitem, transpairw, blk, before_item_html,
        #            after_item_html, before_trans, after_trans,
        #            before_source, after_source)
        self.text_entries = []
        if mode < 3:
            for blkitem, transpairw in zip(self.blkitems, self.transpairw_list):
                write_trans = mode != 0
                before_item_html = blkitem.toHtml() if write_trans else None
                before_trans = (
                    transpairw.e_trans.toPlainText() if write_trans else None
                )
                before_source = transpairw.e_source.toPlainText()
                with replay_guard(
                    blkitem, transpairw.e_trans, transpairw.e_source
                ):
                    if write_trans:
                        trs = blkitem.blk.translation
                        transpairw.e_trans.setPlainTextAndKeepUndoStack(trs)
                        blkitem.setPlainTextAndKeepUndoStack(trs)
                    blkitem.blk.rich_text = ""
                    if mode >= 0:
                        transpairw.e_source.setPlainTextAndKeepUndoStack(
                            blkitem.blk.get_text()
                        )
                self.text_entries.append(
                    (
                        blkitem,
                        transpairw,
                        blkitem.blk,
                        before_item_html,
                        blkitem.toHtml() if write_trans else None,
                        before_trans,
                        transpairw.e_trans.toPlainText() if write_trans else None,
                        before_source,
                        transpairw.e_source.toPlainText(),
                    )
                )

        self.canvas = canvas
        self.mode = mode
        if mode > 1:
            self.undo_img_list = []
            self.undo_mask_list = []
            self.redo_img_list = []
            self.redo_mask_list = []
            self.inpaint_rect_lst = []
            img_array = self.canvas.imgtrans_proj.inpainted_array
            mask_array = self.canvas.imgtrans_proj.mask_array
            self.num_inpainted = 0
            for item in self.blkitems:
                inpainted_dict = item.blk.region_inpaint_dict
                item.blk.region_inpaint_dict = None
                if inpainted_dict is None:
                    self.undo_img_list.append(None)
                    self.undo_mask_list.append(None)
                    self.redo_mask_list.append(None)
                    self.redo_img_list.append(None)
                    self.inpaint_rect_lst.append(None)
                else:
                    inpaint_rect = inpainted_dict["inpaint_rect"]
                    try:
                        img_view = img_array[
                            inpaint_rect[1] : inpaint_rect[3],
                            inpaint_rect[0] : inpaint_rect[2],
                        ]
                        mask_view = mask_array[
                            inpaint_rect[1] : inpaint_rect[3],
                            inpaint_rect[0] : inpaint_rect[2],
                        ]
                        self.undo_img_list.append(np.copy(img_view))
                        self.undo_mask_list.append(np.copy(mask_view))
                        self.redo_img_list.append(inpainted_dict["inpainted"])
                        self.redo_mask_list.append(inpainted_dict["mask"])
                        self.inpaint_rect_lst.append(inpaint_rect)
                        self.num_inpainted += 1
                    except (IndexError, ValueError):
                        # Page may have changed during async translation
                        self.undo_img_list.append(None)
                        self.undo_mask_list.append(None)
                        self.redo_mask_list.append(None)
                        self.redo_img_list.append(None)
                        self.inpaint_rect_lst.append(None)

    def redo(self) -> None:

        if self.empty_command is not None:
            self.empty_command.redo()

        if self.mode > 1 and self.num_inpainted > 0:
            img_array = self.canvas.imgtrans_proj.inpainted_array
            mask_array = self.canvas.imgtrans_proj.mask_array
            for inpaint_rect, redo_img, redo_mask in zip(
                self.inpaint_rect_lst, self.redo_img_list, self.redo_mask_list
            ):
                if inpaint_rect is None:
                    continue
                img_view = img_array[
                    inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
                ]
                mask_view = mask_array[
                    inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
                ]
                img_view[:] = redo_img
                mask_view[:] = redo_mask
            self.canvas.updateLayers()

        if self.op_counter < 0:
            self.op_counter += 1
            return

        self._replay_text(after=True)

    def _replay_text(self, after: bool):
        """文字部分快照重放。跨页历史：命令存活跨越切页，重放前按 blk
        锚点重解析 live widget（脱离场景的旧引用会静默写到隐形对象上）；
        页屏障过期或锚点失效（页重写/块删除）时整条跳过。widget 已销毁
        的兜底防御保留（Qt 虚函数内异常会 qFatal，必须捕获 RuntimeError）。
        """
        if command_page_stale(self, getattr(self, "_proj", None)):
            return
        for (
            blkitem,
            transpairw,
            blk,
            before_item_html,
            after_item_html,
            before_trans,
            after_trans,
            before_source,
            after_source,
        ) in self.text_entries:
            resolved = resolve_blk_entry(blk, blkitem, transpairw)
            if resolved is None:
                continue
            blkitem, transpairw = resolved
            try:
                with replay_guard(
                    blkitem, transpairw.e_trans, transpairw.e_source
                ):
                    if before_item_html is not None:
                        blkitem.load_rich_text_html(
                            after_item_html if after else before_item_html
                        )
                    if before_trans is not None:
                        sync_text_by_diff(
                            transpairw.e_trans,
                            after_trans if after else before_trans,
                        )
                    sync_text_by_diff(
                        transpairw.e_source,
                        after_source if after else before_source,
                    )
            except RuntimeError:
                continue

    def undo(self) -> None:

        if self.empty_command is not None:
            self.empty_command.undo()

        if self.mode > 1 and self.num_inpainted > 0:
            img_array = self.canvas.imgtrans_proj.inpainted_array
            mask_array = self.canvas.imgtrans_proj.mask_array
            for inpaint_rect, undo_img, undo_mask in zip(
                self.inpaint_rect_lst, self.undo_img_list, self.undo_mask_list
            ):
                if inpaint_rect is None:
                    continue
                img_view = img_array[
                    inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
                ]
                mask_view = mask_array[
                    inpaint_rect[1] : inpaint_rect[3], inpaint_rect[0] : inpaint_rect[2]
                ]
                img_view[:] = undo_img
                mask_view[:] = undo_mask
            self.canvas.updateLayers()

        self._replay_text(after=False)
