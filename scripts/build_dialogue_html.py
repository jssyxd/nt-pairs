#!/usr/bin/env python
"""把本项目的全部人机对话、Agent 交互转录、模型推导与交付文档汇总成一个自包含 HTML。

收录范围（一个字都不删）：
- 人类 ↔ Agent 原始对话（《对话2.md》《nautilus原生方案讨论.md》）
- 人类在本项目的指令原文（docs/human_inputs.md）
- Captain 对人类的回复原文（docs/captain_log.md）
- 每一个 Herdr job 的：captain 下发的 brief（input.md）→ worker 终端逐字转录（pane.md）→ worker 交付（report.md）
- 项目全部文档、代码、配置、测试、field guide

每个文件同时给出「渲染版」与「原文（raw，折叠）」，保证可读性与逐字可查。

用法：python scripts/build_dialogue_html.py
输出：对话.html（仓库根目录）
"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "对话.html")

SECTIONS = [
    ("A", "人类 ↔ Agent 原始对话（前序项目）", [
        (r"W:\聪明人的个人成长\对话2.md", "对话2.md"),
        (r"W:\聪明人的个人成长\nautilus原生方案讨论.md", "nautilus原生方案讨论.md"),
    ]),
    ("B", "人类在本项目的指令 / Captain 回复原文", [
        (os.path.join(ROOT, "docs", "human_inputs.md"), "human_inputs.md"),
        (os.path.join(ROOT, "docs", "captain_log.md"), "captain_log.md"),
    ]),
    ("C", "Herdr 派发记录（captain ↔ worker，nt-pairs）", None),
    ("D", "Herdr 派发记录（captain ↔ worker，ant-cross-arb 前序研究）", None),
    ("E", "项目文档：总纲、契约、状态、矩阵、EV、部署、回测", None),
    ("F", "源码与脚本（可执行证据）", None),
    ("G", "配置、测试与 Field Guide", None),
]

CSS = """
:root{--fg:#1c1e21;--muted:#6b7280;--bg:#fbfbfa;--card:#fff;--bd:#e6e4df;--acc:#0b6bcb;--warn:#a1420f;--code:#f6f5f1}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,"Segoe UI",Roboto,"Noto Sans SC","PingFang SC",sans-serif}
header{padding:34px 30px 22px;border-bottom:1px solid var(--bd);background:#fff}
h1{margin:0 0 6px;font-size:26px}
.sub{color:var(--muted);font-size:13px}
nav{position:sticky;top:0;z-index:9;background:#fffffff2;backdrop-filter:blur(6px);border-bottom:1px solid var(--bd);padding:10px 30px;font-size:13px;max-height:38vh;overflow:auto}
nav a{color:var(--acc);text-decoration:none;margin-right:14px;display:inline-block}
nav a:hover{text-decoration:underline}
main{padding:22px 30px 80px;max-width:1180px;margin:0 auto}
section{margin:30px 0}
h2{font-size:21px;border-bottom:2px solid var(--bd);padding-bottom:8px;margin:34px 0 14px}
h3{font-size:17px;margin:22px 0 8px}
h4{font-size:15px;margin:18px 0 6px;color:#374151}
p{margin:9px 0}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:13px;font-family:ui-monospace,Consolas,monospace}
pre{background:var(--code);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;overflow:auto;font-size:12.5px;line-height:1.5}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px;background:var(--card)}
th,td{border:1px solid var(--bd);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f0efeb;font-weight:600}
tr:nth-child(even) td{background:#fcfcfb}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--acc);background:#f4f8fd;color:#1f2937}
details{margin:10px 0;border:1px solid var(--bd);border-radius:8px;background:var(--card)}
details>summary{cursor:pointer;padding:8px 12px;font-size:13px;color:var(--muted)}
details>div{padding:0 12px 12px}
.file-meta{font-size:12.5px;color:var(--muted);margin:2px 0 8px}
a{color:var(--acc)}
hr{border:0;border-top:1px solid var(--bd);margin:24px 0}
.tag{display:inline-block;font-size:11.5px;border:1px solid var(--bd);border-radius:10px;padding:1px 8px;color:var(--muted);margin-right:6px}
.warn{color:var(--warn)}
"""


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception as exc:  # noqa: BLE001
        return f"<< 读取失败：{path}：{exc} >>"


def md_to_html(md: str) -> str:
    out = []
    lines = md.split("\n")
    i = 0
    in_code = False
    code_buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        # table?
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            out.append("<hr/>")
            i += 1
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + "<br/>".join(inline(x) for x in buf) + "</blockquote>")
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if not line.strip():
            i += 1
            continue
        out.append(f"<p>{inline(line)}</p>")
        i += 1
    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    return "\n".join(out)


def inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank">\1</a>', s)
    s = re.sub(r"(?<![(\w])(https?://[^\s<>\"]+)", r'<a href="\1" target="_blank">\1</a>', s)
    return s


def file_block(path: str, label: str) -> str:
    raw = read(path)
    size = len(raw.encode("utf-8"))
    return (
        f'<h3>{html.escape(label)}</h3>'
        f'<div class="file-meta"><span class="tag">{html.escape(path)}</span>'
        f'<span class="tag">{size:,} bytes</span></div>'
        f"{md_to_html(raw)}"
        f"<details><summary>原文（逐字，raw）</summary><div><pre><code>"
        f"{html.escape(raw)}</code></pre></div></details>"
    )


def job_files(base: str) -> list[tuple[str, str]]:
    root = os.path.join(base, ".herd-dispatch", "jobs")
    res: list[tuple[str, str]] = []
    if not os.path.isdir(root):
        return res
    for job in sorted(os.listdir(root)):
        jd = os.path.join(root, job)
        if not os.path.isdir(jd):
            continue
        for name in ("input.md", "brief.md", "pane.md", "report.md"):
            p = os.path.join(jd, name)
            if os.path.exists(p):
                res.append((p, f"job `{job}` · {name}"))
    return res


def glob_files(base: str, patterns: list[str]) -> list[tuple[str, str]]:
    import glob
    res = []
    for pat in patterns:
        for p in sorted(glob.glob(os.path.join(base, pat))):
            if os.path.isfile(p):
                res.append((p, os.path.relpath(p, base).replace("\\", "/")))
    return res


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    parts: list[str] = []
    toc: list[str] = []
    for tag, title, files in SECTIONS:
        sid = f"sec-{tag}"
        toc.append(f'<a href="#{sid}">{tag}. {html.escape(title)}</a>')
        parts.append(f'<section id="{sid}"><h2>{tag}. {html.escape(title)}</h2>')
        if files is not None:
            for path, label in files:
                if os.path.exists(path):
                    parts.append(file_block(path, label))
                else:
                    parts.append(f'<div class="file-meta warn">缺失：{html.escape(path)}</div>')
        else:
            if tag == "C":
                items = job_files(ROOT)
            elif tag == "D":
                items = job_files(r"W:\ant-cross-arb")
            elif tag == "E":
                items = glob_files(ROOT, ["README.md", "CONTRACT.md", "STATUS.md", "docs/*.md"])
            elif tag == "F":
                items = glob_files(ROOT, ["src/nt_pairs/*.py", "scripts/*.py"])
            else:
                items = (glob_files(ROOT, ["configs/*.json", "tests/*.py", ".field-guide/*.md"]))
            if not items:
                parts.append('<div class="file-meta warn">（未找到文件）</div>')
            for path, label in items:
                parts.append(file_block(path, label))
        parts.append("</section>")

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>nt-pairs · 全量人机对话与建模记录</title><style>{CSS}</style></head><body>
<header>
<h1>nt-pairs · 全量人机对话 / Agent 交互 / 建模与部署记录</h1>
<div class="sub">生成时间 {now} ｜ 仓库 nt-pairs ｜ 引擎 NautilusTrader 2.0.0rc6 ｜
涵盖：人类↔Agent 对话、Captain↔worker 逐字转录、矩阵与演算、EV/净边际、实时监控口径、回测报告、部署指导书</div>
</header>
<nav>{''.join(toc)}</nav>
<main>
<div class="file-meta warn">说明：本文件按「一个字都不拉」收录所有源文件的渲染版，并附每份文件的原文（raw，折叠）。渲染由
<code>scripts/build_dialogue_html.py</code> 生成；如需逐字核对请展开对应文件的「原文」区块。</div>
{''.join(parts)}
</main></body></html>"""

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"wrote {OUT} ({len(body.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())