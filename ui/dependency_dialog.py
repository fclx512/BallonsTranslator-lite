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

    *type* is ``"core"`` or ``"gpu"``.
    """
    pp = Path(shared.PROGRAM_PATH) / "pyproject.toml"
    if not pp.exists() or tomllib is None:
        return []

    with open(pp, "rb") as f:
        data = tomllib.load(f)

    deps: list[tuple[str, str]] = []
    for dep in data.get("project", {}).get("dependencies", []):
        deps.append((dep, "core"))
    for dep in (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("gpu", [])
    ):
        deps.append((dep, "gpu"))
    return deps


# ── Helpers ───────────────────────────────────────────────────────────

_DEP_TYPE_META = {
    "core": ("Core", QColor("#4a9eff")),
    "gpu": ("GPU (optional)", QColor("#9b59b6")),
}

_STATUS_COLORS = {
    "installed": QColor("#27ae60"),
    "missing": QColor("#e74c3c"),
    "mismatch": QColor("#f39c12"),
}


def _installed_version(req_name: str) -> str:
    try:
        dist = importlib_metadata.distribution(canonicalize_name(req_name))
        return dist.version
    except importlib_metadata.PackageNotFoundError:
        return ""


def _check_req(req_str: str) -> tuple[str, str]:
    """Return (status, version) for a requirement string."""
    req = Requirement(req_str)
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

    def run(self):
        python = sys.executable
        total = len(self.reqs)
        for i, req in enumerate(self.reqs):
            try:
                subprocess.run(
                    [python, "-m", "uv", "pip", "install", req, "--prefer-binary"],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                self.finished_one.emit(req, True)
            except Exception:
                self.finished_one.emit(req, False)
            self.progress.emit(i + 1, total)
        self.all_done.emit()


# ── Dialog ────────────────────────────────────────────────────────────


class DependencyDialog(QDialog):
    """Visual dependency list with install status and one-click install."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Dependencies"))
        self.setMinimumSize(640, 420)
        self._data: list[tuple[str, str, str, str, str]] = []
        self._build_ui()
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.tr("Package"),
            self.tr("Type"),
            self.tr("Status"),
            self.tr("Version"),
        ])
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
            lambda: self._install_missing(include_gpu=False)
        )
        self.install_all_btn = QPushButton(self.tr("Install All (incl. GPU)"))
        self.install_all_btn.clicked.connect(
            lambda: self._install_missing(include_gpu=True)
        )
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.install_missing_btn)
        btn_row.addWidget(self.install_all_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ── Refresh ───────────────────────────────────────────────────────

    def _refresh(self):
        self.table.setRowCount(0)
        raw = _load_declared_deps()
        self._data = []

        installed_count = 0
        for req_str, dep_type in raw:
            status, ver = _check_req(req_str)
            type_label, _ = _DEP_TYPE_META.get(dep_type, (dep_type, QColor("#888")))
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

            self.table.setItem(row, 0, QTableWidgetItem(pkg_name))

            type_item = QTableWidgetItem(type_label)
            _, type_color = _DEP_TYPE_META.get(dep_type, ("", QColor("#888")))
            type_item.setForeground(type_color)
            self.table.setItem(row, 1, type_item)

            status_text = {
                "installed": self.tr("Installed"),
                "missing": self.tr("Missing"),
                "mismatch": self.tr("Version mismatch"),
            }.get(status, status)
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(_STATUS_COLORS.get(status, QColor("#888")))
            self.table.setItem(row, 2, status_item)

            self.table.setItem(row, 3, QTableWidgetItem(ver or "—"))

        total = len(self._data)
        missing = sum(1 for d in self._data if d[2] in ("missing", "mismatch"))
        self.summary_label.setText(
            self.tr("{installed}/{total} installed, {missing} missing or mismatched").format(
                installed=installed_count, total=total, missing=missing
            )
        )
        self.install_missing_btn.setEnabled(missing > 0)

    # ── Install ───────────────────────────────────────────────────────

    def _install_missing(self, include_gpu=False):
        missing = [
            d[0]
            for d in self._data
            if d[2] in ("missing", "mismatch")
            and (include_gpu or d[4] != "gpu")
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
