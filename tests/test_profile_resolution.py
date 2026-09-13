"""profile 选取解析层测试：可用性判定 / 全局激活项 / 归位 / 「使用」按钮。

回归背景（2026-09-13 实测）：四个消费点（翻译器 / llm_ocr / LLMInpaint /
术语工作台）的空值回退都写死「取列表第一项」，而内置示例第一项是没配 key
的 OpenAI，于是用户配好的 profile 永远轮不到用。本测试锁住新的解析优先级：
模块显式选的可用项 → 全局激活的 → 候选池第一个可用项。

``setUp`` 显式给 ``pcfg.module.model_profiles`` 受控值并把 ``save_config``
打桩，避免导入期的配置加载把开发者自己的 profile 漏进单例、也避免测试写盘
（见 docs/基础速查/经验教训.md 的 pcfg 单例污染教训）。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe tests/test_profile_resolution.py
"""

import json
import os
import os.path as osp
import sys
import unittest
from unittest import mock

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from utils import profile_manager  # noqa: E402
from utils.config import pcfg  # noqa: E402
from utils.profile_manager import (  # noqa: E402
    get_default_profile_name,
    heal_profile_selector,
    profile_is_usable,
    resolve_profile,
    resolve_profile_name,
    set_default_profile,
)


def _profile(name, host, key="", model="", vision=True, image=False, image_model=""):
    return {
        "name": name,
        "api_host": host,
        "api_key": key,
        "model": model,
        "vision_support": vision,
        "image_support": image,
        "image_model": image_model,
    }


# 名字刻意覆盖三个 builtin（DeepSeek / LM Studio / Ollama），这样
# load_profiles 的 _merge_builtin_defaults 不会追加额外条目，期望值可控。
_CONTROLLED = [
    # 云端、无 key → 不可用（模仿内置 OpenAI 样例）
    _profile(
        "OpenAI",
        "https://api.openai.com/v1",
        model="gpt-4o",
        image=True,
        image_model="gpt-image-1",
    ),
    # 云端、有 key → 可用（列表里第一个可用项）
    _profile(
        "DeepSeek",
        "https://api.deepseek.com/v1",
        key="sk-real-key",
        model="deepseek-chat",
        image=True,
        image_model="ds-image",
    ),
    # 本地、占位 key 但有模型 → 可用
    _profile("LM Studio", "http://localhost:1234/v1", key="dummy-key", model="qwen-vl"),
    # 本地、占位 key、没模型 → 不可用
    _profile("Ollama", "http://localhost:11434/v1", key="dummy-key"),
]


class _ProfileConfigMixin:
    def setUp(self):
        self._saved_raw = pcfg.module.model_profiles
        self._saved_default = pcfg.module.default_profile
        self._save_patcher = mock.patch("utils.profile_manager.save_config")
        self._save_patcher.start()
        self.set_profiles(_CONTROLLED)
        pcfg.module.default_profile = ""

    def tearDown(self):
        self._save_patcher.stop()
        pcfg.module.model_profiles = self._saved_raw
        pcfg.module.default_profile = self._saved_default

    @staticmethod
    def set_profiles(profiles):
        pcfg.module.model_profiles = json.dumps(profiles, ensure_ascii=False)


class TestProfileUsability(unittest.TestCase):
    def test_remote_with_key_and_model_is_usable(self):
        self.assertTrue(
            profile_is_usable(_profile("p", "https://x/v1", "sk-1", "m"))
        )

    def test_remote_without_key_is_not_usable(self):
        self.assertFalse(profile_is_usable(_profile("p", "https://x/v1", "", "m")))

    def test_remote_with_placeholder_key_is_not_usable(self):
        self.assertFalse(
            profile_is_usable(_profile("p", "https://x/v1", "dummy-key", "m"))
        )

    def test_local_with_placeholder_key_is_usable(self):
        self.assertTrue(
            profile_is_usable(_profile("p", "http://localhost:1234/v1", "dummy-key", "m"))
        )

    def test_missing_model_is_not_usable(self):
        self.assertFalse(profile_is_usable(_profile("p", "https://x/v1", "sk-1")))
        self.assertFalse(
            profile_is_usable(_profile("p", "http://localhost:1234/v1", "dummy-key"))
        )

    def test_missing_host_is_not_usable(self):
        self.assertFalse(profile_is_usable(_profile("p", "", "sk-1", "m")))

    def test_non_dict_is_not_usable(self):
        self.assertFalse(profile_is_usable(None))
        self.assertFalse(profile_is_usable({}))

    def test_image_pool_uses_image_model(self):
        profile = _profile("p", "https://x/v1", "sk-1", model="m", image_model="")
        self.assertTrue(profile_is_usable(profile))
        self.assertFalse(profile_is_usable(profile, model_key="image_model"))
        profile["image_model"] = "img"
        self.assertTrue(profile_is_usable(profile, model_key="image_model"))


class TestDefaultProfileName(_ProfileConfigMixin, unittest.TestCase):
    def test_falls_back_to_first_usable(self):
        self.assertEqual(get_default_profile_name(), "DeepSeek")

    def test_explicit_usable_wins(self):
        pcfg.module.default_profile = "LM Studio"
        self.assertEqual(get_default_profile_name(), "LM Studio")

    def test_explicit_unusable_falls_back(self):
        pcfg.module.default_profile = "OpenAI"
        self.assertEqual(get_default_profile_name(), "DeepSeek")

    def test_explicit_missing_name_falls_back(self):
        pcfg.module.default_profile = "ghost"
        self.assertEqual(get_default_profile_name(), "DeepSeek")

    def test_no_usable_profile_returns_empty(self):
        self.set_profiles([_CONTROLLED[0]])  # 只有无 key 的 OpenAI
        self.assertEqual(get_default_profile_name(), "")

    def test_set_default_profile_writes_field_and_saves(self):
        set_default_profile("DeepSeek")
        self.assertEqual(pcfg.module.default_profile, "DeepSeek")
        set_default_profile("")
        self.assertEqual(pcfg.module.default_profile, "")


class TestResolveProfile(_ProfileConfigMixin, unittest.TestCase):
    def test_empty_selection_follows_default(self):
        self.assertEqual(resolve_profile_name(""), "DeepSeek")

    def test_explicit_usable_wins_over_default(self):
        self.assertEqual(resolve_profile_name("LM Studio"), "LM Studio")

    def test_explicit_unusable_falls_back_to_default(self):
        self.assertEqual(resolve_profile_name("OpenAI"), "DeepSeek")

    def test_explicit_missing_falls_back_to_default(self):
        self.assertEqual(resolve_profile_name("ghost"), "DeepSeek")

    def test_vision_pool_excludes_non_vision_default(self):
        # 默认项不支持视觉时，视觉消费点退到池内第一个可用项
        profiles = json.loads(pcfg.module.model_profiles)
        profiles[1]["vision_support"] = False  # DeepSeek 关掉视觉
        self.set_profiles(profiles)
        self.assertEqual(get_default_profile_name(), "DeepSeek")
        self.assertEqual(resolve_profile_name("", vision=True), "LM Studio")

    def test_image_pool_excludes_profiles_without_image_model(self):
        profiles = json.loads(pcfg.module.model_profiles)
        profiles[1]["image_model"] = ""  # DeepSeek 没填图像模型
        self.set_profiles(profiles)
        self.assertEqual(get_default_profile_name(), "DeepSeek")
        self.assertIsNone(resolve_profile("", image=True))

    def test_returns_none_when_nothing_usable(self):
        self.set_profiles([_CONTROLLED[0]])
        self.assertIsNone(resolve_profile("OpenAI"))
        self.assertIsNone(resolve_profile("", vision=True))
        self.assertEqual(resolve_profile_name(""), "")


class TestHealProfileSelector(_ProfileConfigMixin, unittest.TestCase):
    def test_unusable_value_is_healed_to_default(self):
        cfg = {"options": [p["name"] for p in _CONTROLLED], "value": "OpenAI"}
        self.assertEqual(heal_profile_selector(cfg), "DeepSeek")
        self.assertEqual(cfg["value"], "DeepSeek")

    def test_usable_value_is_left_alone(self):
        cfg = {"options": ["OpenAI", "DeepSeek"], "value": "LM Studio"}
        self.assertEqual(heal_profile_selector(cfg), "LM Studio")

    def test_empty_value_gets_filled(self):
        cfg = {"options": [p["name"] for p in _CONTROLLED], "value": ""}
        self.assertEqual(heal_profile_selector(cfg), "DeepSeek")

    def test_value_outside_pool_cleared_when_nothing_usable(self):
        # 旧值已不在候选池（profile 改过名 / 关掉了能力位）又没有可用项
        # → 清空，而不是让下拉框挂一个不存在的选择
        self.set_profiles([_CONTROLLED[0]])
        cfg = {"options": ["DeepSeek", "LM Studio"], "value": "OpenAI"}
        self.assertEqual(heal_profile_selector(cfg, vision=True), "")

    def test_in_pool_unusable_value_kept_for_diagnostics(self):
        # 池内但不可用 → 保留：报错要靠这个名字指出缺哪个字段
        self.set_profiles([_CONTROLLED[0]])
        cfg = {"options": ["OpenAI", "DeepSeek"], "value": "OpenAI"}
        self.assertEqual(heal_profile_selector(cfg, vision=True), "OpenAI")

    def test_value_outside_pool_falls_back_to_pool(self):
        # 视觉池里没有显式选的那个 → 归位到池内可用项
        cfg = {"options": ["OpenAI", "DeepSeek"], "value": "Ollama"}
        self.assertEqual(heal_profile_selector(cfg, vision=True), "DeepSeek")


class TestUseButton(_ProfileConfigMixin, unittest.TestCase):
    """模型管理页「使用 / 使用中」按钮。"""

    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication(
            sys.argv[:1] + ["--platform", "offscreen"]
        )

    def setUp(self):
        super().setUp()
        from ui.llm_profile_cards import LLMProfileListWidget

        self._write_patcher = mock.patch(
            "ui.llm_profile_cards.save_all_profiles"
        )
        self._write_patcher.start()
        self.widget = LLMProfileListWidget()

    def tearDown(self):
        self.widget.deleteLater()
        self._write_patcher.stop()
        super().tearDown()

    def _card(self, name):
        return next(c for c in self.widget._cards if c.profile["name"] == name)

    def test_only_effective_profile_marked_in_use(self):
        checked = [
            c.profile["name"] for c in self.widget._cards if c._use_btn.isChecked()
        ]
        # 没显式设过时也要标出「推断出来的」那一个，否则用户看不出在用哪个
        self.assertEqual(checked, ["DeepSeek"])

    def test_use_click_records_default_and_resyncs(self):
        received = []
        self.widget.profiles_changed.connect(lambda: received.append(1))
        self._card("LM Studio").use_requested.emit("LM Studio")
        self.assertEqual(pcfg.module.default_profile, "LM Studio")
        self.assertEqual(
            [c.profile["name"] for c in self.widget._cards if c._use_btn.isChecked()],
            ["LM Studio"],
        )
        self.assertTrue(received)

    def test_use_button_text_reflects_state(self):
        self.assertEqual(self._card("DeepSeek")._use_btn.text(), "In use")
        self.assertEqual(self._card("OpenAI")._use_btn.text(), "Use")


if __name__ == "__main__":
    unittest.main()
