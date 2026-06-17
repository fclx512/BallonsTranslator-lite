# 修复 PSD 二进制导出 — 文字栅格化问题

## 背景

BallonsTranslator-lite 的二进制 PSD 导出功能（`utils/psd_binary_exporter.py`）直接生成 .psd 文件。当前**问题：导出的 PSD 在 Photoshop 中打开时提示导入错误，文字图层被栅格化**，不可编辑。

相关文档：
- 现状总览：[docs/psd_binary_export.md](../../../docs/psd_binary_export.md)
- Rust 参考实现：`D:\ruanjian\koharu-reference\koharu-psd\src\engine_data.rs`
- 对比脚本群：`scripts/` 下的 `extract_psd_tysh.py`、`compare_tysh.py`、`compare_tysh_items.py`、`debug_ref_items.py`

## 关键发现

已经做了两轮修复，**修复仍然无效**。比对参考 PSD（PhotoShop 生成的只有"A"的竖排文字 PSD）发现：

### ✅ 已修且验证匹配

这些修复让 EngineData 结构与参考 PSD **完全匹配**（50 个测试通过）：

1. **[`_paragraph_run_lengths()`]** — 修复的 `RunLengthArray` 长度不包括 `\r` 终止符 → 现在含 `\r`。在 `utils/psd_engine_data.py`。
2. **[EngineData 缩进]** — PICON dict 的缩进层级修正（`in_property=True` 时 `<<` 前补 `\t`）。
3. **[数组缩进]** — `[ ]` 内元素与 key 同级（不是 +1）。
4. **[PICON 浮点格式]** — `0.8` → `.8`（PS 约定）。
5. **[AntiAlias/ShapeType]** — 值匹配 PS 预期。
6. **[删除 BoxBounds]** — PS 不生成这个。
7. **[`\n\n` 前缀]** — EngineData 开头加空行（匹配 PS）。

### ❌ 仅知但未验证是否修复的关键差异

从参考 PSD 的完整 TySh 对比中，在 `utils/psd_binary_exporter.py` 的 `_tysh_body()` 发现**三个结构性差异**（已改动但未验证效果）：

| 差异 | 旧（我们的代码） | 新（参考 PSD） | 状态 |
|------|-----------------|---------------|------|
| 描述符项目数 | 8 项 | **9** 项（多出 `TxMg`） | ✅ 已加 `DescriptorValue.enum("TxMg", "TxNM")` |
| 枚举值 | `AntA` → `antiAliasSharp` | `AntA` → **`AnCr`** | ✅ 已改 |
| bounds 单位 | `#Pxl` 像素 | **`#Pnt`** 磅值 | ✅ 已改（`bounds_descriptor` 加 `unit` 参数，`utils/psd_descriptor.py` 新增 `unit_points()`） |

### EngineData 参数值差异（不是结构问题，但可能影响）

比较我们生成的 EngineData 和参考 PSD 的 EngineData，值有这些差异（在 `utils/psd_engine_data.py`）：

| 参数 | 我们的值 | 参考值 |
|------|---------|-------|
| `AutoHyphenate` | `true` | `false` |
| `AutoLeading` | `1.2` | `1.75` |
| `LeadingType` | `0` | `1` |
| 字体集 | 1 个字体（ArialMT） | 3 个字体（InvisFont, MyriadPro, AdobeHeitiStd） |
| StyleRun FontSize | `72.0` | `50.0` |

这些大概率不影响可编辑性，PS 只是用这些值渲染文字。但若 PS 严格验证值域，可能触发回退。

## 当前状态

**第 2 轮修复完成但尚未验证。** 改动文件：

- `utils/psd_binary_exporter.py` — `_tysh_body()` 加 `TxMg`、改 `AnCr`、`bounds` 用 `#Pnt`
- `utils/psd_descriptor.py` — 新增 `unit_points()` 和通用 `unit_float()` 工厂方法、更新 `bounds_descriptor()` 支持 `unit` 参数

## 接下来的任务

### 任务 1：验证本轮修复是否有效

```bash
python -m pytest tests/test_psd_binary.py -v
```

1. 确保 50 个测试全部通过
2. 打开 BallonsTranslator-lite，创建一个含文字项目
3. 工具 → Export as PSD... → 选 "Binary PSD (direct)" → 导出
4. 在 Photoshop CC/CS6 中打开 .psd
5. 用文字工具（T）点击文字图层 → 检查是否出现可编辑文字光标
6. 如果仍然栅格化 → 进入任务 2

### 任务 2：如果仍然无效，系统性排查

#### 排查思路 A：对比脚本验证修改效果

对比脚本在 `scripts/compare_tysh_items.py`：

```bash
PYTHONIOENCODING=utf-8 python scripts/compare_tysh_items.py
```

验证输出应该显示 OUR TySh 有 9 项（同参考），且 bounds 单位是 `#Pnt`。

#### 排查思路 B：检查 PSD 底层结构

问题可能不在 TySh 本身，而在**整个 PSD 文件的组织**：

1. **图层记录标志** — 文字图层的 flags 是否正确（`0x%08x` 格式？）
2. **附加图层信息的定位** — TySh block 在 "Additional Layer Information" 段的位置和长度
3. **通道数据格式** — 文字图层的像素通道是否与图层记录匹配
4. **BlendMode 键** — 是 `norm` 还是正确值？

检查 `psd_binary_exporter.py` 的 `_write_layer_records()` 和 `_write_additional_layer_info()` 函数的实现。

#### 排查思路 C：全文件对比

生成一个 PS 能打开的参考 PSD 和我们的 PSD，做全文件二进制对比，找出第一个差异点：

```python
# 在已有的 compare_tysh_items.py 基础上，加上完整的 TySh 头尾对比
```

关键检查点：
- 图层记录二进制布局（每层 68 字节头）
- 通道偏移量
- "8BIMTySh" 标签的定位
- TySh 最后的 f32 bounds 值（Top, Left, Bottom, Right）

#### 排查思路 D：用 psd_tools 库验证

```bash
pip install psd-tools
python -c "from psd_tools import PSDImage; psd = PSDImage.open('output.psd'); print(psd); [print(layer) for layer in psd]"
```

看 psd_tools 能否正确解析我们的 TySh 数据。

### 任务 3：如果第 2 轮修复 + 以上排查仍无效

考虑更根本的问题：

1. **Transform 矩阵** — textblock 的 `angle` 值可能为 `None` 或 0 度，但 transform 的构造逻辑是否正确？
2. **字体可用性** — PS 中不存在我们指定的字体时是否会栅格化？尝试用 `ArialMT`、`MyriadPro-Regular` 等广泛存在的字体。
3. **文字方向** — 如果是竖排但 `Ornt` 没设对（Horizontal vs Vertical）。
4. **EngineData `/Rendered` 部分** — 参考 PSD 的 Rendered 部分有 `/PointBase` 字段（我们的是 `/BoxBounds`，已被删除）。确认我们 `/Lines/Children [ ]` 和 `/Cookie/Photoshop/Base` 的结构完整。

## 相关文件索引

| 文件 | 路径 | 用途 |
|------|------|------|
| EngineData 序列化 | [utils/psd_engine_data.py](../../../utils/psd_engine_data.py) | PICON 格式 EngineData 生成 |
| 主导出器 | [utils/psd_binary_exporter.py](../../../utils/psd_binary_exporter.py) | PSD 完整导出 + TySh 组装 |
| Descriptor 序列化 | [utils/psd_descriptor.py](../../../utils/psd_descriptor.py) | AM 描述符二进制写出 |
| Binary Writer | [utils/psd_binary_writer.py](../../../utils/psd_binary_writer.py) | 大端序字节写出 |
| 现有测试 | [tests/test_psd_binary.py](../../../tests/test_psd_binary.py) | 50 个测试，涵盖各函数单元 |
| Rust 参考 | `D:\ruanjian\koharu-reference\koharu-psd\src\engine_data.rs` | 原版 EngineData 实现 |
| 参考 PSD | `D:\下载\测试.psd` | PS 生成的单字"A" PSD（仅 1 个文字层 + 白底） |
| 对比脚本: TySh 结构对比 | [scripts/compare_tysh_items.py](../../../scripts/compare_tysh_items.py) | 逐项对比 OUR vs REF 的描述符 |
| 对比脚本: 字节级对比 | [scripts/compare_tysh.py](../../../scripts/compare_tysh.py) | 完整 TySh 字节级 side-by-side |
| 对比脚本: 参考 PSD 项提取 | [scripts/debug_ref_items.py](../../../scripts/debug_ref_items.py) | 参考 PSD 描述符纯文本解析 |
| 参考 PSD 原始数据 | `D:\下载\_tysh_extracted.bin` | TySh body 二进制 |

## 验证方式

修改后手动测试：

1. 打开一个含文字的项目页面
2. 工具 → Export as PSD... → 方法选 "Binary PSD (direct)" → 导出
3. 在 Photoshop 中打开 .psd
4. 用文字工具（T）点击文字图层 → 应出现可编辑的文字光标
5. 检查文字位置是否与原图对齐
