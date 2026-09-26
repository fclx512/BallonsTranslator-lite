#!/usr/bin/env python3
"""泛用工作台的参数复算台（只读）。

为什么常驻：C1／C2／C4 的判据与参数是"测出来的"——分组阈值、判据命中率都要
靠真实项目**反复复算**来调整（`ui/batch_merge.py::MergeConfig` 的阈值、
`ProgramConfig` 的工作台设置项）。本工具把这些复算收成一个入口，改判据前后
各跑一次即可对比。

子命令（全部只读；脚本末尾自证样本顶层文件 mtime 未变）：

    merge   [--sweep]                分组口径：组／框／对／excluded／rotated／
                                     oversize／suspect；--sweep 追加阈值敏感度扫描
    c1                               批量简单背景修复的判据命中率
                                     （simple／complex／unknown）
    queue                            误框队列口径（四类子类型计数、待处理数）
    review                           已驳回链路自检（内存构造，不落盘）
    hook    [--page NAME]            D39 程序筛选器端到端（真机跑一次 OCR）
    list                             只打印样本概况（页数／块数／标签）

（原 ``expand`` 子命令随批量框扩张退役，2026-09-26。）

用法：

    ./ballontrans_pylibs_win/python.exe scripts/workbench_recalc.py merge \\
        --project "D:\\汉化\\施工区" --sweep

需要 `ballontrans_pylibs_win` 的 Python（依赖 cv2／onnxruntime 等；`hook` 还要
识别模型）。样本目录约定为**只读**：本工具绝不写项目文件，写操作只在
`ui/batch_*.py` 的 `apply` 里发生。
"""

import argparse
import itertools
import json
import os
import os.path as osp
import sys
import time

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── 通用：只读自证 ──────────────────────────────────────────────


def snapshot(directory: str) -> dict:
    """记录样本顶层文件的 mtime／size，跑完比对，自证只读。"""
    out = {}
    for name in os.listdir(directory):
        path = osp.join(directory, name)
        if osp.isfile(path):
            st = os.stat(path)
            out[name] = (st.st_mtime, st.st_size)
    return out


def assert_readonly(directory: str, before: dict) -> None:
    changed = []
    for name, (mtime, size) in before.items():
        path = osp.join(directory, name)
        if not osp.isfile(path):
            changed.append(name + " (gone)")
            continue
        st = os.stat(path)
        if (st.st_mtime, st.st_size) != (mtime, size):
            changed.append(name)
    backup = osp.isdir(osp.join(directory, ".bt_batch_backup"))
    print("")
    print("[只读自证] 样本顶层文件被改动 %d 个 %s" % (len(changed), changed or ""))
    print("[只读自证] 样本目录里出现 .bt_batch_backup：%s" % backup)


def load_project(directory: str):
    from utils.proj_imgtrans import ProjImgTrans

    return ProjImgTrans(directory=directory)


def project_stats(proj) -> dict:
    blocks = sum(len(b) for b in proj.pages.values())
    return {
        "pages": len(proj.pages),
        "blocks": blocks,
        "not_found": len(getattr(proj, "not_found_pages", {}) or {}),
    }


def _percentiles(values, quantiles):
    if not values:
        return [0 for _ in quantiles]
    values = sorted(values)
    n = len(values)
    out = []
    for q in quantiles:
        idx = n - 1 if q >= 1.0 else max(0, int(n * q) - 1)
        out.append(values[idx])
    return out


# ── merge：C2 分组口径 ─────────────────────────────────────────


def cmd_merge(args) -> int:
    from ui.batch_merge import BatchMerge, MergeConfig

    proj = load_project(args.project)
    stats = project_stats(proj)
    print("样本：%s" % args.project)
    print("页数=%d 块数=%d not_found=%d" % (stats["pages"], stats["blocks"], stats["not_found"]))
    info = proj._image_info
    have_size = sum(
        1
        for p in proj.pages
        if (info.get(p) or {}).get("width") and (info.get(p) or {}).get("height")
    )
    print("image_info 含宽高的页 = %d/%d" % (have_size, len(proj.pages)))

    def run(cfg):
        t0 = time.time()
        return BatchMerge(proj, config=cfg).plan(), time.time() - t0

    cfg = MergeConfig(
        oversize_ratio=float(args.oversize_ratio)
    ) if args.oversize_ratio is not None else MergeConfig()
    report, elapsed = run(cfg)
    print("")
    print("=== 默认口径（MergeConfig）===")
    print("耗时 %.1fs" % elapsed)
    print(
        "group_count=%d member_count=%d pair_count=%d excluded=%d rotated=%d "
        "oversize=%d suspect=%d apply_default=%d"
        % (
            report["group_count"], report["member_count"], report["pair_count"],
            report["excluded"], report["rotated"], report["oversize"],
            report["suspect"], report["apply_default"],
        )
    )
    print("（设计 §16 的旧口径记录：435 组 / 1323 框 / 1042 对，含全部块、无排除）")
    print("（实测报告 2026-09-16 默认口径：380 组 / 1173 框 / 1113 对）")
    if report["groups"]:
        sizes = {}
        for g in report["groups"]:
            sizes[g.size] = sizes.get(g.size, 0) + 1
        print("组大小分布（框数:组数）：%s" % ", ".join(
            "%d:%d" % (k, sizes[k]) for k in sorted(sizes)
        ))
        bases = {}
        for g in report["groups"]:
            bases[g.basis] = bases.get(g.basis, 0) + 1
        print("方向判定依据分布：%s" % bases)
        print("误聚组（D33d）：")
        for g in report["groups"]:
            if g.oversize:
                w = g.bbox[2] - g.bbox[0]
                h = g.bbox[3] - g.bbox[1]
                page = info.get(g.pagename) or {}
                print(
                    "  %s bbox=%s (%dx%d) 页=%sx%s 成员=%s"
                    % (g.pagename, g.bbox, w, h, page.get("width"),
                       page.get("height"), g.indices)
                )

    if args.sweep:
        print("")
        print("=== 阈值敏感度扫描（overlap_min × size_ratio_min × max_gap_ratio）===")
        print("overlap size gap | groups members pairs oversize suspect  秒")
        rows = []
        for ov, sr, mg in itertools.product(
            (0.5, 0.6, 0.7), (0.0, 0.6, 0.8), (0.6, 1.0, 1.5, 2.0)
        ):
            rep, el = run(
                MergeConfig(overlap_min=ov, size_ratio_min=sr, max_gap_ratio=mg)
            )
            rows.append((ov, sr, mg, rep["group_count"], rep["member_count"],
                         rep["pair_count"], rep["oversize"], rep["suspect"], el))
            print(
                "%7s %4s %3s | %6d %7d %5d %8d %7d %4.1f"
                % (ov, sr, mg, rep["group_count"], rep["member_count"],
                   rep["pair_count"], rep["oversize"], rep["suspect"], el)
            )
        groups = [r[3] for r in rows]
        print("组数范围 %d ~ %d（默认口径 %d，波动越小说明阈值越不敏感）"
              % (min(groups), max(groups), report["group_count"]))
        print("调参方向：组数偏高先调小 max_gap_ratio；size_ratio_min 是唯一有实质"
              "影响的门限（关掉它组数会明显上升）；overlap_min 几乎不起作用。")

    if args.json:
        print(json.dumps({
            "pages": stats["pages"],
            "blocks": stats["blocks"],
            "group_count": report["group_count"],
            "member_count": report["member_count"],
            "pair_count": report["pair_count"],
            "excluded": report["excluded"],
            "rotated": report["rotated"],
            "oversize": report["oversize"],
            "suspect": report["suspect"],
        }, ensure_ascii=False))
    return 0


# ── c1：批量简单背景修复的判据命中率 ───────────────────────────


def cmd_c1(args) -> int:
    from ui.batch_inpaint import BatchSimpleInpaint

    proj = load_project(args.project)
    stats = project_stats(proj)
    print("样本：%s" % args.project)
    print("页数=%d 块数=%d" % (stats["pages"], stats["blocks"]))
    t0 = time.time()
    plan = BatchSimpleInpaint(proj, None).plan()
    total = plan["simple"] + plan["complex"] + plan["unknown"]
    print("")
    print("=== C1 判据命中率（classify_simple）===")
    print("耗时 %.1fs" % (time.time() - t0))
    print("simple=%d complex=%d unknown=%d 块数=%d" % (
        plan["simple"], plan["complex"], plan["unknown"], total))
    if total:
        print("占比：simple %.1f%% / complex %.1f%% / unknown %.1f%%" % (
            plan["simple"] / total * 100,
            plan["complex"] / total * 100,
            plan["unknown"] / total * 100,
        ))
    print("有可涂块的页=%d/%d" % (plan["page_count"], stats["pages"]))
    print("（基线：逐行框 8.3% simple / 85.9% unknown；合并成气泡级后 51.3% / 32.0%）")
    print(
        "判据要求「裁剪区内有一条盖住全部遮罩像素的闭合气泡轮廓」——命中率与"
        "框的粒度绑定：逐行框喂进去必然判不出，C2 合并之后再跑才有意义。"
    )
    if args.json:
        print(json.dumps({k: plan[k] for k in ("simple", "complex", "unknown", "page_count")},
                         ensure_ascii=False))
    return 0


# ── queue：误框队列口径 ────────────────────────────────────────


def cmd_queue(args) -> int:
    from utils.block_tags import (
        iter_misread_blocks,
        misread_queue_summary,
        misread_subtype_counts,
    )

    proj = load_project(args.project)
    stats = project_stats(proj)
    summary = misread_queue_summary(proj.pages)
    counts = misread_subtype_counts(proj.pages)
    print("样本：%s" % args.project)
    print("页数=%d 块数=%d" % (stats["pages"], stats["blocks"]))
    print("")
    print("=== 误框队列（D28 口径）===")
    print("队列块数=%d 已驳回=%d 待处理=%d"
          % (summary["total"], summary["rejected"], summary["pending"]))
    print("子类型命中（一块可跨类，合计可大于块数）：%s"
          % {k: counts[k] for k in ("no_japanese", "empty", "numeric", "symbolic")})
    print("命中合计=%d" % sum(counts.values()))
    pages = {}
    for pagename, _idx, _blk in iter_misread_blocks(proj.pages):
        pages[pagename] = pages.get(pagename, 0) + 1
    print("涉及页数=%d" % len(pages))
    print("（A1 分类器基线：128 块 / 92-36-20-15 / 命中合计 163 / 51 页）")
    if args.json:
        print(json.dumps({"total": summary["total"], "rejected": summary["rejected"],
                          "pending": summary["pending"], "subtypes": counts},
                         ensure_ascii=False))
    return 0


# ── review：已驳回链路自检（内存，不落盘）──────────────────────


def cmd_review(args) -> int:
    from ui.batch_delete import BatchDeleteMisread
    from ui.batch_merge import BatchMerge, MergeConfig
    from utils.block_tags import (
        MISREAD_TAG_ID,
        get_tag,
        is_tag_reviewed,
        iter_misread_blocks,
        misread_queue_summary,
        set_tag,
        set_tags_reviewed,
        tag_misread_hook,
    )

    proj = load_project(args.project)
    pages = proj.pages
    base = misread_queue_summary(pages)
    print("样本：%s" % args.project)
    print("基线队列：%s" % base)
    target = None
    for pagename, idx, blk in iter_misread_blocks(pages):
        target = (pagename, idx, blk)
        break
    if target is None:
        print("样本没有队列块，无法自检")
        return 1
    pagename, idx, blk = target
    before_entry = dict(get_tag(blk, MISREAD_TAG_ID) or {})
    print("目标块：%s[%d] text=%r subtypes=%s"
          % (pagename, idx, blk.get_text(), before_entry.get("subtypes")))

    # 粒度＝一条问题（D28 修订）：表态必须带 tag_id，否则会连带冻结同块的
    # 其它程序问题（如低置信度建议）
    changed = set_tags_reviewed(blk, True, MISREAD_TAG_ID)
    after = dict(get_tag(blk, MISREAD_TAG_ID) or {})
    summary = misread_queue_summary(pages)
    print("")
    print("=== 1. 驳回（D28：针对这一条问题、可逆、条目保留）===")
    print("改动 program 条目=%d 条目仍在=%s reviewed=%s"
          % (changed, MISREAD_TAG_ID in blk.tags, after.get("reviewed")))
    if "ocr_low_conf" in (blk.tags or {}):
        print("同块的低置信度建议未被牵连：reviewed=%s"
              % is_tag_reviewed(blk, "ocr_low_conf"))
    print("队列=%s（期望 total 不变、rejected=%d、pending=%d）"
          % (summary, base["rejected"] + 1, base["pending"] - 1))

    plan = BatchDeleteMisread(proj).plan()
    keys = [e.key for e in plan["entries"]]
    print("")
    print("=== 2. C4 默认口径（已驳回不进默认删除）===")
    print("plan: queue=%d rejected=%d pending=%d apply_default=%d entries=%d"
          % (plan["queue"], plan["rejected"], plan["pending"],
             plan["apply_default"], len(plan["entries"])))
    print("目标块仍在 entries=%s；不在默认选择集=%s"
          % ((pagename, idx) in keys, plan["apply_default"] == summary["pending"]))

    print("")
    print("=== 3. 重跑 OCR 后驳回记忆存活（hook 路径）===")

    class _FakeOCR:
        name = "paddleocr_v6_onnx"

    tag_misread_hook(textblocks=[blk], img=None, ocr_module=_FakeOCR())
    re_entry = dict(get_tag(blk, MISREAD_TAG_ID) or {})
    print("重跑后 subtypes=%s reviewed=%s"
          % (re_entry.get("subtypes"), re_entry.get("reviewed")))
    print("（none_ocr 短路另测：ocr_module.name 为 none_ocr 时不挂标）")

    print("")
    print("=== 4. D30：已驳回的误框重新参与合并 ===")
    set_tags_reviewed(blk, False, MISREAD_TAG_ID)
    unrev = BatchMerge(proj, config=MergeConfig()).plan()
    set_tags_reviewed(blk, True, MISREAD_TAG_ID)
    rev = BatchMerge(proj, config=MergeConfig()).plan()
    print("未驳回：excluded=%d member=%d / 已驳回：excluded=%d member=%d"
          % (unrev["excluded"], unrev["member_count"],
             rev["excluded"], rev["member_count"]))
    print("（D30：已驳回的框不再被排除 —— excluded 应减少）")

    print("")
    print("=== 5. D33c：合并块的 reviewed 语义 ===")
    # 找一个"驳回后真能进组"的队列块：孤立块不成组，逐块试（最多 60 个）
    group = None
    probe_blk = None
    tried = 0
    for probe_page, probe_idx, probe in iter_misread_blocks(pages):
        if tried >= 60:
            break
        tried += 1
        set_tags_reviewed(probe, True, MISREAD_TAG_ID)
        rep = BatchMerge(proj, config=MergeConfig()).plan()
        hit = next(
            (g for g in rep["groups"]
             if g.pagename == probe_page and probe_idx in g.indices),
            None,
        )
        if hit is not None:
            group, probe_blk = hit, probe
            print("找到一个可成组的已驳回误框：%s[%d]（试了 %d 个）"
                  % (probe_page, probe_idx, tried))
            break
        set_tags_reviewed(probe, False, MISREAD_TAG_ID)

    if group is None:
        print("尝试 %d 个队列块，都不与邻框构成候选组 —— 该样本上无法验证"
              "「合并块 reviewed 语义」（合成单测已覆盖该语义）" % tried)
    else:
        print("该组：%s members=%d" % (group.indices, len(group.indices)))
        merged = BatchMerge(proj, config=MergeConfig()).build_merged_block(
            group.pagename, group, flatten_lines=True
        )
        entry = (merged.tags or {}).get(MISREAD_TAG_ID) or {}
        print("合并块 tags[ocr_misread]=%s" % entry)
        print("期望：贡献者全部已驳回 → 保留 reviewed（既不洗白也不虚化）→ "
              "PASS" if entry.get("reviewed") else "期望未满足 → FAIL")
        # 构造边界：给一个未驳回成员也挂上标签
        victim = None
        for i in group.indices:
            candidate_blk = pages[group.pagename][i]
            if MISREAD_TAG_ID not in (candidate_blk.tags or {}):
                victim = candidate_blk
                break
        if victim is None:
            victim = pages[group.pagename][group.indices[0]]
            set_tags_reviewed(victim, False, MISREAD_TAG_ID)
        else:
            set_tag(victim, MISREAD_TAG_ID, "program", subtypes=["no_japanese"])
        merged2 = BatchMerge(proj, config=MergeConfig()).build_merged_block(
            group.pagename, group, flatten_lines=True
        )
        entry2 = (merged2.tags or {}).get(MISREAD_TAG_ID) or {}
        print("给一个未驳回成员挂标后：%s" % entry2)
        print("期望：reviewed 被移除（有成员未驳回 → 合并块回到未驳回态、重新进队列）"
              "→ %s" % ("PASS" if not entry2.get("reviewed") else "FAIL"))
        _ = probe_blk

    print("")
    print("（本子命令全程只改内存对象：样本文件不会被写；队列与合并的写回只在 "
          "ui/batch_*.py 的 apply 里发生）")
    if args.json:
        print(json.dumps({"total": base["total"], "rejected": base["rejected"],
                          "target": [pagename, idx]}, ensure_ascii=False))
    return 0


# ── hook：D39 端到端（真机跑一次 OCR）──────────────────────────


def cmd_hook(args) -> int:
    from utils.block_tags import (
        MISREAD_TAG_ID,
        classify_misread_text,
        clear_program_tags,
        get_tag,
    )
    from utils.io_utils import imread

    proj = load_project(args.project)
    pages = proj.pages
    pagename = args.page
    if not pagename:
        pagename = max(pages, key=lambda p: len(pages[p] or []))
    if pagename not in pages:
        print("页不存在：%s" % pagename)
        return 1

    from modules.ocr.base import OCRBase
    from utils.block_tags import tag_misread_hook

    # 与 ui/module_manager.py 同一行：注册一次即覆盖全部 OCR 模块
    OCRBase.register_postprocess_hooks({"tag_misread": tag_misread_hook})
    from modules.ocr.ocr_onnx import PaddleOCRv6ONNX

    mod = PaddleOCRv6ONNX(device=args.device)
    print("样本：%s / 页=%s（%d 块）" % (args.project, pagename, len(pages[pagename])))
    print("模块 name=%r / 设备=%s" % (mod.name, args.device))

    blks = list(pages[pagename])
    img = imread(osp.join(args.project, pagename))
    if img is None:
        print("缺图，无法跑 OCR：%s" % pagename)
        return 1
    for blk in blks:
        clear_program_tags(blk)
    mod.run_ocr(img, blks)

    mismatch = 0
    hits = 0
    low_conf = 0
    print("")
    print("=== 逐块核对挂标 ===")
    for i, blk in enumerate(blks):
        text = blk.get_text()
        expect = classify_misread_text(text)
        actual = list((get_tag(blk, MISREAD_TAG_ID) or {}).get("subtypes") or [])
        ok = sorted(expect) == sorted(actual)
        mismatch += 0 if ok else 1
        hits += 1 if expect else 0
        low_conf += 1 if "ocr_low_conf" in blk.tags else 0
        print("  [%2d] %-30r 期望=%-30s 实际=%-30s %s"
              % (i, text[:28], expect, actual, "ok" if ok else "!! MISMATCH"))
    print("")
    print("块数=%d 误识别命中=%d ocr_low_conf=%d 不一致=%d"
          % (len(blks), hits, low_conf, mismatch))
    print("断言（判定与分类器逐块一致）：%s" % ("PASS" if mismatch == 0 else "FAIL"))

    if args.json:
        print(json.dumps({"page": pagename, "blocks": len(blks), "misread": hits,
                          "low_conf": low_conf, "mismatch": mismatch},
                         ensure_ascii=False))
    return 0 if mismatch == 0 else 1


# ── list ──────────────────────────────────────────────────────


def cmd_list(args) -> int:
    proj = load_project(args.project)
    stats = project_stats(proj)
    info = proj._image_info
    print("样本：%s" % args.project)
    print("页数=%d 块数=%d not_found=%d" % (stats["pages"], stats["blocks"], stats["not_found"]))
    det_models = {}
    tags = {}
    for blks in proj.pages.values():
        for blk in blks:
            det = getattr(blk, "det_model", None)
            if det:
                det_models[det] = det_models.get(det, 0) + 1
            for tid in (blk.tags or {}):
                tags[tid] = tags.get(tid, 0) + 1
    print("det_model 分布：%s" % det_models)
    print("标签分布：%s" % tags)
    have_size = sum(
        1 for p in proj.pages
        if (info.get(p) or {}).get("width") and (info.get(p) or {}).get("height")
    )
    print("image_info 含宽高的页=%d/%d" % (have_size, len(proj.pages)))
    return 0


# ── main ──────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="泛用工作台的参数复算台（只读）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[-1],
    )
    parser.add_argument("command",
                        choices=["merge", "c1", "queue", "review",
                                 "hook", "list"])
    parser.add_argument("--project", required=True,
                        help="项目目录（含 imgtrans_*.json 与图片）")
    parser.add_argument("--json", action="store_true",
                        help="额外打印一行机器可读 JSON")
    parser.add_argument("--sweep", action="store_true",
                        help="merge：追加阈值敏感度扫描")
    parser.add_argument("--oversize-ratio", type=float, default=None,
                        help="merge：误聚阈值（缺省取设置里的值）")
    parser.add_argument("--page", default=None, help="hook：要跑 OCR 的页")
    parser.add_argument("--device", default="cpu", help="hook：OCR 设备")
    args = parser.parse_args()

    if not osp.isdir(args.project):
        print("项目目录不存在：%s" % args.project)
        return 2

    before = snapshot(args.project)
    handlers = {
        "merge": cmd_merge, "c1": cmd_c1,
        "queue": cmd_queue, "review": cmd_review, "hook": cmd_hook,
        "list": cmd_list,
    }
    try:
        code = handlers[args.command](args)
    finally:
        assert_readonly(args.project, before)
    return code


if __name__ == "__main__":
    sys.exit(main())
