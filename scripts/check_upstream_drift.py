#!/usr/bin/env python3
"""上游富文本契约漂移巡检：比对 fork 与上游的序列化契约面，只报「相对基线的新变化」。

背景（见 docs/技术实现/上游兼容策略_长期规范.md）：fork 与上游长期并行演进，
互开工程靠两侧序列化契约的同构性。上游一旦新增/删除/改默认某个键，fork 的
兼容姿态就会静默失效。本脚本把契约面抽成快照，与基线比对，只报漂移——
已知的、已拍板接受的差异存在基线里，不再重复报警。

巡检面（三项，全部用 AST 静态抽取，不 import 任何一侧代码）：
1. `utils/textblock.py::TextBlock` 字段（注解 + 默认值源码）
2. `utils/fontformat.py::FontFormat` 字段（注解 + 默认值源码）
3. `utils/text_effects.py` 效果类型注册表（类名 -> effect_type）
4. `utils/proj_imgtrans.py::ProjImgTrans.to_dict` 工程写入键（启发式：
   取函数体内键数最多的字典字面量；抓不到时该项跳过并说明）

用法：
  python scripts/check_upstream_drift.py                 # 与基线比对，有漂移退出码 1
  python scripts/check_upstream_drift.py --update-baseline   # 重算并写基线
  python scripts/check_upstream_drift.py --print              # 打印当前契约面（不比对）
  python scripts/check_upstream_drift.py --json               # 机器可读输出
  python scripts/check_upstream_drift.py --upstream D:\\path\\to\\BallonsTranslator\\ballontranslator

上游定位优先级：--upstream > 环境变量 BT_UPSTREAM_ROOT > 默认 DEFAULT_UPSTREAM_ROOT。
**上游不存在时打印跳过并退出 0**（不是每台机器都有上游 clone，不能因此挂 CI）。

退出码：0 无漂移 / 已跳过；1 存在漂移或抽取失败。
"""

import argparse
import ast
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "upstream_drift_baseline.json"

# 上游 clone 的包根（本机的默认位置；其他机器用 --upstream 或 BT_UPSTREAM_ROOT 覆盖）
DEFAULT_UPSTREAM_ROOT = Path(r"D:\ruanjian\BallonsTranslator\ballontranslator")

# 契约面：展示名 -> (相对包根的路径, dataclass 名)
FIELD_TARGETS: List[Tuple[str, str, str]] = [
    ("TextBlock", "utils/textblock.py", "TextBlock"),
    ("FontFormat", "utils/fontformat.py", "FontFormat"),
]
EFFECTS_RELPATH = "utils/text_effects.py"
PROJECT_RELPATH = "utils/proj_imgtrans.py"


def _reconfigure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read_source(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _iter_classes(tree: ast.Module):
    """深度遍历所有 ClassDef（含嵌套）。"""
    stack: List[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ClassDef):
            yield node
            stack.extend(node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(node.body)
        elif isinstance(node, ast.With):
            stack.extend(node.body)
        elif isinstance(node, ast.If):
            stack.extend(node.body)
            stack.extend(node.orelse)


def _unparse(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def extract_fields(tree: ast.Module, class_name: str) -> Dict[str, Dict[str, Optional[str]]]:
    """抽取 dataclass 的字段：{字段名: {"annotation": 注解源码, "default": 默认值源码}}。"""
    for cls in _iter_classes(tree):
        if cls.name != class_name:
            continue
        fields: Dict[str, Dict[str, Optional[str]]] = {}
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            fields[stmt.target.id] = {
                "annotation": _unparse(stmt.annotation),
                "default": _unparse(stmt.value),
            }
        return fields
    raise LookupError(f"未找到类定义：{class_name}")


def extract_effects(tree: ast.Module) -> Dict[str, str]:
    """抽取效果类 -> effect_type 默认值（data 层效果的注册标识）。"""
    effects: Dict[str, str] = {}
    for cls in _iter_classes(tree):
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            if stmt.target.id != "effect_type" or stmt.value is None:
                continue
            value = stmt.value
            default: Optional[str] = None
            if isinstance(value, ast.Call):
                for kw in value.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                        default = kw.value.value
            elif isinstance(value, ast.Constant):
                default = value.value
            if isinstance(default, str):
                effects[cls.name] = default
    return effects


def _iter_stmts(body: List[ast.stmt]):
    """递归展开语句块（穿透 if/for/while/with/try，不进入表达式内部）。"""
    stack: List[ast.stmt] = list(body)
    while stack:
        stmt = stack.pop()
        yield stmt
        for attr in ("body", "orelse", "finalbody"):
            sub = getattr(stmt, attr, None)
            if isinstance(sub, list):
                stack.extend(sub)
        for attr in ("handlers",):
            for handler in getattr(stmt, attr, []) or []:
                stack.extend(handler.body)


def _dict_keys(node: ast.Dict) -> List[str]:
    return [
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]


def extract_project_keys(tree: ast.Module) -> Optional[List[str]]:
    """抽取 ProjImgTrans.to_dict 的工程写入键。

    先定位 return 的变量名（如 `return proj_dict`），再只认两种写法——
    这样嵌在字典值里的 dict（条件写入的载荷）不会被误当顶层键：
    1. 给该返回变量的字典字面量赋值（`proj_dict = {...}`）或 return 字典字面量
    2. 该返回变量的常量下标赋值（`proj_dict['llm_compact_memory'] = ...`）
    """
    target = None
    for cls in _iter_classes(tree):
        if cls.name == "ProjImgTrans":
            target = cls
            break
    if target is None:
        return None
    keys: set = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.FunctionDef) or node.name != "to_dict":
            continue
        stmts = list(_iter_stmts(node.body))
        returned: set = set()
        for stmt in stmts:
            if isinstance(stmt, ast.Return):
                if isinstance(stmt.value, ast.Name):
                    returned.add(stmt.value.id)
                elif isinstance(stmt.value, ast.Dict):
                    keys.update(_dict_keys(stmt.value))
        if not returned:
            continue
        for stmt in stmts:
            if not isinstance(stmt, ast.Assign):
                continue
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id in returned:
                    if isinstance(stmt.value, ast.Dict):
                        keys.update(_dict_keys(stmt.value))
                elif (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id in returned
                    and isinstance(tgt.slice, ast.Constant)
                    and isinstance(tgt.slice.value, str)
                ):
                    keys.add(tgt.slice.value)
    return sorted(keys) or None


def collect_snapshot(root: Path) -> Dict:
    """抽取一侧的完整契约快照。"""
    snapshot: Dict = {"fields": {}, "effects": {}, "project_keys": None}
    for label, relpath, cls_name in FIELD_TARGETS:
        snapshot["fields"][label] = extract_fields(_read_source(root / relpath), cls_name)
    snapshot["effects"] = extract_effects(_read_source(root / EFFECTS_RELPATH))
    snapshot["project_keys"] = extract_project_keys(_read_source(root / PROJECT_RELPATH))
    return snapshot


def diff_fields(up: Dict, fork: Dict) -> Dict:
    only_up = sorted(set(up) - set(fork))
    only_fork = sorted(set(fork) - set(up))
    changed = {}
    for name in sorted(set(up) & set(fork)):
        for aspect in ("annotation", "default"):
            a, b = up[name].get(aspect), fork[name].get(aspect)
            if a != b:
                changed[f"{name}.{aspect}"] = {"upstream": a, "fork": b}
    return {"only_upstream": only_up, "only_fork": only_fork, "changed": changed}


def diff_effects(up: Dict[str, str], fork: Dict[str, str]) -> Dict:
    only_up = {k: v for k, v in sorted(up.items()) if k not in fork}
    only_fork = {k: v for k, v in sorted(fork.items()) if k not in up}
    changed = {
        k: {"upstream": up[k], "fork": fork[k]}
        for k in sorted(set(up) & set(fork))
        if up[k] != fork[k]
    }
    return {"only_upstream": only_up, "only_fork": only_fork, "changed": changed}


def diff_project_keys(up: Optional[List[str]], fork: Optional[List[str]]) -> Dict:
    if up is None or fork is None:
        return {"unavailable": True, "upstream": up is None, "fork": fork is None}
    return {
        "unavailable": False,
        "only_upstream": sorted(set(up) - set(fork)),
        "only_fork": sorted(set(fork) - set(up)),
    }


def build_diff(up: Dict, fork: Dict) -> Dict:
    return {
        "fields": {
            label: diff_fields(up["fields"][label], fork["fields"][label])
            for label, _, _ in FIELD_TARGETS
        },
        "effects": diff_effects(up["effects"], fork["effects"]),
        "project_keys": diff_project_keys(up.get("project_keys"), fork.get("project_keys")),
    }


def _is_empty(diff: Dict) -> bool:
    for label in diff["fields"]:
        part = diff["fields"][label]
        if part["only_upstream"] or part["only_fork"] or part["changed"]:
            return False
    if diff["effects"]["only_upstream"] or diff["effects"]["only_fork"] or diff["effects"]["changed"]:
        return False
    pk = diff["project_keys"]
    if not pk.get("unavailable") and (pk.get("only_upstream") or pk.get("only_fork")):
        return False
    return True


def format_diff(diff: Dict, title: str) -> str:
    lines = [title]
    for label in diff["fields"]:
        part = diff["fields"][label]
        lines.append(f"  · {label}")
        for name in part["only_upstream"]:
            lines.append(f"      上游独有字段：{name}")
        for name in part["only_fork"]:
            lines.append(f"      fork 独有字段：{name}")
        for name, vals in part["changed"].items():
            lines.append(f"      字段差异：{name}  上游={vals['upstream']}  fork={vals['fork']}")
    eff = diff["effects"]
    lines.append("  · 效果类型")
    for name, val in eff["only_upstream"].items():
        lines.append(f"      上游独有：{name} -> {val}")
    for name, val in eff["only_fork"].items():
        lines.append(f"      fork 独有：{name} -> {val}")
    for name, vals in eff["changed"].items():
        lines.append(f"      类型差异：{name}  上游={vals['upstream']}  fork={vals['fork']}")
    pk = diff["project_keys"]
    if pk.get("unavailable"):
        lines.append(f"  · 工程写入键：跳过（上游未提取到={pk['upstream']} fork 未提取到={pk['fork']}）")
    else:
        lines.append("  · 工程写入键")
        for name in pk.get("only_upstream", []):
            lines.append(f"      上游独有：{name}")
        for name in pk.get("only_fork", []):
            lines.append(f"      fork 独有：{name}")
    return "\n".join(lines)


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def main() -> int:
    _reconfigure_stdout()
    parser = argparse.ArgumentParser(description="上游富文本契约漂移巡检")
    parser.add_argument("--upstream", help="上游包根路径（ballontranslator/ 所在目录）")
    parser.add_argument("--baseline", default=str(BASELINE_PATH), help="基线文件路径")
    parser.add_argument("--update-baseline", action="store_true", help="重算并写基线")
    parser.add_argument("--print", dest="do_print", action="store_true", help="打印当前契约面")
    parser.add_argument("--json", dest="as_json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    upstream_root = Path(
        args.upstream or os.environ.get("BT_UPSTREAM_ROOT") or DEFAULT_UPSTREAM_ROOT
    )
    if not (upstream_root / "utils" / "textblock.py").is_file():
        print(f"⏭  上游巡检: 跳过（未找到上游包根：{upstream_root}）")
        print("     用 --upstream <path> 或环境变量 BT_UPSTREAM_ROOT 指定")
        return 0

    try:
        up = collect_snapshot(upstream_root)
        fork = collect_snapshot(ROOT)
    except (LookupError, SyntaxError, OSError) as exc:
        print(f"❌ 上游巡检: 契约抽取失败 —— {exc}")
        return 1

    if args.do_print:
        payload = {"upstream": str(upstream_root), "upstream_snapshot": up, "fork_snapshot": fork}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.as_json else
              json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    diff = build_diff(up, fork)
    baseline_file = Path(args.baseline)

    if args.update_baseline:
        payload = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "upstream_root": str(upstream_root),
            "diff": diff,
        }
        baseline_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"✅ 上游巡检: 基线已写入 {baseline_file}")
        print(f"   上游包根：{upstream_root}")
        print("   基线内容（已拍板接受的差异）:")
        print(format_diff(diff, ""))
        return 0

    if not baseline_file.is_file():
        print(f"❌ 上游巡检: 缺少基线 {baseline_file}")
        print("   先跑一次：python scripts/check_upstream_drift.py --update-baseline")
        return 1

    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    old_diff = baseline.get("diff", {})

    drift: List[str] = []
    for label in diff["fields"]:
        new, old = diff["fields"][label], old_diff.get("fields", {}).get(label, {})
        for key in ("only_upstream", "only_fork"):
            added = sorted(set(new.get(key, [])) - set(old.get(key, [])))
            gone = sorted(set(old.get(key, [])) - set(new.get(key, [])))
            for name in added:
                drift.append(f"{label}: 新增 {key}={name}")
            for name in gone:
                drift.append(f"{label}: 消失 {key}={name}")
        for name, vals in new.get("changed", {}).items():
            if not _same(old.get("changed", {}).get(name), vals):
                drift.append(f"{label}: {name}  上游={vals['upstream']}  fork={vals['fork']}")

    new_eff, old_eff = diff["effects"], old_diff.get("effects", {})
    for key in ("only_upstream", "only_fork", "changed"):
        new_map, old_map = new_eff.get(key, {}), old_eff.get(key, {})
        for name in sorted(set(new_map) - set(old_map)):
            drift.append(f"效果 {key}: 新增 {name}={new_map[name]}")
        for name in sorted(set(old_map) - set(new_map)):
            drift.append(f"效果 {key}: 消失 {name}")

    new_pk, old_pk = diff["project_keys"], old_diff.get("project_keys", {})
    if not new_pk.get("unavailable") and not old_pk.get("unavailable"):
        for key in ("only_upstream", "only_fork"):
            for name in sorted(set(new_pk.get(key, [])) - set(old_pk.get(key, []))):
                drift.append(f"工程写入键 {key}: 新增 {name}")
            for name in sorted(set(old_pk.get(key, [])) - set(new_pk.get(key, []))):
                drift.append(f"工程写入键 {key}: 消失 {name}")

    if args.as_json:
        print(json.dumps({"drift": drift, "baseline": str(baseline_file)},
                         ensure_ascii=False, indent=2))
        return 1 if drift else 0

    if not drift:
        print(f"✅ 上游巡检: 无漂移（基线 {baseline.get('generated', '?')}，"
              f"上游 {upstream_root}）")
        return 0

    print(f"❌ 上游巡检: 相对基线出现 {len(drift)} 处漂移（基线 {baseline.get('generated', '?')}）")
    for item in drift:
        print(f"   - {item}")
    print()
    print("处理：确认是已接受的取舍 → --update-baseline；否则按"
          " docs/技术实现/上游兼容策略_长期规范.md 评估兼容影响")
    return 1


if __name__ == "__main__":
    sys.exit(main())
