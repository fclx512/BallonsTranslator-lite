"""说话人调板（调板角色标注 → 翻译注入 speaker hints）回归。

覆盖方案评估（2026-10-03 拍板 v2）钉的会动契约：
- 旧裸三元组宽进（载入即补占位名）与占位名删除不复用（fg_palette_seq 持久化）；
- 落盘口径：role 空不落键、空调板不落键；
- 注入三态：有角色 / 无角色 / 多色一角色，及 build_user_task_message 小节组装。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_fg_palette_roles.py -q
"""

import os
import os.path as osp
import sys
import tempfile
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from qtpy.QtCore import QPointF, QSizeF  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from modules.translators.agent.prompts import (  # noqa: E402
    build_user_task_message,
)
from ui.canvas import page_pixel_from_scene  # noqa: E402
from utils.proj_imgtrans import (  # noqa: E402
    ProjImgTrans,
    is_placeholder_role,
    palette_role_to_seq,
    palette_seq_to_name,
    speaker_display_name,
    speaker_hints_for_blocks,
)
from utils.textblock import TextBlock  # noqa: E402


class PaletteNamingTest(unittest.TestCase):
    def test_seq_name_excel_columns(self):
        self.assertEqual(palette_seq_to_name(1), "A")
        self.assertEqual(palette_seq_to_name(26), "Z")
        self.assertEqual(palette_seq_to_name(27), "AA")
        self.assertEqual(palette_seq_to_name(28), "AB")
        self.assertEqual(palette_seq_to_name(703), "AAA")

    def test_roundtrip_and_placeholder_shape(self):
        for seq in (1, 26, 27, 703):
            self.assertEqual(palette_role_to_seq(palette_seq_to_name(seq)), seq)
        self.assertTrue(is_placeholder_role("A"))
        self.assertTrue(is_placeholder_role("AB"))
        self.assertFalse(is_placeholder_role("Alice"))
        self.assertFalse(is_placeholder_role("a"))
        self.assertFalse(is_placeholder_role(""))

    def test_display_name(self):
        self.assertEqual(speaker_display_name("A"), "Speaker A")
        self.assertEqual(speaker_display_name("Alice"), "Alice")


class PaletteRoundTripTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        cv2.imwrite(osp.join(self.proj_dir, "a.png"), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self, **extra):
        self.proj.load_from_dict({"pages": {"a.png": []}, **extra})

    def test_legacy_bare_triples_get_placeholders(self):
        # 旧工程宽进：裸三元组载入即固化占位名；已是 dict 的条目不动
        self._load(
            fg_palette=[[27, 25, 50], {"rgb": [1, 2, 3], "role": "Alice"}]
        )
        self.assertEqual(self.proj.fg_palette[0]["role"], "A")
        self.assertEqual(self.proj.fg_palette[1]["role"], "Alice")
        self.assertEqual(self.proj.fg_palette_seq, 1)
        # 迁移后接着取样，占位名从 B 续
        self.assertEqual(self.proj.next_palette_role(), "B")

    def test_deleted_names_are_not_reused(self):
        self._load(fg_palette=[[1, 2, 3], [4, 5, 6]])
        self.assertEqual(
            [e["role"] for e in self.proj.fg_palette], ["A", "B"]
        )
        self.proj.fg_palette.pop(1)  # 删掉「B」
        self.assertEqual(self.proj.next_palette_role(), "C")
        # 删到 0 也不重头分配：序号随项目持久化
        self.proj.fg_palette.clear()
        self.proj.save()
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertEqual(fresh.next_palette_role(), "D")

    def test_save_omits_empty_role_and_keys(self):
        self.proj.fg_palette = [
            {"rgb": [1, 2, 3], "role": ""},
            {"rgb": [4, 5, 6], "role": "B"},
        ]
        self.proj.fg_palette_seq = 2
        data = self.proj.to_dict()
        self.assertEqual(data["fg_palette"][0], {"rgb": [1, 2, 3]})
        self.assertEqual(data["fg_palette"][1], {"rgb": [4, 5, 6], "role": "B"})
        self.assertEqual(data["fg_palette_seq"], 2)
        # 空调板 + 零序号：两键都不落（上游侧零告警键）
        self.proj.fg_palette = []
        self.proj.fg_palette_seq = 0
        data = self.proj.to_dict()
        self.assertNotIn("fg_palette", data)
        self.assertNotIn("fg_palette_seq", data)

    def test_bad_entries_are_skipped(self):
        # 4 元素列表沿用旧口径截断容忍；len<3 / 不可转 int 才丢
        self._load(fg_palette=[[1, 2], "junk", {"rgb": "no"}, [9, 9]])
        self.assertEqual(len(self.proj.fg_palette), 0)


def _make_blk(fg=None, text="こんにちは"):
    blk = TextBlock(xyxy=[100, 100, 300, 200], text=text)
    if fg is not None:
        blk.fg_colors = list(fg)
    return blk


class SpeakerHintsTest(unittest.TestCase):
    PALETTE = [
        {"rgb": [27, 25, 50], "role": "A"},
        {"rgb": [250, 100, 100], "role": "Alice"},
        {"rgb": [10, 200, 10], "role": ""},  # 未赋名
    ]

    def test_hits_placeholders_and_real_names(self):
        blocks = [
            _make_blk([27, 25, 50]),  # Speaker A
            _make_blk([250, 100, 100]),  # Alice
            _make_blk([10, 200, 10]),  # 有色但未赋名 → 不标
            _make_blk([99, 99, 99]),  # 调板外的启发式自取色 → 不标
            _make_blk(None),  # 无取色 → 不标
        ]
        hints = speaker_hints_for_blocks(blocks, self.PALETTE)
        self.assertEqual(
            hints, ["Speaker A", "Alice", None, None, None]
        )

    def test_multi_color_one_role(self):
        palette = [
            {"rgb": [1, 2, 3], "role": "Alice"},
            {"rgb": [4, 5, 6], "role": "Alice"},
        ]
        hints = speaker_hints_for_blocks(
            [_make_blk([1, 2, 3]), _make_blk([4, 5, 6])], palette
        )
        self.assertEqual(hints, ["Alice", "Alice"])

    def test_empty_palette_all_none(self):
        self.assertEqual(
            speaker_hints_for_blocks([_make_blk([1, 2, 3])], []),
            [None],
        )

    def test_task_message_section_assembly(self):
        src = ["あ", "い", "う", "え"]
        hints = ["Speaker A", None, "Alice", None]
        msg = build_user_task_message(src, "Page 1", "", speaker_hints=hints)
        self.assertIn("Speaker hints", msg)
        self.assertIn("- id 1: Speaker A", msg)
        self.assertIn("- id 3: Alice", msg)
        self.assertNotIn("id 2", msg)
        self.assertNotIn("id 4", msg)
        # 全员未命中 → 整节省略（宁缺毋滥）
        none_msg = build_user_task_message(
            src, "Page 1", "", speaker_hints=[None] * 4
        )
        self.assertNotIn("Speaker hints", none_msg)
        # 未传参数 → 同样无小节
        plain_msg = build_user_task_message(src, "Page 1", "")
        self.assertNotIn("Speaker hints", plain_msg)


class PalettePageUiTest(unittest.TestCase):
    """离屏实跑：画布取色入板 → 行内改真名 → 落盘往返 → 删除报影响面。

    取样入口是画布（``ui/canvas.py::color_sampled``），页面只收
    ``accept_canvas_sample``；这里直接调页面的收色口，不建画布。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        # cv2 写 BGR：读回来是 RGB (27, 25, 50)，供取样点命中
        img[12, 12] = [50, 25, 27]
        cv2.imwrite(osp.join(self.proj_dir, "a.png"), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        blk = _make_blk([27, 25, 50])
        self.proj.pages["a.png"] = [blk]
        self.proj._image_info = {"a.png": {"width": 200, "height": 200}}
        self.proj.save()
        self.proj.set_current_img("a.png")

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self):
        from ui.workbench_palette import FgPalettePage

        page = FgPalettePage(self.proj)
        page.notify_requested.connect(self._on_notify)
        self.notifies = []
        return page

    def _on_notify(self, text, kind):
        self.notifies.append((text, kind))

    def test_sample_assigns_placeholder_and_persists(self):
        self.notifies = []
        page = self._page()
        page.accept_canvas_sample("a.png", 12, 12)
        self.assertEqual(self.proj.fg_palette[0]["rgb"], [27, 25, 50])
        self.assertEqual(self.proj.fg_palette[0]["role"], "A")
        self.assertTrue(any("Speaker A" in text for text, _ in self.notifies))
        # 近重不入板
        page.accept_canvas_sample("a.png", 12, 12)
        self.assertEqual(len(self.proj.fg_palette), 1)
        # 越界/未知页不入板
        page.accept_canvas_sample("a.png", 9999, 12)
        page.accept_canvas_sample("missing.png", 12, 12)
        self.assertEqual(len(self.proj.fg_palette), 1)
        # 落盘往返：角色与序号都在
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertEqual(fresh.fg_palette[0]["role"], "A")
        self.assertEqual(fresh.fg_palette_seq, 1)

    def test_page_cache_drops_on_project_switch(self):
        """换项目后页图缓存必须作废（同名页会静默读到上一个项目的像素）。

        ``ui/workbench_tasks.py::PageImageCache`` 只按页名索引，而主窗口是
        **同一个 ProjImgTrans 原地换项目**（``refresh_project_state``）：
        不随项目作废的话，取样色与候选缩略图都拿旧项目的图，且不报任何错。
        """
        page = self._page()
        page.accept_canvas_sample("a.png", 12, 12)
        self.assertEqual(self.proj.fg_palette[0]["rgb"], [27, 25, 50])

        # 第二个项目：同名页、不同像素、空调板
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        img[12, 12] = [50, 100, 200]  # cv2 写 BGR → 读回来 RGB (200, 100, 50)
        cv2.imwrite(osp.join(other.name, "a.png"), img)
        self.proj.directory = other.name
        self.proj.pages = {"a.png": [_make_blk([200, 100, 50])]}
        self.proj._image_info = {"a.png": {"width": 200, "height": 200}}
        self.proj.fg_palette = []
        self.proj.fg_palette_seq = 0
        page.refresh_state()

        page.accept_canvas_sample("a.png", 12, 12)
        self.assertEqual(self.proj.fg_palette[0]["rgb"], [200, 100, 50])

    def test_row_edit_renames_role_and_feeds_hints(self):
        page = self._page()
        page.accept_canvas_sample("a.png", 12, 12)
        row = page._rows[0]
        self.assertEqual(row.role_combo.currentText(), "Speaker A")
        row.role_combo.setCurrentText("Alice")
        row._on_edit_finished()
        self.assertEqual(self.proj.fg_palette[0]["role"], "Alice")
        # 改名后注入显示形态跟着变（颜色不变）
        self.assertEqual(
            speaker_hints_for_blocks(self.proj.pages["a.png"], self.proj.fg_palette),
            ["Alice"],
        )
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertEqual(fresh.fg_palette[0]["role"], "Alice")
        # 清空角色名同理写回空串（占位不再注入）
        page._rows[0].role_combo.setCurrentText("")
        page._rows[0]._on_edit_finished()
        self.assertEqual(self.proj.fg_palette[0]["role"], "")
        self.assertEqual(
            speaker_hints_for_blocks(self.proj.pages["a.png"], self.proj.fg_palette),
            [None],
        )

    def test_remove_reports_usage_count(self):
        self.notifies = []
        page = self._page()
        page.accept_canvas_sample("a.png", 12, 12)
        page._rows[0].remove_requested.emit()
        self.assertEqual(self.proj.fg_palette, [])
        self.assertTrue(
            any("Speaker A" in text and "1" in text for text, _ in self.notifies)
        )

    def test_hint_section_end_to_end(self):
        """调板角色 → speaker_hints（与 src_list 对齐）→ 任务消息小节。"""
        page = self._page()
        page.accept_canvas_sample("a.png", 12, 12)
        hints = speaker_hints_for_blocks(
            self.proj.pages["a.png"], self.proj.fg_palette
        )
        msg = build_user_task_message(
            ["こんにちは"], "Page 1", "", speaker_hints=hints
        )
        self.assertIn("- id 1: Speaker A", msg)

    def test_sampling_state_reflects_canvas(self):
        """画布侧状态回流（set_sampling_state）只改开关，不回发 sampling_toggled。"""
        page = self._page()
        seen = []
        page.sampling_toggled.connect(seen.append)
        page.set_sampling_state(True)
        self.assertTrue(page.sample_btn.isChecked())
        self.assertIn("Sampling", page.sample_btn.text())
        page.set_sampling_state(False)
        self.assertFalse(page.sample_btn.isChecked())
        self.assertEqual(seen, [])


class PaletteCandidateTest(unittest.TestCase):
    """全书扫描 → 候选 → 勾选入调板 / 改色路由（草稿态、不落项目 JSON）。

    只钉会动的契约：勾选才入库且占位名续号、已在调板者禁勾不入库、单次色
    默认隐藏（关掉时不可见行不得被带走）、改色只改候选不追加条目。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.full((200, 200, 3), 240, np.uint8)
        img[10:40, 10:60] = [200, 30, 30]
        img[60:90, 10:60] = [210, 40, 40]
        img[120:150, 10:60] = [30, 200, 30]
        cv2.imwrite(osp.join(self.proj_dir, "a.png"), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)

        def blk(fg, xyxy):
            b = TextBlock(xyxy=list(xyxy), text="こんにちは")
            b.fg_colors = list(fg)
            return b

        self.proj.pages["a.png"] = [
            blk([200, 30, 30], (10, 10, 60, 40)),
            blk([200, 30, 30], (10, 60, 60, 90)),  # 与上同色 → 2 块
            blk([30, 200, 30], (10, 120, 60, 150)),  # 单次色
        ]
        self.proj._image_info = {"a.png": {"width": 200, "height": 200}}
        self.proj.save()
        self.proj.set_current_img("a.png")

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self):
        from ui.workbench_palette import FgPalettePage

        page = FgPalettePage(self.proj)
        self.notifies = []
        self.jumps = []
        page.notify_requested.connect(
            lambda text, kind: self.notifies.append((text, kind))
        )
        page.jump_requested.connect(
            lambda pagename, index: self.jumps.append((pagename, index))
        )
        return page

    def test_scan_hides_singletons_then_apply_adds_checked(self):
        page = self._page()
        page._on_scan_colors()
        # 两条候选：2 块的主色 + 单次色；单次色默认不显示
        self.assertEqual([c["rgb"] for c in page._candidates], [[200, 30, 30], [30, 200, 30]])
        self.assertEqual(len(page._candidate_rows), 1)
        self.assertIn("1 colors found", page._summary_label.text())
        self.assertIn("appear only once", page._summary_label.text())

        page._candidate_rows[0].check.setChecked(True)
        self.assertTrue(page._add_checked_btn.isEnabled())
        page._on_add_checked()
        self.assertEqual(self.proj.fg_palette[0]["rgb"], [200, 30, 30])
        self.assertEqual(self.proj.fg_palette[0]["role"], palette_seq_to_name(1))
        # 入库后候选保留并标「已在调板」（复选框禁勾）
        self.assertTrue(page._candidates[0]["in_palette"])
        self.assertTrue(page._candidate_rows[0].check.isEnabled() is False)
        self.assertIn("Re-extract Whole Book", self.notifies[-1][0])
        # 落盘往返：占位名序号落在项目里（删除不复用的前提）
        fresh = ProjImgTrans(directory=self.proj_dir)
        self.assertEqual(fresh.fg_palette[0]["role"], palette_seq_to_name(1))
        self.assertEqual(fresh.fg_palette_seq, 1)
        # 候选本身不进项目 JSON
        self.assertNotIn("candidates", self.proj.to_dict())

    def test_singleton_toggle_hides_and_unchecks(self):
        page = self._page()
        page._on_scan_colors()
        self.assertEqual(len(page._candidate_rows), 1)
        page._singleton_check.setChecked(True)
        self.assertEqual(len(page._candidate_rows), 2)
        self.assertNotIn("appear only once", page._summary_label.text())
        # 勾上单次色后再关掉显示：不可见的行不能被「应用」带走
        page._candidate_rows[1].check.setChecked(True)
        self.assertTrue(page._candidates[1]["checked"])
        page._singleton_check.setChecked(False)
        self.assertFalse(page._candidates[1]["checked"])
        self.assertEqual(len(page._candidate_rows), 1)

    def test_existing_palette_colour_is_blocked(self):
        self.proj.fg_palette = [{"rgb": [200, 30, 30], "role": "A"}]
        page = self._page()
        page._on_scan_colors()
        self.assertTrue(page._candidates[0]["in_palette"])
        self.assertFalse(page._candidate_rows[0].check.isEnabled())
        # 即便被强制勾上，入库也要跳过「已在调板」的候选
        page._candidates[0]["checked"] = True
        page._on_add_checked()
        self.assertEqual(len(self.proj.fg_palette), 1)

    def test_recolor_target_updates_candidate_not_palette(self):
        page = self._page()
        page._on_scan_colors()
        seen = []
        page.sampling_toggled.connect(seen.append)
        page.set_sample_target(0)
        self.assertTrue(page.sample_btn.isChecked())
        self.assertEqual(seen, [True])  # 走既有 sampling_toggled 链路武装画布
        page.accept_canvas_sample("a.png", 15, 125)  # 绿色像素
        self.assertEqual(page._candidates[0]["rgb"], [30, 200, 30])
        self.assertEqual(self.proj.fg_palette, [])  # 改色不追加条目
        self.assertIsNone(page._sample_target)  # 目标一次性
        # 目标清掉后，下一次取样回到「追加新条目」的既有语义
        page.accept_canvas_sample("a.png", 15, 125)
        self.assertEqual(self.proj.fg_palette[0]["rgb"], [30, 200, 30])

    def test_sample_thumbnail_jumps_to_block(self):
        from ui.workbench_palette import _SampleThumb

        page = self._page()
        page._on_scan_colors()
        thumbs = page._candidate_rows[0].findChildren(_SampleThumb)
        self.assertTrue(thumbs)  # 有真实裁剪证据（经共享 PageImageCache 裁出）
        thumbs[0].clicked.emit()
        self.assertEqual(self.jumps, [("a.png", 0)])


class PagePixelFromSceneTest(unittest.TestCase):
    """scene → 页像素换算：除缩放是本次唯一「静默错一位」的风险，必须钉住。"""

    def test_scale_one(self):
        self.assertEqual(
            page_pixel_from_scene(QPointF(12.9, 30.2), 1.0, QSizeF(200, 200)),
            (12, 30),
        )

    def test_non_unit_scale_divides(self):
        # 200% 显示：显示坐标 24,40 → 页像素 12,20（不除缩放会得到 24,40）
        self.assertEqual(
            page_pixel_from_scene(QPointF(24, 40), 2.0, QSizeF(200, 200)),
            (12, 20),
        )
        # 50% 显示：显示坐标 6,10 → 页像素 12,20
        self.assertEqual(
            page_pixel_from_scene(QPointF(6, 10), 0.5, (200, 200)), (12, 20)
        )

    def test_out_of_bounds_and_bad_args(self):
        self.assertIsNone(page_pixel_from_scene(QPointF(200, 10), 1.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, 200), 1.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(-1, 10), 1.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, -0.5), 1.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, 10), 0.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, 10), -2.0, (200, 200)))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, 10), 1.0, None))
        self.assertIsNone(page_pixel_from_scene(QPointF(10, 10), 1.0, (0, 0)))


class _StubTranslator:
    """只实现 translate_textblk_lst 依赖的最小面。"""

    supports_tag_instructions = True
    supports_speaker_hints = True
    _preprocess_hooks = {}
    _postprocess_hooks = {}

    def translate(self, text, **kwargs):
        self.captured = (list(text), dict(kwargs))
        return [f"t:{t}" for t in text]


class TranslateWiringTest(unittest.TestCase):
    """translate_textblk_lst → translate(**kwargs) 的 speaker_hints 接线。

    关键契约：列表与**非空块**对齐且含 None 占位（注入侧按位置还原块 id）；
    调板无角色标注时连参数都不传（其余翻译器签名不含它）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        cv2.imwrite(osp.join(self.proj_dir, "a.png"), img)
        self.proj = ProjImgTrans(directory=self.proj_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, blocks, palette):
        from modules.translators.base import BaseTranslator

        self.proj.fg_palette = palette
        self.proj.pages["a.png"] = blocks
        trans = _StubTranslator()
        BaseTranslator.translate_textblk_lst(
            trans, blocks, project=self.proj, page_key="a.png"
        )
        return trans.captured[1]

    def test_aligned_with_none_placeholders(self):
        blocks = [
            _make_blk([27, 25, 50], text="aa"),
            _make_blk([9, 9, 9], text="bb"),
            _make_blk([27, 25, 50], text="   "),  # 空原文不进任务
        ]
        kwargs = self._run(blocks, [{"rgb": [27, 25, 50], "role": "A"}])
        self.assertEqual(kwargs["speaker_hints"], ["Speaker A", None])

    def test_absent_without_roles(self):
        blocks = [_make_blk([27, 25, 50], text="aa")]
        kwargs = self._run(blocks, [{"rgb": [27, 25, 50], "role": ""}])
        self.assertNotIn("speaker_hints", kwargs)
        kwargs = self._run(blocks, [])
        self.assertNotIn("speaker_hints", kwargs)


if __name__ == "__main__":
    unittest.main()
