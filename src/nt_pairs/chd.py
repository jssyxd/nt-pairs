"""CryptoHFTData (CHD) HTTP client: symbol discovery + hourly Parquet download with disk cache.

Pure `requests` dependency; no nautilus_trader import so it can be used offline.

Conventions of the upstream service (verified against https://www.cryptohftdata.com/llms.txt):
  * file object path  : {exchange}/{YYYY-MM-DD}/{HH}/{symbol}_{data_type}.parquet
  * download endpoint : GET {base}/download?file=<object path>  -> 302 to the object store
  * symbol discovery  : GET {base}/symbols?exchange=<ex>&data_type=<dt>
  * counts            : GET {base}/symbol-counts?exchange=<ex>&data_type=<dt>
  * ingestion health  : GET {base}/status?lookback_hours=<n>
  * bearer token      : POST {base}/jwt-token with header X-API-Key

Symbol strings must be percent-encoded (`:` -> `%3A`); `requests` does this when the
object path is passed as a query *parameter* value.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.parse import quote, urlencode

import requests

DEFAULT_BASE_URL = "https://api.cryptohftdata.com/v1"
DEFAULT_CACHE_DIR = "data/chd_cache"
DATA_TYPES = (
    "orderbook",
    "trades",
    "ticker",
    "mark_price",
    "open_interest",
    "liquidations",
)
FUTURES_EXCHANGES = (
    "hyperliquid_futures",
    "lighter",
    "okx_futures",
    "binance_futures",
    "bybit",
    "kraken_derivatives",
    "aster_futures",
    "bitget_futures",
)


class CHDError(RuntimeError):
    """Base error for CHD access."""


class CHDNotFound(CHDError):
    """Requested object does not exist (404)."""


class CHDRateLimited(CHDError):
    """Rate limit hit after exhausting retries."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    from_cache: bool
    bytes: int


def load_api_key(
    explicit: str | None = None,
    env: dict[str, str] | None = None,
    env_files: "Sequence[Path] | None" = None,
) -> str | None:
    """Resolve the API key: explicit argument > CHD_API_KEY env var > local .env file.

    The key is never written into the repository; `.env` is git-ignored and only
    `.env.example` is committed.
    """
    if explicit is not None:
        return explicit.strip() or None
    env = os.environ if env is None else env
    key = env.get("CHD_API_KEY")
    if key and key.strip():
        return key.strip()
    if env_files is None:
        env_files = (
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        )
    for candidate in env_files:
        try:
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() != "CHD_API_KEY":
                    continue
                value = value.strip().strip("'\"")
                if value:
                    return value
        except OSError:
            continue
    return None


def object_path(
    exchange: str,
    when: date | datetime | str,
    hour: int | str,
    symbol: str,
    data_type: str,
) -> str:
    """Build the upstream object path (unencoded)."""
    day = when if isinstance(when, str) else when.strftime("%Y-%m-%d")
    hh = f"{int(hour):02d}"
    return f"{exchange}/{day}/{hh}/{symbol}_{data_type}.parquet"


def download_url(base_url: str, path: str) -> str:
    """Full download URL with the object path percent-encoded as a query parameter."""
    return f"{base_url.rstrip('/')}/download?" + urlencode({"file": path})


def _as_datetime(value: date | datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.fromisoformat(value)


def iter_hours(
    start: date | datetime | str,
    end: date | datetime | str,
) -> Iterator[tuple[str, int]]:
    """Yield (YYYY-MM-DD, HH) for every hour in [start, end)."""
    s = _as_datetime(start)
    e = _as_datetime(end)
    cur = s.replace(minute=0, second=0, microsecond=0)
    while cur < e:
        yield cur.strftime("%Y-%m-%d"), cur.hour
        cur += timedelta(hours=1)


class CHDClient:
    """Read-only client for the CryptoHFTData market-data API.

    Parameters
    ----------
    api_key:
        Optional key. When omitted it is resolved from ``CHD_API_KEY`` / ``.env``.
        Anonymous access is allowed for the free tier (60 req/min per IP).
    cache_dir:
        Root of the local parquet cache. Objects are stored under
        ``<cache_dir>/<exchange>/<date>/<HH>/<symbol>_<data_type>.parquet``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        base_url: str = DEFAULT_BASE_URL,
        session: "requests.Session | None" = None,
        timeout: float = 60.0,
        max_retries: int = 5,
        sleep=time.sleep,
        user_agent: str = "nt-pairs-chd/0.1",
    ) -> None:
        self.api_key = load_api_key(api_key)
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self.user_agent = user_agent
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------ auth

    def bearer_token(self, force: bool = False) -> str | None:
        """Return a short-lived bearer token, or None when no API key is configured."""
        if not self.api_key:
            return None
        if not force and self._token and time.time() < self._token_expires_at - 30:
            return self._token
        resp = self.session.post(
            f"{self.base_url}/jwt-token",
            headers={"X-API-Key": self.api_key, "User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise CHDError(f"jwt-token failed: HTTP {resp.status_code} {resp.text[:200]}")
        payload = resp.json()
        token = payload.get("jwt_token") or payload.get("access_token")
        if not token:
            raise CHDError(f"jwt-token response without token: {str(payload)[:200]}")
        self._token = token
        self._token_expires_at = time.time() + float(payload.get("expires_in", 3600))
        return token

    def _auth_headers(self) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        token = self.bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # --------------------------------------------------------------- helpers

    def _handle_rate_limit(self, resp) -> None:
        """Sleep for Retry-After (or exponential backoff) and let the caller retry."""
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        delay = 2.0
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0
        else:
            remaining = resp.headers.get("X-RateLimit-Reset")
            if remaining:
                try:
                    delay = max(delay, float(remaining) - time.time())
                except ValueError:
                    pass
        self._sleep(delay)

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    url, params=params, headers=self._auth_headers(), timeout=self.timeout
                )
            except requests.RequestException as exc:  # network hiccup
                last_exc = exc
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                continue
            if resp.status_code >= 500:
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if resp.status_code >= 400:
                raise CHDError(f"GET {url} -> HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise CHDRateLimited(f"GET {url} failed after {self.max_retries} attempts ({last_exc})")

    # ------------------------------------------------------------- discovery

    def status(self, lookback_hours: int = 24) -> dict:
        """Ingestion health per exchange / data type."""
        return self._get_json("/status", {"lookback_hours": int(lookback_hours)})

    def symbols(self, exchange: str, data_type: str) -> list[str]:
        """Symbols stored for one exchange and one data family."""
        payload = self._get_json("/symbols", {"exchange": exchange, "data_type": data_type})
        return list(payload.get("symbols", []))

    def symbol_counts(self, exchange: str, data_type: str) -> dict:
        """Count of stored symbols for one exchange and one data family."""
        payload = self._get_json(
            "/symbol-counts", {"exchange": exchange, "data_type": data_type}
        )
        return dict(payload.get("counts", {}).get(exchange, {}))

    # -------------------------------------------------------------- download

    def cached_path(
        self, exchange: str, when: str, hour: int, symbol: str, data_type: str
    ) -> Path:
        """Local cache path of one object (may not exist yet)."""
        return (
            self.cache_dir
            / exchange
            / when
            / f"{int(hour):02d}"
            / f"{symbol}_{data_type}.parquet"
        )

    def download(
        self,
        exchange: str,
        when: date | datetime | str,
        hour: int,
        symbol: str,
        data_type: str,
        force: bool = False,
    ) -> Path:
        """Download one hourly parquet object and return its local path.

        Cached files are returned without touching the network unless ``force``.
        """
        return self.download_result(
            exchange, when, hour, symbol, data_type, force=force
        ).path

    def download_result(
        self,
        exchange: str,
        when: date | datetime | str,
        hour: int,
        symbol: str,
        data_type: str,
        force: bool = False,
    ) -> DownloadResult:
        day = when if isinstance(when, str) else when.strftime("%Y-%m-%d")
        target = self.cached_path(exchange, day, hour, symbol, data_type)
        if target.is_file() and target.stat().st_size > 0 and not force:
            return DownloadResult(path=target, from_cache=True, bytes=target.stat().st_size)

        path = object_path(exchange, day, hour, symbol, data_type)
        url = download_url(self.base_url, path)
        payload = self._fetch_bytes(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".parquet.part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        return DownloadResult(path=target, from_cache=False, bytes=len(payload))

    def object_exists(
        self, exchange: str, when: str, hour: int, symbol: str, data_type: str
    ) -> bool:
        """True when the object exists upstream (probe only, body is not downloaded)."""
        path = object_path(exchange, when, hour, symbol, data_type)
        current = download_url(self.base_url, path)
        hops = 0
        while True:
            resp = self.session.get(
                current,
                headers=self._auth_headers(),
                timeout=self.timeout,
                stream=True,
            )
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location") or resp.headers.get("location")
                    if not location:
                        return False
                    hops += 1
                    if hops > 5:
                        return False
                    current = requests.compat.urljoin(current, location)
                    continue
                if resp.status_code == 429:
                    self._handle_rate_limit(resp)
                    continue
                return resp.status_code == 200
            finally:
                close = getattr(resp, "close", None)
                if callable(close):
                    close()

    def _fetch_bytes(self, url: str) -> bytes:
        """GET with redirect following and 429 backoff."""
        hops = 0
        current = url
        while True:
            resp = self.session.get(
                current, headers=self._auth_headers(), timeout=self.timeout
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location") or resp.headers.get("location")
                if not location:
                    raise CHDError(f"redirect without Location header from {current}")
                hops += 1
                if hops > 5:
                    raise CHDError(f"too many redirects for {url}")
                current = requests.compat.urljoin(current, location)
                continue
            if resp.status_code == 429:
                self._handle_rate_limit(resp)
                continue
            if resp.status_code == 404:
                raise CHDNotFound(f"object not found: {url}")
            if resp.status_code >= 400:
                raise CHDError(f"GET {current} -> HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.content

    def iter_files(
        self,
        exchange: str,
        symbols: Sequence[str],
        data_type: str,
        start: date | datetime,
        end: date | datetime,
        force: bool = False,
        skip_missing: bool = True,
    ) -> Iterator[Path]:
        """Yield local paths for every (symbol, hour) in the window.

        Hours are emitted in chronological order per symbol so that a caller can
        stream a full window without holding it in memory.
        """
        hours = list(iter_hours(start, end))
        for symbol in symbols:
            for day, hour in hours:
                try:
                    yield self.download(
                        exchange, day, hour, symbol, data_type, force=force
                    )
                except CHDNotFound:
                    if not skip_missing:
                        raise

    # ------------------------------------------------------------ utilities

    def cached_objects(self) -> list[Path]:
        """All parquet objects currently in the local cache."""
        if not self.cache_dir.is_dir():
            return []
        return sorted(self.cache_dir.rglob("*.parquet"))

    def write_cache_index(self, path: str | Path) -> Path:
        """Dump the cache inventory to JSON (useful for the data survey)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        items = []
        for p in self.cached_objects():
            rel = p.relative_to(self.cache_dir).as_posix()
            parts = rel.split("/")
            items.append(
                {
                    "object": rel,
                    "exchange": parts[0],
                    "date": parts[1],
                    "hour": parts[2],
                    "file": parts[3],
                    "bytes": p.stat().st_size,
                }
            )
        out.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return out


def encode_symbol(symbol: str) -> str:
    """Percent-encode a venue symbol for use inside an object path."""
    return quote(symbol, safe="")


def iter_symbol_exchange_pairs(
    exchange: str, symbols: Iterable[str]
) -> Iterator[tuple[str, str]]:
    """Convenience generator: (exchange, symbol) pairs."""
    for symbol in symbols:
        yield exchange, symbol
