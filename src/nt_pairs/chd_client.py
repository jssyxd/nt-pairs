"""CryptoHFTData 数据访问层（HTTP + parquet 落盘缓存 + 盘口重建）。

基址 `https://api.cryptohftdata.com/v1`：
* `GET /symbols?exchange=<ex>&data_type=<dt>`
* `GET /symbol-counts?...`
* `GET /status?lookback_hours=24`
* `GET /download?file={exchange}/{YYYY-MM-DD}/{HH}/{symbol}_{data_type}.parquet`（**必须跟随 302**）

依赖：`pandas`/`pyarrow` 只在真正读 parquet 时才 import，模块本身可被无网环境导入。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "CHD_BASE",
    "ChdClient",
    "BookPoint",
    "rebuild_book_series",
    "hour_range",
    "utcnow",
]

CHD_BASE = "https://api.cryptohftdata.com/v1"
_UA = {"User-Agent": "nt-pairs-chd/1.0"}


def _api_key_header() -> dict[str, str]:
    key = os.environ.get("CHD_API_KEY", "").strip()
    if not key:
        return {}
    return {"X-API-Key": key, "Authorization": f"Bearer {key}"}


@dataclass(frozen=True)
class BookPoint:
    """某一时刻的盘口快照（由增量行重建）。"""

    ts_ms: int
    bid: float
    ask: float
    mid: float
    bid_usd: float          # ±band 带内买盘名义
    ask_usd: float          # ±band 带内卖盘名义
    bid_qty: float
    ask_qty: float
    bid_levels: tuple[tuple[float, float], ...] = ()   # 买档（price, qty），价格降序
    ask_levels: tuple[tuple[float, float], ...] = ()   # 卖档（price, qty），价格升序


class ChdClient:
    """CryptoHFTData 的极简客户端（带本地缓存）。"""

    def __init__(self, base: str = CHD_BASE, cache_dir: str | Path = "data/chd_cache",
                 timeout: float = 180.0, retries: int = 2) -> None:
        self.base = base.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.retries = retries
        self._local = threading.local()

    # ---------------------------------------------------------------- HTTP

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self.base}{path}"
        if params:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        headers = {**_UA, **_api_key_header()}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                    return json.load(fh)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    json.JSONDecodeError) as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after {self.retries + 1} tries: {last}")

    def symbols(self, exchange: str, data_type: str = "orderbook") -> list[str]:
        d = self._get_json("/symbols", {"exchange": exchange, "data_type": data_type})
        return list(d.get("symbols") or [])

    def symbol_counts(self, exchange: str, data_type: str) -> dict[str, Any]:
        return self._get_json("/symbol-counts", {"exchange": exchange, "data_type": data_type})

    def status(self, lookback_hours: int = 24) -> Any:
        return self._get_json("/status", {"lookback_hours": str(lookback_hours)})

    # ------------------------------------------------------------ 下载缓存

    def _remote_path(self, exchange: str, day: str, hour: int, symbol: str,
                     data_type: str) -> str:
        sym = symbol.replace(":", "%3A")
        return f"{exchange}/{day}/{hour:02d}/{sym}_{data_type}.parquet"

    def download(self, exchange: str, day: str, hour: int, symbol: str,
                 data_type: str, *, force: bool = False) -> Path | None:
        """下载一个小时的数据文件到缓存目录；文件不存在返回 None。

        用 `requests`（连接池 + keep-alive）时吞吐高一个量级；没有 requests 时
        退回 stdlib urllib（功能相同，只是慢）。
        """
        rel = self._remote_path(exchange, day, hour, symbol, data_type)
        dest = (self.cache_dir / exchange / day / f"{hour:02d}"
                / f"{symbol.replace(':', '%3A')}_{data_type}.parquet")
        if dest.exists() and dest.stat().st_size > 0 and not force:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.base}/download?file={rel}"
        headers = {**_UA, **_api_key_header()}
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                body = self._fetch(url, headers)
                if not body.startswith(b"PAR1"):
                    raise ValueError(f"not a parquet file: {body[:80]!r}")
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(body)
                tmp.replace(dest)
                return dest
            except Exception as exc:  # noqa: BLE001 - 网络异常一律重试
                last = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                    return None
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"download {rel} failed: {last}")

    def _fetch(self, url: str, headers: dict[str, str]) -> bytes:
        """取一次响应体；`requests` 可用时走连接池（keep-alive）。"""
        session = getattr(self._local, "session", None)
        if session is None:
            try:
                import requests
                session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
            except ImportError:
                session = False
            self._local.session = session
        if session:
            r = session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
            if r.status_code == 404:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            r.raise_for_status()
            return r.content
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as fh:
            return fh.read()

    # -------------------------------------------------------------- 读盘

    @staticmethod
    def read_parquet(path: str | Path):
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise RuntimeError("需要 pandas/pyarrow：pip install pandas pyarrow") from exc
        return pd.read_parquet(path)


def rebuild_book_series(df, *, band_bps: float = 25.0, sample_ms: int = 1000,
                        top_levels: int = 10) -> list[BookPoint]:
    """把 orderbook 的增量行重建成**等间隔**的盘口快照序列。

    * `snapshot` 批次 → 重置两侧簿；`update` 批次 → 按价格增量更新（数量 0 = 删除）；
    * 每 `sample_ms` 输出一帧（用当时簿状态），因此缺行**不会被当成连续时间**：
      输出帧的时间戳就是数据的真实时间。

    ⚠️ 调用方必须用 `ts_ms` 做同刻对齐，不得跨瞬间取价。
    """
    if df is None or len(df) == 0:
        return []
    rows = df.sort_values(["received_time"], kind="stable")
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    out: list[BookPoint] = []
    last_sample = -10 ** 12
    cur_ts = 0
    pending: list[tuple[str, float, float]] = []

    def flush(ts: int) -> None:
        nonlocal last_sample
        for side, px, qty in pending:
            book = bids if side == "bid" else asks
            if qty <= 0:
                book.pop(px, None)
            else:
                book[px] = qty
        pending.clear()
        if ts - last_sample < sample_ms:
            return
        last_sample = ts
        if not bids or not asks:
            return
        bb = max(bids)
        ba = min(asks)
        mid = (bb + ba) / 2.0
        lo = mid * (1 - band_bps / 10000.0)
        hi = mid * (1 + band_bps / 10000.0)
        b_usd = b_qty = a_usd = a_qty = 0.0
        for px, qty in bids.items():
            if px < lo:
                continue
            b_usd += px * qty
            b_qty += qty
        for px, qty in asks.items():
            if px > hi:
                continue
            a_usd += px * qty
            a_qty += qty
        top_bids = tuple(sorted(bids.items(), key=lambda kv: -kv[0])[:top_levels])
        top_asks = tuple(sorted(asks.items(), key=lambda kv: kv[0])[:top_levels])
        out.append(BookPoint(ts, bb, ba, mid, b_usd, a_usd, b_qty, a_qty,
                             top_bids, top_asks))

    for received, event_time, event_type, side, price, qty in zip(
            rows["received_time"], rows["event_time"], rows["event_type"],
            rows["side"], rows["price"], rows["quantity"]):
        ts = int(event_time)
        if ts != cur_ts:
            flush(cur_ts)
            cur_ts = ts
            if event_type == "snapshot":
                bids.clear()
                asks.clear()
        try:
            px = float(price)
            q = float(qty)
        except (TypeError, ValueError):
            continue
        pending.append((str(side), px, q))
    flush(cur_ts)
    return out


def hour_range(now: datetime, hours: int) -> list[tuple[str, int]]:
    """返回最近 `hours` 个 (日期字符串, 小时) 组合，由旧到新。"""
    out: list[tuple[str, int]] = []
    cur = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
    for _ in range(hours):
        out.append((cur.strftime("%Y-%m-%d"), cur.hour))
        cur += timedelta(hours=1)
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
