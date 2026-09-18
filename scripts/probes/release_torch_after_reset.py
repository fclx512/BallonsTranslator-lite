# -*- coding: utf-8 -*-
"""销毁 CUDA 上下文之后，torch 还能不能用（安全性质疑，只读）。

场景：torch 分配过显存 →（cache 里留着块）→ 各种原因走了 cudaDeviceReset。
问题是 reset 之后 torch 的 caching allocator / cuBLAS handle 会不会已经失效：
  - 只 empty_cache 再 reset（cache 清空）；
  - 不 empty_cache 直接 reset（cache 里还留着已失效的块）——这条最危险。
两段都做，且用真实算子（matmul = cuBLAS）而不是单纯分配，看是否报错/结果错。
"""

import ctypes
import ctypes.wintypes as wt
import faulthandler
import gc
import glob
import os
import sys
import time

APP = r"D:\ruanjian\BallonsTranslator-lite"
OUT = os.path.join(APP, "tmp", "_rr_torch_after_reset.out")

sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_log = open(OUT, "w", encoding="utf-8", buffering=1)
faulthandler.enable(file=open(os.path.join(APP, "tmp", "_rr_torch_after_reset.crash"), "w"))


def P(*a):
    line = " ".join(str(x) for x in a)
    _log.write(line + "\n")
    _log.flush()
    try:
        print(line, flush=True)
    except Exception:
        pass


def reset_cuda(torch):
    p = sorted(glob.glob(os.path.join(os.path.dirname(torch.__file__), "lib", "cudart*.dll")))[0]
    lib = ctypes.WinDLL(p)
    lib.cudaDeviceReset.argtypes = []
    lib.cudaDeviceReset.restype = ctypes.c_int
    return lib.cudaDeviceReset()


def matmul_check(torch, tag):
    """真实算子 + 数值校验：分配、填数、matmul、比对。"""
    n = 2048
    try:
        a = torch.full((n, n), 0.001, device="cuda")
        b = torch.full((n, n), 2.0, device="cuda")
        c = a @ b
        got = float(c[0, 0].item())
        want = 0.001 * 2.0 * n
        P("  [%s] matmul 结果 %.4f（期望 %.4f）%s" %
          (tag, got, want, "OK" if abs(got - want) < 0.5 else "!! 数值不对"))
        del a, b, c
    except Exception as e:
        P("  [%s] matmul 失败: %r" % (tag, e))


def main():
    import torch

    P("torch:", torch.__version__, "| CUDA:", getattr(torch.version, "cuda", None),
      "| available:", torch.cuda.is_available())

    P("")
    P("=== 场景 1：empty_cache 之后再 reset ===")
    x = torch.full((64 * 1024 * 1024,), 1.0, device="cuda")  # 256MB
    P("  分配 256MB 并释放（让 allocator cache 里留块）")
    del x
    torch.cuda.empty_cache()
    P("  empty_cache 完成；reset rc=%s" % reset_cuda(torch))
    gc.collect()
    matmul_check(torch, "empty_cache 后 reset")

    P("")
    P("=== 场景 2：不 empty_cache 直接 reset ===")
    y = torch.full((64 * 1024 * 1024,), 1.0, device="cuda")
    del y  # 块留在 allocator cache 里
    gc.collect()
    P("  reset rc=%s（cache 未清）" % reset_cuda(torch))
    matmul_check(torch, "未 empty_cache 就 reset")

    P("")
    P("=== 场景 3：不 reset，正常 empty_cache（对照）===")
    z = torch.full((64 * 1024 * 1024,), 1.0, device="cuda")
    del z
    torch.cuda.empty_cache()
    gc.collect()
    matmul_check(torch, "未 reset 对照")

    P("")
    P("结论行：三段都没抛异常/数值都对 → 本机 torch 13.2 对 reset 有容忍；"
      "反之则说明 reset 会破坏 torch 状态。")
    P("done at %.1fs" % time.time())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        P("!! 异常:", repr(e))
        P(traceback.format_exc())
    _log.close()
