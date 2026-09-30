"""给选中的事件补原文、配图候选和显存估算。

原文以主来源为准；主来源只有推文、摘要这类短文本时，把为找配图打开的其他来源网页正文也附上，写稿才有料可写。

配图候选按质量排序：主来源自带的图 > 主来源网页的图或推文自带的图片 / 视频封面
> 同一事件其他来源网页的图 > 其他来源自带的图（论文缩略图、推文图片等）
> 推文线索里附带链接的网页的图 > 通用卡片（HF 模型卡、GitHub 仓库卡）。
“网页的图”是 og:image，og:image 只是网站 logo 或没有时，用正文里的图。
下载时从前往后试，第一张能用的就是这条的配图。
"""
import logging
import re
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup

from . import vram
from .collect import hf
from .collect.inbox import TWEET, fetch_tweet, tweet_media
from .models import LABELS, Event, Item, canonical_url, pick_primary
from .net import get
from .textutil import clip

log = logging.getLogger(__name__)
PAGE_KINDS = {"official", "media", "newsletter", "community", "inbox", "repo"}
MAX_SOURCE_CHARS = 12000     # 单条原文上限（给写作 prompt 用，约 3–4k token）
MAX_EXTRA_PAGES = 2          # 每个事件为找配图额外打开的网页数
SHORT_SOURCE_CHARS = 1500    # 主来源正文短于这个长度时，附上其他来源的正文
# 通用卡片：每篇都一样的站点分享图，只在别的图都没有时用
GENERIC_IMAGES = ("cdn-thumbnails.huggingface.co/social-thumbnails/models/", "opengraph.githubassets.com/",
                  "api-docs.deepseek.com/img/deepseek-social-card")
MAX_BODY_IMAGES = 2          # og:image 不能用时，从正文里取的图数
SKIP_IMAGE = re.compile(r"logo|icon|avatar|qr_?code|/themes?/|placeholder|loading|blank|spacer|sprite|banner|emoji"
                        r"|nologin", re.I)
SKIP_BLOCK = re.compile(r"header|nav|footer|sidebar|related|recommend|comment|author|share|qrcode|banner|toolbar"
                        r"|menu", re.I)


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        for attrs in ({"property": name}, {"name": name}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return tag["content"].strip()
    return None


def _declared_small(img) -> bool:
    return any((img.get(k) or "").isdigit() and 0 < int(img[k]) < 200 for k in ("width", "height"))


def page_images(soup: BeautifulSoup, base: str) -> list[str]:
    """网页里能当配图的图：og:image 在前（网站 logo 这类通用图不算），再加正文里的前两张。
    正文图优先取懒加载的真实地址（data-original / data-src），跳过页头、导航、作者栏、横幅里的图。"""
    out = []
    og = _meta(soup, "og:image", "twitter:image")
    if og and not SKIP_IMAGE.search(og):
        out.append(urljoin(base, og))
    body = []
    for img in soup.find_all("img"):
        src = next((img[k].strip() for k in ("data-original", "data-src", "data-actualsrc", "src")
                    if (img.get(k) or "").strip() and not img[k].startswith(("data:", "#"))), None)
        if not src or SKIP_IMAGE.search(f"{src} {' '.join(img.get('class') or [])}") or _declared_small(img):
            continue
        if any(p.name in ("header", "nav", "footer", "aside")
               or SKIP_BLOCK.search(f"{' '.join(p.get('class') or [])} {p.get('id') or ''}") for p in img.parents):
            continue
        url = urljoin(base, src)
        if url not in out and url not in body:
            body.append(url)
        if len(body) >= MAX_BODY_IMAGES:
            break
    return out + body


def fetch_page(http, url: str) -> tuple[str, list[str], str | None]:
    """(正文, 可用作配图的图片, 网站名)。正文用 trafilatura 抽取；不是 HTML 返回空。"""
    resp = get(http, url, retries=1)
    if "html" not in resp.headers.get("content-type", ""):
        return "", [], None
    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    text = trafilatura.extract(html, url=str(resp.url), include_comments=False, include_tables=True) or ""
    return text, page_images(soup, str(resp.url)), _meta(soup, "og:site_name")


def _model_vram(http, repo_id: str) -> tuple[str, str]:
    info = hf.model_info(http, repo_id)
    return vram.describe(vram.estimate(info["safetensors"]), info["context"], info["license"]), info["readme"]


def _is_generic(url: str) -> bool:
    return any(g in url for g in GENERIC_IMAGES)


class _Candidates:
    def __init__(self):
        self.ranked: list[tuple[int, int, str, str, str]] = []

    def add(self, priority: int, url: str | None, item: Item, referer: str | None = None, credit: str | None = None):
        if not url or any(url == c[2] for c in self.ranked):
            return
        if _is_generic(url):
            priority = 5
        self.ranked.append((priority, len(self.ranked), url, credit or item.source_name, referer or item.url))

    def result(self) -> list[tuple[str, str, str]]:
        return [(url, credit, referer) for _, _, url, credit, referer in sorted(self.ranked)]


def _tweet_images(http, item: Item, cands: _Candidates, priority: int) -> None:
    """推文自带的图片 / 视频封面，图源写 X 账号。"""
    m = TWEET.match(canonical_url(item.url))
    if not m:
        return
    for url in tweet_media(http, item.url)[:2]:
        cands.add(priority, url, item, item.url, credit=f"X @{m.group(1)}")


def _rename_from_site(item: Item, site_name: str | None) -> None:
    """TLDR 这类线索的信源名先是域名，打开网页后换成网站自己的名字。"""
    if site_name and item.meta.get("via"):
        item.source_name = " ".join(site_name.split())[:40]


def enrich_event(http, ev: Event) -> None:
    it = ev.primary = pick_primary(ev.items, ev.title)
    text = it.meta.get("text") or it.summary
    cands = _Candidates()
    cands.add(0, it.image, it)
    try:
        if it.kind == "model":
            ev.vram, readme = _model_vram(http, it.meta["repo_id"])
            text = readme or text
        elif it.kind == "social" or (it.kind == "inbox" and it.meta.get("tweet")):
            tweet = it.meta.get("tweet") or fetch_tweet(http, it.url)
            if tweet and tweet["text"] and tweet["text"] not in text:
                text = f"{text}\n\n推文原文（{tweet['author']} @{tweet['handle']}）：{tweet['text']}"
            _tweet_images(http, it, cands, 1)
        elif it.kind in PAGE_KINDS:
            page_text, images, site = fetch_page(http, it.url)
            _rename_from_site(it, site)
            if len(page_text) > len(text):
                text = page_text
            for url in images:
                cands.add(1, url, it, it.url)
    except httpx.HTTPError as e:
        failed = type(e).__name__
        log.warning("抓原文失败 %s：%s", it.url, _brief(e))
    else:
        failed = None
    if not ev.vram:            # 主来源是博客、但事件里有 HF 模型仓库时，也算一下显存
        model = next((x for x in ev.items if x.kind == "model"), None)
        if model:
            try:
                ev.vram, _ = _model_vram(http, model.meta["repo_id"])
            except httpx.HTTPError as e:
                log.warning("查模型详情失败 %s：%s", model.url, _brief(e))
    extra = _other_sources(http, ev, it, cands)
    if len(text) < SHORT_SOURCE_CHARS:
        for name, body in extra:
            text += f"\n\n（以下摘自{name}）\n{clip(body, MAX_SOURCE_CHARS // 2)}"
    if failed:
        ev.flags.append(f"主来源网页打不开（{failed}），" + ("依据信源摘要和其他来源写的" if extra else "只能依据信源摘要"))
    ev.source_text = clip(text, MAX_SOURCE_CHARS)
    ev.image_candidates = cands.result()
    ev.image_url = ev.image_candidates[0][0] if ev.image_candidates else None


def _other_sources(http, ev: Event, primary: Item, cands: _Candidates) -> list[tuple[str, str]]:
    """同一事件的其他来源、推文线索附带的链接，也是配图来源。返回打开过的其他来源网页的 (信源名, 正文)。"""
    pages, texts = 0, []
    others = sorted((x for x in ev.items if x is not primary),
                    key=lambda x: LABELS.index(x.label) if x.label in LABELS else len(LABELS))
    for other in others:
        cands.add(3, other.image, other)
        if other.kind in ("social", "inbox"):
            _tweet_images(http, other, cands, 3)
        if other.kind in PAGE_KINDS and other.kind != "repo" and pages < MAX_EXTRA_PAGES:
            pages += 1
            try:
                body, images, site = fetch_page(http, other.url)
                _rename_from_site(other, site)
                for url in images:
                    cands.add(2, url, other, other.url)
                if body:
                    texts.append((other.source_name, body))
            except httpx.HTTPError as e:
                log.info("找配图时打不开 %s：%s", other.url, _brief(e))
    for item in [primary] + others:
        for link in (item.meta.get("links") or [])[:2]:
            if pages >= MAX_EXTRA_PAGES + 1:
                return texts
            pages += 1
            try:
                _, images, site = fetch_page(http, link)
                for url in images:
                    cands.add(4, url, item, link, credit=site or (urlsplit(link).hostname or "").removeprefix("www."))
            except httpx.HTTPError as e:
                log.info("找配图时打不开 %s：%s", link, _brief(e))
    return texts


def _brief(e: httpx.HTTPError) -> str:
    """一行的错误说明（httpx 的消息带一行文档链接）。"""
    return str(e).splitlines()[0] if str(e) else type(e).__name__
