"""RSS / Atom 信源：官方博客、镜像、newsletter、GitHub releases、Reddit、国内媒体。"""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import feedparser
from bs4 import BeautifulSoup

from ..models import Item
from ..net import get
from ..textutil import clip, html_to_text, matches_keywords

MEDIA_HOSTS = ("redd.it",)          # Reddit 的图片、视频、预览图床，不是原文
TWEET = re.compile(r"^https?://(?:x|twitter)\.com/([^/?#]+)/status/\d+")


def _entry_time(e) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _entry_html(e) -> str:
    if e.get("content"):
        return e["content"][0].get("value", "")
    return e.get("summary", "")


def _entry_image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            if m.get("url"):
                return m["url"]
    for link in e.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href")
    return None


def collect_feed(http, spec: dict, since: datetime, keywords=(), official_handles=()) -> list[Item]:
    """spec 来自 sources.yaml 的 feeds 条目（或按 github_releases 生成的条目）。"""
    feed = feedparser.parse(get(http, spec["url"]).content)
    items = []
    for e in feed.entries:
        when = _entry_time(e)
        if when is None or when < since:
            continue
        if spec.get("parser") == "ainews":
            items.extend(parse_ainews(e, spec, when, official_handles))
            continue
        if spec.get("parser") == "tldr":
            items.extend(parse_tldr(http, e, spec, when))
            continue
        title = html_to_text(e.get("title", ""))
        if spec.get("title_strip"):
            title = re.sub(spec["title_strip"], "", title).strip() or title
        text = html_to_text(_entry_html(e))
        if spec.get("filter_keywords") and not matches_keywords(f"{title} {text[:400]}", keywords):
            continue
        if spec["kind"] == "release":
            title = f"{spec['name']} {title}"
        url, name, meta = e.get("link", ""), spec["name"], {"text": clip(text, 8000)}
        original = _original_link(_entry_html(e), url) if spec.get("follow_original") else None
        if original:          # 聚合站（Techmeme）、Reddit 链接帖只当发现渠道：链接和信源名换成原文，抓原文时再换成网站名
            url, name = original, _lead_name(original, spec["name"])
            meta.update(via=spec["name"], via_url=e.get("link", ""))
        items.append(Item(source=spec["key"], source_name=name, kind=spec["kind"], title=title,
                          url=url, published=when, summary=clip(text, 500),
                          image=_entry_image(e), track=spec.get("track"), label=spec.get("label"),
                          meta=meta))
    if spec.get("parser") not in ("ainews", "tldr"):
        items.sort(key=lambda it: it.published, reverse=True)
    if spec.get("latest_only"):
        items = items[:1]
    return items[: spec.get("max_items", 20)]


def _original_link(html: str, own: str) -> str | None:
    """聚合站条目摘要里第一条指向别的网站文章的链接（跳过聚合站自己的链接和只有域名的首页链接）。
    Reddit 链接帖的 [link] 指向原文；自发帖的 [link] 是帖子本身，图片、视频帖指向 i.redd.it / v.redd.it，都不算原文。"""
    own_host = (urlsplit(own).hostname or "").removeprefix("www.")
    for a in BeautifulSoup(html or "", "lxml").find_all("a", href=True):
        parts = urlsplit(a["href"])
        host = (parts.hostname or "").removeprefix("www.")
        if host == own_host or host.endswith("." + own_host) or host.endswith(MEDIA_HOSTS):
            continue
        if parts.scheme in ("http", "https") and parts.path.strip("/"):
            return a["href"]
    return None


def _lead_name(url: str, fallback: str) -> str:
    """newsletter 线索的信源名：推文写 X 账号，其他网页写域名（抓原文时再换成网站名）。"""
    m = TWEET.match(url)
    if m:
        return f"X @{m.group(1)}"
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return host if host and host not in ("latent.space", "news.smol.ai") else fallback


def parse_ainews(entry, spec: dict, when: datetime, official_handles=()) -> list[Item]:
    """AINews 一期：当期标题算一条 newsletter 线索；「AI Twitter Recap」里每个要点拆成一条 X 线索，
    链接取要点里的第一条推文。只当发现线索用，正文不照搬。"""
    title = html_to_text(entry.get("title", ""))
    if not title.startswith("[AINews]"):
        return []
    items = [Item(source=spec["key"], source_name=spec["name"], kind="newsletter",
                  title=title.removeprefix("[AINews]").strip(), url=entry.get("link", ""), published=when,
                  summary="AINews 当期标题（线索）")]
    soup = BeautifulSoup(_entry_html(entry), "lxml")
    start = next((h for h in soup.find_all(["h1", "h2"]) if "Twitter Recap" in h.get_text()), None)
    if start is None:
        return items
    official = {h.lower() for h in official_handles}
    topic = ""
    for el in start.find_all_next(["h1", "h2", "p", "li"]):
        if el.name in ("h1", "h2"):
            break                                   # 下一节（AI Reddit Recap）
        if el.name == "p" and el.find_parent("li") is None:
            topic = el.get_text(" ", strip=True)     # 小节主题行
            continue
        if el.name != "li":
            continue
        text = el.get_text(" ", strip=True)
        lead = el.find("strong")
        links = [a["href"] for a in el.find_all("a", href=True)]
        if not links:
            continue                                # 没有链接的是上一条的细节子项，内容已经在上一条里
        tweets = [h for h in links if TWEET.match(h)]
        url = (tweets or links)[0]
        m = TWEET.match(url)
        label = "官方" if m and m.group(1).lower() in official else "社区"
        items.append(Item(source=spec["key"], source_name=_lead_name(url, spec["name"]), kind="social",
                          title=clip(lead.get_text(" ", strip=True) if lead else text, 120), url=url,
                          published=when, summary=clip(text, 700), label=label,
                          meta={"topic": topic, "tweets": tweets[:8], "text": clip(text, 3000), "via": spec["name"],
                                "links": [h for h in links if not TWEET.match(h)][:4]}))   # 博客、仓库等：找配图用
    return items


_TLDR_SUFFIX = re.compile(r"\s*\((?:\d+\s+minute read|GitHub Repo|Website|Tool)\)\s*$", re.I)


def parse_tldr(http, entry, spec: dict, when: datetime) -> list[Item]:
    """TLDR AI 的 RSS 每期只有一条链接：打开当期网页，把每条（跳过赞助商广告）拆成线索。
    链接指向一手来源，信源名先用域名，抓原文时再换成网站名。"""
    soup = BeautifulSoup(get(http, entry.get("link", "")).text, "lxml")
    items = []
    for art in soup.select("article"):
        head, link = art.find(["h3", "h2"]), art.find("a", href=True)
        if head is None or link is None:
            continue
        title = head.get_text(" ", strip=True)
        if "(sponsor)" in title.lower():
            continue
        body = art.select_one(".newsletter-html") or art
        url = link["href"]
        items.append(Item(source=spec["key"], source_name=_lead_name(url, spec["name"]), kind="newsletter",
                          title=_TLDR_SUFFIX.sub("", title), url=url, published=when,
                          summary=clip(body.get_text(" ", strip=True), 500),
                          meta={"via": spec["name"], "text": clip(body.get_text(" ", strip=True), 3000)}))
    return items
