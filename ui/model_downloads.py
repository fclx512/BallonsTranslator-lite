"""模型依赖（pip 包 + 权重文件）的后台下载任务层。

原先这套执行器嵌在 ``ui/module_manager.py::_ensure_module_deps`` 的函数体里
（``_InstallWorker`` + ``_InstallDialog``），下载期间用一个模态窗锁住整个界面，
点「Later」还会连带取消模块切换。大模型（paddleocr-vl 约 1.9GB）在这个形态下
没法用，故把执行器提出来并公开，供两个入口共用：

1. 用户在底部栏 / 配置面板 / 运行对话框**选择**了某个模块；
2. 设置页 Models →「模型文件」节点点了**下载**。

行为定稿（``docs/技术实现/模型文件管理_设计方案.md`` §5）：

- 触发即后台线程开始：**不弹确认、不弹进度窗、不阻断任何交互**；
- 进度与结果只进终端（``utils/logger.py`` 双写 console 与 ``logs/*.log``，
  ``utils/download_util.py::download_url_to_file`` 的 tqdm 进度条写 stderr）；
- 下载中可取消；取消后**已完成的文件保留**、未写完的临时文件丢弃，下次跳过已存在的；
- 失败才打断用户一次，且文案里带上排障指引（:func:`format_failure_message`）。

线程约定：本模块的 QObject（注册表）**必须建在主线程**——它由 UI 入口首次调用时
创建，只做信号转发与状态查询，绝不碰 QWidget；真正的重活全在
:class:`ModelDownloadTask` 的线程里。

文案一律写成 ``QCoreApplication.translate()`` 的字面调用——第一个参数是上下文
``"model_downloads"``，第二个是要翻译的英文原文。**不要**包一层别名函数，也
**不要**在注释里写出「上下文 + 原文」两个相邻字面量的完整调用形态：
``scripts/i18n_common.py`` 的提取器是纯正则、不认 AST，注释里的字面调用会被
当成真调用收进 .ts（已踩过），且它**不支持隐式字符串拼接**，每条消息都必须是
单个字面量。
"""

import os.path as osp
import shutil
import subprocess
import sys
import threading

from qtpy.QtCore import QCoreApplication, QObject, QThread, Signal

from utils.download_util import DownloadCancelled
from utils.logger import logger as LOGGER
from utils.message import create_info_dialog

__all__ = [
    "ModelDownloadTask",
    "ModelDownloadRegistry",
    "format_failure_message",
    "gpu_required_message",
    "gpu_requirement_block",
    "missing_model_files_hint",
    "model_downloads",
]


def gpu_required_message(display_name: str) -> str:
    """「本机没有 GPU，所以不下」的说明文案（配声明了 ``requires_gpu`` 的模块）。

    三段短句：结论（拒绝）、原因（CPU 上的时间开销远大于它给的结果）、出路（有 N 卡换
    CUDA 版 torch／只有 CPU 就换内置模型）。

    写给**普通用户**看，不出现 token／自回归／VLM 这类术语。措辞由用户定稿（2026-09-20
    要求更简洁，删掉了原先「已经手工拿到文件」那条出路），第二段只说不提供**下载**——
    闸门本就只管下载，用户手工把权重放进去仍可运行（判据与取舍见设计 §5.5）。

    两个入口共用这一处文案：「模型文件」页点下载、选中模块时的自动补装
    （见 :func:`gpu_requirement_block`）。
    """
    return (
        QCoreApplication.translate("model_downloads", '"%1" needs a GPU, and this machine has no usable GPU acceleration — the download was refused.').replace("%1", display_name)
        + "\n\n"
        + QCoreApplication.translate("model_downloads", "This model is very demanding: on CPU the time it costs far exceeds what it can deliver, so it is not offered for download under a CPU-only PyTorch build. Install and use it under a GPU PyTorch build instead.")
        + "\n\n"
        + QCoreApplication.translate("model_downloads", "If this machine has a supported NVIDIA GPU, run install_cuda.bat from the project folder to switch PyTorch to the CUDA build, then download this model. If you only have a CPU environment, use one of the built-in models instead.")
    )


def gpu_requirement_block(module_type: str, key: str) -> str:
    """该模块是否因「本机没有加速设备」被拦；被拦时返回给用户看的文案。

    返回空串＝不拦（模块没声明 ``requires_gpu``，或本机有加速设备）。文案与拦不拦
    的判据收在这里一处，下载入口不必各写一遍——见
    ``modules/base.py::accelerator_available`` 与 ``modules/base.py`` 的
    ``requires_gpu`` 声明。
    """
    from modules import GET_MODULE_REQUIREMENTS

    info = GET_MODULE_REQUIREMENTS(module_type, key)
    if info is None or not info.get("requires_gpu"):
        return ""
    from modules.base import accelerator_available

    if accelerator_available():
        return ""
    return gpu_required_message(info["display_name"])


def missing_model_files_hint(display_name: str, requires_gpu: bool = False) -> str:
    """加载模型时缺权重的指引文案（配 ``modules.base.MissingModelFilesError`` 用）。

    大模型（``background_download_only``）在加载期只检查不下载，缺文件时抛异常；
    这里给的是「怎么把它弄到手」的唯一指路，避免用户看到底层的路径报错。

    ``requires_gpu`` 置位且本机没有加速设备时改说「这机器上没有 GPU」——此时指路去
    设置页下载是死路（下载入口本身就会拒绝，见 :func:`gpu_requirement_block`）。
    """
    if requires_gpu:
        from modules.base import accelerator_available

        if not accelerator_available():
            return gpu_required_message(display_name)
    return (
        QCoreApplication.translate("model_downloads", 'Model files for "%1" are not downloaded yet.').replace("%1", display_name)
        + "\n\n"
        + QCoreApplication.translate("model_downloads", "Open Settings → Models → Model Files and click Download. The download runs in the background and its progress is printed in the terminal.")
    )


def format_failure_message(display_name: str, code: str) -> str:
    """把失败码翻成一段可读的告知（含排障指引）。

    这套文案是接入以来踩过坑攒下的（HF 在国内直连不稳、pip 走 uv 会失败等），
    随旧的安装对话框一起保留下来，改成「失败时告知一次」而不是常驻窗里的红字。
    """
    if code.startswith("pip_failed:"):
        package = code.split(":", 1)[1]
        head = QCoreApplication.translate("model_downloads", 'Failed to install Python package "%1".').replace("%1", package)
        tail = QCoreApplication.translate("model_downloads", "Check the log in the terminal or in logs/ for details, then download again from Settings → Models → Model Files.")
        return head + "\n\n" + tail

    hints = {
        "network_hf_no_mirror": QCoreApplication.translate("model_downloads", "HuggingFace is not reachable and no mirror is configured.\nOpen Settings → Network & Mirror Settings, set hf_endpoint to\nhttps://hf-mirror.com, then download again."),
        "network_hf": QCoreApplication.translate("model_downloads", "HuggingFace may be blocked in your region.\nConfigure a mirror in Settings → Network & Mirror Settings, then download again."),
        "network_github": QCoreApplication.translate("model_downloads", "GitHub may not be reachable.\nConfigure a mirror in Settings → Network & Mirror Settings, then download again."),
        "network_other": QCoreApplication.translate("model_downloads", "Check your network connection. If you are in a restricted region, configure a download mirror in Settings → Network & Mirror Settings, then download again."),
        "internal_error": QCoreApplication.translate("model_downloads", "See the log in the terminal or in logs/ for details."),
    }
    hint = hints.get(code) or QCoreApplication.translate("model_downloads", "You can also place the files manually — the expected file list is shown in Settings → Models → Model Files.")
    head = QCoreApplication.translate("model_downloads", 'Failed to prepare model files for "%1".').replace("%1", display_name)
    return head + "\n\n" + hint


class ModelDownloadTask(QThread):
    """装 pip 依赖 + 下权重文件的后台线程。

    只发信号、不弹窗；``finished_with_result(False)`` 之后由注册表负责告知用户
    与分流（取消 vs 失败看 :attr:`cancelled_by_user`）。
    """

    status = Signal(str)          # 当前动作的可读文本
    log_line = Signal(str)        # 逐行日志（同时写 LOGGER）
    progress = Signal(int, int)   # 已完成条目数 / 总条目数
    finished_with_result = Signal(bool)

    def __init__(
        self,
        module_type: str,
        key: str,
        display_name: str = "",
        missing_packages=None,
        download_file_list=None,
        parent=None,
    ):
        super().__init__(parent)
        self.module_type = module_type
        self.key = key
        self.display_name = display_name or key
        self.missing_packages = list(missing_packages or [])
        self.download_file_list = list(download_file_list or [])
        self._cancel_event = threading.Event()
        self.cancelled_by_user = False
        self._failure_code = ""

    # ── 外部控制 ─────────────────────────────────────────────────────────

    def cancel(self):
        """请求取消。线程安全——下载循环里逐块检查这个标记。"""
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def failure_code(self) -> str:
        return self._failure_code

    # ── 线程体 ───────────────────────────────────────────────────────────

    def run(self):
        success = True
        has_hf_no_mirror = self._check_hf_no_mirror()
        try:
            if self.missing_packages:
                success = self._install_packages()
            if success and self.download_file_list:
                success = self._download_files(has_hf_no_mirror)
        except DownloadCancelled:
            self.cancelled_by_user = True
            self._log(">> Cancelled by user. Files already downloaded are kept.")
            success = False
        except Exception as e:  # 兜底：绝不让异常逃出 run() 把线程打哑
            self._failure_code = "internal_error"
            LOGGER.error("Model download task failed: %s", e, exc_info=True)
            self._log(f">> FAILED: {e}")
            success = False
        self.finished_with_result.emit(success)

    # ── pip 依赖 ─────────────────────────────────────────────────────────

    def _install_packages(self) -> bool:
        self.status.emit(QCoreApplication.translate("model_downloads", "Installing required packages…"))
        self._log(">> Packages: " + ", ".join(self.missing_packages))

        python = sys.executable
        uv_available = (
            subprocess.run(
                [python, "-m", "uv", "--version"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        runners = []
        if uv_available:
            runners.append([python, "-m", "uv", "pip", "install"])
        runners.append([python, "-m", "pip", "install"])

        def _pip_install(pkgs, *, no_deps=False):
            bases = runners if not no_deps else runners[::-1]
            for runner in bases:
                is_uv = "uv" in runner
                extra = []
                if no_deps:
                    extra = ["--no-deps"]
                elif not is_uv:
                    # pip 支持 --prefer-binary / --timeout，uv 不支持
                    extra = ["--prefer-binary", "--timeout", "30"]
                try:
                    subprocess.run(
                        [*runner, *pkgs, *extra],
                        timeout=300,
                        check=True,
                    )
                    return True
                except Exception:
                    continue
            sys_py = shutil.which("python")
            if sys_py and osp.realpath(sys_py) != osp.realpath(python):
                try:
                    subprocess.run(
                        [
                            sys_py,
                            "-m",
                            "pip",
                            "install",
                            *pkgs,
                            "--prefer-binary",
                            "--timeout",
                            "30",
                        ],
                        timeout=300,
                        check=True,
                    )
                    return True
                except Exception:
                    pass
            return False

        for package in self.missing_packages:
            if self._cancel_event.is_set():
                raise DownloadCancelled()
            self.status.emit(
                QCoreApplication.translate("model_downloads", "Installing %1…").replace("%1", package)
            )
            self._log(f">> Installing {package} …")
            if not _pip_install([package]):
                self._log(
                    f">> Package '{package}' failed with deps, retrying --no-deps …"
                )
                if not _pip_install([package], no_deps=True):
                    self._log(f">> FAILED: {package}")
                    self._failure_code = f"pip_failed:{package}"
                    return False
        self._log(">> Package installation complete.")
        return True

    # ── 权重文件 ─────────────────────────────────────────────────────────

    def _download_files(self, has_hf_no_mirror: bool) -> bool:
        from utils.download_util import download_and_check_files

        total = len(self.download_file_list)
        self.progress.emit(0, total)
        for index, entry in enumerate(self.download_file_list, start=1):
            if self._cancel_event.is_set():
                raise DownloadCancelled()
            url = entry.get("url", "?")
            files = entry.get("files") or []
            if isinstance(files, str):
                files = [files]
            label = osp.basename(files[0]) if files else url
            self.status.emit(
                QCoreApplication.translate("model_downloads", "Downloading model files (%1/%2)…")
                .replace("%1", str(index))
                .replace("%2", str(total))
            )
            self._log(f">> [{index}/{total}] {label}")
            self._log(f"   from: {url}")
            try:
                ok = download_and_check_files(
                    **entry, cancel_check=self._cancel_event.is_set
                )
            except DownloadCancelled:
                raise
            except Exception as e:
                self._log(f">> Error: {e}")
                ok = False
            if ok:
                self._log(f">> Downloaded: {label}")
            else:
                self._log(f">> FAILED: {label}")
                self._failure_code = self._classify_network_error(
                    url, has_hf_no_mirror
                )
                return False
            self.progress.emit(index, total)
        return True

    # ── 排障判定（都在工作线程里跑，不碰 Qt） ────────────────────────────

    def _check_hf_no_mirror(self) -> bool:
        """有 HF 地址但没配镜像 → True（最常见的失败原因）。"""
        if not self.download_file_list:
            return False
        try:
            from utils.config import pcfg

            if pcfg.mirror.hf_endpoint:
                return False
            for entry in self.download_file_list:
                if "huggingface.co" in entry.get("url", ""):
                    return True
        except Exception:
            pass
        return False

    def _classify_network_error(self, url: str, has_hf_no_mirror: bool) -> str:
        if "huggingface.co" in url and has_hf_no_mirror:
            return "network_hf_no_mirror"
        if "huggingface.co" in url:
            return "network_hf"
        if "github" in url:
            return "network_github"
        return "network_other"

    def _log(self, line: str):
        LOGGER.info(line)
        self.log_line.emit(line)


class ModelDownloadRegistry(QObject):
    """「谁在下载、怎么取消、防重复触发」的单例注册表。

    主线程持有；UI 只读它的信号来刷新状态，不直接管线程生命周期
    （``ModuleManager.parent()`` 取到的是 ``None``——创建时没传 parent——所以
    后台任务不能挂在某个 widget 上）。
    """

    task_started = Signal(str, str)              # module_type, key
    task_finished = Signal(str, str, bool)       # module_type, key, success
    task_status = Signal(str, str, str)          # module_type, key, text
    task_progress = Signal(str, str, int, int)   # module_type, key, done, total
    # 任何一次开始/结束都发一次，供「缺文件警示色」之类的全局状态刷新
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # {(module_type, key): ModelDownloadTask}；同时是「防重复触发」的锁
        self._tasks: dict = {}

    # ── 查询 ─────────────────────────────────────────────────────────────

    def is_running(self, module_type: str, key: str) -> bool:
        return (module_type, key) in self._tasks

    def is_busy(self) -> bool:
        return bool(self._tasks)

    def running_keys(self) -> list:
        return list(self._tasks.keys())

    def status_text(self, module_type: str, key: str) -> str:
        task = self._tasks.get((module_type, key))
        if task is None:
            return ""
        return getattr(task, "_last_status", "")

    def progress_of(self, module_type: str, key: str):
        task = self._tasks.get((module_type, key))
        if task is None:
            return None
        return getattr(task, "_last_progress", None)

    # ── 控制 ─────────────────────────────────────────────────────────────

    def start(self, module_type: str, key: str) -> bool:
        """为该模块起一个后台下载任务。返回是否真的起了。

        返回 ``False`` 的情形：已经在下载同一个模块、该模块无本地权重、
        文件与依赖都齐备（无事可做），或**该模块要求 GPU 而本机没有**——
        最后这条是硬闸门：pip 依赖与权重文件都不碰，调用方负责把
        :func:`gpu_requirement_block` 的文案告诉用户。**调用方不该把 False 当失败**
        ——「不缺东西」是常态。
        """
        if self.is_running(module_type, key):
            LOGGER.info(
                "Model download already running for %s/%s, skip duplicate start.",
                module_type,
                key,
            )
            return False

        reason = gpu_requirement_block(module_type, key)
        if reason:
            LOGGER.info(
                "Refused to download %s/%s: the module needs a GPU and this machine "
                "has none. See install_cuda.bat for the CUDA build of PyTorch.",
                module_type,
                key,
            )
            return False

        from modules import GET_MODULE_REQUIREMENTS

        info = GET_MODULE_REQUIREMENTS(module_type, key)
        if info is None:
            return False
        if not info["missing"] and not info["missing_packages"]:
            return False

        task = ModelDownloadTask(
            module_type,
            key,
            display_name=info["display_name"],
            missing_packages=info["missing_packages"],
            download_file_list=info["download_file_list"],
        )
        self._tasks[(module_type, key)] = task
        task.status.connect(
            lambda text, mt=module_type, k=key: self._on_status(mt, k, text)
        )
        task.progress.connect(
            lambda done, total, mt=module_type, k=key: self._on_progress(
                mt, k, done, total
            )
        )
        task.finished_with_result.connect(
            lambda ok, mt=module_type, k=key, t=task: self._on_finished(mt, k, t, ok)
        )

        LOGGER.info(
            "Start background download for %s/%s (%s): %d package(s) to install, "
            "%d download entry(ies). Progress is printed here and in logs/.",
            module_type,
            key,
            info["display_name"],
            len(info["missing_packages"]),
            len(info["download_file_list"]),
        )
        task.start()
        self.task_started.emit(module_type, key)
        self.changed.emit()
        return True

    def cancel(self, module_type: str, key: str) -> bool:
        task = self._tasks.get((module_type, key))
        if task is None:
            return False
        LOGGER.info("Cancelling model download for %s/%s …", module_type, key)
        task.cancel()
        return True

    def cancel_all(self):
        for task in list(self._tasks.values()):
            task.cancel()

    def wait_all(self, timeout_ms: int = 5000):
        """退出前收尾：请求取消并等线程结束，避免 QThread 带着活线程被销毁。"""
        self.cancel_all()
        for task in list(self._tasks.values()):
            if task.isRunning():
                task.wait(timeout_ms)

    # ── 内部转发 ─────────────────────────────────────────────────────────

    def _on_status(self, module_type, key, text):
        task = self._tasks.get((module_type, key))
        if task is not None:
            task._last_status = text
        self.task_status.emit(module_type, key, text)

    def _on_progress(self, module_type, key, done, total):
        task = self._tasks.get((module_type, key))
        if task is not None:
            task._last_progress = (done, total)
        self.task_progress.emit(module_type, key, done, total)

    def _on_finished(self, module_type, key, task, success):
        self._tasks.pop((module_type, key), None)
        if success:
            # 装完把新文件的 sha256 落一次哈希缓存，下次检查直接命中
            try:
                from utils import shared

                if shared.CACHE_UPDATED:
                    shared.dump_cache()
            except Exception:
                pass
        elif task.cancelled_by_user:
            LOGGER.info("Model download cancelled: %s/%s", module_type, key)
        else:
            LOGGER.error(
                "Model download failed for %s/%s (code=%s)",
                module_type,
                key,
                task.failure_code,
            )
            create_info_dialog(
                format_failure_message(task.display_name, task.failure_code)
            )
        self.task_finished.emit(module_type, key, success)
        self.changed.emit()


_REGISTRY = None


def model_downloads() -> ModelDownloadRegistry:
    """取全局下载注册表。

    **首次调用必须发生在主线程**（UI 入口），否则信号跨线程投递会错位。
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ModelDownloadRegistry()
    return _REGISTRY
