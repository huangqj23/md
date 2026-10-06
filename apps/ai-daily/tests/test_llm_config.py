import json

import httpx2
import pytest

from ai_daily import llm_config as lc
from ai_daily import native_host as nh
from ai_daily import secrets
from ai_daily.config import Settings
from ai_daily.llm import OpenAICompatLLM
from ai_daily.llm_anthropic import AnthropicLLM

KEY = "sk-live-0123456789abcdef"


class FakeKeychain:
    """代替 macOS 的 security 命令，测试不碰真的登录钥匙串。记下每次调用的命令行，用来检查 key 不在命令行里。"""

    def __init__(self):
        self.items, self.argv = {}, []

    def __call__(self, args, stdin=None):
        self.argv.append(args)
        if args == ["-i"]:
            parts = stdin.split()
            account, secret = parts[parts.index("-a") + 1], parts[parts.index("-w") + 1].strip('"')
            self.items[account] = secret
            return ""
        account = args[args.index("-a") + 1]
        if args[0] == "find-generic-password":
            if account not in self.items:
                raise OSError(44, "not found")
            return self.items[account] + "\n"
        self.items.pop(account, None)
        return ""


@pytest.fixture(autouse=True)
def keychain(monkeypatch):
    fake = FakeKeychain()
    monkeypatch.setattr(secrets, "_security", fake)
    return fake


@pytest.fixture
def settings(tmp_path):
    return Settings(vault=tmp_path / "vault", brand_python="python", data_dir=tmp_path / "data",
                    llm_base_url="https://api.deepseek.com", llm_api_key="", llm_model_triage="deepseek-flash",
                    llm_model_write="deepseek-v4-pro", llm_extra_body={})


def provider(view, pid):
    return next(p for p in view["providers"] if p["id"] == pid)


def test_empty_config_lists_presets_and_is_not_ready(settings):
    view = lc.public_view(settings)
    assert view["source"] == "none" and view["encryption"] == secrets.scheme()
    ids = [p["id"] for p in view["providers"]]
    assert ids[:3] == ["deepseek", "qwen", "kimi"] and "anthropic" in ids and "ollama" in ids
    assert view["roles"] == {"triage": {"provider": "deepseek", "model": "deepseek-flash"},
                             "write": {"provider": "deepseek", "model": "deepseek-v4-pro"}}
    assert view["readiness"]["ready"] is False
    assert view["readiness"]["problems"] == ["选题：DeepSeek 还没有填 API key", "写稿：DeepSeek 还没有填 API key"]


def test_saving_a_key_encrypts_it_and_never_returns_it(settings):
    view = lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": f"  {KEY}  "}]})
    raw_text = lc.config_path(settings).read_text(encoding="utf-8")
    assert KEY not in raw_text and f'"{secrets.scheme()}"' in raw_text
    assert KEY not in json.dumps(view, ensure_ascii=False)
    assert provider(view, "deepseek")["has_key"] and provider(view, "deepseek")["key_hint"] == "sk-…cdef"
    assert view["readiness"] == {"source": "file", "ready": True, "problems": [],
                                 "triage": "DeepSeek · deepseek-flash", "write": "DeepSeek · deepseek-v4-pro"}
    raw, _ = lc.load(settings)
    assert lc.stored_key(raw, "deepseek") == KEY


def test_keychain_keeps_key_off_the_command_line_and_cleans_up(settings, keychain, monkeypatch):
    monkeypatch.setattr(secrets, "scheme", lambda: "keychain")
    view = lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": KEY}]})
    assert view["encryption"] == "keychain"
    raw, _ = lc.load(settings)
    assert set(raw["providers"]["deepseek"]["api_key"]) == {"keychain"} and lc.stored_key(raw, "deepseek") == KEY
    assert all(KEY not in " ".join(argv) for argv in keychain.argv) and list(keychain.items.values()) == [KEY]
    lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": KEY + "x"}]})
    assert list(keychain.items.values()) == [KEY + "x"]          # 换 key：旧条目删掉
    lc.apply_update(settings, {"providers": [{"id": "deepseek", "clear_key": True}]})
    assert keychain.items == {}
    with pytest.raises(ValueError):
        secrets.seal('sk-"quoted"')


def test_key_is_kept_unless_replaced_or_cleared(settings):
    lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": KEY}]})
    lc.apply_update(settings, {"providers": [{"id": "deepseek"}], "roles": {"write": {"provider": "deepseek", "model": "deepseek-v4-pro"}}})
    assert provider(lc.public_view(settings), "deepseek")["has_key"]
    view = lc.apply_update(settings, {"providers": [{"id": "deepseek", "clear_key": True}]})
    assert not provider(view, "deepseek")["has_key"] and not view["readiness"]["ready"]


def test_roles_can_use_different_vendors(settings):
    view = lc.apply_update(settings, {
        "providers": [{"id": "deepseek", "api_key": KEY}, {"id": "anthropic", "api_key": "sk-ant-abcdefghijkl"}],
        "roles": {"triage": {"provider": "deepseek", "model": "deepseek-flash"},
                  "write": {"provider": "anthropic", "model": "claude-opus-5"}}})
    assert view["readiness"]["write"] == "Claude（Anthropic） · claude-opus-5"
    pair = lc.build_pair(settings)
    assert isinstance(pair.triage, OpenAICompatLLM) and isinstance(pair.write, AnthropicLLM)
    assert pair.triage.label == "DeepSeek · deepseek-flash"


def test_same_provider_and_model_share_one_client(settings):
    lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": KEY}],
                               "roles": {"triage": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                                         "write": {"provider": "deepseek", "model": "deepseek-v4-pro"}}})
    pair = lc.build_pair(settings)
    assert pair.triage is pair.write


def test_custom_provider_lifecycle_and_url_rules(settings):
    view = lc.apply_update(settings, {"providers": [{"name": "4090 上的 vLLM", "base_url": "http://10.101.100.36:8000/v1/"}]})
    custom = next(p for p in view["providers"] if p["custom"])
    assert custom["base_url"] == "http://10.101.100.36:8000/v1" and custom["requires_key"] is False
    view = lc.apply_update(settings, {"roles": {"triage": {"provider": custom["id"], "model": "Qwen3-32B"},
                                                "write": {"provider": custom["id"], "model": "Qwen3-32B"}}})
    assert view["readiness"]["ready"]                          # 自建服务可以不填 key
    view = lc.apply_update(settings, {"remove": [custom["id"]]})
    assert not any(p["custom"] for p in view["providers"])
    assert view["roles"]["triage"] == {"provider": "deepseek", "model": "deepseek-flash"}
    for bad in ("http://api.example.com/v1", "ftp://x", "https://user:pw@api.example.com", "not a url"):
        with pytest.raises(ValueError):
            lc.apply_update(settings, {"providers": [{"name": "x", "base_url": bad}]})
    with pytest.raises(ValueError, match="未知厂商"):
        lc.apply_update(settings, {"providers": [{"id": "nope", "api_key": KEY}]})
    with pytest.raises(ValueError, match="API key 格式不对"):
        lc.apply_update(settings, {"providers": [{"id": "deepseek", "api_key": "sk bad"}]})


def test_preset_base_url_override_and_reset(settings):
    view = lc.apply_update(settings, {"providers": [{"id": "qwen", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}]})
    assert provider(view, "qwen")["base_url"].startswith("https://dashscope-intl")
    view = lc.apply_update(settings, {"providers": [{"id": "qwen", "base_url": ""}]})
    assert provider(view, "qwen")["base_url"] == provider(view, "qwen")["default_base_url"]


def test_env_fallback_until_the_panel_saves(settings):
    settings.llm_api_key = KEY
    view = lc.public_view(settings)
    assert view["source"] == "env" and view["readiness"]["ready"]
    assert view["readiness"]["triage"] == "来自 .env · deepseek-flash"
    assert KEY not in json.dumps(view, ensure_ascii=False)
    assert lc.apply_update(settings, {"providers": [{"id": "kimi", "api_key": KEY}]})["source"] == "file"


def test_connection_test_uses_unsaved_key_and_lists_models(settings):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={"object": "list", "data": [{"id": "deepseek-flash", "object": "model", "created": 1, "owned_by": "d"}]})
        body = {"id": "c", "object": "chat.completion", "created": 1, "model": "deepseek-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}
        return httpx2.Response(200, json=body)

    out = lc.test_connection(settings, "deepseek", "deepseek-flash", api_key=KEY,
                             http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    assert out["success"] and out["reply_ok"] and out["models"] == ["deepseek-flash"] and out["latency_ms"] >= 0
    assert all(r.headers["authorization"] == f"Bearer {KEY}" for r in seen)
    assert not lc.config_path(settings).exists()               # 测试不会顺手保存 key
    with pytest.raises(ValueError, match="还没有 API key"):
        lc.test_connection(settings, "deepseek", "deepseek-flash")


def test_native_host_llm_commands(settings, monkeypatch):
    r = nh.handle({"cmd": "llm_set", "providers": [{"id": "deepseek", "api_key": KEY}]}, settings)
    assert r["ok"] and provider(r, "deepseek")["has_key"]
    r = nh.handle({"cmd": "llm_get"}, settings)
    assert r["ok"] and r["readiness"]["ready"] and KEY not in json.dumps(r, ensure_ascii=False)
    assert nh.handle({"cmd": "status", "date": "2026-09-30"}, settings)["llm"]["ready"]
    r = nh.handle({"cmd": "llm_set", "providers": [{"id": "deepseek", "api_key": "has space"}]}, settings)
    assert r == {"ok": False, "error": "API key 格式不对（不能有空格或换行）"}

    calls = []
    monkeypatch.setattr(lc, "test_connection", lambda s, *a: calls.append(a) or {"success": False, "error": "401"})
    r = nh.handle({"cmd": "llm_test", "provider": "kimi", "model": "kimi-k3", "api_key": "sk-x"}, settings)
    # 连接失败也是一次成功的命令调用：失败原因在 success/error 里，不能变成协议层的 ok=false
    assert r == {"ok": True, "success": False, "error": "401"} and calls == [("kimi", "kimi-k3", "sk-x", "")]


def test_local_endpoints_bypass_the_system_proxy(monkeypatch):
    """httpx2 不认 Windows 的代理绕过列表：本机 / 内网地址必须显式直连，公网地址照常走系统代理。"""
    monkeypatch.setattr(lc.urllib.request, "proxy_bypass", lambda host: host.endswith(".corp"))
    assert lc.bypass_proxy("http://127.0.0.1:8000/v1") and lc.bypass_proxy("http://10.101.100.36:8000/v1")
    assert lc.bypass_proxy("http://localhost:11434/v1") and lc.bypass_proxy("https://llm.corp/v1")
    assert not lc.bypass_proxy("https://api.anthropic.com")

    def spec(url, api="openai"):
        return lc.RoleSpec("t", "p", "P", api, url, "k", "m")

    assert lc.build_llm(spec("http://10.101.100.36:8000/v1")).client._client.trust_env is False
    assert lc.build_llm(spec("https://api.deepseek.com")).client._client.trust_env is True
    assert lc.build_llm(spec("http://localhost:9/v1", "anthropic")).client._client.trust_env is False
    assert lc.build_llm(spec("https://api.anthropic.com", "anthropic")).client._client.trust_env is True
