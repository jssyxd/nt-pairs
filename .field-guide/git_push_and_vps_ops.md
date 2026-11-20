# 经验记录：GitHub 推送、VPS 运维、nautilus 2.0 的坑

> 约定：只记**踩过的坑**（环境怪癖、非显然的不变量、看起来对但失败的做法），每条带日期与证据；
> 只追加，写错了用带日期的一行更正，不要重写历史。

## 2026-11-20 · 明文 PAT 会被 GitHub 自动吊销
- 现象：`ghp_…` 在 `W:\.env.md`、两个仓库的 `.git/config` remote URL、agent 会话日志里以明文存在过，
  后来**三个不同的 PAT 同时**变成 `401 Bad credentials`（Bearer / token / Basic 三种鉴权方式全拒）。
- 结论：GitHub secret scanning 会自动吊销暴露过的 PAT。
- 做法：token 只进 `git credential approve`（Windows 凭据管理器），**不进任何明文文件、不进 .git/config**。
  清理明文：`.git/config` 用 `git remote set-url` 去掉内嵌 token；notes/报告文件直接替换掉字符串。

## 2026-11-20 · 6h 报告 cron 48 次推送全被拒
- 现象：`/var/log/nt-pairs/report_cron.log` 里 `grep -c rejected` = 48，全部是
  `! [rejected] HEAD -> main (fetch first)`——cron 只 commit+push，从不 fetch，
  而 captain 往 main 推过代码 → VPS 分叉。
- 修法：`scripts/vps_report_cron.sh` 改成 **先 `git fetch` + `rebase` 再提交推送**；
  推送失败要**优雅退出**（退出码 3 + 打印本地报告路径），不要 `set -e` 直接崩。

## 2026-11-20 · `git reset --soft origin/main` 会把上游新文件当成删除提交
- 现象：本地没有 VPS 刚推的报告文件时，`git reset --soft origin/main` 会保留旧 index，
  于是那些上游文件在 index 里显示为 `D`，跟着下一次 commit 一起把**上游文件删掉**
  （本次误删 `reports/20261120T092143Z.{md,json}`，CI 随即变红）。
- 做法：同步上游用 `git fetch origin main && git merge --ff-only`（或 `git pull --rebase`）；
  必须 rebase 时先配好 `user.name`/`user.email`（仓库原本没配，`git rebase --continue` 会卡在
  `fatal: unable to auto-detect email address`）。

## 2026-11-20 · nautilus 2.0.0rc6 的 `with_timeout_connection` 要**整数秒**
- 现象：传 `timedelta(seconds=60)` → `TypeError: 'datetime.timedelta' object cannot be interpreted as an integer`。
- 签名：`with_timeout_connection(self, /, timeout_secs)`、`with_timeout_disconnection_secs(self, secs)`（都是 int）。

## 2026-11-20 · `live_runner --max-secs` 默认 60 会造成 systemd 重启风暴
- 现象：`nt-trader` 一夜 `NRestarts=6029`，journal 里每分钟一次 `Awaiting engine connections (60s timeout)`，
  看着像网络问题，实际是**节点自己到点优雅停机** + systemd `Restart=always` 又拉起来。
- 做法：常驻单元必须传 `--max-secs 0`（一直跑到 systemctl stop）；症状是「每 60 秒正好一次」。

## 2026-11-20 · VPS 上的 deploy key 必须在仓库里注册且勾「允许写入」
- 自检：`ssh -T -i /root/.ssh/nt_pairs_report git@github.com` 应回
  `Hi jssyxd/nt-pairs! You've successfully authenticated`；
  `git ls-remote` 还必须带 `GIT_SSH_COMMAND="ssh -i … -o IdentitiesOnly=yes"`，否则会去用默认 identity 而被拒。
- 注册（用有 admin 的 token 调 API）：`POST /repos/{owner}/{repo}/keys`
  `{"title":"nt-pairs-vps","key":"<公钥>","allow_write_access":true}`。
