"""模型配置 data/llm.json：浏览器面板（经 native host）写，流水线和计划任务读。

- 每个厂商一条：预置厂商可以改地址（留空恢复默认）；自定义厂商是任意 OpenAI 兼容接口。
- API key 用 DPAPI 加密落盘；对外（面板）只给首尾几位，不回传明文。
- 两个角色（选题 / 写稿）分别指定厂商 + 模型。
- 没有 llm.json 时回退到 .env 的 LLM_*（旧配置照常可用）。
"""
import ipaddress
import json
import os
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import openai

from . import secrets
from .llm import LLMError, LLMPair, OpenAICompatLLM
from .providers import BY_ID, DEFAULT_PROVIDER, PRESETS

ROLES = ("triage", "write")
ROLE_NAMES = {"triage": "选题", "write": "写稿"}
ENV_PROVIDER = "env"


@dataclass
class RoleSpec:
    role: str
    provider_id: str
    provider_name: str
    api: str
    base_url: str
    api_key: str
    model: str
    json_mode: bool = True
    token_param: str = "max_tokens"
    temperature: bool = True
    extra_body: dict | None = None

    @property
    def label(self) -> str:
        return f"{self.provider_name} · {self.model}"


# ---------------------------------------------------------------- 读写

def config_path(settings) -> Path:
    return settings.data_dir / "llm.json"


def read_raw(settings) -> dict | None:
    try:
        return json.loads(config_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_raw(settings, raw: dict) -> None:
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load(settings) -> tuple[dict, str]:
    """(配置, 来源)。来源：file = llm.json；env = .env 的 LLM_*；none = 都没有。"""
    raw = read_raw(settings)
    if raw is not None:
        return raw, "file"
    if settings.llm_api_key:
        env = {"custom": True, "name": "来自 .env", "base_url": settings.llm_base_url,
               "api_key_plain": settings.llm_api_key, "extra_body": settings.llm_extra_body}
        return {"providers": {ENV_PROVIDER: env},
                "roles": {"triage": {"provider": ENV_PROVIDER, "model": settings.llm_model_triage},
                          "write": {"provider": ENV_PROVIDER, "model": settings.llm_model_write}}}, "env"
    return {"providers": {}, "roles": {}}, "none"


# ---------------------------------------------------------------- 厂商

def provider_info(raw: dict, pid: str) -> dict | None:
    """预置厂商合并用户的覆盖项；自定义厂商原样。未知返回 None。"""
    entry = raw.get("providers", {}).get(pid) or {}
    preset = BY_ID.get(pid)
    if preset:
        return {"id": pid, "name": preset.name, "api": preset.api, "custom": False,
                "base_url": entry.get("base_url") or preset.base_url, "default_base_url": preset.base_url,
                "requires_key": preset.requires_key, "json_mode": preset.json_mode,
                "token_param": preset.token_param, "temperature": preset.temperature,
                "models": list(preset.models), "triage": preset.triage, "write": preset.write,
                "key_url": preset.key_url, "note": preset.note}
    if entry.get("custom"):
        return {"id": pid, "name": entry.get("name") or pid, "api": "openai", "custom": True,
                "base_url": entry.get("base_url", ""), "default_base_url": "", "requires_key": False,
                "json_mode": True, "token_param": "max_tokens", "temperature": True, "models": [],
                "triage": "", "write": "", "key_url": "", "note": "",
                "readonly": pid == ENV_PROVIDER}
    return None


def stored_key(raw: dict, pid: str) -> str:
    entry = raw.get("providers", {}).get(pid) or {}
    if entry.get("api_key_plain"):
        return entry["api_key_plain"]
    try:
        return secrets.unseal(entry.get("api_key"))
    except OSError:
        return ""


def _is_local_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def validate_base_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or any(c.isspace() for c in url):
        raise ValueError(f"接口地址不对：{url[:80]}")
    if parts.username or parts.password:
        raise ValueError("接口地址里不要带用户名和密码")
    if parts.scheme == "http" and not _is_local_host(parts.hostname):
        raise ValueError("http 只允许本机或内网地址；公网地址请用 https，否则 key 会明文传输")
    return url.rstrip("/")


def _validate_key(key: str) -> str:
    key = key.strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        raise ValueError("API key 格式不对（不能有空格或换行）")
    return key


def _validate_model(model: str) -> str:
    model = (model or "").strip()
    if len(model) > 200 or any(c.isspace() for c in model):
        raise ValueError(f"模型名不对：{model[:60]}")
    return model


# ---------------------------------------------------------------- 角色

def resolve(raw: dict, role: str) -> tuple[RoleSpec | None, str | None]:
    """(RoleSpec, None) 或 (None, 问题描述)。"""
    r = raw.get("roles", {}).get(role) or {}
    pid = r.get("provider") or DEFAULT_PROVIDER
    info = provider_info(raw, pid)
    name = ROLE_NAMES[role]
    if info is None:
        return None, f"{name}：厂商 {pid} 不存在"
    model = r.get("model") or info.get(role, "")
    if not model:
        return None, f"{name}：{info['name']} 还没选模型"
    key = stored_key(raw, pid)
    if info["requires_key"] and not key:
        return None, f"{name}：{info['name']} 还没有填 API key"
    entry = raw.get("providers", {}).get(pid) or {}
    return RoleSpec(role, pid, info["name"], info["api"], info["base_url"], key, model, info["json_mode"],
                    info["token_param"], info["temperature"], entry.get("extra_body")), None


def readiness(settings) -> dict:
    raw, source = load(settings)
    out = {"source": source, "ready": True, "problems": []}
    for role in ROLES:
        spec, problem = resolve(raw, role)
        out[role] = spec.label if spec else None
        if problem:
            out["ready"] = False
            out["problems"].append(problem)
    return out


def bypass_proxy(base_url: str) -> bool:
    """本机 / 内网地址，以及 Windows 代理设置里“不走代理”的地址，要直连。

    openai / anthropic SDK 底层的 httpx2 会读系统代理（Windows 注册表里的 ProxyServer），却不认注册表的
    ProxyOverride 绕过列表，只认 NO_PROXY 环境变量。开着 Clash 这类系统代理时，发往 127.0.0.1 / 10.x 的请求
    也会被塞给代理而卡住；海外接口（Claude、OpenAI、Gemini）又必须走代理，所以按地址区分。"""
    host = urlsplit(base_url).hostname or ""
    if _is_local_host(host):
        return True
    try:
        return bool(urllib.request.proxy_bypass(host))
    except OSError:
        return False


def build_llm(spec: RoleSpec, *, timeout: float | None = None, max_retries: int = 3, http_client=None):
    direct = http_client is None and bypass_proxy(spec.base_url)
    if spec.api == "anthropic":
        import anthropic

        from .llm_anthropic import AnthropicLLM      # 只有用 Claude 时才加载 anthropic SDK
        return AnthropicLLM(spec.api_key, spec.model, base_url=spec.base_url, label=spec.label,
                            timeout=timeout or 600, max_retries=max_retries,
                            http_client=anthropic.DefaultHttpxClient(trust_env=False) if direct else http_client)
    if direct:
        http_client = openai.DefaultHttpxClient(trust_env=False)
    return OpenAICompatLLM(spec.base_url, spec.api_key, spec.model, label=spec.label, json_mode=spec.json_mode,
                           token_param=spec.token_param, temperature=spec.temperature, extra_body=spec.extra_body,
                           timeout=timeout or 180, max_retries=max_retries, http_client=http_client)


def build_pair(settings) -> LLMPair:
    raw, _ = load(settings)
    specs, problems = {}, []
    for role in ROLES:
        spec, problem = resolve(raw, role)
        if problem:
            problems.append(problem)
        specs[role] = spec
    if problems:
        raise LLMError("模型还没配好：" + "；".join(problems) + "（在 md 扩展的“AI 早报 → 模型设置”里配置）")
    triage = build_llm(specs["triage"])
    same = (specs["triage"].provider_id, specs["triage"].model) == (specs["write"].provider_id, specs["write"].model)
    return LLMPair(triage, triage if same else build_llm(specs["write"]))


# ---------------------------------------------------------------- 面板用

def public_view(settings) -> dict:
    """给面板看的配置：不含明文 key，只有 has_key / key_hint。"""
    raw, source = load(settings)
    ids = [p.id for p in PRESETS] + [pid for pid, e in raw.get("providers", {}).items() if e.get("custom")]
    providers = []
    for pid in ids:
        info = provider_info(raw, pid)
        key = stored_key(raw, pid)
        providers.append({**{k: v for k, v in info.items() if k not in ("json_mode", "token_param", "temperature")},
                          "has_key": bool(key), "key_hint": secrets.hint(key)})
    roles = {}
    for role in ROLES:
        r = raw.get("roles", {}).get(role) or {}
        pid = r.get("provider") or DEFAULT_PROVIDER
        info = provider_info(raw, pid) or {}
        roles[role] = {"provider": pid, "model": r.get("model") or info.get(role, "")}
    return {"source": source, "encryption": "dpapi" if secrets.available() else "plain",
            "providers": providers, "roles": roles, "readiness": readiness(settings)}


def apply_update(settings, update: dict) -> dict:
    """面板的保存：providers 里 api_key 有值才更新，clear_key 清除；没有 id 且有 name 的是新增自定义厂商。"""
    raw = read_raw(settings) or {"providers": {}, "roles": {}}
    providers = raw.setdefault("providers", {})
    for p in update.get("providers") or []:
        pid = p.get("id")
        if not pid:
            name = " ".join(str(p.get("name", "")).split())[:40]
            if not name:
                raise ValueError("自定义厂商要有名字")
            pid = f"custom-{uuid.uuid4().hex[:8]}"
            providers[pid] = {"custom": True, "name": name, "base_url": validate_base_url(p.get("base_url", ""))}
        elif pid in BY_ID:
            entry = providers.setdefault(pid, {})
            if "base_url" in p:
                entry["base_url"] = validate_base_url(p["base_url"]) if p["base_url"] else None
        elif (providers.get(pid) or {}).get("custom"):
            entry = providers[pid]
            if p.get("name"):
                entry["name"] = " ".join(str(p["name"]).split())[:40]
            if p.get("base_url"):
                entry["base_url"] = validate_base_url(p["base_url"])
        else:
            raise ValueError(f"未知厂商：{pid}")
        entry = providers[pid]
        if p.get("clear_key"):
            entry.pop("api_key", None)
        elif p.get("api_key"):
            entry["api_key"] = secrets.seal(_validate_key(str(p["api_key"])))
    for pid in update.get("remove") or []:
        if (providers.get(pid) or {}).get("custom"):
            del providers[pid]
    roles = raw.setdefault("roles", {})
    for role, r in (update.get("roles") or {}).items():
        if role not in ROLES:
            raise ValueError(f"未知用途：{role}")
        pid = r.get("provider") or DEFAULT_PROVIDER
        if provider_info(raw, pid) is None:
            raise ValueError(f"{ROLE_NAMES[role]}：厂商 {pid} 不存在")
        roles[role] = {"provider": pid, "model": _validate_model(r.get("model", ""))}
    for role in ROLES:                  # 删掉的自定义厂商还被用着：退回默认厂商
        if role in roles and provider_info(raw, roles[role]["provider"]) is None:
            roles[role] = {"provider": DEFAULT_PROVIDER, "model": ""}
    raw["version"] = 1
    _write_raw(settings, raw)
    return public_view(settings)


def test_connection(settings, pid: str, model: str = "", api_key: str = "", base_url: str = "",
                    http_client=None) -> dict:
    """用面板里当前填的值（可以是还没保存的 key）试一次：拉模型列表 + 用给定模型做一次最小的 JSON 调用。
    结果用 success 表示连通与否（ok 是 native host 协议自己的字段，不能占用）。"""
    raw, _ = load(settings)
    info = provider_info(raw, pid)
    if info is None:
        raise ValueError(f"未知厂商：{pid}")
    key = _validate_key(api_key) if api_key else stored_key(raw, pid)
    if info["requires_key"] and not key:
        raise ValueError(f"{info['name']} 还没有 API key")
    spec = RoleSpec("test", pid, info["name"], info["api"], validate_base_url(base_url) if base_url else info["base_url"],
                    key, _validate_model(model), info["json_mode"], info["token_param"], info["temperature"],
                    (raw.get("providers", {}).get(pid) or {}).get("extra_body"))
    llm = build_llm(spec, timeout=45, max_retries=0, http_client=http_client)
    started = time.perf_counter()
    out = {"success": False, "models": [], "models_error": "", "error": "", "reply_ok": None}
    try:
        out["models"] = llm.list_models()[:300]
    except Exception as e:  # noqa: BLE001 —— 不少兼容接口没有 /models，不算失败
        out["models_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    if spec.model:
        try:
            data = llm.json("你在做连通性测试。只输出 json。", '请原样输出这个 json：{"ok": true}', max_tokens=1000,
                            temperature=0, attempts=1)
            out["reply_ok"] = data.get("ok") is True
            if not out["reply_ok"]:
                out["error"] = f"模型回复了，但内容不对：{json.dumps(data, ensure_ascii=False)[:120]}"
        except LLMError as e:
            out["error"] = str(e)[:300]
        out["success"] = bool(out["reply_ok"])
    else:
        out["success"] = bool(out["models"])
        if not out["success"]:
            out["error"] = out["models_error"] or "没有拉到模型列表"
    out["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return out
