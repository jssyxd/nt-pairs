#!/usr/bin/env python
"""VPS 远端执行/上传助手（paramiko）。

用途：VibeShell 的本地条目里这台 VPS 的凭证已失效（password 条目被服务器拒绝、
key 条目密钥未被授权），本脚本用**用户显式提供的密码**建立会话。
密码只从环境变量 `VPS_PASSWORD` 读取，不落盘、不进 argv、不进仓库。

用法：
    VPS_PASSWORD=... python scripts/vps.py run --host 155.254.60.38 --user root -- "uname -a"
    VPS_PASSWORD=... python scripts/vps.py put --host ... --local a.sh --remote /tmp/a.sh
    VPS_PASSWORD=... python scripts/vps.py get --host ... --remote /var/log/x.log --local x.log
"""

from __future__ import annotations

import argparse
import os
import sys

import paramiko


def connect(host: str, user: str, port: int = 22, timeout: int = 20):
    pwd = os.environ.get("VPS_PASSWORD")
    if not pwd:
        raise SystemExit("需要环境变量 VPS_PASSWORD")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, port=port, username=user, password=pwd, timeout=timeout,
              banner_timeout=timeout, auth_timeout=timeout, look_for_keys=False,
              allow_agent=False)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["run", "put", "get"])
    ap.add_argument("--host", default="155.254.60.38")
    ap.add_argument("--user", default="root")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--local")
    ap.add_argument("--remote")
    ap.add_argument("command", nargs="*")
    args = ap.parse_args()

    c = connect(args.host, args.user, args.port)
    try:
        if args.action == "run":
            cmd = args.command[0] if args.command else ""
            if args.remote and os.path.exists(args.remote):   # 本地脚本内容当命令
                cmd = open(args.remote, encoding="utf-8").read()
            elif args.remote:                                  # 远端脚本：下载后执行
                sftp = c.open_sftp()
                with sftp.open(args.remote, "r") as fh:
                    cmd = fh.read().decode("utf-8", "replace")
                sftp.close()
            stdin, stdout, stderr = c.exec_command(cmd, timeout=900)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            sys.stdout.write(out)
            sys.stderr.write(err)
            return code
        if args.action == "put":
            sftp = c.open_sftp()
            sftp.put(args.local, args.remote)
            sftp.close()
            print(f"uploaded {args.local} -> {args.remote}")
            return 0
        if args.action == "get":
            sftp = c.open_sftp()
            sftp.get(args.remote, args.local)
            sftp.close()
            print(f"downloaded {args.remote} -> {args.local}")
            return 0
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())