"""术语/剧情整合 agent 工作台纯逻辑核(与 translators/agent 平行,Qt-free)。

- draft.py     权威草稿模型:AI patch 只落草稿,唯一落盘路径是 UI「应用」
- tools.py     工具面:只读探索(复用 translators/agent 收窄白名单)+
               submit_glossary_patch / submit_story_patch 两个写出口
- session.py   会话式循环(纯文本回复合法即指令轮结束,非收敛式)
- prompts.py   工作台 system prompt
- precollect.py 频率启发式(自 glossary_extractor.py 迁入,作草稿预填充)
- story.py     剧情数据读写(格式对齐上游 vision_context)
"""
