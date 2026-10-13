"""Stage runner: script → audio → characters → keyframes → videos → compose, each stage resumable.

The script is a JSON file written in the conversation and imported here (see script.py); no
language model is called. Models come from the `routing` section of providers.yaml. Video shots go
to the default video model unless marked photoreal; a refusal from the default model falls back to
the photoreal one, so a shot is only lost when both refuse (it then becomes a slow push on its
keyframe at compose time).

A drama project (template: drama) takes each shot's length from the script, makes its dialogue
with the video model (or with TTS, routing.dialogue: tts), draws scene references with the
character references, skips the keyframe of a reference-mode shot and leaves quote cards to the
compose step.
"""
import logging
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from ..config import Settings, load_yaml
from ..errors import ProviderError, Unconfirmed
from ..ledger import Ledger
from ..media import probe_duration
from ..net import download
from ..providers import load_providers
from ..providers.base import ImageRequest, SpeechRequest, VideoRequest
from ..tasks import run_video
from . import compose
from .fonts import CARD_FONT, resolve_font
from .presets import get_style
from .project import CHARACTER_VIEWS, STAGES, Project, Shot, Workspace, new_project_id
from .script import CHARS_PER_SECOND, PREV, Changes, apply_script, diff_script, hard_errors, storyboard_markdown

log = logging.getLogger(__name__)

# Reference images per character: a full body and a head shot. Seedance's prompt guide advises a
# head shot plus a full-body picture rather than a multi-view sheet, which reads as several people.
CHARACTER_PROMPTS = {
    "body": "角色参考图：单人全身正面站姿，双臂自然下垂，纯浅灰色背景，柔和均匀的光线，画面里没有其他人物、道具和文字",
    "face": "角色参考图：参考图中这个人物的头像特写，只画头部和肩膀，脸部占画面一半以上，正面平视，五官、发型和发饰"
            "与参考图一致，表情平静，纯浅灰色背景，柔和均匀的光线，画面里没有其他景物和文字",
}
CHARACTER_RATIOS = {"body": "3:4", "face": "1:1"}
VIEW_NAMES = {"body": "全身参考图", "face": "头部参考图"}
SCENE_PROMPT = "场景参考图：只有环境，没有任何人物，画面里没有文字"
# Keyframes are drawn full frame, but a 2.35 letterbox hides about an eighth at the top and bottom.
LETTERBOX_FRAMING = "宽银幕取景：画面上下各约八分之一会被电影黑边遮住，人物的头顶、脚和关键物体都放在中间部分"
DIALOGUE_ROUTES = ("native", "tts")
STAGE_NAMES = {"script": "剧本与分镜", "audio": "配音", "characters": "角色参考图", "keyframes": "关键帧",
               "videos": "视频", "compose": "合成"}


def ref_note(project: Project, shot: Shot) -> str:
    """Tell the image model what each reference image is, in the order they are sent."""
    parts, n = [], 1
    for ref in shot.refs:
        if ref == PREV:
            parts.append(f"图{n}是上一个镜头的画面，保持场景、光线和色调连贯")
            n += 1
        elif project.scene(ref):
            parts.append(f"图{n}是场景「{project.scene(ref).get('name', ref)}」的参考，保持环境一致")
            n += 1
        else:
            name = (project.character(ref) or {}).get("name", ref)
            parts.append(f"图{n}、图{n + 1}是人物「{name}」的全身和头部参考，保持外貌和服饰一致")
            n += 2
    return "；".join(parts) + "。" if parts else ""


class Producer:
    def __init__(self, settings: Settings):
        cfg = load_yaml(settings.providers_file)
        self.settings = settings
        self.routing = cfg.get("routing") or {}
        self.plans = cfg.get("plans") or {}
        self.providers = load_providers(cfg)
        bench = cfg.get("bench") or {}
        self.poll_seconds = float(bench.get("poll_seconds", 10))
        self.timeout_seconds = float(bench.get("timeout_minutes", 30)) * 60
        self.projects_dir = settings.data_dir / "projects"
        self.ledger = Ledger(settings.data_dir / "ledger.jsonl")
        self.draft = False   # route every shot to routing.video.draft (cheap first pass)

    def fetch(self, http, url: str, out: Path) -> None:
        download(http, url, out)

    # ------------------------------------------------------------ routing

    def _provider(self, kind: str, pid: str):
        p = self.providers[kind].get(pid)
        if p is None:
            raise ValueError(f"routing 指向的 {pid} 不在 providers.yaml 的 {kind} 里")
        if not p.ready:
            raise ValueError(f"{pid} 没配置好：{'；'.join(p.missing())}")
        return p

    def video_provider(self, shot: Shot):
        """Photoreal shots go to routing.video.photoreal even in draft mode: the default and draft
        models (Seedance) refuse realistic faces, so a draft attempt there would only be refused."""
        video = self.routing.get("video") or {}
        if shot.photoreal and video.get("photoreal"):
            return self._provider("video", video["photoreal"])
        if self.draft and video.get("draft"):
            return self._provider("video", video["draft"])
        if shot.hero and video.get("hero"):
            return self._provider("video", video["hero"])
        return self._provider("video", video["default"])

    def fallback_provider(self):
        pid = (self.routing.get("video") or {}).get("photoreal")
        return self._provider("video", pid) if pid else None

    @property
    def dialogue_route(self) -> str:
        """native: the video model speaks the line (default, fits the character's age best in the
        2026-10 listening test); tts: the clip keeps only ambient sound and TTS says the line."""
        route = self.routing.get("dialogue", "native")
        if route not in DIALOGUE_ROUTES:
            raise ValueError("routing.dialogue 只能是 native（视频模型原生台词）或 tts（TTS 配台词）")
        return route

    @property
    def image_provider(self):
        return self._provider("image", self.routing["image"])

    @property
    def tts(self):
        tts = self.routing.get("tts") or {}
        p = self._provider("tts", tts["provider"])
        voice = tts.get("voice") or p.voices[0]
        if voice not in p.voices:
            raise ValueError(f"routing.tts.voice「{voice}」不是 {p.id} 的音色（可选：{'、'.join(p.voices)}）："
                             f"换配音模型时要同时改 voice，或者把它加进 {p.id} 的 voices")
        return p, voice

    # ------------------------------------------------------------ projects

    def create(self, prompt: str, aspect: str, seconds: int, style: str, template: str = "narration") -> Workspace:
        get_style(style)  # validate early
        pid = base = new_project_id()
        n = 2
        while (self.projects_dir / pid).exists():  # two projects in the same second
            pid, n = f"{base}-{n}", n + 1
        ws = Workspace(self.projects_dir / pid)
        ws.save(Project(id=pid, prompt=prompt, template=template, aspect=aspect, seconds=seconds, style=style,
                        created_at=pid[1:]))
        return ws

    def new_from_script(self, data: dict) -> tuple[Workspace, list[str]]:
        """Create a project from a script JSON written in the conversation."""
        errors = hard_errors(data)
        if errors:
            raise ValueError("剧本有问题，改好再导入：\n" + "\n".join(f"  - {e}" for e in errors))
        ws = self.create(str(data["prompt"]).strip(), data.get("aspect", "9:16"), int(data.get("seconds", 60)),
                         data.get("style", "guofeng-ink"))
        project = ws.load()
        warnings = apply_script(project, data)
        ws.save(project)
        self.write_storyboard(ws, project)
        return ws, warnings

    def import_script(self, ws: Workspace, data: dict) -> tuple[Changes, list[str]]:
        """Replace a project's script with a revised one; only what the changes affect is removed."""
        errors = hard_errors(data)
        if errors:
            raise ValueError("剧本有问题，改好再导入：\n" + "\n".join(f"  - {e}" for e in errors))
        old = ws.load()
        new = Project.from_dict(asdict(old))
        new.prompt = str(data["prompt"]).strip()
        new.aspect = data.get("aspect", old.aspect)
        new.seconds = int(data.get("seconds", old.seconds))
        new.style = data.get("style", old.style)
        get_style(new.style)
        warnings = apply_script(new, data)
        old_shots = {s.id: s for s in old.shots}
        for shot in new.shots:  # measurements of untouched shots stay valid
            before = old_shots.get(shot.id)
            if before:
                shot.audio_s, shot.duration, shot.provider, shot.note = (before.audio_s, before.duration,
                                                                         before.provider, before.note)
        changes = diff_script(old, new)
        for cid in changes.characters:
            for path in ws.character_files(cid):
                path.unlink(missing_ok=True)
        for sid in changes.scenes:
            ws.scene(sid).unlink(missing_ok=True)
        for sid in changes.audio:
            ws.clear_audio(new.shot(sid))
        for sid in changes.keyframes:
            ws.clear_keyframe(new.shot(sid))
        for sid in changes.clips:
            ws.clear_clip(new.shot(sid))
        for sid in changes.removed:
            ws.clear_shot(old_shots[sid])
        ws.save(new)
        self.write_storyboard(ws, new)
        return changes, warnings

    def open(self, project_id: str = "latest") -> Workspace:
        if project_id == "latest":
            found = sorted(p.parent for p in self.projects_dir.glob("p*/project.json"))
            if not found:
                raise FileNotFoundError("还没有项目：先用 /short-video 写剧本，再运行 ai-video new --script <文件>")
            return Workspace(found[-1])
        ws = Workspace(self.projects_dir / project_id)
        if not ws.project_file.exists():
            raise FileNotFoundError(f"没有项目 {project_id}")
        return ws

    def list_projects(self) -> list[Project]:
        return [Workspace(p.parent).load() for p in sorted(self.projects_dir.glob("p*/project.json"))]

    def write_storyboard(self, ws: Workspace, project: Project) -> None:
        (ws.root / "storyboard.md").write_text(storyboard_markdown(project), encoding="utf-8")

    # ------------------------------------------------------------ references

    @staticmethod
    def cast(project: Project) -> list[str]:
        """Characters that appear in at least one shot, in declaration order."""
        used = {r for s in project.shots for r in s.refs if r != PREV}
        return [c["id"] for c in project.characters if c["id"] in used]

    @staticmethod
    def scene_cast(project: Project) -> list[str]:
        used = {r for s in project.shots for r in s.refs}
        return [c["id"] for c in project.scenes if c["id"] in used]

    @staticmethod
    def keyframe_refs(ws: Workspace, project: Project, shot: Shot) -> list[Path]:
        """Reference images of a shot in `refs` order: the keyframe or the reference clip draws on these."""
        refs = []
        index = project.shots.index(shot)
        for ref in shot.refs:
            if ref == PREV:
                refs.append(ws.keyframe(project.shots[index - 1]))
            elif project.scene(ref):
                refs.append(ws.scene(ref))
            else:
                refs += ws.character_files(ref)
        return refs

    # ------------------------------------------------------------ cost

    @staticmethod
    def _shortest(p, need: int) -> int:
        longest = int(p.cfg.get("max_seconds", 15))
        probe = Path("probe")
        for d in range(max(1, need), longest + 1):
            if p.supports(VideoRequest(prompt="", duration=d, first_frame=probe)) is None:
                return d
        return longest

    def fit_duration(self, p, audio_s: float) -> int:
        """Shortest whole-second clip the provider accepts that covers the line plus the pause."""
        return self._shortest(p, math.ceil(audio_s + compose.GAP))

    def clip_seconds(self, p, shot: Shot) -> int:
        """Drama: the shortest clip the provider accepts that covers the scripted screen time (a 3 s
        shot is made at the model's 4 s minimum and trimmed when composing)."""
        return self._shortest(p, shot.seconds)

    def estimate(self, ws: Workspace, project: Project) -> dict[str, tuple[float, Counter]]:
        """¥ and plan credits (AFP by plan) still to spend, per stage."""
        out = {stage: [0.0, Counter()] for stage in ("characters", "keyframes", "videos")}

        def add(stage: str, p, cost: tuple[float, float]) -> None:
            out[stage][0] += cost[0]
            if cost[1]:
                out[stage][1][p.plan_id] += cost[1]

        img = self.image_provider
        for cid in self.cast(project):
            for view in CHARACTER_VIEWS:
                if not ws.has(ws.character(cid, view)):
                    refs = [Path("body")] if view == "face" else []
                    add("characters", img, img.cost(ImageRequest(prompt="", ratio=CHARACTER_RATIOS[view], refs=refs)))
        for sid in self.scene_cast(project):
            if not ws.has(ws.scene(sid)):
                add("characters", img, img.cost(ImageRequest(prompt="", ratio=project.aspect)))
        for shot in project.shots:
            if shot.is_card:
                continue  # drawn at compose time
            if shot.needs_keyframe and not ws.has(ws.keyframe(shot)):
                add("keyframes", img, img.cost(ImageRequest(prompt="", ratio=project.aspect,
                                                            refs=self.keyframe_refs(ws, project, shot))))
            if not ws.has(ws.clip(shot)):
                p = self.video_provider(shot)
                if project.is_drama:
                    duration = shot.duration or self.clip_seconds(p, shot)
                else:
                    duration = shot.duration or self.fit_duration(p, shot.audio_s or len(shot.narration) / CHARS_PER_SECOND)
                add("videos", p, p.cost(VideoRequest(prompt="", duration=duration, ratio=project.aspect,
                                                     first_frame=Path("probe") if shot.needs_keyframe else None)))
        return {stage: (round(cny, 2), afp) for stage, (cny, afp) in out.items()}

    def _charge(self, project: Project, kind: str, p, name: str, cny: float, afp: float = 0.0, **extra) -> None:
        """One ledger line per paid call; status="unconfirmed" marks a call that failed after it may
        have been processed, so the quota gate still counts it."""
        record = dict(run=project.id, kind=kind, provider=p.id, model=p.model, name=name, cny=round(cny, 3), **extra)
        if afp:
            record.update(plan=p.plan_id, afp=round(afp, 1))
        self.ledger.add(**record)

    # ------------------------------------------------------------ stages

    def stage_script(self, ws: Workspace, project: Project) -> list[str]:
        if not project.shots:
            raise ValueError("这个项目还没有剧本：在 Claude Code 里用 /short-video 写好剧本 JSON，"
                             "再运行 ai-video new --script <文件>")
        self.write_storyboard(ws, project)
        return []

    def _speak(self, ws: Workspace, project: Project, p, shot: Shot, text: str, voice: str) -> str | None:
        """Synthesise one shot's spoken audio (narration, or a drama line on the TTS route)."""
        style = get_style(project.style)
        req = SpeechRequest(text=text, voice=voice, speed=style.speed, style=style.tts_style,
                            pronunciations=project.pronunciations)
        with p.client() as http:
            try:
                audio = p.synthesize(http, req)
            except Unconfirmed as e:
                self._charge(project, "tts", p, shot.id, *p.cost(req), status="unconfirmed")
                return f"{shot.id} 配音结果未知（可能已扣费）：{e}"
            except ProviderError as e:
                return f"{shot.id} 配音失败：{e}"
        path = ws.audio(shot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        self._charge(project, "tts", p, shot.id, *p.cost(req))
        return None

    def spoken_by_tts(self, project: Project, shot: Shot) -> tuple[str, str] | None:
        """(text, voice) when a shot's speech is made by TTS rather than by the video model."""
        p, voice = self.tts
        if shot.is_card:
            return None
        if shot.voice == "narration" and shot.narration:
            return shot.narration, voice
        if project.is_drama and shot.voice == "dialogue" and self.dialogue_route == "tts":
            return shot.line, (project.character(shot.speaker) or {}).get("tts_voice") or voice
        return None

    def stage_audio(self, ws: Workspace, project: Project) -> list[str]:
        p, _ = self.tts
        todo = [(s, spoken) for s in project.shots
                if (spoken := self.spoken_by_tts(project, s)) and not ws.audio(s).exists()]
        problems = []
        with ThreadPoolExecutor(max_workers=int(p.cfg.get("max_concurrency", 2))) as pool:
            for problem in pool.map(lambda item: self._speak(ws, project, p, item[0], *item[1]), todo):
                if problem:
                    problems.append(problem)
        for shot in project.shots:
            if shot.is_card:
                continue
            if ws.audio(shot).exists():
                shot.audio_s = round(probe_duration(ws.audio(shot)), 3)
            p_video = self.video_provider(shot)
            longest = int(p_video.cfg.get("max_seconds", 15))
            if project.is_drama:
                shot.duration = self.clip_seconds(p_video, shot)
                if shot.seconds > longest:
                    problems.append(f"{shot.id} 写了 {shot.seconds} 秒，超过 {p_video.label} 单段 {longest} 秒上限，"
                                    f"合成时会定格补足；建议拆成两个镜头")
                if shot.audio_s > shot.seconds:
                    problems.append(f"{shot.id} 的配音 {shot.audio_s:.1f} 秒，比镜头的 {shot.seconds} 秒长，会被截断")
            elif ws.audio(shot).exists():
                shot.duration = self.fit_duration(p_video, shot.audio_s)
                if shot.audio_s + compose.GAP > longest:
                    problems.append(f"{shot.id} 旁白 {shot.audio_s:.1f} 秒，超过单个镜头 {longest} 秒上限，建议拆成两个镜头")
        ws.save(project)
        return problems

    def stage_characters(self, ws: Workspace, project: Project) -> list[str]:
        cast, scenes = self.cast(project), self.scene_cast(project)
        if not cast and not scenes:
            return []
        p = self.image_provider
        style = get_style(project.style)

        def draw_scene(sid: str) -> str | None:
            out = ws.scene(sid)
            if out.exists():
                return None
            look = (project.scene(sid) or {}).get("look", "")
            req = ImageRequest(prompt=f"{look}。{SCENE_PROMPT}。{style.image_suffix}", ratio=project.aspect)
            with p.client() as http:
                try:
                    data = p.generate(http, req)
                except Unconfirmed as e:
                    self._charge(project, "image", p, f"scene_{sid}", *p.cost(req), status="unconfirmed")
                    return f"场景 {sid} 的参考图结果未知（可能已扣费）：{e}"
                except ProviderError as e:
                    return f"场景 {sid} 的参考图失败：{e}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            self._charge(project, "image", p, f"scene_{sid}", *p.cost(req))
            return None

        def draw(cid: str) -> str | None:
            look = (project.character(cid) or {}).get("look", "")
            with p.client() as http:
                for view in CHARACTER_VIEWS:  # the head shot is drawn from the full-body image
                    out = ws.character(cid, view)
                    if out.exists():
                        continue
                    refs = [ws.character(cid, "body")] if view == "face" else []
                    req = ImageRequest(prompt=f"{look}。{CHARACTER_PROMPTS[view]}。{style.character_suffix}",
                                       ratio=CHARACTER_RATIOS[view], refs=refs)
                    try:
                        data = p.generate(http, req)
                    except Unconfirmed as e:
                        self._charge(project, "image", p, f"{cid}_{view}", *p.cost(req), status="unconfirmed")
                        return f"角色 {cid} 的{VIEW_NAMES[view]}结果未知（可能已扣费）：{e}"
                    except ProviderError as e:
                        return f"角色 {cid} 的{VIEW_NAMES[view]}失败：{e}"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                    self._charge(project, "image", p, f"{cid}_{view}", *p.cost(req))
            return None

        with ThreadPoolExecutor(max_workers=int(p.cfg.get("max_concurrency", 2))) as pool:
            jobs = [pool.submit(draw, cid) for cid in cast] + [pool.submit(draw_scene, sid) for sid in scenes]
            return [r for r in (job.result() for job in jobs) if r]

    def stage_keyframes(self, ws: Workspace, project: Project) -> list[str]:
        p = self.image_provider
        style = get_style(project.style)

        def draw(shot: Shot) -> str | None:
            refs = self.keyframe_refs(ws, project, shot)
            missing = [r.name for r in refs if not r.exists()]
            if missing:
                shot.note = f"缺少参考图 {', '.join(missing)}：先生成角色参考图或上一镜的关键帧"
                return f"{shot.id} {shot.note}"
            framing = f"。{LETTERBOX_FRAMING}" if project.letterbox else ""
            req = ImageRequest(prompt=f"{ref_note(project, shot)}{shot.image_prompt}{framing}。{style.image_suffix}",
                               ratio=project.aspect, refs=refs)
            with p.client() as http:
                try:
                    data = p.generate(http, req)
                except Unconfirmed as e:
                    self._charge(project, "image", p, shot.id, *p.cost(req), status="unconfirmed")
                    shot.note = f"关键帧结果未知（可能已扣费）：{e}"
                    return f"{shot.id} {shot.note}"
                except ProviderError as e:
                    shot.note = f"关键帧失败：{e}"
                    return f"{shot.id} {shot.note}"
            path = ws.keyframe(shot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            shot.note = ""
            self._charge(project, "image", p, shot.id, *p.cost(req))
            return None

        todo = [s for s in project.shots if s.needs_keyframe and not ws.keyframe(s).exists()]
        independent = [s for s in todo if PREV not in s.refs]
        chained = [s for s in todo if PREV in s.refs]
        with ThreadPoolExecutor(max_workers=int(p.cfg.get("max_concurrency", 2))) as pool:
            problems = [r for r in pool.map(draw, independent) if r]
        for shot in chained:  # in order: each needs the keyframe just before it
            problem = draw(shot)
            if problem:
                problems.append(problem)
        ws.save(project)
        return problems

    @staticmethod
    def spoken_line(project: Project, shot: Shot) -> str:
        """The line as the video model should say it, with the speaker's voice description."""
        c = project.character(shot.speaker) or {}
        voice = f"（{c['voice_desc']}）" if c.get("voice_desc") else ""
        line = shot.line.rstrip("。")
        if line[-1:] not in "？！…?!":
            line += "。"
        return f"{c.get('name', shot.speaker)}{voice}说：「{line}」"

    def video_prompt(self, project: Project, shot: Shot) -> str:
        style = get_style(project.style)
        if not project.is_drama:
            camera = f"镜头{shot.camera}。" if shot.camera else ""
            return f"{shot.video_prompt}。{camera}{style.video_suffix}"
        parts = [style.video_prefix, shot.video_prompt]
        if shot.voice == "dialogue" and self.dialogue_route == "native":
            parts.append(self.spoken_line(project, shot))
        else:
            parts.append("画面中无人说话")  # a clip may still invent speech; only ambient sound is wanted
        if shot.camera:
            parts.append(f"镜头{shot.camera}")
        parts.append(style.video_suffix)
        return "。".join(part.rstrip("。") for part in parts if part) + "。"

    def video_request(self, ws: Workspace, project: Project, shot: Shot) -> VideoRequest:
        """Frame mode animates the keyframe; reference mode composes from the character and scene
        images. Drama clips keep their sound when it is used (dialogue, ambience)."""
        audio = project.is_drama and shot.voice in ("dialogue", "ambient")
        duration = shot.duration or (self.clip_seconds(self.video_provider(shot), shot) if project.is_drama else 5)
        if shot.mode == "ref":
            return VideoRequest(prompt=self.video_prompt(project, shot), duration=duration, ratio=project.aspect,
                                audio=audio, ref_images=self.keyframe_refs(ws, project, shot))
        return VideoRequest(prompt=self.video_prompt(project, shot), duration=duration, ratio=project.aspect,
                            first_frame=ws.keyframe(shot), audio=audio)

    def _make_clip(self, ws: Workspace, project: Project, shot: Shot) -> str | None:
        req = self.video_request(ws, project, shot)
        missing = [r.name for r in req.ref_images if not r.exists()]
        if missing:
            shot.note = f"缺少参考图 {', '.join(missing)}：先跑 characters 阶段"
            return f"{shot.id} {shot.note}"
        candidates = [self.video_provider(shot)]
        fallback = self.fallback_provider()
        if fallback and fallback.id != candidates[0].id:
            candidates.append(fallback)
        last = ""
        for p in candidates:
            if p.supports(req):
                last = p.supports(req)
                continue
            cny, afp = p.cost(req)
            base = {"shot": shot.id, "provider": p.id, "model": p.model, "est_cny": round(cny, 3),
                    "est_afp": round(afp, 1)}
            state = run_video(p, req, ws.clip(shot), ws.job(shot, p.id), base, poll_seconds=self.poll_seconds,
                              timeout_seconds=self.timeout_seconds, fetch=self.fetch,
                              on_done=lambda st, p=p: self._charge(project, "video", p, shot.id, *p.settle(st),
                                                                   task_id=st.get("task_id")),
                              on_unconfirmed=lambda st, p=p: self._charge(project, "video", p, shot.id,
                                                                          st.get("est_cny", 0.0), st.get("est_afp", 0.0),
                                                                          status="unconfirmed"))
            if state.get("state") == "done":
                shot.provider, shot.note = p.id, ""
                return None
            last = f"{p.label}：{state.get('state')} {state.get('message', '')}"
            if state.get("state") == "pending":
                break  # still queued; the next run resumes polling instead of trying another model
            if state.get("state") == "unconfirmed":
                last += "；先到控制台确认有没有生成，再用 --revideo 重做"
                break  # may have created a billed task: never resubmitted automatically
            if state.get("state") != "rejected":
                break  # only refusals move on to the fallback model
        shot.note = f"视频未生成（{last}）"
        return f"{shot.id} {shot.note}"

    def stage_videos(self, ws: Workspace, project: Project) -> list[str]:
        missing = [s.id for s in project.shots if s.needs_keyframe and not ws.keyframe(s).exists()]
        if missing:
            return [f"这些镜头没有关键帧，跳过：{', '.join(missing)}"]
        todo = [s for s in project.shots if not s.is_card and not ws.clip(s).exists()]
        groups: dict[str, list[Shot]] = {}
        for shot in todo:
            groups.setdefault(self.video_provider(shot).id, []).append(shot)
        problems, pools, futures = [], [], []
        try:
            for pid, shots in groups.items():
                pool = ThreadPoolExecutor(max_workers=int(self.providers["video"][pid].cfg.get("max_concurrency", 2)))
                pools.append(pool)
                futures += [pool.submit(self._make_clip, ws, project, s) for s in shots]
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    problems.append(result)
                    log.warning(result)
        finally:
            for pool in pools:
                pool.shutdown(wait=True)
        ws.save(project)
        return problems

    def stage_compose(self, ws: Workspace, project: Project, bgm: Path | None = None) -> Path:
        style = get_style(project.style)
        family, font_file = resolve_font(style.font, self.settings.data_dir / "fonts")
        card_font = resolve_font(CARD_FONT, self.settings.data_dir / "fonts") if project.is_drama else None
        return compose.render(project, ws, family, font_file, bgm=bgm, card_font=card_font)

    def make(self, ws: Workspace, until: str = "compose", bgm: Path | None = None) -> dict:
        """Run the stages in order up to `until`; finished outputs are reused."""
        if until not in STAGES:
            raise ValueError(f"until 只能是 {', '.join(STAGES)}")
        project = ws.load()
        report: dict = {"problems": []}
        for stage in STAGES[:STAGES.index(until) + 1]:
            print(f"\n== {STAGE_NAMES[stage]}")
            if stage == "compose":
                report["final"] = self.stage_compose(ws, project, bgm)
                continue
            problems = getattr(self, f"stage_{stage}")(ws, project)
            for problem in problems:
                print(f"  ! {problem}")
            report["problems"] += problems
            project = ws.load()
        return report
