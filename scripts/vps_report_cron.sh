#!/usr/bin/env bash
# VPS 侧 6 小时汇报 cron 脚本：生成报告 → 提交到 `reports/` → 用 SSH deploy key 推送。
#
# 设计约束：
#   * **脚本里不写任何 token / 密钥 / 密码**。认证完全由外部提供：
#       - SSH deploy key（推荐）：`NT_REPORT_SSH_KEY` 指向私钥文件路径；
#         或用 ssh-agent（此时不设该变量）。
#       - git credential helper（次选）：由 `git config credential.helper` 提供，
#         token 存在 helper 里，不落盘到本脚本。
#   * 只推 `reports/` 目录，绝不推其他路径。
#   * 失败时以非 0 退出（systemd/cron 会记录），但不 delete 任何东西。
#
# 安装（见 docs/CI_CD.md）：
#   crontab -e
#   17 */6 * * * /opt/nt-pairs/scripts/vps_report_cron.sh >> /var/log/nt-pairs/report_cron.log 2>&1
#
# 环境变量（全部可选）：
#   NT_REPO_DIR        仓库目录（默认：脚本所在目录的上一级）
#   NT_LOG_DIR         日志目录（默认 /var/log/nt-pairs）
#   NT_COLLECTOR_DIR   L2 采集目录（默认 $NT_REPO_DIR/data/collector）
#   NT_REPORT_SSH_KEY  deploy key 私钥文件路径（默认 ~/.ssh/nt_pairs_report）
#   NT_REPORT_SSH_USER deploy key 在 GitHub 上注册的登录名（默认 git）
#   NT_REPORT_BRANCH   目标分支（默认 main）
#   NT_REPORT_REMOTE   远端名（默认 origin）
#   NT_PYTHON          Python 解释器（默认 python3）
#   NT_REPORT_WINDOW_HOURS  统计窗口小时数（默认 6）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${NT_REPO_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
LOG_DIR="${NT_LOG_DIR:-/var/log/nt-pairs}"
COLLECTOR_DIR="${NT_COLLECTOR_DIR:-${REPO_DIR}/data/collector}"
SSH_USER="${NT_REPORT_SSH_USER:-git}"
BRANCH="${NT_REPORT_BRANCH:-main}"
REMOTE="${NT_REPORT_REMOTE:-origin}"
SSH_KEY="${NT_REPORT_SSH_KEY:-${HOME}/.ssh/nt_pairs_report}"
PYTHON="${NT_PYTHON:-python3}"
WINDOW_HOURS="${NT_REPORT_WINDOW_HOURS:-6}"

cd "${REPO_DIR}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_MD="reports/${STAMP}.md"
REPORT_JSON="reports/${STAMP}.json"

mkdir -p reports

# 1) 生成报告（纯标准库；无任何密钥输入）
"${PYTHON}" scripts/make_report.py \
  --logdir "${LOG_DIR}" \
  --collector "${COLLECTOR_DIR}" \
  --matrix "${REPO_DIR}/data/matrix.json" \
  --sim "${REPO_DIR}/data/soxl_sim.json" \
  --window-hours "${WINDOW_HOURS}" \
  --out "${REPORT_MD}" \
  --json-out "${REPORT_JSON}"

# 2) 提交（只提交 reports/）
git add -- "${REPORT_MD}" "${REPORT_JSON}"
if git diff --cached --quiet -- "${REPORT_MD}" "${REPORT_JSON}"; then
  echo "[$(date -u +%FT%TZ)] 报告内容无变化，跳过提交与推送"
  exit 0
fi
git -c user.name="${GIT_AUTHOR_NAME:-nt-pairs-report}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-nt-pairs-report@localhost}" \
    commit -m "report: ${STAMP} 6h summary" -- "${REPORT_MD}" "${REPORT_JSON}"

# 3) 推送：优先 deploy key，其次 ssh-agent / credential helper
if [ -f "${SSH_KEY}" ]; then
  export GIT_SSH_COMMAND="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi
git push "${REMOTE}" "HEAD:${BRANCH}"

echo "[$(date -u +%FT%TZ)] 已推送 ${REPORT_MD} / ${REPORT_JSON} → ${REMOTE}/${BRANCH}"
