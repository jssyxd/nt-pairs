#!/usr/bin/env python
"""CI 侧汇报渲染器：把 VPS 推上来的最新报告 + 只读探针结果渲染成 GitHub job summary。

纯标准库。输入：
  * `--reports`   仓库里的 `reports/` 目录（VPS 侧 cron 用 deploy key 推上来的）；
  * `--matrix` / `--fill` / `--sim`   本次 CI 探针跑出来的 JSON（`build_matrix.py` /
    `fill_model.py` / `sim_soxl.py` 的 `--out-json`）。

输出：Markdown 打到 stdout（workflow 里重定向进 `$GITHUB_STEP_SUMMARY`）。

退出码：
  * 0 —— summary 渲染成功（哪怕报告本身是「无数据」）；
  * 3 —— `reports/` 里没有可用报告 / 报告太旧（调用方据此开 issue）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

REPORT_NAME_RE = re.compile(r"^(\d{8})T(\d{6})Z\.md$")


def load_json(path: str | None) -> Any:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def report_stamp(path: str) -> datetime | None:
    """从文件名 `20261112T120000Z.md` 解析时间戳；不符合命名的一律忽略。"""
    base = os.path.basename(path)
    m = REPORT_NAME_RE.match(base)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def pick_latest(reports_dir: str) -> tuple[str | None, datetime | None]:
    if not os.path.isdir(reports_dir):
        return None, None
    cands: list[tuple[datetime, str]] = []
    for name in os.listdir(reports_dir):
        if not name.endswith(".md"):
            continue
        path = os.path.join(reports_dir, name)
        if not os.path.isfile(path):
            continue
        ts = report_stamp(path)
        if ts:
            cands.append((ts, path))
    if not cands:
        return None, None
    cands.sort()
    return cands[-1][1], cands[-1][0]


def sidecar(md_path: str) -> Any:
    return load_json(md_path[:-3] + ".json")


def fmt(v: Any, digits: int = 2, default: str = "n/a") -> str:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return f"{v:.{digits}f}"
    return str(v)


def esc(v: Any) -> str:
    return str(v).replace("|", "\\|")


def render_report_block(md_path: str, ts: datetime | None, side: Any) -> list[str]:
    L: list[str] = []
    L.append("## VPS 6 小时报告")
    L.append("")
    if not side:
        L.append(f"- 最新报告：`{md_path}`（生成 {ts.isoformat() if ts else 'n/a'}）")
        L.append("- ⚠️ 无机读旁挂 JSON，只显示文件头。")
        L.append("")
        return L
    logs = side.get("logs") or {}
    coll = side.get("collector") or {}
    mx = side.get("matrix") or {}
    sm = side.get("sim") or {}
    L.append(f"- 最新报告：`{md_path}`（生成 {side.get('generated_utc', 'n/a')}）")
    L.append(f"- 统计窗口：{side.get('window_hours', 'n/a')} 小时（自 {side.get('window_start', 'n/a')}）")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 采集样本（窗口内 / 累计） | {coll.get('window_samples', 0)} / {coll.get('total_samples', 0)} |")
    L.append(f"| 成交 / 未成交 | {logs.get('fills', 0)} / {logs.get('no_fills', 0)} |")
    L.append(f"| 风控事件 | {logs.get('risk_events', 0)} |")
    L.append(f"| 异常行 | {logs.get('errors', 0)} |")
    L.append(f"| 日志行数 | {logs.get('lines', 0)} |")
    if mx.get("available"):
        L.append(f"| 矩阵（可交易 / 总对数） | {mx.get('tradable', 0)} / {mx.get('pairs', 0)} |")
    if sm.get("available"):
        ov = sm.get("overall") or {}
        L.append(f"| 净 EV（事件级回放） | {fmt(ov.get('net_mean_bps'))} bps/轮 |")
    L.append("")

    venues = coll.get("venues") or {}
    if venues:
        L.append("| 场所 | 窗口样本 | 累计 | 重复收盘率(同mid未变) |")
        L.append("|---|---:|---:|---:|")
        for venue, v in venues.items():
            ratio = v.get("stale_ratio")
            L.append(f"| {venue} | {v.get('window_samples', 0)} | {v.get('samples', 0)} | "
                     f"{fmt(ratio * 100 if ratio is not None else None, 1)}% |")
        L.append("")

    sigs = side.get("error_signatures") or []
    if sigs:
        L.append("**异常摘要**")
        L.append("")
        for s in sigs[:5]:
            L.append(f"- ({s.get('count', 0)}×) `{s.get('text', '')}`")
        L.append("")

    findings = (logs.get("findings") or [])
    if findings:
        L.append("**新发现**")
        L.append("")
        for f in findings[:10]:
            L.append(f"- {f}")
        L.append("")
    return L


def render_probe_block(matrix: Any, fill: Any, sim: Any) -> list[str]:
    L: list[str] = []
    L.append("## 只读公开数据探针")
    L.append("")
    if matrix and isinstance(matrix.get("pairs"), list):
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
                "cap": cap.get("cap_usd"), "p95": win.get("p95"),
                "tradable": bool(p.get("tradable")),
            })
        rows.sort(key=lambda r: (not r["tradable"], r["edge"] is None, -(r["edge"] or 0)))
        L.append("| pair | 覆盖倍数 | b*(bps) | 30d p95(bps) | 深度容量($) | 可交易 |")
        L.append("|---|---:|---:|---:|---:|:--:|")
        for r in rows[:8]:
            L.append(f"| {esc(r['pair'])} | {fmt(r['edge'], 2)} | {fmt(r['b_star'], 1)} | "
                     f"{fmt(r['p95'], 1)} | {fmt(r['cap'], 0)} | {'✅' if r['tradable'] else '❌'} |")
        L.append("")
    else:
        L.append("- 矩阵探针无输出。")
        L.append("")

    if fill and isinstance(fill.get("pairs"), list):
        L.append("| 对 | 基差(bps) | p_fill@HL(60s) | p_fill@LIGHTER(60s) | 贴价队列 HL / LT |")
        L.append("|---|---:|---:|---:|---|")
        for p in fill["pairs"][:6]:
            if not isinstance(p, dict):
                continue
            sizes = p.get("sizes") or {}
            q = sizes.get("100") or next(iter(sizes.values()), {}) if sizes else {}
            queue = p.get("queue") or {}
            L.append(f"| {esc(p.get('sym', ''))} | {fmt(p.get('basis_bps'), 1)} | "
                     f"{fmt(q.get('p_fill_60s_on_HL'), 3)} | {fmt(q.get('p_fill_60s_on_LIGHTER'), 3)} | "
                     f"{fmt(queue.get('hl_touch_qty'), 0)} / {fmt(queue.get('lt_touch_qty'), 0)} |")
        L.append("")
        L.append("> p_fill 只是**代理**（单次贴价队列快照），不是实测；见 `docs/FILL_MODEL.md`。")
        L.append("")
    else:
        L.append("- 成交率探针无输出。")
        L.append("")

    if sim and sim.get("overall"):
        ov = sim["overall"]
        co = sim.get("costs") or {}
        cap = sim.get("capacity") or {}
        legs = sim.get("legs") or {}
        slip_hl = (legs.get("A_hyperliquid") or {}).get("slip_curve") or {}
        slip_lt = (legs.get("B_lighter") or {}).get("slip_curve") or {}
        L.append(f"- 回放对：`{sim.get('pair', 'n/a')}`，{ov.get('n', 'n/a')} 笔，"
                 f"胜率 {fmt(ov.get('win_rate'), 1)}%，中位持仓 {fmt(ov.get('median_hold_min'), 0)} 分钟")
        L.append(f"- 捕获 {fmt(ov.get('mean_capture_bps'), 1)} bps/轮，b\\* = {fmt(co.get('b_star'), 2)} bps，"
                 f"**净 EV = {fmt(ov.get('net_mean_bps'))} bps/轮**"
                 f"（p_fill 0.2/0.5/0.8 → {fmt(ov.get('exp_net_bps_pfill_0.2'))}/"
                 f"{fmt(ov.get('exp_net_bps_pfill_0.5'))}/{fmt(ov.get('exp_net_bps_pfill_0.8'))}）")
        L.append(f"- 滑点：HL {fmt(co.get('slip_hl'), 2)} bps，Lighter {fmt(co.get('slip_lt'), 2)} bps；"
                 f"容量上限 ${fmt(cap.get('cap_usd_10pct_of_depth25'), 0)}")
        if slip_hl or slip_lt:
            L.append("")
            L.append("| 名义($) | HL 滑点(bps) | Lighter 滑点(bps) |")
            L.append("|---:|---:|---:|")
            for tier in sorted(set(slip_hl) | set(slip_lt), key=lambda x: float(str(x))):
                a = slip_hl.get(tier) or {}
                b = slip_lt.get(tier) or {}
                L.append(f"| {tier} | {fmt(a.get('buy_slip_bps'), 2)} | {fmt(b.get('buy_slip_bps'), 2)} |")
        L.append("")
    else:
        L.append("- SOXL 执行模拟无输出。")
        L.append("")

    L.append("> 探针只用**公开端点**，不接触任何交易所密钥；净 EV 是模型输出，不是实盘。")
    L.append("")
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染 nt-pairs 6 小时汇报的 GitHub job summary")
    ap.add_argument("--reports", default="reports", help="VPS 推上来的报告目录")
    ap.add_argument("--matrix", default=None, help="build_matrix.py 的 JSON 输出")
    ap.add_argument("--fill", default=None, help="fill_model.py 的 JSON 输出")
    ap.add_argument("--sim", default=None, help="sim_soxl.py 的 JSON 输出")
    ap.add_argument("--max-age-hours", type=float, default=24.0,
                    help="报告最大允许年龄（小时）；超过视为失败")
    ap.add_argument("--out", default=None, help="输出文件；缺省打到 stdout")
    args = ap.parse_args(argv)

    md_path, ts = pick_latest(args.reports)
    md_path_disp = md_path.replace(os.sep, "/") if md_path else md_path
    now = datetime.now(timezone.utc)
    age_h = ((now - ts).total_seconds() / 3600.0) if ts else None
    if md_path is None:
        print(f"reports 目录 {args.reports} 里没有 .md 报告 —— VPS 侧 cron 未推送？", file=sys.stderr)
        return 3
    if age_h is not None and age_h > args.max_age_hours:
        print(f"最新报告 {md_path} 已 {age_h:.1f} 小时未更新（阈值 {args.max_age_hours} 小时）",
              file=sys.stderr)
        return 3

    L: list[str] = []
    L.append("# nt-pairs 6 小时汇报")
    L.append("")
    age_txt = f"；报告年龄 {age_h:.1f} 小时" if age_h is not None else ""
    L.append(f"- 渲染时间(UTC)：{now.strftime('%Y-%m-%dT%H:%M:%SZ')}{age_txt}")
    L.append("")
    L += render_report_block(md_path_disp, ts, sidecar(md_path))
    L += render_probe_block(load_json(args.matrix), load_json(args.fill), load_json(args.sim))

    text = "\n".join(x for x in L if x is not None) + "\n"
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
