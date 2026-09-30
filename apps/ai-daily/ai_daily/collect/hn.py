"""Hacker News（Algolia API）：首页 + 窗口内高分帖，只留 AI 相关。"""
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..models import Item
from ..net import get
from ..textutil import matches_keywords

API = "https://hn.algolia.com/api/v1/search"


def collect(http, cfg: dict, keywords, since: datetime) -> list[Item]:
    hits = {}
    queries = ({"tags": "front_page", "hitsPerPage": 50},
               {"tags": "story", "hitsPerPage": 100,
                "numericFilters": f"created_at_i>{int(since.timestamp())},points>{cfg.get('min_points', 80)}"})
    for params in queries:
        for h in get(http, API, params=params).json().get("hits", []):
            hits[h["objectID"]] = h
    items = []
    for h in hits.values():
        title = h.get("title") or ""
        created = datetime.fromtimestamp(h.get("created_at_i", 0), timezone.utc)
        if created < since or not matches_keywords(title, keywords):
            continue
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        points, comments = h.get("points") or 0, h.get("num_comments") or 0
        # 链接帖的信源名写被链接的网站（抓原文时再换成网站名），HN 只是发现渠道
        host = (urlsplit(h.get("url") or "").hostname or "").removeprefix("www.")
        items.append(Item(source="hn", source_name=host or "Hacker News", kind="community", title=title,
                          url=h.get("url") or hn_url, published=created,
                          summary=f"HN {points} 分，{comments} 条评论",
                          meta={"points": points, "comments": comments, "hn_url": hn_url, "via": "Hacker News"}))
    items.sort(key=lambda it: it.meta["points"], reverse=True)
    return items[: cfg.get("max_items", 15)]
