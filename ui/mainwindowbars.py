import os.path as osp
from typing import List, Union

from qtpy.QtCore import QEvent, QPoint, Qt, Signal
from qtpy.QtGui import QActionGroup, QKeySequence, QMouseEvent
from qtpy.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
)

from utils import shared as C
from utils.config import pcfg
from utils.shared import (
    BOTTOMBAR_HEIGHT,
    LEFTBAR_WIDTH,
    LEFTBTN_WIDTH,
    TITLEBAR_HEIGHT,
    WINDOW_BORDER_WIDTH,
)

from .custom_widget import PaintQSlider, Widget
from .framelesswindow import FramelessMoveResize
from .module_tool_button import ModuleSelectionWidget

if C.FLAG_QT6:
    from qtpy.QtGui import QAction
else:
    from qtpy.QtWidgets import QAction


class ShowPageListChecker(QCheckBox): ...


class OpenBtn(QToolButton): ...


class StatusButton(QPushButton):
    pass


class TitleBarToolBtn(QToolButton):
    pass


class StateChecker(QCheckBox):
    checked = Signal(str)
    unchecked = Signal(str)

    def __init__(
        self, checker_type: str, uncheckable: bool = False, *args, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.checker_type = checker_type
        self.uncheckable = uncheckable

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.isChecked():
                self.setChecked(True)
            elif self.uncheckable:
                self.setChecked(False)

    def setChecked(self, check: bool) -> None:
        check_state = self.isChecked()
        super().setChecked(check)
        if check_state != check:
            if check:
                self.checked.emit(self.checker_type)
            else:
                self.unchecked.emit(self.checker_type)


class LeftBar(Widget):
    recent_proj_list = []
    imgTransChecked = Signal()
    configChecked = Signal()
    open_dir = Signal(str)
    open_json_proj = Signal(str)
    open_images = Signal(list)  # list of image file paths
    save_proj = Signal()
    save_proj_as = Signal()
    save_config = Signal()

    def __init__(self, mainwindow, *args, **kwargs) -> None:
        super().__init__(mainwindow, *args, **kwargs)
        self.mainwindow: QMainWindow = mainwindow

        padding = (LEFTBAR_WIDTH - LEFTBTN_WIDTH) // 2
        self.setFixedWidth(LEFTBAR_WIDTH)
        self.showPageListLabel = ShowPageListChecker()

        self.globalSearchChecker = QCheckBox()
        self.globalSearchChecker.setObjectName("GlobalSearchChecker")
        self.globalSearchChecker.setToolTip(self.tr("Global Search (Ctrl+G)"))

        self.imgTransChecker = StateChecker("imgtrans")
        self.imgTransChecker.setObjectName("ImgTransChecker")
        self.imgTransChecker.checked.connect(self.stateCheckerChanged)

        self.configChecker = StateChecker("config", uncheckable=True)
        self.configChecker.setObjectName("ConfigChecker")
        self.configChecker.checked.connect(self.stateCheckerChanged)
        self.configChecker.unchecked.connect(self.stateCheckerChanged)

        actionOpenFolder = QAction(self.tr("Open Folder ..."), self)
        actionOpenFolder.triggered.connect(self.onOpenFolder)
        actionOpenFolder.setShortcut(QKeySequence.Open)

        actionOpenProj = QAction(self.tr("Open Project ... *.json"), self)
        actionOpenProj.triggered.connect(self.onOpenProj)

        actionOpenImage = QAction(self.tr("Open Image ..."), self)
        actionOpenImage.triggered.connect(self.onOpenImage)

        actionSaveProj = QAction(self.tr("Save Project"), self)
        self.save_proj = actionSaveProj.triggered
        actionSaveProj.setShortcut(QKeySequence.StandardKey.Save)

        actionSaveProjAs = QAction(self.tr("Save Project As ..."), self)
        self.save_proj_as = actionSaveProjAs.triggered
        actionSaveProjAs.setShortcut(QKeySequence.StandardKey.SaveAs)

        actionExportSrcTxt = QAction(self.tr("Export source text as TXT"), self)
        self.export_src_txt = actionExportSrcTxt.triggered
        actionExportTranslationTxt = QAction(self.tr("Export translation as TXT"), self)
        self.export_trans_txt = actionExportTranslationTxt.triggered

        actionImportTranslationTxt = QAction(
            self.tr("Import translation from TXT"), self
        )
        self.import_trans_txt = actionImportTranslationTxt.triggered

        self.recentMenu = QMenu(self.tr("Open Recent"), self)

        openMenu = QMenu(self)
        openMenu.addActions([actionOpenFolder, actionOpenProj, actionOpenImage])
        self._recent_menu_action = openMenu.addMenu(self.recentMenu)
        openMenu.addSeparator()
        openMenu.addActions(
            [
                actionSaveProj,
                actionSaveProjAs,
                actionExportSrcTxt,
                actionExportTranslationTxt,
                actionImportTranslationTxt,
            ]
        )
        self.openBtn = OpenBtn()
        self.openBtn.setFixedSize(LEFTBTN_WIDTH, LEFTBTN_WIDTH)
        self.openBtn.setMenu(openMenu)
        self.openBtn.setPopupMode(QToolButton.InstantPopup)

        openBtnToolBar = QToolBar(self)
        openBtnToolBar.setFixedSize(LEFTBTN_WIDTH, LEFTBTN_WIDTH)
        openBtnToolBar.addWidget(self.openBtn)

        self.runImgtransBtn = QPushButton()
        self.runImgtransBtn.setObjectName("RunButton")
        self.runImgtransBtn.setText(self.tr("Run"))
        font = self.runImgtransBtn.font()
        font.setPixelSize(10)
        self.runImgtransBtn.setFont(font)
        self.runImgtransBtn.setFixedSize(LEFTBTN_WIDTH, LEFTBTN_WIDTH)
        self.run_imgtrans_clicked = self.runImgtransBtn.clicked
        self.runImgtransBtn.setFixedSize(LEFTBTN_WIDTH, LEFTBTN_WIDTH)

        vlayout = QVBoxLayout(self)
        vlayout.addWidget(openBtnToolBar)
        vlayout.addWidget(self.showPageListLabel)
        vlayout.addWidget(self.globalSearchChecker)
        vlayout.addWidget(self.imgTransChecker)
        vlayout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))
        vlayout.addWidget(self.configChecker)
        vlayout.addWidget(self.runImgtransBtn)
        vlayout.setContentsMargins(
            padding, LEFTBTN_WIDTH // 2, padding, LEFTBTN_WIDTH // 2
        )
        vlayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vlayout.setSpacing(LEFTBTN_WIDTH * 3 // 4)
        self.setGeometry(0, 0, 300, 500)
        self.setMouseTracking(True)

    def initRecentProjMenu(self, proj_list: List[str]):
        self.recent_proj_list = proj_list
        for proj in proj_list:
            action = QAction(proj, self)
            self.recentMenu.addAction(action)
            action.triggered.connect(self.recentActionTriggered)
        self._add_trailing_clear_action()
        self._update_recent_menu_state()

    # ── Recent menu helpers ────────────────────────────────


    def _get_proj_actions(self):
        """Return actions that correspond to recent project entries only."""
        actions = self.recentMenu.actions()
        return [
            a for a in actions if not a.property("_is_clear_action") and not a.isSeparator()
        ]

    def _remove_trailing_clear_action(self):
        """Remove existing 'Clear History' action and its preceding separator."""
        actions = self.recentMenu.actions()
        for a in list(actions):
            if a.property("_is_clear_action"):
                self.recentMenu.removeAction(a)
        actions = self.recentMenu.actions()
        if actions and actions[-1].isSeparator():
            self.recentMenu.removeAction(actions[-1])

    def _add_trailing_clear_action(self):
        """Add separator and 'Clear History' action at the end if there are entries."""
        if not self._get_proj_actions():
            return
        self.recentMenu.addSeparator()
        clear_action = QAction(self.tr("Clear History"), self)
        clear_action.setProperty("_is_clear_action", True)
        clear_action.triggered.connect(self._clearRecentProjList)
        self.recentMenu.addAction(clear_action)

    def _rebuild_trailing_clear_action(self):
        """Remove and re-add the trailing separator + Clear History."""
        self._remove_trailing_clear_action()
        self._add_trailing_clear_action()

    def _clearRecentProjList(self):
        """Clear all recent project history entries."""
        for a in self._get_proj_actions():
            self.recentMenu.removeAction(a)
        self.recent_proj_list.clear()
        self._remove_trailing_clear_action()
        self.save_config.emit()
        self._update_recent_menu_state()

    def _update_recent_menu_state(self):
        """Gray out the 'Open Recent' menu item when there are no recent projects."""
        has_recent = bool(self._get_proj_actions())
        self._recent_menu_action.setEnabled(has_recent)

    def updateRecentProjList(self, proj_list: Union[str, List[str]]):
        if len(proj_list) == 0:
            return
        if isinstance(proj_list, str):
            proj_list = [proj_list]
        if self.recent_proj_list == proj_list:
            return

        actionlist = self.recentMenu.actions()
        if len(self.recent_proj_list) == 0:
            self.recent_proj_list.append(proj_list.pop())
            topAction = QAction(self.recent_proj_list[-1], self)
            topAction.triggered.connect(self.recentActionTriggered)
            self.recentMenu.addAction(topAction)
        else:
            topAction = actionlist[0]
        for proj in proj_list[::-1]:
            try:  # remove duplicated
                idx = self.recent_proj_list.index(proj)
                if idx == 0:
                    continue
                del self.recent_proj_list[idx]
                self.recentMenu.removeAction(self.recentMenu.actions()[idx])
                if len(self.recent_proj_list) == 0:
                    topAction = QAction(proj, self)
                    self.recentMenu.addAction(topAction)
                    topAction.triggered.connect(self.recentActionTriggered)
                    continue
            except ValueError:
                pass
            newTop = QAction(proj, self)
            self.recentMenu.insertAction(topAction, newTop)
            newTop.triggered.connect(self.recentActionTriggered)
            self.recent_proj_list.insert(0, proj)
            topAction = newTop

        MAXIUM_RECENT_PROJ_NUM = 14
        actionlist = self._get_proj_actions()
        num_to_remove = len(actionlist) - MAXIUM_RECENT_PROJ_NUM
        if num_to_remove > 0:
            actions_to_remove = actionlist[-num_to_remove:]
            for action in actions_to_remove:
                self.recentMenu.removeAction(action)
                self.recent_proj_list.pop()

        self._rebuild_trailing_clear_action()
        self.save_config.emit()
        self._update_recent_menu_state()

    def recentActionTriggered(self):
        path = self.sender().text()
        if osp.exists(path):
            self.updateRecentProjList(path)
            self.open_dir.emit(path)
        else:
            self.recent_proj_list.remove(path)
            self.recentMenu.removeAction(self.sender())
            self._rebuild_trailing_clear_action()
            self.save_config.emit()
            self._update_recent_menu_state()

    def onOpenFolder(self) -> None:

        d = None
        if len(self.recent_proj_list) > 0:
            for projp in self.recent_proj_list:
                if not osp.isdir(projp):
                    projp = osp.dirname(projp)
                if osp.exists(projp):
                    d = projp
                    break

        dialog = QFileDialog()
        folder_path = str(
            dialog.getExistingDirectory(self, self.tr("Select Directory"), d)
        )
        if osp.exists(folder_path):
            self.updateRecentProjList(folder_path)
            self.open_dir.emit(folder_path)

    def onOpenProj(self):
        dialog = QFileDialog()
        json_path = str(
            dialog.getOpenFileUrl(
                self.parent(), self.tr("Open Project ... *.json"), filter="*.json"
            )[0].toLocalFile()
        )
        if osp.exists(json_path):
            self.open_json_proj.emit(json_path)

    def onOpenImage(self):
        dialog = QFileDialog()
        paths = dialog.getOpenFileNames(
            self,
            self.tr("Open Image ..."),
            "",
            "Images (*.bmp *.jpg *.jpeg *.png *.webp *.jxl);;All Files (*)",
        )[0]
        if paths:
            self.open_images.emit(paths)

    def stateCheckerChanged(self, checker_type: str):
        if checker_type == "imgtrans":
            self.configChecker.setChecked(False)
            self.imgTransChecked.emit()
        elif checker_type == "config":
            if self.configChecker.isChecked():
                self.imgTransChecker.setChecked(False)
                self.configChecked.emit()
            else:
                self.imgTransChecker.setChecked(True)

    def needleftStackWidget(self) -> bool:
        return self.showPageListLabel.isChecked()


class TitleBar(Widget):
    closebtn_clicked = Signal()
    display_lang_changed = Signal(str)
    help_about_triggered = Signal()


    def __init__(self, parent, *args, **kwargs) -> None:
        super().__init__(parent, *args, **kwargs)
        self.mainwindow: QMainWindow = parent
        self.mainwindow.installEventFilter(self)
        self.mPos: QPoint = None
        self.normalsize = False
        self.proj_name = ""
        self.page_name = ""
        self.save_state = ""
        self.setFixedHeight(TITLEBAR_HEIGHT)
        self.setMouseTracking(True)

        self.editToolBtn = TitleBarToolBtn(self)
        self.editToolBtn.setText(self.tr("Edit"))

        undoAction = QAction(self.tr("Undo"), self)
        self.undo_trigger = undoAction.triggered
        redoAction = QAction(self.tr("Redo"), self)
        self.redo_trigger = redoAction.triggered
        pageSearchAction = QAction(self.tr("Search"), self)
        self.page_search_trigger = pageSearchAction.triggered
        globalSearchAction = QAction(self.tr("Global Search"), self)
        self.global_search_trigger = globalSearchAction.triggered

        editMenu = QMenu(self.editToolBtn)
        editMenu.addActions([undoAction, redoAction])
        editMenu.addSeparator()
        editMenu.addActions([pageSearchAction, globalSearchAction])
        self.editToolBtn.setMenu(editMenu)
        self.editToolBtn.setPopupMode(QToolButton.InstantPopup)

        self.viewToolBtn = TitleBarToolBtn(self)
        self.viewToolBtn.setText(self.tr("View"))

        self.displayLanguageMenu = QMenu(self.tr("Display Language"), self)
        self.lang_ac_group = lang_ac_group = QActionGroup(self)
        lang_ac_group.setExclusive(True)
        lang_actions = []
        for lang, lang_code in C.DISPLAY_LANGUAGE_MAP.items():
            la = QAction(lang, self)
            if lang_code == pcfg.display_lang:
                la.setChecked(True)
            la.triggered.connect(self.on_displaylang_triggered)
            la.setCheckable(True)
            lang_ac_group.addAction(la)
            lang_actions.append(la)
        self.displayLanguageMenu.addActions(lang_actions)

        drawBoardAction = QAction(self.tr("Drawing Board"), self)
        texteditAction = QAction(self.tr("Text Editor"), self)
        self._styleMgrAction = QAction(self.tr("Font Style Manager"), self)
        self.stylemgr_trigger = self._styleMgrAction.triggered
        self.darkModeAction = darkModeAction = QAction(self.tr("Dark Mode"), self)
        darkModeAction.setCheckable(True)

        self.viewMenu = viewMenu = QMenu(self.viewToolBtn)
        viewMenu.addMenu(self.displayLanguageMenu)
        viewMenu.addActions([drawBoardAction, texteditAction])
        viewMenu.addSeparator()
        viewMenu.addAction(darkModeAction)
        self.viewToolBtn.setMenu(viewMenu)
        self.viewToolBtn.setPopupMode(QToolButton.InstantPopup)
        self.textedit_trigger = texteditAction.triggered
        self.drawboard_trigger = drawBoardAction.triggered
        self.darkmode_trigger = darkModeAction.triggered

        # 工具菜单
        self.toolsToolBtn = TitleBarToolBtn(self)
        self.toolsToolBtn.setText(self.tr("Tools"))

        # 区域合并工具
        mergeToolAction = QAction(self.tr("Region Merge Tool"), self)
        self.merge_tool_trigger = mergeToolAction.triggered

        # 路径重排（替换原有的智能重排）
        smartReorderAction = QAction(self.tr("Path Reorder…"), self)
        self.smart_reorder_trigger = smartReorderAction.triggered

        # PSD 导出（封存 — 在 PS 中打开时有兼容问题，等待维修）
        psdExportAction = QAction(self.tr("Export as PSD… (Under Repair)"), self)
        psdExportAction.setEnabled(False)
        psdExportAction.setToolTip(
            self.tr("暂不可用 — Photoshop 打开时有兼容问题，等待修复")
        )
        self.psd_export_triggered = psdExportAction.triggered

        # Quick Symbol dialog
        quickSymbolAction = QAction(self.tr("Quick Symbol"), self)
        self.quick_symbol_trigger = quickSymbolAction.triggered

        # Advanced Alignment
        advAlignAction = QAction(self.tr("Advanced Alignment"), self)
        self.adv_align_trigger = advAlignAction.triggered

        # 整理换行
        normalizeBreaksAction = QAction(self.tr("Normalize Breaks…"), self)
        self.normalize_breaks_triggered = normalizeBreaksAction.triggered

        # 无字图配对工具
        noTextToolAction = QAction(self.tr("Pair No-text Images…"), self)
        self.launch_notext_tool = noTextToolAction.triggered

        # 术语表提取
        glossaryExtractAction = QAction(self.tr("Extract Glossary…"), self)
        self.glossary_extract_triggered = glossaryExtractAction.triggered

        toolsMenu = QMenu(self.toolsToolBtn)
        # 页面布局工具
        toolsMenu.addAction(mergeToolAction)
        toolsMenu.addAction(smartReorderAction)
        toolsMenu.addSeparator()
        # 文字 / 样式工具
        toolsMenu.addAction(self._styleMgrAction)
        toolsMenu.addAction(quickSymbolAction)
        toolsMenu.addAction(advAlignAction)
        toolsMenu.addSeparator()
        # 导出 / 批量处理
        toolsMenu.addAction(psdExportAction)
        toolsMenu.addAction(normalizeBreaksAction)
        toolsMenu.addSeparator()
        # 术语表
        toolsMenu.addAction(glossaryExtractAction)
        toolsMenu.addSeparator()
        # 外部工具
        toolsMenu.addAction(noTextToolAction)
        self.toolsToolBtn.setMenu(toolsMenu)
        self.toolsToolBtn.setPopupMode(QToolButton.InstantPopup)

        # 帮助菜单（关于等）
        aboutAction = QAction(self.tr("About BallonsTranslator-lite"), self)
        self.help_about_triggered = aboutAction.triggered

        self.helpToolBtn = TitleBarToolBtn(self)
        self.helpToolBtn.setText(self.tr("Help"))
        helpMenu = QMenu(self.helpToolBtn)
        helpMenu.addAction(aboutAction)
        self.helpToolBtn.setMenu(helpMenu)
        self.helpToolBtn.setPopupMode(QToolButton.InstantPopup)

        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedWidth(LEFTBAR_WIDTH - 12)

        self.titleLabel = QLabel("BallonsTranslator-lite")
        self.titleLabel.setObjectName("TitleLabel")
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hlayout = QHBoxLayout(self)
        hlayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hlayout.addWidget(self.iconLabel)
        hlayout.addWidget(self.editToolBtn)
        hlayout.addWidget(self.viewToolBtn)
        hlayout.addWidget(self.toolsToolBtn)
        hlayout.addWidget(self.helpToolBtn)
        hlayout.addStretch()
        hlayout.addWidget(self.titleLabel)
        hlayout.addStretch()
        hlayout.setContentsMargins(0, 0, 0, 0)

        self.minBtn = QPushButton()
        self.minBtn.setObjectName("minBtn")
        self.minBtn.clicked.connect(self.onMinBtnClicked)
        self.maxBtn = QCheckBox()
        self.maxBtn.setObjectName("maxBtn")
        self.maxBtn.clicked.connect(self.onMaxBtnClicked)
        self.maxBtn.setFixedSize(48, 27)
        self.closeBtn = QPushButton()
        self.closeBtn.setObjectName("closeBtn")
        self.closeBtn.clicked.connect(self.closebtn_clicked)
        hlayout.addWidget(self.minBtn)
        hlayout.addWidget(self.maxBtn)
        hlayout.addWidget(self.closeBtn)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(0)

    def eventFilter(self, obj, e):
        if obj == self.mainwindow:
            if e.type() == QEvent.Type.WindowStateChange:
                self.maxBtn.setChecked(self.mainwindow.isMaximized())
                return False

        return super().eventFilter(obj, e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        super().mouseDoubleClickEvent(e)
        FramelessMoveResize.toggleMaxState(self.mainwindow)

    def onMaxBtnClicked(self):
        FramelessMoveResize.toggleMaxState(self.mainwindow)

    def onMinBtnClicked(self):
        self.mainwindow.showMinimized()

    def on_displaylang_triggered(self):
        ac = self.lang_ac_group.checkedAction()
        self.display_lang_changed.emit(C.DISPLAY_LANGUAGE_MAP[ac.text()])

    def mousePressEvent(self, event: QMouseEvent) -> None:

        if C.FLAG_QT6:
            g_pos = event.globalPosition().toPoint()
        else:
            g_pos = event.globalPos()
        if event.button() == Qt.MouseButton.LeftButton:
            if (
                not self.mainwindow.isMaximized()
                and event.pos().y() < WINDOW_BORDER_WIDTH
            ):
                pass
            else:
                self.mPos = event.pos()
                self.mPosGlobal = g_pos
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.mPos = None
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.mPos is not None:
            if C.FLAG_QT6:
                g_pos = event.globalPosition().toPoint()
            else:
                g_pos = event.globalPos()
            FramelessMoveResize.startSystemMove(self.window(), g_pos)

    def hideEvent(self, e) -> None:
        self.mPos = None
        return super().hideEvent(e)

    def leaveEvent(self, e) -> None:
        self.mPos = None
        return super().leaveEvent(e)

    def setTitleContent(
        self, proj_name: str = None, page_name: str = None, save_state: str = None
    ):
        max_proj_len = 50
        max_page_len = 50
        if proj_name is not None:
            if len(proj_name) > max_proj_len:
                proj_name = proj_name[: max_proj_len - 3] + "..."
            self.proj_name = proj_name
        if page_name is not None:
            if len(page_name) > max_page_len:
                page_name = page_name[: max_page_len - 3] + "..."
            self.page_name = page_name
        if save_state is not None:
            self.save_state = save_state
        title = self.proj_name + " - " + self.page_name
        if self.save_state != "":
            title += " - " + self.save_state
        self.titleLabel.setText(title)




class BottomBar(Widget):
    textedit_checkchanged = Signal()
    paintmode_checkchanged = Signal()
    textblock_checkchanged = Signal()

    def __init__(self, mainwindow: QMainWindow, *args, **kwargs) -> None:
        super().__init__(mainwindow, *args, **kwargs)
        self.setFixedHeight(BOTTOMBAR_HEIGHT)
        self.setMouseTracking(True)
        self.mainwindow = mainwindow

        self.textdet_selector = ModuleSelectionWidget(self.tr("Text Detector"), "textdetect.svg")
        self.ocr_selector = ModuleSelectionWidget(self.tr("OCR"), "small_ocr.svg")
        self.inpaint_selector = ModuleSelectionWidget(self.tr("Inpaint"), "drawingtools_inpaint.svg")
        self.trans_selector = ModuleSelectionWidget(self.tr("Translator"), "bottombar_translate_activate.svg")

        self.hlayout = QHBoxLayout(self)
        self.paintChecker = QCheckBox()
        self.paintChecker.setObjectName("PaintChecker")
        self.paintChecker.setToolTip(self.tr("Enable/disable paint mode"))
        self.paintChecker.clicked.connect(self.onPaintCheckerPressed)
        self.texteditChecker = QCheckBox()
        self.texteditChecker.setObjectName("TexteditChecker")
        self.texteditChecker.setToolTip(self.tr("Enable/disable text edit mode"))
        self.texteditChecker.clicked.connect(self.onTextEditCheckerPressed)
        self.textblockChecker = QCheckBox()
        self.textblockChecker.setObjectName("TextblockChecker")
        self.textblockChecker.clicked.connect(self.onTextblockCheckerClicked)

        self.originalSlider = PaintQSlider(
            self.tr("Original Compare"), Qt.Orientation.Horizontal, self
        )
        self.originalSlider.setFixedWidth(150)
        self.originalSlider.setRange(0, 100)

        self.textlayerSlider = PaintQSlider(
            self.tr("Text layer opacity"), Qt.Orientation.Horizontal, self
        )
        self.textlayerSlider.setFixedWidth(150)
        self.textlayerSlider.setValue(100)
        self.textlayerSlider.setRange(0, 100)

        self.hlayout.addWidget(self.textdet_selector)
        self.hlayout.addWidget(self.ocr_selector)
        self.hlayout.addWidget(self.inpaint_selector)
        # Separator between module selectors and translator
        sep1 = self._make_vseparator()
        self.hlayout.addWidget(sep1)
        self.hlayout.addWidget(self.trans_selector)
        self.hlayout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )
        self.hlayout.addWidget(self.textlayerSlider)
        self.hlayout.addWidget(self.originalSlider)
        # Separator between sliders and mode toggles
        sep2 = self._make_vseparator()
        self.hlayout.addWidget(sep2)
        self.hlayout.addWidget(self.paintChecker)
        self.hlayout.addWidget(self.texteditChecker)
        self.hlayout.addWidget(self.textblockChecker)
        self.hlayout.setContentsMargins(60, 0, 10, WINDOW_BORDER_WIDTH)

    @staticmethod
    def _make_vseparator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setFixedWidth(8)
        return sep

    def onPaintCheckerPressed(self):
        checked = self.paintChecker.isChecked()
        if checked:
            self.texteditChecker.setChecked(False)
        pcfg.imgtrans_paintmode = checked
        self.paintmode_checkchanged.emit()

    def onTextEditCheckerPressed(self):
        checked = self.texteditChecker.isChecked()
        if checked:
            self.paintChecker.setChecked(False)
        pcfg.imgtrans_textedit = checked
        self.textedit_checkchanged.emit()

    def onTextblockCheckerClicked(self):
        self.textblock_checkchanged.emit()
