"""从 HF safetensors 元数据估算权重显存（只算权重，不含 KV cache 和激活）。"""

# 这些 dtype 的元素数就是参数数；整数类型（U8 / I8 / I32 …）常是量化后打包存的，元素数不等于参数数
FLOAT_DTYPES = {"BF16", "FP16", "F16", "F32", "F64", "F8_E4M3", "F8_E5M2"}
CARDS = (16, 24)        # 常见单卡显存（GB）
HEADROOM = 0.9          # 权重最多占显存的 90%，给 KV cache 和激活留余量


def weight_gb(params: int, bytes_per_param: float) -> float:
    return params * bytes_per_param / 1e9


def estimate(safetensors: dict) -> dict | None:
    """safetensors = HF API 的 {"parameters": {"BF16": n, ...}, "total": n}。"""
    total = (safetensors or {}).get("total") or sum((safetensors or {}).get("parameters", {}).values())
    if not total:
        return None
    dtypes = (safetensors or {}).get("parameters") or {}
    stored = max(dtypes, key=dtypes.get) if dtypes else None
    return {
        "params": total,
        "stored_dtype": stored,
        "gb": {"BF16": weight_gb(total, 2), "INT8": weight_gb(total, 1), "INT4": weight_gb(total, 0.5)},
    }


def fits(gb: float, card: int) -> bool:
    return gb <= card * HEADROOM


def _params(n: int) -> str:
    return f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.0f}M"


def describe(est: dict | None, context: int | None = None, license_: str | None = None) -> str:
    """例：参数 7.6B｜权重显存 BF16 15.2 GB / INT8 7.6 GB / INT4 3.8 GB（不含 KV cache）｜16 GB 卡：INT8 权重放得下；24 GB 卡：BF16 权重放得下"""
    if not est:
        return ""
    stored = est.get("stored_dtype")
    if stored and stored not in FLOAT_DTYPES:
        parts = [f"权重以 {stored} 存储（多半是量化后打包），按元素数算不出真实参数量和显存，请看模型卡"]
    else:
        gb = est["gb"]
        parts = [f"参数 {_params(est['params'])}",
                 "权重显存 " + " / ".join(f"{k} {v:.1f} GB" for k, v in gb.items()) + "（不含 KV cache 和激活）"]
        verdicts = []
        for card in CARDS:
            best = next((k for k, v in gb.items() if fits(v, card)), None)
            verdicts.append(f"{card} GB 卡：{best + ' 权重放得下' if best else '都放不下'}")
        parts.append("；".join(verdicts))
        if stored and stored not in ("BF16", "FP16", "F16"):
            parts.append(f"官方权重为 {stored}")
    if context:
        parts.append(f"上下文 {context:,}")
    if license_:
        parts.append(f"许可证 {license_}")
    return "｜".join(parts)
