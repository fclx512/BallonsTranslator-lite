# PSD 导出 — 功能存档（已移除）

> **状态（2026-09-05）：功能实现已整体删除，仅保留本文档描述技术状态。**
> 原因：JSX 路线实机反复出现各类排版/样式问题，排查无法收敛；等待更强的 AI 修复能力出现后再重启。
> 本文是重启时的唯一凭据：完整实现可从 git 历史 `fd90b31` 一并取回（含测试与当时的技术文档，原 docs 下 PSD导出_JSX路线 一篇）。

## 一、曾经的技术方案（结论仍有效）

核心结论：**可编辑文本层只能在 Photoshop 进程内创建**（ExtendScript DOM API）。
采用「Python 生成单个自包含 `.jsx`（内嵌 JSON payload）→ PS 里 File → Scripts → Browse 跑一次 → 批量产出全部页面的可编辑文本层 PSD」路线。

```text
曾经涉及的文件（均已删除，git show fd90b31:<路径> 可取回）：
  utils/psd_exporter.py         公共层：ExportOptions / AbstractPsdExporter / create_exporter
  utils/psd_jsx_exporter.py     JSX 路线核心：生成自包含 .jsx（payload 预留字符级样式扩展点）
  utils/psd_binary_exporter.py  二进制直写路线（已封存）
  utils/psd_binary_writer.py    PSD 二进制写码器
  utils/psd_descriptor.py       ActionDescriptor 序列化
  utils/psd_engine_data.py      文本引擎 EngineData 编码
  utils/psd_packbits.py         PackBits RLE 压缩
  utils/font_mapping.py         Qt 家族名 → PS PostScript 名映射（resolve_font_name / exact_ps_name）
  ui/psd_export_dialog.py       导出对话框（页码范围/输出目录/字体兼容性检查）
  ui/io_thread.py::PsdExportThread  后台导出线程（早已是死代码）
  tests/test_psd_jsx_export.py  JSX 路线 14 用例
  tests/test_psd_binary.py      二进制路线全链路测试
```

## 二、为什么反复失败（重启前必读）

1. **二进制直写路线是死胡同**：文本层必须手写 `TySh` + `EngineData`（Adobe 专有文本引擎序列化），
   任一细节错误 → PS 打不开 / 文本不可编辑 / 样式错乱，排查成本极高。已封存。
2. **COM 自动化路线不稳定**，早已删除（"COM route permanently blocked"）。
3. **JSX 批量路线**是三条中最可行的（参考 [ZsIsMe/PS-Script](https://github.com/ZsIsMe/PS-Script) 的 LabelPlus 分支），
   但实机验收时排版与样式仍持续出现难以定位的问题，最终放弃维护。

重启时的忠告：**不要回到二进制路线**；从 `fd90b31` 恢复 JSX 路线，在它的问题清单上逐项修复。

## 三、残留的关联资产（保留未删）

- `utils/font_scan.py` 的 PS 名索引（`build_font_data` 返回的 `ps_index`）与 `utils/shared.py::FONT_PS_NAMES`：
  PSD 导出专属消费者（`font_mapping.py`）已删，但「一键精简」的别名补录仍消费该索引，故保留。
- `config/stylesheet.css` / `translate/zh_CN.ts`：PSD 相关条目已同步清理
  （ts 中 DrawingPanel 的「在 Photoshop 中编辑」、InpaintConfigPanel 的 Photoshop 路径等条目
  属于**图像修复外部编辑功能，与 PSD 导出无关**，未动）。
- 审计登记：`scripts/audit_registry.json` 的 `deprecated` 区登记了全部已删文件，
  残留引用白名单指向本文。

## 四、技术要点备忘（来自原实现，重启时直接复用）

- 输出 `.jsx` 必须 **UTF-8 with BOM**（`utf-8-sig`）；数据内嵌 `var DATA = {...}`，不在运行期读外部文件（规避 ExtendScript 编码坑）。
- 竖排文本层须在设置 `contents` **之前**设 `ti.direction = Direction.VERTICAL`。
- 字号按图片 DPI 换算（`px × 72 / DPI`）+ 脚本内 `doc.resizeImage(undefined, undefined, dpi)` 对齐文档分辨率。
- 中心对齐兜底（读 `art.bounds` 平移图层）是抵消 PS 排版差异最有效的一招。
- 描边/投影用 ActionManager 图层样式（`frameFX` / `dropShadow`）；DOM 做不到的效果都走这条路。
- 已知未映射项：`letter_spacing`（PS tracking 单位换算近似）；字符级样式（縦中横等，需 `textKey.textStyleRange`）。
