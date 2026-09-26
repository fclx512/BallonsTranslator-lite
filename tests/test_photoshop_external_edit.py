"""Photoshop 外部编辑链的**纯逻辑**回归（离屏；不启动真实 Photoshop）。

边界（实施计划阶段三第 5 条）：Photoshop 不进项目依赖、不随包、不进
requirements——项目只提供"导出当前修复图为 PNG → 外部启动（detached，不等待）
→ 回读校验"这条链；真机启动 PS 留 Windows 人工验收。

本文件只用替身钉住导出/回读契约，绝不拉起真实进程：

- **回读**：RGBA 归一成 RGB（PS 存 PNG 常带 alpha）、尺寸变化拒绝并提示、
  文件不存在提示；写回走修复撤销栈（一次撤销回到原图）并落盘到项目；
- **导出**：按 ``<页名>.png`` 写到 ``inpainted/``，用配置里的 PS 路径
  detached 启动；路径没配/不存在时**既不该导出也不该启动**，只提示去哪儿设。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_photoshop_external_edit.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from utils.config import load_config, pcfg  # noqa: E402

H, W = 20, 30
PAGE = "p001.jpg"  # 非 png：导出名要按"去掉扩展名 + .png"推出来
EDITED_RGB = (10, 20, 30)  # 写进 PNG 的 RGB（回读后按 BGR 读出）


class _FakeProj:
    """只实现 ``ui/drawingpanel.py`` 的 PS 链用到的那几个成员。"""

    img_valid = True
    inpainted_valid = True
    mask_valid = False
    notext_array = None

    def __init__(self, directory):
        self.directory = directory
        self.current_img = PAGE
        self.img_array = np.zeros((H, W, 3), np.uint8)
        self.inpainted_array = np.zeros((H, W, 3), np.uint8)
        self.mask_array = np.zeros((H, W), np.uint8)
        self.saved = {}

    def inpainted_dir(self):
        return self.directory

    def save_inpainted(self, name, array):
        self.saved[name] = array.copy()

    def page_generation(self, name):
        return 0

    def page_image_generation(self, name):
        return 0


class PhotoshopExternalEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_config()

    def setUp(self):
        # 每个用例一套自己的画布/面板：撤销栈要干净，不能串到下一个用例
        from ui.canvas import Canvas
        from ui.drawingpanel import DrawingPanel
        from ui.module_parse_widgets import InpaintConfigPanel

        self._ps_path = pcfg.drawpanel.photoshop_path
        self.addCleanup(
            lambda: setattr(pcfg.drawpanel, "photoshop_path", self._ps_path)
        )
        pcfg.drawpanel.photoshop_path = ""

        self.canvas = Canvas()
        # InpaintConfigPanel 只是个上下文对象，画布工具面板的引擎下拉来源
        self.panel = DrawingPanel(self.canvas, InpaintConfigPanel(""))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = _FakeProj(self.tmp.name)
        self.canvas.imgtrans_proj = self.proj

        import ui.drawingpanel as drawingpanel

        self.drawingpanel = drawingpanel
        self.info_dialogs = []
        patcher = mock.patch.object(
            drawingpanel,
            "create_info_dialog",
            side_effect=lambda *args, **kwargs: self.info_dialogs.append(args),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 工具 ────────────────────────────────────────────────────────

    def _png_path(self) -> str:
        return osp.join(self.tmp.name, osp.splitext(PAGE)[0] + ".png")

    def _write_edited(self, shape=(H, W, 4), rgb=EDITED_RGB, path=None):
        path = path or self._png_path()
        img = np.zeros(shape, np.uint8)
        img[..., 0], img[..., 1], img[..., 2] = rgb
        if shape[-1] == 4:
            img[..., 3] = 0  # PS 常见的"alpha 全 0"PNG
        cv2.imwrite(path, img)
        return path

    def _dialog_text(self) -> str:
        """所有提示框文本拼起来（谁弹的、弹了几句都能查）。"""
        return " | ".join(str(arg) for call in self.info_dialogs for arg in call)

    # ── 回读：RGBA / 尺寸 / 缺失 / 撤销 ──────────────────────────────

    def test_refresh_normalizes_rgba_and_pushes_undo(self):
        self._write_edited()
        self.panel.on_refresh_from_photoshop()

        self.assertEqual(self.info_dialogs, [], self._dialog_text())
        # RGBA 丢掉第 4 通道、按 RGB 写回（读出来是 BGR）
        self.assertEqual(self.proj.inpainted_array.shape, (H, W, 3))
        self.assertEqual(
            self.proj.inpainted_array[0, 0].tolist(), list(reversed(EDITED_RGB))
        )
        # 落盘到项目（当前页名）
        self.assertIn(PAGE, self.proj.saved)
        np.testing.assert_array_equal(self.proj.saved[PAGE], self.proj.inpainted_array)
        # 写回是一步可撤销的图像命令
        self.assertEqual(self.canvas.text_undo_stack.count(), 1)
        self.canvas.undo()
        np.testing.assert_array_equal(self.proj.inpainted_array, 0)

    def test_refresh_rejects_changed_dimensions(self):
        self._write_edited(shape=(H + 5, W, 3))
        self.panel.on_refresh_from_photoshop()

        self.assertIn("dimensions", self._dialog_text())
        self.assertEqual(self.canvas.text_undo_stack.count(), 0)
        self.assertEqual(self.proj.saved, {})
        np.testing.assert_array_equal(self.proj.inpainted_array, 0)

    def test_refresh_reports_missing_edited_file(self):
        self.panel.on_refresh_from_photoshop()

        self.assertNotEqual(self.info_dialogs, [])
        self.assertEqual(self.canvas.text_undo_stack.count(), 0)
        self.assertEqual(self.proj.saved, {})

    # ── 导出 + 外部启动 ─────────────────────────────────────────────

    def test_edit_exports_png_and_launches_detached(self):
        ps_exe = osp.join(self.tmp.name, "Photoshop.exe")
        open(ps_exe, "wb").close()
        pcfg.drawpanel.photoshop_path = ps_exe
        self.proj.inpainted_array[:] = 77

        calls = []

        class _FakeQProcess:
            @staticmethod
            def startDetached(program, args):
                calls.append((program, list(args)))
                return True

        with mock.patch.object(self.drawingpanel, "QProcess", _FakeQProcess):
            self.panel.on_edit_in_photoshop()

        self.assertEqual(self.info_dialogs, [], self._dialog_text())
        png = self._png_path()
        self.assertTrue(osp.exists(png), "当前修复图必须按 <页名>.png 导出")
        exported = cv2.imread(png)
        self.assertEqual(exported.shape, (H, W, 3))
        self.assertEqual(exported[0, 0].tolist(), [77, 77, 77])
        self.assertEqual(calls, [(ps_exe, [png])])

    def test_edit_without_photoshop_path_writes_and_launches_nothing(self):
        calls = []

        class _FakeQProcess:
            @staticmethod
            def startDetached(program, args):
                calls.append((program, list(args)))
                return True

        with mock.patch.object(
            self.drawingpanel.DrawingPanel,
            "_find_photoshop_path_registry",
            return_value=None,
        ):
            with mock.patch.object(self.drawingpanel, "QProcess", _FakeQProcess):
                self.panel.on_edit_in_photoshop()

        self.assertIn("Photoshop was not found", self._dialog_text())
        # 解析不到 PS 就地退出：不导出、不启动，别留下半个外部编辑现场
        self.assertFalse(osp.exists(self._png_path()))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
