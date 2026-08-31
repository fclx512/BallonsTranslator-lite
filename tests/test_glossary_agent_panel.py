"""GlossaryAgentPanel offscreen 回归测试(阶段 2)。

覆盖:worker 基底载入(术语表 + 剧情数据)/UI 镜像同步/预填充/用户编辑
(user-owned)/「应用」落盘(术语表 JSON + 项目内存结构)/旧入口已删
(mainwindow 不再引用 extractor)。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_glossary_agent_panel.py
"""

import json
import os
import os.path as osp
import sys
import tempfile
import types
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtWidgets import QApplication  # noqa: E402


def _make_proj():
    return types.SimpleNamespace(
        pages={"p01": [], "p02": []},
        idx2pagename=lambda i: ["p01", "p02"][i],
        _image_info={
            "p01": {"width": 800, "height": 1200, "llm_visual_summary": "old"},
            "p02": {"width": 800, "height": 1200},
        },
    )


class GlossaryAgentPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from ui.glossary_agent_panel import GlossaryAgentPanel, GlossaryAgentWorker

        self.proj = _make_proj()
        panel = GlossaryAgentPanel(self.proj)
        # 直连 worker(不启 QThread):避免 started→initialize 异步竞态;
        # 线程生命周期由 test_context_agent_session 的逻辑测试与实机覆盖
        worker = GlossaryAgentWorker(self.proj)
        panel._worker = worker
        panel._wire_worker(worker)
        return panel, worker

    def test_initialize_loads_base(self):
        panel, worker = self._panel()
        worker.initialize()
        # 剧情基底来自 _image_info("old" 摘要);术语基底当前路径为空
        self.assertEqual(len(worker.story.page_summaries), 1)
        self.assertEqual(
            worker.story.page_summaries["p01"].summary, "old"
        )
        # UI 镜像已同步
        self.assertEqual(panel.story_table.rowCount(), 1)

    def test_mirror_sync_and_user_edit(self):
        panel, worker = self._panel()
        worker.initialize()
        worker.glossary.apply_patch([{"src": "勇者", "dst": "Hero"}])
        worker._sync_all()
        self.assertEqual(panel.glossary_table.rowCount(), 1)
        self.assertEqual(panel.glossary_table.item(0, 0).text(), "勇者")
        self.assertEqual(panel.glossary_table.item(0, 3).text(), "AI")
        # 用户编辑 → origin 升级 user,镜像同步
        worker.user_glossary_edit("勇者", "Brave", "note")
        self.assertEqual(worker.glossary.entries[0].translation, "Brave")
        row = panel.glossary_table.item(0, 0)
        self.assertEqual(panel.glossary_table.item(0, 3).text(), "you")
        self.assertIsNotNone(row)

    def test_story_table_sync(self):
        panel, worker = self._panel()
        worker.initialize()
        pages, synopsis = worker.story.snapshot()
        self.assertEqual(panel.story_table.rowCount(), 1)
        self.assertEqual(panel.story_table.item(0, 0).text(), "p01")
        self.assertEqual(panel.synopsis_edit.toPlainText(), "")

    def test_apply_glossary_writes_file(self):
        panel, worker = self._panel()
        worker.initialize()
        worker.glossary.set_user_entry("魔王", "Demon King", "final boss")
        with tempfile.TemporaryDirectory() as tmp:
            path = osp.join(tmp, "glossary.json")
            worker.apply_glossary(path)
            with open(path, encoding="utf-8") as f:
                rows = json.loads(f.read())
        self.assertEqual(rows, [{"src": "魔王", "dst": "Demon King",
                                 "info": "final boss"}])

    def test_apply_story_writes_project(self):
        from modules.context_agent.story import PAGE_SUMMARY_KEY, SYNOPSIS_KEY

        panel, worker = self._panel()
        worker.initialize()
        worker.user_summary_edit("p01", "rewritten")
        worker.user_summary_edit("p02", "new")
        worker.apply_story()
        self.assertEqual(self.proj._image_info["p01"][PAGE_SUMMARY_KEY],
                         "rewritten")
        self.assertEqual(self.proj._image_info["p02"][PAGE_SUMMARY_KEY], "new")

    def test_prefill_merges_rows(self):
        panel, worker = self._panel()
        worker.initialize()
        worker.prefill_from_frequency()
        # 无译文项目:预填充为空,不报错
        self.assertEqual(len(worker.glossary.entries), 0)

    def test_old_entry_removed(self):
        # 旧入口彻底退役:标题栏信号/对话框引用不复存在(纯源码检查,
        # 避免 offscreen 导入完整主窗口)
        mw_src = open(osp.join(APP_ROOT, "ui", "mainwindow.py"),
                      encoding="utf-8").read()
        self.assertNotIn("glossary_extract", mw_src)
        self.assertNotIn("GlossaryExtractorDialog", mw_src)
        bars_src = open(osp.join(APP_ROOT, "ui", "mainwindowbars.py"),
                        encoding="utf-8").read()
        self.assertNotIn("glossary_extract", bars_src)
        self.assertNotIn("Extract Glossary", bars_src)


if __name__ == "__main__":
    unittest.main()
