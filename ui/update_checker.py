"""
In-app update checker and About dialog.
Background git operations (fetch + reset --hard) via QThread.
"""

import subprocess
import sys

from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from utils.update_cache import (
    human_readable_last_check,
    record_check,
)


class UpdateThread(QThread):
    """Background thread for git fetch/compare or git reset --hard.

    Two modes:
      mode='check'   →  git fetch + compare commits, emit check_complete
      mode='update'  →  git fetch + reset --hard origin/branch, emit update_complete
    """

    check_complete = Signal(dict)
    update_complete = Signal(dict)

    def __init__(
        self, git_path, branch, repo_path, mode="check", cached_remote_commit=None
    ):
        super().__init__()
        self._git = git_path
        self._branch = branch
        self._repo = repo_path
        self._mode = mode
        self._cached_remote_commit = cached_remote_commit

    # ── helpers ────────────────────────────────────────────────

    def _run(self, args, timeout=60):
        """Run a git command, return (returncode, stdout, stderr)."""
        try:
            proc = subprocess.run(
                [self._git] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._repo,
                encoding="utf-8",
                errors="replace",
            )
            out = proc.stdout or ""
            err = proc.stderr or ""
            return proc.returncode, out.strip(), err.strip()
        except FileNotFoundError:
            return -1, "", ""
        except subprocess.TimeoutExpired:
            return -1, "", self.tr("Command timed out")
        except (UnicodeDecodeError, ValueError):
            return -1, "", self.tr("Encoding error")

    # ── run dispatch ──────────────────────────────────────────

    def run(self):
        if self._mode == "check":
            self._do_check()
        elif self._mode == "cached_check":
            self._do_cached_check()
        elif self._mode == "update":
            self._do_update()

    # ── check phase: fetch + compare ──────────────────────────

    def _do_check(self):
        rc, _, _ = self._run(["--version"], timeout=10)
        if rc != 0:
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr(
                        "Git is not available.\n"
                        "Please install Git from https://git-scm.com/downloads"
                    ),
                }
            )
            return

        if getattr(sys, "frozen", False):
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr(
                        "Update is not available in portable/exe builds.\n"
                        "Please download the latest version from GitHub."
                    ),
                }
            )
            return

        rc, _, stderr = self._run(["fetch", "origin", self._branch], timeout=60)
        if rc != 0:
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr("Failed to contact GitHub.\n{err}").replace(
                        "{err}", stderr or ""
                    ),
                }
            )
            return

        rc, current_full, _ = self._run(["rev-parse", "HEAD"])
        current_short = current_full[:8] if rc == 0 else "?"

        rc, latest_full, _ = self._run(["rev-parse", f"origin/{self._branch}"])
        if rc != 0:
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr("Failed to check remote status."),
                }
            )
            return

        latest_short = latest_full[:8]

        if current_full == latest_full:
            self.check_complete.emit(
                {
                    "status": "up_to_date",
                    "current_commit": current_short,
                    "latest_commit": latest_short,
                    "latest_commit_full": latest_full,
                }
            )
        else:
            rc, log, _ = self._run(
                ["log", "HEAD..origin/main", "--oneline", "--no-merges", "-20"],
                timeout=30,
            )
            self.check_complete.emit(
                {
                    "status": "update_available",
                    "current_commit": current_short,
                    "latest_commit": latest_short,
                    "latest_commit_full": latest_full,
                    "changelog": log if rc == 0 else "",
                }
            )

    # ── cached check phase: local-only comparison ──────────────

    def _do_cached_check(self):
        """Local-only check: compare HEAD against previously cached remote commit.
        No network call."""
        rc, _, _ = self._run(["--version"], timeout=10)
        if rc != 0:
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr(
                        "Git is not available.\n"
                        "Please install Git from https://git-scm.com/downloads"
                    ),
                }
            )
            return

        if getattr(sys, "frozen", False):
            self.check_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr(
                        "Update is not available in portable/exe builds.\n"
                        "Please download the latest version from GitHub."
                    ),
                }
            )
            return

        rc, current_full, _ = self._run(["rev-parse", "HEAD"])
        current_short = current_full[:8] if rc == 0 else "?"

        cached = self._cached_remote_commit or ""

        if current_full == cached:
            self.check_complete.emit(
                {
                    "status": "up_to_date",
                    "current_commit": current_short,
                    "latest_commit": cached[:8] if cached else "?",
                    "latest_commit_full": cached,
                }
            )
        else:
            self.check_complete.emit(
                {
                    "status": "update_available",
                    "current_commit": current_short,
                    "latest_commit": cached[:8] if cached else "?",
                    "latest_commit_full": cached,
                    "changelog": "",
                }
            )

    # ── update phase: fetch + hard reset ──────────────────────

    def _do_update(self):
        rc, _, stderr = self._run(["fetch", "origin", self._branch], timeout=60)
        if rc != 0:
            self.update_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr("Failed to fetch.\n{err}").replace(
                        "{err}", stderr or ""
                    ),
                }
            )
            return

        rc, _, stderr = self._run(
            ["reset", "--hard", f"origin/{self._branch}"],
            timeout=30,
        )
        if rc != 0:
            self.update_complete.emit(
                {
                    "status": "error",
                    "error_msg": self.tr("Failed to apply update.\n{err}").replace(
                        "{err}", stderr or ""
                    ),
                }
            )
            return

        rc, new_head, _ = self._run(["rev-parse", "HEAD"])
        self.update_complete.emit(
            {
                "status": "success",
                "commit": new_head.strip() if rc == 0 else "",
            }
        )


class AboutDialog(QDialog):
    """About dialog with version info and embedded update check section."""

    restart_requested = Signal()

    def __init__(self, parent, version, commit, branch, git_path=None, repo_path=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("About"))
        self.setMinimumWidth(520)
        self.setModal(True)

        self._git_path = git_path
        self._branch = branch
        self._repo_path = repo_path

        short = commit[:8] if commit and commit != "<none>" else "?"
        self._version = version
        self._commit = short
        self._full_commit = commit
        self._latest_short = ""
        self._latest_full = ""
        self._thread = None
        self._state = "idle"

        self._build_ui(short, branch)
        self._set_state_idle()

    # ── build ──────────────────────────────────────────────────

    def _build_ui(self, short_commit, branch):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(32, 24, 32, 24)

        # ── About section ──────────────────────────────────────

        title = QLabel("BallonsTranslator-lite")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        layout.addSpacing(8)

        info = (
            self.tr("Version")
            + ": "
            + self._version
            + "<br>"
            + self.tr("Commit")
            + ": "
            + short_commit
            + "<br>"
            + self.tr("Branch")
            + ": "
            + branch
        )
        info_label = QLabel(info)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)

        layout.addSpacing(8)

        link = QLabel(
            '<a href="https://github.com/fclx512/BallonsTranslator" '
            'style="color: #42a5f5;">'
            "github.com/fclx512/BallonsTranslator</a>"
        )
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        layout.addSpacing(16)

        # ── Separator ──────────────────────────────────────────

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        layout.addSpacing(12)

        # ── Update section ─────────────────────────────────────

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 28px;")
        layout.addWidget(self.icon_label)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(6)
        layout.addWidget(self.progress_bar)

        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #888;")
        self.info_label.hide()
        layout.addWidget(self.info_label)

        self.changelog_label = QLabel(self.tr("Recent changes:"))
        self.changelog_label.hide()
        layout.addWidget(self.changelog_label)

        self.changelog_text = QTextEdit()
        self.changelog_text.setReadOnly(True)
        self.changelog_text.setFixedHeight(180)
        self.changelog_text.hide()
        layout.addWidget(self.changelog_text)

        self.warning_label = QLabel("⚠ " + self.tr("Local changes will be overwritten"))
        self.warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warning_label.setStyleSheet("color: #e68a00; font-weight: bold;")
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.action_btn = QPushButton()
        self.action_btn.clicked.connect(self._on_action)
        self.action_btn.hide()
        btn_layout.addWidget(self.action_btn)

        layout.addLayout(btn_layout)

    # ── idle state ─────────────────────────────────────────────

    def _set_state_idle(self):
        """Initial state — last-checked time and Check Now button."""
        self._state = "idle"
        self.icon_label.setText("")
        self.icon_label.setStyleSheet("font-size: 28px;")
        self.status_label.setText(self.tr("Check for updates"))
        self.progress_bar.hide()
        self.info_label.setText(
            self.tr("Last checked: {time}").replace(
                "{time}", human_readable_last_check()
            )
        )
        self.info_label.setStyleSheet("color: #888;")
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr("Check Now"))
        self.action_btn.show()
        self.cancel_btn.setText(self.tr("Cancel"))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    def _set_state_checking(self):
        self._state = "checking"
        self.icon_label.setText("⟳")
        self.icon_label.setStyleSheet("font-size: 28px;")
        self.status_label.setText(self.tr("Checking for updates..."))
        self.progress_bar.show()
        self.info_label.hide()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.hide()
        self.cancel_btn.setText(self.tr("Cancel"))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    def _set_state_up_to_date(self):
        self._state = "up_to_date"
        self.icon_label.setText("✓")
        self.icon_label.setStyleSheet("font-size: 28px; color: #4caf50;")
        self.status_label.setText(self.tr("You are running the latest version."))
        self.progress_bar.hide()
        self.info_label.hide()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr("OK"))
        self.action_btn.show()
        self.cancel_btn.hide()

    def _set_state_update_available(self, changelog=""):
        self._state = "update_available"
        self.icon_label.setText("▲")
        self.icon_label.setStyleSheet("font-size: 28px; color: #2196f3;")
        self.status_label.setText(self.tr("A new version is available!"))
        self.progress_bar.hide()
        self.info_label.hide()

        if changelog:
            self.changelog_label.show()
            self.changelog_text.setPlainText(changelog)
            self.changelog_text.show()
        else:
            self.changelog_label.hide()
            self.changelog_text.hide()

        self.warning_label.show()
        self.action_btn.setText(self.tr("Update Now"))
        self.action_btn.show()
        self.cancel_btn.setText(self.tr("Cancel"))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    def _set_state_updating(self):
        self._state = "updating"
        self.icon_label.setText("⟳")
        self.status_label.setText(self.tr("Updating..."))
        self.progress_bar.show()
        self.info_label.hide()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.hide()
        self.cancel_btn.setText(self.tr("Working..."))
        self.cancel_btn.setEnabled(False)

    def _set_state_error(self, msg):
        self._state = "error"
        self.icon_label.setText("✗")
        self.icon_label.setStyleSheet("font-size: 28px; color: #f44336;")
        self.status_label.setText(self.tr("Update check failed"))
        self.progress_bar.hide()
        self.info_label.setText(msg)
        self.info_label.setStyleSheet("color: #888;")
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr("OK"))
        self.action_btn.show()
        self.cancel_btn.hide()

    def _set_state_restart_prompt(self):
        self._state = "restart_prompt"
        self.icon_label.setText("✓")
        self.icon_label.setStyleSheet("font-size: 28px; color: #4caf50;")
        self.status_label.setText(self.tr("Update complete!"))
        self.progress_bar.hide()
        self.info_label.setText(self.tr("Restart to apply changes?"))
        self.info_label.setStyleSheet("color: #888;")
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr("Restart Now"))
        self.action_btn.show()
        self.cancel_btn.setText(self.tr("Later"))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    # ── flow ──────────────────────────────────────────────────

    def _on_check_now(self):
        self._set_state_checking()
        self._thread = UpdateThread(
            self._git_path, self._branch, self._repo_path, mode="check"
        )
        self._thread.check_complete.connect(self._on_check_complete)
        self._thread.start()

    def _on_check_complete(self, result):
        status = result["status"]
        self._latest_short = result.get("latest_commit", "")
        self._latest_full = result.get("latest_commit_full", "")
        if self._latest_full:
            record_check(self._latest_full)
        if status == "up_to_date":
            self._set_state_up_to_date()
        elif status == "update_available":
            self._set_state_update_available(result.get("changelog", ""))
        else:
            self._set_state_error(result.get("error_msg", ""))
        self._thread = None

    def _start_update(self):
        self._set_state_updating()
        self._thread = UpdateThread(
            self._git_path, self._branch, self._repo_path, mode="update"
        )
        self._thread.update_complete.connect(self._on_update_complete)
        self._thread.start()

    def _on_update_complete(self, result):
        if result["status"] == "success":
            new_commit = result.get("commit", "")
            if new_commit:
                record_check(new_commit)
            self._set_state_restart_prompt()
        else:
            self._set_state_error(result.get("error_msg", ""))
            self.cancel_btn.setEnabled(True)
        self._thread = None

    def _on_action(self):
        if self._state == "idle":
            self._on_check_now()
        elif self._state == "update_available":
            self._start_update()
        elif self._state == "restart_prompt":
            self.restart_requested.emit()
            self.accept()
        else:
            self.accept()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)
