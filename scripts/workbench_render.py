"""工作台各任务页 → PNG（目视验收布局用）。

**这不是测试**，是改工作台 UI 时的"看一眼"工具：合成一个带内容的工程，
把 ``ui/glossary_agent_panel.py::GlossaryAgentPanel`` 的每个任务页截下来。

用法::

    ./ballontrans_pylibs_win/python.exe scripts/workbench_render.py
    ... scripts/workbench_render.py --scale 2 --out tmp/workbench_render

输出：``tmp/workbench_render/`` 下每任务一张 PNG（``00_empty`` 是未开项目的
空态页），并在 stdout 打印导航状态（两行任务按钮／当前项／计数），便于不看图
也能核对结构。

**两个必须照抄 app 启动过程的点**（否则截图没有参考价值）：

1. **不许设 ``QT_QPA_PLATFORM=offscreen``。** 离屏平台插件下
   ``QFontDatabase.families()`` 是**空的**（实测 0 个字体家族），任何文字都
   渲染成豆腐块，且设 ``QApplication.setFont`` 也救不回来——必须走默认的
   windows 平台插件（同理见 ``docs/技术实现/快捷菜单_实现总结.md``）。
2. **要装主题**：``QApplication.setFont`` 用 ``launch.py`` 同款
   （Microsoft YaHei UI）+ ``ui/misc.py::parse_stylesheet`` 解析主题变量后
   ``setStyleSheet``。少任何一步，截图就是"白底裸控件"，看不出真实观感。
3. **宿主不上屏但要 show()**：直接 ``host.show()`` 会让窗口管理器把它压到
   屏幕大小（实测 960×540 的屏上 1180×900 变成 962×531），输出随环境变，
   审批浮层还会被夹成"几乎铺满中央区"、看不出真实尺寸。故先打
   ``WA_DontShowOnScreen`` 再 ``show()``——布局照跑、``showEvent`` 照发
   （面板的惰性规划靠它），但不开原生窗口，尺寸就钉在请求值上。
"""

import argparse
import os
import os.path as osp
import sys
import tempfile

sys.path.insert(0, os.getcwd())

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtGui import QFont  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

# 页码/尺寸固定，输出可比对（不随环境变）
PAGE_A, PAGE_B = "002.jpg", "004.jpg"
PAGE_SIZE = (320, 240)


def build_app(scale: int, theme=None, language=None) -> QApplication:
    """按 ``launch.py`` 的方式起 QApplication（字体 + 语言 + 主题 + 缩放）。"""
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    app = QApplication([])
    # launch.py 同款：设了 CJK 家族才有中文字形
    app_font = QFont("Microsoft YaHei UI")
    if app_font.exactMatch():
        QApplication.setFont(app_font)

    from qtpy.QtCore import QTranslator

    from utils import shared
    from utils.config import load_config, pcfg

    load_config()
    # 仅当前渲染进程覆盖，不保存用户配置。
    if theme:
        pcfg.darkmode = "dark" in theme
        if pcfg.darkmode:
            pcfg.dark_theme = theme
        else:
            pcfg.light_theme = theme
    if language:
        pcfg.display_lang = language
    # 翻译器必须早于 ui.* 导入装好：模块级 QCoreApplication.translate 表
    # （导航标签等）在导入时求值，晚了就冻成英文。引用要挂在 app 上——
    # installTranslator 不接管所有权，局部变量一出函数就可能被 GC。
    translator = QTranslator()
    if translator.load(pcfg.display_lang, shared.TRANSLATE_DIR):
        app.installTranslator(translator)
        app._render_translator = translator
    print("display lang:", pcfg.display_lang, "theme:", pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme)

    from ui.misc import parse_stylesheet

    app.setStyleSheet(
        parse_stylesheet(pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme)
    )
    return app


def build_project():
    """合成一个能体现各任务差异的小工程（两页，含各类标签与可合并的邻框）。"""
    from utils.block_tags import (
        MISREAD_TAG_ID,
        OCR_REVIEW_ID,
        TRANS_REVIEW_ID,
        set_tag,
    )
    from utils.proj_imgtrans import ProjImgTrans
    from utils.textblock import TextBlock

    def blk(text, xyxy, fg=None):
        block = TextBlock(xyxy=list(xyxy), text=[text])
        block._bounding_rect = [xyxy[0], xyxy[1], xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]]
        if fg is not None:
            block.fg_colors = list(fg)
        return block

    def misread(text, xyxy, subtypes=("numeric",), fg=None):
        block = blk(text, xyxy, fg)
        set_tag(block, MISREAD_TAG_ID, "program", subtypes=list(subtypes))
        return block

    # 块上给文字色（检测阶段本就会写 ``fg_colors``）：取色调板页的「全书扫描」
    # 要靠它才有候选可看——三种重复色 + 一种只出现一次，顺带把「仅出现 1 次」
    # 的过滤开关也照出来（见 docs/技术实现/字体自动取色_设计与实现.md）。
    (red, blue, orange, teal) = (
        [214, 26, 27],
        [27, 25, 50],
        [246, 154, 86],
        [45, 130, 120],
    )

    tmp = tempfile.TemporaryDirectory()
    width, height = PAGE_SIZE
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    cv2.rectangle(img, (20, 20), (140, 90), (180, 200, 230), -1)
    cv2.rectangle(img, (20, 110), (140, 180), (230, 200, 180), -1)
    # 右边一栏画渐变当"复杂背景"对照，左边保持纯色
    gradient = np.tile(np.linspace(60, 220, 80, dtype=np.uint8), (30, 1))
    img[26:56, 150:230] = gradient[:, :, None]
    for name in (PAGE_A, PAGE_B):
        cv2.imwrite(osp.join(tmp.name, name), img)

    # 人工待办：原文待校对（新 ID）、译文待重译；程序侧再造一条低置信度建议，
    # 好让两个待办页的分区（人工记录／程序建议）都有内容可看
    pending = blk("むずかしい漢字", (24, 92, 132, 106), red)
    set_tag(pending, OCR_REVIEW_ID, "manual")
    retrans = blk("ありがとう", (150, 92, 230, 106), orange)
    set_tag(retrans, TRANS_REVIEW_ID, "manual")
    retrans.translation = "谢谢"
    low_conf = blk("てがみ", (150, 120, 230, 146), orange)
    set_tag(low_conf, "ocr_low_conf", "program", score=0.52)

    proj = ProjImgTrans(directory=tmp.name)
    proj.pages[PAGE_A] = [
        blk("こんにちは", (24, 26, 132, 56), red),
        misread("8", (24, 60, 132, 86), ("numeric", "no_japanese"), red),
        misread("……", (150, 26, 230, 56), ("symbolic",), blue),
        blk("ありがとう", (150, 60, 230, 86), red),
        pending,
        retrans,
        low_conf,
    ]
    proj.pages[PAGE_B] = [
        blk("おはよう", (24, 116, 132, 146), red),
        blk("ございます", (24, 150, 132, 176), blue),
        blk("またね", (150, 116, 230, 146), teal),
    ]
    proj._image_info = {
        PAGE_A: {"width": width, "height": height},
        PAGE_B: {"width": width, "height": height},
    }
    # 取色调板页要有行才看得出角色名下拉与删除钮的形态：三态各给一条
    # （占位名 / 已改真名 / 未赋名）。角色名可在页内直接改，见
    # docs/技术实现/字体自动取色_设计与实现.md 的「说话人颜色标记」节。
    from utils.proj_imgtrans import palette_seq_to_name

    proj.fg_palette = [
        {"rgb": [182, 154, 25], "role": palette_seq_to_name(1)},
        {"rgb": [253, 156, 252], "role": "Alice"},
        {"rgb": [246, 154, 86], "role": ""},
    ]
    proj.fg_palette_seq = 1
    proj.save()
    proj.set_current_img(PAGE_A)
    # 遮罩按块文字区画（贴内 3px）——不画遮罩的话「背景修复」页全是"判不出"，
    # 截图看不出判据的真实分布（判据要求窗口内必须有遮罩像素）
    for name in (PAGE_A, PAGE_B):
        mask = np.zeros((height, width), dtype=np.uint8)
        for block in proj.pages.get(name, []):
            bx1, by1, bx2, by2 = (int(v) for v in block.xyxy)
            mask[by1 + 3 : by2 - 3, bx1 + 3 : bx2 - 3] = 255
        proj.save_mask(name, mask)
    return proj, tmp


def _first_table_with_rows(view):
    """任务页里第一个有行的表（批量页一张表，待办队列页分区多张）。

    选中一行才会出审批预览；没有行的页不选（空表选首行会把浮层弹成"取不到图"）。
    """
    tables = getattr(view, "tables", None)
    if callable(tables):  # ui/workbench_review_view.py::ReviewQueueView
        for table, rows in tables():
            if rows:
                return table
        return None
    if view is not None and getattr(view, "_rows", None):
        return getattr(view, "_table", None)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=osp.join("tmp", "workbench_render"))
    parser.add_argument(
        "--scale", type=int, default=2, help="渲染倍率（默认 2，小字看得清）"
    )
    parser.add_argument("--theme", help="仅覆盖渲染进程主题，如 eva-light")
    parser.add_argument("--language", help="仅覆盖渲染进程语言，如 en_US")
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--width", type=int, default=460, help="工作台逻辑宽度")
    args = parser.parse_args()

    app = build_app(args.scale, args.theme, args.language)

    from ui.glossary_agent_panel import WORKBENCH_ORDER, GlossaryAgentPanel
    from ui.workbench_tasks import FG_PALETTE
    from utils.proj_imgtrans import ProjImgTrans

    out = args.out
    os.makedirs(out, exist_ok=True)

    # 宿主模拟"左栏 + 中央区"：工作台靠左 460 宽，右边留出画布区——
    # 审批预览浮层（D44）就浮在那上面，抓整窗才看得见它的实际位置与尺寸
    host = QWidget()
    # 不开原生窗口：否则窗口管理器会把宿主压到屏幕大小（见模块 docstring）
    host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    host.resize(1180, args.height)
    host_lay = QHBoxLayout(host)
    host_lay.setContentsMargins(0, 0, 0, 0)
    host_lay.setSpacing(0)
    # 面板要拿主窗口的落盘/对齐回调，渲染时用替身（真跑批量才需要实现）
    host._sync_block_data = lambda: None
    slot = QWidget(host)  # 左栏槽位：面板与浮层都挂在这里（浮层宿主＝host）
    slot.setFixedWidth(args.width)
    slot_lay = QVBoxLayout(slot)
    slot_lay.setContentsMargins(0, 0, 0, 0)
    host_lay.addWidget(slot)
    # 中央区（画布页的位置）：预览浮层挂在这里——真程序里它的宿主就是
    # MainWindow.centralStackWidget，故浮层不会盖住工作台
    canvas_area = QWidget(host)
    canvas_area.setObjectName("RenderCanvasArea")
    host.centralStackWidget = canvas_area
    host_lay.addWidget(canvas_area, 1)
    empty_panel = GlossaryAgentPanel(ProjImgTrans(), host)
    slot_lay.addWidget(empty_panel)
    host.show()
    app.processEvents()
    host.grab().save(osp.join(out, "00_empty.png"))
    empty_panel.setParent(None)
    empty_panel.deleteLater()

    proj, tmp = build_project()
    host._sync_and_commit_project = lambda force_sync=False: proj.save()
    panel = GlossaryAgentPanel(proj, host)
    slot_lay.addWidget(panel)
    app.processEvents()

    for index, task_id in enumerate(WORKBENCH_ORDER, start=1):
        panel.nav.select(task_id)
        app.processEvents()
        app.processEvents()  # 布局稳定后截图；导航不再有宽度动画。
        view = panel._batch_views.get(task_id) or panel._review_views.get(task_id)
        table = _first_table_with_rows(view)
        if table is not None:
            table.setCurrentCell(0, 0)  # 选中首行，展开审批预览
            app.processEvents()
        name = f"{index:02d}_{task_id}.png"
        host.grab().save(osp.join(out, name))
        panel.grab().save(osp.join(out, "panel_" + name))
        print("saved", osp.join(out, name))

    # 导航结构回显：不打开图也能核对扁平顺序与各项计数
    print("nav:", [(t, b.text()) for t, b in panel.nav._buttons.items()])
    print("current task:", panel.current_task(), "page:", panel.pages.currentIndex())

    # 翻译准备是**一个入口两个子页**：两块草稿各截一张，否则只看到术语那一页
    from ui.workbench_tasks import GLOSSARY, STORY

    for label, sub_id in (("glossary", GLOSSARY), ("story", STORY)):
        panel.select_prep_tab(sub_id)
        app.processEvents()
        app.processEvents()
        name = f"05b_prep_{label}.png"
        host.grab().save(osp.join(out, name))
        print("saved", osp.join(out, name))

    # 富内容状态只写视图，不提交草稿、不触发模型调用。
    from qtpy.QtTest import QTest
    from modules.context_agent.draft import ORIGIN_AI, ORIGIN_USER
    from ui.workbench_tasks import TRANSLATION_PREP, MISREAD
    QTest.qWait(40)  # 等 worker 的初始空草稿同步完成
    panel._sync_glossary_table([
        ("千尋", "千寻", "人物名，统一用此译法", ORIGIN_USER),
        ("ハク", "白龙", "人物名", ORIGIN_AI),
        ("湯屋", "澡堂", "场所名称；较长备注用来检查列宽与全文提示", ORIGIN_AI),
    ])
    panel._sync_story_tab("少女来到陌生世界，与白龙相遇，开始寻找回家的办法。", [
        (PAGE_A, "白龙让千寻在原地等待，并承诺很快回来。", ORIGIN_AI),
        (PAGE_B, "两人继续前往澡堂。", ORIGIN_USER),
    ])
    panel._append_log("删除完成：2 个文本框，图像与修复层保留。", MISREAD)
    panel.select_prep_tab(GLOSSARY)
    panel._append_log("角色建议：A 可对应千寻。请核对台词后在取色调板修改角色名。\n这是一条合成回复，用于检查多行活动记录。", TRANSLATION_PREP)
    panel._activity_toggle.setChecked(True)
    app.processEvents()
    current = panel.pages.currentWidget()
    print("prep geometry:", {
        "viewport": panel._page_scroll.viewport().height(),
        "pages": panel.pages.height(), "page": current.height(),
        "min_hint": current.minimumSizeHint().height(),
        "hfw": current.heightForWidth(current.width()),
        "minimum": current.minimumHeight(),
        "tabs_min": panel._prep_tabs.minimumSizeHint().height(),
        "scroll": panel._page_scroll.verticalScrollBar().maximum(),
    })
    host.grab().save(osp.join(out, "08_prep_activity.png"))
    panel.grab().save(osp.join(out, "panel_08_prep_activity.png"))
    panel.select_prep_tab(STORY)
    app.processEvents()
    host.grab().save(osp.join(out, "09_story_activity.png"))
    panel._on_busy_changed(True)
    app.processEvents()
    host.grab().save(osp.join(out, "10_prep_busy.png"))
    panel._on_busy_changed(False)
    panel._activity_toggle.setChecked(False)
    panel.nav.select(MISREAD)
    panel._last_version_seq = 1  # 只显示撤销入口，不执行
    panel._update_rollback_scope()
    app.processEvents()
    host.grab().save(osp.join(out, "11_batch_undo.png"))
    print("geometry:", {"host": (host.width(), host.height()), "panel": (panel.width(), panel.height()),
                       "nav_rows": len({b.y() for b in panel.nav._buttons.values()}),
                       "body_scroll": panel._page_scroll.verticalScrollBar().maximum()})

    # 退出前收掉 worker 线程（否则解释器退出时残留线程会崩）
    panel._shutdown()
    tmp.cleanup()
    print("DONE", out)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
