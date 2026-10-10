"""Append-only cost ledger (JSON lines). Every record carries `cny`; records billed through a credit
plan (Ark Agent Plan) also carry `plan` and `afp`. Costs are estimates unless a provider reported
its actual usage."""
import json
import threading
import time
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def add(self, **record) -> None:
        record = {"ts": round(time.time(), 1), **record}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict]:
        """All records; a damaged line (e.g. a crash mid-write) is skipped rather than blocking the
        quota check."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def total(self, run: str | None = None) -> float:
        return sum(e.get("cny", 0.0) for e in self.entries() if run is None or e.get("run") == run)

    def afp(self, plan: str, since: float = 0.0) -> float:
        """Plan credits recorded for `plan` since a Unix time."""
        return sum(float(e.get("afp") or 0) for e in self.entries()
                   if e.get("plan") == plan and e.get("ts", 0) >= since)
