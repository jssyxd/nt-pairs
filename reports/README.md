# reports/ —— VPS 侧每 6 小时自动汇报

本目录由 **VPS 上的 cron**（`scripts/vps_report_cron.sh`，见 `docs/CI_CD.md`）写入，
每次推送两个文件：

| 文件 | 内容 |
|---|---|
| `<YYYYMMDD>T<HHMMSS>Z.md` | 人读的报告：进程状态、采集样本、重复收盘率、基差、成交/未成交、净 EV、风控、异常、新发现 |
| `<YYYYMMDD>T<HHMMSS>Z.json` | 同一份报告的机读旁挂（CI 的 job summary 直接读它，不用解析 Markdown） |

命名规则固定为 **UTC 时间戳**，CI 用它挑「最新一份」（`scripts/ci_summary.py`）。

**规则**：

- 这里只允许出现**公开数据与本机日志的统计**；任何 key / secret / passphrase / 私钥 / 账户地址
  一律经 `scripts/make_report.py` 的 `redact()` 处理为「前 6 位 + …」。
- CI 只**读**本目录，不写、不 push。推送由 VPS 侧用 SSH deploy key 完成。
- 手工补一份：VPS 上跑 `./scripts/vps_report_cron.sh`。
