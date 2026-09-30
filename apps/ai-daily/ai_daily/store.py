"""SQLite：条目首次出现时间（去重）、OpenRouter 快照、已发布 / 已写进草稿的链接和标题、GitHub 仓库创建时间。"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Item, canonical_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT, kind TEXT, title TEXT, url TEXT,
    published TEXT, first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    name TEXT, day TEXT, data TEXT,
    PRIMARY KEY (name, day)
);
CREATE TABLE IF NOT EXISTS published (
    day TEXT, url TEXT,
    PRIMARY KEY (day, url)
);
CREATE TABLE IF NOT EXISTS published_titles (
    day TEXT, title TEXT,
    PRIMARY KEY (day, title)
);
CREATE TABLE IF NOT EXISTS drafted (
    day TEXT, url TEXT,
    PRIMARY KEY (day, url)
);
CREATE TABLE IF NOT EXISTS drafted_titles (
    day TEXT, title TEXT,
    PRIMARY KEY (day, title)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS repo_created (
    repo TEXT PRIMARY KEY, created TEXT NOT NULL
);
"""
BEFORE_KINDS = ("official", "media", "newsletter", "social", "community", "release", "model", "inbox")


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def mark_seen(self, items: list[Item], now: datetime) -> None:
        """记录首次出现时间，写进 item.meta["first_seen"]。热门榜这类没有发布时间的条目靠它判断新旧。"""
        for it in items:
            row = self.db.execute("SELECT first_seen FROM items WHERE id = ?", (it.id,)).fetchone()
            if row:
                it.meta["first_seen"] = datetime.fromisoformat(row[0])
                continue
            self.db.execute(
                "INSERT INTO items (id, source, kind, title, url, published, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (it.id, it.source, it.kind, it.title, it.url,
                 it.published.isoformat() if it.published else None, now.isoformat()))
            it.meta["first_seen"] = now
        self.db.commit()

    def seen_before(self, since: datetime, hours: int = 36, limit: int = 200) -> list[tuple[datetime, str, str]]:
        """窗口开始前 hours 小时内发布、以前的运行采集到过的条目 (发布时间, 信源, 标题)，新的在前。
        给选题识别“媒体今天才转述的旧闻”；论文、热榜这类不带新闻性的不列。"""
        lo = since - timedelta(hours=hours)
        rows = self.db.execute(
            f"SELECT published, source, title FROM items WHERE published IS NOT NULL AND first_seen >= ? "
            f"AND kind IN ({','.join('?' * len(BEFORE_KINDS))})",
            ((lo - timedelta(days=3)).isoformat(), *BEFORE_KINDS))
        out = {}
        for published, source, title in rows:
            when = datetime.fromisoformat(published)
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            if lo <= when < since:
                out.setdefault(title, (when, source, title))
        return sorted(out.values(), reverse=True)[:limit]

    # ---- 键值（上次生成时间等）
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    # ---- 快照
    def save_snapshot(self, name: str, day: date, data) -> None:
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)",
                        (name, day.isoformat(), json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    def previous_snapshot(self, name: str, day: date):
        """day 之前最近的一份快照，没有返回 None。"""
        row = self.db.execute(
            "SELECT data FROM snapshots WHERE name = ? AND day < ? ORDER BY day DESC LIMIT 1",
            (name, day.isoformat())).fetchone()
        return json.loads(row[0]) if row else None

    # ---- 已发布 / 已写进草稿
    def mark_published(self, day: date, urls: list[str], titles: list[str]) -> None:
        d = day.isoformat()
        self.db.executemany("INSERT OR IGNORE INTO published VALUES (?, ?)", [(d, canonical_url(u)) for u in urls])
        self.db.executemany("INSERT OR IGNORE INTO published_titles VALUES (?, ?)", [(d, t) for t in titles])
        self.db.commit()

    def mark_drafted(self, day: date, urls: list[str], titles: list[str]) -> None:
        """每次生成都记下草稿正文里的链接和标题，同一天重新生成时覆盖。没走 publish 的日子，第二天也不会重复。"""
        d = day.isoformat()
        self.db.execute("DELETE FROM drafted WHERE day = ?", (d,))
        self.db.execute("DELETE FROM drafted_titles WHERE day = ?", (d,))
        self.db.executemany("INSERT OR IGNORE INTO drafted VALUES (?, ?)", [(d, canonical_url(u)) for u in urls])
        self.db.executemany("INSERT OR IGNORE INTO drafted_titles VALUES (?, ?)", [(d, t) for t in titles])
        self.db.commit()

    def has_published(self, day: date) -> bool:
        return self.db.execute("SELECT 1 FROM published WHERE day = ? LIMIT 1", (day.isoformat(),)).fetchone() is not None

    def published_urls(self, day: date, days: int = 7) -> set[str]:
        """day 之前 days 天内发过、或写进过草稿的链接（规范化后），选题时排除。当天重新生成不受影响。"""
        args = ((day - timedelta(days=days)).isoformat(), day.isoformat()) * 2
        rows = self.db.execute("SELECT url FROM published WHERE day >= ? AND day < ? "
                               "UNION SELECT url FROM drafted WHERE day >= ? AND day < ?", args)
        return {r[0] for r in rows}

    def recent_titles(self, day: date, days: int = 3) -> list[str]:
        """近几天已发、或写进过草稿的条目标题，给选题做“别重复、是不是后续”的判断。"""
        args = ((day - timedelta(days=days)).isoformat(), day.isoformat()) * 2
        rows = self.db.execute("SELECT day, title FROM published_titles WHERE day >= ? AND day < ? "
                               "UNION SELECT day, title FROM drafted_titles WHERE day >= ? AND day < ? "
                               "ORDER BY day", args)
        return [f"{d} {t}" for d, t in rows]

    # ---- GitHub 仓库创建时间（不会变，查过就存）
    def repo_created(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT repo, created FROM repo_created"))

    def save_repo_created(self, known: dict[str, str]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO repo_created VALUES (?, ?)", list(known.items()))
        self.db.commit()
