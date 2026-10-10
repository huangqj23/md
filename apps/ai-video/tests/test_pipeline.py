import argparse
import copy
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ai_video.cli import _run_make
from ai_video.config import Settings
from ai_video.media import probe_duration
from ai_video.pipeline import compose
from ai_video.pipeline.project import Project, Shot, Workspace
from ai_video.pipeline.script import diff_script, hard_errors, load_script
from ai_video.pipeline.stages import Producer
from ai_video.providers.base import TaskStatus, VideoProvider, VideoRequest

PROVIDERS = """
plans:
  ark: {label: Ark, cny_per_afp: 0.002, daily_afp: 125000, monthly_afp: 250000}
video:
  seedance:
    adapter: ark_video
    label: Seedance
    plan: ark
    key_env: FAKE_KEY
    base_url: https://fake
    model: seedance
    resolution: 720p
    afp_coef: 230
  h3:
    adapter: minimax_video
    label: H3
    key_env: FAKE_KEY
    base_url: https://fake
    model: MiniMax-H3
    price_cny_per_second: 0.5
  kling:
    adapter: kling_video
    label: Kling
    key_env: FAKE_KEY
    base_url: https://fake
    model: kling-v3
    price_cny_per_second: 1.0
image:
  seedream:
    adapter: ark_image
    label: Seedream
    plan: ark
    key_env: FAKE_KEY
    base_url: https://fake
    model: seedream
    afp_per_image: 150
    afp_per_extra_ref: 10
tts:
  mm:
    adapter: minimax_tts
    label: MiniMax
    key_env: FAKE_KEY
    base_url: https://fake
    model: speech-2.8-hd
    voices: [presenter_male]
routing:
  image: seedream
  video: {default: seedance, photoreal: h3}
  tts: {provider: mm, voice: presenter_male}
bench:
  poll_seconds: 0
  timeout_minutes: 1
"""

SCRIPT = {
    "prompt": "霜降 古诗 水墨", "aspect": "9:16", "seconds": 30, "style": "guofeng-ink",
    "title": "霜降：秋天最后的告别", "hook": "你知道霜从哪里来吗？", "cover_title": "霜降",
    "tags": ["#霜降", "节气"], "source": {"title": "山行", "author": "杜牧", "text": "停车坐爱枫林晚，霜叶红于二月花。"},
    "characters": [{"id": "girl", "name": "执伞女子", "look": "二十岁左右，月白色长裙，撑油纸伞"}],
    "check": {"ok": True, "issues": []},
    "shots": [
        {"narration": "你知道霜从哪里来吗？", "image_prompt": "执伞女子站在结霜的枫林里", "video_prompt": "霜花慢慢凝结",
         "camera": "推近", "refs": ["girl"]},
        {"narration": "停车坐爱枫林晚，霜叶红于二月花。", "image_prompt": "同一条枫林小路的远景",
         "video_prompt": "女子缓步前行", "camera": "跟拍", "photoreal": True, "refs": ["prev"]},
    ],
}


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 18), "gray").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def producer(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")
    (tmp_path / "providers.yaml").write_text(PROVIDERS, encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", providers_file=tmp_path / "providers.yaml",
                        cases_file=tmp_path / "cases.yaml", budget_cny=100)
    return Producer(settings)


@pytest.fixture
def fake_image(producer):
    """Record image requests instead of calling Seedream."""
    img = producer.providers["image"]["seedream"]
    calls = []

    def generate(http, req):
        calls.append(req)
        return png_bytes()

    img.generate = generate
    return calls


def test_script_import_builds_project_and_storyboard(producer, tmp_path):
    path = tmp_path / "script.json"
    path.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8-sig")  # BOM from Windows editors is fine
    ws, warnings = producer.new_from_script(load_script(path))
    project = ws.load()
    assert [s.id for s in project.shots] == ["s01", "s02"] and project.shots[1].refs == ["prev"]
    assert project.tags == ["霜降", "节气"] and project.source["author"] == "杜牧"
    assert project.characters == [{"id": "girl", "name": "执伞女子", "look": "二十岁左右，月白色长裙，撑油纸伞"}]
    assert project.check == {"ok": True, "issues": [], "checker": "Claude"}
    assert project.aspect == "9:16" and project.seconds == 30
    assert any("镜头数" in w for w in warnings)  # 2 shots is below the range for 30 seconds
    board = (ws.root / "storyboard.md").read_text(encoding="utf-8")
    assert "## 角色" in board and "| s02 | 停车坐爱枫林晚" in board and "| prev |" in board


def _mutate(path: str, value):
    data = copy.deepcopy(SCRIPT)
    target = data
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[int(key)] if key.isdigit() else target[key]
    last = keys[-1]
    if last.isdigit():
        target[int(last)] = value
    else:
        target[last] = value
    return data


@pytest.mark.parametrize("path,value,message", [
    ("prompt", "", "prompt"),
    ("aspect", "4:5", "aspect"),
    ("shots.0.camera", "缓慢推近", "camera"),
    ("shots.0.refs", ["nobody"], "没有声明"),
    ("shots.0.refs", ["prev"], "prev"),
    ("characters", [{"id": "S1", "look": "x"}], "角色"),
    ("characters", [{"id": "girl"}], "look"),
    ("shots.0.image_prompt", " ", "image_prompt"),
    ("shots", [SCRIPT["shots"][0]], "至少要有 2 个镜头"),
])
def test_script_import_rejects_hard_errors(producer, path, value, message):
    data = _mutate(path, value)
    assert any(message in e for e in hard_errors(data))
    with pytest.raises(ValueError, match="剧本有问题"):
        producer.new_from_script(data)


def test_too_many_characters_in_one_shot():
    data = copy.deepcopy(SCRIPT)
    data["characters"] += [{"id": "boy", "look": "x"}, {"id": "monk", "look": "y"}]
    data["shots"][0]["refs"] = ["girl", "boy", "monk"]
    assert any("最多引用" in e for e in hard_errors(data))


def test_make_without_script_points_to_the_skill(producer):
    ws = producer.create("霜降", "9:16", 30, "guofeng-ink")
    with pytest.raises(ValueError, match="short-video"):
        producer.make(ws, until="script")


def test_projects_created_in_the_same_second_get_their_own_folder(producer, monkeypatch):
    monkeypatch.setattr("ai_video.pipeline.stages.new_project_id", lambda: "p20261009-120000")
    a = producer.create("一", "9:16", 30, "guofeng-ink")
    b = producer.create("二", "9:16", 30, "guofeng-ink")
    assert a.root != b.root and b.root.name == "p20261009-120000-2" and a.load().prompt == "一"


def test_character_sheets_then_keyframes_with_refs(producer, fake_image):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    assert producer.stage_characters(ws, project) == []
    body, face = ws.character_files("girl")
    assert body.exists() and face.exists()
    assert [r.ratio for r in fake_image] == ["3:4", "1:1"] and fake_image[1].refs == [body]
    assert "留白构图" not in fake_image[0].prompt  # scene words of the style stay out of character references
    assert producer.stage_keyframes(ws, project) == []
    s01, s02 = project.shots
    assert fake_image[2].refs == [body, face] and "图1、图2是人物「执伞女子」" in fake_image[2].prompt
    assert fake_image[3].refs == [ws.keyframe(s01)] and "上一个镜头" in fake_image[3].prompt  # drawn after s01
    afp = [e["afp"] for e in producer.ledger.entries() if e.get("kind") == "image"]
    assert afp == [150, 150, 160, 150] and all(e["plan"] == "ark" for e in producer.ledger.entries())


def test_keyframe_waits_for_missing_references(producer, fake_image):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    problems = producer.stage_keyframes(ws, project)  # no character sheets yet
    assert len(problems) == 2 and "girl_body.png" in problems[0] and fake_image == []


def test_estimate_counts_sheets_extra_refs_and_clip_afp(producer):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    cost = producer.estimate(ws, project)
    assert cost["characters"][1] == {"ark": 300}
    assert cost["keyframes"][1] == {"ark": 310}            # s01 with two reference images, s02 with one
    seedance = producer.providers["video"]["seedance"]
    duration = producer.fit_duration(seedance, len(project.shots[0].narration) / 4.2)
    expected = seedance.cost(VideoRequest("", duration=duration, ratio="9:16"))
    assert cost["videos"][1]["ark"] == pytest.approx(expected[1])   # the photoreal shot goes to H3: ¥ only
    assert cost["videos"][0] == pytest.approx(expected[0] + 0.5 * producer.fit_duration(
        producer.providers["video"]["h3"], len(project.shots[1].narration) / 4.2), abs=0.01)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def _all_files(ws: Workspace, project: Project) -> None:
    for cid in ("girl",):
        for path in ws.character_files(cid):
            _touch(path)
    for shot in project.shots:
        for path in (ws.audio(shot), ws.keyframe(shot), ws.clip(shot)):
            _touch(path)


def test_import_script_redoes_only_what_changed(producer):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    _all_files(ws, project)
    s01, s02 = project.shots
    revised = copy.deepcopy(SCRIPT)
    revised["shots"][0]["narration"] = "霜，是从哪里来的？"
    revised["shots"][1]["video_prompt"] = "女子停下脚步回望"
    changes, _ = producer.import_script(ws, revised)
    assert changes.audio == ["s01"] and changes.clips == ["s02"] and not changes.keyframes
    assert not ws.audio(s01).exists() and not ws.clip(s01).exists() and ws.keyframe(s01).exists()
    assert ws.audio(s02).exists() and ws.keyframe(s02).exists() and not ws.clip(s02).exists()
    assert ws.load().shots[0].narration == "霜，是从哪里来的？"

    _all_files(ws, ws.load())
    revised["characters"][0]["look"] = "二十岁左右，绯红色长裙，撑油纸伞"
    changes, _ = producer.import_script(ws, revised)
    assert changes.characters == ["girl"] and changes.keyframes == ["s01", "s02"]  # s02 follows s01 via prev
    assert not any(p.exists() for p in ws.character_files("girl"))
    assert not ws.keyframe(s02).exists() and ws.audio(s02).exists()


def test_diff_drops_removed_shots_and_redoes_shots_whose_prev_changed():
    old = Project(id="p", prompt="x", shots=[Shot("s01", "a", "i1", "v1"), Shot("s02", "b", "i2", "v2"),
                                             Shot("s03", "c", "i3", "v3", refs=["prev"])])
    new = Project(id="p", prompt="x", shots=[Shot("s01", "a", "i1", "v1"), Shot("s03", "c", "i3", "v3", refs=["prev"])])
    changes = diff_script(old, new)
    assert changes.removed == ["s02"] and changes.keyframes == ["s03"]


def test_redo_character_clears_its_sheets_and_keyframes(producer):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    _all_files(ws, project)
    assert ws.clear_character(project, "girl") == ["s01"]
    assert not any(p.exists() for p in ws.character_files("girl")) and not ws.keyframe(project.shots[0]).exists()
    assert ws.audio(project.shots[0]).exists() and ws.keyframe(project.shots[1]).exists()


def test_make_estimate_prices_redo_and_revideo_without_deleting_anything(producer, capsys):
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    _all_files(ws, project)
    project.shots[0].provider = "seedance"
    ws.save(project)
    args = argparse.Namespace(draft=False, redo="girl", revideo="s02", until="compose", estimate=True,
                              budget=None, yes=False, bgm=None)
    assert _run_make(producer, ws, args, producer.settings) == 0
    assert all(p.exists() for p in ws.character_files("girl"))
    assert all(p.exists() for s in project.shots for p in (ws.audio(s), ws.keyframe(s), ws.clip(s)))
    assert ws.load().shots[0].provider == "seedance"
    out = capsys.readouterr().out
    assert "将清除角色 girl" in out and "将清除 s02 的视频" in out and "已清除" not in out
    assert "角色参考图 ¥0.6" in out and "视频 ¥" in out     # the two sheets and the cleared clips are priced


def test_clip_length_fits_each_provider(producer):
    seedance, kling = producer.providers["video"]["seedance"], producer.providers["video"]["kling"]
    assert producer.fit_duration(seedance, 2.0) == 4      # Seedance minimum is 4 s
    assert producer.fit_duration(seedance, 6.2) == 7
    assert producer.fit_duration(kling, 6.2) == 10       # without a duration range, Kling takes 5 or 10


class FakeVideo(VideoProvider):
    def __init__(self, pid, cfg, refuse=False):
        super().__init__(pid, cfg)
        self.refuse = refuse
        self.calls = 0

    def submit(self, http, req):
        self.calls += 1
        if self.refuse:
            from ai_video.errors import Rejected
            raise Rejected("may contain real person")
        return "t1"

    def poll(self, http, task_id):
        return TaskStatus("done", url="https://cdn/x.mp4")


def test_refused_shot_falls_back_to_the_photoreal_model(producer, tmp_path):
    vids = producer.providers["video"]
    vids["seedance"] = FakeVideo("seedance", vids["seedance"].cfg, refuse=True)
    vids["h3"] = FakeVideo("h3", vids["h3"].cfg)
    producer.fetch = lambda http, url, out: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b"mp4"))
    ws = producer.create("霜降", "9:16", 30, "guofeng-ink")
    project = ws.load()
    shot = Shot(id="s01", narration="旁白", image_prompt="枫林", video_prompt="风吹", duration=5)
    project.shots = [shot]
    ws.keyframe(shot).parent.mkdir(parents=True)
    Image.new("RGB", (32, 18)).save(ws.keyframe(shot))
    assert producer._make_clip(ws, project, shot) is None
    assert shot.provider == "h3" and ws.clip(shot).exists()
    assert vids["seedance"].calls == 1 and vids["h3"].calls == 1
    assert json.loads(ws.job(shot, "seedance").read_text(encoding="utf-8"))["state"] == "rejected"
    charged = [e for e in producer.ledger.entries() if e.get("kind") == "video"]
    assert [e["provider"] for e in charged] == ["h3"] and charged[0]["cny"] == 2.5


def test_subtitles_wrap_and_carry_title_and_ai_label():
    assert compose.wrap("霜降是秋天的最后一个节气，过了它就要立冬", 13) == ["霜降是秋天的最后一个节气", "过了它就要立冬"]
    assert compose.wrap("霜降，是秋天的最后一个节气，过了它就要立冬了", 13) == ["霜降，是秋天的最后一个节气", "过了它就要立冬了"]
    assert compose.clean_line("停车坐爱枫林晚<long pause>，霜叶红于二月花。") == "停车坐爱枫林晚，霜叶红于二月花"
    project = Project(id="p", prompt="x", aspect="9:16", cover_title="霜降",
                      shots=[Shot("s01", "你知道霜从哪里来吗？", "", "", audio_s=2.0),
                             Shot("s02", "停车坐爱枫林晚。", "", "", audio_s=3.0)])
    ass = compose.build_ass(project, "LXGW WenKai", compose.offsets(compose.slots(project)))
    assert "Style: Sub,LXGW WenKai" in ass and ",Title,," in ass and "AI生成" in ass
    assert "停车坐爱枫林晚\n" in ass or "停车坐爱枫林晚" in ass.split("Sub,,0,0,0,,")[-1]
    assert "Dialogue: 0,0:00:02.33,0:00:05.43,Sub" in ass  # 2.35 s rounded to 56 frames at 24 fps
    assert all(abs(s * compose.FPS - round(s * compose.FPS)) < 1e-6 for s in compose.slots(project))
    assert compose.clean_line("当 v<c 且 m>0 时，速度变慢<#0.5#>。") == "当 v<c 且 m>0 时，速度变慢"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_compose_builds_a_final_video(tmp_path):
    font = next((p for p in (Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"), Path("data/fonts/LXGWWenKai-Regular.ttf"))
                 if p.exists()), None)
    if font is None:
        pytest.skip("no CJK font available")
    ws = Workspace(tmp_path / "proj")
    project = Project(id="p", prompt="霜降", aspect="9:16", title="霜降", cover_title="霜降", tags=["霜降"],
                      shots=[Shot("s01", "你知道霜从哪里来吗？", "", ""), Shot("s02", "停车坐爱枫林晚。", "", "")])
    for shot, seconds in zip(project.shots, (1.2, 1.6)):
        ws.audio(shot).parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", ws.audio(shot))
        shot.audio_s = probe_duration(ws.audio(shot))
    ws.clip(project.shots[0]).parent.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=24", ws.clip(project.shots[0]))  # shorter than its line
    ws.keyframe(project.shots[1]).parent.mkdir(parents=True)
    Image.new("RGB", (720, 1280), "darkred").save(ws.keyframe(project.shots[1]))  # no clip: still push-in
    final = compose.render(project, ws, "Noto Sans SC", font)
    expected = sum(compose.slots(project))
    assert abs(probe_duration(final) - expected) < 0.25
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                              "-of", "csv=p=0", str(final)], capture_output=True, text=True).stdout
    assert "video,1080,1920" in streams and "audio" in streams
    assert "AI 生成内容" in (ws.out / "publish.md").read_text(encoding="utf-8")
    assert "停车坐爱枫林晚" in (ws.out / "final.srt").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_contact_sheet_marks_missing_keyframes(producer):
    from ai_video.pipeline.sheet import contact_sheet
    ws, _ = producer.new_from_script(SCRIPT)
    project = ws.load()
    _touch(ws.keyframe(project.shots[0]))
    Image.new("RGB", (90, 160), "navy").save(ws.keyframe(project.shots[0]))
    sheet = contact_sheet(ws, project, "keyframes")
    with Image.open(sheet) as im:
        assert im.width > 0 and im.height > 0


def test_tts_voice_must_belong_to_the_routed_model(producer):
    assert producer.tts[1] == "presenter_male"
    producer.routing["tts"]["voice"] = "Charon"  # a Gemini voice left behind after switching to MiniMax
    with pytest.raises(ValueError, match="Charon"):
        _ = producer.tts


def test_draft_mode_keeps_photoreal_shots_on_the_photoreal_model(producer):
    producer.routing["video"]["draft"] = "kling"
    producer.providers["video"]["kling"].cfg["secret_env"] = "FAKE_KEY"
    producer.draft = True
    assert producer.video_provider(Shot("s01", "n", "i", "v", photoreal=True)).id == "h3"
    assert producer.video_provider(Shot("s02", "n", "i", "v")).id == "kling"


def test_cover_keeps_the_keyframe_format_and_the_final_carries_the_aigc_label(tmp_path):
    jpeg = tmp_path / "s01.png"  # Seedream returns JPEG bytes, stored under the keyframe's .png name
    Image.new("RGB", (36, 64), "navy").save(jpeg, "JPEG")
    out = tmp_path / "out"
    out.mkdir()
    (out / "cover.png").write_bytes(b"old")
    assert compose.copy_cover(jpeg, out).name == "cover.jpg" and not (out / "cover.png").exists()
    label = json.loads(compose.aigc_label(Project(id="p20261009-120000", prompt="x")))
    assert label["Label"] == "1" and label["ProduceID"] == "p20261009-120000"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_still_clip_from_a_jpeg_keyframe_is_limited_range(tmp_path):
    jpeg = tmp_path / "s01.png"  # Seedream JPEG bytes under the keyframe's .png name
    Image.new("RGB", (90, 160), (230, 200, 120)).save(jpeg, "JPEG")
    out = tmp_path / "still.mp4"
    compose._still_clip(jpeg, out, 0.5, 108, 192)
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=pix_fmt,color_range,nb_frames", "-of", "json", str(out)],
                          capture_output=True, text=True).stdout
    stream = json.loads(info)["streams"][0]
    assert stream["pix_fmt"] == "yuv420p" and stream.get("color_range", "tv") == "tv"
    assert int(stream["nb_frames"]) == 12  # 0.5 s at 24 fps, cut by frame count


def test_pronunciations_are_validated_and_a_new_reading_redoes_only_affected_audio(producer):
    data = copy.deepcopy(SCRIPT)
    data["pronunciations"] = {"枫林": "(feng1)"}  # one syllable for two characters
    assert any("pronunciations" in e for e in hard_errors(data))
    data["pronunciations"] = {"枫林": "(feng1)(lin2)"}
    ws, _ = producer.new_from_script(data)
    project = ws.load()
    assert project.pronunciations == {"枫林": "(feng1)(lin2)"}
    _all_files(ws, project)
    data["pronunciations"] = {"枫林": "(feng2)(lin2)"}
    changes, _ = producer.import_script(ws, data)
    assert changes.audio == ["s02"]  # only s02's narration contains 枫林
