"""MiniMax open platform: H3 video (video generation V2) and T2A v2 speech, authorised with MINIMAX_API_KEY.

An M Plan subscription key (sk-cp-…) works on the same endpoints and draws on the plan's quota.
V2 video takes the same content array as Ark (text + images with first_frame / last_frame roles);
H3 always returns stereo audio, so the request's audio flag does not apply.
"""
import re

import httpx

from ..errors import ProviderError, Rejected, classify
from ..net import call
from ..speechtags import PAUSE_MARK, WORD_TAG
from .ark import media_content
from .base import SpeechProvider, SpeechRequest, TaskStatus, VideoProvider, VideoRequest, ping_auth

PENDING = {"queued", "queueing", "pending", "preparing", "processing", "running", "submitted"}
DONE = {"succeeded", "success"}
FAILED = {"failed", "fail", "expired", "cancelled", "canceled"}
# Narration may carry Gemini-style tags; T2A writes pauses as <#seconds#> and reads other tags aloud.
PAUSES = {"short pause": 0.3, "medium pause": 0.5, "long pause": 0.8}
PAUSE_RUN = re.compile(r"(?:<#\d+(?:\.\d+)?#>\s*){2,}")
LEADING_PAUSES = re.compile(r"^(?:\s*<#\d+(?:\.\d+)?#>)+")
TRAILING_PAUSES = re.compile(r"(?:<#\d+(?:\.\d+)?#>\s*)+$")
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _check_base_resp(data: dict) -> None:
    base = data.get("base_resp") or {}
    code = base.get("status_code", 0)
    if code not in (0, None):
        raise classify(code, base.get("status_msg"))(f"{code}: {base.get('status_msg')}")


def speech_text(text: str) -> str:
    """Narration for T2A: pause tags become <#seconds#>, other word tags are dropped, adjacent pauses
    are merged and pauses at the very start or end removed (T2A only accepts a pause between two
    pieces of speech)."""
    def word(m: re.Match) -> str:
        seconds = PAUSES.get(m.group(1).strip().lower())
        return f"<#{seconds}#>" if seconds else ""

    def merge(m: re.Match) -> str:
        total = sum(float(s) for s in PAUSE_MARK.findall(m.group(0)))
        return f"<#{round(min(total, 99.99), 2):g}#>"

    text = PAUSE_RUN.sub(merge, WORD_TAG.sub(word, text))
    return TRAILING_PAUSES.sub("", LEADING_PAUSES.sub("", text.strip())).strip()


def billable_chars(text: str) -> int:
    """T2A bills each CJK ideograph as two characters (a 37-character line was billed as 63)."""
    return len(text) + len(HAN.findall(text))


class MiniMaxMixin:
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def ping(self, http: httpx.Client) -> str:
        """With `ping_url` (the M Plan remains endpoint) the key is checked for real; the fallback
        probe only proves the key is not rejected outright."""
        url = self.cfg.get("ping_url")
        if not url:
            return ping_auth(http, "GET", f"{self.base_url}/v2/query/video_generation/0", headers=self.headers())
        data = call(http, "GET", url, headers=self.headers()).json()
        _check_base_resp(data)
        weekly = next((i.get("current_weekly_remaining_percent") for i in data.get("model_remains") or []
                       if isinstance(i, dict) and "current_weekly_remaining_percent" in i), None)
        return "鉴权通过（M Plan" + (f"，周额度剩余 {weekly}%" if weekly is not None else "") + "）"


class MiniMaxVideo(MiniMaxMixin, VideoProvider):
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
                    headers=self.headers(), billed=True).json()
        _check_base_resp(data)
        if not data.get("task_id"):
            raise ProviderError(f"没有返回 task_id：{str(data)[:200]}")
        return str(data["task_id"])

    def poll(self, http: httpx.Client, task_id: str) -> TaskStatus:
        data = call(http, "GET", f"{self.base_url}/v2/query/video_generation/{task_id}", headers=self.headers()).json()
        base = data.get("base_resp") or {}
        code = base.get("status_code", 0)
        if code not in (0, None):
            message = f"{code}: {base.get('status_msg')}"
            if classify(code, base.get("status_msg")) is Rejected:
                return TaskStatus("rejected", message=message)
            raise ProviderError(message)  # e.g. 1002 rate limit: keep polling
        task = data.get("task") or {}
        status = str(task.get("status") or "").lower()
        if status in PENDING:
            return TaskStatus("pending")
        if status in DONE:
            return TaskStatus("done", url=(task.get("content") or {}).get("url"), usage=task.get("usage") or {})
        if status in FAILED:
            err = task.get("error") or {}
            state = "rejected" if classify(err.get("code"), err.get("message")) is Rejected else "failed"
            return TaskStatus(state, message=f"{err.get('code') or status}: {err.get('message') or ''}")
        raise ProviderError(f"查询结果里没有可识别的任务状态：{str(data)[:200]}")  # transient: keep polling

    def settle(self, state: dict) -> tuple[float, float]:
        """Billed by the seconds the task reports (total includes reference video input)."""
        usage = state.get("usage") or {}
        seconds = usage.get("total_seconds") or usage.get("output_seconds")
        if seconds:
            return float(self.cfg.get("price_cny_per_second", 0)) * float(seconds), 0.0
        return super().settle(state)


class MiniMaxTTS(MiniMaxMixin, SpeechProvider):
    def estimate(self, req: SpeechRequest) -> float:
        return float(self.cfg.get("price_cny_per_10k_chars", 0)) * billable_chars(speech_text(req.text)) / 10_000

    def synthesize(self, http: httpx.Client, req: SpeechRequest) -> bytes:
        text = speech_text(req.text)
        if not text:
            raise ProviderError("旁白去掉停顿标签后是空的")
        voice = {"voice_id": req.voice, "speed": req.speed, "vol": 1.0, "pitch": 0}
        if req.emotion:
            voice["emotion"] = req.emotion
        body = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": voice,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "output_format": "hex",
        }
        used = {w: r for w, r in req.pronunciations.items() if w in text}
        if used:  # e.g. 天姥 is tian1 mu3, not the everyday lao3
            body["pronunciation_dict"] = {"tone": [f"{w}/{r}" for w, r in used.items()]}
        data = call(http, "POST", f"{self.base_url}/v1/t2a_v2", json=body, headers=self.headers(), billed=True).json()
        _check_base_resp(data)
        audio = (data.get("data") or {}).get("audio")
        if not audio:
            raise ProviderError("没有返回音频")
        return bytes.fromhex(audio)
