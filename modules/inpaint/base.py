from collections import OrderedDict
from typing import List

import cv2
import numpy as np

from utils.imgproc_utils import enlarge_window
from utils.registries import INPAINTERS
from utils.textblock_mask import extract_ballon_mask

from ..base import (
    BF16_SUPPORTED,
    DEFAULT_DEVICE,
    DEVICE_SELECTOR,
    TORCH_DTYPE_MAP,
    BaseModule,
    soft_empty_cache,
)
from ..textdetector import TextBlock

register_inpainter = INPAINTERS.register_module


def inpaint_handle_alpha_channel(original_alpha, mask):
    """
    perhaps a better idea is to feed the alpha into inpainting model, but it'll double the cost
    for now it just return the original alpha
    """

    result_alpha = original_alpha.copy()

    # Analyze the alpha values around the original mask to determine appropriate transparency
    mask_dilated = cv2.dilate(
        (mask > 127).astype(np.uint8), np.ones((15, 15), np.uint8), iterations=1
    )
    surrounding_mask = mask_dilated - (mask > 127).astype(np.uint8)

    if np.any(surrounding_mask > 0):
        surrounding_alpha = original_alpha[surrounding_mask > 0]
        if len(surrounding_alpha) > 0:
            median_surrounding_alpha = np.median(surrounding_alpha)
            # If surrounding area is mostly transparent (median alpha < 128),
            # make inpainted areas transparent too
            if median_surrounding_alpha < 128:
                inpainted_mask = mask > 127
                result_alpha[inpainted_mask] = median_surrounding_alpha

    return result_alpha


def block_local_mask(msk: np.ndarray, rect) -> np.ndarray:
    """只留与本块矩形相交的遮罩连通域（「块局部遮罩」）。

    裁剪窗口是块的 1.7 倍外扩，邻块的文字遮罩会一起落进来：遮罩包围盒被撑成
    跨块的并集，没有任何轮廓能"包住整个包围盒"⇒ ``extract_ballon_mask``
    围不出气泡 ⇒ 判不出（实测"明明是纯色气泡却判不出"的主因之一）。

    按**连通域**筛而不是按矩形硬裁：本块自己伸出矩形的笔画不会被切掉，筛不出
    任何相交域时（几何已错位）原样返回，不把情况改坏。``rect`` 是本块 ``xyxy``
    在裁剪区坐标下的矩形，可越界，函数内会夹到数组范围内。
    """
    height, width = msk.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in rect)
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return msk
    count, labels = cv2.connectedComponents(
        (msk > 0).astype(np.uint8), connectivity=8
    )
    if count <= 1:
        return msk
    inside = np.unique(labels[y1:y2, x1:x2])
    inside = inside[inside > 0]
    if inside.size == 0:
        return msk
    return np.where(np.isin(labels, inside), msk, 0).astype(msk.dtype)


def classify_simple(im: np.ndarray, msk: np.ndarray, blk_rect=None):
    """判定该块是否「简单背景」（可用纯色覆盖、无需修复模型）。

    判据链与管线逐块路径完全一致——``InpainterBase.inpaint`` 的逐块分支
    就是调用本函数，别处（批量任务 ``ui/batch_inpaint.py::BatchSimpleInpaint``
    的扫描与执行）也用它，判据单点维护：``utils/textblock_mask.py`` 的
    ``extract_ballon_mask`` 取出气泡掩码与非文字掩码 → 非文字像素的中位色
    与逐通道标准差 → ``std_max < inpaint_thresh``（阈值 7，整体标准差 ≤1
    时放宽到 10）即判为简单。

    Args:
        im: 块的裁剪图。**按 D34 一律传原图**——传已修过或手工改过的修复图
            会让复杂块被误判成"简单"而遭纯色覆盖，毁掉手工成果。
        msk: 同区域的文本遮罩裁剪（只读，本函数不改它）。
        blk_rect: 本块 ``xyxy`` 在裁剪区坐标下的矩形；给了就先做「块局部
            遮罩」（``block_local_mask``，D45）——裁剪区是 1.7 倍外扩，
            邻块遮罩会把包围盒撑成跨块并集而"判不出"。不给＝保持旧行为。

    Returns:
        ``(need_inpaint, ballon_msk, average_bg_color)``：

        - ``need_inpaint`` 为 True＝判据不满足（或判不出来），该块需走修复
          模型；为 False＝简单块，可用 ``average_bg_color`` 覆盖 ``ballon_msk``；
        - 后两项在判不出来时（裁剪区无文本像素、围不出气泡、无非文字像素）
          为 ``None``，调用方据此**跳过**该块。
    """
    if msk is None or not np.any(msk):
        # 裁剪区内没有文本遮罩像素：既围不出气泡也定不出背景色。既有实现会
        # 在 extract_ballon_mask 内部的 findNonZero→boundingRect 处抛异常，
        # 这里统一返回"需修复"（与 ballon_msk 为 None 同义），调用方跳过。
        return True, None, None
    if blk_rect is not None:
        msk = block_local_mask(msk, blk_rect)
        if not np.any(msk):
            return True, None, None
    ballon_msk, non_text_msk = extract_ballon_mask(im, msk)
    if ballon_msk is None or non_text_msk is None:
        return True, None, None
    non_text_px = im[np.where(non_text_msk > 0)]
    if non_text_px.shape[0] == 0:
        return True, None, None
    average_bg_color = np.median(non_text_px, axis=0)
    std_rgb = np.std(non_text_px - average_bg_color, axis=0)
    std_max = np.max(std_rgb)
    inpaint_thresh = 7 if np.std(std_rgb) > 1 else 10
    if std_max < inpaint_thresh:
        return False, ballon_msk, average_bg_color
    return True, ballon_msk, average_bg_color


class InpainterBase(BaseModule):
    inpaint_by_block = True
    check_need_inpaint = True

    _postprocess_hooks = OrderedDict()
    _preprocess_hooks = OrderedDict()

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.name = ""
        for key in INPAINTERS.module_dict:
            if INPAINTERS.module_dict[key] == self.__class__:
                self.name = key
                break

    def memory_safe_inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        """
        handle cuda out of memory
        """
        try:
            return self._inpaint(img, mask, textblock_list)
        except Exception as e:
            if DEFAULT_DEVICE == "cuda" and isinstance(e, TorchOOMError):
                soft_empty_cache()
                try:
                    return self._inpaint(img, mask, textblock_list)
                except Exception as ee:
                    if isinstance(ee, TorchOOMError):
                        self.logger.warning(
                            f"CUDA out of memory while calling {self.name}, fall back to cpu...\n\
                                            if running into it frequently, consider lowering the inpaint_size"
                        )
                        self.moveToDevice("cpu")
                        inpainted = self._inpaint(img, mask, textblock_list)
                        precision = None
                        if hasattr(self, "precision"):
                            precision = self.precision
                        self.moveToDevice("cuda", precision)

                        return inpainted
            else:
                raise e

    def inpaint(
        self,
        img: np.ndarray,
        mask: np.ndarray,
        textblock_list: List[TextBlock] = None,
        check_need_inpaint: bool = False,
        only_simple: bool = False,
    ) -> np.ndarray:
        """按块修复。

        默认行为（``only_simple=False``）：简单块纯色覆盖、其余块走修复模型。

        Args:
            check_need_inpaint: 本次调用启用「简单块」判定（管线侧开关，
                ``utils/config.py::ModuleConfig`` 的 ``check_need_inpaint``
                经类属性或运行对话框传进来）。
            only_simple: **只处理简单块**（工作台批量任务用，默认关闭）。
                开启时判据取真的块**一律跳过修复模型**（"复杂块完全不动"），
                取假的块照常纯色覆盖；判定需要的像素来自传入的 ``img``，
                故调用方须按 D34 传**原图**。此模式下**不加载模型**
                （纯色覆盖用不到模型，也不该为它拉几百 MB 权重）。
                仅逐块路径（``inpaint_by_block``）支持；判不出结果的块
                （见 ``classify_simple``）按跳过处理。
        """
        if not only_simple and not self.all_model_loaded():
            self.load_model()

        # Handle RGBA images by preserving alpha channel
        original_alpha = None
        if len(img.shape) == 3 and img.shape[2] == 4:
            original_alpha = img[:, :, 3:4]  # Keep alpha channel
            img_rgb = img[:, :, :3]  # Use only RGB for inpainting
        else:
            img_rgb = img

        if not self.inpaint_by_block or textblock_list is None:
            if check_need_inpaint:
                ballon_msk, non_text_msk = extract_ballon_mask(img_rgb, mask)
                if ballon_msk is not None:
                    non_text_region = np.where(non_text_msk > 0)
                    non_text_px = img_rgb[non_text_region]
                    average_bg_color = np.median(non_text_px, axis=0)
                    std_rgb = np.std(non_text_px - average_bg_color, axis=0)
                    std_max = np.max(std_rgb)
                    inpaint_thresh = 7 if np.std(std_rgb) > 1 else 10
                    if std_max < inpaint_thresh:
                        result_rgb = img_rgb.copy()
                        result_rgb[np.where(ballon_msk > 0)] = average_bg_color
                        # Recombine with alpha if original was RGBA
                        if original_alpha is not None:
                            return np.concatenate([result_rgb, original_alpha], axis=2)
                        return result_rgb
            result_rgb = self.memory_safe_inpaint(img_rgb, mask, textblock_list)
            # Recombine with alpha if original was RGBA
            if original_alpha is not None:
                result_alpha = inpaint_handle_alpha_channel(original_alpha, mask)
                return np.concatenate([result_rgb, result_alpha], axis=2)
            return result_rgb
        else:
            im_h, im_w = img_rgb.shape[:2]
            inpainted = np.copy(img_rgb)

            # Preserve original mask for transparency analysis
            original_mask = mask.copy()

            # only_simple 时判据必须生效（否则分不出简单／复杂块）
            judge_simple = self.check_need_inpaint or check_need_inpaint or only_simple

            for blk in textblock_list:
                xyxy = blk.xyxy
                xyxy_e = enlarge_window(xyxy, im_w, im_h, ratio=1.7)
                im = inpainted[xyxy_e[1] : xyxy_e[3], xyxy_e[0] : xyxy_e[2]]
                msk = mask[xyxy_e[1] : xyxy_e[3], xyxy_e[0] : xyxy_e[2]]
                need_inpaint = True
                if judge_simple:
                    # 本块 xyxy 在裁剪区坐标下的矩形（块局部遮罩，D45）
                    bx1, by1, bx2, by2 = xyxy
                    need_inpaint, ballon_msk, average_bg_color = classify_simple(
                        im,
                        msk,
                        (bx1 - xyxy_e[0], by1 - xyxy_e[1], bx2 - xyxy_e[0], by2 - xyxy_e[1]),
                    )
                    if not need_inpaint:
                        im[np.where(ballon_msk > 0)] = average_bg_color
                    # cv2.imshow('im', im)
                    # cv2.imshow('ballon', ballon_msk)
                    # cv2.imshow('non_text', non_text_msk)
                    # cv2.waitKey(0)

                # only_simple：判据取真的块（复杂块）保持原样，不调模型
                if need_inpaint and not only_simple:
                    inpainted[xyxy_e[1] : xyxy_e[3], xyxy_e[0] : xyxy_e[2]] = (
                        self.memory_safe_inpaint(im, msk)
                    )

                mask[xyxy[1] : xyxy[3], xyxy[0] : xyxy[2]] = 0

            # Recombine with alpha if original was RGBA
            if original_alpha is not None:
                result_alpha = inpaint_handle_alpha_channel(
                    original_alpha, original_mask
                )
                return np.concatenate([inpainted, result_alpha], axis=2)
            return inpainted

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        raise NotImplementedError

    def moveToDevice(self, device: str, precision: str = None):
        raise not NotImplementedError


try:
    import torch

    TorchOOMError = torch.cuda.OutOfMemoryError
except (ImportError, AttributeError):
    torch = None
    TorchOOMError = type("_NoTorchOOM", (Exception,), {})
if torch is not None:
    from utils.imgproc_utils import resize_keepasp

    from .lama import LamaFourier, load_lama_mpe

    class LamaInpainterMPE(InpainterBase):
        params = {
            "inpaint_size": {
                "type": "selector",
                "options": [1024, 2048],
                "value": 2048,
                "description": "Maximum image dimension for inpainting (larger images are resized)",
            },
            "device": DEVICE_SELECTOR(not_supported=["privateuseone"]),
        }

        download_file_list = [
            {
                "url": "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/inpainting_lama_mpe.ckpt",
                "sha256_pre_calculated": "d625aa1b3e0d0408acfd6928aa84f005867aa8dbb9162480346a4e20660786cc",
                "files": "data/models/lama_mpe.ckpt",
            }
        ]
        _load_model_keys = {"model"}

        def __init__(self, **params) -> None:
            super().__init__(**params)
            self.device = self.params["device"]["value"]
            self.inpaint_size = int(self.params["inpaint_size"]["value"])
            self.precision = "fp32"
            self.model: LamaFourier = None

        def _load_model(self):
            self.model = load_lama_mpe(r"data/models/lama_mpe.ckpt", self.device)

        def inpaint_preprocess(self, img: np.ndarray, mask: np.ndarray) -> np.ndarray:

            img_original = np.copy(img)
            mask_original = np.copy(mask)
            mask_original[mask_original < 127] = 0
            mask_original[mask_original >= 127] = 1
            mask_original = mask_original[:, :, None]

            new_shape = (
                self.inpaint_size if max(img.shape[0:2]) > self.inpaint_size else None
            )
            # high resolution input could produce cloudy artifacts
            img = resize_keepasp(img, new_shape, stride=None)
            mask = resize_keepasp(mask, new_shape, stride=None)

            im_h, im_w = img.shape[:2]
            # 对齐靠补边而不是重采样：保住网点与遮罩边缘（上游 b36210b）。
            longer = (max(im_h, im_w) + 63) // 64 * 64
            pad_bottom = longer - im_h
            pad_right = longer - im_w
            mask = cv2.copyMakeBorder(
                mask, 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT
            )
            img = cv2.copyMakeBorder(
                img, 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT
            )

            img_torch = (
                torch.from_numpy(img).permute(2, 0, 1).unsqueeze_(0).float() / 255.0
            )
            mask_torch = (
                torch.from_numpy(mask).unsqueeze_(0).unsqueeze_(0).float() / 255.0
            )
            mask_torch[mask_torch < 0.5] = 0
            mask_torch[mask_torch >= 0.5] = 1
            rel_pos, _, direct = self.model.load_masked_position_encoding(
                mask_torch[0][0].numpy()
            )
            rel_pos = torch.LongTensor(rel_pos).unsqueeze_(0)
            direct = torch.LongTensor(direct).unsqueeze_(0)

            if self.device != "cpu":
                img_torch = img_torch.to(self.device)
                mask_torch = mask_torch.to(self.device)
                rel_pos = rel_pos.to(self.device)
                direct = direct.to(self.device)
            img_torch *= 1 - mask_torch
            return (
                img_torch,
                mask_torch,
                rel_pos,
                direct,
                img_original,
                mask_original,
                pad_bottom,
                pad_right,
            )

        @torch.no_grad()
        def _inpaint(
            self,
            img: np.ndarray,
            mask: np.ndarray,
            textblock_list: List[TextBlock] = None,
        ) -> np.ndarray:

            im_h, im_w = img.shape[:2]
            (
                img_torch,
                mask_torch,
                rel_pos,
                direct,
                img_original,
                mask_original,
                pad_bottom,
                pad_right,
            ) = self.inpaint_preprocess(img, mask)

            precision = TORCH_DTYPE_MAP[self.precision]
            if self.device in {"cuda"}:
                try:
                    with torch.autocast(device_type=self.device, dtype=precision):
                        img_inpainted_torch = self.model(
                            img_torch, mask_torch, rel_pos, direct
                        )
                except Exception as e:
                    self.logger.error(e)
                    self.logger.error(
                        f"{precision} inference is not supported for this device, use fp32 instead."
                    )
                    img_inpainted_torch = self.model(
                        img_torch, mask_torch, rel_pos, direct
                    )
            else:
                img_inpainted_torch = self.model(img_torch, mask_torch, rel_pos, direct)

            img_inpainted = (
                img_inpainted_torch.to(device="cpu", dtype=torch.float32)
                .squeeze_(0)
                .permute(1, 2, 0)
                .numpy()
                * 255
            )
            img_inpainted = (np.clip(np.round(img_inpainted), 0, 255)).astype(np.uint8)
            if pad_bottom > 0:
                img_inpainted = img_inpainted[:-pad_bottom]
            if pad_right > 0:
                img_inpainted = img_inpainted[:, :-pad_right]
            new_shape = img_inpainted.shape[:2]
            if new_shape[0] != im_h or new_shape[1] != im_w:
                img_inpainted = cv2.resize(
                    img_inpainted, (im_w, im_h), interpolation=cv2.INTER_LINEAR
                )
            img_inpainted = img_inpainted * mask_original + img_original * (
                1 - mask_original
            )

            return img_inpainted

        def updateParam(self, param_key: str, param_content):
            super().updateParam(param_key, param_content)

            if param_key == "device":
                param_device = self.params["device"]["value"]
                if self.model is not None:
                    self.model.to(param_device)
                self.device = param_device

            elif param_key == "inpaint_size":
                self.inpaint_size = int(self.params["inpaint_size"]["value"])

            elif param_key == "precision":
                precision = self.params["precision"]["value"]
                self.precision = precision

        def moveToDevice(self, device: str, precision: str = None):
            self.model.to(device)
            self.device = device
            if precision is not None:
                self.precision = precision

    @register_inpainter("lama_large_512px")
    class LamaLarge(LamaInpainterMPE):
        params = {
            "inpaint_size": {
                "type": "selector",
                "options": [512, 768, 1024, 1536, 2048],
                "value": 1536,
                "description": "Maximum image dimension for inpainting (larger images are resized)",
            },
            "device": DEVICE_SELECTOR(not_supported=["privateuseone"]),
            "precision": {
                "type": "selector",
                "options": ["fp32", "bf16"],
                "value": "bf16"
                if DEFAULT_DEVICE == "cuda" and BF16_SUPPORTED
                else "fp32",
                "description": "Model precision (bf16 is faster on supported GPUs, fp32 is more compatible)",
            },
        }

        download_file_list = [
            {
                "url": "https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt",
                "sha256_pre_calculated": "11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935",
                "files": "data/models/lama_large_512px.ckpt",
            }
        ]

        def __init__(self, **params) -> None:
            super().__init__(**params)
            self.precision = self.params["precision"]["value"]

        def _load_model(self):
            device = self.params["device"]["value"]
            precision = self.params["precision"]["value"]

            self.model = load_lama_mpe(
                r"data/models/lama_large_512px.ckpt",
                device="cpu",
                use_mpe=False,
                large_arch=True,
            )
            self.moveToDevice(device, precision=precision)
