#!/usr/bin/env python3
"""OKX 访问可达性矩阵（直连 / UA / 代理 × 域名 × 端点）。

回答一个问题：**这台机器怎么访问 OKX 公共 API？**
（历史记录里 `curl` 失败但 `urllib` + `User-Agent` 成功 —— 结论曾被「只测 curl」误判过，
所以这里把三种客户端 × 三个域名 × 三个端点全测一遍，并留下可复现的证据。）

用法：
    python scripts/probe_okx_access.py                 # 跑完整矩阵，写 docs/OKX_ACCESS.md
    python scripts/probe_okx_access.py --offline       # 不联网：只渲染（用已有结果或空表）
    python scripts/probe_okx_access.py --proxy http://127.0.0.1:7897
    python scripts/probe_okx_access.py --json data/okx_access.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

HOSTS = ("www.okx.com", "aws.okx.com", "static.okx.com")
PATHS = (
    ("/api/v5/public/time", ""),
    ("/api/v5/market/books", "?instId=SOXL-USDT-SWAP&sz=5"),
    ("/api/v5/market/history-candles", "?instId=SOXL-USDT-SWAP&bar=1m&limit=5"),
)
METHODS = ("curl", "urllib+UA", "urllib+UA+proxy")

DEFAULT_PROXY = "http://127.0.0.1:7897"


@dataclass
class Probe:
    method: str
    url: str
    host: str = ""
    path: str = ""
    status: str = ""          # HTTP 状态码（失败时为空）
    ok: bool = False
    elapsed_s: float = 0.0
    error: str = ""
    body_snippet: str = ""

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _snippet(body: bytes, n: int = 120) -> str:
    try:
        return body.decode("utf-8", "replace")[:n].replace("\n", " ")
    except Exception:  # noqa: BLE001
        return ""


def probe_curl(url: str, timeout: float, proxy: str | None) -> Probe:
    cmd = ["curl", "-sS", "-o", "-", "-w", "\n__CODE__%{http_code}__%{time_total}",
           "--max-time", str(timeout), "-A", UA]
    if proxy:
        cmd += ["-x", proxy]
    cmd.append(url)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    except FileNotFoundError:
        return Probe(method="curl", url=url, error="curl 未安装")
    except subprocess.TimeoutExpired:
        return Probe(method="curl", url=url, elapsed_s=time.time() - t0, error="curl 超时")
    elapsed = time.time() - t0
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace").strip()
    code = ""
    if "__CODE__" in out:
        head, _, tail = out.rpartition("__CODE__")
        body = head.rsplit("\n", 1)[0] if "\n" in head else ""
        parts = tail.split("__")
        code = parts[0]
        try:
            elapsed = float(parts[1]) if len(parts) > 1 and parts[1] else elapsed
        except ValueError:
            pass
        return Probe(method="curl", url=url, status=code, ok=code.startswith("2"),
                     elapsed_s=round(elapsed, 3), error=err,
                     body_snippet=_snippet(body.encode()))
    return Probe(method="curl", url=url, status=code, ok=False, elapsed_s=round(elapsed, 3),
                 error=err or "无响应", body_snippet=_snippet(proc.stdout))


def probe_urllib(url: str, timeout: float, proxy: str | None) -> Probe:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as fh:
            body = fh.read()
            code = str(fh.status)
        return Probe(method="urllib", url=url, status=code, ok=code.startswith("2"),
                     elapsed_s=round(time.time() - t0, 3),
                     body_snippet=_snippet(body))
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return Probe(method="urllib", url=url, status=str(exc.code), ok=False,
                     elapsed_s=round(time.time() - t0, 3), error=f"HTTP {exc.code}",
                     body_snippet=_snippet(body))
    except Exception as exc:  # noqa: BLE001
        return Probe(method="urllib", url=url, status="", ok=False,
                     elapsed_s=round(time.time() - t0, 3), error=repr(exc))


def run_matrix(*, timeout: float = 10.0, attempts: int = 2, proxy: str | None = None,
               offline: bool = False) -> list[Probe]:
    """跑完整矩阵；`offline=True` 时不发起任何网络请求。"""
    probes: list[Probe] = []
    for method in METHODS:
        for host in HOSTS:
            for path, query in PATHS:
                url = f"https://{host}{path}{query}"
                if offline:
                    probes.append(Probe(method=method, url=url, host=host, path=path,
                                        status="", error="offline：未发起请求"))
                    continue
                best: Probe | None = None
                for _ in range(max(1, attempts)):
                    if method == "curl":
                        p = probe_curl(url, timeout, None)
                    elif method == "urllib+UA":
                        p = probe_urllib(url, timeout, None)
                    else:
                        p = probe_urllib(url, timeout, proxy or DEFAULT_PROXY)
                    p.method = method
                    p.host = host
                    p.path = path
                    best = p
                    if p.ok:
                        break
                    time.sleep(0.5)
                probes.append(best)
    return probes


def summarize(probes: Sequence[Probe]) -> dict[str, Any]:
    """按方法/域名统计成功率，并给出推荐访问方式。"""
    by_method: dict[str, dict[str, Any]] = {}
    for m in METHODS:
        rows = [p for p in probes if p.method == m]
        ok = sum(1 for p in rows if p.ok)
        lat = [p.elapsed_s for p in rows if p.ok]
        by_method[m] = {"ok": ok, "total": len(rows),
                        "median_s": round(sorted(lat)[len(lat) // 2], 3) if lat else None}
    by_host: dict[str, dict[str, Any]] = {}
    for h in HOSTS:
        rows = [p for p in probes if p.host == h]
        by_host[h] = {"ok": sum(1 for p in rows if p.ok), "total": len(rows),
                      "reachable": sum(1 for p in rows if p.status not in ("", "000")),
                      "codes": sorted({p.status for p in rows if p.status})}
    best = max(METHODS, key=lambda m: (by_method[m]["ok"], -(by_method[m]["median_s"] or 1e9)))
    attempted = any(p.status or p.error != "offline：未发起请求" for p in probes)
    ok_any = any(p.ok for p in probes)
    if not attempted:
        recommendation = "离线模式（--offline）：本次未发起任何网络请求，无可用结论。"
    elif not ok_any:
        recommendation = ("全部失败：本次没有任何方式能连通 OKX —— "
                          "换出口（clash 规则/代理节点）后重跑。")
    elif by_method[best]["ok"] == 0:
        recommendation = "全部失败。"
    else:
        hosts_by_method = {m: sorted({p.host for p in probes if p.method == m and p.ok})
                           for m in METHODS}
        direct = [m for m in ("urllib+UA", "curl") if by_method[m]["ok"] > 0]
        proxied = by_method["urllib+UA+proxy"]["ok"] > 0
        parts: list[str] = []
        if direct:
            primary = "urllib+UA" if "urllib+UA" in direct else "curl"
            parts.append(f"**{primary}** 直连 `{hosts_by_method[primary][0]}` 可用")
            if proxied:
                parts.append(f"（{DEFAULT_PROXY} 代理同样可用，作为**直连抖动时的兜底**："
                             f"`export HTTPS_PROXY={DEFAULT_PROXY} HTTP_PROXY={DEFAULT_PROXY}`）")
        else:
            parts.append(f"**urllib+UA+proxy**（代理 {DEFAULT_PROXY}）可用")
        dead = [h for h in HOSTS if by_host[h]["reachable"] == 0]
        if dead:
            parts.append(f"；`{'`、`'.join(dead)}` 连不上（TLS EOF / 超时），不要用")
        noapi = [h for h in HOSTS if by_host[h]["reachable"] > 0 and by_host[h]["ok"] == 0]
        if noapi:
            parts.append(f"；`{'`、`'.join(noapi)}` 能连但不托管 `/api/v5`"
                         f"（返回 {by_host[noapi[0]]['codes']}），别当 API 入口")
        recommendation = "推荐：" + "".join(parts)
    return {"by_method": by_method, "by_host": by_host,
            "recommendation": recommendation}


def render_markdown(probes: Sequence[Probe], summary: dict[str, Any],
                    meta: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# OKX 访问可达性矩阵\n")
    L.append(f"生成时间：{meta.get('generated_at')}　"
             f"代理：{meta.get('proxy') or '（未用代理）'}　"
             f"超时：{meta.get('timeout')}s　重试：{meta.get('attempts')}　"
             f"离线：{'是' if meta.get('offline') else '否'}\n")
    L.append("## 结论\n")
    L.append(f"{summary['recommendation']}\n")
    L.append("### 按方法汇总\n")
    L.append("| 方法 | 成功/总数 | 成功请求中位耗时 |")
    L.append("|---|---:|---:|")
    for m in METHODS:
        s = summary["by_method"][m]
        med = f"{s['median_s']:.3f}s" if s["median_s"] is not None else "—"
        L.append(f"| {m} | {s['ok']}/{s['total']} | {med} |")
    L.append("")
    L.append("### 按域名汇总（跨方法）\n")
    L.append("| 域名 | 成功/总数 |")
    L.append("|---|---:|")
    for h in HOSTS:
        s = summary["by_host"][h]
        L.append(f"| {h} | {s['ok']}/{s['total']} |")
    L.append("")
    L.append("## 完整矩阵\n")
    L.append("| 方法 | 域名 | 端点 | HTTP | 耗时(s) | 错误 |")
    L.append("|---|---|---|---:|---:|---|")
    for p in probes:
        err = (p.error or "").replace("|", "\\|")[:80]
        L.append(f"| {p.method} | {p.host} | {p.path} | {p.status or '—'} | "
                 f"{p.elapsed_s:.3f} | {err or '—'} |")
    L.append("")
    L.append("## 口径说明\n")
    L.append("- 三个端点：`/api/v5/public/time`（连通性）、`/api/v5/market/books`（盘口）、"
             "`/api/v5/market/history-candles`（K 线），后两个带 `instId=SOXL-USDT-SWAP`。")
    L.append("- `curl` 与 `urllib` 都带 `User-Agent: Mozilla/5.0`；区别只在 TLS/HTTP 栈"
             "（curl 走 OpenSSL、urllib 走 Python 的 ssl），因此**只测 curl 会误判**。")
    L.append("- `urllib+UA+proxy` 走 `HTTP(S)_PROXY` 指定的 HTTP 代理端口"
             "（clash-verge / mihomo 默认 7897，**39133 是控制端口，不能当 HTTP 代理**）。")
    L.append("- 失败信息保留原始异常串，便于区分 DNS / TLS / 超时 / HTTP 错误。")
    L.append("- **直连是抖动的**：同一台机器上 `curl` 直连曾出现 `error:0A000126:SSL "
             "routines::unexpected eof`，而 urllib 直连同时刻成功 —— 所以脚本统一用 "
             "`urllib + User-Agent`，并保留代理 7897 作兜底，而不是把结论钉死在 "
             "「curl 通 / 不通」上。")
    L.append("")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OKX 访问可达性矩阵")
    ap.add_argument("--offline", action="store_true",
                    help="不联网：只渲染已有结果（无结果时输出空表）")
    ap.add_argument("--proxy", type=str, default=DEFAULT_PROXY,
                    help=f"HTTP 代理地址（默认 {DEFAULT_PROXY}）")
    ap.add_argument("--timeout", type=float, default=10.0, help="单次请求超时（秒）")
    ap.add_argument("--attempts", type=int, default=2, help="每个组合的重试次数")
    ap.add_argument("--out", type=str, default="docs/OKX_ACCESS.md")
    ap.add_argument("--json", type=str, default="data/okx_access.json")
    ap.add_argument("--from-json", type=str, default="",
                    help="不联网：直接用已有结果 JSON 重新渲染报告")
    args = ap.parse_args(argv)

    if args.from_json:
        saved = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        probes = [Probe(**row) for row in saved["probes"]]
        meta = saved.get("meta") or {}
    else:
        proxy = None if args.offline else (args.proxy or None)
        probes = run_matrix(timeout=args.timeout, attempts=args.attempts,
                            proxy=proxy, offline=args.offline)
        meta = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "proxy": proxy, "timeout": args.timeout, "attempts": args.attempts,
            "offline": args.offline,
        }
    summary = summarize(probes)
    payload = {"meta": meta, "summary": summary,
               "probes": [p.as_row() for p in probes]}
    if not args.from_json:
        jp = Path(args.json)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(probes, summary, meta), encoding="utf-8")
    print(f"已写出 {out}" + ("" if args.from_json else f" 与 {args.json}"))
    print(summary["recommendation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
