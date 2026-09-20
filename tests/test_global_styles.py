"""Offscreen unit tests for ``utils/global_styles.py`` — global style library.

Pure data layer (no Qt widgets), matching ``tests/test_base_styles.py``
conventions. File paths are pinned to pytest tmp_path so the user's real
``config/global_styles.json`` is never touched.

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_global_styles.py -q
"""

import json
import os
import os.path as osp
import sys

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402

from utils import global_styles as gs  # noqa: E402
from utils.fontformat import FontFormat  # noqa: E402


def _ffmt(family: str = "Test Font", size: float = 40.0, vertical: bool = False):
    ffmt = FontFormat()
    ffmt.font_family = family
    ffmt.font_size = size
    ffmt.vertical = vertical
    return ffmt


@pytest.fixture(autouse=True)
def _clean_library():
    gs.global_styles.clear()
    yield
    gs.global_styles.clear()


class TestRoundtrip:
    def test_save_then_load_preserves_entries(self, tmp_path):
        gs.add_style("正文", _ffmt())
        gs.add_style("旁白", _ffmt("Other Font", 32.0, True))
        p = str(tmp_path / "global_styles.json")

        assert gs.save_global_styles(p) is True
        gs.global_styles.clear()
        gs.load_global_styles(p)

        assert [bs.name for bs in gs.global_styles] == ["正文", "旁白"]
        by_name = {bs.name: bs for bs in gs.global_styles}
        assert by_name["正文"].fontformat.font_family == "Test Font"
        assert by_name["旁白"].fontformat.vertical is True
        assert by_name["旁白"].fontformat.font_size == 32.0

    def test_saved_file_is_bare_json_list(self, tmp_path):
        gs.add_style("s", _ffmt())
        p = tmp_path / "global_styles.json"
        gs.save_global_styles(str(p))
        data = json.loads(p.read_text(encoding="utf8"))
        assert isinstance(data, list) and len(data) == 1
        assert set(data[0].keys()) == {"name", "fontformat"}

    def test_load_missing_file_keeps_library_empty(self, tmp_path):
        gs.load_global_styles(str(tmp_path / "absent.json"))
        assert gs.global_styles == []

    def test_load_corrupt_file_does_not_crash(self, tmp_path):
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf8")
        gs.load_global_styles(str(p))
        assert gs.global_styles == []

    def test_load_skips_invalid_entries(self, tmp_path):
        p = tmp_path / "mixed.json"
        p.write_text(
            json.dumps([{"name": "ok", "fontformat": {"font_family": "F"}},
                        {"garbage": True}]),
            encoding="utf8",
        )
        gs.load_global_styles(str(p))
        assert len(gs.global_styles) == 1
        assert gs.global_styles[0].name == "ok"


class TestNaming:
    def test_unique_name_passthrough_when_free(self):
        assert gs.unique_name("新样式") == "新样式"

    def test_unique_name_suffixes_on_conflict(self):
        gs.add_style("样式", _ffmt())
        assert gs.unique_name("样式") == "样式 2"
        gs.add_style("样式 2", _ffmt())
        assert gs.unique_name("样式") == "样式 3"

    def test_find_by_name(self):
        entry = gs.add_style("目标", _ffmt())
        assert gs.find_by_name("目标") is entry
        assert gs.find_by_name("不存在") is None


class TestAddRemove:
    def test_add_style_deepcopies_format(self):
        ffmt = _ffmt()
        entry = gs.add_style("s", ffmt)
        ffmt.font_size = 1.0
        assert entry.fontformat.font_size == 40.0
        assert entry.fontformat is not ffmt

    def test_add_style_same_name_twice_gets_suffix(self):
        e1 = gs.add_style("s", _ffmt())
        e2 = gs.add_style("s", _ffmt("Other"))
        assert e1.name == "s" and e2.name == "s 2"

    def test_remove_style(self):
        entry = gs.add_style("s", _ffmt())
        gs.remove_style(entry)
        assert gs.global_styles == []
        gs.remove_style(entry)  # absent → no-op, no raise
