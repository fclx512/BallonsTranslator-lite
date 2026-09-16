"""查找替换的批量回滚：与工作台批量任务共用同一套版本仓库。

2026-09-16 归并——原先查找替换走 ``utils/proj_imgtrans.py::ProjImgTrans``
的单槽会话快照（``*.batch_backup.json``，会话内有效、单槽覆盖），工作台
批量任务走 ``utils/batch_versions.py::BatchVersionStore``（多版本轮转 + 像素
前图）。两者本质都是批量修改，故统一到版本仓库：替换前写一版、回滚条按
版本号撤销、被更晚的批量操作顶掉即不可用、空替换丢弃刚写的版本。

本文件只钉**接线语义**（谁在什么时机写版本、回滚条怎么判可用、空替换
怎么收尾）；版本仓库自身的机制回归在 ``tests/test_batch_versions.py``。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_replace_rollback.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.batch_versions import BatchVersionStore  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

REPLACE_LABEL = "Global replace"


def _make_blk(translation):
    blk = TextBlock(xyxy=[100, 100, 300, 200], translation=translation)
    blk._bounding_rect = [100, 100, 300, 200]
    return blk


class _ReplaceFixture(unittest.TestCase):
    """真 ProjImgTrans（临时项目目录）+ 真查找替换面板（offscreen）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 2

        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        for name in ("a.png", "b.png"):
            cv2.imwrite(osp.join(self.proj_dir, name), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages["a.png"] = [_make_blk("old")]
        self.proj.pages["b.png"] = []
        self.proj.save()

        from ui.global_search_widget import GlobalSearchWidget

        self.w = GlobalSearchWidget()
        self.w.imgtrans_proj = self.proj
        self.store = BatchVersionStore(self.proj)
        self.rolled_back = []
        self.w.batch_rollback_requested.connect(self.rolled_back.append)

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self.w.deleteLater()
        self._tmp.cleanup()

    # ── 辅助 ────────────────────────────────────────────────────

    def _write_version(self):
        meta = self.store.begin(REPLACE_LABEL, dirty_pages=["a.png"])
        self.w.set_replace_version(meta.seq if meta is not None else None)
        return meta

    def _show_strip(self):
        self.w._show_rollback_strip(
            {"src": [], "trans": []}, {"src": [], "trans": []}, []
        )

    def _arm_replace(self, collect):
        """把 on_replace 跑通所需的旁路依赖换成 stub，交给 collect 收尾。"""
        self.w.counter_sum = 1
        self.w._confirm_replace = lambda *a, **k: True
        self.w._refresh_style_combo = lambda *a, **k: None
        self.w._collect_replace_targets = collect


class ReplaceRollbackStripTest(_ReplaceFixture):
    # ── 回滚条与版本号 ──────────────────────────────────────────

    def test_strip_hidden_when_version_write_failed(self):
        """版本没写成时宁可不显示回滚条，也不显示点了没用的按钮。"""
        self.w.set_replace_version(None)
        self._show_strip()
        self.assertFalse(self.w.rollback_strip.isVisibleTo(self.w))
        self.assertIsNone(self.w.active_replace_version())

    def test_strip_tracks_latest_replace_version(self):
        meta = self._write_version()
        self._show_strip()
        self.assertTrue(self.w.rollback_strip.isVisibleTo(self.w))
        self.assertEqual(self.w.active_replace_version(), meta.seq)

    def test_newer_batch_operation_invalidates_strip(self):
        """更晚的批量操作（如工作台任务）写新版后，旧回滚条即失效。"""
        self._write_version()
        self._show_strip()
        self.store.begin("简单背景修复")  # 模拟工作台任务

        self.assertIsNone(self.w.active_replace_version())
        # 点了也不发起回滚（主窗口侧不会收到请求）
        self.w._confirm_replace = lambda *a, **k: True
        self.w._on_rollback_clicked()
        self.assertEqual(self.rolled_back, [])
        self.assertFalse(self.w.rollback_strip.isVisibleTo(self.w))

    def test_rollback_click_emits_its_own_version_seq(self):
        meta = self._write_version()
        self._show_strip()
        self.w._confirm_replace = lambda *a, **k: True
        self.w._on_rollback_clicked()
        self.assertEqual(self.rolled_back, [meta.seq])

    def test_rollback_click_aborts_when_not_confirmed(self):
        self._write_version()
        self._show_strip()
        self.w._confirm_replace = lambda *a, **k: False
        self.w._on_rollback_clicked()
        self.assertEqual(self.rolled_back, [])

    def test_replace_version_rolls_the_data_back(self):
        """端到端：替换前写的版本能把替换后的数据整体换回。"""
        meta = self._write_version()
        self.proj.pages["a.png"][0].translation = "replaced"
        self.proj.pages["b.png"].append(_make_blk("added"))

        self.store.restore_latest(expect_seq=meta.seq)

        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertEqual(self.proj.pages["b.png"], [])
        self.assertEqual(self.store.available_steps(), 0)


class NoopReplaceTest(_ReplaceFixture):
    # ── 空替换：丢弃刚写的版本 ──────────────────────────────────

    def test_preparing_writes_version_before_collecting(self):
        """顺序契约：版本必须在收集器原地改写非当前页数据之前写好。"""
        seen = {}

        def prepare():
            self._write_version()

        def collect(target):
            seen["version_exists"] = self.store.latest() is not None
            return {"src": [], "trans": []}, {"src": [], "trans": []}, []

        self.w.replace_preparing.connect(prepare)
        self._arm_replace(collect)
        self.w.on_replace()

        self.assertTrue(seen.get("version_exists"))

    def test_noop_replace_discards_version_and_strip(self):
        meta = self._write_version()
        self._show_strip()

        self._arm_replace(
            lambda target: ({"src": [], "trans": []},
                            {"src": [], "trans": []}, [])
        )
        self.w.on_replace()

        self.assertIsNone(self.store.latest())
        self.assertFalse(osp.exists(meta.dir))
        self.assertIsNone(self.w._replace_version_seq)
        self.assertFalse(self.w.rollback_strip.isVisibleTo(self.w))

    def test_hide_rollback_strip_forgets_the_version(self):
        """回滚完成后收起回滚条＝本面板不再提供回滚入口（见底提示随之失效）。"""
        self._write_version()
        self._show_strip()
        self.w.hide_rollback_strip()
        self.assertIsNone(self.w.active_replace_version())
        self.assertIsNone(self.w._replace_version_seq)

    def test_noop_replace_keeps_data_untouched(self):
        self._write_version()
        self._arm_replace(
            lambda target: ({"src": [], "trans": []},
                            {"src": [], "trans": []}, [])
        )
        self.w.on_replace()
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")


class MainWindowRollbackGuardTest(_ReplaceFixture):
    """主窗口侧的第二道闸：过期版本号一律拒绝，不换数据。"""

    def _shim(self):
        from ui.mainwindow import MainWindow

        shim = SimpleNamespace(
            imgtrans_proj=self.proj,
            canvas=SimpleNamespace(
                clear_undostack=lambda **kw: None, updateCanvas=lambda: None
            ),
            st_manager=SimpleNamespace(updateSceneTextitems=lambda: None),
            global_search_widget=self.w,
            _sync_and_commit_project=lambda **kw: None,
            updatePageList=lambda: None,
            _ask_rerender_dirty_pages=lambda: None,
        )
        shim.on_batch_rollback = MainWindow.on_batch_rollback.__get__(shim)
        return shim

    def test_matching_version_rolls_back(self):
        meta = self._write_version()
        self._show_strip()
        self.proj.pages["a.png"][0].translation = "replaced"

        self._shim().on_batch_rollback(meta.seq)

        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertEqual(self.store.available_steps(), 0)
        # 回滚收尾会收起回滚条并忘掉那一版
        self.assertIsNone(self.w._replace_version_seq)

    def test_stale_version_refused(self):
        self._write_version()
        self._show_strip()
        self.store.begin("简单背景修复")  # 更晚的批量操作顶掉替换那一版
        self.proj.pages["a.png"][0].translation = "later"

        self._shim().on_batch_rollback(1)

        self.assertEqual(self.proj.pages["a.png"][0].translation, "later")
        self.assertIsNone(self.w._replace_version_seq)

    def test_preparing_writes_version_and_arms_the_strip(self):
        """替换前写版本＝归并后的核心落点：写一版 + 把版本号交给面板。"""
        from ui.mainwindow import MainWindow

        self.proj.set_current_img("a.png")
        shim = SimpleNamespace(
            imgtrans_proj=self.proj,
            _sync_and_commit_project=lambda **kw: None,
            global_search_widget=self.w,
            tr=lambda s, *a, **k: s,
        )
        shim.on_global_replace_preparing = (
            MainWindow.on_global_replace_preparing.__get__(shim)
        )
        shim.on_global_replace_preparing()

        meta = self.store.latest()
        self.assertIsNotNone(meta)
        self.assertEqual(meta.label, REPLACE_LABEL)
        self.assertEqual(self.w._replace_version_seq, meta.seq)
        # 替换会改文本/样式 → 当前页进脏页清单（回滚后据此重渲）
        self.assertIn("a.png", meta.dirty_pages)


if __name__ == "__main__":
    unittest.main()
