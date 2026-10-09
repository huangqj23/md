"""Paths, .env loading and YAML config files."""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines (# starts a comment); variables already in the environment win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    data_dir: Path
    providers_file: Path
    cases_file: Path
    budget_cny: float


def load_settings() -> Settings:
    load_env()
    return Settings(
        data_dir=Path(os.environ.get("AI_VIDEO_DATA", ROOT / "data")),
        providers_file=Path(os.environ.get("AI_VIDEO_PROVIDERS", ROOT / "providers.yaml")),
        cases_file=Path(os.environ.get("AI_VIDEO_CASES", ROOT / "bench" / "cases.yaml")),
        budget_cny=float(os.environ.get("BENCH_BUDGET_CNY") or 300),
    )


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
