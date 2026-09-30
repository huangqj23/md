"""HTTP 客户端：浏览器 UA、跟随重定向（仓库迁移后旧地址返回 301）、失败重试。"""
import time

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def make_client(**kw) -> httpx.Client:
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}
    headers.update(kw.pop("headers", {}))
    kw.setdefault("timeout", httpx.Timeout(25.0, connect=10.0))
    kw.setdefault("follow_redirects", True)
    return httpx.Client(headers=headers, **kw)


def get(http: httpx.Client, url: str, *, retries: int = 2, **kw) -> httpx.Response:
    """GET：连接错误、超时和 5xx 重试 retries 次；4xx 直接抛 HTTPStatusError。"""
    for attempt in range(retries + 1):
        try:
            resp = http.get(url, **kw)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500 or attempt == retries:
                raise
        except httpx.TransportError:
            if attempt == retries:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")
