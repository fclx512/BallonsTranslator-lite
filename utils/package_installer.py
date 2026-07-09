"""Structured Python package installer backend.

Provides a unified interface for installing Python packages via pip or uv,
with streaming output and automatic backend resolution.

Port of upstream ``ballontranslator.utils.package_installer``, adapted
for the lite launcher's startup dependency management.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

BACKENDS = ("auto", "pip", "uv")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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


def resolve_backend(backend: str = "auto", env: Optional[dict] = None) -> str:
    """Resolve the installer backend.

    ``auto`` picks ``uv`` if found on PATH, otherwise ``pip``.
    """
    if backend != "auto":
        return backend if backend in BACKENDS else "auto"
    env = env or os.environ
    if shutil.which("uv", path=env.get("PATH")):
        return "uv"
    return "pip"


def build_install_command(
    requirements: Iterable[str] = (),
    requirements_file: str = "",
    backend: str = "auto",
    extra_args: str = "",
    env: Optional[dict] = None,
    python_executable: str = "",
) -> List[str]:
    """Build a pip/uv install command without ``shell=True``.

    Duplicate requirements are deduplicated while preserving order.

    >>> cmd = build_install_command(['openai>=2.8.1'], backend='pip', python_executable='python')
    >>> cmd[:5]
    ['python', '-m', 'pip', 'install', 'openai>=2.8.1']
    """
    reqs = list(dict.fromkeys(requirements))  # dedup, preserve order
    if requirements_file:
        reqs.extend(["-r", requirements_file])
    extra = shlex.split(extra_args or "")
    env = env or os.environ
    index_url = env.get("INDEX_URL")
    index_args = ["--index-url", index_url] if index_url else []
    python_executable = python_executable or sys.executable
    resolved_backend = resolve_backend(backend, env=env)

    if resolved_backend == "uv":
        return [
            "uv",
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
) -> InstallResult:
    """Install Python packages and stream installer output to stdout.

    Returns an ``InstallResult`` with ``.ok`` indicating success.
    On Windows the output is captured and re-printed line-by-line
    (PTY is unavailable on Windows).
    """
    command = build_install_command(
        requirements=requirements,
        requirements_file=requirements_file,
        backend=backend,
        extra_args=extra_args,
        env=env,
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

    return InstallResult(
        returncode == 0,
        command,
        returncode=returncode,
        stdout=output,
        stderr="",
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
