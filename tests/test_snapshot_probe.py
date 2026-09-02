"""快照探针（撤销体系重构计划 4.2 节 0-c）。

终局命令 = 前后全文快照。候选物理格式：项目自有的富文本 HTML 对
（``ui/text_engine/annotations.py::to_rich_text_html`` /
``ui/text_engine/annotations.py::load_rich_text_html``）——保存链路已在用，
天然覆盖 ruby 注解、逐字符格式与行距扩展。

探针回答两个问题：
1. 「快照 → 重放」往返是否保真：纯文本一致 + HTML 幂等 + ruby 容器数一致；
2. 单命令快照的体量与重放耗时（打印实测值，供撤销上限策略参考）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_snapshot_probe.py
"""

import os
import os.path as osp
import sys
import time
import unittest

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtGui import QColor, QTextCursor, QTextDocument  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402


class SnapshotProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._APP = QApplication.instance() or QApplication([])

    def _build_complex_doc(self) -> QTextDocument:
        """构造代表性复杂块：多行文本 + 逐字符格式 + ruby 注解。"""
        from ui.text_engine.annotations import apply_ruby

        doc = QTextDocument()
        doc.setUndoRedoEnabled(False)
        cursor = QTextCursor(doc)
        cursor.insertText("振り仮名付きの文章\n二行目のテキスト")
        # 逐字符格式：加粗 + 字号 + 颜色 span
        cursor.setPosition(0)
        cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        cfmt = cursor.charFormat()
        cfmt.setFontWeight(700)
        cfmt.setFontPointSize(24.0)
        cfmt.setForeground(QColor("#c00000"))
        cursor.mergeCharFormat(cfmt)
        # ruby 注解（覆盖前两字）
        cursor.setPosition(0)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        apply_ruby(cursor, "group", "ふり")
        return doc

    def test_html_snapshot_roundtrip_fidelity(self):
        from ui.text_engine.annotations import (
            load_rich_text_html,
            ruby_containers,
            to_rich_text_html,
        )

        doc = self._build_complex_doc()

        t0 = time.perf_counter()
        html1 = to_rich_text_html(doc)
        serialize_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        doc2 = QTextDocument()
        load_rich_text_html(doc2, html1)
        replay_ms = (time.perf_counter() - t0) * 1000

        html2 = to_rich_text_html(doc2)
        size_bytes = len(html1.encode("utf-8"))
        print(
            f"\n[probe] 快照体量 {size_bytes} B | "
            f"序列化 {serialize_ms:.2f} ms | 重放 {replay_ms:.2f} ms"
        )

        # ① 纯文本一致
        self.assertEqual(doc2.toPlainText(), doc.toPlainText())
        # ② HTML 幂等（第二次往返不动——快照可嵌套重放）
        self.assertEqual(html1, html2)
        # ③ ruby 容器数一致
        self.assertEqual(
            len(ruby_containers(doc2)), len(ruby_containers(doc))
        )
        self.assertGreater(len(ruby_containers(doc)), 0)

    def test_snapshot_cost_at_scale(self):
        """典型体量（约 200 字符块）× 100 命令的成本外推。"""
        from ui.text_engine.annotations import (
            load_rich_text_html,
            to_rich_text_html,
        )

        doc = QTextDocument()
        doc.setUndoRedoEnabled(False)
        cursor = QTextCursor(doc)
        cursor.insertText("これは典型的な翻訳テキストです。" * 12)

        t0 = time.perf_counter()
        html = to_rich_text_html(doc)
        serialize_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        for _ in range(10):
            d = QTextDocument()
            load_rich_text_html(d, html)
        replay_ms = (time.perf_counter() - t0) * 100

        size_bytes = len(html.encode("utf-8"))
        print(
            f"\n[probe] ~200 字符块: 快照 {size_bytes} B | "
            f"序列化 {serialize_ms:.2f} ms | 重放 {replay_ms:.2f} ms/次 | "
            f"100 命令约 {size_bytes * 100 / 1024:.0f} KiB"
        )
        # 宽松 sanity 上限：重放不得成为交互瓶颈
        self.assertLess(replay_ms, 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
