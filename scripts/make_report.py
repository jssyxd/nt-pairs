#!/usr/bin/env python
"""VPS 侧 6 小时报告生成器（纯标准库，不联网、不需要任何密钥）。

输入：
  * `--logdir`      日志目录（默认 `/var/log/nt-pairs/`）：任意文本文件，按行解析；
  * `--collector`   L2 采集目录（默认 `data/collector/`）：`<VENUE>/<YYYYMMDD>.jsonl(.gz)`；
  * `--matrix`      可选，`data/matrix.json`（三场所矩阵，用于重复收盘率 / 基差 / 覆盖倍数）；
  * `--sim`         可选，`data/soxl_sim.json`（事件级回放，用于净 EV 估计）。

输出：
  * Markdown 报告（`--out`；缺省打到 stdout）；
  * 可选机读旁挂 JSON（`--json-out`），供 CI 的 summary 渲染器直接取数，避免解析 Markdown。

**脱敏**：任何 key / secret / passphrase / 私钥 / 账户地址一律只输出「前 6 位 + …」。

**鲁棒性**：日志目录不存在、采集目录为空、矩阵/模拟文件缺失 —— 一律产出「无数据」报告，
不抛异常（退出码 0）。这是 VPS cron 的硬要求：报告失败不能拖垮采集器。

用法：
    python scripts/make_report.py --out reports/20261112T120000Z.md \
        --json-out reports/20261112T120000Z.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------- 脱敏

_ASSIGN_RE = re.compile(
    r"(?i)([A-Za-z0-9_\-]*(?:api[_-]?key|apikey|secret|passphrase|password|private[_-]?key|privkey|"
    r"token|mnemonic|seed|wallet[_-]?address|account[_-]?address|access[_-]?key)[A-Za-z0-9_\-]*)"
    r"(\s*[:=]\s*)([\"']?)([^\s,\"'}\]]+)"
)
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")


def mask(value: str) -> str:
    """只允许输出前 6 位。"""
    return f"{value[:6]}…"


def redact(text: str) -> str:
    """把 key / secret / 私钥 / 账户地址替换成「前 6 位 + …」。"""
    if not text:
        return text
    out = _ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{mask(m.group(4))}", text)
    out = _ADDR_RE.sub(lambda m: mask(m.group(0)), out)
    out = _HEX_RE.sub(lambda m: mask(m.group(0)), out)
    return out


# ---------------------------------------------------------------- 小工具


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_age(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def num(values: Iterable[float]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    return f"{statistics.mean(vals):+.2f}"


def read_text(path: str) -> str:
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def load_json(path: str | None) -> Any:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 日志解析

_NUM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*(-?\d+(?:\.\d+)?)")
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
_FINDING_RE = re.compile(r"(?i)\bFINDING\s*[:：]\s*(.+)")
_STATE_RE = re.compile(r"(?i)\bstate\s*=\s*(\w+)")

BASIS_KEYS = ("basis_bps", "basis", "spread_bps", "edge_bps")
EV_KEYS = ("net_ev_bps", "ev_bps", "net_bps", "expected_net_bps")
PFILL_KEYS = ("p_fill", "pfill", "p_fill_prob")


_EVENT_RE = re.compile(r"(?i)\bevent[_=:]\s*[\"']?([A-Za-z0-9_\-]+)")
# 只有带 `event=` 的行才按事件名分类；否则用受限的关键词兜底（避免把
# `timeout`（网络超时）误记成「未成交」、把 `p_fill=0.62` 误记成「成交」）。
_NO_FILL_EVENTS = ("no_fill", "nofill", "unfilled", "not_filled", "no-fill", "cancel",
                   "cancelled", "canceled", "expire", "expired", "reject", "rejected")
_FILL_EVENTS = ("fill", "filled", "exec", "executed", "hedge_filled")
_RISK_EVENTS = ("risk", "风控", "halt", "stop_trading", "killed", "delist", "kill_switch",
                "risk_limit", "risk_rejected")
_ERROR_EVENTS = ("error", "traceback", "exception", "critical", "failed", "失败", "fatal")
_NO_FILL_WORDS = ("no_fill", "nofill", "unfilled", "not_filled", "未成交")
_FILL_WORDS = ("filled", "成交")
_RISK_WORDS = ("risk", "风控", "halt", "stop_trading", "margin", "kill_switch")
_ERROR_WORDS = ("error", "traceback", "exception", "critical", "fatal", "失败")


def _event_name(low: str) -> str:
    m = _EVENT_RE.search(low)
    return m.group(1).lower() if m else ""


def _words(low: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9_\-一-鿿]+", low) if w]


def _is_no_fill(low: str) -> bool:
    ev = _event_name(low)
    if ev:
        return any(k in ev for k in _NO_FILL_EVENTS)
    return any(k in low for k in _NO_FILL_WORDS)


def _is_fill(low: str) -> bool:
    ev = _event_name(low)
    if ev:
        if any(k in ev for k in _NO_FILL_EVENTS):
            return False
        return any(k in ev for k in _FILL_EVENTS)
    if any(k in low for k in _NO_FILL_WORDS):
        return False
    if any(w in ("filled", "fill") for w in _words(low)):
        return True
    return "成交" in low


def _is_risk(low: str) -> bool:
    ev = _event_name(low)
    if ev:
        return any(k in ev for k in _RISK_EVENTS)
    return any(w in _RISK_WORDS for w in _words(low)) or "风控" in low


def _is_error(low: str) -> bool:
    ev = _event_name(low)
    if ev:
        return any(k in ev for k in _ERROR_EVENTS)
    return any(w in _ERROR_WORDS for w in _words(low))


def _line_in_window(line: str, since: datetime, file_fresh: bool) -> bool:
    """行是否落在统计窗口内：行内时间戳优先；无时间戳时用「文件最后修改时间在窗口内」兜底。"""
    if since is None:
        return True
    m = _TS_RE.search(line)
    if m:
        try:
            t = datetime.fromisoformat(m.group(1).replace(" ", "T"))
        except ValueError:
            t = None
        if t is not None:
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t >= since
    return file_fresh


def scan_logs(logdir: str, since: datetime) -> dict:
    """统计日志：成交 / 未成交 / 风控 / 异常 / 基差 / 净 EV / 新发现。"""
    out = {
        "files": [], "lines": 0, "lines_in_window": 0, "fills": 0, "no_fills": 0,
        "risk_events": 0, "errors": 0, "basis": [], "ev": [], "p_fill": [], "findings": [],
        "error_signatures": {}, "dir_exists": os.path.isdir(logdir),
    }
    if not out["dir_exists"]:
        return out
    now = utcnow()
    window_s = (now - since).total_seconds() if since else 0.0
    for root, _dirs, names in os.walk(logdir):
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            age_s = (now - datetime.fromtimestamp(st.st_mtime, timezone.utc)).total_seconds()
            text = read_text(path)
            lines = text.splitlines()
            rel = os.path.relpath(path, logdir)
            state = ""
            for line in lines[-200:]:
                m = _STATE_RE.search(line)
                if m:
                    state = m.group(1).lower()
            last_ts = ""
            for line in reversed(lines):
                m = _TS_RE.search(line)
                if m:
                    last_ts = m.group(1)
                    break
            out["files"].append({
                "name": rel, "bytes": st.st_size, "age_h": round(age_s / 3600, 2),
                "lines": len(lines), "state": state, "last_ts": last_ts,
                "in_window": age_s <= window_s,
            })
            for line in lines:
                out["lines"] += 1
                # 窗口判定：行内时间戳优先；没有时间戳时退回「文件最后修改时间在窗口内」
                if not _line_in_window(line, since, age_s <= window_s):
                    continue
                out["lines_in_window"] += 1
                low = line.lower()
                if _is_fill(low):
                    out["fills"] += 1
                if _is_no_fill(low):
                    out["no_fills"] += 1
                if _is_risk(low):
                    out["risk_events"] += 1
                if _is_error(low):
                    out["errors"] += 1
                    sig = redact(line.strip())[:140]
                    out["error_signatures"][sig] = out["error_signatures"].get(sig, 0) + 1
                fm = _FINDING_RE.search(line)
                if fm:
                    finding = redact(fm.group(1).strip())[:200]
                    if finding and finding not in out["findings"]:
                        out["findings"].append(finding)
                for key, val in _NUM_RE.findall(line):
                    k = key.lower()
                    try:
                        v = float(val)
                    except ValueError:
                        continue
                    if k in BASIS_KEYS:
                        out["basis"].append(v)
                    elif k in EV_KEYS:
                        out["ev"].append(v)
                    elif k in PFILL_KEYS:
                        out["p_fill"].append(v)
    return out


# ---------------------------------------------------------------- 采集统计


def _rec_in_window(rec: dict, since: datetime) -> bool:
    """按记录自身的时间戳判断是否落在统计窗口内。"""
    if since is None:
        return True
    raw = rec.get("ts")
    if isinstance(raw, str) and raw:
        try:
            t = datetime.fromisoformat(raw)
        except ValueError:
            t = None
        if t is not None:
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t >= since
    epoch = rec.get("t_epoch")
    if isinstance(epoch, (int, float)):
        return datetime.fromtimestamp(epoch, timezone.utc) >= since
    return False


def scan_collector(root: str, since: datetime) -> dict:
    """按场所统计 L2 快照数、覆盖时段、重复收盘率（同中位价未变占比，stale-print 代理）。"""
    out = {"root": root, "exists": os.path.isdir(root), "venues": {}, "total_samples": 0,
           "window_samples": 0}
    if not out["exists"]:
        return out
    for venue in sorted(os.listdir(root)):
        vdir = os.path.join(root, venue)
        if not os.path.isdir(vdir):
            continue
        vinfo = {"samples": 0, "window_samples": 0, "files": 0, "first_ts": "", "last_ts": "",
                 "symbols": {}, "stale_ratio": None}
        prev_mid: dict[str, float] = {}
        for name in sorted(os.listdir(vdir)):
            if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                continue
            path = os.path.join(vdir, name)
            if not os.path.isfile(path):
                continue
            vinfo["files"] += 1
            try:
                opener = (gzip.open(path, "rt", encoding="utf-8", errors="replace")
                          if name.endswith(".gz")
                          else open(path, "r", encoding="utf-8", errors="replace"))
                with opener as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        vinfo["samples"] += 1
                        ts = str(rec.get("ts") or rec.get("t_epoch") or "")
                        if ts and (not vinfo["first_ts"] or ts < vinfo["first_ts"]):
                            vinfo["first_ts"] = ts
                        if ts and ts > vinfo["last_ts"]:
                            vinfo["last_ts"] = ts
                        # 窗口内样本按**记录自身的时间戳**判定（文件 mtime 会被最后写入污染）
                        if _rec_in_window(rec, since):
                            vinfo["window_samples"] += 1
                        sym = str(rec.get("symbol") or "")
                        bids = rec.get("bids") or []
                        asks = rec.get("asks") or []
                        if not sym or not bids or not asks:
                            continue
                        try:
                            mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                        except (TypeError, ValueError, IndexError):
                            continue
                        s = vinfo["symbols"].setdefault(sym, {"n": 0, "stale": 0})
                        s["n"] += 1
                        if sym in prev_mid and prev_mid[sym] == mid:
                            s["stale"] += 1
                        prev_mid[sym] = mid
            except OSError:
                continue
        total_n = sum(s["n"] for s in vinfo["symbols"].values())
        total_stale = sum(s["stale"] for s in vinfo["symbols"].values())
        vinfo["stale_ratio"] = (total_stale / total_n) if total_n else None
        vinfo["n_symbols"] = len(vinfo["symbols"])
        out["venues"][venue] = vinfo
        out["total_samples"] += vinfo["samples"]
        out["window_samples"] += vinfo["window_samples"]
    return out


# ---------------------------------------------------------------- 矩阵 / 模拟


def matrix_stats(matrix: Any) -> dict:
    out: dict[str, Any] = {"available": False, "generated_utc": "", "pairs": 0,
                            "tradable": 0, "top": [], "repeat_close": [], "basis_p95": []}
    if not isinstance(matrix, dict) or not isinstance(matrix.get("pairs"), list):
        return out
    out["available"] = True
    out["generated_utc"] = str(matrix.get("generated_utc") or "")
    rows = []
    for p in matrix["pairs"]:
        if not isinstance(p, dict):
            continue
        score = p.get("score") or {}
        costs = p.get("costs") or {}
        cap = p.get("capacity") or {}
        win = (p.get("windows") or {}).get("30d") or {}
        rows.append({
            "pair": str(p.get("pair") or ""), "sym": str(p.get("sym") or ""),
            "edge": score.get("edge_ratio"), "b_star": costs.get("b_star_bps"),
            "k_b_star": costs.get("k_b_star"), "cap": cap.get("cap_usd"),
            "p95": win.get("p95"), "mean": win.get("mean"), "n": win.get("n"),
            "tradable": bool(p.get("tradable")),
            "cv": p.get("data_quality_cv"),
            "repeat_a": p.get("repeat_close_pct_A"), "repeat_b": p.get("repeat_close_pct_B"),
            "reasons": [str(r) for r in (p.get("reasons") or [])][:3],
        })
    out["pairs"] = len(rows)
    out["tradable"] = sum(1 for r in rows if r["tradable"])
    rows.sort(key=lambda r: (not r["tradable"], r["edge"] is None, -(r["edge"] or 0)))
    out["top"] = rows[:5]
    for r in rows:
        if r["repeat_a"] is not None or r["repeat_b"] is not None:
            out["repeat_close"].append({"pair": r["pair"], "a": r["repeat_a"], "b": r["repeat_b"],
                                        "cv": r["cv"], "tradable": r["tradable"]})
    # 重复收盘率最差的 8 对（陈旧报价嫌疑最大）
    out["repeat_close"].sort(key=lambda r: -max(r["a"] or 0.0, r["b"] or 0.0))
    # p95 基差只统计「可交易」对：被 unit_fail / stale_print 排除的对会污染均值
    out["basis_p95"] = [r["p95"] for r in rows
                        if r["tradable"] and isinstance(r["p95"], (int, float))]
    out["basis_p95_all"] = [r["p95"] for r in rows if isinstance(r["p95"], (int, float))]
    return out


def sim_stats(sim: Any) -> dict:
    out: dict[str, Any] = {"available": False}
    if not isinstance(sim, dict):
        return out
    out["available"] = True
    out["generated_utc"] = str(sim.get("generated_utc") or "")
    out["pair"] = str(sim.get("pair") or "")
    out["overall"] = sim.get("overall") or {}
    out["costs"] = sim.get("costs") or {}
    out["capacity"] = sim.get("capacity") or {}
    out["basis"] = sim.get("basis_5m_30d") or {}
    out["legs"] = sim.get("legs") or {}
    out["by_session"] = sim.get("by_session") or {}
    return out


# ---------------------------------------------------------------- 渲染


def esc(v: Any) -> str:
    """Markdown 表格单元格转义（pair 名里带 `|`）。"""
    return str(v).replace("|", "\\|")


def fmt(v: Any, digits: int = 2, default: str = "n/a") -> str:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return f"{v:.{digits}f}"
    return str(v)


def render(ctx: dict) -> str:
    gen = ctx["generated_utc"]
    window_h = ctx["window_hours"]
    logs = ctx["logs"]
    coll = ctx["collector"]
    mx = ctx["matrix"]
    sm = ctx["sim"]
    now = ctx["now"]

    L: list[str] = []
    L.append(f"# nt-pairs 6 小时报告 — {gen}")
    L.append("")
    L.append(f"- 生成时间(UTC)：{gen}")
    L.append(f"- 覆盖窗口：最近 {window_h:g} 小时（自 {ctx['window_start']}）")
    L.append(f"- 数据来源：`{ctx['logdir']}`、`{ctx['collector_dir']}`"
             + ("、`data/matrix.json`" if mx["available"] else "")
             + ("、`data/soxl_sim.json`" if sm["available"] else ""))
    L.append("- 脱敏：所有 key / secret / passphrase / 私钥 / 账户地址仅输出「前 6 位 + …」。")
    L.append("")

    # 1. 进程状态
    L.append("## 1. 进程状态")
    L.append("")
    if not logs["files"]:
        L.append(f"- ⚠️ 日志目录 `{ctx['logdir']}` 不存在或为空 —— **无数据**（采集器可能未启动或路径不同）。")
    else:
        L.append("| 日志 | 最后写入(UTC) | 距今 | 状态 | 行数 |")
        L.append("|---|---|---:|---|---:|")
        for f in sorted(logs["files"], key=lambda x: x["age_h"]):
            state = f["state"] or ("running" if f["age_h"] < 1 else "stale")
            L.append(f"| `{f['name']}` | {f['last_ts'] or '—'} | {human_age(f['age_h'] * 3600)} | "
                     f"{state} | {f['lines']} |")
    L.append("")

    # 2. 采集统计
    L.append("## 2. 采集样本数与重复收盘率")
    L.append("")
    if not coll["venues"]:
        L.append(f"- ⚠️ 采集目录 `{ctx['collector_dir']}` 不存在或无样本 —— **无数据**。")
    else:
        L.append("| 场所 | 窗口内样本 | 累计样本 | 文件 | 标的数 | 重复收盘率(同mid未变) | 覆盖起 | 覆盖止 |")
        L.append("|---|---:|---:|---:|---:|---:|---|---|")
        for venue, v in coll["venues"].items():
            L.append(f"| {venue} | {v['window_samples']} | {v['samples']} | {v['files']} | "
                     f"{v.get('n_symbols', 0)} | {fmt(v['stale_ratio'] * 100 if v['stale_ratio'] is not None else None, 1)}% "
                     f"| {v['first_ts'] or '—'} | {v['last_ts'] or '—'} |")
        L.append("")
        L.append(f"- 采集合计：窗口内 **{coll['window_samples']}** 条 / 累计 **{coll['total_samples']}** 条。")
        L.append("- 重复收盘率 = 相邻快照中位价完全未变的比例（L2 快照上的 stale-print 代理；"
                 "小时收盘口径见 §3 的矩阵重复收盘率）。")
    if mx["repeat_close"]:
        L.append("")
        L.append("矩阵口径（小时收盘，重复收盘率 > 10% 视为报价陈旧）：")
        L.append("")
        L.append("| pair | A 侧 | B 侧 | CV | 可交易 |")
        L.append("|---|---:|---:|---:|:--:|")
        for r in mx["repeat_close"][:8]:
            L.append(f"| {esc(r['pair'])} | {fmt(r['a'], 1)}% | {fmt(r['b'], 1)}% | {fmt(r['cv'], 5)} | "
                     f"{'✅' if r['tradable'] else '❌'} |")
    L.append("")

    # 3. 基差统计
    L.append("## 3. 基差统计")
    L.append("")
    if mx["available"] and mx["top"]:
        L.append(f"- 矩阵生成于 {mx['generated_utc']}，共 {mx['pairs']} 对，其中 **可交易 {mx['tradable']}** 对。")
        L.append("")
        L.append("| pair | 30d mean(bps) | p95(bps) | b* | 覆盖倍数 | 容量($) | 可交易 |")
        L.append("|---|---:|---:|---:|---:|---:|:--:|")
        for r in mx["top"]:
            L.append(f"| {esc(r['pair'])} | {fmt(r['mean'], 1)} | {fmt(r['p95'], 1)} | {fmt(r['b_star'], 1)} | "
                     f"{fmt(r['edge'], 2)} | {fmt(r['cap'], 0)} | {'✅' if r['tradable'] else '❌'} |")
        if mx["basis_p95"]:
            L.append("")
            L.append(f"- 可交易对的 30d p95 基差：均值 {num(mx['basis_p95'])} bps，"
                     f"最大 {max(mx['basis_p95']):.1f} bps。")
        elif mx.get("basis_p95_all"):
            L.append("")
            L.append(f"- ⚠️ 无可交易对；全矩阵 p95 基差均值 {num(mx['basis_p95_all'])} bps"
                     f"（已被 unit_fail / 陈旧报价污染，仅供参考）。")
    else:
        L.append("- 矩阵数据不可用 —— **无数据**。")
    if logs["basis"]:
        L.append(f"- 日志口径基差样本 {len(logs['basis'])} 条，均值 {num(logs['basis'])} bps。")
    L.append("")

    # 4. 成交 / 未成交
    L.append("## 4. 成交 / 未成交计数")
    L.append("")
    L.append(f"- 成交：**{logs['fills']}**")
    L.append(f"- 未成交：**{logs['no_fills']}**")
    L.append(f"- 风控事件：**{logs['risk_events']}**")
    L.append(f"- 日志行数：窗口内 {logs['lines_in_window']} / 全部 {logs['lines']}")
    if not logs["fills"] and not logs["no_fills"]:
        L.append("- ⚠️ 未从日志中识别到成交/未成交事件 —— 交易链路尚未上线、日志格式不同，"
                 "或这些事件不在统计窗口内。")
    L.append("")

    # 5. 净 EV 估计
    L.append("## 5. 净 EV 估计")
    L.append("")
    if sm["available"]:
        ov = sm["overall"]
        co = sm["costs"]
        cap = sm["capacity"]
        L.append(f"- 对：{sm['pair']}（回放 {ov.get('n', 'n/a')} 笔，胜率 {fmt(ov.get('win_rate'), 1)}%）")
        L.append(f"- 捕获 {fmt(ov.get('mean_capture_bps'), 1)} bps/轮，成本 b* = {fmt(co.get('b_star'), 2)} bps"
                 f"（费 {fmt(co.get('fees_hl'), 1)} + {fmt(co.get('fees_lt'), 1)}，"
                 f"滑点 {fmt(co.get('slip_hl'), 2)}/{fmt(co.get('slip_lt'), 2)}）")
        L.append(f"- **净 EV = {fmt(ov.get('net_mean_bps'), 2)} bps/轮**；"
                 f"p_fill=0.2 → {fmt(ov.get('exp_net_bps_pfill_0.2'), 2)}，"
                 f"p_fill=0.5 → {fmt(ov.get('exp_net_bps_pfill_0.5'), 2)}，"
                 f"p_fill=0.8 → {fmt(ov.get('exp_net_bps_pfill_0.8'), 2)}")
        L.append(f"- 容量上限（±25bps 深度的 10%）：${fmt(cap.get('cap_usd_10pct_of_depth25'), 0)}")
        L.append(f"- 基差（5m/2d）：n={sm['basis'].get('n', 'n/a')}，mean {fmt(sm['basis'].get('mean'), 1)} bps，"
                 f"p95|.| {fmt(sm['basis'].get('p95_abs'), 1)} bps")
        for sess, s in (sm["by_session"] or {}).items():
            if isinstance(s, dict):
                L.append(f"  - 会话 {sess}：净 {fmt(s.get('net_mean_bps'), 2)} bps/轮"
                         f"（捕获 {fmt(s.get('mean_capture_bps'), 1)}，胜率 {fmt(s.get('win_rate'), 1)}%）")
    else:
        L.append("- 事件级回放数据不可用 —— **无数据**。")
    if logs["ev"]:
        L.append(f"- 日志口径净 EV 样本 {len(logs['ev'])} 条，均值 {num(logs['ev'])} bps。")
    if logs["p_fill"]:
        L.append(f"- 日志口径 p_fill 样本 {len(logs['p_fill'])} 条，均值 {num(logs['p_fill'])}。")
    L.append("")

    # 6. 风控事件
    L.append("## 6. 风控事件")
    L.append("")
    if logs["risk_events"]:
        L.append(f"- 日志中识别到 **{logs['risk_events']}** 条风控相关事件（risk / 风控 / halt / 拒单 / 保证金）。")
    else:
        L.append("- 未识别到风控事件。")
    L.append("")

    # 7. 异常摘要
    L.append("## 7. 异常摘要")
    L.append("")
    sigs = sorted(logs["error_signatures"].items(), key=lambda kv: -kv[1])[:10]
    if not sigs:
        L.append("- 无异常。")
    else:
        L.append("| 次数 | 异常（已脱敏） |")
        L.append("|---:|---|")
        for sig, n in sigs:
            L.append(f"| {n} | `{sig}` |")
    L.append("")

    # 8. 新发现
    L.append("## 8. 新发现清单")
    L.append("")
    if logs["findings"]:
        for f in logs["findings"][:20]:
            L.append(f"- {f}")
    elif mx["available"] and mx["top"]:
        L.append("- 日志中无显式 FINDING 标记；以下为矩阵口径的自动发现：")
        for r in mx["top"]:
            tags = []
            if r["tradable"]:
                tags.append("覆盖为正")
            if (r["repeat_a"] or 0) > 10 or (r["repeat_b"] or 0) > 10:
                tags.append("疑似陈旧报价")
            if r["cv"] is not None and r["cv"] > 0.005:
                tags.append("CV 超阈")
            L.append(f"- {r['pair']}：{'、'.join(tags) if tags else '无标记'}"
                     f"（覆盖倍数 {fmt(r['edge'], 2)}，容量 ${fmt(r['cap'], 0)}）")
    else:
        L.append("- 无数据。")
    L.append("")

    L.append("## 9. 边界")
    L.append("")
    L.append("- 本报告只统计**公开数据与本机日志**；不含任何交易所凭据、私钥或账户地址。")
    L.append("- 「重复收盘率」在 §2 为 L2 快照的中位价未变比例（代理指标），§3 为小时收盘的真实口径。")
    L.append("- 净 EV 估计来自事件级回放（`scripts/sim_soxl.py`），**不等于**实盘可得；"
             "p_fill 情景是假设，不是实测。")
    L.append(f"- 报告生成于 {gen}（UTC），由 `scripts/make_report.py` 产生。")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="生成 nt-pairs 的 6 小时 Markdown 报告（VPS 侧，纯标准库、无密钥）。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--logdir", default="/var/log/nt-pairs", help="日志目录")
    p.add_argument("--collector", default="data/collector", help="L2 采集目录")
    p.add_argument("--matrix", default="data/matrix.json", help="矩阵 JSON（可选，缺失即无数据）")
    p.add_argument("--sim", default="data/soxl_sim.json", help="事件级回放 JSON（可选，缺失即无数据）")
    p.add_argument("--out", default=None, help="Markdown 输出路径；缺省打到 stdout")
    p.add_argument("--json-out", default=None, help="机读旁挂 JSON 输出路径（CI 用）")
    p.add_argument("--window-hours", type=float, default=6.0, help="统计窗口（小时）")
    p.add_argument("--now", default=None, help="覆盖当前时间（ISO8601，测试用）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    since = now - timedelta(hours=args.window_hours)

    logs = scan_logs(args.logdir, since)
    coll = scan_collector(args.collector, since)
    mx = matrix_stats(load_json(args.matrix))
    sm = sim_stats(load_json(args.sim))

    ctx = {
        "generated_utc": iso(now), "now": now, "window_hours": args.window_hours,
        "window_start": iso(since), "logdir": args.logdir, "collector_dir": args.collector,
        "logs": logs, "collector": coll, "matrix": mx, "sim": sm,
    }
    md = render(ctx)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(md)

    if args.json_out:
        side = {
            "generated_utc": ctx["generated_utc"], "window_start": ctx["window_start"],
            "window_hours": args.window_hours,
            "logs": {k: v for k, v in logs.items() if k != "error_signatures"},
            "error_signatures": [{"count": n, "text": s}
                                 for s, n in sorted(logs["error_signatures"].items(), key=lambda kv: -kv[1])],
            "collector": coll,
            "matrix": mx,
            "sim": sm,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(side, fh, ensure_ascii=False, indent=1)
        print(f"wrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
