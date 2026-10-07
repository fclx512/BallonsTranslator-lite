"""工作台「取色调板」页：人工采样各角色文字色，自动取色按最近原则吸附。

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
保持。首版塞在 460px 侧栏里的自绘取样视图已整体删除——画布本身就是现成的
取样区，侧栏那份既浪费空间又多一套缩放/平移要学。写回（重取色）后由面板
注入的 ``refresh_canvas`` 回调刷新画布当前页视觉层。

**全书扫描 → 候选色 → 勾选入调板**（2026-10-03 三版）：``Scan Book Colors``
读块上已提取的 ``fg_colors``（检测阶段写的，不重跑管线），经
``utils/color_extract.py::scan_project_colors`` LAB 聚类出候选；每行给代表色、
命中块数、最多 4 张样本缩略图（缩略图经共享的
``ui/workbench_tasks.py::PageImageCache`` 裁，几何不另写一份）。候选**只是草稿**：
勾选后点 ``Add Checked to Palette`` 才入调板，且状态全在内存（换项目/重扫即清，
不落项目 JSON）。命中 ``PALETTE_SNAP_DIST`` 的候选标「已在调板」并禁勾；
行上的「改色」把下一个画布取样路由到该候选（改代表色、不追加条目）。
"""

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigCheckBox, ConfigComboBox
from ui.workbench_batch_view import to_pixmap
from ui.workbench_tasks import PageImageCache

# 色表滚动区上限：再多颜色也不把页撑长（滚动看；行内加了角色名下拉后放高些）
_LIST_MAX_H = 180
# 角色名下拉的固定宽度：占位名短，真名给到能看全常用长度
_ROLE_COMBO_W = 130
# 候选样本缩略图（460px 页宽：4 张 + 间距仍排得下；按比例缩放不裁切）
_THUMB_MAX_W = 72
_THUMB_MAX_H = 52
_MAX_THUMBS = 4
# 候选列表滚动区上限：候选多时也不把页撑长
_CAND_LIST_MAX_H = 200


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
        self.role_combo.setFixedWidth(_ROLE_COMBO_W)
        self.role_combo.setEditable(True)
        editor = self.role_combo.lineEdit()
        if editor is not None:
            editor.setClearButtonEnabled(False)
            editor.editingFinished.connect(self._on_edit_finished)
        self.role_combo.activated.connect(self._on_activated)
        self.set_role(self._role, role_candidates)
        layout.addWidget(self.role_combo)

        layout.addStretch(1)

        del_btn = QPushButton("✕", self)
        del_btn.setFixedSize(22, 22)
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


class _SampleThumb(QLabel):
    """候选样本缩略图：点击 → 跳到该块（只读跳转，不写数据）。

    页面私有控件（不进 ``ui/custom_widget/``——那里导出要被展示台登记）。
    固定外框、图片按比例居中，故一行的缩略图高低齐平。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_THUMB_MAX_W, _THUMB_MAX_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _CandidateRow(QWidget):
    """一条候选色（草稿）：勾选＋色卡＋HEX＋块数＋[已在调板]＋改色；下行样本缩略图。

    ``candidate`` 是页面持有的内存字典（``rgb``/``count``/``samples``/
    ``checked``/``in_palette``）：本行只是它的视图，勾选写回字典，**不入项目 JSON**。
    缩略图一律经共享的 ``ui/workbench_tasks.py::PageImageCache`` 裁（不自己写
    外扩/裁剪几何）；一张都裁不出时显示「无可显示区域」。
    """

    toggled = Signal(bool)
    resample_requested = Signal(int)
    jump_requested = Signal(str, int)

    def __init__(self, candidate, index, *, crops=None, parent=None):
        super().__init__(parent)
        self._index = int(index)
        self._crops = crops
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.check = ConfigCheckBox("", self)
        in_palette = bool(candidate.get("in_palette"))
        self.check.setChecked(False if in_palette else bool(candidate.get("checked")))
        # 「已在调板」：命中吸附距离 → 禁勾（再入板只会重复）
        self.check.setEnabled(not in_palette)
        self.check.toggled.connect(lambda checked: self.toggled.emit(bool(checked)))
        head.addWidget(self.check)

        rgb = [int(v) for v in (candidate.get("rgb") or [0, 0, 0])[:3]]
        r, g, b = (max(0, min(255, value)) for value in rgb)
        chip = QLabel(self)
        chip.setFixedSize(44, 18)
        chip.setStyleSheet(
            f"background-color: rgb({r},{g},{b});"
            "border: 1px solid rgba(128,128,128,120); border-radius: 3px;"
        )
        head.addWidget(chip)
        head.addWidget(QLabel(_fmt_hex(rgb), self))
        count = int(candidate.get("count") or 0)
        head.addWidget(
            QLabel(self.tr("%1 block(s)").replace("%1", str(count)), self)
        )
        if in_palette:
            tag = QLabel(self.tr("[In palette]"), self)
            tag.setToolTip(
                self.tr("Within the palette snap distance; nothing to add.")
            )
            head.addWidget(tag)
        head.addStretch(1)

        self.recolor_btn = QPushButton(self.tr("Recolor"), self)
        self.recolor_btn.setObjectName("ConfigButton")
        self.recolor_btn.setToolTip(
            self.tr(
                "Sample a glyph on the canvas to replace this candidate's color (no new palette entry)."
            )
        )
        self.recolor_btn.clicked.connect(
            lambda _checked=False: self.resample_requested.emit(self._index)
        )
        head.addWidget(self.recolor_btn)
        layout.addLayout(head)

        thumbs = QHBoxLayout()
        thumbs.setSpacing(4)
        self._add_thumbs(thumbs, list(candidate.get("samples") or []))
        thumbs.addStretch(1)
        layout.addLayout(thumbs)

    def _add_thumbs(self, layout, samples):
        shown = 0
        for sample in samples[:_MAX_THUMBS]:
            pixmap = None
            if self._crops is not None:
                cropped = self._crops.crop_around(
                    sample.pagename, sample.rect, exclude={sample.block_index}
                )
                if cropped is not None:
                    pixmap = to_pixmap(cropped[0])
            if pixmap is None or pixmap.isNull():
                continue
            thumb = _SampleThumb(self)
            thumb.setPixmap(
                pixmap.scaled(
                    _THUMB_MAX_W,
                    _THUMB_MAX_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            thumb.setToolTip(self.tr("Jump to this block"))
            thumb.clicked.connect(
                lambda pagename=sample.pagename,
                block_index=sample.block_index: self.jump_requested.emit(
                    pagename, block_index
                )
            )
            layout.addWidget(thumb)
            shown += 1
        if shown == 0:
            empty = QLabel(self.tr("No displayable area"), self)
            empty.setWordWrap(True)
            layout.addWidget(empty)


class FgPalettePage(QWidget):
    """取色调板页：取样开关 + 色表 + 重取色/扫描 + 候选区（取样区＝真画布）。

    对外接口（W3 的候选区在此之上叠加，不要回退）：

    - ``notify_requested``：页内提示 → 面板统一走 toast
    - ``sampling_toggled``：取样开关 → 面板转发给画布取色模式
    - ``jump_requested``：样本/候选 → 跳画布（面板已有同名信号可转发）
    - ``accept_canvas_sample``：画布取色回流（页是取色的数据落点）
    - ``set_sampling_state``：画布侧状态回流（blockSignals 防回环）
    - ``set_sample_target``：把下一个画布取样路由到某候选（「改色」；W3）
    """

    # (文案, kind) → 面板统一走 toast（与批量任务视图同约定）
    notify_requested = Signal(str, str)
    sampling_toggled = Signal(bool)
    jump_requested = Signal(str, int)

    def __init__(self, proj, refresh_canvas=None, parent=None):
        super().__init__(parent)
        self._proj = proj
        self._refresh_canvas = refresh_canvas  # 写回后刷新画布当前页（面板注入）
        self._rows: list = []
        # 候选区（内存态，绝不落项目 JSON）：扫描结果 / 勾选 / 待改目标
        self._crops = PageImageCache(proj)  # 组合复用裁剪几何（不继承）
        self._candidates: list = []
        self._candidate_rows: list = []
        self._scan_summary = None
        self._sample_target = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
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
                "Click a text glyph on the canvas to sample its color. Pages keep sampling while the mode is on (use the canvas page shortcuts), and Esc turns it off. The palette is saved with the project; auto color extraction snaps to the nearest palette color. Each color gets a placeholder name you can replace with the character's name — tagged colors are injected to the translator as speaker hints."
            ),
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 色表 ─────────────────────────────────────────────
        self._list_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._list_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        scroll.setMaximumHeight(_LIST_MAX_H)
        scroll.setObjectName("WorkbenchPaletteList")
        self._scroll = scroll
        layout.addWidget(scroll)

        self._empty_label = QLabel(
            self.tr(
                "No colors yet. Turn on sampling and click text on the canvas. Each sampled color gets a placeholder name (A, B, …) for speaker hints."
            ),
            self,
        )
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        self.clear_btn = QPushButton(self.tr("Clear Palette"), self)
        self.clear_btn.setObjectName("ConfigButton")
        self.clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(self.clear_btn)

        sep = QFrame(self)
        sep.setObjectName("fmtGroupSeparator")
        sep.setFixedHeight(1)
        sep.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(sep)

        note = QLabel(
            self.tr(
                "Re-extract refills existing blocks' font colors from the source image (palette colors take priority)."
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.reapply_page_btn = QPushButton(self.tr("Re-extract Current Page"), self)
        self.reapply_page_btn.setObjectName("ConfigButton")
        self.reapply_page_btn.clicked.connect(self._on_reapply_page)
        bottom.addWidget(self.reapply_page_btn)
        self.reapply_book_btn = QPushButton(self.tr("Re-extract Whole Book"), self)
        self.reapply_book_btn.setObjectName("ConfigButton")
        self.reapply_book_btn.clicked.connect(self._on_reapply_book)
        bottom.addWidget(self.reapply_book_btn)
        # 全书扫描与重取色同排：两者都是「对已有块的颜色动手」的入口
        self.scan_btn = QPushButton(self.tr("Scan Book Colors"), self)
        self.scan_btn.setObjectName("ConfigButton")
        self.scan_btn.setToolTip(
            self.tr(
                "List the text colors already extracted on blocks across the book. Suggestions only — nothing is added to the palette until you tick colors and apply."
            )
        )
        self.scan_btn.clicked.connect(self._on_scan_colors)
        bottom.addWidget(self.scan_btn)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        # ── 候选色区（扫描后出现；草稿，勾选+应用才入调板）────────
        sep2 = QFrame(self)
        sep2.setObjectName("fmtGroupSeparator")
        sep2.setFixedHeight(1)
        sep2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(sep2)

        self._cand_hint = QLabel(
            self.tr(
                "Scan the book to see which text colors already exist on blocks. Candidates are suggestions — check the ones that are really text and add them; nothing is written before that."
            ),
            self,
        )
        self._cand_hint.setWordWrap(True)
        layout.addWidget(self._cand_hint)

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._singleton_check = ConfigCheckBox(
            self.tr("Show colors that appear only once"), self
        )
        self._singleton_check.toggled.connect(self._on_show_singletons)
        layout.addWidget(self._singleton_check)

        self._cand_host = QWidget(self)
        self._cand_layout = QVBoxLayout(self._cand_host)
        self._cand_layout.setContentsMargins(0, 0, 0, 0)
        self._cand_layout.setSpacing(6)
        self._cand_scroll = QScrollArea(self)
        self._cand_scroll.setWidgetResizable(True)
        self._cand_scroll.setWidget(self._cand_host)
        self._cand_scroll.setMaximumHeight(_CAND_LIST_MAX_H)
        self._cand_scroll.setObjectName("WorkbenchPaletteCandidates")
        layout.addWidget(self._cand_scroll)

        cand_footer = QHBoxLayout()
        cand_footer.setSpacing(6)
        self._add_checked_btn = QPushButton(self.tr("Add Checked to Palette"), self)
        self._add_checked_btn.setObjectName("ConfigButton")
        self._add_checked_btn.clicked.connect(self._on_add_checked)
        cand_footer.addWidget(self._add_checked_btn)
        self._select_none_btn = QPushButton(self.tr("Select None"), self)
        self._select_none_btn.setObjectName("ConfigButton")
        self._select_none_btn.clicked.connect(self._on_select_none)
        cand_footer.addWidget(self._select_none_btn)
        cand_footer.addStretch(1)
        layout.addLayout(cand_footer)

        self.refresh_state()

    # ── 对面板的接口 ──────────────────────────────────────────

    def refresh_state(self):
        """开工程/换项目后同步：清取样显示态、清候选、重挂色表。

        ``proj`` 为 None（面板空态构造）时同样要能建页：清空后照常重挂
        （空表）。真正的画布取色模式由面板在 `refresh_project_state` 里先
        强制解除，这里只保证本页开关不残留旧状态。候选是**内存草稿**，
        换项目即整体作废（不落项目 JSON）。
        """
        self.set_sampling_state(False)
        self._reset_candidates()
        # 页图缓存必须随项目作废：``PageImageCache`` 只按页名索引，两个项目
        # 同名页（001.jpg 之类）会静默读到上一个项目的像素——取样色与候选
        # 缩略图都会取错，且没有任何报错。
        self._crops = PageImageCache(self._proj)
        self._reload_palette()

    def set_sampling_state(self, on: bool):
        """画布侧状态回流：只改开关显示，**不发** ``sampling_toggled``（防回环）。

        开关被画布自行解除（Esc／模式冲突／换项目）时面板回灌 False，开关
        与提示必须跟着回到「未取样」，否则界面与实际交互不一致。取样解除后
        待改目标也作废（留着会让下次重新武装时静默改错候选）。
        """
        on = bool(on)
        if not on and self._sample_target is not None:
            self._sample_target = None
        self.sample_btn.blockSignals(True)
        self.sample_btn.setChecked(on)
        self.sample_btn.blockSignals(False)
        self._apply_sample_button_state(on)

    def accept_canvas_sample(self, pagename: str, x: int, y: int):
        """画布取色回流：读原图像素。

        - **有待改目标**（行上「改色」设的）：改该候选的代表色 → 清目标 →
          重渲染候选行，**不**追加调板条目；
        - **无待改目标**：判重（``PALETTE_DUP_DIST`` 内近重不入板）→
          追加 ``{"rgb": …, "role": next_palette_role()}``（原语义不变）。
        """
        proj = self._proj
        if proj is None or not pagename or pagename not in proj.pages:
            return
        # 走本页已有的页图缓存：连点取样不该每下重读一次整页原图
        # （与候选缩略图共用同一份缓存，扫完再取样已经是热的）。
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

        target = self._sample_target
        if target is not None:
            self._sample_target = None
            if 0 <= target < len(self._candidates):
                self._candidates[target]["rgb"] = rgb
                self._candidates[target]["in_palette"] = self._rgb_in_palette(rgb)
                self._render_candidates()
                self.notify_requested.emit(
                    self.tr("Candidate color changed to %1").replace(
                        "%1", _fmt_hex(rgb)
                    ),
                    "info",
                )
            self._apply_sample_button_state(self.sample_btn.isChecked())
            return

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

    def set_sample_target(self, candidate_index):
        """把下一个画布取样路由到某候选（「改色」）；``None`` 清除目标。

        需要画布武装时顺手把取样开关拨到开（走 ``sampling_toggled`` 那条
        既有链路，不另立入口）；画布拒绝武装时面板会回灌
        ``set_sampling_state(False)``，目标随之作废。
        """
        if candidate_index is None:
            self._sample_target = None
            self._apply_sample_button_state(self.sample_btn.isChecked())
            return
        try:
            index = int(candidate_index)
        except (TypeError, ValueError):
            return
        if not 0 <= index < len(self._candidates):
            return
        self._sample_target = index
        if not self.sample_btn.isChecked():
            self.sample_btn.setChecked(True)  # → _on_sample_toggled → sampling_toggled
        else:
            self._apply_sample_button_state(True)

    # ── 内部：取样开关 ────────────────────────────────────────

    def _on_sample_toggled(self, on: bool):
        self._apply_sample_button_state(on)
        self.sampling_toggled.emit(on)

    def _apply_sample_button_state(self, on: bool):
        if on and self._sample_target is not None:
            self.sample_btn.setText(self.tr("Sampling… (recolor candidate)"))
            self.sample_btn.setToolTip(
                self.tr(
                    "Click a text glyph on the canvas to replace the candidate's color; Esc stops sampling"
                )
            )
            return
        self.sample_btn.setText(
            self.tr("Sampling… (click glyph)") if on else self.tr("Sample on Canvas")
        )
        self.sample_btn.setToolTip(
            self.tr("Sampling is on: click a text glyph on the canvas (Esc stops it)")
            if on
            else self.tr("Toggle sampling, then click text glyphs on the canvas")
        )

    # ── 内部：全书扫描 → 候选（草稿，勾选+应用才入调板）─────────

    def _on_scan_colors(self):
        """同步跑 ``scan_project_colors``（给等待光标；量大时才让事件转起来）。"""
        proj = self._proj
        if proj is None or not getattr(proj, "pages", None):
            self.notify_requested.emit(self.tr("No project pages to scan."), "info")
            return
        from utils.color_extract import scan_project_colors

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            result = scan_project_colors(proj)
        finally:
            QApplication.restoreOverrideCursor()
        self._scan_summary = result
        self._sample_target = None
        self._apply_sample_button_state(self.sample_btn.isChecked())
        self._candidates = [
            {
                "rgb": list(cluster.rgb),
                "count": int(cluster.count),
                "samples": list(cluster.samples),
                "checked": False,
                "in_palette": self._rgb_in_palette(cluster.rgb),
            }
            for cluster in result.get("clusters") or []
        ]
        self._render_candidates()
        self._update_summary()

    def _rgb_in_palette(self, rgb) -> bool:
        """该色是否已在（吸附距离内的）调板里——命中则候选禁勾。"""
        from utils.color_extract import PALETTE_SNAP_DIST, nearest_palette_entry

        entry = nearest_palette_entry(rgb, self._proj.fg_palette)
        return entry is not None and entry[0] <= PALETTE_SNAP_DIST

    def _reset_candidates(self):
        """清候选状态（换项目/重扫即清；候选不落项目 JSON）。"""
        self._candidates = []
        self._candidate_rows = []
        self._scan_summary = None
        self._sample_target = None
        self._singleton_check.blockSignals(True)
        self._singleton_check.setChecked(False)
        self._singleton_check.blockSignals(False)
        self._render_candidates()
        self._update_summary()

    def _visible_candidates(self) -> list:
        """按「显示仅出现 1 次的颜色」筛出可见候选 ``(下标, 候选)``。"""
        show_singletons = self._singleton_check.isChecked()
        return [
            (index, candidate)
            for index, candidate in enumerate(self._candidates)
            if show_singletons or int(candidate.get("count") or 0) > 1
        ]

    def _render_candidates(self):
        """按当前筛选重建候选行（勾选态存在候选字典里，重建不丢）。"""
        for row in self._candidate_rows:
            row.setParent(None)
            row.deleteLater()
        self._candidate_rows.clear()
        for index, candidate in self._visible_candidates():
            row = _CandidateRow(
                candidate, index, crops=self._crops, parent=self._cand_host
            )
            row.toggled.connect(
                lambda checked, idx=index: self._on_candidate_toggled(idx, checked)
            )
            row.resample_requested.connect(self.set_sample_target)
            row.jump_requested.connect(
                lambda pagename, block_index: self.jump_requested.emit(
                    pagename, block_index
                )
            )
            self._cand_layout.addWidget(row)
            self._candidate_rows.append(row)
        has = bool(self._candidate_rows)
        self._cand_scroll.setVisible(has)
        self._update_apply_enabled()

    def _update_summary(self):
        """摘要行：共 N 块 / M 块有颜色 / 发现 K 种 + 单次色计数（可切换）。"""
        result = self._scan_summary
        if result is None:
            self._cand_hint.setVisible(True)
            self._summary_label.setVisible(False)
            self._singleton_check.setVisible(False)
            return
        self._cand_hint.setVisible(False)
        self._summary_label.setVisible(True)
        self._singleton_check.setVisible(True)
        clusters = list(result.get("clusters") or [])
        singletons = sum(1 for cluster in clusters if int(cluster.count) <= 1)
        text = (
            self.tr("%1 blocks scanned, %2 with a color; %3 colors found")
            .replace("%1", str(int(result.get("total_blocks") or 0)))
            .replace("%2", str(int(result.get("colored_blocks") or 0)))
            .replace("%3", str(len(self._visible_candidates())))
        )
        if singletons and not self._singleton_check.isChecked():
            text += " " + self.tr("(%1 more color(s) appear only once)").replace(
                "%1", str(singletons)
            )
        self._summary_label.setText(text)

    def _on_show_singletons(self, on: bool):
        # 关掉时把被隐藏的单次色取消勾选：不可见的行不能还被「应用」带走
        if not on:
            for candidate in self._candidates:
                if int(candidate.get("count") or 0) <= 1:
                    candidate["checked"] = False
        self._render_candidates()
        self._update_summary()

    def _on_candidate_toggled(self, index: int, checked: bool):
        if 0 <= index < len(self._candidates):
            self._candidates[index]["checked"] = bool(checked)
        self._update_apply_enabled()

    def _update_apply_enabled(self):
        has_checked = any(c.get("checked") for c in self._candidates)
        self._add_checked_btn.setEnabled(has_checked)
        self._select_none_btn.setEnabled(bool(self._candidates))

    def _on_select_none(self):
        for candidate in self._candidates:
            candidate["checked"] = False
        self._render_candidates()

    def _on_add_checked(self):
        """勾选的候选 → 调板条目（占位名）→ 落盘 → 重挂色表；候选保留。"""
        proj = self._proj
        if proj is None:
            return
        checked = [
            candidate
            for candidate in self._candidates
            if candidate.get("checked") and not candidate.get("in_palette")
        ]
        if not checked:
            return
        for candidate in checked:
            proj.fg_palette.append(
                {"rgb": list(candidate["rgb"]), "role": proj.next_palette_role()}
            )
            candidate["checked"] = False
            candidate["in_palette"] = True
        self._reload_palette()
        self._save()
        self._render_candidates()
        self._update_summary()
        self._update_apply_enabled()
        self.notify_requested.emit(
            self.tr(
                'Added %1 color(s) to the palette. To align already-extracted block colors, run "Re-extract Whole Book".'
            ).replace("%1", str(len(checked))),
            "info",
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
        self.reapply_page_btn.setEnabled(has)
        self.reapply_book_btn.setEnabled(has)
        # 调板一变，候选的「已在调板」标记就可能要翻（不然会重复入库）
        self._refresh_candidate_palette_flags()

    def _refresh_candidate_palette_flags(self):
        """调板变动后同步候选标记（不重扫；候选为空时是空操作）。"""
        changed = False
        for candidate in self._candidates:
            flag = self._rgb_in_palette(candidate["rgb"])
            if flag != bool(candidate.get("in_palette")):
                candidate["in_palette"] = flag
                if flag:
                    candidate["checked"] = False
                changed = True
        if changed:
            self._render_candidates()

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

    # ── 内部：按调板对已有块重取色 ────────────────────────────

    def _on_reapply_page(self):
        self._reapply("page")

    def _on_reapply_book(self):
        self._reapply("book")

    def _reapply(self, scope: str):
        """按调板对已有块重取色（scope: "page" | "book"）。

        与检测管线的取色写法一致（置信度闸门同 ``TextDetectorBase``）：
        块列表不换新、不动页代数（保住撤销锚点），改动页标记待重渲染；
        当前页的视觉层刷新经面板注入的回调走主窗口。
        """
        proj = self._proj
        if not proj.fg_palette or not proj.pages:
            return
        from modules.textdetector.base import TextDetectorBase
        from utils.color_extract import extract_block_colors

        if scope == "page":
            pages = [proj.current_img] if proj.current_img else []
        else:
            pages = list(proj.pages.keys())
        if not pages:
            return
        conf_min = TextDetectorBase._COLOR_CONF_MIN
        updated = total = 0
        changed_pages = []
        for name in pages:
            try:
                img = proj.read_img(name)
            except Exception:
                continue
            page_changed = False
            for blk in proj.pages.get(name, []):
                total += 1
                try:
                    res = extract_block_colors(img, blk.xyxy, palette=proj.fg_palette)
                except Exception:
                    continue
                if res is None or res.confidence < conf_min:
                    continue
                blk.fg_colors = res.frgb
                updated += 1
                page_changed = True
            if page_changed:
                proj.mark_page_needs_rerender(name)
                changed_pages.append(name)
        if not changed_pages:
            self.notify_requested.emit(
                self.tr("No blocks updated (nothing passed the confidence gate)."),
                "info",
            )
            return
        if proj.current_img in pages and self._refresh_canvas is not None:
            self._refresh_canvas()
        self._save()
        self.notify_requested.emit(
            self.tr("Re-extracted font colors for %1/%2 blocks")
            .replace("%1", str(updated))
            .replace("%2", str(total)),
            "info",
        )
