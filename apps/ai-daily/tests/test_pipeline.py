import json
import re
from datetime import date, timedelta

import pytest

from ai_daily import publish, render
from ai_daily.config import Settings
from ai_daily.llm import LLMPair
from ai_daily.models import Event, Item
from ai_daily.pipeline import dedupe, run, swap_thin_events
from ai_daily.store import Store
from ai_daily.triage import Layout, select
from conftest import NOW, ScriptedLLM

DAY = date(2026, 9, 30)
SOURCES = {
    "window_hours": 36,
    "feeds": [
        {"key": "openai-news", "name": "OpenAI 官方博客", "url": "https://openai.com/news/rss.xml", "kind": "official"},
        {"key": "ainews", "name": "AINews（Latent Space）", "url": "https://www.latent.space/feed",
         "kind": "newsletter", "parser": "ainews", "max_items": 10},
        {"key": "qbitai", "name": "量子位", "url": "https://www.qbitai.com/feed", "kind": "media"},
    ],
    "github_releases": {"llm": ["vllm-project/vllm"], "agent": ["openai/codex"]},
    # 热门榜放宽到 30 天：让旧模型进入候选，验证选题后的旧闻过滤会把它们去掉
    "hf": {"daily_papers_limit": 5, "orgs": ["Qwen"], "trending_max_age_hours": 30 * 24},
    # 脚本化选题只给 ~16 个事件，而且每条候选单独成事件、集中在少数几个信源：同源上限放宽，论文上限照常
    # （上限本身见 test_select_caps_papers_and_same_source）
    "layout": {"main": 10, "briefs": 5, "backup": 20, "max_images": 11, "max_per_source": 20},
    "hn": {}, "github_trending": {}, "openrouter": {"enabled": True},
    "official_x_handles": ["OpenAI"],
    "ai_keywords": ["ai", "agent", "model", "gpt", "llm", "voice"],
}


@pytest.fixture
def settings(tmp_path):
    return Settings(vault=tmp_path / "vault", brand_python="python", data_dir=tmp_path / "data",
                    llm_base_url="", llm_api_key="", llm_model_triage="triage-m",
                    llm_model_write="write-m", llm_extra_body={})


# 编号 | 类型 | 来源 | 可信度 | 发布时间 | 标题 ｜ …
LINE = re.compile(r"^\[(\d+)\] (\w+) \| ([^|]+) \| [^|]+ \| [^|]+ \| (.+?) ｜", re.M)
TRACKS = ["llm", "agent", "vision"]


def triage_reply(user):
    lines = [(int(m.group(1)), m.group(3), m.group(4)) for m in LINE.finditer(user)]
    sol = [i for i, src, title in lines if "GPT-6.1 Sol" in title and src in ("OpenAI 官方博客", "OpenRouter")]
    events = [{"title": "OpenAI 发布 GPT-6.1 Sol", "items": sol + [999], "track": "llm", "score": 95,
               "label": "官方", "why": "新模型，价格低", "follow_up": ""}]
    for n, i in enumerate([i for i, _, _ in lines if i not in sol][:18]):
        events.append({"title": f"事件 {i}", "items": [i], "track": TRACKS[n % 3], "score": 80 - n,
                       "label": "社区", "why": f"理由 {i}"})
    return {"events": events}


def write_reply(user):
    src = user.split("---- 原文开始 ----")[1].split("---- 原文结束 ----")[0].strip()
    quotes = [{"claim": "开头", "source_quote": src[:60]}]
    if "头条：" in user:
        quotes.append({"claim": "编的", "source_quote": "This sentence does not exist anywhere in the source text."})
    return {"title": "中文标题", "paragraphs": ["第一段正文。"], "quotes": quotes,
            "engineer_note": "可以直接在 API 里试。", "insufficient": False}


def brief_reply(user):
    return {"briefs": [{"i": int(i), "title": f"快讯{i}", "text": "一句话。"} for i in re.findall(r"^\[(\d+)\]", user, re.M)]}


def editor_reply(user):
    return {"highlights": ["看点一", "看点二", "看点三"],
            "titles": ["AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一", "备选标题二", "备选标题三"],
            "cover_lines": ["GPT-6.1 Sol 发布", "Dots 常驻智能体", "vLLM 新版本"]}


def scripted():
    llm = ScriptedLLM({"选题编辑": triage_reply, "中文早报的作者": write_reply, "快讯": brief_reply, "主编": editor_reply})
    return LLMPair(llm, llm)


def test_full_run_with_llm(http, settings):
    llm = scripted()
    result = run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=llm)
    md = result.article.read_text(encoding="utf-8")

    assert result.article == settings.daily_dir / "2026-09" / "2026-09-30_AI日报.md"
    assert md.startswith("# AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一\n")
    assert "> 1. 看点一" in md and "## 头条｜中文标题" in md
    assert "【我的看法：待写】" in md and "图片版权归原作者，出处见图注。" in md
    assert "由 AI 辅助" not in md                                # 文末不写 AI 辅助声明（用户 2026-10-01 定的）
    assert "## 要闻" in md and "## 快讯" in md and "## LLM" not in md          # 不分栏，平铺
    numbers = [int(n) for n in re.findall(r"^### (\d+)\. ", md, re.M)]
    assert numbers == list(range(1, 11))                        # 要闻 10 条，连续编号
    assert "- **工程师视角**：可以直接在 API 里试。" in md
    assert "> 原文：" in md
    assert "Qwen/Qwen-Image-2.1" not in md                      # 09-14 上传的模型上热门榜：旧闻，不收
    # 图源接在图片下面第一段末尾的括号里，不单独一行
    credits = re.findall(r"\]\(images/2026-09-30_\w+_nowm\.png\)\n\n[^\n]+（图源：([^\n（）]+)）\n", md)
    assert len(credits) >= 9                                     # 头条 + 10 条要闻，大部分有配图
    assert "X @OpenAI" in credits and "GitHub Trending" in credits   # 推文自带的图、仓库卡片都用上了
    # 头条里编造的那句原文被核对出来，其余条目的引用都能在原文里找到
    assert result.n_flags == 1 and "1 条原文句在来源里没找到" in md
    # 头条配图：官方博客的 og:image，存成 _nowm，不加水印
    assert re.search(r"!\[中文标题\]\(images/2026-09-30_h_nowm\.png\)\n\n[^\n]+（图源：OpenAI 官方博客）\n", md)
    assert "<p style=" not in md
    assert "关注**Hollis的视觉大模型实战**：" in md                     # 文末公众号名加粗（二维码名片上的写法）
    # 文末二维码引用 vault 的品牌目录，相对正文写；文件名带 _wxonly，多平台同步时只发公众号
    assert md.rstrip().endswith("![微信扫码关注 Hollis的视觉大模型实战](../../_brand/hollis23/wechat-qrcode_nowm_wxonly.png)")
    assert (result.article.parent / "images" / "2026-09-30_h_nowm.png").is_file()

    review = result.review.read_text(encoding="utf-8")
    assert "scripted 调用" in review
    assert "1. AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一" in review and "共 1 处" in review
    spec = json.loads(result.cover_json.read_text(encoding="utf-8"))
    assert spec["layout"] == "daily" and spec["tag"] == "AI 早报 · 2026-09-30 · 第 1 期"
    assert spec["headlines"] == editor_reply("")["cover_lines"]
    assert spec["visual"]["highlight"] == 0 and sum(spec["visual"]["counts"]) >= 4
    assert not result.cover_ok                                 # 测试 vault 里没有 cover.py

    # 选题 prompt 里的编号要对得上；越界编号被忽略
    triage_user = llm.triage.calls[0][1]
    assert "[0] " in triage_user and "今天是 2026-09-30" in triage_user
    assert "窗口开始前已经出现过的动态" in triage_user and "（无记录）" in triage_user     # 第一次运行，库里没有更早的

    # 没走 publish 也记下草稿写过的链接：第二天不再选
    store = Store(settings.db_path)
    assert "https://openai.com/index/introducing-gpt-6-1-sol" in store.published_urls(DAY + timedelta(days=1))
    assert not store.has_published(DAY)
    store.close()


def test_rerun_refuses_to_overwrite_edits(http, settings):
    run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), cover=False)
    with pytest.raises(FileExistsError):
        run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), cover=False)
    result = run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), cover=False, force=True)
    assert result.article.with_name(result.article.name + ".bak").is_file()


def test_no_llm_mode_marks_everything_for_review(http, settings, tmp_path):
    result = run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=None, out_dir=tmp_path / "trial", cover=False)
    md = result.article.read_text(encoding="utf-8")
    assert result.article.parent == tmp_path / "trial"
    assert md.startswith("# AI 早报 09.30｜")
    assert "无 LLM 模式" in md and result.n_flags >= 3


def test_publish_check_then_mark_published(http, settings):
    result = run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), cover=False)
    problems = publish.check(result.article)
    assert any("我的看法" in p for p in problems) and any("待核对" in p for p in problems)

    md = result.article.read_text(encoding="utf-8").replace("【我的看法：待写】", "我认为值得一试。")
    md = re.sub(r"【待核对：[^】]*】\n?", "", md)
    result.article.write_text(md, encoding="utf-8")
    # 文末二维码放在 vault 的品牌目录里；还没放进去时，发布检查会拦下来
    assert publish.check(result.article) == ["图片不存在：../../_brand/hollis23/wechat-qrcode_nowm_wxonly.png"]
    settings.qr_code.parent.mkdir(parents=True, exist_ok=True)
    settings.qr_code.write_bytes(b"png")
    assert publish.check(result.article) == []

    store = Store(settings.db_path)
    n = publish.mark_published(store, DAY, result.article)
    assert n > 5 and store.has_published(DAY)
    assert "https://openai.com/index/introducing-gpt-6-1-sol" in store.published_urls(date(2026, 10, 1))
    recent = store.recent_titles(date(2026, 10, 1))
    assert any("中文标题" in t for t in recent)
    assert not any(t.endswith(("工程师视角", "显存估算")) for t in recent)     # 条目里的固定小标题不算标题
    store.close()


def test_publish_check_catches_missing_image(tmp_path):
    art = tmp_path / "a.md"
    art.write_text("# 标题\n\n![图](images/none.png)\n\n本文由 AI 辅助收集信息。\n", encoding="utf-8")
    assert publish.check(art) == ["图片不存在：images/none.png"]


def _ev(title, track, score, source=None, kind="official", label="官方"):
    src = source or f"s-{title}"
    return Event(title=title, items=[Item(src, src.upper(), kind, title, f"https://e.com/{title}")],
                 track=track, score=score, label=label)


def _paper(title, score):
    return _ev(title, "llm", score, source="hf-papers", kind="paper", label="论文")


def test_select_caps_papers_and_same_source():
    events = ([_paper("p0", 99), _ev("n1", "llm", 98)] + [_paper(f"p{i}", 97 - i) for i in range(1, 5)]
              + [_ev(f"o{i}", "llm", 90 - i, source="openai") for i in range(5)]
              + [_ev(f"x{i}", "other", 80 - i) for i in range(20)])
    layout = select(events, main=10, briefs=5, backup=10, max_papers=1, max_per_source=3)
    assert layout.headline.title == "n1"                                   # 头条不选论文，哪怕论文分数更高
    assert [e.title for e in layout.main] == ["p0", "o0", "o1", "o2"] + [f"x{i}" for i in range(6)]
    assert [e.title for e in layout.briefs] == ["p1", "o3", "o4", "x6", "x7"]   # 超上限的顺延到快讯
    assert [e.title for e in layout.backup][:4] == ["p2", "p3", "p4", "x8"] and len(layout.backup) == 10


def test_select_headline_falls_back_to_paper_when_all_are_papers():
    layout = select([_paper("a", 9), _paper("b", 8)], main=5, briefs=5, backup=5)
    assert layout.headline.title == "a" and [e.title for e in layout.briefs] == ["b"]


def test_swap_thin_events_respects_paper_cap():
    head, paper_main, thin = _ev("h", "llm", 99), _paper("pm", 90), _ev("thin", "llm", 80)
    thin.flags = ["主来源网页打不开（HTTP 403），只能依据信源摘要"]
    paper_brief, normal = _paper("pb", 70), _ev("n", "llm", 60)
    layout = Layout(head, [paper_main, thin], [paper_brief, normal], [])
    assert swap_thin_events(layout, lambda ev: None) == ["thin"]
    assert [e.title for e in layout.main] == ["pm", "n"]                   # 论文已经满了，换进来的是新闻
    assert [e.title for e in layout.briefs] == ["thin", "pb"]


def test_badge_lists_other_sources_after_one_see_also():
    ev = Event(title="t", track="llm", score=90, label="官方", items=[
        Item("claude", "Claude 博客", "official", "t", "https://anthropic.com/a"),
        Item("openrouter", "OpenRouter", "price", "t", "https://openrouter.ai/a"),
        Item("tns", "The New Stack", "media", "t", "https://thenewstack.io/a")])
    md = render.render_article(Layout(ev, [], [], []), {"titles": ["AI 早报"], "highlights": []})
    line = next(ln for ln in md.splitlines() if ln.startswith("`官方`"))
    assert line.count("另见") == 1
    assert line.endswith("另见 [OpenRouter](https://openrouter.ai/a)、[The New Stack](https://thenewstack.io/a)")


def test_select_is_a_flat_ranking():
    events = [_ev(f"e{i}", ["llm", "agent", "vision", "other"][i % 4], 100 - i) for i in range(30)]
    layout = select(events, main=15, briefs=10, backup=3)
    assert layout.headline.title == "e0"
    assert [e.title for e in layout.main] == [f"e{i}" for i in range(1, 16)]     # 不按主线分组，只按分数
    assert [e.title for e in layout.briefs] == [f"e{i}" for i in range(16, 26)]
    assert [e.title for e in layout.backup] == ["e26", "e27", "e28"]
    assert layout.written[0] is layout.headline and len(layout.written) == 16


def test_dedupe_keeps_most_credible_source():
    a = Item("hn", "Hacker News", "community", "t", "https://openai.com/x?utm_source=hn")
    b = Item("openai", "OpenAI 官方博客", "official", "t", "https://openai.com/x")
    [kept] = dedupe([a, b])
    assert kept is b and kept.meta["also"] == ["Hacker News"]


def test_issue_number_counts_earlier_days(tmp_path):
    (tmp_path / "2026-09").mkdir()
    for d in ("2026-09-28", "2026-09-29", "2026-09-30"):
        (tmp_path / "2026-09" / f"{d}_AI日报.md").write_text("x", encoding="utf-8")
    (tmp_path / "2026-09" / "2026-09-29_AI日报_内嵌图片版.md").write_text("x", encoding="utf-8")
    assert render.issue_number(tmp_path, date(2026, 9, 30)) == 3


def test_preview_page_embeds_images_and_shows_draft_status(http, settings, tmp_path):
    from html.parser import HTMLParser

    from ai_daily import preview
    result = run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), cover=False)
    out = tmp_path / "preview.html"
    info = preview.build(result.article, out)
    page = out.read_text(encoding="utf-8")
    briefs = result.article.read_text(encoding="utf-8").split("## 快讯", 1)[1].split("---", 1)[0]
    # 脚本化选题的事件不多，其中 5 篇论文受上限只留 2 篇，快讯剩两三条
    assert info["items"] == 10 and info["briefs"] == len(re.findall(r"^- ", briefs, re.M)) >= 2
    assert info["flags"] == 1 and info["opinion_todo"]
    assert page.startswith("<title>AI 早报 09.30</title>")
    assert '<span class="who">Hollis的视觉大模型实战</span>' in page          # 署名用公众号名，不用 hollis23
    assert page.count("data:image/webp;base64,") == info["images"] >= 9      # 图片全部内嵌，不引用本地文件
    assert 'src="images/' not in page
    assert page.count("（图源：") == info["images"] and "<figcaption>" not in page   # 图源在正文段落里
    assert "&lt;p style=" not in page
    assert '<li class="used">AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一</li>' in page   # 候选标题里标出正文在用的
    assert '<span class="cred official">官方</span>' in page and 'class="flag"' in page

    class Balance(HTMLParser):
        VOID = {"img", "meta", "link", "br", "hr", "rect", "path"}

        def __init__(self):
            super().__init__()
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            else:
                self.bad.append(tag)

    checker = Balance()
    checker.feed(page)
    assert checker.stack == [] and checker.bad == []                        # 标签成对，结构没乱


def test_new_machine_recovers_previous_issues_from_the_vault(settings, tmp_path):
    """换电脑后库是空的：前几期写过的链接、标题、发布状态和生成时间从 vault 补回来，第二天不会重复选。"""
    from datetime import datetime, timezone
    from ai_daily import history
    from ai_daily.pipeline import window_start
    prev = DAY - timedelta(days=1)
    month = settings.daily_dir / f"{prev:%Y-%m}"
    month.mkdir(parents=True)
    p = render.paths(month, prev)
    p["article"].write_text("# AI 早报\n\n## 头条｜OpenAI 发布 GPT-6.1 Sol\n\n正文（[来源](https://openai.com/index/sol/)）\n",
                            encoding="utf-8")
    p["review"].write_text("## 本次运行\n\n- 生成时间：2026-09-29T07:05:00+08:00\n- 采集：1 条\n", encoding="utf-8")
    store = Store(tmp_path / "fresh.db")
    assert history.sync_from_vault(store, settings.daily_dir, DAY) == [prev]
    assert "https://openai.com/index/sol" in {u.rstrip("/") for u in store.published_urls(DAY)}
    assert any("GPT-6.1 Sol" in t for t in store.recent_titles(DAY))
    assert not store.has_published(prev)
    assert store.previous_run(DAY) == datetime(2026, 9, 28, 23, 5, tzinfo=timezone.utc)
    since = window_start(store, {"window": {"min_hours": 12, "max_hours": 72}}, NOW, DAY)
    assert since == datetime(2026, 9, 28, 21, 5, tzinfo=timezone.utc)      # 上一期生成时间再早 2 小时
    p["article"].with_name(f"{p['stem']}{render.EMBED_SUFFIX}.md").write_text("x", encoding="utf-8")
    history.sync_from_vault(store, settings.daily_dir, DAY)
    assert store.has_published(prev)
    store.close()


def test_review_records_when_the_issue_was_generated(http, settings):
    run(settings, SOURCES, day=DAY, now=NOW, http=http, llm=scripted(), images=False, cover=False)
    review = render.paths(settings.daily_dir / "2026-09", DAY)["review"].read_text(encoding="utf-8")
    from datetime import datetime
    assert datetime.fromisoformat(render.GENERATED.search(review).group(1)) == NOW
