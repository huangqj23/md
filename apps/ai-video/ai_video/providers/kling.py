"""Kling open platform: text2video / image2video tasks. image2video takes `image_tail` for
first+last frame generation.

Direct calls are authorised with a JWT signed from the AccessKey/SecretKey pair; through a relay
(no `secret_env` configured) the relay's own key is sent as a plain bearer token."""
import base64
import hashlib
import hmac
import json
import time

import httpx

from .. import media
from ..errors import ProviderError, Rejected, classify
from ..net import call
from .base import TaskStatus, VideoProvider, VideoRequest, ping_auth

PENDING = {"submitted", "processing"}
NEGATIVE = "文字，字幕，水印，畸形，肢体错乱，崩坏，模糊"
PROMPT_LIMIT = 500


def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def jwt_token(access_key: str, secret_key: str, now: float | None = None) -> str:
    """HS256 token valid for 30 minutes; nbf is backdated a few seconds to tolerate clock skew."""
    now = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iss": access_key, "exp": now + 1800, "nbf": now - 5}, separators=(",", ":")).encode())
    signing = header + b"." + payload
    signature = _b64url(hmac.new(secret_key.encode(), signing, hashlib.sha256).digest())
    return (signing + b"." + signature).decode()


class KlingVideo(VideoProvider):
    def missing(self) -> list[str]:
        out = super().missing()
        if self.cfg.get("secret_env") and not self.env("secret_env"):
            out.append(f".env 里没有 {self.cfg['secret_env']}")
        return out

    def headers(self) -> dict:
        if not self.cfg.get("secret_env"):
            return {"Authorization": f"Bearer {self.api_key}"}
        return {"Authorization": f"Bearer {jwt_token(self.api_key, self.env('secret_env'))}"}

    def supports(self, req: VideoRequest) -> str | None:
        # kling-v3 takes any length in a range (configured as durations: [3, 15]); older models only 5 or 10.
        if self.cfg.get("durations"):
            low, high = self.cfg["durations"]
            return None if low <= req.duration <= high else f"{self.model} 只支持 {low}–{high} 秒"
        if req.duration not in (5, 10):
            return "可灵 API 只支持 5 秒或 10 秒"
        return None

    def build(self, req: VideoRequest) -> tuple[str, dict]:
        body = {
            "model_name": self.model,
            "prompt": req.prompt[:PROMPT_LIMIT],
            "negative_prompt": NEGATIVE,
            "mode": self.cfg.get("mode", "pro"),
            "duration": str(req.duration),
            "sound": "on" if req.audio else "off",
        }
        if req.mode == "t2v":
            body["aspect_ratio"] = req.ratio
            return "text2video", body
        body["image"] = media.raw_b64(req.first_frame)
        if req.last_frame:
            body["image_tail"] = media.raw_b64(req.last_frame)
        return "image2video", body

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        endpoint, body = self.build(req)
        data = call(http, "POST", f"{self.base_url}/v1/videos/{endpoint}", json=body, headers=self.headers()).json()
        if data.get("code") != 0:
            raise classify(data.get("code"), data.get("message"))(f"{data.get('code')}: {data.get('message')}")
        # The query path depends on the endpoint, so it travels inside the task id.
        return f"{endpoint}:{data['data']['task_id']}"

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        endpoint, tid = task_id.split(":", 1)
        data = call(http, "GET", f"{self.base_url}/v1/videos/{endpoint}/{tid}", headers=self.headers()).json()
        if data.get("code") != 0:
            raise ProviderError(f"查询失败 {data.get('code')}: {data.get('message')}")
        task = data.get("data") or {}
        status = task.get("task_status")
        if status in PENDING:
            return TaskStatus("pending")
        if status == "succeed":
            videos = (task.get("task_result") or {}).get("videos") or []
            if videos:
                return TaskStatus("done", url=videos[0].get("url"), usage={"duration": videos[0].get("duration")})
            return TaskStatus("done")
        message = task.get("task_status_msg") or status or ""
        return TaskStatus("rejected" if classify("", message) is Rejected else "failed", message=message)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/v1/videos/text2video",
                         params={"pageNum": 1, "pageSize": 1}, headers=self.headers())
