"""泛用工作台回归：任务适配层 + 面板接线。

覆盖 D20（三段式：任务导航 → 候选列表 → 执行）、D23／D11（审批预览
100% 原比例）、D26（跳转信号）、D27（批量告知弹窗）、D28（驳回可逆、
且粒度＝单个问题）、D33d（误聚组默认不勾选）、D35（整批一条撤回），以及
批次 C 的减法与加法：**扁平六项导航**（不再两级、不再跳步门禁）、
**两个非删除待办队列**（人工待办／程序建议分区，退出＝从列表移除／忽略）、
**批量框扩张退役**（2026-09-26，见 scripts/audit_registry.json）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_workbench_panel.py -q
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
from qtpy.QtWidgets import QApplication, QWidget  # noqa: E402

from ui.batch_ops import BatchOperation  # noqa: E402
from ui.workbench_tasks import (  # noqa: E402
    CLEANUP_TASK_IDS,
    GLOSSARY,
    MERGE,
    MISREAD,
    OCR_REVIEW,
    SIMPLE_INPAINT,
    STORY,
    TRANS_REVIEW,
    build_batch_tasks,
    build_review_tasks,
)
from utils.block_tags import (  # noqa: E402
    MISREAD_TAG_ID,
    OCR_REVIEW_ID,
    has_ocr_review_pending,
    has_tag,
    has_trans_review_pending,
    is_tag_reviewed,
    set_tag,
)
from utils.config import pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402

PAGE_A = "a.png"
PAGE_B = "b.png"


def _blk(text, xyxy):
    blk = TextBlock(xyxy=list(xyxy), text=[text])
    blk._bounding_rect = [xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]]
    return blk


def _misread(text, xyxy, *, reviewed=False):
    blk = _blk(text, xyxy)
    set_tag(blk, MISREAD_TAG_ID, "program", subtypes=["numeric"])
    if reviewed:
        from utils.block_tags import set_tags_reviewed

        set_tags_reviewed(blk, True)
    return blk


class _WorkbenchTestCase(unittest.TestCase):
    """合成两页项目：A 页一行三框（中间是误框），B 页一行两框。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._limit = pcfg.batch_backup_versions
        pcfg.batch_backup_versions = 1
        self._tmp = tempfile.TemporaryDirectory()
        self.proj_dir = self._tmp.name
        image = np.full((200, 200, 3), 240, dtype=np.uint8)
        for name in (PAGE_A, PAGE_B):
            cv2.imwrite(osp.join(self.proj_dir, name), image)
        self.proj = ProjImgTrans(directory=self.proj_dir)
        self.proj.pages[PAGE_A] = [
            _blk("keep", (10, 10, 60, 40)),
            _misread("8", (10, 42, 60, 72)),
            _blk("tail", (10, 74, 60, 104)),
        ]
        # B 页两框并排（同一行、左右相邻）：组内方向反转会真的改拼接顺序
        self.proj.pages[PAGE_B] = [
            _blk("hello", (10, 10, 40, 60)),
            _blk("world", (50, 10, 80, 60)),
        ]
        self.proj._image_info = {
            PAGE_A: {"width": 200, "height": 200},
            PAGE_B: {"width": 200, "height": 200},
        }
        self.proj.save()
        self.proj.set_current_img(PAGE_A)
        for name in (PAGE_A, PAGE_B):
            self.proj.save_mask(name, np.zeros((200, 200), dtype=np.uint8))
        self.window = QWidget()
        # 主窗口侧的两个调用口（设计 §8 的必传项：落盘 + 前置对齐）
        self.window._sync_block_data = lambda: None
        self.window._sync_and_commit_project = lambda force_sync=False: (
            self.proj.save()
        )
        self.window.show()

    def tearDown(self):
        pcfg.batch_backup_versions = self._limit
        self.window.close()
        self._tmp.cleanup()

    def _op(self):
        return BatchOperation(
            self.proj,
            commit=self.window._sync_and_commit_project,
            sync_block_data=self.window._sync_block_data,
        )

    def _tasks(self):
        return build_batch_tasks(self.proj, self._op())

    def _review_tasks(self):
        return build_review_tasks(self.proj)

    def _panel(self, *, show=True):
        from ui.glossary_agent_panel import GlossaryAgentPanel

        panel = GlossaryAgentPanel(self.proj, self.window)
        panel.resize(460, 800)
        if show:
            # show 会 `_ensure_worker` 起 QThread；addCleanup 保证它被收掉，
            # 否则解释器退出时残留线程会崩（替换 _worker 的用例改用 show=False）
            panel.show()
        self.app.processEvents()
        self.addCleanup(panel._shutdown)
        return panel

    def _texts(self, pagename):
        return [b.get_text() for b in self.proj.pages[pagename]]


# ── 任务适配层（无 QWidget）───────────────────────────────────────


class TaskAdapterTest(_WorkbenchTestCase):
    def test_misread_rows_and_defaults(self):
        task = self._tasks()[MISREAD]
        plan = task.plan({})
        self.assertEqual(len(plan["rows"]), 1)
        row = plan["rows"][0]
        self.assertEqual(row.key, (PAGE_A, 1))
        self.assertEqual(row.pagename, PAGE_A)
        self.assertEqual(row.block_index, 1)
        self.assertTrue(row.checked)  # 未驳回 → 默认勾选（D28）
        self.assertIn(
            "1 suspicious block(s) found; 0 marked as false positives, 1 selected for deletion",
            plan["summary"],
        )
        self.assertIn("Source: OCR post-processing rules.", plan["detail"])
        self.assertEqual(row.cells[2], "Numeric only")  # 子类名来自标签体系
        self.assertEqual(task.pending({}), 1)

    def test_no_japanese_rows_start_unchecked(self):
        """交接 §4.2①：「无假名汉字」最容易误伤真实拉丁文本与拟声词，默认不勾选。

        含多子类型时以含它为准（不能靠"别的子类型更确定"替它背书），且这
        与"驳回"无关——它只是**默认勾选口径**，用户仍可手动勾上删除。
        """
        from utils.block_tags import apply_misread_tag  # noqa: E402

        blk = _blk("SS", (10, 106, 60, 136))
        apply_misread_tag(blk, ["no_japanese"])
        self.proj.pages[PAGE_A].append(blk)
        task = self._tasks()[MISREAD]
        rows = {row.block_index: row for row in task.plan({})["rows"]}
        risky = rows[3]
        self.assertFalse(risky.checked)
        self.assertEqual(risky.cells[3], "Confirm before deleting")
        # 纯数字那行不受影响：仍按 D28 默认勾选
        self.assertTrue(rows[1].checked)
        self.assertEqual(rows[1].cells[3], "Delete by default")
        # 队列计数口径不变（默认勾选只影响勾选，不改"待审校条数"）
        self.assertEqual(task.pending({}), 2)
        # 摘要第三个数字必须＝**实际默认勾选的行数**：摘要不能替勾选框说话
        # （含 no_japanese 的行不勾选，2 个可疑框里只有 1 个默认要删）
        self.assertIn("1 selected for deletion by default", task.plan({})["summary"])

    def test_misread_reject_is_reversible_and_block_level(self):
        task = self._tasks()[MISREAD]
        plan = task.plan({})
        row = plan["rows"][0]
        out = task.run_action("reject", [row], {})
        self.assertTrue(out["replan"])
        self.assertTrue(is_tag_reviewed(self.proj.pages[PAGE_A][1], MISREAD_TAG_ID))
        # 驳回后：状态列变 Rejected、默认不再勾选、待处理计数归零（D28）
        row2 = task.plan({})["rows"][0]
        self.assertFalse(row2.checked)
        self.assertEqual(task.pending({}), 0)
        # 再点一次即取消驳回（可逆、不从队列消失）
        task.run_action("reject", [row2], {})
        self.assertFalse(is_tag_reviewed(self.proj.pages[PAGE_A][1], MISREAD_TAG_ID))
        self.assertEqual(len(task.plan({})["rows"]), 1)

    def test_misread_preview_is_unscaled(self):
        task = self._tasks()[MISREAD]
        row = task.plan({})["rows"][0]
        preview = task.preview(row)
        # 记录块 50x30，外扩短边 50%＝15，左右被页边/邻框截住 → 75x34
        self.assertEqual(preview.image.shape[:2], (34, 75))
        self.assertEqual(preview.origin, (0, 40))
        self.assertEqual(preview.overlays[0]["rect"], [10, 42, 60, 72])

    def test_merge_excludes_unrejected_misread(self):
        """D30：带未驳回误识别标签的框整框排除；驳回后视同普通框。"""
        task = self._tasks()[MERGE]
        plan = task.plan({})
        # a 页中间框被排除后不再相邻 → 只剩 b 页那一组
        self.assertEqual([r.pagename for r in plan["rows"]], [PAGE_B])
        self.assertIn("1 excluded", plan["summary"])
        from utils.block_tags import set_tags_reviewed

        set_tags_reviewed(self.proj.pages[PAGE_A][1], True)
        plan = task.plan({})
        keys = [row.key for row in plan["rows"]]
        self.assertIn((PAGE_A, 0), keys)
        self.assertEqual(task.pending({}), len(plan["rows"]))

    def test_merge_reverse_only_flips_build_order(self):
        """D32：反转只改拼接顺序，勾选范围与样式来源不变。"""
        task = self._tasks()[MERGE]
        row = [r for r in task.plan({})["rows"] if r.pagename == PAGE_B][0]
        before = task.apply([row.key], {})
        self.assertTrue(before["started"])
        self.assertEqual(self._texts(PAGE_B), ["hello world"])

    def test_merge_reverse_flips_text_order(self):
        task = self._tasks()[MERGE]
        row = [r for r in task.plan({})["rows"] if r.pagename == PAGE_B][0]
        options = {"flatten_lines": True}
        task.run_action("reverse", [row], options)
        self.assertEqual(options["reversed_groups"], {row.key})
        report = task.apply([row.key], options)
        self.assertTrue(report["started"])
        self.assertEqual(self._texts(PAGE_B), ["world hello"])

    def test_merge_default_checkbox_skips_oversize(self):
        """D33d：误聚组默认不勾选（此处用超小页把整页一组判成误聚）。"""
        # 组包围盒 70x50；页 55x70 → 宽度超过 85%（D33d 的阈值）
        self.proj._image_info[PAGE_B] = {"width": 55, "height": 70}
        task = self._tasks()[MERGE]
        rows = [r for r in task.plan({})["rows"] if r.pagename == PAGE_B]
        self.assertTrue(rows and rows[0].payload["group"].oversize)
        self.assertFalse(rows[0].checked)
        self.assertIn("False grouping", rows[0].cells[3])

    def test_batch_family_is_three_tasks_and_expand_is_gone(self):
        """批量框扩张退役（2026-09-26）：工厂与导航都不再有它。

        退役依据＝只有机械几何写入、没有可靠的自动排版消费；登记见
        ``scripts/audit_registry.json``。
        """
        from ui import workbench_tasks as wt

        self.assertEqual(
            set(self._tasks()), {MISREAD, MERGE, SIMPLE_INPAINT}
        )
        self.assertEqual(set(wt.CLEANUP_TASK_IDS), set(self._tasks()))
        self.assertFalse(hasattr(wt, "ExpandTask"))
        self.assertFalse(hasattr(wt, "EXPAND"))
        from utils.config import ProgramConfig

        self.assertNotIn("workbench_expand_px", ProgramConfig.__annotations__)

    def test_simple_inpaint_rows_are_pages(self):
        task = self._tasks()[SIMPLE_INPAINT]
        plan = task.plan({})
        for row in plan["rows"]:
            self.assertIsInstance(row.key, str)
            self.assertIsNone(row.block_index)
        self.assertIn("complex", plan["summary"])

    def test_confirm_html_mentions_consequences(self):
        """D27：每个批量任务的告知弹窗都要照实说后果。"""
        tasks = self._tasks()
        misread = tasks[MISREAD].plan({})["rows"]
        html = tasks[MISREAD].confirm_html(misread, {})
        self.assertIn("inpainted", html)  # 修复痕迹不会因此还原
        merge = tasks[MERGE].plan({})["rows"]
        html = tasks[MERGE].confirm_html(merge, {})
        self.assertIn("styles", html.lower())
        inpaint = tasks[SIMPLE_INPAINT].plan({})["rows"]
        html = tasks[SIMPLE_INPAINT].confirm_html(inpaint, {})
        self.assertIn("inpainted layer", html)


# ── 面板接线 ──────────────────────────────────────────────────────


class PanelTest(_WorkbenchTestCase):
    def test_nav_order_and_default_task(self):
        """扁平六项导航（批次 C）：一行一个任务，顺序＝用户工作流顺序。"""
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        panel = self._panel()
        self.assertEqual(WORKBENCH_ORDER, (
            MISREAD, MERGE, OCR_REVIEW, SIMPLE_INPAINT,
            # 翻译准备是一个入口（术语与剧情是它的两个子页）
            "translation_prep", TRANS_REVIEW,
        ))
        self.assertEqual(panel.pages.count(), 6)
        self.assertEqual(panel.current_task(), MISREAD)
        self.assertTrue(panel.nav._buttons[MISREAD].isChecked())
        # 组标题只是视觉分段：每个任务都有自己的钮，没有两级选择
        self.assertFalse(hasattr(panel.nav, "_category_buttons"))
        for task_id in WORKBENCH_ORDER:
            self.assertIn(task_id, panel.nav._buttons)

    def test_switching_plans_the_task_once(self):
        panel = self._panel()
        view = panel._batch_views[MERGE]
        self.assertEqual(view._rows, [])  # 还没切过去，没规划
        panel.nav.select(MERGE)
        self.app.processEvents()
        self.assertTrue(view._rows)
        self.assertNotIn(MERGE, panel._dirty_tasks)

    def test_nav_chip_flow_fits_two_rows_with_complete_active_label(self):
        """导航＝按需换行的 chip 流（批次 D 修订）：常态两行、激活项不省略。

        会动的三条契约：① 整条导航的高度按行数收紧（原先六项各占一行、外加两行
        组标题＝8 行高，把页内信息挤没了）；② **当前任务**的完整标签必须排得下
        ——压缩与省略只发生在未激活项上，中英文两套标签各排一遍（英文更长）；
        ③ ``text()`` 始终是完整逻辑标签（省略只在渲染层）。宽度读
        ``target_width``／``full_width``（分配结果，补间动画进行中也真），故离屏
        环境不依赖动画。
        """
        from ui.glossary_agent_panel import WORKBENCH_ORDER, _TASK_LABELS

        panel = self._panel()
        # 标签源取模块表本身（不是 chip 上的文本——那里已缀过计数）
        english = {task_id: _TASK_LABELS[task_id] for task_id in WORKBENCH_ORDER}
        self._assert_active_chip_never_elides(panel, english)
        chinese = self._chinese_nav_labels(english)
        if chinese:
            # 真取到了译文才算排过第二套（否则只是把英文再排一遍）
            self.assertNotEqual(chinese["translation_prep"], english["translation_prep"])
            self._assert_active_chip_never_elides(panel, chinese)

    def _assert_active_chip_never_elides(self, panel, labels):
        """给定一套标签逐项激活：激活项完整、行数不超两行、逻辑文本不缩短。"""
        from ui import glossary_agent_panel as gap
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        nav = panel.nav
        # 标签源就是这张表（真机上由启动时装的 qm 决定），换语言＝换表内容
        original = dict(gap._TASK_LABELS)
        gap._TASK_LABELS.update(labels)
        self.addCleanup(gap._TASK_LABELS.update, original)
        for task_id in WORKBENCH_ORDER:
            nav.set_count(task_id, 0)  # 把新标签推给 chip
        nav._relayout(animate=False)
        self.assertLessEqual(nav.height(), 56)  # ≤ 两行 chip（6 + 22 + 4 + 22）
        for task_id in WORKBENCH_ORDER:
            nav.select(task_id)
            self.app.processEvents()
            chip = nav._buttons[task_id]
            self.assertTrue(chip.isChecked())
            # 逻辑文本永远是完整标签（计数只是后缀），省略只发生在渲染层
            self.assertTrue(chip.text().startswith(labels[task_id]))
            self.assertGreaterEqual(chip.target_width, chip.full_width())
            self.assertLessEqual(nav.height(), 56)

    def _chinese_nav_labels(self, english):
        """同一 source 的 ``zh_CN`` 译文；qm 还没编出来时返回 ``{}``（跳过该套）。"""
        from qtpy.QtCore import QCoreApplication, QTranslator

        from utils import shared

        translator = QTranslator()
        if not translator.load("zh_CN", shared.TRANSLATE_DIR):
            return {}
        self.app.installTranslator(translator)
        try:
            return {
                task_id: QCoreApplication.translate("GlossaryAgentPanel", label)
                for task_id, label in english.items()
            }
        finally:
            self.app.removeTranslator(translator)

    def test_nav_order_and_default_task(self):
        """扁平六项导航（批次 C/D）：chip 流按需换行，顺序＝用户工作流顺序。"""
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        panel = self._panel()
        self.assertEqual(WORKBENCH_ORDER, (
            MISREAD, MERGE, OCR_REVIEW, SIMPLE_INPAINT,
            # 翻译准备是一个入口（术语与剧情是它的两个子页）
            "translation_prep", TRANS_REVIEW,
        ))
        self.assertEqual(panel.pages.count(), 6)
        self.assertEqual(panel.current_task(), MISREAD)
        self.assertTrue(panel.nav._buttons[MISREAD].isChecked())
        # 组标题只是视觉分段：每个任务都有自己的钮，没有两级选择
        self.assertFalse(hasattr(panel.nav, "_category_buttons"))
        for task_id in WORKBENCH_ORDER:
            self.assertIn(task_id, panel.nav._buttons)

    def test_plan_detail_line_is_visible(self):
        """plan 的 detail（原因/来源说明）落在页面可见的说明行，不再藏 tooltip。"""
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view.replan()
        self.assertTrue(view._detail.isVisibleTo(view))
        self.assertIn("OCR post-processing rules", view._detail.text())

    def test_preview_is_unscaled_and_handed_to_the_float(self):
        """D11／D23 口径不变：交给浮层的图是 100% 原比例（界面不缩不放）。

        D44：预览已不在任务页里（原先固定 120~280px 高，大图看不全），
        改由面板的浮层显示，选中行经 ``preview_requested`` 交接。
        标题只报"哪一页的哪个框"——图尺寸对审阅没有意义，缩放看浮层读数。
        """
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        seen = []
        view.preview_requested.connect(lambda pixmap, caption: seen.append((pixmap, caption)))
        view._table.setCurrentCell(0, 1)
        self.app.processEvents()
        self.assertEqual(len(seen), 1)
        pixmap, caption = seen[0]
        self.assertEqual(pixmap.size().width(), 75)
        self.assertEqual(pixmap.size().height(), 34)
        self.assertIn(PAGE_A, caption)
        # 面板侧：浮层收到同一张图，且默认就是 100%（不自动适应窗口）
        self.assertIs(panel._preview_panel.canvas._pixmap, pixmap)
        self.assertEqual(panel._preview_panel.canvas.scale(), 1.0)
        self.assertTrue(panel._preview_panel.is_open())

    def test_switching_task_dismisses_the_float(self):
        """换任务即收起审批浮层（旧图对应的行已不在当前列表里）。"""
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._table.setCurrentCell(0, 1)
        self.app.processEvents()
        self.assertTrue(panel._preview_panel.is_open())
        panel.nav.select(MERGE)
        self.app.processEvents()
        self.assertFalse(panel._preview_panel.is_open())

    def test_single_row_can_reopen_the_float_after_closing(self):
        """列表只有一行时，关掉浮层后再点那行仍能弹出来。

        选中行没变 → 不发 ``itemSelectionChanged``；靠 ``cellClicked`` 补，
        且视图要先从浮层的 ``closed`` 知道"预览已收起"（否则多行时点别行
        碰巧能弹、只有一行时永远弹不出来）。
        """
        panel = self._panel()  # 本用例的合成工程只有一个误识别块
        view = panel._batch_views[MISREAD]
        self.assertEqual(len(view._rows), 1)
        view._table.setCurrentCell(0, 1)
        self.app.processEvents()
        self.assertTrue(panel._preview_panel.is_open())
        # 用户点画布／按 Esc 关掉浮层
        panel._preview_panel.close_panel()
        self.assertFalse(panel._preview_panel.is_open())
        # 再点那一行（选中行没变）
        view._table.cellClicked.emit(0, 1)
        self.app.processEvents()
        self.assertTrue(panel._preview_panel.is_open())
        self.assertIsNotNone(panel._preview_panel.canvas._pixmap)

    def test_nav_order_and_default_task(self):
        """扁平六项导航（批次 C）：一行一个任务，顺序＝用户工作流顺序。"""
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        panel = self._panel()
        self.assertEqual(WORKBENCH_ORDER, (
            MISREAD, MERGE, OCR_REVIEW, SIMPLE_INPAINT,
            # 翻译准备是一个入口（术语与剧情是它的两个子页）
            "translation_prep", TRANS_REVIEW,
        ))
        self.assertEqual(panel.pages.count(), 6)
        self.assertEqual(panel.current_task(), MISREAD)
        self.assertTrue(panel.nav._buttons[MISREAD].isChecked())
        # 组标题只是视觉分段：每个任务都有自己的钮，没有两级选择
        self.assertFalse(hasattr(panel.nav, "_category_buttons"))
        for task_id in WORKBENCH_ORDER:
            self.assertIn(task_id, panel.nav._buttons)

    def test_first_task_is_planned_when_workbench_opens_late(self):
        """打开项目时工作台还没开：首个任务不能"以为已规划过"。

        2026-09-18 实测：误识别清理导航上写着 15 条未处理，点进去列表却是空的
        ——``refresh_project_state`` 在面板不可见时跳过规划，却已经把该任务
        从 ``_dirty_tasks`` 摘掉了，之后 ``showEvent`` 也不再补。
        """
        from ui.glossary_agent_panel import GlossaryAgentPanel

        hidden = GlossaryAgentPanel(self.proj, self.window)  # 不 show＝工作台还没开
        self.addCleanup(hidden._shutdown)
        hidden.refresh_project_state()
        self.assertIn(MISREAD, hidden._dirty_tasks)  # 没规划过就不能算规划过
        hidden.show()
        self.app.processEvents()
        self.assertTrue(hidden._batch_views[MISREAD]._rows)
        self.assertNotIn(MISREAD, hidden._dirty_tasks)

    def test_unchecking_all_disables_execute(self):
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._rows[0].checked = False
        view._on_check_toggled(0, False)
        self.assertFalse(view._execute_btn.isEnabled())

    def test_apply_records_version_and_marks_others_dirty(self):
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._confirm = lambda rows: True
        view._on_execute()
        self.assertEqual(self._texts(PAGE_A), ["keep", "tail"])
        self.assertTrue(panel.rollback_btn.isEnabled())
        self.assertIsNotNone(panel._last_version_seq)
        # 版本号即本次写版本，可供「撤销上次批量」精确回滚（D35）
        self.assertEqual(panel._last_version_seq, 1)

    def test_rollback_restores_and_consumes_version(self):
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._confirm = lambda rows: True
        view._on_execute()
        panel._batch_tasks[MISREAD].op.undo_last(expect_seq=panel._last_version_seq)
        panel.clear_batch_version()
        self.assertEqual(self._texts(PAGE_A), ["keep", "8", "tail"])
        self.assertFalse(panel.rollback_btn.isEnabled())
        self.assertIsNone(panel._last_version_seq)

    def test_jump_signal_carries_page_and_block(self):
        panel = self._panel()
        seen = []
        panel.jump_requested.connect(lambda page, idx: seen.append((page, idx)))
        view = panel._batch_views[MISREAD]
        view._table.setCurrentCell(0, 1)
        view._jump_btn.click()
        self.assertEqual(seen, [(PAGE_A, 1)])

    def test_jumping_steps_is_never_gated(self):
        """批次 C：跳步弹窗与 ``workbench_warn_skip_order`` 一并去掉。

        顺序仍按用户工作流排列（导航顺序即提示），但**不门禁、不弹窗**：
        前序队列里还有东西也照常切过去。
        """
        from utils.config import ProgramConfig
        from ui.glossary_agent_panel import GlossaryAgentPanel

        self.assertNotIn(
            "workbench_warn_skip_order", ProgramConfig.__annotations__
        )
        self.assertFalse(hasattr(GlossaryAgentPanel, "earlier_pending"))
        panel = self._panel()
        self.assertTrue(self._tasks()[MISREAD].pending({}) > 0)  # 前序确有未处理
        panel.nav.select(TRANS_REVIEW)
        self.assertEqual(panel.current_task(), TRANS_REVIEW)

    def test_chat_is_gone_but_log_survives(self):
        """D19：砍 Chat；worker 日志改落底部日志条。"""
        panel = self._panel()
        self.assertFalse(hasattr(panel, "_chat_area"))
        self.assertFalse(hasattr(panel, "input_edit"))
        panel._append_log("hello from worker")
        self.assertIn("hello from worker", panel._log_view.toPlainText())

    def test_prepare_emits_instruction_without_bubble(self):
        """D19：翻译准备不再经 Chat 气泡链路，指令直接入队。"""
        from ui.glossary_agent_panel import GlossaryAgentWorker

        panel = self._panel(show=False)
        worker = GlossaryAgentWorker(self.proj)
        panel._worker = worker
        panel._wire_worker(worker)
        sent = []
        worker.instruction_requested.disconnect(worker.run_instruction)
        worker.instruction_requested.connect(sent.append)
        old = pcfg.workbench_confirm_costly
        pcfg.workbench_confirm_costly = False
        try:
            panel._on_prepare()
        finally:
            pcfg.workbench_confirm_costly = old
        self.assertEqual(len(sent), 1)
        self.assertIn("Prepare the drafts", sent[0])

    def test_no_project_keeps_empty_state(self):
        from ui.glossary_agent_panel import GlossaryAgentPanel

        proj = ProjImgTrans(directory=None)
        panel = GlossaryAgentPanel(proj, None)
        self.assertFalse(panel.has_project())
        self.assertEqual(panel._stack.currentIndex(), 0)

    def test_page_index_maps_every_task(self):
        """任务→页下标是显式登记，切任务必落到它自己那页。"""
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        panel = self._panel()
        indices = [panel._page_index(task_id) for task_id in WORKBENCH_ORDER]
        self.assertEqual(len(set(indices)), len(WORKBENCH_ORDER))  # 一一对应
        for task_id in WORKBENCH_ORDER:
            panel.nav.select(task_id)
            self.app.processEvents()
            self.assertEqual(panel.pages.currentIndex(), panel._page_index(task_id))
            view = panel._batch_views.get(task_id)
            if view is not None:
                self.assertIs(panel.pages.widget(panel._page_index(task_id)), view)

    def test_log_lines_carry_their_task_label(self):
        """面板日志跨任务共用：批量行前缀自己的任务名，才分得清是谁干的。"""
        from ui.glossary_agent_panel import _TASK_LABELS

        panel = self._panel()
        panel._append_log("deleted 2 blocks", MISREAD)
        # 署名与正文之间带分隔符，否则两段文字粘成一句
        self.assertIn(
            _TASK_LABELS[MISREAD] + " · deleted 2 blocks",
            panel._log_view.toPlainText().replace("\xa0", " "),
        )
        # worker 的行（无任务归属）不缀前缀
        panel._append_log("Draft loaded.", "")
        self.assertIn("Draft loaded.", panel._log_view.toPlainText())

    # ── 刷新前置对齐 + 过时灯（2026-09-23 用户实测「刷新无效」）──────

    def test_replan_runs_pre_sync_before_plan(self):
        """刷新按钮的修复点：重扫**先对齐数据层再规划**——顺序反了等于没修
        （对齐跑在 plan 之后，plan 读到的仍是旧 proj.pages）。"""
        from ui.workbench_batch_view import BatchTaskView

        task = self._tasks()[MISREAD]
        order = []
        original_plan = task.plan

        def spy_plan(options):
            order.append("plan")
            return original_plan(options)

        task.plan = spy_plan
        view = BatchTaskView(task, pre_replan=lambda: order.append("sync"))
        view.replan()
        self.assertEqual(order, ["sync", "plan"])
        # 无对齐口（离屏台场景）时重扫照常工作
        bare = BatchTaskView(task)
        bare.replan()
        self.assertTrue(bare._rows)

    # ── 非删除待办队列（批次 C 新增）───────────────────────────────

    def _seed_reviews(self):
        """给合成工程挂上各类记录：人工待办两条 + 旧 ID 一条 + 程序建议一条。"""
        from utils.block_tags import (
            OCR_REVIEW_ID,
            TRANS_REVIEW_ID,
            set_manual_tag,
            set_tag,
        )

        manual_ocr = self.proj.pages[PAGE_B][0]
        set_manual_tag(manual_ocr, OCR_REVIEW_ID, True)
        pending_trans = self.proj.pages[PAGE_B][1]
        set_manual_tag(pending_trans, TRANS_REVIEW_ID, True)
        pending_trans.translation = "old"
        # 旧项目的旧 ID：读侧算同一条待办，取消时连它一起清
        legacy = _blk("legacy", (10, 62, 80, 92))
        set_tag(legacy, "trans_polish", "manual")
        self.proj.pages[PAGE_B].append(legacy)
        # 程序建议：低置信度（未驳回才是建议）
        suggest = self.proj.pages[PAGE_A][0]
        set_tag(suggest, "ocr_low_conf", "program", score=0.31)
        return manual_ocr, pending_trans, legacy, suggest

    def test_review_queue_sections_and_no_batch_semantics(self):
        """人工待办与程序建议分区呈现，且这个队列**没有**批量写回与版本。"""
        self._seed_reviews()
        task = self._review_tasks()[OCR_REVIEW]
        plan = task.plan()
        self.assertEqual(set(plan["sections"]), {"manual", "suggestions"})
        self.assertEqual(len(plan["sections"]["manual"]), 1)
        self.assertEqual(len(plan["sections"]["suggestions"]), 1)
        self.assertIn("recorded by you", plan["summary"])
        # 行的单元格是「页名 + 原文摘要」，没有删除列、没有勾选默认值
        self.assertEqual(plan["sections"]["manual"][0].cells[0], PAGE_B)
        # 待办队列没有 apply / confirm_html / options_spec 这套批量语义
        self.assertFalse(hasattr(task, "apply"))
        self.assertFalse(hasattr(task, "confirm_html"))
        self.assertFalse(hasattr(task, "options_spec"))
        # 行的默认勾选＝未选中（勾选只是"选中这条"，不是"要执行"）
        self.assertFalse(plan["sections"]["manual"][0].checked)

    def test_review_pending_count_only_manual(self):
        """导航计数只数人工承诺的工作，程序建议不冒充待办（交接 §4.2③）。"""
        manual_ocr, pending_trans, legacy, suggest = self._seed_reviews()
        self.assertEqual(self._review_tasks()[OCR_REVIEW].pending(), 1)
        # 旧 ID 也是一条待办（读侧兼容），故这里 2 条
        self.assertEqual(self._review_tasks()[TRANS_REVIEW].pending(), 2)
        self.assertIn(
            "1", self._review_tasks()[OCR_REVIEW].summary_text({"manual": 1, "suggestions": 3})
        )

    def test_dropping_records_clears_only_the_record(self):
        manual_ocr, pending_trans, legacy, suggest = self._seed_reviews()
        tasks = self._review_tasks()
        rows = tasks[TRANS_REVIEW].plan()["sections"]["pending"]
        self.assertEqual(len(rows), 2)
        out = tasks[TRANS_REVIEW].run_section_action("pending", rows)
        self.assertTrue(out["replan"])
        # 待办清空，但**译文一个字没改**、块本身还在
        self.assertFalse(has_trans_review_pending(pending_trans))
        self.assertFalse(has_trans_review_pending(legacy))
        self.assertEqual(pending_trans.translation, "old")
        self.assertIs(self.proj.pages[PAGE_B][1], pending_trans)
        self.assertNotIn("trans_polish", legacy.tags)
        self.assertEqual(tasks[TRANS_REVIEW].pending(), 0)

    def test_manual_record_drop_keeps_directives_and_program_suggestion(self):
        from utils.block_tags import set_manual_tag

        blk = self.proj.pages[PAGE_B][0]
        set_manual_tag(blk, OCR_REVIEW_ID, True)
        set_tag(blk, "handwritten", "manual")  # 持久翻译指示
        set_tag(blk, "ocr_low_conf", "program", score=0.2)  # 程序建议
        task = self._review_tasks()[OCR_REVIEW]
        rows = task.plan()["sections"]["manual"]
        task.run_section_action("manual", rows)
        # 只清这份记录：指示与程序建议都还在（交接 §3.2）
        self.assertFalse(has_ocr_review_pending(blk))
        self.assertTrue(has_tag(blk, "handwritten"))
        self.assertIn("ocr_low_conf", blk.tags)
        # 丢掉人工记录后，那块仍以程序建议的身份留在列表里
        self.assertEqual(
            [r.pagename for r in task.plan()["sections"]["suggestions"]],
            [PAGE_B],
        )

    def test_ignoring_a_suggestion_marks_that_problem_only(self):
        from utils.block_tags import (
            MISREAD_TAG_ID,
            apply_misread_tag,
            set_tag,
        )

        blk = self.proj.pages[PAGE_A][0]
        set_tag(blk, "ocr_low_conf", "program", score=0.2)
        apply_misread_tag(blk, ["no_japanese"])
        task = self._review_tasks()[OCR_REVIEW]
        rows = task.plan()["sections"]["suggestions"]
        task.run_section_action("suggestions", rows)
        # 驳回的是"低置信度"这一条：误框那一条不受影响（粒度＝一个问题）
        self.assertTrue(is_tag_reviewed(blk, "ocr_low_conf"))
        self.assertFalse(is_tag_reviewed(blk, MISREAD_TAG_ID))
        self.assertEqual(task.plan()["sections"]["suggestions"], [])

    def test_canvas_side_record_updates_nav_count(self):
        """画布上记下／处理掉一条，工作台导航计数立刻跟上（交接批次 B）。

        工作台与画布同屏，所以「现场处理完成时正确更新活动计数」不能等切任务
        ——``canvas.content_modified`` 的落点（``mark_content_changed``）顺手
        刷一遍便宜计数。
        """
        from ui.glossary_agent_panel import _TASK_LABELS
        from utils.block_tags import OCR_REVIEW_ID, set_manual_tag

        panel = self._panel()
        panel._refresh_nav_counts()
        self.assertNotIn("(", panel.nav._buttons[OCR_REVIEW].text())  # 0 不缀数
        set_manual_tag(self.proj.pages[PAGE_A][0], OCR_REVIEW_ID, True)
        panel.mark_content_changed()  # canvas.content_modified 的落点
        # 块口径的队列只报数字（标签已带「待校对」，「待审校」是重复说法）
        self.assertEqual(
            panel.nav._buttons[OCR_REVIEW].text(),
            _TASK_LABELS[OCR_REVIEW] + " (1)",
        )
        # 处理完把记录移除 → 数字跟着回落
        set_manual_tag(self.proj.pages[PAGE_A][0], OCR_REVIEW_ID, False)
        panel.mark_content_changed()
        self.assertNotIn("(", panel.nav._buttons[OCR_REVIEW].text())

    def test_content_change_only_refreshes_cheap_counts(self):
        """内容一变只刷纯内存能数的队列：合并组数要跑全书 plan，不在这里重算。"""
        panel = self._panel()
        merge = panel._batch_tasks[MERGE]
        original = merge.pending
        called = []

        def spy(options):
            called.append(1)
            return original(options)

        merge.pending = spy
        panel.mark_content_changed()
        self.assertEqual(called, [])  # 便宜档不碰引擎
        panel._refresh_nav_counts()
        self.assertEqual(called, [1])  # 显式全刷时才跑

    def test_review_page_uses_queue_view(self):
        """待办任务进的是 ReviewQueueView（不是在批量视图上塞待办语义）。"""
        from ui.workbench_review_view import ReviewQueueView

        panel = self._panel()
        panel.nav.select(OCR_REVIEW)
        self.app.processEvents()
        view = panel._review_views[OCR_REVIEW]
        self.assertIsInstance(view, ReviewQueueView)
        self.assertIs(panel.pages.widget(panel._page_index(OCR_REVIEW)), view)

    def test_review_row_opens_the_same_card_directly(self):
        """③⑥ 的第二条出口：不跳过去，直接开同一张框级确认卡（交接 §4.2）。

        两条出口共用同一行寻址；这里只钉「信号带的是页/块/动作 id」与
        「动作 id 就是画布上那两个现场动作」，真正的卡片拉起在画布侧。
        """
        from utils.block_actions import ACTION_REGISTRY

        self._seed_reviews()
        panel = self._panel()
        # 首行分别是「人工记下的校对记录」(PAGE_B 第 0 块) 与「待重译记录」
        # (PAGE_B 第 1 块，第 2 块是旧 ID 那条)
        for task_id, action_id, expected in (
            (OCR_REVIEW, "act_ocr_fix", (PAGE_B, 0)),
            (TRANS_REVIEW, "act_retranslate", (PAGE_B, 1)),
        ):
            panel.nav.select(task_id)
            self.app.processEvents()
            view = panel._review_views[task_id]
            self.assertEqual(view.task.action_id, action_id)
            self.assertIn(action_id, ACTION_REGISTRY)
            self.assertEqual(
                view._sections[0].process_btn.text(),
                ACTION_REGISTRY[action_id].short_label,
            )
            seen = []
            view.action_requested.connect(
                lambda page, idx, aid: seen.append((page, idx, aid))
            )
            table, rows = view.tables()[0]
            table.setCurrentCell(0, 0)
            self.app.processEvents()
            view._sections[0].process_btn.click()
            self.assertEqual(seen, [(*expected, action_id)])

    def test_review_jump_signal_carries_page_and_block(self):
        manual_ocr, _p, _l, _s = self._seed_reviews()
        panel = self._panel()
        panel.nav.select(OCR_REVIEW)
        self.app.processEvents()
        seen = []
        panel.jump_requested.connect(lambda page, idx: seen.append((page, idx)))
        view = panel._review_views[OCR_REVIEW]
        table, rows = view.tables()[0]
        table.setCurrentCell(0, 0)
        self.app.processEvents()
        view._on_jump("manual")
        self.assertEqual(seen, [(PAGE_B, 0)])

    def test_stale_light_lifecycle(self):
        """内容改动 → 已规划的页亮「列表可能过时」；replan 熄灯；未规划的
        页不打扰（没有可过时的列表）；换项目一并作废。"""
        panel = self._panel()
        planned = panel._batch_views[MISREAD]
        unplanned = panel._batch_views[MERGE]
        self.assertTrue(planned._planned)
        self.assertFalse(unplanned._planned)

        panel.mark_content_changed()
        self.assertFalse(planned._stale_label.isHidden())  # 亮
        self.assertTrue(unplanned._stale_label.isHidden())  # 不打扰

        planned.replan()  # 刷新出口：灯灭、列表重建
        self.assertTrue(planned._stale_label.isHidden())
        # 灭灯后再改一次内容 → 再亮（灯不是一次性的）
        panel.mark_content_changed()
        self.assertFalse(planned._stale_label.isHidden())

        # 换项目：旧列表与灯一并作废
        planned.forget_plan()
        self.assertTrue(planned._stale_label.isHidden())
        panel.mark_content_changed()
        self.assertTrue(planned._stale_label.isHidden())


if __name__ == "__main__":
    unittest.main()
