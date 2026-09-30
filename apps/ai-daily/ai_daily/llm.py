"""LLM 调用：统一的 .json(system, user) 接口，每个实例绑定一个厂商 + 模型。

这里是 OpenAI 兼容接口（DeepSeek、通义、Kimi、智谱、OpenRouter、OpenAI、Gemini、xAI、本机 vLLM/Ollama 等）；
Claude 走官方 SDK，见 llm_anthropic.py。按角色（选题 / 写稿）组合成 LLMPair 交给流水线。
"""
import json
import logging
import re
import threading
from dataclasses import dataclass

import openai
from openai import OpenAI

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


MAX_BUDGET = 64000          # 截断后翻倍的上限

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json_object(text: str) -> dict:
    """容忍 ```json 代码块和前后的说明文字，取第一个 { 到最后一个 } 之间的内容。"""
    text = _FENCE.sub("", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("没有找到 json 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("输出不是 json 对象")
    return data


class _Usage:
    def __init__(self):
        self.usage = {"calls": 0, "input": 0, "output": 0}
        self._lock = threading.Lock()

    def _count(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.usage["calls"] += 1
            self.usage["input"] += input_tokens or 0
            self.usage["output"] += output_tokens or 0


class OpenAICompatLLM(_Usage):
    def __init__(self, base_url: str, api_key: str, model: str, *, label: str = "", json_mode: bool = True,
                 token_param: str = "max_tokens", temperature: bool = True, extra_body: dict | None = None,
                 timeout: float = 180, max_retries: int = 3, http_client=None):
        super().__init__()
        kw = {"base_url": base_url, "api_key": api_key or "not-needed", "timeout": timeout, "max_retries": max_retries}
        if http_client is not None:
            kw["http_client"] = http_client
        self.client = OpenAI(**kw)
        self.model = model
        self.label = label or model
        self.json_mode = json_mode
        self.token_param = token_param
        self.use_temperature = temperature
        self.extra_body = extra_body or {}

    def _create(self, messages: list, max_tokens: int, temperature: float):
        """有的兼容接口不认 response_format / max_tokens / temperature：按 400 的报错调整参数重试，并记住调整。"""
        for _ in range(4):
            kw = {"model": self.model, "messages": messages, self.token_param: max_tokens}
            if self.json_mode:
                kw["response_format"] = {"type": "json_object"}
            if self.use_temperature:
                kw["temperature"] = temperature
            if self.extra_body:
                kw["extra_body"] = self.extra_body
            try:
                return self.client.chat.completions.create(**kw)
            except openai.BadRequestError as e:
                msg = str(e).lower()
                if self.json_mode and "response_format" in msg:
                    self.json_mode = False
                elif self.token_param == "max_tokens" and "max_completion_tokens" in msg:
                    self.token_param = "max_completion_tokens"
                elif self.use_temperature and "temperature" in msg:
                    self.use_temperature = False
                else:
                    raise
                log.info("%s 不支持某个参数，已调整后重试：%s", self.label, str(e)[:160])
        raise LLMError(f"{self.label} 调整参数后仍然被拒绝")

    def json(self, system: str, user: str, *, max_tokens: int = 8000, temperature: float = 0.3,
             attempts: int = 3) -> dict:
        """prompt 里要出现 json 字样和示例（DeepSeek 等的 JSON 模式要求）。

        - 因长度截断（finish_reason=length）：推理模型的思考 token 也算在上限里，常见的表现是空回复或半截 JSON。
          把上限翻倍、用原始消息重试，而不是把半截输出喂回去。
        - 其他解析失败：把输出和错误告诉模型，让它重写。"""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        budget, last_error = max_tokens, ""
        for attempt in range(attempts):
            try:
                resp = self._create(messages, budget, temperature)
            except (openai.BadRequestError, openai.AuthenticationError, openai.PermissionDeniedError,
                    openai.NotFoundError) as e:
                raise LLMError(f"{self.label} 请求被拒：{e}") from e      # key、模型名或参数有问题，重试没用
            except openai.APIError as e:
                last_error = f"{type(e).__name__}: {e}"
                log.warning("%s 调用失败（第 %d 次）：%s", self.label, attempt + 1, last_error)
                continue
            if resp.usage is not None:
                self._count(resp.usage.prompt_tokens, resp.usage.completion_tokens)
            if not resp.choices:
                last_error = "回复里没有 choices"
                continue
            choice = resp.choices[0]
            if choice.finish_reason == "content_filter":
                raise LLMError(f"{self.label} 触发内容审核，这条需要人工处理")
            content = (choice.message.content or "").strip()
            truncated = choice.finish_reason == "length"
            if content:
                try:
                    return parse_json_object(content)
                except ValueError as e:
                    last_error = f"JSON 解析失败：{e}"
            else:
                last_error = f"空回复（finish_reason={choice.finish_reason}）"
            if truncated:
                budget = min(budget * 2, MAX_BUDGET)
                last_error += f"（输出被长度上限截断，上限提到 {budget} 再试）"
            elif content:
                messages = messages[:2] + [{"role": "assistant", "content": content},
                                           {"role": "user", "content": f"上次的输出有问题：{last_error}。请只输出一个合法的 json 对象。"}]
            log.warning("%s 第 %d 次：%s", self.label, attempt + 1, last_error)
        raise LLMError(f"{self.label} 连续 {attempts} 次没有得到合法 JSON：{last_error}")

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list())


@dataclass
class LLMPair:
    """流水线里两个角色各用一个模型：选题（输入长，用便宜的）和写稿（质量优先）。"""
    triage: object
    write: object

    def usage_summary(self) -> str:
        parts, seen = [], set()
        for llm in (self.triage, self.write):
            if id(llm) in seen:
                continue
            seen.add(id(llm))
            u = llm.usage
            parts.append(f"{llm.label} 调用 {u['calls']} 次，输入 {u['input']:,} / 输出 {u['output']:,} token")
        return "；".join(parts)
