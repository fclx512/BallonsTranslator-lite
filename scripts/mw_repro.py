"""MainWindow 在线演练台：拉起真实主窗口做模拟复现与交互驱动。

静态审查与离屏单测覆盖不到的问题类别——真实绘制路径、原生模态对话
框、GC 时机、模态嵌套、FramelessWindow win32 交互——用本工具在真机
上演练。起源：撤销阶段4第二批确认弹窗的 GC 悬空 AV 闪退（见
docs/基础速查/经验教训.md §3.3），当时以临时脚本定位，本工具为其
常驻化。

要点（来自那次排查的直接教训）：
- 必须**窗口模式**（offscreen 拉不起 FramelessWindow，win32 句柄无效）；
- 自动点击确认弹窗须延迟 ≥200ms（弹窗绘制完成前点击是另一类崩溃源）；
- faulthandler 常开；无声 AV 的 Python 栈行号是下游受害者，仅作起点。

Run from the repo root:
    ./ballontrans_pylibs_win/python.exe scripts/mw_repro.py [options]
"""

import argparse
import faulthandler
import os
import sys
import tempfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)
# 注意：不可在此吞掉 sys.argv——argparse 的场景参数靠它传递
# （QApplication 以空参数构造，CLI 参数不会泄入 Qt）。

faulthandler.enable()


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project", metavar="DIR",
        help="打开真实工程（只读演练：强制 scenario=none，不跑改写场景）",
    )
    parser.add_argument("--pages", type=int, default=2, help="合成工程页数")
    parser.add_argument("--blocks", type=int, default=8, help="每页块数")
    parser.add_argument(
        "--scenario",
        choices=["group-undo", "stroke-switch", "fmt-sync", "align-dialog", "none"],
        default="group-undo",
        help="group-undo=整本对齐→组化确认弹窗→撤销→重渲 全链路；"
        "stroke-switch=竖排描边块快速切页（闪退回归演练）；"
        "fmt-sync=数据层与渲染态对齐分叉探针（样式同步排查）；"
        "align-dialog=整本对齐对话框交互链路（智能默认/点块取边/拖线/确定执行）",
    )
    parser.add_argument(
        "--switch-rounds", type=int, default=12,
        help="stroke-switch 场景的快速切页轮数",
    )
    parser.add_argument(
        "--confirm-delay", type=int, default=200,
        help="自动点击确认弹窗 AcceptRole 按钮的延迟 ms（勿低于 200）",
    )
    parser.add_argument(
        "--no-panel", action="store_true", help="不开历史面板（默认开）"
    )
    parser.add_argument(
        "--no-show", action="store_true",
        help="不显示主窗口（默认显示，走真实绘制路径）",
    )
    parser.add_argument(
        "--watchdog", type=int, default=60,
        help="卡死看门狗秒数：到点打印全线程 Python 栈并退出",
    )
    return parser.parse_args()


def _setup_qt(args):
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication, QMessageBox

    app = QApplication([])
    if args.scenario == "group-undo" or args.confirm_delay:
        # 自动点击真实确认弹窗的 AcceptRole 按钮（模拟真人手点节奏）
        original_exec = QMessageBox.exec

        def _patched_exec(self):
            for button in self.buttons():
                if self.buttonRole(button) == self.ButtonRole.AcceptRole:
                    QTimer.singleShot(args.confirm_delay, button.click)
                    break
            return original_exec(self)

        QMessageBox.exec = _patched_exec
    return app, QTimer


def _open_mainwindow(app, args):
    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    # 演练台禁用两类启动副作用
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    from ui.mainwindow import MainWindow

    window = MainWindow(app, config, open_dir=args.project or "")
    if not args.no_show:
        window.show()
    app.processEvents()

    if args.project:
        window.openDir(args.project)
        app.processEvents()
    return window


def _make_synthetic(window, app, args, style="plain"):
    """合成临时工程：N 页 × M 块（富文本 + 描边），保证渲染路径真实。"""
    import numpy as np

    from utils.io_utils import imwrite
    from utils.textblock import TextBlock

    tmp = tempfile.mkdtemp(prefix="mw_repro_")
    window._temp_project_dirs.add(tmp)
    for i in range(args.pages):
        img = np.full((1200, 900, 3), 200, dtype=np.uint8)
        imwrite(os.path.join(tmp, f"p{i:02d}.jpeg"), img, ext=".jpeg")

    window.openDir(tmp)
    app.processEvents()
    proj = window.imgtrans_proj

    rich = "<p style=\"color:#222\">演练文本 <b>{}</b> 行一<br>行二内容</p>"
    # 竖排 + 收紧字距：真实字体下原布局按 85% 字距折列，描边克隆经
    # toHtml 往返丢字距后按 100% 折列——两文档行结构漂移（闪退回归场景）
    vertical_rich = (
        "<p style=\"color:#222\">"
        "<span style=\"letter-spacing:-0.15em\">"
        "竖排描边演练：这一段文本足够长，会让竖排布局在真实字体下"
        "折出多列，从而触发描边克隆文档与原布局之间的行结构漂移，"
        "用于复现快速切图时的 IndexError 闪退。继续补充更多文字，"
        "确保即使单列容量较大也能折出至少三列。"
        "</span></p>"
    )
    for pname in list(proj.pages.keys()):
        for i in range(args.blocks):
            if style == "vertical-stroke":
                if i >= 2:  # 竖排块占满整页，两块即可
                    break
                blk = TextBlock(
                    xyxy=[80, 80, 480, 820],
                    translation=f"竖排演练 {pname} {i}",
                )
                blk._bounding_rect = [80, 80, 400, 740]
            else:
                blk = TextBlock(
                    xyxy=[80, 80 + i * 120, 480, 200 + i * 120],
                    translation=f"演练 {pname} {i}",
                )
            if style == "vertical-stroke":
                blk.rich_text = vertical_rich
                blk.vertical = True
            else:
                blk.rich_text = rich.format(f"{pname}-{i}")
                blk._bounding_rect = [80, 80 + i * 120, 400, 120]
            blk.font_family = "SimHei"
            blk.fontformat.stroke_width = 2.0
            proj.pages[pname].append(blk)
    proj.current_img = next(iter(proj.pages))
    window.st_manager.updateSceneTextitems()
    app.processEvents()


def _open_history_panel(app):
    from ui.history_panel import HistoryPanel

    panel = HistoryPanel()
    panel.show()
    app.processEvents()
    print(f"[mw_repro] history panel bound: {panel.stack is not None}", flush=True)


def _scenario_group_undo(window, app, args):
    """高级对齐 → 组化确认弹窗（自动点击）→ 撤销 → 重渲 全链路。"""
    import faulthandler

    proj = window.imgtrans_proj
    window.execute_advanced_align(None, 100.0, "top", "y")
    app.processEvents()
    stack = window.canvas.text_undo_stack
    print(
        f"[mw_repro] stack count={stack.count()} index={stack.index()} "
        f"dirty={[p for p in proj.pages if proj.page_needs_rerender(p)]}",
        flush=True,
    )

    if not args.no_panel:
        _open_history_panel(app)

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    window.canvas.undo()
    for _ in range(20):
        app.processEvents()
    faulthandler.cancel_dump_traceback_later()

    print(
        f"[mw_repro] after undo: index={stack.index()} count={stack.count()} "
        f"dirty={[p for p in proj.pages if proj.page_needs_rerender(p)]}",
        flush=True,
    )
    print("[mw_repro] SCENARIO OK: group-undo", flush=True)


def _scenario_stroke_switch(window, app, args):
    """竖排描边块快速切页：克隆描边与原布局行结构漂移的闪退回归演练。

    用户反馈的复现路径：竖排文本 + 文字描边，快速切换图片时描边渲染
    走克隆文档路径，克隆经 toHtml 往返丢 letter-spacing 后行结构与原
    布局漂移，曾以旧偏移表索引新结构 IndexError 闪退（修复见
    vertical_layout.py::updateDrawOffsets 的形状校验守卫）。
    """
    import faulthandler

    from qtpy.QtCore import Qt

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    if len(pages) < 2:
        raise SystemExit("[mw_repro] stroke-switch 需要至少 2 页")

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    for r in range(args.switch_rounds):
        nxt = pages[(r + 1) % len(pages)]
        rows = window.pageList.findItems(nxt, Qt.MatchFlag.MatchExactly)
        # 走完整 UI 切页链路（会话落账、条件保存、场景重建、重绘）
        window.pageList.setCurrentItem(rows[0])
        for _ in range(8):
            app.processEvents()
        # 模拟切换 churn 中的字号自适应（应用自身的 setRelFontSize）：
        # relayout 抑制窗口内每次 mergeCharFormat 同步触发 contentsChanged
        # → 描边光栅重生成 → 克隆描边路径，曾以旧偏移表撞新文档结构闪退
        items = window.st_manager.textblk_item_list
        if items:
            items[0].setRelFontSize(1.1 if r % 2 == 0 else 0.9)
            for _ in range(4):
                app.processEvents()

    # 竞态演练段：复现引擎事务的抑制窗口——字号/字体/文本应用等事务
    # 在 relayout_on_changed=False 下改文档（行结构随之变化），窗口期内
    # 的同步 contentsChanged → 描边光栅重生成会以旧偏移表克隆描边。
    # 修复前此处以用户同款调用栈 IndexError 闪退。
    from qtpy.QtGui import QTextCursor

    items = window.st_manager.textblk_item_list
    if items:
        item = items[0]
        layout = item.layout
        layout.relayout_on_changed = False
        cursor = QTextCursor(item.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(
            "竞态演练：这段明显更长的文本会在 relayout 抑制窗口内改变行结构，"
            "让克隆文档的列数超过原布局的旧偏移表，从而复现 IndexError 闪退。"
            "继续补足长度：竖排布局每字符占一行，行数随字符数线性增长，"
            "所以新文本必须比原文更长才能让形状失配朝越界方向发生，"
            "这与用户切换图片时新页面文本更长的情形一致。"
            "竞态演练：这段明显更长的文本会在 relayout 抑制窗口内改变行结构，"
            "让克隆文档的列数超过原布局的旧偏移表，从而复现 IndexError 闪退。"
            "继续补足长度：竖排布局每字符占一行，行数随字符数线性增长，"
            "所以新文本必须比原文更长才能让形状失配朝越界方向发生。"
        )
        layout.relayout_on_changed = True
        layout.reLayoutEverything()
        for _ in range(8):
            app.processEvents()
    faulthandler.cancel_dump_traceback_later()

    print(
        f"[mw_repro] stroke-switch: {args.switch_rounds} 轮切页完成，"
        f"current={proj.current_img}",
        flush=True,
    )
    print("[mw_repro] SCENARIO OK: stroke-switch", flush=True)


def _scenario_align_dialog(window, app, args):
    """整本对齐对话框交互链路演练（合成工程）。

    覆盖：智能默认（当前页对齐边众数）→ 画布对齐模式 → 拖线回推 →
    点块取边 → 换轴（对齐边标签/目标重取）→ 幽灵预览在场 →
    确定执行（全页数据层落位）→ 画布清理。撤销路径由 group-undo 场景
    覆盖，此处不重复。
    """
    import faulthandler

    from ui.point_align_dialog import block_edge_value, smart_default_target
    from utils.config import pcfg

    proj = window.imgtrans_proj
    canvas = window.canvas

    faulthandler.dump_traceback_later(args.watchdog, exit=True)

    # 记忆项强制为已知默认（用户真机用过后 config.json 里可能是任意值，
    # 换轴恢复记忆是设计行为，不隔离会让精确值断言随用户配置漂移）
    _saved_align_cfg = (
        pcfg.point_align_axis,
        pcfg.point_align_edge_y,
        pcfg.point_align_edge_x,
        pcfg.point_align_all_pages,
    )
    pcfg.point_align_axis = "y"
    pcfg.point_align_edge_y = "top"
    pcfg.point_align_edge_x = "left"
    pcfg.point_align_all_pages = True

    # 等合成布局事件全部落定（几何连续两次 pump 无变化），否则对话框的
    # 智能默认/点块取边会在落定窗口内被后台 relayout 改写，精确值断言抖动
    def _geo_snapshot():
        return [
            tuple(it.blk._bounding_rect or tuple(it.blk.xyxy))
            for it in window.st_manager.textblk_item_list
        ]

    last = None
    for _ in range(100):
        app.processEvents()
        cur = _geo_snapshot()
        if cur and cur == last:
            break
        last = cur

    # 期望值必须在 on_open 之前取快照：对话框在打开瞬间同步算智能默认，
    # 若在其后取数，期间跑掉的布局事件可能已改写块几何，比较就不成立
    page = proj.pages[proj.current_img]
    expect = smart_default_target(page, "y", "top")

    window.on_open_whole_book_align()
    for _ in range(5):
        app.processEvents()
    dlg = window._align_dialog
    assert dlg is not None and dlg.isVisible(), "对话框未打开"
    assert canvas.is_align_mode(), "画布未进入对齐模式"

    assert dlg.target_value() == expect, (
        f"智能默认 {dlg.target_value()} != 期望 {expect}"
    )
    print(f"[mw_repro] align-dialog: 智能默认 target={expect}", flush=True)

    # 拖线：画布回推目标值
    canvas.align_target_changed.emit(222)
    for _ in range(3):
        app.processEvents()
    assert dlg.target_value() == 222, "拖线未联动数值框"

    # 点块：取该块当前轴（y）对齐边（top）
    items = window.st_manager.textblk_item_list
    canvas.align_block_picked.emit(items[0])
    for _ in range(3):
        app.processEvents()
    expect = int(round(block_edge_value(items[0].blk._bounding_rect, "y", "top")))
    assert dlg.target_value() == expect, "点块未取对齐边"
    print(f"[mw_repro] align-dialog: 点块取边 target={expect}", flush=True)

    # 换轴 X：对齐边换标签、目标从点选块重取（left）
    dlg._axis_bar.set_current(1, emit=True)
    for _ in range(3):
        app.processEvents()
    assert dlg.axis() == "x"
    expect = int(round(block_edge_value(items[0].blk._bounding_rect, "x", "left")))
    assert dlg.target_value() == expect, (
        f"换轴后未从点选块重取: dlg={dlg.target_value()} expect={expect} "
        f"edge_idx={dlg._edge_bar.current()}"
    )

    # 拖到新位置再确定 → 幽灵预览在场（目标偏离众数 80，应有落点矩形）
    canvas.align_target_changed.emit(300)
    for _ in range(3):
        app.processEvents()
    assert dlg.target_value() == 300
    assert len(canvas._align_ghosts) > 0, "幽灵预览为空"
    dlg.accept()
    for _ in range(10):
        app.processEvents()

    assert window._align_dialog is None, "对话框引用未清"
    assert not canvas.is_align_mode(), "画布对齐模式未退出"
    assert canvas._align_ghosts == [] and canvas._align_line is None, "画布未清理"
    # 退出后光标必须两处都清（视图 + baseLayer 场景层），
    # 否则 baseLayer 十字丝残留成「精确选择」粘在画布上
    assert not canvas.baseLayer.hasCursor(), "baseLayer 十字丝残留"
    for pname, blks in proj.pages.items():
        for blk in blks:
            if blk.angle != 0:
                continue
            got = blk._bounding_rect[0] if blk._bounding_rect is not None else blk.xyxy[0]
            assert abs(got - 300) < 0.5, f"{pname}: 块 left={got} 未对齐到 300"
    print(
        f"[mw_repro] align-dialog: {proj.num_pages} 页全部块 left→300，撤销栈 count="
        f"{canvas.text_undo_stack.count()}",
        flush=True,
    )

    faulthandler.cancel_dump_traceback_later()
    # 还原用户原记忆项并落盘（对话框 done() 已把演练值写进 config.json）
    (
        pcfg.point_align_axis,
        pcfg.point_align_edge_y,
        pcfg.point_align_edge_x,
        pcfg.point_align_all_pages,
    ) = _saved_align_cfg
    from utils.config import save_config

    save_config()
    print("[mw_repro] SCENARIO OK: align-dialog", flush=True)


def _align_int(flag) -> int:
    """Qt.AlignmentFlag → TextAlignment 值（0 左 / 1 中 / 2 右）。"""
    from qtpy.QtCore import Qt

    f = int(flag)
    if f & int(Qt.AlignmentFlag.AlignHCenter):
        return 1
    if f & int(Qt.AlignmentFlag.AlignRight):
        return 2
    return 0


def _scenario_fmt_sync(window, app, args):
    """数据层 blk.fontformat 与 item 渲染态（QTextDocument）的对齐分叉探针。

    背景：两份状态只在 TextBlkItem.set_fontformat 被调用时同步，任何
    单侧写入都会分叉（面板显示居中、画布渲染靠右一类）。本场景用合成
    工程逐一验证候选根源与治愈手段，只读用户数据（工程在临时目录）：

    A0 基线：数据层居中 + 无 align 属性的 HTML → 三处应一致；
    A1a/A1b 旧项目方向：HTML 段落自带对齐（上游/旧版可能写出），数据层
       居中 → 修复前在此分叉（参数居中、渲染靠右）；修复后
       load_rich_text_html 的 _strip_paragraph_alignment 归一，应一致；
    A2 对 A1 的活 item 整包 set_fontformat 重应用 → 修复前治不了
       （alignment 只写 doc 默认 option，块级 blockFormat 不受影响）；
    B0/B1 数据侧直写（管线 postprocess 同款 blk.alignment=...，item 在场）
       → 渲染陈旧分叉（机制演示；管线收尾实际有整页重建兜底）；
    B2 set_fontformat 重应用 → 验证 B 方向可以治愈；
    C  样式管理器空 diff 门：changed_values 对「已是目标值」的编辑返回
       空 → _apply_* 直接 return，解释「先改成别的再改回来」现象。
    """
    from qtpy.QtCore import Qt

    from utils.textblock import TextAlignment

    proj = window.imgtrans_proj
    pages = list(proj.pages.keys())
    if len(pages) < 1:
        raise SystemExit("[mw_repro] fmt-sync 需要 1 页以上")

    findings = []

    def rebuild():
        window.st_manager.updateSceneTextitems()
        for _ in range(8):
            app.processEvents()

    def snap(label: str) -> bool:
        app.processEvents()
        blk = proj.current_block_list()[0]
        item = window.st_manager.textblk_item_list[0]
        doc = item.document()
        data = int(blk.fontformat.alignment)
        doc_default = _align_int(doc.defaultTextOption().alignment())
        blk_fmt = _align_int(doc.firstBlock().blockFormat().alignment())
        blk_opt = _align_int(doc.firstBlock().layout().textOption().alignment())
        html_align = 'align' in (blk.rich_text or '')
        # 渲染真值 = 块级 blockFormat 对齐（非 0）优先，否则 doc 默认
        render = blk_fmt if blk_fmt else doc_default
        ok = data == render
        findings.append((label, ok))
        print(
            f"[mw_repro] fmt-sync {label}: 数据层={data} doc默认={doc_default} "
            f"块blockFormat={blk_fmt} 首块option={blk_opt} "
            f"rich_text带align={html_align} → 渲染真值={render} "
            f"{'一致' if ok else '★分叉'}",
            flush=True,
        )
        return ok

    def live_item():
        return window.st_manager.textblk_item_list[0]

    blk = proj.current_block_list()[0]

    # ---- A0 基线：数据层居中 + 无 align 的 HTML，重建后应一致 ----
    blk.alignment = TextAlignment.Center
    blk.rich_text = '<p style="color:#222">基线文本：无段落对齐属性</p>'
    rebuild()
    snap("A0 基线（数据居中/HTML无align）")

    # 我们自己的 toHtml 是否会把 doc 默认对齐写进段落属性
    # （决定「本工具保存的工程」会不会自带 align）
    probe_html = live_item().toHtml()
    print(
        f"[mw_repro] fmt-sync 自产toHtml含align属性: "
        f"{'align=' in probe_html}",
        flush=True,
    )

    # ---- A1 旧项目方向：HTML 自带段落对齐，数据层仍居中 ----
    # 两种写法都试：旧式 align 属性与 CSS text-align（Qt 解析行为可能不同）
    blk.rich_text = (
        '<p align="right">旧项目文本A：HTML 段落自带 align 属性</p>'
    )
    blk.fontformat.alignment = TextAlignment.Center
    rebuild()
    snap("A1a <p align=right>（模拟旧工程）")
    blk.rich_text = (
        '<p style="text-align:right">旧项目文本B：CSS text-align 右对齐，'
        "用于模拟旧版/上游工程保存的富文本</p>"
    )
    rebuild()
    snap("A1b <p style=text-align:right>（模拟旧工程）")

    # ---- A2 对活 item 整包重应用：能否治愈 A1 ----
    live_item().set_fontformat(blk.fontformat, set_char_format=True)
    snap("A2 set_fontformat整包重应用后")

    # ---- B0/B1/B2 数据侧直写方向（postprocess 同款） ----
    blk.rich_text = '<p style="color:#222">探针B文本：无段落对齐属性</p>'
    blk.alignment = TextAlignment.Center
    rebuild()
    snap("B0 重建基线（数据居中）")
    blk.alignment = TextAlignment.Right  # item 在场，只写数据层
    snap("B1 数据侧直写Right后（渲染应仍居中）")
    live_item().set_fontformat(blk.fontformat, set_char_format=True)
    snap("B2 set_fontformat重应用后")

    # ---- C 样式管理器空 diff 门（只读演示，不改工程） ----
    try:
        from ui.style_format_editor import FormatEditorPanel

        panel = FormatEditorPanel()
        panel.set_format(blk.fontformat.deepcopy())
        changed = panel.changed_values()
        gate = "空 dict" if not changed else str(changed)
        print(
            f"[mw_repro] fmt-sync C 空diff门: 编辑器与数据层同值时 "
            f"changed_values={gate} → _apply_* 直接 return，不会触发重建",
            flush=True,
        )
    except Exception as e:  # 面板实例化失败不影响前面的探针结论
        print(f"[mw_repro] fmt-sync C 演示跳过: {e}", flush=True)

    n_ok = sum(1 for _, ok in findings if ok)
    print(
        f"[mw_repro] fmt-sync 汇总: {n_ok}/{len(findings)} 处一致"
        f"（A1a/A1b/A2 一致=段落对齐归一生效，分叉=修复失效；"
        f"B1 为数据侧直写的机制演示，分叉属预期，B2 验证可治愈）",
        flush=True,
    )
    print("[mw_repro] SCENARIO OK: fmt-sync", flush=True)


def main():
    args = _parse_args()
    if args.project and args.scenario != "none":
        # 真实工程只读：改写场景会动并保存用户数据，一律拒绝
        print(
            "[mw_repro] --project 只读演练，--scenario 强制为 none"
            "（改写场景请用合成工程）",
            flush=True,
        )
        args.scenario = "none"

    app, QTimer = _setup_qt(args)
    window = _open_mainwindow(app, args)
    print(
        f"[mw_repro] pages={window.imgtrans_proj.num_pages} "
        f"current={window.imgtrans_proj.current_img}",
        flush=True,
    )

    if not args.project:
        _make_synthetic(
            window, app, args,
            style=(
                "vertical-stroke"
                if args.scenario == "stroke-switch" else "plain"
            ),
        )

    if args.scenario == "group-undo":
        _scenario_group_undo(window, app, args)
    elif args.scenario == "stroke-switch":
        _scenario_stroke_switch(window, app, args)
    elif args.scenario == "fmt-sync":
        _scenario_fmt_sync(window, app, args)
    elif args.scenario == "align-dialog":
        _scenario_align_dialog(window, app, args)
    else:
        print("[mw_repro] scenario=none：主窗口已就绪，进入事件循环", flush=True)

    QTimer.singleShot(0, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
