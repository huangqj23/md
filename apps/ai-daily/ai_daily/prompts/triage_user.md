今天是 {{ day }}，现在是 {{ now }}（北京时间）。本期窗口：{{ since }} 之后。

近 3 天已经写过（不要重复，除非有实质新进展）：
{% for t in recent %}- {{ t }}
{% else %}（无）
{% endfor %}
窗口开始前已经出现过的动态（发布时间 | 信源 | 标题；不是候选，只用来识别转述的旧闻）：
{% for t in before %}- {{ t }}
{% else %}（无记录）
{% endfor %}
候选条目（编号 | 类型 | 来源 | 可信度 | 发布时间 | 标题 ｜ 摘要 ｜ 热度）：
{% for line in lines %}{{ line }}
{% endfor %}
请输出 json，格式示例：
{"events": [{"title": "中文事件标题，说清谁发布了什么", "items": [3, 17], "track": "llm", "score": 86, "label": "官方", "why": "一句话：工程师为什么要关心", "follow_up": ""}]}
