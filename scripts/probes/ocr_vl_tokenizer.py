"""`paddleocr_vl_manga` 的 tokenizer 加载路径探针（真机、只读、不联网）。

背景（2026-09-20 实测结论）：权重仓库 `tokenizer_config.json` 里写着
``"tokenizer_class": "LlamaTokenizer"`` 与 ``"add_prefix_space": false``。
transformers 的 `LlamaTokenizerFast.__init__` 一见到 `add_prefix_space` 非 None，
就覆写 ``from_slow=True``（它认为 fast 后端无法事后改前缀空格），于是**绕开仓库自带
的 tokenizer.json**、去构造 slow 版再转换；而缺 sentencepiece 时 `slow_tokenizer_class`
是 None，直接抛
``ValueError: Cannot instantiate this tokenizer from a slow version.``
——应用里表现为「选了这个 OCR，跑管线 OCR 失败」。

本探针校对两件事：

1. 该 tokenizer.json 的 `pre_tokenizer` 是否为空（为空则 `add_prefix_space` 语义上不适用）；
2. 「吃 tokenizer.json」（`add_prefix_space=None` 抑制 from_slow）与「装 sentencepiece
   走 slow→fast 转换」（作者训练时的实际路径）两条路的 token id、词表、chat 模板是否一致。

若一致 ⇒ `modules/ocr/ocr_vl_manga.py` 里传 `add_prefix_space=None` 与作者路径等价，
且不必引入 sentencepiece 依赖。**权重更新后请重跑本探针再下结论。**

跑法（仓库根目录）：

    ballontrans_pylibs_win\\python.exe scripts/probes/ocr_vl_tokenizer.py

第二条路径需要 sentencepiece。若本环境没有、又不想动应用环境，可临时装到独立目录：

    ballontrans_pylibs_win\\python.exe -m pip install --target tmp/sp sentencepiece

本脚本会自动把 `tmp/sp` 挂进 sys.path（存在时），于是两条路都能测；缺它时只报 A 路结果。
"""
import json
import os
import os.path as osp
import sys

PROGRAM_PATH = osp.abspath(osp.join(osp.dirname(__file__), "..", ".."))
sys.path.insert(0, PROGRAM_PATH)

MODEL_DIR = osp.join(PROGRAM_PATH, "data", "models", "paddleocr_vl_manga")
LOCAL_SP = osp.join(PROGRAM_PATH, "tmp", "sp")
if osp.isdir(LOCAL_SP):
    sys.path.insert(0, LOCAL_SP)

SAMPLES = [
    "こんにちは、世界！",
    "あとは『メルニィ宇宙鉄道』とか",
    "心拍呼吸正常値",
    "！？",
    "!?",
    "ＯＫ",
    "ok",
    "１２０５",
    "1205",
    "人",
    "⼈",
    " hello",
    "各行\nテスト",
    "",
    "我々魔女協会が長年追い続ける最大の敵",
    "パパッ...!",
    "生存確認っ...!",
]


def main() -> int:
    if not osp.isdir(MODEL_DIR):
        print("SKIP: model dir not found:", MODEL_DIR)
        return 2

    import torch  # noqa: F401  (只为与应用环境一致，本探针实际不用 GPU)
    from transformers import LlamaTokenizerFast

    with open(osp.join(MODEL_DIR, "tokenizer_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    print("tokenizer_class:", cfg.get("tokenizer_class"))
    print("add_prefix_space:", cfg.get("add_prefix_space"), "legacy:", cfg.get("legacy"))

    try:
        import sentencepiece  # noqa: F401

        sp_ok = True
        print("sentencepiece: installed")
    except ImportError:
        sp_ok = False
        print("sentencepiece: missing -> 只能测 A 路（吃 tokenizer.json）")

    print("LlamaTokenizerFast.slow_tokenizer_class:",
          LlamaTokenizerFast.slow_tokenizer_class)

    print()
    print("=== tokenizer.json 的结构 ===")
    with open(osp.join(MODEL_DIR, "tokenizer.json"), encoding="utf-8") as f:
        tok_json = json.load(f)
    print("model type    :", tok_json.get("model", {}).get("type"))
    print("pre_tokenizer :", json.dumps(tok_json.get("pre_tokenizer"), ensure_ascii=False))
    print("normalizer    :", json.dumps(tok_json.get("normalizer"), ensure_ascii=False)[:200])

    from transformers import AutoProcessor, AutoTokenizer

    print()
    print("=== A 路：add_prefix_space=None（吃 tokenizer.json）===")
    try:
        tok_a = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True, add_prefix_space=None)
        print("OK:", type(tok_a).__name__, "vocab =", len(tok_a))
    except Exception as e:  # noqa: BLE001
        print("FAIL:", type(e).__name__, e)
        return 1

    print("=== 默认路（复现崩溃用）===")
    try:
        AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True)
        print("默认路居然也通过了——说明环境里已经有 sentencepiece；"
              "此时两条路都要比对")
    except Exception as e:  # noqa: BLE001
        print("按预期失败:", type(e).__name__, str(e)[:120])

    exit_code = 0
    if sp_ok:
        print("=== B 路：装 sentencepiece 后走 slow→fast 转换（作者路径）===")
        tok_b = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=True)
        print("OK:", type(tok_b).__name__, "vocab =", len(tok_b))

        diff = 0
        for s in SAMPLES:
            ids_a = tok_a(s)["input_ids"]
            ids_b = tok_b(s)["input_ids"]
            if ids_a != ids_b:
                diff += 1
                print(f"  DIFF {s!r}: A={ids_a} B={ids_b}")
        print(f"逐样本 token id 差异: {diff}/{len(SAMPLES)}")

        va, vb = tok_a.get_vocab(), tok_b.get_vocab()
        print("词表大小 A/B:", len(va), len(vb))
        print("仅 A 有:", len(set(va) - set(vb)), "仅 B 有:", len(set(vb) - set(va)))
        print("共有词 id 不一致:", sum(1 for k in set(va) & set(vb) if va[k] != vb[k]))

        tpl = AutoProcessor.from_pretrained(
            MODEL_DIR, trust_remote_code=True, add_prefix_space=None
        ).apply_chat_template(
            [{"role": "user", "content": [{"type": "text", "text": "OCR:"}]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        print("chat 模板:", repr(tpl))
        print("模板 token A/B:", tok_a(tpl)["input_ids"], tok_b(tpl)["input_ids"])
        if diff:
            print()
            print("!! 两条路不等价 —— 此时应改为依赖 sentencepiece（把 sentencepiece")
            print("   加进模块的 requires_packages），不要用 add_prefix_space=None。")
            exit_code = 1
    else:
        print("（B 路跳过：无 sentencepiece）")

    print()
    print("结论：A/B 一致 ⇒ add_prefix_space=None 与作者路径等价。" if not exit_code
          else "结论：见上方差异。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
