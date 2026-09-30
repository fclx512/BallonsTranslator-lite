"""数据层 fontformat 与画布渲染态对齐分叉的回归钉。

数据层 ``blk.fontformat.alignment`` 与渲染态（QTextDocument）只在
``TextBlkItem.set_fontformat`` 被调用时同步，单侧写入即分叉——用户实测
症状：参数面板显示居中、画布渲染靠右；样式管理器批量修改因空 diff 门
跳过重建，只能「先改成别的再改回来」。探针（``scripts/mw_repro.py
--scenario fmt-sync``）钉出两类根源，本测试钉住对应修复契约：

  1. 旧工程 rich_text 段落自带 align 属性 → 块级 blockFormat 对齐脱离
     数据层，且 set_fontformat 只写 doc 默认 option 治不了。修复 =
     ``TextBlkItem.load_rich_text_html`` 加载后归一
     （``_strip_paragraph_alignment``）：本工具自产 HTML 无 align，
     修复必须是 no-op（检查 3）。
  2. 数据侧直写（管线收尾同款）→ 渲染陈旧；set_fontformat 整包重应用
     可治愈（检查 4）。管线收尾另有整页重建兜底。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_format_sync.py -v
    # or directly:
    ./ballontrans_pylibs_win/python.exe tests/test_format_sync.py
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def make_item(scene, alignment, rich_html):
    from ui.textitem import TextBlkItem
    from utils.textblock import TextBlock

    blk = TextBlock(xyxy=[50, 50, 400, 150])
    blk._bounding_rect = [50, 50, 400, 150]
    blk.fontformat.alignment = alignment
    blk.rich_text = rich_html
    item = TextBlkItem(blk=blk, idx=0)
    item.setPos(50, 50)
    scene.addItem(item)
    return item, blk


def run_all_checks():
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QApplication, QGraphicsScene

    from utils.fontformat import TextAlignment

    RESULTS.clear()
    app = QApplication.instance() or QApplication(sys.argv[:1])

    align_left = int(Qt.AlignmentFlag.AlignLeft)
    align_center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

    def block_align(item):
        return int(item.document().firstBlock().blockFormat().alignment())

    def default_align(item):
        return int(item.document().defaultTextOption().alignment())

    def layout_align(item):
        # 文档创建时 initTextBlock 已整版排版；再强制一次确保读的是排后值
        item.document().documentLayout().documentSize()
        return int(
            item.document().firstBlock().layout().textOption().alignment()
        )

    # ── 1. 旧工程 HTML 段落 align=right + 数据层居中 → 渲染必须跟随数据层 ──
    scene = QGraphicsScene()
    item, blk = make_item(
        scene, TextAlignment.Center,
        '<p align="right">旧工程段落文本</p>',
    )
    check(
        "html align normalized to data layer",
        block_align(item) == align_left
        and layout_align(item) == align_center
        and default_align(item) == align_center
        and blk.fontformat.alignment == TextAlignment.Center,
        "(blockFormat=%d doc默认=%d layout=%d)"
        % (block_align(item), default_align(item), layout_align(item)),
    )

    # ── 2. CSS text-align 同型（上游/旧版另一种写法） ────────────────────
    scene2 = QGraphicsScene()
    item2, _ = make_item(
        scene2, TextAlignment.Center,
        '<p style="text-align:right">旧工程段落文本</p>',
    )
    check(
        "css text-align normalized to data layer",
        block_align(item2) == align_left and layout_align(item2) == align_center,
        "(blockFormat=%d layout=%d)" % (block_align(item2), layout_align(item2)),
    )

    # ── 3. 本工具自产 HTML（无 align）必须零影响 ────────────────────────
    scene3 = QGraphicsScene()
    item3, _ = make_item(
        scene3, TextAlignment.Center,
        '<p style="color:#222">普通段落文本</p>',
    )
    check(
        "own html without align is a no-op",
        default_align(item3) == align_center and layout_align(item3) == align_center,
        "(doc默认=%d layout=%d)" % (default_align(item3), layout_align(item3)),
    )

    # ── 4. 数据侧直写后 set_fontformat 整包重应用可治愈 ──────────────────
    from utils.textblock import TextAlignment as _TA

    blk.alignment = _TA.Right  # 模拟管线收尾的单侧直写
    check(
        "raw data write diverges render (pre-condition)",
        default_align(item) == align_center,
        "(doc默认=%d)" % default_align(item),
    )
    item.set_fontformat(blk.fontformat, set_char_format=True)
    check(
        "set_fontformat heals after raw data write",
        default_align(item) == int(Qt.AlignmentFlag.AlignRight)
        and layout_align(item) == int(Qt.AlignmentFlag.AlignRight)
        and blk.fontformat.alignment == _TA.Right,
        "(doc默认=%d layout=%d)" % (default_align(item), layout_align(item)),
    )

    return RESULTS


class TestFormatSync(unittest.TestCase):
    def test_sync_contract(self):
        results = run_all_checks()
        failed = [name for name, ok in results if not ok]
        self.assertEqual(
            failed, [], "format-sync checks failed: %s" % ", ".join(failed)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
