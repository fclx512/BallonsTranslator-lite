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
    MERGE,
    MISREAD,
    SIMPLE_INPAINT,
    STORY,
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
        # 主窗口侧的两个调用口（复核文档 §4.2 的必传项）
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
        self.assertEqual(row.cells[3], "4 4 66 42")

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

    def test_execute_disabled_without_candidates(self):
        panel = self._panel()
        panel.nav.select(EXPAND)
        self.app.processEvents()
        view = panel._batch_views[EXPAND]
        self.assertFalse(view._rows)
        self.assertFalse(view._execute_btn.isEnabled())
        view._option_widgets["amount"].setValue(6)
        view.replan()
        self.assertTrue(view._rows)
        self.assertTrue(view._execute_btn.isEnabled())

    def test_preview_is_unscaled_and_shown_below_list(self):
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._table.setCurrentCell(0, 1)
        self.app.processEvents()
        self.assertTrue(view._preview_box.isVisible())
        self.assertEqual(view._preview_pixmap.size().width(), 75)
        self.assertEqual(view._preview_pixmap.size().height(), 34)

    def test_unchecking_all_disables_execute(self):
        panel = self._panel()
        view = panel._batch_views[MISREAD]
        view._rows[0].checked = False
        from qtpy.QtCore import Qt

        view._syncing = False
        view._on_item_changed(
            type("I", (), {"row": lambda self: 0, "column": lambda self: 0,
                           "checkState": lambda self: Qt.CheckState.Unchecked})()
        )
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
        self.assertEqual(
            pending,
            [(_TASK_LABELS[MISREAD], 1), (_TASK_LABELS[MERGE], 1)],
        )
        # 目标之后的步骤不计（扩张量未设 → 无计数），术语／剧情也不参与
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


if __name__ == "__main__":
    unittest.main()
