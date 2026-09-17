"""手动释放内存：把「跑完管线后留在进程里的那部分」尽量还回去。

**为什么需要它**：CUDA 相关的内存不只在 torch 的池里。实测（2026-09-17，本机
RTX + torch 2.13+cu132，ppocrv6 检测 + paddleocr OCR 各跑一次）：

| 动作 | 进程工作集 |
|---|---|
| 引擎导入 + 开项目（未用 CUDA） | 590 MB |
| 跑完一轮 CUDA 管线 | 1503 MB |
| 卸掉全部模型 + `gc` | 1503 MB（**几乎不动**） |
| `torch.cuda.empty_cache()` | 1503 MB（也没用） |
| `cudart!cudaDeviceReset()` | 1295 MB（**−208MB，真还**，GPU 侧完全归还） |
| `psapi!EmptyWorkingSet()` | **111 MB**（见下"交回"语义） |
| 之后再用一次 CUDA | 420 MB（首次 0.33s） |

⇒ "卸载模型"救不回这部分，能救的只有本模块的两级动作。

**两种语义别混**：

- ``release_cuda_context()`` 是**真释放**——销毁本进程的 CUDA 上下文，驱动侧
  资源与 GPU 显存完全归还（nvidia-smi 回到基线）。
- ``return_working_set()`` 是**交回**——把工作集退给**系统备用内存**（DLL 的
  干净文件映射页系统可立即丢弃给别的程序，私有脏页进 pagefile）。任务管理器
  数字立刻回落、别的程序能拿到内存，但页表映射还在，**下次访问要 fault in**
  （实测首次 CUDA 用 0.33s，可忽略）。此处不写成"释放"是有意的。

**安全性（实测，调用方必须遵守）**：

1. ``cudaDeviceReset`` 之前**必须** ``torch.cuda.empty_cache()``，且没有活跃的
   CUDA 张量／模型。实测不先 empty_cache 直接 reset → torch 进入损坏状态：
   ``matmul`` 报 ``CUBLAS_STATUS_INTERNAL_ERROR``，随后任何分配报
   ``an illegal memory access was encountered``。先 empty_cache 再 reset 则
   一切正常。``release_memory`` 用"卸载回调返回 False"来挡这一条（卸载没成功就
   不 reset）。
2. reset 时**不能有别的 CUDA 工作在进行**（管线在跑、后台推理线程、区域再检测
   的后台线程）——那些对象会拿到失效指针。调用方负责确认空闲，本模块不猜。
3. 交回工作集会把界面也要用的页换出去，紧接着的交互可能卡一下（毫秒级），
   所以由用户**手动**触发，不做成"跑完自动"。
"""

import ctypes
import gc
import glob
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .logger import logger as LOGGER

__all__ = [
    "ReleaseReport",
    "release_cuda_context",
    "release_memory",
    "return_working_set",
    "working_set_mb",
]


@dataclass
class ReleaseReport:
    """一次释放动作的读数与结果（给界面拼提示用，不做文案）。

    各 ``after_*`` 字段为 ``None`` 表示该步没执行（例如卸载失败时跳过了 reset）。
    """

    before_mb: float = 0.0
    after_unload_mb: Optional[float] = None
    after_reset_mb: Optional[float] = None
    after_trim_mb: Optional[float] = None
    #: 卸载回调的返回值；``None``＝调用方没提供卸载回调
    unloaded: Optional[bool] = None
    context_reset: bool = False
    working_set_returned: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def after_mb(self) -> float:
        """最后一步的读数（没执行任何步骤时退回 ``before_mb``）。"""
        for value in (self.after_trim_mb, self.after_reset_mb, self.after_unload_mb):
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


def _torch_module():
    try:
        import torch

        return torch
    except Exception:
        return None


def _find_cudart_library() -> Optional[str]:
    """torch 自带的 ``cudart`` 动态库路径（reset 要从这里取）。"""
    torch = _torch_module()
    if torch is None:
        return None
    libdir = os.path.join(os.path.dirname(torch.__file__), "lib")
    for pattern in ("cudart*.dll", "libcudart*.so*", "libcudart*.dylib"):
        hits = sorted(glob.glob(os.path.join(libdir, pattern)))
        if hits:
            return hits[0]
    return None


def release_cuda_context() -> bool:
    """销毁本进程的 CUDA 上下文（真还，实测约 −208MB）。

    顺序：``torch.cuda.synchronize`` → ``empty_cache`` → ``ipc_collect`` →
    ``cudart!cudaDeviceReset``。**empty_cache 不能省**（见模块 docstring 第 1 条）。

    返回是否成功销毁；torch 不可用、找不到 cudart、或 reset 返回非 0 均为 ``False``。
    """
    torch = _torch_module()
    if torch is None:
        LOGGER.warning("CUDA context reset skipped: torch is not available")
        return False
    try:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as e:
        # 同步失败说明还有活在跑 —— 此时 reset 会拿到失效指针，直接放弃
        LOGGER.warning(f"CUDA context reset skipped, device is busy: {e}")
        return False

    lib_path = _find_cudart_library()
    if lib_path is None:
        LOGGER.warning("CUDA context reset skipped: cudart library not found")
        return False
    try:
        lib = ctypes.WinDLL(lib_path) if os.name == "nt" else ctypes.CDLL(lib_path)
        fn = lib.cudaDeviceReset
        fn.argtypes = []
        fn.restype = ctypes.c_int
        rc = int(fn())
    except Exception as e:
        LOGGER.warning(f"CUDA context reset failed: {e}")
        return False
    if rc != 0:
        LOGGER.warning(f"cudaDeviceReset returned {rc}")
        return False
    LOGGER.info("CUDA context released")
    return True


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
    reset_context: bool = True,
    trim_working_set: bool = True,
    measure: Callable[[], float] = working_set_mb,
) -> ReleaseReport:
    """按「卸载模型 → 销毁 CUDA 上下文 → 交回工作集」跑一遍并记录读数。

    - ``unload``：无参回调，返回"是否卸干净"。**返回 False 时不做 reset**（卸载
      没成功意味着可能有活跃 CUDA 对象，reset 会让它们拿到失效指针）。
    - ``reset_context`` / ``trim_working_set``：两步各自可关（测试与降级用）。
    - ``measure``：读数函数，默认 ``working_set_mb``；测试注入替身即可，无需真 CUDA。

    不做任何异常抛出：单步失败只写进 ``report.errors``，后续步骤照常（trim 与
    reset 互不依赖）。**调用方必须先确认没有 CUDA 工作在进行**，见模块 docstring。
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

    if reset_context:
        if report.unloaded is False:
            report.errors.append("CUDA context reset skipped: models are still loaded")
            LOGGER.warning(
                "release_memory: reset skipped because unloading did not succeed"
            )
        else:
            try:
                report.context_reset = release_cuda_context()
            except Exception as e:
                report.errors.append(f"context reset failed: {e}")
                LOGGER.error(f"release_memory: context reset failed: {e}")
            gc.collect()
            report.after_reset_mb = measure()

    if trim_working_set:
        try:
            report.working_set_returned = return_working_set()
        except Exception as e:
            report.errors.append(f"working set trim failed: {e}")
        report.after_trim_mb = measure()

    return report
