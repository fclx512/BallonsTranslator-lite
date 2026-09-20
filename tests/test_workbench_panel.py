"""泛用工作台（批次 D）回归：任务适配层 + 面板接线。

覆盖规划 D20（三段式：任务导航 → 候选列表 → 执行）、D23／D11（审批预览
100% 原比例）、D26（跳转信号）、D27（批量告知弹窗）、D28（驳回可逆、与
勾选两轴正交）、D33d（误聚组默认不勾选）、D35（整批一条撤回）、D37
（跳步提示可禁用）、D5（扩张量无默认值）。

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
    EXPAND,
    GLOSSARY,
    MERGE,
    MISREAD,
    SIMPLE_INPAINT,
    STORY,
    ExpandTask,
    build_batch_tasks,
)
from utils.block_tags import (  # noqa: E402
    MISREAD_TAG_ID,
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
        self._warn = pcfg.workbench_warn_skip_order
        pcfg.workbench_warn_skip_order = False
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
        pcfg.workbench_warn_skip_order = self._warn
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
        self.assertIn("1 in the queue, 0 rejected, 1 to delete", plan["summary"])
        self.assertEqual(row.cells[2], "Numeric only")  # 子类名来自标签体系
        self.assertEqual(task.pending({}), 1)

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

    def test_expand_needs_an_explicit_amount(self):
        task = self._tasks()[EXPAND]
        plan = task.plan({})
        self.assertEqual(plan["rows"], [])
        self.assertIsNone(task.pending({}))
        plan = task.plan({"amount": 6, "mode": "px"})
        self.assertTrue(plan["rows"])
        row = plan["rows"][0]
        self.assertEqual(row.payload["old"], [10, 10, 60, 40])
        # 下边被下一框（y1=42）截住 → "碰到邻框即停"
        self.assertEqual(row.payload["new"], [4, 4, 66, 42])
        # 行内第三列是「增长描述」（页名 / 索引 / 增长），只讲每边长多少、哪边受限，
        # 不铺坐标数字（2026-09-20 修：原断言写的是铺坐标的旧形态）。
        self.assertEqual(len(row.cells), 3)
        self.assertIn("+6 px", row.cells[2])
        self.assertIn(ExpandTask._SIDE_LABELS["bottom"], row.cells[2])

    def test_expand_ratio_mode_takes_percent(self):
        task = self._tasks()[EXPAND]
        # 短边 30 的 20% ＝ 6px，与 px 模式给 6 等价
        by_px = task.plan({"amount": 6, "mode": "px"})
        by_ratio = task.plan({"amount": 20, "mode": "ratio"})
        self.assertEqual(
            [r.payload["new"] for r in by_px["rows"]],
            [r.payload["new"] for r in by_ratio["rows"]],
        )

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
        expand = tasks[EXPAND].plan({"amount": 6, "mode": "px"})["rows"]
        html = tasks[EXPAND].confirm_html(expand, {"amount": 6, "mode": "px"})
        self.assertIn("rendering rectangle", html)


# ── 面板接线 ──────────────────────────────────────────────────────


class PanelTest(_WorkbenchTestCase):
    def test_nav_order_and_default_task(self):
        panel = self._panel()
        self.assertEqual(panel.pages.count(), 6)
        self.assertEqual(panel.current_task(), MISREAD)
        self.assertTrue(panel.nav._buttons[MISREAD].isChecked())
        # D16／§3：导航顺序＝用户工作流顺序
        from ui.glossary_agent_panel import WORKBENCH_ORDER

        self.assertEqual(WORKBENCH_ORDER[:4], CLEANUP_TASK_IDS)

    def test_switching_plans_the_task_once(self):
        panel = self._panel()
        view = panel._batch_views[MERGE]
        self.assertEqual(view._rows, [])  # 还没切过去，没规划
        panel.nav.select(MERGE)
        self.app.processEvents()
        self.assertTrue(view._rows)
        self.assertNotIn(MERGE, panel._dirty_tasks)

    def test_expand_amount_starts_from_setting_and_zero_disables(self):
        """扩张量初值取设置里的默认值（D5／C3 定值）；清零即无候选、执行禁用。"""
        panel = self._panel()
        panel.nav.select(EXPAND)
        self.app.processEvents()
        view = panel._batch_views[EXPAND]
        spec = [s for s in view.task.options_spec() if s["key"] == "amount"][0]
        self.assertEqual(spec["value"], int(pcfg.workbench_expand_px))
        self.assertTrue(view._rows)
        self.assertTrue(view._execute_btn.isEnabled())
        view._option_widgets["amount"].setValue(0)
        view.replan()
        self.assertFalse(view._rows)
        self.assertFalse(view._execute_btn.isEnabled())

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

    def test_replan_updates_nav_count(self):
        """参数一变候选就变，导航上的「还有 N 个未处理」不能停在旧值。"""
        panel = self._panel()
        view = panel._batch_views[EXPAND]
        view._option_widgets["amount"].setValue(0)
        view.replan()
        self.app.processEvents()
        self.assertFalse(view._rows)
        self.assertNotIn("(", panel.nav._buttons[EXPAND].text())  # 0 不缀数
        view._option_widgets["amount"].setValue(20)
        view.replan()
        self.app.processEvents()
        self.assertTrue(view._rows)
        self.assertIn(str(len(view._rows)), panel.nav._buttons[EXPAND].text())

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

    def test_earlier_pending_counts_cleanup_steps_only(self):
        """D37 的计数口径：只有前四项「问题清理」参与，且带标签名。"""
        from ui.glossary_agent_panel import _TASK_LABELS

        panel = self._panel()
        panel.nav.select(MERGE)
        pending = panel.earlier_pending(SIMPLE_INPAINT)
        # 三项都在（扩张量有设置里给的初值 → 有可执行候选），顺序即 D16 的导航序
        self.assertEqual(
            [label for label, _ in pending],
            [
                _TASK_LABELS[MISREAD],
                _TASK_LABELS[MERGE],
                _TASK_LABELS[EXPAND],
            ],
        )
        self.assertTrue(all(count > 0 for _, count in pending))
        # 目标之后的步骤不计（术语／剧情也不参与）
        self.assertEqual(panel.earlier_pending(STORY), pending)
        self.assertEqual(panel.earlier_pending(MISREAD), [])

    def test_skip_order_prompt_blocks_on_cancel(self):
        """D37：跳步提示取消即停在原任务（顺序是提示，不是门禁）。"""
        from ui.glossary_agent_panel import QMessageBox

        panel = self._panel()
        pcfg.workbench_warn_skip_order = True
        original = QMessageBox.exec
        shown = []

        def _cancel(self):
            shown.append(True)
            return QMessageBox.StandardButton.Cancel

        QMessageBox.exec = _cancel
        try:
            panel.nav.select(MERGE)
        finally:
            QMessageBox.exec = original
        self.assertTrue(shown)
        self.assertEqual(panel.current_task(), MISREAD)
        self.assertTrue(panel.nav._buttons[MISREAD].isChecked())
        self.assertFalse(panel.nav._buttons[MERGE].isChecked())

    def test_skip_order_prompt_dont_ask_writes_config(self):
        """勾「不再提示」→ 写 pcfg.workbench_warn_skip_order=False（并落盘）。"""
        from ui import glossary_agent_panel as gap
        from utils import config as config_module

        panel = self._panel()
        pcfg.workbench_warn_skip_order = True
        saved = []
        original_exec = gap.QMessageBox.exec
        original_box = gap.QCheckBox
        original_save = config_module.save_config

        gap.QMessageBox.exec = lambda self: gap.QMessageBox.StandardButton.Ok

        class _AlwaysCheckedBox(original_box):
            def isChecked(self):
                return True

        gap.QCheckBox = _AlwaysCheckedBox
        config_module.save_config = lambda *a, **k: saved.append(True)
        try:
            panel.nav.select(MERGE)
        finally:
            gap.QMessageBox.exec = original_exec
            gap.QCheckBox = original_box
            config_module.save_config = original_save
        self.assertFalse(pcfg.workbench_warn_skip_order)
        self.assertTrue(saved)
        # 用户确认后即切过去（提示只是提示，不门禁）
        self.assertEqual(panel.current_task(), MERGE)

    def test_skip_order_silent_when_nothing_pending(self):
        panel = self._panel()
        pcfg.workbench_warn_skip_order = True
        # 先清空队列（未驳回的全部驳回）→ 前序步骤没有未处理项 → 不弹窗
        from utils.block_tags import set_tags_reviewed

        set_tags_reviewed(self.proj.pages[PAGE_A][1], True)
        panel.nav.select(MERGE)
        self.assertEqual(panel.current_task(), MERGE)

    def test_chat_is_gone_but_log_survives(self):
        """D19：砍 Chat；worker 日志改落底部日志条。"""
        panel = self._panel()
        self.assertFalse(hasattr(panel, "_chat_area"))
        self.assertFalse(hasattr(panel, "input_edit"))
        panel._append_log("hello from worker")
        self.assertIn("hello from worker", panel._log_view.toPlainText())

    def test_prepare_emits_instruction_without_bubble(self):
        """D19：一键准备不再经 Chat 气泡链路，指令直接入队。"""
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

    # ── 两级导航（UI 优化 D41）──────────────────────────────────────

    def _visible_chips(self, panel):
        return [
            task_id
            for task_id, chip in panel.nav._buttons.items()
            if chip.isVisible()
        ]

    def test_nav_is_two_level_categories(self):
        """一级＝管线阶段大类，二级＝该大类下的任务；切大类即换 chip 行。"""
        from ui.glossary_agent_panel import (
            CATEGORY_INPAINT,
            CATEGORY_TEXT,
            CATEGORY_TRANSLATE,
        )

        panel = self._panel()
        self.assertEqual(
            list(panel.nav._category_buttons),
            [CATEGORY_TEXT, CATEGORY_INPAINT, CATEGORY_TRANSLATE],
        )
        # 开局停在第一个大类，只露出它的三个任务
        self.assertTrue(panel.nav._category_buttons[CATEGORY_TEXT].isChecked())
        self.assertFalse(panel.nav._category_buttons[CATEGORY_TRANSLATE].isChecked())
        self.assertEqual(self._visible_chips(panel), [MISREAD, MERGE, EXPAND])
        # 切到别的大类：页签高亮跟着走，chip 行只剩该大类的
        panel.nav.select(GLOSSARY)
        self.assertTrue(panel.nav._category_buttons[CATEGORY_TRANSLATE].isChecked())
        self.assertFalse(panel.nav._category_buttons[CATEGORY_TEXT].isChecked())
        self.assertEqual(self._visible_chips(panel), [GLOSSARY, STORY])
        self.assertFalse(panel.nav._buttons[MISREAD].isVisible())

    def test_category_tab_repeats_its_tasks_pending_count(self):
        """一级页签缀本大类未处理之和——切到大类外也看得见还有活（D37）。"""
        from ui.glossary_agent_panel import (
            CATEGORY_TEXT,
            _CATEGORY_LABELS,
            _TASK_LABELS,
            _with_count,
        )

        panel = self._panel()
        panel._refresh_nav_counts()
        expected = 0
        for task_id in (MISREAD, MERGE, EXPAND):
            task = panel._batch_tasks[task_id]
            view = panel._batch_views[task_id]
            expected += task.pending(view.options())
            # 二级 chip 缀自己的计数
            self.assertEqual(
                panel.nav._buttons[task_id].text(),
                _with_count(_TASK_LABELS[task_id], task.pending(view.options())),
            )
        self.assertGreater(expected, 0)
        self.assertEqual(
            panel.nav._category_buttons[CATEGORY_TEXT].text(),
            _with_count(_CATEGORY_LABELS[CATEGORY_TEXT], expected),
        )

    def test_category_switch_returns_to_last_task_of_that_category(self):
        """切回大类＝回到上次在该大类里的任务，不重置成第一个。"""
        from ui.glossary_agent_panel import CATEGORY_TEXT

        panel = self._panel()
        panel.nav.select(MERGE)
        panel.nav.select(GLOSSARY)
        panel.nav._category_buttons[CATEGORY_TEXT].click()
        self.assertEqual(panel.current_task(), MERGE)
        self.assertTrue(panel.nav._buttons[MERGE].isChecked())

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
        self.assertIn(
            _TASK_LABELS[MISREAD] + " deleted 2 blocks",
            panel._log_view.toPlainText().replace("\xa0", " "),
        )
        # worker 的行（无任务归属）不缀前缀
        panel._append_log("Draft loaded.", "")
        self.assertIn("Draft loaded.", panel._log_view.toPlainText())


if __name__ == "__main__":
    unittest.main()
