"""Use the existing bundle's MSVC DLLs before importing Qt or model libraries."""

import ctypes
import os
import sys
from pathlib import Path

_HANDLES = []
_LIBRARIES = []


def prepare_bundled_dlls():
    """Preload bundled C++ runtimes, retaining loader handles for this process.

    Python's own vcruntime is already loaded beside python.exe. Do not replace
    it or borrow a different Python's runtime. Qt ships the remaining MSVC
    components needed by torch/OpenCV; prefer their absolute local paths.
    """
    if sys.platform != "win32" or _HANDLES:
        return
    bundle = Path(sys.executable).resolve().parent
    if bundle.name.lower() != "ballontrans_pylibs_win":
        return
    qt_bin = bundle / "Lib/site-packages/PyQt6/Qt6/bin"
    if not qt_bin.is_dir():
        return  # Core-requirement checks explain an incomplete bundle.
    _HANDLES.append(os.add_dll_directory(str(qt_bin)))
    for name in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                 "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll"):
        path = qt_bin / name
        if path.is_file():
            _LIBRARIES.append(ctypes.WinDLL(str(path)))
