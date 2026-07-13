"""
Help documentation viewer with heading navigation and cross-doc search.

Renders markdown files from docs/help/ in a non-modal QDialog.
Left sidebar: document list + current doc's heading outline.
Content area: QTextBrowser.setMarkdown() (Qt 6.8+).
Search renders results as clickable HTML in the content area
with VSCode-style context blocks and borders.
"""

import re
from pathlib import Path

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices, QTextCursor
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
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
        self.resize(920, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        # data
        self._docs = []            # [(title, Path), ...]
        self._headings = []        # [(level, text), ...] for current doc
        self._search_results = []  # [(doc_idx, heading, snippet, matched), ...]

        self._build_ui()
        self._connect_signals()
        self._load_docs()

    # ── UI construction ──────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self._build_header(layout)
        self._build_content(layout)
        self._build_statusbar(layout)

    def _build_header(self, layout):
        header = QWidget()
        header.setObjectName("HelpHeader")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 8, 16, 8)

        title = QLabel(self.tr("使用手册"))
        title.setObjectName("HelpTitle")
        hlay.addWidget(title)

        hlay.addStretch()

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("HelpSearchInput")
        self._search_edit.setPlaceholderText(self.tr("搜索文档..."))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        hlay.addWidget(self._search_edit)

        layout.addWidget(header)

    def _build_content(self, layout):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── left sidebar ──────────────────────────────────
        left = QWidget()
        left.setObjectName("HelpSidebar")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 8, 0, 8)
        left_lay.setSpacing(4)

        doc_label = QLabel(self.tr("文档"))
        doc_label.setObjectName("HelpSectionLabel")
        left_lay.addWidget(doc_label)

        self._doc_list = QListWidget()
        self._doc_list.setObjectName("HelpDocList")
        left_lay.addWidget(self._doc_list)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        left_lay.addWidget(sep)

        self._outline_label = QLabel(self.tr("本节目录"))
        self._outline_label.setObjectName("HelpSectionLabel")
        left_lay.addWidget(self._outline_label)

        self._outline_list = QListWidget()
        self._outline_list.setObjectName("HelpOutlineList")
        left_lay.addWidget(self._outline_list, 1)

        left.setMinimumWidth(180)
        left.setMaximumWidth(280)
        splitter.addWidget(left)

        # ── content area ──────────────────────────────────
        self._browser = QTextBrowser()
        self._browser.setObjectName("HelpContent")
        self._browser.setOpenExternalLinks(False)
        splitter.addWidget(self._browser)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 720])

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
        self._doc_list.currentRowChanged.connect(self._on_doc_changed)
        self._outline_list.currentRowChanged.connect(self._on_outline_clicked)
        self._search_edit.returnPressed.connect(self._on_search)
        self._search_edit.textChanged.connect(self._on_search_edited)
        self._browser.anchorClicked.connect(self._on_search_anchor_clicked)

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
        """Scan docs/help/ for .md files and populate the doc list."""
        self._docs.clear()
        self._doc_list.clear()

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

        for title, _ in self._docs:
            self._doc_list.addItem(title)

        if self._docs:
            self._doc_list.setCurrentRow(0)

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
        self._outline_label.setText(self.tr("本节目录"))
        self._outline_list.clear()

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

        self._browser.setMarkdown(content)

        # ensure sidebar outline is visible (may have been hidden by search)
        self._outline_label.show()
        self._outline_list.show()

        # parse headings for the outline
        self._headings = self._parse_headings(content)
        self._populate_outline()

        title = self._extract_title(filepath)
        self._current_doc_path = filepath  # remember for search restore
        n_total = len(self._docs)
        self._status_label.setText(
            self.tr("共 {0} 篇  |  当前: {1}").format(n_total, title)
        )

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
        """Fill the heading outline list from current _headings."""
        self._outline_label.setText(self.tr("本节目录"))
        self._outline_list.blockSignals(True)
        self._outline_list.clear()

        for level, text in self._headings:
            indent = "\u3000\u3000" * (level - 1)
            prefix = "" if level == 1 else "├ "
            item = QListWidgetItem(f"{indent}{prefix}{text}")
            # store raw heading text for find() navigation
            item.setData(Qt.ItemDataRole.UserRole, text)
            self._outline_list.addItem(item)

        self._outline_list.blockSignals(False)

    # ── document navigation ──────────────────────────────────

    def _on_doc_changed(self, row: int):
        if row < 0 or row >= len(self._docs):
            return
        _, filepath = self._docs[row]
        self._load_doc(filepath)

    def _on_outline_clicked(self, row: int):
        """Scroll the content view to the selected heading."""
        if row < 0 or row >= len(self._headings):
            return
        _, heading_text = self._headings[row]

        # reset cursor to top, then find heading
        cursor = self._browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._browser.setTextCursor(cursor)

        if self._browser.find(heading_text):
            self._browser.ensureCursorVisible()

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
        self._outline_label.setText(self.tr("本节目录"))
        self._outline_label.show()
        self._outline_list.show()
        self._search_status.clear()
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        # restore the current document view
        if hasattr(self, '_current_doc_path') and self._current_doc_path:
            self._load_doc(self._current_doc_path)

    def _show_search_results(self):
        """Render search results as HTML in the main content area."""
        keyword = self._search_edit.text().strip()
        # hide the outline section during search
        self._outline_label.hide()
        self._outline_list.hide()

        ndocs = len({r[0] for r in self._search_results})
        nmatches = len(self._search_results)
        self._search_status.setText(
            self.tr("在 {0} 篇中找到 {1} 处匹配").format(ndocs, nmatches)
        )

        # map anchor id -> (doc_idx, matched_text)
        self._search_anchors = {}

        c = self._result_block_css()
        kw_escaped = self._escape_html(keyword)
        html = (
            "<div style='margin-bottom:20px;'>"
            f"<h2 style='margin:0 0 6px 0;'>{self.tr('搜索结果')}</h2>"
            f"<p style='color:#888; margin:0;'>"
            f"{self.tr('关键词')}: <code>{kw_escaped}</code>  —  "
            f"{self.tr('在 {0} 篇中找到 {1} 处匹配').format(ndocs, nmatches)}"
            f"</p></div>"
        )

        last_doc_idx = -1
        for i, (doc_idx, heading, snippet, matched, context_lines) in enumerate(self._search_results):
            # document heading (only show once per doc)
            if doc_idx != last_doc_idx:
                doc_title = self._escape_html(self._docs[doc_idx][0])
                html += (
                    f"<h3 style='margin:22px 0 8px 0; color:{c['accent']}; "
                    f"border-bottom:1px solid {c['border']}; padding-bottom:4px;'>{doc_title}</h3>"
                )
                last_doc_idx = doc_idx

            anchor_id = f"sr-{i}"
            self._search_anchors[anchor_id] = (doc_idx, matched)

            # PanelGroupBox-style block
            heading_label = (
                f"<span style='color:#888; font-size:smaller;'>"
                f"{self._escape_html(heading)}</span>"
            ) if heading else ""

            hl_bg = c['highlight_bg']
            html += (
                f"<div style='margin:10px 0 10px 16px; border:1px solid {c['border']}; "
                f"border-left:3px solid {c['accent']}; border-radius:6px; "
                f"background:{c['bg']}; overflow:hidden;'>"
            )
            if heading_label:
                html += (
                    f"<div style='padding:3px 8px; border-bottom:1px solid {c['border']}; "
                    f"background:{c['heading_bg']}; font-size:smaller;'>{heading_label}</div>"
                )
            html += (
                "<div style='font-family:Consolas,monospace; font-size:13px; "
                "line-height:1.6; overflow-x:auto;'>"
            )

            for line_text, is_match in context_lines:
                escaped = self._escape_html(line_text)
                if is_match:
                    html += (
                        f"<div style='background:{hl_bg}; padding:2px 8px; white-space:pre; "
                        f"color:{c['highlight_fg']};'>"
                        f"<a href='search:{anchor_id}' "
                        f"style='text-decoration:none; color:inherit; display:block;'>"
                        f"{escaped}</a></div>"
                    )
                else:
                    html += (
                        f"<div style='padding:2px 8px; white-space:pre; color:#888;'>{escaped}</div>"
                    )

            html += "</div></div>"

        html += (
            f"<p style='color:#999; font-size:smaller; margin-top:8px;'>"
            f"{self.tr('点击结果跳转到对应位置')}</p>"
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
        """Return theme-aware CSS values matching PanelGroupBox style."""
        dark = is_dark_theme()
        if dark:
            return {
                "border": "#535671",
                "bg": "#21252b",
                "heading_bg": "#1d2127",
                "accent": "#5dade2",
                "highlight_bg": "#383842",
                "highlight_fg": "#e0e0e0",
            }
        else:
            return {
                "border": "#b3b6bf",
                "bg": "#e1e4eb",
                "heading_bg": "#d4d7de",
                "accent": "#1e93e5",
                "highlight_bg": "#e8e8cc",
                "highlight_fg": "#222",
            }

    def _on_search_anchor_clicked(self, url: QUrl):
        """Handle clicks on links in the content browser.

        - search:  → internal navigation to a search result
        - http/https/mailto → open via system browser
        """
        if url.scheme() == "search":
            # internal navigation to a search result
            anchor_id = url.path().lstrip("/")
            if anchor_id not in self._search_anchors:
                return
            doc_idx, matched = self._search_anchors[anchor_id]
            # switch doc and navigate to match
            self._doc_list.blockSignals(True)
            self._doc_list.setCurrentRow(doc_idx)
            self._doc_list.blockSignals(False)
            _, filepath = self._docs[doc_idx]
            self._load_doc(filepath)
            # navigate to the matched text (strip markdown for accurate find)
            plain = self._strip_markdown(matched)
            cursor = self._browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._browser.setTextCursor(cursor)
            if self._browser.find(plain):
                self._browser.ensureCursorVisible()
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        # other schemes (e.g. file) are ignored for safety

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
