"""Minimal OpenAI-compatible chat call for the LLM blind test.

Streams the reply: reasoning models can think for minutes before the first token, and a relay
gateway drops a silent connection after ~100 s (HTTP 520/524). Streaming keeps bytes flowing.
"""
import json

import httpx

from .errors import ProviderError
from .net import error_from_response


def chat(http: httpx.Client, base_url: str, api_key: str, model: str, prompt: str) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}
    parts: list[str] = []
    with http.stream("POST", f"{base_url.rstrip('/')}/chat/completions", json=body,
                     headers={"Authorization": f"Bearer {api_key}"},
                     timeout=httpx.Timeout(600.0, connect=15.0)) as resp:
        if resp.status_code >= 400:
            resp.read()
            raise error_from_response(resp)
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("error"):
                raise ProviderError(str(chunk["error"])[:300])
            for choice in chunk.get("choices") or []:
                # Only the answer; reasoning models also stream `reasoning_content`, which is skipped.
                parts.append((choice.get("delta") or {}).get("content") or "")
    text = "".join(parts).strip()
    if not text:
        raise ProviderError("模型没有返回内容")
    return text
