# 文本编辑区（右侧格式面板）UI 重构

> 前身是「文本编辑区UI现状_待评估.md」交接稿：上游 v1.5.12 移植把
> Ruby/着重号/縦中横/连字/旧式数字等注解功能全量落地后，右侧格式面板
> 出现「功能多没地放、控件风格杂」的排版问题。本文记录评估结论与
> 最终落地的重构方案。评估对象是 `ui/text_panel.py::FontFormatPanel`
> （画布右侧、『原文/译文』切换上方的格式面板），不涉及设置窗口
> （`ui/configpanel.py`）。
> 2026-08-23 更新：侧栏收纳批次后，Zone C 内容全部外迁画布浮层
> （详见 `docs/技术实现/侧栏图标_画布浮层面板实现.md`），本文结构述已同步修订。

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
        ├ [前景色 | 字体 | 字重]（3:2 定宽 stretch，弹出列表撑宽完整显示）
        ├ [对齐×3 ‖ B/I/U/着重号 ‖ 竖排/TCY/Roman]   （‖ = 1px 竖分隔线）
        └ [字号 | 行距 | 字距] 量测行（排版数值一组）；[描边宽+描边色] 单行
Zone C  拓展样式——内容全部外迁画布浮层（窄栏图标入口 + RailDockPanel
        ├ TextStyleGroup（不透明度/阴影/渐变，原 TextStyleDialog 内容）
        ├ TextTransformPanel（PanelArea 本体进浮层，去折叠标题条）
        ├ AnnotationFormatGroup（Ruby/连字/旧式数字等注解）
        └ EmphasisFormatGroup（着重号）
文本输入区（TextPanel 下半，本节不改）
```

统一规则：**区块标题一律用 capsule 条（`ExpandLabel[capsule]` /
`QPushButton[capsule]`），内容平铺无边框**。移除了行级 GroupFrame、
注解子区 QGroupBox、外层 "Font Format" CollapsibleSection
（`ui/scenetext_manager.py::TextPanel` 直接内嵌 formatpanel，
保留 `formatOuterFrame` 大框与下半文本区对称）。

## 3. 拓展样式全部走画布浮层（2026-08-23 起）

侧栏收纳批次把 Zone C 四组内容（文字样式/变换/注解/着重号）全部移出
格式面板，改为窄栏图标（`RailLauncherButton`，`ui/text_panel.py` 的
`install_*_launcher`）懒创建打开画布浮层（`RailDockPanel`，
`pcfg.*_dock_open` 记忆开合）。布局机制详见
`docs/技术实现/侧栏图标_画布浮层面板实现.md`，这里只记与面板重构相关的
作用域规则：

- **选中级（注解/着重号）**：无选中文字块（全局模式）时窄栏图标禁用；
  浮层若已打开保持打开、仅内容置灰（`_sync_annotation_controls`），
  **绝不因失焦自动关闭**——只响应图标 toggle、标题栏 ×、Esc。
  当前块有激活注解时图标带角标提示（收起态也能看出"这里有东西可改"）。
- **非选中级（变换/文字样式）**：全局与逐块两种模式都可操作，不置灰
  （`_sync_annotation_controls` 不碰这两组）。
- Zone B 图标行保留着重号/TCY/Roman 快捷开关：单击即用，浮层折叠不
  影响其工作（与上游图标排版一致）。
- 中间态说明：重构首批曾用「注解折叠胶囊」（`ViewWidget` +
  `title_capsule=True`）承载注解，`expand_annotation_panel` 字段持久化——
  收纳批次用 `annotation_dock_open` 等四个 `*_dock_open` 字段取代，
  胶囊形态已移除。

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

1. 注解区折叠 + 选区显隐（减法导向：低频功能不占常驻空间）——收纳批次
   后进一步外迁画布浮层，面板本体不再承载；浮层内沿用选中级置灰；
2. 不透明度/行距类型/阴影/渐变收进文字样式浮层（原 Text Style 对话框，
   2026-08-23 收纳批次后为 `ui/text_style_dock.py`）而非内联；
3. 字重控件保留本地实现（不取上游 `FontWeight` 枚举与 HTML 往返）。

另注：`ui/text_engine/formatting/panel.py` / `ui/text_engine/editing/manager.py`
是节点 2a 落地的上游休眠代码（无实例化路径），线上面板只有
`ui/text_panel.py::FontFormatPanel` 一份。

## 6. 涉及文件

- `ui/text_panel.py`：分区重排、四组 Zone C 内容外迁画布浮层
  （`install_annotation/transform/emphasis/textstyle_launcher` +
  `RailDockPanel` 懒创建）
- `ui/text_style_dock.py`：文字样式浮层（不透明度/阴影/渐变）
- `ui/scenetext_manager.py`：移除 "Font Format" CollapsibleSection 包装
- `ui/custom_widget/view_panel.py`：ExpandLabel 串写修复
- `utils/config.py`：`annotation_dock_open`/`emphasis_dock_open`/
  `transform_dock_open`/`textstyle_dock_open` 字段
- `config/stylesheet.css`：`fmtGroupSeparator` 细线规则、浮层标题条样式
- `translate/zh_CN.ts` / `.qm`：新增 `Annotations`（注解）等串，
  删除 TextPanel 的 "Font Format" 死串

## 7. 验证入口

- 全量：`./ballontrans_pylibs_win/python.exe scripts/verify.py`
- 单测：`tests/test_annotation_controls.py`（18 例，含注解组 API、
  面板路由、TCY/Ruby 互斥回滚）、`tests/test_configpanel_node3.py`、
  `tests/test_vertical_engine.py`、`tests/test_startup_imports.py`
- 离屏构造面板：设置 `shared.register_view_widget = lambda *a,**k: None`
  后再 `QApplication` + `FontFormatPanel(app)`；右栏固定 360px、
  面板内宽 348px 是排版硬约束。