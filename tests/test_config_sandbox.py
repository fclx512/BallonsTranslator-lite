"""Exercise the installed guard without attempting real destructive writes."""

import json
import os
import sys
from pathlib import Path

import pytest

from utils import shared


def test_default_config_and_style_writes_use_the_sandbox():
    from utils import config, global_styles
    from utils.fontformat import FontFormat

    real = Path(shared.PROGRAM_PATH) / "config"
    for name in ("CONFIG_PATH", "GLOBAL_STYLES_PATH", "CUSTOM_THEME_PATH", "DEFAULT_TEXTSTYLE_DIR"):
        assert not Path(getattr(shared, name)).is_relative_to(real)
    assert config.save_config() is True
    assert Path(shared.CONFIG_PATH).is_file()
    previous = list(global_styles.global_styles)
    try:
        global_styles.global_styles.clear()
        global_styles.add_style("sandbox style", FontFormat())
        data = json.loads(Path(shared.GLOBAL_STYLES_PATH).read_text(encoding="utf-8"))
        assert [entry["name"] for entry in data] == ["sandbox style"]
    finally:
        global_styles.global_styles[:] = previous


@pytest.mark.parametrize("relative", ["config.json", "global_styles.json", "global_styles.json.tmp", "textstyles/probe.json"])
def test_explicit_real_config_writes_are_rejected(relative):
    # sys.audit emits the same event as open(), but never opens a file even
    # if a regression removes the hook. A failing guard cannot damage data.
    path = str(Path(shared.PROGRAM_PATH) / "config" / relative)
    with pytest.raises(PermissionError, match="real config directory"):
        sys.audit("open", path, "w", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)


def test_real_config_replace_and_delete_are_rejected(tmp_path):
    real = str(Path(shared.PROGRAM_PATH) / "config/global_styles.json")
    with pytest.raises(PermissionError, match="real config directory"):
        sys.audit("os.rename", str(tmp_path / "replacement.json"), real, -1, -1)
    with pytest.raises(PermissionError, match="real config directory"):
        sys.audit("os.remove", real, -1)


def test_verify_rejects_config_changes_even_when_timestamp_is_preserved(tmp_path, monkeypatch):
    from scripts import verify

    monkeypatch.setattr(verify, "ROOT", tmp_path)
    directory = tmp_path / "config"
    directory.mkdir()
    config = directory / "config.json"
    config.write_text("before", encoding="utf-8")
    before = verify._config_snapshot()
    stamp = config.stat()
    config.write_text("after", encoding="utf-8")
    os.utime(config, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert verify._check_config_unchanged(before) is False
