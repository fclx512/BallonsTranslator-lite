"""选中跟随标签工具栏（PS 式浮动工具栏，AI 辅助批次 B 主入口）。

选中文字块时在选区上方出现，紧凑栏 = 每类标签一枚切换钮 + 展开钮 +
上下文感知「处理」钮；展开后显示全类标签面板（名称 + 性质标注）。
选中清空即隐，紧凑态与展开态一致执行（即用即走，无特例——规划 §8.8
已获用户认同的裁剪）。打标不进撤销栈（轻量元数据），写回后刷新块徽
标并标记项目未保存。

数据源是 ``utils/block_tags.py::MANUAL_TAG_DEFS``——**程序专用标签**
（``TagDef.program_only``，即「误识别文本」）不在此处出现：该标签只由
OCR 后处理钩子自动挂，人只在工作台审查误杀（规划 D2／D38）。

挂父在主窗口中央控件上防 GC，不参与布局；定位钳制在宿主范围内。
QSS 容器样式走 objectName 选择器，必须开 WA_StyledBackground 才会
绘制背景/边框（裸 QWidget 子类默认跳过 QSS 背景绘制）。
"""

from typing import List

from qtpy.QtCore import QCoreApplication, Signal, Qt
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from utils.block_actions import BLOCK_ACTIONS, actions_for_block
from utils.block_tags import (
    MANUAL_TAG_DEFS,
    has_tag,
    remove_tag,
    set_tag,
)
from utils.config import pcfg
from utils.textblock import TextBlock

# 性质显示名：字面量定义处显式标注翻译上下文
NATURE_LABELS = {
    "doubt": QCoreApplication.translate("TagToolbar", "Doubt"),
    "directive": QCoreApplication.translate("TagToolbar", "Directive"),
}

COMPACT_BTN_SIZE = 24


class TagToolbar(QWidget):
    """选中态浮动标签工具栏。宿主 = 主窗口中央控件（浮层不参与布局）。"""

    action_requested = Signal(str)  # 框级 AI 动作 id（上下文感知「处理」钮）

    def __init__(self, host: QWidget, canvas) -> None:
        super().__init__(host)
        self.setObjectName("TagToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._canvas = canvas
        self._items: List = []
        self._expanded = False
        self._compact_buttons: dict = {}  # tag_id -> QToolButton（紧凑栏）
        self._panel_rows: dict = {}  # tag_id -> QToolButton（展开面板行）
        self._action_buttons: dict = {}  # action_id -> QToolButton

        # 视口滚动时跟随选区重锚（缩放无独立信号，经布局/下次选中刷新兜底）
        gv = canvas.gv
        gv.horizontalScrollBar().valueChanged.connect(self._maybe_reposition)
        gv.verticalScrollBar().valueChanged.connect(self._maybe_reposition)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 3, 4, 3)
        root.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        root.addLayout(row)

        for tag in MANUAL_TAG_DEFS:
            btn = QToolButton(self)
            btn.setObjectName("TagTagBtn")
            btn.setText(tag.glyph)
            btn.setFixedSize(COMPACT_BTN_SIZE, COMPACT_BTN_SIZE)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCheckable(True)
            nature = NATURE_LABELS.get(tag.nature, "")
            btn.setToolTip(f"{tag.name} ({nature})")
            btn.toggled.connect(
                lambda checked, tid=tag.id: self._apply_tag(tid, checked)
            )
            self._compact_buttons[tag.id] = btn
            row.addWidget(btn)

        row.addSpacing(6)

        # 上下文感知「处理」钮（§8.8）：选中单块且块上有疑点标签时出现，
        # 点击发起对应框级 AI 动作（多选批量动作不在 v1 范围）
        for action in BLOCK_ACTIONS:
            act_btn = QToolButton(self)
            act_btn.setObjectName("TagActionBtn")
            act_btn.setText(action.short_label)
            act_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            act_btn.setToolTip(action.name)
            act_btn.clicked.connect(
                lambda _=False, aid=action.id: self.action_requested.emit(aid)
            )
            act_btn.hide()
            self._action_buttons[action.id] = act_btn
            row.addWidget(act_btn)

        row.addStretch(1)

        self._expand_btn = QToolButton(self)
        self._expand_btn.setObjectName("TagTagBtn")
        self._expand_btn.setText("⋯")
        self._expand_btn.setFixedSize(COMPACT_BTN_SIZE, COMPACT_BTN_SIZE)
        self._expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._expand_btn.setToolTip(QCoreApplication.translate("TagToolbar", "Expand Panel"))
        self._expand_btn.clicked.connect(self.toggle_expanded)
        row.addWidget(self._expand_btn)

        # 展开面板：全类标签行（字形 + 名称 + 性质）与紧凑钮同状态联动
        self._panel = QFrame(self)
        self._panel.setObjectName("TagTagPanel")
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(4, 2, 4, 3)
        panel_layout.setSpacing(1)
        for tag in MANUAL_TAG_DEFS:
            # 行钮用 QPushButton：QToolButton 在本 Qt 版本无视 QSS
            # text-align（实测恒居中），QPushButton 按规则左对齐
            row_btn = QPushButton(self._panel)
            row_btn.setObjectName("TagTagRowBtn")
            row_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row_btn.setCheckable(True)
            # 整行触发范围：横向撑满面板宽，文字左对齐（QSS text-align）
            row_btn.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            row_btn.setText(f"{tag.glyph}  {tag.name}")
            nature = NATURE_LABELS.get(tag.nature, "")
            row_btn.setToolTip(f"{tag.name} ({nature})")
            row_btn.toggled.connect(
                lambda checked, tid=tag.id: self._apply_tag(tid, checked)
            )
            self._panel_rows[tag.id] = row_btn
            panel_layout.addWidget(row_btn)
        self._panel.hide()
        root.addWidget(self._panel)

        self.hide()

    # ── 选中驱动 ────────────────────────────────────────────────

    def sync_from_canvas(self) -> None:
        """选中变化 / 显隐开关链入口：无选中或非文本编辑模式即隐。"""
        canvas = self._canvas
        if (
            canvas is None
            or not canvas.textEditMode()
            or not pcfg.show_tag_toolbar
        ):
            self.hide()
            return
        items = canvas.selected_text_items()
        if not items:
            self.hide()
            return
        self._items = items
        self._update_checks()
        self._refresh_geometry()
        self.show()
        self.raise_()

    def _update_checks(self) -> None:
        """多选批量语义：全体选中块都带标签 → 勾选，否则不勾。紧凑栏与
        展开面板行是两套按钮，须同步刷。"""
        for tag in MANUAL_TAG_DEFS:
            checked = all(has_tag(it.blk, tag.id) for it in self._items)
            for btn in (
                self._compact_buttons[tag.id],
                self._panel_rows[tag.id],
            ):
                btn.blockSignals(True)
                btn.setChecked(checked)
                btn.blockSignals(False)
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        """「处理」钮：单选 + 块上带该动作消费的疑点标签时出现。"""
        applicable = set()
        if len(self._items) == 1:
            applicable = {a.id for a in actions_for_block(self._items[0].blk)}
        changed = False
        for aid, btn in self._action_buttons.items():
            visible = aid in applicable
            if btn.isVisibleTo(self) != visible:
                btn.setVisible(visible)
                changed = True
        if changed and self.isVisible():
            self._refresh_geometry()

    def _apply_tag(self, tag_id: str, checked: bool) -> None:
        """工具栏按钮触发：勾选/取消勾选按钮值直接写（与快捷键的批量
        翻转语义不同）；但多选时若按钮呈半挂状态，按钮值即目标值。"""
        if not self._items:
            return
        for item in self._items:
            blk: TextBlock = item.blk
            if checked:
                set_tag(blk, tag_id, "manual")
            else:
                remove_tag(blk, tag_id)
            item.refresh_tag_badge()
        self._update_checks()
        # 打标不进撤销栈，但属于项目数据变更 → 标记未保存
        self._canvas.setProjSaveState(True)

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._panel.setVisible(self._expanded)
        self._expand_btn.setText("▴" if self._expanded else "⋯")
        if self.isVisible():
            self._refresh_geometry()

    # ── 定位（选区上方，越界翻转下方，钳制宿主） ─────────────────

    def _maybe_reposition(self) -> None:
        if self.isVisible() and self._items:
            self._refresh_geometry()

    def _refresh_geometry(self) -> None:
        """可见性/展开态变化后重算尺寸再锚定（布局重排先行，adjustSize
        才能拿到准确 sizeHint）。"""
        layout: QLayout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()
        self._reposition()

    def _reposition(self) -> None:
        host = self.parentWidget()
        if host is None or not self._items:
            return
        rect = self._items[0].sceneBoundingRect()
        for item in self._items[1:]:
            rect = rect.united(item.sceneBoundingRect())
        gv = self._canvas.gv
        tl = self._scene_to_host(gv, host, rect.topLeft())
        br = self._scene_to_host(gv, host, rect.bottomRight())
        # 先试选区上方，顶部越界改贴下方
        y = tl.y() - self.height() - 6
        if y < 0:
            y = br.y() + 6
        x = tl.x()
        x = max(0, min(x, host.width() - self.width()))
        y = max(0, min(y, host.height() - self.height()))
        self.move(x, y)

    @staticmethod
    def _scene_to_host(gv, host, scene_pos):
        # mapFromScene 返回视图控件坐标，经 mapToGlobal 转全局再落到宿主
        return host.mapFromGlobal(gv.mapToGlobal(gv.mapFromScene(scene_pos)))
