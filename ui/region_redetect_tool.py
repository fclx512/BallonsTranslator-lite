"""区域再检测的 UI 层：手势接线 → 后台「检测 + OCR」 → 一步撤销。

与 ``ui/region_redetect.py``（任务层，无 Qt widget）的分工：这里只做三件事——

1. ``RedetectCommand``：把一次区域再检测的**数据层写回**包成一条可撤销命令，
   ``redo`` 是唯一写入口（整表覆盖写，幂等）；
2. ``_RedetectWorker``：检测会建 ONNX 会话（数秒），OCR 也要时间，故统统放进
   后台线程；结果经信号回主线程，线程里不碰任何 QWidget；
3. ``RegionRedetectTool``：控制器——接画布的 ``region_redetect_rect`` 信号、
   给通知中心的进度提示、把计划交给命令。

**一次拉框 = 一步撤销**：新增块、被替换掉的旧块、OCR 文本、自动挂上的标签都在
同一条命令里（``RedetectCommand`` 抓的是整页块列表的前后快照）。**掩码不在撤销
范围内**——它是派生的像素数据，写法与管线的修复阶段一致（栈外 ``save_mask`` +
``bump_page_image_generation``），见 ``ui/region_redetect.py::RegionRedetect``
的 ``paste_mask``。

**为什么 OCR 不直接复用 ``ui/module_manager.py`` 的 ``_blktrans_pipeline``**：
那条路径的收尾是压一条 ``RunBlkTransCommand``（``blk_ids`` 非空时），一次拉框
就会产生两个撤销步，与"一步撤销"冲突；本模块按它的口径直接走
``modules/ocr/base.py::OCRBase`` 的 ``run_ocr()``——**必须走模块本身**，否则丢掉
``utils/block_tags.py::apply_ocr_confidence_tag`` 的自动挂标（该挂标写在
``modules/ocr/ocr_onnx.py`` 内部）。
"""

from typing import List, Optional

from qtpy.QtCore import QCoreApplication, QObject, QRectF, QThread, Signal

from utils.logger import logger as LOGGER

from .custom_widget.notification import notification
from .region_redetect import (
    SKIP_NO_DETECTION,
    SKIP_NO_IMAGE,
    SKIP_TOO_SMALL,
    RedetectPlan,
    RegionRedetect,
)

try:  # qtpy 版本差异下的 QUndoCommand 归属
    from qtpy.QtWidgets import QUndoCommand
except ImportError:  # pragma: no cover
    from qtpy.QtGui import QUndoCommand

# 通知中心的键（同键刷新而不是堆叠）
_ACTIVITY_KEY = "region-redetect"
_TOAST_KEY = "region-redetect"


class RedetectCommand(QUndoCommand):
    """一次区域再检测的写回：替换 + 新增 + 掩码，合成一步撤销。

    与 ``ui/scenetext_manager.py::CreateItemCommand`` 的"构造期已执行、redo 首次
    跳过"相反：**构造期不写任何东西**，``redo`` 才是唯一写入口。``QUndoStack``
    的 push 会立刻调一次 ``redo``，所以效果一样，而这样"改前／改后"两个端点都在
    命令里、两向对称，不需要手工的首次跳过计数。

    页代数**不加**：这是栈内写入（与 ``DeleteBlkItemsCommand``、
    ``RearrangeBlksCommand`` 同性质），``bump_page_generation`` 会把本页既有
    撤销历史（含本命令）一并僵尸化，那是给栈外整体换新页块列表用的。
    """

    def __init__(self, canvas, scene_manager, task: RegionRedetect, plan: RedetectPlan):
        super().__init__(
            QCoreApplication.translate("UndoCommand", "Region Re-detect")
        )
        self.canvas = canvas
        self.scene_manager = scene_manager
        self.task = task
        self.plan = plan
        self.page_key = plan.page_key
        proj = canvas.imgtrans_proj
        self.before_blocks: List = list(proj.pages.get(plan.page_key) or [])
        self.report = task.build_page(plan)
        self.after_blocks: List = list(self.report["blocks"])

    def redo(self) -> None:
        self._write(self.after_blocks, paste_mask=True)

    def undo(self) -> None:
        self._write(self.before_blocks, paste_mask=False)

    def _write(self, blocks: List, *, paste_mask: bool) -> None:
        proj = self.canvas.imgtrans_proj
        if proj is None or self.page_key not in proj.pages:
            return
        proj.pages[self.page_key] = list(blocks)
        if paste_mask:
            self.report["mask_written"] = self.task.paste_mask(self.plan)
        # ④ 当前页重建视觉层（数据 → UI）。此后禁止再调
        # ui/scenetext_manager.py::SceneTextManager.updateTextBlkList（UI → 数据
        # 方向，会用旧面板值覆盖新数据）。
        if self.page_key == proj.current_img and self.scene_manager is not None:
            self.scene_manager.updateSceneTextitems()
        self.canvas.updateLayers()


class _RedetectWorker(QThread):
    """后台跑「检测 → OCR」；不碰 QWidget，结果经信号回主线程。"""

    plan_ready = Signal(object)
    """``RedetectPlan``；检测本身失败时为 ``None``。"""

    step_failed = Signal(str, str)
    """``(阶段, 消息)``——``"detect"``／``"ocr"``。OCR 失败不阻断块的并入。"""

    def __init__(
        self,
        task: RegionRedetect,
        page_key: str,
        rect: List[int],
        ocr_module=None,
        parent=None,
    ):
        super().__init__(parent)
        self.task = task
        self.page_key = page_key
        self.rect = list(rect)
        self.ocr_module = ocr_module

    def run(self) -> None:
        try:
            plan = self.task.plan(self.page_key, self.rect)
        except Exception as e:
            LOGGER.error(f"Region redetect failed: {e}")
            self.step_failed.emit("detect", str(e))
            self.plan_ready.emit(None)
            return
        if plan.ok and self.ocr_module is not None:
            self._run_ocr(plan)
        self.plan_ready.emit(plan)

    def _run_ocr(self, plan: RedetectPlan) -> None:
        """走 OCR 模块本身的 ``run_ocr``（与 ``ui/module_manager.py`` 的
        ``_blktrans_pipeline`` mode=0 同一口径），自动挂标随之内建。"""
        img = self.task.page_image(plan.page_key)
        if img is None:
            self.step_failed.emit("ocr", "no-image")
            return
        try:
            self.ocr_module.run_ocr(img, plan.new_blocks, split_textblk=True)
        except Exception as e:
            LOGGER.error(f"Region redetect OCR failed: {e}")
            self.step_failed.emit("ocr", str(e))


class RegionRedetectTool(QObject):
    """区域再检测的控制器：接手势、给进度提示、落一条撤销命令。

    Args:
        proj: 项目实例（与画布同一个对象）。
        canvas: ``ui/canvas.py::Canvas``。
        scene_manager: ``ui/scenetext_manager.py::SceneTextManager``。
        module_manager: ``ui/module_manager.py::ModuleManager``；只读它的 OCR
            模块实例与管线运行状态。
    """

    completed = Signal(dict)
    """一次拉的框走完流程后的报告（``applied`` 为假表示没写任何东西）。"""

    def __init__(self, proj, canvas, scene_manager, module_manager, parent=None):
        super().__init__(parent)
        self.proj = proj
        self.canvas = canvas
        self.scene_manager = scene_manager
        self.module_manager = module_manager
        self.task = RegionRedetect(proj)
        self._worker: Optional[_RedetectWorker] = None

    # ── 模式 ────────────────────────────────────────────────────────

    def set_mode(self, enabled: bool) -> None:
        """开关区域再检测模式（画布据此把左键拖框改道到本工具）。"""
        self.canvas.setRegionRedetectMode(bool(enabled))

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def shutdown(self) -> None:
        """关窗收尾：等在跑的检测线程退出，并释放本工具持有的检测器。

        ONNX 推理不可中断，只能等（实测一次裁剪检测约 0.05~1.3s）；不等就是
        "QThread: Destroyed while thread is still running" 后在退出时崩。
        """
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.wait(10000)
            except Exception as e:  # pragma: no cover - 收尾路径
                LOGGER.warning(f"Region redetect worker wait failed: {e}")
        self.task.unload_detector()

    # ── 入口 ────────────────────────────────────────────────────────

    def request(self, rect: QRectF) -> bool:
        """接到画布的拉框（``Canvas.region_redetect_rect``）；返回是否已受理。"""
        if self.proj is None or not self.proj.img_valid or self.proj.current_img is None:
            return False
        if self.is_running():
            notification.toast(
                self.tr("Region re-detect is already running"),
                key=_TOAST_KEY,
            )
            return False
        if self._pipeline_running():
            notification.toast(
                self.tr("Region re-detect is unavailable while the pipeline runs"),
                kind="warning",
                key=_TOAST_KEY,
            )
            return False

        page_rect = [
            int(round(rect.x())),
            int(round(rect.y())),
            int(round(rect.right())),
            int(round(rect.bottom())),
        ]
        side = min(page_rect[2] - page_rect[0], page_rect[3] - page_rect[1])
        if side < self.task.config.min_side:
            # 方案 §七.5：框太小不写任何东西，但要**给提示**（不静默）
            notification.toast(
                self._skip_message(SKIP_TOO_SMALL),
                kind="warning",
                key=_TOAST_KEY,
            )
            return False

        notification.activity(_ACTIVITY_KEY, True, self._status_text())
        worker = _RedetectWorker(
            self.task, self.proj.current_img, page_rect, self._ocr_module(), parent=self
        )
        worker.plan_ready.connect(self._on_plan_ready)
        worker.step_failed.connect(self._on_step_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()
        return True

    # ── 收尾 ────────────────────────────────────────────────────────

    def _on_plan_ready(self, plan: Optional[RedetectPlan]) -> None:
        notification.activity(_ACTIVITY_KEY, False)
        # 用完即卸：会话不留成常驻内存。默认设备是 CPU，重建只要 0.17s（CUDA 会话
        # 一次就吃掉主机工作集 +835MB 且卸载只回收 50MB，见 ui/region_redetect.py
        # 模块 docstring 的「内存」一节）。掩码写回与命令重放都不再需要检测器。
        self.task.unload_detector()
        if plan is None:
            notification.toast(
                self.tr("Region re-detect failed, see the log for details"),
                kind="error",
                key=_TOAST_KEY,
            )
            self.completed.emit({"applied": False, "error": "detect-failed"})
            return
        if plan.page_key != self.proj.current_img:
            # 检测期间切了页：视觉层不是这一页，写进去用户也看不见，直接作废
            notification.toast(
                self.tr("Page changed — region re-detect cancelled"),
                kind="warning",
                key=_TOAST_KEY,
            )
            self.completed.emit({"applied": False, "error": "page-changed"})
            return
        if plan.skip is not None:
            notification.toast(
                self._skip_message(plan.skip),
                kind="warning",
                key=_TOAST_KEY,
            )
            self.completed.emit({"applied": False, "error": plan.skip})
            return

        command = RedetectCommand(
            self.canvas, self.scene_manager, self.task, plan
        )
        self.canvas.push_undo_command(command)
        report = dict(command.report)
        report["applied"] = True
        report["page"] = plan.page_key
        notification.toast(
            self.tr("Region re-detect: added %1, replaced %2")
            .replace("%1", str(report["added"]))
            .replace("%2", str(len(report["replaced"]))),
            kind="success",
            key=_TOAST_KEY,
        )
        self.completed.emit(report)

    def _on_step_failed(self, stage: str, message: str) -> None:
        if stage == "ocr":
            # 整句必须是单个字符串字面量（见 ui/mainwindowbars.py 同款注释）
            notification.toast(
                self.tr("Region re-detect OCR failed; blocks were added without source text"),
                kind="warning",
                key=_TOAST_KEY,
            )
        else:
            LOGGER.error(f"Region redetect step {stage} failed: {message}")

    def _on_worker_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

    # ── 小工具 ──────────────────────────────────────────────────────

    def _pipeline_running(self) -> bool:
        thread = getattr(self.module_manager, "imgtrans_thread", None)
        try:
            return bool(thread is not None and thread.isRunning())
        except RuntimeError:
            return False

    def _ocr_module(self):
        """当前 OCR 模块实例（未选／未加载时返回 ``None``，块仍会并入）。"""
        thread = getattr(self.module_manager, "ocr_thread", None)
        module = getattr(thread, "module", None)
        if module is None or getattr(module, "name", "") == "none_ocr":
            return None
        return module

    def _status_text(self) -> str:
        """进度提示文案（决策 8：不预热，但加载哪个模型要说清楚）。"""
        if not self.task.detector_loaded():
            return self.tr("Region re-detect: loading detector %1 …").replace(
                "%1", str(self.task.effective_detector_name)
            )
        return self.tr("Region re-detect: detecting …")

    def _skip_message(self, skip: str) -> str:
        if skip == SKIP_TOO_SMALL:
            return self.tr("The box is too small")
        if skip == SKIP_NO_IMAGE:
            return self.tr("Region re-detect needs the page image to run")
        if skip == SKIP_NO_DETECTION:
            return self.tr("No text detected in the region")
        return self.tr("Region re-detect found nothing to do")
