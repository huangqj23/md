from datetime import datetime, timedelta, timezone

import httpx

from ai_daily.collect import feeds, github, hf, hn, inbox, labs, openrouter
from ai_daily.textutil import compile_keywords
from conftest import NOW, route

SINCE = NOW - timedelta(hours=36)
KW = compile_keywords(["ai", "agent", "agents", "model", "gpt", "transformer", "llm", "voice", "rag"])


def test_rss_feed_window_and_fields(http):
    spec = {"key": "openai-news", "name": "OpenAI 官方博客", "url": "https://openai.com/news/rss.xml", "kind": "official"}
    items = feeds.collect_feed(http, spec, SINCE)
    assert len(items) == 4
    assert all(it.label == "官方" and it.published >= SINCE for it in items)
    assert items[0].published >= items[-1].published          # 新的在前
    assert feeds.collect_feed(http, spec, NOW) == []           # 窗口外的不收


def test_release_feed_latest_only(http):
    spec = {"key": "gh:vllm", "name": "vllm-project/vllm", "kind": "release", "latest_only": True, "track": "llm",
            "url": "https://github.com/vllm-project/vllm/releases.atom"}
    items = feeds.collect_feed(http, spec, SINCE)
    assert len(items) == 1
    assert items[0].title.startswith("vllm-project/vllm ") and items[0].label == "代码" and items[0].track == "llm"
    assert items[0].meta["text"]                               # release notes 全文留给写稿用


def test_ainews_splits_twitter_recap_into_x_leads(http):
    spec = {"key": "ainews", "name": "AINews（Latent Space）", "url": "https://www.latent.space/feed",
            "kind": "newsletter", "parser": "ainews", "max_items": 40}
    items = feeds.collect_feed(http, spec, SINCE, official_handles=["OpenAI"])
    issue = [it for it in items if it.kind == "newsletter"]
    social = [it for it in items if it.kind == "social"]
    assert len(issue) == 1 and issue[0].title.startswith("OpenAI DevDay 2026")
    assert len(social) >= 5
    assert all(it.url.startswith("https://") and "latent.space" not in it.url for it in social)   # 没链接的子项不单列
    dots = next(it for it in social if it.title.startswith("Dots"))
    assert dots.url == "https://x.com/OpenAI/status/2104984504133918973" and dots.label == "官方"
    assert dots.source_name == "X @OpenAI" and dots.meta["via"] == "AINews（Latent Space）"   # 信源名写发推的账号
    assert all("x.com" not in link for it in social for link in it.meta["links"])   # 非推文链接留着找配图
    assert dots.meta["topic"].startswith("OpenAI DevDay 2026")


def test_hf_daily_papers_sorted_by_upvotes(http):
    items = hf.daily_papers(http, {"daily_papers_limit": 3})
    assert [it.meta["upvotes"] for it in items] == [8, 5, 1]
    assert items[0].url == "https://huggingface.co/papers/2609.31847" and items[0].label == "论文"
    # 周末接口返回旧榜单：上榜时间早于窗口前一天的不收
    assert hf.daily_papers(http, {}, NOW + timedelta(days=3)) == []


def test_hf_trending_drops_old_models_and_labels_official(http):
    items = hf.trending_models(http, {"trending_max_age_hours": 21 * 24}, ["Qwen"], NOW)
    ids = [it.title for it in items]
    assert "XingChen-AGI/TeleOCR" not in ids                   # 8 月的老模型
    qwen = next(it for it in items if it.title == "Qwen/Qwen-Image-2.1")
    assert qwen.label == "官方" and qwen.image.endswith("/social-thumbnails/models/Qwen/Qwen-Image-2.1.png")
    assert next(it for it in items if it.title == "convaiinnovations/laya").label == "社区"
    # 默认只收 72 小时内上传的：fixture 里最新的也是 9 天前，全部不收
    assert hf.trending_models(http, {}, ["Qwen"], NOW) == []


def test_hf_org_models_window(http):
    assert hf.org_models(http, {"orgs": ["Qwen", "deepseek-ai"]}, SINCE) == []
    got = hf.org_models(http, {"orgs": ["Qwen"]}, NOW - timedelta(days=12))
    assert [it.title for it in got] == ["Qwen/Qwen-Image-2.1-PE-I2I", "Qwen/Qwen-Image-2.1-PE-T2I"]


def test_hf_model_info(http):
    info = hf.model_info(http, "Qwen/Qwen2.5-7B-Instruct")
    assert info["safetensors"]["total"] == 7615616512
    assert info["context"] == 32768 and info["license"] == "apache-2.0"
    assert info["readme"].startswith("# Model")                # YAML 头已去掉


def test_openrouter_first_run_uses_created_time(http, openrouter_data):
    snap = openrouter.snapshot(http)
    assert all(":" not in k for k in snap)                     # :batch 等变体不收
    items = openrouter.diff(snap, None, SINCE)
    # claude-sonnet-5.5 创建于 09-28 18:04，刚好在 36 小时窗口内；更早的 jev-router 等不算新上架
    assert {it.url for it in items} == {"https://openrouter.ai/openai/gpt-6.1-sol-pro",
                                        "https://openrouter.ai/openai/gpt-6.1-sol",
                                        "https://openrouter.ai/anthropic/claude-sonnet-5.5"}


def test_openrouter_diff_new_and_price_change(http):
    snap = openrouter.snapshot(http)
    prev = {k: dict(v) for k, v in snap.items() if k != "openai/gpt-6.1-sol-pro"}
    prev["anthropic/claude-sonnet-5.5"]["prompt"] = snap["anthropic/claude-sonnet-5.5"]["prompt"] * 2
    items = openrouter.diff(snap, prev, SINCE)
    titles = [it.title for it in items]
    assert any(t.startswith("OpenRouter 上架") and "gpt-6.1-sol-pro" in it.url for t, it in zip(titles, items))
    change = next(it for it in items if "标价变化" in it.title)
    assert "claude-sonnet-5.5" in change.url and "输入" in change.summary and "→" in change.summary
    assert "不一定是厂商官方调价" in change.summary          # 别让写稿写成“X 降价”
    assert len(items) == 2


def test_github_trending_keyword_and_age_filter(http):
    items = github.trending(http, {"pages": ["", "python"], "max_items": 12, "max_repo_age_days": 30}, KW, NOW)
    names = [it.url.removeprefix("https://github.com/") for it in items]
    assert "NVIDIA/OpenShell" in names and len(names) == len(set(names))     # 两个榜单里重复的只留一条
    assert "t8y2/dbx" not in names                               # 2021 年建的老项目上榜，不算新闻
    shell = next(it for it in items if it.url.endswith("NVIDIA/OpenShell"))
    assert "创建于 10 天前" in shell.summary and shell.published is None
    assert shell.image == "https://opengraph.githubassets.com/1/NVIDIA/OpenShell"
    assert github.trending(http, {}, compile_keywords(["diffusion"]), NOW) == []


def test_github_creation_dates_are_cached_and_rate_limit_stops_lookups(http):
    cfg = {"pages": [""], "max_items": 12, "max_repo_age_days": 30}
    known = {}
    first = github.trending(http, cfg, KW, NOW, known)
    assert known["t8y2/dbx"] == "2021-03-01T00:00:00+00:00" and len(known) > 3      # 查到的记下来

    api_calls = []

    def limited(request):
        if request.url.host == "api.github.com":
            api_calls.append(request.url.path)
            return httpx.Response(403, text='{"message": "API rate limit exceeded"}',
                                  headers={"x-ratelimit-remaining": "0"})
        return route(request)

    with httpx.Client(transport=httpx.MockTransport(limited)) as client:
        again = github.trending(client, cfg, KW, NOW, dict(known))
        assert api_calls == []                                  # 全部命中缓存，不查 API
        assert [it.url for it in again] == [it.url for it in first]
        fresh = github.trending(client, cfg, KW, NOW, {})
    assert len(api_calls) == 1                                  # 第一次被限流就停，不再一个个撞
    assert "t8y2/dbx" in {it.url.removeprefix("https://github.com/") for it in fresh}   # 查不到时间的保留


def test_tldr_issue_split_into_leads(http):
    spec = {"key": "tldr-ai", "name": "TLDR AI", "url": "https://tldr.tech/api/rss/ai", "kind": "newsletter",
            "parser": "tldr", "max_items": 20}
    items = feeds.collect_feed(http, spec, SINCE)                # 09-28 那期在窗口外，只拆 09-29 那期
    assert [it.title for it in items] == ["NVIDIA Launched Open Agent Safety Platform",
                                          "Anthropic's IPO prospectus shows sweeping AI vision, surging costs",
                                          "AMD to Acquire World Labs for $8.2 Billion"]   # 广告跳过，“(4 minute read)”去掉
    nvidia = items[0]
    assert nvidia.url.startswith("https://nvidianews.nvidia.com/news/open-agent-safety-platform")
    assert nvidia.source_name == "nvidianews.nvidia.com" and nvidia.meta["via"] == "TLDR AI"
    assert "OpenShell" in nvidia.summary


def test_hn_keyword_and_window(http):
    items = hn.collect(http, {"max_items": 15}, KW, SINCE)
    titles = [it.title for it in items]
    assert titles[0].startswith("GPT 6.1 Sol") and any(t.startswith("Dots") for t in titles)
    assert not any("Delhi" in t or "Phyllotaxis" in t for t in titles)
    assert items[0].meta["points"] == 870
    # 链接帖的信源名写被链接的网站，HN 记在 via
    assert items[0].source_name == "openai.com" and items[0].meta["via"] == "Hacker News"


def test_inbox_tweet_via_oembed_and_skips_comments(http, tmp_path):
    path = tmp_path / "_inbox.md"
    path.write_text(inbox.INBOX_HEADER + "- https://x.com/OpenAI/status/999?s=20 发布会重点\n"
                    "- https://example.com/post 一篇博客\n", encoding="utf-8")
    items = inbox.collect(http, path, NOW)
    assert len(items) == 2                                   # 说明行里的示例链接不算
    tweet = items[0]
    assert tweet.title == "OpenAI：Meet dots, always-on agents." and tweet.meta["note"] == "发布会重点"
    assert items[1].title == "t" and items[1].kind == "inbox"


def test_tldr_issue_is_dated_by_its_news_day(http):
    """TLDR 的 RSS 只有日期（UTC 0 点），当期讲的是美国前一天的新闻，就按这个时间算。
    早上 7 点跑（窗口从前一天 5 点起）收得到当期；下午跑时新一期还没发，上一期是前天的新闻，不收。"""
    spec = {"key": "tldr-ai", "name": "TLDR AI", "url": "https://tldr.tech/api/rss/ai", "kind": "newsletter",
            "parser": "tldr"}
    morning_window = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    items = feeds.collect_feed(http, spec, morning_window)
    assert len(items) == 3 and items[0].published == datetime(2026, 9, 29, tzinfo=timezone.utc)
    afternoon_window = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
    assert feeds.collect_feed(http, spec, afternoon_window) == []


def test_lab_github_orgs_report_new_repos_only(http):
    items = labs.org_repos(http, ["deepseek-ai", "QwenLM"], SINCE)
    assert [it.url for it in items] == ["https://github.com/deepseek-ai/DeepEP-Ascend"]   # 老仓库、fork、内部组件不算
    it = items[0]
    assert it.label == "官方" and it.source_name == "GitHub · deepseek-ai" and it.kind == "repo"
    assert it.title == "deepseek-ai/DeepEP-Ascend：DeepEP for Ascend NPUs"
    assert it.published == datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc) and it.meta["stars"] == 812

    calls = []

    def limited(request):
        calls.append(request.url.path)
        return httpx.Response(403, text='{"message": "API rate limit exceeded"}', headers={"x-ratelimit-remaining": "0"})

    with httpx.Client(transport=httpx.MockTransport(limited)) as client:
        assert labs.org_repos(client, ["deepseek-ai", "QwenLM", "zai-org"], SINCE) == []
    assert len(calls) == 1                                        # 被限流就停


def test_deepseek_news_walks_back_until_the_window(http):
    items = labs.deepseek_news(http, SINCE, NOW)
    assert [it.title for it in items] == ["DeepSeek-V4.2: Faster and Cheaper"]      # 09-10 那篇在窗口外，不翻了
    it = items[0]
    assert it.url == "https://api-docs.deepseek.com/news/news260929" and it.label == "官方"
    assert it.published == datetime(2026, 9, 29, 23, 59, tzinfo=labs.CST)         # 只有日期：按当天北京时间 23:59
    assert "prices drop by 50%" in it.meta["text"]


def test_zai_releases_one_item_per_dated_block(http):
    items = labs.zai_releases(http, SINCE, NOW)
    assert [it.title for it in items] == ["Z.ai：GLM-5.4"]
    it = items[0]
    assert it.url == "https://docs.z.ai/guides/llm/glm-5.4" and "320B total parameters" in it.summary
    assert it.published == NOW                                    # 当天发布的不晚于现在


def test_aggregator_items_point_at_the_original_article(http):
    spec = {"key": "techmeme", "name": "Techmeme", "url": "https://www.techmeme.com/feed.xml", "kind": "media",
            "follow_original": True}
    [it] = feeds.collect_feed(http, spec, SINCE)
    assert it.url == "https://www.cnbc.com/2026/09/30/california-gavin-newsom-ai-ban.html"   # 不是当天整页新闻流
    assert it.source_name == "cnbc.com" and it.meta["via"] == "Techmeme"
    assert it.meta["via_url"] == "https://www.techmeme.com/260930/p52#a260930p52"
    [plain] = feeds.collect_feed(http, {**spec, "follow_original": False}, SINCE)
    assert plain.url.startswith("https://www.techmeme.com/") and plain.source_name == "Techmeme"
