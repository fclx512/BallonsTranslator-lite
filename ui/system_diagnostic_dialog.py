"""System diagnostic dialog — card-based health check for the entire project.

Categories as cards in a scroll area:
  - Environment (Python, startup mode, OS)
  - GPU status (hardware, PyTorch CUDA, onnxruntime)
  - Pipeline modules (load status, functional test)
  - Dependencies (summary + link to ToolsDialog)
"""

import platform

from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ── Theme helpers ──────────────────────────────────────────────────────────


def _theme_colors():
    """Adapt colors to dark/light mode via pcfg.darkmode."""
    dark = False
    try:
        from utils.config import pcfg

        dark = pcfg.darkmode
    except Exception:
        pass
    if dark:
        return {
            "card_border": "#444",
            "card_bg": "palette(base)",
            "sep": "#444",
            "log_border": "#555",
            "status_muted": "#888",
            "ok": "#2ecc71",
            "warn": "#f39c12",
            "err": "#e74c3c",
            "muted": "#95a5a6",
        }
    else:
        return {
            "card_border": "#ccc",
            "card_bg": "palette(base)",
            "sep": "#ddd",
            "log_border": "#ccc",
            "status_muted": "#95a5a6",
            "ok": "#2ecc71",
            "warn": "#f39c12",
            "err": "#e74c3c",
            "muted": "#95a5a6",
        }


def _c(status: str) -> str:
    tc = _theme_colors()
    return {"ok": tc["ok"], "warn": tc["warn"], "err": tc["err"], "muted": tc["muted"]}.get(
        status, tc["muted"]
    )


_ICON_OK = "✅"
_ICON_WARN = "⚠️"
_ICON_ERR = "❌"
_ICON_MUTED = "⚪"
_ICON_NA = "—"


def _icon(status: str) -> str:
    return {"ok": _ICON_OK, "warn": _ICON_WARN, "err": _ICON_ERR, "muted": _ICON_MUTED, "na": _ICON_NA}.get(
        status, "?"
    )


# ── Card widget ────────────────────────────────────────────────────────────


class _Card(QGroupBox):
    """A flat card with a title and grid body.

    Use ``add_row(key, value, status, widget)`` to populate rows.
    """

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        tc = _theme_colors()
        self.setFlat(True)
        self.setStyleSheet(
            f"QGroupBox {{ border: 1px solid {tc['card_border']}; border-radius: 6px; "
            f"background: {tc['card_bg']}; margin: 0; padding: 8px 4px 4px 4px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; "
            "padding: 0 4px; font-weight: bold; font-size: 13px; }"
        )
        self.setTitle(title)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(8, 12, 8, 4)
        self._grid.setColumnStretch(0, 0)  # icon
        self._grid.setColumnStretch(1, 0)  # label
        self._grid.setColumnStretch(2, 1)  # value
        self._grid.setColumnStretch(3, 0)  # action button
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        self._row_count = 0
        self._widget_refs: list[QWidget] = []  # track widgets for cleanup
        self.setLayout(self._grid)

    def add_row(
        self,
        label: str,
        value: str = "",
        status: str = "muted",
        action_widget: QWidget = None,
    ):
        """Add a row to the card.

        Args:
            label: Row label (bold, left side).
            value: Status text (right of label).
            status: ``"ok"``, ``"warn"``, ``"err"``, ``"muted"``, or ``"na"``.
            action_widget: Optional QPushButton or other widget at far right.
        """
        row = self._row_count

        # Status icon
        icon_label = QLabel(_icon(status))
        icon_label.setStyleSheet(f"color: {_c(status)}; font-size: 14px;")
        icon_label.setFixedWidth(20)
        self._grid.addWidget(icon_label, row, 0, Qt.AlignmentFlag.AlignCenter)

        # Label
        lbl = QLabel(label)
        f = lbl.font()
        f.setBold(False)
        lbl.setFont(f)
        self._grid.addWidget(lbl, row, 1)

        # Value
        val = QLabel(value)
        val.setWordWrap(True)
        val.setStyleSheet(f"color: {_c(status)};")
        self._grid.addWidget(val, row, 2)

        # Action
        if action_widget:
            self._grid.addWidget(action_widget, row, 3, Qt.AlignmentFlag.AlignRight)

        self._row_count += 1

    def add_widget(self, widget: QWidget, colspan: int = 4):
        """Add a full-width widget spanning all columns."""
        self._grid.addWidget(widget, self._row_count, 0, 1, colspan)
        self._widget_refs.append(widget)
        self._row_count += 1

    def remove_last_widgets(self, n: int = 1):
        """Remove last N full-width widgets added via add_widget."""
        for _ in range(n):
            if self._widget_refs:
                w = self._widget_refs.pop()
                self._grid.removeWidget(w)
                w.deleteLater()
                self._row_count -= 1

    def has_widgets(self) -> bool:
        return bool(self._widget_refs)


# ── Test worker thread ─────────────────────────────────────────────────────


class _ModuleTestWorker(QThread):
    """Run a functional test on a single module in a background thread."""

    finished = Signal(dict)  # result dict

    def __init__(self, stage: str, module_key: str, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.module_key = module_key

    def run(self):
        from utils.env_diagnostic import test_module_functional

        result = test_module_functional(self.stage, self.module_key)
        self.finished.emit(result)


# ── Main dialog ────────────────────────────────────────────────────────────


class SystemDiagnosticDialog(QDialog):
    """Card-based environment health check dialog.

    Signals:
        open_tools_requested(str): emitted when user clicks a link that
            should open the ToolsDialog.
        open_settings_requested(str): emitted to navigate to a settings page.
    """

    open_tools_requested = Signal(str)
    open_settings_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("System Diagnostic"))
        self.setMinimumSize(700, 520)
        self.resize(780, 600)
        self._test_workers: list[_ModuleTestWorker] = []
        # Track the currently-shown log widget per stage
        self._current_log_stage: str | None = None
        self._current_log_card: _Card | None = None
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        header_row = QHBoxLayout()
        title = QLabel(self.tr("\U0001f50d System Health Check"))
        f = title.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        title.setFont(f)
        header_row.addWidget(title)
        header_row.addStretch()

        self.run_btn = QPushButton(self.tr("Re-check"))
        self.run_btn.setMinimumHeight(30)
        self.run_btn.clicked.connect(self._refresh_all)
        header_row.addWidget(self.run_btn)
        layout.addLayout(header_row)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._scroll_widget)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._scroll_widget)
        layout.addWidget(scroll, 1)

        # Progress bar (indeterminate, hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximum(0)
        layout.addWidget(self.progress_bar)

        # Status bar
        tc = _theme_colors()
        self.status_label = QLabel()
        self.status_label.setStyleSheet(f"color: {tc['status_muted']}; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Populate
        self._refresh_all()

    # ── Refresh ──────────────────────────────────────────────────────────

    def _clear_cards(self):
        self._current_log_stage = None
        self._current_log_card = None
        for i in reversed(range(self._cards_layout.count())):
            item = self._cards_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

    def _refresh_all(self):
        self._clear_cards()
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText(self.tr("Running diagnostics…"))

        from utils.env_diagnostic import (
            check_module_status,
            dependency_summary,
            detect_gpu_info,
            run_diagnostic,
        )

        env_info = run_diagnostic()
        gpu_info = detect_gpu_info()
        modules = check_module_status()
        deps = dependency_summary()

        # ── Card: Environment ──
        env_card = _Card(self.tr("Runtime Environment"))
        py_ver = env_info.get("python_version", "?")
        py_path = env_info.get("python_path", "?")
        is_embedded = "ballontrans_pylibs_win" in py_path
        mode_str = (
            self.tr("Embedded (Path A)")
            if is_embedded
            else self.tr("System Python (Path B)")
        )
        env_card.add_row(self.tr("Python"), f"{py_ver.split()[0]} ({mode_str})", "ok")
        env_card.add_row(self.tr("Executable"), py_path, "muted")
        env_card.add_row(self.tr("OS"), platform.platform(), "ok")
        self._cards_layout.addWidget(env_card)

        # ── Card: GPU Status ──
        gpu_card = _Card(self.tr("GPU Status"))
        if gpu_info:
            gpu_card.add_row(self.tr("Graphics Card"), gpu_info["name"], "ok")
            gpu_card.add_row(self.tr("Generation"), gpu_info["generation"], "ok")
        else:
            gpu_card.add_row(self.tr("Graphics Card"), self.tr("Not detected"), "muted")

        torch_info = env_info.get("torch", {})
        if torch_info.get("available"):
            cuda_avail = torch_info.get("cuda_available", False)
            cuda_ver = torch_info.get("cuda_version", "")
            cuda_str = f"CUDA {cuda_ver}" if cuda_ver else self.tr("not available")
            gpu_card.add_row(
                self.tr("PyTorch"),
                f"{torch_info.get('version', '?')} ({cuda_str})",
                "ok" if cuda_avail else "warn",
            )
        else:
            gpu_card.add_row(self.tr("PyTorch"), self.tr("Not installed"), "err")

        onnx_cuda = False
        try:
            import onnxruntime

            onnx_cuda = "CUDA" in onnxruntime.get_available_providers()
        except Exception:
            pass
        gpu_card.add_row(
            self.tr("onnxruntime CUDA"),
            self.tr("Available") if onnx_cuda else self.tr("Not available"),
            "ok" if onnx_cuda else "warn",
        )

        if gpu_info and not torch_info.get("cuda_available"):
            install_btn = QPushButton(self.tr("Install CUDA PyTorch"))
            install_btn.setStyleSheet("font-size: 11px; padding: 2px 6px;")
            install_btn.clicked.connect(self._on_install_cuda)
            gpu_card.add_row(
                "", self.tr("GPU detected but CUDA PyTorch not available"),
                "warn", install_btn,
            )
        self._cards_layout.addWidget(gpu_card)

        # ── Card: Pipeline Modules ──
        mod_card = _Card(self.tr("Pipeline Modules"))

        for m in modules:
            _stage_to_nav = {
                "textdetector": "detect",
                "ocr": "ocr",
                "translator": "trans",
                "inpainter": "inpaint",
            }
            nav_key = _stage_to_nav.get(m["stage"], m["stage"])

            stage_label = {
                "textdetector": self.tr("Text Detection"),
                "ocr": "OCR",
                "translator": self.tr("Translation"),
                "inpainter": self.tr("Inpainting"),
            }.get(m["stage"], m["stage"])

            key = m["active_key"]
            if not key or key == "None":
                mod_card.add_row(stage_label, self.tr("No module configured"), "muted")
                continue

            if not m["enabled"]:
                mod_card.add_row(stage_label, f"{key} ({self.tr('disabled')})", "muted")
                continue

            if m["resolved"]:
                row_status = "ok"
                status_text = f"{key} ({self.tr('loaded')})"
                action = QPushButton(self.tr("Test"))
                action.setStyleSheet("font-size: 11px; padding: 2px 6px;")
                action.clicked.connect(
                    lambda checked, stg=m["stage"], mk=m["active_key"], btn=action:
                        self._run_module_test(stg, mk, btn)
                )
            elif m["error"]:
                row_status = "err"
                status_text = f"{key} ({self.tr('error')})"
                action = QPushButton(self.tr("Settings →"))
                action.setStyleSheet("font-size: 11px; padding: 2px 6px;")
                action.clicked.connect(
                    lambda checked, s=nav_key: self._jump_to_settings(s)
                )
            else:
                row_status = "muted"
                status_text = f"{key}"
                action = None

            mod_card.add_row(stage_label, status_text, row_status, action)

            # Error detail row
            if m.get("error"):
                err_label = QLabel(m["error"])
                err_label.setWordWrap(True)
                err_label.setStyleSheet(
                    f"color: {_c('err')}; font-size: 11px; padding: 2px 0 2px 24px;"
                )
                mod_card.add_widget(err_label)

        self._cards_layout.addWidget(mod_card)

        # ── Card: Dependencies ──
        dep_card = _Card(self.tr("Dependencies"))
        dep_total = deps.get("total", 0)
        dep_installed = deps.get("installed", 0)
        dep_missing = deps.get("missing", 0)
        dep_mismatched = deps.get("mismatched", 0)
        dep_skipped = deps.get("skipped", 0)

        if dep_total:
            parts = []
            parts.append(self.tr("{installed}/{total} installed").format(installed=dep_installed, total=dep_total))
            if dep_missing:
                parts.append(self.tr("{n} missing").format(n=dep_missing))
            if dep_mismatched:
                parts.append(self.tr("{n} version mismatch").format(n=dep_mismatched))
            if dep_skipped:
                parts.append(self.tr("{n} not needed (skipped)").format(n=dep_skipped))
            dep_card.add_row(self.tr("pip packages"), ", ".join(parts), "ok" if not (dep_missing or dep_mismatched) else "warn")
        else:
            dep_card.add_row(self.tr("pip packages"), self.tr("Could not read pyproject.toml"), "muted")

        tools_btn = QPushButton(self.tr("Details →"))
        tools_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        tools_btn.clicked.connect(lambda: self.open_tools_requested.emit("deps"))
        dep_card.add_row(self.tr(""), self.tr("Open dependency manager for details"), "muted", tools_btn)

        model_btn = QPushButton(self.tr("Check →"))
        model_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        model_btn.clicked.connect(lambda: self.open_tools_requested.emit("models"))
        dep_card.add_row(self.tr("Model Files"), self.tr("Verify downloaded model files"), "muted", model_btn)

        self._cards_layout.addWidget(dep_card)
        self._cards_layout.addStretch()

        # Done
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(self.tr("Diagnostic complete."))

    # ── Module test ──────────────────────────────────────────────────────

    def _run_module_test(self, stage: str, module_key: str, test_btn: QPushButton):
        test_btn.setEnabled(False)
        test_btn.setText(self.tr("Testing…"))
        self.progress_bar.setVisible(True)
        self.status_label.setText(self.tr("Testing {module}…").format(module=module_key))

        # Remove any previous test log so only one is ever visible
        if self._current_log_card is not None and self._current_log_card.has_widgets():
            self._current_log_card.remove_last_widgets(1)
        self._current_log_stage = None
        self._current_log_card = None

        worker = _ModuleTestWorker(stage, module_key, self)
        self._test_workers.append(worker)
        worker.finished.connect(lambda res: self._on_test_result(res, test_btn, stage))
        worker.start()

    def _on_test_result(self, result: dict, test_btn: QPushButton, stage: str):
        test_btn.setEnabled(True)
        test_btn.setText(self.tr("Test"))
        self.progress_bar.setVisible(False)

        # Find parent _Card
        parent_card = test_btn.parent()
        while parent_card is not None and not isinstance(parent_card, _Card):
            parent_card = parent_card.parent()

        success = result.get("success", False)
        output = result.get("output", "")
        duration = result.get("duration_ms", 0)
        status_text = (
            self.tr("Passed ({duration}ms)").format(duration=duration)
            if success
            else self.tr("Failed")
        )

        tc = _theme_colors()
        log_box = QPlainTextEdit()
        log_box.setReadOnly(True)
        log_box.setMaximumHeight(180)
        log_box.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 11px; "
            f"color: {tc['ok'] if success else tc['err']}; "
            f"background: palette(window); border: 1px solid {tc['log_border']}; "
            f"border-radius: 3px; padding: 2px;"
        )
        log_box.setPlainText(f">>> {status_text}\n{'─' * 40}\n{output}")

        if parent_card is not None:
            # Remove old log if it somehow survived (e.g. rapid double-click)
            if parent_card.has_widgets():
                for w in reversed(parent_card._widget_refs):
                    if isinstance(w, QPlainTextEdit):
                        parent_card.remove_last_widgets(1)
                        break

            parent_card.add_widget(log_box)
            self._current_log_stage = stage
            self._current_log_card = parent_card

        self.status_label.setText(
            self.tr("Test: {status} ({duration}ms)").format(status=status_text, duration=duration)
        )

    # ── Navigation ───────────────────────────────────────────────────────

    def _jump_to_settings(self, stage: str):
        self.open_settings_requested.emit(stage)
        self.accept()

    def _on_install_cuda(self):
        """Launch install_cuda.bat in a subprocess."""
        import subprocess
        import sys

        from utils import shared

        bat_path = shared.PROGRAM_PATH / "install_cuda.bat"
        if bat_path.exists():
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["cmd", "/c", str(bat_path)])
                else:
                    subprocess.Popen(["bash", str(bat_path)])
                self.status_label.setText(self.tr("Launched CUDA installer."))
            except Exception as e:
                self.status_label.setText(self.tr("Failed to launch installer: {err}").format(err=e))
        else:
            self.status_label.setText(self.tr("install_cuda.bat not found in project root."))
