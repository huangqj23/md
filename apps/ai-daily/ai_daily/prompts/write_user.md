事件：{{ ev.title }}
主线：{{ track }}　可信度：{{ ev.label }}
主来源：{{ it.source_name }} {{ it.url }}
{% if others %}其他来源：{% for o in others %}{{ o.source_name }} {{ o.url }}{% if not loop.last %}；{% endif %}{% endfor %}
{% endif %}{% if ev.vram %}显存估算（程序根据 safetensors 元数据算的，可以直接引用）：{{ ev.vram }}
{% endif %}{% if ev.why %}选题理由：{{ ev.why }}
{% endif %}
写法：{{ style }}

---- 原文开始 ----
{{ ev.source_text }}
---- 原文结束 ----

请输出 json，格式示例：
{"title": "中文标题，不超过 28 字", "paragraphs": ["第一段……"], "quotes": [{"claim": "正文里的一个关键事实", "source_quote": "原文里逐字的一句"}], "engineer_note": "一句话：对工程师意味着什么（能不能用、适合什么场景、要注意什么）", "insufficient": false}
