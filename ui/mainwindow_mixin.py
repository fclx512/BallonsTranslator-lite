"""
Shared business logic for MainWindow.

Subclasses must provide these hook methods:
  - _hook_set_title(text='', page_name='', save_state='')
  - _hook_update_recent_projects(path)
  - _hook_is_imgtrans_active() -> bool
  - _hook_show_config_page(page_type: str)  # 'trans'|'inpaint'|'ocr'|'detect'
  - _hook_is_translation_page() -> bool
"""

import os
import os.path as osp
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import List, Union

from qtpy.QtCore import QPoint
from qtpy.QtGui import QClipboard, QIcon, QPainter, QTextCursor
from qtpy.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

from utils import shared
from utils.config import (
    FontFormat,
    load_textstyle_from,
    pcfg,
    save_config,
    save_text_styles,
    text_styles,
)
from utils.logger import logger as LOGGER
from utils.message import create_error_dialog, create_info_dialog
from utils.text_processing import full_len, half_len, is_cjk
from utils.textblock import TextAlignment, TextBlock

from .custom_widget import MessageBox
from .drawing_commands import RunBlkTransCommand
from .scenetext_manager import PasteSrcItemsCommand
from .textedit_commands import GlobalRepalceAllCommand


class MainWindowMixin:
    """Mixin containing shared business logic for both window implementations.

    Expects the subclass to have these attributes:
      canvas, imgtrans_proj, st_manager, pageList, bottomBar, drawingPanel,
      textPanel, rightComicTransStackPanel, leftStackWidget, global_search_widget,
      configPanel, module_manager, imsave_thread,
      imgtrans_progress_msgbox, app, backup_blkstyles, _run_imgtrans_wo_textstyle_update,
      save_on_page_changed, opening_dir, page_changing
    """

    # ── Hooks (override in subclass) ─────────────────────────

    def _hook_set_title(self, text='', page_name='', save_state=''):
        """Update window title / title bar with project info."""
        pass

    def _hook_update_recent_projects(self, path: str):
        """Add path to recent projects list in UI."""
        pass

    def _hook_is_imgtrans_active(self) -> bool:
        """Return True if the image translation page is the active view."""
        return True

    def _hook_show_config_page(self, page_type: str):
        """Navigate to a config sub-page. page_type: 'trans'|'inpaint'|'ocr'|'detect'."""
        pass

    def _hook_is_translation_page(self) -> bool:
        """Return True if we're currently on the translation/canvas page."""
        return True

    # ── Project Opening ─────────────────────────────────────

    def OpenProj(self, proj_path: str):
        if osp.isdir(proj_path):
            self.openDir(proj_path)
        else:
            self.openJsonProj(proj_path)

        if pcfg.let_textstyle_indep_flag and not shared.HEADLESS:
            self.load_textstyle_from_proj_dir(from_proj=True)

    def openDir(self, directory: str):
        try:
            self.opening_dir = True
            self.generate_tif_thumbnails(directory)
            self.imgtrans_proj.load(directory)
            self.st_manager.clearSceneTextitems()
            self._hook_set_title(text=osp.basename(directory))
            self.updatePageList()
            self.opening_dir = False
        except Exception as e:
            self.opening_dir = False
            create_error_dialog(e, self.tr('Failed to load project ') + directory)
            return

    def generate_tif_thumbnails(self, directory: str):
        try:
            from utils.io_utils import create_thumbnail, find_tif_files
            tif_files = find_tif_files(directory)
            for tif_file in tif_files:
                tif_path = osp.join(directory, tif_file)
                base_path = Path(tif_path)
                thumb_path = base_path.parent / f"{base_path.stem}_thumb.jpg"
                if not osp.exists(thumb_path):
                    create_thumbnail(tif_path, max_width=1000)
        except Exception as e:
            LOGGER.error(f"Failed to generate TIF thumbnails: {e}")

    def dropOpenDir(self, directory: str):
        if isinstance(directory, str) and osp.exists(directory):
            self._hook_update_recent_projects(directory)
            self.OpenProj(directory)

    def openJsonProj(self, json_path: str):
        try:
            self.opening_dir = True
            self.imgtrans_proj.load_from_json(json_path)
            self.st_manager.clearSceneTextitems()
            self._hook_update_recent_projects(self.imgtrans_proj.proj_path)
            self.updatePageList()
            self._hook_set_title(text=osp.basename(self.imgtrans_proj.proj_path))
            self.opening_dir = False
        except Exception as e:
            self.opening_dir = False
            create_error_dialog(e, self.tr('Failed to load project from') + json_path)

    def load_textstyle_from_proj_dir(self, from_proj=False):
        if from_proj:
            text_style_path = osp.join(self.imgtrans_proj.directory, 'textstyles.json')
        else:
            text_style_path = 'config/textstyles/default.json'
        if osp.exists(text_style_path):
            load_textstyle_from(text_style_path)
            self.textPanel.formatpanel.textstyle_panel.setStyles(text_styles)
        else:
            pcfg.text_styles_path = text_style_path
            save_text_styles()

    def updatePageList(self):
        if self.pageList.count() != 0:
            self.pageList.clear()
        if len(self.imgtrans_proj.pages) >= shared.PAGELIST_THUMBNAIL_MAXNUM:
            item_func = lambda imgname: QListWidgetItem(imgname)
        else:
            item_func = lambda imgname: \
                QListWidgetItem(QIcon(osp.join(self.imgtrans_proj.directory, imgname)), imgname)
        for imgname in self.imgtrans_proj.pages:
            lstitem = item_func(imgname)
            self.pageList.addItem(lstitem)
            if imgname == self.imgtrans_proj.current_img:
                self.pageList.setCurrentItem(lstitem)

    # ── Page Navigation ─────────────────────────────────────

    def pageListCurrentItemChanged(self):
        item = self.pageList.currentItem()
        self.page_changing = True
        if item is not None:
            if self.save_on_page_changed:
                self.conditional_save()
            self.imgtrans_proj.set_current_img(item.text())
            self.canvas.clear_undostack(update_saved_step=True)
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self._hook_set_title(page_name=self.imgtrans_proj.current_img)
            self.module_manager.handle_page_changed()
            self.drawingPanel.handle_page_changed()
        self.page_changing = False

    def conditional_save(self, keep_exist_as_backup=False):
        if self.canvas.projstate_unsaved and not self.opening_dir:
            update_scene_text = save_proj = self.canvas.text_change_unsaved()
            save_rst_only = not self.canvas.draw_change_unsaved()
            if not save_rst_only:
                save_proj = True
            self.saveCurrentPage(update_scene_text, save_proj, restore_interface=True,
                                 save_rst_only=save_rst_only, keep_exist_as_backup=keep_exist_as_backup)

    def saveCurrentPage(self, update_scene_text=True, save_proj=True, restore_interface=False,
                        save_rst_only=False, keep_exist_as_backup=False):
        if not self.imgtrans_proj.img_valid:
            return

        if restore_interface:
            set_canvas_focus = self.canvas.hasFocus()
            sel_textitem = self.canvas.selected_text_items()
            n_sel_textitems = len(sel_textitem)
            editing_textitem = None
            if n_sel_textitems == 1 and sel_textitem[0].isEditing():
                editing_textitem = sel_textitem[0]

        if update_scene_text:
            self.st_manager.updateTextBlkList()

        if self.rightComicTransStackPanel.isHidden():
            self.bottomBar.texteditChecker.click()

        restore_textblock_mode = False
        if pcfg.imgtrans_textblock:
            restore_textblock_mode = True
            self.bottomBar.textblockChecker.click()

        hide_tsc = False
        if self.st_manager.txtblkShapeControl.isVisible():
            hide_tsc = True
            self.st_manager.txtblkShapeControl.hide()

        if not osp.exists(self.imgtrans_proj.result_dir()):
            os.makedirs(self.imgtrans_proj.result_dir())

        if save_proj:
            try:
                self.imgtrans_proj.save(keep_exist_as_backup=keep_exist_as_backup)
                if not save_rst_only:
                    mask_path = self.imgtrans_proj.get_mask_path()
                    mask_array = self.imgtrans_proj.mask_array
                    if mask_array is not None:
                        self.imsave_thread.saveImg(mask_path, mask_array,
                            save_params={'ext': pcfg.intermediate_imgsave_ext})
                    inpainted_path = self.imgtrans_proj.get_inpainted_path()
                    if self.canvas.drawingLayer.drawed():
                        inpainted = self.canvas.base_pixmap.copy()
                        painter = QPainter(inpainted)
                        painter.drawPixmap(0, 0, self.canvas.drawingLayer.get_drawed_pixmap())
                        painter.end()
                    else:
                        inpainted = self.imgtrans_proj.inpainted_array
                    if inpainted is not None:
                        self.imsave_thread.saveImg(inpainted_path, inpainted,
                            save_params={'ext': pcfg.intermediate_imgsave_ext},
                            keep_alpha=self.imgtrans_proj.current_has_alpha())
            except Exception as e:
                LOGGER.error(f"Failed to save project files: {e}")

        try:
            img = self.canvas.render_result_img()
            imsave_path = self.imgtrans_proj.get_result_path(self.imgtrans_proj.current_img)
            imsave_ext = self.imgtrans_proj.get_result_ext(self.imgtrans_proj.current_img)
            self.imsave_thread.saveImg(imsave_path, img, self.imgtrans_proj.current_img,
                save_params={'ext': imsave_ext, 'quality': pcfg.imgsave_quality},
                keep_alpha=self.imgtrans_proj.current_has_alpha())
            self.canvas.setProjSaveState(False)
            self.canvas.update_saved_undostep()
        except Exception as e:
            LOGGER.error(f"Failed to render and save result image: {e}")

        if restore_interface:
            if restore_textblock_mode:
                self.bottomBar.textblockChecker.click()
            if hide_tsc:
                self.st_manager.txtblkShapeControl.show()
            if set_canvas_focus:
                self.canvas.setFocus()
            if n_sel_textitems > 0:
                self.canvas.block_selection_signal = True
                for blk in sel_textitem:
                    blk.setSelected(True)
                self.st_manager.on_incanvas_selection_changed()
                self.canvas.block_selection_signal = False
            if editing_textitem is not None:
                editing_textitem.startEdit()

    def manual_save(self):
        if self._hook_is_imgtrans_active() and self.imgtrans_proj.directory is not None:
            LOGGER.debug('Manually saving...')
            self.saveCurrentPage(update_scene_text=True, save_proj=True,
                                 restore_interface=True, save_rst_only=False)

    # ── Edit Modes ──────────────────────────────────────────

    def onHideCanvas(self):
        self.canvas.clearToolStates()

    def setPaintMode(self):
        if self.bottomBar.paintChecker.isChecked():
            if self.rightComicTransStackPanel.isHidden():
                self.rightComicTransStackPanel.show()
            self.rightComicTransStackPanel.setCurrentIndex(0)
            self.canvas.setPaintMode(True)
            self.bottomBar.originalSlider.show()
            self.bottomBar.textlayerSlider.show()
            self.bottomBar.textblockChecker.hide()
        else:
            self.canvas.setPaintMode(False)
            self.rightComicTransStackPanel.setHidden(True)
        self.st_manager.setTextEditMode(False)

    def setTextEditMode(self):
        if self.bottomBar.texteditChecker.isChecked():
            if self.rightComicTransStackPanel.isHidden():
                self.rightComicTransStackPanel.show()
            self.bottomBar.textblockChecker.show()
            self.rightComicTransStackPanel.setCurrentIndex(1)
            self.st_manager.setTextEditMode(True)
            self.setTextBlockMode()
        else:
            self.bottomBar.textblockChecker.hide()
            self.rightComicTransStackPanel.setHidden(True)
            self.st_manager.setTextEditMode(False)
        self.canvas.setPaintMode(False)

    def setTextBlockMode(self):
        mode = self.bottomBar.textblockChecker.isChecked()
        self.canvas.setTextBlockMode(mode)
        pcfg.imgtrans_textblock = mode
        self.st_manager.showTextblkItemRect(mode)

    # ── Text Panel ──────────────────────────────────────────

    def fold_textarea(self, fold: bool):
        pcfg.fold_textarea = fold
        self.textPanel.textEditList.setFoldTextarea(fold)

    def show_source_text(self, show: bool):
        pcfg.show_source_text = show
        self.textPanel.textEditList.setSourceVisible(show)

    def show_trans_text(self, show: bool):
        pcfg.show_trans_text = show
        self.textPanel.textEditList.setTransVisible(show)

    def on_transpanel_changed(self):
        self.canvas.editor_index = self.rightComicTransStackPanel.currentIndex()
        if not self.canvas.textEditMode() and self.canvas.search_widget.isVisible():
            self.canvas.search_widget.hide()
        self.canvas.updateLayers()

    # ── Canvas Signals ──────────────────────────────────────

    def on_savestate_changed(self, unsaved: bool):
        save_state = self.tr('unsaved') if unsaved else self.tr('saved')
        self._hook_set_title(save_state=save_state)

    def on_textstack_changed(self):
        if not self.page_changing:
            self.global_search_widget.set_document_edited()

    # ── Translation Pipeline ────────────────────────────────

    def translateBlkitemList(self, blkitem_list: List, mode: int) -> bool:
        tgt_img = self.imgtrans_proj.img_array
        if tgt_img is None:
            return False
        tgt_mask = self.imgtrans_proj.mask_array

        if len(blkitem_list) < 1:
            return False

        self.global_search_widget.set_document_edited()

        im_h, im_w = tgt_img.shape[:2]

        blk_list, blk_ids = [], []
        for blkitem in blkitem_list:
            blk: TextBlock = blkitem.blk
            blk._bounding_rect = blkitem.absBoundingRect()
            blk.text = self.st_manager.pairwidget_list[blkitem.idx].e_source.toPlainText()
            blk_ids.append(blkitem.idx)
            blk.set_lines_by_xywh(blk._bounding_rect, angle=-blk.angle,
                                  x_range=[0, im_w - 1], y_range=[0, im_h - 1], adjust_bbox=True)
            blk_list.append(blk)

        self.module_manager.runBlktransPipeline(blk_list, tgt_img, mode, blk_ids, tgt_mask=tgt_mask)
        return True

    def finishTranslatePage(self, page_key):
        if page_key == self.imgtrans_proj.current_img:
            self.st_manager.updateTranslation()

    def on_imgtrans_pipeline_finished(self):
        self.backup_blkstyles.clear()
        self._run_imgtrans_wo_textstyle_update = False
        if pcfg.module.empty_runcache and not shared.HEADLESS:
            self.module_manager.unload_all_models()
        if shared.args.export_translation_txt:
            self.on_export_txt('translation')
        if shared.args.export_source_txt:
            self.on_export_txt('source')
        if shared.HEADLESS:
            self.run_next_dir()

    def postprocess_translations(self, blk_list: List[TextBlock]) -> None:
        src_is_cjk = is_cjk(pcfg.module.translate_source)
        tgt_is_cjk = is_cjk(pcfg.module.translate_target)
        if tgt_is_cjk:
            for blk in blk_list:
                if src_is_cjk:
                    blk.translation = full_len(blk.translation)
                else:
                    blk.translation = half_len(blk.translation)
                    blk.translation = re.sub(r'([?.!"])\s+', r'\1', blk.translation)
        else:
            for blk in blk_list:
                if blk.vertical:
                    blk.alignment = TextAlignment.Center
                blk.translation = half_len(blk.translation)
                blk.vertical = False

        for blk in blk_list:
            if pcfg.let_uppercase_flag:
                blk.translation = blk.translation.upper()

    def on_pagtrans_finished(self, page_index: int):
        from .misc import set_html_family
        blk_list = self.imgtrans_proj.get_blklist_byidx(page_index)
        ffmt_list = None
        if len(self.backup_blkstyles) == self.imgtrans_proj.num_pages and \
                len(self.backup_blkstyles[page_index]) == len(blk_list):
            ffmt_list: List[FontFormat] = self.backup_blkstyles[page_index]

        self.postprocess_translations(blk_list)

        override_fnt_size = pcfg.let_fntsize_flag == 1
        override_fnt_stroke = pcfg.let_fntstroke_flag == 1
        override_fnt_color = pcfg.let_fntcolor_flag == 1
        override_fnt_scolor = pcfg.let_fnt_scolor_flag == 1
        override_alignment = pcfg.let_alignment_flag == 1
        override_effect = pcfg.let_fnteffect_flag == 1
        override_writing_mode = pcfg.let_writing_mode_flag == 1
        override_font_family = pcfg.let_family_flag == 1
        gf = self.textPanel.formatpanel.global_format

        inpaint_only = pcfg.module.enable_inpaint
        inpaint_only = inpaint_only and not (
            pcfg.module.enable_detect or pcfg.module.enable_ocr or pcfg.module.enable_translate)

        if not inpaint_only:
            for ii, blk in enumerate(blk_list):
                if self._run_imgtrans_wo_textstyle_update and ffmt_list is not None:
                    blk.fontformat.merge(ffmt_list[ii])
                else:
                    if override_fnt_size or blk.font_size < 0:
                        blk.font_size = gf.font_size
                    elif blk._detected_font_size > 0 and not pcfg.module.enable_detect:
                        blk.font_size = blk._detected_font_size
                    if override_fnt_stroke:
                        blk.stroke_width = gf.stroke_width
                    elif pcfg.module.enable_ocr:
                        blk.recalulate_stroke_width()
                    if override_fnt_color:
                        blk.set_font_colors(fg_colors=gf.frgb)
                    if override_fnt_scolor:
                        blk.set_font_colors(bg_colors=gf.srgb)
                    if override_alignment:
                        blk.alignment = gf.alignment
                    elif pcfg.module.enable_detect and not blk.src_is_vertical:
                        blk.recalulate_alignment()
                    if override_effect:
                        blk.opacity = gf.opacity
                        blk.shadow_color = gf.shadow_color
                        blk.shadow_radius = gf.shadow_radius
                        blk.shadow_strength = gf.shadow_strength
                        blk.shadow_offset = gf.shadow_offset
                    if override_writing_mode:
                        blk.vertical = gf.vertical
                    if override_font_family or blk.font_family is None:
                        blk.font_family = gf.font_family
                        if blk.rich_text:
                            blk.rich_text = set_html_family(blk.rich_text, gf.font_family)

                    blk.line_spacing = gf.line_spacing
                    blk.letter_spacing = gf.letter_spacing
                    blk.italic = gf.italic
                    blk.bold = gf.bold
                    blk.underline = gf.underline
                    sw = blk.stroke_width
                    if sw > 0 and pcfg.module.enable_ocr and pcfg.module.enable_detect \
                            and not override_fnt_size:
                        blk.font_size = blk.font_size / (1 + sw)

            self.st_manager.auto_textlayout_flag = pcfg.let_autolayout_flag and \
                (pcfg.module.enable_detect or pcfg.module.enable_translate)

        if page_index != self.pageList.currentIndex().row():
            self.pageList.setCurrentRow(page_index)
        else:
            self.imgtrans_proj.set_current_img_byidx(page_index)
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()

        if not pcfg.module.enable_detect and pcfg.module.enable_translate:
            for blkitem in self.st_manager.textblk_item_list:
                blkitem.squeezeBoundingRect()

        if page_index + 1 == self.imgtrans_proj.num_pages:
            self.st_manager.auto_textlayout_flag = False

        self.imgtrans_proj.save()
        self.saveCurrentPage(False, False)

    def on_run_blktrans(self, mode: int):
        blkitem_list = self.canvas.selected_text_items()
        self.translateBlkitemList(blkitem_list, mode)

    def on_blktrans_finished(self, mode: int, blk_ids: List[int]):
        if len(blk_ids) < 1:
            return
        blkitem_list = [self.st_manager.textblk_item_list[idx] for idx in blk_ids]
        pairw_list = []
        for blk in blkitem_list:
            pairw_list.append(self.st_manager.pairwidget_list[blk.idx])
        self.canvas.push_undo_command(
            RunBlkTransCommand(self.canvas, blkitem_list, pairw_list, mode))

    def on_imgtrans_progressbox_showed(self):
        msg_size = self.module_manager.progress_msgbox.size()
        size = self.size()
        p = self.mapToGlobal(QPoint(size.width() - msg_size.width(),
                                    size.height() - msg_size.height()))
        self.module_manager.progress_msgbox.move(p)

    def on_transpagebtn_pressed(self, run_target: bool):
        page_key = self.imgtrans_proj.current_img
        if page_key is None:
            return
        blkitem_list = self.st_manager.textblk_item_list
        if len(blkitem_list) < 1:
            return
        self.translateBlkitemList(blkitem_list, -1)

    # ── Run / Batch ─────────────────────────────────────────

    def run_imgtrans(self):
        num_pages = self.imgtrans_proj.num_pages
        if num_pages == 0:
            return

        from qtpy.QtWidgets import (
            QCheckBox,
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )

        from ui.custom_widget import RangeSlider

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr('Run'))
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)

        range_frame = QFrame()
        range_frame.setFrameShape(QFrame.Shape.StyledPanel)
        range_layout = QVBoxLayout(range_frame)

        slider = RangeSlider(0, num_pages - 1)
        range_layout.addWidget(slider)

        range_info = QLabel()
        range_layout.addWidget(range_info)

        def update_range_info():
            lo = slider.low() + 1
            hi = slider.high() + 1
            range_info.setText(self.tr('Page %1 ~ Page %2 (%3 pages)')
                               .replace('%1', str(lo)).replace('%2', str(hi))
                               .replace('%3', str(hi - lo + 1)))
        slider.rangeChanged.connect(lambda a, b: update_range_info())

        all_pages_cb = QCheckBox(self.tr('All Pages'))
        all_pages_cb.toggled.connect(lambda checked: (
            slider.set_range(0, num_pages - 1),
            slider.setEnabled(not checked),
            update_range_info()
        ))
        all_pages_cb.setChecked(True)
        range_layout.addWidget(all_pages_cb)

        layout.addWidget(range_frame)
        update_range_info()

        btn_layout = QHBoxLayout()
        run_btn = QPushButton(self.tr('Run'))
        cancel_btn = QPushButton(self.tr('Cancel'))
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        run_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)

        if pcfg.module.all_stages_disabled():
            run_btn.setEnabled(False)

        if dialog.exec_() != QDialog.DialogCode.Accepted:
            return

        page_filter = None
        if not all_pages_cb.isChecked():
            page_filter = []
            for i in range(slider.low(), slider.high() + 1):
                page_filter.append(self.imgtrans_proj.idx2pagename(i))

        self.on_run_imgtrans(page_filter=page_filter)

    def run_imgtrans_wo_textstyle_update(self):
        self._run_imgtrans_wo_textstyle_update = True
        self.run_imgtrans()

    def on_run_imgtrans(self, page_filter=None):
        self.backup_blkstyles.clear()

        if self.bottomBar.textblockChecker.isChecked():
            self.bottomBar.textblockChecker.click()

        all_disabled = pcfg.module.all_stages_disabled()

        pages_to_process = []

        if page_filter is not None:
            pages_to_process = list(page_filter)
            for page_name in self.imgtrans_proj.pages:
                self.imgtrans_proj.set_page_progress(page_name, 0)
        else:
            for page_name in self.imgtrans_proj.pages:
                self.imgtrans_proj.set_page_progress(page_name, 0)

        if pcfg.module.enable_detect:
            for page in self.imgtrans_proj.pages:
                if not pcfg.module.keep_exist_textlines:
                    if not pages_to_process:
                        self.imgtrans_proj.pages[page].clear()
        else:
            self.st_manager.updateTextBlkList()
            textblk: TextBlock = None
            for page_name, blklist in self.imgtrans_proj.pages.items():
                if pages_to_process and page_name not in pages_to_process:
                    continue
                ffmt_list = []
                self.backup_blkstyles.append(ffmt_list)
                for textblk in blklist:
                    if not pcfg.module.enable_detect:
                        ffmt_list.append(textblk.fontformat.deepcopy())
                    if pcfg.module.enable_ocr:
                        textblk.text = []
                        textblk.set_font_colors((0, 0, 0), (0, 0, 0))
                    if pcfg.module.enable_translate or \
                            (all_disabled and not self._run_imgtrans_wo_textstyle_update) or \
                            pcfg.module.enable_ocr:
                        textblk.rich_text = ''
                    textblk.vertical = textblk.src_is_vertical

        self.module_manager.runImgtransPipeline(pages_to_process if pages_to_process else None)

    def run_batch(self, exec_dirs: Union[List, str], **kwargs):
        if not isinstance(exec_dirs, List):
            exec_dirs = exec_dirs.split(',')
        valid_dirs = []
        for d in exec_dirs:
            if osp.exists(d):
                valid_dirs.append(d)
            else:
                LOGGER.warning(f'target directory {d} does not exist.')
        self.exec_dirs = valid_dirs
        self.exec_pages = kwargs.get('pages', '').strip() if kwargs.get('pages') else ''
        self.run_next_dir()

    def run_next_dir(self):
        from tqdm import tqdm
        if len(self.exec_dirs) == 0:
            while self.imsave_thread.isRunning():
                time.sleep(0.1)
            LOGGER.info('finished translating all dirs, quit app...')
            self.app.quit()
            return
        d = self.exec_dirs.pop(0)
        LOGGER.info(f'translating {d} ...')
        self.openDir(d)

        page_filter = None
        if self.exec_pages:
            try:
                from utils.io_utils import page_names_from_range
                page_filter = page_names_from_range(self.imgtrans_proj, self.exec_pages)
            except ValueError as e:
                LOGGER.error(f'Invalid --pages argument: {e}')
                self.app.quit()
                return

        shared.pbar = {}
        npages = len(page_filter) if page_filter else len(self.imgtrans_proj.pages)
        if npages > 0:
            if pcfg.module.enable_detect:
                shared.pbar['detect'] = tqdm(range(npages), desc="Text Detection")
            if pcfg.module.enable_ocr:
                shared.pbar['ocr'] = tqdm(range(npages), desc="OCR")
            if pcfg.module.enable_translate:
                shared.pbar['translate'] = tqdm(range(npages), desc="Translation")
            if pcfg.module.enable_inpaint:
                shared.pbar['inpaint'] = tqdm(range(npages), desc="Inpaint")
        self.on_run_imgtrans(page_filter=page_filter)

    # ── Export / Import ─────────────────────────────────────

    def on_export_txt(self, dump_target, suffix='.txt'):
        try:
            self.imgtrans_proj.dump_txt(dump_target=dump_target, suffix=suffix)
            create_info_dialog(self.tr('Text file exported to ') +
                               self.imgtrans_proj.dump_txt_path(dump_target, suffix))
        except Exception as e:
            create_error_dialog(e, self.tr('Failed to export as TEXT file'))

    def on_import_trans_txt(self):
        try:
            selected_file = ''
            dialog = QFileDialog()
            selected_file = str(dialog.getOpenFileUrl(
                self.parent(), self.tr('Import *.md/*.txt'),
                filter="*.txt *.md *.TXT *.MD")[0].toLocalFile())
            if not osp.exists(selected_file):
                return

            all_matched, match_rst = self.imgtrans_proj.load_translation_from_txt(selected_file)
            matched_pages = match_rst['matched_pages']

            if self.imgtrans_proj.current_img in matched_pages:
                self.canvas.clear_undostack(update_saved_step=True)
                self.st_manager.updateSceneTextitems()

            if all_matched:
                msg = self.tr('Translation imported and matched successfully.')
            else:
                msg = self.tr(
                    'Imported txt file not fully matched with current project, '
                    'please make sure source txt file structured like results from '
                    '\"export TXT\"')
                if len(match_rst['missing_pages']) > 0:
                    msg += '\n' + self.tr('Missing pages: ') + '\n'
                    msg += '\n'.join(match_rst['missing_pages'])
                if len(match_rst['unexpected_pages']) > 0:
                    msg += '\n' + self.tr('Unexpected pages: ') + '\n'
                    msg += '\n'.join(match_rst['unexpected_pages'])
                if len(match_rst['unmatched_pages']) > 0:
                    msg += '\n' + self.tr('Unmatched pages: ') + '\n'
                    msg += '\n'.join(match_rst['unmatched_pages'])
                msg = msg.strip()

            for pagename in matched_pages:
                pass

            create_info_dialog(msg)

        except Exception as e:
            create_error_dialog(e, self.tr('Failed to import translation from ') + selected_file)

    # ── Search ──────────────────────────────────────────────

    def on_req_update_pagetext(self):
        if self.canvas.text_change_unsaved():
            self.st_manager.updateTextBlkList()

    def on_req_move_page(self, page_name: str, force_save=False):
        ori_save = self.save_on_page_changed
        self.save_on_page_changed = False
        current_img = self.imgtrans_proj.current_img
        if current_img == page_name and not force_save:
            return
        if current_img not in self.global_search_widget.page_set:
            if self.canvas.projstate_unsaved:
                self.saveCurrentPage()
        else:
            self.saveCurrentPage(save_rst_only=True)
        self.pageList.setCurrentRow(self.imgtrans_proj.pagename2idx(page_name))
        self.save_on_page_changed = ori_save

    def on_search_result_item_clicked(self, pagename: str, blk_idx: int, is_src: bool,
                                      start: int, end: int):
        idx = self.imgtrans_proj.pagename2idx(pagename)
        self.pageList.setCurrentRow(idx)
        pw = self.st_manager.pairwidget_list[blk_idx]
        edit = pw.e_source if is_src else pw.e_trans
        edit.setFocus()
        edit.ensure_scene_visible.emit()
        cursor = QTextCursor(edit.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)

    def on_global_replace_finished(self):
        rt = self.global_search_widget.replace_thread
        self.canvas.push_text_command(
            GlobalRepalceAllCommand(rt.sceneitem_list, rt.background_list,
                                    rt.target_text, self.imgtrans_proj))
        rt.sceneitem_list = None
        rt.background_list = None

    # ── Config Page Navigation ──────────────────────────────

    def to_trans_config(self):
        self._hook_show_config_page('trans')
        self.configPanel.focusOnTranslator()

    def to_inpaint_config(self):
        self._hook_show_config_page('inpaint')
        self.configPanel.focusOnInpaint()

    def to_ocr_config(self):
        self._hook_show_config_page('ocr')
        self.configPanel.focusOnOCR()

    def to_detect_config(self):
        self._hook_show_config_page('detect')
        self.configPanel.focusOnDetect()

    # ── Module Selector Handlers ────────────────────────────

    def on_finish_setdetector(self):
        module_manager = self.module_manager
        if module_manager.textdetector is not None:
            name = module_manager.textdetector.name
            pcfg.module.textdetector = name
            self.configPanel.detect_config_panel.setDetector(name)
            self.bottomBar.textdet_selector.setSelectedValue(name)
            LOGGER.info('Text detector set to {}'.format(name))

    def on_finish_setocr(self):
        module_manager = self.module_manager
        if module_manager.ocr is not None:
            name = module_manager.ocr.name
            pcfg.module.ocr = name
            self.configPanel.ocr_config_panel.setOCR(name)
            self.bottomBar.ocr_selector.setSelectedValue(name)
            LOGGER.info('OCR set to {}'.format(name))

    def on_finish_setinpainter(self):
        module_manager = self.module_manager
        if module_manager.inpainter is not None:
            name = module_manager.inpainter.name
            pcfg.module.inpainter = name
            self.configPanel.inpaint_config_panel.setInpainter(name)
            self.bottomBar.inpaint_selector.setSelectedValue(name)
            LOGGER.info('Inpainter set to {}'.format(name))

    def on_finish_settranslator(self):
        module_manager = self.module_manager
        translator = module_manager.translator
        if translator is not None:
            name = translator.name
            pcfg.module.translator = name
            self.bottomBar.trans_selector.finishSetTranslator(translator)
            self.configPanel.trans_config_panel.finishSetTranslator(translator)
            LOGGER.info('Translator set to {}'.format(name))
        else:
            LOGGER.error('invalid translator')

    def on_enable_module(self, idx, checked):
        if idx == 0:
            pcfg.module.enable_detect = checked
            self.bottomBar.textdet_selector.setVisible(checked)
        elif idx == 1:
            pcfg.module.enable_ocr = checked
            self.bottomBar.ocr_selector.setVisible(checked)
        elif idx == 2:
            pcfg.module.enable_translate = checked
            self.bottomBar.trans_selector.setVisible(checked)
        elif idx == 3:
            pcfg.module.enable_inpaint = checked
            self.bottomBar.inpaint_selector.setVisible(checked)
        pcfg.module.update_finish_code()

    def on_textdet_changed(self):
        from modules import GET_VALID_TEXTDETECTORS
        module = self.bottomBar.textdet_selector.selector.currentText()
        tgt_selector = self.configPanel.detect_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_TEXTDETECTORS():
            tgt_selector.setCurrentText(module)

    def on_ocr_changed(self):
        from modules import GET_VALID_OCR
        module = self.bottomBar.ocr_selector.selector.currentText()
        tgt_selector = self.configPanel.ocr_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_OCR():
            tgt_selector.setCurrentText(module)

    def on_trans_changed(self):
        from modules import GET_VALID_TRANSLATORS
        module = self.bottomBar.trans_selector.selector.currentText()
        tgt_selector = self.configPanel.trans_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_TRANSLATORS():
            tgt_selector.setCurrentText(module)

    def on_trans_src_changed(self):
        sender = self.sender()
        text = sender.currentText()
        translator = self.module_manager.translator
        if translator is not None:
            translator.set_source(text)
        pcfg.module.translate_source = text
        combobox = self.configPanel.trans_config_panel.source_combobox
        if sender != combobox:
            combobox.blockSignals(True)
            combobox.setCurrentText(text)
            combobox.blockSignals(False)
        combobox = self.bottomBar.trans_selector.src_selector
        if sender != combobox:
            combobox.blockSignals(True)
            combobox.setCurrentText(text)
            combobox.blockSignals(False)

    def on_trans_tgt_changed(self):
        sender = self.sender()
        text = sender.currentText()
        translator = self.module_manager.translator
        if translator is not None:
            translator.set_target(text)
        pcfg.module.translate_target = text
        combobox = self.configPanel.trans_config_panel.target_combobox
        if sender != combobox:
            combobox.blockSignals(True)
            combobox.setCurrentText(text)
            combobox.blockSignals(False)
        combobox = self.bottomBar.trans_selector.tgt_selector
        if sender != combobox:
            combobox.blockSignals(True)
            combobox.setCurrentText(text)
            combobox.blockSignals(False)

    def on_inpaint_changed(self):
        from modules import GET_VALID_INPAINTERS
        module = self.bottomBar.inpaint_selector.selector.currentText()
        tgt_selector = self.configPanel.inpaint_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_INPAINTERS():
            tgt_selector.setCurrentText(module)

    # ── Text Styles ─────────────────────────────────────────

    def import_tstyles(self):
        ddir = osp.dirname(pcfg.text_styles_path)
        p = QFileDialog.getOpenFileName(self, self.tr("Import Text Styles"), ddir, None, "(.json)")
        if not isinstance(p, str):
            p = p[0]
        if p == '':
            return
        try:
            load_textstyle_from(p, raise_exception=True)
            save_config()
            self.textPanel.formatpanel.textstyle_panel.setStyles(text_styles)
        except Exception as e:
            create_error_dialog(e, self.tr('Failed to load from {p}').replace('{p}', p))

    def export_tstyles(self):
        ddir = osp.dirname(pcfg.text_styles_path)
        savep = QFileDialog.getSaveFileName(self, self.tr("Save Text Styles"), ddir, None,
                                            "(.json)")
        if not isinstance(savep, str):
            savep = savep[0]
        if savep == '':
            return
        suffix = Path(savep).suffix
        if suffix != '.json':
            if suffix == '':
                savep = savep + '.json'
            else:
                savep = savep.replace(suffix, '.json')
        oldp = pcfg.text_styles_path
        try:
            pcfg.text_styles_path = savep
            save_text_styles(raise_exception=True)
            save_config()
        except Exception as e:
            create_error_dialog(e, self.tr('Failed save to {savep}').replace('{savep}', savep))
            pcfg.text_styles_path = oldp

    # ── Dialogs & Errors ────────────────────────────────────

    def on_create_errdialog(self, error_msg: str, detail_traceback: str = '',
                            exception_type: str = ''):
        try:
            if exception_type != '':
                shared.showed_exception.add(exception_type)
            err = QMessageBox()
            err.setText(error_msg)
            err.setDetailedText(detail_traceback)
            err.exec()
            if exception_type != '':
                shared.showed_exception.remove(exception_type)
        except:
            if exception_type in shared.showed_exception:
                shared.showed_exception.remove(exception_type)
            LOGGER.error('Failed to create error dialog')
            LOGGER.error(traceback.format_exc())

    def on_create_infodialog(self, info_dict: dict):
        QMessageBox.StandardButton.NoButton
        dialog = MessageBox(**info_dict)
        dialog.show()

    # ── Copy / Paste ────────────────────────────────────────

    def on_copy_src(self):
        blks = self.canvas.selected_text_items()
        if len(blks) == 0:
            return
        src_list = [self.st_manager.pairwidget_list[blk.idx].e_source.toPlainText()
                    .strip().replace('\n', ' ') for blk in blks]
        src_txt = '\n'.join(src_list)
        self.st_manager.app_clipborad.setText(src_txt, QClipboard.Mode.Clipboard)

    def on_paste_src(self):
        blks = self.canvas.selected_text_items()
        if len(blks) == 0:
            return

        src_widget_list = [self.st_manager.pairwidget_list[blk.idx].e_source for blk in blks]
        text_list = self.st_manager.app_clipborad.text().split('\n')

        n_paragraph = min(len(src_widget_list), len(text_list))
        if n_paragraph < 1:
            return

        src_widget_list = src_widget_list[:n_paragraph]
        text_list = text_list[:n_paragraph]

        self.canvas.push_undo_command(PasteSrcItemsCommand(src_widget_list, text_list))

    # ── Misc ────────────────────────────────────────────────

    def on_reveal_file(self):
        current_img_path = self.imgtrans_proj.current_img_path()
        if sys.platform == 'win32':
            p = "\"" + str(Path(current_img_path)) + "\""
            subprocess.Popen("explorer.exe /select," + p, shell=True)

    def refresh_font_list_exclusion(self):
        familybox = self.textPanel.formatpanel.familybox
        current_family = familybox.currentText()
        filtered = shared.get_filtered_font_list(pcfg.excluded_fonts)
        familybox.update_font_list(filtered)
        if current_family in filtered:
            familybox.setCurrentText(current_family)
        elif filtered:
            familybox.setCurrentIndex(0)

    def save_config(self):
        save_config()

    def retranslateUI(self):
        msg = QMessageBox()
        msg.setText(self.tr('Restart to apply changes? \n'))
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        ret = msg.exec_()
        if ret == QMessageBox.StandardButton.Yes:
            self.save_config()
            self.restart_signal.emit()
