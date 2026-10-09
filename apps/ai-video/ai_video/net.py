"""HTTP helpers: retries for transient failures, provider errors turned into exceptions, downloads."""
import os
import time
from pathlib import Path

import httpx

from .errors import ProviderError, classify

RETRY_STATUS = {429, 500, 502, 503, 504}
CONNECT_RETRIES = 3
# The Met's bot protection rejects the default python-httpx agent.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def make_client(**kw) -> httpx.Client:
    kw.setdefault("timeout", httpx.Timeout(180.0, connect=15.0))
    kw.setdefault("follow_redirects", True)
    kw.setdefault("headers", {"User-Agent": UA})
    # AI_VIDEO_PROXY=off ignores the system proxy: domestic endpoints such as api.302ai.com are steadier
    # direct than through a local proxy, which drops TLS handshakes now and then.
    kw.setdefault("trust_env", os.environ.get("AI_VIDEO_PROXY", "system").lower() != "off")
    return httpx.Client(**kw)


def error_from_response(resp: httpx.Response) -> ProviderError:
    code, message = str(resp.status_code), resp.text[:500]
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else (body.get("base_resp") or {})
        code = str(err.get("code") or err.get("err_code") or err.get("status_code") or body.get("code") or code)
        # 302.AI adds a Chinese message next to the vendor's English one.
        message = str(err.get("message_cn") or err.get("message") or err.get("status_msg")
                      or body.get("message") or message)
    return classify(code, message)(f"HTTP {resp.status_code} · {code}: {message}")


def call(http: httpx.Client, method: str, url: str, *, retries: int = 3, **kw) -> httpx.Response:
    """Send a request. Transport errors, 429 and 5xx are retried with backoff (longer for 429, which
    usually means the provider's concurrency limit); other 4xx raise ProviderError / Rejected.

    `retries` covers failures after the request may have reached the server; billed generation calls
    pass retries=0. Connection failures (nothing was sent) are always retried, CONNECT_RETRIES times.
    """
    attempt = connect_failures = 0
    while True:
        try:
            resp = http.request(method, url, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            connect_failures += 1
            if connect_failures > CONNECT_RETRIES:
                raise ProviderError(f"网络错误：{e!r}") from e
            time.sleep(2.0 * connect_failures)
            continue
        except httpx.TransportError as e:
            if attempt >= retries:
                raise ProviderError(f"网络错误：{e!r}") from e
            attempt += 1
            time.sleep(3.0 * attempt)
            continue
        if resp.status_code < 400:
            return resp
        if resp.status_code not in RETRY_STATUS or attempt >= retries:
            raise error_from_response(resp)
        attempt += 1
        time.sleep((10.0 if resp.status_code == 429 else 3.0) * attempt)


def download(http: httpx.Client, url: str, path: Path, attempts: int = 3) -> None:
    """Stream a file to disk; written to a temp name first so a broken download never looks finished.
    Downloads cost nothing, so network failures are retried."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            with http.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise error_from_response(resp)
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            break
        except httpx.TransportError as e:
            tmp.unlink(missing_ok=True)
            if attempt == attempts:
                raise ProviderError(f"下载失败：{e!r}") from e
            time.sleep(3.0 * attempt)
    os.replace(tmp, path)
