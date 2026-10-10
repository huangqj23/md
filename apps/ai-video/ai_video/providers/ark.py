"""Volcengine Ark: Seedance video tasks and Seedream images.

Field names follow the official volcengine-python-sdk (volcenginesdkarkruntime):
POST /contents/generations/tasks, GET /contents/generations/tasks/{id}, POST /images/generations.
Through the Agent Plan these live under https://ark.cn-beijing.volces.com/api/plan/v3 and need the
plan's own API key; usage is charged in plan credits (AFP).
"""
import re

import httpx

from .. import media
from ..errors import ProviderError, Rejected, classify
from ..net import call
from .base import ImageProvider, ImageRequest, TaskStatus, VideoProvider, VideoRequest, image_bytes, ping_auth

PENDING = {"queued", "running"}

# Output frame sizes of the Seedance 2.0 series (the video API reference). Video tokens are
# frames × width × height / 1024 with frames = 24 × seconds + 1 (a 5 s 720p clip reports
# 121 × 1280 × 720 / 1024 = 108,900 completion tokens), and AFP = tokens / 10,000 × the model's coefficient.
VIDEO_PIXELS = {
    "480p": {"16:9": (864, 496), "9:16": (496, 864), "4:3": (752, 560), "3:4": (560, 752), "1:1": (640, 640),
             "21:9": (992, 432)},
    "720p": {"16:9": (1280, 720), "9:16": (720, 1280), "4:3": (1112, 834), "3:4": (834, 1112), "1:1": (960, 960),
             "21:9": (1470, 630)},
    "1080p": {"16:9": (1920, 1080), "9:16": (1080, 1920), "4:3": (1664, 1248), "3:4": (1248, 1664),
              "1:1": (1440, 1440), "21:9": (2206, 946)},
}
# Seedance 2.5 differs from the 2.0 series only at 480p (verified: a 9:16 480p clip is 480 × 854).
VIDEO_PIXELS_25 = {
    **VIDEO_PIXELS,
    "480p": {"16:9": (854, 480), "9:16": (480, 854), "4:3": (752, 560), "3:4": (560, 752), "1:1": (640, 640),
             "21:9": (992, 432)},
}
VIDEO_FPS = 24
SEEDANCE_25 = re.compile(r"seedance-2[.-]5(?!\d)")
LOW_RES_ONLY = re.compile(r"seedance-2[.-]0-(fast|mini)")   # these offer 480p and 720p only

# Seedream 5.0 Pro bills 150 AFP for a 1.5K image and 300 AFP above. The plan documents the limit
# as 2.61 MP, other sources as 1536² = 2,359,296 px; 16:9, 9:16 and 1:1 sit exactly at the lower
# figure and the others below it. The final video is 1080p, so nothing is lost.
IMAGE_SIZES = {
    "16:9": "2048x1152", "9:16": "1152x2048", "1:1": "1536x1536",
    "4:3": "1728x1296", "3:4": "1296x1728", "21:9": "2240x960",
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

    def missing(self) -> list[str]:
        out = super().missing()
        if self.plan and not self.coef():
            out.append("providers.yaml 里没填 afp_coef（套餐抵扣系数，可以按分辨率分别填）")
        resolution = self.cfg.get("resolution", "720p")
        if resolution not in VIDEO_PIXELS:
            out.append(f"不认识的分辨率 {resolution}")
        elif resolution == "1080p" and LOW_RES_ONLY.search(self.model):
            out.append(f"{self.model} 只有 480p 和 720p")
        return out

    def coef(self) -> float:
        """Plan credits per 10,000 tokens. `afp_coef` is one number, or a map by resolution because
        1080p costs more (Seedance 2.0: 230 at 480p/720p, 255 at 1080p)."""
        value = self.cfg.get("afp_coef") or 0
        if isinstance(value, dict):
            return float(value.get(self.cfg.get("resolution", "720p")) or 0)
        return float(value)

    def supports(self, req: VideoRequest) -> str | None:
        longest = int(self.cfg.get("max_seconds", 15))
        if not 4 <= req.duration <= longest:
            return f"{self.label} 只支持 4–{longest} 秒"
        return None

    def video_tokens(self, req: VideoRequest) -> float:
        is_25 = self.cfg.get("size_table") == "2.5" or bool(SEEDANCE_25.search(self.model))
        table = VIDEO_PIXELS_25 if is_25 else VIDEO_PIXELS
        sizes = table.get(self.cfg.get("resolution", "720p"), table["720p"])
        w, h = sizes.get(req.ratio, sizes["16:9"])
        return (VIDEO_FPS * req.duration + 1) * w * h / 1024

    def estimate_afp(self, req: VideoRequest) -> float:
        return self.video_tokens(req) / 10_000 * self.coef()

    def usage_afp(self, usage: dict) -> float:
        tokens = (usage or {}).get("completion_tokens") or 0
        return float(tokens) / 10_000 * self.coef()

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
                    json=self.build_body(req), headers=self.headers(), billed=True).json()
        return data["id"]

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        data = call(http, "GET", f"{self.base_url}/contents/generations/tasks/{task_id}", headers=self.headers()).json()
        status = data.get("status", "")
        if not status:
            raise ProviderError(f"查询结果里没有任务状态：{str(data)[:200]}")  # transient: keep polling
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

    def missing(self) -> list[str]:
        out = super().missing()
        if self.plan and not self.cfg.get("afp_per_image"):
            out.append("providers.yaml 里没填 afp_per_image（套餐抵扣系数）")
        return out

    def generate(self, http: httpx.Client, req: ImageRequest) -> bytes:
        if req.ratio not in IMAGE_SIZES:
            raise ProviderError(f"Seedream 不支持画幅 {req.ratio}")
        # No `sequential_image_generation`: Seedream 5.0 Pro rejects it (it has no image-set mode), and
        # leaving it out means a single image on every Seedream version. The image comes back as a URL
        # on Ark's object storage: on the Agent Plan endpoint a b64_json reply once hung for ~5 minutes
        # until the server dropped the connection, while the same request with a URL took ~40 s.
        body = {
            "model": self.model,
            "prompt": req.prompt,
            "size": IMAGE_SIZES[req.ratio],
            "response_format": "url",
            "watermark": False,
        }
        if req.refs:
            urls = [media.data_url(p) for p in req.refs]
            body["image"] = urls[0] if len(urls) == 1 else urls
        if req.seed is not None:
            body["seed"] = req.seed
        payload = call(http, "POST", f"{self.base_url}/images/generations", json=body, headers=self.headers(),
                       billed=True).json()
        return image_bytes(http, payload)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/contents/generations/tasks",
                         params={"page_num": 1, "page_size": 1}, headers=self.headers())
