"""Doubao speech synthesis (Volcengine), V3 unidirectional HTTP stream.

The response is a stream of JSON lines, each carrying a base64 audio chunk in `data`; the last line
has code 20000000. `model` in providers.yaml is sent as the X-Api-Resource-Id header. Field names
follow the public V3 docs as of 2026 and have not been checked against a live key yet.
"""
import base64
import json
import uuid

import httpx

from ..errors import ProviderError, classify
from ..net import error_from_response
from .base import SpeechProvider, SpeechRequest

FINISHED = 20000000


class DoubaoTTS(SpeechProvider):
    def missing(self) -> list[str]:
        out = super().missing()
        if self.cfg.get("app_id_env") and not self.env("app_id_env"):
            out.append(f".env 里没有 {self.cfg['app_id_env']}")
        return out

    def headers(self) -> dict:
        return {
            "X-Api-App-Id": self.env("app_id_env"),
            "X-Api-Access-Key": self.api_key,
            "X-Api-Resource-Id": self.model,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    def build_body(self, req: SpeechRequest) -> dict:
        # speech_rate runs from -50 to 100 with 0 as normal speed, i.e. percent change.
        rate = max(-50, min(100, round((req.speed - 1.0) * 100)))
        return {
            "user": {"uid": "ai-video"},
            "req_params": {
                "text": req.text,
                "speaker": req.voice,
                "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": rate},
            },
        }

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        chunks = []
        with http.stream("POST", self.base_url, json=self.build_body(req), headers=self.headers()) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise error_from_response(resp)
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                code = msg.get("code", 0)
                if code not in (0, FINISHED):
                    raise classify(code, msg.get("message"))(f"{code}: {msg.get('message')}")
                if msg.get("data"):
                    chunks.append(base64.b64decode(msg["data"]))
        if not chunks:
            raise ProviderError("没有返回音频")
        return b"".join(chunks)

    def ping(self, http: httpx.Client) -> str:
        self.synthesize(http, SpeechRequest(text="测试", voice=self.voices[0]))
        return "合成成功"
