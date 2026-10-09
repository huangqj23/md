"""302.AI relay: its unified video API, its OpenAI-style speech endpoint, and the listings used to
fill model names and voice ids into providers.yaml.

Vendors whose native API 302.AI passes through (Seedance under /volcengine/api/v3, Seedream under
/doubao, Kling under /klingai, MiniMax H3 under /minimaxi, GPT Image under /v1) reuse the direct
adapters with a relay base URL; this module covers models only offered in the unified format
(e.g. HappyHorse 1.0, Wan 2.7, Vidu). The public listing at /302/v2/model/video gives each model's
name, price and accepted parameters.
"""
import json

import httpx

from .. import media
from ..errors import Rejected, classify
from ..net import call
from .base import SpeechProvider, SpeechRequest, TaskStatus, VideoProvider, VideoRequest, ping_auth

PENDING = {"pending", "processing", "queued", "running", "submitted"}
DONE = {"completed", "succeeded", "success"}


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


class Relay302Video(VideoProvider):
    """POST /302/v2/video/create, GET /302/v2/video/fetch/{id}. The service treats a request with
    `image` as image-to-video and one without as text-to-video; `end_image` is the last frame.

    Some models are listed once per mode (happyhorse-1.0-t2v / -i2v), configured as
    models_by_mode; `end_image: false` marks models without last-frame support."""

    def model_for(self, req: VideoRequest) -> str | None:
        by_mode = self.cfg.get("models_by_mode") or {}
        if not by_mode:
            return self.model
        return by_mode.get("t2v" if req.mode == "t2v" else "i2v")

    def supports(self, req: VideoRequest) -> str | None:
        low, high = self.cfg.get("durations") or (1, 60)
        if not low <= req.duration <= high:
            return f"{self.label} 只支持 {low}–{high} 秒"
        if req.mode == "flf2v" and self.cfg.get("end_image") is False:
            return f"{self.label} 不支持尾帧，长镜头用例跳过"
        if not self.model_for(req):
            return f"{self.label} 没有配置{'文生视频' if req.mode == 't2v' else '图生视频'}的模型名"
        return None

    def build_body(self, req: VideoRequest) -> dict:
        body = {"model": self.model_for(req), "prompt": req.prompt, "duration": req.duration}
        if req.first_frame:
            body["image"] = media.data_url(req.first_frame)
        else:
            body["aspect_ratio"] = req.ratio
        if req.last_frame:
            body["end_image"] = media.data_url(req.last_frame)
        if self.cfg.get("resolution"):
            body["resolution"] = self.cfg["resolution"]
        # The unified format has no common audio switch; the model's own field name is configured.
        if self.cfg.get("audio_field"):
            body[self.cfg["audio_field"]] = req.audio
        body.update(self.cfg.get("extra") or {})
        return body

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        data = call(http, "POST", f"{self.base_url}/302/v2/video/create", json=self.build_body(req),
                    headers=_headers(self.api_key)).json()
        if data.get("task_id") and data.get("status") != "failed":
            return str(data["task_id"])
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = err.get("message_cn") or err.get("message") or str(data)[:300]
        raise classify(err.get("err_code") or err.get("code"), message)(f"提交失败：{message}")

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        data = call(http, "GET", f"{self.base_url}/302/v2/video/fetch/{task_id}", headers=_headers(self.api_key)).json()
        status = str(data.get("status") or "").lower()
        if status in PENDING:
            return TaskStatus("pending")
        if status in DONE:
            return TaskStatus("done", url=data.get("video_url"),
                              usage={"execution_time": data.get("execution_time"), "model": data.get("model")})
        raw = data.get("raw_response") or data.get("error") or data
        message = (raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))[:300]
        return TaskStatus("rejected" if classify("", message) is Rejected else "failed", message=message)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/302/v2/model/video", headers=_headers(self.api_key))


class Relay302TTS(SpeechProvider):
    """POST /302/tts/generate (synchronous, returns an audio URL). `provider` is the TTS vendor
    behind the relay (doubao, minimaxi, …); `model` is the vendor's model where it has several
    (minimaxi: speech-2.8-hd) and is left empty otherwise (doubao). Voice ids per vendor come from
    `ai-video relay-models --tts <provider>`."""

    needs_model = False

    def missing(self) -> list[str]:
        out = super().missing()
        if not self.cfg.get("provider"):
            out.append("providers.yaml 里没填 provider")
        return out

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        body = {"text": req.text, "provider": self.cfg["provider"], "voice": req.voice, "speed": req.speed,
                "output_format": "mp3"}
        if self.model:
            body["model"] = self.model
        if req.emotion:
            body["emotion"] = req.emotion
        data = call(http, "POST", f"{self.base_url}/302/tts/generate", json=body, headers=_headers(self.api_key)).json()
        if data.get("audio_url"):
            return call(http, "GET", data["audio_url"]).content
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = err.get("message_cn") or err.get("message") or str(data)[:300]
        raise classify(err.get("err_code"), message)(f"合成失败：{message}")

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/302/tts/provider", params={"provider": self.cfg.get("provider")},
                         headers=_headers(self.api_key))


def video_models(http: httpx.Client, base_url: str, key: str = ""):
    return call(http, "GET", f"{base_url}/302/v2/model/video", headers=_headers(key) if key else {}).json()


def tts_providers(http: httpx.Client, base_url: str, key: str, providers: list[str]) -> list[dict]:
    params = [("provider", p) for p in providers]
    data = call(http, "GET", f"{base_url}/302/tts/provider", params=params, headers=_headers(key)).json()
    return data.get("provider_list") or []


def model_names(obj) -> list[str]:
    """Collect model names from the model listing without assuming its exact layout."""
    names: list[str] = []

    def add(name: str) -> None:
        if name not in names:
            names.append(name)

    def walk(node):
        if isinstance(node, dict):
            for key in ("model", "model_name", "name", "id"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    add(value)
                    return  # the rest of a model entry describes that model, not other models
            for key, value in node.items():
                # {"kling-v3": {...}, "MiniMax-H3": {...}}: model names used as keys.
                if isinstance(value, dict) and ("-" in key or "." in key):
                    add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str):
                    add(item)
                else:
                    walk(item)

    walk(obj)
    return names
