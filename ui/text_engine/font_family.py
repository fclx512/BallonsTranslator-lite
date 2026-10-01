"""Qt font-family compatibility at the text-engine boundary."""

from hashlib import sha1
import re
from typing import Callable, Iterable, Sequence

from qtpy.QtGui import QFont, QFontDatabase, QTextCursor, QTextDocument


_QT_FAMILY_BY_PROJECT_NAME: dict[str, str] = {}
_PROJECT_FAMILY_BY_QT_NAME: dict[str, str] = {}
_FONT_FAMILY_DECLARATION = re.compile(
    r'font-family\s*:[^;>]*',
    re.IGNORECASE,
)

# Qt 注册家族名的两种键：精确名集合 + 空白折叠名→精确名。项目数据里的
# 家族名可能因字体元数据自带首尾空格、或各版本归并口径不同，与 Qt 注册
# 名差在空白上；QFont 按名精确匹配，差一个空格就整体失配回退默认字体。
# 索引只建一次，字体库未就绪（尚无 QApplication）时留待下次调用再建。
_QT_FAMILY_EXACT: set[str] = set()
_QT_FAMILY_BY_NORM_KEY: dict[str, str] = {}
_NORM_INDEX_READY = False


def _norm_key(value: str) -> str:
    return ' '.join(value.casefold().split())


def _ensure_qt_family_index() -> None:
    global _NORM_INDEX_READY
    if _NORM_INDEX_READY:
        return
    families = QFontDatabase.families()
    if not families:
        return
    _QT_FAMILY_EXACT.update(families)
    for family in families:
        _QT_FAMILY_BY_NORM_KEY.setdefault(_norm_key(family), family)
    _NORM_INDEX_READY = True


def _safe_font_family_alias(family: str, used_names: set[str]) -> str:
    """Return a stable family name that Qt cannot parse as foundry syntax.

    >>> alias = _safe_font_family_alias('[toolbox]BuDing-JF', set())
    >>> alias.startswith('BalloonsTranslator Font ') and '[' not in alias
    True
    """
    digest = sha1(family.encode('utf-8')).hexdigest()[:12]
    base = f'BalloonsTranslator Font {digest}'
    alias = base
    suffix = 2
    while alias.casefold() in used_names:
        alias = f'{base} {suffix}'
        suffix += 1
    return alias


def register_qt_font_family_aliases(
    font_families: Iterable[str],
    style_names: Callable[[str], Sequence[str]],
) -> dict[str, str]:
    """Register safe aliases for bracketed families Qt cannot select.

    Qt treats square brackets in a family name as foundry syntax. Some real
    fonts begin with a bracketed vendor tag, so Qt advertises their family but
    returns no styles and resolves a different face when that name is used.
    """
    families = tuple(font_families)
    used_names = {family.casefold() for family in families}
    used_names.update(_PROJECT_FAMILY_BY_QT_NAME)
    registered: dict[str, str] = {}
    for family in sorted(families, key=str.casefold):
        key = family.casefold()
        if key in _QT_FAMILY_BY_PROJECT_NAME:
            registered[family] = _QT_FAMILY_BY_PROJECT_NAME[key]
            continue
        if '[' not in family and ']' not in family:
            continue
        if style_names(family):
            # A newer Qt/font backend may learn to handle this name directly.
            continue
        alias = _safe_font_family_alias(family, used_names)
        QFont.insertSubstitution(alias, family)
        _QT_FAMILY_BY_PROJECT_NAME[key] = alias
        _PROJECT_FAMILY_BY_QT_NAME[alias.casefold()] = family
        used_names.add(alias.casefold())
        registered[family] = alias
    return registered


def font_family_for_qt(family: str) -> str:
    """Return the internal family name Qt can resolve correctly."""
    resolved = _QT_FAMILY_BY_PROJECT_NAME.get(family.casefold())
    if resolved is not None:
        return resolved
    if not family or family in _QT_FAMILY_EXACT:
        return family
    _ensure_qt_family_index()
    # 差在空白/大小写上的名字映射回真实注册名；映射不到（字体未装、
    # 名字真不存在）时原样返回，交回 Qt 既有回退行为，不臆造替换。
    return _QT_FAMILY_BY_NORM_KEY.get(_norm_key(family), family)


def font_family_for_project(family: str) -> str:
    """Return the stable user/project-facing name for an internal family."""
    return _PROJECT_FAMILY_BY_QT_NAME.get(family.casefold(), family)


def qfont_with_family(font: QFont, family: str) -> QFont:
    """Copy ``font`` and safely set any project-facing family."""
    resolved = font_family_for_qt(family)
    result = QFont(font)
    # Qt 5 can retain an HTML font's old family list after setFamily(), while
    # family() can retain its old value after setFamilies(). Set both so the
    # renderer and the persisted/UI-facing accessor agree on one family.
    result.setFamilies([resolved])
    result.setFamily(resolved)
    return result


def normalize_document_font_families(document: QTextDocument) -> int:
    """Rewrite unresolvable font families in a live document to Qt-safe names.

    覆盖两类：内部别名（``_QT_FAMILY_BY_PROJECT_NAME``，当前无注册方）与
    差在空白/大小写上的家族名变体。家族名本可解析的片段原样跳过、不改
    一个字节，因此对正常文档零写入。
    """
    replacements = 0
    default_font = document.defaultFont()
    resolved_default = font_family_for_qt(default_font.family())
    if resolved_default != default_font.family():
        document.setDefaultFont(
            qfont_with_family(default_font, default_font.family())
        )
        replacements += 1

    cursor = QTextCursor(document)
    block = document.firstBlock()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            char_format = fragment.charFormat()
            family = char_format.font().family()
            if font_family_for_qt(family) != family:
                char_format.setFont(
                    qfont_with_family(char_format.font(), family)
                )
                cursor.setPosition(fragment.position())
                cursor.setPosition(
                    fragment.position() + fragment.length(),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.setCharFormat(char_format)
                replacements += 1
            iterator += 1
        block = block.next()
    return replacements


def restore_project_font_families_in_html(html: str) -> str:
    """Hide internal aliases in serialized ``font-family`` declarations."""
    aliases = _QT_FAMILY_BY_PROJECT_NAME.values()
    if not any(alias in html for alias in aliases):
        return html

    def restore_declaration(match: re.Match) -> str:
        declaration = match.group(0)
        for alias in aliases:
            declaration = declaration.replace(
                alias,
                _PROJECT_FAMILY_BY_QT_NAME[alias.casefold()],
            )
        return declaration

    return _FONT_FAMILY_DECLARATION.sub(restore_declaration, html)
