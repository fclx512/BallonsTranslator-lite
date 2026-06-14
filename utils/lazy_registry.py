"""AST-based lazy module registry scanner.

Scans module files using Python's AST to extract decorator keys and class
attributes (params, dependencies, etc.) *without importing* the module code.
This lets the UI populate dropdowns and show config panels without paying
the cost of importing torch, model architectures, etc. at startup.

The real ``importlib.import_module()`` call is deferred until the user first
selects a module (via ``Registry.resolve_module()``).
"""

import ast
import importlib.metadata
import os
import platform
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from utils.registries import DECORATORS
from utils.registry import ModuleSpec

UNKNOWN = object()

# Project root — used to convert file paths to dotted module names.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

INITIALIZED_REGISTRIES = set()


def _package_version(package_name):
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _torch_package_backend():
    version = _package_version("torch")
    if version is None:
        return None
    if sys.platform == "darwin":
        return "mps"
    if "+" not in version:
        return None
    local_version = version.split("+", 1)[1].lower()
    if local_version.startswith(("cu", "rocm")):
        return "cuda"
    if local_version.startswith("xpu"):
        return "xpu"
    return None


def _candidate_device_options():
    options = ["cpu"]
    backend = _torch_package_backend()
    if backend is not None:
        options.append(backend)
    return options


def _preferred_device_value(options):
    preferred = ["mps"] if sys.platform == "darwin" else ["cuda", "xpu"]
    for device in preferred:
        if device in options:
            return device
    if "cpu" in options:
        return "cpu"
    return options[0] if options else "cpu"


def _device_selector(not_supported=None):
    if not_supported is None:
        not_supported = []
    options = _candidate_device_options()
    options = [
        opt for opt in options if all(device not in opt for device in not_supported)
    ]
    return {
        "type": "selector",
        "options": options,
        "value": _preferred_device_value(options),
        "__device_not_supported": not_supported,
    }


class SafeEval:
    """Evaluate the small literal subset needed for lazy module metadata.

    Unknown or unsafe expressions return ``UNKNOWN`` so the lazy scanner can
    keep building specs without importing the module being scanned.

    Example:
        >>> expr = ast.parse('base + 3', mode='eval').body
        >>> SafeEval({'base': 2}).eval(expr)
        5
        >>> unknown = ast.parse('load_model()', mode='eval').body
        >>> SafeEval({}).eval(unknown) is UNKNOWN
        True
    """

    def __init__(self, env: Dict[str, Any]):
        self.env = env

    def eval(self, node):
        try:
            return self.visit(node)
        except Exception:
            return UNKNOWN

    def visit(self, node):
        method = "visit_" + node.__class__.__name__
        visitor = getattr(self, method, None)
        if visitor is None:
            return UNKNOWN
        return visitor(node)

    def visit_Constant(self, node):
        return node.value

    def visit_Name(self, node):
        if node.id in self.env:
            return self.env[node.id]
        if node.id == "None":
            return None
        return UNKNOWN

    def visit_List(self, node):
        values = [self.visit(v) for v in node.elts]
        return UNKNOWN if any(v is UNKNOWN for v in values) else values

    def visit_Tuple(self, node):
        values = [self.visit(v) for v in node.elts]
        return UNKNOWN if any(v is UNKNOWN for v in values) else tuple(values)

    def visit_Set(self, node):
        values = [self.visit(v) for v in node.elts]
        return UNKNOWN if any(v is UNKNOWN for v in values) else set(values)

    def visit_Dict(self, node):
        out = {}
        for key_node, value_node in zip(node.keys, node.values):
            value = self.visit(value_node)
            if value is UNKNOWN:
                return UNKNOWN
            if key_node is None:
                if not isinstance(value, dict):
                    return UNKNOWN
                out.update(value)
                continue
            key = self.visit(key_node)
            if key is UNKNOWN:
                return UNKNOWN
            out[key] = value
        return out

    def visit_UnaryOp(self, node):
        value = self.visit(node.operand)
        if value is UNKNOWN:
            return UNKNOWN
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.Not):
            return not value
        return UNKNOWN

    def visit_BoolOp(self, node):
        values = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            for value in values:
                if value is False:
                    return False
                if value is UNKNOWN:
                    return UNKNOWN
            return True
        if isinstance(node.op, ast.Or):
            for value in values:
                if value is True:
                    return True
                if value is UNKNOWN:
                    return UNKNOWN
            return False
        return UNKNOWN

    def visit_Compare(self, node):
        left = self.visit(node.left)
        if left is UNKNOWN:
            return UNKNOWN
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if right is UNKNOWN:
                return UNKNOWN
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            elif isinstance(op, ast.Is):
                ok = left is right
            elif isinstance(op, ast.IsNot):
                ok = left is not right
            else:
                return UNKNOWN
            if not ok:
                return False
            left = right
        return True

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Mod):
            return left % right
        return UNKNOWN

    def visit_IfExp(self, node):
        test = self.visit(node.test)
        if test is UNKNOWN:
            return self.visit(node.orelse)
        return self.visit(node.body if test else node.orelse)

    def visit_Subscript(self, node):
        value = self.visit(node.value)
        if value is UNKNOWN:
            return UNKNOWN
        index = self.visit(node.slice)
        if index is UNKNOWN:
            return UNKNOWN
        try:
            return value[index]
        except Exception:
            return UNKNOWN

    def visit_Slice(self, node):
        lower = None if node.lower is None else self.visit(node.lower)
        upper = None if node.upper is None else self.visit(node.upper)
        step = None if node.step is None else self.visit(node.step)
        if lower is UNKNOWN or upper is UNKNOWN or step is UNKNOWN:
            return UNKNOWN
        return slice(lower, upper, step)

    def visit_Attribute(self, node):
        value = self.visit(node.value)
        if value is UNKNOWN:
            if isinstance(node.value, ast.Name):
                root = node.value.id
                if root == "sys" and node.attr == "platform":
                    return sys.platform
            return UNKNOWN
        return getattr(value, node.attr, UNKNOWN)

    def visit_Call(self, node):
        func_name = _call_name(node.func)
        args = [self.visit(arg) for arg in node.args]
        if any(arg is UNKNOWN for arg in args):
            return UNKNOWN

        if func_name == "DEVICE_SELECTOR":
            not_supported = args[0] if args else []
            for kw in node.keywords:
                if kw.arg == "not_supported":
                    not_supported = self.visit(kw.value)
                    if not_supported is UNKNOWN:
                        not_supported = []
            return _device_selector(not_supported)
        if func_name in {"deepcopy", "copy.deepcopy"} and len(args) == 1:
            return deepcopy(args[0])
        if func_name == "list" and len(args) == 1:
            return list(args[0])
        if func_name == "tuple" and len(args) == 1:
            return tuple(args[0])
        if func_name == "set" and len(args) == 1:
            return set(args[0])
        if func_name == "str" and len(args) == 1:
            return str(args[0])
        if func_name == "int" and len(args) == 1:
            return int(args[0])
        if func_name == "float" and len(args) == 1:
            return float(args[0])
        if func_name == "platform.system":
            return platform.system()
        if func_name == "platform.mac_ver":
            return platform.mac_ver()
        if func_name == "platform.version":
            return platform.version()
        if func_name in {"os.path.join", "osp.join"}:
            return os.path.join(*args)
        # Fallback: handle method calls on previously-evaluated objects
        # e.g. lang_map.keys() where lang_map is a dict in the environment.
        if "." in func_name:
            obj_name, _, method_name = func_name.rpartition(".")
            if obj_name in self.env:
                obj = self.env[obj_name]
                method = getattr(obj, method_name, None)
                if callable(method):
                    try:
                        return method(*args)
                    except Exception:
                        return UNKNOWN
        return UNKNOWN


def _call_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _module_name_from_path(path: str) -> str:
    """Convert a file path to a dotted Python module name.

    Example:
        >>> _module_name_from_path('/project/modules/ocr/ocr_mit.py')
        'modules.ocr.ocr_mit'
    """
    path_obj = Path(path).resolve()
    try:
        rel_path = path_obj.relative_to(PACKAGE_ROOT)
        return ".".join(rel_path.with_suffix("").parts)
    except ValueError:
        module_name = path.replace(os.sep, ".").replace("/", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        return module_name


def _decorator_key(node, module_type: str, env: Dict[str, Any]) -> Optional[str]:
    if not isinstance(node, ast.Call):
        return None
    if _call_name(node.func) not in DECORATORS[module_type]:
        return None
    if len(node.args) == 0:
        return None
    value = SafeEval(env).eval(node.args[0])
    return value if isinstance(value, str) else None


def _assign_name(node):
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None, None


def _walk_assignments(stmts: Iterable[ast.stmt], env: Dict[str, Any]):
    evaluator = SafeEval(env)
    for node in stmts:
        name, value_node = _assign_name(node)
        if name is not None and value_node is not None:
            value = evaluator.eval(value_node)
            if value is not UNKNOWN:
                env[name] = value


def _collect_class_attrs(
    class_node: ast.ClassDef, env: Dict[str, Any]
) -> Dict[str, Any]:
    """Collect class-level metadata without executing the class body."""
    attrs = {}
    class_env = env.copy()

    def walk(stmts):
        evaluator = SafeEval(class_env)
        for node in stmts:
            name, value_node = _assign_name(node)
            if name is not None and value_node is not None:
                value = evaluator.eval(value_node)
                if value is not UNKNOWN:
                    class_env[name] = value
                    if name in {
                        "params",
                        "download_file_list",
                        "download_file_on_load",
                        "dependencies",
                    }:
                        attrs[name] = value
            elif isinstance(node, ast.If):
                cond = evaluator.eval(node.test)
                if cond is True:
                    walk(node.body)
                elif cond is False:
                    walk(node.orelse)
                else:
                    walk(node.body)
                    walk(node.orelse)

    walk(class_node.body)
    return attrs


def _collect_translator_langs(class_node: ast.ClassDef, env: Dict[str, Any]):
    """Infer translator language lists from simple class metadata."""
    langs = []
    src = tgt = None
    cht_require_convert = False
    evaluator = SafeEval(env)

    for node in class_node.body:
        name, value_node = _assign_name(node)
        if name == "cht_require_convert" and value_node is not None:
            value = evaluator.eval(value_node)
            if isinstance(value, bool):
                cht_require_convert = value

        if isinstance(node, ast.FunctionDef):
            if node.name in {"supported_src_list", "supported_tgt_list"}:
                value = _return_list(node, env)
                if node.name == "supported_src_list":
                    src = value
                else:
                    tgt = value
            if node.name == "_setup_translator":
                for child in ast.walk(node):
                    if not isinstance(child, ast.Assign) or len(child.targets) != 1:
                        continue
                    target = child.targets[0]
                    if not isinstance(target, ast.Subscript):
                        continue
                    if not isinstance(target.value, ast.Attribute):
                        continue
                    if target.value.attr != "lang_map":
                        continue
                    key = evaluator.eval(target.slice)
                    value = evaluator.eval(child.value)
                    if (
                        isinstance(key, str)
                        and value not in {"", None, UNKNOWN}
                        and key not in langs
                    ):
                        langs.append(key)

    if class_node.name in {"TransNone", "TransSource"}:
        _BASE_TRANSLATOR_LANGS = [
            "Auto",
            "简体中文",
            "繁體中文",
            "日本語",
            "English",
            "한국어",
            "Tiếng Việt",
            "čeština",
            "Nederlands",
            "Français",
            "Deutsch",
            "magyar nyelv",
            "Italiano",
            "Polski",
            "Português",
            "Brazilian Portuguese",
            "limba română",
            "русский язык",
            "Español",
            "Türk dili",
            "украї́нська мо́ва",
            "Thai",
            "Arabic",
            "Hindi",
            "Malayalam",
            "Tamil",
        ]
        langs = _BASE_TRANSLATOR_LANGS
    if cht_require_convert and "简体中文" in langs and "繁體中文" not in langs:
        langs.append("繁體中文")
    if src is None:
        src = langs or None
    if tgt is None:
        tgt = langs or None
    return src, tgt


def _return_list(func_node: ast.FunctionDef, env: Dict[str, Any]):
    evaluator = SafeEval(env)
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value is not None:
            value = evaluator.eval(node.value)
            if isinstance(value, list):
                return value
    return None


def _scan_file(path: str, module_type: str) -> List[ModuleSpec]:
    """Build lazy module specs from decorators and class attributes in one file.

    Example:
        >>> import tempfile
        >>> source = '''
        ... @register_translator("demo")
        ... class DemoTranslator:
        ...     params = {"device": DEVICE_SELECTOR(not_supported=["cuda", "xpu", "mps"])}
        ... '''
        >>> with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        ...     _ = f.write(source)
        ...     path = f.name
        >>> specs = _scan_file(path, 'translator')
        >>> os.unlink(path)
        >>> specs[0].key, specs[0].class_name
        ('demo', 'DemoTranslator')
        >>> specs[0].params['device']['value']
        'cpu'
    """

    with open(path, "r", encoding="utf8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    module_path = _module_name_from_path(path)
    specs = []
    env = {
        "sys": sys,
        "platform": platform,
        "DEFAULT_DEVICE": "cpu",
        "BF16_SUPPORTED": False,
        "True": True,
        "False": False,
        "None": None,
    }

    def walk(stmts):
        _walk_assignments(stmts, env)
        evaluator = SafeEval(env)
        for node in stmts:
            if isinstance(node, ast.ClassDef):
                key = None
                for decorator in node.decorator_list:
                    key = _decorator_key(decorator, module_type, env)
                    if key is not None:
                        break
                if key is None:
                    continue
                attrs = _collect_class_attrs(node, env)
                src = tgt = None
                if module_type == "translator":
                    src, tgt = _collect_translator_langs(node, env)
                specs.append(
                    ModuleSpec(
                        key=key,
                        import_path=module_path,
                        class_name=node.name,
                        module_type=module_type,
                        params=attrs.get("params"),
                        download_file_list=attrs.get("download_file_list"),
                        download_file_on_load=attrs.get("download_file_on_load", False),
                        dependencies=deepcopy(attrs.get("dependencies", [])),
                        supported_src_list=src,
                        supported_tgt_list=tgt,
                    )
                )
            elif isinstance(node, ast.If):
                cond = evaluator.eval(node.test)
                if cond is True:
                    walk(node.body)
                elif cond is False:
                    walk(node.orelse)
                else:
                    walk(node.body)
                    walk(node.orelse)
            elif isinstance(node, ast.Try):
                walk(node.body)

    walk(tree.body)
    return specs


def init_lazy_module_registries(target_modules=None):
    """Register lightweight module specs while leaving real imports deferred.

    Example:
        >>> init_lazy_module_registries('ocr')  # doctest: +SKIP
        >>> OCR.get_spec('mit48px').resolved_class is None  # doctest: +SKIP
        True
    """
    from utils.registries import MODULE_SCRIPTS, MODULETYPE_TO_REGISTRIES

    def _module_files(module_type: str) -> List[str]:
        script = MODULE_SCRIPTS[module_type]
        module_dir = script["module_dir"]
        pattern = re.compile(script["module_pattern"])

        # Resolve relative path against the project root.
        search_dir = module_dir
        if not os.path.isabs(search_dir):
            search_dir = str(PACKAGE_ROOT / module_dir)

        files = []
        if os.path.isdir(search_dir):
            for name in sorted(os.listdir(search_dir)):
                if pattern.match(name):
                    files.append(os.path.join(search_dir, name))
        return [path for path in files if os.path.exists(path)]

    def _targets(target_modules=None):
        if target_modules is None:
            return list(MODULE_SCRIPTS.keys())
        if isinstance(target_modules, str):
            return [target_modules]
        return list(target_modules)

    for module_type in _targets(target_modules):
        if module_type in INITIALIZED_REGISTRIES:
            continue
        registry = MODULETYPE_TO_REGISTRIES[module_type]
        for path in _module_files(module_type):
            try:
                specs = _scan_file(path, module_type)
            except Exception as e:
                import warnings as _w

                _w.warn(f"Lazy-registry scan failed for {path}: {e}")
                continue
            for spec in specs:
                registry.register_lazy_module(spec)
        INITIALIZED_REGISTRIES.add(module_type)
