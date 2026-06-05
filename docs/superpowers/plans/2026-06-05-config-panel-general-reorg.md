# Config Panel General Section Reorganization — Implementation Plan

> **面向 AI 代理的工作者：** 使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 重组设置面板 General 区域为三组（Project / Typesetting / Interface），优化 Typesetting 视觉密度，移除 Misc 杂项分组。

**架构：** 纯 UI 重组 — 控件和信号连接不变，仅改变分组容器和导航结构。影响文件：`ui/configpanel.py`（主）、`translate/zh_CN.ts`（翻译）、`config/stylesheet.css`（可选样式）。

**技术栈：** PyQt6, Python 3.10+

**设计文档：** `docs/superpowers/specs/2026-06-05-config-panel-general-reorg-design.md`

---

### 任务 1：合并 Startup + Save → Project 组

**文件：**
- 修改：`ui/configpanel.py:1209-1212,1271-1284,1457-1500`（标签、startup_widget、save_widget 构造）
- 修改：`ui/configpanel.py:1544-1556`（导航列表 sections）

- [ ] **步骤 1：替换分组标签**

将行 1209-1212 中的标签定义从：
```python
label_startup = self.tr("Startup")
label_typesetting = self.tr("Typesetting")
label_save = self.tr("Save")
label_shortcuts = self.tr("Miscellaneous")
```

改为：
```python
label_project = self.tr("Project")
label_typesetting = self.tr("Typesetting")
label_interface = self.tr("Interface")
```

- [ ] **步骤 2：替换 Startup 组为 Project 组**

删除行 1271-1284（startup_widget + startup_block 全部）：
```python
        # === General: Startup ===
        startup_widget = QWidget()
        startup_layout = QVBoxLayout(startup_widget)
        startup_layout.setContentsMargins(0, 0, 0, 0)
        self.open_on_startup_checker = QCheckBox(
            self.tr("Reopen last project on startup")
        )
        self.open_on_startup_checker.stateChanged.connect(
            self.on_open_onstartup_changed
        )
        startup_layout.addWidget(self.open_on_startup_checker)
        self.startup_block = generalConfigPanel.addGroupedBlock(
            label_startup, startup_widget, object_name="GroupGeneral"
        )
```

替换为 Project 组构建（启动复选框 + 后续 Save 控件将在同一组内）：
```python
        # === General: Project (startup + save merged) ===
        project_widget = QWidget()
        project_layout = QVBoxLayout(project_widget)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setSpacing(0)

        # Startup
        self.open_on_startup_checker = QCheckBox(
            self.tr("Reopen last project on startup")
        )
        self.open_on_startup_checker.stateChanged.connect(
            self.on_open_onstartup_changed
        )
        project_layout.addWidget(self.open_on_startup_checker)
```

- [ ] **步骤 3：将 Save 控件移入 Project 组**

删除行 1457-1500 中的 `save_widget` 构造和 `self.save_block = generalConfigPanel.addGroupedBlock(...)` 调用。将 Save 控件（rst_imgformat_combobox、rst_autoformat_checker、rst_imgquality_edit、intermediate_imgformat_combobox）的创建代码移到步骤 2 的 `project_layout` 中，放在启动复选框之后：

在 `project_layout.addWidget(self.open_on_startup_checker)` 之后插入：

```python
        # Output section label
        output_label = ConfigTextLabel(
            self.tr("Output"), CONFIG_FONTSIZE_CONTENT - 2
        )
        project_layout.addWidget(output_label)

        # JXL removed from options: pillow-jxl-plugin compatibility issues
        self.rst_imgformat_combobox, imsave_sublock = combobox_with_label(
            ["PNG", "JPG", "WEBP"], self.tr("Result image format"), parent=self
        )
        self.rst_imgformat_combobox.activated.connect(self.on_rst_imgformat_changed)
        project_layout.addWidget(imsave_sublock)

        self.rst_autoformat_checker, autoformat_sublock = checkbox_with_label(
            self.tr("Auto detect source format")
        )
        self.rst_autoformat_checker.stateChanged.connect(self.on_autoformat_changed)
        project_layout.addWidget(autoformat_sublock)

        self.rst_imgquality_edit = PercentageLineEdit("100")
        self.rst_imgquality_edit.setFixedWidth(CONFIG_COMBOBOX_SHORT)
        self.rst_imgquality_edit.finish_edited.connect(self.on_edit_quality_changed)

        quality_sublock = ConfigSubBlock(
            self.rst_imgquality_edit, self.tr("Quality"), vertical_layout=False
        )
        quality_sublock.layout().setAlignment(Qt.AlignmentFlag.AlignLeft)
        quality_sublock.layout().insertStretch(-1)
        imsave_sublock.layout().addWidget(quality_sublock)

        # JXL removed from options
        self.intermediate_imgformat_combobox, intermediate_imsave_sublock = (
            combobox_with_label(
                ["PNG"], self.tr("Intermediate image format"), parent=self
            )
        )
        self.intermediate_imgformat_combobox.activated.connect(
            self.on_intermediate_imgformat_changed
        )
        project_layout.addWidget(intermediate_imsave_sublock)

        self.project_block = generalConfigPanel.addGroupedBlock(
            label_project, project_widget, object_name="GroupGeneral"
        )
```

- [ ] **步骤 4：更新导航列表 sections**

将行 1544-1556 的 sections 列表从：
```python
        sections = [
            ("_header", self.tr("DL Module")),
            (self.detect_sub_block.section_widget, label_text_det),
            (self.ocr_sub_block.section_widget, label_text_ocr),
            (self.inpaint_sub_block.section_widget, label_inpaint),
            (self.trans_sub_block.section_widget, label_translator),
            ("_sep", None),
            ("_header", self.tr("General")),
            (self.startup_block.section_widget, label_startup),
            (self.typesetting_block.section_widget, label_typesetting),
            (self.save_block.section_widget, label_save),
            (self.shortcut_block.section_widget, label_shortcuts),
        ]
```

改为（注意此时 `self.project_block` 和 `self.interface_block` 还未创建，先改为引用 `label_project` 和 `label_interface`；实际的 widget 引用在后续任务中由各自组的 `addGroupedBlock` 创建）：
```python
        sections = [
            ("_header", self.tr("DL Module")),
            (self.detect_sub_block.section_widget, label_text_det),
            (self.ocr_sub_block.section_widget, label_text_ocr),
            (self.inpaint_sub_block.section_widget, label_inpaint),
            (self.trans_sub_block.section_widget, label_translator),
            ("_sep", None),
            ("_header", self.tr("General")),
            (self.project_block.section_widget, label_project),
            (self.typesetting_block.section_widget, label_typesetting),
            (self.interface_block.section_widget, label_interface),
        ]
```

注意：`sections` 列表构造位于 `__init__` 末尾（行 1544），在所有 `addGroupedBlock` 之后。但由于 Project 组的 `addGroupedBlock` 现在在最前面，`self.project_block` 已经存在；`self.typesetting_block` 将在任务 2 末尾创建；`self.interface_block` 将在任务 3 末尾创建。

- [ ] **步骤 5：验证 Project 组**

在项目根目录运行应用，打开设置面板：
- 确认 "General" 标题下左侧导航显示 "Project"、"Typesetting"、"Interface" 三项
- 确认 Project 组包含启动复选框和输出格式控件
- 确认切换复选框、修改 Quality 值时 `config.json` 中对应字段更新

---

### 任务 2：Typesetting 组优化 — 紧凑容器包装委托网格

**文件：**
- 修改：`ui/configpanel.py:1286-1455`（委托网格 + 排版行为控件）
- 可选修改：`config/stylesheet.css`（新增 `#CompactDelegationFrame` 样式）

- [ ] **步骤 1：添加紧凑容器 CSS（可选，建议做）**

在 `config/stylesheet.css` 末尾添加：
```css
#CompactDelegationFrame {
    background-color: @widgetBackgroundColor;
    border: 1px solid @borderColor;
    border-radius: 6px;
}
```

如果不加 CSS，则使用 `QFrame` + `setStyleSheet` 内联样式。

- [ ] **步骤 2：在委托网格外围包 QFrame 容器**

找到委托网格的构建代码（行 1289-1381，从 `# Build typesetting wrapper widget` 到 `global_fntfmt_layout.addItem(QSpacerItem(...))`）。

在 `ts_layout.addWidget(b)` 之前，插入一个 QFrame 包装：

```python
        # Compact container for font format delegation grid
        delegation_frame = QFrame()
        delegation_frame.setObjectName("CompactDelegationFrame")
        delegation_layout = QVBoxLayout(delegation_frame)
        delegation_layout.setContentsMargins(12, 8, 12, 8)
        delegation_layout.setSpacing(4)

        # Context label
        delegation_label = ConfigTextLabel(
            self.tr("Default font format (when not set per-textblock):"),
            CONFIG_FONTSIZE_CONTENT - 2,
        )
        delegation_layout.addWidget(delegation_label)
```

然后将原来 `b = ConfigSubBlock(global_fntfmt_widget)` 和 `ts_layout.addWidget(b)` 两行改为添加到 `delegation_layout`：

```python
        b = ConfigSubBlock(global_fntfmt_widget)
        b.layout().setContentsMargins(0, 0, 0, 0)
        b.setContentsMargins(0, 0, 0, 0)
        delegation_layout.addWidget(b)
```

最后将 `delegation_frame` 加入 `ts_layout`：
```python
        ts_layout.addWidget(delegation_frame)
```

- [ ] **步骤 3：缩短委托网格中的下拉框宽度**

`combobox_with_label` 函数（在 `configpanel.py` 之外定义，行 177-207）使用 `ConfigComboBox(fix_size=True, scrollWidget=parent)` 创建下拉框，`fix_size=True` 时使用 `CONFIG_COMBOBOX_SHORT`（200px）。

对于委托网格的 8 个下拉框，200px 对于 "decide by program" / "use global setting" 这类短文本来说过宽。在 `combobox_with_label` 调用之后手动设置更窄的固定宽度：

在行 1304-1373 的每个 `combobox_with_label` 调用后，对返回的 `combox` 调用 `.setFixedWidth(SHORT_DELEGATION_COMBO)`。

```python
# 在 __init__ 顶部附近（靠近 dec_program_str 定义之后）添加常量：
DELEGATION_COMBO_WIDTH = 140  # narrower than CONFIG_COMBOBOX_SHORT=200

# 对每个委托组合框，在 combobox_with_label 调用和 signal connect 之间插入：
combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
```

例如，对于 Font Size：
```python
        self.let_fntsize_combox, sublock = combobox_with_label(
            [dec_program_str, use_global_str],
            self.tr("Font Size"),
            parent=self,
            insert_stretch=True,
        )
        self.let_fntsize_combox.setFixedWidth(DELEGATION_COMBO_WIDTH)
        self.let_fntsize_combox.activated.connect(self.on_fntsize_flag_changed)
        global_fntfmt_layout.addWidget(sublock, 0, 0)
```

对全部 8 个组合框重复此操作：let_fntsize_combox, let_fntstroke_combox, let_fntcolor_combox, let_fnt_scolor_combox, let_effect_combox, let_alignment_combox, let_writing_mode_combox, let_family_combox。

- [ ] **步骤 4：添加 "Text formatting" 子标签**

在 `ts_layout.addWidget(al_sublock)` 之前（行 1390 附近），插入子标签：

```python
        # Text formatting sub-label
        fmt_label = ConfigTextLabel(
            self.tr("Text formatting"), CONFIG_FONTSIZE_CONTENT - 2
        )
        ts_layout.addWidget(fmt_label)
```

- [ ] **步骤 5：将 Presets 区从 Typesetting 中移除（暂存注释或待后续任务迁移）**

将行 1422-1451 的预设区代码（从 `# --- Preset values for font format combo boxes ---` 到 `_make_preset_row(self.tr("Opacity:"), "opacity_presets")`）整个删除。这些代码将在任务 3 中重新放到 Interface 组。

**注意：** `self._preset_editors = {}` 在行 1423 初始化。保留这行不动（或移到 Interface 组代码中）。`_make_preset_row` 是 `__init__` 内部的局部函数 — 也需要保留其定义，在任务 3 中需要再次调用它。

实际操作：将 `self._preset_editors = {}` 和 `_make_preset_row` 函数定义移到 __init__ 的较早位置（在 `# Build typesetting wrapper widget` 之前），以便在 Typesetting 和 Interface 中都能引用（但只在 Interface 中调用）。

更简单的方案：保留 `self._preset_editors = {}` 在行 1423，但将整个 preset 构建块注释掉。在任务 3 的 Interface 组代码中重新实现 preset 构建。

- [ ] **步骤 6：验证 Typesetting 组**

运行应用：
- 确认 Typesetting 组中委托网格被深色容器包围
- 确认容器上方有 "Default font format..." 引导文字
- 确认 "Text formatting" 子标签在委托网格和 checkboxes 之间
- 确认 Combo Box Presets 已移除
- 确认所有下拉框和复选框功能正常

---

### 任务 3：Interface 组 — 重命名 Misc + 吸入 Presets

**文件：**
- 修改：`ui/configpanel.py:1502-1534`（misc_widget 构造）

- [ ] **步骤 1：重命名 Misc 为 Interface 并添加子标签**

将行 1502-1534 的 Misc 构造替换为 Interface 构造。

原代码：
```python
        # === General: Miscellaneous (shortcut editor + animation) ===
        misc_widget = QWidget()
        misc_layout = QVBoxLayout(misc_widget)
        misc_layout.setContentsMargins(0, 0, 0, 0)
        misc_layout.setSpacing(8)

        # Animation mode
        anim_row = QHBoxLayout()
        anim_row.setSpacing(6)
        anim_label = QLabel(self.tr("Animation"))
        ...
```

改为：
```python
        # === General: Interface (animation + shortcuts + presets) ===
        interface_widget = QWidget()
        interface_layout = QVBoxLayout(interface_widget)
        interface_layout.setContentsMargins(0, 0, 0, 0)
        interface_layout.setSpacing(0)

        # Behavior sub-label
        behavior_label = ConfigTextLabel(
            self.tr("Behavior"), CONFIG_FONTSIZE_CONTENT - 2
        )
        interface_layout.addWidget(behavior_label)

        # Animation mode
        anim_row = QHBoxLayout()
        anim_row.setSpacing(6)
        anim_label = QLabel(self.tr("Animation"))
        anim_row.addWidget(anim_label)
        self.anim_combo = ConfigComboBox()
        self.anim_combo.setFixedWidth(CONFIG_COMBOBOX_MIDEAN)
        self.anim_combo.addItems([
            self.tr("Auto (match display)"),
            "60 FPS",
            "30 FPS",
            self.tr("Off (no animation)"),
        ])
        self.anim_combo.activated.connect(self._on_anim_mode_changed)
        anim_row.addWidget(self.anim_combo)
        anim_row.addStretch()
        interface_layout.addLayout(anim_row)

        # Shortcut button
        self.shortcut_btn = QPushButton(self.tr("Edit Shortcuts..."), parent=self)
        self.shortcut_btn.setFixedWidth(CONFIG_COMBOBOX_LONG + 32)
        self.shortcut_btn.clicked.connect(self._open_shortcut_dialog)
        interface_layout.addWidget(self.shortcut_btn)
```

- [ ] **步骤 2：在 Interface 组中添加 Combo Box Presets**

紧接步骤 1 的代码之后，添加 Presets 区：

```python
        # Combo Box Presets (moved from Typesetting)
        preset_header = QLabel(self.tr("Combo Box Presets"))
        preset_header.setStyleSheet("font-weight: bold; padding: 12px 0 4px 24px;")
        interface_layout.addWidget(preset_header)

        # Helper label
        preset_hint = ConfigTextLabel(
            self.tr("Comma-separated values — used in font format panel dropdowns."),
            CONFIG_FONTSIZE_CONTENT - 3,
        )
        interface_layout.addWidget(preset_hint)

        def _make_preset_row(label: str, config_key: str):
            """Build a label + comma-separated QLineEdit row for a preset list."""
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFixedWidth(110)
            row.addWidget(lbl)
            edit = QLineEdit()
            edit.setText(", ".join(str(v) for v in getattr(pcfg, config_key)))
            edit.setPlaceholderText(self.tr("comma-separated values"))
            row.addWidget(edit, 1)
            sublock = ConfigSubBlock(row)
            interface_layout.addWidget(sublock)
            self._preset_editors[config_key] = edit
            edit.editingFinished.connect(
                lambda k=config_key, e=edit: self._on_preset_edited(k, e)
            )

        _make_preset_row(self.tr("Font Size:"), "font_size_presets")
        _make_preset_row(self.tr("Line Spacing:"), "line_spacing_presets")
        _make_preset_row(self.tr("Letter Spacing:"), "letter_spacing_presets")
        _make_preset_row(self.tr("Stroke Width:"), "stroke_width_presets")
        _make_preset_row(self.tr("Opacity:"), "opacity_presets")
```

- [ ] **步骤 3：注册 Interface 组**

在 Presets 代码块之后，添加 `addGroupedBlock` 调用：

```python
        self.interface_block = generalConfigPanel.addGroupedBlock(
            label_interface, interface_widget, object_name="GroupGeneral"
        )
```

**注意：** 原 `self.shortcut_block` 属性参照被替换为 `self.interface_block`。检查代码中是否有其他地方引用 `self.shortcut_block` — 当前只有一个地方：`setupConfig` 中不引用它，仅在 nav 中通过 `self.shortcut_block.section_widget` 使用（已在任务 1 中更新为 `self.interface_block.section_widget`）。

- [ ] **步骤 4：确保 `self._preset_editors = {}` 在 preset 构建之前初始化**

检查 `self._preset_editors = {}` 是否在 `_make_preset_row` 调用之前定义。如果原本在 Typesetting 的 preset 区定义的那一行被删除了，需要确保在 Interface 组代码之前补充：

```python
        self._preset_editors = {}
```

放在步骤 2 的 `def _make_preset_row(...)` 之前。

- [ ] **步骤 5：验证 Interface 组**

运行应用：
- 确认 Interface 组包含 Animation 下拉框、Edit Shortcuts 按钮、Combo Box Presets
- 确认修改预设值后 `config.json` 更新
- 确认 `presets_changed` 信号正常 emit
- 确认 ShortcutEditor 对话框正常弹出
- 确认动画模式切换生效

---

### 任务 4：清理与最终验证

**文件：**
- 修改：`ui/configpanel.py`（全局检查）
- 修改：`translate/zh_CN.ts`（i18n 新字符串）

- [ ] **步骤 1：检查残留引用**

在当前文件中搜索以下已删除的属性名，确保没有遗留引用：
- `self.startup_block` — 应不存在（被 `self.project_block` 取代）
- `self.save_block` — 应不存在（被 `self.project_block` 取代）
- `self.shortcut_block` — 应不存在（被 `self.interface_block` 取代）

在项目根目录运行：
```bash
grep -n "startup_block\|save_block\|shortcut_block" ui/configpanel.py
```
预期无输出。

- [ ] **步骤 2：运行 i18n 检查**

在项目根目录运行：
```bash
python scripts/i18n_check.py
```
注意新增的 `self.tr("Project")`、`self.tr("Interface")` 等字符串是否出现在缺失列表中。如果缺失，更新 `translate/zh_CN.ts`。

- [ ] **步骤 3：编译翻译并验证 UI**

```bash
python scripts/qm_compile.py translate/zh_CN.ts translate/zh_CN.qm
```

切换语言为中文，确认所有新标签（Project → 项目, Interface → 界面）显示正确。

- [ ] **步骤 4：完整功能回归**

逐项检查：
- [ ] Project：启动复选框 + 输出格式/质量/中间格式 全功能正常
- [ ] Typesetting：8 个委托下拉框 + 3 个复选框 + Exclude Fonts + Max Font Size 全功能正常
- [ ] Interface：动画 + 快捷键编辑 + 5 个预设编辑 全功能正常
- [ ] 左侧导航：点击各标签正确滚动到对应分组
- [ ] 滚动同步：手动滚动画布时左侧导航自动高亮对应项
- [ ] 配置持久化：关闭设置面板后 `config.json` 保存所有修改
- [ ] DL Module 区（Text Detection/OCR/Inpaint/Translator）未受影响

- [ ] **步骤 5：Commit**

```bash
git add ui/configpanel.py translate/zh_CN.ts translate/zh_CN.qm config/stylesheet.css
git commit -m "refactor: reorganize General settings into Project/Typesetting/Interface groups

- Merge Startup + Save into Project group
- Wrap Typesetting delegation grid in compact container
- Move Combo Box Presets from Typesetting to Interface
- Rename Miscellaneous to Interface
- Reduce nav items under General from 4 to 3"
```
