#!/usr/bin/env python3
"""把 `calibrate_fill_chd.py` 的标定结果转成座舱 `load_fill_calibration` 认的格式。

两边的 schema 不同：
- `scripts/calibrate_fill_chd.py` 产出：每 (TTL, 名义, 模型) 一行的 `summary`，
  含 `empirical_p_fill` / `model_p_fill` / `bias`。
- `src/nt_pairs/cockpit.py::load_fill_calibration` 期望：**每标的一条**，
  含 `symbol` / `minutes` / `fills` / `p_fill_per_minute` / `markout_5_bps` /
  `net_bps_per_fill` / `by_session`。

本脚本做格式对齐，并额外给出**座舱要的「实测成交/分钟」**：
标定里我们按 `probe_interval` 频率在贴价档位挂合成被动单，
所以「每分钟成交数」= 探针频率 × 实测 p_fill。
同时保留 `model_p_fill_per_minute`（用模型 p_fill 算的同口径值），
让座舱的「实测 ÷ 模型」规则能真正比较**同口径**的两个数。

用法
----
    python scripts/convert_fill_calibration.py \\
        --in data/fill_calibration.json --out data/fill_calibration_cockpit.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", default="data/fill_calibration.json")
    ap.add_argument("--out", dest="dst", default="data/fill_calibration_cockpit.json")
    ap.add_argument("--ttl", type=int, default=180, help="取哪个 TTL 的口径（默认 180s）")
    ap.add_argument("--probe-interval", type=float, default=5.0,
                    help="标定时的探针间隔（秒）；用于把 p_fill 换算成「成交/分钟」")
    ap.add_argument("--model", default="model_full",
                    help="用哪个模型与实测比较（model_full / model_any / compound_full / compound_any）")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.is_file():
        print(f"missing {src}", flush=True)
        return 2
    payload = json.loads(src.read_text(encoding="utf-8"))
    summary = payload.get("summary") or []
    if not summary:
        print("no summary rows in calibration file", flush=True)
        return 2

    symbol = str(payload.get("symbol") or "")
    probes_per_min = 60.0 / max(args.probe_interval, 1e-9)

    rows = [r for r in summary
            if r.get("ttl_s") == args.ttl and r.get("model") == args.model]
    if not rows:
        print(f"no rows for ttl={args.ttl} model={args.model}", flush=True)
        return 2

    # 每名义档一行 → 座舱按「每标的一条」读，这里取**最小名义档**作为主口径，
    # 并把各名义档的实测/模型比值放进 by_session（键名用 nominal 标签）。
    rows.sort(key=lambda r: r.get("notional", 0))
    base = rows[0]
    emp = float(base.get("empirical_p_fill") or 0.0)
    mdl = float(base.get("model_p_fill") or 0.0)

    by_session: dict[str, float] = {}
    for r in rows:
        label = f"notional_{int(r.get('notional', 0))}"
        by_session[label] = round(
            float(r.get("empirical_p_fill") or 0.0)
            / max(float(r.get("model_p_fill") or 0.0), 1e-9), 4)

    out = {
        "symbol": symbol,
        "minutes": float(payload.get("aligned_seconds") or 0) / 60.0,
        "fills": None,
        # 实测：探针频率 × 实测 p_fill（成交/分钟）
        "p_fill_per_minute": round(probes_per_min * emp, 4),
        # 模型同口径值（供座舱比较「实测 ÷ 模型」）
        "model_p_fill_per_minute": round(probes_per_min * mdl, 4),
        "empirical_p_fill": round(emp, 4),
        "model_p_fill": round(mdl, 4),
        "ttl_s": args.ttl,
        "probe_interval_s": args.probe_interval,
        "by_session": by_session,
        "source": payload.get("source"),
    }
    Path(args.dst).parent.mkdir(parents=True, exist_ok=True)
    Path(args.dst).write_text(json.dumps([out], indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.dst}")
    print(f"  实测 p_fill={emp:.3f}  模型 p_fill={mdl:.3f}  比值={emp/max(mdl,1e-9):.3f}")
    print(f"  成交/分钟：实测 {out['p_fill_per_minute']:.3f} vs 模型 {out['model_p_fill_per_minute']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
