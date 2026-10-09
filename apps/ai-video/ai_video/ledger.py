"""Append-only cost ledger (JSON lines). Costs are estimates from the unit prices in providers.yaml."""
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
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def total(self, run: str | None = None) -> float:
        return sum(e.get("cny", 0.0) for e in self.entries() if run is None or e.get("run") == run)
