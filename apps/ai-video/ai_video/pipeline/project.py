"""Project data (project.json) and its folder layout under data/projects/<id>/."""
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..tasks import read_json, write_json

STAGES = ("script", "audio", "characters", "keyframes", "videos", "compose")
CHARACTER_VIEWS = ("body", "face")   # full-body and head reference images of each character


@dataclass
class Shot:
    id: str
    narration: str
    image_prompt: str
    video_prompt: str
    camera: str = ""
    photoreal: bool = False      # a clearly visible realistic face: Seedance refuses such first frames
    refs: list[str] = field(default_factory=list)   # character ids drawn in this shot, or "prev" for the previous keyframe
    audio_s: float = 0.0         # narration length measured from the TTS file
    duration: int = 0            # clip length requested from the video model, in whole seconds
    provider: str = ""           # video provider that produced the clip
    note: str = ""               # last problem with this shot (refusal, failure)

    @classmethod
    def from_dict(cls, raw: dict) -> "Shot":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})


@dataclass
class Project:
    id: str
    prompt: str
    template: str = "narration"
    aspect: str = "9:16"
    seconds: int = 60
    style: str = "guofeng-ink"
    title: str = ""              # Douyin title
    hook: str = ""
    cover_title: str = ""
    tags: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)   # quoted classic text: {title, author, text}
    check: dict = field(default_factory=dict)    # fact check: {ok, issues, checker}
    characters: list[dict] = field(default_factory=list)   # [{id, name, look}]
    pronunciations: dict = field(default_factory=dict)      # word -> pinyin with tones, e.g. 天姥 -> (tian1)(mu3)
    shots: list[Shot] = field(default_factory=list)
    created_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "Project":
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in names}
        data["shots"] = [Shot.from_dict(s) for s in raw.get("shots") or []]
        return cls(**data)

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise ValueError(f"没有镜头 {shot_id}")

    def character(self, cid: str) -> dict | None:
        return next((c for c in self.characters if c.get("id") == cid), None)


class Workspace:
    """Paths of one project folder."""

    def __init__(self, root: Path):
        self.root = root
        # make --estimate: clear_* only mark files as gone, so a --redo/--revideo can be priced
        # without deleting what was already paid for
        self.dry_run = False
        self.gone: set[Path] = set()

    def has(self, path: Path) -> bool:
        """The generated file is there and a dry run has not marked it as cleared."""
        return path not in self.gone and path.exists()

    def _remove(self, path: Path) -> None:
        if self.dry_run:
            self.gone.add(path)
        else:
            path.unlink(missing_ok=True)

    @property
    def project_file(self) -> Path:
        return self.root / "project.json"

    def audio(self, shot: Shot) -> Path:
        return self.root / "audio" / f"{shot.id}.mp3"

    def keyframe(self, shot: Shot) -> Path:
        return self.root / "keyframes" / f"{shot.id}.png"

    def character(self, cid: str, view: str) -> Path:
        return self.root / "characters" / f"{cid}_{view}.png"

    def character_files(self, cid: str) -> list[Path]:
        return [self.character(cid, view) for view in CHARACTER_VIEWS]

    def clip(self, shot: Shot) -> Path:
        return self.root / "clips" / f"{shot.id}.mp4"

    def job(self, shot: Shot, provider: str) -> Path:
        return self.root / "jobs" / f"{shot.id}.{provider}.json"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def load(self) -> Project:
        raw = read_json(self.project_file)
        if not raw:
            raise FileNotFoundError(f"找不到项目文件 {self.project_file}")
        return Project.from_dict(raw)

    def save(self, project: Project) -> None:
        write_json(self.project_file, asdict(project))

    def clear_clip(self, shot: Shot) -> None:
        """Remove only the video clip and its task records (keeps narration and keyframe)."""
        self._remove(self.clip(shot))
        for job in (self.root / "jobs").glob(f"{shot.id}.*.json"):
            self._remove(job)
        shot.provider, shot.note = "", ""

    def clear_audio(self, shot: Shot) -> None:
        """Remove the narration and the clip cut to its length (keeps the keyframe)."""
        self._remove(self.audio(shot))
        self.clear_clip(shot)
        shot.audio_s, shot.duration = 0.0, 0

    def clear_keyframe(self, shot: Shot) -> None:
        """Remove the keyframe and the clip made from it (keeps the narration)."""
        self._remove(self.keyframe(shot))
        self.clear_clip(shot)

    def clear_character(self, project: Project, cid: str) -> list[str]:
        """Remove a character's reference images and the keyframes drawn from them; returns those shot ids."""
        for path in self.character_files(cid):
            self._remove(path)
        cleared = []
        for shot in project.shots:
            if cid in shot.refs:
                self.clear_keyframe(shot)
                cleared.append(shot.id)
        return cleared

    def clear_shot(self, shot: Shot) -> None:
        """Remove what was generated for a shot so the next run makes it again."""
        for path in (self.audio(shot), self.keyframe(shot), self.clip(shot)):
            self._remove(path)
        for job in (self.root / "jobs").glob(f"{shot.id}.*.json"):
            self._remove(job)
        shot.audio_s, shot.duration, shot.provider, shot.note = 0.0, 0, "", ""


def new_project_id() -> str:
    return time.strftime("p%Y%m%d-%H%M%S")
