# -*- coding: utf-8 -*-
"""区域再检测：真机验收（方案 §七 的第 1、2 条，只读）。

在**可写的工作副本**上跑 ``ui/region_redetect.py::RegionRedetect.plan()``——
只读，不写项目的 JSON／掩码（写回路径由 tests/test_region_redetect.py 的合成
项目覆盖）。真实检测器 ＋ 真实 OCR 模块，核对：

  063.jpg (350,265)-(690,435)   期望 5~6 块，全部倾斜四边形、angle 非 0
  047.jpeg (560,1200)-(900,1325) 期望 3 块
  047.jpeg (140,340)-(440,445)   期望 2 块

输出写 tmp/rr_accept.out。
"""

import io
import os
import sys
import time

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

from utils.registries import OCR, TEXTDETECTORS  # noqa: E402
from ui.region_redetect import RegionRedetect, RedetectConfig  # noqa: E402

DET = "ppocrv6_onnx"
OCR_NAME = "paddleocr_v6_onnx"

REGIONS = [
    ("063.jpg", [350, 265, 690, 435], "手机屏（5 行横排 UI 字）"),
    ("047.jpeg", [560, 1200, 900, 1325], "底部 3 行横排旁白"),
    ("047.jpeg", [140, 340, 440, 445], "左中 2 行横排旁白"),
]

buf = io.StringIO()


def P(*args):
    line = " ".join(str(a) for a in args)
    print(line)
    buf.write(line + "\n")


def main():
    P("config: textdetector=%s ocr=%s region_redetect_detector=%s"
      % (pcfg.module.textdetector, pcfg.module.ocr,
         pcfg.region_redetect_detector))

    det_class = TEXTDETECTORS.resolve_module(DET)
    det = det_class(**(pcfg.module.get_params("textdetector").get(DET) or {}))
    P("detector:", det.name)
    ocr_class = OCR.resolve_module(OCR_NAME)
    ocr = ocr_class(**(pcfg.module.get_params("ocr").get(OCR_NAME) or {}))
    P("ocr:", ocr.name, "|", OCR_NAME, "from config:",
      pcfg.module.ocr)

    proj = ProjImgTrans(directory=PROJ)
    P("proj pages:", len(proj.pages))
    P("")

    for name, rect, label in REGIONS:
        if name not in proj.pages:
            P("!! 页不在项目里:", name)
            continue
        proj.set_current_img(name)
        task = RegionRedetect(proj, detector=det, detector_name=DET)
        before = len(proj.pages[name])
        t0 = time.time()
        plan = task.plan(name, rect)
        dt = time.time() - t0
        P("=== %s %s ===" % (name, label))
        P("   区域 %s  裁剪 %s  耗时 %.2fs  页内已有块 %d"
          % (rect, plan.crop_rect, dt, before))
        P("   skip=%s  检出 %d 块（区域外剔除 %d）  待替换 %s"
          % (plan.skip, len(plan.new_blocks), plan.dropped_outside,
             plan.replaced_indices))
        angles, sizes, models = [], [], set()
        for blk in plan.new_blocks:
            angles.append(round(float(blk.angle), 1))
            sizes.append(int(blk.font_size))
            models.add(blk.det_model)
        P("   angle:", angles)
        P("   font_size(量出来的):", sizes)
        P("   det_model:", sorted(models))
        P("   mask 非零像素:", int((plan.mask > 0).sum()) if plan.mask is not None else None)
        if plan.ok:
            t1 = time.time()
            ocr.run_ocr(proj.img_array, plan.new_blocks, split_textblk=True)
            P("   OCR 耗时 %.2fs" % (time.time() - t1))
            for i, blk in enumerate(plan.new_blocks):
                P("     [%d] xyxy=%s angle=%s v=%s tags=%s"
                  % (i, blk.xyxy, blk.angle, blk.src_is_vertical,
                     sorted(blk.tags.keys())))
                P("         text=%r" % (blk.get_text()[:80],))
            report = task.build_page(plan)
            P("   build_page: kept=%d added=%d replaced=%s direction=%s"
              % (report["kept"], report["added"], report["replaced"],
                 report["direction"]))
            P("   inserted(idx, why):", report["inserted"])
            P("   新块列表 xyxy: %s"
              % [b.xyxy for b in report["blocks"]])
        P("")

    with io.open(os.path.join(APP, "tmp", "rr_accept.out"), "w",
                 encoding="utf-8") as f:
        f.write(buf.getvalue())
    print("saved tmp/rr_accept.out")


if __name__ == "__main__":
    main()
