"""手动释放内存：把「跑完管线后留在进程里的那部分」尽量交回给系统。

**为什么需要它**：CUDA 相关的内存不只在 torch 的池里。实测（2026-09-17，本机
RTX + torch 2.13+cu132，ppocrv6 检测 + paddleocr OCR 各跑一次）：

| 动作 | 进程工作集 |
|---|---|
| 引擎导入 + 开项目（未用 CUDA） | 590 MB |
| 跑完一轮 CUDA 管线 | 1503 MB |
| 卸掉全部模型 + `gc` | 1503 MB（**几乎不动**） |
| `torch.cuda.empty_cache()` | 1503 MB（也没用） |
| `psapi!EmptyWorkingSet()` | **111 MB**（见下"交回"语义） |
| 之后再用一次 CUDA | 420 MB（首次 0.33s） |

⇒ "卸载模型"救不回这部分；能救的是 ``return_working_set()``，而它只是"交回"。

**只有"交回"一种语义（别再往"真释放"上加）**：``return_working_set()`` 把工作集退给
**系统备用内存**（驱动/DLL 的干净文件映射页系统可立即丢弃给别的程序，私有脏页进
pagefile）。任务管理器数字立刻回落、别的程序能拿到内存，但页表映射还在，
**下次访问要 fault in**（实测首次 CUDA 用 0.33s，可忽略）。此处不写成"释放"是有意的。

**已删除的能力：``cudaDeviceReset``（2026-09-20，用户拍板删掉）**。它曾在这里做"真还
~170~208MB"那一步，但实测**不可逆地毁掉本进程的 CUDA**（"释放内存之后
paddleocr_vl_manga 再也跑不起来"就是这么来的），整条路径已移除：

- `torch.cuda.is_available()` / `is_initialized()` 在 reset 之后**仍返回 True**（骗人）；
- 第一次真实分配报 `cudaErrorInvalidValue`；同一段代码另一次**直接段错误**
  （exit `0xC0000005`，整个进程没了）；
- `torch.cuda.init()` / `set_device` / `empty_cache` / `synchronize` /
  `_cuda_clearCublasWorkspaces()` / 直调 `cudart` 的 `cudaSetDevice`/`cudaFree(0)`
  **都救不回来**——失败点就是第一次分配本身 ⇒ 只能重启进程。

**别把它加回来**：真还那点内存的代价是"本次会话的 GPU 模型全废"，不值；要"确定性释放到
进程外"，唯一的干净做法是把这段工作放进子进程、然后结束子进程（本机重建 ≈2~3s，
见 `docs/技术实现/内存释放_设计与实现_存档.md` "不做的事"）。读数与复算脚本见同文档 §4 与
`scripts/probes/release_cuda_context_aftermath.py`。

**调用方必须遵守**：交回工作集会把界面也要用的页换出去，紧接着的交互可能卡一下
（毫秒级），所以由用户**手动**触发，不做成"跑完自动"；调用前还要确认没有 CUDA 活在跑
（管线／后台推理线程）——正在用的页换出去再 fault 回来是白折腾。
"""

import ctypes
import gc
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .logger import logger as LOGGER

__all__ = [
    "ReleaseReport",
    "release_memory",
    "return_working_set",
    "working_set_mb",
]


@dataclass
class ReleaseReport:
    """一次释放动作的读数与结果（给界面拼提示用，不做文案）。

    各 ``after_*`` 字段为 ``None`` 表示该步没执行。
    """

    before_mb: float = 0.0
    after_unload_mb: Optional[float] = None
    after_trim_mb: Optional[float] = None
    #: 卸载回调的返回值；``None``＝调用方没提供卸载回调
    unloaded: Optional[bool] = None
    working_set_returned: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def after_mb(self) -> float:
        """最后一步的读数（没执行任何步骤时退回 ``before_mb``）。"""
        for value in (self.after_trim_mb, self.after_unload_mb):
            if value is not None:
                return value
        return self.before_mb

    @property
    def freed_mb(self) -> float:
        """看起来少了多少（交回的那部分也算在内，逗号前的数字就是它）。"""
        return max(0.0, self.before_mb - self.after_mb)


class _ProcessMemoryCounters(ctypes.Structure):
    """``PROCESS_MEMORY_COUNTERS``（只用到 ``WorkingSetSize``，其余凑结构体）。"""

    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def working_set_mb() -> float:
    """本进程工作集（任务管理器看到的"内存"），MB。取不到返回 0.0。

    **必须显式声明 argtypes**：``GetCurrentProcess()`` 的伪句柄是 -1，不声明会被
    当 32 位整数传进去 → 调用失败 → 静默返回 0（看起来像"没有内存"）。
    """
    if os.name != "nt":
        return 0.0
    try:
        import ctypes.wintypes as wt

        psapi = ctypes.WinDLL("psapi.dll")
        fn = psapi.GetProcessMemoryInfo
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wt.DWORD]
        fn.restype = wt.BOOL
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        info = _ProcessMemoryCounters()
        info.cb = ctypes.sizeof(_ProcessMemoryCounters)
        if not fn(kernel32.GetCurrentProcess(), ctypes.byref(info), info.cb):
            return 0.0
        return info.WorkingSetSize / 1024 / 1024
    except Exception as e:  # 非 Windows / 结构体不匹配
        LOGGER.warning(f"working set query failed: {e}")
        return 0.0


def return_working_set() -> bool:
    """把本进程的工作集交回系统备用内存（实测 1503→111MB）。

    语义是**交回不是 free**：页表映射还在，下次访问要 fault in。仅 Windows 有效。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes.wintypes as wt

        psapi = ctypes.WinDLL("psapi.dll")
        psapi.EmptyWorkingSet.argtypes = [wt.HANDLE]
        psapi.EmptyWorkingSet.restype = wt.BOOL
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        ok = bool(psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()))
    except Exception as e:
        LOGGER.warning(f"EmptyWorkingSet failed: {e}")
        return False
    if ok:
        LOGGER.info("working set returned to the system")
    return ok


def release_memory(
    unload: Optional[Callable[[], bool]] = None,
    *,
    trim_working_set: bool = True,
    measure: Callable[[], float] = working_set_mb,
) -> ReleaseReport:
    """按「卸载模型 → 交回工作集」跑一遍并记录读数。

    - ``unload``：无参回调，返回"是否卸干净"。不参与成败判定，只用于让 trim 更有效
      （少留脏私有页）并在读数里体现；抛异常会记进 ``errors``。
    - ``trim_working_set``：可关（测试与降级用）。
    - ``measure``：读数函数，默认 ``working_set_mb``；测试注入替身即可。

    不做任何异常抛出：单步失败只写进 ``report.errors``。**调用方必须先确认没有
    CUDA／推理工作在跑**，见模块 docstring。
    """
    report = ReleaseReport(before_mb=measure())

    if unload is not None:
        try:
            report.unloaded = bool(unload())
        except Exception as e:
            report.unloaded = False
            report.errors.append(f"unload failed: {e}")
            LOGGER.error(f"release_memory: unload failed: {e}")
    gc.collect()
    report.after_unload_mb = measure()

    if trim_working_set:
        try:
            report.working_set_returned = return_working_set()
        except Exception as e:
            report.errors.append(f"working set trim failed: {e}")
            LOGGER.error(f"release_memory: working set trim failed: {e}")
        report.after_trim_mb = measure()

    return report
