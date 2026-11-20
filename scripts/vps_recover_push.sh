#!/usr/bin/env bash
# 在 VPS 上把本地状态并回上游并推送（**跑在 VPS 上**）。
#
# 前提：VPS 的 deploy key 已在 GitHub 仓库里注册且**允许写入**
#   （仓库 Settings → Deploy keys → Add deploy key → Allow write access）。
# 自检：ssh -T -i ~/.ssh/nt_pairs_report git@github.com 应回
#   "Hi jssyxd! You've successfully authenticated…"
#
# 为什么是 reset 而不是 rebase：
#   那 26 期历史报告的**内容**已经随本机仓库的提交（c57d54b「收录 VPS 上 28 期 6h 报告」）
#   进了上游，VPS 本地那串 report commit 与它内容重复 → rebase 会撞同一批文件。
#   所以这里直接把 VPS 的分支对齐到上游（历史报告由上游那一份承载），
#   之后 cron 从这个点继续生成新报告、继续推送。
#
# 用法：bash /opt/nt-pairs/scripts/vps_recover_push.sh
set -euo pipefail

REPO_DIR="${NT_REPO_DIR:-/opt/nt-pairs}"
SSH_KEY="${NT_REPORT_SSH_KEY:-${HOME}/.ssh/nt_pairs_report}"

cd "${REPO_DIR}"
export GIT_SSH_COMMAND="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

echo "== 1) 认证检查"
ssh -T -i "${SSH_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new git@github.com 2>&1 | head -2 || true

echo "== 2) 备份当前 HEAD（万一要回滚）"
git branch -f "backup-before-align-$(date -u +%Y%m%dT%H%M%SZ)" HEAD || true

echo "== 3) 取上游并对齐"
git fetch origin main
git reset --hard origin/main

echo "== 4) 立刻跑一次报告脚本，验证推送链路"
bash scripts/vps_report_cron.sh || echo "（cron 退出码非 0：推送失败或无变化）"

echo "== 5) 校验"
git fetch origin main
git log --oneline -3
echo "local HEAD : $(git rev-parse HEAD)"
echo "origin/main: $(git rev-parse origin/main)"
echo "报告文件数: $(ls -1 reports/*.md 2>/dev/null | wc -l)"
