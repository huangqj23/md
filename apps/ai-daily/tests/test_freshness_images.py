"""2026-10 调整：只收新动态（窗口、旧闻过滤）、更多配图（候选顺序、去重、Referer）、截断后提高 token 上限。"""
import json
from datetime import date, timedelta

import httpx
import httpx2
from bs4 import BeautifulSoup

from ai_daily import enrich, pipeline, triage
from ai_daily.llm import OpenAICompatLLM
from ai_daily.models import Event, Item
from ai_daily.store import Store
from ai_daily.triage import Layout, drop_stale
from conftest import NOW, png_bytes


def test_window_starts_where_the_previous_issue_left_off(tmp_path):
    cfg = {"window": {"default_hours": 26, "min_hours": 12, "max_hours": 72}}
    day, yesterday = date(2026, 9, 30), date(2026, 9, 29)
    store = Store(tmp_path / "db")
    assert pipeline.window_start(store, cfg, NOW, day) == NOW - timedelta(hours=26)       # 第一次运行
    store.mark_run(yesterday, NOW - timedelta(hours=16))
    assert pipeline.window_start(store, cfg, NOW, day) == NOW - timedelta(hours=18)       # 上一期之后 + 2 小时余量
    store.mark_run(day, NOW - timedelta(hours=1))
    assert pipeline.window_start(store, cfg, NOW, day) == NOW - timedelta(hours=18)       # 同一天重跑：起点不变
    store.mark_run(yesterday, NOW - timedelta(hours=5))
    assert pipeline.window_start(store, cfg, NOW, day) == NOW - timedelta(hours=12)       # 两期挨得近：至少 12 小时
    store.mark_run(yesterday, NOW - timedelta(days=9))
    assert pipeline.window_start(store, cfg, NOW, day) == NOW - timedelta(hours=72)       # 停了很久：最多 72 小时
    store.close()

    legacy = Store(tmp_path / "legacy")                 # 按日期记录之前的库：只有一个全局时间
    legacy.set_meta(pipeline.LAST_RUN, (NOW - timedelta(hours=20)).isoformat())
    assert pipeline.window_start(legacy, cfg, NOW, day) == NOW - timedelta(hours=22)
    legacy.set_meta(pipeline.LAST_RUN, (NOW - timedelta(hours=1)).isoformat())   # 那是今天这一期自己的
    assert pipeline.window_start(legacy, cfg, NOW, day) == NOW - timedelta(hours=26)
    legacy.close()


def _item(title, kind="official", published=None, **kw):
    return Item("s", kw.pop("source_name", "S"), kind, title, kw.pop("url", f"https://e.com/{title}"),
                published=published, **kw)


def _event(*items, title="e"):
    return Event(title=title, items=list(items), track="llm", score=50, label=items[0].label)


def test_drop_stale_keeps_fresh_and_undated_events():
    since = NOW - timedelta(hours=26)
    old, fresh = NOW - timedelta(days=5), NOW - timedelta(hours=2)
    events = [
        _event(_item("old-model", "model", old), title="旧模型上榜"),
        _event(_item("old-blog", published=old), _item("x", "social", fresh), title="旧事件有新讨论"),
        _event(_item("repo", "repo"), title="热门新仓库（不带时间）"),
        _event(_item("new", published=fresh), title="新发布"),
    ]
    assert [e.title for e in drop_stale(events, since)] == ["旧事件有新讨论", "热门新仓库（不带时间）", "新发布"]


def test_image_candidates_look_beyond_the_primary_source(monkeypatch):
    """头条的主来源是推文（没有图），同一事件里的官方博客有 og:image；模型卡、GitHub 卡排最后。"""
    pages = {"https://openai.com/post": ("博客正文", ["https://openai.com/og.png"], "OpenAI"),
             "https://blog.example.com/a": ("", ["https://blog.example.com/og.jpg"], None)}
    monkeypatch.setattr(enrich, "fetch_page", lambda http, url: pages[url])
    monkeypatch.setattr(enrich, "fetch_tweet", lambda http, url: None)
    monkeypatch.setattr(enrich, "tweet_media", lambda http, url: ["https://pbs.twimg.com/media/a.jpg"])
    tweet = _item("tweet", "social", url="https://x.com/OpenAI/status/1", source_name="X（AINews 回顾）", label="官方",
                  meta={"links": ["https://blog.example.com/a"], "text": "推文"})
    blog = _item("blog", "official", url="https://openai.com/post", source_name="OpenAI 官方博客", label="媒体")
    repo = _item("repo", "repo", url="https://github.com/o/r", image="https://opengraph.githubassets.com/1/o/r")
    ev = _event(tweet, blog, repo)
    enrich.enrich_event(None, ev)
    assert ev.primary is tweet
    assert [url for url, _, _ in ev.image_candidates] == [
        "https://pbs.twimg.com/media/a.jpg",                         # 推文自带的图最优先
        "https://openai.com/og.png", "https://blog.example.com/og.jpg", "https://opengraph.githubassets.com/1/o/r"]
    assert ev.image_candidates[0][1] == "X @OpenAI"
    assert ev.image_candidates[1][1:] == ("OpenAI 官方博客", "https://openai.com/post")
    assert ev.image_candidates[2][1] == "blog.example.com"          # 从推文附带链接找到的图，图源写网站


def test_tldr_leads_take_the_site_name_once_fetched(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_page", lambda http, url: ("正文" * 50, ["https://cnbc.com/og.jpg"], "CNBC"))
    lead = _item("ipo", "newsletter", url="https://www.cnbc.com/x", source_name="cnbc.com", meta={"via": "TLDR AI"})
    ev = _event(lead)
    enrich.enrich_event(None, ev)
    assert lead.source_name == "CNBC" and ev.image_candidates[0][1] == "CNBC"


def test_attach_images_dedupes_and_sends_referer(tmp_path):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("referer")))
        if request.url.path == "/blocked.png":
            return httpx.Response(403)
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})

    shared = ("https://img.example.com/shared.png", "A", "https://a.example.com/post")
    head = _event(_item("h"))
    head.image_candidates = [("https://img.example.com/blocked.png", "B", "https://b.example.com"), shared]
    second = _event(_item("n1"))
    second.image_candidates = [shared, ("https://img.example.com/own.png", "C", "https://c.example.com")]
    layout = Layout(head, [second], [], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        pipeline.attach_images(http, layout, tmp_path, NOW.date(), max_images=16)
    assert head.image_path == "images/2026-09-30_h_nowm.png" and head.image_credit == "图源：A"
    assert second.image_path == "images/2026-09-30_n1_nowm.png" and second.image_credit == "图源：C"   # 同一张图不重复用
    assert ("https://img.example.com/shared.png", "https://a.example.com/post") in seen                # 带 Referer 防盗链


def test_truncated_output_retries_with_a_bigger_budget():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        n = len(bodies)
        content, finish = ("", "length") if n == 1 else ('{"events": [', "length") if n == 2 else ('{"ok": true}', "stop")
        return httpx2.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    llm = OpenAICompatLLM("https://api.example.com/v1", "k", "m", max_retries=0,
                          http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    assert llm.json("json", "u", max_tokens=1500) == {"ok": True}
    assert [b["max_tokens"] for b in bodies] == [1500, 3000, 6000]       # 思考 token 占满上限：翻倍再试
    assert all(len(b["messages"]) == 2 for b in bodies)                 # 截断的半截输出不喂回去


def test_store_lists_what_appeared_just_before_the_window(tmp_path):
    """选题 prompt 里的“窗口开始前已经出现过的动态”：以前的运行见过、发布在窗口前 36 小时内的新闻类条目。"""
    store = Store(tmp_path / "db")
    since = NOW - timedelta(hours=26)
    store.mark_seen([_item("Claude Sonnet 5.5", published=since - timedelta(hours=8)),
                     _item("更早的", published=since - timedelta(hours=60)),
                     _item("窗口内", published=since + timedelta(hours=1)),
                     _item("论文", "paper", published=since - timedelta(hours=2)),
                     _item("热榜仓库", "repo")], NOW - timedelta(hours=30))
    assert store.seen_before(since) == [(since - timedelta(hours=8), "s", "Claude Sonnet 5.5")]
    store.close()


def test_previous_drafts_are_not_repeated_even_without_publish(tmp_path):
    store = Store(tmp_path / "db")
    day = NOW.date()
    store.mark_drafted(day, ["https://openai.com/x?utm_source=rss"], ["OpenAI 发布 X"])
    nxt = day + timedelta(days=1)
    assert store.published_urls(nxt) == {"https://openai.com/x"} and store.recent_titles(nxt) == [f"{day} OpenAI 发布 X"]
    assert store.published_urls(day) == set()                           # 当天重新生成不受影响
    store.mark_drafted(day, ["https://openai.com/y"], ["OpenAI 发布 Y"])   # 重新生成：以最后一版为准
    assert store.published_urls(nxt) == {"https://openai.com/y"}
    store.close()


def test_short_primary_gets_text_from_other_sources(monkeypatch):
    """主来源只有一条推文回顾时，把其他来源网页的正文也给写稿；主来源网页打不开要标出来。"""
    pages = {"https://arstechnica.com/a": ("OpenAI says GPT-6.1 Astra is too insecure to release. " * 20,
                                           ["https://arstechnica.com/og.jpg"], "Ars Technica")}
    monkeypatch.setattr(enrich, "fetch_page", lambda http, url: pages[url])
    monkeypatch.setattr(enrich, "fetch_tweet", lambda http, url: None)
    monkeypatch.setattr(enrich, "tweet_media", lambda http, url: [])
    tweet = _item("tweet", "social", url="https://x.com/OpenAI/status/1", source_name="X @OpenAI", label="官方",
                  meta={"text": "Safety claims: fewer factual errors."})
    ars = _item("ars", "media", url="https://arstechnica.com/a", source_name="arstechnica.com", label="媒体")
    ev = _event(tweet, ars)
    enrich.enrich_event(None, ev)
    assert ev.source_text.startswith("Safety claims")
    assert "（以下摘自arstechnica.com）\nOpenAI says GPT-6.1 Astra" in ev.source_text
    assert ev.flags == []

    def blocked(http, url):
        raise httpx.HTTPStatusError("403 Forbidden", request=httpx.Request("GET", url),
                                    response=httpx.Response(403))
    monkeypatch.setattr(enrich, "fetch_page", blocked)
    blog = _item("blog", url="https://openai.com/index/x", source_name="OpenAI 官方博客", label="官方",
                 summary="short")
    ev = _event(blog)
    enrich.enrich_event(None, ev)
    assert ev.flags == ["主来源网页打不开（HTTPStatusError），只能依据信源摘要"]


def test_dedupe_keeps_discovery_channel_and_heat():
    hn_item = _item("t", "community", url="https://openai.com/x?utm_source=hn", source_name="openai.com",
                    meta={"via": "Hacker News", "points": 870})
    blog = _item("t", url="https://openai.com/x", source_name="OpenAI 官方博客", label="官方")
    [kept] = pipeline.dedupe([hn_item, blog])
    assert kept is blog and kept.meta["also"] == ["Hacker News"] and kept.meta["points"] == 870


def test_triage_prompt_lists_what_came_before_the_window():
    prompts = []

    class Recorder:
        def json(self, system, user, **kw):
            prompts.append(user)
            return {"events": []}

    since = NOW - timedelta(hours=26)
    earlier = since - timedelta(hours=8)
    triage.llm_events(Recorder(), [_item("x", published=NOW)], NOW.date(), ["2026-09-29 OpenAI 发布 X"],
                      now=NOW, since=since, before=[(earlier, "simonw", "Claude Sonnet 5.5")])
    assert f"- {triage._when(earlier)} | simonw | Claude Sonnet 5.5" in prompts[0]
    assert "- 2026-09-29 OpenAI 发布 X" in prompts[0]


def test_page_images_skip_site_logos_and_use_lazy_body_images():
    """量子位的 og:image 是网站 logo；IT之家的正文图是懒加载（src 是占位图）；页头、作者头像、二维码不要。"""
    html = """<html><head><meta property="og:image" content="https://www.qbitai.com/wp-content/uploads/imgs/qbitai-logo-1.png">
    </head><body><header><img src="https://site.com/header-hero.jpg"></header>
    <div class="top_weixin"><img src="/wp-content/uploads/2019/01/qrcode_QbitAI_1.jpg"></div>
    <div class="article"><span class="author"><img class="avatar" src="https://site.com/head.jpg" width="200"></span>
    <p><img src="//img.ithome.com/images/v2/t.png" class="lazy" data-original="https://img.site.com/news/1.jpg"></p>
    <p><img src="https://img.site.com/news/tiny.png" width="16" height="16"></p>
    <div class="pgc-img"><img src="https://mp.toutiao.com/open_image/get?code=abc"></div>
    <p><img src="https://img.site.com/news/3.jpg"></p></div></body></html>"""
    got = enrich.page_images(BeautifulSoup(html, "lxml"), "https://www.qbitai.com/2026/09/1.html")
    assert got == ["https://img.site.com/news/1.jpg", "https://mp.toutiao.com/open_image/get?code=abc"]
    with_og = html.replace("imgs/qbitai-logo-1.png", "2026/09/cover.png")
    assert enrich.page_images(BeautifulSoup(with_og, "lxml"), "https://www.qbitai.com/")[0].endswith("/2026/09/cover.png")
