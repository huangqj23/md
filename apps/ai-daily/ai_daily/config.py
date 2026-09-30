"""路径、环境变量和信源配置。"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / ".env"):
    """读 .env（KEY=VALUE，# 开头为注释）；已经存在的环境变量优先。"""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    vault: Path
    brand_python: str
    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    llm_model_triage: str
    llm_model_write: str
    llm_extra_body: dict

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ai_daily.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def daily_dir(self) -> Path:
        return self.vault / "AI_Daily"

    @property
    def brand_dir(self) -> Path:
        return self.vault / "_brand" / "hollis23"

    @property
    def inbox(self) -> Path:
        return self.daily_dir / "_inbox.md"


def load_settings() -> Settings:
    load_env()
    extra = os.environ.get("LLM_EXTRA_BODY", "").strip()
    return Settings(
        vault=Path(os.environ.get("AI_DAILY_VAULT", r"D:\Obisidian")),
        brand_python=os.environ.get("AI_DAILY_BRAND_PYTHON", "python"),
        data_dir=Path(os.environ.get("AI_DAILY_DATA", ROOT / "data")),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model_triage=os.environ.get("LLM_MODEL_TRIAGE", "deepseek-flash"),
        llm_model_write=os.environ.get("LLM_MODEL_WRITE", "deepseek-v4-pro"),
        llm_extra_body=json.loads(extra) if extra else {},
    )


def load_sources(path=ROOT / "sources.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
