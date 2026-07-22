"""縦中横（竖内横排）功能渲染对照测试（一次性验证脚本，验完即删）。

不依赖 app 启动，直接构造 VerticalTextDocumentLayout + QTextDocument 渲染到 PNG。
对每个测试文本分别用 threshold=0（关闭/逐字旋转）和 threshold=3（开启）各渲染一张，
输出 PNG 到临时目录，并打印关键字内部数据（line 数、naturalTextWidth、draw_shifted、
layout_left、shrink_width）供对照断言。
"""
# ruff: noqa
import os
import sys
import tempfile

# 让脚本能 import 项目内模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import QApplication

from ui.scene_textlayout import VerticalTextDocumentLayout
from utils.fontformat import FontFormat

THRESHOLD_ON = 3
THRESHOLD_OFF = 0

# 测试样本：(标签, 文本, 是否大字号, 说明)
SAMPLES = [
    ("n", "あいうえお", False, "纯竖排无 run：开关都不应有回归"),
    ("tch", "あABい", False, "短 run：off=逐字旋转4字位 / on=合并横排3字位"),
    ("tch_long", "あABCDEFい", False, "超阈值长 run (len=6 > 3)：on 仍应回退逐字旋转"),
    ("multi", "第1話2Aい", False, "多 run 块（数字1/数字2/字母A 三个 run）：验证 Bug1 是否非末尾 run 仍侧倒"),
    ("big", "あABABABいCDE", True, "大字号多 run：验证横向溢出块左扩（Bug2 遗留项4）"),
]


def make_doc(text: str, fmt: FontFormat, doc_margin: int = 0) -> QTextDocument:
    doc = QTextDocument()
    doc.setDocumentMargin(doc_margin)
    font = QFont(fmt.font_family, int(fmt.font_size))
    font.setBold(fmt.bold)
    doc.setDefaultFont(font)
    # 用 charformat 确保字号逐 fragment 落地
    cursor = QTextCursor(doc)
    cf = QTextCharFormat()
    cf.setFont(font)
    cf.setFontPointSize(fmt.size_pt)
    cursor.insertText(text, cf)
    return doc


def render(text: str, threshold: int, fmt: FontFormat) -> tuple:
    """返回 (pixmap, info_dict)。不传 rect 给 drawContents，让文档自 (0,0) 画起。
    available_height 按 ~3 字高设，强制多列；off 时逐字旋转每字一条 line 占一列位，
    on 时 run 合并横排省字位 → 列数/line 数差异在图上可见。"""
    doc = make_doc(text, fmt)
    layout = VerticalTextDocumentLayout(
        doc, fmt, punctuation_position=0, tatechuyoko_threshold=threshold
    )
    layout.need_ideal_width = True
    # 手调：24pt 下 avail_h=70 强制双列、能体现 on/off 列差异
    avail_h = 70 if fmt.font_size < 40 else 140
    avail_w = 400
    layout.setMaxSize(avail_w, avail_h, relayout=False)
    layout.reLayoutEverything()
    layout.reLayout()
    # 必须显式把自定义 layout 挂到 doc，否则 drawContents 用 doc 默认横向 layout
    # （textitem.py:238/:627 同样显式 setDocumentLayout）。
    doc.setDocumentLayout(layout)
    # drawContents 会触发 documentChanged→reLayoutEverything，把手动布局压回默认，
    # 必须在绘制前冻结（参考 textitem.paint_stroke 的 relayout_on_changed=False）。
    layout.relayout_on_changed = False

    tl = doc.firstBlock().layout()
    lines = [
        f"l{i}:tl={tl.lineAt(i).textLength()} x={int(tl.lineAt(i).x())} y={int(tl.lineAt(i).y())} ntw={tl.lineAt(i).naturalTextWidth():.0f}"
        for i in range(tl.lineCount())
    ]
    info = {
        "lineCount": tl.lineCount(),
        "avail_h": avail_h,
        "draw_shifted": round(layout.draw_shifted, 1),
        "layout_left": round(layout.layout_left, 1),
        "shrink_width": round(layout.shrink_width, 1),
        "lines": lines,
        "pixmap_size": [int(layout.max_width + 40), int(layout.max_height + 40)],
    }

    pw = int(layout.max_width + 40)
    ph = int(layout.max_height + 40)
    pix = QPixmap(pw, ph)
    pix.fill(QColor(255, 255, 255))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0), 1))
    doc.drawContents(p)
    p.end()
    return pix, info


def main():
    app = QApplication(sys.argv)
    out_dir = tempfile.mkdtemp(prefix="tatechuyoko_test_")
    print(f"OUTPUT_DIR={out_dir}")
    for tag, text, big, desc in SAMPLES:
        fmt = FontFormat(
            font_size=(48 if big else 24),
            vertical=True,
            frgb=[0, 0, 0],
            stroke_width=0.0,
        )
        for thr, label in [(THRESHOLD_OFF, "off"), (THRESHOLD_ON, "on")]:
            pix, info = render(text, thr, fmt)
            fn = os.path.join(out_dir, f"{tag}_{label}.png")
            pix.save(fn)
            print(f"[{tag}_{label}] {desc}")
            print(f"  text={text!r} threshold={thr}")
            print(f"  info={info}")
            print(f"  -> {fn}")
    print("\nDONE. PNG 全部生成在上述 OUTPUT_DIR。")


if __name__ == "__main__":
    main()