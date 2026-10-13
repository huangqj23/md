"""Bench cases (bench/cases.yaml): video cases and speech samples.

A keyframe or reference is named by its id within the case, or as "<case>/<keyframe>" to share
images across cases (one character sheet used by several shots)."""
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_yaml

MODES = ("i2v", "t2v", "chain", "r2v")
KEYFRAME_IDS = "abcdefgh"


@dataclass
class Keyframe:
    id: str
    prompt: str = ""
    ref_prev: bool = False       # generate with the previous keyframe as reference (same scene)
    refs: list[str] = field(default_factory=list)   # other keyframes drawn in as references


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
    first: str = ""              # i2v first frame when it is not this case's first keyframe
    refs: list[str] = field(default_factory=list)       # r2v reference images
    ref_audio: list[str] = field(default_factory=list)  # r2v voice samples: speech case ids
    r2v_prompt: str = ""         # r2v has no frame to animate, so its prompt describes the scene

    @property
    def t2v_prompt(self) -> str:
        scene = self.keyframes[0].prompt if self.keyframes else ""
        return f"{scene}\n{self.video_prompt}".strip()

    @property
    def first_frame(self) -> str:
        return self.first or (self.keyframes[0].id if self.keyframes else "")

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
    provider: str = ""           # a voice sample: made once with this provider and voice only
    voice: str = ""


def _case(raw: dict) -> Case:
    modes = list(raw["modes"]) if "modes" in raw else ["i2v"]   # modes: [] makes images only
    bad = [m for m in modes if m not in MODES]
    if bad:
        raise ValueError(f"用例 {raw.get('id')}：未知的 modes {bad}")
    source = raw.get("source")
    if source:
        count = int((source.get("windows") or {}).get("count", 1))
        keyframes = [Keyframe(KEYFRAME_IDS[i]) for i in range(count)]
    else:
        keyframes = [Keyframe(k["id"], k.get("prompt", "").strip(), bool(k.get("ref_prev")), list(k.get("refs") or []))
                     for k in raw.get("keyframes") or []]
    segments = [Segment(s["from"], s["to"], s["prompt"].strip()) for s in raw.get("segments") or []]
    if "chain" in modes and not segments:
        raise ValueError(f"用例 {raw['id']}：chain 模式需要 segments")
    if "r2v" in modes and not (raw.get("refs") or raw.get("ref_audio")):
        raise ValueError(f"用例 {raw['id']}：r2v 模式需要 refs 或 ref_audio")
    if "i2v" in modes and not (keyframes or raw.get("first")):
        raise ValueError(f"用例 {raw['id']}：i2v 模式需要 keyframes 或 first")
    return Case(
        id=raw["id"], title=raw.get("title", raw["id"]), direction=raw.get("direction", ""),
        route=raw.get("route", raw["id"]), aspect=str(raw.get("aspect", "16:9")),
        duration=int(raw.get("duration", 5)), modes=modes, video_prompt=(raw.get("video_prompt") or "").strip(),
        keyframes=keyframes, segments=segments, source=source, audio=bool(raw.get("audio", True)),
        check=(raw.get("check") or "").strip(), first=str(raw.get("first") or ""),
        refs=list(raw.get("refs") or []), ref_audio=list(raw.get("ref_audio") or []),
        r2v_prompt=(raw.get("r2v_prompt") or "").strip(),
    )


def _check_names(cases: list[Case], speech: list[SpeechCase]) -> None:
    """Fail on a reference to a keyframe or voice sample that no case defines, before anything is billed."""
    keyframes = {c.id: {k.id for k in c.keyframes} for c in cases}
    samples = {s.id for s in speech if s.provider}
    for case in cases:
        names = [case.first] if case.first else []
        names += case.refs + [r for k in case.keyframes for r in k.refs]
        for name in names:
            cid, _, kid = name.rpartition("/")
            if kid not in keyframes.get(cid or case.id, set()):
                raise ValueError(f"用例 {case.id}：找不到关键帧 {name}")
        for sid in case.ref_audio:
            if sid not in samples:
                raise ValueError(f"用例 {case.id}：ref_audio 里的 {sid} 不是声音样本（speech 里要写 provider）")


def load_cases(path: Path) -> tuple[list[Case], list[SpeechCase]]:
    raw = load_yaml(path)
    cases = [_case(c) for c in raw.get("cases") or []]
    speech = [SpeechCase(s["id"], s.get("title", s["id"]), s["text"].strip(), float(s.get("speed", 1.0)),
                         str(s.get("style") or "").strip(), str(s.get("provider") or ""), str(s.get("voice") or ""))
              for s in raw.get("speech") or []]
    _check_names(cases, speech)
    return cases, speech
