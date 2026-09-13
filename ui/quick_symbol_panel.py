"""Soft keyboard (kana / symbol) content for the panel-rail feature.

Hosted by a ``SymbolFloatPanel`` that edge-follows the focused editor
(anchored just outside the text column, over the canvas); the rail icon
is a **feature toggle** (``pcfg.symbol_keyboard_enabled``): while
enabled, focusing a text editor pops the keyboard up beside that editor
(see ``ui/text_panel.py`` focus orchestration) and leaving the editor
hides it.

Three zones:

- Romaji line: type romaji, live kana preview, Enter inserts into the
  target editor and hands focus back to it (so the cursor is visible
  where the text landed).
- Paged key grid: kana page (gojūon + dakuten + small kana, hiragana /
  katakana share the grid shape) and symbols page (manga symbols +
  ``pcfg.quick_insert_characters``).
- Function row: full-width space and backspace.

Keys are ``NoFocus`` — clicking never steals focus from the editor, the
blinking cursor in the editor is always the insertion target.  The
romaji line is the only focus-taking part; its Enter re-focuses the
target editor before inserting so the change lands inside a typing
session (undo history).  ``ui/canvas.py::note_source_edit`` additionally
reconstructs a session for any out-of-focus insertion as a safety net.
"""
import weakref
from typing import List, Optional

from qtpy.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    Qt,
)
from qtpy.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import (
    ConfigLineEdit,
    FloatDropPanel,
    ScrollBar,
    SmallParamLabel,
    Widget,
)
from utils.config import pcfg

# 符号分组：名字在字面量处显式标注上下文，供 ts 工具链提取
# （模块级数据禁止 self.tr(variable) 间接查表，见 AGENTS.md i18n 规则）
_SYMBOL_GROUPS = [
    (QCoreApplication.translate("QuickSymbolPanel", "Quotes"), [
        "「", "」", "『", "』", "〝", "〟", "【", "】", "（", "）",
    ]),
    (QCoreApplication.translate("QuickSymbolPanel", "Punctuation"), [
        "！", "？", "…", "——", "～", "〜", "〰", "·", "‼", "⁉",
    ]),
    (QCoreApplication.translate("QuickSymbolPanel", "Decoratives"), [
        "※", "♥", "♡", "●", "○", "■", "□", "◆", "◇", "♪", "♫", "♬",
    ]),
]

# 五十音：清音（传统行=辅音段、列=元音的查表布局，"" 为对位空格）
_KANA_MAIN = [
    ["あ", "い", "う", "え", "お"],
    ["か", "き", "く", "け", "こ"],
    ["さ", "し", "す", "せ", "そ"],
    ["た", "ち", "つ", "て", "と"],
    ["な", "に", "ぬ", "ね", "の"],
    ["は", "ひ", "ふ", "へ", "ほ"],
    ["ま", "み", "む", "め", "も"],
    ["や", "", "ゆ", "", "よ"],
    ["ら", "り", "る", "れ", "ろ"],
    ["わ", "", "", "", "を"],
    ["ん", "", "", "", ""],
]
# 浊音 / 半浊音 / 小书き / 记号（与清音同形并排，行数不超过清音）
_KANA_DAKUTEN = [
    ["が", "ぎ", "ぐ", "げ", "ご"],
    ["ざ", "じ", "ず", "ぜ", "ぞ"],
    ["だ", "ぢ", "づ", "で", "ど"],
    ["ば", "び", "ぶ", "べ", "ぼ"],
    ["ぱ", "ぴ", "ぷ", "ぺ", "ぽ"],
    ["ぁ", "ぃ", "ぅ", "ぇ", "ぉ"],
    ["っ", "ゃ", "ゅ", "ょ", "ゎ"],
    ["ー", "・", "。", "「", "」"],
    ["～", "…", "『", "』", ""],
]

_CELL_SIZE = 26
_GRID_SPACING = 2
_MAX_COLS = 10
_FUNC_KEY_WIDTH = 96


# ── Romaji → kana（wapuro 式，纯逻辑表，无 IME 依赖） ──────────────

_VOWELS = "aiueo"

_CONSONANT_ROWS = {
    "k": "かきくけこ", "g": "がぎぐげご",
    "s": "さしすせそ", "z": "ざじずぜぞ",
    "t": "たちつてと", "d": "だぢづでど",
    "n": "なにぬねの", "h": "はひふへほ",
    "b": "ばびぶべぼ", "p": "ぱぴぷぺぽ",
    "m": "まみむめも", "r": "らりるれろ",
}


def _build_romaji_table() -> dict:
    table: dict = {}

    # 清音 + 拗音生成（kya=きゃ / sya=しゃ / tyu=ちゅ …）
    small = {"a": "ゃ", "i": "ぃ", "u": "ゅ", "e": "ぇ", "o": "ょ"}
    for c, row in _CONSONANT_ROWS.items():
        for i, v in enumerate(_VOWELS):
            table[c + v] = row[i]
            table[c + "y" + v] = row[1] + small[v]

    # 习惯拼法 / 例外（后写覆盖生成项）
    table.update({
        # 单独元音（长音/词中元音：kyuu → きゅう）
        "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
        "ya": "や", "yu": "ゆ", "yo": "よ", "ye": "いぇ",
        "wa": "わ", "wo": "を", "wi": "うぃ", "we": "うぇ",
        "wyi": "ゐ", "wye": "ゑ",
        # ん 歧义（konnichiha → こんいちは）用 n' 断开
        "nn": "ん", "n": "ん", "n'": "ん",
        "-": "ー",
        "shi": "し", "sha": "しゃ", "shu": "しゅ", "sho": "しょ",
        "she": "しぇ",
        "chi": "ち", "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ",
        "che": "ちぇ",
        "tsu": "つ", "tsa": "つぁ", "tsi": "つぃ", "tse": "つぇ",
        "tso": "つぉ",
        "fu": "ふ", "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ",
        "fo": "ふぉ", "fyu": "ふゅ",
        "ji": "じ", "ja": "じゃ", "ju": "じゅ", "jo": "じょ",
        "je": "じぇ",
        "di": "ぢ", "du": "づ", "dzu": "づ",
        "vu": "ゔ", "va": "ゔぁ", "vi": "ゔぃ", "ve": "ゔぇ",
        "vo": "ゔぉ",
    })

    # 小书き：x/l 前缀
    for prefix in ("x", "l"):
        for v, ch in zip(_VOWELS, "ぁぃぅぇぉ"):
            table[prefix + v] = ch
            table[prefix + "y" + v] = small[v]
        table[prefix + "tu"] = "っ"
        table[prefix + "tsu"] = "っ"
        table[prefix + "wa"] = "ゎ"
    return table


_ROMAJI = _build_romaji_table()
_CONSONANT_SET = set(_CONSONANT_ROWS) | set("cjfwvylxgzkp")


def _to_katakana(text: str) -> str:
    """平假名 → 片假名（Unicode 平/片区间偏移 0x60，其余原样）。"""
    return "".join(
        chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in text
    )


def romaji_to_kana(text: str, katakana: bool = False) -> str:
    """Greedy longest-match wapuro conversion; unmatched letters pass through."""
    text = text.lower()
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        for length in (4, 3, 2, 1):
            seg = text[i:i + length]
            if seg in _ROMAJI:
                out.append(_ROMAJI[seg])
                i += length
                break
        else:
            ch = text[i]
            # 双写辅音 → 促音（kka → っか）
            if (
                ch in _CONSONANT_SET
                and ch not in "n"
                and i + 1 < n
                and text[i + 1] == ch
            ):
                out.append("っ")
                i += 1
            else:
                out.append(ch)
                i += 1
    result = "".join(out)
    return _to_katakana(result) if katakana else result


# ── 编辑器判定（软键盘触发范围 / 插入白名单共用） ────────────────────


def _engine_editor_classes() -> tuple:
    try:
        from ui.text_engine.editing import widgets as _engine_widgets

        return _engine_widgets.SourceTextEdit, _engine_widgets.TransTextEdit
    except ImportError:
        return ()


def is_source_editor(w: QWidget) -> bool:
    """Strictly a source (原文) editor — TransTextEdit subclasses
    SourceTextEdit, so isinstance would wrongly match the trans side."""
    from ui.textedit_area import SourceTextEdit

    for cls in (SourceTextEdit,) + _engine_editor_classes():
        if type(w) is cls:
            return True
    return False


def is_editor(w: QWidget) -> bool:
    from ui.textedit_area import SourceTextEdit, TransTextEdit

    return isinstance(w, (SourceTextEdit, TransTextEdit) + _engine_editor_classes())


def editor_in_scope(w: QWidget) -> bool:
    """软键盘触发判定：是编辑器，且通过触发范围过滤
    （``pcfg.symbol_keyboard_source_only`` 默认仅原文框）。"""
    if not is_editor(w):
        return False
    if pcfg.symbol_keyboard_source_only and not is_source_editor(w):
        return False
    return True


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


class QuickSymbolPanel(Widget):
    """Kana / symbol soft keyboard; inserts into the focused editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 最后聚焦过的文本编辑器（弱引用）。键盘自身控件（罗马字输入行）
        # 拿焦点不清空记忆；焦点落到其它任何非编辑器控件上立即清空 ——
        # 保证「没有明确焦点就不产生输入」。
        self._last_editor_ref: Optional[weakref.ref] = None
        self._katakana = False

        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        self.setMinimumWidth(310)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 全内容包滚动区：宿主窗口过矮时可滚动，任何尺寸不裁剪
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        ScrollBar(Qt.Orientation.Vertical, scroll)
        outer.addWidget(scroll)

        content = Widget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── 罗马字输入行 ─────────────────────────────────────────
        romaji_row = QHBoxLayout()
        romaji_row.setSpacing(4)
        self.romaji_edit = ConfigLineEdit("", content)
        self.romaji_edit.setPlaceholderText(
            self.tr("Romaji → kana (Enter to insert)")
        )
        self.romaji_edit.setClearButtonEnabled(True)
        self.romaji_edit.textChanged.connect(self._update_romaji_preview)
        self.romaji_edit.returnPressed.connect(self._insert_romaji)
        romaji_row.addWidget(self.romaji_edit, 1)

        self.kana_mode_btn = QToolButton(content)
        self.kana_mode_btn.setObjectName("SymbolCell")
        self.kana_mode_btn.setCheckable(True)
        self.kana_mode_btn.setFixedSize(_CELL_SIZE, _CELL_SIZE)
        self.kana_mode_btn.setText("あ")
        self.kana_mode_btn.setToolTip(self.tr("Hiragana / Katakana"))
        self.kana_mode_btn.toggled.connect(self._on_kana_mode_toggled)
        romaji_row.addWidget(self.kana_mode_btn)
        layout.addLayout(romaji_row)

        self.romaji_preview = QLabel(content)
        self.romaji_preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self.romaji_preview)

        # ── 页签行：假名 / 符号 切换 + 右侧退格 ──────────────────
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._kana_page_btn = self._page_btn(
            QCoreApplication.translate("QuickSymbolPanel", "Kana")
        )
        self._symbol_page_btn = self._page_btn(
            QCoreApplication.translate("QuickSymbolPanel", "Symbols")
        )
        self._kana_page_btn.setChecked(True)
        self._kana_page_btn.clicked.connect(self._show_kana_page)
        self._symbol_page_btn.clicked.connect(self._show_symbol_page)
        tab_row.addWidget(self._kana_page_btn)
        tab_row.addWidget(self._symbol_page_btn)
        tab_row.addStretch(1)
        backspace_key = self._cell("⌫", tip=self.tr("Backspace"))
        backspace_key.setFixedWidth(_FUNC_KEY_WIDTH)
        backspace_key.clicked.disconnect()
        backspace_key.clicked.connect(self._on_backspace)
        tab_row.addWidget(backspace_key)
        layout.addLayout(tab_row)

        # 假名页：清音 + 浊音/小书き/记号 并排
        self._kana_page = Widget(content)
        kana_layout = QHBoxLayout(self._kana_page)
        kana_layout.setContentsMargins(0, 0, 0, 0)
        kana_layout.setSpacing(6)
        self._kana_grid_holder = QHBoxLayout()
        self._kana_grid_holder.setSpacing(6)
        kana_layout.addLayout(self._kana_grid_holder)
        self._fill_kana_grids()
        layout.addWidget(self._kana_page)

        # 符号页
        self._symbol_page = Widget(content)
        self._symbol_grid = QGridLayout(self._symbol_page)
        self._symbol_grid.setSpacing(_GRID_SPACING)
        self._fill_symbol_grid()
        layout.addWidget(self._symbol_page)

        # ── 功能键行：全角空格 ──────────────────────────────────
        func_row = QHBoxLayout()
        func_row.setSpacing(4)
        space_key = self._cell("　", tip=self.tr("Full-width space"))
        space_key.setFixedWidth(_FUNC_KEY_WIDTH)
        func_row.addWidget(space_key)
        func_row.addStretch(1)
        layout.addLayout(func_row)

        layout.addStretch(1)
        scroll.setWidget(content)
        self._show_kana_page()

    # ── 构建 ─────────────────────────────────────────────────────

    def _page_btn(self, text: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("SymbolCell")
        btn.setText(text)
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedHeight(_CELL_SIZE)
        return btn

    def _cell(self, text: str, tip: str = "") -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("SymbolCell")
        btn.setText(text)
        btn.setToolTip(tip or text)
        btn.setFixedSize(_CELL_SIZE, _CELL_SIZE)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(self._on_symbol_clicked)
        return btn

    def _fill_symbol_grid(self):
        _clear_layout(self._symbol_grid)
        row = col = 0
        for group_name, symbols in self._symbol_groups():
            label = SmallParamLabel(group_name, parent=self)
            self._symbol_grid.addWidget(label, row, 0, 1, _MAX_COLS)
            row += 1
            col = 0
            for sym in symbols:
                self._symbol_grid.addWidget(
                    self._cell(sym), row, col, 1, 1
                )
                col += 1
                if col >= _MAX_COLS:
                    col = 0
                    row += 1
            row += 1

    def _symbol_groups(self):
        groups = list(_SYMBOL_GROUPS)
        custom = (pcfg.quick_insert_characters or "").strip()
        chars = [ch for ch in custom if not ch.isspace()]
        if chars:
            groups.append(
                (QCoreApplication.translate("QuickSymbolPanel", "Custom"), chars)
            )
        return groups

    def _fill_kana_grids(self):
        _clear_layout(self._kana_grid_holder)
        for rows in (_KANA_MAIN, _KANA_DAKUTEN):
            grid = QGridLayout()
            grid.setSpacing(_GRID_SPACING)
            for r, row in enumerate(rows):
                for c, ch in enumerate(row):
                    if not ch:
                        continue
                    text = _to_katakana(ch) if self._katakana else ch
                    grid.addWidget(self._cell(text), r, c, 1, 1)
            holder = QWidget(self._kana_page)
            holder.setLayout(grid)
            self._kana_grid_holder.addWidget(holder)

    # ── 交互 ─────────────────────────────────────────────────────

    def _show_kana_page(self):
        self._kana_page_btn.setChecked(True)
        self._symbol_page_btn.setChecked(False)
        self._kana_page.show()
        self._symbol_page.hide()

    def _show_symbol_page(self):
        self._symbol_page_btn.setChecked(True)
        self._kana_page_btn.setChecked(False)
        self._symbol_page.show()
        self._kana_page.hide()

    def refresh_symbols(self):
        """Re-read custom characters (config may have changed since last open)."""
        self._fill_symbol_grid()

    def _on_kana_mode_toggled(self, checked: bool):
        self._katakana = checked
        self.kana_mode_btn.setText("ア" if checked else "あ")
        self._fill_kana_grids()
        self._update_romaji_preview()

    def _update_romaji_preview(self):
        text = self.romaji_edit.text()
        if not text:
            self.romaji_preview.clear()
            return
        converted = romaji_to_kana(text, katakana=self._katakana)
        self.romaji_preview.setText(converted)
        self.romaji_preview.setToolTip(converted)

    def _insert_romaji(self):
        text = self.romaji_edit.text()
        if not text:
            return
        if self._insert_text(romaji_to_kana(text, katakana=self._katakana)):
            self.romaji_edit.clear()

    def _on_symbol_clicked(self):
        btn = self.sender()
        if isinstance(btn, QToolButton):
            self._insert_text(btn.text())

    def _on_backspace(self):
        """退格：删除目标编辑器光标前一字符（或选区），与会话链路兼容。

        先把焦点还给编辑器（focus_in 重开原文键入会话）再删——即使焦点
        此刻在罗马字输入行，删除也落在会话内进撤销历史。"""
        editor = self._target_editor()
        if editor is None:
            return
        editor.setFocus()
        try:
            cursor = editor.textCursor()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            else:
                cursor.deletePreviousChar()
            editor.setTextCursor(cursor)
        except RuntimeError:
            self._last_editor_ref = None

    # ── 焦点追踪与插入 ───────────────────────────────────────────

    def _on_focus_changed(self, _old: Optional[QWidget], new: Optional[QWidget]):
        if new is not None and is_editor(new):
            self._last_editor_ref = weakref.ref(new)
            return
        # 焦点落在键盘自身（罗马字输入行等）→ 保留编辑器记忆；
        # 落到其它任何控件 → 清空，杜绝用户无感知的误插入。
        w = new
        while w is not None:
            if w is self:
                return
            w = w.parentWidget()
        self._last_editor_ref = None

    def _target_editor(self):
        """当前插入目标：优先实焦点编辑器，否则用键盘内的记忆。"""
        fw = QApplication.focusWidget()
        if fw is not None and is_editor(fw):
            return fw
        editor = (
            self._last_editor_ref() if self._last_editor_ref is not None else None
        )
        if editor is None:
            return None
        try:
            editor.toPlainText()
        except RuntimeError:
            # 编辑器已随页面切换销毁，记忆作废
            self._last_editor_ref = None
            return None
        return editor

    def _insert_text(self, text: str) -> bool:
        editor = self._target_editor()
        if editor is None:
            return False
        # 焦点还给目标编辑框：光标可见（落在哪一目了然），且 focus_in
        # 会重开原文键入会话，插入正常进入撤销历史。
        editor.setFocus()
        try:
            if hasattr(editor, "insert_external_text"):
                editor.insert_external_text(text)
            else:
                cursor = editor.textCursor()
                cursor.insertText(text)
                editor.setTextCursor(cursor)
        except RuntimeError:
            self._last_editor_ref = None
            return False
        return True


class SymbolFloatPanel(FloatDropPanel):
    """贴框跟随的软键盘浮层（复用 ``FloatDropPanel`` 的宿主/外观骨架）。

    与按钮锚定的 ``FloatDropPanel`` 差异：锚定目标是**当前聚焦的编辑器**
    而非固定按钮——弹出时贴在编辑器外缘（默认画布侧，即编辑器左缘；
    画布过窄时改贴右缘），焦点换编辑器时随迁（``open_at_editor``；
    已可见时随迁走位置滑动而非重播淡入）；
    宿主缩放、编辑器随滚动移动/改尺寸都自动重锚。

    弹出不抢焦点（编辑器光标须保持可见；键区按钮全部 NoFocus），
    Esc/× 只隐藏，下次焦点进入编辑器由 ``_sync_symbol_keyboard`` 重弹。
    """

    MIN_WIDTH = 330
    MIN_HEIGHT = 280
    ANCHOR_GAP = 4
    ANIM_DURATION = 220
    ANIM_SLIDE = 16  # 显隐滑动距离（向编辑器方向出入）

    def __init__(self, title: str, content_widget: QWidget):
        super().__init__(title, content_widget, anchor=content_widget)
        self._editor_ref: Optional[weakref.ref] = None
        self._anim: Optional[QPropertyAnimation] = None
        self._hiding = False

    # ── public API ───────────────────────────────────────────────

    def open_at_editor(self, editor: QWidget) -> None:
        prev = self._editor_ref() if self._editor_ref is not None else None
        if prev is not editor:
            if prev is not None:
                try:
                    prev.removeEventFilter(self)
                except RuntimeError:
                    pass
            self._editor_ref = weakref.ref(editor)
            editor.installEventFilter(self)
        if self.isVisible() and not self._hiding and prev is editor:
            self.raise_()  # 已在该编辑器旁：不重播动画
            return
        if self.isVisible():
            # 已在其它编辑器旁（含正在收起途中）：随焦点滑到新锚点，
            # 不重播淡入；顺带取消进行中的收起动画
            if pcfg.animation_fps >= 0:
                self._move_animated()
            else:
                self._place()
            return
        self.open_panel()

    def open_panel(self) -> None:
        self._ensure_host()
        self._stop_anim()
        self._place()
        self.show()
        self.raise_()
        if pcfg.animation_fps >= 0:
            target = self.pos()
            self.move(target.x() + self.ANIM_SLIDE, target.y())
            self._start_anim(self.pos(), target)
        # 不 setFocus：焦点必须留在编辑器里（区别于 FloatDropPanel）

    def hide_keep_state(self) -> None:
        self._unwatch_editor()
        self._hide_animated()

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self.closed.emit()
        self._unwatch_editor()
        self._hide_animated()

    # ── internals ────────────────────────────────────────────────

    def _hide_animated(self) -> None:
        if not self.isVisible() or pcfg.animation_fps < 0:
            self.hide()
            return
        self._stop_anim()
        start = self.pos()
        self._start_anim(
            start, QPoint(start.x() + self.ANIM_SLIDE, start.y()),
            on_done=self.hide,
        )

    def _move_animated(self) -> None:
        """焦点切换时的随迁动画：从当前位置滑向新锚点（不重播淡入），
        并取消进行中的收起动画（面板保持可见）。"""
        self._stop_anim()
        old_pos = self.pos()
        self._place()
        new_pos = self.pos()
        if old_pos == new_pos:
            return
        self.move(old_pos)
        self._start_anim(old_pos, new_pos)

    def _start_anim(self, start, end, on_done=None) -> None:
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(self.ANIM_DURATION)
        anim.setEasingCurve(QEasingCurve.Type.InOutExpo)
        anim.setStartValue(start)
        anim.setEndValue(end)
        self._anim = anim
        self._hiding = on_done is not None
        anim.finished.connect(lambda: self._anim_done(anim, on_done))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _stop_anim(self) -> None:
        """停止当前动画；先清引用再 stop，让 finished 回调变空操作。"""
        if self._anim is not None:
            anim, self._anim = self._anim, None
            self._hiding = False
            anim.stop()

    def _anim_done(self, anim, on_done) -> None:
        if self._anim is not anim:
            return  # 已被 _stop_anim 打断
        self._anim = None
        self._hiding = False
        if on_done is not None:
            on_done()

    def _ensure_host(self) -> None:
        """宿主解析：从聚焦编辑器反查主窗口（内容面板自身无父级，
        ``FloatDropPanel`` 的锚点 ``window()`` 路径解析不到主窗口），
        面板挂进中央控件区，才能贴着文本列向画布方向展开。"""
        editor = self._editor_ref() if self._editor_ref is not None else None
        window = editor.window() if editor is not None else None
        central = (
            getattr(window, "centralWidget", lambda: None)()
            if window is not None
            else None
        )
        host = central or window
        if host is None or self.parentWidget() is host:
            return
        old = self.parentWidget()
        if old is not None:
            old.removeEventFilter(self)
        self.setParent(host)
        host.installEventFilter(self)

    def _unwatch_editor(self) -> None:
        editor = self._editor_ref() if self._editor_ref is not None else None
        if editor is not None:
            try:
                editor.removeEventFilter(self)
            except RuntimeError:
                pass
        self._editor_ref = None

    def _place(self) -> None:
        editor = self._editor_ref() if self._editor_ref is not None else None
        host = self.parentWidget()
        if editor is None or host is None:
            return
        try:
            e_tl = editor.mapTo(host, QPoint(0, 0))
        except RuntimeError:
            self._editor_ref = None
            return
        w = max(self.MIN_WIDTH, self.sizeHint().width())
        avail_h = host.height() - 2 * self.MARGIN
        h = min(max(self.MIN_HEIGHT, self.sizeHint().height()),
                max(self.MIN_HEIGHT, avail_h))
        # 默认贴编辑器左缘（画布侧空间充裕）；放不下改贴右缘，
        # 再兜底夹回宿主内
        x = e_tl.x() - w - self.ANCHOR_GAP
        if x < self.MARGIN:
            x = e_tl.x() + editor.width() + self.ANCHOR_GAP
        x = max(self.MARGIN, min(x, host.width() - w - self.MARGIN))
        y = max(self.MARGIN, min(e_tl.y(), host.height() - h - self.MARGIN))
        self.setGeometry(x, y, w, h)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if self._anim is None and self.isVisible() and et in (
            QEvent.Type.Resize, QEvent.Type.Move,
        ):
            if obj is self.parentWidget() and et == QEvent.Type.Resize:
                self._place()
            elif (
                self._editor_ref is not None
                and obj is self._editor_ref()
            ):
                self._place()
        return super().eventFilter(obj, event)
