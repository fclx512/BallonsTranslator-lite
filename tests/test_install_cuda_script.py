"""Real cmd wrapper tests in scratch directories. No pip or network calls."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

@unittest.skipUnless(sys.platform == 'win32', 'Windows batch behavior')
class BundledWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bt-cuda-bat-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / 'install_cuda.bat', self.root / 'install_cuda.bat')

    def run_bat(self, *args):
        return subprocess.run(['cmd', '/d', '/c', 'install_cuda.bat', *args],
            cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=20)

    def test_missing_bundle_no_system_fallback(self):
        result = self.run_bat()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing the matching complete package', result.stdout)
        self.assertNotIn('pip install', result.stdout)

    def prepare_stub(self):
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J',
            str(self.root / 'ballontrans_pylibs_win'), str(Path(sys.executable).parent)],
            check=True, capture_output=True)
        self.addCleanup(lambda: os.rmdir(self.root / 'ballontrans_pylibs_win'))
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/install_cuda_bundle.py').write_text(
            "import sys,os,json\nprint(json.dumps([sys.argv[1:],os.environ.get('PYTHONPATH'),os.environ.get('PYTHONHOME')]))\nsys.exit(7)\n", encoding='utf-8')

    def test_flags_and_exit_status(self):
        self.prepare_stub()
        for args, expected in (((), []), (('--check',), []), (('--install',), ['--install']),
                               (('--replace',), ['--install']), (('--json',), ['--json'])):
            with self.subTest(args=args):
                result = self.run_bat(*args)
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout.strip()), [expected, None, None])

    def test_unknown_argument(self):
        self.prepare_stub()
        self.assertEqual(self.run_bat('--manual').returncode, 2)

if __name__ == '__main__':
    unittest.main()
