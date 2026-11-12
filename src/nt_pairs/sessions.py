"""美股会话标定（纯函数，无第三方依赖）。

用于把任意 UTC 时间戳映射到：pre-market / RTH / after-hours / closed，
并区分周末、美国联邦假日、以及提前收盘日。

时区用固定规则而非 pytz：美东在 3 月第二个周日 02:00 切 EDT、11 月第一个周日 02:00 切 EST。
（本仓库只用 2026 年数据，规则足够；跨年时按同规则重算即可。）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc
ET = timezone(timedelta(hours=-5), "EST")
EDT = timezone(timedelta(hours=-4), "EDT")

# 2026 年 NYSE 全天休市（来源：NYSE 2026 交易日历）
HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # MLK
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (observed; Jul 4 is Saturday)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas
    }
)

# 提前收盘（13:00 ET）日
EARLY_CLOSE_2026: frozenset[date] = frozenset(
    {
        date(2026, 11, 27),  # day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve
    }
)

PRE_OPEN = time(4, 0)
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
POST_CLOSE = time(20, 0)


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (6 - d.weekday()) % 7  # Monday=0 … Sunday=6
    return d + timedelta(days=offset + 7 * (n - 1))


def is_dst(dt_utc: datetime) -> bool:
    """美东是否处于 EDT。"""
    year = dt_utc.year
    start = datetime.combine(_nth_sunday(year, 3, 2), time(2, 0), tzinfo=UTC) - timedelta(hours=5)
    end = datetime.combine(_nth_sunday(year, 11, 1), time(2, 0), tzinfo=UTC) - timedelta(hours=5)
    return start <= dt_utc < end


def to_et(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(EDT if is_dst(dt_utc.astimezone(UTC)) else ET)


@dataclass(frozen=True)
class SessionInfo:
    session: str        # "pre" | "rth" | "post" | "closed"
    trading_date: date
    is_weekend: bool
    is_holiday: bool
    is_early_close: bool
    minutes_to_rth_open: float | None
    minutes_from_rth_close: float | None


def classify(ts_ms: int) -> SessionInfo:
    """毫秒时间戳 → 会话。"""
    dt_utc = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
    et = to_et(dt_utc)
    d = et.date()
    weekend = et.weekday() >= 5
    holiday = d in HOLIDAYS_2026
    early = d in EARLY_CLOSE_2026
    t = et.time()

    if weekend or holiday:
        return SessionInfo("closed", d, weekend, holiday, early, None, None)

    rth_close = time(13, 0) if early else RTH_CLOSE
    if PRE_OPEN <= t < RTH_OPEN:
        mins = (datetime.combine(d, RTH_OPEN) - datetime.combine(d, t)).total_seconds() / 60
        return SessionInfo("pre", d, False, False, early, mins, None)
    if RTH_OPEN <= t < rth_close:
        return SessionInfo("rth", d, False, False, early, 0.0, 0.0)
    if rth_close <= t < POST_CLOSE:
        mins = (datetime.combine(d, t) - datetime.combine(d, rth_close)).total_seconds() / 60
        return SessionInfo("post", d, False, False, early, None, mins)
    return SessionInfo("closed", d, False, False, early, None, None)


def bucket_sessions(ts_ms_list: list[int]) -> dict[str, int]:
    """统计各会话的采样数。"""
    out = {"pre": 0, "rth": 0, "post": 0, "closed": 0}
    for ts in ts_ms_list:
        out[classify(ts).session] += 1
    return out