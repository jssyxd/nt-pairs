#!/usr/bin/env python
"""独立复核（E8）：数值、风控与「可交易」结论的第三方重算。

只读仓库既有实现（`src/nt_pairs/*`、`scripts/build_matrix.py`、
`scripts/screen_candidates.py`），不改它们；本脚本只做复算与对拍。

四个复核问题（对应任务书）：
  Q1 EV 口径      —— `leg_policy.expected_net_bps` / `pair_cost_bps` 与 `docs/EV_AND_MONITORING.md`
                     的公式逐项对拍；腿风险量级表（σ_1m × t_leg × P_1leg）。
  Q2 费率对拍     —— HL HIP-3 `deployerFeeScale` + `growthMode` 的**官方费率公式**、
                     Lighter `maker_fee/taker_fee`、OKX `public/instruments` 费率字段。
  Q3 五道闸门     —— `scripts/build_matrix.py` 里哪些闸门真的作用在 `tradable` 上；
                     `repeat_close_pct` 的口径（close / 分母 / 阈值）。
  Q4 可交易反证   —— 事件级 fade 回放的**同价口径**复算（开→开 / 收→收）、
                     成本账重算、以及「真实净 EV 区间」。

用法：
    python scripts/recheck.py                 # 有网就跑实时对拍，无网自动退回仓库内数据
    python scripts/recheck.py --offline       # 强制只用仓库内数据（data/recheck 缓存）
    python scripts/recheck.py --quick         # 跳过需要时间的采样
输出：终端 markdown + `data/recheck/recheck.json`
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
import urllib.request
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from decimal import Decimal  # noqa: E402

from nt_pairs.leg_policy import (  # noqa: E402
    LegQuote,
    expected_net_bps,
    leg_risk_bps,
    maker_cost_bps,
    pair_cost_bps,
    taker_cost_bps,
)

HL = "https://api.hyperliquid.xyz/info"
LT = "https://mainnet.zklighter.elliot.ai/api/v1"
OKX = "https://www.okx.com/api/v5"
UA = {"User-Agent": "Mozilla/5.0"}
CACHE = os.path.join(ROOT, "data", "recheck")

D = Decimal


def _http(url: str, payload: dict | None = None, timeout: int = 30) -> Any:
    if payload is None:
        req = urllib.request.Request(url, headers=UA)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


def _try(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


# =============================================================================
# Q1  EV 口径复核
# =============================================================================
def q1_ev_formula_audit() -> dict:
    """把 `leg_policy` 的实现与 `docs/EV_AND_MONITORING.md` 的公式逐项对拍。"""
    m = LegQuote(venue="HL", instrument_id="xyz:SOXL", maker_bps=D("1.5"), taker_bps=D("4.5"),
                 half_spread_bps=D("1.2"), depth25_usd=D("365000"), tick=D("0.01"), price=D("64.0"),
                 p_fill=D("0.5"), carry_bps_h=D("0.05"))
    t = LegQuote(venue="LIGHTER", instrument_id="SOXL", maker_bps=D("0"), taker_bps=D("0"),
                 half_spread_bps=D("1.5"), depth25_usd=D("495000"), tick=D("0.01"), price=D("64.0"),
                 p_fill=None, carry_bps_h=D("0"))

    findings: list[str] = []

    # (a) expected_net_bps == p_fill * (capture - cost)  —— 形式上与文档一致
    cap = D("16.9")
    net = expected_net_bps(m, t, cap, D("500"))
    cost = pair_cost_bps(m, t, D("500"))
    form_ok = net == m.p_fill * (cap - cost)
    findings.append("expected_net_bps 形式 == p_fill×(capture−cost)："
                    f"{'成立' if form_ok else '不成立'}（净={net}，cost={cost}）")

    # (b) p_fill 未知时按 1 计 → 上界
    net_nop = expected_net_bps(t, m, cap, D("500"))
    findings.append(f"p_fill 未知时按 1 计：净={net_nop}（乐观上界，不是期望值）")

    # (c) pair_cost_bps 的六项覆盖 —— 用「一次成交的成本项」做差分探针
    probes: dict[str, bool] = {}
    base = pair_cost_bps(m, t, D("500"))
    probes["fee(maker leg)"] = pair_cost_bps(
        LegQuote(**{**m.__dict__, "maker_bps": D("2.5")}), t, D("500")) > base
    probes["fee(taker leg)"] = pair_cost_bps(
        m, LegQuote(**{**t.__dict__, "taker_bps": D("1.0")}), D("500")) > base
    probes["half_spread"] = pair_cost_bps(
        LegQuote(**{**m.__dict__, "half_spread_bps": D("2.2")}), t, D("500")) > base
    probes["slippage(taker leg)"] = pair_cost_bps(
        m, LegQuote(**{**t.__dict__, "tick": D("0.1")}), D("500")) > base
    probes["carry"] = pair_cost_bps(
        m, LegQuote(**{**t.__dict__, "carry_bps_h": D("1.0")}), D("500")) > base
    probes["leg_risk"] = pair_cost_bps(m, t, D("500"), leg_risk_bps=D("5")) > base
    probes["recon"] = pair_cost_bps(m, t, D("500"), recon_bps=D("5")) > base

    # (d) 关键缺陷：一轮 = 开仓 + 平仓，但成本只计了「一次 maker + 一次 taker」
    full_round_cost = round_cost_bps(
        leader_maker_bps=D("1.5"), leader_taker_bps=D("4.5"),
        follower_taker_bps=D("0"), follower_maker_bps=D("0"),
        hs_leader_bps=D("1.2"), hs_follower_bps=D("1.5"),
        slip_leader_bps=D("0.33"), slip_follower_bps=D("3.27"),
        carry_leader_bps_h=D("0.05"), carry_follower_bps_h=D("0.0"),
        hold_h=D("0.1"), leg_risk_bps=D("0.3"), recon_bps=D("0.5"))
    frc = full_round_cost["total"]
    findings.append(
        "一轮完整成本（开+平，leader=maker / follower=taker）= "
        f"{frc} bps，而 pair_cost_bps = {base} bps；"
        f"缺口 {frc - base} bps = 平仓腿的 taker 费 + 平仓腿的半价差/滑点 + 第二腿资金费")

    # (e) leg_risk 是常数 0.3，而不是由 σ_1m / t_leg / P_1leg 推出
    lr_const = pair_cost_bps(m, t, D("500"))
    lr_hi = pair_cost_bps(m, t, D("500"), leg_risk_bps=leg_risk_bps(D("12.9"), D("30"), D("0.30")))
    findings.append(f"pair_cost_bps 的腿风险是写死常数 0.3（实测 t=30s/P=0.30 应为 {lr_hi}）："
                    f"成本被低估 {lr_hi - lr_const} bps/轮")

    # (f) 滑点用「2 tick」代理，不是按规模吃盘口
    findings.append(f"slip_bps 只用 2×tick/price 代理（taker 腿 {taker_cost_bps(t, D('500')) - D('1.5')} bps），"
                    "与文档 §5 要求的「按规模吃盘口 VWAP」不是一回事")

    return {"form_ok": form_ok, "probes": probes, "base_cost": str(base),
            "full_round_cost": str(full_round_cost), "findings": findings}


def leg_risk_table(sigmas=(D("12.9"), D("11.6767")), t_legs=(D("2"), D("30")),
                   p_legs=(D("0.15"), D("0.30")), alpha=D("0.5")) -> list[dict]:
    """腿风险量级表：√(2/π)·σ_1m·√(t_leg/60)·P_1leg·α。"""
    rows = []
    for s in sigmas:
        for t in t_legs:
            for p in p_legs:
                rows.append({"sigma_1m": str(s), "t_leg_s": str(t), "p_1leg": str(p),
                             "alpha": str(alpha), "leg_risk_bps": float(leg_risk_bps(s, t, p, alpha))})
    return rows


def q1_leg_risk_verdict() -> dict:
    """回答「t_leg=30s 时腿风险能否吃掉限价方案的全部收益」。"""
    sigma = D("12.9")
    lr30 = float(leg_risk_bps(sigma, D("30"), D("0.30")))
    lr30_lo = float(leg_risk_bps(sigma, D("30"), D("0.15")))
    lr2 = float(leg_risk_bps(sigma, D("2"), D("0.30")))
    # MATRIX_SUMMARY §3.0 的 headline 与 §4 的规模敏感性
    headline = 5.3      # SOXL 净 EV/轮（b*=11.6 的旧口径）
    at500 = 1.3         # §4：$500/腿 规模下的净
    at100 = 4.1         # §4：$100/腿
    return {"leg_risk_30s_p030": lr30, "leg_risk_30s_p015": lr30_lo, "leg_risk_2s_p030": lr2,
            "share_of_headline": lr30 / headline, "share_of_500": lr30 / at500,
            "share_of_100": lr30 / at100,
            "verdict": (f"t_leg=30s 时腿风险 {lr30:.2f} bps（P=0.30）/ {lr30_lo:.2f} bps（P=0.15）："
                        f"吃掉 headline +{headline} 的 {100*lr30/headline:.0f}%，"
                        f"但吃掉 $500 规模净 +{at500} 的 {100*lr30/at500:.0f}% —— "
                        "腿风险不是主要矛盾，退出侧成本与 p_fill 才是")}


# =============================================================================
# Q2  费率对拍（公开端点）
# =============================================================================
def hl_fee_formula(base_maker_bps: float, base_taker_bps: float, deployer_fee_scale: float,
                   growth_mode: bool, referral_discount: float = 0.0,
                   aligned_quote: bool = False) -> dict:
    """官方 `feeRates` 公式（docs/trading/fees.md「Fee formula for developers」）。

        growthMultiplier = growthMode ? 0.1 : 1
        hip3Scale       = scale < 1 ? scale + 1 : scale * 2
        maker' = maker * growth * hip3Scale * (1 - referral)   (maker>0)
        taker' = taker * hip3Scale * growth * (1 - referral)
    """
    growth_mult = 0.1 if growth_mode else 1.0
    hip3_scale = deployer_fee_scale + 1 if deployer_fee_scale < 1 else deployer_fee_scale * 2
    deployer_share = (deployer_fee_scale / (1 + deployer_fee_scale)
                      if deployer_fee_scale < 1 else 0.5)
    maker = base_maker_bps * growth_mult * hip3_scale * (1 - referral_discount)
    taker = base_taker_bps * hip3_scale * growth_mult * (1 - referral_discount)
    if aligned_quote:
        maker *= (1 - deployer_share) * 1.5 + deployer_share
        taker *= (1 - deployer_share) * 0.8 + deployer_share
    return {"maker_bps": maker, "taker_bps": taker, "hip3_scale": hip3_scale,
            "growth_multiplier": growth_mult, "deployer_share": deployer_share,
            "all_in_round_bps": maker + taker}


def q2_fee_audit(offline: bool = False) -> dict:
    out: dict[str, Any] = {"offline": offline}

    # ---- HL：dex 级 deployerFeeScale + growthMode --------------------------------
    hl_assets = ("xyz:SOXL", "xyz:MRVL", "xyz:EWY", "xyz:RKLB", "xyz:BABA", "io:OAI")
    hl_rows = []
    for name in hl_assets:
        dex = name.split(":")[0]
        meta = _try(_http, HL, {"type": "meta", "dex": dex})
        if "__error__" in meta or "universe" not in meta:
            hl_rows.append({"asset": name, "error": meta.get("__error__", "bad payload")})
            continue
        row = next((u for u in meta["universe"] if u["name"] == name), None)
        if row is None:
            hl_rows.append({"asset": name, "error": "not in universe"})
            continue
        scale = float(row.get("deployerFeeScale", "nan"))
        gm = row.get("growthMode") == "enabled"
        eff = hl_fee_formula(1.5, 4.5, scale, gm)
        hl_rows.append({
            "asset": name, "deployer_fee_scale": scale, "growth_mode": gm,
            "repo_assumed_maker_bps": 1.5, "repo_assumed_taker_bps": 4.5,
            "effective_maker_bps": round(eff["maker_bps"], 4),
            "effective_taker_bps": round(eff["taker_bps"], 4),
            "effective_round_bps": round(eff["all_in_round_bps"], 4),
            "hip3_scale": eff["hip3_scale"], "deployer_share": eff["deployer_share"],
        })
        time.sleep(0.2)
    out["hl"] = hl_rows

    # ---- Lighter：maker_fee / taker_fee ------------------------------------------
    markets = {197: "SOXL", 174: "MRVL", 166: "EWY", 186: "RKLB", 192: "OPENAI"}
    lt_rows = []
    if not offline:
        d = _try(_http, f"{LT}/orderBookDetails")
        if "order_book_details" in d:
            for m in d["order_book_details"]:
                if m.get("market_id") in markets:
                    lt_rows.append({"market": markets[m["market_id"]], "market_id": m["market_id"],
                                    "maker_fee": float(m.get("maker_fee") or 0),
                                    "taker_fee": float(m.get("taker_fee") or 0),
                                    "maker_bps": float(m.get("maker_fee") or 0) * 1e4,
                                    "taker_bps": float(m.get("taker_fee") or 0) * 1e4,
                                    "is_maker_fee_enabled": m.get("is_maker_fee_enabled"),
                                    "is_taker_fee_enabled": m.get("is_taker_fee_enabled")})
        else:
            lt_rows.append({"error": d.get("__error__", "bad payload")})
    out["lighter"] = lt_rows

    # ---- OKX：public/instruments 有没有费率字段 ------------------------------------
    okx_info: dict[str, Any] = {"fee_fields_present": False, "sample": None,
                                 "note": "OKX 的费率按账户等级走，公开端点不携带费率字段"}
    if not offline:
        inst = _try(_http, f"{OKX}/public/instruments?instType=SWAP")
        if isinstance(inst, dict) and inst.get("data"):
            rows = inst["data"]
            okx_info["n_swap"] = len(rows)
            fields = set()
            for r in rows[:50]:
                fields |= set(r.keys())
            okx_info["fee_fields_present"] = bool({"makerFee", "takerFee", "makerFeeRate",
                                                    "takerFeeRate"} & fields)
            s = next((r for r in rows if r.get("instId") == "SOXL-USDT-SWAP"), None)
            okx_info["sample"] = {k: v for k, v in (s or {}).items()
                                  if k in ("instId", "instType", "ctVal", "ctValCcy", "tickSz",
                                           "lotSz", "settleCcy", "makerFee", "takerFee")}
            okx_info["sample_has_fee_fields"] = bool({"makerFee", "takerFee"} & set((s or {}).keys()))
    out["okx"] = okx_info
    return out


# =============================================================================
# Q3  五道闸门复核
# =============================================================================
def q3_gate_audit() -> dict:
    """从 `data/matrix.json` 复算每道闸门，并回到源码确认是否作用在 `tradable` 上。"""
    path = os.path.join(ROOT, "data", "matrix.json")
    if not os.path.exists(path):
        return {"error": "data/matrix.json 不存在"}
    payload = json.load(open(path, encoding="utf-8"))
    pairs = payload.get("pairs", [])

    gate_counts = {"unit_fail": 0, "stale_print_suspect": 0, "edge_le_0": 0,
                   "cap_lt_1000": 0, "tradable": 0}
    for p in pairs:
        if p.get("unit_fail"):
            gate_counts["unit_fail"] += 1
        if p.get("stale_print_suspect"):
            gate_counts["stale_print_suspect"] += 1
        edge = (p.get("score") or {}).get("edge_ratio")
        if edge is not None and edge <= 0:
            gate_counts["edge_le_0"] += 1
        cap = (p.get("capacity") or {}).get("cap_usd")
        if cap is None or cap < 1000:
            gate_counts["cap_lt_1000"] += 1
        if p.get("tradable"):
            gate_counts["tradable"] += 1

    # 逐对复算 tradable，看每道闸门各挡下了多少「本该 tradable」的候选
    per_gate_blocked = {"unit_fail": 0, "stale_print_suspect": 0, "edge_le_0": 0, "cap_lt_1000": 0}
    only_edge_ok = []
    for p in pairs:
        edge = (p.get("score") or {}).get("edge_ratio")
        cap = (p.get("capacity") or {}).get("cap_usd") or 0.0
        if edge is None or edge <= 0:
            continue
        if p.get("unit_fail"):
            per_gate_blocked["unit_fail"] += 1
            continue
        if p.get("stale_print_suspect"):
            per_gate_blocked["stale_print_suspect"] += 1
            continue
        if cap < 1000:
            per_gate_blocked["cap_lt_1000"] += 1
            continue
        only_edge_ok.append(p["pair"])

    # 源码层面：build_matrix.py 里 tradable 的判定式
    src = open(os.path.join(ROOT, "scripts", "build_matrix.py"), encoding="utf-8").read()
    has_event_ev_gate = ("screen_candidates" in src or "expected_net_bps" in src
                          or "capture" in src or "net_bps" in src)
    src_line = next((ln.strip() for ln in src.splitlines()
                     if "tradable" in ln and "bool(" in ln), "")

    # 事件级 EV 闸门在 screen_candidates.py 里是否有布尔判定
    scr = open(os.path.join(ROOT, "scripts", "screen_candidates.py"), encoding="utf-8").read()
    scr_has_tradable = "tradable" in scr

    # 单位比例是否真的作用到 basis（ratio 归一）——只看计算 basis 的那几行
    basis_lines = [ln.strip() for ln in src.splitlines() if "basis = [" in ln]
    ratio_applied = any(("ratio" in ln and ("/ ratio" in ln or "/ rm" in ln or "* rm" in ln))
                        for ln in basis_lines)

    # 重复收盘率的口径
    rep_src = [ln.strip() for ln in src.splitlines()
               if "same = sum" in ln or "len(vals) - 1" in ln or "len(series) - 1" in ln]
    rep_close_not_mid = "close" in src and "mid" not in rep_src[0] if rep_src else None

    # 泄漏：ratio 偏移量进入 basis
    leak = []
    for p in pairs:
        w = (p.get("windows") or {}).get("30d") or {}
        re_ = p.get("ratio_evidence") or {}
        if "mean" in re_ and w.get("mean") is not None:
            off = (re_["mean"] - 1) * 1e4
            leak.append({"pair": p["pair"], "ratio_offset_bps": off, "basis_mean_bps": w["mean"],
                         "basis_sd_bps": w.get("sd"), "p95": w.get("p95"),
                         "tradable": p.get("tradable"),
                         "edge_ratio": (p.get("score") or {}).get("edge_ratio")})
    leak.sort(key=lambda r: -abs(r["ratio_offset_bps"]))

    # signed p95 vs |basis| p95
    signed_issue = []
    for p in pairs:
        w = (p.get("windows") or {}).get("30d") or {}
        if w.get("mean") is not None and w.get("mean") < 0 and w.get("p95") is not None:
            signed_issue.append({"pair": p["pair"], "mean": w["mean"], "signed_p95": w["p95"],
                                 "p50": w.get("p50"), "sd": w.get("sd"),
                                 "edge_ratio": (p.get("score") or {}).get("edge_ratio")})

    return {"n_pairs": len(pairs), "gate_counts": gate_counts,
            "per_gate_blocked_after_edge": per_gate_blocked,
            "pairs_passing_all_matrix_gates": only_edge_ok,
            "tradable_line_in_build_matrix": src_line,
            "build_matrix_has_event_ev_gate": has_event_ev_gate,
            "screen_candidates_has_tradable_bool": scr_has_tradable,
            "ratio_applied_to_basis": ratio_applied, "basis_lines": basis_lines,
            "repeat_close_source_lines": rep_src, "repeat_close_uses_close": rep_close_not_mid,
            "ratio_offset_leak_top": leak[:12], "signed_p95_negative_mean": signed_issue}


def q3_repeat_close_semantics() -> dict:
    """`repeat_close_pct` 的口径判定 + 阈值建议。"""
    def repeat_pct(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        same = sum(1 for a, b in zip(values, values[1:]) if a == b)
        return 100.0 * same / (len(values) - 1)

    # ① 分母：相邻桶数 vs 桶数（3 个相邻对里有 2 对相同 → 50%；若按桶数则是 40%）
    v = [1.0, 1.0, 2.0, 3.0, 3.0]
    adj = repeat_pct(v)
    over_n = 100.0 * sum(1 for a, b in zip(v, v[1:]) if a == b) / len(v)
    # ② close vs mid：close 是成交价，mid 是盘口中点
    close_only = "close"
    # ③ 阈值对 bar size 的敏感性：用实测数据说明
    return {
        "denominator_is_adjacent_pairs": abs(adj - 50.0) < 1e-9,
        "value_adjacent_denominator": adj,
        "value_bucket_denominator_would_be": over_n,
        "price_used": close_only,
        "bar_size_sensitivity_observed": {
            "note": "同一标的重复率随 bar 变粗而下降：EWY LTR 5m=56.2%（§3.0）→ 1h=5.3%（matrix.json）",
            "soxl_5m_LT": 1.57, "soxl_5m_HL": 0.17,
            "mrvl_5m_LT": 8.35, "mrvl_5m_HL": 1.91,
            "ewy_5m_LT": 56.35, "ewy_5m_HL": 2.26,
        },
        "recommendation": (
            "阈值 10% 只有在**固定 bar size**（建议 1m 或 5m）且**分母=相邻桶对数**时才有意义；"
            "现在 build_matrix 用 1h、screen_candidates 用 5m，同一阈值在两处含义不同，"
            "EWY 在 5m 下 56%（STALE）、在 1h 下 5.3%（干净）——同一对在同一份数据里被两套阈值判成相反结论。"
            "建议：(1) 统一到 5m；(2) 阈值取 5%（当前 1h 口径漏掉了 EWY 这类 5m 下 50%+ 的腿）；"
            "(3) 更强的判据是「捕获在只保留两侧都成交的桶上是否存活」，重复收盘率只当预警、不当否决。"),
    }


# =============================================================================
# Q4  事件级回放复算
# =============================================================================
def _fetch_candles(coin: str, mid: int, resolution: str = "5m", days: int = 2) -> dict:
    step = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}[resolution]
    now = int(time.time() * 1000)
    d = _http(HL, {"type": "candleSnapshot",
                   "req": {"coin": coin, "interval": resolution,
                           "startTime": now - days * 86400 * 1000, "endTime": now}})
    h = {int(c["t"]) // step * step: {"o": float(c["o"]), "c": float(c["c"]), "v": float(c.get("v", 0))}
         for c in d if isinstance(c, dict)}
    out: dict[int, dict] = {}
    cur = int(time.time()) - days * 86400
    end_now = int(time.time())
    step_s = step // 1000
    while cur < end_now:
        end = min(cur + 400 * step_s, end_now)
        j = _http(f"{LT}/candles?market_id={mid}&resolution={resolution}"
                  f"&start_timestamp={cur}&end_timestamp={end}&count_back=2")
        for c in j.get("c", []):
            out[int(c["t"]) // step * step] = {"o": float(c["o"]), "c": float(c["c"]),
                                              "v": float(c.get("v", 0))}
        if end <= cur:
            break
        cur = end
    return {"h": {str(k): v for k, v in h.items()}, "l": {str(k): v for k, v in out.items()}}


def _load_candles(name: str, coin: str, mid: int, resolution: str = "5m",
                  days: int = 2, offline: bool = False) -> dict | None:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{resolution}_{days}d_{name}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    if offline:
        return None
    try:
        data = _fetch_candles(coin, mid, resolution, days)
        json.dump(data, open(path, "w", encoding="utf-8"))
        return data
    except Exception:  # noqa: BLE001
        return None


def fade_replay(h: dict, l: dict, W: int = 48, entry_z: float = 1.5, exit_z: float = 0.3,
                hold: int = 288) -> dict:
    """复刻 `scripts/screen_candidates.py` 的 fade 回放，并加同价口径变体。"""
    ts = sorted(set(h) & set(l))
    basis_c = [(l[t]["c"] / h[t]["c"] - 1) * 1e4 for t in ts]
    basis_o = [(l[t]["o"] / h[t]["o"] - 1) * 1e4 for t in ts]
    n = len(ts)
    events = []
    for i in range(W, n - 1):
        win = basis_c[i - W:i]
        med = statistics.median(win)
        mad = statistics.median(abs(x - med) for x in win)
        sig = max(1.4826 * mad, 1e-9)
        z = (basis_c[i] - med) / sig
        if abs(z) < entry_z:
            continue
        side = -1 if z > 0 else 1
        ex = None
        for j in range(i + 1, min(i + hold, n)):
            if abs((basis_c[j] - med) / sig) <= exit_z:
                ex = j
                break
        events.append({"i": i, "side": side, "exit": ex, "z": z})

    def collect(mode: str) -> dict:
        caps, timeouts = [], 0
        for e in events:
            i, side, j = e["i"], e["side"], e["exit"]
            if j is None:
                timeouts += 1
                if mode == "as_implemented":
                    continue
                j = min(i + hold, n - 1)
            if mode in ("as_implemented", "timeout_loss"):
                cap = (basis_c[j] - basis_o[i + 1]) * side
            elif mode == "open_open":
                cap = (basis_o[min(j + 1, n - 1)] - basis_o[i + 1]) * side
            elif mode == "close_close":
                cap = (basis_c[j] - basis_c[i + 1]) * side
            else:
                raise ValueError(mode)
            caps.append(cap)
        if not caps:
            return {"n": 0}
        wins = sum(1 for x in caps if x > 0)
        return {"n": len(caps), "timeouts": timeouts,
                "mean": statistics.mean(caps), "median": statistics.median(caps),
                "win_pct": 100 * wins / len(caps),
                "p10": sorted(caps)[int(0.1 * len(caps))], "worst": min(caps)}

    return {"n_bars": n, "n_entries": len(events),
            "variants": {m: collect(m) for m in ("as_implemented", "timeout_loss",
                                                "open_open", "close_close")}}


def round_cost_bps(*, leader_maker_bps: float, leader_taker_bps: float,
                   follower_taker_bps: float, follower_maker_bps: float = 0.0,
                   hs_leader_bps: float, hs_follower_bps: float,
                   slip_leader_bps: float = 0.0, slip_follower_bps: float = 0.0,
                   carry_leader_bps_h: float = 0.0, carry_follower_bps_h: float = 0.0,
                   hold_h: float = 1.0, leg_risk_bps: float = 0.3,
                   recon_bps: float = 0.5) -> dict:
    """一轮（开仓 + 平仓）的完整成本账。

    leader  = 被动腿（maker 入场，taker 平仓）
    follower= 主动腿（taker 入场，taker 平仓）
    """
    fees = leader_maker_bps + leader_taker_bps + 2 * follower_taker_bps
    spread = hs_leader_bps / 2 + hs_follower_bps          # 平仓穿 leader 价差 + follower 进出两次半价差
    slip = slip_leader_bps + 2 * slip_follower_bps
    carry = (carry_leader_bps_h + carry_follower_bps_h) * hold_h
    total = fees + spread + slip + carry + leg_risk_bps + recon_bps
    return {"fees": fees, "spread": spread, "slip": slip, "carry": carry,
            "leg_risk": leg_risk_bps, "recon": recon_bps, "total": total}


# =============================================================================
# Q4 实时成本（盘口/资金费）与「真实净 EV 区间」
# =============================================================================
def _tick_from_levels(levels: list[tuple[float, float]]) -> float:
    px = sorted({p for p, _ in levels})
    ds = [round(y - x, 10) for x, y in zip(px, px[1:]) if y > x]
    return min(ds) if ds else 0.01


def live_costs(coin: str, market: int, dex: str = "xyz", offline: bool = False) -> dict:
    """从公开端点取实时盘口/资金费，算「仓库口径 b*」与「完整一轮成本账」。"""
    if offline:
        return {"error": "offline"}
    try:
        hb = _http(HL, {"type": "l2Book", "coin": coin})
        h_bids = [(float(x["px"]), float(x["sz"])) for x in hb["levels"][0]]
        h_asks = [(float(x["px"]), float(x["sz"])) for x in hb["levels"][1]]
        h_mid = (h_bids[0][0] + h_asks[0][0]) / 2
        hs_hl = (h_asks[0][0] - h_bids[0][0]) / h_mid * 1e4
        tick_hl = _tick_from_levels(h_bids + h_asks)

        lb = _http(f"{LT}/orderBookOrders?market_id={market}&limit=100")
        l_bids = sorted(((float(x["price"]), float(x["remaining_base_amount"]))
                         for x in lb.get("bids", [])), reverse=True)
        l_asks = sorted(((float(x["price"]), float(x["remaining_base_amount"]))
                         for x in lb.get("asks", [])))
        l_mid = (l_bids[0][0] + l_asks[0][0]) / 2
        hs_lt = (l_asks[0][0] - l_bids[0][0]) / l_mid * 1e4
        tick_lt = _tick_from_levels([(p, 0) for p, _ in l_bids + l_asks])

        mctx = _http(HL, {"type": "metaAndAssetCtxs", "dex": dex})
        idx = next(i for i, u in enumerate(mctx[0]["universe"]) if u["name"] == coin)
        uni = mctx[0]["universe"][idx]
        ctx = mctx[1][idx]
        funding_bps_h = float(ctx.get("funding") or 0) * 1e4
        scale = float(uni.get("deployerFeeScale", "nan"))
        growth = uni.get("growthMode") == "enabled"
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    slip_hl = 2 * tick_hl / h_mid * 1e4
    slip_lt = 2 * tick_lt / l_mid * 1e4
    repo = round_cost_bps(leader_maker_bps=1.5, leader_taker_bps=4.5, follower_taker_bps=0.0,
                          hs_leader_bps=hs_hl, hs_follower_bps=hs_lt,
                          slip_leader_bps=slip_hl, slip_follower_bps=slip_lt,
                          carry_leader_bps_h=funding_bps_h, carry_follower_bps_h=0.0,
                          hold_h=1.0, leg_risk_bps=0.3, recon_bps=0.5)
    off = hl_fee_formula(1.5, 4.5, scale, growth)
    full = round_cost_bps(leader_maker_bps=off["maker_bps"], leader_taker_bps=off["taker_bps"],
                          follower_taker_bps=0.0,
                          hs_leader_bps=hs_hl, hs_follower_bps=hs_lt,
                          slip_leader_bps=slip_hl, slip_follower_bps=slip_lt,
                          carry_leader_bps_h=funding_bps_h, carry_follower_bps_h=0.0,
                          hold_h=1.0, leg_risk_bps=0.3, recon_bps=0.5)
    # 仓库 `screen_candidates.py` 的 b* 口径（HL 6.0 + 两腿半价差 + 两腿 2tick + 0.5 carry + 0.3 + 0.5）
    repo_screen = 6.0 + hs_hl / 2 + hs_lt / 2 + slip_hl + slip_lt + 0.5 + 0.3 + 0.5
    return {"hl_spread_bps": hs_hl, "lt_spread_bps": hs_lt,
            "hl_tick": tick_hl, "lt_tick": tick_lt,
            "slip_hl_2tick_bps": slip_hl, "slip_lt_2tick_bps": slip_lt,
            "hl_funding_bps_h": funding_bps_h,
            "deployer_fee_scale": scale, "growth_mode": growth,
            "hl_effective_maker_bps": off["maker_bps"], "hl_effective_taker_bps": off["taker_bps"],
            "repo_screen_bstar_bps": repo_screen,
            "full_round_cost_repo_fees": repo, "full_round_cost_official_fees": full}


def ev_bands(capture_impl: float, capture_open_open: float, capture_close_close: float,
             b_star_repo: float, b_star_full: float, p_fill: float = 0.5) -> dict:
    """三档真实净 EV（bps/轮）。

    保守：捕获按「同价口径」（close→close，扣掉开盘价不可得的部分），
          成本按完整一轮 + 仓库（高）费率，p_fill 打折。
    中性：捕获按「开→开」（无前视、两腿同刻定价），成本按完整一轮 + 官方 HIP-3 费率，
          p_fill=0.5。
    乐观：捕获按仓库原口径（开→收），成本按仓库 b*，p_fill=1。
    """
    bands = {
        "conservative": {
            "capture_bps": capture_close_close,
            "cost_bps": b_star_full,
            "p_fill": p_fill * 0.6,
        },
        "neutral": {
            "capture_bps": capture_open_open,
            "cost_bps": b_star_full,
            "p_fill": p_fill,
        },
        "optimistic": {
            "capture_bps": capture_impl,
            "cost_bps": b_star_repo,
            "p_fill": 1.0,
        },
    }
    for b in bands.values():
        b["net_bps"] = b["p_fill"] * (b["capture_bps"] - b["cost_bps"])
    return bands


def headline_decomposition(capture: float, b_star: float, costs: dict) -> dict:
    """把报告里的 headline（capture − b*）逐项拆开：补上漏计的成本、修正多计的成本。

    用于回答「+5.3 bps/轮 在扣掉哪些还没扣的成本之后还剩多少」。
    """
    hs_lt = costs["lt_spread_bps"]
    slip_lt = costs["slip_lt_2tick_bps"]
    carry_live = costs["hl_funding_bps_h"]
    fee_repo = 6.0
    fee_real = costs["hl_effective_maker_bps"] + costs["hl_effective_taker_bps"]
    lr_const = 0.3
    lr_30s = float(leg_risk_bps(D("12.9"), D("30"), D("0.30")))
    items = [
        ("报告 headline（capture − b*）", capture - b_star),
        ("补：follower 平仓再穿一次价差（半价差）", -hs_lt / 2),
        ("补：follower 平仓的按规模滑点（2 tick）", -slip_lt),
        ("修正：carry 0.5 → 实测（成本降 → 净升）", 0.5 - carry_live),
        ("修正：HL 费率 6.0 → 官方 HIP-3 实际（成本降 → 净升）", fee_repo - fee_real),
        ("修正：腿风险 0.3 → t_leg=30s / P=0.30（成本升 → 净降）", lr_const - lr_30s),
    ]
    total = sum(v for _, v in items)
    return {"items": items, "net_at_p1": total,
            "net_at_p05": 0.5 * total, "net_at_p03": 0.3 * total,
            "note": "capture 口径若换成「两腿同刻定价」（收→收），capture ≈ 0 → 三档全部转负"}


def analyse_quote_samples(symbol: str = "SOXL") -> dict:
    """分析 `scripts/recheck_quote_sample.py` 的高频采样：盘口基差 vs 成交价基差。"""
    path = os.path.join(CACHE, f"quote_samples_{symbol}.jsonl")
    if not os.path.exists(path):
        return {"error": f"no samples at {path}"}
    rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    rows = [r for r in rows if "error" not in r]
    if len(rows) < 10:
        return {"error": "too few samples"}
    gap = [r["basis_print_bps"] - r["basis_book_bps"] for r in rows]
    out = {
        "n": len(rows),
        "span_s": rows[-1]["ts"] - rows[0]["ts"],
        "basis_book_mean_bps": statistics.mean(r["basis_book_bps"] for r in rows),
        "basis_book_sd_bps": statistics.pstdev(r["basis_book_bps"] for r in rows),
        "basis_print_mean_bps": statistics.mean(r["basis_print_bps"] for r in rows),
        "basis_print_sd_bps": statistics.pstdev(r["basis_print_bps"] for r in rows),
        "print_minus_book_mean_bps": statistics.mean(gap),
        "print_minus_book_median_bps": statistics.median(gap),
        "print_minus_book_sd_bps": statistics.pstdev(gap),
        "hl_mid_minus_print_mean_bps": statistics.mean(r["hl_mid_minus_print_bps"] for r in rows),
        "lt_mid_minus_print_mean_bps": statistics.mean(r["lt_mid_minus_print_bps"] for r in rows),
        "hl_print_age_median_s": statistics.median(r["h_print_age_s"] for r in rows),
        "lt_print_age_median_s": statistics.median(r["l_print_age_s"] for r in rows),
        "half_spread_sum_mean_bps": statistics.mean(
            (r["h_spread_bps"] + r["l_spread_bps"]) / 2 for r in rows),
    }
    # 同价口径的回归幅度：偏离滚动中位数 1.5σ 之后，H 秒内的回归
    for label, key in (("book", "basis_book_bps"), ("print", "basis_print_bps")):
        series = [r[key] for r in rows]
        rev = []
        for i in range(20, len(series) - 1):
            win = series[i - 20:i]
            med = statistics.median(win)
            mad = statistics.median(abs(x - med) for x in win)
            sig = max(1.4826 * mad, 1e-9)
            z = (series[i] - med) / sig
            if abs(z) < 1.5:
                continue
            side = -1 if z > 0 else 1
            fut = series[i + 1:min(i + 21, len(series))]
            if not fut:
                continue
            rev.append((statistics.mean(fut) - series[i]) * side)
        out[f"{label}_reversion_1sigma_bps"] = statistics.mean(rev) if rev else None
        out[f"{label}_reversion_n"] = len(rev)

    # 同一条规则、同一段样本，只换价格来源（盘口中点 vs 成交打印）→ 捕获差多少
    for label, key in (("book", "basis_book_bps"), ("print", "basis_print_bps")):
        series = [r[key] for r in rows]
        caps = []
        for i in range(20, len(series) - 1):
            win = series[i - 20:i]
            med = statistics.median(win)
            mad = statistics.median(abs(x - med) for x in win)
            sig = max(1.4826 * mad, 1e-9)
            z = (series[i] - med) / sig
            if abs(z) < 1.5:
                continue
            side = -1 if z > 0 else 1
            for j in range(i + 1, min(i + 21, len(series))):
                if abs((series[j] - med) / sig) <= 0.3:
                    caps.append((series[j] - series[i]) * side)
                    break
        out[f"{label}_replay_capture_bps"] = statistics.mean(caps) if caps else None
        out[f"{label}_replay_capture_n"] = len(caps)
        out[f"{label}_replay_win_pct"] = (100 * sum(1 for c in caps if c > 0) / len(caps)) if caps else None
    return out


#: `docs/MATRIX_SUMMARY.md` §3.0 的最终裁决（已发布的数字，复核基准）
PUBLISHED = {
    "SOXL": {"events": 115, "b_star": 11.6, "capture": 16.9, "net": 5.3, "win_pct": 97,
             "rth_net": -0.2, "closed_net": 10.3, "rep_hl": 0.2, "rep_lt": 1.6},
    "MRVL": {"events": 119, "b_star": 14.7, "capture": 16.2, "net": 1.5, "win_pct": 90,
             "rth_net": 7.8, "closed_net": -3.0, "rep_hl": 1.7, "rep_lt": 8.2},
}


def q4_replay_and_ev(offline: bool = False) -> dict:
    cands = [("SOXL", "xyz:SOXL", 197), ("MRVL", "xyz:MRVL", 174),
             ("EWY", "xyz:EWY", 166), ("RKLB", "xyz:RKLB", 186), ("BABA", "xyz:BABA", 177)]
    out: dict[str, Any] = {}
    for name, coin, mid in cands:
        data = _load_candles(name, coin, mid, "5m", 2, offline)
        if not data:
            out[name] = {"error": "no candles (offline & no cache)"}
            continue
        h = {int(k): v for k, v in data["h"].items()}
        l = {int(k): v for k, v in data["l"].items()}
        rec = fade_replay(h, l)
        costs = live_costs(coin, mid, dex=coin.split(":")[0], offline=offline)
        rec["costs"] = costs
        rec["published"] = PUBLISHED.get(name)
        if "error" not in costs:
            v = rec["variants"]
            rec["ev_bands"] = ev_bands(
                capture_impl=v["as_implemented"]["mean"],
                capture_open_open=v["open_open"]["mean"],
                capture_close_close=v["close_close"]["mean"],
                b_star_repo=costs["repo_screen_bstar_bps"],
                b_star_full=costs["full_round_cost_official_fees"]["total"])
            pub = PUBLISHED.get(name)
            if pub:
                hd = headline_decomposition(pub["capture"], pub["b_star"], costs)
                cc = v["close_close"]["mean"]
                hd["alt_contemporaneous_capture"] = {
                    "capture_bps": cc,
                    "net_at_p1": cc - costs["full_round_cost_official_fees"]["total"],
                    "net_at_p05": 0.5 * (cc - costs["full_round_cost_official_fees"]["total"]),
                }
                rec["headline_decomposition"] = hd
        out[name] = rec
        time.sleep(0.3)
    return out


# =============================================================================
# 汇总
# =============================================================================
def build_report(offline: bool = False, quick: bool = False) -> dict:
    rep: dict[str, Any] = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "offline": offline,
        "q1_ev_formula": q1_ev_formula_audit(),
        "q1_leg_risk_table": leg_risk_table(),
        "q1_leg_risk_verdict": q1_leg_risk_verdict(),
        "q2_fees": q2_fee_audit(offline=offline),
        "q3_gates": q3_gate_audit(),
        "q3_repeat_close": q3_repeat_close_semantics(),
        "q4_replay": q4_replay_and_ev(offline=offline),
        "q4_quote_samples": analyse_quote_samples("SOXL"),
    }
    return rep


def print_report(rep: dict) -> None:
    print("=" * 78)
    print(f"独立复核报告  {rep['generated_utc']}  offline={rep['offline']}")
    print("=" * 78)

    print("\n## Q1 EV 口径")
    for f in rep["q1_ev_formula"]["findings"]:
        print("  - " + f)
    print("\n  腿风险量级表  √(2/π)·σ_1m·√(t/60)·P_1leg·α  (α=0.5)")
    print(f"    {'σ_1m':>8s}{'t_leg':>8s}{'P_1leg':>8s}{'leg_risk_bps':>14s}")
    for r in rep["q1_leg_risk_table"]:
        print(f"    {r['sigma_1m']:>8s}{r['t_leg_s']:>8s}{r['p_1leg']:>8s}{r['leg_risk_bps']:>14.4f}")
    v = rep["q1_leg_risk_verdict"]
    print(f"  → {v['verdict']}")

    print("\n## Q2 费率对拍")
    q2 = rep["q2_fees"]
    print("  HL（官方 feeRates 公式：hip3Scale = scale<1 ? scale+1 : scale*2；growthMode ×0.1）")
    for r in q2.get("hl", []):
        if "error" in r:
            print(f"    {r['asset']:12s} ERROR {r['error']}")
        else:
            print(f"    {r['asset']:12s} scale={r['deployer_fee_scale']:<5} growth={str(r['growth_mode']):5s}"
                  f" → maker {r['effective_maker_bps']:.3f} bps / taker {r['effective_taker_bps']:.3f} bps"
                  f" (仓库假设 {r['repo_assumed_maker_bps']}/{r['repo_assumed_taker_bps']}，"
                  f"一轮 {r['effective_round_bps']:.2f} vs 6.0)")
    print("  Lighter")
    for r in q2.get("lighter", []):
        if "error" in r:
            print(f"    ERROR {r['error']}")
        else:
            print(f"    {r['market']:8s} maker {r['maker_bps']:.2f} bps / taker {r['taker_bps']:.2f} bps"
                  f" (enabled={r['is_maker_fee_enabled']}/{r['is_taker_fee_enabled']})")
    okx = q2.get("okx", {})
    print(f"  OKX: 公开 instruments 携带费率字段 = {okx.get('fee_fields_present')}；"
          f"{okx.get('note', '')}")

    print("\n## Q3 五道闸门")
    g = rep["q3_gates"]
    if "error" in g:
        print("  " + g["error"])
    else:
        print(f"  样本 {g['n_pairs']} 对；各闸门命中数：{g['gate_counts']}")
        print(f"  过成本闸后被各闸门挡下：{g['per_gate_blocked_after_edge']}")
        print(f"  build_matrix.py 的 tradable 判定式：{g['tradable_line_in_build_matrix']}")
        print(f"  build_matrix 里是否存在事件级 EV 闸门 = {g['build_matrix_has_event_ev_gate']}")
        print(f"  screen_candidates.py 是否有 tradable 布尔 = {g['screen_candidates_has_tradable_bool']}")
        print(f"  basis 是否除以 ratio = {g['ratio_applied_to_basis']}  ({g['basis_lines']})")
        print("  重复收盘率实现：")
        for ln in g["repeat_close_source_lines"]:
            print(f"    {ln}")
        print("  ratio 偏移泄漏（basis 均值 == (ratio_mean−1)·1e4）：")
        print(f"    {'pair':30s}{'offset_bps':>12s}{'basis_mean':>12s}{'basis_sd':>10s}{'p95':>10s}{'trad':>6s}")
        for r in g["ratio_offset_leak_top"][:8]:
            print(f"    {r['pair']:30s}{r['ratio_offset_bps']:>12.1f}{r['basis_mean_bps']:>12.1f}"
                  f"{(r['basis_sd_bps'] or 0):>10.1f}{(r['p95'] or 0):>10.1f}{str(r['tradable']):>6s}")
    rc = rep["q3_repeat_close"]
    print(f"  repeat_close 分母=相邻桶对：{rc['denominator_is_adjacent_pairs']}"
          f"（相邻分母={rc['value_adjacent_denominator']}%，桶分母={rc['value_bucket_denominator_would_be']:.1f}%）")
    print(f"  建议：{rc['recommendation']}")

    print("\n## Q4 事件级回放复算（5m，2 天）")
    print(f"    {'sym':8s}{'bars':>6s}{'entries':>8s} | {'as_impl':>9s}{'win%':>6s} | "
          f"{'open→open':>10s}{'win%':>6s} | {'close→close':>12s}{'win%':>6s}")
    for name, r in rep["q4_replay"].items():
        if "error" in r:
            print(f"    {name:8s} {r['error']}")
            continue
        v = r["variants"]
        print(f"    {name:8s}{r['n_bars']:>6d}{r['n_entries']:>8d} | "
              f"{v['as_implemented']['mean']:>9.2f}{v['as_implemented']['win_pct']:>6.1f} | "
              f"{v['open_open']['mean']:>10.2f}{v['open_open']['win_pct']:>6.1f} | "
              f"{v['close_close']['mean']:>12.2f}{v['close_close']['win_pct']:>6.1f}")

    print("\n## Q4 成本账与真实净 EV 区间（bps/轮）")
    for name, r in rep["q4_replay"].items():
        if "error" in r or "ev_bands" not in r:
            continue
        c = r["costs"]
        b = r["ev_bands"]
        print(f"  {name}: HL 价差 {c['hl_spread_bps']:.2f} / LT 价差 {c['lt_spread_bps']:.2f} bps；"
              f"HL 资金费 {c['hl_funding_bps_h']:.4f} bps/h；"
              f"HL 实际费率 {c['hl_effective_maker_bps']:.2f}/{c['hl_effective_taker_bps']:.2f} bps")
        print(f"    仓库 screen b* = {c['repo_screen_bstar_bps']:.2f} bps；"
              f"完整一轮（官方费率）= {c['full_round_cost_official_fees']['total']:.2f} bps "
              f"{ {k: round(vv, 2) for k, vv in c['full_round_cost_official_fees'].items()} }")
        print(f"    保守 {b['conservative']['net_bps']:+.2f} | 中性 {b['neutral']['net_bps']:+.2f} | "
              f"乐观 {b['optimistic']['net_bps']:+.2f}")
        hd = r.get("headline_decomposition")
        if hd:
            print(f"    headline 拆解（{name}，docs/MATRIX_SUMMARY.md §3.0）：")
            for label, val in hd["items"]:
                print(f"      {label:42s}{val:+8.2f}")
            print(f"      → 补齐后 p_fill=1: {hd['net_at_p1']:+.2f} | p_fill=0.5: {hd['net_at_p05']:+.2f} "
                  f"| p_fill=0.3: {hd['net_at_p03']:+.2f}")
            alt = hd.get("alt_contemporaneous_capture")
            if alt:
                print(f"      → 若捕获改用「两腿同刻定价」capture={alt['capture_bps']:+.2f}："
                      f"p_fill=1 {alt['net_at_p1']:+.2f} / p_fill=0.5 {alt['net_at_p05']:+.2f}")

    qs = rep.get("q4_quote_samples", {})
    print("\n## Q4 高频采样：盘口基差 vs 成交价基差")
    if "error" in qs:
        print("  " + str(qs["error"]))
    else:
        print(f"  样本 {qs['n']}（{qs['span_s']:.0f}s）")
        print(f"    盘口基差 mean {qs['basis_book_mean_bps']:+.2f} / sd {qs['basis_book_sd_bps']:.2f} bps")
        print(f"    成交基差 mean {qs['basis_print_mean_bps']:+.2f} / sd {qs['basis_print_sd_bps']:.2f} bps")
        print(f"    成交−盘口 = {qs['print_minus_book_mean_bps']:+.2f} bps "
              f"(median {qs['print_minus_book_median_bps']:+.2f}, sd {qs['print_minus_book_sd_bps']:.2f})")
        print(f"    打印年龄中位数：HL {qs['hl_print_age_median_s']:.1f}s / LT {qs['lt_print_age_median_s']:.1f}s")
        print(f"    1.5σ 偏离后的 60s 回归幅度：盘口 {qs.get('book_reversion_1sigma_bps')} bps "
              f"(n={qs.get('book_reversion_n')}) / 成交价 {qs.get('print_reversion_1sigma_bps')} bps "
              f"(n={qs.get('print_reversion_n')})")


def main(argv: list[str]) -> int:
    offline = "--offline" in argv
    quick = "--quick" in argv
    rep = build_report(offline=offline, quick=quick)
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, "recheck.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1, default=str)
    print_report(rep)
    print(f"\n[写出] {os.path.join(CACHE, 'recheck.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
