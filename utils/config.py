import copy
import json
import os
import os.path as osp
import traceback
from dataclasses import field
from datetime import datetime
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


class TranslateContext:
    """Canonical translation grouping values stored in module config.

    >>> TranslateContext.Page
    'page'
    """

    TextBlock = 'textblock'
    Page = 'page'
    Valid = (TextBlock, Page)


class LLMTranslateContext:
    """Canonical LLM translation-context modes stored in module config.

    >>> LLMTranslateContext.HISTORY
    'history'
    """

    PAGE = 'page'
    HISTORY = 'history'
    Valid = (PAGE, HISTORY)


class LLMGlossaryMode:
    """Canonical glossary selection modes stored in module config.

    >>> LLMGlossaryMode.Matching
    'matching'
    """

    Matching = 'matching'
    All = 'all'
    Valid = (Matching, All)


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
    translate_context: str = TranslateContext.Page
    llm_translate_context: str = LLMTranslateContext.PAGE
    llm_prior_context_token_budget: int = 4096
    llm_glossary_path: str = ''
    llm_glossary_mode: str = LLMGlossaryMode.Matching
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
        if self.translate_context not in TranslateContext.Valid:
            self.translate_context = TranslateContext.Page
        if self.llm_translate_context not in LLMTranslateContext.Valid:
            self.llm_translate_context = LLMTranslateContext.PAGE
        if not isinstance(self.llm_glossary_path, str):
            self.llm_glossary_path = ''
        if self.llm_glossary_mode not in LLMGlossaryMode.Valid:
            self.llm_glossary_mode = LLMGlossaryMode.Matching
        if (
            not isinstance(self.llm_prior_context_token_budget, int)
            or isinstance(self.llm_prior_context_token_budget, bool)
            or self.llm_prior_context_token_budget <= 0
        ):
            self.llm_prior_context_token_budget = 4096
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
    photoshop_path: str = ""


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
    let_alignment_flag: int = 0
    let_writing_mode_flag: int = 0
    let_family_flag: int = 0
    punctuation_position: int = PunctuationPosition.Simplified
    halfwidth_jp_corner_brackets: bool = False
    halfwidth_jp_corner_brackets_horizontal: bool = False
    tatechuyoko_threshold: int = 3
    let_uppercase_flag: bool = True
    use_notext_images: bool = True
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
    fold_textarea: bool = True
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
            6,
            8,
            10,
            12,
            14,
            18,
            24,
            30,
            36,
            48,
            60,
            72,
            96,
            120,
        ]
    )
    line_spacing_presets: List[float] = field(
        default_factory=lambda: [1.0, 1.1, 1.2, 1.5, 2.0]
    )
    letter_spacing_presets: List[float] = field(
        default_factory=lambda: [0.0, 0.5, 1.0, 1.5, 2.0]
    )
    stroke_width_presets: List[float] = field(
        default_factory=lambda: [0.1, 0.15, 0.2, 1.0]
    )
    opacity_presets: List[float] = field(
        default_factory=lambda: [1.0, 0.8, 0.6, 0.4, 0.2]
    )
    animation_fps: int = 0  # 0=auto, 30=30fps, 60=60fps, -1=disabled

    # ── Temp project (drag-imported single/multi images) ─────
    temp_project_dir: str = ""  # empty = use default TEMP_PROJECTS_DIR
    auto_clean_temp_projects: bool = False  # delete temp dirs on close

    # ── Performance settings ─────────────────────────────────
    text_rendering: int = 1  # 0=Crisp(always vector), 1=Smooth(bitmap cache)
    show_decorations_during_drag: bool = False
    open_image_fit_window: bool = False
    fit_window_on_page_switch: bool = False
    show_text_style_preset: bool = True
    expand_tstyle_panel: bool = True
    show_text_effect_panel: bool = True
    expand_teffect_panel: bool = True
    text_advanced_format_panel: bool = True
    expand_tadvanced_panel: bool = True
    show_seq_badge: bool = True
    overflow_mode: bool = False  # 过界模式 — 画布边界视觉指示 + 文字块跨边界裁剪
    clip_text_overflow: bool = True  # 翻译填充时裁剪溢出文字并显示黄色提示框，拖拽调整后解除

    # ── Right-click context menu customization ─────────────
    context_menu_order: List[str] = field(default_factory=lambda: [
        "copy", "paste", "delete",
        "copy_src", "paste_src",
        "---",
        "reset_angle", "squeeze", "normalize_breaks",
        "---",
        "reorder",
        "---",
        "align",
        "merge",
        "behavior",
        "---",
        "translate", "ocr", "ocr_translate", "ocr_translate_inpaint",
        ])

    # ── Development / Debug ─────────────────────────────
    context_translation_debug_log: bool = False

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

    # Reset gradient fields — gradient is a per-text-block visual effect,
    # not a global default. Don't persist across restarts.
    # See also FontFormatPanel.on_active_textstyle_label_changed in text_panel.py.
    gf = pcfg.global_fontformat
    gf.gradient_enabled = False
    gf.gradient_start_color = [0, 0, 0]
    gf.gradient_end_color = [255, 255, 255]
    gf.gradient_angle = 0.0
    gf.gradient_size = 1.0

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
        if hasattr(obj, "to_serializable_dict"):
            return obj.to_serializable_dict()
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


# ── Config Import / Export ──────────────────────────────────────────

CONFIG_EXPORT_VERSION = 1

# Keys whose values are always stripped during export when exclude_api_keys=True
EXPORT_SENSITIVE_KEYS = {"api_key", "proxy"}


def _compare_export_schema(data, ref_obj, prefix=""):
    """Recursively compare exported dict keys against a Config schema.

    Returns:
        dict with keys:
        - unknown_keys: keys in export not in current schema (future-version features)
        - missing_keys:  keys in schema not in export (will keep current values)
    """
    result = {"unknown_keys": [], "missing_keys": []}
    ref_annotations = getattr(ref_obj, "__annotations__", {})
    ref_keys = set(ref_annotations.keys())
    data_keys = set(data.keys()) if isinstance(data, dict) else set()

    for key in sorted(data_keys - ref_keys):
        if not key.startswith("_"):
            path = f"{prefix}.{key}" if prefix else key
            result["unknown_keys"].append(path)

    for key in sorted(ref_keys - data_keys):
        path = f"{prefix}.{key}" if prefix else key
        result["missing_keys"].append(path)

    # Recurse into known nested Config fields
    for key in ref_keys & data_keys:
        child_val = getattr(ref_obj, key, None)
        child_data = data.get(key)
        if isinstance(child_val, Config) and isinstance(child_data, dict):
            child_result = _compare_export_schema(
                child_data, child_val, f"{prefix}.{key}" if prefix else key
            )
            result["unknown_keys"].extend(child_result["unknown_keys"])
            result["missing_keys"].extend(child_result["missing_keys"])

    return result


def _get_app_version():
    """Return the application version string, or 'unknown' if unavailable."""
    try:
        from importlib.metadata import version
        return version("ballonstranslator")
    except Exception:
        pass
    try:
        return __import__("launch").__version__
    except Exception:
        pass
    return "0.3.0"


def export_config(path, exclude_api_keys=True, exclude_recent_projects=False):
    """Export pcfg to a JSON file with optional filtering.

    Args:
        path: Target file path (should end in .json).
        exclude_api_keys: Strip api_key and proxy from LLM profiles.
        exclude_recent_projects: Omit recent project list.

    Returns:
        True on success, False on failure.
    """
    global pcfg
    try:
        data = json.loads(json_dump_program_config(pcfg))

        # Stash metadata before filtering
        export_meta = {
            "version": CONFIG_EXPORT_VERSION,
            "app_version": _get_app_version(),
            "exported_at": datetime.now().isoformat(),
            "excluded": [],
        }

        # Filter sensitive data
        if exclude_api_keys:
            profiles_raw = data.get("module", {}).get("model_profiles", "[]")
            try:
                profiles = json.loads(profiles_raw) if profiles_raw else []
                excluded_keys = set()
                for p in profiles:
                    for sk in EXPORT_SENSITIVE_KEYS:
                        if sk in p:
                            p[sk] = ""
                            excluded_keys.add(
                                "module.model_profiles[].%s" % sk
                            )
                if excluded_keys:
                    data["module"]["model_profiles"] = json.dumps(
                        profiles, ensure_ascii=False
                    )
                    export_meta["excluded"].extend(sorted(excluded_keys))
            except (json.JSONDecodeError, TypeError):
                LOGGER.warning("Failed to parse model_profiles for export filtering")

        if exclude_recent_projects:
            data.pop("recent_proj_list", None)
            export_meta["excluded"].append("recent_proj_list")

        data["_export_meta"] = export_meta

        # Atomic write
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp, path)
        LOGGER.info("Config exported to %s", path)
        return True

    except Exception as e:
        LOGGER.error("Failed to export config to %s: %s", path, e)
        LOGGER.error(traceback.format_exc())
        return False


def import_config(path):
    """Import config from a JSON file and merge into pcfg.

    Returns:
        dict with keys:
        - success: bool
        - unknown_keys: list of keys in export not in current schema
        - missing_keys: list of keys in current schema not in export
        - export_meta: the _export_meta block (or empty dict)
    """
    global pcfg
    result = {
        "success": False,
        "unknown_keys": [],
        "missing_keys": [],
        "export_meta": {},
    }

    try:
        with open(path, "r", encoding="utf8") as f:
            data = json.load(f)
    except Exception as e:
        LOGGER.error("Failed to read config file %s: %s", path, e)
        return result

    # Extract metadata
    export_meta = data.pop("_export_meta", {})
    result["export_meta"] = export_meta

    # Compare schemas for compatibility hints
    schema_result = _compare_export_schema(data, pcfg)
    result["unknown_keys"] = schema_result["unknown_keys"]
    result["missing_keys"] = schema_result["missing_keys"]

    try:
        constructed = ProgramConfig(**data)
        pcfg.merge(constructed)
        save_config()
        result["success"] = True
        LOGGER.info(
            "Config imported from %s (unknown: %d, missing: %d)",
            path,
            len(result["unknown_keys"]),
            len(result["missing_keys"]),
        )
    except Exception as e:
        LOGGER.error("Failed to import config from %s: %s", path, e)
        LOGGER.error(traceback.format_exc())

    return result
