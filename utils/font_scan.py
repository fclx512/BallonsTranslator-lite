"""字体名称表扫描与家族名归并（Photoshop 对齐的显示名）。

``QFontDatabase.families()`` 按字体名称表 nameID 1（GDI 兼容家族名）枚举，
由此产生两类重复：同一字体的每个字重常被注册成独立家族（如
``MiSans VF Bold``），同一字体的中英文名也各占一项（``Microsoft YaHei``
与 ``微软雅黑``）。Photoshop 的字体菜单则按"家族 + 字重样式"组织，中文
系统显示本地化名。本模块在枚举期读字体文件的 name 表，把这些重复归并成
单一规范名，并建立"任意家族名 → 精确 PostScript 名"索引供 PSD 导出使用。

归并只在"face 集完全一致"或"字重后缀家族且基家族确实拥有该字重"时发生，
被隐藏的别名仍是真实 Qt 家族名：旧项目数据按原名存储/渲染不受影响，
仅下拉列表与排除过滤按规范名呈现。
"""

import glob
import os
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

# Windows name 表 langID（canonical 规范名优先级用）
LANG_ZH_CN = 0x0804
LANG_EN_US = 0x0409

_SFNT_EXTS = (".ttf", ".otf", ".ttc")

# 字重后缀 → Qt 字重整数（与上游 _FONT_WEIGHT_SUFFIXES 同语义）
_FONT_WEIGHT_SUFFIXES = (
    ("extra light", 200),
    ("extra bold", 800),
    ("semi bold", 600),
    ("demi bold", 600),
    ("extralight", 200),
    ("extrabold", 800),
    ("semibold", 600),
    ("demibold", 600),
    ("regular", 400),
    ("normal", 400),
    ("medium", 500),
    ("light", 300),
    ("black", 900),
    ("bold", 700),
    ("thin", 100),
)


# 100-900 标准字重（就近分桶目标，与上游 coerce_font_weight 同语义）
_CANONICAL_WEIGHTS = (100, 200, 300, 400, 500, 600, 700, 800, 900)


def bucket_weight(weight: int) -> int:
    """就近归到 100-900 标准字重（与 ui/text_engine/font_weight.py 的
    ``coerce_font_weight`` 分桶语义一致；utils 不反向依赖 ui，此处复制）。

    DirectWrite 报告的实际字重常偏离名字暗示值（如 ``MiSans VF Bold``
    实际 630），按名字比较会漏归并，分桶后才与上游行为一致。
    """
    if 100 <= weight <= 1000:
        return min(_CANONICAL_WEIGHTS, key=lambda c: abs(c - weight))
    return weight


def split_weight_family_name(family: str) -> Tuple[Optional[str], Optional[int]]:
    """仅当家族名以已识别的字重词结尾时拆出 (基家族, 字重)。

    >>> split_weight_family_name('Inter Display SemiBold')
    ('Inter Display', 600)
    >>> split_weight_family_name('Blackadder ITC') is None
    True
    """
    folded = family.casefold()
    for suffix, weight in _FONT_WEIGHT_SUFFIXES:
        marker = f" {suffix}"
        if folded.endswith(marker):
            return family[: -len(marker)], weight
    return None, None


def weight_suffix_aliases(
    families: Sequence[str],
    styles_of: Callable[[str], Sequence[str]],
    weight_of: Callable[[str, str], int],
) -> Dict[str, str]:
    """识别"基家族 + 字重"后缀别名，返回 {别名家族: 基家族}。

    仅当基家族存在、其样式覆盖该字重、且别名家族自身只有这一个字重的
    face 时才归并（与上游 ``_weight_family_aliases`` 同语义），避免把
    真正的独立家族（如恰好叫 X Bold 的无字重字体）误并。
    """
    by_folded = {f.casefold(): f for f in families}
    weights_by_family: Dict[str, Set[int]] = {}
    aliases: Dict[str, str] = {}

    def family_weights(family: str) -> Set[int]:
        if family not in weights_by_family:
            weights_by_family[family] = {
                weight_of(family, s) for s in styles_of(family)
            }
        return weights_by_family[family]

    for alias in families:
        base_name, weight = split_weight_family_name(alias)
        if base_name is None:
            continue
        base = by_folded.get(base_name.casefold())
        if base is None:
            continue
        base_weights = {bucket_weight(w) for w in family_weights(base)}
        alias_weights = {bucket_weight(w) for w in family_weights(alias)}
        if weight in base_weights and alias_weights == {weight}:
            aliases[alias] = base
    return aliases


def scan_font_faces(font_dirs: Optional[List[str]] = None) -> List[dict]:
    """读取字体文件 name 表，返回 face 记录列表。

    每条记录 ``{"ps": PostScript 名, "weight": OS/2 字重,
    "names": {家族名: {langID 集}}}``（家族名含 nameID 1 与 16）。
    依赖 fontTools；不可用或目录不存在时返回空列表，调用方按
    "无扫描数据"降级（仅做字重后缀归并）。
    """
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except Exception:
        return []

    faces: List[dict] = []
    for d in font_dirs if font_dirs is not None else _default_font_dirs():
        for path in sorted(glob.glob(os.path.join(d, "*"))):
            if not path.lower().endswith(_SFNT_EXTS):
                continue
            try:
                if path.lower().endswith(".ttc"):
                    fonts = TTCollection(path, lazy=True).fonts
                else:
                    fonts = [TTFont(path, lazy=True)]
            except Exception:
                continue
            for f in fonts:
                face = _face_record(f)
                if face:
                    faces.append(face)
    return faces


def _default_font_dirs() -> List[str]:
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    user_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts")
    if user_dir:
        dirs.append(user_dir)
    return [d for d in dirs if os.path.isdir(d)]


def _face_record(font) -> Optional[dict]:
    """提取 face 的 PostScript 名、OS/2 字重与家族名集合。

    家族名同时收 nameID 1（GDI 兼容家族）与 nameID 16（排版家族）：
    Qt/DirectWrite 会把两者各列一项（如 ``RiiPopkaku`` 与
    ``RiiPopkaku-R``），必须纳入才能按 face 归并。结构为
    ``{名字: {langID 集}}``——同一 langID 下 1/16 可能给出不同字符串。
    """
    try:
        name_table = font["name"]
        weight = int(font["OS/2"].usWeightClass)
    except Exception:
        return None
    ps_name = None
    names: Dict[str, Set[int]] = {}
    for rec in name_table.names:
        if rec.platformID != 3 or rec.nameID not in (1, 6, 16):
            continue
        try:
            text = rec.toUnicode().strip()
        except Exception:
            continue
        if not text:
            continue
        if rec.nameID == 6:
            if ps_name is None:
                ps_name = text
        else:
            names.setdefault(text, set()).add(rec.langID)
    if not ps_name or not names:
        return None
    return {"ps": ps_name, "weight": weight, "names": names}


def group_localized_aliases(
    families: Sequence[str], faces: Sequence[dict]
) -> Dict[str, str]:
    """把指向同一组 face 的多语言家族名归并到规范名。

    canonical 优先取 zh-CN 名（对齐中文系统上 Photoshop 的显示与本项目
    数据惯用名），其次 en-US 名，最后最短名。返回 {别名: 规范名}。
    name 表键是 strip 过的字符串，Qt 家族名可能带尾随空格（攸望系
    ``攸望圆体（简繁）Medium ``），按 rstrip 后的名字查 face。
    """
    fam_set = set(families)
    name_faces: Dict[str, Set[str]] = {}
    name_langs: Dict[str, Set[int]] = {}
    for face in faces:
        for name, lang_ids in face["names"].items():
            name_faces.setdefault(name, set()).add(face["ps"])
            name_langs.setdefault(name, set()).update(lang_ids)

    by_faces: Dict[frozenset, List[str]] = {}
    for name in fam_set:
        ps_set = name_faces.get(name.rstrip())
        if ps_set:
            by_faces.setdefault(frozenset(ps_set), []).append(name)

    aliases: Dict[str, str] = {}
    for members in by_faces.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda n: (
                LANG_ZH_CN not in name_langs.get(n.rstrip(), set()),
                LANG_EN_US not in name_langs.get(n.rstrip(), set()),
                len(n),
                n,
            )
        )
        canonical = members[0]
        for alias in members[1:]:
            aliases[alias] = canonical
    return aliases


def resolve_alias_chains(alias_to_canonical: Dict[str, str]) -> Dict[str, str]:
    """收敛二级别名（字重别名指向的基家族又本身是语言别名）。"""
    resolved = {}
    for alias in alias_to_canonical:
        target = alias
        seen = set()
        while target in alias_to_canonical and target not in seen:
            seen.add(target)
            target = alias_to_canonical[target]
        resolved[alias] = target
    return resolved


def merge_families(
    families: Sequence[str],
    styles_of: Callable[[str], Sequence[str]],
    weight_of: Callable[[str, str], int],
    faces: Sequence[dict],
) -> dict:
    """对给定家族列表执行默认归并（上游字重语义 + 语言名 face 归并）。

    供 ``build_font_data`` 与 ``compute_simplify_map`` 共用；键见
    ``build_font_data``，另含 ``alias_to_canonical``。
    """
    weight_alias = weight_suffix_aliases(families, styles_of, weight_of)
    survivors = [f for f in families if f not in weight_alias]
    loc_alias = group_localized_aliases(survivors, faces)

    alias_to_canonical = dict(weight_alias)
    alias_to_canonical.update(loc_alias)
    alias_to_canonical = resolve_alias_chains(alias_to_canonical)

    display = sorted(f for f in survivors if f not in alias_to_canonical)

    # 样式表覆盖所有真实家族名：旧数据按别名存储时仍能回显字重列表
    styles: Dict[str, List[str]] = {}
    for family in families:
        seen: Dict[str, str] = {}
        for style in styles_of(family):
            key = style.strip()
            if key and key not in seen:
                seen[key] = style
        styles[family] = sorted(
            seen.values(), key=lambda s: (weight_of(family, s), s)
        )

    # name → {字重: PS 名}；字重别名不在 name 表里（可变字体命名实例），
    # 借用其规范名的 face 集
    face_by_name: Dict[str, Dict[int, str]] = {}
    for face in faces:
        for name in face["names"]:
            face_by_name.setdefault(name, {}).setdefault(
                face["weight"], face["ps"]
            )
    ps_index: Dict[str, Dict[int, str]] = {}
    for family in families:
        source = face_by_name.get(family)
        if source is None:
            source = face_by_name.get(
                alias_to_canonical.get(family, "").rstrip()
            )
        if source:
            ps_index[family] = source

    canonical_to_aliases: Dict[str, List[str]] = {}
    for alias, canonical in alias_to_canonical.items():
        canonical_to_aliases.setdefault(canonical, []).append(alias)

    return {
        "display_families": display,
        "alias_to_canonical": alias_to_canonical,
        "styles": styles,
        "canonical_to_aliases": canonical_to_aliases,
        "ps_index": ps_index,
    }


def build_font_data() -> dict:
    """枚举 Qt 家族并完成全部默认归并，返回 ``shared.init_font_list`` 所需数据。

    返回键：
    - ``display_families``: 归并后的规范名（排序），即下拉列表内容
    - ``alias_to_canonical``: {被隐藏别名: 规范名}
    - ``styles``: **所有**真实 Qt 家族名（含被隐藏别名）→ 各自样式列表
    - ``canonical_to_aliases``: 规范名 → 被隐藏的别名列表
    - ``ps_index``: 任意家族名（规范名+别名）→ {OS/2 字重: PostScript 名}
    """
    from qtpy.QtGui import QFontDatabase

    families = [f for f in QFontDatabase.families() if not f.startswith("@")]

    def styles_of(family: str) -> Sequence[str]:
        return QFontDatabase.styles(family)

    def weight_of(family: str, style: str) -> int:
        return int(QFontDatabase.weight(family, style))

    return merge_families(families, styles_of, weight_of, scan_font_faces())


# 「一键精简」在共享后缀表基础上补充的词项（上游无：heavy/ultra 系）
_SIMPLIFY_EXTRA_SUFFIXES = {
    ("heavy", 800),
    ("ultra light", 200),
    ("ultralight", 200),
    ("ultra bold", 800),
    ("ultrabold", 800),
}
# 长词优先，避免 'Foo SemiBold' 被 'bold' 先截断成 'Foo Semi'
_SIMPLIFY_SUFFIXES = sorted(
    set(_FONT_WEIGHT_SUFFIXES) | _SIMPLIFY_EXTRA_SUFFIXES,
    key=lambda item: -len(item[0]),
)


def compute_simplify_map(
    families: Optional[Sequence[str]] = None,
    faces: Optional[Sequence[dict]] = None,
    styles_of: Optional[Callable[[str], Sequence[str]]] = None,
    weight_of: Optional[Callable[[str, str], int]] = None,
) -> Dict[str, str]:
    """本项目私有「一键精简」规则：返回 {可隐藏家族名: 规范名}。

    比默认归并激进（按需求优先，不对齐上游），结果经用户确认后写入
    ``pcfg.simplified_font_map``，默认行为不受影响：

    1. 『基家族+字重后缀』一律并入基家族——后缀词表补 heavy/ultralight
       等，后缀前不要求空格（攸望系 ``（简繁）Bold``）、忽略尾随空格，
       且不要求实际字重与名字一致，只要基家族样式列表里有同名字样
       （选中基家族后该字重仍可从样式下拉选到）。
    2. face 集被另一存活家族包含的语言名变体并入后者（如 'YW YuanTi'
       的 face 全部包含于『攸望圆体（简繁）』）。

    守卫：别名 face 集必须是目标家族 face 集的子集（face 数据缺失时
    跳过该守卫——可变字体命名实例家族在 name 表里没有记录）。
    结果只含默认归并后仍显示的家族名。
    """
    from qtpy.QtGui import QFontDatabase

    if families is None:
        families = [f for f in QFontDatabase.families() if not f.startswith("@")]
    if faces is None:
        faces = scan_font_faces()
    if styles_of is None:
        styles_of = QFontDatabase.styles
    if weight_of is None:

        def weight_of(family: str, style: str) -> int:
            return int(QFontDatabase.weight(family, style))

    default = merge_families(families, styles_of, weight_of, faces)
    display_set = set(default["display_families"])

    by_folded = {f.casefold(): f for f in families}
    name_faces: Dict[str, Set[str]] = {}
    name_langs: Dict[str, Set[int]] = {}
    for face in faces:
        for name, lang_ids in face["names"].items():
            name_faces.setdefault(name, set()).add(face["ps"])
            name_langs.setdefault(name, set()).update(lang_ids)

    def face_set(name: str) -> Optional[frozenset]:
        ps_set = name_faces.get(name.rstrip())
        return frozenset(ps_set) if ps_set else None

    def covers(base: str, suffix: str) -> bool:
        return any(
            s.strip().casefold() == suffix for s in styles_of(base)
        )

    raw: Dict[str, str] = {}
    # 规则一：基家族+字重后缀（宽松拆分 + 样式同名覆盖 + face 子集守卫）
    for alias in families:
        stripped = alias.rstrip()
        folded = stripped.casefold()
        hit = next(
            ((s, w) for s, w in _SIMPLIFY_SUFFIXES if folded.endswith(s)),
            None,
        )
        if hit is None:
            continue
        suffix, _weight = hit
        base = by_folded.get(stripped[: -len(suffix)].rstrip().casefold())
        if base is None or base == alias:
            continue
        if not covers(base, suffix):
            continue
        alias_faces, base_faces = face_set(alias), face_set(base)
        if alias_faces is not None and base_faces is not None:
            if not alias_faces <= base_faces:
                continue
        raw[alias] = base

    # 规则二：face 集包含关系归并语言名变体
    def pref_key(n: str):
        return (
            LANG_ZH_CN not in name_langs.get(n.rstrip(), set()),
            LANG_EN_US not in name_langs.get(n.rstrip(), set()),
            len(n),
            n,
        )

    survivors = [(f, face_set(f)) for f in families if f not in raw]
    for fam, fam_faces in survivors:
        if not fam_faces:
            continue
        best = None
        for other, other_faces in survivors:
            if other == fam or not other_faces:
                continue
            contained = other_faces > fam_faces or (
                other_faces == fam_faces and pref_key(other) < pref_key(fam)
            )
            if contained and (best is None or pref_key(other) < pref_key(best)):
                best = other
        if best is not None:
            raw[fam] = best

    combined = dict(default["alias_to_canonical"])
    combined.update(raw)
    resolved = resolve_alias_chains(combined)
    return {
        alias: resolved[alias] for alias in raw if alias in display_set
    }
