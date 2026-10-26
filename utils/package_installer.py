"""Structured Python package installer backend.

Provides a unified interface for installing Python packages via pip or uv,
with streaming output and automatic backend resolution.

Port of upstream ``ballontranslator.utils.package_installer``, adapted
for the lite launcher's startup dependency management.
"""

import importlib.metadata
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

BACKENDS = ("auto", "pip", "uv")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# Packages whose declared dependencies conflict with this app's pinned
# versions (onnxocr requires numpy<2 while the app ships numpy 2.x) and
# whose actually-needed deps are already covered by requirements.txt.
# Both install paths (``modules/base.py::BaseModule.ensure_dependencies``
# and the GUI download task ``ui/model_downloads.py``) must install these
# bare with ``--no-deps``: letting the resolver "satisfy" the declaration
# would silently downgrade numpy and break everything else.
NO_DEPS_PACKAGES = frozenset({"onnxocr"})


@dataclass
class InstallResult:
    """Structured result from a package install command."""

    ok: bool
    command: List[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    @property
    def command_text(self) -> str:
        return shlex.join(self.command)


def find_uv(env: Optional[dict] = None, python_executable: str = "") -> str:
    """Return a usable ``uv`` executable path, or ``""`` when unavailable.

    Lookup order: the directory holding the target interpreter, then ``PATH``
    interpreter.  The release bundle puts ``uv.exe`` next to ``python.exe``
    inside ``ballontrans_pylibs_win/`` and does **not** add that directory to
    ``PATH`` (``launch.bat`` calls the interpreter by absolute path), so a
    PATH-only lookup would silently fall back to pip — several times slower
    on the ~520 MB core dependency set.
    """
    env = env or os.environ
    exe_dir = os.path.dirname(python_executable or sys.executable)
    if exe_dir:
        for name in ("uv.exe", "uv"):
            candidate = os.path.join(exe_dir, name)
            if os.path.isfile(candidate):
                return candidate
    if "ballontrans_pylibs_win" in exe_dir.lower():
        return ""  # Never borrow an unrelated uv for the bundled interpreter.
    found = shutil.which("uv", path=env.get("PATH"))
    if found:
        return found
    return ""


def resolve_backend(
    backend: str = "auto", env: Optional[dict] = None, python_executable: str = ""
) -> str:
    """Resolve the installer backend.

    ``auto`` picks ``uv`` if one is found (PATH or next to the interpreter),
    otherwise ``pip``.
    """
    if backend != "auto":
        return backend if backend in BACKENDS else "auto"
    return "uv" if find_uv(env, python_executable) else "pip"


def build_install_command(
    requirements: Iterable[str] = (),
    requirements_file: str = "",
    backend: str = "auto",
    extra_args: str = "",
    env: Optional[dict] = None,
    python_executable: str = "",
    constraints_file: str = "",
) -> List[str]:
    """Build a pip/uv install command without ``shell=True``.

    Duplicate requirements are deduplicated while preserving order.
    ``constraints_file`` is passed as ``-c`` (supported by both pip and uv):
    with a constraints file listing ``name==version``, pip resolves new
    packages around the pinned versions — an already-installed package is
    never upgraded; a requirement that would force an upgrade fails instead.

    >>> cmd = build_install_command(['openai>=2.8.1'], backend='pip', python_executable='python')
    >>> cmd[:5]
    ['python', '-m', 'pip', 'install', 'openai>=2.8.1']
    """
    reqs = list(dict.fromkeys(requirements))  # dedup, preserve order
    if requirements_file:
        reqs.extend(["-r", requirements_file])
    if constraints_file:
        reqs.extend(["-c", constraints_file])
    extra = shlex.split(extra_args or "")
    env = env or os.environ
    index_url = env.get("INDEX_URL")
    index_args = ["--index-url", index_url] if index_url else []
    python_executable = python_executable or sys.executable
    resolved_backend = resolve_backend(
        backend, env=env, python_executable=python_executable
    )

    uv_path = find_uv(env, python_executable) if resolved_backend == "uv" else ""
    if resolved_backend == "uv" and uv_path:
        return [
            uv_path,
            "pip",
            "install",
            "--python",
            python_executable,
            *reqs,
            *index_args,
            *extra,
        ]

    # Default: pip
    return [
        python_executable,
        "-m",
        "pip",
        "install",
        *reqs,
        "--prefer-binary",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        *index_args,
        *extra,
    ]


def install(
    requirements: Iterable[str] = (),
    requirements_file: str = "",
    backend: str = "auto",
    extra_args: str = "",
    env: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    constraints_file: str = "",
    additive_only: bool = True,
    verify_imports: bool = False,
) -> InstallResult:
    """Install Python packages and stream installer output to stdout.

    Returns an ``InstallResult`` with ``.ok`` indicating success.
    On Windows the output is captured and re-printed line-by-line
    (PTY is unavailable on Windows).

    ``constraints_file``: optional pip/uv constraints file (``-c``) pinning
    installed versions, used for additive-only installs into an environment
    shared with other projects (see ``utils/core_requirements.py``).
    """
    req_list = list(requirements)
    no_deps = "--no-deps" in shlex.split(extra_args or "")
    # A requirements-file repair must also work when packaging itself is missing.
    parsed = []
    if req_list:
        try:
            from packaging.requirements import Requirement
            parsed = [Requirement(req) for req in req_list]
        except Exception as exc:
            return InstallResult(False, [], returncode=-1, error=f"Invalid requirements: {exc}")
    if no_deps and (requirements_file or not parsed or
                    any(req.name.lower() not in NO_DEPS_PACKAGES for req in parsed)):
        return InstallResult(False, [], returncode=-1,
                             error="--no-deps is reserved for declared exceptions")
    for req in parsed:
        if req.marker and not req.marker.evaluate():
            continue
        name = req.name.lower().replace("_", "-")
        if name == "onnxruntime-gpu" and "ballontrans_pylibs_win" in sys.executable.lower():
            return InstallResult(False, [], returncode=-1,
                                 error="GPU ONNX Runtime must match Torch's CUDA profile. Use a matching complete package or install_cuda.bat --install.")
        other = {"onnxruntime": "onnxruntime-gpu", "onnxruntime-gpu": "onnxruntime"}.get(name)
        if other:
            try:
                importlib.metadata.version(other)
            except importlib.metadata.PackageNotFoundError:
                pass
            else:
                return InstallResult(False, [], returncode=-1,
                                     error="ONNX Runtime CPU/GPU packages overlap. Use a matching complete package or install_cuda.bat.")
    owned_snapshot = ""
    if additive_only and not constraints_file:
        try:
            fd, owned_snapshot = tempfile.mkstemp(prefix="bt-installed-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                from utils.runtime_compatibility import installed_inventory
                versions = installed_inventory()
                for name, version in sorted(versions.items()):
                    stream.write(f"{name}=={version}\n")
            constraints_file = owned_snapshot
        except Exception as exc:
            return InstallResult(False, [], returncode=-1,
                                 error=f"Cannot protect installed packages: {exc}")
    command = build_install_command(
        requirements=req_list,
        requirements_file=requirements_file,
        backend=backend,
        extra_args=extra_args,
        env=env,
        constraints_file=constraints_file,
    )

    if _can_stream_with_pty():
        try:
            returncode, output = _run_with_pty(
                command, env=env, progress_callback=progress_callback
            )
        except Exception as e:
            return InstallResult(False, command, error=str(e), returncode=-1)
    else:
        try:
            process = subprocess.Popen(
                command,
                env=env or os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                bufsize=1,
            )
        except Exception as e:
            return InstallResult(False, command, error=str(e), returncode=-1)
        output = _stream_process_output(process, progress_callback=progress_callback)
        returncode = process.wait()

    verified = returncode == 0
    verification_errors = []
    if verified and verify_imports:
        import_names = {"opencv-python": "cv2", "pillow": "PIL.Image",
                        "pyyaml": "yaml", "onnxruntime-gpu": "onnxruntime",
                        "pillow-jxl-plugin": "pillow_jxl", "pyqt6": "PyQt6.QtCore",
                        "pyqt6-qt6": "PyQt6.QtCore", "pywin32": "win32api",
                        "opencc-python-reimplemented": "opencc", "fonttools": "fontTools"}
        for requirement in parsed:
            if requirement.marker and not requirement.marker.evaluate():
                continue
            name = requirement.name.lower()
            module = import_names.get(name, name.replace("-", "_"))
            try:
                probe = subprocess.run(
                    [sys.executable, "-c", "import importlib,sys; importlib.import_module(sys.argv[1])", module],
                    capture_output=True, text=True, timeout=60,
                )
                failure = probe.returncode != 0
                detail = probe.stderr[-1000:]
            except Exception as exc:
                failure, detail = True, str(exc)
            if failure:
                verified = False
                verification_errors.append(f"Installed {requirement} but cannot import {module}: {detail}")
                output += "\n" + verification_errors[-1]
    if owned_snapshot and verified:
        os.remove(owned_snapshot)
    return InstallResult(
        verified,
        command,
        returncode=returncode if verified else (returncode or 1),
        stdout=output,
        stderr="",
        error="\n".join(verification_errors) or (output[-2000:] if not verified else ""),
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _can_stream_with_pty() -> bool:
    """PTY-based streaming is only available on Unix TTYs."""
    if os.name == "nt":
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    try:
        import pty  # noqa: F401
    except Exception:
        return False
    return True


def _run_with_pty(
    command: List[str],
    env: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> Tuple[int, str]:
    import pty
    import select

    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            command,
            env=env or os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            shell=False,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    captured: List[str] = []
    pending: List[str] = []
    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                captured.append(text)
                print(text, end="", flush=True)
                _feed_progress_text(text, pending, progress_callback)
            elif process.poll() is not None:
                break
    finally:
        os.close(master_fd)
    _emit_progress_message(pending, progress_callback)
    return process.wait(), "".join(captured)


def _stream_process_output(
    process: subprocess.Popen,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> str:
    captured: List[str] = []
    pending: List[str] = []

    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(1)
        if chunk == "":
            if process.poll() is not None:
                break
            continue
        captured.append(chunk)
        print(chunk, end="", flush=True)
        _feed_progress_text(chunk, pending, progress_callback)

    _emit_progress_message(pending, progress_callback)
    return "".join(captured)


def _feed_progress_text(
    text: str,
    pending: List[str],
    progress_callback: Optional[Callable[[dict], None]] = None,
):
    for char in text:
        if char in {"\n", "\r"}:
            _emit_progress_message(pending, progress_callback)
        else:
            pending.append(char)
            if len(pending) >= 200:
                _emit_progress_message(pending, progress_callback)


def _emit_progress_message(
    pending: List[str],
    progress_callback: Optional[Callable[[dict], None]] = None,
):
    if not pending:
        return
    message = ANSI_ESCAPE_RE.sub("", "".join(pending)).strip()
    pending.clear()
    if message and progress_callback is not None:
        progress_callback({"event": "package_output", "message": message})
