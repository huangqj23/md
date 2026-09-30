"""手动投喂：AI_Daily/_inbox.md 里每行贴一个链接，可以跟一句备注。
推文链接用 X 的 oEmbed 接口（免费、不用登录）取作者和正文；其他链接取网页标题。"""
import logging
import math
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from ..models import Item, canonical_url
from ..net import get
from ..textutil import clip, html_to_text

log = logging.getLogger(__name__)
URL = re.compile(r"https?://[^\s<>()\[\]]+")
TWEET = re.compile(r"^https://x\.com/([^/]+)/status/\d+")
OEMBED = "https://publish.twitter.com/oembed"

INBOX_HEADER = """# AI 早报 · 手动投喂

> 白天看到值得收录的链接，每行贴一个，后面可以跟一句备注，例如：
> - https://x.com/karpathy/status/123 这条讲 nanochat 新版本
> 每天运行时会读这里的链接；已经收录过的不会重复收录，攒多了可以清空。

"""


def fetch_tweet(http, url: str) -> dict | None:
    """推文作者和正文；不是推文链接或取不到返回 None。"""
    m = TWEET.match(canonical_url(url))
    if not m:
        return None
    try:
        data = get(http, OEMBED, params={"url": url, "omit_script": 1, "dnt": "true"}, retries=1).json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("oEmbed 失败 %s：%s", url, e)
        return None
    quote = BeautifulSoup(data.get("html", ""), "lxml").find("p")
    return {"author": data.get("author_name") or m.group(1), "handle": m.group(1),
            "text": html_to_text(quote.decode_contents()) if quote else ""}


SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _syndication_token(tweet_id: str) -> str:
    """X 嵌入组件用的 token：((id / 1e15) * π) 的 36 进制表示去掉 0 和小数点。"""
    value = int(tweet_id) / 1e15 * math.pi
    whole, frac = int(value), value - int(value)
    digits = ""
    while whole:
        whole, r = divmod(whole, 36)
        digits = _B36[r] + digits
    fraction = ""
    for _ in range(12):
        frac *= 36
        fraction += _B36[int(frac)]
        frac -= int(frac)
    return re.sub(r"0+|\.", "", f"{digits or '0'}.{fraction}")


def tweet_media(http, url: str) -> list[str]:
    """推文自带的图片和视频封面，用 X 嵌入组件的公开接口（不用登录）取。不是推文或取不到返回空。"""
    m = re.match(r"^https://x\.com/[^/]+/status/(\d+)", canonical_url(url))
    if not m:
        return []
    try:
        data = get(http, SYNDICATION, params={"id": m.group(1), "token": _syndication_token(m.group(1)), "lang": "en"},
                   retries=1).json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("取推文配图失败 %s：%s", url, e)
        return []
    media = [p["url"] for p in data.get("photos") or [] if p.get("url")]
    poster = (data.get("video") or {}).get("poster")
    return media + ([poster] if poster else [])


def collect(http, path, now: datetime) -> list[Item]:
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("#", ">")):      # 标题和说明行
            continue
        note = URL.sub("", line).strip(" -*\t")
        for url in URL.findall(line):
            items.append(_item(http, url.rstrip(".,;:，。；"), note, now))
    return items


def _item(http, url: str, note: str, now: datetime) -> Item:
    tweet = fetch_tweet(http, url)
    if tweet:
        return Item(source="inbox", source_name=f"X @{tweet['handle']}", kind="inbox",
                    title=f"{tweet['author']}：{clip(tweet['text'], 80)}", url=url, published=now,
                    summary=clip(f"{note} {tweet['text']}".strip(), 700),
                    meta={"note": note, "text": tweet["text"], "tweet": tweet})
    title = note or url
    try:
        page = BeautifulSoup(get(http, url, retries=0).text, "lxml")
        og = page.find("meta", property="og:title")
        title = (og.get("content") if og else None) or (page.title.get_text(strip=True) if page.title else title)
    except httpx.HTTPError as e:
        log.warning("投喂链接打不开 %s：%s", url, e)
    return Item(source="inbox", source_name="手动投喂", kind="inbox", title=title, url=url,
                published=now, summary=note, meta={"note": note})
