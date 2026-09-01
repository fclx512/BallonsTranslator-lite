"""Offscreen contract tests for 剧情注入管线(工作台阶段 3)。

覆盖:
- build_system_message 的全局梗概 system 段(有/无、与 profile prompt 共存);
- effective_history_budget:梗概为强制注入项,可驱逐可选历史页,不限额(<=0)原样;
- modules/context_agent/story.py::project_synopsis 只读消费(缺失/非 str 安全);
- ProjImgTrans.llm_compact_memory 持久化(to_dict/load_from_dict 往返 + 坏值归一);
- AgentTranslator._run_agent_task 编排接线:synopsis 进 system、预算驱逐历史页、
  llm_story_context 开关关闭时零注入。

Run from the repo root:

    QT_QPA_PLATFORM=offscreen ./ballontrans_pylibs_win/python.exe -m pytest tests/test_story_injection.py -q
"""

import os
import os.path as osp
import sys
import tempfile
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402

import modules.translators.trans_agent as trans_agent  # noqa: E402
from modules.context.token_usage import fallback_token_count  # noqa: E402
from modules.context_agent.story import (  # noqa: E402
    SYNOPSIS_KEY,
    project_synopsis,
)
from modules.translators.agent.prompts import (  # noqa: E402
    build_history_snippet,
    build_system_message,
    effective_history_budget,
    synopsis_section,
)
from modules.translators.trans_agent import AgentTranslator  # noqa: E402
from utils.config import SingleBlkTranslateMode, pcfg  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────


class _Blk:
    def __init__(self, src, tr=""):
        self._src = src
        self.translation = tr

    def get_text(self):
        return self._src


def _proj(pages, synopsis=None):
    p = SimpleNamespace(pages=pages)
    if synopsis is not None:
        setattr(p, SYNOPSIS_KEY, synopsis)
    return p


@pytest.fixture
def trans(monkeypatch):
    monkeypatch.setattr(pcfg.module, "llm_story_context", True)
    monkeypatch.setattr(pcfg.module, "llm_translate_context", True)
    monkeypatch.setattr(pcfg.module, "llm_prior_context_token_budget", 4096)
    monkeypatch.setattr(pcfg.module, "llm_glossary_path", "")
    monkeypatch.setattr(pcfg.module, "agent_translation_debug_log", False)
    # 多块任务固定走完整 agent loop,不受用户本机单框档位影响
    monkeypatch.setattr(
        pcfg.module, "single_blk_translate_mode", SingleBlkTranslateMode.Plain
    )
    t = AgentTranslator("日本語", "简体中文")
    monkeypatch.setattr(t, "all_model_loaded", lambda: True)
    monkeypatch.setattr(t, "_select_api_key", lambda: "test-key")
    monkeypatch.setattr(t, "client", SimpleNamespace(api_key="test-key"))
    monkeypatch.setattr(
        AgentTranslator, "_effective_model", property(lambda self: "test-model")
    )
    return t


# ── prompts 层 ───────────────────────────────────────────────────────


def test_system_message_includes_synopsis_section():
    msg = build_system_message("Auto", "English", synopsis="故事发生在……")
    assert "Story synopsis" in msg and "故事发生在……" in msg
    # 与 profile prompt 共存,梗概在前
    msg2 = build_system_message(
        "Auto", "English", profile_prompt="keep it punchy", synopsis="梗概"
    )
    assert msg2.index("Story synopsis") < msg2.index("keep it punchy")
    assert "Story synopsis" not in build_system_message("Auto", "English")


def test_effective_history_budget_eviction():
    synopsis = "あ" * 200  # ~200 token,远超小预算
    budget = 100
    # 无梗概:原预算可用,短历史页放得下
    page_cost = fallback_token_count('Page "p1":\n- "hi" -> "你好"') + 24
    assert page_cost < budget
    assert build_history_snippet(
        _proj({"p1": [_Blk("hi", "你好")], "cur": [_Blk("now")]}), "cur", budget
    ) != ""
    # 有梗概:强制项吃掉预算,可选历史页被驱逐(地板 1,任何页都放不下)
    reduced = effective_history_budget(budget, synopsis)
    assert reduced == max(
        1, budget - fallback_token_count(synopsis_section(synopsis)) - 24
    )
    assert reduced < page_cost
    assert (
        build_history_snippet(
            _proj({"p1": [_Blk("hi", "你好")], "cur": [_Blk("now")]}),
            "cur",
            reduced,
        )
        == ""
    )


def test_effective_history_budget_edge_cases():
    assert effective_history_budget(4096, "") == 4096
    assert effective_history_budget(0, "あ" * 50) == 0  # 0=不限额,原样返回
    assert effective_history_budget(10, "あ" * 50) == 1  # 地板为 1(0 是不限额语义)


# ── story 数据层 ─────────────────────────────────────────────────────


def test_project_synopsis_reader():
    assert project_synopsis(_proj({}, synopsis="  梗概  ")) == "梗概"
    assert project_synopsis(_proj({})) == ""
    assert project_synopsis(None) == ""
    assert project_synopsis(_proj({}, synopsis=123)) == ""


# ── 项目持久化 ───────────────────────────────────────────────────────


def test_project_synopsis_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        proj = ProjImgTrans(directory=tmp)
        proj.llm_compact_memory = "全局梗概"
        payload = proj.to_dict()
        assert payload["llm_compact_memory"] == "全局梗概"

        restored = ProjImgTrans(directory=tmp)
        restored.load_from_dict(dict(payload))
        assert restored.llm_compact_memory == "全局梗概"


def test_project_synopsis_bad_value_normalized():
    proj = ProjImgTrans()
    proj.load_from_dict({"pages": {}, "llm_compact_memory": 42})
    assert proj.llm_compact_memory == ""
    proj.load_from_dict({"pages": {}})
    assert proj.llm_compact_memory == ""


# ── AgentTranslator 编排接线 ─────────────────────────────────────────


def _capture_loop(monkeypatch, trans):
    captured = {}

    def fake_loop(chat, execute, src_list, **kwargs):
        captured.update(kwargs)
        return {i + 1: f"t{i + 1}" for i in range(len(src_list))}

    monkeypatch.setattr(trans_agent, "run_agent_task", fake_loop)
    return captured


def test_agent_task_injects_synopsis_into_system(monkeypatch, trans):
    captured = _capture_loop(monkeypatch, trans)
    proj = _proj({"cur": [_Blk("now")]}, synopsis="少年踏上旅途")
    result = trans.translate(["你好", "世界"], project=proj, page_key="cur")
    assert result == ["t1", "t2"]
    assert "少年踏上旅途" in captured["system_message"]


def test_agent_task_switch_off_no_injection(monkeypatch, trans):
    monkeypatch.setattr(pcfg.module, "llm_story_context", False)
    captured = _capture_loop(monkeypatch, trans)
    proj = _proj({"cur": [_Blk("now")]}, synopsis="少年踏上旅途")
    trans.translate(["你好", "世界"], project=proj, page_key="cur")
    assert "Story synopsis" not in captured["system_message"]


def test_agent_task_synopsis_evicts_history(monkeypatch, trans):
    monkeypatch.setattr(pcfg.module, "llm_prior_context_token_budget", 100)
    captured = _capture_loop(monkeypatch, trans)
    synopsis = "あ" * 200
    proj = _proj(
        {"p1": [_Blk("hi", "你好")], "cur": [_Blk("now")]}, synopsis=synopsis
    )
    trans.translate(["你好", "世界"], project=proj, page_key="cur")
    assert "Story synopsis" in captured["system_message"]
    # 预算被梗概占满,可选历史页被驱逐
    assert "Prior translated pages" not in captured["user_message"]
