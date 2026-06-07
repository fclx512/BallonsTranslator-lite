"""Network & mirror settings dialog.

Pick a source from a dropdown — no manual URL entry needed for
normal use.  Advanced options (extra pip index, custom HF endpoint)
are collapsed behind a checkbox.
"""

import os

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from utils.config import pcfg, save_config
from utils.mirror import patch_hf_env

# ── Presets (display_name, url) per scenario ─────────────────────────

_PRESETS_UPDATES = [
    ("Official (GitHub)", ""),
    ("gitclone.com", "https://gitclone.com"),
    ("Custom...", "__custom__"),
]

_PRESETS_PACKAGES = [
    ("Official (PyPI)", ""),
    ("Tsinghua", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("Aliyun", "https://mirrors.aliyun.com/pypi/simple/"),
    ("USTC", "https://pypi.mirrors.ustc.edu.cn/simple/"),
    ("Custom...", "__custom__"),
]


# ── Reusable dropdown + explanation + (optional) custom field ─────────


class _SourceRow(QWidget):
    """Dropdown, explanation text, and an optional custom-URL input that
    appears when the user selects "Custom..."."""

    def __init__(
        self,
        presets: list[tuple[str, str]],
        desc_official: str,
        desc_mirror: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self._presets = presets
        self._desc_official = desc_official
        self._desc_mirror = desc_mirror

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.combo = QComboBox()
        self.combo.setMinimumHeight(34)
        for display, _ in presets:
            self.combo.addItem(display)
        self.combo.currentIndexChanged.connect(self._on_change)
        layout.addWidget(self.combo)

        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        layout.addWidget(self.explanation)

        self.custom_field = QLineEdit()
        self.custom_field.setPlaceholderText(
            self.tr("Enter custom mirror URL...")
        )
        self.custom_field.setMinimumHeight(34)
        self.custom_field.setVisible(False)
        layout.addWidget(self.custom_field)

        self._on_change(0)

    def _on_change(self, index: int):
        _, url = self._presets[index]
        if url == "__custom__":
            self.custom_field.setVisible(True)
            self.explanation.clear()
        elif url == "":
            self.custom_field.setVisible(False)
            self.explanation.setText(self._desc_official)
        else:
            self.custom_field.setVisible(False)
            self.explanation.setText(self._desc_mirror.get(url, ""))

    def get_value(self) -> str:
        _, url = self._presets[self.combo.currentIndex()]
        if url == "__custom__":
            return self.custom_field.text().strip()
        return url

    def set_value(self, url: str):
        for i, (_, val) in enumerate(self._presets):
            if val == url:
                self.combo.setCurrentIndex(i)
                return
        for i, (display, val) in enumerate(self._presets):
            if val == "__custom__":
                self.combo.setCurrentIndex(i)
                self.custom_field.setText(url)
                return


# ── Dialog ────────────────────────────────────────────────────────────


class NetworkSettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Network & Mirror Settings"))
        self.setMinimumSize(500, 360)
        self._build_ui()
        self._load_config()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Updates & Model Downloads (GitHub) ──
        layout.addWidget(QLabel(
            self.tr("Software Updates & Model Downloads — from GitHub")
        ))
        self._row_updates = _SourceRow(
            _PRESETS_UPDATES,
            desc_official=self.tr(
                "Uses official GitHub servers. This covers both software update checks and most AI model file downloads (text detection, OCR, inpainting)."
            ),
            desc_mirror={
                "https://gitclone.com": self.tr(
                    "Gitclone mirror — recommended for users in China who experience slow or failed downloads."
                ),
            },
            parent=self,
        )
        layout.addWidget(self._row_updates)

        # ── Python Package Installation (pip) ──
        layout.addWidget(QLabel(
            self.tr("Python Package Installation — from PyPI")
        ))
        self._row_packages = _SourceRow(
            _PRESETS_PACKAGES,
            desc_official=self.tr(
                "Installs Python packages from the official PyPI index. Reliable worldwide."
            ),
            desc_mirror={
                "https://pypi.tuna.tsinghua.edu.cn/simple": self.tr(
                    "Tsinghua mirror — one of the fastest PyPI mirrors in China."
                ),
                "https://mirrors.aliyun.com/pypi/simple/": self.tr(
                    "Aliyun mirror — maintained by Alibaba Cloud, good coverage."
                ),
                "https://pypi.mirrors.ustc.edu.cn/simple/": self.tr(
                    "USTC mirror — maintained by University of Science and Technology of China."
                ),
            },
            parent=self,
        )
        layout.addWidget(self._row_packages)

        # ── Advanced ──
        self._adv_group = QGroupBox(self.tr("Advanced"))
        self._adv_group.setCheckable(True)
        self._adv_group.setChecked(False)
        adv = QVBoxLayout(self._adv_group)
        adv.setSpacing(8)

        # Extra pip index
        adv.addWidget(QLabel(self.tr("Extra pip index (for PyTorch, etc.):")))
        self._extra_input = QLineEdit()
        self._extra_input.setMinimumHeight(34)
        self._extra_input.setPlaceholderText("https://download.pytorch.org/whl/cu124")
        adv.addWidget(self._extra_input)

        # Custom HF endpoint
        adv.addWidget(QLabel(self.tr("Custom HuggingFace endpoint (advanced):")))
        self._hf_input = QLineEdit()
        self._hf_input.setMinimumHeight(34)
        self._hf_input.setPlaceholderText("https://hf-mirror.com")
        adv.addWidget(self._hf_input)

        hint_adv = QLabel(
            self.tr(
                "These are only needed if you use a custom Python environment or want to override the HuggingFace download path for the few models hosted there (font detection, LaMa inpainter)."
            )
        )
        hint_adv.setWordWrap(True)
        hint_adv.setStyleSheet("color: #888; font-size: 12px;")
        adv.addWidget(hint_adv)

        layout.addWidget(self._adv_group)

        layout.addStretch()

        # Footer hint
        hint = QLabel(
            self.tr(
                "Pick the first option (Official) for each category if you are not experiencing network issues."
            )
        )
        hint.setStyleSheet("color: #999; font-size: 12px;")
        layout.addWidget(hint)

        # ── Utility buttons row (small, bottom-left) ──
        util_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Reset to defaults"))
        reset_btn.setMinimumHeight(28)
        reset_btn.clicked.connect(self._reset_defaults)
        cn_btn = QPushButton(self.tr("Quick setup for China"))
        cn_btn.setMinimumHeight(28)
        cn_btn.clicked.connect(self._quick_cn)
        util_row.addWidget(reset_btn)
        util_row.addWidget(cn_btn)
        util_row.addStretch()
        layout.addLayout(util_row)

        # Save / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(self.tr("Save"))
        ok_btn.setMinimumWidth(100)
        ok_btn.setMinimumHeight(34)
        ok_btn.clicked.connect(self._save)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setMinimumWidth(100)
        cancel_btn.setMinimumHeight(34)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ── Load / Save / Quick ──────────────────────────────────────────

    def _load_config(self):
        m = pcfg.mirror
        self._row_updates.set_value(m.github_mirror)
        self._row_packages.set_value(m.pip_index_url)
        self._extra_input.setText(m.pip_extra_index_url)
        self._hf_input.setText(m.hf_endpoint)
        self._adv_group.setChecked(bool(m.pip_extra_index_url or m.hf_endpoint))

    def _quick_cn(self):
        self._row_updates.set_value("https://gitclone.com")
        self._row_packages.set_value("https://pypi.tuna.tsinghua.edu.cn/simple")
        self._extra_input.setText("https://mirrors.aliyun.com/pypi/simple/")
        self._hf_input.setText("https://hf-mirror.com")
        self._adv_group.setChecked(True)

    def _reset_defaults(self):
        """Clear all mirror settings back to official (empty) defaults."""
        self._row_updates.set_value("")
        self._row_packages.set_value("")
        self._extra_input.clear()
        self._hf_input.clear()
        self._adv_group.setChecked(False)

    def _save(self):
        m = pcfg.mirror
        m.github_mirror = self._row_updates.get_value()
        m.pip_index_url = self._row_packages.get_value()
        m.pip_extra_index_url = (
            self._extra_input.text().strip()
            if self._adv_group.isChecked()
            else ""
        )
        m.hf_endpoint = (
            self._hf_input.text().strip()
            if self._adv_group.isChecked()
            else ""
        )

        # Apply env vars
        if m.pip_index_url:
            os.environ["INDEX_URL"] = m.pip_index_url
        else:
            os.environ.pop("INDEX_URL", None)
        if m.pip_extra_index_url:
            os.environ["UV_EXTRA_INDEX_URL"] = m.pip_extra_index_url
        else:
            os.environ.pop("UV_EXTRA_INDEX_URL", None)
        if m.github_mirror:
            os.environ["GITHUB_MIRROR"] = m.github_mirror
        else:
            os.environ.pop("GITHUB_MIRROR", None)
        patch_hf_env(m.hf_endpoint)

        save_config()
        self.accept()
