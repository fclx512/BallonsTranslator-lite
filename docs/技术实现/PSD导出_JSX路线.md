# PSD 导出 — JSX 批量路线

> 本文档记录 PSD 导出的实现机制、保真度策略与已知限制。
> 结论速览：**可编辑文本层只能在 Photoshop 进程内创建**（ExtendScript DOM API），
> 因此本项目采用"Python 生成单个自包含 .jsx → 在 PS 里 File → Scripts → Browse 跑一次 → 全部页面产出可编辑文本层的 PSD"。

---

## 一、背景：为什么弃用二进制路线

此前两条导出路线均已封存，问题根源：

| 路线 | 问题 |
|------|------|
| 二进制直写 PSD（`utils/psd_binary_*.py`，Koharu `export.rs` 移植） | 文本层必须手写 `TySh` + `EngineData`（Adobe 专有文本引擎序列化）。格式任一细节错误 → PS 打不开 / 文本不可编辑 / 样式错乱，排查成本极高 |
| COM 自动化驱动 PS（`psd_com_exporter.py`） | 不稳定，已删除（commit "COM route permanently blocked"） |
| 旧 JSX 导出（每页一个 .jsx） | 每页都要手动 Browse 一次，流程冗长 |

参考实现：**[ZsIsMe/PS-Script](https://github.com/ZsIsMe/PS-Script)**（LabelPlus PS 脚本分支）——
它证明"在 PS 内跑脚本建文本层"是工程上唯一被长期验证的路径。本项目只取其**文本→可编辑文本层**
机制，不需要标号、涂白、模板 PSD 等其余功能。

## 二、机制

### 2.1 数据链路

```
BT 项目 ──> Python（utils/psd_jsx_exporter.py）──> 单个 .jsx（内嵌 Meo 风格 JSON）
                                                  │  UTF-8 with BOM
                                                  ▼
                            Photoshop: File → Scripts → Browse → 运行一次
                                                  │
                   每页：app.open(原图) → resizeImage(DPI) → 逐块建文本层 → saveAs(.psd)
```

要点：

- **数据内嵌进 .jsx**（`var DATA = {...}`），不在运行期读外部文件——规避 ExtendScript
  读 UTF-8 外部文件的 BOM/编码坑（PS-Script 1.7.4 就为此修过 BOM 兼容）。
- **输出文件 UTF-8 with BOM**（`utf-8-sig`）——非 ASCII ExtendScript 源文件的通行约定
  （PS-Script 成品脚本同样带 BOM）。
- **不复制图片**：脚本引用原图绝对路径，要求导出后在同一台机器上运行（对话框文案已注明）。

### 2.2 文本层创建（移植自 PS-Script `newTextLayer()`）

核心是 Photoshop DOM API 调用链（`psd_jsx_exporter.py` 内嵌模板的 `createTextLayer`）：

```js
var art = doc.artLayers.add();
art.name = blk.name;
art.kind = LayerKind.TEXT;                 // ← 原生文本图层
var ti = art.textItem;
ti.kind = TextType.POINT_TEXT;
if (blk.vertical) ti.direction = Direction.VERTICAL;   // 竖排须在 contents 之前
ti.contents = blk.text;
ti.font = blk.font;                        // PostScript 字体名
ti.size = blk.size_pt;                     // pt
ti.color = makeSolidColor(r, g, b);
ti.fauxBold / fauxItalic / underline;
ti.justification = LEFT|CENTER|RIGHT;
ti.useAutoLeading = true; ti.autoLeadingAmount = blk.line_spacing;  // 百分比
ti.position = [x, y];                      // 块盒附近，之后中心对齐修正
art.rotate(-blk.rotation, MIDDLECENTER);   // 旋转：BT 正=逆时针，PS 正=顺时针 → 取负
```

DOM 做不到的效果用 ActionManager 补：

| 效果 | 实现 | 位置（模板内） |
|------|------|--------------|
| 描边 | 图层样式 `frameFX`（ActionDescriptor `setd`） | `applyLayerEffects()` |
| 投影 | 图层样式 `dropShadow`（blur/distance/opacity/angle/color） | `applyLayerEffects()` |
| 居中兜底 | 读 `art.bounds` → 平移图层使中心 = 块盒中心（移植 PS-Script `centerAlign`，importer.ts:175） | `createTextLayer()` 尾部 |

### 2.3 payload 字段（每块）

| 字段 | 来源/换算 |
|------|----------|
| `text` | `blk.translation.strip()`，保留 `\n` 换行（点文本保持行结构） |
| `font` | `resolve_font_name(ff.font_family)` → PS PostScript 名 |
| `size_pt` | `font_size(px) × 72 / 图片DPI`（PIL 读 DPI，缺省 96） |
| `size_px` | `ff.font_size`（初始定位用） |
| `color` / `stroke_color` | `ff.foreground_color()` / `ff.stroke_color()` |
| `bold/italic/underline/vertical/alignment/opacity` | `ff.*` 直取 |
| `line_spacing` | proportional → `×100`（120%）；Distance 型 → `值/font_size×100` |
| `stroke_size` | `font_size × ff.stroke_width`（与 `ui/text_engine/effects/renderer.py::_stroke_paint_context` 一致；旧代码 `×0.5` 是错的） |
| `shadow_blur/distance/opacity/angle` | 沿用 `ui/text_engine/effects/renderer.py` 公式：blur/offset = 参数×font_size；strength→百分比；angle 为 PS 光源角（offset 反向 +180°） |
| `rotation` | `blk.angle` 直取（含竖排的 -90 已内置，无需修正），JSX 内取负 |
| `box` | `blk.xyxy`（中心对齐的锚） |
| `center` | 对话框「居中」勾选（默认开） |

## 三、保真度策略

PS 用自己的排版引擎重排文字，字体度量与本地渲染必然有差异。三层缓解：

1. **DPI 对齐**：`size_pt` 按图片 DPI 换算 + 脚本内 `doc.resizeImage(undefined, undefined, page.dpi)`
   把文档分辨率对齐，保证 pt→px 与源一致。
2. **显式换行**：点文本 + 保留 `\n`，行结构不依赖 PS 自动折行。
3. **中心对齐（默认开）**：按图层 bounds 平移，使文本中心落在原文字块盒中心——
   抵消基线/字宽度量差异带来的偏移。这是最有效的一招（PS-Script 同款）。

## 四、已知限制（如实标注）

- **排版差异不能 100% 消除**：不同字体/缺字回退/字距渲染仍可能有偏差，译文最终需在 PS 里微调。
- **`letter_spacing` 未映射**：PS tracking 单位换算近似，暂不处理。
- **字符级样式未做（Phase 2）**：縦中横 / 直立罗马字 / 比例间距需操作
  `textKey.textStyleRange`（PS-Script `applyCharStyleOverrides`），payload schema 已按可扩展结构预留。
- **同机使用**：脚本引用原图绝对路径，跨机器需连同图片一起搬运（或后续改为可选打包）。
- 与上游 `BallonsTranslator/scripts/export to photoshop/Import from BallonTranslator JSON.jsx` 的关系：上游脚本是
  "在已打开的 PS 文档里导入项目 JSON" 的老方案，无批量、无旋转/描边/阴影、字号直当 pt 用（有偏差）。
  本路线是其改进版：批量单脚本 + 完整样式 + DPI 换算 + 中心对齐。

## 五、验证

- `tests/test_psd_jsx_export.py`：payload 字段换算、脚本自包含（`#target photoshop`、无 `$`
  残留、BOM）、批量覆盖全部页、工厂默认。
- 真机验收：导出项目 → 在 Photoshop 跑生成的 .jsx → 目视确认每页文本层位置/大小/样式，
  重点看竖排与带旋转的块。
