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


def _cjk_font() -> Path:
    candidates = (Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"), Path("data/fonts/LXGWWenKai-Regular.ttf"),
                  Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), Path("/System/Library/Fonts/STHeiti Medium.ttc"))
    font = next((p for p in candidates if p.exists()), None)
    if font is None:
        pytest.skip("no CJK font available")
    return font


@pytest.mark.skipif(not compose.can_burn_subtitles(), reason="needs ffmpeg built with libass (subtitles filter)")
def test_compose_builds_a_final_video(tmp_path):
    font = _cjk_font()
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



# ---------------------------------------------------------------- drama (template: drama)

DRAMA = {
    "prompt": "杜甫 弃官 秦州", "template": "drama", "aspect": "16:9", "seconds": 13, "style": "film-history",
    "title": "杜甫弃官", "cover_title": "弃官", "letterbox": 2.35, "notice": "剧情演绎", "credits": ["导演：测试"],
    "characters": [{"id": "dufu", "name": "杜甫", "look": "四十七八岁，清瘦，青灰长袍",
                    "voice_desc": "四十七八岁，嗓音低沉略沙哑", "tts_voice": "elder_male"}],
    "scenes": [{"id": "road", "name": "陇山道", "look": "深秋黄土山道"}],
    "check": {"ok": True, "issues": []},
    "shots": [
        {"id": "s01", "seconds": 6, "voice": "dialogue", "speaker": "dufu", "line": "这官，不做了", "caption": "（759 年，杜甫弃官）",
         "label": "杜甫", "image_prompt": "官署门口", "video_prompt": "他把官服放下", "camera": "固定", "refs": ["dufu"]},
        {"id": "s02", "seconds": 3, "voice": "ambient", "transition": "dissolve", "mode": "ref", "refs": ["dufu", "road"],
         "video_prompt": "他牵马走在山道上", "camera": "横移"},
        {"id": "c01", "kind": "card", "text": "露从今夜白，月是故乡明", "seconds": 4, "over": "s02", "transition": "black"},
    ],
}


def _drama(**changes) -> dict:
    data = copy.deepcopy(DRAMA)
    for path, value in changes.items():
        target, keys = data, path.split("__")
        for key in keys[:-1]:
            target = target[int(key)] if key.isdigit() else target[key]
        last = keys[-1]
        target[int(last) if last.isdigit() else last] = value
    return data


def test_drama_import_keeps_every_field_and_writes_a_drama_storyboard(producer):
    ws, warnings = producer.new_from_script(DRAMA)
    project = ws.load()
    assert project.is_drama and project.letterbox == 2.35 and project.credits == ["导演：测试"]
    assert project.characters[0]["voice_desc"] == "四十七八岁，嗓音低沉略沙哑" and project.scenes[0]["id"] == "road"
    s01, s02, c01 = project.shots
    assert (s01.voice, s01.speaker, s01.line, s01.label, s01.seconds) == ("dialogue", "dufu", "这官，不做了", "杜甫", 6)
    assert (s02.mode, s02.transition, s02.voice, s02.needs_keyframe) == ("ref", "dissolve", "ambient", False)
    assert c01.is_card and c01.over == "s02" and c01.voice == "none" and not c01.needs_keyframe
    assert any("不到 4 秒" in w for w in warnings)
    board = (ws.root / "storyboard.md").read_text(encoding="utf-8")
    assert "剧情片" in board and "杜甫：「这官，不做了」" in board and "【卡片】露从今夜白" in board and "叠化" in board


@pytest.mark.parametrize("changes,message", [
    ({"shots__0__seconds": None}, "seconds"),
    ({"shots__0__line": ""}, "缺少 line"),
    ({"shots__0__speaker": "nobody"}, "speaker"),
    ({"shots__1__refs": []}, "参考模式"),
    ({"shots__1__refs": ["dufu", "lake"]}, "没有声明"),
    ({"shots__2__text": ""}, "金句卡"),
    ({"shots__2__over": "s09"}, "over"),
    ({"shots__1__voice": "music"}, "voice"),
    ({"shots__1__transition": "wipe"}, "transition"),
    ({"letterbox": 9}, "letterbox"),
    ({"scenes": [{"id": "dufu", "look": "x"}]}, "重复"),
])
def test_drama_hard_errors(changes, message):
    assert any(message in e for e in hard_errors(_drama(**changes)))


def test_cards_are_drama_only_and_prev_needs_a_keyframe_before_it():
    narration = copy.deepcopy(SCRIPT)
    narration["shots"].append({"kind": "card", "text": "x", "seconds": 3})
    assert any("只有剧情片" in e for e in hard_errors(narration))
    data = _drama()
    data["shots"].append({"id": "s03", "seconds": 5, "image_prompt": "x", "video_prompt": "y", "refs": ["prev"]})
    assert any("上一个镜头没有关键帧" in e for e in hard_errors(data))  # the shot before is a card


def test_drama_diff_redoes_only_what_a_change_touches(producer):
    old = Project.from_dict(json.loads(json.dumps(_project_dict(producer, DRAMA))))
    def changed(**kw):
        new = Project.from_dict(json.loads(json.dumps(_project_dict(producer, _drama(**kw)))))
        return diff_script(old, new)
    assert not changed(**{"shots__0__caption": "（新注释）", "shots__2__text": "新卡片", "shots__1__transition": "cut"})
    ch = changed(**{"shots__0__line": "这官，我不做了"})
    assert ch.audio == ["s01"] and not ch.keyframes and not ch.clips
    ch = changed(**{"scenes": [{"id": "road", "name": "陇山道", "look": "初冬雪后山道"}]})
    assert ch.scenes == ["road"] and ch.keyframes == ["s02"]
    ch = changed(**{"characters__0__voice_desc": "五十岁，嗓音苍老"})
    assert ch.audio == ["s01"]  # the speaker's dialogue clip is remade with the new voice
    assert changed(**{"shots__1__seconds": 5}).clips == ["s02"]


def _project_dict(producer, data) -> dict:
    from dataclasses import asdict
    from ai_video.pipeline.script import apply_script
    project = Project(id="p", prompt=data["prompt"], aspect=data["aspect"], seconds=data["seconds"], style=data["style"])
    apply_script(project, data)
    return asdict(project)


def test_drama_video_prompt_and_request(producer):
    ws, _ = producer.new_from_script(DRAMA)
    project = ws.load()
    s01, s02, _card = project.shots
    prompt = producer.video_prompt(project, s01)
    assert prompt.startswith("真实历史电影实拍质感")  # film-history video_prefix
    assert "他把官服放下。杜甫（四十七八岁，嗓音低沉略沙哑）说：「这官，不做了。」。镜头固定。" in prompt
    assert prompt.rstrip("。").endswith("画面中不要出现任何文字、字幕和水印")
    assert "画面中无人说话" in producer.video_prompt(project, s02)
    req = producer.video_request(ws, project, s01)
    assert req.first_frame == ws.keyframe(s01) and req.audio and req.mode == "i2v"
    req = producer.video_request(ws, project, s02)
    assert req.mode == "r2v" and req.first_frame is None and req.audio
    assert req.ref_images == ws.character_files("dufu") + [ws.scene("road")]
    producer.routing["dialogue"] = "tts"
    assert "说：" not in producer.video_prompt(project, s01) and "无人说话" in producer.video_prompt(project, s01)


def test_drama_audio_stage_sets_lengths_and_speaks_only_on_the_tts_route(producer, monkeypatch):
    ws, _ = producer.new_from_script(DRAMA)
    project = ws.load()
    said = []
    tts = producer.providers["tts"]["mm"]
    monkeypatch.setattr(tts, "synthesize", lambda http, req: said.append(req) or b"ID3")
    monkeypatch.setattr("ai_video.pipeline.stages.probe_duration", lambda path: 1.5)
    assert producer.stage_audio(ws, project) == [] and said == []  # native: the video model speaks
    project = ws.load()
    assert [s.duration for s in project.shots] == [6, 4, 0]  # 3 s is made at Seedance's 4 s minimum
    producer.routing["dialogue"] = "tts"
    producer.stage_audio(ws, project)
    assert [(r.text, r.voice) for r in said] == [("这官，不做了", "elder_male")]


def test_drama_estimate_skips_cards_and_reference_keyframes(producer):
    ws, _ = producer.new_from_script(DRAMA)
    cost = producer.estimate(ws, ws.load())
    # characters: body + face (its one reference is free) + the scene; keyframes: s01 only
    # (s02 is reference mode, c01 a card)
    assert cost["characters"][1]["ark"] == 150 + 150 + 150
    assert cost["keyframes"][1]["ark"] == 150 + 10
    seedance = producer.providers["video"]["seedance"]
    clips = sum(seedance.estimate_afp(VideoRequest("", duration=d, ratio="16:9")) for d in (6, 4))
    assert cost["videos"][1]["ark"] == pytest.approx(clips)


def test_scene_references_are_drawn_and_fed_to_keyframes(producer, fake_image):
    data = _drama(**{"shots__0__refs": ["dufu", "road"]})
    ws, _ = producer.new_from_script(data)
    project = ws.load()
    assert producer.stage_characters(ws, project) == []
    scene = next(r for r in fake_image if "没有任何人物" in r.prompt)  # drawn alongside the character sheets
    assert ws.scene("road").exists() and scene.ratio == "16:9" and "深秋黄土山道" in scene.prompt
    assert producer.stage_keyframes(ws, project) == []
    assert not ws.keyframe(project.shots[1]).exists()  # reference mode: no keyframe
    assert fake_image[-1].refs == ws.character_files("dufu") + [ws.scene("road")]
    assert "场景「陇山道」" in fake_image[-1].prompt
    assert "电影黑边" in fake_image[-1].prompt  # letterbox: keep heads out of the bars


def test_drama_timeline_overlaps_transitions():
    from ai_video.pipeline import drama
    project = Project.from_dict(_project_dict(None, DRAMA))
    lengths, starts, overlaps = drama.timeline(project)
    assert lengths == [6.0, 3.0, 4.0]
    assert overlaps[1] == drama.DISSOLVE and overlaps[2] == pytest.approx(1.0)
    assert starts == pytest.approx([0.0, 6.0 - drama.DISSOLVE, 9.0 - drama.DISSOLVE - 1.0], abs=1e-3)
    for shot in project.shots:
        shot.transition = "cut"
    assert drama.timeline(project)[2] == [0.0, 0.0, 0.0]  # only cuts: plain concatenation


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_drama_join_keeps_every_shot_through_a_long_chain(tmp_path):
    """Regression: cuts were one-frame xfades, and two in a row ended a 90 s film after 22 s.
    This is that film's opening."""
    from ai_video.pipeline import drama
    cuts = [(6, "cut"), (6, "cut"), (6, "dissolve"), (5, "cut"), (7, "cut"), (6, "black"), (5, "cut")]
    project = Project(id="p", prompt="x", template="drama", shots=[
        Shot(id=f"s{i}", narration="", image_prompt="x", video_prompt="x", seconds=s, transition=t)
        for i, (s, t) in enumerate(cuts)])
    lengths, starts, overlaps = drama.timeline(project)
    for i in range(1, len(starts)):
        assert starts[i] + overlaps[i] == pytest.approx(starts[i - 1] + lengths[i - 1], abs=1e-9)
    names = []
    for i, length in enumerate(lengths):
        names.append(f"v{i}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r={compose.FPS}",
                        "-frames:v", str(round(length * compose.FPS)), "-pix_fmt", "yuv420p", str(tmp_path / names[-1])],
                       check=True)
    drama._join(names, lengths, starts, overlaps, [t for _, t in cuts], tmp_path)
    assert probe_duration(tmp_path / "video.mp4") == pytest.approx(starts[-1] + lengths[-1], abs=0.05)


def test_quote_cards_break_at_clauses_without_commas():
    from ai_video.pipeline.drama import card_rows
    assert card_rows("莫道不销魂，帘卷西风，人比黄花瘦。") == ["莫道不销魂", "帘卷西风", "人比黄花瘦"]
    assert card_rows("谁怕？一蓑烟雨任平生") == ["谁怕？一蓑烟雨任平生"]
    assert card_rows("一二三四五六七八九十一二三四五六七八") == ["一二三四五六七八九十一二三四五六", "七八"]


def test_texts_of_neighbouring_shots_never_share_the_screen():
    from ai_video.pipeline import drama
    data = copy.deepcopy(DRAMA)
    data["shots"].append({"id": "c02", "kind": "card", "text": "戍鼓断人行，边秋一雁声", "seconds": 4,
                          "over": "s02", "transition": "dissolve"})
    project = Project.from_dict(_project_dict(None, data))
    lengths, starts, overlaps = drama.timeline(project)
    ass = drama.build_ass(project, "LXGW WenKai", "Ma Shan Zheng", lengths, starts, 1920, 1080)
    cards = [line.split(",")[1:3] for line in ass.splitlines() if ",Card,," in line]
    assert len(cards) == 2 and cards[0][1] <= cards[1][0]  # the first card is gone before the second shows
    assert cards[1][0] == compose.ass_time(starts[2] + lengths[2])  # after the dissolve into it
    caption_end = next(line.split(",")[2] for line in ass.splitlines() if ",Caption,," in line)
    assert caption_end <= compose.ass_time(starts[1])  # s01's note is off before s02 dissolves in


def test_drama_subtitles_are_layered():
    from ai_video.pipeline import drama
    project = Project.from_dict(_project_dict(None, DRAMA))
    lengths, starts, _ = drama.timeline(project)
    ass = drama.build_ass(project, "LXGW WenKai", "Ma Shan Zheng", lengths, starts, 1920, 1080)
    bar = drama.letterbox_bar(project, 1920, 1080)
    assert bar == 131
    for name in ("Line", "Caption", "Chapter", "Name", "Card", "Notice", "Credit", "AILabel"):
        assert f"Style: {name}," in ass
    assert "Style: Card,Ma Shan Zheng," in ass
    assert ass.count(",Chapter,,") == 1 and "杜甫" in ass.split(",Chapter,,")[1]
    assert ",Line,,0,0,0,,这官，不做了" in ass and "（759 年，杜甫弃官）" in ass
    assert "露从今夜白\\N月是故乡明" in ass.split(",Card,,")[1]
    assert "剧\\N情\\N演\\N绎" in ass and "《弃官》" in ass and "导演：测试" in ass
    srt = drama.build_srt(project, lengths, starts)
    assert "这官，不做了" in srt and "露从今夜白" in srt


def _drama_media(tmp_path) -> tuple[Workspace, Project]:
    ws = Workspace(tmp_path / "proj")
    project = Project.from_dict(_project_dict(None, DRAMA))
    s01, s02, _card = project.shots
    ws.clip(s01).parent.mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=6.5:size=640x360:rate=24", "-f", "lavfi", "-i",
            "sine=frequency=330:duration=6.5", "-shortest", ws.clip(s01))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=duration=4:size=640x360:rate=24", "-f", "lavfi", "-i",
            "sine=frequency=550:duration=4", "-shortest", ws.clip(s02))
    ws.keyframe(s01).parent.mkdir(parents=True)
    Image.new("RGB", (640, 360), "darkred").save(ws.keyframe(s01))
    return ws, project


def _mean_volume(path: Path, start: float, seconds: float) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(seconds), "-i", str(path),
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(err.split("mean_volume:")[1].split("dB")[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_drama_pictures_transitions_and_sound_buses(tmp_path):
    """Everything before the subtitle burn, which needs libass (missing from slim ffmpeg builds)."""
    from ai_video.pipeline import drama
    ws, project = _drama_media(tmp_path)
    build = ws.build
    build.mkdir(parents=True)
    lengths, starts, overlaps = drama.timeline(project)
    names = []
    for shot, length in zip(project.shots, lengths):
        drama._picture(project, ws, shot, build / f"v_{shot.id}.mp4", length, 1920, 1080)
        names.append(f"v_{shot.id}.mp4")
    assert (build / "last_s02.png").exists()  # the card sits on s02's last frame
    drama._join(names, lengths, starts, overlaps, [s.transition for s in project.shots], build)
    total = starts[-1] + lengths[-1]
    assert abs(probe_duration(build / "video.mp4") - total) < 0.1
    drama._sound(project, ws, lengths, starts, total, build, None, 0.25)
    assert abs(probe_duration(build / "mix.wav") - total) < 0.1
    assert _mean_volume(build / "speech.wav", 1, 4) > -40      # s01 (dialogue): its own sound is speech
    assert _mean_volume(build / "speech.wav", 7, 1.5) < -80    # s02 (ambient) is not on the speech bus
    assert _mean_volume(build / "ambience.wav", 6, 1.5) > -40
    assert _mean_volume(build / "mix.wav", total - 2, 1.5) < -80  # the card is silent (no BGM given)


@pytest.mark.skipif(not compose.can_burn_subtitles(), reason="needs ffmpeg built with libass (subtitles filter)")
def test_drama_compose_builds_a_letterboxed_film(tmp_path):
    from ai_video.pipeline import drama
    font = _cjk_font()
    ws, project = _drama_media(tmp_path)
    final = compose.render(project, ws, "Hiragino Sans GB", font, card_font=("Hiragino Sans GB", font))
    lengths, starts, _ = drama.timeline(project)
    assert abs(probe_duration(final) - (starts[-1] + lengths[-1])) < 0.25
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                              "-of", "csv=p=0", str(final)], capture_output=True, text=True).stdout
    assert "video,1920,1080" in streams and "audio" in streams
    with Image.open(_grab(final, 2.0, tmp_path / "f.png")) as im:
        assert im.convert("L").crop((0, 0, 1920, 120)).getextrema()[1] < 40  # black bar on top
    assert (ws.out / "cover.png").exists() and "露从今夜白" in (ws.out / "final.srt").read_text(encoding="utf-8")


def test_compose_refuses_an_ffmpeg_that_cannot_burn_subtitles(monkeypatch, tmp_path):
    monkeypatch.setattr(compose, "can_burn_subtitles", lambda: False)
    with pytest.raises(RuntimeError, match="libass"):
        compose.render(Project(id="p", prompt="x"), Workspace(tmp_path), "f", tmp_path / "f.ttf")


def _grab(video: Path, at: float, out: Path) -> Path:
    _ffmpeg("-ss", at, "-i", video, "-frames:v", 1, out)
    return out
