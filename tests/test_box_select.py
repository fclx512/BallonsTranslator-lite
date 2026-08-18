"""Offscreen regression tests for the left-drag text-block interaction.

Left-drag in text edit mode is split by what is under the press
(2026-08-18):

- on EMPTY canvas it is the box select: selects the text blocks inside the
  dragged rectangle and nothing else — it must not pan the canvas, single
  clicks keep their native single-block selection, Ctrl/Shift drags add to
  the selection, and the shape control is re-synced after a real drag.
- on a TEXT BLOCK it is the MOVE gesture again (the hover move cursor /
  ``ItemIsMovable`` drag): the block — or its whole selection — follows the
  mouse, and no rubber band starts.

Right-drag stays a context-menu gesture and no longer opens the old rubber
band.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_box_select.py
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from qtpy.QtGui import QMouseEvent, QPointingDevice  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from ui.image_edit import ImageEditMode  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(xyxy, translation="测试文字"):
    blk = TextBlock(xyxy=list(xyxy), translation=translation)
    blk._bounding_rect = list(xyxy)
    return blk


class _ShapeControlSpy:
    """Stand-in for TextBlkShapeControl that records setBlkItem calls."""

    def __init__(self):
        self.blk_item = None
        self.calls = []
        self.rect = QRectF(0, 0, 1, 1)

    def isVisible(self):
        return False

    def setBlkItem(self, item):
        self.calls.append(item)
        self.blk_item = item

    # startCreateTextblock / endCreateTextblock touch these on the control.
    def setPos(self, *a):
        pass

    def setRotation(self, *a):
        pass

    def setRect(self, rect):
        self.rect = rect

    def rect(self):
        return self.rect

    def hide(self):
        pass

    def show(self):
        pass

    def hideControls(self):
        pass


class TestBoxSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.canvas import Canvas

        cls.Canvas = Canvas
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.textitem import TextBlkItem

        canvas = self.Canvas()
        canvas.imgtrans_proj = SimpleNamespace(
            img_valid=True, inpainted_valid=False
        )
        canvas.editor_index = 1  # text editor tab
        canvas.image_edit_mode = ImageEditMode.NONE
        canvas.txtblkShapeControl = _ShapeControlSpy()
        # Alignment snapping would perturb exact move deltas in the drag tests.
        canvas.alignment_enabled = False
        self.canvas = canvas
        self.gv = canvas.gv
        # Attach the view and give scene coords a 1:1, fully visible viewport
        # so we can deliver press/move/release through the real view.
        self.gv.setScene(canvas)
        canvas.setSceneRect(QRectF(0, 0, 800, 800))
        self.gv.setSceneRect(canvas.sceneRect())
        self.gv.resize(820, 820)
        self.TextBlkItem = TextBlkItem

        # Three blocks: two near each other, one far away.
        self.blk_rects = [
            (50, 50, 150, 150),    # A
            (220, 50, 320, 150),   # B
            (420, 320, 520, 420),  # C — far from A/B
        ]
        self.items = []
        for i, xyxy in enumerate(self.blk_rects):
            item = TextBlkItem(blk=_make_blk(xyxy), idx=i)
            canvas.addItem(item)
            item.setParentItem(canvas.textLayer)
            self.items.append(item)
        self.a, self.b, self.c = self.items
        # sanity: block rects really sit where the test expects
        self.a_center = QPointF(100, 100)
        self.b_center = QPointF(270, 100)

    # ── event helpers (delivered through the real view) ─────

    def _send(self, typ, scene_pos, button, buttons, modifiers):
        vp = self.gv.mapFromScene(QPointF(*scene_pos))
        lx, ly = vp.x(), vp.y()
        ev = QMouseEvent(
            typ,
            QPointF(lx, ly),
            QPointF(lx + 50, ly + 50),
            button,
            buttons,
            modifiers,
            QPointingDevice.primaryPointingDevice(),
        )
        if typ == QEvent.Type.MouseButtonPress:
            self.gv.mousePressEvent(ev)
        elif typ == QEvent.Type.MouseMove:
            self.gv.mouseMoveEvent(ev)
        else:
            self.gv.mouseReleaseEvent(ev)

    def _press(self, pos, button=Qt.MouseButton.LeftButton,
               modifiers=Qt.KeyboardModifier.NoModifier):
        self._send(
            QEvent.Type.MouseButtonPress, pos, button, button, modifiers
        )

    def _move(self, pos, button=Qt.MouseButton.LeftButton,
              modifiers=Qt.KeyboardModifier.NoModifier):
        self._send(
            QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, button,
            modifiers,
        )

    def _release(self, pos, button=Qt.MouseButton.LeftButton,
                 modifiers=Qt.KeyboardModifier.NoModifier):
        self._send(
            QEvent.Type.MouseButtonRelease, pos, button,
            Qt.MouseButton.NoButton, modifiers,
        )

    def _drag(self, start, end, button=Qt.MouseButton.LeftButton,
              modifiers=Qt.KeyboardModifier.NoModifier):
        self._press(start, button=button, modifiers=modifiers)
        # two moves: one inside the click threshold, one crossing it
        self._move(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2),
                   button=button, modifiers=modifiers)
        self._move(end, button=button, modifiers=modifiers)
        self._release(end, button=button, modifiers=modifiers)

    def _selected_idxs(self):
        return sorted(
            item.idx for item in self.canvas.selected_text_items()
        )

    # ── box select behavior ────────────────────────────────

    def test_drag_selects_only_boxed_blocks(self):
        self._drag((40, 40), (330, 170))  # covers A and B, not C
        self.assertEqual(self._selected_idxs(), [0, 1])
        self.assertFalse(self.c.isSelected())

    def test_drag_starting_on_block_moves_it(self):
        # A press on a text block is the MOVE gesture (move cursor active) —
        # the block follows the mouse and no rubber band starts.
        start = self.a_center
        before = self.a.pos()
        self._drag((start.x(), start.y()), (350, 300))
        self.assertEqual(self.a.pos() - before, QPointF(250, 200))
        self.assertFalse(self.canvas.rubber_band.isVisible())
        self.assertFalse(self.canvas.rubber_band_dragged)
        self.assertEqual(self._selected_idxs(), [0])

    def test_drag_on_selected_block_moves_whole_selection(self):
        # Qt's ItemIsMovable drag moves every selected block together.
        self.a.setSelected(True)
        self.b.setSelected(True)
        a_before = self.a.pos()
        b_before = self.b.pos()
        self._drag((100, 100), (250, 200))  # press on A, drag by (150, 100)
        self.assertEqual(self.a.pos() - a_before, QPointF(150, 100))
        self.assertEqual(self.b.pos() - b_before, QPointF(150, 100))

    def test_block_hover_sets_move_cursor(self):
        # The move-cursor style is what marks a block as draggable; the
        # editing block hands the cursor back to its text editor.
        self.a._update_move_cursor()
        self.assertEqual(
            self.a.cursor().shape(), Qt.CursorShape.SizeAllCursor
        )
        self.a.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        self.a._update_move_cursor()
        self.assertEqual(
            self.a.cursor().shape(), Qt.CursorShape.ArrowCursor
        )

    # ── text-block creation mode (W toggle) interplay ──────

    def test_textblock_mode_left_drag_still_box_selects(self):
        # W toggles textblock creation mode (defaults ON).  With nothing
        # selected its branch used to swallow left presses, killing the box
        # select — left press must fall through to the normal interaction.
        self.canvas.textblock_mode = True
        self._drag((40, 40), (330, 170))  # covers A and B
        self.assertEqual(self._selected_idxs(), [0, 1])

    def test_textblock_mode_left_drag_still_moves_block(self):
        self.canvas.textblock_mode = True
        before = self.a.pos()
        self._drag((100, 100), (300, 250))  # press on A, drag by (200, 150)
        self.assertEqual(self.a.pos() - before, QPointF(200, 150))

    def test_textblock_mode_right_press_still_creates_block(self):
        # The mode's own purpose is untouched: right-press creates a block.
        self.canvas.textblock_mode = True
        self._press((700, 700), button=Qt.MouseButton.RightButton)
        self.assertTrue(self.canvas.creating_textblock)
        self.canvas.creating_textblock = False  # leave the test canvas clean

    # ── quick-menu canvas safety net ────────────────────────

    def test_canvas_press_closes_open_pie_menu(self):
        # A press reaching the canvas proves it landed outside the pie-menu
        # window — the menu must close no matter the geometry bookkeeping.
        from ui.pie_menu import PieMenu

        menu = PieMenu(canvas=None, mw=None, parent=None)
        menu._state = "pin"  # open without showing (offscreen show crashes)
        self.gv.pie_menu = menu  # gv.window() == gv in the standalone test
        self._press((400, 700))  # any press on the canvas
        self.assertEqual(menu._state, "hidden")

    def test_canvas_press_on_block_also_closes_pie_menu(self):
        from ui.pie_menu import PieMenu

        menu = PieMenu(canvas=None, mw=None, parent=None)
        menu._state = "holding"
        self.gv.pie_menu = menu
        self._press((100, 100))  # press on block A
        self.assertEqual(menu._state, "hidden")

    def test_click_selects_single_block_without_override(self):
        self.b.setSelected(True)
        # plain click (no real drag) on A
        self._press((100, 100))
        self._release((100, 100))
        self.assertEqual(self._selected_idxs(), [0])
        self.assertEqual(self.canvas.txtblkShapeControl.calls, [])  # untouched

    def test_click_empty_clears_selection(self):
        self.a.setSelected(True)
        self._press((10, 450))  # empty area
        self._release((10, 450))
        self.assertEqual(self._selected_idxs(), [])

    def test_ctrl_drag_adds_to_selection(self):
        self.a.setSelected(True)
        self.b.setSelected(True)
        self._drag(
            (400, 300), (530, 430),  # covers C
            modifiers=Qt.KeyboardModifier.ControlModifier,
        )
        self.assertEqual(self._selected_idxs(), [0, 1, 2])

    def test_empty_drag_replaces_selection(self):
        self.a.setSelected(True)
        self._drag((10, 460), (120, 560))  # empty rectangle
        self.assertEqual(self._selected_idxs(), [])
        # shape control unbound after the replaced (empty) selection
        self.assertEqual(self.canvas.txtblkShapeControl.blk_item, None)

    def test_single_after_box_rebinds_shape_control(self):
        self._drag((40, 40), (160, 160))  # covers only A
        self.assertEqual(self.canvas.txtblkShapeControl.blk_item, self.a)

    def test_multi_box_unbinds_shape_control(self):
        self._drag((40, 40), (330, 170))  # covers A and B
        self.assertEqual(self.canvas.txtblkShapeControl.blk_item, None)

    def test_subclick_threshold_keeps_native_selection(self):
        self.a.setSelected(True)
        # sub-threshold wiggle on a block: still a click — no box override
        self._press((100, 100))
        self._move((101, 101))
        self._release((101, 101))
        self.assertEqual(self._selected_idxs(), [0])
        self.assertFalse(self.canvas.rubber_band_dragged)

    def test_press_on_editing_block_does_not_start_rubber_band(self):
        self.a.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction
        )
        self._press((100, 100))
        self._release((100, 100))
        self.assertFalse(self.canvas.rubber_band.isVisible())
        self.assertFalse(self.canvas.rubber_band_dragged)

    # ── right button stays the context-menu gesture ────────

    def test_right_drag_selects_nothing_and_emits_menu_request(self):
        self.a.setSelected(True)
        # Replace the real context-menu handler (it would open a modal QMenu
        # and hang offscreen) with a recorder.
        self.canvas.context_menu_requested.disconnect()
        got = []
        self.canvas.context_menu_requested.connect(
            lambda pos, is_p: got.append((pos, is_p))
        )
        self._drag(
            (300, 60), (500, 60),
            button=Qt.MouseButton.RightButton,
        )
        self.assertEqual(self._selected_idxs(), [0])  # selection untouched
        self.assertTrue(got, "context menu request was emitted")
        self.assertFalse(self.canvas.rubber_band.isVisible())


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestBoxSelect)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)