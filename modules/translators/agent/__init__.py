"""翻译 agent 逻辑包:loop(状态机)/ tools(工具面)/ prompts(注入)/ validator(出口校验)。

实现类 AgentTranslator 在 modules/translators/trans_agent.py——懒加载
注册表只扫 translators 顶层的 trans_*.py,且要求类定义与 params 在扫描
文件内可静态求值,故类落在那层、本包只装纯逻辑。
"""
