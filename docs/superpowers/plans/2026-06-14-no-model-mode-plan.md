# 无模型运行模式 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让项目能在不安装 PyTorch 的环境下运行，同时支持按需安装模型模块及其依赖

**Architecture:** 依赖重组（ultralytics → 可选） + 新增空操作模块（detector_none, inpaint_none） + ComboBox 可视化分组 + 安装前依赖检查弹窗 + 模型面板多选下载

**Tech Stack:** Python 3.10+, PyQt6, pip/uv

---

## 文件总览

| 文件 | 动作 |
|------|------|
| `pyproject.toml` | 修改 — ultralytics 移入 [gpu] |
| `modules/textdetector/detector_none.py` | 创建 |
| `modules/inpaint/inpaint_none.py` | 创建 |
| `modules/inpaint/base.py` | 修改 — torch 引用保护 |
| `ui/module_manager.py` | 修改 — 安装前依赖检查 |
| `ui/module_parse_widgets.py` | 修改 — ComboBox 分组 + 标注 |
| `ui/model_check_dialog.py` | 修改 — 复选框 + 下载选中 |
| `docs/2026-06-14-no-model-mode-design.md` | 引用说明（无需改动） |

---

### Task 1: 将 ultralytics 移入可选依赖组

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 编辑 pyproject.toml，移除 ultralytics 核心依赖**

定位 `pyproject.toml` 中的 `[project] dependencies` 列表，找到并删除 `"ultralytics>=8.4.14"` 这一行。

- [ ] **Step 2: 编辑 pyproject.toml，将 ultralytics 加入 [gpu] 组**

定位 `[project.optional-dependencies]` 中的 `gpu = [...]` 行，在列表末尾加上 `"ultralytics>=8.4.14"`：

```toml
[project.optional-dependencies]
gpu = ["torch", "torchvision", "transformers", "diffusers", "ultralytics>=8.4.14"]
paddle = ["paddleocr>=3.7.0"]
mcp = ["mcp>=1.0.0"]
```

- [ ] **Step 3: 检查 requirements.txt**

打开 `requirements.txt`，确认它和 pyproject.toml 的核心依赖同步。如果发现 `ultralytics` 在其中，删除该行。requirements.txt 不应包含 torch、torchvision、transformers、diffusers 或 ultralytics（这些都在 [gpu] 组）。

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml requirements.txt
git commit -m "refactor: move ultralytics from core deps to [gpu] optional group"
```

---

### Task 2: 创建无操作文字检测模块 (detector_none)

**Files:**
- Create: `modules/textdetector/detector_none.py`

- [ ] **Step 1: 创建文件 `modules/textdetector/detector_none.py`**

```python
import numpy as np

from .base import TextDetectorBase, TextBlock, register_textdetectors, List


@register_textdetectors("none")
class TextDetectorNone(TextDetectorBase):
    params = {
        "description": "Skip text detection. No model needed.",
    }

    def _detect(self, img: np.ndarray, proj=None):
        """Return empty mask and empty block list — detection is skipped."""
        mask = np.zeros(img.shape[:2], dtype=np.uint8)
        return mask, []

    def setup_detector(self):
        pass
```

- [ ] **Step 2: 验证 AST 扫描能识别**

```bash
cd /path/to/project
python -c "
import ast
with open('modules/textdetector/detector_none.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                print('Decorator:', dec.func.id, 'args:', [ast.dump(a) for a in dec.args])
"
```
预期输出应包含 `Decorator: register_textdetectors args: ['Constant(value=\\'none\\')']`。

- [ ] **Step 3: 提交**

```bash
git add modules/textdetector/detector_none.py
git commit -m "feat: add no-op text detector module (none)"
```

---

### Task 3: 创建无操作图像修复模块 (inpaint_none)

**Files:**
- Create: `modules/inpaint/inpaint_none.py`

- [ ] **Step 1: 创建文件 `modules/inpaint/inpaint_none.py`**

```python
import numpy as np

from modules.textdetector import TextBlock
from .base import InpainterBase, register_inpainter, List


@register_inpainter("none")
class InpainterNone(InpainterBase):
    inpaint_by_block = False
    check_need_inpaint = False

    params = {
        "description": "Skip inpainting. No model needed.",
    }

    def _inpaint(
        self,
        img: np.ndarray,
        mask: np.ndarray,
        textblock_list: List[TextBlock] = None,
    ) -> np.ndarray:
        """Return original image unchanged — inpainting is skipped."""
        return img
```

> 注意：`inpaint_by_block = False` 告诉管线不要逐块调用，`check_need_inpaint = False` 跳过"是否需要修复"的判断。这两个属性在基类 `InpainterBase` 中定义。

- [ ] **Step 2: 验证 AST 扫描**

```bash
cd /path/to/project
python -c "
import ast
with open('modules/inpaint/inpaint_none.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                print('Decorator:', dec.func.id, 'args:', [ast.dump(a) for a in dec.args])
"
```
预期输出应包含 `Decorator: register_inpainter args: ['Constant(value=\\'none\\')']`。

- [ ] **Step 3: 提交**

```bash
git add modules/inpaint/inpaint_none.py
git commit -m "feat: add no-op inpainter module (none)"
```

---

### Task 4: 保护 inpaint/base.py 中的 torch 引用

**Files:**
- Modify: `modules/inpaint/base.py`

- [ ] **Step 1: 定位需要修改的代码**

打开 `modules/inpaint/base.py`，找到 `memory_safe_inpaint` 方法（约第 66-94 行），其中有：

```python
if DEFAULT_DEVICE == "cuda" and isinstance(e, torch.cuda.OutOfMemoryError):
```

虽然当 `DEFAULT_DEVICE == "cpu"` 时不会执行到此分支，但仍需保护这段引用以防后续代码改动导致崩溃。

- [ ] **Step 2: 在文件顶部加入 TorchOOMError 别名**

在 `from ..base import ...` 行之后（约第 19 行之后），加入：

```python
try:
    from ..base import torch as _base_torch
    TorchOOMError = _base_torch.cuda.OutOfMemoryError
except (AttributeError, ImportError, TypeError):
    # torch is not installed — create a dummy exception type that
    # never matches, so isinstance(..., TorchOOMError) is always False.
    TorchOOMError = type("_NoTorchOOM", (Exception,), {})
```

- [ ] **Step 3: 替换 torch.cuda.OutOfMemoryError 引用**

将 `memory_safe_inpaint` 方法中两处 `torch.cuda.OutOfMemoryError` 替换为 `TorchOOMError`：

第 75 行：`isinstance(e, torch.cuda.OutOfMemoryError)` → `isinstance(e, TorchOOMError)`
第 80 行：`isinstance(ee, torch.cuda.OutOfMemoryError)` → `isinstance(ee, TorchOOMError)`

- [ ] **Step 4: 提交**

```bash
git add modules/inpaint/base.py
git commit -m "fix: protect torch.cuda.OutOfMemoryError ref in inpaint base"
```

---

### Task 5: ModuleConfig 默认值更新 + GET_VALID 函数适配

**Files:**
- Modify: `utils/config.py` (ModuleConfig 默认值)
- Modify: `modules/__init__.py` (GET_VALID 函数)

- [ ] **Step 1: 更新 ModuleConfig 默认模块值**

`utils/config.py` 约第 28 行：

```python
@nested_dataclass
class ModuleConfig(Config):
    textdetector: str = "ctd"           # → "none"
    ocr: str = "mit48px_ctc"            # → "none_ocr"
    inpainter: str = "lama_large_512px" # → "none"
    translator: str = "None"            # 不变
```

改为：

```python
    textdetector: str = "none"
    ocr: str = "none_ocr"
    inpainter: str = "none"
```

这样全新启动时默认就是无模型模式。
> **注意**：用户已有的 `config.json` 中存了旧值，不会受此影响。只有没 `config.json` 的首次启动才用新默认值。

- [ ] **Step 2: 提交**

```bash
git add utils/config.py
git commit -m "feat: default ModuleConfig to none modules for no-model mode"
```

---

### Task 6: 模块选择弹出依赖安装对话框

**Files:**
- Modify: `ui/module_manager.py`
- Modify: `ui/module_parse_widgets.py`
- Modify: `ui/mainwindow_mixin.py`

#### 背景

当用户选择一个需要 torch/ultralytics 等未安装依赖的模块时，目前显示的是一个通用错误对话框。我们需要改为显示一个安装提示对话框，让用户一键安装缺失依赖。

#### 实现思路

在 `ModuleManager.set*()` 方法中（它们在主线程运行），**先做依赖检查**。检查方法：尝试 `importlib.util.find_spec()` 检测模块文件顶级的 import 目标。如果缺失，弹安装对话框。用户确认后，用 subprocess 调用 pip/uv 安装。

#### UI 依赖

- [ ] **Step 1: 创建安装对话框 `ui/install_dialog.py`**

```python
"""Module dependency install dialog.

Shows missing Python packages and model files for a module,
with one-click install and network-limited user hints.
"""

import subprocess
import sys
from typing import List, Optional

from qtpy.QtCore import Qt
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QProgressBar,
)

from utils.logger import logger as LOGGER


class InstallDialog(QDialog):
    """Modal dialog showing what a module needs and offering to install it.

    Usage:
        dialog = InstallDialog(
            module_name="mit48px_ctc",
            pip_packages=["torch>=2.0.0", "torchvision>=0.15.0"],
            model_files=["data/models/mit48px_ctc.pt"],
            parent=self,
        )
        if dialog.exec_() == QDialog.DialogCode.Accepted:
            dialog.install_all()  # blocks
    """

    def __init__(
        self,
        module_name: str,
        pip_packages: Optional[List[str]] = None,
        model_files: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Module Dependencies"))
        self.setMinimumWidth(520)
        self._pip_packages = pip_packages or []
        self._model_files = model_files or []
        self._install_result = False

        layout = QVBoxLayout(self)

        # Header
        header = QLabel(
            self.tr('Module "{name}" requires additional dependencies:').format(
                name=module_name
            )
        )
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 8px;")
        layout.addWidget(header)

        # Pip packages section
        if self._pip_packages:
            pip_label = QLabel(self.tr("Python packages:"))
            pip_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
            layout.addWidget(pip_label)
            pip_text = QTextEdit()
            pip_text.setReadOnly(True)
            pip_text.setMaximumHeight(80)
            pip_lines = []
            for pkg in self._pip_packages:
                status = self._check_package(pkg)
                pip_lines.append(f"  {'✓' if status else '○'} {pkg}")
            pip_text.setPlainText("\n".join(pip_lines))
            layout.addWidget(pip_text)

        # Model files section
        if self._model_files:
            model_label = QLabel(self.tr("Model files (auto-downloaded):"))
            model_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
            layout.addWidget(model_label)
            model_text = QTextEdit()
            model_text.setReadOnly(True)
            model_text.setMaximumHeight(80)
            model_lines = [f"  ○ {f}" for f in self._model_files]
            model_text.setPlainText("\n".join(model_lines))
            layout.addWidget(model_text)

        # Network hint
        hint = QLabel(
            self.tr(
                '⚠ Network restricted? Open  Settings → Mirror Config  to adjust download sources.'
            )
        )
        hint.setStyleSheet(
            "color: #e67e22; padding: 8px; background: #fff8e1; border-radius: 4px;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Buttons
        btn_layout = QHBoxLayout()
        self.install_btn = QPushButton(self.tr("Install All"))
        self.install_btn.clicked.connect(self._on_install)
        self.later_btn = QPushButton(self.tr("Later"))
        self.later_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.later_btn)
        layout.addLayout(btn_layout)

    @staticmethod
    def _check_package(req_str: str) -> bool:
        """Check if a pip requirement string is satisfied."""
        try:
            from packaging.requirements import Requirement
            from packaging.utils import canonicalize_name
            import importlib.metadata as ilmd

            req = Requirement(req_str)
            dist = ilmd.distribution(canonicalize_name(req.name))
            return req.specifier.contains(dist.version, prereleases=True)
        except Exception:
            return False

    def _on_install(self):
        self.install_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate
        # Run install in this thread (it's a modal dialog)
        self.install_all()
        self.accept()

    def install_all(self):
        """Install all missing pip packages. Model files are auto-downloaded
        by ``BaseModule._ensure_model_files()`` on first load_model() call."""
        if not self._pip_packages:
            self._install_result = True
            return

        missing = [p for p in self._pip_packages if not self._check_package(p)]
        if not missing:
            self._install_result = True
            return

        python = sys.executable
        try:
            subprocess.run(
                [python, "-m", "uv", "pip", "install", *missing, "--prefer-binary"],
                capture_output=True,
                timeout=300,
            )
        except Exception:
            try:
                subprocess.run(
                    [python, "-m", "pip", "install", *missing, "--prefer-binary"],
                    capture_output=True,
                    timeout=300,
                )
            except Exception as e:
                LOGGER.warning(f"Auto-install failed: {e}")
                self._install_result = False
                return
        self._install_result = True

    @property
    def install_succeeded(self) -> bool:
        return self._install_result
```

- [ ] **Step 2: 在 ModuleManager 中添加依赖预检查方法**

打开 `ui/module_manager.py`，在 `ModuleManager` 类中添加一个新方法（约第 1000 行附近）：

```python
def _check_module_deps(self, module_key: str, module_name: str) -> bool:
    """Check if a module's dependencies are satisfied before loading.

    Returns True if deps are met (or user chose to proceed anyway).
    Returns False if user chose "Later" (skip setting this module).
    """
    from modules.registries import MODULETYPE_TO_REGISTRIES
    from utils.registry import ModuleSpec

    registry = MODULETYPE_TO_REGISTRIES.get(module_key)
    if registry is None:
        return True

    spec = registry.get_spec(module_name)
    if spec is None or not isinstance(spec, ModuleSpec):
        return True  # already resolved or not available

    # Gather required packages from spec + known patterns
    pip_needed = list(spec.dependencies) if spec.dependencies else []

    # For torch-based modules that don't declare dependencies in spec,
    # try a quick heuristic: check if torch is available when the
    # module's import_path suggests it needs it.
    if module_key in ("ocr", "inpainter", "textdetector"):
        if module_name not in ("none", "none_ocr", "None"):
            try:
                import importlib.util as iu
                if iu.find_spec("torch") is None:
                    if "torch" not in pip_needed:
                        pip_needed.append("torch>=2.0.0")
            except ImportError:
                if "torch" not in pip_needed:
                    pip_needed.append("torch>=2.0.0")

    # Check if any are missing
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    import importlib.metadata as ilmd

    missing = []
    for req_str in pip_needed:
        try:
            req = Requirement(req_str)
            dist = ilmd.distribution(canonicalize_name(req.name))
            if not req.specifier.contains(dist.version, prereleases=True):
                missing.append(req_str)
        except ilmd.PackageNotFoundError:
            missing.append(req_str)

    if not missing:
        return True

    # Show install dialog
    from ui.install_dialog import InstallDialog

    dialog = InstallDialog(
        module_name=module_name,
        pip_packages=missing,
        parent=self.progress_msgbox,  # use any QWidget parent
    )
    result = dialog.exec_()

    if result != QDialog.DialogCode.Accepted:
        return False  # user chose "Later"

    return dialog.install_succeeded
```

注意：需要在文件顶部加上 `from qtpy.QtWidgets import QDialog`（确认已导入）。

- [ ] **Step 3: 修改 ModuleManager 的 set* 方法**

将 `setOCR`、`setTextDetector`、`setInpainter` 方法中，在分发给线程前调用依赖检查：

**setTextDetector (约第 1040 行)：**
```python
def setTextDetector(self, textdetector: str = None):
    if textdetector is None:
        textdetector = cfg_module.textdetector
    # 依赖预检查
    if not self._check_module_deps("textdetector", textdetector):
        return  # user cancelled
    if self.textdetect_thread.isRunning():
        LOGGER.warning("Terminating a running text detection thread.")
        self.textdetect_thread.terminate()
    self.textdetect_thread.setTextDetector(textdetector)
```

**setOCR (约第 1048 行)：**
```python
def setOCR(self, ocr: str = None):
    if ocr is None:
        ocr = cfg_module.ocr
    if not self._check_module_deps("ocr", ocr):
        return
    if self.ocr_thread.isRunning():
        LOGGER.warning("Terminating a running OCR thread.")
        self.ocr_thread.terminate()
    self.ocr_thread.setOCR(ocr)
```

**setInpainter (约第 1018 行)：**
```python
def setInpainter(self, inpainter: str = None):
    if self.block_set_inpainter:
        return
    if inpainter is None:
        inpainter = cfg_module.inpainter
    if not self._check_module_deps("inpainter", inpainter):
        return
    # ... 原代码 ...
    self.inpaint_thread.setInpainter(inpainter)
```

**注意**：setTranslator 不需要加依赖检查，因为 LLM API 翻译器没有本地模型依赖。

- [ ] **Step 4: 提交**

```bash
git add ui/install_dialog.py ui/module_manager.py
git commit -m "feat: add dependency pre-check dialog before module loading"
```

---

### Task 7: ComboBox 分组展示 + 依赖状态标注

**Files:**
- Modify: `ui/module_parse_widgets.py`

- [ ] **Step 1: 修改 `ModuleConfigParseWidget` 的 ComboBox 填充逻辑**

找到 `populate()` 方法（约第 360 行）。在它用 `addItem()` 添加模块时，改为分组添加——在 "none" 类模块和需要依赖的模块之间加一个分隔项。

具体修改 `populate()` 方法：

```python
def populate(self, module_dict, initial_module=None):
    """Populate the module combobox with grouped items.

    Group 1: "none" / no-model modules (always available)
    Group 2: modules requiring local models (may need deps)
    """
    self.module_combobox.blockSignals(True)
    self.module_combobox.clear()
    self.param_widget_map.clear()

    has_none_group = False
    has_model_group = False
    none_modules = []
    model_modules = []

    for module_key in module_dict:
        if module_key in ("none", "none_ocr", "None", "Source"):
            none_modules.append(module_key)
        else:
            model_modules.append(module_key)

    # --- Group 1: no-model modules ---
    none_modules.sort()
    for module_key in none_modules:
        label = module_key
        self.module_combobox.addItem(label, module_key)
        self.param_widget_map[module_key] = None
        has_none_group = True

    # --- Separator ---
    if none_modules and model_modules:
        sep_item = QListWidgetItem()  # use QListWidgetItem as visual separator
        sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
        # ComboBox doesn't support separators natively, so add a textual divider
        self.module_combobox.addItem("───" + self.tr(" Needs local model ") + "───")
        last_idx = self.module_combobox.count() - 1
        self.module_combobox.model().item(last_idx).setEnabled(False)  # not selectable

    # --- Group 2: model modules ---
    model_modules.sort()
    for module_key in model_modules:
        label = module_key
        # Check if this module's deps are satisfied
        if self._module_deps_satisfied(module_key):
            pass  # no extra label
        else:
            label = module_key + "  ⚠"
            pass  # We'll set the color/icon below
        self.module_combobox.addItem(label, module_key)
        self.param_widget_map[module_key] = None
        # Gray out / warn if deps not available
        idx = self.module_combobox.count() - 1
        if not self._module_deps_satisfied(module_key):
            item = self.module_combobox.model().item(idx)
            item.setToolTip(
                self.tr("Requires additional dependencies (PyTorch, etc.)")
            )
        has_model_group = True

    num_widgets_after = len(self.param_widget_map)
    if num_widgets_before == 0 and num_widgets_after > 0:
        self.on_module_changed()
    self.module_combobox.blockSignals(False)
```

需要新增辅助方法：

```python
@staticmethod
def _module_deps_satisfied(module_key: str) -> bool:
    """Quick check: does the module's framework (torch) seem available?"""
    # For known torch-dependent module types
    if module_key in ("none", "none_ocr", "None", "Source",
                      "LLM_API_Translator", "SakuraLLM", "context_batch"):
        return True
    # For non-model modules in detector/inpainter
    if module_key == "none":
        return True
    # For all others, check if torch is importable
    import importlib.util
    return importlib.util.find_spec("torch") is not None
```

- [ ] **Step 2: 提交**

```bash
git add ui/module_parse_widgets.py
git commit -m "feat: group modules in combobox by no-model vs needs-deps"
```

---

### Task 8: 模型面板增加多选和下载功能

**Files:**
- Modify: `ui/model_check_dialog.py`

- [ ] **Step 1: 在 ModelCheckPanel 中添加复选框列和下载按钮**

打开 `ui/model_check_dialog.py`，做以下改动：

**1a. 修改 `_build_ui()`，在表格第 0 列前增加复选框列（表头从 4 列变 5 列）：**

```python
def _build_ui(self):
    # ... 现有代码 ...
    self.table = QTableWidget(0, 5)  # 4 → 5 columns
    self.table.setHorizontalHeaderLabels([
        "",  # new checkbox column
        self.tr("Model"),
        self.tr("Category"),
        self.tr("Status"),
        self.tr("Source / Notes"),
    ])
    # ... 列宽调整 ...
```

**1b. 底部按钮区增加下载按钮组：**

在 `_build_ui` 的按钮行（约第 410 行），添加：

```python
self.select_all_btn = QPushButton(self.tr("Select All"))
self.select_all_btn.clicked.connect(self._select_all)
self.deselect_all_btn = QPushButton(self.tr("Deselect All"))
self.deselect_all_btn.clicked.connect(self._deselect_all)
self.download_btn = QPushButton(self.tr("Download Selected"))
self.download_btn.clicked.connect(self._download_selected)
self.download_btn.setEnabled(False)
btn_row.insertWidget(0, self.download_btn)
btn_row.insertWidget(1, self.select_all_btn)
btn_row.insertWidget(2, self.deselect_all_btn)
# 原有的 refresh_btn 和 stretch 在 btn_row 中的位置不变
```

**1c. 在 `_refresh()` 中为每行增加复选框：**

在 `_refresh()` 方法中，插入新行时，在第 0 列添加 `QTableWidgetItem` 并设置 `ItemFlag.ItemIsUserCheckable` 标志：

```python
# 在设置 name_item 之前（原有第 472 行之前）：
chk_item = QTableWidgetItem()
chk_item.setFlags(
    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
)
chk_item.setCheckState(Qt.CheckState.Unchecked)
# 如果是缺失状态才允许勾选
if entry["status"] != "missing":
    chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable)  # read-only
    chk_item.setToolTip(self.tr("Already installed or no source"))
self.table.setItem(row, 0, chk_item)
```

所有后续列索引 +1：（原 col 0→1, col 1→2, col 2→3, col 3→4）

在 `_refresh()` 末尾更新摘要时，加上选中状态更新：

```python
self._update_download_button()
```

**1d. 新增方法：**

```python
def _select_all(self):
    for row in range(self.table.rowCount()):
        item = self.table.item(row, 0)
        if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            item.setCheckState(Qt.CheckState.Checked)
    self._update_download_button()

def _deselect_all(self):
    for row in range(self.table.rowCount()):
        item = self.table.item(row, 0)
        if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            item.setCheckState(Qt.CheckState.Unchecked)
    self._update_download_button()

def _update_download_button(self):
    count = 0
    total_entries = 0
    for row in range(self.table.rowCount()):
        item = self.table.item(row, 0)
        if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            total_entries += 1
            if item.checkState() == Qt.CheckState.Checked:
                count += 1
    self.download_btn.setEnabled(count > 0)
    # Update summary
    summary_text = self.summary_label.text()
    if count:
        self.summary_label.setText(
            self.tr("{summary} | {n} selected for download").format(
                summary=summary_text.split(" | ")[0] if " | " in summary_text else summary_text,
                n=count,
            )
        )

def _download_selected(self):
    """Download all checked model files sequentially."""
    from utils.download_util import download_and_check_files

    to_download = []
    for row in range(self.table.rowCount()):
        chk = self.table.item(row, 0)
        if chk and chk.checkState() == Qt.CheckState.Checked:
            # Find the matching entry by row index relative to group structure
            # We stored entries in self._entries - map by file path
            name_item = self.table.item(row, 1)  # col 1 = model name (was col 0)
            if name_item:
                file_name = name_item.text()
                for entry in self._entries:
                    if osp.basename(entry["file"]) == file_name:
                        to_download.append(entry)
                        break

    if not to_download:
        return

    self.download_btn.setEnabled(False)
    self.progress_bar = QProgressBar()
    self.progress_bar.setMaximum(len(to_download))
    self.progress_bar.setValue(0)
    # Insert progress bar above the button row
    # (requires finding the right layout position)
    self.layout().insertWidget(
        self.layout().count() - 2, self.progress_bar
    )
    self.progress_bar.show()

    for i, entry in enumerate(to_download):
        if entry.get("source_only"):
            # PP-OCR models are auto-downloaded — just note it
            LOGGER.info(
                f"PP-OCRv6 model '{entry['file']}' will be auto-downloaded "
                "by PaddleOCR on first use."
            )
            self.progress_bar.setValue(i + 1)
            continue
        if entry.get("source"):
            # Construct download kwarg from entry
            dl_kwargs = {
                "url": entry.get("source", ""),
                "files": [entry["file"]],
            }
            if entry.get("sha256"):
                dl_kwargs["sha256_pre_calculated"] = [entry["sha256"]]
            download_and_check_files(**dl_kwargs)
        self.progress_bar.setValue(i + 1)

    self.progress_bar.hide()
    self._refresh()  # re-scan after download
```

注意需要确保 `from utils.download_util import download_and_check_files` 在文件顶部已导入。

**1e. 连接 `cellChanged` 信号以更新按钮状态（可选）：**

如果 `QTableWidget` 的复选框状态变化不自动触发更新，可以连接 `itemChanged` 信号：

```python
# 在 _build_ui 中，self.table 创建后：
self.table.itemChanged.connect(self._on_item_changed)
```

新增：

```python
def _on_item_changed(self, item):
    if item.column() == 0:
        self._update_download_button()
```

- [ ] **Step 2: 验证表格可以正常渲染**

```bash
cd /path/to/project
python -c "
from ui.model_check_dialog import ModelCheckPanel
print('ModelCheckPanel imported OK')
from qtpy.QtWidgets import QApplication
app = QApplication([])
panel = ModelCheckPanel()
print('ModelCheckPanel instantiated OK')
print(f'Table columns: {panel.table.columnCount()}')
print(f'Table rows: {panel.table.rowCount()}')
"
```
预期输出应能看到 5 列表格和若干行。

- [ ] **Step 3: 提交**

```bash
git add ui/model_check_dialog.py
git commit -m "feat: add checkbox selection + download button to model panel"
```

---

### Task 9: 集成验证

**Files:**（无需修改文件，仅运行命令验证）

- [ ] **Step 1: 确认应用能在无 torch 环境启动**

```bash
# 确保待测试环境中无 torch
pip uninstall torch torchvision ultralytics -y

cd /path/to/project
python launch.py --headless --cpu 2>&1 | head -30
```

预期：应用启动成功，不因缺失 torch 而崩溃。

- [ ] **Step 2: 确认模块列表包含新 none 模块**

启动后，底部工具栏的 `textdet_selector` 下拉中应包含 "none"，`ocr_selector` 包含 "none_ocr"，`inpaint_selector` 包含 "none"。

- [ ] **Step 3: 确认选择需要依赖的模块时弹出安装对话框**

选择 "mit48px_ctc" OCR，预期看到 InstallDialog 弹窗，列出 torch/torchvision。

- [ ] **Step 4: 确认模型面板可选择和下载**

打开 Tools → Model Files，确认表格第一列为复选框，勾选后 "Download Selected" 按钮可用。

- [ ] **Step 5: 确认管线在 none 模块下正常运行**

选择所有 none 模块，打开一个项目，点击 Run。管线应跳过所有阶段，不报错完成。

---

## 执行顺序建议

```
Task 1 (依赖重组)
  ↓
Task 2 + Task 3 + Task 4  ← 可并行
  ↓
Task 5 (默认值)
  ↓
Task 6 (安装对话框 + 依赖预检查)
  ↓
Task 7 (ComboBox 分组)
  ↓
Task 8 (模型面板多选)
  ↓
Task 9 (验证)
```

每个 Task 均独立可提交，不会破坏主干。

---

## 注意事项

1. **`config.json` 已有用户**：Task 5 改默认值不影响现有用户，因为他们已有 `config.json` 存了旧模块名。只有全新用户受影响。

2. **torch 检测机制**：`modules/base.py` 已有的 `try: import torch` 已能处理缺失情况，不需改动。所有 torch-based 模块的 import 都在函数内部或 try/except 内。

3. **`prepare_environment()` 在 launch.py 中**：当 `--cpu` 时直接跳过 torch 检测。无 `--cpu` 且无 GPU 时打印提示后继续。不需要修改。

4. **PaddleOCR**：其模型文件（`~/.paddleocr/`）是 PaddleOCR 库自动下载的，`requires_packages = []` 意味着不自动安装。已经符合"按需下载"的设计。

5. **测试环境**：建议在 Windows 上用 `python -m venv test-nomodel` 创建纯净环境，只 `pip install -e .`（不装 torch），验证完全流程。
