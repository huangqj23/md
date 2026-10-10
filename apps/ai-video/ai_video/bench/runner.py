"""Bench runner: keyframes, clips and speech for every case × configured provider.

A video task id is written to jobs/video/<name>.json before polling starts, so an interrupted run
resumes polling the same task instead of paying for a new one. Finished outputs are never redone;
refused or failed attempts are kept (they are results too) unless --retry-failed is given.
"""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .. import media
from ..config import Settings, load_yaml
from ..errors import ProviderError, Rejected, Unconfirmed
from ..ledger import Ledger
from ..net import call, download, make_client
from ..providers import load_providers
from ..providers.base import ImageRequest, SpeechRequest, VideoRequest
from ..tasks import TERMINAL, read_json, run_video, write_json
from .cases import Case, load_cases

log = logging.getLogger(__name__)

MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
STATE_NAMES = {"done": "完成", "rejected": "被拒绝", "failed": "失败", "pending": "等待中",
               "skipped": "跳过", "error": "出错"}


@dataclass
class Job:
    kind: str                    # image | video | tts
    name: str
    provider: str
    out: Path
    estimate: float = 0.0        # ¥
    case: Case | None = None
    meta: dict = field(default_factory=dict)
    afp: float = 0.0             # plan credits, for providers billed through a plan
    plan: str = ""


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_")


class Bench:
    def __init__(self, settings: Settings, run: str = "phase0", cases: list[str] | None = None,
                 providers: list[str] | None = None, retry_failed: bool = False):
        cfg = load_yaml(settings.providers_file)
        self.settings = settings
        self.options = cfg.get("bench") or {}
        self.providers = load_providers(cfg)
        self.plans = cfg.get("plans") or {}
        all_cases, self.speech_cases = load_cases(settings.cases_file)
        unknown = set(cases or []) - {c.id for c in all_cases}
        if unknown:
            raise ValueError(f"没有这些用例：{', '.join(sorted(unknown))}")
        self.cases = [c for c in all_cases if not cases or c.id in cases]
        self.only = set(providers or [])
        self.retry_failed = retry_failed
        self.run_name = run
        self.root = settings.data_dir / "bench" / run
        self.ledger = Ledger(settings.data_dir / "ledger.jsonl")
        self.poll_seconds = float(self.options.get("poll_seconds", 10))
        self.timeout_seconds = float(self.options.get("timeout_minutes", 30)) * 60

    # ------------------------------------------------------------ providers

    def _selected(self, pid: str) -> bool:
        return not self.only or pid in self.only

    def active(self, kind: str) -> list:
        return [p for pid, p in self.providers[kind].items() if p.ready and self._selected(pid)]

    def inactive(self, kind: str) -> list[tuple[str, list[str]]]:
        return [(pid, p.missing()) for pid, p in self.providers[kind].items() if not p.ready and self._selected(pid)]

    def concurrency(self, kind: str, pid: str) -> int:
        """Tasks in flight per provider, so a run does not trip the provider's concurrency limit."""
        return max(1, int(self.providers[kind][pid].cfg.get("max_concurrency", 2)))

    # ------------------------------------------------------------ paths and state

    def keyframe_file(self, case: Case, kf_id: str, pid: str) -> Path:
        return self.root / "images" / case.id / f"{kf_id}.{pid}.png"

    def source_file(self, case: Case, kf_id: str) -> Path:
        return self.root / "images" / case.id / f"{kf_id}.source.jpg"

    def video_file(self, case: Case, pid: str, mode: str) -> Path:
        return self.root / "videos" / case.id / f"{pid}.{mode}.mp4"

    def segment_file(self, case: Case, pid: str, index: int) -> Path:
        return self.root / "videos" / case.id / f"{pid}.chain.seg{index}.mp4"

    def speech_file(self, case_id: str, pid: str, voice: str) -> Path:
        return self.root / "tts" / f"{case_id}.{pid}.{safe_name(voice)}.mp3"

    def keyframe_order(self, case: Case) -> list[str]:
        """Image providers to take keyframes from: the preferred ones, then any other with files on disk."""
        preferred = self.options.get("keyframe_provider", ["seedream"])
        order = [preferred] if isinstance(preferred, str) else list(preferred)
        found = sorted({p.name.split(".")[1] for p in (self.root / "images" / case.id).glob("*.*.png")})
        return order + [pid for pid in found if pid not in order]

    def keyframe_for(self, case: Case, kf_id: str) -> Path | None:
        """The image that drives a clip: the painting crop, else the first provider in keyframe_order
        that has this keyframe."""
        if case.source:
            path = self.source_file(case, kf_id)
            return path if path.exists() else None
        for pid in self.keyframe_order(case):
            path = self.keyframe_file(case, kf_id, pid)
            if path.exists():
                return path
        return None

    def chain_keyframes(self, case: Case) -> dict[str, Path] | None:
        """Every keyframe of a chained long take from one image provider, so all segments share one
        scene; None until some provider has all of them."""
        if case.source:
            paths = {kf.id: self.source_file(case, kf.id) for kf in case.keyframes}
            return paths if all(p.exists() for p in paths.values()) else None
        for pid in self.keyframe_order(case):
            paths = {kf.id: self.keyframe_file(case, kf.id, pid) for kf in case.keyframes}
            if all(p.exists() for p in paths.values()):
                return paths
        return None

    def state_file(self, kind: str, name: str) -> Path:
        return self.root / "jobs" / kind / f"{name}.json"

    def read_state(self, kind: str, name: str) -> dict:
        return read_json(self.state_file(kind, name))

    def save_state(self, kind: str, name: str, state: dict) -> dict:
        return write_json(self.state_file(kind, name), state)

    def _blocked(self, kind: str, name: str) -> bool:
        """A refused/failed attempt is a result; it is only redone with --retry-failed. Planning never
        changes state: the old attempt is replaced only when the retry actually runs."""
        return self.read_state(kind, name).get("state") in TERMINAL and not self.retry_failed

    def fetch(self, http: httpx.Client, url: str, out: Path) -> None:
        download(http, url, out)

    # ------------------------------------------------------------ planning

    def plan_images(self) -> list[Job]:
        jobs = []
        for case in self.cases:
            if case.source:
                continue
            for p in self.active("image"):
                missing = [kf for kf in case.keyframes if not self.keyframe_file(case, kf.id, p.id).exists()]
                name = f"{case.id}.{p.id}"
                if missing and not self._blocked("image", name):
                    cny, afp = p.cost(ImageRequest(prompt="", ratio=case.aspect))
                    jobs.append(Job("image", name, p.id, self.keyframe_file(case, case.keyframes[-1].id, p.id),
                                    cny * len(missing), case, afp=afp * len(missing), plan=p.plan_id))
        return jobs

    def video_specs(self, case: Case) -> list[dict]:
        """One spec per clip to generate for this case (a chain case yields one spec per segment)."""
        first = case.keyframes[0].id if case.keyframes else None
        specs = []
        for mode in case.modes:
            if mode == "chain":
                for i, seg in enumerate(case.segments, 1):
                    specs.append({"mode": mode, "seg": i, "prompt": seg.prompt, "first": seg.src, "last": seg.dst,
                                  "audio": False})
            elif mode == "t2v":
                specs.append({"mode": mode, "prompt": case.t2v_prompt, "audio": case.audio})
            else:
                specs.append({"mode": mode, "prompt": case.video_prompt, "first": first, "audio": case.audio})
        return specs

    def plan_videos(self) -> tuple[list[Job], list[tuple[str, str]]]:
        jobs, skipped = [], []
        for case in self.cases:
            for p in self.active("video"):
                for spec in self.video_specs(case):
                    seg = spec.get("seg")
                    name = f"{case.id}.{p.id}.{spec['mode']}" + (f".seg{seg}" if seg else "")
                    out = self.segment_file(case, p.id, seg) if seg else self.video_file(case, p.id, spec["mode"])
                    if out.exists() or self._blocked("video", name):
                        continue
                    # Placeholder frame paths only mark the mode (t2v / i2v / first+last) for supports().
                    stub = VideoRequest(prompt="", duration=case.duration, ratio=case.aspect,
                                        first_frame=Path(spec["first"]) if spec.get("first") else None,
                                        last_frame=Path(spec["last"]) if spec.get("last") else None)
                    reason = p.supports(stub)
                    if reason:
                        skipped.append((name, reason))
                        continue
                    # A task still running costs nothing more to finish; a retried one is a new task.
                    prior = self.read_state("video", name)
                    running = prior.get("task_id") and prior.get("state") not in TERMINAL
                    cny, afp = (0.0, 0.0) if running else p.cost(stub)
                    jobs.append(Job("video", name, p.id, out, cny, case, spec, afp=afp, plan=p.plan_id))
        return jobs, skipped

    def plan_speech(self) -> list[Job]:
        jobs = []
        for sc in self.speech_cases:
            for p in self.active("tts"):
                for voice in p.voices:
                    name = f"{sc.id}.{p.id}.{safe_name(voice)}"
                    out = self.speech_file(sc.id, p.id, voice)
                    if out.exists() or self._blocked("tts", name):
                        continue
                    req = SpeechRequest(text=sc.text, voice=voice, speed=sc.speed, style=sc.style)
                    jobs.append(Job("tts", name, p.id, out, p.estimate(req), meta={"request": req, "case": sc.id}))
        return jobs

    # ------------------------------------------------------------ execution

    def execute(self, jobs: list[Job]) -> list[dict]:
        """Run jobs with one pool per provider: a slow provider's queue never holds up the others."""
        groups: dict[tuple[str, str], list[Job]] = {}
        for job in jobs:
            groups.setdefault((job.kind, job.provider), []).append(job)
        pools, futures, results = [], {}, []
        try:
            for (kind, pid), group in groups.items():
                pool = ThreadPoolExecutor(max_workers=self.concurrency(kind, pid))
                pools.append(pool)
                for job in group:
                    futures[pool.submit(getattr(self, f"_run_{job.kind}"), job)] = job
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:  # keep the rest of the bench going; the error is listed below
                    log.exception("%s 出错", job.name)
                    res = {"state": "error", "message": repr(e)}
                results.append({"name": job.name, **res})
                label = STATE_NAMES.get(res.get("state"), res.get("state"))
                print(f"  [{label}] {job.kind} {job.name} {str(res.get('message') or '')[:160]}")
        finally:
            for pool in pools:
                pool.shutdown(wait=True)
        return results

    def _charge(self, job: Job, cny: float, afp: float = 0.0, **extra) -> None:
        record = dict(run=self.run_name, kind=job.kind, provider=job.provider, name=job.name, cny=round(cny, 3), **extra)
        if afp and job.plan:
            record.update(plan=job.plan, afp=round(afp, 1))
        self.ledger.add(**record)

    def _run_image(self, job: Job) -> dict:
        p = self.providers["image"][job.provider]
        case = job.case
        done = 0
        started = time.time()
        with p.client() as http:
            prev: Path | None = None
            for kf in case.keyframes:
                out = self.keyframe_file(case, kf.id, p.id)
                if not out.exists():
                    refs = [prev] if kf.ref_prev and prev else []
                    req = ImageRequest(prompt=kf.prompt, ratio=case.aspect, refs=refs)
                    try:
                        data = p.generate(http, req)
                    except Rejected as e:
                        return self.save_state("image", job.name, {"state": "rejected", "keyframe": kf.id, "message": str(e)})
                    except Unconfirmed as e:
                        self._charge(job, *p.cost(req), keyframe=kf.id, status="unconfirmed")
                        return self.save_state("image", job.name, {"state": "failed", "keyframe": kf.id,
                                                                   "message": f"结果未知（可能已扣费）：{e}"})
                    except ProviderError as e:
                        return self.save_state("image", job.name, {"state": "failed", "keyframe": kf.id, "message": str(e)})
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                    self._charge(job, *p.cost(req), keyframe=kf.id)
                    done += 1
                prev = out
        return self.save_state("image", job.name, {"state": "done", "generated": done,
                                                   "latency_s": round(time.time() - started, 1)})

    def _run_video(self, job: Job) -> dict:
        p = self.providers["video"][job.provider]
        case, spec = job.case, job.meta
        state_path = self.state_file("video", job.name)
        state = read_json(state_path)
        req, base = None, {}
        if state.get("state") in TERMINAL or not state.get("task_id"):  # a new task is needed
            first = last = None
            if spec["mode"] == "chain":
                frames = self.chain_keyframes(case)
                if frames is None:
                    return {"state": "skipped", "message": "长镜头的关键帧还没齐：需要同一家生成的全部关键帧"}
                first, last = frames[spec["first"]], frames[spec["last"]]
            elif spec["mode"] != "t2v":
                first = self.keyframe_for(case, spec["first"])
                last = self.keyframe_for(case, spec["last"]) if spec.get("last") else None
                if first is None or (spec.get("last") and last is None):
                    return {"state": "skipped", "message": "缺少关键帧，先运行 bench images"}
            req = VideoRequest(prompt=spec["prompt"], duration=case.duration, ratio=case.aspect,
                               first_frame=first, last_frame=last, audio=spec["audio"])
            cny, afp = p.cost(req)
            base = {"case": case.id, "provider": p.id, "model": p.model, "mode": spec["mode"],
                    "seg": spec.get("seg"), "est_cny": round(cny, 3), "est_afp": round(afp, 1),
                    "keyframes": [x.name for x in (first, last) if x]}
        return run_video(p, req, job.out, state_path, base, poll_seconds=self.poll_seconds,
                         timeout_seconds=self.timeout_seconds, fetch=self.fetch,
                         on_done=lambda st: self._charge(job, *p.settle(st), mode=st.get("mode"), task_id=st.get("task_id")),
                         on_unconfirmed=lambda st: self._charge(job, st.get("est_cny", 0.0), st.get("est_afp", 0.0),
                                                                mode=st.get("mode"), status="unconfirmed"))

    def _run_tts(self, job: Job) -> dict:
        p = self.providers["tts"][job.provider]
        req: SpeechRequest = job.meta["request"]
        started = time.time()
        base = {"case": job.meta["case"], "provider": p.id, "voice": req.voice}
        with p.client() as http:
            try:
                audio = p.synthesize(http, req)
            except Rejected as e:
                return self.save_state("tts", job.name, {**base, "state": "rejected", "message": str(e)})
            except Unconfirmed as e:
                self._charge(job, job.estimate, status="unconfirmed")
                return self.save_state("tts", job.name, {**base, "state": "failed", "message": f"结果未知（可能已扣费）：{e}"})
            except ProviderError as e:
                return self.save_state("tts", job.name, {**base, "state": "failed", "message": str(e)})
        job.out.parent.mkdir(parents=True, exist_ok=True)
        job.out.write_bytes(audio)
        self._charge(job, job.estimate)
        return self.save_state("tts", job.name, {**base, "state": "done", "latency_s": round(time.time() - started, 1)})

    # ------------------------------------------------------------ sources and chains

    def _source_url(self, http: httpx.Client, case: Case) -> str:
        src = case.source
        if src.get("url"):
            return src["url"]
        if src.get("met_object"):
            meta = call(http, "GET", MET_OBJECT.format(src["met_object"])).json()
            if not meta.get("isPublicDomain"):
                raise ProviderError(f"{case.id}：大都会博物馆标注该作品不是公有领域")
            return meta["primaryImage"]
        if src.get("commons_file"):
            data = call(http, "GET", COMMONS_API, params={
                "action": "query", "titles": f"File:{src['commons_file']}", "prop": "imageinfo",
                "iiprop": "url", "format": "json"}).json()
            pages = (data.get("query") or {}).get("pages") or {}
            info = next(iter(pages.values()), {}).get("imageinfo") or []
            if info:
                return info[0]["url"]
            raise ProviderError(f"{case.id}：维基共享资源上找不到 {src['commons_file']}")
        raise ProviderError(f"{case.id}：source 需要 url、met_object 或 commons_file")

    def prepare_sources(self) -> None:
        """Download public-domain paintings once and cut the keyframe windows from them. A file
        placed by hand at images/<case>/original.jpg is used as is (e.g. when a source site is slow)."""
        with make_client() as http:
            for case in self.cases:
                if not case.source:
                    continue
                targets = [self.source_file(case, kf.id) for kf in case.keyframes]
                if all(t.exists() for t in targets):
                    continue
                original = self.root / "images" / case.id / "original.jpg"
                if not original.exists():
                    print(f"  下载原画 {case.id} …")
                    download(http, self._source_url(http, case), original)
                win = case.windows
                media.crop_windows(original, targets, case.aspect, start=float(win.get("start", 0.5)),
                                   step=float(win.get("step", 0.5)))

    def finish_chains(self) -> None:
        """Join the segments of each chained long take once all of them exist."""
        for case in self.cases:
            if "chain" not in case.modes:
                continue
            for pid in self.providers["video"]:
                out = self.video_file(case, pid, "chain")
                segs = [self.segment_file(case, pid, i) for i in range(1, len(case.segments) + 1)]
                if out.exists() or not all(s.exists() for s in segs):
                    continue
                try:
                    media.concat(segs, out)
                    print(f"  [完成] 拼接长镜头 {case.id}.{pid}")
                except Exception as e:  # a failed join leaves the segments in place for manual review
                    print(f"  [失败] 拼接长镜头 {case.id}.{pid}：{e}")
