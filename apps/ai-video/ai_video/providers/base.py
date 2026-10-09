"""Request types and provider base classes.

Video providers are asynchronous (submit → poll → download URL); image and speech providers return
bytes from a single call. Every provider estimates its own cost from the unit price in providers.yaml.
"""
import base64
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..errors import ProviderError, classify
from ..net import call


@dataclass
class VideoRequest:
    prompt: str
    duration: int = 5
    ratio: str = "16:9"          # used for text-to-video; image-driven requests follow the image
    first_frame: Path | None = None
    last_frame: Path | None = None
    audio: bool = True
    seed: int | None = None

    @property
    def mode(self) -> str:
        if self.first_frame and self.last_frame:
            return "flf2v"
        return "i2v" if self.first_frame else "t2v"


@dataclass
class ImageRequest:
    prompt: str
    ratio: str = "16:9"
    refs: list[Path] = field(default_factory=list)
    seed: int | None = None


@dataclass
class SpeechRequest:
    text: str
    voice: str
    speed: float = 1.0
    emotion: str | None = None


@dataclass
class TaskStatus:
    state: str                   # pending | done | failed | rejected
    url: str | None = None
    message: str = ""
    usage: dict = field(default_factory=dict)


def resolve_base_url(cfg: dict) -> str:
    """Base URL from the env var named by `base_url_env` (e.g. a relay address), else `base_url`,
    plus an optional `base_path`: a relay such as 302.AI serves each vendor's native API under a
    prefix (/doubao, /klingai, /minimaxi, /v1)."""
    base = os.environ.get(cfg["base_url_env"], "") if cfg.get("base_url_env") else ""
    return (base or cfg.get("base_url", "")).rstrip("/") + cfg.get("base_path", "")


def image_bytes(http: httpx.Client, payload: dict) -> bytes:
    """Image from an images-API response (OpenAI and Ark share the shape): base64 inline or a URL."""
    items = payload.get("data") or []
    if items and items[0].get("b64_json"):
        return base64.b64decode(items[0]["b64_json"])
    if items and items[0].get("url"):
        return call(http, "GET", items[0]["url"]).content
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    if not err and items and isinstance(items[0].get("error"), dict):
        err = items[0]["error"]
    message = err.get("message") or str(payload)[:300]
    raise classify(err.get("code"), message)(f"{err.get('code') or ''} 没有返回图片：{message}".strip())


def ping_auth(http: httpx.Client, method: str, url: str, **kw) -> str:
    """Probe an endpoint that costs nothing; only 401/403 means the key is wrong."""
    resp = http.request(method, url, **kw)
    if resp.status_code in (401, 403):
        raise ProviderError(f"鉴权失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    return f"鉴权通过（HTTP {resp.status_code}）"


class Provider:
    kind = ""
    needs_model = True

    def __init__(self, pid: str, cfg: dict):
        self.id = pid
        self.cfg = cfg
        self.label = cfg.get("label", pid)
        self.model = cfg.get("model") or ""
        self.base_url = resolve_base_url(cfg)

    def env(self, field_name: str) -> str:
        name = self.cfg.get(field_name)
        return os.environ.get(name, "") if name else ""

    @property
    def api_key(self) -> str:
        return self.env("key_env")

    def missing(self) -> list[str]:
        """Config problems that keep this provider from running; empty when ready."""
        out = []
        if self.cfg.get("enabled") is False:
            out.append("providers.yaml 里设置了 enabled: false")
        if self.needs_model and not self.model:
            out.append("providers.yaml 里没填 model")
        if self.cfg.get("key_env") and not self.api_key:
            out.append(f".env 里没有 {self.cfg['key_env']}")
        return out

    @property
    def ready(self) -> bool:
        return not self.missing()

    def ping(self, http: httpx.Client) -> str:
        raise NotImplementedError


class VideoProvider(Provider):
    kind = "video"

    def estimate(self, req: VideoRequest) -> float:
        return float(self.cfg.get("price_cny_per_second", 0)) * req.duration

    def supports(self, req: VideoRequest) -> str | None:
        """Why this request cannot run here (None when it can)."""
        return None

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        raise NotImplementedError

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        raise NotImplementedError


class ImageProvider(Provider):
    kind = "image"

    def estimate(self, req: ImageRequest) -> float:
        return float(self.cfg.get("price_cny_per_image", 0))

    def generate(self, http: httpx.Client, req: ImageRequest) -> bytes:
        raise NotImplementedError


class SpeechProvider(Provider):
    kind = "tts"

    @property
    def voices(self) -> list[str]:
        return list(self.cfg.get("voices") or [])

    def missing(self) -> list[str]:
        out = super().missing()
        if not self.voices:
            out.append("providers.yaml 里没填 voices")
        return out

    def estimate(self, req: SpeechRequest) -> float:
        return float(self.cfg.get("price_cny_per_10k_chars", 0)) * len(req.text) / 10_000

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        raise NotImplementedError
