"""OpenAI images API (GPT Image 2.x). Works with any OpenAI-compatible gateway via OPENAI_BASE_URL.

Image requests are never retried automatically.
"""
import httpx

from .. import media
from ..net import call
from .base import ImageProvider, ImageRequest, image_bytes, ping_auth

# True aspect ratios (sides in multiples of 16): GPT Image 2.x takes any size, and the old fixed 1536x1024 was 3:2,
# so a 16:9 keyframe came back cropped differently from the clip it drives.
SIZES = {"16:9": "2048x1152", "9:16": "1152x2048", "1:1": "1536x1536", "4:3": "1536x1152", "3:4": "1152x1536"}


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
        payload = call(http, "POST", url, headers=self.headers(), billed=True, **kw).json()
        return media.fit_ratio(image_bytes(http, payload), req.ratio)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/models", headers=self.headers())
