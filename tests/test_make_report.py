"""`scripts/make_report.py` 与 `scripts/ci_summary.py` 的行为测试。

覆盖三件**消费者可见**的事：
1. 没有任何日志/采集数据时，仍产出一份「无数据」报告（不抛异常，退出码 0）；
2. 脱敏：key / secret / 私钥 / 账户地址只输出「前 6 位 + …」，普通数字不受影响；
3. 报告与 summary 的关键数字正确（成交/未成交计数、重复收盘率、净 EV）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "logs")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


make_report = _load("make_report")
ci_summary = _load("ci_summary")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """在进程内调用 `make_report.main()`（比起子进程快得多，且同样覆盖 CLI 参数解析）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = make_report.main(args)
    return subprocess.CompletedProcess(args, code, buf.getvalue(), buf.getvalue())


# ------------------------------------------------------------------ 脱敏


@pytest.mark.parametrize("line,forbidden", [
    ("api_key=HL_PRIVATE_KEY=0x1234567890abcdef feed=ok", "1234567890abcdef"),
    ("secret: abcd1234efgh5678", "abcd1234efgh5678"),
    ("passphrase=\"xyz9876543210\"", "xyz9876543210"),
    ("account_address=0x1234567890abcdef1234567890abcdef12345678", "1234567890abcdef"),
])
def test_redact_hides_secrets(line: str, forbidden: str) -> None:
    out = make_report.redact(line)
    assert forbidden not in out
    assert "…" in out


def test_redact_keeps_ordinary_numbers() -> None:
    line = "2026-11-12T03:05:12Z INFO pair=xyz:SOXL|OPENAI event=fill qty=100 px=62.88"
    assert make_report.redact(line) == line


# ------------------------------------------------------------------ 无数据


def test_report_without_any_input(tmp_path) -> None:
    out = tmp_path / "r.md"
    proc = _run(["--logdir", str(tmp_path / "nologs"), "--collector", str(tmp_path / "nocoll"),
                 "--matrix", str(tmp_path / "no.json"), "--sim", str(tmp_path / "no.json"),
                 "--out", str(out)])
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "无数据" in text
    assert "# nt-pairs 6 小时报告" in text


def test_report_with_fixture_logs(tmp_path) -> None:
    out = tmp_path / "r.md"
    side = tmp_path / "r.json"
    # 固定「现在」，让样本日志（2026-11-12T03:00~05:00）落在 6 小时窗口内
    proc = _run(["--logdir", FIXTURES, "--collector", str(tmp_path / "nocoll"),
                 "--matrix", str(tmp_path / "no.json"), "--sim", str(tmp_path / "no.json"),
                 "--now", "2026-11-12T09:00:00Z",
                 "--out", str(out), "--json-out", str(side)])
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "成交：**4**" in text
    assert "未成交：**3**" in text
    assert "风控事件：**2**" in text
    data = json.loads(side.read_text(encoding="utf-8"))
    assert data["logs"]["fills"] == 4
    assert data["logs"]["no_fills"] == 3
    assert data["logs"]["risk_events"] == 2
    # 报告里不能出现日志中的明文密钥
    assert "0x1234567890abcdef" not in text
    assert "HL_PRIVATE_KEY" not in text


def test_log_counts_respect_window(tmp_path) -> None:
    """窗口外的日志事件不计入统计。"""
    out = tmp_path / "r.md"
    proc = _run(["--logdir", FIXTURES, "--collector", str(tmp_path / "nocoll"),
                 "--matrix", str(tmp_path / "no.json"), "--sim", str(tmp_path / "no.json"),
                 "--now", "2026-11-13T09:00:00Z", "--out", str(out)])
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    assert "成交：**0**" in text
    assert "未成交：**0**" in text


def _summary(args: list[str]) -> subprocess.CompletedProcess:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = ci_summary.main(args)
    return subprocess.CompletedProcess(args, code, buf.getvalue(), buf.getvalue())


def test_summary_requires_a_report(tmp_path) -> None:
    empty = tmp_path / "reports"
    empty.mkdir()
    assert _summary(["--reports", str(empty)]).returncode == 3


def test_summary_ignores_non_timestamped_markdown(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "README.md").write_text("# 说明\n", encoding="utf-8")
    proc = _summary(["--reports", str(reports)])
    assert proc.returncode == 3, proc.stdout


def test_summary_renders_numbers(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    md = reports / "20261112T120000Z.md"
    md.write_text("# 报告\n", encoding="utf-8")
    (reports / "20261112T120000Z.json").write_text(json.dumps({
        "generated_utc": "2026-11-12T12:00:00Z", "window_hours": 6,
        "logs": {"fills": 4, "no_fills": 3, "risk_events": 2, "errors": 1, "lines": 20,
                 "findings": ["队列跳变"]},
        "collector": {"window_samples": 17, "total_samples": 100,
                      "venues": {"HYPERLIQUID": {"window_samples": 6, "samples": 60,
                                                 "stale_ratio": 0.25}}},
        "matrix": {"available": True, "pairs": 10, "tradable": 2},
        "sim": {"available": True, "overall": {"net_mean_bps": 3.41}},
    }, ensure_ascii=False), encoding="utf-8")
    proc = _summary(["--reports", str(reports)])
    assert proc.returncode == 0, proc.stderr
    assert "17 / 100" in proc.stdout
    assert "4 / 3" in proc.stdout
    assert "3.41" in proc.stdout
    assert "队列跳变" in proc.stdout
