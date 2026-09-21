"""CUDA 环境与索引的 L3 真环境体检台（只读，不安装任何东西）。

这是 `tests/test_cuda_install_env.py` 三层测试里的第三层。L1 静态、L2 离网
模拟都不联网也不碰真解释器；本脚本反过来 —— 它只做真机上的**观察**，用来
回答两类 L1/L2 答不了的问题：

  1. **某个 CUDA 索引现在还活着吗？**（cu124 就是悄悄 EOL 的 —— 索引不会
     发公告，只是版本列表停止增长。）
  2. **本机/某个解释器现在到底是什么状态？** torch 是 CPU 还是 CUDA 版、
     onnxruntime 是哪个发行名、有没有残留的混合状态。

设计原则：**只读**。不 pip install、不 pip uninstall、不写任何被检查的目录。
唯一的写操作是 `--cache` 指定的临时缓存文件（默认写在系统临时目录）。

用法：

    python scripts/check_cuda_env.py                  # 全量：索引存活 + 本机环境
    python scripts/check_cuda_env.py --indexes        # 只查索引存活
    python scripts/check_cuda_env.py --env            # 只查本机环境
    python scripts/check_cuda_env.py --python <path>  # 指定解释器（可多次）
    python scripts/check_cuda_env.py --json           # 机器可读输出

退出码：0 = 全部符合预期；1 = 有索引 EOL 或环境状态异常。

`--python` 的典型用法 —— 借用用户机器上已有的依赖包做「变更前」快照，
确认脚本会把它识别成 CPU 版并给出正确的目标索引：

    python scripts/check_cuda_env.py --python "D:/某个/ballontrans_pylibs_win/python.exe"

注意：被 --python 指向的解释器**只会被执行只读命令**（import 后打印版本），
不会安装、卸载或写入任何东西。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ----------------------------------------------------------------------
# 索引存活检查
# ----------------------------------------------------------------------

# 索引 → 期望仍可安装的最低 torch 版本。索引 EOL 时最高版本会停止增长，
# 因此判据是「索引里还存在 >= 期望最低版」的 wheel。
INDEXES = {
    "cu118": "2.0.0",
    "cu126": "2.6.0",
    "cu128": "2.7.0",
    "cu130": "2.9.0",
    "cu132": "2.12.0",
}

# 已 EOL 的稳定索引：还能打开、还能装，只是**版本停滞**在某个上限。
# 判据不能用「索引为空」—— EOL 的索引依然列着一堆老 wheel，真正的信号是
# 「最高版本不再增长」。这里记下实测上限，版本超过它说明索引复活了。
EOL_INDEXES = {
    "cu124": "2.6.0",
}

# 明确不该被当作稳定安装源引用的索引（nightly 是每晚构建，不是稳定通道；
# 它能打开、能装，但版本会漂移，不适合让普通用户照抄）。
DISCOURAGED_INDEXES = {
    "nightly/cu128": "nightly 是每晚构建通道，版本会漂移，不面向普通用户",
}

INDEX_URL = "https://download.pytorch.org/whl/{tag}/torch/"


def _fetch_index(tag: str, timeout: int = 30, cache_dir: Path | None = None):
    """抓取索引页并解析出所有 torch 版本号。只读网络操作。"""
    url = INDEX_URL.format(tag=tag)
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / (tag.replace("/", "_") + ".html")

    html = None
    if cache_file is not None and cache_file.exists():
        try:
            html = cache_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            html = None

    if html is None:
        req = urllib.request.Request(url, headers={"User-Agent": "bt-lite-checker"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"tag": tag, "error": str(exc), "versions": []}
        if cache_file is not None:
            try:
                cache_file.write_text(html, encoding="utf-8")
            except OSError:
                pass

    # 形如 torch-2.13.0+cu132-cp313-cp313-win_amd64.whl
    versions = set()
    for m in re.finditer(r"torch-(\d+\.\d+\.\d+)\+", html):
        versions.add(m.group(1))
    if not versions:
        for m in re.finditer(r"torch-(\d+\.\d+\.\d+)", html):
            versions.add(m.group(1))

    def key(v):
        return tuple(int(x) for x in v.split("."))

    return {"tag": tag, "error": None, "versions": sorted(versions, key=key)}


def check_indexes(cache_dir: Path | None = None) -> list[dict]:
    results = []
    for tag, floor in INDEXES.items():
        info = _fetch_index(tag, cache_dir=cache_dir)
        vs = info["versions"]
        if info["error"]:
            info["status"] = "unreachable"
        elif not vs:
            info["status"] = "empty"
        elif key_ge(vs[-1], floor):
            info["status"] = "alive"
        else:
            info["status"] = "eol"
        info["floor"] = floor
        info["latest"] = vs[-1] if vs else ""
        results.append(info)

    for tag, ceiling in EOL_INDEXES.items():
        info = _fetch_index(tag, cache_dir=cache_dir)
        vs = info["versions"]
        info["ceiling"] = ceiling
        info["latest"] = vs[-1] if vs else ""
        if info["error"]:
            info["status"] = "unreachable"
        elif not vs:
            # 索引被整个摘掉了 —— 也算 EOL，只是形态更彻底
            info["status"] = "eol"
        elif key_ge(vs[-1], ceiling) and vs[-1] != ceiling:
            # 版本超过实测上限 → 索引又被更新了，EOL 判定需要复核
            info["status"] = "revived"
        else:
            info["status"] = "eol"
        results.append(info)

    for tag, why in DISCOURAGED_INDEXES.items():
        info = _fetch_index(tag, cache_dir=cache_dir)
        vs = info["versions"]
        info["latest"] = vs[-1] if vs else ""
        info["why"] = why
        info["status"] = "discouraged" if vs else "unreachable"
        results.append(info)
    return results


def key_ge(a: str, b: str) -> bool:
    pa = tuple(int(x) for x in a.split("."))
    pb = tuple(int(x) for x in b.split("."))
    return pa >= pb


# ----------------------------------------------------------------------
# 解释器环境检查（只读）
# ----------------------------------------------------------------------

_ENV_PROBE = r"""
import json, sys
out = {"executable": sys.executable, "python": sys.version.split()[0]}
try:
    import torch
    out["torch"] = torch.__version__
    out["torch_cuda_build"] = bool(getattr(torch.version, "cuda", None))
    out["torch_cuda_available"] = bool(torch.cuda.is_available())
except Exception as exc:
    out["torch"] = None
    out["torch_error"] = f"{type(exc).__name__}: {exc}"
try:
    import onnxruntime
    out["ort_version"] = onnxruntime.__version__
    out["ort_providers"] = list(onnxruntime.get_available_providers())
except Exception as exc:
    out["ort_version"] = None
    out["ort_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""


def probe_interpreter(python_exe: Path | str, timeout: int = 120) -> dict:
    """执行只读探针：import 后打印版本。绝不安装/卸载任何包。"""
    try:
        p = subprocess.run(
            [str(python_exe), "-c", _ENV_PROBE],
            capture_output=True, timeout=timeout,
            # 子进程 stdout 是管道，Windows 上 Python 会退回 cp936；显式指定
            # utf-8 才能让含中文路径/输出的报告正常解码。
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"executable": str(python_exe), "error": str(exc)}
    raw = p.stdout.decode("utf-8", errors="replace").strip()
    line = raw.splitlines()[-1] if raw else ""
    try:
        return json.loads(line)
    except ValueError:
        return {
            "executable": str(python_exe),
            "error": "probe returned no JSON",
            "stderr": p.stderr.decode("utf-8", errors="replace")[-400:],
        }


def dist_info_names(python_exe: Path | str) -> list[str]:
    """列出 site-packages 里的 dist-info，用来发现 onnxruntime 混合状态。

    onnxruntime 与 onnxruntime-gpu 共用同一 import 名与 capi DLL，各自留一份
    dist-info；两份同时存在就是「一个盖在另一个上面」的混合状态。
    """
    py = Path(python_exe)
    # 嵌入式布局：<root>/python.exe + <root>/Lib/site-packages
    sp = py.parent / "Lib" / "site-packages"
    if not sp.is_dir():
        return []
    return sorted(
        d.name for d in sp.iterdir()
        if d.is_dir() and d.name.endswith(".dist-info")
        and ("onnx" in d.name.lower() or "torch" in d.name.lower())
    )


def classify_env(info: dict) -> dict:
    """把探针结果归类，给出「脚本会怎么判」以及建议的目标索引。"""
    verdict = {"torch_kind": "unknown", "ort_kind": "unknown", "notes": []}
    tv = info.get("torch")
    if tv is None:
        verdict["torch_kind"] = "missing"
        verdict["notes"].append("没装 torch")
    elif "+cu" in tv:
        verdict["torch_kind"] = "cuda"
    elif "+cpu" in tv:
        verdict["torch_kind"] = "cpu"
        verdict["notes"].append(
            "CPU 版 torch —— install_cuda.bat 会把它换成 CUDA 版"
        )
    else:
        verdict["torch_kind"] = "cpu-or-unknown"

    providers = info.get("ort_providers") or []
    if info.get("ort_version") is None:
        verdict["ort_kind"] = "missing"
        verdict["notes"].append("没装 onnxruntime")
    elif "CUDAExecutionProvider" in providers:
        verdict["ort_kind"] = "good"
    elif "CPUExecutionProvider" in providers or "AzureExecutionProvider" in providers:
        verdict["ort_kind"] = "cpu"
        verdict["notes"].append(
            "CPU 版 onnxruntime —— 脚本会双卸载后装 onnxruntime-gpu"
        )
    else:
        verdict["ort_kind"] = "odd"
        verdict["notes"].append("providers 不含 CPU/CUDA，状态可疑")

    # 混合状态：两份 dist-info 同时存在
    names = info.get("_dist_info", [])
    has_cpu = any(n.startswith("onnxruntime-") for n in names)
    has_gpu = any(n.startswith("onnxruntime_gpu-") for n in names)
    if has_cpu and has_gpu:
        verdict["ort_kind"] = "mixed"
        verdict["notes"].append(
            "onnxruntime 与 onnxruntime-gpu 两份 dist-info 同时存在"
            "（混合状态，必须双卸载才能清干净）"
        )
    return verdict


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------


def render_index_report(results: list[dict]) -> tuple[str, bool]:
    lines = ["", "== PyTorch 索引存活 ==", ""]
    ok = True
    for r in results:
        tag = r["tag"]
        st = r["status"]
        if st == "discouraged":
            lines.append(f"  [--] {tag:<14} 可访问（最新 {r['latest']}），"
                         f"但不作稳定源推荐：{r['why']}")
        elif st == "revived":
            lines.append(f"  [!] {tag:<14} 版本已超过实测上限 {r['ceiling']}"
                         f"（现为 {r['latest']}）—— 之前判定 EOL，需复核")
        elif st == "eol":
            lines.append(f"  [ok] {tag:<14} 已 EOL（上限 {r['ceiling']}，"
                         f"现为 {r['latest']}）—— 预期如此，脚本不得引用")
        elif st == "alive":
            lines.append(f"  [ok] {tag:<14} 存活，最新 torch {r['latest']}"
                         f"（下限 {r['floor']}）")
        elif st == "unreachable":
            ok = False
            lines.append(f"  [!!] {tag:<14} 抓取失败：{r['error']}")
        else:
            ok = False
            lines.append(f"  [!!] {tag:<14} 索引为空")
    return "\n".join(lines), ok


def render_env_report(entries: list[dict]) -> tuple[str, bool]:
    lines = ["", "== 解释器环境（只读探测） ==", ""]
    ok = True
    for e in entries:
        lines.append(f"  {e.get('executable')}")
        if e.get("error"):
            ok = False
            lines.append(f"    [!!] 探测失败：{e['error']}")
            continue
        lines.append(f"    python      {e.get('python')}")
        lines.append(f"    torch       {e.get('torch')}"
                     f"   (CUDA build: {e.get('torch_cuda_build')},"
                     f" available: {e.get('torch_cuda_available')})")
        lines.append(f"    onnxruntime {e.get('ort_version')}")
        lines.append(f"    providers   {e.get('ort_providers')}")
        names = e.get("_dist_info") or []
        if names:
            lines.append(f"    dist-info   {names}")
        v = e.get("_verdict") or {}
        for note in v.get("notes", []):
            lines.append(f"    -> {note}")
        lines.append("")
    return "\n".join(lines), ok


def main(argv=None) -> int:
    # 报告里含中文路径与中文说明；Windows 控制台默认 cp936，重定向到文件时
    # 会变成乱码。这里显式把本进程 stdout 切成 utf-8（不改环境）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="CUDA 环境与索引真环境体检（只读，不安装任何东西）",
    )
    ap.add_argument("--indexes", action="store_true", help="只查索引存活")
    ap.add_argument("--env", action="store_true", help="只查解释器环境")
    ap.add_argument("--python", action="append", default=[],
                    help="要探测的解释器路径（可重复；只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--cache", default=None,
                    help="索引页缓存目录（默认系统临时目录下的 bt_cuda_idx_cache）")
    ap.add_argument("--no-cache", action="store_true", help="不使用缓存，强制联网")
    args = ap.parse_args(argv)

    do_indexes = args.indexes or not args.env
    do_env = args.env or not args.indexes

    cache_dir = None
    if do_indexes and not args.no_cache:
        cache_dir = Path(args.cache) if args.cache else \
            Path(tempfile.gettempdir()) / "bt_cuda_idx_cache"

    report = {"indexes": [], "env": []}
    all_ok = True
    text_parts = []

    if do_indexes:
        report["indexes"] = check_indexes(cache_dir=cache_dir)
        chunk, ok = render_index_report(report["indexes"])
        text_parts.append(chunk)
        all_ok = all_ok and ok

    if do_env:
        targets = [Path(p) for p in args.python]
        # 默认也看一眼本仓的一键包（如果存在）
        for cand in REPO_ROOT.glob("ballontrans_pylibs_win*/python.exe"):
            if cand not in targets:
                targets.append(cand)
        if not targets:
            text_parts.append(
                "\n== 解释器环境 ==\n\n  （未指定 --python，也没找到本仓一键包）\n"
            )
        else:
            entries = []
            for t in targets:
                if not Path(t).exists():
                    entries.append({"executable": str(t),
                                    "error": "路径不存在"})
                    all_ok = False
                    continue
                info = probe_interpreter(t)
                info["_dist_info"] = dist_info_names(t)
                info["_verdict"] = classify_env(info)
                entries.append(info)
            report["env"] = entries
            chunk, ok = render_env_report(entries)
            text_parts.append(chunk)
            all_ok = all_ok and ok

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n".join(text_parts))
        print("")
        print("结论：" + ("全部符合预期" if all_ok else "存在需要处理的问题"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
