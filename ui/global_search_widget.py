import re
from typing import List, Optional, Tuple

from qtpy.QtCore import QItemSelection, QRectF, QSize, Qt, Signal
from qtpy.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QPalette,
    QStandardItem,
    QStandardItemModel,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from utils import shared as C
from utils.base_styles import copy_value
from utils.config import pcfg
from utils.fontformat import FontFormat
from utils.proj_imgtrans import ProjImgTrans
from utils.style_query import FormatCondition, FormatPredicate

from .custom_widget import (
    ConfigComboBox,
    FloatDropPanel,
    NoBorderPushBtn,
    Widget,
)
from .misc import doc_replace, doc_replace_no_shift
from .page_search_widget import SearchEditor, _search_highlight_color
from .style_format_editor import FormatEditorPanel
from .textedit_area import SourceTextEdit, TransPairWidget, TransTextEdit
from .textitem import TextBlkItem, TextBlock

SEARCHRST_FONTSIZE = 10.3


class HTMLDelegate(QStyledItemDelegate):
    def __init__(self):
        super().__init__()
        self.doc = QTextDocument()
        self.doc.setUndoRedoEnabled(False)

    def paint(self, painter, option, index):

        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        painter.save()
        self.doc.setDefaultFont(options.font)
        self.doc.setHtml(options.text)

        options.text = ""

        painter.translate(options.rect.left(), options.rect.top())

        clip = QRectF(0, 0, options.rect.width(), options.rect.height())
        painter.setClipRect(clip)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.clip = clip
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(*C.FOREGROUND_FONTCOLOR))
        self.doc.documentLayout().draw(painter, ctx)
        painter.restore()
        style = (
            QApplication.style() if options.widget is None else options.widget.style()
        )
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)


def get_rstitem_renderhtml(text: str, span: Tuple[int, int], font: QFont = None) -> str:
    if text == "":
        return text
    doc = QTextDocument()
    if font is None:
        font = doc.defaultFont()
    font.setPointSizeF(SEARCHRST_FONTSIZE)
    doc.setDefaultFont(font)
    doc.setPlainText(text.replace("\n", " "))
    cursor = QTextCursor(doc)
    cursor.setPosition(span[0])
    cursor.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
    cfmt = QTextCharFormat()
    cfmt.setBackground(_search_highlight_color())
    cursor.setCharFormat(cfmt)
    html = doc.toHtml()
    cleaned_html = re.findall(r"<body(.*?)>(.*?)</body>", html, re.DOTALL)
    if len(cleaned_html) > 0:
        cleaned_html = cleaned_html[0]
        return f"<body{cleaned_html[0]}>{cleaned_html[1]}</body>"
    else:
        return ""


class SearchResultItem(QStandardItem):
    def __init__(
        self,
        text: str,
        span: Tuple[int, int],
        blk_idx: int,
        pagename: str,
        is_src: bool,
    ):
        super().__init__()
        self.text = text

        self.start = span[0]
        self.end = span[1]
        self.is_src = is_src
        self.blk_idx = blk_idx
        self.pagename = pagename
        self.setText(get_rstitem_renderhtml(text, span, font=self.font()))
        self.setEditable(False)


class PageSeachResultItem(QStandardItem):
    def __init__(self, pagename: str, result_counter: int, blkid2match: dict):
        super().__init__()
        self.setData(result_counter, Qt.ItemDataRole.UserRole)
        self.pagename = pagename
        self.setText(str(result_counter) + " - " + pagename)
        self.blkid2match = blkid2match
        font = self.font()
        font.setPointSizeF(SEARCHRST_FONTSIZE)
        self.setFont(font)
        self.setEditable(False)


def gen_searchitem_list(
    span_list: List[int], text: str, blk_idx: int, pagename: str, is_src: bool
) -> List[SearchResultItem]:
    sr_list = []
    for span in span_list:
        sr_list.append(SearchResultItem(text, span, blk_idx, pagename, is_src))
    return sr_list


def match_blk(
    pattern: re.Pattern, blk: TextBlock, match_src: bool
) -> Tuple[List[Tuple], int]:
    if match_src:
        rst_iter = pattern.finditer(blk.get_text())
    else:
        rst_iter = pattern.finditer(blk.translation)
    rst_span_list = []
    match_counter = 0
    for rst in rst_iter:
        rst_span_list.append(rst.span())
        match_counter += 1
    return rst_span_list, match_counter


class SearchResultModel(QStandardItemModel):
    # https://stackoverflow.com/questions/32229314/pyqt-how-can-i-set-row-heights-of-qtreeview
    def data(self, index, role):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.SizeHintRole:
            size = QSize()
            item = self.itemFromIndex(index)
            size.setHeight(item.font().pointSize() + 14)
            return size
        else:
            return super().data(index, role)


class SearchResultTree(QTreeView):
    result_item_clicked = Signal(str, int, bool, int, int)

    def __init__(self, parent: QWidget = None, *args, **kwargs) -> None:
        super().__init__(parent, *args, **kwargs)

        sm = SearchResultModel()
        self.sm = sm
        self.setItemDelegate(HTMLDelegate())
        self.root_item = sm.invisibleRootItem()
        self.setModel(sm)
        font = self.font()
        font.setPointSizeF(SEARCHRST_FONTSIZE)
        self.setFont(font)
        self.setUniformRowHeights(True)
        self.selected: SearchResultItem = None
        self.last_selected: SearchResultItem = None
        self.setHeaderHidden(True)
        self.expandAll()

    def selectionChanged(
        self, selected: QItemSelection, deselected: QItemSelection
    ) -> None:
        selected_indexes = selected.indexes()
        if len(selected_indexes) > 0:
            sel: SearchResultItem = self.sm.itemFromIndex(selected_indexes[0])
            if isinstance(sel, SearchResultItem):
                self.result_item_clicked.emit(
                    sel.pagename, sel.blk_idx, sel.is_src, sel.start, sel.end
                )
        super().selectionChanged(selected, deselected)

    def addPage(
        self, pagename: str, num_result: int, blkid2match: dict
    ) -> PageSeachResultItem:
        prst = PageSeachResultItem(pagename, num_result, blkid2match)
        self.root_item.appendRow(prst)
        return prst

    def clearPages(self):
        rc = self.root_item.rowCount()
        if rc > 0:
            self.root_item.removeRows(0, rc)

    def rowCount(self):
        return self.root_item.rowCount()


class GlobalSearchWidget(Widget):
    req_update_pagetext = Signal()
    pages_dirtied = Signal()
    # Emitted right after the user confirms a replace and before any change
    # is applied: MainWindow syncs the current page UI into the model, saves
    # the project and writes the single-slot batch snapshot (which must
    # reflect the pre-replace state).
    replace_preparing = Signal()
    # (sceneitem_list, background_list, target_text, format_changes) —
    # emitted after a synchronous Replace All collection; MainWindow applies
    # the current-page changes via GlobalReplaceApplier (no undo stack —
    # rollback goes through the batch snapshot), persists the project, then
    # asks about re-rendering the dirty pages. format_changes 契约 =
    # utils/style_query.build_query_changes（old/new_ffmt 均为深拷贝）。
    replace_finished = Signal(object, object, str, object)
    # Emitted when the user clicks the rollback strip button (already
    # confirmed in-panel); MainWindow restores the batch snapshot and
    # rebuilds the scene.
    batch_rollback_requested = Signal()

    def __init__(self, parent: QWidget = None, *args, **kwargs) -> None:
        super().__init__(parent, *args, **kwargs)
        self.imgtrans_proj: ProjImgTrans = None

        # Live per-page widget lists (the same list objects mutated in place by
        # SceneTextManager), used by Replace All for the current page.
        self.pairwidget_list: List[TransPairWidget] = []
        self.textblk_item_list: List[TextBlkItem] = []
        self.counter_sum = 0
        self.searched_pattern: re.Pattern = None

        self.search_editor = SearchEditor(self, commit_latency=500)
        self.search_editor.setPlaceholderText(self.tr("Find"))
        self.search_editor.enter_pressed.connect(self.commit_search)
        self.search_editor.commit.connect(self.commit_search)

        self.no_result_str = self.tr("No results found. ")
        self.doc_edited_str = self.tr("Document changed. Press Enter to re-search.")
        self.search_rst_str = self.tr("Found results: ")
        self.invalid_regex_str = self.tr("Invalid regular expression.")
        self.result_label = QLabel(self.no_result_str)
        self.result_label.setMaximumHeight(32)

        self.case_sensitive_toggle = QCheckBox(self)
        self.case_sensitive_toggle.setObjectName("CaseSensitiveToggle")
        self.case_sensitive_toggle.setToolTip(self.tr("Match Case"))
        self.case_sensitive_toggle.clicked.connect(self.on_case_clicked)

        self.whole_word_toggle = QCheckBox(self)
        self.whole_word_toggle.setObjectName("WholeWordToggle")
        self.whole_word_toggle.setToolTip(self.tr("Match Whole Word"))
        self.whole_word_toggle.clicked.connect(self.on_whole_word_clicked)

        self.regex_toggle = QCheckBox(self)
        self.regex_toggle.setObjectName("RegexToggle")
        self.regex_toggle.setToolTip(self.tr("Use Regular Expression"))
        self.regex_toggle.clicked.connect(self.on_regex_clicked)

        self.range_combobox = ConfigComboBox(fix_size=False, scrollWidget=self)
        self.range_combobox.addItems(
            [self.tr("Translation"), self.tr("Source"), self.tr("All")]
        )
        self.range_combobox.setMaximumWidth(120)
        self.range_combobox.currentIndexChanged.connect(self.on_range_changed)
        self.range_label = QLabel(self)
        self.range_label.setText(self.tr(" in"))

        self.replace_editor = SearchEditor(self)
        self.replace_editor.setPlaceholderText(self.tr("Replace"))

        self.search_tree = SearchResultTree(self)
        self.replace_btn = NoBorderPushBtn(self.tr("Replace All"))
        self.replace_btn.clicked.connect(self.on_replace)
        sp = self.replace_btn.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        self.replace_btn.setSizePolicy(sp)

        # 批量替换后的回滚条：批量操作的唯一撤销入口（快照回滚），
        # 下一次替换或执行回滚后消失，不常驻
        self.rollback_strip = QWidget(self)
        self.rollback_strip.setObjectName("RollbackStrip")
        rollback_layout = QHBoxLayout(self.rollback_strip)
        rollback_layout.setContentsMargins(0, 0, 0, 0)
        self.rollback_label = QLabel(self.rollback_strip)
        self.rollback_btn = NoBorderPushBtn(self.tr("Undo This Replace"), self.rollback_strip)
        self.rollback_btn.clicked.connect(self._on_rollback_clicked)
        rollback_layout.addWidget(self.rollback_label, 1)
        rollback_layout.addWidget(self.rollback_btn)
        self.rollback_strip.setVisible(False)

        # ── 格式条件 / 替换格式（阶段 4，复用 FormatEditorPanel）──────
        # 两个入口按钮互斥展开内嵌面板；动过的字段数显示在按钮文案里。
        self._find_format_panel = FormatEditorPanel(self)
        self._find_format_panel.set_format(FontFormat())
        self._find_format_panel.field_changed.connect(
            self._update_format_btn_texts
        )
        self._replace_format_panel = FormatEditorPanel(self)
        self._replace_format_panel.set_format(FontFormat())
        self._replace_format_panel.field_changed.connect(
            self._update_format_btn_texts
        )

        self.find_format_btn = QToolButton(self)
        self.find_format_btn.setObjectName("FormatToggleBtn")
        self.find_format_btn.setCheckable(True)
        self.find_format_btn.toggled.connect(self._on_find_format_toggled)

        self.replace_format_btn = QToolButton(self)
        self.replace_format_btn.setObjectName("FormatToggleBtn")
        self.replace_format_btn.setCheckable(True)
        self.replace_format_btn.toggled.connect(self._on_replace_format_toggled)
        self._update_format_btn_texts()

        self.replace_mode_combo = ConfigComboBox(fix_size=False, scrollWidget=self)
        self.replace_mode_combo.addItems(
            [self.tr("Patch Fields"), self.tr("Apply Base Style")]
        )
        self.replace_mode_combo.currentIndexChanged.connect(
            self._sync_replace_mode_vis
        )

        self.style_combo = ConfigComboBox(fix_size=False, scrollWidget=self)

        self.format_stack = QStackedWidget(self)
        self.format_stack.addWidget(self._wrap_format_panel(self._find_format_panel))
        self.format_stack.addWidget(self._wrap_replace_format_panel())
        # 浮层宿主是主窗口中央控件（画布层）：左缘钉在本栏右缘、向画布
        # 方向按内容给足宽度——内嵌会撑大本栏最小宽导致动画宽度下右侧
        # 被裁切；本栏保持完全可见可交互
        self.format_float = FloatDropPanel(
            self.find_format_btn.text(),
            self.format_stack,
            self.find_format_btn,
            edge=self,
        )
        self.format_float.closed.connect(self._on_format_float_closed)

        hlayout_bar3 = QHBoxLayout()
        hlayout_bar3.setSpacing(5)
        hlayout_bar3.addWidget(self.find_format_btn)
        hlayout_bar3.addWidget(self.replace_format_btn)
        hlayout_bar3.addStretch()
        self._sync_replace_mode_vis()

        hlayout_bar1_0 = QHBoxLayout()
        hlayout_bar1_0.addWidget(self.search_editor)
        hlayout_bar1_0.setAlignment(Qt.AlignmentFlag.AlignTop)
        hlayout_bar1_0.setSpacing(10)

        hlayout_bar1_1 = QHBoxLayout()
        hlayout_bar1_1.addWidget(self.case_sensitive_toggle)
        hlayout_bar1_1.addWidget(self.whole_word_toggle)
        hlayout_bar1_1.addWidget(self.regex_toggle)
        hlayout_bar1_1.setAlignment(
            hlayout_bar1_1.alignment() | Qt.AlignmentFlag.AlignTop
        )
        hlayout_bar1_1.setSpacing(5)

        hlayout_bar1 = QHBoxLayout()
        hlayout_bar1.addLayout(hlayout_bar1_0)
        hlayout_bar1.addLayout(hlayout_bar1_1)

        hlayout_bar2_0 = QHBoxLayout()
        hlayout_bar2_0.addWidget(self.replace_editor)
        hlayout_bar2_0.setAlignment(Qt.AlignmentFlag.AlignTop)
        hlayout_bar2_0.setSpacing(10)

        hlayout_bar2_1 = QHBoxLayout()
        hlayout_bar2_1.addWidget(self.range_label)
        hlayout_bar2_1.addWidget(self.range_combobox)
        hlayout_bar2_1.setAlignment(
            hlayout_bar2_1.alignment() | Qt.AlignmentFlag.AlignTop
        )
        hlayout_bar2_1.setSpacing(5)

        hlayout_bar2 = QHBoxLayout()
        hlayout_bar2.addLayout(hlayout_bar2_0)
        hlayout_bar2.addLayout(hlayout_bar2_1)

        vlayout = QVBoxLayout(self)
        vlayout.addLayout(hlayout_bar1)
        vlayout.addLayout(hlayout_bar2)
        vlayout.addLayout(hlayout_bar3)
        vlayout.addWidget(self.result_label)
        vlayout.addWidget(self.search_tree)
        vlayout.addWidget(self.replace_btn)
        vlayout.addWidget(self.rollback_strip)
        vlayout.setStretchFactor(self.search_tree, 10)
        vlayout.setSpacing(7)

    def hideEvent(self, event):
        # 搜索栏收起时浮层挂在中央堆叠区，不会随之隐藏，需显式收起
        self.format_float.close_panel()
        super().hideEvent(event)

    def set_page_widget_lists(
        self,
        pairwidget_list: List[TransPairWidget],
        textblk_item_list: List[TextBlkItem],
    ):
        """Store the per-page live widget lists (mutated in place on page switch)."""
        self.pairwidget_list = pairwidget_list
        self.textblk_item_list = textblk_item_list

    # ── 格式条件 / 替换格式（阶段 4）────────────────────────────────

    def _wrap_format_panel(self, panel: FormatEditorPanel) -> QWidget:
        host = QWidget(self)
        vlayout = QVBoxLayout(host)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(2)
        clear_btn = NoBorderPushBtn(self.tr("Clear"), host)
        clear_btn.clicked.connect(lambda: self._clear_format_panel(panel))
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addStretch(1)
        header.addWidget(clear_btn)
        vlayout.addLayout(header)
        vlayout.addWidget(panel, 1)
        return host

    def _wrap_replace_format_panel(self) -> QWidget:
        """替换格式面板：模式选择与清空并入面板头，样式下拉仅大样式模式显示。"""
        host = QWidget(self)
        vlayout = QVBoxLayout(host)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(2)
        clear_btn = NoBorderPushBtn(self.tr("Clear"), host)
        clear_btn.clicked.connect(
            lambda: self._clear_format_panel(self._replace_format_panel)
        )
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.replace_mode_combo)
        header.addStretch(1)
        header.addWidget(clear_btn)
        vlayout.addLayout(header)
        vlayout.addWidget(self.style_combo)
        vlayout.addWidget(self._replace_format_panel, 1)
        return host

    def _clear_format_panel(self, panel: FormatEditorPanel):
        panel.set_format(FontFormat())
        self._update_format_btn_texts()
        if panel is self._find_format_panel:
            self.commit_search()

    def _on_find_format_toggled(self, checked: bool):
        if checked:
            self.replace_format_btn.setChecked(False)
            self.format_stack.setCurrentIndex(0)
            self.format_float.set_title(self.find_format_btn.text())
            self.format_float.open_panel()
        elif not self.replace_format_btn.isChecked():
            self.format_float.close_panel()

    def _on_replace_format_toggled(self, checked: bool):
        if checked:
            self.find_format_btn.setChecked(False)
            self.format_stack.setCurrentIndex(1)
            self._refresh_style_combo()
            self.format_float.set_title(self.replace_format_btn.text())
            self.format_float.open_panel()
        elif not self.find_format_btn.isChecked():
            self.format_float.close_panel()
        self._sync_replace_mode_vis()

    def _on_format_float_closed(self):
        # × / Esc 关闭浮层：静默复位两个入口按钮（blockSignals 防止
        # toggled 处理器再次触发 close_panel 递归发 closed）
        for btn in (self.find_format_btn, self.replace_format_btn):
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._sync_replace_mode_vis()
        self.search_editor.setFocus()

    def _sync_replace_mode_vis(self, _idx: int = 0):
        on = self.replace_format_btn.isChecked()
        self.replace_mode_combo.setVisible(on)
        self.style_combo.setVisible(on and self.replace_mode_combo.currentIndex() == 1)
        if self.format_stack.isVisible():
            self.format_stack.setCurrentIndex(1)

    def _update_format_btn_texts(self):
        n = len(self._find_format_panel.changed_values())
        self.find_format_btn.setText(
            self.tr("Format Conditions") + (f" ({n})" if n else "")
        )
        n = len(self._replace_format_panel.changed_values())
        self.replace_format_btn.setText(
            self.tr("Replace Format") + (f" ({n})" if n else "")
        )
        # 浮层开着时标题跟随当前页按钮文案（含动过字段计数）；
        # init 期间 format_float 可能尚未创建，用 getattr 防护
        float_panel = getattr(self, "format_float", None)
        if float_panel is not None and float_panel.isVisible():
            src = (
                self.replace_format_btn
                if self.replace_format_btn.isChecked()
                else self.find_format_btn
            )
            float_panel.set_title(src.text())

    def _find_format_conditions(self) -> Optional[FormatPredicate]:
        """查找侧格式条件：入口未开或未动字段时返回 None（不参与过滤）。"""
        if not self.find_format_btn.isChecked():
            return None
        changed = self._find_format_panel.changed_values()
        if not changed:
            return None
        return FormatPredicate(
            [FormatCondition(fname, "eq", copy_value(v)) for fname, v in changed.items()]
        )

    def _refresh_style_combo(self):
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        if self.imgtrans_proj is not None:
            for bs in self.imgtrans_proj.base_styles:
                self.style_combo.addItem(bs.name, bs)
        self.style_combo.blockSignals(False)

    def _replace_format_payload(self) -> Tuple[Optional[dict], Optional[FontFormat]]:
        """替换侧格式载荷：(字段 patch, 大样式 ffmt)，互斥，未启用时 (None, None)。"""
        if not self.replace_format_btn.isChecked():
            return None, None
        if self.replace_mode_combo.currentIndex() == 1:
            bs = self.style_combo.currentData()
            return (None, bs.fontformat.deepcopy() if bs is not None else None)
        changed = self._replace_format_panel.changed_values()
        if not changed:
            return None, None
        return ({k: copy_value(v) for k, v in changed.items()}, None)


    def on_whole_word_clicked(self):
        pcfg.gsearch_whole_word = self.whole_word_toggle.isChecked()
        self.commit_search()

    def on_regex_clicked(self):
        pcfg.gsearch_regex = self.regex_toggle.isChecked()
        self.commit_search()

    def on_case_clicked(self):
        pcfg.gsearch_case = self.case_sensitive_toggle.isChecked()
        self.commit_search()

    def on_range_changed(self):
        pcfg.gsearch_range = self.range_combobox.currentIndex()
        self.commit_search()

    def get_regex_pattern(self) -> re.Pattern:
        target_text = self.search_editor.toPlainText()
        if target_text == "":
            return None

        body = target_text if self.regex_toggle.isChecked() else re.escape(target_text)
        if self.whole_word_toggle.isChecked():
            body = r"\b" + body + r"\b"

        flag = re.DOTALL
        if not self.case_sensitive_toggle.isChecked():
            flag |= re.IGNORECASE

        try:
            return re.compile(body, flag)
        except re.error:
            return None

    def commit_search(self):
        self.search_tree.clearPages()
        self.counter_sum = 0
        # 格式条件激活时文本维度（原文/译文）只作用于文本谓词：
        # 格式是块级属性，不分 src/trans。
        fmt_pred = self._find_format_conditions()
        text_str = self.search_editor.toPlainText()
        pattern = self.get_regex_pattern() if text_str else None
        if pattern is None and text_str:
            if fmt_pred is None:
                self.searched_pattern = None
                self.result_label.setText(self.invalid_regex_str)
                return
        elif pattern is None and fmt_pred is None:
            self.searched_pattern = None
            self.updateResultText()
            return
        self.searched_pattern = pattern
        self.req_update_pagetext.emit()

        match_src = self.range_combobox.currentIndex() != 0
        match_trans = self.range_combobox.currentIndex() != 1

        for pagename, page in self.imgtrans_proj.pages.items():
            page_match_counter = 0
            page_rstitem_list = []
            blkid2match = {"src": {}, "trans": {}}
            blk: TextBlock
            for ii, blk in enumerate(page):
                if fmt_pred is not None and not fmt_pred.matches(blk):
                    continue
                if pattern is not None:
                    if match_src:
                        rst_span_list, match_counter = match_blk(
                            pattern, blk, match_src=True
                        )
                        if match_counter > 0:
                            rstitem_list = gen_searchitem_list(
                                rst_span_list, blk.get_text(), ii, pagename, is_src=True
                            )
                            blkid2match["src"][ii] = rstitem_list
                            page_rstitem_list += rstitem_list
                            page_match_counter += match_counter
                    if match_trans:
                        rst_span_list, match_counter = match_blk(
                            pattern, blk, match_src=False
                        )
                        if match_counter > 0:
                            rstitem_list = gen_searchitem_list(
                                rst_span_list, blk.translation, ii, pagename, is_src=False
                            )
                            blkid2match["trans"][ii] = rstitem_list
                            page_rstitem_list += rstitem_list
                            page_match_counter += match_counter
                else:
                    # 格式-only 命中：块级条目，无高亮 span（点击即跳块）
                    disp_text = blk.get_text() or (blk.translation or "")
                    rstitem = SearchResultItem(disp_text, (0, 0), ii, pagename, is_src=True)
                    blkid2match["src"][ii] = [rstitem]
                    page_rstitem_list.append(rstitem)
                    page_match_counter += 1
            if page_match_counter > 0:
                self.counter_sum += page_match_counter
                pageitem = self.search_tree.addPage(
                    pagename, page_match_counter, blkid2match
                )
                pageitem.appendRows(page_rstitem_list)

        self.search_tree.expandAll()
        self.updateResultText()

    def updateResultText(self):
        if self.counter_sum > 0:
            self.result_label.setText(self.search_rst_str + str(self.counter_sum))
        else:
            self.result_label.setText(self.no_result_str)

    def _confirm_replace(self, msg: str) -> bool:
        msg_box = QMessageBox()
        msg_box.setText(msg)
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        return msg_box.exec_() == QMessageBox.StandardButton.Yes

    def _collect_replace_targets(self, target: str):
        """Re-match the live pattern × format conditions against every page
        and stage replacements.

        Runs synchronously on the GUI thread. Matches are recomputed from the
        current text/format of every block instead of trusting the spans
        captured at search time, so stale search results can never corrupt
        edited text. 格式条件激活时，命中块 = 文本谓词 AND 格式谓词。
        Current-page changes are staged as live widget references (applied
        afterwards by GlobalReplaceApplier); all other pages' text is
        modified in place in the project model. 格式 patch 不在收集期写
        ——统一收集为 format_changes（old/new_ffmt 深拷贝），由施加器的
        数据层落点一次性写入并标脏。

        Returns (sceneitem_list, background_list, format_changes).
        """
        pattern = self.searched_pattern
        doc = QTextDocument()
        doc.setUndoRedoEnabled(False)
        match_src = self.range_combobox.currentIndex() != 0
        match_trans = self.range_combobox.currentIndex() != 1
        current_img = self.imgtrans_proj.current_img
        fmt_pred = self._find_format_conditions()
        fmt_patch, style_ffmt = self._replace_format_payload()
        want_ffmt = fmt_patch is not None or style_ffmt is not None

        def _new_ffmt(blk):
            if style_ffmt is not None:
                return style_ffmt.deepcopy()
            new_ffmt = blk.fontformat.deepcopy()
            for k, v in fmt_patch.items():
                setattr(new_ffmt, k, copy_value(v))
            return new_ffmt

        sceneitem_list = {"src": [], "trans": []}
        background_list = {"src": [], "trans": []}
        format_changes = []

        for pagename, page in self.imgtrans_proj.pages.items():
            if pagename == current_img:
                for pw, item in zip(self.pairwidget_list, self.textblk_item_list):
                    if not 0 <= item.idx < len(page):
                        continue
                    blk = page[item.idx]
                    if fmt_pred is not None and not fmt_pred.matches(blk):
                        continue
                    if match_src and pattern is not None:
                        src = pw.e_source
                        text = src.toPlainText()
                        replace = pattern.sub(target, text)
                        if replace != text:
                            sceneitem_list["src"].append(
                                {"edit": src, "replace": replace, "idx": item.idx}
                            )
                    if match_trans and pattern is not None:
                        spans = [
                            list(m.span()) for m in pattern.finditer(pw.e_trans.toPlainText())
                        ]
                        if spans:
                            sceneitem_list["trans"].append(
                                {
                                    "edit": pw.e_trans,
                                    "item": item,
                                    "matched_map": spans,
                                }
                            )
                    if want_ffmt:
                        format_changes.append(
                            {
                                "pagename": pagename,
                                "block_idx": item.idx,
                                "old_ffmt": blk.fontformat.deepcopy(),
                                "new_ffmt": _new_ffmt(blk),
                            }
                        )
            else:
                page_dirty = False
                for idx, blk in enumerate(page):
                    if fmt_pred is not None and not fmt_pred.matches(blk):
                        continue
                    if match_src and pattern is not None:
                        text = blk.get_text()
                        replace = pattern.sub(target, text)
                        if replace != text:
                            blk.text = [replace]
                            background_list["src"].append(
                                {
                                    "ori": text,
                                    "replace": replace,
                                    "pagename": pagename,
                                    "idx": idx,
                                }
                            )
                            page_dirty = True
                    if match_trans and pattern is not None:
                        ori = blk.translation
                        ori_html = blk.rich_text
                        replace_html = ""
                        if blk.rich_text:
                            doc.setHtml(blk.rich_text)
                            spans = [
                                list(m.span()) for m in pattern.finditer(doc.toPlainText())
                            ]
                            if spans:
                                doc_replace(doc, spans, target)
                                replace_html = doc.toHtml()
                                replace = doc.toPlainText()
                            else:
                                replace = ori
                        else:
                            replace = pattern.sub(target, ori)
                        if replace != ori:
                            blk.translation = replace
                            blk.rich_text = replace_html
                            background_list["trans"].append(
                                {
                                    "ori": ori,
                                    "replace": replace,
                                    "ori_html": ori_html,
                                    "replace_html": replace_html,
                                    "pagename": pagename,
                                    "idx": idx,
                                }
                            )
                            page_dirty = True
                    if want_ffmt:
                        format_changes.append(
                            {
                                "pagename": pagename,
                                "block_idx": idx,
                                "old_ffmt": blk.fontformat.deepcopy(),
                                "new_ffmt": _new_ffmt(blk),
                            }
                        )
                if page_dirty:
                    self.imgtrans_proj.mark_page_needs_rerender(pagename)

        return sceneitem_list, background_list, format_changes

    def _has_staged_changes(
        self, sceneitem_list: dict, background_list: dict, format_changes: list
    ) -> bool:
        return bool(format_changes) or any(
            sceneitem_list[key] or background_list[key] for key in ("src", "trans")
        )

    def on_replace(self):
        """Replace all matches synchronously; rollback goes through the
        pre-replace batch snapshot, never through the undo stack."""
        if self.counter_sum < 1:
            return
        if not self._confirm_replace(self.tr("Replace all occurrences?")):
            return
        # Snapshot + UI sync must happen before collection, which mutates
        # non-current pages in place.
        self.replace_preparing.emit()
        self._refresh_style_combo()
        target = self.replace_editor.toPlainText()
        sceneitem_list, background_list, format_changes = (
            self._collect_replace_targets(target)
        )
        if not self._has_staged_changes(sceneitem_list, background_list, format_changes):
            # No-op replace: the just-written snapshot only mirrors the
            # current state, so it is worthless — drop it together with any
            # rollback strip still showing a (now unrecoverable) batch.
            self.imgtrans_proj.clear_batch_backup()
            self.hide_rollback_strip()
            self.set_document_edited()
            return
        self.replace_finished.emit(
            sceneitem_list, background_list, target, format_changes
        )
        self.pages_dirtied.emit()
        self._show_rollback_strip(sceneitem_list, background_list, format_changes)

    # ── 批量回滚条 ─────────────────────────────────────────────────

    def _show_rollback_strip(
        self, sceneitem_list: dict, background_list: dict, format_changes: list = ()
    ):
        current_img = self.imgtrans_proj.current_img
        block_keys = set()
        for rec in sceneitem_list["src"]:
            block_keys.add((current_img, rec.get("idx")))
        for rec in sceneitem_list["trans"]:
            block_keys.add((current_img, rec["item"].idx))
        for rec in background_list["src"] + background_list["trans"]:
            block_keys.add((rec["pagename"], rec["idx"]))
        for ch in format_changes:
            block_keys.add((ch["pagename"], ch["block_idx"]))
        block_keys.discard((current_img, None))
        pages = {p for p, _ in block_keys}
        self.rollback_label.setText(
            self.tr("Replaced %d block(s) across %d page(s)")
            % (len(block_keys), len(pages))
        )
        self.rollback_strip.setVisible(True)

    def hide_rollback_strip(self):
        self.rollback_strip.setVisible(False)

    def _on_rollback_clicked(self):
        if not self.imgtrans_proj.has_batch_backup():
            self.hide_rollback_strip()
            return
        confirmed = self._confirm_replace(
            self.tr(
                "Roll back the last batch replace? All edits made after it will be discarded."
            )
        )
        if confirmed:
            self.batch_rollback_requested.emit()

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        size.setWidth(300)
        return size

    def set_document_edited(self):
        if self.counter_sum > 0:
            self.search_tree.clearPages()
            self.result_label.setText(self.doc_edited_str)
            self.counter_sum = 0
        self.searched_pattern = None
