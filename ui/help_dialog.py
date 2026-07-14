"""
Help documentation viewer with heading navigation and cross-doc search.

Renders markdown files from docs/help/ in a non-modal QDialog.
Top tab bar for document switching (pill-style tabs).
Left sidebar: collapsible heading outline (QTreeWidget) + search bar.
Content area: QTextBrowser with scroll-spy (outline tracks viewport).
Search renders results as clickable HTML with theme-aware styling.
"""

import re
from pathlib import Path

from qtpy.QtCore import Qt, QTimer, QUrl, QPoint
from qtpy.QtGui import QColor, QDesktopServices, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils.shared import PROGRAM_PATH
from ui.theme_helpers import is_dark_theme

HELP_DIR = Path(PROGRAM_PATH) / "docs" / "help"


class HelpDialog(QDialog):
    """Help documentation viewer with heading navigation and cross-doc search."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("帮助 · 使用手册"))
        self.setMinimumSize(800, 550)
        self.resize(960, 640)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        # data
        self._docs = []             # [(title, Path), ...]
        self._headings = []         # [(level, text), ...] for current doc
        self._search_results = []   # [(doc_idx, heading, snippet, matched), ...]
        self._heading_blocks = []   # [(block_num, heading_text, tree_item), ...]
        self._navigating = False    # suppress scroll-spy during programmatic nav
        self._current_doc_idx = -1
        self._current_doc_path = None

        # scroll-spy debounce
        self._spy_timer = QTimer(self)
        self._spy_timer.setSingleShot(True)
        self._spy_timer.setInterval(150)
        self._spy_timer.timeout.connect(self._on_scroll_spy)

        self._build_ui()
        self._connect_signals()
        self._load_docs()

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self._build_header(layout)
        self._build_tab_bar(layout)
        self._build_content(layout)
        self._build_statusbar(layout)

    def _build_header(self, layout):
        header = QWidget()
        header.setObjectName("HelpHeader")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 10, 16, 6)

        title = QLabel(self.tr("使用手册"))
        title.setObjectName("HelpTitle")
        hlay.addWidget(title)
        hlay.addStretch()

        layout.addWidget(header)

    def _build_tab_bar(self, layout):
        self._tab_bar = QWidget()
        self._tab_bar.setObjectName("HelpDocTabBar")
        self._tab_layout = QHBoxLayout(self._tab_bar)
        self._tab_layout.setContentsMargins(16, 0, 16, 0)
        self._tab_layout.setSpacing(6)
        # stretch at end pushes tabs left
        self._tab_layout.addStretch()
        layout.addWidget(self._tab_bar)

    def _build_content(self, layout):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── left sidebar ──────────────────────────────────
        left = QWidget()
        left.setObjectName("HelpSidebar")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 9, 8, 9)
        left_lay.setSpacing(6)

        self._outline_tree = QTreeWidget()
        self._outline_tree.setObjectName("HelpOutlineTree")
        self._outline_tree.setHeaderHidden(True)
        self._outline_tree.setRootIsDecorated(False)
        self._outline_tree.setAnimated(True)
        self._outline_tree.setIndentation(12)
        left_lay.addWidget(self._outline_tree, 1)

        # search bar at sidebar bottom
        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("HelpSearchInput")
        self._search_edit.setPlaceholderText(self.tr("搜索文档..."))
        self._search_edit.setClearButtonEnabled(True)
        left_lay.addWidget(self._search_edit)

        left.setMinimumWidth(190)
        left.setMaximumWidth(280)
        splitter.addWidget(left)

        # ── content area ──────────────────────────────────
        self._browser = QTextBrowser()
        self._browser.setObjectName("HelpContent")
        self._browser.setOpenExternalLinks(False)
        self._browser.document().setDefaultFont(
            QFont("Microsoft YaHei", 10)
        )
        self._browser.document().setDocumentMargin(8)  # match sidebar's internal padding
        splitter.addWidget(self._browser)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([210, 750])
        # uniform 8px padding: left/right matches header's 16px + sidebar 8px inset;
        # top/bottom gives breathing room between tab bar / status bar and content
        splitter.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(splitter, 1)

    def _build_statusbar(self, layout):
        bar = QWidget()
        bar.setObjectName("HelpStatusBar")
        hlay = QHBoxLayout(bar)
        hlay.setContentsMargins(16, 4, 16, 4)

        self._status_label = QLabel()
        self._status_label.setObjectName("HelpStatusLabel")
        hlay.addWidget(self._status_label)

        hlay.addStretch()

        self._search_status = QLabel()
        self._search_status.setObjectName("HelpSearchStatus")
        hlay.addWidget(self._search_status)

        layout.addWidget(bar)

    # ── signals ──────────────────────────────────────────────

    def _connect_signals(self):
        self._outline_tree.currentItemChanged.connect(self._on_outline_clicked)
        self._search_edit.returnPressed.connect(self._on_search)
        self._search_edit.textChanged.connect(self._on_search_edited)
        self._browser.anchorClicked.connect(self._on_search_anchor_clicked)
        self._browser.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed
        )

    # ── keyboard ─────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._search_edit.text():
                self._clear_search()
            else:
                self.close()
        elif (
            event.modifiers() == Qt.KeyboardModifier.ControlModifier
            and event.key() == Qt.Key.Key_F
        ):
            self._search_edit.setFocus()
            self._search_edit.selectAll()
        else:
            super().keyPressEvent(event)

    # ── doc loading ──────────────────────────────────────────

    def _load_docs(self):
        """Scan docs/help/ for .md files and populate the tab bar."""
        self._docs.clear()
        self._clear_tabs()

        if not HELP_DIR.exists():
            self._show_placeholder(self.tr("暂无帮助文档"))
            return

        md_files = sorted(f for f in HELP_DIR.iterdir()
                          if f.suffix.lower() == ".md")
        if not md_files:
            self._show_placeholder(self.tr("暂无帮助文档"))
            return

        for f in md_files:
            title = self._extract_title(f)
            self._docs.append((title, f))

        for i, (title, _) in enumerate(self._docs):
            btn = self._make_tab_button(title, i)
            # insert before the trailing stretch
            self._tab_layout.insertWidget(
                self._tab_layout.count() - 1, btn
            )

        if self._docs:
            self._activate_tab(0)

    def _clear_tabs(self):
        """Remove all tab buttons (leaving the trailing stretch)."""
        while self._tab_layout.count() > 1:
            item = self._tab_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_tab_button(self, title: str, idx: int) -> QPushButton:
        btn = QPushButton(title)
        btn.setObjectName("HelpDocTab")
        btn.setProperty("current", False)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self._activate_tab(idx))
        return btn

    def _activate_tab(self, idx: int):
        """Switch to document at index idx."""
        if idx < 0 or idx >= len(self._docs):
            return

        self._current_doc_idx = idx

        # update tab "current" property
        for i in range(self._tab_layout.count()):
            w = self._tab_layout.itemAt(i).widget()
            if w and isinstance(w, QPushButton) and w.objectName() == "HelpDocTab":
                is_current = (i == idx)  # idx matches insertion order
                w.setProperty("current", is_current)
                w.style().unpolish(w)
                w.style().polish(w)

        _, filepath = self._docs[idx]
        self._load_doc(filepath)

    # ── document rendering ───────────────────────────────────

    def _load_doc(self, filepath: Path):
        """Read a markdown file and render it in the content view."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception as e:
            self._browser.setPlainText(
                self.tr("读取文档失败: {0}").format(str(e))
            )
            return

        # apply theme-aware typography before rendering markdown
        self._browser.document().setDefaultStyleSheet(
            self._document_stylesheet()
        )
        self._browser.setMarkdown(content)
        # programmatic code-block styling (CSS alone can't override
        # setMarkdown()'s inline character formats for <pre>)
        self._style_code_blocks()

        # parse headings
        self._headings = self._parse_headings(content)
        self._populate_outline()

        # build scroll-spy mapping
        self._build_heading_block_map()

        title = self._extract_title(filepath)
        self._current_doc_path = filepath
        n_total = len(self._docs)
        self._status_label.setText(
            self.tr("共 {0} 篇  |  当前: {1}").format(n_total, title)
        )

    # ── document typography ─────────────────────────────────

    def _document_stylesheet(self) -> str:
        """Return a theme-aware CSS string for the QTextDocument.

        Styles headings, blockquotes, tables, lists, links, etc.
        Code blocks are styled programmatically (_style_code_blocks)
        because setMarkdown() sets inline character formats that
        override document-level CSS for <pre> blocks.
        """
        dark = is_dark_theme()
        if dark:
            text = "#abb2bf"
            text_secondary = "#7f848e"
            heading = "#e5e5e5"
            inline_code_bg = "#2c313a"
            bq_border = "#5dade2"
            bq_bg = "#21252b"
            table_border = "#3e4452"
            table_head_bg = "#282c34"
            hr_color = "#3e4452"
            link_color = "#5dade2"
        else:
            text = "#383a42"
            text_secondary = "#8b8e96"
            heading = "#212121"
            inline_code_bg = "#dfe2e7"
            bq_border = "#1e93e5"
            bq_bg = "#f4f5f7"
            table_border = "#cdd0d8"
            table_head_bg = "#eaecf0"
            hr_color = "#cdd0d8"
            link_color = "#1e93e5"

        return (
            # language=CSS
            f"""
            body {{
                color: {text};
                line-height: 1.75;
            }}
            h1 {{
                font-size: 22px;
                font-weight: bold;
                color: {heading};
                margin: 28px 0 14px 0;
                padding-bottom: 6px;
                border-bottom: 1px solid {hr_color};
            }}
            h2 {{
                font-size: 18px;
                font-weight: bold;
                color: {heading};
                margin: 22px 0 10px 0;
                padding-bottom: 4px;
                border-bottom: 1px solid {hr_color};
            }}
            h3 {{
                font-size: 16px;
                font-weight: bold;
                color: {heading};
                margin: 18px 0 8px 0;
            }}
            h4 {{
                font-size: 15px;
                font-weight: bold;
                color: {heading};
                margin: 14px 0 6px 0;
            }}
            h5, h6 {{
                font-size: 14px;
                font-weight: bold;
                color: {text_secondary};
                margin: 12px 0 4px 0;
            }}
            p {{
                margin: 6px 0 10px 0;
                line-height: 1.75;
            }}
            code {{
                background-color: {inline_code_bg};
                padding: 2px 6px;
                border-radius: 4px;
                font-size: 13px;
            }}
            blockquote {{
                border-left: 3px solid {bq_border};
                background-color: {bq_bg};
                margin: 12px 0;
                padding: 10px 16px;
                color: {text_secondary};
            }}
            blockquote p {{
                margin: 4px 0;
            }}
            th {{
                background-color: {table_head_bg};
                border: 1px solid {table_border};
                padding: 7px 14px;
                font-weight: bold;
                text-align: left;
            }}
            td {{
                border: 1px solid {table_border};
                padding: 6px 14px;
            }}
            ul, ol {{
                margin: 8px 0;
                padding-left: 26px;
            }}
            li {{
                margin: 3px 0;
                line-height: 1.65;
            }}
            a {{
                color: {link_color};
                text-decoration: none;
            }}
            hr {{
                border: none;
                border-top: 1px solid {hr_color};
                margin: 20px 0;
            }}
            img {{
                max-width: 100%;
                border-radius: 4px;
            }}
            strong {{
                color: {heading};
            }}
            """
        )

    @staticmethod
    def _block_is_code_block(block) -> bool:
        """Detect whether a QTextBlock is part of a fenced code block.

        Relies on font family rather than fontFixedPitch() because
        Qt's setMarkdown() does not consistently set the fixed-pitch
        property on <pre> blocks (see debug_codeblocks.py).
        """
        families = block.charFormat().fontFamilies()
        if families:
            family = families[0].lower()
            if any(kw in family for kw in ("courier", "monospace", "consolas", "mono")):
                return True
        return False

    def _style_code_blocks(self):
        """Post-process after setMarkdown(): apply theme-aware
        background and spacing to fenced code blocks.

        Uses QTextBlockFormat rather than CSS because
        setMarkdown()'s inline character formats for <pre>
        override document-level stylesheet rules.
        """
        dark = is_dark_theme()
        code_bg = QColor("#282c34") if dark else QColor("#eaecf0")

        doc = self._browser.document()
        block = doc.begin()

        while block.isValid():
            if self._block_is_code_block(block):
                bfmt = block.blockFormat()
                bfmt.setBackground(code_bg)
                bfmt.setTopMargin(10)
                bfmt.setBottomMargin(10)
                bfmt.setLeftMargin(14)
                bfmt.setRightMargin(14)
                bfmt.setNonBreakableLines(True)
                cursor = QTextCursor(block)
                cursor.setBlockFormat(bfmt)
            block = block.next()

        # Second pass: merge consecutive code blocks (remove gap)
        block = doc.begin()
        prev_was_code = False
        while block.isValid():
            is_code = self._block_is_code_block(block)
            if is_code and prev_was_code:
                bfmt = block.blockFormat()
                bfmt.setTopMargin(0)
                cursor = QTextCursor(block)
                cursor.setBlockFormat(bfmt)
            prev_was_code = is_code
            block = block.next()

    # ── heading parsing ─────────────────────────────────────

    @staticmethod
    def _parse_headings(md_text: str):
        """Parse markdown headings into (level, text) list."""
        pat = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
        headings = []
        for m in pat.finditer(md_text):
            level = len(m.group(1))
            text = m.group(2).strip()
            headings.append((level, text))
        return headings

    def _populate_outline(self):
        """Fill the outline tree from current _headings."""
        self._outline_tree.blockSignals(True)
        self._outline_tree.clear()

        # stack of (level, item) for parent tracking
        stack = []

        for level, text in self._headings:
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.ItemDataRole.UserRole, text)  # raw heading for nav
            item.setToolTip(0, text)

            # find parent
            while stack and stack[-1][0] >= level:
                stack.pop()

            if stack and stack[-1][0] < level:
                stack[-1][1].addChild(item)
            else:
                self._outline_tree.addTopLevelItem(item)

            stack.append((level, item))

        self._outline_tree.blockSignals(False)

    # ── scroll-spy ───────────────────────────────────────────

    def _build_heading_block_map(self):
        """Map heading text → QTextBlock number for scroll-spy tracking."""
        self._heading_blocks.clear()

        doc = self._browser.document()
        block = doc.begin()
        heading_texts = set(text for _, text in self._headings)

        while block.isValid():
            fmt = block.blockFormat()
            hlevel = fmt.headingLevel()
            if hlevel > 0:
                text = block.text().strip()
                if text in heading_texts:
                    # find corresponding tree item
                    tree_item = self._find_tree_item(text)
                    self._heading_blocks.append(
                        (block.blockNumber(), hlevel, text, tree_item)
                    )
            block = block.next()

        # sort by block number
        self._heading_blocks.sort(key=lambda x: x[0])

    def _find_tree_item(self, text: str) -> QTreeWidgetItem | None:
        """Recursively search the outline tree for an item with given heading text."""
        def _search(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) == text:
                    return child
                found = _search(child)
                if found:
                    return found
            return None

        for i in range(self._outline_tree.topLevelItemCount()):
            item = self._outline_tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == text:
                return item
            found = _search(item)
            if found:
                return found
        return None

    def _on_scroll_changed(self, _value: int):
        """Debounced scroll handler → spy callback."""
        if self._navigating:
            return
        self._spy_timer.start()

    def _on_scroll_spy(self):
        """Highlight the outline item corresponding to the top-visible heading."""
        if not self._heading_blocks:
            return

        # get the block at the top of the viewport
        cursor = self._browser.cursorForPosition(QPoint(0, 0))
        current_block = cursor.block().blockNumber()

        # find the last heading whose block is <= current_block
        active_item = None
        for block_num, _hlevel, _text, tree_item in self._heading_blocks:
            if block_num <= current_block:
                active_item = tree_item
            else:
                break

        if active_item:
            self._outline_tree.blockSignals(True)
            self._outline_tree.setCurrentItem(active_item)
            # ensure the item is visible (scroll tree to it)
            self._outline_tree.scrollToItem(
                active_item, QTreeWidget.ScrollHint.EnsureVisible
            )
            self._outline_tree.blockSignals(False)

    # ── document navigation ──────────────────────────────────

    def _on_outline_clicked(self, current: QTreeWidgetItem, _previous):
        """Scroll the content view to the selected heading."""
        if current is None:
            return

        heading_text = current.data(0, Qt.ItemDataRole.UserRole)
        if not heading_text:
            return

        self._navigating = True

        # reset cursor to top, then find heading
        cursor = self._browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._browser.setTextCursor(cursor)

        if self._browser.find(heading_text):
            self._browser.ensureCursorVisible()

        # release navigating flag after a short delay so the scroll
        # triggered by find() doesn't cause scroll-spy to fight us
        QTimer.singleShot(100, lambda: setattr(self, '_navigating', False))

    # ── search ───────────────────────────────────────────────

    def _on_search(self):
        """Search all documents and display results."""
        keyword = self._search_edit.text().strip()
        if not keyword:
            self._clear_search()
            return

        kw_lower = keyword.lower()
        results = []

        for doc_idx, (title, filepath) in enumerate(self._docs):
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = content.splitlines()

            for li, line in enumerate(lines):
                s = line.strip()
                if not s or s.startswith("#") or s.startswith(">"):
                    continue
                if kw_lower in s.lower():
                    heading = self._find_nearest_heading(lines, li)
                    # collect context lines (2 above, 2 below)
                    ctx_start = max(0, li - 2)
                    ctx_end = min(len(lines), li + 3)
                    context = []
                    for ci in range(ctx_start, ctx_end):
                        context.append((
                            lines[ci].rstrip(),
                            ci == li
                        ))
                    results.append((doc_idx, heading, s[:100], s, context))

        self._search_results = results
        self._show_search_results()

    def _on_search_edited(self, text: str):
        """If the input is cleared, revert to the document view."""
        if not text.strip():
            self._clear_search()

    def _clear_search(self):
        """Clear search and restore the current document view."""
        self._search_results.clear()
        self._search_status.clear()
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        # restore the current document view
        if self._current_doc_path:
            self._load_doc(self._current_doc_path)
        self._outline_tree.show()

    def _show_search_results(self):
        """Render search results as HTML in the main content area."""
        keyword = self._search_edit.text().strip()

        ndocs = len({r[0] for r in self._search_results})
        nmatches = len(self._search_results)
        self._search_status.setText(
            self.tr("在 {0} 篇中找到 {1} 处匹配").format(ndocs, nmatches)
        )

        # map anchor id -> (doc_idx, matched_text)
        self._search_anchors = {}

        c = self._result_block_css()
        text_muted = c["text_muted"]
        kw_escaped = self._escape_html(keyword)
        html = (
            "<div style='margin-bottom:20px;'>"
            f"<h2 style='margin:0 0 6px 0;'>{self.tr('搜索结果')}</h2>"
            f"<p style='color:{text_muted}; margin:0;'>"
            f"{self.tr('关键词')}: <code>{kw_escaped}</code>  —  "
            f"{self.tr('在 {0} 篇中找到 {1} 处匹配').format(ndocs, nmatches)}"
            f"</p></div>"
        )

        last_doc_idx = -1
        for i, (doc_idx, heading, snippet, matched, context_lines) in enumerate(
            self._search_results
        ):
            # document heading (only show once per doc)
            if doc_idx != last_doc_idx:
                doc_title = self._escape_html(self._docs[doc_idx][0])
                html += (
                    f"<h3 style='margin:22px 0 8px 0; color:{c['accent']}; "
                    f"border-bottom:1px solid {c['border']}; "
                    f"padding-bottom:4px;'>{doc_title}</h3>"
                )
                last_doc_idx = doc_idx

            anchor_id = f"sr-{i}"
            self._search_anchors[anchor_id] = (doc_idx, matched)

            # card-style result block
            heading_label = (
                f"<span style='color:{text_muted}; font-size:smaller;'>"
                f"{self._escape_html(heading)}</span>"
            ) if heading else ""

            html += (
                f"<div style='margin:10px 0 10px 16px; "
                f"border:1px solid {c['border']}; "
                f"border-left:3px solid {c['accent']}; "
                f"border-radius:{c['radius']}; "
                f"background:{c['bg']}; overflow:hidden;'>"
            )
            if heading_label:
                html += (
                    f"<div style='padding:4px 10px; "
                    f"border-bottom:1px solid {c['border']}; "
                    f"background:{c['heading_bg']}; "
                    f"font-size:smaller;'>{heading_label}</div>"
                )
            html += (
                "<div style='font-family:Consolas,monospace; font-size:13px; "
                "line-height:1.7; overflow-x:auto;'>"
            )

            for line_text, is_match in context_lines:
                escaped = self._escape_html(line_text)
                if is_match:
                    html += (
                        f"<div style='background:{c['highlight_bg']}; "
                        f"padding:3px 10px; white-space:pre; "
                        f"border-radius:{c['highlight_radius']}; "
                        f"margin:1px 4px;'>"
                        f"<a href='search:{anchor_id}' "
                        f"style='text-decoration:none; color:{c['highlight_fg']}; "
                        f"display:block;'>{escaped}</a></div>"
                    )
                else:
                    html += (
                        f"<div style='padding:3px 10px; white-space:pre; "
                        f"color:{c['text_muted']};'>{escaped}</div>"
                    )

            html += "</div></div>"

        html += (
            f"<p style='color:{c['text_muted']}; font-size:smaller; "
            f"margin-top:8px;'>{self.tr('点击结果跳转到对应位置')}</p>"
        )

        self._browser.setHtml(html)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Minimal HTML escaping for text rendered in search results."""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove common inline markdown formatting so find() can match rendered text."""
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'~~(.+?)~~', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        return text

    def _result_block_css(self) -> dict:
        """Return theme-aware CSS values for search result blocks."""
        dark = is_dark_theme()
        if dark:
            return {
                "border": "#44475a",
                "bg": "#21252c",
                "heading_bg": "#1a1d23",
                "accent": "#5dade2",
                "highlight_bg": "rgba(93,173,226,0.15)",
                "highlight_fg": "#e0e0e0",
                "text_muted": "#6a7080",
                "radius": "8px",
                "highlight_radius": "4px",
            }
        else:
            return {
                "border": "#c0c4cc",
                "bg": "#eaecf0",
                "heading_bg": "#dfe2e7",
                "accent": "#1e93e5",
                "highlight_bg": "rgba(30,147,229,0.12)",
                "highlight_fg": "#222",
                "text_muted": "#808894",
                "radius": "8px",
                "highlight_radius": "4px",
            }

    def _on_search_anchor_clicked(self, url: QUrl):
        """Handle clicks on links in the content browser.

        - search:  → internal navigation to a search result
        - http/https/mailto → open via system browser
        """
        if url.scheme() == "search":
            anchor_id = url.path().lstrip("/")
            if anchor_id not in self._search_anchors:
                return
            doc_idx, matched = self._search_anchors[anchor_id]
            self._activate_tab(doc_idx)
            # full-document search for the matched text
            plain = self._strip_markdown(matched)
            cursor = self._browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._browser.setTextCursor(cursor)
            if self._browser.find(plain):
                self._browser.ensureCursorVisible()
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)

    @staticmethod
    def _find_nearest_heading(lines, line_idx):
        """Walk backwards to find the nearest ## heading as context."""
        for i in range(line_idx - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith("## "):
                return line[3:].strip()
            if line.startswith("# ") and not line.startswith("##"):
                return line[2:].strip()
        return ""

    # ── placeholder ──────────────────────────────────────────

    def _show_placeholder(self, message: str):
        """Show a centered placeholder when no docs are available."""
        html = (
            "<div style='text-align:center; padding:80px 20px; color:#888;'>"
            "<p style='font-size:48px; margin:0 0 16px 0; color:#ccc;'>?</p>"
            f"<p style='font-size:18px; margin:0;'>{message}</p>"
            "<p style='font-size:13px; margin-top:8px; color:#999;'>"
            f"{self.tr('请将 .md 文档放入 {0} 目录后重新打开')}<br>"
            f"<code style='background:#f0f0f0; padding:2px 6px;"
            f"border-radius:3px;'>docs/help/</code></p></div>"
        )
        self._browser.setHtml(html)
        self._status_label.setText(self.tr("无可用文档"))
        self._outline_tree.clear()

    @staticmethod
    def _extract_title(filepath: Path) -> str:
        """Extract the first `# ` heading as the document title."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            return filepath.stem
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("# ") and not s.startswith("##"):
                return s[2:].strip()
        return filepath.stem
