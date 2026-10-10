import httpx
import pytest
from PIL import Image

from ai_video.bench.cases import Case
from ai_video.bench.report import write_report
from ai_video.bench.runner import Bench
from ai_video.bench.summary import summarize
from ai_video.config import Settings
from ai_video.media import crop_windows
from ai_video.providers.base import TaskStatus, VideoProvider

PROVIDERS = """
video:
  a:
    adapter: ark_video
    label: Alpha
    key_env: FAKE_KEY
    base_url: https://fake
    model: alpha-1
    price_cny_per_second: 1.0
  b:
    adapter: ark_video
    label: Beta
    key_env: FAKE_KEY
    base_url: https://fake
    model: beta-1
    price_cny_per_second: 0.5
image: {}
tts: {}
bench:
  poll_seconds: 0
  timeout_minutes: 1
"""

CASES = """
cases:
  - id: c1
    title: Case 1
    route: r
    aspect: "16:9"
    duration: 5
    modes: [i2v, t2v]
    keyframes: [{id: a, prompt: scene}]
    video_prompt: move
"""


class FakeVideo(VideoProvider):
    def __init__(self, pid, cfg):
        super().__init__(pid, cfg)
        self.submitted = []
        self.polls = 0

    def submit(self, http, req):
        self.submitted.append(req)
        return f"task{len(self.submitted)}"

    def poll(self, http, task_id):
        self.polls += 1
        return TaskStatus("pending") if self.polls == 1 else TaskStatus("done", url="https://cdn/x.mp4")


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")
    (tmp_path / "providers.yaml").write_text(PROVIDERS, encoding="utf-8")
    (tmp_path / "cases.yaml").write_text(CASES, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", providers_file=tmp_path / "providers.yaml",
                        cases_file=tmp_path / "cases.yaml", budget_cny=100)
    b = Bench(settings, providers=["a"])
    fake = FakeVideo("a", b.providers["video"]["a"].cfg)
    b.providers["video"]["a"] = fake

    def fetch(http, url, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")

    b.fetch = fetch
    return b, fake


def add_keyframe(b: Bench) -> None:
    path = b.keyframe_file(b.cases[0], "a", "seedream")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18)).save(path)


def test_jobs_run_once_and_are_charged(bench):
    b, fake = bench
    add_keyframe(b)
    jobs, skipped = b.plan_videos()
    assert sorted(j.name for j in jobs) == ["c1.a.i2v", "c1.a.t2v"] and not skipped
    assert sum(j.estimate for j in jobs) == 10
    results = b.execute(jobs)
    assert {r["state"] for r in results} == {"done"}
    assert len(fake.submitted) == 2
    prompts = {req.mode: req.prompt for req in fake.submitted}
    assert prompts == {"i2v": "move", "t2v": "scene\nmove"}
    assert b.ledger.total("phase0") == 10
    assert b.plan_videos()[0] == []


def test_interrupted_task_resumes_polling_without_resubmitting(bench):
    b, fake = bench
    b.save_state("video", "c1.a.t2v", {"state": "pending", "task_id": "old", "submitted_at": 0, "est_cny": 5})
    jobs = [j for j in b.plan_videos()[0] if j.name == "c1.a.t2v"]
    assert jobs[0].estimate == 0
    b.execute(jobs)
    assert fake.submitted == []
    assert b.read_state("video", "c1.a.t2v")["state"] == "done"


def test_missing_keyframe_skips_without_spending(bench):
    b, fake = bench
    jobs = [j for j in b.plan_videos()[0] if j.name == "c1.a.i2v"]
    assert b.execute(jobs)[0]["state"] == "skipped"
    assert fake.submitted == []


def test_refused_attempts_are_kept_unless_retry(bench):
    b, fake = bench
    b.save_state("video", "c1.a.t2v", {"state": "failed", "task_id": "old", "message": "task timeout"})
    assert "c1.a.t2v" not in [j.name for j in b.plan_videos()[0]]
    b.retry_failed = True
    jobs = [j for j in b.plan_videos()[0] if j.name == "c1.a.t2v"]
    assert jobs and b.read_state("video", "c1.a.t2v")["state"] == "failed"  # planning changes nothing
    assert jobs[0].estimate == 5  # a retry is a new paid task, even though the old state has a task id
    b.execute(jobs)
    assert len(fake.submitted) == 1  # the old task id is not polled again; a new task is submitted
    assert b.read_state("video", "c1.a.t2v")["state"] == "done"


def test_summary_recommends_best_score_and_report_lists_results(bench):
    b, _ = bench
    case = b.cases[0]
    for pid in ("a", "b"):
        out = b.video_file(case, pid, "i2v")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        b.save_state("video", f"c1.{pid}.i2v", {"state": "done", "latency_s": 60, "est_cny": 5})
    b.save_state("video", "c1.a.t2v", {"state": "rejected", "message": "real person"})
    scores = {"video|c1|a|i2v": {"quality": 3, "motion": 3, "adherence": 3},
              "video|c1|b|i2v": {"quality": 5, "motion": 4, "adherence": 5, "usable": True}}
    text = summarize(b, scores)
    assert "| r | Beta | 3.00（1） | 4.67（1） |" in text
    assert "| r（文生视频） | — | — 拒1 | — |" in text
    html = write_report(b).read_text(encoding="utf-8")
    assert "video|c1|b|i2v" in html and "__DATA__" not in html


def test_source_urls_from_met_and_commons(bench):
    b, _ = bench

    def handler(req):
        if req.url.host == "collectionapi.metmuseum.org":
            return httpx.Response(200, json={"isPublicDomain": True, "primaryImage": "https://images.met/x.jpg"})
        assert req.url.params["titles"] == "File:Scroll.jpg"
        return httpx.Response(200, json={"query": {"pages": {"1": {"imageinfo": [{"url": "https://upload/s.jpg"}]}}}})

    def case(source):
        return Case(id="p", title="p", direction="", route="r", aspect="16:9", duration=5, modes=["i2v"], source=source)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert b._source_url(http, case({"met_object": 1})) == "https://images.met/x.jpg"
        assert b._source_url(http, case({"commons_file": "Scroll.jpg"})) == "https://upload/s.jpg"
        assert b._source_url(http, case({"url": "https://direct/y.jpg"})) == "https://direct/y.jpg"


def test_crop_windows_step_across_a_scroll(tmp_path):
    src = tmp_path / "scroll.png"
    im = Image.new("RGB", (1600, 100))
    for x in range(1600):
        for y in range(100):
            im.putpixel((x, y), (x * 255 // 1599, 0, 0))
    im.save(src)
    outs = [tmp_path / f"{k}.jpg" for k in "abc"]
    crop_windows(src, outs, "16:9", start=0.3, step=0.5)
    lefts = []
    for out in outs:
        with Image.open(out) as w:
            assert abs(w.width / w.height - 16 / 9) < 0.02
            lefts.append(w.getpixel((0, 50))[0])
    # Window width is 178 px, so each window starts ~89 px (≈14 red levels) right of the previous one.
    assert lefts[0] < lefts[1] < lefts[2]
    assert abs((lefts[2] - lefts[1]) - (lefts[1] - lefts[0])) <= 3


def test_keyframe_preference_list_skips_missing_providers(bench):
    b, _ = bench
    case = b.cases[0]
    b.options["keyframe_provider"] = ["seedream", "seedream_alt"]
    for pid in ("gpt_image", "seedream_alt"):
        path = b.keyframe_file(case, "a", pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8)).save(path)
    assert b.keyframe_for(case, "a").name == "a.seedream_alt.png"


def test_chain_takes_all_keyframes_from_one_provider(bench):
    b, _ = bench
    from ai_video.bench.cases import Keyframe, Segment
    case = b.cases[0]
    case.keyframes = [Keyframe("a"), Keyframe("b"), Keyframe("c")]
    case.segments = [Segment("a", "b", "p1"), Segment("b", "c", "p2")]
    b.options["keyframe_provider"] = ["seedream_alt"]

    def add(pid, ids):
        for kf in ids:
            path = b.keyframe_file(case, kf, pid)
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8)).save(path)

    add("seedream_alt", "a")           # preferred provider is missing b and c
    add("gpt_image", "ab")
    assert b.chain_keyframes(case) is None
    add("gpt_image", "c")
    assert {p.name for p in b.chain_keyframes(case).values()} == {"a.gpt_image.png", "b.gpt_image.png",
                                                                  "c.gpt_image.png"}
    add("seedream_alt", "bc")
    assert {p.name.split(".")[1] for p in b.chain_keyframes(case).values()} == {"seedream_alt"}


def test_upload_jpeg_is_capped_in_size_and_resolution(tmp_path):
    import os
    from ai_video.media import UPLOAD_BYTES, UPLOAD_SIDE, encode_jpeg
    src = tmp_path / "noisy.png"
    Image.frombytes("RGB", (2560, 1440), os.urandom(2560 * 1440 * 3)).save(src)
    data = encode_jpeg(src)
    import io
    with Image.open(io.BytesIO(data)) as im:
        assert max(im.size) == UPLOAD_SIDE
    small = encode_jpeg(src, max_side=640)
    assert len(small) <= UPLOAD_BYTES
