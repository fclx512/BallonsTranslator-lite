import argparse
import os
import os.path as osp
import sys

sys.path.append(osp.dirname(osp.dirname(__file__)))

from tqdm import tqdm

from modules import (
    MODULETYPE_TO_REGISTRIES,
    init_textdetector_registries,
)
from utils.config import load_config, pcfg
from utils.io_utils import imwrite
from utils.proj_imgtrans import ProjImgTrans
from utils.shared import PROGRAM_PATH
from utils.textblock import visualize_textblocks

os.chdir(PROGRAM_PATH)


def init_module(module_type: str, module_name: str):
    assert module_type in MODULETYPE_TO_REGISTRIES
    module_cls = MODULETYPE_TO_REGISTRIES[module_type].get(module_name)
    module_cls_params = getattr(pcfg.module, module_type + "_params")
    module_params = module_cls_params.get(module_name, {})
    return module_cls(**module_params)


def run_detector(proj_dir, detector, config, save_dir):

    init_textdetector_registries()
    load_config(config)
    if detector is None:
        detector = pcfg.module.textdetector

    detector = init_module("textdetector", detector)
    print("detector params:", detector.params)

    proj = ProjImgTrans(proj_dir)
    for page_name in tqdm(proj.pages):
        blk_list = proj.pages[page_name]
        proj.set_current_img(page_name)
        mask, blk_list = detector.detect(proj.img_array, blk_list)
        blk_list = blk_list[:1]
        print(blk_list[0].get_text())
        vis = visualize_textblocks(proj.img_array, blk_list)
        imwrite(osp.join(save_dir, proj.current_img), vis, ext=".jpg")
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="text detector testing scripts.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run_detector")
    p.add_argument("--proj_dir", required=True)
    p.add_argument("--detector", default=None)
    p.add_argument("--config", default="config/config.json")
    p.add_argument("--save_dir", default="tmp/test_ctd")

    args = parser.parse_args()
    if args.command == "run_detector":
        run_detector(args.proj_dir, args.detector, args.config, args.save_dir)
    else:
        parser.print_help()
