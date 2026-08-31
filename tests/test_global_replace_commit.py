"""Global replace current-page commit regression (offscreen).

Background (2026-08-31): since stage 2.5 the batch replace applier
(``ui.textedit_commands.GlobalReplaceApplier``) mutates the current page's
widgets/items without pushing any undo command. The post-replace commit
(``ui.mainwindow.MainWindow._sync_and_commit_project``) used to gate its
UI → model sync on ``canvas.text_change_unsaved()`` — a probe of the canvas
undo-stack counter, which the applier never advances — so the replaced text
never reached the project model: JSON kept the old text and a page switch
made the replacement vanish.

The fix adds ``force_sync``; these tests bind the REAL
``_sync_and_commit_project`` and REAL
``ui.scenetext_manager.SceneTextManager.updateTextBlkList`` onto a shim and
assert both semantics: guarded (legacy) and forced (post-replace).

Run:
    ./ballontrans_pylibs_win/python.exe tests/test_global_replace_commit.py
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyqt6")

from qtpy.QtWidgets import QApplication, QGraphicsScene  # noqa: E402

from ui.scenetext_manager import SceneTextManager  # noqa: E402
from ui.textedit_commands import GlobalReplaceApplier  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


class _FakeProj:
    def __init__(self, block_list):
        self.pages = {"cur.png": block_list}
        self.current_img = "cur.png"
        self.img_valid = True
        self.save_calls = 0

    def current_block_list(self):
        return self.pages["cur.png"]

    def save(self, **kwargs):
        self.save_calls += 1


class _FakeCanvas:
    """Mimics the post-applier canvas state: the undo-stack counter was
    never advanced, so the unsaved-change probe reports False."""

    def text_change_unsaved(self):
        return False

    def setProjSaveState(self, _dirty):
        pass

    def update_saved_undostep(self):
        pass


class GlobalReplaceCommitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.mainwindow import MainWindow
        from ui.textitem import TextBlkItem

        cls.app = QApplication.instance() or QApplication([])
        cls.MainWindow = MainWindow
        cls.TextBlkItem = TextBlkItem
        cls.scene = QGraphicsScene()

    def _make_shim(self):
        """MainWindow-like shim carrying the real sync implementation."""
        blk = TextBlock(xyxy=[100, 100, 300, 200], translation="foo bar")
        blk._bounding_rect = [100, 100, 300, 200]
        item = self.TextBlkItem(blk=blk, idx=0)
        self.scene.addItem(item)
        item.setPlainText("foo bar")

        from ui.textedit_area import SourceTextEdit, TransTextEdit

        trans = TransTextEdit(0, None)
        trans.setPlainText("foo bar")
        src = SourceTextEdit(0, None)
        src.setPlainText("foo")
        pair = SimpleNamespace(e_trans=trans, e_source=src)

        proj = _FakeProj([blk])
        st_manager = SimpleNamespace(
            imgtrans_proj=proj, textblk_item_list=[item], pairwidget_list=[pair]
        )
        st_manager.updateTextBlkList = (
            SceneTextManager.updateTextBlkList.__get__(st_manager)
        )
        shim = SimpleNamespace(
            canvas=_FakeCanvas(), st_manager=st_manager, imgtrans_proj=proj
        )
        shim._sync_and_commit_project = (
            self.MainWindow._sync_and_commit_project.__get__(shim)
        )
        return blk, item, trans, src, proj, shim

    def _run_applier(self, item, trans, src, proj):
        GlobalReplaceApplier(
            {
                "src": [{"edit": src, "replace": "Xoo", "idx": 0}],
                "trans": [
                    {"edit": trans, "item": item, "matched_map": [[0, 3]]}
                ],
            },
            "X",
            proj,
        )

    def test_forced_sync_commits_replacement_to_model(self):
        """force_sync=True must push applier edits through to blk data."""
        blk, item, trans, src, proj, shim = self._make_shim()
        self._run_applier(item, trans, src, proj)
        # Widgets/items show the replacement…
        self.assertEqual(item.toPlainText(), "X bar")
        # …while the guarded sync would skip it (probe reports False).
        self.assertFalse(shim.canvas.text_change_unsaved())
        shim._sync_and_commit_project(force_sync=True)
        self.assertEqual(blk.translation, "X bar")
        self.assertEqual(blk.text, ["Xoo"])
        self.assertEqual(blk.rich_text, item.toHtml())
        self.assertEqual(shim.imgtrans_proj.save_calls, 1)

    def test_guarded_sync_keeps_legacy_skip(self):
        """Without force_sync the unsaved-probe gate stays authoritative.

        This pins the pre-fix behaviour: the applier bypasses the undo stack
        (no canvas push → ``num_pushed_textstep`` frozen → probe False), so
        the guarded sync skips ``updateTextBlkList`` and the model keeps the
        pre-replace text.
        """
        blk, item, trans, src, proj, shim = self._make_shim()
        self._run_applier(item, trans, src, proj)
        shim._sync_and_commit_project()
        self.assertEqual(blk.translation, "foo bar")
        self.assertEqual(shim.imgtrans_proj.save_calls, 1)


if __name__ == "__main__":
    unittest.main()
