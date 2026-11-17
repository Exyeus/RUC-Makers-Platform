#!/usr/bin/env python3
"""Render a local, escaped, dependency-free HTML evidence report. Not a test runner."""
from __future__ import annotations
import argparse
import collections
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from gate_evidence import evidence_path


def render(data: dict, source: Path, output: Path) -> str:
    esc = lambda s: html.escape(str(s), quote=True)
    run = data.get('run', {})
    checks = data.get('checks', [])
    counts = collections.Counter(c.get('status', 'not_run') for c in checks)
    chart = ''.join(f'<tr><th>{esc(k)}</th><td><meter min="0" max="{max(1, len(checks))}" value="{v}"></meter> {v}</td></tr>' for k, v in sorted(counts.items()))
    blocks = []
    for c in checks:
        steps = ''.join(f'<li><strong>{esc(s.get("action", ""))}</strong><p>{esc(s.get("observed", ""))}</p></li>' for s in c.get('steps', []))
        artifacts = []
        for e in c.get('evidence', []):
            try:
                file = evidence_path(source.parent, e['path'])
                relative = os.path.relpath(file, output.parent).replace(os.sep, '/')
                href = esc(quote(relative, safe='/'))
                label = esc(e.get('caption', e['path']))
                if file.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
                    artifacts.append(f'<figure><a href="{href}"><img src="{href}" alt="{label}" loading="lazy"></a><figcaption>{label}</figcaption></figure>')
                else:
                    artifacts.append(f'<p><a href="{href}">{label}</a> · {esc(e.get("kind", "evidence"))}</p>')
            except (ValueError, KeyError, TypeError) as exc:
                artifacts.append(f'<p class="gap">证据缺失或路径不安全：{esc(exc)}</p>')
        assertions = ''.join(f'<li>{esc(k)}: {esc(v)}</li>' for k, v in c.get('assertions', {}).items())
        blocks.append(f'<section><h2>{esc(c.get("id", ""))} · {esc(c.get("name", ""))}</h2><p><strong>{esc(c.get("status", "not_run"))}</strong> · {esc(c.get("kind", "unspecified"))}</p><p>{esc(c.get("summary", ""))}</p><ol>{steps}</ol><ul>{assertions}</ul>{"".join(artifacts)}</section>')
    findings = ''.join(f'<li><strong>{esc(f.get("id", ""))} {esc(f.get("severity", ""))} {esc(f.get("title", ""))}</strong><p>{esc(f.get("description", ""))}</p></li>' for f in data.get('findings', []))
    gaps = ''.join(f'<li>{esc(g)}</li>' for g in data.get('gaps', []))
    title = run.get('title', 'RUCMA 验收证据报告')
    metadata = ' · '.join(f'{esc(k)}: {esc(run[k])}' for k in ('id', 'kind', 'revision', 'environment', 'generated_at') if k in run)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:1080px;margin:40px auto;padding:0 24px;line-height:1.7;font-size:16px}}h1{{font-size:30px}}h2{{font-size:22px}}section{{border-top:1px solid #ddd;padding:24px 0}}img{{max-width:100%;height:auto}}figure{{margin:16px 0}}th,td{{padding:6px 14px;text-align:left}}.meta{{overflow-wrap:anywhere}}.notice{{border-left:4px solid #555;padding:12px 16px;background:#f3f3f3}}.gap{{font-weight:600}}meter{{width:180px}}@media print{{body{{margin:0;max-width:none}}section{{break-inside:avoid}}}}
</style></head><body><h1>{esc(title)}</h1><p class="meta">{metadata}</p><p class="notice">{esc(run.get('disclaimer', '以实际执行记录为准。passed、failed、blocked、not_run、skipped、flaky 分开统计；本报告不代表生产部署获准。'))}</p><h2>已报告检查的状态分布</h2><table>{chart or '<tr><td>尚无执行记录</td></tr>'}</table><p>这张图只统计本报告收录项；完整必测范围由 contracts.json 与 gate_evidence.py 核验。</p><h2>覆盖缺口</h2><ul>{gaps or '<li>请结合契约门禁确认，空列表不证明覆盖完整。</li>'}</ul>{''.join(blocks)}<section><h2>发现的问题</h2><ul>{findings or '<li>本报告未登记 finding；不等同于网站无缺陷。</li>'}</ul></section></body></html>'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding='utf-8'))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(data, args.input.resolve(), args.out.resolve()), encoding='utf-8')
        print(args.out.resolve())
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'REPORT_ERROR: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
