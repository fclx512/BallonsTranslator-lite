"""Headless diagnostic for the right-panel edit -> export render sync chain.

The right-panel (TransTextEdit) and the canvas text item each hold their own
QTextDocument. They used to be synchronized by a focus-gated positional delta
replay (``propagate_user_edit``): the panel recorded the edit position only
while focused, so any panel change made without focus was silently skipped and
the item document went stale — the next save persisted the stale text into
both the project JSON and the exported result image.

They are now reconciled by ``sync_text_by_diff``: the diff and the insertion
points are recomputed from the current full text of both documents on every
panel change, so there is no cross-document position drift, no focus gate,
and a missed sync self-corrects on the next change. This probe verifies:

  1. a plain document edit reaches QGraphicsView.render() immediately
     (no stale pixmap feeding the export);
  2. a focused panel edit converges into the item;
  3. an UNFOCUSED panel change also converges (the original bug case);
  4. the diff preserves char formats outside the edited range;
  5. an item inline edit converges back into the panel;
  6. rapid mixed edits keep both documents in lockstep (no loop).

Run:
    ./ballontrans_pylibs_win/python.exe scripts/render_sync_probe.py

Exit code 0 = the sync chain behaves as documented (regression guard);
exit code 1 = an unexpected failure.
"""

import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont, QImage, QPainter, QTextCursor
from qtpy.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

from utils.fontformat import TextAlignment
from utils.textblock import TextBlock

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def make_item(scene, text):
    from ui.textitem import TextBlkItem

    blk = TextBlock(xyxy=[50, 50, 400, 150], translation=text)
    blk._bounding_rect = [50, 50, 400, 150]
    blk.fontformat.alignment = TextAlignment(1)
    item = TextBlkItem(blk=blk, idx=0)
    item.setPos(50, 50)
    scene.addItem(item)
    return item


def render(view, w=480, h=240):
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    view.render(p)
    p.end()
    return img


def diff_count(a, b):
    import numpy as np

    ba = bytes(a.constBits().asarray(a.sizeInBytes()))
    bb = bytes(b.constBits().asarray(b.sizeInBytes()))
    na = np.frombuffer(ba, dtype=np.uint8)
    nb = np.frombuffer(bb, dtype=np.uint8)
    return int((na != nb).sum())


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])

    from ui.textedit_area import TransTextEdit
    from ui.textedit_commands import sync_text_by_diff

    # ── 1. render freshness: doc edit -> immediate view.render() ─────────
    scene = QGraphicsScene()
    view = QGraphicsView(scene)
    view.resize(480, 240)
    item = make_item(scene, "AB长译文C")
    before = render(view)
    cursor = item.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText("")
    after = render(view)
    check(
        "render freshness",
        diff_count(before, after) > 0,
        "(%d px changed; item text now %r)" % (diff_count(before, after), item.toPlainText()),
    )

    # ── panel <-> item pair wired like scenetext_manager handlers ────────
    scene2 = QGraphicsScene()
    item2 = make_item(scene2, "AB译文C")
    panel = TransTextEdit(idx=0, parent=None)
    panel.setPlainText("AB译文C")
    panel.show()

    def sync_from_panel(joint_previous):
        sync_text_by_diff(item2, panel.toPlainText(), joint_previous)

    panel.propagate_user_edited.connect(sync_from_panel)

    # ── 2. focused panel edit converges into the item ────────────────────
    panel.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    cur = panel.textCursor()
    cur.setPosition(2)
    cur.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
    cur.removeSelectedText()
    app.processEvents()
    check(
        "focused panel delete converges item",
        panel.toPlainText() == item2.toPlainText() == "AB文C",
        "(panel=%r item=%r)" % (panel.toPlainText(), item2.toPlainText()),
    )

    # ── 3. UNFOCUSED panel change converges (old bug case) ───────────────
    panel.clearFocus()
    app.processEvents()
    panel.setPlainText("AB文C此行后面又被程序改写")
    app.processEvents()
    check(
        "unfocused panel change converges item",
        item2.toPlainText() == panel.toPlainText(),
        "(panel=%r item=%r)" % (panel.toPlainText(), item2.toPlainText()),
    )

    # ── 4. diff preserves char formats outside the edited range ──────────
    item2.setPlainText("AB译文C")
    fmt = cursor.charFormat()
    fmt.setFontWeight(QFont.Weight.Bold)
    bold_cursor = QTextCursor(item2.document())
    bold_cursor.setPosition(0)
    bold_cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    bold_cursor.setCharFormat(fmt)
    # 光标移到文末键入（焦点在面板；格式区“AB”在改动区外）
    panel.setPlainText("AB译文C")
    cur = panel.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    panel.setTextCursor(cur)
    panel.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    cur.insertText("尾")
    app.processEvents()
    first_char_fmt = QTextCursor(item2.document())
    first_char_fmt.setPosition(0)
    first_char_fmt.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    still_bold = first_char_fmt.charFormat().fontWeight() == QFont.Weight.Bold
    check(
        "diff keeps out-of-range char format",
        still_bold and item2.toPlainText() == "AB译文C尾",
        "(bold=%s item=%r)" % (still_bold, item2.toPlainText()),
    )

    # ── 5. item inline edit converges back into the panel ────────────────
    panel.setPlainText("AB译文C尾")
    item2.setPlainText("AB译文C尾")
    sync_text_by_diff(panel, item2.toPlainText())
    inline_cursor = item2.textCursor()
    inline_cursor.setPosition(0)
    inline_cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
    inline_cursor.insertText("")
    sync_text_by_diff(panel, item2.toPlainText())
    check(
        "item inline edit converges panel",
        panel.toPlainText() == item2.toPlainText() == "B译文C尾",
        "(panel=%r item=%r)" % (panel.toPlainText(), item2.toPlainText()),
    )

    # ── 6. rapid mixed edits stay in lockstep (panel->item and back) ─────
    panel.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    texts = ["第一行", "第一行\n第二行", "第一行第二行", "第X行第二行", "第X行第二行Y"]
    for t in texts:
        panel.setPlainText(t)
        app.processEvents()
        sync_text_by_diff(item2, panel.toPlainText())
        ok_texts = panel.toPlainText() == item2.toPlainText() == t
    check(
        "rapid mixed edits stay in lockstep",
        ok_texts,
        "(panel=%r item=%r)" % (panel.toPlainText(), item2.toPlainText()),
    )

    # ── 7. continuous typing keeps undo-step parity (lockstep undo) ──────
    scene3 = QGraphicsScene()
    item3 = make_item(scene3, "起")
    panel3 = TransTextEdit(idx=0, parent=None)
    panel3.setPlainText("起")
    panel3.show()

    def sync3(joint_previous):
        sync_text_by_diff(item3, panel3.toPlainText(), joint_previous)

    panel3.propagate_user_edited.connect(sync3)
    panel3.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    for ch in "XYZ":
        cur3 = panel3.textCursor()
        cur3.movePosition(QTextCursor.MoveOperation.End)
        cur3.insertText(ch)
        app.processEvents()
    panel_steps = panel3.document().availableUndoSteps()
    item_steps = item3.document().availableUndoSteps()
    check(
        "continuous typing keeps undo-step parity",
        panel_steps == item_steps and item3.toPlainText() == "起XYZ",
        "(panel_steps=%d item_steps=%d item=%r)"
        % (panel_steps, item_steps, item3.toPlainText()),
    )

    ok = all(ok for _, ok in RESULTS)
    print("----")
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())