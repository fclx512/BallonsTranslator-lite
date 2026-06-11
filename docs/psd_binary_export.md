# Binary PSD Export — Current Status & Known Issues

## 概述

新增一个直接二进制 PSD 导出器 `PsBinaryExporter`（`utils/psd_binary_exporter.py`），位于原有的 ExtendScript 路径之外。调用方式：

```
工具 → Export as PSD... → 方法选 "Binary PSD (direct)"
```

## 实现架构

| 层 | 文件 | 移植来源 |
|----|------|----------|
| 二进制 I/O | `utils/psd_binary_writer.py` | koharu `writer.rs` |
| PackBits RLE | `utils/psd_packbits.py` | koharu `packbits.rs` |
| 描述符 | `utils/psd_descriptor.py` | koharu `descriptor.rs` |
| 文本引擎数据 | `utils/psd_engine_data.py` | koharu `engine_data.rs` |
| 主组装器 | `utils/psd_binary_exporter.py` | koharu `export.rs` |

PSD 文件结构（自底向上）：

1. **Original Image** — 有修补图时隐藏
2. **Inpainted** — 显示
3. **Segmentation Mask** — 隐藏
4. **文字层** — 每个 TextBlock 一个图层，TySh + EngineData 元数据嵌入

## 已确认的问题

### 1. 文字被栅格化（非可编辑文字）

**现象**：导入 PS 后文字图层显示为栅格化像素，而非可编辑的文字对象。TySh/EngineData 段已嵌入 PSD 二进制数据中（可用 hex 搜索 `TySh` 和 `/EngineDict` 确认），但 PS 未能识别为可编辑文字图层。

**可能原因（待排查）**：

- **EngineData 结构不完整** — koharu 的 EngineData 序列化（830 行 Rust）在移植中可能遗漏了某些 PS 期望的字段或嵌套层级。PS 对 EngineData 的解析非常严格，缺少任何必要字段都会导致整段被忽略，回退到栅格化像素。
- **Descriptor 格式偏差** — `write_ascii_or_class_id` 对 4 字节值的处理与 PS 预期不完全一致。PS 对 OSType key 的解析有特定规则，可能某些 key 应写为裸 class ID (`i32(0)+4bytes`) 但被写成了长度前缀格式，或反之。
- **版本号/签名不匹配** — TySh 段内的版本号 (`i16(1)`, `i16(50)`) 或 Descriptor 版本号 (`u32(16)`) 与目标 PS 版本不兼容。
- **字符串编码** — EngineData 中的 UTF-16BE 字符串使用 `( \376\377 ... )` 格式，转义处理（`(`, `)`, `\` 字符）可能在边界情况有误。
- **ResourceDict/DocumentResources 重复结构** — 两个字典内容需完全一致，PS 某些版本依赖此结构。

**建议排查方向**：
- 用 PS 的 Action Manager 导出一个真实的可编辑文字图层，比较二进制 TySh 段与本工具输出的差异
- 用 [psd-tools](https://pypi.org/project/psd-tools/) 或类似工具解析输出的 .psd，检查 TySh 段是否能被正确解析
- 验证 `write_ascii_or_class_id` 中例外列表 `{"warp", "time", "hold", "list"}` 是否完整

### 2. 背景色块（文本框空白区域被填充）

**现象**：文字图层在 PS 中显示为带背景色的矩形块，而非仅文字区域的像素。

**原因**：`_crop_from_result()` 从 `result/` 目录的已渲染图像中裁剪文字块区域时，裁到了整个 bounding box 的像素（含文字周围的空白/背景）。BallonsTranslator 的 `result/` 图像是整页合图，文字块周围的背景像素一并被裁剪。

**当前行为**：
```
文字块 xyxy=[5, 10, 55, 30]
→ 从 result/ 裁剪 (5, 10) 到 (55, 30) 的矩形区域
→ 该区域包含文字 + 周围背景色（非透明）
```

**改进方向**：
- 利用 `blk.lines` 多边形数据（每个字行的四边形顶点），裁剪时只取文字多边形内部像素
- 或从 `blk.region_mask`（文字区域的二值遮罩）提取 alpha 通道，将非文字区域置为透明
- 或让渲染管线输出单独的透明背景文字图层（涉及渲染模块改动）

### 3. 依赖 result/ 目录的存在

`_crop_from_result()` 依赖 `proj.get_result_path(page_name)` 获取已渲染图像。若用户未渲染结果图（仅做了翻译），文字层像素将退化为全透明占位，PS 中不可见。

## 验证方法

### 单元测试

```
python -m pytest tests/test_psd_binary.py -v
```

46 个测试覆盖：writer 原语、PackBits 编码、Descriptor 序列化、EngineData 生成、完整 PSD 导出。

### 人工验证

1. 导出一页含文字的 PSD
2. 在 PS 中打开，检查：
   - 图层结构（原图 / 修补 / 遮罩 / 文字）
   - 文字是否可编辑（文字工具点击文字层）
   - 文字位置是否与原图对齐
3. 对比本导出结果与 Koharu 工具的输出（十六进制比较 TySh 段差异）

## 导出文件列表

| 文件 | 行数 | 备注 |
|------|------|------|
| `utils/psd_binary_writer.py` | ~110 | 已验证 |
| `utils/psd_packbits.py` | ~100 | 已验证 |
| `utils/psd_descriptor.py` | ~150 | 已验证 |
| `utils/psd_engine_data.py` | ~300 | EngineDict 结构可能不完整 |
| `utils/psd_binary_exporter.py` | ~450 | 含裁剪逻辑和 TySh 组装 |
| `tests/test_psd_binary.py` | ~250 | 46 个测试 |

## 修改过的文件

| 文件 | 改动 |
|------|------|
| `utils/psd_exporter.py` | 工厂支持 `export_method` |
| `ui/psd_export_dialog.py` | 方法选择器 ComboBox |
| `ui/mainwindow.py` | 透传导出方法，完成消息区分 |
