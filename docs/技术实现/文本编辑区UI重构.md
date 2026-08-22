# 文本编辑区（右侧格式面板）UI 重构

> 前身是「文本编辑区UI现状_待评估.md」交接稿：上游 v1.5.12 移植把
> Ruby/着重号/縦中横/连字/旧式数字等注解功能全量落地后，右侧格式面板
> 出现「功能多没地放、控件风格杂」的排版问题。本文记录评估结论与
> 最终落地的重构方案。评估对象是 `ui/text_panel.py::FontFormatPanel`
> （画布右侧、『原文/译文』切换上方的格式面板），不涉及设置窗口
> （`ui/configpanel.py`）。

## 1. 评估结论（重构依据）

- **两类使用频率不同的功能被赋予同等视觉权重**：字体/字号/颜色/对齐等
  高频整块粒度功能应常驻；Ruby/连字等注解是低频、选区粒度，只在日语
  排版细修阶段使用。重构前注解区（两个 `QGroupBox`）常驻约 230px 高，
  且无选中块时整组灰置，是面板最大的空间消耗者兼「死区」。
- **水平方向无余量**：右栏是 `ui/mainwindow.py` 中
  `rightComicTransStackPanel.setFixedWidth(360)` 的固定宽度列
  （内宽约 348px），只能向「纵向折叠」要空间。
- **容器语言混用**：重构前面板内叠加了 4 种容器视觉——行级 `GroupFrame`
  胶囊、注解子区 `QGroupBox`（标题骑边框）、外层 "Font Format"
  `ui/collapsible_section.py::CollapsibleSection` 头（26px 箭头标题）、
  `ExpandLabel[capsule]`/`QPushButton[capsule]` 灰底标题条，
  边框套边框、观感杂乱。

## 2. 重构后的结构（自上而下）

```
Zone A  全局字体样式
        └ TextStylePresetPanel 折叠胶囊（标题承载 Global Font Format /
          TextBlock #N，即 ui/text_style_presets.py）
Zone B  基本选项（平铺三行，无边框）
        ├ [前景色 | 字体 | 字重] | 字号
        ├ [对齐×3 ‖ B/I/U/着重号 ‖ 竖排/TCY/Roman]  （‖ = 1px 竖分隔线）
        └ [行距] [字距] [描边宽+描边色]
Zone C  拓展样式与变换
        ├ Text Style 按钮（capsule 样式，弹 ui/shadow_gradient_dialog.py
        │  的 TextStyleDialog：不透明度/行距类型/阴影/渐变）
        ├ TextTransformPanel 折叠胶囊
        └ 注解折叠胶囊（AnnotationFormatGroup，默认收起）
文本输入区（TextPanel 下半，本节不改）
```

统一规则：**区块标题一律用 capsule 条（`ExpandLabel[capsule]` /
`QPushButton[capsule]`），内容平铺无边框**。移除了行级 GroupFrame、
注解子区 QGroupBox、外层 "Font Format" CollapsibleSection
（`ui/scenetext_manager.py::TextPanel` 直接内嵌 formatpanel，
保留 `formatOuterFrame` 大框与下半文本区对称）。

## 3. 注解折叠胶囊行为（`ui/text_panel.py`）

- **默认收起**：展开状态持久化在 `utils/config.py::pcfg` 的
  `expand_annotation_panel`（默认 `False`）；复用
  `ui/custom_widget/view_panel.py::ViewWidget`（`title_capsule=True`），
  不注册进 View 菜单。
- **状态显隐**：`_sync_annotation_controls()` 在无选中文字块（全局模式）
  时对整个胶囊 `setVisible(False)`，消除灰色死区；选中块后显示并回显。
- **标题标记**：当前块任一注解激活（着重号≠none / TCY / 任一连字轴≠
  default / 旧式数字≠default / Ruby 存在）时标题显示 `Annotations •`，
  收起态也能提示「这里有东西可改」（`_update_annotation_title()`）。
- **着重号/TCY/Roman 图标留在 Zone B 图标行**：单击即用的快捷开关，
  与上游图标排版一致；胶囊折叠不影响其工作。
- **内部平铺**：`AnnotationFormatGroup` 去掉了两个 QGroupBox，改为
  小加粗标签小节（Ruby / Furigana、Ligature）+ 细横线分隔；
  Ruby 的 Type/Position 仍走 `FlowLayout`（窄面板自动换行），
  连字/旧式数字为 **2×2 紧凑网格**——主题字号下单个下拉 sizeHint
  约 126px（"Default" 文本实测 98px），一行四个约 534px 超出 348
  内宽会挤压到不可读，2×2 每列约 168px 充裕。

## 4. 附带修复

- **ExpandLabel 串写 bug**：`ui/custom_widget/view_panel.py` 的
  `ExpandLabel.mousePressEvent` 原硬编码 `pcfg.expand_tstyle_panel =
  self.expanded`，任何胶囊标题点击（含文本变形、注解）都会改写文字样式
  预设面板的展开状态。已删除该行——持久化由宿主
  `ViewWidget.set_expend_area` 按 `config_expand_name` 落地。
- **图标行分组分隔**：Zone B 图标行在对齐/格式/竖排三组间加 1px 竖线
  （objectName `fmtGroupSeparator`，样式在 `config/stylesheet.css`，
  与注解小节横线共用规则），解决 10 个图标挤一行无分隔的观感问题。

## 5. 与上游的刻意分歧

上游 `ballontranslator/ui/text_engine/formatting/panel.py` 把注解全部
内联平铺（无折叠），高级格式也在面板内联。本 fork 的分歧：

1. 注解区折叠 + 选区显隐（减法导向：低频功能不占常驻空间）；
2. 不透明度/行距类型/阴影/渐变收进 Text Style 对话框而非内联；
3. 字重控件保留本地实现（不取上游 `FontWeight` 枚举与 HTML 往返）。

另注：`ui/text_engine/formatting/panel.py` / `ui/text_engine/editing/manager.py`
是节点 2a 落地的上游休眠代码（无实例化路径），线上面板只有
`ui/text_panel.py::FontFormatPanel` 一份。

## 6. 涉及文件

- `ui/text_panel.py`：分区重排、注解折叠胶囊（ViewWidget + 显隐 + 标记）、
  `AnnotationFormatGroup` 平铺化
- `ui/scenetext_manager.py`：移除 "Font Format" CollapsibleSection 包装
- `ui/custom_widget/view_panel.py`：ExpandLabel 串写修复
- `utils/config.py`：`expand_annotation_panel` 字段
- `config/stylesheet.css`：`fmtGroupSeparator` 细线规则
- `translate/zh_CN.ts` / `.qm`：新增 `Annotations`（注解），
  删除 TextPanel 的 "Font Format" 死串

## 7. 验证入口

- 全量：`./ballontrans_pylibs_win/python.exe scripts/verify.py`
- 单测：`tests/test_annotation_controls.py`（18 例，含注解组 API、
  面板路由、TCY/Ruby 互斥回滚）、`tests/test_configpanel_node3.py`、
  `tests/test_vertical_engine.py`、`tests/test_startup_imports.py`
- 离屏构造面板：设置 `shared.register_view_widget = lambda *a,**k: None`
  后再 `QApplication` + `FontFormatPanel(app)`；右栏固定 360px、
  面板内宽 348px 是排版硬约束。
