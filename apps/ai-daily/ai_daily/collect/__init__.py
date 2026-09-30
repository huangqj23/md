"""并发跑所有信源；单个信源失败只记录，不影响其他信源。"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ..models import Item
from ..textutil import compile_keywords
from . import feeds, github, hf, hn, inbox, labs, openrouter

log = logging.getLogger(__name__)


def release_specs(sources: dict) -> list[dict]:
    return [{"key": f"gh:{repo}", "name": repo, "url": f"https://github.com/{repo}/releases.atom",
             "kind": "release", "track": track, "latest_only": True}
            for track, repos in (sources.get("github_releases") or {}).items() for repo in repos]


def collect_all(http, sources: dict, *, now: datetime, since: datetime, inbox_path=None,
                openrouter_prev: dict | None = None,
                repo_created: dict | None = None) -> tuple[list[Item], dict, dict | None]:
    """返回 (条目, 各信源条数或错误, OpenRouter 新快照)。OpenRouter 快照、GitHub 仓库创建时间缓存由调用方
    存取（repo_created 会被补上新查到的），这样采集线程里不碰 SQLite。"""
    kw = compile_keywords(sources.get("ai_keywords", []))
    handles = sources.get("official_x_handles", [])
    hf_cfg = sources.get("hf") or {}
    jobs = {}
    for spec in list(sources.get("feeds") or []) + release_specs(sources):
        jobs[spec["key"]] = lambda s=spec: feeds.collect_feed(http, s, since, kw, handles)
    jobs["hf-papers"] = lambda: hf.daily_papers(http, hf_cfg, since)
    jobs["hf-trending"] = lambda: hf.trending_models(http, hf_cfg, hf_cfg.get("orgs", []), now)
    jobs["hf-orgs"] = lambda: hf.org_models(http, hf_cfg, since)
    jobs["github-trending"] = lambda: github.trending(http, sources.get("github_trending") or {}, kw, now,
                                                      repo_created)
    jobs["hn"] = lambda: hn.collect(http, sources.get("hn") or {}, kw, since)
    lab_cfg = sources.get("labs") or {}
    if lab_cfg.get("github_orgs"):
        jobs["gh-orgs"] = lambda: labs.org_repos(http, lab_cfg["github_orgs"], since)
    if lab_cfg.get("deepseek_news"):
        jobs["deepseek-news"] = lambda: labs.deepseek_news(http, since, now)
    if lab_cfg.get("zai_releases"):
        jobs["zai-releases"] = lambda: labs.zai_releases(http, since, now)
    if (sources.get("openrouter") or {}).get("enabled", True):
        jobs["openrouter"] = lambda: openrouter.snapshot(http)
    if inbox_path is not None:
        jobs["inbox"] = lambda: inbox.collect(http, inbox_path, now)

    items, stats, snap = [], {}, None
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                got = fut.result()
            except Exception as e:  # noqa: BLE001 —— 任何信源出错都不能拖垮整次运行
                stats[name] = f"失败：{type(e).__name__}: {e}"[:200]
                log.warning("信源 %s 失败：%s", name, e)
                continue
            if name == "openrouter":
                snap = got
                got = openrouter.diff(snap, openrouter_prev, since)
            stats[name] = len(got)
            items.extend(got)
    return items, stats, snap
