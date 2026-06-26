import os
import os.path as osp
import re
import subprocess
import sys
import time
import traceback
from functools import partial
from pathlib import Path
from typing import List, Optional, Union

from qtpy.QtCore import QEvent, QEventLoop, QPoint, QPointF, QSize, Qt, Signal

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand
from qtpy.QtGui import (
    QClipboard,
    QCloseEvent,
    QContextMenuEvent,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QShortcut,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from tqdm import tqdm

from modules import (
    GET_VALID_INPAINTERS,
    GET_VALID_OCR,
    GET_VALID_TEXTDETECTORS,
    GET_VALID_TRANSLATORS,
)
from utils import shared
from utils.config import (
    FontFormat,
    ProgramConfig,
    load_textstyle_from,
    pcfg,
    save_config,
    save_text_styles,
    text_styles,
)
from utils.logger import logger as LOGGER
from utils.message import create_error_dialog, create_info_dialog
from utils.proj_imgtrans import ProjImgTrans
from utils.text_processing import full_len, half_len, is_cjk
from utils.textblock import TextAlignment, TextBlock

from . import shared_widget as SW
from .canvas import Canvas
from .configpanel import ConfigPanel
from .custom_widget import (
    FrameLessMessageBox,
    ImgtransProgressMessageBox,
    MessageBox,
    ViewWidget,
    Widget,
)
from .drawing_commands import RunBlkTransCommand
from .drawingpanel import DrawingPanel
from .framelesswindow import FramelessMoveResize, FramelessWindow
from .global_search_widget import GlobalSearchWidget
from .io_thread import ImgSaveThread
from .mainwindowbars import BottomBar, LeftBar, TitleBar
from .misc import QKEY, parse_stylesheet, set_html_family
from .module_manager import ModuleManager
from .overlay_modal import OverlayModal
from .overlay_slide import OverlaySlider
from .psd_export_dialog import PsdExportDialog
from .scenetext_manager import PasteSrcItemsCommand, SceneTextManager, TextPanel
from .textedit_area import SourceTextEdit, TransTextEdit
from .textedit_commands import GlobalRepalceAllCommand
from .update_checker import AboutDialog


class PageListView(QListWidget):
    reveal_file = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setIconSize(
            QSize(shared.PAGELIST_THUMBNAIL_SIZE, shared.PAGELIST_THUMBNAIL_SIZE)
        )

    def contextMenuEvent(self, e: QContextMenuEvent):
        menu = QMenu()
        reveal_act = menu.addAction(self.tr("Reveal in File Explorer"))
        rst = menu.exec_(e.globalPos())

        if rst == reveal_act:
            self.reveal_file.emit()

        return super().contextMenuEvent(e)


class _PointAlignCommand(QUndoCommand):
    """Undo command for batch point alignment across pages.

    Stores old/new ``_bounding_rect`` for every affected TextBlock,
    and for current-page items also old/new scene positions so the
    visual state stays in sync.
    """

    def __init__(self, canvas, data_changes, item_changes=None):
        super().__init__("Advanced Alignment")
        self.canvas = canvas
        # (TextBlock, [old_x, old_y, old_w, old_h], [new_x, new_y, new_w, new_h])
        self.data_changes = list(data_changes)
        # (TextBlkItem, old_QPointF, new_QPointF)  —  current-page items
        self.item_changes = list(item_changes or [])

    def _apply_data(self, changes):
        for blk, old_br, new_br in changes:
            blk._bounding_rect = list(new_br)

    def _apply_items(self, changes):
        for item, old_pos, new_pos in changes:
            item.oldPos = item.pos()
            item.setPos(new_pos)

    def redo(self):
        self._apply_data(self.data_changes)
        self._apply_items(self.item_changes)

    def undo(self):
        rev_data = [(blk, new_br, old_br) for blk, old_br, new_br in self.data_changes]
        rev_items = [(item, new_pos, old_pos) for item, old_pos, new_pos in self.item_changes]
        self._apply_data(rev_data)
        self._apply_items(rev_items)


mainwindow_cls = Widget if shared.HEADLESS else FramelessWindow


class MainWindow(mainwindow_cls):
    imgtrans_proj: ProjImgTrans = ProjImgTrans()
    save_on_page_changed = True
    opening_dir = False
    page_changing = False
    translator = None

    restart_signal = Signal()
    create_errdialog = Signal(str, str, str)
    create_infodialog = Signal(dict)

    def __init__(
        self, app: QApplication, config: ProgramConfig, open_dir="", **exec_args
    ) -> None:
        super().__init__()

        shared.create_errdialog_in_mainthread = self.create_errdialog.emit
        self.create_errdialog.connect(self.on_create_errdialog)
        shared.create_infodialog_in_mainthread = self.create_infodialog.emit
        self.create_infodialog.connect(self.on_create_infodialog)
        shared.register_view_widget = self.register_view_widget

        self.app = app
        self.backup_blkstyles = []
        self._run_imgtrans_wo_textstyle_update = False

        self.setupThread()
        self.setupUi()
        self.setupConfig()
        self.setupShortcuts()
        self.setupRegisterWidget()
        # self.showMaximized()
        FramelessMoveResize.toggleMaxState(self)
        self.setAcceptDrops(True)

        if open_dir != "" and osp.exists(open_dir):
            self.OpenProj(open_dir)
        elif pcfg.open_recent_on_startup:
            if len(self.leftBar.recent_proj_list) > 0:
                proj_dir = self.leftBar.recent_proj_list[0]
                if osp.exists(proj_dir):
                    self.OpenProj(proj_dir)

        if shared.HEADLESS:
            self.run_batch(**exec_args)

        # Windows: apply font & set titlebar

    def setStyleSheet(self, styleSheet: str) -> None:
        self.imgtrans_progress_msgbox.setStyleSheet(styleSheet)
        return super().setStyleSheet(styleSheet)

    def setupThread(self):
        self.imsave_thread = ImgSaveThread()

    def resetStyleSheet(self, reverse_icon: bool = False):
        theme = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
        self.setStyleSheet(parse_stylesheet(theme, reverse_icon))

    def setupUi(self):
        screen_size = QGuiApplication.primaryScreen().geometry().size()
        self.setMinimumWidth(screen_size.width() // 2)

        self.centralStackWidget = QStackedWidget(self)

        self.configPanel = ConfigPanel(self.centralStackWidget)

        self.leftBar = LeftBar(self)
        self.leftBar.showPageListLabel.clicked.connect(self.pageLabelStateChanged)
        self.leftBar.imgTransChecked.connect(self.setupImgTransUI)
        self.leftBar.configChecked.connect(self.setupConfigUI)
        self.leftBar.globalSearchChecker.clicked.connect(self.on_set_gsearch_widget)
        self.leftBar.open_dir.connect(self.OpenProj)
        self.leftBar.open_json_proj.connect(self.openJsonProj)
        self.leftBar.save_proj.connect(self.manual_save)
        self.leftBar.export_src_txt.connect(
            lambda: self.on_export_txt(dump_target="source")
        )
        self.leftBar.export_trans_txt.connect(
            lambda: self.on_export_txt(dump_target="translation")
        )
        self.leftBar.import_trans_txt.connect(self.on_import_trans_txt)

        self.pageList = PageListView()
        self.pageList.reveal_file.connect(self.on_reveal_file)
        self.pageList.setHidden(True)
        self.pageList.currentItemChanged.connect(self.pageListCurrentItemChanged)

        self.leftStackWidget = QStackedWidget(self.centralStackWidget)
        self.leftStackWidget.addWidget(self.pageList)
        self.leftStackWidget.setVisible(False)

        self.global_search_widget = GlobalSearchWidget(self.centralStackWidget)
        self.global_search_widget.setVisible(False)
        self.global_search_widget.req_update_pagetext.connect(
            self.on_req_update_pagetext
        )
        self.global_search_widget.req_move_page.connect(self.on_req_move_page)
        self.imsave_thread.img_writed.connect(self.global_search_widget.on_img_writed)
        self.global_search_widget.search_tree.result_item_clicked.connect(
            self.on_search_result_item_clicked
        )

        self.titleBar = TitleBar(self)
        self.titleBar.closebtn_clicked.connect(self.on_closebtn_clicked)
        self.titleBar.display_lang_changed.connect(self.on_display_lang_changed)
        self.bottomBar = BottomBar(self)
        self.bottomBar.textedit_checkchanged.connect(self.setTextEditMode)
        self.bottomBar.paintmode_checkchanged.connect(self.setPaintMode)
        self.bottomBar.textblock_checkchanged.connect(self.setTextBlockMode)

        mainHLayout = QHBoxLayout()
        mainHLayout.addWidget(self.leftBar)
        mainHLayout.addWidget(self.centralStackWidget)
        mainHLayout.setContentsMargins(0, 0, 0, 0)
        mainHLayout.setSpacing(0)

        # set up canvas
        SW.canvas = self.canvas = Canvas()
        self.canvas.imgtrans_proj = self.imgtrans_proj
        self.canvas.gv.hide_canvas.connect(self.onHideCanvas)
        self.canvas.proj_savestate_changed.connect(self.on_savestate_changed)
        self.canvas.textstack_changed.connect(self.on_textstack_changed)
        self.canvas.run_blktrans.connect(self.on_run_blktrans)
        self.canvas.drop_open_folder.connect(self.dropOpenDir)
        self.canvas.copy_src_signal.connect(self.on_copy_src)
        self.canvas.paste_src_signal.connect(self.on_paste_src)

        self.bottomBar.originalSlider.valueChanged.connect(
            self.canvas.setOriginalTransparencyBySlider
        )
        self.bottomBar.textlayerSlider.valueChanged.connect(
            self.canvas.setTextLayerTransparencyBySlider
        )

        self.drawingPanel = DrawingPanel(
            self.canvas, self.configPanel.inpaint_config_panel
        )
        self.textPanel = TextPanel(self.app)
        self.textPanel.formatpanel.foldTextBtn.checkStateChanged.connect(
            self.fold_textarea
        )
        self.textPanel.formatpanel.sourceBtn.checkStateChanged.connect(
            self.show_source_text
        )
        self.textPanel.formatpanel.transBtn.checkStateChanged.connect(
            self.show_trans_text
        )
        self.textPanel.formatpanel.textstyle_panel.export_style.connect(
            self.export_tstyles
        )
        self.textPanel.formatpanel.textstyle_panel.import_style.connect(
            self.import_tstyles
        )

        SW.st_manager = self.st_manager = SceneTextManager(
            self.app, self, self.canvas, self.textPanel
        )
        self.st_manager.new_textblk.connect(self.canvas.search_widget.on_new_textblk)
        self.canvas.search_widget.pairwidget_list = self.st_manager.pairwidget_list
        self.canvas.search_widget.textblk_item_list = self.st_manager.textblk_item_list
        self.canvas.search_widget.replace_one.connect(
            self.st_manager.on_page_replace_one
        )
        self.canvas.search_widget.replace_all.connect(
            self.st_manager.on_page_replace_all
        )

        # comic trans pannel
        self.rightComicTransStackPanel = QStackedWidget(self)
        self.rightComicTransStackPanel.addWidget(self.drawingPanel)
        self.rightComicTransStackPanel.addWidget(self.textPanel)
        self.rightComicTransStackPanel.currentChanged.connect(
            self.on_transpanel_changed
        )

        # Right panel container: canvas | trans stack (right)
        self._rightPanelContainer = QWidget()
        right_layout = QHBoxLayout(self._rightPanelContainer)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Middle: canvas (stretches to fill remaining space)
        right_layout.addWidget(self.canvas.gv, 1)

        # Right: trans stack panel (fixed width)
        right_layout.addWidget(self.rightComicTransStackPanel)
        self.rightComicTransStackPanel.setFixedWidth(360)

        self.centralStackWidget.addWidget(self._rightPanelContainer)

        # Config panel as floating modal overlay (centered over the canvas region;
        # scrim only dims centralStackWidget, left bar / bottom bar / title bar
        # stay interactive).
        self.configPanel.setParent(self.centralStackWidget)
        self.configPanel.setVisible(False)
        self._configModal = OverlayModal(
            self.configPanel,
            self.centralStackWidget,
            duration=350,
        )
        self.configPanel._modal_ref = self._configModal
        self._configModal.on_before_show(lambda: self.configPanel.setFocus())
        self._configModal.on_after_hide(self._on_config_hidden)

        # Font Style Manager — opened as a dialog from Tools menu
        self._styleMgrDialog: Optional[QDialog] = None

        # Quick Symbol dialog
        self._quickSymbolDialog: Optional[QDialog] = None

        # Search widget as floating overlay (slides in from left)
        self._searchSlide = OverlaySlider(
            self.global_search_widget,
            direction="left",
            width=lambda: self.global_search_widget.sizeHint().width(),
        )
        self._searchSlide.on_before_show(lambda: self.global_search_widget.setFocus())
        self._searchSlide.on_after_hide(self._on_search_hidden)

        # Page list overlay slides in from left
        self._pageListSlide = OverlaySlider(
            self.leftStackWidget,
            direction="left",
            width=self.PAGE_LIST_WIDTH,
        )
        self._pageListSlide.on_before_show(
            lambda: self.leftStackWidget.setCurrentWidget(self.pageList)
        )
        self._pageListSlide.on_after_hide(self._on_page_list_hidden)

        mainVBoxLayout = QVBoxLayout(self)
        mainVBoxLayout.addWidget(self.titleBar)
        mainVBoxLayout.addLayout(mainHLayout)
        mainVBoxLayout.addWidget(self.bottomBar)
        margin = mainVBoxLayout.contentsMargins()
        self.main_margin = margin
        mainVBoxLayout.setContentsMargins(0, 0, 0, 0)
        mainVBoxLayout.setSpacing(0)

        self.mainvlayout = mainVBoxLayout
        self.imgtrans_progress_msgbox = ImgtransProgressMessageBox()
        self.resetStyleSheet()

    def on_finish_setdetector(self):
        module_manager = self.module_manager
        if module_manager.textdetector is not None:
            name = module_manager.textdetector.name
            pcfg.module.textdetector = name
            self.configPanel.detect_config_panel.setDetector(name)
            self.bottomBar.textdet_selector.setSelectedValue(name)
            LOGGER.info("Text detector set to {}".format(name))

    def on_finish_setocr(self):
        module_manager = self.module_manager
        if module_manager.ocr is not None:
            name = module_manager.ocr.name
            pcfg.module.ocr = name
            self.configPanel.ocr_config_panel.setOCR(name)
            self.bottomBar.ocr_selector.setSelectedValue(name)
            LOGGER.info("OCR set to {}".format(name))

    def on_finish_setinpainter(self):
        module_manager = self.module_manager
        if module_manager.inpainter is not None:
            name = module_manager.inpainter.name
            pcfg.module.inpainter = name
            self.configPanel.inpaint_config_panel.setInpainter(name)
            self.bottomBar.inpaint_selector.setSelectedValue(name)
            LOGGER.info("Inpainter set to {}".format(name))

    def on_finish_settranslator(self):
        module_manager = self.module_manager
        translator = module_manager.translator
        if translator is not None:
            name = translator.name
            pcfg.module.translator = name
            self.bottomBar.trans_selector.finishSetTranslator(translator)
            self.configPanel.trans_config_panel.finishSetTranslator(translator)
            LOGGER.info("Translator set to {}".format(name))
        else:
            LOGGER.error("invalid translator")

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

    def setupConfig(self):

        self.bottomBar.originalSlider.setValue(int(pcfg.original_transparency * 100))
        trans_items = list(GET_VALID_TRANSLATORS())
        none_items = [x for x in trans_items if x.startswith("none") or x.startswith("None")]
        other_items = [x for x in trans_items if x not in none_items]
        self.bottomBar.trans_selector.selector.addItems(none_items)
        if other_items:
            self.bottomBar.trans_selector.selector.insertSeparator(len(none_items))
            self.bottomBar.trans_selector.selector.addItems(other_items)
        self.bottomBar.trans_selector.selector.setCurrentText(pcfg.module.translator)
        ocr_items = list(GET_VALID_OCR())
        none_items = [x for x in ocr_items if x.startswith("none") or x.startswith("None")]
        other_items = [x for x in ocr_items if x not in none_items]
        self.bottomBar.ocr_selector.selector.addItems(none_items)
        if other_items:
            self.bottomBar.ocr_selector.selector.insertSeparator(len(none_items))
            self.bottomBar.ocr_selector.selector.addItems(other_items)
        self.bottomBar.ocr_selector.setSelectedValue(pcfg.module.ocr)
        td_items = list(GET_VALID_TEXTDETECTORS())
        none_items = [x for x in td_items if x.startswith("none") or x.startswith("None")]
        other_items = [x for x in td_items if x not in none_items]
        self.bottomBar.textdet_selector.selector.addItems(none_items)
        if other_items:
            self.bottomBar.textdet_selector.selector.insertSeparator(len(none_items))
            self.bottomBar.textdet_selector.selector.addItems(other_items)
        self.bottomBar.textdet_selector.setSelectedValue(pcfg.module.textdetector)
        self.bottomBar.textdet_selector.selector.currentTextChanged.connect(
            self.on_textdet_changed
        )
        self.bottomBar.inpaint_selector.selector.addItems(GET_VALID_INPAINTERS())
        self.bottomBar.inpaint_selector.selector.currentTextChanged.connect(
            self.on_inpaint_changed
        )
        self.bottomBar.trans_selector.cfg_clicked.connect(self.to_trans_config)
        self.bottomBar.trans_selector.selector.currentTextChanged.connect(
            self.on_trans_changed
        )
        self.bottomBar.trans_selector.tgt_selector.currentTextChanged.connect(
            self.on_trans_tgt_changed
        )
        self.bottomBar.trans_selector.src_selector.currentTextChanged.connect(
            self.on_trans_src_changed
        )
        self.bottomBar.textdet_selector.cfg_clicked.connect(self.to_detect_config)
        self.bottomBar.inpaint_selector.cfg_clicked.connect(self.to_inpaint_config)
        self.bottomBar.ocr_selector.cfg_clicked.connect(self.to_ocr_config)
        self.bottomBar.ocr_selector.selector.currentTextChanged.connect(
            self.on_ocr_changed
        )
        self.bottomBar.textdet_selector.setVisible(pcfg.module.enable_detect)
        self.bottomBar.ocr_selector.setVisible(pcfg.module.enable_ocr)
        self.bottomBar.trans_selector.setVisible(pcfg.module.enable_translate)
        self.bottomBar.inpaint_selector.setVisible(pcfg.module.enable_inpaint)

        self.configPanel.trans_config_panel.target_combobox.currentTextChanged.connect(
            self.on_trans_tgt_changed
        )
        self.configPanel.trans_config_panel.source_combobox.currentTextChanged.connect(
            self.on_trans_src_changed
        )

        self.drawingPanel.maskTransperancySlider.setValue(
            int(pcfg.mask_transparency * 100)
        )
        self.leftBar.initRecentProjMenu(pcfg.recent_proj_list)
        self.leftBar.showPageListLabel.setChecked(pcfg.show_page_list)
        self.updatePageList()
        self.leftBar.save_config.connect(self.save_config)
        self.leftBar.imgTransChecker.setChecked(True)
        self.st_manager.formatpanel.global_format = pcfg.global_fontformat
        self.st_manager.formatpanel.set_active_format(pcfg.global_fontformat)

        self.rightComicTransStackPanel.setHidden(True)
        self.st_manager.setTextEditMode(False)
        self.st_manager.formatpanel.foldTextBtn.setChecked(pcfg.fold_textarea)
        self.st_manager.formatpanel.transBtn.setCheckState(pcfg.show_trans_text)
        self.st_manager.formatpanel.sourceBtn.setCheckState(pcfg.show_source_text)
        self.fold_textarea(pcfg.fold_textarea)
        self.show_trans_text(pcfg.show_trans_text)
        self.show_source_text(pcfg.show_source_text)

        self.module_manager = module_manager = ModuleManager(self.imgtrans_proj)
        module_manager.finish_translate_page.connect(self.finishTranslatePage)
        module_manager.imgtrans_pipeline_finished.connect(
            self.on_imgtrans_pipeline_finished
        )
        module_manager.page_trans_finished.connect(self.on_pagtrans_finished)
        module_manager.setupThread(self.configPanel, self.imgtrans_progress_msgbox)
        module_manager.progress_msgbox.showed.connect(
            self.on_imgtrans_progressbox_showed
        )
        module_manager.blktrans_pipeline_finished.connect(self.on_blktrans_finished)
        module_manager.imgtrans_thread.post_process_mask = (
            self.drawingPanel.rectPanel.post_process_mask
        )
        module_manager.inpaint_thread.finish_set_module.connect(
            self.on_finish_setinpainter
        )
        module_manager.translate_thread.finish_set_module.connect(
            self.on_finish_settranslator
        )
        module_manager.textdetect_thread.finish_set_module.connect(
            self.on_finish_setdetector
        )
        module_manager.ocr_thread.finish_set_module.connect(self.on_finish_setocr)
        module_manager.setTextDetector()
        module_manager.setOCR()
        module_manager.setTranslator()
        module_manager.setInpainter()

        self.leftBar.run_imgtrans_clicked.connect(self.run_imgtrans)

        self.titleBar.darkModeAction.setChecked(pcfg.darkmode)

        self.drawingPanel.set_config(pcfg.drawpanel)
        self.drawingPanel.initDLModule(module_manager)

        self.global_search_widget.imgtrans_proj = self.imgtrans_proj
        self.global_search_widget.setupReplaceThread(
            self.st_manager.pairwidget_list, self.st_manager.textblk_item_list
        )
        self.global_search_widget.replace_thread.finished.connect(
            self.on_global_replace_finished
        )

        self.configPanel.setupConfig()
        self.configPanel.save_config.connect(self.save_config)
        self.configPanel.reload_textstyle.connect(self.load_textstyle_from_proj_dir)
        self.configPanel.font_exclusion_changed.connect(
            self.refresh_font_list_exclusion
        )
        self.configPanel.shortcuts_changed.connect(self.refreshShortcuts)
        self.configPanel.presets_changed.connect(self._on_presets_changed)
        # 初始化字体列表（系统字体枚举）
        shared.init_font_list()
        # 使用过滤后的字体列表（排除用户已隐藏的字体）
        familybox = self.textPanel.formatpanel.familybox
        filtered = shared.get_filtered_font_list(pcfg.excluded_fonts)
        if familybox.count() == 0 and filtered:
            familybox.update_font_list(filtered)

        textblock_mode = pcfg.imgtrans_textblock
        if pcfg.imgtrans_textedit:
            if textblock_mode:
                self.bottomBar.textblockChecker.setChecked(True)
            self.bottomBar.texteditChecker.click()
        elif pcfg.imgtrans_paintmode:
            self.bottomBar.paintChecker.click()

        self.textPanel.formatpanel.textstyle_panel.initStyles(text_styles)

        self.canvas.search_widget.whole_word_toggle.setChecked(pcfg.fsearch_whole_word)
        self.canvas.search_widget.case_sensitive_toggle.setChecked(pcfg.fsearch_case)
        self.canvas.search_widget.regex_toggle.setChecked(pcfg.fsearch_regex)
        self.canvas.search_widget.range_combobox.setCurrentIndex(pcfg.fsearch_range)
        self.global_search_widget.whole_word_toggle.setChecked(pcfg.gsearch_whole_word)
        self.global_search_widget.case_sensitive_toggle.setChecked(pcfg.gsearch_case)
        self.global_search_widget.regex_toggle.setChecked(pcfg.gsearch_regex)
        self.global_search_widget.range_combobox.setCurrentIndex(pcfg.gsearch_range)

        if self.rightComicTransStackPanel.isHidden():
            self.setPaintMode()

    def refresh_font_list_exclusion(self):
        """Re-apply font exclusion filter to the font combobox."""
        familybox = self.textPanel.formatpanel.familybox
        current_family = familybox.currentText()
        filtered = shared.get_filtered_font_list(pcfg.excluded_fonts)
        familybox.update_font_list(filtered)
        if current_family in filtered:
            familybox.setCurrentText(current_family)
        elif filtered:
            familybox.setCurrentIndex(0)

    def _on_presets_changed(self):
        self.textPanel.formatpanel.reload_presets()
        if hasattr(self.textPanel.formatpanel, "textadvancedfmt_panel"):
            self.textPanel.formatpanel.textadvancedfmt_panel.reload_presets()

    def setupImgTransUI(self):
        self._hideConfigOverlay()
        show = self.leftBar.needleftStackWidget()
        is_visible = self.leftStackWidget.isVisible()
        if show and not is_visible:
            if self.leftBar.globalSearchChecker.isChecked():
                self.leftBar.globalSearchChecker.setChecked(False)
                self._hideSearchOverlay()
            # During window init (window not shown yet), set state without
            # animating so the overlay doesn't pop in unexpectedly.
            if self.isVisible():
                self._showPageListOverlay()
            else:
                pw = self.leftStackWidget.parentWidget()
                self.leftStackWidget.setGeometry(
                    0, 0, self.PAGE_LIST_WIDTH, pw.height()
                )
                self.leftStackWidget.setCurrentWidget(self.pageList)
                self.leftStackWidget.raise_()
                self.leftStackWidget.show()
        elif not show and is_visible:
            self._hidePageListOverlay()

    def setupConfigUI(self):
        self._showConfigOverlay()

    def _is_canvas_mode(self) -> bool:
        """True when canvas is active (config overlay not visible)."""
        return not (hasattr(self, "configPanel") and self.configPanel.isVisible())

    def _showConfigOverlay(self):
        self._configModal.show()

    def _hideConfigOverlay(self):
        self._configModal.hide()

    def _on_config_hidden(self):
        if self.leftBar.configChecker.isChecked():
            self.leftBar.configChecker.setChecked(False)

    def on_open_fontstyle_manager(self):
        """Open Font Style Manager as a standalone dialog."""
        if self._styleMgrDialog is not None and self._styleMgrDialog.isVisible():
            self._styleMgrDialog.raise_()
            self._styleMgrDialog.activateWindow()
            return

        from qtpy.QtWidgets import QDialog, QVBoxLayout

        from .fontstyle_manager import FontStyleManager

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Font Style Manager"))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(self._on_stylemgr_dialog_destroyed)

        fsm = FontStyleManager(dialog)
        fsm.set_project(self.imgtrans_proj, self.st_manager)
        fsm.refresh()
        fsm.navigate_to_block.connect(self._on_stylemgr_navigate)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(fsm)

        dialog.resize(800, 540)
        dialog.setMinimumSize(640, 400)
        self._styleMgrDialog = dialog
        dialog.show()

    def _on_stylemgr_dialog_destroyed(self):
        self._styleMgrDialog = None

    def _on_stylemgr_navigate(self, pagename: str, block_idx: int):
        """Switch to *pagename* and select *block_idx* on the canvas."""
        proj = self.imgtrans_proj
        if pagename not in proj.pages:
            return
        # Switch page if needed
        if proj.current_img != pagename:
            if self.save_on_page_changed:
                self.conditional_save()
            proj.set_current_img(pagename)
            self.canvas.clear_undostack(update_saved_step=True)
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self.titleBar.setTitleContent(page_name=pagename)
            self.module_manager.handle_page_changed()
            self.drawingPanel.handle_page_changed()
        # Select the block
        try:
            tbi = self.st_manager.textblk_item_list[block_idx]
            # Clear existing selection
            for item in self.canvas.selected_text_items():
                item.setSelected(False)
            tbi.setSelected(True)
            self.canvas.gv.centerOn(tbi)
        except (IndexError, AttributeError):
            pass

    def _showSearchOverlay(self):
        self._searchSlide.show()

    def _hideSearchOverlay(self):
        self._searchSlide.hide()

    def _on_search_hidden(self):
        if self.leftBar.globalSearchChecker.isChecked():
            self.leftBar.globalSearchChecker.setChecked(False)

    PAGE_LIST_WIDTH = 250

    def _showPageListOverlay(self):
        self._pageListSlide.show()

    def _hidePageListOverlay(self):
        self._pageListSlide.hide()

    def _on_page_list_hidden(self):
        if self.leftBar.showPageListLabel.isChecked():
            self.leftBar.showPageListLabel.setChecked(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._configModal.resize()
        self._searchSlide.resize()
        self._pageListSlide.resize()

    def set_display_lang(self, lang: str):
        self.retranslateUI()

    def OpenProj(self, proj_path: str):
        if osp.isdir(proj_path):
            self.openDir(proj_path)
        else:
            self.openJsonProj(proj_path)

        if pcfg.let_textstyle_indep_flag and not shared.HEADLESS:
            self.load_textstyle_from_proj_dir(from_proj=True)

    def load_textstyle_from_proj_dir(self, from_proj=False, only_custom: bool = False):
        if from_proj:
            if self.imgtrans_proj.directory is None:
                return
            text_style_path = osp.join(self.imgtrans_proj.directory, "textstyles.json")
        else:
            text_style_path = "config/textstyles/default.json"
        if osp.exists(text_style_path):
            load_textstyle_from(text_style_path)
            self.textPanel.formatpanel.textstyle_panel.setStyles(text_styles)
        else:
            pcfg.text_styles_path = text_style_path
            save_text_styles()

        if only_custom:
            font_list = shared.CUSTOM_FONT_FAMILIES
        else:
            font_list = shared.ALL_FONT_FAMILIES

        familybox = self.textPanel.formatpanel.familybox
        current_family = familybox.currentText()
        familybox.update_font_list(font_list)

        # 恢复选中状态并触发 Style 更新
        if current_family in font_list:
            familybox.setCurrentText(current_family)
        elif len(font_list) > 0:
            familybox.setCurrentIndex(0)

    def openDir(self, directory: str):
        try:
            self.opening_dir = True

            # Show indeterminate progress dialog for project loading
            progress = QProgressDialog(self.tr("Loading project..."), "", 0, 0, self)
            progress.setWindowTitle(self.tr("Loading"))
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            progress.show()
            QApplication.processEvents()

            # Generate TIF thumbnails (may take time for large TIF directories)
            self.generate_tif_thumbnails(directory, progress)

            # Load the project JSON data
            progress.setLabelText(self.tr("Reading project data..."))
            progress.setMinimumDuration(0)
            QApplication.processEvents()
            self.imgtrans_proj.load(directory)

            # UI update phase
            progress.setLabelText(self.tr("Updating interface..."))
            progress.setMinimumDuration(0)
            QApplication.processEvents()
            self.st_manager.clearSceneTextitems()
            self.titleBar.setTitleContent(osp.basename(directory))
            self.updatePageList()
            self.opening_dir = False
            progress.close()
        except Exception as e:
            self.opening_dir = False
            create_error_dialog(e, self.tr("Failed to load project ") + directory)
            return

    def generate_tif_thumbnails(
        self, directory: str, progress: "QProgressDialog | None" = None
    ):
        """
        为目录中的TIF文件生成预览图，并确保只加载预览图
        """
        try:
            from utils.io_utils import create_thumbnail, find_tif_files

            # 查找目录中的所有TIF文件
            tif_files = find_tif_files(directory)
            if not tif_files:
                return

            # 统计需要生成预览图的TIF文件
            pending = []
            for tif_file in tif_files:
                tif_path = osp.join(directory, tif_file)
                base_path = Path(tif_path)
                thumb_path = base_path.parent / f"{base_path.stem}_thumb.jpg"
                if not osp.exists(thumb_path):
                    pending.append(tif_path)

            if not pending:
                return

            # 切换到确定进度模式
            if progress is not None:
                progress.setRange(0, len(pending))
                progress.setLabelText(self.tr("Generating TIF thumbnails..."))
                progress.setMinimumDuration(0)

            # 逐个生成预览图
            for i, tif_path in enumerate(pending):
                create_thumbnail(tif_path, max_width=1000)
                if progress is not None:
                    progress.setValue(i + 1)
                    QApplication.processEvents()

        except Exception as e:
            LOGGER.error(f"Failed to generate TIF thumbnails: {e}")

    def dropOpenDir(self, directory: str):
        if isinstance(directory, str) and osp.exists(directory):
            self.leftBar.updateRecentProjList(directory)
            self.OpenProj(directory)

    def openJsonProj(self, json_path: str):
        try:
            self.opening_dir = True
            self.imgtrans_proj.load_from_json(json_path)
            self.st_manager.clearSceneTextitems()
            self.leftBar.updateRecentProjList(self.imgtrans_proj.proj_path)
            self.updatePageList()
            self.titleBar.setTitleContent(osp.basename(self.imgtrans_proj.proj_path))
            self.opening_dir = False
        except Exception as e:
            self.opening_dir = False
            create_error_dialog(e, self.tr("Failed to load project from") + json_path)

    def updatePageList(self):
        if self.pageList.count() != 0:
            self.pageList.clear()

        def item_func(imgname):
            if len(self.imgtrans_proj.pages) >= shared.PAGELIST_THUMBNAIL_MAXNUM:
                return QListWidgetItem(imgname)
            else:
                return QListWidgetItem(
                    QIcon(osp.join(self.imgtrans_proj.directory, imgname)), imgname
                )

        for imgname in self.imgtrans_proj.pages:
            lstitem = item_func(imgname)
            self.pageList.addItem(lstitem)
            if imgname == self.imgtrans_proj.current_img:
                self.pageList.setCurrentItem(lstitem)

    def pageLabelStateChanged(self):
        setup = self.leftBar.showPageListLabel.isChecked()
        if setup:
            if self.leftBar.globalSearchChecker.isChecked():
                self.leftBar.globalSearchChecker.setChecked(False)
                self._hideSearchOverlay()
            self._showPageListOverlay()
        else:
            self._hidePageListOverlay()
        pcfg.show_page_list = setup

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.imgtrans_proj.is_empty:
            self.conditional_save(keep_exist_as_backup=True)
        while True:
            if not self.imsave_thread.isRunning():
                break
            time.sleep(0.1)
        self.st_manager.hovering_transwidget = None
        self.st_manager.blockSignals(True)
        self.canvas.prepareClose()
        self.save_config()
        return super().closeEvent(event)

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMaximized:
                self.titleBar.maxBtn.setChecked(True)
        elif event.type() == QEvent.Type.ActivationChange:
            self.canvas.on_activation_changed()

        super().changeEvent(event)

    def retranslateUI(self):
        msg = QMessageBox()
        msg.setText(self.tr("Restart to apply changes? \n"))
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        ret = msg.exec_()
        if ret == QMessageBox.StandardButton.Yes:
            self.save_config()
            self.restart_signal.emit()

    def save_config(self):
        save_config()

    def onHideCanvas(self):
        self.canvas.clearToolStates()

    def conditional_save(self, keep_exist_as_backup=False):
        if self.canvas.projstate_unsaved and not self.opening_dir:
            update_scene_text = save_proj = self.canvas.text_change_unsaved()
            save_rst_only = not self.canvas.draw_change_unsaved()
            if not save_rst_only:
                save_proj = True

            self.saveCurrentPage(
                update_scene_text,
                save_proj,
                restore_interface=True,
                save_rst_only=save_rst_only,
                keep_exist_as_backup=keep_exist_as_backup,
            )

    def pageListCurrentItemChanged(self):
        item = self.pageList.currentItem()
        self.page_changing = True
        if item is not None:
            if self.save_on_page_changed:
                self.conditional_save()
            self.imgtrans_proj.set_current_img(item.text())
            self.canvas.clear_undostack(update_saved_step=True)
            self.canvas._fit_to_window = self.opening_dir or pcfg.fit_window_on_page_switch
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self.titleBar.setTitleContent(page_name=self.imgtrans_proj.current_img)
            self.module_manager.handle_page_changed()
            self.drawingPanel.handle_page_changed()

        self.page_changing = False

    def setupShortcuts(self):
        self.shortcut_registry = {}

        self.titleBar.textedit_trigger.connect(self.shortcutTextedit)
        self.titleBar.drawboard_trigger.connect(self.shortcutDrawboard)
        self.titleBar.redo_trigger.connect(self.on_redo)
        self.titleBar.undo_trigger.connect(self.on_undo)
        self.titleBar.page_search_trigger.connect(self.on_page_search)
        self.titleBar.global_search_trigger.connect(self.on_global_search)
        self.titleBar.darkmode_trigger.connect(self.on_darkmode_triggered)
        self.titleBar.merge_tool_trigger.connect(self.on_open_merge_tool)
        self.titleBar.stylemgr_trigger.connect(self.on_open_fontstyle_manager)
        self.titleBar.help_about_triggered.connect(self.show_about_dialog)
        self.titleBar.psd_export_triggered.connect(self.on_export_psd)
        self.titleBar.quick_symbol_trigger.connect(self.on_open_quick_symbol)
        self.titleBar.adv_align_trigger.connect(self.on_open_advanced_align)
        self.titleBar.normalize_breaks_triggered.connect(
            self.on_open_normalize_breaks_dialog
        )

        self._install_shortcuts()

    def _get_shortcut_keys(self, action_id, defaults):
        """Resolve shortcut keys: user config overrides defaults."""
        from utils.config import pcfg

        if action_id in pcfg.shortcuts:
            keys = pcfg.shortcuts[action_id]
            if not isinstance(keys, list):
                keys = [keys] if keys else []
            return keys
        return list(defaults)

    def _make_shortcuts(self, action_id, defaults, slot):
        lst = []
        for k in self._get_shortcut_keys(action_id, defaults):
            sc = QShortcut(QKeySequence(k), self)
            sc.activated.connect(slot)
            lst.append(sc)
        return lst

    def refreshShortcuts(self):
        """Rebuild all QShortcut objects from current pcfg.shortcuts (live update after editing)."""
        for lst in self.shortcut_registry.values():
            for sc in lst:
                sc.deleteLater()
        self.shortcut_registry.clear()
        self._install_shortcuts()

    def _install_shortcuts(self):
        """Create all QShortcut objects from current config (used at init + refresh)."""

        self.shortcut_registry["prev_page"] = self._make_shortcuts(
            "prev_page", ["A"], self.shortcutBefore
        )
        self.shortcut_registry["prev_page_alt"] = self._make_shortcuts(
            "prev_page_alt", ["PgUp"], self.shortcutBefore
        )
        self.shortcut_registry["next_page"] = self._make_shortcuts(
            "next_page", ["D"], self.shortcutNext
        )
        self.shortcut_registry["next_page_alt"] = self._make_shortcuts(
            "next_page_alt", ["PgDown"], self.shortcutNext
        )
        self.shortcut_registry["textblock_mode"] = self._make_shortcuts(
            "textblock_mode", ["W"], self.shortcutTextblock
        )
        self.shortcut_registry["zoom_in"] = self._make_shortcuts(
            "zoom_in", ["Ctrl++"], self.canvas.gv.scale_up_signal
        )
        self.shortcut_registry["zoom_out"] = self._make_shortcuts(
            "zoom_out", ["Ctrl+-"], self.canvas.gv.scale_down_signal
        )
        self.shortcut_registry["delete_blks_alt"] = self._make_shortcuts(
            "delete_blks_alt", ["Ctrl+D"], self.shortcutCtrlD
        )
        self.shortcut_registry["space_inpaint"] = self._make_shortcuts(
            "space_inpaint", ["Space"], self.shortcutSpace
        )
        self.shortcut_registry["select_all"] = self._make_shortcuts(
            "select_all", ["Ctrl+A"], self.shortcutSelectAll
        )
        self.shortcut_registry["preview"] = self._make_shortcuts(
            "preview", ["Tab"], self.shortcutPreview
        )
        self.shortcut_registry["escape"] = self._make_shortcuts(
            "escape", ["Escape"], self.shortcutEscape
        )
        self.shortcut_registry["bold"] = self._make_shortcuts(
            "bold", ["Ctrl+B"], self.shortcutBold
        )
        self.shortcut_registry["italic"] = self._make_shortcuts(
            "italic", ["Ctrl+I"], self.shortcutItalic
        )
        self.shortcut_registry["underline"] = self._make_shortcuts(
            "underline", ["Ctrl+U"], self.shortcutUnderline
        )
        self.shortcut_registry["delete_blks"] = self._make_shortcuts(
            "delete_blks", ["Del"], self.shortcutDelete
        )

        # Wire up actions that were previously only available via hardcoded TitleBar QAction shortcuts
        self.shortcut_registry["textedit_mode"] = self._make_shortcuts(
            "textedit_mode", ["T"], self.shortcutTextedit
        )
        self.shortcut_registry["drawboard_mode"] = self._make_shortcuts(
            "drawboard_mode", ["P"], self.shortcutDrawboard
        )
        self.shortcut_registry["undo"] = self._make_shortcuts(
            "undo", ["Ctrl+Z"], self.on_undo
        )
        self.shortcut_registry["redo"] = self._make_shortcuts(
            "redo", ["Ctrl+Y"], self.on_redo
        )
        self.shortcut_registry["page_search"] = self._make_shortcuts(
            "page_search", ["Ctrl+F"], self.on_page_search
        )
        self.shortcut_registry["global_search"] = self._make_shortcuts(
            "global_search", ["Ctrl+G"], self.on_global_search
        )
        self.shortcut_registry["merge_tool"] = self._make_shortcuts(
            "merge_tool", ["Ctrl+Shift+M"], self.on_open_merge_tool
        )
        self.shortcut_registry["quick_symbol"] = self._make_shortcuts(
            "quick_symbol", [], self.on_open_quick_symbol
        )
        self.shortcut_registry["advanced_align"] = self._make_shortcuts(
            "advanced_align", [], self.on_open_advanced_align
        )
        self.shortcut_registry["toggle_original_opacity"] = self._make_shortcuts(
            "toggle_original_opacity", [], self.shortcutToggleOriginalOpacity
        )

        drawpanel_info = {
            "hand": "hand_tool",
            "rect": "rect_tool",
            "inpaint": "inpaint_tool",
            "pen": "pen_tool",
        }
        drawpanel_defs = {
            "hand_tool": ["H"],
            "rect_tool": ["R"],
            "inpaint_tool": ["J"],
            "pen_tool": ["B"],
        }
        for tool_name, action_id in drawpanel_info.items():
            keys = self._get_shortcut_keys(action_id, drawpanel_defs[action_id])
            lst = []
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.activated.connect(
                    partial(self.drawingPanel.shortcutSetCurrentToolByName, tool_name)
                )
                lst.append(sc)
            if keys:
                self.drawingPanel.setShortcutTip(tool_name, keys[0])
            self.shortcut_registry[action_id] = lst

    def shortcutNext(self):
        sender: QShortcut = self.sender()
        if isinstance(sender, QShortcut):
            if sender.key() == QKEY.Key_D:
                if self.canvas.editing_textblkitem is not None:
                    return
        if self._is_canvas_mode():
            focus_widget = self.app.focusWidget()
            if self.st_manager.is_editting():
                self.st_manager.on_switch_textitem(1)
            elif isinstance(focus_widget, (SourceTextEdit, TransTextEdit)):
                self.st_manager.on_switch_textitem(
                    1, current_editing_widget=focus_widget
                )
            else:
                index = self.pageList.currentIndex()
                page_count = self.pageList.count()
                if index.isValid():
                    row = index.row()
                    row = (row + 1) % page_count
                    self.pageList.setCurrentRow(row)

    def shortcutBefore(self):
        sender: QShortcut = self.sender()
        if isinstance(sender, QShortcut):
            if sender.key() == QKEY.Key_A:
                if self.canvas.editing_textblkitem is not None:
                    return
        if self._is_canvas_mode():
            focus_widget = self.app.focusWidget()
            if self.st_manager.is_editting():
                self.st_manager.on_switch_textitem(-1)
            elif isinstance(focus_widget, (SourceTextEdit, TransTextEdit)):
                self.st_manager.on_switch_textitem(
                    -1, current_editing_widget=focus_widget
                )
            else:
                index = self.pageList.currentIndex()
                page_count = self.pageList.count()
                if index.isValid():
                    row = index.row()
                    row = (row - 1 + page_count) % page_count
                    self.pageList.setCurrentRow(row)

    def shortcutTextedit(self):
        if self._is_canvas_mode():
            self.bottomBar.texteditChecker.click()

    def shortcutTextblock(self):
        if self._is_canvas_mode():
            if self.bottomBar.texteditChecker.isChecked():
                self.bottomBar.textblockChecker.click()

    def shortcutDrawboard(self):
        if self._is_canvas_mode():
            self.bottomBar.paintChecker.click()

    def shortcutToggleOriginalOpacity(self):
        if not self._is_canvas_mode():
            return
        preset = pcfg.original_transparency_preset / 100
        current = pcfg.original_transparency
        target = preset if current > preset else 1.0
        self.bottomBar.originalSlider.setValue(int(target * 100))
        self.canvas.setOriginalTransparency(target)

    def shortcutCtrlD(self):
        if self._is_canvas_mode():
            if self.drawingPanel.isVisible():
                if self.drawingPanel.currentTool == self.drawingPanel.rectTool:
                    self.drawingPanel.rectPanel.delete_btn.click()
            elif self.canvas.textEditMode():
                self.canvas.delete_textblks.emit(0)

    def shortcutSelectAll(self):
        if self._is_canvas_mode():
            if self.textPanel.isVisible():
                self.st_manager.set_blkitems_selection(True)

    def shortcutSpace(self):
        if self._is_canvas_mode():
            if self.drawingPanel.isVisible():
                if self.drawingPanel.currentTool == self.drawingPanel.rectTool:
                    self.drawingPanel.rectPanel.inpaint_btn.click()

    def shortcutBold(self):
        if self.textPanel.formatpanel.isVisible():
            self.textPanel.formatpanel.formatBtnGroup.boldBtn.click()

    def shortcutDelete(self):
        if self.canvas.gv.isVisible():
            self.canvas.delete_textblks.emit(1)

    def shortcutItalic(self):
        if self.textPanel.formatpanel.isVisible():
            self.textPanel.formatpanel.formatBtnGroup.italicBtn.click()

    def shortcutUnderline(self):
        if self.textPanel.formatpanel.isVisible():
            self.textPanel.formatpanel.formatBtnGroup.underlineBtn.click()

    def on_redo(self):
        self.canvas.redo()

    def on_undo(self):
        self.canvas.undo()

    def on_page_search(self):
        if self.canvas.gv.isVisible():
            fo = self.app.focusObject()
            sel_text = ""
            tgt_edit = None
            blkitem = self.canvas.editing_textblkitem
            if fo == self.canvas.gv and blkitem is not None:
                sel_text = blkitem.textCursor().selectedText()
                tgt_edit = self.st_manager.pairwidget_list[blkitem.idx].e_trans
            elif isinstance(fo, QTextEdit) or isinstance(fo, QPlainTextEdit):
                sel_text = fo.textCursor().selectedText()
                if isinstance(fo, SourceTextEdit):
                    tgt_edit = fo
            se = self.canvas.search_widget.search_editor
            se.setFocus()
            if sel_text != "":
                se.setPlainText(sel_text)
                cursor = se.textCursor()
                cursor.select(QTextCursor.SelectionType.Document)
                se.setTextCursor(cursor)

            if self.canvas.search_widget.isHidden():
                self.canvas.search_widget.show()
            self.canvas.search_widget.setCurrentEditor(tgt_edit)

    def on_global_search(self):
        if self.canvas.gv.isVisible():
            if not self.leftBar.globalSearchChecker.isChecked():
                self.leftBar.globalSearchChecker.click()
            fo = self.app.focusObject()
            sel_text = ""
            blkitem = self.canvas.editing_textblkitem
            if fo == self.canvas.gv and blkitem is not None:
                sel_text = blkitem.textCursor().selectedText()
            elif isinstance(fo, QTextEdit) or isinstance(fo, QPlainTextEdit):
                sel_text = fo.textCursor().selectedText()
            se = self.global_search_widget.search_editor
            se.setFocus()
            if sel_text != "":
                se.setPlainText(sel_text)
                cursor = se.textCursor()
                cursor.select(QTextCursor.SelectionType.Document)
                se.setTextCursor(cursor)

                self.global_search_widget.commit_search()

    def on_open_merge_tool(self):
        """Open region merge tool dialog"""
        if not hasattr(self, "merge_dialog") or self.merge_dialog is None:
            from .merge_dialog import MergeDialog

            self.merge_dialog = MergeDialog(self)
            self.merge_dialog.run_current_clicked.connect(
                lambda: self.run_merge_task(on_current=True)
            )
            self.merge_dialog.run_all_clicked.connect(
                lambda: self.run_merge_task(on_current=False)
            )

        if self.merge_dialog.isVisible():
            self.merge_dialog.raise_()
            self.merge_dialog.activateWindow()
        else:
            self.merge_dialog.show()

    def on_open_quick_symbol(self):
        """Open the Quick Symbol dialog as a floating always-on-top window."""
        if self._quickSymbolDialog is not None and self._quickSymbolDialog.isVisible():
            self._quickSymbolDialog.raise_()
            self._quickSymbolDialog.activateWindow()
            return

        if self._quickSymbolDialog is None:
            from .quick_symbol_dialog import QuickSymbolDialog

            dlg = QuickSymbolDialog(self)
            dlg.destroyed.connect(self._on_quick_symbol_destroyed)
            self._quickSymbolDialog = dlg

        self._quickSymbolDialog.show()

    def _on_quick_symbol_destroyed(self):
        self._quickSymbolDialog = None

    def on_open_advanced_align(self):
        """Open Advanced Alignment dialog."""
        num_pages = self.imgtrans_proj.num_pages
        if num_pages == 0:
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("No pages in project")
            )
            return

        from .point_align_dialog import PointAlignDialog

        dialog = PointAlignDialog(num_pages, self)
        canvas = self.canvas

        # Use QEventLoop instead of exec_() so hide() during pick
        # doesn't cause exec_() to return Rejected (Qt behavior:
        # hide() on a modal dialog during exec_() returns Rejected).
        _picking = False
        _accepted = False
        loop = QEventLoop()

        def on_pick():
            """Dialog 'Pick' button clicked — enter canvas pick mode."""
            nonlocal _picking
            if _picking:
                return
            _picking = True
            dialog.hide()
            canvas.enter_pick_mode(dialog.alignment_axis())

        def on_position_picked(val: int):
            """Canvas emitted a coordinate — restore dialog after event unwind."""
            nonlocal _picking
            if not _picking:
                return
            _picking = False
            canvas.exit_pick_mode()  # keeps NoDrag — drag restored in on_accepted/on_rejected
            dialog.set_picked_value(val)
            # Defer show() so mouseReleaseEvent can unwind normally
            from qtpy.QtCore import QTimer
            QTimer.singleShot(0, dialog.show)

        def on_accepted():
            nonlocal _accepted
            _accepted = True
            if _picking:
                canvas.exit_pick_mode()
            canvas.restore_drag_mode()
            loop.quit()

        def on_rejected():
            """Dialog cancelled — ensure canvas is clean."""
            if _picking:
                canvas.exit_pick_mode()
            canvas.restore_drag_mode()
            loop.quit()

        dialog.pick_clicked.connect(on_pick)
        canvas.position_picked.connect(on_position_picked)
        dialog.accepted.connect(on_accepted)
        dialog.rejected.connect(on_rejected)

        # Show modeless (not modal) — hide() during pick won't cancel it
        dialog.show()
        loop.exec_()

        if not _accepted:
            return

        target = dialog.target_value()
        axis = dialog.alignment_axis()
        mode = dialog.alignment_mode()
        raw_filter = dialog.page_filter()

        # Resolve page filter
        if raw_filter is None:
            page_filter = None
        else:
            lo, hi = raw_filter
            page_filter = [
                self.imgtrans_proj.idx2pagename(i) for i in range(lo, hi + 1)
            ]

        self.execute_advanced_align(page_filter, target, mode, axis)

    def on_open_normalize_breaks_dialog(self):
        """打开批量整理换行对话框。"""
        if self.imgtrans_proj.num_pages == 0:
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("No pages in project")
            )
            return
        # 刷新当前页 live 文档到 blk.translation，确保读到最新的换行
        self.st_manager.updateTextBlkList()
        from .normalize_breaks_dialog import NormalizeBreaksDialog

        dlg = NormalizeBreaksDialog(
            self.imgtrans_proj, self.st_manager, self
        )
        if dlg.exec() == QDialog.Accepted:
            changes = dlg.get_changes()
            if not changes:
                return
            from .textedit_commands import NormalizeBreaksCommand

            cmd = NormalizeBreaksCommand(self.imgtrans_proj, self.st_manager, changes)
            self.canvas.push_undo_command(cmd)
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.information(
                self,
                self.tr("批量整理换行"),
                self.tr("已整理 {} 块 / 跳过 {} 块（竖排）").format(
                    dlg.processed_count, dlg.skipped_count
                ),
            )

    def execute_advanced_align(self, page_filter, target, mode, axis):
        """Apply point alignment across pages.

        Args:
            page_filter: ``None`` (all pages) or ``List[str]`` of page names.
            target: Target coordinate in scene space (X or Y).
            mode: ``"top"``|``"center"``|``"bottom"`` (Y), or
                  ``"left"``|``"center"``|``"right"`` (X).
            axis: ``"x"`` or ``"y"``.
        """
        proj = self.imgtrans_proj
        canvas = self.canvas
        st_mgr = self.st_manager

        page_names = (
            list(proj.pages.keys()) if page_filter is None else page_filter
        )

        # ── 1. Compute offsets for every non-rotated block ─────
        data_changes = []  # (TextBlock, old_br, new_br, delta)

        for pname in page_names:
            for blk in proj.pages.get(pname, []):
                if blk.angle != 0:
                    continue

                if blk._bounding_rect is not None:
                    x, y, w, h = blk._bounding_rect
                else:
                    x1, y1, x2, y2 = blk.xyxy
                    x, y, w, h = x1, y1, x2 - x1, y2 - y1

                if axis == "y":
                    if mode == "top":
                        delta = target - y
                    elif mode == "center":
                        delta = target - (y + h / 2.0)
                    elif mode == "bottom":
                        delta = target - (y + h)
                    else:
                        continue
                else:  # axis == "x"
                    if mode == "left":
                        delta = target - x
                    elif mode == "center":
                        delta = target - (x + w / 2.0)
                    elif mode == "right":
                        delta = target - (x + w)
                    else:
                        continue

                if abs(delta) < 0.5:
                    continue

                old_br = (
                    blk._bounding_rect[:]
                    if blk._bounding_rect is not None
                    else [x, y, w, h]
                )
                if axis == "y":
                    new_br = [x, y + delta, w, h]
                else:
                    new_br = [x + delta, y, w, h]
                data_changes.append((blk, old_br, new_br, delta))

        if not data_changes:
            create_info_dialog(self.tr("No movable text blocks found"))
            return

        # ── 2. Build current-page item changes ────────────────
        item_changes = []
        if st_mgr is not None and canvas is not None:
            # Use identity (is) comparison — TextBlock is unhashable
            for item in st_mgr.textblk_item_list:
                for blk, old_br, new_br, delta in data_changes:
                    if item.blk is blk:
                        old_pos = item.pos()
                        if axis == "y":
                            new_pos = old_pos + QPointF(0, delta)
                        else:
                            new_pos = old_pos + QPointF(delta, 0)
                        item.oldPos = old_pos
                        item.setPos(new_pos)
                        item_changes.append((item, old_pos, new_pos))
                        break

        # ── 3. Push undo command (applies data + visual) ──────
        # Strip delta from data_changes for the command
        cmd_data = [(blk, old_br, new_br) for blk, old_br, new_br, delta in data_changes]
        cmd = _PointAlignCommand(canvas, cmd_data, item_changes)
        canvas.push_undo_command(cmd)

    def run_merge_task(self, on_current=False):
        """Run region merge task"""
        from qtpy.QtWidgets import QMessageBox

        from utils import merger

        if self.imgtrans_proj.is_empty:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please open a project first")
            )
            return

        config = self.merge_dialog.get_config()

        if on_current:
            # 对当前文件运行 - 直接在内存中操作，不读写文件
            from utils.textblock import TextBlock

            current_img = self.imgtrans_proj.current_img
            if not current_img:
                QMessageBox.warning(
                    self, self.tr("Warning"), self.tr("No current file")
                )
                return

            # 直接从内存获取当前页面的文本框
            if current_img not in self.imgtrans_proj.pages:
                QMessageBox.warning(
                    self, self.tr("Warning"), self.tr("Current page data not found")
                )
                return

            textblocks = self.imgtrans_proj.pages[current_img]
            if not textblocks:
                QMessageBox.warning(
                    self, self.tr("Notice"), self.tr("No text blocks on current page")
                )
                return

            # 将 TextBlock 对象转换为字典格式（merger 需要字典）
            initial_shapes = [blk.to_dict() for blk in textblocks]

            initial_count = len(initial_shapes)
            mode = config.get("MERGE_MODE", "NONE")
            total_merged = 0

            # 在内存中执行合并
            if mode == "VERTICAL":
                final_shapes, count = merger.perform_merge(
                    initial_shapes, "VERTICAL", config
                )
                total_merged += count
            elif mode == "HORIZONTAL":
                final_shapes, count = merger.perform_merge(
                    initial_shapes, "HORIZONTAL", config
                )
                total_merged += count
            elif mode == "VERTICAL_THEN_HORIZONTAL":
                temp, count1 = merger.perform_merge(initial_shapes, "VERTICAL", config)
                final_shapes, count2 = merger.perform_merge(temp, "HORIZONTAL", config)
                total_merged += count1 + count2
            elif mode == "HORIZONTAL_THEN_VERTICAL":
                temp, count1 = merger.perform_merge(
                    initial_shapes, "HORIZONTAL", config
                )
                final_shapes, count2 = merger.perform_merge(temp, "VERTICAL", config)
                total_merged += count1 + count2
            else:
                final_shapes = initial_shapes

            if total_merged > 0:
                # 将字典转回 TextBlock 对象并更新内存
                self.imgtrans_proj.pages[current_img] = [
                    TextBlock(**blk_dict) for blk_dict in final_shapes
                ]
                # 刷新画布
                self.canvas.updateCanvas()
                self.st_manager.updateSceneTextitems()
                final_count = len(final_shapes)
                QMessageBox.information(
                    self,
                    self.tr("Success"),
                    self.tr(
                        "Merge complete: {initial} -> {final} (reduced by {delta})"
                    ).format(
                        initial=initial_count,
                        final=final_count,
                        delta=initial_count - final_count,
                    ),
                )
            else:
                labels = set(s.get("label", "") for s in initial_shapes)
                detail_msg = self.tr("No merge occurred.") + "\n"
                detail_msg += (
                    self.tr("Total text blocks: {count}").format(count=initial_count)
                    + "\n"
                )
                detail_msg += (
                    self.tr("Label types: {labels}").format(
                        labels=", ".join(labels) or self.tr("None")
                    )
                    + "\n\n"
                )
                detail_msg += self.tr("Suggestions:") + "\n"
                detail_msg += (
                    self.tr("1. Try increasing maximum gap (e.g., 100-200)") + "\n"
                )
                detail_msg += (
                    self.tr("2. Lower the minimum overlap ratio (e.g., 50-70%)") + "\n"
                )
                detail_msg += (
                    self.tr("3. Uncheck 'Enable label exclusion (blacklist)'") + "\n"
                )
                detail_msg += self.tr("4. Check if labels are in the blacklist")
                QMessageBox.warning(self, self.tr("Notice"), detail_msg)
        else:
            # 对所有文件运行
            img_list = list(self.imgtrans_proj.pages.keys())
            if not img_list:
                QMessageBox.warning(
                    self, self.tr("Warning"), self.tr("No images in project")
                )
                return

            json_path = self.imgtrans_proj.proj_path
            if not json_path or not osp.exists(json_path):
                QMessageBox.warning(
                    self,
                    self.tr("Warning"),
                    self.tr("Project JSON file not found: {path}").format(
                        path=json_path
                    ),
                )
                return

            # 使用后台线程执行合并
            self.run_merge_all_async(json_path, img_list, config)

    def run_merge_all_async(self, json_path, img_list, config):
        """Run merge async on all files"""
        from .io_thread import MergeThread

        # 创建合并线程（如果不存在）
        if not hasattr(self, "merge_thread"):
            self.merge_thread = MergeThread()
            self.merge_thread.progress_changed.connect(self.on_merge_progress)
            self.merge_thread.merge_finished.connect(self.on_merge_finished)
            self.merge_thread.progress_bar.stop_clicked.connect(self.on_merge_stop)

        # 启动合并
        if self.merge_thread.runMerge(json_path, img_list, config):
            # 显示进度对话框
            self.merge_thread.progress_bar.zero_progress()
            self.merge_thread.progress_bar.show()

    def on_merge_progress(self, current, total):
        """Merge progress update"""
        progress = int(current / total * 100)
        self.merge_thread.progress_bar.updateTaskProgress(
            progress, f" {current}/{total}"
        )

    def on_merge_stop(self):
        """Stop merge"""
        if hasattr(self, "merge_thread"):
            self.merge_thread.requestStop()
            self.merge_thread.progress_bar.hide()

    def on_merge_finished(self, success_count, fail_count):
        """Merge complete"""
        self.merge_thread.progress_bar.hide()

        # 重新加载整个项目
        try:
            json_path = self.imgtrans_proj.proj_path
            current_img = self.imgtrans_proj.current_img
            self.imgtrans_proj.load_from_json(json_path)
            if current_img and current_img in self.imgtrans_proj.pages:
                self.imgtrans_proj.set_current_img(current_img)
                self.canvas.updateCanvas()
                self.st_manager.updateSceneTextitems()
        except Exception:
            pass

        # 显示结果
        total = success_count + fail_count
        QMessageBox.information(
            self,
            self.tr("Done"),
            self.tr("Region merge complete\nSuccess: {s}/{t}\nFailed: {f}/{t}").format(
                s=success_count, f=fail_count, t=total
            ),
        )

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

    def on_search_result_item_clicked(
        self, pagename: str, blk_idx: int, is_src: bool, start: int, end: int
    ):
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

    def shortcutPreview(self):
        if self._is_canvas_mode():
            self.canvas.toggle_preview()

    def shortcutEscape(self):
        if self.canvas.search_widget.isVisible():
            self.canvas.search_widget.hide()
        elif (
            self.canvas.editing_textblkitem is not None
            and self.canvas.editing_textblkitem.isEditing()
        ):
            self.canvas.editing_textblkitem.endEdit()

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

    def manual_save(self):
        if (
            self.leftBar.imgTransChecker.isChecked()
            and self.imgtrans_proj.directory is not None
        ):
            LOGGER.debug("Manually saving...")
            self.saveCurrentPage(
                update_scene_text=True,
                save_proj=True,
                restore_interface=True,
                save_rst_only=False,
            )

    def saveCurrentPage(
        self,
        update_scene_text=True,
        save_proj=True,
        restore_interface=False,
        save_rst_only=False,
        keep_exist_as_backup=False,
    ):

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
                        self.imsave_thread.saveImg(
                            mask_path,
                            mask_array,
                            save_params={"ext": pcfg.intermediate_imgsave_ext},
                        )
                    inpainted_path = self.imgtrans_proj.get_inpainted_path()
                    if self.canvas.drawingLayer.drawed():
                        inpainted = self.canvas.base_pixmap.copy()
                        painter = QPainter(inpainted)
                        painter.drawPixmap(
                            0, 0, self.canvas.drawingLayer.get_drawed_pixmap()
                        )
                        painter.end()
                    else:
                        inpainted = self.imgtrans_proj.inpainted_array
                    if inpainted is not None:
                        self.imsave_thread.saveImg(
                            inpainted_path,
                            inpainted,
                            save_params={"ext": pcfg.intermediate_imgsave_ext},
                            keep_alpha=self.imgtrans_proj.current_has_alpha(),
                        )
            except Exception as e:
                LOGGER.error(f"Failed to save project files: {e}")

        # Render the final result image properly
        try:
            img = self.canvas.render_result_img()
            imsave_path = self.imgtrans_proj.get_result_path(
                self.imgtrans_proj.current_img
            )
            imsave_ext = self.imgtrans_proj.get_result_ext(
                self.imgtrans_proj.current_img
            )
            self.imsave_thread.saveImg(
                imsave_path,
                img,
                self.imgtrans_proj.current_img,
                save_params={"ext": imsave_ext, "quality": pcfg.imgsave_quality},
                keep_alpha=self.imgtrans_proj.current_has_alpha(),
            )
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

    def to_trans_config(self):
        self.leftBar.configChecker.setChecked(True)
        self.configPanel.focusOnTranslator()

    def to_inpaint_config(self):
        self.leftBar.configChecker.setChecked(True)
        self.configPanel.focusOnInpaint()

    def to_ocr_config(self):
        self.leftBar.configChecker.setChecked(True)
        self.configPanel.focusOnOCR()

    def to_detect_config(self):
        self.leftBar.configChecker.setChecked(True)
        self.configPanel.focusOnDetect()

    def on_textdet_changed(self):
        module = self.bottomBar.textdet_selector.selector.currentText()
        tgt_selector = self.configPanel.detect_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_TEXTDETECTORS():
            tgt_selector.setCurrentText(module)

    def on_ocr_changed(self):
        module = self.bottomBar.ocr_selector.selector.currentText()
        tgt_selector = self.configPanel.ocr_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_OCR():
            tgt_selector.setCurrentText(module)

    def on_trans_changed(self):
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
        module = self.bottomBar.inpaint_selector.selector.currentText()
        tgt_selector = self.configPanel.inpaint_config_panel.module_combobox
        if tgt_selector.currentText() != module and module in GET_VALID_INPAINTERS():
            tgt_selector.setCurrentText(module)

    def on_transpagebtn_pressed(self, run_target: bool):
        page_key = self.imgtrans_proj.current_img
        if page_key is None:
            return

        blkitem_list = self.st_manager.textblk_item_list

        if len(blkitem_list) < 1:
            return

        self.translateBlkitemList(blkitem_list, -1)

    def translateBlkitemList(self, blkitem_list: List, mode: int) -> bool:

        tgt_img = self.imgtrans_proj.img_array
        if tgt_img is None:
            return False
        tgt_mask = self.imgtrans_proj.mask_array

        if len(blkitem_list) < 1:
            return False

        self._blktrans_at_page = self.imgtrans_proj.current_img

        self.global_search_widget.set_document_edited()

        im_h, im_w = tgt_img.shape[:2]

        blk_list, blk_ids = [], []
        for blkitem in blkitem_list:
            blk: TextBlock = blkitem.blk
            blk._bounding_rect = blkitem.absBoundingRect()
            blk.text = self.st_manager.pairwidget_list[
                blkitem.idx
            ].e_source.toPlainText()
            blk_ids.append(blkitem.idx)
            blk.set_lines_by_xywh(
                blk._bounding_rect,
                angle=-blk.angle,
                x_range=[0, im_w - 1],
                y_range=[0, im_h - 1],
                adjust_bbox=True,
            )
            blk_list.append(blk)

        self.module_manager.runBlktransPipeline(
            blk_list, tgt_img, mode, blk_ids, tgt_mask=tgt_mask
        )
        return True

    def finishTranslatePage(self, page_key):
        if page_key == self.imgtrans_proj.current_img:
            self.st_manager.updateTranslation()

    def on_imgtrans_pipeline_finished(self):
        self.backup_blkstyles.clear()
        self._run_imgtrans_wo_textstyle_update = False
        # Restore original translator if temporarily swapped for context-aware run
        if hasattr(self, "_ctx_batch_restore") and self._ctx_batch_restore:
            original = self._ctx_batch_restore
            self._ctx_batch_restore = None
            self.module_manager.setTranslator(original)
        # Close context translation log window if open
        if hasattr(self, "_ctx_log_dialog") and self._ctx_log_dialog is not None:
            self._ctx_log_dialog.close()
            self._ctx_log_dialog = None
        if pcfg.module.empty_runcache and not shared.HEADLESS:
            self.module_manager.unload_all_models()
        if shared.args.export_translation_txt:
            self.on_export_txt("translation")
        if shared.args.export_source_txt:
            self.on_export_txt("source")
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
                    blk.translation = re.sub(
                        r'([?.!"])\s+', r"\1", blk.translation
                    )  # remove spaces following punctuations
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
        blk_list = self.imgtrans_proj.get_blklist_byidx(page_index)
        ffmt_list = None
        if len(self.backup_blkstyles) == self.imgtrans_proj.num_pages and len(
            self.backup_blkstyles[page_index]
        ) == len(blk_list):
            ffmt_list: List[FontFormat] = self.backup_blkstyles[page_index]

        self.postprocess_translations(blk_list)

        # override font format if necessary
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
            pcfg.module.enable_detect
            or pcfg.module.enable_ocr
            or pcfg.module.enable_translate
        )

        if not inpaint_only:
            for ii, blk in enumerate(blk_list):
                if self._run_imgtrans_wo_textstyle_update and ffmt_list is not None:
                    blk.fontformat.merge(ffmt_list[ii])
                else:
                    if (
                        override_fnt_size or blk.font_size < 0
                    ):  # fall back to global font size if font size is not valid, it will be set to -1 for detected blocks
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
                            blk.rich_text = set_html_family(
                                blk.rich_text, gf.font_family
                            )

                    blk.line_spacing = gf.line_spacing
                    blk.letter_spacing = gf.letter_spacing
                    blk.italic = gf.italic
                    blk.bold = gf.bold
                    blk.underline = gf.underline
                    sw = blk.stroke_width
                    if (
                        sw > 0
                        and pcfg.module.enable_ocr
                        and pcfg.module.enable_detect
                        and not override_fnt_size
                    ):
                        blk.font_size = blk.font_size / (1 + sw)

            self.st_manager.auto_textlayout_flag = pcfg.let_autolayout_flag and (
                pcfg.module.enable_detect or pcfg.module.enable_translate
            )

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

        # save proj file on page trans finished
        self.imgtrans_proj.save()

        self.saveCurrentPage(False, False)

    def on_savestate_changed(self, unsaved: bool):
        save_state = self.tr("unsaved") if unsaved else self.tr("saved")
        self.titleBar.setTitleContent(save_state=save_state)

    def on_textstack_changed(self):
        if not self.page_changing:
            self.global_search_widget.set_document_edited()

    def on_run_blktrans(self, mode: int):
        blkitem_list = self.canvas.selected_text_items()
        self.translateBlkitemList(blkitem_list, mode)

    def on_blktrans_finished(self, mode: int, blk_ids: List[int]):

        if len(blk_ids) < 1:
            return

        # Guard: page may have changed during async translation
        if getattr(self, '_blktrans_at_page', None) != self.imgtrans_proj.current_img:
            return

        item_list = self.st_manager.textblk_item_list
        if any(idx >= len(item_list) for idx in blk_ids):
            return

        blkitem_list = [item_list[idx] for idx in blk_ids]

        pairw_list = []
        for blk in blkitem_list:
            if blk.idx >= len(self.st_manager.pairwidget_list):
                return
            pairw_list.append(self.st_manager.pairwidget_list[blk.idx])
        self.canvas.push_undo_command(
            RunBlkTransCommand(self.canvas, blkitem_list, pairw_list, mode)
        )

    def on_imgtrans_progressbox_showed(self):
        msg_size = self.module_manager.progress_msgbox.size()
        size = self.size()
        p = self.mapToGlobal(
            QPoint(size.width() - msg_size.width(), size.height() - msg_size.height())
        )
        self.module_manager.progress_msgbox.move(p)

    def on_closebtn_clicked(self):
        if self.imsave_thread.isRunning():
            self.imsave_thread.finished.connect(self.close)
            mb = FrameLessMessageBox()
            mb.setText(self.tr("Saving image..."))
            self.imsave_thread.finished.connect(mb.close)
            mb.exec()
            return
        self.close()

    def on_display_lang_changed(self, lang: str):
        if lang != pcfg.display_lang:
            pcfg.display_lang = lang
            self.set_display_lang(lang)

    def run_imgtrans(self):
        num_pages = self.imgtrans_proj.num_pages
        if num_pages == 0:
            return

        page_filter = None
        if num_pages > 1:
            from qtpy.QtWidgets import (
                QCheckBox,
                QComboBox,
                QDialog,
                QFrame,
                QGridLayout,
                QHBoxLayout,
                QLabel,
                QPushButton,
                QSpinBox,
                QVBoxLayout,
            )

            from ui.custom_widget import RangeSlider

            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Run"))
            dialog.setMinimumWidth(420)
            dialog.setSizeGripEnabled(False)
            layout = QVBoxLayout(dialog)

            range_frame = QFrame()
            range_frame.setFrameShape(QFrame.Shape.StyledPanel)
            range_layout = QVBoxLayout(range_frame)

            # Spinboxes for precise page input (placed above slider)
            spin_layout = QHBoxLayout()
            spin_layout.setContentsMargins(0, 0, 0, 0)
            no_btn_style = """
QSpinBox {
    background: rgba(128,128,128,0.13);
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 4px;
    padding: 2px 4px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 0px; }
"""
            start_spin = QSpinBox()
            start_spin.setRange(1, num_pages)
            start_spin.setValue(1)
            start_spin.setFixedWidth(70)
            start_spin.setStyleSheet(no_btn_style)
            end_spin = QSpinBox()
            end_spin.setRange(1, num_pages)
            end_spin.setValue(num_pages)
            end_spin.setFixedWidth(70)
            end_spin.setStyleSheet(no_btn_style)

            spin_layout.addStretch()
            spin_layout.addWidget(start_spin)
            spin_layout.addWidget(QLabel(" ~ "))
            spin_layout.addWidget(end_spin)
            spin_layout.addStretch()
            range_layout.addLayout(spin_layout)

            slider = RangeSlider(0, num_pages - 1)
            slider.setMinimumWidth(350)
            range_layout.addWidget(slider)

            range_info = QLabel()
            range_layout.addWidget(range_info)

            def update_range_info():
                lo = slider.low() + 1
                hi = slider.high() + 1
                range_info.setText(
                    self.tr("Page %1 ~ Page %2 (%3 pages)")
                    .replace("%1", str(lo))
                    .replace("%2", str(hi))
                    .replace("%3", str(hi - lo + 1))
                )

            def sync_spinboxes():
                start_spin.blockSignals(True)
                end_spin.blockSignals(True)
                start_spin.setValue(slider.low() + 1)
                end_spin.setValue(slider.high() + 1)
                start_spin.blockSignals(False)
                end_spin.blockSignals(False)

            def on_spinbox_changed():
                slider.blockSignals(True)
                slider.set_range(start_spin.value() - 1, end_spin.value() - 1)
                slider.blockSignals(False)
                sync_spinboxes()
                update_range_info()

            start_spin.valueChanged.connect(on_spinbox_changed)
            end_spin.valueChanged.connect(on_spinbox_changed)
            slider.rangeChanged.connect(
                lambda lo, hi: (sync_spinboxes(), update_range_info())
            )

            all_pages_cb = QCheckBox(self.tr("All Pages"))
            all_pages_cb.toggled.connect(
                lambda checked: (
                    slider.set_range(0, num_pages - 1),
                    slider.setEnabled(not checked),
                    start_spin.setEnabled(not checked),
                    end_spin.setEnabled(not checked),
                    update_range_info(),
                )
            )
            all_pages_cb.setChecked(True)
            range_layout.addWidget(all_pages_cb)

            layout.addWidget(range_frame)
            update_range_info()

            # Pipeline stages toggles
            stages_frame = QFrame()
            stages_frame.setFrameShape(QFrame.Shape.StyledPanel)
            stages_layout = QVBoxLayout(stages_frame)
            stages_layout.setContentsMargins(8, 6, 8, 6)

            stage_labels = [
                self.tr("Enable Text Detection"),
                self.tr("Enable OCR"),
                self.tr("Enable Translation"),
                self.tr("Enable Inpainting"),
            ]
            ctx_trans_cb = None
            for idx, label in enumerate(stage_labels):
                cb = QCheckBox(label)
                cb.setChecked(pcfg.module.stage_enabled(idx))
                cb.toggled.connect(
                    lambda checked, i=idx: self.on_enable_module(i, checked)
                )
                if idx == 2:
                    row = QWidget()
                    row_layout = QHBoxLayout(row)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.addWidget(cb)
                    ctx_trans_cb = QCheckBox(self.tr("Context Translation (beta)"))
                    row_layout.addWidget(ctx_trans_cb)
                    row_layout.addStretch()
                    stages_layout.addWidget(row)
                else:
                    stages_layout.addWidget(cb)

            layout.addWidget(stages_frame)

            # AI Chat settings — shown when Context Translation (beta) is checked
            ai_chat_frame = QFrame()
            ai_chat_frame.setFrameShape(QFrame.Shape.StyledPanel)
            ai_grid = QGridLayout(ai_chat_frame)
            ai_grid.setContentsMargins(8, 6, 8, 6)
            ai_grid.setSpacing(6)

            ai_title = QLabel(self.tr("AI Chat Settings"))
            ai_title.setStyleSheet("font-weight: bold;")
            ai_grid.addWidget(ai_title, 0, 0, 1, 2)

            # Adaptive mode info label — updates based on project page count
            mode_label = QLabel()
            mode_label.setWordWrap(True)
            mode_label.setStyleSheet("font-style: italic;")
            ai_grid.addWidget(mode_label, 1, 0, 1, 2)

            ai_grid.addWidget(QLabel(self.tr("Batch Size:")), 2, 0)
            batch_combo = QComboBox()
            batch_combo.addItems(["1", "3", "5", "10", "20"])
            batch_combo.setCurrentText("5")
            ai_grid.addWidget(batch_combo, 2, 1)

            ai_grid.addWidget(QLabel(self.tr("Context Pages:")), 3, 0)
            pages_spin = QSpinBox()
            pages_spin.setRange(0, 20)
            pages_spin.setValue(3)
            ai_grid.addWidget(pages_spin, 3, 1)

            def _update_mode_label():
                if all_pages_cb.isChecked():
                    effective = num_pages
                else:
                    effective = slider.high() - slider.low() + 1
                bs = int(batch_combo.currentText())
                pw = pages_spin.value()
                if effective == 0:
                    text = self.tr("Adaptive -- will be determined when Run starts")
                elif effective <= bs:
                    text = self.tr(
                        "Full context (%1 pages, all previous "
                        "translations as reference)"
                    ).replace("%1", str(effective))
                elif effective <= bs * 4:
                    text = (
                        self.tr("Windowed context (%1 pages, +/-%2 page window)")
                        .replace("%1", str(effective))
                        .replace("%2", str(pw))
                    )
                else:
                    text = self.tr(
                        "Windowed + auto-summary (%1 pages, long-form mode)"
                    ).replace("%1", str(effective))
                mode_label.setText(text)

            _update_mode_label()
            batch_combo.currentTextChanged.connect(lambda _: _update_mode_label())
            pages_spin.valueChanged.connect(lambda _: _update_mode_label())
            slider.rangeChanged.connect(lambda _lo, _hi: _update_mode_label())
            all_pages_cb.toggled.connect(lambda _: _update_mode_label())

            glossary_cb = QCheckBox(self.tr("Enforce Term Consistency (Glossary)"))
            glossary_cb.setChecked(True)
            ai_grid.addWidget(glossary_cb, 4, 0, 1, 2)

            ai_chat_frame.setVisible(False)
            layout.addWidget(ai_chat_frame)

            ctx_trans_cb.toggled.connect(
                lambda checked: ai_chat_frame.setVisible(checked)
            )

            # Run without update textstyle
            wo_update_cb = QCheckBox(self.tr("Run without update textstyle"))
            layout.addWidget(wo_update_cb)

            btn_layout = QHBoxLayout()
            run_btn = QPushButton(self.tr("Run"))
            cancel_btn = QPushButton(self.tr("Cancel"))
            btn_layout.addWidget(run_btn)
            btn_layout.addWidget(cancel_btn)
            layout.addLayout(btn_layout)

            run_btn.clicked.connect(dialog.accept)
            cancel_btn.clicked.connect(dialog.reject)

            if pcfg.module.all_stages_disabled():
                run_btn.setEnabled(False)

            if dialog.exec_() != QDialog.DialogCode.Accepted:
                return

            # If Context Translation is enabled, use current translator's profile
            if ctx_trans_cb.isChecked():
                translator = self.module_manager.translator
                if hasattr(translator, "_active_profile"):
                    profile = translator._active_profile
                    if profile:
                        from modules.translators.context_batch import (
                            ContextBatchTranslator,
                        )

                        def _ctx_status(msg):
                            bar = self.module_manager.progress_msgbox.translate_bar
                            bar.updateProgress(bar.progressbar.value(), msg)
                            if hasattr(self, "_ctx_log_dialog") and self._ctx_log_dialog:
                                self._ctx_log_dialog.append(msg)

                        self._ctx_batch_restore = pcfg.module.translator
                        ctx = ContextBatchTranslator(
                            api_config={
                                "api_host": profile.get("api_host", ""),
                                "api_key": profile.get("api_key", ""),
                                "model": profile.get("model", "gpt-4o"),
                                "temperature": profile.get("temperature", 0.1),
                                "max_tokens": profile.get("max_tokens", ""),
                                "proxy": profile.get("proxy", ""),
                            },
                            translation_prompt=profile.get("prompt_template", ""),
                            status_callback=_ctx_status,
                        )

                        # Create/show the context translation log window
                        if (
                            hasattr(self, "_ctx_log_dialog")
                            and self._ctx_log_dialog is not None
                        ):
                            self._ctx_log_dialog.close()
                            self._ctx_log_dialog = None
                        from ui.context_log_dialog import ContextLogDialog

                        self._ctx_log_dialog = ContextLogDialog(self)
                        self._ctx_log_dialog.show()

                        ctx.batch_size = int(batch_combo.currentText())
                        ctx.context_pages = pages_spin.value()
                        ctx.use_glossary = glossary_cb.isChecked()
                        self.module_manager.translate_thread.translator = ctx
                        self.module_manager.translate_thread.module = ctx

            if wo_update_cb.isChecked():
                self._run_imgtrans_wo_textstyle_update = True

            if not all_pages_cb.isChecked():
                page_filter = []
                for i in range(slider.low(), slider.high() + 1):
                    page_filter.append(self.imgtrans_proj.idx2pagename(i))

        if (
            not self.imgtrans_proj.is_all_pages_no_text
            and not pcfg.module.keep_exist_textlines
        ):
            msgBox = QMessageBox(self)
            msgBox.setIcon(QMessageBox.Question)
            msgBox.setWindowTitle(self.tr("Confirmation"))
            msgBox.setText(self.tr("Run will clear previous results. Continue?"))

            run_btn = msgBox.addButton(self.tr("Run"), QMessageBox.YesRole)
            cancel_btn = msgBox.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
            msgBox.setDefaultButton(run_btn)
            msgBox.exec_()

            if msgBox.clickedButton() == cancel_btn:
                return
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
            if page_filter is not None and page_name not in pages_to_process:
                continue
            self.imgtrans_proj.set_page_progress(page_name, 0)

        if pcfg.module.enable_detect:
            for page in self.imgtrans_proj.pages:
                if not pcfg.module.keep_exist_textlines:
                    if not pages_to_process:
                        self.imgtrans_proj.pages[page].clear()
                    elif page in pages_to_process:
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
                    if (
                        pcfg.module.enable_translate
                        or (all_disabled and not self._run_imgtrans_wo_textstyle_update)
                        or pcfg.module.enable_ocr
                    ):
                        textblk.rich_text = ""
                    textblk.vertical = textblk.src_is_vertical

        self.module_manager.runImgtransPipeline(
            pages_to_process if pages_to_process else None
        )

    def on_transpanel_changed(self):
        self.canvas.editor_index = self.rightComicTransStackPanel.currentIndex()
        if not self.canvas.textEditMode() and self.canvas.search_widget.isVisible():
            self.canvas.search_widget.hide()
        self.canvas.updateLayers()

    def import_tstyles(self):
        ddir = osp.dirname(pcfg.text_styles_path)
        p = QFileDialog.getOpenFileName(
            self, self.tr("Import Text Styles"), ddir, None, "(.json)"
        )
        if not isinstance(p, str):
            p = p[0]
        if p == "":
            return
        try:
            load_textstyle_from(p, raise_exception=True)
            save_config()
            self.textPanel.formatpanel.textstyle_panel.setStyles(text_styles)
        except Exception as e:
            create_error_dialog(e, self.tr(f"Failed to load from {p}"))

    def on_export_psd(self):
        """Open the PSD export dialog and run the chosen export method."""
        if self.imgtrans_proj.directory is None:
            return
        dialog = PsdExportDialog(self.imgtrans_proj, parent=self)
        if not dialog.exec():
            return

        options = dialog.get_options()
        blk_lists = self.imgtrans_proj.pages
        pages_to_export = options.page_filter or list(blk_lists.keys())
        if not pages_to_export:
            return

        total_pages = len(blk_lists)
        filtered = len(pages_to_export)
        if filtered < total_pages:
            LOGGER.info(
                "PSD export: %d/%d pages (filter: %s .. %s)",
                filtered,
                total_pages,
                pages_to_export[0],
                pages_to_export[-1],
            )
        else:
            LOGGER.info("PSD export: all %d pages", total_pages)

        from utils.psd_exporter import create_exporter

        LOGGER.info("PSD export method: %s", options.export_method)
        exporter = create_exporter(method=options.export_method)

        # Store result context for the completion handler
        self._psd_result = {
            "output_dir": options.output_dir,
            "success": 0,
            "total": len(pages_to_export),
            "export_method": options.export_method,
        }

        from .custom_widget import ProgressMessageBox
        from .io_thread import PsdExportThread

        self._psd_thread = PsdExportThread()
        self._psd_thread.page_done.connect(self._on_psd_page_done)
        self._psd_thread.page_failed.connect(self._on_psd_page_failed)
        self._psd_thread.export_finished.connect(self._on_psd_export_finished)

        self._psd_progress = ProgressMessageBox(self.tr("PSD Export"))
        self._psd_progress.zero_progress()
        self._psd_progress.stop_clicked.connect(self._psd_thread.request_stop)
        self._psd_progress.show()

        self._psd_thread.run_export(
            exporter, self.imgtrans_proj, pages_to_export, options
        )

    def _on_psd_page_done(self, page_name: str, out_path: str):
        self._psd_result["success"] += 1
        # Update progress bar
        total = self._psd_result.get("total", 1)
        done = self._psd_result["success"]
        pct = int(done / total * 100)
        self._psd_progress.updateTaskProgress(pct, f"  ({done}/{total})")

    def _on_psd_page_failed(self, page_name: str, error_msg: str):
        pass

    def _on_psd_export_finished(self, success_count: int):
        self._psd_progress.hide()
        # Cleanup is handled by PsdExportThread._run_export's finally block

        if success_count == 0:
            return

        method = self._psd_result.get("export_method", "binary")
        if method == "binary":
            msg = (
                self.tr("Exported ")
                + str(success_count)
                + self.tr(" PSD file(s).\n\nOutput:\n")
                + self._psd_result["output_dir"]
            )
        else:
            msg = (
                self.tr("Exported ")
                + str(success_count)
                + self.tr(
                    " ExtendScript(s).\n\nOpen Photoshop → File → Scripts → Browse to run each .jsx.\n\nOutput:\n"
                )
                + self._psd_result["output_dir"]
            )
        create_info_dialog(msg)

    def export_tstyles(self):
        ddir = osp.dirname(pcfg.text_styles_path)
        savep = QFileDialog.getSaveFileName(
            self, self.tr("Save Text Styles"), ddir, None, "(.json)"
        )
        if not isinstance(savep, str):
            savep = savep[0]
        if savep == "":
            return
        suffix = Path(savep).suffix
        if suffix != ".json":
            if suffix == "":
                savep = savep + ".json"
            else:
                savep = savep.replace(suffix, ".json")
        oldp = pcfg.text_styles_path
        try:
            pcfg.text_styles_path = savep
            save_text_styles(raise_exception=True)
            save_config()
        except Exception as e:
            create_error_dialog(e, self.tr(f"Failed save to {savep}"))
            pcfg.text_styles_path = oldp

    def fold_textarea(self, fold: bool):
        pcfg.fold_textarea = fold
        self.textPanel.textEditList.setFoldTextarea(fold)

    def show_source_text(self, show: bool):
        pcfg.show_source_text = show
        self.textPanel.textEditList.setSourceVisible(show)

    def show_trans_text(self, show: bool):
        pcfg.show_trans_text = show
        self.textPanel.textEditList.setTransVisible(show)

    def on_export_txt(self, dump_target, suffix=".txt"):
        if self.imgtrans_proj.directory is None:
            return
        try:
            self.imgtrans_proj.dump_txt(dump_target=dump_target, suffix=suffix)
            create_info_dialog(
                self.tr("Text file exported to ")
                + self.imgtrans_proj.dump_txt_path(dump_target, suffix)
            )
        except Exception as e:
            create_error_dialog(e, self.tr("Failed to export as TEXT file"))

    def on_import_trans_txt(self):
        try:
            selected_file = ""
            dialog = QFileDialog()
            selected_file = str(
                dialog.getOpenFileUrl(
                    self.parent(),
                    self.tr("Import *.md/*.txt"),
                    filter="*.txt *.md *.TXT *.MD",
                )[0].toLocalFile()
            )
            if not osp.exists(selected_file):
                return

            all_matched, match_rst = self.imgtrans_proj.load_translation_from_txt(
                selected_file
            )
            matched_pages = match_rst["matched_pages"]

            if self.imgtrans_proj.current_img in matched_pages:
                self.canvas.clear_undostack(update_saved_step=True)
                self.st_manager.updateSceneTextitems()

            if all_matched:
                msg = self.tr("Translation imported and matched successfully.")
            else:
                msg = self.tr(
                    'Imported txt file not fully matched with current project, please make sure source txt file structured like results from "export TXT"'
                )
                if len(match_rst["missing_pages"]) > 0:
                    msg += "\n" + self.tr("Missing pages: ") + "\n"
                    msg += "\n".join(match_rst["missing_pages"])
                if len(match_rst["unexpected_pages"]) > 0:
                    msg += "\n" + self.tr("Unexpected pages: ") + "\n"
                    msg += "\n".join(match_rst["unexpected_pages"])
                if len(match_rst["unmatched_pages"]) > 0:
                    msg += "\n" + self.tr("Unmatched pages: ") + "\n"
                    msg += "\n".join(match_rst["unmatched_pages"])
                msg = msg.strip()

            for pagename in matched_pages:
                pass  # keep blk data as-is

            create_info_dialog(msg)

        except Exception as e:
            create_error_dialog(
                e, self.tr("Failed to import translation from ") + selected_file
            )

    def on_reveal_file(self):
        if self.imgtrans_proj.directory is None:
            return
        current_img_path = self.imgtrans_proj.current_img_path()
        if sys.platform == "win32":
            # qprocess seems to fuck up with "\""
            p = '"' + str(Path(current_img_path)) + '"'
            subprocess.Popen("explorer.exe /select," + p, shell=True)

    def on_set_gsearch_widget(self):
        setup = self.leftBar.globalSearchChecker.isChecked()
        if setup:
            self._hidePageListOverlay()
            self.leftBar.showPageListLabel.setChecked(False)
            self._showSearchOverlay()
        else:
            self._hideSearchOverlay()

    def on_global_replace_finished(self):
        rt = self.global_search_widget.replace_thread
        self.canvas.push_text_command(
            GlobalRepalceAllCommand(
                rt.sceneitem_list,
                rt.background_list,
                rt.target_text,
                self.imgtrans_proj,
            )
        )
        rt.sceneitem_list = None
        rt.background_list = None

    def on_darkmode_triggered(self):
        pcfg.darkmode = self.titleBar.darkModeAction.isChecked()
        self.resetStyleSheet(reverse_icon=True)
        self.save_config()

    def on_copy_src(self):
        blks = self.canvas.selected_text_items()
        if len(blks) == 0:
            return

        src_list = [
            self.st_manager.pairwidget_list[blk.idx]
            .e_source.toPlainText()
            .strip()
            .replace("\n", " ")
            for blk in blks
        ]
        src_txt = "\n".join(src_list)

        self.st_manager.app_clipborad.setText(src_txt, QClipboard.Mode.Clipboard)

    def on_paste_src(self):
        blks = self.canvas.selected_text_items()
        if len(blks) == 0:
            return

        src_widget_list = [
            self.st_manager.pairwidget_list[blk.idx].e_source for blk in blks
        ]
        text_list = self.st_manager.app_clipborad.text().split("\n")

        n_paragraph = min(len(src_widget_list), len(text_list))
        if n_paragraph < 1:
            return

        src_widget_list = src_widget_list[:n_paragraph]
        text_list = text_list[:n_paragraph]

        self.canvas.push_undo_command(PasteSrcItemsCommand(src_widget_list, text_list))

    def run_batch(self, exec_dirs: Union[List, str], **kwargs):
        if not isinstance(exec_dirs, List):
            exec_dirs = exec_dirs.split(",")
        valid_dirs = []
        for d in exec_dirs:
            if osp.exists(d):
                valid_dirs.append(d)
            else:
                LOGGER.warning(f"target directory {d} does not exist.")
        self.exec_dirs = valid_dirs
        self.exec_pages = kwargs.get("pages", "").strip() if kwargs.get("pages") else ""
        self.run_next_dir()

    def run_next_dir(self):
        if len(self.exec_dirs) == 0:
            while self.imsave_thread.isRunning():
                time.sleep(0.1)
            LOGGER.info("finished translating all dirs, quit app...")
            self.app.quit()
            return
        d = self.exec_dirs.pop(0)

        LOGGER.info(f"translating {d} ...")
        self.openDir(d)

        page_filter = None
        if self.exec_pages:
            try:
                from utils.io_utils import page_names_from_range

                page_filter = page_names_from_range(self.imgtrans_proj, self.exec_pages)
            except ValueError as e:
                LOGGER.error(f"Invalid --pages argument: {e}")
                self.app.quit()
                return

        shared.pbar = {}
        npages = len(page_filter) if page_filter else len(self.imgtrans_proj.pages)
        if npages > 0:
            if pcfg.module.enable_detect:
                shared.pbar["detect"] = tqdm(range(npages), desc="Text Detection")
            if pcfg.module.enable_ocr:
                shared.pbar["ocr"] = tqdm(range(npages), desc="OCR")
            if pcfg.module.enable_translate:
                shared.pbar["translate"] = tqdm(range(npages), desc="Translation")
            if pcfg.module.enable_inpaint:
                shared.pbar["inpaint"] = tqdm(range(npages), desc="Inpaint")
        self.on_run_imgtrans(page_filter=page_filter)

    def on_create_errdialog(
        self, error_msg: str, detail_traceback: str = "", exception_type: str = ""
    ):
        try:
            if exception_type != "":
                shared.showed_exception.add(exception_type)
            err = QMessageBox()
            err.setText(error_msg)
            err.setDetailedText(detail_traceback)
            err.exec()
            if exception_type != "":
                shared.showed_exception.remove(exception_type)
        except Exception:
            if exception_type in shared.showed_exception:
                shared.showed_exception.remove(exception_type)
            LOGGER.error("Failed to create error dialog")
            LOGGER.error(traceback.format_exc())

    def on_create_infodialog(self, info_dict: dict):
        QMessageBox.StandardButton.NoButton
        dialog = MessageBox(**info_dict)
        dialog.show()  # exec_ will block main thread

    def setupRegisterWidget(self):
        self.titleBar.viewMenu.addSeparator()
        for cfg_name in shared.config_name_to_view_widget:
            d = shared.config_name_to_view_widget[cfg_name]
            widget: ViewWidget = d["widget"]
            action = QAction(widget.action_name, self.titleBar)
            action.setCheckable(True)
            visible = getattr(pcfg, cfg_name)
            action.setChecked(visible)
            action.triggered.connect(self.action_set_view_visible)
            self.titleBar.viewMenu.addAction(action)
            d["action"] = action
            shared.action_to_view_config_name[action] = cfg_name
            widget.set_expend_area(
                expend=getattr(pcfg, widget.config_expand_name), set_config=False
            )
            widget.view_hide_btn_clicked.connect(self.on_hide_view_widget)
            widget.setVisible(visible)

    def register_view_widget(self, widget: ViewWidget):
        assert widget.config_name not in shared.config_name_to_view_widget
        d = {"widget": widget}
        shared.config_name_to_view_widget[widget.config_name] = d

    def action_set_view_visible(self):
        action: QAction = self.sender()
        show = action.isChecked()
        cfg_name = shared.action_to_view_config_name[action]
        widget: ViewWidget = shared.config_name_to_view_widget[cfg_name]["widget"]
        widget.setVisible(show)
        setattr(pcfg, cfg_name, show)

    def on_hide_view_widget(self, cfg_name: str):
        d = shared.config_name_to_view_widget[cfg_name]
        widget: ViewWidget = d["widget"]
        widget.setVisible(False)
        action: QAction = d["action"]
        action.setChecked(False)
        setattr(pcfg, cfg_name, False)

    # ── About / Update Check ──────────────────────────────────

    def show_about_dialog(self):
        """Show the About dialog with version info and update check."""
        import launch

        dlg = AboutDialog(
            self,
            version=launch.VERSION,
            commit=launch.commit_hash(),
            branch=launch.BRANCH,
            git_path=launch.git,
            repo_path=str(launch.PATH_ROOT),
        )
        dlg.restart_requested.connect(self.restart_signal.emit)
        dlg.exec_()
