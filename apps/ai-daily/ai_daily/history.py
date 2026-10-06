"""从 vault 里已有的几期补记去重信息。

去重靠库（data/ai_daily.db）里记的“前几期写过哪些链接、标题，上一期什么时候生成的”。库不进 git，
换电脑、或者另一台电脑生成过几期时，新库什么都不知道，就会把前几期写过的事再选一遍。vault 跟着 git 走，
正文、审稿清单、内嵌版都在，生成前从这里补齐，换电脑就不用拷库。
"""
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from . import publish, render
from .store import Store

log = logging.getLogger(__name__)
LOOKBACK_DAYS = 7           # 和 Store.published_urls 排除的天数一致


def sync_from_vault(store: Store, daily_dir: Path, day: date, days: int = LOOKBACK_DAYS) -> list[date]:
    """day 之前 days 天里 vault 有正文的每一期：
    - 正文里的链接和标题重新记为“写进过草稿”（以 vault 里的最终稿为准，改过的稿也对得上）；
    - 有内嵌版、库里却没有发布记录的，记为已发布；
    - 库里没有那天的生成时间，从审稿清单的“生成时间”补上（2026-10-02 以前的清单没有这一行，就跳过）。
    返回补记过的日期。"""
    synced = []
    for back in range(days, 0, -1):
        d = day - timedelta(days=back)
        p = render.paths(daily_dir / f"{d:%Y-%m}", d)
        if not p["article"].is_file():
            continue
        text = p["article"].read_text(encoding="utf-8")
        store.mark_drafted(d, *publish.article_refs(text))
        embed = p["article"].with_name(f"{p['stem']}{render.EMBED_SUFFIX}.md")
        if embed.is_file() and not store.has_published(d):
            store.mark_published(d, *publish.article_refs(text))
        if not store.has_last_run(d) and p["review"].is_file():
            m = render.GENERATED.search(p["review"].read_text(encoding="utf-8"))
            if m:
                try:
                    store.mark_run(d, datetime.fromisoformat(m.group(1)))
                except ValueError:
                    log.warning("%s 的生成时间读不出来：%s", p["review"].name, m.group(1))
        synced.append(d)
    if synced:
        log.info("从 vault 补记前几期：%s", "、".join(f"{d:%m-%d}" for d in synced))
    return synced
