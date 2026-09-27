"""
Batch font-format commands for the Font Style Manager.

BatchFontformatCommand applies a FontFormat change to text blocks across
all pages of a project — not just the currently visible canvas page.
"""

import copy
from typing import Dict, List, Optional, Tuple

from qtpy.QtCore import QCoreApplication

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.proj_imgtrans import ProjImgTrans

from .textedit_commands import replay_guard


class BatchFontformatCommand(QUndoCommand):
    """Undoable batch application of a FontFormat to multiple blocks.

    Blocks on the *current* page are handled through their live TextBlkItem
    instances (HTML / rect captured up front for full restoration); blocks on
    other pages are restored at the TextBlock data level. Every change dict
    carries its block's own old/new FontFormat (captured at collect time), so
    undo restores each block's exact previous format.

    A base-style edit (``utils/base_styles.py::BaseStyle.fontformat`` changed in
    place by the style manager before the command is pushed) is undone through
    the optional *base_snapshot*: the live ``BaseStyle`` object plus its
    pre-edit and post-edit ``FontFormat``. It is restored on undo/redo even when
    *changes* is empty (a base style with no blocks is still an undoable edit).
    """

    def __init__(
        self,
        proj: ProjImgTrans,
        scene_manager,
        changes: List[Dict],
        description: str = "",
        *,
        base_snapshot: Optional[Tuple] = None,
    ):
        """Initialize the batch command.

        Args:
            proj: The project instance.
            scene_manager: SceneTextManager for live-item access.
            changes: List of dicts, each with:
                - pagename: str
                - block_idx: int
                - old_ffmt: FontFormat (deep copy before modification)
                - new_ffmt: FontFormat (the new format to apply)
            description: Optional undo-stack label.
            base_snapshot: Optional ``(base_style, old_ffmt, new_ffmt)`` where
                *base_style* is the live ``BaseStyle`` and the two formats are
                its pre-edit / post-edit values. Both are deep-copied here, so
                a caller may pass the live (already mutated) object safely.
        """
        super().__init__(
            description
            or QCoreApplication.translate("UndoCommand", "Apply Font Format")
        )
        self.proj = proj
        self.scene_manager = scene_manager
        self.changes: List[Dict] = changes
        self._first_redo = True

        # Deep copies freeze the base style's pre/post formats: the caller
        # mutates base_style.fontformat in place, so an aliased snapshot would
        # be corrupted by the very edit it is meant to undo.
        self._base_style = None
        self._base_old_ffmt = None
        self._base_new_ffmt = None
        if base_snapshot:
            self._base_style, old_ffmt, new_ffmt = base_snapshot
            self._base_old_ffmt = _copy_ffmt(old_ffmt)
            self._base_new_ffmt = _copy_ffmt(new_ffmt)

        # For current-page blocks we need to capture pre-change state
        # from live TextBlkItem instances so we can restore HTML + rect.
        self._old_html: Dict[str, str] = {}  # key = "{pagename}:{idx}"
        self._old_rect: Dict[str, object] = {}

        current_pname = proj.current_img
        for ch in changes:
            pname = ch["pagename"]
            bidx = ch["block_idx"]
            if pname == current_pname:
                item = _find_blk_item(scene_manager, bidx)
                if item is not None:
                    key = f"{pname}:{bidx}"
                    self._old_html[key] = item.toHtml()
                    self._old_rect[key] = item.absBoundingRect(qrect=True)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._apply_format("new")
        self._apply_base_format("new")

    def undo(self):
        self._apply_format("old")
        self._apply_base_format("old")

    # ── helpers ──────────────────────────────────────────────────────

    def _apply_base_format(self, which: str):
        """Restore the base style's FontFormat to its *old* or *new* snapshot.

        Assigns a fresh deep copy so the live style never aliases the frozen
        snapshot; runs independently of *changes* (empty change list is fine).
        """
        if self._base_style is None:
            return
        snapshot = self._base_old_ffmt if which == "old" else self._base_new_ffmt
        if snapshot is None:
            return
        self._base_style.fontformat = _copy_ffmt(snapshot)

    def _apply_format(self, which: str):
        """Apply *old* or *new* FontFormat to every block in the change list."""
        current_pname = self.proj.current_img
        sm = self.scene_manager

        for ch in self.changes:
            pname = ch["pagename"]
            bidx = ch["block_idx"]
            ffmt = ch[f"{which}_ffmt"]
            key = f"{pname}:{bidx}"

            page = self.proj.pages.get(pname)
            if page is None or not 0 <= bidx < len(page):
                continue
            blk = page[bidx]
            blk.fontformat = ffmt

            if pname == current_pname:
                # Live item on the visible canvas → restore fully
                item = _find_blk_item(sm, bidx)
                if item is None:
                    continue
                try:
                    with replay_guard(item):
                        if which == "old" and key in self._old_html:
                            # Full restoration (HTML + rect + format)
                            item.setHtml(self._old_html[key])
                            item.set_fontformat(ffmt)
                            item.setRect(self._old_rect[key])
                        else:
                            item.set_fontformat(ffmt, set_char_format=True)
                except RuntimeError:
                    continue
                self._clear_trans_undo_stack(sm, bidx)
            else:
                # Off-page block: data-level restore + lazy re-render on visit
                self.proj.mark_page_needs_rerender(pname)

    def _clear_trans_undo_stack(self, sm, bidx: int):
        # The panel editor's own undo stack could otherwise replay text from
        # before the batch apply. The widget may be gone by undo time.
        try:
            pair_w = sm.pairwidget_list[bidx]
            if pair_w is not None:
                pair_w.e_trans.document().clearUndoRedoStacks()
        except (RuntimeError, IndexError, AttributeError):
            pass


# ── module helpers ───────────────────────────────────────────────────


def _copy_ffmt(ffmt):
    """Deep-copy a FontFormat-like object (``.deepcopy`` when available)."""
    if ffmt is None:
        return None
    deep = getattr(ffmt, "deepcopy", None)
    if callable(deep):
        return deep()
    return copy.deepcopy(ffmt)


def _find_blk_item(scene_manager, block_idx: int):
    """Return the TextBlkItem for *block_idx* on the current page, or None."""
    try:
        tbi_list = scene_manager.textblk_item_list
        if 0 <= block_idx < len(tbi_list):
            return tbi_list[block_idx]
    except Exception:
        pass
    return None
