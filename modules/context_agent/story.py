"""剧情数据读写:格式对齐上游 vision_context 分支。

- 页段摘要存 image_info[页名]["llm_visual_summary"](上游页级键);
- 全局梗概存项目级 "llm_compact_memory"(上游项目级键);
- 本模块只做取数/序列化;写回项目由工作台「应用」负责,翻译注入只读消费。
"""

from typing import Any, Dict, Mapping, Tuple

PAGE_SUMMARY_KEY = "llm_visual_summary"
SYNOPSIS_KEY = "llm_compact_memory"


def load_story_base(
    image_info: Mapping[str, Mapping[str, Any]],
    synopsis: str = "",
) -> Tuple[Dict[str, str], str]:
    """从项目 image_info 取现有剧情数据作草稿基底。

    返回 (页名→摘要 dict(保持 image_info 顺序、滤空), 全局梗概)。
    """
    summaries: Dict[str, str] = {}
    for page_name, info in image_info.items():
        if not isinstance(info, Mapping):
            continue
        summary = info.get(PAGE_SUMMARY_KEY)
        if isinstance(summary, str) and summary.strip():
            summaries[page_name] = summary.strip()
    return summaries, (synopsis or "").strip()


def export_story_json(
    page_summaries: Mapping[str, str], synopsis: str
) -> str:
    """导出为上游兼容形状的 JSON(诊断/导出用,不用于写回项目)。"""
    import json

    return json.dumps(
        {
            "pages": {k: v for k, v in page_summaries.items()},
            SYNOPSIS_KEY: synopsis,
        },
        ensure_ascii=False,
        indent=2,
    )


def project_synopsis(project) -> str:
    """读项目级全局梗概,供翻译 agent 注入(只读消费;无数据/类型异常返回空)。"""
    value = getattr(project, SYNOPSIS_KEY, "")
    return value.strip() if isinstance(value, str) else ""
