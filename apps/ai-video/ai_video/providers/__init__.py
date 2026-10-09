"""Provider registry: providers.yaml names an adapter for each entry."""
from .ark import ArkImage, ArkVideo
from .base import Provider
from .doubao_tts import DoubaoTTS
from .kling import KlingVideo
from .minimax import MiniMaxTTS, MiniMaxVideo
from .openai_image import OpenAIImage
from .relay302 import Relay302TTS, Relay302Video

ADAPTERS = {
    "ark_video": ArkVideo,
    "ark_image": ArkImage,
    "kling_video": KlingVideo,
    "minimax_video": MiniMaxVideo,
    "minimax_tts": MiniMaxTTS,
    "doubao_tts": DoubaoTTS,
    "openai_image": OpenAIImage,
    "relay302_video": Relay302Video,
    "relay302_tts": Relay302TTS,
}
KINDS = ("video", "image", "tts")


def load_providers(cfg: dict) -> dict[str, dict[str, Provider]]:
    out: dict[str, dict[str, Provider]] = {kind: {} for kind in KINDS}
    for kind in KINDS:
        for pid, pcfg in (cfg.get(kind) or {}).items():
            cls = ADAPTERS.get((pcfg or {}).get("adapter"))
            if cls is None:
                raise ValueError(f"providers.yaml 的 {kind}.{pid}：未知的 adapter {pcfg.get('adapter')!r}")
            out[kind][pid] = cls(pid, pcfg)
    return out
