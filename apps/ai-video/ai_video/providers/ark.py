"""Volcengine Ark: Seedance video tasks and Seedream images, both authorised with ARK_API_KEY.

Field names follow the official volcengine-python-sdk (volcenginesdkarkruntime):
POST /contents/generations/tasks, GET /contents/generations/tasks/{id}, POST /images/generations.
"""
import httpx

from .. import media
from ..errors import Rejected, classify
from ..net import call
from .base import (ImageProvider, ImageRequest, TaskStatus, VideoProvider, VideoRequest, image_bytes, ping_auth,
                   resolve_base_url)
from .relay_async import relay_async

PENDING = {"queued", "running"}

IMAGE_SIZES = {
    "16:9": "2560x1440", "9:16": "1440x2560", "1:1": "2048x2048",
    "4:3": "2304x1728", "3:4": "1728x2304", "21:9": "3024x1296",
}


def media_content(req: VideoRequest) -> list[dict]:
    """Content array shared by Ark and MiniMax V2: prompt text plus first/last frame images."""
    content = [{"type": "text", "text": req.prompt}]
    for role, path in (("first_frame", req.first_frame), ("last_frame", req.last_frame)):
        if path:
            content.append({"type": "image_url", "image_url": {"url": media.data_url(path)}, "role": role})
    return content


class ArkVideo(VideoProvider):
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def supports(self, req: VideoRequest) -> str | None:
        if not 4 <= req.duration <= 15:
            return "Seedance 只支持 4–15 秒"
        return None

    def build_body(self, req: VideoRequest) -> dict:
        body = {
            "model": self.model,
            "content": media_content(req),
            "duration": req.duration,
            "ratio": req.ratio if req.mode == "t2v" else "adaptive",
            "generate_audio": req.audio,
            "watermark": False,
        }
        if self.cfg.get("resolution"):
            body["resolution"] = self.cfg["resolution"]
        if req.seed is not None:
            body["seed"] = req.seed
        return body

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        data = call(http, "POST", f"{self.base_url}/contents/generations/tasks",
                    json=self.build_body(req), headers=self.headers()).json()
        return data["id"]

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        data = call(http, "GET", f"{self.base_url}/contents/generations/tasks/{task_id}", headers=self.headers()).json()
        status = data.get("status", "")
        if status in PENDING:
            return TaskStatus("pending")
        if status == "succeeded":
            return TaskStatus("done", url=(data.get("content") or {}).get("video_url"), usage=data.get("usage") or {})
        err = data.get("error") or {}
        state = "rejected" if classify(err.get("code"), err.get("message")) is Rejected else "failed"
        return TaskStatus(state, message=f"{err.get('code') or status}: {err.get('message') or ''}")

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/contents/generations/tasks",
                         params={"page_num": 1, "page_size": 1}, headers=self.headers())


class ArkImage(ImageProvider):
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate(self, http: httpx.Client, req: ImageRequest) -> bytes:
        body = {
            "model": self.model,
            "prompt": req.prompt,
            "size": IMAGE_SIZES.get(req.ratio, "2K"),
            "response_format": "b64_json",
            "watermark": False,
            "sequential_image_generation": "disabled",
        }
        if req.refs:
            urls = [media.data_url(p) for p in req.refs]
            body["image"] = urls[0] if len(urls) == 1 else urls
        if req.seed is not None:
            body["seed"] = req.seed
        url = f"{self.base_url}/images/generations"
        if self.cfg.get("async"):
            # Reference-image requests can outlast a relay gateway's timeout; see relay_async.
            root = resolve_base_url({**self.cfg, "base_path": ""})
            payload = relay_async(http, "POST", url, root=root, headers=self.headers(), json=body)
        else:
            payload = call(http, "POST", url, json=body, headers=self.headers(), retries=0).json()
        return image_bytes(http, payload)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/contents/generations/tasks",
                         params={"page_num": 1, "page_size": 1}, headers=self.headers())
