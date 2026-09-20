"""模型文件数据层（``utils/model_files.py``）与「模型文件」节的回归。

锁四件事：

1. **落盘判据**：声明解析（``save_files`` 优先、``files`` 回落）、缺失判定，
   以及包目录**只认声明、绝不反推**（反推会把 ``data/models/`` 当成包目录，
   按它算体积、按它删都出事）。
2. **释放的安全边界**：白名单之外的路径一律不动、被 git 跟踪的字典文件必须
   跳过、目录拒删、空目录收尾绝不越过 ``data/models``。
3. **设置页节的行构建**：卡片行内容（主文 / 元数据 / 状态徽章）与
   「勾选 → 动作按钮可用性」的联动。
4. **节依赖的 RowTable 能力**：就地更新行（下载进度的高频路径）与徽章让位宽度。

删除测试一律走 ``use_trash=False``（直接删）——回收站属于真机验收项，见
``docs/技术实现/模型文件管理_测试流程.md``。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_model_files.py -q
"""

import os
import os.path as osp
import shutil
import sys
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_API", "pyqt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils.model_files import (  # noqa: E402
    GIT_TRACKED_MODEL_FILES,
    declared_save_files,
    delete_paths,
    dir_size_bytes,
    format_size,
    missing_declared_files,
    package_dir_of,
    package_size_bytes,
    size_hint_bytes,
    trash_available,
)

# 测试用的临时包目录，落在 data/models 内部（.gitignore 覆盖），用完即删
_SCRATCH_REL = "data/models/_pytest_model_files"
_SCRATCH = osp.join(APP_ROOT, _SCRATCH_REL)


def _dl_entry(files, save_files=None):
    entry = {"url": "https://example.invalid/", "files": files}
    if save_files is not None:
        entry["save_files"] = save_files
    return entry


class TestDeclaredFiles(unittest.TestCase):
    """声明解析与缺失判定"""

    def test_save_files_wins_over_files(self):
        entry = _dl_entry(["a.json"], ["data/models/pkg/a.json"])
        self.assertEqual(declared_save_files([entry]), ["data/models/pkg/a.json"])

    def test_falls_back_to_files(self):
        entry = _dl_entry("data/models/ysgyolo_yolo26_2.0.pt")
        self.assertEqual(
            declared_save_files([entry]), ["data/models/ysgyolo_yolo26_2.0.pt"]
        )

    def test_save_dir_is_deliberately_ignored(self):
        """``save_dir`` 不是合法字段：两个消费方都不读它，用了会全量误报缺失。"""
        entry = {
            "url": "https://example.invalid/",
            "files": ["a.json"],
            "save_dir": "data/models/pkg",
        }
        self.assertEqual(declared_save_files([entry]), ["a.json"])

    def test_dedupes_and_keeps_order(self):
        entries = [
            _dl_entry(["b.bin", "a.bin"]),
            _dl_entry(["a.bin"]),
        ]
        self.assertEqual(declared_save_files(entries), ["b.bin", "a.bin"])

    def test_missing_detects_absent_and_ignores_present(self):
        present = "data/models/lama_large_512px.ckpt"
        if not osp.exists(osp.join(APP_ROOT, present)):
            self.skipTest("lama_large_512px.ckpt not present in this checkout")
        entry = _dl_entry([present, "data/models/_nope_.bin"])
        self.assertEqual(
            missing_declared_files([entry]), ["data/models/_nope_.bin"]
        )

    def test_missing_is_empty_for_empty_declaration(self):
        self.assertEqual(missing_declared_files(None), [])
        self.assertEqual(missing_declared_files([]), [])


class TestPackageBoundary(unittest.TestCase):
    """包目录与体积"""

    def test_package_dir_comes_from_declaration_only(self):
        self.assertEqual(
            package_dir_of({"dir": "data/models/pkg"}), "data/models/pkg"
        )

    def test_package_dir_is_never_reverse_derived(self):
        """单文件模块的父目录是 data/models/ —— 反推等于把全部模型当一包。"""
        entry = _dl_entry([], ["data/models/lama_large_512px.ckpt"])
        self.assertIsNone(package_dir_of(None, entry))
        self.assertIsNone(package_dir_of({"size_hint": "1.9 GB"}, entry))

    def test_size_of_file_package_sums_declared_files_only(self):
        entry = _dl_entry([], ["data/models/lama_large_512px.ckpt"])
        if not osp.exists(osp.join(APP_ROOT, "data/models/lama_large_512px.ckpt")):
            self.skipTest("lama_large_512px.ckpt not present in this checkout")
        size = package_size_bytes(None, [entry])
        self.assertGreater(size, 0)
        # 远小于整个 data/models —— 证明没有反推父目录
        self.assertLess(size, dir_size_bytes(osp.join(APP_ROOT, "data/models")))

    def test_size_hint_parsing(self):
        self.assertEqual(size_hint_bytes("1.9 GB"), int(1.9 * 1024**3))
        self.assertEqual(size_hint_bytes("1.9GB"), int(1.9 * 1024**3))
        self.assertEqual(size_hint_bytes("512 MB"), 512 * 1024**2)
        self.assertEqual(size_hint_bytes("15MB"), 15 * 1024**2)
        self.assertEqual(size_hint_bytes("1024"), 1024)
        self.assertIsNone(size_hint_bytes("大约一个G"))
        self.assertIsNone(size_hint_bytes(None))

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1023), "1023 B")
        self.assertTrue(format_size(1932735283).endswith("GB"))


class TestDeleteSafety(unittest.TestCase):
    """释放的安全边界 —— 本文件最重要的一组"""

    def setUp(self):
        self._created = []

    def tearDown(self):
        for path in reversed(self._created):
            try:
                if osp.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif osp.exists(path):
                    os.remove(path)
            except OSError:
                pass
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    def _mkfile(self, rel, data=b"x" * 64):
        full = osp.join(APP_ROOT, rel)
        os.makedirs(osp.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        self._created.append(full)
        return rel

    def test_git_tracked_dict_is_protected(self):
        for rel in GIT_TRACKED_MODEL_FILES:
            full = osp.join(APP_ROOT, rel)
            if not osp.exists(full):
                continue
            before = osp.getsize(full)
            result = delete_paths([rel], use_trash=False)
            self.assertEqual(result.removed, [])
            self.assertIn(rel, result.skipped)
            self.assertTrue(osp.exists(full), f"{rel} must survive a delete request")
            self.assertEqual(osp.getsize(full), before)

    def test_missing_paths_are_skipped_not_errors(self):
        result = delete_paths(["data/models/_definitely_not_here_.bin"], use_trash=False)
        self.assertEqual(result.removed, [])
        self.assertEqual(result.failed, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertTrue(result.ok)

    def test_declared_directory_is_refused(self):
        """声明里出现目录说明口径有问题——拒绝，绝不递归删。"""
        rel = f"{_SCRATCH_REL}/sub"
        os.makedirs(osp.join(APP_ROOT, rel), exist_ok=True)
        self._created.append(osp.join(APP_ROOT, rel))
        result = delete_paths([rel], use_trash=False)
        self.assertEqual(result.removed, [])
        self.assertIn(rel, result.skipped)
        self.assertTrue(osp.isdir(osp.join(APP_ROOT, rel)))

    def test_delete_removes_files_and_prunes_empty_dirs(self):
        rel_a = self._mkfile(f"{_SCRATCH_REL}/sub/a.bin")
        rel_b = self._mkfile(f"{_SCRATCH_REL}/sub/b.bin")
        result = delete_paths(
            [rel_a, rel_b],
            package_dir=_SCRATCH_REL,
            use_trash=False,
        )
        self.assertEqual(sorted(result.removed), sorted([rel_a, rel_b]))
        self.assertTrue(result.permanent)
        self.assertFalse(osp.exists(osp.join(APP_ROOT, rel_a)))
        self.assertFalse(osp.exists(osp.join(APP_ROOT, rel_b)))
        # 空目录被收尾，但 data/models 本身永不被删
        self.assertFalse(osp.isdir(osp.join(APP_ROOT, _SCRATCH_REL)))
        self.assertTrue(osp.isdir(osp.join(APP_ROOT, "data/models")))

    def test_prune_stops_at_non_empty_parent(self):
        rel_a = self._mkfile(f"{_SCRATCH_REL}/keep/x.bin")
        rel_b = self._mkfile(f"{_SCRATCH_REL}/keep/y.bin")
        rel_c = self._mkfile(f"{_SCRATCH_REL}/keep/neighbour.bin")
        result = delete_paths([rel_a, rel_b], use_trash=False)
        self.assertEqual(len(result.removed), 2)
        self.assertFalse(osp.exists(osp.join(APP_ROOT, rel_a)))
        self.assertTrue(
            osp.exists(osp.join(APP_ROOT, rel_c)), "非空目录里的邻居不能被动到"
        )
        self.assertTrue(osp.isdir(osp.join(APP_ROOT, _SCRATCH_REL)))

    def test_trash_return_code_does_not_decide_success(self):
        """Windows Shell 的返回码不可信：成功与否只看调用后文件还在不在。

        实测同一批文件在不同标志位下给出 0 / 2 / 1223 三种码，而文件都已进回收站
        （见 utils/model_files.py::_trash_windows 的实测表）。这里用一个「报了非零
        码但确实删掉了」的替身钉住这条判据。
        """
        rel = self._mkfile(f"{_SCRATCH_REL}/trashme.bin")
        full = osp.join(APP_ROOT, rel)

        def fake_trash(paths):
            for path in paths:
                os.remove(path)
            return True, "win32com code=2"

        with mock.patch(
            "utils.model_files._trash_windows", side_effect=fake_trash
        ), mock.patch("utils.model_files.trash_available", return_value=True):
            result = delete_paths([rel])
        self.assertEqual(result.removed, [rel])
        self.assertEqual(result.failed, [])
        self.assertFalse(result.permanent)
        self.assertFalse(osp.exists(full))

    def test_shell_failure_never_falls_back_to_permanent_delete(self):
        """Shell 整个调不通时不偷偷改成永久删除——确认框承诺过可还原。"""
        rel = self._mkfile(f"{_SCRATCH_REL}/survivor.bin")
        with mock.patch(
            "utils.model_files._trash_windows", return_value=(False, "boom")
        ), mock.patch("utils.model_files.trash_available", return_value=True):
            result = delete_paths([rel])
        self.assertEqual(result.removed, [])
        self.assertEqual(result.failed, [rel])
        self.assertTrue(osp.exists(osp.join(APP_ROOT, rel)))

    def test_trash_probe_does_not_raise(self):
        """只探测可用性——真的送回收站属于真机验收项。"""
        self.assertIsInstance(trash_available(), bool)


class TestRowTableCard(unittest.TestCase):
    """「模型文件」节依赖的两个 RowTable 能力（公共控件回归）"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.custom_widget import MODE_CARD, RowTable

        self.table = RowTable(MODE_CARD)
        self.table.set_rows(
            [
                {
                    "primary": "A",
                    "meta": "m",
                    "badge": "Ready",
                    "badge_tone": "muted",
                },
                {
                    "primary": "B",
                    "meta": "m",
                    "badge": "Missing 3",
                    "badge_tone": "warning",
                },
            ]
        )

    def tearDown(self):
        self.table.deleteLater()

    def test_update_row_keeps_checks_and_does_not_reset(self):
        self.table.set_checked(1, True)
        self.table.update_row(1, {"badge": "42%", "badge_tone": "accent"})
        row = self.table._model.rows[1]
        self.assertEqual(row["badge"], "42%")
        self.assertEqual(row["badge_tone"], "accent")
        self.assertTrue(row["checked"], "就地更新不能丢勾选")

    def test_update_row_out_of_range_is_noop(self):
        self.table.update_row(9, {"badge": "x"})  # 不该抛
        self.assertEqual(len(self.table._model.rows), 2)

    def test_badge_reserve_grows_with_text(self):
        """长徽章的让位宽度必须跟着文字长——固定值会让它压到主文上。"""
        from ui.custom_widget.row_table import _RowDelegate

        short, _ = _RowDelegate._badge_metrics(self.table.font(), "Ready")
        long, _ = _RowDelegate._badge_metrics(self.table.font(), "Missing package")
        self.assertGreater(long, short)


class TestModelFilesSection(unittest.TestCase):
    """设置页「模型文件」节（卡片列表 + 勾选驱动的动作行）"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import modules
        from ui.model_files_panel import ModelFilesSection

        modules.init_module_registries()
        self.modules = modules
        self.present_rel = f"{_SCRATCH_REL}/present.bin"
        full = osp.join(APP_ROOT, self.present_rel)
        os.makedirs(osp.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"y" * 32)
        self._patched = mock.patch.object(
            modules, "GET_MODEL_PACKAGES", side_effect=self._fake_packages
        )
        self._patched.start()
        self.section = ModelFilesSection()

    def tearDown(self):
        self._patched.stop()
        self.section.deleteLater()
        shutil.rmtree(osp.join(APP_ROOT, _SCRATCH_REL), ignore_errors=True)

    def _fake_packages(self):
        """替身数据源：与 GET_MODEL_PACKAGES 一样按当前磁盘状态重算缺失项。"""
        present = osp.exists(osp.join(APP_ROOT, self.present_rel))
        return [
            {
                "module_type": "ocr",
                "key": "ready_ocr",
                "display_name": "Ready OCR",
                "package_dir": None,
                "size_hint": None,
                "size_bytes": 2048 if present else 0,
                "missing": [] if present else [self.present_rel],
                "files": [self.present_rel],
                "missing_packages": [],
                "download_file_list": [],
            },
            {
                "module_type": "ocr",
                "key": "absent_ocr",
                "display_name": "Absent OCR",
                "package_dir": "data/models/_pytest_absent",
                "size_hint": "1.9 GB",
                "size_bytes": 0,
                "missing": ["data/models/_pytest_absent/model.safetensors"],
                "files": ["data/models/_pytest_absent/model.safetensors"],
                "missing_packages": ["transformers>=4.57.1,<5"],
                "download_file_list": [],
            },
        ]

    # ── 行内容 ───────────────────────────────────────────────────────────

    def _row(self, key: str) -> dict:
        for idx, info in enumerate(self.section._packages):
            if info["key"] == key:
                return self.section._table._model.rows[idx]
        raise AssertionError(f"{key} not in packages")

    def test_one_card_per_package(self):
        self.assertEqual(len(self.section._packages), 2)
        self.assertEqual(len(self.section._table._model.rows), 2)

    def test_ready_card_shows_name_metadata_and_badge(self):
        row = self._row("ready_ocr")
        self.assertEqual(row["primary"], "Ready OCR")
        # 元数据次行：阶段 · 注册名 · 文件数 · 体积
        self.assertIn("ready_ocr", row["meta"])
        self.assertIn("2.0 KB", row["meta"])
        self.assertEqual(row["badge"], "Ready")
        self.assertEqual(row["badge_tone"], "muted")

    def test_missing_card_shows_warning_badge_and_expected_size(self):
        row = self._row("absent_ocr")
        self.assertIn("Missing", row["badge"])
        self.assertEqual(row["badge_tone"], "warning")
        # 未装时显示的是预期体积，而不是 0
        self.assertIn("1.9 GB", row["meta"])

    # ── 勾选 → 动作可用性 ────────────────────────────────────────────────

    def _tick(self, key: str, checked: bool = True):
        for idx, info in enumerate(self.section._packages):
            if info["key"] == key:
                self.section._table.set_checked(idx, checked)
                self.section._on_check_toggled(idx, checked)
                return
        raise AssertionError(f"{key} not in packages")

    def test_no_selection_disables_every_action(self):
        self.assertFalse(self.section._download_btn.isEnabled())
        self.assertFalse(self.section._delete_btn.isEnabled())
        self.assertFalse(self.section._open_btn.isEnabled())
        self.assertFalse(self.section._cancel_btn.isVisible())
        self.assertIn("0 selected", self.section._status.text())

    def test_ready_row_allows_delete_but_not_download(self):
        self._tick("ready_ocr")
        self.assertFalse(self.section._download_btn.isEnabled())
        self.assertTrue(self.section._delete_btn.isEnabled())
        self.assertTrue(self.section._open_btn.isEnabled())

    def test_missing_row_allows_download_but_not_delete(self):
        self._tick("absent_ocr")
        self.assertTrue(self.section._download_btn.isEnabled())
        self.assertFalse(self.section._delete_btn.isEnabled())

    def test_open_folder_requires_exactly_one_selection(self):
        self._tick("ready_ocr")
        self._tick("absent_ocr")
        self.assertFalse(self.section._open_btn.isEnabled())
        self.assertTrue(self.section._download_btn.isEnabled())
        self.assertIn("2 selected", self.section._status.text())

    def test_refresh_keeps_ticks_and_follows_disk_state(self):
        """刷新（或在外部手工删掉文件后）同一行要变成「缺文件」并放开下载。"""
        self._tick("ready_ocr")
        os.remove(osp.join(APP_ROOT, self.present_rel))
        self.section.refresh()
        row = self._row("ready_ocr")
        self.assertTrue(row["checked"], "刷新不该丢掉勾选")
        self.assertNotEqual(row["badge"], "Ready")
        self.assertTrue(self.section._download_btn.isEnabled())
        self.assertFalse(self.section._delete_btn.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
