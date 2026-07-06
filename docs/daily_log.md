# 每日开发日志

> 此文档用于跨 agent 同步当日改动。仅保留最近 3 天的记录，超期内容自动清理。按照时间顺序撰写。

## 2026-07-07

### 设置面板窗口样式改为标准 Dialog + Esc 关闭

**需求：** 设置面板关闭按钮与正常 Windows 窗口样式不同（原 `Qt.WindowType.Tool` 产生小号 Tool 窗口标题栏），且不支持按 Esc 关闭。

**改动：**
- `ui/configpanel.py` — 窗口标志从 `Qt.WindowType.Tool` 改为 `Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint`，获得标准 Windows 对话框标题栏和关闭按钮
- `ui/configpanel.py` — `__init__` 末尾添加 `QShortcut(Qt.Key.Key_Escape)` → `_close_via_esc()` → `OverlayModal.hide()`（淡出动画关闭）

**涉及文件：** `ui/configpanel.py`

---

## 2026-07-06

### 文本框重排——画布右键菜单解除禁用

**需求：** 右键菜单中的 Reorder 子菜单在画布上右键时因 `is_textpanel` 条件被禁用。经核查无技术理由（画布选中与 TextPanel `checked_list` 已双向同步，`move_selected()` 对两者一视同仁），解除限制。

**改动：**
- `ui/canvas.py:1198` — `reorder_menu.setEnabled(is_textpanel and 0 < n_sel < n_total)` → `setEnabled(0 < n_sel < n_total)`

**涉及文件：** `ui/canvas.py`

---

### 文本框重排面板（撤回记录 — 现状已完整记录，改动已回滚）

**需求：** 右侧 TextPanel 中新增可折叠「重排文本框」面板 + 4 个键盘快捷键，替代仅靠拖拽重排。

**改动文件（5 文件，+285 行）：**

1. `ui/textedit_area.py` — 新增 `TextEditListScrollArea.move_selected()`（支持 up/down/top/bottom/to_pos 五种移动，全排列 diff 确保索引正确）；新增 `ReorderContent` 控件：Row1 = ▲▼⏫⏬ QToolButton，Row2 = sel_info_label + Pos 输入 + Go 按钮；连接 `selection_changed` 信号实时更新 UI
2. `ui/scenetext_manager.py` — `TextPanel` 在切换行下方插入 `CollapsibleSection`（`expanded=False`，默认折叠）；`on_rearrange_blks()` 中 reorder 后 emit `selection_changed` 刷新选择信息
3. `ui/configpanel.py` — `DEFAULT_SHORTCUTS`/`_ACTION_NAMES`/`_SHORTCUT_GROUPS` 新增 4 项（move_up/move_down/move_top/move_bottom）
4. `ui/mainwindow.py` — `_install_shortcuts()` 注册 4 个快捷键 + 对应 handler 方法
5. `translate/zh_CN.ts` — `TextPanel` 上下文新增 `"Reorder Text Blocks" → "重排文本框"`

**撤回原因：** 实机验证发现三个问题：① i18n 理解偏差（面板标题未走 `self.tr`）；② 快捷键触发重排后索引未完整更新（`updateTextBlkItemIdx` 只更新 tgt 位置，被挤占项索引标签错乱）；③ 重排后 `selection_changed` 未 emit 导致 `sel_info_label` 未刷新。已修复 (前 3 个 commit 包含修正) 后决定整体回滚到另一台设备继续排查。

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`、`ui/configpanel.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`

---

### 文本框重排——右键菜单实现

**需求：** 文本框重排面板撤回后改用右键菜单 + 快捷键。面板方案暴露信号覆盖盲区（`selection_changed` 只在列表勾选时 emit，画布选中不触发），四个按钮恒不可用。

**改动要点：**
- `ReorderContent` 控件 + CollapsibleSection 整体删除，重排入口全部移至 TextPanel 区右键菜单
- 保留 `move_selected()`（整组移动，`result_list` 全排列 diff 复用 `_emit_rearrange_from_perm`）和快捷键注册项（默认 `[]`）
- Canvas 新增 `reorder_textblks = Signal(str, int)`，`SceneTextManager` 连接到 `textEditList.move_selected`
- 右键菜单 "Reorder" 子菜单含 Move Up/Down/Top/Bottom + "Move to Position…"（`QInputDialog.getInt`）
- 子菜单按 `is_textpanel` 启用（画布右键不显示），Move up/down/top/bottom 按选中位置细粒度禁用
- i18n：Canvas 上下文加 7 条翻译，回滚 TextPanel/ReorderContent 残留条目；qm 重编译 858 条

**涉及文件：**
- `ui/textedit_area.py` — 删 ReorderContent/reordered，保留 move_selected/_emit_rearrange_from_perm
- `ui/scenetext_manager.py` — 删 CollapsibleSection，连 canvas.reorder_textblks → move_selected
- `ui/canvas.py` — 右键菜单 Reorder 子菜单 + reorder_textblks 信号
- `ui/mainwindow.py` — 4 个快捷键 handler（_reorder_move 守门）
- `ui/configpanel.py` — 快捷键 4 项（子 agent A 完成）
- `translate/zh_CN.ts` + `.qm` — 新增 Canvas 重排条目，回滚面板残余条目

---

### 启动时页面列表默认关闭

**需求：** 左侧项目图片列表在启动时默认打开（由之前会话的配置持久化导致），改为每次启动默认关闭。

**改动：**
- `ui/mainwindow.py:515-518` — 启动时强制 `pcfg.show_page_list = False` + `setChecked(False)`，不再还原上次配置状态

**涉及文件：** `ui/mainwindow.py`

---

## 2026-07-05

### 获取模型列表对话框添加搜索筛选栏

**问题/需求：** 管理 API 配置文件 → 获取模型列表中，模型数量多时「获取模型列表」弹窗为纯单选下拉列表，无搜索功能，在大模型列表（如 OpenRouter 数百个模型）中定位困难。

**改动：**

1. `utils/profile_manager.py` — 新增 `FilterableListDialog` 类（搜索栏 + 可筛选 `QListWidget` + 双击/按钮确认）；`_on_fetch_models()` 中原 `QInputDialog.getItem` 替换为 `FilterableListDialog`，输入即筛选（大小写不敏感），搜索栏自动获取焦点

**涉及文件：** `utils/profile_manager.py`

---

### API 配置文件管理新增测试连接功能

**问题/需求：** ProfileManagerDialog 已有「Fetch Models」间接做连接测试，但需要独立的一键测试连接功能，且要求 Host 和 API Key 均必填才发送请求。

**改动：**

1. `utils/profile_manager.py` — Host 输入框行新增「Test」按钮；新增 `_on_test_connection()` 方法，先校验 Host 和 Key 非空，再用 `GET {host}/models` 验证连通性，区分 HTTP 错误/连接失败/超时等场景分别给出中文提示；支持读取 profile 的 proxy 设置
2. `translate/zh_CN.ts` — 新增 10 条翻译条目，已编译（844 条）

**涉及文件：** `utils/profile_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### 画布序号徽标固定 100% 不透明度 + 启动闪退修复

**问题/需求：** ① 画布文本框左上角的序号徽标跟随文本框不透明度变化，用户希望始终 100% 显示；② 闪退：`RowIndexLabel` 调用 `setTextMargins`（QLabel 无此方法），启动时报 `AttributeError`。

**改动：**

1. `ui/textitem.py:811` — `_draw_seq_badge` 中 `painter.save()` 后加 `painter.setOpacity(1.0)`，徽标不受文本框 `setOpacity` 影响
2. `ui/textedit_area.py:318` — `self.setTextMargins` → `self.setContentsMargins`

**涉及文件：** `ui/textitem.py`、`ui/textedit_area.py`

---

### 设置面板改为独立 OS 窗口

**问题/需求：** ConfigPanel 内嵌在 `centralStackWidget` 中，复杂 widget 树与 canvas 在同一渲染表面，已打开项目时显示设置面板仍有明显掉帧。

**改动：**

1. `ui/configpanel.py` — 窗口标志改为 `Qt.WindowType.Tool`（标准标题栏 + 无任务栏入口）；`setWindowTitle("Settings")` + `setMinimumSize(700, 450)` 允许用户拖拽调整大小
2. `ui/overlay_modal.py` — 重写：panel 不再作为 `cover_widget` 子 widget，改为独立 OS 窗口；移除缓存截图动画机制（`_cache`/`_cache_effect`/`_swap_to_real_panel`/`_cleanup_cache`），改用 `setWindowOpacity()`（DWM 合成，无每帧 render-to-texture）；`setFixedSize`→`resize`，允许自由调整；`_center_window()` 用 `mapToGlobal` 映射到屏幕坐标
3. `ui/mainwindow.py` — ConfigPanel 创建 parent 改为 `self`（MainWindow），移除冗余 `setParent`
4. `translate/zh_CN.ts` + `zh_CN.qm` — 新增 `"Settings" → "设置"` 翻译，重新编译（845 条）

**涉及文件：** `ui/configpanel.py`、`ui/overlay_modal.py`、`ui/mainwindow.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`

---

### RowIndexLabel 双击编辑功能移除

**问题/需求：** 画布右侧文本编辑列表中，左侧的顺序编号（`RowIndexLabel`）双击可切换到 `QLineEdit` 编辑模式，用户修改后导致序号显示错乱。

**改动：**

1. `ui/textedit_area.py` — `RowIndexLabel` 从 `QStackedWidget(QLabel + QLineEdit)` 简化为 `QLabel` 子类，保留 `setSizePolicy(Maximum, Maximum)` 维持原尺寸表现；移除 `mouseDoubleClickEvent`/`startEdit`/`keyPressEvent`/`try_update_idx` 等整条编辑信号链；清理不再使用的 import
2. `ui/scenetext_manager.py` — 移除 `pair_widget.idx_edited` 死连接

**涉及文件：** `ui/textedit_area.py`、`ui/scenetext_manager.py`

---

## 2026-07-04

### 縦中横（竖内横排）功能验证修复：多 run 标志丢失、横排居中错位、配置面板闪退

**问题/需求：** 已实现的「竖排文本框内连续 `[A-Za-z0-9]` run 在长度 ≤ 阈值时正立横排」功能经验证存在三类问题：
1. 多 run 块（如 `第1話2Aい`）整列不响应阈值调整、仍逐字 90° 旋转，导致短串与下方字符重叠
2. 横排 run 向右偏移：run 越长越往右探、可见地窜入相邻列；表象为「只在 2 字 run 时恰好居中」，实为横排 line 定位基准错误
3. 设置面板拖动「Vertical Latin/Digits Length」滑块时必触发 `TypeError` 闪退

**改动：**

1. `ui/configpanel.py:1787` — `PaintQSlider(Qt.Orientation.Horizontal)` 改为 `PaintQSlider()`。`PaintQSlider.__init__` 首参为 `draw_content`（额外文字标签），误把 Orientation 枚举喂入；鼠标悬停时 `painter.drawText(0, dy, self.draw_content)` 收到枚举触发 `TypeError`。改为空参与其他 PaintQSlider 用法一致

2. `ui/scene_textlayout.py` `layoutBlock` —— 删除 `pending_tatechuyoko` 单变量「延迟还原」机制（该机制会让非末尾 run 标志在 flush 时被普通 `{line_width}` 覆盖丢失，且不换列短文本会在循环末尾 `pending=None` 清空前根本不触发还原）。改为 run 分流成功时**立即**写 `char_records[char_idx] = {"line_width": run_w, "tatechuyoko": True}`；两处 flush 覆盖点（普通列结束 `for cidx in line_char_ids`、末列结束 `end_char_id`）加守卫 `if not char_records.get(cidx, {}).get("tatechuyoko")` 跳过已带标志字符。效果：列内任意多 run 全部保住横排标志

3. `ui/scene_textlayout.py` `updateDrawOffsets` 横排居中 —— 旧算法 `xoff = -act_rect[0] + (col_w - act_rect[2])/2` 只把 run **第一个字符**居中到列，后续字符从起点往右平延（1 字/2 字恰好看着居中，3 字起明显右探）。改为 `xoff = (cfmt.tbr.width() - line_width)/2 - act_rect[0]`，让整段 run 中点对齐列中点；`line_width`（run 横宽）仍保留给 `line_draw` 做选中裁剪矩形用

**验证：** 用户实机验收通过 —— `第1話2Aい` 三处 run 全部正立横排；`あabい`/`あabcい` 等不同长度 run 均在列内居中、不再右探；横排向邻列偏移消除。另：AIGDT 字体竖排模式把小写字母映射为三角形装饰字形，属字体自身问题（换常规字体解决），非代码问题。

**遗留：** `layoutBlock` `if num_lspaces == 0` 分支把 run 横宽-单字宽塞进 `self.draw_shifted`，进而影响 `layout_left`（块边界左偏），大字号下可能触发 `size_enlarged` 重排。本次未动 —— 该项为块级缩放，与本次修复的 per-run 定位不同根，需大字号实测确认尚有异常才跟进

### i18n 全面检查：隐式拼接修复、缺失/过期条目清理

**问题/需求：** 项目多处 `self.tr()` 存在 Python 隐式字符串拼接（`"part1 " "part2"`），正则扫描器无法识别导致误报 orphan；新功能（JXL 格式、纵中横）的 4 条翻译缺失；移除的底部栏语言选择器、旧 ConfigPanel 备注残留过期 orphan。

**改动：**

1. **隐式拼接修复（6 文件）** — `ui/mainwindow.py`、`ui/mainwindow_mixin.py`、`ui/fontstyle_manager.py`、`ui/model_check_dialog.py`、`ui/update_checker.py`、`ui/module_manager.py`、`utils/profile_manager.py` 中所有跨行 `"a" "b"` 合并为单字面量
2. **i18n_check.py** — 硬编码中文白名单添加 `"无字图配对工具.py"`（文件路径误报）
3. **zh_CN.ts** — 添加 4 条缺失条目（JXL 格式描述 2 条、纵中横备注 2 条）+ 翻译；清理 16 条真 orphan；恢复 11 条间接调用条目（PointAlignDialog/QuickSymbolDialog）；补充 6 条未完成翻译
4. **编译验证** — `.qm` 重新编译，834 条翻译；`i18n_check.py` 硬编码中文 0、缺失 0、仅余 47 条已文档化的间接调用 orphan（退出码 4 可接受）

**涉及文件：** `scripts/i18n_check.py`、`ui/mainwindow.py`、`ui/mainwindow_mixin.py`、`ui/fontstyle_manager.py`、`ui/model_check_dialog.py`、`ui/update_checker.py`、`ui/module_manager.py`、`utils/profile_manager.py`、`translate/zh_CN.ts`、`translate/zh_CN.qm`
