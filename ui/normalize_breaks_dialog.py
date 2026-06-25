"""批量整理换行对话框。

布局（自上而下）：
- ☑ 全部页（勾选时禁用页码列表，按全部页处理）
- 页码列表（每页一个复选框）
- ☐ 完成后自动收缩框
- [应用] [取消]

当前页块的旧 HTML / 旧矩形由 ``NormalizeBreaksCommand`` 在构造时从
live ``TextBlkItem`` 捕获，故本对话框只负责生成每块的文本变更项。
"""

from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from utils.text_normalize import normalize_softbreaks


class NormalizeBreaksDialog(QDialog):
    """批量整理换行对话框。"""

    def __init__(self, proj, scene_manager, parent=None):
        super().__init__(parent)
        self.proj = proj
        self.sm = scene_manager
        self.processed_count = 0
        self.skipped_count = 0
        self._changes = []
        self._setup_ui()
        self._populate_pages()

    def _setup_ui(self):
        self.setWindowTitle(self.tr("批量整理换行"))
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        self.all_check = QCheckBox(self.tr("全部页"), self)
        self.all_check.setChecked(True)
        self.all_check.toggled.connect(self._on_all_toggled)
        layout.addWidget(self.all_check)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.page_container = QWidget()
        self.page_layout = QVBoxLayout(self.page_container)
        self.page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.setSpacing(2)
        self.scroll.setWidget(self.page_container)
        layout.addWidget(self.scroll, stretch=1)

        self.squeeze_check = QCheckBox(self.tr("完成后自动收缩框"), self)
        self.squeeze_check.setChecked(False)
        layout.addWidget(self.squeeze_check)

        btn_layout = QHBoxLayout()
        apply_btn = QPushButton(self.tr("应用"), self)
        cancel_btn = QPushButton(self.tr("取消"), self)
        apply_btn.clicked.connect(self._on_apply)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(apply_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _populate_pages(self):
        self.page_checks = []
        for i in range(self.proj.num_pages):
            pname = self.proj.idx2pagename(i)
            cb = QCheckBox(f"{self.tr('第')} {i} {self.tr('页')} — {pname}", self)
            cb.setEnabled(False)  # 默认全部页勾选 → 禁用列表
            self.page_layout.addWidget(cb)
            self.page_checks.append(cb)
        self.page_layout.addStretch()

    def _on_all_toggled(self, checked: bool):
        for cb in self.page_checks:
            cb.setEnabled(not checked)
            if checked:
                cb.setChecked(False)

    def _on_apply(self):
        self._changes = []
        self.processed_count = 0
        self.skipped_count = 0

        if self.all_check.isChecked():
            page_indices = range(self.proj.num_pages)
        else:
            page_indices = [
                i for i, cb in enumerate(self.page_checks) if cb.isChecked()
            ]

        squeeze = self.squeeze_check.isChecked()
        current_pname = self.proj.current_img

        for pi in page_indices:
            pname = self.proj.idx2pagename(pi)
            blocks = self.proj.pages.get(pname, [])
            for bidx, blk in enumerate(blocks):
                if blk.vertical:
                    self.skipped_count += 1
                    continue
                if not isinstance(blk.translation, str) or blk.translation == "":
                    continue
                new_text = normalize_softbreaks(blk.translation)
                if new_text == blk.translation:
                    continue
                self._changes.append(
                    {
                        "pagename": pname,
                        "block_idx": bidx,
                        "old_translation": blk.translation,
                        "old_rich_text": blk.rich_text,
                        "new_text": new_text,
                        "squeeze": squeeze,
                    }
                )
                self.processed_count += 1
        self.accept()

    def get_changes(self) -> list:
        return self._changes