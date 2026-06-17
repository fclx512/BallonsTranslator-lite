# Binary PSD Export — 现状 & 已知问题

## 概述

直接二进制 PSD 导出器 `PsBinaryExporter`（`utils/psd_binary_exporter.py`），位于原有的 ExtendScript 路径之外。调用方式：

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

## 修复历史

### 第 1 轮修复（EngineData 结构）

- `_paragraph_run_lengths()` — RunLengthArray 含 `\r` 终止符
- EngineData PICON 缩进层级修正
- 数组 `[ ]` 内元素与 key 同级
- PICON 浮点格式 `0.8` → `.8`
- AntiAlias/ShapeType 值匹配 PS 预期
- 删除 EngineData 中的 BoxBounds
- EngineData 开头加 `\n\n` 前缀

### 第 2 轮修复（TySh 描述符结构）

- TySh 描述符从 **8 项改为 9 项**：新增 `TxMg`（`DescriptorValue.enum("TxMg", "TxNM")`）— 控制文字变形网格
- `AntA` 枚举值从 `antiAliasSharp` 改为 `AnCr`（PS 的实际枚举值）
- `bounds` 单位从 `#Pxl`（像素）改为 **`#Pnt`（磅值）**— 文字层坐标系要求使用点
- 新增 `DescriptorValue.unit_points()` 和通用 `unit_float()` 工厂方法

### 第 3 轮修复（致命缺陷）⭐

**UnitFloat 字段顺序错误** — `utils/psd_descriptor.py` 中 `UntF` 值的序列化顺序与 PS 规范相反：

```
旧（错误）： UntF → f64(double value) → unit_id(#Pnt/#Pxl)
新（正确）： UntF → unit_id(#Pnt/#Pxl) → f64(double value)
```

参考 Rust 实现（`descriptor.rs:110-112`）：

```rust
writer.write_signature("UntF");
writer.write_signature("#Pxl");  // unit ID first
writer.write_f64(*number);       // value second
```

**这是导致文字栅格化的根本原因。** psd-tools 解析修复前的 PSD 时报 `b'@\x10\x00\x00' is not a valid Enum` 错误，修复后解析完全正常。

### 验证结果

| 测试场景 | PS 中可编辑？ | 说明 |
|---------|-------------|------|
| 横排英文（ArialMT） | ✅ 可编辑 | 字体缺省（系统无该字体时） |
| 竖排中文（ArialMT） | ✅ 可编辑 | 竖排文字正常 |
| 竖排中文（尚古圆体SC） | ✅ 可编辑 | 字体缺省，手动替换即可 |
| 真实项目导出（含多文字块） | ❌ 栅格化 | 原因未明，见下方 |

## 当前已确认的问题

### 1. 背景色块（文本框空白区域被填充） ⚠️

**现象**：文字图层在 PS 中显示为带背景色的矩形块，而非仅文字区域的像素。

**原因**：`_crop_from_result()` 从 `result/` 目录的已渲染图像中裁剪文字块区域时，裁到了整个 bounding box 的像素（含文字周围的空白/背景）。BallonsTranslator 的 `result/` 图像是整页合图，文字块周围的背景像素一并被裁剪。

**当前行为**：
```
文字块 xyxy=[5, 10, 55, 30]
→ 从 result/ 裁剪 (5, 10) 到 (55, 30) 的矩形区域
→ 该区域包含文字 + 周围背景色（非透明）
```

**改进方向**：
- 利用 `blk.lines` 多边形数据，裁剪时只取文字多边形内部像素
- 或从 `blk.region_mask` 提取 alpha 通道，将非文字区域置为透明
- 或让渲染管线输出单独的透明背景文字图层

### 2. 依赖 result/ 目录的存在 ⚠️

`_crop_from_result()` 依赖 `proj.get_result_path(page_name)` 获取已渲染图像。若用户未渲染结果图（仅做了翻译），文字层像素将退化为全透明占位，PS 中不可见。

### 3. 特定项目数据导致栅格化（未解决） ❓

**现象**：对某些特定项目导出 PSD 后，文字层在 PS 中栅格化（不可编辑）。用完全相同参数人工构造的 TextBlock 导出后可编辑，说明不是通用代码 bug。

**已知不影响的因素**：
- 竖排/横排 — 已验证均正常
- 字体名含 `0x5C`（反斜杠）字节 — `_write_escaped_byte` 正确转义，PS 能正常解码
- 粉红色 / 非黑色文字颜色
- FauxBold=True
- 小字号（14.8pt）

**可能的排查方向**（未验证）：
- TextBlock 中特定属性值组合（如 `angle` 非零、`font_family` 为 None、缺失 `fontformat` 等）
- DPI 值导致 `font_size * 72.0 / dpi` 产生非预期结果
- 渲染管线中某些 TextBlock 字段被修改但未同步到 `fontformat`
- 多个文字块间字体名索引混乱

## 验证方法

### 单元测试

```
python -m pytest tests/test_psd_binary.py -v
```

50 个测试覆盖：writer 原语、PackBits 编码、Descriptor 序列化、EngineData 生成、完整 PSD 导出。

### 人工验证

1. 导出一页含文字的 PSD
2. 在 PS 中打开，检查：
   - 图层结构（原图 / 修补 / 遮罩 / 文字）
   - 文字是否可编辑（文字工具点击文字层）
   - 文字位置是否与原图对齐

### 调试手段

```bash
# TySh 结构对比（vs 参考 PSD）
PYTHONIOENCODING=utf-8 python scripts/compare_tysh_items.py

# psd-tools 解析验证
pip install psd-tools
python -c "from psd_tools import PSDImage; psd = PSDImage.open('output.psd'); print(psd); [print(l) for l in psd]"
```

## 导出文件列表

| 文件 | 行数 | 备注 |
|------|------|------|
| `utils/psd_binary_writer.py` | ~110 | 已验证 |
| `utils/psd_packbits.py` | ~100 | 已验证 |
| `utils/psd_descriptor.py` | ~150 | UnitFloat 顺序已修复 |
| `utils/psd_engine_data.py` | ~300 | EngineDict 已验证 |
| `utils/psd_binary_exporter.py` | ~450 | 含裁剪逻辑和 TySh 组装 |
| `tests/test_psd_binary.py` | ~250 | 50 个测试 |

## 修改过的文件

| 文件 | 改动 |
|------|------|
| `utils/psd_exporter.py` | 工厂支持 `export_method` |
| `ui/psd_export_dialog.py` | 方法选择器 ComboBox |
| `ui/mainwindow.py` | 透传导出方法，完成消息区分 |
| `utils/psd_descriptor.py` | UnitFloat 顺序修复、新增 `unit_points()`、`bounds_descriptor()` 支持 unit 参数 |
| `utils/psd_engine_data.py` | 多项 EngineData 结构修复 |
| `utils/psd_binary_exporter.py` | TySh 加 `TxMg`、`AnCr`、`#Pnt` bounds |
