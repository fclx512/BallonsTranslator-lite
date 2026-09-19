import os
import os.path as osp
import re
import shutil
import subprocess
import sys
import time
import traceback
from functools import partial
from pathlib import Path
from typing import List, Optional, Tuple, Union
from uuid import uuid4

from qtpy.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QEventLoop,
    QCoreApplication,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand
from qtpy.QtGui import (
    QClipboard,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QCursor,
    QGuiApplication,
    QIcon,
    QImageReader,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QShortcut,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from tqdm import tqdm

from modules import (
    GET_MISSING_MODEL_FILES,
    GET_VALID_INPAINTERS,
    GET_VALID_OCR,
    GET_VALID_TEXTDETECTORS,
    GET_VALID_TRANSLATORS,
    HIDDEN_INPAINTERS,
)
from utils import shared
from utils.batch_versions import BatchVersionStore
from utils.block_actions import (
    ACTION_REGISTRY,
    build_context_lines,
    build_ocr_fix_payload,
    page_data_needs_sync,
    parse_ocr_fix_reply,
)
from utils.block_tags import (
    TAG_REGISTRY,
    directive_instructions,
    has_tag,
    remove_tag,
    set_tag,
)
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
from utils.memory_release import release_memory
from utils.message import create_error_dialog, create_info_dialog
from utils.profile_manager import (
    find_profile,
    load_profiles,
    remember_model_option,
    save_all_profiles,
)
from utils.proj_imgtrans import ProjImgTrans
from utils.text_processing import is_cjk
from utils.textblock import TextAlignment, TextBlock

from . import shared_widget as SW
from .canvas import Canvas
from .configpanel import ConfigPanel, default_keys_for
from .custom_widget import (
    FrameLessMessageBox,
    ImgtransProgressMessageBox,
    MessageBox,
    ProgressMessageBox,
    ViewWidget,
    Widget,
)
from .custom_widget.notification import notification
from .drawing_commands import RunBlkTransCommand
from .drawingpanel import DrawingPanel
from .framelesswindow import FramelessMoveResize, FramelessWindow
from .global_search_widget import GlobalSearchWidget
from .glossary_agent_panel import GlossaryAgentPanel
from .io_thread import ImgSaveThread
from .mainwindowbars import BottomBar, LeftBar, TitleBar
from .misc import (
    QKEY,
    mark_module_selector_status,
    parse_stylesheet,
    set_html_family,
    theme_accent_color,
)
from .module_manager import ModuleManager
from .overlay_modal import OverlayModal
from .region_redetect_tool import RegionRedetectTool
from .scenetext_manager import PasteSrcItemsCommand, SceneTextManager, TextPanel
from .block_action_card import BlockActionCard
from .block_action_runner import BlockActionRunner
from .tag_toolbar import TagToolbar
from .textedit_area import SourceTextEdit, TransTextEdit
from .text_engine.pipeline_formatting import (
    AutoTateChuYokoThread,
    apply_auto_tate_chu_yoko,
)
from .textedit_commands import (
    GlobalReplaceApplier,
    capture_page_generations,
    resolve_blk_item,
)
from .textitem import TextBlkItem
from .update_checker import AboutDialog, CommitUpdateDialog
from .update_dialog import UpdateReleaseDialog
from .update_thread import UpdateCheckThread


class PageListView(QListWidget):
    reveal_file = Signal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("PageListView")
        self.setIconSize(
            QSize(shared.PAGELIST_THUMBNAIL_SIZE, shared.PAGELIST_THUMBNAIL_SIZE)
        )
        # Transparent viewport so leftStackWidget's rounded bg shows through
        self.viewport().setStyleSheet("background: transparent;")

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

    阶段 4 跨页历史：item 引用改存 blk 身份锚点（场景重建后按身份
    重解析 live item，防止重放到脱离场景的隐形对象）；作为组化命令
    捕获涉及页代数并暴露撤销影响面摘要。
    """

    def __init__(self, canvas, data_changes, item_changes=None):
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Advanced Alignment")
        )
        self.canvas = canvas
        # (TextBlock, [old_x, old_y, old_w, old_h], [new_x, new_y, new_w, new_h])
        self.data_changes = list(data_changes)
        # (blk, old_QPointF, new_QPointF) — 跨页锚点，执行期重解析 item
        # （调用方 execute_advanced_align 已按 blk 身份构建）
        self.item_changes = list(item_changes or [])
        # 组化命令（阶段 4 第二批）：涉及页块数摘要 + 多页代数快照
        proj = canvas.imgtrans_proj
        wanted = {id(blk) for blk, _, _ in self.data_changes}
        self._group_pages = {
            pname: sum(1 for b in page if id(b) in wanted)
            for pname, page in proj.pages.items()
        }
        self._group_pages = {
            p: n for p, n in self._group_pages.items() if n
        }
        self.group_page_generations = capture_page_generations(
            proj, self._group_pages.keys()
        )

    def group_undo_summary(self) -> dict:
        """撤销影响面：页名 → 块数（撤销确认弹窗/历史面板摘要用）。"""
        return dict(self._group_pages)

    def _mark_affected_dirty(self):
        """数据/位置已变，非当前页结果图层过期（mark 内部排除当前页）。"""
        proj = self.canvas.imgtrans_proj
        for pname in self._group_pages:
            proj.mark_page_needs_rerender(pname)

    def _apply_data(self, changes):
        for blk, old_br, new_br in changes:
            blk._bounding_rect = list(new_br)

    def _apply_items(self, changes):
        for blk, old_pos, new_pos in changes:
            item = resolve_blk_item(blk)
            if item is None:
                # 锚点不在当前场景（非当前页/块被删）：只落数据
                continue
            item.oldPos = item.pos()
            item.setPos(new_pos)

    def redo(self):
        self._apply_data(self.data_changes)
        self._apply_items(self.item_changes)
        self._mark_affected_dirty()

    def undo(self):
        rev_data = [(blk, new_br, old_br) for blk, old_br, new_br in self.data_changes]
        rev_items = [(blk, new_pos, old_pos) for blk, old_pos, new_pos in self.item_changes]
        self._apply_data(rev_data)
        self._apply_items(rev_items)
        self._mark_affected_dirty()


mainwindow_cls = Widget if shared.HEADLESS else FramelessWindow


def _is_text_input(widget) -> bool:
    """True for widgets where Tab would insert text (source/translation
    fields, search boxes) — the pie menu must not fire there, and neither
    should Tab insert a tab char or move focus.
    """
    from qtpy.QtWidgets import QPlainTextEdit, QTextEdit

    return isinstance(widget, (QTextEdit, QPlainTextEdit))


class MainWindow(mainwindow_cls):
    imgtrans_proj: ProjImgTrans = ProjImgTrans()
    save_on_page_changed = True
    opening_dir = False
    page_changing = False
    translator = None

    restart_signal = Signal()
    create_errdialog = Signal(str, str, str)
    create_infodialog = Signal(dict)

    _notext_dot_icon: "QIcon | None" = None
    _dirty_dot_icon: "QIcon | None" = None

    @staticmethod
    def _make_badged_icon(imgpath: str, thumb_size: int) -> "QIcon":
        """Load an image at thumbnail size and add a green dot badge."""
        reader = QImageReader(imgpath)
        reader.setScaledSize(QSize(thumb_size, thumb_size))
        img = reader.read()
        if img.isNull():
            return QIcon(imgpath)
        pixmap = QPixmap.fromImage(img)
        # Paint green dot badge at top-right corner
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_size = 8
        margin = 2
        # White border ring for contrast
        painter.setBrush(QColor(255, 255, 255))
        r = QRect(pixmap.width() - dot_size - margin, margin, dot_size, dot_size)
        painter.drawEllipse(r.adjusted(-1, -1, 1, 1))
        # Green fill (matches canvas notextLabel)
        painter.setBrush(QColor(39, 174, 96))
        painter.drawEllipse(r)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_dirty_badged_icon(imgpath: str, thumb_size: int) -> "QIcon":
        """Load an image at thumbnail size and add an orange dot badge at
        bottom-right, indicating the page has unrendered batch changes."""
        reader = QImageReader(imgpath)
        reader.setScaledSize(QSize(thumb_size, thumb_size))
        img = reader.read()
        if img.isNull():
            return QIcon(imgpath)
        pixmap = QPixmap.fromImage(img)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_size = 8
        margin = 2
        y = pixmap.height() - dot_size - margin
        x = pixmap.width() - dot_size - margin
        # White border ring for contrast
        painter.setBrush(QColor(255, 255, 255))
        r = QRect(x, y, dot_size, dot_size)
        painter.drawEllipse(r.adjusted(-1, -1, 1, 1))
        # Orange fill for batch-dirty
        painter.setBrush(QColor(230, 126, 34))
        painter.drawEllipse(r)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _get_notext_dot_icon() -> "QIcon":
        """Return a small green dot icon for text-only list items (cached)."""
        if MainWindow._notext_dot_icon is None:
            pixmap = QPixmap(14, 14)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(0, 0, 14, 14)
            painter.setBrush(QColor(39, 174, 96))
            painter.drawEllipse(1, 1, 12, 12)
            painter.end()
            MainWindow._notext_dot_icon = QIcon(pixmap)
        return MainWindow._notext_dot_icon

    @staticmethod
    def _get_dirty_dot_icon() -> "QIcon":
        """Return a small orange dot icon for batch-dirty text-only list items (cached)."""
        if MainWindow._dirty_dot_icon is None:
            pixmap = QPixmap(14, 14)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(0, 0, 14, 14)
            painter.setBrush(QColor(230, 126, 34))
            painter.drawEllipse(1, 1, 12, 12)
            painter.end()
            MainWindow._dirty_dot_icon = QIcon(pixmap)
        return MainWindow._dirty_dot_icon

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
        # Live run dialog, so an asynchronous translator load can refresh the
        # language selectors it is showing (None while the dialog is closed).
        self._run_dialog = None


        self.setupThread()
        # 字体枚举必须先于任何 FormatEditorPanel 消费方（如 setupUi 中的
        # GlobalSearchWidget 启动即 set_format 填字体下拉），否则
        # ALL_FONT_FAMILIES 尚为空，下拉只剩补插的默认字体一项
        shared.init_font_list()
        self.setupUi()
        self.setupConfig()
        self.setupShortcuts()
        self.setupPieMenu()
        self.setupRegisterWidget()
        # self.showMaximized()
        # Set a reasonable default geometry before maximizing, so that
        # restoring from maximized (e.g. Win+Down or drag from title bar)
        # returns to a usable size rather than Qt's tiny default.
        screen = QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableSize()
            self.resize(avail.width() * 4 // 5, avail.height() * 4 // 5)
        FramelessMoveResize.toggleMaxState(self)
        self.setAcceptDrops(True)

        self._temp_project_dirs: set = set()  # drag-imported temp project dirs

        if open_dir != "" and osp.exists(open_dir):
            self.OpenProj(open_dir)
        elif pcfg.open_recent_on_startup:
            if len(self.leftBar.recent_proj_list) > 0:
                proj_dir = self.leftBar.recent_proj_list[0]
                if osp.exists(proj_dir):
                    self.OpenProj(proj_dir)

        if shared.HEADLESS:
            self.run_batch(**exec_args)

        if not shared.HEADLESS and pcfg.check_update_on_startup:
            # Defer startup update checks until the event loop can paint progress.
            QTimer.singleShot(
                500,
                lambda: self.check_for_updates(manual=False),
            )

        # Windows: apply font & set titlebar

    def setStyleSheet(self, styleSheet: str) -> None:
        self.imgtrans_progress_msgbox.setStyleSheet(styleSheet)
        return super().setStyleSheet(styleSheet)

    def setupThread(self):
        self.imsave_thread = ImgSaveThread()
        self.update_thread = UpdateCheckThread()
        self.update_thread.progress_changed.connect(self.on_update_progress_changed)
        self.update_thread.update_finished.connect(self.on_update_finished)
        self.update_thread.update_failed.connect(self.on_update_failed)
        self.update_progress_msgbox = ProgressMessageBox(
            self.tr("Updating: "), False, self
        )
        self._update_progress_visible = False
        self.auto_tate_chu_yoko_thread = AutoTateChuYokoThread(self)
        self.auto_tate_chu_yoko_progress = ProgressMessageBox("", True, self)
        self.auto_tate_chu_yoko_thread.progress_changed.connect(
            self.auto_tate_chu_yoko_progress.updateTaskProgress
        )
        self.auto_tate_chu_yoko_thread.processing_finished.connect(
            self.on_auto_tate_chu_yoko_processing_finished
        )
        self.auto_tate_chu_yoko_progress.stop_clicked.connect(
            self.auto_tate_chu_yoko_thread.request_stop
        )
        self.auto_tate_chu_yoko_progress.showed.connect(
            self.on_imgtrans_progressbox_showed
        )

    def resetStyleSheet(self, reverse_icon: bool = False):
        theme = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
        self.setStyleSheet(parse_stylesheet(theme, reverse_icon))
        # 富文本链接色来自 QPalette::Link(不受 QSS 管辖):Win10 原生样式
        # 恒为浅色调色板,深底上深蓝链接不可读——显式对齐主题强调色。
        pal = QApplication.instance().palette()
        link = theme_accent_color(theme)
        pal.setColor(QPalette.ColorRole.Link, link)
        QApplication.instance().setPalette(pal)

    def setupUi(self):
        screen_size = QGuiApplication.primaryScreen().geometry().size()
        self.setMinimumWidth(screen_size.width() // 2)

        self.centralStackWidget = QStackedWidget(self)

        self.configPanel = ConfigPanel(self)
        self.configPanel.check_update.connect(self.check_for_updates)
        self.configPanel.check_commit_update.connect(self.show_commit_update_dialog)
        self.configPanel.apply_auto_tate_chu_yoko_requested.connect(
            self.apply_auto_tate_chu_yoko_to_project
        )

        self.leftBar = LeftBar(self)
        self.leftBar.showPageListLabel.clicked.connect(self.pageLabelStateChanged)
        self.leftBar.configChecked.connect(self.setupConfigUI)
        self.leftBar.globalSearchChecker.clicked.connect(self.on_set_gsearch_widget)
        self.leftBar.workbenchChecker.clicked.connect(self.on_set_workbench_widget)
        self.leftBar.open_dir.connect(self.OpenProj)
        self.leftBar.open_json_proj.connect(self.openJsonProj)
        self.leftBar.open_images.connect(self.openImages)
        self.leftBar.save_proj.connect(self.manual_save)
        self.leftBar.save_proj_as.connect(self.saveProjectAs)
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

        # Left panels: PageList & GlobalSearch are now embedded in the main
        # layout so they push the canvas right when shown, not overlay on top.
        self.leftStackWidget = QStackedWidget(self)
        self.leftStackWidget.setObjectName("leftStackWidget")
        self.leftStackWidget.addWidget(self.pageList)
        self.leftStackWidget.setVisible(False)

        self.global_search_widget = GlobalSearchWidget(self)
        self.global_search_widget.setVisible(False)
        self.global_search_widget.req_update_pagetext.connect(
            self.on_req_update_pagetext
        )
        self.global_search_widget.pages_dirtied.connect(self.updatePageList)
        self.global_search_widget.search_tree.result_item_clicked.connect(
            self.on_search_result_item_clicked
        )

        # Workbench (泛用工作台): embedded left panel (widened GlobalSearch
        # sibling), pushed into the same H layout slot. Hosts the four batch
        # cleanup tasks plus the glossary/story drafts (规划 D20/D25).
        self.glossary_workbench = GlossaryAgentPanel(self.imgtrans_proj, self)
        self.glossary_workbench.setVisible(False)
        self.glossary_workbench.jump_requested.connect(self.on_workbench_jump)
        self.glossary_workbench.rollback_requested.connect(
            self.on_workbench_rollback
        )

        self.titleBar = TitleBar(self)
        self.titleBar.closebtn_clicked.connect(self.on_closebtn_clicked)
        self.titleBar.display_lang_changed.connect(self.on_display_lang_changed)
        self.titleBar.launch_notext_tool.connect(self.on_launch_notext_tool)
        self.bottomBar = BottomBar(self)
        self.bottomBar.textedit_checkchanged.connect(self.setTextEditMode)
        self.bottomBar.paintmode_checkchanged.connect(self.setPaintMode)
        self.bottomBar.textblock_checkchanged.connect(self.setTextBlockMode)
        self.bottomBar.paintmode_checkchanged.connect(self._sync_view_mode_actions)
        self.bottomBar.textedit_checkchanged.connect(self._sync_view_mode_actions)

        mainHLayout = QHBoxLayout()
        mainHLayout.addWidget(self.leftBar)
        mainHLayout.addWidget(self.leftStackWidget)
        mainHLayout.addWidget(self.global_search_widget)
        mainHLayout.addWidget(self.glossary_workbench)
        mainHLayout.addSpacing(5)
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
        self.canvas.drop_images.connect(self.openImages)
        self.canvas.copy_src_signal.connect(self.on_copy_src)
        self.canvas.paste_src_signal.connect(self.on_paste_src)
        # 跨页撤销/历史面板跳转的页切换（阶段 4）
        self.canvas.page_jump_requested.connect(self.on_undo_page_jump_requested)
        self.canvas.rerender_dirty_pages_requested.connect(
            self._on_group_undo_rerender_requested
        )

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
        self.textPanel.sourceBtn.checkStateChanged.connect(
            self.show_source_text
        )
        self.textPanel.transBtn.checkStateChanged.connect(
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

        # 选中跟随标签工具栏（挂画布区容器浮层，不参与布局；批次 B 主入口）
        self.tagToolbar = TagToolbar(self.centralStackWidget, self.canvas)
        self.canvas.incanvas_selection_changed.connect(
            self.tagToolbar.sync_from_canvas
        )
        # 框级 AI 动作（批次 C）：执行器 + 就地确认卡片
        self.blockActionRunner = BlockActionRunner(self)
        self.blockActionCard = BlockActionCard(
            self.centralStackWidget, self.canvas
        )
        self.tagToolbar.action_requested.connect(self.on_block_action_requested)
        self.blockActionRunner.finished.connect(self.on_block_action_finished)
        self.blockActionRunner.failed.connect(self.on_block_action_failed)
        self.blockActionCard.apply_clicked.connect(self.on_block_action_apply)
        self.blockActionCard.cancelled.connect(self.on_block_action_cancel)
        self.blockActionCard.retry_requested.connect(self.on_block_action_retry)

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
        right_layout.setContentsMargins(0, 0, 5, 0)
        right_layout.setSpacing(5)

        # Middle: canvas (stretches to fill remaining space)
        right_layout.addWidget(self.canvas.gv, 1)

        # Right: trans stack panel (fixed width)
        right_layout.addWidget(self.rightComicTransStackPanel)
        self.rightComicTransStackPanel.setFixedWidth(360)

        self.centralStackWidget.addWidget(self._rightPanelContainer)

        # Config panel as independent OS window (Qt.Tool — no taskbar
        # entry).  Scrim only dims centralStackWidget; left bar / bottom
        # bar / title bar stay interactive.
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

        # Left panels are embedded in the layout – no OverlaySlider needed.
        # Width animation is driven by _animate_panel_for / _animate_panel_hide.
        self._panel_anim: dict[int, QTimer] = {}
        self._panel_anim_data: dict[int, dict] = {}

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
            self.bottomBar.trans_selector.setSelectedValue(translator.name)
            self.bottomBar.trans_selector.setTranslatorMetadata(
                translator.name,
                translator.supported_src_list,
                translator.supported_tgt_list,
                translator.lang_source,
                translator.lang_target,
            )
            self.configPanel.trans_config_panel.finishSetTranslator(translator)
            LOGGER.info("Translator set to {}".format(name))
            if self._run_dialog is not None:
                self._sync_run_dialog_translator(self._run_dialog)
        else:
            LOGGER.error("invalid translator")

    def on_enable_module(self, idx, checked):
        if idx == 0:
            pcfg.module.enable_detect = checked
        elif idx == 1:
            pcfg.module.enable_ocr = checked
        elif idx == 2:
            pcfg.module.enable_translate = checked
        elif idx == 3:
            pcfg.module.enable_inpaint = checked
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
        self.bottomBar.inpaint_selector.selector.addItems(
            [m for m in GET_VALID_INPAINTERS() if m not in HIDDEN_INPAINTERS]
        )
        for module_type, selector in (
            ("textdetector", self.bottomBar.textdet_selector.selector),
            ("ocr", self.bottomBar.ocr_selector.selector),
            ("translator", self.bottomBar.trans_selector.selector),
            ("inpainter", self.bottomBar.inpaint_selector.selector),
        ):
            mark_module_selector_status(selector, module_type)
        self.bottomBar.inpaint_selector.selector.currentTextChanged.connect(
            self.on_inpaint_changed
        )
        self.bottomBar.trans_selector.cfg_clicked.connect(self.to_trans_config)
        self.bottomBar.trans_selector.selector.currentTextChanged.connect(
            self.on_trans_changed
        )
        self.bottomBar.textdet_selector.cfg_clicked.connect(self.to_detect_config)
        self.bottomBar.inpaint_selector.cfg_clicked.connect(self.to_inpaint_config)
        self.bottomBar.ocr_selector.cfg_clicked.connect(self.to_ocr_config)
        self.bottomBar.ocr_selector.selector.currentTextChanged.connect(
            self.on_ocr_changed
        )
        # Always show all pipeline stage selectors (upstream behavior: the old
        # cramped layout hid disabled stages; the new layout has room for all.)
        self.bottomBar.textdet_selector.setVisible(True)
        self.bottomBar.ocr_selector.setVisible(True)
        self.bottomBar.trans_selector.setVisible(True)
        self.bottomBar.inpaint_selector.setVisible(True)

        # Source / target languages are chosen in the bottom bar or in the run
        # dialog (the settings page no longer carries them).

        # Bottom-bar translator language submenus.
        self.bottomBar.trans_selector.src_selector.currentTextChanged.connect(
            self.on_trans_src_changed
        )
        self.bottomBar.trans_selector.tgt_selector.currentTextChanged.connect(
            self.on_trans_tgt_changed
        )

        # Bottom-bar translator model submenu (active profile's model list).
        self.bottomBar.trans_selector.model_menu_provider = (
            self._trans_model_menu_data
        )
        self.bottomBar.trans_selector.model_changed.connect(
            self.on_trans_model_changed
        )

        self.drawingPanel.maskTransperancySlider.setValue(
            int(pcfg.mask_transparency * 100)
        )
        self.leftBar.initRecentProjMenu(pcfg.recent_proj_list)
        # 启动时默认关闭页面列表，不还原上一次的配置状态
        pcfg.show_page_list = False
        self.leftBar.showPageListLabel.setChecked(False)
        self.updatePageList()
        self.leftBar.save_config.connect(self.save_config)
        self.setupImgTransUI()
        self.st_manager.formatpanel.global_format = pcfg.global_fontformat
        self.st_manager.formatpanel.set_active_format(pcfg.global_fontformat)

        self.rightComicTransStackPanel.setHidden(True)
        self.st_manager.setTextEditMode(False)
        self.textPanel.transBtn.setCheckState(pcfg.show_trans_text)
        self.textPanel.sourceBtn.setCheckState(pcfg.show_source_text)
        self.show_trans_text(pcfg.show_trans_text)
        self.show_source_text(pcfg.show_source_text)

        # 启动不自动展开窄栏浮层面板：清掉上次会话的开合记忆，浮层
        # 只随窄栏图标手动开（on_textpanel_visibility 首次可见时按
        # pcfg.*_dock_open 复活上次打开的浮层）。软键盘的开合记忆就是
        # 它自己的功能开关 pcfg.symbol_keyboard_enabled——同属"不在启动
        # 时自动生效"一类，一并清掉；该字段在 install_symbol_launcher
        # （本段之前）已写进图标勾选态，故还要把图标复位，否则会出现
        # 「图标亮着但开关是关的」。复位走 toggled，槽里对 None 的
        # symbol_dock 是空操作。
        for flag in (
            "annotation_dock_open",
            "emphasis_dock_open",
            "transform_dock_open",
            "history_dock_open",
            "inpaint_history_dock_open",
            "symbol_keyboard_enabled",
        ):
            setattr(pcfg, flag, False)
        _symbol_launcher = self.textPanel.formatpanel.symbol_launcher
        if _symbol_launcher is not None and _symbol_launcher.isChecked():
            _symbol_launcher.setChecked(False)

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
        # 手动释放内存（设置页「释放内存」）：卸载模型 → 销毁 CUDA 上下文 → 交回
        # 工作集。接线放在这里而不是 module_manager 内部，是为了顺带挡掉"还有后台
        # CUDA 活在跑"的状态（见 _cuda_work_in_progress）。
        self.configPanel.release_memory.connect(self.on_release_memory)
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

        # 区域再检测（底部栏开关 + 画布拉框手势）：单页单手势，走画布撤销栈，
        # 与批量的备份版本机制（utils/batch_versions.py）无关。
        self.region_redetect_tool = RegionRedetectTool(
            self.imgtrans_proj, self.canvas, self.st_manager, module_manager
        )
        self.canvas.region_redetect_rect.connect(
            self.region_redetect_tool.request
        )
        self.bottomBar.redetect_checkchanged.connect(
            self.on_redetect_mode_changed
        )

        self.leftBar.run_imgtrans_clicked.connect(self.run_imgtrans)

        self.titleBar.darkModeAction.setChecked(pcfg.darkmode)
        self.titleBar.overflowAction.setChecked(pcfg.overflow_mode)
        self.titleBar.seqBadgeAction.setChecked(pcfg.show_seq_badge)
        self.titleBar.clipOverflowAction.setChecked(pcfg.clip_text_overflow)

        self.drawingPanel.set_config(pcfg.drawpanel)
        self.drawingPanel.initDLModule(module_manager)

        self.global_search_widget.imgtrans_proj = self.imgtrans_proj
        self.global_search_widget.set_page_widget_lists(
            self.st_manager.pairwidget_list, self.st_manager.textblk_item_list
        )
        self.global_search_widget.replace_finished.connect(
            self.on_global_replace_finished
        )
        self.global_search_widget.replace_preparing.connect(
            self.on_global_replace_preparing
        )
        self.global_search_widget.batch_rollback_requested.connect(
            self.on_batch_rollback
        )

        self.configPanel.setupConfig()
        self.configPanel.save_config.connect(self.save_config)
        self.configPanel.reload_textstyle.connect(self.load_textstyle_from_proj_dir)
        self.configPanel.font_exclusion_changed.connect(
            self.refresh_font_list_exclusion
        )
        self.configPanel.shortcuts_changed.connect(self.refreshShortcuts)
        self.configPanel.presets_changed.connect(self._on_presets_changed)
        self.configPanel.seq_badge_changed.connect(self._on_seq_badge_changed)
        self.configPanel.tag_badge_changed.connect(self._on_tag_badge_changed)
        self.configPanel.tag_toolbar_changed.connect(
            self._on_tag_toolbar_changed
        )
        self.configPanel.clip_overflow_changed.connect(self._on_clip_overflow_changed)
        self.titleBar.seq_badge_trigger.connect(self.on_seq_badge_menu_toggled)
        self.titleBar.clip_overflow_trigger.connect(
            self.on_clip_overflow_menu_toggled
        )
        # 使用过滤后的字体列表（排除用户已隐藏的字体）
        familybox = self.textPanel.formatpanel.familybox
        filtered = shared.get_filtered_font_list(pcfg.excluded_fonts)
        if familybox.count() == 0 and filtered:
            familybox.update_font_list(filtered)

        if pcfg.imgtrans_textedit:
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
        # 重跑归并枚举：让 FONT_PS_NAMES 为本会话新增的「已精简」别名
        # 补上借自规范名的 PS 记录
        shared.init_font_list()
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

    def _on_seq_badge_changed(self):
        """Keep View menu + canvas badges in sync with the settings toggle."""
        self.titleBar.seqBadgeAction.setChecked(pcfg.show_seq_badge)
        if not self.canvas:
            return
        for item in self.canvas.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                item.refresh_seq_badge()

    def _on_tag_badge_changed(self):
        """Keep canvas tag badges in sync with the settings toggle."""
        if not self.canvas:
            return
        for item in self.canvas.textLayer.childItems():
            if isinstance(item, TextBlkItem):
                item.refresh_tag_badge()

    def on_tag_badge_menu_toggled(self, checked: bool):
        pcfg.show_tag_badge = checked
        self.configPanel.tag_badge_checker.blockSignals(True)
        self.configPanel.tag_badge_checker.setChecked(checked)
        self.configPanel.tag_badge_checker.blockSignals(False)
        self._on_tag_badge_changed()
        self.save_config()

    def _on_tag_toolbar_changed(self):
        """关掉时立即隐藏；再开时若有选中经 sync 复现。"""
        if hasattr(self, "tagToolbar"):
            self.tagToolbar.sync_from_canvas()

    def on_tag_toolbar_menu_toggled(self, checked: bool):
        pcfg.show_tag_toolbar = checked
        self.configPanel.tag_toolbar_checker.blockSignals(True)
        self.configPanel.tag_toolbar_checker.setChecked(checked)
        self.configPanel.tag_toolbar_checker.blockSignals(False)
        self._on_tag_toolbar_changed()
        self.save_config()

    def on_tag_shortcut(self, tag_id: str):
        """标签翻转快捷键：多选翻转语义（非全员带→全挂，否则全摘）。"""
        items = self.canvas.selected_text_items()
        if not items or not self.canvas.textEditMode():
            return
        checked = not all(has_tag(it.blk, tag_id) for it in items)
        for it in items:
            if checked:
                set_tag(it.blk, tag_id, "manual")
            else:
                remove_tag(it.blk, tag_id)
            it.refresh_tag_badge()
        self.canvas.setProjSaveState(True)
        if hasattr(self, "tagToolbar"):
            self.tagToolbar.sync_from_canvas()

    # ── 框级 AI 动作（批次 C）：无工具单轮 → 就地确认卡 → 显式应用 ──

    def on_block_action_requested(self, action_id: str):
        items = self.canvas.selected_text_items()
        if len(items) != 1:
            return
        self.start_block_action(action_id, items[0])

    def _sync_block_data(self) -> None:
        """框级动作前置：把数据层按画布重建（数据一致性修复）。

        合并/撤销只改视觉层，`ui/scenetext_manager.py::updateTextBlkList`
        要等保存等时机才让 `proj.pages` 跟上。动作读的是数据层，不先对齐
        就会拿到合并前的子块（实测：合并块重译只返回第一个子块的内容），
        裁图也会取错外框。判据见 `utils/block_actions.py::page_data_needs_sync`。
        """
        if not page_data_needs_sync(
            self.imgtrans_proj.current_block_list(),
            self.st_manager.textblk_item_list,
        ):
            return
        self.st_manager.updateTextBlkList()

    def _block_action_context_items(
        self, src_text: str, context_lines, instructions, hint: str
    ):
        """本次动作实际送出的确定性信息（卡片「上下文包」逐项列出）。"""
        items = [(self.tr("Source sent"), src_text)]
        if context_lines:
            items.append(
                (self.tr("Neighbour blocks (±2)"), "\n".join(context_lines))
            )
        if instructions:
            items.append(
                (self.tr("Active tag instructions"), "\n".join(instructions))
            )
        if hint:
            items.append((self.tr("Your extra requirement"), hint))
        items.append(
            (
                self.tr("Also injected"),
                self.tr(
                    "Other blocks on this page, earlier pages and the story "
                    "synopsis (assembled by the translator)."
                ),
            )
        )
        return items

    def start_block_action(
        self, action_id: str, blkitem, hint: str = ""
    ) -> None:
        action = ACTION_REGISTRY.get(action_id)
        if action is None or self.blockActionRunner.is_busy():
            return
        # 先对齐数据层，再读原文/裁图/邻居（否则合并块读到的是合并前的子块）
        self._sync_block_data()
        blk = blkitem.blk
        blk_idx = blkitem.idx
        pagename = self.imgtrans_proj.current_img
        blk_list = self.imgtrans_proj.current_block_list() or []
        context_lines = build_context_lines(blk_list, blk_idx)
        src_text = blk.get_text()
        instructions = directive_instructions(blk)
        context_items = self._block_action_context_items(
            src_text, context_lines, instructions, hint
        )

        if action.kind == "ocr_fix":
            from utils.profile_manager import (
                get_vision_profiles,
                profile_usage_hint,
                resolve_profile,
            )

            # 取法：OCR 模块选定的 → 翻译器在用的 → 全局激活的 → 第一个可用
            # 的视觉 profile（utils/profile_manager.py::resolve_profile 的池
            # 过滤版本）。先前版本只认翻译器的 active profile，而它自己可能
            # 就是坏的，于是框级动作在第一步就崩——前置条件检查放在这里。
            ocr_module = self.module_manager.ocr
            preferred = self._module_profile_name(
                ocr_module
            ) or self._translator_active_profile(self.module_manager.translator)
            profile = resolve_profile(preferred, vision=True)
            if profile is None:
                if get_vision_profiles():
                    notification.toast(
                        self.tr(
                            "Vision profile is not ready. Configure it in Model Management."
                        )
                        + f" ({profile_usage_hint(preferred)})",
                        anchor="bottom-left",
                        key="block_action",
                    )
                else:
                    notification.toast(
                        self.tr(
                            "No vision API profile found. Configure one in Model Management first."
                        ),
                        anchor="bottom-left",
                        key="block_action",
                    )
                return
            # 载荷在主线程组装：按行透视纠正拼图 + 逐行现有 OCR 文本
            try:
                payload = build_ocr_fix_payload(
                    self.imgtrans_proj.read_img(pagename),
                    blk,
                    context_lines,
                    hint,
                )
            except Exception as e:
                LOGGER.error(f"Block action {action_id} payload failed: {e}")
                notification.toast(
                    self.tr("Cannot prepare the block image for OCR fix.")
                    + f" ({e})",
                    anchor="bottom-left",
                    key="block_action",
                )
                return
            LOGGER.info(
                f"Block action {action_id} uses vision profile: "
                f"{profile.get('name', '')}"
            )
            self._block_action_ctx = (action_id, blk_idx)
            self._block_action_payload = payload
            self.blockActionCard.begin(
                action.name,
                blkitem,
                src_text,
                preview_b64=payload.preview_b64,
                line_texts=payload.line_texts,
                context_items=context_items,
                per_line=payload.per_line,
            )
            self.blockActionRunner.start_ocr_fix(
                pagename, blk_idx, profile, payload.messages
            )
        else:
            translator = self.module_manager.translator
            if translator is None or not hasattr(
                translator, "translate_with_context"
            ):
                notification.toast(
                    self.tr(
                        "Retranslate is not available for the current translator module."
                    ),
                    anchor="bottom-left",
                    key="block_action",
                )
                return
            self._block_action_ctx = (action_id, blk_idx)
            self._block_action_payload = None
            self.blockActionCard.begin(
                action.name,
                blkitem,
                src_text,
                context_items=context_items,
            )
            self.blockActionRunner.start_retranslate(
                translator,
                self.imgtrans_proj,
                pagename,
                blk_idx,
                src_text,
                tag_instructions=instructions,
                hint=hint,
            )

    def on_block_action_retry(self, hint: str) -> None:
        """「补充要求」重跑：按当前选中块重新组装载荷（数据可能已变）。"""
        blkitem = self.blockActionCard._blkitem
        action_id, _ = getattr(self, "_block_action_ctx", (None, None))
        if blkitem is None or action_id is None:
            return
        self.start_block_action(action_id, blkitem, hint=hint)

    def on_block_action_finished(
        self, action_id: str, pagename: str, blk_idx: int, result: str
    ):
        """结果回填：按 (pagename, blk_idx) 寻址，切页后的迟到结果丢弃。"""
        if pagename != self.imgtrans_proj.current_img:
            return
        ctx = getattr(self, "_block_action_ctx", None)
        if ctx != (action_id, blk_idx) or not self.blockActionCard.isVisible():
            return
        # 逐行回复能对齐行才给逐行取舍，否则退化为整块草稿（不猜对齐）
        line_corrections = None
        payload = getattr(self, "_block_action_payload", None)
        if payload is not None and payload.per_line:
            line_corrections = (
                parse_ocr_fix_reply(result, len(payload.line_texts)) or None
            )
        self.blockActionCard.show_proposal(result, line_corrections)

    def on_block_action_failed(
        self, action_id: str, pagename: str, blk_idx: int, error: str
    ):
        LOGGER.error(f"Block action {action_id} failed: {error}")
        if pagename != self.imgtrans_proj.current_img:
            return
        ctx = getattr(self, "_block_action_ctx", None)
        if ctx != (action_id, blk_idx) or not self.blockActionCard.isVisible():
            return
        self.blockActionCard.show_error(error)

    def on_block_action_apply(self, text: str):
        """显式「应用」：写回进全局撤销栈 + 消除已消费的疑点标签。"""
        from .textedit_commands import ApplyBlockTextCommand

        blkitem = self.blockActionCard._blkitem
        if blkitem is None:
            self.blockActionCard.close_card()
            return
        action_id, _ = getattr(self, "_block_action_ctx", (None, None))
        action = ACTION_REGISTRY.get(action_id)
        pairw = self.st_manager.pairwidget_list[blkitem.idx]
        field = "source" if (action and action.kind == "ocr_fix") else "translation"
        self.canvas.push_undo_command(
            ApplyBlockTextCommand(blkitem, pairw, field, text)
        )
        if action:
            for tid in action.consumes:
                # 疑点标签确认后消除；指示标签是持久属性，保留
                if TAG_REGISTRY[tid].nature == "doubt":
                    remove_tag(blkitem.blk, tid)
            blkitem.refresh_tag_badge()
        self.canvas.setProjSaveState(True)
        self.blockActionCard.close_card()
        self.tagToolbar.sync_from_canvas()

    def on_block_action_cancel(self):
        if self.blockActionRunner.is_busy():
            self.blockActionRunner.cancel()
        self.blockActionCard.close_card()

    def cancel_block_action_on_page_switch(self):
        """切页保护（§8.9）：提案未确认即无写入，直接取消并提示。"""
        if self.blockActionCard.isVisible():
            was_busy = self.blockActionRunner.is_busy()
            self.on_block_action_cancel()
            if was_busy:
                notification.toast(
                    self.tr("Block action cancelled (page switched)."),
                    anchor="bottom-left",
                    key="block_action",
                )

    def jump_to_tagged_block(self, backward: bool = False):
        """跳到上/下一个带标签块（全书范围，§8.6 校对闭环：扫过去→跳过去→处理掉）。
        起点为当前选中块（无选中则当前页端部），到头回绕。"""
        proj = self.imgtrans_proj
        entries = []
        for pagename in sorted(
            proj.pages.keys(), key=lambda n: proj._pagename2idx.get(n, 0)
        ):
            for idx, blk in enumerate(proj.pages[pagename]):
                if blk.tags:
                    entries.append(
                        (proj._pagename2idx.get(pagename, 0), idx, pagename)
                    )
        if not entries:
            return
        cur_page = proj.current_img
        sel = self.canvas.selected_text_items()
        pidx = proj._pagename2idx.get(cur_page, 0)
        if sel:
            bidx = sel[0].idx
        else:
            bidx = (
                len(proj.current_block_list()) if backward else -1
            )
        pos = (pidx, bidx)
        if backward:
            before = [e for e in entries if (e[0], e[1]) < pos]
            target = max(before, default=entries[-1])
        else:
            after = [e for e in entries if (e[0], e[1]) > pos]
            target = min(after, default=entries[0])
        self._on_stylemgr_navigate(target[2], target[1])

    def _on_clip_overflow_changed(self):
        """Keep the View menu toggle in sync with the settings panel."""
        self.titleBar.clipOverflowAction.setChecked(pcfg.clip_text_overflow)

    def on_seq_badge_menu_toggled(self, checked: bool):
        pcfg.show_seq_badge = checked
        self.configPanel.seq_badge_checker.blockSignals(True)
        self.configPanel.seq_badge_checker.setChecked(checked)
        self.configPanel.seq_badge_checker.blockSignals(False)
        self._on_seq_badge_changed()
        self.save_config()

    def on_clip_overflow_menu_toggled(self, checked: bool):
        pcfg.clip_text_overflow = checked
        self.configPanel.clip_overflow_checker.blockSignals(True)
        self.configPanel.clip_overflow_checker.setChecked(checked)
        self.configPanel.clip_overflow_checker.blockSignals(False)
        self.save_config()

    def _sync_view_mode_actions(self):
        """Mirror bottom-bar paint/text-edit mode state onto View menu actions."""
        self.titleBar.drawBoardAction.setChecked(
            self.bottomBar.paintChecker.isChecked()
        )
        self.titleBar.texteditAction.setChecked(
            self.bottomBar.texteditChecker.isChecked()
        )

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
                # Window not yet shown — set final width directly,
                # no animation needed. Layout handles positioning.
                self.leftStackWidget.setCurrentWidget(self.pageList)
                self.leftStackWidget.setFixedWidth(self.PAGE_LIST_WIDTH)
                self.leftStackWidget.setVisible(True)
        elif not show and is_visible:
            self._hidePageListOverlay()

    def setupConfigUI(self):
        self._showConfigOverlay()

    def _is_canvas_mode(self) -> bool:
        """True when canvas is active (config overlay not visible)."""
        return not (hasattr(self, "configPanel") and self.configPanel.isVisible())

    def _showConfigOverlay(self):
        self._configModal.show()
        self.configPanel._installOutsideClickFilter()

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
        fsm.pages_dirtied.connect(self.updatePageList)
        fsm.data_committed.connect(self._sync_and_commit_project)

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
            if hasattr(self, "blockActionCard"):
                self.cancel_block_action_on_page_switch()
            self.st_manager.formatpanel.resolve_text_transform_edits_for_page_change()
            self.canvas.commit_edit_sessions()
            if self.save_on_page_changed:
                self.conditional_save()
            proj.set_current_img(pagename)
            # 阶段 4 跨页历史：文本栈不清，仅清页级绘制栈
            self.canvas.prepare_page_switch()
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

    # ── Layout-based panel animation (push canvas, do not overlay) ──

    PAGE_LIST_WIDTH = 250
    SEARCH_WIDTH = 300
    # Workbench (GlossaryAgentPanel) width — D24: sized so the 100%-scale
    # approval crops fit without scrolling (p90 group crop ≈ 280x288 px after
    # the D31 expansion). Kept at 460; only this constant changes if it turns
    # out too small, the panel itself is width-adaptive.
    WORKBENCH_WIDTH = 460

    def _animate_panel_width(self, widget, target_max_w, on_finished=None):
        """Animate a panel's width between collapsed and expanded.

        Uses setFixedWidth() to override layout sizing during the animation,
        keeping the panel at the animated width.  At the end of expansion the
        fixed-width constraint stays (the panel locks to its natural width);
        at the end of collapse the widget is hidden and the constraint is
        released.
        """
        wid = id(widget)

        # Cancel any running animation on this widget
        if wid in self._panel_anim:
            self._panel_anim[wid].stop()
        anim_data = self._panel_anim_data.pop(wid, {})
        if anim_data and "timer" in anim_data:
            anim_data["timer"].stop()

        # Skip if already at target width (defensive — prevents bounce)
        if abs(widget.width() - target_max_w) < 2 and (
            widget.isVisible() if target_max_w > 0 else not widget.isVisible()
        ):
            return

        expanding = target_max_w > 0

        # No-animation mode — jump straight to final state
        if pcfg.animation_fps < 0:
            if expanding:
                widget.setFixedWidth(target_max_w)
                widget.setVisible(True)
            else:
                widget.setVisible(False)
            if on_finished:
                on_finished()
            return

        # Determine start width
        if expanding:
            from_w = widget.width() if widget.isVisible() else 0
            # Jump to "almost collapsed" before expanding
            widget.setFixedWidth(max(from_w, 1))
            widget.setVisible(True)
        else:
            from_w = widget.width()

        timer = QTimer(self)
        timer.setTimerType(Qt.TimerType.PreciseTimer)

        data = {
            "widget": widget,
            "timer": timer,
            "elapsed": QElapsedTimer(),
            "from_w": from_w,
            "to_w": target_max_w if expanding else 1,
            "duration": 350,
            "expanding": expanding,
            "on_finished": on_finished,
        }

        def tick():
            d = self._panel_anim_data.get(wid)
            if not d:
                return
            elapsed = d["elapsed"].elapsed()
            progress = min(elapsed / d["duration"], 1.0)
            eased = QEasingCurve(QEasingCurve.Type.InOutExpo).valueForProgress(progress)
            w = int(round(d["from_w"] + (d["to_w"] - d["from_w"]) * eased))
            d["widget"].setFixedWidth(w)
            if progress >= 1.0:
                timer.stop()
                self._panel_anim.pop(wid, None)
                self._panel_anim_data.pop(wid, None)
                if d["expanding"]:
                    # Lock to final natural width
                    d["widget"].setFixedWidth(d["to_w"])
                else:
                    # Fully collapsed — hide and release constraint
                    d["widget"].setVisible(False)
                if d["on_finished"]:
                    d["on_finished"]()

        timer.timeout.connect(tick)
        fps = pcfg.animation_fps
        interval = int(round(1000.0 / fps)) if fps > 0 else 8
        timer.start(interval)
        data["elapsed"].start()
        self._panel_anim[wid] = timer
        self._panel_anim_data[wid] = data

    def _showSearchOverlay(self):
        self._hideWorkbenchPanel()
        self.global_search_widget.setFocus()
        self._animate_panel_width(self.global_search_widget, self.SEARCH_WIDTH)

    def _hideSearchOverlay(self):
        self._animate_panel_width(
            self.global_search_widget, 0, on_finished=self._on_search_hidden
        )

    def _on_search_hidden(self):
        if self.leftBar.globalSearchChecker.isChecked():
            self.leftBar.globalSearchChecker.setChecked(False)

    def _showPageListOverlay(self):
        self._hideWorkbenchPanel()
        self.leftStackWidget.setCurrentWidget(self.pageList)
        self._animate_panel_width(self.leftStackWidget, self.PAGE_LIST_WIDTH)

    def _hidePageListOverlay(self):
        self._animate_panel_width(
            self.leftStackWidget, 0, on_finished=self._on_page_list_hidden
        )

    def _on_page_list_hidden(self):
        if self.leftBar.showPageListLabel.isChecked():
            self.leftBar.showPageListLabel.setChecked(False)

    def _showWorkbenchPanel(self):
        self.glossary_workbench.setFocus()
        self._animate_panel_width(self.glossary_workbench, self.WORKBENCH_WIDTH)

    def _hideWorkbenchPanel(self):
        if not self.glossary_workbench.isVisible():
            return
        self._animate_panel_width(
            self.glossary_workbench, 0, on_finished=self._on_workbench_hidden
        )

    def _on_workbench_hidden(self):
        if self.leftBar.workbenchChecker.isChecked():
            self.leftBar.workbenchChecker.setChecked(False)

    def on_set_workbench_widget(self):
        setup = self.leftBar.workbenchChecker.isChecked()
        if setup:
            self._hidePageListOverlay()
            self.leftBar.showPageListLabel.setChecked(False)
            self._hideSearchOverlay()
            self.leftBar.globalSearchChecker.setChecked(False)
            self._showWorkbenchPanel()
        else:
            self._hideWorkbenchPanel()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._configModal.resize()

    def set_display_lang(self, lang: str):
        self.retranslateUI()

    _IMG_EXTS = frozenset({".bmp", ".jpg", ".png", ".jpeg", ".webp", ".jxl"})

    def OpenProj(self, proj_path: str):
        if osp.isdir(proj_path):
            self.openDir(proj_path)
        elif osp.splitext(proj_path)[1].lower() in self._IMG_EXTS:
            self.openImages([proj_path])
        else:
            self.openJsonProj(proj_path)

        if pcfg.let_textstyle_indep_flag and not shared.HEADLESS:
            self.load_textstyle_from_proj_dir(from_proj=True)

    def load_textstyle_from_proj_dir(self, from_proj=False):
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

        # 与 refresh_font_list_exclusion 同源：走排除过滤，
        # 否则打开项目时会把用户隐藏的字体重新灌回下拉
        font_list = shared.get_filtered_font_list(pcfg.excluded_fonts)

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
            # 换项目 = 旧撤销历史全部失效（命令锚定旧项目的 blk），清栈
            # 经栈信号顺带刷新历史面板
            self.canvas.clear_undostack(update_saved_step=True)
            self.st_manager.clearSceneTextitems()
            self.titleBar.setTitleContent(osp.basename(directory))
            self.updatePageList()
            self.canvas._update_hint_visibility()
            self.opening_dir = False
            progress.close()
            self.glossary_workbench.refresh_project_state()
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

    def openImages(self, filepaths: List[str]):
        """Create a temp project from dragged/opened image files.

        If a project is already open, this is a no-op — users must close the
        current project first (or use File → Open Image before loading a folder).
        """
        if self.imgtrans_proj.img_valid:
            return

        # Determine parent directory for the temp project
        root = pcfg.temp_project_dir or shared.TEMP_PROJECTS_DIR
        os.makedirs(root, exist_ok=True)

        # Generate a unique project directory name
        base = osp.splitext(osp.basename(filepaths[0]))[0]
        uid = uuid4().hex[:8]
        work_dir = osp.join(root, f"{base}_{uid}")

        # Copy all images into the working directory
        os.makedirs(work_dir)
        for fp in filepaths:
            shutil.copy2(fp, osp.join(work_dir, osp.basename(fp)))

        # Track as temp project for optional auto-clean
        self._temp_project_dirs.add(work_dir)

        # Proceed with normal project loading
        self.openDir(work_dir)
        self.leftBar.updateRecentProjList(work_dir)

    def saveProjectAs(self):
        """Save the current project to a new location (Ctrl+Shift+S).

        Copies the entire project directory (images, JSON, mask/, inpainted/,
        result/, etc.) to a user-chosen destination and switches to it.
        If the source was a drag-imported temp project, it is cleaned up.
        """
        if self.imgtrans_proj.directory is None or not self.imgtrans_proj.img_valid:
            return

        # Save current state first
        self.manual_save()

        # Wait for any pending image saves to complete
        while self.imsave_thread.isRunning():
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            time.sleep(0.05)

        src_dir = self.imgtrans_proj.directory

        # Ask user for destination folder
        dialog = QFileDialog()
        dst_dir = dialog.getExistingDirectory(
            self,
            self.tr("Save project to..."),
            osp.dirname(src_dir),
        )
        if not dst_dir:
            return  # user cancelled

        try:
            # Copy entire project contents
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

            # Rename the project JSON to match the new directory name
            old_basename = osp.basename(src_dir)
            new_basename = osp.basename(dst_dir)
            old_json = osp.join(dst_dir, f"imgtrans_{old_basename}.json")
            new_json = osp.join(dst_dir, f"imgtrans_{new_basename}.json")
            if osp.exists(old_json) and old_json != new_json:
                os.replace(old_json, new_json)

            # Remove .backup file if present (from conditional_save during close)
            old_backup = osp.join(dst_dir, f"imgtrans_{old_basename}.json.backup")
            if osp.exists(old_backup):
                os.remove(old_backup)

            # Switch to new location
            self.openDir(dst_dir)
            self.leftBar.updateRecentProjList(dst_dir)

            # If the source was a temp project, clean it up
            if src_dir in self._temp_project_dirs:
                self._temp_project_dirs.discard(src_dir)
                shutil.rmtree(src_dir, ignore_errors=True)

        except Exception as e:
            create_error_dialog(
                e,
                self.tr("Failed to save project to") + f" {dst_dir}",
            )
        finally:
            # Restore hint visibility (openDir may have loaded a project)
            self.canvas._update_hint_visibility()

    def openJsonProj(self, json_path: str):
        try:
            self.opening_dir = True
            self.imgtrans_proj.load_from_json(json_path)
            # 换项目 = 旧撤销历史全部失效（命令锚定旧项目的 blk），清栈
            # 经栈信号顺带刷新历史面板（同 openDir）
            self.canvas.clear_undostack(update_saved_step=True)
            self.st_manager.clearSceneTextitems()
            self.leftBar.updateRecentProjList(self.imgtrans_proj.proj_path)
            self.updatePageList()
            self.titleBar.setTitleContent(osp.basename(self.imgtrans_proj.proj_path))
            self.opening_dir = False
            self.canvas._update_hint_visibility()
            self.glossary_workbench.refresh_project_state()
        except Exception as e:
            self.opening_dir = False
            create_error_dialog(e, self.tr("Failed to load project from") + json_path)

    def updatePageList(self):
        if self.pageList.count() != 0:
            self.pageList.clear()

        use_thumbnails = len(self.imgtrans_proj.pages) < shared.PAGELIST_THUMBNAIL_MAXNUM

        for imgname in self.imgtrans_proj.pages:
            has_notext = (
                pcfg.use_notext_images
                and self.imgtrans_proj.get_notext_path(imgname) is not None
            )
            is_dirty = self.imgtrans_proj.page_needs_rerender(imgname)

            if use_thumbnails:
                imgpath = osp.join(self.imgtrans_proj.directory, imgname)
                reader = QImageReader(imgpath)
                reader.setScaledSize(
                    QSize(shared.PAGELIST_THUMBNAIL_SIZE, shared.PAGELIST_THUMBNAIL_SIZE)
                )
                img = reader.read()
                if img.isNull():
                    lstitem = QListWidgetItem(QIcon(imgpath), imgname)
                else:
                    pixmap = QPixmap.fromImage(img)
                    if has_notext or is_dirty:
                        painter = QPainter(pixmap)
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        painter.setPen(Qt.PenStyle.NoPen)
                        ts = shared.PAGELIST_THUMBNAIL_SIZE
                        dot_size = 8
                        margin = 2
                        if has_notext:
                            # Green dot at top-right (same as _make_badged_icon)
                            painter.setBrush(QColor(255, 255, 255))
                            r = QRect(ts - dot_size - margin, margin, dot_size, dot_size)
                            painter.drawEllipse(r.adjusted(-1, -1, 1, 1))
                            painter.setBrush(QColor(39, 174, 96))
                            painter.drawEllipse(r)
                        if is_dirty:
                            # Orange dot at bottom-right
                            painter.setBrush(QColor(255, 255, 255))
                            r = QRect(ts - dot_size - margin, ts - dot_size - margin,
                                      dot_size, dot_size)
                            painter.drawEllipse(r.adjusted(-1, -1, 1, 1))
                            painter.setBrush(QColor(230, 126, 34))
                            painter.drawEllipse(r)
                        painter.end()
                    lstitem = QListWidgetItem(QIcon(pixmap), imgname)
            else:
                lstitem = QListWidgetItem(imgname)
                if has_notext:
                    lstitem.setIcon(self._get_notext_dot_icon())
                if is_dirty:
                    font = lstitem.font()
                    font.setItalic(True)
                    lstitem.setFont(font)

            if is_dirty:
                lstitem.setToolTip(
                    self.tr("This page has unrendered batch changes and will refresh automatically when opened")
                )

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
        # Pending numeric transform edits are not dirty until they commit;
        # resolve them before the close-time dirty check.
        self.st_manager.formatpanel.resolve_text_transform_edits_for_save()
        if not self.imgtrans_proj.is_empty:
            self.conditional_save(keep_exist_as_backup=True)
        while True:
            if not self.imsave_thread.isRunning():
                break
            time.sleep(0.1)
        if self.auto_tate_chu_yoko_thread.isRunning():
            self.auto_tate_chu_yoko_thread.request_stop()
            self.auto_tate_chu_yoko_thread.wait()
        redetect_tool = getattr(self, "region_redetect_tool", None)
        if redetect_tool is not None:
            redetect_tool.shutdown()
        self.st_manager.hovering_transwidget = None
        self.st_manager.blockSignals(True)
        self.canvas.prepareClose()
        self.save_config()

        # Auto-clean temp project dirs created by drag-import
        if pcfg.auto_clean_temp_projects:
            for d in list(self._temp_project_dirs):
                try:
                    if osp.isdir(d):
                        shutil.rmtree(d, ignore_errors=True)
                except OSError:
                    pass

        return super().closeEvent(event)

    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMaximized:
                self.titleBar.maxBtn.setChecked(True)
        elif event.type() == QEvent.Type.ActivationChange:
            self.canvas.on_activation_changed()
            self._pie_maybe_cancel_on_activation()

        super().changeEvent(event)

    def _pie_maybe_cancel_on_activation(self):
        """Close the quick menu when the main window loses activation.

        ActivationChange is delivered before ``isActiveWindow()`` settles, so
        the check is deferred by one event-loop turn.  The menu is a Tool
        window that never takes focus, so it can only outlive the main window
        when the user switched to another window/app — in which case it must
        not stay stranded on top.
        """
        if getattr(self, "pie_menu", None) is None:
            return
        QTimer.singleShot(0, self._pie_cancel_if_inactive)

    def _pie_cancel_if_inactive(self):
        if self.pie_menu.is_open() and not self.isActiveWindow():
            self.pie_menu.cancel()

    # ── Pie menu: Tab trigger + cancel routing (app event filter) ──

    def eventFilter(self, watched, event: QEvent):
        """App-level filter driving the pie menu.

        ``ShortcutOverride`` swallows any QShortcut bound to the trigger
        key (Qt checks it before QShortcut); ``KeyPress``/``KeyRelease``
        drive the spring-loaded state machine; Esc / click-outside cancel
        a pinned menu.
        """
        t = event.type()
        if t == QEvent.Type.ShortcutOverride:
            if self._pie_handle_shortcut_override(event):
                return True
        elif t == QEvent.Type.KeyPress:
            if self._pie_handle_keypress(event):
                return True
        elif t == QEvent.Type.KeyRelease:
            if self._pie_handle_keyrelease(event):
                return True
        elif t == QEvent.Type.MouseButtonPress:
            if self._pie_handle_click_outside(watched, event):
                return True
        elif t == QEvent.Type.ApplicationDeactivate:
            # Alt-Tab / switch app while the menu is open would strand it
            # (the release would land in another window) — cancel instead.
            if self.pie_menu.is_open():
                self.pie_menu.cancel()
        return super().eventFilter(watched, event)

    def _pie_trigger_ready(self) -> bool:
        """Mode conditions: textEditMode ∧ ¬creating ∧ ¬editing ∧ canvas mode.

        The *area* condition (cursor over the canvas) is checked separately
        in :meth:`_pie_handle_keypress` via :meth:`_pie_cursor_on_canvas`;
        pure text inputs swallow Tab on their own (see there).
        """
        canvas = self.canvas
        return (
            canvas.textEditMode()
            and not canvas.creating_textblock
            and canvas.editing_textblkitem is None
            and self._is_canvas_mode()
        )

    def _pie_cursor_on_canvas(self) -> bool:
        """True when the global cursor is over the canvas graphics view.

        Review 2026-08-11: the Tab trigger is scoped to the canvas — Tab
        elsewhere (right text panel, bars) keeps its default behavior, so
        the pie menu never hijacks the text panel's keyboard navigation.
        """
        gv = self.canvas.gv
        if gv is None or not gv.isVisible():
            return False
        return gv.rect().contains(gv.mapFromGlobal(QCursor.pos()))

    def _pie_menu_for_event(self, key, mods):
        """Menu config whose trigger key matches (key, mods), or None.

        Both sides are canonicalized through ``QKeySequence`` so the stored
        string ("Tab", "X", "Ctrl+X", ...) compares equal to the key event.
        """
        try:
            key_int = int(key)
            # KeyboardModifier is a Flag enum (no int() in PyQt6) — use .value
            mod_int = int(getattr(mods, "value", mods))
            seq = QKeySequence(key_int | mod_int).toString(
                QKeySequence.SequenceFormat.PortableText)
        except Exception:
            return None
        if not seq:
            return None
        for menu in (pcfg.pie_menus or []):
            trigger = menu.get("trigger")
            if trigger and QKeySequence(trigger).toString(
                    QKeySequence.SequenceFormat.PortableText) == seq:
                return menu
        return None

    def _pie_handle_shortcut_override(self, event) -> bool:
        if self._pie_menu_for_event(event.key(), event.modifiers()) is None:
            return False
        if self.pie_menu.is_open():
            event.accept()
            return True
        if not (self._pie_trigger_ready() and self._pie_cursor_on_canvas()):
            return False
        fw = self.app.focusWidget()
        in_main = fw is self or (fw is not None and self.isAncestorOf(fw))
        if (
            fw is not None and _is_text_input(fw) and in_main
            and event.key() != QKEY.Key_Tab
        ):
            # Letter / combo triggers must not hijack text-editing shortcuts
            # (Ctrl+X cut etc.) — only Tab keeps its inert-swallow behavior.
            return False
        event.accept()
        return True

    def _pie_handle_keypress(self, event) -> bool:
        key = event.key()
        if self.pie_menu.is_open():
            if key == QKEY.Key_Escape:
                self.pie_menu.cancel()
                return True
            if self._pie_menu_for_event(key, event.modifiers()) is not None:
                # Swallow every trigger key (incl. auto-repeat) while the
                # menu is up: the first press cancels a pinned menu; repeats
                # must never reach the focus widget (no focus cycling /
                # tab-char insertion / typing during a long hold).
                if not event.isAutoRepeat():
                    self.pie_menu.cancel()
                return True
            return False
        if event.isAutoRepeat():
            return False
        menu = self._pie_menu_for_event(key, event.modifiers())
        if menu is None:
            return False
        if not self._pie_trigger_ready():
            return False
        fw = self.app.focusWidget()
        in_main = fw is self or (fw is not None and self.isAncestorOf(fw))
        if fw is None and self.isActiveWindow():
            in_main = True
        if fw is not None and _is_text_input(fw) and in_main:
            # Text inputs: Tab stays inert (no \t / focus jump); letter and
            # combo triggers pass through so the user can type them.
            return key == QKEY.Key_Tab
        if not in_main:
            # Dialogs / other windows keep their own key behavior.
            return False
        if not self._pie_cursor_on_canvas():
            # Trigger key outside the canvas keeps its default behavior.
            return False
        # QKeyEvent has no cursor position in PyQt6 — use the global cursor.
        self.pie_menu.set_menu_config(menu)
        self.pie_menu.start_hold(QCursor.pos())
        return True

    def _pie_handle_keyrelease(self, event) -> bool:
        if event.isAutoRepeat():
            return False
        if not self.pie_menu.is_holding():
            return False
        if self._pie_menu_for_event(event.key(), event.modifiers()) is None:
            return False
        self.pie_menu.release_hold()
        return True

    def _pie_handle_click_outside(self, watched, event) -> bool:
        pm = self.pie_menu
        if not pm.is_open():
            return False
        # Any open state (PIN or holding): a press outside the menu rect
        # cancels it.  The holding state is the spring-loaded default, so a
        # long hold must not strand the menu above the canvas while the user
        # tries to do something else (2026-08-18).
        gpos = event.globalPosition().toPoint()
        if not pm.geometry().contains(gpos):
            pm.cancel()
        return False  # never swallow mouse presses

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
        # 撤销步数上限等「保存即生效」项同步到画布
        if getattr(self, "canvas", None) is not None:
            self.canvas.apply_undo_limit()

    def onHideCanvas(self):
        self.canvas.clearToolStates()

    def conditional_save(self, keep_exist_as_backup=False):
        # Typed transform edits belong to the current page and must commit
        # before its dirty check.
        self.st_manager.formatpanel.resolve_text_transform_edits_for_save()
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

    def on_undo_page_jump_requested(self, pagename: str):
        """跨页撤销/历史面板跳转的页切换（canvas.page_jump_requested）。

        经 pageList 设当前行走完整 pageListCurrentItemChanged 链路
        （会话落账、条件保存、场景重建、脏页惰性重渲）后返回。"""
        rows = self.pageList.findItems(pagename, Qt.MatchFlag.MatchExactly)
        if rows:
            self.pageList.setCurrentItem(rows[0])

    def pageListCurrentItemChanged(self):
        item = self.pageList.currentItem()
        self.page_changing = True
        # 框级动作处理中切页 → 直接取消并提示（提案未确认即无写入，§8.9）
        if hasattr(self, "blockActionCard"):
            self.cancel_block_action_on_page_switch()
        if item is not None:
            # Typed transform edits belong to the old page and must commit
            # before its dirty check. Live drags are previews and cancel.
            self.st_manager.formatpanel.resolve_text_transform_edits_for_page_change()
            # 旧页键入会话先落账（命令归属旧页，切页后仍可跨页撤销）
            self.canvas.commit_edit_sessions()
            if self.save_on_page_changed:
                self.conditional_save()
            new_pagename = item.text()
            # 页屏障（阶段4-3b）：图像侧脏且未被条件保存落盘时，离页重载
            # 会从磁盘换新图像缓冲——该页修复历史作废（僵尸化），避免陈旧
            # 端点写回。条件保存成功则计数器已对齐，不触发。
            old_pagename = self.imgtrans_proj.current_img
            if self.save_on_page_changed:
                self.conditional_save()
            if (
                old_pagename is not None
                and self.canvas.saved_imgstep != self.canvas.num_imgstep
            ):
                self.imgtrans_proj.bump_page_image_generation(old_pagename)
            self.imgtrans_proj.set_current_img(new_pagename)
            # 阶段 4 跨页历史：文本栈不清，仅清页级绘制栈
            self.canvas.prepare_page_switch()
            self.canvas._fit_to_window = self.opening_dir or pcfg.fit_window_on_page_switch
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self.titleBar.setTitleContent(page_name=self.imgtrans_proj.current_img)
            self.module_manager.handle_page_changed()
            self.drawingPanel.handle_page_changed()
            # If this page was modified by a batch op without re-rendering,
            # re-render its result image now that the scene is built.
            if self.imgtrans_proj.page_needs_rerender(new_pagename):
                self.imgtrans_proj.clear_page_needs_rerender(new_pagename)
                self._save_result_image_only()
                # Rebuilding the list synchronously re-fires currentItemChanged
                # (clear/addItem/setCurrentItem), which would re-enter this very
                # handler and repeat the full page switch.  Block signals only
                # for this one rebuild (it is synchronous, so no real user click
                # is lost), leaving the openDir first-render chain untouched.
                was_blocked = self.pageList.signalsBlocked()
                self.pageList.blockSignals(True)
                try:
                    self.updatePageList()
                finally:
                    self.pageList.blockSignals(was_blocked)

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
        self.titleBar.overflow_trigger.connect(self.on_overflow_triggered)
        self.titleBar.merge_tool_trigger.connect(self.on_open_merge_tool)
        self.titleBar.smart_reorder_trigger.connect(self.on_path_reorder)
        self.titleBar.stylemgr_trigger.connect(self.on_open_fontstyle_manager)

        self.titleBar.help_about_triggered.connect(self.show_about_dialog)
        self.titleBar.quick_symbol_trigger.connect(self.on_open_quick_symbol)
        # Tools 菜单软键盘项 = 窄栏开关的镜像：初始勾选态对齐，
        # 此后窄栏图标开合经 toggled 反向同步到菜单勾选
        _sym_launcher = self.textPanel.formatpanel.symbol_launcher
        if _sym_launcher is not None:
            self.titleBar.quickSymbolAction.setChecked(_sym_launcher.isChecked())
            _sym_launcher.toggled.connect(self.titleBar.quickSymbolAction.setChecked)
        self.titleBar.adv_align_trigger.connect(self.on_open_advanced_align)
        self.titleBar.normalize_breaks_triggered.connect(
            self.on_open_normalize_breaks_dialog
        )

        self._install_shortcuts()

    def _get_shortcut_keys(self, action_id):
        """Resolve effective shortcut keys: user config overrides defaults.

        Defaults come from the single source of truth ``default_keys_for``
        (which resolves Qt StandardKeys per-platform) rather than duplicating
        them at each call site.
        """
        from utils.config import pcfg

        if action_id in pcfg.shortcuts:
            keys = pcfg.shortcuts[action_id]
            if not isinstance(keys, list):
                keys = [keys] if keys else []
            return [k for k in keys if isinstance(k, str) and k]
        return default_keys_for(action_id)

    def _make_shortcuts(self, action_id, slot):
        """Create one QShortcut per effective key for ``action_id``.

        Each shortcut carries its action identity as a property so handlers
        can branch on semantics (e.g. page-turn suppression) instead of the
        literal bound key.
        """
        lst = []
        for k in self._get_shortcut_keys(action_id):
            sc = QShortcut(QKeySequence(k), self)
            sc.setProperty("action_id", action_id)
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

    def setupPieMenu(self):
        """Create the canvas pie menu and hook the Tab trigger via an app event filter.

        A single app-level filter also handles Esc / click-outside cancel
        while the menu is pinned, and swallows any QShortcut bound to Tab.
        """
        from .pie_menu import PieMenu
        from .context_menu_config import run_cmd

        self.pie_menu = PieMenu(self.canvas, mw=self, parent=self)
        self.pie_menu.command_triggered.connect(
            lambda cmd_id: run_cmd(self, cmd_id)
        )
        self.app.installEventFilter(self)
        # App-wide deactivation (Alt-Tab / switch app) must close the menu
        # too — ApplicationDeactivate in the event filter is not reliable on
        # Windows (2026-08-18).
        self.app.applicationStateChanged.connect(self._pie_on_app_state_changed)

    def _pie_on_app_state_changed(self, state):
        if (
            self.pie_menu.is_open()
            and state != Qt.ApplicationState.ApplicationActive
        ):
            self.pie_menu.cancel()

    def _install_shortcuts(self):
        """Create all QShortcut objects from current config (used at init + refresh)."""

        self.shortcut_registry["prev_page"] = self._make_shortcuts(
            "prev_page", self.shortcutBefore
        )
        self.shortcut_registry["prev_page_alt"] = self._make_shortcuts(
            "prev_page_alt", self.shortcutBefore
        )
        self.shortcut_registry["next_page"] = self._make_shortcuts(
            "next_page", self.shortcutNext
        )
        self.shortcut_registry["next_page_alt"] = self._make_shortcuts(
            "next_page_alt", self.shortcutNext
        )
        self.shortcut_registry["textblock_mode"] = self._make_shortcuts(
            "textblock_mode", self.shortcutTextblock
        )
        self.shortcut_registry["zoom_in"] = self._make_shortcuts(
            "zoom_in", self.canvas.gv.scale_up_signal
        )
        self.shortcut_registry["zoom_out"] = self._make_shortcuts(
            "zoom_out", self.canvas.gv.scale_down_signal
        )
        self.shortcut_registry["delete_blks_alt"] = self._make_shortcuts(
            "delete_blks_alt", self.shortcutCtrlD
        )
        self.shortcut_registry["space_inpaint"] = self._make_shortcuts(
            "space_inpaint", self.shortcutSpace
        )
        self.shortcut_registry["select_all"] = self._make_shortcuts(
            "select_all", self.shortcutSelectAll
        )
        self.shortcut_registry["escape"] = self._make_shortcuts(
            "escape", self.shortcutEscape
        )
        self.shortcut_registry["strike"] = self._make_shortcuts(
            "strike", self.shortcutStrikeout
        )
        self.shortcut_registry["italic"] = self._make_shortcuts(
            "italic", self.shortcutItalic
        )
        self.shortcut_registry["underline"] = self._make_shortcuts(
            "underline", self.shortcutUnderline
        )
        self.shortcut_registry["delete_blks"] = self._make_shortcuts(
            "delete_blks", self.shortcutDelete
        )

        # Wire up actions that were previously only available via hardcoded TitleBar QAction shortcuts
        self.shortcut_registry["textedit_mode"] = self._make_shortcuts(
            "textedit_mode", self.shortcutTextedit
        )
        self.shortcut_registry["drawboard_mode"] = self._make_shortcuts(
            "drawboard_mode", self.shortcutDrawboard
        )
        self.shortcut_registry["undo"] = self._make_shortcuts(
            "undo", self.on_undo
        )
        self.shortcut_registry["redo"] = self._make_shortcuts(
            "redo", self.on_redo
        )
        self.shortcut_registry["page_search"] = self._make_shortcuts(
            "page_search", self.on_page_search
        )
        self.shortcut_registry["global_search"] = self._make_shortcuts(
            "global_search", self.on_global_search
        )
        self.shortcut_registry["merge_tool"] = self._make_shortcuts(
            "merge_tool", self.on_open_merge_tool
        )
        self.shortcut_registry["quick_symbol"] = self._make_shortcuts(
            "quick_symbol", self.on_open_quick_symbol
        )
        self.shortcut_registry["advanced_align"] = self._make_shortcuts(
            "advanced_align", self.on_open_advanced_align
        )
        self.shortcut_registry["toggle_original_opacity"] = self._make_shortcuts(
            "toggle_original_opacity", self.shortcutToggleOriginalOpacity
        )
        self.shortcut_registry["path_reorder"] = self._make_shortcuts(
            "path_reorder", self.on_path_reorder
        )
        self.shortcut_registry["move_up"] = self._make_shortcuts(
            "move_up", self.shortcutMoveUp
        )
        self.shortcut_registry["move_down"] = self._make_shortcuts(
            "move_down", self.shortcutMoveDown
        )
        self.shortcut_registry["move_top"] = self._make_shortcuts(
            "move_top", self.shortcutMoveTop
        )
        self.shortcut_registry["move_bottom"] = self._make_shortcuts(
            "move_bottom", self.shortcutMoveBottom
        )
        self.shortcut_registry["merge_blks"] = self._make_shortcuts(
            "merge_blks", self.shortcutMergeBlks
        )

        # 块标签翻转键：选中态打标（多选翻转语义），画布编辑期间由
        # 编辑器 ShortcutOverride 自然屏蔽
        for tag_id in TAG_REGISTRY:
            self.shortcut_registry[f"tag_{tag_id}"] = self._make_shortcuts(
                f"tag_{tag_id}",
                partial(self.on_tag_shortcut, tag_id),
            )
        self.shortcut_registry["next_tagged_block"] = self._make_shortcuts(
            "next_tagged_block", lambda: self.jump_to_tagged_block(backward=False)
        )
        self.shortcut_registry["prev_tagged_block"] = self._make_shortcuts(
            "prev_tagged_block", lambda: self.jump_to_tagged_block(backward=True)
        )

        drawpanel_info = {
            "hand": "hand_tool",
            "rect": "rect_tool",
            "inpaint": "inpaint_tool",
            "ai": "ai_tool",
        }
        for tool_name, action_id in drawpanel_info.items():
            keys = self._get_shortcut_keys(action_id)
            lst = []
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.setProperty("action_id", action_id)
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
            # ``next_page`` is a letter key by default (D) that must not
            # turn the page while a text block is being edited; the alt
            # page-turn (PgDn) keeps navigating.  Keyed by action identity
            # so rebinding the key does not change this behavior.
            if sender.property("action_id") == "next_page":
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
            # Mirror of shortcutNext: ``prev_page`` (letter key, default A)
            # is suppressed while editing a text block; ``prev_page_alt``
            # keeps navigating.  Keyed by action identity, not the bound key.
            if sender.property("action_id") == "prev_page":
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

    def _reorder_move(self, mode: str):
        if not self._is_canvas_mode() or not self.canvas.textEditMode():
            return
        if not self.st_manager.textEditList.checked_list:
            return
        self.st_manager.textEditList.move_selected(mode)

    def shortcutMoveUp(self):
        self._reorder_move("up")

    def shortcutMoveDown(self):
        self._reorder_move("down")

    def shortcutMoveTop(self):
        self._reorder_move("top")

    def shortcutMoveBottom(self):
        self._reorder_move("bottom")

    def shortcutMergeBlks(self):
        if self._is_canvas_mode() and self.canvas.textEditMode():
            self.canvas.merge_textblks.emit()

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

    def shortcutStrikeout(self):
        if self.textPanel.formatpanel.isVisible():
            self.textPanel.formatpanel.formatBtnGroup.strikeBtn.click()

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
        # A live transform preview must not leak across the history boundary.
        self.st_manager.formatpanel.resolve_text_transform_edits_for_history_change()
        before = self._history_index("redo")
        self.canvas.redo()
        self._notify_history("redo", before)

    def on_undo(self):
        self.st_manager.formatpanel.resolve_text_transform_edits_for_history_change()
        before = self._history_index("undo")
        self.canvas.undo()
        self._notify_history("undo", before)

    def _active_history_stack(self, direction: str):
        """当前模式下撤销/重做实际消费的栈（与 canvas.undo/redo 同路由）：
        文本模式=全局栈；绘制模式涂鸦栈可动则涂鸦栈，否则回退全局栈。"""
        c = self.canvas
        if c.textEditMode():
            return c.text_undo_stack
        if c.drawMode():
            draw = c.draw_undo_stack
            can = draw.canUndo() if direction == "undo" else draw.canRedo()
            return draw if can else c.text_undo_stack
        return None

    def _history_index(self, direction: str):
        """当前模式撤销/重做实际消费栈的历史位置；不可动时返回 None。"""
        stack = self._active_history_stack(direction)
        return None if stack is None else stack.index()

    def _notify_history(self, action: str, before):
        """画布左下角撤销/重做 toast：历史位置未变（无可撤销内容）则不提示；
        同 key 让连续撤销刷新同一条通知而不是堆叠。纯附加提示——任何异常
        只记日志，绝不拖垮已完成的撤销/重做本身。"""
        if before is None:
            return
        try:
            stack = self._active_history_stack(action)
            if stack is None:
                return
            if stack.index() == before:
                if (
                    action == "undo"
                    and self.imgtrans_proj is not None
                    and self.global_search_widget.active_replace_version()
                    is not None
                ):
                    # 批量替换与逐块编辑分治：Ctrl+Z 撤不到批量操作，
                    # 见底时提示真正的回滚入口。判据取面板回滚条的可用性
                    # （而非"备份目录里有版本"）：若最新一版已是别的批量
                    # 操作写的，面板这条回滚条也撤不了，提示会误导。
                    notification.toast(
                        self.tr(
                            "Nothing left to undo. The last batch replace can be rolled back in the search panel."
                        ),
                        anchor="bottom-left",
                        key="history",
                    )
                return
            notification.toast(
                self.tr("Undone") if action == "undo" else self.tr("Redone"),
                anchor="bottom-left",
                key="history",
            )
        except Exception as e:
            LOGGER.error(f"history toast failed: {e}")

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
        """Toggle the Quick Symbol rail dock (text panel format rail)."""
        self.textPanel.formatpanel.toggle_symbol_dock()

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
                self.tr("整理换行"),
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
                        item_changes.append((blk, old_pos, new_pos))
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

    # ── Smart Reorder ─────────────────────────────────────

    def on_path_reorder(self):
        """Path Reorder: user draws a path across text blocks to set reading order."""
        if self.canvas._reorder_mode:
            # Already in path-reorder mode — ignore repeat trigger
            return

        if self.imgtrans_proj.is_empty:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please open a project first")
            )
            return

        current_img = self.imgtrans_proj.current_img
        if not current_img:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("No current file")
            )
            return

        blk_list = self.imgtrans_proj.current_block_list()
        if not blk_list:
            QMessageBox.warning(
                self, self.tr("Notice"), self.tr("No text blocks on current page")
            )
            return

        self.canvas.reorder_path_finished.connect(self._on_reorder_path_done)
        self.canvas.enterReorderMode()

    def _on_reorder_path_done(self, touched_ids):
        """Callback when a reorder path stroke is completed."""
        self.canvas.reorder_path_finished.disconnect(self._on_reorder_path_done)

        if len(touched_ids) < 2:
            mb = QMessageBox(self)
            mb.setWindowTitle(self.tr("Path Reorder"))
            mb.setText(
                self.tr("Not enough text blocks touched by the path (need at least 2).")
            )
            cont_btn = mb.addButton(
                self.tr("Continue Drawing"), QMessageBox.ButtonRole.ActionRole
            )
            mb.addButton(
                self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole
            )
            mb.setDefaultButton(cont_btn)
            mb.exec_()
            if mb.clickedButton() == cont_btn:
                self.canvas.reorder_path_finished.connect(self._on_reorder_path_done)
            else:
                self.canvas.exitReorderMode()
            return

        mb = QMessageBox(self)
        mb.setWindowTitle(self.tr("Path Reorder"))
        mb.setText(self.tr("Apply reorder?"))
        apply_btn = mb.addButton(
            self.tr("Apply"), QMessageBox.ButtonRole.AcceptRole
        )
        cont_btn = mb.addButton(
            self.tr("Continue Drawing"), QMessageBox.ButtonRole.ActionRole
        )
        mb.addButton(
            self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole
        )
        mb.setDefaultButton(apply_btn)
        mb.exec_()

        clicked = mb.clickedButton()
        if clicked == apply_btn:
            current_img = self.imgtrans_proj.current_img
            # Build block list from canvas items (includes unsaved new blocks)
            blk_list = [
                item.blk
                for item in self.canvas.textLayer.childItems()
                if isinstance(item, TextBlkItem)
            ]
            reordered = [blk_list[i] for i in touched_ids]
            untouched = [
                b for i, b in enumerate(blk_list) if i not in touched_ids
            ]
            reordered.extend(untouched)
            self.imgtrans_proj.pages[current_img] = reordered
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self.canvas.exitReorderMode()
        elif clicked == cont_btn:
            self.canvas.reorder_path_finished.connect(self._on_reorder_path_done)
        else:
            self.canvas.exitReorderMode()

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

    def on_search_result_item_clicked(
        self, pagename: str, blk_idx: int, is_src: bool, start: int, end: int
    ):
        idx = self.imgtrans_proj.pagename2idx(pagename)
        if idx < 0:
            return
        self.pageList.setCurrentRow(idx)
        # Page switch is synchronous; bail out if it somehow didn't land,
        # so we never index into another page's widget list.
        if self.imgtrans_proj.current_img != pagename:
            return
        pw_list = self.st_manager.pairwidget_list
        if not 0 <= blk_idx < len(pw_list):
            return
        pw = pw_list[blk_idx]
        edit = pw.e_source if is_src else pw.e_trans
        edit.setFocus()
        edit.ensure_scene_visible.emit()
        cursor = QTextCursor(edit.document())
        doc_len = edit.document().characterCount() - 1
        cursor.setPosition(min(start, doc_len))
        cursor.setPosition(min(end, doc_len), QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)

    def shortcutEscape(self):
        if self.canvas.search_widget.isVisible():
            self.canvas.search_widget.hide()
        elif (
            self.canvas.editing_textblkitem is not None
            and self.canvas.editing_textblkitem.isEditing()
        ):
            self.canvas.editing_textblkitem.endEdit()

    def on_redetect_mode_changed(self):
        """底部栏「区域再检测」开关（clicked）→ 同步画布模式。"""
        self.setRegionRedetectMode(self.bottomBar.redetectChecker.isChecked())

    def setRegionRedetectMode(self, enabled: bool):
        """开关「区域再检测」模式：同步底部栏开关与画布状态。

        该模式下画布左键拉框＝框选待重检测区域（不是橡皮筋多选）。只在文本框
        编辑页可见／可用——绘图模式下文本框层是隐藏的，检出的框看不见。
        """
        enabled = bool(enabled)
        if self.bottomBar.redetectChecker.isChecked() != enabled:
            # setChecked 不触发 clicked（只发 toggled/stateChanged），不会回环
            self.bottomBar.redetectChecker.setChecked(enabled)
        tool = getattr(self, "region_redetect_tool", None)
        if tool is not None:
            tool.set_mode(enabled)

    def setPaintMode(self):
        if self.bottomBar.paintChecker.isChecked():
            if self.rightComicTransStackPanel.isHidden():
                self.rightComicTransStackPanel.show()
            self.rightComicTransStackPanel.setCurrentIndex(0)
            self.canvas.setPaintMode(True)
            # 绘图模式下文本框层隐藏，区域再检测没有意义
            self.setRegionRedetectMode(False)
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
            self.bottomBar.redetectChecker.show()
            self.rightComicTransStackPanel.setCurrentIndex(1)
            self.st_manager.setTextEditMode(True)
            self.setTextBlockMode()
        else:
            self.bottomBar.textblockChecker.hide()
            self.bottomBar.redetectChecker.hide()
            # 模式不跨页保留：离开文本框编辑页即退出（免得下次进来一脸雾水）
            self.setRegionRedetectMode(False)
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
            self._is_canvas_mode()
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

        proj_save_succeeded = not save_proj
        if save_proj:
            try:
                self.imgtrans_proj.save(keep_exist_as_backup=keep_exist_as_backup)
                proj_save_succeeded = True
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
            if proj_save_succeeded:
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

    def _sync_and_commit_project(self, force_sync: bool = False):
        """Sync current page UI → model, immediately save JSON (no image rendering).

        After any batch operation that modifies TextBlock data across pages,
        call this to persist the data to disk immediately, preventing data
        loss on crash or unexpected exit.  Result images for non-current
        pages are left stale (re-rendered lazily on visit).

        *force_sync* — sync UI → model unconditionally. Required after
        batch writers that mutate the current page's widgets/items without
        pushing undo commands (e.g. GlobalReplaceApplier):
        ``text_change_unsaved()`` tracks the canvas undo-stack counter,
        which such writers never advance, so the unsaved-change probe
        stays False and the replaced text would never reach the model.
        """
        if not self.imgtrans_proj.img_valid:
            return
        if force_sync or self.canvas.text_change_unsaved():
            self.st_manager.updateTextBlkList()
        try:
            self.imgtrans_proj.save()
            self.canvas.setProjSaveState(False)
            self.canvas.update_saved_undostep()
        except Exception as e:
            LOGGER.error(f"Failed to commit project: {e}")

    def _save_result_image_only(self):
        """Re-render and save only the current page's result image.

        Assumes the scene is already built from current TextBlock data and
        the JSON has already been committed.  Does NOT sync UI→model or
        save the project JSON.
        """
        if not self.imgtrans_proj.img_valid:
            return
        try:
            img = self.canvas.render_result_img()
            pagename = self.imgtrans_proj.current_img
            imsave_path = self.imgtrans_proj.get_result_path(pagename)
            imsave_ext = self.imgtrans_proj.get_result_ext(pagename)
            self.imsave_thread.saveImg(
                imsave_path, img, pagename,
                save_params={"ext": imsave_ext, "quality": pcfg.imgsave_quality},
                keep_alpha=self.imgtrans_proj.current_has_alpha(),
            )
        except Exception as e:
            LOGGER.error(f"Failed to render result image: {e}")

    def _rerender_dirty_pages(self, clear_history=True):
        """Non-disruptively re-render result images for all pages needing it.

        Iterates over every page marked by ``page_needs_rerender()``,
        loads it, rebuilds the scene, renders and saves the result image,
        then restores the original page the user was viewing.

        ``clear_history=False``（组化命令撤销后的「同时重渲染」路径）：
        保留撤销历史——该路径数据未变仅重渲图像，文本命令 blk 锚点
        依然有效，清栈会让用户刚做的撤销丢掉 redo 能力。
        """
        dirty_pages = [
            p for p in self.imgtrans_proj.pages
            if self.imgtrans_proj.page_needs_rerender(p)
        ]
        if not dirty_pages:
            return

        orig_page = self.imgtrans_proj.current_img
        orig_save = self.save_on_page_changed
        self.save_on_page_changed = False

        progress = QProgressDialog(
            self.tr("Re-rendering result images..."), None,
            0, len(dirty_pages), self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setCancelButton(None)
        progress.show()

        for i, pname in enumerate(dirty_pages):
            progress.setValue(i)
            # 本循环直连 set_current_img 不走切页链路（无条件保存）：保留
            # 历史的组化重渲路径（clear_history=False）下，离页图像侧脏
            # 即重载换新缓冲，修复历史作废（僵尸化），防陈旧端点写回
            if not clear_history:
                _prev = self.imgtrans_proj.current_img
                if (
                    _prev is not None
                    and self.canvas.saved_imgstep != self.canvas.num_imgstep
                ):
                    self.imgtrans_proj.bump_page_image_generation(_prev)
            self.imgtrans_proj.set_current_img(pname)
            if clear_history:
                self.canvas.clear_undostack(update_saved_step=True)
            self.canvas._fit_to_window = False
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self._save_result_image_only()
            self.imgtrans_proj.clear_page_needs_rerender(pname)
            QApplication.processEvents()

        progress.setValue(len(dirty_pages))
        # Restore original page
        if orig_page and orig_page != self.imgtrans_proj.current_img:
            self.pageList.setCurrentRow(
                self.imgtrans_proj.pagename2idx(orig_page)
            )
        self.save_on_page_changed = orig_save
        self.updatePageList()

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
        # Source / target now come from the bottom bar submenu or from the run
        # dialog.  The dialog is modal, so the two can never be live at once:
        # the only mirror that matters is the bottom bar's own submenu.
        sender = self.sender()
        text = sender.currentText()
        translator = self.module_manager.translator
        if translator is not None:
            translator.set_source(text)
        pcfg.module.translate_source = text
        src_selector = self.bottomBar.trans_selector.src_selector
        if sender != src_selector:
            src_selector.blockSignals(True)
            src_selector.setCurrentText(text)
            src_selector.blockSignals(False)

    def on_trans_tgt_changed(self):
        sender = self.sender()
        text = sender.currentText()
        translator = self.module_manager.translator
        if translator is not None:
            translator.set_target(text)
        pcfg.module.translate_target = text
        tgt_selector = self.bottomBar.trans_selector.tgt_selector
        if sender != tgt_selector:
            tgt_selector.blockSignals(True)
            tgt_selector.setCurrentText(text)
            tgt_selector.blockSignals(False)

    @staticmethod
    def _translator_active_profile(translator) -> str:
        """安全读 active_profile：无参数表模块（TransNone 等）断言会炸。"""
        if translator is None or not getattr(translator, "params", None):
            return ""
        try:
            return translator.get_param_value("active_profile") or ""
        except Exception:
            return ""

    @staticmethod
    def _module_profile_name(module) -> str:
        """安全读模块的 ``profile`` 选择器值（无此参数的模块返回 ""）。"""
        if module is None or not getattr(module, "params", None):
            return ""
        try:
            return module.get_param_value("profile") or ""
        except Exception:
            return ""

    def _trans_model_menu_data(self):
        """Model submenu data: the translator's active profile model list.

        Queried on every menu open; returns None when the translator or its
        active profile has no model options to switch between.
        """
        translator = self.module_manager.translator
        if translator is None:
            return None
        profile_name = self._translator_active_profile(translator)
        if not profile_name:
            return None
        profile = find_profile(profile_name)
        if profile is None:
            return None
        options = [
            str(option)
            for option in (profile.get("model_options") or [])
            if str(option)
        ]
        if not options:
            return None
        return {"options": options, "current": str(profile.get("model") or "")}

    def on_trans_model_changed(self, model: str):
        """Write a model picked in the bottom-bar submenu back to the profile.

        The translator re-reads the profile per request, so the next
        translation picks up the new model without re-initialization.
        """
        translator = self.module_manager.translator
        if translator is None or not model:
            return
        profile_name = self._translator_active_profile(translator)
        profiles = load_profiles()
        target = next(
            (p for p in profiles if p.get("name") == profile_name), None
        )
        if target is None or str(target.get("model") or "") == model:
            return
        target["model"] = model
        remember_model_option(target, "model", model)
        save_all_profiles(profiles)
        LOGGER.info("Translator model set to {}".format(model))

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

        blk_list, blk_ids = [], []
        for blkitem in blkitem_list:
            blk: TextBlock = blkitem.blk
            blk.text = self.st_manager.pairwidget_list[
                blkitem.idx
            ].e_source.toPlainText()
            blk_ids.append(blkitem.idx)
            blk_list.append(blk)

        self.module_manager.runBlktransPipeline(
            blk_list,
            tgt_img,
            mode,
            blk_ids,
            tgt_mask=tgt_mask,
            page_key=self._blktrans_at_page,
        )
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
            self.on_export_txt("translation")
        if shared.args.export_source_txt:
            self.on_export_txt("source")
        if shared.HEADLESS:
            self.run_next_dir()

    def _cuda_work_in_progress(self) -> bool:
        """是否还有后台活在跑（手动释放内存前必须为空）。

        销毁 CUDA 上下文会让"正在跑"的对象拿到失效指针，所以宁可拒绝也不冒险：
        覆盖管线线程、四个"切换模块"线程、区域再检测的后台检测线程（它的检测器可
        配成 CUDA）。画布 AI 修图走管线线程，已被第一条覆盖。
        """
        manager = getattr(self, "module_manager", None)
        if manager is not None:
            for name in (
                "imgtrans_thread",
                "textdetect_thread",
                "ocr_thread",
                "translate_thread",
                "inpaint_thread",
            ):
                thread = getattr(manager, name, None)
                if thread is None:
                    continue
                try:
                    if thread.isRunning():
                        return True
                except RuntimeError:  # 线程对象已销毁
                    continue
        tool = getattr(self, "region_redetect_tool", None)
        if tool is not None:
            try:
                if tool.is_running():
                    return True
            except Exception as e:
                LOGGER.warning(f"region redetect busy check failed: {e}")
        return False

    def on_release_memory(self):
        """设置页「释放内存」：用户手动要回跑完管线后留在进程里的那部分内存。

        三步分工与实测数字见 `utils/memory_release.py`：卸载模型几乎不还内存
        （实测 1503MB 只掉 0~75MB），销毁 CUDA 上下文真还 ~200MB，交回工作集把剩下
        大部分退给系统——**那是"交回"不是 free**，页表映射还在、下次访问要 fault in。
        所以先把"接下来会怎样"写清、用户确认后才动手；有后台活在跑时直接拒绝。
        """
        if self._cuda_work_in_progress():
            QMessageBox.information(
                self,
                self.tr("Release memory"),
                self.tr("A pipeline or a background task is still running. Wait until it finishes, then release memory."),
            )
            return
        answer = QMessageBox.question(
            self,
            self.tr("Release memory"),
            self.tr(
                "Unload all models, destroy the CUDA context and hand the working set back to Windows?\n\nWhat to expect next:\n - the next pipeline run (or AI repair) reloads models and rebuilds the CUDA session, so the first run is a few seconds slower;\n - the first interactions may stutter briefly while Windows pages data back in;\n - keep the app idle while releasing; do not start a run at the same time."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        report = release_memory(self.module_manager.unload_all_models)
        LOGGER.info(
            f"release_memory: before={report.before_mb:.0f}MB "
            f"after_unload={report.after_unload_mb} after_reset={report.after_reset_mb} "
            f"after_trim={report.after_trim_mb} unloaded={report.unloaded} "
            f"reset={report.context_reset} trim={report.working_set_returned} "
            f"errors={report.errors}"
        )
        before = str(int(round(report.before_mb)))
        after = str(int(round(report.after_mb)))
        # 占位符用 `%1`/`%2` 而不是 `{}`：`scripts/i18n_common.py` 的提取器会**跳
        # 过含 `{` 的字符串**（当作 format string），那样写 ts 里不会有条目、运行时
        # 回退英文。字面量后面也**紧跟 `)`**，别挂 `.replace(...)`。
        if report.unloaded is False:
            kind = "warning"
            text = self.tr("Models could not be unloaded, so the CUDA context was left alone. Working set: %1 MB → %2 MB")
        elif not report.context_reset:
            kind = "warning"
            text = self.tr("Working set returned to the system (%1 MB → %2 MB), but the CUDA context could not be released.")
        else:
            kind = "info"
            text = self.tr("Memory released: working set %1 MB → %2 MB")
        try:
            notification.toast(
                text.replace("%1", before).replace("%2", after),
                kind=kind,
                anchor="bottom-left",
                duration=3500,
            )
        except Exception as e:
            LOGGER.error(f"release memory toast failed: {e}")

    def postprocess_translations(self, blk_list: List[TextBlock]) -> None:
        src_is_cjk = is_cjk(pcfg.module.translate_source)
        tgt_is_cjk = is_cjk(pcfg.module.translate_target)
        if tgt_is_cjk:
            if not src_is_cjk:
                for blk in blk_list:
                    blk.translation = re.sub(
                        r'([?.!"])\s+', r"\1", blk.translation
                    )  # remove spaces following punctuations
        else:
            for blk in blk_list:
                if blk.vertical:
                    blk.alignment = TextAlignment.Center
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
        override_alignment = pcfg.let_alignment_flag == 1
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
                    if override_alignment:
                        blk.alignment = gf.alignment
                    elif pcfg.module.enable_detect and not blk.src_is_vertical:
                        blk.recalulate_alignment()
                    if override_writing_mode:
                        blk.vertical = gf.vertical
                    # Always apply global font/background colors and effects
                    # (removed "decide by program" — it did nothing meaningful)
                    blk.set_font_colors(fg_colors=gf.frgb, bg_colors=gf.srgb)
                    blk.opacity = gf.opacity
                    blk.shadow_color = gf.shadow_color
                    blk.shadow_radius = gf.shadow_radius
                    blk.shadow_strength = gf.shadow_strength
                    blk.shadow_offset = gf.shadow_offset
                    if override_font_family or blk.font_family is None:
                        blk.font_family = gf.font_family
                        if blk.rich_text:
                            blk.rich_text = set_html_family(
                                blk.rich_text, gf.font_family
                            )

                    blk.line_spacing = gf.line_spacing
                    blk.letter_spacing = gf.letter_spacing
                    blk.italic = gf.italic
                    # bold 已随字重真值化退役（font_weight 单一真值），
                    # 管线建块不再跟随默认（保持未设 → 渲染 Normal）
                    blk.underline = gf.underline
                    blk.fontformat.standard_vertical_roman_alignment = (
                        gf.standard_vertical_roman_alignment
                    )
                    sw = blk.stroke_width
                    if (
                        sw > 0
                        and pcfg.module.enable_ocr
                        and pcfg.module.enable_detect
                        and not override_fnt_size
                    ):
                        blk.font_size = blk.font_size / (1 + sw)

            if pcfg.auto_tate_chu_yoko.enabled and pcfg.module.enable_translate:
                apply_auto_tate_chu_yoko(blk_list, pcfg.auto_tate_chu_yoko)

        # 页屏障（阶段 4）：整页管线写入（翻译/OCR/格式覆写）使该页既有
        # 撤销历史过期。若随即切页，pageListCurrentItemChanged 不再清文本
        # 栈，失效只能在这里做。
        self.canvas.invalidate_text_history_for_page(
            self.imgtrans_proj.idx2pagename(page_index)
        )

        if page_index != self.pageList.currentIndex().row():
            self.pageList.setCurrentRow(page_index)
        else:
            self.imgtrans_proj.set_current_img_byidx(page_index)
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()

        if (
            pcfg.auto_squeeze_after_run
            and not pcfg.module.enable_detect
            and pcfg.module.enable_translate
        ):
            for blkitem in self.st_manager.textblk_item_list:
                blkitem.squeezeBoundingRect()

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
        # Handles both the RUN progress dialog and the TCY apply progress box.
        msgbox = self.sender()
        if msgbox is None or not hasattr(msgbox, "size"):
            msgbox = self.module_manager.progress_msgbox
        if hasattr(msgbox, "fit_to_content"):
            msgbox.fit_to_content()
        msg_size = msgbox.size()
        size = self.size()
        p = self.mapToGlobal(
            QPoint(size.width() - msg_size.width(), size.height() - msg_size.height())
        )
        msgbox.move(p)

    def apply_auto_tate_chu_yoko_to_project(self) -> None:
        if (
            self.imgtrans_proj.is_empty
            or self.auto_tate_chu_yoko_thread.isRunning()
        ):
            return

        # Capture live edits before the worker mutates the project documents.
        self.st_manager.updateTextBlkList()
        self.auto_tate_chu_yoko_progress.zero_progress()
        if self.auto_tate_chu_yoko_thread.start_processing(
            self.imgtrans_proj.pages,
            pcfg.auto_tate_chu_yoko,
        ):
            self.auto_tate_chu_yoko_progress.show_fitted()

    def on_auto_tate_chu_yoko_processing_finished(
        self,
        changed_count: int,
        changed_blocks: Tuple[TextBlock, ...],
    ) -> None:
        self.auto_tate_chu_yoko_progress.hide()
        if not changed_count:
            return

        changed_ids = {id(block) for block in changed_blocks}
        for block_item in self.st_manager.textblk_item_list:
            if id(block_item.blk) in changed_ids:
                block_item.load_rich_text_html(block_item.blk.rich_text)
        self.canvas.setProjSaveState(True)

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

        from .run_pipeline_dialog import RunPipelineDialog

        # 重新标记底部栏下拉的缺模型状态（距启动时可能已下载/删除过权重）
        for module_type, selector in (
            ("textdetector", self.bottomBar.textdet_selector.selector),
            ("ocr", self.bottomBar.ocr_selector.selector),
            ("translator", self.bottomBar.trans_selector.selector),
            ("inpainter", self.bottomBar.inpaint_selector.selector),
        ):
            mark_module_selector_status(selector, module_type)

        page_names = list(self.imgtrans_proj.pages.keys())
        dialog = RunPipelineDialog(
            self,
            page_names=page_names,
            # 区间轨上的完成度：哪几页已经跑完（页级 finish_code 位与）
            finished_pages=[
                bool(self.imgtrans_proj.get_page_progress(name))
                for name in page_names
            ],
        )
        self._run_dialog = dialog
        dialog.stage_toggled.connect(self.on_enable_module)
        dialog.module_selected.connect(self.on_run_module_selected)
        dialog.translate_source_changed.connect(self.on_trans_src_changed)
        dialog.translate_target_changed.connect(self.on_trans_tgt_changed)
        self._sync_run_dialog_translator(dialog)

        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._run_dialog = None
        render_mode = dialog.is_render_only()
        page_filter = dialog.page_filter()
        wo_update = dialog.run_without_textstyle_update()
        dialog.deleteLater()

        # The glossary path was committed to pcfg while browsing, so it stays
        # even when the dialog is cancelled (the pre-translation state shows it).
        if not accepted:
            return

        if render_mode:
            self.run_imgtrans_render_only(page_filter)
            return

        if wo_update:
            self._run_imgtrans_wo_textstyle_update = True

        # 运行前静态检查启用阶段的模型文件：给可读提示而不是让管线在线程里报错
        enabled_stages = (
            ("textdetector", "textdetector", pcfg.module.enable_detect, self.tr("Text Detection")),
            ("ocr", "ocr", pcfg.module.enable_ocr, self.tr("OCR")),
            ("translator", "translator", pcfg.module.enable_translate, self.tr("Translation")),
            ("inpainter", "inpainter", pcfg.module.enable_inpaint, self.tr("Inpainting")),
        )
        missing_lines = []
        for module_type, cfg_key, enabled, stage_label in enabled_stages:
            if not enabled:
                continue
            missing = GET_MISSING_MODEL_FILES(module_type, getattr(pcfg.module, cfg_key))
            if missing:
                missing_lines.append(
                    "{} ({}): {}".format(
                        stage_label, getattr(pcfg.module, cfg_key), ", ".join(missing)
                    )
                )
        if missing_lines:
            msgBox = QMessageBox(self)
            msgBox.setIcon(QMessageBox.Warning)
            msgBox.setWindowTitle(self.tr("Missing Model Files"))
            msgBox.setText(
                self.tr(
                    "Model files were not found for the stages below. You can still run, but those stages may fail. Download prompts appear when selecting the module."
                )
                + "\n\n"
                + "\n".join(missing_lines)
            )
            run_btn = msgBox.addButton(self.tr("Run Anyway"), QMessageBox.YesRole)
            cancel_btn = msgBox.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
            msgBox.setDefaultButton(cancel_btn)
            msgBox.exec_()
            if msgBox.clickedButton() == cancel_btn:
                return

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

    def _sync_run_dialog_translator(self, dialog) -> None:
        """Seed the run dialog's language selectors from the loaded translator."""
        translator = self.module_manager.translator
        if translator is None:
            return
        dialog.set_translator_metadata(
            translator.lang_source,
            translator.lang_target,
            translator.supported_src_list,
            translator.supported_tgt_list,
        )

    def on_run_module_selected(self, module_type: str, module_name: str):
        """A run-dialog module pick drives the bottom bar, which is the single
        source of truth — its signal chain then loads the module."""
        selector = {
            "textdetector": self.bottomBar.textdet_selector,
            "ocr": self.bottomBar.ocr_selector,
            "inpainter": self.bottomBar.inpaint_selector,
            "translator": self.bottomBar.trans_selector,
        }.get(module_type)
        if selector is None:
            return
        selector.setSelectedValue(module_name, block_signals=False)

    def run_imgtrans_render_only(self, page_filter=None) -> None:
        """Iterate the selected pages, render and save each, then restore."""
        orig_page = self.imgtrans_proj.current_img
        orig_save = self.save_on_page_changed
        self.save_on_page_changed = False

        if page_filter is None:
            page_names = list(self.imgtrans_proj.pages.keys())
        else:
            page_names = list(page_filter)

        from qtpy.QtWidgets import QProgressDialog

        progress = QProgressDialog(
            self.tr("Rendering pages..."), self.tr("Cancel"),
            0, len(page_names), self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.show()

        for i, pname in enumerate(page_names):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(
                self.tr("Rendering %1 (%2/%3)")
                .replace("%1", pname)
                .replace("%2", str(i + 1))
                .replace("%3", str(len(page_names)))
            )
            self.imgtrans_proj.set_current_img(pname)
            self.canvas.clear_undostack(update_saved_step=True)
            self.canvas._fit_to_window = False
            self.canvas.updateCanvas()
            self.st_manager.updateSceneTextitems()
            self.saveCurrentPage()
            self.imgtrans_proj.clear_page_needs_rerender(pname)
            QApplication.processEvents()

        progress.setValue(len(page_names))
        # Restore the page that was open before rendering
        if orig_page and orig_page != self.imgtrans_proj.current_img:
            self.pageList.setCurrentRow(
                self.imgtrans_proj.pagename2idx(orig_page)
            )
        self.save_on_page_changed = orig_save
        self.updatePageList()

    def run_imgtrans_wo_textstyle_update(self):
        self._run_imgtrans_wo_textstyle_update = True
        self.run_imgtrans()

    def on_launch_notext_tool(self):
        tool_path = osp.join(shared.PROGRAM_PATH, "tools", "无字图配对工具.py")
        if not osp.exists(tool_path):
            return
        subprocess.Popen([sys.executable, tool_path])

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
            # 阶段 4 页屏障：txt 导入整页重写非当前页的译文数据，
            # 这些页的既有撤销历史一并失效
            for _pname in matched_pages:
                if _pname != self.imgtrans_proj.current_img:
                    self.canvas.invalidate_text_history_for_page(_pname)

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
                if pagename != self.imgtrans_proj.current_img:
                    self.imgtrans_proj.mark_page_needs_rerender(pagename)

            # Persist all imported translations to JSON immediately
            self._sync_and_commit_project()
            self.updatePageList()

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
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(current_img_path)])
        else:
            subprocess.run(["xdg-open", str(Path(current_img_path).parent)])

    def on_set_gsearch_widget(self):
        setup = self.leftBar.globalSearchChecker.isChecked()
        if setup:
            self._hidePageListOverlay()
            self.leftBar.showPageListLabel.setChecked(False)
            self._showSearchOverlay()
        else:
            self._hideSearchOverlay()

    def on_global_replace_preparing(self):
        """替换施加前的最后准备：先同步当前页 UI → 数据并落盘，再写一版备份。

        备份必须反映替换前的完整状态（含当前页未落盘的手动编辑），
        因此同步落盘先行；备份须在收集器原地改写非当前页数据之前写好。
        替换只改文本与样式、不碰像素，故不传 ``pixel_regions``（不产
        像素前图）。写成的版本号回填给面板：回滚条据此确认自己那一版
        还没被更晚的批量操作顶掉（``utils/batch_versions.py::BatchVersionStore``）。
        """
        self._sync_and_commit_project()
        dirty = [
            p for p in self.imgtrans_proj.pages
            if self.imgtrans_proj.page_needs_rerender(p)
        ]
        if self.imgtrans_proj.current_img:
            dirty.append(self.imgtrans_proj.current_img)
        meta = BatchVersionStore(self.imgtrans_proj).begin(
            self.tr("Global replace"), dirty_pages=dirty
        )
        self.global_search_widget.set_replace_version(
            meta.seq if meta is not None else None
        )

    def on_global_replace_finished(
        self,
        sceneitem_list: dict,
        background_list: dict,
        target_text: str,
        format_changes: list,
    ):
        # Runs synchronously right after GlobalSearchWidget collected the
        # live widget references, so they are guaranteed to exist here.
        # No undo stack involvement: the batch is rolled back via the
        # pre-replace batch version written in on_global_replace_preparing.
        GlobalReplaceApplier(
            sceneitem_list,
            target_text,
            self.imgtrans_proj,
            scene_manager=self.st_manager,
            format_changes=format_changes,
        )
        # Persist all modified TextBlock data to JSON immediately.
        # Non-current pages were modified directly in memory by the
        # replace collector; the current page was applied by the applier
        # above.  Result images for non-current pages will be re-rendered
        # lazily when the user visits them.
        # force_sync: the applier bypasses the undo stack, so the
        # unsaved-change probe cannot see the current-page edits.
        self._sync_and_commit_project(force_sync=True)
        # 当前页不标脏、不进重渲询问（mark_page_needs_rerender 的当前页
        # 排除语义）：画布文本已实时更新，结果图随保存/切页重渲。
        self._ask_rerender_dirty_pages()

    def on_batch_rollback(self, version_seq: int = None):
        """批量回滚：用最新一版备份整体换回项目数据并重建当前页场景。

        与逐块编辑撤销分治——本入口丢弃该批量操作之后的全部修改（含其
        后手动编辑），确认对话框在查找替换面板侧完成。``version_seq`` 是
        面板回滚条记下的版本号：只撤它那一版，若期间已有更晚的批量操作
        写了新版则拒绝执行（避免撤错对象）。
        """
        store = BatchVersionStore(self.imgtrans_proj)
        try:
            store.restore_latest(expect_seq=version_seq)
        except Exception as e:
            LOGGER.error(f"Batch rollback failed: {e}")
            self.global_search_widget.hide_rollback_strip()
            try:
                notification.toast(
                    self.tr(
                        "Rollback failed: the backup version is missing or was superseded by a newer batch operation."
                    ),
                    anchor="bottom-left",
                )
            except Exception as err:
                LOGGER.error(f"rollback toast failed: {err}")
            return
        # 版本恢复是数据层的整体换入，与页栈中的任何命令都不再相干
        self.canvas.clear_undostack(update_saved_step=True)
        self.canvas.updateCanvas()
        self.st_manager.updateSceneTextitems()
        self._sync_and_commit_project()
        self.updatePageList()
        self.global_search_widget.hide_rollback_strip()
        try:
            notification.toast(
                self.tr("Batch replace rolled back"), anchor="bottom-left"
            )
        except Exception as e:
            LOGGER.error(f"rollback toast failed: {e}")
        self._ask_rerender_dirty_pages()

    def on_workbench_jump(self, pagename: str, block_idx: int):
        """工作台队列 → 画布跳转（规划 D26）。

        走 ``pageList`` 的整条链路（``pageListCurrentItemChanged``）而不是
        ``ui/mainwindow.py::MainWindow`` 的 ``_on_stylemgr_navigate``——前者
        含脏页惰性重渲，跳过去看到的就是最终画面（见设计 §8 的跳转链路），
        后者只切页与选中块、脏页会显示成过期结果图。
        """
        proj = self.imgtrans_proj
        if pagename not in proj.pages:
            return
        try:
            row = proj.pagename2idx(pagename)
        except Exception as e:
            LOGGER.error(f"Workbench jump could not resolve the page: {e}")
            return
        if row is None or row < 0:
            return
        if proj.current_img != pagename:
            self.pageList.setCurrentRow(row)
        # 切页是同步的；没落到位就不再往别的页的控件列表里索引
        if proj.current_img != pagename:
            return
        items = self.st_manager.textblk_item_list
        if not 0 <= block_idx < len(items):
            return
        for item in self.canvas.selected_text_items():
            item.setSelected(False)
        target = items[block_idx]
        target.setSelected(True)
        self.canvas.gv.centerOn(target)

    def on_workbench_rollback(self, version_seq: int):
        """工作台「撤销上次批量」：整体换回批量操作前的一版（规划 D4／D35）。

        复用查找替换那条回滚链路（``ui/mainwindow.py::MainWindow`` 的
        ``on_batch_rollback``：版本覆盖 → 清撤销栈 → 重画布 → 重建场景 →
        落盘 → 刷页列表 → 问是否重渲脏页），随后让工作台作废版本号并重跑
        当前队列的列表——数据整体换过了，旧列表全部失效。
        """
        self.on_batch_rollback(version_seq)
        self.glossary_workbench.clear_batch_version()

    def _on_group_undo_rerender_requested(self):
        """组化命令撤销确认弹窗勾选「同时重渲染」：重渲脏页但保留撤销历史。"""
        self._rerender_dirty_pages(clear_history=False)

    def _ask_rerender_dirty_pages(self):
        """替换提交后固定询问是否立即重渲脏页（定稿策略：每次都问）。

        稍后处理时保留脏角标，用户可随时切页触发惰性重渲；
        未来后台慢速脏页处理机制接管后由其替换本询问。
        """
        dirty_pages = [
            p for p in self.imgtrans_proj.pages
            if self.imgtrans_proj.page_needs_rerender(p)
        ]
        if not dirty_pages:
            return
        msg_box = QMessageBox(self)
        msg_box.setText(self.tr("Replace finished. Re-render the modified pages now?"))
        msg_box.setInformativeText(
            self.tr("Pages needing re-render: ") + str(len(dirty_pages))
        )
        rerender_btn = msg_box.addButton(
            self.tr("Re-render now"), QMessageBox.ButtonRole.AcceptRole
        )
        msg_box.addButton(
            self.tr("Later"), QMessageBox.ButtonRole.RejectRole
        )
        msg_box.exec()
        if msg_box.clickedButton() is rerender_btn:
            self._rerender_dirty_pages()

    def on_darkmode_triggered(self):
        pcfg.darkmode = self.titleBar.darkModeAction.isChecked()
        self.resetStyleSheet(reverse_icon=True)
        self.save_config()

    def on_overflow_triggered(self, checked: bool):
        self.canvas.setOverflowMode(checked)

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

    def check_for_updates(self, manual: bool = True, show_release_info: bool = False):
        """Check GitHub for a newer release (silently on startup)."""
        if self.update_thread.isBusy():
            LOGGER.info(
                "Ignored update check request because an update check or update is already running."
            )
            return
        self._manual_update_check = manual
        self.configPanel.setUpdateChecking(True)
        self.configPanel.setLatestVersion(self.tr("Checking..."))
        self.update_thread.checkLatest(show_release_info=show_release_info)

    def apply_confirmed_update(self, release_info, current_version: str):
        if self.update_thread.isBusy():
            LOGGER.info(
                "Ignored update apply request because an update check or update is already running."
            )
            return
        self.configPanel.setUpdateChecking(True)
        self.update_progress_msgbox.zero_progress()
        self.update_progress_msgbox.setTaskName(self.tr("Downloading update: "))
        self.update_progress_msgbox.updateTaskProgress(0, release_info.version)
        self.update_progress_msgbox.show()
        self._update_progress_visible = True
        self.update_thread.applyUpdate(release_info, current_version)

    def on_update_progress_changed(self, payload: dict):
        if not self._update_progress_visible:
            return
        progress = payload.get("progress", 0)
        message = payload.get("message", "")
        event = payload.get("event", "")
        task_names = {
            "backup_source": self.tr("Backing up current version: "),
            "download_start": self.tr("Downloading update: "),
            "download_progress": self.tr("Downloading update: "),
            "download_done": self.tr("Downloading update: "),
            "git_safety": self.tr("Saving local changes: "),
            "extract_source": self.tr("Installing update: "),
            "replace_source": self.tr("Installing update: "),
            "done": self.tr("Installing update: "),
        }
        self.update_progress_msgbox.setTaskName(
            task_names.get(event, self.tr("Updating: "))
        )
        self.update_progress_msgbox.updateTaskProgress(progress, message)

    def on_update_finished(self, result):
        if self._update_progress_visible:
            self.update_progress_msgbox.done(0)
            self._update_progress_visible = False
        self.configPanel.setUpdateChecking(False)
        if result.latest_version:
            self.configPanel.setLatestVersion(result.latest_version)

        if result.status in {"available", "preview"}:
            allow_update = result.status == "available"
            if self.confirm_update_release(result, allow_update=allow_update) and allow_update:
                QTimer.singleShot(
                    0,
                    lambda info=result.release_info, current=result.current_version: self.apply_confirmed_update(
                        info, current
                    ),
                )
            return

        if result.status == "up_to_date":
            if self._manual_update_check:
                create_info_dialog(
                    self.tr("Already up-to-date.") + f"\n{result.current_version}"
                )
            else:
                LOGGER.info(
                    f"BallonsTranslator-lite is already up-to-date: {result.current_version}"
                )
            return

        if result.status == "updated":
            # launch.restart() closes this window synchronously.
            self.update_thread.wait()
            self.restart_signal.emit()
            return

        LOGGER.warning(f"Ignored unexpected updater result status: {result.status}")

    def on_update_failed(self, error_msg: str, detail_traceback: str):
        if self._update_progress_visible:
            self.update_progress_msgbox.done(0)
            self._update_progress_visible = False
        self.configPanel.setUpdateChecking(False)
        self.on_create_errdialog(
            error_msg + "\n" + self.tr("Failed to check for updates."),
            detail_traceback,
            "",
        )

    def confirm_update_release(self, result, allow_update: bool = True) -> bool:
        dialog = UpdateReleaseDialog(
            result,
            self,
            display_language=pcfg.display_lang,
            allow_update=allow_update,
        )
        return dialog.exec_() == QDialog.DialogCode.Accepted

    def show_about_dialog(self):
        """Show the About dialog with version info (update checks live in settings)."""
        import launch

        dlg = AboutDialog(
            self,
            version=launch.VERSION,
            commit=launch.commit_hash(),
            branch=launch.BRANCH,
        )
        dlg.exec_()

    def show_commit_update_dialog(self):
        """Show the commit-based update checker (developer channel)."""
        import launch

        dlg = CommitUpdateDialog(
            self,
            branch=launch.BRANCH,
            git_path=launch.git,
            repo_path=str(launch.PATH_ROOT),
        )
        dlg.restart_requested.connect(self.restart_signal.emit)
        dlg.exec_()


