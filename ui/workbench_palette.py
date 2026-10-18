"""工作台「取色调板」页：在画布上人工点字形取色，给颜色标角色名。

落点在工作台而非画布的理由（用户拍板）：取样颜色**后续要加角色标签**，
翻译时把「这个颜色 = 这个角色」喂给翻译 agent 辅译——它是翻译辅助数据，
不是画布样式操作。数据层是 ``utils/proj_imgtrans.py::ProjImgTrans`` 的
``fg_palette``（条目 ``{"rgb": [r,g,b], "role": str}``，role 是角色名，
占位名入板即分配、删除不复用；随项目 JSON 持久化）；吸附语义见
``utils/color_extract.py::_pick_fill_cluster``（``PALETTE_SNAP_DIST`` 内
精确取调板色，低彩首选簇才允许改选候选簇，防调板反吸）。

**取样区就是真画布**（2026-10-03 二版）：本页只放一个取样开关，点开即让画布
进入取色模式（``ui/canvas.py::enter_color_sample_mode``，旁挂常驻 activity
提示），点字形取原图像素；翻页用画布的 A/D、PgUp/PgDn 快捷键，取色模式跨页
保持。

**只留最短人工路径**（2026-10-15 收敛）：本页职责＝取样开关 + 色表 + 角色名
编辑 + 删除/清空，不承担自动取色审阅。全书扫描候选区（``Scan Book Colors``、
候选列表/缩略图/跳转/改色）与批量重取色入口（按调板重算已有块颜色）已整体移除；
自动取色的数据契约（``utils/color_extract.py`` 的聚类与扫描函数）留在数据层，
本页不再调用。
"""

from qtpy.QtCore import Signal, Qt
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigComboBox
from ui.workbench_tasks import PageImageCache

def _fmt_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, rgb[0])), max(0, min(255, rgb[1])), max(0, min(255, rgb[2]))
    )


class _PaletteRow(QWidget):
    """单个调板色行：色块 + HEX + 角色名下拉 + 删除钮。

    角色名是可编辑下拉（候选＝已有角色名，可自由输入）；占位名显示为
    「Speaker A」全称，存储只存短名（见 utils/proj_imgtrans.py 的调板
    节注释）。``role_changed`` 只在值真正变化时发。
    """

    remove_requested = Signal()
    role_changed = Signal(str)

    def __init__(self, rgb, role="", role_candidates=(), parent=None):
        super().__init__(parent)
        self._role = str(role or "")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        r, g, b = (int(v) for v in rgb[:3])
        chip = QLabel(self)
        chip.setFixedSize(44, 18)
        chip.setStyleSheet(
            f"background-color: rgb({r},{g},{b});"
            "border: 1px solid rgba(128,128,128,120); border-radius: 3px;"
        )
        layout.addWidget(chip)

        layout.addWidget(QLabel(_fmt_hex(rgb), self))

        self.role_combo = ConfigComboBox(self)
        self.role_combo.setMinimumWidth(90)
        self.role_combo.setEditable(True)
        editor = self.role_combo.lineEdit()
        if editor is not None:
            editor.setClearButtonEnabled(False)
            editor.editingFinished.connect(self._on_edit_finished)
        self.role_combo.activated.connect(self._on_activated)
        self.set_role(self._role, role_candidates)
        layout.addWidget(self.role_combo, 1)

        del_btn = QPushButton(self.tr("Remove"), self)
        del_btn.setToolTip(self.tr("Remove this color"))
        del_btn.clicked.connect(self.remove_requested.emit)
        layout.addWidget(del_btn)

    # ── 角色名 ────────────────────────────────────────────────

    @staticmethod
    def display_role(role: str) -> str:
        from utils.proj_imgtrans import speaker_display_name

        return speaker_display_name(role)

    def set_role(self, role: str, role_candidates=()):
        """外部重载行数据时同步下拉显示与候选（不触发 role_changed）。"""
        self._role = str(role or "")
        self._role_candidates = tuple(role_candidates)
        combo = self.role_combo
        combo.blockSignals(True)
        combo.clear()
        seen = set()
        for cand in self._role_candidates:
            display = self.display_role(cand)
            if display and display not in seen:
                combo.addItem(display)
                seen.add(display)
        combo.setCurrentText(self.display_role(self._role))
        # 手清后的空值不在候选里，setCurrentText 对可编辑下拉直接生效
        combo.blockSignals(False)

    def _on_edit_finished(self):
        self._commit_text(self.role_combo.currentText())

    def _on_activated(self, index: int):
        self._commit_text(self.role_combo.itemText(index))

    def _commit_text(self, text: str):
        from utils.proj_imgtrans import is_placeholder_role

        text = str(text or "").strip()
        # 占位名全称「Speaker A」还原成短名存储；其余原样
        if text.startswith("Speaker ") and is_placeholder_role(
            text[len("Speaker ") :]
        ):
            text = text[len("Speaker ") :]
        if text == self._role:
            # 用户点开又没改：把显示文本拉回规范形态（如手输过脏值）
            self.set_role(self._role, self._role_candidates)
            return
        self._role = text
        self.role_changed.emit(text)


class FgPalettePage(QWidget):
    """取色调板页：取样开关 + 色表（角色名可编辑）+ 删除/清空。

    对外接口（面板接线见 ``ui/glossary_agent_panel.py``）：

    - ``notify_requested``：页内提示 → 面板统一走 toast
    - ``sampling_toggled``：取样开关 → 面板转发给画布取色模式
    - ``accept_canvas_sample``：画布取色回流（页是取色的数据落点）
    - ``set_sampling_state``：画布侧状态回流（blockSignals 防回环）
    - ``refresh_state``：开工程/换项目后重挂色表
    """

    # (文案, kind) → 面板统一走 toast（与批量任务视图同约定）
    notify_requested = Signal(str, str)
    sampling_toggled = Signal(bool)

    def __init__(self, proj, parent=None):
        """构造人工取样调板页。"""
        super().__init__(parent)
        self._proj = proj
        self._rows: list = []
        # 页图缓存：取样读原图像素（连点取样不该每下重读一次整页图）
        self._crops = PageImageCache(proj)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(self.tr("Color palette"), self)
        title.setObjectName("WorkbenchPageTitle")
        layout.addWidget(title)
        layout.setSpacing(6)

        # ── 取样开关（真正的取样发生在画布上）─────────────────
        top = QHBoxLayout()
        top.setSpacing(6)
        self.sample_btn = QPushButton(self.tr("Sample on Canvas"), self)
        self.sample_btn.setObjectName("ConfigButton")
        self.sample_btn.setCheckable(True)
        self.sample_btn.toggled.connect(self._on_sample_toggled)
        top.addWidget(self.sample_btn)
        top.addStretch(1)
        layout.addLayout(top)

        hint = QLabel(
            self.tr(
                "Turn sampling on, then click text on the canvas to pick its color (Esc stops). Colors are saved with the project; rename a color to a character name to send it to the translator as a speaker hint."
            ),
            self,
        )
        hint.setObjectName("WorkbenchTaskHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 色表 ─────────────────────────────────────────────
        self._list_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._list_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        scroll.setMinimumHeight(100)
        scroll.setObjectName("WorkbenchPaletteList")
        self._scroll = scroll
        layout.addWidget(scroll, 1)

        self._empty_label = QLabel(
            self.tr("No colors yet. Turn on sampling and click text on the canvas."),
            self,
        )
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        self.clear_btn = QPushButton(self.tr("Clear Palette"), self)
        self.clear_btn.setObjectName("ConfigButton")
        self.clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(self.clear_btn, 0, Qt.AlignmentFlag.AlignRight)

        self.refresh_state()

    # ── 对面板的接口 ──────────────────────────────────────────

    def refresh_state(self):
        """开工程/换项目后同步：清取样显示态、重挂色表。

        ``proj`` 为 None（面板空态构造）时同样要能建页：清空后照常重挂
        （空表）。真正的画布取色模式由面板在 `refresh_project_state` 里先
        强制解除，这里只保证本页开关不残留旧状态。页图缓存必须随项目作废：
        ``PageImageCache`` 只按页名索引，两个项目同名页（001.jpg 之类）会
        静默读到上一个项目的像素——取样色会取错，且没有任何报错。
        """
        self.set_sampling_state(False)
        self._crops = PageImageCache(self._proj)
        self._reload_palette()

    def set_sampling_state(self, on: bool):
        """画布侧状态回流：只改开关显示，**不发** ``sampling_toggled``（防回环）。

        开关被画布自行解除（Esc／模式冲突／换项目）时面板回灌 False，开关
        与提示必须跟着回到「未取样」，否则界面与实际交互不一致。
        """
        on = bool(on)
        self.sample_btn.blockSignals(True)
        self.sample_btn.setChecked(on)
        self.sample_btn.blockSignals(False)
        self._apply_sample_button_state(on)

    def accept_canvas_sample(self, pagename: str, x: int, y: int):
        """画布取色回流：读原图像素 → 判重 → 追加带占位名的调板条目。

        判重用 ``utils/color_extract.py::PALETTE_DUP_DIST``（近重不入板）；
        新条目 ``{"rgb": …, "role": next_palette_role()}``，占位名由项目分配
        （删除不复用）。
        """
        proj = self._proj
        if proj is None or not pagename or pagename not in proj.pages:
            return
        img = self._crops.page_image(pagename)
        if img is None or img.ndim != 3:
            self.notify_requested.emit(
                self.tr("Failed to load page image: %1").replace("%1", pagename),
                "warning",
            )
            return
        h, w = img.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return
        rgb = [int(v) for v in img[y, x][:3]]

        from utils.color_extract import PALETTE_DUP_DIST, nearest_palette_entry

        entry = nearest_palette_entry(rgb, proj.fg_palette)
        if entry is not None and entry[0] <= PALETTE_DUP_DIST:
            self.notify_requested.emit(
                self.tr("%1 is already in the palette").replace("%1", _fmt_hex(rgb)),
                "info",
            )
            return
        role = proj.next_palette_role()
        proj.fg_palette.append({"rgb": rgb, "role": role})
        self._reload_palette()
        self._save()
        self.notify_requested.emit(
            _fmt_hex(rgb)
            + " "
            + self.tr("added to palette as %1 (%2 colors)")
            .replace("%1", "Speaker " + role)
            .replace("%2", str(len(proj.fg_palette))),
            "info",
        )

    # ── 内部：取样开关 ────────────────────────────────────────

    def _on_sample_toggled(self, on: bool):
        self._apply_sample_button_state(on)
        self.sampling_toggled.emit(on)

    def _apply_sample_button_state(self, on: bool):
        self.sample_btn.setText(
            self.tr("Sampling… (click glyph)") if on else self.tr("Sample on Canvas")
        )
        self.sample_btn.setToolTip(
            self.tr("Sampling is on: click a text glyph on the canvas (Esc stops it)")
            if on
            else self.tr("Toggle sampling, then click text glyphs on the canvas")
        )

    # ── 内部：色表维护 ────────────────────────────────────────

    def _reload_palette(self):
        palette = list(getattr(self._proj, "fg_palette", None) or [])
        candidates = self._role_candidates(palette)
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        for i, entry in enumerate(palette):
            rgb = entry.get("rgb") if isinstance(entry, dict) else entry
            row = _PaletteRow(
                rgb,
                role=entry.get("role", "") if isinstance(entry, dict) else "",
                role_candidates=candidates,
                parent=self._list_host,
            )
            row.remove_requested.connect(
                lambda _checked=False, idx=i: self._on_remove(idx)
            )
            row.role_changed.connect(
                lambda role, idx=i: self._on_role_changed(idx, role)
            )
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        has = len(palette) > 0
        self._scroll.setVisible(has)
        self._empty_label.setVisible(not has)
        self.clear_btn.setEnabled(has)

    @staticmethod
    def _role_candidates(palette) -> list:
        """角色名候选＝已有非空角色（保持调板顺序，UI 去重）。"""
        seen, out = set(), []
        for entry in palette or []:
            role = entry.get("role", "") if isinstance(entry, dict) else ""
            if role and role not in seen:
                seen.add(role)
                out.append(role)
        return out

    def _on_role_changed(self, idx: int, role: str):
        pal = self._proj.fg_palette
        if 0 <= idx < len(pal) and isinstance(pal[idx], dict):
            pal[idx]["role"] = role
            self._save()
            # 改名后候选集变了：整表重载（保持各行的候选与显示新鲜）
            self._reload_palette()

    def _on_remove(self, idx: int):
        if self._proj is None:
            return
        pal = self._proj.fg_palette
        if 0 <= idx < len(pal):
            entry = pal[idx]
            role = entry.get("role", "") if isinstance(entry, dict) else ""
            rgb = entry.get("rgb") if isinstance(entry, dict) else entry
            pal.pop(idx)
            if role:
                used = self._count_blocks_with_color(rgb)
                self.notify_requested.emit(
                    self.tr("Removed %1; the color was used by %2 block(s)")
                    .replace("%1", _PaletteRow.display_role(role))
                    .replace("%2", str(used)),
                    "info",
                )
            self._reload_palette()
            self._save()

    def _count_blocks_with_color(self, rgb) -> int:
        """该色当前被多少块使用（精确相等口径，与注入判据一致）。"""
        from utils.proj_imgtrans import normalize_rgb

        target = normalize_rgb(rgb)
        if target is None:
            return 0
        used = 0
        for blk_list in (self._proj.pages or {}).values():
            for blk in blk_list:
                if normalize_rgb(getattr(blk, "fg_colors", None)) == target:
                    used += 1
        return used

    def _on_clear(self):
        if self._proj is None:
            return
        self._proj.fg_palette.clear()
        self._reload_palette()
        self._save()

    def _save(self):
        if self._proj is not None and self._proj.proj_path:
            self._proj.save()
