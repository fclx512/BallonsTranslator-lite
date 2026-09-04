from typing import List

from qtpy.QtGui import QFont

from utils import face_resolver
from utils.fontformat import FontFormat, TextTransformState, px2pt

from . import shared_widget as SW
from .textitem import TextBlkItem

global_default_set_kwargs = dict(set_selected=False, restore_cursor=False)
local_default_set_kwargs = dict(set_selected=True, restore_cursor=True)

# 触发 face 派生重算的属性字段（font_weight 单一真值，face 为派生缓存）
_FACE_SYNC_FIELDS = ("font_weight", "font_family", "italic")


def _sync_active_face(act_ffmt: FontFormat, param_name: str) -> None:
    if param_name in _FACE_SYNC_FIELDS:
        face_resolver.sync_face(act_ffmt)


def _mirror_to_global_format(act_ffmt: FontFormat, param_name: str, value) -> None:
    """新块默认跟随最近编辑（选中态编辑镜像写入 global_format）。

    闲置/多选态的编辑本就落在 global_format（全局通道）；此处补齐单选
    （及多选镜像副本）通道，对齐 PS「工具默认跟随最近编辑」语义。
    """
    manager = getattr(SW, "st_manager", None)
    panel = getattr(manager, "formatpanel", None) if manager is not None else None
    gf = getattr(panel, "global_format", None) if panel is not None else None
    if gf is None or gf is act_ffmt or not hasattr(gf, param_name):
        return
    gf[param_name] = value
    _sync_active_face(gf, param_name)


def _active_formatpanel():
    manager = getattr(SW, "st_manager", None)
    return getattr(manager, "formatpanel", None) if manager is not None else None


def wrap_fntformat_input(values: str, blkitems: List[TextBlkItem], is_global: bool):
    if is_global:
        blkitems = SW.canvas.selected_text_items()
    else:
        if not isinstance(blkitems, List):
            blkitems = [blkitems]
    values = [values] * len(blkitems)
    return blkitems, values


def font_formating(is_property=True):

    def func_wrapper(formatting_func):

        def wrapper(
            param_name: str,
            values: str,
            act_ffmt: FontFormat,
            is_global: bool,
            blkitems: List[TextBlkItem] = None,
            set_focus: bool = False,
            *args,
            **kwargs,
        ):
            if is_global and is_property:
                if hasattr(act_ffmt, param_name):
                    act_ffmt[param_name] = values
                    _sync_active_face(act_ffmt, param_name)
                else:
                    print(f"undefined param name: {param_name}")

            blkitems, values = wrap_fntformat_input(values, blkitems, is_global)
            if len(blkitems) > 0:
                if is_property:
                    act_ffmt[param_name] = values[0]
                    _sync_active_face(act_ffmt, param_name)
                    if not is_global:
                        _mirror_to_global_format(act_ffmt, param_name, values[0])
                # 3a 格式化手势：块级格式化变更统一并入画布手势会话，闭合时以
                # 「基线↔终值」一条 FormatGestureCommand 落账（一次手势一步）。
                # 效果类 setter（描边/阴影/透明/行距/字距）不触发
                # on_content_changed 以自登记，故在此显式登记（幂等）。
                # 隔离调用（无画布，如单测直调 ffmt_change_*）跳过手势、仅应用。
                panel = _active_formatpanel()
                canvas = getattr(SW, "canvas", None)
                if canvas is not None and hasattr(canvas, "note_formatting_edit"):
                    for blkitem in blkitems:
                        canvas.note_formatting_edit(blkitem, panel)
                formatting_func(
                    param_name,
                    values,
                    act_ffmt,
                    is_global,
                    blkitems,
                    *args,
                    **kwargs,
                )
            if set_focus:
                if not SW.canvas.hasFocus():
                    SW.canvas.setFocus()

        return wrapper

    return func_wrapper


@font_formating()
def ffmt_change_font_family(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontFamily(value, **set_kwargs)
        # 数据层即时同步 + 逐块 sync_face：每块按自身 weight/italic 映射
        # 新家族 face（face 缺失就近/回落由 resolve_face 兜底），不再单值
        # 广播活动格式的 _style_name
        blkitem.fontformat.font_family = value
        face_resolver.sync_face(blkitem.fontformat)


@font_formating()
def ffmt_change_italic(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontItalic(value, **set_kwargs)


@font_formating()
def ffmt_change_underline(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontUnderline(value, **set_kwargs)


@font_formating()
def ffmt_change_strikeout(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontStrikeOut(value, **set_kwargs)


@font_formating()
def ffmt_change_font_weight(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontWeight(value, **set_kwargs)


@font_formating()
def ffmt_change_bold(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem] = None,
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    values = [QFont.Weight.Bold if value else QFont.Weight.Normal for value in values]
    # ffmt_change_weight('weight', values, act_ffmt, is_global, blkitems, **kwargs)
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontWeight(value, **set_kwargs)


@font_formating()
def ffmt_change_letter_spacing(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setLetterSpacing(value, **set_kwargs)


@font_formating()
def ffmt_change_line_spacing(
    param_name: str,
    values: str,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setLineSpacing(value, **set_kwargs)


@font_formating()
def ffmt_change_vertical(
    param_name: str,
    values: bool,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    for blkitem, value in zip(blkitems, values):
        blkitem.setVertical(value)


@font_formating()
def ffmt_change_frgb(
    param_name: str,
    values: tuple,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setFontColor(value, **set_kwargs)


@font_formating()
def ffmt_change_srgb(
    param_name: str,
    values: tuple,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setStrokeColor(value, **set_kwargs)


@font_formating()
def ffmt_change_stroke_width(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        blkitem.setStrokeWidth(value, **set_kwargs)


@font_formating()
def ffmt_change_stroke_color_custom(
    param_name: str,
    values,  # bool: True=手动自定义, False=自动跟随文字前景反色
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    for blkitem, value in zip(blkitems, values):
        blkitem.setStrokeColorCustom(bool(value))


@font_formating()
def ffmt_change_font_size(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    clip_size=False,
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        if value <= 0:
            continue
        value = px2pt(value)
        blkitem.setFontSize(value, clip_size=clip_size, **set_kwargs)


@font_formating(is_property=False)
def ffmt_change_rel_font_size(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    clip_size=False,
    **kwargs,
):
    set_kwargs = global_default_set_kwargs if is_global else local_default_set_kwargs
    for blkitem, value in zip(blkitems, values):
        if value <= 0:
            continue
        blkitem.setRelFontSize(value, clip_size=clip_size, **set_kwargs)


@font_formating()
def ffmt_change_alignment(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    restore_cursor = not is_global
    for blkitem, value in zip(blkitems, values):
        blkitem.setAlignment(value, restore_cursor=restore_cursor)


@font_formating()
def ffmt_change_opacity(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    for blkitem, value in zip(blkitems, values):
        blkitem.setOpacity(value)


@font_formating()
def ffmt_change_line_spacing_type(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    restore_cursor = not is_global
    for blkitem, value in zip(blkitems, values):
        blkitem.setLineSpacingType(value, restore_cursor=restore_cursor)


@font_formating()
def ffmt_change_punctuation_alignment(
    param_name: str,
    values: int,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    """Deprecated: punctuation alignment is now a global setting (ConfigPanel).
    This function is a no-op kept for backward compatibility with funcmaps."""
    pass


@font_formating(is_property=False)
def ffmt_change_text_transform(
    param_name: str,
    values,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    """Apply a whole transform stack snapshot to each text item.

    Snapshot-style commit (no undo entry by design): the geometry controller
    persists the state into both the model (``blk.fontformat``) and the render
    ``fontformat``. The full editable/undoable path is provided by
    ``SetTextTransformCommand`` via the transform editor (Stage 5 UI).
    """
    for blkitem, value in zip(blkitems, values):
        state = TextTransformState(
            value, blkitem.fontformat.glyph_slant_angle
        )
        blkitem.set_text_transform(state, preview=False)


@font_formating(is_property=False)
def ffmt_change_glyph_slant_angle(
    param_name: str,
    values,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    """Apply a glyph slant angle snapshot to each text item.

    See :func:`ffmt_change_text_transform` for the snapshot semantics.
    """
    for blkitem, value in zip(blkitems, values):
        state = TextTransformState(
            blkitem.fontformat.text_transform, value
        )
        blkitem.set_text_transform(state, preview=False)


@font_formating()
def ffmt_change_shadow_offset(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    for blkitem, value in zip(blkitems, values):
        blkitem.setBGAttribute(param_name, value)


@font_formating()
def ffmt_change_gradient_enabled(
    param_name: str,
    values: float,
    act_ffmt: FontFormat,
    is_global: bool,
    blkitems: List[TextBlkItem],
    **kwargs,
):
    for blkitem, value in zip(blkitems, values):
        blkitem.setGradientAttribute(param_name, value)


ffmt_change_shadow_radius = ffmt_change_shadow_offset
ffmt_change_shadow_strength = ffmt_change_shadow_offset
ffmt_change_shadow_color = ffmt_change_shadow_offset
ffmt_change_shadow_include_stroke = ffmt_change_shadow_offset

ffmt_change_gradient_start_color = ffmt_change_gradient_enabled
ffmt_change_gradient_end_color = ffmt_change_gradient_enabled
ffmt_change_gradient_angle = ffmt_change_gradient_enabled
ffmt_change_gradient_size = ffmt_change_gradient_enabled
