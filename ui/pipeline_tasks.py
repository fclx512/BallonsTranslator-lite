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

**逐页渲染**：每页写回后立刻重建当前页画布，不留"标脏等下次访问再补渲染"
（旧版那一下同步渲染正好砸在用户切页的瞬间，就是跳转卡顿的来源）。非当前页
只落盘，用户切过去时按正常切页路径加载——不需要管线替它渲染，那条路本来
就会读盘。
"""
import os
import time

from qtpy.QtCore import QObject, QTimer, Qt
from qtpy.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

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
        stages = {"render"}
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
                self.runner = PipelineRunner(project, pages if pages is not None else list(project.pages),
                                             mode, pcfg.module, modules, overwrite=overwrite,
                                             targets=targets, hint=hint, await_settlement=True, parent=self)
                self.project = project
                self.candidates = [c for c in self.candidates if c.get("project") is project]
                self.image_edits.clear()
                self.applied = self.conflicts = self.blocks = 0
                self.errors = []
                self.state = {}
                self.started = self.last_activity = time.monotonic()
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

    def _panel_as_notice(self, title: str, message: str):
        """把进度面板改造成"只有一句话 + 一个关闭钮"的告知卡。

        阶段卡片与停止钮都要收掉，而且**必须断开强停信号**：那个按钮原本连的是
        ``request_force_stop``，改个文字就复用会让"关闭"弹出强制退出确认框
        ——在启动失败这种无害路径上摆一个能结束进程的按钮是危险的。
        """
        if self.overlay is None:
            return
        panel = self.overlay.panel
        for card in panel.stage_cards.values():
            card.hide()
        panel.title.setText(title)
        panel.page_label.setText("")
        panel.stop_graceful.hide()
        panel.set_notice(message)
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
        """优雅停止：跑完当前页就收。已保存的页保留，在途结果丢弃。"""
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
        box.setWindowTitle(self.tr("Force stop"))
        waited = int(time.monotonic() - self.stop_requested_at) if self.stop_requested_at else 0
        box.setText(
            self.tr(
                "The background call has not returned after %1 s. Forcing a stop "
                "ends the program immediately.\n\n"
                "Pages already saved stay in the project. The page in flight is "
                "discarded. A model or network call that is still running is cut "
                "off, so restart the app afterwards."
            ).replace("%1", str(waited))
        )
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

    def activity(self, state):
        if state.get("waiting"):
            for key in ("error", "tools", "turn", "covered", "covered_total"):
                self.state.pop(key, None)
        self.state.update(state)
        self.last_activity = time.monotonic()
        self.refresh()

    def refresh(self):
        if self.overlay is None:
            return
        panel = self.overlay.panel
        state = self.state

        number = state.get("number", 0)
        total_pages = len(self.runner.pages) if self.runner is not None else state.get("total", 0)
        panel.update_current_page(number, total_pages, state.get("page", ""))

        self._update_stages(panel, state)
        self._update_summary(panel)

    def _update_stages(self, panel, state: dict):
        """把 runner 上报的单条 activity 落成各卡片状态。

        只认识自己关心的那几种 payload（阶段开始/结束、翻译轮次、错误），
        其余一律当噪音忽略。
        """
        stage = state.get("stage")
        phase = state.get("state")
        error = state.get("error")
        page = state.get("page", "")

        if error and stage:
            card = panel.stage_cards.get(stage)
            if card is not None:
                card.set_failed(error)
        elif stage and phase == "running":
            card = panel.stage_cards.get(stage)
            if card is not None:
                total = state.get("total")
                card.set_running(self._stage_running_text(stage, total), 0, total or None)
        elif stage and phase == "done":
            card = panel.stage_cards.get(stage)
            if card is not None:
                card.set_completed(self._stage_done_text(stage, state))

        # 翻译是唯一有轮次细节的阶段：摘要行 + 可展开详情
        if state.get("turn"):
            self._update_translate_card(panel, state, page)

    def _stage_running_text(self, stage: str, total) -> str:
        if stage == "ocr" and total:
            return self.tr("Recognizing text (0/%1)").replace("%1", str(total))
        return {
            "detect": self.tr("Detecting text regions..."),
            "ocr": self.tr("Recognizing text..."),
            "translate": self.tr("Translating..."),
            "inpaint": self.tr("Repairing image..."),
            "render": self.tr("Rendering..."),
        }.get(stage, self.tr("Working..."))

    def _stage_done_text(self, stage: str, state: dict) -> str:
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
        return self.tr("Done") + suffix

    def _update_translate_card(self, panel, state: dict, page: str):
        """翻译卡片：折叠给摘要行，展开给轮次/工具/用量——**不给思维链与译文**。

        思维链和完整译文会把面板撑爆，而且它们跑完就能在文本框里看到，
        实时滚一遍没有信息量。省略了什么要写在界面上，否则用户以为卡了。
        """
        card = panel.stage_cards.get("translate")
        if card is None:
            return
        turn = state.get("turn", 0)
        tools = list(state.get("tools") or [])
        covered = state.get("covered")
        covered_total = state.get("covered_total")

        if covered is not None and covered_total:
            card.set_running(
                self.tr("Translating (%1/%2 blocks)")
                .replace("%1", str(covered)).replace("%2", str(covered_total)),
                covered, covered_total,
            )

        shown = ", ".join(tools[:2])
        if len(tools) > 2:
            shown += ", ..."
        if not shown:
            shown = self.tr("waiting for the model")
        card.set_summary(
            self.tr("Round %1 · %2").replace("%1", str(turn)).replace("%2", shown)
        )

        lines = [self.tr("<b>Round %1</b>").replace("%1", str(turn))]
        if tools:
            lines.append("└─ " + self.tr("Tools: %1").replace("%1", ", ".join(tools)))
        if covered is not None and covered_total:
            lines.append("└─ " + self.tr("Blocks accepted: %1/%2")
                         .replace("%1", str(covered)).replace("%2", str(covered_total)))
        tokens = state.get("tokens")
        if tokens is not None:
            unknown = state.get("unknown", 0)
            usage = str(tokens)
            if unknown:
                usage += " + ?" + ("  ×" + str(unknown) if unknown > 1 else "")
            lines.append("└─ " + self.tr("Tokens used: %1").replace("%1", usage))
        if state.get("request_mode") == "deepseek_non_thinking":
            lines.append("└─ " + self.tr("Mode: non-thinking request (DeepSeek)"))
        lines.append("")
        lines.append("<i>" + self.tr(
            "Reasoning and full translations are hidden here. "
            "Read them in the text panel once the page is done."
        ) + "</i>")
        card.set_details("<br>".join(lines))

    def _update_summary(self, panel):
        parts = [self.tr("Pages saved: %1").replace("%1", str(self.applied))]
        if self.started:
            parts.append(self.tr("Elapsed: %1").replace("%1", self._format_elapsed()))
        if self.errors:
            parts.append(self.tr("Errors: %1").replace("%1", str(len(self.errors))))
        if self.conflicts:
            parts.append(self.tr("Rejected pages: %1").replace("%1", str(self.conflicts)))
        if self.runner is not None and self.runner.mode in {"translate", "revise"}:
            parts.append(self.tr("Blocks done: %1/%2")
                         .replace("%1", str(self.blocks)).replace("%2", str(self.runner.total_blocks)))
        idle = int(time.monotonic() - self.last_activity)
        if idle >= 5:
            parts.append(self.tr("Waiting on the model: %1 s").replace("%1", str(idle)))
        panel.update_summary("  |  ".join(parts))

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
                if self.overlay is not None:
                    message = self.tr("Edited during the task; rerun this page: ") + page
                    card = self.overlay.panel.stage_cards.get(
                        "translate" if runner.mode in {"translate", "revise"} else runner.mode
                    )
                    if card is not None and card.isVisible():
                        card.set_failed(message)
                return
            if runner.mode == "revise":
                for proposal in result["proposals"]:
                    proposal["project"] = self.project
                self.candidates.extend(result["proposals"])
                self.blocks += len(result["proposals"])
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
            if render_card is not None and render_card.isVisible():
                render_card.set_running(self.tr("Saving page result..."))
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
            w.canvas.setProjSaveState(True)
            self.project.save()
            if current_page:
                # 逐页渲染：当前页结果一到就重建画布，不留"标脏等下次访问"。
                # 旧版那一下补渲染正好落在用户切页的瞬间，就是跳转卡顿的来源。
                w.canvas.updateCanvas()
            accepted = self.project.pages[page]
            self.applied += 1
            self.blocks += result.get("count", len(result["blocks"]))
            w.glossary_workbench.mark_content_changed()
            if render_card is not None and render_card.isVisible():
                render_card.set_completed(
                    self.tr("Saved page result: %1").replace("%1", page))
        except Exception as error:
            self.errors.append(page + ": " + str(error))
        finally:
            runner.acknowledge(accepted)
        self.refresh()

    # ── 收尾 ────────────────────────────────────────────────────────

    def finished(self):
        runner = self.runner
        self.timer.stop()
        self.watchdog.stop()
        self._controls(True)
        self.stop_state = "none"
        self.stop_requested_at = 0.0
        report = runner.report
        errors = self.errors + [page + ": " + error for page, error in report["failed"].items()]
        # runner 马上要被清掉，用量这两个数先落下来（finish 报告还要用）
        known_tokens, unknown_usage = runner.known_tokens, runner.unknown_usage
        self.runner = None
        runner.deleteLater()
        if pcfg.module.empty_runcache:
            self.window.module_manager.unload_all_models()
        self.window.updatePageList()
        self._show_finished_report(report, errors, known_tokens, unknown_usage)
        if self.close_when_done:
            self.window.close()

    def _show_finished_report(self, report, errors, known_tokens, unknown_usage):
        """任务结束：面板改成结果摘要，给个关闭钮；重译候选另开入口。"""
        if self.overlay is None:
            return
        errors_text = "\n".join(errors[:3]) if errors else ""
        self._panel_as_notice(
            self.tr("Cancelled") if report["cancelled"] else self.tr("Task finished"),
            errors_text,
        )
        panel = self.overlay.panel
        parts = [
            self.tr("Pages saved: %1").replace("%1", str(self.applied)),
            self.tr("Skipped: %1").replace("%1", str(len(report["skipped"]))),
            self.tr("Rejected pages: %1").replace("%1", str(self.conflicts)),
            self.tr("Errors: %1").replace("%1", str(len(errors))),
            self.tr("Tokens reported: %1; requests without usage: %2")
            .replace("%1", str(known_tokens)).replace("%2", str(unknown_usage)),
        ]
        panel.update_summary("  |  ".join(parts))
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
