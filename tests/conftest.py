"""Install configuration isolation before pytest imports any test modules."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.test_config_sandbox import install_config_sandbox  # noqa: E402

install_config_sandbox()
