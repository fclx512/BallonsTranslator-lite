"""Global style library — user-curated cross-project style templates.

Aegisub 式分层（设计见 docs/技术实现/全局样式库_设计方案_存档.md）：

* 全局样式库（本模块）＝持久化的命名样式模板集合，存
  ``config/global_styles.json``；条目与项目内大样式
  （``utils/base_styles.py::BaseStyle``）同构，序列化直接复用其
  ``to_dict``/``from_dict``。
* 与项目样式的关系是**拷贝而非引用**：库→项目 / 项目→库 双向复制后
  两边各自独立演化。复制到项目只是登记大样式，块按身份键
  ``(font_family, vertical)`` 归组，库本身绝不自动改任何块。
* 库内 ``name`` 唯一（``unique_name`` 生成不冲突名）；``identity``
  允许重复——模板可以同字体同方向不同参数并存，冲突只在复制到项目
  时按项目侧规则处理（UI 层弹窗）。
* 每次改动后立即 ``save_global_styles`` 落盘，不依赖应用退出。
"""

from __future__ import annotations

import json
import logging
import os
import os.path as osp
from typing import List

from utils import shared
from utils.base_styles import BaseStyle

LOGGER = logging.getLogger(__name__)

global_styles: List[BaseStyle] = []


def load_global_styles(p: str = shared.GLOBAL_STYLES_PATH) -> None:
    """Load the library from *p* into the module-level list (tolerant)."""
    global global_styles
    if not osp.exists(p):
        LOGGER.info(f"Global style library {p} does not exist; starting empty.")
        return
    loaded: List[BaseStyle] = []
    try:
        with open(p, "r", encoding="utf8") as f:
            entries = json.loads(f.read())
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry or "fontformat" not in entry:
                LOGGER.warning(f"Skip invalid global style entry: {entry}")
                continue
            try:
                loaded.append(BaseStyle.from_dict(entry))
            except Exception:
                LOGGER.warning(f"Skip invalid global style entry: {entry}")
    except Exception as e:
        LOGGER.error(f"Failed to load global style library from {p}: {e}")
        return
    global_styles.clear()
    global_styles.extend(loaded)


def save_global_styles(p: str = shared.GLOBAL_STYLES_PATH) -> bool:
    """Persist the module-level list to *p* (atomic tmp + replace)."""
    try:
        style_dir = osp.dirname(p)
        if not osp.exists(style_dir):
            os.makedirs(style_dir)
        tmp_save_tgt = p + ".tmp"
        with open(tmp_save_tgt, "w", encoding="utf8") as f:
            f.write(json.dumps([bs.to_dict() for bs in global_styles], ensure_ascii=False))
    except Exception as e:
        LOGGER.error(f"Failed to save global style library to {p}: {e}")
        return False
    os.replace(tmp_save_tgt, p)
    return True


def find_by_name(name: str) -> BaseStyle | None:
    """First library entry whose name equals *name* (names are unique)."""
    for bs in global_styles:
        if bs.name == name:
            return bs
    return None


def unique_name(name: str) -> str:
    """Return *name* or the first free ``"{name} {n}"`` suffix in the library."""
    if find_by_name(name) is None:
        return name
    n = 2
    while find_by_name(f"{name} {n}") is not None:
        n += 1
    return f"{name} {n}"


def add_style(name: str, fontformat) -> BaseStyle:
    """Append a deepcopied entry under a conflict-free name and save."""
    from utils.fontformat import FontFormat

    entry = BaseStyle(unique_name(name), fontformat.deepcopy() if fontformat else FontFormat())
    global_styles.append(entry)
    save_global_styles()
    return entry


def remove_style(entry: BaseStyle) -> None:
    """Drop *entry* from the library and save (no-op when absent)."""
    if entry in global_styles:
        global_styles.remove(entry)
        save_global_styles()
