import copy
import json
import os
import os.path as osp
import traceback
from dataclasses import field
from typing import Dict, List

from . import shared
from .fontformat import FontFormat, PunctuationPosition
from .io_utils import json_dump_nested_obj, np, serialize_np
from .logger import logger as LOGGER
from .structures import Config, nested_dataclass


class RunStatus:
    FIN_DET = 1
    FIN_OCR = 2
    FIN_INPAINT = 4
    FIN_TRANSLATE = 8
    FIN_ALL = 15


@nested_dataclass
class ModuleConfig(Config):
    textdetector: str = "ctd"
    ocr: str = "none_ocr"
    inpainter: str = "lama_large_512px"
    translator: str = "None"
    enable_detect: bool = True
    keep_exist_textlines: bool = False
    enable_ocr: bool = True
    enable_translate: bool = True
    enable_inpaint: bool = True
    textdetector_params: Dict = field(default_factory=lambda: dict())
    ocr_params: Dict = field(default_factory=lambda: dict())
    translator_params: Dict = field(default_factory=lambda: dict())
    inpainter_params: Dict = field(default_factory=lambda: dict())
    translate_source: str = "日本語"
    translate_target: str = "简体中文"
    check_need_inpaint: bool = True
    load_model_on_demand: bool = True
    empty_runcache: bool = False
    model_profiles: str = ""
    finish_code: int = 15

    def get_params(self, module_key: str, for_saving=False) -> dict:
        d = self[module_key + "_params"]
        if not for_saving:
            return d
        sd = {}
        for module_key, module_params in d.items():
            if module_params is None:
                continue
            saving_module_params = {}
            sd[module_key] = saving_module_params
            for pk, pv in module_params.items():
                if pk in {"description"}:
                    continue
                if pk.startswith("__"):
                    continue
                if isinstance(pv, dict):
                    pv = pv["value"]
                saving_module_params[pk] = pv
        return sd

    def get_saving_params(self, to_dict=True):
        params = copy.copy(self)
        params.ocr_params = self.get_params("ocr", for_saving=True)
        params.inpainter_params = self.get_params("inpainter", for_saving=True)
        params.textdetector_params = self.get_params("textdetector", for_saving=True)
        params.translator_params = self.get_params("translator", for_saving=True)
        if to_dict:
            return params.__dict__
        return params

    def stage_enabled(self, idx: int):
        if idx == 0:
            return self.enable_detect
        elif idx == 1:
            return self.enable_ocr
        elif idx == 2:
            return self.enable_translate
        elif idx == 3:
            return self.enable_inpaint
        else:
            raise Exception(f"not supported stage idx: {idx}")

    def all_stages_disabled(self):
        return (
            self.enable_detect
            or self.enable_ocr
            or self.enable_translate
            or self.enable_inpaint
        ) is False

    def __post_init__(self):
        self.update_finish_code()

    def update_finish_code(self):
        self.finish_code = (
            self.enable_detect * RunStatus.FIN_DET
            + self.enable_ocr * RunStatus.FIN_OCR
            + self.enable_translate * RunStatus.FIN_TRANSLATE
            + self.enable_inpaint * RunStatus.FIN_INPAINT
        )


@nested_dataclass
class DrawPanelConfig(Config):
    pentool_color: List = field(default_factory=lambda: [0, 0, 0])
    pentool_width: float = 30.0
    pentool_shape: int = 0
    inpainter_width: float = 30.0
    inpainter_shape: int = 0
    current_tool: int = 0
    rectool_auto: bool = False
    rectool_method: int = 0
    recttool_dilate_ksize: int = 0


@nested_dataclass
class MirrorConfig(Config):
    """Mirror/registry settings for domestic users.

    Empty strings = use default (official) sources.
    """

    pip_index_url: str = ""
    pip_extra_index_url: str = ""
    hf_endpoint: str = ""
    github_mirror: str = ""


@nested_dataclass
class ProgramConfig(Config):
    module: ModuleConfig = field(default_factory=lambda: ModuleConfig())
    drawpanel: DrawPanelConfig = field(default_factory=lambda: DrawPanelConfig())
    mirror: MirrorConfig = field(default_factory=lambda: MirrorConfig())
    global_fontformat: FontFormat = field(default_factory=lambda: FontFormat())
    recent_proj_list: List = field(default_factory=lambda: list())
    show_page_list: bool = False
    imgtrans_paintmode: bool = False
    imgtrans_textedit: bool = True
    imgtrans_textblock: bool = True
    mask_transparency: float = 0.0
    original_transparency: float = 0.0
    original_transparency_preset: int = 20
    open_recent_on_startup: bool = True

    let_fntsize_flag: int = 0
    let_fntstroke_flag: int = 0
    let_fntcolor_flag: int = 0
    let_fnt_scolor_flag: int = 0
    let_fnteffect_flag: int = 1
    let_alignment_flag: int = 0
    let_writing_mode_flag: int = 0
    let_family_flag: int = 0
    punctuation_position: int = PunctuationPosition.Simplified
    let_autolayout_flag: bool = True
    let_uppercase_flag: bool = True
    let_textstyle_indep_flag: bool = False
    text_styles_path: str = osp.join(shared.DEFAULT_TEXTSTYLE_DIR, "default.json")

    fsearch_case: bool = False
    fsearch_whole_word: bool = False
    fsearch_regex: bool = False
    fsearch_range: int = 0
    gsearch_case: bool = False
    gsearch_whole_word: bool = False
    gsearch_regex: bool = False
    gsearch_range: int = 0

    darkmode: bool = False
    light_theme: str = "eva-light"
    dark_theme: str = "eva-dark"
    fold_textarea: bool = False
    expand_font_format_panel: bool = True
    show_source_text: bool = True
    show_trans_text: bool = True
    display_lang: str = field(
        default_factory=lambda: shared.DEFAULT_DISPLAY_LANG
    )  # to always apply shared.DEFAULT_DISPLAY_LANG
    imgsave_quality: int = 100
    imgsave_ext: str = ".png"
    imgsave_auto_format: bool = False
    intermediate_imgsave_ext: str = ".png"
    excluded_fonts: List[str] = field(default_factory=lambda: list())
    max_font_size: int = 200
    shortcuts: Dict[str, List[str]] = field(default_factory=lambda: dict())
    font_size_presets: List[float] = field(
        default_factory=lambda: [
            5,
            5.5,
            6.5,
            7.5,
            8,
            9,
            10,
            10.5,
            11,
            12,
            14,
            16,
            18,
            20,
            22,
            26,
            28,
            36,
            48,
            56,
            72,
            93,
            123,
            163,
        ]
    )
    line_spacing_presets: List[float] = field(
        default_factory=lambda: [1.0, 1.1, 1.2, 1.5, 2.0]
    )
    letter_spacing_presets: List[float] = field(
        default_factory=lambda: [0.0, 0.5, 1.0, 1.5, 2.0]
    )
    stroke_width_presets: List[float] = field(
        default_factory=lambda: [0.1, 0.5, 1.0, 2.0, 3.0]
    )
    opacity_presets: List[float] = field(
        default_factory=lambda: [1.0, 0.8, 0.6, 0.4, 0.2]
    )
    animation_fps: int = 0  # 0=auto, 30=30fps, 60=60fps, -1=disabled
    open_image_fit_window: bool = False
    fit_window_on_page_switch: bool = False
    show_text_style_preset: bool = True
    expand_tstyle_panel: bool = True
    show_text_effect_panel: bool = True
    expand_teffect_panel: bool = True
    text_advanced_format_panel: bool = True
    expand_tadvanced_panel: bool = True

    @staticmethod
    def load(cfg_path: str):

        with open(cfg_path, "r", encoding="utf8") as f:
            config_dict = json.loads(f.read())

        # for backward compatibility
        if "dl" in config_dict:
            dl = config_dict.pop("dl")
            if "module" not in config_dict:
                if "textdetector_setup_params" in dl:
                    textdetector_params = dl.pop("textdetector_setup_params")
                    dl["textdetector_params"] = textdetector_params
                if "inpainter_setup_params" in dl:
                    inpainter_params = dl.pop("inpainter_setup_params")
                    dl["inpainter_params"] = inpainter_params
                if "ocr_setup_params" in dl:
                    ocr_params = dl.pop("ocr_setup_params")
                    dl["ocr_params"] = ocr_params
                if "translator_setup_params" in dl:
                    translator_params = dl.pop("translator_setup_params")
                    dl["translator_params"] = translator_params
                config_dict["module"] = dl

        if "module" in config_dict:
            module_cfg = config_dict["module"]
            trans_params = module_cfg["translator_params"]
            repl_pairs = {"chatgpt": "ChatGPT"}
            for k, i in repl_pairs.items():
                if k in trans_params:
                    trans_params[i] = trans_params.pop(k)
            if module_cfg["translator"] in repl_pairs:
                module_cfg["translator"] = repl_pairs[module_cfg["translator"]]
            # Migrate removed translators
            if module_cfg["translator"] in ("ChatGPT", "Gemini"):
                module_cfg["translator"] = "LLM_API_Translator"
            for removed in ("ChatGPT", "Gemini"):
                trans_params.pop(removed, None)

        return ProgramConfig(**config_dict)


pcfg = ProgramConfig()
text_styles: List[FontFormat] = []
active_format: FontFormat = None


def load_textstyle_from(p: str, raise_exception=False):

    if not osp.exists(p):
        LOGGER.warning(f"Text style {p} does not exist.")
        return

    try:
        with open(p, "r", encoding="utf8") as f:
            style_list = json.loads(f.read())
            styles_loaded = []
            for style in style_list:
                try:
                    styles_loaded.append(FontFormat(**style))
                except Exception:
                    LOGGER.warning(f"Skip invalid text style: {style}")
    except Exception as e:
        LOGGER.error(f"Failed to load text style from {p}: {e}")
        if raise_exception:
            raise e
        return

    global text_styles, pcfg
    if len(text_styles) > 0:
        text_styles.clear()
    text_styles.extend(styles_loaded)
    pcfg.text_styles_path = p


def load_config(config_path: str = shared.CONFIG_PATH):
    if config_path != shared.CONFIG_PATH:
        shared.CONFIG_PATH = config_path
        LOGGER.info(f"Using specified config file at {shared.CONFIG_PATH}")

    if osp.exists(shared.CONFIG_PATH):
        try:
            config = ProgramConfig.load(shared.CONFIG_PATH)
        except Exception as e:
            LOGGER.exception(e)
            LOGGER.warning("Failed to load config file, using default config")
            config = ProgramConfig()
    else:
        LOGGER.info(
            f"{shared.CONFIG_PATH} does not exist, new config file will be created."
        )
        config = ProgramConfig()

    global pcfg
    pcfg.merge(config)

    # Backward compat: migrate old theme_name to light_theme/dark_theme
    if hasattr(pcfg, "theme_name") and pcfg.theme_name:
        old = pcfg.theme_name
        if "dark" in old.lower():
            pcfg.dark_theme = old
        else:
            pcfg.light_theme = old

    p = pcfg.text_styles_path
    if not osp.exists(pcfg.text_styles_path):
        dp = osp.join(shared.DEFAULT_TEXTSTYLE_DIR, "default.json")
        if p != dp and osp.exists(dp):
            p = dp
            LOGGER.warning(f"Text style {p} does not exist, use the default from {dp}.")
        else:
            with open(dp, "w", encoding="utf8") as f:
                f.write(json.dumps([], ensure_ascii=False))
            LOGGER.info(f"New text style file created at {dp}.")
    load_textstyle_from(p)

    # Migrate profiles from old translator storage to new shared location
    from .profile_manager import migrate_old_profiles

    migrate_old_profiles()


def json_dump_program_config(obj, **kwargs):
    def _default(obj):
        if isinstance(obj, (np.ndarray, np.ScalarType)):
            return serialize_np(obj)
        elif isinstance(obj, ModuleConfig):
            return obj.get_saving_params()
        return obj.__dict__

    return json.dumps(obj, default=lambda o: _default(o), ensure_ascii=False, **kwargs)


def save_config():
    global pcfg
    try:
        tmp_save_tgt = shared.CONFIG_PATH + ".tmp"
        with open(tmp_save_tgt, "w", encoding="utf8") as f:
            f.write(json_dump_program_config(pcfg))
    except Exception as e:
        LOGGER.error(f"Failed save config to {tmp_save_tgt}: {e}")
        LOGGER.error(traceback.format_exc())
        return False

    os.replace(tmp_save_tgt, shared.CONFIG_PATH)
    LOGGER.info("Config saved")
    return True


def save_text_styles(raise_exception=False):
    global pcfg, text_styles
    try:
        style_dir = osp.dirname(pcfg.text_styles_path)
        if not osp.exists(style_dir):
            os.makedirs(style_dir)
        tmp_save_tgt = pcfg.text_styles_path + ".tmp"
        with open(tmp_save_tgt, "w", encoding="utf8") as f:
            f.write(json_dump_nested_obj(text_styles))

    except Exception as e:
        LOGGER.error(f"Failed save text style to {tmp_save_tgt}: {e}")
        LOGGER.error(traceback.format_exc())
        if raise_exception:
            raise e
        return False

    os.replace(tmp_save_tgt, pcfg.text_styles_path)
    LOGGER.info("Text style saved")
    return True
