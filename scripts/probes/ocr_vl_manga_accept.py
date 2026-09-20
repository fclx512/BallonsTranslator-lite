"""`paddleocr_vl_manga` 的真机验收探针（只读、不联网、不写项目数据）。

做三件事，把 `docs/技术实现/模型文件管理_测试流程.md` 第 4 层的数字测出来：

1. 逐块识别：拿施工区副本里块数最多的一页，前 N 块喂本模块，与项目内既有
   （ppocrv6 的）文本并排打印并算字符相似度——既有文本不是标准答案，只作质量对照，
   真正的质量判断要人看。
2. 整页一次喂：看它是否自带检测/版面能力（结论：**不是**，见下）。
3. 显存与耗时：加载耗时、单块耗时分布、峰值显存。

**已测结论（2026-09-20，RTX 5070 Ti Laptop 11.9GB / torch 2.13.0+cu132）**

- 加载 3.7s；加载后 allocated 1.80GB；整页跑完峰值 allocated 2.51GB / reserved 3.22GB。
- 单块中位约 190ms（首块 865ms 含预热）。小 crop 会先被 processor 放大到
  `min_pixels = 147384`（≈384×384）再编码，这是小气泡也不便宜的原因。
- 质量对照那页：气泡正文基本与 ppocrv6 打平或更好（`えマジで` 对 `えてで`、
  `さっぱりだけど` 对 `キいはーだけン`），**拟声词是明显增量**（`ざわ`/`がや` 在
  ppocrv6 下是空的或是 `#`）——这正是本模块的定位。
- **整页一次喂不能用**：同样一页 27 个文本块，整页输出只有 11 行，且只覆盖页面的一部分
  内容、无任何坐标。权重仓库自述「Best Use Case: Manga text crops (individual text
  bubbles/regions, not full pages)」，本探针与该自述一致 ⇒ 检测必须由本项目自己的
  检测器提供，本模块只做识别。

跑法（仓库根目录）：

    ballontrans_pylibs_win\\python.exe scripts/probes/ocr_vl_manga_accept.py

报告写 `tmp/vl_e2e_report.md`（UTF-8；**别让它走 PowerShell 管道**，控制台是 GBK，
非 ASCII 会乱码）。
"""
import difflib
import os.path as osp
import sys
import time

PROGRAM_PATH = osp.abspath(osp.join(osp.dirname(__file__), "..", ".."))
sys.path.insert(0, PROGRAM_PATH)

# 样本目录：只读的施工区（`D:\汉化\施工区`）或可写副本（`...\施工区副本`）。
# 本探针不写项目数据，所以两者都可；副本页数与块数更全。
PROJ_DIR = r"D:\汉化\施工区副本"
MAX_BLOCKS = 12
REPORT = osp.join(PROGRAM_PATH, "tmp", "vl_e2e_report.md")


def main() -> int:
    import torch

    from utils.proj_imgtrans import ProjImgTrans

    lines: list[str] = []

    def emit(text=""):
        lines.append(text)

    emit("# paddleocr_vl_manga 端到端验证报告")
    emit()
    emit("## 1. 项目与页面")
    proj = ProjImgTrans(directory=PROJ_DIR)
    pages = proj.pages
    page = max(pages, key=lambda k: len(pages[k]))
    blks = pages[page]
    img = proj.read_img(page)
    emit(f"- 样本：`{PROJ_DIR}`，共 {len(pages)} 页")
    emit(f"- 取块数最多的一页：`{page}`，图像 {img.shape[1]}x{img.shape[0]}，块数 {len(blks)}")

    emit()
    emit("## 2. 模型加载")
    from modules.ocr.ocr_vl_manga import PaddleOCRVLManga

    mod = PaddleOCRVLManga()
    t0 = time.time()
    mod.load_model()
    load_s = time.time() - t0
    emit(f"- 加载耗时 **{load_s:.1f}s**，device=`{mod._device}`")
    emit(f"- 加载后 VRAM allocated {torch.cuda.memory_allocated() / 2**30:.2f} GB / "
         f"reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB")

    orig_run_batch = mod._run_batch
    per_block: list[tuple[float, tuple]] = []

    def timed_run_batch(images):
        t = time.perf_counter()
        out = orig_run_batch(images)
        per_block.append((time.perf_counter() - t, images[0].shape))
        return out

    mod._run_batch = timed_run_batch

    target = blks[:MAX_BLOCKS]
    stored = [list(b.text) for b in target]
    crop_shapes = [
        (None if (c := PaddleOCRVLManga.crop_block(img, b)) is None else c.shape)
        for b in target
    ]

    emit()
    emit(f"## 3. 逐块识别（前 {len(target)} 块）")
    t0 = time.time()
    mod._ocr_blk_list(img, target)
    elapsed = time.time() - t0
    times = [t for t, _ in per_block]
    emit(f"- 总耗时 **{elapsed:.1f}s**（{len(target)} 块）")
    if len(times) > 1:
        rest = sorted(times[1:])
        emit(f"- 单块耗时：首块 {times[0] * 1000:.0f}ms（含预热），"
             f"其余中位 {rest[len(rest) // 2] * 1000:.0f}ms，"
             f"最快 {rest[0] * 1000:.0f}ms，最慢 {rest[-1] * 1000:.0f}ms")
    emit()

    same = 0
    for i, b in enumerate(target):
        crop = crop_shapes[i]
        crop_s = "-" if crop is None else f"{crop[1]}x{crop[0]}"
        ms = f"{per_block[i][0] * 1000:.0f}ms" if i < len(per_block) else "-"
        model_text = "".join(b.text) if b.text else "(空)"
        old_text = "".join(stored[i]) if stored[i] else "(空)"
        ratio = difflib.SequenceMatcher(None, model_text, old_text).ratio()
        same += int(model_text == old_text)
        emit(f"### 块 {i}　crop {crop_s}　{ms}　相似度 {ratio:.2f}")
        emit(f"- 本模型：`{model_text}`")
        emit(f"- 既有(ppocrv6)：`{old_text}`")
        emit()

    emit(f"**与既有文本逐字相同：{same}/{len(target)}**"
         "（既有文本出自另一个模型 ppocrv6，不是标准答案，仅供质量对照）")

    emit()
    emit("## 4. 整页一次喂（看是否自带检测/版面能力）")
    mod.set_param_value("max_new_tokens", 1024)
    t0 = time.time()
    whole = mod._run_batch([img])
    whole_s = time.time() - t0
    px = img.shape[0] * img.shape[1]
    emit(f"- 耗时 **{whole_s:.1f}s**；图像 {img.shape[1]}x{img.shape[0]} = {px / 1e6:.2f}M 像素，"
         f"`max_pixels` 2.82M ⇒ {'在预算内、未被缩放' if px <= 2822400 else '超预算、被缩放'}")
    emit(f"- 输出 {len(whole[0])} 行，**无坐标信息**")
    emit()
    emit("```")
    for line in whole[0][:60]:
        emit(line)
    emit("```")
    emit()
    emit(f"- 页面实际有 {len(blks)} 个文本块，整页输出 {len(whole[0])} 行 ⇒ "
         "整页模式只覆盖部分内容，**不能替代检测器**（与权重仓库自述"
         "「Best Use Case: Manga text crops, not full pages」一致）")

    emit()
    emit("## 5. 显存")
    emit(f"- 峰值 allocated {torch.cuda.max_memory_allocated() / 2**30:.2f} GB / "
         f"reserved {torch.cuda.max_memory_reserved() / 2**30:.2f} GB")

    mod.unload_model()

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("report written:", REPORT)
    print("blocks=%d identical=%d whole_page_lines=%d" % (len(target), same, len(whole[0])))
    print("load_s=%.1f first_block_ms=%.0f vram_peak=%.2fGB"
          % (load_s, times[0] * 1000, torch.cuda.max_memory_allocated() / 2**30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
