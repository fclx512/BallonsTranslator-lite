import copy
import json
import os
import os.path as osp
import string
import traceback
from dataclasses import field, fields
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


class LLMGlossaryMode:
    """Canonical glossary selection modes stored in module config.

    >>> LLMGlossaryMode.Matching
    'matching'
    """

    Matching = 'matching'
    All = 'all'
    Valid = (Matching, All)


class SingleBlkTranslateMode:
    """单框翻译策略(设计方案 §9):plain = 单条直译,context = 轻量 agent 注入本页块。

    >>> SingleBlkTranslateMode.Plain
    'plain'
    """

    Plain = 'plain'
    Context = 'context'
    Valid = (Plain, Context)


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
    # 是否注入前页历史(设计方案 §12:阶段 4 由 page/history 枚举简化为开关,默认开)
    llm_translate_context: bool = True
    llm_prior_context_token_budget: int = 4096
    llm_glossary_path: str = ''
    llm_glossary_mode: str = LLMGlossaryMode.Matching
    single_blk_translate_mode: str = SingleBlkTranslateMode.Plain
    # 剧情注入开关(工作台阶段 3):全局梗概进 agent system 稳定前缀,
    # 强制注入项可驱逐可选历史页;仅影响翻译注入,不影响工作台本身
    llm_story_context: bool = True
    # agent 翻译每轮状态写 utils/debug_log.py(阶段 5 F 类;原 beta 的
    # context_translation_debug_log 已合并进此开关)
    agent_translation_debug_log: bool = False
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
        if not isinstance(self.llm_translate_context, bool):
            self.llm_translate_context = True
        if not isinstance(self.llm_story_context, bool):
            self.llm_story_context = True
        if not isinstance(self.agent_translation_debug_log, bool):
            self.agent_translation_debug_log = False
        if not isinstance(self.llm_glossary_path, str):
            self.llm_glossary_path = ''
        if self.llm_glossary_mode not in LLMGlossaryMode.Valid:
            self.llm_glossary_mode = LLMGlossaryMode.Matching
        if self.single_blk_translate_mode not in SingleBlkTranslateMode.Valid:
            self.single_blk_translate_mode = SingleBlkTranslateMode.Plain
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
    inpainter_width: float = 30.0
    inpainter_shape: int = 0
    inpaint_crop_ratio: str = ""
    inpaint_crop_mode: bool = False
    current_tool: int = 0
    ai_mask_mode: int = 0
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


# ── Pie menu defaults & legacy migration ────────────────────
# The pre-2026-08-11 single-menu layout (still present in old config.json
# files as ``pie_sectors``); used only to decide the migration path.
_LEGACY_PIE_SECTORS = [
    ["ocr_translate"],                              # 0 top
    ["ocr"],                                        # 1 upper-right
    ["copy"],                                       # 2 right
    ["paste"],                                      # 3 lower-right
    ["delete"],                                     # 4 bottom
    ["merge"],                                      # 5 lower-left
    ["align_left", "align_right", "align_hcenter"], # 6 left (stacked)
    ["translate"],                                  # 7 upper-left
]

# Default menu template: one trigger key per menu, sector count sized to the
# content (4/6/8) so no ring sector is left as a dead zone.  Undo/redo, page
# navigation and zoom were dropped from the defaults (2026-08-14): they have
# universal shortcuts (Ctrl+Z/Y, PgUp/PgDn, wheel) and only waste menu slots.
# Each menu keeps two *independent* layouts — ``slots`` (ring) and ``panels``
# (list): switching the style only shows one, they never convert into each
# other (decision 2026-08-16).
DEFAULT_PIE_MENUS = [
    {
        "id": "edit",
        "name": "Editing",
        "trigger": "Tab",
        "sectors": 8,
        "layout": "ring",
        "slots": [
            ["copy", "copy_src"],
            ["paste", "paste_src"],
            ["delete"],
            ["clip_overflow", "seq_badge", "overflow_mode"],
            [],
            ["drag_decorations", "snap_alignment"],
            [],
            []
        ],
        "direction": "right",
        "panels": [
            ["copy", "copy_src"],
            ["paste", "paste_src"],
            ["delete"],
            ["drag_decorations"],
            ["snap_alignment", "seq_badge", "clip_overflow"]
        ]
    },
    {
        "id": "align",
        "name": "Alignment",
        "trigger": "X",
        "sectors": 8,
        "layout": "list",
        "slots": [
            ["align_top"],
            ["align_vcenter", "align_hcenter"],
            ["align_right"],
            [],
            ["align_bottom"],
            ["reset_angle", "squeeze"],
            ["align_left"],
            ["merge"]
        ],
        "direction": "right",
        "panels": [
            ["align_top", "align_bottom"],
            ["align_hcenter", "align_vcenter"],
            ["align_left", "align_right"],
            ["reset_angle", "squeeze", "merge"],
            []
        ]
    },
    {
        "id": "pipeline",
        "name": "Pipeline",
        "trigger": "C",
        "sectors": 4,
        "layout": "ring",
        "slots": [
            [],
            [],
            [],
            ["ocr_translate_inpaint", "ocr_translate", "ocr"]
        ],
        "direction": "right",
        "panels": [
            [],
            [],
            [],
            [],
            []
        ]
    }
]


def migrate_legacy_pie(legacy: List[List[str]]) -> List[dict]:
    """Migrate a legacy ``pie_sectors`` value to the ``pie_menus`` model.

    An untouched default becomes the three-menu template; a customized
    layout is kept as the first ("edit") menu with the two other default
    menus appended (the user can delete them).  The legacy layout is
    always 8 sectors — keep its own sector count, not the new template's.
    """
    if legacy == _LEGACY_PIE_SECTORS:
        return copy.deepcopy(DEFAULT_PIE_MENUS)
    # The legacy model had no list layout — seed one from the ring's right
    # half (sectors 0..n//2 incl. the poles) so the first style switch is
    # coherent.  The two layouts stay independent afterwards.
    # ``normalize_pie_menu`` re-validates the panel shape/caps on load.
    panels = [list(s) for s in legacy[: len(legacy) // 2 + 1]]
    panels += [[] for _ in range(5 - len(panels))]
    menus = [dict(DEFAULT_PIE_MENUS[0], sectors=len(legacy),
                  slots=copy.deepcopy(legacy), panels=panels)]
    menus += [copy.deepcopy(m) for m in DEFAULT_PIE_MENUS[1:]]
    return menus


@nested_dataclass
class AutoTateChuYokoConfig(Config):
    """Automatic tate-chu-yoko detection settings (upstream v1.5.12 parity).

    Consumed by the pipeline's background tate-chu-yoko pass; its UI is
    added in a later node.
    """

    enabled: bool = False
    max_length: int = 4
    include_numbers: bool = True
    include_letters: bool = False
    additional_chars: str = ""

    def allowed_characters(self) -> frozenset[str]:
        """Return the configured character categories as one lookup set."""
        characters = set(self.additional_chars)
        if self.include_numbers:
            characters.update(string.digits)
        if self.include_letters:
            characters.update(string.ascii_letters)
        return frozenset(characters)

    def __post_init__(self) -> None:
        for setting in fields(self):
            value = getattr(self, setting.name)
            valid = type(value) is setting.type
            if setting.name == "max_length":
                valid = valid and 1 <= value <= 99
            if not valid:
                LOGGER.warning(
                    f"Discard invalid auto_tate_chu_yoko.{setting.name} config."
                )
                setattr(self, setting.name, setting.default)


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
    check_update_on_startup: bool = False

    let_fntsize_flag: int = 0
    let_fntstroke_flag: int = 0
    let_alignment_flag: int = 0
    let_writing_mode_flag: int = 0
    let_family_flag: int = 0
    punctuation_position: int = PunctuationPosition.Simplified
    halfwidth_jp_corner_brackets: bool = False
    halfwidth_jp_corner_brackets_horizontal: bool = False
    auto_tate_chu_yoko: AutoTateChuYokoConfig = field(
        default_factory=AutoTateChuYokoConfig
    )
    compact_vertical_punctuation_spacing: bool = True
    quick_insert_characters: str = "『』「」♥♡★☆※♩♬"
    let_uppercase_flag: bool = True
    auto_squeeze_after_run: bool = True
    # 描边色自动跟随文字反色（黑字白边/白字黑边）全局开关；关闭后未手动指定的
    # 块描边色按存档 srgb 渲染，不再随字体颜色联动。
    stroke_auto_follow: bool = True
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
    # 「一键精简」映射 {被隐藏家族名: 规范名}（utils/font_scan.py::
    # compute_simplify_map 产物），与手动 excluded_fonts 分开管理
    simplified_font_map: Dict[str, str] = field(
        default_factory=lambda: dict()
    )
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
    open_image_fit_window: bool = False
    fit_window_on_page_switch: bool = False
    show_text_style_preset: bool = True
    expand_tstyle_panel: bool = True
    show_text_effect_panel: bool = True
    expand_teffect_panel: bool = True
    text_advanced_format_panel: bool = True
    expand_tadvanced_panel: bool = True
    text_transform_panel: bool = True
    expand_ttransform_panel: bool = True
    # 注解停靠面板（PS 式图标栏入口，Ruby/连字/旧式数字）：记忆开合
    annotation_dock_open: bool = False
    # 图标栏停靠面板开合记忆：着重号 / 文本样式 / 文本变换
    emphasis_dock_open: bool = False
    textstyle_dock_open: bool = False
    transform_dock_open: bool = False
    show_seq_badge: bool = True
    overflow_mode: bool = False  # 过界模式 — 画布边界视觉指示 + 文字块跨边界裁剪
    clip_text_overflow: bool = True  # 翻译填充时裁剪溢出文字并显示黄色提示框，拖拽调整后解除
    show_decorations_during_drag: bool = False  # 拖拽调整时保留描边/阴影（代价是帧率下降）
    # 术语/剧情工作台：耗时/耗费操作（一键准备等）执行前弹确认窗说明步骤与
    # API 花销；默认开启，弹窗内「不再提示」或设置面板翻译器页可关闭/恢复
    workbench_confirm_costly: bool = True

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

    # ── Pie menus (canvas ring menus) ─────────────────────────
    # Each menu: {id, name, trigger, sectors, layout, slots}
    #   id      — stable identifier
    #   name    — display name (defaults are tr keys; user renames are free text)
    #   trigger — QKeySequence string; "" = not reachable by key
    #   sectors — 4 / 6 / 8
    #   layout  — "ring" (extension point for future layouts)
    #   slots   — `sectors` lists, each up to SECTOR_MAX_CARDS command ids
    # Command ids reuse COMMAND_REGISTRY (ui/context_menu_config.py).
    pie_menus: List[dict] = field(
        default_factory=lambda: copy.deepcopy(DEFAULT_PIE_MENUS)
    )

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

        # Backward compat: single-menu pie_sectors -> pie_menus
        if "pie_menus" not in config_dict and "pie_sectors" in config_dict:
            config_dict["pie_menus"] = migrate_legacy_pie(
                config_dict.pop("pie_sectors")
            )
        elif "pie_sectors" in config_dict:
            config_dict.pop("pie_sectors")

        # Mirror upstream v1.5.12 guard: a malformed persisted value would
        # otherwise crash consumers that iterate the characters.
        if not isinstance(config_dict.get("quick_insert_characters", ""), str):
            LOGGER.warning(
                "Discard invalid quick_insert_characters config: expected a string."
            )
            config_dict.pop("quick_insert_characters")

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


def sanitize_shortcuts(shortcuts: dict) -> dict:
    """Return a cleaned copy of the user shortcut map.

    Hand-edited or legacy ``config.json`` values can be malformed: a value
    that is not a list, non-string keys, or duplicated sequences would crash
    the shortcut editor (``QLabel(non-str)``) or be silently dropped.  Keep
    only known-string sequences, drop everything else, and collapse
    intra-list duplicates while preserving order.  A ``None`` value is
    treated as "no override" and removed so the default keys apply.
    """
    if not isinstance(shortcuts, dict):
        return {}
    cleaned = {}
    for action_id, keys in shortcuts.items():
        if not isinstance(action_id, str):
            LOGGER.warning(f"sanitize_shortcuts: dropping non-str action id {action_id!r}")
            continue
        if keys is None:
            continue
        if not isinstance(keys, list):
            keys = [keys]
            LOGGER.warning(
                f"sanitize_shortcuts: {action_id} value is not a list, wrapped: {keys!r}"
            )
        kept = []
        for k in keys:
            if not isinstance(k, str) or not k:
                LOGGER.warning(
                    f"sanitize_shortcuts: dropping invalid shortcut for {action_id}: {k!r}"
                )
                continue
            if k not in kept:
                kept.append(k)
        cleaned[action_id] = kept
    return cleaned


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

    # Sanitize persisted shortcut data before use: malformed values would
    # crash the shortcut editor or be silently ignored at runtime.
    pcfg.shortcuts = sanitize_shortcuts(pcfg.shortcuts)

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
