import io
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from ai_daily import jobs, native_host as nh
from ai_daily.config import Settings
from ai_daily import host_install
from ai_daily.host_install import build_manifest

DAY = "2026-09-30"
DRAFT = """# AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一

## 头条｜OpenAI 发布 GPT-6.1 Sol

正文。

【待核对：1 条原文句在来源里没找到，已去掉】

**我的看法**：【我的看法：待写】

## LLM

### 1. vLLM 发布新版本

正文。

【待核对：这些数字在原文里没找到：80】

## 快讯

- **快讯**：一句话。（[来源](https://example.com/a)）

---

本文由 AI 辅助收集信息、生成初稿，经人工核对并点评。
"""


@pytest.fixture
def settings(tmp_path):
    return Settings(vault=tmp_path / "vault", brand_python="python", data_dir=tmp_path / "data",
                    llm_base_url="", llm_api_key="", llm_model_triage="t", llm_model_write="w", llm_extra_body={})


def write_draft(settings, text=DRAFT):
    d = settings.daily_dir / "2026-09"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{DAY}_AI日报.md").write_text(text, encoding="utf-8")
    return d


def test_message_framing_roundtrip():
    buf = io.BytesIO()
    nh.write_message(buf, {"cmd": "ping", "text": "中文"})
    buf.seek(0)
    assert nh.read_message(buf) == {"cmd": "ping", "text": "中文"}
    assert nh.read_message(buf) is None                       # EOF


def test_parse_draft_attaches_flags_to_sections():
    d = nh.parse_draft(DRAFT)
    assert d["title"] == "AI 早报 09.30｜GPT-6.1 Sol 价格降到五分之一"
    assert d["opinion_written"] is False
    assert d["flags"] == [{"section": "头条｜OpenAI 发布 GPT-6.1 Sol", "text": "1 条原文句在来源里没找到，已去掉"},
                          {"section": "vLLM 发布新版本", "text": "这些数字在原文里没找到：80"}]


def test_status_without_and_with_draft(settings):
    s = nh.handle({"cmd": "status", "date": DAY}, settings)
    assert s["ok"] and s["article"] is None and s["job"] == {"state": "none"}
    assert s["llm"]["ready"] is False and s["llm"]["source"] == "none"
    write_draft(settings)
    s = nh.handle({"cmd": "status", "date": DAY}, settings)
    assert len(s["article"]["flags"]) == 2 and not s["article"]["opinion_written"]
    assert any("我的看法" in p for p in s["article"]["problems"])
    assert s["embed"] is None and s["published"] is False


def test_bad_date_and_unknown_command(settings):
    assert nh.handle({"cmd": "status", "date": "../../etc"}, settings) == {"ok": False, "error": "日期格式不对：../../etc"}
    assert nh.handle({"cmd": "rm"}, settings)["ok"] is False


def test_panel_cannot_generate_drafts(settings):
    # 2026-10-10：面板去掉了“生成草稿”，早报只在 Claude Code 里生成
    assert nh.handle({"cmd": "run", "date": DAY}, settings) == {"ok": False, "error": "未知命令：run"}
    with pytest.raises(ValueError):
        jobs.start("run", ["--date", DAY], settings.jobs_dir)


def test_publish_returns_problems_without_starting_a_job(settings):
    write_draft(settings)
    r = nh.handle({"cmd": "publish", "date": DAY}, settings)
    assert r["ok"] and "job" not in r and len(r["problems"]) == 2


def test_inbox_add_dedupes_and_validates(settings):
    r = nh.handle({"cmd": "inbox_add", "url": "https://x.com/OpenAI/status/1", "note": "发布会\n重点"}, settings)
    assert r == {"ok": True, "added": True, "count": 1}
    assert nh.handle({"cmd": "inbox_add", "url": "https://x.com/OpenAI/status/1"}, settings)["added"] is False
    assert nh.handle({"cmd": "inbox_add", "url": "javascript:alert(1)"}, settings)["ok"] is False
    text = settings.inbox.read_text(encoding="utf-8")
    assert text.startswith("# AI 早报 · 手动投喂") and text.endswith("- https://x.com/OpenAI/status/1 发布会 重点\n")
    assert nh.handle({"cmd": "inbox_list"}, settings)["recent"] == ["https://x.com/OpenAI/status/1 发布会 重点"]


def test_open_builds_obsidian_uri(settings):
    write_draft(settings)
    r = nh.handle({"cmd": "open", "date": DAY, "which": "article", "dry_run": True}, settings)
    assert r["uri"] == "obsidian://open?vault=vault&file=AI_Daily/2026-09/2026-09-30_AI%E6%97%A5%E6%8A%A5"
    assert nh.handle({"cmd": "open", "date": DAY, "which": "review", "dry_run": True}, settings)["ok"] is False


def test_open_refuses_a_vault_obsidian_has_never_opened(settings, tmp_path, monkeypatch):
    write_draft(settings)
    config = tmp_path / "obsidian.json"
    config.write_text('{"vaults": {"a": {"path": "/Users/x/Data/Obisidian", "open": true}}}', encoding="utf-8")
    monkeypatch.setattr(nh, "obsidian_config", lambda: config)
    opened = []
    monkeypatch.setattr(nh, "open_external", opened.append)
    r = nh.handle({"cmd": "open", "date": DAY, "which": "article"}, settings)
    assert r["ok"] is False and "打开本地仓库" in r["error"] and opened == []
    config.write_text(json.dumps({"vaults": {"b": {"path": str(settings.vault)}}}), encoding="utf-8")
    assert nh.handle({"cmd": "open", "date": DAY, "which": "article"}, settings)["ok"] is True
    assert opened == ["obsidian://open?vault=vault&file=AI_Daily/2026-09/2026-09-30_AI%E6%97%A5%E6%8A%A5"]
    assert nh.vault_known_to_obsidian(settings.vault, tmp_path / "missing.json")      # 读不到配置就不拦


def test_read_embed_in_chunks(settings, monkeypatch):
    d = write_draft(settings)
    body = "# 标题\n\n" + "x" * 25
    (d / f"{DAY}_AI日报_内嵌图片版.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(nh, "CHUNK_CHARS", 10)
    parts, i, chunks = [], 0, 1
    while i < chunks:
        r = nh.handle({"cmd": "read_embed", "date": DAY, "chunk": i}, settings)
        chunks, i = r["chunks"], i + 1
        parts.append(r["content"])
    assert "".join(parts) == body and r["title"] == "标题" and chunks == 4
    assert nh.handle({"cmd": "read_embed", "date": DAY, "chunk": 9}, settings)["ok"] is False


def test_status_marks_stale_embed(settings):
    d = write_draft(settings)
    embed = d / f"{DAY}_AI日报_内嵌图片版.md"
    embed.write_text("# t", encoding="utf-8")
    old = time.time() - 60
    os.utime(embed, (old, old))
    assert nh.handle({"cmd": "status", "date": DAY}, settings)["embed"]["stale"] is True


def test_detached_job_writes_completion_marker(settings, monkeypatch):
    """真起一个脱离的任务进程：publish 找不到草稿，返回码 2，日志里有原因。"""
    monkeypatch.setenv("AI_DAILY_VAULT", str(settings.vault))
    monkeypatch.setenv("AI_DAILY_DATA", str(settings.data_dir))
    job = jobs.start("publish", ["--date", DAY], settings.jobs_dir)
    assert job["state"] in ("running", "failed") and job["pid"] > 0
    with pytest.raises(RuntimeError) if job["state"] == "running" else _noop():
        jobs.start("publish", ["--date", DAY], settings.jobs_dir)          # 同时只能跑一个
    deadline = time.time() + 60
    while (st := jobs.status(settings.jobs_dir))["state"] == "running" and time.time() < deadline:
        time.sleep(0.3)
    assert st["state"] == "failed" and st["returncode"] == 2
    assert "找不到正文" in st["log_tail"]


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_host_process_speaks_protocol(settings, tmp_path):
    env = dict(os.environ, AI_DAILY_VAULT=str(settings.vault), AI_DAILY_DATA=str(settings.data_dir))
    buf = io.BytesIO()
    nh.write_message(buf, {"cmd": "ping"})
    nh.write_message(buf, {"cmd": "status", "date": DAY})
    out = subprocess.run([sys.executable, "-m", "ai_daily.native_host"], input=buf.getvalue(),
                         capture_output=True, env=env, timeout=60, cwd=Path(__file__).parent.parent)
    reader = io.BytesIO(out.stdout)
    ping, status = nh.read_message(reader), nh.read_message(reader)
    assert ping == {"ok": True, "version": nh.VERSION, "vault": str(settings.vault)}
    assert status["ok"] and status["date"] == DAY
    assert nh.read_message(reader) is None                    # stdout 里没有别的输出


def test_manifest_merges_extension_ids(tmp_path):
    exe = tmp_path / "ai-daily-host.exe"
    m1 = build_manifest(["aaa"], exe)
    m2 = build_manifest(["bbb", "aaa"], exe, m1)
    assert m2["allowed_origins"] == ["chrome-extension://aaa/", "chrome-extension://bbb/"]
    assert m2["type"] == "stdio" and m2["path"] == str(exe) and m2["name"] == nh.HOST_NAME


def test_install_host_on_macos_writes_manifest_into_each_browser_dir(tmp_path, monkeypatch):
    exe = tmp_path / "ai-daily-host"
    monkeypatch.setattr(host_install, "host_executable", lambda: exe)
    master = tmp_path / "data" / "host.json"
    for browser in ("Google/Chrome", "Microsoft Edge", "Doubao"):          # 装了的浏览器才有用户数据目录
        (tmp_path / "Library/Application Support" / browser).mkdir(parents=True)
    assert host_install.install(["abc"], list(host_install.BROWSERS), master, platform="darwin",
                                home=tmp_path) == ["chrome", "edge", "doubao"]
    assert (tmp_path / "Library/Application Support/Doubao/NativeMessagingHosts" / f"{nh.HOST_NAME}.json").is_file()
    assert host_install.install(["abc"], ["doubao"], master, platform="linux", home=tmp_path) == []   # Linux 上没有豆包
    host_install.install(["def"], ["chrome", "edge"], master, platform="darwin", home=tmp_path)
    chrome = tmp_path / "Library/Application Support/Google/Chrome/NativeMessagingHosts" / f"{nh.HOST_NAME}.json"
    edge = tmp_path / "Library/Application Support/Microsoft Edge/NativeMessagingHosts" / f"{nh.HOST_NAME}.json"
    manifest = __import__("json").loads(chrome.read_text(encoding="utf-8"))
    assert manifest["path"] == str(exe) and edge.read_text(encoding="utf-8") == chrome.read_text(encoding="utf-8")
    assert manifest["allowed_origins"] == ["chrome-extension://abc/", "chrome-extension://def/"]
    assert not (tmp_path / "Library/Application Support/Chromium").exists()
    host_install.uninstall(["chrome", "edge", "chromium"], platform="darwin", home=tmp_path)
    assert not chrome.exists() and not edge.exists()


def test_extension_id_is_derived_from_the_unpacked_folder():
    # 2026-09 那台 Windows 上 md 扩展的真实 ID
    assert host_install.extension_id_for_path(r"D:\md\apps\web\.output\chrome-mv3", "win32") == "omlhhhbkjnjbnpgbomhdoeginlijonij"
    assert host_install.extension_id_for_path("/Users/x/md/apps/web/.output/chrome-mv3", "darwin") \
        != host_install.extension_id_for_path("/Users/x/md/apps/web/.output/chrome-mv3", "win32")
