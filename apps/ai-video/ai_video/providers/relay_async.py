"""302.AI async mode for slow synchronous endpoints.

Any request sent with ?async=true returns a task_id at once; GET /async_result?task_id=… later
returns the original response (JSON as is, files as a URL) — answering err="result pending" until
it is ready. This avoids the gateway's ~100 s limit (HTTP 524) on image generation, where a timed-out
request may still have been completed and billed upstream, so it must not simply be retried.
"""
import json
import time

import httpx

from ..errors import ProviderError, classify
from ..net import call

ASYNC_PENDING_HTTP = {202, 404, 425}


def relay_async(http: httpx.Client, method: str, url: str, *, root: str, headers: dict,
                timeout_s: float = 900, poll_s: float = 5, **kw) -> dict:
    """Submit asynchronously and wait; returns the original JSON response of the endpoint."""
    params = dict(kw.pop("params", None) or {})
    params["async"] = "true"
    submitted = call(http, method, url, params=params, headers=headers, retries=0, **kw).json()
    task_id = submitted.get("task_id") or submitted.get("id")
    if not task_id and submitted.get("data"):
        return submitted  # endpoint ignored ?async=true and answered synchronously; the work is done
    if not task_id:
        raise ProviderError(f"异步提交没有返回 task_id：{str(submitted)[:300]}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = http.get(f"{root}/async_result", params={"task_id": task_id}, headers=headers)
        except httpx.TransportError:  # polling is free; a dropped connection just means ask again
            time.sleep(poll_s)
            continue
        if resp.status_code == 200:
            result = resp.json()
            err = str(result.get("err") or "")
            if err and "pending" not in err.lower():
                raise classify("", err)(f"生成失败：{err}")
            if not err and result.get("data"):
                payload = result["data"]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except ValueError:
                        payload = {"url": payload}
                status = int(result.get("status_code") or 200)
                if status >= 400:
                    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                    message = error.get("message") or str(payload)[:300]
                    raise classify(error.get("code"), message)(f"HTTP {status} · {error.get('code')}: {message}")
                return payload
        elif resp.status_code not in ASYNC_PENDING_HTTP:
            raise ProviderError(f"查询异步结果失败 HTTP {resp.status_code}：{resp.text[:200]}")
        time.sleep(poll_s)
    raise ProviderError(f"等待异步结果超时（task_id={task_id}）")
