"""Dependency visualisation dialog.

Shows all declared dependencies (core + optional) with install status
and version numbers.  Provides one-click install for missing packages.
"""

import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from qtpy.QtCore import QThread, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils import shared
from utils.logger import logger as LOGGER

try:
    import importlib.metadata as importlib_metadata
except (ModuleNotFoundError, ImportError):
    import importlib_metadata


# ── TOML loader (stdlib on 3.11+, tomli backport on 3.10) ────────────

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


def _load_declared_deps() -> list[tuple[str, str]]:
    """Parse pyproject.toml and return (req_str, type) pairs.

    *type* is ``"core"`` for mandatory deps, or the optional-dependency
    group name (e.g. ``"gpu"``, ``"mcp"``) for optional groups.
    Automatically discovers all groups — no hardcoded list needed.
    """
    pp = Path(shared.PROGRAM_PATH) / "pyproject.toml"
    if not pp.exists() or tomllib is None:
        return []

    with open(pp, "rb") as f:
        data = tomllib.load(f)

    deps: list[tuple[str, str]] = []
    for dep in data.get("project", {}).get("dependencies", []):
        deps.append((dep, "core"))
    for group_name, group_deps in (
        data.get("project", {}).get("optional-dependencies", {}).items()
    ):
        for dep in group_deps:
            deps.append((dep, group_name))
    return deps


# ── Helpers ───────────────────────────────────────────────────────────

_DEP_TYPE_META: dict[str, tuple[str, QColor]] = {
    "core": ("Core", QColor("#4a9eff")),
}

# Palette cycled for optional-dependency groups discovered dynamically
_OPT_GROUP_COLORS = [
    QColor("#9b59b6"),  # purple
    QColor("#e67e22"),  # orange
    QColor("#1abc9c"),  # teal
    QColor("#2ecc71"),  # green
    QColor("#f39c12"),  # yellow
    QColor("#e74c3c"),  # red
    QColor("#3498db"),  # blue
]
_OPT_GROUP_META: dict[str, tuple[str, QColor]] = {}

_STATUS_COLORS = {
    "installed": QColor("#27ae60"),
    "missing": QColor("#e74c3c"),
    "mismatch": QColor("#f39c12"),
    "skipped": QColor("#95a5a6"),
}


def _installed_version(req_name: str) -> str:
    try:
        dist = importlib_metadata.distribution(canonicalize_name(req_name))
        return dist.version
    except importlib_metadata.PackageNotFoundError:
        return ""


def _check_req(req_str: str) -> tuple[str, str]:
    """Return (status, version) for a requirement string.

    Status is one of ``"installed"``, ``"missing"``, ``"mismatch"``, or
    ``"skipped"`` — the last means an environment marker (e.g. OS or Python
    version guard) evaluated to False in the current runtime, so the dep
    is *correctly* absent and should not be flagged.
    """
    req = Requirement(req_str)
    # Evaluate environment markers — skip if not applicable
    if req.marker and not req.marker.evaluate():
        return "skipped", ""
    ver = _installed_version(req.name)
    if not ver:
        return "missing", ver
    if req.specifier.contains(ver, prereleases=True):
        return "installed", ver
    return "mismatch", ver


# ── Install worker ────────────────────────────────────────────────────


class _InstallWorker(QThread):
    progress = Signal(int, int)  # current, total
    finished_one = Signal(str, bool)  # req_str, success
    all_done = Signal()

    def __init__(self, reqs: list[str], parent=None):
        super().__init__(parent)
        self.reqs = reqs
        self._install_cmd, self._using_uv = self._detect_installer()

    @staticmethod
    def _strip_marker(req_str: str) -> str:
        """Strip environment markers so pip never mis-evaluates them on CLI.

        ``"tomli; python_version < '3.11'"`` → ``"tomli"``
        ``"opencv-python>=4.10.0.84; sys_platform == 'win32'"`` → ``"opencv-python>=4.10.0.84"``
        """
        req = Requirement(req_str)
        return f"{req.name}{req.specifier}" if req.specifier else req.name

    @staticmethod
    def _detect_installer() -> tuple[list[str], bool]:
        """Return (cmd_base, using_uv) for the best available installer.

        uv is preferred when available, but we must NOT pass pip-specific
        flags (``--prefer-binary``) to it — uv doesn't support them.
        """
        python = sys.executable
        try:
            subprocess.run(
                [python, "-m", "uv", "--version"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            return [python, "-m", "uv", "pip", "install"], True
        except Exception:
            return [python, "-m", "pip", "install"], False

    def run(self):
        total = len(self.reqs)
        pip_fallback = [sys.executable, "-m", "pip", "install", "--prefer-binary"]
        for i, req in enumerate(self.reqs):
            stripped = self._strip_marker(req)
            success = False
            # Try preferred installer first
            try:
                subprocess.run(
                    [*self._install_cmd, stripped],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                success = True
            except Exception:
                # If uv failed, fall back to pip
                if self._using_uv:
                    try:
                        subprocess.run(
                            [*pip_fallback, stripped],
                            capture_output=True,
                            timeout=300,
                            check=True,
                        )
                        success = True
                    except Exception:
                        pass
            self.finished_one.emit(req, success)
            self.progress.emit(i + 1, total)
        self.all_done.emit()


# ── Dialog ────────────────────────────────────────────────────────────


class DependencyPanel(QWidget):
    """Visual dependency list with install status and one-click install.

    Embeddable in a dialog or tab widget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[str, str, str, str, str]] = []
        self._build_ui()
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Package"),
                self.tr("Type"),
                self.tr("Status"),
                self.tr("Version"),
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Summary
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        # Buttons
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton(self.tr("Refresh"))
        self.refresh_btn.clicked.connect(self._refresh)
        self.install_missing_btn = QPushButton(self.tr("Install Missing"))
        self.install_missing_btn.clicked.connect(
            lambda: self._install_missing(include_optional=False)
        )
        self.install_all_btn = QPushButton(self.tr("Install All (incl. optional)"))
        self.install_all_btn.clicked.connect(
            lambda: self._install_missing(include_optional=True)
        )
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.install_missing_btn)
        btn_row.addWidget(self.install_all_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ── Refresh ───────────────────────────────────────────────────────

    @staticmethod
    def _get_type_meta(dep_type: str) -> tuple[str, QColor]:
        """Return (display_label, color) for a dependency type.

        Core is always known; optional groups are auto-assigned a colour
        on first encounter so new groups need no code change.
        """
        if dep_type in _DEP_TYPE_META:
            return _DEP_TYPE_META[dep_type]
        if dep_type not in _OPT_GROUP_META:
            idx = len(_OPT_GROUP_META)
            display = dep_type.replace("_", " ").title()
            colour = _OPT_GROUP_COLORS[idx % len(_OPT_GROUP_COLORS)]
            _OPT_GROUP_META[dep_type] = (display, colour)
        return _OPT_GROUP_META[dep_type]

    def _refresh(self):
        self.table.setRowCount(0)
        _OPT_GROUP_META.clear()
        raw = _load_declared_deps()
        self._data = []

        installed_count = 0
        for req_str, dep_type in raw:
            status, ver = _check_req(req_str)
            type_label, _ = self._get_type_meta(dep_type)
            self._data.append((req_str, type_label, status, ver, dep_type))
            if status == "installed":
                installed_count += 1

        # Sort: missing first, then mismatch, then installed
        sort_order = {"missing": 0, "mismatch": 1, "installed": 2}
        self._data.sort(key=lambda x: sort_order.get(x[2], 99))

        for req_str, type_label, status, ver, dep_type in self._data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            pkg_name = Requirement(req_str).name

            name_item = QTableWidgetItem(pkg_name)
            if status == "skipped":
                name_item.setForeground(QColor("#95a5a6"))
            self.table.setItem(row, 0, name_item)

            type_item = QTableWidgetItem(type_label)
            _, type_color = self._get_type_meta(dep_type)
            type_item.setForeground(type_color)
            self.table.setItem(row, 1, type_item)

            status_text = {
                "installed": self.tr("Installed"),
                "missing": self.tr("Missing"),
                "mismatch": self.tr("Version mismatch"),
                "skipped": self.tr("Not needed"),
            }.get(status, status)
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(_STATUS_COLORS.get(status, QColor("#888")))
            self.table.setItem(row, 2, status_item)

            # Version cell; for skipped deps show the marker as tooltip
            ver_item = QTableWidgetItem(ver or "—")
            if status == "skipped":
                ver_item.setForeground(QColor("#95a5a6"))
                # Extract marker text from original req_str for transparency
                marker_str = Requirement(req_str).marker
                if marker_str:
                    ver_item.setToolTip(
                        self.tr("Only needed when: {marker}").format(
                            marker=str(marker_str)
                        )
                    )
            self.table.setItem(row, 3, ver_item)

        total = sum(1 for d in self._data if d[2] != "skipped")
        missing = sum(1 for d in self._data if d[2] in ("missing", "mismatch"))
        skipped = sum(1 for d in self._data if d[2] == "skipped")
        if skipped:
            self.summary_label.setText(
                self.tr(
                    "{installed}/{total} installed, {missing} missing, {skipped} skipped (not needed here)"
                ).format(
                    installed=installed_count,
                    total=total,
                    missing=missing,
                    skipped=skipped,
                )
            )
        else:
            self.summary_label.setText(
                self.tr(
                    "{installed}/{total} installed, {missing} missing or mismatched"
                ).format(installed=installed_count, total=total, missing=missing)
            )
        self.install_missing_btn.setEnabled(missing > 0)

    # ── Install ───────────────────────────────────────────────────────

    def _install_missing(self, include_optional=False):
        missing = [
            d[0]
            for d in self._data
            if d[2] in ("missing", "mismatch") and (include_optional or d[4] == "core")
        ]
        if not missing:
            return

        self.install_missing_btn.setEnabled(False)
        self.install_all_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(missing))
        self.progress_bar.setValue(0)

        self._worker = _InstallWorker(missing, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_one.connect(self._on_finished_one)
        self._worker.all_done.connect(self._on_install_done)
        self._worker.start()

    def _on_progress(self, current, total):
        self.progress_bar.setValue(current)

    def _on_finished_one(self, req_name, success):
        LOGGER.info(f"{'Installed' if success else 'Failed'}: {req_name}")

    def _on_install_done(self):
        self.progress_bar.setVisible(False)
        self._refresh()
        self.install_missing_btn.setEnabled(True)
        self.install_all_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)


class DependencyDialog(QDialog):
    """Dialog wrapper for DependencyPanel (backward-compatible)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Dependencies"))
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = DependencyPanel(self)
        layout.addWidget(self.panel)
