"""预置的模型厂商。地址在 2026-09-30 逐个核实过（不带 key 请求返回 401/400，说明地址正确）；
模型名只是建议，按 OpenRouter 当天的列表和各家文档填写，面板里“测试连接”会拉取厂商实际可用的模型。"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    api: str                          # "openai"（OpenAI 兼容接口）或 "anthropic"（官方 SDK）
    base_url: str
    models: tuple[str, ...] = ()      # 建议的模型名
    triage: str = ""                  # 选题默认模型（输入长，用便宜的）
    write: str = ""                   # 写稿默认模型
    key_url: str = ""                 # 去哪儿拿 key
    requires_key: bool = True
    json_mode: bool = True            # 支持 response_format={"type": "json_object"}
    token_param: str = "max_tokens"   # OpenAI 新模型只认 max_completion_tokens
    temperature: bool = True          # 推理模型不接受自定义 temperature
    note: str = ""
    extra: dict = field(default_factory=dict)


PRESETS: tuple[Preset, ...] = (
    Preset("deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
           ("deepseek-flash", "deepseek-v4-pro"), "deepseek-flash", "deepseek-v4-pro",
           "https://platform.deepseek.com/api_keys"),
    Preset("qwen", "通义千问（阿里云百炼）", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1",
           ("qwen3.8-flash", "qwen3.8-max", "qwen-plus"), "qwen3.8-flash", "qwen3.8-max",
           "https://bailian.console.aliyun.com/"),
    Preset("kimi", "Kimi（月之暗面）", "openai", "https://api.moonshot.cn/v1",
           ("kimi-k2.6", "kimi-k3"), "kimi-k2.6", "kimi-k3", "https://platform.moonshot.cn/console/api-keys"),
    Preset("zhipu", "智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4",
           ("glm-5.3-flash", "glm-5.3"), "glm-5.3-flash", "glm-5.3", "https://open.bigmodel.cn/usercenter/apikeys"),
    Preset("minimax", "MiniMax", "openai", "https://api.minimaxi.com/v1",
           ("MiniMax-M3",), "MiniMax-M3", "MiniMax-M3", "https://platform.minimaxi.com/"),
    Preset("doubao", "豆包（火山方舟）", "openai", "https://ark.cn-beijing.volces.com/api/v3",
           key_url="https://console.volcengine.com/ark", note="模型填方舟控制台里的模型 ID 或接入点 ID（ep-…）"),
    Preset("siliconflow", "硅基流动", "openai", "https://api.siliconflow.cn/v1",
           key_url="https://cloud.siliconflow.cn/account/ak", note="模型名用“测试连接”拉取"),
    Preset("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
           ("deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-pro", "anthropic/claude-opus-5",
            "google/gemini-3.8-flash", "openai/gpt-6.1-sol"),
           "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-pro", "https://openrouter.ai/settings/keys"),
    Preset("openai", "OpenAI", "openai", "https://api.openai.com/v1",
           ("gpt-6.1-sol", "gpt-6-luna"), "gpt-6.1-sol", "gpt-6.1-sol", "https://platform.openai.com/api-keys",
           token_param="max_completion_tokens", temperature=False),
    Preset("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
           ("gemini-3.8-flash",), "gemini-3.8-flash", "gemini-3.8-flash", "https://aistudio.google.com/apikey"),
    Preset("xai", "xAI Grok", "openai", "https://api.x.ai/v1",
           ("grok-4.7",), "grok-4.7", "grok-4.7", "https://console.x.ai/"),
    Preset("anthropic", "Claude（Anthropic）", "anthropic", "https://api.anthropic.com",
           ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"), "claude-opus-5", "claude-opus-5",
           "https://console.anthropic.com/settings/keys"),
    Preset("ollama", "Ollama / 本机或内网部署", "openai", "http://localhost:11434/v1",
           requires_key=False, note="本机或内网的 OpenAI 兼容服务（Ollama、vLLM 等），模型名用“测试连接”拉取"),
)
BY_ID = {p.id: p for p in PRESETS}
DEFAULT_PROVIDER = "deepseek"
