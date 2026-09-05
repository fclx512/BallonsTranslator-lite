"""Offscreen regression tests for clicking a dirty (italic) page in the page
list.

Background (2026-08-26): after a batch style change, non-current pages are
marked dirty (``page_needs_rerender``) and rendered in italic in the page list
(text mode, >= 100 pages).  ``ui.mainwindow.py::pageListCurrentItemChanged``
*rebuilt the whole list synchronously inside its own handler* by calling
``self.updatePageList()`` when the clicked page was dirty.  That rebuild's
``clear``/``addItem``/``setCurrentItem`` re-fired ``currentItemChanged`` while
the handler was still on the stack, re-entering it and re-running the full page
switch (save / switch / redraw / re-render) — landing the user on the wrong
page and rendering the result image twice.

The fix wraps ONLY that dirty-branch ``updatePageList()`` call in
``pageList.blockSignals(True)/...`` so the rebuild cannot re-enter the handler.
The first-screen render path is preserved because it flows through the
top-level ``updatePageList()`` call in ``openDir``, which is never signal-blocked.

These tests bind the REAL ``pageListCurrentItemChanged`` / ``updatePageList``
handlers onto a lightweight shim and a REAL ``PageListView`` (so the
``currentItemChanged`` re-entry actually fires) with a SimpleNamespace-style
fake project.  ``save_on_page_changed`` is disabled so the test never touches a
real save path.  These tests FAIL on the unfixed code (the re-entry runs the
switch twice) and PASS after the fix.

Run:
    ./ballontrans_pylibs_win/python.exe tests/test_page_list_dirty_click.py
"""

import os
import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QMainWindow

from ui.mainwindow import MainWindow, PageListView
from utils.config import pcfg


class _FakeProject:
    """Stands in for ``imgtrans_proj``: a list of page names + a dirty set."""

    def __init__(self, pages, dirty):
        self.pages = list(pages)
        self.current_img = self.pages[0]
        self._dirty = set(dirty)
        self.directory = ""  # text-mode path never reads it

    def page_needs_rerender(self, name):
        return name in self._dirty

    def clear_page_needs_rerender(self, name):
        self._dirty.discard(name)

    def set_current_img(self, name):
        self.current_img = name

    def get_notext_path(self, name):
        return None


class _FakeCanvas:
    def __init__(self):
        self._fit_to_window = False
        self.update_calls = 0

    def clear_undostack(self, update_saved_step=False):
        pass

    # 阶段 4 跨页历史：切页路径改为会话落账 + 仅清绘制栈
    def commit_edit_sessions(self):
        pass

    def prepare_page_switch(self):
        pass

    def updateCanvas(self):
        self.update_calls += 1


class _FakeHandlerSetter:
    """Counting stand-in for module_manager / drawingPanel."""

    def __init__(self):
        self.calls = 0

    def handle_page_changed(self):
        self.calls += 1


class _FakeTitleBar:
    def setTitleContent(self, page_name=None):
        pass


class _PageShim(QMainWindow):
    """QMainWindow binding the real page-list handlers (QObject gives ``tr``)."""


class TestPageListDirtyClick(unittest.TestCase):
    PAGES = [f"p{i}" for i in range(100)]  # text mode (> = 100, no thumbnails)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )

    def setUp(self):
        self.app.processEvents()
        self.shim = _PageShim()
        self.shim.pageList = PageListView()

        self.proj = _FakeProject(self.PAGES, dirty={self.PAGES[5]})
        self.shim.imgtrans_proj = self.proj
        self.shim.canvas = _FakeCanvas()
        self.shim.module_manager = _FakeHandlerSetter()
        self.shim.drawingPanel = _FakeHandlerSetter()
        self.shim.titleBar = _FakeTitleBar()
        self.shim.st_manager = SimpleNamespace(
            formatpanel=SimpleNamespace(
                resolve_text_transform_edits_for_page_change=lambda: None
            ),
            updateSceneTextitems=lambda: None,
        )
        self.shim.save_on_page_changed = False
        self.shim.opening_dir = False
        self.shim.page_changing = False
        self.save_calls = {"n": 0}

        def _save_result():
            self.save_calls["n"] += 1

        self.shim._save_result_image_only = _save_result

        # Bind the REAL handlers, then hook the REAL signal like MainWindow does.
        self.shim.updatePageList = MethodType(MainWindow.updatePageList, self.shim)
        self.shim.pageListCurrentItemChanged = MethodType(
            MainWindow.pageListCurrentItemChanged, self.shim
        )
        self.shim.pageList.currentItemChanged.connect(
            self.shim.pageListCurrentItemChanged
        )

        # Populate the list (italic p5), then reset counters: the setup pass may
        # fire the handler for the clean current page and we only care about the
        # click that follows.
        self.shim.updatePageList()
        self._reset_counters()

    def tearDown(self):
        self.shim.pageList.clear()
        self.app.processEvents()

    # ── helpers ────────────────────────────────────────────────

    def _reset_counters(self):
        self.shim.canvas.update_calls = 0
        self.shim.module_manager.calls = 0
        self.shim.drawingPanel.calls = 0
        self.save_calls["n"] = 0

    def _item_text(self, index):
        return self.shim.pageList.item(index).text()

    def _is_italic(self, index):
        return self.shim.pageList.item(index).font().italic()

    def _click(self, name):
        index = self.PAGES.index(name)
        self.shim.pageList.setCurrentItem(self.shim.pageList.item(index))

    # ── core: dirty-page click must not re-enter the handler ────

    def test_dirty_click_switches_exactly_once(self):
        clicked = self.PAGES[5]
        self.assertTrue(self.proj.page_needs_rerender(clicked))  # setup sanity

        self._click(clicked)

        # The page switch logic (the redraw inside the handler) runs ONCE.
        self.assertEqual(self.shim.canvas.update_calls, 1)
        self.assertEqual(self.shim.module_manager.calls, 1)
        self.assertEqual(self.shim.drawingPanel.calls, 1)
        # The dirty-branch result render runs once, not twice.
        self.assertEqual(self.save_calls["n"], 1)

    def test_dirty_click_lands_on_clicked_page(self):
        clicked = self.PAGES[5]

        self._click(clicked)

        self.assertEqual(
            self.shim.pageList.currentItem().text(), clicked
        )
        self.assertEqual(self.proj.current_img, clicked)

    def test_dirty_click_clears_dirty_marker(self):
        clicked = self.PAGES[5]
        self.assertTrue(self.proj.page_needs_rerender(clicked))

        self._click(clicked)

        self.assertFalse(self.proj.page_needs_rerender(clicked))

    def test_dirty_click_rebuilds_list_without_italic(self):
        clicked = self.PAGES[5]
        # Before the click, the dirty page shows an italic font.
        self.assertTrue(self._is_italic(self.PAGES.index(clicked)))

        self._click(clicked)

        # The list was rebuilt: same count, clicked page no longer italic.
        self.assertEqual(self.shim.pageList.count(), len(self.PAGES))
        self.assertFalse(self._is_italic(self.PAGES.index(clicked)))

    # ── guard: a clean-page click must NOT rebuild / over-block ──

    def test_clean_click_no_rebuild_no_dirty_render(self):
        # Click a NON-current clean page (p1): the current page after setup is
        # p0, so clicking p1 actually changes the current item and fires the
        # handler.  p0 is dirty-free AND already current — clicking it would be
        # a no-op on the signal.
        clean = self.PAGES[1]
        self.assertFalse(self.proj.page_needs_rerender(clean))

        self._click(clean)

        # A clean page still switches once, but the dirty result-render branch
        # is untouched and the list is NOT rebuilt (count unchanged).
        self.assertEqual(self.shim.canvas.update_calls, 1)
        self.assertEqual(self.save_calls["n"], 0)
        self.assertEqual(self.shim.pageList.count(), len(self.PAGES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
