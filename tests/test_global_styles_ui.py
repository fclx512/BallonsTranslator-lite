"""UI-level tests for the global style library in the Font Style Manager.

Covers the left-tree library section, the library-entry detail mode and the
copy semantics in both directions (deepcopy, no live reference) including
the identity-conflict dialogs. Runs fully offscreen; the library module list
is cleaned between tests and the on-disk library file is redirected to a
tmp path so the user's real data is never touched.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QMessageBox

from utils import global_styles as gs
from utils.base_styles import BaseStyle
from utils.fontformat import FontFormat

app = QApplication.instance() or QApplication(sys.argv[:1])


class FakeBlk:
    def __init__(self, **kw):
        self.fontformat = FontFormat()
        for k, v in kw.items():
            setattr(self.fontformat, k, v)
        self.text = "hello"
        self.translation = "你好"


class FakeProj:
    def __init__(self, pages):
        self.pages = pages
        self.current_img = "p1.png"
        self.base_styles = []
        self.rerendered = []

    def mark_page_needs_rerender(self, p):
        self.rerendered.append(p)


@pytest.fixture(autouse=True)
def _clean_library(tmp_path, monkeypatch):
    gs.global_styles.clear()
    # 重定向库文件：UI 操作内的 save_global_styles 不落用户真实数据
    monkeypatch.setattr(gs, "save_global_styles", lambda p=None: True)
    yield
    gs.global_styles.clear()


@pytest.fixture
def proj():
    pages = {"p1.png": [FakeBlk(font_family="Arial", vertical=True)]}
    p = FakeProj(pages)
    p.base_styles.append(
        BaseStyle("Arial", FontFormat(font_family="Arial", vertical=True))
    )
    return p


def _make_manager(proj):
    from ui.fontstyle_manager import FontStyleManager

    fsm = FontStyleManager()
    fsm.set_project(proj, None)
    fsm.refresh()
    return fsm


def _select(fsm, payload):
    assert fsm.styleTree.select_payload(payload), f"node not found: {payload}"
    return fsm.detailContent


def test_library_section_in_tree(proj):
    from qtpy.QtCore import Qt

    from ui.fontstyle_manager import _DISPLAY_ROLE

    gs.add_style("库样式", FontFormat(font_family="SimSun", vertical=False))
    fsm = _make_manager(proj)
    top = fsm.styleTree.topLevelItem(0)
    assert top.text(0) == fsm.styleTree.tr("Global Style Library")
    child = top.child(0)
    assert child.data(0, Qt.ItemDataRole.UserRole) == {
        "type": "global",
        "name": "库样式",
    }
    # 库条目带「模板」标签（委托据此在右侧块数槽画来源胶囊）——它是库条目与
    # 项目样式的唯一行内区分，键名一改就静默失效、两边又混在一起
    assert child.data(0, _DISPLAY_ROLE)["tag"] == fsm.styleTree.tr("Template")


def test_actions_hidden_until_a_style_is_selected(proj):
    """未选中时右栏的动作按钮全部收起。

    它们的显隐只写在对模式的 ``show_*`` 里，"没有模式"这条路曾谁都没隐藏——
    刚打开管理器时六个按钮挤成一行被裁、且全都无从触发（用户实测反馈）。
    """
    fsm = _make_manager(proj)
    detail = fsm.detailContent
    buttons = (
        detail._reset_base_btn,
        detail._promote_btn,
        detail._add_library_btn,
        detail._delete_base_btn,
        detail._copy_project_btn,
        detail._delete_library_btn,
        detail._apply_all_btn,
    )
    # 测试里窗口不上屏，可见性要用 isVisibleTo(祖先)
    assert not any(b.isVisibleTo(detail) for b in buttons)

    _select(fsm, {"type": "base", "identity": ("Arial", True)})
    assert detail._apply_all_btn.isVisibleTo(detail)


def test_library_entry_detail_mode(proj):
    gs.add_style("库样式", FontFormat(font_family="SimSun", vertical=False))
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "库样式"})
    assert detail._mode == detail.MODE_GLOBAL
    assert detail._lib_style is gs.find_by_name("库样式")
    assert detail._name_edit.text() == "库样式"
    # blocks section must be empty for a template
    assert detail._block_chips._chips == []


def test_copy_to_project_creates_base_style(proj):
    gs.add_style("库样式", FontFormat(font_family="SimSun", vertical=False))
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "库样式"})
    detail._copy_global_to_project()
    assert [bs.name for bs in proj.base_styles] == ["Arial", "库样式"]
    new = proj.base_styles[1]
    assert (new.fontformat.font_family, new.fontformat.vertical) == ("SimSun", False)


def test_copy_to_project_is_deepcopy(proj):
    entry = gs.add_style("库样式", FontFormat(font_family="SimSun", vertical=False))
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "库样式"})
    detail._copy_global_to_project()
    entry.fontformat.font_size = 5.0
    assert proj.base_styles[1].fontformat.font_size != 5.0


def test_copy_to_project_overwrites_on_identity_conflict(proj, monkeypatch):
    # 项目里已有 Arial 竖排大样式；库条目同身份键 → 询问覆盖
    gs.add_style(
        "Arial 库版", FontFormat(font_family="Arial", vertical=True, font_size=77.0)
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "Arial 库版"})
    detail._copy_global_to_project()
    assert len(proj.base_styles) == 1
    assert proj.base_styles[0].fontformat.font_size == 77.0
    # 名字换成了库条目名（项目内无同名冲突）
    assert proj.base_styles[0].name == "Arial 库版"


def test_copy_to_project_declined_keeps_project(proj, monkeypatch):
    gs.add_style(
        "Arial 库版", FontFormat(font_family="Arial", vertical=True, font_size=77.0)
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "Arial 库版"})
    detail._copy_global_to_project()
    assert len(proj.base_styles) == 1
    assert proj.base_styles[0].fontformat.font_size != 77.0


def test_add_base_to_library(proj):
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "base", "identity": ("Arial", True)})
    detail._add_base_to_library()
    entry = gs.find_by_name("Arial")
    assert entry is not None
    assert entry.fontformat.font_family == "Arial"


def test_add_base_to_library_overwrite_conflict(proj, monkeypatch):
    gs.add_style("Arial", FontFormat(font_family="Old", vertical=True, font_size=9.0))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "base", "identity": ("Arial", True)})
    detail._add_base_to_library()
    assert len(gs.global_styles) == 1
    assert gs.find_by_name("Arial").fontformat.font_family == "Arial"


def test_delete_library_style(proj, monkeypatch):
    gs.add_style("库样式", FontFormat())
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "库样式"})
    detail._delete_library_style()
    assert gs.global_styles == []
    assert detail._lib_style is None


def test_apply_library_entry_writes_params(proj):
    gs.add_style("库样式", FontFormat(font_family="SimSun", vertical=False))
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "库样式"})
    detail._panel.set_field_value("font_size", 33.0)
    detail._apply_library_entry()
    assert gs.find_by_name("库样式").fontformat.font_size == 33.0


def test_rename_library_entry_auto_suffix(proj):
    gs.add_style("样式", FontFormat())
    gs.add_style("样式 2", FontFormat())
    fsm = _make_manager(proj)
    detail = _select(fsm, {"type": "global", "name": "样式"})
    detail._name_edit.setText("样式 2")
    detail._on_name_edited()
    # 冲突自动加后缀，不打扰；改名后原条目名被腾出
    assert detail._lib_style.name == "样式 2 2"
    assert gs.find_by_name("样式 2 2") is detail._lib_style
    assert gs.find_by_name("样式") is None


def test_new_library_style_button(proj):
    fsm = _make_manager(proj)
    fsm._new_library_style()
    assert len(gs.global_styles) == 1
    assert fsm.detailContent._mode == fsm.detailContent.MODE_GLOBAL
