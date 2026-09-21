"""设置页 Models →「模型文件」节：带本地权重的模块的获取 / 总览 / 释放。

设计见 ``docs/技术实现/模型文件管理_设计方案_存档.md`` §6～§8。三条硬约束：

1. **删除白名单**：只删模块自己声明过的 ``save_files``（
   ``modules/__init__.py::GET_MODULE_REQUIREMENTS`` 的 ``files`` 字段），
   绝不按目录盲删——单文件模块的父目录是 ``data/models/``，那是全部模型的家。
2. **不碰 git 跟踪的文件**：由 ``utils/model_files.py::delete_paths`` 用白名单排除。
3. **下载归后台**：本节点只负责「起任务 / 看状态 / 取消」，进度与结果在终端
   （``ui/model_downloads.py``）。界面不被下载阻塞。

界面形态对齐泛用工作台的候选列表：``ui/custom_widget/row_table.py::RowTable``
的卡片模式（一行一张圆角卡：模型名 + 元数据次行 + 右侧状态徽章），**勾选**
表示「本次要操作的模型」，动作按钮统一放在列表下方、对勾选集合生效。这样
每行不必挤四个按钮，状态与体积也有地方摆。

本控件不直接持有 ModuleManager：卸载模型（删除前必做）经
``ConfigPanel.model_unload_requested`` 信号交出去接线，保持设置页与管线解耦。
"""

import os
import os.path as osp

from qtpy.QtCore import Qt, QUrl, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import MODE_CARD, RowTable
from utils.logger import logger as LOGGER
from utils.message import create_info_dialog
from utils.model_files import delete_paths, format_size, trash_available

# 卡片行高取自 ui/custom_widget/row_table.py::_CARD_HEIGHT；设置页本身是滚动区，
# 节内表格最高显示这么多行，再多就表内滚动（否则一节占掉一整屏）
_ROW_HEIGHT = 56
_MAX_VISIBLE_ROWS = 5
# 删除确认框最多逐行列出多少个文件（1.9GB 那个包有 15 个）
_CONFIRM_FILE_LIMIT = 20


class ModelFilesSection(QWidget):
    """「模型文件」节：勾选模型 → 用下方动作行下载 / 取消 / 删除 / 打开目录。"""

    # 删除前请求卸载该阶段的模型（接线在 ui/module_manager.py）
    unload_requested = Signal(str)  # module_type
    # 任何「装载状态变了」的动作完成后发一次，供主窗重刷选择器的缺文件警示色
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packages: list = []  # 与表格行一一对应（GET_MODEL_PACKAGES 的顺序）
        self._index_of: dict = {}  # (module_type, key) → 行号
        self._checked: set = set()  # 勾选集合，跨 refresh 保留
        self._build()
        self._connect_registry()
        self.refresh()

    # ── 构建 ─────────────────────────────────────────────────────────────

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题不在这里画：本节由设置页的分节卡承载（标题＝卡片的「模型文件」）
        # 译文串必须是**单个字面量**：i18n 提取器不认隐式拼接（scripts/i18n_common.py）
        hint = QLabel(
            self.tr(
                "Tick a model, then use the buttons below. Downloads run in the background — progress and errors go to the terminal and to logs/."
            )
        )
        hint.setObjectName("ModelFilesHint")
        hint.setWordWrap(True)
        hint.setContentsMargins(16, 8, 16, 6)
        layout.addWidget(hint)

        self._table = RowTable(MODE_CARD)
        # RowTable 默认 objectName 是工作台那张表，这里换成自己的（见 stylesheet）
        self._table.setObjectName("ModelFilesTable")
        # 表格自带的最小宽度建议偏大，放着不管窄窗口下会把设置页撑出横向滚动条
        # （卡片列宽是 Stretch，压窄后自动跟着收）
        self._table.setMinimumWidth(0)
        table_policy = self._table.sizePolicy()
        table_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self._table.setSizePolicy(table_policy)
        self._table.checkToggled.connect(self._on_check_toggled)
        table_row = QHBoxLayout()
        table_row.setContentsMargins(16, 0, 16, 6)
        table_row.addWidget(self._table)
        layout.addLayout(table_row)

        self._empty_hint = QLabel(
            self.tr("No module with downloadable model files is registered.")
        )
        self._empty_hint.setObjectName("ModelFilesHint")
        self._empty_hint.setContentsMargins(16, 4, 16, 6)
        self._empty_hint.setVisible(False)
        layout.addWidget(self._empty_hint)

        # ── 动作行（对勾选集合生效；刷新钮在最左，同工作台） ──
        actions = QHBoxLayout()
        actions.setContentsMargins(16, 0, 16, 8)
        actions.setSpacing(6)

        from ui.workbench_batch_view import RefreshButton

        self._refresh_btn = RefreshButton(self)
        self._refresh_btn.setToolTip(
            self.tr("Refresh: rescan which model files are on disk.")
        )
        self._refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self._refresh_btn)

        self._download_btn = QPushButton(self.tr("Download"), self)
        self._download_btn.setObjectName("ModelFilesPrimaryButton")
        self._download_btn.clicked.connect(self._download_selected)
        actions.addWidget(self._download_btn)

        self._cancel_btn = QPushButton(self.tr("Cancel"), self)
        self._delete_btn = QPushButton(self.tr("Delete"), self)
        self._open_btn = QPushButton(self.tr("Open folder"), self)
        for button in (self._cancel_btn, self._delete_btn, self._open_btn):
            button.setObjectName("ConfigButton")
        self._cancel_btn.clicked.connect(self._cancel_selected)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._open_btn.clicked.connect(self._open_selected_folder)
        actions.addWidget(self._cancel_btn)
        actions.addWidget(self._delete_btn)
        actions.addWidget(self._open_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ── 底部状态条（同工作台：顶边一条分隔线） ──
        bar = QWidget(self)
        bar.setObjectName("ModelFilesStatusBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 6, 16, 6)
        self._status = QLabel("", bar)
        self._status.setObjectName("ModelFilesHint")
        bar_layout.addWidget(self._status)
        bar_layout.addStretch(1)
        layout.addWidget(bar)

    def _connect_registry(self):
        from ui.model_downloads import model_downloads

        registry = model_downloads()
        registry.task_started.connect(self._on_task_changed)
        registry.task_status.connect(self._on_task_changed)
        registry.task_progress.connect(self._on_task_changed)
        registry.task_finished.connect(self._on_task_finished)

    # ── 刷新 ─────────────────────────────────────────────────────────────

    def showEvent(self, event):
        # 显示时重算一次：文件可能是在外部手工放入／删掉的
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        """整表重建（磁盘状态重算）。下载进度走 :meth:`_refresh_rows`，不重建。"""
        from modules import GET_MODEL_PACKAGES
        from ui.model_downloads import model_downloads

        self._packages = GET_MODEL_PACKAGES()
        self._index_of = {
            self._ident(info): idx for idx, info in enumerate(self._packages)
        }
        # 已消失的包，勾选跟着作废
        self._checked &= set(self._index_of)
        self._table.set_rows([self._row_dict(info) for info in self._packages])
        self._empty_hint.setVisible(not self._packages)
        self._table.setVisible(bool(self._packages))
        rows = max(1, min(len(self._packages), _MAX_VISIBLE_ROWS))
        self._table.setFixedHeight(rows * _ROW_HEIGHT + 2)
        self._sync_actions(model_downloads())

    def _refresh_rows(self):
        """只重画行内容（下载进度/状态变化时用，不重置模型与勾选）。"""
        from ui.model_downloads import model_downloads

        for idx, info in enumerate(self._packages):
            self._table.update_row(idx, self._row_dict(info))
        self._sync_actions(model_downloads())

    # ── 行内容 ───────────────────────────────────────────────────────────

    @staticmethod
    def _ident(info: dict):
        return (info["module_type"], info["key"])

    def _row_dict(self, info: dict) -> dict:
        from ui.model_downloads import model_downloads

        registry = model_downloads()
        ident = self._ident(info)
        badge, tone = self._badge_of(info, registry, ident)
        meta = " · ".join(
            [
                self._stage_label(info["module_type"]),
                info["key"],
                self.tr("%1 files").replace("%1", str(len(info["files"]))),
                self._size_text(info),
            ]
        )
        return {
            "primary": info["display_name"],
            "meta": meta,
            "badge": badge,
            "badge_tone": tone,
            "checked": ident in self._checked,
            "tooltip": self._tooltip_of(info),
        }

    def _badge_of(self, info: dict, registry, ident):
        """``(徽章文本, 色调)``；色调取 ``RowTable`` 认的 warning/accent/muted。"""
        if registry.is_running(*ident):
            progress = registry.progress_of(*ident)
            if progress and progress[1]:
                percent = int(progress[0] * 100 / progress[1])
                return self.tr("%1%").replace("%1", str(percent)), "accent"
            return self.tr("Downloading…"), "accent"
        if self._gpu_blocked(info):
            # 本机没有 GPU：这不是「缺文件」（下载入口本身就会拒绝），
            # 徽章得说清是哪种缺，否则用户会一直点下载
            return self.tr("Needs GPU"), "warning"
        if info["missing"]:
            return (
                self.tr("Missing %1").replace("%1", str(len(info["missing"]))),
                "warning",
            )
        if info["missing_packages"]:
            return self.tr("Missing package"), "warning"
        return self.tr("Ready"), "muted"

    @staticmethod
    def _gpu_blocked(info: dict) -> bool:
        if not info.get("requires_gpu"):
            return False
        from modules.base import accelerator_available

        return not accelerator_available()

    def _size_text(self, info: dict) -> str:
        if info["size_bytes"] > 0:
            return format_size(info["size_bytes"])
        if info["size_hint"]:
            return self.tr("about %1").replace("%1", str(info["size_hint"]))
        return "—"

    def _tooltip_of(self, info: dict) -> str:
        lines = [self.tr("Registered module: %1").replace("%1", info["key"])]
        if info.get("package_dir"):
            lines.append(info["package_dir"])
        shown = list(info["files"])[:8]
        lines.extend(shown)
        if len(info["files"]) > len(shown):
            lines.append(
                self.tr("… and %1 more files").replace(
                    "%1", str(len(info["files"]) - len(shown))
                )
            )
        return "\n".join(lines)

    def _stage_label(self, module_type: str) -> str:
        return {
            "textdetector": self.tr("Text Detection"),
            "ocr": self.tr("OCR"),
            "translator": self.tr("Translation"),
            "inpainter": self.tr("Inpainting"),
        }.get(module_type, module_type)

    @staticmethod
    def _program_path() -> str:
        from utils.shared import PROGRAM_PATH

        return PROGRAM_PATH

    # ── 勾选与动作可用性 ─────────────────────────────────────────────────

    def _on_check_toggled(self, row: int, checked: bool):
        if not (0 <= row < len(self._packages)):
            return
        ident = self._ident(self._packages[row])
        if checked:
            self._checked.add(ident)
        else:
            self._checked.discard(ident)
        from ui.model_downloads import model_downloads

        self._sync_actions(model_downloads())

    def _selected_infos(self) -> list:
        return [
            info for info in self._packages if self._ident(info) in self._checked
        ]

    def _files_on_disk(self, info: dict) -> list:
        program_path = self._program_path()
        found = []
        for path in info["files"]:
            full = path if osp.isabs(path) else osp.join(program_path, path)
            if osp.exists(full):
                found.append(path)
        return found

    def _sync_actions(self, registry):
        selected = self._selected_infos()
        pending = [i for i in selected if i["missing"] or i["missing_packages"]]
        running = [i for i in selected if registry.is_running(*self._ident(i))]
        on_disk = [i for i in selected if self._files_on_disk(i)]

        self._download_btn.setEnabled(bool(pending))
        self._download_btn.setToolTip(
            self.tr(
                "Downloads the ticked models in the background; progress is printed in the terminal."
            )
            if pending
            else self.tr("Tick a model that is not installed yet.")
        )
        self._cancel_btn.setVisible(bool(running))
        self._cancel_btn.setToolTip(
            self.tr("Stops the download. Files already fetched are kept.")
        )
        self._delete_btn.setEnabled(bool(on_disk))
        self._delete_btn.setToolTip(
            self.tr("Moves the ticked models' files to the recycle bin.")
            if on_disk
            else self.tr("Tick a model that has files on disk.")
        )
        self._open_btn.setEnabled(len(selected) == 1)
        self._open_btn.setToolTip(
            self.tr("Open the folder of the ticked model.")
            if len(selected) == 1
            else self.tr("Tick exactly one model to open its folder.")
        )

        total = sum(info["size_bytes"] for info in self._packages)
        self._status.setText(
            self.tr("%1 model(s) · %2 selected · %3 on disk")
            .replace("%1", str(len(self._packages)))
            .replace("%2", str(len(selected)))
            .replace("%3", format_size(total))
        )

    # ── 动作 ─────────────────────────────────────────────────────────────

    def _download_selected(self):
        from ui.model_downloads import gpu_requirement_block, model_downloads

        registry = model_downloads()
        selected = self._selected_infos()
        # 要求 GPU 而本机没有的：不下、并说清为什么（文案与判据都在 model_downloads）
        blocked = [
            gpu_requirement_block(info["module_type"], info["key"])
            for info in selected
        ]
        blocked = [text for text in blocked if text]
        if blocked:
            create_info_dialog(
                "\n\n".join(blocked), btn_type=QMessageBox.StandardButton.Ok
            )
        for info in selected:
            if info["missing"] or info["missing_packages"]:
                registry.start(info["module_type"], info["key"])
        self._refresh_rows()

    def _cancel_selected(self):
        from ui.model_downloads import model_downloads

        registry = model_downloads()
        for info in self._selected_infos():
            ident = self._ident(info)
            if registry.is_running(*ident):
                registry.cancel(*ident)
        self._refresh_rows()

    def _open_selected_folder(self):
        selected = self._selected_infos()
        if len(selected) != 1:
            return
        target = self._folder_for(selected[0])
        if target is None:
            return
        if not osp.isdir(target):
            try:
                os.makedirs(target, exist_ok=True)
            except OSError as e:
                LOGGER.error("Failed to create model folder %s: %s", target, e)
                QMessageBox.warning(
                    self,
                    self.tr("Open folder"),
                    self.tr("Failed to open the folder:\n%1").replace("%1", str(e)),
                )
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _folder_for(self, info: dict):
        """要打开的目录：声明过的包目录，否则第一个声明文件所在目录。"""
        program_path = self._program_path()
        if info.get("package_dir"):
            return osp.join(program_path, info["package_dir"])
        for path in info["files"]:
            full = path if osp.isabs(path) else osp.join(program_path, path)
            return osp.dirname(full)
        return None

    def _delete_selected(self):
        from ui.model_downloads import model_downloads

        targets = [
            (info, self._files_on_disk(info)) for info in self._selected_infos()
        ]
        targets = [(info, files) for info, files in targets if files]
        if not targets:
            return

        program_path = self._program_path()
        all_files = [path for _info, files in targets for path in files]
        total = 0
        for rel in all_files:
            full = rel if osp.isabs(rel) else osp.join(program_path, rel)
            try:
                total += osp.getsize(full)
            except OSError:
                pass

        if not self._confirm_delete(targets, all_files, total):
            return

        registry = model_downloads()
        for info, _files in targets:
            ident = self._ident(info)
            # 正在下载就先叫停，否则同一只后台线程还在往这些文件里写
            if registry.is_running(*ident):
                registry.cancel(*ident)
            # 模型还载在内存里会握着文件句柄（Windows 上删不掉）
            self.unload_requested.emit(info["module_type"])

        result = delete_paths(all_files, use_trash=trash_available())
        LOGGER.info(
            "Deleted model files: removed=%d skipped=%d failed=%d permanent=%s",
            len(result.removed),
            len(result.skipped),
            len(result.failed),
            result.permanent,
        )
        if result.failed:
            QMessageBox.warning(
                self,
                self.tr("Delete model files"),
                self.tr("Some files could not be deleted:\n%1").replace(
                    "%1", "\n".join(result.failed)
                ),
            )
        self.refresh()
        self.changed.emit()

    def _confirm_delete(self, targets, all_files, total: int) -> bool:
        """删除前的告知（D27 同款口径：说清确认后会发生什么）。"""
        permanent = not trash_available()
        shown = all_files[:_CONFIRM_FILE_LIMIT]
        lines = [
            self.tr("About to delete %1 file(s) of %2 model(s) (%3):")
            .replace("%1", str(len(all_files)))
            .replace("%2", str(len(targets)))
            .replace("%3", format_size(total)),
            "",
            "\n".join(shown),
        ]
        if len(all_files) > len(shown):
            lines.append(
                self.tr("… and %1 more files").replace(
                    "%1", str(len(all_files) - len(shown))
                )
            )
        lines += [
            "",
            self.tr("These files are no longer recoverable in the recycle bin.")
            if permanent
            else self.tr("They are moved to the recycle bin and can be restored."),
            self.tr("The next run of this module will need to download them again."),
        ]

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Delete model files"))
        box.setText("\n".join(lines))
        delete_btn = box.addButton(
            self.tr("Delete"), QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is delete_btn

    # ── 注册表信号 ───────────────────────────────────────────────────────

    def _on_task_changed(self, *_args):
        # 进度/状态高频变化：只重画行，不重置模型（否则勾选与滚动位置会跳）
        self._refresh_rows()

    def _on_task_finished(self, *_args):
        # 下载结束时该阶段的文件可能刚齐备（体积也变了），整表重算
        self.refresh()
        self.changed.emit()
