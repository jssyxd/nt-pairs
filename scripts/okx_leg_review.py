#!/usr/bin/env python3
"""把 `chd_screen` 的结果渲染成 **OKX 腿复核报告**（`docs/OKX_LEG_REVIEW.md`）。

回答三个问题：
1. 多一条 OKX 腿之后，主推标的（pair）变不变？
2. 「OKX 只能做兜底 taker」这条结论，在有 OKX 历史之后**还成立吗**？
3. OKX 腿最终该当 maker 还是 taker、在什么名义档以下才有意义？

输入是 `scripts/chd_screen.py` 写出的 `data/chd_screen.json`（同一窗口、同一参数）。

用法：
    python scripts/okx_leg_review.py
    python scripts/okx_leg_review.py --screen data/chd_screen.json --out docs/OKX_LEG_REVIEW.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

VENUE_LABEL = {
    "hyperliquid_futures": "HL",
    "lighter": "Lighter",
    "okx_futures": "OKX",
    "binance_futures": "Binance",
}


def _label(leg: str) -> str:
    venue, _, sym = leg.partition(":")
    return f"{VENUE_LABEL.get(venue, venue)}:{sym}"


def _sess(row: dict[str, Any], sess: str = "rth") -> dict[str, Any]:
    rep = row.get("replay") or {}
    return ((rep.get("by_session") or {}).get(sess) or {})


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (payload.get("stage_b") or []) if not r.get("error")]


def _sorted_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按 RTH 净 EV/事件排序（从高到低）。"""
    def key(r: dict[str, Any]):
        s = _sess(r)
        return -(s.get("net_bps_per_event") or -1e9)
    return sorted(_rows(payload), key=key)


def render(payload: dict[str, Any]) -> str:
    rows = _sorted_rows(payload)
    cfg = payload.get("config") or {}
    win = payload.get("window") or {}
    meta = payload.get("okx_instruments") or {}
    L: list[str] = []
    L.append("# OKX 腿复核：Lighter↔OKX / HL↔Lighter / HL↔OKX 同一窗口对照\n")
    L.append(f"生成时间：{payload.get('generated_at')}　"
             f"Stage A 窗口：{win.get('start')} → {win.get('end')}　"
             f"回放窗口：{win.get('replay_hours')} 小时　"
             f"单笔名义：${float(cfg.get('qty_usd') or 0):.0f}/腿\n")
    if meta.get("note"):
        L.append(f"数量单位换算：{meta['note']}；乘子 "
                 f"{', '.join(f'{k}×{v}' for k, v in (meta.get('contract_multipliers') or {}).items())}"
                 f"（OKX 永续数量单位是「张」，乘 `ctVal` 才是标的单位）。\n")

    L.append("## 1. 三组对照（RTH 会话口径）\n")
    L.append("| 对 | 腿角色（maker / taker） | b* bps | 费率 | 点差 | 滑点 | 腿风险 | 对账 | "
             "p95覆盖倍数 | 毛捕获 bps | 净EV/事件 bps | 胜率 | mean p_fill | 日均机会 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        st = r.get("stage_a") or {}
        bd = r.get("b_star_breakdown") or {}
        s = _sess(r)
        roles = r.get("b_star_roles") or {}
        L.append(
            f"| {r['key']} | {_label(roles.get('maker', ''))} / {_label(roles.get('taker', ''))} | "
            f"{r.get('b_star_bps') or 0:.2f} | {bd.get('fee_bps', 0):.2f} | {bd.get('spread_bps', 0):.2f} | "
            f"{bd.get('slippage_bps', 0):.2f} | {bd.get('leg_risk_bps', 0):.2f} | {bd.get('recon_bps', 0):.2f} | "
            f"{(r.get('coverage_ratio') or 0):.2f} | "
            f"{s.get('gross_capture_bps_per_event') or 0:.2f} | "
            f"{s.get('net_bps_per_event') or 0:+.2f} | {s.get('win_rate') or 0:.0%} | "
            f"{s.get('mean_p_fill') or 0:.3f} | {s.get('opportunities_per_day') or 0:.1f} |")
    L.append("")

    L.append("## 2. 分会话明细（净 EV/事件 bps）\n")
    L.append("| 对 | " + " | ".join(s for s in ("rth", "pre", "post", "closed")) + " |")
    L.append("|---|" + "---:|" * 4)
    for r in rows:
        rep = r.get("replay") or {}
        by_sess = rep.get("by_session") or {}
        cells = []
        for s in ("rth", "pre", "post", "closed"):
            v = by_sess.get(s)
            cells.append(f"{v['net_bps_per_event']:+.2f} ({v['events']} 事件)" if v else "—")
        L.append(f"| {r['key']} | " + " | ".join(cells) + " |")
    L.append("")

    L.append("## 3. 逐交易日（回放窗口只覆盖一天时这里是单日）\n")
    L.append("| 对 | 交易日 | 事件数 | 胜率 | 净 EV/事件 bps | 毛捕获 bps | mean p_fill |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for r in rows:
        rep = r.get("replay") or {}
        by_day = {k: v for k, v in (rep.get("by_day") or {}).items() if not k.startswith("_")}
        for day, v in by_day.items():
            L.append(f"| {r['key']} | {day} | {v['events']} | {v['win_rate']:.0%} | "
                     f"{v['net_bps_per_event']:+.2f} | {v['gross_capture_bps_per_event']:.2f} | "
                     f"{v['mean_p_fill']:.3f} |")
    L.append("")

    L.append("## 4. 闸门与盘口\n")
    L.append("| 对 | 闸门①单位 | 闸门②新鲜 | p95\\|b\\| bps | k | 闸门③覆盖 | 闸门④容量 | 闸门⑤净EV | "
             "最小深度 US$ | 点差 a/b bps | 滑点 a/b bps |")
    L.append("|---|:--:|:--:|---:|---:|:--:|:--:|:--:|---:|---|---|")
    for r in rows:
        st = r.get("stage_a") or {}
        bk = r.get("book") or {}
        a, b = bk.get("a") or {}, bk.get("b") or {}
        L.append(f"| {r['key']} | {'✅' if st.get('gate1_unit') else '❌'} | "
                 f"{'✅' if st.get('gate2_fresh') else '❌'} | "
                 f"{st.get('basis_p95_dev') or 0:.2f} | {st.get('k') or 0:.3f} | "
                 f"{'✅' if r.get('gate3_cost_cover') else '❌'} | "
                 f"{'✅' if r.get('gate4_capacity') else '❌'} | "
                 f"{'✅' if r.get('gate5_net_ev') else '❌'} | "
                 f"{r.get('depth_min_usd') or 0:,.0f} | "
                 f"{a.get('spread_bps', 0):.2f} / {b.get('spread_bps', 0):.2f} | "
                 f"{a.get('slip_bps', 0):.2f} / {b.get('slip_bps', 0):.2f} |")
    L.append("")

    L.append("## 5. p_fill 情景（窗口内净 bps 合计）\n")
    L.append("| 对 | 保守(0.5×) | 中性(1×) | 乐观(1.5×) |")
    L.append("|---|---:|---:|---:|")
    for r in rows:
        sc = (r.get("replay") or {}).get("pfill_scenarios") or {}
        if not sc:
            continue
        L.append(f"| {r['key']} | {sc.get('conservative', {}).get('total_net_bps', 0):.1f} | "
                 f"{sc.get('neutral', {}).get('total_net_bps', 0):.1f} | "
                 f"{sc.get('optimistic', {}).get('total_net_bps', 0):.1f} |")
    L.append("")
    return "\n".join(L) + "\n"


def render_sweep(sweeps: Sequence[tuple[str, dict[str, Any]]]) -> str:
    """名义档敏感性：同一个窗口、同一参数，只改单笔名义。"""
    if not sweeps:
        return ""
    L: list[str] = []
    L.append("## 6. 名义档敏感性（同一窗口，只改单笔名义）\n")
    L.append("| 名义/腿 | 对 | b* bps | 毛捕获 bps | 净EV/事件 bps (RTH) | 胜率 | mean p_fill | 闸门③覆盖 |")
    L.append("|---:|---|---:|---:|---:|---:|---:|:--:|")
    for qty, payload in sweeps:
        for r in _sorted_rows(payload):
            s = _sess(r)
            L.append(f"| ${qty} | {r['key']} | {r.get('b_star_bps') or 0:.2f} | "
                     f"{s.get('gross_capture_bps_per_event') or 0:.2f} | "
                     f"{s.get('net_bps_per_event') or 0:+.2f} | {s.get('win_rate') or 0:.0%} | "
                     f"{s.get('mean_p_fill') or 0:.3f} | "
                     f"{'✅' if r.get('gate3_cost_cover') else '❌'} |")
    L.append("")
    return "\n".join(L)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OKX 腿复核报告")
    ap.add_argument("--screen", type=str, default="data/chd_screen.json")
    ap.add_argument("--sweep", action="append", default=[],
                    help="名义档扫描：'<qty>:<screen.json>'，可重复")
    ap.add_argument("--out", type=str, default="docs/OKX_LEG_REVIEW.md")
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.screen).read_text(encoding="utf-8"))
    md = render(payload)
    sweeps: list[tuple[str, dict[str, Any]]] = []
    for item in args.sweep:
        qty, _, path = item.partition(":")
        sweeps.append((qty, json.loads(Path(path).read_text(encoding="utf-8"))))
    if sweeps:
        md = md.replace("## 5. p_fill 情景",
                        render_sweep(sweeps) + "## 7. p_fill 情景", 1)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
