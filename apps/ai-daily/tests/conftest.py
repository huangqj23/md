"""测试夹具：用 httpx.MockTransport 按 URL 返回 fixtures/ 下保存的真实响应（2026-09-30 抓取并裁剪）。"""
import io
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from PIL import Image

FIX = Path(__file__).parent / "fixtures"
REPO_CREATED = {"t8y2/dbx": "2021-03-01T00:00:00Z"}     # 老项目：上榜也不收
# X 嵌入组件接口返回的推文媒体（id 来自 latent_space.xml 里 AINews 的推文链接）
TWEET_MEDIA = {"2104984504133918973": {"video": {"poster": "https://pbs.twimg.com/amplify_video_thumb/1/img/dots.jpg"}},
               "2104986129686741046": {"photos": [{"url": "https://pbs.twimg.com/media/sol.jpg"}]}}
NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)

EMPTY_ATOM = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
ARTICLE_HTML = """<html><head><title>t</title><meta property="og:image" content="/img/cover.png"></head>
<body><article><h1>Introducing GPT-6.1 Sol</h1>
<p>GPT-6.1 Sol delivers near-Astra intelligence for a fifth of the price.</p>
<p>It supports a 1,050,000 token context window and costs $2 per million input tokens.</p>
<p>Developers can use it today in the API.</p></article></body></html>"""

# Techmeme 的 RSS：条目链接是当天的新闻流页面，摘要里有原文链接和原文网站首页（结构照 2026-10-01 的真实内容）
TECHMEME_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Techmeme</title>
<item><title>California Governor Gavin Newsom signs the No Robo Bosses Act</title>
<link>https://www.techmeme.com/260930/p52#a260930p52</link><pubDate>Wed, 30 Sep 2026 22:10:00 GMT</pubDate>
<description><![CDATA[<a href="https://www.techmeme.com/260930/p52#a260930p52">Techmeme</a> <a href="http://www.cnbc.com/">CNBC</a>:
<a href="https://www.cnbc.com/2026/09/30/california-gavin-newsom-ai-ban.html">California Governor Gavin Newsom signs
the No Robo Bosses Act, which prevents employers from relying solely on AI to fire workers</a>]]></description></item>
</channel></rss>"""

# 国内大模型公司的官方渠道（结构照 2026-09-30 的真实页面：GitHub API、DeepSeek 文档、Z.ai 发布页）
ORG_REPOS = {"deepseek-ai": [
    {"full_name": "deepseek-ai/DeepEP-Ascend", "html_url": "https://github.com/deepseek-ai/DeepEP-Ascend",
     "description": "DeepEP for Ascend NPUs", "created_at": "2026-09-30T02:00:00Z", "fork": False,
     "stargazers_count": 812, "language": "C++"},
    {"full_name": "deepseek-ai/dsh-libreoffice-kit", "html_url": "https://github.com/deepseek-ai/dsh-libreoffice-kit",
     "description": "An internal component used by DeepSeek Harness", "created_at": "2026-09-30T03:00:00Z",
     "fork": False, "stargazers_count": 2},
    {"full_name": "deepseek-ai/vllm", "html_url": "https://github.com/deepseek-ai/vllm", "description": "fork",
     "created_at": "2026-09-29T12:00:00Z", "fork": True, "stargazers_count": 3},
    {"full_name": "deepseek-ai/DeepGEMM", "html_url": "https://github.com/deepseek-ai/DeepGEMM", "description": "old",
     "created_at": "2025-02-26T00:00:00Z", "fork": False, "stargazers_count": 6000}]}
DEEPSEEK_HOME = ('<html><body><nav><a href="/">DeepSeek API Docs</a><a href="/news/news260929">News</a></nav>'
                 '<main><h1>Your First API Call</h1></main></body></html>')
DEEPSEEK_NEWS = {
    "/news/news260929": ('<html><body><article><h1>DeepSeek-V4.2: Faster and Cheaper</h1><p>DeepSeek-V4.2 Release '
                         '2026/09/29. Output speed doubles; prices drop by 50%.</p></article><nav>'
                         '<a class="pagination-nav__link pagination-nav__link--next" href="/news/news260910">'
                         'Next DeepSeek-V4.1-Flash Release</a></nav></body></html>'),
    "/news/news260910": '<html><body><article><h1>DeepSeek-V4.1-Flash</h1></article></body></html>'}
ZAI_PAGE = ('<html><body><h1 id="page-title">New Released</h1>'
            '<div class="update" id="2026-09-30"><a href="#2026-09-30">​</a><div>2026-09-30</div><div>GLM-5.4</div>'
            '<ul><li>Hybrid attention with 320B total parameters and 20B activated.</li></ul>'
            '<p>Learn more in our <a href="/guides/llm/glm-5.4">documentation</a>.</p></div>'
            '<div class="update" id="2026-08-26"><div>2026-08-26</div><div>GLM-5.3-Flash</div></div></body></html>')


def png_bytes(w=800, h=400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 90, 40)).save(buf, "PNG")
    return buf.getvalue()


def _file(name, ctype):
    return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"content-type": ctype})


def route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host, path = request.url.host, request.url.path
    if host == "openai.com" and path == "/news/rss.xml":
        return _file("openai_news.xml", "application/rss+xml")
    if host == "www.latent.space":
        return _file("latent_space.xml", "application/rss+xml")
    if host == "www.qbitai.com":
        return _file("qbitai.xml", "application/rss+xml")
    if host == "www.reddit.com":
        return _file("reddit_localllama.xml", "application/atom+xml")
    if path == "/vllm-project/vllm/releases.atom":
        return _file("gh_vllm_releases.atom", "application/atom+xml")
    if path.endswith("/releases.atom"):
        return httpx.Response(200, content=EMPTY_ATOM, headers={"content-type": "application/atom+xml"})
    if host == "github.com" and path.startswith("/trending"):
        return _file("github_trending.html", "text/html")
    if host == "api.github.com" and path.startswith("/users/") and path.endswith("/repos"):
        return httpx.Response(200, json=ORG_REPOS.get(path.split("/")[2], []))
    if host == "api-docs.deepseek.com":
        page = DEEPSEEK_HOME if path == "/" else DEEPSEEK_NEWS.get(path)
        return httpx.Response(200, text=page, headers={"content-type": "text/html"}) if page else httpx.Response(404)
    if host == "www.techmeme.com" and path == "/feed.xml":
        return httpx.Response(200, text=TECHMEME_RSS, headers={"content-type": "application/rss+xml"})
    if host == "docs.z.ai":
        return httpx.Response(200, text=ZAI_PAGE, headers={"content-type": "text/html; charset=utf-8"})
    if host == "api.github.com" and path.startswith("/repos/"):
        repo = path.removeprefix("/repos/")
        return httpx.Response(200, json={"full_name": repo, "created_at": REPO_CREATED.get(repo, "2026-09-20T00:00:00Z")})
    if host == "tldr.tech" and path == "/api/rss/ai":
        return _file("tldr_rss.xml", "application/rss+xml")
    if host == "tldr.tech" and path.startswith("/ai/"):
        return _file("tldr_issue.html", "text/html")
    if host == "hn.algolia.com":
        return _file("hn_front.json", "application/json")
    if host == "openrouter.ai" and path == "/api/v1/models":
        return _file("openrouter_models.json", "application/json")
    if host == "huggingface.co":
        if path == "/api/daily_papers":
            return _file("hf_daily_papers.json", "application/json")
        if path == "/api/models" and request.url.params.get("sort") == "trendingScore":
            return _file("hf_trending.json", "application/json")
        if path == "/api/models" and request.url.params.get("author") == "Qwen":
            return _file("hf_org_qwen.json", "application/json")
        if path == "/api/models":
            return httpx.Response(200, json=[])
        if path.startswith("/api/models/"):
            return _file("hf_model_qwen25_7b.json", "application/json")
        if path.endswith("/raw/main/config.json"):
            return httpx.Response(200, json={"max_position_embeddings": 32768})
        if path.endswith("/raw/main/README.md"):
            return httpx.Response(200, text="---\nlicense: apache-2.0\n---\n# Model\nA 7B instruct model.")
    if host == "cdn.syndication.twimg.com":
        return httpx.Response(200, json=TWEET_MEDIA.get(request.url.params.get("id"), {}))
    if host == "publish.twitter.com":
        return httpx.Response(200, json={"author_name": "OpenAI", "author_url": "https://x.com/OpenAI",
                                         "html": '<blockquote class="twitter-tweet"><p lang="en">Meet dots, always-on agents.</p></blockquote>'})
    if (path.endswith((".png", ".jpg", ".jpeg", ".webp")) or "thumbnail" in url or "img" in path
            or host == "opengraph.githubassets.com"):
        return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
    # 每个网页的 og:image 不同（真实网站也是），否则配图去重会把它们当成同一张
    page = ARTICLE_HTML.replace("/img/cover.png", f"/img/{zlib.crc32(url.encode())}.png")
    return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})


@pytest.fixture
def http():
    with httpx.Client(transport=httpx.MockTransport(route), follow_redirects=True) as client:
        yield client


@pytest.fixture
def openrouter_data():
    return json.loads((FIX / "openrouter_models.json").read_text(encoding="utf-8"))["data"]


class ScriptedLLM:
    """按 system prompt 里的关键词分派固定回复，记录每次调用。接口同 llm.OpenAICompatLLM。"""

    def __init__(self, handlers, label="scripted"):
        self.handlers, self.calls, self.label = handlers, [], label
        self.usage = {"calls": 0, "input": 0, "output": 0}

    def json(self, system, user, **kw):
        self.calls.append((system, user))
        self.usage["calls"] += 1
        for key, fn in self.handlers.items():
            if key in system:
                return fn(user)
        raise AssertionError(f"没有匹配的回复：{system[:40]}")
