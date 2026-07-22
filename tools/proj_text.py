"""
CLI 工具 — AI agent 读取/修改项目原文译文。

用法：
  python tools/proj_text.py index   <proj_dir>             项目概览
  python tools/proj_text.py read    <proj_dir> --pages 0-4  读取指定页原文译文
  python tools/proj_text.py search  <proj_dir> <keyword>    全文搜索
  python tools/proj_text.py apply   <proj_dir> <patch.json> 应用修改

依赖：proj_compact.py（已内置 chunking + 选择性字段 + hash 校验）
"""

import argparse
import json
import os
import os.path as osp
import sys

sys.path.append(osp.dirname(osp.dirname(osp.abspath(__file__))))

from utils.config import load_config
from utils.proj_imgtrans import ProjImgTrans
from utils.shared import PROGRAM_PATH
from utils.proj_compact import (
    build_detail,
    build_index,
    build_paginated_detail,
    apply_modifications,
    generate_project_hash,
)


def _init(proj_dir: str) -> ProjImgTrans:
    """加载配置和项目，返回 ProjImgTrans 实例。"""
    load_config()
    os.chdir(PROGRAM_PATH)
    proj = ProjImgTrans()
    proj.load(proj_dir)
    return proj


# ── 解析页码 ─────────────────────────────────────────────────────────────


def _parse_page_spec(spec: str, total: int) -> list[int]:
    """解析 '0-4,7,9-12' 为页码列表。"""
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a.strip()), int(b.strip())
            if start < 0 or end >= total or start > end:
                raise ValueError(
                    f"页码范围无效: {part}（项目共 {total} 页，索引 0-{total - 1}）"
                )
            indices.update(range(start, end + 1))
        else:
            idx = int(part)
            if idx < 0 or idx >= total:
                raise ValueError(
                    f"页码无效: {idx}（项目共 {total} 页，索引 0-{total - 1}）"
                )
            indices.add(idx)
    return sorted(indices)


# ── 子命令: index ────────────────────────────────────────────────────────


def cmd_index(args):
    proj = _init(args.proj_dir)
    data = build_index(proj, include_global_font=True)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


# ── 子命令: read ─────────────────────────────────────────────────────────


def cmd_read(args):
    proj = _init(args.proj_dir)
    total = len(proj.pages)
    indices = _parse_page_spec(args.pages, total)

    fields = args.fields
    fields_whitelist = set(f.strip() for f in fields.split(",")) if fields else None

    if args.paginate > 0 and len(indices) > args.paginate:
        chunks = build_paginated_detail(
            proj,
            indices,
            max_pages_per_chunk=args.paginate,
            fields_whitelist=fields_whitelist,
        )
        data = {
            "type": "paginated_detail",
            "n_chunks": len(chunks),
            "total_pages": len(indices),
            "chunks": chunks,
        }
    else:
        data = build_detail(proj, indices, fields_whitelist=fields_whitelist)

    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


# ── 子命令: search ───────────────────────────────────────────────────────


def cmd_search(args):
    proj = _init(args.proj_dir)
    query_lower = args.keyword.lower()
    results: list[dict] = []
    max_results = args.max or 50

    for pidx, (pname, blklist) in enumerate(proj.pages.items()):
        for bidx, blk in enumerate(blklist):
            src = blk.get_text()
            trans = blk.translation or ""
            match_info = None

            if args.field in ("src", "both") and query_lower in src.lower():
                pos = src.lower().find(query_lower)
                start = max(0, pos - 15)
                end = min(len(src), pos + len(args.keyword) + 15)
                snippet = (
                    ("..." if start > 0 else "")
                    + src[start:end]
                    + ("..." if end < len(src) else "")
                )
                match_info = {"field": "src", "snippet": snippet}

            elif args.field in ("trans", "both") and query_lower in trans.lower():
                pos = trans.lower().find(query_lower)
                start = max(0, pos - 15)
                end = min(len(trans), pos + len(args.keyword) + 15)
                snippet = (
                    ("..." if start > 0 else "")
                    + trans[start:end]
                    + ("..." if end < len(trans) else "")
                )
                match_info = {"field": "trans", "snippet": snippet}

            if match_info:
                results.append(
                    {
                        "id": f"{pidx}:{bidx}",
                        "page": pidx,
                        "page_name": pname,
                        **match_info,
                    }
                )
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    json.dump(
        {
            "type": "search_results",
            "query": args.keyword,
            "field": args.field,
            "n_results": len(results),
            "results": results,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    print()


# ── 子命令: apply ────────────────────────────────────────────────────────


def cmd_apply(args):
    proj = _init(args.proj_dir)

    with open(args.patch, "r", encoding="utf-8") as f:
        modifications = json.load(f)

    # hash 校验（防冲突）
    metadata = {}
    if args.hash:
        metadata["hash"] = args.hash
    elif modifications.get("meta", {}).get("hash"):
        metadata["hash"] = modifications["meta"]["hash"]

    changed, warnings = apply_modifications(proj, modifications, metadata=metadata)
    proj.save()

    result = {"type": "apply_result", "changed": changed}
    if warnings:
        result["warnings"] = warnings
    if metadata.get("hash"):
        result["new_hash"] = generate_project_hash(proj)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


# ── 子命令: hash ─────────────────────────────────────────────────────────


def cmd_hash(args):
    """获取当前项目 hash（供后续 apply 时校验用）。"""
    proj = _init(args.proj_dir)
    h = generate_project_hash(proj)
    print(h)


# ── 主入口 ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="BallonsTranslator 项目原文/译文读取修改工具（AI agent 用）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p_index = sub.add_parser("index", help="项目概览（页面列表 + 字符统计）")
    p_index.add_argument("proj_dir", help="项目目录路径")

    # read
    p_read = sub.add_parser("read", help="读取指定页面的原文/译文")
    p_read.add_argument("proj_dir", help="项目目录路径")
    p_read.add_argument("--pages", default="0", help="页码范围，如 0-4,7,9-12（默认第0页）")
    p_read.add_argument(
        "--fields",
        default="src,trans",
        help="返回字段，逗号分隔（默认 src,trans。全部: src,trans,lang,v,lb,ff,fs,...）",
    )
    p_read.add_argument(
        "--paginate",
        type=int,
        default=10,
        help="超过此页数时分块输出（0=不分块，默认10）",
    )

    # search
    p_search = sub.add_parser("search", help="全文搜索原文/译文")
    p_search.add_argument("proj_dir", help="项目目录路径")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument(
        "--field", default="both", choices=("src", "trans", "both"), help="搜索字段"
    )
    p_search.add_argument("--max", type=int, default=50, help="最大返回条数")

    # apply
    p_apply = sub.add_parser("apply", help="应用修改到项目")
    p_apply.add_argument("proj_dir", help="项目目录路径")
    p_apply.add_argument("patch", help="修改 JSON 文件路径")
    p_apply.add_argument("--hash", help="项目 hash（如不提供则从 patch 中读取）")

    # hash
    p_hash = sub.add_parser("hash", help="获取当前项目 hash")
    p_hash.add_argument("proj_dir", help="项目目录路径")

    args = parser.parse_args()

    try:
        {
            "index": cmd_index,
            "read": cmd_read,
            "search": cmd_search,
            "apply": cmd_apply,
            "hash": cmd_hash,
        }[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
