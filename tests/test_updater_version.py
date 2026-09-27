"""发布 tag 格式契约（``lite-v*``）与版本比较的钉子。

本项目的发布 tag 从 ``lite-v1.0.0`` 起步，与上游 ``v1.x.x`` 在同一个
git tag 命名空间里共存（fetch 上游会把它的 tag 一起拉下来），区分与剥离
都靠 ``utils/updater.py::normalize_version_tag``。这里的断言钉住两件会
静默坏掉的事：

1. ``lite-v`` 前缀剥离后版本比较仍然正确（剥不掉会退化到字典序回退路径，
   表现为「永远提示有更新」或「该提示时不提示」）；
2. 老版本（0.6.0 时代的无前缀 tag）与新格式之间的升级路径是单调的。
"""

import unittest

from utils.updater import is_remote_newer, normalize_version_tag


class TestNormalizeVersionTag(unittest.TestCase):
    def test_strips_lite_prefix(self):
        self.assertEqual(normalize_version_tag("lite-v1.0.0"), "1.0.0")
        self.assertEqual(normalize_version_tag("lite-V1.0.0"), "1.0.0")

    def test_keeps_upstream_and_legacy_forms(self):
        self.assertEqual(normalize_version_tag("v1.5.16"), "1.5.16")
        self.assertEqual(normalize_version_tag(" release-2.0 "), "2.0")
        self.assertEqual(normalize_version_tag("0.6.0"), "0.6.0")


class TestRemoteNewerWithLiteTag(unittest.TestCase):
    def test_same_version_is_not_newer(self):
        # 本地 pyproject 是纯数字，远端 tag 带前缀：同版本必须判「已是最新」
        self.assertFalse(is_remote_newer("1.0.0", "lite-v1.0.0"))

    def test_upgrade_from_legacy_version(self):
        # 0.6.0 时代的安装升级到 lite-v1.0.0：必须判「有新版」
        self.assertTrue(is_remote_newer("0.6.0", "lite-v1.0.0"))

    def test_next_lite_release_is_newer(self):
        self.assertTrue(is_remote_newer("lite-v1.0.0", "lite-v1.1.0"))
        self.assertFalse(is_remote_newer("lite-v1.1.0", "lite-v1.0.0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
