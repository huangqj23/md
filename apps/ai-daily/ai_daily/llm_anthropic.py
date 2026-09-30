"""Claude（Anthropic 官方 SDK）后端，接口与 llm.OpenAICompatLLM 相同：.json(system, user) / .list_models()。

- Claude Opus 5 等模型的安全分类器可能拒绝请求（HTTP 200 + stop_reason="refusal"），所以先看 stop_reason 再读内容，
  并默认开启服务端回退 fallbacks="default"（beta server-side-fallback-2026-07-01）：被拒时由 Anthropic 按拒绝类别
  换一个模型重跑同一请求。
- 这些模型不接受 temperature 等采样参数，也不支持 assistant prefill，JSON 格式靠提示词约束 + 解析重试。
- 自适应思考默认开启，思考 token 计入 max_tokens，所以 max_tokens 至少给 16000。
"""
import logging

import anthropic

from .llm import LLMError, _Usage, parse_json_object

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5", "claude-opus-5-5", "claude-fable-5", "claude-fable-5-1"}
DEFAULT_BASE_URL = "https://api.anthropic.com"
MIN_MAX_TOKENS = 16000
MAX_NON_STREAMING = 20000     # 非流式请求再大，SDK 会要求改用流式


class AnthropicLLM(_Usage):
    def __init__(self, api_key: str, model: str, *, base_url: str | None = None, label: str = "",
                 timeout: float = 600, max_retries: int = 3, http_client=None):
        super().__init__()
        kw = {"api_key": api_key, "timeout": timeout, "max_retries": max_retries}
        if base_url and base_url.rstrip("/") != DEFAULT_BASE_URL:
            kw["base_url"] = base_url
        if http_client is not None:
            kw["http_client"] = http_client
        self.client = anthropic.Anthropic(**kw)
        self.model = model
        self.label = label or model

    def _create(self, system: str, messages: list, max_tokens: int):
        params = {"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": messages}
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        return self.client.messages.create(**params)

    def json(self, system: str, user: str, *, max_tokens: int = 8000, temperature: float | None = None,
             attempts: int = 3) -> dict:
        messages = [{"role": "user", "content": user}]
        budget, last_error = max(max_tokens, MIN_MAX_TOKENS), ""
        for attempt in range(attempts):
            try:
                resp = self._create(system, messages, budget)
            except (anthropic.BadRequestError, anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                    anthropic.NotFoundError) as e:
                raise LLMError(f"{self.label} 请求被拒：{e}") from e
            except anthropic.APIError as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning("%s 调用失败（第 %d 次）：%s", self.label, attempt + 1, last_error)
                continue
            if resp.usage is not None:
                self._count(resp.usage.input_tokens, resp.usage.output_tokens)
            if resp.stop_reason == "refusal":           # 回退链也拒绝了；不要原样重试
                category = getattr(resp.stop_details, "category", None) if resp.stop_details else None
                raise LLMError(f"{self.label} 拒绝了这个请求（类别：{category or '未注明'}），这条需要人工处理")
            text = "".join(block.text for block in resp.content if block.type == "text").strip()
            if text:
                try:
                    return parse_json_object(text)
                except ValueError as e:
                    last_error = f"JSON 解析失败：{e}"
            else:
                last_error = f"没有文字输出（stop_reason={resp.stop_reason}）"
            if resp.stop_reason == "max_tokens":         # 思考 token 也算在上限里：提高上限、用原始消息重试
                budget = min(budget * 2, MAX_NON_STREAMING)
                last_error += f"（输出被 max_tokens 截断，上限提到 {budget} 再试）"
            elif text:
                messages = messages[:1] + [{"role": "assistant", "content": text},
                                           {"role": "user", "content": f"上次的输出有问题：{last_error}。请只输出一个合法的 json 对象。"}]
            log.warning("%s 第 %d 次：%s", self.label, attempt + 1, last_error)
        raise LLMError(f"{self.label} 连续 {attempts} 次没有得到合法 JSON：{last_error}")

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())
