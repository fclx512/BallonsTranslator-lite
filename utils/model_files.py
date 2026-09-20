"""模型权重文件的落盘判据、体积统计与释放。

**单一来源**：模块声明的权重文件（``download_file_list`` 的 ``save_files``／
``files``）在磁盘上是否齐备，此前在 ``modules/__init__.py::GET_MISSING_MODEL_FILES``
与 ``ui/module_manager.py::_ensure_module_deps`` 里各写了一遍。两者口径必须一致，
故一并收进本模块的 :func:`missing_declared_files`，那两处改为调用这里。

设计背景与安全边界见 ``docs/技术实现/模型文件管理_设计方案.md``。三条硬约束：

1. 删除对象的唯一来源是模块自己声明的落盘路径——绝不接受运行时拼出来的任意
   路径，绝不按目录盲删（单文件模块的父目录是 ``data/models/``，那是全部模型的家）。
2. 不碰被 git 跟踪的文件（当前只有 ``data/models/ppocrv6_onnx/ppocrv6_dict_proper.txt``
   这一份字典）。运行时不宜调 git，用白名单排除实现。
3. 只删文件，不删目录。空目录只做「空了就 rmdir」的收尾，且绝不越过
   ``data/models/`` 这条上界。
"""

import os
import os.path as osp
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .logger import logger as LOGGER
from . import shared

__all__ = [
    "GIT_TRACKED_MODEL_FILES",
    "MODEL_ROOT_REL",
    "DeleteResult",
    "declared_save_files",
    "delete_paths",
    "dir_size_bytes",
    "format_size",
    "missing_declared_files",
    "package_dir_of",
    "package_size_bytes",
    "size_hint_bytes",
]

# 相对程序根目录的模型存放根。删除收尾（rmdir 空目录）绝不越过这一层。
MODEL_ROOT_REL = "data/models"

# 唯一被 git 跟踪的模型文件（见 .gitignore 的白名单）。删除逻辑必须显式跳过它，
# 否则会弄脏工作区。新增被跟踪的字典／配置时同步加到这里。
GIT_TRACKED_MODEL_FILES = frozenset(
    {"data/models/ppocrv6_onnx/ppocrv6_dict_proper.txt"}
)


def _abs(path: str) -> str:
    """把声明里的相对路径解析成绝对路径（已是绝对路径则原样返回）。"""
    if osp.isabs(path):
        return osp.normpath(path)
    return osp.normpath(osp.join(shared.PROGRAM_PATH, path))


def _norm_rel(path: str) -> str:
    """归一化成「相对程序根、正斜杠」的展示用路径。"""
    if osp.isabs(path):
        try:
            path = osp.relpath(path, shared.PROGRAM_PATH)
        except ValueError:
            return path.replace("\\", "/")
    return path.replace("\\", "/")


def declared_save_files(download_file_list) -> List[str]:
    """取出下载清单里声明的落盘路径（相对程序根、去重、保持声明顺序）。

    只读 ``save_files``，缺省回落到 ``files``——与
    ``utils/download_util.py::download_and_check_files`` 的 ``_wrap_up_checkinputs``
    同源。``save_dir`` 字段刻意不支持：两个消费方都不读它，用了会让缺文件检查
    全部误报（见设计文档 §4.1）。
    """
    out: List[str] = []
    for entry in download_file_list or []:
        if not isinstance(entry, dict):
            continue
        paths = entry.get("save_files") or entry.get("files") or []
        if isinstance(paths, str):
            paths = [paths]
        for path in paths:
            if not isinstance(path, str) or not path:
                continue
            norm = _norm_rel(path)
            if norm not in out:
                out.append(norm)
    return out


def missing_declared_files(download_file_list) -> List[str]:
    """返回声明了但磁盘上不存在的落盘路径（相对程序根），齐备则空列表。"""
    return [
        rel
        for rel in declared_save_files(download_file_list)
        if not osp.exists(_abs(rel))
    ]


def package_dir_of(model_package, download_file_list=None) -> Optional[str]:
    """返回包根目录（相对程序根），未声明则 ``None``。

    **刻意不做反推**：单文件模块（``ysgyolo``、LaMa）的落盘文件就在
    ``data/models/`` 下，按父目录反推会得到「全部模型的家」，拿它算体积、
    拿它删都出事。未声明即视为**文件型包**，只按声明的 ``save_files``
    逐个处理（设计文档 §4.3）。
    """
    if isinstance(model_package, dict):
        declared = model_package.get("dir")
        if isinstance(declared, str) and declared.strip():
            return _norm_rel(declared.strip())
    return None


def dir_size_bytes(path: str) -> int:
    """递归统计目录内所有文件的总字节数（目录不存在返回 0）。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += osp.getsize(osp.join(root, name))
            except OSError:
                continue
    return total


def package_size_bytes(model_package, download_file_list) -> int:
    """当前占用体积：声明了 ``dir`` 就算整目录，否则只累加已存在的声明文件。"""
    pkg_dir = package_dir_of(model_package, download_file_list)
    if pkg_dir is not None:
        return dir_size_bytes(_abs(pkg_dir))
    total = 0
    for rel in declared_save_files(download_file_list):
        full = _abs(rel)
        if osp.exists(full):
            try:
                total += osp.getsize(full)
            except OSError:
                continue
    return total


def format_size(num_bytes: int) -> str:
    """人类可读体积，如 ``1.8 GB``；0 显示为 ``0 B``。"""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGT]?I?B?)\s*$", re.I)


def size_hint_bytes(size_hint) -> Optional[int]:
    """把包描述里的 ``size_hint``（如 ``"1.9 GB"`` / ``"1.9GB"``）解析成字节数。

    解析不出返回 ``None``——体积提示是给人看的，读不懂就不显示，绝不当成判据。
    """
    if isinstance(size_hint, (int, float)):
        return int(size_hint)
    if not isinstance(size_hint, str):
        return None
    match = _SIZE_RE.match(size_hint)
    if match is None:
        return None
    number = float(match.group(1))
    raw_unit = (match.group(2) or "B").upper().replace("IB", "B") or "B"
    if raw_unit in ("K", "M", "G", "T"):
        raw_unit += "B"
    factor = _SIZE_UNITS.get(raw_unit)
    if factor is None:
        return None
    return int(number * factor)


@dataclass
class DeleteResult:
    """一次释放动作的结果。"""

    removed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    # True 表示走的是直接删除（没进回收站，不可还原）
    permanent: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed


def trash_available() -> bool:
    """当前环境能否把文件送进回收站（Windows + SHFileOperation 可用）。"""
    if not shared.ON_WINDOWS:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.SHFileOperationW)
    except Exception:
        return False


def _trash_windows(paths: List[str]) -> Tuple[bool, str]:
    """经 Windows Shell 把文件送进回收站。返回 ``(Shell 调用是否没抛异常, 诊断说明)``。

    **返回码不能当成功判据。** 本机实测（Win11，进程无交互窗口站）同一批文件在
    不同标志位下给出三种码，而文件**都已进回收站**：

    =============================== ============ ==========================
    标志                            返回码       ``fAnyOperationsAborted``
    =============================== ============ ==========================
    无                              0            False
    ``FOF_ALLOWUNDO``               1223          True
    ``+ FOF_SILENT``                2             False
    ``+ FOF_NOERRORUI``             2             False
    =============================== ============ ==========================

    所以成功与否一律由**调用方**在调用后 `osp.exists` 判定（``delete_paths``
    就是这么做的），这里只把返回码当诊断信息带出去，并且只在 Shell 调用整个
    抛异常（例如无 pywin32 且 ctypes 也不可用）时返回 ``False``。

    优先用 ``win32com.shell``（pywin32 已在 requirements 里，零新增依赖），
    不可用时退回 ``ctypes`` 直调 ``shell32.SHFileOperationW``。
    """
    payload = "\0".join(paths) + "\0\0"
    flags_allow_undo = 0x40  # FOF_ALLOWUNDO —— 送回收站而非永久删除
    # FOF_SILENT(0x4) | FOF_NOCONFIRMATION(0x10) | FOF_NOERRORUI(0x400)
    flags_silent = 0x0004 | 0x0010 | 0x0400
    flags = flags_allow_undo | flags_silent

    try:
        import pythoncom  # noqa: F401  (win32com 依赖，先导入以确认可用)
        from win32com.shell import shell, shellcon

        result = shell.SHFileOperation(
            (
                0,
                shellcon.FO_DELETE,
                payload,
                None,
                flags,
                None,
                None,
            )
        )
        code = result[0] if isinstance(result, tuple) else result
        return True, f"win32com code={code}"
    except Exception as com_error:  # 无 pywin32 或调用失败 → ctypes 兜底
        try:
            import ctypes

            class _SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", ctypes.c_void_p),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_uint16),
                    ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p),
                ]

            op = _SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = 0x0003  # FO_DELETE
            op.pFrom = payload
            op.pTo = None
            op.fFlags = flags
            code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            return True, f"ctypes code={code}"
        except Exception as e:
            return False, f"{com_error}; ctypes fallback failed: {e}"


def _is_strictly_within(path: str, root: str) -> bool:
    """``path`` 是否在 ``root`` 内部（不含 ``root`` 自身）。"""
    path = osp.normcase(osp.normpath(osp.abspath(path)))
    root = osp.normcase(osp.normpath(osp.abspath(root)))
    return path != root and path.startswith(root + osp.sep)


def _prune_empty_dirs(start_dir: str, roots: List[str]) -> None:
    """自下而上删掉空目录，只允许在 ``roots`` 内部动，且遇到非空即停。

    只用 ``os.rmdir``——它在目录非空时必然失败，因此这条路径天然删不掉内容；
    ``roots`` 自身（``data/models``）永远不会被删。
    """
    allowed = [osp.normpath(osp.abspath(r)) for r in roots]
    current = osp.normpath(osp.abspath(start_dir))
    while osp.isdir(current):
        if not any(_is_strictly_within(current, root) for root in allowed):
            break
        try:
            if os.listdir(current):
                break
            os.rmdir(current)
        except OSError:
            break
        parent = osp.dirname(current)
        if parent == current:
            break
        current = parent


def delete_paths(
    paths: List[str],
    *,
    package_dir: str = None,
    use_trash: bool = True,
    protect: frozenset = GIT_TRACKED_MODEL_FILES,
    prune_empty: bool = True,
) -> DeleteResult:
    """删除给定的落盘路径（逐个校验），返回 :class:`DeleteResult`。

    入参 ``paths`` 应是模块声明的落盘路径（相对程序根或绝对路径均可）。
    被 git 跟踪的文件、不存在的路径会被跳过并记进 ``skipped``。
    目录从不删除，只在 ``prune_empty`` 时收尾清掉空壳。
    """
    result = DeleteResult()
    targets: List[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        rel = _norm_rel(raw.strip())
        if rel in protect:
            LOGGER.info("Skip git-tracked model file: %s", rel)
            result.skipped.append(rel)
            continue
        full = _abs(rel)
        if not osp.exists(full):
            result.skipped.append(rel)
            continue
        if osp.isdir(full):
            # 声明里出现目录说明口径有问题——拒绝，绝不递归删。
            LOGGER.warning("Refusing to delete declared directory: %s", rel)
            result.skipped.append(rel)
            continue
        targets.append(full)

    if not targets:
        return result

    if use_trash and trash_available():
        result.permanent = False
        called, note = _trash_windows(targets)
        if note:
            LOGGER.info("Recycle bin call: %s", note)
        if not called:
            # Shell 整个调不通：不偷偷改成永久删除（确认框里承诺过可还原）。
            # 全部记成失败，交给界面告知，用户可「打开目录」自己处理。
            LOGGER.error("Recycle bin call failed: %s", note)
            result.error = note
            result.failed = [_norm_rel(full) for full in targets]
            return result
    else:
        result.permanent = True
        if use_trash:
            result.error = "recycle bin unavailable; files were deleted permanently"
        for full in targets:
            try:
                os.remove(full)
            except OSError as e:
                LOGGER.warning("Failed to delete %s: %s", full, e)

    # 成功判据一律看「调用后文件还在不在」——Windows Shell 的返回码不可信
    # （见 _trash_windows 的实测表）。
    for full in targets:
        rel = _norm_rel(full)
        if osp.exists(full):
            result.failed.append(rel)
        else:
            result.removed.append(rel)
    if result.failed and not result.error:
        result.error = "some files could not be removed"

    if prune_empty and result.removed:
        # 允许清理的范围：模型根 + 声明过的包目录（若在模型根之外）。
        # 收尾只用 rmdir，非空即停，且 data/models 自身永不被删。
        roots = [_abs(MODEL_ROOT_REL)]
        if package_dir:
            abs_pkg = _abs(package_dir)
            if not _is_strictly_within(abs_pkg, roots[0]) and osp.normcase(
                abs_pkg
            ) != osp.normcase(roots[0]):
                roots.append(abs_pkg)
        for rel in result.removed:
            _prune_empty_dirs(osp.dirname(_abs(rel)), roots)

    return result
