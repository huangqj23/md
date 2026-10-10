"""Provider registry: providers.yaml names an adapter for each entry, and optionally the
subscription plan (`plans` section) it is billed through."""
from .ark import ArkImage, ArkVideo
from .base import Provider
from .doubao_tts import DoubaoTTS
from .gemini import GeminiImage, GeminiTTS
from .kling import KlingVideo
from .minimax import MiniMaxTTS, MiniMaxVideo
from .openai_image import OpenAIImage

ADAPTERS = {
    "ark_video": ArkVideo,
    "ark_image": ArkImage,
    "kling_video": KlingVideo,
    "minimax_video": MiniMaxVideo,
    "minimax_tts": MiniMaxTTS,
    "doubao_tts": DoubaoTTS,
    "openai_image": OpenAIImage,
    "gemini_tts": GeminiTTS,
    "gemini_image": GeminiImage,
}
KINDS = ("video", "image", "tts")


def load_providers(cfg: dict) -> dict[str, dict[str, Provider]]:
    plans = cfg.get("plans") or {}
    out: dict[str, dict[str, Provider]] = {kind: {} for kind in KINDS}
    for kind in KINDS:
        for pid, pcfg in (cfg.get(kind) or {}).items():
            pcfg = pcfg or {}
            cls = ADAPTERS.get(pcfg.get("adapter"))
            if cls is None:
                raise ValueError(f"providers.yaml 的 {kind}.{pid}：未知的 adapter {pcfg.get('adapter')!r}")
            plan_id = pcfg.get("plan")
            if plan_id and plan_id not in plans:
                raise ValueError(f"providers.yaml 的 {kind}.{pid}：plans 里没有 {plan_id}")
            out[kind][pid] = cls(pid, pcfg, plans.get(plan_id) if plan_id else None)
    return out
