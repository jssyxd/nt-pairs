"""复核测试：`scripts/aggregate_daily.py` 的逐日聚合（2026-11-21 独立复核）。

要钉住的行为：

* 每次跑覆盖的窗口是「[前一天 23:00, 当天 23:00]」，所以 `by_day` 里既有
  **当天整天**的条目，也有**前一天最后一小时**的碎片条目；
* 聚合必须**只取「这一天自己那次跑」的整天条目**，绝不能把前一天的 1 小时碎片
  当成整天（否则事件数、净 EV 全部失真）；
* 同一天有多个文件（重跑）时，取事件数多的那份。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_aggregate():
    path = ROOT / "scripts" / "aggregate_daily.py"
    spec = importlib.util.spec_from_file_location("_aggregate_daily", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


PAIR = "hyperliquid_futures:XYZ:SOXL~lighter:SOXL"


def _day(events: int, net_per_event: float, gross: float = 20.0,
         win: float = 0.5, p_fill: float = 0.4) -> dict:
    return {
        "events": events,
        "win_rate": win,
        "net_bps_total": round(events * net_per_event, 2),
        "net_bps_per_event": net_per_event,
        "gross_capture_bps_per_event": gross,
        "mean_p_fill": p_fill,
    }


def _payload(by_day: dict, by_session: dict | None = None) -> dict:
    return {
        "stage_b": [{
            "key": PAIR,
            "b_star_bps": 11.1,
            "coverage_ratio": 2.05,
            "depth_min_usd": 264600.0,
            "replay": {
                "by_day": by_day,
                "by_session": by_session or {},
            },
        }],
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fragment_of_previous_day_is_not_treated_as_a_full_day(tmp_path):
    """前一天的 1 小时碎片不得进入聚合；当天必须用当天整天的条目。"""
    agg = _load_aggregate()
    # 跑 11-03：窗口 = [11-02 23:00, 11-03 23:00]
    #   by_day["2026-11-02"] = 前一天最后一小时的碎片（12 个事件）
    #   by_day["2026-11-03"] = 当天整天（880 个事件）
    _write(tmp_path / "daily_2026-11-03.json",
           _payload({"2026-11-02": _day(12, 5.0), "2026-11-03": _day(880, 0.93)},
                    {"rth": {"net_bps_per_event": 2.05}, "closed": {"net_bps_per_event": -0.11}}))
    # 跑 11-04：by_day["2026-11-03"] 又是 1 小时碎片（15 个事件，净 EV 看起来很好）
    _write(tmp_path / "daily_2026-11-04.json",
           _payload({"2026-11-03": _day(15, 5.0), "2026-11-04": _day(700, 3.0)}))

    out = tmp_path / "daily_stability.json"
    rc = agg.main(["--glob", str(tmp_path / "daily_*.json"), "--out", str(out),
                   "--report", str(tmp_path / "DAILY_STABILITY.md")])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    rows = {r["date"]: r for r in payload["daily"]}

    # 只有两天，且没有「前一天碎片」这一天
    assert set(rows) == {"2026-11-03", "2026-11-04"}
    # 11-03 用的是自己那次跑的整天（880 事件），不是 11-04 那次跑出来的 1 小时碎片
    assert rows["2026-11-03"]["events"] == 880
    assert rows["2026-11-03"]["net_bps_per_event"] == pytest_approx(0.93)
    assert rows["2026-11-04"]["events"] == 700


def test_same_day_from_two_files_keeps_the_larger_run(tmp_path):
    """同一天重跑两次 → 取事件数多的那份（完整 24h），不取碎片。"""
    agg = _load_aggregate()
    _write(tmp_path / "daily_2026-11-03.json",
           _payload({"2026-11-03": _day(880, 0.93)}))
    _write(tmp_path / "daily_2026-11-03_rerun.json",
           _payload({"2026-11-03": _day(880, 0.93)}))
    out = tmp_path / "daily_stability.json"
    rc = agg.main(["--glob", str(tmp_path / "daily_*.json"), "--out", str(out),
                   "--report", str(tmp_path / "DAILY_STABILITY.md")])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["days"] == 1
    assert payload["daily"][0]["events"] == 880


def test_session_columns_come_from_the_same_run(tmp_path):
    """会话列来自同一次跑的 by_session。"""
    agg = _load_aggregate()
    _write(tmp_path / "daily_2026-11-03.json",
           _payload({"2026-11-03": _day(880, 0.93)},
                    {"rth": {"net_bps_per_event": 2.05},
                     "pre": {"net_bps_per_event": 0.49},
                     "post": {"net_bps_per_event": 0.52},
                     "closed": {"net_bps_per_event": -0.11}}))
    out = tmp_path / "daily_stability.json"
    agg.main(["--glob", str(tmp_path / "daily_*.json"), "--out", str(out),
              "--report", str(tmp_path / "DAILY_STABILITY.md")])
    payload = json.loads(out.read_text(encoding="utf-8"))
    sess = payload["daily"][0]["sessions"]
    assert sess["rth"] == pytest_approx(2.05)
    assert sess["closed"] == pytest_approx(-0.11)
    # 报告里的表格行数 = 天数
    text = (tmp_path / "DAILY_STABILITY.md").read_text(encoding="utf-8")
    assert text.count("| 2026-11-03 |") == 1


def pytest_approx(value, rel=1e-9):
    import pytest
    return pytest.approx(value, rel=rel)
