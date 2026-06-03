"""
Batch font-format commands for the Font Style Manager.

BatchFontformatCommand applies a FontFormat change to text blocks across
all pages of a project — not just the currently visible canvas page.
"""

from typing import Dict, List

try:
    from qtpy.QtWidgets import QUndoCommand
except ImportError:
    from qtpy.QtGui import QUndoCommand

from utils.fontformat import FontFormat
from utils.proj_imgtrans import ProjImgTrans


class BatchFontformatCommand(QUndoCommand):
    """Undoable batch application of a FontFormat to multiple blocks.

    Handles blocks on the *current* page via live TextBlkItem instances
    (storing old HTML / rect / format) and blocks on *other* pages by
    modifying TextBlock.fontformat directly in the project data structure.
    """

    def __init__(
        self,
        proj: ProjImgTrans,
        scene_manager,
        changes: List[Dict],
        description: str = "",
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
        """
        super().__init__(description)
        self.proj = proj
        self.scene_manager = scene_manager
        self.changes: List[Dict] = changes
        self._first_redo = True

        # For current-page blocks we need to capture pre-change state
        # from live TextBlkItem instances so we can restore HTML + rect.
        self._old_html: Dict[str, str] = {}    # key = "{pagename}:{idx}"
        self._old_rect: Dict[str, object] = {}
        self._old_fmt: Dict[str, FontFormat] = {}

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
                    self._old_fmt[key] = item.get_fontformat()

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            return
        self._apply_format("new")

    def undo(self):
        self._apply_format("old")

    # ── helpers ──────────────────────────────────────────────────────

    def _apply_format(self, which: str):
        """Apply *old* or *new* FontFormat to every block in the change list."""
        current_pname = self.proj.current_img
        sm = self.scene_manager

        for ch in self.changes:
            pname = ch["pagename"]
            bidx = ch["block_idx"]
            ffmt = ch[f"{which}_ffmt"]
            key = f"{pname}:{bidx}"

            if pname == current_pname:
                # Live item on the visible canvas → restore fully
                item = _find_blk_item(sm, bidx)
                if item is None:
                    continue
                if which == "old" and key in self._old_html:
                    # Full restoration (HTML + rect + format)
                    item.setHtml(self._old_html[key])
                    item.set_fontformat(ffmt)
                    item.setRect(self._old_rect[key])
                    # Clear the trans widget undo stack
                    try:
                        pair_w = sm.pairwidget_list[bidx]
                        if pair_w is not None:
                            pair_w.e_trans.document().clearUndoRedoStacks()
                    except Exception:
                        pass
                else:
                    item.set_fontformat(ffmt, set_char_format=True)
                    try:
                        pair_w = sm.pairwidget_list[bidx]
                        if pair_w is not None:
                            pair_w.e_trans.document().clearUndoRedoStacks()
                    except Exception:
                        pass
            else:
                # Off-page block → modify TextBlock.fontformat directly
                blk = self.proj.pages[pname][bidx]
                blk.fontformat = ffmt


# ── module helpers ───────────────────────────────────────────────────

def _find_blk_item(scene_manager, block_idx: int):
    """Return the TextBlkItem for *block_idx* on the current page, or None."""
    try:
        tbi_list = scene_manager.textblk_item_list
        if 0 <= block_idx < len(tbi_list):
            return tbi_list[block_idx]
    except Exception:
        pass
    return None
