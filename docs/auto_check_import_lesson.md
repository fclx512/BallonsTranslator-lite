# 自动代码检查误删 re-export 的教训

## 现象

2026-05-29，自动代码检查工具扫描全库时删除了大量"未使用的 import"，导致启动时一连串 `ImportError: cannot import name 'XXX'`。

## 根因

工具判断 import 是否"未使用"的依据是它在当前文件中是否被直接引用。但这忽略了 Python 中常见的 **re-export 模式**：

```python
# utils/structures.py
from typing import List, Dict  # ← 本文件未直接使用 List/Dict

# 但其他文件依赖从 structures 导入：
#   from .structures import Config, List, Dict, field, nested_dataclass
```

这类 re-export 被删除时，本文件不会报错（工具认为"清理成功"），但所有依赖它的模块会立刻崩溃。

## 波及范围

| 文件 | 被删的 re-export | 依赖方 |
|------|-----------------|--------|
| `utils/structures.py` | `List, Dict, Union, Tuple, field` | `fontformat.py`, `config.py`, `textblock.py`, `misc.py` |
| `modules/ocr/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `ocr/__init__.py`, `ocr_mit.py` |
| `modules/textdetector/base.py` | `DEFAULT_DEVICE, DEVICE_SELECTOR` | `textdetector/__init__.py` |
| `modules/translators/base.py` | `DEVICE_SELECTOR` | `translators/__init__.py` |

## 教训

1. **Python 静态分析工具无法理解 re-export 语义。** 删除 import 前必须 grep 全库确认没有其他模块从当前位置导入该名字。
2. **批量修改后必须先跑完整导入链验证。** 仅 `ruff check .` 通过不代表能启动。
3. **`from .base import *` 模式（如 `modules/translators/__init__.py`）让问题更难被发现。** 任何从 `..base` 删除的导出都会静默消失，直到运行时才暴露。

## 预防

- 对所有 `__init__.py` 中的 `from .base import *` 保持警惕，优先显式列出导出名。
- 未来做全库 import 清理时，至少运行 `python -c "from modules.base import init_module_registries; init_module_registries()"` 验证模块注册无异常。
