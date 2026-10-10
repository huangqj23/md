"""Style presets (presets/styles.yaml): prompt suffixes, subtitle font, narration speed and delivery."""
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT, load_yaml

STYLES_FILE = ROOT / "presets" / "styles.yaml"


@dataclass
class Style:
    id: str
    label: str
    hint: str
    image_suffix: str
    video_suffix: str
    font: str
    speed: float
    tts_style: str = ""          # narration delivery in words, for TTS models steered by prompts (Gemini)
    character_suffix: str = ""   # rendering only, for character references: scene words would leak into them


def load_styles(path: Path = STYLES_FILE) -> dict[str, Style]:
    raw = load_yaml(path)
    return {sid: Style(id=sid, label=c.get("label", sid), hint=c.get("hint", ""),
                       image_suffix=c.get("image_suffix", ""), video_suffix=c.get("video_suffix", ""),
                       font=c.get("font", "Noto Sans SC"), speed=float(c.get("speed", 1.0)),
                       tts_style=c.get("tts_style", ""), character_suffix=c.get("character_suffix", ""))
            for sid, c in raw.items()}


def get_style(style_id: str) -> Style:
    styles = load_styles()
    if style_id not in styles:
        raise ValueError(f"没有风格 {style_id}，可选：{', '.join(styles)}")
    return styles[style_id]
