"""国内大模型公司自己的发布渠道。媒体只收国外的，公司自己的发布照收：

- GitHub 组织新建的仓库：开源发布多半从一个新仓库开始（GitHub API，每个组织一次请求）；
- DeepSeek API 文档的新闻页：模型发布、API 和价格变动，网址里带日期（/news/news260910）；
- Z.ai（智谱）的新模型发布页：每次发布一块，块的 id 是日期。

HF 上新传的模型由 hf.org_models 收，阿里巴巴的官方新闻（Alizila）走 sources.yaml 里的 RSS。
Qwen 官网（qwen.ai）、StepFun、MiniMax 的新闻页是前端渲染的，抓不到列表，靠 GitHub / HF / X 线索覆盖。
只有日期的页面，按那天北京时间 23:59 算（当天随时可能发），但不晚于现在。
"""
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import Item
from ..net import get
from ..textutil import clip
from . import github

log = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
DEEPSEEK = "https://api-docs.deepseek.com"
DEEPSEEK_NEWS = re.compile(r"/news/news(\d{2})(\d{2})(\d{2})$")
ZAI_RELEASES = "https://docs.z.ai/release-notes/new-released"
DAY_ID = re.compile(r"^20\d\d-\d\d-\d\d$")
INTERNAL = re.compile(r"\binternal\b", re.I)     # “An internal component used by …”这类配套小仓库不是新闻


def _day_end(day: date, now: datetime) -> datetime:
    return min(datetime.combine(day, time(23, 59), CST), now)


def org_repos(http, orgs, since: datetime) -> list[Item]:
    """各组织在窗口内新建的公开仓库（fork、自称内部组件的不算）。被限流就停，剩下的组织本次不查。"""
    items = []
    for org in orgs:
        try:
            repos = github.api_get(http, f"users/{org}/repos", sort="created", direction="desc", per_page=5)
        except github.RateLimited:
            log.warning("GitHub API 限流，剩下的组织本次不查新仓库（设 GITHUB_TOKEN 可提高到每小时 5000 次）")
            break
        except httpx.HTTPError as e:
            log.info("查不到 %s 的仓库：%s", org, type(e).__name__)
            continue
        for r in repos:
            created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            desc = (r.get("description") or "").strip()
            if r.get("fork") or created < since or INTERNAL.search(desc):
                continue
            lang = f"，{r['language']}" if r.get("language") else ""
            items.append(Item(source="gh-orgs", source_name=f"GitHub · {org}", kind="repo", label="官方",
                              title=f"{r['full_name']}：{desc}" if desc else r["full_name"], url=r["html_url"],
                              published=created, summary=f"{org} 新建的仓库，{r.get('stargazers_count', 0)} star{lang}",
                              image=f"https://opengraph.githubassets.com/1/{r['full_name']}",
                              meta={"text": desc, "stars": r.get("stargazers_count", 0)}))
    return items


def deepseek_news(http, since: datetime, now: datetime, max_pages: int = 4) -> list[Item]:
    """从文档首页找到最新一篇新闻，顺着“下一篇”（更早的）往回翻，翻到窗口外为止。"""
    home = BeautifulSoup(get(http, DEEPSEEK + "/").text, "lxml")
    link = next((a["href"] for a in home.select("a[href]") if DEEPSEEK_NEWS.search(a["href"])), None)
    items = []
    while link and len(items) < max_pages:
        yy, mm, dd = DEEPSEEK_NEWS.search(link).groups()
        when = _day_end(date(2000 + int(yy), int(mm), int(dd)), now)
        if when < since:
            break
        url = urljoin(DEEPSEEK, link)
        page = BeautifulSoup(get(http, url).text, "lxml")
        h1 = page.find("h1")
        text = (page.select_one("article") or page).get_text(" ", strip=True)
        items.append(Item(source="deepseek-news", source_name="DeepSeek API 文档", kind="official", label="官方",
                          title=h1.get_text(" ", strip=True) if h1 else url, url=url, published=when,
                          summary=clip(text, 500), meta={"text": clip(text, 8000)}))
        nxt = next((a for a in page.select("a[href]") if "pagination-nav__link--next" in " ".join(a.get("class") or [])
                    or a.get_text(" ", strip=True).startswith("Next")), None)
        link = nxt["href"] if nxt is not None and DEEPSEEK_NEWS.search(nxt["href"]) else None
    return items


def zai_releases(http, since: datetime, now: datetime) -> list[Item]:
    """Z.ai 新模型发布页：每块第一行是模型名，后面是说明；链接用块里的文档地址（同一页的锚点去重时会被当成一条）。"""
    soup = BeautifulSoup(get(http, ZAI_RELEASES).text, "lxml")
    items = []
    for block in soup.find_all(attrs={"id": DAY_ID}):
        when = _day_end(date.fromisoformat(block["id"]), now)
        if when < since:
            continue
        lines = [x for x in (s.strip("​ ").strip() for s in block.get_text("\n").split("\n"))
                 if x and x != block["id"]]
        if not lines:
            continue
        name, text = lines[0], " ".join(lines[1:])
        doc = next((urljoin(ZAI_RELEASES, a["href"]) for a in block.find_all("a", href=True)
                    if not a["href"].startswith("#")), None)
        items.append(Item(source="zai-releases", source_name="Z.ai（智谱）", kind="official", label="官方",
                          title=f"Z.ai：{name}", url=doc or f"{ZAI_RELEASES}?release={block['id']}", published=when,
                          summary=clip(text, 500), meta={"text": clip(text, 8000)}))
    return items
