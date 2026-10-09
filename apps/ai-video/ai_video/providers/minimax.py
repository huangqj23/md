"""MiniMax open platform: H3 video (video generation V2) and T2A v2 speech, authorised with MINIMAX_API_KEY.

V2 video takes the same content array as Ark (text + images with first_frame / last_frame roles);
H3 always returns stereo audio, so the request's audio flag does not apply.
"""
import httpx

from ..errors import ProviderError, Rejected, classify
from ..net import call
from .ark import media_content
from .base import SpeechProvider, SpeechRequest, TaskStatus, VideoProvider, VideoRequest, ping_auth

PENDING = {"queued", "running"}


def _check_base_resp(data: dict) -> None:
    base = data.get("base_resp") or {}
    code = base.get("status_code", 0)
    if code not in (0, None):
        raise classify(code, base.get("status_msg"))(f"{code}: {base.get('status_msg')}")


class MiniMaxVideo(VideoProvider):
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def supports(self, req: VideoRequest) -> str | None:
        low = 5 if self.model.endswith("Max") else 4
        if not low <= req.duration <= 15:
            return f"{self.model} 只支持 {low}–15 秒"
        return None

    def build_body(self, req: VideoRequest) -> dict:
        return {
            "model": self.model,
            "content": media_content(req),
            "resolution": self.cfg.get("resolution", "768P"),
            "duration": req.duration,
            "ratio": req.ratio if req.mode == "t2v" else "adaptive",
            "aigc_watermark": False,
        }

    def submit(self, http: httpx.Client, req: VideoRequest) -> str:
        data = call(http, "POST", f"{self.base_url}/v2/video_generation", json=self.build_body(req),
                    headers=self.headers()).json()
        _check_base_resp(data)
        if not data.get("task_id"):
            raise ProviderError(f"没有返回 task_id：{str(data)[:200]}")
        return str(data["task_id"])

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        data = call(http, "GET", f"{self.base_url}/v2/query/video_generation/{task_id}", headers=self.headers()).json()
        task = data.get("task") or {}
        status = task.get("status", "")
        if status in PENDING:
            return TaskStatus("pending")
        if status == "succeeded":
            return TaskStatus("done", url=(task.get("content") or {}).get("url"), usage=task.get("usage") or {})
        err = task.get("error") or {}
        state = "rejected" if classify(err.get("code"), err.get("message")) is Rejected else "failed"
        return TaskStatus(state, message=f"{err.get('code') or status}: {err.get('message') or ''}")

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/v2/query/video_generation/0", headers=self.headers())


class MiniMaxTTS(SpeechProvider):
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        voice = {"voice_id": req.voice, "speed": req.speed, "vol": 1.0, "pitch": 0}
        if req.emotion:
            voice["emotion"] = req.emotion
        body = {
            "model": self.model,
            "text": req.text,
            "stream": False,
            "voice_setting": voice,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "output_format": "hex",
        }
        data = call(http, "POST", f"{self.base_url}/v1/t2a_v2", json=body, headers=self.headers()).json()
        _check_base_resp(data)
        audio = (data.get("data") or {}).get("audio")
        if not audio:
            raise ProviderError("没有返回音频")
        return bytes.fromhex(audio)

    def ping(self, http: httpx.Client) -> str:
        return ping_auth(http, "GET", f"{self.base_url}/v2/query/video_generation/0", headers=self.headers())
