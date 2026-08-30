"""批量操作快照（单槽）write / restore / clear 回归。

阶段 2.5（查找替换重构 §4.5）：批量替换的撤销不走撤销栈，而是操作前
把整个项目 JSON 快照到单槽备份文件（``utils/proj_imgtrans.py::
write_batch_backup``），一键整体回滚（``utils/proj_imgtrans.py::
restore_batch_backup``）。快照带脏页清单，回滚时整体重标脏；用户当前
所在页不跳转；快照跨会话残留不视为可用。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_batch_backup.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from utils.exceptions import ProjectLoadFailureException  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


def _make_blk(translation):
    blk = TextBlock(xyxy=[100, 100, 300, 200], translation=translation)
    blk._bounding_rect = [100, 100, 300, 200]
    return blk


class BatchBackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        # 项目目录内需有真实图片，new_project 才能发现页面
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        for name in ("a.png", "b.png"):
            cv2.imwrite(osp.join(self.proj_dir, name), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages["a.png"] = [_make_blk("old")]
        self.proj.pages["b.png"] = []
        self.proj.save()

    def tearDown(self):
        self._tmp.cleanup()

    def _backup_path(self):
        return self.proj.batch_backup_path

    def test_write_snapshot_captures_pre_replace_state(self):
        self.assertTrue(self.proj.write_batch_backup(["a.png"]))
        self.assertTrue(self.proj.has_batch_backup())
        self.assertTrue(osp.exists(self._backup_path()))

        # 快照之后继续改数据
        self.proj.pages["a.png"][0].translation = "newer"
        self.proj.pages["b.png"].append(_make_blk("added"))

        dirty = self.proj.restore_batch_backup()
        self.assertEqual(dirty, ["a.png"])
        self.assertEqual(self.proj.pages["a.png"][0].translation, "old")
        self.assertEqual(self.proj.pages["b.png"], [])

    def test_restore_keeps_current_page_and_consumes_slot(self):
        self.proj.write_batch_backup()
        self.proj.set_current_img("b.png")

        self.proj.restore_batch_backup()
        self.assertEqual(self.proj.current_img, "b.png")
        # 快照消费后单槽清空
        self.assertFalse(self.proj.has_batch_backup())
        self.assertFalse(osp.exists(self._backup_path()))

    def test_clear_backup(self):
        self.proj.write_batch_backup()
        self.proj.clear_batch_backup()
        self.assertFalse(self.proj.has_batch_backup())
        self.assertFalse(osp.exists(self._backup_path()))

    def test_restore_without_backup_raises(self):
        with self.assertRaises(ProjectLoadFailureException):
            self.proj.restore_batch_backup()

    def test_corrupt_backup_raises_and_keeps_file(self):
        self.proj.write_batch_backup()
        with open(self._backup_path(), "w", encoding="utf8") as f:
            f.write("{corrupted")
        with self.assertRaises(ProjectLoadFailureException):
            self.proj.restore_batch_backup()
        self.assertTrue(osp.exists(self._backup_path()))

    def test_new_project_load_invalidates_stale_backup(self):
        """跨会话残留的备份文件对新会话不可用（重新 load 后失效）。"""
        self.proj.write_batch_backup()
        # 模拟重开项目：重新 load 同一目录
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertTrue(osp.exists(fresh.batch_backup_path))
        self.assertFalse(fresh.has_batch_backup())

    def test_roundtrip_preserves_base_styles(self):
        from utils.base_styles import BaseStyle
        from utils.fontformat import FontFormat

        self.proj.base_styles = [
            BaseStyle("TestFont", FontFormat(font_family="TestFont"))
        ]
        self.proj.save()
        self.proj.write_batch_backup()
        self.proj.base_styles = []
        self.proj.restore_batch_backup()
        self.assertEqual(len(self.proj.base_styles), 1)
        self.assertEqual(self.proj.base_styles[0].fontformat.font_family,
                         "TestFont")


if __name__ == "__main__":
    unittest.main()
