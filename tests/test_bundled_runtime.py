"""The bundle must resolve MSVC DLLs locally before model imports."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import bundled_runtime as runtime


class BundledRuntimeTests(unittest.TestCase):
    def test_preloads_absolute_bundle_paths_once_and_retains_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "ballontrans_pylibs_win"
            qt = bundle / "Lib/site-packages/PyQt6/Qt6/bin"
            qt.mkdir(parents=True)
            for name in ("msvcp140.dll", "concrt140.dll"):
                (qt / name).touch()
            with mock.patch.object(runtime.sys, "platform", "win32"), \
                 mock.patch.object(runtime.sys, "executable", str(bundle / "python.exe")), \
                 mock.patch.object(runtime, "_HANDLES", []), \
                 mock.patch.object(runtime, "_LIBRARIES", []), \
                 mock.patch.object(runtime.os, "add_dll_directory", create=True) as add, \
                 mock.patch.object(runtime.ctypes, "WinDLL", create=True) as load:
                runtime.prepare_bundled_dlls()
                runtime.prepare_bundled_dlls()
                add.assert_called_once_with(str(qt))
                self.assertEqual(load.call_args_list, [mock.call(str(qt / "msvcp140.dll")),
                                                       mock.call(str(qt / "concrt140.dll"))])
                self.assertEqual(runtime._HANDLES, [add.return_value])
                self.assertEqual(len(runtime._LIBRARIES), 2)

    def test_does_not_borrow_dlls_for_unrelated_python(self):
        with mock.patch.object(runtime.sys, "platform", "win32"), \
             mock.patch.object(runtime.sys, "executable", str(Path.cwd() / "other/python.exe")), \
             mock.patch.object(runtime, "_HANDLES", []), \
             mock.patch.object(runtime.os, "add_dll_directory", create=True) as add:
            runtime.prepare_bundled_dlls()
            add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
