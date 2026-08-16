"""Offscreen regression tests for the screen color picker (eyedropper).

Covers the two failure classes of the earlier eyedropper attempt:
freezing the UI and picking wrong pixels.  The sampling math (including
DPR mapping and clamping), the pick/cancel interaction paths, and the
dialog's copy-format helpers are all verified without a real screen.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_screen_picker.py
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import QEvent, QPointF, QRect, Qt
from qtpy.QtGui import QColor, QImage, QKeyEvent, QMouseEvent

from ui.custom_widget.color_picker import ColorPickerDialog
from ui.custom_widget.screen_picker import _PickerOverlay, _ScreenFrame


def _press(widget, x, y, button=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(ev)


def _make_overlay(w=100, h=100, dpr=1.0, fill=(5, 6, 7), mark=None):
    """Overlay over a single synthetic screen with a known fill color.

    ``mark`` is a (x, y, color) triple placed at one pixel to verify that
    the sampling lands on the exact device pixel.
    """
    img = QImage(w, h, QImage.Format.Format_RGBA8888)
    img.fill(QColor(*fill))
    if mark is not None:
        mx, my, mcolor = mark
        img.setPixelColor(mx, my, QColor(*mcolor))
    ov = _PickerOverlay([_ScreenFrame(QRect(0, 0, w, h), img, dpr)])
    ov.setGeometry(QRect(0, 0, w, h))
    return ov, img


class TestScreenPicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(sys.argv[:1])

    # ── sampling math ─────────────────────────────────────

    def test_probe_dpr1_lands_on_device_pixel(self):
        ov, img = _make_overlay(mark=(50, 50, (250, 251, 252)))
        frame, color, ix, iy = ov._probe(QPointF(50.9, 49.2))
        self.assertEqual((ix, iy), (50, 49))
        self.assertEqual((color.red(), color.green(), color.blue()), (5, 6, 7))
        # move half a pixel to the marked pixel
        _, color, ix, iy = ov._probe(QPointF(50.0, 50.0))
        self.assertEqual((ix, iy), (50, 50))
        self.assertEqual((color.red(), color.green(), color.blue()), (250, 251, 252))

    def test_probe_dpr2_scales_by_device_pixel_ratio(self):
        # geometry 50x50 at dpr=2 → image is 100x100 device px
        img = QImage(100, 100, QImage.Format.Format_RGBA8888)
        img.fill(QColor(5, 6, 7))
        img.setPixelColor(50, 50, QColor(250, 251, 252))
        ov = _PickerOverlay([_ScreenFrame(QRect(0, 0, 50, 50), img, 2.0)])
        ov.setGeometry(QRect(0, 0, 50, 50))
        frame, color, ix, iy = ov._probe(QPointF(25.0, 25.0))
        self.assertEqual((ix, iy), (50, 50))
        self.assertEqual((color.red(), color.green(), color.blue()), (250, 251, 252))

    def test_probe_clamps_and_misses_out_of_bounds(self):
        ov, img = _make_overlay(mark=(99, 99, (250, 251, 252)))
        # just past the edge still clamps into the image
        _, color, ix, iy = ov._probe(QPointF(150.0, 150.0))
        self.assertIsNone(color)
        # inside the frame, coords clamp to the last pixel row/col
        ov.setGeometry(QRect(0, 0, 500, 500))
        _, color, ix, iy = ov._probe(QPointF(99.9, 99.9))
        self.assertEqual((ix, iy), (99, 99))
        self.assertEqual((color.red(), color.green(), color.blue()), (250, 251, 252))

    def test_probe_empty_frames_returns_none(self):
        ov = _PickerOverlay([])
        ov.setGeometry(QRect(0, 0, 100, 100))
        self.assertEqual(ov._probe(QPointF(10, 10)), (None, None, 0, 0))

    # ── interaction paths ──────────────────────────────────

    def test_left_click_picks(self):
        from qtpy.QtWidgets import QDialog

        ov, img = _make_overlay(mark=(40, 40, (250, 251, 252)))
        _press(ov, 40, 40)
        self.assertEqual(ov.result(), QDialog.DialogCode.Accepted)
        c = ov.picked_color()
        self.assertEqual((c.red(), c.green(), c.blue()), (250, 251, 252))

    def test_right_click_cancels(self):
        from qtpy.QtWidgets import QDialog

        ov, _ = _make_overlay()
        _press(ov, 40, 40, button=Qt.MouseButton.RightButton)
        self.assertEqual(ov.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(ov.picked_color())

    def test_escape_cancels(self):
        from qtpy.QtWidgets import QDialog

        ov, _ = _make_overlay()
        ov.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        )
        self.assertEqual(ov.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(ov.picked_color())

    def test_overlay_renders_after_move(self):
        ov, _ = _make_overlay()
        ov.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(60, 40),
                QPointF(60, 40),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        pm = ov.grab()  # exercises paintEvent incl. magnifier + readout
        self.assertFalse(pm.isNull())

    # ── dialog copy helpers ────────────────────────────────

    def test_dialog_copy_hex_and_rgb(self):
        from qtpy.QtWidgets import QApplication

        dlg = ColorPickerDialog(QColor(12, 34, 56))
        self.assertEqual(dlg._copy_hex_btn.text(), "HEX #0C2238")
        self.assertEqual(dlg._copy_rgb_btn.text(), "RGB(12, 34, 56)")
        dlg._copy_color(0)
        self.assertEqual(QApplication.clipboard().text(), "#0C2238")
        dlg._copy_color(1)
        self.assertEqual(QApplication.clipboard().text(), "rgb(12, 34, 56)")

    def test_dialog_copy_strips_alpha(self):
        # The app controls opacity via separate sliders/dropdowns, so the
        # picker always works (and copies) in opaque RGB.
        from qtpy.QtWidgets import QApplication

        dlg = ColorPickerDialog(QColor(12, 34, 56))
        dlg.set_color_direct(QColor(1, 2, 3, 128))
        self.assertEqual(dlg.get_color().alpha(), 255)
        self.assertEqual(dlg._copy_hex_btn.text(), "HEX #010203")
        dlg._copy_color(0)
        self.assertEqual(QApplication.clipboard().text(), "#010203")
        dlg._copy_color(1)
        self.assertEqual(QApplication.clipboard().text(), "rgb(1, 2, 3)")

    def test_dialog_hex_field_selects_all_on_focus(self):
        dlg = ColorPickerDialog(QColor(12, 34, 56))
        dlg.show()
        dlg.hex_edit.setFocus()
        self.app.processEvents()
        self.assertEqual(dlg.hex_edit.selectedText(), "0C2238")
        dlg.close()


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestScreenPicker)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
