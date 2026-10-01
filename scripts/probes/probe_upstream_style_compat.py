"""上游 ↔ fork 字体/样式数据交叉往返探针（纯数据层，不开 GUI）。

在同进程内同时挂两套包：fork 走 `utils.*`（本仓库），上游走
`ballontranslator.*`（D:\\ruanjian\\BallonsTranslator，工作树含未提交的
FontRegistry/效果栈新架构）。对每条方向做「构造 → 对方加载 → 再序列化」，
打印两侧差异裁决。

用法（须用 fork 嵌入解释器，cwd 任意）：
  ./ballontrans_pylibs_win/python.exe scripts/probes/probe_upstream_style_compat.py upstream-to-fork
  ./ballontrans_pylibs_win/python.exe scripts/probes/probe_upstream_style_compat.py fork-to-upstream
  ./ballontrans_pylibs_win/python.exe scripts/probes/probe_upstream_style_compat.py all
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPSTREAM_ROOT = os.environ.get(
    "BT_UPSTREAM_ROOT", r"D:\ruanjian\BallonsTranslator"
)
for p in (REPO_ROOT, UPSTREAM_ROOT):  # fork 优先，utils 归 fork、ballontranslator 归上游
    if p not in sys.path:
        sys.path.insert(0, p)


def j(obj, cls=None):
    return json.loads(json.dumps(obj, cls=cls, ensure_ascii=False))


def build_upstream_block():
    """用上游当前架构构造一个带全部新特性的块。"""
    from ballontranslator.utils.fontformat import (
        BendTextTransform,
        FontFormat,
        TextTransformStack,
    )
    from ballontranslator.utils.text_effects import (
        StrokeEffect,
        SyntheticBoldEffect,
        TextEffectStack,
    )
    from ballontranslator.utils.textblock import TextBlock

    ff = FontFormat(
        font_family="思源黑体 CN",
        font_size=32.0,
        font_weight=700,
        alignment=1,
        vertical=True,
        line_spacing=1.2,
        _style_name="预设甲",
    )
    ff.text_effects = TextEffectStack(
        effects=(
            StrokeEffect(width=0.3),
            SyntheticBoldEffect(x=0.02, y=0.02),
        )
    )
    ff.text_transform = TextTransformStack((BendTextTransform(bend=0.25),))
    blk = TextBlock(
        xyxy=[10, 10, 210, 110],
        lines=[[[10, 10], [210, 10], [210, 110], [10, 110]]],
        text=["原文"],
        translation="译文",
        rich_text='<p style="font-weight:700"><span style=" font-weight:700;">译文</span></p>',
    )
    blk.fontformat = ff
    return blk


def build_fork_block():
    """用 fork 当前架构构造一个块（含 fork-only 字段与 synthetic_bold 透传）。"""
    from utils.fontformat import (
        BendTextTransform,
        FontFormat,
        TextTransformStack,
    )
    from utils.text_effects import (
        StrokeEffect,
        TextEffectStack,
        coerce_text_effect,
        coerce_text_effect_stack,
    )
    from utils.textblock import TextBlock

    ff = FontFormat(
        font_family="思源黑体 CN",
        font_size=32.0,
        font_weight=700,
        alignment=1,
        vertical=True,
        line_spacing=1.2,
        _style_name="预设甲",
    )
    stack = coerce_text_effect_stack(
        {
            "overall_opacity": 1.0,
            "effects": [
                {"effect_type": "stroke", "enabled": True, "width": 0.3},
                {
                    "effect_type": "synthetic_bold",
                    "enabled": True,
                    "shape": "ellipse",
                    "x": 0.02,
                    "y": 0.02,
                },
            ],
        }
    )
    ff.text_effects = stack
    ff.text_transform = TextTransformStack((BendTextTransform(bend=0.25),))
    blk = TextBlock(
        xyxy=[10, 10, 210, 110],
        lines=[[[10, 10], [210, 10], [210, 110], [10, 110]]],
        text=["原文"],
        translation="译文",
        rich_text='<p style="font-weight:700"><span style=" font-weight:700;">译文</span></p>',
    )
    blk.fontformat = ff
    blk.tags = ["onomatopoeia"]
    return blk


def upstream_project_dict(blk_dict):
    d = os.path.join(REPO_ROOT, ".tmp_compat", "fake_proj")
    os.makedirs(d, exist_ok=True)
    img = os.path.join(d, "001.png")
    if not os.path.exists(img):
        import cv2
        import numpy as np

        cv2.imwrite(img, np.full((1400, 1000, 3), 255, np.uint8))
    return {
        "directory": d,
        "pages": {"001.png": [blk_dict]},
        "current_img": "001.png",
        "image_info": {"001.png": {"width": 1000, "height": 1400}},
    }


def fork_project_dict(blk_dict):
    from utils.base_styles import BaseStyle
    from utils.fontformat import FontFormat

    d = os.path.join(REPO_ROOT, ".tmp_compat", "fake_proj")
    os.makedirs(d, exist_ok=True)
    img = os.path.join(d, "001.png")
    if not os.path.exists(img):
        import cv2
        import numpy as np

        cv2.imwrite(img, np.full((1400, 1000, 3), 255, np.uint8))
    ff = blk_dict["fontformat"]
    bs = BaseStyle(
        ff["font_family"] if isinstance(ff, dict) else ff.font_family,
        FontFormat(**ff) if isinstance(ff, dict) else ff,
    )
    return {
        "directory": d,
        "pages": {"001.png": [blk_dict]},
        "current_img": "001.png",
        "image_info": {"001.png": {"width": 1000, "height": 1400}},
        "base_styles": [bs.to_dict()],
        "llm_compact_memory": "测试记忆",
    }


def describe_ff(ff):
    """两侧通用的 FontFormat 摘要。"""
    out = {
        "font_family": getattr(ff, "font_family", None),
        "font_weight": getattr(ff, "font_weight", None),
        "_style_name": getattr(ff, "_style_name", None),
        "alignment": getattr(ff, "alignment", None),
        "vertical": getattr(ff, "vertical", None),
        "line_spacing": getattr(ff, "line_spacing", None),
    }
    eff = getattr(ff, "text_effects", None)
    if eff is None:
        out["effects"] = None
    else:
        effs = list(eff)
        out["effects"] = [
            {"type": type(e).__name__, "dict": e.to_serializable_dict()} for e in effs
        ]
    tt = getattr(ff, "text_transform", None)
    try:
        out["transform"] = [type(t).__name__ + ":" + json.dumps(t.to_serializable_dict() if hasattr(t, "to_serializable_dict") else vars(t), ensure_ascii=False, default=str) for t in (tt or [])]
    except Exception as exc:
        out["transform"] = f"<err {exc}>"
    return out


def run_upstream_to_fork():
    print("=" * 30, "方向 A：上游构造 → fork 加载", "=" * 30)
    from utils.proj_imgtrans import ProjImgTrans, TextBlkEncoder
    from utils.textblock import TextBlock as ForkBlock

    from ballontranslator.utils.proj_imgtrans import TextBlkEncoder as UpEncoder

    up_blk = build_upstream_block()
    up_blk_dict = j(up_blk, cls=UpEncoder)
    print("[上游落盘块 dict 键]", sorted(up_blk_dict.keys()))
    ff_d = up_blk_dict.get("fontformat", {})
    print("[上游 fontformat 键]", sorted(ff_d.keys()) if isinstance(ff_d, dict) else type(ff_d))
    print("[上游 text_effects]", ff_d.get("text_effects"))
    print("[上游 text_transform]", ff_d.get("text_transform"))
    print("[上游 text_layout_version]", up_blk_dict.get("text_layout_version"))

    print("\n--- fork 逐块加载 TextBlock(**dict) ---")
    fk = ForkBlock(**up_blk_dict)
    print(json.dumps(describe_ff(fk.fontformat), ensure_ascii=False, indent=1))
    print("[fork tags]", getattr(fk, "tags", None))

    print("\n--- fork 再落盘（TextBlkEncoder）检查 synthetic_bold 透传 ---")
    re = j(fk, cls=TextBlkEncoder)
    re_eff = re["fontformat"]["text_effects"]
    print("[fork 再落盘 text_effects]", json.dumps(re_eff, ensure_ascii=False))
    orig = ff_d.get("text_effects", {})
    re_types = [e.get("effect_type") for e in re_eff.get("effects", [])]
    orig_types = [e.get("effect_type") for e in orig.get("effects", [])]
    print("[裁决] effect_type 序列 上游=%s fork回吐=%s -> %s" % (
        orig_types, re_types, "一致" if orig_types == re_types else "丢失/变形"))

    print("\n--- fork 工程级加载 ProjImgTrans.load_from_dict ---")
    proj = ProjImgTrans()
    pdict = upstream_project_dict(up_blk_dict)
    proj.directory = pdict["directory"]
    proj.load_from_dict(pdict)
    print("[loaded_without_base_styles]", getattr(proj, "loaded_without_base_styles", "N/A"))
    print("[base_styles]", [b.to_dict().get("name") for b in getattr(proj, "base_styles", [])])
    print("[pages keys]", list(getattr(proj, "pages", {}).keys()))
    print("[llm_compact_memory]", repr(getattr(proj, "llm_compact_memory", "N/A")))


def run_fork_to_upstream():
    print("=" * 30, "方向 B：fork 构造 → 上游加载", "=" * 30)
    from utils.proj_imgtrans import TextBlkEncoder
    from utils.textblock import TextBlock as ForkBlock

    fk_blk = build_fork_block()
    fk_blk_dict = j(fk_blk, cls=TextBlkEncoder)
    print("[fork 落盘块 dict 键]", sorted(fk_blk_dict.keys()))
    ff_d = fk_blk_dict["fontformat"]
    print("[fork 落盘 alignment]", ff_d.get("alignment"), "| vertical:", ff_d.get("vertical"), "| text_layout_version 键存在:", "text_layout_version" in fk_blk_dict)
    print("[fork text_effects]", json.dumps(ff_d.get("text_effects"), ensure_ascii=False))

    from ballontranslator.utils.textblock import (
        TextBlock as UpBlock,
        normalize_textblock_effect_payload,
    )

    normalized, notices = normalize_textblock_effect_payload(fk_blk_dict)
    print("[normalize notices]", notices)
    print("\n--- 上游逐块加载 ---")
    ub = UpBlock(**normalized)
    print("[text_layout_version]", getattr(ub, "text_layout_version", "N/A"))
    print("[alignment after load (vertical=%s)]" % getattr(ub, "vertical", None),
          ub.fontformat.alignment)
    print(json.dumps(describe_ff(ub.fontformat), ensure_ascii=False, indent=1))
    da = getattr(ub, "deprecated_attributes", None)
    print("[deprecated_attributes 键]", sorted(da.keys()) if isinstance(da, dict) else da)
    print("[tags 去向]", "deprecated_attributes" if isinstance(da, dict) and "tags" in da else getattr(ub, "tags", "<无此字段>"))

    print("\n--- 上游再落盘检查 synthetic_bold 是否幸存 ---")
    from ballontranslator.utils.proj_imgtrans import TextBlkEncoder as UpEncoder

    re = j(ub, cls=UpEncoder)
    re_eff = re["fontformat"]["text_effects"]
    re_types = [e.get("effect_type") for e in re_eff.get("effects", [])]
    orig_types = [e.get("effect_type") for e in ff_d["text_effects"]["effects"]]
    print("[上游回吐 effect_type 序列] 上游=%s <- fork=%s -> %s" % (
        re_types, orig_types, "一致" if re_types == orig_types else "丢失/变形"))

    print("\n--- 上游工程级加载（带 fork 独有的 base_styles/llm_compact_memory 键）---")
    from ballontranslator.utils.proj_imgtrans import ProjImgTrans as UpProj

    pdict = fork_project_dict(fk_blk_dict)
    try:
        p = UpProj()
        p.directory = pdict["directory"]
        p.load_from_dict(pdict)
        print("[上游 load_from_dict 成功], pages keys:", list(p.pages.keys()))
        blk0 = p.pages["001.png"][0]
        print("[上游加载后 alignment]", blk0.fontformat.alignment,
              "| text_layout_version:", getattr(blk0, "text_layout_version", "N/A"))
    except Exception as exc:
        print("[上游 load_from_dict 失败]", type(exc).__name__, exc)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("upstream-to-fork", "all"):
        run_upstream_to_fork()
    if mode in ("fork-to-upstream", "all"):
        run_fork_to_upstream()


if __name__ == "__main__":
    main()
