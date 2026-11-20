"""Offline tests for the CryptoHFTData client (no real network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nt_pairs.chd import (
    CHDClient,
    CHDNotFound,
    download_url,
    iter_hours,
    load_api_key,
    object_path,
)


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None, json_body=None, text=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._json = json_body
        self.text = text or (content[:200].decode("utf-8", "replace") if content else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Records calls and replays a scripted list of responses."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.slept = []

    def _next(self, kind, url, kwargs):
        self.calls.append((kind, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected {kind} call to {url}")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)


def make_client(tmp_path: Path, responses, **kwargs):
    session = FakeSession(responses)
    client = CHDClient(
        api_key="",
        cache_dir=tmp_path / "cache",
        session=session,
        sleep=lambda d: session.slept.append(d),
        **kwargs,
    )
    return client, session


# --------------------------------------------------------------------- URLs


def test_object_path_layout():
    assert (
        object_path("hyperliquid_futures", "2026-11-13", 14, "IO:NVDA", "orderbook")
        == "hyperliquid_futures/2026-11-13/14/IO:NVDA_orderbook.parquet"
    )
    assert (
        object_path("okx_futures", "2026-11-13", 9, "NVDA-USDT-SWAP", "trades")
        == "okx_futures/2026-11-13/09/NVDA-USDT-SWAP_trades.parquet"
    )


def test_download_url_percent_encodes_colon():
    url = download_url(
        "https://api.cryptohftdata.com/v1",
        object_path("hyperliquid_futures", "2026-11-13", 14, "IO:NVDA", "orderbook"),
    )
    assert "IO%3ANVDA" in url
    assert ":" not in url.split("?file=")[1]
    assert url.startswith("https://api.cryptohftdata.com/v1/download?")


def test_iter_hours_spans_days():
    hours = list(iter_hours("2026-11-13", "2026-11-14"))
    assert hours[0] == ("2026-11-13", 0)
    assert hours[-1] == ("2026-11-13", 23)
    assert len(hours) == 24
    assert list(iter_hours("2026-11-13", "2026-11-13")) == []


# ------------------------------------------------------------------- cache


def test_download_writes_cache_and_second_call_does_not_hit_network(tmp_path):
    payload = b"PAR1-fake"
    client, session = make_client(
        tmp_path, [FakeResponse(200, content=payload)]
    )
    path = client.download("hyperliquid_futures", "2026-11-13", 14, "IO:NVDA", "orderbook")
    assert path.is_file()
    assert path.read_bytes() == payload
    assert len(session.calls) == 1

    result = client.download_result(
        "hyperliquid_futures", "2026-11-13", 14, "IO:NVDA", "orderbook"
    )
    assert result.from_cache is True
    assert len(session.calls) == 1  # no second network call


def test_force_download_bypasses_cache(tmp_path):
    client, session = make_client(
        tmp_path, [FakeResponse(200, content=b"a"), FakeResponse(200, content=b"b")]
    )
    client.download("lighter", "2026-11-13", 10, "NVDA", "trades")
    client.download("lighter", "2026-11-13", 10, "NVDA", "trades", force=True)
    assert len(session.calls) == 2
    assert (
        client.cached_path("lighter", "2026-11-13", 10, "NVDA", "trades").read_bytes() == b"b"
    )


def test_cache_index_lists_objects(tmp_path):
    client, _ = make_client(tmp_path, [FakeResponse(200, content=b"x")])
    client.download("lighter", "2026-11-13", 10, "NVDA", "trades")
    index = client.write_cache_index(tmp_path / "index.json")
    items = json.loads(index.read_text())
    assert items[0]["object"] == "lighter/2026-11-13/10/NVDA_trades.parquet"
    assert items[0]["exchange"] == "lighter"


# --------------------------------------------------------------- redirects


def test_download_follows_302_redirect(tmp_path):
    signed = "https://objects.example.com/signed/NVDA.parquet?sig=abc"
    client, session = make_client(
        tmp_path,
        [
            FakeResponse(302, headers={"Location": signed}),
            FakeResponse(200, content=b"redirected-body"),
        ],
    )
    path = client.download("hyperliquid_futures", "2026-11-13", 14, "IO:NVDA", "orderbook")
    assert path.read_bytes() == b"redirected-body"
    assert len(session.calls) == 2
    assert session.calls[1][1] == signed


def test_download_404_raises_not_found(tmp_path):
    client, _ = make_client(tmp_path, [FakeResponse(404)])
    with pytest.raises(CHDNotFound):
        client.download("lighter", "2026-11-13", 10, "NOPE", "trades")


# -------------------------------------------------------------- rate limit


def test_429_retries_after_retry_after_header(tmp_path):
    client, session = make_client(
        tmp_path,
        [FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, content=b"ok")],
    )
    path = client.download("lighter", "2026-11-13", 10, "NVDA", "trades")
    assert path.read_bytes() == b"ok"
    assert session.slept == [3.0]
    assert len(session.calls) == 2


def test_429_json_endpoint_also_backs_off(tmp_path):
    client, session = make_client(
        tmp_path,
        [
            FakeResponse(429, headers={"Retry-After": "2"}),
            FakeResponse(200, json_body={"exchange": "lighter", "data_type": "trades", "symbols": ["NVDA"]}),
        ],
    )
    assert client.symbols("lighter", "trades") == ["NVDA"]
    assert session.slept == [2.0]


# --------------------------------------------------------------- discovery


def test_symbols_and_counts_pass_query_params(tmp_path):
    client, session = make_client(
        tmp_path,
        [
            FakeResponse(200, json_body={"exchange": "lighter", "data_type": "trades", "symbols": ["A", "B"]}),
            FakeResponse(200, json_body={"counts": {"lighter": {"trades": 2}}}),
        ],
    )
    assert client.symbols("lighter", "trades") == ["A", "B"]
    params = session.calls[0][2]["params"]
    assert params == {"exchange": "lighter", "data_type": "trades"}
    assert client.symbol_counts("lighter", "trades") == {"trades": 2}


def test_status_passes_lookback(tmp_path):
    client, session = make_client(tmp_path, [FakeResponse(200, json_body={"exchanges": []})])
    client.status(lookback_hours=6)
    assert session.calls[0][2]["params"] == {"lookback_hours": 6}


def test_iter_files_skips_missing_objects(tmp_path):
    client, session = make_client(
        tmp_path,
        [
            FakeResponse(404),
            FakeResponse(200, content=b"a"),
        ],
    )
    files = list(
        client.iter_files(
            "lighter", ["NVDA"], "trades", "2026-11-13", "2026-11-13 02:00"
        )
    )
    assert len(files) == 1
    assert files[0].name == "NVDA_trades.parquet"


# ------------------------------------------------------------------ api key


def test_api_key_prefers_explicit_then_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHD_API_KEY", "env-key")
    assert load_api_key("explicit-key") == "explicit-key"
    assert load_api_key() == "env-key"


def test_api_key_reads_local_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CHD_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nCHD_API_KEY='file-key'\nOTHER=1\n")
    assert load_api_key(env_files=[env_file]) == "file-key"


def test_api_key_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("CHD_API_KEY", raising=False)
    assert load_api_key(env_files=[]) is None


def test_bearer_token_used_as_authorization_header(tmp_path):
    client, session = make_client(
        tmp_path,
        [
            FakeResponse(200, json_body={"jwt_token": "tok", "expires_in": 3600}),
            FakeResponse(200, json_body={"symbols": []}),
        ],
    )
    client.api_key = "key"
    client.symbols("lighter", "trades")
    headers = session.calls[1][2]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert session.calls[0][0] == "POST"

def test_object_exists_probes_without_downloading(tmp_path):
    client, session = make_client(
        tmp_path, [FakeResponse(404), FakeResponse(200, content=b"body")]
    )
    assert client.object_exists("lighter", "2026-11-13", 10, "NVDA", "trades") is False
    assert client.object_exists("lighter", "2026-11-13", 10, "NVDA", "trades") is True
    assert len(session.calls) == 2


def test_object_exists_follows_redirect(tmp_path):
    signed = "https://objects.example.com/signed/NVDA.parquet"
    client, session = make_client(
        tmp_path,
        [FakeResponse(302, headers={"Location": signed}), FakeResponse(200, content=b"x")],
    )
    assert client.object_exists("lighter", "2026-11-13", 10, "NVDA", "trades") is True
    assert session.calls[1][1] == signed


def test_empty_api_key_means_anonymous(tmp_path):
    client, _ = make_client(tmp_path, [])
    assert client.api_key is None
    assert client.bearer_token() is None
