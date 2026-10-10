"""Running one asynchronous video task to completion: submit, persist the task id, poll, download.

Shared by the bench and the production pipeline. The task id is written to the state file before
polling starts, so an interrupted run resumes polling the same task instead of paying for a new one.
The cost is recorded once, when the provider reports success (state["charged"]), and a failed
download leaves the task resumable: the next run polls it again for a fresh URL instead of paying
for a new task.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import httpx

from .errors import ProviderError, Rejected, Unconfirmed
from .net import download
from .providers.base import VideoProvider, VideoRequest

log = logging.getLogger(__name__)

TERMINAL = {"rejected", "failed"}
# A submit that may have created a billed task we cannot see: not resent automatically (clear the
# job file, e.g. with `make --revideo`, after checking the provider console).
UNCONFIRMED = "unconfirmed"
Fetch = Callable[[httpx.Client, str, Path], None]
Callback = Callable[[dict], None]


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return data


def submit_video(p: VideoProvider, req: VideoRequest, state_path: Path, base: dict,
                 on_unconfirmed: Callback | None = None) -> dict:
    """Submit and record the task id; a refusal or failure at submit time is recorded as the result."""
    with p.client() as http:
        try:
            task_id = p.submit(http, req)
        except Rejected as e:
            return write_json(state_path, {**base, "state": "rejected", "message": str(e)})
        except Unconfirmed as e:
            state = write_json(state_path, {**base, "state": UNCONFIRMED, "message": str(e)})
            if on_unconfirmed:
                on_unconfirmed(state)
            return state
        except ProviderError as e:
            return write_json(state_path, {**base, "state": "failed", "message": str(e)})
    return write_json(state_path, {**base, "state": "pending", "task_id": task_id, "submitted_at": time.time()})


def wait_video(p: VideoProvider, state: dict, state_path: Path, out: Path, *, poll_seconds: float,
               timeout_seconds: float, fetch: Fetch = download, on_done: Callback | None = None) -> dict:
    """Poll until the task finishes, then download it. On timeout the state stays pending (with a
    message) so the next run resumes polling. On success `on_done` records the cost once, before the
    download; a failed download keeps the task pending so the next run fetches it again."""
    deadline = time.time() + timeout_seconds
    with p.client() as http:
        while True:
            try:
                status = p.poll(http, state["task_id"])
            except ProviderError as e:  # transient query errors: keep polling until the deadline
                log.warning("%s 查询失败：%s", state_path.stem, e)
                status = None
            if status and status.state != "pending":
                break
            if time.time() > deadline:
                state["message"] = "等待超时，任务可能还在排队；再次运行会继续查询，不会重新提交"
                return write_json(state_path, state)
            time.sleep(poll_seconds)
        state.update(state=status.state, message=status.message, usage=status.usage, finished_at=time.time())
        state["latency_s"] = round(state["finished_at"] - state.get("submitted_at", state["finished_at"]), 1)
        if status.state == "done":
            if not status.url:
                state.update(state="failed", message="接口没有返回视频地址")
                return write_json(state_path, state)
            if on_done and not state.get("charged"):
                on_done(state)
                state["charged"] = True
                write_json(state_path, state)
            try:
                fetch(http, status.url, out)
            except ProviderError as e:
                state.update(state="pending", message=f"视频已生成但下载失败（{e}）；再次运行会重新查询并下载，不会重新提交")
    return write_json(state_path, state)


def run_video(p: VideoProvider, req: VideoRequest, out: Path, state_path: Path, base: dict, *,
              poll_seconds: float, timeout_seconds: float, fetch: Fetch = download,
              on_done: Callback | None = None, on_unconfirmed: Callback | None = None,
              retry_failed: bool = True) -> dict:
    """Submit (or resume) one task and wait for it. A previous refused/failed attempt is replaced when
    retry_failed is set; otherwise it is returned unchanged. An unconfirmed submit is never resent."""
    if out.exists():
        return {**read_json(state_path), "state": "done"}
    state = read_json(state_path)
    if state.get("state") == UNCONFIRMED:
        return state
    if state.get("state") in TERMINAL:
        if not retry_failed:
            return state
        state = {}
    if not state.get("task_id"):
        state = submit_video(p, req, state_path, base, on_unconfirmed)
        if state["state"] != "pending":
            return state
    return wait_video(p, state, state_path, out, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds,
                      fetch=fetch, on_done=on_done)
