"""Bench cases (bench/cases.yaml): video cases and speech samples."""
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_yaml

MODES = ("i2v", "t2v", "chain")
KEYFRAME_IDS = "abcdefgh"


@dataclass
class Keyframe:
    id: str
    prompt: str = ""
    ref_prev: bool = False       # generate with the previous keyframe as reference (same scene)


@dataclass
class Segment:
    src: str
    dst: str
    prompt: str


@dataclass
class Case:
    id: str
    title: str
    direction: str
    route: str
    aspect: str
    duration: int
    modes: list[str]
    video_prompt: str = ""
    keyframes: list[Keyframe] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    source: dict | None = None   # public-domain painting instead of generated keyframes
    audio: bool = True
    check: str = ""              # what to look for when scoring

    @property
    def t2v_prompt(self) -> str:
        scene = self.keyframes[0].prompt if self.keyframes else ""
        return f"{scene}\n{self.video_prompt}".strip()

    @property
    def windows(self) -> dict:
        return (self.source or {}).get("windows") or {}


@dataclass
class SpeechCase:
    id: str
    title: str
    text: str
    speed: float = 1.0
    style: str = ""              # delivery in words, for TTS models steered by prompts


def _case(raw: dict) -> Case:
    modes = list(raw.get("modes") or ["i2v"])
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise ValueError(f"用例 {raw.get('id')}：未知的 modes {bad}")
    source = raw.get("source")
    if source:
        count = int((source.get("windows") or {}).get("count", 1))
        keyframes = [Keyframe(KEYFRAME_IDS[i]) for i in range(count)]
    else:
        keyframes = [Keyframe(k["id"], k.get("prompt", "").strip(), bool(k.get("ref_prev"))) for k in raw.get("keyframes") or []]
    segments = [Segment(s["from"], s["to"], s["prompt"].strip()) for s in raw.get("segments") or []]
    if "chain" in modes and not segments:
        raise ValueError(f"用例 {raw['id']}：chain 模式需要 segments")
    return Case(
        id=raw["id"], title=raw.get("title", raw["id"]), direction=raw.get("direction", ""),
        route=raw.get("route", raw["id"]), aspect=str(raw.get("aspect", "16:9")),
        duration=int(raw.get("duration", 5)), modes=modes, video_prompt=(raw.get("video_prompt") or "").strip(),
        keyframes=keyframes, segments=segments, source=source, audio=bool(raw.get("audio", True)),
        check=(raw.get("check") or "").strip(),
    )


def load_cases(path: Path) -> tuple[list[Case], list[SpeechCase]]:
    raw = load_yaml(path)
    cases = [_case(c) for c in raw.get("cases") or []]
    speech = [SpeechCase(s["id"], s.get("title", s["id"]), s["text"].strip(), float(s.get("speed", 1.0)),
                         str(s.get("style") or "").strip())
              for s in raw.get("speech") or []]
    return cases, speech
