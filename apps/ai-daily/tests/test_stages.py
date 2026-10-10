"""不调用模型的分步流程：prepare → plan.json → materials → Claude 写正文 → finalize。"""
import io
import json
import re
from datetime import timedelta

import httpx
import pytest
from PIL import Image, ImageDraw

from ai_daily import images, render, stages
from ai_daily.config import Settings
from ai_daily.models import Event, Item
from ai_daily.store import Store
from ai_daily.triage import Layout
from conftest import NOW, png_bytes
from test_pipeline import DAY, SOURCES

CAND = re.compile(r"^\[(E\d+)\] (.+)$", re.M)
QR = "../../_brand/hollis23/wechat-qrcode_nowm_wxonly.png"
IMAGE_LINE = re.compile(r"!\[<标题>\]\((.+?)\)　第一段末尾加：（(.+)）$")


@pytest.fixture
def settings(tmp_path):
    s = Settings(vault=tmp_path / "vault", brand_python="python", data_dir=tmp_path / "data", llm_base_url="",
                 llm_api_key="", llm_model_triage="", llm_model_write="", llm_extra_body={})
    s.qr_code.parent.mkdir(parents=True)
    s.qr_code.write_bytes(png_bytes(400, 400))
    return s


def _plan(prepared, **extra) -> dict:
    rows = CAND.findall(prepared.candidates.read_text(encoding="utf-8"))
    sol = next(eid for eid, line in rows if "GPT-6.1 Sol" in line and "OpenAI 官方博客" in line)
    rest = [eid for eid, _ in rows if eid != sol]
    plan = {"headline": sol, "main": rest[:3], "briefs": rest[3:5], "backup": rest[5:7], **extra}
    prepared.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan


def _blocks(mat: str) -> dict[str, dict[str, str]]:
    out = {}
    for chunk in re.split(r"^## ", mat, flags=re.M)[1:]:
        out[chunk.split(" ", 1)[0]] = dict(re.findall(r"^- (来源行|配图行|链接)：(.+)$", chunk, re.M))
    return out


def _article(mat: str) -> str:
    """照 materials.md 写的最小正文：来源行、配图行、快讯链接、文末都照抄。"""
    b = _blocks(mat)

    def item(slot, title):
        img, credit = IMAGE_LINE.match(b[slot]["配图行"]).groups()
        return [b[slot]["来源行"], "", f"![{title}]({img})", "", f"第一段。（{credit}）", ""]

    lines = ["# AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一", "", "> **今日看点**", "> 1. 看点一", "> 2. 看点二",
             "> 3. 看点三", "", "## 头条｜GPT-6.1 Sol 发布", ""] + item("h", "GPT-6.1 Sol 发布")
    lines += ["> 原文：GPT-6.1 Sol delivers near-Astra intelligence for a fifth of the price.", "",
              "**我的看法**：值得先拿自己的任务试一下。", "", "## 要闻", ""]
    for i in (1, 2, 3):
        lines += [f"### {i}. 条目{i}", ""] + item(f"n{i}", f"条目{i}") + ["- **工程师视角**：恭喜团队，值得一试。", ""]
    lines += ["## 快讯", ""] + [f"- **快讯{i}**：一句话。（{b[f'b{i}']['链接']}）" for i in (1, 2)]
    return "\n".join(lines + ["", render.footer(QR), ""])


def test_prepare_lists_candidates_without_calling_a_model(http, settings):
    r = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    text = r.candidates.read_text(encoding="utf-8")
    assert r.n_events == len(CAND.findall(text)) > 10
    assert "## 近 3 天已经写过" in text and "GPT-6.1 Sol" in text
    state = json.loads((stages.work_dir(settings, DAY) / "state.json").read_text(encoding="utf-8"))
    assert state["counts"]["stale"] >= 1                 # 09-14 上传的模型上了热门榜：来源全早于窗口，不列
    assert "Qwen/Qwen-Image-2.1 " not in text
    store = Store(settings.db_path)
    assert store.has_last_run(DAY)                        # 下一期的窗口从这次采集算起
    store.close()
    assert not render.paths(settings.daily_dir / "2026-09", DAY)["article"].exists()


def test_prepare_moves_an_existing_draft_aside_only_with_force(http, settings):
    article = render.paths(settings.daily_dir / "2026-09", DAY)["article"]
    article.parent.mkdir(parents=True)
    article.write_text("改过的稿", encoding="utf-8")
    with pytest.raises(FileExistsError):
        stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http, force=True)
    assert not article.exists()
    assert article.with_name(article.name + ".bak").read_text(encoding="utf-8") == "改过的稿"


def test_claude_flow_end_to_end(http, settings):
    prepared = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    plan = _plan(prepared)
    m = stages.materials(settings, SOURCES, day=DAY, http=http)
    mat = m.path.read_text(encoding="utf-8")
    assert m.fetched == 6 and m.images == 4 and m.no_image == []
    head = _blocks(mat)["h"]
    assert head["来源行"].startswith("`官方` · [OpenAI 官方博客](")
    assert IMAGE_LINE.match(head["配图行"]).groups() == ("images/2026-09-30_h_nowm.png", "图源：OpenAI 官方博客")
    assert "near-Astra intelligence for a fifth of the price" in mat
    assert mat.rstrip().endswith(render.footer(QR))

    article = render.paths(settings.daily_dir / "2026-09", DAY)["article"]
    article.write_text(_article(mat), encoding="utf-8")
    plan.update(titles=["AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一", "备选标题二"],
                cover_lines=["GPT-6.1 Sol 发布", "第二件事", "第三件事"], notes=["头条：价格和上下文对过官方博客。"])
    prepared.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    f = stages.finalize(settings, SOURCES, day=DAY)
    assert f.problems == [] and f.warnings == []

    review = f.review.read_text(encoding="utf-8")
    assert "- [x] 选题、写稿、核对：Claude 已完成" in review
    assert "## 核对记录\n\n- 头条：价格和上下文对过官方博客。" in review
    assert "1. AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一" in review and "未调用（选题和写稿由 Claude 完成）" in review
    assert "头条的 AI 建议角度" not in review and " 分 · [" not in review
    assert render.GENERATED.search(review).group(1).startswith(NOW.astimezone().isoformat()[:16])
    spec = json.loads(f.cover_json.read_text(encoding="utf-8"))
    assert spec["headlines"] == ["GPT-6.1 Sol 发布", "第二件事", "第三件事"]
    assert spec["publish_titles"][0] == "AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一"
    assert f.contact is not None and f.contact.is_file()
    store = Store(settings.db_path)
    assert "2026-09-30 GPT-6.1 Sol 发布" in store.recent_titles(DAY + timedelta(days=1))   # 之后几天去重
    store.close()


def test_materials_rerun_only_fetches_swapped_items(http, settings):
    prepared = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    plan = _plan(prepared)
    first = stages.materials(settings, SOURCES, day=DAY, http=http)
    spare = plan["backup"][0]
    plan["main"][0], plan["backup"][0] = spare, plan["main"][0]
    prepared.plan.write_text(json.dumps(plan), encoding="utf-8")
    second = stages.materials(settings, SOURCES, day=DAY, http=http)
    assert first.fetched == 6 and second.fetched == 1
    state = json.loads((stages.work_dir(settings, DAY) / "state.json").read_text(encoding="utf-8"))
    assert state["slots"]["n1"]["event"] == spare and second.images == 4


def test_materials_rerun_keeps_an_image_found_by_hand(http, settings):
    prepared = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    _plan(prepared)
    stages.materials(settings, SOURCES, day=DAY, http=http)
    state_path = stages.work_dir(settings, DAY) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["slots"]["n2"].update(image_path=None, image_credit="", image_url=None)     # 当时没下到
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    manual = settings.daily_dir / "2026-09" / "images" / "2026-09-30_n2_nowm.png"
    manual.write_bytes(png_bytes(640, 360))                                             # Claude 自己找来的图
    mat = stages.materials(settings, SOURCES, day=DAY, http=http).path.read_text(encoding="utf-8")
    assert manual.read_bytes() == png_bytes(640, 360)
    assert _blocks(mat)["n2"]["配图行"] == "![<标题>](images/2026-09-30_n2_nowm.png)　第一段末尾加：（图源：自己补上）"


def test_plan_can_merge_events_and_add_links(http, settings):
    prepared = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    ids = [eid for eid, _ in CAND.findall(prepared.candidates.read_text(encoding="utf-8"))]
    prepared.plan.write_text(json.dumps({"headline": f"{ids[0]}+{ids[1]}",
                                         "main": ["url:https://example.com/news/launch"]}), encoding="utf-8")
    mat = stages.materials(settings, SOURCES, day=DAY, http=http).path.read_text(encoding="utf-8")
    assert f"## h · {ids[0]}+{ids[1]} · " in mat
    assert _blocks(mat)["n1"]["来源行"] == "`媒体` · [example.com](https://example.com/news/launch)"
    prepared.plan.write_text(json.dumps({"headline": "E9999", "main": [ids[0]]}), encoding="utf-8")
    with pytest.raises(stages.PlanError, match="E9999"):
        stages.materials(settings, SOURCES, day=DAY, http=http)


def test_finalize_lists_what_still_needs_fixing(http, settings):
    prepared = stages.prepare(settings, SOURCES, day=DAY, now=NOW, http=http)
    _plan(prepared)
    stages.materials(settings, SOURCES, day=DAY, http=http)
    article = render.paths(settings.daily_dir / "2026-09", DAY)["article"]
    article.write_text("# AI 早报 09.30｜标题\n\n## 头条｜x\n\n**我的看法**：【我的看法：待写】\n\n"
                       "- **工程师视角**：原文打不开，无法核对。\n\n见[报道](https://www.qbitai.com/2026/09/1.html)\n",
                       encoding="utf-8")
    f = stages.finalize(settings, SOURCES, day=DAY)
    problems = "\n".join(f.problems)
    for expected in ("我的看法", "今日看点 0 条", "要闻 0 条", "快讯 0 条", "文末", "国内媒体", "下好的配图没用上"):
        assert expected in problems
    assert any("来源打不开" in w for w in f.warnings)


def test_black_video_frames_are_not_used_as_images(tmp_path):
    black = Image.new("RGB", (1200, 675), (0, 0, 0))
    frame = black.copy()
    ImageDraw.Draw(frame).rectangle((300, 250, 900, 420), fill=(255, 255, 255))     # 黑底白字的视频画面
    assert images.is_blank(black) and not images.is_blank(frame)
    assert not images.is_blank(Image.new("RGB", (800, 400), (200, 90, 40)))
    buf = io.BytesIO()
    black.save(buf, "PNG")
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/png"})))
    assert not images.save_image(http, "https://pbs.twimg.com/amplify_video_thumb/1/img/x.jpg", tmp_path / "x.png")


def test_article_template_uses_the_shared_badge_and_footer():
    ev = Event(title="t", track="llm", score=1, label="官方", items=[
        Item("a", "A 博客", "official", "t", "https://a.com/x"), Item("b", "B 媒体", "media", "t", "https://b.com/y")])
    md = render.render_article(Layout(ev, [], [], []), {"titles": ["AI 早报"], "highlights": []}, qr_code="qr.png")
    assert render.badge(ev) in md.splitlines()
    assert md.rstrip().endswith(render.footer("qr.png"))
