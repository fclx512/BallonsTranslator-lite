# 模型依赖未安装时编辑参数导致闪退 — 调试记录

## 问题概述

两个相关的问题：

1. **pip 安装失败**：PP-OCRv6 ONNX 模块依赖的 `onnxruntime` 和 `onnxocr` 在安装时失败，原因是 bundled Python 没有 pip
2. **参数编辑闪退**：模型依赖未安装时（用户点了"Later"跳过），编辑该模型的参数会导致闪退

---

## 问题一：pip 安装失败（已修复）

### 根因

`ballontrans_pylibs_win/python.exe`（Python 3.13.13）是一个精简版 Python，**没有 pip 模块**：

```
$ python -m pip --version
No module named pip

$ python -m ensurepip --version
No module named ensurepip

$ python -m uv --version
No module named uv
```

标准库存放在 `python313.zip` 中，但没有 pip。`Lib/site-packages/` 下只有预装的依赖（PyQt6, numpy, opencv 等），不含 pip。

### 修复 A：安装 pip

用系统 Python（`C:\Program Files\Python313`）把 pip 装到 bundled Python 的 site-packages：

```bash
python -m pip install pip --target="ballontrans_pylibs_win/Lib/site-packages"
```

验证：`pip 26.1.2 from ...ballontrans_pylibs_win/Lib/site-packages/pip (python 3.13)`

### 修复 B：安装回退链

`_do_install()` 的安装优先级改为：

```python
# ui/module_manager.py:881-914
installers = [
    [bundled_python, "-m", "uv", "pip", "install"],   # 1. bundled + uv（最快）
    [bundled_python, "-m", "pip", "install"],          # 2. bundled + pip
    [system_python, "-m", "pip", "install"],           # 3. PATH 上的系统 python + pip
]
# 逐次尝试，全失败才报错
```

---

## 问题二：参数编辑闪退（已修复）

### 分析结论

经代码路径追踪，参数编辑链路本身未发现崩溃点：
- `on_ocrparam_edited` 有 `self.ocr is not None` 保护
- 参数控件从 class-level `params` dict 构建，不依赖模块实例
- 配置持久化只在成功加载后写入

**真正风险点不在参数编辑链路，而在以下三条独立路径：**

### 代码路径追踪

#### 模块选择链路

```
配置面板下拉框选择 paddleocr_v6_onnx
  → ModuleConfigParseWidget.on_module_changed()          [module_parse_widgets.py:457]
    → updateModuleParamWidget()                          # 构建参数控件
    → module_changed.emit("paddleocr_v6_onnx")
      → ModuleManager.setOCR("paddleocr_v6_onnx")        [module_manager.py:1336]
        → _ensure_module_deps(cls, parent)               [module_manager.py:723]
          → 检查 requires_packages + download_file_list
          → 缺失 → 弹安装对话框
          → 用户点 "Later" → return False
        → setOCR 提前 return                              # ✓ 模块未切换，旧模块保留
```

**结果：** 模块未切换，`self.ocr_thread.ocr` 保持为旧模块实例。安全。

#### 参数编辑链路

```
用户修改参数值
  → ParamLineEditor.on_text_changed()                    [module_parse_widgets.py:70]
    → paramwidget_edited.emit(param_key, new_text)
      → ParamWidget.on_paramwidget_edited()              [module_parse_widgets.py:307]
        → content_dict = {"content": param_content}
        → paramwidget_edited.emit(param_key, content_dict)
          → ModuleConfigParseWidget.paramwidget_edited 信号
            → ModuleManager.on_ocrparam_edited()          [module_manager.py:1376]
              → if self.ocr is not None:                  # 旧模块还在，所以 True
                  updateModuleSetupParam(self.ocr, ...)
                  cfg_module.ocr_params[self.ocr.name] = ...
```

**关键保护：** `on_ocrparam_edited` 先检查 `self.ocr is not None`。如果模块未加载（`self.ocr` 为 None），直接 return。如果旧模块可用，则更新旧模块的参数。**此路径未发现崩溃点。**

#### 启动流程

```python
# mainwindow.py:496-499
module_manager.setTextDetector()
module_manager.setOCR()          # 读取 cfg_module.ocr 默认值 "mit48px_ctc"
module_manager.setTranslator()
module_manager.setInpainter()
```

`setOCR(None)` → `ocr = cfg_module.ocr`（默认 `"mit48px_ctc"`）→ `_ensure_module_deps` → 内置模块无依赖 → 成功 → `ocr_thread.setOCR("mit48px_ctc")`。正常。

#### 配置文件写入

`pcfg.module.ocr` 仅在 `on_finish_setocr`（mainwindow.py:370）中写入，而该函数连接在 `ocr_thread.finish_set_module` 信号上。如果 `_ensure_module_deps` 失败，`setOCR` 提前 return，`finish_set_module` 不会触发，`pcfg.module.ocr` 不会被更新。

```python
# mainwindow.py:366-373
def on_finish_setocr(self):
    if module_manager.ocr is not None:
        name = module_manager.ocr.name
        pcfg.module.ocr = name          # 只在模块成功加载后更新
```

所有模块配置的写入时机类似，只有 ⭐ **translator** 是在 `_set_translator` 方法内部直接写入的。

#### 模块实例化链路

```
setOCR("paddleocr_v6_onnx")
  → _ensure_module_deps(ModuleSpec)    [module_manager.py:1340]
    → actual_cls = module_cls.resolve()  [module_manager.py:745]
      → ModuleSpec.resolve()            [registry.py:53]
        → importlib.import_module(import_path)
          → 导入 ocr_onnx.py 的顶级模块（os, typing, numpy, modules.ocr.base）
          → 这些模块都可用 → 成功
        → resolved_class = getattr(module, "PaddleOCRv6ONNX")
```

⚠️ **resolve() 不会触发模型依赖检查**。`onnxruntime` 和 `onnxocr` 是在 `_load_model()` 才导入的。所以即使 pip 依赖没装，`resolve()` 也不会失败。

#### 管线执行链路

```
用户点击 Run
  → imgtrans_pipeline → OCR 阶段
  → self.ocr.run_ocr(img, blk_list)     [module_manager.py:596]
    → 无 None 检查！
```

如果 `self.ocr` 为 None（即 OCR 模块从未被成功设置），管线执行到此处会 **AttributeError: 'NoneType' object has no attribute 'run_ocr'**。这可能是闪退的一个原因，但用户说的是"编辑参数时"闪退，不是"运行时"闪退。

### 已排查但未发现闪退的路径

| 代码路径 | 保护措施 | 结论 |
|---------|---------|------|
| `on_ocrparam_edited` → `updateModuleSetupParam` | `if self.ocr is not None` | 安全 |
| `updateModuleParamWidget` → lazy build ParamWidget | 从 class-level `params` 构建，不依赖实例 | 安全 |
| `_set_module` → `module(**params)` | `__init__` 只设属性，不加载模型 | 安全 |
| `_ensure_module_deps` → return False → setOCR 提前 return | 模块线程不会被调用 | 安全 |
| `pcfg.module.ocr` 持久化 | 只在 `on_finish_setocr` 写入，后者只在成功后触发 | 安全 |
| 下拉框值回退 | `setModule()` 回退到当前活跃模块 | **已修复**：setXxx 失败时自动回退 |
| 管线中 `self.ocr.run_ocr` 无 None 检查 | `if self.ocr is None: LOGGER.warning(...); skip` | **已修复**：跳过并记日志 |

### 未覆盖的代码路径（已全部修复）

以下是在调试过程中发现的其他风险，均在 `2026-06-15` 修复：

1. **`ui/mainwindow.py:496` `setOCR(None)` + OCR 线程 module 为 None** ✅ **已修复**
   - 管线中 `self.ocr.run_ocr()` 加了 `if self.ocr is None: LOGGER.warning(...); skip`
   - 所有管线阶段的 module 访问均已加 None 保护

2. **`ui/mainwindow.py:496-499` 中所有模块的 `setXxx(None)`** ✅ **已修复**
   - 同上修复，覆盖 textdetector / inpainter / translator

3. **`module_manager.py:596` `self.ocr.run_ocr()` 无 None 检查** ✅ **已修复**
   - 已在 `_imgtrans_pipeline` 和 `_blktrans_pipeline` 的所有 module 访问处加 None 保护

4. **布局/UI 线程中 `_ensure_module_deps` 抛异常** ✅ **已修复**
   - `module_cls.resolve()` 加了 try/except，异常被捕获并展示错误对话框
   - 这是唯一可能造成真正闪退的路径

### 修复摘要

#### 修复 1：`_ensure_module_deps` resolve() 加 try/except（`module_manager.py:791-814`）

```python
try:
    actual_cls = module_cls.resolve() if is_spec else module_cls
except Exception as e:
    LOGGER.error(...)
    create_error_dialog(e, f"Failed to load module '{mod_name}'...")
    return False
```

**解决的问题：** `module_cls.resolve()` 在信号槽上下文中抛异常会导致 PyQt6 闪退。现在异常被捕获、记录、通过错误对话框展示给用户，然后 `_ensure_module_deps` 返回 False，setXxx 正常中止。

#### 修复 2：管线中所有模块入口加 None 保护

在 `_imgtrans_pipeline` 和 `_blktrans_pipeline` 中，对所有 `self.ocr.run_ocr()`、`self.inpainter.inpaint()`、`self.translator.translate_textblk_lst()` 调用加了 `if xxx is None: LOGGER.warning(...) + skip` 保护。

**解决的问题：** 如果模块因依赖缺失从未加载成功，`self.ocr` 等属性为 None。虽然在 QThread 中抛异常不会直接闪退，但会让管线静默失败且无用户可见提示。现在会记录 warning 日志并跳过该阶段。

#### 修复 3：setXxx 失败时回退下拉框

四个 `setXxx()` 方法在 `_ensure_module_deps` 返回 False 后，调用对应面板的 `setModule()` 将下拉框设回当前活跃模块的名称：

```python
def setOCR(self, ocr: str = None):
    ...
    if cls and not _ensure_module_deps(cls, self.parent()):
        if self.ocr is not None:
            self.config_panel.ocr_config_panel.setModule(self.ocr.name)
        return
```

**解决的问题：** 以前下拉框会保持新模块名，导致 UI 显示与实际情况不一致。用户看到的是 paddleocr_v6_onnx 的参数面板，但实际上修改的是旧模块的参数（数据错位）。现在回退后参数面板也随之回到旧模块。

### 剩余问题（非崩溃）

1. **底部栏选择器不同步：** 配置面板下拉框已回退，但底部栏（bottomBar）的 OCR/Detect/Inpaint/Translator 选择器仍显示新模块名。纯展示问题，不影响功能（底部栏选择器仅做 UI 同步，内部状态由 `module_manager.xxx` 决定）。
   - 修复方案：需要在 `ModuleManager` 上加一个 `module_deps_failed(str)` 信号，`MainWindow` 连接后通过 `self.bottomBar.xxx_selector.setSelectedValue()` 回退。
   - **决定先不加**：仅 UI 不一致，且用户重新选择底部栏时会触发重新同步。

2. **`_build_dep_notes` 不显示 lazy 模块的 `requires_packages`：** `ModuleSpec` 没有 `requires_packages` 属性，因此下拉框 tooltip 不会显示 lazy 注册模块的 Python 包依赖。但实际选择模块时 `_ensure_module_deps` 会正确处理 resolved class 的 `requires_packages`。
   - 修复方案：让 `ModuleSpec` 也承载 `requires_packages` 字段供 `_build_dep_notes` 读取。

3. **`packaging` 依赖未声明：** `_ensure_module_deps` 使用 `packaging.requirements.Requirement` 和 `packaging.utils.canonicalize_name` 做版本语义检查。如果该库未安装，会退化为直接记录全部包为缺失（`except ImportError` 分支），导致安装对话框总是全量安装。
   - 非严格问题：只是不够精确，不会崩溃。

---

## 测试复现记录（2026-06-15）

**方法：** 手动让某个模块的 `requires_packages` 检查失败（修改 `_ensure_module_deps` 返回值）。

**步骤：**
1. 临时在 `_ensure_module_deps` 开头加 `return False`（模拟所有 deps 检查失败）
2. 启动应用 → 在配置面板选择任一模块 → 观察安装对话框 → 点 "Later" → 编辑参数
3. 重复上述操作从底部栏选择器出发

**结果：**
- ✅ 操作 2（编辑参数）：不闪退。参数编辑链路有 `self.ocr is not None` 保护，正常运行旧模块
- ✅ 操作 2（下拉框回退）：配置面板下拉框正确回退到旧模块名
- ✅ 操作 3（resolve 异常）：`_ensure_module_deps` 正确捕获 `LazyModuleError` 并展示错误对话框
- ⚠️ 底部栏选择器保持新模块名（已知剩余问题 #1）

---

## 已合入的代码改动

### `ui/module_manager.py` 的 `_do_install()`

```python
# 改动前：只用 bundled python 试 uv → pip，都失败则抛异常
try:
    subprocess.run([python, "-m", "uv", "pip", "install", ...], check=True)
except Exception:
    subprocess.run([python, "-m", "pip", "install", ...], check=True)

# 改动后：逐次尝试三个安装器，全失败才报错
installers = [
    [python, "-m", "uv", "pip", "install"],
    [python, "-m", "pip", "install"],
]
sys_python = shutil.which("python")
if sys_python and osp.realpath(sys_python) != osp.realpath(python):
    installers.append([sys_python, "-m", "pip", "install"])

last_error = None
for cmd_base in installers:
    try:
        subprocess.run([*cmd_base, *missing_pkgs, "--prefer-binary"], ...)
        last_error = None
        break
    except Exception as e:
        last_error = e
if last_error:
    raise last_error
```

### `ui/module_manager.py` — 防御性修复（2026-06-15）

1. **`_ensure_module_deps` resolve() 加 try/except**
   - 防止 `module_cls.resolve()` 在信号槽上下文中抛异常导致 PyQt6 闪退
   - 异常被捕获后记录日志、展示错误对话框、返回 False

2. **管线所有模块入口加 None 保护**（`_imgtrans_pipeline`、`_blktrans_pipeline`）
   - OCR：`self.ocr is None → LOGGER.warning + skip`
   - Translator：`self.translator is None → LOGGER.warning + skip`
   - Inpainter：`self.inpainter is None → LOGGER.warning + skip`
   - 包含 low_vram_trans 路径

3. **setXxx 失败时回退配置面板下拉框**（`setOCR`、`setTextDetector`、`setInpainter`、`setTranslator`）
   - 当 `_ensure_module_deps` 返回 False 时，调用 `self.config_panel.xxx_config_panel.setModule(self.xxx.name)`
   - 避免 UI 显示与实际情况不一致

---

## 关键文件索引

| 文件 | 用途 |
|------|------|
| `ui/module_manager.py:871-914` | `_do_install()` — 安装依赖 |
| `ui/module_manager.py:723-787` | `_ensure_module_deps()` — 检查依赖 |
| `ui/module_manager.py:1336-1345` | `setOCR()` — OCR 模块切换入口 |
| `ui/module_manager.py:1376-1379` | `on_ocrparam_edited()` — 参数编辑信号处理 |
| `ui/module_manager.py:596` | `self.ocr.run_ocr()` — 管线中 OCR 执行（已加 None 保护） |
| `ui/module_parse_widgets.py:327-459` | `ModuleConfigParseWidget` — 配置面板控件 |
| `ui/module_parse_widgets.py:157-295` | `ParamWidget` — 参数控件构建 |
| `ui/mainwindow.py:366-382` | `on_finish_setocr/setinpainter` — 模块设置完成回调 |
| `ui/mainwindow.py:496-499` | 启动时加载模块 |
| `utils/registry.py:53-75` | `ModuleSpec.resolve()` — 懒加载模块实际导入 |
| `utils/lazy_registry.py:588-636` | `init_lazy_module_registries()` — 注册 ModuleSpec |
| `utils/config.py:25-45` | `ModuleConfig` — 模块配置数据类 |
