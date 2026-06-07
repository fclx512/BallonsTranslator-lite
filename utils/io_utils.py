import base64
import importlib
import io
import json
import os
import os.path as osp
import sys
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Union

import cv2
import numpy as np
import PIL
import requests
from natsort import natsorted
from PIL import Image

from .logger import logger as LOGGER

# Ensure JXL codec is registered if pillow-jxl-plugin is installed
# JXL codec registration — currently disabled in UI due to compatibility issues.
# See docs/en/lessons_learned.md §4.1 for details. If re-enabling, fix imwrite error handling first.
try:
    import pillow_jxl  # noqa: F401 — registers JXL codec with Pillow
except ImportError:
    pass

# Bypass ultralytics' broken Image.open monkey-patch. It catches ALL exceptions
# and blindly tries to pip-install pi-heif — including for unrelated formats like
# .jxl — which fails forever in the embedded Python (no pip module).
try:
    from ultralytics.utils.patches import _image_open as _pil_image_open
except ImportError:
    _pil_image_open = Image.open

IMG_EXT = [".bmp", ".jpg", ".png", ".jpeg", ".webp", ".jxl"]

NP_INT_TYPES = (
    np.int_,
    np.int8,
    np.int16,
    np.int32,
    np.int64,
    np.uint,
    np.uint8,
    np.uint16,
    np.uint32,
    np.uint64,
)
if int(np.version.full_version.split(".")[0]) == 1:
    NP_BOOL_TYPES = (np.bool_, np.bool8)
    NP_FLOAT_TYPES = (np.float_, np.float16, np.float32, np.float64)
else:
    NP_BOOL_TYPES = (np.bool_, np.bool)
    NP_FLOAT_TYPES = (np.float16, np.float32, np.float64)


def parse_page_range(range_str: str) -> List[int]:
    """Parse page range string like "1-5,8,10-12" into 0-based int list, deduplicated."""
    if not range_str or not range_str.strip():
        return []
    seen = set()
    result = []
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            parts = part.split("-", maxsplit=1)
            try:
                start = int(parts[0].strip())
                end = int(parts[1].strip())
            except ValueError:
                raise ValueError(f'Invalid page range: "{part}"')
            if start < 1 or end < 1:
                raise ValueError(f'Page numbers must be >= 1, got: "{part}"')
            if start > end:
                raise ValueError(f'Start page > end page in "{part}"')
            for i in range(start, end + 1):
                idx = i - 1
                if idx not in seen:
                    seen.add(idx)
                    result.append(idx)
        else:
            try:
                page = int(part)
            except ValueError:
                raise ValueError(f'Invalid page number: "{part}"')
            if page < 1:
                raise ValueError(f"Page numbers must be >= 1, got: {page}")
            idx = page - 1
            if idx not in seen:
                seen.add(idx)
                result.append(idx)
    return result


def to_dict(obj):
    return json.loads(json.dumps(obj, default=lambda o: o.__dict__, ensure_ascii=False))


def serialize_np(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.ScalarType):
        if isinstance(obj, NP_BOOL_TYPES):
            return bool(obj)
        elif isinstance(obj, NP_FLOAT_TYPES):
            return float(obj)
        elif isinstance(obj, NP_INT_TYPES):
            return int(obj)
    return obj


def json_dump_nested_obj(obj, **kwargs):
    def _default(obj):
        if isinstance(obj, (np.ndarray, np.ScalarType)):
            return serialize_np(obj)
        return obj.__dict__

    return json.dumps(obj, default=lambda o: _default(o), ensure_ascii=False, **kwargs)


# https://stackoverflow.com/questions/26646362/numpy-array-is-not-json-serializable
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.ndarray, np.ScalarType)):
            return serialize_np(obj)
        return json.JSONEncoder.default(self, obj)


def find_all_imgs(img_dir, abs_path=False, sort=False):
    imglist = []
    for filename in os.listdir(img_dir):
        file_suffix = Path(filename).suffix
        if file_suffix.lower() not in IMG_EXT:
            continue
        # 额外检查：确保不包含原始TIF文件，但可以包含预览图
        if file_suffix.lower() in [".tif", ".tiff"]:
            continue
        if abs_path:
            imglist.append(osp.join(img_dir, filename))
        else:
            imglist.append(filename)

    if sort:
        imglist = natsorted(imglist)

    return imglist


def page_names_from_range(proj, pages_str: str):
    """Parse a page range string like '1-5,7,9-12' into a list of page names.

    Raises ValueError if any page index is out of range.
    """
    num_pages = len(proj.pages)
    page_names = list(proj.pages.keys())
    natsorted_page_names = natsorted(page_names)

    selected = set()
    parts = [p.strip() for p in pages_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if start < 1 or end > num_pages or start > end:
                raise ValueError(f"Invalid range: {part}")
            for i in range(start, end + 1):
                selected.add(natsorted_page_names[i - 1])
        else:
            i = int(part)
            if i < 1 or i > num_pages:
                raise ValueError(f"Invalid page index: {i}")
            selected.add(natsorted_page_names[i - 1])

    # Preserve project order
    return [name for name in page_names if name in selected]


def create_thumbnail(img_path, max_width=1000):
    """
    为图像创建缩略图，保持宽高比。
    缩略图的最大宽度为 max_width（默认 1000），
    高度将根据原始比例自动计算。

    参数:
        img_path (str): 原始图像的文件路径
        max_width (int): 缩略图最大宽度，默认为 1000

    返回:
        bool: 成功创建缩略图返回 True，否则返回 False
    """
    try:
        # 使用 PIL 打开图像
        with Image.open(img_path) as img:
            # 获取原始尺寸
            original_width, original_height = img.size
            # 如果原图tif是黑白位图转换为灰度
            if img.mode == "1":
                img = img.convert("L")
            # 计算缩放比例并确定新尺寸
            scale_factor = max_width / original_width
            new_width = max_width
            new_height = int(original_height * scale_factor)

            # 使用高质量重采样算法进行缩放（LANCZOS）
            thumbnail = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # 构造缩略图保存路径：原路径目录下，文件名 + _thumb.jpg
            base_path = Path(img_path)
            thumb_path = base_path.parent / f"{base_path.stem}_thumb.jpg"

            # 保存为 JPEG 格式，质量设为 95，启用优化
            thumbnail.save(thumb_path, "JPEG", quality=95, optimize=True)

            LOGGER.info(f"Thumbnail created: {thumb_path}")
            return True

    except Exception as e:
        LOGGER.error(f"Failed to create thumbnail for {img_path}: {e}")
        return False


def find_tif_files(img_dir, abs_path=False, sort=False):
    """
    查找目录中的TIF文件，用于生成预览图
    """
    imglist = []
    for filename in os.listdir(img_dir):
        file_suffix = Path(filename).suffix.lower()
        if file_suffix in [".tif", ".tiff"]:
            if abs_path:
                imglist.append(osp.join(img_dir, filename))
            else:
                imglist.append(filename)

    if sort:
        imglist = natsorted(imglist)

    return imglist


def find_all_files_recursive(
    tgt_dir: Union[List, str], ext: Union[List, set], exclude_dirs=None
):
    if isinstance(tgt_dir, str):
        tgt_dir = [tgt_dir]

    if exclude_dirs is None:
        exclude_dirs = set()

    filelst = []
    for d in tgt_dir:
        for root, _, files in os.walk(d):
            if osp.basename(root) in exclude_dirs:
                continue
            for f in files:
                if Path(f).suffix.lower() in ext:
                    filelst.append(osp.join(root, f))

    return filelst


def _imread_jxl_fallback(imgpath, read_type):
    """Try to read a .jxl file with cv2 if PIL can't handle it."""
    try:
        img = cv2.imread(imgpath, read_type)
    except cv2.error:
        return None
    if img is not None:
        if read_type != cv2.IMREAD_GRAYSCALE:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return img
    return None


def imread(imgpath, read_type=cv2.IMREAD_COLOR, max_retry_limit=5, retry_interval=0.1):
    if not osp.exists(imgpath):
        return None

    suffix = Path(imgpath).suffix.lower()

    # JXL: try PIL once, fall back to cv2 on failure. No retries — if the JXL
    # codec can't decode a fully-written file, retrying won't help.
    # JXL read path — kept for backward compatibility with existing .jxl cache files.
    # See docs/en/lessons_learned.md §4.1 for known decode failures.
    if suffix == ".jxl":
        if ".jxl" in Image.EXTENSION:
            try:
                img = _pil_image_open(imgpath)
            except PIL.UnidentifiedImageError:
                img = None
            if img is not None:
                if read_type == cv2.IMREAD_GRAYSCALE:
                    img = img.convert("L")
                return np.array(img)
        img = _imread_jxl_fallback(imgpath, read_type)
        if img is not None:
            LOGGER.info(f"JXL opened via cv2 fallback: {imgpath}")
        else:
            LOGGER.warning(f"JXL format not supported, cannot read: {imgpath}")
        return img

    num_tries = 0
    while True:
        try:
            img = _pil_image_open(imgpath)  # bypass ultralytics' broken Image.open patch
            if img.mode == "CMYK":
                img = img.convert("RGB")
            elif img.mode == "P":
                img = img.convert("RGBA")
            if read_type == cv2.IMREAD_GRAYSCALE:
                img = img.convert("L")
            img = np.array(img)
            if read_type != cv2.IMREAD_GRAYSCALE:
                if img.ndim == 3 and img.shape[-1] == 1:
                    img = img[..., :2]
                if img.ndim == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

            if img.ndim == 3 and img.shape[-1] == 4:
                if np.all(img[..., -1] == 255):
                    img = np.ascontiguousarray(img[..., :3])
            break
        except PIL.UnidentifiedImageError as e:
            # IMG I/O thread might not finished yet
            num_tries += 1
            if max_retry_limit is not None and num_tries >= max_retry_limit:
                LOGGER.exception(e)
                return None
            LOGGER.warning(
                f"Failed to read {imgpath}: {e}, retries: {num_tries} / {max_retry_limit}"
            )
            time.sleep(retry_interval)

    return img


def imwrite(img_path, img, ext=".png", quality=100, jxl_encode_effort=3):
    # cv2 writing is faster than PIL
    suffix = Path(img_path).suffix
    ext = ext.lower()
    assert ext in IMG_EXT
    if suffix != "":
        img_path = img_path.replace(suffix, ext)
    else:
        img_path += ext

    if ext != ".webp":
        quality = min(
            quality, 100
        )  # for webp quality above 100 the lossless compression is used

    # Ensure directory exists
    save_dir = osp.dirname(img_path)
    if save_dir and not osp.exists(save_dir):
        os.makedirs(save_dir)

    encode_param = None
    if ext in {".jpg", ".jpeg"}:
        encode_param = [cv2.IMWRITE_JPEG_QUALITY, quality]
    elif ext == ".webp":
        encode_param = [cv2.IMWRITE_WEBP_QUALITY, quality]
    # JXL save path — NOT currently reachable from UI (JXL disabled in configpanel).
    # WARNING: no try/except here; a failed encode can leave a 0-byte or corrupt file.
    # If re-enabling JXL, wrap this in try/except with PNG fallback. See docs/en/lessons_learned.md §4.1.
    if ext == ".jxl":
        if ".jxl" in Image.EXTENSION:
            lossless = quality > 99
            Image.fromarray(img).save(
                img_path,
                quality=quality,
                lossless=lossless,
                effort=jxl_encode_effort,
            )
            return
        # JXL not supported by Pillow — fall back to PNG
        LOGGER.warning(
            "JXL format not supported by Pillow, falling back to PNG. "
            "Install pillow-jxl-plugin for JXL support."
        )
        ext = ".png"
        img_path = str(Path(img_path).with_suffix(ext))

    if ext != ".jxl":  # handles both non-JXL and JXL-fallback
        if len(img.shape) == 3:
            if img.shape[-1] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            elif img.shape[-1] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA)
        cv2.imencode(ext, img, encode_param)[1].tofile(img_path)


def show_img_by_dict(imgdicts):
    for keyname in imgdicts.keys():
        cv2.imshow(keyname, imgdicts[keyname])
    cv2.waitKey(0)


def text_is_empty(text) -> bool:
    if isinstance(text, str):
        if text.strip() == "":
            return True
    if isinstance(text, list):
        for t in text:
            t_is_empty = text_is_empty(t)
            if not t_is_empty:
                return False
        return True
    elif text is None:
        return True


def empty_func(*args, **kwargs):
    return


def get_obj_from_str(string, reload=False):
    module, cls = string.rsplit(".", 1)
    if reload:
        module_imp = importlib.import_module(module)
        importlib.reload(module_imp)
    return getattr(importlib.import_module(module, package=None), cls)


def get_module_from_str(module_str: str):
    return importlib.import_module(module_str, package=None)


def build_funcmap(
    module_str: str,
    params_names: List[str],
    func_prefix: str = "",
    func_suffix: str = "",
    fallback_func: Callable = None,
    verbose: bool = True,
) -> Dict:

    if fallback_func is None:
        fallback_func = empty_func

    module = get_module_from_str(module_str)

    funcmap = {}
    for param in params_names:
        tgt_func = f"{func_prefix}{param}{func_suffix}"
        try:
            tgt_func = getattr(module, tgt_func)
        except Exception as e:
            if verbose:
                print(f"failed to import {tgt_func} from {module_str}: {e}")
            tgt_func = fallback_func
        funcmap[param] = tgt_func

    return funcmap


def _b64encode(x: bytes) -> str:
    return base64.b64encode(x).decode("utf-8")


def img2b64(img):
    """
    Convert a PIL image to a base64-encoded string.
    """
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return _b64encode(buffered.getvalue())


def save_encoded_image(b64_image: str, output_path: str):
    with open(output_path, "wb") as image_file:
        image_file.write(base64.b64decode(b64_image))


def submit_request(url, data, exist_on_exception=True, auth=None, wait_time=5):
    response = None
    try:
        while True:
            try:
                response = requests.post(url, data=data, auth=auth)
                response.raise_for_status()
                break
            except Exception as e:
                if wait_time > 0:
                    print(traceback.format_exc(), file=sys.stderr)
                    print(f"sleep {wait_time} sec...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise e
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        if response is not None:
            print("response content: " + response.text)
        if exist_on_exception:
            exit()
    return response
