import os
import os.path as osp
from typing import List

import utils.shared as shared
from utils.download_util import download_and_check_files

from . import INPAINTERS, OCR, TEXTDETECTORS, TRANSLATORS
from .base import LOGGER, BaseModule


def download_and_check_module_files(module_class_list: List[BaseModule] = None):
    if module_class_list is None:
        module_class_list = []
        for registered in [INPAINTERS, TEXTDETECTORS, OCR, TRANSLATORS]:
            for module_key in registered.module_dict.keys():
                module_class_list.append(registered.get(module_key))

    for module_class in module_class_list:
        if (
            module_class.download_file_on_load
            or module_class.download_file_list is None
        ):
            continue
        for download_kwargs in module_class.download_file_list:
            all_successful = download_and_check_files(**download_kwargs)
            if all_successful:
                continue
            LOGGER.error(
                f"Please save these files manually to sepcified path and restart the application, otherwise {module_class} will be unavailable."
            )


def prepare_pkuseg():
    try:
        import pkuseg
    except ImportError:
        try:
            import spacy_pkuseg as pkuseg
        except ImportError:
            LOGGER.warning(
                "pkuseg / spacy-pkuseg not available. Chinese text segmentation will fall back to character-level."
            )
            return

    flist = [
        {
            "url": "https://github.com/lancopku/pkuseg-python/releases/download/v0.0.16/postag.zip",
            "files": ["features.pkl", "weights.npz"],
            "sha256_pre_calculated": [
                "17d734c186a0f6e76d15f4990e766a00eed5f72bea099575df23677435ee749d",
                "2bbd53b366be82a1becedb4d29f76296b36ad7560b6a8c85d54054900336d59a",
            ],
            "archived_files": "postag.zip",
            "save_dir": "data/models/pkuseg/postag",
        },
        {
            "url": "https://github.com/explosion/spacy-pkuseg/releases/download/v0.0.26/spacy_ontonotes.zip",
            "files": ["features.msgpack", "weights.npz"],
            "sha256_pre_calculated": [
                "fd4322482a7018b9bce9216173ae9d2848efe6d310b468bbb4383fb55c874a18",
                "5ada075eb25a854f71d6e6fa4e7d55e7be0ae049255b1f8f19d05c13b1b68c9e",
            ],
            "archived_files": "spacy_ontonotes.zip",
            "save_dir": "data/models/pkuseg/spacy_ontonotes",
        },
    ]
    for files_download_kwargs in flist:
        download_and_check_files(**files_download_kwargs)

    PKUSEG_HOME = osp.join(shared.PROGRAM_PATH, "data/models/pkuseg")
    pkuseg.config.pkuseg_home = PKUSEG_HOME

    # there must be data/models/pkuseg/postag.zip and data/models/pkuseg/spacy_ontonotes.zip
    # otherwise the dumb package download these models again becuz its dumb checking
    p = osp.join(PKUSEG_HOME, "postag.zip")
    if not osp.exists(p):
        os.makedirs(p)

    p = osp.join(PKUSEG_HOME, "spacy_ontonotes.zip")
    if not osp.exists(p):
        os.makedirs(p)


def prepare_local_files_forall():

    # download files required by detect, ocr, inpaint and translators
    download_and_check_module_files()

    if shared.CACHE_UPDATED:
        shared.dump_cache()
