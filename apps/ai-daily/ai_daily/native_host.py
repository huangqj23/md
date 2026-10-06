"""浏览器扩展的 Native Messaging host（md 扩展里的“AI 早报”面板通过它操作本机的早报流程）。

协议：每条消息 = 4 字节小端长度 + UTF-8 JSON。请求 {"cmd": ..., ...}，回复 {"ok": true, ...} 或
{"ok": false, "error": ...}。Chrome 每次 sendNativeMessage 都会拉起一个新进程，所以这里只做快操作；
生成草稿、发布这类慢操作交给 jobs 在独立进程里跑，面板轮询 job 状态。
"""
import json
import os
import re
import struct
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import jobs, llm_config, render
from .config import load_settings
from .publish import PLACEHOLDER, check
from .store import Store

HOST_NAME = "com.hollis23.ai_daily"
VERSION = 1
CHUNK_CHARS = 200_000          # 单条回复上限 1 MB（Chrome 限制），按字符分块读大文件
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FLAG = re.compile(r"【待核对：([^】]*)】")
SECTION = re.compile(r"^(?:##\s+(头条)｜(.+)|###\s+\d+\.\s+(.+)|##\s+(.+))$")
EMBED_SUFFIX = render.EMBED_SUFFIX


def read_message(stream) -> dict | None:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<I", raw)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream, message: dict) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def _day(msg: dict) -> date:
    value = msg.get("date") or datetime.now().astimezone().date().isoformat()
    if not DATE.match(str(value)):
        raise ValueError(f"日期格式不对：{value}")
    return date.fromisoformat(value)


def parse_draft(text: str) -> dict:
    """标题、【待核对】（带所在条目）、“我的看法”是否已写。"""
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    flags, section = [], ""
    for line in text.splitlines():
        m = SECTION.match(line.strip())
        if m:
            section = "头条｜" + m.group(2) if m.group(1) else (m.group(3) or m.group(4))
        for f in FLAG.findall(line):
            flags.append({"section": section, "text": f})
    return {"title": title, "flags": flags, "opinion_written": PLACEHOLDER not in text}


def _paths(settings, day: date) -> dict:
    p = render.paths(settings.daily_dir / f"{day:%Y-%m}", day)
    p["embed"] = p["article"].with_name(f"{p['stem']}{EMBED_SUFFIX}.md")
    return p


def _mtime(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else None


def cmd_status(settings, msg) -> dict:
    day = _day(msg)
    p = _paths(settings, day)
    out = {"date": day.isoformat(), "article": None, "review": p["review"].is_file(),
           "cover": (p["cover"].parent / f"{day.isoformat()}_cover_nowm_wechat.png").is_file(),
           "embed": None, "published": False, "job": jobs.status(settings.jobs_dir), "vault": str(settings.vault),
           "llm": llm_config.readiness(settings)}
    if p["article"].is_file():
        text = p["article"].read_text(encoding="utf-8")
        out["article"] = {"path": str(p["article"]), "modified": _mtime(p["article"]), **parse_draft(text),
                          "problems": check(p["article"])}
    if p["embed"].is_file():
        out["embed"] = {"path": str(p["embed"]), "modified": _mtime(p["embed"]),
                        "stale": p["article"].is_file() and p["article"].stat().st_mtime > p["embed"].stat().st_mtime}
    if settings.db_path.is_file():
        store = Store(settings.db_path)
        out["published"] = store.has_published(day)
        store.close()
    return out


def cmd_run(settings, msg) -> dict:
    day = _day(msg)
    args = ["--date", day.isoformat()]
    if msg.get("force"):
        args.append("--force")
    if msg.get("no_llm"):
        args.append("--no-llm")
    else:
        ready = llm_config.readiness(settings)
        if not ready["ready"]:
            raise ValueError("模型还没配好：" + "；".join(ready["problems"]) + "。去“模型设置”里配置，或勾选“不用 LLM”试跑")
    if _paths(settings, day)["article"].exists() and not msg.get("force"):
        raise ValueError("当天的草稿已经存在，可能已经改过；确定要重新生成请勾选“覆盖”（旧稿会备份成 .bak）")
    return {"job": jobs.start("run", args, settings.jobs_dir)}


def cmd_publish(settings, msg) -> dict:
    day = _day(msg)
    article = _paths(settings, day)["article"]
    if not article.is_file():
        raise ValueError("当天还没有草稿")
    problems = check(article)
    if problems:
        return {"problems": problems}
    args = ["--date", day.isoformat()] + (["--skip-links"] if msg.get("skip_links") else [])
    return {"problems": [], "job": jobs.start("publish", args, settings.jobs_dir)}


def cmd_read_embed(settings, msg) -> dict:
    """内嵌版全文，分块返回（带 base64 图片，可能超过单条消息上限）。"""
    p = _paths(settings, _day(msg))["embed"]
    if not p.is_file():
        raise ValueError("还没有生成内嵌版，先点“生成内嵌版”")
    text = p.read_text(encoding="utf-8")
    chunks = max(1, -(-len(text) // CHUNK_CHARS))
    i = int(msg.get("chunk", 0))
    if not 0 <= i < chunks:
        raise ValueError(f"chunk 越界：{i}")
    return {"title": parse_draft(text)["title"], "chunks": chunks, "index": i,
            "content": text[i * CHUNK_CHARS:(i + 1) * CHUNK_CHARS]}


def obsidian_uri(vault: Path, target: Path) -> str:
    rel = target.relative_to(vault).with_suffix("").as_posix()
    return f"obsidian://open?vault={quote(vault.name)}&file={quote(rel)}"


def obsidian_config(platform: str = sys.platform, home: Path | None = None) -> Path:
    """Obsidian 记录已打开仓库的文件（obsidian.json）。"""
    home = home or Path.home()
    if platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "obsidian" / "obsidian.json"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    return home / ".config" / "obsidian" / "obsidian.json"


def vault_known_to_obsidian(vault: Path, config: Path) -> bool:
    """obsidian.json 里有没有这个仓库。读不到这个文件（没装 Obsidian、格式变了）时不拦，照常打开。"""
    try:
        vaults = json.loads(config.read_text(encoding="utf-8")).get("vaults", {})
    except (OSError, ValueError, AttributeError):
        return True
    want = os.path.normcase(str(vault.resolve()))
    return any(os.path.normcase(str(Path(v.get("path", "")).resolve())) == want for v in vaults.values())


def open_external(uri: str) -> None:
    """交给系统打开 Obsidian，浏览器不会弹“是否打开外部应用”。"""
    if sys.platform == "win32":
        os.startfile(uri)
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", uri], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def cmd_open(settings, msg) -> dict:
    which = msg.get("which", "article")
    if which == "inbox":
        target = settings.inbox
    elif which in ("article", "review"):
        target = _paths(settings, _day(msg))[which]
    else:
        raise ValueError(f"不能打开：{which}")
    if not target.is_file():
        raise ValueError(f"文件还不存在：{target.name}")
    uri = obsidian_uri(settings.vault, target)
    if msg.get("dry_run"):
        return {"uri": uri}
    if not vault_known_to_obsidian(settings.vault, obsidian_config()):
        # obsidian:// 按仓库名找仓库；Obsidian 没打开过这个文件夹时，链接会落到别的仓库或报找不到
        raise ValueError(f"Obsidian 还没有打开过这个仓库：{settings.vault}。在 Obsidian 里点“打开本地仓库”选这个文件夹，之后再点")
    open_external(uri)
    return {"uri": uri}


def _inbox_lines(settings) -> list[str]:
    if not settings.inbox.is_file():
        return []
    return [line for line in settings.inbox.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*-\s+https?://", line)]


def cmd_inbox_add(settings, msg) -> dict:
    url = str(msg.get("url", "")).strip()
    if urlsplit(url).scheme not in ("http", "https") or any(c in url for c in " \n\r"):
        raise ValueError(f"只能投喂 http(s) 链接：{url[:80]}")
    note = " ".join(str(msg.get("note", "")).split())[:200]
    if any(url in line for line in _inbox_lines(settings)):
        return {"added": False, "count": len(_inbox_lines(settings))}
    from .pipeline import ensure_inbox       # 延迟导入：pipeline 依赖较重
    ensure_inbox(settings.inbox)
    text = settings.inbox.read_text(encoding="utf-8")
    with settings.inbox.open("a", encoding="utf-8") as f:
        f.write(("" if text.endswith("\n") else "\n") + f"- {url} {note}".rstrip() + "\n")
    return {"added": True, "count": len(_inbox_lines(settings))}


def cmd_inbox_list(settings, msg) -> dict:
    lines = _inbox_lines(settings)
    return {"count": len(lines), "recent": [line.strip()[2:] for line in lines[-10:]]}


def cmd_llm_test(settings, msg) -> dict:
    return llm_config.test_connection(settings, str(msg.get("provider", "")), str(msg.get("model", "")),
                                      str(msg.get("api_key", "") or ""), str(msg.get("base_url", "") or ""))


COMMANDS = {
    "ping": lambda s, m: {"version": VERSION, "vault": str(s.vault)},
    "status": cmd_status,
    "run": cmd_run,
    "publish": cmd_publish,
    "job": lambda s, m: {"job": jobs.status(s.jobs_dir)},
    "read_embed": cmd_read_embed,
    "open": cmd_open,
    "inbox_add": cmd_inbox_add,
    "inbox_list": cmd_inbox_list,
    "llm_get": lambda s, m: llm_config.public_view(s),
    "llm_set": lambda s, m: llm_config.apply_update(s, m),
    "llm_test": cmd_llm_test,
}


def handle(msg: dict, settings) -> dict:
    fn = COMMANDS.get(msg.get("cmd") if isinstance(msg, dict) else None)
    if fn is None:
        return {"ok": False, "error": f"未知命令：{msg.get('cmd') if isinstance(msg, dict) else msg}"}
    try:
        return {"ok": True, **fn(settings, msg)}
    except (ValueError, RuntimeError, OSError) as e:
        return {"ok": False, "error": str(e)}


def main() -> int:
    settings = load_settings()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while (msg := read_message(stdin)) is not None:
        write_message(stdout, handle(msg, settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
