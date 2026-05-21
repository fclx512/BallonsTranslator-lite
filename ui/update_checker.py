"""
In-app update checker and About dialog.
Background git operations (fetch + reset --hard) via QThread.
"""

import subprocess
import sys

from qtpy.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QProgressBar,
)
from qtpy.QtCore import Qt, QThread, Signal


class UpdateThread(QThread):
    """Background thread for git fetch/compare or git reset --hard.

    Two modes:
      mode='check'   →  git fetch + compare commits, emit check_complete
      mode='update'  →  git fetch + reset --hard origin/branch, emit update_complete
    """
    check_complete = Signal(dict)
    update_complete = Signal(dict)

    def __init__(self, git_path, branch, repo_path, mode='check'):
        super().__init__()
        self._git = git_path
        self._branch = branch
        self._repo = repo_path
        self._mode = mode

    # ── helpers ────────────────────────────────────────────────

    def _run(self, args, timeout=60):
        """Run a git command, return (returncode, stdout, stderr)."""
        try:
            proc = subprocess.run(
                [self._git] + args,
                capture_output=True, text=True, timeout=timeout,
                cwd=self._repo,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except FileNotFoundError:
            return -1, '', ''
        except subprocess.TimeoutExpired:
            return -1, '', self.tr('Command timed out')

    # ── run dispatch ──────────────────────────────────────────

    def run(self):
        if self._mode == 'check':
            self._do_check()
        elif self._mode == 'update':
            self._do_update()

    # ── check phase: fetch + compare ──────────────────────────

    def _do_check(self):
        rc, _, _ = self._run(['--version'], timeout=10)
        if rc != 0:
            self.check_complete.emit({
                'status': 'error',
                'error_msg': self.tr(
                    'Git is not available.\n'
                    'Please install Git from https://git-scm.com/downloads'),
            })
            return

        if getattr(sys, 'frozen', False):
            self.check_complete.emit({
                'status': 'error',
                'error_msg': self.tr(
                    'Update is not available in portable/exe builds.\n'
                    'Please download the latest version from GitHub.'),
            })
            return

        rc, _, stderr = self._run(['fetch', 'origin', self._branch], timeout=60)
        if rc != 0:
            self.check_complete.emit({
                'status': 'error',
                'error_msg': self.tr('Failed to contact GitHub.\n{err}')
                               .replace('{err}', stderr or ''),
            })
            return

        rc, current, _ = self._run(['rev-parse', 'HEAD'])
        current = current[:8] if rc == 0 else '?'

        rc, latest, _ = self._run(['rev-parse', f'origin/{self._branch}'])
        if rc != 0:
            self.check_complete.emit({
                'status': 'error',
                'error_msg': self.tr('Failed to check remote status.'),
            })
            return

        latest_short = latest[:8]

        if current == latest[:40]:
            self.check_complete.emit({
                'status': 'up_to_date',
                'current_commit': current,
                'latest_commit': latest_short,
            })
        else:
            rc, log, _ = self._run(
                ['log', 'HEAD..origin/main', '--oneline', '--no-merges', '-20'],
                timeout=30,
            )
            self.check_complete.emit({
                'status': 'update_available',
                'current_commit': current,
                'latest_commit': latest_short,
                'changelog': log if rc == 0 else '',
            })

    # ── update phase: fetch + hard reset ──────────────────────

    def _do_update(self):
        rc, _, stderr = self._run(['fetch', 'origin', self._branch], timeout=60)
        if rc != 0:
            self.update_complete.emit({
                'status': 'error',
                'error_msg': self.tr('Failed to fetch.\n{err}')
                               .replace('{err}', stderr or ''),
            })
            return

        rc, _, stderr = self._run(
            ['reset', '--hard', f'origin/{self._branch}'], timeout=30,
        )
        if rc != 0:
            self.update_complete.emit({
                'status': 'error',
                'error_msg': self.tr('Failed to apply update.\n{err}')
                               .replace('{err}', stderr or ''),
            })
            return

        self.update_complete.emit({'status': 'success'})


class UpdateCheckDialog(QDialog):
    """Modal dialog: check → show result → optionally update → restart."""

    restart_requested = Signal()

    def __init__(self, parent, git_path, branch, repo_path,
                 current_version, current_commit):
        super().__init__(parent)
        self.setWindowTitle(self.tr('Check for Updates'))
        self.setMinimumWidth(460)
        self.setModal(True)

        self._git_path = git_path
        self._branch = branch
        self._repo_path = repo_path
        self._version = current_version
        short = current_commit[:8] if current_commit and current_commit != '<none>' else '?'
        self._commit = short
        self._latest_short = ''
        self._thread = None
        self._state = 'checking'

        self._build_ui()
        self._start_check()

    # ── build ──────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet('font-size: 28px;')
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
        self.info_label.setStyleSheet('color: #888;')
        self.info_label.hide()
        layout.addWidget(self.info_label)

        self.changelog_label = QLabel(self.tr('Recent changes:'))
        self.changelog_label.hide()
        layout.addWidget(self.changelog_label)

        self.changelog_text = QTextEdit()
        self.changelog_text.setReadOnly(True)
        self.changelog_text.setFixedHeight(180)
        self.changelog_text.hide()
        layout.addWidget(self.changelog_text)

        self.warning_label = QLabel(
            '⚠ ' + self.tr('Local changes will be overwritten'))
        self.warning_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warning_label.setStyleSheet('color: #e68a00; font-weight: bold;')
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton(self.tr('Cancel'))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.action_btn = QPushButton()
        self.action_btn.clicked.connect(self._on_action)
        self.action_btn.hide()
        btn_layout.addWidget(self.action_btn)

        layout.addLayout(btn_layout)

    # ── state transitions ─────────────────────────────────────

    def _set_state_checking(self):
        self._state = 'checking'
        self.icon_label.setText('⟳')
        self.icon_label.setStyleSheet('font-size: 28px;')
        self.status_label.setText(self.tr('Checking for updates...'))
        self.progress_bar.show()
        self.info_label.hide()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.hide()
        self.cancel_btn.setText(self.tr('Cancel'))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    def _set_state_up_to_date(self):
        self._state = 'up_to_date'
        self.icon_label.setText('✓')
        self.icon_label.setStyleSheet('font-size: 28px; color: #4caf50;')
        self.status_label.setText(self.tr('You are running the latest version.'))
        self.progress_bar.hide()
        self.info_label.setText(
            self.tr('Version {ver} (commit {commit})')
            .replace('{ver}', self._version)
            .replace('{commit}', self._commit))
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr('OK'))
        self.action_btn.show()
        self.cancel_btn.hide()

    def _set_state_update_available(self, changelog=''):
        self._state = 'update_available'
        self.icon_label.setText('▲')
        self.icon_label.setStyleSheet('font-size: 28px; color: #2196f3;')
        self.status_label.setText(self.tr('A new version is available!'))
        self.progress_bar.hide()
        self.info_label.setText(
            self.tr('Current: {ver} ({cur})  ->  Latest: {latest}')
            .replace('{ver}', self._version)
            .replace('{cur}', self._commit)
            .replace('{latest}', self._latest_short))
        self.info_label.show()

        if changelog:
            self.changelog_label.show()
            self.changelog_text.setPlainText(changelog)
            self.changelog_text.show()
        else:
            self.changelog_label.hide()
            self.changelog_text.hide()

        self.warning_label.show()
        self.action_btn.setText(self.tr('Update Now'))
        self.action_btn.show()
        self.cancel_btn.setText(self.tr('Cancel'))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    def _set_state_updating(self):
        self._state = 'updating'
        self.icon_label.setText('⟳')
        self.status_label.setText(self.tr('Updating...'))
        self.progress_bar.show()
        self.info_label.hide()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.hide()
        self.cancel_btn.setText(self.tr('Working...'))
        self.cancel_btn.setEnabled(False)

    def _set_state_error(self, msg):
        self._state = 'error'
        self.icon_label.setText('✗')
        self.icon_label.setStyleSheet('font-size: 28px; color: #f44336;')
        self.status_label.setText(self.tr('Update check failed'))
        self.progress_bar.hide()
        self.info_label.setText(msg)
        self.info_label.setStyleSheet('color: #888;')
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr('OK'))
        self.action_btn.show()
        self.cancel_btn.hide()

    def _set_state_restart_prompt(self):
        self._state = 'restart_prompt'
        self.icon_label.setText('✓')
        self.icon_label.setStyleSheet('font-size: 28px; color: #4caf50;')
        self.status_label.setText(self.tr('Update complete!'))
        self.progress_bar.hide()
        self.info_label.setText(self.tr('Restart to apply changes?'))
        self.info_label.setStyleSheet('color: #888;')
        self.info_label.show()
        self.changelog_label.hide()
        self.changelog_text.hide()
        self.warning_label.hide()
        self.action_btn.setText(self.tr('Restart Now'))
        self.action_btn.show()
        self.cancel_btn.setText(self.tr('Later'))
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)

    # ── flow ──────────────────────────────────────────────────

    def _start_check(self):
        self._set_state_checking()
        self._thread = UpdateThread(
            self._git_path, self._branch, self._repo_path, mode='check')
        self._thread.check_complete.connect(self._on_check_complete)
        self._thread.start()

    def _on_check_complete(self, result):
        status = result['status']
        if status == 'up_to_date':
            self._set_state_up_to_date()
        elif status == 'update_available':
            self._latest_short = result.get('latest_commit', '')
            self._set_state_update_available(result.get('changelog', ''))
        else:
            self._set_state_error(result.get('error_msg', ''))
        self._thread = None

    def _start_update(self):
        self._set_state_updating()
        self._thread = UpdateThread(
            self._git_path, self._branch, self._repo_path, mode='update')
        self._thread.update_complete.connect(self._on_update_complete)
        self._thread.start()

    def _on_update_complete(self, result):
        if result['status'] == 'success':
            self._set_state_restart_prompt()
        else:
            self._set_state_error(result.get('error_msg', ''))
            self.cancel_btn.setEnabled(True)
        self._thread = None

    def _on_action(self):
        if self._state == 'update_available':
            self._start_update()
        elif self._state == 'restart_prompt':
            self.restart_requested.emit()
            self.accept()
        else:
            # up_to_date or error → dismiss
            self.accept()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


class AboutDialog(QDialog):
    """Minimal About dialog — version, commit, branch, GitHub link."""

    def __init__(self, parent, version, commit, branch):
        super().__init__(parent)
        self.setWindowTitle(self.tr('About'))
        self.setFixedSize(340, 210)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(32, 24, 32, 24)

        title = QLabel('BallonsTranslator-lite')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet('font-size: 18px; font-weight: bold;')
        layout.addWidget(title)

        layout.addSpacing(8)

        info = (self.tr('Version') + ': ' + version + '<br>' +
                self.tr('Commit') + ': ' + commit + '<br>' +
                self.tr('Branch') + ': ' + branch)
        info_label = QLabel(info)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)

        layout.addSpacing(8)

        link = QLabel(
            '<a href="https://github.com/fclx512/BallonsTranslator" '
            'style="color: #42a5f5;">'
            'github.com/fclx512/BallonsTranslator</a>')
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        layout.addStretch()

        ok_btn = QPushButton(self.tr('OK'))
        ok_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
