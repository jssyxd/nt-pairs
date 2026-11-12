"""会话标定的行为测试（2026 年 NYSE 日历）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nt_pairs.sessions import classify


def ms(y, mo, d, h, mi, tz=timezone.utc):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp() * 1000)


def test_rth_midday_is_rth():
    # 2026-11-12 15:00 UTC = 10:00 ET (EST)
    s = classify(ms(2026, 11, 12, 15, 0))
    assert s.session == "rth"
    assert s.is_weekend is False and s.is_holiday is False


def test_pre_market_and_after_hours():
    # 13:00 UTC = 08:00 ET → pre ; 22:00 UTC = 17:00 ET → post
    assert classify(ms(2026, 11, 12, 13, 0)).session == "pre"
    assert classify(ms(2026, 11, 12, 22, 0)).session == "post"


def test_weekend_is_closed():
    # 2026-11-14 is Saturday
    assert classify(ms(2026, 11, 14, 16, 0)).session == "closed"
    assert classify(ms(2026, 11, 14, 16, 0)).is_weekend is True


def test_holiday_is_closed():
    # Thanksgiving 2026-11-26
    s = classify(ms(2026, 11, 26, 16, 0))
    assert s.session == "closed"
    assert s.is_holiday is True


def test_early_close_day_post_runs_until_20et():
    # 2026-11-27 (half day): 18:30 UTC = 13:30 ET → post
    s = classify(ms(2026, 11, 27, 18, 30))
    assert s.session == "post"
    assert s.is_early_close is True


def test_dst_boundary_et_offset():
    # 2026-03-06 15:00 UTC = 10:00 EST → RTH
    assert classify(ms(2026, 3, 6, 15, 0)).session == "rth"
    # 2026-03-10 14:00 UTC = 10:00 EDT → RTH（夏令时后提前一小时）
    assert classify(ms(2026, 3, 10, 14, 0)).session == "rth"


def test_minutes_fields():
    s = classify(ms(2026, 11, 12, 14, 0))  # 09:00 ET
    assert s.session == "pre"
    assert s.minutes_to_rth_open == pytest.approx(30.0)