#!/usr/bin/env python3
"""Candidate lint for changed activity code. Standard library; never edits sources.
This is a heuristic scanner, not an AST parser or a detector of model authorship.
Exit 0: no blocking findings; 1: blocking findings; 2: incomplete/error.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fnmatch
import hashlib
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

UI_EXT = {'.html', '.htm', '.vue', '.js', '.cjs', '.mjs', '.ts', '.jsx', '.tsx'}
CODE_EXT = UI_EXT | {'.py', '.css', '.scss', '.styl', '.svelte', '.astro'}
EXCLUDED = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'vendor', 'playwright-report', 'test-results'}
COMMENT_RE = re.compile(r'/\*[\s\S]*?\*/|(?<!:)//[^\n]*|<!--[\s\S]*?-->|\{#[\s\S]*?#\}|\{%\s*comment\b[^%]*%\}[\s\S]*?\{%\s*endcomment\s*%\}')
DEBT_RE = re.compile(r'\b(?:TODO|FIXME|HACK|TBD|XXX)\b|(?:暂时|暂且|先).{0,12}(?:占位|跳过|忽略|不处理)|(?:后续|以后|留待).{0,14}(?:实现|完善|补充|处理)|(?:placeholder|implement\s+later|not\s+implemented)', re.I)
COPY_RE = re.compile(r'(?:并|绝)?不是[^。！？\n]{1,64}[，,]\s*而是|不(?:仅(?:仅)?|只(?:是)?)[^。！？\n]{1,64}[，,]\s*(?:更(?:是|能)|而是)')


def git(root: Path, *args: str) -> str:
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace').strip() or 'git failed')
    return p.stdout.decode('utf-8', 'replace')


def comments(text: str, suffix: str):
    """Python uses tokenize. JS/HTML/CSS use conservative lexical candidates."""
    if suffix == '.py':
        offsets = [0]
        for line in text.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                start = offsets[tok.start[0] - 1] + tok.start[1]
                yield start, start + len(tok.string), tok.string
    else:
        for m in COMMENT_RE.finditer(text):
            yield m.start(), m.end(), m.group()


def location(text: str, start: int):
    return text.count('\n', 0, start) + 1, start - text.rfind('\n', 0, start)


def scan_text(path: str, text: str, today: dt.date | None = None):
    today = today or dt.date.today()
    suffix = Path(path).suffix.lower()
    result = []

    def add(rule, start, end, message, fix, severity='warning', certainty='heuristic'):
        line, col = location(text, start)
        excerpt = text[start:end].strip()[:240]
        result.append({'rule': rule, 'path': path, 'line': line, 'column': col,
                       'end_line': text.count('\n', 0, end) + 1,
                       'severity': severity, 'certainty': certainty,
                       'message': message, 'suggested_action': fix, 'excerpt': excerpt,
                       'fingerprint': hashlib.sha256((rule+'\0'+path+'\0'+excerpt).encode()).hexdigest()[:20]})

    comment_spans = list(comments(text, suffix))
    masked = list(text)
    for a, b, comment in comment_spans:
        for i in range(a, b):
            if masked[i] != '\n':
                masked[i] = ' '
        debt = DEBT_RE.search(comment)
        if not debt:
            continue
        owner = re.search(r'owner\s*=\s*([^,;)\s]+)', comment)
        issue = re.search(r'issue\s*=\s*(#[0-9]+|https://[^\s,;)]+)', comment)
        expiry = re.search(r'expires\s*=\s*(\d{4}-\d{2}-\d{2})', comment)
        deadline = None
        if expiry:
            try:
                deadline = dt.date.fromisoformat(expiry.group(1))
            except ValueError:
                pass
        if owner and issue and deadline and deadline >= today:
            continue
        rule = 'debt/expired-deferral' if deadline and deadline < today else 'debt/untracked-deferral'
        add(rule, a, b, '延期或占位注释需要核实真实行为及责任归属。',
            '检查对应功能和测试；实现已承诺行为，或关联 owner、issue、expires 及业务批准。删除注释不等于修复。')

    # Only source candidates: a string here is not necessarily user-visible.
    code = ''.join(masked)
    is_test = any(p in {'tests', 'test', '__tests__'} for p in Path(path).parts) or '.spec.' in path or '.test.' in path
    if suffix in UI_EXT and not is_test:
        for m in COPY_RE.finditer(code):
            add('copy/negated-contrast', m.start(), m.end(), '疑似无信息增量的否定对立句；需结合界面复核。',
                '直接说明用户动作、结果或原因。保留必要的业务限制、错误信息、引用及真正有区分价值的对比。')
    if suffix in CODE_EXT and not is_test:
        for m in re.finditer(r'<\s*(?:marquee|blink|font)\b[^>]*>', code, re.I):
            add('ui/legacy-presentational-tag', m.start(), m.end(), '发现历史展示标签候选。',
                '改用语义 HTML 与项目样式；确认不是示例文本或第三方代码。')
        for m in re.finditer(r'font-size\s*:\s*(\d+(?:\.\d+)?)px\b', code, re.I):
            if float(m.group(1)) < 12:
                add('ui/tiny-text', m.start(), m.end(), '小于 12px 的显式字号，需检查实际可读性。',
                    '重要正文优先使用设计字号 token；以渲染结果和用途复核，小图标等可有具名例外。')
        for m in re.finditer(r'outline\s*:\s*(?:none|0(?:px)?)\s*[;}]', code, re.I):
            add('ui/focus-indicator-review', m.start(), m.end(), '可能移除了键盘焦点提示。',
                '检查是否有等价的 :focus-visible 样式，并用键盘实际验证；本规则不证明缺陷成立。')
    if suffix in {'.js', '.cjs', '.mjs', '.ts', '.jsx', '.tsx'} and is_test:
        for m in re.finditer(r'\b(?:test|it|describe)\.only\s*\(', code):
            add('test/focused-test', m.start(), m.end(), '聚焦测试可能隐藏其他回归用例。',
                '恢复完整相关测试集合并运行；同时在 Playwright 中启用 forbidOnly。', 'error')
        for m in re.finditer(r'\b(?:test|it|describe)\.(?:skip|fixme)\s*\(', code):
            add('test/skipped-case-review', m.start(), m.end(), '测试被跳过；关键契约必须另有已执行覆盖。',
                '查明跳过原因；关键流程的 skipped/blocked/not_run 均不计入通过。')
    return result


def selected(path: str, config):
    p = Path(path)
    return (not any(x in EXCLUDED for x in p.parts)
            and p.suffix.lower() in CODE_EXT
            and any(fnmatch.fnmatchcase(path, pat) for pat in config['include'])
            and not any(fnmatch.fnmatchcase(path, pat) for pat in config.get('exclude', [])))


def changed_lines(root: Path, base: str, path: str):
    patch = git(root, 'diff', '--no-ext-diff', '--no-color', '--unified=0', base, '--', path)
    lines = set()
    for m in re.finditer(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', patch, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path.cwd())
    ap.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'rules.json')
    ap.add_argument('--diff-base', help='Scan only added/modified lines versus this Git ref, including untracked source files.')
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    root = args.root.resolve()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if not root.is_dir() or not config.get('include'):
            raise ValueError('Existing root and nonempty include patterns are required.')
        today = dt.date.today()
        exemptions = config.get('exceptions', [])
        for e in exemptions:
            for key in ('rule', 'path', 'fingerprint', 'reason', 'owner', 'expires'):
                if not e.get(key):
                    raise ValueError(f'Exception is missing {key}')
            dt.date.fromisoformat(e['expires'])
        revision, dirty, untracked = None, None, set()
        try:
            revision = git(root, 'rev-parse', 'HEAD').strip()
            dirty = bool(git(root, 'status', '--porcelain'))
            untracked = set(filter(None, git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
            if args.diff_base:
                if args.diff_base.startswith('-'):
                    raise ValueError('Invalid base ref')
                git(root, 'rev-parse', '--verify', args.diff_base + '^{commit}')
                names = set(filter(None, git(root, 'diff', '--no-ext-diff', '--name-only', '-z', '--diff-filter=ACMR', args.diff_base, '--').split('\0'))) | untracked
            else:
                names = set(filter(None, git(root, 'ls-files', '-z').split('\0'))) | untracked
        except (RuntimeError, FileNotFoundError):
            if args.diff_base:
                raise RuntimeError('Diff scan requires Git and a valid base commit; no silent full-scan fallback.')
            names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and not any(x in EXCLUDED for x in p.relative_to(root).parts)}
        findings, suppressed, gaps, checked = [], [], [], []
        for name in sorted(n for n in names if selected(n, config)):
            file = root / name
            if not file.is_file():
                continue
            try:
                if file.is_symlink() or not file.resolve().is_relative_to(root):
                    raise ValueError('Symlink or path outside source root')
                if file.stat().st_size > config.get('max_file_bytes', 1_000_000):
                    raise ValueError('File exceeds scan limit')
                text = file.read_text(encoding='utf-8-sig')
                items = scan_text(name, text, today)
                if args.diff_base and name not in untracked:
                    changed = changed_lines(root, args.diff_base, name)
                    items = [d for d in items if any(n in changed for n in range(d['line'], d['end_line'] + 1))]
                checked.append(name)
                for d in items:
                    exc = next((e for e in exemptions if e['rule'] == d['rule'] and e['path'] == name and e['fingerprint'] == d['fingerprint'] and dt.date.fromisoformat(e['expires']) >= today), None)
                    if exc:
                        suppressed.append({**d, 'exception': exc})
                    else:
                        findings.append(d)
            except (OSError, UnicodeError, ValueError, tokenize.TokenError, IndentationError) as exc:
                gaps.append({'path': name, 'reason': str(exc)})
        if not checked and not args.diff_base:
            gaps.append({'path': '.', 'reason': 'No matching source files. Verify include patterns; this is not a clean application audit.'})
        report = {'schema_version': 1, 'kind': 'heuristic-source-scan',
                  'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'revision': revision, 'dirty': dirty, 'diff_base': args.diff_base,
                  'checked_files': checked, 'findings': findings, 'suppressed': suppressed,
                  'coverage_gaps': gaps, 'limitations': ['Not an AST parser for JS/HTML/CSS.', 'Copy matches are candidates, not proof of AI authorship or bad writing.', 'No browser, backend, or deployment verification.']}
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(encoded + '\n', encoding='utf-8')
        else:
            print(encoded)
        for d in findings:
            print(f"{d['severity'].upper()} {d['path']}:{d['line']} [{d['rule']}] {d['message']}", file=sys.stderr)
        print(f"scanned={len(checked)} findings={len(findings)} suppressed={len(suppressed)} gaps={len(gaps)}", file=sys.stderr)
        return 2 if gaps else (1 if any(d['severity'] == 'error' for d in findings) else 0)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, KeyError) as exc:
        print(f'CHECK_INCOMPLETE: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
