"""Tests for the configurable shortcut system.

Locks in the fixes made in response to the upstream review of the shortcut
PR:

- conflict detection must not report a key duplicated *within one action* as
  a cross-action conflict (``find_conflict_keys`` dedupes owners);
- persisted shortcut data is sanitized on load so malformed values (e.g.
  ``{"undo": [1]}``) no longer crash the editor (``sanitize_shortcuts``);
- factory defaults for the standard actions resolve through Qt StandardKeys
  so macOS gets native Command bindings, while on Windows the resolved
  defaults stay byte-identical to the legacy literals (zero regression);
- the editor's action registry (``DEFAULT_SHORTCUTS``) stays in sync with the
  grouped display (``_SHORTCUT_GROUPS``).

Qt-touching helpers run under an offscreen ``QApplication`` (see
``test_screen_picker.py`` for the same pattern).
"""

import os
import os.path as osp
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)


class TestShortcutConflictAndSanitize(unittest.TestCase):
    """Pure-logic tests that need no Qt application."""

    def test_conflict_dedupes_owners_per_key(self):
        """A key repeated within a single action is not a conflict."""
        from utils.shortcut_conflicts import find_conflict_keys

        self.assertEqual(find_conflict_keys({"undo": ["Ctrl+Z", "Ctrl+Z"]}), set())
        self.assertEqual(
            find_conflict_keys(
                {"a": ["Ctrl+Z", "Ctrl+Z"], "b": ["Ctrl+Z"]}
            ),
            {"Ctrl+Z"},
        )

    def test_conflict_across_actions_only(self):
        from utils.shortcut_conflicts import find_conflict_keys

        self.assertEqual(
            find_conflict_keys({"undo": ["Ctrl+Z"], "redo": ["Ctrl+Z"]}),
            {"Ctrl+Z"},
        )
        self.assertEqual(
            find_conflict_keys({"a": ["X"], "b": ["Y"], "c": ["X", "Z"]}),
            {"X"},
        )

    def test_conflict_ignores_empty_and_none(self):
        from utils.shortcut_conflicts import find_conflict_keys

        self.assertEqual(find_conflict_keys({"a": [], "b": None}), set())

    def test_sanitize_non_dict(self):
        from utils.config import sanitize_shortcuts

        self.assertEqual(sanitize_shortcuts(None), {})
        self.assertEqual(sanitize_shortcuts("junk"), {})

    def test_sanitize_drops_non_string_items(self):
        """``{"undo": [1]}`` must not survive to reach a QLabel."""
        from utils.config import sanitize_shortcuts

        self.assertEqual(sanitize_shortcuts({"undo": [1]}), {"undo": []})
        self.assertEqual(
            sanitize_shortcuts({"undo": [1, "A", "B", "A", ""]}),
            {"undo": ["A", "B"]},
        )

    def test_sanitize_drops_none_and_non_str_actions(self):
        from utils.config import sanitize_shortcuts

        self.assertEqual(sanitize_shortcuts({"undo": None}), {})
        self.assertEqual(sanitize_shortcuts({1: ["A"]}), {})

    def test_sanitize_wraps_single_string(self):
        from utils.config import sanitize_shortcuts

        self.assertEqual(
            sanitize_shortcuts({"undo": "Ctrl+Z"}), {"undo": ["Ctrl+Z"]}
        )

    def test_load_config_calls_sanitize(self):
        """load_config must route persisted shortcuts through sanitize."""
        with open(
            osp.join(APP_ROOT, "utils", "config.py"), "r", encoding="utf-8"
        ) as f:
            source = f.read()
        self.assertIn("pcfg.shortcuts = sanitize_shortcuts(pcfg.shortcuts)", source)


class TestShortcutEditorHelpers(unittest.TestCase):
    """Shortcut editor helpers that create Qt objects."""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(sys.argv[:1])

    def test_default_keys_windows_parity(self):
        """On Windows, resolved defaults must equal the legacy literals."""
        from ui.configpanel import (
            DEFAULT_SHORTCUTS,
            _STANDARD_DEFAULT_KEYS,
            default_keys_for,
        )

        for aid, literal in DEFAULT_SHORTCUTS.items():
            resolved = default_keys_for(aid)
            self.assertEqual(
                resolved, literal, f"default regression for {aid}"
            )
        # Standard-key set must be a known subset of the registry.
        self.assertLessEqual(set(_STANDARD_DEFAULT_KEYS), set(DEFAULT_SHORTCUTS))

    def test_native_display_is_identity_on_windows(self):
        from ui.configpanel import native_key_display

        self.assertEqual(native_key_display("Ctrl+Z"), "Ctrl+Z")
        self.assertEqual(native_key_display("A"), "A")
        self.assertEqual(native_key_display("Del"), "Del")

    def test_registry_matches_grouped_display(self):
        from ui.configpanel import DEFAULT_SHORTCUTS, _SHORTCUT_GROUPS

        grouped = {aid for _, aids in _SHORTCUT_GROUPS for aid in aids}
        self.assertEqual(set(DEFAULT_SHORTCUTS), grouped)


if __name__ == "__main__":
    unittest.main()
