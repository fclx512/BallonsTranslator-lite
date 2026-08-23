"""Offscreen contract tests for AgentTranslator 单框翻译策略(设计方案 §9)。

覆盖 translate() 入口的分支:
- plain 档单框 → 父类直译路径(_translate),不启动 agent loop;
- context 档单框 → _run_agent_task(block_mode=True):max_turns=2 + 注入当前页其余块;
- 多块任务始终走完整 agent(block_mode=False);
- 配置默认值与坏值校验。

Run from the repo root:

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_agent_single_block.py -q
"""

import os
import os.path as osp
import sys
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402

from modules.translators.trans_agent import AgentTranslator  # noqa: E402
import modules.translators.trans_agent as trans_agent  # noqa: E402
from utils.config import ModuleConfig, SingleBlkTranslateMode, pcfg  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────


class _Blk:
    def __init__(self, src, tr=""):
        self._src = src
        self.translation = tr

    def get_text(self):
        return self._src


def _proj(pages):
    return SimpleNamespace(pages=pages)


@pytest.fixture(autouse=True)
def _clean_pcfg(monkeypatch):
    monkeypatch.setattr(
        pcfg.module, "single_blk_translate_mode", SingleBlkTranslateMode.Plain
    )
    monkeypatch.setattr(pcfg.module, "llm_glossary_path", "")
    monkeypatch.setattr(pcfg.module, "llm_glossary_mode", "matching")


@pytest.fixture
def trans(monkeypatch):
    t = AgentTranslator("日本語", "简体中文")
    # 不触碰真实网络/模型加载:桩掉模型检查与 client/模型解析
    monkeypatch.setattr(t, "all_model_loaded", lambda: True)
    monkeypatch.setattr(t, "_select_api_key", lambda: "test-key")
    monkeypatch.setattr(t, "client", SimpleNamespace(api_key="test-key"))
    monkeypatch.setattr(
        AgentTranslator, "_effective_model", property(lambda self: "test-model")
    )
    return t


# ── translate() 分支 ─────────────────────────────────────────────────


def test_single_block_plain_uses_direct_path(monkeypatch, trans):
    calls = {"direct": 0, "agent": 0}

    def fake_direct(src_list, **kw):
        calls["direct"] += 1
        return ["你好"]

    def fake_agent(*a, **k):
        calls["agent"] += 1
        return {1: "x"}

    monkeypatch.setattr(trans, "_translate", fake_direct)
    monkeypatch.setattr(trans, "_run_agent_task", fake_agent)

    result = trans.translate(["こんにちは"], project=_proj({}), page_key="p1")
    assert result == ["你好"]
    assert calls["direct"] == 1
    assert calls["agent"] == 0


def test_single_block_plain_ignores_project_context(monkeypatch, trans):
    # plain 档显式不带页面上下文:直译路径收到的 project/page_key 均为 None
    seen = {}

    def fake_direct(src_list, **kw):
        seen["project"] = kw.get("project")
        seen["page_key"] = kw.get("page_key")
        return ["你好"]

    monkeypatch.setattr(trans, "_translate", fake_direct)
    result = trans.translate(
        ["こんにちは"], project=_proj({"p1": []}), page_key="p1"
    )
    assert result == ["你好"]
    assert seen["project"] is None
    assert seen["page_key"] is None


def test_single_block_context_runs_agent(monkeypatch, trans):
    calls = {}

    def fake_task(src_list, *, project=None, page_key=None, block_mode=False):
        calls["src_list"] = list(src_list)
        calls["block_mode"] = block_mode
        return {1: "你好"}

    monkeypatch.setattr(trans, "_run_agent_task", fake_task)
    monkeypatch.setattr(
        pcfg.module, "single_blk_translate_mode", SingleBlkTranslateMode.Context
    )
    result = trans.translate(["こんにちは"], project=_proj({}), page_key="p1")
    assert result == ["你好"]
    assert calls["src_list"] == ["こんにちは"]
    assert calls["block_mode"] is True


def test_multi_block_always_full_agent(monkeypatch, trans):
    calls = {}

    def fake_task(src_list, *, project=None, page_key=None, block_mode=False):
        calls["block_mode"] = block_mode
        return {i + 1: f"t{i}" for i in range(len(src_list))}

    monkeypatch.setattr(trans, "_run_agent_task", fake_task)
    monkeypatch.setattr(
        pcfg.module, "single_blk_translate_mode", SingleBlkTranslateMode.Context
    )
    result = trans.translate(
        ["こんにちは", "世界"], project=_proj({}), page_key="p1"
    )
    assert result == ["t0", "t1"]
    assert calls["block_mode"] is False


def test_context_mode_caps_turns_and_injects_page(monkeypatch, trans):
    pages = {
        "p1": [_Blk("コマ１", "画面1")],
        "p2": [_Blk("こんにちは"), _Blk("世界", "世　界")],
    }
    captured = {}

    def fake_run(chat, execute, src_list, **kw):
        captured["max_turns"] = kw["max_turns"]
        captured["user_message"] = kw["user_message"]
        return {1: "你好"}

    monkeypatch.setattr(trans_agent, "run_agent_task", fake_run)
    monkeypatch.setattr(
        pcfg.module, "single_blk_translate_mode", SingleBlkTranslateMode.Context
    )
    result = trans.translate(["こんにちは"], project=_proj(pages), page_key="p2")
    assert result == ["你好"]
    assert captured["max_turns"] == 2
    ctx_section = captured["user_message"].split(
        "Other text blocks on the current page", 1
    )[1]
    assert "世 界" in ctx_section  # _one_line 归一空格
    assert "こんにちは" not in ctx_section  # 任务块自身不重复进上下文
    assert "コマ１" not in ctx_section  # 仅当前页,不串邻近页


def test_full_agent_keeps_configured_turns(monkeypatch, trans):
    captured = {}

    def fake_run(chat, execute, src_list, **kw):
        captured["max_turns"] = kw["max_turns"]
        return {1: "a", 2: "b"}

    monkeypatch.setattr(trans_agent, "run_agent_task", fake_run)
    result = trans.translate(
        ["こんにちは", "世界"], project=_proj({}), page_key="p1"
    )
    assert result == ["a", "b"]
    assert captured["max_turns"] == int(trans.get_param_value("agent_max_turns"))


# ── config ───────────────────────────────────────────────────────────


def test_config_default_and_validation():
    cfg = ModuleConfig()
    assert cfg.single_blk_translate_mode == SingleBlkTranslateMode.Plain
    cfg.single_blk_translate_mode = "garbage"
    cfg.__post_init__()
    assert cfg.single_blk_translate_mode == SingleBlkTranslateMode.Plain
    cfg.single_blk_translate_mode = SingleBlkTranslateMode.Context
    cfg.__post_init__()
    assert cfg.single_blk_translate_mode == SingleBlkTranslateMode.Context


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failures else 0)