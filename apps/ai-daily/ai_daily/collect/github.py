"""GitHub Trending（综合日榜 + 各语言榜），只留 AI 相关的新项目。

常年挂榜的老项目不算新闻：用 GitHub API 查创建时间，超过 max_repo_age_days 天的不收。创建时间不会变，
查到的存进数据库（调用方传进来的 known），下次不再查。API 未认证每小时只能查 60 次，设了 GITHUB_TOKEN
会带上（每小时 5000 次）；遇到限流就停，本次剩下的项目不查，条目保留，不因为查不到而丢数据。
"""
import logging
import os
import re
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from ..models import Item
from ..net import get
from ..textutil import matches_keywords

log = logging.getLogger(__name__)


def _int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def parse_rows(html: str) -> list[dict]:
    rows = []
    for row in BeautifulSoup(html, "lxml").select("article.Box-row"):
        link = row.select_one("h2 a")
        if link is None:
            continue
        desc_el = row.select_one("p")
        today = row.select_one("span.d-inline-block.float-sm-right")
        total = row.select_one('a[href$="/stargazers"]')
        lang = row.select_one("[itemprop=programmingLanguage]")
        rows.append({"repo": link["href"].strip("/"), "desc": desc_el.get_text(" ", strip=True) if desc_el else "",
                     "stars_today": _int(today.get_text() if today else ""),
                     "stars": _int(total.get_text() if total else ""),
                     "lang": lang.get_text(strip=True) if lang else ""})
    return rows


class RateLimited(Exception):
    pass


def api_get(http, path: str, **params):
    """GitHub API 的 GET，返回 JSON；被限流抛 RateLimited，其他错误照常抛 httpx 的异常。"""
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        return get(http, "https://api.github.com/" + path, headers=headers, params=params or None, retries=0).json()
    except httpx.HTTPStatusError as e:
        resp = e.response
        if resp.status_code in (403, 429) and (resp.headers.get("x-ratelimit-remaining") == "0"
                                               or "rate limit" in resp.text.lower()):
            raise RateLimited from e
        raise


def repo_created(http, repo: str) -> datetime | None:
    """查不到返回 None；被限流抛 RateLimited。"""
    try:
        created = api_get(http, "repos/" + repo).get("created_at")
    except httpx.HTTPStatusError as e:
        log.info("查不到 %s 的创建时间：HTTP %d", repo, e.response.status_code)
        return None
    except (httpx.HTTPError, ValueError) as e:
        log.info("查不到 %s 的创建时间：%s", repo, type(e).__name__)
        return None
    return datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None


def trending(http, cfg: dict, keywords, now: datetime, known: dict[str, str] | None = None) -> list[Item]:
    """known：仓库 → 创建时间（ISO 字符串），新查到的会写回去。"""
    known = {} if known is None else known
    limited = False
    rows: dict[str, dict] = {}
    pages = cfg.get("pages") or [""]
    for page in pages:
        url = "https://github.com/trending" + (f"/{page}" if page else "")
        try:
            html = get(http, url, params={"since": "daily"}).text
        except httpx.HTTPError as e:
            if len(pages) == 1:
                raise
            log.warning("GitHub Trending %s 拉取失败：%s", page or "综合", e)
            continue
        for r in parse_rows(html):
            if r["repo"] not in rows or r["stars_today"] > rows[r["repo"]]["stars_today"]:
                rows[r["repo"]] = r
    max_age = timedelta(days=cfg.get("max_repo_age_days", 365))
    items = []
    for r in sorted(rows.values(), key=lambda r: r["stars_today"], reverse=True):
        if len(items) >= cfg.get("max_items", 15):
            break
        if not matches_keywords(f"{r['repo']} {r['desc']}", keywords):
            continue
        created = datetime.fromisoformat(known[r["repo"]]) if r["repo"] in known else None
        if created is None and not limited:
            try:
                created = repo_created(http, r["repo"])
            except RateLimited:
                limited = True
                log.warning("GitHub API 限流，剩下的热榜项目本次不查创建时间（设 GITHUB_TOKEN 可提高到每小时 5000 次）")
            if created:
                known[r["repo"]] = created.isoformat()
        if created and now - created > max_age:
            continue
        age = f"，创建于 {(now - created).days} 天前" if created else ""
        items.append(Item(source="github-trending", source_name="GitHub Trending", kind="repo",
                          title=f"{r['repo']}：{r['desc']}" if r["desc"] else r["repo"],
                          url=f"https://github.com/{r['repo']}",
                          summary=f"今日 +{r['stars_today']} star，共 {r['stars']} star" + (f"，{r['lang']}" if r["lang"] else "") + age,
                          image=f"https://opengraph.githubassets.com/1/{r['repo']}",
                          meta={"stars_today": r["stars_today"], "text": r["desc"],
                                "created": created.isoformat() if created else None}))
    return items
