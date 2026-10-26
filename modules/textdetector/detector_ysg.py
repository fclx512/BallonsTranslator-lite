"""
YSG-YOLO 文字检测模块

原作者仓库:
  - HuggingFace 模型下载: https://huggingface.co/YSGforMTL/YSGYoloDetector
  - GitHub 训练源码:     https://github.com/lhj5426/YSG
  - Telegram 频道:       https://t.me/yinshuguan
"""

import importlib.util
import os
import os.path as osp
from copy import deepcopy
from typing import List, Optional, Tuple

if importlib.util.find_spec("torch") is None:
    raise ImportError("PyTorch not available")
import cv2
import numpy as np

from utils.imgproc_utils import xywh2xyxypoly
from utils.proj_imgtrans import ProjImgTrans
from utils.textblock import (
    examine_textblk,
    mit_merge_textlines,
    sort_pnts,
    sort_regions,
)

from .base import DEVICE_SELECTOR, TextBlock, TextDetectorBase, register_textdetectors

MODEL_DIR = "data/models"
YSGYOLO_MODEL_PREFIXES = ("ysgyolo", "ultralyticsyolo")

CTD_ONNX_PATH = "data/models/comictextdetector.pt.onnx"
CTD_TORCH_PATH = "data/models/comictextdetector.pt"

# 内置权重排在本地扫描结果之前：保存的路径失效时，回退必须落在主模型上，
# 而不是按文件名字典序先撞到 OBB 权重（OBB 会把大字判成 other）。
DEFAULT_CKPT_NAMES = (
    "ysgyolo_yolo26_2.0.pt",
    "ysgyolo_yolo26OBB_2.0.pt",
)


def find_model_paths(model_dir, prefixes) -> List[str]:
    """Return model filenames in ``model_dir``, bundled defaults first.

    返回值是裸文件名（相对 ``data/models``）：检测器存盘值与下拉选项都沿用它，
    加载时再拼 ``MODEL_DIR``。内置默认即使文件不在盘上也列出来，供下载目标与
    惰性元数据共用。

    Example:
        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     _ = Path(tmp, 'ysgyolo_demo.pt').write_text('')
        ...     find_model_paths(tmp, ('ysgyolo',))[2]
        'ysgyolo_demo.pt'
    """
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    ordered = [name for name in DEFAULT_CKPT_NAMES if name.startswith(tuple(prefixes))]
    if not osp.exists(model_dir):
        return ordered
    for name in sorted(os.listdir(model_dir)):
        if name.startswith(tuple(prefixes)) and name not in ordered:
            ordered.append(name)
    return ordered


def update_ckpt_list() -> List[str]:
    CKPT_LIST[:] = find_model_paths(MODEL_DIR, YSGYOLO_MODEL_PREFIXES)
    return CKPT_LIST


# 直接写成调用表达式（不是先赋空列表再原地刷新）：惰性 AST 扫描会执行
# ``find_model_paths``，否则下拉选项在导入模块体之前读到的是空列表。
CKPT_LIST = find_model_paths(MODEL_DIR, YSGYOLO_MODEL_PREFIXES)
update_ckpt_list()


# Module-level metadata so detector variants can share one SafeEval-compatible
# params/download template instead of duplicating it.
_YSGYOLO_PARAMS = {
    "model path": {
        "type": "selector",
        "options": CKPT_LIST,
        "value": DEFAULT_CKPT_NAMES[0],
        "editable": True,
        "flush_btn": True,
        "path_selector": True,
        "path_filter": "*.pt *.ckpt *.pth *.safetensors",
        "size": "short",
        "display_name": "Model Path",
        "description": "Path to the YSGYolo model checkpoint file",
    },
    "merge text lines": {
        "display_name": "Merge Text Lines",
        "type": "checkbox",
        "value": True,
        "description": "Merge adjacent text fragments into single text blocks",
    },
    "confidence threshold": {
        "display_name": "Confidence Threshold",
        "type": "line_editor",
        "value": 0.3,
        "description": "Minimum confidence score for accepting a text detection",
    },
    "IoU threshold": {
        "display_name": "IoU Threshold",
        "type": "line_editor",
        "value": 0.5,
        "description": "Intersection-over-Union threshold for merging overlapping detection boxes",
    },
    "font size multiplier": {
        "display_name": "Font Size Multiplier",
        "type": "line_editor",
        "value": 1.0,
        "description": "Scale factor applied to detected font size",
    },
    "font size max": {
        "display_name": "Max Font Size",
        "type": "line_editor",
        "value": -1,
        "description": "Maximum allowed font size (-1 for unlimited)",
    },
    "font size min": {
        "display_name": "Min Font Size",
        "type": "line_editor",
        "value": -1,
        "description": "Minimum allowed font size (-1 for unlimited)",
    },
    "detect size": {
        "display_name": "Detection Size",
        "type": "line_editor",
        "value": 1024,
        "description": "Input image size for text detection",
    },
    "extract colors": {
        "display_name": "Extract Colors",
        "type": "checkbox",
        "value": True,
        "description": "Extract per-block font colors from the image (classical binarization; this detector has no prob map)",
    },
    "device": {
        **DEVICE_SELECTOR(),
        "display_name": "Device",
    },
    "label": {
        # other 默认关闭：它拉的是覆盖整个气泡的**气泡级框**，而不只是多一层
        # 遮罩——下面走 mit_merge_textlines 时该框按文字框一样收进合并池，
        # 于是多出若干"整只气泡"的 TextBlock 会被 OCR／翻译／渲染；对修复
        # 侧则是遮罩把裁剪窗吃光、简单背景判据直接判不出。要不要用它的
        # "气泡本身"信息另议。
        "value": {
            "balloon": True,
            "qipao": True,
            "shuqing": True,
            "changfangtiao": True,
            "hengxie": True,
            "other": False,
        },
        "type": "check_group",
        "display_name": "Label",
        "description": "Text region types to detect",
    },
    "source text is vertical": {
        "display_name": "Vertical Text",
        "type": "checkbox",
        "value": True,
        "description": "Assume source text is written vertically (right-to-left)",
    },
    "mask dilate size": {
        "display_name": "Mask Expansion Size",
        "type": "line_editor",
        "value": 2,
        "description": "Dilation kernel size for text region mask",
    },
}

_YSGYOLO_DOWNLOAD_FILE_LIST = [
    {
        "url": "https://huggingface.co/YSGforMTL/YSGYoloDetector/resolve/main/",
        "files": "data/models/ysgyolo_yolo26_2.0.pt",
        "concatenate_url_filename": 2,
    },
]

# Mirrors the ComicTextDetector download entry so the hybrid detector can
# prepare both model families without depending on the CTD detector.
_CTD_DOWNLOAD_FILE_LIST = [
    {
        "url": "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/",
        "files": [
            CTD_TORCH_PATH,
            CTD_ONNX_PATH,
        ],
        "sha256_pre_calculated": [
            "1f90fa60aeeb1eb82e2ac1167a66bf139a8a61b8780acd351ead55268540cccb",
            "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f",
        ],
        "concatenate_url_filename": 2,
    },
]


def _dilate_mask(mask: np.ndarray, ksize: int) -> np.ndarray:
    if ksize > 0:
        element = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * ksize + 1, 2 * ksize + 1), (ksize, ksize)
        )
        mask = cv2.dilate(mask, element)
    return mask


def _build_box_mask(blk_list: List[TextBlock], im_w: int, im_h: int) -> np.ndarray:
    """Draw the union of all detected text polygons without dilation."""

    mask = np.zeros((im_h, im_w), np.uint8)
    for blk in blk_list:
        for line in np.asarray(blk.lines, dtype=np.int32):
            cv2.fillPoly(mask, [line.reshape(-1, 2)], 255)
    return mask


def _bbox_overlap_ratio(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = [float(v) for v in box_a]
    bx1, by1, bx2, by2 = [float(v) for v in box_b]
    inter_w = min(ax2, bx2) - max(ax1, bx1)
    inter_h = min(ay2, by2) - max(ay1, by1)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    area_a = max(ax2 - ax1, 0.0) * max(ay2 - ay1, 0.0)
    if area_a <= 0:
        return 0.0
    return inter_w * inter_h / area_a


def _combine_refined_mask(
    box_mask: np.ndarray,
    fine_mask: np.ndarray,
    blk_list: List[TextBlock],
    ctd_blk_list: List[TextBlock],
    ctd_overlap_ratio: float = 0.25,
) -> np.ndarray:
    """Keep CTD's glyph mask inside YOLO boxes, with per-line coarse fallback.

    A YOLO text line whose region was not detected/refined by CTD keeps its
    coarse box mask so the replacement text is never left unmasked.

    Example:
        >>> box = np.zeros((6, 6), np.uint8)
        >>> box[1:5, 1:5] = 255
        >>> fine = np.zeros_like(box)
        >>> fine[2:3, 2:4] = 255
        >>> blk = TextBlock(lines=[[[1, 1], [5, 1], [5, 5], [1, 5]]])
        >>> blk.adjust_bbox()
        >>> ctd_blk = TextBlock(xyxy=[1, 1, 5, 5])
        >>> combined = _combine_refined_mask(box, fine, [blk], [ctd_blk])
        >>> int(combined[2, 2]), int(combined[1, 1])
        (255, 0)
    """

    combined = cv2.bitwise_and(fine_mask, box_mask)
    im_h, im_w = box_mask.shape[:2]
    for blk in blk_list:
        for line in np.asarray(blk.lines, dtype=np.int32):
            pts = line.reshape(-1, 2)
            x1, y1 = pts[:, 0].min(), pts[:, 1].min()
            x2, y2 = pts[:, 0].max() + 1, pts[:, 1].max() + 1
            x1, y1 = max(int(x1), 0), max(int(y1), 0)
            x2, y2 = min(int(x2), im_w), min(int(y2), im_h)
            if x2 <= x1 or y2 <= y1:
                continue
            box_crop = box_mask[y1:y2, x1:x2]
            if not np.any(box_crop):
                continue
            line_bbox = [x1, y1, x2, y2]
            ctd_refined = any(
                _bbox_overlap_ratio(line_bbox, ctd_blk.xyxy) >= ctd_overlap_ratio
                for ctd_blk in ctd_blk_list
            )
            if ctd_refined:
                crop = combined[y1:y2, x1:x2]
                if np.any(crop):
                    continue
            local_pts = pts - np.array([x1, y1], dtype=np.int32)
            line_mask = np.zeros((y2 - y1, x2 - x1), np.uint8)
            cv2.fillPoly(line_mask, [local_pts], 255)
            line_region = cv2.bitwise_and(line_mask, box_crop)
            if np.any(line_region):
                combined[y1:y2, x1:x2] = cv2.bitwise_or(
                    combined[y1:y2, x1:x2], line_region
                )
    return combined


@register_textdetectors("ysgyolo")
class YSGYoloDetector(TextDetectorBase):
    # Shared metadata template: the plain detector keeps the live list objects
    # (model list flush updates them) while variants deep-copy their own.
    params = _YSGYOLO_PARAMS

    # ultralytics is not a core requirement (it pulls torch + matplotlib).
    # 只声明 requires_packages 一处：惰性 AST 扫描会把它带进 ModuleSpec，
    # 选型依赖提示（ui/module_manager.py::_build_dep_notes）、后台下载判定
    # （modules/__init__.py::GET_MISSING_PACKAGES）与加载期自动安装
    # （modules/base.py::BaseModule.ensure_dependencies）三处都读同一个字段。
    requires_packages = ["ultralytics"]

    _load_model_keys = {"model"}
    download_file_list = _YSGYOLO_DOWNLOAD_FILE_LIST

    def __init__(self, **params) -> None:
        super().__init__(**params)
        update_ckpt_list()

    def _load_model(self):
        update_ckpt_list()  # refresh model list in case files were downloaded after init
        model_path = self.get_param_value("model path")
        # If it's a bare filename (preset), prepend MODEL_DIR; otherwise use as-is (custom path)
        if not osp.isabs(model_path) and not model_path.startswith(MODEL_DIR):
            full_path = osp.join(MODEL_DIR, model_path).replace("\\", "/")
        else:
            full_path = model_path
        if not osp.exists(full_path):
            global CKPT_LIST
            df_model_path = full_path
            for p in CKPT_LIST:
                candidate = osp.join(MODEL_DIR, p).replace("\\", "/")
                if osp.exists(candidate):
                    df_model_path = candidate
                    break
            self.logger.warning(
                f"{model_path} does not exist, try fall back to default value {osp.basename(df_model_path)}"
            )
            full_path = df_model_path

        if "rtdetr" in os.path.basename(full_path):
            from ultralytics import RTDETR as MODEL
        else:
            from ultralytics import YOLO as MODEL
        if not hasattr(self, "model") or self.model is None:
            self.model = MODEL(full_path).to(device=self.get_param_value("device"))

    def get_valid_labels(self):
        return [k for k, v in self.params["label"]["value"].items() if v]

    @property
    def is_ysg(self):
        return osp.basename(self.get_param_value("model path").startswith("ysg"))

    def _detect(
        self, img: np.ndarray, proj: ProjImgTrans = None
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        result = self.model.predict(
            source=img,
            save=False,
            show=False,
            verbose=False,
            conf=self.get_param_value("confidence threshold"),
            iou=self.get_param_value("IoU threshold"),
            agnostic_nms=True,
        )[0]

        valid_labels = set(self.get_valid_labels())
        valid_ids = [idx for idx, name in result.names.items() if name in valid_labels]

        mask = np.zeros_like(img[..., 0])
        if not valid_ids:
            # 返回顺序必须是 (mask, blk_list)：混合检测器子类在父类结果上解包，
            # 顺序反了会让它把 ndarray 当 block 列表做布尔判断而抛 ValueError。
            return mask, []

        im_h, im_w = img.shape[:2]
        detected_items = []

        # Process standard boxes
        dets = result.boxes
        if dets is not None and len(dets.cls) > 0:
            # 整批一次搬到 CPU：逐框 .cpu() 每项都要等一次 GPU 同步
            # （上游 1.5.20 的 ysg_ctd 同款优化）。
            cls_all = dets.cls.cpu().numpy()
            xyxy_all = dets.xyxy.cpu().numpy().astype(int)
            for i in range(len(cls_all)):
                cls_idx = int(cls_all[i])
                if cls_idx in valid_ids:
                    label_name = result.names[cls_idx]

                    x1, y1, x2, y2 = xyxy_all[i]
                    cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
                    pts = (
                        xywh2xyxypoly(np.array([[x1, y1, x2 - x1, y2 - y1]]))
                        .reshape(4, 2)
                        .tolist()
                    )
                    detected_items.append({"pts": pts, "label": label_name})

        # Process oriented boxes
        dets = result.obb
        if dets is not None and len(dets.cls) > 0:
            cls_all = dets.cls.cpu().numpy()
            polys_all = dets.xyxyxyxy.cpu().numpy().astype(int)
            for i in range(len(cls_all)):
                cls_idx = int(cls_all[i])
                if cls_idx in valid_ids:
                    label_name = result.names[cls_idx]
                    pts = polys_all[i]
                    cv2.fillPoly(mask, [pts], 255)
                    detected_items.append({"pts": pts.tolist(), "label": label_name})

        blk_list = []
        if self.get_param_value("merge text lines"):
            pts_only_list = [item["pts"] for item in detected_items]
            blk_list = mit_merge_textlines(pts_only_list, width=im_w, height=im_h)
        else:
            for item in detected_items:
                pts_sorted, is_vertical = sort_pnts(item["pts"])
                blk = TextBlock(
                    lines=[pts_sorted], src_is_vertical=is_vertical, label=item["label"]
                )
                blk.vertical = is_vertical
                blk.adjust_bbox()
                examine_textblk(blk, im_w, im_h)
                blk_list.append(blk)

        blk_list = sort_regions(blk_list)

        fnt_rsz = self.get_param_value("font size multiplier")
        fnt_max = self.get_param_value("font size max")
        fnt_min = self.get_param_value("font size min")
        for blk in blk_list:
            sz = blk._detected_font_size * fnt_rsz
            if fnt_max > 0:
                sz = min(fnt_max, sz)
            if fnt_min > 0:
                sz = max(fnt_min, sz)
            blk.font_size = sz
            blk._detected_font_size = sz

        mask = _dilate_mask(mask, self.get_param_value("mask dilate size"))

        # 字体颜色提取：本检测器没有概率图，走经典二值化兜底（同一套判定逻辑）
        palette = getattr(proj, "fg_palette", None) if proj is not None else None
        self.extract_colors(img, blk_list, palette=palette)

        return mask, blk_list

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)

        if param_key == "model path":
            if hasattr(self, "model"):
                del self.model

    def flush(self, param_key: str):
        if param_key == "model path":
            update_ckpt_list()
            global CKPT_LIST
            return CKPT_LIST


@register_textdetectors("ysg_ctd")
class YSGYoloCTDDetector(YSGYoloDetector):
    """YOLO decides which text to process, CTD refines its glyph mask.

    YSGYOLO reliably separates body text from stylized onomatopoeia but only
    produces rectangular masks. CTD produces glyph-tight masks but also fires
    on onomatopoeia. This detector keeps the YOLO boxes as the allowed region
    and intersects them with CTD's refined mask, so SFX outside the boxes are
    ignored while replacement/OCR still uses the YOLO text blocks.

    The YOLO stage must behave exactly like the ``ysgyolo`` detector, otherwise
    a text block the user trusts ysgyolo to find would be dropped before CTD
    ever contributes. Its shared params are therefore seeded from the saved
    ysgyolo config by ``TEXTDETECTOR_PARAM_INHERITANCE``; only the CTD-specific
    params below are hybrid-only.
    """

    requires_packages = ["ultralytics", "einops"]

    # 掩膜是字形级而非矩形级，OCR 之后可由 utils/font_size_estimate.py
    # 反推原文字号；其它检测器不声明该能力，保持原字号逻辑不变。
    provides_glyph_mask = True

    _load_model_keys = {"model", "ctd_model"}

    params = {
        **deepcopy(_YSGYOLO_PARAMS),
        # 字形掩膜只覆盖深色笔画，文字的描边/白边不在掩膜内；2px 会留下
        # 文字形状的白影，8px 是普通描边的可靠档。这个值与 ysgyolo 的矩形
        # 掩膜需求不同，故不从基础检测器继承（见继承排除表）。
        "mask dilate size": {
            "display_name": "Mask Expansion Size",
            "type": "line_editor",
            "value": 8,
            "description": "Mask dilation in pixels. Glyph masks only cover the dark ink; outlined lettering needs a larger value (typically 8-20)",
        },
        "ctd detect size": {
            "display_name": "CTD Detect Size",
            "type": "selector",
            "options": [896, 1024, 1152, 1280],
            "value": 1280,
            "description": "Input image size for the ComicTextDetector mask refinement",
        },
        "ctd det rearrange max batches": {
            "display_name": "CTD Max Split Batch Size",
            "type": "selector",
            "options": [1, 2, 4, 6, 8, 12, 16, 24, 32],
            "value": 4,
            "description": "Maximum parallel batches when rearranging detected text blocks",
        },
        "description": "Detect text boxes with ysgyolo, then refine the mask with ComicTextDetector inside those boxes",
    }

    # Both model families are declared so selecting the hybrid prepares them.
    download_file_list = deepcopy(_YSGYOLO_DOWNLOAD_FILE_LIST) + _CTD_DOWNLOAD_FILE_LIST

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.ctd_model = None

    def _load_model(self):
        super()._load_model()
        self._load_ctd_model()

    def _load_ctd_model(self) -> None:
        from .ctd import CTDModel

        device = self.get_param_value("device")
        model_path = CTD_ONNX_PATH if device == "cpu" else CTD_TORCH_PATH
        self.ctd_model = CTDModel(
            model_path,
            detect_size=int(self.get_param_value("ctd detect size")),
            device=device,
            det_rearrange_max_batches=self.get_param_value(
                "ctd det rearrange max batches"
            ),
        )

    def _detect_fine_text_mask(
        self, img: np.ndarray
    ) -> Tuple[Optional[np.ndarray], List[TextBlock]]:
        if self.ctd_model is None:
            return None, []
        try:
            _, fine_mask, ctd_blk_list = self.ctd_model(img)
        except Exception as error:
            # YOLO block detection is still useful without mask refinement.
            self.logger.warning(
                f"CTD mask refinement failed, falling back to ysgyolo masks: {error}"
            )
            return None, []
        fine_mask = np.asarray(fine_mask).squeeze()
        if fine_mask.shape[:2] != img.shape[:2]:
            fine_mask = cv2.resize(
                fine_mask,
                (img.shape[1], img.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        fine_mask = np.where(fine_mask > 127, 255, 0).astype(np.uint8)
        return fine_mask, ctd_blk_list

    def _detect(
        self, img: np.ndarray, proj: ProjImgTrans = None
    ) -> Tuple[np.ndarray, List[TextBlock]]:
        mask, blk_list = super()._detect(img, proj)
        if not blk_list:
            return mask, blk_list
        fine_mask, ctd_blk_list = self._detect_fine_text_mask(img)
        if fine_mask is None:
            return mask, blk_list
        # 原始 CTD 精细掩膜只在这里存在；先量出字形指标供 OCR 后估字号，
        # 后面的合并/膨胀掩膜用于修复，不适合当字形尺寸来源。
        from utils.font_size_estimate import attach_glyph_font_metrics

        attach_glyph_font_metrics(blk_list, fine_mask)
        im_h, im_w = img.shape[:2]
        box_mask = _build_box_mask(blk_list, im_w, im_h)
        mask = _combine_refined_mask(box_mask, fine_mask, blk_list, ctd_blk_list)
        mask = _dilate_mask(mask, self.get_param_value("mask dilate size"))
        # 扩张会吃掉描边，但也可能越出检测框；裁回框内，避免污染框外背景。
        mask = cv2.bitwise_and(mask, box_mask)
        return mask, blk_list

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        if param_key == "device":
            # Both backends must be rebuilt on the newly selected device.
            for key in ("model", "ctd_model"):
                if hasattr(self, key):
                    setattr(self, key, None)
        elif param_key in {"ctd detect size", "ctd det rearrange max batches"}:
            # 两者都是 CTDModel 的构造参数，但可直接改运行中实例的属性——
            # 为一次尺寸变化重载整套权重没有意义（上游 1.5.20 同款做法）。
            if self.ctd_model is None:
                return
            try:
                value = int(param_content)
            except (TypeError, ValueError):
                # 非法值留给下次加载重新校验，不要带着坏属性继续推理。
                self.ctd_model = None
                return
            if param_key == "ctd detect size":
                self.ctd_model.detect_size = value
            else:
                self.ctd_model.det_rearrange_max_batches = value
