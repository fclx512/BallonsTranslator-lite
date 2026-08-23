"""UI-level tests for the base-style / variant tree in the Font Style Manager.

Covers the three detail modes, variant auto-naming, rename flow and the
flatten (batch edit) chain: changed parameters propagate to every block of
the style across pages, untouched overrides survive, one undo command
restores everything. Runs fully offscreen with fake project/scene objects.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication

from utils.base_styles import (
    BaseStyle,
    variant_display_name,
)
from utils.fontformat import FontFormat

app = QApplication.instance() or QApplication(sys.argv[:1])


class FakeBlk:
    def __init__(self, **kw):
        self.fontformat = FontFormat()
        for k, v in kw.items():
            setattr(self.fontformat, k, v)
        self.text = "hello"
        self.translation = "你好"


class FakeItem:
    def __init__(self, blk):
        self.blk = blk

    def set_fontformat(self, ffmt, set_char_format=False):
        self.blk.fontformat = ffmt

    def toHtml(self):
        return "<p>x</p>"

    def absBoundingRect(self, qrect=False):
        return None

    def setHtml(self, h):
        pass

    def setRect(self, r):
        pass


class FakeCanvas:
    def __init__(self):
        self.pushed = []

    def push_text_command(self, cmd, *args):
        self.pushed.append(cmd)


class FakeSceneManager:
    def __init__(self, items):
        self.canvas = FakeCanvas()
        self.textblk_item_list = items
        self.updated = 0

    def updateSceneTextitems(self):
        self.updated += 1


class FakeProj:
    def __init__(self, pages, current="p1.png"):
        self.pages = pages
        self.current_img = current
        self.base_styles = []
        self.rerendered = []

    def mark_page_needs_rerender(self, p):
        self.rerendered.append(p)


@pytest.fixture
def proj():
    b1 = FakeBlk(font_family="Arial", vertical=True)  # pure
    b2 = FakeBlk(font_family="Arial", vertical=True)  # pure
    b3 = FakeBlk(font_family="Arial", vertical=True, font_size=40.0001, frgb=[255, 0, 0])
    b4 = FakeBlk(font_family="Arial", vertical=True, font_size=40.0, frgb=[255, 0, 0])
    b5 = FakeBlk(font_family="SimSun", vertical=True)  # ungrouped
    pages = {"p1.png": [b1, b2, b3, b4], "p2.png": [b5]}
    p = FakeProj(pages)
    p.base_styles.append(
        BaseStyle("Arial", FontFormat(font_family="Arial", vertical=True))
    )
    return p


def _make_manager(proj, scene_manager=None):
    from ui.fontstyle_manager import FontStyleManager

    fsm = FontStyleManager()
    fsm.set_project(proj, scene_manager)
    fsm.refresh()
    return fsm


def test_discover_tree_structure(proj):
    fsm = _make_manager(proj)
    tree = fsm._tree
    assert len(tree.nodes) == 1
    node = tree.nodes[0]
    # b1, b2 pure; b3, b4 merge into one variant (quantized 40.0001 == 40.0)
    assert node.pure.count == 2
    assert len(node.variants) == 1 and node.variants[0].count == 2
    # b5 lands in ungrouped
    assert len(tree.ungrouped) == 1 and tree.ungrouped[0].count == 1


def test_variant_display_name_quantized(proj):
    fsm = _make_manager(proj)
    var = fsm._tree.nodes[0].variants[0]
    name = variant_display_name("Arial", var.overrides)
    # representative raw value 40.0001 must render as the quantized 40px
    assert "40px" in name and "fg#FF0000" in name
    assert "40.0001" not in name


def test_three_detail_modes_and_rename(proj):
    fsm = _make_manager(proj)
    tree = fsm._tree

    base_payload = {"type": "base", "identity": ("Arial", True)}
    assert fsm.styleTree.select_payload(base_payload)
    fsm._on_node_selected(base_payload)
    assert fsm.detailContent._mode == "base"
    assert fsm.detailContent._name_edit.text() == "Arial"

    var = tree.nodes[0].variants[0]
    var_payload = {"type": "variant", "identity": ("Arial", True), "key": var.key}
    assert fsm.styleTree.select_payload(var_payload)
    fsm._on_node_selected(var_payload)
    assert fsm.detailContent._mode == "variant"

    sig_payload = {"type": "sig", "signature": tree.ungrouped[0].signature}
    assert fsm.styleTree.select_payload(sig_payload)
    fsm._on_node_selected(sig_payload)
    assert fsm.detailContent._mode == "sig"

    # rename flow
    fsm._on_node_selected(base_payload)
    fsm.detailContent._name_edit.setText("正文 Arial")
    fsm.detailContent._on_name_edited()
    assert proj.base_styles[0].name == "正文 Arial"


def test_flatten_applies_changed_param_and_keeps_other_overrides():
    b1 = FakeBlk(font_family="Arial", vertical=True)
    b3 = FakeBlk(font_family="Arial", vertical=True, font_size=40, frgb=[255, 0, 0])
    b5 = FakeBlk(font_family="Arial", vertical=True)  # off-page, data-level path
    other = FakeBlk(font_family="SimSun", vertical=True)
    proj = FakeProj({"p1.png": [b1, b3, other], "p2.png": [b5]})
    proj.base_styles.append(
        BaseStyle("Arial", FontFormat(font_family="Arial", vertical=True))
    )
    sm = FakeSceneManager([FakeItem(b1), FakeItem(b3), FakeItem(other)])

    fsm = _make_manager(proj, sm)
    payload = {"type": "base", "identity": ("Arial", True)}
    fsm.styleTree.select_payload(payload)
    fsm._on_node_selected(payload)

    d = fsm.detailContent
    assert d._mode == "base"
    d._size_spin.setValue(42.0)
    d._apply_all()

    # every Arial block got the new size (both live-item and off-page paths)
    for b in (b1, b3, b5):
        assert b.fontformat.font_size == 42.0
    # the variant's colour override survives; other fonts untouched
    assert b3.fontformat.frgb == [255, 0, 0]
    assert other.fontformat.font_size != 42.0
    # base style itself updated, single undo command, off-page rerender flag
    assert proj.base_styles[0].fontformat.font_size == 42.0
    assert len(sm.canvas.pushed) == 1
    assert "p2.png" in proj.rerendered

    # post-flatten rediscovery: pure count 2, colour-only variant remains
    fsm._on_styles_changed({"type": "base", "identity": ("Arial", True)})
    node = fsm._tree.nodes[0]
    assert node.pure.count == 2
    assert len(node.variants) == 1
    assert set(node.variants[0].overrides) == {"frgb"}
    assert "fg#FF0000" in variant_display_name("Arial", node.variants[0].overrides)

    # undo restores every block's exact previous format
    sm.canvas.pushed[0].undo()
    assert b1.fontformat.font_size != 42.0
    assert b3.fontformat.font_size == 40.0
    assert b5.fontformat.font_size != 42.0


def test_delete_base_style_moves_blocks_to_ungrouped(proj):
    from qtpy.QtWidgets import QMessageBox

    fsm = _make_manager(proj)
    payload = {"type": "base", "identity": ("Arial", True)}
    fsm._on_node_selected(payload)

    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    fsm.detailContent._delete_base_style()
    assert proj.base_styles == []
    fsm._on_styles_changed(None)
    assert fsm._tree.nodes == []
    assert len(fsm._tree.ungrouped) >= 1


def test_promote_ungrouped_creates_base_style(proj):
    from qtpy.QtWidgets import QMessageBox

    empty_proj = FakeProj({"p1.png": [FakeBlk(font_family="SimSun", vertical=True)]})
    fsm = _make_manager(empty_proj)
    assert empty_proj.base_styles == []
    assert len(fsm._tree.ungrouped) == 1

    sig_payload = {"type": "sig", "signature": fsm._tree.ungrouped[0].signature}
    fsm._on_node_selected(sig_payload)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    fsm.detailContent._promote_to_base()

    assert len(empty_proj.base_styles) == 1
    bs = empty_proj.base_styles[0]
    assert bs.identity == ("SimSun", True)
    assert bs.name == "SimSun"

    # duplicate identity is rejected with a warning (no second style)
    fsm._on_node_selected({"type": "sig", "signature": "nonexistent"})
    fsm.detailContent._promote_to_base()
    assert len(empty_proj.base_styles) == 1
