"""「释放内存」的验收 + 为什么**不**销毁 CUDA 上下文（真机、只读项目数据）。

两件事拼在一个脚本里，因为它们是同一个决定的两面（2026-09-20，用户拍板）：

1. **验收现在这条路径**（`ui/mainwindow.py` 的按钮＝`utils/memory_release.py::release_memory`）：
   卸载模型 → 交回工作集；期望工作集掉到几 MB，**之后 torch 与 `paddleocr_vl_manga`
   全部照常**。
2. **复算被删掉的那一步的后果**：本地 ctypes 直调 `cudart!cudaDeviceReset`（代码已从
   `utils/memory_release.py` 删除，这里只留证据）。实测——`torch.cuda.is_available()` 仍返回
   True 骗人，第一次真实分配报 `cudaErrorInvalidValue`，同一段代码另一次**直接段错误**
   （exit `0xC0000005`），且进程内救不回来（`torch.cuda.init()` / `set_device` /
   `empty_cache` / `synchronize` / `_cuda_clearCublasWorkspaces()` / 直调 cudart 全试过）。
   它只多还 70~166MB，代价是本次会话 GPU 模型全废 ⇒ 删除。

**第 2 步可能把进程打崩**，所以放最后，且报告逐行落盘（崩了也不丢前面的结论）。

跑法（仓库根目录；没装 VL 权重时第 1 步的最后一段会跳过）：

    ballontrans_pylibs_win\\python.exe scripts/probes/release_cuda_context_aftermath.py
"""
import ctypes
import gc
import glob
import os.path as osp
import sys
import time

PROGRAM_PATH = osp.abspath(osp.join(osp.dirname(__file__), "..", ".."))
sys.path.insert(0, PROGRAM_PATH)

VL_DIR = osp.join(PROGRAM_PATH, "data", "models", "paddleocr_vl_manga")
PROJ_DIR = r"D:\汉化\施工区副本"
REPORT = osp.join(PROGRAM_PATH, "tmp", "release_aftermath_report.md")

_fh = open(REPORT, "w", encoding="utf-8")


def emit(text="") -> None:
    _fh.write(str(text) + "\n")
    _fh.flush()


def step(title: str) -> None:
    emit()
    emit(f"## {title}")


import torch  # noqa: E402

from utils.memory_release import release_memory, working_set_mb  # noqa: E402

emit("# 「释放内存」的验收 + 为什么不销毁 CUDA 上下文")
emit()
emit(f"- torch `{torch.__version__}` / cuda `{torch.version.cuda}`，设备 "
     f"`{torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(无)'}`")


def probe_cuda(label: str) -> bool:
    """一次分配 + 一次 matmul（覆盖 caching allocator 与 cuBLAS）。"""
    try:
        value = int(torch.zeros(4, device="cuda").sum().item())
    except Exception as e:  # noqa: BLE001
        emit(f"- {label} → **分配 FAIL**：`{type(e).__name__}: {str(e)[:200].splitlines()[0]}`")
        return False
    try:
        got = (torch.randn(8, 8, device="cuda") @ torch.randn(8, 8, device="cuda")).sum().item()
    except Exception as e:  # noqa: BLE001
        emit(f"- {label} → 分配 OK 但 **matmul FAIL**：`{type(e).__name__}: {str(e)[:200].splitlines()[0]}`")
        return False
    emit(f"- {label} → 分配 + matmul 都 OK（{value} / {got:.3f}），"
         f"allocated {torch.cuda.memory_allocated() / 2**20:.0f} MB")
    return True


step("0. 基线")
emit(f"- 工作集 {working_set_mb():.0f} MB")
probe_cuda("torch CUDA")

step("1. 验收：release_memory（卸载 + 交回工作集）")
emit(f"- 释放前工作集 {working_set_mb():.0f} MB")
report = release_memory(None)
emit(f"- 读数：before={report.before_mb:.0f} after_unload={report.after_unload_mb} "
     f"after_trim={report.after_trim_mb} MB")
emit(f"- unloaded={report.unloaded} working_set_returned={report.working_set_returned} "
     f"errors={report.errors}")
time.sleep(2)
emit(f"- 两秒后工作集 {working_set_mb():.0f} MB")
safe_cuda_ok = probe_cuda("释放后的 torch CUDA（必须照常）")

emit()
emit("### 1.1 释放之后 paddleocr_vl_manga 还能不能照常跑")
if not osp.isdir(VL_DIR):
    emit("- SKIP：本机没有 `data/models/paddleocr_vl_manga/`，这一步测不了")
    vl_ok = None
else:
    from modules.ocr.ocr_vl_manga import PaddleOCRVLManga
    from utils.proj_imgtrans import ProjImgTrans

    proj = ProjImgTrans(directory=PROJ_DIR)
    page = max(proj.pages, key=lambda k: len(proj.pages[k]))
    blks = proj.pages[page][:2]
    img = proj.read_img(page)

    mod = PaddleOCRVLManga()
    vl_ok = True
    try:
        t0 = time.time()
        mod.load_model()
        emit(f"- 加载 OK（{time.time() - t0:.1f}s），device=`{mod._device}`")
        t0 = time.time()
        mod._ocr_blk_list(img, blks)
        emit(f"- 推理 OK（{time.time() - t0:.1f}s）："
             + " | ".join("".join(b.text) or "(空)" for b in blks))
    except Exception as e:  # noqa: BLE001
        vl_ok = False
        emit(f"- **FAIL**：`{type(e).__name__}: {str(e)[:300]}`")
    finally:
        try:
            mod.unload_model()
        except Exception:  # noqa: BLE001
            pass
        del mod
        gc.collect()

step("2. 历史证据：销毁 CUDA 上下文会怎样（该能力已从代码里删除）")
emit()
emit("下面**直接调 cudart**（不再经过 `utils/memory_release.py`，那里已经没有这条路径）：")
lib_dir = osp.join(osp.dirname(torch.__file__), "lib")
hits = sorted(glob.glob(osp.join(lib_dir, "cudart*.dll")))
emit(f"- cudart：`{osp.basename(hits[0]) if hits else '(未找到)'}`")
if hits:
    torch.cuda.synchronize()
    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
    before_reset_mb = working_set_mb()
    lib = ctypes.WinDLL(hits[0])
    fn = lib.cudaDeviceReset
    fn.argtypes = []
    fn.restype = ctypes.c_int
    rc = int(fn())
    gc.collect()
    emit(f"- `cudaDeviceReset()` → rc={rc}（0＝成功）；工作集 "
         f"{before_reset_mb:.0f} → {working_set_mb():.0f} MB")
    emit(f"- 真还 ≈ {int(before_reset_mb - working_set_mb())} MB（这一点点就是它换来的全部）")
    emit(f"- `torch.cuda.is_available()` 仍返回 **{torch.cuda.is_available()}**（骗人）")
    emit()
    emit("第一次真实分配（**可能直接把进程打崩**，所以放最后）：")
    deep_ok = probe_cuda("reset 之后的 torch CUDA")
    if not deep_ok:
        emit()
        emit("进程内救回尝试（列在这里是因为当初都试过、都没有效果）：`torch.cuda.init()`、")
        emit("`torch.cuda.set_device(0)`、`empty_cache()`、`synchronize()`、")
        emit("`_cuda_clearCublasWorkspaces()`、`cudart!cudaSetDevice`/`cudaFree(0)`。")
else:
    deep_ok = None

step("3. 结论")
emit(f"- 现路径（卸载 + 交回工作集）：torch CUDA 照常 = **{safe_cuda_ok}**，"
     f"`paddleocr_vl_manga` 照常 = **{vl_ok if vl_ok is not None else '未测'}**")
emit(f"- 销毁上下文：之后 torch CUDA 可用 = **{deep_ok}** ⇒ 只多还几十~一百多 MB，"
     "却要重启应用、还可能崩进程，**已删除**。")
emit("- 详见 `docs/技术实现/内存释放_设计与实现.md` §4。")

_fh.close()
print("report written:", REPORT)
print("safe_cuda_ok:", safe_cuda_ok, "vl_ok:", vl_ok, "deep_cuda_ok:", deep_ok)
