"""静态校验:pcfg 属性引用与 Config 类声明一致(防"字段没声明"类回归)。

回归背景(已三度翻车):
- glossary_dock_open 未声明进 ProgramConfig → 嵌字页 getattr 崩溃;
- agent_translation_debug_log 声明在 ProgramConfig,而 trans_agent 读
  pcfg.module.agent_translation_debug_log → agent loop 每轮 AttributeError,
  永远静默回退直译;
- glossary_agent_panel 读 pcfg.source_lang/target_lang(ProgramConfig 从无
  此字段)→ 工作台会话轮必崩。

本测试 AST 扫描 live 代码中 utils.config.pcfg 全部导入别名的属性链,逐级
核对声明:成员(字段/方法)存在于该 Config 类则通过,注解指向另一个
Config 类则下钻,非 Config 注解(Dict/str/dict 实例方法调用等)停止下钻。
仅静态扫描:动态 setattr、getattr(字符串)不在覆盖范围;tests/ 与审计
登记 suspended 的休眠拷贝(ui/text_engine/editing/、formatting/)不扫。
"""

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_DIRS = ("modules", "ui", "utils", "scripts")
SCAN_FILES = ("launch.py",)
SCAN_EXCLUDES = (
    os.path.join("ui", "text_engine", "editing"),
    os.path.join("ui", "text_engine", "formatting"),
)

# (类名, 成员名):hasattr 守卫的旧配置兼容读取等,无法静态判定意图的引用
ALLOWLIST = {
    ("ProgramConfig", "theme_name"),
}


def _collect_class_members(tree):
    """模块顶层类 → {类名: {成员名: 注解类名或 None}}。"""
    classes = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        members = {}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(
                stmt.target, ast.Name
            ):
                ann = stmt.annotation
                members[stmt.target.id] = (
                    ann.id if isinstance(ann, ast.Name) else None
                )
            elif isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        members[tgt.id] = None
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members[stmt.name] = None
        classes[node.name] = members
    return classes


def _load_classes():
    classes = {}
    for rel in (("utils", "structures.py"), ("utils", "config.py")):
        path = os.path.join(REPO_ROOT, *rel)
        with open(path, encoding="utf-8") as f:
            classes.update(_collect_class_members(ast.parse(f.read())))
    return classes


def _members_of(classes, cls):
    """cls 的成员并集入其基类 Config(utils/structures.py)的方法。"""
    members = dict(classes.get("Config", {}))
    members.update(classes.get(cls, {}))
    return members


def _pcfg_aliases(tree):
    """本文件绑定 utils.config.pcfg 的名字集合(含 asname 别名)。"""
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] != "config":
                continue
            for alias in node.names:
                if alias.name == "pcfg":
                    roots.add(alias.asname or alias.name)
    return roots


def _pcfg_chains(tree, roots):
    """所有以 pcfg 别名开头的属性链,根→叶顺序,如 ('module', 'translator')。"""
    chains = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        attrs = []
        cur = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in roots:
            chains.append(tuple(reversed(attrs)))
    return chains


def _check_chain(chain, classes):
    """返回 None(通过)或违规说明。非 Config 注解处停止下钻。"""
    cls = "ProgramConfig"
    for i, attr in enumerate(chain):
        members = _members_of(classes, cls)
        if attr not in members:
            if (cls, attr) in ALLOWLIST:
                return None
            return f"{cls} 未声明 '{attr}'"
        ann = members[attr]
        if i + 1 < len(chain):
            if ann in classes:
                cls = ann
            else:
                break
    return None


def _iter_py_files():
    for d in SCAN_DIRS:
        base = os.path.join(REPO_ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)
    for fn in SCAN_FILES:
        yield os.path.join(REPO_ROOT, fn)


class TestPcfgFieldDeclarations(unittest.TestCase):
    def test_all_referenced_fields_declared(self):
        classes = _load_classes()
        problems = []
        for path in _iter_py_files():
            rel = os.path.relpath(path, REPO_ROOT)
            if any(rel.startswith(ex) for ex in SCAN_EXCLUDES):
                continue
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            roots = _pcfg_aliases(tree)
            if not roots:
                continue
            for chain in _pcfg_chains(tree, roots):
                msg = _check_chain(chain, classes)
                if msg:
                    problems.append(f"{rel}: pcfg.{'.'.join(chain)} → {msg}")
        self.assertEqual(
            problems,
            [],
            "pcfg 属性引用未声明在对应 Config 类上:\n" + "\n".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
