# -*- coding: utf-8 -*-
"""只读探针：063.jpg 的既有块与「待替换」块到底是什么，以及 ysgyolo 在同一裁剪里
检出多少（验证替换判据在真机上打的是"重复/大框"，而不是误伤正常块）。"""

import io
import os
import sys

APP = r"D:\ruanjian\BallonsTranslator-lite"
PROJ = r"D:\汉化\施工区副本"
sys.path.insert(0, APP)
os.chdir(APP)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np  # noqa: E402

import utils.shared as shared  # noqa: E402
from utils import config as program_config  # noqa: E402

program_config.load_config()
from utils.config import pcfg  # noqa: E402
from utils.lazy_registry import init_lazy_module_registries  # noqa: E402
from utils.proj_imgtrans import ProjImgTrans  # noqa: E402

init_lazy_module_registries(None)

from utils.registries import TEXTDETECTORS  # noqa: E402
from ui.region_redetect import RegionRedetect  # noqa: E402

buf = io.StringIO()


def P(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    buf.write(line + "\n")


def main():
    proj = ProjImgTrans(directory=PROJ)
    name = "063.jpg"
    proj.set_current_img(name)
    blocks = proj.pages[name]
    P("=== %s 既有 %d 块 ===" % (name, len(blocks)))
    for i, b in enumerate(blocks):
        P("  [%02d] xyxy=%-26s det=%-14s v=%-5s angle=%-5s text=%r trans=%r"
          % (i, b.xyxy, b.det_model, b.src_is_vertical, b.angle,
             b.get_text()[:24], (b.translation or "")[:20]))
    P("")

    # ysgyolo 在同一裁剪里的检出（对照）
    ysg_name = pcfg.module.textdetector
    ysg_class = TEXTDETECTORS.resolve_module(ysg_name)
    ysg = ysg_class(**(pcfg.module.get_params("textdetector").get(ysg_name) or {}))
    rect = [350, 265, 690, 435]
    task = RegionRedetect(proj, detector=ysg, detector_name=ysg_name)
    plan = task.plan(name, rect)
    P("=== 同一区域用 ysgyolo（%s）再检测 ===" % ysg_name)
    P("  检出 %d 块（区域外剔除 %d）待替换 %s"
      % (len(plan.new_blocks), plan.dropped_outside, plan.replaced_indices))
    for i, b in enumerate(plan.new_blocks):
        P("    [%d] xyxy=%s angle=%s" % (i, b.xyxy, b.angle))
    P("")

    # ppocrv6 的替换对象几何说明
    pp_class = TEXTDETECTORS.resolve_module("ppocrv6_onnx")
    pp = pp_class(**(pcfg.module.get_params("textdetector").get("ppocrv6_onnx") or {}))
    task = RegionRedetect(proj, detector=pp, detector_name="ppocrv6_onnx")
    plan = task.plan(name, rect)
    report = task.build_page(plan)
    P("=== ppocrv6 替换判据命中的既有块（min 面积比 > 0.5）===")
    for idx in report["replaced"]:
        b = blocks[idx]
        P("  [%02d] xyxy=%-26s det=%-14s 面积=%d  text=%r"
          % (idx, b.xyxy, b.det_model,
             (b.xyxy[2] - b.xyxy[0]) * (b.xyxy[3] - b.xyxy[1]), b.get_text()[:30]))
    P("")
    P("=== 新块几何 ===")
    for i, b in enumerate(plan.new_blocks):
        P("  [%d] xyxy=%-26s 面积=%d  angle=%s font=%s"
          % (i, b.xyxy,
             (b.xyxy[2] - b.xyxy[0]) * (b.xyxy[3] - b.xyxy[1]), b.angle, b.font_size))
    P("")
    P("kept=%d added=%d inserted=%s" % (report["kept"], report["added"], report["inserted"]))
    P("新块在列表中的位置: %s"
      % [(i, b.xyxy) for i, b in enumerate(report["blocks"])
         if any(b is nb for nb in plan.new_blocks)])

    with io.open(os.path.join(APP, "tmp", "rr_probe.out"), "w",
                 encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("saved tmp/rr_probe.out")


if __name__ == "__main__":
    main()
