"""沙箱替身：以真 python.exe 为宿主，用 sitecustomize 拦截所有探测。

为什么这么绕
------------
`install_cuda.bat` 会调用 ``ballontrans_pylibs_win\\python.exe``。沙箱要驱动
脚本全部分支，就必须让这个路径上有一个**真可执行文件**：

- 放 .bat 不行 —— 脚本里带重定向的那条 ORT 探测没写 `call`，直接调 .bat
  会交出控制权、后续语句不再执行（沙箱假象，不是脚本缺陷）。
- 本机没有任何 C/C++ 编译器（gcc / cl / tcc 全无，csc 被策略拦下），
  没法把替身编成 exe。

因此采用：**复制宿主解释器的 python.exe + 同目录 DLL**，再放一个
``sitecustomize.py``。解释器启动时自动 import 它，由它读 BT_* 环境变量、
按 ``sys.argv`` 里的探测语句打印结果并 ``os._exit``，从而在脚本看来
「就是一个按预期回话的 python」。

真脚本、真环境完全不受影响 —— 沙箱只在自己的临时目录里动土。

宿主布局差异（标准安装 / 嵌入式一键包）由 ``host_base_paths`` 兜掉：
嵌入式没有 ``Lib/stdlib``，照搬 ``<home>/Lib`` 会让替身死在启动阶段。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
INSTALL_SCRIPT = REPO_ROOT / "install_cuda.bat"


@dataclass
class StubConfig:
    """替身的「环境快照」—— 决定每条探测语句回什么。"""

    torch_version: str = "2.13.0+cpu"
    """装 torch 探测回的版本；空串表示没装 torch（import 失败）。"""

    torch_version_after_install: Optional[str] = None
    """执行过 pip 之后 torch 变成什么版本，用于验降级保护。
    None 表示与初始相同。"""

    ort_providers: str = "CPUExecutionProvider"
    """逗号分隔的 provider 串。空串表示 import onnxruntime 失败。"""

    compute_cap: str = "12.0"
    """nvidia-smi 报的计算能力；空串表示无 NVIDIA GPU。"""

    pip_exit: int = 0
    """pip 调用的退出码；非 0 用于验失败分支。"""

    downgrade_result: str = "0"
    """降级判据语句的返回值：1 = 确实降级了。"""


class SandboxUnavailable(RuntimeError):
    """沙箱自己起不来（宿主解释器布局不受支持）。

    与「脚本行为不符预期」严格区分：前者是回归台缺零件，后者才是缺陷。
    """


def host_interpreter_files() -> list[Path]:
    """返回复制 python.exe 所需的同目录文件（exe + DLL）。"""
    exe = Path(sys.executable)
    home = exe.parent
    needed = [exe]
    for name in ("python3.dll", "python313.dll", "python312.dll", "python311.dll",
                 "python310.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        p = home / name
        if p.exists():
            needed.append(p)
    # 版本号可能不是上面列的，兜底扫一遍 python3*.dll
    for p in home.glob("python3*.dll"):
        if p not in needed:
            needed.append(p)
    return needed


def versioned_dll_stem(home: Path) -> str:
    """宿主版本化 DLL 的主干名（``python312`` / ``python313``）。

    解释器只认与 DLL 同名的 ``._pth``，所以这个名字不能猜版本号写死；
    扫不到时退回按 ``sys.version_info`` 推导。
    """
    want = f"python{sys.version_info.major}{sys.version_info.minor}"
    for p in sorted(home.glob("python3*.dll")):
        if p.stem == want:
            return p.stem
    return want


def host_base_paths(home: Path) -> list[str]:
    """宿主解释器启动所需的 base 路径（stdlib 所在处）。

    **不能假定 ``<home>/Lib``**：嵌入式布局（本仓一键包
    ``ballontrans_pylibs_win`` 就是）没有 ``Lib/stdlib``，stdlib 打在
    ``python3XX.zip`` 里，而 ``<home>/Lib`` 只放 ``site-packages``——
    照搬 ``<home>/Lib`` 会让替身死在 ``init_fs_encoding``
    （``No module named 'encodings'``），脚本那边看到的是「CC 探测为空」，
    一屏看不懂的断言失败。

    因此优先照抄宿主自己的 ``<home>/python3XX._pth``：那是宿主已经
    验证过能启动的路径集合，比任何猜测都可靠。
    """
    stem = versioned_dll_stem(home)
    pth = home / f"{stem}._pth"
    if pth.exists():
        paths: list[str] = []
        for raw in pth.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("import "):
                continue
            p = Path(line)
            if not p.is_absolute():          # ._pth 里的相对路径以它自己为基准
                p = home / line
            paths.append(str(p))
        # 至少一条真的存在才认账，避免照抄到垃圾行
        if paths and any(Path(p).exists() for p in paths):
            return paths

    lib = home / "Lib"
    if (lib / "encodings").is_dir():         # 标准安装布局
        return [str(lib)]

    # 嵌入式但 ._pth 缺失：zip + 自身目录（DLL/.pyd 都在后者）
    return [str(p) for p in home.glob("python3*.zip")] + [str(home)]


def install_fake_python(target_dir: Path) -> Path:
    """在 target_dir 下造一个替身 python.exe，返回其路径。

    同时写入 ``_pth``（指向宿主 stdlib）与 ``sitecustomize.py``（拦截逻辑）。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in host_interpreter_files():
        shutil.copy2(src, target_dir / src.name)

    # 复制出来的 exe 不知道 stdlib 在哪，必须用 ._pth 显式告诉它。
    # 文件名必须与 DLL 同名（python312._pth），否则解释器不读它。
    # `import site` 让 sitecustomize.py 有机会被自动导入。
    home = Path(sys.executable).parent
    pth_stem = versioned_dll_stem(home)
    # 沙箱目录排在前面，保证 import sitecustomize 拿到我们这份，
    # 而不是宿主自带的同名文件（若存在）。
    lines = [str(target_dir), *host_base_paths(home), "import site"]
    (target_dir / f"{pth_stem}._pth").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (target_dir / "sitecustomize.py").write_text(_HOOK, encoding="utf-8")
    return target_dir / Path(sys.executable).name


# sitecustomize 拦截逻辑：按 BT_* 回话，然后立刻退出。
_HOOK = r'''"""沙箱替身钩子：按 BT_* 环境变量回话，模拟一个 python 解释器。

必须在 site 阶段就退出，且只能用 os._exit —— sys.exit 会抛 SystemExit，
在 site 初始化中途抛出会被解释器当成致命错误。
"""
import os
import sys


def _rec(path, text):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _finish(code, text=None):
    if text is not None:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except Exception:
            pass
    os._exit(code)


def _main():
    # 启动自检（Sandbox.__init__ 用）：只证明解释器活着，不留任何日志痕迹。
    if os.environ.get("BT_PROBE"):
        _finish(0)
    sandbox = os.environ.get("BT_SANDBOX", ".")
    # site 阶段 sys.argv 还没填好（Python 3.8+ 才有 orig_argv），
    # 因此优先用 orig_argv；再退回 argv；最后退回命令行。
    argv = getattr(sys, "orig_argv", None) or sys.argv or []
    args = " ".join(argv[1:])
    if not args.strip():
        args = " ".join(sys.argv[1:])
    _rec(os.path.join(sandbox, "all_calls.log"), args)

    torch = os.environ.get("BT_TORCH", "")
    after = os.environ.get("BT_AFTER", torch)
    ort = os.environ.get("BT_ORT", "")
    cc = os.environ.get("BT_CC", "")
    pip_exit = os.environ.get("BT_PIP_EXIT", "")
    down = os.environ.get("BT_DOWNGRADED", "0")
    pip_ran = os.path.exists(os.path.join(sandbox, "pip_ran"))

    if "pip" in args:
        _rec(os.path.join(sandbox, "pip_calls.log"), args)
        open(os.path.join(sandbox, "pip_ran"), "w").close()
        _finish(int(pip_exit) if pip_exit else 0)

    if "a>b" in args:
        _finish(0, down)

    if "torch.__version__" in args:
        if pip_ran:
            _finish(0, after)
        if not torch:
            _finish(1)
        _finish(0, torch)

    if "get_available_providers" in args:
        if not ort:
            _finish(1)
        if "GOOD" in args:
            _finish(0, "GOOD" if "CUDAExecutionProvider" in ort else "CPU")
        _finish(0, ort)

    if "compute_cap" in args:
        _finish(0, cc if cc else None)

    _finish(0)


_main()
'''


class Sandbox:
    """造一个临时项目根，内含替身 python，可直接跑 install_cuda.bat。"""

    def __init__(self, cfg, with_bundle: bool = True):
        self.cfg = cfg
        self.root = Path(tempfile.mkdtemp(prefix="bt_cuda_sandbox_"))
        shutil.copy2(INSTALL_SCRIPT, self.root / "install_cuda.bat")
        if with_bundle:
            pydir = self.root / "ballontrans_pylibs_win"
            try:
                stub = install_fake_python(pydir)
                self._assert_stub_starts(stub)
            except BaseException:
                self.cleanup()
                raise

    @staticmethod
    def _assert_stub_starts(stub: Path) -> None:
        """替身必须真能启动；起不来就在这里报「沙箱不可用」。

        替身起不来（stdlib 路径解析错、DLL 缺失）时，脚本看到的只是
        「探测回空」——表症是十几个 `'' != 'cu132'` 断言失败，看起来
        像脚本坏了。那是回归台缺零件，必须当场说清楚。
        """
        import subprocess

        env = dict(os.environ)
        env["BT_PROBE"] = "1"
        proc = subprocess.run(
            [str(stub), "-c", "probe"], capture_output=True, env=env, timeout=60
        )
        if proc.returncode == 0:
            return
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SandboxUnavailable(
            "沙箱替身解释器无法启动，回归台在当前宿主 Python 下不可用。\n"
            f"  宿主解释器 : {sys.executable}\n"
            f"  替身路径   : {stub}\n"
            f"  退出码     : {proc.returncode}\n"
            f"  解释器 stderr:\n{err}\n"
            "这属于 tests/cuda_sandbox.py 的布局适配问题，不是 install_cuda.bat 缺陷。"
        )

    def run(self, args: tuple = ()):
        import subprocess

        env = dict(os.environ)
        env.update(
            {
                "BT_SANDBOX": str(self.root),
                "BT_TORCH": self.cfg.torch_version,
                "BT_AFTER": (
                    self.cfg.torch_version_after_install
                    if self.cfg.torch_version_after_install is not None
                    else self.cfg.torch_version
                ),
                "BT_ORT": self.cfg.ort_providers,
                "BT_CC": self.cfg.compute_cap,
                "BT_PIP_EXIT": str(self.cfg.pip_exit),
                "BT_DOWNGRADED": self.cfg.downgrade_result,
            }
        )
        proc = subprocess.run(
            ["cmd", "/c", str(self.root / "install_cuda.bat"), *args],
            capture_output=True,
            cwd=str(self.root),
            env=env,
            timeout=180,
            input=b"\r\n",
        )
        proc.stdout_text = proc.stdout.decode("utf-8", errors="replace")
        proc.stderr_text = proc.stderr.decode("utf-8", errors="replace")
        return proc

    def pip_calls(self) -> list[str]:
        f = self.root / "pip_calls.log"
        if not f.exists():
            return []
        return [ln.strip() for ln in f.read_text(
            encoding="utf-8", errors="replace").splitlines() if ln.strip()]

    def all_calls(self) -> list[str]:
        f = self.root / "all_calls.log"
        if not f.exists():
            return []
        return [ln.strip() for ln in f.read_text(
            encoding="utf-8", errors="replace").splitlines() if ln.strip()]

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False
