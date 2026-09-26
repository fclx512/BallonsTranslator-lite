"""精简包构建脚本的跨文件契约。

`scripts/build_win_minimal.ps1` 里那份「绝不允许进入精简包的重依赖」清单是它独立
维护的：模型后端拆进 `pyproject.toml` 的 optional-dependencies 之后，两者之间没有
任何机制保证同步。往 pyproject 里新加一个重后端（或换名字）而忘了同步构建脚本的
清单，构建校验不会失败——残包会被当成精简包发出去，而且没有任何报错线索。
这里把这层同步钉住（`scripts/README.md`「精简包发行」一节是同一份口径的文字版）。

Run:
    ./ballontrans_pylibs_win/python.exe -m pytest tests/test_minimal_package_contract.py -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_win_minimal.ps1"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# 参与同步的 extras：模型后端与加速。`mcp` 不在此列（它不是模型后端，
# 也不需要打进精简包）。
HEAVY_EXTRAS = ("gpu", "acc", "onnx")

# 不在 pyproject 里的重后端：onnxocr 声明了 numpy<2 约束，安装时必须裸装
# （``utils/package_installer.py::NO_DEPS_PACKAGES``），所以只能单独钉。
EXTRA_FORBIDDEN = ("onnxocr",)


def _build_script_text() -> str:
    # 脚本是 UTF-8 with BOM（中文提示语 + Windows PowerShell 5.1）。
    return BUILD_SCRIPT.read_text(encoding="utf-8-sig")


def _declared_forbidden_packages() -> set:
    match = re.search(
        r"\$ForbiddenPackages\s*=\s*@\((.*?)\n\)", _build_script_text(), re.S
    )
    if not match:
        raise AssertionError(
            "$ForbiddenPackages 数组没找到（构建脚本改了声明形状？同步检查需要跟着改）"
        )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _normalize(name: str) -> str:
    """PEP 503 归一化，与构建脚本对 -/_ 与大小写的处理对齐。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _heavy_extra_packages() -> set:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    packages = set()
    for extra in HEAVY_EXTRAS:
        for requirement in extras.get(extra, []):
            base = re.split(r"[<>=!\[; ]", requirement.strip())[0]
            if base:
                packages.add(_normalize(base))
    return packages


class TestForbiddenPackageListStaysInSync(unittest.TestCase):
    """构建脚本的禁用清单必须覆盖 pyproject 里声明的每一个模型后端/加速包。"""

    def test_list_is_not_empty(self):
        self.assertTrue(
            _declared_forbidden_packages(),
            "$ForbiddenPackages 是空的——精简包成分校验形同虚设",
        )

    def test_every_heavy_extra_is_forbidden(self):
        declared = {_normalize(name) for name in _declared_forbidden_packages()}
        missing = sorted(_heavy_extra_packages() - declared)
        self.assertEqual(
            missing,
            [],
            "pyproject.toml 声明的重依赖没进 build_win_minimal.ps1 的禁用清单："
            f"{missing}（新增模型后端时两处都要改）",
        )

    def test_packages_outside_pyproject_are_forbidden(self):
        declared = {_normalize(name) for name in _declared_forbidden_packages()}
        missing = sorted(
            name for name in EXTRA_FORBIDDEN if _normalize(name) not in declared
        )
        self.assertEqual(missing, [], f"禁用清单缺：{missing}")


class TestReleaseGateHashNormalization(unittest.TestCase):
    """发行门禁的哈希比对必须归一 manifest 的 "sha256:" 前缀与大小写。

    manifest.json 的值形如 "sha256:<小写hex>"（``scripts/generate_manifest.py::compute_sha256``），
    而 ``Get-FileHash`` 返回大写裸 hex——直接相等比较会让发行门禁逐文件误报，
    正式构建永远过不去。谁要是"修好"了报错又把归一拆了，这里拦住。
    """

    def test_gate_strips_prefix_and_casefolds_both_sides(self):
        text = _build_script_text()
        # $Full 只出现在发行门禁的逐文件比对里（另一处 $Actual 是归档钉定，用 $Path）
        actual = re.search(r"\$Actual\s*=.*Get-FileHash\s+-LiteralPath\s+\$Full.*", text)
        expected = re.search(r"\$Expected\s*=.*", text)
        self.assertIsNotNone(actual, "门禁的实际哈希行不见了（比对逻辑改名？同步本用例）")
        self.assertIsNotNone(expected, "门禁的期望哈希归一行不见了（比对逻辑改名？同步本用例）")
        self.assertIn("ToLower()", actual.group(0), "Get-FileHash 结果没有归一大小写")
        self.assertIn("sha256:", expected.group(0), "manifest 值没有剥 sha256: 前缀")
        self.assertIn("ToLower()", expected.group(0), "manifest 值没有归一大小写")


if __name__ == "__main__":
    unittest.main()
