"""桌面端管线任务的主线程控制器：调度后台 runner、写回结果、驱动进度覆盖层。

**两条互相独立的路径，别混在一起**：

- **数据路径**：``ui/pipeline_runner.py::PipelineRunner`` 在后台用项目快照跑
  管线，逐页把结果交给本类；本类在主线程校验页签名、写回项目、渲染当前页，
  再 ``acknowledge`` 放行下一页。
- **表现路径**：``ui/pipeline_progress_overlay.py::PipelineProgressOverlay``
  盖住画布区显示进度。它只认识自己那几个 setter，不认识 runner。

**运行期间为什么直接屏蔽画布交互**：旧版允许边跑边改，于是要处理"用户改了
正在算的页"——靠页签名拒收，代价是用户哪怕只动了一个块，整页结果白算且无法
退回。既然交互能屏蔽（覆盖层压住画布 + 配置/模块/工作台已在 ``_controls``
里停用），冲突就无从发生。**但签名校验整条保留**：它是数据层的一致性门，
不是为"边跑边改"专门写的——队列里排着的图还在写盘、后台其他线程也可能碰
项目，那些不经过覆盖层，所以这层校验一步都不能省。

**逐页渲染**：默认写回后生成结果图，非当前页使用独立场景，不切换用户画布。
等待图像保存回执后才清脏并放行下一页；关闭此选项则只保存数据，访问时补渲染。
"""
import copy
import os
import time

from qtpy.QtCore import QObject, QRectF, QTimer, Qt
from qtpy.QtGui import QPainter
from qtpy.QtWidgets import QDialog, QGraphicsScene, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from utils.config import pcfg, RunStatus
from ui.custom_widget import ConfigTextEdit
from ui.batch_ops import BatchWriteback
from ui.pipeline_progress_overlay import PipelineProgressOverlay
from ui.pipeline_runner import PipelineRunner, page_signature, block_signature

#: 宽限期：点了优雅停止之后等多久转"疑似无响应"。模型调用与网络请求都不可
#: 中断，正常收尾一般几秒内就完；30 秒还没动静基本就是卡住了。
STOP_GRACE_SECONDS = 30

#: 强制退出前给还在写盘的图留的时间。超时也走，宁可少一张结果图，也不能
#: 因为一个后台写盘卡住就永远退不出去。
FORCE_EXIT_SAVE_WAIT = 3.0


def render_page_image(project, page):
    """Render a non-current page on the GUI thread without changing the canvas."""
    from ui.misc import ndarray2pixmap
    from ui.textitem import TextBlkItem

    view = copy.copy(project)
    view.set_current_img(page)
    base = view.notext_array if view.notext_array is not None else view.inpainted_array
    image = ndarray2pixmap(base, return_qimg=True).copy()
    scene = QGraphicsScene()
    rect = QRectF(0, 0, image.width(), image.height())
    scene.setSceneRect(rect)
    items = []
    try:
        for index, block in enumerate(project.pages[page]):
            item = TextBlkItem(copy.deepcopy(block), index)
            items.append(item)
            item._hide_badge = True
            item.refresh_seq_badge()
            item.refresh_tag_badge()
            item.settle_overflow_state()
            scene.addItem(item)
        painter = QPainter(image)
        try:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing |
                                   QPainter.RenderHint.TextAntialiasing |
                                   QPainter.RenderHint.SmoothPixmapTransform)
            scene.render(painter, rect, rect)
        finally:
            painter.end()
        return image
    finally:
        for item in items:
            item.release_render_resources()
        scene.clear()


class PipelineTasks(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.runner = None
        self.pending = False
        self._start_seq = 0
        self.candidates = []
        self.close_when_done = False
        self.overlay = None
        # 停止状态机：none → graceful →(宽限期)→ unresponsive → force
        self.stop_state = "none"
        self.stop_requested_at = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._check_stop_timeout)
        self.dialog = None
        self.image_edits = set()
        self.processed = 0
        self.state = {}
        window.canvas.content_modified.connect(self._note_edit)

    # ── 状态查询 ────────────────────────────────────────────────────

    def _note_edit(self):
        if self.busy() and self.window.canvas.draw_change_unsaved():
            self.image_edits.add(self.window.imgtrans_proj.current_img)

    def busy(self):
        return self.pending or self.runner is not None

    def guard(self):
        """拒绝启动第二个任务。

        覆盖层已经在跑，说明任务在进行中；这里**不弹窗**——覆盖层本身就是最
        响亮的提示，再叠一个对话框只会打断用户看进度。
        """
        return self.busy()

    def _controls(self, enabled):
        w = self.window
        # 配置、模块选择、工作台共用引擎，运行期间必须停用；画布由覆盖层压住
        for widget in (w.configPanel, w.glossary_workbench,
                       w.bottomBar.textdet_selector, w.bottomBar.ocr_selector,
                       w.bottomBar.inpaint_selector, w.bottomBar.trans_selector):
            widget.setEnabled(enabled)
        w.module_manager.background_task_active = not enabled
        # QShortcut 挂在主窗口上，覆盖层挡不住它（快捷键不走鼠标事件）：
        # 不禁用的话，运行中按翻页键还是会把画布翻走
        self._set_shortcuts_enabled(enabled)

    def _set_shortcuts_enabled(self, enabled: bool):
        registry = getattr(self.window, "shortcut_registry", None)
        if not registry:
            return
        for shortcuts in registry.values():
            for shortcut in shortcuts:
                shortcut.setEnabled(enabled)

    # ── 启动 ────────────────────────────────────────────────────────

    def _enabled_stages(self, mode: str) -> set:
        """本次真正会跑的阶段。没开的阶段不占界面（整行收掉，少四行噪音）。"""
        stages = set() if mode == "revise" else {"render"}
        if mode == "recognize":
            if pcfg.module.enable_detect:
                stages.add("detect")
            if pcfg.module.enable_ocr:
                stages.add("ocr")
        elif mode in {"translate", "revise"}:
            stages.add("translate")
        elif mode == "inpaint":
            stages.add("inpaint")
        return stages

    def _show_overlay(self, mode: str):
        """盖出进度覆盖层。宿主不存在（无画布的宿主/测试夹具）时静默跳过。"""
        host = getattr(self.window, "centralStackWidget", None)
        if host is None:
            return
        self._teardown_overlay()
        self.overlay = PipelineProgressOverlay(host)
        self.overlay.stop_graceful_requested.connect(self.request_graceful_stop)
        self.overlay.stop_force_requested.connect(self.request_force_stop)
        self.overlay.panel.set_stages(self._enabled_stages(mode))
        self.overlay.panel.title.setText({
            "recognize": self.tr("Recognize original text"),
            "translate": self.tr("Translate original text"),
            "inpaint": self.tr("Repair images"),
            "revise": self.tr("Generate revision candidates"),
        }.get(mode, self.tr("Running")))
        self.overlay.show()
        self.overlay.raise_()

    def start(self, mode, pages=None, *, overwrite=False, targets=None, hint=""):
        w = self.window
        if self.guard():
            return
        worker = getattr(w.glossary_workbench, "_worker", None)
        if w._cuda_work_in_progress() or (worker is not None and worker._busy):
            # 起不来也要给出路：走同一条"启动失败"收尾，面板留在屏幕上说明
            # 原因并带一个关闭钮（只弹提示不给关闭路径会把画布锁死）
            self._show_overlay(mode)
            self._fail_to_start(self.tr("Wait for the current background task to finish."))
            return
        if self.dialog is not None:
            self.dialog.close()
        self._start_seq += 1
        start_seq = self._start_seq
        self.pending = True
        self._show_overlay(mode)
        self._controls(False)
        if self.overlay is not None:
            self.overlay.panel.append_activity(self.tr("Saving current work before starting..."))
        save_failures = w.imsave_thread.failure_count
        try:
            w.conditional_save()
        except Exception as error:
            self._fail_to_start(str(error))
            return

        def begin():
            if start_seq != self._start_seq or not self.pending:
                return
            if w.imsave_thread.isRunning():
                QTimer.singleShot(100, begin)
                return
            try:
                if w.imsave_thread.failure_count != save_failures:
                    raise RuntimeError(self.tr("Images could not be saved. Fix the save error before starting."))
                w._sync_block_data()
                project = w.imgtrans_proj
                project.save()
                manager = w.module_manager
                names = {"recognize": [n for n, yes in (("textdetector", pcfg.module.enable_detect), ("ocr", pcfg.module.enable_ocr)) if yes],
                         "translate": ["translator"], "revise": ["translator"], "inpaint": ["inpainter"]}[mode]
                modules = {name: getattr(manager, name) for name in names}
                if not names or any(value is None for value in modules.values()):
                    raise RuntimeError(self.tr("A required module is not ready. Select it in Settings and retry."))
                if any(getattr(module, "name", None) != getattr(pcfg.module, name)
                       for name, module in modules.items()):
                    raise RuntimeError(self.tr("A required module is not ready. Select it in Settings and retry."))
                self.runner = PipelineRunner(project, pages if pages is not None else list(project.pages),
                                             mode, pcfg.module, modules, overwrite=overwrite,
                                             targets=targets, hint=hint, await_settlement=True, parent=self)
                self.project = project
                self.candidates = [c for c in self.candidates if c.get("project") is project]
                self.image_edits.clear()
                self.applied = self.conflicts = self.blocks = 0
                self.task_mode = mode
                self.processed = 0
                self.errors = []
                self.state = {}
                self.started = self.last_activity = time.monotonic()
                if self.overlay is not None:
                    panel = self.overlay.panel
                    engines = [str(getattr(module, "name", "") or getattr(module, "module_name", "") or type(module).__name__) for module in modules.values()]
                    context = self.tr("Engines: %1").replace("%1", " / ".join(engines))
                    if mode == "translate":
                        context += " · " + (self.tr("Replace existing translations") if overwrite else self.tr("Only empty translations"))
                    elif mode == "revise":
                        context += " · " + self.tr("Candidates require review before applying")
                        panel.footer_hint.setText(self.tr("Candidates are kept for review in this session."))
                    panel.context.setText(context)
                    panel.append_activity(self.tr("Task started"))
                self.stop_state = "none"
                self.runner.activity.connect(self.activity)
                self.runner.page_ready.connect(self.receive)
                self.runner.finished.connect(self.finished)
                self.pending = False
                self.runner.start()
                self.timer.start()
                self.refresh()
            except Exception as error:
                self.runner = None
                self.pending = False
                self._fail_to_start(str(error))

        begin()

    def _fail_to_start(self, message: str):
        """启动失败：把面板留在屏幕上，改为显示错误（用户要看见为什么没跑）。"""
        self.pending = False
        self._controls(True)
        self.timer.stop()
        self.watchdog.stop()
        self._panel_as_notice(self.tr("Could not start"), message)
        if self.overlay is not None:
            panel = self.overlay.panel
            panel.progress.setRange(0, 1)
            panel.progress.setValue(0)
            panel.progress_label.setText(self.tr("Task did not start"))
            panel.append_activity(message)

    def _panel_as_notice(self, title: str, message: str):
        """把进度面板改造成"只有一句话 + 一个关闭钮"的告知卡。

        阶段卡片与停止钮都要收掉，而且**必须断开强停信号**：那个按钮原本连的是
        ``request_force_stop``，改个文字就复用会让"关闭"弹出强制退出确认框
        ——在启动失败这种无害路径上摆一个能结束进程的按钮是危险的。
        """
        if self.overlay is None:
            return
        panel = self.overlay.panel
        panel.show_result(title, message)
        panel.stop_force.setText(self.tr("Close"))
        panel.stop_force.setEnabled(True)
        panel.stop_force.setObjectName("PipelineStopGraceful")
        panel.stop_force.style().unpolish(panel.stop_force)
        panel.stop_force.style().polish(panel.stop_force)
        try:
            panel.stop_force.clicked.disconnect()
        except TypeError:
            pass  # 本来就没连过
        panel.stop_force.clicked.connect(self._dismiss_overlay)
        panel.relayout()

    # ── 停止 ────────────────────────────────────────────────────────

    def cancel(self):
        """对外仍保留 ``cancel``：``MainWindow.closeEvent`` 与既有调用点用它。"""
        self.request_graceful_stop()

    def request_graceful_stop(self):
        """协作停止：等当前调用返回，丢弃未结算结果，保留已保存页面。"""
        if self.stop_state != "none":
            return
        self.stop_state = "graceful"
        self.stop_requested_at = time.monotonic()
        if self.runner is not None:
            self.runner.cancel()
        elif self.pending:
            # 还没起线程（等保存收尾）：直接撤销这一次启动
            self.pending = False
            self._teardown_overlay()
            self._controls(True)
            if self.close_when_done:
                QTimer.singleShot(0, self.window.close)
            return
        if self.overlay is not None:
            self.overlay.panel.enter_stopping_state(0)
        self.watchdog.start()
        self.refresh()

    def request_force_stop(self):
        """强制停止：先跟用户讲清楚代价，再尽力落盘、结束进程。"""
        if self._confirm_force_stop():
            self._force_exit()

    def _confirm_force_stop(self) -> bool:
        """二次确认。文案必须写明"已完成的页会保存、在途页会丢、程序会关闭"。"""
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("End program"))
        waited = int(time.monotonic() - self.stop_requested_at) if self.stop_requested_at else 0
        box.setText(self.tr("The current call has not returned after %1 s. End the program now?\n\nSaved pages stay in the project. Unsaved results and revision candidates will be lost. You will need to restart the app.").replace("%1", str(waited)))
        wait_btn = box.addButton(self.tr("Keep waiting"), QMessageBox.ButtonRole.RejectRole)
        force_btn = box.addButton(self.tr("End program"), QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(wait_btn)
        box.exec()
        return box.clickedButton() is force_btn

    def _force_exit(self):
        """不走 ``QThread.terminate()``：线程可能正握着 GIL 或在 CUDA 调用里，
        强杀会留下不一致的进程状态。改为尽力落盘后直接结束进程。"""
        self.timer.stop()
        self.watchdog.stop()
        try:
            self.window.conditional_save()
        except Exception:
            pass  # 已经要退出了，保存失败没有补救余地，别把它变成新的崩溃点
        deadline = time.monotonic() + FORCE_EXIT_SAVE_WAIT
        try:
            while self.window.imsave_thread.isRunning() and time.monotonic() < deadline:
                time.sleep(0.1)
        except Exception:
            pass
        os._exit(1)

    def _check_stop_timeout(self):
        """宽限期到了就转"疑似无响应"。

        **刻意不自动弹确认框**：点了停止之后用户很可能已经离开去干别的了，
        这时抢焦点弹个模态框只会更烦。改为面板自身变色 + 提示，要放弃自己按。
        """
        if self.stop_state != "graceful" or not self.stop_requested_at:
            return
        elapsed = time.monotonic() - self.stop_requested_at
        if self.overlay is not None:
            self.overlay.panel.enter_stopping_state(int(elapsed))
        if elapsed >= STOP_GRACE_SECONDS:
            self.stop_state = "unresponsive"
            self.watchdog.stop()
            if self.overlay is not None:
                self.overlay.panel.enter_unresponsive_state(int(elapsed))
                self.overlay.panel.set_force_enabled(True)

    def _teardown_overlay(self):
        if self.overlay is not None:
            self.overlay.close_overlay()
            self.overlay = None

    # ── 进度刷新 ────────────────────────────────────────────────────

    def _log_activity(self, message):
        if self.overlay is not None:
            self.overlay.panel.append_activity(self._format_elapsed() + "  " + message)

    def activity(self, state):
        panel = self.overlay.panel if self.overlay is not None else None
        if state.get("waiting"):
            # 页首 total 是页数，阶段 total 是块数，不能混进同一个字段。
            self.state = {"page": state["page"], "number": state["number"]}
            self.processed = max(self.processed, state["number"] - 1)
            if panel is not None:
                panel.reset_page()
            self._log_activity(self.tr("Started page: %1").replace("%1", state["page"]))
        else:
            if state.get("error") and not self.state.get("stage"):
                # 读图等阶段前错误不能沿用 runner 上一页的 current_stage。
                state = {key: value for key, value in state.items() if key != "stage"}
            if state.get("state") == "running":
                for key in ("total", "seconds", "error", "turn", "tools", "covered", "covered_total"):
                    self.state.pop(key, None)
            self.state.update(state)
            if state.get("error"):
                self.processed = max(self.processed, self.state.get("number", 0))
                self._log_activity(state.get("page", "") + ": " + state["error"])
            elif state.get("stage"):
                stage = state["stage"]
                message = (self._stage_done_text(stage, state) if state.get("state") == "done"
                           else self._stage_running_text(stage, state.get("total")))
                self._log_activity(message)
            elif state.get("turn"):
                message = self.tr("Response received: round %1").replace("%1", str(state["turn"]))
                if state.get("tools"):
                    message += " · " + self.tr("Requested tools: %1").replace("%1", self._tool_names(state["tools"]))
                self._log_activity(message)
        self.last_activity = time.monotonic()
        self.refresh()

    def refresh(self):
        if self.overlay is None or self.runner is None:
            return
        panel = self.overlay.panel
        panel.update_current_page(self.state.get("number", 0), len(self.runner.pages), self.state.get("page", ""))
        self._update_stages(panel, self.state)
        self._update_summary(panel)

    def _update_stages(self, panel, state):
        stage = state.get("stage")
        phase = state.get("state")
        card = panel.stage_cards.get(stage)
        if state.get("error"):
            panel.current_status.setText(state["error"])
            if card is not None:
                card.set_failed()
            return
        if card is not None:
            if phase == "running":
                card.set_running()
                panel.current_status.setText(self._stage_running_text(stage, state.get("total")))
            elif phase == "done":
                card.set_completed()
                panel.current_status.setText(self._stage_done_text(stage, state))
        if stage in {"ocr", "translate"} and state.get("total") is not None:
            panel.set_fact("target", self.tr("%1 blocks").replace("%1", str(state["total"])))
        if stage == "translate" and state.get("turn"):
            self._update_translate_card(panel, state, state.get("page", ""))
        if stage == "translate" and phase == "done":
            count = state.get("total", 0)
            panel.set_fact("accepted", self.tr("%1 / %2 blocks").replace("%1", str(count)).replace("%2", str(count)))

    def _stage_running_text(self, stage, total):
        return {
            "detect": self.tr("Detecting text regions..."),
            "ocr": self.tr("Recognizing text..."),
            "translate": self.tr("Processing translation..."),
            "inpaint": self.tr("Repairing image..."),
            "render": self.tr("Saving page result..."),
        }.get(stage, self.tr("Working..."))

    def _stage_done_text(self, stage, state):
        total = state.get("total")
        seconds = state.get("seconds")
        suffix = ""
        if seconds is not None:
            suffix = " · " + self.tr("%1 s").replace("%1", "%.1f" % seconds)
        if stage == "detect" and total is not None:
            return self.tr("%1 text boxes").replace("%1", str(total)) + suffix
        if stage == "ocr" and total is not None:
            return self.tr("%1 blocks processed").replace("%1", str(total)) + suffix
        if stage == "translate" and total is not None:
            return self.tr("%1 blocks translated").replace("%1", str(total)) + suffix
        if stage == "inpaint":
            return self.tr("Repair finished") + suffix
        if stage == "render":
            return self.tr("Page result saved")
        return self.tr("Done") + suffix

    def _update_translate_card(self, panel, state, page):
        # 回调发生在模型整轮返回、工具执行之前；不是流式输出或工具成功回执。
        panel.set_fact("round", self.tr("Round %1").replace("%1", str(state["turn"])))
        panel.set_fact("tools", self._tool_names(state.get("tools") or []) or self.tr("No tool requested"))
        covered, total = state.get("covered"), state.get("covered_total")
        if covered is not None and total:
            panel.set_fact("accepted", self.tr("%1 / %2 blocks").replace("%1", str(covered)).replace("%2", str(total)))
            if state.get("state") == "running":
                panel.stage_cards["translate"].set_running(progress=covered, total=total)

    def _tool_names(self, tools):
        names = {
            "list_pages": self.tr("List pages"),
            "read_pages": self.tr("Read page text"),
            "search_blocks": self.tr("Search text"),
            "get_page_info": self.tr("Read page information"),
            "search_glossary": self.tr("Search glossary"),
            "submit_translations": self.tr("Submit translations"),
        }
        return ", ".join(names.get(name, name) for name in tools)

    def _update_summary(self, panel):
        runner = self.runner
        panel.update_progress(self.processed, len(runner.pages), self._format_elapsed())
        parts = [self.tr("Pages saved: %1").replace("%1", str(self.applied))]
        if runner.mode == "revise":
            parts = [self.tr("Revision candidates: %1 / %2").replace("%1", str(self.blocks)).replace("%2", str(runner.total_blocks))]
        elif runner.mode == "translate":
            parts.append(self.tr("Blocks saved: %1 / %2").replace("%1", str(self.blocks)).replace("%2", str(runner.total_blocks)))
        parts.append(self.tr("Skipped: %1").replace("%1", str(len(runner.report["skipped"]))))
        errors = len(self.errors) + len(runner.report["failed"])
        parts.append(self.tr("Errors: %1 (including %2 conflicts)").replace("%1", str(errors)).replace("%2", str(self.conflicts)))
        panel.update_summary("    ·    ".join(parts))
        if runner.mode in {"translate", "revise"}:
            panel.usage.show()
            panel.usage.setText(self.tr("Task tokens reported: %1 · Responses without usage: %2").replace("%1", str(runner.known_tokens)).replace("%2", str(runner.unknown_usage)))
        idle = int(time.monotonic() - self.last_activity)
        panel.activity_age.setText(self.tr("Last status update %1 s ago").replace("%1", str(idle)))

    def _format_elapsed(self) -> str:
        seconds = int(time.monotonic() - self.started)
        return "%02d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)

    # ── 结果写回（主线程）──────────────────────────────────────────

    def receive(self, result, _runner=None):
        w, runner = self.window, self.runner
        if runner is None or (_runner is not None and runner is not _runner):
            return
        if not runner.cancel_event.is_set() and w.imsave_thread.isRunning():
            # File fingerprints must be read after queued image writes finish.
            QTimer.singleShot(50, lambda: self.receive(result, _runner=runner))
            return
        page = result["page"]
        accepted = None
        waiting_for_image = False
        try:
            if runner.cancel_event.is_set():
                return
            w._sync_block_data()
            current_image_dirty = (w.imgtrans_proj.current_img == page and
                                   w.canvas.num_imgstep != w.canvas.saved_imgstep)
            if (w.imgtrans_proj is not self.project
                    or current_image_dirty or page in self.image_edits
                    or page_signature(self.project, page) != runner.signatures[page]):
                self.conflicts += 1
                self.errors.append(self.tr("Edited during the task; rerun this page: ") + page)
                self.state["error"] = self.errors[-1]
                self._log_activity(self.errors[-1])
                return
            if runner.mode == "revise":
                for proposal in result["proposals"]:
                    proposal["project"] = self.project
                self.candidates.extend(result["proposals"])
                self.blocks += len(result["proposals"])
                self._log_activity(self.tr("Revision candidates ready: %1").replace("%1", str(len(result["proposals"]))))
                return
            if runner.mode == "translate":
                translated = [result["blocks"][i] for i in result["indexes"]]
                w.postprocess_translations(translated)
                if pcfg.auto_tate_chu_yoko.enabled:
                    from ui.text_engine.pipeline_formatting import apply_auto_tate_chu_yoko
                    apply_auto_tate_chu_yoko(translated, pcfg.auto_tate_chu_yoko)
            elif runner.mode == "recognize" and runner.config.enable_detect:
                for block in result["blocks"]:
                    if block.font_size <= 0:
                        block.font_size = pcfg.global_fontformat.font_size
                    if not block.font_family:
                        block.font_family = pcfg.global_fontformat.font_family
            render_card = self.overlay.panel.stage_cards.get("render") if self.overlay is not None else None
            self.state.update(stage="render", state="running")
            if render_card is not None and render_card.isVisible():
                render_card.set_running()
                self.overlay.panel.current_status.setText(self.tr("Saving page result..."))
            if result["mask"] is not None:
                self.project.save_mask(page, result["mask"])
            if result["inpainted"] is not None:
                self.project.save_inpainted(page, result["inpainted"])
            image_changed = result["mask"] is not None or result["inpainted"] is not None
            w.canvas.invalidate_text_history_for_page(page)
            current_page = self.project.current_img == page
            if image_changed and current_page:
                self.project.set_current_img(page)
            report = BatchWriteback(self.project, w.st_manager if current_page else None).apply(
                {page: result["blocks"]}, image_changed=image_changed)
            if not report["verified"]:
                raise RuntimeError(self.tr("Data and canvas are out of sync."))
            if runner.mode == "recognize" and runner.config.enable_detect:
                preserved = self.project._image_info[page].get("finish_code", 0) & RunStatus.FIN_INPAINT
                self.project.set_page_progress(page, result["bits"] | preserved)
            else:
                self.project.update_page_progress(page, result["bits"])
            for key in ("width", "height"):
                if key in result.get("image_info", {}):
                    self.project._image_info[page][key] = result["image_info"][key]
            self.project.save()
            if current_page:
                # 逐页渲染：当前页结果一到就重建画布，不留"标脏等下次访问"。
                # 旧版那一下补渲染正好落在用户切页的瞬间，就是跳转卡顿的来源。
                w.canvas.updateCanvas()
                w.canvas.setProjSaveState(True)
            accepted = self.project.pages[page]
            self.project.mark_page_needs_rerender(page, include_current=True)
            if runner.config.render_after_pipeline:
                image = w.canvas.render_result_img() if current_page else render_page_image(self.project, page)
                failures = w.imsave_thread.failure_count
                w.imsave_thread.saveImg(
                    self.project.get_result_path(page), image, page,
                    save_params={"ext": self.project.get_result_ext(page),
                                 "quality": pcfg.imgsave_quality},
                    keep_alpha=image.hasAlphaChannel(),
                )
                waiting_for_image = True
                self._image_settlement = runner
                self._finish_page_image(runner, page, accepted, result, failures)
                return
            self.applied += 1
            self.blocks += result.get("count", len(result["blocks"]))
            w.glossary_workbench.mark_content_changed()
            self.state.update(stage="render", state="done")
            self._log_activity(self.tr("Saved page result: %1").replace("%1", page))
            if render_card is not None and render_card.isVisible():
                render_card.set_completed()
        except Exception as error:
            self.errors.append(page + ": " + str(error))
            self.state["error"] = self.errors[-1]
            self._log_activity(self.errors[-1])
        finally:
            if not runner.cancel_event.is_set():
                self.processed = max(self.processed, self.state.get("number", 0))
            self.last_activity = time.monotonic()
            if not waiting_for_image:
                runner.acknowledge(accepted)
                self.refresh()

    def _finish_page_image(self, runner, page, accepted, result, failures):
        """Keep the worker waiting until the result-image save has a receipt."""
        if runner is not self.runner:
            return
        if self.window.imsave_thread.isRunning():
            QTimer.singleShot(50, lambda: self._finish_page_image(runner, page, accepted, result, failures))
            return
        try:
            if self.window.imsave_thread.failure_count != failures:
                raise RuntimeError(self.tr("Images could not be saved. Fix the save error before starting."))
            self.project.clear_page_needs_rerender(page)
            if page == self.project.current_img:
                self.window.canvas.setProjSaveState(False)
                self.window.canvas.update_saved_undostep()
            self.applied += 1
            self.blocks += result.get("count", len(result["blocks"]))
            self.window.glossary_workbench.mark_content_changed()
            self.state.update(stage="render", state="done")
            self._log_activity(self.tr("Saved page result: %1").replace("%1", page))
        except Exception as error:
            self.errors.append(page + ": " + str(error))
            self.state["error"] = self.errors[-1]
            self._log_activity(self.errors[-1])
        finally:
            self._image_settlement = None
            self.last_activity = time.monotonic()
            runner.acknowledge(accepted)
            self.refresh()

    # ── 收尾 ────────────────────────────────────────────────────────

    def finished(self):
        if self.window.imsave_thread.isRunning() or getattr(self, "_image_settlement", None) is not None:
            QTimer.singleShot(50, self.finished)
            return
        runner = self.runner
        self.timer.stop()
        self.watchdog.stop()
        self._controls(True)
        self.stop_state = "none"
        self.stop_requested_at = 0.0
        report = runner.report
        errors = self.errors + [page + ": " + error for page, error in report["failed"].items()]
        # 线程清理前冻结最终进度与用量；completed 只表示结算，不能当作保存数。
        processed_pages = set(report["completed"]) | set(report["skipped"]) | (set(report["failed"]) & set(runner.pages))
        self.processed = max(self.processed, len(processed_pages))
        if self.overlay is not None:
            self._update_summary(self.overlay.panel)
        self.runner = None
        runner.deleteLater()
        if pcfg.module.empty_runcache:
            self.window.module_manager.unload_all_models()
        self.window.updatePageList()
        self._show_finished_report(report, errors)
        if self.close_when_done:
            self.window.close()

    def _show_finished_report(self, report, errors):
        """任务结束：面板改成结果摘要，给个关闭钮；重译候选另开入口。"""
        if self.overlay is None:
            return
        errors_text = "\n".join(errors)
        self._panel_as_notice(
            self.tr("Cancelled") if report["cancelled"] else (self.tr("Task ended with errors") if errors else self.tr("Task finished")),
            errors_text,
        )
        panel = self.overlay.panel
        panel.append_activity(panel.title.text())
        if self.task_mode == "revise":
            panel.footer_hint.setText(self.tr("Candidates require review before applying"))
        if self.candidates:
            panel.stop_graceful.setText(self.tr("Review revisions"))
            panel.stop_graceful.setEnabled(True)
            panel.stop_graceful.show()
            panel.stop_graceful.clicked.disconnect()
            panel.stop_graceful.clicked.connect(self._review_from_panel)
        panel.relayout()

    def _review_from_panel(self):
        self._dismiss_overlay()
        self.review_next()

    def _dismiss_overlay(self):
        self._teardown_overlay()

    # ── 重译候选审阅（沿用原有单条确认流程）────────────────────────

    def review_next(self):
        if self.busy() or not self.candidates:
            return
        if self.dialog is not None:
            self.dialog.close()
        candidate = self.candidates[0]
        dialog = self.dialog = QDialog(self.window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        def closed(_result):
            if self.dialog is dialog:
                self.dialog = None
        dialog.finished.connect(closed)
        dialog.setWindowTitle(self.tr("Review revision"))
        dialog.resize(640, 460)
        layout = QVBoxLayout(dialog)
        source = QLabel(candidate["page"] + "\n" + candidate["source"])
        source.setTextFormat(Qt.TextFormat.PlainText)
        source.setWordWrap(True)
        layout.addWidget(source)
        layout.addWidget(QLabel(self.tr("Current translation")))
        before = ConfigTextEdit()
        before.setReadOnly(True)
        before.setPlainText(candidate["before"])
        layout.addWidget(before)
        layout.addWidget(QLabel(self.tr("Proposed translation (editable)")))
        after = ConfigTextEdit()
        after.setPlainText(candidate["after"])
        layout.addWidget(after)
        message = QLabel()
        message.setWordWrap(True)
        layout.addWidget(message)
        row = QHBoxLayout()
        apply = QPushButton(self.tr("Apply revision"))
        keep = QPushButton(self.tr("Keep original"))
        row.addWidget(apply)
        row.addWidget(keep)
        layout.addLayout(row)
        def advance():
            self.candidates.pop(0)
            dialog.close()
            self.dialog = None
            self.review_next()
        def accept():
            w = self.window
            w._sync_block_data()
            blocks = w.imgtrans_proj.pages.get(candidate["page"], [])
            index = candidate["index"]
            if (w.imgtrans_proj is not candidate["project"] or not 0 <= index < len(blocks)
                    or block_signature(blocks[index]) != candidate["signature"]):
                message.setText(self.tr("This block changed. Keep the current text and generate a new revision."))
                apply.setEnabled(False)
                return
            if not after.toPlainText().strip():
                return
            w.on_workbench_jump(candidate["page"], index)
            items = w.st_manager.textblk_item_list
            pairs = w.st_manager.pairwidget_list
            if (w.imgtrans_proj.current_img != candidate["page"]
                    or not 0 <= index < len(items) or index >= len(pairs)
                    or items[index].blk is not blocks[index]):
                message.setText(self.tr("This block changed. Keep the current text and generate a new revision."))
                apply.setEnabled(False)
                return
            from ui.textedit_commands import ApplyBlockTextCommand
            from utils.block_actions import ACTION_REGISTRY
            item = items[index]
            command = ApplyBlockTextCommand(item, w.st_manager.pairwidget_list[index],
                                            "translation", after.toPlainText(),
                                            consumed_tags=ACTION_REGISTRY["act_retranslate"].consumes)
            w.canvas.push_undo_command(command)
            w.canvas.setProjSaveState(True)
            w.glossary_workbench.mark_content_changed()
            advance()
        apply.clicked.connect(accept)
        keep.clicked.connect(advance)
        dialog.show()
