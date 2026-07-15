import gc
import importlib
import os
import re
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Callable, Dict, List, Union

from utils import shared
from utils.lock import aquire_model_loading_lock, release_model_loading_lock
from utils.logger import logger as LOGGER

GPUINTENSIVE_SET = {"cuda", "mps", "xpu", "privateuseone"}


def register_hooks(
    hooks_registered: OrderedDict, callbacks: Union[List, Callable, Dict]
):
    if callbacks is None:
        return
    if isinstance(callbacks, (Dict, OrderedDict)):
        for k, v in callbacks.items():
            hooks_registered[k] = v
    else:
        nhooks = len(hooks_registered)

        if isinstance(callbacks, Callable):
            callbacks = [callbacks]
        for callback in callbacks:
            hk = "hook_" + str(nhooks).zfill(2)
            while True:
                if hk not in hooks_registered:
                    break
                hk = hk + "_" + str(time.time_ns())
            hooks_registered[hk] = callback
            nhooks += 1


def patch_module_params(cfg_param, module_params, module_name: str = ""):
    # cfg_param = config_params[module_key]
    cfg_key_set = set(cfg_param.keys())
    module_key_set = set(module_params.keys())
    for ck in cfg_key_set:
        if ck not in module_key_set:
            if ck.startswith("_"):
                continue
            LOGGER.warning(f"Found invalid {module_name} config: {ck}")
            cfg_param.pop(ck)

    for mk in module_key_set:
        if mk not in cfg_key_set:
            if not mk.startswith("__") and mk != "description":
                LOGGER.info(f"Found new {module_name} config: {mk}")
            cfg_param[mk] = module_params[mk]
        else:
            mparam = module_params[mk]
            cparam = cfg_param[mk]
            if isinstance(mparam, dict):
                tgt_type = mparam.get("data_type", type(mparam["value"]))
                if isinstance(cparam, dict):
                    if "value" in cparam:
                        v = cparam["value"]
                    elif isinstance(mparam["value"], dict):
                        for k in mparam["value"]:
                            if k in cparam:
                                mparam["value"][k] = cparam[k]
                        v = mparam["value"]
                    else:
                        v = mparam["value"]
                else:
                    v = cparam
                valid = True
                if tgt_type is not type(v):
                    try:
                        v = tgt_type(v)
                    except (ValueError, TypeError):
                        valid = False
                        LOGGER.warning(
                            f"Invalid param value {v} for defined dtype: {tgt_type}, it will be set to default value: {mparam}"
                        )
                if valid:
                    mparam["value"] = v
                cfg_param[mk] = mparam
            else:
                if type(cparam) is not type(mparam):
                    if not isinstance(mparam, dict) and isinstance(cparam, dict):
                        cparam = cparam["value"]
                    try:
                        cfg_param[mk] = type(mparam)(cparam)
                    except ValueError:
                        LOGGER.warning(
                            f"Invalid param value {cparam} for defined dtype: {type(mparam)}, it will be set to default value: {mparam}"
                        )
                        cfg_param[mk] = mparam

    cfg_key_list = [k for k in cfg_param.keys() if not k.startswith("_")]
    module_key_list = list(module_params.keys())
    if cfg_key_list != module_key_list:
        internal = {k: cfg_param[k] for k in cfg_param if k.startswith("_")}
        new_params = {key: cfg_param[key] for key in module_key_list}
        new_params.update(internal)
        cfg_param.clear()
        cfg_param.update(new_params)
        module_key_set = set(module_params.keys())
    cfg_param["__param_patched"] = True
    return cfg_param


def merge_config_module_params(
    config_params: Dict, module_keys: List, get_module: Callable
) -> Dict:
    for module_key in module_keys:
        module_params = get_module(module_key).params
        if module_params is None:
            module_params = {}
        if module_key not in config_params or config_params[module_key] is None:
            config_params[module_key] = module_params
        else:
            if module_params:
                patch_module_params(
                    config_params[module_key], module_params, module_key
                )

    # Auto-select best available device on each startup,
    # overriding any stale "cpu" setting from a previous CPU-mode run.
    if not _force_cpu and DEFAULT_DEVICE != "cpu":
        for module_key in module_keys:
            params = config_params.get(module_key)
            if params and "device" in params and isinstance(params["device"], dict):
                if params["device"]["value"] == "cpu":
                    params["device"]["value"] = DEFAULT_DEVICE

    return config_params


def standardize_module_params(params):
    if params is None:
        return
    for k, v in params.items():
        if not isinstance(v, dict) and k not in {
            "description"
        }:  # remember to exclude special keys here
            v = {"value": v}
        if isinstance(v, dict) and "data_type" not in v:
            v["data_type"] = type(v["value"])
        params[k] = v


class BaseModule:
    params: Dict = None
    logger = LOGGER

    _preprocess_hooks: OrderedDict = None
    _postprocess_hooks: OrderedDict = None

    download_file_list: List = None
    download_file_on_load = False

    _load_model_keys: set = None

    # Optional extra pip packages this module needs beyond what is
    # declared in pyproject.toml.  Each entry is a PEP 508 requirement
    # string (e.g. ``"torch>=2.0"``).  Checked & auto-installed on
    # first ``load_model()`` call via :meth:`ensure_dependencies`.
    requires_packages: List[str] = []

    def __init__(self, **params) -> None:
        standardize_module_params(self.params)
        if self.params is not None and "__param_patched" not in params:
            params = patch_module_params(params, self.params, self)
        if params:
            if self.params is None:
                self.params = params
            else:
                self.params.update(params)

    @classmethod
    def register_postprocess_hooks(cls, callbacks: Union[List, Callable]):
        """
        these hooks would be shared among all objects inherited from the same super class
        """
        assert cls._postprocess_hooks is not None
        register_hooks(cls._postprocess_hooks, callbacks)

    @classmethod
    def register_preprocess_hooks(cls, callbacks: Union[List, Callable, Dict]):
        """
        these hooks would be shared among all objects inherited from the same super class
        """
        assert cls._preprocess_hooks is not None
        register_hooks(cls._preprocess_hooks, callbacks)

    def get_param_value(self, param_key: str):
        assert self.params is not None and param_key in self.params
        p = self.params[param_key]
        if isinstance(p, dict):
            return p["value"]
        return p

    def set_param_value(self, param_key: str, param_value, convert_dtype=True):
        assert self.params is not None and param_key in self.params
        p = self.params[param_key]
        if isinstance(p, dict):
            if convert_dtype:
                try:
                    val_type = p.get("data_type", type(p["value"]))
                    param_value = val_type(param_value)
                except ValueError:
                    dtype = type(p["value"])
                    self.logger.warning(
                        f"Invalid param value {param_value} for defined dtype: {dtype}"
                    )
            p["value"] = param_value
        else:
            if convert_dtype:
                try:
                    param_value = type(p)(param_value)
                except ValueError:
                    self.logger.warning(
                        f"Invalid param value {param_value} for defined dtype: {type(p)}, revert to original value {p}"
                    )
                    param_value = p
            self.params[param_key] = param_value

    def updateParam(self, param_key: str, param_content):
        self.set_param_value(param_key, param_content)

    @property
    def low_vram_mode(self):
        if "low vram mode" in self.params:
            return self.get_param_value("low vram mode")
        return False

    def is_cpu_intensive(self) -> bool:
        if self.params is not None and "device" in self.params:
            return self.params["device"]["value"] == "cpu"
        return False

    def is_gpu_intensive(self) -> bool:
        if self.params is not None and "device" in self.params:
            return self.params["device"]["value"] in GPUINTENSIVE_SET
        return False

    def is_computational_intensive(self) -> bool:
        if self.params is not None and "device" in self.params:
            return True
        return False

    def unload_model(self, empty_cache=False):
        model_deleted = False
        if self._load_model_keys is not None:
            for k in self._load_model_keys:
                if hasattr(self, k):
                    model = getattr(self, k)
                    if model is not None:
                        if hasattr(model, "unload_model"):
                            model.unload_model(empty_cache=False)
                        del model
                        setattr(self, k, None)
                        model_deleted = True

        if empty_cache and model_deleted:
            soft_empty_cache()

        return model_deleted

    def load_model(self):
        # Ensure extra pip packages are installed first
        self.ensure_dependencies()
        # Ensure model files are downloaded before loading
        self._ensure_model_files()
        aquire_model_loading_lock()
        self._load_model()
        release_model_loading_lock()
        return

    def _ensure_model_files(self):
        """Download declared model files if they are missing on disk.

        This replaces the old startup-time forced download in
        ``prepare_local_files_forall()`` — files are now fetched on
        demand when a module is first loaded.
        """
        if not self.download_file_list:
            return
        from utils.download_util import download_and_check_files

        for dl_entry in self.download_file_list:
            ok = download_and_check_files(**dl_entry)
            if not ok:
                self.logger.warning(
                    f"Failed to download model files for {self.__class__.__name__}. "
                    "Some features may be unavailable."
                )

        # Persist hash cache so subsequent checks skip re-calculation
        if shared.CACHE_UPDATED:
            shared.dump_cache()

    def ensure_dependencies(self):
        """Check and auto-install extra pip packages declared in
        ``requires_packages``.

        Only packages NOT already satisfied are installed.  Uses uv if
        available, falls back to pip.
        """
        if not self.requires_packages:
            return
        try:
            import importlib.metadata as importlib_metadata

            from packaging.requirements import Requirement
            from packaging.utils import canonicalize_name
        except (ImportError, ModuleNotFoundError):
            return  # packaging itself missing — shouldn't happen

        missing: list[str] = []
        for req_str in self.requires_packages:
            req = Requirement(req_str)
            try:
                dist = importlib_metadata.distribution(canonicalize_name(req.name))
                if not req.specifier.contains(dist.version, prereleases=True):
                    missing.append(req_str)
            except importlib_metadata.PackageNotFoundError:
                # Metadata name mismatch (e.g. onnxruntime installed as
                # onnxruntime-gpu).  Try a direct import as last resort —
                # if the top-level module can be loaded the dependency is
                # actually satisfied.
                import importlib as _il

                try:
                    _il.import_module(req.name)
                except ImportError:
                    missing.append(req_str)

        if not missing:
            return

        import subprocess
        import sys

        python = sys.executable
        # Prefer uv (does NOT support pip-specific --prefer-binary), fall back to pip
        try:
            subprocess.run(
                [python, "-m", "uv", "pip", "install", *missing],
                capture_output=True,
                timeout=300,
                check=True,
            )
            self.logger.info(
                f"Auto-installed extra deps for {self.__class__.__name__}: {missing}"
            )
        except Exception:
            try:
                subprocess.run(
                    [python, "-m", "pip", "install", *missing, "--prefer-binary"],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                self.logger.info(
                    f"Auto-installed extra deps (via pip) for {self.__class__.__name__}: {missing}"
                )
            except Exception as e:
                self.logger.warning(
                    f"Failed to auto-install extra deps for {self.__class__.__name__}: {e}"
                )

    def _load_model(self):
        return

    def all_model_loaded(self):
        if self._load_model_keys is None:
            return True
        for k in self._load_model_keys:
            if not hasattr(self, k) or getattr(self, k) is None:
                return False
        return True

    def __del__(self):
        self.unload_model()

    @property
    def debug_mode(self):
        return shared.DEBUG

    def flush(self, param_key: str):
        return None


TORCH_AVAILABLE = False
torch = None
DEFAULT_DEVICE = "cpu"
AVAILABLE_DEVICES = ["cpu"]
BF16_SUPPORTED = False
TORCH_DTYPE_MAP = {}

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
_force_cpu = os.environ.get("BALLOONTRANS_CPU_ONLY") == "1"

try:
    import torch

    TORCH_AVAILABLE = True

    DEFAULT_DEVICE = "cpu"
    AVAILABLE_DEVICES = ["cpu"]

    if not _force_cpu:
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            DEFAULT_DEVICE = "cuda"
            AVAILABLE_DEVICES.append(DEFAULT_DEVICE)
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            DEFAULT_DEVICE = "xpu"
            AVAILABLE_DEVICES.append(DEFAULT_DEVICE)
        if (
            hasattr(torch, "backends")
            and hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            DEFAULT_DEVICE = "mps"
            AVAILABLE_DEVICES.append(DEFAULT_DEVICE)

        try:
            import torch_directml

            if hasattr(torch, "privateuseone") and torch_directml.device_count() > 0:
                torch.dml = torch_directml
                DEFAULT_DEVICE = f"privateuseone:{torch.dml.default_device()}"
                AVAILABLE_DEVICES += [
                    f"privateuseone:{d}" for d in range(torch.dml.device_count())
                ]
        except Exception:
            pass

    BF16_SUPPORTED = False
    if _force_cpu:
        pass
    elif (
        DEFAULT_DEVICE == "cuda"
        and torch.cuda.is_bf16_supported()
        or DEFAULT_DEVICE == "xpu"
        and torch.xpu.is_bf16_supported()
    ):
        BF16_SUPPORTED = True
    if DEFAULT_DEVICE == "mps":
        BF16_SUPPORTED = True

    TORCH_DTYPE_MAP = {
        "fp32": torch.float32,
        "fp16": torch.float16,
        "bf16": torch.bfloat16,
    }
except ImportError:
    pass


def is_nvidia():
    if TORCH_AVAILABLE and DEFAULT_DEVICE == "cuda":
        if torch.version.cuda:
            return True
    return False


def is_intel():
    if TORCH_AVAILABLE and DEFAULT_DEVICE == "xpu":
        if torch.version.xpu:
            return True
    return False


def soft_empty_cache():
    gc.collect()
    if TORCH_AVAILABLE:
        if DEFAULT_DEVICE == "cuda":
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        elif DEFAULT_DEVICE == "xpu":
            torch.xpu.empty_cache()
        elif DEFAULT_DEVICE == "mps":
            torch.mps.empty_cache()


def DEVICE_SELECTOR(not_supported: list[str] = []):
    return deepcopy(
        {
            "type": "selector",
            "options": [
                opt
                for opt in AVAILABLE_DEVICES
                if all(device not in opt for device in not_supported)
            ],
            "value": DEFAULT_DEVICE
            if not any(DEFAULT_DEVICE in device for device in not_supported)
            else "cpu",
            "description": "Hardware device for inference (GPU recommended)",
        }
    )


MODULE_ROOT = Path(__file__).resolve().parent

from utils.registries import MODULE_SCRIPTS  # noqa: E402 — single source of truth


def import_module_registries(target_modules=None):
    """Eagerly import all module files — kept as a fallback for debugging only.

    The normal startup path uses ``init_module_registries()`` (lazy/AST-based).
    """

    def _load_module(module_dir: str, module_pattern: str):
        modules = os.listdir(module_dir)
        pattern = re.compile(module_pattern)
        module_path = module_dir.replace("/", ".")
        if not module_path.endswith("."):
            module_path += "."
        for module_name in modules:
            if pattern.match(module_name) is not None:
                try:
                    module = module_path + module_name.replace(".py", "")
                    importlib.import_module(module)
                except Exception as e:
                    LOGGER.warning(f"Failed to import {module}: {e}")

    if target_modules is None:
        target_modules = MODULE_SCRIPTS
    if isinstance(target_modules, str):
        target_modules = [target_modules]

    for k in target_modules:
        _load_module(**MODULE_SCRIPTS[k])


def init_module_registries(target_modules=None):
    """Startup entry point — registers lightweight ModuleSpecs via AST scanning.

    Real module imports are deferred until the user selects a module in the UI
    (via ``Registry.resolve_module()``).
    """
    from utils.lazy_registry import init_lazy_module_registries

    init_lazy_module_registries(target_modules)


def init_textdetector_registries():
    init_module_registries("textdetector")


def init_inpainter_registries():
    init_module_registries("inpainter")


def init_ocr_registries():
    init_module_registries("ocr")


def init_translator_registries():
    init_module_registries("translator")
