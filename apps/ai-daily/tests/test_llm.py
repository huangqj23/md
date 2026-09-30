"""两个后端的真实请求形状：用 httpx2.MockTransport 截获 SDK 发出的请求（openai 3.x / anthropic 1.x 都基于 httpx2）。"""
import json

import httpx2
import pytest

from ai_daily.llm import LLMError, LLMPair, OpenAICompatLLM, parse_json_object
from ai_daily.llm_anthropic import FALLBACK_BETA, AnthropicLLM


def chat_reply(content, finish="stop", usage=(12, 7)):
    return {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}}


def bad_request(message):
    return httpx2.Response(400, json={"error": {"message": message, "type": "invalid_request_error"}})


class Recorder:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request):
        self.requests.append(request)
        r = self.responses.pop(0)
        return r if isinstance(r, httpx2.Response) else httpx2.Response(200, json=r)

    def body(self, i):
        return json.loads(self.requests[i].content)


def openai_llm(recorder, **kw):
    return OpenAICompatLLM("https://api.example.com/v1", "sk-test", "m-1", label="Example · m-1", max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)), **kw)


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('好的：{"a": {"b": 2}} 以上') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_openai_compat_request_shape_and_usage():
    rec = Recorder(chat_reply('```json\n{"events": []}\n```'))
    llm = openai_llm(rec)
    assert llm.json("系统，只输出 json", "用户") == {"events": []}
    body = rec.body(0)
    assert body["model"] == "m-1" and body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 8000 and body["temperature"] == 0.3
    assert body["messages"][0] == {"role": "system", "content": "系统，只输出 json"}
    assert rec.requests[0].headers["authorization"] == "Bearer sk-test"
    assert llm.usage == {"calls": 1, "input": 12, "output": 7}


def test_openai_compat_adapts_unsupported_params_and_remembers():
    rec = Recorder(
        bad_request("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."),
        bad_request("Unsupported value: 'temperature' does not support 0.3 with this model."),
        bad_request("response_format type json_object is not supported"),
        chat_reply('{"ok": true}'),
        chat_reply('{"ok": true}'),
    )
    llm = openai_llm(rec)
    assert llm.json("json", "u") == {"ok": True}
    last = rec.body(3)
    assert "max_tokens" not in last and last["max_completion_tokens"] == 8000
    assert "temperature" not in last and "response_format" not in last
    llm.json("json", "u")                                     # 第二次直接用调整后的参数
    assert len(rec.requests) == 5 and "max_completion_tokens" in rec.body(4)


def test_openai_compat_retries_empty_and_invalid_json_then_fails():
    rec = Recorder(chat_reply(""), chat_reply("不是 json"), chat_reply('{"ok": 1}'))
    assert openai_llm(rec).json("json", "u") == {"ok": 1}
    followup = rec.body(2)["messages"]
    assert followup[-2] == {"role": "assistant", "content": "不是 json"} and "合法的 json" in followup[-1]["content"]

    rec = Recorder(chat_reply("x"), chat_reply("y"), chat_reply("z"))
    with pytest.raises(LLMError, match="连续 3 次"):
        openai_llm(rec).json("json", "u")


def test_openai_compat_auth_error_is_not_retried():
    rec = Recorder(httpx2.Response(401, json={"error": {"message": "invalid api key"}}))
    with pytest.raises(LLMError, match="请求被拒"):
        openai_llm(rec).json("json", "u")
    assert len(rec.requests) == 1


def test_openai_compat_list_models():
    rec = Recorder({"object": "list", "data": [{"id": "b", "object": "model", "created": 1, "owned_by": "x"},
                                               {"id": "a", "object": "model", "created": 1, "owned_by": "x"}]})
    assert openai_llm(rec).list_models() == ["a", "b"]
    assert rec.requests[0].url.path == "/v1/models"


# ---------------------------------------------------------------- Claude

def claude_reply(text='{"ok": true}', stop="end_turn", content=None, stop_details=None, model="claude-opus-5"):
    body = {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
            "content": content if content is not None else [
                {"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 30, "output_tokens": 9}}
    if stop_details:
        body["stop_details"] = stop_details
    return body


def claude_llm(recorder, model="claude-opus-5"):
    import anthropic
    client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(recorder))
    return AnthropicLLM("sk-ant-test", model, label=f"Claude · {model}", max_retries=0, http_client=client)


def test_claude_opus_uses_default_fallbacks_and_reads_text_blocks():
    rec = Recorder(claude_reply('```json\n{"ok": true}\n```'))
    llm = claude_llm(rec)
    assert llm.json("只输出 json", "u", max_tokens=3000) == {"ok": True}
    req = rec.requests[0]
    body = json.loads(req.content)
    assert FALLBACK_BETA in req.headers["anthropic-beta"]
    assert body["fallbacks"] == "default" and body["model"] == "claude-opus-5"
    assert body["max_tokens"] >= 16000 and "temperature" not in body            # 思考 token 计入 max_tokens
    assert body["system"] == "只输出 json" and body["messages"] == [{"role": "user", "content": "u"}]
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert llm.usage == {"calls": 1, "input": 30, "output": 9}


def test_claude_models_without_fallback_support_use_plain_messages():
    rec = Recorder(claude_reply(model="claude-haiku-4-5"))
    claude_llm(rec, "claude-haiku-4-5").json("json", "u")
    body = json.loads(rec.requests[0].content)
    assert "fallbacks" not in body and "anthropic-beta" not in rec.requests[0].headers


def test_claude_refusal_is_reported_not_parsed():
    rec = Recorder(claude_reply(stop="refusal", content=[],
                                stop_details={"type": "refusal", "category": "cyber", "explanation": "x"}))
    with pytest.raises(LLMError, match="拒绝了这个请求（类别：cyber）"):
        claude_llm(rec).json("json", "u")
    assert len(rec.requests) == 1


def test_claude_list_models():
    rec = Recorder({"data": [{"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5",
                              "created_at": "2026-01-01T00:00:00Z"}],
                    "has_more": False, "first_id": "claude-opus-5", "last_id": "claude-opus-5"})
    assert claude_llm(rec).list_models() == ["claude-opus-5"]


def test_pair_usage_summary_dedupes_shared_client():
    rec = Recorder(chat_reply('{"a": 1}'))
    llm = openai_llm(rec)
    llm.json("json", "u")
    assert LLMPair(llm, llm).usage_summary() == "Example · m-1 调用 1 次，输入 12 / 输出 7 token"
