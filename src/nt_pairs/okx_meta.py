"""OKX 合约元数据（`ctVal` / `tickSz` / `lotSz`）与本地缓存。

OKX 永续的盘口与成交数量单位是**张**（contract），1 张 = `ctVal` 个标的单位。
仓库里所有腿的深度 / 滑点 / 排队量都按**标的单位**（股）算，所以 OKX 腿必须乘
`ctVal` 才能和 Lighter / Hyperliquid 同口径比较（否则 OKX 深度会被高估 ctVal 倍）。

本模块只做三件事：
1. 拉 `https://www.okx.com/api/v5/public/instruments?instType=SWAP`；
2. 缓存到 `data/okx_instruments.json`（带 TTL，过期自动重取；`offline=True` 时只用缓存）；
3. 提供 `contract_multiplier(venue, symbol, instruments)`：把「张」换算成标的单位。

零第三方依赖（只用 stdlib `urllib`），网络不可用时不会抛异常，只会在 note 里说明。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "OKX_INSTRUMENTS_URL",
    "OkxInstrument",
    "parse_instruments",
    "fetch_instruments",
    "load_instruments",
    "contract_multiplier",
    "save_instruments",
]

OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DEFAULT_CACHE = "data/okx_instruments.json"
DEFAULT_TTL_S = 24 * 3600.0


@dataclass(frozen=True)
class OkxInstrument:
    """一个 OKX 永续合约的静态元数据。"""

    inst_id: str
    ct_val: Decimal          # 1 张 = 多少个标的单位
    ct_val_ccy: str = ""
    tick_size: Decimal = Decimal("0")
    lot_size: Decimal = Decimal("0")
    state: str = ""

    @property
    def live(self) -> bool:
        return self.state in ("", "live")


def _dec(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def parse_instruments(payload: Mapping[str, Any]) -> dict[str, OkxInstrument]:
    """把 OKX `instruments` 响应解析成 `{instId: OkxInstrument}`。

    只保留 `instType=SWAP` 且 `ctVal > 0` 的合约（`ctVal` 缺失的合约无法换算单位，
    调用方应视为「未知」而不是当成 1）。
    """
    out: dict[str, OkxInstrument] = {}
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("instType") not in (None, "SWAP"):
            continue
        inst_id = str(row.get("instId") or "")
        if not inst_id:
            continue
        ct_val = _dec(row.get("ctVal"))
        if ct_val <= 0:
            continue
        out[inst_id] = OkxInstrument(
            inst_id=inst_id,
            ct_val=ct_val,
            ct_val_ccy=str(row.get("ctValCcy") or ""),
            tick_size=_dec(row.get("tickSz")),
            lot_size=_dec(row.get("lotSz")),
            state=str(row.get("state") or ""),
        )
    return out


def _open(url: str, timeout: float, proxy: str | None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    return opener.open(req, timeout=timeout)


def fetch_instruments(*, timeout: float = 30.0, proxy: str | None = None,
                      url: str = OKX_INSTRUMENTS_URL) -> dict[str, OkxInstrument]:
    """拉一次 OKX 合约清单。失败抛异常（调用方决定是否退回缓存）。"""
    with _open(url, timeout, proxy) as fh:
        payload = json.loads(fh.read().decode("utf-8"))
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"OKX instruments error: {payload.get('code')} {payload.get('msg')}")
    return parse_instruments(payload)


def save_instruments(instruments: Mapping[str, OkxInstrument], path: str | Path,
                     *, source: str = OKX_INSTRUMENTS_URL) -> Path:
    """写缓存（原子替换）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.time(),
        "source": source,
        "instruments": {
            k: {"ctVal": str(v.ct_val), "ctValCcy": v.ct_val_ccy,
                "tickSz": str(v.tick_size), "lotSz": str(v.lot_size), "state": v.state}
            for k, v in instruments.items()
        },
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def _read_cache(path: str | Path) -> tuple[dict[str, OkxInstrument], float] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("instruments") or {}
    out: dict[str, OkxInstrument] = {}
    for inst_id, row in rows.items():
        if not isinstance(row, Mapping):
            continue
        ct_val = _dec(row.get("ctVal"))
        if ct_val <= 0:
            continue
        out[str(inst_id)] = OkxInstrument(
            inst_id=str(inst_id), ct_val=ct_val,
            ct_val_ccy=str(row.get("ctValCcy") or ""),
            tick_size=_dec(row.get("tickSz")), lot_size=_dec(row.get("lotSz")),
            state=str(row.get("state") or ""),
        )
    return out, float(payload.get("fetched_at") or 0.0)


def load_instruments(*, cache_path: str | Path = DEFAULT_CACHE,
                     ttl_s: float = DEFAULT_TTL_S,
                     offline: bool = False,
                     timeout: float = 30.0,
                     proxy: str | None = None,
                     now: float | None = None) -> tuple[dict[str, OkxInstrument], str]:
    """取合约清单：缓存新鲜就用缓存，否则联网重取；`offline=True` 时只用缓存。

    返回 `(instruments, note)`；note 说明数据来源，便于在报告里标注口径。
    """
    now = time.time() if now is None else now
    cached = _read_cache(cache_path)
    if cached is not None:
        instruments, fetched_at = cached
        age = now - fetched_at
        if age <= ttl_s:
            return instruments, f"缓存命中（{Path(cache_path)}，{age / 3600:.1f}h 前）"
        if offline:
            return instruments, f"缓存已过期（{age / 3600:.1f}h）但 offline=True，直接使用"
    if offline:
        if cached is not None:
            return cached[0], f"offline=True 且无新鲜缓存，使用过期缓存（{Path(cache_path)}）"
        return {}, "offline=True 且无缓存 → 无 ctVal 元数据（按 1 张=1 单位处理）"
    try:
        instruments = fetch_instruments(timeout=timeout, proxy=proxy)
    except (urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        if cached is not None:
            return cached[0], f"拉取失败（{exc}）→ 退回缓存（{Path(cache_path)}）"
        return {}, f"拉取失败（{exc}）且无缓存 → 无 ctVal 元数据（按 1 张=1 单位处理）"
    try:
        save_instruments(instruments, cache_path)
    except OSError:
        pass
    return instruments, f"在线拉取（{len(instruments)} 个 SWAP）"


def contract_multiplier(venue: str, symbol: str,
                        instruments: Mapping[str, OkxInstrument] | None) -> Decimal:
    """把「张」换算成**标的单位**的乘数。

    非 OKX 场所（以及查不到的符号）返回 1 —— 调用方应保证这确实是标的单位。
    OKX 查不到符号时同样返回 1，但这是**假设**，需要单独标注（否则深度会被高估）。
    """
    if venue != "okx_futures" or not instruments:
        return Decimal("1")
    inst = instruments.get(symbol)
    if inst is None:
        return Decimal("1")
    return inst.ct_val


def proxy_from_env() -> str | None:
    """从环境变量取 HTTP 代理（`HTTPS_PROXY` 优先）。"""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None
