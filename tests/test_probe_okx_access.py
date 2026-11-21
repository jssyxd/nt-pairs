"""单测：OKX 访问探测脚本（`scripts/probe_okx_access.py`）。

重点：矩阵渲染、推荐口径、以及 `--offline` **绝不联网**。

Run: python -m pytest tests/test_probe_okx_access.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import probe_okx_access as po  # noqa: E402


def _probe(method: str, host: str, path: str, ok: bool, status: str = "200") -> po.Probe:
    return po.Probe(method=method, url=f"https://{host}{path}", host=host, path=path,
                    status=status, ok=ok, elapsed_s=0.5)


def test_matrix_covers_every_combination():
    probes = po.run_matrix(offline=True)
    assert len(probes) == len(po.METHODS) * len(po.HOSTS) * len(po.PATHS)
    assert {p.method for p in probes} == set(po.METHODS)
    assert {p.host for p in probes} == set(po.HOSTS)


def test_offline_never_touches_network(monkeypatch):
    def boom(*_a, **_k):  # pragma: no cover - 只应在被调用时失败
        raise AssertionError("offline 模式不应发起网络请求")

    monkeypatch.setattr(po.subprocess, "run", boom)
    monkeypatch.setattr(po.urllib.request, "build_opener", boom)
    probes = po.run_matrix(offline=True)
    assert all(p.status == "" for p in probes)
    summary = po.summarize(probes)
    assert "离线" in summary["recommendation"]


def test_recommendation_prefers_direct_when_it_works():
    probes = []
    for method in po.METHODS:
        for host in po.HOSTS:
            for path, _q in po.PATHS:
                ok = (host == "www.okx.com" and method in ("curl", "urllib+UA", "urllib+UA+proxy"))
                probes.append(_probe(method, host, path, ok, "200" if ok else "000"))
    summary = po.summarize(probes)
    assert "urllib+UA" in summary["recommendation"]
    assert "7897" in summary["recommendation"]
    assert "aws.okx.com" in summary["recommendation"]


def test_recommendation_falls_back_to_proxy():
    probes = []
    for method in po.METHODS:
        for host in po.HOSTS:
            for path, _q in po.PATHS:
                ok = (method == "urllib+UA+proxy" and host == "www.okx.com")
                probes.append(_probe(method, host, path, ok, "200" if ok else "000"))
    summary = po.summarize(probes)
    assert "urllib+UA+proxy" in summary["recommendation"]


def test_render_markdown_contains_matrix_rows():
    probes = [_probe("curl", "www.okx.com", "/api/v5/public/time", True)]
    summary = po.summarize(probes)
    md = po.render_markdown(probes, summary, {"generated_at": "2026-01-01T00:00:00Z"})
    assert "www.okx.com" in md
    assert "/api/v5/public/time" in md
    assert "39133" in md          # 提醒：39133 是控制端口


def test_main_offline_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(po.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("offline 不应调用 curl")))
    out = tmp_path / "okx_access.md"
    js = tmp_path / "okx_access.json"
    rc = po.main(["--offline", "--out", str(out), "--json", str(js)])
    assert rc == 0
    assert out.exists() and js.exists()
    payload = json.loads(js.read_text())
    assert payload["meta"]["offline"] is True
    assert len(payload["probes"]) == len(po.METHODS) * len(po.HOSTS) * len(po.PATHS)
