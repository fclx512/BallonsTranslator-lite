"""Offscreen tests for the node 2d pipeline linkage.

``apply_auto_tate_chu_yoko`` finalizes translated blocks (persisting
tate-chu-yoko into ``block.rich_text``) inside ``on_pagtrans_finished`` and
the manual apply path; ``AutoTateChuYokoThread`` runs that pass over whole
project documents off the UI thread.

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_pipeline_tcy.py
"""

import os
import os.path as osp
import sys
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.config import AutoTateChuYokoConfig  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

from ui.text_engine.pipeline_formatting import (  # noqa: E402
    AutoTateChuYokoThread,
    apply_auto_tate_chu_yoko,
)


def _vertical_blk(translation):
    blk = TextBlock(translation=translation)
    blk.vertical = True
    return blk


class ApplyAutoTateChuYokoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_vertical_digits_get_tcy(self):
        blk = _vertical_blk("セリフ12語")
        settings = AutoTateChuYokoConfig(enabled=True, max_length=2)
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 1)
        self.assertIn("text-combine-upright", blk.rich_text)
        self.assertIn("data-btrans-text-combine-id", blk.rich_text)

    def test_horizontal_unchanged(self):
        blk = TextBlock(translation="abc12")
        blk.vertical = False
        settings = AutoTateChuYokoConfig(enabled=True, max_length=2)
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 0)
        self.assertEqual(blk.rich_text, "")

    def test_disabled_unchanged(self):
        blk = _vertical_blk("12")
        settings = AutoTateChuYokoConfig(enabled=False)
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 0)
        self.assertEqual(blk.rich_text, "")

    def test_max_length_threshold(self):
        settings = AutoTateChuYokoConfig(enabled=True, max_length=2)
        long_blk = _vertical_blk("abc123")
        self.assertEqual(apply_auto_tate_chu_yoko([long_blk], settings), 0)
        self.assertEqual(long_blk.rich_text, "")
        short_blk = _vertical_blk("abc12")
        self.assertEqual(apply_auto_tate_chu_yoko([short_blk], settings), 1)

    def test_no_allowed_characters_unchanged(self):
        blk = _vertical_blk("セリフ語")
        settings = AutoTateChuYokoConfig(enabled=True, max_length=4)
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 0)

    def test_letters_include(self):
        settings = AutoTateChuYokoConfig(
            enabled=True,
            max_length=4,
            include_numbers=False,
            include_letters=True,
        )
        blk = _vertical_blk("セリフabc語")
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 1)
        self.assertIn("text-combine-upright", blk.rich_text)

    def test_existing_rich_text_preserved(self):
        blk = _vertical_blk("A12B")
        blk.rich_text = (
            '<p style="font-weight:700;">'
            'A<span style="text-combine-upright: all" '
            'data-btrans-text-combine-id="1">12</span>B</p>'
        )
        settings = AutoTateChuYokoConfig(enabled=True, max_length=2)
        self.assertEqual(apply_auto_tate_chu_yoko([blk], settings), 1)
        self.assertIn("font-weight:700", blk.rich_text)
        self.assertIn("text-combine-upright", blk.rich_text)


class AutoTateChuYokoThreadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_thread_applies_over_pages(self):
        page_blocks = [
            _vertical_blk("セリフ12語"),
            TextBlock(translation="horizontal12"),
        ]
        settings = AutoTateChuYokoConfig(enabled=True, max_length=2)
        thread = AutoTateChuYokoThread()
        results = {}

        def on_finished(changed_count, changed_blocks):
            results["count"] = changed_count
            results["blocks"] = changed_blocks

        thread.processing_finished.connect(on_finished)
        self.assertTrue(
            thread.start_processing({"page1": page_blocks}, settings)
        )
        self.assertTrue(thread.wait(10000))
        # processing_finished is queued across threads; flush the event queue.
        self.app.processEvents()
        self.assertEqual(results["count"], 1)
        self.assertIs(results["blocks"][0], page_blocks[0])
        self.assertIn("text-combine-upright", page_blocks[0].rich_text)
        self.assertEqual(page_blocks[1].rich_text, "")


class ProgressBoxFittedTest(unittest.TestCase):
    """ProgressMessageBox.show_fitted (node 2d) must not crash offscreen."""

    @classmethod
    def setUpClass(cls):
        from ui.custom_widget import ProgressMessageBox

        cls.app = QApplication.instance() or QApplication([])
        cls.ProgressMessageBox = ProgressMessageBox

    def test_show_fitted(self):
        box = self.ProgressMessageBox("task", False)
        box.fit_to_content()
        box.show_fitted()
        box.hide()
        box.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
