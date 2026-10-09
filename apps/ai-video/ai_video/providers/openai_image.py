"""OpenAI images API (GPT Image 2). Works with any OpenAI-compatible relay via OPENAI_BASE_URL.

GPT Image 2 often takes longer than a relay gateway's ~100 s timeout, so `async: true` uses
302.AI's async mode (see relay_async). Image requests are never retried automatically.
"""
import httpx

from .. import media
from ..net import call
from .base import ImageProvider, ImageRequest, image_bytes, ping_auth, resolve_base_url
from .relay_async import relay_async

SIZES = {"16:9": "1536x1024", "9:16": "1024x1536", "1:1": "1024x1024", "4:3": "1536x1024", "3:4": "1024x1536"}


class OpenAIImage(ImageProvider):
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate(self, http: httpx.Client, req: ImageRequest) -> bytes:
        fields = {"model": self.model, "prompt": req.prompt, "size": SIZES.get(req.ratio, "auto"),
                  "quality": self.cfg.get("quality", "medium")}
        if req.refs:
            files = [("image[]", (f"{p.stem}.jpg", media.encode_jpeg(p), "image/jpeg")) for p in req.refs]
            url, kw = f"{self.base_url}/images/edits", {"data": fields, "files": files}
        else:
            url, kw = f"{self.base_url}/images/generations", {"json": {**fields, "n": 1}}
        if self.cfg.get("async"):
            root = resolve_base_url({**self.cfg, "base_path": ""})
            payload = relay_async(http, "POST", url, root=root, headers=self.headers(), **kw)
        else:
            payload = call(http, "POST", url, headers=self.headers(), retries=0, **kw).json()
        return image_bytes(http, payload)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/models", headers=self.headers())
