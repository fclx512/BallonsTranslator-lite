"""框扩张 → 译文居中实测探针（2026-09-19，框扩张页重设计前的验收）。

回答一个问题：**批量扩张只改 `_bounding_rect`／`xyxy`，之后填入的译文
能否在扩张后的矩形里如预期地居中重排？** 方法＝真机拉起主窗口，在工程
副本上对同一批块各填一条占位译文，分别在「扩张前／扩张后」渲染结果图，
逐块裁剪并排输出，目视对比文字落点。

用法（仓库根目录）::

    ./ballontrans_pylibs_win/python.exe scripts/probes/expand_centering_probe.py

- 工程副本：``projects/004_819b9e93`` 整目录拷到 ``tmp/expand_centering/proj/``
  （样本目录约定只读，写操作一律在副本上）。
- 扩张走生产路径 ``ui/batch_expand.py::BatchExpand.apply``（10px，全书）。
- 输出：``tmp/expand_centering/{before,after}_<page>.png`` 与
  ``crop/<page>_<idx>.png``（每块左右并排：左＝扩张前、右＝扩张后）。
- 必须窗口模式（offscreen 拉不起 FramelessWindow，同 ``scripts/mw_repro.py``）。
"""

import faulthandler
import os
import shutil
import sys
import tempfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _APP_ROOT)
os.chdir(_APP_ROOT)
faulthandler.enable()

PROJ = os.path.join(_APP_ROOT, "projects", "004_819b9e93")
OUT = os.path.join(_APP_ROOT, "tmp", "expand_centering")
EXPAND_PX = 10
# 占位译文：每行配一段**短于**原文的中文占位（不触发溢出提示框，专心看落点）
_PLACEHOLDER_LINE = "图像翻译"


def _clone_project() -> str:
    # 目录名不可改：工程 json 名＝imgtrans_<目录名>.json，改名会识别成空工程
    dest = os.path.join(OUT, os.path.basename(PROJ))
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(PROJ, dest)
    return dest


def _fill_translations(proj) -> None:
    """全书填占位译文（rich_text 置空走 translation 的朴素路径）。"""
    for blk_list in proj.pages.values():
        for blk in blk_list:
            lines = blk.text if isinstance(blk.text, list) else [blk.text or ""]
            blk.translation = "\n".join(
                _PLACEHOLDER_LINE[: max(1, len(str(line).strip()) // 2 - 1)]
                for line in lines
            )
            blk.rich_text = ""


def _render_pages(window, app, tag: str) -> dict:
    """逐页重建场景并渲染结果图，返回 {页名: 结果图 ndarray(BGR)}。"""
    import numpy as np
    from qtpy.QtGui import QImage

    from utils.io_utils import imwrite

    def qimage_to_bgr(qimg) -> "np.ndarray":
        # render_result_img 的 QImage 是把 cv2 的 BGR 连续缓冲直接按
        # Format_RGB888 包出来的：字节序就是 BGR，取出来勿再换通道
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        height, width = qimg.height(), qimg.width()
        buf = qimg.constBits()
        buf.setsize(height * qimg.bytesPerLine())
        arr = np.frombuffer(buf, dtype=np.uint8)
        # bytesPerLine 有对齐填充：先按行取 width*3 字节再回成立体形状
        return arr.reshape(height, qimg.bytesPerLine())[
            :, : width * 3
        ].reshape(height, width, 3).copy()

    proj = window.imgtrans_proj
    images = {}
    for pagename in list(proj.pages.keys()):
        proj.set_current_img(pagename)
        window.canvas.updateCanvas()
        window.st_manager.updateSceneTextitems()
        app.processEvents()
        images[pagename] = qimage_to_bgr(window.canvas.render_result_img())
        imwrite(
            os.path.join(OUT, f"{tag}_{os.path.splitext(pagename)[0]}.png"),
            images[pagename],
        )
    return images


def _crops(before_images, after_images, blocks) -> int:
    """逐块把扩张前后的结果图裁剪并排（PIL），返回写出张数。

    前后两图裁**同一个绝对窗口**（old∪new 外扩 margin）——窗口一致，
    文字在图里的位移才是真实的位移。
    """
    from PIL import Image

    os.makedirs(os.path.join(OUT, "crop"), exist_ok=True)
    count = 0
    for entry in blocks:
        page = entry["pagename"]
        margin = 24
        x1, y1, x2, y2 = entry["old"]
        ax1, ay1, ax2, ay2 = entry["new"]
        wx1, wy1 = max(0, min(x1, ax1) - margin), max(0, min(y1, ay1) - margin)
        wx2, wy2 = max(x2, ax2) + margin, max(y2, ay2) + margin
        before = Image.fromarray(before_images[page][wy1:wy2, wx1:wx2])
        after = Image.fromarray(after_images[page][wy1:wy2, wx1:wx2])
        height = max(before.height, after.height)
        combo = Image.new("RGB", (before.width * 2 + 8, height), (40, 40, 46))
        combo.paste(before, (0, 0))
        combo.paste(after, (before.width + 8, 0))
        name = f"{os.path.splitext(page)[0]}_{entry['index']:02d}.png"
        combo.save(os.path.join(OUT, "crop", name))
        count += 1
    return count


def main() -> int:
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QApplication, QMessageBox

    app = QApplication([])
    # 自动关掉可能弹出的信息框（不吞确认弹窗以外的路径——本探针不该有）
    original_exec = QMessageBox.exec

    def _patched_exec(self):
        for button in self.buttons():
            if self.buttonRole(button) == self.ButtonRole.AcceptRole:
                QTimer.singleShot(200, button.click)
                break
        return original_exec(self)

    QMessageBox.exec = _patched_exec

    from utils import config as program_config

    program_config.load_config()
    config = program_config.pcfg
    config.open_recent_on_startup = False
    config.check_update_on_startup = False

    from ui.mainwindow import MainWindow

    window = MainWindow(app, config, open_dir="")
    window.show()
    app.processEvents()

    proj_dir = _clone_project()
    window.openDir(proj_dir)
    app.processEvents()
    proj = window.imgtrans_proj

    _fill_translations(proj)
    proj.save()

    before_images = _render_pages(window, app, "before")

    # 生产路径整批扩张：BatchExpand → BatchOperation（含写版本/落盘/写回）
    from ui.batch_expand import BatchExpand, MODE_PX
    from ui.batch_ops import BatchOperation

    op = BatchOperation(
        proj,
        window.st_manager,
        commit=window._sync_and_commit_project,
        sync_block_data=window._sync_block_data,
    )
    engine = BatchExpand(proj, op=op)
    # plan 是只读的：先拿条目（old/new 矩形）供裁剪，apply 内部会同样再规划一遍
    entries = engine.plan(EXPAND_PX, MODE_PX)["entries"]
    report = engine.apply(EXPAND_PX, MODE_PX, label="expand_centering_probe")
    if not report.get("started"):
        print("[probe] expand did not start:", report.get("error"))
        return 1
    blocks = [
        {"pagename": e["pagename"], "index": e["index"], "old": e["old"], "new": e["new"]}
        for e in entries
    ]
    print(f"[probe] expanded {len(blocks)} block(s)")
    app.processEvents()

    after_images = _render_pages(window, app, "after")

    n = _crops(before_images, after_images, blocks)
    print(f"[probe] wrote {n} crop(s) to {os.path.join(OUT, 'crop')}")

    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
