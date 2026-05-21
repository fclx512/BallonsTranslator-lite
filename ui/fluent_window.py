"""
Fluent Design window for BallonsTranslator-lite.
Replaces FramelessWindow + custom TitleBar/LeftBar with FluentWindow + NavigationInterface.
"""

import os, os.path as osp
from qtpy.QtWidgets import QWidget, QVBoxLayout, QStackedWidget, QSplitter
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QIcon

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition,
    FluentIcon as FIF, setTheme, Theme, setThemeColor,
    isDarkTheme, PushButton, TransparentToolButton,
)

from utils import shared
from utils.config import pcfg, save_config, text_styles
from utils.proj_imgtrans import ProjImgTrans
from ui.mainwindow import PageListView
from ui.canvas import Canvas
from ui.drawingpanel import DrawingPanel
from ui.scenetext_manager import SceneTextManager, TextPanel
from ui.configpanel import ConfigPanel
from ui.mainwindowbars import BottomBar
from ui.global_search_widget import GlobalSearchWidget
from ui.custom_widget import ImgtransProgressMessageBox
from ui.io_thread import ImgSaveThread, ExportDocThread, ImportDocThread
from ui.module_manager import ModuleManager
from modules import GET_VALID_TEXTDETECTORS, GET_VALID_INPAINTERS, GET_VALID_TRANSLATORS, GET_VALID_OCR
from ui import shared_widget as SW


THEME_MAP = {
    'eva-light':    (Theme.LIGHT, '#1e93e5'),
    'eva-dark':     (Theme.DARK,  '#5dade2'),
    'ember-light':  (Theme.LIGHT, '#C2410C'),
    'ember-dark':   (Theme.DARK,  '#EA580C'),
}


class FluentTranslatorWindow(FluentWindow):
    """Fluent Design main window with NavigationInterface + FluentTitleBar."""

    imgtrans_proj: ProjImgTrans = ProjImgTrans()
    save_on_page_changed = True
    opening_dir = False
    page_changing = False
    translator = None

    restart_signal = Signal()
    create_errdialog = Signal(str, str, str)
    create_infodialog = Signal(dict)

    def __init__(self, app, config, open_dir='', **exec_args):
        super().__init__()
        self.app = app
        self.config = config
        self.backup_blkstyles = []
        self._run_imgtrans_wo_textstyle_update = False

        shared.create_errdialog_in_mainthread = self.create_errdialog.emit
        self.create_errdialog.connect(self._on_create_errdialog)
        shared.create_infodialog_in_mainthread = self.create_infodialog.emit
        self.create_infodialog.connect(self._on_create_infodialog)
        shared.register_view_widget = self._register_view_widget

        self._apply_theme(pcfg.theme_name or 'eva-dark')

        self._setup_threads()
        self._setup_ui()
        self._setup_config()
        self._setup_shortcuts()
        self._setup_register_widget()

        self.setWindowTitle('BallonsTranslator-lite')
        self.resize(1200, 800)
        self.setMinimumWidth(800)

        if open_dir and osp.exists(open_dir):
            self.OpenProj(open_dir)
        elif pcfg.open_recent_on_startup:
            pass  # TODO: recent projects via NavigationInterface

    # ── Theme ──────────────────────────────────────────────

    def _apply_theme(self, theme_name: str):
        mode, accent = THEME_MAP.get(theme_name, (Theme.DARK, '#1e93e5'))
        setTheme(mode)
        setThemeColor(accent)
        pcfg.darkmode = mode == Theme.DARK

    def _on_theme_changed(self, theme_name: str):
        pcfg.theme_name = theme_name
        self._apply_theme(theme_name)
        save_config()

    # ── Threads ────────────────────────────────────────────

    def _setup_threads(self):
        self.imsave_thread = ImgSaveThread()
        self.export_doc_thread = ExportDocThread()
        self.export_doc_thread.fin_io.connect(lambda: None)
        self.import_doc_thread = ImportDocThread(self)
        self.import_doc_thread.fin_io.connect(lambda: None)

    # ── UI Construction ────────────────────────────────────

    def _setup_ui(self):
        self.configPanel = ConfigPanel(self)

        # Page list
        self.pageList = PageListView()
        self.pageList.reveal_file.connect(lambda: None)
        self.pageList.setHidden(True)
        self.pageList.currentItemChanged.connect(lambda c, p: None)

        self.leftStackWidget = QStackedWidget(self)
        self.leftStackWidget.addWidget(self.pageList)

        # Global search
        self.global_search_widget = GlobalSearchWidget(self.leftStackWidget)
        self.global_search_widget.req_update_pagetext.connect(lambda: None)
        self.global_search_widget.req_move_page.connect(lambda: None)
        self.imsave_thread.img_writed.connect(self.global_search_widget.on_img_writed)
        self.global_search_widget.search_tree.result_item_clicked.connect(lambda: None)
        self.leftStackWidget.addWidget(self.global_search_widget)

        # Bottom bar
        self.bottomBar = BottomBar(self)
        self.bottomBar.textedit_checkchanged.connect(lambda c: None)
        self.bottomBar.paintmode_checkchanged.connect(lambda c: None)
        self.bottomBar.textblock_checkchanged.connect(lambda c: None)

        # Canvas
        SW.canvas = self.canvas = Canvas()
        self.canvas.imgtrans_proj = self.imgtrans_proj
        self.canvas.gv.hide_canvas.connect(lambda: None)
        self.canvas.proj_savestate_changed.connect(lambda: None)
        self.canvas.textstack_changed.connect(lambda: None)
        self.canvas.run_blktrans.connect(lambda: None)
        self.canvas.drop_open_folder.connect(lambda p: None)
        self.canvas.originallayer_trans_slider = self.bottomBar.originalSlider
        self.canvas.textlayer_trans_slider = self.bottomBar.textlayerSlider
        self.canvas.copy_src_signal.connect(lambda: None)
        self.canvas.paste_src_signal.connect(lambda: None)
        self.bottomBar.originalSlider.valueChanged.connect(
            self.canvas.setOriginalTransparencyBySlider)
        self.bottomBar.textlayerSlider.valueChanged.connect(
            self.canvas.setTextLayerTransparencyBySlider)

        # Drawing & Text
        self.drawingPanel = DrawingPanel(self.canvas, self.configPanel.inpaint_config_panel)
        self.textPanel = TextPanel(self.app)
        self.textPanel.formatpanel.foldTextBtn.checkStateChanged.connect(lambda s: None)
        self.textPanel.formatpanel.sourceBtn.checkStateChanged.connect(lambda c: None)
        self.textPanel.formatpanel.transBtn.checkStateChanged.connect(lambda c: None)
        self.textPanel.formatpanel.textstyle_panel.export_style.connect(lambda: None)
        self.textPanel.formatpanel.textstyle_panel.import_style.connect(lambda: None)

        # Scene text manager
        SW.st_manager = self.st_manager = SceneTextManager(
            self.app, self, self.canvas, self.textPanel)
        self.st_manager.new_textblk.connect(self.canvas.search_widget.on_new_textblk)
        self.canvas.search_widget.pairwidget_list = self.st_manager.pairwidget_list
        self.canvas.search_widget.textblk_item_list = self.st_manager.textblk_item_list
        self.canvas.search_widget.replace_one.connect(self.st_manager.on_page_replace_one)
        self.canvas.search_widget.replace_all.connect(self.st_manager.on_page_replace_all)

        # Right panel stack
        self.rightComicTransStackPanel = QStackedWidget(self)
        self.rightComicTransStackPanel.addWidget(self.drawingPanel)
        self.rightComicTransStackPanel.addWidget(self.textPanel)
        self.rightComicTransStackPanel.currentChanged.connect(lambda i: None)

        # Splitter
        self.comicTransSplitter = QSplitter(Qt.Orientation.Horizontal)
        self.comicTransSplitter.addWidget(self.leftStackWidget)
        self.comicTransSplitter.addWidget(self.canvas.gv)
        self.comicTransSplitter.addWidget(self.rightComicTransStackPanel)
        self.comicTransSplitter.setStretchFactor(0, 1)
        self.comicTransSplitter.setStretchFactor(1, 10)
        self.comicTransSplitter.setStretchFactor(2, 1)

        # Progress
        self.imgtrans_progress_msgbox = ImgtransProgressMessageBox()

        # Build pages
        canvasPage = QWidget()
        canvasPage.setObjectName('canvasPage')
        cv = QVBoxLayout(canvasPage)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.comicTransSplitter, 1)
        cv.addWidget(self.bottomBar, 0)

        settingsPage = QWidget()
        settingsPage.setObjectName('settingsPage')
        sv = QVBoxLayout(settingsPage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.configPanel)

        self.addSubInterface(canvasPage, FIF.EDIT, self.tr('Image Translation'),
                             position=NavigationItemPosition.TOP)
        self.addSubInterface(settingsPage, FIF.SETTING, self.tr('Settings'),
                             position=NavigationItemPosition.BOTTOM)

        # Theme switcher at the bottom of navigation
        self.navigationInterface.addItem(
            routeKey='theme_toggle',
            icon=FIF.BRIGHTNESS,
            text=self.tr('Switch Theme'),
            onClick=lambda: self._cycle_theme(),
            position=NavigationItemPosition.BOTTOM,
        )

        self.switchTo(canvasPage)


    # ── Theme helpers ───────────────────────────────────────

    def _cycle_theme(self):
        """Cycle through 4 themes: eva-light → eva-dark → ember-light → ember-dark"""
        order = ['eva-light', 'eva-dark', 'ember-light', 'ember-dark']
        current = pcfg.theme_name or 'eva-dark'
        try:
            idx = (order.index(current) + 1) % len(order)
        except ValueError:
            idx = 0
        self._on_theme_changed(order[idx])

    # ── Configuration ──────────────────────────────────────

    def _setup_config(self):
        self.bottomBar.originalSlider.setValue(int(pcfg.original_transparency * 100))
        self.bottomBar.trans_selector.selector.addItems(GET_VALID_TRANSLATORS())
        self.bottomBar.ocr_selector.selector.addItems(GET_VALID_OCR())
        self.bottomBar.textdet_selector.selector.addItems(GET_VALID_TEXTDETECTORS())
        self.bottomBar.inpaint_selector.selector.addItems(GET_VALID_INPAINTERS())

        self.bottomBar.textdet_selector.setVisible(pcfg.module.enable_detect)
        self.bottomBar.ocr_selector.setVisible(pcfg.module.enable_ocr)
        self.bottomBar.trans_selector.setVisible(pcfg.module.enable_translate)
        self.bottomBar.inpaint_selector.setVisible(pcfg.module.enable_inpaint)

        self.bottomBar.textdet_selector.selector.currentTextChanged.connect(
            lambda t: setattr(pcfg.module, 'textdetector', t))
        self.bottomBar.ocr_selector.selector.currentTextChanged.connect(
            lambda t: setattr(pcfg.module, 'ocr', t))
        self.bottomBar.trans_selector.selector.currentTextChanged.connect(
            lambda t: setattr(pcfg.module, 'translator', t))
        self.bottomBar.inpaint_selector.selector.currentTextChanged.connect(
            lambda t: setattr(pcfg.module, 'inpainter', t))

        self.drawingPanel.maskTransperancySlider.setValue(
            int(pcfg.mask_transparency * 100))

        self.st_manager.formatpanel.global_format = pcfg.global_fontformat
        self.st_manager.formatpanel.set_active_format(pcfg.global_fontformat)

        self.rightComicTransStackPanel.setHidden(True)
        self.st_manager.setTextEditMode(False)
        self.st_manager.formatpanel.foldTextBtn.setChecked(pcfg.fold_textarea)
        self.st_manager.formatpanel.transBtn.setCheckState(pcfg.show_trans_text)
        self.st_manager.formatpanel.sourceBtn.setCheckState(pcfg.show_source_text)

        self.module_manager = ModuleManager(self.imgtrans_proj)
        self.module_manager.finish_translate_page.connect(lambda: None)
        self.module_manager.imgtrans_pipeline_finished.connect(lambda: None)
        self.module_manager.page_trans_finished.connect(lambda: None)
        self.module_manager.setupThread(self.configPanel, self.imgtrans_progress_msgbox)
        self.module_manager.progress_msgbox.showed.connect(lambda: None)
        self.module_manager.blktrans_pipeline_finished.connect(lambda: None)
        self.module_manager.setTextDetector()
        self.module_manager.setOCR()
        self.module_manager.setTranslator()
        self.module_manager.setInpainter()

        self.global_search_widget.imgtrans_proj = self.imgtrans_proj
        self.global_search_widget.setupReplaceThread(
            self.st_manager.pairwidget_list, self.st_manager.textblk_item_list)
        self.global_search_widget.replace_thread.finished.connect(lambda: None)

        self.configPanel.setupConfig()
        self.configPanel.save_config.connect(save_config)
        self.configPanel.reload_textstyle.connect(lambda p: None)
        self.configPanel.font_exclusion_changed.connect(lambda: None)

        shared.init_font_list()
        familybox = self.textPanel.formatpanel.familybox
        filtered = shared.get_filtered_font_list(pcfg.excluded_fonts)
        if familybox.count() == 0 and filtered:
            familybox.update_font_list(filtered)

        self.textPanel.formatpanel.textstyle_panel.initStyles(text_styles)

        self.canvas.search_widget.whole_word_toggle.setChecked(pcfg.fsearch_whole_word)
        self.canvas.search_widget.case_sensitive_toggle.setChecked(pcfg.fsearch_case)
        self.canvas.search_widget.regex_toggle.setChecked(pcfg.fsearch_regex)
        self.canvas.search_widget.range_combobox.setCurrentIndex(pcfg.fsearch_range)
        self.global_search_widget.whole_word_toggle.setChecked(pcfg.gsearch_whole_word)
        self.global_search_widget.case_sensitive_toggle.setChecked(pcfg.gsearch_case)
        self.global_search_widget.regex_toggle.setChecked(pcfg.gsearch_regex)
        self.global_search_widget.range_combobox.setCurrentIndex(pcfg.gsearch_range)

    def _setup_shortcuts(self):
        if pcfg.shortcuts:
            from qtpy.QtWidgets import QShortcut
            from qtpy.QtGui import QKeySequence
            for action_name, keys in pcfg.shortcuts.items():
                if keys and isinstance(keys, str):
                    try:
                        QShortcut(QKeySequence(keys), self)
                    except (TypeError, ValueError):
                        pass

    def _setup_register_widget(self):
        pass  # view widgets handled via NavigationInterface in fluent mode

    # ── Style ──────────────────────────────────────────────

    def setStyleSheet(self, styleSheet: str) -> None:
        if hasattr(self, 'imgtrans_progress_msgbox'):
            self.imgtrans_progress_msgbox.setStyleSheet(styleSheet)
        if hasattr(self, 'export_doc_thread'):
            self.export_doc_thread.progress_bar.setStyleSheet(styleSheet)
        if hasattr(self, 'import_doc_thread'):
            self.import_doc_thread.progress_bar.setStyleSheet(styleSheet)
        return super().setStyleSheet(styleSheet)

    # ── Stub methods (replaced incrementally) ──────────────

    def OpenProj(self, path): pass
    def save_config(self): save_config()
    def _on_create_errdialog(self, *a): pass
    def _on_create_infodialog(self, *a): pass
    def _register_view_widget(self, w): pass
