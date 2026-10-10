"""Gemini API through the Interactions endpoint: speech (Gemini Flash TTS) and images (Nano Banana).

Both answer with base64 data inside the interaction's model-output steps. Google endpoints are not
reachable directly from mainland China, so these providers usually set `proxy_env` (see
Provider.client). The key goes in the x-goog-api-key header, which accepts both the old AIza… and the
newer AQ.… key formats.
"""
import base64

import httpx

from .. import media
from ..errors import ProviderError, classify
from ..net import call, error_from_response
from .base import ImageProvider, ImageRequest, Provider, SpeechProvider, SpeechRequest


def output_items(payload: dict, kind: str) -> list[dict]:
    """Content items of one type ("audio" / "image") with data, from the convenience field
    `output_<kind>` or from the model-output steps."""
    items = []
    top = payload.get(f"output_{kind}")
    if isinstance(top, dict) and top.get("data"):
        items.append(top)
    for step in payload.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if isinstance(item, dict) and item.get("type") == kind and item.get("data"):
                items.append(item)
    return items


def no_output_error(payload: dict, what: str) -> ProviderError:
    """Explain an interaction that finished without the expected output (often a safety block)."""
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    reason = err.get("message") or payload.get("status") or ""
    message = f"没有返回{what}：{reason or str(payload)[:300]}"
    return classify(err.get("code") or err.get("status"), f"{reason} {str(payload)[:300]}")(message)


class GeminiMixin(Provider):
    def headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    def interact(self, http: httpx.Client, body: dict) -> dict:
        return call(http, "POST", f"{self.base_url}/interactions", json=body, headers=self.headers(),
                    billed=True).json()

    def warnings(self) -> list[str]:
        out = super().warnings()
        if self.cfg.get("proxy_env") and not self.env("proxy_env"):
            out.append(f".env 里没有 {self.cfg['proxy_env']}：国内网络通常连不上 Google 接口")
        return out

    def ping(self, http: httpx.Client) -> str:
        """Listing models is free; anything but HTTP 200 means the key does not work."""
        resp = http.get(f"{self.base_url}/models", params={"pageSize": 1}, headers=self.headers())
        if resp.status_code == 200:
            return "鉴权通过（HTTP 200）"
        err = error_from_response(resp)
        hint = ("：Google 不认这个 Key（多半是复制时少了或认错了字符），请在 AI Studio 点「复制密钥」后重新粘贴"
                if "ACCESS_TOKEN_TYPE_UNSUPPORTED" in str(err) or "API_KEY_INVALID" in str(err) else "")
        raise ProviderError(f"鉴权失败：{err}{hint}")


class GeminiTTS(GeminiMixin, SpeechProvider):
    def build_body(self, req: SpeechRequest) -> dict:
        text = {"type": "text", "text": req.text}
        if req.style:
            # Delivery directions go here; the text itself is read verbatim.
            text["annotations"] = [{"type": "speech_metadata", "style": req.style}]
        return {
            "model": self.model,
            "input": [{"type": "user_input", "content": [text]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav"},
            "generation_config": {"speech_config": [{"voice": req.voice}]},
        }

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        payload = self.interact(http, self.build_body(req))
        items = output_items(payload, "audio")
        if not items:
            raise no_output_error(payload, "音频")
        return media.to_mp3(base64.b64decode(items[-1]["data"]))


class GeminiImage(GeminiMixin, ImageProvider):
    def build_body(self, req: ImageRequest) -> dict:
        parts = [{"type": "text", "text": req.prompt}]
        parts += [{"type": "image", "mime_type": "image/jpeg", "data": media.raw_b64(p)} for p in req.refs]
        body = {
            "model": self.model,
            "input": parts,
            "response_format": {"type": "image", "mime_type": "image/png", "aspect_ratio": req.ratio,
                                "image_size": self.cfg.get("image_size", "2K")},
        }
        if self.cfg.get("thinking_level"):
            body["generation_config"] = {"thinking_level": self.cfg["thinking_level"]}
        return body

    def generate(self, http: httpx.Client, req: ImageRequest) -> bytes:
        payload = self.interact(http, self.build_body(req))
        items = output_items(payload, "image")
        if not items:
            raise no_output_error(payload, "图片")
        return base64.b64decode(items[-1]["data"])
