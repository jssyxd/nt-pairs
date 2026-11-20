#!/usr/bin/env python3
"""本地提交 → 经 VPS 推送到 GitHub（本地没有 GitHub 凭证时的中继）。

为什么需要：本机 Git Credential Manager 里存的 token 已失效（API 401），
本机 SSH key 也没在 GitHub 注册；但 **VPS 上的 deploy key** 一旦被授予写权限，
就能代推。本脚本把本地「还没进上游」的提交打成 bundle → 送到 VPS → 由 VPS 推送。

用法：
    python scripts/push_via_vps.py            # 推送本地 main 相对 origin/main 的全部提交
    python scripts/push_via_vps.py --dry-run  # 只看要推什么，不动远端

前提：
    VPS 的 deploy key 已注册且允许写入（ssh -T -i ~/.ssh/nt_pairs_report git@github.com 自检）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VPS_PY = REPO / "scripts" / "vps.py"
REMOTE_BUNDLE = "/opt/nt-pairs/relay.bundle"
REMOTE_BRANCH = "relay/from-local"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只显示要推的提交")
    ap.add_argument("--host", default="155.254.60.38")
    ap.add_argument("--user", default="root")
    args = ap.parse_args()

    ahead = run(["git", "log", "--oneline", "origin/main..main"]).stdout.strip()
    if not ahead:
        print("本地没有待推送的提交（origin/main..main 为空）")
        return 0
    print("== 待推送提交 ==")
    print(ahead)
    if args.dry_run:
        return 0

    if not os.environ.get("VPS_PASSWORD"):
        print("需要环境变量 VPS_PASSWORD", file=sys.stderr)
        return 2

    bundle = REPO / ".tmp" / "relay.bundle"
    bundle.parent.mkdir(exist_ok=True)
    bundle.unlink(missing_ok=True)
    b = run(["git", "bundle", "create", str(bundle), "main"])
    if b.returncode != 0:
        print(b.stderr, file=sys.stderr)
        return 1
    print(f"== bundle {bundle} ({bundle.stat().st_size} bytes)")

    put = run([sys.executable, str(VPS_PY), "put", "--host", args.host, "--user", args.user,
               "--local", str(bundle), "--remote", REMOTE_BUNDLE])
    print(put.stdout.strip() or put.stderr.strip())
    if put.returncode != 0:
        return 1

    remote_cmd = (
        f"cd /opt/nt-pairs && "
        f"export GIT_SSH_COMMAND='ssh -i /root/.ssh/nt_pairs_report -o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new' && "
        f"git fetch {REMOTE_BUNDLE} main:{REMOTE_BRANCH} && "
        f"git push origin {REMOTE_BRANCH}:main && "
        f"git fetch origin main && git log --oneline -3 origin/main"
    )
    push = run([sys.executable, str(VPS_PY), "run", "--host", args.host, "--user", args.user, "--", remote_cmd])
    print(push.stdout.strip() or push.stderr.strip())
    if push.returncode != 0:
        return 1

    print("== 同步本地引用 ==")
    run(["git", "fetch", "origin", "main"])
    print(run(["git", "log", "--oneline", "-3"]).stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
