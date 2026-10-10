"""Request types and provider base classes.

Video providers are asynchronous (submit → poll → download URL); image and speech providers return
bytes from a single call. Every provider estimates its own cost: providers billed through a
subscription plan (Ark Agent Plan) estimate plan credits (AFP) and convert them with the plan's
¥/AFP rate; the others use the unit price in providers.yaml.
"""
import base64
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..errors import ProviderError, Unconfirmed, classify
from ..net import call, error_from_response, make_client


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
    style: str = ""              # delivery in words (tone, pace); used by models steered by prompts
    pronunciations: dict[str, str] = field(default_factory=dict)   # word -> "(tian1)(mu3)", for TTS that takes them


@dataclass
class TaskStatus:
    state: str                   # pending | done | failed | rejected
    url: str | None = None
    message: str = ""
    usage: dict = field(default_factory=dict)


def resolve_base_url(cfg: dict) -> str:
    """Base URL from the env var named by `base_url_env` (e.g. an OpenAI-compatible gateway), else `base_url`."""
    base = os.environ.get(cfg["base_url_env"], "") if cfg.get("base_url_env") else ""
    return (base or cfg.get("base_url", "")).rstrip("/")


def image_bytes(http: httpx.Client, payload: dict) -> bytes:
    """Image from an images-API response (OpenAI and Ark share the shape): base64 inline or a URL."""
    items = payload.get("data") or []
    if items and items[0].get("b64_json"):
        return base64.b64decode(items[0]["b64_json"])
    if items and items[0].get("url"):
        try:
            return call(http, "GET", items[0]["url"]).content
        except ProviderError as e:  # the image exists and was billed; only the download failed
            raise Unconfirmed(f"图片已生成（已扣费）但下载失败：{e}") from e
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    if not err and items and isinstance(items[0].get("error"), dict):
        err = items[0]["error"]
    message = err.get("message") or str(payload)[:300]
    raise classify(err.get("code"), message)(f"{err.get('code') or ''} 没有返回图片：{message}".strip())


def ping_auth(http: httpx.Client, method: str, url: str, **kw) -> str:
    """Probe an endpoint that costs nothing. Any 4xx except 404 (no such object) and 429 (rate
    limited) means the key was not accepted: an invalid Google key, for one, gets a 400."""
    resp = http.request(method, url, **kw)
    if 400 <= resp.status_code < 500 and resp.status_code not in (404, 429):
        raise ProviderError(f"鉴权失败：{error_from_response(resp)}")
    return f"鉴权通过（HTTP {resp.status_code}）"


class Provider:
    kind = ""
    needs_model = True

    def __init__(self, pid: str, cfg: dict, plan: dict | None = None):
        self.id = pid
        self.cfg = cfg
        self.label = cfg.get("label", pid)
        self.model = cfg.get("model") or ""
        self.base_url = resolve_base_url(cfg)
        self.plan_id = cfg.get("plan") or ""
        self.plan = plan or {}

    def client(self, **kw) -> httpx.Client:
        """HTTP client for this provider: through the proxy named by `proxy_env` when set (Google
        endpoints need one in mainland China), otherwise direct as configured by AI_VIDEO_PROXY.
        `timeout_seconds` raises the read timeout for slow synchronous generation: giving up early
        does not stop the server, which may still bill a result that never arrives."""
        if self.cfg.get("timeout_seconds") and "timeout" not in kw:
            kw["timeout"] = httpx.Timeout(float(self.cfg["timeout_seconds"]), connect=15.0)
        return make_client(proxy=self.env("proxy_env") or None, **kw)

    def afp_to_cny(self, afp: float) -> float:
        return afp * float(self.plan.get("cny_per_afp", 0))

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

    def warnings(self) -> list[str]:
        """Config that works but is probably not what was meant (shown by `ai-video check`)."""
        prefix = self.cfg.get("key_prefix")
        if prefix and self.api_key and not self.api_key.startswith(prefix):
            return [f"{self.cfg['key_env']} 不是以 {prefix} 开头的套餐 Key：按量计费的 Key 会扣账户余额"]
        return []

    @property
    def ready(self) -> bool:
        return not self.missing()

    def ping(self, http: httpx.Client) -> str:
        raise NotImplementedError


class VideoProvider(Provider):
    kind = "video"

    def estimate_afp(self, req: VideoRequest) -> float:
        """Plan credits this request is expected to use; 0 for providers not billed in AFP."""
        return 0.0

    def usage_afp(self, usage: dict) -> float:
        """Plan credits actually used, from a finished task's usage report (0 when unknown)."""
        return 0.0

    def estimate(self, req: VideoRequest) -> float:
        afp = self.estimate_afp(req)
        if afp:
            return self.afp_to_cny(afp)
        return float(self.cfg.get("price_cny_per_second", 0)) * req.duration

    def cost(self, req: VideoRequest) -> tuple[float, float]:
        """(¥, AFP) expected for a request; AFP is 0 for providers outside a credit plan."""
        return self.estimate(req), self.estimate_afp(req)

    def settle(self, state: dict) -> tuple[float, float]:
        """(¥, AFP) of a finished task: the provider's usage report when it has one, else the
        estimate stored in the task state at submit time."""
        afp = self.usage_afp(state.get("usage") or {})
        if afp:
            return self.afp_to_cny(afp), afp
        return float(state.get("est_cny", 0.0)), float(state.get("est_afp", 0.0))

    def supports(self, req: VideoRequest) -> str | None:
        """Why this request cannot run here (None when it can)."""
        return None

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        raise NotImplementedError

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        raise NotImplementedError


class ImageProvider(Provider):
    kind = "image"

    def estimate_afp(self, req: ImageRequest) -> float:
        """Plan credits per image (`afp_per_image`), plus `afp_per_extra_ref` for every reference
        image after the first."""
        per_image = float(self.cfg.get("afp_per_image", 0))
        if not per_image:
            return 0.0
        return per_image + float(self.cfg.get("afp_per_extra_ref", 0)) * max(0, len(req.refs) - 1)

    def estimate(self, req: ImageRequest) -> float:
        afp = self.estimate_afp(req)
        if afp:
            return self.afp_to_cny(afp)
        return float(self.cfg.get("price_cny_per_image", 0))

    def cost(self, req: ImageRequest) -> tuple[float, float]:
        return self.estimate(req), self.estimate_afp(req)

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

    def cost(self, req: SpeechRequest) -> tuple[float, float]:
        return self.estimate(req), 0.0

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        raise NotImplementedError
